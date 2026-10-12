import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"
img_breach_path = os.path.join(ARTIFACTS, "derinkuyu_discovery_breach_1791761708022.jpg")

W, H = 1080, 1920
SAFE_Y_MIN = 450   # Top of 1:1 Instagram profile grid
SAFE_Y_MAX = 1470  # Bottom of 1:1 Instagram profile grid

def get_font(name, size):
    p = os.path.join(FONTS_DIR, "Anton-Regular.ttf" if name == "Anton" else "Montserrat-Variable.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def build_refined_concept1():
    im = Image.open(img_breach_path).convert("RGBA")
    im = im.resize((W, H), Image.LANCZOS)
    
    # 1. Atmospheric Scrim (subtle cinematic grade)
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    for y in range(H):
        if y < 420:
            alpha = int(150 * (1.0 - y / 420.0))
        elif y > 1420:
            alpha = int(190 * ((y - 1420) / 500.0))
        else:
            alpha = 30
        sdraw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    im = Image.alpha_composite(im, scrim)
    
    # Typography coordinates (Perfect Center of 1:1 Safe Grid y=960)
    center_y = 960
    
    title_line1 = "BEHIND A FAKE WALL..."
    title_line2 = "A 280-FT FORBIDDEN CITY"
    tagline     = "THE 1963 DERINKUYU DISCOVERY"
    eyebrow     = "DECLASSIFIED ARCHAEOLOGY"
    
    font_title = get_font("Anton", 86)
    font_tag   = get_font("Montserrat", 34)   # Increased to 34pt Bold
    font_eye   = get_font("Montserrat", 26)   # Refined eyebrow
    
    # Measure
    dummy = ImageDraw.Draw(im)
    bb_eye = dummy.textbbox((0, 0), eyebrow, font=font_eye)
    bb1 = dummy.textbbox((0, 0), title_line1, font=font_title)
    bb2 = dummy.textbbox((0, 0), title_line2, font=font_title)
    bb_tag = dummy.textbbox((0, 0), tagline, font=font_tag)
    
    w_eye, h_eye = bb_eye[2] - bb_eye[0], bb_eye[3] - bb_eye[1]
    w1, h1       = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2       = bb2[2] - bb2[0], bb2[3] - bb2[1]
    w_tag, h_tag = bb_tag[2] - bb_tag[0], bb_tag[3] - bb_tag[1]
    
    total_block_h = h_eye + 20 + h1 + 18 + h2 + 28 + h_tag
    start_y = center_y - total_block_h // 2
    
    y_eye = start_y
    y1    = y_eye + h_eye + 20
    y2    = y1 + h1 + 16
    y_tag = y2 + h2 + 26
    
    # 2. Film-Grade Dark Halo/Backing for 100% Readability
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    
    max_w = max(w1, w2)
    # Master headline glow
    gdraw.rounded_rectangle(
        [(W - max_w)//2 - 45, y1 - 20, (W + max_w)//2 + 45, y2 + h2 + 20],
        radius=20, fill=(0, 0, 0, 220)
    )
    # Dedicated high-contrast backing for tagline (solves the invisibility issue!)
    tag_pad_x, tag_pad_y = 30, 12
    gdraw.rounded_rectangle(
        [(W - w_tag)//2 - tag_pad_x, y_tag - tag_pad_y, (W + w_tag)//2 + tag_pad_x, y_tag + h_tag + tag_pad_y],
        radius=10, fill=(0, 0, 0, 235)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    im = Image.alpha_composite(im, glow)
    draw = ImageDraw.Draw(im)
    
    # 3. Eyebrow Tag (Gold pill border)
    eye_x = (W - w_eye) // 2
    draw.rounded_rectangle(
        [eye_x - 18, y_eye - 8, eye_x + w_eye + 18, y_eye + h_eye + 8],
        radius=6, fill=(18, 18, 18, 220), outline=(255, 215, 0, 240), width=1
    )
    draw.text((eye_x, y_eye), eyebrow, font=font_eye, fill=(255, 215, 0, 255))
    
    # 4. Headline (Line 1 White, Line 2 Warm Gold)
    draw.text(((W - w1)//2, y1), title_line1, font=font_title, fill=(255, 255, 255, 255))
    draw.text(((W - w2)//2, y2), title_line2, font=font_title, fill=(255, 215, 0, 255))
    
    # 5. Crisp, High-Contrast Tagline Badge (Gold accent border + pure white bold text)
    tag_x = (W - w_tag) // 2
    draw.rounded_rectangle(
        [tag_x - 24, y_tag - 10, tag_x + w_tag + 24, y_tag + h_tag + 10],
        radius=8, fill=(14, 14, 14, 230), outline=(255, 215, 0, 200), width=1
    )
    draw.text((tag_x, y_tag), tagline, font=font_tag, fill=(255, 255, 255, 255))
    
    # Save 9:16 and 1:1
    out_916 = os.path.join(ARTIFACTS, "thumbnail_concept1_perfected.jpg")
    out_1x1 = os.path.join(ARTIFACTS, "thumbnail_concept1_perfected_1x1.jpg")
    
    im.convert("RGB").save(out_916, quality=97)
    im.crop((0, SAFE_Y_MIN, W, SAFE_Y_MIN + W)).convert("RGB").save(out_1x1, quality=97)
    print("Perfected Concept 1 generated:", out_916)
    return out_916

if __name__ == "__main__":
    build_refined_concept1()
