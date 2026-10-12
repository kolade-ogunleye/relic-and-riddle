"""
size_preview.py
Generates a 1080x1920 image showing Anton at multiple sizes
so you can pick the right one without rendering a full video.
Shows the actual pill-box caption style exactly as it appears in the video.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

FONTS_DIR  = "fonts"
BG_SCENE   = "assets/scene2_tunnel.jpg"
OUT_PATH   = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57\size_preview.jpg"

W, H = 1080, 1920

COL_GOLD   = (255, 215,   0)
COL_WHITE  = (255, 255, 255)
COL_GRAY   = (145, 145, 145)
COL_PILL   = (16,  16,  16, 210)
COL_LABEL  = (200, 200, 200, 255)

# Sizes to compare — current V7 + bigger options
SIZES = [56, 68, 80, 100]

# Sample phrase exactly as it appears mid-video (3 words, word 2 is active)
WORDS = ["WHAT", "HE", "STUMBLED"]

def draw_caption(draw, overlay_draw, font, words, active_idx, y_center):
    parts = []
    for i, w in enumerate(words):
        col = COL_GOLD if i == active_idx else (COL_WHITE if i < active_idx else COL_GRAY)
        parts.append((w, col))

    full = " ".join(w for w, _ in parts)
    bb = draw.textbbox((0, 0), full, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]

    px, py = 44, 20
    x0 = (W - tw) // 2 - px
    y0 = y_center - th // 2 - py
    x1 = (W - tw) // 2 + tw + px
    y1 = y_center + th // 2 + py
    overlay_draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=COL_PILL)

    x = (W - tw) // 2
    for i, (word, col) in enumerate(parts):
        draw.text((x, y_center - th // 2), word, font=font, fill=col)
        wb = draw.textbbox((x, y_center - th // 2), word, font=font)
        x = wb[2]
        if i < len(parts) - 1:
            sb = draw.textbbox((x, 0), " ", font=font)
            x += sb[2] - sb[0]

def main():
    # Background
    bg = Image.open(BG_SCENE).convert("RGBA")
    bw, bh = bg.size
    ratio = W / H
    if bw / bh > ratio:
        nh, nw = H, int(H * bw / bh)
    else:
        nw, nh = W, int(W * bh / bw)
    bg = bg.resize((nw, nh), Image.LANCZOS)
    bg = bg.crop(((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H))
    bg = bg.filter(ImageFilter.GaussianBlur(1.2))

    # Dark overlay
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 140))
    bg = Image.alpha_composite(bg, dark)

    # Label font
    try:
        label_font = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 34)
        small_font = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
    except:
        label_font = ImageFont.load_default()
        small_font = label_font

    anton_path = os.path.join(FONTS_DIR, "Anton-Regular.ttf")

    # Title
    draw = ImageDraw.Draw(bg)
    draw.text((W // 2, 80), "ANTON — SIZE COMPARISON", font=label_font,
              fill=COL_GOLD, anchor="mm")
    draw.text((W // 2, 128), "Same phrase  |  Same pill style  |  Same 1080x1920 canvas",
              font=small_font, fill=(180, 180, 180, 255), anchor="mm")
    draw.line([(80, 158), (W - 80, 158)], fill=(255, 215, 0, 150), width=2)

    # Evenly space the 4 size examples vertically
    spacing = (H - 220) // (len(SIZES) + 1)
    for idx, size in enumerate(SIZES):
        y = 180 + spacing * (idx + 1)
        font = ImageFont.truetype(anton_path, size)

        # Draw pill on overlay
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        draw_caption(draw, ov_draw, font, WORDS, active_idx=1, y_center=y)
        bg = Image.alpha_composite(bg, overlay)
        draw = ImageDraw.Draw(bg)

        # Size label (left side, vertically centred on caption)
        label = f"{size}pt"
        marker = "  <-- V7 current" if size == 56 else ""
        draw.text((60, y), f"{label}{marker}", font=label_font,
                  fill=COL_GOLD if size == 100 else COL_LABEL, anchor="lm")

        # Divider (except after last)
        if idx < len(SIZES) - 1:
            div_y = y + spacing // 2
            draw.line([(80, div_y), (W - 80, div_y)], fill=(60, 60, 60, 180), width=1)

    # Footer note
    draw.line([(80, H - 110), (W - 80, H - 110)], fill=(255, 215, 0, 120), width=2)
    draw.text((W // 2, H - 72), "Active word = GOLD  |  Past = WHITE  |  Future = GRAY",
              font=small_font, fill=(160, 160, 160, 255), anchor="mm")
    draw.text((W // 2, H - 38), "Pill box background exactly as rendered in video",
              font=small_font, fill=(120, 120, 120, 255), anchor="mm")

    bg.convert("RGB").save(OUT_PATH, quality=96)
    print(f"Saved -> {OUT_PATH}")

if __name__ == "__main__":
    main()
