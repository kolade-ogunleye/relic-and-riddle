# QF-114: every post is bound to the review card you approved

## Defect

QF-110 review 1, point 8 (pre-existing). After approval, a master swapped together with its composition record and
provenance.json was caught only on QF-110's fact re-check path and at the carousel's slot (QF-81b). The provenance
ledger is unsigned, and the release gate checks that the files agree with each other, not with the approval. The
Reel, its Stories, a Stories retry and the YouTube Short button uploaded whatever the run folder held at that moment.

## Fix

- main.PipelineCoordinator._binding_problem runs before every real upload. It compares the run's review
  fingerprint (the public copy, video, cover, review proxy, Stories, and carousel or carousel plan) with the
  fingerprint recorded for the card the approval was made on. For a card recorded since this commit, it also
  compares the SHA-256 of the master's composition record and of provenance.json with those recorded with that
  card. The release gate already ties the master to its composition record and ledger; those two are now tied to
  the approval too.
- Where it runs:
  - `_require_release` (non-dry): the Reel's publish, Post now, Retry Reel, and the booking.
  - `_post_photos`: Stories and carousel at their slot. A Stories booking is marked failed (`binding`) with one
    Needs-you notice: "Nothing was posted. Make a new version".
  - `retry_post`: "Not retried".
  - `post_youtube_short`: "Not posted".
- The carousel's own slot checks (QF-81b) are unchanged.
- telegram_state.put records the file digests ("review-files") whenever a "review-card" is recorded. That covers
  every place a card is recorded, now and later, without changing the five call sites.
- Not bound, as before:
  - an approval with no card record (the command line, the auto-approve flag);
  - a card recorded before this commit, whose fingerprint is checked but which has no file record. A swap of the
    composition record or ledger alone, with the master unchanged, is caught only for cards delivered from this
    release on.

## Measurements

measure.py runs the real posting paths on temporary databases with a stub publisher. The release gate is stubbed so
that only the binding decides. Each run is approved on its card, then one thing is swapped.

| swapped after approval | base 6a7ef303 (Reel / Stories / Stories retry / Short) | this commit |
|---|---|---|
| master | posted 4 of 4 | 0 of 4 |
| composition record | posted 4 of 4 | 0 of 4 |
| provenance ledger | posted 4 of 4 | 0 of 4 |
| cover | posted 4 of 4 | 0 of 4 |
| public copy | posted 4 of 4 | 0 of 4 |
| **uploads of a swapped review** | **20 of 20** | **0 of 20** |
| nothing swapped | posts on every path | posts on every path |

Real data, a downstream check:

- Development database (a read-only copy): 6 runs are approved on a recorded card. On this commit all 6 still match
  their card (`_binding_problem` None), the same as the base's `_approved_fingerprint`. No approved run would be
  held by the new check. Their cards predate this commit, so they are bound by fingerprint only.
- Production sample: the approvals and Telegram tables are excluded from the sample, so it could not be checked.
  Production's pending approved bookings are checked at their slot the same way the carousel has been since QF-81b.

## Notes

- No rendered surface changes. The wording is new: the Stories notice and the retry and Short replies.
- Regression test: tests/test_qf114_post_bound_to_approval.py. Its class check reads the source of every
  coordinator method that uploads, and requires the binding check (or `_require_release`) before the upload.

## Round 2 (review 1: B3, and two notes)

### B3: the episode's finishing uploads are bound too

set_facebook_cover (the cover), add_facebook_captions (the master's .srt) and set_youtube_thumbnail (the cover on
the Short) upload from the episode's buttons at any time after posting, and set_facebook_cover and
add_facebook_captions also run from _facebook_finishing. All three now check _binding_problem before uploading and
answer in their own style: "🖼 Cover not set: …", "💬 Captions not added: …", "▶️ Thumbnail not set: …".

The caption file is now among the records bound to the card (telegram_state.REVIEW_FILES: "captions" = the
master's .srt). A card recorded before this round has no caption digest; its other records are checked as before.

The source test no longer names the uploaders. It finds them: every PipelineCoordinator method that calls a
publisher's upload method (publish*, set_*, upload*, taken from src/publisher.py's *Publisher classes) on
self.publisher or on a publisher it creates, outside a dry run. Each such method must call the binding check (or
_require_release) before its first upload. It finds the original paths plus set_facebook_cover,
add_facebook_captions and set_youtube_thumbnail.

### Notes closed in this round

- **A card record gone:** review records were purged after 90 days (and beyond 10,000 rows per namespace), and an
  approval whose card record was gone then posted unchecked. telegram_state.put now keeps "review-card" and
  "review-files" for good. A Telegram approval whose card record is missing anyway (purged before this release) is
  held with the reason "the record of the review card you approved is gone, so what posts cannot be checked against
  it". An approval with no message (the auto-approve flag) is still not bound.
- **The carousel's slot:** the carousel checks its binding before it takes the day's slot (_approval_broken). That
  check now covers the file records (composition record, ledger, captions) and a missing card record too, so a
  carousel that fails only those no longer uses up the slot.
- **Old cards:** they are checked by fingerprint on every path. That is stricter than before QF-114, when only the
  carousel checked; it is not unchanged. On the dev copy no approved run is held (below).

### Round-2 measurements (measure.py)

Uploads that went out after a swap, over every path (Reel release, Stories, Stories retry, Short button, Facebook
cover, Facebook captions, YouTube thumbnail, carousel slot):

| swap after approval (8 paths each) | base 6a7ef303 | round 1 (5d09d631) | round 2 |
|---|---|---|---|
| master | 7 | 3 (the finishing buttons) | 0 |
| composition record | 8 | 4 (finishing buttons, carousel slot) | 0 |
| provenance ledger | 8 | 4 (finishing buttons, carousel slot) | 0 |
| cover | 7 | 3 | 0 |
| public copy | 7 | 3 | 0 |
| captions (.srt) | 8 | 8 | 0 |
| card record gone | 8 | 8 | 0 |
| **uploads of a swapped review** | **53 of 56** | **33 of 56** | **0 of 56** |
| nothing swapped | posts on all 8 | posts on all 8 | posts on all 8 |

(On the base, the carousel slot check already held a changed master, cover or copy: QF-81b.)

Downstream on the dev database copy, the 6 runs approved on a recorded card still match it, and no approved run has
a missing card record. The 7 others were approved without a message (auto-approve) and are unbound, as before.

## Round 3

Review 2: OK 9/10. One nit closed. The uploader discovery now also follows a local name bound to a publisher the
method makes (`fb = FacebookPageReelsPublisher(); fb.set_cover(...)`). A test checks this against a copy of main.py
with such a method added. No product code changed in this round.
