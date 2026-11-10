# QF-80: Stories are hook and reveal; the poll frame is cut

The third Story frame asked the poll's question over an empty band kept for a native Instagram poll sticker. The
sticker cannot be posted through the API (Upload-Post takes a photo per Story, nothing more), and nothing outside
`src/multi_format.py` told anyone to place one, so every episode's Stories ended on a question nobody could answer.

Ruling: decisions/QF-80-stories-threshold.md, option 1 (operator delegation of 2026-11-09). Cut the poll frame; frames
1-2 keep posting as now, with **no audience gate** (the 1,000 followers / 20% reach gate of the quality review is not
adopted, and nothing of the earlier `story_policy` build is carried); each frame's reach stays recorded so a gate can
later be set from evidence. The two origin/main assertions changed are the ones the operator approved
(decisions/QF-80.md, decisions/QF-80-proposed-main-tests.patch).

## What changed

- `multi_format.generate_story_sequence` draws `STORY_FRAMES` = hook and reveal. The script's third slide and the
  poll are not drawn; no frame is given the poll (a two-frame sequence would otherwise make the reveal the "last"
  frame and turn it into the question frame). `companion_posting.json` no longer carries `story_poll_sticker`. A
  `story_03_question.jpg` left in the folder by an earlier render is removed.
- `multi_format.story_posting_frames(paths)`: the one rule every consumer uses. It drops a retired poll frame, keeps
  at most two frames, and numbers each by its place in the sequence (hook 1, reveal 2) when the files carry the
  sequence names.
- Booking (`main._book_followers`), the dry-run simulation and the Story credit check (`release.story_frames`) count
  and book only those frames. The review card (`telegram_bot`, "Also posted on approval: N Story frame(s)") leaves
  out a retired poll frame; it does not apply the two-frame cap, so the origin/main test that sends it three
  generically named frames (tests/test_review_package.py) keeps its expected values.
  So a run rendered before this row (all 30 dev and 34 production-sample runs with Stories have the poll frame on
  disk) books 2.
- `MetaPublisher.publish_photos` posts only those frames: a booking made before this row (three files) sends two
  requests. Each request keeps its frame's number: a retried reveal was sent as `-story-1-retry…` and recorded as
  part 1, so its reach was stored as the hook's (`audience` labels a Story by `part`: `<platform>_story<part>`). It is
  now `-story-2-retry…`, part 2, stored as `instagram_story2`. A failed frame's result names its part.
- `retry_post` refuses to retry the poll frame of an older booking ("that was the poll frame, which is no longer
  posted"), before any retry is counted, and picks a frame by its number, not its place among the files still on disk.
- `/posts` is unchanged: this row adds no frame-count line there. A count of the frames each Story booking
  posted (retries included) moved to the proposed row QF-103, because the stored record of a booking finished
  by a retry before this row cannot always say which frame the retry sent (decisions/QF-80-blocked.md).

Story reach recording: unchanged code (`audience.collect` already stores each frame's 18-hour snapshot). What this row
adds is that the number it is stored under is the frame's own. The production sample has Story reach rows for
frames 1, 2 and 3 (6, 6 and 4 rows on each platform; 2 per Instagram frame carry a reach figure).

## Measured

`python docs/reviews/quality-fixes-2026-11/QF-80/measure.py <repo root> [--runs DIR] [--metrics FILE] [--db FILE]`
prints JSON, writes nothing in either tree and makes no network call (sockets refused; Upload-Post is a local
recorder). The fixture part needs no data. `--runs` reads run folders read only; `--db` reads a temporary copy of a
pipeline database. `before.json` is the parent 1a7d2759 and `measurements.json` this row, on the dev output folder
and dev database; the `*_production_sample.json` pair is the same on a copy of the production sample's Story folders
and its metrics.json (a read-only copy of the production sample, kept outside the repository).

| | parent 1a7d2759 | QF-80 |
|---|---:|---:|
| Fixture: frames made | 3 (hook, reveal, question) | 2 (hook, reveal) |
| Fixture: poll-sticker rooms made / sticker spec in companion_posting.json | 1 / yes | 0 / no |
| Booking made before this row (3 files): frames booked / requests sent | 3 / story-1, -2, -3 | 2 / story-1, -2 |
| Retried reveal: request key / reach stored as | story-1-retry1 / instagram_story1 | story-2-retry1 / instagram_story2 |
| Retry of an older booking's poll frame reaches the publisher | yes | no |
| Dev runs (30 with Stories): runs that would book the poll frame / frames booked | 30 / 90 | 0 / 60 |
| Production sample (34 with Stories): runs that would book the poll frame / frames booked | 34 / 102 | 0 / 68 |
| Poll rooms recorded in layout.json (dev 22 layouts / production 33) | 1 / 17 | unchanged (old files; never posted) |
| Hook and reveal redrawn from 7 real dev runs' recipes: SHA-256 | see before.json | identical to the parent, all 14 frames |

Dev database (simulated publications only): 7 Story publications per part 1, 2 and 3, no Story metrics. Production
sample: 15 published Story posts and 1 unknown in the sample period.

## Before / after (real runs, half size)

Each frame redrawn by the measured code from the run's recorded recipe (its photo per frame and its verified script):

- `before_richat.jpg` / `after_richat.jpg`: run_richat_structure_sahara_eye_1793474843 (the dev run whose layout
  records a poll room). Frame 3, "ORIGIN DEBATE", is a headline over the photo with nothing to answer it.
- `before_gol.jpg` / `after_gol.jpg`: run_gol_gumbaz_whispering_gallery_1793278143.

The hook and reveal are pixel-identical before and after (the SHA-256 above); only the third frame is gone.

## Effects downstream

- Every surface that counts frames still to post now says 2: booking lines ("📱 2 Story frames"), the dry-run receipt
  and the review card. Bookings already in the queue post 2 of their 3 files. `/posts` shows no frame count
  (QF-103).
- A new run's Story photos are matched for 2 lines instead of 3 (`_match_photos` -> `scene_planner.plan_scenes`, a
  model call that cannot be replayed offline). The hook and reveal may be given a different photo than a 3-line plan
  would have given them; the model request is one line shorter. The redraws above use the recorded photos.
- The script still writes three Story slides and the poll; the poll still feeds the carousel's closing question, and
  the fact check still checks slide 3's words. Left as they are (QF-80 is confined to the Story frames).
- The video audit (`video_qa.audit_story_cards`) and the review fingerprint (`telegram_state`) still read every
  `story_*.jpg` in an older run's folder, including its poll frame: over-inclusive, never posting it.
- The poll-frame rendering path (`asks_poll`, `POLL_STICKER_ROOM`, `poll_room_box`) stays, used only to redraw an
  older run's recorded recipe; removing it is a separate cleanup, not mixed into this fix.
- Design freeze: Stories are in the freeze scope. The parked round-4 social spec item 4 (poll-sticker room above the
  bottom-35% line, a lit poll ground; VERDICT-round3-social.md, from REVIEW-round3-social-platform.md defect 3 and
  REVIEW-round3-social-creative.md item 5) is about the frame this row removes: it becomes moot. Nothing in the park
  tree was changed.
