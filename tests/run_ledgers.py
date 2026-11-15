"""The provenance ledger of a fixture run (QF-126, round 3).

A rendered run has a provenance.json (credits.generate_provenance_ledger); the release gate lets no Reel out without
one. Fixture runs that are put together by hand had none, and a carousel is now held when its run's ledger is missing
and something must be read from it: a slide without a record of the photo it shows, or a photo whose credits.json
entry cannot be read. `write_ledger` gives such a fixture run the ledger a real run has. It changes no expectation:
the photos it records are public domain, which asks for no credit.
"""
import hashlib
import json
from pathlib import Path


def write_ledger(run_dir, photos=()):
    """Writes `run_dir`/provenance.json recording `photos` (paths) as public-domain assets, and returns its path."""
    run_dir = Path(run_dir)
    assets = []
    for photo in photos:
        photo = Path(photo)
        assets.append({"path": str(photo.resolve()), "file": photo.name,
                       "sha256": hashlib.sha256(photo.read_bytes()).hexdigest() if photo.is_file() else "",
                       "origin": "source_media", "title": photo.stem, "artist": None, "license": "Public domain",
                       "license_url": None, "source_url": "https://example.org/" + photo.name,
                       "attribution_required": False, "rights_status": "verified"})
    ledger = run_dir / "provenance.json"
    ledger.write_text(json.dumps({"schema_version": "2.0.0", "run_id": run_dir.name, "status": "recorded", "issues": [],
                                  "assets_count": len(assets), "assets": assets}, indent=1),
                      encoding="utf-8", newline="\n")
    return ledger
