"""Tests for episode artwork: fonts, backdrops, and cover composition."""

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageFont

import publish
from tests.test_episode import META, make_episode_dir

REPO_ROOT = Path(publish.__file__).parent
REPO_FONT = REPO_ROOT / "artwork" / "fonts" / "SpaceGrotesk-Bold.ttf"


def make_artwork(root: Path, colors=((255, 0, 0), (0, 0, 255))) -> None:
    """Build a minimal artwork tree under ``root``: tiny solid-color
    backdrops (offline stand-ins for the real pool) plus the real
    committed font copied from the repo."""
    backdrops = root / "artwork" / "backdrops"
    backdrops.mkdir(parents=True)
    for i, color in enumerate(colors):
        Image.new("RGB", (256, 256), color).save(
            backdrops / f"backdrop-{i:02d}.jpg", quality=95
        )
    fonts = root / "artwork" / "fonts"
    fonts.mkdir()
    shutil.copy(REPO_FONT, fonts / "SpaceGrotesk-Bold.ttf")


def test_bundled_font_loads():
    font = ImageFont.truetype(str(REPO_FONT), 120)
    assert font.getbbox("AI AGENT FM")[2] > 0


def test_font_license_committed():
    text = (REPO_ROOT / "artwork" / "fonts" / "OFL.txt").read_text()
    assert "SIL OPEN FONT LICENSE" in text
