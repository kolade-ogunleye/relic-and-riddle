# Face-privacy blur: verified boxes, face proportions, soft focus (2026-10-28)

**Found by:** production Amber Room, run_amber_room_heist_1793042462 beat 7 (`production_qa_07_before.jpg`): a flat
grey oval on the mirror beside a gilded column. The release gate blocked it, and the regeneration reused the same
photo (`cache/privacy/08_commons_858dcae…jpg`, from `assets/amber_room_heist/08_commons.jpg`). The dev audit added
Roman concrete photo 19 (`cache/privacy/19_commons_2e369ae7f2cb042e.jpg`): a row of tall grey ellipses that the video
judge called "fingers blocking the lens".

**Causes, all in `src/privacy.py`:**

1. Gemini's face boxes were accepted without the check YuNet boxes get. The Amber Room box covers a plain mirror.
2. Gemini returned head-and-body boxes (h/w 1.9 to 2.8, up to 7 once padded) that were blurred whole.
3. The blur's Gaussian kernel was as large as the box, with sigma = kernel / 2, so the patch averaged to one flat
   colour. The feather was capped at 15 px, which left a hard rim on a 300 px box.
4. The cache key was path + size + mtime only, so a fixed blur never replaced an old copy. When Gemini timed out,
   the YuNet-only answer was cached and reused from then on.

**Fix:**

- Every candidate, from Gemini or YuNet, is narrowed to face proportions (`_face_geometry`: h/w 0.6 to 1.6, a tall
  box keeps its top 1.3·w, a box over 8% of the image is dropped). It is then verified by
  `_is_living_human_candidate`. A detector box on a face Gemini already proposed is one candidate, checked once. Both
  prompts now name gilded or carved ornaments, cherubs, busts, masks, mannequins and plain mirror or glass, and ask
  for the head only.
- Soft-focus blur. The face is resampled to about 6 samples across its shorter side, then smoothed with cubic
  upscaling and a Gaussian of 0.6 cell, so the blur gets stronger as the face gets bigger. The colour gradients are
  kept, and grain as strong as the surrounding photo's (1.5 to 6 grey levels) is added back. The mask is feathered
  over 25% of the box. A core ellipse covering 1.3·w × 1.4·h of the face box is always fully blurred. Padding went
  from 20% to 30%, because a YuNet box stops at the mouth and a white beard showed below it in the first render.
- A failed Gemini call now returns `None` rather than `[]`. In that case `blur_human_faces` writes
  `<stem>_<key>_partial.jpg`, which is never read back as a cache. The cache key adds `ALGORITHM_VERSION`
  (`v9_verified_soft`) and `GEMINI_MODEL`.

## Measurements (`measurements.json`, made by `render_evidence.py`)

No model was called. The old boxes were recovered from the old cached blurs and stand in for Gemini's answer (24 on
Roman 19, 1 on Amber 08). YuNet ran locally. The verifier was assumed to accept every Roman candidate (the worst case
for looks). On Amber it was assumed to accept only the old mirror box and to refuse YuNet's 5 gilded busts and
cherubs, so the render shows what that blur would look like. In the real pipeline the verifier is expected to refuse
the mirror box as well, which `test_amber_room_mirror_box_is_not_blurred_when_the_verifier_refuses` covers.

| Photo | Blurred areas | Max h/w | Min pixel std inside | Median texture std inside |
|---|---|---|---|---|
| Roman 19, before | 24 | **7.08** | 1.28 | 0.38 |
| Roman 19, after | 28 (YuNet added 4, including the foreground woman and a man by the fountain) | **1.45** | 5.04 | 3.75 |
| Amber 08, before | 1 | 1.55 | 10.8 | **0.32** (flat) |
| Amber 08, after (if verified) | 1 | 1.55 | 11.1 | **1.52** |

Texture std is the standard deviation left after removing a sigma-3 blur, measured inside the inner third of the
ellipse. A flat disc scores near 0.

Synthetic face (`synthetic_face_before.jpg` / `_after.jpg`), measured on the face box:

- Feature-scale energy (band-pass, sigma 2 to 8): **238.2 → 1.6**, 149 times lower. The eyes, brows, nose and mouth
  are gone.
- Laplacian energy: 1107 → 432. The remainder is the added grain, which carries no face detail.

## Files (all half-size JPEGs)

- `production_qa_07_before.jpg`: the production frame that was blocked.
- `amber_room_08_before.jpg` / `_after.jpg`: the whole photo. `amber_room_08_zoom_{original,before,after}.jpg`: the
  mirror. Before it is a grey oval; after it is soft focus that keeps the mirror's tone and the candle.
- `roman_concrete_19_before.jpg` / `_after.jpg`, and `roman_concrete_19_zoom_{original,before,after}.jpg`. Before
  is a row of tall grey ellipses; after, heads are blurred at head size and the bodies are left as they are.
- `synthetic_face_before.jpg` / `_after.jpg`.

**Tests:** `tests/test_privacy_blur_2026_10_28.py` (14 tests).

**Not covered here:** when the face check fails, the regeneration reuses the same photo. That decision is made in
the release and regeneration code (`main.py` and `src/release.py`), not in this module.
