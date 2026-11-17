"""Production run_derinkuyu_underground_city_1793303149 (29 Oct, rc72).

1. A locator map was rendered and the place named in the first sentence, but the video used scale, numbers and
   timeline and no map: two beats named the place equally (beat 2, "in Cappadocia, Turkey", and the last line,
   "Derinkuyu ... between Greece and Turkey"), the lock took the latest, the graphics cap then dropped the latest
   locked graphic (the map), and the scale graphic followed the hook onto the place's words.
2. The rewording of three lines no photo showed was refused: "Caption plus image credits exceeds Instagram's 2,200
   characters". The revision started from the verified caption, which already carried the credit block, and the
   block was appended again.
3. The timeline is earliest first, top to bottom, revealed in that order (1923 above 1963 is chronological).
No model, network or repository write: the planner, writer and checks are stubs, files go to tmp_path.
"""
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import main
from src import beat_images, config, graphics, prevoice_lines, scene_planner as sp
from src.lineage import record_graphic

from test_prevoice_lines_2026_10_28 import MODERN, ROMAN, world  # noqa: F401  (the fixture)


def _graphic(tmp_path, name, inputs):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    g = tmp_path / name
    g.write_bytes(b"graphic")
    Image.new("RGB", (32, 32), (0, 0, 120)).save(g.with_suffix(".jpg"))
    record_graphic(g, code, inputs)
    return g


@pytest.fixture
def four(tmp_path):
    numbers = _graphic(tmp_path, "graphic_numbers.mp4", {"builder": "numbers", "payload": {
        "stat_cards": [{"value": "Up to 20,000", "label": "People sheltered"}]}})
    place = _graphic(tmp_path, "graphic_map.mp4", {"builder": "map", "payload": {}, "place": {
        "label": "Derinkuyu Underground City", "country": "Turkey", "region": "Nevşehir / Cappadocia"}})
    timeline = _graphic(tmp_path, "graphic_timeline.mp4", {"builder": "timeline", "payload": {"timeline": [
        {"year": 1923, "approximate": False, "label": "Completely abandoned"},
        {"year": 1963, "approximate": False, "label": "Rediscovered during home renovation"}]}})
    scale = _graphic(tmp_path, "graphic_scale.mp4", {"builder": "scale", "payload": {"scale_fact": {
        "value": 280, "unit": "ft", "dimension": "depth", "subject": "City depth", "approximate": True}}})
    return numbers, place, timeline, scale


BEATS = [
    {"text": "A subterranean city plunges roughly 280 feet beneath houses"},      # the hook
    {"text": "in Cappadocia, Turkey."},
    {"text": "Up to 20,000 people could shelter inside."},
    {"text": "Stone doors sealed each level."},
    {"text": "Stables and chapels filled the upper floors."},
    {"text": "Air shafts reached the deepest rooms."},
    {"text": "A homeowner rediscovered it in 1963 during a home renovation."},   # the timeline's best line
    {"text": "Wells kept water out of reach of raiders."},
    {"text": "Narrow tunnels linked it to other towns."},
    {"text": "Derinkuyu was completely abandoned in 1923 following the compulsory population exchange "
             "between Greece and Turkey."},
]


# ---------------------------------------------------------------- 1: the map where the place is named

def test_the_map_takes_the_earliest_beat_that_names_the_place_most_strongly(four):
    numbers, place, timeline, scale = four
    locked = sp.lock_narrative_graphics(BEATS, [numbers, place, timeline, scale])
    assert locked[1] == place                       # "in Cappadocia, Turkey", not the last line
    assert locked[2] == numbers and locked[6] == timeline
    assert scale not in locked.values()             # only the hook says 280 feet, and the beat after it is the map's


def test_a_map_named_only_by_the_hook_follows_it_and_one_named_nowhere_is_not_locked(four):
    _, place, _, _ = four
    beats = [{"text": "Beneath Cappadocia in Turkey, a city waited."}, {"text": "Its shafts carried air."},
             {"text": "Stone doors sealed it."}]
    assert sp.map_beat(beats, place) == 1
    assert sp.map_beat([{"text": "A city waited."}, {"text": "Its shafts carried air."}], place) is None
    assert sp.map_beat([{"text": "In Turkey."}], place) is None             # no beat after the hook


def test_the_plan_keeps_the_map_over_the_graphics_cap_and_the_planners_choice(four, tmp_path, monkeypatch):
    numbers, place, timeline, scale = four
    photos = []
    for i in range(8):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (64, 64), (i * 25, 40, 0)).save(p)
        photos.append(p)
    media = photos + [numbers, place, timeline, scale]
    by = {p: i + 1 for i, p in enumerate(media)}
    # The planner's own plan (as production's): the scale graphic on the place's words, the map nowhere.
    picks = [photos[0], scale, numbers, photos[1], photos[2], photos[3], timeline, photos[4], photos[5], photos[6]]
    monkeypatch.setattr(sp, "claude_available", lambda: True)
    monkeypatch.setattr(sp.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(sp, "structured_call", lambda *a, **k: sp.ScenePlan(images=[by[p] for p in picks], notes="planner"))
    beats = [dict(b, start=3.0 * i, end=3.0 * (i + 1)) for i, b in enumerate(BEATS)]
    scenes, _, _ = sp.plan_scenes(beats, media, use_storyboard=False)
    assert scenes[1] == place and scenes.count(place) == 1
    assert sum(1 for s in scenes if s.stem.startswith("graphic_")) <= sp.MAX_GRAPHICS


# ---------------------------------------------------------------- 2: the rewording and the caption's credits

CREDITS = {"reviewed": True, "images": [
    {"file": f"{i:02d}_commons.jpg", "title": f"File:Photo {i}.jpg", "artist": f"Photographer number {i} of the archive",
     "license": "CC BY-SA 4.0", "attribution_required": True} for i in range(1, 15)]}


def _coordinator(tmp_path, monkeypatch, engine):
    assets = tmp_path / "assets"
    (assets / "t").mkdir(parents=True)
    (assets / "t" / "credits.json").write_text(json.dumps(CREDITS), encoding="utf-8")
    for r in CREDITS["images"]:
        (assets / "t" / r["file"]).write_bytes(b"jpg")
    monkeypatch.setattr(main, "ASSETS_DIR", assets)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")      # no fact sheet: no review recorded
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = SimpleNamespace(update_run=lambda *a, **k: None)
    c.script_engine = engine
    c._check_cancel = lambda run_id: None
    c.progress_msg_id = None
    return c


class Engine:
    """The writer: a revision changes one narration line and keeps the caption it was given."""
    progress = None

    def __init__(self, caption):
        self.caption, self.given = caption, []

    def generate_script(self, topic, revision_notes="", previous=None):
        self.given.append(previous)
        if previous is None:
            return {"origin": "claude", "narration_script": "One. Two.", "caption": self.caption}
        return dict(previous, narration_script="One. Three.")

    def verify_claims(self, payload, topic=None, allow_unreviewed_curated=False):
        return True, payload, "PASSED"

    def _load_curated(self, topic_id):
        return None


def test_a_revision_of_a_verified_script_carries_one_credit_block(tmp_path, monkeypatch):
    caption = "A city lies beneath Cappadocia. " * 30 + "\n\n#history"        # about 970 characters
    engine = Engine(caption)
    c = _coordinator(tmp_path, monkeypatch, engine)
    topic = {"id": "t", "title": "T"}
    first = c._write_and_check_script(topic, "run", tmp_path, dry_run=False)
    assert first["caption"].count(beat_images.CREDIT_PREFIX) == 1 and len(first["caption"]) <= 2200
    revised = c._write_and_check_script(topic, "run", tmp_path, dry_run=False, initial_payload=dict(first),
                                        initial_notes="reword line 2")
    assert revised["guardrail_passed"] and revised["narration_script"] == "One. Three."
    assert revised["caption"].count(beat_images.CREDIT_PREFIX) == 1 and revised["caption"] == first["caption"]


def test_the_caption_length_guard_still_refuses_a_caption_too_long_to_credit(tmp_path, monkeypatch):
    c = _coordinator(tmp_path, monkeypatch, Engine("Too long. " * 200 + "\n\n#history"))
    with pytest.raises(RuntimeError, match="2,200"):
        c._write_and_check_script({"id": "t", "title": "T"}, "run", tmp_path, dry_run=False)


def test_the_line_picture_rewording_gives_the_writer_the_caption_without_its_credits(world):  # noqa: F811
    credit = beat_images.CREDIT_PREFIX + " Someone (CC BY 4.0)"
    payload = {"narration_script": ROMAN, "guardrail_passed": True, "title": "Roman concrete",
               "caption": "Roman concrete heals.\n\n#history\n\n" + credit}
    out = world.check(payload)
    given = world.revisions[0]["payload"]["caption"]
    assert beat_images.CREDIT_PREFIX not in given and "Roman concrete heals." in given and "#history" in given
    assert MODERN not in out["narration_script"] and out[prevoice_lines.STAGE]["left"] == []
    assert payload["caption"].endswith(credit)                          # the verified payload itself is untouched


# ---------------------------------------------------------------- 3: the timeline is earliest first

def test_the_timeline_is_drawn_and_revealed_earliest_first(tmp_path, monkeypatch):
    drawn, per_frame = [], []
    # Since the overhaul (2026-10-29) the 9:16 timeline places each year and label as a measured plate
    # (typography.TextPlate), drawn per frame once it appears; the years are set in capitals ("C. 800 BC").
    from src import typography
    real = typography.TextPlate.draw

    def draw(self, im, alpha=1.0, dy=0):
        if alpha > 0:
            drawn.append((self.xy[1], self.text))
        return real(self, im, alpha, dy)
    monkeypatch.setattr(typography.TextPlate, "draw", draw)
    real_plate = graphics.draw_plate

    def draw_plate(im, plate, alpha=1.0, dy=0):   # round 4 review 7: the timeline composites each plate's box only
        if alpha > 0:
            drawn.append((plate.xy[1], plate.text))
        return real_plate(im, plate, alpha, dy)
    monkeypatch.setattr(graphics, "draw_plate", draw_plate)

    def encode(frames, out, n, **k):
        for _ in frames:
            per_frame.append({t: y for y, t in drawn})
            drawn.clear()
        return out
    monkeypatch.setattr(graphics, "_encode", encode)
    # The script's order (production: 1963 told before 1923), and one BC date.
    events = [{"year": 1963, "approximate": False, "label": "Rediscovered during home renovation"},
              {"year": 1923, "approximate": False, "label": "Completely abandoned"},
              {"year": -800, "approximate": True, "label": "First chambers"}]
    # QF-23: at the pipeline's 9:16 size; the half-size canvas (540 x 960) held "Rediscovered during home renovation"
    # only cut at the label floor, and a timeline that cuts a label is refused now
    graphics.render_timeline(events, tmp_path / "graphic_timeline.mp4", width=1080, height=1920)
    years = ("800 BC", "1923", "1963")          # round 4: the circa mark is its own lowercase plate
    shown_at = [next(i for i, f in enumerate(per_frame) if y in f) for y in years]
    assert shown_at[0] < shown_at[1] < shown_at[2]                            # revealed earliest first
    last = per_frame[-1]
    assert last["800 BC"] < last["1923"] < last["1963"]                    # top to bottom, earliest first
