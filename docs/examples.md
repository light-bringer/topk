# Examples

Four runnable scripts live in [`examples/`](../examples). Each uses only `topk` and the standard
library, and the test suite runs every one of them. The snippets on this page are doctests, so they
are checked too.

```console
$ python examples/word_frequency.py
$ python examples/log_heavy_hitters.py
$ python examples/custom_key_encoder.py
$ python examples/checkpointing.py
```

(From a source checkout, run `pip install -e .` first so that `import topk` works.)

## 1. Count words: `word_frequency.py`

The smallest useful program: build a `TopK`, feed it keys, read the result.

```pycon
>>> import re
>>> from topk import TopK
>>> text = "the cat saw the dog and the cat ran"
>>> top = TopK(3, seed=1)
>>> top.update(re.findall(r"[a-z']+", text))
>>> top.most_common(2)
[('the', 3), ('cat', 2)]
```

`update` takes any iterable of keys, or a mapping of `key -> count`. The script counts the opening
sentence of *A Tale of Two Cities*:

```console
 14  the
 12  of
 11  was
 10  it
  4  we
```

## 2. Find the busiest clients: `log_heavy_hitters.py`

A few clients make most of the requests while a long tail shows up once or twice, which is the
case HeavyKeeper is built for. The script simulates 200,000 requests from about 27,000 distinct
clients and keeps the ten busiest with a 24.6 KB sketch:

```pycon
>>> requests = ["10.0.0.1"] * 5 + ["10.0.0.2"] * 3 + ["10.0.0.3"]  # the script streams 200,000
>>> top: TopK[str] = TopK(10, seed=1)
>>> top.update(requests)  # any iterable of keys; the script passes an iterator
>>> for client, count in top.most_common():
...     print(client, count)
10.0.0.1 5
10.0.0.2 3
10.0.0.3 1
```

```console
200,000 requests from 27,087 distinct clients
sketch: width=1024 depth=2 (24,576 bytes)

client          reported   exact
10.0.0.0           26981   26981
10.0.0.1           12707   12707
10.0.0.2            8160    8160
...
precision: 100% of the true top 10 found
```

The exact column comes from a `Counter`, which the script builds only to check the answer. In
production that `Counter` is what you avoid: it grows with the number of distinct clients, the
sketch does not.

## 3. Count keys the default encoder does not know: `custom_key_encoder.py`

Keys are encoded to bytes before hashing (see the README for why). The default encoder handles
`None`, `bool`, `int`, `str`, `bytes` and tuples of these; anything else raises `TypeError`, naming
`key_encoder`:

```pycon
>>> from dataclasses import dataclass
>>> @dataclass(frozen=True)
... class Endpoint:
...     method: str
...     path: str
>>> TopK[Endpoint](3).add(Endpoint("GET", "/"))
Traceback (most recent call last):
    ...
TypeError: unsupported key type 'Endpoint': ... pass key_encoder= to count other (hashable) key types
```

Give `TopK` a function from a key to bytes that maps equal keys to equal bytes. The key must still
be hashable (a frozen dataclass is):

```pycon
>>> def encode_endpoint(e: Endpoint) -> bytes:
...     return f"{e.method} {e.path}".encode()
>>> top = TopK[Endpoint](2, key_encoder=encode_endpoint, seed=1)
>>> top.update([Endpoint("GET", "/")] * 5 + [Endpoint("POST", "/login")] * 2)
>>> top.most_common()
[(Endpoint(method='GET', path='/'), 5), (Endpoint(method='POST', path='/login'), 2)]
```

To pickle such a `TopK`, define the encoder at module level (as the script does) rather than as a
lambda or a function inside another function.

## 4. Checkpoint and resume: `checkpointing.py`

A pickled `TopK` keeps its sketch, its top-k set and its random-number state, so resuming gives
exactly what one uninterrupted run would:

```pycon
>>> import pickle
>>> events = list("abracadabra" * 3)
>>> straight = TopK(3, seed=1)
>>> straight.update(events)
>>> first = TopK(3, seed=1)
>>> first.update(events[:15])
>>> resumed = pickle.loads(pickle.dumps(first))  # write these bytes to disk in real use
>>> resumed.update(events[15:])
>>> resumed.most_common() == straight.most_common()
True
```

Pickles are guaranteed readable only by the same release of the library; state written by an
incompatible version raises `ValueError` rather than silently misbehaving.

## Recipes

### Sizing the sketch

`width` is the main accuracy knob. The default (`max(1024, 32 * k)`) found the true top 100 of a
500k-item stream with precision 1.000. To size it for your own data, run a sample through a few
widths and compare with an exact count, as in this snippet:

```pycon
>>> import itertools, random
>>> from collections import Counter
>>> def zipf_stream(n, distinct, skew, seed):
...     rng = random.Random(seed)
...     cumulative = list(itertools.accumulate(1 / (r + 1) ** skew for r in range(distinct)))
...     return rng.choices(range(distinct), cum_weights=cumulative, k=n)
>>> stream = zipf_stream(30_000, 20_000, 1.1, seed=2)
>>> truth = Counter(stream)
>>> k = 20
>>> kth_count = truth.most_common(k)[-1][1]
>>> def evaluate(width):
...     top = TopK(k, width=width, seed=1)
...     top.update(stream)
...     found = sum(truth[key] >= kth_count for key, _ in top.most_common())
...     worst_error = max(abs(c - truth[key]) / truth[key] for key, c in top.most_common())
...     return found, worst_error
>>> found, worst_error = evaluate(512)
>>> found == k and worst_error < 0.05
True
>>> tiny_found, tiny_error = evaluate(8)
>>> tiny_found < k and tiny_error > 0.5  # far too narrow: wrong keys and wrong counts
True
```

One sample run (yours differs slightly):

```console
width=   8  found 16/20  worst relative error 0.991
width=  32  found 18/20  worst relative error 0.973
width= 128  found 19/20  worst relative error 0.111
width= 512  found 20/20  worst relative error 0.011
```

Memory is `12 * depth * width` bytes plus about 84 bytes per tracked key, so widening is cheap:
`width=512` here is 12 KB.

### Counting over time windows

There is no sliding window and no merging of two sketches. To answer "top keys over the last few
windows", keep one `TopK` per window and add up their reported counts:

```pycon
>>> windows = ["abaca", "bbda", "bcbb"]
>>> trackers = []
>>> for events in windows:
...     tracker = TopK(2, seed=1)
...     tracker.update(events)
...     trackers.append(tracker)
>>> combined = Counter()
>>> for tracker in trackers:
...     combined.update(dict(tracker.most_common()))
>>> combined.most_common(2)
[('b', 6), ('a', 3)]
>>> Counter("".join(windows)).most_common(2)  # the exact answer, for comparison
[('b', 6), ('a', 4)]
```

A key that is not in a window's top k contributes nothing for that window, so the combined counts
are lower bounds (`a` is missing one occurrence here). Use a larger `k` per window than the number
of keys you finally want.

### Tracked keys versus estimates

`most_common()` and `key in top` describe the top k. `estimate(key)` asks the sketch, and a key
that is not in the top k can still show a small count while its buckets survive:

```pycon
>>> top = TopK(2, seed=1)
>>> top.update({"x": 50, "y": 30})
>>> top.add("z")
>>> "z" in top, top.estimate("z"), top.estimate("never-seen")
(False, 1, 0)
```

Treat `estimate` as meaningful for tracked keys only.

### Reproducible runs

By default every instance draws its own random seed, which makes it hard to craft keys that
collide on purpose. Pass `seed` for repeatable results; `top.seed` tells you the one in use:

```pycon
>>> a, b, c = TopK(3, seed=42), TopK(3, seed=42), TopK(3)
>>> for tracker in (a, b, c):
...     tracker.update("mississippi")
>>> a.most_common() == b.most_common(), a.seed, isinstance(c.seed, int)
(True, 42, True)
```
