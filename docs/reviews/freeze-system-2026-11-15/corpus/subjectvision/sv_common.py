"""Shared by the SUBJECT-VISION evaluation scripts (design freeze c6): the labelled set, photograph paths, the box
measures (the SUBJECT stream's own: corpus/subject/evalboxes.py, copied so these scripts run without its scratch-only
gt_select.json), the context each photograph is asked with, and the sets the hand-back names.

Paths: RR_DEV (default the dev checkout) for photographs, credits and run records; SV_SCRATCH (default
C:\\tmp\\qf-scratch-c6\\subjectvision) for requests, answers and sheets. Everything is read-only on RR_DEV."""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBJECT = HERE.parent / "subject"
RR_DEV = Path(os.environ.get("RR_DEV", r"C:\Users\omokolade\GitHub\relic-and-riddle"))
SCRATCH = Path(os.environ.get("SV_SCRATCH", r"C:\tmp\qf-scratch-c6\subjectvision"))
LABELS = json.load(open(SUBJECT / "subject_labels.json", encoding="utf-8"))
CUT = 0.85            # the SUBJECT stream's failure: the predicted box holds < 85 % of the labelled subject

# The named hard cases of the brief, by labelled id
HARD = {"059": "Richat 01", "062": "Richat 06 (Soyuz)", "065": "Richat 10", "067": "Richat 14", "068": "Richat 17",
        "072": "Roman 05 dome", "074": "Roman 09 dome", "076": "Roman 13 dome", "124": "Tunguska 10 map",
        "123": "Tunguska 09 figure", "096": "Aswan 13 tourist + obelisk", "114": "Antikythera 01 label board",
        "126": "Nazca 06 wing", "127": "Nazca 06 wing (upscaled copy)", "082": "Memnon 09 lithograph",
        "104": "Voynich 14", "086": "Trepanation 01"}
HARD.pop("059")       # not named in the brief (Richat 06/10/14/17 are)
# Panel 7's subject-dependent findings (queue/PANEL7-RESULT.md fix 5: cover inset, Nazca wing, Story hook credit,
# Aswan s07) fall on these topics' photographs; the run-to-photo map of the board's cases is not recorded per photo,
# so the set is every labelled photograph of those topics (the SUBJECT hand-back's "Richat, Memnon, Aswan, Nazca",
# plus Antikythera's near-white cover).
PANEL7_TOPICS = ("richat_structure_sahara_eye", "singing_colossi_of_memnon", "unfinished_obelisk_aswan",
                 "nazca_lines_runway_paradox", "antikythera_mechanism", "upscaled")


def panel7(label):
    return label["name"].split("/")[0] in PANEL7_TOPICS


def area(b): return max(0, b[2] - b[0]) * max(0, b[3] - b[1])
def inter(a, b): return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
def iou(a, b):
    i = inter(a, b); return i / max(1e-9, area(a) + area(b) - i)
def cover(gt, p): return inter(gt, p) / max(1e-9, area(gt))   # share of the subject inside the predicted box


def photo_path(label) -> Path:
    topic, name = label["name"].split("/", 1)
    if topic == "upscaled":
        return RR_DEV / "cache" / "upscaled" / name
    return RR_DEV / "assets" / topic / name


def _credits(topic):
    f = RR_DEV / "assets" / topic / "credits.json"
    try:
        return {i.get("file"): i for i in json.load(open(f, encoding="utf-8")).get("images", [])}
    except (OSError, ValueError):
        return {}


def _claims():
    """{"<folder>/<file name>": the card text of the first slide that drew it}, from every run's carousel_photos.json
    ("photos" is aligned with "key.cards")."""
    out = {}
    for f in sorted((RR_DEV / "output").glob("run_*/carousel_photos.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cards, photos = (d.get("key") or {}).get("cards") or [], d.get("photos") or []
        if len(cards) != len(photos):
            continue
        for card, photo in zip(cards, photos):
            pp = Path(str(photo).replace("\\", "/"))
            out.setdefault(f"{pp.parent.name}/{pp.name}", card.replace("|", ": ", 1))
    return out


def contexts():
    """{label id: {"topic", "title", "caption", "credit", "claim"}}: what the pipeline knows about each photograph.
    An upscaled copy takes its original's metadata (lineage: cache/upscaled/06_commons_<hash>.jpg <- 06_commons.jpg)."""
    claims, cache, out = _claims(), {}, {}
    for lab in LABELS:
        topic, name = lab["name"].split("/", 1)
        if topic == "upscaled":
            topic, base = "nazca_lines_runway_paradox", name.split("_commons")[0] + "_commons.jpg"
        else:
            base = name
        if topic not in cache:
            cache[topic] = _credits(topic)
        meta = cache[topic].get(base, {})
        desc = (meta.get("description") or "").split("\n")[0]
        credit = ", ".join(x for x in (meta.get("artist"), meta.get("license")) if x)
        out[lab["id"]] = {"topic": topic.replace("_", " "), "title": meta.get("title", ""), "caption": desc,
                          "credit": credit, "claim": claims.get(lab["name"], "")}
    return out


def load_answers(path):
    """{id: answer record} from an answers .jsonl written by ask.py (last line per id wins)."""
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = r
    return out


def stability_ids():
    """The 30 photographs asked a second time: every named hard case, then the first others by SHA-256 (a fixed,
    content-defined draw over every topic)."""
    hard = [i for i in HARD]
    rest = sorted((lab for lab in LABELS if lab["id"] not in HARD), key=lambda lab: lab["sha256"])
    return hard + [lab["id"] for lab in rest[:30 - len(hard)]]


def photo_subject_records():
    """{sha256: {"box", "confidence"}}: the SUBJECT stream's instrument (U2-Net + IS-Net union) over the pool."""
    return json.load(open(SUBJECT / "pool_records.json", encoding="utf-8"))


def screen_verdict(label):
    """The pool screen's cached look (.picture_text.json beside the topic's photographs), or None."""
    topic = label["name"].split("/")[0]
    folder = RR_DEV / ("cache/upscaled" if topic == "upscaled" else f"assets/{topic}")
    try:
        return json.load(open(folder / ".picture_text.json", encoding="utf-8")).get(label["sha256"])
    except (OSError, ValueError):
        return None


def yxyx(box):
    """The check's [ymin, xmin, ymax, xmax] 0-1000 as [x0, y0, x1, y1] 0-1, or None."""
    if not box or len(box) != 4:
        return None
    y0, x0, y1, x1 = box
    return [x0 / 1000, y0 / 1000, x1 / 1000, y1 / 1000] if y1 > y0 and x1 > x0 else None
