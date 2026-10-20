import json

from src.credits import add_provenance_attributions, generate_provenance_ledger
from src.fact_evidence import current_fact_review, record_fact_review


def test_same_filename_in_another_directory_cannot_supply_rights(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    topic = assets / "test"
    topic.mkdir(parents=True)
    old = topic / "photo.jpg"
    old.write_bytes(b"old")
    (topic / "credits.json").write_text(json.dumps({"images": [{
        "file": "photo.jpg", "title": "Old", "artist": "Old author", "license": "CC BY-SA 3.0"}]}))
    monkeypatch.setattr("src.config.ASSETS_DIR", assets)
    run = tmp_path / "run"
    run.mkdir()
    photo = run / "photo.jpg"
    photo.write_bytes(b"new")
    credit = {"file": "photo.jpg", "title": "New", "artist": "New author", "license": "CC BY 4.0",
              "source_url": "https://example.org/new", "license_url": "https://creativecommons.org/licenses/by/4.0/",
              "attribution_required": True}
    (run / "credits.json").write_text(json.dumps({"images": [credit]}))
    ledger = generate_provenance_ledger(run, {"id": "test"}, {}, photo, media_items=[photo])
    record = json.loads(ledger.read_text())["assets"][0]
    assert record["artist"] == "New author"
    assert record["rights_status"] == "verified"


def test_required_credit_reaches_both_publication_surfaces(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    sheet = tmp_path / "facts.json"
    sheet.write_text('{}')
    payload = {"guardrail_passed": True, "caption": "Caption", "description": "Description",
               "asset_credits": [{"file": str(photo), "title": "Photo", "artist": "Author",
                   "license": "CC BY 4.0", "source_url": "https://example.org/photo",
                   "license_url": "https://creativecommons.org/licenses/by/4.0/", "attribution_required": True}]}
    record_fact_review(payload, sheet)
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, payload, photo, media_items=[photo])
    add_provenance_attributions(payload, ledger)
    assert "https://example.org/photo" in payload["caption"]
    assert "https://creativecommons.org/licenses/by/4.0/" in payload["description"]
    assert current_fact_review(payload)
