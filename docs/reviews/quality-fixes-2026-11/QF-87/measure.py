"""QF-87 acceptance metrics: Bebas removed, one Montserrat source, true small capitals, the grid masthead.

    python measure.py <repository root>

Runs on this commit and on its base alike (it reads only what both trees have) and prints JSON; it writes nothing
outside a temporary folder (the app root it points the code at, with a copy of fonts/). Inputs, all committed:
- fonts/ and fonts/license_evidence.json; src/*.py (references to a removed face);
- the freeze panel's caption fixtures, docs/reviews/freeze-panel-2026-11-04/fixtures/runs/*/words.json (the seven
  9:16 dev-run narrations with the catalogue's emphasis words, and the long Richat documentary for 16:9), built into
  caption files as the pipeline builds them;
- the catalogue's five real cover copies (fixtures.json "covers"), drawn in the three grid-delivered styles on a
  flat ground (the masthead's grid size depends on its type size, not on the photograph);
- "Königsberg" (the Amber Room run's narration) set in Anton and Montserrat with and without smcp;
- one native libass frame each of "Königsberg" written precomposed and decomposed (ffmpeg).

The acceptance: fonts/ holds only registered files (typography.bundled_font_files), no Bebas and one Montserrat
file, every file with one licence record; no caption scales a capital down (\\fscx under 100); the 9:16 captions
keep the narration's case and every word; smcp and default forms of "Königsberg" draw different bounds with the
accent drawn; no grid-delivered cover and no hook frame (the Reel's first frame, the design-card tests' signed Richat
claim on three flat grounds) draws a masthead under 9 px cap height at 124 px wide.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRID_FLOOR = 9.0        # px of cap height at the 124 px tile (the row's rule)
STYLES = ("minimal", "badge", "depth_sandwich")
SMALL_SCALE = re.compile(r"\\fsc[xy](\d+)")


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


def _fonts(root: Path) -> dict:
    from src import typography
    files = sorted(p.name for p in (root / "fonts").glob("*.ttf"))
    registry = list(typography.bundled_font_files())
    evidence = json.loads((root / "fonts" / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]
    rows = [r["file"] for r in evidence]
    src_refs = sum(len(re.findall(r"Bebas", p.read_text(encoding="utf-8"))) for p in sorted((root / "src").glob("*.py")))
    return {"files": files, "unregistered_files": [f for f in files if f not in registry],
            "bebas_files": sum("Bebas" in f for f in files),
            "montserrat_files": sum(f.startswith("Montserrat") for f in files),
            "evidence_rows": len(rows), "evidence_without_file": [r for r in rows if r not in files],
            "files_without_evidence": [f for f in files if f not in rows],
            "bebas_references_in_src": src_refs}


def _plain(event_text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", event_text)


def _captions(root: Path, out: Path) -> dict:
    from src import safe_zones
    from src import video_composer as vc
    from src.video_qa import caption_pills
    fx = root / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
    catalogue = json.loads((fx / "fixtures.json").read_text(encoding="utf-8"))
    emphasis = {Path(c["run"]).name: c.get("emphasis_words") or []
                for c in catalogue["designs"]["captions"]["cases"]}
    band_top = safe_zones.caption_band_top(1080, 1920)
    safe_foot = safe_zones.safe_box("reel", 1080, 1920)[3]
    runs, wide = [], {}
    for words_file in sorted((fx / "runs").glob("run_*/words.json")):
        run = words_file.parent.name
        words = json.loads(words_file.read_text(encoding="utf-8"))
        original = json.dumps(words, ensure_ascii=False)
        composer = vc.VideoComposer()
        if run.endswith("_long"):
            ass = composer.build_subtitles(words, out / f"{run}_wide.ass", width=1920, height=1080,
                                           emphasis=emphasis.get(run))
            text = ass.read_text(encoding="utf-8")
            style = next(ln for ln in text.splitlines() if ln.startswith("Style: CapText,")).split(",")
            wide = {"run": run, "family": style[1], "bold": style[7],
                    "embedded_face": "[Fonts]" in text,
                    "scaled_tags": sum(int(v) < 100 for v in SMALL_SCALE.findall(text))}
            continue
        ass = composer.build_subtitles(words, out / f"{run}.ass", emphasis=emphasis.get(run))
        text = ass.read_text(encoding="utf-8")
        style = next(ln for ln in text.splitlines() if ln.startswith("Style: CapText,")).split(",")
        events = [ln.split(",", 9) for ln in text.splitlines() if ln.startswith("Dialogue:")]
        # a burst's last word is drawn without its closing comma or full stop (by design): compare without them
        tokens = {t.rstrip(",.;:") for e in events if e[3] == "CapText" for t in _plain(e[9]).split()}
        phrases = vc.caption_phrases(words, emphasis.get(run))
        two_line = [p for p in phrases if vc.reel_caption_lines(p)]
        plates = caption_pills(ass)
        narration = [unicodedata.normalize("NFC", str(w["word"])).rstrip(",.;:") for w in words]
        runs.append({
            "run": run, "family": style[1], "bold": style[7], "embedded_face": "[Fonts]" in text,
            "bursts": len(phrases), "two_line_bursts": len(two_line),
            "one_word_bursts": sum(len(p) == 1 for p in phrases),
            "sizes": sorted({vc._caption_fit_size(p) for p in phrases}),
            "scaled_tags": sum(int(v) < 100 for v in SMALL_SCALE.findall(text)),
            "words_unchanged": json.dumps(words, ensure_ascii=False) == original
                               and [w for p in phrases for w in p] == words,
            "case_kept": sum(w in tokens for w in narration),
            "words": len(words),
            "plates": len(plates),
            "plates_outside_band": sum(p[1] < band_top or p[3] > safe_foot for p in plates),
            "ass_sha": hashlib.sha256(re.sub(r"\[Fonts\].*?\n\n", "", text, flags=re.DOTALL).encode()).hexdigest()[:16],
        })
    total = {k: sum(r[k] for r in runs) for k in ("bursts", "two_line_bursts", "one_word_bursts", "scaled_tags",
                                                   "case_kept", "words", "plates", "plates_outside_band")}
    total["runs"] = len(runs)
    total["words_unchanged"] = all(r["words_unchanged"] for r in runs)
    return {"total": total, "runs": runs, "wide": wide}


def _konigsberg() -> dict:
    import numpy as np
    from PIL import Image, ImageDraw

    from src import typography
    out = {}
    for family in ("Anton", "Montserrat"):
        font = typography.get_font(family, 200)

        def ink(text, features, font=font):
            im = Image.new("L", (2400, 400))
            ImageDraw.Draw(im).text((40, 300), text, font=font, fill=255, anchor="ls", features=features)
            return np.asarray(im) > 0

        def box(a):
            xs, ys = np.nonzero(a.any(0))[0], np.nonzero(a.any(1))[0]
            return [int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1]
        plain, small = ink("Königsberg", ["kern"]), ink("Königsberg", ["kern", "smcp"])
        out[family] = {"default": box(plain), "smcp": box(small), "bounds_differ": box(plain) != box(small),
                       "accent_px_default": int((plain & ~ink("Konigsberg", ["kern"])).sum()),
                       "accent_px_smcp": int((small & ~ink("Konigsberg", ["kern", "smcp"])).sum())}
    try:
        from src.font_assets import CAPTION_FAMILY
        from src.font_engine import shape
        derived = typography.get_font(CAPTION_FAMILY, 200)
        out["caption_face_is_anton_smcp"] = (shape("Königsberg", derived, features=("-kern",))
                                             == shape("Königsberg", typography.get_font("Anton", 200),
                                                      features=("-kern", "smcp")))
    except ImportError:
        out["caption_face_is_anton_smcp"] = None
    return out


def _native_accents(app: Path, out: Path) -> dict:
    """The same word precomposed and decomposed, drawn by libass from the 9:16 caption file: identical ink?"""
    import numpy as np
    from PIL import Image

    from src import video_composer as vc
    frames = []
    for i, word in enumerate(("Königsberg", "Ko\u0308nigsberg")):
        ass = vc.VideoComposer().build_subtitles([{"word": word, "start": 0.1, "dur": 1.2}], out / f"accent{i}.ass")
        png = out / f"accent{i}.png"
        def esc(p: Path) -> str:
            return str(p.resolve()).replace("\\", "/").replace(":", "\\:")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202020:s=1080x1920:r=30",
                        "-vf", f"setpts=PTS+0.6/TB,subtitles='{esc(ass)}':fontsdir='{esc(app / 'fonts')}'",
                        "-frames:v", "1", "-threads", "1", str(png)], check=True, capture_output=True, timeout=300)
        frames.append(np.asarray(Image.open(png).convert("L")).astype(int))
    diff = np.abs(frames[0] - frames[1]) > 8
    return {"identical": bool(not diff.any()), "differing_px": int(diff.sum())}


def _masthead() -> dict:
    from PIL import Image

    from src import cover_design
    from src.still_layout import Layout
    fx = Path(__file__).resolve().parents[2] / "freeze-panel-2026-11-04" / "fixtures" / "fixtures.json"
    cases = json.loads(fx.read_text(encoding="utf-8"))["designs"]["covers"]["cases"]
    rows = []
    for case in cases:
        for style in STYLES:
            lay = Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
            photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
            cover_design.render_cover(photo.resize((1080, 1920)), case["copy"], style, lay=lay, source=photo)
            brand = lay.meta["grid_proof"]["elements"].get("brand")
            rows.append({"case": Path(case["run"]).name.split("_")[1], "style": style,
                         "drawn": brand is not None, "cap_124": brand["cap_124"] if brand else None,
                         "grid_fails": [f for f in lay.meta["grid_proof"].get("fails", []) if "brand" in f]})
    drawn = [r for r in rows if r["drawn"]]
    return {"covers": len(rows), "masthead_drawn": len(drawn),
            "drawn_under_9px": sum(r["cap_124"] < GRID_FLOOR for r in drawn),
            "min_cap_124": min((r["cap_124"] for r in drawn), default=None),
            "grid_floor": getattr(cover_design, "GRID_HOOK", {}).get("brand"), "rows": rows}


def _hook(root: Path, tmp: Path) -> dict:
    """The hook card's frame 0 (the Reel's first frame) for the design-card tests' signed Richat claim, on three flat
    grounds (dark, mid, bright), signed in this process with the tests' throwaway key: its masthead at the tile."""
    from PIL import Image

    from src import config
    sys.path.insert(0, str(root / "tests"))
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key, this process only
    import test_design_cards_2026_11_03 as t
    rows = []
    for ground in ((40, 35, 30), (120, 110, 95), (235, 238, 242)):
        photo = tmp / f"hook_{ground[0]}.png"
        Image.new("RGB", (1600, 2400), ground).save(photo)
        plan = t.dc.plan_hook_frame(t.hook(photo, eyebrow="The Richat ring"), t.SHEET)
        brand = plan.meta["grid_proof"]["elements"].get("brand")
        rows.append({"ground": list(ground), "drawn": brand is not None,
                     "cap_124": brand["cap_124"] if brand else None, "position": plan.meta["anchors"]["position"]})
    drawn = [r for r in rows if r["drawn"]]
    return {"frames": len(rows), "masthead_drawn": len(drawn),
            "drawn_under_9px": sum(r["cap_124"] < GRID_FLOOR for r in drawn), "rows": rows}


def main(root: Path) -> None:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="qf87-measure-") as tmp:
        app = Path(tmp) / "app"
        shutil.copytree(root / "fonts", app / "fonts")
        (app / "data").mkdir(parents=True)
        shutil.copyfile(root / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        out = Path(tmp) / "out"
        out.mkdir()
        os.environ.update(RELIC_APP_ROOT=str(app), FONT_SIZE="80", PYTHONIOENCODING="utf-8")
        socket.create_connection = _offline
        socket.socket.connect = _offline
        sys.path.insert(0, str(root))
        fonts = _fonts(root)
        captions = _captions(root, out)
        konigsberg = _konigsberg()
        native = _native_accents(app, out)
        masthead = _masthead()
        hook = _hook(root, Path(tmp))
    c = captions["total"]
    result = {
        "schema": 1,
        "source_sha256": {n: hashlib.sha256((root / n).read_bytes()).hexdigest()[:16] for n in (
            "src/video_composer.py", "src/typography.py", "src/font_engine.py", "src/cover_design.py")},
        "fonts": fonts, "captions": captions, "konigsberg": konigsberg, "native_accents": native,
        "masthead": masthead, "hook_masthead": hook,
    }
    result["acceptance_met"] = bool(
        not fonts["unregistered_files"] and fonts["bebas_files"] == 0 and fonts["montserrat_files"] == 1
        and not fonts["evidence_without_file"] and not fonts["files_without_evidence"]
        and c["scaled_tags"] == 0 and captions["wide"]["scaled_tags"] == 0 and c["words_unchanged"]
        and c["case_kept"] == c["words"] and c["plates_outside_band"] == 0
        and all(konigsberg[f]["bounds_differ"] and konigsberg[f]["accent_px_smcp"] > 0
                and konigsberg[f]["accent_px_default"] > 0 for f in ("Anton", "Montserrat"))
        and native["identical"] and masthead["drawn_under_9px"] == 0
        and hook["drawn_under_9px"] == 0)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py <repository root>")
    main(Path(sys.argv[1]))
