# QF-134: one deterministic encoder for delivered and judged video on every machine

Proposed by the lander 2026-11-15 (freeze-efficiency review, GPU question), in the operator-approved freeze plan
(decisions FREEZE-EFFICIENCY-2026-11-15, item 5: before any video design group is judged). Parent: dbcabf65 (QF-126b).

## The problem, measured

`src/encoding.get_video_encoder()` chose the encoder by a hardware probe: `h264_nvenc -preset p4 -cq 21` (and
`p1 -cq 12` for the loop bridge's body intermediate) wherever an NVIDIA GPU answered, else `libx264 -preset slow
-crf 18` (intermediate `veryfast -crf 10`). The production VM has no NVIDIA adapter, so it encodes with libx264; the
development PC has an NVENC GPU. Every master, graphic clip and design card that development made, and that reviews
and the design freeze judged, was an NVENC encode production never delivers.

On the development runs (main checkout `output/run_*`, read only; `real_runs.py`, the encoder read from the stream:
libx264 writes its settings into it, NVENC does not):

| delivered files on disk | NVENC | libx264 |
|---|---|---|
| masters (Reel and documentary) | 30 | 0 |
| graphic clips (map, numbers, timeline, scale, then-and-now) | 65 | 0 |

## The fix

Every delivered or judged encode now takes explicit production arguments, never the probe:

| path | caller | before (GPU machine) | after (every machine) |
|---|---|---|---|
| Reel, Short and documentary masters | `video_composer.compose_video` / `compose_deep_dive` | nvenc p4 cq 21 | libx264 slow crf 18 |
| loop bridge body intermediate | `_render_with_loop_bridge` | nvenc p1 cq 12 | libx264 veryfast crf 10 |
| loop bridge delivery pass (and its no-bridge fallback) | `_render_with_loop_bridge` | nvenc p4 cq 21 | libx264 slow crf 18 |
| micro-patched master | `splice_single_beat` | nvenc p4 cq 21 | libx264 slow crf 18 |
| graphic, design-card, callout and dense-timeline clips | `graphics._encode` (and its cache key) | nvenc p4 cq 21 | libx264 slow crf 18 |

`encoding.delivery_video_encoder()` and `delivery_intermediate_encoder()` return exactly what the probe's fallback
returned, so production's commands are unchanged byte for byte (below). Already deterministic and untouched:
parallax, footage and motion clips (libx264), then-and-now (libx264 slow crf 16 grain), still-camera clips (FFV1),
the review proxy (libx264 fast crf 28), Story frames, carousel slides and contact sheets (images).

Scratch previews: no path in the repository makes one today. `encoding.preview_video_encoder()` is the only way to
the probe: it returns the delivery encoder unless `VIDEO_PREVIEW_ENCODER=fastest` (src/config.py, `.env.example`;
default `production`). `get_video_encoder()` (the probe) and `get_intermediate_video_encoder()` are kept, with
their tests, for that and for `scripts/benchmark_encoders.py`; a regression test fails if any module in `src/`,
`main.py` or a design-freeze evidence script (`docs/reviews/freeze-*/`) names either. The two
"probe once before the parallel renderers" warm-ups in `main.py` are gone (nothing delivered asks the probe).

Callers outside `src/` (round 2):

| script | class | what it does now |
|---|---|---|
| `docs/reviews/freeze-panel-2026-11-04/thennow/render_evidence.py` (`delivered_chain`) | delivery (freeze evidence, reused) | encodes its stand-in master with `encoding.delivery_video_encoder()`; the NVENC retry loop is gone |
| `docs/reviews/quality-fixes-2026-11/QF-86/floor.py`, `measure.py`, `render.py` | historical (QF-86 evidence) | unchanged; see the note below |
| `docs/reviews/quality-fixes-2026-11/QF-25/seam_real_runs.py` | historical (QF-25 evidence) | unchanged; patches `encoding.get_video_encoder` and `vc._get_video_encoder` to a fast encoder |
| `docs/reviews/quality-fixes-2026-11/QF-02`, `QF-03` `measure.py` | historical | patch `vc._get_video_encoder`, which still reaches the master (the alias is kept) |
| `docs/reviews/quality-fixes-2026-11/QF-117`, `QF-26` `measure.py` | historical | set `encoding._DETECTED_ENCODER`; nothing delivered reads it any more, and the delivery encoder is the libx264 they pinned (QF-26 pinned veryfast, so a re-run now encodes its graphics slow) |
| `scripts/benchmark_encoders.py` | benchmark | stays on the probe (`get_video_encoder`): comparing NVENC with libx264 is its purpose |

Historical scripts are evidence of rows already landed; at their own commits they reproduce as before. On a tip
with QF-134, a patch of `encoding.get_video_encoder` no longer reaches `graphics._encode`: QF-86's floor.py and
measure.py patch it to a lossless encoder so the graphics are measured losslessly, and a re-run there now measures
libx264 CRF 18 graphic clips instead. To reproduce them on a later tip, patch `encoding.delivery_video_encoder`
as well.

The master's manifest (`<master>.composition.json`) records `video_encoder`: the master encoder, the intermediate
(the loop bridge's body, or null) and every delivery argument.

## Acceptance

`measure.py <repo root>` runs every path above for real (local ffmpeg, generated inputs) as two machines, one
whose NVENC probe succeeds and one without (production; the probe is mocked), and records every video encode
command (repository, work-folder and temporary-file paths normalised). `before.json` (dbcabf65), `after.json`:

| | before | after |
|---|---|---|
| surfaces whose commands depend on the hardware (of 4) | 4 | 0 |
| delivered outputs encoded by NVENC on the GPU machine (of 4) | 4 | 0 |
| production commands (machine without NVENC), SHA-256 per surface | see `production_sha256` | identical to before, all 4 |
| GPU machine commands equal production's | 0 of 4 | 4 of 4 |
| manifest records the encoder | no | yes (`manifest_video_encoder`) |

So on this PC a re-encode now uses production's argument list exactly, and production's arguments did not change.

## Real runs: full-length re-render on this PC (real hardware, no mock)

`real_runs.py <repo root> <output dir> <work dir>` composes the master of the two newest development Reels again
from the run's own parallax clips, narration, bed and words, ending on the loop bridge (`real_runs_before.json` on
dbcabf65, `real_runs_after.json` on this commit; run one after the other on an otherwise idle render slot):

| | before | after |
|---|---|---|
| master stream encoder | NVENC, 2 of 2 | libx264 (slow, crf 18), 2 of 2 |
| video encodes | nvenc p1 cq 12 + nvenc p4 cq 21 | libx264 veryfast crf 10 + libx264 slow crf 18 |
| manifest `video_encoder` | absent | master and intermediate recorded |
| compose wall time, case 1 (44.9 s master) | 197.9 s | 324.6 s |
| compose wall time, case 2 (45.8 s master) | 226.5 s | 302.4 s |

The cost is the difference of the two rows: one to two minutes more per 45-second Reel on this PC (a third to
two thirds of the compose), the time production already spends. Case 1's base render ran one more video encode than the
others (an extra NVENC p4 pass before the body; three encodes against two), which is part of its time. Each wall
time is a single measurement, not a benchmark. Preview speed: no preview path existed or changed; the review proxy is unchanged.

## Known limits

- `-threads` is the machine's core count in the master and splice commands (as before, unchanged so production's
  arguments stay byte-identical). x264 output can differ slightly with the thread count, so development and
  production masters are the same encoder and settings, not bit-identical files. The manifest records the count.
- Changing `src/encoding.py` changes the source hash `main.py` keys the master stage with, so the first render of
  a cached run after deployment re-encodes its master (production: same arguments; development: libx264 replaces
  the cached NVENC master). Graphic clips cached on development are re-drawn once (their key names the encoder);
  production's graphic keys are unchanged.
- The dev runs' existing NVENC files on disk stay as they are until re-rendered.
