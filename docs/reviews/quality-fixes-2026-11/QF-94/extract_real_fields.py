"""Collects every text field real runs drew (or would draw) on a card, for measure.py (QF-94).

    python extract_real_fields.py <out json> <dev checkout holding output/run_*>

Read-only over the checkout's output/run_*/verified_script.json, verified_deep_dive.json, provenance.json (the photo
credits: title, artist, licence), carousel_photos.json and stories/layout.json, and over the repository's
data/fact_sheets (the signed card fields: statements, myths, quotes). output/ is not committed, so the dev strings
are copied into real_fields.json beside this file: source ("dev" or "fact_sheet"), run folder name, field path and
text, nothing else of a run.

The production sample is never committed (its text, run names and author names stay out of the repository):
measure.py reads a temporary copy of it through collect() at run time and reports counts only.
"""
import json
import sys
from pathlib import Path

DRAWN = ("title", "youtube_title", "hook_sentence", "closing_sentence", "share_line", "thumbnail", "story_slides",
         "carousel_slides", "timeline", "stat_cards", "scale_fact", "cross_section", "route", "photo_labels",
         "location", "poll", "emphasis_words", "acts", "chapters", "myth", "quote")
SKIP = ("url", "id", "path", "sha256", "latitude", "longitude", "lat", "lon", "zoom", "source", "kind", "type")
CARD_KINDS = ("statements", "myths", "quotes")


def _leaves(value, path):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            if not any(str(k).lower() == s or str(k).lower().endswith("_" + s) for s in SKIP):
                yield from _leaves(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, f"{path}[{i}]")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect(root: Path, source: str) -> list[dict]:
    """Every drawn text field of the runs under root/output, as {source, run, field, text} rows (read-only)."""
    rows = []

    def add(run, field, text):
        text = " ".join(str(text or "").split())
        if text and not text.startswith(("http://", "https://")):
            rows.append({"source": source, "run": run, "field": field, "text": text})

    for folder in sorted((Path(root) / "output").glob("run_*")):
        run = folder.name
        for name in ("verified_script.json", "verified_deep_dive.json"):
            p = _read(folder / name)
            if isinstance(p, dict):
                for key in DRAWN:
                    for field, text in _leaves(p.get(key), key):
                        add(run, field, text)
        prov = _read(folder / "provenance.json")
        for a in (prov or {}).get("assets") or []:
            for key in ("title", "artist", "license"):
                if isinstance(a, dict):
                    add(run, f"credit.{key}", a.get(key))
        for field, text in _leaves(_read(folder / "carousel_photos.json"), "carousel_photos"):
            if any(k in field.lower() for k in ("credit", "artist", "author", "title", "line")):
                add(run, field, text)
        for field, text in _leaves(_read(folder / "stories" / "layout.json"), "stories"):
            if "credit" in field.lower() or field.endswith(("lines", "drawn")) or "drawn[" in field:
                add(run, field, text)
    return rows


def fact_sheet_fields(repo: Path) -> list[dict]:
    rows = []
    for sheet in sorted((Path(repo) / "data" / "fact_sheets").glob("*.json")):
        d = _read(sheet) or {}
        cards = d.get("cards") if isinstance(d.get("cards"), dict) else d     # the signed fields live under "cards"
        for kind in CARD_KINDS:
            for field, text in _leaves(cards.get(kind), f"{sheet.stem}.{kind}"):
                text = " ".join(str(text or "").split())
                if ".check" not in field and text:
                    rows.append({"source": "fact_sheet", "run": sheet.stem, "field": field, "text": text})
    return rows


def distinct(rows: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for r in rows:
        if r["text"] not in seen:
            seen.add(r["text"])
            unique.append(r)
    return unique


def main(out: Path, dev: Path) -> None:
    repo = Path(__file__).resolve().parents[4]
    rows = collect(dev, "dev") + fact_sheet_fields(repo)
    unique = distinct(rows)
    out.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(len(rows), "fields,", len(unique), "distinct strings")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
