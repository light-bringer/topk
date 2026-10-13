"""Measure insertion throughput of the pure-Python implementation.

    python -m benchmarks.throughput
    python -m benchmarks.throughput --n 1000000 --repeats 5

Reports the median of several runs over a pre-generated Zipf stream, so the time is only
``TopK.add``. Numbers depend heavily on the interpreter: PyPy is typically several times faster.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import time
from collections.abc import Sequence
from typing import Any

from benchmarks.common import BasicTopK, zipf_stream
from topk import TopK


def inserts_per_second(cls: type[TopK[Any]], stream: Sequence[int], k: int, **kwargs: Any) -> float:
    tk = cls(k, **kwargs)
    add = tk.add
    start = time.perf_counter()
    for key in stream:
        add(key)
    return len(stream) / (time.perf_counter() - start)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--n", type=int, default=500_000)
    parser.add_argument("--distinct", type=int, default=100_000)
    parser.add_argument("--skew", type=float, default=1.1)
    parser.add_argument("--k", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)

    stream = zipf_stream(args.n, args.distinct, args.skew, seed=1)
    interpreter = f"{platform.python_implementation()} {platform.python_version()}"
    print(f"{interpreter}, {args.n:,} inserts, k = {args.k}")
    print(f"{'variant':<10}{'width':>8}{'Minserts/s (median)':>24}")
    for name, cls in (("full", TopK), ("basic", BasicTopK)):
        for factor in (8, 32, 128):
            width = factor * args.k
            rates = [
                inserts_per_second(cls, stream, args.k, width=width, seed=r + 1)
                for r in range(args.repeats)
            ]
            print(f"{name:<10}{width:>8}{statistics.median(rates) / 1e6:>24.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
