"""The public ``TopK`` class: HeavyKeeper's Algorithm 1 over a sketch and a min-heap."""

from __future__ import annotations

import operator
import secrets
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from typing import Any, Generic, TypeVar

from topk._encoding import encode_key
from topk._heap import IndexedMinHeap
from topk._sketch import PySketch

__all__ = ["TopK"]

K = TypeVar("K", bound=Hashable)

_STATE_VERSION = 1


class TopK(Generic[K]):
    """Track the ``k`` most frequent keys of a stream in bounded memory (HeavyKeeper).

    Memory is ``O(k + depth * width)`` however many distinct keys the stream holds. Mouse flows are
    decayed out of the sketch while elephant flows keep accurate counters, so the reported counts
    of the top keys are accurate and never exceed the true counts (barring fingerprint collisions).

    Keys must be hashable. By default ``None``, ``bool``, ``int``, ``str``, ``bytes`` and tuples of
    these are supported; pass ``key_encoder`` (a function from a key to ``bytes``) to count other
    hashable keys. The encoder must map equal keys to equal bytes, and be picklable (a module-level
    function) if the instance is to be pickled.

    Not thread-safe: guard concurrent use with a lock.

    Args:
        k: How many keys to track (``>= 1``).
        width: Buckets per array. Larger is more accurate. Defaults to ``max(1024, 32 * k)``.
        depth: Number of arrays (hash functions); the paper uses 2.
        decay_base: The paper's ``b`` (``> 1``): a colliding insertion decays a counter ``C`` with
            probability ``b ** -C``. Note this is ``1 / decay`` of the convention used by Redis.
        fingerprint_bits: Fingerprint size, 1 to 32 bits.
        seed: Seeds hashing and the decay RNG (``0 <= seed < 2**64``). Defaults to a random seed;
            pass an int for reproducible results.
        key_encoder: Optional function from a key to ``bytes``; see above.

    Example:
        >>> tk = TopK(2, seed=1)
        >>> tk.update("abracadabra")
        >>> tk.most_common()
        [('a', 5), ('b', 2)]
    """

    # Private switches that turn this into the "basic" HeavyKeeper without the paper's Section 3.4
    # optimizations; the benchmarks subclass it to measure what they buy.
    _selective_increment = True  # Optimization II
    _exact_admission = True  # Optimization I

    def __init__(
        self,
        k: int,
        *,
        width: int | None = None,
        depth: int = 2,
        decay_base: float = 1.08,
        fingerprint_bits: int = 32,
        seed: int | None = None,
        key_encoder: Callable[[K], bytes] | None = None,
    ) -> None:
        # operator.index rejects floats (and NaN): k = 1.5 would admit two keys before reporting
        # itself full and k = nan would never be full, silently breaking the memory bound.
        k = operator.index(k)
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        width = max(1024, 32 * k) if width is None else operator.index(width)
        depth = operator.index(depth)
        fingerprint_bits = operator.index(fingerprint_bits)
        seed = secrets.randbits(64) if seed is None else operator.index(seed)
        self._k = k
        self._sketch = PySketch(width, depth, decay_base, fingerprint_bits, seed)
        self._heap: IndexedMinHeap[K] = IndexedMinHeap()
        self._encode: Callable[[Any], bytes] = encode_key if key_encoder is None else key_encoder
        self._total = 0

    # --- updating ----------------------------------------------------------------------------

    def add(self, key: K, count: int = 1) -> None:
        """Count ``count`` occurrences of ``key``.

        Equivalent to ``count`` calls of ``add(key)`` (the key is hashed once), so it costs
        ``O(count * depth)``; it is not a constant-time bulk update.
        """
        count = operator.index(count)
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count}")
        if count == 0:
            return
        slots, fp = self._sketch.locate(self._encode(key))
        for _ in range(count):
            self._add_one(key, slots, fp)

    def update(self, items: Iterable[K] | Mapping[K, int]) -> None:
        """Count every key of an iterable, or add each count of a mapping (like ``Counter``)."""
        if isinstance(items, Mapping):
            for key, count in items.items():
                self.add(key, count)
        else:
            for key in items:
                self.add(key)

    def _add_one(self, key: K, slots: tuple[int, ...], fp: int) -> None:
        # Algorithm 1 with Optimizations I and II.
        heap = self._heap
        full = len(heap) >= self._k
        n_min = heap.min_count() if full else 0
        monitored = key in heap
        maxv = self._sketch.insert(slots, fp, monitored or not self._selective_increment, n_min)
        self._total += 1
        if monitored:
            heap.increase(key, maxv)
        elif maxv > 0:
            if not full:
                heap.push(key, maxv)
            elif (maxv == n_min + 1) if self._exact_admission else (maxv > n_min):
                # Theorem 1: without a fingerprint collision a flow that deserves a place has
                # exactly n_min + 1, so any larger estimate is a collision artifact.
                heap.replace_min(key, maxv)

    # --- querying ----------------------------------------------------------------------------

    def estimate(self, key: K) -> int:
        """Return the sketch's current estimate of ``key``'s count.

        Never exceeds the true count (barring fingerprint collisions). It is 0 for a key the
        sketch holds no bucket for: one never seen, or one whose counters have decayed away. A key
        outside the top ``k`` may still show a small estimate while its buckets survive, so use
        ``key in top`` or ``most_common()`` to ask what is in the top ``k``.
        """
        slots, fp = self._sketch.locate(self._encode(key))
        return self._sketch.estimate(slots, fp)

    def most_common(self, n: int | None = None) -> list[tuple[K, int]]:
        """Return the tracked ``(key, count)`` pairs, largest count first (ties in no set order).

        Args:
            n: Return only the ``n`` largest; ``None`` returns all (at most ``k``).
        """
        if n is not None and n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        items = self._heap.items_desc()
        return items if n is None else items[:n]

    def __contains__(self, key: object) -> bool:
        """Whether ``key`` is currently among the tracked top ``k``."""
        return key in self._heap

    def __len__(self) -> int:
        """The number of tracked keys (at most ``k``)."""
        return len(self._heap)

    def __iter__(self) -> Iterator[K]:
        """Iterate the tracked keys, largest count first."""
        return iter([key for key, _ in self._heap.items_desc()])

    @property
    def k(self) -> int:
        return self._k

    @property
    def width(self) -> int:
        return self._sketch.width

    @property
    def depth(self) -> int:
        return self._sketch.depth

    @property
    def decay_base(self) -> float:
        return self._sketch.decay_base

    @property
    def seed(self) -> int:
        """The seed in use, including a randomly drawn one when ``seed=None`` was passed."""
        return self._sketch.seed

    @property
    def total(self) -> int:
        """The number of occurrences counted so far."""
        return self._total

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(k={self._k}, width={self.width}, depth={self.depth}, "
            f"decay_base={self.decay_base}, tracked={len(self)}, total={self._total})"
        )

    # --- pickling ----------------------------------------------------------------------------

    def __getstate__(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "k": self._k,
            "total": self._total,
            "key_encoder": self._encode,
            "heap": self._heap,
            "sketch": self._sketch,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        if state.get("version") != _STATE_VERSION:
            raise ValueError(
                f"cannot load TopK state version {state.get('version')!r}; "
                f"this release reads version {_STATE_VERSION}"
            )
        self._k = state["k"]
        self._total = state["total"]
        self._encode = state["key_encoder"]
        self._heap = state["heap"]
        self._sketch = state["sketch"]
