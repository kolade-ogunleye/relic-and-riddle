import os, sys
from PIL import Image, ImageDraw
R = "./maps_work"
B1, B2, A = f"{R}/r10rev2/before", f"{R}/r11build/before", f"{R}/r11build/after"
os.makedirs(f"{R}/r11build/boards", exist_ok=True)
n = 0
for j in sys.argv[1:]:
    for z in ("1080x1920", "1920x1080"):
        a = f"{A}/{j}_{z}.mp4.png"
        if not os.path.exists(a):
            continue
        b = next((p for p in (f"{B2}/{j}_{z}.mp4.png", f"{B1}/{j}_{z}.mp4.png") if os.path.exists(p)), None)
        Ai = Image.open(a).convert("RGB"); Ai = Ai.resize((Ai.width // 2, Ai.height // 2))
        Bi = Image.open(b).convert("RGB").resize(Ai.size) if b else Image.new("RGB", Ai.size, (60, 0, 0))
        o = Image.new("RGB", (Ai.width * 2 + 10, Ai.height + 30), (30, 30, 30))
        o.paste(Bi, (0, 30)); o.paste(Ai, (Ai.width + 10, 30))
        d = ImageDraw.Draw(o)
        d.text((6, 8), "HEAD 5fdb5dc" + ("" if b else " (none)"), fill=(255, 255, 255))
        d.text((Ai.width + 16, 8), f"working tree: {j} {z}", fill=(255, 255, 255))
        o.save(f"{R}/r11build/boards/ba_{j}_{z}.jpg", quality=85); n += 1
print(n)
