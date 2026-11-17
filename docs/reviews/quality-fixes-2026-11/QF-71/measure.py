"""QF-71 measurements: which messages ring (no disable_notification), in the earlier layout (V1) and in V2.

    python measure.py <repo root>

Runs on the base (QF-70's commit) and on this commit. Every notice call site in the measured root's src/ and main.py
is found by its syntax (TelegramApprovalBot.notify, notifications.say(...notify...), the coordinator's _notice and
_alert) with the group and section it passes; each distinct (sender, group, section) is then sent once through the
measured root's own code, with a stub in place of the Telegram API, and the request it would make is read. The run's
progress card and the review card with its albums are sent the same way. Generated inputs only: a throwaway database
and app root in the system temp folder (no .env); nothing is sent.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="qf71-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
for tracked in subprocess.run(["git", "-C", str(ROOT), "ls-files", "data", "fonts"], capture_output=True, text=True,
                              check=True).stdout.splitlines():
    (TMP / tracked).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / tracked, TMP / tracked)
sys.path.insert(0, str(ROOT))
TOPICS = {"studio": 1, "needs": 2, "reviews": 3, "runs": 4, "info": 5}


def _const(node):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except ValueError:
        return "<expr>"   # decided at run time (e.g. "needs" if failed else "info")


def call_sites() -> list[dict]:
    """(file, line, sender, group, section) for every notice call site."""
    out = []
    for path in sorted((ROOT / "src").glob("*.py")) + [ROOT / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            kw = {k.arg: k.value for k in n.keywords if k.arg}
            said = n.args[0].attr if name == "say" and n.args and isinstance(n.args[0], ast.Attribute) else ""
            if (name == "notify" and isinstance(f, ast.Attribute)) or said == "notify":
                sender = "notify"
            elif said == "_alert":
                continue   # _notice's own body: counted at each _notice site
            elif name == "_notice":
                sender = "_notice"
            else:
                continue
            group, section = _const(kw.get("group")), _const(kw.get("section"))
            if "silent" in kw and _const(kw["silent"]) is True:
                group = f"{group} (silent=True)"
            out.append({"site": f"{path.relative_to(ROOT).as_posix()}:{n.lineno}", "sender": sender,
                        "group": group, "section": section})
    return out


def bot_and_calls():
    from src.db import PipelineDatabase
    from src.telegram_bot import TelegramApprovalBot
    bank = TMP / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / f"m{len(list(TMP.glob('m*.db')))}.db", topic_bank_file=bank)
    bot = TelegramApprovalBot(db=db, bot_token="1:offline", chat_id="555", approver_ids={"555"})
    calls = []
    bot._api = lambda method, **kw: calls.append((method, dict(kw.get("data") or {}))) or {"message_id": 100 + len(calls)}
    return db, bot, calls


def rings(data) -> bool:
    return str(data.get("disable_notification", "")).lower() not in ("true", "1")


def send_once(sender, group, section) -> bool:
    import main
    _, bot, calls = bot_and_calls()
    silent = isinstance(group, str) and group.endswith("(silent=True)")
    g = group.split(" ")[0] if isinstance(group, str) else group
    g = None if g in ("None", "<expr>") else g
    s = None if section in (None, "<expr>") else section
    item = {k: v for k, v in (("group", g), ("section", s)) if v is not None}
    if sender == "notify":
        bot.notify("x", silent=silent, **item)
    else:
        owner = SimpleNamespace(telegram_bot=bot)
        owner._alert = lambda text, buttons=None, group=None, **it: main.PipelineCoordinator._alert(owner, text, buttons,
                                                                                                    group, **it)
        main._notice(owner, "x", **item)
    sent = [d for m, d in calls if m == "sendMessage"]
    return rings(sent[-1])


def cards() -> dict:
    """The progress card, and the earlier layout's review card with its albums (a real run's committed records)."""
    from src import release
    _, bot, calls = bot_and_calls()
    db = bot.db
    db.create_run("r1", "01_topic")
    bot.send_progress_card("r1", "Topic")
    progress_rings = sum(rings(d) for m, d in calls if m == "sendMessage")
    run = "run_gol_gumbaz_whispering_gallery_1793278143"
    root = TMP / f"cards{len(list(TMP.glob('cards*')))}" / run
    shutil.copytree(ROOT / "tests" / "fixtures" / "ui02_review_v2" / run, root)
    (root / "master.mp4").write_bytes(b"offline review video")
    from PIL import Image
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    release.write_release_report = lambda run_, dry_run=False: report
    from src import telegram_bot
    telegram_bot._video_dimensions = lambda path: {"width": 720, "height": 1280}
    from src.telegram_state import initialize
    initialize(db)
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    payload["topic_id"] = "01_topic"
    db.create_run(run, "01_topic")
    db.update_run(run, "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    calls.clear()
    bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, run)
    sends = [d for m, d in calls if m.startswith("send") and m != "sendChatAction"]
    return {"progress_card_rings": progress_rings, "review_messages": len(sends),
            "review_messages_ringing": sum(rings(d) for d in sends)}


def main_() -> None:
    from src import config
    try:
        sites = call_sites()
        kinds = Counter((s["sender"], str(s["group"]), str(s["section"])) for s in sites)
        out = {"call_sites": len(sites), "kinds": len(kinds)}
        for layout, v2 in (("v1", False), ("v2", True)):
            config.TELEGRAM_CLEAN_CHAT = False
            config.TELEGRAM_CARDS_V2 = v2
            config.TELEGRAM_TOPICS = TOPICS
            config.RELIC_STRICT_TOPICS = False
            rang = {k: send_once(*k) for k in kinds}
            # a Needs-you site with an explicit silent=True counts as a silent Needs-you site (review 1 of QF-71)
            needs = [k for k in kinds if "needs" in (k[1].split(" ")[0], k[2])]
            routine = [k for k in kinds if k not in needs and "<expr>" not in k[1:]]
            out[layout] = {
                "routine_call_sites_ringing": sum(kinds[k] for k in routine if rang[k]),
                "routine_call_sites": sum(kinds[k] for k in routine),
                "needs_call_sites_silent": sum(kinds[k] for k in needs if not rang[k]),
                "needs_call_sites": sum(kinds[k] for k in needs),
                "notice_naming_no_group_or_section_rings": send_once("notify", None, None),
                **cards(),
            }
        v1, v2 = out["v1"], out["v2"]
        out["acceptance_met"] = all(x["routine_call_sites_ringing"] == 0 and x["needs_call_sites_silent"] == 0
                                    and not x["notice_naming_no_group_or_section_rings"]
                                    and x["progress_card_rings"] == 0 and x["review_messages_ringing"] == 1
                                    for x in (v1, v2))
        print(json.dumps(out, indent=1))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main_()
