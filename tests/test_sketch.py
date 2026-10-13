import hashlib
import math
import pickle
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from topk._sketch import PySketch

B = 1.08


class ScriptedRandom(random.Random):
    """A Random whose random() replays scripted values and fails on any unexpected draw."""

    def __new__(cls, *values: float) -> "ScriptedRandom":
        # Before Python 3.11, Random.__new__ rejects more than one positional argument.
        return super().__new__(cls)

    def __init__(self, *values: float) -> None:
        super().__init__(0)
        self._script = list(values)

    def random(self) -> float:
        if not self._script:
            raise AssertionError("unexpected random draw")
        return self._script.pop(0)


def make(
    *,
    width: int = 1,
    depth: int = 1,
    b: float = B,
    fp_bits: int = 32,
    seed: int = 7,
    rng: random.Random | None = None,
) -> PySketch:
    return PySketch(width, depth, b, fp_bits, seed, rng=rng)


def reference_locate(
    data: bytes, seed: int, width: int, depth: int, fp_bits: int
) -> tuple[tuple[int, ...], int]:
    """The documented hashing scheme, re-derived independently of the implementation."""
    digest = hashlib.blake2b(data, digest_size=16, key=seed.to_bytes(8, "big")).digest()
    x = int.from_bytes(digest, "little")
    h1 = x & (2**64 - 1)
    h2 = ((x >> 64) & (2**32 - 1)) | 1
    fp = (x >> 96) & ((1 << fp_bits) - 1)
    return tuple(j * width + (h1 + j * h2) % width for j in range(depth)), fp


# --- hashing ---------------------------------------------------------------------------------


@given(
    st.binary(max_size=40),
    st.integers(0, 2**64 - 1),
    st.integers(1, 5000),
    st.integers(1, 6),
    st.integers(1, 32),
)
def test_locate_matches_the_documented_scheme(
    data: bytes, seed: int, width: int, depth: int, fp_bits: int
) -> None:
    sketch = make(width=width, depth=depth, fp_bits=fp_bits, seed=seed)
    assert sketch.locate(data) == reference_locate(data, seed, width, depth, fp_bits)


def test_locate_golden_vector_pins_the_hash_scheme() -> None:
    # Pickled sketches depend on this mapping staying stable across releases.
    sketch = make(width=1024, depth=2, fp_bits=32, seed=42)
    assert sketch.locate(b"heavykeeper") == ((288, 1819), 4242471597)


@given(st.binary(max_size=40), st.integers(1, 5000), st.integers(1, 6), st.integers(1, 32))
def test_locate_stays_inside_each_row_and_fingerprint_mask(
    data: bytes, width: int, depth: int, fp_bits: int
) -> None:
    slots, fp = make(width=width, depth=depth, fp_bits=fp_bits).locate(data)
    assert len(slots) == depth
    for j, slot in enumerate(slots):
        assert j * width <= slot < (j + 1) * width
    assert 0 <= fp < 1 << fp_bits


def test_locate_depends_on_the_seed() -> None:
    assert make(seed=1).locate(b"abc") != make(seed=2).locate(b"abc")


def test_width_one_maps_every_key_to_the_same_bucket_in_each_row() -> None:
    sketch = make(width=1, depth=3)
    assert sketch.locate(b"a")[0] == sketch.locate(b"b")[0] == (0, 1, 2)
    assert sketch.locate(b"a")[1] != sketch.locate(b"b")[1]


# --- decay probability -----------------------------------------------------------------------


def test_decay_probability_is_b_to_the_minus_c() -> None:
    sketch = make()
    for c in (1, 2, 10, 100, 400):
        assert sketch.decay_probability(c) == pytest.approx(B**-c)


def test_decay_probability_is_zero_past_the_cutoff() -> None:
    sketch = make()
    cutoff = sketch.decay_cutoff
    assert B**-cutoff < 2**-53 <= B ** -(cutoff - 1)
    assert sketch.decay_probability(cutoff - 1) > 0
    assert sketch.decay_probability(cutoff) == 0.0
    assert sketch.decay_probability(10**9) == 0.0


def assert_is_the_first_negligible_count(b: float, cutoff: int) -> None:
    assert cutoff >= 1
    assert b**-cutoff < 2**-53
    assert cutoff == 1 or b ** -(cutoff - 1) >= 2**-53


@pytest.mark.parametrize(
    "b",
    [
        1.08,
        1.0001,
        1.5,
        2.0,
        10.0,
        1e6,
        1e300,
        1 + 2**-52,
        # 53 * ln(2) / ln(b) evaluates to exactly 5.0 here although b**-5 is already below 2**-53,
        # so a floor(...) + 1 formula would answer 6.
        float.fromhex("0x1.8406003b2ae5dp+10"),
    ],
)
def test_decay_cutoff_is_the_first_count_with_a_negligible_probability(b: float) -> None:
    assert_is_the_first_negligible_count(b, make(b=b).decay_cutoff)


@given(st.floats(min_value=1 + 2**-20, max_value=1e300, allow_nan=False, allow_infinity=False))
def test_decay_cutoff_holds_for_any_base(b: float) -> None:
    assert_is_the_first_negligible_count(b, make(b=b).decay_cutoff)


def test_a_bucket_at_the_true_cutoff_never_consumes_a_draw() -> None:
    # b**-5 is already below 2**-53 for this base (see above), so a counter of 5 can never decay
    # and a draw would be wasted; the scripted RNG fails the test if one is taken.
    b = float.fromhex("0x1.8406003b2ae5dp+10")
    assert b**-5 < 2**-53
    sketch, slots, fp = seeded(5, other=True, b=b, rng=ScriptedRandom())
    other_fp = sketch.fps[0]
    assert sketch.insert(slots, fp, True, 0) == 0
    assert (sketch.fps[0], sketch.counts[0]) == (other_fp, 5)


def test_decay_beyond_the_lookup_table_is_still_computed_exactly() -> None:
    # b this close to 1 puts the cutoff far beyond the precomputed table.
    sketch = make(b=1.0001)
    assert sketch.decay_cutoff > 10_000
    assert sketch.decay_probability(9_000) == pytest.approx(1.0001**-9_000)


# --- insert: the three cases of Section 3.2 --------------------------------------------------


def seeded(c: int, other: bool = False, **kw: object) -> tuple[PySketch, tuple[int, ...], int]:
    """A depth-1 sketch whose only bucket holds count c, owned by us (or by another flow)."""
    sketch = make(**kw)  # type: ignore[arg-type]
    slots, fp = sketch.locate(b"a")
    sketch.fps[0] = (fp + 1) % (1 << 32) if other else fp
    sketch.counts[0] = c
    return sketch, slots, fp


def test_case1_empty_bucket_is_claimed_with_count_one() -> None:
    sketch = make()
    slots, fp = sketch.locate(b"a")
    assert sketch.insert(slots, fp, False, 0) == 1
    assert (sketch.fps[0], sketch.counts[0]) == (fp, 1)


@pytest.mark.parametrize(
    ("monitored", "n_min", "c", "expected"),
    [
        (True, 0, 5, 6),  # monitored flows always increment
        (False, 5, 5, 6),  # c <= n_min increments (the paper prints "<"; the reference code "<=")
        (False, 9, 5, 6),
        (False, 4, 5, 5),  # Opt II: c > n_min and not monitored -> fingerprint collision, hold
        (False, 0, 1, 1),  # heap not full (n_min = 0): only monitored flows may increment
    ],
)
def test_case2_matching_fingerprint_and_selective_increment(
    monitored: bool, n_min: int, c: int, expected: int
) -> None:
    sketch, slots, fp = seeded(c)
    assert sketch.insert(slots, fp, monitored, n_min) == expected
    assert sketch.counts[0] == expected


def test_case3_collision_without_decay_changes_nothing() -> None:
    # b = 2, c = 1 -> p = 0.5; a draw of exactly 0.5 is not < p.
    sketch, slots, fp = seeded(1, other=True, b=2.0, rng=ScriptedRandom(0.5))
    other_fp = sketch.fps[0]
    assert sketch.insert(slots, fp, True, 0) == 0
    assert (sketch.fps[0], sketch.counts[0]) == (other_fp, 1)


def test_case3_decay_decrements_but_keeps_the_resident_flow() -> None:
    sketch, slots, fp = seeded(3, other=True, b=2.0, rng=ScriptedRandom(0.0))
    other_fp = sketch.fps[0]
    assert sketch.insert(slots, fp, True, 0) == 0
    assert (sketch.fps[0], sketch.counts[0]) == (other_fp, 2)


def test_case3_decay_to_zero_hands_the_bucket_to_the_new_flow() -> None:
    sketch, slots, fp = seeded(1, other=True, b=2.0, rng=ScriptedRandom(0.49))
    assert sketch.insert(slots, fp, False, 0) == 1
    assert (sketch.fps[0], sketch.counts[0]) == (fp, 1)


def test_case3_never_draws_when_the_probability_is_zero() -> None:
    sketch = make()
    cutoff = sketch.decay_cutoff
    sketch, slots, fp = seeded(cutoff, other=True, rng=ScriptedRandom())
    other_fp = sketch.fps[0]
    assert sketch.insert(slots, fp, True, 0) == 0
    assert (sketch.fps[0], sketch.counts[0]) == (other_fp, cutoff)


def test_insert_decays_exactly_when_the_draw_is_below_the_probability() -> None:
    probe = make()
    for c in range(2, probe.decay_cutoff + 3):
        p = probe.decay_probability(c)
        draws = [p * 0.999] if p > 0 else []
        sketch, slots, fp = seeded(c, other=True, rng=ScriptedRandom(*draws))
        sketch.insert(slots, fp, True, 0)
        assert sketch.counts[0] == (c - 1 if p > 0 else c)
        if p > 0:
            sketch, slots, fp = seeded(c, other=True, rng=ScriptedRandom(p))
            sketch.insert(slots, fp, True, 0)
            assert sketch.counts[0] == c


def test_figure_1_example_across_three_arrays() -> None:
    # Paper Figure 1: packet P5 of flow f3 maps to one bucket per array.
    #   array 1: (F4, 21)  collision, decays with p = b^-21 -> survives this time
    #   array 2: (F1, 1)   collision, decays to 0           -> f3 takes the bucket with count 1
    #   array 3: (F3, 7)   own fingerprint                  -> incremented to 8
    sketch = make(width=1, depth=3, rng=ScriptedRandom(0.5, 0.0))
    slots, f3 = sketch.locate(b"f3")
    sketch.fps[:] = [f3 + 1, f3 + 2, f3]
    sketch.counts[:] = [21, 1, 7]
    assert sketch.insert(slots, f3, True, 0) == 8
    assert sketch.fps == [f3 + 1, f3, f3]
    assert sketch.counts == [21, 1, 8]


# --- estimate --------------------------------------------------------------------------------


def test_estimate_is_the_largest_count_among_matching_fingerprints() -> None:
    sketch = make(width=1, depth=3)
    slots, fp = sketch.locate(b"a")
    sketch.fps[:] = [fp, fp + 1, fp]
    sketch.counts[:] = [4, 99, 9]
    assert sketch.estimate(slots, fp) == 9


def test_estimate_is_zero_when_no_fingerprint_matches() -> None:
    sketch = make(width=1, depth=2)
    slots, fp = sketch.locate(b"a")
    sketch.fps[:] = [fp + 1, fp + 2]
    sketch.counts[:] = [5, 6]
    assert sketch.estimate(slots, fp) == 0
    assert make().estimate(*make().locate(b"a")) == 0


# --- Theorem 2: no overestimation ------------------------------------------------------------


@given(
    stream=st.lists(st.integers(0, 30), max_size=300),
    width=st.integers(1, 8),
    depth=st.integers(1, 3),
    seed=st.integers(0, 2**32),
)
def test_theorem_2_estimates_never_exceed_true_counts(
    stream: list[int], width: int, depth: int, seed: int
) -> None:
    sketch = make(width=width, depth=depth, seed=seed, rng=random.Random(seed))
    located = {key: sketch.locate(str(key).encode()) for key in range(31)}
    # Theorem 2 assumes no fingerprint collisions; with 32-bit fingerprints there are none here.
    assert len({fp for _, fp in located.values()}) == 31
    flags = random.Random(seed ^ 0x5EED)  # vary the Opt II inputs; the theorem holds for any
    truth: dict[int, int] = {}
    for key in stream:
        slots, fp = located[key]
        sketch.insert(slots, fp, flags.random() < 0.5, flags.randrange(13))
        truth[key] = truth.get(key, 0) + 1
        # Inserting a flow can only raise that flow's own counters, so it is the one to check.
        assert sketch.estimate(slots, fp) <= truth[key]
    for key, count in truth.items():
        assert sketch.estimate(*located[key]) <= count


# --- state -----------------------------------------------------------------------------------


def _feed(sketch: PySketch, stream: list[int]) -> None:
    for key in stream:
        slots, fp = sketch.locate(str(key).encode())
        sketch.insert(slots, fp, key % 3 == 0, 2)


def test_pickle_round_trip_continues_identically() -> None:
    rng = random.Random(1)
    stream = [int(rng.paretovariate(1.2)) % 40 for _ in range(4000)]
    straight = make(width=16, depth=2, seed=5, rng=random.Random(5))
    resumed = make(width=16, depth=2, seed=5, rng=random.Random(5))

    _feed(straight, stream)
    _feed(resumed, stream[:2000])
    resumed = pickle.loads(pickle.dumps(resumed))
    _feed(resumed, stream[2000:])

    assert (resumed.fps, resumed.counts) == (straight.fps, straight.counts)


def test_setstate_rejects_an_unknown_scheme() -> None:
    state = make().__getstate__()
    bad = ("some-other-scheme", *state[1:])
    with pytest.raises(ValueError, match="scheme"):
        make().__setstate__(bad)


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"width": 0}, "width"),
        ({"depth": 0}, "depth"),
        ({"b": 1.0}, "decay_base"),
        ({"b": 0.5}, "decay_base"),
        ({"b": math.nan}, "decay_base"),
        ({"b": math.inf}, "decay_base"),
        ({"fp_bits": 0}, "fingerprint_bits"),
        ({"fp_bits": 33}, "fingerprint_bits"),
        ({"seed": -1}, "seed"),
        ({"seed": 2**64}, "seed"),
    ],
)
def test_invalid_parameters_raise_value_error(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        make(**kwargs)  # type: ignore[arg-type]
