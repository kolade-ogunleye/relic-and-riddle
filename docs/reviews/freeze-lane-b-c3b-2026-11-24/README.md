# Freeze lane B, cycle 3b: one pre-draw figure gate (2026-11-24)

## The defect

The int-3 board (claude/freeze-int-3 at 714b6f16) drew the Amber Room Reel cover and grid tile as
"THE 6-TON ROOM / STOLEN IN 36 HOURS". On the same run, `visual_engine.youtube_headline_chain` refused that line:
`evidence_faults("THE 6-TON ROOM")` returns `'6 TON': no signed figure has this number in this unit`. The Reel path
(`VisualEngine.generate_thumbnails` -> `cover_design.render_cover`) checked the hero figure only against the fact
sheet (`cover_figure` / `must_withhold`). Its lines were never checked against the evidence.

## Ruling applied

A figure the run's evidence does not sign is never drawn on any surface. Every surface that draws a figure taken from
copy calls one function on the text it will draw: `visual_engine.gate_drawn`. It runs after the surface's unit
conversion and before any layout, and it works like this:

- **Check.** Each figure goes through `evidence_faults`, with its hedge and both ends of a span kept.
- **Lift out.** A figure that fails is removed as `figure_free` removes it. The line stays if what is left still
  reads; no word is added.
- **Record.** The withheld figure goes on the surface's own record as `figure_withheld`, and on the run's ledger in
  `payload["layout_holds"]`, which is what the review card's Held list and `figure_gate.held_note` read.
- **Nothing readable left.** The surface tries the next candidate in its own chain: the tagline alternates, the
  YouTube chain, or the plain share line. If there is none, the surface is refused with `refusal.Declined`, rule
  `figure_gate.evidence`. It is never drawn with the figure and never drawn empty.

## Inventory (find-all, before the change)

The code inventory is `visual_engine.DRAWN_FIGURE_PATHS`. `tests/test_figure_gate_surfaces.py` holds it in two ways:

- every "gate" entry must call `gate_drawn` or `gate_deck`;
- every function in a renderer module that reads a drawn copy field must be listed.

| Surface | Path | Before (714b6f16) | After |
|---|---|---|---|
| Reel cover and grid tile | `VisualEngine.generate_thumbnails` -> `cover_design.render_cover` | Fact sheet only (`cover_figure` / `must_withhold` on the hero figure). No `evidence_faults`. "THE 6-TON ROOM" drawn. | `gate_drawn` on eyebrow, line1, line2, tagline and badge. The grid tile is the cover's crop. |
| YouTube headline | `youtube_headline_chain` / `_chain_candidate` | `evidence_faults` on each candidate; `figure_free` variant; `drawn_faults` after render | Unchanged, now through `gate_drawn` |
| YouTube kicker and subline | `_chain_candidate` | Gated only on a figure-withheld variant; `drawn_faults` judges title rows only | `gate_drawn` on every candidate |
| YouTube badge (figure branch) | `youtube_render_inputs` | No pre-draw check (`drawn_faults` after) | `gate_drawn` |
| Instagram and Facebook carousel heading and body (colossal-stat figure row read from the body) | `generate_carousel_sequence`, `_render_colossal_stat` | `figures.truth_faults(stat, [body])`: the figure is checked against its own body, never the evidence | `gate_deck` -> `gate_drawn` |
| Carousel poll question, closing share line | `generate_carousel_sequence`, `script_engine.share_prompt` | None | `gate_drawn`; share line falls back to the plain line |
| Story frames (line1, line2, body) | `generate_story_sequence` | None | `gate_deck` -> `gate_drawn` |
| Photo callouts | `callouts.make` (`photo_labels`) | None (vision locate/verify only) | `gate_drawn` on each label |
| End card share line (9:16 loop bridge; drawn only with `LOOP_LINE`) | `script_engine.end_card_share` | None | `gate_drawn`; falls back to the plain line |
| Quote card | `design_cards._quote_gates` | `verify_field` and attribution checks; no figure check | `gate_drawn`; an unsigned figure refuses the card (a quotation is drawn whole) |
| Hook card | `design_cards._hook_claim` | `ground_line` / `ground_text` against the cited facts, plus `figure_check` | Equivalent, unchanged |
| Chapter card | `design_cards.plan_chapter_card` | `ground_line` / `ground_text` | Equivalent, unchanged |
| Myth card correction | `design_cards.plan_myth_fact` | `ground_text` | Equivalent, unchanged |
| Myth card myth line | `design_cards.plan_myth_fact` | Not checked | **Exempt (for a lander ruling):** it is the refuted claim, drawn under its MYTH label |
| Number (stat) cards, scale, cross-section, route, timeline | `graphics.render_*` | Draw the evidence fields themselves | Evidence; the post-render `figure_gate.check_run` still judges rewrites |
| Map scale bar, then-and-now years, 16:9 end screen | `graphics.render_map`, `then_now.make`, `end_card.frames` | Derived or structural | Derived |
| Captions' figure bursts | `VideoComposer.build_subtitles` | Narration words; `check_run` reads the sequence | Evidence (the narration is evidence) |

## Evidence-gate fixes found on real data

These came up while gating mid-sentence copy:

1. **The fact sheet was not evidence.** Payloads carry `fact_sheet: true` and a sheet path, so `evidence_texts` never
   read the sheet. Richat's "between 94 and 104 million years ago" and Greek fire's "10th century" failed as
   unsigned. The sheet is now loaded through `fact_sheet_for` (cached), and a disputed fact counts only through its
   hedge, as `figure_gate.evidence_texts` reads it.
2. **A figure is judged whole.** It is judged as parsed (`Figure.drawn()`), not as a slice of its characters. The
   slice lost a sentence-lead hedge ("By one estimate, ... 144 feet") and a span's first end ("between roughly 41.75
   metres and 42 metres").
3. **An ordinal is judged as an ordinal.** "21st century" is signed by "twenty-first century".
4. **A cut that tears its sentence leaves nothing drawn.** Examples: "CARVED FROM [900 TONS] OF SALT", a cut inside a
   word, or "STOLEN IN [36 HOURS]". The surface's chain or refusal then decides.

## Dev runs where a figure is now withheld (output/run_*, 34 runs with a verified script)

| Run | Surface | Withheld | Drawn after |
|---|---|---|---|
| amber_room_heist_1792790550 | cover line1 | 6 TON | THE ROOM / STOLEN IN 36 HOURS |
| gol_gumbaz_whispering_gallery_1793278143 | cover line1 | 144 FT (signed: estimated) | WHISPERING DOME |
| hal_saflieni_hypogeum_1792949304 | cover tagline | 7,000 (signed: approximately) | RESTING SOULS |
| hal_saflieni_hypogeum_1792949304 | carousel slide 4 heading | 7,000 | deck refused (heading empty) |
| hal_saflieni_hypogeum_1792949304 | Story frame 2 line2 | 3 LEVELS (parser miss, see below) | line1 only |
| kailasa_temple_monolith_1792524470 | carousel slide 7 body | 12 CENTURIES | deck refused |
| nazca_lines_runway_paradox_1792961797 | carousel slide 1 body, Story frame 1 line2 | OVER 2 THOUSAND YEARS | deck refused; Story line1 only |
| odessa_catacombs (1792970549, 1793098390, 1793100750, 1793159709) | carousel slide 5 heading | 73 DAY (signed: estimated) | THE SIEGE |
| richat_structure_sahara_eye_1792737039_short | carousel slide 5 body | APPROXIMATELY 98.2 MILLION YEARS AGO | deck refused |
| richat_structure_sahara_eye_1792778206 | carousel slide 6 body, Story frame 2 line2 | 145,000 YEARS AGO; 100 MILLION YEARS | figure-free body; Story line1 only |
| singing_colossi_of_memnon_1792952878 | cover line2 | 720 TON | THE SINGING |
| tunguska_event_1908_1792930600 | cover line1 | 830 SQ MI (signed: more than) | FLATTENED FOREST |
| unfinished_obelisk_aswan_1792985912 | cover line1 | 1,200 TON | BEDROCK MONSTER |
| unfinished_obelisk_aswan_1792993374 | Story frame 2 | OVER 1,000 TONS; UP TO 1,168 TONNES | Story refused |
| wieliczka_salt_mine_1792974672 | cover tagline, badge | 327 M (signed: about) | tagline left out (no alternate) |

**Totals:**

- Runs with at least one figure withheld: 16 of 34. In 13 of them a figure is lifted out and the surface is drawn
  without it. In 3 more (Kailasa, Richat 1792737039_short, Aswan 1792993374) the only effect is a refused surface.
- By surface: cover 7, carousel 5, Story 3, callouts 0, share line 0.
- Surfaces refused: 5 runs. Four carousel decks (Hal Saflieni, Kailasa, Nazca, Richat short) and one Story
  (Aswan 1792993374).
- The YouTube chain's truth verdicts are unchanged on every run (0 flips).

**Known false positive:** "IN THREE LEVELS" (Hal Saflieni). The evidence says "three superimposed underground
levels", and `figures.parse` does not join a count across two adjectives. It fails closed: the figure is withheld,
never drawn unsigned. That is a parser item, outside this lane.

## Evidence renders

The renders are in C:\tmp\qf-handoff\queue\LANE-B-c3b-evidence\. Each is at published size, before | after, with a
label strip built from the render's own record.
