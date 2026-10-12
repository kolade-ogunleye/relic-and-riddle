import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

p_a = os.path.join(ARTIFACTS, "thumb_opacity_78pct_1x1.jpg")
p_b = os.path.join(ARTIFACTS, "thumb_opacity_85pct_1x1.jpg")
p_c = os.path.join(ARTIFACTS, "thumb_opacity_90pct_1x1.jpg")

im_a = Image.open(p_a)
im_b = Image.open(p_b)
im_c = Image.open(p_c)

target_size = 600
s_a = im_a.resize((target_size, target_size), Image.LANCZOS)
s_b = im_b.resize((target_size, target_size), Image.LANCZOS)
s_c = im_c.resize((target_size, target_size), Image.LANCZOS)

sheet_w = 40 + (target_size + 30) * 3 + 10
sheet_h = target_size + 180
sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 14, 14))
draw = ImageDraw.Draw(sheet)

f_head = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 48)
f_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
f_col = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)

draw.text((sheet_w // 2, 40), "1:1 INSTAGRAM PROFILE GRID TRANSPARENCY CALIBRATION", font=f_head, fill=(255, 215, 0), anchor="mm")
draw.text((sheet_w // 2, 80), "How the 3 transparency levels look cropped inside the 1:1 profile grid", font=f_sub, fill=(210, 210, 210), anchor="mm")

cols = [
    ("VARIANT A: 78% Opacity", s_a, 40),
    ("VARIANT B: 85% Opacity (Recommended)", s_b, 40 + target_size + 30),
    ("VARIANT C: 90% Opacity", s_c, 40 + (target_size + 30) * 2),
]
for label, img, x in cols:
    draw.text((x + target_size // 2, 120), label, font=f_col, fill=(255, 255, 255), anchor="mm")
    sheet.paste(img, (x, 145))
    draw.rectangle([x, 145, x + target_size, 145 + target_size], outline=(255, 215, 0) if "85%" in label else (70, 70, 70), width=2)
    
sheet_path = os.path.join(ARTIFACTS, "transparency_1x1_sheet.jpg")
sheet.save(sheet_path, quality=96)
print("Saved 1:1 calibration sheet:", sheet_path)
