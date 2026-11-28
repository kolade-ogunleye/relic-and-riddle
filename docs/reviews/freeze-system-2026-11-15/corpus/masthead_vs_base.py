"""MASTHEAD no-regression proof (freeze cycle 6): every cover (Instagram and Facebook slide 1) and every interior slide
plates_draw.py drew, and every YouTube thumbnail masthead_draw_youtube.py drew, base against tip:

1. text identical (every element's text; the thumbnail's drawn plates);
2. the title size not lower (heading px; the lockup's sizes), else listed with the ratio rule's record;
3. contrast per element after the platform re-encode (still_layout "contrast_platform") not lower than base by more
   than DROP; the YouTube masthead's own judged ratio likewise;
4. the photograph not darker (outside every text box: the share of the frame more than DARK luma darker than base);
5. the subject not less visible: the photograph's detail (mean absolute luma gradient) in the slide's photo view
   outside the text boxes, tip against base, not lower by more than DETAIL %;
6. interiors of a one-line post: pixel-identical (the class does not touch them); a stacked post's interiors carry
   the stacked mark and are held to 1-5 like the covers.

    python masthead_vs_base.py <base decks>/<brand> <tip decks>/<brand> [--yt-base d --yt-tip d] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

DARK, SLACK, PAD, DROP, DETAIL = 20.0, 0.5, 6, 0.05, 1.0
L = np.array([0.2126, 0.7152, 0.0722], np.float32)
VIEW = 0.55   # the cover's photo view (multi_format PHOTO_VIEW["cover"])


def frames(run: Path, sub: str) -> dict:
    f = run / sub / "still_layout.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def luma(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGB"), np.float32) @ L


def mask_out(shape, boxes) -> np.ndarray:
    keep = np.ones(shape, bool)
    for x0, y0, x1, y1 in boxes:
        keep[max(0, y0 - PAD):y1 + PAD, max(0, x0 - PAD):x1 + PAD] = False
    return keep


def detail(lum: np.ndarray, keep: np.ndarray, top: int) -> float:
    g = np.abs(np.diff(lum[:top], axis=1))
    k = keep[:top, 1:]
    return float(g[k].mean()) if k.any() else 0.0


def decks(base: Path, tip: Path, rows: list, bad: list) -> None:
    for run in sorted(p for p in tip.iterdir() if (p / "draw.json").is_file()):
        brun = base / run.name
        if not (brun / "draw.json").is_file():
            continue
        stacked = json.loads((run / "draw.json").read_text(encoding="utf-8")).get("masthead_form") == "stacked"
        for sub in ("carousel", "carousel_fb"):
            fb_, ft_ = frames(brun, sub), frames(run, sub)
            for name, et in sorted(ft_.items()):
                eb = fb_.get(name)
                pb, pt = brun / sub / name, run / sub / name
                if not eb or not pb.is_file() or not pt.is_file():
                    continue
                cover = name == "carousel_slide_01.jpg"
                lb, lt = luma(pb), luma(pt)
                same = lb.shape == lt.shape and bool(np.array_equal(lb, lt))
                tb = {e["name"]: e.get("text") for e in eb.get("elements") or []}
                tt = {e["name"]: e.get("text") for e in et.get("elements") or []}
                text_ok = {k: v for k, v in tb.items()} == {k: v for k, v in tt.items()}
                hb = ((eb.get("meta") or {}).get("heading") or {})
                ht = ((et.get("meta") or {}).get("heading") or {})
                size_ok = float(ht.get("size") or 0) >= float(hb.get("size") or 0) - 1e-6
                lines_same = hb.get("lines") == ht.get("lines")
                cb = {e["name"]: e.get("contrast_platform") for e in eb.get("elements") or []}
                ct = {e["name"]: e.get("contrast_platform") for e in et.get("elements") or []}
                drops = {k: (cb[k], ct.get(k)) for k in cb if cb[k] is not None
                         and (ct.get(k) is None or ct[k] < cb[k] - DROP)}
                boxes = [e["box"] for e in (eb.get("elements") or []) + (et.get("elements") or [])]
                keep = mask_out(lt.shape, boxes)
                dark = round(100.0 * float(((lb - lt > DARK) & keep).mean()), 2) if lb.shape == lt.shape else None
                top = round(lt.shape[0] * VIEW)
                db, dt = detail(lb, keep, top), detail(lt, keep, top)
                det = round(100.0 * (dt - db) / db, 2) if db else 0.0
                ok = text_ok and size_ok and not drops and (dark or 0) <= SLACK and det >= -DETAIL
                if not cover and not stacked:   # a one-line post's interiors: untouched by the class
                    ok = ok and same
                row = {"run": run.name[4:], "deck": sub, "still": name, "cover": cover, "stacked_post": stacked,
                       "pixels_same": same,
                       "text_same": text_ok, "lines_same": lines_same, "title_px": [hb.get("size"), ht.get("size")],
                       "contrast_platform": {k: [cb.get(k), ct.get(k)] for k in sorted(set(cb) | set(ct))},
                       "drops": drops, "darker_pct": dark, "detail_change_pct": det,
                       "mast_px": [((eb.get("meta") or {}).get("masthead") or {}).get("size_px"),
                                   ((et.get("meta") or {}).get("masthead") or {}).get("size_px")],
                       "strip": [((eb.get("meta") or {}).get("swipe_strip") or {}).get("box"),
                                 ((et.get("meta") or {}).get("swipe_strip") or {}).get("box")], "ok": ok}
                rows.append(row)
                if not ok:
                    bad.append(row)
                if cover or not ok:
                    print(f"{run.name[4:44]:40} {sub:11} {name[-6:-4]} {'same' if same else 'diff'} text "
                          f"{'=' if text_ok else 'CHANGED'} title {hb.get('size')}->{ht.get('size')} mast "
                          f"{row['mast_px'][0]}->{row['mast_px'][1]} cp {cb.get('header_brand')}->"
                          f"{ct.get('header_brand')} dark {dark}% detail {det:+.2f}% "
                          f"{'pass' if ok else 'FAIL ' + json.dumps(drops)}", flush=True)


def youtube(base: Path, tip: Path, rows: list, bad: list) -> None:
    for run in sorted(p for p in tip.iterdir() if (p / "yt.json").is_file()):
        fb, ft = base / run.name / "yt.json", run / "yt.json"
        if not fb.is_file():
            continue
        b, t = json.loads(fb.read_text(encoding="utf-8")), json.loads(ft.read_text(encoding="utf-8"))
        if not (b.get("ok") and t.get("ok")):
            ok = b.get("ok") == t.get("ok")
            row = {"run": run.name[4:], "deck": "youtube", "refused": [not b.get("ok"), not t.get("ok")], "ok": ok}
        else:
            pb, pt = base / run.name / "thumbnail_youtube_16x9.jpg", run / "thumbnail_youtube_16x9.jpg"
            same = bool(np.array_equal(np.asarray(Image.open(pb)), np.asarray(Image.open(pt))))
            text_ok = [p["text"] for p in b["plates"]] == [p["text"] for p in t["plates"]]
            sb, st = (b.get("lockup") or {}).get("sizes"), (t.get("lockup") or {}).get("sizes")
            size_ok = not sb or not st or min(st) >= min(sb)
            mb, mt = (b.get("masthead") or {}).get("contrast"), (t.get("masthead") or {}).get("contrast")
            c_ok = mb is None or (mt is not None and mt >= mb - DROP)
            ok = text_ok and (size_ok or bool((t.get("hook_ratio") or {}).get("title_remedy"))) and c_ok
            row = {"run": run.name[4:], "deck": "youtube", "pixels_same": same, "text_same": text_ok,
                   "title_px": [sb, st], "mast_contrast": [mb, mt], "ok": ok}
        rows.append(row)
        if not row["ok"]:
            bad.append(row)
        print(f"{run.name[4:44]:40} youtube     {json.dumps({k: v for k, v in row.items() if k not in ('run', 'deck')})}",
              flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("tip", type=Path)
    ap.add_argument("--yt-base", type=Path)
    ap.add_argument("--yt-tip", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows, bad = [], []
    decks(a.base, a.tip, rows, bad)
    if a.yt_base and a.yt_tip:
        youtube(a.yt_base, a.yt_tip, rows, bad)
    covers = [r for r in rows if r.get("cover")]
    inter = [r for r in rows if r.get("deck") != "youtube" and not r.get("cover")]
    yts = [r for r in rows if r.get("deck") == "youtube"]
    counts = {"covers": len(covers), "covers changed": sum(not r["pixels_same"] for r in covers),
              "covers text identical": sum(r["text_same"] for r in covers),
              "covers title size lower": sum(float(r["title_px"][1] or 0) < float(r["title_px"][0] or 0)
                                             for r in covers),
              "interiors": len(inter), "interiors pixel-identical": sum(r["pixels_same"] for r in inter),
              "one-line posts' interiors": sum(not r["stacked_post"] for r in inter),
              "one-line posts' interiors pixel-identical": sum(r["pixels_same"] for r in inter if not r["stacked_post"]),
              "contrast lower (any element)": sum(bool(r.get("drops")) for r in rows if r.get("deck") != "youtube"),
              "thumbnails": len(yts), "thumbnails pixel-identical": sum(bool(r.get("pixels_same")) for r in yts),
              "violations": len(bad)}
    print(f"\nMASTHEAD vs base: {json.dumps(counts)}")
    if a.json:
        a.json.write_text(json.dumps({"counts": counts, "rows": rows}, indent=1, default=str), encoding="utf-8",
                          newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
