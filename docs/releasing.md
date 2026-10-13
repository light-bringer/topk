# Releasing

This page is for maintainers. It explains how a new version of `heavykeeper-topk` gets onto PyPI.

Publishing uses **PyPI Trusted Publishing**: PyPI is told to trust one specific GitHub Actions
workflow in this repository, so no API token is stored anywhere and there is nothing to leak. The
workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## One-time setup

These steps need your own PyPI accounts, so they cannot be done from the repository.

1. Create accounts on [pypi.org](https://pypi.org) and [test.pypi.org](https://test.pypi.org), and
   turn on two-factor authentication for both.
2. On **each** site, register a *pending trusted publisher* (in your account, under Publishing).
   Use these values. They must match exactly:

   | Field | Value |
   |---|---|
   | PyPI project name | `heavykeeper-topk` |
   | Owner | `light-bringer` |
   | Repository name | `topk` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` on pypi.org, `testpypi` on test.pypi.org |

   A *pending* publisher lets the first upload create the project, so the name is claimed by the
   first release. PyPI's own documentation on Trusted Publishers has the current form.
3. In the GitHub repository, under *Settings, Environments*, create two environments named `pypi`
   and `testpypi`. For `pypi` it is worth adding yourself as a *required reviewer*, so that a
   release waits for a human click before it publishes.

## Try it first: a dry run on TestPyPI

1. On GitHub, open *Actions, Release, Run workflow*, leave the choice on `testpypi` and run it.
2. The workflow builds the package, checks it, installs the built wheel on Linux (CPython 3.10 and
   3.13, PyPy), macOS and Windows and runs a small program with it, and then uploads it to TestPyPI.
3. Install it from TestPyPI in a clean environment and try it:

   ```console
   $ python -m venv /tmp/try && /tmp/try/bin/pip install -i https://test.pypi.org/simple/ heavykeeper-topk
   $ /tmp/try/bin/python -c "from topk import TopK; t = TopK(2, seed=1); t.update('abracadabra'); print(t.most_common())"
   ```

Pull requests that change the release files (the workflow, its helper script or `pyproject.toml`)
run the same build and smoke tests automatically, so a broken release workflow shows up in review.
The publish jobs cannot run from a pull request.

A version can be uploaded only once, even to TestPyPI. For another dry run, change the version
first (for example from `0.1.0.dev0` to `0.1.0.dev1`).

## Releasing a version

1. **Set the version.** In `pyproject.toml`, change `version` to the release (for example `0.1.0`).
   In `CHANGELOG.md`, rename the *Unreleased* heading to the version and the date, and add a fresh
   *Unreleased* section above it.
2. **Check locally:** `pytest`, `pytest -m slow`, `ruff check .`, `ruff format --check .`, `mypy`,
   and `python -m build && twine check dist/*`.
3. **Merge** the change through a pull request as usual.
4. **Tag the merge commit and push the tag:**

   ```console
   $ git switch main && git pull
   $ git tag v0.1.0
   $ git push origin v0.1.0
   ```

5. **Watch the Release workflow.** It first checks that the tag is `v` plus exactly the version in
   `pyproject.toml`, and that the version is not a development version such as `0.1.0.dev0` (so a
   tag cannot publish a different version, or a snapshot, by mistake). Then it builds the package,
   installs the built wheel on the oldest and newest supported Python to check that it works, and
   publishes to PyPI.
6. **Verify:** in a clean environment run `pip install heavykeeper-topk` and try it. Create a GitHub
   release from the tag, using the changelog text.
7. **Start the next version:** set `version` to the next development version (for example
   `0.2.0.dev0`) in a commit.

The README says "not on PyPI yet" until the first release. Update that note when 0.1.0 is out.

## If something goes wrong

- **The tag check fails.** Either the tag and `pyproject.toml` disagree, or the version in
  `pyproject.toml` is a development version such as `0.1.0.dev0`, which is never released. Fix
  `pyproject.toml` in a commit, then delete the tag (`git push --delete origin v0.1.0`, then
  `git tag -d v0.1.0`) and tag the fixed commit.
- **A smoke test fails.** Nothing was published. Fix the problem and merge it. The tag still points
  at the commit that failed, so move it: delete it (`git push --delete origin v0.1.0`, then
  `git tag -d v0.1.0`) and tag the fixed commit with the same version. Or bump the version and use a
  new tag. Either is safe, because nothing reached PyPI.
- **PyPI rejects the upload with a trusted-publisher error.** One of the values in the table above
  does not match the workflow file name, the repository, or the environment. Compare them.
- **A bad version was published.** PyPI never lets you replace a version. *Yank* it from the project
  page on PyPI (it stays installable only if someone pins it exactly) and release a new patch
  version.
