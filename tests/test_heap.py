import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from topk._heap import IndexedMinHeap


def _assert_consistent(heap: IndexedMinHeap[int]) -> None:
    keys, counts, pos = heap._keys, heap._counts, heap._pos
    assert len(keys) == len(counts) == len(pos) == len(heap)
    for i, key in enumerate(keys):
        assert pos[key] == i
        if i:
            assert counts[(i - 1) // 2] <= counts[i], "min-heap order violated"


class HeapMachine(RuleBasedStateMachine):
    """Drive the heap with random operations and compare against a dict model."""

    def __init__(self) -> None:
        super().__init__()
        self.heap: IndexedMinHeap[int] = IndexedMinHeap()
        self.model: dict[int, int] = {}

    @rule(key=st.integers(0, 15), count=st.integers(1, 50))
    def push(self, key: int, count: int) -> None:
        if key in self.model:
            with pytest.raises(ValueError, match="already"):
                self.heap.push(key, count)
            return
        self.heap.push(key, count)
        self.model[key] = count

    @precondition(lambda self: bool(self.model))
    @rule(data=st.data(), count=st.integers(1, 80))
    def increase(self, data: st.DataObject, count: int) -> None:
        key = data.draw(st.sampled_from(sorted(self.model)))
        self.heap.increase(key, count)
        self.model[key] = max(self.model[key], count)

    @precondition(lambda self: bool(self.model))
    @rule(key=st.integers(16, 31), count=st.integers(1, 50))
    def replace_min(self, key: int, count: int) -> None:
        if key in self.model:
            return
        lowest = min(self.model.values())
        evicted_key, evicted_count = self.heap.replace_min(key, count)
        assert evicted_count == lowest
        assert self.model.pop(evicted_key) == lowest
        self.model[key] = count

    @invariant()
    def matches_model(self) -> None:
        _assert_consistent(self.heap)
        assert len(self.heap) == len(self.model)
        for key, count in self.model.items():
            assert key in self.heap
            assert self.heap.get(key) == count
        if self.model:
            assert self.heap.min_count() == min(self.model.values())
        listed = self.heap.items_desc()
        assert dict(listed) == self.model
        assert [c for _, c in listed] == sorted(self.model.values(), reverse=True)


TestHeapMachine = HeapMachine.TestCase


def test_empty_heap() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    assert len(heap) == 0
    assert "a" not in heap
    assert heap.get("a") is None
    assert heap.items_desc() == []
    with pytest.raises(IndexError):
        heap.min_count()
    with pytest.raises(IndexError):
        heap.replace_min("a", 1)


def test_increase_unknown_key_raises_key_error() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    with pytest.raises(KeyError):
        heap.increase("a", 3)


def test_increase_to_a_lower_or_equal_count_is_a_no_op() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    heap.push("a", 5)
    heap.increase("a", 3)
    heap.increase("a", 5)
    assert heap.get("a") == 5


def test_replace_min_returns_the_evicted_entry_and_rejects_present_keys() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    for key, count in [("a", 5), ("b", 2), ("c", 9)]:
        heap.push(key, count)
    assert heap.replace_min("d", 3) == ("b", 2)
    assert "b" not in heap
    assert heap.min_count() == 3
    with pytest.raises(ValueError, match="already"):
        heap.replace_min("a", 1)


def test_items_desc_orders_by_count() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    for key, count in [("a", 5), ("b", 2), ("c", 9), ("d", 7)]:
        heap.push(key, count)
    assert heap.items_desc() == [("c", 9), ("d", 7), ("a", 5), ("b", 2)]


def test_increase_can_promote_the_root_past_every_other_entry() -> None:
    heap: IndexedMinHeap[str] = IndexedMinHeap()
    for key, count in [("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5)]:
        heap.push(key, count)
    heap.increase("a", 100)
    assert heap.min_count() == 2
    assert heap.items_desc()[0] == ("a", 100)
