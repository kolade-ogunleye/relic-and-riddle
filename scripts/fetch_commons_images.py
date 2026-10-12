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
import html
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import ASSETS_DIR, MIN_SCENE_IMAGES, TOPICS_DIR  # noqa: E402
from src.credits import CONTACT_SHEET, license_allowed, load_credits, normalize, save_credits  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks automated clients to identify themselves with contact details and throttles
# anonymous ones hard. Set COMMONS_CONTACT (a URL or email you choose) for better rate limits.
_CONTACT = os.getenv("COMMONS_CONTACT", "").strip()
HEADERS = {"User-Agent": "MetaReelsPipeline/1.0 (scene image fetcher" + (f"; {_CONTACT}" if _CONTACT else "") + ")"}
TARGET_IMAGES = 8
MIN_PIXELS = 1_500_000
MIN_HEIGHT = 1000


def _get(url: str, params: dict = None, timeout: float = 60) -> requests.Response:
    """GET with polite pacing and Retry-After-aware backoff on 429/503."""
    for attempt in range(5):
        time.sleep(1.0)
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code not in (429, 503):
            resp.raise_for_status()
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        wait = int(retry_after) if retry_after.isdigit() else 15 * 2 ** attempt
        print(f"    rate limited; waiting {wait}s")
        time.sleep(min(wait, 300))
    resp.raise_for_status()
    return resp


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def search(query: str, limit: int = 50) -> list:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlheight": 2400,
        "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|AttributionRequired",
    }
    resp = _get(API, params=params, timeout=30)
    pages = resp.json().get("query", {}).get("pages", {})
    return sorted(pages.values(), key=lambda p: p.get("index", 0))


def candidates(pages: list, keywords: list, allow_sa: bool):
    for page in pages:
        if not any(k in normalize(page["title"]) for k in keywords):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if not license_allowed(licence, allow_sa):
            continue
        if info.get("width", 0) * info.get("height", 0) < MIN_PIXELS or info.get("height", 0) < MIN_HEIGHT:
            continue
        yield {
            "title": page["title"],
            "download_url": info.get("thumburl") or info["url"],
            "source_url": info.get("descriptionurl", ""),
            "artist": _plain(meta.get("Artist", {}).get("value", "")) or "Unknown",
            "license": licence,
            "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            "attribution_required": meta.get("AttributionRequired", {}).get("value", "true") != "false",
        }


def download_jpeg(url: str, dest: Path) -> None:
    resp = _get(url)
    with Image.open(io.BytesIO(resp.content)) as im:
        im.verify()  # reject corrupt / non-image payloads
    with Image.open(io.BytesIO(resp.content)) as im:
        im.convert("RGB").save(dest, "JPEG", quality=92)  # re-encode: strips metadata and odd formats


def write_contact_sheet(topic_dir: Path, images: list) -> None:
    cell_w, cell_h, cols = 360, 270, 4
    rows = max(1, (len(images) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for i, c in enumerate(images):
        with Image.open(topic_dir / c["file"]) as im:
            im.thumbnail((cell_w - 8, cell_h - 8))
            sheet.paste(im, ((i % cols) * cell_w + 4, (i // cols) * cell_h + 4))
    sheet.save(topic_dir / CONTACT_SHEET, quality=85)


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
