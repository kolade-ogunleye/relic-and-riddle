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
    MIN_VERTICAL_CROP,
    NATIVE_VERTICAL_CROP,
    vertical_crop_width,
    best_first,
    candidates,
    search_graded,
    download_jpeg,
    find_categories,
    search,
)
from src.credits import license_allowed, load_credits, normalize, save_credits, write_contact_sheet  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402
from src.museum_sources import openverse_search as search_openverse  # noqa: E402

# A ranked pool, not a fixed handful: the scene planner picks per beat from all of them, using
# each image's caption (title, description, date) and the picture itself.
TARGET_IMAGES = 20


def interleave(ranked_lists: list) -> list:
    """Round-robin: the best of each list, then the second best of each, and so on."""
    out = []
    for i in range(max((len(r) for r in ranked_lists), default=0)):
        out += [r[i] for r in ranked_lists if i < len(r)]
    return out


def fetch_topic(topic: dict, spec: dict, allow_sa: bool, target_images: int = TARGET_IMAGES,
                assets_dir: Optional[Path] = None) -> int:
    """Openly licensed images first (public domain, CC0, CC BY). CC BY-SA, when allowed, only fills what is
    still missing afterwards: a video that uses one must itself be released under CC BY-SA, so most
    videos should not need one. Returns the topic's image count."""
    count = _fetch_pass(topic, spec, False, target_images, assets_dir)
    if allow_sa and count < target_images:
        count = _fetch_pass(topic, spec, True, target_images, assets_dir)
    return count


def _fetch_pass(topic: dict, spec: dict, allow_sa: bool, target_images: int = TARGET_IMAGES,
                assets_dir: Optional[Path] = None) -> int:
    from src.operator_jobs import checkpoint
    checkpoint()
    topic_dir = (assets_dir or ASSETS_DIR) / topic["id"]  # resolved per call, never frozen at import
    topic_dir.mkdir(parents=True, exist_ok=True)
    credits = load_credits(topic_dir)
    images = credits["images"]
    essential_titles = spec.get("essential_images") or {}
    if not isinstance(essential_titles, dict):
        raise TypeError("essential_images must map exact source titles to reasons")
    if any(not isinstance(reason, str) or not reason.strip() for reason in essential_titles.values()):
        raise ValueError("Every essential image needs an explicit reason")
    for record in images:
        if record.get("title") in essential_titles:
            from src.image_presentation import designate, full_image
            path = topic_dir / record["file"]
            if not full_image(path):
                designate(path, essential_titles[record["title"]])
                record.update(presentation="full_image", essential_reason=essential_titles[record["title"]])
                credits["reviewed"] = False
                save_credits(topic_dir, credits)
    from src import photo_pool
    photo_pool.refresh(topic_dir, images)
    if credits.get("reviewed_by") != "operator":
        old_reserve = credits.get("diversity_reserve", [])
        rejected_titles = {c.get("title") for c in credits.get("rejected_by_review", [])}
        known_files = {c["file"] for c in images}
        reusable = [c for c in old_reserve if c.get("reserve_reason") != "near duplicate"
                    and c.get("title") not in rejected_titles and c.get("file") not in known_files
                    and (topic_dir / c.get("file", "")).is_file()]
        photo_pool.refresh(topic_dir, reusable)
        selected, reserve = photo_pool.fetch_subset(images + reusable, target_images, retained=len(images))
        images[:] = selected
        credits["diversity_reserve"] = [c for c in old_reserve if c not in reusable] + reserve
    missing_essential = set(essential_titles) - {record.get("title") for record in images}
    if len(images) >= target_images and not missing_essential:
        credits["pool_diversity"] = photo_pool.assess(images)
        save_credits(topic_dir, credits)
        print(f"  {topic['id']}: already has {len(images)} images")
        return len(images)

    from src.photo_queries import curated_queries, query_plan, require_identity
    queries, keyword_names, subject_anchor = query_plan(spec)
    try:
        require_identity(spec, subject_anchor)
    except ValueError as held:
        credits["fetch_held"] = str(held)  # shown by asset_pipeline.not_ready_reason on the automatic path
        save_credits(topic_dir, credits)
        raise
    if credits.pop("fetch_held", None):
        save_credits(topic_dir, credits)
    curated = set(curated_queries(spec))
    keywords = [normalize(k) for k in keyword_names]
    exclude = [normalize(x) for x in spec.get("exclude", [])]

    def namesake(cand: dict) -> bool:
        """A file about something else that shares the name (curated per topic)."""
        text = normalize(" ".join([cand.get("title", ""), cand.get("description", "")] + cand.get("categories", [])))
        return any(x in text for x in exclude)
    loc = topic.get("location") or {}
    place = ({"lat": loc["latitude"], "lon": loc["longitude"]}
             if loc.get("latitude") and loc.get("longitude") else None)  # 0,0 means unknown
    # Photos a review already rejected are never fetched again.
    have = {c["title"] for c in images} | {c.get("title") for c in credits.get("rejected_by_review", [])}
    have |= {c.get("title") for c in credits.get("diversity_reserve", [])}
    added = 0
    candidate_options = {"essential_titles": essential_titles} if essential_titles else {}

    def fetch_picture(cand, dest):
        # An undersized legacy/unknown record consumes the quota until refreshed.
        # Admit one sub-1350 crop only after nine native crops; every prefix of a
        # new pool therefore stays at <=10%, even if a later query fails.
        reason = essential_titles.get(cand["title"], "")
        if isinstance(reason, str) and reason.strip():
            from src.image_presentation import designate
            w, h = download_jpeg(cand["download_url"], dest, preserve_source=True)
            designate(dest, reason)
            cand.update(width=w, height=h, vertical_crop_width=vertical_crop_width(w, h),
                        essential_reason=reason.strip(), presentation="full_image",
                        preserved_source=dest.name + ".source")
            return
        automatic = [c for c in images if c.get("presentation") != "full_image"]
        low = sum(vertical_crop_width(c.get("width", 0), c.get("height", 0)) < NATIVE_VERTICAL_CROP
                  for c in automatic)
        floor = MIN_VERTICAL_CROP if (low + 1) * 10 <= len(automatic) + 1 else NATIVE_VERTICAL_CROP
        w, h = download_jpeg(cand["download_url"], dest, min_pixels=MIN_PIXELS,
                             min_height=MIN_HEIGHT, min_crop_width=floor)
        cand.update(width=w, height=h, vertical_crop_width=vertical_crop_width(w, h))

    def diverse_candidate(cand, dest):
        try:
            cand.update(photo_pool.signature(dest))
        except (OSError, ValueError) as exc:
            # As refresh() does: no hashes, recorded, so assess() reports "lack verified perceptual hashes".
            cand["hash_error"] = type(exc).__name__
        cand["shot_class"] = photo_pool.provisional_shot_class(cand)
        duplicate = photo_pool.duplicate_of(cand, images)
        cand_author = photo_pool.known_author(cand)
        author_count = sum(photo_pool.known_author(c) == cand_author for c in images) if cand_author else 0
        class_count = sum(photo_pool.shot_class(c) == cand["shot_class"] for c in images)
        reason = ("near duplicate" if duplicate else
                  "author quota" if photo_pool.author_quota_applies(target_images) and cand_author
                  and author_count >= max(1, int(target_images * 0.35)) else
                  "shot class quota" if cand["shot_class"] != "unknown" and class_count >= max(1, (target_images + 1) // 2)
                  else "")
        if reason and not photo_pool.protected(cand):
            credits.setdefault("diversity_reserve", []).append({**cand, "file": dest.name, "reserve_reason": reason})
            have.add(cand["title"])
            return False
        return True

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

    # Museum and agency archives (The Met, NASA, Smithsonian) alongside Commons for each search.
    from src.museum_sources import search_all
    ranked_lists = []
    for q in [*sorted(missing_essential), *queries]:
        checkpoint()
        ranked_lists.append(best_first(list(candidates(query_pages(q), keywords, allow_sa, **candidate_options)), keywords, place))
        # Museums get the hand-written searches, not the Wikidata identity searches.
        if spec.get("museums", True) and (q in missing_essential or q in curated):
            ranked_lists.append(best_first(search_all(q, keywords), keywords, place))
    ordered = interleave(ranked_lists)
    ordered.sort(key=lambda cand: cand["title"] not in essential_titles)
    for cand in ordered:
        checkpoint()
        if cand["relevance"] <= 0 or namesake(cand):
            continue  # metadata says off-subject, nowhere near the site, or a namesake
        if len(images) >= target_images and cand["title"] not in missing_essential:
            continue
        if cand["title"] in have:
            continue
        n = 1
        while (topic_dir / f"{n:02d}_commons.jpg").exists():
            n += 1
        dest = topic_dir / f"{n:02d}_commons.jpg"
        try:
            fetch_picture(cand, dest)
            if not diverse_candidate(cand, dest):
                continue
        except Exception as e:
            print(f"    skip {cand['title']}: {e}")
            continue
        cand["file"] = dest.name
        images.append(cand)
        credits["reviewed"] = False
        save_credits(topic_dir, credits)
        have.add(cand["title"])
        added += 1

    # 2. Category fallback shares the beat search's namesake and place guards.
    if len(images) < target_images:
        from src.commons import topic_anchor
        anchor = topic_anchor(topic, photos=images, aliases=spec["queries"], subject_anchor=subject_anchor,
                              category_search=find_categories)
        cats = [anchor["name"]] if anchor and anchor.get("kind") == "category" else []
        for cat in cats:
            from src.photo_queries import quoted
            cat_clean = cat.removeprefix("Category:").strip()
            cat_q = f"incategory:{quoted(cat_clean)}"
            for cand in best_first(list(candidates(search(cat_q), keywords, allow_sa, **candidate_options)), keywords, place):
                checkpoint()
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
                    fetch_picture(cand, dest)
                    if not diverse_candidate(cand, dest):
                        continue
                except Exception:
                    continue
                cand["file"] = dest.name
                images.append(cand)
                credits["reviewed"] = False
                save_credits(topic_dir, credits)
                have.add(cand["title"])
                added += 1
            if len(images) >= target_images:
                break

    # 3. Museum Fallback: Openverse (Commercial CC Only, with Strict Dimension Gating)
    if len(images) < target_images:
        for query in spec["queries"]:
            checkpoint()
            for cand in search_openverse(query, limit=15):
                checkpoint()
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
                    fetch_picture(cand, dest)
                    if not diverse_candidate(cand, dest):
                        continue
                except Exception:
                    # Skips low-res museum thumbnails or unreachable endpoints
                    continue
                cand["file"] = dest.name
                images.append(cand)
                credits["reviewed"] = False
                save_credits(topic_dir, credits)
                have.add(cand["title"])
                added += 1
            if len(images) >= target_images:
                break

    if added:
        credits["reviewed"] = False  # new images always need a fresh look
    selected, reserve = photo_pool.deduplicated(images)  # new photos met the quotas as they were fetched
    images[:] = selected
    credits.setdefault("diversity_reserve", []).extend(reserve)
    credits["pool_diversity"] = photo_pool.assess(images)
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
        except ValueError as e:  # a held topic (no verified subject) must not stop the topics after it
            print(f"  {t['id']}: held: {e}")
            short.append(t["id"])
    print(f"Done. Short of images ({len(short)}): {', '.join(short) or 'none'}")
    print("Review each contact_sheet.jpg, delete wrong images, then: python main.py assets approve <topic_id>")
    return 0 if not short else 2


if __name__ == "__main__":
    sys.exit(main())
