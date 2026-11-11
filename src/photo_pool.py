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
# Attribution that names nobody ("Unknown author", "Anonymous, Flemish, 15th century"): unverified, so it is
# neither one shared author nor a source of diversity.
UNKNOWN_AUTHOR = re.compile(r"\bunknown|\banonym")


def protected(image: dict) -> bool:
    return bool(image.get("operator_keep") or image.get("presentation") == "full_image")


def crop_budget(images: list[dict]) -> bool:
    """Diversity selection must preserve QF-52's new-pool resolution budget."""
    automatic = [image for image in images if image.get("presentation") != "full_image" and "vertical_crop_width" in image]
    if not automatic:
        return True  # legacy pools are not retroactively treated as new fetches
    crops = [image["vertical_crop_width"] for image in automatic]
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
        result = {"source_sha256": hashlib.sha256(data).hexdigest(),
                  "phash": f"{phash(image):016x}", "dhash": f"{bits:016x}"}
        if small.max() == small.min():
            # A flat frame has no structure: its perceptual hashes say nothing about what it shows.
            result["hash_flat"] = True
        return result


# Near duplicates: the pHash and dHash distances (bits of 64 each) added together. Calibrated on every
# within-pool pair of the real development pools (QF-53b evidence; the production sample checked as counts):
# re-edits and re-exposures of one photo sum to 12 or less (Derinkuyu 21/23 is 10 + 2); different objects
# start at 17 (The Met swords 30975/30903 are 10 + 7). Pairs beyond this (two scans of one engraving at 21)
# are a known limit of hashing, reported in the evidence, not tuned for.
DUPLICATE_DISTANCE = 15


def _hash_bits(image: dict) -> tuple[int, int] | None:
    values = image.get("phash"), image.get("dhash")
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{16}", v) for v in values):
        return None
    return int(values[0], 16), int(values[1], 16)


def duplicate_of(candidate: dict, images: list[dict]) -> dict | None:
    bits = _hash_bits(candidate)
    if bits is None:
        return None
    for other in images:
        other_bits = _hash_bits(other)
        if other_bits is None:
            continue
        if candidate.get("hash_flat") and other.get("hash_flat"):
            # Two flat frames hash alike whatever their colour (the hashes are greyscale structure):
            # only identical bytes make them duplicates.
            if candidate.get("source_sha256") and candidate.get("source_sha256") == other.get("source_sha256"):
                return other
            continue
        distance = (bits[0] ^ other_bits[0]).bit_count() + (bits[1] ^ other_bits[1]).bit_count()
        if distance <= DUPLICATE_DISTANCE:
            return other
    return None


def author_key(image: dict) -> str:
    """Normalised author; "unknown" when the record names none (unverified, never one shared author)."""
    return re.sub(r"[^\w]+", " ", str(image.get("artist") or "unknown").casefold()).strip() or "unknown"


def known_author(image: dict) -> str | None:
    key = author_key(image)
    return None if UNKNOWN_AUTHOR.search(key) else key


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
            image.pop("hash_flat", None)
            image.update(signature(topic_dir / image["file"]))
            image.pop("hash_error", None)
        except (OSError, ValueError, KeyError) as exc:
            for field in ("source_sha256", "phash", "dhash"):
                image.pop(field, None)
            image["hash_error"] = type(exc).__name__
        if shot_class(image) == "unknown":
            image["shot_class"] = provisional_shot_class(image)


def author_quota_applies(target: int) -> bool:
    """No pool of fewer than three photos can keep every author at or under 35% (two are 50% each)."""
    return target >= 3


def fetch_subset(images: list[dict], target: int, retained: int = 0) -> tuple[list[dict], list[dict]]:
    """Choose which photos a refill keeps. The first `retained` are the pool's own photos: only a near duplicate
    of an earlier one is moved out. The rest (reserves offered back) must also fit the author and shot-class
    quotas, counted with the pool's own photos."""
    kept, reserve = [], []
    authors, classes = Counter(), Counter()
    for index, image in enumerate(images):
        author, kind = known_author(image), shot_class(image)
        offered = index >= retained
        reason = ("near duplicate" if duplicate_of(image, kept) else
                  "author quota" if offered and author_quota_applies(target) and author
                  and authors[author] >= max(1, int(target * 0.35)) else
                  "shot class quota" if offered and kind != "unknown"
                  and classes[kind] >= max(1, math.ceil(target / 2)) else "")
        if reason and not protected(image):
            reserve.append({**image, "reserve_reason": reason})
            continue
        kept.append(image)
        authors[author] += 1
        classes[kind] += 1
    return kept, reserve


_HOLDING = {"Photos lack verified perceptual hashes",
            "Automatic photo crop budget no longer meets the resolution floor"}


def assess(images: list[dict]) -> dict:
    # The author share counts named authors only; unnamed ones are reported as unverified (like shot classes).
    named = [known_author(image) for image in images]
    authors = Counter(author for author in named if author)
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
    unverified = classes.get("unknown", 0)
    problems = []
    if missing:
        problems.append("Photos lack verified perceptual hashes")
    if pairs:
        problems.append(f"Near-duplicate photos (hash distance {DUPLICATE_DISTANCE} or less)")
    if share > 0.35:
        problems.append("An author supplies more than 35% of the pool")
    if len(known) < 3 and not unverified:
        problems.append("Fewer than three verified shot classes")
    if any(count > math.ceil(len(images) / 2) for kind, count in known.items()):
        problems.append("One shot class exceeds its half-pool quota")
    if not crop_budget(images):
        problems.append("Automatic photo crop budget no longer meets the resolution floor")
    # Duplicates, missing hashes and the crop budget hold an automatic approval. Author share and shot-class
    # balance are quotas on what the fetcher adds (operator decision QF-53b-author-cap, 2026-11-09): a pool's
    # existing photos are never trimmed for them; they are reported.
    holds = [p for p in problems if p in _HOLDING or p.startswith("Near-duplicate")]
    return {"ok": not problems, "problems": problems, "holds": holds, "duplicate_pairs": pairs,
            "max_author_share": share, "shot_classes": known,
            "unverified_shot_classes": unverified, "unverified_authors": named.count(None),
            "missing_hashes": missing}


def deduplicated(images: list[dict]) -> tuple[list[dict], list[dict]]:
    """The pool without its near-duplicates, in its existing order; the duplicates become reserves.

    No photo is moved out for an author or shot-class quota (operator decision QF-53b-author-cap). A protected
    photo (an operator keep or an essential full image) is never the one reserved: when it repeats an unprotected
    photo, the unprotected one goes.
    """
    kept: list[dict] = []
    reserves: list[dict] = []
    for image in images:
        other = duplicate_of(image, kept)
        if other is None:
            kept.append(image)
        elif protected(image) and not protected(other):
            kept[kept.index(other)] = image
            reserves.append({**other, "reserve_reason": "near duplicate"})
        elif protected(image):
            kept.append(image)  # two protected photos: both stay; assess() reports the pair
        else:
            reserves.append({**image, "reserve_reason": "near duplicate"})
    return kept, reserves
