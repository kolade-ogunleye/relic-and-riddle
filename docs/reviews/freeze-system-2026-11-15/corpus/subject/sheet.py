"""Contact sheets: labelled box (green) and candidate boxes (red, other colours) on each labelled photo."""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
GT = json.load(open(HERE / "gt.json"))
font = ImageFont.truetype("arial.ttf", 16)
def draw_sheet(items, out, cols=4, cell=360):
    """items: [(gt, {label: (box, colour)} , caption)]"""
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 22)), (30, 30, 30))
    for i, (g, boxes, cap) in enumerate(items):
        im = Image.open(HERE / "thumbs" / (g["sha256"][:20] + ".png")).convert("RGB")
        im.thumbnail((cell, cell)); w, h = im.size
        d = ImageDraw.Draw(im)
        def rect(b, c, wd):
            d.rectangle([b[0]*w, b[1]*h, max(b[0]*w, b[2]*w-1), max(b[1]*h, b[3]*h-1)], outline=c, width=wd)
        rect(g["box"], (0, 255, 0), 3)
        for lab, (b, c) in boxes.items():
            if b: rect(b, c, 2)
        x, y = (i % cols) * cell, (i // cols) * (cell + 22)
        sheet.paste(im, (x + (cell - w) // 2, y))
        ImageDraw.Draw(sheet).text((x + 4, y + cell + 2), f"{g['id']} {cap}"[:46], fill=(255, 255, 255), font=font)
    sheet.save(out, quality=85)
