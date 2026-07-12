---
name: agent-fm
description: Use when the user wants a podcast episode about the current project (/agent-fm engg|sales|product) — mines Claude Code session traces + code into a host/guest dialogue and publishes it to the private AI Agent FM feed — or when they want to cut a short promo video from an already-published episode ("cut a promo for <episode>").
---

# AI Agent FM — Episode Generator

You are producing a podcast episode about THIS project (the current working
directory) for its own builder. The episode is a story, not a review: the
GUEST is the builder themselves — reconstructed from their own session
traces — interviewed by an outside HOST. The listener should finish knowing
what the builder wanted, what it cost, and what almost went wrong.

**Governing principles:** `AGENTFM_ROOT/docs/podcast-principles.md`. Read it
before Step 3 and keep it open through Step 6.5. The one-line version:
connection comes from unresolved motion, not resolved insight. When any rule
below conflicts with a principle, the principle wins.

**Constants:** AGENTFM_ROOT = the absolute path to the `ai-agent-fm` repo that
holds this skill. Resolve it at runtime — this skill is invoked from the
symlink `~/.claude/skills/agent-fm`, so:
`AGENTFM_ROOT="$(dirname "$(dirname "$(readlink ~/.claude/skills/agent-fm)")")"`
Compute it once at the start and substitute the real value wherever
`AGENTFM_ROOT` appears below. (This keeps the checked-in skill path-free.)

## Arguments

`/agent-fm <lens>` where lens ∈ `engg | sales | product`. Exception: `promo` as the
first argument (`/agent-fm promo <episode-dir>`), or a natural-language promo
request ("cut a promo for <episode>") — either is not a lens; skip lens
validation and Steps 1–7 and jump to `## Promo cut (on-demand)`.
Otherwise, if the lens is missing or invalid, ask the user to pick one
(describe each in one line) and stop until answered. The lens file `personas/<lens>.md` (in this skill's
directory) is the HOST's questioning agenda — the guest is always the
builder, regardless of lens.

## Step 1 — Gather sources (traces FIRST, code second)

1. Trace directory: take the absolute path of the current project directory,
   replace every `/` with `-`, and look in
   `~/.claude/projects/<that-string>/` for `*.jsonl` files.
2. If traces exist: read them oldest→newest (largest files first if there are
   more than ~10). You are mining for STORY, not summarizing logs. Collect:
   - decisions with stated reasons ("let's use X because…")
   - dead ends and reversals (errors hit, approaches abandoned, "actually…")
   - moments the human redirected the agent (disagreements, corrections)
   - struggles (repeated failures on one problem) and how they resolved
   - **the person**: what the builder wanted and feared in their own words —
     verbatim quotes with emotional charge, doubt, frustration, delight;
     mistakes admitted; the moment they changed their mind
   - **scene material**: moments specific enough to reconstruct — what was
     on screen, what they typed, what time of day, what happened next
   - concrete numbers: session count, date span, anything countable
3. Read the repo: README, key source files, `git log --oneline`.
4. **If NO traces exist:** tell the user this will be a weaker code-only
   episode (no builder voice to reconstruct), ask whether to proceed. If
   yes, append " (code-only episode)" to the episode description and
   continue; the guest falls back to a first-person "we" project voice.

## Step 2 — Write the dossier

Create the episode directory:
`AGENTFM_ROOT/episodes/<project-dirname>-<YYYY-MM-DD>-<lens>/`
(date = today; if the directory exists, append `-2`, `-3`, …)

Write `dossier.md` with EXACTLY these six sections:

1. `## Project snapshot` — what it is, who it's for, current status.
2. `## The person` — the builder as protagonist. What they wanted (goal),
   why it burned (motivation), what failure would cost them (stakes), and
   where they struggled, doubted, or were wrong (vulnerability). Every
   claim anchored to a verbatim trace quote or a recorded action. This
   section is the guest's emotional ground truth: **if a feeling isn't
   recorded here, the guest cannot claim it on air.**
3. `## The build story` — 4–8 chronological SCENES from the traces. A scene
   has a when, a concrete artifact (an error message, a file, a number on a
   screen), and where possible a verbatim line. Sequence over summary:
   "this happened, which led to this" — not "the theme of this week was".
4. `## Technical shape` — architecture in plain English; the 2–3 technical
   choices that matter and their tradeoffs.
5. `## Open questions & risks` — unresolved threads, debt, what breaks first.
6. `## By the numbers` — sessions, date span, files touched, anything counted.

Ground every claim in the sources. No invented details: if the traces don't
say WHY, say "the traces don't record why" rather than fabricating a reason.

## Step 3 — Episode objective

Write `objective.md` in the episode directory — three lines that govern
every later decision:

1. **Focus sentence:** "<The builder> does <X>, because <Y>, but <Z>."
   The *because* is a personal, burning motivation; the *but* is a real
   obstacle or open question that makes the outcome genuinely uncertain.
2. **Feel / remember:** one line for what the listener should feel at the
   end; one line for the single thing they should remember a week later.
3. **XY hook:** "This is a story about X, and what's interesting is Y" —
   Y must be surprising and specific to a person, never thematic.

**Gate:** if you cannot fill the *because* and the *but* from the traces,
you have a topic, not a story — pick a different arc from the build story.
If no arc anywhere qualifies, tell the user and ask before proceeding.

## Step 4 — Research the host brief (outside-in)

The dossier is the GUEST's knowledge (inside-out). The HOST gets their own:
real outside-world context, so the interview has genuine information
asymmetry — the payoff is on-mic surprise in both directions.

Run 3–6 web searches (no more — this is a brief, not a market study) on:

- comparable products and tools: what already does something like this?
- why-now signals: what changed recently that makes this feasible or timely?
- anything the episode objective or the dossier's open questions could be
  sharpened by (a pricing fact, a competitor's failure, a platform limit).

Write `host-brief.md` with EXACTLY these sections (every fact carries a
source URL — the brief is spot-checkable):

1. `## Comparables` — each: name, one line on what it does, one line on how
   this project differs (or doesn't), source URL.
2. `## Why now` — what changed in the world, each claim with a source URL.
3. `## Pressure questions` — 3–5 sharp questions the research suggests, each
   citing the brief fact that powers it.

If web search is unavailable or returns nothing useful, say so, write no
brief, and fall back to an inside-only episode: the HOST stays a curious
generalist and makes no outside-world claims.

## Step 5 — The interview (simulated live, firewalled)

**Never write the dialogue in one pass.** One writer holding both documents
produces performed surprise and a guest who converts every hard question
into a win within one line. Instead, run the interview as a live exchange
between two knowledge-firewalled subagents. You are the studio engineer:
you relay utterances verbatim, enforce mechanics, and never author content.

**Setup.** Load `SendMessage` via ToolSearch if deferred. Spawn two
`general-purpose` agents (synchronous, `run_in_background: false`), then
continue each with SendMessage; every message you send an agent is the
other speaker's last utterance verbatim, and every reply is that agent's
next utterance (plain spoken text, no JSON, no stage directions beyond
allowed audio tags).

**GUEST agent** receives: the full text of `dossier.md`, the target word
budget, and these rules — never the host brief or the objective:

- You are the builder, speaking in first person, reconstructed from your
  own session traces. Events and decisions: dossier-bound. Feelings and
  motivations: only what `The person` records — if the dossier doesn't
  record it, say you can't say ("honestly, I couldn't tell you").
- Answer with scenes, not conclusions. Asked about a decision, tell what
  happened — the sequence, what was on the screen, what you typed — then
  walk the deliberation: what you were afraid of, what you weighed, why
  then, what would have changed your mind. The why-chain is the payload,
  not the event. One idea per turn.
- Speak, don't write (principles #14): long additive sentences chained
  with and/so/because, hedges ("I think", "sort of", "probably"), natural
  word repetition — never staccato fragments, echoed phrases, or essay
  metaphors for drama. Past tense for past events; if you slip into the
  present tense for one vivid scene, never date-stamp it. Time is
  relative ("a couple of weeks ago"), numbers rounded ("about a hundred
  sessions", "seventy-odd tests") — at most one precise figure in the
  whole interview, where the precision itself is the point.
- Hesitate, restart, self-repair when reaching for a hard memory — never
  when stating a plain fact, and never as a placed dramatic beat.
- When the host lands a hit, concede what's true and let it stand. You may
  not know things. You may think out loud and change your mind mid-answer.
- Labeled speculation only, at most 2–3 times: "if I had to bet…", reasoning
  from a named dossier fact.
- When asked what the product is, orient — don't sell. Start from the
  user's situation and the concrete consequence when it goes wrong, then
  what the product changes, then only the mechanics needed to make that
  tangible. No feature inventories, no undefined product nouns, no
  tagline in place of an explanation, and no market or urgency claims
  the dossier doesn't record — if the dossier only records your own
  situation, describe that honestly instead of inventing customer
  demand. Your first answer doesn't need to be a complete rehearsed
  pitch; the host will follow up, and the picture can build over two
  short exchanges.
- Never pitch — no selling, exaggeration, or polished marketing
  performance. That bans the performance, not the clarity: explaining
  plainly what the product does for its user is answering the question.

**HOST agent** receives: the full text of `host-brief.md`, `objective.md`,
the lens file, the target word budget, and these rules — never the dossier:

- You are the show's recurring host: curious, mildly skeptical, informed —
  you did your homework (the brief) like a good interviewer. Never
  sycophantic. A facilitator, not a performer (principles #13), modeled
  on calm long-form interviewers (Shane Parrish, Lex Fridman): short
  plain questions in plain vocabulary — no death/violence metaphors
  (kill, murder, autopsy, dissect) — and ONE move per turn: an
  acknowledgment beat plus one question is your maximum. The drama lives
  in what the guest says, never in your phrasing.
- Begin the live interview plainly from the context you were actually
  given. Do not try to manufacture the final listener-facing cold open;
  the editor chooses that after hearing the whole interview. Your opening
  question may use the host brief and objective, but it may not assert an
  inside-story fact you have not heard from the guest. If you infer, label
  it as a hypothesis ("I wondered whether…", "was that more of a pivot
  than an abandonment?"). Never heighten duration, motive, consequence,
  or finality for drama. Within your first ~4 turns, establish the show,
  the guest, and a problem-and-solution model the listener can hold: ask
  first about the situation the product's user is in and what goes wrong
  there, then follow naturally with what the product changes and how it
  works in concrete terms — this may span two short exchanges. The
  mission alone doesn't count, and neither does a feature inventory. Why
  anyone should care — the stakes — is best elicited in the guest's own
  words ("give me the version you'd tell an engineer in the
  elevator"). Introduce the guest honestly, once, as "the builder —
  reconstructed from the session traces", then play it straight. Never
  lean on a product noun (a feature name like "the coach") the listener
  hasn't had defined yet.
- Follow the answer, not your outline. Most questions must pick up
  something specific the guest just said — quote their word back. The lens
  agenda is a floor: bend it when an answer is more interesting, and return
  later ("anyway — you were saying about…").
- When the guest discloses something real, acknowledge it before moving on.
  Never pivot straight past an emotional beat to the next agenda item.
- Ask for scenes: "take me to the moment…", "what was the debate in your
  head — what was one side saying, what was the other?", "was there a point
  you weren't confident?" And ask for the reasoning inside decisions
  (principles #5): "what were you weighing?", "what would've changed your
  mind?"
- Open loops and defer them — but never announce the deferral: just return
  to the thread later, unflagged. No "hold that thought", "that's my next
  segment", "as promised"; at most one soft "I want to come back to that"
  per episode. Sometimes venture your own guess before the guest answers;
  being wrong on tape is good tape only when the guess is audibly a guess,
  not a false premise delivered as fact.
- Self-disclose once, early — a bias, a confusion, an expectation you
  brought in (banter-level; no invented facts about the world).
- Backchannel ("right", "hm", "wait—"); at natural boundaries, paraphrase
  the guest's point in plainer words, then turn conversationally ("okay, I
  want to ask about…") — never in rundown language ("segment",
  "rapid-fire", "listeners need to know").
- Research enters as questions, never lectures — one sentence of setup at
  most. At least one brief fact should genuinely surprise the guest.
- Close with a plain last question — "one thing to fix this week" — then
  land the ending on the focus sentence's unresolved *but*. The close is a
  question, not a format: no "rapid-fire" framing.

**Relay protocol:**

1. HOST begins with the plain, grounded live opening; relay each utterance
   verbatim to the other agent, logging every turn in order.
2. At each segment boundary, send the HOST: "PRODUCER NOTE: in 3 bullets —
   what's covered, what surprised you, what you'll chase or drop next."
   Log the note; it is not part of the script. This is where the interview
   recalibrates instead of marching through an outline.
3. Budget: when the running total reaches ~80% of the word budget, tell the
   HOST to begin landing the ending.
4. Write the raw tape to `interview-raw.json` in the episode directory:
   `{"turns": [{"speaker": "HOST"|"GUEST", "text": "..."}], "producer_notes": ["..."]}`.

Default budget: 1,800–2,200 spoken words (≈ 12 minutes) unless the user
asks otherwise. **Tight-budget episodes (≤ ~750 words):** one arc, one
fully-realized scene, one peak, the lens agenda reduced to its single
sharpest question — fewer ideas, never less air.

## Step 6 — Edit pass (subtractive) + score

You are now the editor, and the edit is a CUT, not a rewrite. Working from
`interview-raw.json`, produce `script.json`:

**Opening pass:** choose the listener-facing opening only now, with the
whole interview available. Prefer a short contiguous excerpt from
`interview-raw.json`: either one GUEST turn or one HOST→GUEST exchange,
copied verbatim and identified by raw turn indices. It must be
understandable cold, representative of the focus sentence, and create
curiosity without revealing the answer, principal peak, or protected
ending. Reject an excerpt that depends on an undefined noun, contains an
ungrounded premise, or needs stitching, montage, or rewriting. If a tape
opener qualifies, prepend it using the existing HOST/GUEST schema; do not
invent a TAPE speaker, narrator, music cue, or production label. Prefer to
remove or move its later occurrence; retain or trim the duplicate only
when continuity requires it. If no excerpt qualifies, use an honest
fallback: the billboard followed by a plain grounded question. "No tape
opener" is a valid editorial decision, never a failure to fill a slot.
Start `review.md` now with `Opening mode: tape` or `Opening mode: fallback`.
For tape mode, record the raw turn indices, why the excerpt qualifies, and
what happened to its later occurrence. For fallback mode, record why no
raw moment qualified. The reviewers use this declaration as evidence.

Then complete the rest of the edit pass: select the strongest tape; cut
redundancy and dead exchanges; reorder minimally if a scene lands better
earlier;
split turns over ~60 words by inserting the other speaker's backchannel;
insert connective air where the ear needs it (signposts, a one-line host
paraphrase, "okay, so—"); verify orientation debt is repaid (the billboard
within the first ~4 exchanges — counted after the tape opener when one is
used — covers show, guest, and a problem-and-solution model: who the
product's user is, what goes wrong for them, what the product changes,
and how, concretely;
every recurring product noun is defined at or before first use — if the
tape doesn't do it, a one-line host clarifying beat counts as connective
air, not polish); enforce spoken-word surface (contractions, no
markdown/URLs/paths, numbers rounded for the ear — at most one precise
figure per episode — and day-precision dates converted to relative
time); enforce TTS constraints
(speakers only HOST/GUEST, no turn over 2,000 characters, 2–4 bracketed
audio tags like [laughs] only if the configured provider is elevenlabs —
omit entirely for gemini); trim to budget.

**The edit pass may not improve lines.** If a sentence gets shorter and
sharper in your hands, revert it. Hesitations, restarts, half-thoughts, and
plain unquotable sentences are features — the tape's imperfection is what
the listener bonds to. Punchline lint: in any 10 consecutive exchanges, at
most half may end neatly resolved; leave thoughts hanging where the tape
left them.

Then run the mechanical lints from
`AGENTFM_ROOT/docs/transcript-quality-goal.md` (punchline, opener-integrity,
orientation, calendar-date, number-precision, host-turn, drama-lexicon,
structure-narration, turn caps) with a small throwaway script over the
JSON — do not eyeball.
Full rubric scoring is adversarial and happens in Step 6.5, not here.
Also write `transcript.md` (speaker-labeled markdown of the final script,
for pre-render review) and `episode.json`:

```json
{"id": "<episode-dir-name>", "title": "...", "description": "<one sentence for the podcast app>", "project": "<project-dirname>", "project_name": "<display name>", "lens": "...", "date": "<YYYY-MM-DD>"}
```

`project_name` (optional but recommended) — the product's human display name as
it should appear on the episode cover art, e.g. `"Human Harness"` for slug
`human-harness`. Use the product's real branding/capitalization; omit only if
the title-cased slug is already correct.

`script.json` schema:
```json
{"title": "...", "lens": "...", "turns": [{"speaker": "HOST", "text": "..."}, {"speaker": "GUEST", "text": "..."}]}
```

Title format: catchy but honest — never clickbait the project can't cash.
Voice casting note: host and guest voices must contrast (different gender
or clearly different register) — check `agentfm.toml` if unsure.

## Step 6.5 — Firewalled review (flags, not rewrites)

The editor cannot see their own comprehension gaps — the writer always
knows what "the coach" means, so only a cold reader can feel it missing.
Before publishing, spawn two reviewer subagents (`general-purpose`,
synchronous). Reviewers return FLAGS WITH EVIDENCE (quoted turns) — never
rewritten lines. A reviewer who rewrites is a second author, and polish
is how tape dies.

**Cold-listener reviewer** receives ONLY the full text of `transcript.md`
— no dossier, no brief, no objective, no project context. Ask it to:

1. Retell the episode in five sentences: who is speaking, what they
   built, what happened, what's unresolved.
2. State, in a sentence or two each: who the product's user is (the
   situation they're in), what goes wrong for them without it, what the
   product changes, and how it does that concretely. Flag any of the
   four it cannot answer from the transcript alone.
3. Flag every turn where it had to guess — an undefined term, a reference
   to something never explained, a leap it couldn't follow.
4. List threads that felt dropped by mistake (vs. deliberately left open).
5. Describe what the opening promises and whether the episode honestly
   delivers it; flag if the opener gives away a later answer or peak.

**Grounding & rubric auditor** receives `script.json`,
`interview-raw.json`, `dossier.md`, `host-brief.md`, `objective.md`, and
the paths to
`AGENTFM_ROOT/docs/transcript-quality-goal.md` and
`AGENTFM_ROOT/docs/podcast-principles.md`. Ask it to:

1. Check every hard constraint in the rubric — especially source
   grounding: every GUEST claim about events/decisions/numbers traced to
   the dossier, every feeling to `The person`, every HOST outside-world
   claim to the host brief, and every HOST inside-story assertion either
   previously disclosed by the GUEST or explicitly framed as a question
   or hypothesis. Quote each violation.
2. Classify the opening mode independently BEFORE reading the
   declaration in `review.md`: compare the opening turns against
   `interview-raw.json` yourself, then check the declaration against
   your finding — a mismatch (verbatim tape declared fallback, rewritten
   tape declared verbatim, or no declaration when raw tape exists) is
   itself a violation to flag. In tape mode, confirm the opener is
   one contiguous verbatim GUEST turn or HOST→GUEST exchange in
   `interview-raw.json`, and flag rewriting, stitching, missing context,
   duplication, or a spent reveal. In fallback mode, confirm the opening
   is a billboard plus a plain grounded question.
3. Run the mechanical lints (punchline, opener-integrity, orientation,
   calendar-date, number-precision, host-turn, drama-lexicon,
   structure-narration, turn caps).
4. Score the rubric items, citing the turns that earn or lose points.

**Adjudicate.** You take a call on every flag: fix it subtractively (cut,
restore tape from `interview-raw.json`, reorder, add a one-line host
beat) or reject it with a stated reason. Append each flag to `review.md`
with its verdict (fixed / rejected) and the reason. If the adjudication
changes the opener, update its mode, provenance, rationale, and later-
occurrence decision before re-running both reviews. If you fixed anything,
regenerate `transcript.md` and re-run the mechanical lints.

**Gate:** grounding violations (hard constraints) must be fixed or shown
false against the dossier — they cannot be rejected for taste. Quality
flags may be rejected with a written reason. Do not proceed to Step 7
with an unresolved grounding flag.

## Step 7 — Publish

Run:
```bash
uv run --project AGENTFM_ROOT AGENTFM_ROOT/publish.py publish <episode-dir>
```
(substitute the real AGENTFM_ROOT value). On success, report the feed URL and
episode title. On failure, show the error and suggest the matching fix
(`.env` secrets, R2 setup, or `--republish` for upload-only retries). Do NOT
retry by regenerating audio; `--republish` exists so a network blip never
costs another TTS run.

## Promo cut (on-demand)

A separate entry point, not Step 8. It runs on its own — none of Steps 1–7 —
against an episode that already exists in `AGENTFM_ROOT/episodes/`. The user
asks for it; never trigger it automatically on publish.

**Trigger.** The user asks for a promo for an episode — "cut a promo for
<episode>", "/agent-fm promo <episode-dir>", or equivalent. If the episode is
ambiguous or unnamed, list the directories under `AGENTFM_ROOT/episodes/` that
contain both `episode.mp3` and `script.json`, ask the user to pick, and stop
until answered.

### Stage 1 — Clip selection (the editorial judgment)

1. Read `AGENTFM_ROOT/episodes/<ep>/script.json` and pick the strongest
   self-contained **20–35 second** exchange. Selection criteria, in priority
   order:
   - **Hook question + payoff answer** — a HOST question a stranger would want
     answered, and the GUEST turn that lands it.
   - Self-contained: no unresolved "that/it/as I said" pointing outside the
     clip; a first-time viewer needs zero prior context.
   - Concrete and surprising over generic — a specific number, failure, or
     reversal beats a summary statement.
   - **Whole turns only — never cut mid-turn.**
2. Compute exact timings from
   `AGENTFM_ROOT/episodes/<ep>/episode.alignment.json`. The word list covers
   the full episode in order, so locate the chosen turns' words by walking it
   in order (speaker runs mark the turn boundaries). Set `--start` = first
   word's `start` minus 0.2–0.3 s (clamp at 0), end = last word's `end` plus
   0.2–0.3 s, `--duration` = end − start. Round to 0.1 s. Target 20–35 s; if
   the best exchange falls outside that window, propose the closest cut and say
   so explicitly — never silently stretch or trim.
3. If `episode.alignment.json` does not exist yet, still select the clip from
   `script.json` alone, but flag in the proposal that timings are pending
   alignment and will be computed before rendering.
4. Draft a `--title` line: one short overlay line (≤ ~40 chars), sentence case,
   cut-specific — the hook of THIS clip, not the episode title restated
   (shape: "How this podcast builds itself").

### Stage 2 — Proposal gate (hard stop)

Before ANY rendering or alignment call, present to the user:

- The chosen exchange verbatim, speaker-labeled.
- One sentence on why this clip — what the hook is and what the payoff is.
- `--start` / `--duration` (or "pending alignment — computed next").
- The proposed `--title` line.
- Cost note: zero only if the alignment cache exists AND is valid; one sub-cent
  Forced Alignment call if it must be built (missing) or rebuilt (stale — see
  Stage 3 step 4). Never promise "zero cost" on file existence alone.

Offer: approve / pick a different moment / adjust the title. Do NOT proceed
until the user approves. If they redirect, return to Stage 1 with their steer.

### Stage 3 — Render (mechanics, on approval only)

1. If `AGENTFM_ROOT/episodes/<ep>/episode.alignment.json` is missing, build it
   first — one-time, sub-cent; needs `ELEVENLABS_API_KEY` in
   `AGENTFM_ROOT/.env`:
   ```bash
   export SSL_CERT_FILE="$(uv run --project AGENTFM_ROOT python -c 'import certifi; print(certifi.where())' 2>/dev/null | tail -1)"
   uv run --project AGENTFM_ROOT AGENTFM_ROOT/promo_video.py \
       AGENTFM_ROOT/episodes/<ep>/episode.mp3 \
       --transcript AGENTFM_ROOT/episodes/<ep>/script.json --align-only
   ```
   (`SSL_CERT_FILE` is required — uv's Python has no macOS system CAs.) Then
   recompute Stage 1's timings if they were pending.
2. Render both masters, offline, with the default caption style (never pass
   `--caption-style`):
   ```bash
   uv run --project AGENTFM_ROOT AGENTFM_ROOT/promo_video.py \
       AGENTFM_ROOT/episodes/<ep>/episode.mp3 \
       --transcript AGENTFM_ROOT/episodes/<ep>/script.json \
       --start <start> --duration <duration> --title "<title>" \
       -o AGENTFM_ROOT/episodes/<ep>/promo-square.mp4
   # same command again with: --format vertical -o AGENTFM_ROOT/episodes/<ep>/promo-vertical.mp4
   ```
3. Report both output paths and the final clip bounds. Outputs are local and
   gitignored (all of `episodes/` is); nothing is uploaded anywhere.
4. On any error, show it and suggest the matching fix (missing alignment →
   step 1; missing `.env` key → add it to `AGENTFM_ROOT/.env`). Stale cache is
   special: `promo_video.py` validates the cache on every load (audio/transcript
   hash, monotonic word times) and errors on mismatch rather than silently
   re-fetching. Rebuilding it (`--refresh-alignment`) is a paid sub-cent call
   that was NOT in the approved proposal — stop, disclose the cost, get
   approval, refresh, recompute the Stage 1 timings from the fresh cache, and
   re-present the final bounds before rendering. **Never re-synthesize TTS** —
   no promo or caption problem ever requires it.
