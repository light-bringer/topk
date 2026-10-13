import topk


def test_package_imports_and_has_version() -> None:
    assert isinstance(topk.__version__, str)
    assert topk.__version__
