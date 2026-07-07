#!/usr/bin/env python3
"""One-time, re-runnable brand-asset generator for AI Agent FM cover backdrops.

Renders a fixed pool of 12 "neural soundwave" backdrops: dark-to-mid coloured
squares with a subtle waveform texture built from sums of sinusoids. These sit
UNDER large near-white episode type added by a later step, so they are
deliberately quiet -- low-contrast waves in the same hue family as the
background, never a foreground element.

Everything is deterministic: `generate_backdrop(index, size)` returns a
pixel-identical image for the same arguments (randomness, where used, is seeded
per design with `random.Random(index)` -- no wall-clock, no global RNG). Re-run
`python artwork/make_backdrops.py` any time to regenerate the committed pool.

Usage:
    python artwork/make_backdrops.py                 # -> ./backdrops/*.jpg, 3000px
    python artwork/make_backdrops.py --out DIR --size N
"""

from __future__ import annotations

import argparse
import colorsys
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

# Twelve designs. Each dict fully specifies one backdrop:
#   hue        base hue (0..1) -- chosen for pairwise-distinct, deep colours
#   sat        background saturation (0..1)
#   v_top      background value (brightness 0..1) at the top edge
#   v_bottom   background value at the bottom edge (soft vertical gradient)
#   n_waves    number of stacked waveform curves
#   symmetric  mirror the curves about the horizontal midline
#   components list of (frequency-in-cycles-across-width, amplitude-fraction)
#              sinusoids summed to shape each curve
#   wave_dv    signed lightness offset of the wave colour vs. background value
#              (kept small so waves stay subtle)
PARAMS: list[dict] = [
    # 0  indigo, symmetric, calm low-frequency swells
    {"hue": 230 / 360, "sat": 0.55, "v_top": 0.30, "v_bottom": 0.12,
     "n_waves": 4, "symmetric": True, "wave_dv": 0.14,
     "components": [(1.5, 1.0), (3.0, 0.35)]},
    # 1  teal, asymmetric, mid-frequency ripple
    {"hue": 175 / 360, "sat": 0.50, "v_top": 0.28, "v_bottom": 0.14,
     "n_waves": 6, "symmetric": False, "wave_dv": 0.12,
     "components": [(2.0, 1.0), (5.0, 0.30), (8.0, 0.15)]},
    # 2  crimson, symmetric, few bold swells
    {"hue": 350 / 360, "sat": 0.52, "v_top": 0.26, "v_bottom": 0.11,
     "n_waves": 3, "symmetric": True, "wave_dv": 0.13,
     "components": [(1.0, 1.0), (2.5, 0.40)]},
    # 3  forest green, asymmetric, dense fine ripple
    {"hue": 140 / 360, "sat": 0.45, "v_top": 0.24, "v_bottom": 0.12,
     "n_waves": 7, "symmetric": False, "wave_dv": 0.11,
     "components": [(3.0, 1.0), (7.0, 0.30)]},
    # 4  royal purple, symmetric, layered harmonics
    {"hue": 275 / 360, "sat": 0.50, "v_top": 0.29, "v_bottom": 0.13,
     "n_waves": 5, "symmetric": True, "wave_dv": 0.15,
     "components": [(1.5, 1.0), (4.0, 0.35), (9.0, 0.12)]},
    # 5  ocean blue, asymmetric, long slow wave
    {"hue": 210 / 360, "sat": 0.55, "v_top": 0.27, "v_bottom": 0.12,
     "n_waves": 5, "symmetric": False, "wave_dv": 0.13,
     "components": [(1.0, 1.0), (2.0, 0.45)]},
    # 6  burnt amber (dark), symmetric, tight ripple
    {"hue": 28 / 360, "sat": 0.55, "v_top": 0.25, "v_bottom": 0.11,
     "n_waves": 6, "symmetric": True, "wave_dv": 0.12,
     "components": [(2.5, 1.0), (6.0, 0.30)]},
    # 7  magenta, asymmetric, sparse tall swells
    {"hue": 320 / 360, "sat": 0.48, "v_top": 0.28, "v_bottom": 0.13,
     "n_waves": 4, "symmetric": False, "wave_dv": 0.14,
     "components": [(1.5, 1.0), (3.5, 0.30)]},
    # 8  cyan, symmetric, dense many-line field
    {"hue": 190 / 360, "sat": 0.50, "v_top": 0.26, "v_bottom": 0.12,
     "n_waves": 8, "symmetric": True, "wave_dv": 0.11,
     "components": [(2.0, 1.0), (5.0, 0.25)]},
    # 9  olive, asymmetric, gentle rolling wave
    {"hue": 80 / 360, "sat": 0.42, "v_top": 0.24, "v_bottom": 0.13,
     "n_waves": 5, "symmetric": False, "wave_dv": 0.12,
     "components": [(1.5, 1.0), (4.5, 0.28)]},
    # 10 rust red, symmetric, bold low harmonics
    {"hue": 12 / 360, "sat": 0.52, "v_top": 0.25, "v_bottom": 0.11,
     "n_waves": 4, "symmetric": True, "wave_dv": 0.13,
     "components": [(1.0, 1.0), (3.0, 0.35)]},
    # 11 violet-navy, asymmetric, layered fine detail
    {"hue": 255 / 360, "sat": 0.50, "v_top": 0.28, "v_bottom": 0.12,
     "n_waves": 7, "symmetric": False, "wave_dv": 0.13,
     "components": [(2.5, 1.0), (6.0, 0.30), (10.0, 0.12)]},
]

assert len(PARAMS) == 12, "the pool is exactly 12 designs"

_SUPERSAMPLE = 2  # render at 2x then LANCZOS-downsample for smooth curves


def _hsv_rgb(hue: float, sat: float, val: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (round(r * 255), round(g * 255), round(b * 255))


def _background(w: int, h: int, p: dict) -> Image.Image:
    """Soft vertical gradient from ``v_top`` (top) to ``v_bottom`` (bottom)."""
    column = Image.new("RGB", (1, h))
    top, bottom = p["v_top"], p["v_bottom"]
    px = []
    for y in range(h):
        t = y / max(1, h - 1)
        val = top + (bottom - top) * t
        px.append(_hsv_rgb(p["hue"], p["sat"], val))
    column.putdata(px)
    return column.resize((w, h), Image.NEAREST)


def _curve_points(w: int, h: int, center: float, amp: float, phase: float,
                  components: list) -> list:
    """Sample one waveform curve across the full width."""
    step = max(2, w // 600)
    norm = sum(a for _, a in components) or 1.0
    pts = []
    x = 0
    while x <= w:
        u = x / w
        s = sum(a * math.sin(2 * math.pi * f * u + phase * (i + 1))
                for i, (f, a) in enumerate(components))
        y = center + amp * (s / norm)
        pts.append((x, y))
        x += step
    return pts


def generate_backdrop(index: int, size: int) -> Image.Image:
    """Deterministically render backdrop ``index`` at ``size`` x ``size`` (RGB)."""
    if not 0 <= index < len(PARAMS):
        raise IndexError(f"index {index} out of range(12)")
    p = PARAMS[index]
    rng = random.Random(index)

    w = h = size * _SUPERSAMPLE
    img = _background(w, h, p)
    draw = ImageDraw.Draw(img)

    val_mid = (p["v_top"] + p["v_bottom"]) / 2
    wave_val = max(0.0, min(1.0, val_mid + p["wave_dv"]))
    wave_rgb = _hsv_rgb(p["hue"], p["sat"] * 0.85, wave_val)
    line_w = max(1, round(size / 340) * _SUPERSAMPLE)

    n = p["n_waves"]
    amp = (h / (n + 1)) * 0.42  # curves must not collide -> subtle bands
    mid = h / 2

    if p["symmetric"]:
        # Genuine mirror symmetry: each curve above the midline is reflected
        # to an identical partner below it (y -> h - y).
        half = n // 2
        for k in range(half):
            d = (k + 1) / (half + 1) * (mid * 0.85)
            phase = rng.uniform(0, 2 * math.pi)
            up = _curve_points(w, h, mid - d, amp, phase, p["components"])
            draw.line(up, fill=wave_rgb, width=line_w, joint="curve")
            down = [(x, h - y) for x, y in up]
            draw.line(down, fill=wave_rgb, width=line_w, joint="curve")
        if n % 2 == 1:  # odd count -> one curve on the axis of symmetry
            phase = rng.uniform(0, 2 * math.pi)
            axis = _curve_points(w, h, mid, amp, phase, p["components"])
            draw.line(axis, fill=wave_rgb, width=line_w, joint="curve")
    else:
        for k in range(n):
            center = mid + (((k + 1) / (n + 1)) - 0.5) * h * 0.9
            phase = rng.uniform(0, 2 * math.pi)
            pts = _curve_points(w, h, center, amp, phase, p["components"])
            draw.line(pts, fill=wave_rgb, width=line_w, joint="curve")

    if _SUPERSAMPLE != 1:
        img = img.resize((size, size), Image.LANCZOS)
    return img.convert("RGB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the committed pool of 12 waveform backdrops.")
    parser.add_argument(
        "--out", default=str(Path(__file__).parent / "backdrops"),
        help="output directory (default: backdrops/ next to this script)")
    parser.add_argument(
        "--size", type=int, default=3000, help="edge length in px (default 3000)")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(len(PARAMS)):
        img = generate_backdrop(i, args.size)
        path = out / f"backdrop-{i:02d}.jpg"
        img.save(path, "JPEG", quality=88)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
