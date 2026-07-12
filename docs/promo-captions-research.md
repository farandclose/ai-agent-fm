# Promo video captions — research findings

*2026-07-12. Research for adding burned-in, word-synced captions to `promo_video.py` output.
Scope: promo clips only (15–60 s), two target canvases — 1080×1080 (LinkedIn feed primary) and
1080×1920 (TikTok / Reels / Shorts / LinkedIn vertical). Three tracks: caption craft, word-timing
sourcing, platform safe areas. Each claim cites the source that owns it; confidence flagged where
sources are secondary or disagree.*

---

## 1. Caption craft — what word-synced captions should look like

### Block geometry

- Netflix (primary spec): max 2 lines per caption event, prefer 1; max 42 chars/line; min display
  5/6 s, max 7 s; center-justified ([Netflix Timed Text Style Guide](https://partnerhelp.netflixstudios.com/)).
- BBC Subtitle Guidelines v1.2.3: reading speed 160–180 WPM (~15 chars/sec); safe-area margins
  5%–95% of frame; permitted caption colors restricted to white/yellow/cyan/green
  (secondary — direct fetch of bbc.github.io/subtitle-guidelines 404'd; codes confirmed via
  [bbc/ttml-validator](https://github.com/bbc/ttml-validator)).
- Short-form tools (CapCut, Submagic, Opus Clip) converge on **3–8 word blocks**, single line,
  "headline" framing — much shorter than broadcast line limits
  ([opus.pro](https://www.opus.pro/), [submagic.co](https://www.submagic.co/)).

### Word-level emphasis

- The underlying mechanism is the ASS/SSA karaoke tag family (`\k` instant color swap, `\kf`
  sweep fill) at 10 ms resolution ([aegisub.org](https://aegisub.org/)) — renderer precision is
  never the constraint; timestamp quality is.
- Two coexisting style families, no evidenced winner:
  **(a) full block visible, active word recolored** (CapCut, Submagic, Hormozi-style — majority);
  **(b) progressive reveal**, words appear as spoken (minority). Family (a) is
  lower-complexity and more convergent.
- The "Hormozi" preset that dominates 2026 short-form: ALL CAPS white text, thick black stroke,
  yellow `#f7c204` pill behind the active word ([submagic help](https://care.submagic.co/)).

### Block transitions: swap, not scroll

- No usability study directly compares scroll-up vs page-swap at short-form pace. But all five
  audited tools (CapCut, Submagic, Opus Clip, Descript, Veed) **replace** blocks in place; none
  scroll. BBC's scroll-up recommendation is explicitly scoped to *live* captioning — a real-time
  broadcast constraint that doesn't apply to pre-scripted content.
- **Consequence for us:** the original "lines move up" idea should become "block swaps in place."

### Typography

- BBC anchors caption size at ~72 px absolute on both 1080-tall and 1920-tall frames
  (6.667% landscape / 3.75% vertical of frame height — [bbc/ttml-validator](https://github.com/bbc/ttml-validator), T.8).
  Attention-style presets (Opus/Hormozi) run much bigger: caption block 12–20% of frame height.
- ALL CAPS vs sentence case is a genuine disagreement: readability research says all-caps blocks
  read 10–15% slower and Netflix mandates sentence case for dialogue; the 2026 short-form *trend*
  is ALL CAPS. This is a brand/taste fork, not a solved question.
- Contrast: WCAG 1.4.3 wants 4.5:1, but assumes solid backgrounds; over video the convergent
  technique is thick dark stroke + soft shadow (no solid box) ([w3.org](https://www.w3.org/TR/WCAG21/)).

### Two-speaker handling

- Netflix: hyphen-prefixed lines, one speaker per line. BBC: color per speaker, capped to an
  accessible 4-color palette (white/yellow/cyan/green). Short-form podcast-clip convention:
  **distinct caption color per speaker**, no name tags ([convergent across multiple tool guides](https://www.yumcut.com/)).

### Timing feel

- Block in-cue within 1–2 frames of audio onset (Netflix, primary).
- Word highlight should fire **~50–100 ms before** the word's spoken onset — late highlights read
  as laggy (practitioner consensus, not a controlled study — flag as heuristic).
- Karaoke-visible error threshold: ~100–200 ms. Anything sourcing timestamps must beat that.

---

## 2. Word timing — where timestamps come from

We never need speech-to-text: `script.json` is ground truth. The problem is **forced alignment**
(snapping known text onto audio) or **synthesis-native timestamps**.

### The winner: ElevenLabs `POST /v1/forced-alignment`

Verified directly against the [official API reference](https://elevenlabs.io/docs/api-reference/forced-alignment/create):

- Multipart upload: audio `file` (any major format, <1 GB) + plain `text` transcript.
- Returns **native word-level** `words[]` — each `{text, start, end, loss}` (seconds; `loss` is a
  per-word alignment-confidence score) plus a `characters[]` array.
- Works on **already-synthesized MP3s from either provider** (ElevenLabs or Gemini episodes) —
  honors the "never re-synthesize" rule.
- Zero new Python dependencies (raw urllib multipart, matching the existing ElevenLabs call
  style); same `ELEVENLABS_API_KEY` already in `.env`.
- Priced at the Speech-to-Text rate, $0.22/hour of audio
  ([capability page](https://elevenlabs.io/docs/overview/capabilities/forced-alignment),
  [pricing](https://elevenlabs.io/pricing/api)) — **under one cent per promo clip**.
- Pre-processing required: strip `HOST:`/`GUEST:` labels and `[laughs]`-style audio tags from the
  transcript before upload (endpoint takes plain text, no diarization); use per-word `loss` to
  sanity-check spans around laughs.

### Future optimization: `POST /v1/text-to-dialogue/with-timestamps`

- Exists, defaults to `eleven_v3`, same `inputs` body as our current call
  ([API ref](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert-with-timestamps)).
  Response becomes JSON: `audio_base64` + character-level `alignment`/`normalized_alignment` +
  `voice_segments`.
- Timings are emitted by the synthesis model itself — ground truth by construction, better than
  any post-hoc aligner. But: chunk-relative timestamps need cumulative-duration offsets (our
  pipeline chunks turns), audio arrives base64 not raw, and character→word grouping is on us.
- **Optimization, not requirement** — forced alignment alone covers every case including
  back-catalog audio. Adopt later if alignment quality around audio tags disappoints.
- Undocumented: how `[laughs]` tags appear in the character alignment arrays. One sub-cent test
  call settles it.

### Rejected alternatives (and why)

| Option | Why not |
|---|---|
| Gemini TTS timestamps | None exist — response is audio bytes only ([docs](https://ai.google.dev/gemini-api/docs/speech-generation)) |
| whisperX local alignment | ~110 ms mean word-boundary error ([2026 survey, arXiv 2606.18466](https://arxiv.org/html/2606.18466v1)) — inside the visible-error band — and a multi-GB torch stack against our dependency budget |
| Montreal Forced Aligner | Best measured accuracy (~14–25 ms) but conda-locked Kaldi install; keep as offline fallback only |
| stable-ts | Archived read-only May 2026 |
| whisper-timestamped | AGPL; no known-transcript mode |
| OpenAI/Deepgram/AssemblyAI STT APIs | ASR-based — ignore our ground-truth transcript, can mis-transcribe and break word mapping |
| Google Cloud STT | Word offsets quantized to 100 ms ([docs](https://docs.cloud.google.com/speech-to-text/docs/v1/speech-to-text-requests)) — equals the visibility threshold by design |

---

## 3. Safe areas and layout

### LinkedIn (1080×1080 primary)

- Official aspect ratios: 1:1, 4:5, 9:16, 16:9; **9:16 renders on mobile only** — square keeps
  desktop reach ([LinkedIn video specs](https://business.linkedin.com/advertise/ads/sponsored-content/video-ads/specs)).
- LinkedIn paints **no UI over the frame** — controls live in a thin bottom strip on hover/tap
  ([player controls](https://www.linkedin.com/help/linkedin/answer/a566568)). Feed video autoplays
  **muted** ([official](https://www.linkedin.com/help/linkedin/answer/a565326)) — the standing
  justification for burned-in captions. (The oft-quoted "79–85% watched muted" stat is from 2021;
  treat as directional.)
- Layout: nominal 60 px margins as hygiene + 80 px extra clearance at the very bottom for the
  control strip.

### Vertical multi-platform master (1080×1920)

Worst-case intersection of TikTok + Meta Reels + YouTube Shorts safe zones
(Meta official: [Business Help Center](https://www.facebook.com/business/help/980593475366490);
TikTok official-qualitative + convergent numeric secondaries; Shorts numbers are third-party
estimates — lowest confidence):

| Edge | Clear margin | Driven by |
|---|---|---|
| Top | ~270 px (14%) | Meta Reels status/search area |
| Bottom | ~670–700 px (35%) | Meta Reels caption/CTA stack |
| Right | ~210 px (19%) | TikTok/Shorts engagement rail |
| Left | ~70 px (6%) | Symmetry margin |

Safe content core ≈ **800×950 px, slightly left of center**.

### Wordmark corner: top-left

- No platform's persistent UI ever touches top-left. Right edge carries the TikTok/Shorts
  engagement rail; bottom-right collides with TikTok's spinning music disc (explicitly the worst
  corner). LinkedIn doesn't care, so top-left works for both masters — one convention everywhere.
- Broadcast standards corroborate inset discipline: SMPTE ST 2046-1 and EBU R95 independently set
  action-safe at 3.5%/edge and graphics-safe at 5%/edge
  ([SMPTE](https://pub.smpte.org/pub/st2046-1/st2046-1-2009.pdf), [EBU](https://tech.ebu.ch/publications/r095)).
- Size: 5–10% of frame width for an active brand mark.

### Caption band

- Square: lower-middle third, y ≈ 550–850 px on 1080 (below a centered cone, clear of the player
  strip).
- Vertical: **y ≈ 960–1220 px (50–64% of frame height)** — the only band simultaneously clear of
  every platform's top and bottom exclusions. Reads center-weighted; tool guidance agrees
  (middle-to-lower-middle, never bottom quarter —
  [caption placement guide](https://blitzcutai.com/blog/best-caption-placement-short-form-video)).

### Square→vertical strategy

- Keep all critical content (cone, captions, wordmark position logic) inside a centered
  1080×1080 region of the 1920-tall canvas; extend the ink background vertically. One composition
  engine, two crops. (Meta's 2026 guidance prefers per-placement assets, but center-safe design
  is the accepted single-master fallback.)

---

## 4. Recommended design (synthesis)

| Parameter | Recommendation | Grounding |
|---|---|---|
| Timestamp source | ElevenLabs `/v1/forced-alignment` on the finished MP3 | §2 — zero deps, works on back catalog, <1¢/clip |
| Wordmark | Top-left corner, ~5.5% inset, ~⅓ current size | §3 — only universally safe corner |
| Caption block | 1 line, 3–6 words, centered, beneath the cone | Netflix 1-line preference + short-form convergence |
| Font | Space Grotesk Bold, ~8% of frame height (~86 px @1080) | Splits BBC accessibility anchor (6.7%) and Hormozi attention style (12–20%) |
| Case | Sentence case | Readability evidence + Netflix dialogue rule; ALL CAPS is the trend but reads 10–15% slower — brand-taste fork, flagged |
| Contrast | Ink-dark stroke + soft shadow, no background box | Convergent tool technique; preserves the cone composition |
| Word highlight | Full block visible; active word recolored + ~105–110% scale pop | Majority style family; pill/box would fight the cone visually |
| Speaker distinction | Two caption colors (HOST vs GUEST) drawn from brand spectrum, no name tags | BBC color-coding convention, zero vertical cost |
| Block transition | Swap in place (no scrolling) | Universal tool default; scroll is a live-broadcast artifact |
| Highlight timing | Fire 50–100 ms before word onset; block in-cue within 1–2 frames of speech | Netflix spec + practitioner consensus |

### Open questions before spec

1. **Empirical:** how do `[laughs]` audio tags come back from forced alignment — clean skip or
   smeared boundaries? (One sub-cent test call.)
2. **Brand:** sentence case (recommended) vs ALL-CAPS trend style — taste decision.
3. **Speaker colors:** which two spectrum hues read as HOST/GUEST while meeting contrast on ink
   (`#101322`-ish ground)? Needs a render test against BRAND-SPEC.
