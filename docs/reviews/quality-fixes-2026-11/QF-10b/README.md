# QF-10b: the old-master note survives a failed measurement

QF-10 review 5, non-blocking 1 (recommended before deploy). The operator's ruling "Grandfather old masters" gives a
master gated before QF-10, whose colour tags are exactly the pre-QF-01 set, a note instead of a picture_colour block.
In 8a7b8973 the gate decided "gated before QF-10" only after a successful measurement, and recorded it only in that
measurement. When the first QF-10 check of such a master could not measure (a timeout or any error), it wrote
`picture_measurement: fail` without the marker. Every later check then saw a QF-10 report without the marker and
blocked on picture_colour for good, although the failure said "it is tried again at the next check". The same
happened after a failed re-measure once `METHOD_VERSION` changes (QF-88 will bump it).

## The fix (`src/release.py`)

- `_gated_before_qf10(composition, report)` decides eligibility before any measurement. It reads the same stored
  records as before:
  - a composition record bound to the master's sha256 and written before QF-10, so without `full_image_spans`;
  - and a previous release report written by the gate before QF-10, so without a picture measurement.

  A later check also accepts this gate's own `gated_before_qf10` mark for the same sha256, or a round-5 report's
  `grandfathered` measurement.
- That mark (the master's sha256) is written on the `picture_measurement` check whatever its outcome, failed
  included. The failed check itself still blocks. The next check that measures finds the master eligible and gives
  the note.
- `_grandfather_colour(picture, eligible)` gives the note only when the measured tags are exactly `PRE_QF01_TAGS`
  and the mark is for the measured bytes. The scope is exactly the ruling: new renders (key present), masters never
  gated before QF-10, masters first gated by QF-10, other tags and other bytes are never noted. Black, freeze, bars
  and range block every master.
- The report is now written atomically (`atomic_files.write_json`), so a check never reads half a report written by
  another, which would have lost the mark too. The JSON is unchanged (same keys, indent 2). On disk it is LF, with
  raw UTF-8 instead of `\uXXXX` escapes. Every reader opens it as UTF-8, and nothing is byte-sensitive.
- On Windows the atomic rename fails while another program holds the report open without delete sharing (an
  editor, a reader, a virus scanner). `release._save_report` retries for about 1.3 s (7 tries, 6 waits doubling from 20 ms: 1.26 s).
  It then raises `ReleaseReportLocked`, a ValueError, in plain words ("the release report ... is open in another
  program, so this check could not be saved, and nothing was approved or posted. Close it, then try again.").
  - `post_now`, the Reel retry, `promote_run` and the CLI `review publish` already turn a ValueError into
    "Not posted" or "Refusing".
  - The scheduled `post_due` reports it as it reports a blocked release.
  - The card's re-check catches it, and the saved verdict stands.
  - The pipeline's two gate runs and the copy edit hold the run, with `release_report: <reason>` on the card,
    instead of failing it.
- The review card shows the note even when another check blocks ("picture_colour (note, not blocking): Made before
  the colour fix …"), because it says what a re-render will also fix (`telegram_bot.release_lines`).

## Measurements (`measure.py <root> [--real <output dir> ...]`, counts only)

Generated scenarios: the first check fails to measure (time limit 0), then a normal check runs.

| Scenario | 8a7b8973 | this commit |
|---|---|---|
| gated before QF-10, pre-QF-01 tags | blocked (note lost) | note |
| same, black picture | blocked (note lost) | note, picture_black still blocks |
| never gated | blocked | blocked |
| new render (`full_image_spans` present) | blocked | blocked |
| other wrong tags | blocked | blocked |

Real masters, each copied with its composition record and its release report from the gate before QF-10, then the
same two checks:

| Set | Masters | First check failed | Noted after it, 8a7b8973 → this commit | Colour-blocked after it, 8a7b8973 → this commit |
|---|---|---|---|---|
| Development Reels (29 today) | 28 (1 skipped: no record) | 28 | 0 → 28 | 28 → 0 |
| Production-sample copy | 33 | 33 | 0 → 33 | 33 → 0 |

No other picture check blocked any of them.

## Tests

`tests/test_qf10b_old_master_note.py`, 13 tests:
- **Fail on 8a7b8973** (3):
  - a first-check timeout, then the note;
  - a failed re-measure after a method change, then the note;
  - the card shows the note beside a block.
- **Pin the ruling's scope on both trees:**
  - never gated, new render and first-gated-by-QF-10 stay blocked after a failed check;
  - the mark is for these bytes only;
  - other wrong tags stay blocked.
- **The composer writes `full_image_spans`** on every render: a Reel and a deep dive, with and without an essential
  image.
- **A report held open by another program** (a real Python handle on Windows, which has no delete sharing):
  - saved after a retry once the handle closes;
  - refused as `ReleaseReportLocked` (a ValueError) when the file stays held, with the old report intact and no
    temporary file left;
  - `require_release` refuses it like a blocked release.
