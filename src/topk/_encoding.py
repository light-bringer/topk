"""Stable, injective byte encoding of keys.

Sketch bucket positions and fingerprints are derived from ``encode_key(key)``. Python's built-in
``hash()`` is randomized per process for ``str`` and ``bytes``, so it cannot be used: a pickled
sketch would map keys to different buckets after being loaded in another process.

The encoding is prefix-free, so it is injective, including for nested tuples. It is also
consistent with ``==``: keys that compare equal encode identically (``True`` and ``1``, an
``IntEnum`` member and its int value, a ``str`` subclass and the plain string).

Wire format (``u64`` is an 8-byte big-endian unsigned integer):

========  ============================================================
``None``  ``b"N"``
``int``   ``b"I" + u64(len) + two's-complement big-endian bytes``
``str``   ``b"S" + u64(len) + UTF-8 bytes`` (``surrogatepass``)
``bytes`` ``b"B" + u64(len) + bytes``
``tuple`` ``b"T" + u64(item count) + concatenated item encodings``
========  ============================================================

Subclasses of ``str``, ``int``, ``bytes`` and ``tuple`` (``bool``, ``IntEnum``, ``namedtuple``,
...) are accepted only if they inherit ``__eq__`` and ``__hash__`` from the builtin unchanged. A
subclass that redefines equality can make distinct dict keys encode alike (or equal ones encode
differently), silently merging or splitting their counts, so it must go through a custom
``key_encoder``.
The encoding is computed with the builtins' own methods (``str.encode(key)``, ``int.to_bytes(n)``,
...), so a subclass cannot change it by overriding them.

Pickled sketches depend on this format, so changing it is a breaking change.
"""

from __future__ import annotations

__all__ = ["encode_key"]


def _u64(n: int) -> bytes:
    return n.to_bytes(8, "big")


def encode_key(key: object) -> bytes:
    """Return the canonical byte encoding of ``key``.

    Supports ``None``, ``bool``, ``int``, ``str``, ``bytes`` and (nested) tuples of these, plus
    subclasses that keep their base type's equality and hashing.

    Raises:
        TypeError: for any other type. Pass a custom ``key_encoder`` to ``TopK`` to count other
            kinds of keys.
    """
    if isinstance(key, str):
        _require_base_equality(key, str)
        data = str.encode(key, "utf-8", "surrogatepass")
        return b"S" + _u64(len(data)) + data
    # bool is an int subclass and True == 1, so it encodes as the int it equals.
    if isinstance(key, int):
        _require_base_equality(key, int)
        return _encode_int(key)
    if isinstance(key, bytes):
        _require_base_equality(key, bytes)
        return b"B" + _u64(bytes.__len__(key)) + key
    if isinstance(key, tuple):
        _require_base_equality(key, tuple)
        return _encode_tuple(key)
    if key is None:
        return b"N"
    raise TypeError(
        f"unsupported key type {type(key).__name__!r}: the default key encoding handles None, "
        "bool, int, str, bytes and tuples of these; pass key_encoder= to count other (hashable) "
        "key types"
    )


def _require_base_equality(key: object, base: type) -> None:
    t = type(key)
    if t is not base and not _keeps_base_equality(t, base):
        raise TypeError(
            f"unsupported key type {t.__name__!r}: it overrides __eq__ or __hash__ of "
            f"{base.__name__}, so equal keys might not encode alike; pass key_encoder= to count it"
        )


def _keeps_base_equality(t: type, base: type) -> bool:
    return t.__eq__ is base.__eq__ and t.__hash__ is base.__hash__


def _encode_int(n: int) -> bytes:
    data = int.to_bytes(n, (int.bit_length(n) + 8) // 8, "big", signed=True)
    return b"I" + _u64(len(data)) + data


def _encode_tuple(items: tuple[object, ...]) -> bytes:
    count = tuple.__len__(items)
    return b"T" + _u64(count) + b"".join(map(encode_key, tuple.__iter__(items)))
