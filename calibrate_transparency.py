import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARTIFACTS = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"
FONTS_DIR = "fonts"
img_breach_path = os.path.join(ARTIFACTS, "derinkuyu_discovery_breach_1791761708022.jpg")

W_916, H_916 = 1080, 1920

def get_font(name, size):
    p = os.path.join(FONTS_DIR, "Anton-Regular.ttf" if name == "Anton" else "Montserrat-Variable.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def render_calibrated_variant(alpha_val, blur_radius, out_name):
    raw = Image.open(img_breach_path).convert("RGBA")
    im = raw.resize((W_916, H_916), Image.LANCZOS)
    
    center_y = 960
    
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
    
    gap_eye_to_title = 28
    gap_title_lines  = 16
    gap_title_to_tag = 32
    
    total_content_h = eye_box_h + gap_eye_to_title + h_l1 + gap_title_lines + h_l2 + gap_title_to_tag + tag_box_h
    start_y = center_y - total_content_h // 2
    
    # Measure the main title lines block specifically:
    title_block_y0 = start_y + eye_box_h + gap_eye_to_title
    title_block_y1 = title_block_y0 + h_l1 + gap_title_lines + h_l2
    title_block_h  = title_block_y1 - title_block_y0
    title_block_center_y = (title_block_y0 + title_block_y1) // 2
    
    # The translucent dark background specifically behind the title with equal symmetrical padding:
    title_pad_y = 38
    title_pad_x = 48
    bg_w = max(w_l1, w_l2) + title_pad_x * 2
    bg_h = title_block_h + title_pad_y * 2
    
    bg_x0 = (W_916 - bg_w) // 2
    bg_x1 = bg_x0 + bg_w
    bg_y0 = title_block_center_y - bg_h // 2
    bg_y1 = title_block_center_y + bg_h // 2
    
    # Dark translucent backing layer (soft rounded rectangle with subtle blur, NO border stroke)
    halo = Image.new("RGBA", (W_916, H_916), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(halo)
    hdraw.rounded_rectangle(
        [bg_x0, bg_y0, bg_x1, bg_y1],
        radius=20,
        fill=(0, 0, 0, alpha_val)
    )
    if blur_radius > 0:
        halo = halo.filter(ImageFilter.GaussianBlur(blur_radius))
    im = Image.alpha_composite(im, halo)
    draw = ImageDraw.Draw(im)
    
    # Draw Eyebrow Badge
    eye_center_x = W_916 // 2
    eye_box_y0 = start_y
    eye_box_y1 = start_y + eye_box_h
    eye_center_y = (eye_box_y0 + eye_box_y1) // 2
    draw.rounded_rectangle(
        [eye_center_x - eye_box_w // 2, eye_box_y0, eye_center_x + eye_box_w // 2, eye_box_y1],
        radius=10, fill=(12, 12, 12, 220), outline=(255, 215, 0, 240), width=2
    )
    draw.text((eye_center_x, eye_center_y), eyebrow_text, font=font_eye, fill=(255, 215, 0, 255), anchor="mm")
    
    # Draw Main Title Lines (mathematically centered inside the dark background)
    l1_y = title_block_y0 + h_l1 // 2
    l2_y = title_block_y0 + h_l1 + gap_title_lines + h_l2 // 2
    draw.text((W_916 // 2, l1_y), line1_text, font=font_title, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((W_916 // 2, l2_y), line2_text, font=font_title, fill=(255, 215, 0, 255), anchor="mm")
    
    # Draw Tagline Badge
    tag_center_x = W_916 // 2
    tag_box_y0 = title_block_y1 + gap_title_to_tag
    tag_box_y1 = tag_box_y0 + tag_box_h
    tag_center_y = (tag_box_y0 + tag_box_y1) // 2
    draw.rounded_rectangle(
        [tag_center_x - tag_box_w // 2, tag_box_y0, tag_center_x + tag_box_w // 2, tag_box_y1],
        radius=12, fill=(14, 14, 14, 225), outline=(255, 215, 0, 240), width=2
    )
    draw.text((tag_center_x, tag_center_y), tagline_text, font=font_tag, fill=(255, 255, 255, 255), anchor="mm")
    
    out_path = os.path.join(ARTIFACTS, out_name)
    im.convert("RGB").save(out_path, quality=97)
    return out_path, im

def main():
    # Generate 3 distinct calibrated levels:
    # Level A: 78% opacity (Smooth cinematic dark halo)
    p_a, im_a = render_calibrated_variant(195, 10, "thumb_opacity_78pct.jpg")
    # Level B: 85% opacity (Rich dark translucent backing - ideal contrast & texture)
    p_b, im_b = render_calibrated_variant(215, 10, "thumb_opacity_85pct.jpg")
    # Level C: 90% opacity (Deep bold dark glass)
    p_c, im_c = render_calibrated_variant(230, 8, "thumb_opacity_90pct.jpg")
    
    # Also generate the 1:1 crops for all three
    for p, im in [(p_a, im_a), (p_b, im_b), (p_c, im_c)]:
        crop_1x1 = im.crop((0, 450, 1080, 1470 + 60))
        crop_1x1.resize((1080, 1080), Image.LANCZOS).convert("RGB").save(p.replace(".jpg", "_1x1.jpg"), quality=97)
        
    # Build a 3-way calibration sheet
    target_w, target_h = 560, 996
    s_a = im_a.resize((target_w, target_h), Image.LANCZOS)
    s_b = im_b.resize((target_w, target_h), Image.LANCZOS)
    s_c = im_c.resize((target_w, target_h), Image.LANCZOS)
    
    sheet_w = 40 + (target_w + 30) * 3 + 10
    sheet_h = target_h + 180
    sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 14, 14))
    draw = ImageDraw.Draw(sheet)
    
    f_head = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), 48)
    f_sub = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
    f_col = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 28)
    
    draw.text((sheet_w // 2, 40), "TRANSPARENCY CALIBRATION SHEET", font=f_head, fill=(255, 215, 0), anchor="mm")
    draw.text((sheet_w // 2, 80), "All 3 variants feature mathematically centered typography, enlarged text, and zero hard box edges", font=f_sub, fill=(210, 210, 210), anchor="mm")
    
    cols = [
        ("VARIANT A: 78% Opacity (Medium Translucent)", s_a, 40),
        ("VARIANT B: 85% Opacity (Rich Balanced - Recommended)", s_b, 40 + target_w + 30),
        ("VARIANT C: 90% Opacity (Deep Bold Glass)", s_c, 40 + (target_w + 30) * 2),
    ]
    for label, img, x in cols:
        draw.text((x + target_w // 2, 120), label, font=f_col, fill=(255, 255, 255), anchor="mm")
        sheet.paste(img, (x, 145))
        draw.rectangle([x, 145, x + target_w, 145 + target_h], outline=(255, 215, 0) if "85%" in label else (70, 70, 70), width=2)
        
    sheet_path = os.path.join(ARTIFACTS, "transparency_calibration_sheet.jpg")
    sheet.save(sheet_path, quality=96)
    print("Saved calibration sheet:", sheet_path)

if __name__ == "__main__":
    main()
