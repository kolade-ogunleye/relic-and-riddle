"""The analytics dashboard (operator, 2026-10-28): "a consolidated analytics for all the platforms and separate
for each platform ... look more like a dashboard ... useful information for an operator at a glance and easy to
dive deeper". The fixtures are production's own rows of 2026-10-28 (Kailasa, Göbekli Tepe); nothing here calls
an API: the screens read audience_metrics and audience_sources only."""
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

from src import audience, control
from src.control import make_command_handler
from src.telegram_ui import HOME, SCREENS, long_labels, with_nav

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
KAILASA_POSTED = NOW - timedelta(days=3, hours=2)
GOBEKLI_POSTED = NOW - timedelta(days=1, hours=3)
GRAPH_400 = ("Instagram Graph: HTTP 400: Unsupported get request. Object with ID '17900000000000000' does not exist, "
             "cannot be loaded due to missing permissions, or does not support this operation (code 100); "
             "Upload-Post: no instagram metrics for run_gobekli")


def run(db, run_id, title, posted, fmt="reel", duration=None, tmp_path=None):
    db.create_run(run_id, "01_topic", content_format=fmt)
    fields = {"script_payload": {"title": title}}
    if duration and tmp_path:
        folder = tmp_path / run_id
        folder.mkdir()
        (folder / "release_report.json").write_text(json.dumps(
            {"checks": [{"name": "duration", "detail": f"Measured {duration}s"}]}), encoding="utf-8")
        fields["master_video_path"] = str(folder / "master.mp4")
    db.update_run(run_id, "approved", **fields)
    for channel in ("upload-post", "youtube-shorts"):
        db.record_publication("01_topic", run_id, channel, "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE run_id = ?", (posted.isoformat(), run_id))


def metric(db, run_id, platform, snapshot, source, metrics, fetched):
    audience.ensure_schema(db)
    with db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                     (run_id, platform, snapshot, source, json.dumps({"source": source, **metrics}), fetched.isoformat()))


def sources(db, collected, outcomes):
    audience.ensure_schema(db)
    with db._conn() as conn:
        conn.executemany("INSERT INTO audience_sources VALUES (?,?,?,?,?,?,?)",
                         [(collected.isoformat(), rid, snap, label, source, outcome, detail)
                          for rid, snap, label, source, outcome, detail in outcomes])


@pytest.fixture
def production(db, tmp_path):
    """Production's audience on 2026-10-28."""
    run(db, "run_kailasa", "Kailasa: the temple carved from one rock", KAILASA_POSTED, duration=15.2, tmp_path=tmp_path)
    run(db, "run_gobekli", "Göbekli Tepe: older than farming", GOBEKLI_POSTED)
    k1, k3 = KAILASA_POSTED + timedelta(days=1, hours=1), KAILASA_POSTED + timedelta(days=3, hours=1)
    metric(db, "run_kailasa", "instagram", "1d", "instagram-graph",
           {"views": 121, "reach": 107, "ig_reels_avg_watch_time": 7900, "reels_skip_rate": 38.4, "shares": 3,
            "saved": 2, "likes": 11}, k1)
    metric(db, "run_kailasa", "facebook", "3d", "facebook-graph", {"fb_reels_total_plays": 339}, k3)
    metric(db, "run_kailasa", "youtube", "3d", "upload-post", {"views": 899, "likes": 17}, k3)
    metric(db, "run_kailasa", "instagram_story1", "story", "expired",
           {"unavailable": "story expired before it was measured (26 h after posting; Meta keeps story insights 24 h)"}, k1)
    metric(db, "run_kailasa", "facebook_story1", "story", "upload-post",
           {"unavailable": "Upload-Post: HTTP 400: story metrics are not available for this post"}, k1)
    g1 = GOBEKLI_POSTED + timedelta(days=1, hours=1)
    metric(db, "run_gobekli", "youtube", "1d", "upload-post", {"views": 1244}, g1)
    metric(db, "run_gobekli", "instagram", "1d", "upload-post", {"unavailable": GRAPH_400}, g1)
    metric(db, "run_gobekli", "facebook", "1d", "upload-post", {"unavailable": "Facebook video not found"}, g1)
    sources(db, g1, [("run_gobekli", "1d", "instagram", "instagram-graph", "failed",
                      "HTTP 400: Unsupported get request (code 100)"),
                     ("run_gobekli", "1d", "instagram", "upload-post", "failed", "no metrics"),
                     ("run_gobekli", "1d", "facebook", "facebook-graph", "ok", "ok (id post URL)"),
                     ("run_gobekli", "1d", "youtube", "youtube-graph", "skipped", "skipped: no Graph source"),
                     ("run_gobekli", "1d", "youtube", "upload-post", "ok", "post analytics")])
    return db


class Tags(HTMLParser):
    """Telegram's HTML: every tag it is given must be closed, in order."""
    ALLOWED = {"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"}

    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.bad.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append("/" + tag)


def conventions(reply):
    """One message, Telegram's HTML, a registered screen behind every button, one 🏠, labels that show whole,
    and the bot's own navigation (with_nav) changes none of it."""
    text, rows = reply
    assert len(text) <= 4096 and len(text) <= control.ANALYTICS_BUDGET
    parser = Tags()
    parser.feed(text)
    assert parser.bad == [] and parser.stack == [], (parser.bad, parser.stack)
    assert long_labels(rows) == []
    flat = [b for row in rows for b in row]
    assert sum(b == HOME for b in flat) == 1 and rows[-1][-1] == HOME
    assert all(b["callback_data"].partition(":")[0] in SCREENS for b in flat)
    assert all(len(b["callback_data"].encode()) <= 64 for b in flat)
    again = with_nav(rows, "canalytics:")
    assert sum(b == HOME for row in again for b in row) == 1
    return text, rows


def plain(text):
    return re.sub(r"<[^>]+>", "", text).replace("&amp;", "&")


def test_the_overview_reads_at_a_glance(production):
    text, rows = conventions(control.analytics_overview(production, NOW))
    body = plain(text)
    assert text.startswith("📈 <b>Audience overview</b>")
    # The last 7 and 28 days: Kailasa 121 + 339 + 899 and Göbekli 1,244 views; Kailasa's 7.9 s of 15.2 s.
    assert "7 days (2 videos): 2.6k views, 52% watched, 3 shares, 2 saves" in body
    assert "28 days (2 videos): 2.6k views" in body
    assert "📸 Instagram — 1 video: 121 views, 52% watched, 3 shares, 2 saves" in body
    assert "📘 Facebook — 1 video: 339 views" in body
    assert "▶️ YouTube Shorts — 2 videos: 2.1k views" in body
    assert "🎬 YouTube long: no data yet" in body                      # an empty row says so, never zeros
    assert "Stories & carousels: none of 2 measurable" in body
    assert "Most views: Kailasa" in body and "1.4k" in body and "Fewest views: Göbekli" in body
    assert "Watch %: only Kailasa" in body                              # one video with watch data: no ranking
    assert "Data so far: 2 videos. Patterns from 12: 10 more, around" in body and "a week" in body
    assert "⚠ Instagram: Graph failed, nothing answered" in body
    assert "✓ Facebook: Facebook Graph" in body and "✓ YouTube: Upload-Post" in body
    assert "<blockquote expandable>Instagram: instagram-graph: 1 failed" in text   # the full lines, folded
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert callbacks[:6] == ["caplat:ig", "caplat:fb", "caplat:yt", "caplat:ytlong", "caplat:stories", "capick:0"]
    assert {"text": "← Audience overview", "callback_data": "canalytics:"} not in [b for row in rows for b in row]


def test_a_platform_screen_lists_its_videos_newest_first_with_retention(production):
    text, rows = conventions(control.analytics_platform(production, "ig", now=NOW))
    body = plain(text)
    assert text.startswith("📸 <b>Instagram</b>")
    assert body.index("1. Göbekli") < body.index("2. Kailasa")
    assert "1d: not measurable: Instagram Graph: HTTP 400: Unsupported get…" in body
    assert "Object with ID" in text and text.count("<blockquote expandable>") == 2   # the whole reason, folded
    assert "1d: 121 views, reach" not in body and "1d: 121 views, 3 shares, 2 saves, 11 likes" in body
    assert "Retention: 52% watched, avg 7.9 s of 15.2 s, skip 38%" in body
    assert "Posted 25 Oct (3 d ago) · snapshots 1d · next 3d due now" in body
    assert "Trend of the newest: — (from 3 videos at one snapshot)" in body
    assert [b["callback_data"] for b in rows[0]] == [f"cavideo:{production.run_key('run_gobekli')}",
                                                     f"cavideo:{production.run_key('run_kailasa')}"]
    assert rows[-1] == [{"text": "← Audience overview", "callback_data": "canalytics:"}, HOME]
    text, _ = conventions(control.analytics_platform(production, "ytlong", now=NOW))
    assert "No data yet" in text and "0 views" not in text
    text, _ = conventions(control.analytics_platform(production, "stories", now=NOW))
    assert "Instagram story 1 (story): not measurable: story expired" in plain(text)


def test_the_video_screen_sets_the_platforms_side_by_side(production):
    text, rows = conventions(control.analytics_video(production, production.run_key("run_kailasa"), NOW))
    table = re.search(r"<pre>(.*?)</pre>", text, re.S).group(1).splitlines()
    assert table[0].split() == ["IG", "FB", "YT"]
    assert table[1].split() == ["1d", "121", "·", "·"] and table[2].split() == ["3d", "·", "339", "899"]
    body = plain(text)
    assert "📸 Instagram (1d): 121 views, 3 shares, 2 saves, 11 likes, 52% watched, avg 7.9 s, skip 38%" in body
    assert "▶️ YouTube Shorts (3d): 899 views, 0 shares" not in body and "899 views, 17 likes" in body
    assert "Facebook story 1 (story): not measurable" in body
    assert "Every snapshot" in body and "Meta keeps story insights 24 h" in text
    assert {"text": "📸 Instagram", "callback_data": "caplat:ig"} in rows[0]
    text, _ = conventions(control.analytics_video(production, "run_gobekli", NOW))
    table = re.search(r"<pre>(.*?)</pre>", text, re.S).group(1).splitlines()
    assert table[1].split() == ["1d", "×", "×", "1.2k"]


def test_the_picker_numbers_videos_as_analytics_n_opens_them(production):
    text, rows = conventions(control.analytics_picker(production, now=NOW))
    body = plain(text)
    assert "1. Göbekli" in body and "1.2k views (YT)" in body
    assert "2. Kailasa" in body and "1.4k views, 52% watched (IG/FB/YT)" in body
    assert [b["text"] for b in rows[0]] == ["1", "2"]


@pytest.mark.parametrize("typed, starts", [
    ("", "📈 <b>Audience overview</b>"), ("ig", "📸 <b>Instagram</b>"), ("instagram", "📸 <b>Instagram</b>"),
    ("fb", "📘 <b>Facebook</b>"), ("yt", "▶️ <b>YouTube Shorts</b>"), ("shorts", "▶️ <b>YouTube Shorts</b>"),
    ("ytlong", "🎬 <b>YouTube long</b>"), ("youtube long", "🎬 <b>YouTube long</b>"),
    ("stories", "🗂 <b>Stories &amp; carousels</b>"), ("carousels", "🗂 <b>Stories &amp; carousels</b>"),
    ("videos", "🎞 <b>Videos measured</b>"), ("1", "🎞 <b>Göbekli"), ("video 2", "🎞 <b>Kailasa"),
    ("run run_kailasa", "🎞 <b>Kailasa"),
])
def test_typed_subcommands_open_their_screen(production, typed, starts):
    h = make_command_handler(production, lambda: None, lambda: "")
    text, rows = conventions(h("analytics", typed))
    assert text.startswith(starts), text[:80]


def test_the_buttons_open_the_same_screens(production):
    h = make_command_handler(production, lambda: None, lambda: "")
    key = production.run_key("run_kailasa")
    assert h("analytics", "run " + key)[0] == h("cavideo", key)[0]
    assert h("analytics", "fb")[0] == h("caplat", "fb")[0] and h("canalytics", "")[0] == h("analytics", "")[0]
    assert h("analytics", "videos")[0] == h("capick", "0")[0]
    text, _ = conventions(h("analytics", "nonsense"))
    assert "Not a view I know" in text and "<code>/analytics ig</code>" in text
    text, _ = conventions(h("analytics", "9"))
    assert "No video 9 on the list: there are 2." in text


def test_every_analytics_screen_is_registered_with_a_parent():
    for key in ("canalytics", "caplat", "cavideo", "capick", "analytics"):
        assert key in SCREENS and SCREENS[key][2].partition(":")[0] in SCREENS


def _three_instagram_videos(db, newest_views):
    for i, views in enumerate((100, 120, newest_views)):
        posted = NOW - timedelta(days=6 - 2 * i)
        run(db, f"run_{i}", f"Video {i}", posted)
        metric(db, f"run_{i}", "instagram", "1d", "instagram-graph", {"views": views}, posted + timedelta(days=1))


def test_a_trend_arrow_needs_three_videos_at_the_same_snapshot(db):
    for i, views in enumerate((100, 300)):
        posted = NOW - timedelta(days=4 - 2 * i)
        run(db, f"run_{i}", f"Video {i}", posted)
        metric(db, f"run_{i}", "instagram", "1d", "instagram-graph", {"views": views}, posted + timedelta(days=1))
    assert audience.trend(audience.dashboard(db), "ig") is None
    assert "📸 Instagram — 2 videos" in plain(control.analytics_overview(db, NOW)[0])


@pytest.mark.parametrize("newest, arrow", [(300, "▲"), (40, "▼"), (112, "≈")])
def test_with_three_videos_the_newest_is_set_against_the_median(db, newest, arrow):
    _three_instagram_videos(db, newest)
    assert audience.trend(audience.dashboard(db), "ig") == arrow
    assert f"📸 Instagram {arrow} 3 videos" in plain(control.analytics_overview(db, NOW)[0])
    assert f"Trend of the newest: {arrow}" in plain(control.analytics_platform(db, "ig", now=NOW)[0])


def test_a_newer_snapshot_is_never_set_against_older_ones(db):
    _three_instagram_videos(db, 300)
    metric(db, "run_0", "instagram", "7d", "instagram-graph", {"views": 5000}, NOW)  # only the oldest at 7d
    assert audience.trend(audience.dashboard(db), "ig") == "▲"                  # still the 1-day counts


def test_the_sources_line_warns_only_when_a_graph_source_failed(db):
    run(db, "run_a", "A", NOW - timedelta(days=1, hours=2))
    metric(db, "run_a", "instagram", "1d", "instagram-graph", {"views": 50}, NOW)
    sources(db, NOW, [("run_a", "1d", "instagram", "instagram-graph", "ok", "ok (id remembered)"),
                      ("run_a", "1d", "facebook", "facebook-graph", "skipped", "skipped: not configured"),
                      ("run_a", "1d", "facebook", "upload-post", "ok", "post analytics")])
    body = plain(control.analytics_overview(db, NOW)[0])
    assert "⚠" not in body and "✓ Instagram: Instagram Graph" in body and "✓ Facebook: Upload-Post" in body
    sources(db, NOW + timedelta(hours=6), [("run_a", "3d", "facebook", "facebook-graph", "failed", "HTTP 403: (#200) permission"),
                                           ("run_a", "3d", "facebook", "upload-post", "ok", "post analytics")])
    body = plain(control.analytics_overview(db, NOW)[0])
    assert "⚠ Facebook: Graph failed, Upload-Post answered" in body and "(#200) permission" in body


def test_nothing_measured_says_so(db):
    text, rows = conventions(control.analytics_overview(db, NOW))
    assert "Nothing measured yet" in text and "no collection yet" in text
    for reply in (control.analytics_platform(db, "fb", now=NOW), control.analytics_picker(db, now=NOW),
                  control.analytics_video(db, "17", NOW)):
        text, _ = conventions(reply)
        assert "no data yet" in text.lower() or "no audience numbers" in text.lower()


def test_a_long_history_stays_one_message_and_pages(db):
    audience.ensure_schema(db)
    values = []
    for i in range(30):
        posted = NOW - timedelta(days=i + 1)
        run(db, f"run_{i:02d}", f"A long title for video number {i} about an ancient wonder", posted)
        for platform in ("instagram", "facebook", "youtube"):
            for snap in ("1d", "3d", "7d", "28d"):
                values.append((f"run_{i:02d}", platform, snap, "upload-post", json.dumps(
                    {"views": 1000 + i, "reach": 900, "shares": 4, "saves": 2, "likes": 30, "comments": 2,
                     "ig_reels_avg_watch_time": 9100, "reels_skip_rate": 41.0}), (posted + timedelta(days=1)).isoformat()))
            values.append((f"run_{i:02d}", f"{platform}_story1", "story", "expired",
                           json.dumps({"unavailable": "x" * 290}), posted.isoformat()))
    with db._conn() as conn:
        conn.executemany("INSERT INTO audience_metrics VALUES (?,?,?,?,?,?)", values)
    for reply in (control.analytics_overview(db, NOW), control.analytics_platform(db, "ig", 0, NOW),
                  control.analytics_platform(db, "ig", 5, NOW), control.analytics_platform(db, "stories", 1, NOW),
                  control.analytics_video(db, "run_03", NOW), control.analytics_picker(db, 2, NOW)):
        conventions(reply)
    _, rows = control.analytics_platform(db, "ig", 0, NOW)
    assert ["▶", "⏭"] == [b["text"] for b in rows[-2]]
    assert "enough to read patterns" in control.analytics_overview(db, NOW)[0]


def test_numbers_are_compact_with_their_units():
    assert [audience.compact(x) for x in (0, 7.9, 121, 1244, 12_400, 1_350_000, None)] == \
        ["0", "7.9", "121", "1.2k", "12k", "1.4M", "—"]
    assert audience.compact(7.9, " s") == "7.9 s"
