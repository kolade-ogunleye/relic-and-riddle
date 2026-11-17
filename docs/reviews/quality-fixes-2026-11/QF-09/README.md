# QF-09: Pillow 13, hashed dependency lock and bounded image decoding

The current candidate sits on QF-08 (`8b64b78`). `current-before.json` and
`current-after.json` are the current qualification evidence. Older `before.*`
and `after.*` files are retained historical evidence and are not current results.

## Reproducible measurements

Run `python docs/reviews/quality-fixes-2026-11/QF-09/measure.py <repo root>`.
All image inputs are generated; downloads and the external upscaler are mocked.
Only pip-audit performs network advisory lookups. The harness records actual
stream/redirect argument values, rather than merely checking that keys exist.

The parent probe is `0aeef52`, whose application code is identical to `8b64b78`
(the difference is only the QF-08 ledger finalization). It ran in the shared
project interpreter with Pillow 12.3.0; the candidate ran there with Pillow
13.0.0 and raqm enabled. Evidence records the measured revision plus changes.

| Measurement | Parent | Candidate |
|---|---|---|
| Pillow installed / pinned | 12.3.0 / 12.3.0 | 13.0.0 / 13.0.0 |
| requirements advisories | 39 | 0 (78 packages audited) |
| hashed lock advisories | no lock | 0 (81 packages audited, strict) |
| scipy pin | unpinned range | 1.17.1 |
| uv.lock | absent | 83 packages, 479 artifact hashes |
| requirements / project / lock agreement | absent | passes |
| global pixel limit lifted / changed during decodes | 7 / 12 | 0 / 0 |
| 120 MiB body bytes read | 125,829,120 | 52,494,336 (50 MiB + one 64 KiB chunk) |
| oversized download saved | yes | no |
| streamed request | no | yes |
| automatic redirects disabled | yes | yes (QF-44 retained) |

## Current validation

- New regression tests on the parent: **24 failed, 4 passed** (15.48 s).
- Pillow 13 covering qualification: **2652 passed, 8 existing skips, 46 warnings**
  in 5103.05 s, `-n 4 --dist worksteal`, across the 61 files listed in
  `qualification-files.txt`. This includes the QF-09 image/lock regressions,
  text, captions, cards, graphics, maps, relief, upscaling, Commons and SSRF.
  It is a covering run, not the full landing suite.
- No measured text/graphics expected value changed. No origin/main expected
  assertion changed; no test was skipped, disabled or quarantined for this row.
- `uv lock --check`, requirements/lock agreement and the strict hashed audit pass.
- Ruff comparison against the parent has no new findings per changed file.

The shared interpreter was restored to Pillow 12.3.0 with raqm after all
qualification runs finished, so earlier rows can land against their own pins.
The installed-version regression intentionally requires Pillow 13; independent
review and eventual landing must use a coordinated idle dependency window.
Do not switch packages while another suite is running. Full-suite qualification
is still required at landing, followed by green hosted CI.

## What changed

- `requirements.txt` pins pillow 13.0.0 and scipy 1.17.1. It stays the production install source: the deployer
  is unchanged, and edge-tts, pip-audit, pytest, pytest-xdist and httpx stay in it (operator decision,
  2026-11-05).
- `pyproject.toml` holds only what uv needs: the same pins as requirements.txt, plus a `dev` dependency group for
  tools production never runs (ruff 0.17.3). `uv.lock` was made with uv 0.13.7, which was installed into the
  project venv.
- `scripts/check_lock.py` fails when requirements.txt and uv.lock disagree. It checks that every requirement is
  an exact pin, that requirements.txt and pyproject.toml hold the same pins and markers, that uv.lock locks each
  pin at its version, and that every locked artifact has a sha256 hash.
- `.github/workflows/tests.yml` installs uv, runs `uv lock --check` and installs the exported lock with
  `--require-hashes` (dev group included). It then runs `check_lock.py`, then `pip_audit --strict
  --require-hashes --disable-pip` on the lock, which fails on any advisory, then runs ruff and the suite.
- `src/trusted_image.open_trusted` decodes our own PNG/TIFF rasters under a limit that applies to that one call.
  `Image.MAX_IMAGE_PIXELS` is never assigned. It is used by `graphics._relief`, `upscale._upscale_ncnn` and
  `fetch_relief.valid_relief`.
- `commons.download_jpeg` streams the body and reads at most `COMMONS_DOWNLOAD_MAX_MB` (default 50; set in
  `src/config.py` and documented in `.env.example`). A larger declared Content-Length is refused before any of
  the body is read. Downloads are still decoded under Pillow's own pixel limit.

## Pillow 13 compatibility

The saved release-note assessment identified dropped Python 3.10 support (this
project uses 3.11), HarfBuzz wheel linkage, RGBA/LA reducing-gap changes, TIFF
duplicate-tag behavior, I-mode PNG saving changes, and removed/moved private image
helpers. Current text/graphics qualification above passed without repinning any
measurement. The trusted helper limits each PNG/TIFF decode individually and
never changes `Image.MAX_IMAGE_PIXELS`; untrusted downloads retain Pillow's guard.

The integration also preserves QF-44 URL validation on every redirect, refuses
oversized Content-Length before reading, does not parse streamed JSON errors
before enforcing the byte limit, and closes refused/error/retry responses.

Branch-only fixture adaptations: QF-09 mock URLs use the provider allowlist with
mocked public DNS, and assert both streaming and redirect suppression. These
new QF-09 tests do not exist on origin/main. No existing expected values changed.
