"""Lane B c3c: a figure whose only fault is the signed hedge it lacks is drawn with that hedge, not withheld.

Lane B c3b's pre-draw gate (visual_engine.gate_drawn) withheld "830 SQUARE MILES" from the Tunguska cover because the
evidence says "more than 830 square miles"; the cover drew "FLATTENED FOREST" alone. "OVER 830 SQUARE MILES" is true.
The gate now draws a missing bound or approximation hedge in its short form (visual_engine.hedge_figure), held to the
evidence again, where the line's grammar allows it (the article rule) and the surface holds it at its own size rules;
an attribution hedge ("by one estimate" as a sentence's lead, a disputed fact, "some scholars") stays withheld.

Real run inputs: tests/fixtures/lane_b_c3c/<run>.verified_script.json and <topic>.fact_sheet.json (copies of the dev
runs' verified scripts and the fact sheets they were reviewed with; sheet paths repo-relative).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from PIL import Image

from src import visual_engine as ve
from src.refusal import Declined

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lane_b_c3c"
C3B = ROOT / "tests" / "fixtures" / "lane_b_c3b"
TUNGUSKA = "run_tunguska_event_1908_1792930600"
ODESSA = "run_odessa_catacombs_1792970549"
HAL = "run_hal_saflieni_hypogeum_1792949304"
AMBER = "run_amber_room_heist_1792790550"
RICHAT = "run_richat_structure_sahara_eye_1793272675"


def _payload(run: str) -> dict:
    if (FIX / f"{run}.verified_script.json").is_file():
        p = json.loads((FIX / f"{run}.verified_script.json").read_text(encoding="utf-8"))
        sheet = ROOT / p["fact_review"]["sheet_path"]
        assert sheet.is_file() and not Path(p["fact_review"]["sheet_path"]).is_absolute()
        p["fact_review"]["sheet_path"] = str(sheet)     # the fixture sheet, wherever the tests run from
        return p
    p = json.loads((C3B / f"{run}.verified_script.json").read_text(encoding="utf-8"))
    p.setdefault("fact_review", {}).pop("sheet_path", None)
    return p


def _hero(tmp_path: Path) -> Path:
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    return hero


def _record(still: Path) -> dict:
    from src import still_layout
    return still_layout.load(still) or {}


def _cover_copy(p: dict) -> dict:
    copy = dict(p["thumbnail"])
    copy.setdefault("badge", ve.extract_curiosity_badge(p))
    return ve._in_spoken_units(copy, p)


# ----------------------------------------------------------------------------------------------- the Tunguska cover
def test_tunguska_cover_draws_over_830_square_miles(tmp_path):
    p = _payload(TUNGUSKA)
    assert ve.evidence_faults("830 SQUARE MILES", p)                      # the c3b refusal, unchanged
    cover, grid = ve.VisualEngine().generate_thumbnails(_hero(tmp_path), p, tmp_path)
    rec = _record(cover)
    assert rec["recipe"]["copy"]["line1"] == "OVER 830 SQUARE MILES"
    assert rec["recipe"]["copy"]["line2"] == "FLATTENED FOREST"
    drawn = " ".join(str(e.get("text") or "") for e in rec.get("elements") or []).upper()
    assert "OVER" in drawn and "830" in drawn
    hedged = rec["recipe"]["figure_hedged"]
    assert hedged["rule"] == ve.FIGURE_HEDGE_RULE
    row = hedged["figures"][0]
    assert (row["field"], row["drawn"], row["hedged"], row["hedge"]) == ("line1", "830 SQ MI", "OVER 830 SQ MI", "OVER")
    assert set(row["signed"]) == {"MORE THAN", "ESTIMATED MORE THAN"}
    assert "figure_withheld" not in rec["recipe"]
    # drawn, so never a hold; the run's own hedge ledger names it for the review card
    assert not [h for h in p.get("layout_holds") or [] if h.get("surface") == "cover"]
    assert [(h["surface"], h["drawn"], h["from"]) for h in p["figure_hedges"]] == [("cover", "OVER 830 SQ MI",
                                                                                     "830 SQ MI")]
    assert Path(grid).is_file()
    # the hedged line passes the evidence gate itself
    assert ve.evidence_faults("OVER 830 SQUARE MILES", p) == []


def test_the_youtube_chain_draws_the_same_hedged_line_in_its_own_rank():
    p = _payload(TUNGUSKA)
    chain = ve.youtube_headline_chain(p)
    first = next(c for c in chain if c["copy"] is not None)
    assert (first["source"], first.get("variant"), first["text"]) == ("thumbnail", "figure hedged",
                                                                      "OVER 830 SQUARE MILES FLATTENED FOREST")
    assert first["hedged"][0]["hedged"] == "OVER 830 SQ MI"
    # the figure-withheld form of the same line stays at the chain's end, the fallback it always was
    assert chain[-1].get("variant") == "figure withheld" and chain[-1]["text"] == "FLATTENED FOREST"


def test_the_youtube_figure_branch_draws_the_hedge_as_the_figures_qualifier(tmp_path):
    """The figure branch lifts "830 SQUARE MILES" out of its line as the hero; the hedge beside it is the figure's
    qualifier and leaves with it (the first render drew a stray "OVER" line under "830 SQ MI")."""
    p = _payload(TUNGUSKA)
    out = ve.VisualEngine().generate_youtube_thumbnail(_hero(tmp_path), p, tmp_path / "yt.jpg")
    qa = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    drawn = [e["text"] for e in qa["elements"] if e.get("text")]
    assert drawn.count("OVER") == 1 and "FLATTENED FOREST" in drawn
    assert qa["headline_source"] == "thumbnail" and qa["figure_hedged"]["figures"][0]["hedged"] == "OVER 830 SQ MI"
    assert "OVER 830 SQ MI for 830 SQ MI: drawn with its signed hedge" in p["thumbnail_note"]


# -------------------------------------------------------------------------------------------- what stays withheld
def test_odessa_73_day_by_one_estimate_stays_withheld():
    p = _payload(ODESSA)
    slide = next(s for s in p["carousel_slides"] if "73" in s["heading"])
    hedged: list = []
    gated, rows = ve.gate_drawn({"heading": slide["heading"].upper()}, p, "carousel slide 5", hedged=hedged)
    assert not hedged and gated["heading"] == "THE SIEGE"
    assert rows[0]["drawn"] == "73 DAY" and "attribution" in rows[0]["hedge_declined"]
    # the attribution decides, not only the article: the same figure opening a line is withheld too
    from src.figures import parse
    f = parse("73 DAYS UNDER SIEGE")[0]
    got, why = ve.hedge_figure("73 DAYS UNDER SIEGE", f, ve.evidence_faults("73 DAYS", p), p)
    assert got is None and "sentence's lead" in why


def test_amber_6_ton_stays_withheld():
    p = _payload(AMBER)
    hedged: list = []
    gated, rows = ve.gate_drawn(_cover_copy(p), p, "cover", ve.COVER_FIELDS, hedged=hedged)
    assert not hedged and gated["line1"] == "THE ROOM" and rows[0]["drawn"] == "6 TON"
    assert "no signed figure" in rows[0]["reason"]


SYN = {"title": "The Bronze Machine",
       "narration_script": "The wreck lies about 45 metres down. It held more than 82 fragments. Some scholars say it "
                           "weighed nearly 900 tons."}


@pytest.mark.parametrize("line, drawn", [
    ("45 METRES DOWN", "ABOUT 45 METRES DOWN"),                 # opens the line
    ("SUNK 45 METRES DOWN", "SUNK ABOUT 45 METRES DOWN"),       # after a verb
    ("82 FRAGMENTS FOUND", "OVER 82 FRAGMENTS FOUND"),          # a lower bound -> OVER
    ("It lies 45 metres down.", "It lies about 45 metres down."),   # sentence case: lower case mid-sentence
    ("45 metres down.", "About 45 metres down."),               # sentence case: capital at the sentence's start
])
def test_the_hedge_is_set_before_the_figure_in_the_lines_case(line, drawn):
    hedged: list = []
    gated, rows = ve.gate_drawn({"t": line}, SYN, "x", hedged=hedged)
    assert gated["t"] == drawn and not rows
    assert ve.evidence_faults(drawn, SYN) == []


@pytest.mark.parametrize("line, why", [
    ("THE 45-METRE DROP", "hyphen"),                            # an adjective: never "THE ABOUT 45-METRE DROP"
    ("ITS 45 METRES OF WATER", "determiner"),                    # after a determiner: never "ITS ABOUT 45 METRES"
    ("THE 82 FRAGMENTS", "determiner"),
    ("NEARLY 900 TONS", None),                                  # already hedged: drawn as it is
    ("900 TONS OF BRONZE", "attribution"),                      # "Some scholars say": an attribution
])
def test_the_article_and_attribution_rules_keep_the_figure_withheld(line, why):
    hedged: list = []
    gated, rows = ve.gate_drawn({"t": line}, SYN, "x", hedged=hedged)
    assert not hedged
    if why is None:
        assert gated["t"] == line and not rows
    else:
        assert rows and why in rows[0]["hedge_declined"]


def test_a_hedge_beside_a_cut_figure_is_not_set():
    """A line another figure is cut from is gated plain ("30 and 70 m" -> "and about 70 m" would misread)."""
    p = {"narration_script": "The outer ring is about 70 metres wide."}
    hedged: list = []
    gated, rows = ve.gate_drawn({"t": "RINGS 900 TONS HEAVY, 70 METRES WIDE"}, p, "x", hedged=hedged)
    assert not hedged and gated == ve.gate_drawn({"t": "RINGS 900 TONS HEAVY, 70 METRES WIDE"}, p, "x")[0]


def test_hedge_short_forms_reuse_the_parsers_hedge_classes():
    from src import figures
    assert {ve.hedge_short_form(h) for h in figures.HEDGE_LOWER} == {"OVER"}
    assert {ve.hedge_short_form(h) for h in figures.HEDGE_UPPER} == {"UP TO", "NEARLY", "UNDER"}
    for h in ("ABOUT", "AROUND", "ROUGHLY", "APPROXIMATELY", "SOME", "CIRCA", "ESTIMATED"):
        assert ve.hedge_short_form(h) == "ABOUT"
    # every drawn hedge the parser can give maps to one short form or none, never a guess
    for h in [figures._hedge_drawn(x) for x in figures.HEDGES]:
        assert ve.hedge_short_form(h) in ("OVER", "UP TO", "NEARLY", "UNDER", "ABOUT")
    # each short form, drawn, is read back by the parser as a hedge of the same direction
    for word in ("OVER", "UP TO", "NEARLY", "UNDER", "ABOUT"):
        f = figures.parse(f"{word} 45 METRES")[0]
        assert f.hedge == word


# ------------------------------------------------------------------------------------ a signed figure is unchanged
def test_richat_94_to_104_still_draws_unchanged():
    p = _payload(RICHAT)
    body = next(s["body"] for s in p["carousel_slides"] if "104" in s["body"])
    hedged: list = []
    assert ve.gate_drawn({"body": body}, p, "carousel", hedged=hedged)[0]["body"] == body and not hedged
    from src.multi_format import gate_deck
    found: list = []
    slides, _, rows = gate_deck([dict(s) for s in p["carousel_slides"]], p.get("poll"), p, "carousel", hedged=found)
    assert not rows and not found
    assert [s["body"] for s in slides] == [s["body"] for s in p["carousel_slides"]]


# ---------------------------------------------------------------------------------- fit: else the withheld form
def test_a_hedged_cover_that_does_not_fit_falls_through_to_withheld(tmp_path, monkeypatch):
    from src import cover_design
    from src.typography import TextFitError
    real = cover_design.render_cover

    def tight(im, copy, *a, **k):
        if "OVER" in str(copy.get("line1") or ""):
            raise TextFitError("the hedged line does not hold at its size (test)")
        return real(im, copy, *a, **k)
    monkeypatch.setattr(cover_design, "render_cover", tight)
    p = _payload(TUNGUSKA)
    cover, _ = ve.VisualEngine().generate_thumbnails(_hero(tmp_path), p, tmp_path)
    rec = _record(cover)
    copy = rec["recipe"]["copy"]
    assert (copy["line1"], copy["line2"]) == ("", "FLATTENED FOREST")     # c3b's withheld form, as before
    assert "figure_hedged" not in rec["recipe"]
    assert rec["recipe"]["figure_withheld"]["figures"][0]["drawn"] == "830 SQ MI"
    assert [h["omitted"] for h in p["layout_holds"] if h.get("surface") == "cover"] == ["830 SQ MI"]
    assert not p.get("figure_hedges")


def test_a_hedged_callout_label_that_does_not_fit_its_plate_is_withheld():
    from src.callouts import label_holds
    assert label_holds("ABOUT 45 BRONZE GEARS", "BRONZE GEARS", "portrait")
    # the hedge takes the label past what its plate sets at the plain label's size: the plain label stands
    assert not label_holds("ROMAN HARBOUR WALLS ABOUT 45 M", "ROMAN HARBOUR WALLS", "portrait")
    assert not label_holds("ABOUT 45 M SUBMERGED HARBOUR WALL", "SUBMERGED HARBOUR WALL", "landscape")


def test_a_deck_slide_that_does_not_fit_takes_its_plain_form_or_refuses():
    from src.multi_format import gate_deck, plain_deck, revert_hedges
    p = _payload(HAL)
    cards = [dict(s) for s in p["carousel_slides"]]
    with pytest.raises(Declined):                                    # c3b: the plain deck is refused (heading empty)
        gate_deck([dict(c, heading=c["heading"].upper()) for c in cards], None, dict(p), "carousel")
    found: list = []
    q = dict(p)
    slides, poll, _rows = gate_deck([dict(c, heading=c["heading"].upper()) for c in cards], None, q, "carousel",
                                   hedged=found)
    assert [(h["slide"], h["text"]) for h in found] == [(4, "RESTING PLACE OF ABOUT 7,000")]
    assert slides[3]["heading"] == "RESTING PLACE OF ABOUT 7,000" and not q.get("layout_holds")
    assert plain_deck(slides, found)[3]["heading"] == ""
    # its hedged form does not fit: the plain form would empty its heading, so the deck is refused as in c3b
    with pytest.raises(Declined):
        revert_hedges(slides, poll, found, {4}, q, "carousel")


def test_a_share_line_draws_its_signed_hedge_when_it_still_holds():
    from src.script_engine import SHARE_FALLBACK, share_line_problems, share_prompt
    p = dict(SYN, share_line="Send this to the diver who would swim 45 metres down, and follow for more wrecks.")
    assert not share_line_problems(p["share_line"])
    got = share_prompt(p)
    assert got == "Send this to the diver who would swim about 45 metres down, and follow for more wrecks."
    assert p["figure_hedges"][0]["surface"] == "carousel share line"
    assert share_prompt(dict(SYN, share_line="Send this to a friend who loves 900-ton wrecks")) == SHARE_FALLBACK


def test_the_quote_card_never_hedges_a_quotation():
    from src import design_cards
    src = inspect.getsource(design_cards._quote_gates)
    assert "gate_drawn(" in src and "hedged=" not in src
    assert ve.gate_drawn({"quotation": "It lies 45 metres down."}, SYN, "quote card")[1]    # refuses, as in c3b


def test_the_review_card_lists_each_hedged_figure():
    from src.telegram_bot import figure_hedge_lines
    p = _payload(TUNGUSKA)
    rows: list = []
    ve.gate_drawn(_cover_copy(p), p, "cover", ve.COVER_FIELDS, hedged=rows)
    ve.record_hedged(p, rows)
    ve.record_hedged(p, rows)                                         # once per surface and figure
    assert figure_hedge_lines(p["figure_hedges"]) == [
        "cover: OVER 830 SQ MI (copy: 830 SQ MI; signed ESTIMATED MORE THAN, MORE THAN)"]


def test_every_gated_surface_offers_the_hedge_but_the_quote_card():
    """The inventory: each "gate" path passes a `hedged` list (directly, through gate_deck, or through its share
    helper), except the quote card."""
    import importlib
    for surface, path, how, _note in ve.DRAWN_FIGURE_PATHS:
        if how != "gate" or surface in ("quote card", "carousel deck gate"):
            continue
        mod, _, qual = path.partition(":")
        fn = importlib.import_module(mod)
        for part in qual.split("."):
            fn = getattr(fn, part)
        src = inspect.getsource(fn)
        assert "hedged=" in src or "_share_hedged(" in src, f"{surface}: {path} never offers the hedge"
