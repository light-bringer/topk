# Documentation

Start with the [project README](../README.md) for what the library does, how to install and use it,
and how to choose its settings. The pages here go further:

- **[How HeavyKeeper works](algorithm.md)**: the algorithm explained step by step, with diagrams.
  Read this to understand why the library behaves the way it does.
- **[Examples](examples.md)**: four runnable scripts (counting words, finding the busiest clients in
  a log, counting your own key types, saving and resuming) and a few recipes, such as sizing the
  sketch and counting over time windows.
- **[Design notes](superpowers/specs/2026-10-13-heavykeeper-topk-design.md)**: how the library is
  built, the decisions behind it, and what is left to do. Written for people who want to change the
  code.
- **[Releasing](releasing.md)**: for maintainers, how a version gets onto PyPI, including the
  one-time setup that needs your own PyPI accounts.

Every `>>>` example in these pages is run by the test suite, and so are the example scripts, so the
code you read is code that works. (Blocks that show a program's output, marked `console`, are
samples and are not checked.)
