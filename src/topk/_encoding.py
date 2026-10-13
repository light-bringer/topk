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

Pickled sketches depend on this format, so changing it is a breaking change.
"""

from __future__ import annotations

__all__ = ["encode_key"]


def _u64(n: int) -> bytes:
    return n.to_bytes(8, "big")


def encode_key(key: object) -> bytes:
    """Return the canonical byte encoding of ``key``.

    Supports ``None``, ``bool``, ``int``, ``str``, ``bytes`` and (nested) tuples of these.

    Raises:
        TypeError: for any other type. Pass a custom ``key_encoder`` to ``TopK`` to count other
            kinds of keys.
    """
    if isinstance(key, str):
        data = key.encode("utf-8", "surrogatepass")
        return b"S" + _u64(len(data)) + data
    # bool is an int subclass and True == 1, so it encodes as the int it equals.
    if isinstance(key, int):
        return _encode_int(key)
    if isinstance(key, bytes):
        return b"B" + _u64(len(key)) + key
    if isinstance(key, tuple):
        return _encode_tuple(key)
    if key is None:
        return b"N"
    raise TypeError(
        f"unsupported key type {type(key).__name__!r}: the default key encoding handles None, "
        "bool, int, str, bytes and tuples of these; pass key_encoder= to count other (hashable) "
        "key types"
    )


def _encode_int(n: int) -> bytes:
    data = n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True)
    return b"I" + _u64(len(data)) + data


def _encode_tuple(items: tuple[object, ...]) -> bytes:
    return b"T" + _u64(len(items)) + b"".join(map(encode_key, items))
