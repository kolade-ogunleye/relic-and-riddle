# QF-57: source rights evidence

A generic public-domain label no longer clears a photo or a film. A public-domain record needs a worldwide basis:
evidence that it is free everywhere, not only in the US or only by its author's life term. The rule is the
operator's ruling (decisions/QF-57.md, Option A, confirmed by the operator on 2026-11-10):

- CC0, and Commons' CC-Zero category (a worldwide waiver).
- Author dedications: PD-self, PD-user, PD-author, PD-author-FlickrPDM.
- US federal works (17 U.S.C. 105) whose credit names no third party (ESA, CNES, JAXA, CSA, "courtesy", "third
  party", as whole words): PD-USGov, PD-USGS, PD-CIA, and NASA (PD-NASA, PD-USGov-NASA, checked first).
- A life term (PD-old-70/80/90/100/auto, "author died more than N years ago", or a Wikidata death date under
  death_year + 70 < this year) together with a US basis (PD-US-expired or PD-old-N-expired). "PD-old-auto: no death
  date" proves no life term.
- An archive.org film the operator approved with "use" (archive-film-operator-approved). The film card says so:
  "Using a film records your approval as its worldwide rights basis". The basis is read from the film's asset record
  and carried into the run's footage credit (which rewrites the note), and a film without one is not used at all.
- Uploads: the wizard offers no "Public domain" button (an upload carries no recorded worldwide basis, so it would be
  held); its prompt says to choose CC0 when the source waives its rights.

Anything else stays held: a US-only tag, a life term without a US basis, PD-algorithm, a bare label.

## Where the rule applies

- Fetching: `image_rights.commons_basis` records the basis on each new Commons candidate. Without a life-term tag it
  looks up the creator in the file's structured data (Commons MediaInfo answers carry `statements`, P170) and the
  creator's death date on Wikidata (P570: preferred rank first, deprecated skipped, year precision or finer). Both
  lookups are cached per item and run only when a US basis is present.
- Stored records: `image_rights.basis_from_record` reads the same rules from what a record already carries (its
  Commons categories, licence, credit, or the operator's recorded approval of a film). `effective_basis` takes a
  stored basis first. So existing dev and production records pass or fail the same way with **no migration step**:
  a release does not depend on anyone running a script on production.
- Release: `credits.rights_evidence_ok` refuses a held record, and the provenance ledger records the effective basis.
- Photo selection: `image_review.tier` makes a held photo weak, even one you kept, so a video never picks it and a
  run is not rendered (and paid for) only to be blocked at release.
- Released runs are grandfathered: the release gate reads the rights status recorded when the run was rendered, and
  nothing re-checks a released run. The rule applies to new renders.
- `scripts/migrate_legacy_pd_records.py` is an audit (dry run; `--apply` writes the derived bases into the records,
  atomically, with LF). It uses the same function, so it cannot disagree with the release check.

## Measured

Run `python docs/reviews/quality-fixes-2026-11/QF-57/measure.py <repo root> [<assets dir>]`. It prints JSON, writes
nothing and makes no network call. The fixtures come from `fixtures.py` beside it, so it runs on the parent too.
`before.json` is the parent (bfb090d) on the tracked dev pools, `measurements.json` this row; the
`*_production_sample.json` pair is the same on a copy of the production sample (C:\tmp\prod-export-data, read-only),
kept outside the repository and reported as counts only.

| | parent bfb090d | QF-57 |
|---|---:|---:|
| Fixture: bare PD / US-only / life term only admitted | yes / yes / yes | no / no / no |
| Fixture: life term + US / auto-expired / US federal / CC0 admitted | yes / yes / yes / yes | yes / yes / yes / yes |
| Bare PD passes the release check | yes | no |
| Openverse results / provider calls while disabled | 1 / 1 | 0 / 0 |
| Revoked Flickr result kept / valid Flickr result kept | 1 / 1 | 0 / 1 |
| Dev pools: PD records passing the release check | 72 of 72 | 50 of 72 |
| Dev pools: ready for a video | 17 of 31 | 17 of 31 |
| Production sample: PD records passing the release check | 42 of 42 | 34 of 42 |
| Production sample: ready for a video | 14 of 16 | 14 of 16 |

No ready pool becomes unready, on dev or production. Topic selection does change: `topic_engine.authoritative_images`
counts topic and kept photos, and a topic with fewer than `TOPIC_MIN_AUTHORITATIVE_IMAGES` (8) is moved behind the
others (not dropped). On dev, Wieliczka falls from 8 to 5 and is demoted. Others lose photos but stay at or above 8
(Amber Room 18 -> 16, Voynich 18 -> 16, Roman concrete 12 -> 11, Colossi of Memnon 16 -> 10); Greek fire (2 -> 1)
and Tunguska (1 -> 0) were already below 8.

The held records (dev files listed in `measurements.json`; the production sample as counts only):
- dev, 22: Colossi of Memnon 6, Tunguska 4, Wieliczka 3, and 1-2 in Amber Room, Flannan Isles, Greek fire,
  Roman concrete, Voynich and Zhang Heng (Nazca 04 passes by its CC-Zero category);
- production sample: 8 held in 4 pools; no ready pool becomes unready.

By recorded evidence, the dev holds are: 7 with no rights category recorded, 3 PD-algorithm, and the rest a life
term without a US basis (CC-PD-Mark with PD-old-80, PD-Russia-1996, "PD Old") or a US basis without a life term.

## Openverse

The fetch script calls the shared Openverse provider; direct requests, explicit source lists and cached paths all
honour `MUSEUM_OPENVERSE_ENABLED`. Flickr candidates need an optional `FLICKR_API_KEY`, an exact photo ID match
between source page and image URL, a public photo, and a current permitted licence from `flickr.photos.getInfo` and
`flickr.photos.licenses.getInfo`; without a key, Flickr results are held. The key is masked in logs. Commons results in
Openverse are rechecked against the exact file. Accepted records carry `license_verified_at`. No environment file,
feature flag or spending setting is changed. NASA records crediting another agency are refused; LoC keeps its recorded
non-US-limited advisory; Europeana's generic PD mark stays held.

## References

- [Commons PD-old-70](https://commons.wikimedia.org/wiki/Template:PD-old-70): life term is not US publication.
- [Commons PD-Art](https://commons.wikimedia.org/wiki/Commons:When_to_use_the_PD-Art_tag): 2D reproductions only.
- [Wikidata P570](https://www.wikidata.org/wiki/Property:P570): date of death, with rank and precision.
- [NASA media guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/): NASA vs third-party material.
- [Flickr getInfo](https://www.flickr.com/services/api/flickr.photos.getInfo.html) and
  [licenses.getInfo](https://www.flickr.com/services/api/flickr.photos.licenses.getInfo.html).
