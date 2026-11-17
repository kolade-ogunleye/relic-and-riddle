"""QF-73 measurement: the topic bank's hooks, sources, regions, sensitivity spacing, intake and nightly proposals.

    python docs/reviews/quality-fixes-2026-11/QF-73/measure.py <repo root>

Prints JSON and writes nothing in the repository. <repo root> is the code and data measured (the parent or this row).
Inputs: the committed topic bank, image searches, fact sheets and parity record; every database is a fresh one in a
temporary folder built from synthetic topics. Sockets are refused for the whole run (no network, no model, no
provider call: spend $0). On the parent, the checks that need this row's code (src/topic_policy.py) report null.
"""

import hashlib
import json
import socket
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
STARTED = time.perf_counter()


def _no_network(*args, **kwargs):
    raise OSError("QF-73 measure: no network")


socket.socket.connect = _no_network
socket.create_connection = _no_network

from src import config
from src.script_engine import hook_problems

try:
    from src import topic_policy
except ImportError:   # the parent
    topic_policy = None

TOPICS = ROOT / "data/topics"
bank = json.loads((TOPICS / "topic_bank.json").read_text(encoding="utf-8"))
topics = bank["topics"]
queries = json.loads((TOPICS / "image_queries.json").read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def sheet_evidence(tid):
    path = ROOT / "data/fact_sheets" / f"{tid}.json"
    if not path.exists():
        return set()
    sheet = json.loads(path.read_text(encoding="utf-8"))
    return {e["url"] for fact in sheet["facts"] for e in fact.get("evidence", [])}


regions = [t.get("region_group") or "unclassified" for t in topics]
classified = [r for r in regions if r != "unclassified"]
largest = max((classified.count(r) for r in set(classified)), default=0)
out = {
    "topics": len(topics),
    "hooks_passing": sum(not hook_problems(t.get("hook") or "") for t in topics),
    "topics_with_sources": sum(any(str(s.get("url", "")).startswith("https://") for s in t.get("sources") or [])
                               for t in topics),
    "sources_backed_by_a_committed_fact_sheet": sum(
        any(s.get("url") in sheet_evidence(t["id"]) for s in t.get("sources") or []) for t in topics),
    "sources_unesco_list_records": sum(
        any(str(s.get("url", "")).startswith("https://whc.unesco.org/en/list/") for s in t.get("sources") or [])
        and not any(s.get("url") in sheet_evidence(t["id"]) for s in t.get("sources") or []) for t in topics),
    "unclassified_topics": regions.count("unclassified"),
    "largest_region_share": round(largest / len(topics), 4),
    "region_groups": len(set(classified)),
    "new_region_groups_covered": sorted({"Sub-Saharan Africa", "Mesoamerica", "Southeast Asia", "Oceania"}
                                        & set(classified)),
    "sensitive_topics": sum(bool(t.get("sensitivity")) for t in topics),
    "held_topics": sum(bool(t.get("hold")) for t in topics),
    "viral_hashtags": sum(h.casefold() == "#reelsviral" for t in topics for h in t.get("hashtags", [])),
    "duplicate_corrections_present": (TOPICS / "topic_corrections.json").exists(),
    "image_query_entries": len([k for k in queries if not k.startswith("_")]),
}

parity_file = ROOT / "docs/reviews/quality-fixes-2026-11/QF-73/parent_entries.json"
if parity_file.exists():
    parity = json.loads(parity_file.read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in topics}
    out["parent_image_query_entries_changed"] = sum(
        digest(queries.get(k)) != v for k, v in parity["image_queries"].items()) + len(
        set(queries) - set(parity["image_queries"]))
    out["parent_bank_entries_changed_outside_owned_fields"] = sum(
        digest({k: by_id.get(tid, {}).get(k) for k in rec["keys"]}) != rec["sha256"]
        for tid, rec in parity["topic_bank"].items())
else:
    out["parent_image_query_entries_changed"] = out["parent_bank_entries_changed_outside_owned_fields"] = None

# The database checks run on both trees: the parent books and claims with its own code (post_schedule's next free
# slot, the plain claim), this row with topic_policy.next_safe_slot and the spacing-checked claim.
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from threading import Barrier
from zoneinfo import ZoneInfo

from src import post_schedule
from src.db import PipelineDatabase

config.POST_TIMEZONE = "America/New_York"
config.POST_SCHEDULING = True


def audience_day(moment):
    return moment.astimezone(ZoneInfo(config.POST_TIMEZONE)).date()


def sample_db(folder, n_sensitive, n_plain):
    path = Path(folder) / "bank.json"
    path.write_text(json.dumps({"total": n_sensitive + n_plain, "topics": [
        {"id": f"s{i}", "title": f"S{i}", "hook": "How?", "location": {},
         "sensitivity": ["Human remains"] if i < n_sensitive else []}
        for i in range(n_sensitive + n_plain)]}), encoding="utf-8")
    db = PipelineDatabase(Path(folder) / "m.db", path)
    for i in range(n_sensitive + n_plain):
        db.create_run(f"r{i}", f"s{i}")
        db.update_run(f"r{i}", "approved")
    return db


def book_slot(db, i, now, taken):
    if topic_policy is None:
        return post_schedule.next_reel_slot(now, taken)
    return topic_policy.next_safe_slot(db, f"s{i}", now, taken, run_id=f"r{i}")


with tempfile.TemporaryDirectory() as folder:
    db = sample_db(folder, 2, 0)
    at = datetime(2030, 5, 10, 16, tzinfo=UTC)
    db.add_scheduled_post("r0", "reel", at.isoformat(), [])
    refused = 0
    for delta in (-1, 0, 1):
        try:
            post = db.add_scheduled_post("r1", "reel", (at + timedelta(days=delta)).isoformat(), [])
            db.update_scheduled_post(post, "cancelled")
        except ValueError:
            refused += 1
    out["adjacent_sensitive_bookings_refused_of_3"] = refused
with tempfile.TemporaryDirectory() as folder:
    # 12 runs, 8 of them sensitive, booked one after another the way main._schedule_run books them
    db = sample_db(folder, 8, 4)
    now, taken, booked = datetime(2030, 5, 10, 12, tzinfo=UTC), set(), []
    for i in range(12):
        slot = book_slot(db, i, now, taken)
        db.add_scheduled_post(f"r{i}", "reel", slot.astimezone(UTC).isoformat(), [])
        taken.add(audience_day(slot))
        booked.append((audience_day(slot), i < 8))
    days = sorted(day for day, sensitive in booked if sensitive)
    out["sensitive_on_consecutive_days"] = sum((b - a).days <= 1 for a, b in pairwise(days))
with tempfile.TemporaryDirectory() as folder:
    db = sample_db(folder, 2, 0)
    barrier = Barrier(2)

    def claim(run):
        barrier.wait()
        return db.claim_publication(run)

    with ThreadPoolExecutor(2) as pool:
        out["simultaneous_sensitive_claims"] = sum(pool.map(claim, ["r0", "r1"]))

# Carousels (review 3). Sensitive A (run r0) and plain C (r2) posted their Reels in a Reel slot; both carousels wait,
# A's the newer (the daemon's first pick). Sensitive B (r1) is booked (or posted) on a nearby day. The daemon's own
# carousel slots run for 9 days; the publisher is replaced by a recorder (no network). Counted: A's carousel posted
# within SENSITIVE_TOPIC_GAP_DAYS of B's Reel. Review 4: alerts are recorded; an A carousel that expires with no alert
# naming sensitive-topic spacing, or without that reason on its booking, expired silently.
import main

CAROUSEL_CASES = (   # (A's Reel day from Monday 6 May 2030, B's Reel day from it, B posted)
    (0, 2, False),   # the review's case: A Monday, B booked Wednesday, A's carousel due Thursday
    (0, 2, True),    # the same with B's Reel posted
    (5, 3, False),   # A Saturday, B booked Tuesday: A's carousel is due Tuesday, Wednesday, then Thursday
)


def _shown(db, run_id, kind, when):
    db.record_publication(db.get_run(run_id)["topic_id"], run_id, f"upload-post:{kind}", "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE id = (SELECT MAX(id) FROM publications)",
                     (when.astimezone(UTC).isoformat(),))


def carousel_case(folder, a_day, b_after, b_posted):
    config.CAROUSEL_DAYS, config.CAROUSEL_GAP_HOURS, config.CAROUSEL_MAX_AGE_DAYS = ["tue", "wed", "thu"], 48, 7
    config.REEL_POST_TIMES = ["12:00", "19:00"]
    db = sample_db(folder, 2, 1)   # s0 (A) and s1 (B) sensitive, s2 (C) not
    start = post_schedule.reel_time(datetime(2030, 5, 6).date() + timedelta(days=a_day)) + timedelta(minutes=1)
    for run in ("r2", "r0"):
        image = Path(folder) / f"{run}.jpg"
        image.write_bytes(b"x")
        db.update_run(run, "published", master_video_path=Path(folder) / run / "m.mp4")
        reel = db.add_scheduled_post(run, "reel", start.astimezone(UTC).isoformat(), [])
        db.update_scheduled_post(reel, "published", detail={"posted_at": start.astimezone(UTC).isoformat()})
        _shown(db, run, "reel", start)
        db.add_scheduled_post(run, "carousel", None, [str(image)], status="waiting")
    b_at = (start + timedelta(days=b_after)).replace(hour=19, minute=0)
    reel = db.add_scheduled_post("r1", "reel", b_at.astimezone(UTC).isoformat(), [])
    if b_posted:
        db.update_run("r1", "published")
        db.update_scheduled_post(reel, "published", detail={"posted_at": b_at.astimezone(UTC).isoformat()})
        _shown(db, "r1", "reel", b_at)
    coord = main.PipelineCoordinator(db, schedule_posts=True)
    posted, clock = [], {}

    def post_photos(post, run, kind):
        posted.append((post["run_id"], audience_day(clock["now"])))
        db.update_scheduled_post(post["id"], "published")
        _shown(db, post["run_id"], kind, clock["now"])
        return "published"
    coord._post_photos = post_photos
    alerts = []
    coord._alert = lambda text, buttons=None, group=None: alerts.append(text)
    for n in range(9):
        slot = post_schedule.carousel_time((start + timedelta(days=n)).date())
        if slot:
            clock["now"] = slot + timedelta(minutes=1)
            coord._post_carousel_if_due(clock["now"])
    a_days = [day for run, day in posted if run == "r0"]
    gap = getattr(config, "SENSITIVE_TOPIC_GAP_DAYS", 1)   # the parent has no setting: this row's default
    a_car = next(p for p in db.list_scheduled_posts(("waiting", "published", "expired", "failed", "cancelled"),
                                                   run_id="r0") if p["kind"] == "carousel")
    said = sum("sensitive-topic spacing" in text for text in alerts)
    expired = a_car["status"] == "expired"
    reason = (a_car.get("detail") or {}).get("reason") == "sensitive-topic spacing"
    return {"next_to_b": sum(abs((day - audience_day(b_at)).days) <= gap for day in a_days),
            "a_posted": len(a_days), "c_posted": sum(run == "r2" for run, _ in posted),
            "a_expired": expired, "a_silent": expired and not (said and reason), "a_reason": expired and reason,
            "a_alerts": said}


carousel_results = []
for case in CAROUSEL_CASES:
    with tempfile.TemporaryDirectory() as folder:
        carousel_results.append(carousel_case(folder, *case))
out["sensitive_carousel_next_to_another_sensitive_reel_of_3"] = sum(r["next_to_b"] for r in carousel_results)
out["sensitive_carousels_posted_spaced_of_3"] = sum(r["a_posted"] - r["next_to_b"] for r in carousel_results)
out["plain_carousels_posted_of_3"] = sum(r["c_posted"] for r in carousel_results)
out["sensitive_carousels_expired_of_3"] = sum(r["a_expired"] for r in carousel_results)
out["sensitive_carousels_expired_silently_of_3"] = sum(r["a_silent"] for r in carousel_results)
out["sensitive_carousels_expired_with_their_reason_of_3"] = sum(r["a_reason"] for r in carousel_results)
out["spacing_alerts_per_case_max"] = max(r["a_alerts"] for r in carousel_results)

# Review 5: "no slot left" decided once, by the carousel plan. Three cases, each run by the daemon's posting loop
# (one pass an hour for 12 days; Post anyway tapped where the case says, on the trees that have it):
#  lost:      A's Reel Sat 11 May, sensitive B's Reel booked Tue: spacing refuses A Tue and Wed; a newer plain
#             episode (r3, Reel 49 h before Thursday's slot) takes Thursday, A's only spaced slot.
#  two_taps:  A and D (both topic s0) wait behind B's Wednesday Reel; Thursday is the only slot. Post anyway on A,
#             then on D. The plain carousel C would have taken Thursday but for Post anyway.
#  displaced: Post anyway on A over a newer plain carousel r3 ready the same Thursday.
# Counted for each carousel spacing or Post anyway kept from posting (the others lose to newer carousels, as
# before): expired silently (no Needs-you alert about it), with a wrong reason (its booking does not record
# "sensitive-topic spacing", or its status says "waited over 7 days"), and Post anyway replies that named a slot
# the carousel then did not post in.
import html as _html

REVIEW5_CASES = {   # name: (A's Reel day from Mon 6 May 2030, B's Reel day from A's, extra runs, taps, affected)
    "lost": (5, 3, {"r3": ("s2", "thu-49h")}, (), ("r0",)),
    "two_taps": (0, 2, {"r3": ("s0", "reel")}, ("r0", "r3"), ("r0", "r3", "r2")),
    "displaced": (0, 2, {"r3": ("s2", "reel")}, ("r0",), ("r0", "r3")),
}


def review5_case(folder, a_day, b_after, extra, taps, affected):
    config.CAROUSEL_DAYS, config.CAROUSEL_GAP_HOURS, config.CAROUSEL_MAX_AGE_DAYS = ["tue", "wed", "thu"], 48, 7
    config.REEL_POST_TIMES = ["12:00", "19:00"]
    db = sample_db(folder, 2, 1)   # s0 (A) and s1 (B) sensitive, s2 (C) not
    start = post_schedule.reel_time(datetime(2030, 5, 6).date() + timedelta(days=a_day)) + timedelta(minutes=1)
    later = []

    def reel_out(run, reel, when):
        db.update_run(run, "published")
        db.update_scheduled_post(reel, "published", detail={"posted_at": when.astimezone(UTC).isoformat()})
        _shown(db, run, "reel", when)

    for run in ("r2", "r0", *extra):
        if run in extra:
            db.create_run(run, extra[run][0])
        image = Path(folder) / f"{run}.jpg"
        image.write_bytes(b"x")
        when = start
        if run in extra and extra[run][1] == "thu-49h":
            when = post_schedule.carousel_time((start + timedelta(days=5)).date()) - timedelta(hours=49)
        db.update_run(run, "approved", master_video_path=Path(folder) / run / "m.mp4",
                      script_payload={"title": f"Episode {run}"})
        reel = db.add_scheduled_post(run, "reel", when.astimezone(UTC).isoformat(), [])
        if when <= start:
            reel_out(run, reel, when)
        else:
            later.append((when, run, reel))
        db.add_scheduled_post(run, "carousel", None, [str(image)], status="waiting")
    b_at = (start + timedelta(days=b_after)).replace(hour=19, minute=0)
    db.add_scheduled_post("r1", "reel", b_at.astimezone(UTC).isoformat(), [])
    coord = main.PipelineCoordinator(db, schedule_posts=True)
    posted, clock, alerts, replies = [], {}, [], {}

    def post_photos(post, run, kind):
        posted.append(post["run_id"])
        db.update_scheduled_post(post["id"], "published")
        _shown(db, post["run_id"], kind, clock["now"])
        return "published"
    coord._post_photos = post_photos
    coord._alert = lambda text, buttons=None, group=None: alerts.append(text)

    def car(run):
        return next(p for p in db.list_scheduled_posts(("waiting", "published", "expired", "failed", "cancelled"),
                                                       run_id=run) if p["kind"] == "carousel")
    for n in range(12 * 24):
        clock["now"] = start + timedelta(hours=n)
        for when, run, reel in [x for x in later if x[0] <= clock["now"]]:
            reel_out(run, reel, when)
            later.remove((when, run, reel))
        coord._post_carousel_if_due(clock["now"])
        if n == 2 and hasattr(coord, "post_carousel_anyway"):
            for run in taps:
                replies[run] = coord.post_carousel_anyway(car(run)["id"])
    from src.posting_view import status_words
    unposted = [run for run in affected if run not in posted]
    named = {run: sum(f"<b>{_html.escape(db.run_label(run))}</b>" in text for text in alerts) for run in affected}
    wrong = [run for run in unposted if (car(run).get("detail") or {}).get("reason") != "sensitive-topic spacing"
             or "waited over 7 days" in status_words(db, car(run))]
    return {"unposted": len(unposted), "silent": sum(not named[run] for run in unposted), "wrong": len(wrong),
            "alerts_max": max(named.values()),
            "broken_replies": sum("posts at" in replies.get(run, "") and run not in posted for run in taps)}


review5 = []
for case in REVIEW5_CASES.values():
    with tempfile.TemporaryDirectory() as folder:
        review5.append(review5_case(folder, *case))
out["review5_affected_carousels"] = sum(len(case[4]) for case in REVIEW5_CASES.values())
out["review5_affected_carousels_not_posted"] = sum(r["unposted"] for r in review5)
out["review5_expired_silently"] = sum(r["silent"] for r in review5)
out["review5_expired_with_a_wrong_reason"] = sum(r["wrong"] for r in review5)
out["review5_post_anyway_replies_naming_a_slot_not_kept"] = sum(r["broken_replies"] for r in review5)
out["review5_alerts_per_carousel_max"] = max(r["alerts_max"] for r in review5)

if topic_policy is None:
    for key in ("topics_with_concrete_fact", "bank_lint_problems", "lint_missing_source_fails",
                "unconfirmed_upload_holds_card", "source_free_intake_refused", "nightly_below_floor_triggers",
                "nightly_at_floor_triggers", "nightly_research_starts_in_14_nights", "research_runs_per_week_cap",
                "hooks_dropping_a_fact_hedge"):
        out[key] = None
    out["acceptance_met"] = False
else:
    from src import publishable
    from src.models import ProposedLocation, TopicProposal

    out["topics_with_concrete_fact"] = sum(topic_policy.has_concrete_fact(t) for t in topics)
    out["bank_lint_problems"] = len(topic_policy.lint_bank(bank))
    bad = {**topics[0], "sources": []}
    out["lint_missing_source_fails"] = any("source is required" in p for p in topic_policy.lint_bank(
        {"total": len(topics), "topics": [bad, *topics[1:]]}))
    with tempfile.TemporaryDirectory() as folder:
        db = sample_db(folder, 2, 0)
        db.update_run("r0", "publish_unknown")
        record = publishable.schedule(db, posting="slot", topic_id="s1", run_id="r1")
        out["unconfirmed_upload_holds_card"] = record["slot"] is None and bool(record.get("scheduling_hold"))
    with tempfile.TemporaryDirectory() as folder:
        from src import topic_proposer
        db = sample_db(folder, 0, 1)
        topic_proposer.BANK = Path(folder) / "bank.json"
        topic_proposer.QUERIES = Path(folder) / "queries.json"
        topic_proposer.QUERIES.write_text("{}", encoding="utf-8")
        proposal = TopicProposal(slug="new_subject", title="New subject", sub_theme="x", hook="How?",
                                 location=ProposedLocation(name="n", city="", region="", country="c"),
                                 key_facts=["a", "b", "c"], hashtags=[], image_queries=["q"], image_keywords=["q"],
                                 why_it_works="w", sources=[])
        db.set_setting(topic_proposer.PENDING_KEY, json.dumps({"new_subject": proposal.model_dump(mode="json")}))
        before = topic_proposer.BANK.read_bytes()
        try:
            topic_proposer.add(db, "new_subject")
            out["source_free_intake_refused"] = False
        except ValueError:
            out["source_free_intake_refused"] = topic_proposer.BANK.read_bytes() == before

    class Queue:
        def __init__(self, queued):
            self.queued = queued

        def list_all_topics(self):
            return [{"status": "queued"}] * self.queued

        def get_setting(self, key, default=None):
            return getattr(self, "settings", {}).get(key, default)

        def set_setting(self, key, value):
            self.settings = {**getattr(self, "settings", {}), key: value}

    for key, queued in (("nightly_below_floor_triggers", config.TOPIC_QUEUE_FLOOR - 1),
                        ("nightly_at_floor_triggers", config.TOPIC_QUEUE_FLOOR)):
        calls = []
        topic_policy.nightly_proposals(Queue(queued), lambda made=calls: made.append(1))
        out[key] = len(calls)
    # Research spend (review 3): research starts in 14 nights below the floor, Monday 6 May 2030 03:30 Eastern on.
    # The weekly Friday job it replaced started 2 in any 14 days.
    config.TIMEZONE = "America/New_York"
    starts, queue = [], Queue(config.TOPIC_QUEUE_FLOOR - 1)
    for night in range(14):
        moment = datetime(2030, 5, 6, 7, 30, tzinfo=UTC) + timedelta(days=night)
        try:
            topic_policy.nightly_proposals(queue, lambda: starts.append(1), moment)
        except TypeError:   # round 3: no weekly cap, one start a night
            topic_policy.nightly_proposals(queue, lambda: starts.append(1))
    out["nightly_research_starts_in_14_nights"] = len(starts)
    out["research_runs_per_week_cap"] = getattr(config, "TOPIC_RESEARCH_RUNS_PER_WEEK", None)
    out["hooks_dropping_a_fact_hedge"] = (sum(bool(topic_policy.hook_hedge_problems(t)) for t in topics)
                                          if hasattr(topic_policy, "hook_hedge_problems") else None)
    out["acceptance_met"] = (out["hooks_passing"] == out["topics"] and out["largest_region_share"] <= 0.4
                             and out["unclassified_topics"] == 0 and out["sensitive_on_consecutive_days"] == 0
                             and out["adjacent_sensitive_bookings_refused_of_3"] == 3
                             and out["simultaneous_sensitive_claims"] == 1 and out["lint_missing_source_fails"]
                             and out["bank_lint_problems"] == 0 and out["parent_image_query_entries_changed"] == 0
                             and out["sensitive_carousel_next_to_another_sensitive_reel_of_3"] == 0
                             and out["sensitive_carousels_expired_silently_of_3"] == 0
                             and out["spacing_alerts_per_case_max"] <= 1
                             and out["review5_expired_silently"] == 0
                             and out["review5_expired_with_a_wrong_reason"] == 0
                             and out["review5_post_anyway_replies_naming_a_slot_not_kept"] == 0
                             and out["review5_alerts_per_carousel_max"] <= 1
                             and out["hooks_dropping_a_fact_hedge"] == 0
                             and out["nightly_research_starts_in_14_nights"] <= 2)
out["spend_usd"] = 0.0
out["runtime_seconds"] = round(time.perf_counter() - STARTED, 2)
print(json.dumps(out, indent=2))
