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


# --------------------------------------------------------------------------------- 5. the Facebook collage (ruling)
def test_the_collage_model_is_the_measured_two_over_three_of_squares():
    from src.multi_format import FACEBOOK_COLLAGE
    tiles = FACEBOOK_COLLAGE[5]
    assert all(abs(tw - th) < 1e-9 for _x, _y, tw, th in tiles)                 # squares
    assert abs(tiles[0][2] - tiles[1][2]) < 1e-9 and abs(tiles[2][2] - tiles[4][2]) < 1e-9
    assert abs(tiles[1][0] + tiles[1][2] - 1) < 1e-6 and abs(tiles[4][0] + tiles[4][2] - 1) < 1e-6   # full width
    w = 1137                                                                   # the capture's measured gaps
    assert round((tiles[1][0] - tiles[0][2]) * w) == 4 and round((tiles[2][1] - tiles[0][3]) * w) == 4


def test_plus_n_is_modelled_over_the_fifth_tile_when_more_are_sent(tmp_path):
    from src.multi_format import facebook_collage_report
    folder = tmp_path / "carousel_fb"
    folder.mkdir()
    slides = []
    for i in range(7):
        p = folder / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (1440, 1800), (40, 40, 40)).save(p)
        lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
        lay.add("heading", (300, 860, 1100, 960))                              # across the tile's centre
        still_layout.save(p, lay, {"archetype": "immersive"})
        slides.append(p)
    rep = facebook_collage_report(slides)
    assert rep["more"] == 2 and len(rep["tiles"]) == 5
    assert rep["problems"] == ["carousel_slide_05.jpg: the collage tile cuts its heading (under +2)"]
    assert facebook_collage_report(slides[:5])["problems"] == []               # five sent: no "+N"


def _fb_slide(tmp_path, archetype, total, card, photo=None):
    photo = photo or _photos(tmp_path)[1]
    return MultiFormatGenerator()._render_carousel_slide(archetype, photo, photo, card, 0 if archetype == "cover" else 1,
                                                          total, {}, "", True, subject="THE GIANT STONE")[1]


def test_the_facebook_collage_is_branded_on_its_cover_only(tmp_path):
    from src.multi_format import collage_crop
    crop = collage_crop((1440, 1800), 1.0)
    cover = _fb_slide(tmp_path, "cover", 7, CARDS[0])
    box = next(e["box"] for e in cover.elements if e["name"] == "header_brand")
    assert box[1] >= crop[1] and box[3] <= crop[3] and not cover.problems()      # inside the square, whole
    for archetype, card in (("immersive", CARDS[1]), ("editorial_split", CARDS[2])):
        lay = _fb_slide(tmp_path, archetype, 7, card)
        assert not any(e["name"] == "header_brand" for e in lay.elements) and lay.meta["masthead"]["fb_interior"]
        ig = MultiFormatGenerator()._render_carousel_slide(archetype, _photos(tmp_path)[1], _photos(tmp_path)[1],
                                                           card, 1, 7, {}, "", False, subject="S")[1]
        assert any(e["name"] == "header_brand" for e in ig.elements)              # Instagram unchanged


def test_the_facebook_cover_of_a_five_photo_post_takes_the_full_column(tmp_path):
    from src.multi_format import collage_crop
    g = MultiFormatGenerator()
    card = {"heading": "THE EYE OF THE SAHARA", "body": "A concentric formation that fascinates observers."}
    wide = _fb_slide(tmp_path, "cover", 7, card)       # posts 5: the cover is a full-width square tile
    hero = _fb_slide(tmp_path, "cover", 4, card)       # posts 4: the cover is the 2:3 hero tile
    ig = g._render_carousel_slide("cover", _photos(tmp_path)[1], _photos(tmp_path)[1], card, 0, 7, {}, "", False)[1]
    size = {k: next(e["px"] for e in lay.elements if e["name"] == "heading") for k, lay in
            (("wide", wide), ("hero", hero), ("ig", ig))}
    assert size["wide"] == size["ig"] > size["hero"]    # the full tier where the collage shows the full width
    tall = collage_crop((1440, 1800), 2 / 3)
    for e in hero.elements:
        if e["name"] in ("heading", "body", "header_brand"):
            assert e["box"][0] >= tall[0] and e["box"][2] <= tall[2]


def test_the_figure_slide_takes_the_collages_second_top_tile(tmp_path):
    from src.multi_format import facebook_pick
    folder = tmp_path / "carousel_fb"
    folder.mkdir()
    slides = []
    for i, arch in enumerate(["cover", "immersive", "editorial_split", "immersive", "colossal_stat", "immersive",
                              "debate"]):
        p = folder / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (1440, 1800), (40 + 20 * i, 40, 40)).save(p)
        lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
        still_layout.save(p, lay, {"archetype": arch, "photo": str(p), "photo_next": str(p),
                                   "card": {"heading": f"H{i}", "body": "A body."}})
        slides.append(str(p))
    posted = facebook_pick(slides)
    assert len(posted) == 5 and posted[0] == slides[0] and posted[1] == slides[4] and posted[-1] == slides[-1]
    assert posted[2:4] == sorted(posted[2:4])


def test_the_lower_facebook_masthead_keeps_the_strength_of_its_ground(tmp_path):
    """The masthead's ground is a top-edge gradient: at FB_HEADER_Y the default reach left it under the gate where
    Instagram's position passes (6 of 28 real Facebook slides). The Facebook copy then takes the reach that gives the
    name the same share of the ramp; the gate is unchanged."""
    import numpy as np
    rng = np.random.default_rng(3)
    a = np.clip(np.array((170, 160, 150), np.float32) + rng.normal(0, 60, (1200, 1600, 1)), 0, 255).astype(np.uint8)
    photo = tmp_path / "mid.jpg"
    Image.fromarray(a).save(photo)
    card = {"heading": "THE CARVED BEDROCK", "body": "Workers carved it."}
    g = MultiFormatGenerator()
    ig = g._render_carousel_slide("cover", photo, photo, card, 0, 7, {}, "", False, subject="S")[1]
    fb = g._render_carousel_slide("cover", photo, photo, card, 0, 7, {}, "", True, subject="S")[1]
    assert ig.meta["masthead"]["shown"] and "reach_matched" not in ig.meta["masthead"]
    m = fb.meta["masthead"]
    from src.brand_system import tok
    assert m["shown"] and m["reach_matched"] and m["contrast"] >= tok("contrast.masthead_gate") and not fb.problems()


# --------------------------------------------------------------------------------- 6. cover over the collage's tile 2
def _rec_slide(folder: Path, i: int, arch: str, px: float | None):
    p = folder / f"carousel_slide_{i + 1:02d}.jpg"
    Image.new("RGB", (1440, 1800), (30 + 25 * i, 40, 40)).save(p)
    lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
    if px:
        lay.add("heading", (100, 1000, 1200, 1200))
        lay.elements[-1]["px"] = px
    still_layout.save(p, lay, {"archetype": arch, "photo": str(p), "photo_next": str(p),
                               "card": {"heading": f"H{i}", "body": "A body."}})
    return str(p)


def test_a_headline_second_tile_steps_down_until_the_cover_reads_over_it(tmp_path):
    from src.multi_format import COLLAGE_COVER_OVER_TILE, collage_tile_step
    archs = ["cover", "immersive", "editorial_split", "immersive", "immersive", "immersive", "debate"]
    for tile, want in ((184.0, 112.0), (165.33, 112.0), (149.33, None)):
        folder = tmp_path / f"t{int(tile)}"
        folder.mkdir()
        slides = [_rec_slide(folder, i, a, 200.0 if i == 0 else tile) for i, a in enumerate(archs)]
        got = collage_tile_step(slides)
        if want is None:
            assert got is None                                       # 200 / 149.3 = 1.34: already holds
        else:
            assert got is not None and got[1] == want and 200.0 >= COLLAGE_COVER_OVER_TILE * want * 1440 / 1080 - 1e-6
    folder = tmp_path / "fig"
    folder.mkdir()
    slides = [_rec_slide(folder, i, a, 200.0 if i == 0 else 184.0)
              for i, a in enumerate(["cover", "immersive", "colossal_stat", "immersive", "immersive", "immersive",
                                     "debate"])]
    assert collage_tile_step(slides) is None                          # a figure on tile 2: a different device


def test_the_facebook_deck_draws_its_second_tile_under_the_cover(tmp_path):
    from src.multi_format import COLLAGE_COVER_OVER_TILE, facebook_pick
    cards = [{"heading": "THE GIANT STONE", "body": "A giant stone lies in the quarry."},
             {"heading": "CARVED IN PLACE", "body": "Workers cut it from the bedrock."},
             {"heading": "ANCIENT WORK MARKS", "body": "Crews burned and wedged the rock."},
             {"heading": "SOFT RED OCHRE", "body": "Guide lines were painted in red."},
             {"heading": "CRACKED", "body": "A crack ended the work."},
             {"heading": "LEFT BEHIND", "body": "The stone was abandoned in place."},
             {"heading": "YOUR TAKE", "body": "Would you have finished it?"}]
    run = tmp_path / "run"
    payload = {"carousel_slides": cards, "title": "The giant stone"}
    g = MultiFormatGenerator()
    ig = g.generate_carousel_sequence(_photos(tmp_path) * 2, payload, run)
    fb = g.generate_carousel_sequence(_photos(tmp_path) * 2, payload, run, facebook=True)
    posted = facebook_pick([str(x) for x in fb])
    recs = {p: still_layout.load(Path(p)) for p in posted[:2]}
    px = {p: next(e["px"] for e in r["elements"] if e["name"] == "heading") for p, r in recs.items()}
    assert px[posted[0]] >= COLLAGE_COVER_OVER_TILE * px[posted[1]] - 1e-6 and not recs[posted[1]]["problems"]
    assert recs[posted[1]]["recipe"]["heading_px"]
    j = [str(x) for x in fb].index(posted[1])
    ig_px = next(e["px"] for e in still_layout.load(ig[j])["elements"] if e["name"] == "heading")
    assert ig_px > px[posted[1]]                                      # Instagram's copy keeps the deck's tier
