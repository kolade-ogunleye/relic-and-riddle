"""Retain bundled-font licensing notices and identity evidence from primary repositories."""
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import FONTS_DIR
from src.credits import sha256_file
from src.font_evidence import embedded_names


def main():
    records = []
    for filename, directory in (("Anton-Regular.ttf", "anton"),
                                ("Montserrat-Variable.ttf", "montserrat"),
                                ("BebasNeue-Regular.ttf", "bebasneue"),
                                ("Cinzel-Variable.ttf", "cinzel")):   # the display serif for titles (art direction, 2026-10-24)
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
                        "source_url": url, "checked_on": "2026-10-24"})
    (FONTS_DIR / "license_evidence.json").write_text(json.dumps({"fonts": records}, indent=2), encoding="utf-8")
    print(f"Recorded licensing evidence for {len(records)} bundled fonts")


if __name__ == "__main__":
    main()
