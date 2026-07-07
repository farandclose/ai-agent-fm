import json
from pathlib import Path

import publish
from tests.test_artwork import make_artwork
from tests.test_config import VALID_TOML
from tests.test_episode import make_episode_dir


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "agentfm.toml").write_text(VALID_TOML)
    make_artwork(root)
    return root


def test_publish_fake_no_upload_creates_mp3_and_meta(tmp_path):
    root = make_root(tmp_path)
    ep_dir = make_episode_dir(tmp_path)
    rc = publish.main(
        ["publish", str(ep_dir), "--fake-tts", "--no-upload", "--root", str(root)]
    )
    assert rc == 0
    assert (ep_dir / "episode.mp3").stat().st_size > 0
    assert json.loads((ep_dir / "audio_meta.json").read_text())["duration_secs"] >= 1
    assert (ep_dir / "cover.jpg").stat().st_size > 0
    assert not (ep_dir / "episode.wav").exists()
    assert not (root / "episodes.json").exists()  # --no-upload has no side effects
    assert not (root / "feed.xml").exists()


def test_publish_uploads_manifest_and_feed(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    ep_dir = make_episode_dir(tmp_path)
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(publish, "make_r2_client", lambda: object())
    monkeypatch.setattr(
        publish, "upload_file",
        lambda client, bucket, key, path, content_type: uploads.append((key, content_type)),
    )
    rc = publish.main(["publish", str(ep_dir), "--fake-tts", "--root", str(root)])
    assert rc == 0
    assert uploads == [
        ("episodes/clicky-2026-07-07-engg.mp3", "audio/mpeg"),
        ("episodes/clicky-2026-07-07-engg.jpg", "image/jpeg"),
        ("feed.xml", "application/rss+xml"),
    ]
    manifest = json.loads((root / "episodes.json").read_text())
    assert manifest["episodes"][0]["id"] == "clicky-2026-07-07-engg"
    assert (
        manifest["episodes"][0]["cover_key"]
        == "episodes/clicky-2026-07-07-engg.jpg"
    )
    assert (root / "feed.xml").read_text().startswith("<?xml")


def test_republish_skips_synthesis(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    ep_dir = make_episode_dir(tmp_path)
    publish.main(["publish", str(ep_dir), "--fake-tts", "--no-upload", "--root", str(root)])
    monkeypatch.setattr(publish, "make_r2_client", lambda: object())
    monkeypatch.setattr(publish, "upload_file", lambda *a, **k: None)

    def explode(*a, **k):
        raise AssertionError("synthesis must not run on --republish")

    monkeypatch.setattr(publish, "synthesize_all", explode)
    rc = publish.main(["publish", str(ep_dir), "--republish", "--root", str(root)])
    assert rc == 0


def test_republish_without_mp3_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    ep_dir = make_episode_dir(tmp_path)
    rc = publish.main(["publish", str(ep_dir), "--republish", "--root", str(root)])
    assert rc == 1
    assert "republish" in capsys.readouterr().err.lower()


def test_empty_base_url_blocks_upload(tmp_path, capsys):
    root = make_root(tmp_path)
    toml = (root / "agentfm.toml").read_text().replace(
        'public_base_url = "https://pub-abc.r2.dev"', 'public_base_url = ""'
    )
    (root / "agentfm.toml").write_text(toml)
    ep_dir = make_episode_dir(tmp_path)
    rc = publish.main(["publish", str(ep_dir), "--fake-tts", "--root", str(root)])
    assert rc == 1
    assert "setup" in capsys.readouterr().err.lower()


def test_publish_uploads_cover_when_present(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    (root / "artwork").mkdir(exist_ok=True)
    (root / "artwork" / "cover.jpg").write_bytes(b"\xff\xd8fakejpeg")
    ep_dir = make_episode_dir(tmp_path)
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(publish, "make_r2_client", lambda: object())
    monkeypatch.setattr(
        publish, "upload_file",
        lambda client, bucket, key, path, content_type: uploads.append((key, content_type)),
    )
    rc = publish.main(["publish", str(ep_dir), "--fake-tts", "--root", str(root)])
    assert rc == 0
    assert uploads == [
        ("episodes/clicky-2026-07-07-engg.mp3", "audio/mpeg"),
        ("episodes/clicky-2026-07-07-engg.jpg", "image/jpeg"),
        ("cover.jpg", "image/jpeg"),
        ("feed.xml", "application/rss+xml"),
    ]
    feed = (root / "feed.xml").read_text()
    assert "https://pub-abc.r2.dev/cover.jpg" in feed
    assert "https://pub-abc.r2.dev/episodes/clicky-2026-07-07-engg.jpg" in feed


def test_republish_regenerates_cover(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    ep_dir = make_episode_dir(tmp_path)
    publish.main(
        ["publish", str(ep_dir), "--fake-tts", "--no-upload", "--root", str(root)]
    )
    (ep_dir / "cover.jpg").unlink()
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(publish, "make_r2_client", lambda: object())
    monkeypatch.setattr(
        publish, "upload_file",
        lambda client, bucket, key, path, content_type: uploads.append(
            (key, content_type)
        ),
    )
    rc = publish.main(["publish", str(ep_dir), "--republish", "--root", str(root)])
    assert rc == 0
    assert (ep_dir / "cover.jpg").exists()
    assert ("episodes/clicky-2026-07-07-engg.jpg", "image/jpeg") in uploads


def test_missing_backdrops_is_actionable_error(tmp_path, capsys):
    root = make_root(tmp_path)
    for f in (root / "artwork" / "backdrops").glob("*.jpg"):
        f.unlink()
    ep_dir = make_episode_dir(tmp_path)
    rc = publish.main(
        ["publish", str(ep_dir), "--fake-tts", "--no-upload", "--root", str(root)]
    )
    assert rc == 1
    assert "make_backdrops" in capsys.readouterr().err
