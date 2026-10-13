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


@pytest.mark.parametrize("name", ["README.md", "docs/examples.md"])
def test_documentation_examples_run(name: str) -> None:
    results = run_markdown_doctests(name)
    assert results.attempted > 0, f"{name} has no pycon examples"
    assert results.failed == 0


@pytest.mark.parametrize("name", ["README.md", "docs/examples.md"])
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
