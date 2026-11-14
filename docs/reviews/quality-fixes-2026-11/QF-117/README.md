# QF-117: safe patch bumps, one verified row

Operator decision 2026-11-14 (tools audit follow-ups, item 5 of the audit's top 5): bump pydantic, fonttools,
soundfile, pytest, ruff and uv to their latest patch releases in one lock change, with hashes, pixel identity on
real frames, pip-audit and CI. anthropic is excluded and stays 1.13.0.

## Versions

| package | before | after | where pinned | why |
|---|---|---|---|---|
| pydantic | 2.14.1 | 2.14.3 | pyproject, requirements.txt | approved bump |
| pydantic-core | 2.50.1 | 2.50.4 | uv.lock only (transitive) | pydantic 2.14.3 requires `pydantic-core==2.50.4` exactly (its metadata) |
| fonttools | 4.67.0 | 4.68.0 | pyproject, requirements.txt | approved bump |
| soundfile | 0.13.1 | 0.14.0 | pyproject, requirements.txt | approved bump; it now declares `typing-extensions` (already locked at 4.16.0, unchanged) |
| pytest | 9.0.3 | 9.2.0 | pyproject, requirements.txt | approved bump |
| ruff | 0.17.3 | 0.17.5 | pyproject dev group | approved bump |
| uv | 0.13.7 | 0.13.8 | pyproject `required-version`, CI `pip install uv==` | approved bump; uv 0.13.7 refuses this project from now on |
| anthropic | 1.13.0 | 1.13.0 | | excluded by the decision |

`uv lock` (uv 0.13.8) changed exactly the six packages above, with sha256 hashes for every artifact; nothing else in
the lock moved. A venv synced from the old lock and one synced from the new lock differ in exactly these six
packages (`uv pip freeze`). Checks on the new lock: `uv lock --check` passes with uv 0.13.8; `scripts/check_lock.py`
"requirements.txt agrees with uv.lock"; `pip-audit --strict` on the hashed export: no known vulnerabilities
(`pip-audit.txt`). The other places that name uv's version are the historical QF-09 and QF-26 READMEs (left as they
were); nothing in `docs/deployment.md` names it.

## Pixel identity

`measure.py <repo root>` renders 91 items from three real development runs (`inputs/`, made by `make_inputs.py`
from the runs' own script, map place, palette, words and hero photo) with the interpreter that runs it, and hashes
the decoded pixels. `before.json` is its output in a venv synced from the parent's lock (981b0326); `after.json` its
output in a venv synced from this commit's lock. Same source tree, same inputs, same ffmpeg 8.1 (libass), the
encoder pinned to libx264.

| item | runs | items | frames | differing |
|---|---|---|---|---|
| stat card graphic (clip, every frame, and still) | Memnon, Gol Gumbaz | 4 | 360 | 0 |
| locator map, country/site/water labels (clip and still) | Memnon, Gol Gumbaz, Wieliczka | 6 | 540 | 0 |
| timeline graphic (clip and still) | Memnon | 2 | 180 | 0 |
| scale graphic (clip and still) | Wieliczka (Memnon: the renderer declines it in both trees, as in the real run) | 3 | 180 | 0 |
| covers with text, all five renderable styles, and the profile-grid tile | all three | 30 | | 0 |
| captions: the ASS file (plates measured through font_engine) and three frames burnt by libass, 9:16 and 16:9 | all three | 24 | | 0 |
| fonts: the face `font_engine._glyph_font_data` compiles with fontTools, and `inspect_font`, the 7 bundled faces | | 14 | | 0 |
| audio: soundfile WAV PCM_16/PCM_24/FLOAT and FLAC bytes, and the samples read back | | 8 | | 0 |
| **total** | | **91** | **1,260** | **0** |

0 differing items is 0 differing pixels: each digest is SHA-256 over the raw decoded RGB(A) buffer (every clip
frame decoded to rgb24). The font check is the strongest: fontTools 4.68.0 recompiles each bundled face
(the private glyph-id cmap Pillow rasterises tracked glyphs from) to the same bytes as 4.67.0. As the audit
expected, the 4.68.0 outline changes are CFF2 instancing only, and all nine files in `fonts/` are TrueType `glyf`
(four carry `gvar`), none CFF or CFF2; `src/` does not call the instancer.

One measurement note: libsndfile writes the current second into a float WAV's PEAK chunk, so two writes a second
apart differ with the same library (shown with both soundfile versions on libsndfile 1.2.2). `measure.py` zeroes
that time stamp before hashing (`audio_bytes`); the samples read back are hashed as they are.

The measurements were taken with this change on 981b0326; the commit was then rebased onto 38f6f5d3 (QF-116, CI's
ffmpeg, and QF-73b, posting), which changes no renderer, font, pin or lock line, so they stand as measured.

Reproduce: run `measure.py <repo root>` with any venv synced from either lock. The map needs the fetched
`tools/geo` data (gitignored): the worktree's own, else, read only, the main checkout's (found through git's common
directory, so it also runs in preflight's fresh worktrees). It prints `differing_items` against `before.json`: 0
with either lock. About 25 minutes on the workstation (the three maps take most of it).

## ruff 0.17.5 on existing code

`ruff-0.17.3-vs-0.17.5.txt`: the whole tree at the parent, no configuration. 0.17.5 reports one finding 0.17.3 does
not: `src/control.py:3100:13 SIM102` (a nested `if` under an `elif`, untouched code), and drops none. Not fixed and
not suppressed here (no noqa, no reformatting): CI only prints `ruff --version`, preflight's per-file check compares
changed files only, and enforcing lint is QF-27. This commit's own new files are clean on both versions.

## pytest 9.2.0

Breaking changes in 9.1/9.2: `parametrize` `ids`/`indirect`/`scope` keyword-only, and `-c` with a missing path now
errors. The suite uses `ids=` only as a keyword (audit grep). The full suite on 9.2.0 is the CI run (Linux).

`pytest.approx` (9.2.0, pytest issue 10210) now raises `TypeError` for any nested container. 9.0.3 refused only a
container nested in one of its own type (a list in a list), so a list of tuples was accepted and each tuple was
then compared by exact equality: the tolerance never applied. One test did that and failed the first CI run on
9.2.0: `tests/test_callouts_v3_2026_11_02.py::test_the_audit_trail_is_written_and_replays_without_a_model`
(branch-only file). Ruling (lander, under the overnight delegation, "QF-117 CI failure", 2026-11-14): keep pytest
9.2.0 and rewrite that one comparison with the same expected values: same length, labels exact and in order, the
numbers by `pytest.approx` at its default tolerance. Because 9.0.3 had really compared the `(label, x, y)` tuples
exactly, the rewrite keeps that exact comparison as a further assertion, so nothing it checked before is checked
less strictly now. Shown with a throwaway mutation of the expected points (not committed), on 9.2.0: unchanged
passes; a changed label, a coordinate off by 0.01, a coordinate off by one part in 10^9, a dropped point and a
swapped order each fail. The rewritten test also passes on 9.0.3.

Other behaviour changes in 9.1/9.2 looked at for a silent change in what the suite checks:

- Parametrize ids of long strings (issue 6881): 70 of the 9,775 collected ids change (the long sentence becomes
  `<argname><index>`), in 20 test functions. Both versions collect 9,775 tests, the same functions in the same
  order; nothing in the repository selects a test by a parametrized id.
- `approx` on `Decimal`, `timedelta` and object-dtype arrays (issues 14754, 15005, 15006): the suite passes none of
  these to `approx`.
- Warning filters now apply during `pytest_configure` and plugin-import warnings are reported (issues 10128, 12697):
  `pytest.ini` sets no warning filter and no `--max-warnings`, so no warning can fail a test.
- Deprecations only (no behaviour change yet): `yield_fixture`, higher-scoped fixtures as instance methods,
  non-collection `argvalues`, non-string ini values. `pytest.ini` holds one string option.

## Regression test

`tests/test_qf117_patch_bumps.py` (4 tests): pyproject, requirements.txt and uv.lock hold the bumped versions
(pydantic-core 2.50.4 included, anthropic still 1.13.0, ruff only in the dev group), and CI installs the uv that
pyproject requires. It checks files, not installed packages, so a venv still synced from the parent's lock can run
the suite. Parent: 4 failed; this commit: 4 passed.
