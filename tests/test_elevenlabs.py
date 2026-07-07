"""Tests for the ElevenLabs dialogue TTS fallback provider."""

import io
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import publish

BASE_TOML = """
[feed]
title = "T"
description = "D"
author = "A"
public_base_url = ""

[r2]
bucket = "b"

[tts]
model = "gemini-3.1-flash-tts-preview"
host_voice = "Kore"

[tts.guest_voices]
engg = "Charon"
sales = "Puck"
product = "Fenrir"
"""

EL_BLOCK = """
[tts.elevenlabs]
host_voice_id = "HOSTID"

[tts.elevenlabs.guest_voice_ids]
engg = "ENGGID"
sales = "SALESID"
product = "PRODID"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "agentfm.toml"
    path.write_text(text)
    return path


def _cfg_elevenlabs(tmp_path: Path) -> publish.Config:
    toml = BASE_TOML.replace('[tts]\n', '[tts]\nprovider = "elevenlabs"\n') + EL_BLOCK
    return publish.load_config(_write(tmp_path, toml))


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_provider_defaults_to_gemini(tmp_path):
    cfg = publish.load_config(_write(tmp_path, BASE_TOML))
    assert cfg.tts_provider == "gemini"
    assert cfg.el_host_voice_id == ""
    assert cfg.el_guest_voice_ids == {}


def test_unknown_provider_rejected(tmp_path):
    toml = BASE_TOML.replace('[tts]\n', '[tts]\nprovider = "espeak"\n')
    with pytest.raises(publish.ConfigError, match="espeak"):
        publish.load_config(_write(tmp_path, toml))


def test_elevenlabs_provider_loads_voice_ids(tmp_path):
    cfg = _cfg_elevenlabs(tmp_path)
    assert cfg.tts_provider == "elevenlabs"
    assert cfg.el_host_voice_id == "HOSTID"
    assert cfg.el_guest_voice_ids == {
        "engg": "ENGGID",
        "sales": "SALESID",
        "product": "PRODID",
    }


def test_elevenlabs_provider_requires_voice_table(tmp_path):
    toml = BASE_TOML.replace('[tts]\n', '[tts]\nprovider = "elevenlabs"\n')
    with pytest.raises(publish.ConfigError, match="tts.elevenlabs"):
        publish.load_config(_write(tmp_path, toml))


def test_elevenlabs_tts_posts_dialogue(monkeypatch, tmp_path):
    cfg = _cfg_elevenlabs(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["key"] = req.get_header("Xi-api-key")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["ctx_strict"] = bool(context.verify_flags & ssl.VERIFY_X509_STRICT)
        return _FakeResponse(b"\x01\x02pcm")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    turns = [
        {"speaker": "HOST", "text": "Hi"},
        {"speaker": "GUEST", "text": "Hello"},
    ]
    pcm = publish.elevenlabs_tts(turns, cfg, "engg")
    assert pcm == b"\x01\x02pcm"
    assert captured["url"] == (
        "https://api.elevenlabs.io/v1/text-to-dialogue?output_format=pcm_24000"
    )
    assert captured["key"] == "k-test"
    assert captured["ctx_strict"] is False
    assert captured["body"]["model_id"] == "eleven_v3"
    assert captured["body"]["inputs"] == [
        {"text": "Hi", "voice_id": "HOSTID"},
        {"text": "Hello", "voice_id": "ENGGID"},
    ]


def test_elevenlabs_tts_requires_api_key(monkeypatch, tmp_path):
    cfg = _cfg_elevenlabs(tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(publish.TTSError, match="ELEVENLABS_API_KEY"):
        publish.elevenlabs_tts([{"speaker": "HOST", "text": "x"}], cfg, "engg")


def test_elevenlabs_tts_wraps_http_error(monkeypatch, tmp_path):
    cfg = _cfg_elevenlabs(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-secret")

    def fake_urlopen(req, timeout=None, context=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", None, io.BytesIO(b'{"detail":"bad key"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(publish.TTSError) as excinfo:
        publish.elevenlabs_tts([{"speaker": "HOST", "text": "x"}], cfg, "engg")
    assert "401" in str(excinfo.value)
    assert "k-secret" not in str(excinfo.value)


def _make_episode(tmp_path: Path) -> Path:
    ep = tmp_path / "episodes" / "proj-2026-07-07-engg"
    ep.mkdir(parents=True)
    (ep / "episode.json").write_text(
        json.dumps(
            {
                "id": "proj-2026-07-07-engg",
                "title": "T",
                "description": "D",
                "project": "proj",
                "lens": "engg",
                "date": "2026-07-07",
            }
        )
    )
    (ep / "script.json").write_text(
        json.dumps(
            {
                "title": "T",
                "lens": "engg",
                "turns": [{"speaker": "HOST", "text": "Hello world"}],
            }
        )
    )
    return ep


def test_publish_dispatches_to_elevenlabs(monkeypatch, tmp_path):
    toml = BASE_TOML.replace('[tts]\n', '[tts]\nprovider = "elevenlabs"\n') + EL_BLOCK
    _write(tmp_path, toml)
    ep_dir = _make_episode(tmp_path)
    calls = []

    def fake_el_tts(turns, cfg, lens):
        calls.append((tuple((t["speaker"], t["text"]) for t in turns), lens))
        return b"\x00\x00" * publish.SAMPLE_RATE

    monkeypatch.setattr(publish, "elevenlabs_tts", fake_el_tts)
    rc = publish.main(["publish", str(ep_dir), "--no-upload", "--root", str(tmp_path)])
    assert rc == 0
    assert (ep_dir / "episode.mp3").exists()
    assert calls == [((("HOST", "Hello world"),), "engg")]
