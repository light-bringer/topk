"""Fail unless a release tag matches the version in pyproject.toml.

Run by the release workflow before anything is built or published:

    python .github/scripts/check_release_tag.py v0.1.0

The tag must be "v" followed by exactly the version in pyproject.toml, so a tag cannot publish a
different version from the one in the source (or publish a ".dev0" snapshot by mistake).
"""

from __future__ import annotations

import sys
from pathlib import Path

import tomllib

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def check_tag(tag: str, version: str) -> str | None:
    """Return an error message if ``tag`` is not ``v`` + ``version``, otherwise None."""
    if not tag.startswith("v"):
        return f'release tags start with "v" (for example v{version}), got {tag!r}'
    if tag[1:] != version:
        return (
            f"tag {tag!r} does not match the version {version!r} in pyproject.toml. "
            "Set the version in pyproject.toml (and date the changelog entry) in a commit, "
            "then tag that commit."
        )
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_release_tag.py <tag>", file=sys.stderr)
        return 2
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    problem = check_tag(argv[0], version)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"ok: tag {argv[0]} matches version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
