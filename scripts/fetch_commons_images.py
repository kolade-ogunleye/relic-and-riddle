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
  python scripts/fetch_commons_images.py --topic-id 03_kailasa_temple_monolith
  python scripts/fetch_commons_images.py --allow-share-alike
"""

import argparse
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
    candidates,
    download_jpeg,
    find_categories,
    search,
)
from src.credits import load_credits, normalize, save_credits, write_contact_sheet  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

TARGET_IMAGES = 8


def search_openverse(query: str, limit: int = 15) -> list:
    url = "https://api.openverse.org/v1/images/"
    params = {"q": query, "page_size": limit, "license_type": "commercial"}
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 200:
            results = r.json().get("results", [])
            out = []
            for item in results:
                lic = item.get("license", "").upper()
                out.append({
                    "title": item.get("title") or query,
                    "download_url": item.get("url"),
                    "source_url": item.get("foreign_landing_url") or item.get("url"),
                    "artist": item.get("creator") or "Unknown",
                    "license": f"CC {lic}" if not lic.startswith("CC") else lic,
                    "license_url": item.get("license_url", ""),
                    "provider": "openverse",
                    "year": None,
                })
            return out
    except Exception:
        pass
    return []


def fetch_topic(topic: dict, spec: dict, allow_sa: bool, target_images: int = TARGET_IMAGES) -> int:
    topic_dir = ASSETS_DIR / topic["id"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    credits = load_credits(topic_dir)
    images = credits["images"]
    if len(images) >= target_images:
        print(f"  {topic['id']}: already has {len(images)} images")
        return len(images)

    keywords = [normalize(k) for k in spec["keywords"]]
    have = {c["title"] for c in images}
    added = 0

    # 1. Primary: Curated & LLM Documentary Shot Archetype Searches
    for query in spec["queries"]:
        for cand in candidates(search(query), keywords, allow_sa):
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
        if len(images) >= target_images:
            break

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
                for cand in candidates(search(cat_q), keywords, allow_sa):
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
                if cand["title"] in have:
                    continue
                n = 1
                while (topic_dir / f"{n:02d}_commons.jpg").exists():
                    n += 1
                dest = topic_dir / f"{n:02d}_commons.jpg"
                try:
                    download_jpeg(cand["download_url"], dest, min_pixels=MIN_PIXELS, min_height=MIN_HEIGHT)
                except Exception as e:
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
        generate_storyboard(topic["id"])
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
            if t["id"] != "01_derinkuyu_underground_city":  # Derinkuyu uses the curated shared suite
                print(f"  {t['id']}: no entry in image_queries.json, skipping")
            continue
        try:
            if fetch_topic(t, spec, args.allow_share_alike, target_images=args.target_images) < MIN_SCENE_IMAGES:
                short.append(t["id"])
        except requests.RequestException as e:
            print(f"  {t['id']}: Commons request failed: {e}")
            short.append(t["id"])
    print(f"Done. Short of images ({len(short)}): {', '.join(short) or 'none'}")
    print("Review each contact_sheet.jpg, delete wrong images, then: python main.py assets approve <topic_id>")
    return 0 if not short else 2


if __name__ == "__main__":
    sys.exit(main())
