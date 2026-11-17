# QF-103: /posts counts a booking's Story frames, retries included

## Defect

QF-80 cut the poll frame from the Stories, so a booking now posts two frames, not three. Its /posts line ("Frames
posted: N") could not see a frame finished by a retry in a record written before c497369c. A production episode
(2026-10-24/25) sent three frames: frames 1 and 2 published, frame 3 reached one platform, and a retry finished it.
Its line read 2 (QF-80 review 5). The operator had QF-80 land without the line (decisions/QF-80-blocked.md), and the
count returns here. QF-80 review 6 sets the scope: both counts, the pending booking's "Frames: N" and the posted
booking's count, retries included.

## Fix (src/posting_view.py, display only)

- `story_frames_sent` counts the distinct frames that went out. It joins the published upload-post:story receipts'
  parts, the booking's first send (`posted_parts`), and the frame a successful retry sent.
- retry_post stores no `part` of its own. The retried frame is therefore taken exactly as retry_post chooses it:
  `int(detail.get("part") or int(detail.get("posted_parts") or 0) + 1)`. This is the review-5 fix. QF-80's round 5
  required `detail["part"]`, which the real record lacks.
- The episode's Stories block shows one line:
  - pending, waiting, or failed with nothing sent: "Frames: N", the frames it will post (story_posting_frames:
    never an older run's poll frame);
  - published: "Frames posted: N";
  - unknown, or failed with frames out: "Frames confirmed: N";
  - nothing confirmed, or cancelled: no line.

  The wording is QF-80 rounds 3 to 5's, which those reviews checked.

## Measurements

measure.py drives the real /posts screen on a temporary database, with each record shape the code has written.

| case | base 6a7ef303 | this commit | went out (or will post) |
|---|---|---|---|
| booked, two frames | no line | Frames: 2 | 2 |
| booked before QF-80 with the poll frame | no line | Frames: 2 | 2 |
| posted before QF-80 (three frames) | no line | Frames posted: 3 | 3 |
| posted, two frames | no line | Frames posted: 2 | 2 |
| production shape: frame 3 finished by a retry before c497369c | no line | Frames posted: 3 | 3 |
| reveal reached one platform | no line | Frames confirmed: 1 | 1 |
| reveal finished by today's retry | no line | Frames posted: 2 | 2 |
| failed, nothing sent | no line | Frames: 2 | 2 |
| **right count** | **0 of 8** | **8 of 8** | |

Real data, counts only:

- Production sample (metrics.json): 16 upload-post:story receipts over 5 episodes. Four episodes have 3 published
  receipts each. The fifth has published, published, unknown and published (the retry): the shape above. On
  this commit all 5 read "Frames posted: 3"; on the base, no count. The booking records themselves are not in the
  sample, so the fifth booking's record is the one review 5 reconstructed from the code history.
- Development database (a read-only copy): 0 Story bookings and 21 simulated story receipts. There is no real
  booking to read, so the dev side is covered by the shapes above.

## Notes

- Same limit as QF-80's README: a run booked twice would count both bookings' frames. No app path makes such a
  booking while the first has frames out.
- Telegram text only; no rendered surface changes. The Telegram batch (QF-69 to QF-71) may also touch
  src/posting_view.py; check with merge-tree at landing.
- Regression test: tests/test_qf103_posts_story_frames.py.
