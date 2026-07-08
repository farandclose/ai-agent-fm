# AI Agent FM

*Turn the story of how you built something with an AI coding agent into a private podcast you can actually listen to.*

## Why this exists

When you build a project with an AI coding agent, the finished code is just the residue. The real story — the dead ends, the midnight reversal when you tore out the auth layer, the bug that ate an afternoon, the moment you realized you were solving the wrong problem — is recorded, turn by turn, in your Claude Code **session traces**. And then it's gone. Nobody ever reads a trace back.

AI Agent FM mines those traces and turns them into a podcast episode. An outside host interviews *you* — a version of you reconstructed from your own traces — about what you were trying to do, what it cost, and what almost went wrong. You subscribe in a normal podcast app, and on a walk you hear a third-person take on your own work: not a changelog, a story, with tension and surprises, about a project you know intimately.

It's part keepsake, part review, part genuinely fun. Hearing your own build narrated back has a way of surfacing the decisions you rushed past and the things you actually learned.

You pick the angle, and the host presses from there:

- **Engineering** — how it's built, the tradeoffs, what's fragile, what was clever.
- **Product** — who it's for, what to cut, what's next, where scope got away from you.
- **Sales / Marketing** — who'd pay, the one-line pitch, the objections, the competition.

## How it works

```
┌─ Claude Code skill: /agent-fm <lens> ──────────────────────┐
│  run inside any target project                              │
│  1. locate session traces (~/.claude/projects/<enc>/*.jsonl)│
│  2. write DOSSIER  (markdown brief, trace-first)            │
│  3. write SCRIPT   (host + guest dialogue, script.json)     │
│  4. invoke publish.py                                        │
└─────────────────────────────────────────────────────────────┘
┌─ publish.py (Python, lives in ai-agent-fm) ────────────────┐
│  script.json → TTS (2 voices) → mp3 → upload → feed.xml     │
└─────────────────────────────────────────────────────────────┘
┌─ Cloudflare R2 bucket ─────────────────────────────────────┐
│  episodes/*.mp3 + feed.xml  ← your podcast app polls        │
└─────────────────────────────────────────────────────────────┘
```

The `/agent-fm` skill does all the judgment work — mining traces, deciding what's a story, writing the dialogue. `publish.py` does only the mechanical work — audio, upload, feed. To improve episode quality you edit the skill's prompts, never the Python.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — runs `publish.py` and the tests in a managed environment (`uv run …`).
- **ffmpeg** — encodes the generated audio to MP3 (`brew install ffmpeg`).
- **Claude Code** — the skill runs as `/agent-fm` from inside your projects.

## One-time setup

Everything below is done once. Secrets live only in `.env` (gitignored) — never commit or paste real credential values anywhere else.

1. **Cloudflare R2 bucket.** In the Cloudflare dashboard: R2 → Create bucket (any name, e.g. `my-podcast`; location: automatic).
2. **Public URL.** Bucket → Settings → Public access → **Enable r2.dev subdomain**. Copy the `https://pub-<hash>.r2.dev` URL it gives you.
3. **API token.** R2 → Manage API tokens → Create token with **Object Read & Write** scoped to that bucket. Copy the account ID, access key ID, and secret.
4. **Gemini key.** Get an API key from [Google AI Studio](https://aistudio.google.com/apikey) (only needed if you use the Gemini TTS provider — see below).
5. **Fill secrets.** Copy the env template and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and set `ELEVENLABS_API_KEY` (or `GEMINI_API_KEY`), plus `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
6. **Fill feed config.** Copy the config template and set your own values:
   ```bash
   cp agentfm.example.toml agentfm.toml
   ```
   In `agentfm.toml` set `[feed] author` (your podcast byline), `[feed] public_base_url` (the r2.dev URL from step 2, no trailing slash), and `[r2] bucket` (the bucket name from step 1). `agentfm.toml` is gitignored, so your values stay local.
7. **Symlink the skill** so `/agent-fm` is invocable from any project:
   ```bash
   ln -sfn "$(pwd)/skills/agent-fm" ~/.claude/skills/agent-fm
   ```
   The skill resolves its own repo path from this symlink at runtime, so there's nothing else to configure.

### TTS provider

`agentfm.toml` → `[tts] provider` selects the voice engine: `"elevenlabs"`
(default; needs `ELEVENLABS_API_KEY` in `.env` — free tier works) or
`"gemini"` (needs `GEMINI_API_KEY` on a billing-enabled Google project).
Voice casting lives in `[tts]`/`[tts.guest_voices]` (Gemini voice names)
and `[tts.elevenlabs]` (ElevenLabs voice IDs).

## Making an episode

From inside a target project directory, run in Claude Code:

```
/agent-fm engg      # or: sales | product
```

- **engg** — Engineering Lead: how it's built, tradeoffs, fragility, what was clever.
- **sales** — Sales/Marketing: who pays, the one-line pitch, objections, competition.
- **product** — Product: user value, what to cut, what's next, scope discipline.

The skill locates the project's Claude Code traces, then creates an episode directory under this repo:

```
episodes/<project>-<YYYY-MM-DD>-<lens>/
  dossier.md      # trace-first research brief (the debuggable intermediate)
  script.json     # host/guest dialogue, provider-neutral turns
  episode.json    # metadata: id, title, description, project, lens, date
  episode.mp3     # synthesized audio (generated by publish.py)
```

It then runs `publish.py`, which synthesizes the two-voice audio, uploads the MP3 to R2, updates `episodes.json`, regenerates `feed.xml`, and prints the feed URL. If the project has no traces, the skill offers a weaker code-only episode.

Under the hood the publish step is:

```bash
uv run publish.py publish episodes/<project>-<date>-<lens>
```

Useful flags for iterating without spending on TTS or touching R2:

- `--fake-tts` — deterministic placeholder tone instead of a real TTS call (offline, zero API spend).
- `--no-upload` — build the MP3 locally and stop (no R2, no feed).
- `--republish` — reuse the existing MP3; skip synthesis and only retry upload + feed refresh (see below).

Voice check (real TTS smoke test, writes `check-tts.mp3` at repo root):

```bash
uv run publish.py check-tts "Welcome to AI Agent FM. This is a voice check."
```

## Republish / fix an episode

`episodes.json` is the source of truth; `feed.xml` is regenerated from it idempotently.

**Upload blipped (network failure after the MP3 was built).** The MP3 and its metadata stay local. Retry publishing only — no new TTS run:

```bash
uv run publish.py publish episodes/<project>-<date>-<lens> --republish
```

**Fix a wrong title/description, or pull a bad episode from the feed.** Edit the entry in `episodes.json` (or delete it to remove the episode), then regenerate and re-upload the feed by republishing any current episode:

```bash
# 1. edit episodes.json by hand
# 2. republish to rebuild feed.xml from the manifest and push it to R2
uv run publish.py publish episodes/<any-current-episode> --republish
```

Republish requires the episode's `episode.mp3` and `audio_meta.json` to still exist locally; if they were deleted, run once without `--republish` to re-synthesize.

## Artifact map

Episode content and feed state are **local to your machine** — they're gitignored so your private projects never end up in this repo. The repo ships the tool and templates only.

| Path | What it is | In git? |
|---|---|---|
| `episodes/<ep>/dossier.md`, `script.json`, `episode.json` | Per-episode research, dialogue, and metadata | Local only (gitignored) |
| `episodes/<ep>/episode.mp3`, `.wav` | Synthesized audio | Local only (gitignored) |
| `episodes.json` | Manifest — source of truth for your feed | Local only (gitignored) |
| `feed.xml` | Generated RSS 2.0 feed (regenerated from the manifest) | Local only (gitignored) |
| `.env` | Secrets: TTS + `R2_*` credentials | Local only (gitignored) — template: `.env.example` |
| `agentfm.toml` | Feed metadata, bucket name, voices, `public_base_url` | Local only (gitignored) — template: `agentfm.example.toml` |
| `artwork/cover.jpg` | Show cover art (3000×3000 JPEG, uploaded to R2 on publish) | Committed |
| `skills/agent-fm/`, `publish.py`, `tests/` | The tool itself | Committed |
