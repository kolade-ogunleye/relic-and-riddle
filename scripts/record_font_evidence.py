"""Retain bundled-font licensing notices and identity evidence from primary repositories."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import FONTS_DIR
from src.credits import sha256_file
from src.font_evidence import embedded_names, font_rights
from src.typography import bundled_font_files

GOOGLE_FONT_DIRECTORIES = {"Anton-Regular.ttf": "anton", "Montserrat-Variable.ttf": "montserrat",
                           "Cinzel-Variable.ttf": "cinzel"}


def main():
    # QF-87: one record per bundled file (typography.bundled_font_files), nothing for a deleted one. The faces not
    # from google/fonts (IBM Plex Mono from its release archive, the QF-94 Noto subsets) keep their recorded,
    # hash-bound evidence, checked before any request; it is never replaced by another project's notice.
    retained = {}
    filenames = bundled_font_files()
    for filename in filenames:
        if filename not in GOOGLE_FONT_DIRECTORIES:
            valid, record = font_rights(FONTS_DIR / filename)
            if not valid or not record.get("source_url"):
                raise ValueError(f"Missing intact retained source evidence: {filename}")
            retained[filename] = record
    records = []
    for filename in filenames:
        if filename in retained:
            records.append(retained[filename])
            continue
        directory = GOOGLE_FONT_DIRECTORIES[filename]
        path = FONTS_DIR / filename
        names = embedded_names(path)
        url = f"https://raw.githubusercontent.com/google/fonts/main/ofl/{directory}/OFL.txt"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if "SIL OPEN FONT LICENSE" not in response.text:
            raise ValueError("Unexpected font license document")
        notice = FONTS_DIR / "licenses" / f"{filename}.OFL.txt"
        notice.parent.mkdir(parents=True, exist_ok=True)
        # LF throughout: the upstream notices carry CRLF, the repository normalises text to LF, and the recorded
        # hash must match the file as checked out (2026-10-24).
        notice.write_text(response.text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        records.append({"file": filename, "sha256": sha256_file(path), **names,
                        "license_file": notice.relative_to(FONTS_DIR).as_posix(), "license_sha256": sha256_file(notice),
                        "source_url": url, "checked_on": datetime.now(UTC).date().isoformat()})
    (FONTS_DIR / "license_evidence.json").write_text(json.dumps({"fonts": records}, indent=2) + "\n", encoding="utf-8",
                                                     newline="\n")
    print(f"Recorded licensing evidence for {len(records)} bundled fonts")


if __name__ == "__main__":
    main()
