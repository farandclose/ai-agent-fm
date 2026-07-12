"""Tests for the promo-video tool.

Tests 1–5 are pure functions (no ffmpeg, no I/O). Test 6 monkeypatches the
subprocess boundary. Test 7 is a ffmpeg-guarded end-to-end smoke render. The
whole file stays offline — no network, no API keys — matching the repo's test
property.
"""

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

import promo_video

REPO_ROOT = Path(promo_video.__file__).parent
BADGE_ARC = REPO_ROOT / "artwork" / "brand" / "badge-arc-512.png"


# ---------------------------------------------------------------------------
# Test area 1 — RMS envelope
# ---------------------------------------------------------------------------


def _sine_samples(freq, seconds, rate=promo_video._DECODE_RATE, amp=10000):
    n = int(rate * seconds)
    return [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]


def test_rms_of_silence_is_all_zero():
    rms = promo_video.frame_rms_series([0] * 48000, fps=30)
    assert rms and all(v == 0.0 for v in rms)


def test_rms_of_constant_sine_is_constant():
    # 1000 Hz over 1 s at 30 fps: each 1600-sample window holds ~33 whole
    # periods, so per-frame RMS is constant to well within 1%.
    rms = promo_video.frame_rms_series(_sine_samples(1000, 1.0), fps=30)
    body = rms[:-1]  # drop the zero-padded final partial window
    assert len(body) > 5
    mean = sum(body) / len(body)
    assert mean > 0
    assert (max(body) - min(body)) / mean < 0.01


# ---------------------------------------------------------------------------
# Test area 2 — attack/release asymmetry
# ---------------------------------------------------------------------------


def test_attack_is_faster_than_release():
    raw = [0.0] * 20 + [1.0] * 40 + [0.0] * 80
    smoothed = promo_video.smooth_asymmetric(raw, fps=30)

    rise = next(i for i in range(20, 60) if smoothed[i] >= 0.9) - 20
    decay = next(i for i in range(60, 140) if smoothed[i] <= 0.1) - 60
    assert rise < decay


# ---------------------------------------------------------------------------
# Test area 3 — normalization
# ---------------------------------------------------------------------------


def test_normalize_clamps_to_unit_range():
    out = promo_video.normalize_envelope([float(x) for x in range(101)])
    assert all(0.0 <= v <= 1.0 for v in out)
    assert min(out) == 0.0 and max(out) == 1.0
    # Values below p5 / above p95 are clamped, not merely scaled.
    assert out[0] == 0.0 and out[-1] == 1.0


def test_normalize_constant_input_is_all_zero():
    assert promo_video.normalize_envelope([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# Test area 4 — color at radius
# ---------------------------------------------------------------------------


def test_color_at_hold_midpoints_are_exact_palette():
    # Midpoint of each color hold returns that palette hex exactly.
    holds = {
        (0.000 + 0.159) / 2: (0xEF, 0x49, 0x38),  # vermilion
        (0.232 + 0.381) / 2: (0xF3, 0x9A, 0x2B),  # amber
        (0.454 + 0.586) / 2: (0xC8, 0x3C, 0x88),  # magenta
        (0.660 + 0.766) / 2: (0x70, 0x4F, 0xBC),  # violet
        (0.839 + 1.000) / 2: (0x28, 0x5D, 0xB5),  # cobalt
    }
    for offset, expected in holds.items():
        assert promo_video.color_at_offset(offset) == expected


def test_color_at_blend_midpoint_is_rounded_average():
    # Midpoint of the vermilion→amber crossfade zone.
    verm = (0xEF, 0x49, 0x38)
    amber = (0xF3, 0x9A, 0x2B)
    mid = (0.159 + 0.232) / 2
    expected = tuple(round((a + b) / 2) for a, b in zip(verm, amber))
    assert promo_video.color_at_offset(mid) == expected


def test_color_clamps_beyond_gradient_radius():
    assert promo_video.color_at_radius(promo_video._GRADIENT_RADIUS * 2) == (
        0x28,
        0x5D,
        0xB5,
    )


# ---------------------------------------------------------------------------
# Test area 5 — cone mask fidelity vs badge-arc-512.png
# ---------------------------------------------------------------------------


def test_cone_mask_matches_badge_arc_512():
    # badge-arc-512.png crops canonical viewBox (172, 196, 680, 680) at 512 px.
    scale = 512 / 680
    mask = promo_video.render_cone_mask(512, scale, origin_x=172, origin_y=196)

    with Image.open(BADGE_ARC) as ref_img:
        ref_alpha = ref_img.convert("RGBA").getchannel("A").resize(
            (512, 512), Image.LANCZOS
        )

    mine = mask.tobytes()
    ref = ref_alpha.tobytes()
    agree = sum(1 for a, b in zip(mine, ref) if (a >= 128) == (b >= 128))
    ratio = agree / len(mine)
    assert ratio >= 0.98, f"mask agreement only {ratio:.4f}"


# ---------------------------------------------------------------------------
# Test area 6 — ffmpeg command construction (monkeypatched subprocess)
# ---------------------------------------------------------------------------


class _FakeStdin:
    def __init__(self):
        self.written = 0

    def write(self, data):
        self.written += len(data)

    def close(self):
        pass


class _FakePopen:
    def __init__(self, captured):
        self.captured = captured
        self.stdin = _FakeStdin()
        self.returncode = 0

    def wait(self):
        return 0


def test_ffmpeg_argv_construction(tmp_path, monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["decode"] = argv
        # 1 s of s16le mono at 48 kHz so the render loop produces real frames.
        pcm = struct.pack("<%dh" % 48000, *([5000] * 48000))
        return subprocess.CompletedProcess(argv, 0, stdout=pcm, stderr=b"")

    def fake_popen(argv, **kwargs):
        captured["encode"] = argv
        return _FakePopen(captured)

    monkeypatch.setattr(promo_video.subprocess, "run", fake_run)
    monkeypatch.setattr(promo_video.subprocess, "Popen", fake_popen)

    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")  # must exist; ffmpeg is faked
    out = tmp_path / "out.mp4"
    promo_video.build_promo(
        audio, out, size=32, fps=4, start=63, duration=45, title="Hi", root=REPO_ROOT
    )

    decode = captured["decode"]
    assert decode[:3] == ["ffmpeg", "-i", str(audio)]
    assert "-ss" in decode and decode[decode.index("-ss") + 1] == "63"
    assert "-t" in decode and decode[decode.index("-t") + 1] == "45"
    assert decode[-6:] == ["-ac", "1", "-ar", "48000", "-f", "s16le"] or decode[
        -7:
    ] == ["-ac", "1", "-ar", "48000", "-f", "s16le", "-"]

    encode = captured["encode"]
    assert encode[0] == "ffmpeg"
    assert "-f" in encode and "rawvideo" in encode
    assert "32x32" in encode
    assert encode[encode.index("-r") + 1] == "4"
    # trim flags apply to the audio input too
    assert "-ss" in encode and encode[encode.index("-ss") + 1] == "63"
    assert "-t" in encode and encode[encode.index("-t") + 1] == "45"
    assert str(audio) in encode
    assert "libx264" in encode and "yuv420p" in encode
    assert encode[encode.index("-crf") + 1] == "18"
    assert encode[encode.index("-b:a") + 1] == "192k"
    assert "+faststart" in encode and "-shortest" in encode
    assert encode[-1] == str(out)


def test_decode_argv_omits_trim_when_none():
    argv = promo_video.decode_argv(Path("a.mp3"), None, None)
    assert "-ss" not in argv and "-t" not in argv
    assert argv[-1] == "-"


# ---------------------------------------------------------------------------
# Test area 7 — end-to-end smoke render (guarded on ffmpeg)
# ---------------------------------------------------------------------------


def _write_wav(path, freq=220, seconds=1.0, rate=48000):
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            # Amplitude-modulated tone so the envelope actually varies.
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / rate)
            s = int(12000 * env * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", s)
        w.writeframes(bytes(frames))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_end_to_end_smoke(tmp_path):
    wav = tmp_path / "clip.wav"
    _write_wav(wav)
    out = tmp_path / "promo.mp4"
    rc = promo_video.main(
        [str(wav), "-o", str(out), "--size", "64", "--fps", "6", "--title", "Smoke"]
    )
    assert rc == 0
    assert out.exists() and out.stat().st_size > 1000
