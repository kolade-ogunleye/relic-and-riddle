"""
size_screenshots.py
Generates one full 1080x1920 screenshot per font size,
caption positioned at the exact MarginV=430 baseline used in the video.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

FONTS_DIR  = "fonts"
BG_SCENE   = "assets/scene4_pillars.jpg"   # pillars — dramatic, good contrast
ARTIFACTS  = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"

W, H       = 1080, 1920
MARGIN_V   = 430          # same as video — pixels from bottom

COL_GOLD   = (255, 215,   0)
COL_WHITE  = (255, 255, 255)
COL_GRAY   = (145, 145, 145)
COL_PILL   = (16,  16,  16, 215)

SIZES      = [56, 68, 80, 100]
# Use a phrase that has a long middle word to stress-test line width
WORDS      = ["WHAT", "HE", "STUMBLED"]

def make_bg():
    bg = Image.open(BG_SCENE).convert("RGBA")
    bw, bh = bg.size
    if bw / bh > W / H:
        nh, nw = H, int(H * bw / bh)
    else:
        nw, nh = W, int(W * bh / bw)
    bg = bg.resize((nw, nh), Image.LANCZOS)
    bg = bg.crop(((nw-W)//2, (nh-H)//2, (nw-W)//2+W, (nh-H)//2+H))
    bg = bg.filter(ImageFilter.GaussianBlur(1.0))
    # Gradient overlay — dark bottom
    ov = Image.new("RGBA", (W, H), (0,0,0,0))
    d  = ImageDraw.Draw(ov)
    for y in range(H//2, H):
        a = int(180 * ((y - H//2) / (H//2)))
        d.line([(0,y),(W,y)], fill=(0,0,0,a))
    for y in range(H//2):
        a = int(120 * (1 - y/(H//2)))
        d.line([(0,y),(W,y)], fill=(0,0,0,a))
    return Image.alpha_composite(bg, ov)


def draw_caption_pill(img, font, words, active_idx):
    draw   = ImageDraw.Draw(img)
    parts  = []
    for i, w in enumerate(words):
        col = COL_GOLD if i==active_idx else (COL_WHITE if i<active_idx else COL_GRAY)
        parts.append((w, col))

    full = " ".join(w for w,_ in parts)
    bb   = draw.textbbox((0,0), full, font=font)
    tw, th = bb[2]-bb[0], bb[3]-bb[1]

    # Caption Y — same formula as video (MARGIN_V from bottom = distance from bottom to centre)
    y_center = H - MARGIN_V

    px, py = 44, 22
    x0 = (W-tw)//2 - px
    y0 = y_center - th//2 - py
    x1 = (W-tw)//2 + tw + px
    y1 = y_center + th//2 + py

    # Pill on separate overlay for alpha
    pill = Image.new("RGBA", (W,H), (0,0,0,0))
    pd   = ImageDraw.Draw(pill)
    pd.rounded_rectangle([x0,y0,x1,y1], radius=18, fill=COL_PILL)
    merged = Image.alpha_composite(img, pill)
    draw   = ImageDraw.Draw(merged)

    # Text words
    x = (W-tw)//2
    for i,(word,col) in enumerate(parts):
        draw.text((x, y_center-th//2), word, font=font, fill=col)
        wb = draw.textbbox((x, y_center-th//2), word, font=font)
        x  = wb[2]
        if i < len(parts)-1:
            sb = draw.textbbox((x,0)," ",font=font)
            x += sb[2]-sb[0]

    return merged, y_center, th


def draw_size_badge(img, size, y_center, th, is_current):
    draw = ImageDraw.Draw(img)
    try:
        badge_font = ImageFont.truetype(
            os.path.join(FONTS_DIR,"Montserrat-Variable.ttf"), 36)
    except:
        badge_font = ImageFont.load_default()

    label = f"{size}pt" + ("  (V7 current)" if is_current else "")
    col   = (255,215,0,255) if not is_current else (180,180,180,255)
    # Place label just above the pill
    draw.text((W//2, y_center - th//2 - 60), label,
              font=badge_font, fill=col, anchor="mm")
    return img


def main():
    anton = os.path.join(FONTS_DIR, "Anton-Regular.ttf")

    for size in SIZES:
        bg   = make_bg()
        font = ImageFont.truetype(anton, size)

        img, y_center, th = draw_caption_pill(bg, font, WORDS, active_idx=1)
        img = draw_size_badge(img, size, y_center, th, is_current=(size==56))

        out = os.path.join(ARTIFACTS, f"size_{size}pt.jpg")
        img.convert("RGB").save(out, quality=96)
        print(f"Saved {size}pt -> {out}")

    print("All done.")

if __name__ == "__main__":
    main()
