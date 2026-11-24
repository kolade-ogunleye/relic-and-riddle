"""Lane B c3b (int-3 board, lander ruling): a figure the run's evidence does not sign is never drawn on any surface.

The int-3 board drew "THE 6-TON ROOM" on the Amber Room Reel cover and grid tile while the YouTube chain refused the
same line ("'6 TON': no signed figure has this number in this unit"). Every path that draws a figure taken from copy
now goes through one pre-draw gate (visual_engine.gate_drawn); visual_engine.DRAWN_FIGURE_PATHS is the inventory.

Real run inputs: tests/fixtures/lane_b_c3b/<run>.verified_script.json (copies of the dev runs' verified scripts); the
fact sheets are the committed data/fact_sheets.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from src import visual_engine as ve
from src.refusal import LayoutError

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lane_b_c3b"
AMBER = "run_amber_room_heist_1792790550"
RICHAT = "run_richat_structure_sahara_eye_1793272675"


def _payload(run: str) -> dict:
    p = json.loads((FIX / f"{run}.verified_script.json").read_text(encoding="utf-8"))
    # the committed topic sheet, never a sheet path from the machine that made the run
    p.setdefault("fact_review", {}).pop("sheet_path", None)
    return p


def _hero(tmp_path: Path) -> Path:
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    return hero


def _drawn(still: Path) -> str:
    from src import still_layout
    rec = still_layout.load(still) or {}
    return " ".join(str(e.get("text") or "") for e in rec.get("elements") or [] if isinstance(e, dict))


def _record(still: Path) -> dict:
    from src import still_layout
    return still_layout.load(still) or {}


# ------------------------------------------------------------------------------------------ the Amber Room defect
def test_the_amber_reel_cover_and_grid_tile_draw_no_6_ton(tmp_path):
    p = _payload(AMBER)
    assert ve.evidence_faults("THE 6-TON ROOM", p)                      # the YouTube chain's refusal, unchanged
    cover, grid = ve.VisualEngine().generate_thumbnails(_hero(tmp_path), p, tmp_path)
    rec = _record(cover)
    drawn = json.dumps(rec.get("elements"), ensure_ascii=False).upper()
    assert "6-TON" not in drawn and "6 TON" not in drawn and '"6"' not in drawn
    assert "ROOM" in drawn and "36 HOURS" in drawn                       # the line kept, never a word added
    assert rec["recipe"]["copy"]["line1"] == "THE ROOM"
    held = rec["recipe"]["figure_withheld"]
    assert held["rule"] == ve.FIGURE_GATE_RULE and held["figures"][0]["drawn"] == "6 TON"
    assert "no signed figure" in held["reason"]
    # the grid tile is the cover's own crop: the same drawn copy, the same record
    assert Path(grid).is_file() and Path(grid).name == "thumbnail_profile_grid.jpg"
    # on the run's ledger for the review card and the release gate's note
    rows = [h for h in p["layout_holds"] if h.get("rule") == ve.FIGURE_GATE_RULE]
    assert [(h["surface"], h["omitted"], h["drawn"]) for h in rows] == [("cover", "6 TON", True)]


def test_the_reel_and_youtube_hold_a_figure_to_one_gate():
    """The sibling surfaces agree: what the YouTube chain refuses, the Reel gate lifts out, with the same reason."""
    p = _payload(AMBER)
    gated, rows = ve.gate_drawn(dict(p["thumbnail"]), p, "cover", ve.COVER_FIELDS)
    chain = {c["source"]: c for c in ve.youtube_headline_chain(p) if not c.get("variant")}
    assert chain["thumbnail"]["skipped"].startswith("truth: ")
    assert rows and rows[0]["reason"].endswith(chain["thumbnail"]["skipped"][len("truth: "):])
    variant = [c for c in ve.youtube_headline_chain(p) if c.get("variant")]
    assert variant[0]["text"] == f"{gated['line1']} {gated['line2']}"


# --------------------------------------------------------------------------------------- a signed figure still draws
def test_a_signed_figure_still_draws_richats_94_to_104_million_years():
    p = _payload(RICHAT)
    body = next(s["body"] for s in p["carousel_slides"] if "104" in s["body"])
    assert ve.evidence_faults(body, p) == []                             # the loaded fact sheet signs it
    assert ve.gate_drawn({"body": body}, p, "carousel")[0]["body"] == body
    from src.multi_format import gate_deck
    slides, _, rows = gate_deck([dict(s) for s in p["carousel_slides"]], p.get("poll"), p, "carousel")
    assert not rows and [s["body"] for s in slides] == [s["body"] for s in p["carousel_slides"]]


def test_the_fact_sheet_is_evidence_as_the_review_loaded_it():
    """A payload holds fact_sheet: true and the sheet's path; the sheet was never read, so its figures failed."""
    p = _payload(RICHAT)
    assert p["fact_sheet"] is True
    assert any("94 and 104" in t for t in ve.evidence_texts(p))
    assert not any("94 and 104" in t for t in ve.evidence_texts(dict(p, fact_sheet=False, fact_review={})))


def test_a_disputed_fact_signs_only_through_its_hedge():
    sheet = {"facts": [{"statement": "It is 40 km across.", "hedge": "about 40 km by survey, almost 50 km from orbit",
                        "disputed": True}]}
    p = {"fact_sheet": sheet}
    assert ve.evidence_faults("40 KM ACROSS", p)
    assert not ve.evidence_faults("ABOUT 40 KM ACROSS", p)


# ------------------------------------------------------------------------------------------- the gate, per surface
SYN = {"title": "The Bronze Machine", "narration_script": "The wreck lies about 45 metres down. It held 82 fragments."}


def test_the_gate_lifts_an_unsigned_figure_whole_and_keeps_a_signed_one():
    gated, rows = ve.gate_drawn({"heading": "THE 900-TON WRECK", "body": "It held 82 fragments in about 45 metres."},
                                SYN, "carousel slide 1", ("heading", "body"))
    assert gated == {"heading": "THE WRECK", "body": "It held 82 fragments in about 45 metres."}
    assert [(r["surface"], r["field"], r["drawn"]) for r in rows] == [("carousel slide 1", "heading", "900 TON")]


def test_a_hedge_closing_the_line_before_reads_with_the_figure():
    gated, rows = ve.gate_drawn({"line1": "THE WRECK LIES ABOUT", "line2": "45 METRES DOWN"}, SYN, "cover",
                                ("line1", "line2"))
    assert not rows and gated["line2"] == "45 METRES DOWN"


def test_a_deck_slide_left_with_nothing_readable_is_refused_never_drawn_with_the_figure():
    from src.multi_format import gate_deck
    from src.refusal import Declined
    with pytest.raises(Declined) as e:
        gate_deck([{"heading": "THE WRECK", "body": "900 TONS"}], None, dict(SYN), "carousel")
    assert e.value.rule == ve.FIGURE_GATE_RULE and "carousel slide 1" in str(e.value)
    p = dict(SYN)
    slides, poll, rows = gate_deck([{"heading": "THE 900-TON WRECK", "body": "It held 82 fragments."}],
                                   {"question": "Could divers raise the 900-ton wreck?", "options": ["Yes", "No"]}, p,
                                   "carousel")
    assert slides[0]["heading"] == "THE WRECK" and poll["question"] == "Could divers raise the wreck?"
    assert {h["surface"] for h in p["layout_holds"]} >= {"carousel slide 1"}


def test_a_cover_headline_left_with_nothing_readable_is_refused(tmp_path):
    from src.refusal import Declined
    p = dict(SYN, thumbnail={"eyebrow": "SUNKEN GEARS", "line1": "900 TONS", "line2": "", "tagline": "FOUND BY DIVERS"})
    with pytest.raises(Declined) as e:
        ve.VisualEngine().generate_thumbnails(_hero(tmp_path), p, tmp_path)
    assert e.value.rule == ve.FIGURE_GATE_RULE and isinstance(e.value, LayoutError)


def test_story_frames_draw_no_unsigned_figure(tmp_path):
    from src.multi_format import gate_deck
    p = dict(SYN)
    frames, _, rows = gate_deck([{"line1": "THE 900-TON", "line2": "BRONZE WRECK", "body": "It held 82 fragments."}],
                                None, p, "Story", ("line1", "line2", "body"), ("line1", "line2"), "frame")
    assert frames[0]["line1"] == "" and frames[0]["line2"] == "BRONZE WRECK" and rows[0]["surface"] == "Story frame 1"


def test_a_cut_that_tears_its_sentence_leaves_nothing_drawn():
    """Mid-sentence copy (slide bodies, Story lines): a preposition or joiner left without the figure it governed is
    no line ("CARVED FROM OF SALT"); a cut the sentence survives stands."""
    assert ve.figure_free("Carved from 900 tons of salt", SYN, []) == ""
    assert ve.figure_free("Did 900 tons of bronze sink it?", SYN, []) == ""
    assert ve.figure_free("STOLEN IN 36 HOURS", SYN, []) == ""
    assert ve.figure_free("The 900-ton wreck sank fast", SYN, []) == "The wreck sank fast"


def test_callout_labels_and_share_lines_go_through_the_gate():
    from src.script_engine import SHARE_FALLBACK, share_prompt
    assert share_prompt(dict(SYN, share_line="Send this to a friend who loves 900-ton wrecks")) == SHARE_FALLBACK
    assert "gate_drawn(" in inspect.getsource(importlib.import_module("src.callouts").make)


# ------------------------------------------------------------------------------------------ the inventory is a test
_KEYS = ("thumbnail", "carousel_slides", "story_slides", "photo_labels", "share_line", "poll", "hook_sentence",
         "closing_sentence", "youtube_headline", "end_card")
_RENDERERS = ("cover_design", "visual_engine", "multi_format", "youtube_thumbnail", "callouts", "design_cards",
              "end_card", "graphics", "then_now", "video_composer", "card_composition", "timeline_dense",
              "carousel_lazy", "script_engine")


def _resolve(path: str):
    mod, _, qual = path.partition(":")
    obj = importlib.import_module(mod)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj


def test_every_inventoried_path_exists_and_every_gate_entry_calls_the_shared_gate():
    seen = set()
    for surface, path, how, note in ve.DRAWN_FIGURE_PATHS:
        assert how in ("gate", "equivalent", "evidence", "derived", "exempt", "reader"), (surface, how)
        assert note, surface
        fn = _resolve(path)
        if how == "gate":
            src = inspect.getsource(fn)
            assert "gate_drawn(" in src or "gate_deck(" in src, f"{surface}: {path} does not call the shared gate"
        seen.add(path)
    # the shared deck gate is the shared function's own form, never a copy of it
    assert "gate_drawn(" in inspect.getsource(_resolve("src.multi_format:gate_deck"))


def test_every_reader_of_drawn_copy_in_a_renderer_module_is_inventoried():
    """Find-all: any function of a renderer module that reads a drawn copy field must be on DRAWN_FIGURE_PATHS (as a
    gated path or a named reader), so a new surface cannot draw copy past the gate unseen."""
    listed = {p.split(":", 1)[1].split(".")[-1] + "@" + p.split(":", 1)[0].rsplit(".", 1)[-1]
              for _, p, _, _ in ve.DRAWN_FIGURE_PATHS}
    pat = re.compile(r"""(?:\.get\(|\[)\s*["'](%s)["']""" % "|".join(_KEYS))
    missing = []
    for m in _RENDERERS:
        src = (ROOT / "src" / f"{m}.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and pat.search(ast.get_source_segment(src, node) or ""):
                if f"{node.name}@{m}" not in listed:
                    missing.append(f"src/{m}.py:{node.lineno} {node.name}")
    assert not missing, "copy read outside the figure-gate inventory: " + ", ".join(missing)
