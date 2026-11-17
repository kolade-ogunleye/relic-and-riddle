"""QF-81 real-data check (outside the repo): python equivalence.py <qf-81 worktree> <dev output dir> <scratch dir>.
For each real dev run with a recorded carousel photo match: draws both carousel copies the way the run used to
(MultiFormatGenerator.generate_carousel_sequence on the recorded photos, Instagram then Facebook), and the QF-81 way
(carousel_lazy.plan then carousel_lazy.draw, as the preview and the daemon do, into its staging folder), each in its
own scratch copy of the run's script and graphics; compares every slide's bytes and counts what the slot audit
(slide audit plus both copies' text contrast) holds. The vision look (still_vision.look) is replaced by "not checked"
(no live call); the photo match returns the run's recorded match (no live call). Prints counts and a per-run line with
the run's topic slug (dev data)."""
import hashlib
import json
import shutil
import socket
import sys
from pathlib import Path
from unittest.mock import patch


def h(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    tree, output, scratch = (Path(a) for a in sys.argv[1:4])
    sys.path.insert(0, str(tree))

    def refuse(*a, **k):
        raise OSError("network refused")
    socket.socket.connect = refuse
    from src import carousel_lazy
    from src.multi_format import CAROUSEL_PHOTOS, MultiFormatGenerator
    from src.video_qa import QualityAuditor
    rows = []
    for run in sorted(output.glob("run_*")):
        if not (run / CAROUSEL_PHOTOS).is_file() or not (run / "script_metadata.json").is_file():
            continue
        record = json.loads((run / CAROUSEL_PHOTOS).read_text(encoding="utf-8"))
        stills = [Path(p) for p in record["key"]["stills"]]
        if not all(p.is_file() for p in stills):
            rows.append({"run": run.name, "skipped": "photos missing"})
            continue
        payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        dirs = {}
        for way in ("eager", "lazy"):
            d = scratch / run.name / way
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            if (run / "graphics").is_dir():
                (d / "graphics").mkdir()
                for f in (run / "graphics").iterdir():   # the posters and clips (not the nested source records)
                    if f.is_file():
                        shutil.copyfile(f, d / "graphics" / f.name)
            shutil.copyfile(run / CAROUSEL_PHOTOS, d / CAROUSEL_PHOTOS)
            dirs[way] = d
        gen = MultiFormatGenerator()
        matched = [Path(p) for p in record["photos"]]
        with patch("src.still_vision.look", lambda *a, **k: ([], "not checked")), \
                patch.object(MultiFormatGenerator, "_match_photos", lambda self, cards, stills, strict=False, matched=matched: matched):
            gen.generate_carousel_sequence(stills, payload, dirs["eager"])
            gen.generate_carousel_sequence(stills, payload, dirs["eager"], facebook=True)
            carousel_lazy.plan(dirs["lazy"], stills, payload)
            drawing = carousel_lazy.draw(dirs["lazy"], payload, gen, QualityAuditor())
            held = drawing.get("held") or ""
        same = total = 0
        for copy in ("carousel", "carousel_fb"):
            a = sorted((dirs["eager"] / copy).glob("carousel_slide_*.jpg"))
            b = sorted((dirs["lazy"] / carousel_lazy.STAGE / copy).glob("carousel_slide_*.jpg"))
            total += max(len(a), len(b))
            same += sum(1 for x, y in zip(a, b) if x.name == y.name and h(x) == h(y))
        types = sorted({i.get("type") for i in drawing.get("held_by") or []})
        rows.append({"run": run.name, "slides": total, "identical": same, "held": bool(held), "held_by_types": types,
                     "audit_issues": len(drawing.get("issues") or []), "held_by": len(drawing.get("held_by") or [])})
        print(json.dumps(rows[-1]), flush=True)
    done = [r for r in rows if "slides" in r]
    print(json.dumps({"runs": len(done), "skipped": len(rows) - len(done), "slides": sum(r["slides"] for r in done),
                      "identical": sum(r["identical"] for r in done), "held": sum(bool(r["held"]) for r in done),
                      "held_by_contrast": sum("text_contrast" in r["held_by_types"] for r in done)}))


if __name__ == "__main__":
    main()
