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
from src.commons import candidates, download_jpeg, search  # noqa: E402
from src.credits import load_credits, normalize, save_credits, write_contact_sheet  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

TARGET_IMAGES = 8


def fetch_topic(topic: dict, spec: dict, allow_sa: bool) -> int:
    topic_dir = ASSETS_DIR / topic["id"]
    topic_dir.mkdir(parents=True, exist_ok=True)
    credits = load_credits(topic_dir)
    images = credits["images"]
    if len(images) >= TARGET_IMAGES:
        print(f"  {topic['id']}: already has {len(images)} images")
        return len(images)

    keywords = [normalize(k) for k in spec["keywords"]]
    have = {c["title"] for c in images}
    added = 0
    for query in spec["queries"]:
        for cand in candidates(search(query), keywords, allow_sa):
            if len(images) >= TARGET_IMAGES:
                break
            if cand["title"] in have:
                continue
            n = 1
            while (topic_dir / f"{n:02d}_commons.jpg").exists():
                n += 1
            dest = topic_dir / f"{n:02d}_commons.jpg"
            try:
                download_jpeg(cand["download_url"], dest)
            except Exception as e:
                print(f"    skip {cand['title']}: {e}")
                continue
            cand["file"] = dest.name
            images.append(cand)
            have.add(cand["title"])
            added += 1
        if len(images) >= TARGET_IMAGES:
            break

    if added:
        credits["reviewed"] = False  # new images always need a fresh look
    images.sort(key=lambda c: c["file"])
    save_credits(topic_dir, credits)
    if images:
        write_contact_sheet(topic_dir, images)
    status = "ok, needs review" if len(images) >= MIN_SCENE_IMAGES else f"SHORT (need {MIN_SCENE_IMAGES})"
    print(f"  {topic['id']}: {len(images)} images, +{added} new [{status}]")
    return len(images)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic-id")
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
            if fetch_topic(t, spec, args.allow_share_alike) < MIN_SCENE_IMAGES:
                short.append(t["id"])
        except requests.RequestException as e:
            print(f"  {t['id']}: Commons request failed: {e}")
            short.append(t["id"])
    print(f"Done. Short of images ({len(short)}): {', '.join(short) or 'none'}")
    print("Review each contact_sheet.jpg, delete wrong images, then: python main.py assets approve <topic_id>")
    return 0 if not short else 2


if __name__ == "__main__":
    sys.exit(main())
