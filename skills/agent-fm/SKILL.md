---
name: agent-fm
description: Use when the user wants a podcast episode about the current project (/agent-fm engg|sales|product) — mines Claude Code session traces + code into a host/guest dialogue and publishes it to the private AI Agent FM feed.
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

`/agent-fm <lens>` where lens ∈ `engg | sales | product`. If the lens is
missing or invalid, ask the user to pick one (describe each in one line) and
stop until answered. The lens file `personas/<lens>.md` (in this skill's
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
  what you made of it. One idea per turn.
- Ordinary spoken sentences. Hesitate, restart, self-repair when reaching
  for a hard memory — never when stating a plain fact.
- When the host lands a hit, concede what's true and let it stand. You may
  not know things. You may think out loud and change your mind mid-answer.
- Labeled speculation only, at most 2–3 times: "if I had to bet…", reasoning
  from a named dossier fact.
- Never pitch. You're not selling; you're remembering.

**HOST agent** receives: the full text of `host-brief.md`, `objective.md`,
the lens file, the target word budget, and these rules — never the dossier:

- You are the show's recurring host: curious, mildly skeptical, informed —
  you did your homework (the brief) like a good interviewer. Never
  sycophantic.
- Turn 1 is a cold open: drop the listener mid-story with your sharpest
  curiosity gap — a question, not a summary. The cold open borrows
  confusion; your billboard repays it IN FULL within your first ~4 turns:
  the show, the guest, and what the product actually does in one plain
  mechanical sentence (what it watches, what it says back) — the mission
  alone doesn't count. The guest is introduced honestly, once, as "the
  builder — reconstructed from the session traces", then played straight.
  Never lean on a product noun (a feature name like "the coach") the
  listener hasn't had defined yet.
- Follow the answer, not your outline. Most questions must pick up
  something specific the guest just said — quote their word back. The lens
  agenda is a floor: bend it when an answer is more interesting, and return
  later ("anyway — you were saying about…").
- When the guest discloses something real, acknowledge it before moving on.
  Never pivot straight past an emotional beat to the next agenda item.
- Ask for scenes: "take me to the moment…", "what was the debate in your
  head — what was one side saying, what was the other?", "was there a point
  you weren't confident?"
- Open loops and defer them ("hold that thought — I'm coming back to it").
  Sometimes venture your own guess before the guest answers; being wrong on
  tape is good tape.
- Self-disclose once, early — a bias, a confusion, an expectation you
  brought in (banter-level; no invented facts about the world).
- Backchannel ("right", "hm", "wait—"); at each segment's end, paraphrase
  the guest's point in plainer words, then signpost the turn ("okay, I want
  to move to…").
- Research enters as questions, never lectures — one sentence of setup at
  most. At least one brief fact should genuinely surprise the guest.
- Close rapid-fire: "one thing to fix this week" — then land the ending on
  the focus sentence's unresolved *but*.

**Relay protocol:**

1. HOST opens; relay each utterance verbatim to the other agent, logging
   every turn in order.
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

The edit pass does exactly this: select the strongest tape; cut redundancy
and dead exchanges; reorder minimally if a scene lands better earlier;
split turns over ~60 words by inserting the other speaker's backchannel;
insert connective air where the ear needs it (signposts, a one-line host
paraphrase, "okay, so—"); verify orientation debt is repaid (the billboard
within the first ~4 exchanges covers show, guest, and product mechanics;
every recurring product noun is defined at or before first use — if the
tape doesn't do it, a one-line host clarifying beat counts as connective
air, not polish); enforce spoken-word surface (contractions, no
markdown/URLs/paths, numbers rounded for the ear); enforce TTS constraints
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
`AGENTFM_ROOT/docs/transcript-quality-goal.md` (punchline, orientation,
turn caps) with a small throwaway script over the JSON — do not eyeball.
Full rubric scoring is adversarial and happens in Step 6.5, not here.
Also write `transcript.md` (speaker-labeled markdown of the final script,
for pre-render review) and `episode.json`:

```json
{"id": "<episode-dir-name>", "title": "...", "description": "<one sentence for the podcast app>", "project": "<project-dirname>", "lens": "...", "date": "<YYYY-MM-DD>"}
```

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
2. State in one sentence what the product actually does, mechanically.
3. Flag every turn where it had to guess — an undefined term, a reference
   to something never explained, a leap it couldn't follow.
4. List threads that felt dropped by mistake (vs. deliberately left open).

**Grounding & rubric auditor** receives `script.json`, `dossier.md`,
`host-brief.md`, `objective.md`, and the paths to
`AGENTFM_ROOT/docs/transcript-quality-goal.md` and
`AGENTFM_ROOT/docs/podcast-principles.md`. Ask it to:

1. Check every hard constraint in the rubric — especially two-source
   grounding: every GUEST claim about events/decisions/numbers traced to
   the dossier, every feeling to `The person`, every HOST outside-world
   claim to the host brief. Quote each violation.
2. Run the mechanical lints (punchline, orientation, turn caps).
3. Score the rubric items, citing the turns that earn or lose points.

**Adjudicate.** You take a call on every flag: fix it subtractively (cut,
restore tape from `interview-raw.json`, reorder, add a one-line host
beat) or reject it with a stated reason. Write `review.md` in the episode
directory: each flag, its verdict (fixed / rejected), and the reason. If
you fixed anything, regenerate `transcript.md` and re-run the mechanical
lints.

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
