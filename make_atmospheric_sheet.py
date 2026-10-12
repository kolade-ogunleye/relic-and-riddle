import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

p_916 = os.path.join(ARTIFACTS, "thumbnail_atmospheric_9x16.jpg")
p_1x1 = os.path.join(ARTIFACTS, "thumbnail_atmospheric_1x1.jpg")

im_916 = Image.open(p_916)
im_1x1 = Image.open(p_1x1)

target_h = 1000
target_w_916 = int(target_h * (1080 / 1920))
target_w_1x1 = target_h

s_916 = im_916.resize((target_w_916, target_h), Image.LANCZOS)
s_1x1 = im_1x1.resize((target_w_1x1, target_h), Image.LANCZOS)

sheet_w = 40 + target_w_916 + 50 + target_w_1x1 + 40
sheet_h = target_h + 180
sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 14, 14))
draw = ImageDraw.Draw(sheet)

font_header = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 48)
font_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
font_label = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)

draw.text((sheet_w // 2, 40), "ATMOSPHERIC THUMBNAIL (SOFT TRANSPARENCY RESTORED)", font=font_header, fill=(255, 215, 0), anchor="mm")
draw.text((sheet_w // 2, 80), "Feathered Organic Drop-Shadow / Zero Hard Box Edges / Stone Texture Fully Visible", font=font_sub, fill=(210, 210, 210), anchor="mm")

x_916 = 40
draw.text((x_916 + target_w_916 // 2, 120), "9:16 Vertical Reel Cover", font=font_label, fill=(255, 255, 255), anchor="mm")
sheet.paste(s_916, (x_916, 145))
draw.rectangle([x_916, 145, x_916 + target_w_916, 145 + target_h], outline=(255, 215, 0), width=2)

x_1x1 = x_916 + target_w_916 + 50
draw.text((x_1x1 + target_w_1x1 // 2, 120), "1:1 Instagram Profile Grid Crop", font=font_label, fill=(255, 255, 255), anchor="mm")
sheet.paste(s_1x1, (x_1x1, 145))
draw.rectangle([x_1x1, 145, x_1x1 + target_w_1x1, 145 + target_h], outline=(255, 215, 0), width=2)

out_path = os.path.join(ARTIFACTS, "atmospheric_comparison_sheet.jpg")
sheet.save(out_path, quality=96)
print("Saved atmospheric sheet:", out_path)
