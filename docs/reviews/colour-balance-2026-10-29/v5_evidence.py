"""colour_balance v5 evidence: v4 vs v5 over the ten development pools and production's Derinkuyu door.
Usage: v5_evidence.py <repo> <out_dir>.  CACHE_DIR goes to a temporary folder; nothing in assets/ or cache/ is written."""
import sys, json, time, tempfile, pathlib, types
sys.stdout.reconfigure(encoding="utf-8")
root = pathlib.Path(sys.argv[1]); out_dir = pathlib.Path(sys.argv[2]); sys.path.insert(0, str(root))
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
from src import colour_balance as cb
cb.CACHE_DIR = pathlib.Path(tempfile.mkdtemp())
v4 = types.ModuleType("cb_v4")
v4.__dict__["BASE_DIR"] = pathlib.Path(tempfile.mkdtemp())
import subprocess   # version 4 as committed (d333bc6), run side by side with the working tree's version 5
exec(subprocess.run(["git", "-C", str(root), "show", "d333bc6:src/colour_balance.py"], capture_output=True,
                    text=True, encoding="utf-8", check=True).stdout.replace("from .config import BASE_DIR", ""), v4.__dict__)
assert v4.VERSION == 4 and cb.VERSION == 5
POOLS = ["derinkuyu_underground_city", "richat_structure_sahara_eye", "nazca_lines_runway_paradox",
         "roman_self_healing_concrete", "amber_room_heist", "lycurgus_cup_nanotech", "odessa_catacombs",
         "gobekli_tepe_enclosure_d", "kailasa_temple_monolith", "hal_saflieni_hypogeum"]
PROD = pathlib.Path(r"C:\Users\omokolade\.claude\jobs\d4ed881d\tmp\d72\f\prod08.jpg")
RENDER_UNCHANGED = {("amber_room_heist", "03_commons.jpg"), ("lycurgus_cup_nanotech", "09_commons.jpg"),
                    ("derinkuyu_underground_city", "19_commons.jpg"), ("derinkuyu_underground_city", "21_commons.jpg")}

def small(im):
    s = im.convert("RGB"); s.thumbnail((256, 256)); return np.asarray(s, dtype=np.float64)

def m(a, ref=None):
    sc = cb.scene(a); n = sc["neutral"]
    hue, sat = cb._hsv(a); y = cb._luma(a)
    r = {"cast": None if n is None else round(float(np.max(np.abs(n.mean() / np.maximum(n, 1e-6) - 1))), 4),
         "neutral_hue": None if n is None else round(cb._hue(n), 1), "sat": round(float(sat.mean()), 4),
         "luma_std": round(float(y.std()), 2), "lime_share": round(float(((hue >= 52) & (hue <= 90) & (sat > 0.3)).mean()), 4)}
    if ref is not None:
        rh, rs = cb._hsv(ref); k = rs > 0.12
        dh = np.abs(((hue - rh + 180) % 360) - 180)
        r["hue_shift"] = round(float((dh * rs)[k].sum() / max(rs[k].sum(), 1e-6)), 2)
        w = (rh >= 10) & (rh <= 48) & (rs > 0.2)
        r["warm_px"] = int(w.sum())
        if w.sum() > 20:
            r["warm_hue_change"] = round(float(dh[w].mean()), 2)
            r["warm_sat_ratio"] = round(float(sat[w].mean() / rs[w].mean()), 3)
    return r

def pair(tiles, path):
    h = min(t.height for t in tiles)
    tiles = [t.resize((max(1, t.width * h // t.height), h)) for t in tiles]
    W = sum(t.width for t in tiles) + 8 * (len(tiles) - 1)
    board = Image.new("RGB", (W, h), "white"); x = 0
    for t in tiles:
        board.paste(t, (x, 0)); x += t.width + 8
    board = board.resize((board.width // 2, board.height // 2), Image.LANCZOS)   # half size
    board.thumbnail((1600, 1600)); board.save(path, quality=85)

files = [(t, p) for t in POOLS for p in sorted((root / "assets" / t).glob("[0-9]*_commons.jpg"))] + [("production_derinkuyu", PROD)]
rows, pools = [], {}
for topic, p in files:
    with Image.open(p) as im:
        im = im.convert("RGB")
        s4, s5 = v4.plan(im), cb.plan(im)
        base5 = None if s5 is None else {k: v for k, v in s5.items() if k != "lamp_step"}
        same_settings = s4 == base5
        step = bool(s5 and s5.get("lamp_step"))
        o4 = v4.apply(im, s4) if s4 else im
        t0 = time.perf_counter(); o5 = cb.apply(im, s5) if s5 else im; dt = time.perf_counter() - t0
        diff = int(np.abs(np.asarray(o4, dtype=np.int16) - np.asarray(o5, dtype=np.int16)).max())
        r0, r4, r5 = small(im), small(o4), small(o5)
    row = {"topic": topic, "photo": p.name, "settings_v4": s4, "settings_v5": s5, "same_settings_as_v4": same_settings,
           "lamp_step": step, "max_pixel_diff_vs_v4": diff, "apply_seconds": round(dt, 2), "size": list(im.size),
           "raw": m(r0), "v4": m(r4, r0), "v5": m(r5, r4)}
    rows.append(row)
    pl = pools.setdefault(topic, {"photos": 0, "changed_vs_v4": 0, "settings_differ": 0, "lamp_step": 0, "rows": []})
    pl["photos"] += 1; pl["changed_vs_v4"] += diff > 0; pl["settings_differ"] += not same_settings; pl["lamp_step"] += step
    if step:
        pl["rows"].append(row)
        pair([im, o4, o5], out_dir / f"v5_{topic.split('_')[0]}_{p.stem.split('_')[0]}_raw_v4_v5.jpg")
    elif (topic, p.name) in RENDER_UNCHANGED:
        pair([o4, o5], out_dir / f"v5_{topic.split('_')[0]}_{p.stem.split('_')[0]}_v4_v5_unchanged.jpg")
print("| pool | photos | lamp step | pixels changed vs v4 | settings changed vs v4 (besides the step) |")
for t, pl in pools.items():
    print(f"| {t} | {pl['photos']} | {pl['lamp_step']} | {pl['changed_vs_v4']} | {pl['settings_differ']} |")
print()
print("| photo | cast raw / v4 / v5 | neutral hue v4 / v5 | sat v4 / v5 | lime share v4 / v5 | hue shift v4->v5 | warm px hue change / sat ratio | luma std v4 / v5 | apply s |")
for t, pl in pools.items():
    for r in pl["rows"]:
        a, b, c = r["raw"], r["v4"], r["v5"]
        print(f"| {t.split('_')[0]} {r['photo']} | {a['cast']:.3f} / {b['cast']:.3f} / {c['cast']:.3f} | {b['neutral_hue']:.0f} / {c['neutral_hue']:.0f} | "
              f"{b['sat']:.3f} / {c['sat']:.3f} | {b['lime_share']:.2f} / {c['lime_share']:.2f} | {c['hue_shift']:.1f} | "
              f"{c.get('warm_hue_change', '-')} / {c.get('warm_sat_ratio', '-')} | {b['luma_std']:.1f} / {c['luma_std']:.1f} | {r['apply_seconds']} |")
(out_dir / "measurements_v5.json").write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="\n")
