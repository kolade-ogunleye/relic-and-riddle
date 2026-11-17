# QF-116: CI runs the same ffmpeg as development and production

Operator-approved 2026-11-14 (tools audit of 2026-11-14, item 1). Parent: 981b0326 (QF-20).

## The problem, measured

`.github/workflows/tests.yml` installed the runner's apt `ffmpeg`. Development and production run FFmpeg 8.1
(`docs/deployment.md` step 5: "Keep FFmpeg 8.1 until the 9.0.2 encoded-audio compatibility problem is resolved";
development: `ffmpeg version 8.1-full_build-www.gyan.dev`, configured with libass, HarfBuzz, FriBidi, FreeType,
fontconfig, libx264 and libvmaf). So caption (libass), encode and picture/audio-QA tests ran on a binary that never
ships.

The audit named Ubuntu 24.04's 6.1.1. The CI run of the parent commit (run 43585602356, green: 8885 passed,
381 skipped) shows that `ubuntu-latest` has since moved to the **ubuntu-26.04** image (20261111.196.1), whose apt
build is **8.0.1-3ubuntu2** (libavcodec62, libass9 0.17.4). Still not 8.1, and it would drift again with the next
image, which is the point of pinning.

| | before (981b0326) | after |
|---|---|---|
| CI ffmpeg | apt, whatever `ubuntu-latest` carries (8.0.1-3ubuntu2 on 2026-11-14) | 8.1 (n8.1-7-ga3475e2554), pinned |
| how it is fetched | apt mirror, unpinned | fixed URL + SHA-256, checked before unpacking |
| a release mismatch | passes silently | fails the job before the tests (`scripts/check_ffmpeg.py`) and in the suite |
| filters/encoders/formats src/ uses | not checked | 65 filters, 8 encoders, 9 formats asserted present |

`measure.py <repo root>` prints these facts from the workflow (and the local check where the script exists):
`before.json` (parent workflow) and `after.json`.

## The source: BtbN FFmpeg-Builds, month-end autobuild

- URL: `https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-03-31-13-11/ffmpeg-n8.1-7-ga3475e2554-linux64-gpl-8.1.tar.xz`
- SHA-256: `86aa72199be1f828fa895e1c854e27241a66e038ba23b091ae3b78b5f00814ac` (the digest GitHub records for the release
  asset, and the hash of the file downloaded here; 141,710,540 bytes).

Why this one:
- It is the Linux static build the FFmpeg project's own download page links ("Linux Static Builds",
  https://ffmpeg.org/download.html, checked 2026-11-14; the page links no other Linux static provider). Its GPL
  variant is configured with `--enable-libass --enable-libharfbuzz --enable-libfribidi --enable-libfreetype
  --enable-fontconfig --enable-libx264 --enable-libvmaf` (and the rest the dev build has: libzimg, librubberband,
  libvidstab), so every library the captions and encodes use is present. johnvansickle's static builds, the other
  common source, are no longer linked there and its `ffmpeg-release-amd64-static.tar.xz` URL is rolling (content
  changes in place), which is not a pin.
- The release tag is fixed, and BtbN's README states its retention: "The last build of each month is kept for two
  years. The last 14 daily builds are kept." So a month-end tag (here 2026-03-31) stays downloadable until about
  2028-03; a daily tag would vanish in two weeks. The asset is 8.1 plus 7 release-branch point commits, the closest
  kept build to development's 8.1. Later month-ends carry 8.1.1 to 8.1.3; the audit defers 8.1.3 until after the
  design freeze with a re-render, so it is not taken here.
- Static: no shared libraries from the runner image, so the binary does not change when the image does.

The workflow downloads over HTTPS only (`--proto '=https' --tlsv1.2`), checks the hash with `sha256sum --check
--strict` (any mismatch fails the step before `tar` runs), unpacks only `bin/ffmpeg` and `bin/ffprobe`, puts them
first on `PATH`, then asserts `command -v ffmpeg/ffprobe` are those files and runs `python scripts/check_ffmpeg.py`.
`ffmpeg` is removed from the apt line; the other apt packages are unchanged (libfribidi0 and libfontconfig1 stay:
Pillow's raqm and fontconfig's configuration use them). No other step changed: lock check, hashed install,
`check_lock.py`, `pip-audit --strict`, the ruff version step, the topic-bank lint, `pytest -q -n 4` and the Windows
launcher tests are as before.

## What `scripts/check_ffmpeg.py` asserts

1. `ffmpeg -version` and `ffprobe -version` both report major.minor 8.1 (`RELEASE`). The parser accepts both the
   dev form (`8.1-full_build-www.gyan.dev`) and the BtbN form (`n8.1-7-g...`); 8.0.1, 6.1.1, 9.0.2 and a git nightly
   (`N-123456-g...`) all fail (tested).
2. The `configuration:` line has `--enable-` for libass, libharfbuzz, libfribidi, libfreetype, fontconfig, libx264
   and libvmaf.
3. Every filter in `FILTERS`, encoder in `ENCODERS` and format in `FORMATS` is in the binary's `-filters`,
   `-encoders` and `-formats` listing.
4. Those lists cover what `src/` uses: it tokenises `src/**/*.py` and takes, from string literals only (comments never
   count), every name the binary offers as a filter that stands in filter-graph position (after `[ ] , ; { }` or the
   quote, before `=`, `,`, `;`, `[`, `]`, `@label` or the quote), every encoder literal after `-c:v`/`-c:a`/`-vcodec`/
   `-acodec`/`-c`, and every format literal after `-f`. A name `src/` starts using that the lists do not name fails
   the check, so the list cannot go stale. A few ordinary words are also filter names (`copy`, `null`, `format`,
   `field`, `signature`, `thumbnail`): they are listed rather than special-cased; every full build has them.

Required lists (dev 8.1 and the pinned build both offer all of them):
- filters (65): adelay, aeval, afade, aformat, alimiter, amerge, amix, amultiply, anullsink, apad, aresample,
  asetpts, asplit, astats, atrim, blend, boxblur, color, colorspace, compand, concat, copy, crop, cue, curves,
  ebur128, eq, equalizer, exposure, field, format, fps, highpass, hstack, hue, identity, life, loop, lowpass,
  metadata, null, overlay, pad, pan, phase, scale, scroll, segment, select, setparams, setpts, setsar, settb,
  sidechaincompress, signature, silencedetect, split, subtitles, thumbnail, tile, tpad, trim, vignette, volume, xfade
- encoders (8): aac, ffv1, h264_nvenc, libx264, pcm_s16le, pcm_s24le, png, rawvideo (`h264_nvenc` is listed by both
  builds; CI has no GPU, so `src/encoding.py`'s probe falls back to libx264 there as before)
- formats (9): f32le, image2, image2pipe, lavfi, matroska, mp4, null, rawvideo, wav

## Local verification (development PC, no production contact)

- Development ffmpeg (`8.1-full_build-www.gyan.dev`): `python scripts/check_ffmpeg.py` passes (`after.json`,
  `local_check_problems: []`).
- The pinned build, hash-verified, run under WSL Ubuntu 24.04 (a Linux host like the runner):
  `ffmpeg version n8.1-7-ga3475e2554-20260331`; `python3 scripts/check_ffmpeg.py` passes (exit 0, same line).
- A libass caption frame (1080x1920, Montserrat Bold 80 from `fonts/`, outline and shadow, diacritics, an "fi"
  ligature pair, curly quotes, an em dash) rendered by dev 8.1 on Windows and by the pinned 8.1 on Linux through
  `subtitles=...:fontsdir=.`: rgb24 frames byte-identical, SHA-256 `7502dce0...b3e19` on both. So libass,
  HarfBuzz, FreeType and fontconfig in the pinned build lay captions out as development does.

## Tests

`tests/test_qf116_ci_ffmpeg.py` (fails on the parent: the workflow apt-installs ffmpeg and the check does not exist):
- the workflow apt-installs no ffmpeg; `FFMPEG_URL` is a fixed BtbN `autobuild-*` tag whose asset names 8.1 (never
  `latest`); `FFMPEG_SHA256` is 64 hex; the hash is checked after the download and before `tar`; PATH is set before
  the check, and the check runs before pytest;
- the ffmpeg on PATH passes `check()` (dev 8.1 locally, the pinned build on CI);
- `check()` fails on 8.0.1, 6.1.1, 9.0.2 and a nightly for both tools, on a build without libass, on a build without
  `subtitles`, and when `src/` starts using a filter (`zoompan`), encoder (`libvpx-vp9`) or format (`webm`) the lists
  do not name, while a comment naming a filter does not count;
- a missing binary is reported for both tools.

## CI results

Pending: the lander pushes this commit to `claude/qf-ci-QF-116`; the run id and result are recorded in the build
note (`queue\QF-116.build-*.md`) and here on the next amend.

## Finding: `test_real_ffmpeg_mix_preserves_stereo_bed` is nondeterministic on every release (not this row's)

The lander asked for this test to be checked on 8.1: CI run 43622218766 (apt 8.0.1-3ubuntu2, ubuntu-26.04; a commit
that does not touch audio) failed it with an L-R RMS of 0.0468 against `> 0.05`.

The test mixes 1 s of silent voice with a 1 s stereo bed through `_audio_chain(0, 1)`, i.e. with `end=None`: the
path whose docstring describes the input-order race (sidechaincompress forwards the first EOF it sees and drops what
the other input had queued). The same input, run repeatedly:

| binary | runs | L-R RMS (last bed sample of 48,000) |
|---|---|---|
| dev 8.1 (gyan, Windows) | 15 | 0.0898 (39,311) x7, 0.0943 (43,439) x2, 0.0975 (46,367) x1, 0.0992 (47,999) x5 |
| pinned 8.1 (BtbN, Linux/WSL) | 20 | 0.0850 (35,231) x1, 0.0898 x1, 0.0943 x5, 0.0975 x6, 0.0992 x7 |

The bed is cut short by whole filter frames in a share of runs on both 8.1 builds, so the margin over 0.05 depends
on scheduling; a loaded runner can cut more (0.0468 means about three quarters of the bed was dropped). The pinned
8.1 does not make the test stable and does not make it worse; the threshold is not changed here. The delivered
master always passes `end=` (`src/video_composer.py` render path), so the shipped mix is sample-bounded; but
any measurement call that passes no `end` (`end_card_level` builds `end=None` when there is no pad; `_calibrate_gain`
defaults to `end=None`) goes through the same racy graph. Not traced further here: not this row's scope. Recommended follow-up row: give the test (and those measurement calls) a sample-bounded `end`,
with the operator's approval for the origin/main test change.

## Ways this could fail, and what guards it

- Hash or asset swapped upstream: `sha256sum --check --strict` fails the step; nothing is unpacked.
- The month-end release is pruned (after two years): the download fails loudly (curl `-f`); the fix is a new pin,
  never an unpinned fallback.
- A runner image ships its own ffmpeg ahead on PATH: the step asserts `command -v` is the pinned file.
- Code starts using a filter the pinned build lacks: check 3/4 fail before the suite runs.
- The dev or prod release moves (8.1.3, 9.0.2): `RELEASE`, the URL and hash change together in one row, with the
  re-render the audit asks for.
- Static fontconfig not finding a configuration: the caption tests pass `fontsdir` to libass and the byte-identical
  render above used the same path; any failure shows on CI as a real finding.
