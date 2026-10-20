import json

import pytest

from src.credits import generate_provenance_ledger
from src.lineage import record_derived, source_chain
from src.video_qa import verify_provenance_integrity


def test_nested_lineage_detects_source_changes(tmp_path):
    original, enlarged, animated = [tmp_path / n for n in ("original.jpg", "large.jpg", "clip.mp4")]
    for path in (original, enlarged, animated):
        path.write_bytes(path.name.encode())
    record_derived(enlarged, [original], "upscale")
    record_derived(animated, [enlarged], "parallax")
    assert len(source_chain(animated)) == 4
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, animated, media_items=[animated])
    assert verify_provenance_integrity(ledger)
    original.write_bytes(b"changed")
    assert not verify_provenance_integrity(ledger)
    with pytest.raises(ValueError, match="Changed or missing source"):
        source_chain(animated)


def test_derived_asset_inherits_source_credit_without_inventing_rights(tmp_path):
    original, derived = tmp_path / "original.jpg", tmp_path / "derived.jpg"
    original.write_bytes(b"original")
    derived.write_bytes(b"derived")
    record_derived(derived, [original], "upscale")
    payload = {"asset_credits": [{"file": str(original), "license": "CC BY-NC 4.0",
                                 "source_url": "https://example.org/source",
                                 "license_url": "https://example.org/license"}]}
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, payload, derived, media_items=[derived])
    record = json.loads(ledger.read_text())["assets"][0]
    assert record["license"] == "CC BY-NC 4.0"
    assert record["rights_status"] == "unverified"


def test_original_drawing_and_photo_composite_have_distinct_rights(tmp_path):
    from src.lineage import record_graphic
    code, graphic, photo = [tmp_path / name for name in ("draw.py", "graphic.mp4", "photo.jpg")]
    for path in (code, graphic, photo):
        path.write_bytes(path.name.encode())
    record_graphic(graphic, code, {"builder": "timeline"})
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, graphic, media_items=[graphic])
    assert json.loads(ledger.read_text())["assets"][0]["rights_status"] == "verified"
    record_graphic(graphic, code, {"builder": "numbers"}, [photo])
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, graphic, media_items=[graphic])
    assert json.loads(ledger.read_text())["assets"][0]["rights_status"] == "unverified"
