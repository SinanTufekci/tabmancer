"""config.merge_json: a key saved beside the others, never over a file that could not be read."""

from __future__ import annotations

import pytest

from tabmancer import config


def test_a_key_joins_the_rest():
    assert config.merge_json("config.json", split="off")  # no file yet: made
    config.write_json("config.json", {"theme": "dusk", "split": "off"})
    assert config.merge_json("config.json", split="auto")
    assert config.read_json("config.json") == {"theme": "dusk", "split": "auto"}


@pytest.mark.parametrize("text", ['{"theme": "dusk", ', "[1, 2]"])
def test_a_file_that_cannot_be_read_is_left_alone(text):
    path = config.config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert not config.merge_json("config.json", split="off")
    assert path.read_text(encoding="utf-8") == text
