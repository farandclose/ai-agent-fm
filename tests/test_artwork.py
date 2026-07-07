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


def _load_generator():
    path = REPO_ROOT / "artwork" / "make_backdrops.py"
    spec = importlib.util.spec_from_file_location("make_backdrops", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generator_is_deterministic():
    gen = _load_generator()
    a = gen.generate_backdrop(0, 128)
    b = gen.generate_backdrop(0, 128)
    assert a.tobytes() == b.tobytes()


def test_generator_designs_all_differ():
    gen = _load_generator()
    renders = [gen.generate_backdrop(i, 64).tobytes() for i in range(12)]
    assert len(set(renders)) == 12


def test_generator_main_writes_pool(tmp_path):
    gen = _load_generator()
    rc = gen.main(["--out", str(tmp_path), "--size", "64"])
    assert rc == 0
    files = sorted(tmp_path.glob("backdrop-*.jpg"))
    assert [f.name for f in files] == [f"backdrop-{i:02d}.jpg" for i in range(12)]
    with Image.open(files[0]) as img:
        assert img.size == (64, 64) and img.mode == "RGB"


def test_committed_pool_present_and_sized():
    files = sorted((REPO_ROOT / "artwork" / "backdrops").glob("backdrop-*.jpg"))
    assert len(files) == 12
    with Image.open(files[0]) as img:
        assert img.size == (3000, 3000)
