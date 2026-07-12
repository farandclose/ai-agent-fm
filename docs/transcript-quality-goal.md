# Goal: a transcript the listener connects with (transcript-only run)

You are scoring/optimizing a podcast transcript (`script.json`: alternating
HOST/GUEST turns, GUEST = the builder in first person) against the
principles in `docs/podcast-principles.md`. The bar is connection, not
polish: a listener should finish knowing what the builder wanted, what it
cost, and what almost went wrong — and feel they met a person, not a
spokesperson. The register is the long-form conversation show (the
principles' north star): the script must read as two people talking,
never as produced radio — structure is felt, never heard.

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

**1. Spine & person — 20 pts**
- The builder's want (goal + burning motivation) and the obstacle (the
  focus sentence's *but*) are both audible in the first quarter of the
  episode: 6
- The guest is the builder in first person throughout — never a
  commentator describing the builder in third person: 4
- Goals, stakes, AND vulnerability are all audible (what they wanted, what
  failure would cost, where they struggled or were wrong): 5
- At least one concession, mistake, or open doubt is left standing —
  not converted into a win within the same exchange: 5

**2. Anecdote engine — 20 pts**
- Every segment alternates action and reflection (something HAPPENS, then
  someone says what it meant) — no segment is reflection-only: 8
- At least 2 fully-realized scenes with concrete, imageable detail (an
  artifact, a verbatim line, what was on the screen): 7
- Abstractions are converted to instances; the host audibly extracts
  scenes ("take me to the moment…") rather than opinions: 5

**3. Reasoning depth — 15 pts** (principles #5)
- Every pivotal decision kept in the episode comes with its deliberation
  — what the builder feared, what alternatives they weighed, why then —
  not just the event and its outcome: 8
- The host audibly asks for reasoning at least twice ("what were you
  weighing?", "what would have changed your mind?"): 4
- At least one tradeoff is walked honestly enough that a listener could
  disagree with the call: 3

**4. Pull & shape (invisible) — 15 pts**
- ≥ 2 open loops raised early and answered later — with real distance
  between raise and resolve, and the return UNANNOUNCED (the host just
  picks the thread back up). If a tape opener is used, it raises one of
  these loops without closing it; bonus behavior: the host guesses wrong
  once before a reveal, with the guess audibly labeled as a guess: 6
- At most 2 emotional peaks, each a moment of substance (a confession, a
  live realization, an unanswerable question) — never a prose effect —
  with plainer connective material between them: 5
- The ending is protected: the final exchanges land on the focus
  sentence's unresolved *but*, and nothing important is crammed after the
  emotional close: 4

**5. Listening & asymmetry — 15 pts**
- ≥ 1/3 of host turns are reactive follow-ups that pick up a specific word
  or claim from the guest's previous turn: 5
- Disclosures get an acknowledgment beat before any topic change; the
  prepared agenda visibly bends at least once (a chase, a drift-and-return): 4
- Information asymmetry pays off on-mic in BOTH directions: the host is
  genuinely surprised by something from the dossier side, the guest by a
  host-brief fact, and at least one speaker audibly updates their view: 6

**6. Speech realism & air — 15 pts** (principles #14)
- No written-prose tells: no staccato fragments, echo repetition, or
  placed ellipses as drama; no elegant variation or essay metaphors.
  (Real spoken texture — long additive sentences, hedges, natural word
  repetition — is what remains when the tells are gone; per the one-way
  rule below, its presence is never scored, only the tells' absence.): 6
- Past tense is the default for past events; the historical present
  appears at most inside one vivid scene and is never date-stamped: 3
- Signposts at natural boundaries and at least one host paraphrase in
  plainer words; backchannels present; at least one unfinished thought;
  hesitation/self-repair on hard memories only — scattered and
  unremarkable, never placed at an emotional peak as a beat: 4
- Direct address to the listener at most once or twice, where it earns
  intimacy: 2

**The one-way rule (governs every lint and texture criterion):** a
mechanical lint may only forbid a surface form, never require the
presence of one. A judged item may reward the function a line performs
(a paraphrase for the ear, an unfinished thought left standing) but
never the frequency or placement of a spontaneity marker (hedges,
fillers, stumbles, hesitations). Rewarding a marker's presence
manufactures that marker — the Goodhart failure this rubric exists to
prevent. Any future lint or criterion that violates this rule is invalid
as written.

**Punchline lint (mechanical, applied before scoring):** in every window of
10 consecutive exchanges, at most 5 may end neatly resolved (an aphorism,
a zinger, a wrapped bow). Each violating window: −5 from the total. This
is the anti-aphorism guard — quotable lines are allowed to exist, but only
against a plainer surround.

**Opener-integrity lint (mechanical + judged, applied before scoring):**
the reviewer first classifies the opening independently — compare the
opening turns against the raw tape BEFORE reading the declaration in
`review.md` — then checks the declaration against that finding; a
mismatch (an opening that is verbatim tape but declared fallback, or
rewritten tape declared verbatim, or no declaration at all when raw tape
exists) is itself an integrity violation. In tape mode, the opening must
be exactly one contiguous verbatim GUEST turn or HOST→GUEST exchange
from the raw turns, with its indices recorded in `review.md`.
No rewriting, stitching, montage, undefined dependency, ungrounded
premise, duplicate left without a continuity reason, or excerpt that
spends the answer, principal peak, or protected ending. In fallback mode,
the opening is a billboard plus a plain grounded question, and `review.md`
states why no raw moment qualified. If raw tape is unavailable, do not
claim tape provenance; assess the opening as a direct opening. Any
provenance or integrity violation rejects the revision (principles #9).

**Orientation lint (mechanical + judged, applied before scoring):** by
the end of the first 4 exchanges — counted after the tape opener when one
is used (the opener borrows context; the window is for repaying it) — the
listener must be able to assemble a coherent problem-and-solution model:
the show, the guest, who the product's user is and what goes wrong for
them without it, what the product changes, and how it does that in
concrete terms (not just the mission, and not a feature inventory), AND
why anyone should care — the stakes, best in the builder's own words.
Every recurring product noun (feature names like "the coach") must be
defined at or before its first use. Each violation: −5 from the total.
An opener may borrow context; the billboard must repay it in full
(principles #9).

**Calendar-date lint (mechanical):** no spoken day-precision date ("June
27th", "on July 3rd"). Time is anchored relative to now ("a couple of
weeks ago", "the other day"); month/year anchors ("back in late June",
"in 2014") are allowed only for spans long enough to warrant them. Each
violation: −5.

**Number-precision lint (mechanical):** every spoken number must be round
(a multiple of ten/hundred, or a natural fraction like "two-thirds") or
hedged ("about a hundred", "seventy-odd", "something like"); counts of a
hundred or more always want a hedge ("about a hundred sessions", "a
hundred-plus"), round or not. At most ONE un-round, un-hedged number in
the whole episode, reserved for when the precision itself is the point.
Each additional violation: −5.

**Host-turn lint (mechanical + judged):** at most one question mark per
HOST turn, and no host turn stacks more than two moves (a move =
acknowledge/react, paraphrase, self-disclose, introduce a brief fact, or
ask). Each violation: −5. (Principles #13.)

**Drama-lexicon lint (mechanical):** the HOST never uses death/violence/
forensics metaphors — kill(ed), murder(ed), autopsy, dissect, corpse,
wound, scar, brutal, mercy killing. The GUEST may use such a word only
where the dossier records it as the builder's own. Each violation: −5.

**Structure-narration lint (mechanical):** zero show-rundown vocabulary
on-mic — "segment", "rapid-fire", "as promised", "hold that thought",
"that's my next…", "listeners need to know". Callbacks return to their
thread unannounced; at most one soft, human deferral per episode ("I want
to come back to that"). Each violation: −5. (Principles #13.)

**Lint provenance (every lint records the real failure it came from):**
punchline — the aphorism-string episodes (2026-07-07); opener-integrity
— the manufactured cold-open premises in
`human-harness-2026-07-08-product`; orientation — a billboard that named
the mission but left the problem, mechanics, and feature nouns undefined;
calendar-date, number-precision, host-turn, drama-lexicon, and
structure-narration — the drama-moved-house builder review of
`human-harness-2026-07-07-product-2`. A lint proposed without a real
observed failure behind it doesn't get added.

**Lint audit (standing rule):** when builder review of a new episode
finds a quality failure, ask two questions before adding a lint: did the
drama move house again (and which existing lint *should* have caught
it), and did any existing lint manufacture a new tic (markers appearing
on schedule, evenly spaced, or at rule-shaped frequencies)? Removing or
narrowing a lint that causes tics is as legitimate an outcome of review
as adding one.

## Hard constraints (any violation = reject the revision, score is void)

1. **Text only.** No audio synthesis, no `publish.py`, no TTS or upload API
   calls anywhere in the run.
2. **Source grounding.** Every GUEST claim about events, decisions, or
   numbers traces to the episode's `dossier.md`; every GUEST claim about
   feelings or motivations traces to the dossier's `The person` section;
   every HOST outside-world claim traces to `host-brief.md`. Banter is
   free; claims are not. If the dossier doesn't record it, the guest says
   they can't say — never a fabricated memory. Every HOST inside-story
   assertion must either have been disclosed by the GUEST earlier in the
   raw interview or be explicitly framed as a question or hypothesis. No
   host brief → the HOST makes no outside-world claims.
3. **Opener provenance.** Tape mode satisfies the opener-integrity lint:
   one contiguous verbatim GUEST turn or HOST→GUEST exchange from
   `interview-raw.json`, with recorded indices and no spent reveal. If no
   excerpt qualifies, fallback mode is required and is not penalized.
4. **Labeled speculation only.** The GUEST may extrapolate beyond the
   dossier at most 2–3 times, each audibly marked as judgment ("my read
   is…", "if I had to bet…") and reasoning from a named dossier fact.
5. **Length budget.** Total spoken text within the stated budget for the
   run. Default: 1,800–2,200 words (~12 min). Tight budget (~750 words):
   one arc, one fully-realized scene, one peak — fewer ideas, never less
   air.
6. **Schema.** Valid `script.json`; `speaker` only HOST or GUEST; no single
   turn over 2,000 characters (the TTS request limit). Turns over ~60 words
   should be split with the other speaker's backchannel.
7. **Character consistency.** HOST is the same curious, mildly skeptical,
   informed generalist every episode, modeled on calm long-form
   interviewers (Shane Parrish, Lex Fridman): plain short questions,
   research surfacing as questions never lectures, a facilitator not a
   performer (principles #13); GUEST is the builder, first person,
   introduced once as reconstructed from the traces. Never sycophantic.
8. **Spoken-word surface.** No markdown, URLs, or code identifiers longer
   than a word or two; numbers rounded for the ear and day-precision dates
   converted to relative time (see the calendar-date and number-precision
   lints); contractions.
9. **Provider fit.** 2–4 bracketed audio tags ([laughs], [sighs]) only if
   the configured TTS provider is elevenlabs; omit entirely for gemini.

## Run procedure (score → revise → repeat, all on text)

1. Check every hard constraint; fix any failure before scoring.
2. Run every mechanical lint — punchline, opener-integrity, orientation,
   calendar-date, number-precision, host-turn, drama-lexicon,
   structure-narration — and the turn-cap check (a small throwaway script
   over the JSON; do not eyeball).
3. Judge items 1–6 against the rubric, quoting the evidence turns per line.
4. Revise ONLY the lowest-scoring items, subtractively (cut, restore tape,
   reorder, add air). Preserve what already scores well.
5. Repeat. Stop when: score ≥ 85 with all hard constraints green, OR two
   consecutive iterations fail to improve, OR the next revision would
   violate a hard constraint.
6. Final report: opening mode and provenance decision; total score,
   per-item breakdown with evidence, iteration count, what changed. End
   with: "Ready to render — synthesizing this costs ~N credits" where N =
   total character count. Do NOT render.

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

## Why revised again (2026-07-08)

The 2026-07-07 rubric killed the aphorism string — and the drama moved
house: into sentence rhythm (staccato fragments, echo repetition, placed
ellipses), screenplay devices (present-tense date-stamped scenes,
hyper-precise counts like "a hundred and twenty sessions"), and
produced-radio furniture (announced callbacks, a "rapid-fire close", a
host drama lexicon of kills and autopsies). Builder review of
`human-harness-2026-07-07-product-2` caught all of it. Root cause: the
taste target was produced narrative radio; it is now the long-form
conversation show (Lex Fridman, The Knowledge Project, a16z). Structure
survives as writer-side discipline but must be inaudible; speech texture
and reasoning depth are now scored (items 3 and 6); five new lints guard
the tells. See the principles' north star and #5, #13, #14, derived from
verbatim transcripts of the target shows.

## Why revised again (2026-07-11)

Builder review of `human-harness-2026-07-08-product` found that the
required pre-interview cold open had manufactured two false premises: work
that took days became "weeks", and a pivot became "walking away". The
failure was structural, not just a bad line: a firewalled host was being
asked to invent the episode's sharpest framing before hearing the guest.
The pipeline now separates the plain live opening from the final
listener-facing opening. After the interview, the editor may promote only
a short contiguous verbatim raw-tape excerpt with recorded provenance; if
none qualifies, an honest billboard and grounded question is the correct
fallback. The new opener-integrity lint protects factuality, context, and
later reveals without introducing a new speaker or production layer.

## Why revised again (2026-07-12)

Two fixes from review of the 2026-07-08/07-11 revisions, before either
failure occurred on tape. First, rubric item 6 rewarded the *presence*
of hedges and word repetition — the same Goodhart mechanism as the old
"exactly one self-correction" rule, which would eventually manufacture
hedge tics. Texture is now scored only by the absence of written-prose
tells, and the one-way rule makes that a standing constraint on all
future lints. Second, the opener-integrity checks branched on the mode
the editor *declared*, so a misdeclared or undeclared mode got weaker
checks; the reviewer now classifies the opening independently and treats
a mismatch as a violation. Same pass: the orientation lint was upgraded
from "one plain mechanical sentence" to a problem-and-solution model
(situation → consequence → change → mechanics), per
`docs/specs/2026-07-11-listener-first-product-orientation-design.md` —
mechanics-first orientation passed the lint while cold listeners still
assembled the product from feature names — and the ~4-exchange window
was pinned to start after the tape opener, which had silently tightened
when tape openers were introduced.
