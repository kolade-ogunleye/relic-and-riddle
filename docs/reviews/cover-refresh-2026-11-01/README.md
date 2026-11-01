# Cover refresh — 1 November 2026

Open [the review gallery](index.html) to compare the existing and redesigned
covers at 9:16, 1:1 and 3:4. [The square overview](overview-grid.jpg) shows the
three showcase subjects together. The additional Gol Gumbaz interior is retained
in the gallery as a difficult photographic input.

The October 29 brand overhaul deliberately deferred cover redesign. This change
brings the existing Reel cover variants into the same type hierarchy as the
Stories and carousels: bold Anton headlines, bone and gold text, a small Cinzel
brand mark, plain kickers, and measured contrast. No variants were added.

| Existing choice | New composition | Preserved renderer ID |
| --- | --- | --- |
| A | Evidence plate: photograph over an ink caption panel | `depth_sandwich` |
| B | Discovery: large left-aligned two-tone headline | `badge` |
| C | Cinema: centred bone headline with restrained gold accents | `minimal` |
| Existing rotation style | Editorial: bottom-aligned two-tone headline | `lower_third` |

The A/B/C output filenames and selection behavior remain compatible. The
foreground mask that could obscure letters is removed from the Reel variant.
The separate 16:9 documentary renderer is outside this change.

All text stays inside the central square. The layouts retain the eyebrow, both
title fields and tagline, fit with actual font metrics, and support the existing
tighter redraw. Contrast is measured again on the saved JPEG and recorded in
`still_layout.json`, including on redraw. Photographic shading is neutral;
there is no added colour grade.

## Evidence and validation

Each before/after pair uses identical copy and the exact same photograph.
`inputs.json` freezes the inputs and photo hashes. The initial before renders
were captured before editing the renderer. The added exterior example uses the
original renderer from commit `3616284b6a4562696cfe8c22e91ad47220137367`, with its
source hash in `exterior_baseline.json`.

- All 16 redesigned examples have no measured layout problems or shortened copy.
- Minimum delivered text contrast across those examples: **5.88:1**.
- **37 focused tests passed**: covers, unit conversion, layout/repair, photo hue,
  and the new bright/dark/textured JPEG contrast checks.
- The local gallery was checked in headless Microsoft Edge across 24 combinations
  of subject, design version and crop. All images loaded, there were no script
  errors, and the 390 px mobile view had no horizontal overflow.
- Browser evidence: [desktop](gallery-desktop.png), [mobile](gallery-mobile.png).

Rebuild current-code previews from the repository root, using the project's
installed Python dependencies:

```powershell
python docs/reviews/cover-refresh-2026-11-01/render_evidence.py after
python docs/reviews/cover-refresh-2026-11-01/render_evidence.py gallery
```

The `before` and `add-showcase` phases refuse to overwrite their captured evidence.
`before` must be run against the original renderer when recreating a fresh review;
it is not a command to regenerate historical evidence with current code.

```powershell
python -m pytest -q tests/test_covers.py tests/test_cover_units_2026_10_29.py tests/test_still_layout_2026_10_28.py tests/test_colour_v5_still_grade_2026_10_29.py tests/test_cover_refresh_2026_11_01.py
```

This is development-only rendering. No bot, publication, production operation,
paid provider call, environment setting or spending setting was changed.

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
