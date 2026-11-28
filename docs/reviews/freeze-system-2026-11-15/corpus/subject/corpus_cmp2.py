"""Round 3: every pool photograph with a subject box (oracle boxes, or the reviewed boxes of data/cover_art_direction.json)
x every photo window, measured in ONE tree: the crop without a box ("blind") and the crop with the box ("box"), each
with the subject's share in the window's view and hold. Run it in the base tree (box = the cycle-5 rules with the box)
and in the tip tree (box = the tip rules); compare with cmp2_report.py.
python corpus_cmp2.py <tree> <boxes.json> <out.json>"""
import sys, json, importlib.util, warnings, copy, hashlib
from pathlib import Path
warnings.simplefilter("ignore")
tree, boxes_f, out_f = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(tree))
spec = importlib.util.spec_from_file_location("corp", tree / "docs/reviews/freeze-system-2026-11-15/corpus/photo_fitness.py")
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
pf = c.pf
from PIL import Image, ImageOps
HERE = Path(__file__).resolve().parent
boxes = json.loads(boxes_f.read_text())
reviewed = {e["sha256"]: e["focus_box"] for e in json.loads((tree / "data/cover_art_direction.json").read_text(encoding="utf-8"))["photos"]}
pool = json.load(open(HERE / "pool.json"))
DEV = Path(__import__("os").environ["RR_DEV"])   # the dev checkout
for e in json.loads((tree / "data/cover_art_direction.json").read_text(encoding="utf-8"))["photos"]:
    pool.append({"path": str(DEV / e["source"]), "sha256": e["sha256"]})
photos = c.Photos(HERE / "pfcache")
wins = c.windows()
KC = HERE / "kept_cache.json"
kept_cache = json.loads(KC.read_text()) if KC.is_file() else {}


def kept_of(p):
    if p not in kept_cache:
        with Image.open(p) as im:
            W, H = ImageOps.exif_transpose(im).size
        clean, kept = pf.load(Path(p))
        kept_cache[p] = (W, H, list(kept))
        KC.write_text(json.dumps(kept_cache))
    return kept_cache[p]


def to_px(p, nb, a):
    W, H, kept = kept_of(p)
    x, y = kept[0], kept[1]
    b = (nb[0] * W - x, nb[1] * H - y, nb[2] * W - x, nb[3] * H - y)
    cw, ch = a.size
    b = (max(0., b[0]), max(0., b[1]), min(float(cw), b[2]), min(float(ch), b[3]))
    return b if b[2] > b[0] and b[3] > b[1] else None


def shares(rec, window, box):
    if box is None or not rec.get("box"):
        return None, None
    view = pf._sub(tuple(rec["box"]), window.size, window.view_box())
    hold = pf._sub(tuple(rec["box"]), window.size, pf._clip(window.hold, window.size)) if window.hold else view
    return round(pf._overlap(box, view), 3), round(pf._overlap(box, hold), 3)


rows = []
seen = set()
for o in pool:
    p, sha = o["path"], o["sha256"]
    if sha in seen or not (sha in boxes or sha in reviewed):
        continue
    seen.add(sha)
    a = photos.get(Path(p))
    if a is None:
        continue
    src = "reviewed" if sha in reviewed else "oracle"
    box = to_px(p, reviewed.get(sha) or boxes[sha], a)
    blind = copy.copy(a); blind.focus = None; blind._sat = dict(a._sat)
    withb = copy.copy(a); withb.focus = box; withb._sat = dict(a._sat)
    for surface, kind, window in wins:
        b = c.crop_tip(blind, window, kind)
        t = c.crop_tip(withb, window, kind)
        if b.get("outcome") == "refused":
            continue
        bv, bh = shares(b, window, box)
        tv, th = shares(t, window, box)
        rows.append({"photo": p, "sha": sha, "source": src, "surface": surface, "kind": kind,
                     "blind_box": b.get("box"), "box_box": t.get("box"), "blind_fit": b.get("fit"), "box_fit": t.get("fit"),
                     "blind_problems": b.get("problems"), "box_problems": t.get("problems"),
                     "blind_view": bv, "blind_hold": bh, "box_view": tv, "box_hold": th,
                     "box_partial": t.get("subject_partial"), "box_basis": t.get("basis"),
                     "blind_prot": b.get("protected"), "box_prot": t.get("protected")})
out_f.write_text(json.dumps(rows))
print(len(rows), len(seen))
