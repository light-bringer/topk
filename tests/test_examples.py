"""Run every example script, so none can rot."""

from collections import Counter

import pytest
from examples import checkpointing, custom_key_encoder, log_heavy_hitters, word_frequency


def test_word_frequency(capsys: pytest.CaptureFixture[str]) -> None:
    result = word_frequency.main()
    exact = Counter(word_frequency.words(word_frequency.TEXT))
    assert [word for word, _ in result[:3]] == ["the", "of", "was"]
    assert all(count == exact[word] for word, count in result)  # few keys: no collisions
    assert "the" in capsys.readouterr().out


def test_log_heavy_hitters(capsys: pytest.CaptureFixture[str]) -> None:
    precision = log_heavy_hitters.main()
    out = capsys.readouterr().out
    assert precision >= 0.9
    assert "precision" in out
    assert "10.0.0.0" in out  # the busiest client


def test_custom_key_encoder(capsys: pytest.CaptureFixture[str]) -> None:
    result = custom_key_encoder.main()
    assert "key_encoder" in capsys.readouterr().out  # the default encoder's complaint
    assert result[0] == (custom_key_encoder.Endpoint("GET", "/"), 40)
    assert [count for _, count in result] == [40, 25, 10]


def test_checkpointing(capsys: pytest.CaptureFixture[str]) -> None:
    result = checkpointing.main()
    assert len(result) == 5
    assert "identical to one uninterrupted run" in capsys.readouterr().out
