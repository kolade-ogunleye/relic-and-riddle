"""QF-126c acceptance metric: a Story frame's photo credit follows TASL and fits the frame whole.

Usage: python measure.py <repo root>. Prints the metric as JSON. Runs the real code of the repo given
(multi_format.story_credit, and story_credit_fit where the repo has it) on every photo of the committed photo pools
(assets/*/credits.json) whose licence asks for a credit, each staged in a temporary folder. No network, nothing
written in the repo. It runs on the commit before the change too (it uses only names that commit has, and reports
None for what that commit cannot say).

What a complete credit is, is decided here and not by the code under test (Creative Commons' recommended
attribution, TASL): the frame's lines name
  title    the record's title, without Commons' "File:" and the file's type (where the record supplies one),
  author   the author (credits.author_name, unchanged by this row),
  source   the address of the photo's page as text: the recorded link without its scheme (its escapes read as
           letters or not), or https://commons.wikimedia.org/?curid=<id> without its scheme for the id given,
  licence  the licence's name, and
  address  the address of the photo's own licence, without the scheme and the final "/",
  change   the note that the photo was adapted.
`fits_*`: the credit set within the frame's lines at the type floor or more (story_credit_fit); `page_id` with a page
id for every Commons page (the worst case, a nine-digit id, as QF-126b measures it), `full_address` with none.

With RR_REAL_OUTPUT set to a folder of real run_* folders (read only), every Story frame those runs drew is drawn
again from its recorded recipe (photo, copy, place in the sequence) by the repo given, and counted: the frames whose
photo asks for a credit, those whose credit is complete by this file's reader, those whose credit fits, those with no
layout problem (still_layout: in the Story box, over nothing) and with the frame's text above the credit. RR_ASSETS
names the photo pools those runs' photos are read from when the recorded path is on another machine (the production
sample: <sample>/assets; a frame's photo is found there as <topic>/<NN>_commons.jpg). RR_PAGE_IDS names a JSON file
{"page_ids": {source link: page id}} known for those runs. RR_RENDER_DIR, when set, receives each credited frame as a
half-size JPEG (evidence renders; never for the production sample). Counts only.
"""

import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PIL import Image, ImageDraw

from src import multi_format as mf
from src.credits import author_name, needs_attribution

PAGE_ID_BOUND = 999_999_999
FILE_TITLE = re.compile(r"^\s*File:\s*|\.(?:jpe?g|png|tiff?|webp|gif|svg)\s*$", re.IGNORECASE)
HAS_PAGES = "pages" in inspect.signature(mf.story_credit).parameters
HAS_FIT = hasattr(mf, "story_credit_fit")


def bare(url):
    return re.sub(r"^(?:https?:)?//(?:www\.)?", "", str(url or "").strip()).rstrip("/")


def parts_named(lines, record, page_id=None):
    """Which TASL parts the frame's lines name, by this file's own reading."""
    text = " ".join(lines)
    flat = " ".join(text.split())
    title = FILE_TITLE.sub("", FILE_TITLE.sub("", str(record.get("title") or ""))).strip()
    title = " ".join(title.split())
    sources = {bare(record.get("source_url")), bare(unquote(str(record.get("source_url") or "")))} - {""}
    if page_id:
        sources.add(f"commons.wikimedia.org/?curid={page_id}")
    licence = " ".join(str(record.get("license") or "").split())
    return {
        "title": (not title) or title in flat,                  # whole: never cut
        "author": author_name(record) in flat,
        "source": any(s in flat for s in sources),
        "licence": bool(licence) and licence in flat,
        "address": bool(record.get("license_url")) and bare(record.get("license_url")) in flat,
        "change": "adapted" in flat,
    }


def credit_of(photo, pages):
    return mf.story_credit(photo, pages) if HAS_PAGES else mf.story_credit(photo)


def pool_records():
    for credits in sorted((ROOT / "assets").glob("*/credits.json")):
        data = json.loads(credits.read_text(encoding="utf-8"))
        for entry in data if isinstance(data, list) else data.get("images", []):
            if isinstance(entry, dict) and entry.get("file") and needs_attribution(entry):
                yield credits.parent.name, entry


def pool_topics():
    """(the topics whose pool has a photo, those with a photo that asks for no credit)."""
    topics, free = set(), set()
    for credits in sorted((ROOT / "assets").glob("*/credits.json")):
        data = json.loads(credits.read_text(encoding="utf-8"))
        for entry in data if isinstance(data, list) else data.get("images", []):
            if isinstance(entry, dict) and entry.get("file"):
                topics.add(credits.parent.name)
                if not needs_attribution(entry):
                    free.add(credits.parent.name)
    return topics, free


def measure_pools():
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    out = {"photos": 0, "complete": 0, "missing": {}, "fits_page_id": None, "fits_full_address": None,
           "lines_px_page_id": {}, "never_cut": None}
    fits_id = fits_full = never_cut = 0
    usable = {"page_id": set(), "full_address": set()}
    with tempfile.TemporaryDirectory() as tmp:
        for n, (topic, entry) in enumerate(pool_records()):
            folder = Path(tmp) / f"{n:04d}"
            folder.mkdir()
            (folder / "credits.json").write_text(json.dumps({"images": [dict(entry, file="p.jpg")]}), encoding="utf-8")
            photo = folder / "p.jpg"
            out["photos"] += 1
            pages = {entry.get("source_url"): PAGE_ID_BOUND}
            credit = credit_of(photo, pages)
            named = parts_named(credit["lines"], entry, PAGE_ID_BOUND if HAS_PAGES else None)
            if all(named.values()):
                out["complete"] += 1
            for part, ok in named.items():
                if not ok:
                    out["missing"][part] = out["missing"].get(part, 0) + 1
            if HAS_FIT:
                setting = mf.story_credit_fit(d, credit["lines"])
                fits_id += bool(setting["fits"])
                key = f"{len(setting['drawn'])} lines at {setting['px']} px" if setting["fits"] else "does not fit"
                out["lines_px_page_id"][key] = out["lines_px_page_id"].get(key, 0) + 1
                chars = ["".join(" ".join(x).replace("·", " ").split()) for x in (setting["drawn"], credit["lines"])]
                never_cut += chars[0] == chars[1]
                full = bool(mf.story_credit_fit(d, credit_of(photo, {})["lines"])["fits"])
                fits_full += full
                usable["page_id"].update([topic] if setting["fits"] else [])
                usable["full_address"].update([topic] if full else [])
    if HAS_FIT:
        out.update(fits_page_id=fits_id, fits_full_address=fits_full, never_cut=never_cut)
        # topics whose pool has no photo a Story could use (none free of a credit, none whose credit fits): their
        # Story would be held (StoryCreditFitError)
        topics, free = pool_topics()
        out["topics_with_photos"] = len(topics)
        out["topics_story_held"] = {k: sorted(topics - free - v) for k, v in usable.items()}
    out["lines_px_page_id"] = dict(sorted(out["lines_px_page_id"].items()))
    return out


# ---------------------------------------------------------------- real runs (counts only)

def relocated(photo, run, assets):
    photo = Path(photo)
    if photo.is_file() and not assets:
        return photo
    topic = re.sub(r"^run_|_\d{9,}(?:_short)?$", "", run.name)
    m = re.match(r"(\d+_commons)", photo.name)
    candidate = Path(assets) / topic / (m.group(1) + ".jpg") if (assets and m) else None
    return candidate if candidate and candidate.is_file() else photo


def half_jpeg(im, path):
    im = im.convert("RGB").resize((im.width // 2, im.height // 2), Image.LANCZOS)
    for q in (88, 82, 76, 70, 64, 58):
        im.save(path, quality=q, optimize=True)
        if path.stat().st_size <= 300_000:
            return


def measure_real(folder, assets, page_ids, render_dir):
    out = {"runs": 0, "frames": 0, "credited_frames": 0, "complete": 0, "fits": None, "no_layout_problem": 0,
           "text_above_credit": 0, "photo_missing": 0}
    fits = 0
    gen = mf.MultiFormatGenerator()
    for record in sorted(Path(folder).glob("run_*/stories/still_layout.json")):
        run = record.parent.parent
        try:
            frames = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out["runs"] += 1
        for name, rec in sorted(frames.items()):
            recipe = rec.get("recipe") or {}
            if not recipe.get("photo") or name in getattr(mf, "RETIRED_STORY_FRAMES", ()):
                continue
            photo = relocated(recipe["photo"], run, assets)
            if not photo.is_file():
                out["photo_missing"] += 1
                continue
            out["frames"] += 1
            kwargs = {"pages": page_ids} if HAS_PAGES else {}
            im, lay = gen._render_story_frame(photo, recipe["slide"], int(recipe["index"]), int(recipe["count"]),
                                              recipe.get("poll") or {}, **kwargs)
            credit = lay.meta["photo_credit"]
            if not credit.get("required"):
                continue
            out["credited_frames"] += 1
            entry = mf.photo_credit(photo)
            page = page_ids.get(entry.get("source_url")) if HAS_PAGES else None
            out["complete"] += all(parts_named(credit["lines"], entry, page).values())
            fits += bool(credit.get("fits"))
            out["no_layout_problem"] += lay.problems() == []
            out["text_above_credit"] += lay.meta["text_box"][3] < credit["box"][1]
            if render_dir:
                Path(render_dir).mkdir(parents=True, exist_ok=True)
                half_jpeg(im, Path(render_dir) / f"{run.name}__{name}")
    if HAS_FIT:
        out["fits"] = fits
    return out


def main():
    result = {"commit_has_story_credit_fit": HAS_FIT, "pools": measure_pools()}
    real = os.environ.get("RR_REAL_OUTPUT")
    if real:
        ids = {}
        if os.environ.get("RR_PAGE_IDS"):
            ids = json.loads(Path(os.environ["RR_PAGE_IDS"]).read_text(encoding="utf-8")).get("page_ids") or {}
        result["real"] = measure_real(real, os.environ.get("RR_ASSETS"), ids, os.environ.get("RR_RENDER_DIR"))
        result["real"]["page_ids_known"] = len(ids)
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
