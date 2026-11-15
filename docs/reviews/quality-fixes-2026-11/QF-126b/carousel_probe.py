"""QF-126b round 2: every real carousel through the carousel request (read only on the data; scratch copies only).

Usage: RR_DEV_OUTPUT=<a folder of run_* folders> RR_PROD_SAMPLE=<the production sample: output/ and assets/>
       python carousel_probe.py <repo root> <out json> <scratch dir>
Writes counts only to <out json> (the per-run rows go to <out json>.rows.json beside it, which is not committed).
Sends every carousel of three data sets through PipelineCoordinator._carousel_request of the repo given:
  dev            RR_DEV_OUTPUT's run_* (copied to scratch without media; slide photos read where recorded)
  prod_raw       the production sample's output/run_* as exported (copied to scratch)
  prod_relocated the same, each slide's recorded photo pointed at the sample's own assets/<topic>/ copy (a derived
                 cache copy at its original), as QF-126 review 3 did, so the credit files are read as production reads
Each sent caption is checked by this file's own reader: every photo the slides show (recorded carousel) or every
photo of the run's ledger (a carousel with a slide without a record) whose licence asks a credit has its author,
title, licence name, link to its page (or the page id recorded for that link) and licence link on one line.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SCRATCH = Path(sys.argv[3])
sys.path.insert(0, str(ROOT))
DEV = Path(os.environ["RR_DEV_OUTPUT"])
PROD = Path(os.environ["RR_PROD_SAMPLE"])
URL = r"https?://[^\s|<>\"]+"
ENTRY = re.compile(r"^(?:(?P<author>.+?): )?“(?P<title>.*)” (?P<link>" + URL + r")$")
MEDIA = ("*.mp4", "*.mp3", "*.wav", "*.m4a", "*.mov")


def read(text):
    works = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("📷") or line.startswith("📷 Images via"):
            continue
        fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
        if len(fields) == 5 and all(re.fullmatch(URL, f) or f == "Unknown" for f in fields[3:]):
            works.append(tuple(fields))
            continue
        head = fields[0].rsplit(" ", 1)
        entries = [ENTRY.match(f) for f in fields[1:]]
        if len(head) != 2 or not re.fullmatch(URL, head[1]) or not entries or not all(entries):
            continue
        author = ""
        for e in entries:
            author = e.group("author") or author
            works.append((author, e.group("title"), head[0], e.group("link"), head[1]))
    return works


def deed(link):
    return re.sub(r"^http://", "https://", str(link)).rstrip("/")


def needs(r):
    lic = str(r.get("license") or "").lower()
    return bool(r.get("attribution_required")) or lic.startswith("cc by")


def carried(caption, records, pages):
    from src.credits import author_name, credit_title
    works = read(caption)
    asked = []
    for r in records:
        if not isinstance(r, dict) or not needs(r):
            continue
        parts = (author_name(r), credit_title(r), " ".join(str(r.get("license") or "").split()),
                 str(r.get("source_url") or ""), str(r.get("license_url") or ""))
        if parts not in asked:
            asked.append(parts)
    have = 0
    for a, t, lic, src, lurl in asked:
        links = {src} | ({f"https://commons.wikimedia.org/?curid={pages[src]}"} if src in pages else set())
        have += any(w[:3] == (a, t, lic) and w[3] in links and deed(w[4]) == deed(lurl) for w in works)
    return have, len(asked)


def topic_of(run_name):
    return re.sub(r"_\d+$", "", run_name.removeprefix("run_"))


def relocate(run_copy):
    topic = topic_of(run_copy.name)
    moved = 0
    for layout in list(run_copy.glob("carousel/still_layout.json")) + list(run_copy.glob("carousel_fb/still_layout.json")):
        data = json.loads(layout.read_text(encoding="utf-8"))
        for rec in data.values():
            recipe = rec.get("recipe") if isinstance(rec, dict) else None
            if not isinstance(recipe, dict):
                continue
            for key in ("photo", "photo_next"):
                if not recipe.get(key):
                    continue
                name = re.split(r"[\\/]", str(recipe[key]))[-1]
                name = re.sub(r"_[0-9a-f]{16}(\.\w+)$", r"\1", name)
                target = PROD / "assets" / topic / name
                if target.is_file():
                    recipe[key] = str(target)
                    moved += 1
        layout.write_text(json.dumps(data), encoding="utf-8")
    return moved


def copy_run(run, dest):
    shutil.copytree(run, dest, ignore=shutil.ignore_patterns(*MEDIA))
    return dest


def probe(run, out_rows):
    from main import PipelineCoordinator
    from src import multi_format
    slides = sorted(str(p) for p in (run / "carousel").glob("carousel_slide_*.jpg"))
    if not slides:
        return
    try:
        payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
    except (OSError, ValueError, AssertionError):
        payload = {}
    shown = [Path(s) for s in slides] + sorted((run / "carousel_fb").glob("carousel_slide_*.jpg"))
    recorded = all(multi_format.slide_record(s) for s in shown)
    try:
        ledger = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = None
    try:
        pages = json.loads((run / "source_links.json").read_text(encoding="utf-8")).get("page_ids") or {}
    except (OSError, ValueError):
        pages = {}
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": payload.get("topic_id") or "t1"})
    row = {"run": run.name, "recorded": recorded, "ledger": ledger is not None}
    try:
        caption, comment = coordinator._carousel_request(run.name, payload, slides)
    except ValueError as e:
        row.update(status="held", final=bool(getattr(e, "final", False)), why=str(e)[:300])
        out_rows.append(row)
        return
    if recorded:
        records = []
        for photo in dict.fromkeys(p for s in shown for p in multi_format.slide_photos(s)):
            entry = multi_format.photo_credit(photo)
            if entry:
                records.append(entry)
    else:
        from src.credits import _reference_records
        assets = (ledger or {}).get("assets") or []
        records = list(assets) + _reference_records({"assets": assets})
    have, want = carried(caption, records, pages)
    row.update(status="sent", length=len(caption), comment_lines=len([x for x in comment.splitlines() if x.strip()]),
               credits_have=have, credits_asked=want, compact="“" in caption,
               complete=have == want and not comment and len(caption) <= 2200)
    out_rows.append(row)


def main():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    results = {}
    for name, source in (("dev", DEV), ("prod_raw", PROD / "output"), ("prod_relocated", PROD / "output")):
        rows = []
        for run in sorted(source.glob("run_*")):
            if not (run / "carousel").is_dir():
                continue
            dest = copy_run(run, SCRATCH / name / run.name)
            if name == "prod_relocated":
                relocate(dest)
            probe(dest, rows)
        results[name] = rows
    summary = {}
    for name, rows in results.items():
        s = {}
        for r in rows:
            group = ("recorded" if r["recorded"] else "recordless") + ("" if r["ledger"] else "_no_ledger")
            g = s.setdefault(group, {"carousels": 0, "sent_complete": 0, "sent_lacking": 0, "sent_compact": 0,
                                     "held_final_do_not_fit": 0, "held_other": {}, "longest_sent": 0})
            g["carousels"] += 1
            if r["status"] == "sent":
                g["sent_complete" if r["complete"] else "sent_lacking"] += 1
                g["sent_compact"] += int(r["compact"])
                g["longest_sent"] = max(g["longest_sent"], r["length"])
            elif r["final"] and "do not fit" in r["why"]:
                g["held_final_do_not_fit"] += 1
            else:
                key = ("over 2,200" if "over the 2,200" in r["why"] else "photo in neither record"
                       if "does not hold th" in r["why"] else "ledger missing" if "is missing" in r["why"]
                       else "incomplete" if "lacks" in r["why"] else r["why"][:80])
                g["held_other"][key] = g["held_other"].get(key, 0) + 1
        summary[name] = s
    OUT.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + chr(10), encoding="utf-8", newline=chr(10))
    OUT.with_name(OUT.name + ".rows.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


main()
