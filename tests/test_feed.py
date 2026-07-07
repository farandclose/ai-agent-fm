import xml.etree.ElementTree as ET

import publish
from tests.test_config import VALID_TOML, write_toml

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

EPISODE = {
    "id": "clicky-2026-07-07-engg",
    "title": "Inside Clicky",
    "description": "The engineering story.",
    "project": "clicky",
    "lens": "engg",
    "date": "2026-07-07",
    "mp3_key": "episodes/clicky-2026-07-07-engg.mp3",
    "duration_secs": 725,
    "bytes": 8640000,
}


def make_config(tmp_path):
    return publish.load_config(write_toml(tmp_path, VALID_TOML))


def test_channel_metadata(tmp_path):
    root = ET.fromstring(publish.generate_feed(make_config(tmp_path), []))
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel.findtext("title") == "AI Agent FM"
    assert channel.findtext("description") == "Test feed"
    assert channel.findtext("link") == "https://pub-abc.r2.dev"


def test_item_fields(tmp_path):
    root = ET.fromstring(publish.generate_feed(make_config(tmp_path), [EPISODE]))
    item = root.find("channel/item")
    assert item.findtext("title") == "Inside Clicky"
    guid = item.find("guid")
    assert guid.text == "clicky-2026-07-07-engg"
    assert guid.get("isPermaLink") == "false"
    enc = item.find("enclosure")
    assert enc.get("url") == "https://pub-abc.r2.dev/episodes/clicky-2026-07-07-engg.mp3"
    assert enc.get("length") == "8640000"
    assert enc.get("type") == "audio/mpeg"
    assert item.findtext(f"{ITUNES}duration") == "725"
    assert item.findtext("pubDate").endswith("+0000")
    assert "2026" in item.findtext("pubDate")


def test_items_in_manifest_order(tmp_path):
    second = EPISODE | {"id": "older-ep", "title": "Older", "mp3_key": "episodes/older-ep.mp3"}
    root = ET.fromstring(publish.generate_feed(make_config(tmp_path), [EPISODE, second]))
    titles = [i.findtext("title") for i in root.findall("channel/item")]
    assert titles == ["Inside Clicky", "Older"]


def test_channel_artwork_when_cover_url_given(tmp_path):
    root = ET.fromstring(
        publish.generate_feed(
            make_config(tmp_path), [], cover_url="https://pub-abc.r2.dev/cover.jpg"
        )
    )
    channel = root.find("channel")
    itunes_img = channel.find(f"{ITUNES}image")
    assert itunes_img.get("href") == "https://pub-abc.r2.dev/cover.jpg"
    image = channel.find("image")
    assert image.findtext("url") == "https://pub-abc.r2.dev/cover.jpg"
    assert image.findtext("title") == "AI Agent FM"
    assert image.findtext("link") == "https://pub-abc.r2.dev"


def test_no_artwork_tags_without_cover_url(tmp_path):
    root = ET.fromstring(publish.generate_feed(make_config(tmp_path), []))
    channel = root.find("channel")
    assert channel.find(f"{ITUNES}image") is None
    assert channel.find("image") is None
