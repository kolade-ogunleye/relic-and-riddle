# QF-49: Scrub operator and infrastructure identifiers

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP). Start commit `a9089ea`.

## Metric, before -> after

The row's acceptance grep (`git grep -Il` over the user name, the lab's DNS suffix, the GitHub handle and webmail addresses,
case-sensitive) over the tracked tree, measured by `measure.py`:

| Measure | Before (`a9089ea`) | After |
|---|---|---|
| Files the row's grep matches | 163 | 3 |
| ... outside the attribution allowlist | 160 | **0** |
| Lines the test's wider denylist matches (case-insensitive, plus the host's short name and the jump host) | 1031 | 4 |
| ... outside the allowlist | 1027 | **0** |
| `tests/test_no_operator_identifiers.py` in the suite | no | yes |

The 3 files left hold 4 allowlisted lines, each one a third party's attribution or contact (justified in the test):
a Commons photographer's author credit in `assets/derinkuyu_underground_city/credits.json` (a rejected file;
sanitising author strings at ingest is QF-42) and the Prelinger Archives' published licensing contact in
`src/archive_film.py` and its test.

Re-run: `python docs/reviews/quality-fixes-2026-11/QF-49/measure.py <repo root>` prints the JSON and writes
`measurements.json`. `before.json` is that script run on a worktree of `a9089ea`, `after.json` on the fix. Every path
it prints is repo-relative. The patterns carry one bracketed letter (`omokolad[e]`) so the script, the test and the
review row do not match themselves; the regex is the row's.

## What changed

- **Paths.** Every absolute path into the development checkout (JSON-escaped, plain, forward-slash and ffmpeg-escaped
  forms) is now repo-relative with `/` separators: the 25 SFX rights records (`data/sfx/*.rights.json`), the Wieliczka
  library script's `reference_path`, review evidence JSON, run logs and evidence scripts (111 files under `docs/reviews`, the review row's own wording
  among them), the release
  report test fixture, `docs/*.json`, `docs/issues`, `evals/light_engines`. A scratchpad path became
  `<scratchpad>/...`; production paths under the operator's profile became `<operator-home>\...`.
- **Records stay clean.** `audio_provenance.record_sfx` writes the repo-relative path (`tracked_path`) for a file in
  the checkout; `script_library.export` writes `reference_path` the same way. Neither field is signed or hashed:
  `sfx_rights` checks the bytes' sha256, `reference_path` is in `fact_evidence.STAMP_UNSIGNED` and `kept_script`
  re-points it at the checkout's copy. No record needed re-signing.
- **User-Agent.** `archive_film` builds its User-Agent with `user_agent(config.BRAND_CONTACT)`; `BRAND_CONTACT` is a
  new setting in `src/config.py`, documented in `.env.example` (empty: brand and purpose only).
- **Telegram topics.** `.env.example` carries placeholders (commented, so a copied file still parses) for the five
  forum topic ids instead of the live ones.
- **Host, handle, accounts.** `AGENTS.md` and the docs say `<production-host>`, `<owner>` (GitHub links), and
  `<jump-host>` / `<DOMAIN>\<operator>` in the SSH example. Evidence scripts under `docs/reviews` find the checkout
  from `__file__` (or `RR_FIXTURE_REPO`) and ffmpeg on `PATH`.
- **Deployer scripts** (code only; nothing installed or run on production):
  `Install-ContentReleaseDeployer.ps1` takes `-Repository` as a mandatory argument, `-Signer` defaults to the only
  principal of `release/allowed_signers` (the check that it is in that file is unchanged), and `-Git` defaults under
  `$HOME`. The pinned key's principal is renamed `release-signer` (same key, same single entry).
  `Promote-ContentRelease.ps1` takes `-HandoffFlag` as a mandatory argument. `docs/deployment.md` shows both.
- **Handoff copy** (`deployment/windows/handoff`, the lab's source): `Stop-ContentVmLegacyTasks.ps1` takes the legacy
  worker's account as `-LegacyOwner` (required only when a worker exists), play 52 targets
  `{{ content_runtime_host }}`, a test fixture uses `LAB\operator`. `provenance.json` keeps the lab's `sha256` and adds
  `local_sha256` for those three files.

## Branch-only expected-value change

`deployment/windows/tests/ContentDeployerInstall.Tests.ps1`, "reads the pinned signer principal": `Should -Contain`
the handle -> `'release-signer'`, because this row renames the principal in `release/allowed_signers`. The
"builds the deployer settings" test passes example arguments instead of the real handle (input only). This test file
is not on `origin/main`. The handoff Pester test changed on `origin/main` (`ContentIdentityProbes.Tests.ps1`) changes
an input (a foreign task owner), not an expected value.

## Tests

- `tests/test_no_operator_identifiers.py`: the denylist over the tracked tree with a four-line allowlist (file plus
  the line's exact text, since line numbers move), and a check that every allowlisted line still exists.
- `tests/test_qf49_identifier_scrub.py` (regression): fails on `a9089ea` (7 of 8), passes on the fix (8 of 8). It
  also mocks the archive.org request and asserts the User-Agent it sends, and checks that `record_sfx` and
  `script_library.export` write repo-relative paths.
- Pester (`deployment/windows/tests`, `handoff/tests/unit`): 66 passed, 8 failed both before and after. The 8 failures
  come from lab files this copy never had (`site.yml`, `Register-ContentVmIdentityProbes.ps1` and others).

## Out of scope, and what is left

- **Git history** is out of scope: the identifiers stay in earlier commits. Rewriting published history is the
  operator's decision, not a code change.
- Binary files (`git grep -I` skips them, as the row's grep does): photo EXIF and rendered images were not inspected.
- Kept because they are working values, not text: the bot ids in `src/control.py` and `src/backup_runtime.py`
  (and the tests and docs that name them), the forum topic defaults in `src/config.py` (an environment without the
  variables relies on them), and the computer-name guard `CONTENTCLIENTWI` (tests on `origin/main` expect it). Moving
  them into settings is follow-up work. Test fixtures still use the operator's first name as a Telegram `first_name`;
  tests on `origin/main` assert it, so changing it would change an expected value.
- When the deployer is next installed, pass `-Repository`; when promoting, pass `-HandoffFlag`. The installed
  deployer keeps its own signer copy and settings until that reinstall.
