"""Helpers shared by the benchmark scripts and the slow accuracy tests."""

from __future__ import annotations

import itertools
import random
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from topk import TopK

__all__ = ["BasicTopK", "Scores", "run", "score", "zipf_stream"]


class BasicTopK(TopK[Any]):
    """HeavyKeeper without the paper's Section 3.4 optimizations (the plain Section 3.3 version)."""

    _selective_increment = False
    _exact_admission = False


def zipf_stream(n: int, distinct: int, skew: float, seed: int) -> list[int]:
    """Draw ``n`` keys from a Zipf law: rank ``r`` (0-based) has weight ``1 / (r + 1) ** skew``."""
    rng = random.Random(seed)
    cumulative = list(itertools.accumulate((r + 1) ** -skew for r in range(distinct)))
    return rng.choices(range(distinct), cum_weights=cumulative, k=n)


def run(cls: type[TopK[Any]], stream: Iterable[Hashable], k: int, **kwargs: Any) -> TopK[Any]:
    """Feed ``stream`` into a fresh ``cls(k, **kwargs)`` and return it."""
    tk = cls(k, **kwargs)
    for key in stream:
        tk.add(key)
    return tk


@dataclass(frozen=True)
class Scores:
    precision: float
    are: float  # average relative error over the reported keys
    aae: float  # average absolute error over the reported keys


def score(reported: Sequence[tuple[Hashable, int]], truth: Counter[Hashable], k: int) -> Scores:
    """Score a report against the exact counts (the metrics of the paper's Section 5.2).

    ``precision`` is the fraction of ``k`` that are genuine top-k keys. A reported key counts as
    genuine if its true count is at least the k-th largest true count, so neither side of a tie at
    the boundary is penalized.
    """
    if not reported:
        return Scores(precision=0.0, are=0.0, aae=0.0)
    ranked = sorted(truth.values(), reverse=True)
    threshold = ranked[min(k, len(ranked)) - 1]
    genuine = 0
    relative = 0.0
    absolute = 0.0
    for key, estimate in reported:
        actual = truth[key]
        if actual == 0:
            raise ValueError(f"reported key {key!r} is not in the stream")
        genuine += actual >= threshold
        relative += abs(estimate - actual) / actual
        absolute += abs(estimate - actual)
    return Scores(genuine / k, relative / len(reported), absolute / len(reported))
