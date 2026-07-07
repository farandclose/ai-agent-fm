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


def test_backdrop_index_follows_sha256_contract():
    digest = hashlib.sha256(b"human-harness").digest()
    expected = int.from_bytes(digest[:8], "big") % 12
    assert publish.backdrop_index("human-harness", 12) == expected


def test_backdrop_index_stable_and_in_range():
    for pool_size in (1, 2, 12):
        idx = publish.backdrop_index("clicky", pool_size)
        assert idx == publish.backdrop_index("clicky", pool_size)
        assert 0 <= idx < pool_size


def test_make_cover_writes_3000px_jpeg(tmp_path):
    make_artwork(tmp_path)
    ep = publish.load_episode(make_episode_dir(tmp_path))
    cover = publish.make_cover(ep, tmp_path)
    assert cover == ep.dir / "cover.jpg"
    with Image.open(cover) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.size == (3000, 3000)


def test_make_cover_uses_project_locked_backdrop(tmp_path):
    # Fixture pool: backdrop-00 solid red, backdrop-01 solid blue. The corner
    # pixel sits in the text-free 2% margin, so it must show the backdrop
    # chosen by backdrop_index for this project.
    make_artwork(tmp_path)
    ep = publish.load_episode(make_episode_dir(tmp_path))
    expected = publish.backdrop_index(ep.project, 2)
    with Image.open(publish.make_cover(ep, tmp_path)) as img:
        r, g, b = img.getpixel((20, 20))[:3]
    if expected == 0:
        assert r > 180 and b < 100  # red backdrop
    else:
        assert b > 180 and r < 100  # blue backdrop


def test_make_cover_handles_long_project_name(tmp_path):
    make_artwork(tmp_path)
    meta = dict(
        META,
        project_name="An Extremely Long Product Name That Wraps Onto Several Lines",
    )
    ep = publish.load_episode(make_episode_dir(tmp_path, meta=meta))
    with Image.open(publish.make_cover(ep, tmp_path)) as img:
        assert img.size == (3000, 3000)


def test_make_cover_missing_backdrops_raises(tmp_path):
    make_artwork(tmp_path)
    for f in (tmp_path / "artwork" / "backdrops").glob("*.jpg"):
        f.unlink()
    ep = publish.load_episode(make_episode_dir(tmp_path))
    with pytest.raises(publish.ConfigError, match="make_backdrops"):
        publish.make_cover(ep, tmp_path)


def test_make_cover_missing_font_raises(tmp_path):
    make_artwork(tmp_path)
    (tmp_path / "artwork" / "fonts" / "SpaceGrotesk-Bold.ttf").unlink()
    ep = publish.load_episode(make_episode_dir(tmp_path))
    with pytest.raises(publish.ConfigError, match="SpaceGrotesk"):
        publish.make_cover(ep, tmp_path)


def test_make_cover_corrupt_backdrop_raises_config_error(tmp_path):
    make_artwork(tmp_path)
    for f in sorted((tmp_path / "artwork" / "backdrops").glob("*.jpg")):
        f.write_bytes(b"not a jpeg at all")
    ep = publish.load_episode(make_episode_dir(tmp_path))
    with pytest.raises(publish.ConfigError, match="backdrop"):
        publish.make_cover(ep, tmp_path)


def test_make_cover_unbreakable_word_stays_on_canvas(tmp_path):
    # A single unbroken 80-char token must shrink until it fits — the
    # outermost 2% of the canvas (left/right mid-edges included) must
    # remain pure backdrop (solid red fixture backdrop-00 or blue -01).
    make_artwork(tmp_path)
    meta = dict(META, project_name="X" * 80)
    ep = publish.load_episode(make_episode_dir(tmp_path, meta=meta))
    with Image.open(publish.make_cover(ep, tmp_path)) as img:
        assert img.size == (3000, 3000)
        for x, y in ((10, 1500), (2990, 1500)):
            r, g, b = img.getpixel((x, y))[:3]
            assert (r > 180 and b < 100) or (b > 180 and r < 100), (
                f"text bled into the edge margin at {(x, y)}: {(r, g, b)}"
            )
