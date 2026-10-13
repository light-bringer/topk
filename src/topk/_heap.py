"""A binary min-heap of ``(key, count)`` entries with O(1) key lookup.

``TopK`` keeps its k current heavy hitters here. Admission needs the smallest count in O(1)
(``min_count``), a monitored key's count is raised in O(log k) (``increase``; counts only ever
grow), and a new key evicts the minimum in O(log k) (``replace_min``).
"""

from __future__ import annotations

from collections.abc import Hashable
from operator import itemgetter
from typing import Generic, TypeVar

__all__ = ["IndexedMinHeap"]

K = TypeVar("K", bound=Hashable)


class IndexedMinHeap(Generic[K]):
    """Min-heap keyed by count, with a ``key -> heap position`` index."""

    __slots__ = ("_counts", "_keys", "_pos")

    def __init__(self) -> None:
        self._keys: list[K] = []
        self._counts: list[int] = []
        self._pos: dict[K, int] = {}

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: object) -> bool:
        return key in self._pos

    def get(self, key: K) -> int | None:
        """Return the count stored for ``key``, or ``None`` if it is not in the heap."""
        i = self._pos.get(key)
        return None if i is None else self._counts[i]

    def min_count(self) -> int:
        """Return the smallest count in O(1). Raises ``IndexError`` if the heap is empty."""
        if not self._counts:
            raise IndexError("min_count() on an empty heap")
        return self._counts[0]

    def push(self, key: K, count: int) -> None:
        """Add a new entry. Raises ``ValueError`` if ``key`` is already present."""
        if key in self._pos:
            raise ValueError(f"key {key!r} is already in the heap")
        i = len(self._keys)
        self._keys.append(key)
        self._counts.append(count)
        self._pos[key] = i
        self._sift_up(i)

    def increase(self, key: K, count: int) -> None:
        """Raise ``key``'s count to ``count``; a lower or equal count is ignored.

        Raises ``KeyError`` if ``key`` is not in the heap.
        """
        i = self._pos[key]
        if count > self._counts[i]:
            self._counts[i] = count
            self._sift_down(i)

    def replace_min(self, key: K, count: int) -> tuple[K, int]:
        """Evict the minimum entry, insert ``(key, count)`` and return the evicted entry.

        Raises ``IndexError`` if the heap is empty and ``ValueError`` if ``key`` is present.
        """
        if not self._keys:
            raise IndexError("replace_min() on an empty heap")
        if key in self._pos:
            raise ValueError(f"key {key!r} is already in the heap")
        old_key, old_count = self._keys[0], self._counts[0]
        del self._pos[old_key]
        self._keys[0] = key
        self._counts[0] = count
        self._pos[key] = 0
        self._sift_down(0)
        return old_key, old_count

    def items_desc(self) -> list[tuple[K, int]]:
        """Return all entries sorted by count, largest first (order among ties is unspecified)."""
        return sorted(zip(self._keys, self._counts, strict=True), key=itemgetter(1), reverse=True)

    def _sift_up(self, i: int) -> None:
        keys, counts, pos = self._keys, self._counts, self._pos
        key, count = keys[i], counts[i]
        while i:
            parent = (i - 1) >> 1
            if counts[parent] <= count:
                break
            keys[i] = keys[parent]
            counts[i] = counts[parent]
            pos[keys[i]] = i
            i = parent
        keys[i] = key
        counts[i] = count
        pos[key] = i

    def _sift_down(self, i: int) -> None:
        keys, counts, pos = self._keys, self._counts, self._pos
        n = len(keys)
        key, count = keys[i], counts[i]
        while True:
            child = 2 * i + 1
            if child >= n:
                break
            if child + 1 < n and counts[child + 1] < counts[child]:
                child += 1
            if counts[child] >= count:
                break
            keys[i] = keys[child]
            counts[i] = counts[child]
            pos[keys[i]] = i
            i = child
        keys[i] = key
        counts[i] = count
        pos[key] = i
