# QF-73: topic bank hooks, sources, regions, sensitivity spacing, proposals and lint

Round 6 (review 5, operator decision A, "one source of truth"): whether a carousel has a slot left is decided once,
by the carousel plan itself, for the screens, the Needs-you alert and the expiry record, and Post anyway secures
the slot it names. See "Round 6" below. Round 5 (operator decision A on review 4): a carousel that sensitive-topic
spacing holds back is said, never left to expire silently. See "Round 5" below. Round 4 (scoped, review 3): carousels keep the sensitive spacing, three
hooks get their facts' hedges back, and the nightly research is capped at the weekly run it replaced. See "Round 4"
below. Round 3: every data file was rebuilt
from this commit's parent (d9d623f), adding only this row's fields; no data file was copied from an earlier round.
`parent_entries.json` records the SHA-256 of every parent entry, and
`tests/test_qf73_topic_bank.py::test_pre_existing_data_entries_equal_the_parents` checks them: all 32
`image_queries.json` entries are unchanged (QF-54a's 31 QIDs and QF-55's keywords included), and every pre-existing
bank entry is unchanged outside `hook` and the dropped `#ReelsViral`. A later row that changes an entry on purpose
updates its digest there, in the open. `tests/test_qf54a_photo_search.py` and `tests/test_qf55_category_anchor.py`
pass unchanged.

Run `python docs/reviews/quality-fixes-2026-11/QF-73/measure.py <repo-root>` (sockets refused, $0 spend). It reads
the committed bank, image searches, fact sheets and parity record, and builds throwaway databases for the booking,
claim, card, carousel, intake and nightly checks. `before.json` is the parent, `measurements.json` this commit,
`round3.json` the round-3 commit (2d6892b), `round4.json` the round-4 commit (a81e049) and `round5.json` the
round-5 commit (de16504) measured by this script (round3.json predates the round-5 and round-6 keys).

| Measure | Parent | This commit |
|---|---|---|
| hooks passing `hook_problems()` | 0 / 31 | 39 / 39 |
| topics with a public HTTPS source | 1 / 31 | 39 / 39 |
| largest region group | unclassified 31 / 31 | Europe 12 / 39 (30.8%) |
| Sub-Saharan Africa, Mesoamerica, Southeast Asia, Oceania | 0 | 2 each |
| `#ReelsViral` tags | 30 | 0 |
| `topic_corrections.json` | present | removed |
| adjacent or same-day sensitive bookings refused | 0 / 3 | 3 / 3 |
| 12 bookings in a row, 8 sensitive: sensitive pairs on consecutive days | 7 | 0 |
| two sensitive claims at once: claims granted | 2 | 1 |
| lint fails on an entry without sources | no lint | yes |
| bank lint problems | no lint | 0 |
| sensitive carousel posted next to another sensitive Reel, 3 cases (round 3: 3) | 3 | 0 |
| sensitive carousels posted at a spaced slot, 3 cases (round 3: 0) | 0 | 1 |
| plain carousels posted in the same 3 cases (round 3: 1) | 1 | 3 |
| hooks that drop a hedge their key facts keep (round 3: 3, by this check) | no check | 0 |
| nightly research starts in 14 nights below the floor (round 3: 14) | 2 (weekly Friday job) | 2 |
| sensitive carousels that expire, 3 cases (round 4: 2) | 0 | 2 |
| of those, expired silently: no alert naming the spacing, or no reason on the booking (round 4: 2) | 0 | 0 |
| expired with the reason "sensitive-topic spacing" on the booking (round 4: 0) | 0 | 2 |
| spacing alerts in a case, at most (round 4: 0) | 0 | 1 |
| review 5's cases: carousels spacing or Post anyway kept from posting, of 6 (round 5: 4) | 3 | 4 |
| of those, expired silently: no Needs-you alert about it (round 5: 3, round 4: 4) | 3 | 0 |
| of those, expired with a wrong reason (round 5: 4, round 4: 4) | 3 | 0 |
| Post anyway replies naming a slot the carousel then did not post in (round 5: 1) | no button | 0 |
| Needs-you alerts about one carousel, at most (round 5: 1) | 0 | 1 |

## Round 6

Review 5 found two ways a carousel still vanished. (1) The spacing watch asked "is there a spaced slot?" (spacing
only), while the Posting plan also applies newest first. A carousel spacing held back on Tuesday and Wednesday, whose
spaced Thursday slot a newer carousel then took, got no alert, and `_spacing_holds` forgot the spacing record at the
spaced slot, so it expired "it waited over 7 days" with the Reel's rebook button. (2) Post anyway did not secure the
slot it named: the plan ordered by id only, two Post anyway carousels for one slot left one to expire silently, and
the plain carousel a Post anyway displaced was still promised the slot.

- One answer. `posting_view.carousel_outlook(db, now)` runs the slot loop's own rule over every waiting carousel's
  window: at each carousel slot (counted until its 3 h posting window closes, unless a carousel already posted that
  day) the eligible carousel of the highest rank takes it, priority first (QF-81's redraw, Post anyway), then the
  newest, as `main._post_carousel_if_due` sorts. Spacing refuses a sensitive carousel there unless Post anyway was
  chosen, and a sensitive carousel the plan posts counts on its day. It returns the plan and, for each carousel
  spacing or Post anyway keeps from a slot, its wait: kind ("spacing" or "slot"), the reason, the slot it lost and
  to whom, and its deadline. `carousel_plan` is its plan. Posting, the episode, the dashboard, Slots, `wont_post`,
  the watch, Post anyway and Let it expire all read this one answer.
- Spaced slot lost to another carousel. The wait reads "sensitive-topic spacing held it back, then a newer carousel
  (<episode>) takes its spaced slot, <slot>". The watch sends one Needs-you alert as soon as the plan gives it no
  slot (in the review's probe, Saturday, at the first pass after A's Reel posts), with Post anyway naming the slot
  the plan would then give it. Its booking keeps the spacing record (`waiting_for`, `spacing_why`) to the end, and
  the expiry records "sensitive-topic spacing" with that reason. The screens say "expired: sensitive-topic spacing
  held it back, then a newer carousel (...) took its spaced slot, <slot>": never "it waited over 7 days", "cancelled
  with the failed slot" or the rebook button.
- Post anyway secures its slot. `posting_view.post_anyway_outcome` asks the plan what the override would give. The
  reply names that slot ("posts at its next carousel slot, <slot>", or the later slot the plan gives). If a
  carousel you chose to post anyway earlier would lose its only slot, nothing is changed and the reply says which
  carousel holds the slot and when this one expires (the first choice keeps its slot). If it displaces a plain
  carousel, the reply names it. That carousel gets one "Carousel without a slot" Needs-you alert, its line reads
  "waiting: no carousel slot before its deadline", and it expires recording why ("the carousel of <episode>, which
  you chose to post anyway, took its carousel slot"). Two carousels that both carry Post anyway for one slot (the
  plan gives it to the higher rank) leave the other with one alert naming that carousel, never a silent expiry.
  Screens show the planned slot for a Post anyway carousel, never "after its Reel".
- Review 5's non-blocking items fixed with it: spacing that waits only for an unconfirmed sensitive publication
  raises no alert (the plan decides once it is known), and Post anyway inside the only slot's posting window takes
  that slot (the plan counts it, as the daemon does).
- Measured: the three cases above, run by the daemon's posting loop for 12 days. Of the 6 carousels spacing or Post
  anyway kept from a slot, 4 do not post here and on round 5. On round 5, 3 of them expire silently and 4 with a
  wrong reason, and 1 Post anyway reply named a slot the carousel did not get. Here: 0, 0 and 0, with at most one
  alert per carousel.

## Round 5

Review 4 found that a sensitive carousel spacing held back until its 7-day deadline expired silently: no alert, the
Posting line and episode screen still promised a slot, and after expiry the screens gave a wrong reason ("it waited
over 7 days", "cancelled with the failed slot"). Operator decision A: the wait is visible, one Needs-you notice
offers Post anyway and Let it expire, and the expiry records its true reason. The 7-day deadline is unchanged.

- Visible wait. `posting_view.spacing_wait` says whether spacing refuses a waiting carousel's next carousel slot,
  and finds its first spaced slot before its deadline. The Posting line reads "waiting: sensitive-topic spacing,
  earliest <slot>" (the slot the plan gives it) or "waiting: sensitive-topic spacing, no spaced slot before its
  deadline, <deadline>". The episode screen gives the same line, the reason and the deadline. With no slot left it
  also shows the two buttons. `wont_post` names the case when no spaced slot is left, and the dashboard's upcoming
  list uses the same words.
- One alert. `main._watch_carousel_spacing` runs at every pass of the posting loop (each minute), before the
  carousel-slot gate. As soon as a carousel's Reel has posted, its next slot is refused for spacing and no spaced
  slot is left before its deadline, it sends one Needs-you notice through the existing notify path (group "needs").
  The notice gives the topic, the reason, the deadline and the next slot, with "▶️ Post anyway" and "⏹ Let it
  expire". The booking records `spacing_alert`, so it is never sent again. It goes out at the first pass after the
  condition holds, usually when the other topic's Reel is booked or posts, days before the deadline. In the probe,
  it is the first pass after A's Reel posts (Monday 19:01, three days before its only slot, Thursday, and seven
  before its deadline). Ticking only the carousel slots, as measure.py does, it is Tuesday's slot.
- Post anyway (`carspace:<id>`, `main.post_carousel_anyway`) sets spacing aside for that one carousel
  (`spacing_override`) and puts it first (`priority`, as QF-81's redraw does) for its next carousel slot in its
  window. Every hold at the slot still applies, because the override only lets it into the ready list, and the
  QF-81, QF-81b and QF-81c checks run on that list.
- Let it expire (`carexpire:<id>`) expires it at once. Untouched, it expires at its deadline. Either way the
  booking records `{"reason": "sensitive-topic spacing", ...}` (with `"by": "operator"` when you chose it). The
  episode screen then reads "expired: sensitive-topic spacing left no carousel slot before its deadline" or
  "expired: you let it expire (sensitive-topic spacing)". The Reel's "cancelled with the failed slot" line and its
  rebook button are not shown for it.
- Measured over the same 3 carousel cases: 2 expire on round 4 and here, silently on round 4 (2 of 3) and with one
  alert and their reason here (0 of 3 silent).

## Round 4

Carousels. Review 3 found that a carousel posts with no spacing check: sensitive A's Reel on Monday, sensitive B's
Reel on Wednesday (Tuesday is clear), and A's carousel, due from Thursday (48 h after the Reel), went out the day
after B. At the carousel slot, `main._post_carousel_if_due` now leaves a ready carousel out when
`topic_problem(topic, slot, spacing_only=True)` is not empty. It stays waiting for a later slot inside its own
deadline (7 days after its Reel, unchanged), and the next ready episode takes the slot. When no spaced slot is left
before the deadline, it expires as any unposted carousel does. The Reel takes precedence over its own carousel. The
Posting plan (`posting_view.carousel_plan`) applies the same check, so the slot it names is the one the daemon takes.
In `spacing_problem`, every surface the audience saw counts: each Reel, Stories and carousel publication of another
sensitive topic, on its own day (round 3 took the latest one), and an uncertain (`unknown`) one counts on the day it
was tried. Stories count: they are a public post of the topic, shown for 24 h, and normally go out 45 minutes after
that day's Reel, so they rarely move a day; counting them is the cautious choice. Measured over 3 cases (A on
Monday with B booked or posted on Wednesday, and A on Saturday with B on Tuesday), A's carousel is posted next to B
3 times on the parent and on round 3, and 0 times now. In the third case it waits at Tuesday and Wednesday, then
posts on Thursday.

Hooks. Great Zimbabwe ("a nearly 80-hectare trading city", UNESCO: "nearly 80 ha"), Angkor ("some 400 square
kilometres", UNESCO: "some 400 km2") and Acoustic Kitty ("reportedly put a microphone in a cat", its key fact:
"reportedly implanted") keep their facts' hedges again, and still pass `hook_problems()`. The lint gains a general
check (`topic_policy.hook_hedge_problems`): a hook that states a number that every key fact giving it hedges, or
that states as fact what a key fact reports ("reportedly", "said to", "disputed"...), sharing two content words with
it, is a lint problem. Over the round-3 bank it finds exactly those 3 hooks, and none now.

Research spend. Round 3 made research nightly (up to 7 runs a week). `TOPIC_RESEARCH_RUNS_PER_WEEK` (default 1, the
weekly Friday run it replaced, same size: 3 ideas) now caps the nightly starts per calendar week (ISO week,
`DEFAULT_TIMEZONE`); a start counts whatever it finds, and 0 turns the nightly research off. Research you start
yourself is not counted, as before. Raising the cap raises spend: that is the operator's decision. In 14 nights below
the floor: 14 starts on round 3, 2 now. The tests mock the model.

## Hooks and facts

All 31 suggested hooks are rewritten as grounded questions within QF-14's rule (12 words, a signal in the first 8),
each answered by the topic's own key facts. `script_engine.hook_problems` is the check; the topic policy adds no
second word list. Following `decisions/QF-73-ingest-hook-check.md`, every topic needs one concrete fact (a number,
a scale or a contrast, found with `script_engine.hook_signal`'s own categories): 39 / 39 have one. A proposal card
shows whether its hook passes and whether a concrete fact exists. Add keeps a passing hook and drops a failing one,
so the writer opens from the facts. A manual `/add` is never blocked for a missing fact; its reply says research
will have to find one. Real proposals (the development queue, read-only, counts only): 22 waiting, 1 hook passes,
22 have a concrete fact, 0 viral tags. The existing key facts are unchanged. The roman-concrete seed's "40 times"
line is still in the bank: the fact gate checks facts before a script uses them, and editing a fact is not this row.

## Sources

- 20 topics cite the page that backs the most signed facts in their committed fact sheet (`data/fact_sheets`).
  Singing Colossi keeps the source it already had, which is also one of those pages.
- The 8 new topics cite their UNESCO World Heritage List record (`https://whc.unesco.org/en/list/<id>/`). The owner
  checked each record and its Wikidata item on 2026-11-08 (`photo_search_identities.json`).
- 11 topics without a fact sheet (Camp Century, Piri Reis, Flannan Isles, Heracleion, Zhang Heng, Archimedes'
  claw, Dyatlov, Gorgan, the 1518 dancing plague, the Baltic anomaly, Acoustic Kitty) cite the reference that the
  round-1 builder read on 2026-11-07. This round made no network calls, so these 11 were not opened again. They are
  research starting points, not checked facts. Re-check them when each topic's fact sheet is built.

The lint checks URL syntax and host only (public HTTPS, no credentials, no local address). It does not show that a
page exists, or that it proves a fact.

## The eight regional additions

Great Zimbabwe and Lalibela (Sub-Saharan Africa), Monte Albán and Palenque (Mesoamerica), Borobudur and Angkor
(Southeast Asia), Nan Madol and Rapa Nui (Oceania). Their key facts paraphrase each site's short description in
UNESCO's own List dataset (`https://whc.unesco.org/en/list/xml/`, read offline from the cached copy the application
already uses). Nothing else is added: the earlier rounds' "UNESCO maps" pages were not owner-verified and are
dropped, along with their component coordinates, claims about mortarless walls, and reservoirs. Rapa Nui's
settlement date is left out.

These eight have no photo searches yet. QF-54a requires a recorded Wikidata answer (`wikidata_snapshot.json`)
for every curated search, and fixes the curated count at 31. Recording an answer needs a network step, which this
round could not take. Each of the eight therefore carries a `hold`: production never ranks it, so neither the
run start nor the nightly preparation fetches photos or a fact sheet for it or reports it, and naming it with
`--topic-id` is refused with that reason. Nightly research
does not count it as queued. The verified QIDs are in `photo_search_identities.json`. To release a topic: record its
answer, add its search, then delete its `hold`.

## Sensitivity spacing

21 topics carry reasons (deaths, human remains, war, wartime looting, animal harm, living sacred practice). An empty
list means "assessed: none". A topic added by `/add` or proposed without an assessment counts as sensitive until
someone assesses it. Two sensitive topics never publish within `SENSITIVE_TOPIC_GAP_DAYS` (1) audience-calendar days
of each other in `POST_TIMEZONE`. Publications count here (including a partial one on a run that later failed), and
so do uploads in flight or unconfirmed, and booked Reels. Two runs of the same topic never space each other. The
check runs inside `BEGIN IMMEDIATE` for the publication claim, the booking and the move, so concurrent runs cannot
both pass it. Booking, Move and missed-slot rescheduling take the next safe day. Ranking leaves a sensitive topic
out while its day is not clear. The review card names the slot it would book. When no day is safe (another
sensitive upload is unconfirmed), the card says "Waiting: ..." and never "posted as soon as you approve". Both
review cards show the "⚠️ Sensitive topic · reasons" chip, which comes from the topic-origin note they both list.

## Proposals and intake

Nightly at `TOPIC_PROPOSAL_TIME` (03:30), and only when fewer than `TOPIC_QUEUE_FLOOR` (14) producible topics are
queued, research proposes `TOPIC_NIGHTLY_PROPOSALS` (3) ideas. It goes through the existing cancellable operator
job, providers and budgets, and you still Add each idea. This replaces the weekly Friday run, and starts at most
`TOPIC_RESEARCH_RUNS_PER_WEEK` (1) times a calendar week (round 4). The proposal prompt
asks for a rule-compliant hook, a concrete fact, a region group and a sensitivity assessment, and never
`#ReelsViral`. A proposal without a source is dropped at research. At Add, it is refused, nothing is written, and
it stays waiting. `/add Title | https://page` records that page. With no page, `/add` records the matched UNESCO
record, or else the English Wikipedia page of the title (the page QF-55 already verifies the photo subject by: a
title that is not an exact page holds the photo search). It makes up no hook or fact: key facts come from the UNESCO
description when a site matched. A non-HTTPS or local source is refused.

## Real data (counts only)

Topic ids are taken from each run folder's name, matched exactly, with legacy `NN_` prefixes reported separately.
Development output: 47 run folders, 46 exact matches and 1 legacy prefix, 0 unmatched. 34 have a `topic_id` in
their metadata, all 34 equal. Production sample: 34 folders, 34 exact, 34 metadata checks, all equal. The
development database's 31 topics are all in the bank. Production-sample runs whose topic is now sensitive: 8 of 34.
The sample has no publish times, so historical spacing is not measured.

## Downstream effects

Production order changes: `calculate_vps` scores the hook text, and questions score differently from the old date
openings. The database gains an `editorial_json` column (additive). Each topic dict also carries its sources,
sensitivity, region group and hold. The schedule line has a "Waiting" form. A claim refused for spacing says why.
The weekly Friday proposals job is gone; the nightly one starts at most once a week by default. CI runs
`scripts/lint_topic_bank.py`. Round 4: a sensitive carousel can wait or, with no spaced slot before its deadline,
expire, and the Posting screen shows the slot it will take. Round 5: that wait is shown on Posting, the episode and
the dashboard, and a carousel with no spaced slot left raises one Needs-you notice (two new callbacks, `carspace` and
`carexpire`, both one-shot) and expires with its reason. Three hooks change text, so their production-order
scores can move a little.
