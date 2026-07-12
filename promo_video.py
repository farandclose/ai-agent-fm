"""AI Agent FM — promo video ("Radiating Pulse").

Standalone CLI (sibling of ``publish.py``) that turns an audio clip into a
square, LinkedIn-ready MP4: the Spectrum Cone brand mark on a flat ink ground,
its radial arc gradient pulsing outward from the source point in sync with the
audio, the wordmark beneath, and the audio muxed in.

    uv run promo_video.py episodes/<ep>/episode.mp3 -o promo.mp4 \
        [--start 63] [--duration 45] [--title "How I built X"] \
        [--fps 30] [--size 1080]

Design is final (see ``docs/design/promo-video-pulse.md``); geometry and color
come verbatim from ``docs/design/spectrum-cone-v2/BRAND-SPEC.md``. This module
holds only mechanics: audio-energy envelope math, gradient/mask rendering, and
a single-process ffmpeg encode over a rawvideo stdin pipe. It imports the error
taxonomy from ``publish`` so ``main()`` follows the same contract: catch only
``AgentFMError``, print ``error: …`` to stderr, exit 1. Pillow is imported
lazily inside the functions that need it; the only external tool is
ffmpeg-on-PATH (already required by the repo).
"""

import argparse
import array
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from publish import AgentFMError, AudioError, ConfigError

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


def _build_base_frame(size: int, title, root: Path):
    """Compose the static base frame: ink ground + wordmark + optional title.

    Raises ``ConfigError`` if the committed Space Grotesk Bold font is missing
    or unreadable (mirrors make_cover's wording).
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = root / "artwork" / "fonts" / "SpaceGrotesk-Bold.ttf"
    if not font_path.exists():
        raise ConfigError(
            f"promo font missing: {font_path} (SpaceGrotesk-Bold.ttf)"
        )

    img = Image.new("RGB", (size, size), INK)
    draw = ImageDraw.Draw(img)
    center_x = size / 2

    try:
        wordmark_font = _font_for_cap_height(
            font_path, _WORDMARK_CAP_FRAC * size, ImageFont
        )
    except OSError as exc:
        raise ConfigError(
            f"promo font is unreadable or corrupt: {font_path} "
            f"(SpaceGrotesk-Bold.ttf) ({exc})"
        ) from exc
    tracking = _WORDMARK_TRACKING_EM * wordmark_font.size
    _draw_tracked(
        draw,
        "AI AGENT FM",
        wordmark_font,
        tracking,
        center_x,
        round(_WORDMARK_TOP_FRAC * size),
        IVORY,
    )

    if title:
        title_font = ImageFont.truetype(
            str(font_path), max(1, round(_TITLE_PT_FRAC * size))
        )
        text = _ellipsize(title, title_font, _TITLE_MAX_WIDTH_FRAC * size)
        width = title_font.getlength(text)
        draw.text(
            (center_x - width / 2, round(_TITLE_TOP_FRAC * size)),
            text,
            font=title_font,
            fill=_blend(IVORY, INK, _TITLE_OPACITY),
        )

    return img


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
    out_path: Path, size: int, fps: int, audio_path: Path, start, duration
) -> list[str]:
    """Build the single-process encode argv (rawvideo stdin + muxed audio)."""
    argv = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{size}x{size}",
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
) -> None:
    """Render the promo video for ``audio_path`` to ``out_path``.

    Decodes the audio to an energy envelope, precomputes the gradient layer,
    cone mask, and static base frame, then streams one rendered frame per
    envelope value as raw rgb24 into a single ffmpeg encode that muxes the
    (trimmed) audio. Deterministic: same input → same frames. Every
    user-facing failure raises an ``AgentFMError`` subclass.
    """
    samples = _decode_audio(audio_path, start, duration)
    envelope = compute_envelope(samples, fps)

    base = _build_base_frame(size, title, root)

    mark_px = round(_MARK_BOX_FRAC * size)
    mark_left = round((size - mark_px) / 2)
    mark_top = round(_MARK_TOP_FRAC * size)
    mark_scale = mark_px / _CANVAS
    anchor = (_TIP_ANCHOR[0] * mark_scale, _TIP_ANCHOR[1] * mark_scale)

    mask = render_cone_mask(mark_px, mark_scale)
    bbox = mask.getbbox()
    if bbox:
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    else:
        center = (mark_px / 2, mark_px / 2)
    layer = build_gradient_layer(mark_px, anchor)

    argv = encode_argv(out_path, size, fps, audio_path, start, duration)
    stderr_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file
        )
    except FileNotFoundError:
        stderr_file.close()
        raise AudioError("ffmpeg not found — brew install ffmpeg")

    cache: dict[int, tuple] = {}
    try:
        for e in envelope:
            level = round(e * (_QUANT_LEVELS - 1))
            tile = cache.get(level)
            if tile is None:
                e_q = level / (_QUANT_LEVELS - 1)
                tile = _render_mark_tile(e_q, layer, mask, anchor, center, mark_px)
                cache[level] = tile
            img, (dx, dy) = tile
            frame = base.copy()
            frame.paste(img, (mark_left + dx, mark_top + dy), img)
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
        "-o", "--output", required=True, help="output MP4 path"
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
        "--size", type=_pos_int, default=1080, help="square side in px (default 1080)"
    )
    args = parser.parse_args(argv)

    try:
        build_promo(
            Path(args.audio),
            Path(args.output),
            args.size,
            args.fps,
            args.start,
            args.duration,
            args.title,
            root,
        )
    except AgentFMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
