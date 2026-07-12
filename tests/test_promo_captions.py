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
        captured["body"] = req.data
        return _FakeResponse(b'{"words": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = promo_video.request_alignment(b"AUDIODATA", "hello world", "k-secret")
    assert out == b'{"words": []}'
    assert captured["url"] == "https://api.elevenlabs.io/v1/forced-alignment"
    assert captured["key"] == "k-secret"
    assert b"AUDIODATA" in captured["body"]
    assert b"hello world" in captured["body"]


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
    out = tmp_path / "promo.mp4"
    rc = promo_video.main([
        str(wav), "-o", str(out), "--captions-json", str(cache),
        "--size", "64", "--fps", "6", "--format", fmt, "--title", "Smoke",
    ])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 1000


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
