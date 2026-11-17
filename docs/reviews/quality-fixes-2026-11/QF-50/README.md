# QF-50: Local face verification by default and a privacy data map

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2. Operator decision:
`FACE_VERIFY_CLOUD=false` by default (local YuNet + geometry); blurring of archival photos stays on;
`docs/privacy.md` data map; a prune rule for the `audience_*` rows. Operator decisions of 2026-11-05 on the
builder's two questions: (1) `tests/test_privacy.py::test_kailasa_stone_carving_not_blurred` now expects the two
living visitors in `kailasa_temple_monolith/03_commons.jpg` blurred and the carving not; (2) option C for
relief/art faces (below).

## Change

- `src/config.py`, `.env.example`: `FACE_VERIFY_CLOUD` (default false).
- `src/privacy.py`: with the flag off, `detect_human_faces` does not ask Gemini to propose faces and
  decides every candidate on the machine (`_local_verdict`). A YuNet box, trimmed by the existing face
  geometry, is blurred when:
  - YuNet's own score is at least 0.55 (`LOCAL_MIN_SCORE`);
  - the face is at least 0.3 % of the photo's shorter side (`LOCAL_MIN_FACE_SHARE`). A smaller face is at
    most 9 px across in any frame it can be shown in: 1920 px over the shorter side, at the deepest framing
    zoom (repeat 1.35 x push 1.16). It is not identifiable;
  - a box over a tenth of the shorter side scores at least 0.70 (`LOCAL_LARGE_*`);
  - it is not a printed letter on a light page (`_printed_letter`): the surroundings are light (mean grey
    150 or more), the box has the page's own hue (Cr and Cb standard deviation under 2 and 6), and it
    splits into ink and paper (Otsu between-class share 0.70 or more, grey standard deviation 20 or more).

  The skin-tone share and the round-1 "a monochrome photo keeps every face" rule are gone. A monochrome
  photo is judged like a colour one, so archival faces are still blurred. The Haar cascade now stands in only
  when YuNet cannot run (no model, or an error); then its boxes are kept, because nothing else checks the
  photo. With the flag on, the cloud path (billed Gemini key, Claude fallback) is unchanged from the start
  commit. The cache key names the verifier.
- Option C (`src/config.py`, `.env.example`: `FACE_VERIFY_CLOUD_AMBIGUOUS`, default false). With
  `FACE_VERIFY_CLOUD` off, local verification decides every face it can. A face it keeps in an art-like photo
  (`privacy.looks_like_art`: the source record names a relief, carving, statue, stained glass, gilding, a mask
  and the like) cannot be told from a carved or painted one. With the setting on, only that face crop (never
  the whole photo) is sent to Gemini on the billed `GEMINI_API_KEY` (paid tier, confirmed by the operator;
  never the free key, no Claude fallback), `_ambiguous_cloud_verdict`. Off, no key, no ledger, or a failed
  call: the face is blurred and nothing is sent, and a failed call's copy is not cached. Every request that
  went out is recorded in `llm_usage` (task `privacy:ambiguous-face`, tokens, USD at `free_llm.PAID_PRICES`);
  each photo's count and cost are returned in `status` and logged with the photo. The cache key names the
  verifier (`local`, `local+ambiguous:<model>` or the cloud model). `is_artwork` and `looks_like_art` share
  `_source_record`; the verification prompt is one constant (`VERIFY_PROMPT`).

  The word list names objects, not venues. On the development photos, adding temple, church, museum, shrine
  and the like would have sent 117 visitors' crops to Google to clear 32 carvings; the object list sends 33
  visitors' crops for 21 carvings.
- `src/db.py`: `prune_history` also prunes `audience_metrics` and `audience_media_ids` (730 days) and
  `audience_sources` (90 days), each by its own timestamp column. `src/audience.py`: `due_snapshots` skips
  a post older than the metrics' retention, so pruned rows are not fetched again.
- `docs/privacy.md`: four tables (purpose, retention, processors, deletion) over `audit_log`,
  `audience_*`, `cache/privacy` and `assets/**`, and how faces are handled with the flag off and on,
  including what the local check cannot do.

## Metric (`python measure.py <repo root>`)

Real `detect_human_faces` on a deterministic synthetic photo (colour and monochrome) with both cloud
models configured and every outbound request recorded, never sent. The YuNet model is not in the
repository, so a stand-in proposes one face box with frontal landmarks. Real `prune_history` runs on a
temporary database with 800-day-old audience rows.

| | before (712c87c) | after |
|---|---|---|
| `FACE_VERIFY_CLOUD` default | absent (always cloud) | false |
| requests off the machine per photo (colour / monochrome) | 3 / 3 (Gemini detect, Gemini verify, Claude) | 0 / 0 |
| face kept for the blur (colour / archival monochrome) | 1 / 1 | 1 / 1 |
| `docs/privacy.md` data-map tables, covering all four stores | 0, 0 | 4, 4 |
| `audience_*` tables with a prune rule; rows pruned at 800 days | 0; 0 | 3; 3 |
| `FACE_VERIFY_CLOUD_AMBIGUOUS` default | absent | false |
| setting off, art-like photo: requests; face blurred | (no setting) | 0; 1 |
| setting on: requests for a plain photo / an art-like photo | (no setting) | 0 / 1 |
| setting on: what is sent | (no setting) | the 164 x 140 px face crop, not the 400 x 400 photo |
| setting on, failed call (HTTP 400): face blurred | (no setting) | 1 |
| ledger rows / requests sent | (no setting) | 2 / 2 |
| `docs/privacy.md` names Google, the DPA and the operator's responsibility | false | true |
| acceptance_met | false | true |

### Development photos (dev evidence only)

`dev_faces.json` holds every candidate YuNet proposed on the 541 development photos (`assets/*/NN_*.jpg`),
or the cascade's where YuNet found none: 1,407 candidates on 354 photos. Each candidate was labelled by
viewing its crop, and its context where the crop was unclear: `face` is a photographed person, `not` is a
letter, carving, statue, painting, map or texture, and `unsure` (dark or unreadable) is left out. Each
photo's SHA-256 prefix is recorded. Where the same photos are present (under the repo root or
`RR_FIXTURE_REPO`), `measure.py` passes each one through the real `detect_human_faces` with both cloud
models unconfigured. YuNet is replayed from the recorded candidates. Photos whose source record marks
them as artwork are skipped, as `blur_human_faces` skips them: 325 photos are checked. Without the photos,
the table reports "not available".

The before column is the start commit's path with no cloud model: a skin-tone share of 0.40 on a padded
crop. Round 1 (ad8362b) added "monochrome keeps every face" to it, giving 167/319 faces and 663/950
non-faces.

| development photos | before (712c87c, no cloud model) | after |
|---|---|---|
| faces blurred | 167/319 | 305/319 |
| non-faces blurred | 591/950 | 405/950 |
| Dyatlov 02 / 07 / 08 typed letters blurred | 17/17, 17/17, 12/12 | 0/17, 7/17, 3/12 |
| Kailasa 03 visitors blurred | 0/2 | 2/2 |
| Kailasa 05 visitors blurred; relief boxes | 2/2; 1/3 | 2/2; 1/3 |
| Kailasa 16 relief boxes blurred | 1/1 | 1/1 |
| Kailasa 20 visitors blurred; relief boxes | 5/6; 4/7 | 5/6; 7/7 |
| art-like photos (source record) among those checked | (no rule) | 26 |
| crops the ambiguous setting would send: faces / non-faces | (no setting) | 33 / 21 |

With the setting off (the default) those 54 faces are blurred, as in the table above. With it on, Gemini
would be asked about each; a correct answer clears the 21 carvings and keeps the 33 visitors blurred.

What the measurements showed (exploration over the same candidates):

- The skin-tone share dropped 152 of 319 real faces. Most were visitors in shade or with darker skin:
  Kailasa 02 (8 of 8 dropped), 07 (10 of 10), Aswan and the Colossi crowds. It kept amber walls and gilding
  as "skin".
- After YuNet found nothing, the cascade's 92 boxes were all non-faces (windows, branches, letters, map
  lines).
- In the dev set, the monochrome keep-all rule blurred 75 candidates, none of them a face: engravings,
  scanned pages and a halftone print.
- YuNet's landmarks did not tell letters from faces. On typed letters the median eye distance was 0.44 of
  the box width, against 0.42 for faces. The nose sat between and below the eyes, and the mouth below the
  nose, as on faces. Real profile faces had the most "implausible" landmarks, so no landmark rule was
  added.
- A score floor alone does not clear letters: typed "o", "e" and "a" score 0.58 to 0.85.

The 14 faces the local check misses: six are under 0.3 % of the shorter side (8 to 12 px boxes on large photos). Six score under 0.55, five of them 9 to 23 px across, plus one 52 px face (Colossi 15,
score 0.53). Two are 9 x 11 px visitors on bright stone that pass the letter check (Göbekli 02).

What the local check cannot do: tell a carved, painted or stained-glass face from a living one when YuNet
is confident. Most of the 405 non-faces still blurred are such faces: manuscript miniatures (Greek fire),
Persian miniatures (Gorgan), stained glass (Flannan Isles), the Amber Room's gilding, and Kailasa reliefs.
A colour-distance rule ("the face is the colour of the stone around it") would have cleared about a quarter
of them. It was not used, because in a monochrome photo every face is the colour of its surroundings: it
would have dropped every archival face. These photos are protected by the artwork flag in their source
record (`privacy.is_artwork`). Where that flag is missing, they are blurred, which fails closed.

Files: `before.json`/`before.txt`, `after.json`/`after.txt`, `measurements.json` (last run),
`dev_faces.json` (labelled candidates).

Regression test: `tests/test_qf50_local_face_verify.py` (synthetic, deterministic inputs: a face in shade,
a monochrome studio portrait, a typed page, carved stone, cascade-only boxes; Gemini's HTTP call mocked for
option C). On 712c87c, 13 failed and 2 passed; on the fix, 15 passed. `tests/test_privacy_blur_2026_10_28.py` (branch-only) gains an autouse
fixture that turns `FACE_VERIFY_CLOUD` on, since its cases cover the cloud verifier; no expected value
changed.

Expected-value change on origin/main, approved by the operator on 2026-11-05:
`tests/test_privacy.py::test_kailasa_stone_carving_not_blurred` expected 0 faces in the dev photo
`assets/kailasa_temple_monolith/03_commons.jpg`; it now expects the photo's two living visitors (16 and 13 px
face boxes) blurred and nothing else, so the carving is not. It runs only where the dev photo is present
(not in CI or a worktree); checked by hand against the dev photo with YuNet: the two visitor boxes, nothing else.
