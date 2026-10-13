"""The release workflow and the helper that guards it."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+; these tests are skipped on 3.10

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "check_release_tag.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_tag", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v0.1.0", "0.1.0"),
        ("v1.2.3", "1.2.3"),
        ("v0.2.0rc1", "0.2.0rc1"),
        ("v1.0.0.post1", "1.0.0.post1"),
    ],
)
def test_a_matching_tag_is_accepted(tag: str, version: str) -> None:
    assert load_script().check_tag(tag, version) is None


def test_a_tag_that_differs_from_the_version_is_rejected_with_advice() -> None:
    message = load_script().check_tag("v0.1.0", "0.1.0.dev0")
    assert message
    assert "0.1.0.dev0" in message
    assert "pyproject.toml" in message


def test_a_tag_without_the_v_prefix_is_rejected() -> None:
    message = load_script().check_tag("0.1.0", "0.1.0")
    assert message
    assert '"v"' in message


def test_main_compares_the_tag_with_the_real_pyproject_version() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = config["project"]["version"]
    module = load_script()
    assert module.main([f"v{version}"]) == 0
    assert module.main(["v999.0.0"]) == 1
    assert module.main([]) == 2


# --- the workflow ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def job(workflow: str, name: str) -> str:
    """The text of one top-level job."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", workflow, re.M | re.S)
    assert match, f"job {name!r} not found"
    return match.group(1)


def test_it_runs_on_version_tags_and_by_hand(workflow: str) -> None:
    assert re.search(r'push:\n\s+tags: \["v\*"\]', workflow)
    assert "workflow_dispatch:" in workflow


def test_it_publishes_without_stored_secrets(workflow: str) -> None:
    # Trusted Publishing: PyPI trusts this workflow's identity, so there is no token to leak.
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "secrets." not in workflow
    assert "password:" not in workflow


def test_only_the_publish_jobs_may_mint_an_identity_token(workflow: str) -> None:
    assert "permissions:\n  contents: read\n" in workflow  # workflow-wide default
    assert "id-token: write" not in job(workflow, "build")
    assert "id-token: write" not in job(workflow, "smoke-test")
    for name in ("publish-testpypi", "publish-pypi"):
        assert "id-token: write" in job(workflow, name)


def test_publishing_waits_for_the_build_and_the_smoke_test(workflow: str) -> None:
    for name in ("publish-testpypi", "publish-pypi"):
        assert "needs: [build, smoke-test]" in job(workflow, name)


def test_real_pypi_is_only_reachable_from_a_version_tag(workflow: str) -> None:
    assert "startsWith(github.ref, 'refs/tags/v')" in job(workflow, "publish-pypi")
    assert "environment:\n      name: pypi" in job(workflow, "publish-pypi")
    assert "test.pypi.org" in job(workflow, "publish-testpypi")
    assert "test.pypi.org" not in job(workflow, "publish-pypi")


def test_the_build_checks_the_tag_against_the_version(workflow: str) -> None:
    build = job(workflow, "build")
    assert "check_release_tag.py" in build
    assert "startsWith(github.ref, 'refs/tags/')" in build
    assert "twine check" in build


def test_pull_requests_that_touch_the_release_build_and_test_it_but_never_publish(
    workflow: str,
) -> None:
    # So a change to the release machinery is exercised before it can run for real.
    assert re.search(r"pull_request:\n\s+paths:", workflow)
    assert '".github/workflows/release.yml"' in workflow
    assert '".github/scripts/**"' in workflow
    for name in ("publish-testpypi", "publish-pypi"):
        condition = re.search(r"^    if: (.*)$", job(workflow, name), re.M)
        assert condition, f"{name} must be conditional"
        assert "pull_request" not in condition.group(1)
    # Each publish condition depends on a tag or a manual run, neither of which a PR can be.
    assert "refs/tags/v" in job(workflow, "publish-pypi")
    assert "workflow_dispatch" in job(workflow, "publish-testpypi")
