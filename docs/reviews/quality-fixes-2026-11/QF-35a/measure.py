"""QF-35a acceptance metrics: the frozen designs read their palette, faces and masthead from one brand configuration.

    python measure.py <repository root>

Runs on this commit and on its base alike (the scan and the default renders read only what both trees have) and
prints JSON; it writes nothing outside a temporary folder. Inputs, all committed:
- src/*.py of the frozen design modules, scanned by tests/test_qf35a_role_tokens.py's scan (loaded from beside this
  folder and pointed at <root>): colour, font-family and brand-string literals outside the brand configuration, and
  role reads that bind at import;
- the freeze panel's caption narrations (docs/reviews/freeze-panel-2026-11-04/fixtures/runs/*/words.json, with the
  catalogue's emphasis words), built into caption files;
- the committed Richat still (fixtures/stills) as the ground of the end card (the catalogue's share lines), the
  hook, chapter and myth cards (the card tests' committed fields, signed with the tests' throwaway key here only)
  and the number cards (the Richat run's own stat cards, tests/test_qf35a_role_tokens.py does not need them);
- the cross-section test spec (tests/test_diagrams_2026_11_02.py SECTION).

Each surface is hashed over its pixels (every frame) or its caption file. With the default brand the hashes must be
equal on the base and here (pixel identity: compare before.json and after.json). Where the tree has the brand
configuration, every surface is drawn again in the test brand (red and blue swapped in every palette colour,
Montserrat in the display role, another masthead and end-card lines): it must draw (no error), differ from the
default, carry none of today's caption colours, put the new masthead and face where the old ones were, and keep
today's gold (#F2B630) off the end card. A card whose words do not fit in the wider display face refuses (a card
fails closed, never cut): it is drawn once more in the test brand with today's display face kept, and must then
carry the test brand's colours. `elapsed_seconds_default_renders` times the default renders (runtime cost,
wall clock).
"""
import hashlib
import importlib.util
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
CARD_FIELD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key (tests/test_freeze_cards_2026_11_04)
TODAY_GOLD = (242, 182, 48)
TODAY_ASS = ("&H00E2EDF3&", "&H0030B6F2&", "&H00A3B1B8&", "&H20120E0B&")   # bone, gold, stone_lt, the plate


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


def _hash_frames(frames) -> dict:
    h, n, last = hashlib.sha256(), 0, None
    for im in frames:
        rgb = im.convert("RGB")
        h.update(f"{rgb.width}x{rgb.height};".encode())
        h.update(rgb.tobytes())
        n, last = n + 1, rgb
    return {"sha256": h.hexdigest(), "frames": n, "_last": last}


def _near(im, rgb, tol=6) -> int:
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    return int((np.abs(a - np.array(rgb)).max(axis=2) <= tol).sum())


def _scan(root: Path) -> dict:
    spec = importlib.util.spec_from_file_location("qf35a_scan", HERE.parents[3] / "tests" / "test_qf35a_role_tokens.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    scan.ROOT = root
    out = {}
    for module in scan.FROZEN:
        lit = scan.literal_findings(module)
        out[module] = {"colour": sum(f[0] == "colour" for f in lit), "font": sum(f[0] == "font" for f in lit),
                       "brand_string": sum(f[0] == "brand string" for f in lit),
                       "import_time": len(scan.import_time_findings(module))}
    out["total"] = {k: sum(v[k] for v in out.values()) for k in ("colour", "font", "brand_string", "import_time")}
    out["allowed"] = len(scan.ALLOW)
    return out


def _surfaces(root: Path, tmp: Path, cards_only: set | None = None) -> dict:
    """{name: {"sha256", "frames", "_last", plus checks}} for every committed-input surface, drawn now (with
    `cards_only`, those cards alone)."""
    from PIL import Image

    from src import config, end_card, graphics
    from src import design_cards as dc
    from src import video_composer as vc
    config.FACT_RECORD_KEY = CARD_FIELD_KEY
    from tests import test_freeze_cards_2026_11_04 as fc
    from tests.test_diagrams_2026_11_02 import SECTION
    catalogue = json.loads((root / FIX / "fixtures.json").read_text(encoding="utf-8"))["designs"]
    still = root / FIX / "stills" / "richat_structure_sahara_eye_01_commons_960.jpg"
    out: dict = {}
    every = cards_only is None
    # captions: the caption file carries every colour and face the burn-in draws
    emphasis = {Path(c["run"]).name: c.get("emphasis_words") or [] for c in catalogue["captions"]["cases"]}
    for words_file in sorted((root / FIX / "runs").glob("run_*/words.json")) if every else ():
        run = words_file.parent.name
        words = json.loads(words_file.read_text(encoding="utf-8"))
        kw = {"width": 1920, "height": 1080} if run.endswith("_long") else {}
        ass = vc.VideoComposer().build_subtitles(words, tmp / f"{run}.ass", emphasis=emphasis.get(run), **kw)
        text = ass.read_text(encoding="utf-8")
        style = next(ln for ln in text.splitlines() if ln.startswith("Style: CapText,")).split(",")
        out[f"captions {run}"] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "frames": 0, "_last": None,
                                  "face": style[1], "today_colours": sum(c in text for c in TODAY_ASS)}
    # end card and loop bridge on the committed still, with the catalogue's share lines
    ground = Image.open(still).convert("RGB")
    ground = ground.resize((1080, round(ground.height * 1080 / ground.width))).crop((0, 0, 1080, 1920)) \
        if ground.height * 1080 / ground.width >= 1920 else ground.resize((1080, 1920))
    for i, case in enumerate(c for c in catalogue["endcard"]["cases"] if every and c.get("format") == "9:16"):
        r = _hash_frames(end_card.frames(ground, case["share_line"], {}))
        r["masthead"] = end_card.layout(case["share_line"])["masthead"]["text"]
        r["today_gold_px"] = _near(r["_last"], TODAY_GOLD)
        out[f"endcard {i}"] = r
        r = _hash_frames(end_card.loop_bridge(ground, ground, case["share_line"], {}, line=False, follow=True))
        r["today_gold_px"] = 0
        out[f"bridge_follow {i}"] = r
        if i >= 1:
            break
    # cards: the tests' committed fields on the committed still
    richat = fc.real_sheet("richat_structure_sahara_eye")
    # the long titles and the long myth (the widest fields the card tests hold), then a title and a myth short
    # enough for a wider display face
    makers = {"myth_richat": lambda size: fc.richat_myth(richat, photo=still, size=size),
              "chapter_act1": lambda size: fc.chapter_plan(size, 1, fc.ACTS, fc.RICHAT_ACTS[0], photo=still),
              "chapter_act4": lambda size: fc.chapter_plan(size, 4, fc.ACTS, fc.RICHAT_ACTS[3], photo=still),
              "chapter_act5": lambda size: fc.chapter_plan(size, 5, fc.ACTS, fc.RICHAT_ACTS[4], photo=still),
              "myth_short": lambda size: fc.plan_myth(fc.SHORT, size=size, photo=still)}
    for name, make in makers.items():
        for size, tag in ((fc.TALL, "916"), (fc.WIDE, "169")):
            if not every and f"card {name} {tag}" not in cards_only:
                continue
            try:
                plan = make(size)
            except dc.CardRefused as e:
                out[f"card {name} {tag}"] = {"refused": str(e)[:160], "sha256": None, "frames": 0, "_last": None}
                continue
            r = _hash_frames(dc.frame_at(plan, k) for k in range(plan.frames))
            r["faces"] = sorted({e.face for e in plan.elements if e.kind == "text"})
            r["today_gold_fills"] = sum(tuple(e.fill) == TODAY_GOLD for e in plan.elements)
            out[f"card {name} {tag}"] = r
    # graphics: the cross-section test spec, hashed where it is encoded
    kept: dict = {}

    def encode(frames, path, n, **_k):
        kept[Path(path).name] = _hash_frames(frames)
        return Path(path)
    graphics._encode = encode
    for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")) if every else ():
        graphics.render_cross_section(SECTION, tmp / f"section_{tag}.mp4", width=w, height=h)
        r = kept.pop(f"section_{tag}.mp4")
        r["today_gold_px"] = _near(r["_last"], TODAY_GOLD)
        out[f"cross_section {tag}"] = r
    return out


def main(root: Path) -> None:
    root = root.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(root))
    scan = _scan(root)
    from src import brand
    with tempfile.TemporaryDirectory(prefix="qf35a-measure-") as tmp:
        t0 = time.perf_counter()
        default = _surfaces(root, Path(tmp))
        seconds = round(time.perf_counter() - t0, 1)
        second = palette_only = None
        display_faces: list = []
        if hasattr(brand, "use"):
            spec = importlib.util.spec_from_file_location("qf35a_test", root / "tests" / "test_qf35a_role_tokens.py")
            test = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(test)
            display_faces = [brand.DEFAULT_BRAND.fonts["display"], test._second_brand().fonts["display"]]
            with brand.use(test._second_brand()):
                second = _surfaces(root, Path(tmp))
            # the same brand with today's display face: the cards a wider display face cannot fit, in its colours
            refused = {k for k, v in second.items() if v.get("sha256") is None}
            with brand.use(test._second_brand().derive("Second test brand, display face kept",
                                                       fonts={"display": display_faces[0]})):
                palette_only = _surfaces(root, Path(tmp), cards_only=refused)
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_last"} for k, v in default.items()}
    result = {"schema": 1, "scan": scan, "default": clean, "elapsed_seconds_default_renders": seconds}
    if second is not None:
        def rows_of(drawn: dict) -> dict:
            rows = {}
            for k, v in drawn.items():
                row = {"drawn": v.get("sha256") is not None,
                       "differs_from_default": v.get("sha256") != default[k]["sha256"]}
                for key in ("face", "today_colours", "masthead", "today_gold_px", "faces", "today_gold_fills",
                            "refused"):
                    if key in v:
                        row[key] = v[key]
                rows[k] = row
            return rows

        def clean_row(r: dict) -> bool:
            return (r["differs_from_default"] and r.get("today_colours", 0) == 0 and r.get("today_gold_px", 0) == 0
                    and r.get("today_gold_fills", 0) == 0 and r.get("masthead", "SECOND TEST BRAND") == "SECOND TEST BRAND")
        rows, kept = rows_of(second), rows_of(palette_only)
        result["second_brand"], result["second_brand_display_kept"] = rows, kept
        # every surface draws in the second brand, with none of today's colours, its masthead and its display face;
        # a card that refuses (CardRefused, its reason kept: Montserrat is wider than the condensed Anton, and a card
        # fails closed, never cut) must draw in the second brand's colours once today's display face is kept
        result["second_brand_refused_for_fit"] = sorted(k for k, r in rows.items() if not r["drawn"])
        result["second_brand_cards_in_its_display_face"] = sorted(
            k for k, r in rows.items() if r["drawn"] and display_faces[1] in r.get("faces", [])
            and default[k].get("faces") and display_faces[0] in default[k]["faces"])
        result["second_brand_ok"] = bool(result["second_brand_cards_in_its_display_face"]) and all(
            (r["drawn"] and clean_row(r) and display_faces[0] not in r.get("faces", []))
            or (not r["drawn"] and r.get("refused") and k in kept and kept[k]["drawn"] and clean_row(kept[k]))
            for k, r in rows.items())
    result["acceptance_met"] = bool(sum(scan["total"].values()) == 0 and result.get("second_brand_ok"))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py <repository root>")
    main(Path(sys.argv[1]))
