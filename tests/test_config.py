import os
from pathlib import Path

import pytest

import publish

VALID_TOML = """\
[feed]
title = "AI Agent FM"
description = "Test feed"
author = "Test Author"
public_base_url = "https://pub-abc.r2.dev"

[r2]
bucket = "agent-fm"

[tts]
model = "gemini-3.1-flash-tts-preview"
host_voice = "Kore"

[tts.guest_voices]
engg = "Charon"
sales = "Puck"
product = "Fenrir"
"""


def write_toml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "agentfm.toml"
    p.write_text(text)
    return p


def test_load_config_parses_all_fields(tmp_path):
    cfg = publish.load_config(write_toml(tmp_path, VALID_TOML))
    assert cfg.feed_title == "AI Agent FM"
    assert cfg.public_base_url == "https://pub-abc.r2.dev"
    assert cfg.bucket == "agent-fm"
    assert cfg.tts_model == "gemini-3.1-flash-tts-preview"
    assert cfg.host_voice == "Kore"
    assert cfg.guest_voices == {"engg": "Charon", "sales": "Puck", "product": "Fenrir"}


def test_load_config_missing_key_names_it(tmp_path):
    broken = VALID_TOML.replace('host_voice = "Kore"\n', "")
    with pytest.raises(publish.ConfigError, match="tts.host_voice"):
        publish.load_config(write_toml(tmp_path, broken))


def test_load_config_missing_guest_lens_names_it(tmp_path):
    broken = VALID_TOML.replace('sales = "Puck"\n', "")
    with pytest.raises(publish.ConfigError, match="tts.guest_voices.sales"):
        publish.load_config(write_toml(tmp_path, broken))


def test_load_env_sets_missing_vars_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ALREADY_SET", "keep-me")
    monkeypatch.delenv("AGENTFM_NEW_VAR", raising=False)
    envfile = tmp_path / ".env"
    envfile.write_text("# comment\n\nALREADY_SET=clobbered\nAGENTFM_NEW_VAR=hello\n")
    publish.load_env(envfile)
    assert os.environ["ALREADY_SET"] == "keep-me"
    assert os.environ["AGENTFM_NEW_VAR"] == "hello"


def test_load_env_missing_file_is_noop(tmp_path):
    publish.load_env(tmp_path / "does-not-exist.env")  # must not raise
