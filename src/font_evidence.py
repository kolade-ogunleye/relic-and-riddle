"""Read licensing metadata from bundled TrueType files and verify local notices."""
import json
import struct
from pathlib import Path

from .credits import sha256_file


def embedded_names(path):
    data = Path(path).read_bytes()
    count = struct.unpack_from(">H", data, 4)[0]
    tables = {data[12 + i * 16:16 + i * 16]: struct.unpack_from(">II", data, 20 + i * 16)
              for i in range(count)}
    offset, length = tables[b"name"]
    _, count, strings = struct.unpack_from(">HHH", data, offset)
    fields = {}
    for index in range(count):
        platform, _, language, name, size, start = struct.unpack_from(">HHHHHH", data, offset + 6 + index * 12)
        if name not in (0, 5, 13, 14) or name in fields:
            continue
        raw = data[offset + strings + start:offset + strings + start + size]
        fields[name] = raw.decode("utf-16-be" if platform in (0, 3) else "mac_roman", errors="replace")
    return {"copyright": fields.get(0), "version": fields.get(5),
            "license_description": fields.get(13), "license_url": fields.get(14)}


# ---------------------------------------------------------------- reserved names and notices (QF-126)
# OFL 1.1 condition 3: a modified version may not use a Reserved Font Name; condition 2: every copy carries the
# copyright notice and the licence (catalogue audit 2026-11-14, R5). IBM Plex reserves "Plex"; its two files here are
# unmodified upstream copies, recorded as such. A subset, an instance or any other derived face (the Noto builds of
# scripts/build_fallback_fonts.py; faces built into a cache) is a modified version: it may not carry a reserved name
# and it keeps both notices in its name table.
_FACE_NAME_IDS = (1, 3, 4, 6, 16, 18, 21)   # family, unique id, full, PostScript, typographic family, compatible, WWS


def reserved_font_names(licence_text: str) -> list[str]:
    """The Reserved Font Names an OFL text declares in its copyright lines ('... with Reserved Font Name "Plex"',
    'with Reserved Font Names "A" and "B"', unquoted too); [] when it declares none. The licence's own definition
    of the term, below its preamble, is not a declaration."""
    import re
    names: list[str] = []
    for line in str(licence_text or "").splitlines():
        if line.strip().lower().startswith(("this font software is licensed", "preamble", "definitions")):
            break
        found = re.search(r"\bReserved Font Names?\b[:\s]+(.+)$", line.strip(), re.IGNORECASE)
        if not found:
            continue
        rest = found.group(1).strip().rstrip(".")
        quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]+)[\"“”'‘’]", rest)
        for name in quoted or re.split(r"\s*,\s*|\s+and\s+", rest):
            name = name.strip().strip(".")
            if name and name not in names:
                names.append(name)
    return names


def recorded_unmodified(record) -> bool:
    """Whether a face's evidence records it as an unmodified upstream copy (its `release` says "copied unmodified";
    "a subset of the unmodified upstream" does not). Anything else is treated as a modified version."""
    return "copied unmodified" in " ".join(str((record or {}).get("release") or "").lower().split())


def face_licence_problems(path, licence_text: str, unmodified: bool = False) -> list[str]:
    """Why a face breaks its OFL's terms, [] when it does not: its name table lacks the copyright notice or the
    licence notice, or (unless it is an unmodified upstream copy) one of its names carries a Reserved Font Name
    of its licence. `licence_text`: the OFL text that travels with the face."""
    import re

    from fontTools.ttLib import TTFont, TTLibError
    try:
        font = TTFont(str(path), lazy=True)
        table = font["name"]
        strings = {i: {str(r.toUnicode()) for r in table.names if r.nameID == i} for i in (0, 13, *_FACE_NAME_IDS)}
        font.close()
    except (OSError, TTLibError, KeyError, UnicodeDecodeError, ValueError) as exc:
        return [f"its name table could not be read ({type(exc).__name__})"]
    problems = []
    if not any(s.strip() for s in strings[0]):
        problems.append("its name table carries no copyright notice")
    if not any("open font license" in s.lower() for s in strings[13]):
        problems.append("its name table carries no SIL Open Font License notice")
    if not unmodified:
        for reserved in reserved_font_names(licence_text):
            word = re.compile(r"(?<![A-Za-z])" + re.escape(reserved) + r"(?![a-z])", re.IGNORECASE)
            kept = sorted(s for i in _FACE_NAME_IDS for s in strings[i] if word.search(s))
            if kept:
                problems.append(f"a modified face keeps the Reserved Font Name \"{reserved}\" in its name ({kept[0]}); "
                                "it must be renamed")
    return problems


def font_rights(path):
    path = Path(path)
    try:
        data = json.loads((path.parent / "license_evidence.json").read_text(encoding="utf-8"))
        record = next(r for r in data["fonts"] if r["file"] == path.name)
        license_path = path.parent / record["license_file"]
        valid = bool(record["sha256"] == sha256_file(path) and
                     record.get("license_sha256") and license_path.is_file() and
                     record["license_sha256"] == sha256_file(license_path) and
                     "SIL Open Font License" in record["license_description"] and record["copyright"])
        if valid:   # QF-126: the face itself keeps its notices, and a modified face no reserved name
            problems = face_licence_problems(path, license_path.read_text(encoding="utf-8", errors="replace"),
                                             unmodified=recorded_unmodified(record))
            if problems:
                return False, {**record, "licence_problems": problems}
        return valid, record
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return False, {"file": str(path), "status": "unverified"}
