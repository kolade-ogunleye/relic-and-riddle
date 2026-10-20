"""
Fetch licensed scene images for topics from Wikimedia Commons.

For each topic short of images, runs the curated searches in data/topics/image_queries.json,
keeps only files whose title contains a topic keyword, whose licence is acceptable and whose
resolution is sufficient, re-encodes them as JPEG into assets/<topic_id>/, records
author/licence/source in credits.json, and writes contact_sheet.jpg for review.

Fetched sets are UNREVIEWED: the pipeline will not use them until you look at the contact
sheet, delete any wrong images, and run `python main.py assets approve <topic_id>`.
Re-running this script refills gaps left by deleted images.

Usage:
  python scripts/fetch_commons_images.py                 # every topic short of images
  python scripts/fetch_commons_images.py --topic-id kailasa_temple_monolith
  python scripts/fetch_commons_images.py --allow-share-alike
"""

import argparse
from typing import Optional
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import ASSETS_DIR, MIN_SCENE_IMAGES, TOPICS_DIR  # noqa: E402
from src.commons import (  # noqa: E402
    MIN_HEIGHT,
    MIN_PIXELS,
    best_first,
    candidates,
    search_graded,
    download_jpeg,
    find_categories,
    search,
)
from src.credits import license_allowed, load_credits, normalize, save_credits, write_contact_sheet  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

# A ranked pool, not a fixed handful: the scene planner picks per beat from all of them, using
# each image's caption (title, description, date) and the picture itself.
TARGET_IMAGES = 20


def openverse_licence(code: str, version: str) -> str:
    """Openverse codes ("by", "by-nd", "cc0", "pdm") as Commons-style names the licence filter reads."""
    code = (code or "").strip().lower()
    if code == "pdm":
        return "Public domain"
    if code == "cc0":
        return "CC0"
    return f"CC {code.upper()} {version}".strip()


def search_openverse(query: str, limit: int = 15) -> list:
    url = "https://api.openverse.org/v1/images/"
    params = {"q": query, "page_size": limit, "license_type": "commercial"}
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 200:
            results = r.json().get("results", [])
            out = []
            for item in results:
                licence = openverse_licence(item.get("license", ""), item.get("license_version", ""))
                if not license_allowed(licence):  # e.g. NoDerivatives forbids our crops and upscaling
                    continue
                out.append({
                    "title": item.get("title") or query,
                    "download_url": item.get("url"),
                    "source_url": item.get("foreign_landing_url") or item.get("url"),
                    "artist": item.get("creator") or "Unknown",
                    "license": licence,
                    "license_url": item.get("license_url", ""),
                    "provider": "openverse",
                    "year": None,
                })
            return out
    except Exception:
        pass
    return []


def interleave(ranked_lists: list) -> list:
    """Round-robin: the best of each list, then the second best of each, and so on."""
    out = []
    for i in range(max((len(r) for r in ranked_lists), default=0)):
        out += [r[i] for r in ranked_lists if i < len(r)]
    return out


def fetch_topic(topic: dict, spec: dict, allow_sa: bool, target_images: int = TARGET_IMAGES,
                assets_dir: Optional[Path] = None) -> int:
    topic_dir = (assets_dir or ASSETS_DIR) / topic["id"]  # resolved per call, never frozen at import
    topic_dir.mkdir(parents=True, exist_ok=True)
    credits = load_credits(topic_dir)
    images = credits["images"]
    if len(images) >= target_images:
        print(f"  {topic['id']}: already has {len(images)} images")
        return len(images)

    keywords = [normalize(k) for k in spec["keywords"]]
    exclude = [normalize(x) for x in spec.get("exclude", [])]

    def namesake(cand: dict) -> bool:
        """A file about something else that shares the name (curated per topic)."""
        text = normalize(" ".join([cand.get("title", ""), cand.get("description", "")] + cand.get("categories", [])))
        return any(x in text for x in exclude)
    loc = topic.get("location") or {}
    place = ({"lat": loc["latitude"], "lon": loc["longitude"]}
             if loc.get("latitude") and loc.get("longitude") else None)  # 0,0 means unknown
    have = {c["title"] for c in images}
    added = 0

    # 1. Primary: the curated shot searches (wide, detail, interior...). Each query's results are
    # ranked best first (Commons grade, then resolution) and the queries take turns, so the set
    # gets the finest photo of each kind of shot rather than the first few of one.
    def query_pages(q: str) -> list:
        seen_titles, pages = set(), []
        for p in search_graded(q) + search(q):
            if p["title"] not in seen_titles:
                seen_titles.add(p["title"])
                pages.append(p)
        return pages

    for cand in interleave([best_first(list(candidates(query_pages(q), keywords, allow_sa)), keywords, place)
                            for q in spec["queries"]]):
        if cand["relevance"] <= 0 or namesake(cand):
            continue  # metadata says off-subject, nowhere near the site, or a namesake
        if len(images) >= target_images:
            break
        if cand["title"] in have:
            continue
        n = 1
        while (topic_dir / f"{n:02d}_commons.jpg").exists():
            n += 1
        dest = topic_dir / f"{n:02d}_commons.jpg"
        try:
            download_jpeg(cand["download_url"], dest, min_pixels=MIN_PIXELS, min_height=MIN_HEIGHT)
        except Exception as e:
            print(f"    skip {cand['title']}: {e}")
            continue
        cand["file"] = dest.name
        images.append(cand)
        have.add(cand["title"])
        added += 1

    # 2. Cascading Commons Fallback: Direct Category Discovery
    if len(images) < target_images:
        cat_search_terms = [topic.get("title", ""), spec.get("keywords", [""])[0]]
        for term in cat_search_terms:
            if not term or len(images) >= target_images:
                break
            cats = find_categories(term, limit=3)
            for cat in cats:
                cat_clean = cat.replace("Category:", "").strip()
                cat_q = f'incategory:"{cat_clean}"'
                for cand in best_first(list(candidates(search(cat_q), keywords, allow_sa)), keywords, place):
                    if len(images) >= target_images:
                        break
                    if cand["relevance"] <= 0 or namesake(cand):
                        continue
                    if cand["title"] in have:
                        continue
                    n = 1
                    while (topic_dir / f"{n:02d}_commons.jpg").exists():
                        n += 1
                    dest = topic_dir / f"{n:02d}_commons.jpg"
                    try:
                        download_jpeg(cand["download_url"], dest, min_pixels=MIN_PIXELS, min_height=MIN_HEIGHT)
                    except Exception:
                        continue
                    cand["file"] = dest.name
                    images.append(cand)
                    have.add(cand["title"])
                    added += 1
                if len(images) >= target_images:
                    break

    # 3. Museum Fallback: Openverse (Commercial CC Only, with Strict Dimension Gating)
    if len(images) < target_images:
        for query in spec["queries"]:
            for cand in search_openverse(query, limit=15):
                if len(images) >= target_images:
                    break
                # Same subject rules as Commons: a topic word in the title, and no namesake.
                if cand["title"] in have or not any(k in normalize(cand["title"]) for k in keywords) or namesake(cand):
                    continue
                n = 1
                while (topic_dir / f"{n:02d}_commons.jpg").exists():
                    n += 1
                dest = topic_dir / f"{n:02d}_commons.jpg"
                try:
                    download_jpeg(cand["download_url"], dest, min_pixels=MIN_PIXELS, min_height=MIN_HEIGHT)
                except Exception:
                    # Skips low-res museum thumbnails or unreachable endpoints
                    continue
                cand["file"] = dest.name
                images.append(cand)
                have.add(cand["title"])
                added += 1
            if len(images) >= target_images:
                break

    if added:
        credits["reviewed"] = False  # new images always need a fresh look
    images.sort(key=lambda c: c["file"])
    save_credits(topic_dir, credits)

    # Automatically generate storyboard and render storyboard contact sheet
    try:
        from src.storyboard import generate_storyboard
        generate_storyboard(topic["id"], topic_dir)
    except Exception:
        if images:
            write_contact_sheet(topic_dir, images)

    status = "ok, needs review" if len(images) >= MIN_SCENE_IMAGES else f"SHORT (need {MIN_SCENE_IMAGES})"
    print(f"  {topic['id']}: {len(images)} images, +{added} new [{status}]")
    return len(images)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic-id")
    parser.add_argument("--target-images", type=int, default=TARGET_IMAGES, help="Target image count (default: 8)")
    parser.add_argument("--allow-share-alike", action="store_true", help="Also accept CC BY-SA images")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Write here instead of assets/ (e.g. a fresh set to compare before swapping in)")
    args = parser.parse_args()

    specs = json.loads((TOPICS_DIR / "image_queries.json").read_text(encoding="utf-8"))
    db = PipelineDatabase()
    topics = [db.get_topic_by_id(args.topic_id)] if args.topic_id else db.list_all_topics()
    if not topics or topics[0] is None:
        print("Unknown topic")
        return 1

    short = []
    for t in topics:
        spec = specs.get(t["id"])
        if not spec:
            print(f"  {t['id']}: no entry in image_queries.json, skipping")
            continue
        try:
            if fetch_topic(t, spec, args.allow_share_alike, target_images=args.target_images,
                           assets_dir=args.out_dir) < MIN_SCENE_IMAGES:
                short.append(t["id"])
        except requests.RequestException as e:
            print(f"  {t['id']}: Commons request failed: {e}")
            short.append(t["id"])
    print(f"Done. Short of images ({len(short)}): {', '.join(short) or 'none'}")
    print("Review each contact_sheet.jpg, delete wrong images, then: python main.py assets approve <topic_id>")
    return 0 if not short else 2


if __name__ == "__main__":
    sys.exit(main())
