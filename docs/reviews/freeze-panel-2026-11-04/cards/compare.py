"""Side-by-side before | current JPEGs (labelled) for every render both runs made, into compare/.

    python compare.py [--root <dir holding before/ and current/>]
    python compare.py --before current --after after --before-label "BEFORE (round 1 tree)"         --after-label "AFTER (round 2 tree)" --suffix _before_after     (round 2: compare/<variant>_before_after.jpg)

Each pair is the two half-size JPEGs side by side under a label strip naming the render, its run and topic, every
"new" label the render carries (render_evidence.py: all card fields here are new, real facts test-signed), the tree
and the plan's size, line counts and source (or the refusal). Files stay under 2 MB (quality steps down if needed).
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MAX_BYTES = 2 * 1024 * 1024
ap = argparse.ArgumentParser()
ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
ap.add_argument("--before", default="before")
ap.add_argument("--after", default="current")
ap.add_argument("--before-label", default="BEFORE  (HEAD 5fdb5dc)")
ap.add_argument("--after-label", default="CURRENT (working tree)")
ap.add_argument("--suffix", default="")
ARGS = ap.parse_args()
ROOT = ARGS.root
B, C, OUT = ROOT / ARGS.before, ROOT / ARGS.after, ROOT / "compare"
OUT.mkdir(exist_ok=True)


def font(px):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    """`text` broken at ", " and spaces to lines no wider than `width`."""
    lines, cur = [], ""
    for word in text.split(" "):
        t = (cur + " " + word).strip()
        if cur and d.textlength(t, font=f) > width:
            lines.append(cur)
            cur = word
        else:
            cur = t
    return lines + ([cur] if cur else [])


def note(rec: dict | None, render: str) -> str:
    if rec is None:
        return "not rendered"
    if rec.get("refused"):
        return "REFUSED: " + rec["refused"][:90]
    if rec.get("error"):
        return "ERROR: " + rec["error"][:90]
    a = rec.get("anchors") or {}
    bits = []
    for k in ("px", "P", "title_px", "myth_px"):
        if k in a:
            bits.append(f"{k} {a[k]}")
    for k, lab in (("myth_baselines", "myth lines"), ("fact_baselines", "fact lines"), ("lines", "lines"),
                   ("title_baselines", "title lines")):
        if k in a:
            bits.append(f"{lab} {a[k] if isinstance(a[k], int) else len(a[k])}")
    if rec["name"].startswith("myth"):
        bits.append("source DROPPED (" + str(rec["source_omitted"]) + ")" if rec.get("source_omitted")
                    else "source kept")
    r = (rec.get("renders") or {}).get(render) or {}
    if r.get("drawn_contrast_min") is not None:
        bits.append(f"min contrast {r['drawn_contrast_min']}")
    if "t" in r:
        bits.append(f"t {r['t']} s")
    return ", ".join(bits)


def load(run: Path) -> dict:
    """Every manifest the run wrote (manifest.json, or one per --only shard), merged."""
    out: dict = {}
    for p in sorted(run.glob("manifest*.json")):
        out.update(json.loads(p.read_text(encoding="utf-8"))["renders"])
    return out


def main() -> None:
    mb, mc = load(B), load(C)
    names = sorted({p.stem for p in (B / "jpg").glob("*.jpg")} | {p.stem for p in (C / "jpg").glob("*.jpg")})
    f_big, f_small = font(26), font(19)
    index = []
    for stem in names:
        card, _, render = stem.partition("__")
        tiles = []
        for run, man, label in ((B, mb, ARGS.before_label), (C, mc, ARGS.after_label)):
            p = run / "jpg" / f"{stem}.jpg"
            if not p.exists():   # the other run refused (its slate is __last) or did not render this frame
                alt = run / "jpg" / f"{card}__last.jpg"
                p = alt if alt.exists() else None
            im = Image.open(p).convert("RGB") if p else None
            tiles.append((im, label, note(man.get(card), render)))
        w = max(t[0].width for t in tiles if t[0] is not None)
        h = max(t[0].height for t in tiles if t[0] is not None)
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        rec = mc.get(card) or mb.get(card) or {}
        head = [f"{rec.get('run', '?')} | {rec.get('topic', '?')} | {rec.get('ground', '')} {rec.get('photo_name', '')}"]
        news = list(rec.get("labels") or [])
        blocks = [[(label, f_big, (235, 220, 160)), (stem, f_small, (190, 190, 190))]
                  + [(ln, f_small, (190, 190, 190)) for h in head for ln in wrap(probe, h, f_small, w - 20)]
                  + [(ln, f_small, (250, 196, 90)) for n in news for ln in wrap(probe, n, f_small, w - 20)]
                  + [(ln, f_small, (225, 225, 225)) for ln in wrap(probe, txt, f_small, w - 20)]
                  for _im, label, txt in tiles]
        strip = 16 + max(sum(f.size + 8 for _t, f, _c in bl) for bl in blocks)
        sheet = Image.new("RGB", (2 * w + 12, h + strip), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        for k, ((im, _label, _txt), bl) in enumerate(zip(tiles, blocks)):
            x = k * (w + 12)
            if im is not None:
                sheet.paste(im, (x, strip))
            y = 8
            for t, f, c in bl:
                d.text((x + 10, y), t, font=f, fill=c)
                y += f.size + 8
        out = OUT / f"{stem}{ARGS.suffix}.jpg"
        for q in (88, 80, 72, 64):
            buf = io.BytesIO()
            sheet.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= MAX_BYTES:
                break
        out.write_bytes(buf.getvalue())
        index.append({"compare": out.name, "run": rec.get("run"), "topic": rec.get("topic"),
                      "labels": news, "before": note(mb.get(card), render), "current": note(mc.get(card), render)})
    (OUT / f"index{ARGS.suffix}.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(index)} side-by-sides in {OUT}")


if __name__ == "__main__":
    main()
