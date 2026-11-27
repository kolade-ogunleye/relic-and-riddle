"""Base (no derived box) vs tip (derived box as the subject) over every pool photograph x every photo window + the Reel
cover, measured with the tree's own photo_fitness (corpus/photo_fitness.py crop_tip / cover_tip).
python corpus_cmp.py <tree> <boxes.json> <out.json>
boxes.json: {sha256: [x0,y0,x1,y1] normalised on the whole EXIF-turned picture} (accepted boxes only)."""
import sys, json, importlib.util, warnings, hashlib, copy
from pathlib import Path
warnings.simplefilter("ignore")
tree, boxes_f, out_f = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(tree))
spec = importlib.util.spec_from_file_location("corp", tree / "docs/reviews/freeze-system-2026-11-15/corpus/photo_fitness.py")
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
pf = c.pf
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent
boxes = json.loads(boxes_f.read_text())
gt = {g["sha256"]: g for g in json.load(open(HERE / "gt.json"))}
pool = json.load(open(HERE / "pool.json"))
photos = c.Photos(HERE / "pfcache")
wins = c.windows()
KC = HERE / 'kept_cache.json'
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
    b = (nb[0]*W - x, nb[1]*H - y, nb[2]*W - x, nb[3]*H - y)
    cw, ch = a.size
    b = (max(0., b[0]), max(0., b[1]), min(float(cw), b[2]), min(float(ch), b[3]))
    return b if b[2] > b[0] and b[3] > b[1] else None
def vis(a, rec, window, box):
    """share of `box` (picture px) inside the crop's view"""
    if box is None or not rec.get("box"): return None
    view = pf._sub(tuple(rec["box"]), window.size, window.view_box())
    return round(pf._overlap(box, view), 3)
rows = []
for o in pool:
    p = o["path"]; sha = o["sha256"]
    if sha not in boxes and sha not in gt: continue
    a0 = photos.get(Path(p))
    if a0 is None: continue
    dbox = to_px(p, boxes[sha], a0) if sha in boxes else None
    gbox = to_px(p, gt[sha]["box"], a0) if sha in gt else None
    a1 = copy.copy(a0); a1._sat = dict(a0._sat)
    if a0.focus is None and dbox is not None:
        a1.focus = dbox
    for surface, kind, window in wins + [("reel cover", "cover", None)]:
        if kind == "cover":
            b = c.cover_tip(a0); t = c.cover_tip(a1)
            rows.append({"photo": p, "sha": sha, "surface": surface, "base": b.get("layout"), "tip": t.get("layout"),
                         "base_problems": b.get("problems"), "tip_problems": t.get("problems") or t.get("why")})
            continue
        b = c.crop_tip(a0, window, kind); t = c.crop_tip(a1, window, kind)
        if b.get("outcome") == "refused": continue
        r = {"photo": p, "sha": sha, "surface": surface, "kind": kind,
             "base_box": b.get("box"), "tip_box": t.get("box"), "base_problems": b.get("problems"),
             "tip_problems": t.get("problems"), "base_fit": b.get("fit"), "tip_fit": t.get("fit"),
             "tip_moved": t.get("moved"), "base_moved": b.get("moved"),
             "gt_base": vis(a0, b, window, gbox), "gt_tip": vis(a0, t, window, gbox),
             "d_base": vis(a0, b, window, dbox), "d_tip": vis(a0, t, window, dbox)}
        if b.get("protected"): r["base_prot"] = b["protected"]
        if t.get("protected"): r["tip_prot"] = t["protected"]
        rows.append(r)
out_f.write_text(json.dumps(rows))
print(len(rows))
