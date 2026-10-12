import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

c1_raw = os.path.join(ARTIFACTS, "derinkuyu_discovery_breach_1791761708022.jpg")
c1_titled = os.path.join(ARTIFACTS, "hollywood_thumb_breach.jpg")
c2_raw = os.path.join(ARTIFACTS, "derinkuyu_subterranean_abyss_1791761727194.jpg")
c2_titled = os.path.join(ARTIFACTS, "hollywood_thumb_abyss.jpg")

im_c1_raw = Image.open(c1_raw)
im_c1_tit = Image.open(c1_titled)
im_c2_raw = Image.open(c2_raw)
im_c2_tit = Image.open(c2_titled)

target_w, target_h = 480, 853
s_c1_raw = im_c1_raw.resize((target_w, target_h), Image.LANCZOS)
s_c1_tit = im_c1_tit.resize((target_w, target_h), Image.LANCZOS)
s_c2_raw = im_c2_raw.resize((target_w, target_h), Image.LANCZOS)
s_c2_tit = im_c2_tit.resize((target_w, target_h), Image.LANCZOS)

sheet_w = 40 + (target_w + 25) * 4 + 15
sheet_h = target_h + 200
sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 14, 14))
draw = ImageDraw.Draw(sheet)

font_header = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 52)
font_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
font_label = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)

draw.text((sheet_w // 2, 40), "HOLLYWOOD KEY-ART DOCUMENTARY COVERS", font=font_header, fill=(255, 215, 0), anchor="mm")
draw.text((sheet_w // 2, 85), "8K Cinematic AI Artwork vs Film-Grade Title Treatments (1080x1920 / 9:16)", font=font_sub, fill=(210, 210, 210), anchor="mm")

cols = [
    ("CONCEPT 1: The Breach (Raw Artwork)", s_c1_raw, 40),
    ("CONCEPT 1: With Film Title", s_c1_tit, 40 + (target_w + 25)),
    ("CONCEPT 2: The Abyss (Raw Artwork)", s_c2_raw, 40 + (target_w + 25) * 2),
    ("CONCEPT 2: With Film Title", s_c2_tit, 40 + (target_w + 25) * 3),
]

for label, img, x in cols:
    draw.text((x + target_w // 2, 125), label, font=font_label, fill=(255, 255, 255), anchor="mm")
    sheet.paste(img, (x, 150))
    draw.rectangle([x, 150, x + target_w, 150 + target_h], outline=(255, 215, 0) if "Title" in label else (80, 80, 80), width=2)

out_path = os.path.join(ARTIFACTS, "hollywood_comparison_sheet.jpg")
sheet.save(out_path, quality=95)
print("Saved Hollywood comparison sheet:", out_path)
