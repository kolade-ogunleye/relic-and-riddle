"""Evidence renders for QF-23: the real surfaces whose checked copy the base tree cut, drawn by a given tree.

    python render_evidence.py <code root> <checkout holding output/run_* and assets/> <out dir> <before|after>

Real inputs only: each case's run_*/verified_script.json and its topic's licensed photo pool (assets/<topic_id>/),
read in place. The cases are the surfaces measure.py found cut on the base (the carousel's closing slide, whose
"<SUBJECT> · YOUR TAKE" kicker lost its last word to an ellipsis, in both copies, and the analytics chart's Reel name);
the photos are matched to the slides as the pipeline matches them; and (review 1) a dev run's title made as a
documentary's 9:16 cover (main.documentary_cover cut it with "…" at 22 characters). Writes half-size JPEGs <run>_<surface>_<tag>.jpg
and drawn.json (what each drew, or the refusal) into <out dir>; the app root is a temporary folder (no .env read).
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

CASES = [("run_unfinished_obelisk_aswan_1793056896", "carousel"),
         ("run_unfinished_obelisk_aswan_1793056896", "carousel_fb"),
         ("run_roman_self_healing_concrete_1793163918", "carousel"),
         ("run_damascus_steel_nanotubes_1792732472_short", "carousel"),
         ("run_greek_fire_siphon_1792735284_short", "chart"),
         ("run_unfinished_obelisk_aswan_1793056896", "doc_cover")]


def _offline(*_a, **_k):
    raise RuntimeError("render_evidence.py is offline")


def _half(src: Path, dst: Path) -> None:
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.resize((im.width // 2, im.height // 2), Image.Resampling.LANCZOS).save(dst, quality=82)


def main(code: Path, data: Path, out: Path, tag: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = Path(tempfile.mkdtemp(prefix="qf23-evidence-"))
    shutil.copytree(code / "fonts", app / "fonts")
    (app / "data").mkdir()
    shutil.copyfile(code / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
    os.environ.update(RELIC_APP_ROOT=str(app), FONT_SIZE="80")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(code))
    from src import audience_chart
    from src.multi_format import MultiFormatGenerator
    record = {}
    for run, surface in CASES:
        payload = json.loads((data / "output" / run / "verified_script.json").read_text(encoding="utf-8"))
        key = f"{run}_{surface}"
        try:
            if surface == "chart":
                reel = {"run_id": run, "title": payload["title"],
                        "views": {"instagram": 1200, "youtube": 900, "facebook": 300}}
                _, info = audience_chart.render([reel], app / "chart.png", sample=True)
                _half(app / "chart.png", out / f"{key}_{tag}.jpg")
                record[key] = {"drawn": info["titles"]}
                continue
            pool = sorted(p for p in (data / "assets" / payload["topic_id"]).glob("*.jpg"))
            work = app / key
            if surface == "doc_cover":
                from main import documentary_cover
                from src.visual_engine import VisualEngine
                doc = {**payload, "thumbnail": documentary_cover(payload["title"])}
                cover, _ = VisualEngine().generate_thumbnails(pool[0], doc, work, variants=False)
                _half(cover, out / f"{key}_{tag}.jpg")
                record[key] = {"copy": {k: doc["thumbnail"][k] for k in ("line1", "line2")}}
                continue
            slides = MultiFormatGenerator().generate_carousel_sequence(pool, payload, work,
                                                                        facebook=surface == "carousel_fb")
            last = Path(slides[-1])
            _half(last, out / f"{key}_{tag}.jpg")
            rec = json.loads((last.parent / "still_layout.json").read_text(encoding="utf-8"))[last.name]
            record[key] = {"slide": last.name, "shortened": rec.get("shortened"),
                           "kicker": [e for e in rec.get("elements", []) if e.get("name") == "kicker"]}
        except ValueError as e:
            record[key] = {"refused": str(e)}
    with open(out / f"drawn_{tag}.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
        f.write("\n")
    shutil.rmtree(app, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[4] not in ("before", "after"):
        raise SystemExit("usage: render_evidence.py <code root> <checkout> <out dir> <before|after>")
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
