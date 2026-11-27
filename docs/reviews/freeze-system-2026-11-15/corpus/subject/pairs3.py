import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
H = Path(__file__).resolve().parent
font = ImageFont.truetype("arial.ttf", 24)
out = H / "pairs3"; out.mkdir(exist_ok=True)
for n in sys.argv[1:]:
    cells = []
    for cfg, lab in (("r3_blind", "base, no box"), ("r3_c5", "base rules + box"), ("r3_tip", "tip + box")):
        d = H / cfg / "covers" / n
        a = Image.open(d / "thumbnail_cover.jpg").convert("RGB").resize((360, 640))
        b = Image.open(d / "thumbnail_profile_grid.jpg").convert("RGB"); b = b.resize((400, int(b.height * 400 / b.width)))
        cells.append((lab, a, b))
    W = sum(a.width + b.width + 30 for _, a, b in cells); Hh = 680
    im = Image.new("RGB", (W, Hh), (25, 25, 25)); x = 0; dr = ImageDraw.Draw(im)
    for lab, a, b in cells:
        dr.text((x + 4, 4), lab + " (reel | tile)", fill="white", font=font)
        im.paste(a, (x, 36)); im.paste(b, (x + a.width + 10, 36)); x += a.width + b.width + 30
    im.save(out / f"{n}.jpg", quality=85); print("ok", n)
