"""Design-freeze board tooling, cycle 4 (docs/reviews/freeze-system-2026-11-15, QF-35a render_surfaces.py): the
evidence fixes are invariants of the board's own records, never pinned pictures.

- --workers: every record key render_surfaces.py can write merges (the omission ledger crashed it), a surface in
  two shards still refuses;
- second-colours: today's faces with the test brand's colour set, every colour role defined; the palette record
  names what it moves and what it leaves;
- omissions: each ledger row is attributed to the surface it was drawn into, shows on the tile's label and in the
  refusal table, a job-level row no surface took is named by its job;
- row 13: each social frame's masthead record by kind, a Facebook interior's undrawn masthead with its reason;
- motion: the then-and-now marks come from the renderer's schedule and divider, the callout's from its per-frame
  record (picklable, its masks left out);
- stand-ins: a stress ground's label reads the dev checkout and says when a real run makes it unneeded;
- the expected cases: the palette's refusal table renamed, cycle 4's files expected under the names the board writes.
"""
from __future__ import annotations

import ast
import importlib
import json
import pickle
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"
QF35A = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"


@pytest.fixture(scope="module")
def mods():
    for p in (str(BOARD), str(QF35A)):
        if p not in sys.path:
            sys.path.insert(0, p)
    names = ("board_evidence", "board_motion", "board_table", "board", "board_extras", "board_portability",
             "render_surfaces", "exp_cases")
    return {n: importlib.import_module(n) for n in names}


def _result_keys() -> set[str]:
    """Every top-level key render_surfaces.py writes into its result (result["k"] = / result.setdefault("k", ...) /
    the dict literal), read from its source."""
    tree = ast.parse((QF35A / "render_surfaces.py").read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "result"
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                and isinstance(node.ctx, ast.Store)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "result"
                or isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "result") and node.args and isinstance(node.args[0], ast.Constant):
            keys.add(node.args[0].value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "result" and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return keys


def test_every_render_surfaces_record_key_merges_across_shards(mods):
    b = mods["board"]
    keys = _result_keys()
    assert {"omissions", "surface_omissions", "masthead_records", "surfaces", "count"} <= keys
    groups = (b.SHARD_ONLY_KEYS, b.SHARD_UNION_KEYS, b.SHARD_SAME_KEYS, b.SHARD_SKIP_KEYS, b.SHARD_COPY_SET_KEYS)
    known = set().union(*map(set, groups))
    assert keys <= known, sorted(keys - known)
    assert sum(len(set(g)) for g in groups) == len(known)   # each key merges one way


def test_shards_merge_the_omission_ledger_and_refuse_a_duplicate(mods):
    b = mods["board"]
    row = {"surface": "map m.mp4", "omitted": "X", "rule": "r", "reason": "drawn without 'X'", "drawn": True}
    one = {"surfaces": {"a": {}}, "seconds": {"j1": 1}, "omissions": {"j1": [row]},
           "surface_omissions": {"a": [row]}, "masthead_records": {"a": {"shown": True}}, "brand": "default"}
    two = {"surfaces": {"b": {}}, "seconds": {"j2": 1}, "omissions": {}, "masthead_records": {"b": {"shown": False}},
           "brand": "default", "count": 1}
    merged = b.merge_shards([one, two])
    assert merged["omissions"] == {"j1": [row]} and merged["surface_omissions"] == {"a": [row]}
    assert set(merged["masthead_records"]) == {"a", "b"} and merged["count"] == 2
    with pytest.raises(RuntimeError):
        b.merge_shards([one, {"surfaces": {}, "omissions": {"j1": [row]}}])
    with pytest.raises(RuntimeError):
        b.merge_shards([one, {"surfaces": {}, "brand": "second"}])


def test_copy_set_records_merge_their_swapped_groups(mods):
    b = mods["board"]
    rec = {"file": "c.json", "sha256": "h", "unswapped": "u"}
    merged = b.merge_shards([{"copy_set": {**rec, "swapped": ["covers"]}}, {"copy_set": {**rec, "swapped": ["social"]}}])
    assert merged["copy_set"] == {**rec, "swapped": ["covers", "social"]}
    with pytest.raises(RuntimeError):
        b.merge_shards([{"copy_set": {**rec, "swapped": []}}, {"copy_set": {**rec, "sha256": "other", "swapped": []}}])


def test_second_colours_keeps_todays_faces_and_moves_the_colour_set(mods):
    b = mods["board"]
    from src import brand
    base = brand.DEFAULT_BRAND
    second = b.evidence_brand(ROOT, "second")
    colours = b.evidence_brand(ROOT, "second-colours")
    assert b.evidence_brand(ROOT, "default") is None
    assert dict(colours.fonts) == dict(base.fonts)                       # colour-only: every face today's
    assert dict(second.fonts)["display"] != base.fonts["display"]         # the evidence brand keeps its display face
    assert dict(colours.palette) == dict(second.palette)                  # one colour set: second's
    assert set(colours.palette) == set(base.palette)                      # every colour role defined
    # the "second" brand is exactly render_surfaces.py's --brand second (the boards before cycle 4)
    assert second == mods["render_surfaces"].second_brand(brand)
    rec = b.palette_record("second-colours", ROOT)
    assert rec["fonts_changed"] == {} and rec["moved"] + len(rec["unmoved"]) == rec["colour_roles"]
    assert rec["moved"] >= rec["colour_roles"] - 2   # red/blue swapped: only a neutral grey can stay
    assert b.palette_record("default", ROOT)["moved"] == 0


def test_palettes_reach_the_cli_the_portability_board_and_render_surfaces(mods):
    b, port = mods["board"], mods["board_portability"]
    assert set(b.PALETTES) == {"default", "second", "second-colours"}
    variants = {name: extra for name, _what, extra in port.VARIANTS}
    assert variants["second-colours"] == ["--brand", "second-colours"]
    src = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    assert '"second-colours"' in src
    sh = (BOARD / "run_palette.sh").read_text(encoding="utf-8")
    assert "second-colours" in sh and "BOARD_PY" in sh and "\r" not in sh


def test_omissions_are_attributed_to_the_surface_they_were_drawn_into(mods):
    from src.refusal import collecting, omit
    rs = mods["render_surfaces"]
    result: dict = {}
    surfaces = rs.AttributedSurfaces(result)
    with collecting():
        surfaces.new_job()
        omit("map a.mp4", "the neighbour's name X", "rule A", "no room")
        surfaces["map__a__916"] = {"sha256": "1"}
        surfaces["map__b__916"] = {"sha256": "2"}             # drew nothing out
        omit("map c.mp4", "event 1523", "rule B", "fit")
        surfaces["map__c__916"] = {"refused": "x"}            # a refused surface keeps what it was tried without
    with collecting():
        surfaces.new_job()
        surfaces["cover__d__reel"] = {"sha256": "3"}
    got = result["surface_omissions"]
    assert set(got) == {"map__a__916", "map__c__916"}
    assert got["map__a__916"][0]["omitted"] == "the neighbour's name X" and got["map__c__916"][0]["rule"] == "rule B"
    assert json.loads(json.dumps({"surfaces": surfaces}))["surfaces"]["map__b__916"] == {"sha256": "2"}


def test_a_recorded_omission_is_on_the_tile_and_in_the_table(mods):
    be = mods["board_evidence"]
    row = {"surface": "map m.mp4", "omitted": "the neighbour's name FINLAND", "rule": "names its neighbours",
           "reason": "drawn without 'FINLAND': no room (rule: names its neighbours)", "drawn": True}
    lone = dict(row, omitted="ESTONIA", reason="drawn without 'ESTONIA'")
    sj = {"surface_omissions": {"map__amber__916": [row]}, "omissions": {"map run_amber 916": [row],
                                                                         "route run_amber 169": [lone]}}
    note = be.omission_note("map__amber__916", sj)
    assert note.startswith("RECORDED OMISSION") and "FINLAND" in note and "names its neighbours" in note
    assert be.omission_note("map__other__916", sj) == "" and be.omission_note("x", None) == ""
    rows = be.omission_table_rows(sj)
    assert ("map__amber__916", be.OMISSION_WHAT, row["reason"]) in rows
    assert ("route run_amber 169 (job)", be.OMISSION_WHAT, lone["reason"]) in rows
    assert not any(r[0] == "map run_amber 916 (job)" for r in rows)   # placed on its surface, not twice
    assert be.label("map__amber__916", note).endswith(note)


def test_social_masthead_rows_name_the_facebook_interior_rule(mods):
    bt = mods["board_table"]
    assert bt.social_part("social__richat_1__fb_carousel_slide_03") == ("richat_1", "fb", 3)
    assert bt.social_part("social__richat_1__story_story_02_reveal") == ("richat_1", "story", 2)
    assert bt.masthead_class("social__r_1__carousel_carousel_slide_01") == "Instagram carousel cover (frame 1)"
    why = "Facebook interior slide: the collage carries the brand on its cover tile only"
    recs = {"social__r_1__fb_carousel_slide_01": {"shown": True, "size": 40.0, "variant": "bone", "contrast": 6.1,
                                                  "reach_matched": 412},
            "social__r_1__fb_carousel_slide_02": {"shown": False, "reason": why, "fb_interior": True},
            "social__r_1__carousel_carousel_slide_01": {"shown": True, "size": 40.0, "variant": "bone", "contrast": 7.0}}
    rows = bt.social_masthead_rows({"masthead_records": recs})
    by = {r[0]: r for r in rows[1:]}
    assert by["Facebook multi-photo post interior (frame 2+)"][4] == "0 / 1"
    assert by["Facebook multi-photo post interior (frame 2+)"][5] == why
    assert by["Facebook multi-photo post cover (frame 1)"][6] == "412"
    assert "no frame masthead records" in bt.social_masthead_rows({})[1][0]


def test_thennow_marks_come_from_the_schedule_and_the_divider(mods):
    bm = mods["board_motion"]
    sched = {"then": [0.0, 0.0, 0.5, 1.0, 1.0, 0.4, 0.0], "now": [0.0] * 4 + [0.6, 1.0, 1.0]}
    div = [1920.0, 1920.0, 1500.0, 900.0, 1000.0, 960.0, 960.0]
    marks = bm.thennow_marks({"schedule": sched, "divider": div})
    assert marks["then marker first on"] == 2 and marks["then marker fully in"] == 3
    assert marks["then marker first fade"] == 5 and marks["now marker fully in"] == 5
    assert marks["divider first move"] == 2 and marks["divider far point"] == 3 and marks["divider settled"] == 5


def test_callout_marks_come_from_the_renderers_record(mods):
    bm = mods["board_motion"]
    lab = [{}, {"lead": 0.2}, {"plate": 1.0, "text_a": 0.5}, {"plate": 1.0, "text_a": 1.0},
           {"plate": 1.0, "text_a": 1.0}, {"plate": 1.0, "text_a": 0.3}]
    frames = [{"i": i, "t": i / 30, "labels": ({0: d} if d else {})} for i, d in enumerate(lab)]
    marks = bm.callout_marks({"frames": frames, "texts": {"0": "stone door"}})
    assert marks == {"label 1 (stone door) marker lands": 1, "label 1 (stone door) plate fully open": 2,
                     "label 1 (stone door) text fully in": 3, "label 1 (stone door) exit starts": 5,
                     "label 1 (stone door) last drawn": 5}


def test_callout_record_is_picklable_and_drops_the_masks(mods):
    bx = mods["board_extras"]
    rec = {"labels": [{"text": object(), "box": (0, 0, 1, 1)}], "exit_frame": 168,
           "frames": [{"i": 0, "t": 0.0, "labels": {0: {"plate_a": 1, "text_a": 0.5, "plate": 1, "lead": 1, "g": 1,
                                                       "text_at": object(), "shift": (1, 2)}}}]}
    out = bx.callout_record(rec, [("stone door recess", 600, 900)])
    assert pickle.loads(pickle.dumps(out))["frames"][0]["labels"][0] == {"plate_a": 1.0, "text_a": 0.5, "plate": 1.0,
                                                                         "lead": 1.0, "g": 1.0}
    assert out["texts"] == {"0": "stone door recess"} and out["reveal"] == bx.CALLOUT_REVEAL
    assert "labels" not in out or not isinstance(out.get("labels"), list)


def test_a_stress_ground_label_reads_the_dev_checkout(mods, tmp_path, monkeypatch):
    be = mods["board_evidence"]
    (tmp_path / "output").mkdir()
    monkeypatch.setenv("RR_FIXTURE_REPO", str(tmp_path))
    cls, why = be.pairing("cover__antikythera_mechanism__near_white__youtube")
    assert cls == be.STAND_IN and why.startswith("stress case (near-white ground)") and "no run of" in why
    lab = be.label("social__antikythera_mechanism_near_white__carousel_slide_01", "cover | near-white stress ground")
    assert lab.count("STAND-IN") == 1
    (tmp_path / "output" / "run_antikythera_mechanism_1799999999").mkdir()
    cls, why = be.pairing("cover__antikythera_mechanism__near_white__reel")
    assert cls == be.STAND_IN and why.startswith("STAND-IN NO LONGER NEEDED: 1 run(s)")


def test_expected_cases_follow_the_names_the_board_writes(mods, tmp_path):
    ex = mods["exp_cases"]
    base = [{"surface": "refusals_default", "case": "refusals_default", "format": "default"},
            {"surface": "surfaces", "case": "cover__amber_room_heist", "format": "reel"},
            {"surface": "surfaces", "case": "cover__amber_room_heist__grid", "format": "default"},
            {"surface": "surfaces", "case": "map__x", "format": "916"}]
    refusals = tmp_path / "refusals.json"
    refusals.write_text(json.dumps([{"surface": "cover__amber_room_heist__reel", "what": "refused (recorded)",
                                     "reason": "fit"},
                                    {"surface": "cover__amber_room_heist__grid", "what": "refused (recorded)",
                                     "reason": "fit"},
                                    {"surface": "map__x__916", "what": OMIT, "reason": "drawn without"}]))
    out, dropped = ex.build(base, "second-colours", ROOT, refusals)
    keys = {(r["surface"], r["case"], r["format"]) for r in out}
    assert ("refusals_second-colours", "refusals_second-colours", "default") in keys
    assert ("surfaces", "map__x", "916") in keys                          # an omission is drawn, still expected
    assert sorted(dropped) == ["surfaces/cover__amber_room_heist__grid", "surfaces/cover__amber_room_heist__reel"]
    topics = ex.thennow_topics(ROOT)
    assert topics
    for t in topics:
        for tag in ex.THENNOW_TAGS:
            assert ("clips", f"thennow__{t}", tag) in keys and ("row12_motion_thennow", t, tag) in keys
            assert ("row12_motion_thennow", f"{t}__{tag}_timing", "default") in keys
    assert ("clips", "callouts__reference_pair", "916") in keys
    assert ("row13_fb_masthead", "row13_fb_masthead", "default") in keys
    plain, _ = ex.build(base, "default", ROOT, None, cycle4=False)
    assert plain == base


OMIT = "drawn without a part (recorded omission)"


def test_board_writes_the_motion_names_exp_cases_expects(mods):
    """The sheet and clip names board.py writes for the cycle 4 motion cases are the ones exp_cases.py expects."""
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert 'f"row12_motion_{name}"' in src and '"row12_motion_callouts__reference_pair__916"' in src
    assert '"row13_fb_masthead"' in src
    bm_src = (BOARD / "board_motion.py").read_text(encoding="utf-8")
    assert 'f"thennow__{topic}__{tag}.mp4"' in bm_src
    assert '"callouts__reference_pair__916.mp4"' in (BOARD / "board_extras.py").read_text(encoding="utf-8")


def test_fb_collage_of_fewer_than_five_follows_the_trees_model(mods):
    """Facebook's post of 5 is drawn on the capture's measured boxes; a smaller post (a deck of 4 slides or fewer)
    on the tree's own collage model, never as the 5-photo layout with an empty box."""
    be = mods["board_evidence"]
    from PIL import Image

    from src.multi_format import FACEBOOK_COLLAGE
    photos = [Image.new("RGB", (1440, 1800), (40 * k, 200, 0)) for k in range(5)]
    five = be.fb_collage(photos)
    assert five.size == (1137, be.fb_collage_boxes(1137)[-1][3])
    if 4 in FACEBOOK_COLLAGE:
        boxes = be.tree_collage_boxes(4, 1137)
        four = be.fb_collage(photos[:4])
        assert len(boxes) == 4 and four.size == (1137, max(b[3] for b in boxes))
        for k, b in enumerate(boxes):   # every box holds its own photo, none is left as the gap colour
            cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
            assert four.getpixel((cx, cy)) == (40 * k, 200, 0)
