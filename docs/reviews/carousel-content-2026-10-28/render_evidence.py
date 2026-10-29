"""Evidence renders for docs/reviews/carousel-content-2026-10-28: the production scripts of Roman concrete (whose
comparison slide called the Pantheon's dome "SUBTERRANEAN DEPTH" and whose marine slide was a "DISCOVERY RECORD")
and Nazca (whose hedged "By one estimate, ..." stat slide was "VERIFIED BY ARCHEOLOGICAL RECORD"), drawn by one
version of the real carousel renderer (no model calls; photos in /photos sheet order, rotated the same way for
both versions). Every ImageDraw text is spied on, so before and after are measured the same way: which labels
the slides draw that their cards do not say. The after version also records each slide's alt text, the
carousel's credit lines and Facebook's four photos.

usage: python render_evidence.py <code_root> <label> <out_dir> <repo_root>"""
import json
import os
import sys
from pathlib import Path

code_root, label, out_dir, repo = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
os.environ["RELIC_APP_ROOT"] = str(repo)            # fonts and assets from the repository, for both versions
sys.path.insert(0, str(code_root))
from PIL import Image, ImageDraw  # noqa: E402

from src import multi_format  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402

RUNS = {"roman_self_healing_concrete": "run_roman_self_healing_concrete_1793163918",
        "nazca_lines_runway_paradox": "run_nazca_lines_runway_paradox_1792961797"}
MultiFormatGenerator._match_photos = lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))]

calls = []
_text = ImageDraw.ImageDraw.text


def spy_text(self, xy, text, fill=None, font=None, anchor=None, *a, **k):
    out = _text(self, xy, text, fill, font, anchor, *a, **k)
    if str(text).strip():
        calls.append(str(text))
    return out


ImageDraw.ImageDraw.text = spy_text
saved = []
_save = Image.Image.save


def spy_save(self, fp, *a, **k):
    saved.append((Path(str(fp)), list(calls)))
    calls.clear()
    return _save(self, fp, *a, **k)


Image.Image.save = spy_save


def sheet_photos(topic):
    folder = repo / "assets" / topic
    images = json.load(open(folder / "credits.json", encoding="utf-8"))["images"]
    return [folder / im["file"] for im in images if (folder / im["file"]).is_file()][:7]


def half(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB")
    _save(im.resize((im.width // 2, im.height // 2), Image.LANCZOS), dst, quality=85)


def unstated(text: str, card: dict, share: str) -> bool:
    """Drawn text that is not the card's own words, the brand, the share line or the comments footer."""
    said = " ".join([card.get("heading", ""), card.get("body", ""), share]).upper()
    words = text.upper().replace("…", "").strip()
    return words not in ("RELIC & RIDDLE", "TELL US IN THE COMMENTS") and words not in said


results = {}
work = Path("C:/tmp/rr_carousel_content/work") / label
for topic, run in RUNS.items():
    p = json.load(open(repo / "output" / run / "verified_script.json", encoding="utf-8"))
    payload = p.get("payload", p)
    photos = sheet_photos(topic)
    short = topic.split("_")[0]
    gen = MultiFormatGenerator()
    from src.script_engine import share_prompt
    share = share_prompt(payload)
    for facebook in (False, True):
        copy = "carousel_fb" if facebook else "carousel"
        calls.clear(); saved.clear()
        slides = gen.generate_carousel_sequence(photos, payload, work / topic, facebook=facebook)
        for i, (path, texts) in enumerate(saved):
            card = payload["carousel_slides"][i]
            results[f"{short}/{copy}/{path.name}"] = {
                "card": f"{card['heading']}: {card['body']}",
                "labels_the_card_does_not_say": [t for t in texts if unstated(t, card, share)],
            }
            half(path, out_dir / label / short / copy / path.name)
        if label == "after":
            results[f"{short}/{copy}/alt_text"] = {s.name: multi_format.slide_alt_text(s) for s in slides}
            if facebook:
                results[f"{short}/facebook_photos"] = [Path(s).name for s in multi_format.facebook_pick([str(s) for s in slides])]
            else:
                results[f"{short}/credits"] = multi_format.carousel_credits(slides)[0]
                sys.path.insert(0, str(repo))
                from main import PipelineCoordinator
                cap, comment = PipelineCoordinator._carousel_post(payload, [str(s) for s in slides])
                results[f"{short}/caption"] = {"caption": cap, "first_comment": comment, "chars": len(cap)}

(out_dir / label / "measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
for k, v in results.items():
    print(label, k, json.dumps(v, ensure_ascii=False)[:240])
