"""QF-73b: carousel spacing has one source of truth (the slot loop posts the carousel plan's pick), spacing is checked
again under the publication lock, a Post anyway reply names the slot the carousel really takes, a carousel that only
loses to newer ones is said instead of expiring silently, a carousel's notice closes once it posts or expires, and
every Needs-you sender carries its item key (UI-01g, QF-88c)."""
import ast
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
import test_qf73_topic_bank as qf73

from src import config

ROOT = Path(__file__).resolve().parent.parent
MEASURE = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-73b" / "measure.py"

sensitive_db = qf73.sensitive_db     # QF-73's fixtures: a sensitive topic 0 (r0), 1 (r1) and a plain topic 2 (r2)
carousel_week = qf73.carousel_week
frozen = qf73.frozen


def _measure():
    spec = importlib.util.spec_from_file_location("qf73b_measure", MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record_alerts(coord):
    coord.alerts = []
    coord._alert = lambda text, buttons=None, group=None: coord.alerts.append((text, buttons, group))


def _notices_for(coord, post_id):
    return [a for a in coord.alerts if a[2] == "needs" and any(
        b.get("callback_data") == f"carexpire:{post_id}" for row in a[1] or [] for b in row)]


def _pick(outlook, slot):
    return next((pid for pid, at in outlook["plan"].items() if at == slot), None)


# ------------------------------------------------------------------ 1. one source of truth

def test_the_slot_loop_posts_the_carousel_the_plan_names(carousel_week, monkeypatch):
    """The slot loop takes its pick from posting_view.carousel_outlook, the answer every screen shows: when the plan
    names the older carousel for Thursday, that one posts (it reimplemented the rule and posted the newest)."""
    from src import posting_view
    db = carousel_week.db
    real = posting_view.carousel_outlook
    r2 = qf73._carousel(db, "r2")["id"]

    def plan_names_r2(db_, now=None, *args, **kwargs):
        out = real(db_, now, *args, **kwargs)
        slot = next(iter(out["plan"].values()), None)
        if slot is not None:
            out["plan"] = {r2: slot}
        return out
    monkeypatch.setattr(posting_view, "carousel_outlook", plan_names_r2)
    carousel_week.week(4)
    assert carousel_week.posted == [("r2", "Thu")]


def test_a_carousel_whose_reel_is_late_is_never_planned_in_a_slot_it_cannot_take(carousel_week, frozen, tmp_path):
    """A plain episode's Reel was booked for Sunday and is still held (a QF-110 hold): its carousel cannot post
    before its Reel does. The plan the screens show a minute before each slot is what the slot loop does there
    (before: Tuesday's slot named that carousel, and nothing posted)."""
    from src import post_schedule, posting_view
    db, monday = carousel_week.db, carousel_week.monday
    sunday = post_schedule.reel_time((monday - timedelta(days=1)).date())
    qf73._another(db, tmp_path, "r3", "2", sunday, posted=False)
    seen = []
    for days in range(1, 4):
        slot = post_schedule.carousel_time((monday + timedelta(days=days)).date())
        frozen.at = slot - timedelta(minutes=1)
        predicted = _pick(posting_view.carousel_outlook(db, frozen.at), slot)
        before = list(carousel_week.posted)
        frozen.at = carousel_week.slot_now = slot + timedelta(minutes=1)
        carousel_week._post_carousel_if_due(frozen.at)
        done = [run for run, _day in carousel_week.posted[len(before):]]
        predicted_run = next((p["run_id"] for p in db.list_scheduled_posts(
            ("waiting", "published")) if p["id"] == predicted), None)
        seen.append((slot.strftime("%a"), predicted_run, done[0] if done else None))
    assert seen == [("Tue", None, None), ("Wed", None, None), ("Thu", "r0", "r0")]


@pytest.mark.parametrize("mode", ["rolling", "taps", "held"])
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_three_weeks_of_slots_plan_equals_action(seed, mode, tmp_path):
    """Review 6's simulation, twelve scenarios (measure.py): 21 daily Reels, every carousel slot for 31 days. At
    every slot the plan's pick (a minute before and at the slot) is what posted; every Post anyway reply names the
    slot its carousel posted at; no carousel expires without its notice, none gets the same notice twice, and
    none is left waiting."""
    result = _measure().simulate(seed, mode, tmp_path / "sim")
    assert result["slots"] >= 13 and result["posted"] >= 8
    assert result["detail"]["mismatches"] == []
    assert result["detail"]["replies_not_kept"] == []
    assert result["detail"]["silent"] == []
    assert result["same_reason_max"] <= 1
    assert result["waiting_at_end"] == 0


# ------------------------------------------------------------------ 2. spacing checked again under the lock

def _claim_other_sensitive(db):
    """Another sensitive topic's Reel is claimed for publication now (Post now, a retry): db.claim_publication."""
    db.update_run("r1", "publishing")


@pytest.mark.parametrize("drawn", [False, True])
def test_a_sensitive_claim_while_the_carousel_is_prepared_keeps_it_waiting(carousel_week, monkeypatch, drawn):
    """Sensitive A's carousel is chosen for Thursday; while it is drawn (or its review binding is read), sensitive
    B's Reel is claimed for publication. Under the publication lock, just before posting, spacing is checked again:
    A is not posted, it keeps waiting with the reason, the day's slot is freed, and it posts once B is settled."""
    from src import carousel_lazy
    from src.topic_policy import UNCONFIRMED
    db = carousel_week.db
    db.update_scheduled_post(qf73._carousel(db, "r2")["id"], "cancelled")
    car = qf73._carousel(db, "r0")
    slide = car["files"][0]
    if drawn:
        def binding(run_id):
            _claim_other_sensitive(db)
        carousel_week._approval_broken = binding
    else:
        db.update_scheduled_post(car["id"], "waiting", files=[])
        monkeypatch.setattr(carousel_lazy, "plan_problem", lambda run_dir: None)

        def render(*args, **kwargs):
            _claim_other_sensitive(db)
            return [slide]
        monkeypatch.setattr(carousel_lazy, "render", render)
    carousel_week.week(4)
    car = qf73._carousel(db, "r0")
    assert carousel_week.posted == []
    assert car["status"] == "waiting" and car["detail"]["waiting_for"] == "spacing"
    assert car["detail"]["spacing"] == UNCONFIRMED and car["files"] == [slide]
    assert db.get_setting("carousel_posted_on") != carousel_week.slot_now.astimezone(config_tz()).date().isoformat()
    db.update_run("r1", "approved")   # B's claim settled without a publication
    carousel_week._approval_broken = lambda run_id: None
    carousel_week._post_carousel_if_due(carousel_week.slot_now + timedelta(minutes=30))
    assert carousel_week.posted == [("r0", "Thu")]


def config_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(config.POST_TIMEZONE)


# ------------------------------------------------------------------ 3. a Post anyway reply names the real slot

@pytest.mark.parametrize("carousel_week", [5], indirect=True)
def test_a_later_post_anyway_never_moves_the_slot_an_earlier_reply_named(carousel_week, frozen, tmp_path):
    """Review 6's case: A and a newer D (both sensitive) wait behind B's Wednesday Reel, with three slots in their
    window. Post anyway on A names Tuesday; Post anyway on D, later, must not take Tuesday from A (both carry
    priority and the newer won it, so A posted Thursday after a reply naming Tuesday): D gets the next slot and its
    reply names that one. Each carousel posts at the slot its reply named."""
    db, saturday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(qf73._carousel(db, "r2")["id"], "cancelled")
    db.add_scheduled_post("r1", "reel", qf73._evening(saturday, 4), [])
    qf73._another(db, tmp_path, "r4", "0", saturday)
    _record_alerts(carousel_week)
    qf73._passes(carousel_week, frozen, saturday, 2)
    a_reply = carousel_week.post_carousel_anyway(qf73._carousel(db, "r0")["id"])
    frozen.at += timedelta(hours=5)
    d_reply = carousel_week.post_carousel_anyway(qf73._carousel(db, "r4")["id"])
    assert a_reply.done and d_reply.done
    assert "Tue 14 May" in a_reply and "Tue 14 May" not in d_reply
    qf73._passes(carousel_week, frozen, frozen.at, 7 * 24, every=60)
    days = dict(carousel_week.posted)
    assert _named_day(a_reply) == days["r0"] == "Tue" and _named_day(d_reply) == days["r4"]


def _named_day(reply):
    import re
    return re.search(r"posts at .*?(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d May", reply).group(1)


# ------------------------------------------------------------------ 4. a carousel that only loses to newer ones

def test_a_plain_carousel_losing_only_to_newer_ones_is_said_once_and_expires_with_its_reason(carousel_week, frozen,
                                                                                         tmp_path):
    """Plain r2's Reel posted Monday; its window has one carousel slot, Thursday. A newer plain episode (r5, Reel
    Tuesday) takes it, newest first. Every screen says r2 waits with no slot, one Needs-you notice says why (Post
    anyway and Let it expire), and it expires recording the newer carousel: never "after its Reel" up to a silent
    "waited over 7 days" (QF-110: never silent)."""
    from src import post_schedule, posting_view
    db, monday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(qf73._carousel(db, "r0")["id"], "cancelled")
    tuesday = post_schedule.reel_time((monday + timedelta(days=1)).date()) + timedelta(minutes=1)
    qf73._another(db, tmp_path, "r5", "2", tuesday)
    _record_alerts(carousel_week)
    qf73._passes(carousel_week, frozen, tuesday, 2)
    r2 = qf73._carousel(db, "r2")
    line = posting_view._post_line(db, r2, posting_view.carousel_plan(db))
    assert "waiting: no carousel slot before its deadline" in line and "after its Reel" not in line
    said = _notices_for(carousel_week, r2["id"])
    assert len(said) == 1
    text, buttons, _group = said[0]
    assert "Carousel without a slot" in text and "newer carousel" in text and "Thu 09 May" in text
    assert {b["text"] for row in buttons for b in row} == {"▶️ Post anyway", "⏹ Let it expire"}
    assert "newer carousel" in posting_view.wont_post(db, "r2")
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=2), 8 * 24, every=60)
    assert carousel_week.posted == [("r5", "Thu")]
    r2 = qf73._carousel(db, "r2")
    words = posting_view.status_words(db, r2)
    assert r2["status"] == "expired" and "newer carousel" in words and "waited over 7 days" not in words
    assert len(_notices_for(carousel_week, r2["id"])) == 1


def test_post_anyway_on_a_carousel_losing_to_newer_ones_takes_its_slot_and_says_nothing_of_spacing(carousel_week,
                                                                                                   frozen, tmp_path):
    """Post anyway on r2 puts it ahead of the newer r5 at Thursday, the slot its reply names; r5 still has Tuesday 14
    May, so nobody is left without a slot."""
    from src import post_schedule
    db, monday = carousel_week.db, carousel_week.monday
    db.update_scheduled_post(qf73._carousel(db, "r0")["id"], "cancelled")
    tuesday = post_schedule.reel_time((monday + timedelta(days=1)).date()) + timedelta(minutes=1)
    qf73._another(db, tmp_path, "r5", "2", tuesday)
    _record_alerts(carousel_week)
    qf73._passes(carousel_week, frozen, tuesday, 2)
    reply = carousel_week.post_carousel_anyway(qf73._carousel(db, "r2")["id"])
    assert reply.done and "Thu 09 May" in reply and "gives up that slot" not in reply
    assert "Sensitive-topic spacing" not in reply   # nothing about spacing was set aside: it is not sensitive
    qf73._passes(carousel_week, frozen, tuesday + timedelta(hours=2), 8 * 24, every=60)
    assert carousel_week.posted == [("r2", "Thu"), ("r5", "Tue")]


# ------------------------------------------------------------------ 5. a carousel's notice closes when it is answered

def _real_post_photos(coord, monkeypatch):
    """The coordinator's own _post_photos, with the publisher mocked (nothing is sent)."""
    from types import SimpleNamespace
    del coord._post_photos
    coord.publisher = SimpleNamespace(publish_photos=lambda *args, **kwargs: {"status": "published"})
    monkeypatch.setattr(coord, "_carousel_request", lambda run_id, payload, files: ("caption", ""), raising=False)


def test_a_spacing_item_closes_when_its_carousel_posts(carousel_week, monkeypatch, frozen):
    from src import notifications
    db, monday, _bot, _fake = qf73._clean_desk(carousel_week, monkeypatch)
    _real_post_photos(carousel_week, monkeypatch)
    qf73._passes(carousel_week, frozen, monday, 2)
    car = qf73._carousel(db, "r0")
    key = f"post-failed:{car['id']}"
    assert notifications.has_open(db, key)
    reel = next(p for p in db.list_scheduled_posts(("pending",), run_id="r1") if p["kind"] == "reel")
    db.update_scheduled_post(reel["id"], "cancelled")   # B's Reel is cancelled: Thursday is spaced again
    qf73._passes(carousel_week, frozen, monday + timedelta(hours=2), 4 * 24, every=60)
    assert qf73._carousel(db, "r0")["status"] == "published"
    assert not notifications.has_open(db, key)


def test_a_spacing_item_closes_when_its_carousel_expires(carousel_week, monkeypatch, frozen):
    from src import notifications
    db, monday, _bot, _fake = qf73._clean_desk(carousel_week, monkeypatch)
    qf73._passes(carousel_week, frozen, monday, 2)
    car = qf73._carousel(db, "r0")
    key = f"post-failed:{car['id']}"
    assert notifications.has_open(db, key)
    qf73._passes(carousel_week, frozen, monday + timedelta(hours=2), 9 * 24, every=120)
    assert qf73._carousel(db, "r0")["status"] == "expired"
    assert not notifications.has_open(db, key)
    closed = [i for i in notifications.recent(db) if i["dedupe_key"] == key]
    assert closed and closed[0]["state"] == notifications.RESOLVED


# ------------------------------------------------------------------ UI-01g and QF-88c: every Needs-you item is keyed

PANEL_MODULES = {"src/telegram_bot.py", "src/notifications.py", "src/layout_repost.py", "src/review_panel.py"}
SENDERS = ("_alert", "_notice", "notify", "say")


def _switched_off(tree):
    """Nodes of TypeError fallbacks and of the clean chat's switch-off branches (the earlier chat has no items)."""
    def mentions(test):
        return any(isinstance(x, ast.Attribute) and x.attr == "TELEGRAM_CLEAN_CHAT" for x in ast.walk(test))
    out = {id(sub) for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
           and isinstance(node.type, ast.Name) and node.type.id == "TypeError" for sub in ast.walk(node)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test.operand if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not) else None
        if test is not None and mentions(test):
            out |= {id(sub) for branch in node.body for sub in ast.walk(branch)}
        elif mentions(node.test):
            out |= {id(sub) for branch in node.orelse for sub in ast.walk(branch)}
    return out


def _unkeyed(source, where):
    """(Needs-you senders without key=, direct _alert(group=) calls) of a module's source."""
    tree = ast.parse(source)
    off = _switched_off(tree)
    unkeyed, raw = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in SENDERS:
            continue
        words = {k.arg: k.value for k in node.keywords}
        if name == "_alert" and "group" in words:
            raw.append(f"{where}:{node.lineno}")
        needs = any(isinstance(words.get(w), ast.Constant) and words[w].value == "needs" for w in ("section", "group"))
        if needs and "key" not in words and id(node) not in off:
            unkeyed.append(f"{where}:{node.lineno}")
    return unkeyed, raw


def _modules():
    paths = ["main.py"] + sorted(f"src/{p.name}" for p in (ROOT / "src").glob("*.py"))
    return [p for p in paths if p not in PANEL_MODULES]


def test_every_needs_you_sender_carries_its_item_key_and_none_calls_alert_with_a_group():
    """UI-01g: UI-01a's scan took group= without key=; an unkeyed Needs-you notice becomes an item keyed by its
    title, tied to no booking or run, so two runs' notices merge and one run's never closes. Outside the panel
    modules every Needs-you sender names its key, and a coordinator notice goes through _notice(section=...), never
    a direct _alert(group=...)."""
    found = [_unkeyed((ROOT / path).read_text(encoding="utf-8"), path) for path in _modules()]
    assert [line for unkeyed, _raw in found for line in unkeyed] == []
    assert [line for _unkeyed_, raw in found for line in raw] == []
    assert len(_modules()) > 100   # the scan reads every module


def test_the_key_scan_catches_a_raw_alert_and_an_unkeyed_needs_notice():
    """Mutation check: QF-73's raw call, restored, fails both rules; the switch-off branch is left alone."""
    source = (ROOT / "main.py").read_text(encoding="utf-8") + (
        "\n\nclass _Mutant:\n    def notice(self, text):\n        self._alert(text, buttons=[], group=\"needs\")\n"
        "        _notice(self, text, section=\"needs\", actions=[])\n"
        "        if not config.TELEGRAM_CLEAN_CHAT:\n            self.telegram_bot.notify(text, group=\"needs\")\n")
    unkeyed, raw = _unkeyed(source, "main.py")
    lines = len((ROOT / "main.py").read_text(encoding="utf-8").splitlines())
    assert [int(x.rsplit(":", 1)[1]) - lines for x in raw] == [5]
    assert [int(x.rsplit(":", 1)[1]) - lines for x in unkeyed] == [5, 6]


def test_both_auto_approve_refusals_are_one_item_per_run(monkeypatch):
    """QF-88c: the two "Auto-approve refused" notices carry a per-run key, so two runs' refusals are two items and a
    run's repeat replaces its own."""
    import types

    import main
    from src import release
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    calls = []
    me = types.SimpleNamespace(_alert=lambda text, **item: calls.append((text, item)),
                               db=types.SimpleNamespace(run_label=lambda run_id: run_id))
    used = {"picture_fit": {"generated": [{"beat": 3, "line": "x"}]}}
    for run_id in ("r1", "r2"):
        assert main.PipelineCoordinator._auto_approve_allowed(me, run_id, used, True) is False
    monkeypatch.setattr(release, "held_flash", lambda report: [{"start": 1.0}])
    for run_id in ("r1", "r2"):
        assert main.PipelineCoordinator._held_for_look(me, run_id, {"checks": []}) is True
    keys = [(item["key"], item["replace"], item["section"]) for _text, item in calls]
    assert keys == [("autoapprove-illustration:r1", True, "needs"), ("autoapprove-illustration:r2", True, "needs"),
                    ("autoapprove-flash:r1", True, "needs"), ("autoapprove-flash:r2", True, "needs")]
