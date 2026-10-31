"""
tests/test_archive_film_e2e.py
==============================
Archive film end to end, with real files and the real bot (operator, 2026-10-28: "take a second look at your own
work"): an approved film is cut by the footage path a run uses, passes the release gate's rights check and is
credited; the Telegram commands reach the handler from typed text and from the Find film button.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from src import archive_film, config, footage
from src.credits import rights_evidence_ok


def _film(path: Path, seconds: int = 12):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=640x480:rate=25:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120)


def _pending(assets: Path, topic_id: str) -> Path:
    folder = assets / topic_id / "footage" / "pending"
    folder.mkdir(parents=True)
    clip = folder / "01_PompeiiAndVesuvius.mp4"
    _film(clip)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", str(clip.with_suffix(".jpg"))], check=True)
    clip.with_suffix(".json").write_text(json.dumps({
        "provider": "archive.org", "id": "PompeiiAndVesuvius", "title": "Pompeii And Vesuvius", "year": "1950",
        "artist": "AV Geeks", "license": "Public domain", "license_url": "http://creativecommons.org/publicdomain/mark/1.0/",
        "attribution_required": False, "source_url": "https://archive.org/details/PompeiiAndVesuvius",
        "collections": ["avgeeks", "ephemera"], "community_upload": False, "duration": 12.0, "width": 640, "height": 480}),
        encoding="utf-8")
    return clip


def test_an_approved_film_is_cut_whole_passes_the_rights_check_and_is_credited(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path)
    _pending(tmp_path, "pompeii")
    assert footage.get_curated_footage("pompeii", tmp_path / "run")[0] == []          # pending is never used
    archive_film.decide(tmp_path, "pompeii", [1], use=True)
    clips, credits = footage.get_curated_footage("pompeii", tmp_path / "run")
    assert len(clips) == 1 and footage.probe(clips[0])["width"] == 1080 and footage.probe(clips[0])["height"] == 1920
    rec = credits[0]
    assert rights_evidence_ok(rec)                                                 # the release gate's test, as it is
    assert rec["source_url"] == "https://archive.org/details/PompeiiAndVesuvius" and rec["file"] == str(clips[0])
    assert footage.credit_line(credits) == "🎥 Footage: AV Geeks (Public domain) via the Internet Archive"


def test_a_cc_by_film_needs_its_creator_named_to_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path)
    clip = _pending(tmp_path, "pompeii")
    rec = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8"))
    rec.update(license="CC BY 4.0", license_url="https://creativecommons.org/licenses/by/4.0/", attribution_required=True)
    clip.with_suffix(".json").write_text(json.dumps(rec), encoding="utf-8")
    archive_film.decide(tmp_path, "pompeii", [1], use=True)
    _, credits = footage.get_curated_footage("pompeii", tmp_path / "run")
    assert rights_evidence_ok(credits[0]) and credits[0]["artist"] == "AV Geeks"
    assert "AV Geeks (CC BY 4.0) via the Internet Archive" in footage.credit_line(credits)


# ------------------------------------------------------------------ the real bot, typed and tapped
def _bot(db, calls):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": 900}
    return bot


def _typed(bot, text, handler, message_id=300):
    bot._process_update({"update_id": message_id, "message": {"message_id": message_id, "text": text, "date": int(time.time()),
                                                               "from": {"id": 555}, "chat": {"id": 555}}}, handler)
    bot.drain()


def _tap(bot, data, handler, message_id=70, update_id=1):
    bot._process_update({"update_id": update_id, "callback_query": {"id": f"q{update_id}", "data": data, "from": {"id": 555},
                                                                      "message": {"message_id": message_id, "chat": {"id": 555}}}}, handler)
    bot.drain()


def test_typed_footage_commands_and_the_find_button_reach_the_handler(db, tmp_path, monkeypatch):
    from src.control import make_command_handler
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path)
    topic = db.list_all_topics()[0]
    n = "1"
    _pending(tmp_path, topic["id"])
    monkeypatch.setattr(archive_film, "find", lambda t, spec, assets, **k: {"saved": [], "ask": []})
    seen = []
    real = make_command_handler(db, lambda: None, lambda *a, **k: "", assets_dir=tmp_path)

    def handler(command, args):
        seen.append((command, args))
        return real(command, args)
    calls = []
    bot = _bot(db, calls)
    _typed(bot, f"/footage {n}", handler, 301)
    _typed(bot, f"/footage {n} find", handler, 302)
    _tap(bot, f"filmfind:{n}", handler, update_id=2)
    _typed(bot, f"/footage {n} use 1", handler, 303)
    assert [c for c in seen if c[0].startswith("film")] == [("filmlist", n), ("filmfind", n), ("filmfind", n), ("filmuse", f"{n} 1")]
    texts = " ".join(str(d.get("text") or d.get("caption") or "") for _, d in calls)
    assert "Archive film" in texts and "Waiting for you" in texts and "✅ In use: Pompeii And Vesuvius" in texts
    assert (tmp_path / topic["id"] / "footage" / "PompeiiAndVesuvius.mp4").exists()   # approved into the topic's footage
