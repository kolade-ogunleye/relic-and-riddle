"""QF-21 acceptance measure: kerning pairs, per-font features and caption width parity, on the tree given.

    python docs/reviews/quality-fixes-2026-11/QF-21/measure.py <repository root>

Runs on the base and on the row's commit (it only calls functions both trees have). Inputs are committed:
real_strings.json beside this file (the copy real runs drew, extract_real_strings.py), the freeze-panel fixture
runs' words.json (docs/reviews/freeze-panel-2026-11-04/fixtures/runs) and fonts/. Prints JSON; writes nothing outside a
temporary folder it removes.

1. tracked copy (every real string, in the font, size and tracking its design sets it in): the line as the tree
   draws it (typography._draw_tracked) against an independent reference: Pillow's raqm layout of the whole run,
   each letter at its kerned place plus the tracking (OpenType features: kern, and case on a run without lower
   case, the QF-21 spec). Counted: pixel-identical lines, and how much wider than the kerned run the drawn ink is.
2. measure vs drawing: typography.tracked_width (what every layout fits with) against the drawn advance.
3. per-font features: each bundled file's GSUB/GPOS features (fontTools, read here) and whether the tree's
   default drawing applies case to capitals.
4. caption width parity: the ASS files the composer's own build_subtitles writes for every committed fixture run
   in both formats (bursts, plate widths, the 16:9 line, the reconstruction badge), hashed; and a decomposed accent
   against the composed spelling.
"""

import hashlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


def main(root: Path) -> None:
    root = root.resolve()
    os.environ.setdefault("FONT_SIZE", "80")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(root))
    import numpy as np
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, features

    from src import callouts as callouts_mod
    from src import cover_design, end_card, graphics, multi_format
    from src import typography as T
    from src import video_composer as vc
    assert features.check("raqm"), "raqm is required (FriBiDi on PATH)"

    here = Path(__file__).resolve().parent
    strings = json.loads((here / "real_strings.json").read_text(encoding="utf-8"))

    # design -> (family, weight, px, tracking in px at that px, upper-case the copy as the design does)
    def em(share, px):
        return round(share * px, 2)
    roles = {
        "cover": ("Montserrat", 800, cover_design.EYEBROW_PX, cover_design.EYEBROW_TRACK, True),
        "story": ("Montserrat", 800, 34, em(multi_format.KICKER_TRACK, 34), True),
        "carousel": ("Anton", None, 72, 0, True),
        "timeline": ("Montserrat", graphics.TALL_LABEL_WEIGHT, 34, em(graphics.TALL_LABEL_TRACK, 34), True),
        "stat_cards": ("Montserrat", 700, 40, 0, True),
        "scale": ("Montserrat", 800, 30, 3, True),
        "route": ("Montserrat", 700, 34, em(graphics.TALL_LABEL_TRACK, 34), True),
        "callouts": ("Montserrat", 700, callouts_mod.LABEL_PX["portrait"],
                     em(callouts_mod.LABEL_TRACK, callouts_mod.LABEL_PX["portrait"]), True),
        "map": ("Montserrat", 600, graphics.COUNTRY_PX, em(graphics.COUNTRY_TRACK, graphics.COUNTRY_PX), True),
        "chapter_card": ("Anton", None, 72, 0, True),
    }
    fixed = [("masthead", cover_design.BRAND_TEXT, "Cinzel", 700, cover_design.BRAND_PX, cover_design.BRAND_TRACK),
             ("end_card", end_card.FOLLOW, "Montserrat", 800, 40, em(end_card.FOLLOW_TRACK, 40))]
    cases = [(r["design"], r["run"], r["text"].upper(), *roles[r["design"]][:4]) for r in strings
             if r["design"] in roles]
    cases += [(d, "", t, f, w, px, tr) for d, t, f, w, px, tr in fixed]

    def spec(text):
        return ["kern"] if any(c.islower() for c in text) else ["kern", "case"]

    def reference(text, font, xy, track, size):
        im = Image.new("L", size)
        d = ImageDraw.Draw(im)
        feats = spec(text)
        for i, ch in enumerate(text):
            at = font.getlength(text[:i + 1], features=feats) - font.getlength(ch, features=feats)
            d.text((xy[0] + at + i * track, xy[1]), ch, font=font, fill=255, anchor="ls", features=feats)
        return im

    tracked, per_design = [], {}
    for design, run, text, family, weight, px, track in cases:
        font = T.get_font(family, px, weight=weight)
        text = T.drawable(text, font)
        if not text:
            continue
        size = (int(T.tracked_width(text, font, track) + 3 * px + 40), 3 * px)
        xy = (20, 2 * px)
        drawn = Image.new("L", size)
        T._draw_tracked(ImageDraw.Draw(drawn), xy, text, font, 255, "ls", track)
        ref = reference(text, font, xy, track, size)
        a, b = np.asarray(drawn), np.asarray(ref)
        db, rb = drawn.getbbox(), ref.getbbox()
        extra = (db[2] - db[0]) - (rb[2] - rb[0]) if db and rb else 0
        measured = T.tracked_width(text, font, track)
        kerned_advance = font.getlength(text, features=spec(text)) + track * (len(text) - 1)
        row = {"design": design, "run": run, "text": text, "font": f"{family} {weight or ''}".strip(), "px": px,
               "track": track, "pixel_identical": bool(np.array_equal(a, b)),
               "changed_px": int(np.count_nonzero(a != b)), "ink_wider_than_kerned_px": int(extra),
               "measure_minus_kerned_advance_px": round(measured - kerned_advance, 3)}
        tracked.append(row)
        s = per_design.setdefault(design, {"lines": 0, "identical": 0, "ink_wider_max_px": 0, "ink_wider_lines": 0})
        s["lines"] += 1
        s["identical"] += row["pixel_identical"]
        s["ink_wider_max_px"] = max(s["ink_wider_max_px"], abs(extra))
        s["ink_wider_lines"] += extra != 0

    fonts = {}
    for filename in T.bundled_font_files():
        with TTFont(str(root / "fonts" / filename)) as f:
            tags = sorted({r.FeatureTag for t in ("GSUB", "GPOS") if t in f for r in f[t].table.FeatureList.FeatureRecord})
        fonts[filename] = tags

    def caps_case_applied(family, weight):
        font = T.get_font(family, 120, weight=weight)
        text = "SAINT-MALO (AD 700) · X"
        out = []
        for feats in (None, ["kern", "case"]):
            im = Image.new("L", (1400, 240))
            kw = {} if feats is None else {"features": feats}
            ImageDraw.Draw(im).text((20, 180), text, font=font, fill=255, anchor="ls", **kw)
            out.append(np.asarray(im))
        return bool(np.array_equal(*out))

    feature_use = {f"{fam} {w or ''}".strip(): {"capitals_drawn_with_case": caps_case_applied(fam, w)}
                   for fam, w in (("Anton", None), ("Montserrat", 700))}

    sys.path.insert(0, str(root / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import fixtures as fx
    # the composer's own caption output (build_subtitles: the burst fit, the plates, the 16:9 line and the badge, as
    # compose_video calls it), for every committed fixture run in both formats; ASS files in a temporary folder
    ass_sha, digest = {}, hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="qf21-ass-") as tmp:
        for case in fx.load("captions"):
            words = json.loads(fx.run_file(case["run"], "words.json").read_text(encoding="utf-8"))
            for fmt, (w, h) in (("9:16", (1080, 1920)), ("16:9", (1920, 1080))):
                ass = vc.VideoComposer().build_subtitles(
                    words, Path(tmp) / f"{case['topic']}_{w}.ass", list(case.get("emphasis_words") or []),
                    width=w, height=h, reconstruction_intervals=[(1.0, 3.0)])
                data = ass.read_bytes()
                ass_sha[f"{case['topic']} {fmt}"] = hashlib.sha256(data).hexdigest()
                digest.update(data)
    wide_font = T.get_font(vc.CAPTION_WIDE_FONT, vc.CAPTION_WIDE_SIZE, weight=700)
    nfc, nfd = "Königsberg Kraków", "Königsberg Kraków"
    parity = {"ass_files": len(ass_sha), "ass_sha256_all": digest.hexdigest(), "ass_sha256": ass_sha,
              "decomposed_minus_composed_px_16x9": round(vc.unkerned_length(wide_font, nfd)
                                                         - vc.unkerned_length(wide_font, nfc), 3)}

    total = {k: sum(s[k] for s in per_design.values()) for k in ("lines", "identical", "ink_wider_lines")}
    out = {"tracked_copy": {"total": total, "per_design": per_design,
                            "measure_vs_kerned_advance_max_px": max(abs(r["measure_minus_kerned_advance_px"])
                                                                    for r in tracked)},
           "font_features": fonts, "default_feature_use": feature_use, "caption_parity": parity,
           "worst_lines": sorted(tracked, key=lambda r: -abs(r["ink_wider_than_kerned_px"]))[:12]}
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py REPOSITORY_ROOT")
    main(Path(sys.argv[1]))
