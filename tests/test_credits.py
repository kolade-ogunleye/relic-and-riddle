import json

import pytest
from PIL import Image

from src.credits import caption_credit_line, license_allowed, load_credits, normalize, save_credits
from src.visual_engine import MissingAssetsError, VisualEngine


@pytest.mark.parametrize("name, sa, ok", [
    ("Public domain", False, True),
    ("PD-US", False, True),
    ("CC0", False, True),
    ("CC BY 4.0", False, True),
    ("CC BY-SA 4.0", False, False),
    ("CC BY-SA 4.0", True, True),
    ("CC BY-NC 2.0", True, False),
    ("CC BY-ND 4.0", True, False),
    ("", True, False),
])
def test_license_filter(name, sa, ok):
    assert license_allowed(name, sa) is ok


def test_normalize_handles_accents_and_underscores():
    assert "gobekli tepe" in normalize("File:Göbekli_Tepe_site.jpg")


def test_credit_line_dedupes_and_skips_non_attribution():
    line = caption_credit_line({"images": [
        {"artist": "A", "license": "CC BY 4.0", "attribution_required": True},
        {"artist": "A", "license": "CC BY 4.0", "attribution_required": True},
        {"artist": "NASA", "license": "Public domain", "attribution_required": False},
    ]})
    assert line == "📷 Images via Wikimedia Commons and partner archives: A (CC BY 4.0) · adapted: cropped, with motion and text added"  # CC BY s3(a)(1)(B)
    assert caption_credit_line({"images": []}) == ""


def _topic_dir(tmp_path, n=6, reviewed=False, credited=None, verdict="yes"):
    d = tmp_path / "02_t"
    d.mkdir()
    for i in range(n):
        Image.new("RGB", (40, 40)).save(d / f"{i:02d}_commons.jpg")
    Image.new("RGB", (40, 40)).save(d / "contact_sheet.jpg")
    files = [f"{i:02d}_commons.jpg" for i in range(n if credited is None else credited)]
    seen = {"shows_topic": verdict, "usable": True, "safe": True} if verdict else {}
    save_credits(d, {"reviewed": reviewed, "images": [{"file": f, "title": f, "vision_review": seen} for f in files]})
    return d


def test_unreviewed_fetched_images_are_refused(tmp_path):
    _topic_dir(tmp_path)
    with pytest.raises(MissingAssetsError, match="not reviewed"):
        VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_t"})


def test_reviewed_set_uses_only_credited_files(tmp_path):
    _topic_dir(tmp_path, n=7, reviewed=True, credited=6)
    names = [p.name for p in VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_t"})]
    assert names == [f"{i:02d}_commons.jpg" for i in range(6)]


def test_uncredited_folder_ignores_contact_sheet(tmp_path):
    d = _topic_dir(tmp_path)
    (d / "credits.json").unlink()
    names = [p.name for p in VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_t"})]
    assert "contact_sheet.jpg" not in names and len(names) == 6


def test_load_credits_drops_deleted_files_and_reads_legacy_list(tmp_path):
    d = _topic_dir(tmp_path, n=6, reviewed=True)
    (d / "00_commons.jpg").unlink()
    assert len(load_credits(d)["images"]) == 5
    (d / "credits.json").write_text(json.dumps([{"file": "01_commons.jpg"}]), encoding="utf-8")
    assert load_credits(d) == {"reviewed": False, "images": [{"file": "01_commons.jpg"}]}



def test_a_video_uses_only_strong_photos_and_a_couple_of_context_ones(tmp_path):
    # Operator, 2026-10-25: weak (context, never checked) photos reached the Antikythera video and carousel.
    from src.credits import load_credits
    d = _topic_dir(tmp_path, n=9, reviewed=True)
    cr = load_credits(d)
    for i, im in enumerate(cr["images"]):
        if i >= 5:
            im["vision_review"] = {"shows_topic": "partly", "usable": True, "safe": True} if i < 8 else {}
    cr["images"][0]["relevance"] = 1
    cr["images"][4]["relevance"] = 9
    save_credits(d, cr)
    names = [p.name for p in VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_t"})]
    assert names[0] == "04_commons.jpg"                                  # the most relevant topic photo: the cover
    assert set(names) == {f"{i:02d}_commons.jpg" for i in range(7)} and len(names) == 7   # 5 topic + 2 context


def test_too_few_strong_photos_stop_the_run_with_the_reason(tmp_path):
    _topic_dir(tmp_path, n=6, reviewed=True, verdict="partly")
    with pytest.raises(MissingAssetsError, match="Only 2 of the 6 approved photos"):
        VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_t"})
