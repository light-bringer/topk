# heavykeeper-topk

[![CI](https://github.com/light-bringer/topk/actions/workflows/ci.yml/badge.svg)](https://github.com/light-bringer/topk/actions/workflows/ci.yml)

Find the **top-k most frequent items** of a data stream in **fixed memory**, however many distinct
items the stream holds. A pure-Python implementation of **HeavyKeeper** (Gong et al., USENIX ATC
'18), including the paper's accuracy optimizations. No dependencies, any hashable key, typed,
picklable.

> **Status: alpha, not on PyPI yet.** Until the first release, install from source:
> `pip install git+https://github.com/light-bringer/topk`. The distribution will be named
> `heavykeeper-topk`; the import name is `topk`. Python 3.10+, CPython and PyPy.

```pycon
>>> from topk import TopK
>>> top = TopK(3, seed=1)  # track the 3 most frequent keys
>>> top.update("banana")  # any iterable of hashable keys, or a {key: count} mapping
>>> top.most_common()
[('a', 3), ('n', 2), ('b', 1)]
>>> top.estimate("a"), "n" in top, len(top), top.total
(3, True, 3, 6)
```

## Why HeavyKeeper

A `Counter` needs one entry per distinct key. When the stream holds millions of distinct keys
(client IPs, URLs, user IDs, flow identifiers) but you only want the busiest few, that is wasteful.
HeavyKeeper keeps a small table of (fingerprint, counter) buckets and **decays** the counters of
keys that collide with a different key: a small key's counter melts away under collisions, a large
key's counter is hard to dislodge. The result is accurate counts for the heavy keys in memory that
does not grow with the stream.

- **Bounded memory:** `12 * depth * width` bytes for the sketch (24.6 KB at the defaults of width
  1024 and depth 2) plus roughly 84 bytes per tracked key.
- **Accurate where it matters:** on a 500k-item Zipf stream the default settings find the true top
  100 with precision 1.000 and a mean relative error under 0.1% (see [Benchmarks](#benchmarks)).
- **Counts are never inflated:** a reported count never exceeds the true count (the paper's
  Theorem 2), barring a fingerprint collision, which 32-bit fingerprints make negligible.
- **Optimizations of the paper:** newcomers enter a full top-k set only at exactly `n_min + 1`
  (Optimization I), and a bucket matching only by fingerprint is not incremented unless its flow is
  monitored (Optimization II). Both matter when fingerprints are small; see the benchmarks.

## Usage

```pycon
>>> from topk import TopK
>>> top = TopK(2, seed=1)
>>> top.add("login", 40)  # count occurrences in bulk (costs O(count), see below)
>>> top.add("search", 25)
>>> top.add("logout")
>>> top.most_common()
[('login', 40), ('search', 25)]
>>> top.most_common(1)
[('login', 40)]
>>> list(top)  # tracked keys, largest first
['login', 'search']
>>> "logout" in top  # seen once, but not among the top 2
False
>>> top.estimate("never-seen")  # no bucket for a key that never appeared
0
```

| Call | What it does |
|---|---|
| `TopK(k, *, width=None, depth=2, decay_base=1.08, fingerprint_bits=32, seed=None, key_encoder=None)` | Create a tracker. |
| `add(key, count=1)` | Count `count` occurrences. The key is hashed once, but `count` unit insertions follow, so the cost is `O(count * depth)`: it is not a constant-time bulk update. |
| `update(items)` | Count every key of an iterable, or add every `key: count` of a mapping (like `Counter.update`). |
| `most_common(n=None)` | The tracked `(key, count)` pairs, largest first. Ties are in no specified order. |
| `estimate(key)` | The sketch's current estimate for `key`: 0 if it holds no bucket for it. A small key outside the top k can still show a small count while its buckets survive; use `key in top` to ask about membership. |
| `key in top`, `len(top)`, `iter(top)` | Membership in the top k, number tracked, tracked keys by count. |
| `top.k`, `.width`, `.depth`, `.decay_base`, `.seed`, `.total` | Read-only settings; `total` is the number of occurrences counted. |

Not thread-safe: guard a shared instance with a lock.

### Keys

Keys must be **hashable**. The default encoder handles `None`, `bool`, `int`, `str`, `bytes` and
tuples of these (and subclasses such as `IntEnum` or `namedtuple` that do not redefine `==` or
`hash`). For anything else, pass `key_encoder`, a function from a key to `bytes` that maps equal
keys to equal bytes:

```pycon
>>> top = TopK(2, key_encoder=lambda price: repr(price).encode(), seed=1)
>>> top.update([9.99, 9.99, 4.5, 9.99, 4.5, 1.0])
>>> top.most_common()
[(9.99, 3), (4.5, 2)]
```

Keys are encoded to bytes (not passed to `hash()`) because the built-in hash of `str` and `bytes`
changes between processes, which would make a pickled sketch useless. If the instance is to be
pickled, `key_encoder` must be a module-level function, not a lambda.
See [docs/examples.md](docs/examples.md) for a worked example.

### Saving state

```pycon
>>> import pickle
>>> top = TopK(2, seed=1)
>>> top.update("aabbbc")
>>> restored = pickle.loads(pickle.dumps(top))
>>> restored.update("bb")
>>> restored.most_common()
[('b', 5), ('a', 2)]
```

A restored instance continues exactly as an uninterrupted one would. State is guaranteed readable
only by the same release of the library; incompatible state raises `ValueError`.

## Choosing parameters

| Parameter | Default | Guidance |
|---|---|---|
| `k` | required | How many keys you want. The sketch should be sized for the *k-th* key, not the first. |
| `width` | `max(1024, 32 * k)` | Buckets per row. The main accuracy and memory knob. At the default, a 500k-item Zipf stream gave precision 1.000 and ARE under 0.001. Double it if the k-th and (k+1)-th keys are nearly tied. |
| `depth` | 2 | Rows (hash functions). The paper uses 2; more rows cost time for little gain. |
| `decay_base` | 1.08 | The paper's `b`. A colliding insertion decays a counter `C` with probability `b ** -C`. Larger `b` forgets small keys faster; the paper uses 1.08. Note this is `1 / decay` in the convention of Redis and the `heavykeeper` package. |
| `fingerprint_bits` | 32 | Collision resistance, 1 to 32. Storage is 4 bytes per bucket either way, so lowering it saves no memory here; it exists to study small fingerprints (see below). |
| `seed` | random | Seeds hashing and the decay RNG. Pass an int for reproducible results; read it back from `top.seed`. |

**Memory** (measured on CPython 3.13): the buckets take 12 bytes each, so `12 * depth * width`
bytes (the default `width=1024, depth=2` is 24.6 KB); each tracked key adds about 84 bytes plus the
key object itself.

**Error.** For a heavy key held in its bucket the paper's Theorem 3 bounds the chance that the
estimate falls short by `epsilon * N` (N is the stream length, `n` the key's true count) by
`1 / (epsilon * width * n * (b - 1))`. For `width = 3200`, `n = 5000`, `b = 1.08` and a tolerated
shortfall of 100 in a 500k stream (`epsilon = 100 / 500000`):

```pycon
>>> epsilon, width, n, b = 100 / 500_000, 3200, 5000, 1.08
>>> round(1 / (epsilon * width * n * (b - 1)), 4)
0.0039
```

That is the paper's bound under its assumptions (no fingerprint collisions, an elephant key that
stays in its bucket); measured errors are far smaller.

## Differences from the paper

The implementation follows Algorithm 1 with Optimizations I and II. Deliberate differences:

1. **Optimization II compares `c <= n_min`**, as the authors' code does. The paper prints
   `C < n_min`; with a strict comparison a newcomer's counter stalls at `n_min`, so it could never
   reach `n_min + 1` and enter a full set.
2. `n_min` is 0 until the top-k set is full (as in the authors' code).
3. Empty buckets are handled explicitly. Read literally, the pseudocode would decrement an empty
   bucket to -1.
4. A key whose estimate is 0 is never admitted to the set.
5. The decay probability is exactly `b ** -C` (the authors' code uses `1 / floor(b ** C)`).
6. Fingerprints are 32 bits by default (the paper used 16); 1 to 32 are accepted.
7. Hashing is keyed BLAKE2b with double hashing over a canonical key encoding, so bucket positions
   survive pickling. (The authors' code uses BOBHash.)
8. Counters are 64-bit, so they never saturate.

Optimization III (replacing the heap by a threshold array) is not implemented.

## Benchmarks

500k-item Zipf stream (skew 1.1, 100k possible keys), `k = 100`, CPython 3.13, one machine:

| Configuration | precision | ARE | AAE |
|---|---|---|---|
| default (32-bit fingerprints, `width = 32k`), five seeds | 1.000 | 0.0005 to 0.0008 | 0.3 to 0.5 |
| 8-bit fingerprints, `width = 8k`: this library | 0.99 to 1.00 | about 0.005 | about 3 |
| 8-bit fingerprints, `width = 8k`: without Optimizations I and II | 0.75 to 0.82 | 385 to 800 | hundreds |

With 32-bit fingerprints the optimizations never fire (fingerprint collisions are essentially
impossible), so the two variants are identical. With 8-bit fingerprints they are the difference
between a usable result and a useless one, which is why they are kept.

Throughput is about **0.3 million inserts per second** on CPython 3.13. The Rust-backed
[`heavykeeper`](https://pypi.org/project/heavykeeper/) package is roughly 20 times faster at
comparable accuracy (it takes `str` keys only and implements the basic algorithm without the
optimizations above). A compiled backend for this library is planned.

Reproduce, from a source checkout (`pip install -e . --group dev`):

```console
$ python -m benchmarks.accuracy --fingerprint-bits 8 32 --with-heavykeeper   # accuracy table
$ python -m benchmarks.throughput                                            # inserts per second
$ pytest -m slow                                                             # accuracy gates, ~15 s
$ pytest -m bench --benchmark-only                                           # microbenchmarks
```

## Other implementations

| | this library | [`heavykeeper`](https://pypi.org/project/heavykeeper/) | [Redis `TOPK`](https://redis.io/docs/stack/bloom/TopK_Commands) |
|---|---|---|---|
| Language | pure Python | Rust, via PyO3 | C module for a Redis server |
| Keys | any hashable key (default encoder: `None`, `bool`, `int`, `str`, `bytes`, tuples) | `str` only | strings |
| Paper's Optimization II | yes | no | no |
| Pickle / persistence | `pickle` | not exposed | server persistence |
| Needs | nothing, any platform, PyPy | CPython 3.11+ wheel or a Rust toolchain | a running Redis server |

## Limitations

- Results are approximate: only the tracked keys have meaningful counts (`estimate` of any other
  key is a lower bound that may be 0), and near-ties at the k-th place can go either way.
- Counts only grow: no deletion, no time decay, no merging of two sketches. To count over time
  windows, keep one `TopK` per window (see [docs/examples.md](docs/examples.md)).
- `add(key, count)` costs `O(count)`.
- Pure Python is slow compared with a compiled implementation (see Benchmarks).

## Development

```console
$ python -m venv .venv && . .venv/bin/activate
$ pip install -e . --group dev        # needs pip 25.1+
$ pytest                              # fast suite, including the examples and these docs
$ pytest -m slow                      # statistical accuracy gates
$ pytest -m bench --benchmark-only    # microbenchmarks
$ ruff check . && ruff format --check . && mypy
```

The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-10-13-heavykeeper-topk-design.md).

## Reference

Junzhi Gong, Tong Yang, Haowei Zhang, Hao Li, Steve Uhlig, Shigang Chen, Lorna Uden, Xiaoming Li.
*HeavyKeeper: An Accurate Algorithm for Finding Top-k Elephant Flows.* USENIX ATC 2018.
<https://www.usenix.org/conference/atc18/presentation/gong>

```bibtex
@inproceedings{gong2018heavykeeper,
  title     = {HeavyKeeper: An Accurate Algorithm for Finding Top-k Elephant Flows},
  author    = {Gong, Junzhi and Yang, Tong and Zhang, Haowei and Li, Hao and Uhlig, Steve
               and Chen, Shigang and Uden, Lorna and Li, Xiaoming},
  booktitle = {2018 USENIX Annual Technical Conference (USENIX ATC 18)},
  pages     = {909--921},
  year      = {2018},
  publisher = {USENIX Association}
}
```

## License

MIT
