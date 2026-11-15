# QF-126b: complete credits that fit the caption

Decision: lander, 2026-11-14, under the operator's delegation (the QF-126 build 1 ruling). Stacks on QF-126 as it
passed review (11b5e812, round 3); round 2 of this row is the rebase onto it (section "Round 2").
Trade-off order: licence compliance > picture fit > posts not held > caption brevity.

## The problem
QF-126 holds a post whose required credits do not fit its published text. As one line a photo
(`📷 author | title | licence | page link | licence link`, about 230 characters each), half of the real Reels were
held: 14 of 28 development runs and 14 of 32 in the production sample.

## Where the characters went (measured first)
Development runs, 161 photo lines (measure.py, `one_line_a_photo_characters_by_part`):

| Part | Characters | Share |
|---|---|---|
| link to the photo's page | 15,447 | 42 % |
| title | 7,598 | 21 % |
| link to the licence | 7,081 | 19 % |
| marks and separators | 2,576 | 7 % |
| author | 2,387 | 6 % |
| licence name | 1,575 | 4 % |

Every one of the 161 photos (and of the 174 in the production sample) is a Wikimedia Commons file. The licence link
is repeated on every line (161 lines name 8 licences), and an author is repeated for each of that author's photos.
The largest part is the link to the photo's page, which spells the whole file name again.

## What changed
1. One line a licence, when the one-photo lines do not fit (`credits.compact_photo_credits`):
   `📷 CC BY 4.0 <licence link> | Ann Lee: “File:Vault.jpg” <page link> | “File:Door.jpg” <page link> | Sam Roe: “File:Stair.jpg” <page link>`.
   The licence name and its link once; each author once, before that author's photos; every photo with its title
   (exactly as the one-photo line prints it) and the link to its own page. A field ends at ` | `, which no author,
   title or licence can contain, each title is in quotation marks and each link follows its title, so the line
   reads back into the same five parts per photo (`compact_credit_works`); a line is written only when it reads
   back to exactly the photos it stands for.
2. The link to a Commons file page may be the page's own address by its page id,
   `https://commons.wikimedia.org/?curid=<id>`, used only when the full links do not fit. It is MediaWiki's own
   `curid` parameter on Commons' own host, not a shortener.
3. The form is chosen by the caption's length as posted (with the illustration disclosure when the run has one).
4. Nothing else moved: the one-photo lines are still what a caption with room carries, and what the description
   and the documentary carry (a carousel caption: round 2). The share-alike notice, the adapted notice and the
   music line are unchanged. QF-126's gate (`credit_reach_problems`), its hold reasons and its limits are not
   weakened; the gate now reads a compact line photo by photo (`credit_present`), and a line that is cut, or that
   names another author, another page or another licence link, credits nothing.

### Checked against the licence texts
- CC BY 4.0 s.3(a)(1): what the licensor supplied (identification of the creator, a copyright notice, notices that
  refer to the licence and to the disclaimer of warranties, a URI or hyperlink to the material to the extent
  reasonably practicable), an indication of modification, and that the material is under the licence, with its
  text or a link to it. What the records supply is written for every photo, as before: author, link to the photo's
  page, licence name and link, and the adapted notice (one line for the video, unchanged). No record holds a
  separate copyright or disclaimer notice; that is unchanged from QF-42 and QF-126.
- s.3(a)(2): the conditions may be met "in any reasonable manner based on the medium, means, and context". A caption
  line that groups photos by licence and author, with each photo's own title and link, is such a manner; the
  licence does not ask for one line a work, or for the licence link to be repeated per work.
- CC BY 4.0 does not ask for the title; 2.0, 2.5 and 3.0 ask for it "if supplied" (s.4(b)/(c)). The title is kept
  for every photo under every version, unchanged (the same `credit_title` text, "File:" included).
- CC BY-SA: the share-alike notice with the BY-SA 4.0 link is still its own line.
- One licence, one link: creativecommons.org serves one deed over http and https, with or without the final "/";
  records hold these spellings for the same deed (`licence_link_key`). A different version, or a ported licence
  (`/by-sa/3.0/pl/`), is never folded into another's line.

### The page-id link (verified from Wikimedia's own documentation, 2026-11-14)
- mediawiki.org, Manual:Parameters to index.php: `curid`: "The ID of the wiki page; overrides the value of title".
- mediawiki.org, Manual:Page table: the page id "is preserved across edits and renames"; a deleted and restored
  page tries to reclaim its old id (MediaWiki 1.27 and later). The address by title depends on a redirect being
  left when a file is renamed.
- Checked on one real file: `https://commons.wikimedia.org/?curid=<id>` answers 200 with the file page itself (its
  canonical link is the `/wiki/File:...` address).
- An id is used only when the Commons API returned it for the file page the source link names
  (`commons.file_page_ids`: `action=query`, File namespace only, a rename's redirect followed, a missing page gets
  none), recorded beside the run's ledger (`source_links.json`) so the gate can check it; a newly fetched Commons
  photo records its page id at fetch. The gate accepts a page-id link only for the page it was recorded for.
- Where an id comes from, in this order: (1) the photo's own record: the Commons search answer the fetch already
  receives carries the page id, it is kept in `credits.json` and copied into the run's ledger (no extra request;
  every Commons photo fetched from this commit on); (2) `source_links.json` beside the ledger, from an earlier
  lookup; (3) only for a photo with neither (photos fetched before this commit), and only when the credits do not
  fit with full links: one read-only Commons API query for the run (50 titles a request; the file titles are all
  it sends), by the same client and User-Agent as the photo search. It runs when the credits are written (render,
  Edit, redraw), never in the release gate, the publishers or a test. A failed or refused lookup changes nothing:
  the full links stay and the gate decides.
- Not used: w.wiki (Wikimedia's shortener needs a write to their service for each link) and any third party.
- `CREDIT_SHORT_SOURCE_LINKS=false` turns the lookup off (full links only; a post that then does not fit is held).

## Result (measure.py; 11b5e812 -> this commit)
Development runs, committed fixture (`tests/fixtures/qf126b/`), equal to the real folders read in place:

| | Before | One line a licence, full links | With page-id links |
|---|---|---|---|
| Held for credits | 14 of 28 | 5 of 28 | 1 of 28 |
| Sent | 14 | 23 | 27 |
| Sent lacking a credit | 0 | 0 | 0 |
| Photo credits in the texts sent, all five parts | 61 of 61 | 122 of 122 | 154 of 154 |
| Notices and music lines in the texts sent | 20 of 20 | 38 of 38 | 47 of 47 |
| Texts sent over their limit | 0 | 0 | 0 |
| Credit lines cut | 0 | 0 | 0 |
| Page-id links not the photo's own page | 0 | 0 | 0 of 32 |

Production sample (read only, counts; `real_before.json`, `real_after.json`):

| | Before | One line a licence, full links | With page-id links |
|---|---|---|---|
| Held for credits | 14 of 32 | 7 of 32 | 1 of 32 |
| Sent lacking a credit | 0 | 0 | 0 |
| Photo credits in the texts sent, all five parts | 70 of 70 | 117 of 117 | 166 of 166 |
| Credit lines cut, texts over their limit | 0 | 0 | 0 |

Characters of a run's whole credit set (development; each line and the blank line before it):

| | All 28 runs | Longest run | Runs over 1,500 |
|---|---|---|---|
| One line a photo | 41,755 | 2,532 | 16 |
| One line a licence, full links | 33,766 | 2,171 | 6 |
| One line a licence, page-id links | 25,894 | 1,505 | 1 |

The form alone (full links) does not reach the target: 5 and 7 stay held. The page-id links are what brings both to
1. The lookup was asked for 5 development runs and 7 production-sample runs (one API query each); the others fit
without it.

The two runs still held are held truthfully:
- Development: a run with 7 photos under 5 licences by 7 authors, whose stored caption also carries the remains of
  a credit broken across two lines before QF-42 (about 250 characters of an old summary line's tail and a line
  with no fields). Its complete credits are 1,505 characters; with a clean caption they fit.
- Production sample: one run is over by a few tens of characters (4 licences).
Both are cleared by Edit (shorten the caption) or Regenerate, as the hold reason says.

## Picture fit
Part (b) of the row, a per-run budget at picture selection, is not built. The form reaches the target on both sets
of real runs, so no picture choice is traded for characters, and the chosen picture for every line is unchanged by
construction: `git diff 11b5e812 -- src/scene_planner.py src/beat_images.py src/image_review.py src/photo_pool.py
src/hero_photo.py src/picture_coverage.py src/storyboard.py` is empty (0 of the lines of 60 real runs can change).
For the record, why (b) is not a small step: a line's fit is a yes/no verdict (`beat_images.line_fit`), the first
choice is the planner's or the approved storyboard's, and the only place candidates of equal fit are compared is
the swap repair in `scene_planner.fit_to_lines`, which stops at the first photo that fits. A licence preference
there would need more looks per line inside a bounded call budget, which can cost a later line its check. It is
listed for the operator with the two options not in scope (a hosted credits page; on-screen credits).

## Old runs
- A caption written one line a photo (QF-42, QF-126) is read exactly as before and passes or is held as before.
- A caption that does not fit is rewritten at Edit or Regenerate; its stale one-photo lines (a photo an earlier
  render used, a line written before QF-42) are taken out when the compact lines are written, since every photo the
  ledger asks a credit for is written again.
- Writing the credits twice changes nothing; a caption that gains room gets the one-photo lines back.

## Round 2 (rebase onto the passed QF-126)
Round 1 was built on QF-126 round 1 (f81ebe96). QF-126 passed review in round 3 (11b5e812) after rounds 2 and 3
changed the carousel credits, the ledger reading and the hold wording. Every hunk was read against them:
- The cherry-pick conflicted only in STATUS.md (both rows kept; QF-126's commit cell is 11b5e81). The Reel numbers
  above are unchanged by the rebase except the character counts by part: QF-126 round 2's author fix
  (`_stated_author`) prints 37 more characters of one development author, on both sides.
- QF-126 round 2 writes a run's credits again from its ledger when its review is sent again (`_recredited`). It now
  passes the same `lookup` as the render, so old stock sent for review gets the form that fits.
- QF-126 round 2's hold says "They fit ... re-send the review or save any Edit" only when writing the credits again
  fits them, else "N characters over". Writing them again now writes them one line a licence when one line a photo
  does not fit, so the measure behind that wording (`_credits_over`) counts that form too (with the page ids the
  run records; not for the documentary's description, which keeps one line a photo). Review 2 found it still said
  "over" of copies whose page ids were not recorded yet; round 3, below, fixes that.
- Carousels. QF-126 review 3 found 2 of the 19 production-sample carousels with slide records finally held
  ("9 credit line(s) ... do not fit its caption"), and said QF-126b's shorter form is what makes such decks fit. A
  carousel caption whose one-photo lines do not fit now carries the same photos one line a licence
  (`credits.with_compact_photo_lines`; page ids from the run's `source_links.json`, never a lookup at posting time),
  both for a carousel whose slides keep records and for one credited from its run's ledger. The check that every
  credit is in the caption reads a compact line photo by photo (`credit_present`), not by exact line equality.
  Everything QF-126 holds is still held: a photo the ledger does not hold, an incomplete credit, a missing ledger,
  credits that do not fit even one line a licence (final, Details only).

Every real carousel through `_carousel_request` (`carousel_probe.py`; scratch copies, the data read only; a sent
caption checked by the probe's own reader: every photo asked has its author, title, licence name, link to its page
and licence link on one line; `carousel_before.json` on 11b5e812, `carousel_after.json` on this commit):

| | 11b5e812 | this commit |
|---|---|---|
| Production sample, slide records, photos read where production reads them (19) | 17 sent, 2 finally held (do not fit) | 19 sent (2 one line a licence) |
| Production sample, slide records, as exported (19) | 2 sent, 17 held (photo in neither record) | the same |
| Production sample, no slide records (15) | 14 sent, 1 held (over 2,200) | 15 sent (1 one line a licence) |
| Development, slide records (7) | 7 sent | 7 sent |
| Development, no slide records, with a ledger (22) | 18 sent, 3 held (over 2,200), 1 held (incomplete author) | 20 sent (2 one line a licence), 1 over, 1 incomplete |
| Development, no ledger (2) | 2 held | 2 held |
| Sent lacking a credit; over 2,200; with a first comment | 0; 0; 0 | 0; 0; 0 |

(Review 3 counted the relocated set as 15 sent, 2 held for a photo in neither record, 2 finally held; this probe
finds the photo of those 2 in the sample's assets, so they are sent on both commits. The 2 that do not fit are the
same 2 runs.)

QF-126's own measure.py, run both ways (11b5e812 -> this commit; its committed JSON is QF-126's and is not
rewritten here):
- Default (posts from the real credit sets): sent with every credit whole in every text 16 -> 21 of 33, held with
  the reason 17 -> 12; 2 Reels and 3 carousels that were held now fit; sent lacking a credit 0 -> 0.
- Development, read in place: Reels credited again without a lookup, held 14 -> 5 (with the lookup: 1, above);
  told "they fit" 11 -> 20; record-less carousels held over 2,200 3 -> 1, sent with every photo credit of the run
  16 -> 18.
- Production sample: Reels held 14 -> 7 without a lookup; record-less carousel over 2,200 1 -> 0. Its reader counts
  1 Reel "sent lacking a credit" (3 credits): it matches each part as written, and that run records one licence's
  link as `https://creativecommons.org/licenses/by-sa/3.0` for one photo and with the final "/" for three others.
  The compact line names that licence once, with the first spelling; it is the same deed (`licence_link_key`), and
  this row's measure.py, which reads the deed either way, counts every credit of that run complete.

## Round 3 (review 2: a hold said "over" of a copy a re-send fits)
Review 2 found that the hold still said "N characters over ... or Regenerate" of real stored copies that a re-send
or an Edit fits: `_credits_over` measured the compact form only with the page ids a run records, and no run fetched
before this row records any (no `source_links.json`), while the re-send and the Edit look the ids up. The gate still
looks nothing up (nor does posting). With `CREDIT_SHORT_SOURCE_LINKS` on, the hold measures again with each Commons
photo that has no recorded id given the longest id (`?curid=` and nine digits; the highest real id is nine digits),
an upper bound on the link a lookup writes (`_with_page_id_bound`). When that fits, the hold says "They fit, and
nothing needs to be cut: the photos' page links are shortened when the credits are written again (re-send or any
Edit)"; otherwise it says "over", and (round 4) the figure it states is the bound's too: an upper bound that is
never below what the Edit must cut, since the Edit writes those links, not the full links' figure. With the
setting off, the full links are measured and stated.

`hold_probe.py` (counts only): each real stored copy through the release gate's own texts
(`payload_credit_texts`), then its credits written again as a re-send writes them (with the page ids, no network):

| | 4599a8c8 (round 2) | this commit |
|---|---|---|
| Development (28): told fit / told over but a re-send fits / truly over | 20 / 4 / 1 | 24 / 0 / 1 |
| Production sample (32): the same | 23 / 6 / 1 | 29 / 0 / 1 |
| Told fit but still held after the re-send | 0 | 0 |

The 3 development and 2 production-sample runs not counted are not held as stored. The run left "over" in each set is
still held after the re-send. Its hold states the nine-digit bound's figure: 201 and 33 characters over (round 3
stated the full links' 482 and 523). With their real ids, which `hold_probe.py` measures after the re-send has
recorded them, they are 194 and 26 over.

A run backed up to the sync folder now keeps `source_links.json` (`src/backup.py`), so a restored run's compact
lines still read as credits.

## Files
- `hold_probe.py`: the hold's wording and stated figure against a re-send, on real runs (counts only).
- `measure.py`: the metric; `before.json` (11b5e812), `after.json` (this commit): the committed development fixture.
- `carousel_probe.py`, `carousel_before.json`, `carousel_after.json`: every real carousel (round 2; counts only).
- `real_before.json`, `real_after.json`: the same counts with `RR_REAL_OUTPUT` on the development output folder
  and on the production sample (counts only; the production page ids are not committed: `RR_PAGE_IDS`).
- `tests/fixtures/qf126b/dev_runs.json`, `dev_page_ids.json`: the 28 development runs and their photos' page ids.
- `tests/test_qf126b_credits_fit.py`: the regression test.
