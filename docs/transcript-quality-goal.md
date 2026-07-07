# Goal: a transcript the listener connects with (transcript-only run)

You are scoring/optimizing a podcast transcript (`script.json`: alternating
HOST/GUEST turns, GUEST = the builder in first person) against the
principles in `docs/podcast-principles.md`. The bar is connection, not
polish: a listener should finish knowing what the builder wanted, what it
cost, and what almost went wrong — and feel they met a person, not a
spokesperson.

**This run optimizes TEXT ONLY.** Do not synthesize audio, do not run
`publish.py`, do not call any TTS API — every audio render spends paid
credits. The deliverable is a revised `script.json` plus a score report;
rendering audio is a separate, human-triggered step.

**Revision bias: subtractive.** Fix a low score by cutting, restoring tape
(from `interview-raw.json` when it exists), reordering, or adding
connective air — never by sharpening lines. If a revision makes a sentence
shorter and more quotable, revert it.

## Objective function (maximize, 0–100)

Judged, with cited evidence per line item — quote the turns that earn or
lose the points. Only the punchline lint and turn-cap checks are counted
mechanically.

**1. Spine & person — 25 pts**
- The builder's want (goal + burning motivation) and the obstacle (the
  focus sentence's *but*) are both audible in the first quarter of the
  episode: 8
- The guest is the builder in first person throughout — never a
  commentator describing the builder in third person: 5
- Goals, stakes, AND vulnerability are all audible (what they wanted, what
  failure would cost, where they struggled or were wrong): 6
- At least one concession, mistake, or open doubt is left standing —
  not converted into a win within the same exchange: 6

**2. Anecdote engine — 25 pts**
- Every segment alternates action and reflection (something HAPPENS, then
  someone says what it meant) — no segment is reflection-only: 10
- At least 2 fully-realized scenes with concrete, imageable detail (a time,
  an artifact, a verbatim line, what was on the screen): 8
- Abstractions are converted to instances; the host audibly extracts
  scenes ("take me to the moment…") rather than opinions: 7

**3. Pull & shape — 20 pts**
- ≥ 2 open loops raised early and answered later — with real distance
  between raise and resolve; bonus behavior: the host guesses wrong once
  before a reveal: 8
- The episode has at most 2 emotional peaks with plainer connective
  material between them — intensity is a curve, not a flat line: 6
- The ending is protected: the final exchanges land on the focus
  sentence's unresolved *but*, and nothing important is crammed after the
  emotional close: 6

**4. Listening & asymmetry — 20 pts**
- ≥ 1/3 of host turns are reactive follow-ups that pick up a specific word
  or claim from the guest's previous turn: 6
- Disclosures get an acknowledgment beat before any topic change; the
  prepared agenda visibly bends at least once (a chase, a drift-and-return): 6
- Information asymmetry pays off on-mic in BOTH directions: the host is
  genuinely surprised by something from the dossier side, the guest by a
  host-brief fact, and at least one speaker audibly updates their view: 8

**5. Air & surface — 10 pts**
- Spoken signposts at segment boundaries and at least one host paraphrase
  of the guest's point in plainer words (load management, second encoding): 4
- Backchannels present; at least one unfinished thought; hesitation/
  self-repair appears on hard memories, not on plain facts: 4
- Direct address to the listener at most once or twice, where it earns
  intimacy: 2

**Punchline lint (mechanical, applied before scoring):** in every window of
10 consecutive exchanges, at most 5 may end neatly resolved (an aphorism,
a zinger, a wrapped bow). Each violating window: −5 from the total. This
is the anti-aphorism guard — quotable lines are allowed to exist, but only
against a plainer surround.

**Orientation lint (mechanical, applied before scoring):** by the end of
the first 4 exchanges, the show, the guest, and what the product actually
does (mechanics — what it watches, what it says back — not just the
mission) must all have been stated; and every recurring product noun
(feature names like "the coach") must be defined at or before its first
use. Each violation: −5 from the total. A cold open may borrow confusion;
the billboard must repay it in full (principles §11).

## Hard constraints (any violation = reject the revision, score is void)

1. **Text only.** No audio synthesis, no `publish.py`, no TTS or upload API
   calls anywhere in the run.
2. **Two-source grounding.** Every GUEST claim about events, decisions, or
   numbers traces to the episode's `dossier.md`; every GUEST claim about
   feelings or motivations traces to the dossier's `The person` section;
   every HOST outside-world claim traces to `host-brief.md`. Banter is
   free; claims are not. If the dossier doesn't record it, the guest says
   they can't say — never a fabricated memory. No host brief → the HOST
   makes no outside-world claims.
3. **Labeled speculation only.** The GUEST may extrapolate beyond the
   dossier at most 2–3 times, each audibly marked as judgment ("my read
   is…", "if I had to bet…") and reasoning from a named dossier fact.
4. **Length budget.** Total spoken text within the stated budget for the
   run. Default: 1,800–2,200 words (~12 min). Tight budget (~750 words):
   one arc, one fully-realized scene, one peak — fewer ideas, never less
   air.
5. **Schema.** Valid `script.json`; `speaker` only HOST or GUEST; no single
   turn over 2,000 characters (the TTS request limit). Turns over ~60 words
   should be split with the other speaker's backchannel.
6. **Character consistency.** HOST is the same curious, mildly skeptical,
   informed generalist every episode (research surfaces as questions, never
   lectures); GUEST is the builder, first person, introduced once as
   reconstructed from the traces. Never sycophantic.
7. **Spoken-word surface.** No markdown, URLs, or code identifiers longer
   than a word or two; numbers rounded for the ear; contractions.
8. **Provider fit.** 2–4 bracketed audio tags ([laughs], [sighs]) only if
   the configured TTS provider is elevenlabs; omit entirely for gemini.

## Run procedure (score → revise → repeat, all on text)

1. Check every hard constraint; fix any failure before scoring.
2. Run the punchline lint, the orientation lint, and the turn-cap check
   mechanically (a small throwaway script over the JSON; do not eyeball).
3. Judge items 1–5 against the rubric, quoting the evidence turns per line.
4. Revise ONLY the lowest-scoring items, subtractively (cut, restore tape,
   reorder, add air). Preserve what already scores well.
5. Repeat. Stop when: score ≥ 85 with all hard constraints green, OR two
   consecutive iterations fail to improve, OR the next revision would
   violate a hard constraint.
6. Final report: total score, per-item breakdown with evidence, iteration
   count, what changed. End with: "Ready to render — synthesizing this
   costs ~N credits" where N = total character count. Do NOT render.

## Why the old rubric was replaced (2026-07-07)

The previous rubric counted surface mechanics — "≥3 sentence-completion
handoffs", "exactly one self-correction", turn-length quotas. Scripts maxed
the counts and lost the story (Goodhart's law): every line became a
quotable aphorism, the builder appeared only in third person, and listeners
connected with no one. The counts manufactured the tics they were meant to
prevent. This rubric scores the properties that research says create
connection and memory — spine, scenes, open loops, shape, audible
listening, imperfection — and treats mechanical counts as guards, not
goals. See `docs/podcast-principles.md` for the evidence base.
