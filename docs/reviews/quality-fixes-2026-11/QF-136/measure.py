"""QF-136 acceptance: the visual critic gate catches planted defects of every class and passes a clean set.

    python docs/reviews/quality-fixes-2026-11/QF-136/measure.py <repo root>

Committed inputs only: Story frames drawn by this tree's own Story renderer (multi_format) from real Story copy on
three crops of the committed real photo of the freeze-panel fixtures, and caption files built by this tree's caption builder from
the committed real narrations (words.json), with and without the reconstruction badge. Defects are planted into
copies (scripts/visual_critic_plant.py: the copy changed and redrawn by the renderer itself (a letter dropped, changed or
transposed, a short word dropped, words swapped, a word added, punctuation or a digit dropped, a digit changed), a
record that gives no truth, record moved, picture shifted over
the edge and out of the zone, a field moved onto another, the ground blended to the ink, a caption family no font
carries, a badge lengthened past its plate, a recorded mark under 3:1), then both sets are judged
(src/visual_critic.check_set, faces off: the face model is gitignored; the faces are measured on the real set,
README). Prints JSON: per planted class (and text mutation), the cases caught as that class and the cases whose frame
fails the gate; and the findings on the clean set, per class. Exits non-zero unless its claims hold: no clean frame
fails, nothing in the clean set is left unjudged, every planted case fails the gate.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO))
os.environ.setdefault("FONT_SIZE", "80")
FIX = REPO / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
PHOTO = FIX / "stills" / "richat_structure_sahara_eye_01_commons_960.jpg"   # the one committed real photo
CROPS = [(0, 480), (240, 720), (480, 960)]                                     # three grounds: its thirds
# Story copy of real development runs (their recipes' slide fields)
SLIDES = [
    {"kicker": "ANCIENT MARVEL", "line1": "THE ACOUSTIC DOME", "line2": "OF GOL GUMBAZ",
     "body": "Sound circles this vast chamber along its curved boundary."},
    {"kicker": "NO METAL RINGS", "line1": "BRICK AND MORTAR", "line2": "WITHOUT TENSION",
     "body": "By one estimate, the central dome spans 144 feet (44 m)."},
    {"kicker": "ACOUSTIC MYSTERY", "line1": "INTENTIONAL DESIGN", "line2": "OR LUCKY ACCIDENT?",
     "body": "Construction halted in 1656 after Mohammad Adil Shah died."},
    {"kicker": "SAHARA EYE", "line1": "A TARGET IN", "line2": "THE DESERT",
     "body": "The rings span about 40 km and are visible from orbit."},
]
STORY_FIELDS = ("kicker", "line1", "line2", "body")


def build(set_dir: Path) -> None:
    from PIL import Image

    from src.multi_format import MultiFormatGenerator
    from src.video_composer import VideoComposer
    gen = MultiFormatGenerator()
    d = set_dir / "social" / "committed"
    d.mkdir(parents=True)
    work = set_dir.parent / "grounds"
    work.mkdir()
    with Image.open(PHOTO) as im:
        src = im.convert("RGB")
    for g, (x0, x1) in enumerate(CROPS):
        photo = work / f"ground_{g}.jpg"             # a crop, so the recipe never names a repository file
        src.crop((x0, 0, x1, src.height)).save(photo, quality=95)
        for i, slide in enumerate(SLIDES):
            im, lay = gen._render_story_frame(photo, slide, i % 3, 3, {}, tight=False)
            png = d / f"stories_g{g}_s{i}.png"
            im.convert("RGB").save(png)
            rec = lay.to_dict()
            (png.with_suffix(".record.json")).write_text(json.dumps({
                "frame": png.name, "kind": "still", "size": rec["size"], "deliverable": "story", "zone": rec["zone"],
                "protected": rec["protected"], "elements": rec["elements"],
                "expect": [{"field": k, "text": slide[k], "truth": "recipe"} for k in STORY_FIELDS],
                "recipe": {"kind": "story", "photo": str(photo), "slide": slide, "index": i % 3, "count": 3, "poll": {},
                           "tight": False}}, indent=1), encoding="utf-8", newline="\n")
    c = set_dir / "captions"
    c.mkdir()
    for wf in sorted((FIX / "runs").glob("run_*/words.json")):
        words = json.loads(wf.read_text(encoding="utf-8"))
        words = [w for w in words if float(w["start"]) < 20.0]        # the first 20 s of each narration
        end = float(words[-1]["start"]) + float(words[-1].get("dur", 1.0))
        topic = wf.parent.name[4:].rsplit("_", 1)[0]
        for suffix, recon in (("", None), ("__badge", [(0.0, end)])):
            ass = c / f"{topic}{suffix}.ass"
            VideoComposer().build_subtitles(words, ass, reconstruction_intervals=recon)
            ass.with_name(ass.stem + ".words.json").write_text(json.dumps(words), encoding="utf-8", newline="\n")


def add_mark_record(set_dir: Path) -> None:
    """A real graphic's contrast record (no poster frame, no inputs: not part of the clean set, which it would fail
    as uncovered): the source the mark_contrast plants are copied from."""
    g = set_dir / "graphics"
    g.mkdir()
    rec = FIX / "runs" / "run_richat_structure_sahara_eye_1793474843" / "graphics" / "graphic_numbers.mp4.contrast.json"
    shutil.copyfile(rec, g / rec.name)


def main() -> None:
    from scripts.visual_critic_plant import plant, score  # noqa: F401  (score: the CLI's reading, below)
    from src.visual_critic import check_set
    tmp = Path(tempfile.mkdtemp(prefix="qf136-measure-"))
    try:
        base, planted = tmp / "set", tmp / "planted"
        build(base)
        rb = check_set(base, tmp / "report-base", faces=False, sheet=False, workers=4)
        add_mark_record(base)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            plant(base, tmp / "report-base" / "report.json", planted, 10, None)
        rp = check_set(planted, tmp / "report-planted", faces=False, sheet=False, workers=4)
        cases = json.loads((planted / "planted.json").read_text(encoding="utf-8"))
        pr = {e["frame"]: e for e in rp["frames"]}
        bf = {e["frame"]: e for e in rb["frames"]}
        recall: dict[str, dict[str, int]] = {}
        for cs in cases:
            n_new = sum(1 for x in pr.get(cs["frame"], {}).get("findings", []) if x["class"] == cs["class"])
            n_old = sum(1 for x in bf.get(cs["from"], {}).get("findings", []) if x["class"] == cs["class"])
            for key in [cs["class"]] + ([f'{cs["class"]}/{cs["mutation"]}'] if cs.get("mutation") else []):
                r = recall.setdefault(key, {"caught": 0, "gate_failed": 0, "planted": 0})
                r["planted"] += 1
                r["caught"] += int(n_new > n_old)                       # a new finding of the planted class
                r["gate_failed"] += int(not pr.get(cs["frame"], {}).get("pass", True))   # the frame fails the gate
        out = {"clean_set": {"frames": rb["frame_count"], "frames_failed": rb["frames_failed"],
                             "findings": {c: v["findings"] for c, v in rb["per_class"].items()}},
               "planted": dict(sorted(recall.items())),
               "recall_min": min(v["caught"] / v["planted"] for v in recall.values()),
               "gate_recall_min": min(v["gate_failed"] / v["planted"] for v in recall.values()),
               "planted_total": sum(v["planted"] for k, v in recall.items() if "/" not in k),
               "caught_total": sum(v["caught"] for k, v in recall.items() if "/" not in k)}
        print(json.dumps(out, indent=1, sort_keys=True))
        # the claims (README): no false fail on the clean set, nothing it holds left unjudged, every planted case
        # fails the gate (found as its own class is reported per class, not claimed: a field set down onto another
        # may fail as text_identity first)
        claims = {"clean set: no frame fails": rb["frames_failed"] == 0,
                  "clean set: no coverage finding": not rb["coverage"]["findings"],
                  "planted: every case fails the gate (no false pass)": all(
                      v["gate_failed"] == v["planted"] for v in recall.values())}
        broken = [k for k, v in claims.items() if not v]
        if broken:
            raise SystemExit("QF-136 measure: claims not met: " + "; ".join(broken))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
