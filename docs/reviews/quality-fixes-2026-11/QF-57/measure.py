"""QF-57 measurement. Prints JSON and writes nothing. No network, no model.

    python docs/reviews/quality-fixes-2026-11/QF-57/measure.py <repo root> [<assets dir>]

<repo root> is the code measured (the parent or this row); the fixtures come from fixtures.py beside this file, so
the same script measures both. <assets dir> defaults to <repo root>/assets (the tracked dev pools); pass a COPY of
another environment's assets (e.g. the production sample, kept outside the repository) to measure it: held records
are then reported as counts only (per-topic file lists only for <repo root>/assets). A field the measured code cannot
produce (its parent has no such rule) is "n/a".
"""

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent


def main(root: Path, assets: Path) -> dict:
    own_assets = assets == (root / "assets").resolve()
    sys.path.insert(0, str(root))
    from scripts import fetch_commons_images as fetch
    from src import commons, config, image_review
    from src import museum_sources as museums
    from src.credits import rights_evidence_ok

    fixtures = runpy.run_path(str(HERE / "fixtures.py"))
    page = fixtures["page"]
    pages = {"bare": page(), "us_federal": page(["PD-USGov"]), "us_only": page(["PD-US-expired"]),
             "life_term_only": page(["PD-old-70"]), "life_and_us": page(["PD-old-70", "PD-US-expired"]),
             "auto_expired": page(["PD-old-auto-expired", "PD-US-expired"]), "cc0": page(licence="CC0")}
    admitted = {name: bool(list(commons.candidates([p], []))) for name, p in pages.items()}
    result = {"fixture_admitted": admitted,
              "bare_pd_release_evidence_accepted": rights_evidence_ok(
                  {"license": "Public domain", "source_url": "https://commons.wikimedia.org/wiki/File:Artifact.jpg"})}

    response = Mock(status_code=200)
    response.json.return_value = {"results": [{**fixtures["flickr_item"](), "license": "cc0"}]}
    with patch.object(config, "MUSEUM_OPENVERSE_ENABLED", False), \
            patch.object(fetch.requests, "get", return_value=response) as request:
        result["openverse_results_when_disabled"] = len(fetch.search_openverse("artifact"))
        result["openverse_calls_when_disabled"] = request.call_count
    if hasattr(museums, "openverse_search"):
        with patch.object(config, "MUSEUM_OPENVERSE_ENABLED", True), \
                patch.object(config, "FLICKR_API_KEY", "fixture-key", create=True):
            with patch.object(museums, "_get", fixtures["flickr_get"](license_id="0")):
                result["revoked_flickr_results"] = len(museums.openverse_search("artifact"))
            with patch.object(museums, "_get", fixtures["flickr_get"]()):
                verified = museums.openverse_search("artifact")
        result["valid_flickr_results"] = len(verified)
        result["flickr_with_verification_timestamp"] = sum(bool(r.get("license_verified_at")) for r in verified)
    else:
        result.update(revoked_flickr_results="n/a", valid_flickr_results="n/a",
                      flickr_with_verification_timestamp="n/a")

    # Real pools, by the rule the release check and photo selection apply.
    try:
        from src.image_rights import public_domain
    except ImportError:
        import re

        def public_domain(licence):
            return bool(re.match(r"^(?:public domain|pd(?:\b|-))", licence.strip(), re.IGNORECASE))
    pools, pd, pd_ok, ready, held_by_topic = 0, 0, 0, 0, {}
    for credits_path in sorted(assets.glob("*/credits.json")):
        data = json.loads(credits_path.read_text(encoding="utf-8"))
        images = data.get("images", [])
        pools += 1
        ready += image_review.video_ready(images, bool(data.get("reviewed")))
        for record in images:
            if public_domain(str(record.get("license") or "")):
                pd += 1
                if rights_evidence_ok(record):
                    pd_ok += 1
                else:
                    held_by_topic.setdefault(credits_path.parent.name, []).append(record.get("file"))
    result["real_pools"] = {"pools": pools, "ready_pools": ready, "pd_records": pd,
                            "pd_records_passing_release_check": pd_ok, "pd_records_held": pd - pd_ok}
    if own_assets:  # the repository's own pools: name the held files
        result["real_pools"]["held"] = held_by_topic
    else:  # another environment's pools: counts only
        result["real_pools"]["pools_with_held"] = len(held_by_topic)
    return result


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    assets = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root / "assets"
    print(json.dumps(main(root, assets), indent=1, ensure_ascii=False, sort_keys=True))
