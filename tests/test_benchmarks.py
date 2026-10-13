"""Microbenchmarks of the hot paths, using pytest-benchmark.

Excluded from the default run. Run them with::

    pytest -m bench --benchmark-only
    pytest -m bench --benchmark-only --benchmark-json=bench.json   # keep the numbers

``--benchmark-compare`` against an earlier ``--benchmark-save`` shows regressions. Every benchmark
also checks that what it timed behaved correctly, so none can pass while measuring something broken.
"""

import itertools
import pickle
from collections import Counter
from typing import Any

import pytest
from benchmarks.common import BasicTopK, zipf_stream
from pytest_benchmark.fixture import BenchmarkFixture

from topk import TopK
from topk._encoding import encode_key
from topk._heap import IndexedMinHeap
from topk._sketch import PySketch

pytestmark = pytest.mark.bench

K = 100
N = 20_000


@pytest.fixture(scope="module")
def stream() -> list[int]:
    return zipf_stream(N, 5_000, 1.1, seed=1)


@pytest.fixture(scope="module")
def filled(stream: list[int]) -> TopK[int]:
    tk: TopK[int] = TopK(K, seed=1)
    tk.update(stream)
    return tk


# --- building blocks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["user-12345", 123_456_789, b"\x00\x01\x02" * 8, ("10.0.0.1", 443, "GET")],
    ids=["str", "int", "bytes", "tuple"],
)
def test_encode_key(benchmark: BenchmarkFixture, key: Any) -> None:
    encoded = benchmark(encode_key, key)
    assert encoded == encode_key(key)


def test_locate(benchmark: BenchmarkFixture) -> None:
    sketch = PySketch(3200, 2, 1.08, 32, 1)
    data = encode_key("user-12345")
    slots, fp = benchmark(sketch.locate, data)
    assert len(slots) == 2
    assert (slots, fp) == sketch.locate(data)


def test_sketch_insert_into_an_owned_bucket(benchmark: BenchmarkFixture) -> None:
    sketch = PySketch(3200, 2, 1.08, 32, 1)
    slots, fp = sketch.locate(encode_key("hot"))
    benchmark(sketch.insert, slots, fp, True, 0)
    assert sketch.estimate(slots, fp) >= 1


def test_heap_increase(benchmark: BenchmarkFixture) -> None:
    heap: IndexedMinHeap[int] = IndexedMinHeap()
    for key in range(K):
        heap.push(key, key + 1)
    counter = itertools.count(1_000)

    def bump() -> None:
        heap.increase(0, next(counter))

    benchmark(bump)
    assert heap.get(0) is not None
    assert len(heap) == K


# --- the public API ----------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [TopK, BasicTopK], ids=["full", "basic"])
def test_add_a_zipf_stream(benchmark: BenchmarkFixture, stream: list[int], cls: type[Any]) -> None:
    """Inserts per second: divide ``inserts_per_round`` by the reported mean."""
    built: list[TopK[int]] = []

    def setup() -> tuple[tuple[TopK[int]], dict[str, Any]]:
        tk = cls(K, seed=1)
        built.append(tk)
        return (tk,), {}

    def feed(tk: TopK[int]) -> None:
        add = tk.add
        for key in stream:
            add(key)

    benchmark.pedantic(feed, setup=setup, rounds=5, warmup_rounds=1)
    benchmark.extra_info["inserts_per_round"] = N
    assert built[-1].total == N
    assert 0 < len(built[-1]) <= K


def test_add_with_a_count(benchmark: BenchmarkFixture) -> None:
    tk: TopK[str] = TopK(K, seed=1)
    benchmark(tk.add, "hot-key", 100)
    assert "hot-key" in tk
    assert tk.estimate("hot-key") > 0


def test_update_from_a_mapping(benchmark: BenchmarkFixture) -> None:
    counts = {f"key-{i}": 5 for i in range(1_000)}
    built: list[TopK[str]] = []

    def setup() -> tuple[tuple[TopK[str]], dict[str, Any]]:
        tk: TopK[str] = TopK(K, seed=1)
        built.append(tk)
        return (tk,), {}

    benchmark.pedantic(lambda tk: tk.update(counts), setup=setup, rounds=5, warmup_rounds=1)
    assert built[-1].total == 5_000


def test_estimate(benchmark: BenchmarkFixture, filled: TopK[int], stream: list[int]) -> None:
    hottest_key = filled.most_common(1)[0][0]
    estimate = benchmark(filled.estimate, hottest_key)
    assert 0 < estimate <= Counter(stream)[hottest_key]  # never above the true count


def test_most_common(benchmark: BenchmarkFixture, filled: TopK[int]) -> None:
    result = benchmark(filled.most_common)
    assert len(result) == len(filled) <= K
    assert [count for _, count in result] == sorted((c for _, c in result), reverse=True)


def test_pickle_round_trip(benchmark: BenchmarkFixture, filled: TopK[int]) -> None:
    def round_trip() -> TopK[int]:
        restored: TopK[int] = pickle.loads(pickle.dumps(filled))
        return restored

    restored = benchmark(round_trip)
    assert restored.most_common() == filled.most_common()
