"""Compare HeavyKeeper variants on a Zipf stream, with the metrics of the paper's Section 5.2.

    python -m benchmarks.accuracy                       # 500k items, k = 100
    python -m benchmarks.accuracy --fingerprint-bits 8 16 32 --width-factors 4 8 32

"full" is ``TopK`` (Algorithm 1 with Optimizations I and II); "basic" is the version without them.
They differ only when fingerprint collisions happen, so compare them at small fingerprint sizes.
With ``--with-heavykeeper`` the PyPI package of that name (a Rust implementation of the basic
algorithm with its own hashing; ``pip install heavykeeper``) is added as a third row.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from typing import Any

from benchmarks.common import BasicTopK, Scores, run, score, zipf_stream
from topk import TopK

Report = Callable[[], Sequence[tuple[Hashable, int]]]


def _measure(
    build: Callable[[int], Report], truth: Counter[Hashable], k: int, seeds: int
) -> tuple[Scores, float]:
    """Average ``seeds`` runs; ``build(seed)`` feeds the stream and returns a report function."""
    results = []
    elapsed = []
    for seed in range(1, seeds + 1):
        start = time.perf_counter()
        report = build(seed)
        elapsed.append(time.perf_counter() - start)
        results.append(score(report(), truth, k))
    mean = Scores(
        precision=statistics.fmean(r.precision for r in results),
        are=statistics.fmean(r.are for r in results),
        aae=statistics.fmean(r.aae for r in results),
    )
    return mean, statistics.fmean(elapsed)


def _heavykeeper_package(stream: Sequence[Any], k: int, width: int, seed: int) -> Report:
    import heavykeeper  # the PyPI package; not a dependency of this project

    hk = heavykeeper.HeavyKeeper.with_seed(k, width, 2, 1 / 1.08, seed)
    hk.add_bulk([str(key) for key in stream])
    return lambda: [(int(key), count) for key, count in hk.list()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--n", type=int, default=500_000, help="stream length")
    parser.add_argument("--distinct", type=int, default=100_000, help="size of the key universe")
    parser.add_argument("--skew", type=float, default=1.1, help="Zipf exponent")
    parser.add_argument("--k", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=3, help="runs to average per row")
    parser.add_argument(
        "--width-factors", type=int, nargs="+", default=[8, 32], help="width = factor * k"
    )
    parser.add_argument("--fingerprint-bits", type=int, nargs="+", default=[8, 32])
    parser.add_argument("--with-heavykeeper", action="store_true", help="add the PyPI package")
    args = parser.parse_args(argv)

    stream = zipf_stream(args.n, args.distinct, args.skew, seed=1)
    truth: Counter[Hashable] = Counter(stream)
    print(
        f"stream: {args.n:,} items, {len(truth):,} distinct, Zipf skew {args.skew}; "
        f"k = {args.k}, mean of {args.seeds} seed(s)"
    )
    header = f"{'variant':<12}{'fp bits':>8}{'width':>8}{'precision':>11}"
    print(header + f"{'ARE':>12}{'AAE':>12}{'ins/s':>10}")

    def row(name: str, bits: str, width: int, scores: Scores, seconds: float) -> None:
        rate = f"{args.n / seconds / 1000:.0f}k"
        print(
            f"{name:<12}{bits:>8}{width:>8}{scores.precision:>11.3f}"
            f"{scores.are:>12.4g}{scores.aae:>12.4g}{rate:>10}"
        )

    for bits in args.fingerprint_bits:
        for factor in args.width_factors:
            width = factor * args.k
            for name, cls in (("full", TopK), ("basic", BasicTopK)):

                def build(
                    seed: int, cls: type[TopK[Any]] = cls, width: int = width, bits: int = bits
                ) -> Report:
                    tk = run(cls, stream, args.k, width=width, fingerprint_bits=bits, seed=seed)
                    return tk.most_common

                row(name, str(bits), width, *_measure(build, truth, args.k, args.seeds))

    if args.with_heavykeeper:
        try:
            import heavykeeper  # noqa: F401
        except ImportError:
            print("heavykeeper package not installed; skipping (pip install heavykeeper)")
        else:
            for factor in args.width_factors:
                width = factor * args.k
                scores, seconds = _measure(
                    lambda seed, width=width: _heavykeeper_package(stream, args.k, width, seed),
                    truth,
                    args.k,
                    args.seeds,
                )
                row("heavykeeper", "-", width, scores, seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
