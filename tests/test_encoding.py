import enum
from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from topk._encoding import encode_key

_scalars = st.none() | st.booleans() | st.integers() | st.text() | st.binary()
_keys = st.recursive(
    _scalars,
    lambda children: st.lists(children, max_size=4).map(tuple),
    max_leaves=12,
)


@given(_keys, _keys)
def test_encoding_is_injective_and_consistent_with_equality(a: object, b: object) -> None:
    assert (a == b) == (encode_key(a) == encode_key(b))


@given(_keys)
def test_encoding_is_deterministic(key: object) -> None:
    assert encode_key(key) == encode_key(key)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (None, b"N"),
        (0, b"I" + (1).to_bytes(8, "big") + b"\x00"),
        (1, b"I" + (1).to_bytes(8, "big") + b"\x01"),
        (-1, b"I" + (1).to_bytes(8, "big") + b"\xff"),
        (128, b"I" + (2).to_bytes(8, "big") + b"\x00\x80"),
        ("a", b"S" + (1).to_bytes(8, "big") + b"a"),
        ("é", b"S" + (2).to_bytes(8, "big") + b"\xc3\xa9"),
        (b"a", b"B" + (1).to_bytes(8, "big") + b"a"),
        (
            (1, "a"),
            b"T"
            + (2).to_bytes(8, "big")
            + b"I"
            + (1).to_bytes(8, "big")
            + b"\x01"
            + b"S"
            + (1).to_bytes(8, "big")
            + b"a",
        ),
        ((), b"T" + (0).to_bytes(8, "big")),
    ],
)
def test_golden_vectors_pin_the_wire_format(key: object, expected: bytes) -> None:
    # Pickled sketches depend on this format being stable across releases.
    assert encode_key(key) == expected


def test_bool_encodes_like_the_equal_int() -> None:
    assert encode_key(True) == encode_key(1)
    assert encode_key(False) == encode_key(0)
    assert encode_key((True, 0)) == encode_key((1, False))


def test_str_and_bytes_with_the_same_content_differ() -> None:
    assert encode_key("a") != encode_key(b"a")


def test_tuple_nesting_is_not_flattened() -> None:
    assert encode_key(((1, 2), 3)) != encode_key((1, (2, 3)))
    assert encode_key(((1, 2), 3)) != encode_key((1, 2, 3))


# Hypothesis' default text() never generates surrogate code points, so cover them explicitly.
# A high surrogate followed by a low surrogate is not the one character U+1F600 that UTF-16 would
# make of them, and the encoding must not fuse them. They are built with chr() so that the
# source file holds no escape sequences or non-BMP characters that tooling could rewrite.
_HIGH, _LOW, _EMOJI = chr(0xD83D), chr(0xDE00), "\U0001f600"
_surrogate_text = st.text(st.sampled_from([_HIGH, _LOW, _EMOJI, "a"]), max_size=6)


@given(_surrogate_text, _surrogate_text)
def test_surrogate_text_is_injective_and_consistent_with_equality(a: str, b: str) -> None:
    assert (a == b) == (encode_key(a) == encode_key(b))


def test_lone_surrogates_are_encodable_and_distinct() -> None:
    assert encode_key(chr(0xD800)) != encode_key(chr(0xD801))
    # A separate high+low surrogate pair is a different string from the one emoji it resembles.
    assert _HIGH + _LOW != _EMOJI
    assert encode_key(_HIGH + _LOW) != encode_key(_EMOJI)


def test_huge_ints_are_supported() -> None:
    big = 10**5000
    assert encode_key(big) != encode_key(-big)
    assert encode_key(big) != encode_key(big + 1)


class _Color(enum.IntEnum):
    RED = 1


class _Name(str):
    __slots__ = ()


def test_int_and_str_subclasses_encode_like_their_base_value() -> None:
    assert encode_key(_Color.RED) == encode_key(1)
    assert encode_key(_Name("x")) == encode_key("x")


@pytest.mark.parametrize(
    "key",
    [1.5, [1], {1}, {"a": 1}, object(), bytearray(b"a"), frozenset({1}), ("a", 1.5), (["a"],)],
)
def test_unsupported_types_raise_type_error_naming_key_encoder(key: object) -> None:
    with pytest.raises(TypeError, match="key_encoder"):
        encode_key(key)


class _Point(NamedTuple):
    x: int
    y: int


class _Mode(str, enum.Enum):
    FAST = "fast"


class _IdentityInt(int):
    """Equal values but distinct dict keys: equality falls back to identity."""

    __eq__ = object.__eq__
    __hash__ = object.__hash__


class _CaseInsensitiveStr(str):
    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and self.lower() == other.lower()

    def __hash__(self) -> int:
        return hash(self.lower())


class _LooseBytes(bytes):
    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return 0


class _LooseTuple(tuple):  # type: ignore[type-arg]
    __hash__ = object.__hash__


def test_subclasses_that_keep_value_semantics_encode_like_their_base() -> None:
    assert encode_key(_Point(1, 2)) == encode_key((1, 2))
    assert encode_key(_Mode.FAST) == encode_key("fast")
    assert encode_key((_Point(1, 2), _Color.RED)) == encode_key(((1, 2), 1))


@pytest.mark.parametrize(
    "key",
    [
        _IdentityInt(7),
        _CaseInsensitiveStr("a"),
        _LooseBytes(b"a"),
        _LooseTuple((1,)),
        (1, _IdentityInt(7)),  # also when nested
    ],
)
def test_subclasses_that_override_equality_are_rejected(key: object) -> None:
    # Such keys can be distinct heap keys with identical encodings (or equal heap keys with
    # different ones), which would silently conflate or split their counts.
    with pytest.raises(TypeError, match="__eq__") as excinfo:
        encode_key(key)
    assert "key_encoder" in str(excinfo.value)
