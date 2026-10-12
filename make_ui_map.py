import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"
bg_path = os.path.join(ARTIFACTS, "thumbnail_concept1_perfected.jpg")

im = Image.open(bg_path).convert("RGBA")
W, H = 1080, 1920

overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

font_ui = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)
font_bold = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 36)

# 1. Top Unsafe Zone (0 to 240px)
# Red translucent box
draw.rectangle([0, 0, W, 240], fill=(255, 0, 0, 80), outline=(255, 50, 50, 200), width=2)
draw.text((W // 2, 110), "UNSAFE ZONE: REEL HEADER (Back Arrow, Audio Title, Search)", font=font_ui, fill=(255, 255, 255, 240), anchor="mm")

# 2. Bottom Unsafe Zone (1500 to 1920px)
# Red translucent box
draw.rectangle([0, 1500, W, H], fill=(255, 0, 0, 80), outline=(255, 50, 50, 200), width=2)
draw.text((W // 2, 1680), "UNSAFE ZONE: NATIVE META UI\n(Account Handle @user, Follow Button, Video Caption, Audio Tag)", font=font_ui, fill=(255, 255, 255, 240), anchor="mm")

# 3. Right Rail Unsafe Zone (920 to 1080px between y=850 and 1500)
draw.rectangle([920, 850, W, 1500], fill=(255, 0, 0, 80), outline=(255, 50, 50, 200), width=2)
draw.text((1000, 1175), "LIKE\nCOMMENT\nSHARE\nRAIL", font=font_ui, fill=(255, 255, 255, 240), anchor="mm")

# 4. 1:1 Instagram Profile Grid Safe Box (y=450 to 1470)
# Green highlight box showing 100% safe zone
draw.rectangle([40, 450, 920, 1470], outline=(0, 255, 120, 255), width=4)
draw.text((480, 480), "GOLDEN SAFE ZONE: 1:1 INSTAGRAM PROFILE GRID CROP", font=font_bold, fill=(0, 255, 120, 255), anchor="mm")
draw.text((480, 1440), "ALL HEADLINES & TAGLINES ARE 100% SAFE INSIDE THIS BOX", font=font_ui, fill=(0, 255, 120, 255), anchor="mm")

# 5. Video Caption Golden Zone Marker (y=1320 to 1420)
draw.rectangle([60, 1340, 920, 1420], fill=(0, 200, 255, 60), outline=(0, 200, 255, 255), width=2)
draw.text((490, 1380), "OPTIMAL VIDEO CAPTION BASELINE (y=1360 / MarginV=560px)", font=font_ui, fill=(0, 200, 255, 255), anchor="mm")

final_im = Image.alpha_composite(im, overlay)
out_path = os.path.join(ARTIFACTS, "meta_ui_safe_zone_map.jpg")
final_im.convert("RGB").save(out_path, quality=95)
print("Saved Meta UI Map:", out_path)
