"""
font_compare.py
Renders a single 1080×1920 comparison image showing Anton vs Montserrat
at the exact caption style used in the video (pill box, gold active word,
white past, gray future). No FFmpeg needed — uses Pillow directly.
"""

import os
from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = "fonts"
BG_IMAGE  = "assets/scene2_tunnel.jpg"   # dark scene for best caption visibility
OUT_PATH  = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57\font_compare.jpg"

W, H = 1080, 1920

# Caption colours
COL_ACTIVE = (255, 215,   0)   # gold
COL_PAST   = (255, 255, 255)   # white
COL_FUTURE = (145, 145, 145)   # gray
COL_PILL   = (16,  16,  16,  200)  # near-black, semi-transparent

def load_font(name, size):
    candidates = {
        "Anton":      os.path.join(FONTS_DIR, "Anton-Regular.ttf"),
        "Montserrat": os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"),
        "Bebas":      os.path.join(FONTS_DIR, "BebasNeue-Regular.ttf"),
    }
    path = candidates.get(name)
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_caption_row(draw, overlay, font, words, active_idx, y_center, pill_pad_x=40, pill_pad_y=18):
    """Draw a pill-boxed running-speech caption row centred at y_center."""
    # Measure total width
    coloured = []
    for i, w in enumerate(words):
        col = COL_ACTIVE if i == active_idx else (COL_PAST if i < active_idx else COL_FUTURE)
        coloured.append((w, col))

    # Build full string for bbox measurement
    full_text = " ".join(w for w, _ in coloured)
    bbox = draw.textbbox((0, 0), full_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x_start = (W - text_w) // 2
    pill_x0 = x_start - pill_pad_x
    pill_y0 = y_center - text_h // 2 - pill_pad_y
    pill_x1 = x_start + text_w + pill_pad_x
    pill_y1 = y_center + text_h // 2 + pill_pad_y

    # Draw pill on the overlay (for alpha)
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle([pill_x0, pill_y0, pill_x1, pill_y1], radius=18, fill=COL_PILL)

    # Draw each word in its colour
    x = x_start
    for i, (word, col) in enumerate(coloured):
        draw.text((x, y_center - text_h // 2), word, font=font, fill=col)
        word_bbox = draw.textbbox((x, y_center - text_h // 2), word, font=font)
        x = word_bbox[2]
        if i < len(coloured) - 1:
            space_bbox = draw.textbbox((x, 0), " ", font=font)
            x += space_bbox[2] - space_bbox[0]


def make_comparison():
    # Load background
    bg = Image.open(BG_IMAGE).convert("RGBA")
    bg = bg.resize((W, H), Image.LANCZOS)

    # Darken slightly for legibility
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 80))
    bg = Image.alpha_composite(bg, dark)

    # Label section line
    draw = ImageDraw.Draw(bg)
    try:
        label_font = ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 38)
    except Exception:
        label_font = ImageFont.load_default()

    words_sample = ["WHAT", "HE", "STUMBLED"]

    sections = [
        ("Anton  44pt", "Anton",      44, H // 3),
        ("Anton  50pt", "Anton",      50, H // 3 + 260),
        ("Montserrat  44pt", "Montserrat", 44, H // 3 * 2 - 80),
        ("Bebas Neue  44pt", "Bebas",      44, H // 3 * 2 + 180),
    ]

    for label, fname, fsize, y in sections:
        font = load_font(fname, fsize)

        # Draw pill overlay
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw_caption_row(draw, overlay, font, words_sample, active_idx=1, y_center=y)
        bg = Image.alpha_composite(bg, overlay)
        draw = ImageDraw.Draw(bg)  # refresh draw after composite

        # Label above
        draw.text((W // 2, y - fsize - 36), label, font=label_font,
                  fill=(220, 220, 220, 255), anchor="mm")

    # Divider lines
    for y in [H // 3 + 120, H // 3 * 2 - 260, H // 3 * 2 + 60]:
        draw.line([(80, y), (W - 80, y)], fill=(80, 80, 80, 200), width=2)

    # Title
    draw.text((W // 2, 120), "CAPTION FONT COMPARISON", font=label_font,
              fill=(255, 215, 0, 255), anchor="mm")
    draw.text((W // 2, 170), "Running-speech style  |  Gold=active  White=past  Gray=future",
              font=ImageFont.truetype(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"), 26)
                   if os.path.exists(os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"))
                   else label_font,
              fill=(180, 180, 180, 255), anchor="mm")

    out = bg.convert("RGB")
    out.save(OUT_PATH, quality=95)
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    make_comparison()
