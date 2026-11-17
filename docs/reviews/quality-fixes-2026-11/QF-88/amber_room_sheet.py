"""argv: <repo root> <trace.pkl> <frames dir> <out.jpg>. Contact sheet of the Amber Room's worst second from real
run data: the master's frames (ffmpeg, 270 x 480) with the worst 10-degree field outlined, each frame's counted
transition, and the field's luminance curves (fine stage: the field mean and its 100 one-degree boxes)."""
import pickle
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from src import config, picture_qa

with open(sys.argv[2], "rb") as fh:
    rec = pickle.load(fh)
rows = [r for r in rec if r["sid"] == 2]
py, px, F = 81, 35, 200
pos = (py, px)
area = config.PICTURE_QA_FLASH_AREA
FS = picture_qa._FlashStage
info = {}
for r in rows:
    g = r["general"]
    up, down = g["up"], g["down"]
    rise, fall = up[py:py + F, px:px + F].mean(), down[py:py + F, px:px + F].mean()
    cu, cd = FS._coarse_shares(up, F, F)[pos], FS._coarse_shares(down, F, F)[pos]
    ev = ""
    if rise > area and fall > area:
        ev = "BOTH, coarse" if (cu > area or cd > area) else "BOTH, fine"
    elif rise > area:
        ev = "UP"
    elif fall > area:
        ev = "DOWN"
    info[round(r["t"], 3)] = (ev, rise, fall, cu, cd, int(g["single"][pos]))
t = np.array([r["t"] for r in rows])
L = np.stack([r["lum"][py:py + F, px:px + F] for r in rows])
boxes = L.reshape(len(t), 10, 20, 10, 20).mean((2, 4)).reshape(len(t), -1)
mean = L.mean((1, 2))

try:
    font = ImageFont.truetype("arial.ttf", 13)
    small = ImageFont.truetype("arial.ttf", 11)
    big = ImageFont.truetype("arialbd.ttf", 15)
except OSError:
    font = small = big = ImageFont.load_default()
frames = sorted(Path(sys.argv[3]).glob("f_*.png"))
times = [round(4.3 + k / 30, 3) for k in range(len(frames))]
pick = [k for k, tt in enumerate(times) if 4.39 <= tt <= 4.77]
tw, th = 150, 267
cw = tw + 6
W = len(pick) * cw + 10
head, cap = 46, 58
chart_h = 300
sheet = Image.new("RGB", (W, head + th + cap + chart_h + 58), "white")
d = ImageDraw.Draw(sheet)
d.text((8, 6), "Amber Room master (dev run 1792790550), 4.40-4.77 s: the worst 10-degree field (yellow box, picture_qa "
       "fine stage). Round 5 counts 7 transitions in 8 frames (3.5 flashes): fail.", fill="black", font=big)
d.text((8, 26), "Each frame: the transition the field makes (more than 25% of it rising / falling), the share rising and "
       "falling, and in brackets the share coarse by majority over 1-degree boxes.", fill="black", font=font)
for j, k in enumerate(pick):
    im = Image.open(frames[k]).convert("RGB").resize((tw, th))
    x, y = 8 + j * cw, head
    dd = ImageDraw.Draw(im)
    fx0, fy0 = px / 599 * tw, py / 599 * th
    dd.rectangle([fx0, fy0, fx0 + F / 599 * tw, fy0 + F / 599 * th], outline=(255, 230, 0), width=2)
    sheet.paste(im, (x, y))
    tt = times[k]
    ev, rise, fall, cu, cd, single = info.get(tt, ("?", 0, 0, 0, 0, 0))
    d.text((x, y + th + 3), f"{tt:.3f} s", fill="black", font=font)
    d.text((x, y + th + 18), ev or "-", fill=(170, 0, 0) if ev else "grey", font=font)
    d.text((x, y + th + 33), f"up {rise:.0%} ({cu:.0%})", fill="black", font=small)
    d.text((x, y + th + 45), f"down {fall:.0%} ({cd:.0%})  n={single}", fill="black", font=small)
# chart
cx0, cy0 = 50, head + th + cap + 14
cw2, ch2 = W - cx0 - 20, chart_h - 40
t0, t1, lmax = 4.30, 4.90, 0.8
def X(tt):
    return cx0 + (tt - t0) / (t1 - t0) * cw2
def Y(v):
    return cy0 + ch2 - min(v, lmax) / lmax * ch2
d.rectangle([cx0, cy0, cx0 + cw2, cy0 + ch2], outline="black")
for v in (0, 0.2, 0.4, 0.6, 0.8):
    d.line([cx0 - 4, Y(v), cx0, Y(v)], fill="black")
    d.text((8, Y(v) - 7), f"{v:.1f}", fill="black", font=small)
sel = (t >= t0 - 1e-6) & (t <= t1 + 1e-6)
for b in range(boxes.shape[1]):
    pts = [(X(tt), Y(v)) for tt, v in zip(t[sel], boxes[sel, b])]
    d.line(pts, fill=(150, 170, 200), width=1)
d.line([(X(tt), Y(v)) for tt, v in zip(t[sel], mean[sel])], fill="black", width=3)
for tt, (ev, *_rest) in info.items():
    if ev and t0 <= tt <= t1:
        d.line([X(tt), cy0, X(tt), cy0 + ch2], fill=(200, 0, 0), width=1)
        d.text((X(tt) + 2, cy0 + 2), {"UP": "U", "DOWN": "D"}.get(ev, "B"), fill=(200, 0, 0), font=small)
for tt in np.arange(4.3, 4.91, 0.1):
    d.text((X(tt) - 12, cy0 + ch2 + 4), f"{tt:.1f} s", fill="black", font=small)
d.text((cx0, cy0 + ch2 + 22), "relative luminance: black = the field's mean; blue = its 100 one-degree boxes; "
       "red = the 7 counted transitions (U up, D down, B counter-phase with a coarse rise or fall)",
       fill="black", font=small)
d.text((cx0, cy0 + ch2 + 36), "within 3.7-4.7 s: 75% of the field's one-degree boxes make 4 or more transitions "
       "of their own, 29% make 5, 6% make 6; the field mean makes 2", fill="black", font=small)
out = Path(sys.argv[4])
q = 85
while True:
    sheet.save(out, quality=q, optimize=True)
    if out.stat().st_size <= 290_000 or q <= 40:
        break
    q -= 5
print(out, sheet.size, out.stat().st_size, q)
