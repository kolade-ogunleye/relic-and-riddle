import json
import pytest

from src import control
from src.control import is_paused, make_command_handler, status_text
from src.telegram_bot import TelegramApprovalBot


@pytest.fixture
def handler(db):
    triggered = []
    h = make_command_handler(db, lambda: "Mon 13 Oct 09:00 EDT", lambda: triggered.append(1) or "starting")
    return h, triggered


def test_pause_and_resume(db, handler):
    h, _ = handler
    assert not is_paused(db)
    assert "paused" in h("pause", "")
    assert is_paused(db)
    assert "resumed" in h("resume", "")
    assert not is_paused(db)


def test_status_next_help_and_unknown(db, handler):
    h, _ = handler
    assert "active" in h("status", "") and "Mon 13 Oct" in h("status", "")
    assert "01_topic" in h("next", "")
    assert "/pause" in h("help", "")
    assert "Unknown command" in h("selfdestruct", "")


def test_run_command_triggers(db, handler):
    h, triggered = handler
    assert h("run", "") == "starting" and triggered == [1]


def test_status_escapes_html(db):
    db.create_run("r1", "01_topic")
    db.update_run("r1", "failed", error="<script>")
    assert "<script>" not in status_text(db, "<b>soon</b>")


def _bot(db, monkeypatch, updates):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    sent = []

    def api(method, **kw):
        if method == "getUpdates":
            return updates
        if method == "sendMessage":
            sent.append(kw["data"]["text"])
        return {"message_id": 1}

    monkeypatch.setattr(bot, "_api", api)
    bot._offset = 0
    return bot, sent


def test_poll_routes_commands_from_approver_only(db, monkeypatch):
    updates = [
        {"update_id": 1, "message": {"text": "/status@relicandriddle_bot", "from": {"id": 555}, "chat": {"id": 555}}},
        {"update_id": 2, "message": {"text": "/pause", "from": {"id": 999}, "chat": {"id": 999}}},
        {"update_id": 3, "message": {"text": "hello", "from": {"id": 555}, "chat": {"id": 555}}},
    ]
    bot, sent = _bot(db, monkeypatch, updates)
    calls = []
    bot.poll_once(lambda c, a: calls.append(c) or "ok")
    assert calls == ["status"] and sent == ["ok"] and bot._offset == 4
    assert not is_paused(db)


def test_poll_applies_approval_click_for_any_pending_run(db, monkeypatch):
    db.create_run("r7", "01_topic")
    db.record_approval("r7", "01_topic")
    click = {"id": "q", "data": "reject:r7", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 3}}
    bot, _ = _bot(db, monkeypatch, [{"update_id": 9, "callback_query": click}])
    bot.poll_once(None)
    assert db.get_approval("r7")["decision"] == "rejected"


def test_command_errors_are_reported_not_raised(db, monkeypatch):
    bot, sent = _bot(db, monkeypatch, [{"update_id": 1, "message": {"text": "/next", "from": {"id": 555}, "chat": {"id": 555}}}])
    bot.poll_once(lambda c, a: (_ for _ in ()).throw(RuntimeError("<boom>")))
    assert "failed" in sent[0] and "<boom>" not in sent[0]


def test_notify_never_raises(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert bot.notify("hi") is False


def test_healthcheck_ping_is_optional_and_safe(monkeypatch):
    calls = []
    monkeypatch.setattr(control, "HEALTHCHECK_PING_URL", "")
    monkeypatch.setattr(control.requests, "get", lambda url, timeout: calls.append(url))
    control.ping_healthcheck()
    assert calls == []
    monkeypatch.setattr(control, "HEALTHCHECK_PING_URL", "https://hc-ping.com/abc")
    control.ping_healthcheck("/fail")
    assert calls == ["https://hc-ping.com/abc/fail"]


def _image_set(tmp_path, name="03_kailasa_temple", n=6, reviewed=False):
    from PIL import Image
    from src.credits import save_credits
    d = tmp_path / name
    d.mkdir()
    files = []
    for i in range(n):
        Image.new("RGB", (60, 40)).save(d / f"{i:02d}_commons.jpg")
        files.append({"file": f"{i:02d}_commons.jpg", "title": f"File:Img {i}.jpg", "license": "CC BY 4.0"})
    save_credits(d, {"reviewed": reviewed, "images": files})
    return d


def test_images_review_drop_approve_flow(db, tmp_path):
    from src.credits import load_credits
    d = _image_set(tmp_path)
    _image_set(tmp_path, "29_baltic_sea_anomaly", n=2)
    sent = []
    h = make_command_handler(db, lambda: None, lambda: "", send_photo=lambda p, cap, btns: sent.append((p.name, cap, btns)) or True,
                             assets_dir=tmp_path)
    listing = h("images", "")
    assert "1 to review" in listing and "1 short" in listing and "03 — kailasa temple (6)" in listing

    assert h("review", "3") == ""
    name, caption, buttons = sent[-1]
    assert name == "contact_sheet.jpg" or name.endswith(".jpg")
    assert "1. Img 0.jpg" in caption and buttons[0]["callback_data"] == "imgok:03_kailasa_temple"

    assert "Removed 1; 5 images remain" in h("drop", "03 2")
    assert len(load_credits(d)["images"]) == 5
    assert "Approved 5 images" in h("imgok", "03_kailasa_temple")
    assert load_credits(d)["reviewed"] is True


def test_drop_below_minimum_then_approve_refused(db, tmp_path):
    _image_set(tmp_path, n=5)
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    h("drop", "3 1")
    with pytest.raises(ValueError, match="need 5"):
        h("imgok", "03_kailasa_temple")


def test_unknown_topic_and_bad_drop_usage(db, tmp_path):
    _image_set(tmp_path)
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    with pytest.raises(ValueError, match="No fetched image set"):
        h("review", "99")
    assert "Usage" in h("drop", "3 two")


def test_approve_button_routes_through_poller(db, monkeypatch, tmp_path):
    click = {"id": "q", "data": "imgok:03_kailasa_temple", "from": {"id": 555}, "message": {"chat": {"id": 555}}}
    bot, sent = _bot(db, monkeypatch, [{"update_id": 5, "callback_query": click}])
    calls = []
    bot.poll_once(lambda c, a: calls.append((c, a)) or "done")
    assert calls == [("imgok", "03_kailasa_temple")] and sent == ["done"]


def test_quota_lines_warn_when_low():
    from src.control import quota_lines
    assert quota_lines(None) == []
    ok = quota_lines({"tier": "starter", "used": 1000, "limit": 30000, "resets": 1794414283})
    assert ok and not ok[0].startswith("⚠️") and "29,000 of 30,000" in ok[0] and "~38 Reels" in ok[0]
    low = quota_lines({"tier": "free", "used": 9000, "limit": 10000, "resets": None})
    assert low[0].startswith("⚠️") and "edge-tts" in low[0]


def test_footage_command_handler(db, tmp_path, monkeypatch):
    _image_set(tmp_path)
    fake_info = {"title": "Drone 4K", "uploader": "Aerial Team"}
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=True): return fake_info

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    res = h("footage", "03_kailasa_temple https://www.youtube.com/watch?v=123")
    assert "Footage saved" in res
    assert "Drone 4K" in res
    curated_meta = tmp_path / "03_kailasa_temple" / "footage" / "curated_footage.json"
    assert curated_meta.exists()
    assert json.loads(curated_meta.read_text(encoding="utf-8"))["artist"] == "Aerial Team"


def test_status_includes_voice_quota(db):
    h = make_command_handler(db, lambda: None, lambda: "", voice_quota=lambda: {"tier": "free", "used": 0, "limit": 10000})
    assert "ElevenLabs (free): 10,000 of 10,000" in h("status", "")


def test_update_command_requires_daemon_and_delegates(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    assert "only be applied while the daemon" in h("upd", "apply")
    h2 = make_command_handler(db, lambda: None, lambda: "", apply_updates=lambda: "applying")
    assert h2("upd", "apply") == "applying"


def test_requeue_command(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    db.record_topic_status("01_topic", "rejected")
    assert db.get_topic_by_id("01_topic")["status"] == "rejected"
    res = h("requeue", "1")
    assert "restored to the queue" in res
    assert db.get_topic_by_id("01_topic")["status"] == "queued"


def test_alerts_command(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    assert "No alerts recorded" in h("alerts", "")
    db.record_alert("Warning: Veo provider timed out", message_id=123, kind="transient")
    res = h("alerts", "")
    assert "SYSTEM ALERTS" in res
    assert "Veo provider timed out" in res


def test_add_command_and_deduplication(db, tmp_path, monkeypatch):
    from pathlib import Path
    import src.topic_proposer as tp
    bank_file = tmp_path / "topic_bank.json"
    queries_file = tmp_path / "image_queries.json"
    bank_file.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic One", "sub_theme": "Ancient"}]}), encoding="utf-8")
    queries_file.write_text(json.dumps({"01_topic": {"queries": [], "keywords": []}}), encoding="utf-8")
    monkeypatch.setattr(tp, "BANK", bank_file)
    monkeypatch.setattr(tp, "QUERIES", queries_file)

    h = make_command_handler(db, lambda: None, lambda: "")
    # Duplicate attempt
    dup_res = h("add", "Topic One")
    assert "Duplicate detected" in dup_res

    # Unique topic
    add_res = h("add", "The Submerged Citadel of Pavlopetri")
    assert "Added" in add_res
    assert "pavlopetri" in add_res.lower()
    updated_bank = json.loads(bank_file.read_text(encoding="utf-8"))
    assert len(updated_bank["topics"]) == 2


def test_issue_command(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    res = h("issue", "run_01 scene 2 audio cut too sharply")
    assert "Issue Recorded" in res
    assert "iss_" in res
    open_issues = db.list_open_issues()
    assert len(open_issues) == 1
    assert open_issues[0]["run_id"] == "run_01"
    assert "audio cut too sharply" in open_issues[0]["critique"]


def test_queue_and_requeue_list(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    db.record_topic_status("01_topic", "rejected")
    q_res = h("queue", "")
    assert "TOPIC QUEUE" in q_res
    assert "Eligible to Requeue" in q_res
    assert "01_topic" in q_res

    rq_res = h("requeue", "list")
    assert "Eligible to Requeue" in rq_res
    assert "01_topic" in rq_res


def test_issue_with_evidence_attachment(db, tmp_path):
    h = make_command_handler(db, lambda: None, lambda: "")
    dummy_ev = tmp_path / "screenshot.jpg"
    dummy_ev.write_bytes(b"image-bytes")
    res = h("issue", f"run_01 subtitle artifact detected --evidence {dummy_ev}")
    assert "Issue Recorded" in res
    assert "Evidence attached" in res
    open_issues = db.list_open_issues()
    assert any("subtitle artifact" in i["critique"] for i in open_issues)




def test_dual_resolution_serial_and_slug(tmp_path, monkeypatch):
    import json
    from src.db import PipelineDatabase
    from src.control import make_command_handler
    from PIL import Image
    from src.credits import save_credits, load_credits

    bank_file = tmp_path / 'topic_bank.json'
    bank_file.write_text(json.dumps({'topics': [
        {'id': 'gobekli_tepe_enclosure_d', 'title': 'Gobekli Tepe Enclosure D', 'hook': 'h1', 'location': {'name': 'Sanliurfa', 'country': 'Turkey'}},
        {'id': 'antikythera_mechanism', 'title': 'Antikythera Mechanism', 'hook': 'h2', 'location': {'name': 'Antikythera', 'country': 'Greece'}}
    ]}), encoding='utf-8')
    db = PipelineDatabase(db_path=tmp_path / 'test.db', topic_bank_file=bank_file)

    assets_dir = tmp_path / 'assets'
    assets_dir.mkdir()
    d1 = assets_dir / 'gobekli_tepe_enclosure_d'
    d1.mkdir()
    files = []
    for i in range(6):
        Image.new('RGB', (60, 40)).save(d1 / f'{i:02d}_commons.jpg')
        files.append({'file': f'{i:02d}_commons.jpg', 'title': f'File:Img {i}.jpg', 'license': 'CC BY 4.0'})
    save_credits(d1, {'reviewed': False, 'images': files})

    sent = []
    h = make_command_handler(db, lambda: None, lambda: '',
                             send_photo=lambda p, cap, btns: sent.append((p.name, cap, btns)) or True,
                             assets_dir=assets_dir)

    # 1. /images labels topics with serial rank and slug
    img_list = h('images', '')
    assert '1. Gobekli Tepe Enclosure D (<code>gobekli_tepe_enclosure_d</code>) (6)' in img_list

    # 2. /review by serial number '1' and by slug 'gobekli_tepe_enclosure_d'
    assert h('review', '1') == ''
    assert sent[-1][2][0]['callback_data'] == 'imgok:gobekli_tepe_enclosure_d'

    assert h('review', 'gobekli_tepe_enclosure_d') == ''
    assert sent[-1][2][0]['callback_data'] == 'imgok:gobekli_tepe_enclosure_d'

    # 3. /drop by serial number '1' and by slug
    assert 'Removed 1; 5 images remain' in h('drop', '1 2')
    assert len(load_credits(d1)['images']) == 5
    assert 'Removed 1; 4 images remain' in h('drop', 'gobekli_tepe_enclosure_d 1')
    assert len(load_credits(d1)['images']) == 4

    # 4. /queue displays serial 1. and 2.
    q_out = h('queue', '')
    assert '1. Gobekli Tepe Enclosure D' in q_out
    assert '2. Antikythera Mechanism' in q_out

    # 5. /requeue by serial number and by slug
    db.record_topic_status('gobekli_tepe_enclosure_d', 'rejected')
    assert db.get_topic_by_id('gobekli_tepe_enclosure_d')['status'] == 'rejected'
    rq_res = h('requeue', '1')
    assert 'restored to the queue' in rq_res
    assert db.get_topic_by_id('gobekli_tepe_enclosure_d')['status'] == 'queued'

    db.record_topic_status('antikythera_mechanism', 'rejected')
    rq_res2 = h('requeue', 'antikythera_mechanism')
    assert 'restored to the queue' in rq_res2
    assert db.get_topic_by_id('antikythera_mechanism')['status'] == 'queued'


def test_review_cancel_and_swap_visual_reorder(tmp_path):
    import json
    from src.db import PipelineDatabase
    from src.control import make_command_handler
    from PIL import Image
    from src.credits import save_credits, load_credits

    bank_file = tmp_path / 'topic_bank.json'
    bank_file.write_text(json.dumps({'topics': [
        {'id': 'derinkuyu', 'title': 'Derinkuyu Underground City', 'hook': 'h1', 'location': {'name': 'Nevsehir', 'country': 'Turkey'}}
    ]}), encoding='utf-8')
    db = PipelineDatabase(db_path=tmp_path / 'test.db', topic_bank_file=bank_file)

    assets_dir = tmp_path / 'assets'
    d1 = assets_dir / 'derinkuyu'
    d1.mkdir(parents=True)
    files = []
    for i in range(5):
        Image.new('RGB', (60, 40), color=(i * 40, i * 40, i * 40)).save(d1 / f'{i:02d}_commons.jpg')
        files.append({'file': f'{i:02d}_commons.jpg', 'title': f'File:Img {i}.jpg', 'license': 'CC BY 4.0'})
    save_credits(d1, {'reviewed': False, 'images': files})

    # Create dummy storyboard
    sb_file = d1 / 'storyboard.json'
    sb_file.write_text(json.dumps([
        {'beat': 1, 'file': '00_commons.jpg', 'type': 'HOOK', 'text': 'hook text'},
        {'beat': 2, 'file': '01_commons.jpg', 'type': 'INTERIOR', 'text': 'beat 2 text'},
        {'beat': 3, 'file': '02_commons.jpg', 'type': 'ARTIFACT', 'text': 'beat 3 text'},
        {'beat': 4, 'file': '03_commons.jpg', 'type': 'CLIMAX', 'text': 'beat 4 text'},
    ]), encoding='utf-8')

    sent = []
    h = make_command_handler(db, lambda: None, lambda: '',
                             send_photo=lambda p, cap, btns: sent.append((p.name, cap, btns)) or True,
                             assets_dir=assets_dir)

    # 1. /review provides both Approve and Cancel buttons
    res = h('review', '1')
    assert res == ''
    assert len(sent[-1][2]) == 2
    assert sent[-1][2][0]['text'] == '🎬 Approve Storyboard'
    assert sent[-1][2][0]['callback_data'] == 'imgok:derinkuyu'
    assert sent[-1][2][1]['text'] == '✖ Cancel'
    assert sent[-1][2][1]['callback_data'] == 'imgcancel:derinkuyu'

    # 2. imgcancel callback leaves topic unreviewed
    cancel_res = h('imgcancel', 'derinkuyu')
    assert 'Storyboard review cancelled' in cancel_res
    assert load_credits(d1)['reviewed'] is False

    # 3. /swap 1 2 4 swaps beats 2 and 4 in both storyboard and credits
    swap_res = h('swap', '1 2 4')
    assert "Swapped Beat 2" in swap_res and "Beat 4" in swap_res
    updated_sb = json.loads(sb_file.read_text(encoding='utf-8'))
    assert updated_sb[1]['file'] == '03_commons.jpg'
    assert updated_sb[3]['file'] == '01_commons.jpg'

    updated_cred = load_credits(d1)
    # The credits images should also have updated ordering
    assert updated_cred['images'][1]['file'] == '03_commons.jpg'
    assert updated_cred['images'][3]['file'] == '01_commons.jpg'
    # And a new contact sheet was sent
    assert len(sent) >= 2


def test_stale_approval_click_rejected_and_buttons_stripped(db, monkeypatch):
    db.create_run("r8", "01_topic")
    db.record_approval("r8", "01_topic")
    click1 = {"id": "q1", "data": "reject:r8", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 10}}
    bot, sent = _bot(db, monkeypatch, [{"update_id": 1, "callback_query": click1}])
    bot.poll_once(None)
    assert db.get_approval("r8")["decision"] == "rejected"

    # Second click on the same run (stale click)
    click2 = {"id": "q2", "data": "approve:r8", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 10}}
    bot2, sent2 = _bot(db, monkeypatch, [{"update_id": 2, "callback_query": click2}])
    bot2.poll_once(None)
    # Decision must remain rejected; should not flip to approved
    assert db.get_approval("r8")["decision"] == "rejected"
    assert any("Card Expired" in s for s in sent2)


def test_cleanup_stale_approvals_sweeps_timed_out(db, monkeypatch):
    from datetime import datetime, timezone, timedelta
    old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    db.create_run("r9", "01_topic")
    db.record_approval("r9", "01_topic", message_id=88)
    # Manually backdate created_at
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at = ? WHERE run_id = ?", (old_time, "r9"))

    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    edited = []
    monkeypatch.setattr(bot, "_api", lambda m, **kw: edited.append((m, kw)) or {"message_id": 1})
    cleaned = bot.cleanup_stale_approvals(max_age_hours=2)
    assert cleaned == 1
    assert db.get_approval("r9")["decision"] == "timeout"
    assert any(e[0] == "editMessageReplyMarkup" for e in edited)


def test_music_mnext_and_duplicate_imgok(tmp_path):
    import json
    from src.db import PipelineDatabase
    from src.control import make_command_handler
    from PIL import Image
    from src.credits import save_credits

    bank_file = tmp_path / "topic_bank.json"
    bank_file.write_text(json.dumps({"topics": [{"id": "t1", "title": "Topic 1"}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp_path / "test.db", topic_bank_file=bank_file)

    assets_dir = tmp_path / "assets"
    d1 = assets_dir / "t1"
    d1.mkdir(parents=True)
    files = []
    for i in range(5):
        Image.new("RGB", (50, 50)).save(d1 / f"{i:02d}.jpg")
        files.append({"file": f"{i:02d}.jpg", "title": f"Img {i}", "license": "PD"})
    save_credits(d1, {"reviewed": False, "images": files})

    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=assets_dir)

    # First approve
    appr1 = h("imgok", "t1")
    assert "✅" in appr1

    # Second approve click should report already approved
    appr2 = h("imgok", "t1")
    assert "was already approved" in appr2


def test_deletion_on_mcancel_mnext_and_umoredone(db, monkeypatch):
    deleted_ids = []
    def fake_delete(mid):
        deleted_ids.append(mid)
        return True

    # 1. Test poll_once with mcancel triggers deleteMessage on the audio card
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "delete_message", fake_delete)

    click_cancel = {"id": "q1", "data": "mcancel:track1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 77}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 1, "callback_query": click_cancel}] if m == "getUpdates" else {"message_id": 1})
    bot.poll_once(lambda a, t: "cancelled")
    assert 77 in deleted_ids

    # 2. Test umoredone clears all recorded discovery card IDs
    cleared_via_handler = []
    h = make_command_handler(db, lambda: None, lambda: "", delete_message=lambda mid: cleared_via_handler.append(mid) or True,
                             send_message=lambda t, b: 101)
    h("discover", "subterranean")
    assert len(cleared_via_handler) == 0

    # Calling umoredone deletes tracked card IDs
    res = h("umoredone", "")
    assert "Cleared" in res
    assert 101 in cleared_via_handler


def test_extended_deletion_and_inplace_transform(db, monkeypatch):
    deleted_ids = []
    edited_text = {}
    edited_any = {}

    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "delete_message", lambda mid: deleted_ids.append(mid) or True)
    monkeypatch.setattr(bot, "edit_message_text", lambda mid, txt: edited_text.update({mid: txt}) or True)
    monkeypatch.setattr(bot, "edit_message_any", lambda mid, txt, is_media=False: edited_any.update({mid: txt}) or True)

    # 1. tskip and uskip delete cards
    click_tskip = {"id": "q1", "data": "tskip:topic1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 81}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 1, "callback_query": click_tskip}] if m == "getUpdates" else {})
    bot.poll_once(lambda a, t: "skipped")
    assert 81 in deleted_ids

    # 2. mdel and imgcancel delete cards
    click_mdel = {"id": "q2", "data": "mdel:track1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 82}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 2, "callback_query": click_mdel}] if m == "getUpdates" else {})
    bot.poll_once(lambda a, t: "removed")
    assert 82 in deleted_ids

    click_imgcancel = {"id": "q3", "data": "imgcancel:t1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 83}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 3, "callback_query": click_imgcancel}] if m == "getUpdates" else {})
    bot.poll_once(lambda a, t: "cancelled")
    assert 83 in deleted_ids

    # 3. uadd transforms in-place via edit_message_text
    click_uadd = {"id": "q4", "data": "uadd:wonder1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 84}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 4, "callback_query": click_uadd}] if m == "getUpdates" else {})
    bot.poll_once(lambda a, t: "Wonder Queued Successfully")
    assert 84 in edited_text
    assert "Wonder Queued" in edited_text[84]

    # 4. imgok and mkeep transform in-place via edit_message_any
    click_imgok = {"id": "q5", "data": "imgok:t1", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 85, "photo": [{"file_id": "p1"}]}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 5, "callback_query": click_imgok}] if m == "getUpdates" else {})
    bot.poll_once(lambda a, t: "Storyboard Approved & Locked")
    assert 85 in edited_any
    assert "Storyboard Approved & Locked" in edited_any[85]


def test_cancel_pipeline_review(db, monkeypatch):
    db.create_run("r99", "01_topic")
    db.record_approval("r99", "01_topic")

    edited_cards = {}
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "edit_message_text", lambda mid, txt: edited_cards.update({mid: txt}) or True)

    # Click cancel on pipeline review
    click_cancel = {"id": "qc", "data": "cancel:r99", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 99, "text": "Run r99 Review"}}
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 1, "callback_query": click_cancel}] if m == "getUpdates" else {})
    bot.poll_once(None)

    assert db.get_approval("r99")["decision"] == "cancelled"
    assert 99 in edited_cards
    assert "CANCELLED" in edited_cards[99]


def test_bump_and_drop_topic_db(db):
    t1 = db.get_topic_by_id("01_topic")
    t2 = db.get_topic_by_id("02_topic")
    assert t1 and t2

    # Bump t2 to priority #1
    assert db.bump_topic("02_topic")
    next_t = db.get_next_topic()
    assert next_t["id"] == "02_topic"
    assert next_t["priority"] > 0

    # Drop t2
    assert db.drop_topic("02_topic")
    dropped = db.get_topic_by_id("02_topic")
    assert dropped["status"] == "archived"

    # Requeue restores it
    db.requeue_topic("02_topic")
    restored = db.get_topic_by_id("02_topic")
    assert restored["status"] == "queued"
    assert restored["priority"] == 0


def test_interactive_queue_controls_and_undo(db, monkeypatch):
    sent_messages = []
    def fake_send(txt, btns=None):
        sent_messages.append((txt, btns))
        return 105

    h = make_command_handler(db, lambda: None, lambda: "", send_message=fake_send)

    # 1. /queue renders queue with bump and drop buttons
    res = h("queue", "")
    assert res == ""
    assert len(sent_messages) > 0
    q_txt, q_btns = sent_messages[-1]
    assert "TOPIC QUEUE" in q_txt
    assert any(b["callback_data"].startswith("qdrop:") for b in q_btns)
    assert any(b["callback_data"] == "qrefresh:" for b in q_btns)

    # 2. /bump command bumps topic
    bump_res = h("bump", "2")
    assert "Promoted" in bump_res
    assert db.get_next_topic()["id"] == "02_topic"

    # 3. /drop_topic archives topic
    drop_res = h("drop_topic", "2")
    assert "Archived" in drop_res
    assert db.get_topic_by_id("02_topic")["status"] == "archived"

    # 4. /undo restores dropped topic
    undo_res = h("undo", "")
    assert "Undone" in undo_res
    assert db.get_topic_by_id("02_topic")["status"] == "queued"


def test_cover_variant_selection(db, tmp_path, monkeypatch):
    from pathlib import Path
    run_dir = Path("output") / "run_test_covers_123"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        img_a = Image.new("RGB", (100, 100), color="blue")
        img_b = Image.new("RGB", (100, 100), color="red")
        img_c = Image.new("RGB", (100, 100), color="green")
        img_a.save(run_dir / "thumbnail_variant_a_blueprint.jpg")
        img_b.save(run_dir / "thumbnail_variant_b_intrigue.jpg")
        img_c.save(run_dir / "thumbnail_variant_c_minimal.jpg")

        h = make_command_handler(db, lambda: None, lambda: "")
        cov_res = h("cov", "run_test_covers_123:a")
        assert "Forensic Blueprint" in cov_res
        assert (run_dir / "thumbnail_cover.jpg").exists()
        assert (run_dir / "thumbnail_profile_grid.jpg").exists()
    finally:
        import shutil
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_progress_card_lifecycle(db, monkeypatch):
    calls = []
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda m, **kw: calls.append((m, kw.get("data", {}))) or {"message_id": 999})

    # 1. Send progress card
    mid = bot.send_progress_card("r1", "Derinkuyu Underground City")
    assert mid == 999
    assert calls[-1][0] == "sendMessage"
    assert "PRODUCTION PROGRESS" in calls[-1][1]["text"]

    # 2. Update progress card in-place
    assert bot.update_progress_card(mid, 3, 5, "Derinkuyu Underground City", "r1", "Compiling graphics")
    assert calls[-1][0] == "editMessageText"
    assert "Step 3/5" in calls[-1][1]["text"]
    assert "Compiling graphics" in calls[-1][1]["text"]

    # 3. Finish progress card (success deletes message for zero clutter)
    assert bot.finish_progress_card(mid, "Derinkuyu Underground City", "r1", success=True)
    assert calls[-1][0] == "deleteMessage"


def test_debate_poll_and_ai_transparency_in_card(db):
    payload = {
        "title": "Derinkuyu",
        "narration_script": "Deep underground...",
        "poll": {
            "question": "Was it built for siege defense or volcanic shelter?",
            "options": ["Siege Defense", "Volcanic Shelter"]
        },
        "is_generated_by_ai": True
    }
    card_text = TelegramApprovalBot.format_card_text(payload, "r1")
    assert "Debate Poll" in card_text
    assert "Siege Defense" in card_text
    assert "AI Transparency" in card_text


def test_format_responsive_keyboard():
    from src.telegram_bot import format_responsive_keyboard
    # 1. Flat list of short buttons -> grouped into pairs of 2
    buttons = [
        {"text": "Btn 1", "callback_data": "1"},
        {"text": "Btn 2", "callback_data": "2"},
        {"text": "Btn 3", "callback_data": "3"},
    ]
    rows = format_responsive_keyboard(buttons, max_per_row=2, max_btn_len=16)
    assert len(rows) == 2
    assert len(rows[0]) == 2
    assert len(rows[1]) == 1

    # 2. Long buttons (>16 chars) get full width
    long_btn = {"text": "🔒 Lock Variant B (Discovery Intrigue)", "callback_data": "lock"}
    mixed = [
        {"text": "A: Blueprint", "callback_data": "a"},
        {"text": "B: Intrigue", "callback_data": "b"},
        long_btn,
    ]
    mixed_rows = format_responsive_keyboard(mixed, max_per_row=2, max_btn_len=16)
    assert len(mixed_rows) == 2
    assert len(mixed_rows[0]) == 2
    assert len(mixed_rows[1]) == 1
    assert mixed_rows[1][0] == long_btn

    # 3. 2D list with an overcrowded 3-button row -> reflowed
    crowded = [
        [
            {"text": "A", "callback_data": "a"},
            {"text": "B", "callback_data": "b"},
            {"text": "C", "callback_data": "c"},
        ]
    ]
    reflowed = format_responsive_keyboard(crowded, max_per_row=2, max_btn_len=16)
    assert len(reflowed) == 2
    assert len(reflowed[0]) == 2
    assert len(reflowed[1]) == 1


def test_active_progress_and_progress_command(db, handler):
    h, _ = handler
    # When idle:
    idle_res = h("progress", "")
    assert "No Production Running" in idle_res

    # When active:
    db.set_active_progress(
        run_id="run_test_01",
        topic_title="Roman Concrete",
        step=2,
        total_steps=5,
        details="Acoustic Voiceover",
        message_id=42
    )
    active_res = h("progress", "")
    assert "PRODUCTION PROGRESS" in active_res
    assert "Roman Concrete" in active_res
    assert "Step 2/5" in active_res
    assert "Acoustic Voiceover" in active_res

    # Finish clears progress:
    db.clear_active_progress()
    assert db.get_active_progress() is None
    assert "No Production Running" in h("progress", "")


def test_bulk_delete_messages_and_clear_command(db, monkeypatch):
    calls = []
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})

    def api_mock(method, **kwargs):
        calls.append((method, kwargs.get("data", {})))
        if method == "sendMessage":
            return {"message_id": 999}
        return True

    monkeypatch.setattr(bot, "_api", api_mock)

    # 1. Test delete_messages chunks into batches of 100
    ids_to_del = list(range(1, 251))  # 250 message IDs -> 3 chunks (100, 100, 50)
    cnt = bot.delete_messages(ids_to_del)
    assert cnt == 250
    bulk_calls = [c for c in calls if c[0] == "deleteMessages"]
    assert len(bulk_calls) == 3

    # 2. Test /clear via poll_once sweeps 500 messages, sends typing action and Cockpit
    calls.clear()
    update = {
        "update_id": 1,
        "message": {
            "message_id": 600,
            "text": "/clear",
            "from": {"id": 555},
            "chat": {"id": 555}
        }
    }
    bot._offset = 1
    monkeypatch.setattr(bot, "_latest_update_id", lambda: 0)

    # Mock getUpdates to return our /clear update
    def get_updates(**kw):
        return [update] if bot._offset == 1 else []
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [update] if m == "getUpdates" and bot._offset <= 1 else api_mock(m, **kw))

    bot.poll_once(lambda c, a: "ok")
    # Check that typing action was sent immediately
    assert any(c[0] == "sendChatAction" for c in calls)
    # Check that bulk delete was called
    assert any(c[0] == "deleteMessages" for c in calls)
    # Check that dashboard was sent with clear text
    send_calls = [c for c in calls if c[0] == "sendMessage"]
    assert any("CHAT CLEARED" in c[1].get("text", "") for c in send_calls)


def test_audit_log_formatting_and_ellipses(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    # Record a long event payload (>45 chars)
    long_payload = "A" * 70
    db.record_audit_event(user_id="123", username="test_user", action="cmd", payload=long_payload, status="allowed")
    # Record a short event payload (<=45 chars)
    short_payload = "short_action_here"
    db.record_audit_event(user_id="123", username="test_user", action="cmd", payload=short_payload, status="blocked")

    res = h("audit", "5")
    assert "AUDIT LOG" in res
    assert "test_user" in res
    # The long payload must use <blockquote expandable> so nothing is truncated or lost
    assert "<blockquote expandable>" in res
    assert "A" * 70 in res
    # The short payload must be rendered inline
    assert "<code>short_action_here</code>" in res


def test_dashboard_command_no_redundant_sent_above_message(db, monkeypatch):
    sent = []
    def fake_send(txt, btns=None):
        sent.append((txt, btns))
        return 501

    # 1. Handler with send_message should send the card directly and return empty string (no second message)
    h = make_command_handler(db, lambda: None, lambda: "", send_message=fake_send)
    reply = h("dashboard", "")
    assert reply == ""
    assert len(sent) == 1
    dash_txt, dash_btns = sent[-1]
    assert "EXECUTIVE COCKPIT" in dash_txt or "Relic & Riddle" in dash_txt
    assert dash_btns is not None

    # 2. Handler without send_message returns dash_text directly
    h_plain = make_command_handler(db, lambda: None, lambda: "")
    reply_plain = h_plain("dashboard", "")
    assert "EXECUTIVE COCKPIT" in reply_plain or "Relic & Riddle" in reply_plain

    # 3. bot.poll_once receiving /dashboard should only send 1 message (the card), never 'Dashboard sent above.'
    calls = []
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    def api_mock(method, **kwargs):
        calls.append((method, kwargs.get("data", {})))
        if method == "sendMessage":
            return {"message_id": 777}
        return True
    monkeypatch.setattr(bot, "_api", api_mock)

    h_bot = make_command_handler(db, lambda: None, lambda: "", send_message=bot.notify)

    update = {
        "update_id": 99,
        "message": {
            "message_id": 100,
            "text": "/dashboard",
            "from": {"id": 555},
            "chat": {"id": 555}
        }
    }
    bot._offset = 99
    monkeypatch.setattr(bot, "_latest_update_id", lambda: 0)
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [update] if m == "getUpdates" and bot._offset <= 99 else api_mock(m, **kw))

    bot.poll_once(h_bot)
    send_calls = [c for c in calls if c[0] == "sendMessage"]
    # Only 1 sendMessage call should have occurred (the dashboard card itself)
    assert len(send_calls) == 1
    # Verify 'Dashboard sent above.' was never sent
    assert not any("Dashboard sent above" in c[1].get("text", "") for c in send_calls)


def test_autocomplete_commands_empty_args_interactive_templates(db):
    sent = []
    def fake_send(txt, btns=None):
        sent.append((txt, btns))
        return 777

    h = make_command_handler(db, lambda: None, lambda: "", send_message=fake_send)

    # 1. /bump with empty args offers interactive bump buttons
    res_bump = h("bump", "")
    assert res_bump == "" or "BUMP TOPIC" in res_bump
    if res_bump == "":
        assert len(sent) > 0
        b_txt, b_btns = sent[-1]
        assert "BUMP TOPIC" in b_txt
        assert len(b_btns) > 0

    # 2. /drop with empty args provides tap-to-copy template
    res_drop = h("drop", "")
    assert "DROP IMAGE" in res_drop
    assert "<code>/drop" in res_drop

    # 3. /swap with empty args provides tap-to-copy template
    res_swap = h("swap", "")
    assert "SWAP BEATS" in res_swap
    assert "<code>/swap" in res_swap

    # 4. /add with empty args provides tap-to-copy template
    res_add = h("add", "")
    assert "ADD TOPIC" in res_add
    assert "<code>/add" in res_add

    # 5. /issue with empty args provides tap-to-copy template
    res_issue = h("issue", "")
    assert "REPORT ISSUE" in res_issue
    assert "<code>/issue" in res_issue

    # 6. /review with empty args does NOT run an arbitrary topic; offers selection or template
    res_review = h("review", "")
    assert res_review == "" or "STORYBOARD REVIEW" in res_review
    if res_review == "":
        assert len(sent) > 0
        r_txt, r_btns = sent[-1]
        assert "STORYBOARD REVIEW" in r_txt


def test_safe_html_truncate_and_tag_balancing():
    from src.telegram_bot import safe_html_truncate
    text = "<b><u>TITLE</u></b>\n<blockquote expandable>Long content with <i>nested italics</i> and &amp; entities here.</blockquote>"
    t_short = safe_html_truncate(text, max_chars=70)
    assert t_short.endswith("</blockquote>")
    assert "…" in t_short

    t_nested = safe_html_truncate(text, max_chars=90)
    assert t_nested.endswith("</i></blockquote>")


def test_format_card_text_uppercase_titles_no_dividers_safe_notes():
    from src.telegram_bot import TelegramApprovalBot
    payload = {
        "title": "Test Roman Concrete",
        "location": {"name": "Rome", "country": "Italy"},
        "word_count": 150,
        "origin": "claude",
        "narration_script": "Ancient Romans built structures that lasted 2,000 years.",
        "voice_note": "🎙️ <b>Voice:</b> Edge-Tts (🟢 Commercially Licensed)",
        "music_note": "🎵 Music: ElevenLabs score (mysterious)",
        "qa_summary": "🔎 Automated video check: no issues found",
    }
    card = TelegramApprovalBot.format_card_text(payload, "run_test_01")
    assert "<b><u>MASTER REVIEW</u></b>" in card
    assert "──────────────" not in card
    assert "&lt;b&gt;" not in card
    assert "<b>🎙️ Voice:</b> Edge-Tts" in card
    assert "<b>🎵 Music:</b> ElevenLabs score" in card


def test_notify_and_edit_message_fallback_on_parse_error(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    calls = []

    def fake_api(method, data=None, **kw):
        calls.append((method, data))
        if data and data.get("parse_mode") == "HTML":
            raise RuntimeError("Telegram sendMessage failed: HTTP 400 Bad Request: can't parse entities")
        return {"message_id": 1234}

    monkeypatch.setattr(bot, "_api", fake_api)

    res = bot.notify("<b>Unclosed tag test")
    assert res == 1234
    assert len(calls) == 2
    assert calls[0][1]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1][1]
    assert calls[1][1]["text"] == "Unclosed tag test"


def test_ask_and_probe_system_intelligence(db):
    h = make_command_handler(db, lambda: None, lambda: "")

    # 1. Empty args returns interactive template
    res_empty = h("ask", "")
    assert "PROBE SYSTEM (AGY)" in res_empty
    assert "<code>/ask" in res_empty

    # 2. /ask with a diagnostic query
    res_kling = h("ask", "Why did the system reject Kling motion?")
    assert "SYSTEM INTELLIGENCE (AGY)" in res_kling
    assert "kling" in res_kling.lower() or "motion" in res_kling.lower()

    # 3. /probe alias
    res_probe = h("probe", "What is the status of the queue?")
    assert "SYSTEM INTELLIGENCE (AGY)" in res_probe
    assert "queue" in res_probe.lower() or "production" in res_probe.lower()


def test_native_telegram_reply_to_message_routing(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    routed = []

    def mock_handler(cmd, args):
        routed.append((cmd, args))
        return "Intelligence response"

    calls = []
    def fake_api(method, data=None, **kw):
        calls.append((method, data))
        if method == "getUpdates":
            return [{
                "update_id": 100,
                "message": {
                    "message_id": 888,
                    "from": {"id": 555, "username": "admin"},
                    "chat": {"id": 555},
                    "text": "Can we get to the bottom of why Veo ran when Kling had credit?",
                    "reply_to_message": {
                        "message_id": 777,
                        "text": "🔍 SYSTEM INTELLIGENCE (AGY)\nQ: What happened?"
                    }
                }
            }]
        elif method == "sendMessage":
            return {"message_id": 999}
        return {}

    monkeypatch.setattr(bot, "_api", fake_api)
    bot.poll_once(mock_handler)

    assert len(routed) == 1
    cmd, args = routed[0]
    assert cmd == "ask"
    assert "Can we get to the bottom of why Veo ran" in args
    assert "Context from replied message" in args
    assert any(c[0] == "sendChatAction" for c in calls)


def test_motion_fallback_tracking(db, monkeypatch, tmp_path):
    from PIL import Image
    import src.motion as motion
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (100, 100), color="blue").save(photo)

    def fail_kling(*a, **k):
        raise motion.MotionError("Kling credit expired")

    def succ_veo(*a, **k):
        out = tmp_path / "veo.mp4"
        out.write_bytes(b"dummy_video")
        return out

    monkeypatch.setattr(motion, "kling_available", lambda: True)
    monkeypatch.setattr(motion, "veo_available", lambda: True)
    monkeypatch.setattr(motion, "animate_kling", fail_kling)
    monkeypatch.setattr(motion, "animate_veo", succ_veo)
    monkeypatch.setattr(motion.media_budget, "allow", lambda *a: True)
    monkeypatch.setattr(motion.media_budget, "record", lambda *a: None)

    clip, name = motion.animate(db, photo, tmp_path / "out.mp4")
    assert clip is not None
    assert name == "veo-3.1-fast-generate-preview"
    assert motion.last_fallback_reason is not None
    assert "kling-3.0 failed (Kling credit expired)" in motion.last_fallback_reason
