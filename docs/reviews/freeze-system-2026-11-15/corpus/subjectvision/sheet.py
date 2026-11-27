"""Half-size JPEG sheets of the named hard cases (and any ids given): each photograph with the labelled subject box
(green), the photo_subject record's box (orange), the existing look's not-subject regions (red: edge intrusion or
printed text, from the cached pool screen) and, when answers exist, the vision subject box (cyan) and its
not-subject boxes (magenta). Writes SV_SCRATCH/sheet_<n>.jpg.  python sheet.py [id ...]"""
import sys

from PIL import Image, ImageDraw, ImageOps

from sv_common import HARD, LABELS, SCRATCH, load_answers, photo_path, photo_subject_records, screen_verdict, yxyx

Image.MAX_IMAGE_PIXELS = None
TILE, COLS = 640, 4
BY_ID = {lab["id"]: lab for lab in LABELS}


def tile(lab, rec, ans):
    with Image.open(photo_path(lab)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((TILE, TILE))
    W, H = im.size
    d = ImageDraw.Draw(im)

    def box(b, colour, width=4):
        if b:
            d.rectangle([b[0] * W, b[1] * H, b[2] * W - 1, b[3] * H - 1], outline=colour, width=width)
    v = screen_verdict(lab) or {}
    box(yxyx(v.get("object_box")), (230, 40, 40))
    if v.get("text"):
        box(yxyx(v.get("box")), (230, 40, 40), 2)
    box((rec.get(lab["sha256"]) or {}).get("box"), (255, 150, 0), 3)
    box(lab["box"], (40, 220, 60), 4)
    sub = ((ans or {}).get("look") or {}).get("subject") or {}
    for ns in sub.get("not_subject") or []:
        box(ns["box"], (230, 0, 230), 2)
    box(sub.get("box"), (0, 220, 255), 3)
    t = Image.new("RGB", (TILE, TILE + 44), (24, 24, 24))
    t.paste(im, ((TILE - W) // 2, 0))
    d = ImageDraw.Draw(t)
    d.text((6, TILE + 4), f"{lab['id']} {lab['name'][:60]}", fill=(240, 240, 240))
    d.text((6, TILE + 22), f"{lab['kind']}: {HARD.get(lab['id'], '')} {sub.get('name', '')[:50]}", fill=(180, 180, 180))
    return t


def main():
    ids = sys.argv[1:] or list(HARD)
    rec, ans = photo_subject_records(), load_answers(SCRATCH / "answers_first.jsonl")
    per = COLS * 3
    for n in range(0, len(ids), per):
        chunk = ids[n:n + per]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * TILE, rows * (TILE + 44)), (0, 0, 0))
        for k, i in enumerate(chunk):
            sheet.paste(tile(BY_ID[i], rec, ans.get(i)), ((k % COLS) * TILE, (k // COLS) * (TILE + 44)))
        sheet = sheet.resize((sheet.width // 2, sheet.height // 2), Image.LANCZOS)
        out = SCRATCH / f"sheet_{n // per}.jpg"
        sheet.save(out, "JPEG", quality=85)
        print(out)


if __name__ == "__main__":
    main()
