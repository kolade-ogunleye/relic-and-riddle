"""QF-143 measurements: YouTube thumbnail headlines that end on a leading mark (":") or a dangling fragment.

    python measure.py [<code root>]                          sweep this tree (or <code root>), print the summary
    python measure.py sweep <code root> <dev checkout> <out dir>     renders + rows.json for one code root
    python measure.py compare <base out> <fix out> <out dir>          before.json, after.json, comparison.json

Cases (every YouTube thumbnail copy source on the dev side; the production sample is not used):
- catalogue: the freeze-panel fixtures' covers (fixtures.load("covers")), each with its catalogue hero;
- library: the script library's payloads (data/script_library/*/verified_script.json);
- dev: every run in the main checkout's output/run_* (read only) with a verified_script.json, or, for a run that
  kept only script_metadata.json (the documentary), that file. Hero: the covers survey's hero for the run (the still
  that matches its delivered cover), else for the documentary run the still that best matches its delivered
  thumbnail_youtube_16x9.jpg (luma correlation), else the first photo of the run's topic folder under assets/
  (recorded per case as hero_source). A library payload is drawn on the catalogue's first hero.

Each case is drawn by VisualEngine.generate_youtube_thumbnail, as a run draws it, offline. youtube_thumbnail.render is
wrapped only to read the plates it drew. A headline is the drawn kicker / title / title_1 / title_2 lines; it "ends on
a mark" when its last line ends on : ; , & / + or a dash, and "ends on a fragment" when that last line ends on a word
that needs what followed it (END_WORDS below: articles, prepositions that take an object, conjunctions) and has no
closing ? ! . or ellipsis, or (drawn alone) opens on OF / THAN, or on AND / OR / BUT / NOR followed by an article.
The predicate is written out here as literal word lists, independent of the code under test (round 2, review
finding 4), and is the same for both trees. "Subtitle kept": the
copy's line2 is drawn (as title_2, or inside title).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKS = ":;,&/+-–—"
END_WORDS = {"THE", "A", "AN", "ITS", "THEIR", "OF", "TO", "FROM", "WITH", "AT", "BY", "FOR", "INTO", "ONTO",
             "UPON", "VIA", "AMONG", "BETWEEN", "DURING", "DESPITE", "AGAINST", "TOWARD", "TOWARDS", "THAN", "AND",
             "OR", "BUT", "NOR"}
CONJUNCTIONS = {"AND", "OR", "BUT", "NOR"}
HEADLINE = ("kicker", "title", "title_1", "title_2")


def _offline(*_a, **_k):
    raise OSError("offline: QF-143 measure makes no network call")


def _main_checkout() -> Path:
    if os.environ.get("QF_MAIN_CHECKOUT"):
        return Path(os.environ["QF_MAIN_CHECKOUT"])
    import subprocess
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE,
                            capture_output=True, text=True, check=True).stdout.strip()
    return Path(common).parent


def _words(t: str) -> list[str]:
    return [w.strip("'").upper() for w in re.findall(r"[^\W_]+(?:'[^\W_]+)*", t or "") if w.strip("'")]


def judge(lines: list[str]) -> dict:
    """{"ends_on_mark", "ends_on_fragment"} for a drawn headline (its lines top to bottom)."""
    if not lines:
        return {"ends_on_mark": False, "ends_on_fragment": False}
    last = lines[-1].rstrip()
    words = _words(last)
    mark = bool(last) and last[-1] in MARKS
    closed = bool(last) and last[-1] in "?!.…"
    alone = len(lines) == 1
    frag = bool(words) and not closed and (
        words[-1] in END_WORDS
        or (alone and (words[0] in {"OF", "THAN"}
                       or (words[0] in CONJUNCTIONS and len(words) > 1 and words[1] in {"THE", "A", "AN"}))))
    return {"ends_on_mark": mark, "ends_on_fragment": frag}


def _cases(code: Path, dev: Path, fx) -> list[tuple]:
    import numpy as np
    from PIL import Image, ImageOps
    cat = fx.load("covers")
    out = [("catalogue", c["topic"], fx.path(c["hero"]["photo"]), "catalogue hero", fx.payload(c)) for c in cat]
    first_hero = fx.path(cat[0]["hero"]["photo"])
    for p in sorted(glob.glob(str(code / "data" / "script_library" / "*" / "verified_script.json"))):
        out.append(("library", Path(p).parent.name, first_hero, "catalogue's first hero",
                    json.loads(Path(p).read_text(encoding="utf-8"))))
    survey = {fx.path(s["run"]).name: fx.path(s["hero"]) for s in fx.section("covers")["survey"]}
    for run in sorted(p for p in (dev / "output").glob("run_*") if p.is_dir()):
        src = run / "verified_script.json"
        if not src.is_file():
            src = run / "script_metadata.json"
            if not src.is_file():
                continue
        payload = json.loads(src.read_text(encoding="utf-8"))
        if not (payload.get("thumbnail") or payload.get("title")):
            continue
        topic = re.sub(r"_\d{10}(_\w+)?$", "", run.name[4:])
        photos = sorted((dev / "assets" / topic).glob("*.jpg"))
        hero, how = survey.get(run.name), "covers survey hero"
        if hero is None and (run / "thumbnail_youtube_16x9.jpg").is_file() and photos:
            dv = np.asarray(Image.open(run / "thumbnail_youtube_16x9.jpg").convert("L").resize((192, 108)),
                            dtype=float).ravel()
            best = max(((float(np.corrcoef(dv, np.asarray(ImageOps.fit(Image.open(c).convert("L"), (192, 108)),
                                                          dtype=float).ravel())[0, 1]), c) for c in photos),
                       key=lambda x: x[0])
            hero, how = best[1], f"best match to the delivered thumbnail (r {best[0]:.2f})"
        if hero is None and photos:
            hero, how = photos[0], "first photo of the topic folder"
        if hero is None:
            out.append(("dev", run.name, None, "no photo", payload))
            continue
        out.append(("dev", run.name, Path(hero), how, payload))
    return out


def sweep(code: Path, dev: Path, out: Path) -> list[dict]:
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    socket.create_connection = _offline
    socket.socket.connect = _offline
    os.chdir(code)
    sys.path.insert(0, str(code))
    sys.path.insert(0, str(code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import logging
    logging.disable(logging.WARNING)
    import fixtures as fx

    from src import youtube_thumbnail
    from src.visual_engine import VisualEngine, documentary_cover_copy
    seen: dict = {}
    orig = youtube_thumbnail.render

    def wrapped(*a, **k):
        im, plates = orig(*a, **k)
        seen["plates"] = [(n, p.text) for n, p in plates]
        seen["meta"] = {key: v for key, v in (k.get("meta") or {}).items()
                        if key.startswith("headline") or key in ("figure_withheld", "tension_from")}
        return im, plates
    youtube_thumbnail.render = wrapped
    rows = []
    out.mkdir(parents=True, exist_ok=True)
    for src, name, hero, how, payload in _cases(code, dev, fx):
        row = {"src": src, "case": name, "hero_source": how}
        copy = documentary_cover_copy(json.loads(json.dumps(payload)))
        row["copy"] = {k: copy.get(k) for k in ("eyebrow", "line1", "line2", "tagline")}
        if hero is None:
            row["error"] = "no photo"
            rows.append(row)
            continue
        seen.clear()
        target = out / src / f"{name}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            VisualEngine().generate_youtube_thumbnail(Path(hero), json.loads(json.dumps(payload)), target)
        except Exception as e:  # noqa: BLE001  one failed case is recorded, the rest render
            row["error"] = f"{type(e).__name__}: {e}"[:300]
            rows.append(row)
            continue
        lines = [t for n, t in seen["plates"] if n in HEADLINE]
        row.update(headline=lines, meta=seen["meta"], **judge(lines))
        l2 = " ".join(str(copy.get("line2") or "").upper().split())
        drawn = " ".join(t for n, t in seen["plates"] if n in ("title", "title_1", "title_2"))
        row["subtitle_kept"] = bool(l2) and l2.rstrip(MARKS + " ") in " ".join(drawn.upper().split())
        row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        rows.append(row)
        print(src, name, lines, file=sys.stderr, flush=True)
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                   newline="\n")
    return rows


def summary(rows: list[dict]) -> dict:
    s = {"cases": len(rows), "rendered": 0, "errors": 0, "with_headline": 0, "ends_on_mark": 0,
         "ends_on_fragment": 0, "subtitle_in_copy": 0, "subtitle_kept": 0, "by_source": {}}
    for r in rows:
        b = s["by_source"].setdefault(r["src"], {"cases": 0, "ends_on_mark": 0, "ends_on_fragment": 0,
                                                 "subtitle_kept": 0})
        b["cases"] += 1
        if "error" in r:
            s["errors"] += 1
            continue
        s["rendered"] += 1
        s["with_headline"] += bool(r["headline"])
        s["subtitle_in_copy"] += bool(str(r["copy"].get("line2") or "").strip())
        for k in ("ends_on_mark", "ends_on_fragment", "subtitle_kept"):
            s[k] += bool(r[k])
            b[k] += bool(r[k])
    return s


def compare(base: Path, fix: Path, out: Path) -> dict:
    import numpy as np
    from PIL import Image
    b_rows, f_rows = (json.loads((p / "rows.json").read_text(encoding="utf-8")) for p in (base, fix))
    changed, identical, differ = [], 0, []
    for b, f in zip(b_rows, f_rows):
        assert (b["src"], b["case"]) == (f["src"], f["case"])
        if "error" in b or "error" in f:
            continue
        defect = b["ends_on_mark"] or b["ends_on_fragment"]
        pa, pb = (np.asarray(Image.open(p / b["src"] / f"{b['case']}.jpg").convert("RGB")) for p in (base, fix))
        same = b["sha256"] == f["sha256"] and pa.shape == pb.shape and bool((pa == pb).all())
        if b["headline"] != f["headline"] or defect:
            changed.append({"src": b["src"], "case": b["case"], "copy": b["copy"], "before": b["headline"],
                            "after": f["headline"], "before_meta": b["meta"], "after_meta": f["meta"],
                            "pixels_identical": same})
        elif same:
            identical += 1
        else:
            differ.append(f"{b['src']}/{b['case']}")
    res = {"before": summary(b_rows), "after": summary(f_rows),
           "unchanged_headline_cases": identical + len(differ), "unchanged_headline_pixel_identical": identical,
           "unchanged_headline_differ": differ, "changed": changed}
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("before.json", res["before"]), ("after.json", res["after"]), ("comparison.json", res)):
        (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return res


def main(argv: list[str]) -> int:
    if argv[:1] == ["sweep"]:
        sweep(Path(argv[1]).resolve(), Path(argv[2]).resolve(), Path(argv[3]).resolve())
    elif argv[:1] == ["compare"]:
        res = compare(*(Path(a).resolve() for a in argv[1:4]))
        print(json.dumps({k: v for k, v in res.items() if k != "changed"}, indent=1))
    else:
        import tempfile
        code = Path(argv[0]).resolve() if argv else HERE.parents[3]
        rows = sweep(code, _main_checkout(), Path(tempfile.mkdtemp(prefix="qf143-")))
        print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
