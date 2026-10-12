"""
generate_thumbnail.py
=====================
Generates a professional, scroll-stopping Reel cover/thumbnail image.

Design principles:
- 1:1 square (1080×1080) for Facebook feed preview
- 9:16 (1080×1920) for Instagram Reel cover (with safe zone crop guides)
- Bold hook question text in Anton font — the #1 scroll-stop driver
- Dramatic dark background from the best archival scene
- Cinematic gradient overlay for text legibility
- Subtle branded frame / accent line
- Saves both the square and portrait versions
"""

import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
FONTS_DIR    = "fonts"
ASSETS_DIR   = "assets"
ARTIFACTS    = r"C:\Users\omokolade\.gemini\antigravity-cli\brain\4b4eb2b3-3526-4c80-80e3-820420a62e57"

# Best dramatic scene for the thumbnail background
BG_SCENE     = os.path.join(ASSETS_DIR, "scene2_tunnel.jpg")   # dark tunnel — most cinematic

# Hook text — the first thing viewers see before hitting play
HOOK_LINE1   = "HE FOUND A"
HOOK_LINE2   = "SECRET DOOR"
HOOK_LINE3   = "IN HIS BASEMENT"

# Sub-hook teaser (smaller text below)
SUB_TEXT     = "What lay beneath changed history forever."

# Accent colour — matches the gold active caption colour in the video
GOLD         = (255, 215,   0)
WHITE        = (255, 255, 255)
DARK_OVERLAY = (0,   0,   0,  170)   # semi-transparent dark gradient
ACCENT_LINE  = (255, 215,   0, 200)  # gold rule line

# Output paths
OUT_PORTRAIT = os.path.join(ARTIFACTS, "thumbnail_portrait.jpg")   # 1080×1920 Reel cover
OUT_SQUARE   = os.path.join(ARTIFACTS, "thumbnail_square.jpg")     # 1080×1080 Feed preview

# ─────────────────────────────────────────────────────────────────────────────
# FONT LOADER
# ─────────────────────────────────────────────────────────────────────────────
def get_font(name, size):
    paths = {
        "Anton":      os.path.join(FONTS_DIR, "Anton-Regular.ttf"),
        "Montserrat": os.path.join(FONTS_DIR, "Montserrat-Variable.ttf"),
    }
    p = paths.get(name)
    if p and os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────────────────────
# CORE THUMBNAIL BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def build_thumbnail(width, height, output_path, label=""):
    W, H = width, height

    # 1. Load + resize background
    bg = Image.open(BG_SCENE).convert("RGBA")
    # Fill the canvas — crop to centre
    bg_ratio  = bg.width / bg.height
    tgt_ratio = W / H
    if bg_ratio > tgt_ratio:
        new_h = H
        new_w = int(H * bg_ratio)
    else:
        new_w = W
        new_h = int(W / bg_ratio)
    bg = bg.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - W) // 2
    top  = (new_h - H) // 2
    bg   = bg.crop((left, top, left + W, top + H))

    # 2. Subtle blur — keeps background mysterious, text pops
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1.4))

    # 3. Gradient overlay — dark at top and bottom, lighter in the middle
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_ov  = ImageDraw.Draw(overlay)
    # Top gradient (dark → transparent)
    for y in range(H // 2):
        alpha = int(200 * (1 - y / (H // 2)))
        draw_ov.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    # Bottom gradient (transparent → very dark)
    for y in range(H // 2, H):
        alpha = int(230 * ((y - H // 2) / (H // 2)))
        draw_ov.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    bg = Image.alpha_composite(bg, overlay)
    draw = ImageDraw.Draw(bg)

    # 4. Gold accent rule — top
    rule_y = int(H * 0.08)
    draw.rectangle([(80, rule_y), (W - 80, rule_y + 4)], fill=GOLD + (210,))

    # 5. "HISTORY UNCOVERED" eyebrow label
    eyebrow_font = get_font("Montserrat", max(26, H // 60))
    eyebrow_text = "HISTORY UNCOVERED"
    ebb = draw.textbbox((0, 0), eyebrow_text, font=eyebrow_font)
    ew  = ebb[2] - ebb[0]
    draw.text(((W - ew) // 2, rule_y + 20), eyebrow_text,
              font=eyebrow_font, fill=GOLD + (255,),
              stroke_width=0)

    # 6. Main hook text — Anton, large, centred, with tight shadow
    hook_font_size = max(80, H // 10)    # scales with canvas
    hook_font      = get_font("Anton", hook_font_size)
    lines          = [HOOK_LINE1, HOOK_LINE2, HOOK_LINE3]

    # Measure total block height
    line_heights = []
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=hook_font)
        line_heights.append(bb[3] - bb[1])
    line_gap     = int(hook_font_size * 0.15)
    total_h      = sum(line_heights) + line_gap * (len(lines) - 1)
    start_y      = (H - total_h) // 2 - int(H * 0.04)   # slightly above centre

    y = start_y
    for i, (ln, lh) in enumerate(zip(lines, line_heights)):
        bb   = draw.textbbox((0, 0), ln, font=hook_font)
        lw   = bb[2] - bb[0]
        x    = (W - lw) // 2

        # Drop shadow
        shadow_offset = max(3, hook_font_size // 30)
        draw.text((x + shadow_offset, y + shadow_offset), ln,
                  font=hook_font, fill=(0, 0, 0, 200))

        # Main text — line 2 gets the gold treatment for maximum impact
        colour = GOLD if i == 1 else WHITE
        draw.text((x, y), ln, font=hook_font, fill=colour)

        y += lh + line_gap

    # 7. Sub-hook teaser
    sub_font = get_font("Montserrat", max(28, H // 52))
    sbb      = draw.textbbox((0, 0), SUB_TEXT, font=sub_font)
    sw       = sbb[2] - sbb[0]
    draw.text(((W - sw) // 2, y + int(H * 0.03)), SUB_TEXT,
              font=sub_font, fill=(210, 210, 210, 240))

    # 8. Bottom gold rule
    bot_rule_y = H - int(H * 0.06)
    draw.rectangle([(80, bot_rule_y), (W - 80, bot_rule_y + 4)], fill=GOLD + (180,))

    # 9. "WATCH TO THE END" call-to-action badge (bottom centre)
    cta_font = get_font("Anton", max(32, H // 42))
    cta_text = "WATCH TO THE END"
    cbb      = draw.textbbox((0, 0), cta_text, font=cta_font)
    cw       = cbb[2] - cbb[0]
    ch       = cbb[3] - cbb[1]
    cx       = (W - cw) // 2
    cy       = bot_rule_y + 12
    # Badge background
    pad = 16
    draw.rounded_rectangle(
        [cx - pad, cy - pad // 2, cx + cw + pad, cy + ch + pad // 2],
        radius=8, fill=(255, 215, 0, 230)
    )
    draw.text((cx, cy), cta_text, font=cta_font, fill=(0, 0, 0, 255))

    # 10. Safe zone guides overlay (visual only — dotted lines showing Instagram safe zone)
    if H == 1920:
        safe_top    = int(H * 0.12)
        safe_bottom = int(H * 0.88)
        # very subtle — just faint dashed lines
        dash_col = (255, 255, 255, 30)
        for x in range(0, W, 20):
            draw.point((x, safe_top),    fill=dash_col)
            draw.point((x, safe_bottom), fill=dash_col)

    # Save
    out = bg.convert("RGB")
    out.save(output_path, quality=97)
    print(f"[THUMB] {label} -> {output_path}")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  THUMBNAIL GENERATOR — Portrait + Square")
    print("=" * 60)
    build_thumbnail(1080, 1920, OUT_PORTRAIT, "Portrait 9:16 (Reel cover)")
    build_thumbnail(1080, 1080, OUT_SQUARE,   "Square 1:1 (Feed preview)")
    print("\nDone. Both thumbnails saved to artifacts.")


if __name__ == "__main__":
    main()
