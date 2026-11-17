"""QF-115 measurements: how long a bot restart takes to post its first message, and photo_pool.refresh() times.

    python measure.py <repo root>

Runs on the parent (fe88d4a6) and on the commit. Generates 12 photo pools of 15 JPEGs each (2400 x 1600, seeded
noise, so every photo hashes differently) in a throwaway app root in the system temp folder, queues one topic per
pool in a throwaway database, and then, each scenario in a fresh Python process (a restart), runs the bot's startup
path as main.py does (the startup card, then show_dashboard) against a fake Bot API that records when each message
is sent. No network, no .env, no real pool is written.

Scenarios: the first start (no signature record on disk: "cold") and the next restart ("warm"), each with
TELEGRAM_CLEAN_CHAT off and on. For each: seconds from the start of the startup path to the first message sent and to
the dashboard; the dashboard's queue line as first posted; and the seconds until the dashboard shows the counted
number with the count itself (on the parent the first draw already waited for it). photo_pool.refresh() over every
pool: in a cold process, again in the same process, and in a restarted process.

Round 2: "worker" is a run's worker process after the next restart (clean chat off, production's mode): no
when_pools_counted listener is registered there, and it draws the dashboard as stamp_review_outcome does
(show_dashboard(fresh=True)). It must show the counted number in that draw, never "counting photo sets…", since
nothing in that process would draw it again.

Optional, for the README only (not part of the printed acceptance): QF115_POOLS=<an assets folder> measures the same
on real pools read-only (the signature record goes to the temp folder) and prints it as a second JSON document.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
CHILD = os.environ.get("QF115_CHILD")
POOLS, PHOTOS, SIZE = 12, 15, (2400, 1600)


def generate(app: Path) -> Path:
    import numpy as np
    from PIL import Image
    assets = app / "assets"
    for p in range(POOLS):
        pool = assets / f"pool_{p:02d}"
        pool.mkdir(parents=True)
        images = []
        for k in range(PHOTOS):
            rng = np.random.default_rng(p * 1000 + k)
            small = rng.integers(0, 256, (SIZE[1] // 40, SIZE[0] // 40, 3), dtype=np.uint8)
            picture = Image.fromarray(small).resize(SIZE, Image.Resampling.BICUBIC)
            noise = rng.integers(0, 24, (SIZE[1], SIZE[0], 3), dtype=np.uint8)
            Image.fromarray(np.clip(np.asarray(picture, dtype=np.int16) + noise, 0, 255).astype(np.uint8)).save(
                pool / f"{k:02d}.jpg", quality=90)
            images.append({"file": f"{k:02d}.jpg", "operator_keep": True, "license": "CC BY 4.0",
                           "artist": f"Author {k}", "title": f"Photo {k}"})
        (pool / "credits.json").write_text(json.dumps({"reviewed": True, "reviewed_by": "operator",
                                                       "images": images}), encoding="utf-8")
    fetched = time.time() - 86400   # fetched a day before the restart, as a real pool is
    for path in assets.rglob("*"):
        if path.is_file():
            os.utime(path, (fetched, fetched))
    return assets


def child() -> dict:
    """One process start: the scenario in QF115_CHILD, the pools in QF115_ASSETS."""
    sys.path.insert(0, str(ROOT))
    app = Path(os.environ["RELIC_APP_ROOT"])
    assets = Path(os.environ["QF115_ASSETS"])
    from src import asset_pipeline, config, control, photo_pool
    config.ASSETS_DIR = control.ASSETS_DIR = asset_pipeline.ASSETS_DIR = assets
    if hasattr(photo_pool, "SIGNATURE_CACHE_FILE"):
        photo_pool.SIGNATURE_CACHE_FILE = app / "cache" / "photo_signatures.json"
    folders = sorted(p for p in assets.iterdir() if (p / "credits.json").is_file())
    if CHILD == "refresh":
        from src.credits import load_credits
        records = [(f, load_credits(f)["images"]) for f in folders]
        times = []
        for _ in range(2):
            t0 = time.perf_counter()
            for folder, images in records:
                photo_pool.refresh(folder, images)
            times.append(time.perf_counter() - t0)
        return {"photos": sum(len(i) for _f, i in records), "first_refresh_elapsed_s": round(times[0], 3),
                "second_refresh_elapsed_s": round(times[1], 3),
                "hash_errors": sum(1 for _f, i in records for im in i if im.get("hash_error"))}
    clean = CHILD.endswith("clean")
    config.TELEGRAM_CLEAN_CHAT = clean
    config.TELEGRAM_CARDS_V2 = False
    from src.db import PipelineDatabase
    from src.telegram_bot import TelegramApprovalBot
    bank = app / f"bank-{CHILD}.json"
    bank.write_text(json.dumps({"topics": [{"id": f.name, "title": f.name.replace("_", " "), "hook": "h",
                                            "location": {"name": "X", "country": "Y"}} for f in folders]}),
                    encoding="utf-8")
    db = PipelineDatabase(db_path=app / f"{CHILD}.db", topic_bank_file=bank)
    sent: list[tuple[float, str]] = []
    edits: list[tuple[float, str]] = []
    state = {"next": 1000}

    def fake(method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        if method == "sendMessage":
            state["next"] += 1
            sent.append((time.perf_counter(), data.get("text", "")))
            return {"message_id": state["next"]}
        if method.startswith("editMessage"):
            edits.append((time.perf_counter(), data.get("text", "")))
            return {"message_id": int(data.get("message_id") or 0)}
        return True
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    bot._api = fake
    bot._delete_soon = bot.delete_message
    bot._queue_navigation = lambda work: work() or True
    if clean:
        bot.layout.clock = time.time
        if hasattr(bot.layout, "repost"):
            bot.layout.repost.sleep = lambda s: None
    t0 = time.perf_counter()
    if CHILD.startswith("worker"):
        # a run's worker after a decision (stamp_review_outcome): no listener, a fresh dashboard
        bot.show_dashboard(fresh=True)
    else:
        # main.py's bot startup, as each commit has it
        build = lambda: control.startup_dashboard_card(db, mode="bot", assets_dir=assets)
        if hasattr(control, "when_pools_counted"):
            bot.startup_card = build
            control.when_pools_counted(bot.pools_counted)
        text, buttons = build()
        bot.show_dashboard(text, buttons)
    first = sent[0][0] - t0 if sent else None
    dash = next(((at, body) for at, body in sent if "RELIC" in body), (None, ""))
    queue = next((ln for ln in dash[1].splitlines() if ln.startswith("📋 Queue")), "")
    deadline = time.perf_counter() + 600
    counted_at, counted = None, None
    while time.perf_counter() < deadline:
        shown = [(at, body) for at, body in sent + edits if "RELIC" in body and "📋 Queue" in body]
        done = [(at, body) for at, body in shown if "with photos ready" in body]
        if done:
            at, body = done[0]
            counted_at = at - t0
            line = next(ln for ln in body.splitlines() if ln.startswith("📋 Queue"))
            counted = int(line.split("· ")[1].split(" ")[0])
            break
        time.sleep(0.05)
    return {"elapsed_to_first_message_s": round(first, 3) if first is not None else None,
            "elapsed_to_dashboard_s": round(dash[0] - t0, 3) if dash[0] is not None else None,
            "first_queue_line": queue, "elapsed_to_count_shown_s": round(counted_at, 3) if counted_at is not None else None,
            "counted_ready_sets": counted, "messages_sent": len(sent),
            "photo_warning_shown": any("have photos ready" in body for _at, body in sent + edits if "RELIC" in body)}


def run(app: Path, assets: Path, scenario: str) -> dict:
    env = {**os.environ, "QF115_CHILD": scenario, "RELIC_APP_ROOT": str(app), "QF115_ASSETS": str(assets),
           "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, __file__, str(ROOT)], env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=1800, check=False)
    if out.returncode:
        raise SystemExit(f"{scenario} failed:\n{out.stdout}\n{out.stderr}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def measure(assets: Path, generated: bool) -> dict:
    result: dict = {}
    for mode in ("plain", "clean"):
        app = Path(tempfile.mkdtemp(prefix="qf115-app-"))
        (app / "data").mkdir()
        for name in ("reference_facts.json", "stage_budget.json", "cover_art_direction.json"):   # read at import
            if (ROOT / "data" / name).is_file():
                (app / "data" / name).write_bytes((ROOT / "data" / name).read_bytes())
        result[f"startup_cold_{mode}"] = run(app, assets, f"cold-{mode}")
        result[f"startup_warm_{mode}"] = run(app, assets, f"warm-{mode}")   # the next restart, its record on disk
        if mode == "plain":
            result["worker_after_restart_plain"] = run(app, assets, "worker-plain")   # round 2
    app = Path(tempfile.mkdtemp(prefix="qf115-app-"))
    (app / "data").mkdir()
    result["refresh_cold_process"] = run(app, assets, "refresh")
    result["refresh_restarted_process"] = run(app, assets, "refresh")
    record = app / "cache" / "photo_signatures.json"
    result["signature_record_on_disk"] = record.is_file()
    result["pools"] = sum(1 for p in assets.iterdir() if (p / "credits.json").is_file())
    if generated:
        result["photos_per_pool"], result["photo_size"] = PHOTOS, list(SIZE)
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if CHILD:
        print(json.dumps(child(), ensure_ascii=False))
        return
    work = Path(tempfile.mkdtemp(prefix="qf115-pools-"))
    assets = generate(work)
    print(json.dumps(measure(assets, True), indent=2, ensure_ascii=False))
    real = os.environ.get("QF115_POOLS")
    if real:
        print(json.dumps({"real_pools": measure(Path(real), False)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
