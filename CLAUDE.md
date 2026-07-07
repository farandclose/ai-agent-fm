# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI Agent FM turns a project built with an AI coding agent into a private podcast episode: the `/agent-fm` skill mines Claude Code session traces into a host/guest dialogue, and `publish.py` synthesizes it to MP3 and publishes an RSS feed on Cloudflare R2.

## Commands

```bash
uv run pytest                                  # run all tests (offline, no network/API keys needed)
uv run pytest tests/test_feed.py               # one test file
uv run pytest tests/test_feed.py::test_name    # one test

uv run publish.py publish episodes/<ep-dir>              # full pipeline: TTS → mp3 → R2 upload → feed
uv run publish.py publish episodes/<ep-dir> --fake-tts   # deterministic tone instead of real TTS (zero spend)
uv run publish.py publish episodes/<ep-dir> --no-upload  # build mp3 locally, skip R2 and feed
uv run publish.py publish episodes/<ep-dir> --republish  # reuse existing mp3; retry upload + feed only
uv run publish.py check-tts "some text"                  # real-TTS smoke test → check-tts.mp3
```

`ffmpeg` must be on PATH (mp3 encoding). Real TTS/upload runs read secrets from `.env` (gitignored): `ELEVENLABS_API_KEY` or `GEMINI_API_KEY`, plus `R2_ACCOUNT_ID`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`.

**TTS costs money.** Never re-synthesize to fix an upload failure — that's what `--republish` is for. Iterate on scripts with `--fake-tts`.

## Architecture

The system is deliberately split into a judgment half and a mechanical half:

- **`skills/agent-fm/SKILL.md`** (+ `personas/{engg,sales,product}.md`) — all editorial judgment: trace mining, dossier writing, host-brief research, dialogue writing, conversational-quality rules. Runs as `/agent-fm <lens>` from inside any target project (symlinked to `~/.claude/skills/agent-fm`), writes episode artifacts into this repo's `episodes/`, then invokes `publish.py`.
- **`publish.py`** — all mechanics, in one file: config/env loading, episode validation, chunking, TTS, WAV→MP3, R2 upload, manifest, RSS feed. Stdlib-first; only deps are `boto3` and `google-genai` (both imported lazily so `--fake-tts`/`--no-upload` runs stay light).

**To improve episode quality, edit the skill prompts (and `docs/transcript-quality-goal.md`, the scoring rubric) — never the Python.**

### publish.py pipeline

`script.json` turns → `chunk_turns()` (greedy, never splits a turn) → per-chunk TTS with retry/backoff (`synthesize_all` — all chunks must succeed before anything is written) → PCM concat (shared format everywhere: 24 kHz, 16-bit LE, mono) → WAV → ffmpeg MP3 → `make_cover()` (project-locked backdrop + typography) → upload mp3 + cover → `upsert_manifest()` → `generate_feed()` → upload feed.

Two TTS providers, selected by `agentfm.toml [tts] provider`, with different call shapes:
- **elevenlabs** (default): raw `urllib` POST to the Text-to-Dialogue API — takes the turns list directly with a `voice_id` per input, 2,000-char chunks, model `eleven_v3` (supports `[laughs]`-style audio tags). Voice IDs live in `[tts.elevenlabs]`.
- **gemini**: `google-genai` SDK with a rendered `HOST:`/`GUEST:` prompt (`build_tts_prompt`) and a multi-speaker voice config keyed by those speaker labels.

Error handling contract: every user-facing failure raises an `AgentFMError` subclass (`ConfigError`, `EpisodeError`, `TTSError`, `AudioError`, `UploadError`) with a plain-English, actionable message; `main()` catches only those → `error: …` on stderr, exit 1. Anything else propagating is a bug. Never echo credential values in errors.

### State and data flow

- `episodes.json` (committed) is the **source of truth** for the feed; `feed.xml` is regenerated from it idempotently. Entries carry `cover_key` (the episode's uploaded art key), and item-level artwork appears in the feed only for episodes that have it. To fix/pull an episode: edit `episodes.json` by hand, then `--republish` any current episode to push the rebuilt feed.
- Per-episode dir: `dossier.md` (guest's inside-out knowledge), `host-brief.md` (host's outside-in research — the two docs create real information asymmetry in the dialogue), `script.json` (provider-neutral `{speaker, text}` turns, speakers only `HOST`/`GUEST`), `episode.json` (metadata; `lens` must be `engg|sales|product`), `audio_meta.json` + `episode.mp3` + `cover.jpg` (generated episode art; audio and cover are gitignored).
- `agentfm.toml` — feed metadata, bucket, voice casting. `load_config` collects *all* missing keys into one error rather than failing on the first.

### Tests

Fully offline. ElevenLabs tests monkeypatch the module-level `urllib.request.urlopen` (which is why `elevenlabs_tts` calls it unaliased); TTS pipeline tests inject `fake_tts`; R2 tests stub the boto3 client. Keep that property — no test should need network or keys.

### Gotchas

- The ElevenLabs call clears `ssl.VERIFY_X509_STRICT` (verification stays on) so corporate-proxy CAs work on Python 3.13 — don't "clean up" that context.
- ElevenLabs voice IDs in config must be free-tier premade voices; paid library voices fail via API (commit 0d13dc2).
- `SKILL.md` hardcodes `AGENTFM_ROOT` as an absolute path — it must be updated if the repo moves.
- `artwork/backdrops/` (12 committed JPEGs) and `artwork/fonts/SpaceGrotesk-Bold.ttf` are runtime dependencies of every publish — deleting them breaks `make_cover` with a `ConfigError`; regenerate backdrops with `uv run artwork/make_backdrops.py`.
