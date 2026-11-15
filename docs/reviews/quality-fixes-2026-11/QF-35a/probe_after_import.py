"""QF-35a port: a brand configured AFTER every design module is imported and has drawn once takes effect at once.

    python probe_after_import.py <tree> <dev checkout holding output/run_*> <out.json>

Every frozen design module (and the colour-vision gate) is imported and draws once in the default brand: the end card
over a real run's last frame (the catalogue's first end-card case), the caption files of a real run's narration in both
formats with the AI-reconstruction badge on screen, the dense timeline's plan, the art direction's fallback accent, the
card measure. Then brand.configure(<third test brand>) (render_surfaces.py third_brand: every channel of every colour
moved, greys too; Cinzel in the display, data and 16:9 caption roles; another masthead) and the same calls again:
each must follow the new brand with none of today's colours or faces left; then configure(None) and the calls again:
each must equal the first draw exactly. Prints and writes JSON; exits 1 on any failed check.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"


def main(tree: Path, dev: Path, out: Path) -> int:
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / FIX))
    import fixtures as fx
    import numpy as np
    from PIL import Image

    from src import (  # noqa: F401  every frozen design module imported before any brand is configured
        art_direction,
        brand,
        callouts,
        colour_vision,
        cover_design,
        design_cards,
        end_card,
        graphics,
        multi_format,
        then_now,
        timeline_dense,
        video_composer,
        youtube_thumbnail,
    )
    spec = importlib.util.spec_from_file_location("render_surfaces", Path(__file__).with_name("render_surfaces.py"))
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)

    case = fx.load("endcard")[0]
    last_src = case.get("last_frame", {}).get("from") or case.get("last")
    last = (Image.open(fx.frame(last_src, last=True)) if str(last_src).endswith(".mp4")
            else Image.open(fx.path(last_src))).convert("RGB")
    cap = next(c for c in fx.load("captions") if c.get("format") != "16:9")
    words = json.loads(Path(fx.path(cap["words"])).read_text(encoding="utf-8"))[:60]
    events = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
              {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
              {"year": 1523, "label": "The knights sail for Malta"},
              {"year": 1912, "label": "Italy takes the Dodecanese"},
              {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]
    work = out.parent / "probe-work"
    work.mkdir(parents=True, exist_ok=True)

    def draw(tag: str) -> dict:
        card = list(end_card.frames(last, case["share_line"], {}))[-1]
        card.save(work / f"endcard_{tag}.png")
        ass = {}
        for fmt, kw in (("916", {}), ("169", {"width": 1920, "height": 1080})):
            p = video_composer.VideoComposer().build_subtitles(words, work / f"cap_{fmt}_{tag}.ass",
                                                               emphasis=cap.get("emphasis_words") or None,
                                                               reconstruction_intervals=[(0.0, 4.0)], **kw)
            ass[fmt] = p.read_text(encoding="utf-8")
        plan = timeline_dense.plan(events, 1080, 1920)
        return {"card": card, "ass": ass, "kicker_px": then_now.KICKER_PX["landscape"],
                "band_fall": then_now.BAND_FALL,
                "dense_year_face": plan["year_font"].getname()[0] if plan else None,
                "accent": tuple(art_direction.palette()["accent"]),
                "gold_role": colour_vision.ROLES["gold"], "cap120": design_cards._cap(120),
                "masthead": end_card.layout(case["share_line"])["masthead"]["text"]
                if "masthead" in end_card.layout(case["share_line"]) else None}

    def style(text: str, name: str) -> list[str]:
        return next(line for line in text.splitlines() if line.startswith(f"Style: {name},")).split(",")

    def digest(d: dict) -> str:
        h = hashlib.sha256(d["card"].tobytes())
        for fmt in sorted(d["ass"]):
            h.update(d["ass"][fmt].encode())
        h.update(repr([d[k] for k in ("dense_year_face", "accent", "gold_role", "cap120", "masthead", "kicker_px",
                                      "band_fall")]).encode())
        return h.hexdigest()

    import subprocess
    fresh = subprocess.run([sys.executable, "-c", ("import sys, importlib.util; sys.path.insert(0, sys.argv[1]); "
                            "from src import brand; spec = importlib.util.spec_from_file_location('rs', sys.argv[2]); "
                            "rs = importlib.util.module_from_spec(spec); spec.loader.exec_module(rs); "
                            "brand.configure(rs.third_brand(brand)); from src import then_now; "
                            "print(then_now.KICKER_PX['landscape'], then_now.BAND_FALL)"),
                            str(tree), str(Path(__file__).with_name("render_surfaces.py"))],
                           capture_output=True, text=True, check=True, env={**os.environ}, timeout=600)
    fresh_then_now = tuple(int(v) for v in fresh.stdout.split()[-2:])
    first = draw("default")
    assert brand.active() is brand.DEFAULT_BRAND
    third = rs.third_brand(brand)
    previous = brand.configure(third)
    swapped = draw("third")
    brand.configure(None)
    again = draw("default_again")

    today = {r: brand.DEFAULT_BRAND.colour(r) for r, v in brand.DEFAULT_BRAND.palette.items() if not isinstance(v, str)}
    a = np.asarray(swapped["card"])
    packed = (a[..., 0].astype(np.uint32) << 16) | (a[..., 1].astype(np.uint32) << 8) | a[..., 2]
    values, n = np.unique(packed, return_counts=True)
    have = dict(zip(values.tolist(), n.tolist()))
    b = np.asarray(first["card"])
    packed_b = (b[..., 0].astype(np.uint32) << 16) | (b[..., 1].astype(np.uint32) << 8) | b[..., 2]
    values_b, n_b = np.unique(packed_b, return_counts=True)
    have_b = dict(zip(values_b.tolist(), n_b.tolist()))
    key = {r: (c[0] << 16) | (c[1] << 8) | c[2] for r, c in today.items()}
    old_in_third = {r: have.get(k, 0) for r, k in key.items() if have.get(k, 0)}
    old_in_default = {r: have_b.get(k, 0) for r, k in key.items() if have_b.get(k, 0)}
    # the caption colours: bone (spoken), gold (key terms), stone (upcoming), the INK plate; present ones are
    # required in the third brand's files, today's must be absent from them
    plate = int(video_composer._PLATE_ASS.strip("&Hh")[:2], 16)
    today_ass = {brand.ass_colour(today[r]) for r in ("BONE", "GOLD", "STONE_LT")} | {
        brand.ass_colour(today["INK"], plate)}
    third_ass = {brand.ass_colour(third.colour(r)) for r in ("BONE", "GOLD")} | {
        brand.ass_colour(third.colour("INK"), plate)}
    checks = {
        "configure replaced the default": previous is brand.DEFAULT_BRAND,
        "end card changed": swapped["card"].tobytes() != first["card"].tobytes(),
        "end card holds no default mark colour exactly where the default frame did not (excess px)":
            sum(max(0, old_in_third.get(r, 0) - old_in_default.get(r, 0)) for r in key if max(today[r]) >= 64) == 0,
        "end card masthead is the third brand's": swapped["masthead"] in (None, "THIRD TEST BRAND"),
        "captions: no default text colour, the third brand's present (both formats)": all(
            not any(c in swapped["ass"][f] for c in today_ass) and all(c in swapped["ass"][f] for c in third_ass)
            for f in ("916", "169")),
        "16:9 captions drawn in RR Serif (Cinzel 700), embedded": style(swapped["ass"]["169"], "CapText")[1] == "RR Serif"
            and "fontname: RRSerif.ttf" in swapped["ass"]["169"],
        "badge in Cinzel at its measured 600 (RR Serif SemiBold, embedded; round 2), both formats": all(
            style(swapped["ass"][f], "ReconBadge")[1] == "RR Serif SemiBold" and "fontname: RRSerifSemiBold.ttf"
            in swapped["ass"][f] for f in ("916", "169")),
        "then-and-now kicker size and glow fall measured in the third brand's faces (round 2), as a fresh import "
        "under it gives": (swapped["kicker_px"], swapped["band_fall"]) == fresh_then_now,
        "dense timeline years in Cinzel": swapped["dense_year_face"] == "Cinzel",
        "fallback accent is the third brand's": swapped["accent"] == third.colour("ACCENT"),
        "colour-vision gold is the third brand's": swapped["gold_role"] == third.colour("GOLD"),
        "card display measure changed": swapped["cap120"] != first["cap120"],
        "configure(None) restores the default exactly": digest(again) == digest(first),
    }
    result = {
        "tree": str(tree), "end_card_case": case["topic"], "captions_case": cap["topic"],
        "default": {"digest": digest(first), "dense_year_face": first["dense_year_face"],
                    "badge_face": style(first["ass"]["169"], "ReconBadge")[1],
                    "caption_169_face": style(first["ass"]["169"], "CapText")[1]},
        "fresh_import_under_third_then_now": fresh_then_now,
        "then_now_default": [first["kicker_px"], first["band_fall"]],
        "third": {"digest": digest(swapped), "then_now": [swapped["kicker_px"], swapped["band_fall"]], "dense_year_face": swapped["dense_year_face"],
                  "badge_face": style(swapped["ass"]["169"], "ReconBadge")[1],
                  "caption_169_face": style(swapped["ass"]["169"], "CapText")[1],
                  "default_colour_px_on_end_card": old_in_third, "same_in_default_end_card": old_in_default},
        "after_configure_none": {"digest": digest(again)},
        "checks": checks, "pass": all(checks.values()),
    }
    text = json.dumps(result, indent=1, sort_keys=True)
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: probe_after_import.py <tree> <dev checkout> <out.json>")
    raise SystemExit(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()))
