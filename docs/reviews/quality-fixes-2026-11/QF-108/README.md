# QF-108: a spliced photo beat takes the master's photo grade

Follow-up to the QF-86 port review (non-blocking 1): the micro-patch (`VideoComposer.splice_single_beat`) rendered
a replacement still through the shared camera with no grade, and a replacement clip with no grade, so a photo
spliced into a warm-bias Reel was cooler than the beats around it.

## Fix

`splice_single_beat` takes `grade_bias` (the bias the master was graded with: compose_video's `grade_bias`, the
run's `art_direction.grade_bias`; default 0.0, compose_video's own default) and applies the master's own rules:

- a still that `is_photo_still` and is not `is_illustration` gets `film_grade_filter(grade_bias)` on the camera's
  moved frame (`still_camera.render_clip(grade=...)`, the chain compose_video's stills take);
- a clip that `is_photo_clip` (a parallax or AI-motion clip of a photo) gets compose_video's `clip_in`:
  `scale=in_color_matrix=<source>,format=gbrp16le,<photo grade>,RGB_TO_BT709`;
- illustrations, graphics, hook frames, footage, clips without a verified lineage and stills with a broken chain
  keep their own colour (fail closed, as in the master). Nothing else in the splice changes.

## Measurement (measure.py, production encoder libx264 slow crf 18)

`python docs/reviews/quality-fixes-2026-11/QF-108/measure.py <repo root>`; base e49616fc -> before.json, this
commit -> after.json. Real run photos (QF-86/inputs, committed, hash-checked) at their runs' real grade biases; a
master of the photo on three 1 s beats, its middle beat spliced with the same photo. Lens off in both and both on
the splice's camera move, so the spliced beat compares pixel for pixel with the beat the master drew. Worst of
t 1.2 / 1.5 / 1.9 s; CIE76 median over every 4th pixel; red-minus-blue in 8-bit codes.

| Case | Bias | CIE76 median before -> after | red-minus-blue shift before -> after | encoder floor (neighbours) after |
|---|---|---|---|---|
| Gol Gumbaz | +0.006 | 1.237 -> 1.064 | -0.612 -> -0.041 | 0.854 |
| Roman concrete | +0.009 | 1.482 -> 1.057 | -1.956 -> -0.04 | 0.874 |
| Aswan | +0.036 | 2.866 -> 1.04 | -6.336 -> 0.023 | 0.791 |
| Wieliczka | +0.041 | 1.506 -> 0.933 | -3.833 -> -0.018 | 0.933 |
| Richat (warmest run) | +0.05 | 5.159 -> 1.044 | -11.82 -> 0.109 | 0.801 |
| Aswan, cool bound | -0.05 | 3.334 -> 0.785 | 8.289 -> -0.002 | 0.71 |
| Richat, 16:9 | +0.05 | 5.257 -> 1.099 | -12.198 -> 0.126 | 0.992 |
| Gol Gumbaz parallax clip | +0.006 | 1.142 -> 0.375 | -0.535 -> -0.007 | 0.391 |
| Illustration (gap_gen_) | +0.05 | 1.013 -> 1.013 | 1.123 -> 1.123 | 0.754 |

- Acceptance: worst photo case CIE76 median 5.257 -> 1.099, at the encoder's floor (the untouched neighbours, which
  the patch re-encodes, are 0.71-0.992 away from the master); worst red-minus-blue shift 12.198 -> 0.126 codes.
- The illustration's spliced beat is pixel-identical on both trees (sha256 4fc82bf6ae57...): it is never graded.
  Its residual 1.0 / 1.1 is the encode alone (two independent encodes of the same ungraded picture).
- `cut` in the JSON is the step a viewer sees at the cut (t 0.95 -> 1.05 in the patched master): red-minus-blue
  Richat -11.376 -> 0.508, Aswan -6.468 -> -0.111, cool bound 8.106 -> -0.178. It includes the camera move between
  the two beats (a different crop), so the per-beat comparison above is the clean measure.

## Renders (render.py)

`python render.py <repo root> <out> before|after`: the worst cases (Richat +0.05 in 9:16 and 16:9; Aswan at the cool
bound), half-size panels: the last frame before the cut, the first frame after it (the spliced beat), and the
master's own beat. `renders/before_*` on e49616fc, `renders/after_*` on this commit; JPEG <= 300,000 bytes.

## Limits

- The splice has no caller in main.py or the bot today; a future caller must pass the run's
  `art_direction.grade_bias` (the default 0.0 gives the neutral photo curve, compose_video's default).
- Out of scope, unchanged: the spliced beat does not get the master's lens vignette or burned captions (the master's
  neighbours carry them); an essential full image (`image_presentation.full_image`) is spliced through the camera
  rather than fitted whole as the master does.
