# QF-13: lossless prepared photo layers

No prepared photo layer goes through a new 4:2:0 JPEG generation any more.

- **Downloads** (pool, beat and then-and-now photos, and operator uploads sent through Telegram). An upright baseline or progressive RGB or
  greyscale JPEG keeps its own coded bytes as `.jpg`. Its metadata segments are stripped without a re-encode, and the
  file ends at its end-of-image marker, so an appended payload (a motion-photo video, a vendor trailer) is dropped.
  JFIF, ICC and Adobe are kept (`commons.strip_jpeg_metadata`, `kept_jpeg`). A source that must be decoded is
  written once as PNG: one with an EXIF rotation, a CMYK or palette image, or a TIFF, PNG or WebP. On the parent,
  every download was re-encoded at q92, one more generation.
- **Derived intermediates are PNG:** colour balance, text-band and detail crops, upscale output, the privacy
  blur, then-and-now registration, motion anchors, illustration references and composites.

Originals and old caches are left as they are. Existing `NN_commons.jpg` pool files are not converted, and new
downloads skip both `.jpg` and `.png` numbers. The PNG keeps the 8-bit RGB pixels each stage computed. It cannot
restore detail that a source JPEG already lost. QF-52's essential-image policy still holds.

Callers read the written file through `commons.stored(dest)`. Name-keyed records follow a crop that is now `.png`
through `scene_planner.photo_names`, which tries the photo's own name, then its checked-lineage originals, then its
stem. Those records are the approved storyboard (`storyboard_order`) and the fit cache (`_known_text`).

## Measurement

`measure.py <repo root> [<data root> [<runs> [<stills per run>]]]` prints JSON and writes only to a temporary
folder. `render.py` makes the boards. Both run on the parent and on this row.

- **A. Fixture.** Seven production serialization paths run on generated pixels. On the parent, all seven write
  JPEG 4:2:0 and change pixels. On this row, all seven are lossless.
- **B. Real runs.** Each still is taken from a run's `provenance.json` and sent through the measured commit's own
  chain: download (transport mocked, the pool file's bytes served), `upscale.ensure_resolution`,
  `colour_balance.balanced`, and the privacy blur where the run made a privacy copy. The chain runs twice, as
  committed and as a lossless reference in which every JPEG save in Pillow or OpenCV writes PNG pixels. Each
  surface is then rendered from both layers by the commit's own function:
  - Reel still: `parallax.render_clip`, frame 0, decoded with `BT709_TO_RGB`.
  - 9:16 photo ground: `typography.cover_crop`. The stat card, callout and hook frame draw on this ground.
  - 16:9 stat ground: `graphics.stat_backdrop_wide`.
  - Then-and-now: `frame_pair` + `compose`, on the run's own `then_k`/`now_k_0` candidates.
- **C. Cost.** Bytes and save time of every image file the chain leaves, by stage (`disk_megabytes_by_stage`).
  For 24 pool photos, the measure also compares JPEG q92 (the parent's download), PNG at the default level and at
  level 1, and what this row stores.

Data: the dev checkout's runs and a temporary copy of the production sample, each 6 runs, 18 stills and 6
then/now pairs. `before*.json` is the parent `1a7d2759`, `measurements*.json` is this row, and
`*_production_sample.json` is the production sample. Production results are committed as counts and aggregates
only (`--counts-only`, decisions/PROD-SAMPLE-counts-only.md): no run names, file names, hashes or per-item records.

## Results

Pixel change on each surface on the parent, committed chain against the lossless chain. Each cell gives the
median, then the maximum, over the measured stills.

| Surface (dev / production sample) | Mean channel error | Max channel error | PSNR dB | Pixels off by ≥3 |
|---|---|---|---|---|
| Reel still (after x264) | 1.86 / 1.82, max 2.15 / 2.45 | 34 / 29, max 39 / 55 | 39.8 / 40.0 | 41 % / 40 % |
| 9:16 photo ground (stat card, callout, hook) | 0.093 / 0.097, max 0.80 | 5 / 7.5, max 28 | 57.9 / 57.5 | 0.09 % / 0.46 %, max 8.8 % |
| 16:9 stat ground (blurred) | 0.011 / 0.010 | 1, max 3 | 67.9 / 68.1 | 0 % |
| Then-and-now | 0.23 / 0.021, max 0.26 | 21.5 / 7, max 25 | 51.2 / 66.2 | 4.0 % / 0.1 %, max 5.2 % |

On this row, every surface differs by 0 on all 48 stills and pairs in both sets.

- **Layer formats.** On the parent, all 36 layers were a q92/q95 JPEG 4:2:0 generation. On this row, 24 layers
  are the source's own JPEG bytes (13 dev, 11 production; they report the source's 4:2:0 coding) and 12 are PNG.
- **Only the JPEG loss changes.** This row's surface hashes equal the parent's lossless reference, bit for bit, on
  every surface in both sets. For example, dev `reel_still` is 9328e7082a727e87. Production hashes were compared on the local copy and are not
  committed.
  Keeping the source bytes therefore gives exactly the pixels a PNG download would. The grades, crops, colour
  balance and the QF-01/02 BT.709 / 16-bit path are otherwise unchanged.
- **Reel colour tags are unchanged.** They read yuv420p, bt709/bt709/bt709 and tv range on both commits.
- **The Reel-still error includes the encoder.** It is measured after x264 (crf 14), whose response to the changed
  input makes it larger than the photo-ground error. The photo ground is taken before any encode.

Boards: `boards/before_*.jpg` (parent) and `boards/after_*.jpg` (this row). Each shows the most detailed 600 x 600
window of the 9:16 photo ground: committed, lossless and |difference| x 32, saved at half size. On the parent the
difference shows block and chroma noise. On this row it is black.

## Disk and time

| Item | Parent | This row |
|---|---|---|
| Disk left by the chain, 18 stills + 6 pairs, dev | 119.0 MB | 191.7 MB (1.6x) |
| Same, production sample | 113.1 MB | 218.5 MB (1.9x) |
| Downloads only, dev / prod | 64.7 / 59.8 MB | 64.7 / 59.8 MB (1.0x) |
| Then-and-now downloads, dev / prod | 33.8 / 20.9 MB | 33.8 / 20.9 MB (1.0x) |
| Upscaled, dev / prod | 9.5 / 11.0 MB | 41.6 / 47.2 MB (4.4x / 4.3x) |
| Colour balance, dev / prod | 8.2 / 16.3 MB | 39.6 / 79.1 MB (4.9x) |
| 24 pool photos stored, dev / prod | 66.1 / 71.1 MB (q92) | 66.2 / 71.1 MB: all 24 kept as source bytes (PNG would be 334 / 371 MB) |
| Image save time, dev / prod | 32 / 19 s | 75 / 136 s (7 / 10 PNG files) |

Times come from a host shared by four measurements, so they are inflated; the ratios are the useful part.

**Projection.**

- **Pool.** New pool and beat downloads cost the source's own bytes, about 1.0x the parent's q92 file, instead of
  5x as in build 4. A fresh dev pool stays at about 1.4 GB and the production sample's at about 0.74 GB.
- **Derived copies.** These grow about 4.5x for each newly prepared photo: upscaled, colour balance, privacy and
  crops.
- **Essential photos** (`preserve_source=True`) also keep `NN_commons.jpg.source`, the original bytes with
  metadata, as before. A kept source therefore occupies about 2x: the stripped copy plus the original.

**PNG level.** Pillow's default level is kept for the derived files. The pixels are the same at every level;
level 1 saves about 2.4x faster but writes 50 % more bytes.

## Cache (one-off growth, no deletion here)

The cache keys are unchanged, but the derived file names change from `.jpg` to `.png`: `cache/upscaled`,
`cache/privacy`, `cache/colour_balance`, `cache/text_crop` and the per-run detail crops.

- **Every prepared photo is made once more on its first use after landing.** The old JPEG entries are not deleted
  by this row.
- **Dev holds about 1.2 GB of such entries** (upscaled 724 MB, privacy 442 MB, text_crop 42 MB, colour_balance
  8 MB). Remade at about 4.5x, they would add about 5.5 GB next to the old 1.2 GB if every one were used again.
  The growth is bounded by the photos future runs actually prepare.
- **The first remake repeats a few looks.** Verdicts are cached by file hash, so the first remake pays these again:
  the second look at a remade crop, the fit looks of renamed files, and the privacy cloud check where
  `FACE_VERIFY_CLOUD` or `_AMBIGUOUS` is on (both off by default).

**Per-run disk check.** `src/ops.py` `PreFlightHealthChecker.check_disk_space` runs before each run and refuses to
start below 5 GB free. A full disk therefore stops new runs before any spend; it does not fail one halfway. A
retention rule for these caches is proposed as its own row (queue QF-100, image cache retention).

## Other changes and limits

- **Operator uploads** keep their source bytes when they are an upright RGB or greyscale JPEG. Their metadata is
  stripped, including a phone photo's GPS position, and anything appended after the image (a motion-photo video) is
  dropped; otherwise they are decoded once to PNG.
- **Covers are unchanged** (`visual_engine` keeps the parent's YCbCr q95 encoding). The RGB 4:4:4 covers of build 4
  pushed 13/32 dev and 19/34 production covers over YouTube's 2 MB thumbnail limit.
- **Cover hue.** `measure_covers.py <root> [<data root>]` draws each real run's cover from that commit's prepared
  hero and measures hue against the lossless hero. The hero is the recorded recipe hero where one exists, else the
  run still best correlated with the delivered cover. Results are in `covers_*.json`.

  | | Parent | This row |
  |---|---|---|
  | Fixture (the colour-v5 test's hero), against the lossless hero | 3.96 deg | 4.62 deg |
  | Fixture, against the hero the cover was drawn from | 0.57 deg | 4.62 deg |
  | Dev, 30 covers: patch median / max | 0.23 / 4.17 deg | 0.21 / 3.21 deg |
  | Production sample, 34 covers: patch median / max | 0.44 / 4.00 deg | 0.44 / 3.21 deg |
  | Full frame: covers worse than the parent by more than 0.1 deg | | 0 of 64 (largest +0.10) |

  - **Patch comparison.** 22 dev and 29 production covers are within 0.1 deg; 5 dev and 1 production are
    better by more than 0.1 deg.
  - **Three covers are worse on the patch:** 1 dev cover (Wieliczka 01), +0.24 deg (0.16 -> 0.40), and 2
    production-sample covers, each by ≤ +0.24 deg (counts only). On the full frame all three improve; the dev
    cover from 7.09 to 6.86 deg.
  - **The dark patch quantises.** On a dark, low-saturation patch the median hue jumps between 8-bit YCbCr
    rounding levels, and a hero already through a JPEG round trip can land on a nearer level.
  - **The cover hue test's limit is now 5.0 deg** (branch-only test; decisions/QF-13.md, option 1). That is this
    row's fixture shift, rounded up to 0.5 deg. QF-101 (cover encoding) restores 2.0 deg.
- **The legacy JPEG branches** keep their old settings: `commons.download_jpeg` with a `.jpg` destination, and the
  illustration composites given a `.jpg` path. No pipeline caller uses them.
- **Privacy.** None of the 36 real stills measured had a privacy copy in its run, so the blur's serialization is
  measured on the fixture and the regression test only.
- **Then-and-now.** No measured pair registered by geometry (0 of 12), so the pairs were measured as the downloaded
  candidates. Registration is covered by the regression test.
