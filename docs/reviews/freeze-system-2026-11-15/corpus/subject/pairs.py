"""base | tip pairs: covers (reel + profile-grid tile), slides / Story frames; plus pixel identity of tip-without-box."""
import json, sys, hashlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
H = Path(__file__).resolve().parent
font = ImageFont.truetype("arial.ttf", 28)
def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def pair(a, b, out, label, scale=0.5):
    A, B = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    A = A.resize((int(A.width * scale), int(A.height * scale))); B = B.resize((int(B.width * scale), int(B.height * scale)))
    W = A.width + B.width + 30; Hh = max(A.height, B.height) + 40
    im = Image.new("RGB", (W, Hh), (25, 25, 25)); im.paste(A, (0, 40)); im.paste(B, (A.width + 30, 40))
    d = ImageDraw.Draw(im); d.text((6, 4), "BASE  " + label, fill="white", font=font); d.text((A.width + 36, 4), "TIP (oracle)", fill="white", font=font)
    im.save(out, quality=85)
out = H / "pairs"; out.mkdir(exist_ok=True)
same = diff = 0; changed = []
for sub in ("covers", "decks"):
    for f in sorted((H / "base" / sub).rglob("*.jpg")):
        rel = f.relative_to(H / "base")
        t, n = H / "tip" / rel, H / "tip_nobox" / rel
        if n.is_file():
            if h(n) == h(f): same += 1
            else: diff += 1; print("NOBOX DIFFERS", rel)
        if t.is_file() and h(t) != h(f):
            changed.append(str(rel))
            name = str(rel).replace("\\", "__").replace("/", "__")
            pair(f, t, out / name, str(rel), 0.5 if "grid" not in name else 1.0)
print("no-box identical", same, "differ", diff)
print("changed with oracle", len(changed)); [print("  ", c) for c in changed]
