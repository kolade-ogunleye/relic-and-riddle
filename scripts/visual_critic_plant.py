"""QF-136: planted defects for the visual critic (scripts/visual_critic.py plant / score).

Each planted case is a copy of a real frame of a render set (or of its caption file, or of its record) with one
known defect of one class, written to a new set with planted.json naming the class and the frame. Pixels are planted
where the critic judges pixels; record-basis classes (mark_contrast, record_mismatch) are planted in the record:

- text_identity (GLYPH-DROP): the frame drawn again by its own renderer from its recipe with one letter taken out of
  a field's longest word; the truth keeps the field whole.
- record_mismatch: the record's box of a found field moved 60-150 px; the pixels unchanged.
- text_cut: the picture shifted sideways so a found field's word crosses the frame edge (the record moved with it).
- safe_zone: the picture shifted up or down so a found field leaves its zone, still inside the frame.
- overlap: a field set down until its foot covers the top of the next field below it (the vacated strip inpainted).
- contrast: the ground around a found field blended to the field's own ink tone.
- plate_overrun: caption files whose badge or caption text is lengthened past its plate.
- glyph_fallback: caption files whose caption style names a family no font carries.
- mark_contrast: a graphic's recorded essential mark set under 3:1.
- face_overlap: a real face (from a real run's photos) put under a found field, its glyphs kept on top.
"""
from __future__ import annotations

import itertools
import json
import re
import shutil
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image


def _write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _load(p: Path) -> np.ndarray:
    with Image.open(p) as im:
        return np.asarray(im.convert("RGB")).copy()


def _found_fields(entry: dict) -> list[dict]:
    return [r for r in entry.get("fields", []) if r.get("box") and all(
        w.get("ncc") is not None and w["ncc"] >= 0.6 for w in r["words"])]


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    h, w = img.shape[:2]
    out = np.empty_like(img)
    ys = np.clip(np.arange(h) - dy, 0, h - 1)
    xs = np.clip(np.arange(w) - dx, 0, w - 1)
    out[:] = img[ys][:, xs]
    return out


def _shift_record(rec: dict, dx: int, dy: int) -> dict:
    rec = json.loads(json.dumps(rec))
    for e in rec.get("elements") or []:
        if e.get("box"):
            b = e["box"]
            e["box"] = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
    return rec


def _ink(img: np.ndarray) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return np.abs(g.astype(np.int16) - cv2.medianBlur(g, 31).astype(np.int16)) > 35


def plant(set_dir: Path, base_report: Path, out: Path, per_class: int = 10, faces_from: Path | None = None) -> int:
    """Writes `out` (a new folder) with the planted cases and planted.json; returns 0. `faces_from`: a checkout whose
    output/run_*/beat_images give the real faces planted (None: no face cases)."""
    import cv2

    from src import safe_zones
    set_dir, out = Path(set_dir).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    report = json.loads(Path(base_report).read_text(encoding="utf-8"))
    pictures = []
    for e in report["frames"]:
        if e["kind"] not in ("still", "card"):
            continue
        rec_p = (set_dir / e["frame"]).with_suffix(".record.json")
        if rec_p.is_file() and _found_fields(e):
            pictures.append((e, rec_p))
    cases: list[dict] = []

    def emit(cls: str, k: int, src_entry: dict, img: np.ndarray | None, rec: dict, detail: str, png_src: Path | None = None):
        name = f"{cls}__{k:02d}__{Path(src_entry['frame']).stem}"
        d = out / cls
        d.mkdir(parents=True, exist_ok=True)
        png = d / f"{name}.png"
        if img is not None:
            Image.fromarray(img).save(png)
        else:
            shutil.copyfile(png_src, png)
        rec = dict(rec)
        rec["frame"] = png.name
        _write_json(png.with_suffix(".record.json"), rec)
        cases.append({"class": cls, "frame": png.relative_to(out).as_posix(), "from": src_entry["frame"], "detail": detail})

    def rec_of(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    # text_identity: the copy changed and redrawn by the renderer itself, the truth kept (GLYPH-DROP and its kin).
    # Each mutation in turn: a letter dropped (first, middle, last), a letter changed, two transposed, a short word
    # dropped, two words swapped, a word added, a punctuation sign or accent dropped, a digit dropped or changed.
    from src.multi_format import MultiFormatGenerator
    from src.visual_critic import CAPS_BY_DESIGN, truth_words
    gen = MultiFormatGenerator()

    def mutate(kind: str, text: str):
        words = truth_words(text)
        letters = [w for w in words if sum(c.isalpha() for c in w) >= 4]
        if kind in ("drop_first", "drop_middle", "drop_last", "change", "transpose") and letters:
            w = letters[0] if kind != "drop_middle" else max(letters, key=len)
            i = {"drop_first": 0, "drop_last": len(w) - 1}.get(kind, len(w) // 2)
            while not w[i].isalpha():
                i = (i + 1) % len(w)
            if kind == "change":
                c = w[i]
                n = chr((ord(c.upper()) - 64) % 26 + 65)
                new = w[:i] + (n if c.isupper() else n.lower()) + w[i + 1:]
            elif kind == "transpose":
                j = min(i + 1, len(w) - 1) if w[min(i + 1, len(w) - 1)] != w[i] else max(0, i - 1)
                a_, b_ = sorted((i, j))
                new = w[:a_] + w[b_] + w[a_] + w[b_ + 1:]
            else:
                new = w[:i] + w[i + 1:]
            return text.replace(w, new, 1), f"{w!r} drawn as {new!r}"
        if kind == "drop_short_word":
            short = [w for w in words if len(w) <= 3 and len(words) > 1]
            if short:
                return " ".join(x for k_, x in enumerate(words) if k_ != words.index(short[0])), f"{short[0]!r} dropped"
        if kind == "swap_words" and len(words) >= 2 and words[0] != words[1]:
            return " ".join([words[1], words[0]] + words[2:]), f"{words[0]!r} and {words[1]!r} swapped"
        if kind == "add_word" and words:
            return text + " " + max(words, key=len), f"{max(words, key=len)!r} added at the end"
        if kind == "drop_punct":
            k_ = next((i for i, c in enumerate(text) if not c.isalnum() and not c.isspace()), None)
            if k_ is not None:
                return text[:k_] + text[k_ + 1:], f"{text[k_]!r} dropped"
        if kind == "drop_accent":
            k_ = next((i for i, c in enumerate(text) if c.isalpha() and unicodedata.normalize("NFD", c) != c), None)
            if k_ is not None:
                return text[:k_] + unicodedata.normalize("NFD", text[k_])[0] + text[k_ + 1:], f"accent of {text[k_]!r} dropped"
        if kind == "prefix" and words:
            return "NOT " + text, "'NOT' added before the field"
        if kind == "case" and any(c.islower() for c in text):
            return text.upper(), "set in capitals"
        if kind == "add_accent":
            k_ = next((i for i, c in enumerate(text) if c in "AEIOUaeiou"), None)
            if k_ is not None:
                acc = {"A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú",
                       "a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú"}[text[k_]]
                return text[:k_] + acc + text[k_ + 1:], f"accent added to {text[k_]!r}"
        if kind in ("drop_digit", "change_digit"):
            k_ = next((i for i, c in enumerate(text) if c.isdigit()), None)
            if k_ is not None:
                if kind == "drop_digit":
                    return text[:k_] + text[k_ + 1:], f"digit {text[k_]!r} dropped"
                return text[:k_] + str((int(text[k_]) + 3) % 10) + text[k_ + 1:], f"digit {text[k_]!r} changed"
        return None

    def redraw(r: dict, src: dict):
        if r.get("kind") == "story":
            im, _ = gen._render_story_frame(Path(r["photo"]), src, int(r["index"]), int(r["count"]), r.get("poll") or {},
                                            tight=bool(r.get("tight")))
        else:
            im, _ = gen._render_carousel_slide(r["archetype"], Path(r["photo"]), Path(r["photo_next"]), src,
                                               int(r["index"]), int(r["total"]), r.get("poll") or {}, r.get("share") or "",
                                               bool(r.get("facebook")), tight=bool(r.get("tight")),
                                               subject=r.get("subject") or "", heading_frame=int(r.get("heading_frame") or 2))
        return np.asarray(im.convert("RGB"))

    MUTATIONS = ("drop_middle", "drop_first", "drop_last", "change", "transpose", "drop_short_word", "swap_words",
                 "add_word", "drop_punct", "drop_accent", "drop_digit", "change_digit", "prefix", "case", "add_accent")
    k = 0
    for kind in MUTATIONS:
        made = 0
        for e, rp in pictures:
            if made >= max(1, per_class // 2):
                break
            rec = rec_of(rp)
            r = rec.get("recipe")
            if not r:
                continue
            key = "slide" if r.get("kind") == "story" else "card"
            src = dict(r.get(key) or {})
            for fd in rec.get("expect") or []:
                if fd["field"] not in src or src[fd["field"]] != fd["text"]:
                    continue
                if kind == "case" and fd["field"] in CAPS_BY_DESIGN:
                    continue             # the design sets this field in capitals: the change draws nothing new
                m = mutate(kind, fd["text"])
                if m is None or m[0] == fd["text"]:
                    continue
                src[fd["field"]] = m[0]
                try:
                    img = redraw(r, src)
                except Exception as ex:  # noqa: BLE001  a refused redraw is skipped (it is not a planted defect)
                    print("skip", kind, e["frame"], ex)
                    break
                emit("text_identity", k, e, img, rec, f"{fd['field']}: {m[1]}; the truth keeps {fd['text']!r}")
                cases[-1]["mutation"] = kind
                k += 1
                made += 1
                break

    # coverage: a record that gives no truth (its expect and recipe gone): judged, never a pass
    for k, (e, rp) in enumerate(pictures[:per_class]):
        rec = {key_: v for key_, v in rec_of(rp).items() if key_ not in ("expect", "recipe")}
        emit("coverage", k, e, None, rec, "the record gives no truth", png_src=set_dir / e["frame"])

    # record_mismatch: the record moved, the pixels not
    for k, (e, rp) in enumerate(pictures[:per_class]):
        rec = rec_of(rp)
        fld = _found_fields(e)[k % len(_found_fields(e))]
        fb = fld["box"]
        best = max((el for el in rec["elements"] if el.get("box")), key=lambda el: _overlap(el["box"], fb), default=None)
        if best is None:
            continue
        d = 60 + 10 * k
        best["box"] = [best["box"][0], best["box"][1] + d, best["box"][2], best["box"][3] + d]
        for el in rec["elements"]:
            if el is not best and el.get("box") and _overlap(el["box"], fb) > 0:
                el["box"] = [el["box"][0], el["box"][1] + d, el["box"][2], el["box"][3] + d]
        emit("record_mismatch", k, e, None, rec, f"record box of {best['name']} moved {d} px down",
             png_src=set_dir / e["frame"])

    # text_cut: a word over the left or right edge
    for k, (e, rp) in enumerate(pictures[:per_class]):
        img, rec = _load(set_dir / e["frame"]), rec_of(rp)
        fld = _found_fields(e)[0]
        w = img.shape[1]
        words = [x for x in fld["words"] if x.get("box")]
        if k % 2 == 0:
            wb = min(words, key=lambda x: x["box"][0])["box"]
            dx = -(wb[0] + (wb[2] - wb[0]) // 2)            # the first word's middle on the left edge
        else:
            wb = max(words, key=lambda x: x["box"][2])["box"]
            dx = w - (wb[0] + (wb[2] - wb[0]) // 2)
        emit("text_cut", k, e, _shift(img, dx, 0), _shift_record(rec, dx, 0), f"{fld['field']} shifted {dx} px")

    # safe_zone: a field out of its zone (inside the frame)
    k = 0
    for e, rp in pictures:
        if k >= per_class:
            break
        img, rec = _load(set_dir / e["frame"]), rec_of(rp)
        h = img.shape[0]
        zone = rec.get("zone") or (list(safe_zones.safe_box("story", img.shape[1], h)) if h > img.shape[1] else None)
        if not zone:
            continue
        flds = sorted(_found_fields(e), key=lambda r: r["box"][1])
        top, bot = flds[0]["box"], flds[-1]["box"]
        if k % 2 == 0 and top[1] - (zone[1] - 40) > 0 and zone[1] - 40 > 0:
            dy = (zone[1] - 40) - top[1]
        elif bot[3] < zone[3] and zone[3] + 40 < h:
            dy = (zone[3] + 40) - bot[3]
        else:
            continue
        rec["zone"] = zone
        emit("safe_zone", k, e, _shift(img, 0, dy), _shift_record(rec, 0, dy), f"shifted {dy} px past the zone {zone}")
        k += 1

    # overlap: one field's pixels moved onto the next
    k = 0
    for e, rp in pictures:
        if k >= per_class:
            break
        flds = sorted(_found_fields(e), key=lambda r: r["box"][1])
        pairs = [(p, q) for p, q in itertools.pairwise(flds)              # one above the next, in the same column
                 if min(p["box"][2], q["box"][2]) - max(p["box"][0], q["box"][0]) > 40]
        if not pairs:
            continue
        a_, b_ = pairs[0][0]["box"], pairs[0][1]["box"]
        img = _load(set_dir / e["frame"])
        crop = img[max(0, a_[1]):a_[3], max(0, a_[0]):a_[2]].copy()
        mask = np.zeros(img.shape[:2], np.uint8)
        mask[max(0, a_[1] - 4):a_[3] + 4, max(0, a_[0] - 4):a_[2] + 4] = 255
        img = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)
        # the upper field set down until its foot covers the top 40% of the next field (a leading collapse)
        ny = b_[1] + int(0.4 * (b_[3] - b_[1])) - crop.shape[0]
        ny = max(0, min(img.shape[0] - crop.shape[0], ny))
        nx = max(0, a_[0])
        img[ny:ny + crop.shape[0], nx:nx + crop.shape[1]] = crop
        flds = [pairs[0][0], pairs[0][1]]
        emit("overlap", k, e, img, rec_of(rp), f"{flds[0]['field']} set down onto {flds[1]['field']} (y {ny})")
        k += 1

    # contrast: the ground blended to the ink's own tone
    for k, (e, rp) in enumerate(pictures[:per_class]):
        img = _load(set_dir / e["frame"]).astype(np.float32)
        fld = _found_fields(e)[k % len(_found_fields(e))]
        b = fld["box"]
        x0, y0, x1, y1 = max(0, b[0] - 14), max(0, b[1] - 14), min(img.shape[1], b[2] + 14), min(img.shape[0], b[3] + 14)
        reg = img[y0:y1, x0:x1]
        lum = reg @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        tone = reg.reshape(-1, 3)[int(np.argsort(lum.reshape(-1))[int(0.97 * lum.size)])]
        img[y0:y1, x0:x1] = reg * 0.3 + tone * 0.7
        emit("contrast", k, e, np.clip(img, 0, 255).astype(np.uint8), rec_of(rp), f"ground of {fld['field']} blended to {tone.tolist()}")

    # face_overlap: a real face under a field, the glyphs kept on top. Only a face the pipeline's model sees where
    # it is pasted counts as planted (the detector's own recall is YuNet's, not the critic's): faces and sizes are
    # tried in turn until one is seen there.
    faces = _real_faces(per_class, faces_from) if faces_from else []
    det = None
    if faces:
        from src.visual_critic import FaceDetector
        det = FaceDetector(device="cpu")
    k = 0
    for e, rp in (pictures if faces else []):
        if k >= per_class:
            break
        fld = _found_fields(e)[-1]                     # the lowest field: the face goes under its foot
        b = fld["box"]
        done = False
        for j in range(len(faces) * 2):
            face = faces[(k + j) % len(faces)]
            side = int(min(480, max(200, (1.2 if j % 2 == 0 else 1.6) * (b[3] - b[1]))))
            img = _load(set_dir / e["frame"])
            fimg = np.asarray(Image.fromarray(face).resize((side, side), Image.LANCZOS))
            x0 = max(0, min(img.shape[1] - side, (b[0] + b[2]) // 2 - side // 2))
            # the crop holds the face in its middle 60% (a third of the face's width round it): the field's foot
            # over the face's top 30%
            y0 = max(0, min(img.shape[0] - side, b[3] - int(0.38 * side)))
            ink = _ink(img)[y0:y0 + side, x0:x0 + side]
            reg = img[y0:y0 + side, x0:x0 + side]
            keep = reg.copy()
            reg[:] = fimg
            reg[ink] = keep[ink]
            seen = [f for f in det.detect(img) if _overlap(f["box"], (x0, y0, x0 + side, y0 + side)) > 0]
            if seen:
                emit("face_overlap", k, e, img, rec_of(rp), f"face {side}px at {[x0, y0]} under {fld['field']} (seen "
                     f"by the model at {seen[0]['box']})")
                done = True
                break
        k += int(done)

    # caption classes: plate_overrun and glyph_fallback planted in copies of caption files
    asses = sorted(p for p in (set_dir / "captions").glob("*.ass")) if (set_dir / "captions").is_dir() else []
    for cls in ("plate_overrun", "glyph_fallback"):
        for k in range(min(per_class, 2 * len(asses))):
            src = asses[k % len(asses)]
            text = src.read_text(encoding="utf-8")
            if cls == "glyph_fallback":
                new = re.sub(r"^(Style: CapText,)([^,]*)", lambda m: m.group(1) + m.group(2) + " Gone", text, count=1,
                             flags=re.MULTILINE)
                detail = "CapText names a family no font carries"
            else:
                pad = " WWWWWWWWWW" if k < len(asses) else " MMMMMMMMMMMMMM"
                # every caption line lengthened (a badge may already overrun its plate: QF-133)
                new = re.sub(r"^(Dialogue:[^\n]*,CapText,[^\n]*?)$", lambda m, pad=pad: m.group(1) + pad
                             if "\\1a&HFF&" not in m.group(1) else m.group(1), text, flags=re.MULTILINE)
                detail = f"caption text lengthened by {pad!r}"
            d = out / cls
            d.mkdir(parents=True, exist_ok=True)
            dst = d / f"{cls}__{k:02d}__{src.name}"
            dst.write_text(new, encoding="utf-8", newline="\n")
            w = src.with_name(src.stem + ".words.json")
            if w.is_file():
                shutil.copyfile(w, dst.with_name(dst.stem + ".words.json"))
            cases.append({"class": cls, "frame": dst.relative_to(out).as_posix(), "from": f"captions/{src.name}",
                          "detail": detail})

    # mark_contrast: a graphic's recorded mark under 3:1 (record basis)
    graphics = sorted(set_dir.rglob("*.mp4.contrast.json"))       # a graphic's record (not a caption file's)
    for k in range(per_class if graphics else 0):
        cj = graphics[k % len(graphics)]
        rec = json.loads(cj.read_text(encoding="utf-8"))
        rec.setdefault("nontext", []).append({"name": "planted_mark", "drawn": 2.1, "graded": 2.1})
        d = out / "mark_contrast" / f"{k:02d}"
        d.mkdir(parents=True, exist_ok=True)
        stem = cj.name[: -len(".mp4.contrast.json")]
        _write_json(d / cj.name, rec)
        for ext in (".png", ".expect.json"):
            if (cj.parent / f"{stem}{ext}").is_file():
                shutil.copyfile(cj.parent / f"{stem}{ext}", d / f"{stem}{ext}")
        cases.append({"class": "mark_contrast", "frame": (d / f"{stem}.png").relative_to(out).as_posix(),
                      "from": cj.relative_to(set_dir).as_posix(), "detail": "recorded mark at 2.1:1"})
    _write_json(out / "planted.json", cases)
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    print(json.dumps(counts, indent=1))
    return 0


def _overlap(a, b) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _real_faces(n: int, dev: Path) -> list[np.ndarray]:
    """Faces from real run photos (the dev checkout's output/run_*/beat_images), found by the pipeline's face model."""
    from src.visual_critic import FaceDetector
    try:
        det = FaceDetector(device="cpu")
    except FileNotFoundError:
        return []
    out = []
    for p in sorted((dev / "output").glob("run_*/beat_images/*.jpg")):
        try:
            img = _load(p)
        except OSError:
            continue
        for f in det.detect(img):
            x0, y0, x1, y1 = f["box"]
            if f["confidence"] < 0.8 or min(x1 - x0, y1 - y0) < 80:
                continue
            m = (x1 - x0) // 3
            out.append(img[max(0, y0 - m):y1 + m, max(0, x0 - m):x1 + m].copy())
        if len(out) >= n:
            break
    return out


def score(planted_set: Path, planted_report: Path, base_report: Path) -> int:
    """Recall: a planted case is caught when its frame has more findings of its class than the frame it was copied
    from. Findings on the unplanted set are listed per class (reviewed by hand: true defect or false)."""
    cases = json.loads((Path(planted_set) / "planted.json").read_text(encoding="utf-8"))
    pr = {e["frame"]: e for e in json.loads(Path(planted_report).read_text(encoding="utf-8"))["frames"]}
    br = json.loads(Path(base_report).read_text(encoding="utf-8"))
    base = {e["frame"]: e for e in br["frames"]}
    recall: dict[str, list[int]] = {}
    missed = []
    for c in cases:
        e = pr.get(c["frame"])
        b = base.get(c["from"])
        n_new = sum(1 for d in (e or {}).get("findings", []) if d["class"] == c["class"])
        n_old = sum(1 for d in (b or {}).get("findings", []) if d["class"] == c["class"])
        hit = e is not None and n_new > n_old
        for key in [c["class"]] + ([f'{c["class"]}/{c["mutation"]}'] if c.get("mutation") else []):
            recall.setdefault(key, [0, 0, 0])
            recall[key][0] += int(hit)
            recall[key][1] += 1
            recall[key][2] += int(e is not None and not e["pass"])
        if not hit:
            missed.append({**c, "found": n_new, "base": n_old})
    out = {"recall": {k: {"caught": v[0], "gate_failed": v[2], "planted": v[1]} for k, v in sorted(recall.items())},
           "missed": missed,
           "base_findings": {c: v["findings"] for c, v in br["per_class"].items()},
           "base_frames": br["frame_count"], "base_frames_failed": br["frames_failed"]}
    print(json.dumps(out, indent=1))
    _write_json(Path(planted_report).parent / "score.json", out)
    return 0
