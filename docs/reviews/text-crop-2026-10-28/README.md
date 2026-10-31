# Burned-in text: crop the edge band instead of dropping the photo (2026-10-28)

**Found by:** the late video judge's finding "Museum accession/catalog number label is visible at the bottom of the
frame". It blocked production Richat (3 runs) and Hal Saflieni, and development Trepanation
(run_trepanation_miracle_1792995462, `dev_qa_03_before.jpg`, photo 14: Wellcome L0035690, card "A650847").

**Causes, all in `src/beat_images.py`:**

1. The screen dropped the whole photo even when the text was a strip it could crop: a label under a specimen, a logo
   band, a scale bar.
2. When dropping would leave fewer than `MIN_SCENE_IMAGES`, it kept every labelled photo, so the late judge blocked
   them.
3. The screen looked at 512 px and its prompt did not name labels or accession numbers. Trepanation photo 14 was
   recorded as `text: false`.
4. The single `reason` field described the subject. A photo set aside for its label was logged as "does not show the
   subject (burned-in text: The photograph displays a human skull…)", and `.picture_text.json` kept that sentence.

**Fix:**

- `PictureVerdict` has new fields: `text_reason` (what the text is and where), `text_edge`
  (top/bottom/left/right/inside) and `text_box` ([ymin, xmin, ymax, xmax], 0 to 1000). The prompt names museum
  labels, placards, specimen tags and accession or catalogue numbers. It also says an inscription that is part of the
  subject is not printed text. `picture_shows` and the beat-download log report the text reason.
- `without_burned_in_text` screens at 1024 px (`TEXT_SCREEN_SIZE`). This is the same light-model call, with roughly
  four times the image tokens. Cached verdicts now carry `screen: 2`, so older verdicts are looked at again once. If
  the new look cannot run, the older verdict stands.
- If the text lies in an edge band, `crop_text_band` cuts that band away. The cut is the box plus a 1.5% margin, and
  at most 15% of the side (Trepanation's card starts 87.1% of the way down, so 12% would not reach it). The crop is
  refused if it would take a photo that met the found-photo bar (`commons.MIN_PIXELS`, `commons.MIN_HEIGHT` = 1000 px)
  below it. Upscaling for the 1080x1920 frame still happens later, in `upscale.ensure_all`. The crop is looked at
  again and used only if its text is gone; otherwise the photo is set aside as before.
- The copy keeps the original's file name, in its own folder: `cache/text_crop/<sha256(source|edge|px)[:16]>/<name>`.
  An approved storyboard (matched by name) and the strong-photo list (matched by stem prefix) therefore still match it.
  `record_derived(..., "text_crop", {edge, fraction, text_box, pixels})` writes its lineage. The provenance ledger and
  `privacy.is_artwork` follow that lineage to the original's credit record, as they do for upscaled copies (tested).

## Measurements (`measurements.json`, made by `render_evidence.py`; no model call, the text box measured by hand)

| Photo | Source | Text box (of 1000) | Cut | Crop |
|---|---|---|---|---|
| Trepanation 14 | 1960x3008 | [871, 322, 940, 597], bottom | 14.4% | 1960x2575, lineage `text_crop` → 14_commons.jpg |

## Files (all half-size JPEGs)

- `dev_qa_03_before.jpg`: the development frame the judge flagged. "A650847" is under the skull.
- `trepanation_14_before.jpg` / `_after.jpg`: the photo, then its crop.
- `trepanation_14_frame_before.jpg` / `_after.jpg`: the centre 1080x1920 cover crop, with the label and then without
  it.

**Tests:** `tests/test_text_crop_2026_10_28.py` (20 tests). `tests/test_beat_images.py` changed in one place only:
its `picture_look` stand-in now accepts the size argument.

**Not covered here:**

- The beat-download path (`_download` → `picture_shows`) still sets aside a found picture that has text. It still
  looks at 512 px; only its log wording changed. It could use the same crop.
- `scene_planner._photo_caption` looks up a caption in the `credits.json` next to the file. A cropped copy gets no
  caption there, the same as upscaled and face-blurred copies already do.
- The fallback when photos run short still keeps photos whose text could not be cropped. Fewer reach it now.
