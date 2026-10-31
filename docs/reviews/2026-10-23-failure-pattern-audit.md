# Failure-pattern audit, 2026-10-23

After a day of production and dev failures, the whole codebase was checked for each pattern behind
them. The checks were run across `src/*.py` and `main.py`, then each hit was read by hand. Each
confirmed fault is fixed with a test; anything left alone is recorded here with the reason.

## Patterns and findings

| # | Pattern (seen today as) | Codebase check | Found | Status |
|---|---|---|---|---|
| 1 | Shared object created on first use without a lock (upscaler: 4 DirectML sessions at once, worker crash 0xC0000005) | every function that lazily sets a module global | `privacy._get_yunet`, `privacy._get_cascade`. One OpenCV detector was shared by parallel face blurring, and each call resets its input size before detecting | **Fixed**: per-thread detectors (`3fbab8a`). Upscaler fixed (`102798b`). Parallax was already safe (created under its inference lock) |
| 2 | A call that can hang forever | `subprocess` calls and HTTP requests without a time limit | ffmpeg in `video_composer._run_ffmpeg` and the motion check's frame grabs. All HTTP requests already have time limits (the other hits were dict lookups) | **Fixed**: render 1 h, frame grabs 2 min (`3fbab8a`). The bot's wait for its run worker has no limit by design: a run can be long |
| 3 | State left wrong after a crash (a dead run stayed "producing" and blocked the deployment stop) | anything that clears a run whose process ended | Nothing did | **Fixed**: daemon and bot startup reconcile such runs (`3fbab8a`) |
| 4 | Native library used from several threads | ONNX Runtime, OpenCV, Azure Speech SDK, PIL | covered by 1; speech runs on one thread | Fixed under 1 |
| 5 | Error swallowed silently | broad `except` whose body is only `pass`/`continue` in publishing, release, database, backup and orchestration | 17 handlers: 16 are best-effort by design (Telegram progress cards, idempotent migrations, optional metadata). One (`publisher` voice record) blocked publishing for the wrong reason | **Fixed**: true reason (`3fbab8a`) |
| 6 | A result depends on where code runs (tests read production's `.env`; a setting read before `.env` loaded) | settings read outside `config.py`; tests reading deployment values | 3 settings outside config, 5 undocumented; tests read deployment choices and credentials | **Fixed**: `5afd0e1`, `8f814c2`, `ac742e6`, with guard tests |
| 7 | Rules that contradict each other (word count vs. spoken time) | duration and length constants across prompt, structure check, spoken check, release gate | the length conflict | **Fixed**: rc6, `748a28c`, `284dcb3` |
| 8 | A destructive step before a risky one (review card's buttons removed, then the upload failed) | re-send flows | review re-send | Deliberate: the old card's approval state is invalidated by a re-send, so its buttons could not work anyway. **Fixed**: the failure notice offers Resend (`0509a0b`) |
| 9 | Process lifecycle (task restarts left 7 dev bots competing) | how runtimes are stopped and started | dev launcher | **Fixed**: single-instance guard (`ebec5ee`); restarts go through `scripts/windows_runtime.ps1` |

## Production runs in a worker process (decided after the audit)

A production run used to execute inside the daemon process, so a native crash in a run (the class
of pattern 1) would have stopped the daemon itself: its Telegram bot and its scheduler too. With the
operator's approval, the daemon now runs each cycle as `main.py run` in its own process, as bot mode
already did. The daemon keeps polling Telegram; the worker reads decisions from the database and
writes its outcome to a result file. A worker that ends without one is reported as crashed (with
its exit code), its run is cleared and its topic requeued, and the daemon carries on.

Whole-process supervision and restart of the daemon itself belong to the lab's managed task and
workload alerting (Wazuh); startup reconciliation (pattern 3) covers the application side once it
restarts.
