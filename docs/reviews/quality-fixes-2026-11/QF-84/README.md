# QF-84: music mood from the sound plan, published uses, replacement set

Finding (review 2026-11-05, section 9.2):
- `choose_mood` returned "mysterious" whenever the classifier was missing or failed, so 360 picks fell on 5 tracks.
- The melancholic set never played.
- `uses` went up on every pick, dry runs included, and those counts sat in a tracked file (data/music/library.json).
- The set mixed four genres from many uploaders.

Acceptance (STATUS.md): after 12 published Reels, no mood above 50% and every track with at least 1 published use.

## What changed

- **Mood from the sound plan** (src/sound_plan.py).
  - The planning call that already marks the narration's moments now also returns 1 to 4 mood sections, each giving
    the word it starts on and a mood.
  - The model is told a reflective pause is not by itself melancholic.
  - Code decides the result:
    - sections on words that do not exist are dropped;
    - each section is timed from the word timings;
    - `dominant_mood` is the mood heard longest, with ties going to the mood heard first.
  - `plan_and_mood` returns the plan and a mood record. When there is no mood, the record gives the reason: no timed
    words, no model, the call's error, or no sections.
  - The call answers in `PlannedSound`, whose sections are required (1 to 4). A model that would omit them is asked
    by the schema itself, in the same single call, rather than sending the Reel to the drone. How often a model omits
    optional sections cannot be measured on the recorded plans: all 34 development plans predate the field.
- **The plan runs before the music**, on both the Reel and the documentary paths (main.py).
  - `music.music_for` takes the mood record.
  - With no mood, the music is held: no library track and no generated score. The drone plays.
  - The review card says so: "🎵 Music held: no mood (<reason>); synthesized drone used".
  - With a mood, the card line reads "🎵 Music: free library track (melancholic, the sound plan's mood for 72% of the
    narration)".
  - `choose_mood`, still used by scripts and by callers without a plan, returns None on failure. It never returns
    "mysterious" by default.
- **Never another mood.**
  - The pipeline picks only from the decided mood (`strict`).
  - An empty set is held with "no approved <mood> track". A paid score fills in only where one is configured.
  - Documentary movements keep their designed arc across moods, each movement in its own mood. A mood with no track
    is skipped, never filled with another mood's track.
  - A documentary keeps its selection (music_selection.json, with the bed), so a resumed one keeps its music line,
    including a held line.
- **Uses are publications** (src/db.py).
  - A new `music_selections` table records which library tracks, with their SHA-256, each run's bed was made from.
    This includes a bed carried over to a regeneration.
  - A re-made bed replaces its run's rows in one transaction.
  - `music_published_uses()` counts, per track, the runs with a confirmed video receipt:
    - counted: status published on upload-post, a direct Graph channel, youtube-shorts, reel-retry or
      manual-confirmation;
    - not counted: the dry run, the coordinator placeholder and companion channels (`<channel>:<kind>`);
    - a run counts once, however many platforms or retries it has;
    - a published documentary (its upload also goes through upload-post) counts each of its movements once: the
      track was heard in a published video.
  - The count is derived when read, so there is no counter to drift, nothing to double-count and nothing to reconcile.
    Dry runs, simulated, failed, unknown, rejected and regenerated runs count zero.
  - Rotation is least-published first. Equal counts go in a stable order per topic: a hash of the topic and the file.
  - Nothing writes `uses` any more. It stays as the old pick history. It was not renamed, not used to seed anything,
    and it is excluded from the bed fingerprint as before.
  - **Backfill.** On first start, `backfill_music_selections` runs once per database. It reads the
    music_selection.json of every earlier run that has no rows and records its library tracks. Production's 6
    published Reels therefore count, and so does a run waiting for review at the release. Nothing is taken from the
    old `uses`.
  - The bed fingerprint now includes src/sound_plan.py and src/music.py: the plan and selection rules. Every bed in
    flight at deploy is re-made once, with one more sound-plan call per resumed run, and again after any later edit
    to those files. This is intended.
- **Replacement set from the tracks the project holds** (data/music/rotation.json, music_set.py).
  - The 21 approved tracks were measured from their audio with FFmpeg (real_data.py).
  - A generic rule keeps three per mood:
    - under 75% of the first minute's power below 120 Hz, so a phone speaker plays it;
    - loudness range at most 15 LU, and at most 4 s near silence in the first minute: a steady bed under a voice;
    - then the least sub-bass.
  - The other 9 approved tracks are listed in **data/music/rotation.json** with their reason. A track the file does not
    list rotates, so a host's own approvals keep rotating.
  - **Why a new file, not library.json.** Every host rewrites its own library.json: approvals, and until this row
    the pick counts. Production's copy has had a pick written for each of its runs (34 with library music in the
    sample), so it differs from the repository's.
    - The release deployer (Update-ContentTree) refuses a release that changes a file production changed
      differently, beyond line endings, and rolls back.
    - This row therefore leaves library.json byte-identical to the base.
    - rotation.json is new and no host writes it. A host that lacks it receives it in the release checkout. The
      deployer's check covers only files that `git status` lists as locally changed, and no host has an untracked
      file by that name.
    - Any FUTURE release that changes library.json will stop at production's modified copy. Change rotation.json
      instead, or reconcile production's copy through the elevated procedure first.
  - The 3 tracks awaiting review are untouched.
  - The Telegram music screen shows "N approved (12 in rotation)", and the library list shows each track's published
    uses or "retired". /ask's music count adds "in rotation" and gives the moods in rotation.
  - No track was bought, downloaded or requested.
  - The one/two-composer, single-family set the review asks for needs the operator's quote under decisions\QF-84.md
    (c). See decisions\QF-84-question.md, Q2.

## Measurements (`python measure.py <repo root>`)

| | base 2debdf8b | this commit |
|---|---|---|
| choose_mood, no model / call fails | "mysterious" / "mysterious" | None / None |
| 12 Reels whose mood could not be decided | 12 mysterious picks (share 1.0) | 0 picks, 12 held with the reason on the card |
| `uses` written by those 12 dry-run picks | 12 | 0 |
| Simulated 12 published Reels, 3 per mood: rotating tracks with at least 1 counted use | 12 of 21 (picks, no published accounting) | 12 of 12 (published uses), max mood share 0.25 |
| Approved tracks in rotation by mood | 5 / 4 / 6 / 6 (mysterious / epic / melancholic / tense) | 3 / 3 / 3 / 3 |
| rotation.json matches the measured rule | false (no file) | true (9 retired with reasons) |

The twelve-Reel row is a simulation: the mood sequence is given, three per mood, and the publication receipts are
written by the script. It shows the mechanism: least-published rotation over a 3-track set uses every track once.
It cannot show the acceptance. That needs 12 real published Reels with moods chosen by the plan, and remains an
operational hold. The decision (decisions\QF-84.md) forbids forcing moods or publishing extra episodes to meet it.

## Real data (real_data.py, real_data.json; production counts only)

- Development: 33 runs chose library music.
  - Moods: mysterious 21, epic 8, tense 4, melancholic 0. The largest share is 0.636.
  - 12 distinct tracks were chosen.
  - All 34 sound plans are non-null.
  - The classifier's 18 logged calls all returned ok. Even then, 11 of the 18 runs with a call chose mysterious. The
    bias was not only the failure default.
  - 0 Reels published (development is dry runs only).
  - Committed `uses`: mysterious 360 (5 tracks x 72), epic 17, tense 8, melancholic 0.
- Production sample (34 runs):
  - Moods: mysterious 23, epic 10, tense 1, melancholic 0. The largest share is 0.676.
  - 34 of 34 sound plans are non-null. The plan, and so the new mood, was available on every run.
  - Confirmed Reel publications: 6 runs, mysterious 4 and epic 2, largest share 0.667.
  - Approved tracks with a published use: 5 of 21.
  - The acceptance's two conditions are therefore unmet before this row: share 0.667 > 0.5, and 5 of 21 tracks used.
  - Backfill replayed on the copy's runs, receipts and run folders: see `prod.backfill` in real_data.json (runs
    filled, a second start fills none, published uses and tracks with a use).

## Not measured, and limits

- **The new mood distribution on real runs.** It needs the planning model, and builds make no live calls. The first
  12 real publications decide it.
- **Listening.** The set rule removes measured misfits only. 120 Hz is a lenient phone cut-off: QF-11's phone check
  uses 300 Hz to 8 kHz. The rule was computed on the development library; production's approved set was not visible
  in the sample. Two kept tense tracks come from one sound pack whose
  names suggest voice-like textures. They need an operator listen under narration (QF-84-question Q2).
- **No render.** The only visible change is one text line on the review card and the Telegram music screens, so this
  folder has no JPEG. The card lines above are the exact strings that tests/test_qf84_music_mood_uses.py checks.
- **QF-33** (Content-ID-safe music, CC BY credit lines) is not taken on. `audio_provenance` is unchanged.
- **The library on a host.** Selection no longer rewrites data/music/library.json, so production's copy stops
  drifting. It stays locally modified, though (see "Why a new file").
