"""Guards on what the source distribution ships."""

from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+; this guard is skipped on 3.10

ROOT = Path(__file__).resolve().parent.parent


def test_sdist_ships_everything_the_tests_read() -> None:
    # An extracted sdist must be able to run its own test suite: tests import examples and
    # benchmarks, and read the README and the docs.
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = set(config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])
    assert {"src/topk", "tests", "benchmarks", "examples", "docs", "README.md"} <= included
