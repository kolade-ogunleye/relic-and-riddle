import json

from src.config import FONTS_DIR
from src.font_evidence import embedded_names, font_rights


def test_font_notice_and_binary_identity_are_both_required(tmp_path):
    original = FONTS_DIR / "Anton-Regular.ttf"
    record = next(r for r in json.loads((FONTS_DIR / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]
                  if r["file"] == original.name)
    local = tmp_path / original.name
    local.write_bytes(original.read_bytes())
    notice = tmp_path / "OFL.txt"
    notice.write_bytes((FONTS_DIR / record["license_file"]).read_bytes())
    (tmp_path / "license_evidence.json").write_text(json.dumps({"fonts": [{**record, "license_file": "OFL.txt"}]}))
    assert embedded_names(local)["copyright"] == record["copyright"]
    assert font_rights(local)[0]
    local.write_bytes(local.read_bytes() + b"changed")
    assert not font_rights(local)[0]
    local.write_bytes(original.read_bytes())
    notice.unlink()
    assert not font_rights(local)[0]
