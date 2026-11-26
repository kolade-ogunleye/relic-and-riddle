"""Design-freeze board tooling, cycle 5, review items 5 and 6 (docs/reviews/freeze-system-2026-11-15): invariants of
the evidence tooling, never pinned pictures.

- the carousel decks of the board are drawn by production's own entry (MultiFormatGenerator.generate_carousel_sequence,
  called as src/carousel_lazy.draw calls it), never by replaying a saved recipe through _render_carousel_slide; the
  photo matcher (a paid vision call) is never reached; a run whose saved data cannot drive that entry is named with the
  missing input, and only then is the replay drawn, with a note on its tile;
- the usage tiers follow the usage inventory (the 16:9 end screen has no production caller; the photo callout is
  behind a flag that is off by default), and every case of every row has one;
- the tier is drawn in the label strip of every tile and above every sheet saved whole, never on the judged pixels.

The decks are drawn here by a stand-in generator on a made-up run folder (any words, any slide count): no slide is
rendered, no topic is named, and the label strip is the same under every palette (it uses no brand token).
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"
QF35A = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"
RUN = "run_sample_subject_1700000001"


@pytest.fixture(scope="module")
def mods():
    for p in (str(BOARD), str(QF35A)):
        if p not in sys.path:
            sys.path.insert(0, p)
    names = ("board", "board_decks", "board_evidence", "board_extras", "render_surfaces")
    return {n: importlib.import_module(n) for n in names}


# ------------------------------------------------------------------------------------------------ a made-up run
def _photo(path: Path, seed: int) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 60), (40 + 20 * seed, 90, 200 - 20 * seed)).save(path, quality=90)
    return path


def make_run(tmp_path: Path, slides: int, words: str, archetypes: list[str] | None = None) -> tuple[Path, dict]:
    """A run folder as a real run leaves it for its carousel: the verified payload's cards, the photo pool and the
    photograph per slide (carousel_photos.json), each copy's recorded recipes, and a stored graphic poster."""
    run = tmp_path / "output" / RUN
    pool = [_photo(tmp_path / "assets" / f"{k:02d}_photo.jpg", k) for k in range(slides + 2)]
    cards = [{"heading": f"{words} HEADING {k}", "body": f"{words} body sentence number {k}."} for k in range(slides)]
    photos = [pool[k] for k in range(slides)]
    payload = {"carousel_slides": cards, "poll": {}, "narration_script": f"{words} narration."}
    run.mkdir(parents=True)
    (run / "carousel_photos.json").write_text(json.dumps({
        "key": {"cards": [f"{c['heading']}|{c['body']}" for c in cards], "stills": [str(p) for p in pool]},
        "photos": [str(p) for p in photos]}), encoding="utf-8", newline="\n")
    kinds = archetypes or (["cover"] + ["immersive"] * (slides - 2) + ["debate"])
    for folder in ("carousel", "carousel_fb"):
        (run / folder).mkdir()
        (run / folder / "still_layout.json").write_text(json.dumps({
            f"carousel_slide_{k + 1:02d}.jpg": {"recipe": {"archetype": kinds[k], "photo": str(photos[k]), "index": k,
                                                           "card": cards[k]}}
            for k in range(slides)}), encoding="utf-8", newline="\n")
    _photo(run / "graphics" / "graphic_map.jpg", 1)
    (run / "graphics" / "graphic_map.mp4").write_bytes(b"")
    return run, payload


class _Layout:
    """What still_layout.save needs of a drawn slide's layout: its record, its problems and its meta."""

    def __init__(self, meta):
        self.meta = meta

    def to_dict(self):
        return {"kind": "carousel", "size": [48, 60], "zone": [0, 0, 48, 60], "tight": False, "elements": [],
                "shortened": [], "protected": []}

    def problems(self):
        return []


class StandInGenerator:
    """generate_carousel_sequence's contract, without the drawing: reads the kept photo match from the output dir,
    saves one small JPEG per card with its layout record, and writes the picker's choice. `diagram`: the slide index
    it draws as a diagram slide (today's deck, where the run recorded a photo slide). `fail`: the copy that raises."""

    def __init__(self, diagram: int | None = None, fail: dict | None = None, match: bool = False):
        self.calls: list[dict] = []
        self.diagram, self.fail, self.match = diagram, fail or {}, match

    def generate_carousel_sequence(self, scene_images, script_payload, output_dir, facebook=False, graphics=None,
                                   strict_match=False):
        from PIL import Image

        from src import still_layout
        from src.multi_format import CAROUSEL_PHOTOS, MultiFormatGenerator
        self.calls.append({"facebook": facebook, "strict_match": strict_match, "stills": list(scene_images),
                           "graphics": [Path(g).name for g in graphics or []], "output_dir": Path(output_dir)})
        if facebook in self.fail:
            raise self.fail[facebook]
        cards = script_payload["carousel_slides"]
        if self.match:   # a path that reaches the matcher: production's own method, as _carousel_photos calls it
            MultiFormatGenerator()._match_photos(cards, list(scene_images), strict=True)
        kept = json.loads((Path(output_dir) / CAROUSEL_PHOTOS).read_text(encoding="utf-8"))
        assert kept["key"]["cards"] == [f"{c['heading']}|{c['body']}" for c in cards]
        assert len(kept["photos"]) == len(cards)
        kept["choice"] = [{"slide": k + 1, "photo": kept["photos"][k], "reason": "matched"} for k in range(len(cards))]
        (Path(output_dir) / CAROUSEL_PHOTOS).write_text(json.dumps(kept), encoding="utf-8", newline="\n")
        out = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for k, card in enumerate(cards):
            path = out / f"carousel_slide_{k + 1:02d}.jpg"
            Image.new("RGB", (48, 60), (10 * k, 200 if facebook else 60, 90)).save(path, quality=92)
            kind = "diagram" if k == self.diagram else ("cover" if k == 0 else "immersive")
            still_layout.save(path, _Layout({"masthead": {"shown": k == 0, "size": 40.0 + k}}),
                              {"archetype": kind, "photo": kept["photos"][k], "index": k, "card": card,
                               "facebook": facebook})
            paths.append(path)
        return paths


# ------------------------------------------------------------------------------------------------ item 5: the decks
@pytest.mark.parametrize("slides,words", [(5, "PLAIN"), (7, "OTHER SUBJECT")])
def test_a_deck_is_drawn_by_generate_carousel_sequence_as_the_lazy_carousel_calls_it(mods, tmp_path, monkeypatch,
                                                                                    slides, words):
    from PIL import Image

    from src.multi_format import MultiFormatGenerator
    bd = mods["board_decks"]
    run, payload = make_run(tmp_path, slides, words)

    def never(*_a, **_k):
        raise AssertionError("a deck slide was drawn outside generate_carousel_sequence (the saved-recipe replay)")
    monkeypatch.setattr(MultiFormatGenerator, "_render_carousel_slide", never)
    real_match = MultiFormatGenerator._match_photos
    gen = StandInGenerator(diagram=2)
    before = json.dumps(payload, sort_keys=True)
    deck = bd.production_deck(run, payload, tmp_path / "work", generator=gen)
    # both copies, Instagram first, each as carousel_lazy.draw calls it: the run's pool, its posters, a strict match
    assert [c["facebook"] for c in gen.calls] == [False, True]
    assert all(c["strict_match"] is True and c["graphics"] == ["graphic_map.jpg"] for c in gen.calls)
    assert all(len(c["stills"]) == slides + 2 and c["output_dir"] == tmp_path / "work" for c in gen.calls)
    assert json.dumps(payload, sort_keys=True) == before          # the caller's payload is not written on
    assert MultiFormatGenerator._match_photos is real_match       # the block is taken off again
    assert deck["path"] == "multi_format.generate_carousel_sequence" and deck["own_copy"] is True
    for folder, kind, fb in (("carousel", "carousel", False), ("carousel_fb", "fb", True)):
        made = deck["copies"][kind]
        assert made["held"] is None and [s["stem"] for s in made["slides"]] == [
            f"carousel_slide_{k + 1:02d}" for k in range(slides)]
        for k, s in enumerate(made["slides"]):
            # the board shows the file production delivered, decoded: pixel for pixel
            with Image.open(tmp_path / "work" / folder / f"{s['stem']}.jpg") as im:
                assert s["image"].tobytes() == im.convert("RGB").tobytes()
            assert s["index"] == k and s["masthead"] == {"shown": k == 0, "size": 40.0 + k}
            assert s["recorded"]["archetype"] == ("cover" if k == 0 else "debate" if k == slides - 1 else "immersive")
            rec = bd.choice_record(deck, s)
            assert rec["drawn_through"] == bd.PATH and rec["reason"] == "matched" and rec["slide"] == k + 1
            assert rec["diagram"] is (k == 2) and Path(rec["recorded"]).name == f"{k:02d}_photo.jpg"
        assert [s["archetype"] for s in made["slides"]][2] == "diagram"


def test_a_copy_set_keeps_the_runs_photographs_and_never_reaches_the_matcher(mods, tmp_path):
    bd = mods["board_decks"]
    run, payload = make_run(tmp_path, 5, "PLAIN")
    other = dict(payload, carousel_slides=[dict(c, heading=f"NEW {k}", body=f"Other words {k}.")
                                           for k, c in enumerate(payload["carousel_slides"])])
    deck = bd.production_deck(run, other, tmp_path / "work", generator=StandInGenerator())
    assert deck["own_copy"] is False and len(deck["copies"]["carousel"]["slides"]) == 5
    assert [Path(p).name for p in deck["photos"]] == [f"{k:02d}_photo.jpg" for k in range(5)]
    # a path that would match the photographs again stops at the block, named; nothing is drawn another way
    with pytest.raises(bd.NotDriven, match="paid vision call"):
        bd.production_deck(run, payload, tmp_path / "work2", generator=StandInGenerator(match=True))


def test_a_run_that_cannot_drive_the_entry_says_which_input_is_missing(mods, tmp_path):
    bd = mods["board_decks"]
    run, payload = make_run(tmp_path, 5, "PLAIN")
    gen = StandInGenerator()
    short = dict(payload, carousel_slides=payload["carousel_slides"][:4])
    with pytest.raises(bd.NotDriven, match=rf"{RUN}: its recorded match holds 5 photograph\(s\) for 4 slide\(s\)"):
        bd.production_deck(run, short, tmp_path / "w1", generator=gen)
    (tmp_path / "assets" / "03_photo.jpg").unlink()
    with pytest.raises(bd.NotDriven, match=rf"{RUN}: 1 photograph\(s\) .* no longer on disk \(03_photo.jpg\)"):
        bd.production_deck(run, payload, tmp_path / "w2", generator=gen)
    (run / "carousel_photos.json").unlink()
    with pytest.raises(bd.NotDriven, match=rf"{RUN} has no carousel_photos.json"):
        bd.production_deck(run, payload, tmp_path / "w3", generator=gen)
    assert gen.calls == []     # never half drawn


def test_a_copy_production_would_not_ship_is_held_not_drawn_another_way(mods, tmp_path):
    from src.refusal import LayoutError
    bd = mods["board_decks"]
    run, payload = make_run(tmp_path, 5, "PLAIN")
    stems = [f"carousel_slide_{k + 1:02d}" for k in range(5)]
    # the Instagram copy refused: the carousel is held and no Facebook copy is drawn (carousel_lazy.draw)
    gen = StandInGenerator(fail={False: LayoutError("the heading cannot be set whole")})
    deck = bd.production_deck(run, payload, tmp_path / "w1", generator=gen)
    assert [c["facebook"] for c in gen.calls] == [False]
    assert deck["copies"]["carousel"]["held"]["class"] == "LayoutError" and deck["copies"]["carousel"]["stems"] == stems
    assert deck["copies"]["fb"]["held"]["code"] == "instagram_copy_held" and deck["copies"]["fb"]["slides"] == []
    # the Facebook copy fails: the Instagram copy stands, the Facebook one is held with its error
    gen = StandInGenerator(fail={True: RuntimeError("boom")})
    deck = bd.production_deck(run, payload, tmp_path / "w2", generator=gen)
    assert len(deck["copies"]["carousel"]["slides"]) == 5 and "boom" in deck["copies"]["fb"]["held"]["error"]


def test_a_diagram_slides_omissions_go_to_that_slide_and_the_rest_stay_with_the_job(mods):
    from src.multi_format import COMPARATOR_RULE, DIAGRAM_SLIDE_SURFACE
    bd, rs = mods["board_decks"], mods["render_surfaces"]
    rows = [{"surface": f"{DIAGRAM_SLIDE_SURFACE} (map graphic_map.mp4)", "omitted": "the scale bar", "rule": "r"},
            {"surface": "carousel slide 4", "omitted": "a line", "rule": COMPARATOR_RULE},
            {"surface": "carousel", "omitted": "slide 3 (X)", "rule": "carousel.slide_drop"}]
    placed = bd.slide_rows(rows + rows[:1], ["carousel_slide_04"])
    assert list(placed) == ["carousel_slide_04"] and [r["omitted"] for r in placed["carousel_slide_04"]] == [
        "the scale bar", "a line"]
    assert bd.slide_rows(rows, []) == {}
    # render_surfaces.py: a deck is drawn whole before its slides are written, so the job names each slide's rows
    result: dict = {}
    surfaces = rs.AttributedSurfaces(result)
    from src import refusal
    with refusal.collecting():
        refusal.omit("deck", "a part", "rule.a", "why")
        refusal.omit("deck", "another", "rule.b", "why")
        surfaces.give("slide_01", [])
        surfaces["slide_01"] = {"sha256": "x"}
        surfaces.give("slide_04", placed["carousel_slide_04"])
        surfaces["slide_04"] = {"sha256": "y"}
        refusal.omit("next", "its own", "rule.c", "why")
        surfaces["after"] = {"sha256": "z"}
    got = result["surface_omissions"]
    assert "slide_01" not in got and [r["omitted"] for r in got["slide_04"]] == ["the scale bar", "a line"]
    assert [r["omitted"] for r in got["after"]] == ["its own"]


def _function(src: str, name: str) -> str:
    """The source of the (nested) function `name`: from its def to the next line at or left of its indent."""
    m = re.search(rf"^(?P<pad> *)def {name}\(", src, re.M)
    assert m, name
    body = src.index(chr(10), m.end()) + 1
    end = re.search(rf"^ {{0,{len(m['pad'])}}}\S", src[body:], re.M)
    return src[m.start():body + end.start()] if end else src[m.start():]


def test_the_social_job_draws_its_decks_through_production_and_replays_only_what_cannot(mods):
    """Fails when the job goes back to the saved-recipe replay: the carousel copies are skipped by the replay loop
    whenever production's entry drew (or held) them, and the one direct slide call left is the replay's own."""
    src = (QF35A / "render_surfaces.py").read_text(encoding="utf-8")
    social = _function(src, "social")
    assert "replay_why = production_decks(run, entry)" in social
    loop = social[social.index("for sub, kind in"):]
    assert loop.split("\n")[1].strip() == 'if kind != "story" and replay_why is None:'
    assert loop.split("\n")[2].strip() == "continue"
    decks = _function(src, "production_decks")
    assert "board_decks.production_deck(run, payload," in decks and "except board_decks.NotDriven as e:" in decks
    assert "_render_carousel_slide" not in decks and "draw_social" not in decks
    assert src.count("gen._render_carousel_slide(") == 1 and "gen._render_carousel_slide(" in _function(src, "draw_social")
    # the replay marks every tile it draws, and the board says so on the tile
    assert 'replay=replay_why, drawn_through="multi_format._render_carousel_slide"' in social
    be = mods["board_evidence"]
    note = be.record_note("s", {"photo_choice": {"s": {"replay": f"{RUN} has no carousel_photos.json"}}})
    assert note.startswith("REPLAY, not production's draw") and RUN in note
    # a production-drawn slide: no replay note; a diagram slide never claims a photograph it does not show
    drawn = {"photo_choice": {"s": {"drawn_through": "multi_format.generate_carousel_sequence", "photo": "a/n.jpg",
                                    "replaced": "a/o.jpg", "reason": "unfit", "diagram": True}}}
    assert be.record_note("s", drawn) == ""
    drawn["photo_choice"]["s"]["diagram"] = False
    assert "n.jpg for o.jpg (unfit)" in be.record_note("s", drawn)
    # the production entry itself, in the module the job calls
    bd_src = (BOARD / "board_decks.py").read_text(encoding="utf-8")
    assert "gen.generate_carousel_sequence, list(got[\"pool\"]), payload, work" in bd_src
    assert "_render_carousel_slide" not in _function(bd_src, "production_deck")
    # the Aswan deck's own call is under the same block
    assert "with board_decks.no_vision():" in _function((BOARD / "board_extras.py").read_text(encoding="utf-8"),
                                                        "aswan_deck")


def test_the_drawn_archetype_is_the_trees_where_it_differs_from_the_runs(mods, tmp_path):
    bx = mods["board_extras"]
    run, _payload = make_run(tmp_path, 5, "PLAIN")
    stem = f"social__{RUN[4:]}__carousel_carousel_slide_03"
    assert bx.recipe_archetype(tmp_path, stem) == "immersive"
    assert bx.recipe_archetype(tmp_path, stem, {stem: {"recorded": "immersive", "current": "diagram"}}) == "diagram"
    assert bx.recipe_archetype(tmp_path, stem, {"other": {"current": "diagram"}}) == "immersive"


# ------------------------------------------------------------------------------------------------ item 6: tiers
# The usage inventory's "reachable in a normal production run today" column, per surface kind (a name of the kind,
# the inventory's words, the tier that follows from them).
INVENTORY = [
    ("endscreen__0_subject", "tests / board only: no caller in src", "T4"),
    ("endscreen__0_subject__rest", "tests / board only: no caller in src", "T4"),
    ("endcard__1_subject", "tests / board only", "T4"),
    ("callouts__reference_pair__916", "flag-gated: CALLOUTS_ENABLED, code default false", "T3"),
    ("callouts__reference_pair__916__f168", "flag-gated: CALLOUTS_ENABLED, code default false", "T3"),
    ("cover__subject__youtube", "flag-gated: documentary", "T3"),
    ("map__subject_1__169", "flag-gated: documentary", "T3"),
    ("cover__subject__reel", "yes, every run", "T1"),
    ("cover__subject__grid", "written every run, never uploaded", "T4"),
    ("bridge_follow__1_subject", "yes, every Reel", "T1"),
    ("scale__subject_1__916", "yes, data-gated", "T2"),
    ("thennow__subject__916", "yes, data-gated", "T2"),
    ("route__subject_1_new_stops__916", "yes, data-gated", "T2"),
    ("cross_section__test_spec__916", "yes, data-gated", "T2"),
    ("timeline_dense__test_events_subject_1__916", "tests only in practice", "T4"),
    ("card__chapter_act1__ground__916", "tests / board only", "T4"),
]


def test_the_tiers_follow_the_usage_inventory(mods):
    be, b = mods["board_evidence"], mods["board"]
    for name, reachable, want in INVENTORY:
        assert be.tier(name) == want, (name, reachable)
    # the two ruled cases, as a clip and as a tile too
    assert be.tier(b.clip_tier_name("endscreen_169__0_subject")) == "T4"
    assert be.tier(b.clip_tier_name("callouts__reference_pair__916")) == "T3"
    assert be.tile_tier("callouts__reference_pair__916__f168 | STAND-IN: x", "row11_thennow_callout_badge")[0] == "T3"
    assert "pipeline calls it" in be.TIER_WHAT["T4"] and "off by default" in be.TIER_WHAT["T3"]
    assert set(be.TIER_WHAT) == set(be.TIER_COLOURS) == {"T1", "T2", "T3", "T4"}
    # an evidence sheet is internal, and says which tier's surface it measures
    assert be.sheet_measures("row12_motion_bridge_nofollow_steps") == "T1"
    assert be.sheet_measures("row12_motion_endscreen__subject_curves") == "T4"
    assert be.sheet_measures("row12_motion_callouts__reference_pair__916_timing") == "T3"
    assert be.sheet_measures("row12_motion_thennow__subject__169") == "T3"
    assert be.sheet_measures("row07_captions_run__subject__916_bursts") == "T1"
    assert be.sheet_measures("row12_motion_eases") is None and be.sheet_measures("row13_elements") is None
    t, words = be.tile_tier("loop bridge f012 t=0.400s", "row12_motion_bridge")
    assert t == "T4" and "measures a tier 1 surface" in words


def test_every_case_of_every_row_has_a_tier(mods):
    be = mods["board_evidence"]
    rows = {"1": ["row01_specimen_tall_ink", "row01_specimen_wide_photo"], "2": ["row02_masthead"],
            "3": ["cover__subject__reel", "cover__subject__grid"],
            "5": ["social__subject_1__carousel_carousel_slide_04", "social__subject_1__fb_carousel_slide_02"],
            "9": ["endscreen__0_subject", "bridge__1_subject"], "11": ["callouts__reference_pair__916__f168"],
            "12": ["row12_motion_bridge", "row12_motion_bridge_curves", "row12_motion_eases",
                   "row12_motion_callouts__reference_pair__916_timing"],
            "13": ["row13_elements", "row13_fb_masthead", "row13_motion_tokens"],
            "peer_feed": ["peer_feed_ig_grid", "peer_feed_fb_feed"]}
    manifest = {"rows": rows, "labels": {"social__subject_1__fb_carousel_slide_02": "x | diagram slide (recipe)"},
                "case_tiers": {"social__subject_1__carousel_carousel_slide_04": "T2"}}   # the loop's own, kept
    tiers = be.case_tiers(manifest, ())
    cases = {c for cs in rows.values() for c in cs}
    assert cases <= set(tiers) and set(tiers.values()) <= set(be.TIER_WHAT)
    assert tiers["social__subject_1__carousel_carousel_slide_04"] == "T2"
    assert tiers["social__subject_1__fb_carousel_slide_02"] == "T2"       # told by its label
    assert tiers["endscreen__0_subject"] == "T4" and tiers["callouts__reference_pair__916__f168"] == "T3"
    assert all(tiers[c] == "T4" for r in ("1", "2", "12", "13", "peer_feed") for c in rows[r])
    assert manifest["sheet_measures"]["row12_motion_bridge_curves"] == "T1"
    assert manifest["sheet_measures"]["row12_motion_callouts__reference_pair__916_timing"] == "T3"
    assert "row12_motion_eases" not in manifest["sheet_measures"]
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert "be.case_tiers(manifest, WIDE_CAPTIONS)" in src and "tier_sheets(out)" in src


def _has(im, colour) -> bool:
    import numpy as np
    return bool((np.asarray(im.convert("RGB")) == np.array(colour, dtype=np.uint8)).all(axis=2).any())


# tiles as the rows outside the surface loop name them: row 2's masthead tiles, row 12's strip frames, row 13's
# Facebook masthead crops, a peer feed's slots; and a case tile of a looped row
OUTSIDE = [("row02_masthead", "dark | carousel cover | bone 5.2:1 | 1x", "T4"),
           ("row12_motion_bridge", "loop bridge f012 t=0.400s", "T4"),
           ("row13_fb_masthead", "social__subject_1__fb_carousel_slide_01 | Facebook cover masthead region", "T1"),
           ("peer_feed_ig_grid", "STAND-IN peer slot 3", "T4"),
           ("peer_feed_yt_home", "cover__subject__youtube | ours: slot 2", "T3"),
           ("row09_endscreen_bridge", "endscreen__0_subject | at rest", "T4")]


@pytest.mark.parametrize("sheet_name,tile,want", OUTSIDE)
def test_the_tier_is_drawn_in_the_label_strip_of_every_tile_never_on_the_image(mods, tmp_path, sheet_name, tile, want):
    import numpy as np
    from PIL import Image
    b, be = mods["board"], mods["board_evidence"]
    rng = np.random.default_rng(7)
    im = Image.fromarray(rng.integers(0, 255, (120, 400, 3), dtype=np.uint8), "RGB")   # no tier colour by chance
    assert not any(_has(im, c) for c in be.TIER_COLOURS.values())
    b.sheet([(tile, im)], tmp_path / sheet_name, tile_h=None, cols=1)
    with Image.open(tmp_path / f"{sheet_name}.png") as got:
        assert got.info.get(b.TIER_KEY) == want
        got = got.convert("RGB")
    strip_h = got.height - im.height
    assert strip_h > b.TIER_PX and got.width == im.width
    assert got.crop((0, strip_h, im.width, got.height)).tobytes() == im.tobytes()      # the judged pixels, untouched
    strip = got.crop((0, 0, got.width, strip_h))
    assert _has(strip, be.TIER_COLOURS[want])
    assert not any(_has(strip, c) for t, c in be.TIER_COLOURS.items() if t != want)
    assert b.TIER_PX < b.LABEL_PX                                                      # small: under the label's size
    plain = b.label(im, tile)                                                          # without a tier: no such line
    assert plain.height < got.height and not any(_has(plain, c) for c in be.TIER_COLOURS.values())


def test_a_sheet_saved_whole_gets_its_tier_strip_once_and_the_surfaces_are_never_opened(mods, tmp_path):
    from PIL import Image
    b, be = mods["board"], mods["board_evidence"]
    out = tmp_path / "board"
    (out / "surfaces").mkdir(parents=True)
    (out / "cases").mkdir()
    table = Image.new("RGB", (640, 200), (250, 250, 250))
    for stem in ("row01_specimen_tall_ink", "row12_motion_bridge_curves", "row13_elements", "refusals_default"):
        table.save(out / f"{stem}.png")
    table.resize((320, 100)).save(out / "row01_specimen_tall_ink_half.jpg", quality=90)
    surface = Image.new("RGB", (64, 64), (1, 2, 3))
    surface.save(out / "surfaces" / "cover__subject__reel.png")
    surface.save(out / "cases" / "endscreen__0_subject__rest.png")
    kept = {p: p.read_bytes() for p in (out / "surfaces").iterdir()} | {p: p.read_bytes() for p in (out / "cases").iterdir()}
    b.sheet([("cover__subject__reel | x", surface)], out / "row03_reel_cover_grid", tile_h=None, cols=1)
    tiled = (out / "row03_reel_cover_grid.png").read_bytes()
    done = b.tier_sheets(out)
    assert done == {"refusals_default": "T4", "row01_specimen_tall_ink": "T4", "row12_motion_bridge_curves": "T4",
                    "row13_elements": "T4"}
    for stem in done:
        with Image.open(out / f"{stem}.png") as im:
            assert im.info.get(b.TIER_KEY) == "T4"
            im = im.convert("RGB")
        strip_h = im.height - table.height
        assert strip_h > 0 and im.crop((0, strip_h, 640, im.height)).tobytes() == table.tobytes()
        assert _has(im.crop((0, 0, im.width, strip_h)), be.TIER_COLOURS["T4"])
    with Image.open(out / "row01_specimen_tall_ink_half.jpg") as half:
        assert half.size == (320, (200 + strip_h) // 2)               # the half-size copy is of the stamped sheet
    assert (out / "row03_reel_cover_grid.png").read_bytes() == tiled   # a sheet of tiles carries its tiers already
    assert b.tier_sheets(out) == {}                                    # a second pass stamps nothing twice
    assert all(p.read_bytes() == data for p, data in kept.items())     # surfaces/ and cases/ are never opened
