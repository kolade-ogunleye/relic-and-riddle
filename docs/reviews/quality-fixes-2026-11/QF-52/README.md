# QF-52: crop-aware photo admission and essential-image exceptions

Automatic full-frame photos need a decoded, upright 9:16 crop of at least
1080 pixels. At most 10% of a new automatic pool may be below1350 pixels,
including incomplete pools: a lower-headroom photo needs nine native photos
already present. Ranking uses usable crop width with a10% portrait/square bonus
after licence and relevance. An original up to the existing download cap is
preferred when its preview discards needed crop height. Metadata cannot bypass
the decoded-pixel check.

The operator approved essential-image exceptions on2026-11-06. These are explicit
designations, distinct from ordinary photo Keep and from automatic pool metrics.
They retain the complete image at native size or smaller. Automatic upscaling,
detail/text crops, AI motion, parallax and loop-bridge zoom cannot replace them.
Colour balancing and privacy blurring retain the presentation constraint through
checked lineage. A cached crop or unreviewed enlargement of a newly designated
source is refused, so preparation must restart from the source; it never silently
returns an unblurred original. Presentation changes invalidate render caches.

## Designation and visual review

For a curated fetch specification, add exact source titles and reasons:

```json
{"essential_images": {"File:Example inscription.jpg": "Only surviving view of the inscription"}}
```

These titles are searched first, retain the normal rights/relevance checks and
can be added to an already full pool. Accepted original response bytes are kept
as`<photo>.source` alongside the upright JPEG. Credits record the exception and
archived source. A source that was already downloaded keeps its existing bytes;
this does not claim to recover an earlier discarded original response.

For an existing local source, the operator can run:

```text
python -m src.image_presentation path/to/photo.jpg --reason "Only surviving view"
```

Seek a better original first. If an enlargement is still needed, prepare a
separate whole-image derivative, inspect it at delivery size, then explicitly
record the review:

```text
python -m src.image_presentation path/to/photo.jpg --reviewed-derivative path/to/reviewed.png --visual-review "Checked the full frame and inscription at delivery size"
```

Review binds the exact source and derivative bytes. Replaced files require a new
review. The derivative must retain the source aspect ratio, and the operator's
visual check must confirm that it contains the complete image and no misleading
invented detail. Pixel enlargement cannot guarantee faithful recovery of missing
detail. The pipeline performs no new automatic enlargement of an essential asset.
Full-image fitting also applies to Stories/carousels and cover photo helpers;
ordinary covers retain their existing layout.

## Reproduce

```text
python docs/reviews/quality-fixes-2026-11/QF-52/measure.py <repository-root>
```

Only the repository root is accepted. The harness generates48 dimension fixtures
and all response bytes, with mocked searches/downloads and no service calls.
This measures admission and geometry, not photographic quality or a new audit of
the original243 historical pool records. `before.json` was run on parent01ad8ca;
`measurements.json` records the candidate source commit.

| Synthetic pool metric | Parent | Candidate |
|---|---:|---:|
| Automatic photos admitted |47|10|
| Automatic crop below1080 |22|0|
| Automatic crop below1350 |38 (80.85%)|1 (10%)|
| Minimum automatic crop width |843|1181|
| Explicit essential exception retained |0|1|
| Original exception response bytes archived |No|Yes|
|200x150 source in1080x1920 and1920x1080 |Unsupported policy|200x150, all30000 source pixels present|

The new regression files test ordinary rejection, decoded dimensions and EXIF,
partial-pool quota, original-versus-preview choice, rights gates for exceptions,
exact reviewed derivatives, privacy preservation, stale cached crops, cache
invalidation, crop/motion guards, social/cover helpers and the compositor graph.
The real FFmpeg geometry checks preserve native extent in both delivery shapes.
All original assertions in`test_image_ranking.py` remain unchanged: ranking
fixtures now meet the new floor, and fetch mocks return decoded dimensions.
The old843px case has its own explicit rejection regression.

Validation so far:71 focused passed;141 covering passed with one existing
development-photo-dependent skip;31 final focused/identifier tests passed.
All19 new tests failed on the parent in20.84s, including the already-full-pool
case. Ruff has no new findings in any changed file. No tests were skipped or weakened by
this change. Final independent review and landing full suite remain required.
