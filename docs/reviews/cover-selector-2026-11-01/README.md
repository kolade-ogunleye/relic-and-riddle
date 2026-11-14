# Cover legibility, hygiene and selection — 1 November 2026

Follow-up to [the cover refinement](../cover-refinement-2026-11-01/README.md). Branch
`claude/intelligent-carson-ybsccd`.

**Inputs.** The original Commons photographs could not be fetched from the cloud
review environment (Wikimedia's robot policy refused its shared address), so these
boards use clean photo regions cropped from the committed refinement covers as
stand-in sources. Framing is therefore tighter than on the real photographs; the
type, contrast and layout behaviour is the same code path. Re-render on the real
sources before judging crops.

## What changed

| Change | Evidence | Test |
|---|---|---|
| Masthead always bone with the solved ink halo on photographs (was a mean-luminance ink/bone switch with the halo disabled) | [masthead_contrast_before_after.jpg](masthead_contrast_before_after.jpg): Derinkuyu rock 2.49:1 → 4.96:1 | `tests/test_cover_brand_legibility_2026_11_01.py` (≥ 4.5:1 on white, sand, mid-grey, sky, black, noise, 1 px stripes) |
| Masthead 40 px Cinzel Bold, led by the gold diamond mark | [masthead_size_and_mark.jpg](masthead_size_and_mark.jpg) | same file, `brand_mark` placement |
| Immersive backdrop: streak-free ambient field instead of repeated edge columns | [streaks_before.jpg](streaks_before.jpg) / [streaks_after.jpg](streaks_after.jpg); banding 15.2 → 3.5 | `tests/test_cover_ambient_2026_11_01.py` (fails on the old clamp) |
| Provider caption strips cropped from sources (`src/photo_hygiene.py`), no model needed | Richat "Copernicus Sentinel data…" bar found; also on the shipped story poll frame; no raw-photo false positives in 573 committed images | `tests/test_caption_strip_2026_11_01.py` |
| `monument` cover style replaces `lower_third` in the rotation | [lineup.jpg](lineup.jpg) vs [lineup_before_monument.jpg](lineup_before_monument.jpg) | `tests/test_cover_monument_2026_11_01.py` + every `COVER_STYLES` gate |
| Cover style chosen by `src/cover_selector.py`, compared in the weekly digest | — | `tests/test_cover_selector_2026_11_01.py`, `tests/test_cover_style_audience_2026_11_01.py` |

## Selection rules

1. Fit: `monument` only when the copy has a measured figure or a year.
2. Neighbours differ: never the previous run's style while another fits.
3. Explore: each fitting style gets `MIN_TRIES` (3) measured Instagram Reels first.
4. Learn: Thompson sampling on a topic-independent reward — mean of (1 − 3-second
   skip rate) and (shares + saves per account reached ÷ 0.03, capped at 1). Seeded
   by run, so a resumed run picks the same style.

The weekly digest prints each style's median skip, share, save and watch rates,
marked an early signal until every style has 8 Reels. Retire the weakest style only
after that.

## Not changed, deliberately

- The dimmed photograph continuing outside the profile square on Cinema and Evidence
  plate: it is the approved "quiet continuation" and is locked by
  `tests/test_colour_v5_still_grade_2026_10_29.py`; a replacement failed that test.
- The end card (`plain_v4`) and carousel chrome: operator decisions of 2026-10-28/29.

## Validation

Full suite on `883ba30`: 2537 passed, 18 skipped, 0 failed.
