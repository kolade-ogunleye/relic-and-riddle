"""Deterministic photo diversity checks, independent of the model's verdicts."""

import hashlib
import io
import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import config
from .image_quarantine import phash

log = logging.getLogger(__name__)

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


# Signatures of the pools' photos, by file (QF-115). After a restart every photo of every queued pool was decoded
# again before the bot's first message (one pHash a photo: minutes on a loaded machine), so the answers of the photos
# under ASSETS_DIR are also kept on disk. An answer is reused while the file's size, modification time, file id and
# st_ctime_ns (the inode change time on POSIX, which a copy that keeps the modification time still moves; the creation
# time on Windows) are the ones recorded and that modification time is older than the check by more than RACY_SECONDS
# (as git's index does: a write in the same clock tick as the check would leave both unchanged); otherwise the bytes
# are read and the answer reused only when their SHA-256 is the recorded one. A changed or replaced photo, of the same
# size or not, is hashed again. A record entry of the wrong shape is treated as missing; writers take a file lock.
SIGNATURE_CACHE_FILE = config.BASE_DIR / "cache" / "photo_signatures.json"
RACY_SECONDS = 2.0
SAVE_LOCK_WAIT_SECONDS = 10.0
_unsaved: set[str] = set()   # answers a save could not write (its lock was held too long): written by the next one
_SIGNATURES: dict[str, dict] = {}   # resolved path -> {size, mtime_ns, ino, ctime_ns, checked_at, digest, signature}
_signature_lock = threading.RLock()
_loaded = False


def _kept_on_disk(key: str) -> bool:
    """Only the pools' own photos are kept on disk (a folder a test or a script makes elsewhere is not)."""
    root = str(Path(config.ASSETS_DIR).resolve())
    return os.path.normcase(key).startswith(os.path.normcase(root) + os.sep)


def _valid(entry) -> bool:
    """A record entry of the shape this module writes: one of any other shape is treated as missing."""
    if not isinstance(entry, dict):
        return False
    whole = ("size", "mtime_ns", "ino", "ctime_ns")
    if any(type(entry.get(field)) is not int for field in whole) or type(entry.get("checked_at")) not in (int, float):
        return False
    found = entry.get("signature")
    return (isinstance(entry.get("digest"), str) and isinstance(found, dict)
            and all(isinstance(found.get(field), str) for field in ("source_sha256", "phash", "dhash"))
            and found["source_sha256"] == entry["digest"])


def _recorded() -> dict[str, dict]:
    """The record on disk: its valid entries, of photos that are still there."""
    try:
        found = json.loads(Path(SIGNATURE_CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    photos = found.get("photos") if isinstance(found, dict) else None
    if not isinstance(photos, dict):
        return {}
    return {key: entry for key, entry in photos.items() if isinstance(key, str) and _valid(entry) and os.path.isfile(key)}


def _load_signatures() -> None:
    """The recorded answers, once a process; those of photos that are gone are dropped."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .publication_lock import publication_lock
    try:
        # Read under the writers' lock: on Windows a reader holding the file open makes a writer's replace fail.
        with publication_lock(SIGNATURE_CACHE_FILE, "photo-signatures", wait=SAVE_LOCK_WAIT_SECONDS):
            found = _recorded()
    except (OSError, ValueError) as e:   # a cache: every photo is checked by its bytes instead
        log.debug("Photo signatures not read: %s", e)
        return
    for key, entry in found.items():
        _SIGNATURES.setdefault(key, entry)


def _save_signatures(keys: set[str]) -> None:
    """The answers found for `keys` are written to the disk record, merged with what another process wrote."""
    global _unsaved
    keys = {key for key in keys if _kept_on_disk(key)} | _unsaved
    if not keys:
        return
    from .atomic_files import write_json
    from .publication_lock import publication_lock
    try:
        # One writer at a time across processes (read, merge, replace): no process's answers are lost.
        with publication_lock(SIGNATURE_CACHE_FILE, "photo-signatures", wait=SAVE_LOCK_WAIT_SECONDS):
            photos = _recorded()
            photos.update({key: _SIGNATURES[key] for key in keys if key in _SIGNATURES})
            write_json(SIGNATURE_CACHE_FILE, {"version": 1, "photos": photos})
        _unsaved = set()
    except (OSError, ValueError) as e:   # a cache: the next save writes them
        _unsaved = keys
        log.debug("Photo signatures not saved: %s", e)


def _current_signature(path: Path, changed: set[str] | None = None) -> dict:
    """signature() of the file as it is now (see SIGNATURE_CACHE_FILE). Readiness rechecks every photo of a pool on
    each screen and at each run start (QF-58); `changed` collects the files whose record was written."""
    path = Path(path)
    stat = path.stat()
    key = str(path.resolve())
    with _signature_lock:
        _load_signatures()
        entry = _SIGNATURES.get(key)
    if (entry and entry.get("size") == stat.st_size and entry.get("mtime_ns") == stat.st_mtime_ns
            and entry.get("ino") == stat.st_ino and entry.get("ctime_ns") == stat.st_ctime_ns
            and stat.st_mtime_ns / 1e9 < entry["checked_at"] - RACY_SECONDS):
        return dict(entry["signature"])
    checked_at = time.time()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if entry and entry.get("digest") == digest:
        found = entry["signature"]   # the same bytes: touched, copied back, or checked just after a write
    else:
        found = signature(path)
    with _signature_lock:
        _SIGNATURES[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino,
                            "ctime_ns": stat.st_ctime_ns, "checked_at": checked_at, "digest": digest,
                            "signature": dict(found)}
    if changed is not None:
        changed.add(key)
    return dict(found)


def refresh(topic_dir: Path, images: list[dict]) -> None:
    """Recompute hashes from the current files before fetch/approval decisions (an unchanged file's are reused)."""
    changed: set[str] = set()
    for image in images:
        try:
            image.pop("hash_flat", None)
            image.update(_current_signature(topic_dir / image["file"], changed))
            image.pop("hash_error", None)
        except (OSError, ValueError, KeyError) as exc:
            for field in ("source_sha256", "phash", "dhash"):
                image.pop(field, None)
            image["hash_error"] = type(exc).__name__
        if shot_class(image) == "unknown":
            image["shot_class"] = provisional_shot_class(image)
    if changed:
        with _signature_lock:
            _save_signatures(changed)


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
