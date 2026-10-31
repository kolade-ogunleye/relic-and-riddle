# Audience chart, 29 Oct 2026

`src/audience_chart.py` draws the chart Telegram sends for the audience: views 7 days after posting for the
last six Reels, one row per Reel with the newest on top, and one marker per platform. The design comes from
the critic's round 4 chart (`C:\tmp\visual_review\critic_round4\final_chart.png`). Tests are in
`tests/test_audience_chart_2026_10_29.py` (36 pass).

## Data

- Only the stored `audience_metrics` rows at snapshot `7d` are drawn. The Reel list, titles, formats and
  posting order come from `audience.dashboard()`. Documentaries (`deep_dive`) are left out. A Reel younger
  than 7 days does not appear yet.
- The development database has **0** `audience_metrics` rows. `dev_db_half.jpg` shows the real path,
  `chart_for(PipelineDatabase())`, which draws the "No audience data yet" state.
- The module contains no sample values. `sample=True` only adds a watermark, twice: "SAMPLE DATA" in coral
  in the kicker and "SAMPLE DATA, NOT AUDIENCE FIGURES" in the footer. Every render here except `empty` and
  `dev_db` uses sample values supplied by `render_evidence.py`: the critic's round 4 numbers, with titles
  from the topic bank.
- The views are read directly, so a real 0 stays 0. `audience.derived()` computes
  `views or fb_reels_total_plays or plays`, which turns a stored 0 into None (a gap). That finding is
  reported here and left unchanged in `audience.py`.

## Headline

`headline()` counts the leaders from the rows. A Reel is compared only when two or more platforms have a
figure. A tie, or all zeros, has no leader. Examples: "Instagram leads on every Reel", "Instagram leads on 5
of 6 Reels", "Instagram and YouTube lead 1 Reel each", "No platform leads yet", "Only Instagram has figures
yet". The Telegram caption is the headline as a sentence (`chart_for()["caption"]`).

## Changes from the critic's render

| | critic round 4 | this chart |
|---|---|---|
| smallest text at the 400 px preview | 8.7 px (26 px footer on 1200) | **12.0 px** (36 px on 1200) |
| row label | "REEL 6" | the Reel's real title, full width, cut at a word with an ellipsis by measured width |
| figures | the newest Reel only | every row: marker and figure per platform, right-aligned (an axis alone cannot be read at 400 px) |
| missing platform | not handled | no key entry; "No Facebook figures yet." in the subtitle; an em dash in a row with a gap |
| large numbers | separators | 3,120 / 12.5K / 1.28M / 2.4B; axis labels in a single unit (2.5K ... 10K, never 7,500 next to 10K) |
| ticks | fixed 0 to 3,000 | whole-number steps of 1, 2, 2.5 or 5 x 10^n with the lowest top (4,100 gives 0 / 2,500 / 5,000); half as many ticks if the labels would collide |
| emoji in a title | n/a | dropped (`typography.drawable`), never drawn as a missing-glyph box |

## Measurements (`measurements.json`)

- Contrast of every text on ink: the lowest is **6.48:1** (coral). Bone, stone, gold and teal are all
  higher. The test checks the pixels of the rendered image, not only the specified colours, and it fails
  when a text is dimmed to (52, 56, 62).
- CVD separation (CIE76 delta E, the Machado 2009 matrices the critic's `cvd.json` used). These numbers
  match the critic's exactly:

  | vision | IG~YT | IG~FB | YT~FB |
  |---|---|---|---|
  | normal | 51.3 | 93.4 | 85.0 |
  | protan | 51.3 | 85.3 | 40.3 |
  | deutan | 34.0 | 88.9 | 57.0 |
  | tritan | 32.8 | 77.2 | 106.3 |

  The shape also carries the series: a circle for Instagram, a triangle for YouTube and a square for Facebook.

## Fonts

Figures use IBM Plex Mono SemiBold and Medium once `fonts/IBMPlexMono-*.ttf` is in the repo. Another change
adds the files. Until then the figures fall back to Montserrat SemiBold (`six_reels_fallback_font_half.jpg`).
The renders here used the critic's Plex files from `C:\tmp\visual_review\critic_round4\fonts`, passed as
`mono_dir`. No font was copied into the repo.

## Files

`<case>_half.jpg` is half size (600 px). `<case>_preview400.png` is the Telegram preview width. The cases
are: `empty`, `one_reel`, `six_reels`, `missing_facebook`, `mixed_leaders_gaps` (ties, zeros, single
platform, gaps), `large_numbers_long_titles`, `six_reels_fallback_font`, `dev_db`.

Regenerate:
`.venv/Scripts/python.exe docs/reviews/audience-chart-2026-10-29/render_evidence.py C:/tmp/visual_review/critic_round4/fonts`
