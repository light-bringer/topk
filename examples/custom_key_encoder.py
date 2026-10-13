"""Count keys the default encoder does not know, with ``key_encoder``.

    python examples/custom_key_encoder.py

The default encoder handles None, bool, int, str, bytes and tuples of these. For anything else
supply a function from a key to ``bytes`` that maps equal keys to equal bytes. Keys must still be
hashable, and the function must be defined at module level if the TopK is to be pickled.
"""

import pickle
from dataclasses import dataclass

from topk import TopK


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str


def encode_endpoint(endpoint: Endpoint) -> bytes:
    return f"{endpoint.method} {endpoint.path}".encode()


TRAFFIC = (
    [Endpoint("GET", "/")] * 40
    + [Endpoint("GET", "/search")] * 25
    + [Endpoint("POST", "/login")] * 10
    + [Endpoint("GET", f"/item/{i}") for i in range(30)]
)


def main() -> list[tuple[Endpoint, int]]:
    try:
        TopK[Endpoint](3).add(Endpoint("GET", "/"))
    except TypeError as error:
        print(f"default encoder: {error}\n")

    top: TopK[Endpoint] = TopK(3, key_encoder=encode_endpoint, seed=1)
    top.update(TRAFFIC)
    for endpoint, count in top.most_common():
        print(f"{count:>3}  {endpoint.method:<5}{endpoint.path}")

    restored = pickle.loads(pickle.dumps(top))  # the encoder travels with the instance
    assert restored.most_common() == top.most_common()
    return top.most_common()


if __name__ == "__main__":
    main()
