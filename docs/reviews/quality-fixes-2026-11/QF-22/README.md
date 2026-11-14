# QF-22: one shared camera (kenburns)

Review 2026-11-05, section 2 item 5: "Integer-pixel Ken Burns. Push-ins and drifts resize to `int()` sizes and crop at
integer offsets ... Four copies of the same code ... zoompan linear with a hard stop at frame 150. Fix: one shared
`kenburns()` using a float affine transform (BICUBIC) and a smoothstep curve; delete the copies."
Acceptance (STATUS.md): edge-position trace monotone, no integer steps.

## What changed

- `src/camera_motion.py` (new): the one sampler. `camera_window()` gives the source window as floats (never
  rounded); `sample()` resamples it in one separable Pillow pass (`Image.resize` with a fractional `box`, which is an
  axis-aligned float affine and antialiases when it reduces); `kenburns()` is window + sample; a window past the
  picture is drawn over a fill colour or the picture's mirrored edge. Time curves: `smoothstep()` and `ease()`, a cubic
  Hermite that is smoothstep at rest and can keep a speed at one end (the loop seam), limited to stay monotone.
- Every copy now samples through it: `graphics._pushed_in` (stat cards' photo push, the hook card, the timeline
  and design cards' ground drift), `graphics._push_about` (scale hold push, cross-section drift), `GroundDrift`
  (the half-pixel step cache is gone; only an exact repeat is reused), `end_card._pushed` / `_pulled` (end card,
  loop bridge), `then_now.pushed`, the photo callouts' background. Each keeps the filter it used: `_pushed_in`
  bilinear (the stat card and hook card photo grounds, GroundDrift), `_push_about` bicubic, the end card, loop bridge,
  then-and-now and callouts Lanczos; the composer's camera uses bicubic (zoompan's scaler was bicubic too).
- `src/still_camera.py` (new): the composer's stills, the AI hook clip and the micro-patch no longer use FFmpeg
  zoompan (`hook_clip_filter`, `HOOK_CLIP_ZOOM` and `end_card.shot_drift`, its model, are deleted). Each shot keeps
  the extent the old move was written to reach at its last frame (`endpoint_for`) and reaches it on smoothstep, at
  rest at both ends, so the loop bridge carries no push from a still (close_push and open_push 0, which the bridge
  gate also passes more often: below). The AI hook clip alone keeps the old 0.0016/frame from its first frame
  (`lead`, as its zoompan did), and the bridge pre-rolls frame 0 at that speed. Its frames are read into RGB with
  accurate rounding and full chroma interpolation in the clip's own matrix and range (review 1: swscale's default read
  made frame 0 0.77 Y levels darker and 0.145 off in chroma; now exact, tested). The pull and drift presets' sine
  pans (out and back) became a one-way sway of the old peak amplitude (16 / 14 x 8 frame px) on the zoom's curve,
  held to half the picture the window can use on that side at every point of the move (the zoom's margin plus the
  photograph beyond the cover box): a landscape photograph in a 9:16 frame keeps the full sway even on a long pull
  that ends at zoom 1; only a photograph of exactly the frame's shape loses it where the zoom reaches 1 (the old sine
  pan was clamped flat there by zoompan too). Frames are sampled
  from a working copy at most 2x the frame (the old render size) and written to a lossless FFV1 clip in the graph's
  own format (the stills' tone curve, then the BT.709 conversion the graph used to do itself); the clips live in a
  per-render folder that is removed after the graph runs. Before rendering, the free disk is checked against
  1.25 B/px for every camera frame plus 2 GB (refused with the figures when short); a folder or clip a killed render
  left (`*.camera-*`, older than 6 h) is swept at the next compose or splice in that run folder.
- `video_composer.loop_motion` reads the seam from the same camera model with compose_video's exact frame counts,
  and compose_video passes the bridge what the rendered cameras really did (`seam_from_cameras`).
- Cache keys: the master checkpoints (main.py) and the graphics clip cache include `camera_motion.py` /
  `still_camera.py`.

Why the stills end at rest rather than carry 0.0016/frame through the seam: on the real run's photographs the
bridge gate refuses the pre-roll push and falls back to a still bridge (evidence.json, base still reel: push and
open_push 0.0 delivered although 0.0016 was requested; a one-off `check_loop_bridge` on that run's first and last
frames measured the last bridge frame 19.95 from frame 0 with the opening push, limit 8, and 1.29 without it). A
closing shot still moving into that still bridge would stop dead at the narration's end; at rest it cannot.

Curves were kept where a review chose them: then-and-now's push stays linear (freeze panel round 2, T4: "linear
steps"), the callouts' push stays linear ("so loupe_mag holds"), the timeline drift and the stat card push stay
linear ("so no frame is frozen" / "a card never sits static"), the end card keeps its ease-out from the moving last
frame. Smoothstep replaces the composer's linear-with-a-cap moves, the only ones with a stop inside a shot.

## Measurements (`measure.py`, generated inputs; before = 1ab9aded, after = this commit)

Drawn paths: 181 frames of each consumer's own function and time curve on a float soft edge (tanh, 3 px).

| path | repeated / reverse steps | largest step change (jerk) px |
|---|---|---|
| stat card photo push | 149 / 16 -> 0 / 0 | 0.716 -> 0.008 |
| hook card push | 111 / 35 -> 0 / 0 | 0.723 -> 0.013 |
| scale hold push (`_push_about`) | 140 / 23 -> 0 / 0 | 1.296 -> 0.005 |
| timeline ground drift | 165 / 7 -> 0 / 0 | 0.712 -> 0.006 |
| end card push | 0 / 0 -> 0 / 0 | 0.005 -> 0.005 (already sub-pixel) |
| loop bridge pre-roll (z < 1) | 0 / 0 -> 0 / 0 | 0.028 -> 0.028 |
| then-and-now push | 0 / 0 -> 0 / 0 | 0.005 -> 0.005 |
| callouts push | 0 / 0 -> 0 / 0 | 0.006 -> 0.006 |

Composer paths: a real `compose_video` Reel (4 stills, 2.5 s each, loop bridge) and a Reel opening on an AI hook
clip, traced on the delivered H.264 file (16-row band, each shot's middle 80%).

| shot | travel px | repeated / reverse | jerk px |
|---|---|---|---|
| still 0, push (opening) | 0.01 -> 24.2 | 43 / 6 -> 0 / 0 | 0.03 -> 0.10 |
| still 1, pull | 30.7 -> 35.9 | 0 / 3 -> 0 / 0 | 1.13 -> 0.11 |
| still 2, drift | 0.04 -> 32.8 | 43 / 7 -> 0 / 0 | 0.06 -> 0.15 |
| still 3, push (closing) | 0.06 -> 24.2 | 40 / 9 -> 0 / 0 | 0.10 -> 0.19 |
| AI hook clip push | 20.2 -> 21.9 | 0 / 13 -> 0 / 0 | 1.13 -> 0.17 |

`acceptance_met` false -> true; failing paths 9 -> 0. Loop seam record (stills Reel): variant loop_bridge_v1
both; push 0.0016 -> 0.0 and open_push 0.0016 -> 0.0 (the stills end and start at rest); max_step_L 7.93 -> 7.97,
mad_last 0.64 -> 0.90 (gate < 8). A jerk under 0.2 px on the composer traces is the H.264 encode's noise on a static
edge; the base's 0.03-0.10 on push and drift is the noise of a shot that does not move.

A finding the trace exposed: on the base, the push and drift stills did not move at all in the delivered master
(travel 0.01-0.06 px). zoompan's `pzoom` is the previous *input* frame's zoom, and a still is one input frame
expanded to d output frames, so `min(pzoom+0.0016,1.24)` stayed at 1.0016 for the whole shot; only the `on`-based
pull and macro moved, in half-pixel ticks. The loop bridge was meanwhile told the closing shot pushed at 0.0016 a
frame. The extents kept here (`endpoint_for`) are the ones the old expressions were written for and that
`loop_motion` assumed.

## Evidence on real run media (`evidence.py`)

`python evidence.py <base root> <new root> output/run_nazca_lines_runway_paradox_1793247464 <this folder>`: that dev
run's own AI hook clip (motion_01.mp4) and the photographs its parallax clips were made from (beat02_1, 04, 08, 10),
composed 2.5 s a shot by each commit's own compose_video (the path a still takes when it is not a parallax clip),
read only. Motion is read back from the delivered master by tracking corners (Lucas-Kanade, sub-pixel) above the
caption band: per shot, frames of the middle two thirds that do not move (< 0.05 px) and the largest frame-to-frame
change of speed.

| reel, shot | held frames (of 48) | jerk px | mean px/frame |
|---|---|---|---|
| hook reel, AI hook clip | 0 -> 0 | 1.21 -> 0.15 | 0.84 -> 0.65 |
| hook reel, pull (beat02_1) | 0 -> 0 | 0.65 -> 0.11 | 0.55 -> 0.45 |
| hook reel, drift (04) | 48 -> 0 | 0.49 -> 0.12 | 0.03 -> 0.42 |
| hook reel, push closing (08) | 48 -> 0 | 0.01 -> 0.09 | 0.00 -> 0.83 |
| still reel, push opening (beat02_1) | 48 -> 0 | 0.01 -> 0.22 | 0.00 -> 0.51 |
| still reel, pull (04) | 0 -> 0 | 0.46 -> 0.07 | 0.47 -> 0.35 |
| still reel, drift (08) | 48 -> 0 | 0.50 -> 0.15 | 0.02 -> 0.75 |
| still reel, push closing (10) | 48 -> 0 | 0.01 -> 0.09 | 0.00 -> 0.85 |

`trace_<reel>_shot<k>.jpg`: per-frame motion, before (red: zoompan's ticks, or a flat zero where the shot did not
move) against after (blue: a smooth rise and settle). `strip_<reel>_shot<k>_<before|after>.jpg`: the first, middle and last frame of each
shot at half size (540x960, each JPEG <= 300 KB), showing each shot keeps its framing and reaches a comparable extent. Seam records: hook
reel push 0.0016 -> 0.0 (the closing still now ends at rest), open_push 0.0016 both, mad_last 2.96 -> 3.09; still reel
push and open_push 0.0 both (the gate's still fallback on the base, at rest by design here), mad_last 1.22 -> 1.19.

## Tests

- `tests/test_qf22_camera_motion.py`: fails on 1ab9aded (11 of 14: the module is new, and the stat card, anchored
  push in and out and timeline paths hold positions then jump; the callouts case fails there only because
  `callouts.camera_sample` does not exist yet, its frames being already fractional and byte-identical); passes here.
- `tests/test_qf22_still_camera.py`: presets move every frame and settle, seam speeds, old extents, tilt, working
  copy, the FFV1 clip equals the frames the graph would have converted byte for byte, the hook clip path, and
  `loop_motion` equals what the cameras render; review 1's additions: the hook clip keeps frame 0's mean Y/U/V
  within 0.2 (untagged and BT.709; the old read fails it by 1.13 Y), the disk check refuses a render that does not
  fit, stale camera folders are swept and fresh ones kept, and a long pull keeps its sway on a wide photograph.

## Known limits

- Composer camera speed: a still shot at about 0.12 s a frame and the hook clip at 0.13 s (the reviewer's figures;
  0.1-0.2 s on this loaded PC), against about 0.02 s for zoompan. Transient disk: up to 2.04 MB a frame (FFV1 of a
  real photograph at 1080x1920), all of a render's clips at once, so a 60 s Reel of stills needs about 3.7 GB while
  it renders (removed after it; checked before it). It is a rare path: of the 28 dev runs with a master record
  (294 scenes), one shot took it, run_richat_structure_sahara_eye_1793474843's 12_commons.jpg (not an essential full
  image), every other photograph being a parallax clip; it matters most when parallax is unavailable.
- GroundDrift (the timeline, myth panel and design cards' photo ground: 2 users in graphics, 5 in design_cards) now
  resamples every frame instead of every half-pixel step. Measured on a full 1080x1920 ground: about 0.036 s of
  resampling a frame in a quiet profile (bilinear kept for this reason; bicubic was 0.05-0.07), 0.17-0.25 s a frame
  under this PC's load against 0.01-0.06 s on the base, so up to about +30 s per 6 s drifting clip on a busy machine.
- Parallax clips (src/parallax.py, review item 4, its own row) already move on a sub-pixel cv2 remap with smoothstep
  and are not changed here.
- `splice_single_beat` (not called in production) keeps cover-cropping an essential full image as before; QF-52's
  rule there is outside this row.
