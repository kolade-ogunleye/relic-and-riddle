# QF-65d: verify older runs' stills via provenance.json before holding them off Facebook

Follow-up from the QF-65b review (non-blocking item 1). Facebook photo posts and Stories cannot carry an AI label
(Upload-Post: Reels only), so QF-65b holds a Story frame or carousel slide that has no still record (a run made
before still records) off Facebook unless its run's video traces wholly to real media (decisions/QF-65b: fail
closed). The trace read each clip's lineage record, and older runs' intermediate clips (`parallax/`, `motion/`) are
often deleted. Real, credited photo posts were held only because those files were gone.

## The fix (`src/ai_label.py`)

The hold rule stays as it was. A scene that cannot be traced on disk is now checked against the run's
`provenance.json` (`_ledger_video`, `_ledger_scene`). The ledger is the record the pipeline wrote when it rendered
the video, and nothing is looked up anywhere else. A scene counts as real only when every link is bound by sha256:

1. The composition manifest is the one the ledger recorded. The manifest's checksum is in the ledger, and its master
   checksum matches the ledger's. This binds the manifest's per-scene checksums to the ledger.
2. The ledger's entry for the scene has the checksum the manifest records. If the clip is still on disk, it has
   those bytes.
3. The scene is one of this run's parallax clips (a camera move over one still, `src/parallax.py`) or our own
   graphic. Anything else stays untraceable.
4. Every picture the clip was made from:
   - has a recorded checksum;
   - is not an AI illustration;
   - if still here, has the recorded bytes and is not AI by its own record.

   At least one of them must still be here to check. A file recorded on another machine, or before the folder
   moved, is looked for at the same place under this app tree, and only its checksum decides that it is the
   recorded file.
5. The licence record names a free licence and the photo's source page. When the licence asks for credit, it also
   names the author and the title.

What stays held:
- AI media. QF-65b's rule is kept: a run whose video is AI keeps its record-less stills off Facebook.
  - The ledger records an AI motion clip just like a photo clip: `origin: source_media`, with the photo's credit.
    So a clip in `motion/` (named `motion_NN`, as `src/motion.py` writes them), or one made from a motion frame,
    counts as AI.
  - So does an AI illustration in the ledger, or a beat the picture check illustrated.
- Anything the ledger cannot bind: no ledger, an unreadable ledger, another manifest, other bytes for the clip or a
  photo, no photo left to check, or an incomplete licence record.

The operator sees the reason on the card and in `held_platforms`. Examples:
- "from a video showing AI media: AI motion clip (motion_01.mp4, in provenance.json)";
- "scene_09.mp4 is missing, and provenance.json cannot verify it: 07_commons.jpg here is not the recorded photo
  (checksum mismatch)".

A run with no video record stays held as before. Its reason now names the AI media its ledger lists, when there is
any.

Unchanged: Instagram's label for record-less stills (still on); stills with a record (judged by their own record);
runs whose clips are all here; every QF-65a path. Only `src/publisher.py`'s comment changes there.

## Measurements (`measure.py <repo root> [--runs DIR ...]`; before.json = parent 94b09f9a, after.json = this row)

### Fixture (built by the real lineage and ledger writers, clips then deleted)

Each case runs through `MetaPublisher.publish_photos` with requests recorded, as a Story and as a carousel.

- **Verified**, on this machine or with another machine's ledger paths: sent to Facebook 0/4 -> 4/4. Instagram is
  flagged 4/4 -> 4/4.
- **Must stay held:** 20/20 held before and after. The cases are an AI motion clip, an AI illustration moved by a
  parallax clip, an illustrated beat, the photo replaced since the run, no photo left, no ledger, an unreadable
  ledger, an edited manifest, other clip bytes, and an incomplete licence record. Each now gives its own reason.
  Before, all but the illustrated beat said "scene_01.mp4 is missing".
- `acceptance_met` false -> true.

### Real runs (read only, counts only)

`folder_1` is the development `output/`; `folder_2` is the production sample.

| | dev before | dev after | prod sample before | prod sample after |
|---|---|---|---|---|
| photo posts checked | 87 | 87 | 102 | 102 |
| with a still without a record | 66 | 66 | 45 | 45 |
| kept off Facebook | 24 | **6** | 45 | 45 |
| record-less, sent to Facebook | 42 | **60** | 0 | 0 |

- **Development: 18 of the 24 held posts are verified and posted.**
  - They come from 7 runs (Amber Room x3, Damascus steel, Greek fire, Richat x2).
  - Their stills were looked at on a contact sheet (not committed): every one is a real photograph or archive
    image of the subject.
  - The 6 still held have no video record:
    - 2 posts (one Kailasa run) whose ledger lists an AI motion clip;
    - 4 posts from two runs that have neither a manifest nor a ledger.
- **Production sample: none of the 45 is verified, and each now says why:**
  - 39 posts (13 runs) show an AI motion clip. Their `motion/` folder was not pulled, and before they read only
    "motion_01.mp4 is missing".
  - 3 posts (one run): the topic's photo `07_commons.jpg` now holds other bytes than the ledger recorded. The pool
    was refetched after the run. A name-based check would have taken it for the run's photo.
  - 3 posts (one run) have no video record. The ledger lists an AI motion clip.

So the production sample's 45 holds stand: 42 are AI media and 3 are a real hash mismatch. QF-65b's "the count there
will be lower" does not hold for this sample.

## Known limits

- A record-less still is judged by its run's video, as in QF-65b. Older runs draw their stills from the run's own
  photo set, and no still records which photo it shows. A still showing a photo the video does not use is not
  checked separately.
- Whether a gone clip is a parallax clip, a motion clip or our own graphic is read from where the manifest and
  ledger (bound by checksum) say it was written. `parallax/scene_NN` and `graphics/graphic_` are accepted, and
  `motion/` or `motion_` counts as AI. That clip's own lineage record is gone. The ledger keeps only that record's
  checksum, not what it said.
- The ledger is the pipeline's own record. It is trusted as written, as the release gate trusts it.
