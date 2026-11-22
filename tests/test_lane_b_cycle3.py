"""Freeze lane B, cycle 3: invariants on synthetic copy (made-up words, no topic), flat grounds.

- S1 truth: the figure hero is the copy's statement of the figure whole: its hedge and both ends of a span are drawn,
  or the figure is declined with a recorded reason that names the rule (YouTube and the Reel cover).
- A figure from outside the headline never leaves one headline line drawn without the other.
- The YouTube figure-to-title gap is measured to the title's top ink (an accent over its caps).
- One YouTube masthead size, whatever the hook.
- The cover and YouTube lockups never stack a one-word row under the balance minimum while another form sets.
- A refused headline names the rule that refused it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import cover_design, figures, masthead
from src import youtube_thumbnail as yt
from src.refusal import LayoutError

FLAT = (96, 92, 88)


def _yt(copy, figure=None):
    meta = {}
    im, plates = yt.render(Image.new("RGB", (1920, 1080), FLAT), copy, meta=meta, figure=figure)
    return im, plates, meta


def _figure_text(plates):
    return " ".join(p.text for n, p in plates if n in ("figure_qualifier", "figure", "figure_unit") and not p.empty)


SPAN_HEDGE = [
    {"eyebrow": "OLD QUARRY", "line1": "GRANITE GIANT", "line2": "NEVER RAISED", "tagline": "LEFT IN STONE",
     "badge": "ROUGHLY 137–138 FT"},
    {"eyebrow": "X", "line1": "THE 2,676–2,954 M", "line2": "RIDGE", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "VAULT", "line2": "", "tagline": "ESTIMATED 2,223 M"},
    {"eyebrow": "X", "line1": "BRONZE", "line2": "", "tagline": "ESTIMATED 3,465 KG"},
    {"eyebrow": "DEEP DUST", "line1": "ETCHED LINES", "line2": "UNCHANGED", "tagline": "WHERE",
     "badge": "EST. 4–6 IN (10–15 CM)"},
]


@pytest.mark.parametrize("copy", SPAN_HEDGE, ids=range(len(SPAN_HEDGE)))
def test_the_youtube_figure_is_a_signed_statement_whole(copy):
    try:
        _, plates, meta = _yt(copy)
    except LayoutError as e:
        assert "refused" in str(e)          # a recorded refusal names its rule
        return
    drawn = _figure_text(plates)
    signed = cover_design.signed_copy_texts(copy)
    if drawn:
        assert figures.truth_faults(drawn, signed) == [], drawn
        for f in figures.parse(drawn):
            twins = [s for s in figures.signed_figures(signed) if set(f.values) & set(s.values)]
            assert any(s.values == f.values for s in twins), drawn         # never one end of a span
    else:
        assert meta.get("figure_declined") or meta.get("figure_withheld") or not any(
            figures.parse(t) for t in signed)


def test_a_comparator_hedge_is_drawn_with_its_figure_never_dropped():
    # integration 3: lane B declined "EST. >13,000 LBS" because the parser could not read ">"; SYS-B-4 reads a
    # comparator as its hedge, so the figure line now draws the statement whole, or nothing
    copy = {"eyebrow": "X", "line1": "LOST ROOM", "line2": "", "tagline": "WHERE", "badge": "EST. >13,000 LBS"}
    _, plates, meta = _yt(copy)
    drawn = _figure_text(plates)
    assert "MORE THAN 13,000" in drawn and "figure_declined" not in meta, (drawn, meta.get("figure_declined"))
    assert figures.truth_faults(drawn, cover_design.signed_copy_texts(copy)) == [], drawn


def test_a_badge_figure_never_leaves_one_headline_line_alone():
    copy = {"eyebrow": "OLD QUARRY", "line1": "GRANITE GIANT", "line2": "NEVER RAISED", "tagline": "LEFT IN STONE",
            "badge": "ROUGHLY 137–138 FT"}
    _, plates, meta = _yt(copy)
    titles = [p.text for n, p in plates if n == "title" and not p.empty]
    for line in ("GRANITE GIANT", "NEVER RAISED"):
        assert line not in titles                                   # the pair whole or neither
    assert meta.get("headline_pair_kept")


def _mask(plate):
    im = Image.new("L", (yt.W, yt.H), 0)
    ImageDraw.Draw(im).text(plate.xy, plate.text, font=plate.font, fill=255, anchor=plate.anchor)
    return np.asarray(im) > 40


def test_the_title_under_a_figure_clears_its_accented_caps():
    from src.typography import cap_height
    copy = {"eyebrow": "X", "line1": "THE 30-MILE DOMÉ", "line2": "", "tagline": "WHERE"}
    _, plates, _ = _yt(copy)
    up = [p for n, p in plates if n in ("figure", "figure_unit", "figure_dim") and not p.empty]
    low = [p for n, p in plates if n == "title" and not p.empty]
    assert up and low
    u = np.any([_mask(p) for p in up], axis=0)
    lo = np.any([_mask(p) for p in low], axis=0)
    gap = int(np.nonzero(lo.any(1))[0].min()) - int(np.nonzero(u.any(1))[0].max()) - 1
    assert gap >= round(yt.TITLE_LINE_GAP * cap_height(low[0].font)) - 1


@pytest.mark.parametrize("copy", [
    {"eyebrow": "X", "line1": "EYE", "line2": "", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "LOST GEARS", "line2": "OLD TIDE", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST", "tagline": "WHERE"},
], ids=["short", "two lines", "figure"])
def test_one_youtube_masthead_size(copy):
    _, _, meta = _yt(copy)
    rec = meta["masthead"]
    assert rec["shown"]
    assert abs(rec["size_px"] - yt.mast_px()) < 1e-6
    assert any(abs(yt.mast_px() - s) < 1e-6 for s in masthead.fit_ladder(yt.BRAND_PX))   # a declared step


def test_the_cover_lockup_never_stacks_an_unbalanced_lone_row_when_one_heading_sets():
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    out, rec = cover_design._lockup(m, {"line1": "CARVED FROM", "line2": "SOLID SALT"}, 912, 108)
    assert cover_design.lone_balance(m, out) is None or rec.get("balance_below_min")
    rows = [" ".join(t for t, _ in lr) for k in ("line1", "line2") if k in out for lr in out[k][1]]
    assert " ".join(rows) == "CARVED FROM SOLID SALT"                                    # the copy's words, whole
    assert all(r.split()[-1] != "FROM" for r in rows[:-1])


def test_a_refusal_names_the_rule():
    width_of = lambda t: 100.0 * len(t)                       # noqa: E731 (a deterministic measure)
    why = yt.refusal_rule("CARVED FROM SOLID SALT", width_of, 1150, 160)
    assert "line-end rule" in why and "FROM" in why
    why = yt.refusal_rule("UNBREAKABLEWORDS", width_of, 500, 160)
    assert why.startswith("the measure")


# ---------------------------------------------------------------------------------------- QF-101: cover encoding
def _sampling(path):
    from PIL import Image, JpegImagePlugin
    with Image.open(path) as im:
        return JpegImagePlugin.get_sampling(im), im.mode


@pytest.mark.parametrize("surface", ["youtube", "reel", "grid"])
def test_a_cover_jpeg_is_444_ycbcr_at_the_highest_quality_within_its_limit(tmp_path, surface, monkeypatch):
    from src import visual_engine as ve
    rng = np.random.default_rng(7)
    im = Image.fromarray(rng.integers(0, 256, (1080, 1920, 3), dtype=np.uint8))   # incompressible: steps down
    rec = ve.save_cover_jpeg(im, tmp_path / "c.jpg", surface)
    assert _sampling(tmp_path / "c.jpg") == (0, "RGB")                 # 4:4:4, YCbCr (decoded to RGB), no keep_rgb
    assert (tmp_path / "c.jpg").stat().st_size == rec["bytes"] <= ve.COVER_LIMITS[surface]
    higher = [q for q in ve.COVER_QUALITIES if q > rec["quality"]]
    if higher:   # the highest step that fits: one step up would not
        import io
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=min(higher), subsampling=0)
        assert buf.tell() > ve.COVER_LIMITS[surface]
    monkeypatch.setitem(ve.COVER_LIMITS, surface, 1000)
    with pytest.raises(ValueError):                                     # never a subsampled or oversized fallback
        ve.save_cover_jpeg(im, tmp_path / "d.jpg", surface)


def test_every_delivered_cover_is_444_within_its_limit(tmp_path):
    from src import visual_engine as ve
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    payload = {"thumbnail": {"eyebrow": "OLD QUARRY", "line1": "LOST GEARS", "line2": "OLD TIDE", "tagline": "WHERE"},
               "title": "Lost gears"}
    cover, grid = VisualEngine().generate_thumbnails(hero, payload, tmp_path / "out", style="minimal", variants=False)
    yt_path = VisualEngine().generate_youtube_thumbnail(hero, dict(payload), tmp_path / "out" / "yt.jpg")
    for path, surface in ((cover, "reel"), (grid, "grid"), (yt_path, "youtube")):
        assert _sampling(path)[0] == 0, path
        assert Path(path).stat().st_size <= ve.COVER_LIMITS[surface]


# ------------------------------------------------- the signed short YouTube headline (lane B cycle 3 follow-up)
def test_the_headline_bound_is_derived_from_the_thumbnails_measure():
    import string
    from PIL import ImageDraw as _D

    from src import brand
    from src.script_engine import youtube_headline_instruction
    from src.typography import get_font
    b = yt.headline_bound()
    font = get_font(brand.DISPLAY_FONT, b["size_px"])
    m = _D.Draw(Image.new("RGB", (8, 8)))
    adv = m.textlength(string.ascii_uppercase, font=font) / 26
    col = yt.TEXT_RIGHT - yt.LEFT                    # lane B c3 ruling: the design's standard text column
    assert b["size_px"] == yt.feed_floor_px(yt.HERO_FLOOR) and b["measure_px"] == col
    assert b["chars_per_row"] * adv <= col + m.textlength(" ", font=font)
    assert (b["chars_per_row"] + 1) * adv > col
    assert f"at most {b['chars_per_row']} characters" in youtube_headline_instruction()


LONG_TITLE = {"title": "The Secret Labyrinth Deep Beneath Harbour", "youtube_title": "",
              "narration_script": "Roughly 137 to 138 feet of stone lie under the harbour. The tunnels were cut by hand."}


def test_a_signed_short_headline_sets_the_thumbnail_first(tmp_path):
    import main as M
    from src.visual_engine import VisualEngine
    cover = M.documentary_cover(LONG_TITLE["title"], payload=dict(LONG_TITLE))
    got, problems = M.signed_youtube_headline(["HARBOUR", "LABYRINTH"], cover, dict(LONG_TITLE))
    assert problems == [] and got["youtube_line1"] == "HARBOUR" and got["youtube_line2"] == "LABYRINTH"
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    out = VisualEngine().generate_youtube_thumbnail(hero, dict(LONG_TITLE, thumbnail=got), tmp_path / "yt.jpg")
    import json
    qa = json.loads(Path(out).with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert qa["headline_source"] == "youtube_headline"
    assert {e["text"] for e in qa["elements"] if e["name"].startswith("title")} <= {"HARBOUR", "LABYRINTH"}


@pytest.mark.parametrize("lines, why", [
    (["138 FT", "TUNNELS"], "signed span"),                # the signed figure is "roughly 137 to 138 feet"
    (["INCOMPREHENSIBILITIES", "AGAIN"], "YouTube thumbnail"),   # a word wider than the measure never sets
    (["A", "B", "C"], "lines"),
], ids=["span end", "measure", "three lines"])
def test_a_signed_headline_that_fails_a_gate_is_left_out_with_its_reason(lines, why):
    import main as M
    cover = M.documentary_cover(LONG_TITLE["title"], payload=dict(LONG_TITLE))
    got, problems = M.signed_youtube_headline(lines, cover, dict(LONG_TITLE))
    assert "youtube_line1" not in got and problems and any(why in p for p in problems), problems


def test_the_thumbnail_falls_back_to_the_title_when_the_signed_headline_does_not_set(tmp_path):
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    payload = {"title": "Lost Gears Old Tide", "youtube_title": "",
               "thumbnail": {"line1": "LOST GEARS", "line2": "OLD TIDE", "youtube_line1": "INCOMPREHENSIBILITIES",
                             "youtube_line2": "UNDERSTANDABLENESS"}}
    out = VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    import json
    qa = json.loads(Path(out).with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert qa["headline_source"] == "thumbnail"
    assert qa["headline_chain"][0]["source"] == "youtube_headline"
    assert qa["headline_chain"][0].get("refused") or qa["headline_chain"][0].get("skipped")


# ------------------------------------------------- the ranked headline chain (lane B cycle 3, lander ruling)
CHAIN_PAYLOAD = {
    "title": "Lost Gears of the Wreck", "youtube_title": "The Bronze Machine Nobody Could Explain",
    "narration_script": "Divers found the gears in 1901. Roughly 137 to 138 feet of wreck lay below.",
    "thumbnail": {"eyebrow": "ANCIENT MYSTERY", "line1": "INCOMPREHENSIBILITIES", "line2": "WRECKAGE",
                  "tagline": "FOUND BY DIVERS"},
    "carousel_slides": [{"heading": "THE BRONZE MACHINE NOBODY"}, {"heading": "INTERIOR SLIDE LINE"}],
    "story_slides": [{"line1": "SUNKEN GEARS", "line2": "OF BRONZE"}, {"line1": "STORY BODY", "line2": "LINE"}],
}


def _by(payload):
    from src.visual_engine import youtube_headline_chain
    return {c["source"]: c for c in youtube_headline_chain(payload)}


def test_the_chain_is_ranked_with_the_eyebrow_last_and_each_source_gated():
    from src.visual_engine import YOUTUBE_HEADLINE_CHAIN, youtube_headline_chain
    chain = youtube_headline_chain(dict(CHAIN_PAYLOAD))
    assert [c["source"] for c in chain] == list(YOUTUBE_HEADLINE_CHAIN) and YOUTUBE_HEADLINE_CHAIN[-1] == "eyebrow"
    by = {c["source"]: c for c in chain}
    assert by["youtube_headline"]["skipped"] == "none in this run"
    assert by["carousel_cover"]["copy"] is None                         # a fragment ending on NOBODY, or the title
    texts = " ".join(c["text"] for c in chain)
    assert "INTERIOR SLIDE LINE" not in texts and "STORY BODY" not in texts           # interior lines never used
    assert by["story_hook"]["copy"] is not None                         # GEARS: a word of this run's subject
    # a non-thumbnail source is the headline alone: the render's own fallback to the eyebrow / tagline (QF-23
    # headline_from) can never draw a line past the chain's gates (lander review: "ANCIENT MARVELS" drawn via it)
    assert by["story_hook"]["copy"]["eyebrow"] == by["story_hook"]["copy"]["tagline"] == ""


def test_a_candidates_figures_are_held_to_the_evidence_never_to_derived_copy():
    """Lander review (Hal Saflieni "7,000 RESTING SOULS"): the title's unhedged figure is derived copy, never
    evidence; the narration says the figure is approximate, so a tagline that drops the hedge fails."""
    p = dict(CHAIN_PAYLOAD, title="The 7,000-Body Wreck", narration_script="It held approximately 7,000 bodies.",
             first_comment="7,000 bodies lay there.",
             thumbnail=dict(CHAIN_PAYLOAD["thumbnail"], tagline="7,000 RESTING SOULS"))
    assert _by(p)["tagline"]["skipped"].startswith("truth:")
    p["thumbnail"]["tagline"] = "APPROXIMATELY 7,000 SOULS"
    assert _by(p)["tagline"]["copy"] is not None


def test_a_candidate_that_repeats_the_title_or_is_a_code_label_is_skipped():
    p = dict(CHAIN_PAYLOAD, carousel_slides=[{"heading": "BRONZE MACHINE NOBODY EXPLAINED"}])
    assert _by(p)["carousel_cover"]["skipped"] == "repeats the video title"
    p = dict(CHAIN_PAYLOAD, thumbnail=dict(CHAIN_PAYLOAD["thumbnail"], tagline="DOCUMENTARY"))
    tag = _by(p)["tagline"]
    assert tag["copy"] is None and tag["text"] != "DOCUMENTARY"     # a code label is never a candidate
    p["parent_copy"] = {"thumbnail": {"tagline": "GEARS FOUND BY DIVERS"}}   # a documentary reads its parent Reel's
    assert _by(p)["tagline"]["text"] == "GEARS FOUND BY DIVERS"


def test_a_category_label_is_not_specific():
    """Lander review ("ANCIENT MARVELS"): a candidate made only of the brand's generic vocabulary, or with no figure
    and no word of this run's subject, is skipped; a subject word or a figure makes it specific."""
    from src.visual_engine import headline_generic_vocabulary, specificity_fault
    assert {"ANCIENT", "MYSTERY"} <= headline_generic_vocabulary()
    assert _by(dict(CHAIN_PAYLOAD))["eyebrow"]["skipped"].startswith("not specific")
    assert specificity_fault("FOUND BY DIVERS", CHAIN_PAYLOAD).startswith("not specific")
    assert specificity_fault("THE WRECK BELOW", CHAIN_PAYLOAD) == ""
    assert specificity_fault("ROUGHLY 137-138 FT DOWN", CHAIN_PAYLOAD) == ""


def test_the_record_says_what_was_drawn_and_from_which_source(tmp_path):
    """Lander review (pair 1): the figure branch may draw the copy's badge figure over another of its lines; the
    record and the review card say what was DRAWN."""
    import json
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    payload = {"title": "The Granite Giant of the Quarry", "narration_script": "It is roughly 137 to 138 ft long.",
               "stat_cards": [{"value": "Roughly 137-138 ft", "label": "Length"}],
               "thumbnail": {"eyebrow": "QUARRY GIANT", "line1": "GRANITE GIANT", "line2": "NEVER RAISED",
                             "tagline": "LEFT IN THE QUARRY", "badge": "ROUGHLY 137–138 FT"}}
    out = VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    qa = json.loads(Path(out).with_suffix(".qa.json").read_text(encoding="utf-8"))
    drawn = " ".join(e["text"] for e in qa["elements"]
                     if e["name"] in ("figure_qualifier", "figure", "figure_unit", "title_1", "title_2", "title"))
    assert qa["headline_chain"][-1]["drawn"] == drawn
    assert qa["headline_chain"][-1]["source"] == qa["headline_source"]
    assert drawn in payload["thumbnail_note"]


def test_the_thumbnail_walks_the_chain_and_the_review_card_names_the_source(tmp_path):
    import json
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    payload = dict(CHAIN_PAYLOAD)
    out = VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    qa = json.loads(Path(out).with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert qa["headline_source"] not in ("youtube_headline", "thumbnail")    # both fail: a later source sets
    assert any(t.get("refused") or t.get("skipped") for t in qa["headline_chain"] if t["source"] == "thumbnail")
    assert "headline from" in payload["thumbnail_note"]


def test_the_thumbnail_is_refused_only_when_the_whole_chain_fails(tmp_path):
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    long = "INCOMPREHENSIBILITIES"
    payload = {"title": long, "thumbnail": {"eyebrow": long, "line1": long, "line2": long, "tagline": long}}
    with pytest.raises(LayoutError) as e:
        VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    assert "every source" in str(e.value) and "eyebrow" in str(e.value)


def test_a_place_alone_is_not_specific():
    """Lander review: "ANCIENT MALTA" / "ANCIENT ASWAN" are category labels; a city, region or country alone is not
    the subject, the thing itself is."""
    from src.visual_engine import specificity_fault
    p = {"topic_id": "harbour_wreck_gears", "title": "The Gears of the Harbour Wreck",
         "location": {"name": "Harbour Museum", "city": "Portville", "country": "Islandia"}}
    assert specificity_fault("ANCIENT PORTVILLE", p).startswith("not specific")
    assert specificity_fault("ISLANDIA", p).startswith("not specific")
    assert specificity_fault("THE SUNKEN GEARS", p) == ""


@pytest.mark.parametrize("text", ["THE BURIED CITY", "THE UNREAD CODEX", "THE SECRET WEAPON"])
def test_ruling_4s_shape_sets_on_the_thumbnail(text):
    """Operator ruling 4 (lander, 2026-11-19): a modifier row over its head noun alone stands at any balance on the
    thumbnail lockup (the engine's phrase exception asks 0.55); the deferral of unbalanced lone rows never overrides it."""
    from PIL import ImageDraw as _D
    from src.cover_design import head_noun_shape, unbalanced_lone
    m = _D.Draw(Image.new("RGB", (8, 8)))
    rows, *_, rec = yt._lockup(m, text, "", 904, kickerless=True)
    w = text.split()
    assert rec["drawn_rows"] in ([text], [" ".join(w[:2]), w[2]])
    assert head_noun_shape([" ".join(w[:2]), w[2]]) and unbalanced_lone([" ".join(w[:2]), w[2]], [600, 200]) is None


def test_the_never_end_rule_is_unchanged_by_ruling_4s_shape():
    from src.cover_design import head_noun_shape
    assert not head_noun_shape(["CARVED FROM", "SALT"])       # a row never ends on a never-end word
    assert not head_noun_shape(["THE BURIED", "OF"])          # the last row is a content word
    assert not head_noun_shape(["WHY ROMAN", "CONCRETE", "HEALS"])


def test_a_noun_the_evidence_repeats_is_the_subject():
    """Lander (2026-11-22): "FORGOTTEN GEARS" names the thing itself: a noun found in at least two separate evidence
    texts (narration, stat cards, graphics data, facts) is a subject word; once is not; a place never is."""
    from src.visual_engine import specificity_fault
    p = {"topic_id": "harbour_wreck", "title": "The Harbour Wreck",
         "location": {"name": "Harbour Museum", "city": "Portville", "country": "Islandia"},
         "narration_script": "Divers raised bronze gears from the wreck near Portville. A lantern lay beside them.",
         "stat_cards": [{"value": "30", "label": "Gears counted"}]}
    assert specificity_fault("FORGOTTEN GEARS", p) == ""
    assert specificity_fault("THE OLD LANTERN", p).startswith("not specific")          # one evidence text only
    assert specificity_fault("ANCIENT PORTVILLE", p).startswith("not specific")
