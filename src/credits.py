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

from . import hold_advice

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
# Where a sentence of the artist field ends: a stop after a word, then space. Not after an abbreviation that stands
# before a name ("from St. Petersburg", "Mt. Athos", "Dr. Jane Doe"): its full stop cut "Andrey Korchagin from St.
# Petersburg, Russia" in two and the credit printed "Unknown" (review of QF-126, 2026-11-14).
_SENTENCE_END = re.compile(r"(?<=\w\w[.!?])(?<!\b(?:St|Mt|Ft|Pt|Dr|Mr|Ms)\.)(?<!\b(?:Mrs|Rev)\.)(?<!\bProf\.)\s+")
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
    kept = [s for s in _SENTENCE_END.split(text) if s.strip() and not _NOTICE.match(s.strip())]
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
    works = compact_credit_works(line)
    if works:   # QF-126b: one line for a licence's photos; its links are each in a slot the line's shape checks
        text = list(dict.fromkeys([works[0][2]] + [part for author, title, *_ in works for part in (author, title)]))
        urls = []
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
    if not line.startswith("📷") or compact_credit_works(line):   # a compact line (QF-126b) is read by its own shape
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
    if any(rewrite_credit_line(old) == line for old in (text or "").splitlines() if _credit_fields(old)):
        return True
    # ... or a compact line that credits this very photo with the same five parts (QF-126b)
    fields = _credit_fields(line)
    if len(fields) != 5:
        return False
    author, title, licence, source, licence_url = fields
    links = {source, short_source_url(record)} - {"Unknown"}
    return licence_url != "Unknown" and any(
        work[:3] == (author, title, licence) and work[3] in links
        and licence_link_key(work[4]) == licence_link_key(licence_url)
        for old in (text or "").splitlines() for work in compact_credit_works(old))


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


# ---------------------------------------------------------------- the credits a post must carry (QF-126)
# CC BY 4.0 s.3(a)(1): the creator, the title (as supplied), a link to the material, the licence and its link, and
# an indication that the material was modified. The catalogue audit (2026-11-14, R3) found the music line without
# its two links or "edited", no modification notice beside the per-photo lines, and nothing that checked the
# credits against the text each platform receives: a Reel's credits were looked for in its caption OR its
# description, and a Reel posts no description, so a photo credit that did not fit the caption was posted nowhere.
# Licence compliance comes before a short caption: a credit is never shortened or left out to fit; a post whose
# credits do not fit is held, with the reason.
CAPTION_MAX = 2200            # Instagram's caption limit (the Reel's caption is Facebook's and the Short's text too)
YOUTUBE_DESCRIPTION_MAX = 5000
# Its own line, matched whole (as the share-alike notice is): it is not a photo's credit line, so it does not start
# with 📷 (the lines that do are one per photo, and are counted and rewritten as such).
ADAPTED_NOTICE = "🎬 Photos adapted for this video: cropped, with motion and text added."


# ---------------------------------------------------------------- credits that fit (QF-126b)
# QF-126 holds a post whose credits do not fit its text, and one line a photo (about 230 characters: the licence's
# address and the file page's full address on every line) left half of the real Reels held: 14 of 28 development runs
# and 14 of 32 in the production sample. Nothing a licence asks for is given up to fit. What repeated is written
# once: one line for each licence, naming the licence and its address once, then each author once with each of that
# author's photos, title and link to its page:
#   📷 CC BY 4.0 https://creativecommons.org/licenses/by/4.0 | Ann Lee: “File:Vault.jpg” https://… |
#   “File:Door.jpg” https://… | Sam Roe: “File:Stair.jpg” https://…
# Every photo keeps its five parts (author, title, licence name, link to the photo's page, link to the licence), as
# CC BY 4.0 s.3(a)(1) lists them and s.3(a)(2) allows ("any reasonable manner based on the medium, means, and
# context"); a field ends at " | ", which no author, title or licence can contain (sanitise_credit_text), a title is
# in quotation marks and each link follows its title, so the line reads back into the same five parts per photo
# (compact_credit_works) and the release gate checks them photo by photo (credit_present). The link to a Wikimedia
# Commons file page may be the page's own address by its page id (https://commons.wikimedia.org/?curid=<id>):
# MediaWiki's documented `curid` parameter ("The ID of the wiki page; overrides the value of title",
# mediawiki.org Manual:Parameters_to_index.php), on Commons' own host, which serves the file page; the id is kept
# when the file is renamed (Manual:Page_table), where the address by title depends on a redirect being left. It is
# used only for a page id the Commons API returned for that very page (commons.file_page_ids), kept beside the
# run's ledger (SOURCE_LINKS_FILE); never a third party's shortener. The one-line-a-photo form is still written
# wherever it fits, and is still what the description and the documentary carry; a carousel's caption takes the
# compact form, too, when its one-photo lines do not fit (with_compact_photo_lines; page ids as recorded, no lookup).
COMMONS_HOST = "commons.wikimedia.org"
SOURCE_LINKS_FILE = "source_links.json"   # in the run's folder: {"page_ids": {source link: Commons page id}}
_COMPACT_HEAD = re.compile(r"^(?P<licence>[^|“”]+?) (?P<url>https?://[^\s|<>\"]+)$")
_COMPACT_WORK = re.compile(r"^(?:(?P<author>[^|“]+?): )?“(?P<title>[^|]*)” (?P<url>https?://[^\s|<>\"]+)$")


def commons_file_title(source_url) -> str:
    """The Wikimedia Commons file page a source link names ("File:Vault.jpg"), "" for any other link."""
    from urllib.parse import unquote, urlsplit
    try:
        parts = urlsplit(str(source_url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != COMMONS_HOST or parts.query or parts.fragment:
        return ""
    title = unquote(parts.path.removeprefix("/wiki/")).replace("_", " ").strip() if parts.path.startswith("/wiki/") else ""
    return title if title.startswith("File:") and len(title) > len("File:") else ""


def short_source_url(record) -> str:
    """The link to a photo's page as a compact credit prints it: a Commons file page whose page id is known
    (record["pageid"], from the Commons API) by that id, any other source by its link as recorded."""
    url = _credit_url((record or {}).get("source_url"))
    page = (record or {}).get("pageid")
    if isinstance(page, int) and not isinstance(page, bool) and page > 0 and commons_file_title(url):
        return f"https://{COMMONS_HOST}/?curid={page}"
    return url


def source_page_ids(run_dir: Path | None) -> dict[str, int]:
    """{source link: Commons page id} recorded for a run (SOURCE_LINKS_FILE in its folder), {} when there is none."""
    if not run_dir:
        return {}
    try:
        found = json.loads((Path(run_dir) / SOURCE_LINKS_FILE).read_text(encoding="utf-8")).get("page_ids") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(url): page for url, page in found.items()
            if isinstance(page, int) and not isinstance(page, bool) and page > 0} if isinstance(found, dict) else {}


def with_page_ids(records: list, run_dir: Path | None) -> list:
    """`records` with the Commons page id the run's folder records for a photo's source link (source_page_ids) on
    each record that has none: its one-photo line is the same, and a compact line may name its page by that id."""
    pages = source_page_ids(run_dir)
    return [{**r, "pageid": pages[str(r.get("source_url"))]}
            if isinstance(r, dict) and not r.get("pageid") and str(r.get("source_url")) in pages else r
            for r in records]


def with_compact_photo_lines(lines: list[str], records: list) -> list[str]:
    """`lines` (credit lines in the order they are written: notices and one line a photo) with the one-photo lines
    of `records` that compact_photo_credits can carry replaced by its compact lines, at the place of the first of
    them; every other line (a notice, a photo the compact form cannot carry) as it is. A carousel's caption takes
    this form when its one-photo lines do not fit (QF-126b): the same photos, each with its five parts."""
    wanted = [r for r in records if isinstance(r, dict) and asset_attribution(r) in lines]
    compact, covered = compact_photo_credits(wanted)
    if not compact:
        return list(lines)
    out: list[str] = []
    for line in lines:
        if line in covered:
            out, compact = out + compact, []
        else:
            out.append(line)
    return out


def find_source_links(ledger: Path, lookup) -> int:
    """Looks up the Commons page id of every photo the run must credit whose id is not yet known, with `lookup`
    ({source link} -> {source link: page id}; commons.file_page_ids), and records them beside the ledger. Returns how
    many were found. A lookup that fails finds none: the credits then keep the links they have."""
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    return find_page_ids(Path(ledger).parent, list(data.get("assets", [])) + _reference_records(data), lookup)


def find_page_ids(run_dir: Path, records: list, lookup) -> int:
    """find_source_links for any credit records (a Story's photos, QF-126c, before the run has a ledger): the Commons
    page id of each Commons photo among `records` whose licence asks for a credit and whose id is not yet known,
    recorded in the run's folder (SOURCE_LINKS_FILE). Returns how many were found; a failed lookup finds none."""
    run_dir = Path(run_dir)
    known = source_page_ids(run_dir)
    wanted = sorted({str(r.get("source_url")) for r in records
                     if isinstance(r, dict) and needs_attribution(r) and commons_file_title(r.get("source_url"))
                     and not r.get("pageid") and str(r.get("source_url")) not in known})
    if not wanted:
        return 0
    try:
        found = {url: page for url, page in (lookup(wanted) or {}).items()
                 if url in wanted and isinstance(page, int) and not isinstance(page, bool) and page > 0}
    except (OSError, ValueError, RuntimeError, LookupError, TypeError, AttributeError) as exc:
        # a shorter link is a saving, never a new way for a run to fail (requests' errors are OSErrors)
        import logging
        logging.getLogger(__name__).warning("Credits: the photos' page ids could not be looked up (%s); the credits "
                                            "keep their full links.", exc)
        return 0
    if found:
        from .atomic_files import write_json
        try:
            write_json(run_dir / SOURCE_LINKS_FILE, {"page_ids": {**known, **found}})
        except OSError:
            return 0
    return len(found)


def short_link_lookup():
    """The lookup add_provenance_attributions may use for shorter links to the photos' pages (the Commons API), or
    None when CREDIT_SHORT_SOURCE_LINKS is off."""
    from . import config
    if not getattr(config, "CREDIT_SHORT_SOURCE_LINKS", True):
        return None
    from .commons import file_page_ids
    return file_page_ids


def compact_credit_works(line: str) -> list[tuple[str, str, str, str, str]]:
    """The photos a compact credit line credits, each as (author, title, licence name, link to the photo's page, link
    to the licence): the same five parts as a one-photo line. [] for any other line, and for a compact line any field
    of which does not read back whole (a line cut short credits nothing)."""
    line = (line or "").strip()
    if not line.startswith("📷") or "“" not in line:
        return []
    fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
    head = _COMPACT_HEAD.match(fields[0])
    if not head or len(fields) < 2:
        return []
    works, author = [], ""
    for field in fields[1:]:
        work = _COMPACT_WORK.match(field)
        author = (work.group("author") or author) if work else ""
        if not work or not author:
            return []
        works.append((author, work.group("title"), head.group("licence"), work.group("url"), head.group("url")))
    return works


def compact_photo_credits(records: list) -> tuple[list[str], list[str]]:
    """(the compact credit lines for `records`, the one-photo lines they stand for). One line for each licence, in
    the order the licences first appear; in it each author once, in the order they first appear, with each of that
    author's photos. A photo is written this way only when its line reads back to exactly its own five parts: one
    without a source or licence link (held by the gate as incomplete), the operator's own photograph, a public-domain
    photo a source asks a credit for, or a name with a quotation mark keeps its one-photo line."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    seen = set()
    for record in records:
        long = asset_attribution(record)
        if not long or long in seen:
            continue
        seen.add(long)
        author, title, licence, source, licence_url = _credit_fields(long) or [""] * 5
        link = short_source_url(record)
        if "Unknown" in (link, licence_url, source) or not (author and licence):
            continue
        group = groups.setdefault((licence, licence_link_key(licence_url)),
                                  {"licence": licence, "link": licence_url, "authors": {}, "stands_for": []})
        group["authors"].setdefault(author, []).append((title, link))
        group["stands_for"].append((long, (author, title, licence, link, group["link"])))
    lines, covered = [], []
    for group in groups.values():
        fields = [f"{group['licence']} {group['link']}"]
        for author, works in group["authors"].items():
            fields += [(f"{author}: " if n == 0 else "") + f"“{title}” {link}" for n, (title, link) in enumerate(works)]
        line = "📷 " + " | ".join(fields)
        if sorted(compact_credit_works(line)) != sorted(parts for _, parts in group["stands_for"]):
            continue   # it would not read back as written: these photos keep their own lines
        lines.append(line)
        covered += [long for long, _ in group["stands_for"]]
    return lines, covered


def licence_link_key(url: str) -> str:
    """What makes two licence links the same link: creativecommons.org serves one deed at its address over http or
    https, with or without the final "/" (records hold both), so a licence is named once with the first link
    recorded for it. Any other address is itself."""
    deed = re.fullmatch(r"https?://(?:www\.)?creativecommons\.org(/[^?#\s]*?)/?", str(url or "").strip(), re.IGNORECASE)
    return "creativecommons.org" + deed.group(1).lower() if deed else str(url or "").strip()


def _bed_record(data: dict, run_dir: Path | None) -> dict:
    """The music bed's rights record (audio_provenance.record_bed) of a ledger: by the bed's file name in `run_dir`
    (the ledger's own folder; a run folder read on another machine too), else beside the bed where the ledger says
    it is. {} when there is none."""
    from pathlib import PureWindowsPath
    bed = (data.get("ambient_bed") or {}).get("path")
    if not bed:
        return {}
    name = PureWindowsPath(str(bed)).name + ".rights.json"
    for candidate in ([Path(run_dir) / name] if run_dir else []) + [Path(str(bed) + ".rights.json")]:
        try:
            record = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return record if isinstance(record, dict) else {}
    return {}


def required_credits(data: dict, run_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every credit a post of the run's video must carry, from its provenance ledger (`data`), in the order they
    are written: the share-alike notice, one line for each photo (and each illustration's reference photo) whose
    licence asks for a credit, the notice that the photos were adapted, and one line for each music track whose
    licence asks for one (rebuilt from the bed's rights record, so a run rendered before QF-126 is asked for the
    complete line too). Each is {"kind", "line", "record"}. Public-domain and CC0 assets ask for none. A photo's
    record carries its Commons page id when the run's folder records one (source_page_ids, QF-126b): its line is the
    same, and a compact line may then name its page by that id."""
    records = with_page_ids(list(data.get("assets", [])) + _reference_records(data), run_dir)
    out: list[dict[str, Any]] = []
    if any(is_share_alike(r.get("license") or "") for r in records):
        out.append({"kind": "share_alike", "line": SHARE_ALIKE_NOTICE, "record": None})
    seen = set()
    for record in records:
        line = asset_attribution(record)
        if line and line not in seen:
            seen.add(line)
            out.append({"kind": "image", "line": line, "record": record})
    if seen:
        out.append({"kind": "adapted", "line": ADAPTED_NOTICE, "record": None})
    from .music import credit_line
    for track in _bed_record(data, run_dir).get("sources") or []:
        line = credit_line(track) if isinstance(track, dict) and track.get("title") else ""
        if line and line not in seen:
            seen.add(line)
            out.append({"kind": "music", "line": line, "record": track})
    return out


def _file_key(path) -> str:
    """A recorded file path as it is compared: its separators and letter case made one."""
    return str(path or "").strip().replace(chr(92), "/").rstrip("/").lower()


def _file_keys(path) -> set[str]:
    """A file's path as recorded and as it resolves here (a ledger records resolved paths)."""
    try:
        return {_file_key(path), _file_key(Path(path).resolve())} - {""}
    except (OSError, ValueError, TypeError):
        return {_file_key(path)} - {""}


def _photo_identity(photo: Path) -> tuple[set[str], set[str]]:
    """(paths, checksums) of a photo and of the files it was made from, as its lineage records them (a crop, blur or
    upscale keeps a lineage file beside it), up to four steps back. A file that is gone keeps its recorded path, and
    a source its recorded checksum."""
    from .lineage import sidecar
    paths: set[str] = set()
    sums: set[str] = set()
    todo = [(Path(photo), "")]
    for _ in range(4):
        following = []
        for path, recorded in todo:
            if _file_key(path) in paths:
                continue
            paths |= _file_keys(path)
            digest = recorded or sha256_file(path)
            if digest:
                sums.add(digest)
            try:
                sources = json.loads(sidecar(path).read_text(encoding="utf-8"))["sources"]
                following += [(Path(str(s["path"])), str(s.get("sha256") or "")) for s in sources
                              if isinstance(s, dict) and s.get("path")]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        todo = following
    return paths, sums


def ledger_photo_credits(data: dict, photo: Path) -> list[dict[str, Any]] | None:
    """The credit records a run's provenance ledger (`data`) asks for one photo, found by the photo's recorded path
    or checksum, through its lineage: the ledger asset that is the photo, that the photo was made from, or that was
    made from the photo. [] when the ledger's record asks for none (public domain or CC0; the run's own drawing or
    illustration, whose reference photos' credits are returned when a licence asks for them). None when the ledger
    does not hold the photo, or holds it without saying what its licence is: nothing then decides, and the caller
    must credit every photo of the run or hold the post (review of QF-126, round 2: a carousel photo whose
    credits.json entry cannot be read)."""
    paths, sums = _photo_identity(photo)
    own = _file_keys(photo)
    own_sum = sha256_file(Path(photo))
    recorded = {_file_key(s.get("path")): str(s.get("sha256") or "")
                for s in data.get("derived_sources") or [] if isinstance(s, dict)}
    out: list[dict[str, Any]] = []
    found = False
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        made_from = {_file_key(p) for p in asset.get("source_paths") or []}
        is_or_from_asset = bool(_file_keys(asset.get("path")) & paths) or (asset.get("sha256") or None) in sums
        asset_from_photo = bool(own & made_from) or bool(own_sum and own_sum in {recorded.get(p) for p in made_from})
        if not (is_or_from_asset or asset_from_photo):
            continue
        found = True
        if needs_attribution(asset):
            out.append(asset)
        elif asset.get("illustration") or asset.get("origin") in ("archaeological_reconstruction", "original_drawing"):
            out += [r for r in _reference_records({"assets": [asset]}) if needs_attribution(r)]
        elif not _PUBLIC_DOMAIN.match(str(asset.get("license") or "").strip()):
            return None   # in the ledger, but its licence is not recorded: nothing says no credit is asked
    return out if found else None


def credit_carried(item: dict[str, Any], text: str) -> bool:
    """Whether `text` carries the required credit `item` whole: a photo's line (or a line written before QF-42 that
    rewrites to it, credit_present), the adapted notice (or the summary line's own "adapted" clause), any other line
    as one of the text's lines."""
    text = text or ""
    if item["kind"] == "image":
        return credit_present(item["record"], text)
    if item["kind"] == "adapted":
        return any(line.strip() == ADAPTED_NOTICE or (line.startswith("📷") and line.rstrip().endswith(CREDIT_ADAPTED))
                   for line in text.splitlines())
    return item["line"].strip() in {line.strip() for line in text.splitlines()}


def incomplete_credit(item: dict[str, Any], regenerate: bool = True) -> str:
    """What a required credit lacks ("" when it is complete): a photo or music line needs its source page and its
    licence's address as http(s) URLs (asset_attribution and music.credit_line print "Unknown" for a missing one),
    A public-domain or CC0 item a source asks a credit for has no licence to link to, and the operator's own
    photograph no source page. An author the source does not name stays the operator's call (asset_rights sends a
    CC BY photo without one for review): the licence asks for the creator "if supplied". A photo line that prints
    "Unknown" while the record's artist field says something else is held for a person to check. `regenerate`:
    whether a new version without the photo can still be made (not for a carousel, whose Reel is already posted)."""
    if item["kind"] not in ("image", "music"):
        return ""
    fields = [f.strip() for f in item["line"].split(" | ")]
    if len(fields) != 5:
        return "its fields (author, title, licence, source link, licence link)"
    licence, source, licence_url = (fields[2:5]) if item["kind"] == "image" else (fields[1:4])
    record = item.get("record") or {}
    # The operator's own photograph has no public page: its source is recorded as "Own work ..." (image_upload).
    own = bool(record.get("own_work")) or str(record.get("source_url") or "").strip().lower().startswith("own work")
    wanted = ([] if own else [("its source link", source)]) + (
        [] if _PUBLIC_DOMAIN.match(licence) else [("its licence link", licence_url)])
    lacking = [what for what, value in wanted if value == "Unknown" or _credit_url(value) != value]
    if item["kind"] == "image" and fields[0].removeprefix("📷").strip() == "Unknown" and _names_someone(record):
        lacking.insert(0, "its author (the source names one: check the credit"
                          + (f"{hold_advice.AUTHOR_REGENERATE})" if regenerate else ")"))
    return " and ".join(lacking)


def _names_someone(record: dict[str, Any]) -> bool:
    """Whether a record's artist field says something other than that the author is unknown: the credit then prints
    "Unknown" for an author the source may name, and a person must look (the licence asks for the name if supplied)."""
    import html as _html
    text = " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", str((record or {}).get("artist") or ""))).split())
    text = _DOUBLED.sub(r"\1", text).strip(" .").lower()
    return bool(text) and not text.startswith(("unknown", "anonymous", "not known", "n/a", "none"))


def credit_reach_problems(texts: dict[str, str], required: list[dict[str, Any]],
                          limits: dict[str, int] | None = None) -> list[str]:
    """Why a post must be held for its credits, [] when every platform's text carries every required credit whole.
    `texts`: {what the platform receives, by name: the exact string sent}; `limits`: {name: its character limit}. A
    text longer than its limit is refused or cut by the platform, and a text as long as its limit was cut to it by
    its builder (publisher.youtube_fields): the credits are the text's last lines, so they are what is lost."""
    from . import config
    from .publishable import YOUTUBE_DESCRIPTION
    problems = []
    subjects = _credit_subjects(required)   # no two read alike (UI-01h)
    for item in required:
        lacking = incomplete_credit(item)
        if lacking:
            problems.append(f"The credit for {subjects[id(item)]} lacks {lacking}, which its licence asks for.")
    # One reason for the texts that lack the same credits (a Short's description is its Reel's caption): the first
    # text's, with its room.
    found: dict[tuple, tuple[str, list[str]]] = {}
    for name, text in texts.items():
        text = text or ""
        limit = (limits or {}).get(name)
        if limit and len(text) > limit:
            problems.append(f"{name}: {len(text):,} characters with its credits; the platform takes {limit:,}. "
                            + hold_advice.SHORTEN_LIMIT)
            text = text[:limit]
        missing = [item for item in required if not credit_carried(item, text)]
        if missing:
            which = (f"{'; '.join(subjects[id(m)] for m in missing[:3])}"
                     f"{' and more' if len(missing) > 3 else ''}")
            cut = bool(limit) and len(text) >= limit
            # the documentary's description keeps one line a photo; every other text is written from the caption
            compact = name != YOUTUBE_DESCRIPTION
            over = _credits_over(text, required, limit, compact=compact) if limit and not cut else 0
            # a photo whose page id is not recorded yet (a copy written before QF-126b, a lookup that failed) gets one
            # when the credits are written again: measured with the longest id, it is not told "over" when a re-send
            # or an Edit would fit it (no lookup here, none at posting); when it is over, the figure it is told is
            # this one too, an upper bound that is never below what the Edit (which writes those links) must cut
            shortened = False
            if over > 0 and compact and getattr(config, "CREDIT_SHORT_SOURCE_LINKS", True):
                over = _credits_over(text, _with_page_id_bound(required), limit)
                shortened = over <= 0
            if shortened:
                reason = (f"{len(missing)} of {len(required)} required credit(s) are not in it yet: {which}. They "
                          "fit, and nothing needs to be cut: the photos' page links are shortened when the credits "
                          f"are written again{hold_advice.FIT_LINKS}. {hold_advice.FIT_WRITE}")
            elif not cut and over <= 0:
                # Nothing is too long: the copy was written before these credits were asked for (a run waiting or
                # booked when QF-126 was deployed), and writing its credits again is all it needs.
                reason = (f"{len(missing)} of {len(required)} required credit(s) are not in it yet: {which}. They "
                          "fit: this copy was written before they were asked for, and nothing needs to be cut. "
                          + hold_advice.FIT_WRITE)
            else:
                room = (f" (the text is cut at the platform's {limit:,} characters)" if cut
                        else f" (with them it is {over:,} characters over the platform's {limit:,})")
                reason = (f"{len(missing)} of {len(required)} required credit(s) are not in it{room}: {which}. "
                          + hold_advice.SHORTEN)
            found.setdefault(tuple(item["line"] for item in missing), (reason, []))[1].append(name)
    return problems + [f"{' and '.join(names)}: {reason}" for reason, names in found.values()]


def _credits_over(text: str, required: list[dict[str, Any]], limit: int, compact: bool = True) -> int:
    """By how many characters `text` with every required credit it lacks passes `limit` (0 or less: they fit), as
    add_provenance_attributions would write them: each on its own paragraph, the summary credit line giving way
    when that is what makes room, and (`compact`, a caption's text) the photos' credits in the compact form when that
    is shorter (QF-126b, with the page ids the `required` records carry; credit_reach_problems measures again with
    the longest id for a photo without one, _with_page_id_bound, so a hold does not say "over" when writing the
    credits again would fit them)."""
    def length(body: str) -> int:
        return len(body.rstrip()) + sum(len(item["line"]) + 2 for item in required if not credit_carried(item, body))

    from .beat_images import CREDIT_PREFIXES
    # as it writes them: lines written before QF-42 rewritten first, the complete music line replacing the short one
    text = _drop_superseded_music(rewrite_credit_lines(text), required)
    without = "\n".join(line for line in text.splitlines() if not line.startswith(CREDIT_PREFIXES)).strip()
    forms = [length(text), length(without)]
    if compact:   # a caption's credits (add_provenance_attributions); not the documentary's description
        forms += [len(_with_compact_credits(_without_compact_lines(body), required).rstrip()) for body in (text, without)]
    return min(forms) - limit


# The longest page-id link a Commons file page is measured with when its id is not recorded yet: Commons page ids have
# at most nine digits (the highest of the real runs' ids is 198,099,384), so "?curid=" and nine 9s is an upper bound
# for the link a lookup gives while ids stay below a billion.
_PAGE_ID_BOUND = 999_999_999


def _with_page_id_bound(required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`required` with each Commons photo whose page id is not recorded given the longest one (_PAGE_ID_BOUND), for
    measuring only: its compact line is then as long as a lookup could make it, never shorter. Its line (the one-photo
    form) is unchanged."""
    return [{**item, "record": {**item["record"], "pageid": _PAGE_ID_BOUND}}
            if item["kind"] == "image" and isinstance(item.get("record"), dict) and not item["record"].get("pageid")
            and commons_file_title(item["record"].get("source_url")) else item
            for item in required]


def ledger_credit_problems(ledger: Path, texts: dict[str, str], limits: dict[str, int] | None = None) -> list[str]:
    """credit_reach_problems for the run whose provenance ledger is `ledger` (its bed's rights record is read from
    the ledger's folder). An unreadable ledger is a problem: what the post must credit is then unknown."""
    try:
        data = json.loads(Path(ledger).read_text(encoding="utf-8"))
        required = required_credits(data, Path(ledger).parent)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return [f"The run's provenance record could not be read ({type(exc).__name__}), so its credits cannot be checked."]
    return credit_reach_problems(texts, required, limits)


def _sheet_number(record: dict[str, Any], cache: dict | None = None) -> int | None:
    """The number of the photo a credit record is for on its topic's /photos contact sheet (sheet_order), or None
    when it is not one of a topic's photos here (a beat image, a run made on another machine for a topic not here,
    a photo dropped since). The record names the photo by its path, or a path it was made from, under
    assets/<topic>/ (the ledger records its own machine's paths: only the topic and the file name are used)."""
    from .config import ASSETS_DIR
    cache = {} if cache is None else cache
    paths = [record.get("path"), *(record.get("source_paths") or [])] if isinstance(record, dict) else []
    for path in paths:
        parts = [p for p in str(path or "").replace(chr(92), "/").split("/") if p]
        found = [i for i, p in enumerate(parts) if p.lower() == "assets" and i == len(parts) - 3]
        if not found:
            continue
        topic, name = parts[-2], parts[-1]
        if topic not in cache:
            topic_dir = Path(ASSETS_DIR) / topic
            try:
                shown = sheet_order(topic_dir, load_credits(topic_dir)["images"]) if topic_dir.is_dir() else []
                cache[topic] = [str(i.get("file")) for i in shown if (topic_dir / str(i.get("file"))).is_file()]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                cache[topic] = []
        if name in cache[topic]:
            return cache[topic].index(name) + 1
    return None


def _distinct_title(title: str, others: list[str]) -> str:
    """`title` cut so that it reads unlike each of `others` (the whole titles of other photos whose cut titles read
    alike): its beginning, cut at a word, then "…" and the fewest last words no other title ends with (a bracket
    opened in them is kept whole). The whole title when it is short enough."""
    words = title.split(" ")
    k = 1
    while k < len(words) and any(o.split(" ")[-k:] == words[-k:] for o in others):
        k += 1
    while k < len(words) and " ".join(words[-k:]).count(")") > " ".join(words[-k:]).count("("):
        k += 1
    tail = " ".join(words[-k:])
    if len(title) <= TITLE_MAX + len(tail) // 2 or k >= len(words) - 1:
        return title
    room = max(16, TITLE_MAX - len(tail) - 2)
    head = []
    for w in words[:len(words) - k]:
        if len(" ".join(head + [w])) > room:
            break
        head.append(w)
    head_text = " ".join(head).rstrip(" ,;:-–—/(") or words[0][:room]
    return f"{head_text}… {tail}"


def _credit_subjects(items: list[dict[str, Any]]) -> dict[int, str]:
    """Each required credit named for the operator (by id(item)), so that no two read alike in one hold reason
    (UI-01h: two photos "Interior of Catacombs - Partisan Resistance Base…" by the same author): a photo on its
    topic's /photos sheet is named by its number there and its title ("photo 3, “title” by author", the
    photos-by-sheet-number convention); a title cut alike as another's keeps the words that tell them apart
    (_distinct_title); two photos with the same whole title and author and no sheet number are counted."""
    cache: dict = {}
    images = [i for i in items if i["kind"] == "image"]
    names = {id(i): (credit_title(i.get("record") or {}), author_name(i.get("record") or {})) for i in images}
    numbers = {id(i): _sheet_number(i.get("record") or {}, cache) for i in images}
    titles = {key: title for key, (title, _) in names.items()}
    for item in images:
        alike = [o for o in images if o is not item and names[id(o)] == names[id(item)]]
        if not alike:
            continue
        whole = sanitise_credit_text((item.get("record") or {}).get("title")) or titles[id(item)]
        others = [sanitise_credit_text((o.get("record") or {}).get("title")) for o in alike]
        others = [o for o in others if o != whole]
        if others:
            titles[id(item)] = _distinct_title(whole, others)
    out = {}
    for item in items:
        if item["kind"] != "image":
            out[id(item)] = _credit_subject(item)
            continue
        number = numbers[id(item)]
        same = [o for o in images if (titles[id(o)], names[id(o)][1], numbers[id(o)])
                == (titles[id(item)], names[id(item)][1], number)]
        count = f" (file {same.index(item) + 1} of {len(same)} so named)" if len(same) > 1 else ""
        out[id(item)] = (f"photo {f'{number}, ' if number else ''}“{titles[id(item)]}”{count} "
                         f"by {names[id(item)][1]}")
    return out


def _credit_subject(item: dict[str, Any]) -> str:
    """A required credit named for the operator: "photo “title” by author", "music “title” by creator", or what
    the notice is."""
    record = item.get("record") or {}
    if item["kind"] == "image":
        return f"photo “{credit_title(record)}” by {author_name(record)}"
    if item["kind"] == "music":
        return f"music “{record.get('title') or 'Unknown'}” by {record.get('creator') or 'Unknown'}"
    return "the share-alike licence notice" if item["kind"] == "share_alike" else "the notice that the photos were adapted"


def _append_credits(text: str, lines: list[str]) -> str:
    for line in lines:
        text = (text or "").rstrip() + "\n\n" + line
    return text or ""


def _drop_superseded_music(text: str, required: list[dict[str, Any]]) -> str:
    """`text` without a music credit written before QF-126 ("🎵 Music: “title” by creator (CC BY 4.0)") for a track
    whose complete line is required: the complete line replaces it, it does not join it."""
    tracks = [(item["line"], item["line"].split(" | ")[0]) for item in required if item["kind"] == "music"]
    lines = (text or "").splitlines()
    kept = [line for line in lines
            if not any(line.strip() != whole and line.strip().startswith(head + " (") for whole, head in tracks)]
    return (text or "") if len(kept) == len(lines) else "\n".join(kept)


def _without_compact_lines(text: str) -> str:
    """`text` without its compact photo credit lines: they are the pipeline's own writing of the ledger's credits,
    written again, in the form that fits, each time the credits are."""
    lines = (text or "").splitlines()
    kept = [line for line in lines if not compact_credit_works(line)]
    return (text or "") if len(kept) == len(lines) else re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip()


def _with_compact_credits(caption: str, required: list[dict[str, Any]]) -> str:
    """`caption` with every required credit, the photos' in the compact form (compact_photo_credits). The caption's
    one-photo lines are taken out first, with or without their 📷 (they are the pipeline's own: the photos the
    ledger asks a credit for are all written again below, and a line left by an earlier render for a photo this
    video does not show is not one of its credits), and the compact lines take the photos' place in the order of
    the credits (the share-alike notice, the photos, the adapted notice, the music). A photo the compact form
    cannot carry keeps its one-photo line."""
    lines, covered = compact_photo_credits([item["record"] for item in required if item["kind"] == "image"])
    if not lines:
        return _append_credits(caption, [item["line"] for item in required if not credit_carried(item, caption)])
    own = {item["line"].strip() for item in required}   # written again below, in their order (no drift on a rewrite)
    kept = [line for line in caption.splitlines()
            if not (_credit_fields(line) or _CREDIT_SHAPE.match(line.strip()) or line.strip() in own)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip()
    out: list[str] = []
    for item in required:
        if item["kind"] == "image" and item["line"] in covered:
            out, lines = out + lines, []
        elif not credit_carried(item, text):
            out.append(item["line"])
    return _append_credits(text, out)


def add_provenance_attributions(payload, ledger, lookup=None):
    """`payload` with every credit its video must carry (required_credits) in its description and its caption. The
    description takes them all. The caption takes them all when they fit Instagram's 2,200 characters, the summary
    credit line ("📷 Images via ...") giving way first (the per-photo lines and the adapted notice say what it
    said); when the one-photo lines still do not fit, the photos' credits are written in the compact form (QF-126b:
    one line a licence, every photo with its five parts), with the page-id links `lookup` finds when it is given and
    they are needed (find_source_links); when they still do not fit, the ones that fit are added in order and the
    release gate holds the post (release "licence_credits"): a credit is never shortened, and the post never goes out
    without one. The length is the caption's as it is posted (with the illustration disclosure when the run has
    one)."""
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    # Credit lines already in the copy (a regeneration's held lines, a run written before QF-42) are rewritten
    # first, so the line added below is not a second copy of an old one.
    rewrite_payload_credits(payload)
    required = required_credits(data, Path(ledger).parent)
    if not required:
        return payload

    def missing(text):
        return [item["line"] for item in required if not credit_carried(item, text)]

    def fits(text):
        from .publisher import with_disclosure
        return len(with_disclosure(text, payload)) <= CAPTION_MAX

    from .beat_images import CREDIT_PREFIXES
    description = _drop_superseded_music(payload.get("description", ""), required)
    payload["description"] = _append_credits(description, missing(description))
    caption = _without_compact_lines(_drop_superseded_music(payload.get("caption", ""), required))
    whole = _append_credits(caption, missing(caption))
    if not fits(whole) and any(line.startswith(CREDIT_PREFIXES) for line in caption.splitlines()):
        caption = "\n".join(line for line in caption.splitlines() if not line.startswith(CREDIT_PREFIXES)).strip()
        whole = _append_credits(caption, missing(caption))
    if not fits(whole):   # QF-126b: the same credits, each licence and each author written once
        compact = _with_compact_credits(caption, required)
        if not fits(compact) and lookup is not None and find_source_links(Path(ledger), lookup):
            required = required_credits(data, Path(ledger).parent)   # with the page ids just found
            compact = _with_compact_credits(caption, required)
        if fits(compact):
            payload["caption"] = compact
            return payload
    if not fits(whole):   # held by the gate; what fits is still written, in order
        whole = caption
        for line in missing(caption):
            if fits(_append_credits(whole, [line])):
                whole = _append_credits(whole, [line])
    payload["caption"] = whole
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
    from . import host_state
    for credits_file in {run_dir / CREDITS_FILE, ASSETS_DIR / topic.get("id", "") / CREDITS_FILE}:
        if host_state.current(credits_file).is_file():   # the pool's: this host's copy (QF-111); files beside it
            try:
                data = pool_record(credits_file)
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
                       **({"pageid": rec["pageid"]} if isinstance(rec.get("pageid"), int) else {}),   # QF-126b
                       "worldwide_basis": _effective_basis(rec),
                       "license_verified_at": rec.get("license_verified_at"),
                       "attribution_required": False if in_house_gap_art else requires_attribution,
                       "rights_status": "verified" if rights_known or original_drawing or embedded_rights_verified or in_house_gap_art else "unverified"})
        assets.append(record)
    master = file_record(master_mp4)
    voice = file_record(vo_path) if vo_path else None
    bed = file_record(bed_path) if bed_path else None
    evidence_files = [file_record(p) for p in (run_dir / CREDITS_FILE,
                      host_state.current(ASSETS_DIR / topic.get("id", "") / CREDITS_FILE)) if p.is_file()]
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
# ... and a compact line (QF-126b: "licence licence-URL | author: “title” URL | ...") the same way.
_COMPACT_SHAPE = re.compile(r"^(?:public domain|pd\b|pd-|cc0|cc by)[^|\n“]*\shttps?://\S+\s*\|[^|\n]*“[^|\n]*”\s+https?://",
                            re.IGNORECASE)


def is_attribution_line(line: str) -> bool:
    """A credit line the pipeline adds to public copy (image, footage, music, the share-alike notice).
    The notice is matched exactly, not by its emoji, so model-written text starting with 🎬 is not one."""
    return (line.startswith(("📷", "🎥", "🎵")) or line.strip() in (SHARE_ALIKE_NOTICE, ADAPTED_NOTICE)
            or bool(_CREDIT_SHAPE.match(line.strip())) or bool(_COMPACT_SHAPE.match(line.strip())))


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


def _effective_basis(rec: dict[str, Any]) -> dict[str, Any] | None:
    from .image_rights import effective_basis
    return effective_basis(rec) or rec.get("worldwide_basis")


def rights_evidence_ok(rec: Dict[str, Any]) -> bool:
    """An allowed licence with its evidence: the source page, and the licence's URL. Public domain and CC0
    have no licence to link to, so their source page (where the status is documented) is the evidence;
    three public-domain Commons images were held for review for lacking a URL (production, 2026-10-23)."""
    licence = str(rec.get("license", "") or "")
    if not (license_allowed(licence) and rec.get("source_url")):
        return False
    from .source_policy import blocked_source
    if blocked_source(rec):   # an excluded source (QF-126), whatever licence the record states
        return False
    from .image_rights import rights_held
    if rights_held(rec):  # public domain needs a worldwide basis: stored, or read from the record's own evidence
        return False
    if rec.get("provider") == "openverse" and not rec.get("license_verified_at"):
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
    from . import host_state
    path = topic_dir / CREDITS_FILE
    if not host_state.exists(path):
        return {"reviewed": False, "images": []}
    data = pool_record(path)   # this host's copy of a pool's record (QF-111), else the shipped one
    if isinstance(data, list):  # legacy format
        data = {"reviewed": False, "images": data}
    data["images"] = [c for c in data.get("images", []) if (topic_dir / c.get("file", "")).exists()]
    return data


# A photo's rights and attribution: the shipped record's for the SAME photo (QF-111 review 1 B2, review 2 B4), so a
# release's credit-line correction (e.g. QF-42's sanitised artist) reaches a host that keeps its own review copy.
# The same photo is the same source (source_url), never the same file name: pool photos are named by position
# (NN_commons.jpg), and in the production sample 21 of 196 same-name photos are a different photo. source_url itself
# is the key and is never taken from the shipped record.
ATTRIBUTION_FIELDS = ("title", "artist", "author", "creator", "credit", "attribution", "license", "license_url",
                      "source", "attribution_required", "provider", "own_work")
HASH_FIELDS = ("sha256", "source_sha256")


def _same_photo(entry: dict[str, Any], shipped: dict[str, Any], folder: Path | None) -> bool:
    """Whether a shipped record entry describes the same photo as this host's entry: the same source_url (both
    set), every content hash both entries record equal, and, when both entries' files are on disk, the same bytes."""
    url = str(entry.get("source_url") or "").strip()
    if not url or url != str(shipped.get("source_url") or "").strip():
        return False
    if any(entry.get(k) and shipped.get(k) and entry[k] != shipped[k] for k in HASH_FIELDS):
        return False
    if folder is not None and entry.get("file") != shipped.get("file"):
        mine, theirs = folder / str(entry.get("file") or ""), folder / str(shipped.get("file") or "")
        if mine.is_file() and theirs.is_file() and sha256_file(mine) != sha256_file(theirs):
            return False
    return True


def with_shipped_attribution(data: dict[str, Any], shipped_path: Path) -> dict[str, Any]:
    """`data` (this host's copy of a pool's record) with the attribution fields of every photo the shipped record
    also lists (the same photo: _same_photo) taken from the shipped record; its source_url, file and review fields
    (reviewed, verdicts, keeps) stay this host's. Rejected photos are corrected the same way. A photo the shipped
    record does not list, an unreadable shipped record: unchanged."""
    try:
        shipped = json.loads(Path(shipped_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(shipped, list):
        shipped = {"images": shipped}
    if not isinstance(shipped, dict):
        return data
    folder = Path(shipped_path).parent
    out = dict(data)
    for key in ("images", "rejected_by_review"):
        by_url: dict[str, list] = {}
        for e in shipped.get(key) or []:
            if isinstance(e, dict) and str(e.get("source_url") or "").strip():
                by_url.setdefault(str(e["source_url"]).strip(), []).append(e)
        if not by_url or not isinstance(data.get(key), list):
            continue
        merged = []
        for entry in data[key]:
            if isinstance(entry, dict):
                candidates = by_url.get(str(entry.get("source_url") or "").strip(), [])
                source = next((c for c in candidates if _same_photo(entry, c, folder)), None)
                if source:
                    entry = {**{k: v for k, v in entry.items() if k not in ATTRIBUTION_FIELDS},
                             **{k: source[k] for k in ATTRIBUTION_FIELDS if k in source}}
            merged.append(entry)
        out[key] = merged
    return out


def pool_record(path) -> Any:
    """A folder's credits.json as this host reads it (QF-111): its own copy when it has one, with the shipped
    record's attribution of the same photos (with_shipped_attribution); else the shipped record. Every reader of a
    pool's record goes through here."""
    from . import host_state
    path = Path(path)
    data = host_state.read_json(path)
    if host_state.current(path) == path or not path.is_file():
        return data
    if isinstance(data, list):
        return with_shipped_attribution({"images": data}, path)["images"]
    return with_shipped_attribution(data, path) if isinstance(data, dict) else data


def save_credits(topic_dir: Path, data: Dict[str, Any]) -> None:
    """A pool's record (fetches, the operator's review, drops) goes to this host's copy, never the tracked
    assets/<topic>/credits.json (QF-111: production's rewritten copies made the deployer refuse releases). A folder
    outside the checkout (a run's, a test's) is written in place."""
    from . import host_state
    host_state.write_json(topic_dir / CREDITS_FILE, data)


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

    from .font_engine import choose_face, plain_face
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
            # QF-94: an archive title no bundled face draws (a CJK or Arabic file name) on this operator sheet is
            # drawn as before, its missing letters as visible .notdef boxes, in the face itself (plain_face): never a
            # failed sheet, and never a refusal recorded on the run's review card (QF-94b), since it is drawn
            face = font_text if choose_face(snippet, font_text) is not None else plain_face(font_text)
            draw.text((x + 12, y + cell_h - banner_h // 2 - 4), snippet, font=face, fill=(241, 245, 249), anchor="lm")

    path = topic_dir / CONTACT_SHEET
    sheet.save(path, quality=85)
    return path


def approve_set(topic_dir: Path, min_images: int) -> str:
    """Marks a fetched set reviewed. Raises ValueError if it can't be."""
    from . import host_state
    if not host_state.exists(topic_dir / CREDITS_FILE):
        raise ValueError(f"No fetched images for {topic_dir.name}.")
    credits = load_credits(topic_dir)
    if len(credits["images"]) < min_images:
        raise ValueError(f"Only {len(credits['images'])} images; need {min_images}. Re-run the fetcher to refill.")
    from . import photo_pool
    photo_pool.refresh(topic_dir, credits["images"])
    diversity = photo_pool.assess(credits["images"])
    credits["pool_diversity"] = diversity
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
    if not diversity["ok"]:
        note += " Diversity notice: " + "; ".join(diversity["problems"]) + "."
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
