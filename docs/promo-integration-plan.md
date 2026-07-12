# Promo video → AI Agent FM integration — session handoff plan

> **STATUS: EXECUTED — historical record, do not execute.** Phase 1 landed as
> `3f9cde7` (2026-07-12: three-line caption default, swap regression tests,
> CLAUDE.md promo docs). Phase 2 landed on `main` 2026-07-12 (on-demand
> `## Promo cut` section in `skills/agent-fm/SKILL.md`, per
> `docs/design/promo-on-demand-skill.md` Rev 2). Everything below is the
> original plan text, kept for the rationale and decision trail; its
> working-tree claims and instructions are stale — in particular,
> `promo_video.py`'s CLI default is now `three-line` and CLAUDE.md already
> documents the promo tooling.

Written 2026-07-12 at the end of the promo-caption sample sessions. This doc is
self-contained: a fresh session should be able to execute it without any prior
conversation context. Working-tree state, decisions, and gotchas below are
accurate as of writing.

## Where things stand

- Branch `main` has **uncommitted, PM-approved work**:
  - `promo_video.py` — multi-line caption window behind `--caption-style
    swap|two-line|three-line` (default currently `swap`), including the Rev 2
    amendments (color-only highlight and speaker-based cue bridging for the
    multi-line styles).
  - `tests/test_promo_captions.py` — extended; full suite is **177 passed**,
    fully offline.
  - `docs/design/promo-video-caption-window.md` — the delta spec (Rev 2),
    local-only like all design docs (see policy note below).
- Sample assets live in `episodes/promo-sample-elevenlabs/` (the whole
  `episodes/` tree is gitignored by design — private, never published):
  `episode.mp3` (real ElevenLabs TTS — **cost money, never re-synthesize**),
  `episode.alignment.json` (cached word timings — renders are offline/free),
  `script.json`, `episode.json`, and `promo-square-3line-v2.mp4` (the render
  the PM approved).

## Decisions already made by the PM (do not re-open)

1. **Three-line is the chosen caption style** (dimmed catch-up line above,
   bright focus line, dimmed read-ahead below, eased upward scroll). It
   becomes the **default**; `swap` and `two-line` remain available flags.
2. **Color-only highlight**: the active word is accent-colored (HOST amber /
   GUEST magenta) at base size — no 1.08× scale pop (it ate the word gaps;
   readability wins, per the base spec's brand guardrail).
3. **Speaker-based bridging**: within one speaker's turn, captions hold
   through pauses and only ever scroll — never blank. A blank beat occurs
   exactly at speaker changes. (`compute_cues(..., bridge="speaker")`.)
4. **Promo generation is on-demand** (PM chose option A): the user asks for a
   promo; the `/agent-fm` skill picks the clip (editorial judgment) and
   proposes it before rendering. Not auto-on-publish. R2 upload/distribution
   is explicitly deferred until promos have actually been shared manually.
5. Brand font stays Space Grotesk. Brand geometry questions →
   `docs/design/spectrum-cone-v2/BRAND-SPEC.md`.

## Repo policy notes (verified against .gitignore)

- `docs/design/*` is deliberately local-only (only BRAND-SPEC.md is
  committed). Do **not** add gitignore negations for spec docs; merge the
  delta spec into the main captions spec locally instead.
- `episodes/` is entirely gitignored — sample assets stay local automatically.
- `agentfm.toml` at the repo root is local user config (gitignored), already
  set up with working free-tier ElevenLabs voice IDs.

## Phase 1 — land the approved work (do this first)

1. Flip the default `--caption-style` to `three-line` in `promo_video.py`
   (argparse default + help text + module docstring). Keep `swap` and
   `two-line` selectable. Tests that relied on the default being `swap` must
   now pass the style explicitly — that contract change is deliberate and
   PM-approved; update those tests, keep everything else green.
   Note: `build_promo(style="swap")`'s byte-identical-to-pre-feature guarantee
   still holds and stays tested — only the CLI default moves.
2. Merge `docs/design/promo-video-caption-window.md` (Rev 2) into
   `docs/design/promo-video-captions.md` as its Rev 4, then delete the delta
   file — one local source of truth. Keep the "verified against rendered
   frames — never re-derive" geometry numbers verbatim.
3. Document the promo tool in the repo `CLAUDE.md` (it currently doesn't
   mention `promo_video.py` at all): the render command, caption styles,
   alignment cache behavior (`--align-only` once per episode, sub-cent;
   re-renders offline), and the never-re-synthesize cost rule.
4. **Codex adversarial review** of the full working-tree diff (per the
   AI-dev-workflow playbook). Use the absolute path `~/Library/pnpm/codex` —
   the `codex` on PATH is a stale hermes build. Fix accepted findings.
5. Verify end-to-end, offline:
   ```bash
   uv run pytest
   uv run promo_video.py episodes/promo-sample-elevenlabs/episode.mp3 \
       -o /tmp/verify-3line.mp4 --title "How this podcast builds itself"
   # spot-check frames (approved-behavior moments):
   #   t=6.4  → caption holds through the HOST's mid-turn pause (no blank)
   #   t=9.0  → blank beat at the HOST→GUEST handoff
   #   t=19.9 → "was throwaway junk." — clear gaps around the magenta word
   ffmpeg -y -ss 6.4 -i /tmp/verify-3line.mp4 -frames:v 1 /tmp/f1.png
   ffmpeg -y -ss 9.0 -i /tmp/verify-3line.mp4 -frames:v 1 /tmp/f2.png
   ffmpeg -y -ss 19.9 -i /tmp/verify-3line.mp4 -frames:v 1 /tmp/f3.png
   ```
6. Commit on `main`: code + tests + CLAUDE.md (spec docs and episodes stay
   local per policy).

## Phase 2 — on-demand promo cut (spec first, PM reviews before build)

Write a spec for a new section in `skills/agent-fm/SKILL.md` (editorial
judgment lives in the skill; `promo_video.py` is the mechanics and likely
needs **zero changes**):

- **Trigger**: the user asks for a promo for an episode ("cut a promo for
  <episode>").
- **Clip selection (the judgment)**: scan the episode's `script.json` for the
  strongest self-contained 20–35 s exchange — ideally a hook question and its
  payoff answer (the shape the PM approved in the sample). Use the word
  timings in `episodes/<ep>/episode.alignment.json` to compute exact
  `--start`/`--duration` snapped to turn boundaries with ~0.2–0.3 s padding.
- **Proposal gate**: show the PM the chosen quote, timings, and a suggested
  `--title` line BEFORE rendering; render only on approval.
- **Mechanics**: if no alignment cache exists, run `--align-only` first
  (needs `export SSL_CERT_FILE="$(uv run python -c 'import certifi;
  print(certifi.where())' 2>/dev/null | tail -1)"` — uv's Python has no macOS
  system CAs; the API call is sub-cent). Then render square + vertical
  three-line masters to `episodes/<ep>/promo-square.mp4` and
  `promo-vertical.mp4` (gitignored, local).
- Out of scope: R2 upload, feed/show-notes references, auto-promo-on-publish.

Per the playbook: PM reviews the spec, a fresh Opus subagent builds it cold
(spec-only context), Codex reviews, Claude verifies the real flow.

## Gotchas (all verified live on 2026-07-12)

- **Never re-synthesize TTS to fix anything downstream** — alignment and
  renders never require it; `episodes/promo-sample-elevenlabs/episode.mp3` is
  the reusable test asset.
- ElevenLabs Forced Alignment permission is **enabled** on the key in `.env`
  (was 401 before 2026-07-12; resolved).
- `SSL_CERT_FILE` export (above) is required before ANY real ElevenLabs call.
- `codex` on PATH is stale — use `~/Library/pnpm/codex`.
- ElevenLabs voice IDs in config must be free-tier premade voices (paid
  library voices 401 via API).
- `skills/agent-fm/SKILL.md` hardcodes `AGENTFM_ROOT` as an absolute path.
