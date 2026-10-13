"""Find the busiest clients in a stream of access-log lines without remembering every client.

    python examples/log_heavy_hitters.py

A few clients make most of the requests while a long tail shows up once or twice: the case
HeavyKeeper is built for. The sketch uses a fixed few kilobytes however many clients appear.
"""

import itertools
import random
from collections import Counter
from collections.abc import Iterator

from topk import TopK

REQUESTS = 200_000
CLIENTS = 100_000
K = 10


def requests(n: int, clients: int, seed: int) -> Iterator[str]:
    """Yield ``n`` client addresses with Zipf-distributed activity (client 0 is the busiest)."""
    rng = random.Random(seed)
    cumulative = list(itertools.accumulate(1 / (rank + 1) ** 1.1 for rank in range(clients)))
    for rank in rng.choices(range(clients), cum_weights=cumulative, k=n):
        yield f"10.{rank >> 16 & 255}.{rank >> 8 & 255}.{rank & 255}"


def main() -> float:
    stream = list(requests(REQUESTS, CLIENTS, seed=7))
    exact = Counter(stream)  # what we are avoiding in production: one counter per client

    top: TopK[str] = TopK(K, seed=1)
    top.update(stream)

    print(f"{len(stream):,} requests from {len(exact):,} distinct clients")
    print(f"sketch: width={top.width} depth={top.depth} ({12 * top.width * top.depth:,} bytes)\n")
    print(f"{'client':<14}{'reported':>10}{'exact':>8}")
    for client, count in top.most_common():
        print(f"{client:<14}{count:>10}{exact[client]:>8}")

    truth = {client for client, _ in exact.most_common(K)}
    precision = len({client for client, _ in top.most_common()} & truth) / K
    print(f"\nprecision: {precision:.0%} of the true top {K} found")
    return precision


if __name__ == "__main__":
    main()
