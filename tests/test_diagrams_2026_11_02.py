"""The rebuilt 9:16 cross-section (design review, 2026-11-02; the rolling-stone door and ventilation diagrams it
was reviewed with were retired and deleted on 2026-11-03). On the vertical render's last frame every recorded
word measures at least 4.5:1 and sits inside the Reel text box, and the level labels run down the frame in order
of depth."""
import json

import pytest

from src import brand, graphics

W, H = 1080, 1920
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables, wine presses and storage"},
                      {"depth_ft": 35.0, "label": "Living quarters and kitchens"},
                      {"depth_ft": 140.0, "label": "Cruciform church and school"},
                      {"depth_ft": 280.0, "label": "Deepest wells and shaft base"}]}
RENDER = {
    "section": lambda out: graphics.render_cross_section(SECTION, out, width=W, height=H),
}


@pytest.fixture(scope="module")
def records(tmp_path_factory):
    work = tmp_path_factory.mktemp("diagrams")
    out = {}
    for kind, render in RENDER.items():
        clip = render(work / f"{kind}.mp4")
        assert clip is not None and clip.exists() and clip.with_suffix(".jpg").exists()
        out[kind] = json.loads((work / f"{kind}.mp4{graphics.CONTRAST_SIDECAR}").read_text(encoding="utf-8"))
    return out


@pytest.mark.parametrize("kind", sorted(RENDER))
def test_every_element_measures_at_least_4_5_to_1(records, kind):
    elements = records[kind]["elements"]
    assert elements
    low = [(e["name"], e["drawn"]) for e in elements if e["drawn"] is None or e["drawn"] < brand.TEXT_CONTRAST]
    assert not low, low


@pytest.mark.parametrize("kind", sorted(RENDER))
def test_every_element_box_is_inside_the_text_box(records, kind):
    x0, y0, x1, y1 = graphics.get_text_box(W, H)
    outside = [(e["name"], e["box"]) for e in records[kind]["elements"]
               if not (e["box"][0] >= x0 and e["box"][1] >= y0 and e["box"][2] <= x1 and e["box"][3] <= y1)]
    assert not outside, outside


def test_cross_section_level_labels_descend_with_depth(records):
    rows = {e["name"]: e for e in records["section"]["elements"]}
    ys = [rows[f"level{k}_label0"]["box"][1] for k in range(len(SECTION["levels"]))]
    assert ys == sorted(ys) and len(set(ys)) == len(ys), ys
    depth_ys = [rows[f"level{k}_depth"]["box"][1] for k in range(len(SECTION["levels"]))]
    assert depth_ys == sorted(depth_ys), depth_ys
    # fanned out: no two level blocks overlap
    blocks = []
    for k in range(len(SECTION["levels"])):
        mine = [e["box"] for name, e in rows.items() if name.startswith(f"level{k}_")]
        blocks.append((min(b[1] for b in mine), max(b[3] for b in mine)))
    assert all(blocks[k][1] < blocks[k + 1][0] for k in range(len(blocks) - 1)), blocks


def test_cross_section_ends_on_the_deepest_level_in_gold(records):
    rows = {e["name"]: e for e in records["section"]["elements"]}
    deepest = rows[f"level{len(SECTION['levels']) - 1}_depth"]
    assert deepest["text"] == "280 FT"
    shallower = [rows[f"level{k}_depth"] for k in range(len(SECTION["levels"]) - 1)]
    assert all((b := s["box"])[3] - b[1] < deepest["box"][3] - deepest["box"][1] for s in shallower)


def test_landscape_diagrams_still_render(tmp_path):
    for kind, spec, render in (("section", SECTION, graphics.render_cross_section),):
        clip = render(spec, tmp_path / f"{kind}_ls.mp4", width=1920, height=1080)
        assert clip is not None and clip.exists()
