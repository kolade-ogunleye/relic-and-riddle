"""
scripts/migrate_legacy_pd_records.py
====================================
Audit and migrate legacy public domain records in assets/*/credits.json to include
explicit worldwide_basis evidence per QF-57 requirements and operator decision decisions/QF-57.md.

Under QF-57, bare 'Public domain' labels without recorded life-term and US-expiry
evidence fail rights_evidence_ok() and block release verification.

This utility inspects existing recorded categories, tags, and source metadata:
- PD-Art + PD-old-auto-expired / PD-US-expired -> commons-pd-art
- PD-old-100-expired / PD-old-70-expired + PD-US-expired -> commons-pd-old
- Author dedications (PD-self, PD-user, PD-author, PD-author-FlickrPDM) -> commons-pd-user
- US Federal works (PD-USGS, PD-CIA, PD-USGov) -> commons-pd-usgov
- NASA direct agency works with no third-party claims -> nasa-own-work
- Archive.org operator-approved films -> archive-film-operator-approved
- Records lacking sufficient evidence are flagged as held for manual review/replacement.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.atomic_files import write_json


def evaluate_record(rec: dict) -> dict | None:
    """The record's worldwide basis: stored, or read from its own evidence (src.image_rights.effective_basis, the
    rule the release check and photo selection apply, so this audit cannot disagree with them)."""
    from src.image_rights import effective_basis
    return effective_basis(rec)


def run_migration(assets_dir: Path, apply: bool = False) -> dict:
    total_images = 0
    total_pd = 0
    already_valid = 0
    migrated = 0
    held_unverified = 0

    topics_summary = {}

    for credits_path in sorted(assets_dir.glob("*/credits.json")):
        topic = credits_path.parent.name
        try:
            data = json.loads(credits_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        images = data.get("images", [])
        topic_changed = False
        topic_pd = 0
        topic_migrated = 0
        topic_held = 0

        for im in images:
            total_images += 1
            lic = str(im.get("license") or "")
            from src.image_rights import public_domain, recorded_basis_ok
            if not public_domain(lic):
                continue
            total_pd += 1
            topic_pd += 1

            if recorded_basis_ok(im):
                already_valid += 1
                continue

            new_basis = evaluate_record(im)
            if new_basis:
                im["worldwide_basis"] = new_basis
                migrated += 1
                topic_migrated += 1
                topic_changed = True
            else:
                held_unverified += 1
                topic_held += 1

        if topic_pd > 0:
            topics_summary[topic] = {
                "pd_images": topic_pd,
                "migrated": topic_migrated,
                "held": topic_held,
            }

        if apply and topic_changed:
            write_json(credits_path, data)  # atomic, LF, the format save_credits writes

    # Also migrate footage files (e.g. PompeiiAndVesuvius.json)
    footage_migrated = 0
    for footage_path in sorted(assets_dir.glob("*/footage/*.json")):
        try:
            f_data = json.loads(footage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        from src.image_rights import basis_from_record, public_domain, recorded_basis_ok
        derived = None if recorded_basis_ok(f_data) else basis_from_record(f_data)
        if public_domain(str(f_data.get("license") or "")) and derived:
            f_data["worldwide_basis"] = derived
            footage_migrated += 1
            if apply:
                write_json(footage_path, f_data)

    return {
        "total_images": total_images,
        "total_pd": total_pd,
        "already_valid": already_valid,
        "migrated": migrated,
        "held_unverified": held_unverified,
        "footage_migrated": footage_migrated,
        "topics": topics_summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit and migrate legacy PD records in credits.json")
    parser.add_argument("--assets", type=Path, default=Path("assets"), help="Path to assets/ directory")
    parser.add_argument("--apply", action="store_true", help="Apply migration in-place to credits.json files")
    args = parser.parse_args()

    results = run_migration(args.assets, apply=args.apply)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
