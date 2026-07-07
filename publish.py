"""AI Agent FM.

Single-file CLI that turns a coding project into a podcast episode:
script -> TTS -> mp3 -> RSS feed on Cloudflare R2.

This module holds the shared error taxonomy plus configuration and
environment loading. Later tasks extend it with the episode, TTS, audio,
manifest, feed, upload, and CLI machinery.
"""

import argparse
import datetime
import email.utils
import functools
import json
import math
import os
import ssl
import struct
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import wave
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class AgentFMError(Exception):
    """Base class for all agent-fm errors."""


class ConfigError(AgentFMError):
    """Raised when configuration is missing or invalid."""


class EpisodeError(AgentFMError):
    """Raised when an episode is missing or invalid."""


class TTSError(AgentFMError):
    """Raised when text-to-speech synthesis fails."""


class AudioError(AgentFMError):
    """Raised when audio processing fails."""


class UploadError(AgentFMError):
    """Raised when uploading to storage fails."""


@dataclass(frozen=True)
class Config:
    """Static configuration loaded from ``agentfm.toml``."""

    feed_title: str
    feed_description: str
    feed_author: str
    public_base_url: str
    bucket: str
    tts_model: str
    host_voice: str
    guest_voices: dict[str, str]
    tts_provider: str
    el_host_voice_id: str
    el_guest_voice_ids: dict[str, str]


@dataclass(frozen=True)
class Episode:
    """A single episode loaded from an episode directory."""

    dir: Path
    id: str
    title: str
    description: str
    project: str
    project_name: str  # Human display name rendered on the episode cover art.
    lens: str
    date: str
    turns: list[dict]


# Sentinel distinguishing "key absent" from a present empty/false value.
_MISSING = object()

# Required guest lenses; each maps to a distinct TTS voice.
_REQUIRED_GUEST_LENSES = ("engg", "sales", "product")

# Allowed values for the editorial lens and speaker labels.
_ALLOWED_LENSES = frozenset(_REQUIRED_GUEST_LENSES)
_ALLOWED_SPEAKERS = frozenset(("HOST", "GUEST"))

# Fields required (present and non-empty) in ``episode.json``.
_EPISODE_META_FIELDS = ("id", "title", "description", "project", "lens", "date")


def load_config(path: Path) -> Config:
    """Load and validate configuration from a TOML file.

    Collects every missing key as a dotted path and, if any are missing,
    raises a single ``ConfigError`` naming all of them. An empty-string
    ``feed.public_base_url`` is valid here (it is checked at upload time).
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)

    missing: list[str] = []

    def get(section: dict, table: str, key: str):
        """Return section[key] or record the dotted path as missing."""
        value = section.get(key, _MISSING)
        if value is _MISSING:
            missing.append(f"{table}.{key}")
            return None
        return value

    feed = data.get("feed", {})
    feed_title = get(feed, "feed", "title")
    feed_description = get(feed, "feed", "description")
    feed_author = get(feed, "feed", "author")
    public_base_url = get(feed, "feed", "public_base_url")

    r2 = data.get("r2", {})
    bucket = get(r2, "r2", "bucket")

    tts = data.get("tts", {})
    tts_model = get(tts, "tts", "model")
    host_voice = get(tts, "tts", "host_voice")

    guest_voices_raw = tts.get("guest_voices", {})
    guest_voices: dict[str, str] = {}
    for lens in _REQUIRED_GUEST_LENSES:
        voice = guest_voices_raw.get(lens, _MISSING)
        if voice is _MISSING:
            missing.append(f"tts.guest_voices.{lens}")
        else:
            guest_voices[lens] = voice

    provider = tts.get("provider", "gemini")
    if provider not in ("gemini", "elevenlabs"):
        raise ConfigError(
            f"unknown tts.provider '{provider}' — expected 'gemini' or 'elevenlabs'"
        )

    elevenlabs = tts.get("elevenlabs", {})
    el_host_voice_id = elevenlabs.get("host_voice_id", "")
    el_guest_voice_ids: dict[str, str] = {}
    el_guest_raw = elevenlabs.get("guest_voice_ids", {})
    if provider == "elevenlabs":
        host_id = elevenlabs.get("host_voice_id", _MISSING)
        if host_id is _MISSING:
            missing.append("tts.elevenlabs.host_voice_id")
        else:
            el_host_voice_id = host_id
        for lens in _REQUIRED_GUEST_LENSES:
            voice = el_guest_raw.get(lens, _MISSING)
            if voice is _MISSING:
                missing.append(f"tts.elevenlabs.guest_voice_ids.{lens}")
            else:
                el_guest_voice_ids[lens] = voice
    else:
        for lens in _REQUIRED_GUEST_LENSES:
            if lens in el_guest_raw:
                el_guest_voice_ids[lens] = el_guest_raw[lens]

    if missing:
        raise ConfigError("missing required config keys: " + ", ".join(missing))

    return Config(
        feed_title=feed_title,
        feed_description=feed_description,
        feed_author=feed_author,
        public_base_url=public_base_url,
        bucket=bucket,
        tts_model=tts_model,
        host_voice=host_voice,
        guest_voices=guest_voices,
        tts_provider=provider,
        el_host_voice_id=el_host_voice_id,
        el_guest_voice_ids=el_guest_voice_ids,
    )


def load_env(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from a ``.env`` file into ``os.environ``.

    Blank lines and ``#`` comments are skipped. Each key is set via
    ``setdefault`` so existing environment variables are never overwritten.
    Values may contain ``=`` (only the first ``=`` splits key from value).
    A missing file is a silent no-op.
    """
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _load_json(path: Path) -> dict:
    """Read and parse a JSON file, raising ``EpisodeError`` naming the file."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise EpisodeError(f"{path.name} not found in episode dir: {path.parent}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EpisodeError(f"{path.name} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise EpisodeError(
            f"{path.name} must contain a JSON object, not {type(data).__name__}"
        )
    return data


def load_episode(episode_dir: Path) -> Episode:
    """Load and validate an episode from its directory.

    Reads ``episode.json`` (metadata) and ``script.json`` (dialogue). Raises
    ``EpisodeError`` with a plain-English message on any invalid input: a
    missing or malformed file, a missing or empty metadata field, a ``lens``
    outside ``engg|sales|product``, a ``date`` not in ``YYYY-MM-DD`` form, an
    empty ``turns`` list, or a turn with an unknown ``speaker`` or empty text.
    """
    episode_dir = Path(episode_dir)
    meta = _load_json(episode_dir / "episode.json")
    script = _load_json(episode_dir / "script.json")

    values: dict[str, str] = {}
    for field in _EPISODE_META_FIELDS:
        value = meta.get(field, _MISSING)
        if value is _MISSING or not isinstance(value, str) or not value.strip():
            raise EpisodeError(
                f"episode.json is missing a non-empty '{field}' field"
            )
        values[field] = value

    if values["lens"] not in _ALLOWED_LENSES:
        raise EpisodeError(
            f"episode.json has invalid lens '{values['lens']}'; "
            f"must be one of engg|sales|product"
        )

    try:
        datetime.date.fromisoformat(values["date"])
    except ValueError:
        raise EpisodeError(
            f"episode.json has invalid date '{values['date']}'; "
            f"must be in YYYY-MM-DD form"
        )

    # Optional human display name for cover art; fall back to the derived
    # slug when absent, non-string, or blank (never raises).
    raw_name = meta.get("project_name")
    if isinstance(raw_name, str) and raw_name.strip():
        project_name = raw_name.strip()
    else:
        project_name = values["project"].replace("-", " ").replace("_", " ").title()

    turns = script.get("turns")
    if not isinstance(turns, list) or not turns:
        raise EpisodeError("script.json must have a non-empty 'turns' list")

    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise EpisodeError(f"script.json turn {i} is not an object")
        speaker = turn.get("speaker")
        if speaker not in _ALLOWED_SPEAKERS:
            raise EpisodeError(
                f"script.json turn {i} has invalid speaker '{speaker}'; "
                f"must be HOST or GUEST"
            )
        text = turn.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EpisodeError(f"script.json turn {i} has empty text")

    return Episode(
        dir=episode_dir,
        id=values["id"],
        title=values["title"],
        description=values["description"],
        project=values["project"],
        project_name=project_name,
        lens=values["lens"],
        date=values["date"],
        turns=turns,
    )


def chunk_turns(turns: list[dict], max_chars: int = 3000) -> list[list[dict]]:
    """Greedily group turns into chunks no larger than ``max_chars``.

    Chunking exists so each chunk can be synthesized in its own TTS call,
    avoiding output-length limits; the resulting PCM is byte-concatenated
    (identical format throughout, so this is safe). Turns are never split
    mid-turn: the current chunk is closed before a turn that would push it
    over ``max_chars``, and a single turn longer than ``max_chars`` becomes
    its own chunk. Never returns empty chunks.
    """
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for turn in turns:
        turn_len = len(turn["text"])
        if current and current_len + turn_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(turn)
        current_len += turn_len

    if current:
        chunks.append(current)

    return chunks


def build_tts_prompt(turns: list[dict]) -> str:
    """Render turns as the multi-speaker TTS prompt string.

    A header line naming the two speakers, then one ``SPEAKER: text`` line
    per turn, newline-joined with no trailing newline. The speaker labels
    match the ``speaker`` field in the API's voice config, which is how
    Gemini maps voices to lines.
    """
    lines = ["TTS the following podcast conversation between HOST and GUEST:"]
    for turn in turns:
        lines.append(f"{turn['speaker']}: {turn['text']}")
    return "\n".join(lines)


# PCM audio format used everywhere: 24000 Hz, 16-bit signed LE, mono.
SAMPLE_RATE = 24000


def fake_tts(prompt: str) -> bytes:
    """Return deterministic placeholder PCM audio for a prompt.

    A stand-in for the real Gemini TTS call (Task 5): it produces a 440 Hz
    sine tone in the shared PCM format (24000 Hz, 16-bit signed LE, mono),
    with duration ``max(1, len(prompt) // 100)`` seconds. Output depends only
    on the prompt length, so it is fully deterministic — handy for tests and
    offline runs. Uses stdlib ``math``/``struct`` only (no numpy).
    """
    seconds = max(1, len(prompt) // 100)
    frames = SAMPLE_RATE * seconds
    amplitude = 10000  # ≤ 10000, comfortably inside the 16-bit range
    out = bytearray()
    for n in range(frames):
        sample = int(amplitude * math.sin(2 * math.pi * 440 * n / SAMPLE_RATE))
        out += struct.pack("<h", sample)
    return bytes(out)


def gemini_tts(prompt: str, cfg: Config, lens: str) -> bytes:
    """Synthesize a multi-speaker TTS prompt via Gemini, returning raw PCM.

    Calls the ``google-genai`` SDK with a two-speaker voice config: the
    ``HOST:`` lines get ``cfg.host_voice`` and the ``GUEST:`` lines get
    ``cfg.guest_voices[lens]`` (the speaker labels here must match those in
    the prompt built by ``build_tts_prompt``). Returns the inline audio bytes
    of the first candidate in the shared PCM format (24000 Hz, 16-bit LE,
    mono). The SDK is imported lazily so ``--fake-tts`` runs never touch it.

    Raises ``TTSError`` (never a raw SDK exception) when ``GEMINI_API_KEY`` is
    unset, the SDK call fails, or the response carries no audio.
    """
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise TTSError(
            "GEMINI_API_KEY is not set — add it to your .env file "
            "(get a key at https://aistudio.google.com/apikey)"
        )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=cfg.tts_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                        speaker_voice_configs=[
                            types.SpeakerVoiceConfig(
                                speaker="HOST",
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=cfg.host_voice
                                    )
                                ),
                            ),
                            types.SpeakerVoiceConfig(
                                speaker="GUEST",
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=cfg.guest_voices[lens]
                                    )
                                ),
                            ),
                        ]
                    )
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — wrap any SDK failure
        raise TTSError(f"Gemini TTS request failed: {exc}") from exc

    try:
        audio = response.candidates[0].content.parts[0].inline_data.data
    except (AttributeError, IndexError, TypeError):
        audio = None
    if not audio:
        raise TTSError(
            "Gemini TTS returned no audio (empty or malformed response)"
        )
    return audio


def elevenlabs_tts(turns: list[dict], cfg: Config, lens: str) -> bytes:
    """Synthesize one chunk of dialogue turns via ElevenLabs, returning raw PCM.

    Posts the turns to the Text-to-Dialogue API as a list of ``{"text",
    "voice_id"}`` inputs — ``HOST`` lines get ``cfg.el_host_voice_id`` and
    ``GUEST`` lines get ``cfg.el_guest_voice_ids[lens]``. Unlike ``gemini_tts``
    this takes the turns directly (not a rendered prompt), because the API maps
    voices per input rather than by speaker label. Requests
    ``output_format=pcm_24000``, so the response bytes are the shared PCM format
    (24000 Hz, 16-bit LE, mono) and are returned unchanged. Uses stdlib
    ``urllib`` only (no SDK); the module-level ``urllib.request.urlopen`` is
    called so tests can monkeypatch it.

    Raises ``TTSError`` (never a raw exception, and never echoing the API key)
    when ``ELEVENLABS_API_KEY`` is unset or the request fails.
    """
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise TTSError("ELEVENLABS_API_KEY missing — add it to .env")

    voice_for = {"HOST": cfg.el_host_voice_id, "GUEST": cfg.el_guest_voice_ids[lens]}
    body = json.dumps(
        {
            "inputs": [
                {"text": t["text"], "voice_id": voice_for[t["speaker"]]}
                for t in turns
            ],
            "model_id": "eleven_v3",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/text-to-dialogue?output_format=pcm_24000",
        data=body,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )

    # Python 3.13 turned on VERIFY_X509_STRICT by default, which rejects
    # corporate proxy CAs (e.g. Zscaler) whose Basic Constraints extension is
    # not marked critical. Clear just the strict bit — certificate
    # verification itself stays fully ON (this is the pre-3.13 default level,
    # matching curl/urllib3).
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        tail = exc.read()[:200].decode("utf-8", "replace")
        raise TTSError(
            f"ElevenLabs TTS request failed: HTTP {exc.code}: {tail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TTSError(f"ElevenLabs TTS request failed: {exc.reason}") from exc


def synthesize_all(
    prompts: list[str],
    synth,
    attempts: int = 3,
    sleep=time.sleep,
) -> bytes:
    """Synthesize every prompt and return the concatenated PCM.

    Calls ``synth(prompt)`` for each prompt, retrying that prompt up to
    ``attempts`` times. Between failed tries it waits ``sleep(2 ** try_index)``
    (exponential backoff; ``try_index`` is 0-based). If a prompt still fails
    after ``attempts`` tries, raises ``TTSError`` naming the 1-based chunk
    number and wrapping the last exception. Nothing is returned unless every
    chunk succeeded — the caller must have the full audio before anything is
    written or uploaded (spec §4.4).
    """
    parts: list[bytes] = []
    for index, prompt in enumerate(prompts):
        last_exc: Exception | None = None
        for try_index in range(attempts):
            try:
                parts.append(synth(prompt))
                break
            except Exception as exc:  # noqa: BLE001 — retry any synth failure
                last_exc = exc
                if try_index < attempts - 1:
                    sleep(2 ** try_index)
        else:
            raise TTSError(
                f"TTS failed for chunk {index + 1} after {attempts} attempts: "
                f"{last_exc}"
            ) from last_exc
    return b"".join(parts)


def write_wav(pcm: bytes, path: Path) -> None:
    """Write raw PCM bytes to a WAV file in the shared format.

    Mono, 16-bit samples, ``SAMPLE_RATE`` frame rate.
    """
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def wav_duration_secs(path: Path) -> int:
    """Return the duration of a WAV file rounded to the nearest second."""
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate())


def upsert_manifest(manifest_path: Path, entry: dict) -> list[dict]:
    """Upsert ``entry`` into the ``episodes.json`` manifest and persist it.

    The manifest is the source of truth for the RSS feed. When the file is
    absent it starts from an empty episode list. The entry replaces any
    existing episode with the same ``id`` (or is appended otherwise), then the
    list is sorted by ``date`` descending with ``id`` ascending as a stable
    tie-breaker. The manifest is written back with ``indent=2`` and a trailing
    newline, and the full episode list is returned.
    """
    if manifest_path.exists():
        episodes = json.loads(manifest_path.read_text())["episodes"]
    else:
        episodes = []

    episodes = [e for e in episodes if e["id"] != entry["id"]]
    episodes.append(entry)
    # Stable sort: id ascending, then date descending wins overall.
    episodes.sort(key=lambda e: e["id"])
    episodes.sort(key=lambda e: e["date"], reverse=True)

    manifest_path.write_text(json.dumps({"episodes": episodes}, indent=2) + "\n")
    return episodes


_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

# Serialize the itunes namespace with its conventional prefix rather than
# ElementTree's default ``ns0``; podcast apps expect ``itunes:``.
ET.register_namespace("itunes", _ITUNES_NS)


def generate_feed(
    cfg: Config, episodes: list[dict], cover_url: str | None = None
) -> str:
    """Render the manifest episodes as a complete RSS 2.0 podcast feed.

    Produces the XML document podcast apps poll to discover episodes. The
    channel carries the feed's title/description/link plus ``language`` (en)
    and ``itunes:author``. Each episode becomes an ``item`` in manifest order
    with a stable ``guid`` (``isPermaLink="false"``, the episode id), an
    ``enclosure`` pointing at ``public_base_url/mp3_key`` (byte length and
    ``audio/mpeg`` type), ``itunes:duration`` in seconds, and a ``pubDate`` of
    the episode date at 06:00:00 UTC in RFC 2822 form. When ``cover_url`` is
    given, the channel also carries show artwork as both an ``itunes:image``
    (href attribute) and an RSS ``image`` element (whose title/link mirror the
    channel's own). Returned as a string with an XML declaration.
    """
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = cfg.feed_title
    ET.SubElement(channel, "description").text = cfg.feed_description
    ET.SubElement(channel, "link").text = cfg.public_base_url
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, f"{{{_ITUNES_NS}}}author").text = cfg.feed_author

    if cover_url:
        ET.SubElement(channel, f"{{{_ITUNES_NS}}}image", {"href": cover_url})
        image = ET.SubElement(channel, "image")
        ET.SubElement(image, "url").text = cover_url
        ET.SubElement(image, "title").text = cfg.feed_title
        ET.SubElement(image, "link").text = cfg.public_base_url

    for ep in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep["description"]
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = ep["id"]

        pub_dt = datetime.datetime.fromisoformat(ep["date"]).replace(
            hour=6, minute=0, second=0, tzinfo=datetime.timezone.utc
        )
        ET.SubElement(item, "pubDate").text = email.utils.format_datetime(pub_dt)

        ET.SubElement(
            item,
            "enclosure",
            {
                "url": cfg.public_base_url + "/" + ep["mp3_key"],
                "length": str(ep["bytes"]),
                "type": "audio/mpeg",
            },
        )
        ET.SubElement(item, f"{{{_ITUNES_NS}}}duration").text = str(
            ep["duration_secs"]
        )

    return ET.tostring(rss, encoding="unicode", xml_declaration=True)


def encode_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Encode a WAV file to MP3 via ffmpeg (libmp3lame, VBR ~q4).

    Raises ``AudioError`` if ffmpeg returns non-zero (including the tail of
    its stderr) or if the ffmpeg binary is not on PATH.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-qscale:a",
                "4",
                str(mp3_path),
            ],
            capture_output=True,
        )
    except FileNotFoundError:
        raise AudioError("ffmpeg not found — brew install ffmpeg")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")[-2000:]
        raise AudioError(
            f"ffmpeg failed encoding {wav_path.name} (exit {result.returncode}):"
            f"\n{stderr}"
        )


def make_r2_client():
    """Build a boto3 S3 client pointed at this account's Cloudflare R2 endpoint.

    R2 speaks the S3 API, so we use boto3's ``s3`` client with a per-account
    endpoint ``https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`` and
    ``region_name="auto"`` (R2 ignores region but boto3 requires one).
    Credentials come from the environment (loaded from ``.env`` earlier).

    Collects every missing ``R2_*`` env var into one ``UploadError`` naming
    them — never echoes any credential value. boto3 is imported lazily so
    ``--no-upload`` runs stay import-light.
    """
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID", access_key_id),
            ("R2_SECRET_ACCESS_KEY", secret_access_key),
        )
        if not value
    ]
    if missing:
        raise UploadError(
            "missing required R2 environment variables: "
            + ", ".join(missing)
            + " — add them to your .env file"
        )

    import boto3

    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def upload_file(client, bucket: str, key: str, path: Path, content_type: str) -> None:
    """Upload ``path`` to ``bucket/key`` via ``put_object`` with ``content_type``.

    Opens the file in binary and streams the handle to the S3-compatible
    client. Any failure surfaces as ``UploadError`` — never a raw exception:
    local file errors (``OSError``, e.g. the file was deleted) name the path,
    and boto3 failures (``ClientError`` for API-level errors, ``BotoCoreError``
    for transport/config errors) name the bucket and key.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        with open(path, "rb") as body:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
    except OSError as exc:
        raise UploadError(f"could not read local file {path}: {exc}") from exc
    except (ClientError, BotoCoreError) as exc:
        raise UploadError(
            f"failed to upload {key} to bucket {bucket}: {exc}"
        ) from exc


def _run_publish(args: argparse.Namespace) -> int:
    """Execute the ``publish`` subcommand; see the module CLI contract.

    Synthesizes (unless ``--republish``), encodes, and either stops after the
    local mp3 (``--no-upload``) or uploads the mp3, upserts the manifest,
    regenerates the feed, and uploads the feed.
    """
    root = Path(args.root)
    load_env(root / ".env")
    cfg = load_config(root / "agentfm.toml")
    ep = load_episode(Path(args.episode_dir))

    wav_path = ep.dir / "episode.wav"
    mp3_path = ep.dir / "episode.mp3"
    meta_path = ep.dir / "audio_meta.json"

    if args.republish:
        if not mp3_path.exists() or not meta_path.exists():
            raise EpisodeError(
                "cannot republish: episode.mp3 or audio_meta.json is missing — "
                "run without --republish first to synthesize the audio"
            )
        duration_secs = json.loads(meta_path.read_text())["duration_secs"]
    else:
        if args.fake_tts:
            items = [build_tts_prompt(chunk) for chunk in chunk_turns(ep.turns)]
            synth = fake_tts
        elif cfg.tts_provider == "elevenlabs":
            items = chunk_turns(ep.turns, max_chars=2000)  # ElevenLabs caps requests at 2,000 chars
            synth = functools.partial(elevenlabs_tts, cfg=cfg, lens=ep.lens)
        else:
            items = [build_tts_prompt(chunk) for chunk in chunk_turns(ep.turns)]
            synth = functools.partial(gemini_tts, cfg=cfg, lens=ep.lens)
        pcm = synthesize_all(items, synth)
        write_wav(pcm, wav_path)
        duration_secs = wav_duration_secs(wav_path)
        encode_mp3(wav_path, mp3_path)
        meta_path.write_text(json.dumps({"duration_secs": duration_secs}) + "\n")
        wav_path.unlink()

    if args.no_upload:
        print(mp3_path)
        return 0

    if not cfg.public_base_url:
        raise ConfigError("run R2 setup first — see README")

    client = make_r2_client()
    mp3_key = f"episodes/{ep.id}.mp3"
    upload_file(client, cfg.bucket, mp3_key, mp3_path, "audio/mpeg")

    entry = {
        "id": ep.id,
        "title": ep.title,
        "description": ep.description,
        "project": ep.project,
        "lens": ep.lens,
        "date": ep.date,
        "mp3_key": mp3_key,
        "duration_secs": duration_secs,
        "bytes": mp3_path.stat().st_size,
    }
    episodes = upsert_manifest(root / "episodes.json", entry)

    cover_path = root / "artwork" / "cover.jpg"
    if cover_path.exists():
        upload_file(client, cfg.bucket, "cover.jpg", cover_path, "image/jpeg")
        cover_url = cfg.public_base_url + "/cover.jpg"
    else:
        cover_url = None

    feed_path = root / "feed.xml"
    feed_path.write_text(generate_feed(cfg, episodes, cover_url=cover_url))
    upload_file(client, cfg.bucket, "feed.xml", feed_path, "application/rss+xml")

    print(f"Feed: {cfg.public_base_url}/feed.xml")
    return 0


def _run_check_tts(args: argparse.Namespace) -> int:
    """Execute the ``check-tts`` subcommand: a real-TTS smoke test.

    Synthesizes the given text as a single ``HOST:`` line via the
    provider-dependent TTS engine (``elevenlabs_tts`` or ``gemini_tts``,
    selected by ``cfg.tts_provider``), using the host voice plus the ``engg``
    guest voice from config, encodes it to ``check-tts.mp3`` under ``--root``,
    and prints its path.
    """
    root = Path(args.root)
    load_env(root / ".env")
    cfg = load_config(root / "agentfm.toml")

    if cfg.tts_provider == "elevenlabs":
        turns = [{"speaker": "HOST", "text": args.text}]
        pcm = elevenlabs_tts(turns, cfg, "engg")
    else:
        prompt = build_tts_prompt([{"speaker": "HOST", "text": args.text}])
        pcm = gemini_tts(prompt, cfg, "engg")

    wav_path = root / "check-tts.wav"
    mp3_path = root / "check-tts.mp3"
    write_wav(pcm, wav_path)
    encode_mp3(wav_path, mp3_path)
    wav_path.unlink()

    print(mp3_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the publish pipeline.

    Dispatches the ``publish`` and ``check-tts`` subcommands. User-facing
    failures raise ``AgentFMError`` subclasses; those are caught here, printed
    to stderr as ``error: <message>``, and turned into exit code 1. Any other
    exception propagates unhandled — it is a bug, not a user error.
    """
    default_root = Path(__file__).parent

    parser = argparse.ArgumentParser(prog="publish.py", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_publish = subparsers.add_parser(
        "publish", help="synthesize, encode, and publish an episode"
    )
    p_publish.add_argument("episode_dir", help="path to the episode directory")
    p_publish.add_argument(
        "--fake-tts", action="store_true", help="use deterministic placeholder audio"
    )
    p_publish.add_argument(
        "--no-upload", action="store_true", help="build the mp3 locally and stop"
    )
    p_publish.add_argument(
        "--republish",
        action="store_true",
        help="reuse the existing mp3 instead of re-synthesizing",
    )
    p_publish.add_argument(
        "--root", default=str(default_root), help="repo root holding config and feed"
    )
    p_publish.set_defaults(func=_run_publish)

    p_check = subparsers.add_parser(
        "check-tts", help="real-TTS smoke test writing check-tts.mp3"
    )
    p_check.add_argument("text", help="text to synthesize as a HOST line")
    p_check.add_argument(
        "--root", default=str(default_root), help="repo root holding config"
    )
    p_check.set_defaults(func=_run_check_tts)

    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except AgentFMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
