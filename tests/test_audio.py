import wave
from pathlib import Path

import pytest

import publish


def test_fake_tts_length_and_determinism():
    prompt = "x" * 250  # 250 // 100 = 2 seconds
    pcm = publish.fake_tts(prompt)
    assert len(pcm) == publish.SAMPLE_RATE * 2 * 2  # rate * 2 bytes * 2 sec
    assert pcm == publish.fake_tts(prompt)


def test_fake_tts_minimum_one_second():
    assert len(publish.fake_tts("hi")) == publish.SAMPLE_RATE * 2 * 1


def test_write_wav_and_duration(tmp_path):
    pcm = publish.fake_tts("x" * 300)  # 3 seconds
    wav = tmp_path / "out.wav"
    publish.write_wav(pcm, wav)
    with wave.open(str(wav), "rb") as w:
        assert w.getframerate() == publish.SAMPLE_RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
    assert publish.wav_duration_secs(wav) == 3


def test_encode_mp3_produces_nonempty_file(tmp_path):
    wav = tmp_path / "in.wav"
    publish.write_wav(publish.fake_tts("x" * 100), wav)
    mp3 = tmp_path / "out.mp3"
    publish.encode_mp3(wav, mp3)
    assert mp3.exists() and mp3.stat().st_size > 0


def test_encode_mp3_bad_input_raises(tmp_path):
    bad = tmp_path / "not-audio.wav"
    bad.write_text("garbage")
    with pytest.raises(publish.AudioError):
        publish.encode_mp3(bad, tmp_path / "out.mp3")


def test_synthesize_all_concatenates():
    out = publish.synthesize_all(["a" * 100, "b" * 100], synth=publish.fake_tts)
    assert out == publish.fake_tts("a" * 100) + publish.fake_tts("b" * 100)


def test_synthesize_all_retries_then_succeeds():
    calls = {"n": 0}

    def flaky(prompt: str) -> bytes:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return b"\x00\x00"

    out = publish.synthesize_all(["p"], synth=flaky, attempts=3, sleep=lambda s: None)
    assert out == b"\x00\x00"
    assert calls["n"] == 3


def test_synthesize_all_raises_tts_error_after_exhaustion():
    def broken(prompt: str) -> bytes:
        raise RuntimeError("permanent")

    with pytest.raises(publish.TTSError):
        publish.synthesize_all(["p"], synth=broken, attempts=3, sleep=lambda s: None)
