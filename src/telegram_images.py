"""Topic-specific image ingestion; rights and human review remain mandatory."""
import io
import json
import logging
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

from PIL import Image, ImageOps

from .credits import load_credits, save_credits, license_allowed, write_contact_sheet
from .publication_lock import exclusive

log = logging.getLogger(__name__)
MAX_BYTES = 20 * 1024 * 1024


def topic_for(db, ref):
    topics = db.list_all_topics()
    topic = topics[int(ref) - 1] if ref.isdigit() and 1 <= int(ref) <= len(topics) else db.get_topic_by_id(ref)
    if not topic or Path(topic["id"]).name != topic["id"] or topic["id"] in (".", ".."):
        raise ValueError("Unknown topic; use its number or ID from /queue")
    with db._conn() as conn:
        if conn.execute("SELECT 1 FROM runs WHERE topic_id=? AND status='producing'", (topic["id"],)).fetchone():
            raise ValueError("This topic has a producing run; inspect/finish it before changing images")
    return topic


def parse_caption(args):
    parts = [p.strip() for p in args.split("|")]
    rights = {}
    for part in parts[1:]:
        key, _, value = part.partition(":")
        rights[key.strip().lower().replace("license", "licence")] = value.strip()
    if not parts[0] or not all(rights.get(k) for k in ("source", "licence", "licence_url", "author")):
        raise ValueError("Include topic | source: URL | licence: CC BY 4.0 | licence_url: URL | author: Name")
    if not license_allowed(rights["licence"]):
        raise ValueError("Use an eligible PD, CC0 or CC BY licence; the image still needs human review")
    if any(urlparse(rights[k]).scheme not in ("http", "https") or not urlparse(rights[k]).netloc for k in ("source", "licence_url")):
        raise ValueError("Source and licence evidence must be HTTP(S) URLs")
    return parts[0], rights


def add_image(db, assets_dir, ref, rights, content, uploaded_by):
    if len(content) > MAX_BYTES:
        raise ValueError("Image exceeds the 20 MiB upload limit")
    topic = topic_for(db, ref)
    with exclusive(db.db_path, "topic-images:" + topic["id"]) as acquired:
        if not acquired:
            raise ValueError("An image job is already running for this topic")
        topic_for(db, topic["id"])
        with Image.open(io.BytesIO(content)) as original:
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")  # upright: a phone photo sent as a file
        directory = Path(assets_dir) / topic["id"]
        directory.mkdir(parents=True, exist_ok=True)
        name = "telegram_" + uuid4().hex + ".jpg"
        image.save(directory / name, "JPEG", quality=95)
        credits = load_credits(directory)
        credits["reviewed"] = False
        credits["images"].append({"file": name, "title": "Operator-supplied image", "artist": rights["author"],
            "license": rights["licence"], "license_url": rights["licence_url"], "source_url": rights["source"],
            "attribution_required": rights["licence"].upper().startswith("CC BY"), "uploaded_by": str(uploaded_by),
            **({"own_work": True} if rights.get("own_work") else {})})
        save_credits(directory, credits)
        write_contact_sheet(directory, credits["images"])
        return topic["id"]


def fetch_images(db, assets_dir, ref):
    topic = topic_for(db, ref)
    with exclusive(db.db_path, "topic-images:" + topic["id"]) as acquired:
        if not acquired:
            raise ValueError("An image job is already running for this topic")
        topic_for(db, topic["id"])
        from . import config
        from scripts.fetch_commons_images import fetch_topic
        specs = json.loads((config.TOPICS_DIR / "image_queries.json").read_text(encoding="utf-8"))
        spec = specs.get(topic["id"])
        if not spec:
            raise ValueError("No curated image search for this topic yet; use /photos N upload to supply photos")
        count = fetch_topic(topic, spec, config.IMAGES_ALLOW_SHARE_ALIKE, assets_dir=Path(assets_dir))
        try:  # the new photos get their verdicts now, so the sheet you review shows them (2026-10-25)
            from .image_review import screen_unchecked
            screen_unchecked(topic, Path(assets_dir) / topic["id"])
        except Exception as e:
            log.warning("New photos of %s not screened: %s", topic["id"], e)
        return topic["id"], count


def review_digest(directory):
    """Identity of the displayed image set, rights and storyboard, not just filenames."""
    import hashlib
    directory = Path(directory).resolve()
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        if path.name in {"credits.json", "storyboard.json"} or path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            if path.name in {"contact_sheet.jpg", "contact_sheet.png"}:
                continue
            if path.resolve().parent != directory:
                raise ValueError("Image set contains a path outside its topic folder")
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:32]
