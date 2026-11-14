# QF-81: carousels drawn on demand or at their slot, audited as before; Instagram album insights; carousels vs Reels

Spec (project-quality-review-2026-11-05.md, QF-81): seven carousels were drawn a week (both copies, the photo match
and the per-slide vision look of the quality audit) for three Tue-Thu slots with a 7-day window, so most expired
unposted; `audience._graph_metrics` skipped every `_carousel` label, so a carousel's saves and reach were never read
from Meta's insights. Fix: draw lazily for the run that takes the slot; enable Graph insights for carousel albums.
Acceptance: `carousel/` exists only for runs whose carousel took a slot; the digest prints saves/reach for carousels
against Reels after 12 posts.

Operator ruling 2026-11-10 (decisions\FREEZE-RESUME-rulings.md, Q4): instrumentation only (lazy drawing, insights);
**no automatic Facebook-gallery cut-off**; the operator decides the Facebook gallery once 12 real posts are measured.
So the spec's "FB gallery off until FB Reel reach is at least half of IG's" is not built: Facebook keeps its grid
copy, drawn as before (now on demand or at the slot), and the digest prints the number that decision needs, or says
plainly that it cannot be worked out yet. The 12-real-post observation itself cannot be produced offline; it stays
open after this row lands (see "Open after landing").

Round 2 answers review 1 (queue\QF-81.review-1.md) and the lander's decision under delegation (decisions\QF-81.md,
"Preview and recovery": (b) + (c) + (d)).

Base 2debdf8b (batch 6: QF-80, QF-82, QF-83, QF-11). The older candidate 3ffc4ce1 (on 05f6c0a2, never reviewed) was
not ported: it carried the half-of-Instagram Facebook policy the ruling removed, imported `story_policy` (ruled out
with QF-80 option 1) and `run_cost` (QF-68), and changed the publisher. src/publisher.py and src/posting_view.py
(QF-83's) are untouched.

## What changed

- `src/carousel_lazy.py` (new).
  - `plan(run_dir, stills, payload)`, while the run is made: `carousel_plan.json` records the photos the carousel may
    use (the topic's own, `image_review.only_strong`, each with its SHA-256), the diagram posters
    (`graphics/graphic_*.jpg`, with theirs) and a digest of the script fields the drawing reads (`carousel_slides`,
    `poll`, `narration_script`, `share_line`). Slides an earlier make of the same run folder drew are removed.
  - `draw(...)` draws both copies into the run's staging folder `carousel_draw/`, never into `carousel/` (which the
    review fingerprint binds and the publisher posts from). Before drawing it holds on any recorded input that changed
    (a photo missing or changed, a poster changed, the cards changed). The photo match is strict: a failed match holds
    the carousel (`multi_format.PhotoMatchFailed`), it never rotates the photos in silently. A failed match includes
    a plan made without the vision planner: `scene_planner.plan_scenes` catches the planner's failures itself and
    returns filename order (the rotation) or caption order, saying so in its notes, and the strict match holds on both
    with the reason. It tries the planner `CAROUSEL_PLAN_ATTEMPTS` times (default 2, first wait
    `CAROUSEL_PLAN_RETRY_SECONDS`, 15 s) instead of the run's 3 tries and about 3 minutes, so the slot decides promptly;
    Redraw tries again for the next slot. A file error while drawing or installing holds too, with its reason, and a
    partly installed copy is cleared. The audit is the run's
    old one: `QualityAuditor.audit_carousel_slides` (measured layout, the per-slide vision look) on the Instagram copy,
    plus the delivered text contrast of **both** copies (`video_qa.still_contrast_problems`, major under
    `brand.TEXT_CONTRAST`, minor when unreadable, exactly as `text_contrast_audit` raised them for the release gate),
    held by the release gate's severity rule (critical and major always, minor when `MINOR_FINDINGS_BLOCK_RELEASE` is
    on, info never). Glyph events of the drawing go to the run's own `glyph_faces.jsonl`
    (`font_engine.glyph_ledger_for_thread`: a thread-local ledger, so a run being made meanwhile keeps its own).
    Everything is kept in `carousel_draw/draw.json`: the verdict, every finding, the vision look's info findings, the
    Facebook collage notes, the glyph notes, a Facebook copy that could not be drawn, each slide's alt text per copy,
    the slides' and the photos actually used (with SHA-256s), and a contact sheet. The record is bound by `digest` to
    the plan, the photos and posters as they are, the fields, and the drawing code and fonts: while none changed, the
    preview's drawing is the slot's (drawn and paid for once). A drawing that failed for a reason a new attempt may not
    meet (a match, an exception, an audit that could not run) is recorded but never reused.
  - `render(...)`, at the slot: the drawing (reused or new); unheld, it is installed into `carousel/` and
    `carousel_fb/` (SHA-256 checked) and its slides are returned; held, it raises `CarouselHeld` with the record.
  - Held slides: never installed. They stay in `carousel_draw/` beside the record that says why, so the preview and
    the alert show what was held, until 🔁 Redraw (`discard`) or a new make of the run clears them. So `carousel/`
    holds slides only for carousels that posted (or are posting).
- Review card: a "🖼 Preview carousel" button (callback `ccar:<run>`, registered in `telegram_ui.SCREENS`, a panel
  action run as a background job). It draws both copies on demand, runs the full slot audit and shows, in the one
  navigator panel (no new standing message), page 0: both copies on one sheet, the verdict and every finding; pages
  1..n: each slide with its Instagram alt text (and Facebook's where it differs). Paging reuses the drawing. The card's
  list says "Carousel slides and their alt text: drawn ... when you tap 🖼 Preview carousel or when it takes its
  carousel slot, and checked before they post", and the stills album caption says the carousel is drawn at its slot
  and the preview shows it now.
- main.py: making a run calls `carousel_lazy.plan` instead of drawing; `_book_followers` books a planned carousel
  `waiting` with no files (a run drawn before QF-81 books its slides as before); `_post_carousel_if_due`, after the
  existing slot choice and once-a-day claim, renders (under a per-run lock shared with the preview), stores the files
  on the booking (`db.update_scheduled_post(..., files=...)`) and posts as before. A held carousel: its booking is
  `failed` (`held`), and **one** alert names the reason and every finding (look, collage, lettering) with
  "🔁 Redraw and post next slot" (`carredraw:<booking>` → `redraw_carousel`). Redraw discards the held drawing and
  puts the booking back `waiting`, first in line for the next carousel slot inside its window (ahead of newer
  episodes, still one carousel a day), where it is drawn afresh and audited before it posts; with no slot left in its
  window it says so and the booking expires. The dry-run receipt says the carousel is drawn only when it takes a slot.
- `publishable.card_record` carries the carousel caption for a planned run (without it, a script with no carousel
  caption of its own would have been refused at the slot by `carousel_post_base`).
- src/audience.py: an `instagram_carousel` is measured from Instagram's Graph (album matched by
  `media_type == CAROUSEL_ALBUM` only; `IG_CAROUSEL_METRICS`: views, reach, saved, shares, likes, comments,
  total_interactions, the FEED metrics of the instagram-media insights reference, read 2026-11-11). Upload-Post stays
  the fallback; `facebook_carousel` stays with Upload-Post. Digest (`carousel_comparison`): per platform at the 7-day
  snapshot, "n of 12 carousels measured" until 12, then medians of reach and saves per account reached for carousels
  and Reels; then `facebook_reach_lines`: "Facebook Reel reach is X% of Instagram's (... Facebook's unique impressions
  against Instagram's accounts reached)", or, when Facebook's rows carry no reach, "Facebook Reel reach not measured
  (7d): its n row(s) carry no reach (source ...)" and, labelled as such, "Instead, views (not reach): Facebook Reel
  views are X% of Instagram's".

## Decoupled from QF-68 (decision under delegation, 2026-11-11)

The old candidate submitted the slot drawing through `run_cost` (QF-68, cost per Reel, NOT OK at r4) so its provider
calls were charged to the run. This build drops that coupling: the drawing calls the renderer and the audit directly,
with no cost scope, and imports nothing from QF-68. So the carousel's photo match and its per-slide vision look run
(once per drawing, preview or slot) and are logged, but are not attributed to any run's cost. QF-68 will add: wrapping
`carousel_lazy.draw` in the run's cost scope (opened from the run id, since the drawing happens at the preview or days
after the run), so the carousel's calls appear in that Reel's cost per Reel, and a cost line for held drawings.
Nothing here changes provider settings, budgets or spending caps.

## Measurements

`measure.py <repo root>` (offline, runs on the parent too): three weeks, one episode a day, the checkout's own stills
block compiled from main.py and run per episode, the real `_book_followers`, and the real daemon pass twice at every
carousel slot.

| | parent 2debdf8b | QF-81 |
|---|---|---|
| episodes / carousel slots | 21 / 9 | 21 / 9 |
| carousel copies drawn while the run is made | 42 | 0 |
| copies drawn at the slot | 0 | 16 |
| copies drawn in total | 42 | 16 |
| slide audits (per-slide vision look) | 21 | 8 |
| carousels posted (duplicates) | 8 (0) | 8 (0) |
| carousel status: published / expired / still waiting | 8 / 5 / 8 | 8 / 5 / 8 |
| run folders holding carousel slides | 21 | 8 |
| drawn but never posted | 13 | 0 |
| folders with slides are exactly the posted ones | no | yes |

A preview adds no drawing when the carousel later takes its slot unchanged (test: drawn once, posted). before.json /
after.json hold the two outputs; real_data.json the real-data counts below (real_data.py, equivalence.py).

Real data (counts only; scripts outside the repository):

- Production sample (copy of metrics.json and the runs' carousel folders): 34 of 34 runs drew a carousel (34 with
  Facebook's copy too); carousels of 4 runs were published (8 carousel publications); 30 drawn carousels never
  posted (this includes runs that were never approved, which drew one all the same). Every carousel metric row came
  from Upload-Post (10 Instagram, 10 Facebook), none from the Graph; 10 carry reach and saves.
- Production sample, digest lines (`carousel_comparison` on its metric rows in a fresh temporary database): 2 "n of 12
  carousels measured" lines (Instagram, Facebook); the 6 Facebook Reel rows at 7 days carry 0 reach, so the reach
  ratio cannot be worked out: 1 "Facebook Reel reach not measured" line and 1 labelled views-ratio line print (review
  1 found no line at all).
- Dev: 31 of 47 run folders hold a carousel (26 with Facebook's copy); 14 carousel publications, all simulated (dry
  runs); 0 carousel bookings, 0 carousel metric rows.
- Drawing on real dev runs (equivalence.py): for every dev run with a recorded photo match whose photos are on disk,
  both copies drawn the old way (the renderer on the recorded photos) and the QF-81 way (plan, then draw into
  `carousel_draw/`) in separate scratch folders, the vision look replaced by "not checked" and the match by the run's
  recorded match (no live call): 7 of 7 runs, 98 of 98 slides (both copies) byte-identical; the full slot audit
  (slide audit plus both copies' text contrast) holds 0 of 7, 0 of them on contrast.
- `slot_draw_gol_gumbaz_whispering_gallery.jpg`: one of those real dev runs as the QF-81 drawing (top row Instagram,
  bottom row Facebook's grid copy; each slide at one fifth size). It is byte-identical to the old drawing, so there is
  no separate before image.

## Effects, including the unwelcome ones

- Before Approve the operator sees the carousel only by tapping 🖼 Preview carousel (it costs one photo match and one
  vision look per slide, the same as the slot's drawing, which then reuses it). Without a tap, it is drawn and checked
  at its slot, and a hold is one alert.
- The carousel's findings (slide audit and contrast) now hold only the carousel; before QF-81 they could block the
  whole run's release.
- A held carousel uses that day's slot (the claim comes first, as before); the next slot goes to the next eligible
  episode unless the operator taps 🔁 Redraw, which puts it first in line. It is never redrawn by itself.
- A failed photo match at the slot or preview holds; before, it rotated the photos silently (the run-time path still
  does for every other caller: only the carousel drawing asks for the strict match).
- The drawing runs under the daemon's posting pass (as the old eager drawing ran under the run): a Reel falling due
  during a slot's drawing is posted when the drawing ends, within MISSED_SLOT_GRACE. A drawing the preview already
  made is reused, so the slot pass is short then.
- Photos come from the shared prepared-photo cache; nothing prunes it today. If one is removed or changed before the
  drawing, the carousel is held, never drawn from another photo.
- A dev dry run does not simulate the carousel request (there are no slides until a preview or a slot); the receipt
  says so.
- The Preview button is on the classic review card; the rebuilt keyboards after an undo (as with 🎨 Covers today) and
  the unreleased TELEGRAM_CARDS_V2 mock card do not carry it.

## Tests

- tests/test_qf81_lazy_carousels.py (new; fails on the parent): making a run draws nothing and records the plan;
  booking undrawn; only each slot's winner drawn (both copies, strict match), twice-per-slot passes idempotent; a
  changed photo holds with one alert and the Redraw button; the audit's blocking rule per severity and setting, a held
  drawing never installed, info look findings kept for the operator; an audit that cannot run holds; review 1's
  contrast probe (2.1:1 in both copies: 6 major findings, both copies named, held) and contrast at the target posts; a
  failed photo match holds and the renderer's strict match raises while the old call still rotates; with the real
  `plan_scenes` (only the provider call replaced), no planner or a planner failing every attempt (2 tries, one 15 s
  wait) holds instead of posting filename order, caption order holds too, and a real plan still posts (one planner
  call for both copies); a file error at the slot holds with its reason and installs nothing; a preview drawn
  once and reused by paging and by the slot; a changed input after the preview means a new drawing; Redraw after a
  hold posts in the next slot ahead of a newer episode, and a second tap is refused; glyph fallbacks reach the run's
  ledger (both surfaces) and the hold alert; the preview panel's sheet, pager, slide and both alt texts; a pre-QF-81
  run posts its slides; the card's caption and note; the dry-run receipt; Instagram album insights; the digest from
  12; Facebook rows without reach (production's row shape) say so and give the labelled views ratio.
- Branch-only change (not on origin/main): tests/test_carousel_waits_for_graphics_2026_11_02.py expected both copies
  drawn after the graphics while the run is made; it now expects none drawn then (and the plan written). Nothing is
  looser: the posters exist long before any drawing.

## Open after landing

- The 12-real-post observation (digest medians, and the Facebook reach ratio once Facebook's Reel rows carry reach)
  for the operator's Facebook-gallery decision: it fills in as real carousels post; nothing offline stands in for it.
- QF-68: cost attribution of the drawing (above).
