# QF-04: beat cuts and act-only dissolves

Reels no longer cycle through zoom, slide and fade-to-black transitions. A scene
boundary is a hard cut on its beat, rounded to the nearest video frame. Explicit
act boundaries alone get an eight-frame dissolve. The deep-dive pipeline passes
its act timestamps through the wrapper, and the render checkpoint includes them.
An act timestamp must coincide with a scene boundary within half a frame; arbitrary
scene boundaries are not promoted to act changes.

## Timing

Every clip is trimmed to its allocated time and starts at zero. Concat and dissolve
inputs share a normalized timebase and frame rate. An act dissolve gets half its
overlap from each adjacent clip; hard cuts have no overlap. This keeps later beats
in place. Default equal scenes divide the narration alone (no extra transition
allowance). The closing loop motion uses the quantized closing boundary and actual
closing overlap. The audio seam filters and programme cut are untouched by this row.

The loop-motion helper retains its legacy default overlap for existing callers;
the composer explicitly supplies the overlap used in the rendered graph.

## Evidence

`python docs/reviews/quality-fixes-2026-11/QF-04/measure.py <repo root>` renders
five generated coloured stills and a generated voice through the actual composer.
It reads the delivered centre pixels frame by frame and records the full filter
graph (machine paths replaced with placeholders). All inputs are generated; no
network, output run, or ignored photo is needed. The helper fixture is committed
in `tests/test_qf04_beat_cuts.py`. Narration is synthetic; mastering is real.

| Measure | Parent f5806e3 | Candidate |
|---|---|---|
| Five-scene graph, ordinary boundaries | 4 crossfades; zoomin and fadeblack present | 4 hard cuts; no xfade |
| Delivered hard cut frames, 30 fps | transition windows span multiple frames | exactly 30, 60, 90, 120 |
| Default equal scenes | transition allowance shifts boundaries | exactly 30, 60, 90, 120 |
| One explicit act boundary at 2 s | not supported; four arbitrary transitions | one dissolve, 8 frames, offset 1.866666667 s |
| Five-second programme | 150 frames | 150 frames |

`before.json` and `after.json` contain the generated fixtures' measurements and
filter graphs. Acceptance false before, true after. Existing expected values have
not been changed. The regression also renders the act case in widescreen.

## Validation

Initial regression: 3 failed on parent, 3 passed on candidate. Widescreen case
added afterward. Initial unchanged loop/caption covering run: 60 passed. Final
covering run includes the new widescreen case, exact-end seam tests, documentary
and end-card tests: 99 passed, 1 existing skip in 512.71 s. Identifier checks: 2 passed.
The documentary handoff/cache regression added after lint exposed a missing helper argument: 9 wiring/documentary tests passed.

After rebasing onto QF-03 f5806e3, all five regression tests fail on the immediate
parent (104.87 s). The rebased regression and exact-end/loop covering tests pass:
54 passed in 154.12 s. Both measurement runs completed against that parent and
the candidate: acceptance false -> true.
