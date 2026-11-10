# QF-54a: structured searches from a verified Wikidata item

QF-54 was split by the operator (decisions/QF-54.md, 2026-11-09). This part, QF-54a, adds searches: six or more
curated shot searches per topic, plus searches from the curated Wikidata item (its depicts statement, its Commons
category, and its label in the topic's own language). It does not change which files are admitted. Every topic's
`queries`, `keywords` and `exclude` are the parent's, value for value (a test checks all 31 against
`before.json`); the shot plan is a separate field, `shot_queries`, and no Wikidata name becomes a title keyword.
Admitting a file by its category or depicts statement is QF-54b, a separate row.

Run: `python docs/reviews/quality-fixes-2026-11/QF-54a/measure.py <repo root>`. It prints JSON, writes nothing, and
exits non-zero when a candidate has no label or the plan differs from the recorded one. Inputs:
- `before.json`: the parent's `data/topics/image_queries.json`, the base plan.
- `data/topics/wikidata_snapshot.json`: Wikidata's answer for every curated QID, recorded 2026-11-10 02:42 UTC by
  `record_wikidata_snapshot.py` in one read-only, free request. Each topic keeps the entity as its own request
  returns it (English plus its languages); of the claims, only P373 is kept, the one the parser reads.
- `commons_sample.json`: real Commons results for five test topics, recorded the same night by
  `record_commons_sample.py` (base abe381a): the fetcher's own `search_graded` and `search`, 50 results each,
  for every base and every QF-54a search; 577 pages, 55 searches; no image downloaded.
- `candidate_labels.json`: a shows_topic label for each of the 290 files either plan admits, by
  `label_candidates.py` from title, description and Commons categories (not pixels): "yes" in the topic's own
  category tree, "partly" for an explanatory panel or reconstruction, and every other file read individually
  (6 "no": other Ellora caves, the Waghora waterfall, a modern map derived from the Voynich manuscript; 2 "partly":
  figurines found in the hypogeum).

## The plan a run uses is the plan measured

`src/photo_queries.py` reads the committed snapshot for a curated QID and asks Wikidata live only for an item not
recorded there (a new topic). Both go through `anchor_from_entities`, one parser. So every run plans the same
searches, measured here, whether or not Wikidata answers. That matters: on 2026-11-09 Wikidata refused every read
for over 40 minutes (its query-service lag, about 12 s, counts against the fetcher's maxlag of 5), which would
have silently removed the identity searches from a live plan. The snapshot was therefore recorded without maxlag,
which the API asks of bots that edit; it was one read, by hand.

## Results

| | base | QF-54a |
|---|---|---|
| topics with a curated QID whose recorded entity names the topic | 0 / 31 | 31 / 31 |
| Commons searches, all 31 topics | 89 | 356 (58 identity, 27 local-language) |
| the base searches run first, unchanged | | yes |
| searches each museum source receives (Met, NASA, Smithsonian, Europeana when keyed; Openverse, Library of Congress, Art Institute when enabled) | 89 | 271 (the hand-written ones; never the identity searches) |
| Commons requests per fetch pass, all 31 topics (two per search) | 178 | 712 |
| the most for one topic: Commons requests / requests to each museum source, per pass | 20 / 10 | 36 / 15 |
| `queries`, `keywords`, `exclude` equal to the parent's, all 31 topics | | yes |
| real pool records whose title matching changes | | 0 gained, 0 lost |

On the recorded real sample (five topics):

| | base | QF-54a | ratio | shows_topic "no" |
|---|---|---|---|---|
| open licences (the fetcher's first pass) | 79 | 113 | 1.43 | 0 -> 0 |
| with share-alike (second pass, when short) | 157 | 290 | 1.85 | 2 (1.3%) -> 6 (2.1%) |

| Topic | open | with share-alike | new "no" |
|---|---|---|---|
| antikythera_mechanism | 9 -> 9 | 23 -> 29 | 0 (the 6 new: 4 explanatory panels, "partly") |
| hal_saflieni_hypogeum | 7 -> 7 | 18 -> 18 | 0 |
| kailasa_temple_monolith | 62 -> 95 | 113 -> 238 | 3 (Caves 10 and 18) |
| voynich_manuscript | 1 -> 2 | 1 -> 3 | 1 (a modern map) |
| zhang_heng_seismoscope | 0 -> 0 | 2 -> 2 | 0 |

No base candidate is lost on any topic. In both passes, the review drops "no" photos before a pool is used.

## What this does not achieve

The QF-54 acceptance ("on 5 test topics at least 2x candidates with no rise in shows_topic: no") is not met by
searches alone, and is not claimed here. Only Kailasa reaches 2x. With share-alike there are 4 more "no" (2.1%
against 1.3%). Most real files for these topics carry a code name or an inventory number, not a keyword, so the
unchanged title rule refuses them. And 21 of the 45 new searches on the sample return no result at all
(`new_searches_with_no_result`: all 6 Antikythera shot searches, 7 of 9 for Zhang Heng): long shot phrases find
little on Commons, so most of the gain comes from the identity and local-language searches. Each search still costs
its requests: a refill pass asks Commons four times as often as before (712 against 178 requests for all 31 topics),
and a share-alike pass doubles that. Request time was not measured, because measure.py makes no live calls. Admitting by
category or depicts is QF-54b's job, measured on this same sample. The 4 new "no" files were not excluded by name:
that would tune the data to the measurement sample.

## Round 1 review, answered

Round 1 (cb5ae9a) carried title keywords and exclusions from an earlier build, and turned Wikidata aliases into
title keywords, while saying admission was unchanged; and its snapshot was a flattened summary, so the measured
plan was not the live one (348 against 353 searches). Both are removed: admission fields are the parent's, no
alias is a keyword, and the snapshot is the raw answer, read by runs themselves. The local-language search is now
the item's label in the topic's language (Hal Saflieni: "L-Ipogew ta’ Ħal Saflieni"), not the first non-ASCII
alias. Because `queries` is unchanged, `beat_images._other_names` (the anchor-category lookup) is unchanged too.
