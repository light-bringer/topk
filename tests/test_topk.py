import pickle
import random
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from topk import TopK


def float_encoder(key: float) -> bytes:
    return repr(key).encode()


def feed(tk: TopK[object], stream: list[object]) -> None:
    for key in stream:
        tk.add(key)


def zipf_stream(n: int, distinct: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    weights = [1 / (rank + 1) ** 1.2 for rank in range(distinct)]
    return [f"key{i}" for i in rng.choices(range(distinct), weights, k=n)]


# --- basic behavior --------------------------------------------------------------------------


def test_counts_are_exact_below_capacity_without_collisions() -> None:
    tk: TopK[str] = TopK(5, seed=1)
    tk.update("aaabbc")
    assert tk.most_common() == [("a", 3), ("b", 2), ("c", 1)]
    assert tk.total == 6
    assert len(tk) == 3


def test_estimate_of_a_tracked_and_an_unseen_key() -> None:
    tk: TopK[str] = TopK(5, seed=1)
    tk.update("aaabbc")
    assert tk.estimate("a") == 3
    assert tk.estimate("never-seen") == 0


def test_membership_len_and_iteration_order() -> None:
    tk: TopK[str] = TopK(5, seed=1)
    tk.update("aaabbc")
    assert "a" in tk
    assert "z" not in tk
    assert list(tk) == ["a", "b", "c"]
    assert tk.most_common(2) == [("a", 3), ("b", 2)]
    assert tk.most_common(0) == []
    assert tk.most_common(99) == [("a", 3), ("b", 2), ("c", 1)]


def test_properties_and_repr() -> None:
    tk: TopK[str] = TopK(3, width=64, depth=3, decay_base=1.5, seed=1)
    assert (tk.k, tk.width, tk.depth, tk.decay_base) == (3, 64, 3, 1.5)
    assert repr(tk) == "TopK(k=3, width=64, depth=3, decay_base=1.5, tracked=0, total=0)"


def test_default_width_scales_with_k() -> None:
    assert TopK(10).width == 1024
    assert TopK(100).width == 3200


def test_add_with_a_count_equals_repeated_adds() -> None:
    a: TopK[str] = TopK(3, width=8, seed=4)
    b: TopK[str] = TopK(3, width=8, seed=4)
    for key, n in [("x", 5), ("y", 3), ("z", 40), ("w", 2), ("x", 7)]:
        a.add(key, n)
        for _ in range(n):
            b.add(key)
    assert a.most_common() == b.most_common()
    assert a.total == b.total == 57
    assert pickle.dumps(a) == pickle.dumps(b)


def test_add_zero_is_a_no_op_and_negative_is_rejected() -> None:
    tk: TopK[str] = TopK(3, seed=1)
    tk.add("a", 0)
    assert len(tk) == 0
    assert tk.total == 0
    with pytest.raises(ValueError, match="count"):
        tk.add("a", -1)


def test_update_accepts_iterables_and_mappings() -> None:
    tk: TopK[str] = TopK(5, seed=1)
    tk.update(["a", "b", "a"])
    tk.update({"b": 3, "c": 2})
    tk.update(Counter("cc"))
    assert tk.most_common() == [("b", 4), ("c", 4), ("a", 2)] or tk.most_common() == [
        ("c", 4),
        ("b", 4),
        ("a", 2),
    ]
    assert tk.total == 10


# --- the algorithm ---------------------------------------------------------------------------


def test_a_new_heavy_key_can_enter_a_full_set() -> None:
    # Regression for the typo in the paper's Algorithm 1 (line 8 prints C < n_min). With a strict
    # comparison a newcomer's counter stalls at n_min, so it can never reach n_min + 1 and is never
    # admitted, however frequent it is.
    tk: TopK[str] = TopK(2, width=4096, seed=1)
    tk.add("a", 5)
    tk.add("b", 5)
    assert len(tk) == 2
    tk.add("c", 6)
    assert "c" in tk
    assert ("c", 6) in tk.most_common()
    assert len(tk) == 2


def test_a_newcomer_below_the_minimum_is_not_admitted() -> None:
    tk: TopK[str] = TopK(2, width=4096, seed=1)
    tk.add("a", 5)
    tk.add("b", 5)
    tk.add("c", 3)
    assert "c" not in tk
    assert sorted(tk.most_common()) == [("a", 5), ("b", 5)]


def test_heavy_keys_survive_a_flood_of_distinct_light_keys() -> None:
    tk: TopK[str] = TopK(5, width=512, seed=3)
    heavy = {f"heavy{i}": 400 - 50 * i for i in range(5)}
    stream = [key for key, n in heavy.items() for _ in range(n)]
    stream += [f"light{i}" for i in range(5000)]
    random.Random(0).shuffle(stream)
    feed(tk, stream)
    assert {key for key, _ in tk.most_common()} == set(heavy)


def test_precision_on_a_zipf_stream() -> None:
    stream = zipf_stream(20_000, 2_000, seed=1)
    truth = Counter(stream)
    tk: TopK[str] = TopK(20, seed=2)
    feed(tk, stream)
    reported = {key for key, _ in tk.most_common()}
    actual = {key for key, _ in truth.most_common(20)}
    assert len(reported & actual) / 20 >= 0.9


@given(
    stream=st.lists(st.integers(0, 30), max_size=250),
    k=st.integers(1, 6),
    width=st.integers(1, 8),
    seed=st.integers(0, 2**32),
)
def test_reported_counts_never_exceed_true_counts(
    stream: list[int], k: int, width: int, seed: int
) -> None:
    # Theorem 2 end to end (32-bit fingerprints: no collisions among these 31 keys).
    tk: TopK[int] = TopK(k, width=width, seed=seed)
    truth = Counter(stream)
    feed(tk, list(stream))  # type: ignore[arg-type]
    assert len(tk) <= k
    for key, count in tk.most_common():
        assert count <= truth[key]
    for key in truth:
        assert tk.estimate(key) <= truth[key]


# --- determinism and state -------------------------------------------------------------------


def test_same_seed_and_stream_give_the_same_result() -> None:
    stream = zipf_stream(5_000, 500, seed=5)
    a: TopK[str] = TopK(10, width=64, seed=9)
    b: TopK[str] = TopK(10, width=64, seed=9)
    feed(a, list(stream))  # type: ignore[arg-type]
    feed(b, list(stream))  # type: ignore[arg-type]
    assert a.most_common() == b.most_common()


def test_unseeded_instances_get_independent_seeds() -> None:
    assert pickle.dumps(TopK(3)) != pickle.dumps(TopK(3))


def test_pickle_round_trip_mid_stream_matches_an_uninterrupted_run() -> None:
    rng = random.Random(1)
    stream: list[object] = [
        (int(rng.paretovariate(1.1)) % 60, "t") if i % 2 else f"s{int(rng.paretovariate(1.1)) % 60}"
        for i in range(6_000)
    ]
    straight: TopK[object] = TopK(8, width=32, seed=7)
    resumed: TopK[object] = TopK(8, width=32, seed=7)

    feed(straight, stream)
    feed(resumed, stream[:3_000])
    resumed = pickle.loads(pickle.dumps(resumed))
    feed(resumed, stream[3_000:])

    assert resumed.most_common() == straight.most_common()
    assert resumed.total == straight.total
    assert pickle.dumps(resumed) == pickle.dumps(straight)


def test_pickle_keeps_a_custom_key_encoder() -> None:
    tk: TopK[float] = TopK(3, key_encoder=float_encoder, seed=1)
    tk.update([1.5, 1.5, 2.5])
    clone = pickle.loads(pickle.dumps(tk))
    clone.add(1.5)
    assert clone.most_common() == [(1.5, 3), (2.5, 1)]


def test_unpickling_rejects_an_unknown_version() -> None:
    tk: TopK[str] = TopK(3, seed=1)
    state = tk.__getstate__()
    state["version"] = 999
    with pytest.raises(ValueError, match="version"):
        TopK(3, seed=1).__setstate__(state)


# --- keys ------------------------------------------------------------------------------------


def test_mixed_key_types_are_counted_separately() -> None:
    tk: TopK[object] = TopK(10, seed=1)
    for key in ["1", 1, b"1", (1,), None, True]:  # True == 1, so it joins the int 1
        tk.add(key)
    assert dict(tk.most_common()) == {"1": 1, 1: 2, b"1": 1, (1,): 1, None: 1}


def test_unsupported_keys_need_a_key_encoder() -> None:
    tk: TopK[float] = TopK(3, seed=1)
    with pytest.raises(TypeError, match="key_encoder"):
        tk.add(1.5)
    custom: TopK[float] = TopK(3, key_encoder=float_encoder, seed=1)
    custom.update([1.5, 1.5, 2.5])
    assert custom.most_common() == [(1.5, 2), (2.5, 1)]


def test_a_failed_add_leaves_the_state_unchanged() -> None:
    tk: TopK[object] = TopK(3, seed=1)
    tk.add("a")
    before = pickle.dumps(tk)
    with pytest.raises(TypeError):
        tk.add(1.5, 3)
    assert pickle.dumps(tk) == before
    assert tk.total == 1


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"k": 0}, "k"),
        ({"k": -3}, "k"),
        ({"k": 3, "width": 0}, "width"),
        ({"k": 3, "depth": 0}, "depth"),
        ({"k": 3, "decay_base": 1.0}, "decay_base"),
        ({"k": 3, "fingerprint_bits": 0}, "fingerprint_bits"),
        ({"k": 3, "fingerprint_bits": 33}, "fingerprint_bits"),
        ({"k": 3, "seed": -1}, "seed"),
    ],
)
def test_invalid_arguments_raise_value_error(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        TopK(**kwargs)  # type: ignore[arg-type]


def test_most_common_rejects_a_negative_n() -> None:
    with pytest.raises(ValueError, match="n"):
        TopK(3, seed=1).most_common(-1)


# --- Optimizations I and II (observable only through fingerprint collisions) -----------------


def collision_scenario(cls: type[TopK[str]] = TopK) -> TopK[str]:
    """A full set (min 3) where "a" has landed on a bucket that holds 50 under its fingerprint.

    That is what a fingerprint collision with an elephant flow looks like to a mouse flow.
    """
    tk = cls(2, width=1, depth=1, seed=1)
    tk._heap.push("b", 3)
    tk._heap.push("c", 3)
    slots, fp = tk._sketch.locate(tk._encode("a"))
    tk._sketch.fps[slots[0]] = fp
    tk._sketch.counts[slots[0]] = 50
    return tk


def test_optimizations_keep_a_colliding_key_out_and_its_bucket_untouched() -> None:
    tk = collision_scenario()
    tk.add("a")
    assert "a" not in tk  # Opt I: 50 is not n_min + 1, so it cannot be a legitimate newcomer
    assert tk._sketch.counts[0] == 50  # Opt II: an unmonitored key does not add to that bucket
    assert sorted(tk.most_common()) == [("b", 3), ("c", 3)]


def test_without_optimization_i_the_colliding_key_is_admitted() -> None:
    class NoOptI(TopK[str]):
        _exact_admission = False

    tk = collision_scenario(NoOptI)
    tk.add("a")
    assert ("a", 50) in tk.most_common()


def test_without_the_optimizations_the_colliding_key_is_admitted_and_inflates_the_bucket() -> None:
    class Basic(TopK[str]):
        _selective_increment = False
        _exact_admission = False

    tk = collision_scenario(Basic)
    tk.add("a")
    assert ("a", 51) in tk.most_common()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"k": 1.5},
        {"k": 2.0},
        {"k": float("nan")},
        {"k": "3"},
        {"k": 3, "width": 2.5},
        {"k": 3, "width": float("nan")},
        {"k": 3, "depth": 1.5},
        {"k": 3, "fingerprint_bits": 8.0},
        {"k": 3, "seed": 1.5},
    ],
)
def test_integer_arguments_must_be_integers(kwargs: dict[str, object]) -> None:
    # TopK(1.5) would admit two keys before reporting itself full, and TopK(nan) would never be
    # full, so the memory bound would silently fail.
    with pytest.raises(TypeError, match="integer"):
        TopK(**kwargs)  # type: ignore[arg-type]


def test_integer_like_arguments_are_accepted() -> None:
    class Index:
        def __index__(self) -> int:
            return 3

    tk: TopK[str] = TopK(Index(), width=Index(), depth=Index(), seed=Index())  # type: ignore[arg-type]
    assert (tk.k, tk.width, tk.depth, tk.seed) == (3, 3, 3, 3)
