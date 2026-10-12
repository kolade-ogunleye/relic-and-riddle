import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"

img_breach_path = os.path.join(ARTIFACTS, "derinkuyu_discovery_breach_1791761708022.jpg")
img_abyss_path = os.path.join(ARTIFACTS, "derinkuyu_subterranean_abyss_1791761727194.jpg")

W, H = 1080, 1920
SAFE_Y_MIN = 450
SAFE_Y_MAX = 1470

def get_font(name, size):
    p = os.path.join(FONTS_DIR, "Anton-Regular.ttf" if name == "Anton" else "Montserrat-Variable.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def process_cover(img_path, title_line1, title_line2, sub_text, out_name):
    im = Image.open(img_path).convert("RGBA")
    im = im.resize((W, H), Image.LANCZOS)
    
    # Cinematic lighting overlay: dark vignette top and bottom, but let center breath
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    
    for y in range(H):
        if y < 400:
            alpha = int(140 * (1.0 - y / 400.0))
        elif y > 1400:
            alpha = int(180 * ((y - 1400) / 520.0))
        else:
            alpha = 25  # minimal tint in center
        sdraw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        
    im = Image.alpha_composite(im, scrim)
    draw = ImageDraw.Draw(im)
    
    # Hollywood title placement: centered in safe zone (y = 960)
    center_y = 960
    
    font_title = get_font("Anton", 88)
    font_sub = get_font("Montserrat", 28)
    
    bb1 = draw.textbbox((0, 0), title_line1, font=font_title)
    bb2 = draw.textbbox((0, 0), title_line2, font=font_title)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
    
    y1 = center_y - (h1 + h2 + 30) // 2
    y2 = y1 + h1 + 18
    
    # Film-grade text shadow (not cheap solid box!)
    # Multi-layered soft glow / shadow behind text
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    
    max_w = max(w1, w2)
    gdraw.rounded_rectangle(
        [(W - max_w)//2 - 50, y1 - 25, (W + max_w)//2 + 50, y2 + h2 + 25],
        radius=20, fill=(0, 0, 0, 215)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(12))
    im = Image.alpha_composite(im, glow)
    draw = ImageDraw.Draw(im)
    
    # Draw Line 1 (White) & Line 2 (Warm Gold)
    draw.text(((W - w1)//2, y1), title_line1, font=font_title, fill=(255, 255, 255, 255))
    draw.text(((W - w2)//2, y2), title_line2, font=font_title, fill=(255, 215, 0, 255))
    
    # Subtitle with letter tracking feel
    sbb = draw.textbbox((0, 0), sub_text, font=font_sub)
    sw = sbb[2] - sbb[0]
    draw.text(((W - sw)//2, y2 + h2 + 35), sub_text, font=font_sub, fill=(230, 230, 230, 255))
    
    out_916 = os.path.join(ARTIFACTS, out_name + ".jpg")
    out_1x1 = os.path.join(ARTIFACTS, out_name + "_1x1.jpg")
    
    im.convert("RGB").save(out_916, quality=97)
    im.crop((0, SAFE_Y_MIN, W, SAFE_Y_MIN + W)).convert("RGB").save(out_1x1, quality=97)
    print("Saved:", out_916, "and", out_1x1)

process_cover(
    img_breach_path,
    "BEHIND A FAKE WALL...",
    "A 280-FT FORBIDDEN CITY",
    "THE 1963 DERINKUYU DISCOVERY",
    "hollywood_thumb_breach"
)

process_cover(
    img_abyss_path,
    "18 STORIES DEEP",
    "THE SUBTERRANEAN METROPOLIS",
    "WHAT FORCED AN ENTIRE CITY UNDERGROUND?",
    "hollywood_thumb_abyss"
)
