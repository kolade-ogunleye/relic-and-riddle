# QF-74 Run deadline, missed-slot notice and stage budgets (2026-11-11)

**Finding** (project quality review 2026-11-05, the QF-74 row). A run had no deadline: `run_in_worker` passed
`timeout=None` to `subprocess.run`, and only `FFMPEG_TIMEOUT_SECONDS=3600` applied, per FFmpeg call, inside a
retry loop. A run approved after its slot was silently booked into the next day's slot. No stage had a budget, and
`src/stage_timer.py` had no tests.

Round 3 replaces 48951828 (review 2 NOT OK 4/10). It is rebuilt on the batch-6 tip 2debdf8b. The design is new
where the review or the brief asked for it: the deadline stops a run at a stage boundary and keeps its files, and
the deadline and grace are settings with measured defaults.

## What changed

**Deadline in the worker (`main.PipelineCoordinator._check_cancel`, `src/worker_deadline.py`).** `claim_run` starts
the run's production clock. This happens each time a run is made or resumed, so a resume gets a new deadline.
Every stage boundary already checks for an operator's cancel, and now it also checks the deadline. Past
`RUN_DEADLINE_MINUTES`, a run that is still `producing` stops as `timed_out`:
- every file it made stays in `output/<run>`;
- its topic goes back in the queue;
- its progress card ends with "Passed its N-minute production deadline";
- one Needs-you notice is sent.

The stop is raised as `RunDeadlineExceeded`, a subclass of `JobCancelled`. The stage code's existing
`except JobCancelled: raise` paths therefore pass it on unchanged. `run_cycle`, `resume_run` and the documentary
return `{"status": "timed_out"}`, and the worker exits 1 (a timed-out run is not a completed run). A run that is
waiting for your review, uploading, or already stopped is never changed: the update only applies to a run that is
still `producing`. A review wait is never `producing`, so it never counts. Once this process has stopped a run,
every later boundary raises again, so a stage that swallowed the first stop cannot carry the run on (a new claim
of the same run, a resume, starts a new clock and clears that). The topic goes
back in the queue only while this run is what holds it in production: in a dual run whose Reel is already booked
or posted, the documentary's stop leaves the topic as it is, and its notice says so. A documentary that stops
without its own progress card sends the one deadline notice and no second "Documentary failed" message.

**Supervisor backstop (`run_in_worker`, the bot's `monitor_worker`).** The worker is launched with `Popen` and
given a random `RELIC_WORKER_ATTEMPT`. `claim_run` records that attempt on the run's owner record.
`worker_deadline.supervise` waits for the worker in 30-second polls.
- Production time stops while every run it claimed is `pending_approval` or `timeout`.
- Production time restarts from zero after a decision, because what follows (a regeneration, a re-cut, the upload)
  is new work.
- Past deadline plus grace (`RUN_DEADLINE_GRACE_MINUTES`, a stage that never reached a boundary), it stops only its
  own child's process tree: `taskkill /T` on Windows, or the child's own session on POSIX.
- A worker is never stopped on an unknown state. The daemon's supervisor opens this process's database at its first
  poll; a failed open (a locked database) or any error reading the runs is retried at the next poll, and the stop
  waits for a poll that can read the runs and sees none of them in review.

`finalize_stopped_worker` then changes only that attempt's runs:
- `producing` becomes `timed_out` (topic queued under the same rule, files kept, progress card finished with
  "Passed its 180-minute production deadline": the deadline, never deadline plus grace; the notice names the grace
  separately: "passed its 180-minute production deadline and no stage finished in the 60 minutes after it");
- `publishing` becomes `publish_unknown` (topic held, never retried; the existing Posted / Not posted answer
  settles it);
- reviews, published runs and other workers' runs are left as they are.

It sends one notice per run. That notice is keyed in settings (`run_deadline_notice:<run>`), so the worker's notice
and the supervisor's notice are never both sent. Before, the daemon's worker waited without a limit (`timeout=None`;
a timeout given would have raised `TimeoutExpired` past the result handling with the worker still running) and the
bot-mode monitor waited forever on `communicate()`. `run_lock` is untouched: the monitor
still holds it while it supervises. A run's `run_deadline_notice:` setting is deleted when it ends (`release_run`,
the supervisor's stop, or `reconcile_interrupted_runs` after a crash). Its `run_started:` setting goes at the same
points, except that a run released while its review still waits keeps it until its booking reads it (a review
resumed later in another process is then judged from its production start, not its creation).

**Re-render keeps the files.** `timed_out` joins `REMAKE_STATUSES` and `resume_run`'s statuses. On the dashboard
(`control.STOPPED`), it is offered like a late failure ("Stopped at its deadline, files kept"), counted in the health
line and shown as "⏱ Last run stopped at its deadline". It has a status word and an icon.

**Missed slot (`book_slot` → `worker_deadline.missed_slot_notice`).** `claim_run` records when the run's production
started (`run_started:<run>`). When the slot booked at approval is later than the first free slot after that start,
one notice is sent through `_alert`, the existing notify path, in the studio stream that the existing "Missed the …
slot (daemon off)" notice already uses. It has no buttons, so it leaves the chat like other notices (no new standing
message, ready for UI-01 to rehome). The notice reads "⏰ Missed today's slot (Wed 11 Nov 19:00 EST): <run> was
approved too late for it, so it is booked for Thu 12 Nov 12:00 EST." Nothing is moved by it. A run started after
the day's slot is not told that it missed one.

**Stage budgets (`data/stage_budget.json`, `src/stage_timer.py`).** The budgets are the spec's: script 20, voice 8,
pictures 25, render 15, QA 5 and upload 5 minutes. The file maps each timed step to its stage
(`master_render_attempt2` → render). A run's total for a stage includes every attempt, failed ones included.
- When the total crosses 150%, one WARNING is logged at that moment.
- `file_findings` files one `operations.stage_budget.<stage>` finding per run and stage, never a second. It runs
  when the run's findings are collected, after each upload (the upload is now timed as the `upload` stage), and
  when the supervisor stops a worker.
- `music_and_bed`, `review_proxy` and `image_prep_wait` are timed but not budgeted. `image_prep_wait` overlaps
  `image_prep`.
- An impossible duration (negative, NaN, infinite) is logged and not written. Timing never fails a run.
- The weekly digest gains "Stage time (minutes per run, p50 / p90, budget)" per stage, plus the number of runs
  stopped at the deadline.

**Settings (`src/config.py`, `.env.example`).** `RUN_DEADLINE_MINUTES=180` and `RUN_DEADLINE_GRACE_MINUTES=60`.
Each must be positive and finite, or start-up fails with a clear message. A bot-started worker re-reads `.env`, and
its supervisor reads the same values from that environment (`config.run_deadline_limits`).
`WORKER_POLL_SECONDS=30` and `STAGE_BUDGET_FILE` are fixed. Development's existing flags and spending settings are
unchanged.

## Why 180 and 60, not the spec's 150 (measured)

`real_data.py` reads the development runs' `timings.json`, with creation times from a read-only copy of the
development database. It reads a production-sample copy for counts and medians only. Its output is
`real_data.json`, and `measure.py` reads it.

| | development (35 runs, 35 attempts) | production sample (34 runs, 37 attempts) |
|---|---|---|
| production minutes, median | 19.6 | 27.2 |
| production minutes, p90 / max | 50.7 / 75.4 | counts only |
| attempts past 150 min | 0 | **1** (it finished normally, with three master renders) |
| attempts past 180 min | 0 | 0 |
| longest single timed step | 57.0 min (a script stage) | 0 steps past 60 min, 2 past 30 |

At the spec's 150 minutes, one healthy production-sample run would have been stopped minutes before its review
card. 180 stops none of the 35 development and 37 production-sample attempts and is more than twice the development maximum (75.4 min). 180 plus 60 also
stays within the review window: a run started at the 09:00 daily time has its production bounded before the 19:00
slot. A run longer than about 2 h 40 min still misses a 12:00 slot. The new notice then says so; the deadline does
not stop it.

The grace covers the longest single step measured (57 minutes), so a working stage always reaches its own
boundary and stops cleanly first. The hard stop is only for a hang.

Both settings are operator-adjustable. Setting `RUN_DEADLINE_MINUTES=150` gives the spec's figure.

## Stage budgets against real runs

| stage (budget) | dev p50 / p90 / max | dev runs over 150% | production sample: median, runs over 150% |
|---|---|---|---|
| script (20) | 5.6 / 14.3 / 57.0 | 1 of 34 | 9.6, 1 of 34 |
| voice (8) | 0.1 / 0.1 / 0.3 | 0 | 0.2, 0 |
| pictures (25) | 3.8 / 9.5 / 41.0 | 1 of 32 | 3.7, 0 |
| render (15) | 4.5 / 10.7 / 14.2 | 0 of 31 | 6.1, 1 of 34 |
| QA (5) | 1.7 / 4.4 / 5.7 | 0 of 30 | 2.0, 2 of 34 |

The spec's budgets are kept. Each sits at or above the development p90, so a finding marks a real outlier: about
6% of development runs and 4 category-runs in the production sample. Upload has no recorded timings before this
commit; it is timed from now on.

## Acceptance (`python measure.py <repo root>`)

| | base 2debdf8b | this commit |
|---|---|---|
| worker that never ends (a 600 s limit given) | `TimeoutExpired` escapes `run_in_worker` (without a limit, the default, it waits forever); run stays `producing`, topic `in_production`, 0 notices | `timed_out` (stopped); run `timed_out`, topic `queued`, files kept, **1** Needs-you notice |
| stage boundary past the deadline | carries on (`producing`) | `RunDeadlineExceeded`; `timed_out`, topic `queued`, files kept, **1** notice; a review past the deadline stays `pending_approval` |
| approved 21:00 for a run made for 19:00 | booked Thu 12:00, **0** notices | booked Thu 12:00, **1** "Missed today's slot" notice |
| render 22.5 min (two attempts) vs 15-min budget | 0 findings, no budget file | **1** finding |
| real runs past the default deadline | — | 0 of 35 dev, 0 of 37 production-sample attempts |

The real-data columns are identical on both trees: they are the committed `real_data.json`.

## Not changed / known limits

- The weekly digest is scheduled on Sunday (`main.py`, since its introduction), while its panel says "it comes on
  Monday mornings". The spec calls it "the Monday report". The day is left as it is, because moving the operator's
  report is not this row. The stage lines appear in whichever digest is sent.
- On POSIX the hard stop kills the child's session. A grandchild that started its own session would survive;
  workers do not do this.
- The supervisor counts the poll during which a review begins (at most 30 s) as production time.
- Evidence renders: none. The changed surfaces are three short text notices, quoted above, and the dashboard words.
