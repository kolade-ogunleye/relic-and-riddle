import sys
from PIL import Image, ImageDraw
d, out, size = sys.argv[1], sys.argv[2], sys.argv[3]
names = sys.argv[4:]
tall = size == "1080x1920"
tw, th = (360, 640) if tall else (640, 360)
cols = 4 if tall else 3
rows = (len(names) + cols - 1) // cols
o = Image.new("RGB", (cols * (tw + 6), rows * (th + 22)), (30, 30, 30))
dr = ImageDraw.Draw(o)
for i, n in enumerate(names):
    try:
        im = Image.open(f"{d}/{n}_{size}.mp4.png").convert("RGB").resize((tw, th))
    except Exception:
        continue
    x, y = (i % cols) * (tw + 6), (i // cols) * (th + 22)
    o.paste(im, (x, y + 20)); dr.text((x + 4, y + 4), n, fill=(255, 255, 255))
o.save(out, quality=85)
