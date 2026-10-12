import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

opt1_p = os.path.join(ARTIFACTS, "thumbnail_v1_documentary.jpg")
opt2_p = os.path.join(ARTIFACTS, "thumbnail_v2_curiosity.jpg")
opt3_p = os.path.join(ARTIFACTS, "thumbnail_v3_vault.jpg")

im1 = Image.open(opt1_p)
im2 = Image.open(opt2_p)
im3 = Image.open(opt3_p)

# Scale down each 1080x1920 to 600x1067 for a clean side-by-side sheet
target_w, target_h = 600, 1067
im1_s = im1.resize((target_w, target_h), Image.LANCZOS)
im2_s = im2.resize((target_w, target_h), Image.LANCZOS)
im3_s = im3.resize((target_w, target_h), Image.LANCZOS)

# Create canvas: 3 columns + margins + header
sheet_w = 40 + (target_w + 30) * 3 + 10
sheet_h = target_h + 220
sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 18))
draw = ImageDraw.Draw(sheet)

font_header = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 54)
font_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)
font_label = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 30)

draw.text((sheet_w // 2, 45), "THUMBNAIL DESIGN SUITE — 3 ELITE CONCEPTS", font=font_header, fill=(255, 215, 0), anchor="mm")
draw.text((sheet_w // 2, 95), "1080x1920 Reel Vertical Covers with 1:1 Instagram Profile Grid Safe Zones", font=font_sub, fill=(200, 200, 200), anchor="mm")

# Paste columns
cols = [
    ("OPTION 1: Modern Documentary", im1_s, 40),
    ("OPTION 2: Curiosity Question", im2_s, 40 + target_w + 30),
    ("OPTION 3: Secret Vault Punch", im3_s, 40 + (target_w + 30) * 2),
]

for label, img, x in cols:
    draw.text((x + target_w // 2, 140), label, font=font_label, fill=(255, 255, 255), anchor="mm")
    sheet.paste(img, (x, 170))
    # Border
    draw.rectangle([x, 170, x + target_w, 170 + target_h], outline=(255, 215, 0), width=2)

out_path = os.path.join(ARTIFACTS, "thumbnail_comparison_sheet.jpg")
sheet.save(out_path, quality=95)
print("Saved comparison sheet:", out_path)
