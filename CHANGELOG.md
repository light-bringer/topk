# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `TopK`: top-k frequent items of a stream in bounded memory, using HeavyKeeper (Gong et al.,
  USENIX ATC '18) with the paper's Optimizations I and II. `add` (with a count), `update`,
  `estimate`, `most_common`, membership, `len`, iteration, read-only settings and `pickle` support.
- Any hashable key: a stable, injective byte encoding of `None`, `bool`, `int`, `str`, `bytes` and
  tuples (and subclasses that keep their base type's equality), and a `key_encoder` hook for other
  hashable keys.
- Compact bucket storage (`array`): 12 bytes per bucket.
- Documentation: a README written for readers new to the algorithm, a guide to how HeavyKeeper works
  with Mermaid diagrams (`docs/algorithm.md`), an examples guide (`docs/examples.md`), four runnable
  scripts in `examples/`, and design notes. Every `>>>` example is run by the test suite.
- Benchmarks: `python -m benchmarks.accuracy` and `python -m benchmarks.throughput`, slow accuracy
  gates (`pytest -m slow`) and `pytest-benchmark` microbenchmarks (`pytest -m bench`).
- CI on CPython 3.10 to 3.14 (Linux, macOS, Windows) and PyPy 3.11, with lint, strict mypy, the
  slow gates, the microbenchmarks and a build check.

### Notes
- Deliberate differences from the paper are listed in the README. In particular Optimization II
  compares `c <= n_min` as the authors' code does; the paper prints `C < n_min`, which would stop a
  newcomer from ever entering a full top-k set.
