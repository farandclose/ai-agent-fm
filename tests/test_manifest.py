import json
from pathlib import Path

import publish


def entry(id: str, date: str) -> dict:
    return {
        "id": id, "title": "T", "description": "D", "project": "p",
        "lens": "engg", "date": date, "mp3_key": f"episodes/{id}.mp3",
        "duration_secs": 60, "bytes": 1000,
    }


def test_creates_file_when_missing(tmp_path):
    path = tmp_path / "episodes.json"
    result = publish.upsert_manifest(path, entry("a-2026-07-07-engg", "2026-07-07"))
    assert len(result) == 1
    assert json.loads(path.read_text())["episodes"] == result


def test_replaces_entry_with_same_id(tmp_path):
    path = tmp_path / "episodes.json"
    publish.upsert_manifest(path, entry("a-2026-07-07-engg", "2026-07-07"))
    updated = entry("a-2026-07-07-engg", "2026-07-07") | {"title": "New title"}
    result = publish.upsert_manifest(path, updated)
    assert len(result) == 1
    assert result[0]["title"] == "New title"


def test_sorted_newest_first(tmp_path):
    path = tmp_path / "episodes.json"
    publish.upsert_manifest(path, entry("old-ep-engg", "2026-07-01"))
    result = publish.upsert_manifest(path, entry("new-ep-engg", "2026-07-07"))
    assert [e["id"] for e in result] == ["new-ep-engg", "old-ep-engg"]
