# QF-126: the complete CC BY credit line, and four small licence rules

Source: operator, 2026-11-14; catalogue audit of 2026-11-14, risks R3 (credits), R4 (ElevenLabs music), R5 (fonts),
R6 (excluded sources). Base: 8d90acd5 (rounds 1 and 2 were built on 38f6f5d3).

Trade-off order (binding): licence compliance, then the truth of what the operator is told, then posts not held,
then a short caption. Not changed: the AI labels (QF-65), fact wording, the hashtag policy, QF-42's expected values.
Round 2 (review 1, 2026-11-14) and round 3 (review 2) are the sections "Round 2" and "Round 3" below; the tables
are re-measured on the final commit. Not changed by round 3: Stories (QF-126c), the ESA rules as ruled, the credit
form (QF-126b), approved button labels.

## What was wrong

1. **The music credit was incomplete.** It read `🎵 Music: “title” by author (CC BY 4.0)`: no link to the track, no
   link to the licence, no word that the track was edited. CC BY 4.0 section 3(a)(1) asks for all three, and every
   bed is cut, looped with a crossfade and mixed under the voice.
2. **A Reel's photo credits were checked in a text it does not post.** The release gate passed a photo credit found
   in the caption *or* the description. A Reel posts no description (Instagram and Facebook get the caption, the
   Short gets the caption and the hashtags), and the caption took only the credit lines that fitted 2,200
   characters. A credit that did not fit was posted nowhere.
3. **Nothing said the photos were adapted** once the summary line ("📷 Images via ... · adapted: ...") had given way
   to the per-photo lines.
4. **A documentary's description is cut at 5,000 characters** (`publisher.youtube_fields`). The credits are its last
   lines, so a long description lost them, and nothing checked.
5. **A carousel's credits** that did not fit its caption went to a first comment, whose failure does not fail the
   post; lines that fitted neither were left out with a log line.

## What changed

- `music.credit_line`: one line with the title, the author, the licence name, the track's page, the licence's
  address and "edited: cut, looped and mixed". CC0 and public-domain tracks (the library's `cc0`, and `pdm`) get no
  line and are never called CC BY.

  Before: `🎵 Music: “Mystery Ambient Documentaries Mysterious Places” by UNIVERSFIELD (CC BY 4.0)`

  After: `🎵 Music: “Mystery Ambient Documentaries Mysterious Places” by UNIVERSFIELD | CC BY 4.0 |
  https://freesound.org/people/UNIVERSFIELD/sounds/697084 | https://creativecommons.org/licenses/by/4.0/ | edited:
  cut, looped and mixed` (one line; a development run's library track).
- `credits.required_credits`: every credit a run's licences ask for, from its provenance ledger and its bed's rights
  record: the share-alike notice, one line per photo (author, title, licence, source link, licence link, as since
  QF-42), the notice `🎬 Photos adapted for this video: cropped, with motion and text added.`, one line per music
  track. The music line is rebuilt from the bed's record, so a run rendered before this change is asked for the
  complete line too.
- `credits.add_provenance_attributions` writes them into the description and the caption. A credit is never
  shortened. When they do not all fit the caption, the summary line gives way first; if they still do not fit, the
  post is held.
- Release gate, new check `licence_credits` (shown as "Licence credits"): every required credit is complete (source
  link; licence link unless the item is public domain or CC0) and is in the text each platform receives, read
  through the publishers' own field builders (`publishable.credit_texts`): the Reel caption (Instagram and
  Facebook, 2,200), the YouTube Short description (5,000), the documentary's YouTube description (5,000). The
  reason names the text, how many credits it lacks, which, by how many characters they do not fit, and what to do
  (Edit to shorten a caption, or Regenerate with fewer photos that need a credit).
- `MetaPublisher.build_envelope` makes the same check on the strings as they are sent (after the topic's hashtag
  list is applied) and refuses the request: "Not posted: a licence credit is missing from the text to be posted".
- Carousel (`PipelineCoordinator._carousel_request`): every photo credit line is in the caption, or the carousel is
  not posted and the alert says why. Credits no longer go out in a first comment.
- Edit rewrites the credits from the ledger (as Regenerate does), so shortening a caption is what clears a hold, and
  a short music line becomes the complete one. The music rights check accepts the complete line in place of the
  short one its record names.
- R4: `music.register_download` refuses a generated score (ElevenLabs as source or provider, or a run's
  `music_elevenlabs.mp3`), and `music.in_rotation` never picks one from a library file edited by hand. Today's code
  had no reuse path: the score is generated per run into the run's folder. The guard is at the library's door.
- R5: `font_evidence.font_rights` (the release gate's font check) now reads the face itself: its name table carries
  the copyright notice and the OFL notice, and a face whose licence declares a Reserved Font Name may carry that
  name only as a recorded unmodified upstream copy. The check reads `fonts/` and each face's own licence record; it
  does not depend on QF-87's files.
- R6: `src/source_policy.py`: David Rumsey Map Collection, BBC Sound Effects (RemArc) and ESA media not marked
  CC BY-SA 3.0 IGO are refused in the Commons search, the museum and Openverse searches (cached answers too), the
  archive film search, the operator's upload (where the source is typed, and again when the image is added), the
  music library, and when a run's rights are decided.

## Round 2 (review 1: four blockers and three notes)

1. **A carousel whose slides have no record** (drawn before slide records were kept) was sent with the Reel
   caption's summary line or with no photo credit. `_carousel_request` now credits every photo of its run from the
   run's ledger (`credits.required_credits`: the share-alike notice, every photo line, the adapted notice; more than
   the slides show, which is lawful) in place of the borrowed summary line. When the ledger cannot be read, a credit
   is incomplete, or the caption would pass 2,200 characters, the carousel is not posted (`CarouselCreditHold`). The
   hold offers Redraw only when a redraw can be made (the run has a carousel plan, QF-81); a run without one gets
   the alert of a carousel that will not post, with the reason. Measured: all 5 real carousels held this way (4
   development, 1 production sample) are runs made before plans existed, so none can be redrawn, and the alert
   says so instead of offering a button that would only answer that. A ledger that is there and cannot be read
   holds the carousel. (Round 2 credited a run folder with no ledger file from its own copy; round 3 holds it.)
2. **The ESA rule.** The agency's token is `ESA` in upper case, as a word, anywhere in an author or credit field
   ("Credit: ESA/ATG medialab", "DLR / ESA"); "Esa Virtanen" is a person. A credit that names NASA before it stays
   NASA's record. ESA/Hubble and ESA/Webb releases (hosts esahubble.org, esawebb.org, spacetelescope.org, or that
   author) are accepted when the record's licence is CC BY 4.0, as their own copyright pages state (ruled
   2026-11-14). esa.int media stay out unless the item is CC BY-SA 3.0 IGO, recognised by its name however it is
   spelled ("CC BY-SA IGO 3.0", "CC-BY-SA-3.0-IGO") or by its licence address.
3. **A true hold reason.** `credit_reach_problems` works out whether the missing credits would fit as the pipeline
   writes them (old lines rewritten, the short music line replaced, the summary line giving way). When they fit, the
   reason says so, says nothing needs to be cut, and names what writes them: re-sending the review or saving any
   Edit while it waits for review; a new version once it is approved or booked. It never says "Shorten" for such a
   copy. And a Resend writes the credits from the ledger before the card is sent
   (`PipelineCoordinator._recredited`; a second pass changes nothing on any of the 60 real runs), so only a run whose
   credits cannot fit stays held.
4. **An author printed "Unknown".** "Andrey Korchagin from St. Petersburg, Russia" was cut at the full stop of
   "St." (`credits._stated_author`); an abbreviation before a name (St., Mt., Ft., Pt., Dr., Mr., Ms., Mrs., Rev.,
   Prof.) no longer ends a sentence. A photo credit that still prints "Unknown" while the record's artist field says
   something else is held: "lacks its author (the source names one: check the credit, or Regenerate without this
   photo)". No QF-42 expected value changed.
5. Notes: the envelope's second check raises `ReleaseBlocked`, so a booking is blocked with its reason and "Make a
   new version / Cancel" (QF-110) instead of being tried again each pass; a face is "unmodified" only when its
   record says "copied unmodified". (Round 2's carousel refusal named Edit, which is not available then: round 3.)
6. The name scan (QF-86: no broadcaster or product name as a quality claim) found the refusal list, which must name
   the sources it refuses. Its five lines are exact approved exceptions in
   `QF-86/approved-name-exceptions.json` (file, text hash, text, reason); a word added to any of them is
   unexpected again (tested).

## Round 3 (review 2: three blockers and its notes)

1. **No ledger file and a slide without a record: held.** Round 2 credited such a carousel from its own copy, or
   sent it as it was. The real case (development, a master run with 7 slides and no `provenance.json`) carries one
   summary line naming six authors: no title, no source link, no licence link. The copy's own lines are never taken
   as the whole set, and a copy without any credit line cannot show that none is asked (ruling 1, 2026-11-14), so
   every such carousel is held: "its slides were drawn before slide records were kept, so it must credit every
   photo of its episode, and the episode's provenance record is missing". `_copy_photo_credits` is gone.
2. **A recorded slide whose credit entry cannot be read.** `photo_credit` returns nothing when `credits.json` is
   missing or corrupt, or a derived copy's lineage file is gone, and the photo was then skipped: the carousel went
   out without its credit. Now the photo is looked up in the run's ledger (`credits.ledger_photo_credits`) by its
   recorded path or checksum, through its lineage: the asset that is the photo, that the photo was made from, or
   that was made from the photo. The ledger's record decides: its credit line is required (with the share-alike
   notice for a BY-SA photo), or it asks for none (public domain, CC0, the run's own illustration, whose reference
   photos' credits are carried when a licence asks for them). This is the path of a carousel drawn at its slot too
   (QF-81): its slides always have records.
   **One point is stricter than the review wrote, on purpose.** The review said: when the photo is not in the
   ledger, credit the whole run's ledger or hold. Measured on the production sample's 18 carousels with slide
   records: 42 of their 123 slide photos are not in their run's ledger by path, most of them originals in the
   topic's photo folder that the Reel did not use. The run's other credits are no credit for such a photo. A known
   photo with no known licence is therefore always held ("the credit record of 1 of its photos (credits.json beside
   the photo) cannot be read, and the episode's provenance record does not hold that photo with a licence, so
   nothing says what credit its licence asks"); the same when the ledger is missing or cannot be read. Licence
   compliance comes before posts not held. With the credit files in place (development: 0 of 37 slide photos lack an
   entry) nothing is held by this. The literal form is one branch in `_carousel_request` (`if unread:`), should the
   lander rule for it.
3. **Credits that do not fit beside slide records.** The refusal said "shorten the carousel caption (Edit) at its
   review" and offered Retry. The review is approved and the Reel posted by then, and a retry or a redraw meets the
   same photos and the same caption. It is now a `CarouselCreditHold` marked final: `_post_photos` holds it as a
   carousel that will not post, Details only, whether or not the run has a plan: "N credit line(s) of its photos
   (... characters) do not fit its caption (... of 2,200 characters used). A credit its licence asks for is never
   shortened, left out or moved to a comment, and the caption was approved with its review and cannot be changed
   now. / This carousel will not post. Nothing was posted, and its Reel and other posts are not affected."
4. **Notes on truthfulness.** A credit hold's reason is said whole (other holds are cut at 400 characters; one real
   notice ended inside a clause). A hold names the photo ("photo “title” by author") instead of quoting its credit
   line with the link cut at 120 characters. "Regenerate without this photo" is no longer said of a carousel (its
   Reel is posted): `incomplete_credit(item, regenerate=False)`. A recorded slide's incomplete credit is a credit
   hold as well (it was a plain refusal with a Retry button, which a held carousel does not answer): Redraw when the
   run has a plan, with what must be done first ("Restore or correct the photo's credit record (credits.json beside
   the photo) first; a redraw then credits the photo from it"), else a carousel that will not post.
5. **measure.py** counts run folders without a ledger instead of skipping them (17 of 47 development folders, 2 with
   a carousel), measures carousels whose slides all have records, and builds each fault path of 1 to 3 in a
   temporary folder (`carousel_fault_paths`).
6. **Existing tests whose fixture runs had no ledger** get one (ruling 1): `tests/run_ledgers.py` (new) writes the
   `provenance.json` a rendered run has, recording the fixture's photos as public domain, which asks for no credit.
   It is called in five fixtures of five test files (listed under Tests). No assertion and no expected value of an
   existing test changed.

## Measurements

`python measure.py <repo root>` (no network, nothing written in the repository). `before.json` is 8d90acd5,
`after.json` this commit. A complete credit is decided by measure.py, not by the code under test.

### Posts made from the longest real credit sets

Three sets (tests/fixtures/qf126/real_credit_sets.json): the two longest of the development runs (10 photos; 7
photos with a share-alike notice and a music track) and the production sample's longest (9 photos and a track, as
line lengths only). Each as a Reel, a documentary, a carousel and a carousel whose slides have no record, whole and
cut down: 33 posts.

| | before | after |
|---|---|---|
| sent | 33 | 16 |
| sent with every credit whole in every platform's text | 4 | 16 |
| sent lacking a credit | 29 | 0 |
| sent with credits only in a first comment | 8 | 0 |
| held, with the reason | 0 | 17 |

The 6 carousels without slide records: sent lacking a credit 6 -> 0; 3 are sent with every photo credit of the
run in the caption (5 photos), 3 are held (the whole sets do not fit a caption).

The whole sets are 2,460 to 2,532 characters of credits: none fits a 2,200-character caption beside any text, so
each whole-set Reel is held. The same sets cut to 5 and to 3 photos are sent with every credit in the Instagram
and Facebook caption and in the Short's description. Each documentary carries every credit in its description; with
a 4,780-character description the 5,000-character cut removed all of them before (0 of 9 to 11 carried) and the
post is held now.

### The library's music credits (24 tracks, 17 ask for a credit)

| part of the line | before | after |
|---|---|---|
| title, author, licence name | 17 | 17 |
| link to the track | 0 | 17 |
| link to the licence | 0 | 17 |
| "edited" | 0 | 17 |
| complete lines | 0 | 17 |
| lines for a CC0 or public-domain track | 0 | 0 |

The longest line is 223 characters (86 before).

### Real runs (counts only)

`RR_REAL_OUTPUT=<folder of run_* folders> python measure.py <repo root>`; `real_before.json`, `real_after.json`.
Read only: nothing is written in the run folders and the gate is not run on them. Each run's stored copy is credited
again by the pipeline (a copy in memory), and the texts its platforms would receive are read.

| | development runs | production sample |
|---|---|---|
| runs / runs whose licences ask for a credit | 30 / 28 | 34 / 32 |
| credits asked for | 199 | 218 |
| stored copy: runs with every credit in every text posted | 3 | 0 |
| before: sent lacking a credit (credits lacking) | 25 (54) | 30 (63) |
| after: sent lacking a credit | 0 | 0 |
| after: sent with every credit in every text | 14 | 18 |
| after: held with the reason | 14 | 14 |
| ledger assets from an excluded source | 0 of 321 | 0 of 351 |
| stored copy failing the new check (runs waiting or booked at deploy would be these) | 25 | 30 |
| of them: told their credits fit and what writes them | 11 | 16 |
| of them: told to shorten or regenerate | 14 | 14 |
| of them: told to shorten though their credits fit | 0 | 0 |
| a second crediting pass changes the copy | 0 | 0 |
| photo credits printing "Unknown" from a filled artist field, before -> after | 2 -> 1 of 161 | 0 -> 0 of 174 |
| carousels with a slide without a record, in a run whose ledger asks photo credits | 20 | 11 |
| before: of them sent without every photo credit line | 20 | 11 |
| after: of them sent without every photo credit line | 0 | 0 |
| after: sent with every photo credit of the run / held | 16 / 4 | 10 / 1 |
| after: held ones a redraw can clear (the run has a carousel plan) | 0 | 0 |
| after: longest carousel caption sent | 2,074 | 2,046 |
| carousels whose slides all have records | 7 | 18 |
| their photos / photos whose credits.json entry cannot be read | 37 / 0 | 123 / 123 |
| of the unread: found in the run's ledger by the photo's path | 0 | 81 |
| before: such carousels sent lacking a credit their photos ask | 0 | 18 |
| after: sent lacking one / sent with every credit / held (a photo in neither record) | 0 / 7 / 0 | 0 / 2 / 16 |
| after: held though every credit is known, complete and fits | 0 | 0 |
| run folders without a ledger / of them with a carousel | 17 / 2 | 0 / 0 |
| before: those carousels sent (one with a summary line only, one with no credit line) | 2 | 0 |
| after: those carousels sent / held | 0 / 2 | 0 / 0 |

The production sample's slide records keep the paths of the machine that made them, which do not exist here, so
every credit entry of its 123 slide photos is unread: the fault of review 2's blocker 2, on real slide records. 81
are found in their run's ledger by path; the other 42 are originals the Reel did not use and derived copies whose
lineage files cannot be reached, so 16 of the 18 carousels are held here and 2 are sent with every credit. Where the
files are in place (development: 0 of 37 unread) nothing is held by this.

The stored copy is read as the release gate reads it (`publishable.payload_credit_texts`). The 11 and 16 runs told
their credits fit are exactly the runs that are no longer held once credited again (25 - 14 and 30 - 14). The one
"Unknown" left in development is a re-use note with no name in it; its credit is held for a person to check.

**What this means for posting.** 14 of 28 development runs and 14 of 32 production-sample runs carry more credit
text than a 2,200-character Instagram caption has room for (a per-photo line is 220 to 280 characters; a Reel with
7 to 10 CC BY photos needs 1,600 to 2,500). Those Reels were posted without part of their credits; they are now
held. A held Reel is cleared by a shorter caption (Edit) or by a Regenerate that uses fewer photos needing a credit.
Runs waiting for approval, or booked, when this is deployed are judged the same way. Bringing the held share down is
a design choice outside this row (for example a shorter credit form, or a per-run budget of photos that need a
credit when the pictures are chosen).

### The carousel's fault paths (round 3; built in a temporary folder, `carousel_fault_paths`)

| | before | after |
|---|---|---|
| no slide records and no ledger file (copy with a summary line only; with one of two photo lines; with no credit line): sent | 3 of 3 | 0 of 3 |
| a recorded slide whose photo the ledger holds, credits.json present / missing / corrupt: sent with the photo's credit | 1 of 3 | 3 of 3 |
| the same, sent without the photo's credit | 2 | 0 |
| a recorded slide whose entry is unread and whose photo the ledger does not hold, or the ledger is missing: sent | 2 of 2 | 0 of 2 |
| an unread entry of a photo that asks no credit (the run's own illustration; public domain): sent as it is | 2 of 2 | 2 of 2 |
| credits that do not fit beside slide records: posted, credit lines in a first comment | yes | no |
| the same: held as a carousel that will not post, saying nothing was posted and the Reel is not affected | no | yes |
| the same: its notice offers Retry or Redraw, or names Edit | - | no |

(Round 2's commit refused the last case with "shorten the carousel caption (Edit)" and a Retry button.)

### The small rules

| | before | after |
|---|---|---|
| generated tracks accepted into the library (of 2) | 2 | 0 |
| generated tracks picked from a hand-edited library (of 2) | 2 | 0 |
| a subset of IBM Plex still named Plex passes the font check | yes | no |
| the same subset renamed passes | yes | yes |
| a renamed subset without its copyright notice / its OFL notice passes | yes / yes | no / no |
| a Noto subset keeping its name passes (no reserved name) | yes | yes |
| bundled faces passing the font check | 9 of 9 | 9 of 9 |
| excluded sources refused: Commons search, museum search, rights decision, upload (of 3 each) | 0, 0, 0, 0 | 3, 3, 3, 3 |
| a BBC Sound Effects track refused by the music library | no | yes |
| ESA media marked CC BY-SA 3.0 IGO refused | no | no |
| lawful records refused by the ESA rule (ESA/Hubble, ESA/Webb under CC BY 4.0; "Esa Virtanen"; the IGO licence in four spellings and by address; NASA-first credits), of 14 | 0 | 0 |
| ESA records without a usable licence refused ("ESA", "Credit: ESA/ATG medialab", "DLR / ESA", esa.int, ...), of 7 | 0 | 7 |
| artist fields naming an author behind an abbreviation printed "Unknown", of 4 | 3 | 0 |
| a copy written before the rule whose credits fit: held / told to shorten / told they fit | no / no / no | yes / no / yes |
| the same copy after its review is sent again: held | no | no |

(Round 1's rule refused 8 of the 14 lawful records and let 3 of the 7 others through: review 1, blocking 2.)

## Tests

`tests/test_qf126_cc_by_credits.py` (46 tests): on 8d90acd5 41 fail and 5 pass (those guard what was already right:
CC0 tracks, a public-domain photo, the committed library, a recorded slide whose credit entry is read). 39 fail by
assertion or "DID NOT RAISE", the two font helper tests by ImportError (face_licence_problems, reserved_font_names).

Fixture ledgers (round 3; each is a comment and the two lines `from run_ledgers import write_ledger` and
`write_ledger(...)` in a fixture that builds a run folder by hand; no assertion, parameter or expected value
changed):

| test file | fixture | on origin/main | tests that are held without it |
|---|---|---|---|
| `tests/test_post_schedule.py` | `coord` | yes | 2 |
| `tests/test_qf81_lazy_carousels.py` | `coord.make` (used by the QF-81b, QF-81c and QF-73b files too) | no | 14 in its own file |
| `tests/test_qf81c_no_silent_slot.py` | `_drawn_booking` | no | 1 |
| `tests/test_carousel_window_2026_11_03.py` | `coord` | no | every test that posts its carousel |
| `tests/test_qf43_publishable_card.py` | `_carousel_coordinator` | no | 1 |

Branch-only expected value changed: `QF-86/approved-name-exceptions.json` gains five exact exceptions (the refusal
list's lines), read by `tests/test_qf86_photo_grade.py::test_name_exception_is_exact_not_a_marketing_allowlist`,
whose four cases behave as before (the clean tree passes, the three tampered trees fail).

## Known limits

- A face's "unmodified" status is what its licence record says ("copied unmodified"), bound to the file by its
  SHA-256; an upstream hash is not fetched.
- A carousel without slide records credits every photo of its run, which is more than its slides show. When that
  does not fit, it does not post: none of the real ones can be redrawn (no plan), and its caption cannot be edited
  once approved. Its slides may also show a photo its Reel did not use, which the ledger does not hold: the ruling
  of review 1 (the whole run's ledger credits, or held) stands for these old carousels.
- A carousel with slide records that shows one photo whose credit entry cannot be read and which the run's ledger
  does not hold is held, although its other photos' credits are known (stricter than review 2 wrote: Round 3, 2).
- The ledger is searched by the photo's recorded path and checksum and through lineage files, four steps back. A
  derived copy whose lineage file is gone is found only when the ledger holds that copy itself or records an asset
  as made from it; otherwise the carousel is held.
- A carousel held because its credits do not fit beside its slide records never posts (the caption is bound to
  its approved review). The shorter credit form of QF-126b makes this rarer; no real carousel is held this way.
- The carousel is checked when it is posted, not on the review card: its slides may be drawn only at its slot
  (QF-81). A carousel held for its credits keeps the day's carousel slot, as every carousel refused at posting does.
- A review whose card is already in the chat when this is deployed keeps its stored copy until it is sent again or
  edited (changing the copy under a card would break what Approve is bound to); the gate holds it and says how.
- Stories are not changed by this row (the frame's credit is queued as QF-126c).
- QF-86's own `after.json` records 4 name matches, as measured on its commit; with the refusal list there are 9,
  all approved exactly.
- Half of the real Reels still cannot carry their credits in 2,200 characters (14 of 28 and 14 of 32): the credit
  form is QF-126b's subject.
