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


def needs_attribution(record) -> bool:
    """A CC BY / BY-SA licence always requires attribution, whatever the source recorded (Openverse entries leave
    attribution_required unset; the rc68 review found a BY-SA carousel posted with no author line, 2026-10-28)."""
    return bool(record.get("attribution_required")) or str(record.get("license", "")).strip().lower().startswith("cc by")


# The author's name in a free-text artist field. Commons' Artist holds whatever the uploader wrote, and
# credits.json keeps it as fetched: Derinkuyu photo 18's is "This Photo was taken by Wolfgang Moroder. Feel free to
# use my photos, but please mention me as the author ..." (nine sentences), which every credit printed whole; others
# read "Nevit Dilmen (talk)", "User:Kleuske", a user-page URL, or "Unknown artistUnknown artist" (development
# assets, design review 2026-10-29). Only a name the text states is taken; when none is clear, the uploader the
# entry records, else "Unknown". Nothing is guessed.
_NAME = r"(?P<name>(?:[^\W\d_]\.\s*)*[^.,;:!?()\[\]|<>\n]+)"   # "J. R. Smith": initials, then up to a stop
_TAKEN_BY = re.compile(r"\b(?:taken|shot|made|created|photographed|captured|drawn|painted)\s+by\s+(?:the\s+)?"
                       r"(?:(?:user|photographer|artist)\s+)?" + _NAME, re.I)
_LEAD_BY = re.compile(r"^(?:(?:photo(?:graph)?|picture|image|credit|author|photographer)s?\s*(?:by\b|:)|by\b)\s*"
                      + _NAME, re.I)
_COPYRIGHT = re.compile(r"(?:©|\(c\)|(?:^|(?<=[.!?] ))copyright\b)\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?\s*,?\s*)?(?:by\s+)?"
                        + _NAME, re.I)
_USER_PAGE = re.compile(r"^(?:(?:https?:)?//[^/\s]*wiki[mp]edia\.org/wiki/)?User:(?P<name>[^/#?\s|]+)$", re.I)
_USER_HREF = re.compile(r"""href=["'][^"']*?(?:\bUser:|Special:EmailUser/)([^"'/#?&]+)["']""", re.I)
_USER_CATEGORY = re.compile(r"^(?:Category:)?User:([^/]+)/", re.I)
_TALK = re.compile(r"\s*\((?:talk|contribs?|talk\s*[·|]\s*contribs?)\)", re.I)
_DOUBLED = re.compile(r"^([A-ZÀ-ɏ].{3,}?)\s?\1$")       # two spans run together: "Unknown artistUnknown artist"
_NOT_A_NAME = re.compile(r"^(?:me|myself|the author|author|the uploader|uploader|this user|i|we|us|you|unknown)$", re.I)
AUTHOR_MAX = 80  # longer than a name and its place ("Warren LeMay from Cullowhee, NC, United States" is 46)


def _stated_name(text: str) -> str:
    """A name a pattern found, tidied, or "" when it is not one (a pronoun, a placeholder, a sentence)."""
    name = re.sub(r"^[\s:;,.\-–—|]+|[\s:;,\-–—|]+$", "", text or "")
    name = re.sub(r"(?<=[^\W\d_]{2})\.$", "", re.sub(r"[\s,]+(?:19|20)\d\d$", "", name))  # "Jane Doe 2010"
    if not name or len(name) > AUTHOR_MAX or len(name.split()) > 6 or _NOT_A_NAME.match(name):
        return ""
    return name if re.search(r"[^\W\d_]", name) else ""


def author_name(record) -> str:
    """The author a credit prints: _stated_author, sanitised (QF-42: an artist field that is an e-mail address
    or carries a link, handle or hashtag publishes none of it), "Unknown" when nothing is left."""
    return sanitise_credit_text(_stated_author(record)) or "Unknown"


def _stated_author(record) -> str:
    """Who to credit for a photo, from its credits entry: the name the artist field states ("taken by X",
    "Photo by X", "© X", a Commons user page), else the field as it is when it is a plain name (so existing credit
    lines do not change), else the one short phrase left once its notices to re-users are dropped, else the
    uploader its HTML links to or the entry records, else "Unknown"."""
    import html as _html
    from urllib.parse import unquote
    raw = str((record or {}).get("artist") or "")
    plain = " ".join(raw.split())
    text = " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
    text = _TALK.sub("", _DOUBLED.sub(r"\1", text)).strip()
    if not text:
        return "Unknown"
    page = _USER_PAGE.match(text)
    if page:
        return unquote(page.group("name")).replace("_", " ")
    stated = False                              # "taken by the author": a credit phrase naming no one
    for pattern in (_TAKEN_BY, _LEAD_BY, _COPYRIGHT):
        found = pattern.search(text)
        name = _stated_name(found.group("name")) if found else ""
        if name:
            return name
        stated = stated or bool(found)
    from .commons import _NOT_AUTHOR, _NOTICE   # one list of re-user notices and placeholders (Commons fetch)
    kept = [s for s in re.split(r"(?<=\w\w[.!?])\s+", text) if s.strip() and not _NOTICE.match(s.strip())]
    if not stated and kept == [text] and text == plain and len(text) <= AUTHOR_MAX and not _NOT_AUTHOR.match(text):
        return text                             # a plain name: printed exactly as before
    if len(kept) == 1 and not stated and not _NOT_AUTHOR.match(kept[0]):
        name = _stated_name(kept[0])            # one short phrase left once the notices are dropped
        if name:
            return name
    users = [unquote(u).replace("_", " ") for u in _USER_HREF.findall(raw)]
    uploader = str((record or {}).get("uploaded_by") or "").strip()
    if uploader and not uploader.isdigit():     # a Telegram upload records the sender's numeric id: not a name
        users.append(uploader)
    # A self-published Commons file sits in its uploader's own categories ("User:Moroder/Taken with Nikon D800");
    # used only when they name one account.
    accounts = {m.group(1).replace("_", " ") for c in (record or {}).get("categories") or []
                for m in [_USER_CATEGORY.match(str(c))] if m}
    if len(accounts) == 1:
        users.append(accounts.pop())
    for user in users:
        name = _stated_name(user)
        if name:
            return name
    return "Unknown"


# What a credit's text fields may not carry into a post (QF-42, project review 2026-11-05): the Commons Artist and
# file title are whatever the uploader wrote, and they were published verbatim, so a title "File:x t.ly/y #tag"
# put a link and a hashtag in the caption and an artist field that is an e-mail address published it. Removed
# from the author, title and licence fields; the source page and licence URL keep their own slots.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_WEB = re.compile(r"(?:\b[a-z][a-z0-9+.-]*:)?//\S+|\bwww\.\S+|"                     # scheme or www
                  r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*|"                                # host/path: "t.ly/y"
                  r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|info|biz|io|ly|co|me|tv|app|dev|link|site|online|"
                  r"xyz|gl|gg|to|cc|us|uk|de|fr|ru|it|nl|es|pl|tr|eu|in|jp|cn|br|au|ca)\b(?![\w-])", re.IGNORECASE)
_HANDLE = re.compile(r"(?<!\w)[@#]+(?!\d+\b)[\w.-]*")                                   # @user, #tag, a lone # or @
_NUMBER_SIGN = re.compile(r"(?<!\w)#+(?=\d+\b)")                                    # "Room #3": the number stays
TITLE_MAX = 60  # a credit names the file, it does not reprint an uploader's sentence (QF-42)


def sanitise_credit_text(text) -> str:
    """`text` for a credit's author, title or licence field: one line, without e-mail addresses, links,
    @handles, hashtags or a "|" (the credit's field separator), "" when nothing is left."""
    text = " ".join(str(text or "").split())
    for pattern in (_EMAIL, _WEB, _HANDLE):
        text = pattern.sub(" ", text)
    text = _NUMBER_SIGN.sub("", text)
    text = " ".join(text.replace("|", "/").split())
    return re.sub(r"^[\s:;,.\-–—/]+|[\s:;,\-–—/(]+$", "", text)


def credit_title(record) -> str:
    """The file's title as a credit prints it: sanitised, at most TITLE_MAX characters (cut at a word, with
    "…"), "Unknown" when nothing is left."""
    title = sanitise_credit_text((record or {}).get("title"))
    if len(title) > TITLE_MAX:
        cut = title[:TITLE_MAX - 1]
        title = (cut.rsplit(" ", 1)[0] if " " in cut.strip() else cut).rstrip(" ,;:-–—/(") + "…"
    return title or "Unknown"


def _credit_url(value) -> str:
    """A source page or licence URL as the credit prints it: one http(s) URL, else "Unknown"."""
    url = str(value or "").strip()
    return url if re.fullmatch(r"https?://[^\s|<>\"]+", url, re.IGNORECASE) else "Unknown"


def credit_line_problems(line: str) -> list:
    """Why a published image credit line is unsafe, [] when it is not: a link, address, handle or hashtag in its
    author, title or licence, a source or licence slot that is not one http(s) URL, or a banned phrase."""
    if not line.startswith("📷"):
        return []
    fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
    text, urls = (fields[:3], fields[3:]) if len(fields) == 5 else (fields, [])
    problems = [f"{what} in the credit text: {m.group(0)!r}" for what, pattern in
                (("an e-mail address", _EMAIL), ("a link", _WEB), ("a handle or hashtag", _HANDLE))
                for f in text for m in [pattern.search(f)] if m]
    problems += [f"not a source or licence URL: {u!r}" for u in urls if u != "Unknown" and _credit_url(u) != u]
    from .script_engine import find_banned_tropes
    problems += [f"banned phrase in the credit: {t!r}" for t in find_banned_tropes(" ".join(text))]
    return problems


def _credit_fields(line: str) -> list:
    """The five fields of a 📷 credit line in asset_attribution's shape, [] for any other line."""
    line = (line or "").strip()
    if not line.startswith("📷"):
        return []
    fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
    return fields if len(fields) == 5 else []


def rewrite_credit_line(line: str) -> str:
    """A 📷 credit line as asset_attribution writes it now, built from the line's own fields: a line written before
    QF-42 (a raw artist, an uncapped title, a link or hashtag in the title) comes back sanitised and capped, the same
    line asset_attribution prints for its record. Any other line is returned as it is."""
    fields = _credit_fields(line)
    if not fields:
        return line
    author, title, licence, source, licence_url = fields
    return "📷 " + " | ".join([sanitise_credit_text(author) or "Unknown", credit_title({"title": title}),
                               sanitise_credit_text(licence) or "Unknown", _credit_url(source),
                               _credit_url(licence_url)])


def rewrite_credit_lines(text: str) -> str:
    """`text` with every 📷 credit line rewritten (rewrite_credit_line) and a credit line that then repeats an
    earlier one dropped: a run written before QF-42 carries its old lines, and a regeneration put them back next to
    the new ones (review of QF-42, 2026-11-05). Lines that are not credits are kept as they are."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        if _credit_fields(line):
            line = rewrite_credit_line(line)
            if line in seen:
                continue
            seen.add(line)
        out.append(line)
    return "\n".join(out) + ("\n" if (text or "").endswith("\n") else "")


CREDITED_TEXT_FIELDS = ("caption", "description", "carousel_caption", "first_comment")


def rewrite_payload_credits(payload: dict[str, Any]) -> dict[str, Any]:
    """`payload` with the credit lines of its public text fields rewritten in place (rewrite_credit_lines)."""
    for field in CREDITED_TEXT_FIELDS:
        if isinstance(payload.get(field), str):
            payload[field] = rewrite_credit_lines(payload[field])
    return payload


def credit_present(record, text: str) -> bool:
    """Whether `text` credits `record`: its credit line as asset_attribution prints it, or a line written before
    QF-42 that rewrites to it (the release gate's old safe form: a run waiting for approval when this change is
    deployed keeps Approve; credit_line_problems still fails such a line when it carries a link, address, handle
    or hashtag)."""
    line = asset_attribution(record)
    if not line or line in (text or ""):
        return True
    return any(rewrite_credit_line(old) == line for old in (text or "").splitlines() if _credit_fields(old))


def asset_attribution(record):
    if not needs_attribution(record):
        return ""
    # One line whatever the source: a newline in a field put the rest of the credit in the caption without
    # the 📷 prefix, where it counted as public prose and failed the facts check (development, 2026-10-26; audit 2026-10-28)
    # The author is the name the artist field states, not the whole field (author_name; design review 2026-10-29).
    # Author, title and licence are sanitised and the title capped (QF-42): no link, address, handle or hashtag
    # of an uploader's reaches the post; the source page and licence keep their URL slots.
    return "📷 " + " | ".join([author_name(record), credit_title(record),
                               sanitise_credit_text(record.get("license")) or "Unknown",
                               _credit_url(record.get("source_url")), _credit_url(record.get("license_url"))])


def _reference_records(data) -> list[dict[str, Any]]:
    """The reference images the run's illustrations were drawn from, as credit records (an attribution licence's
    terms reach the drawing made from it)."""
    return [{"artist": r.get("artist") or "Unknown", "title": r.get("title"), "license": r.get("licence"),
             "source_url": r.get("url"), "license_url": r.get("license_url")}
            for record in data.get("assets", []) for r in (record.get("illustration") or {}).get("references") or []
            if isinstance(r, dict)]


def add_provenance_attributions(payload, ledger):
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    # Credit lines already in the copy (a regeneration's held lines, a run written before QF-42) are rewritten
    # first, so the line added below is not a second copy of an old one.
    rewrite_payload_credits(payload)
    credits_to_add = [asset_attribution(record) for record in data.get("assets", []) if asset_attribution(record)]
    credits_to_add += [line for line in (asset_attribution(r) for r in _reference_records(data))
                       if line and line not in credits_to_add]
    if any(is_share_alike(record.get("license", "")) for record in data.get("assets", []) + _reference_records(data)):
        credits_to_add.insert(0, SHARE_ALIKE_NOTICE)
    if not credits_to_add:
        return payload

    from .beat_images import CREDIT_PREFIXES
    for credit in credits_to_add:
        if credit not in payload.get("description", ""):
            payload["description"] = payload.get("description", "").rstrip() + "\n\n" + credit
        if credit not in payload.get("caption", ""):
            cand = payload.get("caption", "").rstrip() + "\n\n" + credit
            if len(cand) > 2200 and any(p in payload.get("caption", "") for p in CREDIT_PREFIXES):
                lines = [ln for ln in payload["caption"].splitlines() if not ln.startswith(CREDIT_PREFIXES)]
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
        in_house_gap_art = path.name.startswith("gap_gen_")
        illustration_credit = ""
        if in_house_gap_art:
            original_drawing = True
            # A generated illustration says so (2026-11-02, illustration panel): its model and what it was drawn
            # from. The "origin" value stays (tests/test_gap_engine.py locks it); "illustration" carries the detail.
            try:
                inputs = (json.loads(sidecar(path).read_text(encoding="utf-8")).get("settings") or {}).get("inputs") or {}
            except (OSError, ValueError, AttributeError):
                inputs = {}
            model = str(inputs.get("model") or "an image model")
            based_on = ", ".join(str(d) for d in inputs.get("descriptors") or [])
            # The reference images it was drawn from (reliable depiction, 2026-11-02), so a reviewer can trace it:
            # "based on: <reference title>, <licence>".
            refs = [r for r in inputs.get("references") or [] if isinstance(r, dict)]
            if refs:
                based_on = "; ".join(f"{r.get('title') or Path(str(r.get('path') or '')).name}, "
                                     f"{r.get('licence') or 'licence unknown'}" for r in refs)
            record["illustration"] = {"origin": "ai_illustration", "model": model,
                                      "based_on": f"based on: {based_on}" if based_on else "",
                                      "fact_ids": list(inputs.get("fact_ids") or [])}
            if refs:
                record["illustration"]["references"] = [
                    {k: r.get(k) for k in ("title", "licence", "license_url", "url", "source", "artist", "path", "sha256")}
                    for r in refs]
            illustration_credit = f"AI illustration ({model})" + (f", based on: {based_on}" if based_on else "")
        record["origin"] = "archaeological_reconstruction" if in_house_gap_art else ("original_drawing" if original_drawing else "source_media")
        record["source_paths"] = [str(source) for source in source_paths]
        record.update({"file": path.name, "title": rec.get("title", path.stem),
                       "artist": rec.get("artist") or (illustration_credit if in_house_gap_art else None),
                       "license": rec.get("license") or ("AI-generated illustration, not a photograph" if in_house_gap_art else None),
                       "license_url": rec.get("license_url"), "source_url": rec.get("source_url"),
                       "attribution_required": False if in_house_gap_art else requires_attribution,
                       "rights_status": "verified" if rights_known or original_drawing or embedded_rights_verified or in_house_gap_art else "unverified"})
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


# A photo credit as asset_attribution writes it ("artist | title | licence | source URL | licence URL"), recognised
# by that shape even without its 📷: a copy edit told the caption had too many emoji removed the credits' emoji, and
# their photo titles and Commons ids were then checked as figures (production, Derinkuyu 2026-10-29).
_CREDIT_SHAPE = re.compile(r"^[^|\n]*\|[^|\n]*\|\s*(?:public domain|pd\b|pd-|cc0|cc by)[^|\n]*\|\s*https?://", re.IGNORECASE)


def is_attribution_line(line: str) -> bool:
    """A credit line the pipeline adds to public copy (image, footage, music, the share-alike notice).
    The notice is matched exactly, not by its emoji, so model-written text starting with 🎬 is not one."""
    return (line.startswith(("📷", "🎥", "🎵")) or line.strip() == SHARE_ALIKE_NOTICE
            or bool(_CREDIT_SHAPE.match(line.strip())))


def split_attribution(text: str) -> tuple:
    """(the text without the pipeline's credit lines, those lines in order). The writer, the copy edit and the
    caption's length and emoji checks see only the copy someone wrote: the credits are the pipeline's, and their
    📷 and 🎬 counted as the caption's emoji (production, Derinkuyu 2026-10-29)."""
    lines = (text or "").splitlines()
    held = [line for line in lines if is_attribution_line(line)]
    if not held:
        return text or "", []
    kept = "\n".join(line for line in lines if not is_attribution_line(line))
    return re.sub(r"\n{3,}", "\n\n", kept).rstrip(), held


ATTRIBUTED_FIELDS = ("caption", "carousel_caption", "first_comment")


def hold_attribution(payload: Dict[str, Any]) -> tuple:
    """(a copy of `payload` with the credit lines taken out of its copy fields, {field: those lines}): a
    regenerated run starts from a payload that already carries the last render's credits."""
    out, held = dict(payload), {}
    for field in ATTRIBUTED_FIELDS:
        if isinstance(out.get(field), str):
            out[field], lines = split_attribution(out[field])
            if lines:
                held[field] = lines
    return out, held


def restore_attribution(payload: Dict[str, Any], held: Dict[str, List[str]]) -> Dict[str, Any]:
    """`payload` with the credit lines hold_attribution took out appended again, in their order, where missing."""
    for field, lines in (held or {}).items():
        if isinstance(payload.get(field), str):  # the render rewrites them (add_provenance_attributions, QF-42)
            payload[field] = keep_attribution("\n".join(lines), payload[field])
    return payload


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


CREDIT_ADAPTED = " · adapted: cropped, with motion and text added"


def caption_credit_line(credits: Dict[str, Any]) -> str:
    """One compact attribution line for images whose licence requires it."""
    seen, parts = set(), []
    for c in credits.get("images", []):
        if not c.get("attribution_required", True):
            continue
        key = (author_name(c), c.get("license") or "")  # the name, not the whole artist field (2026-10-29)
        if key not in seen:
            seen.add(key)
            parts.append(f"{key[0]} ({key[1]})")
    # CC BY 4.0 s3(a)(1)(B): the video crops, moves and captions each photo, and says so (rc68 review 2)
    # The prefix stays as it is (beat_images.CREDIT_PREFIX finds and replaces this line); the notice goes at its end
    from .beat_images import CREDIT_PREFIX
    return (CREDIT_PREFIX + " " + "; ".join(parts) + CREDIT_ADAPTED) if parts else ""


# ---------------------------------------------------------------- review helpers
TIER_ORDER = ("topic", "kept", "context", "unchecked", "weak")


def sheet_order(topic_dir: Path, images: List[Dict[str, Any]],
                storyboards: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The order photos are numbered on the contact sheet: those that show the topic first (most relevant
    first), then those you kept, context, unchecked and weak (src/image_review.py tiers). /photos N drop and keep use
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
    # Your approval stands, but say when the video could not use the set as it is (it counts Topic + Kept +
    # at most a couple of Context photos: production 2026-10-28 runs failed at start on approved sets).
    from .image_review import readiness, readiness_text
    r = readiness(credits["images"], need=min_images)
    note = "" if r["ready"] else (
        f" Photos not yet checked are looked at before a run; for now {readiness_text(credits['images'], min_images)}."
        if r["pending"] else
        f" A video cannot use it yet: {readiness_text(credits['images'], min_images)}. Keep a photo with "
        f"/photos N keep or add photos that show the topic itself.")
    return f"Approved {len(credits['images'])} images for {topic_dir.name}." + note


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
    # "Too few" is counted as the video counts (image_review.readiness, 2026-10-28): photos not yet checked
    # still count, as they are screened before a run; context shots beyond the couple a video uses do not.
    from .image_review import readiness, readiness_text
    r = readiness(images, bool(credits.get("reviewed")))
    still_approved = r["ready"] or r["pending"]
    credits["reviewed"] = still_approved
    save_credits(topic_dir, credits)
    if images:
        write_contact_sheet(topic_dir, images)
    return (f"Removed {len(set(numbers))}; {len(images)} images remain in {topic_dir.name}"
            + ("; the set stays approved." if still_approved else
               f" ({readiness_text(images)})." if images else "."))


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
