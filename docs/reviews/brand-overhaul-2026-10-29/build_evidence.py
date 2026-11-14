"""Evidence for docs/reviews/brand-overhaul-2026-10-29: before (HEAD) | after half-size pairs and measurements."""
import json
import os
import sys
from pathlib import Path

MAIN = Path(os.environ.get("RR_FIXTURE_REPO") or Path(__file__).resolve().parents[3])  # the checkout holding output/
sys.path.insert(0, str(MAIN))
from PIL import Image, ImageDraw  # noqa: E402
from src.typography import get_font  # noqa: E402
from src.script_engine import engagement_bait  # noqa: E402
from src import still_layout, safe_zones  # noqa: E402

B, A = Path(r"C:\tmp\bo_before\out"), Path(r"C:\tmp\bo_after\out")
DOC = MAIN / "docs" / "reviews" / "brand-overhaul-2026-10-29"
DOC.mkdir(parents=True, exist_ok=True)
TOPICS = ["richat", "gumbaz", "derinkuyu"]


def pair(before: Path, after: Path, out: Path, label: str):
    ims = []
    for p in (before, after):
        if p and p.is_file():
            im = Image.open(p).convert("RGB")
            ims.append(im.resize((im.width // 2, im.height // 2), Image.LANCZOS))
        else:
            ims.append(None)
    w = max(i.width for i in ims if i)
    h = max(i.height for i in ims if i)
    bar = 34
    board = Image.new("RGB", (2 * w + 12, h + bar), (24, 24, 26))
    d = ImageDraw.Draw(board)
    f = get_font("Montserrat", 15 if w < 600 else 20, weight=700)
    for k, (im, tag) in enumerate(zip(ims, ("BEFORE (HEAD)", "AFTER"))):
        x = k * (w + 12)
        d.text((x + 8, 7), f"{tag}  {label}", font=f, fill=(230, 230, 230))
        if im:
            board.paste(im, (x, bar))
        else:
            d.text((x + 20, bar + 40), "(none at HEAD)", font=f, fill=(160, 160, 160))
    board.save(out, quality=82, optimize=True)


made = []
for t in TOPICS:
    for k, n in enumerate(["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"], 1):
        o = DOC / f"story_{t}_{k}.jpg"
        pair(B / t / "stories" / n, A / t / "stories" / n, o, f"{t} Story {k}")
        made.append(o.name)
    for k in range(1, 8):
        n = f"carousel_slide_{k:02d}.jpg"
        o = DOC / f"carousel_{t}_{k}.jpg"
        pair(B / t / "carousel" / n, A / t / "carousel" / n, o, f"{t} slide {k}")
        made.append(o.name)
    for g in ("numbers", "map", "timeline"):
        bp, ap = B / t / "graphics" / f"graphic_{g}_graded.png", A / t / "graphics" / f"graphic_{g}_graded.png"
        if bp.is_file() or ap.is_file():
            o = DOC / f"{g}_{t}.jpg"
            pair(bp, ap, o, f"{t} {g} (last frame, film-graded)")
            made.append(o.name)
    o = DOC / f"captions_{t}.jpg"
    pair(B / t / "captions" / "caption.png", A / t / "captions" / "caption.png", o, f"{t} caption (graded)")
    made.append(o.name)
    o = DOC / f"end_{t}.jpg"
    pair(B / t / "end" / "end_card.png", A / t / "end" / "end_card.png", o, f"{t} end card (last frame)")
    made.append(o.name)
    o = DOC / f"cover_{t}.jpg"
    pair(B / t / "cover" / "thumbnail_cover.jpg", A / t / "cover" / "thumbnail_cover.jpg", o, f"{t} cover")
    made.append(o.name)

# ---------------------------------------------------------------- measurements
m = {"stills": {}, "graphics": {}, "end_card": {}, "gates": {}}
copy = []
for t in TOPICS:
    for folder in ("stories", "carousel"):
        rec = json.loads((A / t / folder / "still_layout.json").read_text(encoding="utf-8"))
        for still, e in rec.items():
            els = e["elements"]
            m["stills"][f"{t}/{folder}/{still}"] = {
                "min_contrast": min(x["contrast"] for x in els if x.get("contrast") is not None),
                "problems": still_layout.problems(e), "shortened": e.get("shortened"),
                "elements": {x["name"]: x.get("contrast") for x in els}}
            r = e.get("recipe") or {}
            if folder == "stories":
                copy += [v for v in (r.get("slide") or {}).values() if isinstance(v, str)]
            else:
                copy += [(r.get("card") or {}).get("heading", ""), (r.get("card") or {}).get("body", ""), r.get("share", "")]
    lay = json.loads((A / t / "stories" / "layout.json").read_text(encoding="utf-8"))
    m["gates"][f"{t}/story_credits"] = {e["frame"]: {"required": e["photo_credit"]["required"],
                                                      "contrast": e["photo_credit"].get("contrast"),
                                                      "box": e["photo_credit"].get("box")} for e in lay}
    for c in sorted((A / t / "graphics").glob("*.contrast.json")):
        d = json.loads(c.read_text(encoding="utf-8"))
        m["graphics"][f"{t}/{d['clip']}"] = {"min_graded": d["min"], "max_bottom": max(e["box"][3] for e in d["elements"]),
                                             "max_right": max(e["box"][2] for e in d["elements"]),
                                             "elements": [(e["name"], e["text"], e["graded"]) for e in d["elements"]]}
    s = A / t / "graphics" / "graphic_map.mp4.scale.json"
    if s.is_file():
        m["graphics"][f"{t}/scale_bar"] = json.loads(s.read_text(encoding="utf-8"))
    st = json.loads((A / t / "end" / "stats.json").read_text(encoding="utf-8"))
    m["end_card"][t] = {k: st.get(k) for k in ("share_lines", "share_size", "follow_size", "contrast", "box")}
    copy += st.get("share_lines") or []
copy += ["FULL STORY IN OUR NEW REEL", "FOLLOW FOR THE NEXT RELIC", "YOUR TAKE"]
m["gates"]["engagement_bait"] = {c: engagement_bait(c) for c in copy if c and engagement_bait(c)}
m["gates"]["graphics_box"] = list(safe_zones.graphics_box())
cap = Path(r"C:\tmp\bo_work\captest")
for f in ("caption_style_measurements.json", "caption_style_measurements_grey909090.json"):
    if (cap / f).is_file():
        m["captions_" + f.split(".")[0].replace("caption_style_measurements", "pill_vs_stroke")] = json.loads(
            (cap / f).read_text(encoding="utf-8"))
(DOC / "measurements.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
print(len(made), "pairs;", "bait:", m["gates"]["engagement_bait"])
print("stills min", min(v["min_contrast"] for v in m["stills"].values()),
      "problems", {k: v["problems"] for k, v in m["stills"].items() if v["problems"]})
print("graphics min", {k: v.get("min_graded") for k, v in m["graphics"].items()})
print("end", {k: v["contrast"] for k, v in m["end_card"].items()})
