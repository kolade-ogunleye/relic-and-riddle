# QF-55: guard category fallback

Category fallback used to search the first discovered categories directly, even
when a category named a film or other namesake. It now asks `commons.topic_anchor`
for the same ordered-name, qualifier and place checks used by beat searches. An
exact subject QID with a P373 category (QF-54a's verified Wikidata anchor) supplies
that anchor directly. No category anchor means no category fallback; normal
searches retain their existing gates.

Every new fetch requires a verified subject (a curated QID, or the single Wikidata
item of the topic's exact English Wikipedia page) or a nonempty list of namesake
exclusions. A complete existing pool does not need a fresh search. Identity is never
inferred from a first search hit, and exclusions are never invented.

## Rebuilt on QF-54a (2026-11-10)

The first build sat on the unsplit QF-54. On QF-54a's base:
- No data change. The first build appended namesake phrases to two topics'
  `exclude` lists, on top of exclusions QF-54a removed (review 1 of QF-54a: substring
  excludes such as "roma" refuse on-topic files). They were never measured on real
  records, and the identity rule no longer needs them: QF-54a gives all 31 curated
  topics a verified QID. `data/topics/image_queries.json` is the parent's.
- Topics added in Telegram (`control.add_topic`, `topic_proposer.add`) previously had
  neither a QID nor exclusions, so the identity rule would hold every one of their
  fetches with no way to configure one from the bot. They now carry
  `wikidata_title`: the English Wikipedia page a proposal cites, else the topic's
  title. A fetch resolves it live through the exact sitelink (QF-54a's
  `entity_anchor`, the same parser as the snapshot). A title that lands on a
  disambiguation page (P31 Q4167410) is no subject. A topic whose title resolves to
  no single item is held with a message that names the title and the upload route
  (`/photos N upload`). The hold is recorded in the pool (`fetch_held`), so the
  automatic path's `asset_pipeline.not_ready_reason` shows it instead of "the fetch
  found nothing new", and a later verified fetch clears it. The command-line fetch
  of every topic reports a held topic and goes on to the next one.
- Limitation: a topic added by a title that is not an exact English Wikipedia page
  title (the bot's own example, "The Lost Labyrinth of Hawara") resolves to nothing,
  and its fetch is held until photos are uploaded; so is any new topic while
  Wikidata does not answer. No current topic is affected: all 31 curated topics have
  a QID, and no topic in the production sample was added in the bot.

Run `python docs/reviews/quality-fixes-2026-11/QF-55/measure.py <repo root>`.
The only input is the root. The committed regression fixture generates JPEGs and
ten metadata catalogues; each offers an unrelated film category before the actual
subject. It runs the production fetch/download/credit path with network and
storyboard calls mocked. Copy the measure and regression file to the parent when
measuring it (`before.json` is that run on 5b9f49e). No ignored assets, provider
API, or model call is used.

| Metric | Parent 5b9f49e (QF-54a) | Candidate |
|---|---:|---:|
| Category-fallback fetches | 10 | 10 |
| Selected photos | 10 | 10 |
| `shows_topic: no` fixture labels | 10 | 0 |
| Curated topics with a verified QID or exclusions | 31 | 31 |

These labels are **fixture truth**, not a live visual-model evaluation or a claim
about photographic merit. The experiment establishes the namesake rejection path;
independent fresh-photo evaluation remains a separate audit.

All 25 regression cases fail on the parent (15 from the first build; 10 for the
new-topic identity path: the disambiguation guard, the sitelink title from `/add`
and from a proposal's cited page, `topic_proposer.add` still writing inside the
topic-bank lock, the recorded hold, and the command-line loop). Existing assertions are
unchanged: fixture specifications in `tests/test_image_ranking.py`,
`tests/test_qf52_crop_floor.py`, `tests/test_qf52_essential_images.py` and the
QF-52/QF-53 measurement harnesses gain an explicit exclusion so they meet the new
rule (they have no QID). No tests were skipped or thresholds changed.
