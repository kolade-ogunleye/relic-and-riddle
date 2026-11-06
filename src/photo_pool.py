"""Deterministic photo diversity checks, independent of the model's verdicts."""

import hashlib
import io
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .image_quarantine import phash

SHOT_CLASSES = ("wide", "interior", "detail", "people-scale", "archival")


def protected(image: dict) -> bool:
    return bool(image.get("operator_keep") or image.get("presentation") == "full_image")


def crop_budget(images: list[dict]) -> bool:
    """Diversity selection must preserve QF-52's new-pool resolution budget."""
    automatic = [image for image in images if image.get("presentation") != "full_image"]
    if not any("vertical_crop_width" in image for image in automatic):
        return True  # legacy pools are not retroactively treated as new fetches
    crops = [image.get("vertical_crop_width", 0) for image in automatic]
    return all(width >= 1080 for width in crops) and sum(width < 1350 for width in crops) * 10 <= len(crops)


def signature(path: Path) -> dict:
    """Hashes of the upright source; source digest prevents stale hash reuse."""
    data = Path(path).read_bytes()
    with Image.open(io.BytesIO(data)) as raw:
        image = ImageOps.exif_transpose(raw)
        small = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
        bits = 0
        for bit in (small[:, 1:] > small[:, :-1]).flat:
            bits = (bits << 1) | int(bit)
        return {"source_sha256": hashlib.sha256(data).hexdigest(),
                "phash": f"{phash(image):016x}", "dhash": f"{bits:016x}"}


def duplicate_of(candidate: dict, images: list[dict]) -> dict | None:
    for other in images:
        for field in ("phash", "dhash"):
            left, right = candidate.get(field), other.get(field)
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{16}", left) or not re.fullmatch(r"[0-9a-fA-F]{16}", right):
                continue
            if (int(left, 16) ^ int(right, 16)).bit_count() <= 10:
                return other
    return None


def author_key(image: dict) -> str:
    # Unknown authors form one group: missing attribution cannot manufacture diversity.
    return re.sub(r"[^\w]+", " ", str(image.get("artist") or "unknown").casefold()).strip() or "unknown"


def shot_class(image: dict) -> str:
    reviewed = (image.get("vision_review") or {}).get("shot_class")
    explicit = reviewed or image.get("shot_class")
    return explicit if explicit in SHOT_CLASSES else "unknown"


def provisional_shot_class(image: dict) -> str:
    """Metadata guides fetch quotas; the pixel review later supplies the final class."""
    text = " ".join([image.get("title", ""), image.get("description", ""),
                     *image.get("categories", [])]).casefold()
    groups = (
        ("archival", r"\b(archival|archive|historical photograph|engraving|lithograph|drawing)\b"),
        ("people-scale", r"\b(visitors?|people|tourists?|person|human scale)\b"),
        ("interior", r"\b(interior|inside|chamber|corridor|ceiling|crypt)\b"),
        ("detail", r"\b(detail|close.?up|inscription|carving|relief|fragment|texture)\b"),
        ("wide", r"\b(wide|exterior|panorama|facade|façade|overview|aerial|landscape)\b"),
    )
    return next((kind for kind, pattern in groups if re.search(pattern, text)), "unknown")


def refresh(topic_dir: Path, images: list[dict]) -> None:
    """Recompute hashes from the current files before fetch/approval decisions."""
    for image in images:
        try:
            image.update(signature(topic_dir / image["file"]))
            image.pop("hash_error", None)
        except (OSError, ValueError, KeyError) as exc:
            for field in ("source_sha256", "phash", "dhash"):
                image.pop(field, None)
            image["hash_error"] = type(exc).__name__
        if shot_class(image) == "unknown":
            image["shot_class"] = provisional_shot_class(image)


def fetch_subset(images: list[dict], target: int) -> tuple[list[dict], list[dict]]:
    """Leave room for other photographers/classes even when an old pool was full."""
    kept, reserve = [], []
    authors, classes = Counter(), Counter()
    for image in images:
        author, kind = author_key(image), shot_class(image)
        reason = ("near duplicate" if duplicate_of(image, kept) else
                  "author quota" if authors[author] >= max(1, int(target * 0.35)) else
                  "shot class quota" if kind != "unknown" and classes[kind] >= max(1, math.ceil(target / 2)) else "")
        if reason and not protected(image):
            reserve.append({**image, "reserve_reason": reason})
            continue
        kept.append(image)
        authors[author] += 1
        classes[kind] += 1
    return kept, reserve


def assess(images: list[dict]) -> dict:
    authors = Counter(author_key(image) for image in images)
    classes = Counter(shot_class(image) for image in images)
    pairs = []
    missing = []
    for index, image in enumerate(images):
        if not all(re.fullmatch(r"[0-9a-fA-F]{16}", str(image.get(field, ""))) for field in ("phash", "dhash")):
            missing.append(image.get("file", str(index)))
        other = duplicate_of(image, images[:index])
        if other is not None:
            pairs.append([other.get("file", ""), image.get("file", "")])
    share = max(authors.values(), default=0) / len(images) if images else 1.0
    known = {key: value for key, value in classes.items() if key in SHOT_CLASSES}
    problems = []
    if missing:
        problems.append("Photos lack verified perceptual hashes")
    if pairs:
        problems.append("Near-duplicate photos within 10 hash bits")
    if share > 0.35:
        problems.append("An author supplies more than 35% of the pool")
    if len(known) < 3:
        problems.append("Fewer than three verified shot classes")
    if any(count > math.ceil(len(images) / 2) for count in classes.values()):
        problems.append("One shot class exceeds its half-pool quota")
    if not crop_budget(images):
        problems.append("Automatic photo crop budget no longer meets the resolution floor")
    return {"ok": not problems, "problems": problems, "duplicate_pairs": pairs,
            "max_author_share": share, "shot_classes": known, "missing_hashes": missing}


def balanced(images: list[dict]) -> tuple[list[dict], list[dict]]:
    """Choose a diverse subset in existing quality order; retain exclusions as reserves.

    Human keeps are never silently discarded: an incompatible set stays intact and
    assess() explains why it cannot newly pass automatic diversity approval.
    """
    unique = []
    for image in images:
        if duplicate_of(image, unique) is None:
            unique.append(image)
    for size in range(len(unique), 2, -1):
        author_cap = int(size * 0.35)
        class_cap = math.ceil(size / 2)
        authors, classes = Counter(), Counter()
        selected = []
        remaining = list(unique)
        resolution_budget = any("vertical_crop_width" in image for image in unique
                                if image.get("presentation") != "full_image")
        essential_count = sum(image.get("presentation") == "full_image" for image in unique)
        low_cap = max(0, (size - essential_count) // 10)
        while remaining and len(selected) < size:
            eligible = [image for image in remaining if authors[author_key(image)] < author_cap
                        and classes[shot_class(image)] < class_cap]
            if resolution_budget:
                low_used = sum(image.get("presentation") != "full_image" and image.get("vertical_crop_width", 0) < 1350
                               for image in selected)
                eligible = [image for image in eligible if image.get("presentation") == "full_image"
                            or (image.get("vertical_crop_width", 0) >= 1080
                                and (image["vertical_crop_width"] >= 1350 or low_used < low_cap))]
            if not eligible:
                break
            chosen = min(eligible, key=lambda image: (shot_class(image) == "unknown",
                                                     classes[shot_class(image)], authors[author_key(image)]))
            selected.append(chosen)
            remaining.remove(chosen)
            authors[author_key(chosen)] += 1
            classes[shot_class(chosen)] += 1
        if len(selected) == size and assess(selected)["ok"]:
            identities = {id(image) for image in selected}
            reserves = [image for image in images if id(image) not in identities]
            if any(protected(image) for image in reserves):
                return images, []
            # Preserve the relevance/merit order consumed by the planner.
            return [image for image in unique if id(image) in identities], reserves
    return images, []  # retain evidence; approval stays held with explicit reasons
