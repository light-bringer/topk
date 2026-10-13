import pytest
from benchmarks import accuracy, throughput


def test_accuracy_script_prints_a_row_per_variant_and_setting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = accuracy.main(
        ["--n", "3000", "--distinct", "300", "--k", "5", "--seeds", "1",
         "--width-factors", "8", "--fingerprint-bits", "8", "32"]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "precision" in out
    assert out.count("full") == 2
    assert out.count("basic") == 2


def test_throughput_script_reports_each_variant(capsys: pytest.CaptureFixture[str]) -> None:
    code = throughput.main(["--n", "2000", "--distinct", "200", "--k", "5", "--repeats", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("full") == 3
    assert out.count("basic") == 3
