import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

FONTS_DIR = "fonts"
ASSETS_DIR = "assets"
ARTIFACTS_DIR = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"

W, H = 1080, 1920
SAFE_Y_MIN = 450   # 1:1 Instagram grid safe top
SAFE_Y_MAX = 1470  # 1:1 Instagram grid safe bottom

def get_font(name, size):
    paths = {
        "Anton": os.path.join(FONTS_DIR, "Anton-Regular.ttf"),
        "Montserrat": os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"),
    }
    p = paths.get(name, "")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def prepare_background(img_path, contrast=1.2, brightness=0.85):
    im = Image.open(img_path).convert("RGBA")
    # Scale to fill 1080x1920
    bw, bh = im.size
    target_ratio = W / H
    if bw / bh > target_ratio:
        nh = H
        nw = int(H * (bw / bh))
    else:
        nw = W
        nh = int(W / (bw / bh))
    im = im.resize((nw, nh), Image.LANCZOS)
    im = im.crop(((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H))
    
    # Grading
    im = ImageEnhance.Contrast(im).enhance(contrast)
    im = ImageEnhance.Brightness(im).enhance(brightness)
    
    # Cinematic Vignette / Radial Darkening
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    
    # Center darkening scrim behind safe zone
    for y in range(H):
        dist_from_center = abs(y - 960) / 960.0
        # Darker at top and bottom, but with a rich dark scrim in center for 100% text pop
        alpha = int(140 + 100 * dist_from_center)
        vdraw.line([(0, y), (W, y)], fill=(0, 0, 0, min(255, alpha)))
        
    return Image.alpha_composite(im, vignette)

def draw_safe_grid_guides(draw):
    """Draw faint safe zone guides for Instagram 1:1 grid preview."""
    # Top safe line
    draw.line([(0, SAFE_Y_MIN), (W, SAFE_Y_MIN)], fill=(255, 255, 255, 30), width=1)
    # Bottom safe line
    draw.line([(0, SAFE_Y_MAX), (W, SAFE_Y_MAX)], fill=(255, 255, 255, 30), width=1)

def fit_text(draw, text, font_name, max_w, initial_size=90, min_size=40):
    size = initial_size
    while size >= min_size:
        font = get_font(font_name, size)
        bb = draw.textbbox((0, 0), text, font=font)
        if (bb[2] - bb[0]) <= max_w:
            return font, size
        size -= 4
    return get_font(font_name, min_size), min_size

# -------------------------------------------------------------
# VARIATION 1: The Modern Documentary Master (Clean, Bold, Vox/Magnates style)
# -------------------------------------------------------------
def build_variation_1(out_path):
    bg = prepare_background("assets/scene3_door.jpg", contrast=1.25, brightness=0.75)
    draw = ImageDraw.Draw(bg)
    
    center_y = 960  # Center of 1:1 grid
    
    # 1. Archival pill tag
    tag_text = "ANCIENT MYSTERY  •  TURKEY 1963"
    tag_font = get_font("Montserrat", 28)
    tbb = draw.textbbox((0, 0), tag_text, font=tag_font)
    tw, th = tbb[2] - tbb[0], tbb[3] - tbb[1]
    
    tag_y = center_y - 230
    tag_pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(tag_pill)
    pdraw.rounded_rectangle(
        [(W - tw)//2 - 24, tag_y - 12, (W - tw)//2 + tw + 24, tag_y + th + 12],
        radius=8, fill=(255, 215, 0, 230)
    )
    bg = Image.alpha_composite(bg, tag_pill)
    draw = ImageDraw.Draw(bg)
    draw.text(((W - tw)//2, tag_y), tag_text, font=tag_font, fill=(10, 10, 10, 255))
    
    # 2. Headline: 2 stacked lines, perfectly fitted
    line1 = "BEHIND A FAKE WALL..."
    line2 = "AN 18-STORY METROPOLIS"
    
    max_w = W - 140  # 70px margin each side
    f1, s1 = fit_text(draw, line1, "Anton", max_w, initial_size=82)
    f2, s2 = fit_text(draw, line2, "Anton", max_w, initial_size=86)
    
    bb1 = draw.textbbox((0, 0), line1, font=f1)
    bb2 = draw.textbbox((0, 0), line2, font=f2)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
    
    y1 = center_y - 130
    y2 = y1 + h1 + 30
    
    # Heavy contrast pill backing for headline
    head_pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hp_draw = ImageDraw.Draw(head_pill)
    total_tw = max(w1, w2)
    hp_draw.rounded_rectangle(
        [(W - total_tw)//2 - 40, y1 - 24, (W + total_tw)//2 + 40, y2 + h2 + 24],
        radius=16, fill=(10, 10, 10, 210)
    )
    bg = Image.alpha_composite(bg, head_pill)
    draw = ImageDraw.Draw(bg)
    
    # Line 1 White
    draw.text(((W - w1)//2, y1), line1, font=f1, fill=(255, 255, 255, 255))
    # Line 2 Warm Gold
    draw.text(((W - w2)//2, y2), line2, font=f2, fill=(255, 215, 0, 255))
    
    # 3. High-contrast sub-badge
    sub_text = "CARVED 280 FEET INTO SOLID VOLCANIC ROCK"
    sub_font = get_font("Montserrat", 32)
    sbb = draw.textbbox((0, 0), sub_text, font=sub_font)
    sw, sh = sbb[2] - sbb[0], sbb[3] - sbb[1]
    sub_y = y2 + h2 + 50
    
    draw.text(((W - sw)//2, sub_y), sub_text, font=sub_font, fill=(230, 230, 230, 255))
    
    # Save 9:16 portrait
    bg.convert("RGB").save(out_path, quality=97)
    
    # Save 1:1 crop
    square_crop = bg.crop((0, SAFE_Y_MIN, W, SAFE_Y_MIN + W))
    square_crop.convert("RGB").save(out_path.replace(".jpg", "_1x1.jpg"), quality=97)
    print("Variation 1 generated:", out_path)

# -------------------------------------------------------------
# VARIATION 2: The Curiosity Hook (High CTR, NatGeo / Investigation Style)
# -------------------------------------------------------------
def build_variation_2(out_path):
    bg = prepare_background("assets/scene2_tunnel.jpg", contrast=1.3, brightness=0.7)
    draw = ImageDraw.Draw(bg)
    center_y = 960
    
    # Eyebrow
    eye_text = "ARCHAEOLOGICAL ENIGMA"
    eye_font = get_font("Montserrat", 30)
    ebb = draw.textbbox((0, 0), eye_text, font=eye_font)
    ew, eh = ebb[2] - ebb[0], ebb[3] - ebb[1]
    eye_y = center_y - 200
    
    draw.text(((W - ew)//2, eye_y), eye_text, font=eye_font, fill=(255, 215, 0, 255))
    
    # Accent rule
    draw.line([((W - ew)//2, eye_y + eh + 8), ((W + ew)//2, eye_y + eh + 8)], fill=(255, 215, 0, 255), width=2)
    
    # Main Headline
    line1 = "WHAT FORCED 20,000 PEOPLE"
    line2 = "TO LIVE UNDERGROUND?"
    
    max_w = W - 120
    f1, _ = fit_text(draw, line1, "Anton", max_w, initial_size=82)
    f2, _ = fit_text(draw, line2, "Anton", max_w, initial_size=82)
    
    bb1 = draw.textbbox((0, 0), line1, font=f1)
    bb2 = draw.textbbox((0, 0), line2, font=f2)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
    
    y1 = eye_y + eh + 40
    y2 = y1 + h1 + 25
    
    # Pill box
    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    mw = max(w1, w2)
    pdraw.rounded_rectangle([(W - mw)//2 - 36, y1 - 20, (W + mw)//2 + 36, y2 + h2 + 20], radius=14, fill=(8, 8, 8, 220))
    bg = Image.alpha_composite(bg, pill)
    draw = ImageDraw.Draw(bg)
    
    draw.text(((W - w1)//2, y1), line1, font=f1, fill=(255, 255, 255, 255))
    draw.text(((W - w2)//2, y2), line2, font=f2, fill=(255, 215, 0, 255))
    
    # Sub badge
    sub = "DERINKUYU • 18 STORIES DEEP"
    sub_font = get_font("Montserrat", 32)
    sbb = draw.textbbox((0, 0), sub, font=sub_font)
    sw = sbb[2] - sbb[0]
    draw.text(((W - sw)//2, y2 + h2 + 45), sub, font=sub_font, fill=(210, 210, 210, 255))
    
    bg.convert("RGB").save(out_path, quality=97)
    square_crop = bg.crop((0, SAFE_Y_MIN, W, SAFE_Y_MIN + W))
    square_crop.convert("RGB").save(out_path.replace(".jpg", "_1x1.jpg"), quality=97)
    print("Variation 2 generated:", out_path)

# -------------------------------------------------------------
# VARIATION 3: The Secret Vault (Punchy 3-Word Maximum Impact)
# -------------------------------------------------------------
def build_variation_3(out_path):
    bg = prepare_background("assets/scene4_pillars.jpg", contrast=1.35, brightness=0.72)
    draw = ImageDraw.Draw(bg)
    center_y = 960
    
    # Very big, 2-line punchy headline
    line1 = "THE FORBIDDEN"
    line2 = "CITY BENEATH"
    
    max_w = W - 140
    f1, _ = fit_text(draw, line1, "Anton", max_w, initial_size=105)
    f2, _ = fit_text(draw, line2, "Anton", max_w, initial_size=105)
    
    bb1 = draw.textbbox((0, 0), line1, font=f1)
    bb2 = draw.textbbox((0, 0), line2, font=f2)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
    
    y1 = center_y - (h1 + h2 + 40) // 2 - 30
    y2 = y1 + h1 + 20
    
    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    mw = max(w1, w2)
    pdraw.rounded_rectangle([(W - mw)//2 - 44, y1 - 24, (W + mw)//2 + 44, y2 + h2 + 24], radius=16, fill=(12, 12, 12, 225))
    bg = Image.alpha_composite(bg, pill)
    draw = ImageDraw.Draw(bg)
    
    draw.text(((W - w1)//2, y1), line1, font=f1, fill=(255, 255, 255, 255))
    draw.text(((W - w2)//2, y2), line2, font=f2, fill=(255, 215, 0, 255))
    
    sub = "DERINKUYU: ANCIENT METROPOLIS"
    sub_font = get_font("Montserrat", 32)
    sbb = draw.textbbox((0, 0), sub, font=sub_font)
    sw = sbb[2] - sbb[0]
    draw.text(((W - sw)//2, y2 + h2 + 44), sub, font=sub_font, fill=(230, 230, 230, 255))
    
    bg.convert("RGB").save(out_path, quality=97)
    square_crop = bg.crop((0, SAFE_Y_MIN, W, SAFE_Y_MIN + W))
    square_crop.convert("RGB").save(out_path.replace(".jpg", "_1x1.jpg"), quality=97)
    print("Variation 3 generated:", out_path)

if __name__ == "__main__":
    build_variation_1(os.path.join(ARTIFACTS_DIR, "thumbnail_v1_documentary.jpg"))
    build_variation_2(os.path.join(ARTIFACTS_DIR, "thumbnail_v2_curiosity.jpg"))
    build_variation_3(os.path.join(ARTIFACTS_DIR, "thumbnail_v3_vault.jpg"))
    print("All 3 variations generated successfully!")
