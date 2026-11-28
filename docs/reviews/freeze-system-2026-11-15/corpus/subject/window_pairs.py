"""blind | cycle-5 with box | tip with box, for chosen (photo, surface) rows of the two-tree corpus: the crop drawn at the
window's size, its view outlined (cyan) and the subject box (green). python window_pairs.py <out_dir> photo_rel surface ..."""
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, r"C:\tmp\qf-w-subject-c6")
from PIL import Image, ImageDraw, ImageFont, ImageOps
from src import photo_fitness as pf
H = Path(__file__).resolve().parent
B = {(r["sha"], r["surface"]): r for r in json.load(open(H / "c2_base.json"))}
T = {(r["sha"], r["surface"]): r for r in json.load(open(H / "c2_tip2.json"))}
boxes = json.load(open(H / "boxes_all.json"))
rev = {e["sha256"]: e["focus_box"] for e in json.loads(Path(r"C:\tmp\qf-w-subject-c6\data\cover_art_direction.json").read_text(encoding="utf-8"))["photos"]}
font = ImageFont.truetype("arial.ttf", 26)
out = Path(sys.argv[1]); out.mkdir(exist_ok=True)
args = sys.argv[2:]
wins = {s: w for s, k, w in __import__("importlib").import_module("corp_windows").windows()} if False else None
spec = __import__("importlib.util").util.spec_from_file_location("corp", r"C:\tmp\qf-w-subject-c6\docs\reviews\freeze-system-2026-11-15\corpus\photo_fitness.py")
corp = __import__("importlib.util").util.module_from_spec(spec); spec.loader.exec_module(corp)
wins = {s: w for s, k, w in corp.windows()}
for rel, surface in zip(args[::2], args[1::2]):
    p = Path(__import__("os").environ["RR_DEV"]) / "assets" / rel
    d = hashlib.sha256(p.read_bytes()).hexdigest()
    clean, kept = pf.load(p)
    with Image.open(p) as im:
        W, Hh = ImageOps.exif_transpose(im).size
    nb = rev.get(d) or boxes[d]
    fb = (nb[0] * W - kept[0], nb[1] * Hh - kept[1], nb[2] * W - kept[0], nb[3] * Hh - kept[1])
    w = wins[surface]
    tiles = []
    for label, box in (("without box", T[(d, surface)]["blind_box"]), ("cycle-5 + box", B[(d, surface)]["box_box"]),
                       ("tip + box", T[(d, surface)]["box_box"])):
        im = pf.crop(clean, box, w.size).convert("RGB")
        dr = ImageDraw.Draw(im)
        kx, ky = w.size[0] / (box[2] - box[0]), w.size[1] / (box[3] - box[1])
        dr.rectangle([(fb[0] - box[0]) * kx, (fb[1] - box[1]) * ky, (fb[2] - box[0]) * kx, (fb[3] - box[1]) * ky], outline=(0, 255, 0), width=6)
        dr.rectangle(w.view_box(), outline=(0, 255, 255), width=4)
        im.thumbnail((540, 960))
        tiles.append((label, im))
    Wd = sum(t.width for _, t in tiles) + 40; Ht = max(t.height for _, t in tiles) + 40
    sheet = Image.new("RGB", (Wd, Ht), (25, 25, 25)); x = 0
    for label, t in tiles:
        sheet.paste(t, (x, 40)); ImageDraw.Draw(sheet).text((x + 4, 6), label, fill="white", font=font); x += t.width + 20
    sheet.save(out / f"{rel.replace('/', '_')}__{surface.replace(' ', '_')}.jpg", quality=85)
    print("ok", rel, surface)
