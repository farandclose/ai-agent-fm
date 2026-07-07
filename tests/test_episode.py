import json
from pathlib import Path

import pytest

import publish

META = {
    "id": "clicky-2026-07-07-engg",
    "title": "Inside Clicky",
    "description": "The engineering story of Clicky.",
    "project": "clicky",
    "lens": "engg",
    "date": "2026-07-07",
}
SCRIPT = {
    "title": "Inside Clicky",
    "lens": "engg",
    "turns": [
        {"speaker": "HOST", "text": "Welcome back to Agent FM."},
        {"speaker": "GUEST", "text": "Great to be here."},
    ],
}


def make_episode_dir(tmp_path: Path, meta=None, script=None) -> Path:
    d = tmp_path / "clicky-2026-07-07-engg"
    d.mkdir()
    (d / "episode.json").write_text(json.dumps(meta if meta is not None else META))
    (d / "script.json").write_text(json.dumps(script if script is not None else SCRIPT))
    return d


def test_load_episode_happy_path(tmp_path):
    ep = publish.load_episode(make_episode_dir(tmp_path))
    assert ep.id == "clicky-2026-07-07-engg"
    assert ep.lens == "engg"
    assert ep.date == "2026-07-07"
    assert len(ep.turns) == 2
    assert ep.turns[0]["speaker"] == "HOST"


def test_missing_script_file_raises(tmp_path):
    d = make_episode_dir(tmp_path)
    (d / "script.json").unlink()
    with pytest.raises(publish.EpisodeError, match="script.json"):
        publish.load_episode(d)


def test_invalid_lens_raises(tmp_path):
    bad = dict(META, lens="finance")
    with pytest.raises(publish.EpisodeError, match="lens"):
        publish.load_episode(make_episode_dir(tmp_path, meta=bad))


def test_invalid_speaker_raises(tmp_path):
    bad = dict(SCRIPT, turns=[{"speaker": "NARRATOR", "text": "hi"}])
    with pytest.raises(publish.EpisodeError, match="speaker"):
        publish.load_episode(make_episode_dir(tmp_path, script=bad))


def test_empty_turns_raises(tmp_path):
    bad = dict(SCRIPT, turns=[])
    with pytest.raises(publish.EpisodeError, match="turns"):
        publish.load_episode(make_episode_dir(tmp_path, script=bad))


def test_bad_date_raises(tmp_path):
    bad = dict(META, date="07/07/2026")
    with pytest.raises(publish.EpisodeError, match="date"):
        publish.load_episode(make_episode_dir(tmp_path, meta=bad))


def test_non_object_episode_json_raises(tmp_path):
    d = make_episode_dir(tmp_path)
    (d / "episode.json").write_text(json.dumps([1, 2, 3]))
    with pytest.raises(publish.EpisodeError, match="episode.json"):
        publish.load_episode(d)


def test_non_object_script_json_raises(tmp_path):
    d = make_episode_dir(tmp_path)
    (d / "script.json").write_text(json.dumps(None))
    with pytest.raises(publish.EpisodeError, match="script.json"):
        publish.load_episode(d)


def test_project_name_defaults_to_titlecased_slug(tmp_path):
    ep = publish.load_episode(make_episode_dir(tmp_path))
    assert ep.project_name == "Clicky"


def test_project_name_titlecases_hyphens_and_underscores(tmp_path):
    meta = dict(META, project="human-harness_cli")
    ep = publish.load_episode(make_episode_dir(tmp_path, meta=meta))
    assert ep.project_name == "Human Harness Cli"


def test_project_name_field_overrides_slug(tmp_path):
    meta = dict(META, project_name="Human Harness CLI")
    ep = publish.load_episode(make_episode_dir(tmp_path, meta=meta))
    assert ep.project_name == "Human Harness CLI"


def test_blank_project_name_falls_back_to_slug(tmp_path):
    meta = dict(META, project_name="   ")
    ep = publish.load_episode(make_episode_dir(tmp_path, meta=meta))
    assert ep.project_name == "Clicky"
