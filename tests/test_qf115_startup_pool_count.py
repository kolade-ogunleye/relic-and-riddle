"""QF-115: the bot's startup dashboard never waits on rehashing every pool photo.

After a restart the first dashboard counted the queued topics' photo sets before anything was posted, and each count
decoded every pool photo again for its perceptual hash (the dev bot stayed silent for minutes on a loaded machine).
The layout is now posted first, the sets are counted in the background ("counting photo sets…", then the number),
and each photo's signature is kept, on disk for the pools, until the file changes. A fake Bot API keeps the sends and
edits; nothing calls Telegram."""
import hashlib
import json
import os
import threading
import time

import pytest
from PIL import Image

from src import config


class FakeTelegram:
    def __init__(self):
        self.next_id, self.calls, self.messages = 100, [], {}
        self.lock = threading.Lock()

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        with self.lock:
            self.calls.append((method, data))
            if method == "sendMessage":
                self.next_id += 1
                self.messages[self.next_id] = data
                return {"message_id": self.next_id}
            if method.startswith("editMessage"):
                mid = int(data["message_id"])
                self.messages[mid] = {**self.messages.get(mid, {}), **data}
                return {"message_id": mid}
        return True

    def dashboards(self):
        with self.lock:
            return [(m, d.get("text", "")) for m, d in self.calls
                    if m in ("sendMessage", "editMessageText") and "RELIC" in d.get("text", "")]


def make_bot(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    monkeypatch.setattr(bot, "_api", fake)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    return bot, fake


@pytest.fixture
def slow_pools(monkeypatch, tmp_path):
    """Every queued topic's set takes `delay` seconds to count (or until `gate` is set)."""
    from src import control
    monkeypatch.setattr(control, "_READY_CACHE", {})
    monkeypatch.setattr(control, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "DASHBOARD_POOL_COUNT_WAIT_SECONDS", 0.2, raising=False)
    state = {"gate": threading.Event(), "delay": 30.0, "counted": 0}

    def pool_ready(topic_id, assets_dir=None, beats=None):
        state["gate"].wait(state["delay"])
        state["counted"] += 1
        return True
    monkeypatch.setattr("src.asset_pipeline.pool_ready", pool_ready)
    yield state
    state["gate"].set()   # a blocked startup (the parent) is let go
    # this test's startup, count and redraw threads end before its fake Bot API is taken away: none of them may
    # reach Telegram, or draw a later test's bot
    deadline = time.monotonic() + 60
    for name in ("qf115-startup", "photo-set-count", "photo-set-redraw"):
        for thread in [t for t in threading.enumerate() if t.name == name]:
            thread.join(max(0.0, deadline - time.monotonic()))
    getattr(control, "when_pools_counted", lambda listener: None)(None)   # no later test's count draws this bot


def start(db, bot):
    """main.py's bot startup: the startup card, then the dashboard."""
    from src import control
    build = lambda: control.startup_dashboard_card(db, mode="bot")
    bot.startup_card = build
    getattr(control, "when_pools_counted", lambda listener: None)(getattr(bot, "pools_counted", None))
    text, buttons = build()
    bot.show_dashboard(text, buttons)


@pytest.mark.parametrize("clean", [False, True])
def test_the_layout_is_posted_before_the_photo_sets_are_counted(db, monkeypatch, slow_pools, clean):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", clean)
    if clean:
        from src import notice_conditions
        monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    bot, fake = make_bot(db, monkeypatch)
    worker = threading.Thread(target=start, args=(db, bot), name="qf115-startup", daemon=True)
    worker.start()
    worker.join(5)
    posted = fake.dashboards()
    assert not slow_pools["gate"].is_set() and slow_pools["counted"] == 0   # nothing counted yet
    assert posted and posted[0][0] == "sendMessage", "the startup dashboard waited for the photo sets"
    assert "counting photo sets…" in posted[0][1] and "🟢 Bot started" in posted[0][1]
    if clean:
        sends = [d.get("text", "") for m, d in fake.calls if m == "sendMessage"]
        assert "RELIC" not in sends[0] and "RELIC" in sends[1]   # the notifications panel, then the dashboard
    slow_pools["gate"].set()


@pytest.mark.parametrize("clean", [False, True])
def test_the_dashboard_line_is_drawn_again_with_the_count(db, monkeypatch, slow_pools, clean):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", clean)
    if clean:
        from src import notice_conditions
        monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    slow_pools["delay"] = 0.6
    bot, fake = make_bot(db, monkeypatch)
    start(db, bot)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any("with photos ready" in t for _m, t in fake.dashboards()):
        time.sleep(0.05)
    shown = fake.dashboards()
    assert "counting photo sets…" in shown[0][1]
    assert shown[-1][0] == "editMessageText", shown   # drawn in place, not a second dashboard
    assert sum(1 for m, _t in shown if m == "sendMessage") == 1
    assert "📋 Queue: 3 topics · 3 with photos ready" in shown[-1][1]
    assert "🟢 Bot started" in shown[-1][1]          # the startup card's own line stays


def test_a_screen_opened_meanwhile_is_left_alone(db, monkeypatch, slow_pools):
    slow_pools["delay"] = 0.6
    bot, fake = make_bot(db, monkeypatch)
    start(db, bot)
    mid = int(db.get_setting("telegram_dashboard_id"))
    bot.show_navigation_panel(mid, "🖼 <b>Photo sets</b>", [[{"text": "🏠 Dashboard", "callback_data": "cdash:"}]])
    slow_pools["gate"].set()
    counted()
    assert fake.messages[mid]["text"].startswith("🖼 <b>Photo sets</b>")


def test_a_photo_condition_is_kept_while_the_sets_are_counted(db, monkeypatch, slow_pools):
    from src import notice_conditions, notifications
    slow_pools["delay"] = 2.0
    notifications.ensure(db)
    notifications.post(db, notifications.NEEDS, "cond:photos", "Photo sets not ready",
                       "Only 0 of 3 queued topics have photos ready.", emoji="🖼", cond_count=1)
    notice_conditions.sync(db)   # the sets are being counted: the item stands
    assert "cond:photos" in {i["dedupe_key"] for i in notifications.open_items(db)}


# ------------------------------------------------------------------------------- the photos' signatures
def picture(path, flip=False):
    pixels = [((x * 4) % 256, (y * 4) % 256, ((x + y) * 2) % 256) for y in range(64) for x in range(64)]
    image = Image.new("RGB", (64, 64))
    image.putdata(pixels[::-1] if flip else pixels)
    image.save(path, format="BMP")   # uncompressed: both pictures are exactly the same size


@pytest.fixture
def pool(monkeypatch, tmp_path):
    from src import photo_pool
    assets = tmp_path / "assets"
    folder = assets / "topic"
    folder.mkdir(parents=True)
    monkeypatch.setattr(config, "ASSETS_DIR", assets)
    monkeypatch.setattr(photo_pool, "SIGNATURE_CACHE_FILE", tmp_path / "cache" / "photo_signatures.json", raising=False)
    monkeypatch.setattr(photo_pool, "_SIGNATURES", {})
    monkeypatch.setattr(photo_pool, "_loaded", False, raising=False)
    monkeypatch.setattr(photo_pool, "_unsaved", set(), raising=False)
    calls = []
    real = photo_pool.signature
    monkeypatch.setattr(photo_pool, "signature", lambda path: calls.append(path) or real(path))
    return folder, calls


def restart(monkeypatch):
    """A new process: nothing remembered in memory."""
    from src import photo_pool
    monkeypatch.setattr(photo_pool, "_SIGNATURES", {})
    monkeypatch.setattr(photo_pool, "_loaded", False, raising=False)


def test_a_cached_signature_is_reused_after_a_restart(pool, monkeypatch):
    from src import photo_pool
    folder, calls = pool
    picture(folder / "a.bmp")
    old = time.time() - 3600
    os.utime(folder / "a.bmp", (old, old))
    first = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, first)
    assert len(calls) == 1
    restart(monkeypatch)
    again = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, again)
    assert len(calls) == 1, "the restart decoded the unchanged photo again"
    assert again[0]["phash"] == first[0]["phash"] and again[0]["source_sha256"] == first[0]["source_sha256"]


def test_a_photo_replaced_in_the_same_clock_tick_is_hashed_again(pool):
    from src import photo_pool
    folder, calls = pool
    path = folder / "a.bmp"
    picture(path)
    stamp = path.stat().st_mtime_ns
    first = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, first)
    picture(path, flip=True)                  # new content, the same size
    os.utime(path, ns=(stamp, stamp))         # and the same modification time (one clock tick)
    assert path.stat().st_size == (folder / "a.bmp").stat().st_size
    again = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, again)
    assert again[0]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert again[0]["phash"] != first[0]["phash"] and len(calls) == 2


def test_a_photo_replaced_with_the_same_size_while_down_is_hashed_again(pool, monkeypatch):
    from src import photo_pool
    folder, calls = pool
    path = folder / "a.bmp"
    picture(path)
    old = time.time() - 3600
    os.utime(path, (old, old))
    first = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, first)
    restart(monkeypatch)
    size = path.stat().st_size
    picture(path, flip=True)
    assert path.stat().st_size == size
    again = [{"file": "a.bmp"}]
    photo_pool.refresh(folder, again)
    assert again[0]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() != first[0]["source_sha256"]
    assert len(calls) == 2


def test_the_record_keeps_only_the_pools_photos(pool, monkeypatch, tmp_path):
    from src import photo_pool
    folder, _calls = pool
    picture(folder / "a.bmp")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    picture(elsewhere / "b.bmp")
    photo_pool.refresh(folder, [{"file": "a.bmp"}])
    photo_pool.refresh(elsewhere, [{"file": "b.bmp"}])
    path = tmp_path / "cache" / "photo_signatures.json"
    assert path.is_file(), "no signature record on disk"
    record = json.loads(path.read_text(encoding="utf-8"))["photos"]
    assert [os.path.basename(k) for k in record] == ["a.bmp"]


# ------------------------------------------------------------------------------- round 2
def counted(timeout=30):
    """Until the background count has ended and its listener has drawn: the counter's thread (it calls the listener
    after the count) and any redraw thread are joined, under load too."""
    from src import control
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and getattr(control, "_counter", None) is not None:
        time.sleep(0.05)
    for name in ("photo-set-count", "photo-set-redraw"):
        for thread in [t for t in threading.enumerate() if t.name == name]:
            thread.join(max(0.0, deadline - time.monotonic()))


def test_a_process_that_cannot_draw_again_shows_the_count_and_the_photo_warning(db, monkeypatch, slow_pools):
    """A run's worker (no when_pools_counted listener) draws the dashboard after a decision with the clean chat off
    (production): it counts in place, so its dashboard has the number and the Needs-you photo line."""
    from src import control
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    monkeypatch.setattr(config, "DASHBOARD_POOL_COUNT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr("src.asset_pipeline.pool_ready", lambda *a, **k: time.sleep(0.5) or False)
    control.when_pools_counted(None)
    bot, fake = make_bot(db, monkeypatch)
    bot.show_dashboard(fresh=True)
    counted()
    shown = fake.dashboards()
    assert len(shown) == 1, shown
    assert "counting photo sets…" not in shown[-1][1]
    assert "📋 Queue: 3 topics · 0 with photos ready" in shown[-1][1]
    assert "🖼 Only 0 queued topic(s) have photos ready" in shown[-1][1]


def _redraw_with_a_tap(db, monkeypatch, slow_pools, tap):
    """The count lands; while the redraw builds the card, `tap(bot, panel)` runs (after the redraw's first check)."""
    from src import control
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    slow_pools["delay"] = 0.6
    bot, fake = make_bot(db, monkeypatch)
    builds = {"n": 0}

    def build():
        builds["n"] += 1
        out = control.startup_dashboard_card(db, mode="bot")
        if builds["n"] == 2:   # the redraw's build
            tap(bot, int(db.get_setting("telegram_dashboard_id")))
        return out
    bot.startup_card = build
    control.when_pools_counted(bot.pools_counted)
    text, buttons = build()
    bot.show_dashboard(text, buttons)
    counted()
    assert builds["n"] == 2, "the count did not reach the redraw"
    return bot, fake, int(db.get_setting("telegram_dashboard_id"))


def test_a_screen_opened_while_the_redraw_is_built_is_left_alone(db, monkeypatch, slow_pools):
    def tap(bot, panel):
        bot.show_navigation_panel(panel, "🖼 <b>Photo sets</b>", [[{"text": "🏠 Dashboard", "callback_data": "cdash:"}]])
    _bot, fake, panel = _redraw_with_a_tap(db, monkeypatch, slow_pools, tap)
    assert fake.messages[panel]["text"].startswith("🖼 <b>Photo sets</b>"), "the opened screen was overwritten"


def test_a_tap_that_arrived_while_the_redraw_is_built_is_not_overwritten(db, monkeypatch, slow_pools):
    """A tap raises the panel's revision as it arrives, before its screen is drawn: the redraw yields to it."""
    def tap(bot, panel):
        with bot._panel_lock:
            bot._panel_revisions[panel] = bot._panel_revisions.get(panel, 0) + 1
    _bot, fake, _panel = _redraw_with_a_tap(db, monkeypatch, slow_pools, tap)
    assert [m for m, _t in fake.dashboards()] == ["sendMessage"], "the redraw raced the tap's screen"


def test_a_note_leading_the_dashboard_is_kept_by_the_redraw(db, monkeypatch, slow_pools):
    from src import control
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False)
    slow_pools["delay"] = 2.0   # the note is drawn while the sets are being counted (6 s in all)
    bot, fake = make_bot(db, monkeypatch)
    start(db, bot)
    panel = int(db.get_setting("telegram_dashboard_id"))
    text, buttons = control.dashboard_text_and_buttons(db, None)
    assert "counting photo sets…" in text
    bot.show_navigation_panel(panel, "⏸ <b>Paused</b>: no scheduled runs or posts until you resume.\n\n" + text, buttons)
    slow_pools["gate"].set()
    counted()
    final = fake.messages[panel]["text"]
    assert final.startswith("⏸ <b>Paused</b>") and "3 with photos ready" in final, final[:200]


def test_a_record_of_the_wrong_shape_is_treated_as_missing(pool, monkeypatch, tmp_path):
    from src import photo_pool
    folder, _calls = pool
    picture(folder / "a.bmp")
    path = tmp_path / "cache" / "photo_signatures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str((folder / "a.bmp").resolve())
    stat = (folder / "a.bmp").stat()
    for record in ({"photos": ["not", "a", "dict"]}, {"photos": "text"},
                   {"photos": {key: {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino,
                                     "ctime_ns": stat.st_ctime_ns, "checked_at": "soon", "digest": "x",
                                     "signature": {"source_sha256": "x", "phash": "0", "dhash": "0"}}}}):
        path.write_text(json.dumps(record), encoding="utf-8")
        restart(monkeypatch)
        images = [{"file": "a.bmp"}]
        try:
            photo_pool.refresh(folder, images)
        except Exception as e:   # a run start's refresh must not fail over its cache
            raise AssertionError(f"refresh failed over the record {record}: {e!r}") from e
        assert "hash_error" not in images[0], record
        assert images[0]["source_sha256"] == hashlib.sha256((folder / "a.bmp").read_bytes()).hexdigest()
    saved = json.loads(path.read_text(encoding="utf-8"))["photos"]
    assert isinstance(saved[key]["checked_at"], float)   # the bad entry was replaced


WRITER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from src import config, photo_pool
config.ASSETS_DIR = Path(sys.argv[2])
photo_pool.SIGNATURE_CACHE_FILE = Path(sys.argv[3])
folder = Path(sys.argv[2]) / "topic"
for name in sys.argv[4:]:
    photo_pool.refresh(folder, [{"file": name}])
"""


def test_two_processes_writing_the_record_lose_nothing(pool, tmp_path):
    import subprocess
    import sys
    folder, _calls = pool
    names = [[f"{who}{i}.bmp" for i in range(12)] for who in "ab"]
    for name in names[0] + names[1]:
        picture(folder / name, flip=name.endswith(("1.bmp", "3.bmp", "5.bmp")))
    record = tmp_path / "cache" / "photo_signatures.json"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, root, str(folder.parent), str(record), *group])
               for group in names]
    assert [w.wait(120) for w in writers] == [0, 0]
    saved = json.loads(record.read_text(encoding="utf-8"))["photos"]
    assert sorted(os.path.basename(k) for k in saved) == sorted(names[0] + names[1])
    assert not [p for p in record.parent.iterdir() if p.suffix == ".tmp"]
