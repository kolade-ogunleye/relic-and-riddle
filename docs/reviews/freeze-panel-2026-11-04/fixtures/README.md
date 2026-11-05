# Real run inputs for the freeze-panel renders (2026-11-04)

Operator rule: evidence renders use existing run facts and media assets only. That means no invented copy, no fake
fact sheets, no synthetic grounds, and no photo paired with another topic's text. Something new is allowed only
where no real input exists, and it carries `label: "new: <why>"`.

- `fixtures.json` is the catalogue: per design, the real cases (run, topic, exact files and fields to feed, why
  each was picked, and measurements). Paths are relative to the repository root.
- `fixtures.py` is the loader. `load(design)` returns cases with absolute `Path`s, and `section(design)` returns
  the whole section with its survey and gaps. It also has `words(case)`, `payload(case)`, `sheet(topic)`,
  `frame(mp4, t | last=True)` and `background_at(case, t)`. It imports nothing from `src`, so it works from any
  `--code-root` tree. To use it, put this folder on `sys.path`. To point it at another checkout, set
  `RR_FIXTURE_REPO`.
- `build_fixtures.py` rebuilds the catalogue. It is read-only over `output/`, `data/` and `assets/`, and it never
  imports `src` or reads `.env`.

The measurements are taken at 512 px on the long side:
- `mean_luma` and `p90_luma`: Rec. 601, 0-255.
- `mean_saturation`: HSV S, 0-255.
- `edge_energy`: mean PIL FIND_EDGES on luma.
- `edge_density`: share of pixels with an edge response above 40.

The ground tags are: dark < 70 luma, bright/near-white > 170, busy edge energy > 14, colourful saturation > 110.

## What each design gets

| Design | Cases | Input fed | Covers |
|---|---|---|---|
| covers (Reel cover, grid tile, YouTube thumbnail) | 5 | The run's own hero plus `<run>/verified_script.json` (`thumbnail`, `youtube_title`), through `VisualEngine.generate_thumbnails` / `generate_youtube_thumbnail` | darkest hero (Wieliczka, luma 69); brightest hero (Greek fire, luma 176), which is also the longest copy at 73 chars and the longest line at 20; busiest (Tunguska, edge 43); most colourful (Amber Room, sat 178), which also has the longest tagline; shortest copy (Richat 1793272675, 51 chars) |
| captions 9:16 / 16:9 | 5 | `<run>/words.json` plus `emphasis_words`. Backgrounds come from the run's own scene timeline (caption-free parallax/graphic clips or stills, via `background_at`) | the only 16:9 narration (Richat deep dive); the most multi-word names (Gol Gumbaz: "Mohammad Adil Shah's"); the longest name ("Podkamennaya Tunguska River"); the longest word ("eighteenth-century", 18) and a non-ASCII name ("Königsberg Castle"); the densest narration (Voynich) |
| social (IG carousel, FB gallery, Stories) | 7 | Gol Gumbaz, Nazca and Richat kept per-slide recipes (photo, photo_next, card, archetype, index, poll), which are exact inputs for `redraw_carousel_slide` / `redraw_story_frame`. The other cases use the payload's `carousel_slides` / `story_slides` | per-recipe runs of all three topics; the longest carousel body (Aswan, 190 chars) and the longest Story body (110); the shortest carousel body (Wieliczka, 58); the longest Story title line (Amber Room); the darkest and brightest recipe photos |
| endcard (loop bridge 9:16, end screen 16:9) | 9 | first = the master's frame 0; last = the last frame of the last scene clip (caption-free); share = `share_line` | the darkest (Wieliczka, luma 43) and longest share line (89); the brightest (Aswan, 172) and hardest seam; the busiest (Nazca); the most colourful (Odessa); the shortest share line (Nazca, 72); the 16:9 run's own last scene; three 16:9 cases labelled new (see gaps) |
| thennow | 2 | `then_0.jpg` and the matched `now_0_1.jpg` from the run's `graphics/`, with the years the run drew | Pantheon 1934 to 2018 (Roman concrete); Colossi of Memnon 1876 to 2011, where the old photo is the brightest pair photo (luma 168, near-white sky) |
| cards (first frame, chapter, myth-vs-fact) | 0 | none can be fed honestly (see gaps). The section lists the real sheets, hero photos, title and act-title candidates | none |

### How the inputs were established

- **Hero photos.** Six runs recorded the hero in `still_layout.json`
  (`thumbnail_cover.jpg.recipe.hero`), so their hero is exact. The older runs kept no cover recipe. For those,
  the hero is the run's still that best matches its delivered `thumbnail_cover.jpg`: a luma correlation of at
  least 0.6, and for the 5 cases it was 0.66-0.995, checked by eye on the three lowest. Two runs (Damascus 1792732472,
  Voynich) match no still and are left out (`heroes_not_verified`). The map relief `tools/geo/relief/SR_HR.tif` is
  a graphic source and is never taken as a hero.
- **Delivered covers differ from the payload.** The covers' drawn text can differ from the payload copy because
  the renderer reshapes it (figure checks, withholding). The input is the payload, not the delivered text.
- **Caption timelines.** Each scene in the timeline lists its clip, source photo, length and estimated start. The
  estimate is the clip lengths less 0.40 s of crossfade per cut. Gol Gumbaz, Tunguska and Voynich have complete
  timelines. For Amber Room and the Richat deep dive, some clips are no longer on disk, so `background_at` falls
  back to that scene's source photo and spreads the unknown starts evenly (`timeline_complete: false`).
- **Then-and-now years.** The years were read off each run's own `graphic_thennow.mp4`: the frame at 0.5 s shows
  the old year and the poster shows the recent one. The recent photo is the `now_0_J.jpg` whose crop matches the
  poster at 0.997-0.998.

## Gaps (no real input exists)

- **No myth field exists anywhere.** No fact sheet in `data/fact_sheets/` or `data/script_library/` has a
  `cards.myths` entry, and `refuting_fact_id` appears in no JSON file in the repository. So there is no longest
  real myth or correction to list. A real myth-vs-fact card cannot be rendered until `fact_sheet.build` proposes
  myth fields and `card_fields.check_cards` checks and signs them with a checker key. Three facts do state a
  refuted belief in their own words. They could seed such a field, but they are not card fields:
  `damascus_steel_nanotubes` damascus-13, `kailasa_temple_monolith` kailasa-legend-kokasa and
  `richat_structure_sahara_eye` fact-9.
- **No usable card field of any kind.** The one statement field (`hal_saflieni_hypogeum` S1) is `needs_check`,
  with no runs and no signature, so `verify_field` refuses it. There are no quote fields. Every run's
  `fact_review` lacks the check record (it holds only fingerprints and sheet hashes), so `Gated("title" |
  "youtube_title")` is refused on every run. The hook frame and chapter card therefore have no renderable real
  case either. Their real text candidates are listed in the cards section:
  - Hook claims within 6 words and 30 chars: "The Lost Amber Room Heist", "Lines Drawn Without Wings" and "Flattened
    830 Square Miles".
  - Chapter titles within 32 chars: the Richat deep dive's act titles "Documented Physical Architecture" (32) and
    "Origin Debates and Rock Dating" (30).
  - Any card rendered before those fields are checked is new and must be labelled so.
- **No near-white hero.** The brightest verified hero is luma 176 (Greek fire manuscript). No run's own hero is
  near-white. A near-white cover ground would be new.
- **Only one 16:9 run** (`run_richat_structure_sahara_eye_1792740961_long`). It has no `share_line`, so its end
  screen carries the code's own `SHARE_FALLBACK`, as a real run would. The three other 16:9 end-screen cases are
  labelled `new: 16:9 end screen of a Reel topic`: each uses its own run's landscape last-scene photo and share
  line.
- **Then-and-now.** Only two pairs were ever accepted, and both predate `then_now.register`, so no
  `registered/` folder exists and no credit record was kept. Every other `then_K.jpg` / `now_K_J.jpg` was rejected
  (run.log: "no honest match"). The brightest rejected old photos (Odessa then_2, luma 207; Gol Gumbaz then_2,
  204) are real photos of their topics but not verified pairs. Pairing one would be new and must be labelled.
- **Social, runs without recipes.** The Aswan, Wieliczka and Amber Room social cases are kept for their real
  extreme copy lengths, but those runs recorded no per-slide recipe. They carry
  `label: "new: photo-to-slide order reconstructed"`, with photos taken from their provenance scene stills.
- **Captions, "RASHTRAKUTA DYNASTY".** The split the handover reports does not occur in any run's `words.json`.
  No real narration says it, so it cannot be a real case. The closest real tests are the multi-word names above.

## Marked new

The three 16:9 end-screen cases and the three recipe-less social cases above. Nothing else in the catalogue is new.
