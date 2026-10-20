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
        return valid, record
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return False, {"file": str(path), "status": "unverified"}
