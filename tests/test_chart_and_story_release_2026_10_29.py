"""The audience chart on /analytics, a real 0 views kept as 0, and the Story credit release check (2026-10-29).

The figures below are test rows, never real audience numbers; send_photo is a stub, so nothing here calls
Telegram or any API."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from src import audience, audience_chart, control, release
from src.control import make_command_handler
from src.telegram_ui import BUTTON_LIMIT, HOME, SCREENS

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def run(db, run_id, title, posted):
    db.create_run(run_id, "01_topic", content_format="reel")
    db.update_run(run_id, "approved", script_payload={"title": title})
    db.record_publication("01_topic", run_id, "upload-post", "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE run_id = ?", (posted.isoformat(), run_id))


def metric(db, run_id, platform, metrics, snapshot="7d"):
    audience.ensure_schema(db)
    with db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                     (run_id, platform, snapshot, "upload-post", json.dumps(metrics), NOW.isoformat()))


@pytest.fixture
def measured(db):
    run(db, "run_a", "Test Reel A", NOW - timedelta(days=9))
    metric(db, "run_a", "instagram", {"views": 1200})
    metric(db, "run_a", "youtube", {"views": 0})          # nobody watched it there: a 0, not a gap
    return db


# ------------------------------------------------------------------ 1. the chart button and screen
def test_the_analytics_overview_has_a_chart_button_registered_with_a_back_link(measured):
    _, rows = control.analytics_overview(measured, NOW)
    flat = [b for row in rows for b in row]
    chart = [b for b in flat if b.get("callback_data") == "cachart:"]
    assert chart and chart[0]["text"] == "📊 Chart"
    assert all(len(b["text"]) <= BUTTON_LIMIT for b in flat)
    assert sum(b == HOME for b in flat) == 1
    assert SCREENS["cachart"][2] == "canalytics:" and SCREENS["chart"] == SCREENS["cachart"]


def test_the_chart_is_rendered_and_sent_as_a_photo(measured, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    sent = []

    def send_photo(path, caption, buttons=None, cache_key=None):
        sent.append((Path(path), caption, buttons))
        return True
    h = make_command_handler(measured, lambda: None, lambda: "", send_photo=send_photo)
    assert h("cachart", "") == ""                         # sent: no text reply follows (as the contact sheet)
    path, caption, buttons = sent[0]
    assert path == tmp_path / "audience_chart.png" and Image.open(path).size[0] == audience_chart.W
    assert caption.endswith(".")
    flat = [b for row in buttons for b in row]
    assert flat[-1] == HOME and any(b.get("callback_data") == "canalytics:" for b in flat)
    # typed: /analytics chart and /chart open the same photo
    assert h("analytics", "chart") == "" and h("chart", "") == "" and len(sent) == 3


def test_without_a_photo_sender_the_chart_comes_as_text(measured):
    h = make_command_handler(measured, lambda: None, lambda: "")
    text, rows = h("cachart", "")
    body = re.sub(r"<[^>]+>", "", text)
    assert body.startswith("📊 Audience chart")
    assert "Test Reel A" in body and "Instagram 1.2k" in body and "YouTube 0" in body and "Facebook —" in body
    flat = [b for row in rows for b in row]
    assert flat[-1] == HOME and any(b.get("callback_data") == "canalytics:" for b in flat)


def test_a_failed_send_falls_back_to_text(measured, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    h = make_command_handler(measured, lambda: None, lambda: "", send_photo=lambda *a, **k: False)
    text, _ = h("cachart", "")
    assert "could not be sent" in text and "Test Reel A" in text


# ------------------------------------------------------------------ 2. a real 0 stays 0
@pytest.mark.parametrize("metrics, views", [
    ({"views": 0}, 0.0),                                   # stored 0: was None before 2026-10-29
    ({"views": 0, "plays": 50}, 0.0),                      # 0 is the answer; plays is not a fallback for it
    ({"fb_reels_total_plays": 0}, 0.0),
    ({"plays": 7}, 7.0),                                   # a missing value still falls through
    ({"views": None, "fb_reels_total_plays": 3}, 3.0),
    ({}, None),
    ({"views": True}, None),                               # a flag is not a count
])
def test_derived_views_keep_a_real_zero(metrics, views):
    assert audience.derived(metrics, None)["views"] == views


def test_derived_reach_keeps_a_real_zero():
    assert audience.derived({"reach": 0, "post_impressions_unique": 9}, None)["reach"] == 0.0
    assert audience.derived({"post_impressions_unique": 9}, None)["reach"] == 9.0


# ------------------------------------------------------------------ 3. Story credits at release
def _report(tmp_path, with_stories):
    master = tmp_path / "run" / "master.mp4"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"")
    if with_stories:
        (master.parent / "stories").mkdir()
        Image.new("RGB", (108, 192), (20, 20, 20)).save(master.parent / "stories" / "story_01_hook.jpg")
    return release.write_release_report({"master_video_path": str(master), "script_payload": {}}, dry_run=True)


def test_a_run_without_stories_has_nothing_to_check(tmp_path):
    report = _report(tmp_path, with_stories=False)
    assert "story_credits" not in [c["name"] for c in report["checks"]]
    assert release.story_credit_problems(tmp_path / "run") is None


def test_a_story_frame_without_its_credit_record_blocks_release_in_plain_words(tmp_path):
    report = _report(tmp_path, with_stories=True)
    check = next(c for c in report["checks"] if c["name"] == "story_credits")
    assert check["status"] == "fail"
    problems = [p for p in release.release_problems(report, dry_run=True) if p.startswith("story_credits")]
    assert problems == ["story_credits: Story frame 1: no record of its photo and credit "
                        "(made before Story credits were drawn: redraw the Stories)"]


def test_wrong_lines_and_low_contrast_read_as_plain_words(tmp_path, monkeypatch):
    from src import multi_format
    stories = tmp_path / "stories"
    stories.mkdir()
    for name in ("story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"):
        (stories / name).write_bytes(b"")
    found = {"credited": [], "problems": [
        "story_01_hook.jpg: the frame credits ['Photo: A'], its photo needs ['Photo: B · CC BY 4.0', 'creativecommons.org/licenses/by/4.0']",
        "story_02_reveal.jpg: the credit's contrast is 3.1:1, under 4.5:1",
        "story_03_question.jpg: the frame credits None, its photo needs ['Photo: C']"]}
    monkeypatch.setattr(multi_format, "story_credit_report", lambda frames: found)
    assert release.story_credit_problems(tmp_path) == [
        'Story frame 1: the frame credits "Photo: A", its photo needs "Photo: B · CC BY 4.0" / '
        '"creativecommons.org/licenses/by/4.0"',
        "Story frame 2: the credit's contrast is 3.1:1, under 4.5:1",
        'Story frame 3: the frame credits nothing, its photo needs "Photo: C"']
    monkeypatch.setattr(multi_format, "story_credit_report", lambda frames: {"credited": [{}], "problems": []})
    assert release.story_credit_problems(tmp_path) == []   # every frame credited: the check passes
