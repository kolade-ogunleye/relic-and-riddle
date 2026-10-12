import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

opt1_p = os.path.join(ARTIFACTS, "thumbnail_v1_documentary_1x1.jpg")
opt2_p = os.path.join(ARTIFACTS, "thumbnail_v2_curiosity_1x1.jpg")
opt3_p = os.path.join(ARTIFACTS, "thumbnail_v3_vault_1x1.jpg")

im1 = Image.open(opt1_p)
im2 = Image.open(opt2_p)
im3 = Image.open(opt3_p)

# Scale down each 1080x1080 to 600x600
target_size = 600
im1_s = im1.resize((target_size, target_size), Image.LANCZOS)
im2_s = im2.resize((target_size, target_size), Image.LANCZOS)
im3_s = im3.resize((target_size, target_size), Image.LANCZOS)

sheet_w = 40 + (target_size + 30) * 3 + 10
sheet_h = target_size + 200
sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 18))
draw = ImageDraw.Draw(sheet)

font_header = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 48)
font_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
font_label = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)

draw.text((sheet_w // 2, 40), "INSTAGRAM PROFILE GRID PREVIEW (1:1 SQUARE CROPS)", font=font_header, fill=(255, 215, 0), anchor="mm")
draw.text((sheet_w // 2, 85), "How these covers appear inside the 3-column feed on Instagram profile tabs", font=font_sub, fill=(200, 200, 200), anchor="mm")

cols = [
    ("OPTION 1 (1:1 Grid)", im1_s, 40),
    ("OPTION 2 (1:1 Grid)", im2_s, 40 + target_size + 30),
    ("OPTION 3 (1:1 Grid)", im3_s, 40 + (target_size + 30) * 2),
]

for label, img, x in cols:
    draw.text((x + target_size // 2, 125), label, font=font_label, fill=(255, 255, 255), anchor="mm")
    sheet.paste(img, (x, 150))
    draw.rectangle([x, 150, x + target_size, 150 + target_size], outline=(255, 215, 0), width=2)

out_path = os.path.join(ARTIFACTS, "thumbnail_grid_sheet.jpg")
sheet.save(out_path, quality=95)
print("Saved grid sheet:", out_path)
