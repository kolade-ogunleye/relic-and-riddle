"""QF-71: only Needs you and the review card ring; every other message is sent with disable_notification.

In the earlier layout (TELEGRAM_CARDS_V2 off, the clean chat off) every notice rang, and so did the run's progress card
and each album and detail message around the review card. In either layout a coordinator's Needs-you notice
(_notice(..., section="needs") with no group) went out as "studio": silent in V2 (a missed alarm), and the section
that says it needs you never reached notify (say() leaves it out with the clean chat off).
"""

from types import SimpleNamespace

import pytest

import main
from src import config
from src.telegram_bot import TelegramApprovalBot

TOPICS = {"studio": 1, "needs": 2, "reviews": 3, "runs": 4, "info": 5}


@pytest.fixture(params=[False, True], ids=["v1", "v2"])
def layout(request, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", request.param)
    monkeypatch.setattr(config, "TELEGRAM_TOPICS", TOPICS, raising=False)
    monkeypatch.setattr(config, "RELIC_STRICT_TOPICS", False, raising=False)
    return request.param


def _bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline_qf71", chat_id="555", approver_ids={"555"})
    calls = []

    def api(method, **kw):
        calls.append((method, dict(kw.get("data") or {})))
        return {"message_id": 100 + len(calls)}
    monkeypatch.setattr(bot, "_api", api)
    return bot, calls


def _rings(data):
    return str(data.get("disable_notification", "")).lower() not in ("true", "1")


@pytest.mark.parametrize("group", ["info", "runs", "studio", "reviews"])
def test_a_routine_notice_is_silent_in_either_layout(db, monkeypatch, layout, group):
    bot, calls = _bot(db, monkeypatch)
    bot.notify("x", group=group)
    sent = [d for m, d in calls if m == "sendMessage"]
    assert sent and not _rings(sent[-1])


def test_a_needs_you_notice_rings_in_either_layout(db, monkeypatch, layout):
    bot, calls = _bot(db, monkeypatch)
    bot.notify("x", group="needs")
    bot.notify("y", group="studio", section="needs")
    sent = [d for m, d in calls if m == "sendMessage"]
    assert len(sent) == 2 and all(_rings(d) for d in sent)


def test_an_explicitly_silent_needs_notice_stays_silent(db, monkeypatch, layout):
    bot, calls = _bot(db, monkeypatch)
    bot.notify("x", group="needs", silent=True)
    assert not _rings([d for m, d in calls if m == "sendMessage"][-1])


def test_a_coordinators_needs_you_notice_rings_and_goes_to_needs(db, monkeypatch, layout):
    """_notice(..., section="needs") with no group: 19 call sites (a failed run, Auto-approve refused, Blocked at the
    release gate, a missed slot...). They went out as "studio"."""
    bot, calls = _bot(db, monkeypatch)
    owner = SimpleNamespace(telegram_bot=bot)
    owner._alert = lambda text, buttons=None, group=None, **item: main.PipelineCoordinator._alert(owner, text, buttons,
                                                                                                  group, **item)
    main._notice(owner, "❌ <b>Run failed</b>", section="needs", key="fail:r")
    main._notice(owner, "🗓 <b>Post scheduled</b>", section="info")
    needs, info = [d for m, d in calls if m == "sendMessage"]
    assert _rings(needs) and not _rings(info)
    if layout:
        assert needs["message_thread_id"] == TOPICS["needs"]


def test_the_progress_card_is_silent(db, monkeypatch, layout):
    bot, calls = _bot(db, monkeypatch)
    db.create_run("r1", db.list_all_topics()[0]["id"])
    bot.send_progress_card("r1", "Topic")
    sent = [d for m, d in calls if m == "sendMessage"]
    assert sent and not any(_rings(d) for d in sent)


def test_the_review_cards_albums_are_silent_and_the_card_rings_once(db, monkeypatch, tmp_path):
    from test_ui02_receipt_and_v2_card import fixture_run
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", False)
    bot, calls = _bot(db, monkeypatch)
    root, payload, _ = fixture_run(db, tmp_path, monkeypatch)
    bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, root.name)
    sends = [(m, d) for m, d in calls if m.startswith("send") and m != "sendChatAction"]
    ringing = [(m, d) for m, d in sends if _rings(d)]
    assert len(sends) > 1
    assert len(ringing) == 1 and "regen:" in ringing[0][1].get("reply_markup", "")   # the card (its gate blocks Approve)


# ---------------------------------------------------------------------------------------------- round 2
def test_a_notice_naming_no_group_and_no_section_is_silent_in_either_layout(db, monkeypatch, layout):
    """Operator, 2026-11-16: one rule, only Needs you and the review card ring."""
    bot, calls = _bot(db, monkeypatch)
    bot.notify("x")
    assert not _rings([d for m, d in calls if m == "sendMessage"][-1])


def test_no_needs_you_call_site_is_sent_silent():
    """Review 1: three Needs-you notices (Publishing accounts, Photo sets not ready, the earlier layout's improvement
    proposal card) carried silent=True. Every notify/say/_notice call naming group or section "needs" rings."""
    import ast
    root = main.Path(main.__file__).resolve().parent
    silent = []
    for path in sorted((root / "src").glob("*.py")) + [root / "main.py"]:
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name)):
                kw = {k.arg: k.value for k in n.keywords if k.arg}
                needs = any(isinstance(kw.get(k), ast.Constant) and kw[k].value == "needs" for k in ("group", "section"))
                if needs and isinstance(kw.get("silent"), ast.Constant) and kw["silent"].value is True:
                    silent.append(f"{path.name}:{n.lineno}")
    assert silent == []


def test_a_batch_that_stopped_on_its_own_and_a_late_approvals_missed_slot_are_needs_you():
    """A /produce batch that stopped because its episode did not finish cleanly is production the operator started
    and expects to go on: Needs you. The missed-slot notice of a late approval goes where _reschedule's does."""
    text = main.Path(main.__file__).read_text(encoding="utf-8")
    assert text.count("\"⚠️ Batch production stopped: the last episode did not finish cleanly.\", group=\"needs\", key=\"batch-stopped\",") == 2
    from datetime import UTC, datetime

    from src import worker_deadline
    said = []
    db = SimpleNamespace(get_setting=lambda key, d=None: datetime(2026, 11, 16, 1, tzinfo=UTC).isoformat(),
                         run_label=lambda run_id: "Petra")
    worker_deadline.missed_slot_notice(db, {"run_id": "r"}, datetime(2026, 11, 20, 18, tzinfo=UTC),
                                       datetime(2026, 11, 16, 23, tzinfo=UTC), set(),
                                       lambda text, **k: said.append(text))
    assert said and said[0].startswith("⏰")
    source = main.Path(main.__file__).read_text(encoding="utf-8")   # its sender in main: a Needs-you _notice
    call = source[source.index("worker_deadline.missed_slot_notice(self.db"):][:400]
    assert '_notice(self, text, section="needs", key=f"missed:{run_id}"' in call
