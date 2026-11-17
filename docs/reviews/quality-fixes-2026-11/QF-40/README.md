# QF-40: Tier-gated corroboration and signed evidence provenance

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2 (STOP-SHIP). Finding: one Wikipedia
edit plus one self-hosted page it cites made a false number "strong". `_apply_rules` counted any two hosts as
two sites, `free_evidence` fetched every `extlinks` page as evidence, and the HMAC then signed the result.

## Change

- `src/fact_sheet.py`: `corroborating_sites(evidence)` counts tier 1-2 quotes only, each site once (any
  Wikipedia language is `wikipedia.org`), and never a page reached through a Wikipedia article's links.
  `strongly_sourced(kind, evidence)` replaces the old `two_sites` test. A number needs a tier-1 source or two
  corroborating sites, otherwise it gets a hedge. A date needs a tier 1-2 source or two corroborating sites.
  Every evidence quote is stored with its provenance (`host`, `tier`, `fetch`: `wikipedia`,
  `wikipedia_cited`, `openalex`, `unesco_registry`, `web_search`). `load()` applies the rule to sheets built
  before it, so a cached number left without two corroborating sites is used only with its hedge. The file on
  disk is unchanged and no research is re-run.
  `sources_line(sheet)` gives the review-card line, for example "sources: Wikipedia + 1 site it cites".
- `src/free_evidence.py`: a page fetched from an article's `extlinks` is stamped `tier: 3,
  fetch: "wikipedia_cited"`, even on an allowlisted host. Article and OpenAlex quotes record their fetch path.
- `src/card_fields.py`: `findings` (which `check_field` and `verify_field` both apply) refuses a quotation
  field, or a statement or myth correction that carries a figure or rests on a `number` fact, when its fact's
  evidence has no tier 1-2 site besides Wikipedia and the pages it cites. Years and centuries do not count as
  figures here, because the existing date rule lets a date rest on a reference work. On a `date` fact the
  whole date expression is no figure: a day of the month, a BC/AD/BCE year of any size, a time of day, a
  century, "N years ago" and ranges of these (review round 2). A quantity on a date fact ("in 36 hours",
  "40 of 120 skulls") still needs the second site. The provenance is part
  of the facts, and the signed card record binds the facts through `facts_sha256`. Editing a quote's
  `fetch`, `tier` or `host` after signing makes `verify_field` refuse the field (`stale_check`).
- `src/script_engine.py`: the review card's fact-sheet note ends with the sources line.
- `README.md`: the evidence-rule sentence now states the tier gate.

## Metric (`python measure.py <repo root>`)

The real path runs with the network mocked: `free_evidence.quotes_for` fetches the Wikipedia article and the
page its extlinks name, then `fact_sheet._apply_rules` runs. Card fields are signed through `check_field`
with mocked checkers and verified by `verify_field`, under a throwaway key (no real key is read).

| | before (a4b4ccb) | after |
|---|---|---|
| Wikipedia + attacker page with one figure: `disputed` | false (strong, no hedge) | true, "by one estimate, ..." |
| Wikipedia + cited `.gov` page: `disputed` | false | true (the cited page inherits tier 3) |
| evidence provenance (host, tier, fetch) stored | no | yes |
| `verify_field`, number statement on Wikipedia + cited page | accepted | refused (`unsupported_field`) |
| `verify_field`, quotation whose only tier 1-2 host is Wikipedia | accepted | refused (`unsupported_field`) |
| controls, the same fields with a tier-1 site added | accepted | accepted |
| provenance edited after signing | refused (`stale_check`) | refused (`stale_check`) |
| review card sources line | none | "sources: Wikipedia + 1 site it cites" |
| date-fact statements on Wikipedia alone (9 date expressions: "30 June 1908", "9600 BC", "AD 79", "7:14", "8th to 7th century BC", ...) | accepted | accepted |
| quantities on a date fact on Wikipedia alone ("36 hours", "40 of 120 skulls") | accepted | refused (`unsupported_field`) |
| shipped undisputed date facts whose statement would be refused on Wikipedia alone | 0 | 5, each with a non-date quantity (36 hours, Chapter 56, 3D, 100 sites, 40 of 120 skulls) |
| acceptance_met | false | true |

Effect on the 20 shipped sheets (`data/fact_sheets`, applied at load): one fact is newly hedged,
`richat_structure_sahara_eye` fact-7. It is a number cited only by Wikipedia and a tier-3 DOI. No other fact
changes. The sheets predate provenance, so their cited pages cannot be told apart from other hosts, and the
sources line counts them as "other sites".

Files: `before.json`/`before.txt` (start commit), `after.json`/`after.txt`, `measurements.json` (last run).
Round 2: round 1 treated only years 1000-2099 and centuries as non-figures, so a date fact's day of the
month, BC/AD year or clock time counted as a figure. Measured with the same scan on round 1, 27 shipped
undisputed date facts would have been refused as card statements on Wikipedia alone, against 5 now.

Regression test: `tests/test_qf40_tier_gated_corroboration.py`. On the start commit 10 failed and 11 passed
(the controls, the provenance-binding test and the date statements, which the start commit never gated).
On round 1's code the new date tests failed (7 failed, 14 passed). On the fix all 21 passed.
