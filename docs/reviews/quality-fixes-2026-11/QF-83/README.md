# QF-83: Shorts on, the dead thumbnail request dropped, FOLLOW on the loop bridge

Spec (project-quality-review-2026-11-05.md section 9.2; HANDOVER-design.md surfaces decision): default Shorts on;
drop the Short thumbnail call; a one-second FOLLOW overlay on the loop bridge. Acceptance: a dry run posts a Short;
no thumbnail request. Operator ruling 2026-11-10 (decisions\FREEZE-RESUME-rulings.md Q5): the FOLLOW overlay is on
in production, and the loop bridge freezes with it on. Base 5c7840e9; the earlier candidate 7fa73b44 (never reviewed)
was ported for its own diff only and reworked.

## What changed

- `YOUTUBE_SHORTS` defaults to true (src/config.py, .env.example); an explicit `false` still turns it off.
- `MetaPublisher.publish_short` makes no thumbnail request after a Short uploads; its receipt has no `thumbnail`
  field. The Shorts feed shows a frame of the video, which only the YouTube app can change. The manual
  "▶️ YouTube thumbnail" button (standard thumbnail for search and the watch page) stays.
- /posts: a Short that never had a thumbnail attempt reads "YouTube thumbnail: a frame of the video, changed only in
  the YouTube app ..." instead of "not set" (it would otherwise call every new Short's thumbnail a defect). Shorts
  posted before QF-83 keep their recorded line.
- `LOOP_FOLLOW_OVERLAY` defaults to true. `end_card.loop_bridge(..., follow=True)` draws the word FOLLOW: Montserrat
  800, 64 px, tracked like the end card's follow line, in bone, its caps centred on y 768 (LOOP_CENTRE, the centre
  of the Reel box). It rises in over the bridge's first 6 frames and fades out over its last 8, so the bridge's
  first and last frames are pixel-identical to the purely visual bridge's and the loop still lands on frame 0. No
  time, narration or sound is added.
- Contrast: the halo (40 px spread, up to full strength) is solved on the lightest picture the word can sit on (per
  pixel, the brighter of the outgoing shot and the incoming frame 0, at the first and last fully-up frame), and the
  ratio is measured on every fully-up frame as drawn. Under 4.5:1 the bridge ships without the FOLLOW
  (`follow_skipped` in the record, an info note on the review card); a measured shortfall is a gate problem, and the
  composer then tries the bridge without its push, then without its FOLLOW, before dropping the bridge.
- The master's cache key carries the setting (main.py), and the record keeps `follow`, `follow_box`, `follow_halo`
  and the measured contrast; the variant stays `loop_bridge_v1`.
- docs/deployment.md: a host whose `.env` does not set `YOUTUBE_SHORTS` posts Shorts after a release with QF-83
  (and draws the FOLLOW unless `LOOP_FOLLOW_OVERLAY=false`); production's `.env` is checked with the operator.

Bone, not the end card's gold: gold measured 3.51:1 on a white frame at full halo and failed 4.5:1 on one of the
seven development endings; bone holds 5.58:1 on white.

## Before / after (measure.py, offline; before.json on 5c7840e9, after.json on this commit)

| Measured | Before | After |
|---|---|---|
| `YOUTUBE_SHORTS` source default | false | true |
| `LOOP_FOLLOW_OVERLAY` source default | absent | true |
| Real coordinator dry run records | dry-run | dry-run + youtube-shorts:simulated |
| Thumbnail requests after a successful Short | 1 | 0 |
| Receipt carries a thumbnail field | yes | no |
| Bridge on the QF-03 Wieliczka photo / white / black | no FOLLOW | FOLLOW, 30 frames, seam problems unchanged, end frames identical |
| FOLLOW contrast (lowest fully-up frame) Wieliczka / white / black | - | 11.9 / 5.58 / 17.6 :1 |

## Real data (real_data.py; real_data.json, real_data_publications.json)

Each master's narration-end frame and its frame 0 (captions as delivered) are read back and the bridge is rendered
plain and with the FOLLOW.

| Set | Masters | FOLLOW drawn | Contrast min / median | Seam problems plain -> with FOLLOW |
|---|---|---|---|---|
| Development, latest per topic | 7 | 7 | 6.39 / 7.05 | 0 -> 0 |
| Production sample copy (counts only) | 31 | 31 | 6.11 / 7.84 | 0 -> 0 |

An earlier version solved the halo on the two endpoint pictures only: 3 of the 31 production endings then measured
4.3-4.5:1 mid-crossfade. That is why it is solved on their per-pixel brighter envelope now.

Production publications (metrics.json, counts only): youtube-shorts published 6, failed 1; manual thumbnail sets 3.

Placement: the FOLLOW box is x 374-709, y 744-791 (plus a 40 px halo and the 10 px rise), inside the Reel safe box
(x 65-888, y 288-1248) and above the caption band (y 1098): frame 0's own caption, where it has one (Nazca), stays
about 400 px below it and the two never touch.

Renders (half size, real development runs): `pair_<topic>.jpg` is bridge frame 14 without (left) and with (right)
the FOLLOW; `strip_<topic>.jpg` is frames 0, 6, 14, 20, 26, 29 at quarter size (frame 0 and 29 carry none of it).
`endscreen_16x9.jpg` (render_endscreen.py, a development 16:9 master): the YouTube end screen on the base (top) and
this commit (bottom); its frames hash identically (`endscreen_16x9.json`), since only the 9:16 composer calls the loop
bridge.

## Reproduce

    python measure.py <repo root>                      # a worktree without .env; prints JSON
    python real_data.py <root> --dev <output dir> [--prod <copy of the sample's output>] [--render <dir>]
    python real_data.py <root> --prod-metrics <copy of the sample's metrics.json>
    python render_endscreen.py <base root> <build root> <16:9 master .mp4> <out .jpg>

## Limits

- The masters measured predate the loop bridge (their endings were the 2.9 s card), so the bridge is rendered on
  their real frames with no push; the composer's recorded seam motion (QF-22) differs slightly.
- This is the offline gate; the 9.5 design-freeze ceiling for the loop bridge is judged in its freeze cycle.
