"""QF-73: the topic bank's hooks, sources, regions, sensitivity spacing, proposals and lint."""
import hashlib
import html
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from src import config
from src.db import PipelineDatabase, QueueEmpty

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ROOT / "data/topics"
BANK = json.loads((TOPICS / "topic_bank.json").read_text(encoding="utf-8"))
PARITY = ROOT / "docs/reviews/quality-fixes-2026-11/QF-73/parent_entries.json"
# The eight regional additions, each cited by the UNESCO World Heritage List record the owner verified
# (Wikidata item and UNESCO site scope, 2026-11-08); the QIDs wait in the evidence for their photo searches.
NEW = {"great_zimbabwe": 364, "lalibela": 18, "monte_alban": 415, "palenque": 411, "borobudur": 592, "angkor": 668,
       "nan_madol": 1503, "rapa_nui": 715}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the committed bank

def test_every_hook_in_the_bank_passes_the_opening_rule():
    from src.script_engine import hook_problems
    bad = {t["id"]: hook_problems(t.get("hook") or "") for t in BANK["topics"] if hook_problems(t.get("hook") or "")}
    assert not bad


def test_every_topic_cites_a_source_and_no_viral_hashtag():
    assert all(any(str(s.get("url", "")).startswith("https://") for s in t.get("sources") or [])
               for t in BANK["topics"])
    assert not [t["id"] for t in BANK["topics"] if "#reelsviral" in {h.casefold() for h in t.get("hashtags", [])}]


def test_the_duplicate_corrections_file_is_gone():
    assert not (TOPICS / "topic_corrections.json").exists()
    assert "topic_corrections" not in (ROOT / "scripts/migrate_topic_ids.py").read_text(encoding="utf-8")


def test_bank_lint_is_clean_and_no_region_holds_more_than_40_percent():
    from src.topic_policy import lint_bank
    assert lint_bank(BANK) == []
    regions = [t["region_group"] for t in BANK["topics"]]
    assert max(regions.count(r) for r in set(regions)) <= 0.4 * len(regions)
    assert {"Sub-Saharan Africa", "Mesoamerica", "Southeast Asia", "Oceania"} <= set(regions)
    assert BANK["total"] == len(BANK["topics"]) == 39


def test_the_new_topics_cite_their_verified_unesco_record_and_wait_for_photo_searches():
    topics = {t["id"]: t for t in BANK["topics"]}
    queries = json.loads((TOPICS / "image_queries.json").read_text(encoding="utf-8"))
    for tid, unesco in NEW.items():
        topic = topics[tid]
        assert [s["url"] for s in topic["sources"]] == [f"https://whc.unesco.org/en/list/{unesco}/"]
        assert topic["location"]["unesco_id"] == unesco
        assert "latitude" not in topic["location"]   # no unverified coordinates
        assert topic["hold"] and tid not in queries   # QF-54a: a curated search needs a recorded Wikidata answer
    assert sum(bool(t.get("hold")) for t in BANK["topics"]) == len(NEW)


def test_pre_existing_data_entries_equal_the_parents():
    # A data file rebuilt from an older commit reverted QF-54/QF-55 once (review QF-73 round 2): every entry the
    # parent had must hash the same, field for field, except the fields this row owns.
    parity = json.loads(PARITY.read_text(encoding="utf-8"))
    queries = json.loads((TOPICS / "image_queries.json").read_text(encoding="utf-8"))
    assert set(queries) == set(parity["image_queries"])
    assert {k: _digest(v) for k, v in queries.items()} == parity["image_queries"]
    topics = {t["id"]: t for t in BANK["topics"]}
    for tid, record in parity["topic_bank"].items():
        kept = {k: topics[tid].get(k) for k in record["keys"]}
        assert _digest(kept) == record["sha256"], tid


def _sample_bank(**change):
    """Three clean entries in three regions; `change` applies to the first."""
    entry = {"hook": "How did 40 people build it?", "key_facts": ["It took 40 years."], "sensitivity": [],
             "hashtags": [], "sources": [{"url": "https://source.example/a"}]}
    topics = [{**entry, "id": f"t{i}", "region_group": f"Region {i}"} for i in range(3)]
    topics[0].update(change)
    return {"total": 3, "topics": topics}


def test_the_lint_fails_on_an_entry_without_sources(tmp_path, capsys):
    from scripts.lint_topic_bank import main
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(_sample_bank()))
    bad.write_text(json.dumps(_sample_bank(sources=[])))
    assert main([str(good)]) == 0
    capsys.readouterr()
    assert main([str(bad)]) == 1
    assert "source is required" in capsys.readouterr().out


@pytest.mark.parametrize("change, problem", [
    ({"hook": "This is the story of a very old and very famous place in the world today"}, "Hook: "),
    ({"key_facts": ["The site is old.", "People visit it."]}, "number, a scale or a contrast"),
    ({"region_group": "unclassified"}, "region_group"),
    ({"sensitivity": ["Editorial sensitivity assessment pending"]}, "sensitivity"),
    ({"hashtags": ["#ReelsViral"]}, "#ReelsViral"),
])
def test_the_lint_names_each_editorial_fault(change, problem):
    from src.topic_policy import lint_bank
    assert lint_bank(_sample_bank()) == []
    assert any(problem in p for p in lint_bank(_sample_bank(**change)))


# Review 3: three hooks said "80-hectare", "400 square kilometres" and "put a microphone in a cat" where their checked
# facts (UNESCO's "nearly 80 ha" and "some 400 km2"; the key fact's "reportedly") hedge.
HEDGED_HOOKS = {"great_zimbabwe": "nearly 80", "angkor": "some 400", "acoustic_kitty_cia": "reportedly"}


def test_no_hook_drops_a_hedge_its_key_facts_keep():
    from src.topic_policy import hook_hedge_problems
    topics = {t["id"]: t for t in BANK["topics"]}
    for tid, hedge in HEDGED_HOOKS.items():
        assert hedge in topics[tid]["hook"], tid
        assert any(hedge in fact for fact in topics[tid]["key_facts"]), tid   # the hedge is the fact's own
    assert {t["id"]: hook_hedge_problems(t) for t in BANK["topics"] if hook_hedge_problems(t)} == {}


@pytest.mark.parametrize("hook, facts, flagged", [
    ("Who built an 80-hectare city?", ["The city covers nearly 80 hectares."], True),
    ("Who built a nearly 80-hectare city?", ["The city covers nearly 80 hectares."], False),
    ("Who built an 80-hectare city?", ["The city covers nearly 80 hectares.", "Its walls enclose 80 hectares."], False),
    ("Why did 1,200 workers stay?", ["About 1200 workers stayed."], True),
    ("How does a 1513 map survive?", ["Compiled in 1513 from about 20 source maps"], False),   # the hedge is on 20
    ("Why put a microphone in a cat?", ["A microphone was reportedly implanted in a cat"], True),
    ("Why reportedly put a microphone in a cat?", ["A microphone was reportedly implanted in a cat"], False),
    ("How did a room lined with amber vanish?", ["It was said to hold several tonnes of amber"], False),
])
def test_the_lint_flags_a_hook_that_drops_its_facts_hedge(hook, facts, flagged):
    from src.topic_policy import hook_hedge_problems, lint_bank
    assert bool(hook_hedge_problems({"hook": hook, "key_facts": facts})) is flagged
    bank = _sample_bank(hook="Who built an 80-hectare city in 40 years?",
                        key_facts=["It took 40 years.", "The city covers nearly 80 hectares."])
    assert any("without the hedge" in p for p in lint_bank(bank))


def test_the_lint_refuses_a_region_above_40_percent():
    from src.topic_policy import lint_bank
    bank = _sample_bank()
    bank["topics"] += [{**bank["topics"][0], "id": "t3"}, {**bank["topics"][1], "id": "t4"}]
    bank["total"] = 5
    assert lint_bank(bank) == []   # 2 of 5 in each of two regions: 40% is allowed
    bank["topics"][4]["region_group"] = "Region 0"
    assert any("Region 0: 3 of 5 topics exceeds 40%" in p for p in lint_bank(bank))


@pytest.mark.parametrize("url", ["http://source.example", "https://127.0.0.1/a", "https://user:pass@source.example",
                                 "https://localhost/a", 123, None, ""])
def test_a_source_must_be_a_public_https_page(url):
    from src.topic_policy import sources_valid
    assert not sources_valid([{"url": url}])
    assert sources_valid([{"url": "https://whc.unesco.org/en/list/364/"}])


def test_a_concrete_fact_uses_the_opening_rules_own_categories():
    from src.topic_policy import concrete_signal
    assert concrete_signal("The city covers an area of nearly 80 hectares.") == "number"
    assert concrete_signal("It is among the largest domes ever built.") == "scale"
    assert concrete_signal("Its purpose is still unknown to historians.") == "contrast"
    assert concrete_signal("Rapa Nui is the indigenous name of Easter Island.") is None
    assert concrete_signal("Why did they build it?") is None   # a question is a hook, not a fact


# ------------------------------------------------------------------ sensitivity spacing

@pytest.fixture
def sensitive_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_TIMEZONE", "America/New_York")
    bank = tmp_path / "topics.json"
    bank.write_text(json.dumps({"total": 4, "topics": [
        {"id": str(i), "title": f"Subject {i}", "hook": "Why build 40 rooms underground?", "location": {},
         "sources": [{"url": "https://source.example/record"}], "region_group": "sample",
         "sensitivity": ["Human remains"] if i < 2 else []} for i in range(3)]
        + [{"id": "held", "title": "Held subject", "hook": "Why build 40 rooms underground?", "location": {},
            "sources": [{"url": "https://source.example/record"}], "region_group": "sample", "sensitivity": [],
            "hold": "Photo searches pending"}]}), encoding="utf-8")
    db = PipelineDatabase(tmp_path / "sample.db", bank)
    for i in range(3):
        db.create_run(f"r{i}", str(i))
        db.update_run(f"r{i}", "approved")
    return db


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_sensitive_bookings_never_fall_on_the_same_or_the_next_day(sensitive_db, delta):
    at = datetime(2030, 5, 10, 16, tzinfo=UTC)
    sensitive_db.add_scheduled_post("r0", "reel", at.isoformat(), [])
    with pytest.raises(ValueError, match="clear audience-calendar day"):
        sensitive_db.add_scheduled_post("r1", "reel", (at + timedelta(days=delta)).isoformat(), [])
    sensitive_db.add_scheduled_post("r2", "reel", (at + timedelta(days=delta)).isoformat(), [])   # not sensitive
    sensitive_db.add_scheduled_post("r1", "reel", (at + timedelta(days=2)).isoformat(), [])        # a clear day


def test_a_move_cannot_bring_two_sensitive_topics_together(sensitive_db):
    at = datetime(2030, 5, 10, 16, tzinfo=UTC)
    sensitive_db.add_scheduled_post("r0", "reel", at.isoformat(), [])
    post = sensitive_db.add_scheduled_post("r1", "reel", (at + timedelta(days=2)).isoformat(), [])
    with pytest.raises(ValueError):
        sensitive_db.update_scheduled_post(post, "pending", (at + timedelta(days=1)).isoformat())
    assert sensitive_db.list_scheduled_posts(run_id="r1")[0]["due_at"] == (at + timedelta(days=2)).isoformat()
    sensitive_db.update_scheduled_post(post, "pending", detail={"held": "x"})   # no move: never refused


def test_the_audience_day_is_the_post_timezones_calendar_day(sensitive_db):
    # 01:00 UTC on 11 May is still 10 May in New York.
    sensitive_db.add_scheduled_post("r0", "reel", "2030-05-11T01:00:00+00:00", [])
    with pytest.raises(ValueError):
        sensitive_db.add_scheduled_post("r1", "reel", "2030-05-12T01:00:00+00:00", [])
    sensitive_db.add_scheduled_post("r1", "reel", "2030-05-12T16:00:00+00:00", [])


def test_two_sensitive_publications_cannot_be_claimed_at_once(sensitive_db):
    barrier = Barrier(2)

    def claim(run):
        barrier.wait()
        return sensitive_db.claim_publication(run)

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, ["r0", "r1"])) == [False, True]
    assert sensitive_db.claim_publication("r2")


@pytest.mark.parametrize("status", ["published", "failed", "publish_unknown"])
def test_a_recent_or_unconfirmed_sensitive_publication_holds_the_next(sensitive_db, status):
    sensitive_db.record_publication("0", "r0", "test", "published", {})
    sensitive_db.update_run("r0", status)
    assert not sensitive_db.claim_publication("r1")
    assert sensitive_db.claim_publication("r2")


def test_runs_of_the_same_topic_never_space_each_other(sensitive_db):
    sensitive_db.create_run("r0b", "0")
    sensitive_db.update_run("r0b", "approved")
    at = datetime(2030, 5, 10, 16, tzinfo=UTC)
    sensitive_db.add_scheduled_post("r0", "reel", at.isoformat(), [])
    sensitive_db.add_scheduled_post("r0b", "reel", (at + timedelta(days=1)).isoformat(), [])


def test_an_unknown_booking_holds_until_it_is_reconciled(sensitive_db):
    from src import topic_policy
    sensitive_db.add_scheduled_post("r0", "reel", "2020-01-01T12:00:00+00:00", [], status="unknown")
    assert not sensitive_db.claim_publication("r1")
    with pytest.raises(topic_policy.SensitiveSpacingError):
        topic_policy.next_safe_slot(sensitive_db, "1", datetime(2030, 5, 10, tzinfo=UTC), set(), run_id="r1")


def test_an_uncertain_upload_is_still_recorded_when_another_run_is_held(sensitive_db):
    post = sensitive_db.add_scheduled_post("r0", "reel", "2030-05-10T16:00:00+00:00", [])
    sensitive_db.update_run("r1", "publish_unknown")
    sensitive_db.update_scheduled_post(post, "unknown", detail={"error": "Upload outcome unknown"})
    assert sensitive_db.list_scheduled_posts(("unknown",), run_id="r0")[0]["status"] == "unknown"


def test_the_next_slot_and_the_review_card_keep_the_clear_day(sensitive_db, monkeypatch):
    from src import publishable, telegram_bot, topic_policy
    monkeypatch.setattr(config, "POST_SCHEDULING", True)
    at = datetime(2030, 5, 10, 16, tzinfo=UTC)
    sensitive_db.add_scheduled_post("r0", "reel", at.isoformat(), [])
    slot = topic_policy.next_safe_slot(sensitive_db, "1", at, set(), run_id="r1")
    assert topic_policy._day(slot).isoformat() == "2030-05-12"   # 10 May is booked, 11 May is next to it
    record = publishable.schedule(sensitive_db, now=at, posting="slot", topic_id="1", run_id="r1")
    assert record["slot"] == slot.isoformat() and "scheduling_hold" not in record
    plain = publishable.schedule(sensitive_db, now=at, posting="slot", topic_id="2", run_id="r2")
    assert plain["slot"] < record["slot"]   # a topic that is not sensitive takes the next free day
    assert "Slot:" in telegram_bot._schedule_line(record)


def test_the_review_card_never_promises_a_post_it_cannot_make(sensitive_db, monkeypatch):
    from src import publishable, telegram_bot
    monkeypatch.setattr(config, "POST_SCHEDULING", True)
    sensitive_db.update_run("r0", "publish_unknown")
    for posting in ("slot", "now"):
        record = publishable.schedule(sensitive_db, posting=posting, topic_id="1", run_id="r1")
        assert record["slot"] is None and record["scheduling_hold"]
        line = telegram_bot._schedule_line(record)
        assert "Waiting" in line and "as soon as" not in line
    assert "scheduling_hold" not in publishable.schedule(sensitive_db, posting="now", topic_id="2", run_id="r2")


def test_ranking_skips_held_topics_and_sensitive_ones_while_an_upload_is_unknown(sensitive_db):
    assert "held" not in [t["id"] for t in sensitive_db.ranked_topics()]
    sensitive_db.update_run("r0", "publish_unknown")
    sensitive_db.record_topic_status("0", "publish_unknown")
    assert [t["id"] for t in sensitive_db.ranked_topics()] == ["2"]
    sensitive_db.record_topic_status("2", "rejected")
    with pytest.raises(QueueEmpty, match="editorial hold"):
        sensitive_db.ranked_topics()


def test_the_sync_keeps_editorial_fields_and_the_operators_state(sensitive_db):
    sensitive_db.record_topic_status("0", "rejected")
    sensitive_db._seed_topics_from_json()
    topic = sensitive_db.get_topic_by_id("0")
    assert topic["status"] == "rejected" and topic["sensitivity"] == ["Human remains"]
    assert topic["sources"][0]["url"] == "https://source.example/record"
    assert sensitive_db.get_topic_by_id("held")["hold"] == "Photo searches pending"


def test_both_review_cards_show_the_sensitivity_chip(sensitive_db):
    from src import telegram_bot, topic_proposer
    note = topic_proposer.origin_note(sensitive_db.get_topic_by_id("0"))
    assert "⚠️ Sensitive topic · Human remains" in note
    assert "Sensitive" not in topic_proposer.origin_note(sensitive_db.get_topic_by_id("2"))
    assert "topic_origin_note" in telegram_bot.V2_NOTE_KEYS   # the V2 card lists the same note as the live card


# ------------------------------------------------------------------ carousels (review 3)

def _shown(db, run_id, kind, when):
    """The run's `kind` went out at `when` (its publication receipt, dated as the publisher records it)."""
    db.record_publication(db.get_run(run_id)["topic_id"], run_id, f"upload-post:{kind}", "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE id = (SELECT MAX(id) FROM publications)",
                     (when.astimezone(UTC).isoformat(),))


def _evening(monday, days):
    return (monday + timedelta(days=days)).replace(hour=19, minute=0).astimezone(UTC).isoformat()


@pytest.fixture
def carousel_week(sensitive_db, tmp_path, monkeypatch, request):
    """Sensitive topic 0 (run r0) and plain topic 2 (r2) posted their Reels in Monday 6 May 2030's Reel slot (19:00
    Eastern), or that many days later (the parameter); both carousels wait. week() runs the daemon's carousel slots from that
    day; a carousel that posts is recorded at its slot."""
    from zoneinfo import ZoneInfo

    from main import PipelineCoordinator
    from src import post_schedule
    for name, value in (("CAROUSEL_DAYS", ["tue", "wed", "thu"]), ("CAROUSEL_GAP_HOURS", 48),
                        ("CAROUSEL_MAX_AGE_DAYS", 7), ("REEL_POST_TIMES", ["12:00", "19:00"]),
                        ("SENSITIVE_TOPIC_GAP_DAYS", 1)):
        monkeypatch.setattr(config, name, value)
    eastern = ZoneInfo("America/New_York")
    day = datetime(2030, 5, 6, tzinfo=eastern).date() + timedelta(days=getattr(request, "param", 0))
    monday = post_schedule.reel_time(day) + timedelta(minutes=1)   # its Reel posted in its slot
    db = sensitive_db
    for run in ("r2", "r0"):   # A's carousel is the newer: the daemon would pick it first
        folder = tmp_path / run
        folder.mkdir()
        (folder / "slide.jpg").write_bytes(b"x")
        db.update_run(run, "published", master_video_path=folder / "m.mp4")
        reel = db.add_scheduled_post(run, "reel", monday.astimezone(UTC).isoformat(), [])
        db.update_scheduled_post(reel, "published", detail={"posted_at": monday.astimezone(UTC).isoformat()})
        _shown(db, run, "reel", monday)
        db.add_scheduled_post(run, "carousel", None, [str(folder / "slide.jpg")], status="waiting")
    coord = PipelineCoordinator(db, schedule_posts=True)
    coord.posted = []

    def post_photos(post, run, kind):
        coord.posted.append((post["run_id"], coord.slot_now.astimezone(eastern).strftime("%a")))
        db.update_scheduled_post(post["id"], "published")
        _shown(db, post["run_id"], kind, coord.slot_now)
        return "published"
    coord._post_photos = post_photos

    def week(days=9):
        for n in range(days):
            slot = post_schedule.carousel_time((monday + timedelta(days=n)).date())
            if slot:
                coord.slot_now = slot + timedelta(minutes=1)
                coord._post_carousel_if_due(coord.slot_now)
    coord.week, coord.monday = week, monday
    return coord


def _carousel(db, run_id):
    return next(p for p in db.list_scheduled_posts(("waiting", "published", "expired", "failed", "cancelled"),
                                                   run_id=run_id) if p["kind"] == "carousel")


@pytest.mark.parametrize("other", ["booked", "posted"])
def test_a_sensitive_carousel_waits_while_another_sensitive_reel_is_near(carousel_week, other):
    """The review's case: sensitive A's Reel posts Monday, sensitive B's Reel is booked for (or posts) Wednesday,
    after Monday's clear Tuesday. A's carousel is ready from Thursday (its first slot 48 h after the Reel) and must not
    go out next to B. It waits (never dropped for it), and the plain topic's ready carousel takes the slot."""
    db, monday = carousel_week.db, carousel_week.monday
    assert monday.weekday() == 0
    reel = db.add_scheduled_post("r1", "reel", _evening(monday, 2), [])   # Tuesday is clear: allowed
    if other == "posted":
        db.update_run("r1", "published")
        db.update_scheduled_post(reel, "published", detail={"posted_at": _evening(monday, 2)})
        _shown(db, "r1", "reel", datetime.fromisoformat(_evening(monday, 2)))
    carousel_week.week(4)
    assert ("r0", "Thu") not in carousel_week.posted     # its first slot is the day after B's Reel
    assert carousel_week.posted[0] == ("r2", "Thu")      # another ready episode takes the slot
    assert _carousel(db, "r0")["status"] == "waiting"    # it waits for a later slot, not failed or held
    carousel_week.week()
    assert _carousel(db, "r0")["status"] == "expired"    # its own 7-day deadline still applies, as before
    assert [run for run, _ in carousel_week.posted].count("r0") == 0


@pytest.mark.parametrize("carousel_week", [5], indirect=True)
def test_a_waiting_sensitive_carousel_posts_at_the_first_spaced_slot_and_the_plan_agrees(carousel_week):
    """A's Reel posts Saturday 11 May; sensitive B's Reel is booked for Tuesday. A's carousel waits at Tuesday (the
    same day) and Wednesday (the next) and posts on Thursday; the Posting plan names that Thursday slot."""
    from src.posting_view import carousel_plan
    db, saturday = carousel_week.db, carousel_week.monday
    assert saturday.weekday() == 5
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    db.add_scheduled_post("r1", "reel", _evening(saturday, 3), [])
    plan = carousel_plan(db, saturday)
    assert plan[_carousel(db, "r0")["id"]].astimezone(config_tz()).strftime("%a") == "Thu"
    carousel_week.week(7)
    assert carousel_week.posted == [("r0", "Thu")]


def test_the_plan_skips_a_slot_the_daemon_will_not_take(carousel_week):
    from src.posting_view import carousel_plan
    db, monday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    db.add_scheduled_post("r1", "reel", _evening(monday, 2), [])   # Wednesday: its only slot, Thursday, is next to it
    assert _carousel(db, "r0")["id"] not in carousel_plan(db, monday)   # no slot is promised before its deadline
    carousel_week.week()
    assert carousel_week.posted == []


def test_a_carousel_of_a_topic_that_is_not_sensitive_is_never_held_back(carousel_week):
    db, monday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(_carousel(db, "r0")["id"], "cancelled")
    db.add_scheduled_post("r1", "reel", _evening(monday, 2), [])
    carousel_week.week(4)
    assert carousel_week.posted == [("r2", "Thu")]


def test_a_posted_sensitive_carousel_counts_as_the_topic_appearing(carousel_week):
    """Once A's carousel is out on Thursday, sensitive B cannot book Thursday or Friday: every surface the audience
    saw counts (Reel, Stories, carousel), not only the latest Reel."""
    db, monday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    carousel_week.week(4)
    assert carousel_week.posted == [("r0", "Thu")]
    for n in (3, 4):
        with pytest.raises(ValueError, match="clear audience-calendar day"):
            db.add_scheduled_post("r1", "reel", _evening(monday, n), [])
    db.add_scheduled_post("r1", "reel", _evening(monday, 5), [])


def test_an_uncertain_carousel_counts_on_the_day_it_was_tried(sensitive_db):
    at = datetime(2030, 5, 8, 16, tzinfo=UTC)
    sensitive_db.update_run("r0", "published")
    sensitive_db.record_publication("0", "r0", "upload-post:carousel", "unknown", {})
    with sensitive_db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ?", (at.isoformat(),))
    with pytest.raises(ValueError, match="clear audience-calendar day"):
        sensitive_db.add_scheduled_post("r1", "reel", (at + timedelta(days=1)).isoformat(), [])
    sensitive_db.add_scheduled_post("r1", "reel", (at + timedelta(days=2)).isoformat(), [])


def config_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(config.POST_TIMEZONE)


# ------------------------------------------------------------------ the spacing wait is said (review 4)

def _spaced_probe(carousel_week, other="booked"):
    """Review 4's probe: sensitive A's Reel posted Monday, sensitive B's Reel is booked for (or posted) Wednesday,
    so A's only carousel slot in its window (Thursday) is next to B. Alerts are recorded."""
    db, monday = carousel_week.db, carousel_week.monday
    reel = db.add_scheduled_post("r1", "reel", _evening(monday, 2), [])
    if other == "posted":
        db.update_run("r1", "published")
        db.update_scheduled_post(reel, "published", detail={"posted_at": _evening(monday, 2)})
        _shown(db, "r1", "reel", datetime.fromisoformat(_evening(monday, 2)))
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None: carousel_week.alerts.append((text, buttons, group))
    return db, monday


def _spacing_alerts(coord):
    return [a for a in coord.alerts if "sensitive-topic spacing" in a[0]]


def _buttons(buttons):
    return {b["text"]: b["callback_data"] for row in buttons or [] for b in row}


@pytest.mark.parametrize("other", ["booked", "posted"])
def test_a_spacing_wait_is_said_once_with_both_actions_and_expires_with_its_reason(carousel_week, other):
    from src import posting_view
    db, monday = _spaced_probe(carousel_week, other)
    carousel_week.week(3)   # Tuesday and Wednesday's slots: A's Thursday slot is already known to be next to B
    for hour in range(48):   # every pass of the posting loop asks again: still one alert
        carousel_week._post_carousel_if_due(monday + timedelta(days=1, hours=hour))
    said = _spacing_alerts(carousel_week)
    assert len(said) == 1
    text, buttons, group = said[0]
    car = _carousel(db, "r0")
    assert group == "needs" and "Subject 0" in text and "deadline" in text
    assert _buttons(buttons) == {"▶️ Post anyway": f"carspace:{car['id']}", "⏹ Let it expire": f"carexpire:{car['id']}"}
    assert all(len(label) <= 28 for label in _buttons(buttons))
    # While it waits, every screen says why and promises no slot
    plan = posting_view.carousel_plan(db, monday)
    line = posting_view._post_line(db, car, plan)
    assert "waiting: sensitive-topic spacing, no spaced slot before its deadline" in line
    assert "after its Reel" not in line
    episode, episode_buttons = posting_view.posting_episode(db, "r0")
    assert "Waiting: sensitive-topic spacing" in episode and "first carousel slot 48 h" not in episode
    assert _buttons(episode_buttons)["▶️ Post anyway"] == f"carspace:{car['id']}"
    assert "sensitive-topic spacing" in posting_view.wont_post(db, "r0")
    assert car["detail"]["waiting_for"] == "spacing" and car["detail"]["spacing"]
    import main
    assert posting_view.SLOT_GRACE == main.MISSED_SLOT_GRACE   # the screens count a slot as long as the daemon does
    # Untouched, it expires at its deadline with the true reason, and is not said again
    carousel_week.week()
    car = _carousel(db, "r0")
    assert car["status"] == "expired" and car["detail"]["reason"] == "sensitive-topic spacing"
    assert [run for run, _ in carousel_week.posted].count("r0") == 0
    assert len(_spacing_alerts(carousel_week)) == 1
    episode, _ = posting_view.posting_episode(db, "r0")
    assert "expired: sensitive-topic spacing left no carousel slot before its deadline" in episode
    assert "waited over 7 days" not in episode and "cancelled with the failed slot" not in episode


def test_the_spacing_alert_comes_days_before_the_only_slot_and_the_deadline(carousel_week):
    """Each pass of the posting loop looks: the alert goes out at the first pass after A's Reel has posted (its
    window is then known), well before Thursday's slot and the deadline, so Post anyway can still make that slot."""
    _db, monday = _spaced_probe(carousel_week)
    first = None
    for minutes in range(0, 3 * 24 * 60, 10):
        carousel_week._post_carousel_if_due(monday + timedelta(minutes=minutes))
        if first is None and _spacing_alerts(carousel_week):
            first = monday + timedelta(minutes=minutes)
    assert first is not None and first - monday < timedelta(hours=1)
    assert len(_spacing_alerts(carousel_week)) == 1
    assert "Thu" in _spacing_alerts(carousel_week)[0][0]   # the slot Post anyway would take


def test_post_anyway_posts_it_at_the_next_carousel_slot(carousel_week):
    from src import posting_view
    db, monday = _spaced_probe(carousel_week)
    carousel_week.week(3)
    assert len(_spacing_alerts(carousel_week)) == 1
    car = _carousel(db, "r0")
    reply = carousel_week.post_carousel_anyway(car["id"])
    assert "next carousel slot" in reply and "Thu" in reply
    assert posting_view.wont_post(db, "r0") is None   # it will post, and the plan names its slot
    assert posting_view.carousel_plan(db, monday)[car["id"]].astimezone(config_tz()).strftime("%a") == "Thu"
    carousel_week.week(4)
    assert ("r0", "Thu") in carousel_week.posted and ("r2", "Thu") not in carousel_week.posted
    assert len(_spacing_alerts(carousel_week)) == 1


def test_post_anyway_still_passes_the_slots_holds(carousel_week, monkeypatch):
    """Post anyway sets spacing aside, nothing else: a carousel that no longer matches the review you approved is
    held at its slot as any other (QF-81b; QF-81c's holds run on the same ready list)."""
    from src import carousel_lazy
    db, _monday = _spaced_probe(carousel_week)
    carousel_week.week(3)
    assert len(_spacing_alerts(carousel_week)) == 1
    car = _carousel(db, "r0")
    db.update_scheduled_post(car["id"], "waiting", files=[])   # planned, drawn at its slot
    monkeypatch.setattr(carousel_lazy, "planned", lambda run_dir: True)
    carousel_week._approval_broken = lambda run_id: "its plan changed after you approved the review" if run_id == "r0" else None
    carousel_week.post_carousel_anyway(car["id"])
    carousel_week.week(4)
    car = _carousel(db, "r0")
    assert ("r0", "Thu") not in carousel_week.posted
    assert car["status"] == "failed" and car["detail"]["held"]
    assert any("Carousel not posted" in a[0] for a in carousel_week.alerts)


def test_let_it_expire_expires_it_now_with_its_reason(carousel_week):
    from src import posting_view
    db, _monday = _spaced_probe(carousel_week)
    carousel_week.week(3)
    assert len(_spacing_alerts(carousel_week)) == 1
    reply = carousel_week.expire_spaced_carousel(_carousel(db, "r0")["id"])
    assert "let it expire" in reply
    car = _carousel(db, "r0")
    assert car["status"] == "expired" and car["detail"]["reason"] == "sensitive-topic spacing"
    assert posting_view.status_words(db, car) == "expired: you let it expire (sensitive-topic spacing)"
    carousel_week.week()
    assert [run for run, _ in carousel_week.posted].count("r0") == 0
    assert len(_spacing_alerts(carousel_week)) == 1
    assert "not waiting" in carousel_week.post_carousel_anyway(car["id"])   # a second tap changes nothing


@pytest.mark.parametrize("carousel_week", [5], indirect=True)
def test_a_wait_with_a_spaced_slot_left_names_it_and_raises_no_alert(carousel_week):
    """A Saturday, B booked Tuesday: Tuesday and Wednesday are too close, Thursday is spaced. The wait is said with
    its earliest slot, and nothing needs you."""
    from src import posting_view
    db, saturday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    db.add_scheduled_post("r1", "reel", _evening(saturday, 3), [])
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None: carousel_week.alerts.append((text, buttons, group))
    car = _carousel(db, "r0")
    line = posting_view._post_line(db, car, posting_view.carousel_plan(db, saturday))
    assert "waiting: sensitive-topic spacing, earliest Thu" in line
    assert posting_view.wont_post(db, "r0") is None
    carousel_week.week(7)
    assert carousel_week.posted == [("r0", "Thu")] and _spacing_alerts(carousel_week) == []


# ------------------------------------------------------------------ one answer: the carousel plan's (review 5)

@pytest.fixture
def frozen(monkeypatch):
    """The screens and the alert buttons read the clock: it is set to the daemon pass being run."""
    import main
    from src import posting_view

    class Clock(datetime):
        at = None

        @classmethod
        def now(cls, tz=None):
            return cls.at.astimezone(tz) if tz else cls.at.astimezone().replace(tzinfo=None)

    monkeypatch.setattr(posting_view, "datetime", Clock)
    monkeypatch.setattr(main, "datetime", Clock)
    return Clock


def _another(db, tmp_path, run_id, topic, reel_at, posted=True):
    """One more episode of `topic` whose Reel posted (or is booked) at reel_at, its carousel waiting."""
    db.create_run(run_id, topic)
    folder = tmp_path / run_id
    folder.mkdir()
    (folder / "slide.jpg").write_bytes(b"x")
    db.update_run(run_id, "published" if posted else "approved", master_video_path=folder / "m.mp4",
                  script_payload={"title": f"Episode {run_id}"})
    reel = db.add_scheduled_post(run_id, "reel", reel_at.astimezone(UTC).isoformat(), [])
    if posted:
        _reel_posted(db, run_id, reel, reel_at)
    db.add_scheduled_post(run_id, "carousel", None, [str(folder / "slide.jpg")], status="waiting")
    return reel


def _reel_posted(db, run_id, reel, when):
    db.update_run(run_id, "published")
    db.update_scheduled_post(reel, "published", detail={"posted_at": when.astimezone(UTC).isoformat()})
    _shown(db, run_id, "reel", when)


def _named(db, run_id, text):
    return html.escape(db.run_label(run_id)) in text


def _needs(coord):
    return [a for a in coord.alerts if a[2] == "needs"]


def _passes(coord, clock, start, hours, every=30, at=None):
    """The daemon's posting loop, one pass every `every` minutes; `at` maps a moment to what happens then."""
    pending = sorted((at or {}).items())
    for n in range(int(hours * 60 / every)):
        now = start + timedelta(minutes=every * n)
        while pending and pending[0][0] <= now:
            pending.pop(0)[1]()
        clock.at = coord.slot_now = now
        coord._post_carousel_if_due(now)


def _wrong_reason(text):
    return any(words in text for words in ("waited over 7 days", "cancelled with the failed slot",
                                           "Book Stories and carousel again", "after its Reel", "first carousel slot 48 h"))


@pytest.mark.parametrize("carousel_week", [5], indirect=True)
def test_a_spaced_slot_lost_to_a_newer_carousel_is_said_once_and_expires_for_spacing(carousel_week, frozen, tmp_path):
    """Review 5, blocking 1: A's Reel posts Sat 11 May, sensitive B's Reel is booked Tue 14 May, so spacing refuses A
    on Tuesday and Wednesday and allows Thursday. A newer plain episode (r3), whose Reel posts 49 h before Thursday's
    slot, takes Thursday (newest first). The plan gives A no slot from Saturday on: one Needs-you alert says so, the
    screens agree, and A expires recording sensitive-topic spacing, never a wrong reason."""
    from src import posting_view
    from src.control import status_text
    db, saturday = carousel_week.db, carousel_week.monday
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None: carousel_week.alerts.append((text, buttons, group))
    db.add_scheduled_post("r1", "reel", _evening(saturday, 3), [])
    thursday = post_schedule_slot(saturday, 5)
    r3_reel_at = thursday - timedelta(hours=49)
    r3_reel = _another(db, tmp_path, "r3", "2", r3_reel_at, posted=False)
    _passes(carousel_week, frozen, saturday, 2)
    car = _carousel(db, "r0")
    assert len(_needs(carousel_week)) == 1
    text, buttons, _group = _needs(carousel_week)[0]
    assert "Subject 0" in text and "sensitive-topic spacing" in text and "spaced slot" in text
    assert set(_buttons(buttons)) == {"▶️ Post anyway", "⏹ Let it expire"}
    line = posting_view._post_line(db, car, posting_view.carousel_plan(db))
    assert "waiting: sensitive-topic spacing, no spaced slot before its deadline" in line and not _wrong_reason(line)
    assert "sensitive-topic spacing" in posting_view.wont_post(db, "r0")
    episode, episode_buttons = posting_view.posting_episode(db, "r0")
    assert "newer carousel" in episode and "Thu 16 May" in episode and not _wrong_reason(episode)
    assert "⏹ Let it expire" in _buttons(episode_buttons)
    assert "no spaced slot before its deadline" in status_text(db, None, None)
    _passes(carousel_week, frozen, saturday + timedelta(hours=2), 12 * 24,
            at={r3_reel_at + timedelta(minutes=1): lambda: _reel_posted(db, "r3", r3_reel, r3_reel_at)})
    assert carousel_week.posted == [("r2", "Tue"), ("r3", "Thu")]
    car = _carousel(db, "r0")
    assert car["status"] == "expired" and car["detail"]["reason"] == "sensitive-topic spacing"
    assert car["detail"]["spacing_why"]["slot"] and car["detail"]["spacing_why"]["kind"] == "spacing"
    assert len(_needs(carousel_week)) == 1
    words = posting_view.status_words(db, car)
    assert words.startswith("expired: sensitive-topic spacing held it back, then a newer carousel") and "Thu" in words
    episode, episode_buttons = posting_view.posting_episode(db, "r0")
    assert not _wrong_reason(episode) and "rebook:" not in str(episode_buttons)


def post_schedule_slot(start, days):
    from src import post_schedule
    return post_schedule.carousel_time((start + timedelta(days=days)).date())


def test_two_post_anyway_taps_never_lose_a_carousel_silently(carousel_week, frozen, tmp_path):
    """Review 5, blocking 2: two sensitive carousels of topic 0 (A r0, D r4) wait behind B's Wednesday Reel; Thursday
    is the only slot. Post anyway on A secures Thursday and says so; Post anyway on D then says A holds it and
    changes nothing. The plain carousel Post anyway displaced is said too. Every carousel that does not post had
    one alert with its true reason and expires recording it; the screens match the plan throughout."""
    from src import posting_view
    db, monday = _spaced_probe(carousel_week)
    _another(db, tmp_path, "r4", "0", monday)
    _passes(carousel_week, frozen, monday, 2)
    a, d = _carousel(db, "r0"), _carousel(db, "r4")
    assert len(_needs(carousel_week)) == 2   # A's and D's
    reply = carousel_week.post_carousel_anyway(a["id"])
    assert "Thu 09 May" in reply and "Subject 2" in reply   # its slot, and the plain carousel giving it up
    reply = carousel_week.post_carousel_anyway(d["id"])
    assert "can't take that slot" in reply and "Nothing was changed" in reply and "Thu 09 May" in reply
    assert not _carousel(db, "r4")["detail"].get("spacing_override")
    plan = posting_view.carousel_plan(db)
    assert plan[a["id"]].strftime("%a") == "Thu" and d["id"] not in plan
    assert "Thu 09 May" in posting_view._post_line(db, _carousel(db, "r0"), plan)
    d_line = posting_view._post_line(db, _carousel(db, "r4"), plan)
    assert "no spaced slot before its deadline" in d_line and not _wrong_reason(d_line)
    _episode, d_buttons = posting_view.posting_episode(db, "r4")
    assert "▶️ Post anyway" not in _buttons(d_buttons)   # only what can still happen
    _passes(carousel_week, frozen, monday + timedelta(hours=2), 9 * 24)
    assert carousel_week.posted == [("r0", "Thu")]
    for run in ("r4", "r2"):
        car = _carousel(db, run)
        assert car["status"] == "expired" and car["detail"]["reason"] == "sensitive-topic spacing", run
        assert sum(_named(db, run, text) for text, _b, _g in _needs(carousel_week)) == 1, run
        assert not _wrong_reason(posting_view.status_words(db, car))
    assert "chose to post anyway" in posting_view.status_words(db, _carousel(db, "r2"))
    assert len(_needs(carousel_week)) == 3


def test_two_carousels_posted_anyway_for_one_slot_raise_one_alert_for_the_one_left_out(carousel_week, frozen, tmp_path):
    """Both carousels carry Post anyway (chosen while two slots were left, say) and one slot remains: the plan gives
    it to one, and the other raises one Needs-you alert naming the true reason, never vanishing."""
    from src import posting_view
    db, monday = _spaced_probe(carousel_week)
    _another(db, tmp_path, "r4", "0", monday)
    for run in ("r0", "r4"):
        car = _carousel(db, run)
        db.update_scheduled_post(car["id"], "waiting", detail={**car["detail"], "spacing_override": True,
                                                               "priority": True, "spacing_alert": "earlier"})
    _passes(carousel_week, frozen, monday, 2)
    a, d = _carousel(db, "r0"), _carousel(db, "r4")
    plan = posting_view.carousel_plan(db)
    assert plan[d["id"]].strftime("%a") == "Thu" and a["id"] not in plan
    said = [text for text, _b, _g in _needs(carousel_week) if _named(db, "r0", text)]
    assert len(said) == 1 and "also chose to post anyway" in said[0]
    assert "Thu 09 May" in posting_view._post_line(db, d, plan)
    a_line = posting_view._post_line(db, a, plan)
    assert "no carousel slot before its deadline" in a_line and not _wrong_reason(a_line)
    _passes(carousel_week, frozen, monday + timedelta(hours=2), 9 * 24)
    assert ("r4", "Thu") in carousel_week.posted
    a = _carousel(db, "r0")
    assert a["status"] == "expired" and a["detail"]["reason"] == "sensitive-topic spacing"
    assert "also chose to post anyway" in posting_view.status_words(db, a)
    assert len([t for t, _b, _g in _needs(carousel_week) if _named(db, "r0", t)]) == 1


def test_a_plain_carousel_post_anyway_displaces_is_shown_and_said_truthfully(carousel_week, frozen, tmp_path):
    """Review 5, blocking 2's third case: Post anyway on A over a newer plain carousel r3 ready the same Thursday. A
    posts; Posting shows A at Thursday (not "after its Reel") and r3 without a slot (not Thursday); r3 gets one
    Needs-you alert and expires recording why, not "it waited over 7 days"."""
    from src import posting_view
    db, monday = _spaced_probe(carousel_week)
    _another(db, tmp_path, "r3", "2", monday)
    _passes(carousel_week, frozen, monday, 2)
    a, r3 = _carousel(db, "r0"), _carousel(db, "r3")
    assert len(_needs(carousel_week)) == 1
    reply = carousel_week.post_carousel_anyway(a["id"])
    assert "Thu 09 May" in reply and _named(db, "r3", reply)
    plan = posting_view.carousel_plan(db)
    assert plan[a["id"]].strftime("%a") == "Thu" and r3["id"] not in plan
    assert "Thu 09 May" in posting_view._post_line(db, _carousel(db, "r0"), plan)
    r3_line = posting_view._post_line(db, r3, plan)
    assert "no carousel slot before its deadline" in r3_line and "Thu 09 May" not in r3_line
    assert "chose to post anyway" in posting_view.wont_post(db, "r3")
    _passes(carousel_week, frozen, monday + timedelta(hours=2), 9 * 24)
    assert carousel_week.posted == [("r0", "Thu")]
    r3 = _carousel(db, "r3")
    assert r3["status"] == "expired" and r3["detail"]["reason"] == "sensitive-topic spacing"
    said = [text for text, _b, _g in _needs(carousel_week) if _named(db, "r3", text)]
    assert len(said) == 1 and "chose to post anyway" in said[0]
    words = posting_view.status_words(db, r3)
    assert "chose to post anyway" in words and not _wrong_reason(words)
    episode, buttons = posting_view.posting_episode(db, "r3")
    assert not _wrong_reason(episode) and "rebook:" not in str(buttons)


def test_post_anyway_inside_the_slots_posting_window_names_that_slot(carousel_week, frozen):
    """Review 5, non-blocking: a slot counts until its posting window closes, for the plan and Post anyway alike."""
    db, monday = _spaced_probe(carousel_week)
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    thursday = post_schedule_slot(monday, 3)
    _passes(carousel_week, frozen, monday, 2)
    frozen.at = thursday + timedelta(minutes=30)
    reply = carousel_week.post_carousel_anyway(_carousel(db, "r0")["id"])
    assert "Thu 09 May" in reply and "can't post" not in reply


def test_an_unconfirmed_sensitive_publication_raises_no_spacing_alert(carousel_week, frozen):
    """Review 5, non-blocking: while another sensitive publication is in flight, no day is known to be spaced or
    not, so nothing is said yet; once it is known, the plan decides."""
    from src import posting_view
    db, monday = carousel_week.db, carousel_week.monday
    carousel_week.alerts = []
    carousel_week._alert = lambda text, buttons=None, group=None: carousel_week.alerts.append((text, buttons, group))
    db.update_run("r1", "publishing")
    _passes(carousel_week, frozen, monday, 6)
    assert _needs(carousel_week) == []
    assert posting_view.wont_post(db, "r0") is None   # not "won't post": it is not known yet
    assert "until another sensitive topic" in posting_view._post_line(
        db, _carousel(db, "r0"), posting_view.carousel_plan(db))
    db.update_run("r1", "approved")
    _passes(carousel_week, frozen, monday + timedelta(hours=6), 9 * 24)
    assert ("r0", "Thu") in carousel_week.posted and _needs(carousel_week) == []


# ------------------------------------------------------------------ proposals and intake

class _Bank:
    def __init__(self, queued):
        self.queued, self.settings = queued, {}

    def list_all_topics(self):
        return ([{"status": "queued"}] * self.queued + [{"status": "queued", "hold": "x"}] * 5
                + [{"status": "published"}] * 30)

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


@pytest.mark.parametrize("queued, calls", [(13, 1), (14, 0), (20, 0)])
def test_nightly_research_runs_only_below_the_queue_floor(queued, calls):
    from src.topic_policy import nightly_proposals
    made = []
    nightly_proposals(_Bank(queued), lambda: made.append(1))
    assert len(made) == calls


def test_nightly_research_never_runs_more_often_than_the_weekly_run_it_replaced(db, monkeypatch):
    """Review 3: up to seven nightly runs a week would raise research spend up to 7x over the weekly Friday job.
    TOPIC_RESEARCH_RUNS_PER_WEEK (default 1: that weekly run) caps the nightly starts per calendar week. The model
    is mocked: no call."""
    import main
    from src import topic_policy, topic_proposer
    assert config.TOPIC_RESEARCH_RUNS_PER_WEEK == 1
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "TOPIC_QUEUE_FLOOR", 10 ** 6)   # always below the floor
    calls = []
    monkeypatch.setattr(topic_proposer, "propose", lambda d, n=3: calls.append(n) or [])

    class Bot:
        def start_operator_job(self, title, work, **kwargs):
            work()

    trigger = main._proposal_trigger(db, Bot(), config.TOPIC_NIGHTLY_PROPOSALS).run_now
    night = datetime(2030, 5, 6, 7, 30, tzinfo=UTC)   # Monday 03:30 Eastern
    for n in range(14):   # two calendar weeks of nights
        topic_policy.nightly_proposals(db, trigger, night + timedelta(days=n))
    assert calls == [3, 3]   # one run a week, the weekly job's size
    monkeypatch.setattr(config, "TOPIC_RESEARCH_RUNS_PER_WEEK", 0)
    topic_policy.nightly_proposals(db, trigger, night + timedelta(days=14))
    assert calls == [3, 3]   # 0 turns it off
    monkeypatch.setattr(config, "TOPIC_RESEARCH_RUNS_PER_WEEK", 2)   # only if the operator raises it
    for n in range(14, 21):
        topic_policy.nightly_proposals(db, trigger, night + timedelta(days=n))
    assert calls == [3, 3, 3, 3]


def test_the_weekly_research_cap_is_a_documented_setting():
    assert "TOPIC_RESEARCH_RUNS_PER_WEEK=1" in (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TOPIC_RESEARCH_RUNS_PER_WEEK" in (ROOT / "src/config.py").read_text(encoding="utf-8")


def test_the_nightly_trigger_asks_research_for_the_configured_count(db, monkeypatch):
    import main
    from src import topic_proposer
    asked = []
    monkeypatch.setattr(topic_proposer, "propose", lambda d, n=3: asked.append(n) or [])   # no model call

    class Bot:
        def start_operator_job(self, title, work, **kwargs):
            work()

    main._proposal_trigger(db, Bot(), config.TOPIC_NIGHTLY_PROPOSALS).run_now()
    assert asked == [3]


def _proposal(**change):
    from src.models import ProposedLocation, Source, TopicProposal
    fields = {"slug": "new_subject", "title": "New subject", "sub_theme": "Engineering",
              "hook": "How did 40 builders raise it?",
              "location": ProposedLocation(name="Site", city="", region="", country="X"),
              "key_facts": ["It took 40 years.", "b", "c"], "hashtags": ["#a", "#ReelsViral"],
              "image_queries": ["subject"], "image_keywords": ["subject"], "why_it_works": "Visible structure",
              "sources": [Source(title="s", url="https://source.example/a")], "region_group": "Europe",
              "sensitivity": ["Warfare and deaths"]}
    return TopicProposal(**{**fields, **change})


def test_the_proposal_card_shows_the_hook_check_the_concrete_fact_and_the_sensitivity():
    from src import topic_proposer
    text = topic_proposer.proposal_text(_proposal())
    assert "Hook passes" in text and "carries a number" in text and "Sensitive topic · Warfare and deaths" in text
    weak = topic_proposer.proposal_text(_proposal(hook="A hook.", key_facts=["a", "b", "c"], sensitivity=[]))
    assert "Hook fails" in weak and "No concrete fact" in weak and "Sensitive" not in weak


def test_an_unassessed_proposal_counts_as_sensitive():
    from src.models import ProposedLocation, TopicProposal
    from src.topic_policy import UNASSESSED
    bare = TopicProposal(slug="s", title="t", sub_theme="x", hook="h", location=ProposedLocation(
        name="n", city="", region="", country="c"), key_facts=[], hashtags=[], image_queries=[], image_keywords=[],
        why_it_works="w", sources=[])
    assert bare.sensitivity == [UNASSESSED] and bare.region_group == "unclassified"


@pytest.fixture
def proposal_bank(db, monkeypatch, tmp_path):
    from src import topic_proposer as tp
    queries = tmp_path / "queries.json"
    queries.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tp, "BANK", db.topic_bank_file)
    monkeypatch.setattr(tp, "QUERIES", queries)
    return queries


def test_a_source_free_proposal_is_refused_and_stays_waiting(db, proposal_bank):
    from src import topic_proposer as tp
    db.set_setting(tp.PENDING_KEY, json.dumps({"new_subject": _proposal(sources=[]).model_dump(mode="json")}))
    before = db.topic_bank_file.read_bytes(), proposal_bank.read_bytes()
    with pytest.raises(ValueError, match="source"):
        tp.add(db, "new_subject")
    assert (db.topic_bank_file.read_bytes(), proposal_bank.read_bytes()) == before
    assert "new_subject" in tp.pending(db)


def test_an_added_proposal_keeps_its_assessment_and_drops_a_failing_hook(db, proposal_bank):
    from src import topic_proposer as tp
    db.set_setting(tp.PENDING_KEY, json.dumps({"new_subject": _proposal(hook="A hook.").model_dump(mode="json")}))
    tp.add(db, "new_subject")
    added = next(t for t in json.loads(db.topic_bank_file.read_text(encoding="utf-8"))["topics"]
                 if t["id"] == "new_subject")
    assert added["hook"] == "" and "#ReelsViral" not in added["hashtags"]
    assert added["sensitivity"] == ["Warfare and deaths"] and added["region_group"] == "Europe"
    assert db.get_topic_by_id("new_subject")["sensitivity"] == ["Warfare and deaths"]


def test_research_drops_the_viral_tag_and_a_source_free_proposal(db, proposal_bank, monkeypatch):
    from src import topic_proposer as tp
    from src.models import Source, TopicProposals
    monkeypatch.setattr(tp, "claude_available", lambda: True)   # the Claude path, every call mocked: no model call
    monkeypatch.setattr("src.free_llm.claude_engine", lambda: True)
    monkeypatch.setattr(tp, "research", lambda *a, **k: ("notes", [Source(title="s", url="https://source.example/a")]))
    monkeypatch.setattr(tp, "structured_call", lambda *a, **k: TopicProposals(topics=[
        _proposal(), _proposal(slug="other_subject", title="Other subject", sources=[])]))
    fresh = tp.propose(db)
    assert [p.slug for p in fresh] == ["new_subject"] and "#ReelsViral" not in fresh[0].hashtags


@pytest.fixture
def add_command(db, proposal_bank, monkeypatch):
    from src import shot_archetypes, unesco
    from src.control import make_command_handler
    monkeypatch.setattr(unesco.UNESCOEngine, "find_site", lambda *a, **k: None)
    monkeypatch.setattr(shot_archetypes, "decompose_topic_visuals",
                        lambda *a, **kw: {"queries": ["site"], "keywords": ["site"]})
    return make_command_handler(db, lambda: None, lambda: "")


def _bank_topic(db, tid):
    return next(t for t in json.loads(db.topic_bank_file.read_text(encoding="utf-8"))["topics"] if t["id"] == tid)


def test_add_records_the_given_source_and_makes_up_no_fact(db, add_command):
    reply = add_command("add", "Pavlopetri Town | https://source.example/pavlopetri")
    topic = _bank_topic(db, "pavlopetri_town")
    assert topic["sources"] == [{"title": "Pavlopetri Town", "url": "https://source.example/pavlopetri"}]
    assert topic["key_facts"] == [] and topic["hook"] == ""
    assert topic["sensitivity"] == ["Editorial sensitivity assessment pending"]
    assert "https://source.example/pavlopetri" in reply and "No concrete fact" in reply


def test_add_without_a_source_cites_the_titles_wikipedia_page(db, add_command):
    add_command("add", "Pavlopetri")
    assert _bank_topic(db, "pavlopetri")["sources"][0]["url"] == "https://en.wikipedia.org/wiki/Pavlopetri"


def test_add_refuses_a_source_that_is_not_a_public_https_page(db, add_command):
    before = db.topic_bank_file.read_bytes()
    reply = add_command("add", "Pavlopetri | http://127.0.0.1/page")
    assert "not added" in reply and db.topic_bank_file.read_bytes() == before


def test_add_takes_key_facts_from_the_matched_unesco_record(db, proposal_bank, monkeypatch):
    from src import control, shot_archetypes
    from src.unesco import UNESCOSite
    monkeypatch.setattr(shot_archetypes, "decompose_topic_visuals",
                        lambda *a, **kw: {"queries": ["site"], "keywords": ["site"]})
    site = UNESCOSite(id_number=364, site="Great Zimbabwe National Monument", states="Zimbabwe",
                      short_description="The city covers nearly 80 ha. It was a trading centre.")
    control._add_topic_to_bank(db, "Great Zimbabwe National Monument", u_site=site)
    topic = _bank_topic(db, "great_zimbabwe_national_monument")
    assert topic["key_facts"] == ["The city covers nearly 80 ha.", "It was a trading centre."]
    assert topic["sources"][0]["url"] == "https://whc.unesco.org/en/list/364/"


def test_a_held_topic_cannot_be_made_by_naming_it(sensitive_db, monkeypatch):
    import main
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db = sensitive_db
    coordinator.topic_engine = type("Engine", (), {"get_topic_by_id": staticmethod(sensitive_db.get_topic_by_id)})()
    coordinator.health_checker = type("Health", (), {"run_all": lambda self, **k: {"details": {}, "all_passed": True}})()
    coordinator.lifecycle_manager = type("Life", (), {"purge_intermediate_assets": lambda self: None})()
    monkeypatch.setattr(config, "effective_format", lambda fmt: fmt)
    monkeypatch.setattr(main, "warn_missing_geo_files", lambda: None)
    monkeypatch.setattr(main, "warn_missing_raqm", lambda: None)
    with pytest.raises(ValueError, match="on hold"):
        coordinator.run_cycle(topic_id="held", content_format="reel")


# ------------------------------------------------------------------ port onto 21b4c5c0 (UI-01, QF-81c)

def _clean_desk(carousel_week, monkeypatch, other="booked"):
    """Review 4's probe with the coordinator's real _alert writing to a bot over UI-01a's fake Bot API."""
    import test_ui01a_notifications_panel as ui01a
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)
    db, monday = _spaced_probe(carousel_week, other)
    del carousel_week._alert   # the class's own: notifications.say(telegram_bot.notify, ...)
    bot, fake = ui01a.make_bot(db, monkeypatch)
    bot.layout.ensure()
    carousel_week.telegram_bot = bot
    return db, monday, bot, fake


def _carousel_items(db):
    from src import notifications
    return [i for i in notifications.open_items(db) if i["dedupe_key"].startswith("post-failed:")]


def _spacing_handler(db, coordinator=None):
    from src.control import make_command_handler
    actions = ({"carspace": coordinator.post_carousel_anyway, "carexpire": coordinator.expire_spaced_carousel}
               if coordinator else None)
    return make_command_handler(db, lambda: "Mon 07:00", lambda: "", run_actions=actions)


def _item_tap(bot, item, label, handler):
    import test_ui01a_notifications_panel as ui01a

    from src import notifications
    action = next(a for a in item["actions"] if a["text"] == label)
    ui01a.tap(bot, f"n:{item['id']}:{notifications.action_ref(action)}", handler)


def _sentence_case(label):
    words = label.split(" ", 1)[1]   # after its emoji
    return words[0].isupper() and words[1:] == words[1:].lower()


def test_the_spacing_notice_is_one_needs_you_item_per_carousel_with_both_actions(carousel_week, monkeypatch, frozen):
    """The clean chat (UI-01): the "no spaced slot" notice is a Needs-you item under its booking's key, with Post
    anyway and Let it expire as its actions, and no message or button of its own; every later pass keeps one item."""
    db, monday, _bot, fake = _clean_desk(carousel_week, monkeypatch)
    sends = len(fake.sends())
    _passes(carousel_week, frozen, monday, 48)
    car = _carousel(db, "r0")
    items = _carousel_items(db)
    assert len(items) == 1
    item = items[0]
    assert item["section"] == "needs" and item["dedupe_key"] == f"post-failed:{car['id']}" and item["run_id"] == "r0"
    # its 41-character head is past the list's 40 (notifications.TITLE_LIMIT): clipped there, whole in the body
    assert item["title"].startswith("Carousel waiting: sensitive-topic")
    assert "Carousel waiting: sensitive-topic spacing" in item["body"] and "Subject 0" in item["body"]
    assert item["actions"] == [{"text": "▶️ Post anyway", "callback_data": f"carspace:{car['id']}"},
                               {"text": "⏹ Let it expire", "callback_data": f"carexpire:{car['id']}"}]
    assert all(len(a["text"]) <= 28 and _sentence_case(a["text"]) for a in item["actions"])
    # no message of its own: only the item's ring, which carries no buttons
    assert all("reply_markup" not in d and "Post anyway" not in d["text"] for d in fake.sends()[sends:])


def test_a_refused_post_anyway_keeps_the_item_saying_why_and_a_done_one_closes_it(carousel_week, monkeypatch, frozen,
                                                                                  tmp_path):
    """Post anyway closes its item only on a structured success (notifications.Outcome): with no daemon, and when
    another carousel chosen first holds the only slot, the item stays open with "Not done" and its reason."""
    from src import notifications
    db, monday, bot, _fake = _clean_desk(carousel_week, monkeypatch)
    _another(db, tmp_path, "r4", "0", monday)
    _passes(carousel_week, frozen, monday, 2)
    a, d = _carousel(db, "r0"), _carousel(db, "r4")
    items = {i["dedupe_key"]: i for i in _carousel_items(db)}
    a_item, d_item = items[f"post-failed:{a['id']}"], items[f"post-failed:{d['id']}"]
    _item_tap(bot, a_item, "▶️ Post anyway", _spacing_handler(db))   # no daemon
    after = notifications.get(db, a_item["id"])
    assert after["state"] == "open" and "only works while the daemon is running" in after["note"]
    assert not _carousel(db, "r0")["detail"].get("spacing_override")
    _item_tap(bot, a_item, "▶️ Post anyway", _spacing_handler(db, carousel_week))
    assert notifications.get(db, a_item["id"])["state"] == "handled"
    assert _carousel(db, "r0")["detail"]["spacing_override"]
    _item_tap(bot, d_item, "▶️ Post anyway", _spacing_handler(db, carousel_week))   # A holds the only slot
    after = notifications.get(db, d_item["id"])
    assert after["state"] == "open" and "can't take that slot" in after["note"]
    assert "⚠️ <i>Not done: " in notifications.render(db)[0]   # the item page it was tapped from says why
    assert not _carousel(db, "r4")["detail"].get("spacing_override")
    _item_tap(bot, d_item, "⏹ Let it expire", _spacing_handler(db, carousel_week))
    assert notifications.get(db, d_item["id"])["state"] == "handled"
    assert _carousel(db, "r4")["status"] == "expired"


def test_the_spacing_actions_say_their_outcome(carousel_week):
    """Both are judged by their handler's notifications.Outcome, never by their words."""
    from src.telegram_bot import ONE_SHOT, SAY_THEIR_OUTCOME
    assert {"carspace", "carexpire"} <= SAY_THEIR_OUTCOME and {"carspace", "carexpire"} <= ONE_SHOT
    for reply in (carousel_week.post_carousel_anyway(999), carousel_week.expire_spaced_carousel(999)):
        assert reply.done is False and "not waiting" in reply


def test_a_carousel_without_a_slot_is_one_item_under_its_bookings_key(carousel_week, monkeypatch, frozen, tmp_path):
    """The plain carousel Post anyway displaces gets its "Carousel without a slot" notice as one Needs-you item."""
    db, monday, bot, _fake = _clean_desk(carousel_week, monkeypatch)
    _another(db, tmp_path, "r3", "2", monday)
    _passes(carousel_week, frozen, monday, 2)
    _item_tap(bot, _carousel_items(db)[0], "▶️ Post anyway", _spacing_handler(db, carousel_week))
    assert _carousel(db, "r0")["detail"]["spacing_override"]
    _passes(carousel_week, frozen, monday + timedelta(hours=2), 6)
    r3 = _carousel(db, "r3")
    items = [i for i in _carousel_items(db) if i["dedupe_key"] == f"post-failed:{r3['id']}"]
    assert len(items) == 1 and items[0]["section"] == "needs"
    assert items[0]["title"].startswith("Carousel without a slot")
    assert [x["text"] for x in items[0]["actions"]] == ["⏹ Let it expire"]


def test_with_the_switch_off_the_spacing_notice_reaches_notify_as_on_50fbade8(carousel_week, monkeypatch, frozen):
    """TELEGRAM_CLEAN_CHAT=false: notify(text, buttons=[[Post anyway, Let it expire]], group="needs"), no item."""
    from src import notifications
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    db, monday = _spaced_probe(carousel_week)
    del carousel_week._alert
    calls = []
    carousel_week.telegram_bot = type("Bot", (), {"notify": lambda self, text, **kw: calls.append((text, kw)) or True})()
    _passes(carousel_week, frozen, monday, 48)
    car = _carousel(db, "r0")
    said = [(t, kw) for t, kw in calls if "sensitive-topic spacing" in t]
    assert len(said) == 1 and said[0][1] == {
        "buttons": [[{"text": "▶️ Post anyway", "callback_data": f"carspace:{car['id']}"},
                     {"text": "⏹ Let it expire", "callback_data": f"carexpire:{car['id']}"}]], "group": "needs"}
    notifications.ensure(db)
    assert notifications.open_items(db) == []


def test_a_spacing_wait_runs_before_the_final_holds_and_never_takes_the_slot(carousel_week):
    """QF-81c composes with the spacing wait: spacing is checked first, on the ready list. A spacing-held carousel
    whose slides are gone is neither held nor posted at a too-close slot (it waits), and with no other ready
    carousel the slot is not taken."""
    db, _monday = _spaced_probe(carousel_week)
    Path(_carousel(db, "r0")["files"][0]).unlink()   # QF-81c: it would be held for good at any slot it took
    db.update_scheduled_post(_carousel(db, "r2")["id"], "cancelled")
    carousel_week.week(4)   # Thursday, its only slot in its window, is next to B's Wednesday Reel
    car = _carousel(db, "r0")
    assert car["status"] == "waiting" and car["detail"]["waiting_for"] == "spacing" and not car["detail"].get("held")
    assert not any("Carousel not posted" in a[0] for a in carousel_week.alerts)
    assert not db.get_setting("carousel_posted_on")   # a spacing wait never takes the slot
    assert carousel_week.posted == []


def test_a_spacing_wait_leaves_the_slot_to_the_next_ready_carousel_past_a_final_hold(carousel_week, tmp_path):
    """A spacing wait never loses a carousel its slot either: at Thursday A waits (spacing), r3's slides are gone
    (QF-81c's final hold), and plain r2 takes the slot."""
    db, monday = _spaced_probe(carousel_week)
    _another(db, tmp_path, "r3", "2", monday)
    Path(_carousel(db, "r3")["files"][0]).unlink()
    carousel_week.week(4)
    assert carousel_week.posted == [("r2", "Thu")]
    assert _carousel(db, "r0")["status"] == "waiting" and _carousel(db, "r3")["detail"].get("final")


def test_post_anyway_never_overrides_a_final_hold_and_the_slot_passes_on(carousel_week):
    """Post anyway sets spacing aside, never QF-81c's final hold: its slides gone, it is held for good at its slot
    with one "Carousel not posted" notice, and the plain carousel it displaced takes the slot in its place."""
    db, _monday = _spaced_probe(carousel_week)
    carousel_week.week(3)
    car = _carousel(db, "r0")
    assert carousel_week.post_carousel_anyway(car["id"]).done
    Path(car["files"][0]).unlink()
    carousel_week.week(4)
    car = _carousel(db, "r0")
    assert car["status"] == "failed" and car["detail"]["final"] and car["detail"]["held"]
    assert ("r0", "Thu") not in carousel_week.posted and ("r2", "Thu") in carousel_week.posted
    assert len([a for a in carousel_week.alerts if "Carousel not posted" in a[0]]) == 1


def test_the_review_panel_shows_the_sensitivity_chip_and_the_waiting_line(sensitive_db, tmp_path, monkeypatch):
    """UI-01e's review panel carries both cards' QF-73 lines: the "Sensitive topic" chip among its notes, and
    "Waiting" (not "posted as soon as you approve") while another sensitive publication is unconfirmed; its record
    is the live card's (run_id passed)."""
    import test_ui01e_review_panel as ui01e

    from src import publishable, telegram_bot, topic_proposer
    from src.notifications import plain
    for name, value in (("TELEGRAM_CLEAN_CHAT", True), ("PUSH_ALERT_SECONDS", 8.0), ("REVIEW_UNDO_MINUTES", 10.0),
                        ("REVIEW_APPROVE_UNDO_LEAD_MINUTES", 15.0)):
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(telegram_bot, "_video_dimensions", lambda path: {"width": 720, "height": 1280})
    db = sensitive_db
    bot, fake = ui01e.make_bot(db, monkeypatch)
    root, payload = ui01e.a_review(db, tmp_path, monkeypatch, "sens")
    assert payload["topic_id"] == "0"
    payload["topic_origin_note"] = topic_proposer.origin_note(db.get_topic_by_id("0"))
    db.update_run("sens", "pending_approval", script_payload=payload)
    db.update_run("r1", "publish_unknown")   # sensitive topic 1's upload: its outcome not known yet
    mid = ui01e.deliver(bot, db, root, payload, "sens")
    summary = plain(ui01e.caption(fake, mid))
    assert "Sensitive topic · Human remains" in summary
    posted = publishable.load_record(db, "sens")
    assert posted.get("scheduling_hold") and "Waiting:" in summary and "posted as soon as you approve" not in summary
    live = publishable.card_record(payload, db, run_dir=root, topic_id="0", posting=posted["posting"],
                                   master=root / "master.mp4", cover=root / "thumbnail_cover.jpg", run_id="sens")
    assert posted["scheduling_hold"] == live["scheduling_hold"]
