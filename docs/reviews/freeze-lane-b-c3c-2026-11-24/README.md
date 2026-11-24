# Freeze lane B, cycle 3c: a missing signed hedge is drawn, not the figure lost (2026-11-24)

## The problem (quality, not truth)

Lane B c3b's pre-draw gate (`visual_engine.gate_drawn`, see `../freeze-lane-b-c3b-2026-11-24/README.md`) withholds
every figure `evidence_faults` refuses. One kind of refusal had a truthful repair: the figure is signed, but only with a
bound or approximation hedge the drawn line lacks.

On `run_tunguska_event_1908_1792930600`, the cover's line1 "830 SQUARE MILES" failed with
`'830 SQ MI': the signed figure is hedged (MORE THAN) and the drawn one is not`. The evidence says "more than 830 square
miles" and "Est. >830 sq mi". The cover drew "FLATTENED FOREST" alone, although "OVER 830 SQUARE MILES" is true.

## The rule

The gate is unchanged: it checks every figure, and it withholds and records what it cannot draw. What changes is a
figure whose only fault is a missing hedge. When the surface passes a `hedged` list, that figure is drawn with its
signed hedge's short form instead (`visual_engine.hedge_figure`). The hedged line is checked again with
`figure_gate.headline_figure_faults`: it must be signed, whole, and carry a hedge of the signed one's kind. A line that
fails that check is withheld, so the gate never fails open.

### Hedge mapping

The short form comes from the signed figures' own hedge. It is read with the parser's existing vocabulary
(`figures.HEDGES`, `HEDGE_DRAWN`) and direction classes (`HEDGE_LOWER`, `HEDGE_UPPER`). There is no second vocabulary.

| Signed hedge (as parsed) | Drawn |
|---|---|
| Any lower bound (`HEDGE_LOWER`): more than, over, at least, just over, well over, est. > | OVER |
| up to, as many as, as much as | UP TO |
| nearly, almost, just under | NEARLY |
| less than, fewer than, under, est. < | UNDER |
| about, around, roughly, approximately, some, circa, ~, an estimated, est. | ABOUT |

- **OVER, never MORE THAN.** It is the same lower bound (`hedge_direction`) in one word, so the line keeps more of
  its size.
- **One form only.** Every signed statement of the figure must give the same short form, or nothing is drawn. For
  example, "up to" in one statement and "nearly" in another draws nothing.
- **UNDER is a reading.** The brief listed no form for "less than / fewer than". UNDER is the parser's own word for
  that class. NEARLY would claim the value is close, which "less than" does not say.

### Attribution stays withheld

A signed figure is an attribution when any one of these holds:

- its hedge is the sentence's lead (`Figure.lead_hedge`), for example "By one estimate, the siege lasted 73 days";
- it comes from a disputed fact's hedge;
- its sentence names who says it or says it is contested (`ATTRIBUTION`: scholars, historians, legend, tradition,
  disputed, according to, claimed, reportedly, said to, believed and similar).

One hedge word would misstate an attribution. If any signed statement of the figure is an attribution, the figure stays
withheld. A figure that has its own bound beside the number keeps that bound even under a lead. Tunguska's narration
says "By one estimate, the blast devastated more than 830 square miles". The signed figure is MORE THAN 830 SQ MI, so
OVER is drawn.

### Where the hedge goes (the article rule)

The hedge goes immediately before the figure group, in the line's case. A line in capitals gets capitals. A
sentence-case line gets a capital only at the start of a sentence. The hedge is set only when the figure opens the line
or its clause, or follows a word that is not a determiner. It is not set, and the figure stays withheld, when:

- **The figure follows a determiner.** Determiners are an article, a demonstrative, a possessive, or EACH / ALL / NO
  and similar. "THE 6-TON ROOM" can never become "THE OVER 6-TON ROOM".
- **A hyphen joins the figure to its noun.** "720-TON TITAN" and "THE 144-FOOT" are adjectives, and a hedge word
  does not read before an adjective.
- **The figure is the far end of a pair.** In "30 and 70 metres", a hedge on 70 would bound only one end.
- **Another figure is cut from the same line.** The cut changes the sentence the hedge would read against. The line
  is gated plain.

## Fit: each surface's own size rules, else the withheld form

Each surface draws the hedged form only where it holds at its normal size rules. It gets no new size site and no
size below an existing floor. Otherwise it draws the plain gate's form, with the figure withheld exactly as in c3b.

| Surface | Hedged form drawn when | Else |
|---|---|---|
| Reel cover and grid tile (`generate_thumbnails`) | It draws in the style the cover chose, with no `TextFitError` or `LayoutError` and no grid fallback to another style or `grid_hold`. A tight redraw in the same style is allowed. | Drawn from the plain copy, through the c3b path (refusal, tagline fallback) |
| YouTube headline (`youtube_headline_chain`) | A truth-refused line that lacks only its hedge becomes its own candidate ("figure hedged"), in that line's rank, held to every chain gate and the render's checks | The chain continues, as in c3b. That line's figure-withheld form stays at the end of the chain. |
| YouTube kicker and badge (`_chain_candidate`, `youtube_render_inputs`) | The candidate renders and passes its checks, and the hedged kicker is drawn whole | The candidate is drawn once more with the plain kicker and badge before the chain moves on |
| Carousel, Instagram and Facebook (`generate_carousel_sequence`) | The deck's heading tier and both copies' heading frames are unchanged, and the slide is drawn with no problem and no shortened field | `revert_hedges`: that slide (and a hedged poll question) takes its plain form. A plain form left with nothing readable refuses the deck, as in c3b. |
| Story frames (`generate_story_sequence`) | The Story's one title size is unchanged, and the frame is drawn with no problem and no shortened field | `revert_hedges`, as for the carousel |
| Photo callouts (`callouts.make`) | The hedged label sets on its plate at the plain label's size or larger (`label_holds`) | The plain label |
| Carousel share line, end-card share (`share_prompt`, `end_card_share`) | Nothing else is withheld, and the line still passes `share_line_problems` (the end card also keeps at least 3 words) | The plain fallback line, as in c3b |
| Quote card (`design_cards._quote_gates`) | Never. A quotation is not edited. | It refuses, as in c3b |

### The YouTube figure branch keeps a hedge with its figure

The first render of the Tunguska thumbnail drew "OVER" twice. The figure branch lifted "830 SQUARE MILES" out of
line1 as its hero, with OVER as its qualifier, and left "OVER" behind as a headline row. `youtube_thumbnail.render` now
lifts a hedge that sits beside the number (`stated_figure`, not a sentence lead) out of the line together with the
figure. The hedge is drawn once, as the figure's qualifier. The same applies to any copy that writes its own hedge,
such as "ABOUT 45 M DEEP". The covers draw the hedged line as written in all five styles: badge, monument,
lower_third, minimal and depth_sandwich, checked on Tunguska.

## Records

- **Surface record.** Each surface's record gets `figure_hedged`: `{"rule": "figure_gate.hedge", "figures": [{surface,
  field, text, drawn, hedged, hedge, signed, reason}]}`. It sits beside `figure_withheld` when other figures were
  withheld. The YouTube `.qa.json` and the review card's thumbnail note name the hedge too.
- **Run ledger.** The run's ledger gets `payload["figure_hedges"]`, one row per surface and figure. `main.py` resets it
  with `layout_holds`. The review card lists it as "〰 Drawn with the signed hedge: n"
  (`telegram_bot.figure_hedge_lines`).
- **No hold.** `payload["layout_holds"]` is untouched for a hedged figure, because the figure is drawn.
- **Declined hedge.** A withheld row whose hedge was considered and declined carries `hedge_declined`, the reason.

## Dev runs (output/run_*, 34 runs with a verified script)

c3b withheld 22 figures across these runs. c3c draws 3 of them with their signed hedge, at the gate level. The renders
below confirm that each one fits. The YouTube chain changes on 2 runs.

| Run | Surface | c3b drew | c3c draws |
|---|---|---|---|
| tunguska_event_1908_1792930600 | Reel cover and grid tile, line1 | FLATTENED FOREST (830 SQ MI withheld) | OVER 830 SQUARE MILES / FLATTENED FOREST |
| tunguska_event_1908_1792930600 | YouTube thumbnail | carousel_cover source: ESTIMATED MORE THAN / 830 SQ MI | thumbnail source (figure hedged): OVER / 830 SQ MI / FLATTENED FOREST |
| hal_saflieni_hypogeum_1792949304 | Reel cover tagline | RESTING SOULS (7,000 withheld) | ABOUT 7,000 RESTING SOULS |
| hal_saflieni_hypogeum_1792949304 | carousel slide 4 heading (Instagram and Facebook) | deck refused (heading emptied) | RESTING PLACE OF ABOUT 7,000; the deck is drawn |
| hal_saflieni_hypogeum_1792949304 | YouTube kicker | CARVED DEEP / UNDERGROUND | unchanged: the render does not draw the hedged kicker whole, so the candidate is drawn with the plain kicker (no hedge recorded) |

Still withheld, with the reason:

- **Odessa** (4 runs), "THE 73-DAY SIEGE": an attribution. The hedge is the sentence's lead ("By one estimate").
- **Gol Gumbaz** "THE 144-FOOT" and **Wieliczka** "327 METERS DOWN" and the "327.0 M" badge: an attribution. The figure
  comes from a disputed fact's hedge.
- **Memnon** "720-TON TITAN": a hyphen joins the figure to its noun.
- **Amber** "THE 6-TON ROOM", **Aswan**, **Nazca**, **Kailasa**, **Richat**, and Hal Saflieni's "3 LEVELS": the fault
  is not a missing hedge (the figure is unsigned, or is one end of a span).

The count script is `count.py` in the evidence folder (c3b's, extended). Its output is `hedge_counts.json`.

## Evidence renders

The renders are in C:\tmp\qf-handoff\queue\LANE-B-c3c-evidence\. Each is at published size, uncropped, before | after,
with a label strip built from the render's own record. The hero is the topic's first photo: these runs predate a
recorded cover hero.
