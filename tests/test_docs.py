"""Run the ``>>>`` examples in the documentation, so the docs cannot rot."""

import doctest
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_markdown_doctests(name: str) -> doctest.TestResults:
    """Run the ``>>>`` examples in the document's ```pycon fenced blocks, sharing one namespace.

    ``doctest.testfile`` would read a closing fence that directly follows an example's output as
    part of that output, so the fenced blocks are extracted first.
    """
    path = ROOT / name
    blocks = re.findall(r"```pycon\n(.*?)```", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    test = doctest.DocTestParser().get_doctest("\n".join(blocks), {}, name, str(path), 0)
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    runner.run(test)
    return runner.summarize()


@pytest.mark.parametrize("name", ["README.md", "docs/examples.md", "docs/algorithm.md"])
def test_documentation_examples_run(name: str) -> None:
    results = run_markdown_doctests(name)
    assert results.attempted > 0, f"{name} has no pycon examples"
    assert results.failed == 0


@pytest.mark.parametrize("name", ["README.md", "docs/examples.md", "docs/algorithm.md"])
def test_documentation_has_no_code_block_that_is_never_run(name: str) -> None:
    # A plain ```python fence is displayed but never executed, so it can drift from the library.
    # Write runnable examples as ```pycon (run by the test above); use ```console for shell
    # sessions and ```bibtex for citations.
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "```python" not in text


@pytest.mark.parametrize(
    "name", ["README.md", "docs/examples.md", "examples/checkpointing.py", "src/topk/_topk.py"]
)
def test_pickle_documentation_warns_about_untrusted_data(name: str) -> None:
    # Unpickling can run arbitrary code before TopK's version check; saved state must be trusted.
    text = (ROOT / name).read_text(encoding="utf-8").lower()
    assert "unpickle" in text
    assert "trust" in text


def mermaid_problems(block: str) -> list[str]:
    """Cheap structural checks on a Mermaid block (rendering itself needs a browser)."""
    problems = []
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    first = next((line for line in lines if not line.startswith("%%")), "")  # skip init directives
    if not first.startswith(("flowchart", "graph", "sequenceDiagram", "xychart-beta")):
        problems.append(f"unknown diagram type {first!r}")
    if block.count('"') % 2:
        problems.append("unbalanced double quotes")
    unquoted = re.sub(r'"[^"]*"', '""', block)  # labels may hold brackets, e.g. [0,1)
    for opening, closing in ("[]", "{}", "()"):
        if unquoted.count(opening) != unquoted.count(closing):
            problems.append(f"unbalanced {opening}{closing}")
    return problems


def test_mermaid_guard_flags_broken_diagrams() -> None:
    assert mermaid_problems('flowchart TD\n  A["ok"] --> B{"fine"}\n') == []
    assert mermaid_problems('flowchart TD\n  A["a [0,1) interval"] --> B\n') == []
    assert mermaid_problems('%%{init: {"a": 1}}%%\nxychart-beta\n  line [1, 2]\n') == []
    assert mermaid_problems("pie title nope\n") == ["unknown diagram type 'pie title nope'"]
    assert "unbalanced double quotes" in mermaid_problems('flowchart TD\n  A["oops --> B\n')
    assert "unbalanced []" in mermaid_problems("flowchart TD\n  A[oops --> B\n")
    assert "unbalanced {}" in mermaid_problems("flowchart TD\n  A{oops --> B\n")


@pytest.mark.parametrize(
    ("name", "at_least"),
    [
        ("README.md", 1),
        ("docs/algorithm.md", 9),
        ("docs/superpowers/specs/2026-10-13-heavykeeper-topk-design.md", 1),
    ],
)
def test_mermaid_diagrams_are_well_formed(name: str, at_least: int) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    blocks = re.findall(r"```mermaid\n(.*?)```", text, flags=re.DOTALL)
    assert len(blocks) >= at_least, f"{name} should draw at least {at_least} diagram(s)"
    for block in blocks:
        assert mermaid_problems(block) == [], block


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "docs/README.md",
        "docs/algorithm.md",
        "docs/examples.md",
        "docs/superpowers/specs/2026-10-13-heavykeeper-topk-design.md",
    ],
)
def test_diagrams_are_mermaid_not_text(name: str) -> None:
    # Diagrams are written in Mermaid, which GitHub renders; box-drawing art in a plain code block
    # is hard to keep aligned and to edit.
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "```text" not in text
    drawing = [c for c in text if c in "┌┐└┘├┤┬┴┼│─╭╮╰╯▼▲►◄"]
    assert not drawing, f"{name} contains box-drawing characters: {''.join(sorted(set(drawing)))}"
