import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"
img_breach_path = os.path.join(ARTIFACTS, "derinkuyu_discovery_breach_1791761708022.jpg")

W_916, H_916 = 1080, 1920
W_1x1, H_1x1 = 1080, 1080

def get_font(name, size):
    p = os.path.join(FONTS_DIR, "Anton-Regular.ttf" if name == "Anton" else "Montserrat-Variable.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def render_title_card(base_img, canvas_w, canvas_h, center_y):
    """
    Renders the unified title card with mathematical precision:
    - anchor='mm' for 100% true vertical and horizontal centering.
    - Equalized padding inside all pill badges.
    - Symmetrical vertical alignment inside the translucent dark backing container.
    """
    im = base_img.copy()
    
    # 1. Fonts with increased, commanding sizes
    font_eye   = get_font("Montserrat", 32)   # Eyebrow: 32pt Bold
    font_title = get_font("Anton", 92)         # Main Title: 92pt Anton
    font_tag   = get_font("Montserrat", 40)   # Tagline: 40pt Bold (Prominent & sharp!)
    
    eyebrow_text = "DECLASSIFIED ARCHAEOLOGY"
    line1_text   = "BEHIND A FAKE WALL..."
    line2_text   = "A 280-FT FORBIDDEN CITY"
    tagline_text = "THE 1963 DERINKUYU DISCOVERY"
    
    # Measure text dimensions
    dummy = ImageDraw.Draw(im)
    
    def get_wh(txt, font):
        bb = dummy.textbbox((0, 0), txt, font=font, anchor="lt")
        return bb[2] - bb[0], bb[3] - bb[1]
    
    w_eye, h_eye = get_wh(eyebrow_text, font_eye)
    w_l1,  h_l1  = get_wh(line1_text, font_title)
    w_l2,  h_l2  = get_wh(line2_text, font_title)
    w_tag, h_tag = get_wh(tagline_text, font_tag)
    
    # Badge paddings (exact equal vertical and horizontal padding)
    pad_eye_x, pad_eye_y = 28, 14
    eye_box_w = w_eye + pad_eye_x * 2
    eye_box_h = h_eye + pad_eye_y * 2
    
    pad_tag_x, pad_tag_y = 34, 16
    tag_box_w = w_tag + pad_tag_x * 2
    tag_box_h = h_tag + pad_tag_y * 2
    
    # Vertical Spacing rhythm inside the hero card
    gap_eye_to_title = 32
    gap_title_lines  = 16
    gap_title_to_tag = 36
    
    card_internal_h = eye_box_h + gap_eye_to_title + h_l1 + gap_title_lines + h_l2 + gap_title_to_tag + tag_box_h
    
    # Master card symmetrical padding
    card_pad_y = 48
    card_pad_x = 56
    
    card_total_h = card_internal_h + card_pad_y * 2
    card_max_content_w = max(eye_box_w, w_l1, w_l2, tag_box_w)
    card_total_w = min(canvas_w - 80, card_max_content_w + card_pad_x * 2)
    
    # Master Card Boundaries centered on center_y
    card_x0 = (canvas_w - card_total_w) // 2
    card_x1 = card_x0 + card_total_w
    card_y0 = center_y - card_total_h // 2
    card_y1 = card_y0 + card_total_h
    
    # 2. Draw Translucent Dark Glassmorphic Backing Card with Soft Glow
    backing = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(backing)
    
    # Outer soft diffused shadow
    shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle([card_x0 - 15, card_y0 - 15, card_x1 + 15, card_y1 + 15], radius=28, fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    im = Image.alpha_composite(im, shadow)
    
    # Main translucent card
    bdraw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=24,
        fill=(8, 8, 8, 225),            # Deep rich dark charcoal, 88% opacity
        outline=(255, 215, 0, 70),       # Subtle gold glass edge
        width=1
    )
    im = Image.alpha_composite(im, backing)
    draw = ImageDraw.Draw(im)
    
    # 3. Y positions of all elements calculated from card_y0 + card_pad_y
    cur_y = card_y0 + card_pad_y
    
    # A. Eyebrow Badge (Centered vertically in its box via anchor="mm")
    eye_center_x = canvas_w // 2
    eye_box_y0 = cur_y
    eye_box_y1 = cur_y + eye_box_h
    eye_center_y = (eye_box_y0 + eye_box_y1) // 2
    
    # Eyebrow pill background & border
    draw.rounded_rectangle(
        [eye_center_x - eye_box_w // 2, eye_box_y0, eye_center_x + eye_box_w // 2, eye_box_y1],
        radius=10,
        fill=(22, 22, 22, 255),
        outline=(255, 215, 0, 240),
        width=2
    )
    draw.text((eye_center_x, eye_center_y), eyebrow_text, font=font_eye, fill=(255, 215, 0, 255), anchor="mm")
    
    # B. Main Title Line 1
    cur_y = eye_box_y1 + gap_eye_to_title
    l1_center_x = canvas_w // 2
    l1_center_y = cur_y + h_l1 // 2
    draw.text((l1_center_x, l1_center_y), line1_text, font=font_title, fill=(255, 255, 255, 255), anchor="mm")
    
    # C. Main Title Line 2
    cur_y = cur_y + h_l1 + gap_title_lines
    l2_center_x = canvas_w // 2
    l2_center_y = cur_y + h_l2 // 2
    draw.text((l2_center_x, l2_center_y), line2_text, font=font_title, fill=(255, 215, 0, 255), anchor="mm")
    
    # D. Tagline Badge
    cur_y = cur_y + h_l2 + gap_title_to_tag
    tag_center_x = canvas_w // 2
    tag_box_y0 = cur_y
    tag_box_y1 = cur_y + tag_box_h
    tag_center_y = (tag_box_y0 + tag_box_y1) // 2
    
    draw.rounded_rectangle(
        [tag_center_x - tag_box_w // 2, tag_box_y0, tag_center_x + tag_box_w // 2, tag_box_y1],
        radius=12,
        fill=(24, 24, 24, 255),
        outline=(255, 215, 0, 240),
        width=2
    )
    draw.text((tag_center_x, tag_center_y), tagline_text, font=font_tag, fill=(255, 255, 255, 255), anchor="mm")
    
    return im

def build_both_aspect_ratios():
    raw = Image.open(img_breach_path).convert("RGBA")
    
    # ---------------- 9:16 Canvas (1080x1920) ----------------
    base_916 = raw.resize((W_916, H_916), Image.LANCZOS)
    # Subtle top/bottom darkening to let the image breathe
    scrim_916 = Image.new("RGBA", (W_916, H_916), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim_916)
    for y in range(H_916):
        if y < 400:
            a = int(140 * (1.0 - y / 400.0))
        elif y > 1450:
            a = int(180 * ((y - 1450) / 470.0))
        else:
            a = 15
        sdraw.line([(0, y), (W_916, y)], fill=(0, 0, 0, a))
    base_916 = Image.alpha_composite(base_916, scrim_916)
    
    out_916_im = render_title_card(base_916, W_916, H_916, center_y=960)
    out_916_path = os.path.join(ARTIFACTS, "thumbnail_aligned_9x16.jpg")
    out_916_im.convert("RGB").save(out_916_path, quality=98)
    print("Saved 9:16:", out_916_path)
    
    # ---------------- 1:1 Canvas (1080x1080) ----------------
    # Crop the central square of raw image
    raw_w, raw_h = raw.size
    crop_size = min(raw_w, raw_h)
    base_1x1 = raw.crop(((raw_w - crop_size) // 2, (raw_h - crop_size) // 2, (raw_w + crop_size) // 2, (raw_h + crop_size) // 2))
    base_1x1 = base_1x1.resize((W_1x1, H_1x1), Image.LANCZOS)
    
    # Subtle vignette on 1:1
    scrim_1x1 = Image.new("RGBA", (W_1x1, H_1x1), (0, 0, 0, 0))
    sdraw1 = ImageDraw.Draw(scrim_1x1)
    for y in range(H_1x1):
        if y < 180:
            a = int(120 * (1.0 - y / 180.0))
        elif y > 900:
            a = int(140 * ((y - 900) / 180.0))
        else:
            a = 15
        sdraw1.line([(0, y), (W_1x1, y)], fill=(0, 0, 0, a))
    base_1x1 = Image.alpha_composite(base_1x1, scrim_1x1)
    
    out_1x1_im = render_title_card(base_1x1, W_1x1, H_1x1, center_y=540)
    out_1x1_path = os.path.join(ARTIFACTS, "thumbnail_aligned_1x1.jpg")
    out_1x1_im.convert("RGB").save(out_1x1_path, quality=98)
    print("Saved 1:1:", out_1x1_path)

if __name__ == "__main__":
    build_both_aspect_ratios()
