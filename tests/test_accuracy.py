"""Statistical accuracy gates on a 500k-item Zipf stream (run with ``pytest -m slow``).

The thresholds sit far from the measured values (see benchmarks/accuracy.py), so they fail only if
the algorithm is broken, not because of seed-to-seed variation.
"""

from collections import Counter

import pytest
from benchmarks.common import BasicTopK, run, score, zipf_stream

from topk import TopK

pytestmark = pytest.mark.slow

N, DISTINCT, SKEW, K = 500_000, 100_000, 1.1, 100


@pytest.fixture(scope="module")
def stream() -> list[int]:
    return zipf_stream(N, DISTINCT, SKEW, seed=1)


@pytest.fixture(scope="module")
def truth(stream: list[int]) -> Counter[int]:
    return Counter(stream)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_default_configuration_is_accurate(
    stream: list[int], truth: Counter[int], seed: int
) -> None:
    # Measured: precision 1.000, ARE <= 0.0008, AAE <= 0.5 for seeds 1-5.
    reported = run(TopK, stream, K, seed=seed).most_common()
    result = score(reported, truth, K)
    assert result.precision >= 0.98
    assert result.are < 0.005
    assert result.aae < 2
    # Theorem 2 end to end: with 32-bit fingerprints nothing is ever overestimated.
    assert all(count <= truth[key] for key, count in reported)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_optimizations_matter_when_fingerprints_collide(
    stream: list[int], truth: Counter[int], seed: int
) -> None:
    # With 8-bit fingerprints, collisions are frequent. Measured over seeds 1-5: the full
    # algorithm keeps precision >= 0.99 and ARE ~ 0.005; the basic version drops to precision
    # 0.75-0.82 with ARE in the hundreds (74,000x to 170,000x worse).
    kwargs = {"width": 8 * K, "fingerprint_bits": 8, "seed": seed}
    full = score(run(TopK, stream, K, **kwargs).most_common(), truth, K)
    basic = score(run(BasicTopK, stream, K, **kwargs).most_common(), truth, K)
    assert full.precision >= 0.95
    assert full.are < 0.05
    assert basic.precision <= 0.9
    assert basic.are > 10
    assert basic.are > 1_000 * full.are
