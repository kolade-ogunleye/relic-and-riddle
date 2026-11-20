"""Design-freeze lane D (carousel and Stories), cycle 3: invariants. Synthetic inputs (CI has no output/).

1. Drawn == signed on every carousel slide (lander correction of ruling 4, D-S1-1): a diagram slide keeps its own
   signed heading and body; the drawing carries only its own labels (no second title system, no copy lost).
2. A scale drawing goes on the slide that speaks of its dimension (a length included), not by the tie-break.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from src import still_layout
from src.multi_format import MultiFormatGenerator, carousel_diagram, slide_drawing, typeset
from src.script_engine import facebook_photo_caption, neutral_cues

FACT = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Giant stone", "approximate": True}
CARDS = [{"heading": "THE GIANT STONE", "body": "A giant stone lies in the quarry."},
         {"heading": "CARVED IN PLACE", "body": "Workers cut it from the bedrock."},
         {"heading": "ANCIENT WORK MARKS", "body": "Crews burned and wedged the rock, leaving tool marks."},
         {"heading": "COLOSSAL SCALE", "body": "Its completed length is about 138 feet."},
         {"heading": "CRACKED", "body": "A crack ended the work."},
         {"heading": "YOUR TAKE", "body": "Would you have finished it?"}]


def _photos(tmp_path: Path) -> list[Path]:
    out = []
    for i, colour in enumerate([(150, 120, 90), (70, 90, 120), (200, 190, 170), (90, 80, 60)]):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1600, 1200), colour).save(p)
        out.append(p)
    return out


def _drawn(rec: dict, name: str) -> str:
    return " ".join(" ".join(e.get("text") or "" for e in rec["elements"] if e["name"] == name).split())


def test_every_slide_draws_its_own_signed_words_the_diagram_slide_included(tmp_path):
    run = tmp_path / "run"
    stored = run / "graphics" / "graphic_scale.jpg"
    stored.parent.mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(stored)
    payload = {"carousel_slides": CARDS, "scale_fact": FACT, "title": "The giant stone"}
    g = MultiFormatGenerator()
    for fb in (False, True):
        clean = facebook_photo_caption if fb else neutral_cues
        slides = g.generate_carousel_sequence(_photos(tmp_path) * 2, payload, run, facebook=fb)
        recs = [still_layout.load(s) for s in slides]
        kinds = [r["recipe"]["archetype"] for r in recs]
        assert kinds.count("diagram") == 1
        for i, (rec, signed) in enumerate(zip(recs, CARDS)):
            # the recipe's card is the signed card (no figure here for the lead unit to convert)
            assert rec["recipe"]["card"] == signed, (i, rec["recipe"]["card"])
            if kinds[i] in ("colossal_stat", "debate"):
                continue   # their stacks set the figure / the question (held by their own tests)
            assert _drawn(rec, "heading") == " ".join(typeset(clean(signed["heading"])).upper().split()), i
            assert _drawn(rec, "body") == " ".join(typeset(clean(signed["body"])).split()), i
        d = recs[kinds.index("diagram")]
        assert slide_drawing(Path(d["recipe"]["photo"])) and not d["problems"]


def test_a_scale_drawing_goes_on_the_slide_about_its_length(tmp_path):
    poster = tmp_path / "graphics" / "graphic_scale.jpg"
    poster.parent.mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(poster)
    assert carousel_diagram(tmp_path / "a", CARDS, [poster]) == (3, poster)


# --------------------------------------------------------------------------------- 3. accents keep the line gaps
def _gaps(plates):
    """The air between consecutive lines' ink, top to bottom (plates grouped into lines by their baseline)."""
    lines = {}
    for p in plates:
        y = lines.setdefault(round(p.xy[1]), [p.box[1], p.box[3]])
        y[0], y[1] = min(y[0], p.box[1]), max(y[1], p.box[3])
    ordered = [lines[k] for k in sorted(lines)]
    return [nxt[0] - cur[1] for cur, nxt in zip(ordered, ordered[1:])]


def test_an_accented_second_heading_line_keeps_the_line_gap(tmp_path):
    photo = _photos(tmp_path)[1]
    g = MultiFormatGenerator()
    plain = {"heading": "THE WHISPERING VAULT", "body": "A gallery where a whisper carries across the dome."}
    accented = dict(plain, heading="THE WHISPERING VÁULT")
    for fb in (False, True):
        got = {}
        for key, card in (("plain", plain), ("accent", accented)):
            _im, lay = g._render_carousel_slide("cover", photo, photo, card, 0, 6, {}, "", fb)
            got[key] = _gaps(lay.plates["heading"])
            assert not [p for p in lay.problems() if "collid" in str(p).lower() or "overlap" in str(p).lower()]
        if got["plain"]:   # a two-line lockup: the accented line keeps (at least) the plain line gap
            assert got["accent"] and min(got["accent"]) >= min(got["plain"]) - 2, got


def test_an_accented_story_title_keeps_its_gaps(tmp_path):
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1080, 1920), (110, 100, 90)).save(photo)
    g = MultiFormatGenerator()
    for line1, line2 in (("BUILT WITHOUT", "WITHOÚT TENSION"), ("ÁNTIKYTHERA", "THE FIRST COMPUTER")):
        slide = {"kicker": "THE DOME", "line1": line1, "line2": line2, "body": "A short body line."}
        _im, lay = g._render_story_frame(photo, slide, 0, 2, {})
        titles = [p for n in ("title_1", "title_2") for p in lay.plates.get(n, [])]
        assert all(gap > 0 for gap in _gaps(titles)), _gaps(titles)
        kicker = next(e["box"] for e in lay.elements if e["name"] == "kicker")
        assert min(p.box[1] for p in titles) > kicker[3]


# --------------------------------------------------------------------------------- 4. the stat lockup's record
def test_a_stacked_stat_unit_is_recorded_in_reading_order(tmp_path):
    from src import figure_gate, figures
    photo = _photos(tmp_path)[2]
    body = "Dating shows the rocks cooled between 94 and 104 million years ago."
    card = {"heading": "ANCIENT COOLING", "body": body}
    _im, lay = MultiFormatGenerator()._render_carousel_slide("colossal_stat", photo, photo, card, 4, 7, {}, "", False)
    text = next(e["text"] for e in lay.elements if e["name"] == "stat")
    assert text == lay.meta["stat_figure"]["drawn"]
    assert figure_gate.figure_faults(text, figures.parse(body)) == []
