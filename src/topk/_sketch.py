"""The HeavyKeeper bucket arrays: hashing, exponential-weakening decay and the insert rule.

The sketch is ``depth`` arrays of ``width`` buckets, stored as two flat ``array.array`` objects
(``fps`` for 32-bit fingerprints, ``counts`` for 64-bit counters) where row ``j`` starts at offset
``j * width``. Compact arrays matter: a list would hold every fingerprint as a separate ~28-byte
int object, about four times the memory, at the same insertion speed. It knows
nothing about the top-k set: the caller says whether the flow is already monitored and what the
smallest monitored count ``n_min`` is, and gets back the flow's estimate for this insertion.

This is the pure-Python kernel. A compiled kernel can replace it by providing the same three
operations (``locate``, ``insert``, ``estimate``) and state scheme.

Hashing (scheme ``py-blake2b-dh-v1``; pickled sketches depend on it)::

    x   = int.from_bytes(blake2b(data, digest_size=16, key=seed as u64 big-endian), "little")
    h1  = x & (2**64 - 1)
    h2  = ((x >> 64) & (2**32 - 1)) | 1
    fp  = (x >> 96) & (2**fingerprint_bits - 1)
    row j bucket = j * width + (h1 + j * h2) % width
"""

from __future__ import annotations

import math
import random
from array import array
from collections.abc import Callable, Sequence
from hashlib import blake2b
from typing import Any

__all__ = ["PySketch"]

_SCHEME = "py-blake2b-dh-v1"
_U64 = (1 << 64) - 1
_U32 = (1 << 32) - 1
# Probabilities below this are indistinguishable from 0 for random.random(), which has 53 bits.
_NEGLIGIBLE = 2.0**-53
# Precompute b**-c for c below this; larger counts (only for b very close to 1) use pow().
_TABLE_LIMIT = 4096

if array("I").itemsize < 4:  # pragma: no cover - no mainstream platform
    raise ImportError("topk needs a 32-bit unsigned int array type code")


def _decay_cutoff(b: float) -> int:
    """Return the smallest ``c >= 1`` with ``b ** -c < 2 ** -53``.

    From that count on a decay draw can never succeed, so no draw is taken. The logarithm gives a
    first guess that rounding can put off by one (e.g. ``53 * ln(2) / ln(b)`` can come out as an
    exact integer although ``b ** -c`` is already below the threshold), so it is corrected against
    the actual powers.
    """
    c = max(1, math.floor(53 * math.log(2) / math.log(b)))
    while c > 1 and b ** -(c - 1) < _NEGLIGIBLE:
        c -= 1
    while b**-c >= _NEGLIGIBLE:
        c += 1
    return c


class PySketch:
    """``depth`` x ``width`` buckets of (fingerprint, counter) with exponential-weakening decay."""

    __slots__ = (
        "_cutoff",
        "_fp_mask",
        "_hasher",
        "_offsets",
        "_rand",
        "_rng",
        "_table",
        "counts",
        "decay_base",
        "depth",
        "fingerprint_bits",
        "fps",
        "seed",
        "width",
    )

    def __init__(
        self,
        width: int,
        depth: int,
        decay_base: float,
        fingerprint_bits: int,
        seed: int,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._configure(width, depth, decay_base, fingerprint_bits, seed, rng)

    def _configure(
        self,
        width: int,
        depth: int,
        decay_base: float,
        fingerprint_bits: int,
        seed: int,
        rng: random.Random | None,
    ) -> None:
        if width < 1:
            raise ValueError(f"width must be >= 1, got {width}")
        if depth < 1:
            raise ValueError(f"depth must be >= 1, got {depth}")
        if not (math.isfinite(decay_base) and decay_base > 1):
            raise ValueError(f"decay_base must be a finite number > 1, got {decay_base}")
        if not 1 <= fingerprint_bits <= 32:
            raise ValueError(f"fingerprint_bits must be between 1 and 32, got {fingerprint_bits}")
        if not 0 <= seed <= _U64:
            raise ValueError(f"seed must satisfy 0 <= seed < 2**64, got {seed}")

        self.width = width
        self.depth = depth
        self.decay_base = decay_base
        self.fingerprint_bits = fingerprint_bits
        self.seed = seed
        self.fps: array[int] = array("I", [0]) * (width * depth)
        self.counts: array[int] = array("Q", [0]) * (width * depth)

        self._fp_mask = (1 << fingerprint_bits) - 1
        self._offsets = tuple(j * width for j in range(depth))
        # A pre-keyed hasher: copying it skips re-absorbing the key block on every call.
        self._hasher = blake2b(digest_size=16, key=seed.to_bytes(8, "big"))

        self._cutoff = _decay_cutoff(decay_base)
        self._table = [decay_base**-c for c in range(min(self._cutoff, _TABLE_LIMIT))]

        self._rng = random.Random(seed) if rng is None else rng
        self._rand: Callable[[], float] = self._rng.random

    @property
    def decay_cutoff(self) -> int:
        """Counters at or above this value are never decayed (``b ** -c`` underflows 53 bits)."""
        return self._cutoff

    def decay_probability(self, count: int) -> float:
        """Return ``b ** -count``, the chance that one colliding insertion decays the bucket."""
        if count >= self._cutoff:
            return 0.0
        if count < len(self._table):
            return self._table[count]
        return float(self.decay_base**-count)

    def locate(self, data: bytes) -> tuple[tuple[int, ...], int]:
        """Hash an encoded key once: return its bucket index in each row and its fingerprint."""
        hasher = self._hasher.copy()
        hasher.update(data)
        x = int.from_bytes(hasher.digest(), "little")
        h1 = x & _U64
        h2 = ((x >> 64) & _U32) | 1
        width = self.width
        slots = tuple([offset + (h1 + j * h2) % width for j, offset in enumerate(self._offsets)])
        return slots, (x >> 96) & self._fp_mask

    def insert(self, slots: Sequence[int], fp: int, monitored: bool, n_min: int) -> int:
        """Count one occurrence of the flow at ``slots``/``fp``; return its resulting estimate.

        For each of the flow's buckets:

        * empty: claim it with count 1;
        * same fingerprint: increment, unless the flow is neither monitored nor has a count at or
          below ``n_min`` (Optimization II: such a bucket most likely belongs to a different flow
          with a colliding fingerprint, so counting into it could only add error);
        * different fingerprint: decay the resident counter by 1 with probability ``b ** -C``, and
          hand the bucket to this flow, with count 1, if that brings it to 0.

        The return value is the largest counter among the buckets that now hold this flow.
        """
        fps = self.fps
        counts = self.counts
        cutoff = self._cutoff
        table = self._table
        table_len = len(table)
        maxv = 0
        for i in slots:
            c = counts[i]
            if c == 0:
                fps[i] = fp
                counts[i] = 1
                if maxv < 1:
                    maxv = 1
            elif fps[i] == fp:
                if monitored or c <= n_min:
                    c += 1
                    counts[i] = c
                if c > maxv:
                    maxv = c
            elif c < cutoff and self._rand() < (table[c] if c < table_len else self.decay_base**-c):
                c -= 1
                if c == 0:
                    fps[i] = fp
                    c = 1
                    if maxv < 1:
                        maxv = 1
                counts[i] = c
        return maxv

    def estimate(self, slots: Sequence[int], fp: int) -> int:
        """Return the largest counter among the flow's buckets that hold its fingerprint."""
        fps = self.fps
        counts = self.counts
        best = 0
        for i in slots:
            if fps[i] == fp and counts[i] > best:
                best = counts[i]
        return best

    def __getstate__(self) -> tuple[Any, ...]:
        return (
            _SCHEME,
            self.width,
            self.depth,
            self.decay_base,
            self.fingerprint_bits,
            self.seed,
            self.fps,
            self.counts,
            self._rng.getstate(),
        )

    def __setstate__(self, state: tuple[Any, ...]) -> None:
        scheme, width, depth, decay_base, fingerprint_bits, seed, fps, counts, rng_state = state
        if scheme != _SCHEME:
            raise ValueError(
                f"cannot load a sketch saved with hashing scheme {scheme!r}; "
                f"this version implements {_SCHEME!r}"
            )
        if len(fps) != len(counts) or len(fps) != width * depth:
            raise ValueError("corrupt sketch state: bucket arrays do not match width * depth")
        rng = random.Random()
        rng.setstate(rng_state)
        self._configure(width, depth, decay_base, fingerprint_bits, seed, rng)
        self.fps = array("I", fps)
        self.counts = array("Q", counts)
