"""
src/credits.py
==============
Licence and review tracking for fetched scene images.

assets/<topic_id>/credits.json:
  {"reviewed": false, "images": [{"file", "title", "artist", "license", "license_url",
                                   "source_url", "attribution_required"}, ...]}

Fetched sets start unreviewed; the pipeline refuses them until a person has checked the
contact sheet and run `python main.py assets approve <topic_id>`. Attribution-required
licences (CC BY) are credited in the post caption.
"""

import datetime
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

CREDITS_FILE = "credits.json"
CONTACT_SHEET = "contact_sheet.jpg"
PROVENANCE_FILE = "provenance.json"


def sha256_file(path: Optional[Path]) -> str:
    """Computes hex SHA-256 checksum of a file."""
    if not path or not Path(path).exists() or not Path(path).is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def asset_attribution(record):
    if not record.get("attribution_required"):
        return ""
    return "📷 " + " | ".join(str(record.get(key) or "Unknown") for key in
                              ("artist", "title", "license", "source_url", "license_url"))


def add_provenance_attributions(payload, ledger):
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    credits_to_add = [asset_attribution(record) for record in data.get("assets", []) if asset_attribution(record)]
    if any(is_share_alike(record.get("license", "")) for record in data.get("assets", [])):
        credits_to_add.insert(0, SHARE_ALIKE_NOTICE)
    if not credits_to_add:
        return payload

    from .beat_images import CREDIT_PREFIX
    for credit in credits_to_add:
        if credit not in payload.get("description", ""):
            payload["description"] = payload.get("description", "").rstrip() + "\n\n" + credit
        if credit not in payload.get("caption", ""):
            cand = payload.get("caption", "").rstrip() + "\n\n" + credit
            if len(cand) > 2200 and CREDIT_PREFIX in payload.get("caption", ""):
                lines = [ln for ln in payload["caption"].splitlines() if not ln.startswith(CREDIT_PREFIX)]
                payload["caption"] = "\n".join(lines).strip()
                cand = payload.get("caption", "").rstrip() + "\n\n" + credit
            if len(cand) <= 2200:
                payload["caption"] = cand
    return payload


def resolve_asset_path(item: Path, roots: List[Path]) -> Path:
    """Resolve an exact relative path; never guess by basename."""
    item = Path(item)
    candidates = [item] if item.is_absolute() else [root / item for root in roots]
    matches = {p.resolve() for p in candidates if p.is_file()}
    if len(matches) != 1:
        reason = "missing" if not matches else "ambiguous"
        raise ValueError(f"{reason} asset path: {item}")
    return matches.pop()


def generate_provenance_ledger(
    run_dir: Path, topic: Dict[str, Any], payload: Dict[str, Any],
    master_mp4: Path, vo_path: Optional[Path] = None,
    bed_path: Optional[Path] = None, media_items: Optional[List[Path]] = None,
    self_healing: Optional[Dict[str, Any]] = None,
) -> Path:
    """Record file identity and available rights evidence without assuming clearance."""
    from .config import ASSETS_DIR
    run_dir = Path(run_dir).resolve()
    roots = [Path.cwd(), run_dir, run_dir / "graphics", ASSETS_DIR / topic.get("id", "")]
    issues, assets, seen, credit_entries = [], [], set(), []
    if not media_items:
        issues.append("No composition assets were supplied; coverage cannot be verified")
    lineage_records = {}
    for credits_file in {run_dir / CREDITS_FILE, ASSETS_DIR / topic.get("id", "") / CREDITS_FILE}:
        if credits_file.is_file():
            try:
                data = json.loads(credits_file.read_text(encoding="utf-8"))
                for entry in (data if isinstance(data, list) else data.get("images", [])):
                    credit_path = Path(entry.get("file", ""))
                    if not credit_path.is_absolute():
                        credit_path = credits_file.parent / credit_path
                    credit_entries.append({**entry, "file": str(credit_path.resolve()),
                                           "_resolved_path": str(credit_path.resolve())})
            except (ValueError, OSError) as exc:
                issues.append(f"Unreadable credits: {credits_file}: {exc}")
    for key in ("beat_image_credits", "footage_credits", "asset_credits"):
        credit_entries.extend(payload.get(key, []))

    def file_record(item):
        try:
            resolved = resolve_asset_path(Path(item), roots)
            return {"path": str(resolved), "sha256": sha256_file(resolved)}
        except ValueError as exc:
            issues.append(str(exc))
            return {"path": str(item), "sha256": "", "status": "failed"}

    for item in media_items or []:
        record = file_record(item)
        if not record["sha256"] or record["path"] in seen:
            continue
        seen.add(record["path"])
        path = Path(record["path"])
        from .lineage import source_chain
        try:
            chain = source_chain(path)
            for source in chain:
                lineage_records[source["path"]] = source
        except (ValueError, KeyError, OSError) as exc:
            issues.append(str(exc))
            chain = []
        source_paths = [Path(source["path"]) for source in chain if source.get("kind") != "lineage"]
        matches = [c for c in credit_entries if any(
            str(c.get(k, "")) == str(item) or str(c.get(k, "")) == str(path)
            for k in ("file", "path", "upscaled", "_resolved_path"))]
        if not matches:
            matches = [c for c in credit_entries if any(
                c.get("_resolved_path", c.get("file")) == str(source)
                for source in source_paths)]
        matches = [c for c in matches if not c.get("_resolved_path") or
                   c["_resolved_path"] in {str(path), *(str(p) for p in source_paths)}]
        # Identical credit records may occur in both run-local and topic credits.
        matches = list({json.dumps(c, sort_keys=True): c for c in matches}.values())
        rec = matches[0] if len(matches) == 1 else {}
        rights_known = rights_evidence_ok(rec)
        requires_attribution = rec.get("attribution_required")
        if str(rec.get("license", "")).strip().lower().startswith("cc by"):
            requires_attribution = True
            rights_known = rights_known and bool(rec.get("title") and rec.get("artist") and
                                                  rec["artist"].strip().lower() != "unknown")
        original_drawing = False
        embedded_rights_verified = False
        from .lineage import sidecar
        if chain:
            declaration = json.loads(sidecar(path).read_text(encoding="utf-8"))
            original_drawing = (declaration.get("operation") == "procedural_graphic" and
                                declaration.get("settings", {}).get("original_drawing") is True and
                                not declaration.get("settings", {}).get("media_sources"))
            settings = declaration.get("settings", {})
            declared_sources = settings.get("media_sources", [])
            declared_credits = settings.get("source_credits", [])
            if declaration.get("operation") == "procedural_graphic" and declared_sources:
                embedded_rights_verified = all(any(
                    c.get("path") == source and license_allowed(c.get("license", "")) and
                    c.get("source_url") and c.get("license_url") and c.get("attribution_required") is False
                    for c in declared_credits) for source in declared_sources)
                record["embedded_source_credits"] = declared_credits
        record["origin"] = "original_drawing" if original_drawing else "source_media"
        record["source_paths"] = [str(source) for source in source_paths]
        record.update({"file": path.name, "title": rec.get("title", path.stem),
                       "artist": rec.get("artist"), "license": rec.get("license"),
                       "license_url": rec.get("license_url"), "source_url": rec.get("source_url"),
                       "attribution_required": requires_attribution,
                       "rights_status": "verified" if rights_known or original_drawing or embedded_rights_verified else "unverified"})
        assets.append(record)
    master = file_record(master_mp4)
    voice = file_record(vo_path) if vo_path else None
    bed = file_record(bed_path) if bed_path else None
    evidence_files = [file_record(p) for p in (run_dir / CREDITS_FILE,
                      ASSETS_DIR / topic.get("id", "") / CREDITS_FILE) if p.is_file()]
    for audio_path in (vo_path, bed_path):
        if audio_path:
            audio_path = Path(audio_path)
            rights_file = audio_path.with_suffix(audio_path.suffix + ".rights.json")
            if rights_file.is_file():
                evidence_files.append(file_record(rights_file))
                try:
                    rights = json.loads(rights_file.read_text(encoding="utf-8"))
                    for source in rights.get("sources", []):
                        lineage_records[source["path"]] = {"path": source["path"], "sha256": source["sha256"]}
                except (ValueError, KeyError, TypeError) as exc:
                    issues.append(f"Invalid audio source evidence: {exc}")
    review = payload.get("fact_review") or {}
    factual_evidence = [file_record(review[k]) for k in ("sheet_path", "reference_path") if review.get(k)]
    composition_path = Path(master_mp4).with_suffix(".composition.json")
    composition_record = file_record(composition_path) if composition_path.is_file() else None
    resource_paths = set()
    if composition_record:
        try:
            composition = json.loads(composition_path.read_text(encoding="utf-8"))
            for font_path in composition.get("fonts", {}):
                font_path = Path(font_path)
                resource_paths.add(font_path)
                evidence = font_path.parent / "license_evidence.json"
                if evidence.is_file():
                    resource_paths.add(evidence)
                    font_records = json.loads(evidence.read_text(encoding="utf-8"))["fonts"]
                    for font_record in font_records:
                        if font_record["file"] == font_path.name:
                            resource_paths.add(font_path.parent / font_record["license_file"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"Invalid composition resource evidence: {exc}")
    resource_evidence = [file_record(p) for p in sorted(resource_paths)]
    provenance = {
        "schema_version": "2.0.0", "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": run_dir.name, "topic_id": topic.get("id", ""), "topic_title": topic.get("title", ""),
        "master_video": master, "voiceover": voice, "ambient_bed": bed,
        "privacy_and_security": {"exif_stripped": "unverified", "face_anonymization": "unverified"},
        "self_healing": self_healing or {"status": "not_run", "certified": False},
        "status": "failed" if issues else "recorded", "issues": issues,
        "assets_count": len(assets), "assets": assets, "license_evidence": evidence_files,
        "derived_sources": list(lineage_records.values()), "factual_evidence": factual_evidence,
        "composition_manifest": composition_record, "resource_evidence": resource_evidence,
    }
    prov_path = run_dir / PROVENANCE_FILE
    prov_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    return prov_path

_ALLOWED = re.compile(r"^(public domain|pd\b|pd-|cc0|cc by \d)", re.IGNORECASE)
# BY-SA 2.0 and later let an adaptation use a later version, so our videos use 4.0; 1.0 does not.
_SHARE_ALIKE = re.compile(r"^cc by-sa [2-4]", re.IGNORECASE)
SHARE_ALIKE_NOTICE = "🎬 This video is licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/"


def is_attribution_line(line: str) -> bool:
    """A credit line the pipeline adds to public copy (image, footage, music, the share-alike notice).
    The notice is matched exactly, not by its emoji, so model-written text starting with 🎬 is not one."""
    return line.startswith(("📷", "🎥", "🎵")) or line.strip() == SHARE_ALIKE_NOTICE


def is_hashtag_line(line: str) -> bool:
    return line.strip().startswith("#")


def keep_hashtags(old: str, new: str) -> str:
    """`new` copy keeps `old`'s hashtag lines unless it brings its own: the edit template leaves them
    out (operator, 2026-10-23), so a caption sent back without any keeps the ones it had."""
    if any(is_hashtag_line(line) for line in new.splitlines()):
        return new
    tags = [line for line in old.splitlines() if is_hashtag_line(line)]
    return new.rstrip() + ("\n\n" + "\n".join(tags) if tags else "")


def keep_attribution(old: str, new: str) -> str:
    """`new` copy with the credit lines `old` carried that `new` lacks, appended in their order: an
    edited caption must keep the attribution its licences require (the release gate checks it)."""
    have = {line.strip() for line in new.splitlines()}
    missing = [line for line in old.splitlines() if is_attribution_line(line) and line.strip() not in have]
    return new.rstrip() + ("\n\n" + "\n".join(missing) if missing else "")


def is_share_alike(short_name: str) -> bool:
    return bool(_SHARE_ALIKE.match((short_name or "").strip()))


_PUBLIC_DOMAIN = re.compile(r"^(public domain|pd\b|pd-|cc0)", re.IGNORECASE)


def rights_evidence_ok(rec: Dict[str, Any]) -> bool:
    """An allowed licence with its evidence: the source page, and the licence's URL. Public domain and CC0
    have no licence to link to, so their source page (where the status is documented) is the evidence;
    three public-domain Commons images were held for review for lacking a URL (production, 2026-10-23)."""
    licence = str(rec.get("license", "") or "")
    if not (license_allowed(licence) and rec.get("source_url")):
        return False
    return bool(rec.get("license_url")) or bool(_PUBLIC_DOMAIN.match(licence.strip()))


def license_allowed(short_name: str, allow_share_alike: Optional[bool] = None) -> bool:
    """PD / CC0 / CC BY by default. CC BY-SA only when IMAGES_ALLOW_SHARE_ALIKE is on (it binds the video)."""
    if allow_share_alike is None:
        from . import config
        allow_share_alike = config.IMAGES_ALLOW_SHARE_ALIKE
    name = (short_name or "").strip()
    if _ALLOWED.match(name):
        return True
    return allow_share_alike and bool(_SHARE_ALIKE.match(name))


def normalize(text: str) -> str:
    """Lowercase, accent-free, underscores as spaces: for keyword matching on file titles."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).replace("_", " ").lower()


def load_credits(topic_dir: Path) -> Dict[str, Any]:
    """Returns {"reviewed": bool, "images": [...]}, dropping entries whose file was deleted."""
    path = topic_dir / CREDITS_FILE
    if not path.exists():
        return {"reviewed": False, "images": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):  # legacy format
        data = {"reviewed": False, "images": data}
    data["images"] = [c for c in data.get("images", []) if (topic_dir / c.get("file", "")).exists()]
    return data


def save_credits(topic_dir: Path, data: Dict[str, Any]) -> None:
    from .atomic_files import write_json
    write_json(topic_dir / CREDITS_FILE, data)


def caption_credit_line(credits: Dict[str, Any]) -> str:
    """One compact attribution line for images whose licence requires it."""
    seen, parts = set(), []
    for c in credits.get("images", []):
        if not c.get("attribution_required", True):
            continue
        key = (c.get("artist") or "Unknown", c.get("license") or "")
        if key not in seen:
            seen.add(key)
            parts.append(f"{key[0]} ({key[1]})")
    return ("📷 Images via Wikimedia Commons: " + "; ".join(parts)) if parts else ""


# ---------------------------------------------------------------- review helpers
TIER_ORDER = ("topic", "kept", "context", "unchecked", "weak")


def sheet_order(topic_dir: Path, images: List[Dict[str, Any]],
                storyboards: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The order photos are numbered on the contact sheet: those that show the topic first (most relevant
    first), then those you kept, context, unchecked and weak (src/image_review.py tiers). /drop and /keep use
    the same order, so "drop 5" removes the photo labelled 5 on the sheet. (A pre-script storyboard led the
    sheet with keyword picks and one repeated caption: operator, 2026-10-25.) `storyboards` is not used."""
    from .image_review import _relevance, tier
    present = [dict(c) for c in images if c.get("file") and (topic_dir / c["file"]).exists()]
    for c in present:
        c["_tier"] = tier(c)
    return sorted(present, key=lambda c: (TIER_ORDER.index(c["_tier"]), -_relevance(c))) or images


def write_contact_sheet(
    topic_dir: Path,
    images: List[Dict[str, Any]],
    storyboards: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Grid of the set's images with tile numbers and optional storyboard beat context."""
    from PIL import Image, ImageDraw

    from .typography import get_font

    ordered_items = sheet_order(topic_dir, images, storyboards)

    cell_w, cell_h, cols = 360, 270, 4
    rows = max(1, (len(ordered_items) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font_num = get_font("Anton", 40)
    font_badge = get_font("Montserrat", 16)
    font_text = get_font("Montserrat", 13)

    for i, c in enumerate(ordered_items):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        with Image.open(topic_dir / c["file"]) as im:
            im.thumbnail((cell_w - 8, cell_h - 8))
            sheet.paste(im, (x + 4, y + 4))

        # Top-left tile number badge (corresponds 1:1 to Beat number)
        draw.rounded_rectangle([x + 10, y + 10, x + 70, y + 66], radius=8, fill=(0, 0, 0))
        draw.text((x + 40, y + 38), str(i + 1), font=font_num, fill=(255, 215, 0), anchor="mm")

        # Top-right: what the vision check made of it, which decides whether a video may use it.
        from .image_review import TIER_LABELS
        t = c.get("_tier", "")
        if t:
            colours = {"topic": ((20, 83, 45), (134, 239, 172)), "kept": ((30, 58, 138), (147, 197, 253)),
                       "context": ((120, 53, 15), (253, 186, 116)), "unchecked": ((51, 65, 85), (203, 213, 225)),
                       "weak": ((127, 29, 29), (252, 165, 165))}
            bg_color, text_color = colours[t]
            label = TIER_LABELS[t].upper()
            badge_w = int(draw.textlength(label, font=font_badge)) + 20
            draw.rounded_rectangle([x + cell_w - badge_w - 12, y + 10, x + cell_w - 12, y + 38], radius=6, fill=bg_color)
            draw.text((x + cell_w - (badge_w // 2) - 12, y + 24), label, font=font_badge, fill=text_color, anchor="mm")

        # Bottom: the check's one-line reason (or the archive title), so a weak pick is visible at a glance.
        why = (c.get("vision_review") or {}).get("reason") or str(c.get("title", "")).removeprefix("File:")
        if why:
            banner_h = 42
            draw.rectangle([x + 4, y + cell_h - banner_h - 4, x + cell_w - 4, y + cell_h - 4], fill=(0, 0, 0))
            snippet = why if len(why) <= 52 else why[:49].rsplit(" ", 1)[0] + "…"
            draw.text((x + 12, y + cell_h - banner_h // 2 - 4), snippet, font=font_text, fill=(241, 245, 249), anchor="lm")

    path = topic_dir / CONTACT_SHEET
    sheet.save(path, quality=85)
    return path


def approve_set(topic_dir: Path, min_images: int) -> str:
    """Marks a fetched set reviewed. Raises ValueError if it can't be."""
    if not (topic_dir / CREDITS_FILE).exists():
        raise ValueError(f"No fetched images for {topic_dir.name}.")
    credits = load_credits(topic_dir)
    if len(credits["images"]) < min_images:
        raise ValueError(f"Only {len(credits['images'])} images; need {min_images}. Re-run the fetcher to refill.")
    credits["reviewed"] = True
    credits["reviewed_by"] = "operator"  # a human decision: automatic review never overrides it
    save_credits(topic_dir, credits)
    return f"Approved {len(credits['images'])} images for {topic_dir.name}."


def drop_images(topic_dir: Path, numbers: List[int]) -> str:
    """Deletes images by their contact-sheet number (1-based) and marks the set unreviewed."""
    credits = load_credits(topic_dir)
    images = credits["images"]
    shown = sheet_order(topic_dir, images)  # the numbering on the contact sheet
    bad = [n for n in numbers if not 1 <= n <= len(shown)]
    if bad or not numbers:
        raise ValueError(f"Numbers must be between 1 and {len(shown)}.")
    doomed = {shown[n - 1]["file"] for n in set(numbers)}
    for f in doomed:
        (topic_dir / f).unlink(missing_ok=True)
    # Remembered as rejected, so a later refill never fetches the same photo again.
    credits["rejected_by_review"] = credits.get("rejected_by_review", []) + [
        {**im, "vision_review": {"reason": "dropped by the operator", "kept": False}}
        for im in images if im["file"] in doomed]
    images[:] = [im for im in images if im["file"] not in doomed]
    # Taking photos out of an approved set leaves it approved (nothing new needs a look); it unapproved the
    # set, so a regeneration after a /drop stopped on "not reviewed" (production 2026-10-25). Too few left
    # for a video still needs a fresh decision.
    from .config import MIN_SCENE_IMAGES
    still_approved = bool(credits.get("reviewed")) and len(images) >= MIN_SCENE_IMAGES
    credits["reviewed"] = still_approved
    save_credits(topic_dir, credits)
    if images:
        write_contact_sheet(topic_dir, images)
    return (f"Removed {len(set(numbers))}; {len(images)} images remain in {topic_dir.name}"
            + ("; the set stays approved." if still_approved else "."))


def keep_images(topic_dir: Path, numbers: List[int], keep: bool = True) -> str:
    """Marks photos, by their contact-sheet number, as kept by you: a context or unchecked photo you want in
    the video anyway (src/image_review.py tiers). `keep=False` takes the mark off again."""
    credits = load_credits(topic_dir)
    images = credits["images"]
    shown = sheet_order(topic_dir, images)
    bad = [n for n in numbers if not 1 <= n <= len(shown)]
    if bad or not numbers:
        raise ValueError(f"Numbers must be between 1 and {len(shown)}.")
    chosen = {shown[n - 1]["file"] for n in set(numbers)}
    for im in images:
        if im["file"] in chosen:
            if keep:
                im["operator_keep"] = True
            else:
                im.pop("operator_keep", None)
    save_credits(topic_dir, credits)
    write_contact_sheet(topic_dir, images)
    return f"{'Kept' if keep else 'No longer kept'}: {len(chosen)} photo(s) in {topic_dir.name}."
