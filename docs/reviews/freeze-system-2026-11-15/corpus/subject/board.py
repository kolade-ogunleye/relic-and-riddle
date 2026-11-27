import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
H = Path(__file__).resolve().parent
font = ImageFont.truetype("arial.ttf", 18)
names = sorted({p.name[len("covers__"):p.name.index("__thumbnail")] for p in (H / "pairs").glob("covers__*")})
def cell(p, w):
    im = Image.open(p).convert("RGB"); return im.resize((w, int(im.height * w / im.width)))
rows = []
for n in names:
    ims = [cell(H / s / "covers" / n / f, 150) for f in ("thumbnail_cover.jpg",) for s in ("base", "tip")]
    ims += [cell(H / s / "covers" / n / "thumbnail_profile_grid.jpg", 220) for s in ("base", "tip")]
    rows.append((n, ims))
per = 10
for k in range(0, len(rows), per):
    chunk = rows[k:k + per]
    cols = 5; cw = 150 + 150 + 220 + 220 + 50
    rh = max(max(i.height for i in ims) for _, ims in chunk) + 24
    W = 2 * cw; Hh = ((len(chunk) + 1) // 2) * rh + 30
    b = Image.new("RGB", (W, Hh), (30, 30, 30)); d = ImageDraw.Draw(b)
    d.text((5, 5), "per case: BASE reel | TIP reel (oracle subject) | BASE profile tile | TIP profile tile", fill="white", font=font)
    for i, (n, ims) in enumerate(chunk):
        x0, y0 = (i % 2) * cw, 30 + (i // 2) * rh
        d.text((x0 + 4, y0), n, fill=(255, 220, 0), font=font)
        x = x0
        for im in ims:
            b.paste(im, (x, y0 + 22)); x += im.width + 10
    b.save(H / f"board_covers_{k // per}.jpg", quality=85)
print(len(rows))
