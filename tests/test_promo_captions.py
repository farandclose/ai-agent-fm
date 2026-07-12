"""Tests for the promo-video word-synced captions + vertical format.

Fully offline: the alignment-client tests monkeypatch the module-level
``urllib.request.urlopen`` exactly like ``tests/test_elevenlabs.py`` (no
network, no API keys). Pure-function tests (block builder, cue timing, window
math, speaker mapping, cache validation) need neither ffmpeg nor Pillow.
"""

import io
import json
import math
import struct
import urllib.error
import urllib.request
import wave
import hashlib
import shutil
from pathlib import Path

import pytest

import promo_video

REPO_ROOT = Path(promo_video.__file__).parent


def _w(text, speaker="HOST", start=0.0, end=0.0, loss=0.0):
    return {"text": text, "speaker": speaker, "start": start, "end": end, "loss": loss}


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Test area 1 — block builder
# ---------------------------------------------------------------------------


def test_block_splits_on_speaker_change():
    words = [_w("a", "HOST"), _w("b", "HOST"), _w("c", "GUEST")]
    blocks = promo_video.build_blocks(words, lambda s: 0.0, max_width=9999)
    assert [[w["text"] for w in b] for b in blocks] == [["a", "b"], ["c"]]


def test_block_splits_on_sentence_punctuation():
    words = [_w("Hello"), _w("world."), _w("Next"), _w("one")]
    blocks = promo_video.build_blocks(words, lambda s: 0.0, max_width=9999)
    assert [[w["text"] for w in b] for b in blocks] == [
        ["Hello", "world."],
        ["Next", "one"],
    ]


def test_block_five_word_cap():
    words = [_w(str(i)) for i in range(6)]
    blocks = promo_video.build_blocks(words, lambda s: 0.0, max_width=9999)
    assert [len(b) for b in blocks] == [5, 1]


def test_block_width_cap_bites_before_word_cap():
    # Fake measure = character count of the joined text; cap at 3 chars.
    words = [_w("a"), _w("b"), _w("c"), _w("d")]
    blocks = promo_video.build_blocks(words, lambda s: len(s), max_width=3)
    assert [[w["text"] for w in b] for b in blocks] == [["a", "b"], ["c", "d"]]


def test_block_comma_only_splits_with_three_or_more_words():
    # Comma on the 2nd word must NOT split (block too short).
    short = [_w("a"), _w("b,"), _w("c"), _w("d")]
    assert len(promo_video.build_blocks(short, lambda s: 0.0, max_width=9999)) == 1
    # Comma on the 3rd word splits (block already has >= 3 words).
    longer = [_w("a"), _w("b"), _w("c,"), _w("d")]
    blocks = promo_video.build_blocks(longer, lambda s: 0.0, max_width=9999)
    assert [[w["text"] for w in b] for b in blocks] == [["a", "b", "c,"], ["d"]]


def test_block_em_dash_splits():
    words = [_w("wait—"), _w("no"), _w("really")]
    blocks = promo_video.build_blocks(words, lambda s: 0.0, max_width=9999)
    assert blocks[0][-1]["text"] == "wait—"
    assert len(blocks) == 2


# ---------------------------------------------------------------------------
# Test area 2 — cue timing
# ---------------------------------------------------------------------------


def test_cue_first_in_lead_and_floor():
    blocks = [[_w("a", start=0.02, end=0.3)]]
    cues = promo_video.compute_cues(blocks, window_end=10.0)
    # First in-cue is max(start - 0.05, 0): 0.02 - 0.05 clamps to 0.
    assert cues[0][0] == 0.0


def test_cue_bridges_small_gap_without_overlap():
    a = [_w("a", start=1.0, end=1.4)]
    b = [_w("b", start=1.5, end=1.9)]
    cues = promo_video.compute_cues([a, b], window_end=10.0)
    (in_a, out_a), (in_b, out_b) = cues
    assert in_a == pytest.approx(0.95)
    # gap = (1.5 - 0.05) - 1.4 = 0.05 <= 0.25 -> bridge to next in-cue.
    assert out_a == pytest.approx(1.45)
    assert in_b == pytest.approx(1.45)  # touches, never overlaps
    assert out_b == pytest.approx(1.9)


def test_cue_genuine_silence_shows_no_caption():
    a = [_w("a", start=1.0, end=1.4)]
    b = [_w("b", start=2.5, end=2.9)]
    cues = promo_video.compute_cues([a, b], window_end=10.0)
    (in_a, out_a), (in_b, out_b) = cues
    # gap = (2.5 - 0.05) - 1.4 = 1.05 > 0.25 -> out-cue is the word's own end.
    assert out_a == pytest.approx(1.4)
    assert in_b == pytest.approx(2.45)  # caption-free gap between 1.4 and 2.45
    assert in_b > out_a


def test_cue_last_out_clamped_to_window_end():
    blocks = [[_w("a", start=0.5, end=4.0)]]
    cues = promo_video.compute_cues(blocks, window_end=3.0)
    assert cues[0][1] == pytest.approx(3.0)


def test_active_word_lead_and_hold_through_pause():
    block = [_w("a", start=1.0), _w("b", start=1.5), _w("c", start=2.0)]
    # 75 ms early-fire lead: word 0 fires at 1.0 - 0.075 = 0.925.
    assert promo_video.active_word_index(block, 0.90) == 0  # before any fire -> first
    assert promo_video.active_word_index(block, 0.93) == 0
    assert promo_video.active_word_index(block, 1.45) == 1  # word 1 fired at 1.425
    # Intra-block pause: word 2 fires at 1.925; at 1.80 word 1 stays lit.
    assert promo_video.active_word_index(block, 1.80) == 1
    assert promo_video.active_word_index(block, 2.00) == 2


# ---------------------------------------------------------------------------
# Test area 3 — window math
# ---------------------------------------------------------------------------


def test_window_keeps_overlapping_words_shifted():
    words = [
        _w("a", start=0.5, end=1.0),
        _w("b", start=9.0, end=10.0),
        _w("c", start=19.0, end=20.0),
    ]
    kept = promo_video.clip_words_to_window(words, w0=8.0, w1=15.0)
    assert [w["text"] for w in kept] == ["b"]
    assert kept[0]["start"] == pytest.approx(1.0)  # 9.0 - 8.0
    assert kept[0]["end"] == pytest.approx(2.0)  # 10.0 - 8.0


def test_window_clamps_boundary_words():
    words = [
        _w("head", start=7.0, end=9.0),  # straddles w0
        _w("tail", start=14.0, end=16.0),  # straddles w1
    ]
    kept = promo_video.clip_words_to_window(words, w0=8.0, w1=15.0)
    assert [w["text"] for w in kept] == ["head", "tail"]
    assert kept[0]["start"] == pytest.approx(0.0)  # 7 - 8 = -1 -> clamp 0
    assert kept[0]["end"] == pytest.approx(1.0)  # 9 - 8
    assert kept[1]["start"] == pytest.approx(6.0)  # 14 - 8
    assert kept[1]["end"] == pytest.approx(7.0)  # 16 - 8 = 8 -> clamp to len 7


def test_window_drops_non_overlapping():
    words = [_w("x", start=0.0, end=1.0), _w("y", start=20.0, end=21.0)]
    assert promo_video.clip_words_to_window(words, w0=5.0, w1=15.0) == []


# ---------------------------------------------------------------------------
# Test area 4 — speaker mapping (character-consumption walk)
# ---------------------------------------------------------------------------


def test_speaker_mapping_multi_turn_and_tokenization():
    turns = [
        {"speaker": "HOST", "text": "Hello there, don't you [laughs] think?"},
        {"speaker": "GUEST", "text": "Our co-founder loves 3.14 and 20% growth."},
    ]
    # API tokenizes differently AND returns different case; both sides reduce
    # to the same NFKC+casefold character stream.
    api = [
        "hello", "there,", "don", "'t", "you", "think?",  # HOST (6)
        "our", "co", "-founder", "loves", "3", ".14", "and", "20", "%", "growth.",  # GUEST (10)
    ]
    speakers = promo_video.attribute_speakers([{"text": t} for t in api], turns)
    assert speakers == ["HOST"] * 6 + ["GUEST"] * 10


def test_speaker_mapping_quotes_and_ellipsis():
    turns = [{"speaker": "GUEST", "text": 'She said "stop" — really…'}]
    api = ["She", "said", '"stop"', "—", "really", "…"]
    speakers = promo_video.attribute_speakers([{"text": t} for t in api], turns)
    assert speakers == ["GUEST"] * 6


def test_speaker_mapping_divergence_reports_both_indexes():
    turns = [{"speaker": "HOST", "text": "don't go"}]
    api = ["don", "'t", "XX"]  # "XX" diverges at source token 1 ("go")
    with pytest.raises(promo_video.AlignmentError) as excinfo:
        promo_video.attribute_speakers([{"text": t} for t in api], turns)
    msg = str(excinfo.value)
    assert "word 2" in msg and "token 1" in msg and "go" in msg


def test_speaker_mapping_leftover_source_raises():
    turns = [{"speaker": "HOST", "text": "alpha beta"}]
    api = ["alpha"]  # leaves "beta" unconsumed
    with pytest.raises(promo_video.AlignmentError, match="beta"):
        promo_video.attribute_speakers([{"text": t} for t in api], turns)


def test_speaker_mapping_leftover_api_raises():
    turns = [{"speaker": "HOST", "text": "alpha"}]
    api = ["alphabeta"]  # runs past the end of the source stream
    with pytest.raises(promo_video.AlignmentError):
        promo_video.attribute_speakers([{"text": t} for t in api], turns)


# ---------------------------------------------------------------------------
# Test area 5 — alignment client (monkeypatched module-level urlopen)
# ---------------------------------------------------------------------------


def test_request_alignment_posts_multipart(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["key"] = req.get_header("Xi-api-key")
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = req.data
        return _FakeResponse(b'{"words": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # A non-ASCII transcript proves the body is UTF-8 encoded (not latin-1).
    transcript = "héllo wörld — café"
    out = promo_video.request_alignment(b"AUDIODATA", transcript, "k-secret")
    assert out == b'{"words": []}'
    assert captured["url"] == "https://api.elevenlabs.io/v1/forced-alignment"
    assert captured["key"] == "k-secret"

    # Content-Type is multipart/form-data with a boundary token.
    ctype = captured["content_type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=", 1)[1]
    assert boundary  # non-empty (uuid-based)

    body = captured["body"]
    bb = boundary.encode("ascii")
    # Full boundary framing with CRLF structure: opening line, closing sentinel.
    assert body.startswith(b"--" + bb + b"\r\n")
    assert body.endswith(b"--" + bb + b"--\r\n")
    # Each part header is CRLF-delimited and blank-line separated from its value.
    assert b'\r\nContent-Disposition: form-data; name="text"\r\n\r\n' in body
    assert (
        b'\r\nContent-Disposition: form-data; name="file"; filename="audio"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
    ) in body
    # File bytes and the UTF-8-encoded transcript both ride in the body.
    assert b"AUDIODATA" in body
    assert transcript.encode("utf-8") in body


def test_request_alignment_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    slept = []

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", None, io.BytesIO(b"slow down")
            )
        return _FakeResponse(b'{"words": [1]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = promo_video.request_alignment(
        b"A", "t", "k", sleep=lambda s: slept.append(s)
    )
    assert out == b'{"words": [1]}'
    assert calls["n"] == 2
    assert slept == [1.0]


def test_request_alignment_404_fails_without_retry(monkeypatch):
    calls = {"n": 0}
    slept = []

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", None, io.BytesIO(b"nope")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(promo_video.AlignmentError) as excinfo:
        promo_video.request_alignment(b"A", "t", "k-secret", sleep=lambda s: slept.append(s))
    assert calls["n"] == 1
    assert slept == []
    assert "404" in str(excinfo.value)
    assert "k-secret" not in str(excinfo.value)


def test_request_alignment_urlerror_retries_then_wraps(monkeypatch):
    calls = {"n": 0}
    slept = []

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(promo_video.AlignmentError):
        promo_video.request_alignment(b"A", "t", "k", sleep=lambda s: slept.append(s))
    assert calls["n"] == 3
    assert slept == [1.0, 2.0]


def test_request_alignment_missing_permissions_message(monkeypatch):
    def fake_urlopen(req, timeout=None, context=None):
        body = io.BytesIO(json.dumps({"detail": {"status": "missing_permissions"}}).encode())
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(promo_video.AlignmentError) as excinfo:
        promo_video.request_alignment(b"A", "t", "k-secret")
    assert "Forced Alignment" in str(excinfo.value)
    assert "k-secret" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_alignment_words_valid():
    raw = json.dumps(
        {"words": [{"text": "hi", "start": 0.0, "end": 0.5, "loss": 0.1}]}
    ).encode()
    words = promo_video.parse_alignment_words(raw)
    assert words == [{"text": "hi", "start": 0.0, "end": 0.5, "loss": 0.1}]


def test_parse_alignment_words_not_json():
    with pytest.raises(promo_video.AlignmentError):
        promo_video.parse_alignment_words(b"not json at all")


def test_parse_alignment_words_missing_words():
    with pytest.raises(promo_video.AlignmentError):
        promo_video.parse_alignment_words(json.dumps({"foo": 1}).encode())


def test_parse_alignment_words_empty_list():
    with pytest.raises(promo_video.AlignmentError):
        promo_video.parse_alignment_words(json.dumps({"words": []}).encode())


def test_parse_alignment_words_non_finite_time():
    raw = json.dumps(
        {"words": [{"text": "hi", "start": 0.0, "end": float("inf"), "loss": 0.0}]}
    ).encode()
    with pytest.raises(promo_video.AlignmentError):
        promo_video.parse_alignment_words(raw)


# ---------------------------------------------------------------------------
# Test area 6 — cache round-trip + validation
# ---------------------------------------------------------------------------


def _cache_words():
    return [
        {"text": "Hello", "start": 0.0, "end": 0.5, "loss": 0.1, "speaker": "HOST"},
        {"text": "world", "start": 0.5, "end": 1.0, "loss": 0.2, "speaker": "GUEST"},
    ]


def test_cache_roundtrip(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    promo_video.write_alignment_cache(cache, "ASHA", "TSHA", _cache_words())
    loaded = promo_video.load_alignment_cache(cache, "ASHA", "TSHA")
    assert loaded == _cache_words()


def test_cache_hash_mismatch_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    promo_video.write_alignment_cache(cache, "ASHA", "TSHA", _cache_words())
    with pytest.raises(promo_video.AlignmentError, match="refresh-alignment"):
        promo_video.load_alignment_cache(cache, "OTHER", "TSHA")


def test_cache_transcript_hash_mismatch_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    promo_video.write_alignment_cache(cache, "ASHA", "TSHA", _cache_words())
    with pytest.raises(promo_video.AlignmentError):
        promo_video.load_alignment_cache(cache, "ASHA", "DIFFERENT")


def test_cache_bad_version_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    cache.write_text(json.dumps({"version": 2, "audio_sha256": "ASHA", "words": _cache_words()}))
    with pytest.raises(promo_video.AlignmentError):
        promo_video.load_alignment_cache(cache, "ASHA")


def test_cache_non_finite_time_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    words = [{"text": "a", "start": 0.0, "end": float("inf"), "loss": 0.0, "speaker": "HOST"}]
    cache.write_text(json.dumps({"version": 1, "audio_sha256": "ASHA", "transcript_sha256": None, "words": words}))
    with pytest.raises(promo_video.AlignmentError):
        promo_video.load_alignment_cache(cache, "ASHA")


def test_cache_unordered_starts_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    words = [
        {"text": "a", "start": 1.0, "end": 2.0, "loss": 0.0, "speaker": "HOST"},
        {"text": "b", "start": 0.5, "end": 1.5, "loss": 0.0, "speaker": "HOST"},
    ]
    cache.write_text(json.dumps({"version": 1, "audio_sha256": "ASHA", "transcript_sha256": None, "words": words}))
    with pytest.raises(promo_video.AlignmentError):
        promo_video.load_alignment_cache(cache, "ASHA")


def test_cache_unknown_speaker_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    words = [{"text": "a", "start": 0.0, "end": 1.0, "loss": 0.0, "speaker": "NARRATOR"}]
    cache.write_text(json.dumps({"version": 1, "audio_sha256": "ASHA", "transcript_sha256": None, "words": words}))
    with pytest.raises(promo_video.AlignmentError):
        promo_video.load_alignment_cache(cache, "ASHA")


def test_cache_corrupt_json_raises(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    cache.write_text("{ this is not json")
    with pytest.raises(promo_video.AlignmentError, match="refresh-alignment"):
        promo_video.load_alignment_cache(cache, "ASHA")


def test_refresh_bypasses_valid_cache(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    audio_sha = hashlib.sha256(b"AUDIOBYTES").hexdigest()
    transcript = tmp_path / "script.json"
    transcript.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "fresh word"}]}))
    text = promo_video.build_transcript_text([{"speaker": "HOST", "text": "fresh word"}])
    tsha = promo_video._sha256_text(text)
    cache = tmp_path / "clip.alignment.json"
    # A valid cache with *stale* content the refresh must ignore.
    promo_video.write_alignment_cache(
        cache, audio_sha, tsha,
        [{"text": "stale", "start": 0.0, "end": 1.0, "loss": 0.0, "speaker": "HOST"}],
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")

    def fake_request(audio_bytes, transcript_text, api_key, sleep=None):
        return json.dumps(
            {"words": [
                {"text": "fresh", "start": 0.0, "end": 0.4, "loss": 0.0},
                {"text": "word", "start": 0.4, "end": 0.9, "loss": 0.0},
            ]}
        ).encode()

    monkeypatch.setattr(promo_video, "request_alignment", fake_request)
    words = promo_video.resolve_caption_words(audio, cache, transcript, refresh=True)
    assert [w["text"] for w in words] == ["fresh", "word"]
    # Cache overwritten with the fresh alignment.
    assert [w["text"] for w in promo_video.load_alignment_cache(cache, audio_sha, tsha)] == [
        "fresh",
        "word",
    ]


def test_valid_cache_renders_without_transcript_or_network(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    audio_sha = hashlib.sha256(b"AUDIOBYTES").hexdigest()
    cache = tmp_path / "clip.alignment.json"
    promo_video.write_alignment_cache(cache, audio_sha, "ignored", _cache_words())

    def boom(*a, **k):
        raise AssertionError("must not touch the network with a valid cache")

    monkeypatch.setattr(promo_video, "request_alignment", boom)
    words = promo_video.resolve_caption_words(audio, cache, transcript_path=None, refresh=False)
    assert [w["text"] for w in words] == ["Hello", "world"]


def test_no_transcript_no_cache_disables_captions(tmp_path):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    cache = tmp_path / "missing.alignment.json"
    assert promo_video.resolve_caption_words(audio, cache, None, False) is None


def test_fetch_without_api_key_raises_configerror(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    transcript = tmp_path / "script.json"
    transcript.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "hi there"}]}))
    cache = tmp_path / "clip.alignment.json"  # absent -> needs a fetch
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(promo_video.ConfigError, match="ELEVENLABS_API_KEY"):
        promo_video.resolve_caption_words(audio, cache, transcript, refresh=False)


def test_cache_unwritable_raises_alignment_error(tmp_path):
    dest = tmp_path / "nope" / "does" / "not" / "exist.json"  # missing parents
    with pytest.raises(promo_video.AlignmentError, match="exist.json"):
        promo_video.write_alignment_cache(dest, "ASHA", "TSHA", _cache_words())


# ---------------------------------------------------------------------------
# Test area 7 — transcript loader
# ---------------------------------------------------------------------------


def test_transcript_missing_file_raises(tmp_path):
    with pytest.raises(promo_video.EpisodeError):
        promo_video.load_transcript(tmp_path / "nope.json")


def test_transcript_bad_json_raises(tmp_path):
    p = tmp_path / "script.json"
    p.write_text("{not json")
    with pytest.raises(promo_video.EpisodeError):
        promo_video.load_transcript(p)


def test_transcript_empty_turns_raises(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"turns": []}))
    with pytest.raises(promo_video.EpisodeError):
        promo_video.load_transcript(p)


def test_transcript_bad_speaker_raises(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"turns": [{"speaker": "NARRATOR", "text": "hi"}]}))
    with pytest.raises(promo_video.EpisodeError):
        promo_video.load_transcript(p)


def test_transcript_empty_text_raises(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "   "}]}))
    with pytest.raises(promo_video.EpisodeError):
        promo_video.load_transcript(p)


# ---------------------------------------------------------------------------
# Test area 8 — audio-tag stripping
# ---------------------------------------------------------------------------


def test_transcript_text_strips_audio_tags():
    turns = [
        {"speaker": "HOST", "text": "Hello [laughs] world"},
        {"speaker": "GUEST", "text": "[sighs] yeah   really"},
    ]
    text = promo_video.build_transcript_text(turns)
    assert text == "Hello world yeah really"
    assert "[" not in text and "]" not in text


# ---------------------------------------------------------------------------
# Test area 9 — vertical format / ffmpeg argv
# ---------------------------------------------------------------------------


def test_vertical_height_math():
    assert promo_video._vertical_height(1080) == 1920
    assert promo_video._vertical_height(64) == 114  # round(16*64/9)=114
    # Odd raw multiple is forced even for yuv420p.
    assert promo_video._vertical_height(57) % 2 == 0


def test_encode_argv_vertical_size_string():
    argv = promo_video.encode_argv(Path("o.mp4"), 1080, 1920, 30, Path("a.mp3"), None, None)
    assert argv[argv.index("-s") + 1] == "1080x1920"


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


def _fake_subprocess(monkeypatch, captured):
    def fake_run(argv, **kwargs):
        pcm = struct.pack("<%dh" % 48000, *([5000] * 48000))
        return __import__("subprocess").CompletedProcess(argv, 0, stdout=pcm, stderr=b"")

    def fake_popen(argv, **kwargs):
        captured["encode"] = argv
        return _FakePopen(captured)

    monkeypatch.setattr(promo_video.subprocess, "run", fake_run)
    monkeypatch.setattr(promo_video.subprocess, "Popen", fake_popen)


def test_build_promo_vertical_argv(tmp_path, monkeypatch):
    captured = {}
    _fake_subprocess(monkeypatch, captured)
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")
    out = tmp_path / "out.mp4"
    promo_video.build_promo(
        audio, out, size=64, fps=4, start=None, duration=None,
        title=None, root=REPO_ROOT, words=None, fmt="vertical",
    )
    assert "64x114" in captured["encode"]


def test_empty_window_warns_and_renders_without_captions(tmp_path, monkeypatch, capsys):
    captured = {}
    _fake_subprocess(monkeypatch, captured)
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")
    out = tmp_path / "out.mp4"
    # All words sit past the 1 s decoded window -> zero captions, not an error.
    words = [{"text": "later", "start": 30.0, "end": 30.5, "loss": 0.0, "speaker": "HOST"}]
    promo_video.build_promo(
        audio, out, size=32, fps=4, start=None, duration=None,
        title=None, root=REPO_ROOT, words=words, fmt="square",
    )
    assert "without captions" in capsys.readouterr().err
    assert "encode" in captured  # the render still ran


# ---------------------------------------------------------------------------
# Test area 10 — end-to-end captioned smoke render (guarded on ffmpeg)
# ---------------------------------------------------------------------------


def _write_wav(path, seconds=1.0, rate=48000, freq=220):
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / rate)
            s = int(12000 * env * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", s)
        w.writeframes(bytes(frames))


def _extract_frame(mp4, t):
    """Decode one frame at ``t`` seconds from ``mp4`` into a PIL RGB image."""
    import subprocess

    from PIL import Image

    png = mp4.with_name(mp4.stem + "-frame.png")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp4), "-ss", str(t), "-frames:v", "1", str(png)],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-800:]
    return Image.open(png).convert("RGB")


def _caption_band_nonink_pixels(img, baseline_y, cap_px):
    """Count pixels in the caption band (just above ``baseline_y``) far from ink.

    yuv420p round-trips ink with a few units of noise, so a generous threshold
    cleanly separates flat ground from ivory/amber/magenta caption glyphs.
    """
    width, height = img.size
    px = img.load()
    top = max(0, baseline_y - cap_px)
    bottom = min(height, baseline_y)
    ink = promo_video.INK
    count = 0
    for y in range(top, bottom):
        for x in range(width):
            r, g, b = px[x, y]
            if abs(r - ink[0]) + abs(g - ink[1]) + abs(b - ink[2]) > 60:
                count += 1
    return count


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
@pytest.mark.parametrize("fmt", ["square", "vertical"])
def test_end_to_end_captioned_smoke(tmp_path, fmt):
    wav = tmp_path / "clip.wav"
    _write_wav(wav)
    audio_sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    cache = tmp_path / "clip.alignment.json"
    words = [
        {"text": "Hello", "start": 0.0, "end": 0.3, "loss": 0.05, "speaker": "HOST"},
        {"text": "there,", "start": 0.3, "end": 0.6, "loss": 0.05, "speaker": "HOST"},
        {"text": "friend.", "start": 0.6, "end": 0.95, "loss": 0.05, "speaker": "GUEST"},
    ]
    promo_video.write_alignment_cache(cache, audio_sha, "ignored", words)

    size = 160
    captioned = tmp_path / "promo.mp4"
    # This test locates captions via the swap-style baseline (_VERTICAL_CAPTION_
    # BASELINE), so it pins --caption-style swap explicitly now that the CLI
    # default is three-line.
    rc = promo_video.main([
        str(wav), "-o", str(captioned), "--captions-json", str(cache),
        "--size", str(size), "--fps", "6", "--format", fmt, "--title", "Smoke",
        "--caption-style", "swap",
    ])
    assert rc == 0
    assert captioned.exists() and captioned.stat().st_size > 1000

    # Control render of the same clip with captions OFF (cache path absent).
    plain = tmp_path / "plain.mp4"
    rc_plain = promo_video.main([
        str(wav), "-o", str(plain),
        "--captions-json", str(tmp_path / "absent.alignment.json"),
        "--size", str(size), "--fps", "6", "--format", fmt, "--title", "Smoke",
    ])
    assert rc_plain == 0

    width, height, _ = promo_video._canvas_dims(size, fmt)
    cap_frame = _extract_frame(captioned, 0.25)
    plain_frame = _extract_frame(plain, 0.25)
    # Output dimensions are probed from a decoded frame (even, yuv420p-safe).
    assert cap_frame.size == (width, height)
    assert plain_frame.size == (width, height)

    if fmt == "vertical":
        baseline_y = round(promo_video._VERTICAL_CAPTION_BASELINE * size / promo_video._VERTICAL_REF)
    else:
        baseline_y = round(promo_video._CAPTION_BASELINE_FRAC * size)
    cap_px = max(1, round(promo_video._CAPTION_PT_FRAC * size))

    # At a cue time the caption band carries ink-contrasting glyphs; the
    # uncaptioned render's identical band is pure ground.
    assert _caption_band_nonink_pixels(cap_frame, baseline_y, cap_px) > 0
    assert _caption_band_nonink_pixels(plain_frame, baseline_y, cap_px) == 0


# ---------------------------------------------------------------------------
# Align-only flow (offline via monkeypatched request_alignment)
# ---------------------------------------------------------------------------


def test_align_only_prints_stats_and_writes_cache(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    transcript = tmp_path / "script.json"
    transcript.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "hello world"}]}))
    cache = tmp_path / "clip.alignment.json"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")

    def fake_request(audio_bytes, transcript_text, api_key, sleep=None):
        assert "hello world" in transcript_text
        return json.dumps(
            {"words": [
                {"text": "hello", "start": 0.0, "end": 0.4, "loss": 0.10},
                {"text": "world", "start": 0.4, "end": 0.9, "loss": 0.30},
            ]}
        ).encode()

    monkeypatch.setattr(promo_video, "request_alignment", fake_request)
    rc = promo_video.main([
        str(audio), "--transcript", str(transcript),
        "--captions-json", str(cache), "--align-only",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2" in out  # word count
    assert cache.exists()
    cached = json.loads(cache.read_text())
    assert [w["speaker"] for w in cached["words"]] == ["HOST", "HOST"]


def test_align_only_requires_transcript(tmp_path):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    with pytest.raises(SystemExit) as excinfo:
        promo_video.main([str(audio), "--align-only"])
    assert excinfo.value.code == 2


def test_render_requires_output_unless_align_only(tmp_path):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    with pytest.raises(SystemExit) as excinfo:
        promo_video.main([str(audio)])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Finding 1 — caption line layout is stable: static words never move when the
# highlight (drawn 1.08x) advances.
# ---------------------------------------------------------------------------


def _caption_fonts(base_pt=40):
    from PIL import ImageFont

    font_path = REPO_ROOT / "artwork" / "fonts" / "SpaceGrotesk-Bold.ttf"
    caption_font = ImageFont.truetype(str(font_path), base_pt)
    active_pt = max(1, round(base_pt * promo_video._CAPTION_ACTIVE_SCALE))
    active_font = ImageFont.truetype(str(font_path), active_pt)
    return caption_font, active_font


def test_caption_static_word_positions_stable_across_active_index():
    caption_font, active_font = _caption_fonts()
    block = [_w(t, "HOST") for t in ("alpha", "beta", "gamma", "delta", "epsilon")]
    center_x = 500.0
    variants = {
        a: promo_video._caption_word_placements(block, caption_font, active_font, a, center_x)
        for a in range(len(block))
    }
    # For each word, its draw_x must be pixel-identical across every variant in
    # which it is NOT the active (1.08x) word.
    for word_i in range(len(block)):
        positions = {
            round(variants[a][word_i][2], 9)
            for a in range(len(block))
            if a != word_i
        }
        assert len(positions) == 1, f"word {word_i} moved across variants: {positions}"


def test_caption_active_word_centered_in_base_cell():
    caption_font, active_font = _caption_fonts()
    block = [_w(t, "HOST") for t in ("alpha", "beta", "gamma")]
    center_x = 500.0
    base = promo_video._caption_word_placements(block, caption_font, active_font, active_idx=-1, center_x=center_x)
    for i, word in enumerate(block):
        variant = promo_video._caption_word_placements(block, caption_font, active_font, i, center_x)
        base_pen = base[i][2]
        base_adv = caption_font.getlength(word["text"])
        active_adv = active_font.getlength(word["text"])
        expected = base_pen + (base_adv - active_adv) / 2
        assert variant[i][2] == pytest.approx(expected)
        # The active word is drawn with the larger font.
        assert variant[i][1] is active_font


# ---------------------------------------------------------------------------
# Finding 2 — empty/whitespace-only API words are dropped before caching.
# ---------------------------------------------------------------------------


def test_empty_api_words_dropped_before_cache(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    audio_sha = hashlib.sha256(b"AUDIOBYTES").hexdigest()
    transcript = tmp_path / "script.json"
    transcript.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "hello world"}]}))
    tsha = promo_video._sha256_text(
        promo_video.build_transcript_text([{"speaker": "HOST", "text": "hello world"}])
    )
    cache = tmp_path / "clip.alignment.json"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")

    def fake_request(audio_bytes, transcript_text, api_key, sleep=None):
        return json.dumps({"words": [
            {"text": "hello", "start": 0.0, "end": 0.4, "loss": 0.1},
            {"text": "", "start": 0.4, "end": 0.4, "loss": 0.0},
            {"text": " ", "start": 0.45, "end": 0.45, "loss": 0.0},
            {"text": "world", "start": 0.5, "end": 0.9, "loss": 0.2},
        ]}).encode()

    monkeypatch.setattr(promo_video, "request_alignment", fake_request)
    words = promo_video.resolve_caption_words(audio, cache, transcript, refresh=False)
    assert [w["text"] for w in words] == ["hello", "world"]
    # The poison words never reach the cache, so it reloads cleanly (no
    # empty-text validation failure and no re-fetch on the next run).
    reloaded = promo_video.load_alignment_cache(cache, audio_sha, tsha)
    assert [w["text"] for w in reloaded] == ["hello", "world"]


# ---------------------------------------------------------------------------
# Finding 3 — fresh responses are ordering-checked exactly like the cache.
# ---------------------------------------------------------------------------


def test_parse_alignment_words_unordered_starts_raises():
    raw = json.dumps({"words": [
        {"text": "a", "start": 1.0, "end": 1.5, "loss": 0.0},
        {"text": "b", "start": 0.5, "end": 1.0, "loss": 0.0},  # start goes backwards
    ]}).encode()
    with pytest.raises(promo_video.AlignmentError, match="non-decreasing"):
        promo_video.parse_alignment_words(raw)


def test_parse_alignment_words_negative_start_raises():
    raw = json.dumps({"words": [{"text": "a", "start": -0.1, "end": 0.5, "loss": 0.0}]}).encode()
    with pytest.raises(promo_video.AlignmentError):
        promo_video.parse_alignment_words(raw)


# ---------------------------------------------------------------------------
# Finding 5 — TimeoutError is transient (retried) and finally wrapped.
# ---------------------------------------------------------------------------


def test_request_alignment_timeout_retries_then_wraps(monkeypatch):
    calls = {"n": 0}
    slept = []

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(promo_video.AlignmentError) as excinfo:
        promo_video.request_alignment(b"A", "t", "k-secret", sleep=lambda s: slept.append(s))
    assert calls["n"] == 3
    assert slept == [1.0, 2.0]
    assert "k-secret" not in str(excinfo.value)


def test_request_alignment_timeout_during_read_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class _TimeoutOnRead:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise TimeoutError("read timed out")

    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _TimeoutOnRead()
        return _FakeResponse(b'{"words": [1]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = promo_video.request_alignment(b"A", "t", "k", sleep=lambda s: None)
    assert out == b'{"words": [1]}'
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Finding 6 — loss required on fresh responses; tolerated absent in a cache.
# ---------------------------------------------------------------------------


def test_parse_alignment_words_missing_loss_raises():
    raw = json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.5}]}).encode()
    with pytest.raises(promo_video.AlignmentError, match="loss"):
        promo_video.parse_alignment_words(raw)


def test_parse_alignment_words_boolean_loss_raises():
    raw = json.dumps(
        {"words": [{"text": "hi", "start": 0.0, "end": 0.5, "loss": True}]}
    ).encode()
    with pytest.raises(promo_video.AlignmentError, match="loss"):
        promo_video.parse_alignment_words(raw)


def test_align_only_tolerates_cache_without_loss(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    audio_sha = hashlib.sha256(b"AUDIOBYTES").hexdigest()
    transcript = tmp_path / "script.json"
    transcript.write_text(json.dumps({"turns": [{"speaker": "HOST", "text": "hello world"}]}))
    tsha = promo_video._sha256_text(
        promo_video.build_transcript_text([{"speaker": "HOST", "text": "hello world"}])
    )
    cache = tmp_path / "clip.alignment.json"
    # Hand-written cache whose words carry NO loss field (valid otherwise).
    cache.write_text(json.dumps({
        "version": 1, "audio_sha256": audio_sha, "transcript_sha256": tsha,
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.4, "speaker": "HOST"},
            {"text": "world", "start": 0.4, "end": 0.9, "speaker": "HOST"},
        ],
    }))

    def boom(*a, **k):
        raise AssertionError("must not fetch with a valid cache")

    monkeypatch.setattr(promo_video, "request_alignment", boom)
    rc = promo_video.main([
        str(audio), "--transcript", str(transcript),
        "--captions-json", str(cache), "--align-only",
    ])
    assert rc == 0
    assert "mean 0.0000" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Finding 7 — non-UTF-8 transcript/cache files raise wrapped errors.
# ---------------------------------------------------------------------------


def test_transcript_non_utf8_raises_episode_error(tmp_path):
    p = tmp_path / "script.json"
    p.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")
    with pytest.raises(promo_video.EpisodeError, match="script.json"):
        promo_video.load_transcript(p)


def test_cache_non_utf8_raises_alignment_error(tmp_path):
    cache = tmp_path / "clip.alignment.json"
    cache.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")
    with pytest.raises(promo_video.AlignmentError, match="refresh-alignment"):
        promo_video.load_alignment_cache(cache, "ASHA")


# ---------------------------------------------------------------------------
# Finding 4 — the caption window uses the explicit --duration, not the decoded
# sample count.
# ---------------------------------------------------------------------------


def test_duration_window_uses_explicit_duration(tmp_path, monkeypatch):
    captured = {}
    _fake_subprocess(monkeypatch, captured)
    seen = {}
    orig = promo_video.clip_words_to_window

    def spy(words, w0, w1):
        seen["w0"], seen["w1"] = w0, w1
        return orig(words, w0, w1)

    monkeypatch.setattr(promo_video, "clip_words_to_window", spy)
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")
    out = tmp_path / "out.mp4"
    # The fake decode yields exactly 1.0 s of samples; --duration says 2.0 s.
    # A word ending exactly on the 2.0 s boundary must fall inside the window.
    words = [{"text": "edge", "start": 1.9, "end": 2.0, "loss": 0.0, "speaker": "HOST"}]
    promo_video.build_promo(
        audio, out, size=32, fps=4, start=0.0, duration=2.0,
        title=None, root=REPO_ROOT, words=words, fmt="square",
    )
    assert seen["w1"] == pytest.approx(2.0)  # explicit duration wins over 1.0 s decoded
    # And the boundary word survives the clip (would be dropped at w1 = 1.0).
    assert promo_video.clip_words_to_window(words, 0.0, 2.0)[0]["text"] == "edge"


# ---------------------------------------------------------------------------
# Finding 8 — an odd --size is rejected at the CLI (both formats).
# ---------------------------------------------------------------------------


def test_odd_size_rejected(tmp_path):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    out = tmp_path / "out.mp4"
    with pytest.raises(SystemExit) as excinfo:
        promo_video.main([str(audio), "-o", str(out), "--size", "65"])
    assert excinfo.value.code == 2


def test_odd_size_rejected_vertical(tmp_path):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    out = tmp_path / "out.mp4"
    with pytest.raises(SystemExit) as excinfo:
        promo_video.main(
            [str(audio), "-o", str(out), "--size", "65", "--format", "vertical"]
        )
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Multi-line caption window planner (--caption-style two-line / three-line)
#
# The window/slide logic is a pure planner: (blocks, cues, style, t) -> a list
# of (block_index, kind, slot, alpha) line plans, slots in pitch units. No
# fonts, no Pillow, no ffmpeg — every case below is a direct unit test.
# ---------------------------------------------------------------------------


def _pb(*speakers):
    """Blocks (one word each) from a sequence of HOST/GUEST speaker labels."""
    return [[_w("w", sp)] for sp in speakers]


def _plan_map(plans):
    """Index a plan list by (block_index, kind) -> (slot, alpha)."""
    return {(b, k): (slot, a) for b, k, slot, a in plans}


def test_plan_swap_single_focus_at_all_live_times():
    blocks = _pb("HOST", "HOST")
    cues = [(0.0, 1.0), (1.0, 2.0)]
    assert promo_video.plan_caption_lines(blocks, cues, "swap", 0.5) == [(0, "focus", 0, 1)]
    assert promo_video.plan_caption_lines(blocks, cues, "swap", 1.5) == [(1, "focus", 0, 1)]
    # No live block -> no plans.
    assert promo_video.plan_caption_lines(blocks, cues, "swap", 9.0) == []


def test_plan_three_line_steady_mid_turn():
    blocks = _pb("HOST", "HOST", "HOST")
    cues = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]  # contiguous (out == in)
    # t is well past block 1's 0.25 s slide window -> steady state.
    plans = promo_video.plan_caption_lines(blocks, cues, "three-line", 1.9)
    assert plans == [(0, "context", -1, 1), (1, "focus", 0, 1), (2, "context", 1, 1)]


def test_plan_speaker_boundary_flushes_next_and_prev():
    blocks = _pb("HOST", "HOST", "GUEST", "GUEST")
    cues = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    # Last block of the HOST turn: prev shown, no next (block 2 is GUEST).
    last = promo_video.plan_caption_lines(blocks, cues, "three-line", 1.9)
    assert last == [(0, "context", -1, 1), (1, "focus", 0, 1)]
    # First block of the GUEST turn at its in-cue: no prev, no slide.
    first = promo_video.plan_caption_lines(blocks, cues, "three-line", 2.0)
    assert first == [(2, "focus", 0, 1), (3, "context", 1, 1)]


def test_plan_post_silence_restart_and_dark_gap():
    blocks = _pb("HOST", "HOST")
    cues = [(0.0, 1.0), (2.0, 3.0)]  # genuine silence: not contiguous
    # During the dark gap the planner returns nothing.
    assert promo_video.plan_caption_lines(blocks, cues, "three-line", 1.5) == []
    # Restart: no prev (bridge not taken), no slide, no next (no block 2).
    assert promo_video.plan_caption_lines(blocks, cues, "three-line", 2.0) == [(1, "focus", 0, 1)]


def test_plan_three_line_slide_math_and_settle():
    blocks = _pb("HOST", "HOST", "HOST", "HOST")
    cues = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    t0, dur = 2.0, 0.25  # slide on block i = 2 (i-1, i-2, i+1 all present)
    e = 1.0 - (1.0 - 0.5) ** 3
    rise = 1.0 - e
    mid = _plan_map(promo_video.plan_caption_lines(blocks, cues, "three-line", t0 + dur / 2))
    assert mid[(2, "focus")] == pytest.approx((rise, 1.0))          # new focus rises 0 + (1-e)
    assert mid[(1, "context")] == pytest.approx((-1 + rise, 1.0))   # old focus -> catch-up
    assert mid[(0, "context")] == pytest.approx((-2 + rise, rise))  # old prev leaves fading
    assert mid[(3, "context")] == pytest.approx((1.0, e))           # new next fades in in place
    # At t0 + dur the window has settled: steady slots, i-2 gone.
    settled = _plan_map(promo_video.plan_caption_lines(blocks, cues, "three-line", t0 + dur))
    assert settled[(2, "focus")] == (0, 1)
    assert settled[(1, "context")] == (-1, 1)
    assert settled[(3, "context")] == (1, 1)
    assert (0, "context") not in settled


def test_plan_slide_dur_clamps_to_short_cue_span():
    blocks = _pb("HOST", "HOST", "HOST")
    # Block 1's cue span is only 0.1 s, shorter than the nominal 0.25 s slide,
    # so dur clamps to 0.1 and u reaches 0.5 at t0 + 0.05 (not t0 + 0.125).
    cues = [(0.0, 1.0), (1.0, 1.1), (1.1, 2.0)]
    rise_half = (1.0 - 0.5) ** 3  # 1 - e(0.5) = 0.125
    mid = _plan_map(promo_video.plan_caption_lines(blocks, cues, "three-line", 1.05))
    assert mid[(1, "focus")] == pytest.approx((rise_half, 1.0))
    assert mid[(1, "focus")][0] == pytest.approx(0.125)  # not the un-clamped 0.512


def test_plan_two_line_never_prev_and_old_focus_slides_out():
    blocks = _pb("HOST", "HOST", "HOST")
    cues = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    # Steady: read-ahead only, never a catch-up (prev) line above the focus.
    steady = promo_video.plan_caption_lines(blocks, cues, "two-line", 1.9)
    assert steady == [(1, "focus", 0, 1), (2, "context", 1, 1)]
    # Sliding: the old focus (block 0) slides up while fading out; still no prev.
    e = 1.0 - (1.0 - 0.5) ** 3
    rise = 1.0 - e
    mid = _plan_map(promo_video.plan_caption_lines(blocks, cues, "two-line", 1.125))
    assert mid[(1, "focus")] == pytest.approx((rise, 1.0))
    assert mid[(0, "context")] == pytest.approx((-1 + rise, rise))  # fades out, alpha < 1
    assert (0, "context") in mid and len(mid) == 3  # old-focus, new-focus, new-next only


def test_caption_focus_baseline_per_style_and_scale():
    # Vertical focus baselines at S = 1080, per style.
    assert promo_video._caption_focus_baseline(1080, "vertical", "swap") == 1160
    assert promo_video._caption_focus_baseline(1080, "vertical", "two-line") == 1120
    assert promo_video._caption_focus_baseline(1080, "vertical", "three-line") == 1104
    # Scales by S / 1080.
    assert promo_video._caption_focus_baseline(540, "vertical", "three-line") == round(
        1104 * 540 / 1080
    )
    # Square is 0.76 S for every style.
    assert promo_video._caption_focus_baseline(1000, "square", "three-line") == 760


# ---------------------------------------------------------------------------
# Rev 2 amendments (PM review of the first three-line sample, 2026-07-12)
#   A. two-line/three-line drop the 1.08x scale pop: the active word renders at
#      the base caption pt (color-only highlight); swap keeps the pop.
#   B. compute_cues gains a bridge mode: "gap" (default, today's <= 0.25 s rule)
#      vs "speaker" (same-speaker neighbours always bridge; different-speaker
#      neighbours never bridge, out = last word's end).
# ---------------------------------------------------------------------------


# --- Amendment A: color-only highlight for the multi-line styles ------------


def test_active_font_swap_keeps_pop_multiline_drops_it():
    caption_font, popped_font = _caption_fonts()
    # swap keeps the 1.08x scale pop; two-line / three-line degenerate to the
    # base caption font (no pop), leaving only the speaker accent color.
    assert promo_video._caption_active_font("swap", caption_font, popped_font) is popped_font
    assert promo_video._caption_active_font("two-line", caption_font, popped_font) is caption_font
    assert promo_video._caption_active_font("three-line", caption_font, popped_font) is caption_font


def test_non_swap_active_word_draw_x_equals_pen_no_shift():
    caption_font, _ = _caption_fonts()
    block = [_w(t, "HOST") for t in ("alpha", "throwaway", "gamma")]
    center_x = 500.0
    # Non-swap passes the base caption font as the active font (Amendment A), so
    # _caption_word_placements degenerates: the active word's draw_x is exactly
    # its pen position, identical to when it is a static (non-active) word — no
    # centering shift bleeds into the neighbours.
    base = promo_video._caption_word_placements(block, caption_font, caption_font, -1, center_x)
    for i in range(len(block)):
        variant = promo_video._caption_word_placements(block, caption_font, caption_font, i, center_x)
        assert variant[i][2] == pytest.approx(base[i][2])  # draw_x == pen (no shift)
        assert variant[i][1] is caption_font               # base pt, no scale pop
        # Every static word also stays put across variants.
        for j in range(len(block)):
            if j != i:
                assert variant[j][2] == pytest.approx(base[j][2])


# --- Amendment B: compute_cues bridge modes ---------------------------------


def test_cue_speaker_mode_bridges_same_speaker_across_big_gap():
    # Same speaker; gap = (2.0 - 0.05) - 1.4 = 0.55 s > 0.25 s. Gap mode would
    # NOT bridge, but speaker mode bridges same-speaker neighbours always.
    a = [_w("a", "HOST", start=1.0, end=1.4)]
    b = [_w("b", "HOST", start=2.0, end=2.4)]
    (in_a, out_a), (in_b, out_b) = promo_video.compute_cues([a, b], 10.0, bridge="speaker")
    early_next = 2.0 - promo_video._CUE_LEAD
    assert out_a == pytest.approx(early_next)  # bridged to the next in-cue
    assert in_b == pytest.approx(early_next)
    assert out_a == in_b  # contiguous -> the planner slides
    # The planner reads the contiguous cues as a same-speaker scroll: the
    # catch-up line shows and the focus advances.
    cues = [(in_a, out_a), (in_b, out_b)]
    kinds = {(bi, k) for bi, k, _, _ in promo_video.plan_caption_lines([a, b], cues, "three-line", in_b + 0.001)}
    assert (0, "context") in kinds  # catch-up line present
    assert (1, "focus") in kinds


def test_cue_speaker_mode_refuses_cross_speaker_small_gap():
    # Different speakers; gap = (1.5 - 0.05) - 1.4 = 0.05 s <= 0.25 s. Gap mode
    # WOULD bridge, but speaker mode never bridges across a speaker change.
    a = [_w("a", "HOST", start=1.0, end=1.4)]
    b = [_w("b", "GUEST", start=1.5, end=1.9)]
    (in_a, out_a), (in_b, out_b) = promo_video.compute_cues([a, b], 10.0, bridge="speaker")
    assert out_a == pytest.approx(1.4)   # out = the last word's own end
    assert in_b == pytest.approx(1.45)   # next in-cue unchanged (early_next)
    assert out_a < in_b                  # a blank beat between the turns


def test_cue_gap_mode_is_default_and_ignores_speaker():
    # The default matches explicit bridge="gap", byte-for-byte, and the gap rule
    # bridges on gap size alone — the speaker change is irrelevant in gap mode.
    a = [_w("a", "HOST", start=1.0, end=1.4)]
    b = [_w("b", "GUEST", start=1.5, end=1.9)]  # different speaker, small gap
    default = promo_video.compute_cues([a, b], 10.0)
    gap = promo_video.compute_cues([a, b], 10.0, bridge="gap")
    assert default == gap
    (in_a, out_a), (in_b, out_b) = default
    assert out_a == pytest.approx(1.45)  # small gap bridges despite the speaker change
    assert in_b == pytest.approx(1.45)


# ---------------------------------------------------------------------------
# End-to-end multi-line captioned smoke render (guarded on ffmpeg)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
@pytest.mark.parametrize("style", ["two-line", "three-line"])
@pytest.mark.parametrize("fmt", ["square", "vertical"])
def test_end_to_end_multiline_caption_smoke(tmp_path, style, fmt):
    wav = tmp_path / "clip.wav"
    _write_wav(wav)
    audio_sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    cache = tmp_path / "clip.alignment.json"
    # Two same-speaker blocks that bridge -> exercises context lines + a slide.
    words = [
        {"text": "Hello", "start": 0.0, "end": 0.15, "loss": 0.05, "speaker": "HOST"},
        {"text": "there", "start": 0.15, "end": 0.3, "loss": 0.05, "speaker": "HOST"},
        {"text": "friend.", "start": 0.3, "end": 0.45, "loss": 0.05, "speaker": "HOST"},
        {"text": "How", "start": 0.45, "end": 0.6, "loss": 0.05, "speaker": "HOST"},
        {"text": "are", "start": 0.6, "end": 0.75, "loss": 0.05, "speaker": "HOST"},
        {"text": "you?", "start": 0.75, "end": 0.95, "loss": 0.05, "speaker": "HOST"},
    ]
    promo_video.write_alignment_cache(cache, audio_sha, "ignored", words)

    out = tmp_path / "promo.mp4"
    rc = promo_video.main([
        str(wav), "-o", str(out), "--captions-json", str(cache),
        "--size", "64", "--fps", "6", "--format", fmt, "--caption-style", style,
    ])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 1000


# ---------------------------------------------------------------------------
# CLI default + swap legacy-path regression (adversarial-review coverage gap)
#
# Two contracts the rest of the suite left unpinned:
#   (a) the CLI's --caption-style default is "three-line"; and
#   (b) build_promo(style="swap") still takes *exactly* the pre-multiline
#       legacy path — gap-bridged cues, the 1.08x popped active font, the
#       single-line focus-tile renderer only, and never any context/window
#       (multiline) code.
# The pre-feature renderer is gone, so (b) is pinned by spying on the module
# seams rather than diffing a binary golden (ffmpeg/PIL vary per host, so a
# byte/hash comparison would be flaky). Every case is fully offline: the CLI
# test stubs build_promo; the render tests use the existing _fake_subprocess.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_argv, expected_style",
    [
        ([], "three-line"),                          # no --caption-style: the CLI default
        (["--caption-style", "swap"], "swap"),
        (["--caption-style", "two-line"], "two-line"),
        (["--caption-style", "three-line"], "three-line"),
    ],
)
def test_cli_caption_style_default_and_passthrough(tmp_path, monkeypatch, extra_argv, expected_style):
    # main() must hand build_promo the resolved caption style; with no
    # --caption-style on argv that resolved value is "three-line". build_promo
    # is stubbed to capture the kwarg so nothing renders (no ffmpeg, no PIL).
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"AUDIOBYTES")
    out = tmp_path / "out.mp4"
    captured = {}

    def fake_build_promo(*args, **kwargs):
        captured["style"] = kwargs.get("style")

    monkeypatch.setattr(promo_video, "build_promo", fake_build_promo)
    # No --transcript and no cache file -> resolve_caption_words returns None
    # (captions off), so main() never needs the network or an API key.
    rc = promo_video.main([str(audio), "-o", str(out), *extra_argv])
    assert rc == 0
    assert captured["style"] == expected_style


def _same_speaker_caption_words():
    """Six HOST words → two same-speaker blocks.

    Same-speaker adjacency is what makes the multiline styles show context
    (catch-up / read-ahead) lines and scroll, so this one fixture exercises the
    full divergence between the swap and three-line render paths.
    """
    return [
        {"text": "Hello", "start": 0.0, "end": 0.15, "loss": 0.05, "speaker": "HOST"},
        {"text": "there", "start": 0.15, "end": 0.3, "loss": 0.05, "speaker": "HOST"},
        {"text": "friend.", "start": 0.3, "end": 0.45, "loss": 0.05, "speaker": "HOST"},
        {"text": "How", "start": 0.45, "end": 0.6, "loss": 0.05, "speaker": "HOST"},
        {"text": "are", "start": 0.6, "end": 0.75, "loss": 0.05, "speaker": "HOST"},
        {"text": "you?", "start": 0.75, "end": 0.95, "loss": 0.05, "speaker": "HOST"},
    ]


def test_swap_render_takes_legacy_path_only(tmp_path, monkeypatch):
    # Offline render (ffmpeg faked) that pins the swap path to the legacy
    # pipeline by spying on the module seams: the cue bridge selection, the
    # planner output, the focus-tile renderer + the font it draws with, and the
    # context-tile renderer (which must never fire).
    captured = {}
    _fake_subprocess(monkeypatch, captured)

    bridges = []
    orig_cues = promo_video.compute_cues

    def cues_spy(blocks, window_end, bridge="gap"):
        bridges.append(bridge)
        return orig_cues(blocks, window_end, bridge)

    monkeypatch.setattr(promo_video, "compute_cues", cues_spy)

    plan_rows = []
    orig_plan = promo_video.plan_caption_lines

    def plan_spy(blocks, cues, style, t):
        plans = orig_plan(blocks, cues, style, t)
        plan_rows.append((style, plans))
        return plans

    monkeypatch.setattr(promo_video, "plan_caption_lines", plan_spy)

    focus = {"caption_font": None, "active_fonts": []}
    orig_focus = promo_video._render_caption_tile

    def focus_spy(block, active_idx, caption_font, active_font, baseline_y, center_x):
        focus["caption_font"] = caption_font
        focus["active_fonts"].append(active_font)
        return orig_focus(block, active_idx, caption_font, active_font, baseline_y, center_x)

    monkeypatch.setattr(promo_video, "_render_caption_tile", focus_spy)

    def context_boom(*a, **k):
        raise AssertionError("swap must never render a context/window tile")

    monkeypatch.setattr(promo_video, "_render_context_tile", context_boom)

    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")
    out = tmp_path / "out.mp4"
    # size 160 -> caption pt 10, popped pt 11: the 1.08x pop is genuinely larger.
    promo_video.build_promo(
        audio, out, size=160, fps=10, start=None, duration=None,
        title=None, root=REPO_ROOT, words=_same_speaker_caption_words(),
        fmt="square", style="swap",
    )

    # Cues were built in the legacy gap-bridge mode (never the speaker mode).
    assert bridges == ["gap"]
    # The legacy single-line focus tile ran (context_boom guarantees the
    # context renderer never did).
    assert focus["active_fonts"], "swap render produced no focus tiles"
    # The active (spoken) word draws with the 1.08x popped font — a distinct,
    # strictly larger font than the base caption font.
    base = focus["caption_font"]
    expected_active_pt = max(1, round(base.size * promo_video._CAPTION_ACTIVE_SCALE))
    for af in focus["active_fonts"]:
        assert af is not base
        assert af.size == expected_active_pt
        assert af.size > base.size  # the pop is real at this size
    # The planner only ever saw "swap" and only ever emitted a single focus line
    # at slot 0 (no catch-up/read-ahead, no fractional slide slot, full alpha).
    assert {style for style, _ in plan_rows} == {"swap"}
    for _, plans in plan_rows:
        assert len(plans) <= 1
        for bidx, kind, slot, alpha in plans:
            assert kind == "focus"
            assert slot == 0.0
            assert alpha == 1.0


def test_multiline_render_diverges_from_legacy_swap_path(tmp_path, monkeypatch):
    # The mirror of the swap test on the identical fixture: three-line must take
    # the *new* path, proving the swap assertions above are discriminating (not
    # vacuously true for every style). Bridges on speaker, renders context
    # tiles, and drops the pop (active word draws at the base caption font).
    captured = {}
    _fake_subprocess(monkeypatch, captured)

    bridges = []
    orig_cues = promo_video.compute_cues

    def cues_spy(blocks, window_end, bridge="gap"):
        bridges.append(bridge)
        return orig_cues(blocks, window_end, bridge)

    monkeypatch.setattr(promo_video, "compute_cues", cues_spy)

    context = {"n": 0}
    orig_context = promo_video._render_context_tile

    def context_spy(*a, **k):
        context["n"] += 1
        return orig_context(*a, **k)

    monkeypatch.setattr(promo_video, "_render_context_tile", context_spy)

    focus = {"caption_font": None, "active_fonts": []}
    orig_focus = promo_video._render_caption_tile

    def focus_spy(block, active_idx, caption_font, active_font, baseline_y, center_x):
        focus["caption_font"] = caption_font
        focus["active_fonts"].append(active_font)
        return orig_focus(block, active_idx, caption_font, active_font, baseline_y, center_x)

    monkeypatch.setattr(promo_video, "_render_caption_tile", focus_spy)

    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"")
    out = tmp_path / "out.mp4"
    promo_video.build_promo(
        audio, out, size=160, fps=10, start=None, duration=None,
        title=None, root=REPO_ROOT, words=_same_speaker_caption_words(),
        fmt="square", style="three-line",
    )

    # three-line bridges same-speaker neighbours on speaker (not gap)...
    assert bridges == ["speaker"]
    # ...renders the dimmed context (catch-up / read-ahead) tiles swap never does...
    assert context["n"] > 0
    # ...and drops the pop: the active word draws with the *base* caption font.
    base = focus["caption_font"]
    assert focus["active_fonts"], "three-line render produced no focus tiles"
    for af in focus["active_fonts"]:
        assert af is base
        assert af.size == base.size
