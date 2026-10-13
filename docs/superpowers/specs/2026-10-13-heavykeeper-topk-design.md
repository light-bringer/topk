# Plan: pure-Python HeavyKeeper top-k library for PyPI

## Context

The goal is a Python library implementing HeavyKeeper (Gong et al., USENIX ATC '18) for finding the top-k most frequent items in a stream, published on PyPI. The chosen strategy is **"pure-Python now, fast later"**: ship a zero-dependency reference implementation first, with an internal seam so a compiled backend can be added later without breaking users.

Research on existing implementations found:
- The only Python package is `heavykeeper` on PyPI, a PyO3 wrapper over `heavykeeper-rs`. It accepts string keys only, has no pickling, has low adoption, and implements the Redis-style *basic* algorithm. Neither it nor RedisBloom implements the paper's Opt I (fingerprint-collision admission check) or Opt II (selective increment).
- There is no pure-Python implementation.
- The opening for this library: pure Python, any hashable key (`str`, `bytes`, `int` and tuples of these out of the box, anything else through `key_encoder`), faithful to Algorithm 1 with Opt I and II, picklable, typed.

Facts from the paper and the authors' code that the design depends on:
1. **Typo in the paper's Algorithm 1, line 8.** It prints `C < n_min`. The authors' code (`papergitkeeper/heavy-keeper-project/heavykeeper.h`) uses `c <= getmin()`. With `<`, a new flow can never reach `n_min + 1`, so it can never enter a full heap. We use `<=`.
2. **`n_min` is 0 while the heap is not full.** This matches `ssummary::getmin()` in the reference code.
3. **Hashing must be stable across processes.** Python's built-in `hash()` is randomized per process for `str` and `bytes`, which would corrupt pickled state. We use keyed BLAKE2b from the standard library instead.
4. **The PyPI name `topk` cannot be registered.** PyPI normalizes names by removing `.`, `_` and `-` and mapping `l`/`i`→`1` and `o`→`0`. That turns `topk` into `t0pk`, the same as the existing project `top-k`. **Recommended distribution name: `heavykeeper-topk`** (currently unregistered). The **import name `topk` is free**: `top-k` installs `top_k` and `topk-sdk` installs `topk_sdk`. The distribution name only appears in `pyproject.toml` and the README, so it is cheap to change.

## Design

### Package layout (src layout, in this repo)
```
pyproject.toml            # PEP 621 metadata, hatchling backend, requires-python >=3.10, MIT
README.md  LICENSE  CHANGELOG.md
src/topk/
  __init__.py             # exports TopK; __version__ via importlib.metadata
  _encoding.py            # encode_key(key) -> bytes: stable and consistent with ==
  _heap.py                # IndexedMinHeap: key -> slot dict + parallel lists
  _sketch.py              # PySketch kernel: hashing, d*w buckets, decay table, insert and estimate
  _topk.py                # TopK public class (Algorithm 1 orchestration)
  py.typed
tests/  benchmarks/  .github/workflows/{ci.yml,release.yml}
```

### Public API (v0.1, the only contract; everything prefixed `_` is private)
```python
TopK(k, *, width=None, depth=2, decay_base=1.08, fingerprint_bits=32,
     seed=None, key_encoder=None)
  .add(key, count=1)            # count>1 = repeated unit inserts (hash once); O(count*d)
  .update(iterable_or_mapping)  # like Counter.update
  .estimate(key) -> int         # sketch query: max counter among buckets whose fingerprint matches
  key in tk                     # is the key currently in the top-k?
  .most_common(n=None) -> list[tuple[K, int]]   # descending by count
  len(tk), iter(tk), repr(tk); read-only properties k, width, depth, decay_base, total
  pickle support via __getstate__/__setstate__ (seed and RNG state included)
```
- `decay_base` is the paper's `b` (must be > 1). This deliberately differs from the `decay = 1/b` used by Redis and the `heavykeeper` package, and the docs say so.
- `width=None` defaults to `max(1024, 32*k)`. Phase 4 benchmarks will check this value.
- `seed=None` draws a random seed (makes hash collisions hard to force on purpose). The seed is stored in the object's state; passing an integer makes runs reproducible.
- Keys must be hashable: the top-k heap looks them up in a dict (`key in heap`). The default `encode_key` handles `str`, `bytes`, `int` (with `bool` encoded as `int`, because `True == 1`), `None`, and tuples of these (recursive, with type tags and length prefixes; the wire format is documented in `_encoding.py` and pinned by golden-vector tests). `bytearray` is not supported because it is unhashable. Any other type raises `TypeError` that names `key_encoder=`; a custom `key_encoder` extends which *hashable* types can be counted, it cannot make an unhashable key trackable. A custom encoder must be picklable for pickling to work.
- Not thread-safe. The docs will say so.

### Internals
**Hashing (in `_sketch.py`):**
- `x = int.from_bytes(blake2b(data, digest_size=16, key=seed_bytes).digest(), "little")`
- `h1 = x & (2**64-1)`, `h2 = ((x >> 64) & (2**32-1)) | 1`, `fp = (x >> 96) & fp_mask`
- Row index: `idx_j = (h1 + j*h2) % width`

**Buckets:** two flat Python lists, `fps` and `counts`, each of length `depth*width`; row `j` starts at offset `j*width`.

**Decay table:** `table[c] = b**-c`, computed until it drops below 2⁻⁵³ (about c ≈ 477 for b = 1.08). Beyond that the decay probability is treated as 0. Randomness comes from `random.Random(seed)`.

**Kernel insert:** `PySketch.insert(data, monitored, n_min) -> maxv`. For each row:
```
c = counts[i]
if c == 0:            fps[i] = fp; counts[i] = 1; maxv = max(maxv, 1)                 # Case 1
elif fps[i] == fp:    if monitored or c <= n_min: c += 1; counts[i] = c                # Case 2 + Opt II
                      maxv = max(maxv, c)
elif c < len(table) and rand() < table[c]:                                              # Case 3
                      c -= 1
                      if c == 0: fps[i] = fp; c = 1; maxv = max(maxv, 1)
                      counts[i] = c
```

**Top-k step:** `TopK._add_one(key, data)` implements Algorithm 1 with Opt I:
```
monitored = key in heap
n_min = heap.min_count() if len(heap) == k else 0
maxv = kernel.insert(data, monitored, n_min)
if monitored:            heap.increase(key, maxv)            # no-op unless maxv > current
elif maxv > 0:
    if len(heap) < k:    heap.push(key, maxv)
    elif maxv == n_min + 1: heap.replace_min(key, maxv)      # Opt I (Theorem 1)
```
Admission goes through a small overridable `_admit` method. The benchmarks subclass it to build a "basic" variant (no Opt I/II) for comparison.

**IndexedMinHeap:** `push`, `min_count` (O(1)), `increase` (sift down, since counts only grow), `replace_min` (returns the evicted entry), `get`, `__contains__`, `items_desc()`.

**Seam for a later compiled backend:** a future native kernel exposes the same private `insert(data, monitored, n_min)` / `estimate(data)` interface and is chosen at import time, falling back to the pure-Python kernel. Pickled state records a kernel/hash-scheme identifier, so mixing backends fails loudly. The plan is to ship the native code as a separate distribution behind an extra (`heavykeeper-topk[fast]`), so the main wheel stays `py3-none-any`.

### Deliberate differences from the paper (all listed in the README)
1. Opt II uses `<=` (fact 1 above).
2. `n_min = 0` until the heap is full (fact 2 above).
3. Empty buckets are handled explicitly as Case 1. Taken literally, the paper's pseudocode would decrement an empty bucket to −1.
4. Items whose estimate is 0 are never admitted to the heap.
5. The decay probability is exactly `b^-C`. The reference code uses `1/floor(b^C)`.
6. Fingerprints default to 32 bits (the paper used 16), configurable from 1 to 32.
7. Hashing is keyed BLAKE2b with double hashing. The reference code uses BOBHash with a different modulus per row.

## Build phases (test-driven: failing test first in every phase)

**Phase 0: Scaffolding**
- Use a stdlib venv with pip 25.1+ (`python -m venv .venv && pip install -e . --group dev`). `uv` is not required.
- Write `pyproject.toml` (hatchling, MIT, Python 3.10+, classifiers, dev group: pytest, hypothesis, pytest-cov, ruff, mypy).
- Add `src/topk/__init__.py` (stub), `py.typed`, `.gitignore`, LICENSE, and a minimal README.
- Add `ci.yml`: ruff, `mypy --strict`, and pytest across Python 3.10–3.14 and PyPy 3.11 on ubuntu, macOS and Windows.
- Save this plan into the repo as `docs/superpowers/specs/2026-10-13-heavykeeper-topk-design.md` and commit it.

**Phase 1: Primitives**
- `_encoding.py`:
  - Encoding matches `==`: `1`, `True` and `1` inside a tuple encode alike where Python treats them as equal.
  - Unequal keys give unequal bytes, tested with Hypothesis.
  - Unsupported types raise `TypeError`.
- `_heap.py`: property test against a sorted reference implementation; check the invariant after every operation.

**Phase 2: Sketch kernel (`_sketch.py`)**
- Unit tests for Cases 1–3 using an injected deterministic RNG, mirroring the paper's Figure 1 example.
- Decay table values and cutoff.
- Opt II gating (`monitored` / `c <= n_min`).
- **Theorem 2 property test** (Hypothesis, 32-bit fingerprints, small streams): `estimate(key) <= true_count(key)` for every key, always.

**Phase 3: `TopK` public class (`_topk.py`)**
- Algorithm 1 and Opt I through `_admit`.
- `add` with a count, `update`, `estimate`, `__contains__`, `most_common`, properties, `repr`.
- Argument validation: `k >= 1`, `width >= 1`, `depth >= 1`, `decay_base > 1`, `1 <= fingerprint_bits <= 32`.
- Pickling: a round trip in the middle of a stream must give identical results to an uninterrupted run with the same seed.
- **Regression test: a new heavy key must be able to enter a full heap.** This catches the `<` vs `<=` typo.

**Phase 4: Accuracy and throughput harness (`benchmarks/`)**
- `zipf.py`: seeded Zipf stream generator.
- `accuracy.py`: reports precision, ARE and AAE (paper §5.2) against an exact `Counter`. Compares this library, the "basic" subclass, and the `heavykeeper` package if it is installed.
- `throughput.py`: inserts per second.
- `tests/test_accuracy.py` (marked `slow`): Zipf with s = 1.1, N = 500k, k = 100, default width. Must reach precision ≥ 0.95 and beat the basic variant's ARE. If it misses, tune the default width here.
- Record measured throughput in the README. There is no hard speed requirement for pure Python.

**Phase 5: Docs and release**
- README: install, quickstart, how to pick parameters (memory vs. precision, using the Theorem 3 bound), the list of differences from the paper, a BibTeX citation of the paper, and a comparison with the `heavykeeper` and Redis implementations.
- Docstrings on every public method; CHANGELOG.
- `release.yml`: on a `v*` tag, run `python -m build`, `twine check`, then publish with `pypa/gh-action-pypi-publish` via PyPI Trusted Publishing.
- **Steps only you can do:** create the PyPI and TestPyPI accounts, and add a "pending trusted publisher" for `heavykeeper-topk` → `light-bringer/topk` / `release.yml`.
- Publish 0.1.0 to TestPyPI first, check `pip install` in a clean virtual environment, then publish to PyPI.

**Later (each with its own spec, not part of v0.1):**
- The native kernel (`[fast]` extra, probably Rust/PyO3 with maturin and cibuildwheel).
- The paper's other uses (§3.6): a heavy-hitter query (`estimate > θN`) and heavy-change detection.
- Opt III's threshold-array mode, a sliding-window variant, and merging sketches.

## Verification
- `pytest` passes (unit and property tests); `pytest -m slow` passes the accuracy gates.
- `ruff check . && ruff format --check . && mypy` are clean (mypy runs in strict mode via `pyproject.toml`).
- `python benchmarks/accuracy.py` prints a precision/ARE/AAE table in which the full algorithm beats the basic variant.
- `python -m build` produces a `py3-none-any` wheel and an sdist; `twine check dist/*` passes.
- Smoke test: install the wheel into a fresh venv on CPython and PyPy, then `python -c "from topk import TopK; t=TopK(3, seed=1); t.update('aaabbc'); print(t.most_common())"`.
- After the TestPyPI upload, `pip install -i https://test.pypi.org/simple/ heavykeeper-topk` works in a clean venv.
