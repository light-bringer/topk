"""Save a TopK to disk and resume counting later, with ``pickle``.

    python examples/checkpointing.py

A pickled TopK keeps its sketch, its top-k set and its random-number state, so resuming gives
exactly the result of one uninterrupted run. Pickles are guaranteed only between the same release
of the library: loading state written by an incompatible version raises ``ValueError``.

Only unpickle data you trust. ``pickle.loads`` can run arbitrary code before ``TopK`` gets to check
the version, so that check is a compatibility guard, not a security boundary.
"""

import pickle
import random
import tempfile
from pathlib import Path

from topk import TopK


def main() -> list[tuple[str, int]]:
    rng = random.Random(3)
    events = [f"user-{int(rng.paretovariate(1.2)) % 500}" for _ in range(30_000)]
    first_half, second_half = events[:15_000], events[15_000:]

    with tempfile.TemporaryDirectory() as directory:
        checkpoint = Path(directory) / "topk.pickle"

        top: TopK[str] = TopK(5, seed=1)
        top.update(first_half)
        checkpoint.write_bytes(pickle.dumps(top))
        print(f"checkpoint after {top.total:,} events: {checkpoint.stat().st_size:,} bytes")

        resumed: TopK[str] = pickle.loads(checkpoint.read_bytes())
        resumed.update(second_half)

    uninterrupted: TopK[str] = TopK(5, seed=1)
    uninterrupted.update(events)

    for user, count in resumed.most_common():
        print(f"{count:>6}  {user}")
    assert resumed.most_common() == uninterrupted.most_common()
    print("identical to one uninterrupted run")
    return resumed.most_common()


if __name__ == "__main__":
    main()
