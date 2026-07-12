"""AI Agent FM — promo video ("Radiating Pulse") with word-synced captions.

Standalone CLI (sibling of ``publish.py``) that turns an audio clip into a
LinkedIn-ready MP4: the Spectrum Cone brand mark on a flat ink ground, its
radial arc gradient pulsing outward from the source point in sync with the
audio, the wordmark, and the audio muxed in. Optionally it burns in
word-synced captions beneath the cone (whole phrase in ivory, the spoken word
recolored per speaker — HOST amber / GUEST magenta — with a scale pop in the
single-line ``swap`` style, color-only in the multi-line styles; the scrolling
``three-line`` window is the default) and can render a vertical 9:16 master
alongside the default square.

    # one-time (sub-cent): fetch + cache word alignment for an episode
    uv run promo_video.py episodes/<ep>/episode.mp3 \
        --transcript episodes/<ep>/script.json --align-only

    # captioned square (or vertical) promo from a window of the episode
    uv run promo_video.py episodes/<ep>/episode.mp3 -o promo.mp4 \
        --transcript episodes/<ep>/script.json --start 63 --duration 45 \
        --title "How I built X" [--format vertical]

Design is final (see ``docs/design/promo-video-pulse.md`` and
``docs/design/promo-video-captions.md``); geometry and color come verbatim from
``docs/design/spectrum-cone-v2/BRAND-SPEC.md``. This module holds only
mechanics: audio-energy envelope math, gradient/mask rendering, caption block
timing, the forced-alignment client (raw urllib multipart, mirroring the
ElevenLabs TTS call in ``publish.py``) with its JSON cache, and a single-
process ffmpeg encode over a rawvideo stdin pipe. It imports the error taxonomy
from ``publish`` (and defines ``AlignmentError`` locally) so ``main()`` follows
the same contract: catch only ``AgentFMError``, print ``error: …`` to stderr,
exit 1. Pillow is imported lazily inside the functions that need it; the only
external tool is ffmpeg-on-PATH (already required by the repo). The alignment
API is the sole network touch and only runs to (re)build a missing cache.
"""

import argparse
import array
import hashlib
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from publish import AgentFMError, AudioError, ConfigError, EpisodeError, load_env


class AlignmentError(AgentFMError):
    """Raised when forced-alignment fetch, parsing, mapping, or caching fails."""

# ---------------------------------------------------------------------------
# Brand constants — copied verbatim from BRAND-SPEC.md. Never re-derive.
# ---------------------------------------------------------------------------

INK = (0x10, 0x12, 0x28)  # #101228 — the flat brand ground.
IVORY = (0xF4, 0xF0, 0xE6)  # #F4F0E6 — wordmark ink.

# Radial hold-and-blend "arc" gradient, centered on the source point. Each
# stop is (offset in [0,1] of the gradient radius, RGB). Five color holds and
# four linear crossfade zones, straight from BRAND-SPEC.md.
_GRADIENT_STOPS = (
    (0.000, (0xEF, 0x49, 0x38)),  # vermilion #ef4938
    (0.159, (0xEF, 0x49, 0x38)),
    (0.232, (0xF3, 0x9A, 0x2B)),  # amber #f39a2b
    (0.381, (0xF3, 0x9A, 0x2B)),
    (0.454, (0xC8, 0x3C, 0x88)),  # magenta #c83c88
    (0.586, (0xC8, 0x3C, 0x88)),
    (0.660, (0x70, 0x4F, 0xBC)),  # violet #704fbc
    (0.766, (0x70, 0x4F, 0xBC)),
    (0.839, (0x28, 0x5D, 0xB5)),  # cobalt #285db5
    (1.000, (0x28, 0x5D, 0xB5)),
)

# The gradient is centered on the tip-circle center; its radius is 546 units in
# the canonical 1024-unit brand canvas.
_TIP_ANCHOR = (512.0, 792.0)
_GRADIENT_RADIUS = 546.0
_TIP_CIRCLE_R = 30.0
_CANVAS = 1024.0  # canonical brand viewBox side.

# Inner clip path (ink-ground variant: inner path only, no keyline). Parsed
# from BRAND-SPEC.md's inner-clip SVG path. The two 14-unit corner quads are
# flattened at render time; the r=30 tip cap is drawn as an ellipse.
_TL = (233.5, 246.0)  # top-left, after M
_TR = (790.5, 246.0)  # top-right, after H
_TR_QUAD_CTRL = (804.5, 246.0)
_TR_QUAD_END = (797.6, 260.5)
_RIGHT_TIP = (539.1, 804.9)  # right side meets the tip circle
_LEFT_TIP = (484.9, 804.9)  # left side meets the tip circle
_LEFT_UP = (226.4, 260.5)
_TL_QUAD_CTRL = (219.5, 246.0)
_TL_QUAD_END = (233.5, 246.0)

# ---------------------------------------------------------------------------
# Composition constants (canvas space, S = --size). From the spec's table.
# ---------------------------------------------------------------------------

_MARK_BOX_FRAC = 0.62  # mark box width as a fraction of S.
_MARK_TOP_FRAC = 0.10  # mark box top edge.
_WORDMARK_CAP_FRAC = 0.048  # wordmark cap height.
_WORDMARK_TRACKING_EM = 0.10  # letter-spacing in em.
_WORDMARK_TOP_FRAC = 0.82
_TITLE_PT_FRAC = 0.030  # title font size.
_TITLE_MAX_WIDTH_FRAC = 0.86  # ellipsize past this width.
_TITLE_TOP_FRAC = 0.90
_TITLE_OPACITY = 0.70  # title reads at 70% ivory over the ink ground.

# ---------------------------------------------------------------------------
# Motion constants (from the spec's motion design).
# ---------------------------------------------------------------------------

_PULSE_GAIN = 0.06  # gradient scale g = 1 + 0.06·e about the tip anchor.
_BREATHE_GAIN = 0.015  # whole-mark scale m = 1 + 0.015·e about the bbox center.

_DECODE_RATE = 48000  # mono s16le decode rate for the energy envelope.
_ATTACK_TAU = 0.050  # one-pole attack time constant (s).
_RELEASE_TAU = 0.350  # one-pole release time constant (s).

# Sanctioned optimization: quantize e to this many gradient-layer scale steps
# and cache the rendered mark tile per level (visually indistinguishable given
# the smoothing, per the spec's performance note).
_QUANT_LEVELS = 32

# ---------------------------------------------------------------------------
# Caption + vertical-format constants (canvas space, S = --size). From the
# captions spec's composition tables and cue-timing formulas. Never re-derive.
# ---------------------------------------------------------------------------

# Vertical coordinates in the spec are given at this reference size and scale
# linearly by S / _VERTICAL_REF.
_VERTICAL_REF = 1080

# Corner wordmark shown whenever captions are on (~1/3 the centered size).
_CAPTION_WORDMARK_CAP_FRAC = 0.026  # cap height / S
_CAPTION_WORDMARK_LEFT_FRAC = 0.055  # square: left inset / S
_CAPTION_WORDMARK_TOP_FRAC = 0.05  # square: top inset / S
_VERTICAL_WORDMARK_XY = (84, 300)  # vertical: (left, top) at S = 1080

# Caption line typography (single line, centered on the canvas mid-x).
_CAPTION_PT_FRAC = 0.062  # base caption font size / S
_CAPTION_ACTIVE_SCALE = 1.08  # active word rendered at 1.08x the base pt
_CAPTION_BASELINE_FRAC = 0.76  # square: baseline y / S
_VERTICAL_CAPTION_BASELINE = 1160  # vertical: baseline y at S = 1080
_CAPTION_TILE_MARGIN_PX = 8  # fixed margin around each cropped caption tile

# Multi-line caption window (--caption-style two-line/three-line). Slots are
# indexed in pitch units from the focus baseline: baseline(slot) = focus +
# slot * pitch, with slot 0 = focus, -1 = catch-up above, +1 = read-ahead
# below. Context (prev/next) lines are a smaller, dimmed single color. Numbers
# verified against rendered frames in the caption-window delta spec — never
# re-derive.
_CAPTION_LINE_PITCH_FRAC = 0.078  # baseline-to-baseline slot pitch / S
_CAPTION_CONTEXT_SCALE = 0.85  # context-line font = 0.85x the base caption pt
_CAPTION_CONTEXT_OPACITY = 0.45  # context ivory pre-blended over ink (cf. title 0.70)
_CAPTION_SCROLL_DUR = 0.25  # s; cubic ease-out slide on a same-speaker advance
_VERTICAL_CAPTION_BASELINE_2LINE = 1120  # vertical focus baseline, two-line, at S = 1080
_VERTICAL_CAPTION_BASELINE_3LINE = 1104  # vertical focus baseline, three-line, at S = 1080

_CAPTION_IVORY = IVORY  # base phrase color #F4F0E6
_HOST_ACCENT = (0xF3, 0x9A, 0x2B)  # amber #f39a2b — HOST active word
_GUEST_ACCENT = (0xC8, 0x3C, 0x88)  # magenta #c83c88 — GUEST active word

# Block construction limits.
_CAPTION_MAX_WORDS = 5
_CAPTION_MAX_WIDTH_FRAC = 0.90  # max rendered block width / S, real font

# Characters that end a caption block. Sentence enders always break; the soft
# enders break only once the block already holds >= 3 words. Trailing closing
# quotes/brackets are ignored when checking the final character.
_SENTENCE_ENDERS = frozenset(".?!…—")  # . ? ! ellipsis em-dash
_SOFT_ENDERS = frozenset(",;:")
_TRAILING_PUNCT = "\"')]}»”’"  # " ' ) ] } » ” ’

# Cue-timing constants (seconds).
_CUE_LEAD = 0.05  # block in-cue fires 50 ms before its first word
_CUE_BRIDGE = 0.25  # gaps <= this are bridged (no caption flicker)
_HIGHLIGHT_LEAD = 0.075  # active word fires 75 ms before its spoken onset

# Forced-alignment API.
_FORCED_ALIGNMENT_URL = "https://api.elevenlabs.io/v1/forced-alignment"
_RETRY_SLEEPS = (1.0, 2.0)  # sleeps between the 3 total attempts
_ALIGNMENT_CACHE_VERSION = 1
_AUDIO_TAG_RE = re.compile(r"\[[^\]]*\]")  # [laughs]-style audio-tag spans


# ---------------------------------------------------------------------------
# Gradient color
# ---------------------------------------------------------------------------


def color_at_offset(t: float) -> tuple[int, int, int]:
    """Return the arc-gradient color at fractional radius ``t`` in ``[0, 1]``.

    Holds exact palette hexes inside a color hold and blends adjacent stops
    with linear RGB interpolation elsewhere. ``t`` is clamped to ``[0, 1]``.
    """
    if t <= 0.0:
        return _GRADIENT_STOPS[0][1]
    if t >= 1.0:
        return _GRADIENT_STOPS[-1][1]
    for (o0, c0), (o1, c1) in zip(_GRADIENT_STOPS, _GRADIENT_STOPS[1:]):
        if o0 <= t <= o1:
            if o1 == o0:
                return c0
            frac = (t - o0) / (o1 - o0)
            return (
                round(c0[0] + (c1[0] - c0[0]) * frac),
                round(c0[1] + (c1[1] - c0[1]) * frac),
                round(c0[2] + (c1[2] - c0[2]) * frac),
            )
    return _GRADIENT_STOPS[-1][1]  # unreachable given the clamps above


def color_at_radius(r_canonical: float) -> tuple[int, int, int]:
    """Return the arc-gradient color at canonical radius ``r_canonical`` units.

    The gradient spans ``_GRADIENT_RADIUS`` canonical units from the source
    point; beyond it the color clamps to the final cobalt hold.
    """
    return color_at_offset(r_canonical / _GRADIENT_RADIUS)


# ---------------------------------------------------------------------------
# Energy envelope (pure, deterministic — no subprocess, no I/O)
# ---------------------------------------------------------------------------


def frame_rms_series(samples, fps: int, rate: int = _DECODE_RATE) -> list[float]:
    """Compute per-frame RMS of ``samples`` (s16 ints) in ``[0, 1]`` amplitude.

    Each video frame takes the aligned window of ``rate // fps`` samples; the
    last partial window is zero-padded (the sum of squares is always divided by
    the full window length). Samples are normalized to ``[-1, 1]`` by 32768.
    """
    spf = max(1, round(rate / fps))
    n = len(samples)
    nframes = max(1, math.ceil(n / spf))
    out: list[float] = []
    for i in range(nframes):
        start = i * spf
        stop = min(n, start + spf)
        acc = 0.0
        for k in range(start, stop):
            v = samples[k] / 32768.0
            acc += v * v
        out.append(math.sqrt(acc / spf))
    return out


def smooth_asymmetric(
    series: list[float],
    fps: int,
    attack_tau: float = _ATTACK_TAU,
    release_tau: float = _RELEASE_TAU,
) -> list[float]:
    """Smooth ``series`` with an asymmetric one-pole filter.

    Uses the fast attack coefficient when the raw value rises above the
    smoothed value and the slow release coefficient otherwise, so pulses feel
    musical: ``α = 1 − exp(−1 / (fps·τ))``.
    """
    if not series:
        return []
    alpha_attack = 1.0 - math.exp(-1.0 / (fps * attack_tau))
    alpha_release = 1.0 - math.exp(-1.0 / (fps * release_tau))
    prev = series[0]
    out = [prev]
    for x in series[1:]:
        alpha = alpha_attack if x > prev else alpha_release
        prev = prev + alpha * (x - prev)
        out.append(prev)
    return out


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile ``p`` (0–100) of a sorted list."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = (p / 100.0) * (n - 1)
    lo = math.floor(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def normalize_envelope(series: list[float]) -> list[float]:
    """Map ``series`` to ``e ∈ [0, 1]`` via robust p5/p95 normalization.

    ``e = clamp((s − p5) / (p95 − p5), 0, 1)``. If ``p95 == p5`` (silence or a
    constant tone), every value maps to 0.
    """
    if not series:
        return []
    ordered = sorted(series)
    p5 = _percentile(ordered, 5.0)
    p95 = _percentile(ordered, 95.0)
    if p95 <= p5:
        return [0.0] * len(series)
    span = p95 - p5
    return [min(1.0, max(0.0, (x - p5) / span)) for x in series]


def compute_envelope(samples, fps: int, rate: int = _DECODE_RATE) -> list[float]:
    """Full RMS → smooth → normalize pipeline; returns one ``e`` per frame."""
    rms = frame_rms_series(samples, fps, rate)
    smoothed = smooth_asymmetric(rms, fps)
    return normalize_envelope(smoothed)


# ---------------------------------------------------------------------------
# Transcript loading + plain-text construction
# ---------------------------------------------------------------------------


def load_transcript(path) -> list[dict]:
    """Load and validate a promo ``script.json`` of ``{speaker, text}`` turns.

    A local loader (``publish.load_episode`` needs an ``episode.json`` sibling
    and must not be reused here). Raises ``EpisodeError`` naming the transcript
    path on a missing/unreadable file, invalid JSON, an empty ``turns`` list, a
    speaker outside ``HOST``/``GUEST``, or an empty text field.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EpisodeError(f"transcript not found: {path}")
    except UnicodeDecodeError as exc:
        raise EpisodeError(
            f"transcript {path} is not valid UTF-8 text: {exc}"
        ) from exc
    except OSError as exc:
        raise EpisodeError(f"could not read transcript {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EpisodeError(f"transcript {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EpisodeError(f"transcript {path} must contain a JSON object")
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise EpisodeError(f"transcript {path} must have a non-empty 'turns' list")
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise EpisodeError(f"transcript {path} turn {i} is not an object")
        if turn.get("speaker") not in ("HOST", "GUEST"):
            raise EpisodeError(
                f"transcript {path} turn {i} has invalid speaker "
                f"{turn.get('speaker')!r}; must be HOST or GUEST"
            )
        text_val = turn.get("text")
        if not isinstance(text_val, str) or not text_val.strip():
            raise EpisodeError(f"transcript {path} turn {i} has empty text")
    return turns


def build_transcript_text(turns: list[dict]) -> str:
    """Build the plain transcript the alignment API is given.

    Joins turn texts in order with single spaces, strips ``[...]`` audio-tag
    spans (so ``[laughs]`` never appears in the aligned text and never shows in
    a caption), and collapses whitespace runs. No speaker labels, no JSON.
    """
    parts = [_AUDIO_TAG_RE.sub(" ", t["text"]) for t in turns]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _normalize_text(text: str) -> str:
    """Unicode NFKC + casefold — the comparison form for speaker mapping."""
    return unicodedata.normalize("NFKC", text).casefold()


def attribute_speakers(api_words: list[dict], turns: list[dict]) -> list[str]:
    """Map each API word to its source speaker by a character-consumption walk.

    Does not assume the API tokenizes like we do. Both sides reduce to the same
    NFKC+casefold character stream (whitespace removed); each API word consumes
    the next characters of the source stream, and its speaker is the speaker of
    the source span it consumed. Robust to the API splitting ``don't``,
    ``co-founder``, ``3.14``, ``20%``, quotes, or ellipses differently.

    Raises ``AlignmentError`` (naming the API word index/text and the source
    token index/text) on any character divergence, a word straddling a speaker
    change, or leftover characters on either side.
    """
    tokens: list[tuple[str, str]] = []  # (source token text, speaker)
    for turn in turns:
        stripped = _AUDIO_TAG_RE.sub(" ", turn["text"])
        for tok in stripped.split():
            tokens.append((tok, turn["speaker"]))

    src_chars: list[str] = []
    src_speaker: list[str] = []
    src_tok: list[int] = []
    for idx, (tok, speaker) in enumerate(tokens):
        for ch in _normalize_text(tok):
            src_chars.append(ch)
            src_speaker.append(speaker)
            src_tok.append(idx)

    speakers: list[str] = []
    pos = 0
    for j, word in enumerate(api_words):
        wtext = word["text"] if isinstance(word, dict) else word
        norm = _normalize_text("".join(wtext.split()))
        start = pos
        for ch in norm:
            if pos >= len(src_chars):
                raise AlignmentError(
                    f"alignment mismatch: API word {j} '{wtext}' runs past the "
                    f"end of the transcript character stream — the audio and "
                    f"script.json disagree; check the transcript, then re-fetch "
                    f"with --refresh-alignment."
                )
            if src_chars[pos] != ch:
                ti = src_tok[pos]
                raise AlignmentError(
                    f"alignment mismatch: API word {j} '{wtext}' diverges from "
                    f"source token {ti} '{tokens[ti][0]}' (API char '{ch}' vs "
                    f"source char '{src_chars[pos]}')."
                )
            pos += 1
        if pos > start:
            span = set(src_speaker[start:pos])
            if len(span) > 1:
                raise AlignmentError(
                    f"alignment word {j} '{wtext}' straddles a speaker change — "
                    f"words should never cross a HOST/GUEST boundary."
                )
            speakers.append(src_speaker[start])
        elif start < len(src_speaker):
            speakers.append(src_speaker[start])  # empty word: borrow next speaker
        elif src_speaker:
            speakers.append(src_speaker[-1])
        else:
            raise AlignmentError(f"alignment word {j} '{wtext}' has no source text")

    if pos < len(src_chars):
        ti = src_tok[pos]
        remainder = "".join(src_chars[pos:])
        raise AlignmentError(
            f"alignment mismatch: {len(src_chars) - pos} source characters left "
            f"unconsumed starting at token {ti} '{tokens[ti][0]}' (remainder "
            f"'{remainder[:40]}') — the transcript and audio disagree; re-fetch "
            f"with --refresh-alignment."
        )
    return speakers


# ---------------------------------------------------------------------------
# Forced-alignment client (the only network touch) + response parsing
# ---------------------------------------------------------------------------


def _encode_multipart(fields: dict, file_field: tuple, boundary: str) -> bytes:
    """Build a ``multipart/form-data`` body by hand (stdlib only).

    ``fields`` are text parts; ``file_field`` is ``(name, filename, bytes,
    content_type)``. Mirrors the hand-rolled request style of the ElevenLabs
    TTS call in ``publish.py`` (no third-party multipart dependency).
    """
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
        parts.append(b"")
        parts.append(value.encode("utf-8"))
    name, filename, data, content_type = file_field
    parts.append(f"--{boundary}".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode(
            "utf-8"
        )
    )
    parts.append(f"Content-Type: {content_type}".encode("utf-8"))
    parts.append(b"")
    parts.append(data)
    parts.append(f"--{boundary}--".encode("utf-8"))
    parts.append(b"")
    return b"\r\n".join(parts)


def _is_missing_permissions(body: bytes) -> bool:
    """True if a 401 body carries ``detail.status == "missing_permissions"``."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return False
    detail = data.get("detail") if isinstance(data, dict) else None
    return isinstance(detail, dict) and detail.get("status") == "missing_permissions"


def request_alignment(
    audio_bytes: bytes, transcript_text: str, api_key: str, sleep=time.sleep
) -> bytes:
    """POST the audio + plain transcript to the Forced Alignment API.

    Multipart body: ``file`` = the whole audio bytes, ``text`` = the tag-
    stripped transcript. Three attempts total, sleeping 1 s then 2 s between
    them; retries on HTTP 429, any 5xx, and transient ``URLError``. Any other
    HTTP status fails immediately. Every final failure is wrapped in
    ``AlignmentError`` (status/reason + hint; the API key is never echoed).
    Called through the module-level ``urllib.request.urlopen`` unaliased so
    tests can monkeypatch it, exactly like ``publish.elevenlabs_tts``.
    """
    boundary = "----agentfm-" + uuid.uuid4().hex
    body = _encode_multipart(
        {"text": transcript_text},
        ("file", "audio", audio_bytes, "application/octet-stream"),
        boundary,
    )
    headers = {
        "xi-api-key": api_key,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    # Python 3.13 turned on VERIFY_X509_STRICT by default, which rejects some
    # corporate-proxy CAs. Clear just the strict bit — verification stays on
    # (copied from publish.py's ElevenLabs call; do not "clean up").
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

    attempts = 3
    for attempt in range(attempts):
        req = urllib.request.Request(
            _FORCED_ALIGNMENT_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read()
            except Exception:  # noqa: BLE001 — body already consumed / closed
                detail = b""
            status = exc.code
            if status == 401 and _is_missing_permissions(detail):
                raise AlignmentError(
                    "forced alignment failed: HTTP 401 — your ELEVENLABS_API_KEY "
                    "lacks the Forced Alignment permission. Enable it for this "
                    "key in the ElevenLabs dashboard (Profile → API Keys → edit "
                    "permissions) and retry."
                ) from exc
            retryable = status == 429 or 500 <= status < 600
            if retryable and attempt < attempts - 1:
                sleep(_RETRY_SLEEPS[attempt])
                continue
            raise AlignmentError(
                f"forced alignment failed: HTTP {status} {exc.reason} — check "
                f"your ELEVENLABS_API_KEY and the audio/transcript, then retry "
                f"(or render from an existing cache via --captions-json)."
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            # A bare timeout — notably during response.read() — is transient and
            # is neither an HTTPError nor a URLError (socket.timeout is an alias
            # of TimeoutError on 3.10+). Retry under the same policy and wrap the
            # final failure so it honors the AgentFMError contract.
            if attempt < attempts - 1:
                sleep(_RETRY_SLEEPS[attempt])
                continue
            raise AlignmentError(
                "forced alignment timed out — check your connection and retry, "
                "or render from an existing cached alignment JSON via "
                "--captions-json."
            ) from exc
        except urllib.error.URLError as exc:
            if attempt < attempts - 1:
                sleep(_RETRY_SLEEPS[attempt])
                continue
            raise AlignmentError(
                f"forced alignment network error: {exc.reason} — check your "
                f"connection and retry, or render from an existing cached "
                f"alignment JSON via --captions-json."
            ) from exc
    raise AlignmentError("forced alignment failed after all retries")  # unreachable


def parse_alignment_words(raw) -> list[dict]:
    """Parse + structurally validate the API response into ``words``.

    Raises ``AlignmentError`` if the body is not JSON, has no list ``words``,
    is empty, or holds a word with a non-string ``text``; a missing or
    non-numeric/non-finite ``start``, ``end``, or ``loss``; times violating
    ``0 <= start <= end``; or a ``start`` earlier than the previous word (starts
    must be non-decreasing — the same rules ``load_alignment_cache`` applies, so
    a fresh response that renders once can never be rejected on reload). Returns
    a list of ``{text, start, end, loss}`` (speaker is attached later).
    """
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        raise AlignmentError(f"forced alignment response was not valid JSON: {exc}")
    if not isinstance(data, dict) or "words" not in data:
        raise AlignmentError("forced alignment response has no 'words' field")
    words = data["words"]
    if not isinstance(words, list):
        raise AlignmentError("forced alignment 'words' field is not a list")
    if not words:
        raise AlignmentError("forced alignment returned an empty 'words' list")
    out: list[dict] = []
    prev_start = None
    for i, w in enumerate(words):
        if not isinstance(w, dict):
            raise AlignmentError(f"forced alignment word {i} is not an object")
        text = w.get("text")
        if not isinstance(text, str):
            raise AlignmentError(f"forced alignment word {i} has no text string")
        start = w.get("start")
        end = w.get("end")
        loss = w.get("loss")
        for label, value in (("start", start), ("end", end), ("loss", loss)):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise AlignmentError(
                    f"forced alignment word {i} has a missing or non-numeric {label}"
                )
        if start < 0 or end < start:
            raise AlignmentError(
                f"forced alignment word {i} has invalid times "
                f"start={start} end={end}"
            )
        if prev_start is not None and start < prev_start:
            raise AlignmentError(
                f"forced alignment word {i} starts before the previous word "
                f"(start={start} < {prev_start}); starts must be non-decreasing"
            )
        prev_start = start
        out.append(
            {"text": text, "start": float(start), "end": float(end), "loss": float(loss)}
        )
    return out


# ---------------------------------------------------------------------------
# Alignment cache (write / validate-on-load)
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_audio_bytes(path) -> bytes:
    """Read the whole audio file, raising ``AlignmentError`` naming the path."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise AlignmentError(
            f"could not read audio for alignment: {path} ({exc})"
        ) from exc


def write_alignment_cache(path, audio_sha: str, transcript_sha, words: list[dict]) -> None:
    """Write the alignment cache JSON, raising ``AlignmentError`` on failure."""
    payload = {
        "version": _ALIGNMENT_CACHE_VERSION,
        "audio_sha256": audio_sha,
        "transcript_sha256": transcript_sha,
        "words": words,
    }
    try:
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        raise AlignmentError(
            f"could not write alignment cache to {path}: {exc}"
        ) from exc


def load_alignment_cache(path, audio_sha: str, transcript_sha=None) -> list[dict]:
    """Load + validate the alignment cache, returning its ``words``.

    Validates: JSON object; ``version == 1``; ``audio_sha256`` matches the
    current audio (and ``transcript_sha256`` when a transcript is given); a
    non-empty ``words`` list; every word has non-empty ``text``, finite
    ``start``/``end`` with ``0 <= start <= end``, non-decreasing ``start``, and
    ``speaker in {HOST, GUEST}``. Any failure raises ``AlignmentError`` naming
    the cache path and pointing at ``--refresh-alignment`` — never a silent
    re-fetch.
    """
    path = Path(path)
    hint = f"pass --refresh-alignment (or delete {path}) to rebuild it"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AlignmentError(
            f"alignment cache {path} is not valid UTF-8 text ({exc}) — {hint}"
        )
    except OSError as exc:
        raise AlignmentError(f"could not read alignment cache {path}: {exc} — {hint}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AlignmentError(
            f"alignment cache {path} is corrupt (invalid JSON: {exc}) — {hint}"
        )
    if not isinstance(data, dict):
        raise AlignmentError(f"alignment cache {path} is not a JSON object — {hint}")
    if data.get("version") != _ALIGNMENT_CACHE_VERSION:
        raise AlignmentError(
            f"alignment cache {path} has unsupported version "
            f"{data.get('version')!r} (expected {_ALIGNMENT_CACHE_VERSION}) — {hint}"
        )
    if data.get("audio_sha256") != audio_sha:
        raise AlignmentError(
            f"alignment cache {path} is stale: it was built for a different "
            f"audio file — {hint}"
        )
    if transcript_sha is not None and data.get("transcript_sha256") != transcript_sha:
        raise AlignmentError(
            f"alignment cache {path} is stale: the transcript has changed since "
            f"it was built — {hint}"
        )
    words = data.get("words")
    if not isinstance(words, list) or not words:
        raise AlignmentError(
            f"alignment cache {path} has an empty or invalid 'words' list — {hint}"
        )
    prev_start = None
    for i, w in enumerate(words):
        if not isinstance(w, dict):
            raise AlignmentError(f"alignment cache {path} word {i} is not an object — {hint}")
        if not isinstance(w.get("text"), str) or not w["text"]:
            raise AlignmentError(f"alignment cache {path} word {i} has empty text — {hint}")
        start = w.get("start")
        end = w.get("end")
        for label, value in (("start", start), ("end", end)):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise AlignmentError(
                    f"alignment cache {path} word {i} has a non-finite {label} — {hint}"
                )
        if not (0.0 <= start <= end):
            raise AlignmentError(
                f"alignment cache {path} word {i} has invalid times "
                f"start={start} end={end} — {hint}"
            )
        if prev_start is not None and start < prev_start:
            raise AlignmentError(
                f"alignment cache {path} word {i} starts before the previous "
                f"word (starts must be non-decreasing) — {hint}"
            )
        prev_start = start
        if w.get("speaker") not in ("HOST", "GUEST"):
            raise AlignmentError(
                f"alignment cache {path} word {i} has invalid speaker "
                f"{w.get('speaker')!r} — {hint}"
            )
    return words


def resolve_caption_words(audio_path, cache_path, transcript_path, refresh: bool):
    """Return the full cached word list for captions, building the cache if needed.

    Returns ``None`` when captions are off (no transcript and no cache file).
    Otherwise loads a valid cache (no network), or — with a transcript and a
    missing/``--refresh``-ed cache — fetches alignment, attributes speakers,
    writes the cache, and returns the words. Raises ``ConfigError`` when a fetch
    is required but ``ELEVENLABS_API_KEY`` is unset with no usable cache.
    """
    audio_path = Path(audio_path)
    cache_path = Path(cache_path)
    has_transcript = transcript_path is not None
    if not has_transcript and not cache_path.exists():
        return None  # captions off — no usable alignment source

    transcript_turns = transcript_text = transcript_sha = None
    if has_transcript:
        transcript_turns = load_transcript(transcript_path)
        transcript_text = build_transcript_text(transcript_turns)
        transcript_sha = _sha256_text(transcript_text)

    audio_bytes = _read_audio_bytes(audio_path)
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()

    if cache_path.exists() and not refresh:
        return load_alignment_cache(cache_path, audio_sha, transcript_sha)

    if not has_transcript:
        raise AlignmentError(
            f"no alignment cache at {cache_path} — pass --transcript to build one"
        )

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise ConfigError(
            "ELEVENLABS_API_KEY is not set — add it to your .env file to fetch "
            "word alignment, or point --captions-json at an existing valid "
            "alignment JSON cache to render offline."
        )

    raw = request_alignment(audio_bytes, transcript_text, api_key)
    api_words = parse_alignment_words(raw)
    speakers = attribute_speakers(api_words, transcript_turns)
    # Drop empty/whitespace-only API words before caching or rendering. The
    # attribution walk tolerates them (its borrow branch assigns a speaker
    # without consuming characters), but they render as invisible caption
    # "words" and — because load_alignment_cache rejects empty text — would
    # poison the cache: a fetched empty word renders once, then every reload
    # fails validation and --refresh-alignment just re-fetches the same poison.
    words = [
        {
            "text": w["text"],
            "start": w["start"],
            "end": w["end"],
            "loss": w["loss"],
            "speaker": sp,
        }
        for w, sp in zip(api_words, speakers)
        if w["text"].strip()
    ]
    write_alignment_cache(cache_path, audio_sha, transcript_sha, words)
    return words


# ---------------------------------------------------------------------------
# Caption block model (pure — window math, block build, cue timing)
# ---------------------------------------------------------------------------


def clip_words_to_window(words: list[dict], w0: float, w1: float) -> list[dict]:
    """Keep words whose ``[start, end)`` overlaps ``[w0, w1)``, shifted to 0.

    Kept words are copied with times shifted by ``-w0`` and clamped to
    ``[0, w1 - w0]`` (so boundary-straddling words are trimmed to the window,
    never dropped). The returned list is ordered like the input.
    """
    length = w1 - w0
    out: list[dict] = []
    for w in words:
        if w["start"] < w1 and w["end"] > w0:  # half-open overlap
            ns = min(max(w["start"] - w0, 0.0), length)
            ne = min(max(w["end"] - w0, 0.0), length)
            out.append({**w, "start": ns, "end": ne})
    return out


def _ends_block(text: str, word_count: int) -> bool:
    """True if a word carrying ``text`` should close the current block.

    Sentence enders (``. ? ! … —``) always close it; soft enders (``, ; :``)
    close it only once the block already holds >= 3 words. Trailing closing
    quotes/brackets are ignored before inspecting the final character.
    """
    core = text.rstrip(_TRAILING_PUNCT)
    if not core:
        return False
    last = core[-1]
    if last in _SENTENCE_ENDERS:
        return True
    return last in _SOFT_ENDERS and word_count >= 3


def build_blocks(
    words: list[dict], measure, max_width: float, max_words: int = _CAPTION_MAX_WORDS
) -> list[list[dict]]:
    """Group ordered (clipped) words into caption blocks.

    A block never spans a speaker change; ends after a word carrying sentence
    punctuation or an em-dash (or a comma/semicolon/colon once it has >= 3
    words); and never exceeds ``max_words`` words or ``max_width`` rendered
    px. ``measure(text)`` returns the rendered width of the space-joined block
    text at caption size (injected so tests need no font); width or word count,
    whichever bites first, forces the break.
    """
    blocks: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            joined = " ".join(w["text"] for w in current + [word])
            if (
                word["speaker"] != current[-1]["speaker"]
                or len(current) >= max_words
                or measure(joined) > max_width
            ):
                blocks.append(current)
                current = []
        current.append(word)
        if _ends_block(word["text"], len(current)):
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_cues(
    blocks: list[list[dict]], window_end: float, bridge: str = "gap"
) -> list[tuple]:
    """Return ``(in_cue, out_cue)`` seconds for each block, never overlapping.

    The first block's in-cue is ``max(first.start - 0.05, 0)``; every in-cue is
    ``>=`` the previous out-cue (no overlap); all cues are clamped to
    ``[0, window_end]``. Only the out-cue *bridge* rule between neighbours
    depends on ``bridge``:

    - ``"gap"`` (default — today's rule, byte-identical): a gap <= 0.25 s
      bridges (the earlier caption stays up until the next appears, so short
      pauses never flicker) while a genuine silence lets the caption clear at
      its last word's end. Speaker is irrelevant.
    - ``"speaker"`` (the ``two-line``/``three-line`` window styles): same-speaker
      neighbours ALWAYS bridge regardless of gap (the earlier window holds
      through the pause, then slides — the planner's contiguity test then reads
      every intra-turn advance as a scroll); different-speaker neighbours NEVER
      bridge (out-cue = the last word's end), so a turn change always flushes
      the window and leaves whatever blank the audio gives.

    Leads, clamps, and the ``in-cue = max(early_next, prev out)`` rule are the
    same in both modes.
    """
    n = len(blocks)
    if n == 0:
        return []
    in_cues = [0.0] * n
    out_cues = [0.0] * n
    in_cues[0] = _clamp(blocks[0][0]["start"] - _CUE_LEAD, 0.0, window_end)
    for i in range(n):
        last_end = blocks[i][-1]["end"]
        if i < n - 1:
            early_next = blocks[i + 1][0]["start"] - _CUE_LEAD
            if bridge == "speaker":
                bridged = _same_speaker(blocks, i, i + 1)
            else:
                bridged = (early_next - last_end) <= _CUE_BRIDGE
            provisional = early_next if bridged else last_end
            out_cues[i] = _clamp(max(provisional, in_cues[i]), 0.0, window_end)
            in_cues[i + 1] = _clamp(max(early_next, out_cues[i]), 0.0, window_end)
        else:
            out_cues[i] = _clamp(max(last_end, in_cues[i]), 0.0, window_end)
    return list(zip(in_cues, out_cues))


def active_word_index(block: list[dict], t: float) -> int:
    """Index of the lit word in a live ``block`` at window-local time ``t``.

    The last word whose ``start - 0.075`` has passed (75 ms early-fire lead);
    it stays lit through intra-block pauses until the next word fires. Defaults
    to the first word (a live block always has one word past its lead).
    """
    idx = 0
    for i, w in enumerate(block):
        if w["start"] - _HIGHLIGHT_LEAD <= t:
            idx = i
        else:
            break
    return idx


def _live_block(cues: list[tuple], t: float):
    """Index of the block live at time ``t`` (``in_cue <= t < out_cue``), or None."""
    for i, (in_cue, out_cue) in enumerate(cues):
        if in_cue <= t < out_cue:
            return i
    return None


# ---------------------------------------------------------------------------
# Multi-line caption window planner (pure — blocks + cues + style + t -> lines)
# ---------------------------------------------------------------------------


def _ease_out_cubic(u: float) -> float:
    """Cubic ease-out ``e(u) = 1 - (1 - u)**3`` over ``u`` in ``[0, 1]``."""
    return 1.0 - (1.0 - u) ** 3


def _block_speaker(block: list[dict]) -> str:
    """Speaker of a block (a block never spans a speaker change, so any word)."""
    return block[0]["speaker"]


def _same_speaker(blocks: list, a: int, b: int) -> bool:
    """True if blocks ``a`` and ``b`` both exist and share a speaker."""
    if not (0 <= a < len(blocks) and 0 <= b < len(blocks)):
        return False
    return _block_speaker(blocks[a]) == _block_speaker(blocks[b])


def _bridged(cues: list[tuple], i: int) -> bool:
    """contiguous(i): block ``i`` bridges from ``i-1`` (out_cue[i-1] == in_cue[i])."""
    return i > 0 and cues[i - 1][1] == cues[i][0]


def _next_shown(blocks: list, i: int) -> bool:
    """Whether block ``i+1`` shows as the read-ahead line of block ``i``."""
    return _same_speaker(blocks, i, i + 1)


def _prev_shown(blocks: list, cues: list[tuple], i: int) -> bool:
    """Whether block ``i-1`` shows as the catch-up line of block ``i``.

    Requires the bridge from ``i-1`` to have fired (no dark gap) *and* the two
    blocks to share a speaker — so a speaker turn or a post-silence restart
    flushes the catch-up line.
    """
    return _bridged(cues, i) and _same_speaker(blocks, i, i - 1)


def plan_caption_lines(blocks: list, cues: list[tuple], style: str, t: float) -> list[tuple]:
    """Plan the caption window's lines at window-local time ``t`` (pure).

    Returns a list of ``(block_index, kind, slot, alpha)`` where ``kind`` is
    ``"focus"`` or ``"context"``, ``slot`` is the baseline offset in pitch units
    (0 = focus, -1 = catch-up above, +1 = read-ahead below; fractional while a
    slide is in flight), and ``alpha`` in ``[0, 1]`` scales the pasted tile.
    Returns ``[]`` when no block is live. ``swap`` always emits exactly one
    ``(i, "focus", 0, 1)`` — today's behavior. ``two-line``/``three-line``
    resolve the window content from the next/prev rules and, on a contiguous
    same-speaker block advance, the cubic ease-out scroll (the Apple-Music-
    lyrics "up and up"). Fonts, Pillow, and ffmpeg are never touched, so every
    case is a direct unit test.
    """
    i = _live_block(cues, t)
    if i is None:
        return []
    if style == "swap":
        return [(i, "focus", 0.0, 1.0)]

    in_i, out_i = cues[i]
    prev_same = _prev_shown(blocks, cues, i)
    dur = min(_CAPTION_SCROLL_DUR, out_i - in_i)
    sliding = prev_same and dur > 0.0 and t < in_i + dur

    if sliding:
        u = _clamp((t - in_i) / dur, 0.0, 1.0)
        rise = 1.0 - _ease_out_cubic(u)  # shared slot offset of the sliding stack
        e = _ease_out_cubic(u)
        plans: list[tuple] = []
        # Old prev (block i-2) leaves the top, fading — three-line only, and
        # only if it was the catch-up line of block i-1.
        if style == "three-line" and _prev_shown(blocks, cues, i - 1):
            plans.append((i - 2, "context", -2.0 + rise, rise))
        # Old focus (block i-1) rises into the catch-up slot.
        if style == "three-line":
            plans.append((i - 1, "context", -1.0 + rise, 1.0))
        else:  # two-line: the old focus leaves entirely, fading.
            plans.append((i - 1, "context", -1.0 + rise, rise))
        # New next (block i+1) fades in in place at slot +1 (no slide-in — a
        # slide from below would cross the title band).
        if _next_shown(blocks, i):
            plans.append((i + 1, "context", 1.0, e))
        # New focus (block i) rises from +1 to 0; kept last so it draws on top.
        plans.append((i, "focus", 0.0 + rise, 1.0))
        return plans

    # Steady state: prev (three-line), focus, next in that order.
    plans = []
    if style == "three-line" and prev_same:
        plans.append((i - 1, "context", -1.0, 1.0))
    plans.append((i, "focus", 0.0, 1.0))
    if _next_shown(blocks, i):
        plans.append((i + 1, "context", 1.0, 1.0))
    return plans


# ---------------------------------------------------------------------------
# Cone mask (inner clip shape, ink-ground variant)
# ---------------------------------------------------------------------------


def _quad_points(p0, p1, p2, segments: int) -> list[tuple[float, float]]:
    """Flatten a quadratic Bezier ``p0→p2`` (control ``p1``) to a polyline.

    Returns ``segments`` points for ``t`` in ``(0, 1]`` (the start ``p0`` is
    assumed already present in the caller's polygon).
    """
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        u = 1.0 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def _cone_polygon(segments: int = 8) -> list[tuple[float, float]]:
    """Canonical (1024-space) polygon of the inner clip outline.

    Top edge + both corner quads (flattened) + the two straight sides down to
    where they meet the r=30 tip circle. The bottom cap is left as a chord
    between the two tip points; the caller unions an ellipse for the tip.
    """
    poly: list[tuple[float, float]] = [_TL, _TR]
    poly += _quad_points(_TR, _TR_QUAD_CTRL, _TR_QUAD_END, segments)
    poly.append(_RIGHT_TIP)
    poly.append(_LEFT_TIP)  # chord across the tip; ellipse fills the cap
    poly.append(_LEFT_UP)
    poly += _quad_points(_LEFT_UP, _TL_QUAD_CTRL, _TL_QUAD_END, segments)
    return poly


def render_cone_mask(
    size: int,
    scale: float,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    supersample: int = 4,
) -> "object":
    """Render the inner-clip alpha mask as an ``L`` image of side ``size``.

    Canonical point ``(x, y)`` maps to pixel
    ``((x − origin_x)·scale, (y − origin_y)·scale)``. The shape is the union of
    the flattened outline polygon and the r=30 tip circle. Rendered at
    ``supersample``× and LANCZOS-downsampled for clean anti-aliased edges.
    """
    from PIL import Image, ImageDraw

    ss = supersample
    big = size * ss
    img = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(img)

    def tx(p):
        return ((p[0] - origin_x) * scale * ss, (p[1] - origin_y) * scale * ss)

    draw.polygon([tx(p) for p in _cone_polygon()], fill=255)

    cx, cy = _TIP_ANCHOR
    r = _TIP_CIRCLE_R
    x0, y0 = tx((cx - r, cy - r))
    x1, y1 = tx((cx + r, cy + r))
    draw.ellipse([x0, y0, x1, y1], fill=255)

    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Gradient layer + per-frame mark rendering
# ---------------------------------------------------------------------------


def build_gradient_layer(mark_px: int, anchor: tuple[float, float]) -> "object":
    """Render the resting arc gradient as an ``RGB`` image of side ``mark_px``.

    Built by drawing concentric 1-px filled circles from the outside in, each
    colored by ``color_at_radius``. Because ``g = 1 + 0.06·e ≥ 1`` only ever
    scales the layer up about the interior anchor, a mark-box-sized layer never
    exposes an edge under the pulse; the circle radius still oversteps the box
    corners by a comfortable margin.
    """
    from PIL import Image, ImageDraw

    scale = mark_px / _CANVAS
    ax, ay = anchor
    img = Image.new("RGB", (mark_px, mark_px), _GRADIENT_STOPS[-1][1])
    draw = ImageDraw.Draw(img)
    # Cover every corner from the anchor, then oversize ~1.3× for safety.
    reach = math.hypot(max(ax, mark_px - ax), max(ay, mark_px - ay))
    r_max = int(math.ceil(reach * 1.3)) + 2
    for r_px in range(r_max, 0, -1):
        color = color_at_radius(r_px / scale)
        draw.ellipse([ax - r_px, ay - r_px, ax + r_px, ay + r_px], fill=color)
    draw.point((ax, ay), fill=color_at_radius(0.0))
    return img


def _scale_about_anchor(layer, g: float, anchor, mark_px: int):
    """Scale ``layer`` by ``g`` about ``anchor``, cropped to a ``mark_px`` box.

    Resizes the whole layer by ``g`` then pastes it so the anchor stays fixed —
    the gradient seams radiate outward while the anchor (source point) holds.
    """
    from PIL import Image

    if g == 1.0:
        return layer
    new = max(1, round(mark_px * g))
    resized = layer.resize((new, new), Image.LANCZOS)
    ax, ay = anchor
    off_x = round(ax - ax * g)
    off_y = round(ay - ay * g)
    canvas = Image.new("RGB", (mark_px, mark_px), _GRADIENT_STOPS[-1][1])
    canvas.paste(resized, (off_x, off_y))
    return canvas


def _breathe(tile, m: float, center, mark_px: int):
    """Scale RGBA ``tile`` by ``m`` about ``center``; return (image, offset).

    ``offset`` is the paste displacement (relative to the mark box top-left)
    that keeps ``center`` fixed, so the whole mark breathes about its own
    bounding-box center without the silhouette drifting.
    """
    from PIL import Image

    if m == 1.0:
        return tile, (0, 0)
    new = max(1, round(mark_px * m))
    scaled = tile.resize((new, new), Image.LANCZOS)
    off_x = round(center[0] - center[0] * m)
    off_y = round(center[1] - center[1] * m)
    return scaled, (off_x, off_y)


def _render_mark_tile(e: float, layer, mask, anchor, center, mark_px: int):
    """Render the masked, pulsing, breathing mark for energy ``e``.

    Returns ``(rgba_tile, (offset_x, offset_y))`` — the tile carries alpha from
    the fixed cone mask; only the gradient fill moves under it.
    """
    g = 1.0 + _PULSE_GAIN * e
    grad = _scale_about_anchor(layer, g, anchor, mark_px).copy()
    grad.putalpha(mask)
    m = 1.0 + _BREATHE_GAIN * e
    return _breathe(grad, m, center, mark_px)


# ---------------------------------------------------------------------------
# Typography (copied tracked-text logic from publish.py — not imported)
# ---------------------------------------------------------------------------


def _draw_tracked(draw, text, font, tracking, center_x, top, fill):
    """Draw ``text`` horizontally centered at ``center_x`` with letter spacing.

    Copied from publish.py per the spec (do not import the private helper).
    """
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = center_x - total / 2
    for ch, width in zip(text, widths):
        draw.text((x, top), ch, font=font, fill=fill)
        x += width + tracking


def _draw_tracked_left(draw, text, font, tracking, left, top, fill):
    """Draw ``text`` left-aligned from ``left`` with letter spacing.

    The corner wordmark (captions-on) hugs the left inset instead of centering.
    """
    x = left
    for ch in text:
        draw.text((x, top), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def _promo_font_path(root: Path) -> Path:
    """Return the committed Space Grotesk Bold path or raise ``ConfigError``."""
    font_path = root / "artwork" / "fonts" / "SpaceGrotesk-Bold.ttf"
    if not font_path.exists():
        raise ConfigError(f"promo font missing: {font_path} (SpaceGrotesk-Bold.ttf)")
    return font_path


def _vertical_height(size: int) -> int:
    """9:16 height for width ``size``: ``even(round(16·size/9))`` for yuv420p."""
    h = round(16 * size / 9)
    return h + 1 if h % 2 else h


def _canvas_dims(size: int, fmt: str) -> tuple[int, int, int]:
    """Return ``(width, height, core_top)`` for the requested format.

    ``square``: ``size × size`` (``core_top`` 0). ``vertical``: ``size ×
    even(round(16·size/9))`` with the centered ``size × size`` core offset
    ``core_top`` down from the top edge.
    """
    if fmt == "vertical":
        height = _vertical_height(size)
        return size, height, round((height - size) / 2)
    return size, size, 0


def _caption_focus_baseline(size: int, fmt: str, style: str) -> int:
    """Focus-line baseline y (px) for the format and caption style.

    Square keeps the base ``0.76·S`` baseline for every style. Vertical uses the
    per-style union-safe baseline given at ``S = 1080`` (swap 1160, two-line
    1120, three-line 1104 — chosen so the whole line stack clears the cone above
    and the title below), scaled by ``S/1080`` like the other vertical
    coordinates.
    """
    if fmt == "vertical":
        ref = {
            "swap": _VERTICAL_CAPTION_BASELINE,
            "two-line": _VERTICAL_CAPTION_BASELINE_2LINE,
            "three-line": _VERTICAL_CAPTION_BASELINE_3LINE,
        }[style]
        return round(ref * size / _VERTICAL_REF)
    return round(_CAPTION_BASELINE_FRAC * size)


def _caption_active_font(style: str, base_font, popped_font):
    """Font for the focus line's active (spoken) word, per caption style.

    ``swap`` keeps the 1.08x scale pop (``popped_font``). The multi-line styles
    drop it (PM review 2026-07-12): on ``two-line``/``three-line`` the active
    word renders at the base caption pt (``base_font``), accent-colored but not
    enlarged, so the pop can no longer bleed into the adjacent words. Passing
    ``base_font`` as the active font makes ``_caption_word_placements``
    degenerate — ``active_adv == base_adv`` so ``draw_x == pen`` and the line
    never shifts while the highlight advances.
    """
    return popped_font if style == "swap" else base_font


def _font_for_cap_height(font_path, cap_px, image_font, ref: int = 200):
    """Return a Space Grotesk font sized so caps are about ``cap_px`` tall."""
    ref_font = image_font.truetype(str(font_path), ref)
    box = ref_font.getbbox("AI AGENT FM")
    height = box[3] - box[1]
    pt = max(1, round(ref * cap_px / height)) if height else ref
    return image_font.truetype(str(font_path), pt)


def _ellipsize(text, font, max_width):
    """Trim ``text`` with a trailing ``…`` until it fits ``max_width`` px."""
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and font.getlength(trimmed + ellipsis) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


def _blend(fg, bg, opacity):
    """Blend ``fg`` toward ``bg`` at ``opacity`` (deterministic, on-ground)."""
    return tuple(round(f * opacity + b * (1 - opacity)) for f, b in zip(fg, bg))


def _build_base_frame(size: int, title, root: Path, captions_on: bool = False, fmt: str = "square"):
    """Compose the static base frame: ink ground + wordmark + optional title.

    When captions are on the wordmark moves to the top-left corner at ~1/3 its
    centered size (the caption band takes its old lower slot); without captions
    the centered layout is unchanged. In ``vertical`` format the canvas is
    ``size × even(round(16·size/9))`` and the mark/title reuse the square math
    offset down by the core top. Raises ``ConfigError`` if the committed Space
    Grotesk Bold font is missing or unreadable (mirrors make_cover's wording).
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = _promo_font_path(root)
    width, height, core_top = _canvas_dims(size, fmt)

    img = Image.new("RGB", (width, height), INK)
    draw = ImageDraw.Draw(img)
    center_x = width / 2

    cap_frac = _CAPTION_WORDMARK_CAP_FRAC if captions_on else _WORDMARK_CAP_FRAC
    try:
        wordmark_font = _font_for_cap_height(font_path, cap_frac * size, ImageFont)
    except OSError as exc:
        raise ConfigError(
            f"promo font is unreadable or corrupt: {font_path} "
            f"(SpaceGrotesk-Bold.ttf) ({exc})"
        ) from exc
    tracking = _WORDMARK_TRACKING_EM * wordmark_font.size
    if captions_on:
        if fmt == "vertical":
            wm_left = round(_VERTICAL_WORDMARK_XY[0] * size / _VERTICAL_REF)
            wm_top = round(_VERTICAL_WORDMARK_XY[1] * size / _VERTICAL_REF)
        else:
            wm_left = round(_CAPTION_WORDMARK_LEFT_FRAC * size)
            wm_top = round(_CAPTION_WORDMARK_TOP_FRAC * size)
        _draw_tracked_left(draw, "AI AGENT FM", wordmark_font, tracking, wm_left, wm_top, IVORY)
    else:
        _draw_tracked(
            draw,
            "AI AGENT FM",
            wordmark_font,
            tracking,
            center_x,
            core_top + round(_WORDMARK_TOP_FRAC * size),
            IVORY,
        )

    if title:
        title_font = ImageFont.truetype(
            str(font_path), max(1, round(_TITLE_PT_FRAC * size))
        )
        text = _ellipsize(title, title_font, _TITLE_MAX_WIDTH_FRAC * size)
        text_width = title_font.getlength(text)
        draw.text(
            (center_x - text_width / 2, core_top + round(_TITLE_TOP_FRAC * size)),
            text,
            font=title_font,
            fill=_blend(IVORY, INK, _TITLE_OPACITY),
        )

    return img


def _caption_word_placements(block, caption_font, active_font, active_idx, center_x):
    """Per-word ``(text, font, draw_x)`` for one caption variant, laid out once.

    Pure and font-only (no rendering) so it is directly testable. Every word's
    pen position comes from the BASE caption font's advance — never the active
    word's inflated 1.08x advance — so a static word's ``draw_x`` is *identical*
    for every ``active_idx``; the highlight never nudges its neighbours by even
    a pixel. The active word is drawn with ``active_font`` centered inside its
    base advance cell (``pen + (base_advance - active_advance) / 2``), so the
    scale pop grows symmetrically about the word without shifting the line. When
    ``active_font`` *is* the base caption font (the multi-line color-only
    highlight — Amendment A) this degenerates: ``active_advance == base_advance``,
    the centering term is zero, and ``draw_x == pen``.
    """
    space_w = caption_font.getlength(" ")
    base_advances = [caption_font.getlength(word["text"]) for word in block]
    total = sum(base_advances) + space_w * (len(block) - 1) if block else 0.0
    placements = []
    pen = center_x - total / 2
    for i, word in enumerate(block):
        base_adv = base_advances[i]
        if i == active_idx:
            font = active_font
            draw_x = pen + (base_adv - active_font.getlength(word["text"])) / 2
        else:
            font = caption_font
            draw_x = pen
        placements.append((word["text"], font, draw_x))
        pen += base_adv + space_w
    return placements


def _render_caption_tile(block, active_idx, caption_font, active_font, baseline_y, center_x):
    """Render one ``(block, active_word_index)`` caption variant as an RGBA tile.

    The whole phrase is drawn on one line, centered on ``center_x`` with its
    baseline at ``baseline_y``; every word is ivory except the active word,
    which is its speaker's accent (HOST amber / GUEST magenta) drawn in
    ``active_font`` — 1.08x pt in the single-line ``swap`` style, the base
    caption pt (color only, no scale pop) in the multi-line styles — baseline-
    aligned. Word positions come from ``_caption_word_placements`` (base
    advances only) so static words never move as the highlight advances. The
    tile is tightly cropped to the ink bounding box plus a fixed 8 px margin;
    returns ``(tile, (paste_x, paste_y))`` so the caller pastes it onto the full
    canvas.
    """
    from PIL import Image, ImageDraw

    placements = _caption_word_placements(
        block, caption_font, active_font, active_idx, center_x
    )

    placed = []  # (text, font, fill, draw_x)
    xs, ys = [], []
    for i, (text, font, draw_x) in enumerate(placements):
        if i == active_idx:
            fill = _HOST_ACCENT if block[i]["speaker"] == "HOST" else _GUEST_ACCENT
        else:
            fill = _CAPTION_IVORY
        left, top, right, bottom = font.getbbox(text, anchor="ls")
        placed.append((text, font, fill, draw_x))
        xs.extend((draw_x + left, draw_x + right))
        ys.extend((baseline_y + top, baseline_y + bottom))

    left = min(xs) - _CAPTION_TILE_MARGIN_PX
    right = max(xs) + _CAPTION_TILE_MARGIN_PX
    top = min(ys) - _CAPTION_TILE_MARGIN_PX
    bottom = max(ys) + _CAPTION_TILE_MARGIN_PX
    off_x = math.floor(left)
    off_y = math.floor(top)
    tile_w = max(1, math.ceil(right) - off_x)
    tile_h = max(1, math.ceil(bottom) - off_y)

    tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    for text, font, fill, pen_x in placed:
        tdraw.text(
            (pen_x - off_x, baseline_y - off_y),
            text,
            font=font,
            fill=fill + (255,),
            anchor="ls",
        )
    return tile, (off_x, off_y)


def _render_context_tile(block, context_font, baseline_y, center_x):
    """Render a context (catch-up / read-ahead) caption line as an RGBA tile.

    The block's full text is drawn on one line, centered on ``center_x`` with its
    baseline at ``baseline_y`` (the focus baseline — the tile is slot-independent
    and the caller shifts it by ``slot * pitch`` at paste time), in a single
    dimmed color: ivory pre-blended 45 % over the ink ground. There is no
    active-word highlight and no accent hue — one tile per block. The tile is
    tightly cropped to the ink bounding box plus the same fixed 8 px margin as
    the focus tile; returns ``(tile, (paste_x, paste_y))``.
    """
    from PIL import Image, ImageDraw

    text = " ".join(word["text"] for word in block)
    fill = _blend(IVORY, INK, _CAPTION_CONTEXT_OPACITY)
    draw_x = center_x - context_font.getlength(text) / 2
    left, top, right, bottom = context_font.getbbox(text, anchor="ls")

    off_x = math.floor(draw_x + left - _CAPTION_TILE_MARGIN_PX)
    off_y = math.floor(baseline_y + top - _CAPTION_TILE_MARGIN_PX)
    tile_w = max(1, math.ceil(draw_x + right + _CAPTION_TILE_MARGIN_PX) - off_x)
    tile_h = max(1, math.ceil(baseline_y + bottom + _CAPTION_TILE_MARGIN_PX) - off_y)

    tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    tdraw.text(
        (draw_x - off_x, baseline_y - off_y),
        text,
        font=context_font,
        fill=fill + (255,),
        anchor="ls",
    )
    return tile, (off_x, off_y)


def _paste_with_alpha(frame, tile, xy, alpha: float) -> None:
    """Paste RGBA ``tile`` at ``xy`` onto ``frame``, scaling its alpha by ``alpha``.

    At full ``alpha`` this is the plain ``frame.paste(tile, xy, tile)`` used for
    every caption tile today (so the ``swap`` path stays byte-identical). Below
    full it composites through a scaled copy of the tile's own alpha channel,
    never mutating the cached tile, so a fading context line dissolves cleanly on
    top of its dimmed color. Zero alpha is a no-op.
    """
    if alpha >= 1.0:
        frame.paste(tile, xy, tile)
        return
    if alpha <= 0.0:
        return
    mask = tile.getchannel("A").point(lambda p: round(p * alpha))
    frame.paste(tile, xy, mask)


# ---------------------------------------------------------------------------
# ffmpeg argv construction (pure — unit-testable and monkeypatchable)
# ---------------------------------------------------------------------------


def _fmt_secs(value: float) -> str:
    """Format a seconds value for ffmpeg without a trailing ``.0``."""
    return "%g" % value


def decode_argv(audio_path: Path, start, duration) -> list[str]:
    """Build the ffmpeg argv that decodes mono s16le PCM at 48 kHz to stdout."""
    argv = ["ffmpeg", "-i", str(audio_path)]
    if start is not None:
        argv += ["-ss", _fmt_secs(start)]
    if duration is not None:
        argv += ["-t", _fmt_secs(duration)]
    argv += ["-ac", "1", "-ar", str(_DECODE_RATE), "-f", "s16le", "-"]
    return argv


def encode_argv(
    out_path: Path, width: int, height: int, fps: int, audio_path: Path, start, duration
) -> list[str]:
    """Build the single-process encode argv (rawvideo stdin + muxed audio).

    ``width``/``height`` size the raw input; square passes ``size, size`` and
    vertical passes ``size, even(round(16·size/9))``.
    """
    argv = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    if start is not None:
        argv += ["-ss", _fmt_secs(start)]
    if duration is not None:
        argv += ["-t", _fmt_secs(duration)]
    argv += [
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out_path),
    ]
    return argv


# ---------------------------------------------------------------------------
# Decode + render + encode
# ---------------------------------------------------------------------------


def _decode_audio(audio_path: Path, start, duration):
    """Decode the (trimmed) input to a mono s16 sample array via ffmpeg.

    Raises ``AudioError`` if the file is missing, ffmpeg is not on PATH, the
    decode fails, or the trim window lands past the end of the audio.
    """
    if not audio_path.exists():
        raise AudioError(f"input audio not found: {audio_path}")
    try:
        result = subprocess.run(decode_argv(audio_path, start, duration), capture_output=True)
    except FileNotFoundError:
        raise AudioError("ffmpeg not found — brew install ffmpeg")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")[-2000:]
        raise AudioError(
            f"ffmpeg failed decoding {audio_path.name} "
            f"(exit {result.returncode}):\n{stderr}"
        )
    pcm = result.stdout
    if len(pcm) < 2:
        raise AudioError(
            f"no audio decoded from {audio_path.name} at "
            f"--start {start} --duration {duration} — the trim window is "
            f"past the end of the audio"
        )
    samples = array.array("h")
    samples.frombytes(pcm[: (len(pcm) // 2) * 2])
    return samples


def build_promo(
    audio_path: Path,
    out_path: Path,
    size: int,
    fps: int,
    start,
    duration,
    title,
    root: Path,
    words=None,
    fmt: str = "square",
    style: str = "swap",
) -> None:
    """Render the promo video for ``audio_path`` to ``out_path``.

    Decodes the audio to an energy envelope, precomputes the gradient layer,
    cone mask, and static base frame, then streams one rendered frame per
    envelope value as raw rgb24 into a single ffmpeg encode that muxes the
    (trimmed) audio. When ``words`` (the full cached alignment) is given and
    the trim window contains at least one word, burned-in word-synced captions
    are composited beneath the cone; ``fmt`` selects ``square`` or ``vertical``
    (9:16) and ``style`` the caption window: ``swap`` (single line, swap in
    place — today's behavior), ``two-line`` (focus + dimmed read-ahead), or
    ``three-line`` (dimmed catch-up + focus + dimmed read-ahead, scrolling
    upward). Deterministic: same input + same alignment → same frames. Every
    user-facing failure raises an ``AgentFMError`` subclass.
    """
    samples = _decode_audio(audio_path, start, duration)
    envelope = compute_envelope(samples, fps)

    width, height, core_top = _canvas_dims(size, fmt)
    center_x = width / 2

    # Caption preparation: clip the alignment to the trim window, build blocks
    # and cues, and load the caption fonts. A window with no caption words is a
    # warning (not an error) — the render simply carries no captions.
    captions_on = words is not None
    caption_blocks: list = []
    caption_cues: list = []
    caption_font = active_font = context_font = None
    baseline_y = None
    pitch = 0
    if captions_on:
        w0 = start or 0.0
        # The spec mandates w1 = w0 + duration when --duration is given. The
        # decoded sample count is subject to ffmpeg's trim quantization (a few
        # samples either way), which can shift right-boundary word inclusion, so
        # it only defines the window when no explicit --duration was passed.
        if duration is not None:
            window_secs = duration
        else:
            window_secs = len(samples) / _DECODE_RATE
        clipped = clip_words_to_window(words, w0, w0 + window_secs)
        if not clipped:
            print(
                f"warning: no caption words fall inside the trim window "
                f"[{w0:g}, {w0 + window_secs:g}] s — rendering {out_path.name} "
                f"without captions",
                file=sys.stderr,
            )
            captions_on = False
        else:
            from PIL import ImageFont

            font_path = _promo_font_path(root)
            caption_pt = max(1, round(_CAPTION_PT_FRAC * size))
            active_pt = max(1, round(caption_pt * _CAPTION_ACTIVE_SCALE))
            context_pt = max(1, round(caption_pt * _CAPTION_CONTEXT_SCALE))
            try:
                caption_font = ImageFont.truetype(str(font_path), caption_pt)
                popped_font = ImageFont.truetype(str(font_path), active_pt)
                context_font = ImageFont.truetype(str(font_path), context_pt)
            except OSError as exc:
                raise ConfigError(
                    f"promo font is unreadable or corrupt: {font_path} "
                    f"(SpaceGrotesk-Bold.ttf) ({exc})"
                ) from exc
            # Amendment A: swap keeps the 1.08x pop; the multi-line styles use
            # the base font as the active font (color-only highlight, no pop).
            active_font = _caption_active_font(style, caption_font, popped_font)
            max_width = _CAPTION_MAX_WIDTH_FRAC * size
            caption_blocks = build_blocks(
                clipped, caption_font.getlength, max_width
            )
            # Amendment B: the multi-line styles hold same-speaker captions
            # through intra-turn pauses (bridge on speaker, not gap) and flush at
            # every turn change; swap keeps today's gap-based bridging.
            bridge = "speaker" if style in ("two-line", "three-line") else "gap"
            caption_cues = compute_cues(caption_blocks, window_secs, bridge)
            baseline_y = _caption_focus_baseline(size, fmt, style)
            pitch = round(_CAPTION_LINE_PITCH_FRAC * size)

    base = _build_base_frame(size, title, root, captions_on=captions_on, fmt=fmt)

    mark_px = round(_MARK_BOX_FRAC * size)
    mark_left = round((width - mark_px) / 2)
    mark_top = core_top + round(_MARK_TOP_FRAC * size)
    mark_scale = mark_px / _CANVAS
    anchor = (_TIP_ANCHOR[0] * mark_scale, _TIP_ANCHOR[1] * mark_scale)

    mask = render_cone_mask(mark_px, mark_scale)
    bbox = mask.getbbox()
    if bbox:
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    else:
        center = (mark_px / 2, mark_px / 2)
    layer = build_gradient_layer(mark_px, anchor)

    argv = encode_argv(out_path, width, height, fps, audio_path, start, duration)
    stderr_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file
        )
    except FileNotFoundError:
        stderr_file.close()
        raise AudioError("ffmpeg not found — brew install ffmpeg")

    cache: dict[int, tuple] = {}
    focus_cache: dict[tuple, tuple] = {}
    context_cache: dict[int, tuple] = {}
    cur_block = -1
    try:
        for i, e in enumerate(envelope):
            level = round(e * (_QUANT_LEVELS - 1))
            tile = cache.get(level)
            if tile is None:
                e_q = level / (_QUANT_LEVELS - 1)
                tile = _render_mark_tile(e_q, layer, mask, anchor, center, mark_px)
                cache[level] = tile
            img, (dx, dy) = tile
            frame = base.copy()
            frame.paste(img, (mark_left + dx, mark_top + dy), img)

            if captions_on:
                t = i / fps
                plans = plan_caption_lines(caption_blocks, caption_cues, style, t)
                if plans:
                    live = _live_block(caption_cues, t)
                    if live != cur_block:
                        # Bounded memory: focus variants only for the live block,
                        # context tiles only for the {i-2, i-1, i, i+1} window.
                        keep = {live - 2, live - 1, live, live + 1}
                        focus_cache = {
                            k: v for k, v in focus_cache.items() if k[0] == live
                        }
                        context_cache = {
                            k: v for k, v in context_cache.items() if k in keep
                        }
                        cur_block = live
                    # Paste order: context lines first, then the focus line on
                    # top (they never overlap, but the focus stays last).
                    for bidx, kind, slot, alpha in plans:
                        if kind != "context":
                            continue
                        entry = context_cache.get(bidx)
                        if entry is None:
                            entry = _render_context_tile(
                                caption_blocks[bidx], context_font, baseline_y, center_x
                            )
                            context_cache[bidx] = entry
                        cimg, (cx, cy) = entry
                        _paste_with_alpha(
                            frame, cimg, (cx, cy + round(slot * pitch)), alpha
                        )
                    for bidx, kind, slot, alpha in plans:
                        if kind != "focus":
                            continue
                        aidx = active_word_index(caption_blocks[bidx], t)
                        key = (bidx, aidx)
                        entry = focus_cache.get(key)
                        if entry is None:
                            entry = _render_caption_tile(
                                caption_blocks[bidx], aidx, caption_font,
                                active_font, baseline_y, center_x,
                            )
                            focus_cache[key] = entry
                        fimg, (fx, fy) = entry
                        _paste_with_alpha(
                            frame, fimg, (fx, fy + round(slot * pitch)), alpha
                        )

            proc.stdin.write(frame.tobytes())
    except BrokenPipeError:
        pass  # ffmpeg died early; the return code below surfaces the reason.
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        stderr_file.seek(0)
        stderr = stderr_file.read().decode("utf-8", "replace")[-2000:]
        stderr_file.close()
        raise AudioError(
            f"ffmpeg failed encoding {out_path.name} "
            f"(exit {proc.returncode}):\n{stderr}"
        )
    stderr_file.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _nonneg_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _pos_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _pos_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _even_pos_int(text: str) -> int:
    """Argparse type for ``--size``: a positive, even integer.

    libx264's yuv420p needs even width and height. An odd square is directly
    unencodable, and a vertical whose width is odd would fail in ffmpeg too, so
    both formats reject an odd size up front with a clear message.
    """
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    if value % 2:
        raise argparse.ArgumentTypeError(
            "must be even — libx264 yuv420p needs even dimensions"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    User-facing failures raise ``AgentFMError`` subclasses; those are caught
    here, printed to stderr as ``error: <message>``, and turned into exit code
    1. Any other exception propagates — it is a bug, not a user error.
    """
    root = Path(__file__).parent

    parser = argparse.ArgumentParser(prog="promo_video.py", description=__doc__)
    parser.add_argument("audio", help="path to the input audio clip")
    parser.add_argument(
        "-o", "--output", default=None, help="output MP4 path (not needed with --align-only)"
    )
    parser.add_argument(
        "--start", type=_nonneg_float, default=None, help="trim start (seconds)"
    )
    parser.add_argument(
        "--duration", type=_pos_float, default=None, help="trim length (seconds)"
    )
    parser.add_argument("--title", default=None, help="optional title line")
    parser.add_argument(
        "--fps", type=_pos_int, default=30, help="frames per second (default 30)"
    )
    parser.add_argument(
        "--size",
        type=_even_pos_int,
        default=1080,
        help="canvas width in px, must be even (default 1080); square is S×S, "
        "vertical is S×even(round(16·S/9))",
    )
    parser.add_argument(
        "--transcript",
        default=None,
        help="episode script.json — required to create or refresh an alignment cache",
    )
    parser.add_argument(
        "--captions-json",
        default=None,
        help="alignment cache path (default <audio stem>.alignment.json)",
    )
    parser.add_argument(
        "--refresh-alignment",
        action="store_true",
        help="ignore any existing cache and re-fetch alignment (needs --transcript)",
    )
    parser.add_argument(
        "--align-only",
        action="store_true",
        help="fetch + cache alignment, print stats, and exit without rendering",
    )
    parser.add_argument(
        "--format",
        choices=("square", "vertical"),
        default="square",
        help="output format: square (default) or vertical 9:16",
    )
    parser.add_argument(
        "--caption-style",
        choices=("swap", "two-line", "three-line"),
        default="three-line",
        help="caption window: swap (single line, swap in place), "
        "two-line (focus + dimmed read-ahead below), three-line (default, "
        "dimmed catch-up above + focus + dimmed read-ahead, scrolling upward)",
    )
    args = parser.parse_args(argv)

    if args.align_only and not args.transcript:
        parser.error("--align-only requires --transcript")
    if args.refresh_alignment and not args.transcript:
        parser.error("--refresh-alignment requires --transcript")
    if not args.align_only and not args.output:
        parser.error("-o/--output is required unless --align-only is given")

    audio_path = Path(args.audio)
    transcript_path = Path(args.transcript) if args.transcript else None
    if args.captions_json:
        cache_path = Path(args.captions_json)
    else:
        cache_path = audio_path.with_suffix(".alignment.json")

    load_env(root / ".env")

    try:
        words = resolve_caption_words(
            audio_path, cache_path, transcript_path, args.refresh_alignment
        )

        if args.align_only:
            # A hand-written cache may omit loss (load_alignment_cache does not
            # require it); default to 0.0 so the stats line never KeyErrors.
            losses = [w.get("loss", 0.0) for w in words]
            mean_loss = sum(losses) / len(losses)
            print(f"aligned {len(words)} words → {cache_path}")
            print(f"per-word loss: mean {mean_loss:.4f}, max {max(losses):.4f}")
            return 0

        build_promo(
            audio_path,
            Path(args.output),
            args.size,
            args.fps,
            args.start,
            args.duration,
            args.title,
            root,
            words=words,
            fmt=args.format,
            style=args.caption_style,
        )
    except AgentFMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
