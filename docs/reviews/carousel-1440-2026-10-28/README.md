# Carousel slides at 1440x1800 (2026-10-28)

The operator asked for the Instagram/Facebook carousel at 1440x1800 in this release. Meta recommends 1440x1800
for 4:5 feed images, and 1440 is the widest the Graph API takes (developers.facebook.com/docs/instagram-platform/
instagram-graph-api/reference/ig-user/media). The ratio stays 4:5, so the layout stays the same and only gets
sharper. Story frames (1080x1920) and covers are unchanged.

## What changed

- `src/multi_format.py`: `CAROUSEL_SIZE = (1440, 1800)`. Every carousel pixel value is still written as the
  design value on the 1080-wide canvas (margins, header, photo heights, gradients, badge padding, strokes, the
  Instagram counter region, the text zone, ensure_legible boxes and feather). Each one is drawn through
  `px = _px(cw)`, which multiplies by `cw / 1080`. Font sizes and stroke widths use `px.exact`, the exact 4/3
  value (FreeType and Pillow accept fractional sizes). Box offsets use `px`, the rounded value.
- Text is fitted on the design canvas (`_fit_text`, `_plan_panel`): the 1080 sizes, floors, widths and line
  counts. Only then are the fonts loaded at 4/3 the size. On the first attempt, fitting at 1440 let fit_text's
  2 px steps and the fonts' hinting choose different sizes and line breaks. One cover heading fitted 920 px on
  one line at 1080 and wrapped to two at 1440 (cycle 1, below).
- Photos are cover-cropped straight from the file the pipeline hands the carousel (`_fill`, one Lanczos
  resample). For a low-resolution source that file is Real-ESRGAN's upscaled copy. No 1080 intermediate is
  made.
- JPEG: quality 97, 4:4:4 chroma, optimised Huffman tables (`CAROUSEL_JPEG`, the same for `redraw_carousel_slide`).
- `src/video_qa.py`: the carousel audit accepts 1440x1800 (and still 1080x1350). Its 12 px edge strips scale
  with the width. `src/safe_zones.py`: docstring only.
- `still_layout` is unchanged. Every record carries its own `size`, `zone` and `protected` boxes, and the
  check uses them. Alt text, credits and Facebook's photo pick read only the recipe, problems and shortened
  lists, never box coordinates.

## Measurements

The renders are in `measurements.json` (per slide) and `before/` and `after/measurements.json`. They use the
production scripts of Roman concrete and Nazca, the two topics of `carousel-content-2026-10-28`, with photos
in /photos sheet order.

| | before (1080) | after (1440) |
|---|---|---|
| slide size | 1080x1350 | 1440x1800 (all 28) |
| layout problems (`still_layout.problems`) | 0 | 0 |
| text shortened | 0 | 0 |
| largest file | 0.69 MB | 1.57 MB (Meta's limit is 8 MB) |
| mean file | 0.44 MB | 1.06 MB |
| fewest source px per slide px (photo) | 1.92 | 1.44 (no photo is enlarged) |
| boxes vs before, in design px | | x within 10 px (line lengths), y within 2 px |
| Instagram counter region | 800,0,1080,140 | 1067,0,1440,187 (the same in design px) |

The Facebook copies are byte-identical to the Instagram copies: these cards have no "swipe" wording to
change. That is why the side-by-side sheets show one copy per topic.

Regression check with synthetic cards (`dump_layouts.py`). The run covers 7 cards (accented, long and stat
headings among them), every archetype, normal and tight, and both copies: 196 slides.

- At 1080x1350, the new renderer's records and pixels match the renderer before the change on all 196
  slides (0 layout differences, 0 pixel differences).
- At 1440x1800, all 196 slides have 0 problems and the same shortened lists. Every box is the 1080 box x 4/3,
  with vertical differences of 3 px or less (median 1) and horizontal ones of 15 px or less (median 2). The
  horizontal difference is line length: FreeType rounds each glyph's advance to whole pixels at either size.

## Cycles

1. **4/3 scale fitted on the canvas, q95 4:2:0.** No layout problems. The covers differed from the 1080 layout
   by up to 375 px: a heading that fitted one line at 1080 wrapped at 1440. The fix: fit on the design canvas
   and draw at 4/3 (above). After the fix the boxes matched within 15 px horizontally and 3 px vertically.
2. **Fonts and strokes at exact 4/3.** One comparison body line was 9 px longer than 4/3 of its 1080 length.
   Exact font sizes (50.67 rather than 51) did not shorten it, because FreeType's hinted advances come out
   the same. The exact sizes are kept because they are correct, but the remaining difference is hinting,
   which no size change removes. Strokes at exact 4/3 (2 becomes 2.67 rather than 3) keep the outline's
   weight relative to the type the same as at 1080.
3. **JPEG** (`jpeg_sweep.py`, `jpeg_sweep.json`, 28 lossless slides). At every quality, 4:2:0 limits the
   type's chroma fidelity to about 38 dB PSNR. `jpeg_chroma_zoom_3x.png` shows it: lossless, then q95 4:2:0
   (darkened fringes on the gold type), then q97 4:4:4 (matches lossless).

   | setting | largest | text chroma (min) | luma (min) |
   |---|---|---|---|
   | q95 4:2:0 | 1.15 MB | 37.9 dB | 43.3 dB |
   | q95 4:4:4 | 1.29 MB | 49.5 dB | 43.3 dB |
   | q97 4:4:4 | 1.64 MB | 50.4 dB | 46.0 dB |
   | q98 4:4:4 | 1.83 MB | 50.9 dB | 47.9 dB |
   | q100 4:4:4 | 2.97 MB | 51.7 dB | 49.4 dB |

   The choice is q97 4:4:4. Above 46 dB the remaining error is below what Instagram's own re-encode adds, and
   q98 or q100 gains no visible change for 12-80% more bytes. That is where the cycles stopped.
4. **Photos.** Every slide samples at least 1.44 source px per output px: the pipeline's upscaled copy (Roman
   photos 1 and 6, Nazca 2 and 5) or the original. Nothing is enlarged, so no further gain is available here.

## Sharper text

`crop_100pct_text.png` shows the same heading and body of Roman slide 3 at the same physical size. On the left
is the 1080 slide enlarged 4/3 with Lanczos, which is what a 1440-px-wide screen does with it. On the right is
the 1440 slide at 1:1. The 99th-percentile edge gradient of the text block is 151 at 1440 against 123 for the
enlarged 1080 slide, so edges are 23% steeper.

## Files

- `side_by_side_roman_carousel.jpg`, `side_by_side_nazca_carousel.jpg`: before on the left, after on the
  right, both at 540x675 (half of 1080 wide; the 1440 slide is downscaled to the same display size).
- `before/`, `after/`: the half-size JPEGs of every slide in both copies, with their measurements.
- `render_evidence.py` (renders), `jpeg_sweep.py` (JPEG settings), `compose.py` (sheets and crop),
  `dump_layouts.py` (the 196-slide regression).
