from collections import Counter

import pytest
from benchmarks.common import BasicTopK, Scores, run, score, zipf_stream


def test_zipf_stream_is_deterministic_and_has_the_requested_shape() -> None:
    a = zipf_stream(5_000, 200, 1.1, seed=3)
    assert a == zipf_stream(5_000, 200, 1.1, seed=3)
    assert a != zipf_stream(5_000, 200, 1.1, seed=4)
    assert len(a) == 5_000
    assert all(0 <= x < 200 for x in a)


def test_zipf_stream_is_skewed_toward_low_ranks() -> None:
    counts = Counter(zipf_stream(50_000, 1_000, 1.2, seed=1))
    ranked = [c for _, c in counts.most_common()]
    assert ranked[0] > 5 * ranked[10] > 5 * 5 * ranked[100]
    # rank r is drawn about 1 / r**skew as often as rank 1
    assert counts[1] / counts[0] == pytest.approx(0.5**1.2, rel=0.1)


def test_score_of_a_perfect_report() -> None:
    truth = Counter({"a": 10, "b": 5, "c": 1})
    assert score([("a", 10), ("b", 5)], truth, k=2) == Scores(precision=1.0, are=0.0, aae=0.0)


def test_score_averages_the_errors_over_the_reported_keys() -> None:
    truth = Counter({"a": 10, "b": 5, "c": 1})
    result = score([("a", 8), ("c", 1)], truth, k=2)
    assert result.precision == 0.5  # "c" is not among the two largest
    assert result.are == pytest.approx((2 / 10 + 0) / 2)
    assert result.aae == pytest.approx((2 + 0) / 2)


def test_score_does_not_penalize_either_side_of_a_tie_at_the_boundary() -> None:
    truth = Counter({"a": 10, "b": 5, "c": 5, "d": 1})
    assert score([("a", 10), ("b", 5)], truth, k=2).precision == 1.0
    assert score([("a", 10), ("c", 5)], truth, k=2).precision == 1.0
    assert score([("a", 10), ("d", 1)], truth, k=2).precision == 0.5


def test_score_of_an_empty_report() -> None:
    assert score([], Counter({"a": 3}), k=1) == Scores(precision=0.0, are=0.0, aae=0.0)


def test_score_rejects_a_key_that_is_not_in_the_stream() -> None:
    with pytest.raises(ValueError, match="not in the stream"):
        score([("ghost", 3)], Counter({"a": 3}), k=1)


def test_run_feeds_the_stream_into_the_chosen_variant() -> None:
    stream = [1, 1, 1, 2, 2, 3]
    tk = run(BasicTopK, stream, 2, seed=1)
    assert isinstance(tk, BasicTopK)
    assert tk.total == 6
    assert tk.most_common() == [(1, 3), (2, 2)]
    assert BasicTopK._selective_increment is False
    assert BasicTopK._exact_admission is False
