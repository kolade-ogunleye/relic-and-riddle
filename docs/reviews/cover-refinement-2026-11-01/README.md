# Cover refinement — 1 November 2026

This is the second design pass. The comparison baseline is the approved bold-type
redesign in [the first review](../cover-refresh-2026-11-01/index.html), rather than
the older serif/pill designs. The same four existing styles remain: A / Evidence
plate, B / Discovery, C / Cinema, and the editorial rotation layout.

Open [the comparison gallery](index.html) to switch subjects, compare prior and
refined versions, inspect 9:16 / 1:1 / 3:4 crops, and view the covers at exactly
180 CSS pixels wide. The source-photo link helps judge the crop against the full
photograph. Click any cover for its full-resolution JPEG.

- [Square overview](overview-grid.jpg)
- [Full-frame overview](overview.jpg)
- [180 px comparison board](phone-comparison.jpg) — open at original size
- [Measured layouts and contrast](measurements.json)
- [Browser validation](gallery-validation.json)

## What changed

The first redesign corrected the typography, but still discarded important
photographic context by filling a 9:16 canvas before taking the profile crop.
This pass composes from the original photograph and plans the image and type
together inside the central square.

- **A / Evidence plate:** a warm bone-paper print, dark Anton headlines, a complete
  inset photograph, and a fine divider. It no longer places a heavy black panel
  over the feature being shown.
- **B / Discovery:** immersive photographic context, a dominant gold headline,
  and a protected feature above the text. The dome and the eye remain visible.
- **C / Cinema:** a centred title above a complete photographic plate, creating a
  different reading order and silhouette from the lower-title designs.
- **Editorial rotation:** a quieter bone headline over the photographic context.

Supporting text is now 36–38 px at design size, with type fitting for longer
copy. Headlines retain Anton. All text, source images and protected photographic
regions have measured geometry in the saved layout and redraw recipe.

### Reviewed framing and conservative fallback

`data/cover_art_direction.json` holds three manually reviewed normalized feature
regions: Richat's eye, Gol Gumbaz's central dome, and Derinkuyu's stone door and
lit passage. Each record is tied to the exact source file's SHA-256. Discovery
and Editorial can scale that region into their reserved image area while
retaining surrounding photographic context. Text and the shading behind it stay
outside the reviewed feature. Edge colours extend into the remaining vertical
canvas; no new photographic content is generated.

This is explicit art direction, not automatic subject detection. A different,
changed, cropped or unreviewed photograph uses whole-image containment. A and C
always keep the complete source. Redraw recipes preserve the original reviewed
framing and verify its photo hash before reusing it. No new provider dependency
or cost is introduced.

## Validation

- **49 focused tests passed**: existing cover/format contracts, spoken units,
  layout and redraw, photo hue, delivered JPEG contrast, landscape and portrait
  source containment, reviewed-feature colour/clearance, and stale-coordinate
  rejection. Network connections are blocked by the composition tests.
- All **16 final examples** have zero measured layout problems and no shortened
  copy. Exact delivered JPEG ratios and recipes are in `measurements.json`.
- An additional long-copy stress render kept all four styles within their safe
  geometry; extreme titles were shortened with an ellipsis and explicitly
  recorded in the layout.
- Headless Edge checked **48 subject/version/crop/scale combinations**, all 16
  comparison pairs, actual 180 px image widths, and responsive layouts at
  390/768/1440 px. All images loaded, with no script errors or page overflow.

```powershell
python -m pytest -q tests/test_covers.py tests/test_cover_units_2026_10_29.py tests/test_still_layout_2026_10_28.py tests/test_colour_v5_still_grade_2026_10_29.py tests/test_cover_refresh_2026_11_01.py tests/test_cover_composition_2026_11_01.py
```

These checks establish rendering correctness and crop/contrast behavior. They do
not establish click-through performance or a universal aesthetic ceiling.

## Reproduction

`before/` contains byte-for-byte copies of the first pass. `baseline.json` records
all 48 copied file hashes and every render checks they are unchanged.
`inputs.json` preserves the exact photos, photo hashes and script payloads from
the first review. The Gol Gumbaz interior remains available as a difficult input;
the main overview uses its exterior.

```powershell
python docs/reviews/cover-refinement-2026-11-01/render_evidence.py after
python docs/reviews/cover-refinement-2026-11-01/render_evidence.py gallery
python docs/reviews/cover-refinement-2026-11-01/check_gallery.py
```

These are local image and review-page operations. They do not run a bot, call a
provider, post anything, or change environment or spending settings. The browser
check uses the installed Microsoft Edge in headless mode.

## Photo credits


Attribution below is copied from the project's existing `credits.json` records.
All review images are adapted by cropping, shading and adding typography. The
interior study also uses the project's existing upscaled derivative.

- Eye of the Sahara: **SentinelHub**, [source photograph](https://commons.wikimedia.org/wiki/File:Eye_of_the_Sahara_(49141999011).png),
  [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
- Gol Gumbaz exterior: **Pinakpani**, [source photograph](https://commons.wikimedia.org/wiki/File:Gol_Gumbaz,_the_mausoleum_of_Mohammed_Adil_Shah,_in_Vijaypura_12.jpg),
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Gol Gumbaz interior: **Pinakpani**, [source photograph](https://commons.wikimedia.org/wiki/File:Interior_of_Gol_Gumbaz_building_in_Vijaypura,_Karnataka_10.jpg),
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Derinkuyu: **Ahmet KAYNARPUNAR**, [source photograph](https://commons.wikimedia.org/wiki/File:DERINKUYU_UNDERGROUND_CITY_-_panoramio.jpg),
  [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). The adapted
  Derinkuyu review images are offered under that same licence.
