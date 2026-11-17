import os, sys
from PIL import Image, ImageDraw
R = "./maps_work/r11rev1"
n = 0
for f in sorted(os.listdir(f"{R}/after")):
    if not f.endswith(".mp4.png"): continue
    j = f[:-8]
    a = f"{R}/after/{f}"; b = f"{R}/before/{f}"
    Ai = Image.open(a).convert("RGB"); Ai = Ai.resize((Ai.width // 2, Ai.height // 2))
    Bi = Image.open(b).convert("RGB").resize(Ai.size) if os.path.exists(b) else Image.new("RGB", Ai.size, (60, 0, 0))
    o = Image.new("RGB", (Ai.width * 2 + 10, Ai.height + 30), (30, 30, 30))
    o.paste(Bi, (0, 30)); o.paste(Ai, (Ai.width + 10, 30))
    d = ImageDraw.Draw(o)
    d.text((6, 8), "HEAD 5fdb5dc" + ("" if os.path.exists(b) else " (none)"), fill=(255, 255, 255))
    d.text((Ai.width + 16, 8), f"working tree: {j}", fill=(255, 255, 255))
    o.save(f"{R}/boards/ba_{j}.jpg", quality=85); n += 1
print(n)
