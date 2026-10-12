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

def render_atmospheric_title(base_img, canvas_w, canvas_h, center_y):
    """
    Renders with:
    - 100% organic, soft feathered transparency (NO hard borders, NO opaque box).
    - Stone and background texture clearly visible through the soft dark halo.
    - Mathematical vertical centering of all text elements and pill badges.
    """
    im = base_img.copy()
    
    font_eye   = get_font("Montserrat", 32)
    font_title = get_font("Anton", 92)
    font_tag   = get_font("Montserrat", 40)
    
    eyebrow_text = "DECLASSIFIED ARCHAEOLOGY"
    line1_text   = "BEHIND A FAKE WALL..."
    line2_text   = "A 280-FT FORBIDDEN CITY"
    tagline_text = "THE 1963 DERINKUYU DISCOVERY"
    
    dummy = ImageDraw.Draw(im)
    def get_wh(txt, font):
        bb = dummy.textbbox((0, 0), txt, font=font, anchor="lt")
        return bb[2] - bb[0], bb[3] - bb[1]
    
    w_eye, h_eye = get_wh(eyebrow_text, font_eye)
    w_l1,  h_l1  = get_wh(line1_text, font_title)
    w_l2,  h_l2  = get_wh(line2_text, font_title)
    w_tag, h_tag = get_wh(tagline_text, font_tag)
    
    pad_eye_x, pad_eye_y = 28, 14
    eye_box_w = w_eye + pad_eye_x * 2
    eye_box_h = h_eye + pad_eye_y * 2
    
    pad_tag_x, pad_tag_y = 34, 16
    tag_box_w = w_tag + pad_tag_x * 2
    tag_box_h = h_tag + pad_tag_y * 2
    
    gap_eye_to_title = 30
    gap_title_lines  = 16
    gap_title_to_tag = 34
    
    total_content_h = eye_box_h + gap_eye_to_title + h_l1 + gap_title_lines + h_l2 + gap_title_to_tag + tag_box_h
    start_y = center_y - total_content_h // 2
    
    # 1. Soft Feathered Dark Halo (NO hard edges, NO opaque card, purely organic transparency)
    halo_pad_y = 44
    halo_pad_x = 54
    halo_max_w = max(eye_box_w, w_l1, w_l2, tag_box_w)
    halo_w = min(canvas_w - 60, halo_max_w + halo_pad_x * 2)
    halo_h = total_content_h + halo_pad_y * 2
    
    halo_x0 = (canvas_w - halo_w) // 2
    halo_x1 = halo_x0 + halo_w
    halo_y0 = center_y - halo_h // 2
    halo_y1 = center_y + halo_h // 2
    
    # Soft feathered alpha overlay (65% opacity in center, feathered out smoothly)
    halo = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(halo)
    hdraw.rounded_rectangle(
        [halo_x0, halo_y0, halo_x1, halo_y1],
        radius=26,
        fill=(0, 0, 0, 160)  # Gentle 62% transparency - stone texture remains visible!
    )
    # Heavy Gaussian Blur creates a seamless, natural photographic drop-shadow falloff
    halo = halo.filter(ImageFilter.GaussianBlur(18))
    im = Image.alpha_composite(im, halo)
    draw = ImageDraw.Draw(im)
    
    # 2. Eyebrow Badge (Centered vertically via anchor="mm")
    cur_y = start_y
    eye_center_x = canvas_w // 2
    eye_box_y0 = cur_y
    eye_box_y1 = cur_y + eye_box_h
    eye_center_y = (eye_box_y0 + eye_box_y1) // 2
    
    # Pill with clean 2px gold border and translucent dark backing
    draw.rounded_rectangle(
        [eye_center_x - eye_box_w // 2, eye_box_y0, eye_center_x + eye_box_w // 2, eye_box_y1],
        radius=10,
        fill=(12, 12, 12, 210),
        outline=(255, 215, 0, 240),
        width=2
    )
    draw.text((eye_center_x, eye_center_y), eyebrow_text, font=font_eye, fill=(255, 215, 0, 255), anchor="mm")
    
    # 3. Main Title Line 1 (Symmetrical vertical centering)
    cur_y = eye_box_y1 + gap_eye_to_title
    l1_center_x = canvas_w // 2
    l1_center_y = cur_y + h_l1 // 2
    draw.text((l1_center_x, l1_center_y), line1_text, font=font_title, fill=(255, 255, 255, 255), anchor="mm")
    
    # 4. Main Title Line 2
    cur_y = cur_y + h_l1 + gap_title_lines
    l2_center_x = canvas_w // 2
    l2_center_y = cur_y + h_l2 // 2
    draw.text((l2_center_x, l2_center_y), line2_text, font=font_title, fill=(255, 215, 0, 255), anchor="mm")
    
    # 5. Tagline Badge (Symmetrical vertical centering inside pill)
    cur_y = cur_y + h_l2 + gap_title_to_tag
    tag_center_x = canvas_w // 2
    tag_box_y0 = cur_y
    tag_box_y1 = cur_y + tag_box_h
    tag_center_y = (tag_box_y0 + tag_box_y1) // 2
    
    draw.rounded_rectangle(
        [tag_center_x - tag_box_w // 2, tag_box_y0, tag_center_x + tag_box_w // 2, tag_box_y1],
        radius=12,
        fill=(14, 14, 14, 215),
        outline=(255, 215, 0, 240),
        width=2
    )
    draw.text((tag_center_x, tag_center_y), tagline_text, font=font_tag, fill=(255, 255, 255, 255), anchor="mm")
    
    return im

def build_atmospheric_versions():
    raw = Image.open(img_breach_path).convert("RGBA")
    
    # 9:16
    base_916 = raw.resize((W_916, H_916), Image.LANCZOS)
    out_916 = render_atmospheric_title(base_916, W_916, H_916, center_y=960)
    out_916_path = os.path.join(ARTIFACTS, "thumbnail_atmospheric_9x16.jpg")
    out_916.convert("RGB").save(out_916_path, quality=98)
    print("Saved 9:16:", out_916_path)
    
    # 1:1
    raw_w, raw_h = raw.size
    crop_size = min(raw_w, raw_h)
    base_1x1 = raw.crop(((raw_w - crop_size) // 2, (raw_h - crop_size) // 2, (raw_w + crop_size) // 2, (raw_h + crop_size) // 2))
    base_1x1 = base_1x1.resize((W_1x1, H_1x1), Image.LANCZOS)
    out_1x1 = render_atmospheric_title(base_1x1, W_1x1, H_1x1, center_y=540)
    out_1x1_path = os.path.join(ARTIFACTS, "thumbnail_atmospheric_1x1.jpg")
    out_1x1.convert("RGB").save(out_1x1_path, quality=98)
    print("Saved 1:1:", out_1x1_path)

if __name__ == "__main__":
    build_atmospheric_versions()
