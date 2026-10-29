"""Side-by-side sheets from the half-size renders (before left, after right, both at 540x675) and one 100% crop
of the same text on a 1080 and a 1440 slide, shown at the same physical size: the 1080 crop enlarged 4/3 with
Lanczos (what a 1440-px-wide phone screen does to a 1080 slide) beside the 1440 crop at 1:1.

usage: python compose.py <review_dir> <before_full_slide> <after_full_slide> <crop x0,y0,x1,y1 in 1080 px>"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from src.typography import get_font

review, before_full, after_full = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
crop = [int(v) for v in sys.argv[4].split(",")]
font = get_font("Montserrat", 22)
for topic in ("roman", "nazca"):
    for copy in ("carousel", "carousel_fb"):
        before = sorted((review / "before" / topic / copy).glob("*.jpg"))
        after = sorted((review / "after" / topic / copy).glob("*.jpg"))
        w, h = 540, 675
        sheet = Image.new("RGB", (2 * w + 30, len(before) * (h + 40) + 10), (24, 24, 28))
        d = ImageDraw.Draw(sheet)
        for i, (b, a) in enumerate(zip(before, after)):
            y = 10 + i * (h + 40)
            d.text((10, y), f"{b.stem}  before 1080x1350", font=font, fill=(220, 220, 220))
            d.text((w + 30, y), "after 1440x1800 (shown at the same size)", font=font, fill=(220, 220, 220))
            sheet.paste(Image.open(b), (0, y + 30))
            sheet.paste(Image.open(a), (w + 30, y + 30))
        sheet.save(review / f"side_by_side_{topic}_{copy}.jpg", quality=85)

x0, y0, x1, y1 = crop
b = Image.open(before_full).convert("RGB").crop((x0, y0, x1, y1))
b = b.resize((round(b.width * 4 / 3), round(b.height * 4 / 3)), Image.LANCZOS)
s = 4 / 3
a = Image.open(after_full).convert("RGB").crop((round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s)))
out = Image.new("RGB", (b.width + a.width + 30, max(b.height, a.height) + 40), (24, 24, 28))
d = ImageDraw.Draw(out)
d.text((10, 8), "1080 slide, enlarged to 1440 (what the phone shows)", font=font, fill=(220, 220, 220))
d.text((b.width + 30, 8), "1440 slide at 100%", font=font, fill=(220, 220, 220))
out.paste(b, (0, 40))
out.paste(a, (b.width + 30, 40))
out.save(review / "crop_100pct_text.png")
