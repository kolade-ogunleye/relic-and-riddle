"""Library of Congress results: only worldwide 'no known restrictions' clearances are taken (2026-11-02)."""

from src import museum_sources as ms


def _result(rights: str) -> dict:
    return {"title": "Great Pyramids, Egypt", "url": "https://www.loc.gov/item/2016887869/", "date": "1936-01-01",
            "image_url": ["https://tile.loc.gov/storage-services/service/pnp/hec/33600/33659v.jpg#h=793&w=1024"],
            "item": {"rights": rights}}


def test_only_unconditional_no_known_restrictions_is_taken(monkeypatch) -> None:
    results = [_result("No known restrictions on use or reproduction."),
               _result("No known restrictions on publication in the U.S. Use elsewhere may be restricted by "
                       "other countries' laws."),
               _result("Rights status not evaluated.")]
    monkeypatch.setattr(ms, "_get", lambda *a, **k: {"results": results})
    found = ms.loc_search("pyramid", 5)
    assert len(found) == 1 and "Rights: No known restrictions on use" in found[0]["description"]
