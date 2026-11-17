# QF-115: the bot's startup dashboard never waits on rehashing every pool photo

Finding (lander, 2026-11-12, dev bot restart after UI-01): py-spy had the main thread in `startup_dashboard_card` ->
`control.dashboard_text_and_buttons` -> `ready_pools` -> `asset_pipeline.pool_ready` -> `photo_pool.refresh` ->
`signature` -> `image_quarantine.phash`, once for every pool photo, before any panel was posted. On a loaded machine
the chat stayed silent for about 12 minutes after the restart. Parent: fe88d4a6 (UI-01f).

## What changed

- `src/photo_pool.py`: every photo's signature (SHA-256, pHash, dHash, flat flag) is remembered by file, and for the
  pools under `ASSETS_DIR` also on disk (`cache/photo_signatures.json`). An answer is reused only while the file's
  size, `mtime_ns`, file id and `st_ctime_ns` are the recorded ones and that modification time is more than
  `RACY_SECONDS` (2 s) older than the check, as git's index does it. Otherwise the bytes are read and the answer is
  reused only when their SHA-256 is the recorded one, so a photo replaced with one of the same size is hashed again,
  even within the same clock tick. The perceptual hashes the pool's dedupe uses are the same values. Nothing is
  decoded when the file is unchanged. The record is written atomically under a cross-process file lock
  (`publication_lock`, read, merge, replace), so two processes never lose each other's answers. Photos that are gone
  are dropped. An entry of the wrong shape (a `photos` list or string, a non-numeric field, a signature that does not
  match its digest) is treated as missing and replaced.
- `src/control.py`: `ready_pools(..., wait=seconds)` counts the sets it does not know yet in one background thread.
  When they are not counted within the wait it returns None, and the process's listener (`when_pools_counted`) is
  called once they are. A process with no listener (a run's worker, a command) cannot draw again, so there a wait
  counts the sets in place, as before this row: its dashboard never says "counting" (round 2). `wait=0` (the
  renderer's notice conditions) never blocks. `wait=None` (the default: probe and the other callers) counts in place,
  as before. The dashboard waits `DASHBOARD_POOL_COUNT_WAIT_SECONDS` (1 s, `src/config.py`, `.env.example`). After
  that it says `📋 Queue: N topics · counting photo sets…` and leaves out the "Only N … have photos ready" Needs-you
  line until the number is known.
- `src/telegram_bot.py`: `pools_counted` (the listener) draws the dashboard again in place, only while it still
  shows the counting line and nothing was opened in it since that line was drawn. A tap raises the panel's revision
  as it arrives, before its screen is drawn, and the revision is recorded when a counting dashboard is drawn
  (`_mark_counting`). The redraw builds the card first and then, under `_dashboard_lock` and with the revision
  raised in the same `_panel_lock` section, checks again that the panel, its revision and its text are the ones it
  saw (`show_dashboard(expect=...)`). A screen opened while the card was built is left alone (round 2). With the
  clean chat's renderer running, the renderer's tick draws it, outside `Layout._lock`. Otherwise the counter's thread
  draws it, holding no lock. Either way the draw goes through `show_dashboard`, so the UI-01 lease and lock rules
  apply (a process that is not the renderer asks the renderer). The startup card is rebuilt by its own builder
  (`bot.startup_card`, set by main.py for the bot and the daemon), so its "🟢 Bot started" line, the daemon's
  validation line and the release note stay. A note leading the dashboard (`⏸ Paused…`, `▶️ Resumed…`) is kept. A
  count that ends while the layout is still being posted (before the dashboard's message is known) is caught when
  that `show_dashboard` returns.
- `src/notice_conditions.py`: the renderer's condition sync never waits (`wait=0`). While the sets are being
  counted, the "Photo sets not ready" item stays as it was: it is neither posted nor resolved.
- `main.py`: the bot and the daemon set `bot.startup_card` and register the listener before the first draw.

## Measurements (`python measure.py <repo root>`)

There are 12 generated pools of 15 photos each (2400 x 1600 JPEG, dated a day before the restart). One topic is
queued per pool. Each scenario runs in a fresh process with a fake Bot API. The machine was busy (other builds and
test suites running), so absolute times are inflated on both sides. `before.json` is fe88d4a6 and `after.json` is
this commit (round 2, measured again on the final tree, the machine more loaded than in round 1).

| | before | after |
|---|---|---|
| first message, first start, clean chat off | 80.995 s | 1.122 s |
| first message, next restart, clean chat off | 85.637 s | 1.165 s |
| first message (notifications panel), first start, clean chat on | 103.777 s | 1.312 s |
| dashboard posted, next restart, clean chat on | 74.376 s | 1.411 s |
| dashboard's first queue line | `12 with photos ready` | `counting photo sets…` |
| count shown on the dashboard, first start / next restart (clean off) | 80.995 s / 85.637 s | 73.098 s / 19.009 s (in place) |
| a run's worker after the restart (no listener): its dashboard | counted in place | counted in place, 26.244 s, `12 with photos ready` |
| `refresh()` over 180 photos: cold process | 41.273 s | 48.61 s |
| `refresh()` again in the same process | 0.331 s | 0.909 s |
| `refresh()` in a restarted process | 38.305 s | 0.315 s |
| counted ready sets | 12 | 12 |

The worker row is new in round 2 (`worker_after_restart_plain`, clean chat off as in production). It draws the
dashboard as `stamp_review_outcome` does and must show the number in that one draw. Its 26.244 s is mostly the fresh
measuring process's first import of `src.image_review`; a real worker has imported it while making its run.

The development checkout's real pools (`dev-pools.json`), measured read-only with `QF115_POOLS=<assets folder>`.
The signature record went to the temp folder, and a listing of the assets folder (paths, sizes, modification times)
hashed the same before and after. 31 pools, 278 photos with records; both sides count 15 sets ready. After = round 2,
the final tree, with other suites loading the machine:

| | before | after |
|---|---|---|
| first message, first start, clean chat off | 261.471 s | 1.778 s |
| first message, next restart, clean chat off | 344.768 s | 1.132 s |
| first message, first start, clean chat on | 377.026 s | 1.214 s |
| first message, next restart, clean chat on | 223.088 s | 4.051 s |
| count shown in place, first start, off / on | 261.471 s / 378.157 s | 278.192 s / 162.359 s |
| count shown in place, next restart, off / on | 344.768 s / 223.641 s | 53.002 s / 55.039 s |
| a run's worker after the restart: its one dashboard | counted in place | 12.598 s, `15 with photos ready` |
| `refresh()` in a restarted process | 166.095 s | 5.539 s |

The first start's count with the clean chat off went past measure.py's 600 s window in the full run (its first
message came at 1.162 s), so that scenario was run again alone; the row shows that run. The restarted `refresh()`
decodes nothing: the same process's second refresh took 5.656 s too (stat calls on the loaded machine; 0.408 s in
the quieter round-1 measurement).

The first message now comes within the 1 s wait plus the card's own build. That does not depend on how big the pools
are. The count still costs what it costs on a first start, but the operator is no longer waiting on it. On a
restart, the 19 to 55 s left before the count shows (in this loaded measurement) is mostly the one-time import of `src.image_review` (the LLM
SDKs) in the counter thread. No photo is decoded then.

Signature-cache edge cases taken in round 2 (from the review's probes): a replacement in the same clock tick (the
racy window sends it to the SHA-256, which differs); a copy that keeps the modification time (on POSIX; `os.replace` also changes the file id, and
`st_ctime_ns` is part of the key); concurrent writers (atomic replace, read-merge-write under a cross-process file
lock, and the record is now also read under that lock, because on Windows a reader holding the file open made a
writer's replace fail); answers a save could not write within the lock's 10 s wait are written by the next save; a
record whose `photos` is a list or a string, or an entry with a non-numeric field, is treated as missing and
replaced. A counting dashboard drawn after its count already ended (built before, drawn after) starts its redraw
from a new thread, since it may be drawn under `_dashboard_lock` or `Layout._lock`.

Residual limit (signature cache, not taken): on Windows `st_ctime_ns` is the creation time, so a different photo of the same
size copied over an existing one with its modification time preserved to the nanosecond (`shutil.copy2`) keeps the
stale signature until the file changes again. On POSIX the inode change time catches it. Nothing in the app writes
pools that way.

## Tests

`tests/test_qf115_startup_pool_count.py` uses a slow mocked `pool_ready` and a fake Bot API. It checks four things.
The layout (the panel, then the dashboard) is sent before any set is counted, with the clean chat off and on. The
dashboard line is then drawn again in place with "3 with photos ready", keeping its started line. A screen opened in
the meantime is left alone. The photo condition is kept while the sets are counted. For the signatures, it checks
that a recorded signature is reused after a restart without decoding, and that a photo replaced with one of the same
size is hashed again: within the same clock tick (the same `mtime_ns`), and while the bot was down. Only the pools'
photos go into the disk record. On fe88d4a6 every regression test fails by assertion, apart from the guard test for
a screen opened in the meantime.

Round 2 adds: a process with no listener (a run's worker, clean chat off) shows the number and the "🖼 Only 0 queued
topic(s) have photos ready" line in its one draw; a screen opened while the redraw's card is built, and a tap that
arrived then (its revision raised, its screen not drawn yet), are both left alone; a note leading the dashboard is
kept by the redraw; a record of the wrong shape is treated as missing; two processes writing the record keep all 24
answers. All six fail on 26e7a47c (round 1), by assertion. On fe88d4a6 all six fail too: the worker and the two tap
tests on the missing API (AttributeError on `control.when_pools_counted`), the two-writers test on the missing record
(FileNotFoundError), the other two by assertion. In all, 14 of the 16 tests fail on fe88d4a6 and the two guard tests
pass.
