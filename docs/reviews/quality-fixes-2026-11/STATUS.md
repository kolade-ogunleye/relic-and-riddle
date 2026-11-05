# Quality fixes ledger (from docs/reviews/project-quality-review-2026-11-05.md)

Branch `claude/intelligent-carson-ybsccd` (PR #3). One row per finding, updated in the same commit as its fix. Every
acceptance metric is reproducible with `python docs/reviews/quality-fixes-2026-11/<ID>/measure.py <repo root>`.
Measured with Pillow raqm layout (FriBiDi on PATH), FONT_SIZE=80.

Parked before step 1 (2026-11-05):
- Named stash `qf-park-2026-11-05: covers/social/endcard step-2 work, not reviewer-passed`
  (`2e431f26ad0e0b017719ba7829e43430de4dd9a1`): the covers, social and end-card freeze-step-2 work and the main.py
  LayoutError catch. None of it passed review; it is not part of any QF row.
- Untracked review renders (31,298 files) moved out of the repo to a local archive folder outside the repository
  (with a manifest); the committed evidence stays.
- In flight, not committed: an independent read-only review of the map fix2 code (581a866, WIP) on 30+ new places;
  its findings feed QF-20.
- Phase 1 stopped for the addendum (QF-36..QF-91, Phase 0 first). Built commits kept on local branches
  `qf-park/QF-01` (built, review interrupted), `qf-park/QF-05` and `qf-park/QF-06` (reviewer-passed),
  `qf-park/QF-09` (built, not reviewed); QF-07 partial (measure.py only) in its worktree. They land after Phase 0,
  with the identifier rule of QF-49 applied to their evidence.

Rows are listed in the order they are done (operator, 2026-11-05: Phase 0 first). QF-92 is the
operator-approved DirectML pin, done after Phase 1.

### Phase 0 - stop-ship (before any further output-quality work)

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-49 | Scrub operator and infrastructure identifiers (STOP-SHIP) | done | 22e89bc | row grep: 160 files outside the allowlist -> 0 (163 -> 3 matched, holding 4 allowlisted third-party attribution lines); wider denylist: 1027 lines outside the allowlist -> 0 | docs/reviews/quality-fixes-2026-11/QF-49/ | tests/test_qf49_identifier_scrub.py |
| QF-46 | Cap and ledger illustration spend (STOP-SHIP) | done | this commit | MEDIA_MONTHLY_BUDGET_USD=0: 9 image requests per run (2 beats x 4 candidates + 1 linework) -> 0; one fake 200: 0 media_usage rows -> 1 (ai-images, $0.14); dashboard "AI images this month" line: no -> yes | docs/reviews/quality-fixes-2026-11/QF-46/ | tests/test_qf46_illustration_spend.py |
| QF-47 | Docs say dry run, code posts live (STOP-SHIP) | todo | | no daemon --dry-run in the four docs; masthead shows the mode | QF-47/ | |
| QF-48 | maintenance.apply() raises in production (STOP-SHIP) | todo | | no upd:apply button in production; apply raises before git | QF-48/ | |
| QF-40 | Tier-gated corroboration, signed evidence provenance (STOP-SHIP) | todo | | Wikipedia + cited-host figure -> disputed and hedged | QF-40/ | |
| QF-41 | Gemini roles, fenced evidence, injection corpus (STOP-SHIP) | todo | | system_instruction set; no tag echoes; >= 10 payloads pass | QF-41/ | |
| QF-42 | Sanitised capped credit lines (STOP-SHIP) | todo | | no URL/hashtag/email in credits; every credit on the card | QF-42/ | |
| QF-43 | Publishable-field allowlist on the card (STOP-SHIP) | todo | | card shows exact final strings, slot and expiry | QF-43/ | |
| QF-44 | SSRF hardening (STOP-SHIP) | todo | | redirect to 127.0.0.1 refused; off-host image download raises | QF-44/ | |
| QF-50 | Local face verification by default, privacy data map (STOP-SHIP) | todo | | no HTTP from face detection with the flag off; docs/privacy.md | QF-50/ | |
| QF-51 | Config drift: YouTube vars, RELIC_* vars, inventory regex | todo | | inventory test fails before, passes after | QF-51/ | |
| QF-45 | Learned-rule grammar and provenance | todo | | a drafted rule with # or http is discarded | QF-45/ | |

### Phase 1 - output defects on every upload

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-01 | Colour matrix (BT.709 end to end) | todo | | pure #FF0000: Y/U/V in master; bt709 tags on every intermediate | QF-01/ | |
| QF-02 | 16-bit grade | todo | | distinct levels of a 0-64 luma ramp after the grade chain; black lift | QF-02/ | |
| QF-03 | Grain removed from the delivery master | todo | | master bitrate at the same CRF; two renders byte-identical | QF-03/ | |
| QF-04 | Transitions: hard cuts, dissolve only on act changes | todo | | fadeblack / zoomin in Reel graphs; seam tests | QF-04/ | |
| QF-05 | Whoosh placement | todo | | whooshes per Reel on the fixture runs; level | QF-05/ | |
| QF-06 | SFX body and headroom | todo | | f0 of hits; limiter gain reduction at SFX cues; narration LUFS per cue | QF-06/ | |
| QF-07 | Ducking | todo | | bed recovery in 0.1-0.5 s word gaps (median dB) | QF-07/ | |
| QF-08 | Voice PCM + seed | todo | | provider output formats, WAV voiceover, seed, punctuation-only words | QF-08/ | |
| QF-09 | Pillow 13 + lockfile | todo | | pillow version, uv.lock with hashes, pip-audit in CI, download byte cap | QF-09/ | |
| QF-92 | Pin ONNX DirectML to the dedicated GPU (operator-approved extra) | todo | | chosen adapter logged; integrated adapter refused under auto | QF-92/ | |

### Phase 1b - photos

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-52 | 9:16 crop floor and aspect-aware ranking | todo | | new pools: 0 under 1080 crop, <= 10% needing upscale | QF-52/ | |
| QF-53 | pHash dedup, author share cap, shot-class quota | todo | | no pair within 10 bits; author share <= 35%; >= 3 shot classes | QF-53/ | |
| QF-54 | Structured per-topic queries, SDC depicts, aliases | todo | | >= 2x candidates on 5 topics, no rise in off-topic | QF-54/ | |
| QF-55 | Category fallback through topic_anchor | todo | | 0 off-topic among fallback files over 10 fetches | QF-55/ | |
| QF-56 | Deterministic photo pre-checks, quality and depiction | todo | | every record has quality + depiction; originals outrank reconstructions | QF-56/ | |
| QF-57 | PD-US handling, Openverse bypass removed, NASA credit rule | todo | | 0 PD records without a worldwide basis | QF-57/ | |
| QF-58 | Beat-driven readiness, shot-class fetching, re-fetch, saliency crops | todo | | no repeat or hold > 6 s; >= 9 distinct photos per Reel | QF-58/ | |
| QF-59 | Embedding line-to-photo ranking, deterministic place check | todo | | first-pass fit >= 85%; 0 different-site findings | QF-59/ | |

### Phase 2 - the gate learns to see and hear

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-10 | Picture checks in the release gate | todo | | each check fails a deliberately broken master | QF-10/ | |
| QF-11 | Audio QA (loudnorm two-pass, ebur128, phone check) | todo | | ffmpeg passes per master; QA fields | QF-11/ | |
| QF-12 | Host-independent delivery encode + manifest | todo | | ffprobe of both masters; encoder args in composition.json | QF-12/ | |
| QF-13 | Lossless intermediates | todo | | no 4:2:0 JPEG written before the render | QF-13/ | |

### Phase 2b - illustrations and motion

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-60 | Structured reconstruction verdict replaces the regex | todo | | 25 ordinary lines -> <= 1 flagged | QF-60/ | |
| QF-61 | Count/number fidelity for illustrations | todo | | count mismatch rejected; element not in facts rejected | QF-61/ | |
| QF-62 | Era/region/material checklist from the subject's own dates | todo | | derive_era on discovered 1901 -> not industrial | QF-62/ | |
| QF-63 | Objective similarity floor, different-vendor judge | todo | | different object with a confident yes -> rejected by the floor | QF-63/ | |
| QF-64 | OCR gate fixed, pytesseract declared, doctor() reports it | todo | | hatched engraving passes; inverted ROMA rejected | QF-64/ | |
| QF-65 | Full illustration provenance, forced AI flags, no illustration in Stories/carousel | todo | | lineage has prompt and raw_sha256; envelope AI flag set | QF-65/ | |
| QF-66 | Motion gates: anchor ssim, homography, text, flicker, resolution | todo | | wrong anchor, burned-in word and 720p raw rejected | QF-66/ | |
| QF-67 | Gate calibration harness and real-image fixture test | todo | | per-threshold tables; fixture test passes without mocks | QF-67/ | |

### Phase 3 - the opening three seconds

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-14 | Hook enforcement | todo | | hook faults blocking; first burst <= 12 words; first-8-words check | QF-14/ | |
| QF-15 | Beat restructure | todo | | payoff_sentence shares a noun with the hook; beat 4 removed | QF-15/ | |
| QF-16 | Hedge rule | todo | | hedges only on numbers/dates, never in hook/close | QF-16/ | |
| QF-17 | Narration on a stronger model + evals harness | todo | | evals baseline vs after | QF-17/ | |
| QF-18 | Pronunciation map + whisper diff | todo | | transcript diff on names/numbers | QF-18/ | |
| QF-19 | Evidence independence | todo | | non-Wikipedia host per number fact; retrieved_at + revid | QF-19/ | |

### Phase 3b - operator and operations

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-74 | Run deadline, missed-slot alert, stage budgets | todo | | worker over timeout -> timed_out, requeued, one alert | QF-74/ | |
| QF-77 | Provider runbooks and live probes | todo | | health --live fails on a 404 voice or non-200 Upload-Post | QF-77/ | |
| QF-68 | Cost per Reel | todo | | usage rows carry run_id; the card shows the run's sum | QF-68/ | |
| QF-69 | V2 card: no mock values, no fingerprint-bypassing undo | todo | | none of the literals in the blocks; undo of a posted Reel refused | QF-69/ | |
| QF-70 | Stale-review state, 12-h floor, open review counts as idle | todo | | topic stays queued after the window; release gate idle | QF-70/ | |
| QF-71 | Silent notifications except needs and the review card | todo | | info notify sends disable_notification | QF-71/ | |
| QF-72 | Hourly SQLite backup, pruning, staleness alerts | todo | | heartbeat with a 27-h snapshot says backup stale | QF-72/ | |
| QF-73 | Topic bank: hooks, sensitivity, proposals, regions, lint | todo | | hook_problems empty; no region > 40%; lint fails without sources | QF-73/ | |
| QF-78 | Voice default from voices.json, listening record | todo | | health --live fails when the id is not in voices.json | QF-78/ | |
| QF-79 | Classify and archive scripts; eval harness base | todo | | scripts/README.md lists every script with its class | QF-79/ | |

### Phase 4 - geometry and type on real libraries

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-20 | Shapely + pyproj under the maps | todo | | 200-site sweep; 30-new-place review >= 9 on every design | QF-20/ | |
| QF-21 | fontTools + uharfbuzz + OpenType features | todo | | kerning-pair, per-font feature and caption width parity tests | QF-21/ | |
| QF-22 | One shared kenburns() | todo | | edge-position trace monotone, no integer steps | QF-22/ | |
| QF-23 | Checked copy never truncated | todo | | fit_text raises instead of an ellipsis | QF-23/ | |
| QF-24 | 9:16 figures use the full frame | todo | | optical weight height; text out of platform bands | QF-24/ | |
| QF-25 | design_cards wired into the composer | todo | | cards in the composed master; caption_clear_until_s honoured | QF-25/ | |
| QF-26 | Aliased geometry: one AA path | todo | | edge aliasing measure on land, borders, scale | QF-26/ | |

### Phase 4b - surfaces and assets

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-80 | Stories: cut the poll frame, pause frames 1-2 | todo | | no auto-posted frame with poll_sticker_room | QF-80/ | |
| QF-81 | Carousels: lazy render, insights, FB gallery off | todo | | carousel/ only for booked runs | QF-81/ | |
| QF-82 | Covers: final + grid only, bandit frozen | todo | | no cover_a/b/c in a run folder | QF-82/ | |
| QF-83 | Shorts on, dead thumbnail call dropped, FOLLOW overlay | todo | | dry run posts a Short; no thumbnail request | QF-83/ | |
| QF-84 | Music: mood from the sound plan, published uses, replacement set | todo | | no mood > 50%; every track >= 1 published use | QF-84/ | |
| QF-85 | SFX regenerated with headroom, <= 12 effects | todo | | every file Flat_factor 0, TP <= -1 dBTP | QF-85/ | |
| QF-86 | LUT removed, photo-only curves, no broadcaster names | todo | | graphic frames within dE 2 of brand tokens; >= 250 levels | QF-86/ | |
| QF-87 | Fonts: Bebas removed, one Montserrat, OpenType features | todo | | fonts/ lists only FONT_FILES; Konigsberg smcp bounds differ | QF-87/ | |
| QF-36 | Commons and NASA video in the pool fetch | todo | | a Commons-video topic yields >= 1 approved clip, rights bound to bytes | QF-36/ | |
| QF-37 | archive_film.find in the nightly pool job | todo | | nightly log shows the search per next topic | QF-37/ | |
| QF-38 | Vision-check film candidates | todo | | an off-topic film is rejected | QF-38/ | |
| QF-39 | Footage docs and originality weighting | todo | | docs match code; operator footage outweighs archive | QF-39/ | |

### Phase 5 - engineering floor

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-27 | pyproject + ruff + pyright + pre-commit + CI matrix | todo | | new lint findings blocked; Windows + py3.13 CI | QF-27/ | |
| QF-28 | pydantic-settings config | todo | | env vars typed and validated | QF-28/ | |
| QF-29 | Fold dated tests; golden images | todo | | dated files; getsource asserts; golden diffs per graphic | QF-29/ | |
| QF-30 | import-linter, structlog, signals, tenacity, LFS | todo | | contracts; run_id logs; ffmpeg children terminated | QF-30/ | |

### Phase 5b - engineering floor (sweep)

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-75 | CI runs the maps and Windows; skip ceiling | todo | | skipped <= 25; Windows job green | QF-75/ | |
| QF-76 | Deployer installs hashed, no network fallback | todo | | Pester asserts --require-hashes; CI fails if lock != requirements | QF-76/ | |

### Phase 6 - monetization and niches

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-31 | Originality score | todo | | originality fields on the review card; needs_review floor | QF-31/ | |
| QF-32 | YouTube revenue data, policy watch, metadata | todo | | monetary scope; RPM digest; youtube metadata block | QF-32/ | |
| QF-33 | Content-ID-safe music | todo | | content_id_safe flag enforced; CC BY credit lines | QF-33/ | |
| QF-34 | Compliance nits | todo | | AI flag on photos; token expiry watch; masker | QF-34/ | |
| QF-35 | BrandProfile / Niche config | todo | | a second brand renders end to end in dry-run | QF-35/ | |

### Phase 6b - accessibility, legal, docs

| ID | Finding | Status | Commit | Acceptance metric before -> after | Evidence path | Regression test |
|---|---|---|---|---|---|---|
| QF-88 | Photosensitivity/black/freeze gate, CVD gate, CPS enforced | todo | | 10-Hz strobe blocked; 20-cps burst fails | QF-88/ | |
| QF-89 | Caption-track status, alt text from specs, synthetic note | todo | | caption status on every facebook/youtube row | QF-89/ | |
| QF-90 | THIRD_PARTY_NOTICES.md, tools manifest, YuNet absence logged | todo | | notices match pip-licenses; missing model warns | QF-90/ | |
| QF-91 | Docs corrections, generated test count, PROJECT.md | todo | | README count equals collect-only; PROJECT.md exists | QF-91/ | |
