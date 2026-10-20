"""A graphic belongs on the beat that says what the graphic shows. Its terms come from its own
declared spec (lineage sidecar), matched as whole words; the old substring locks fired on "air" in
"stairs" and "era" in "several", and their vocabulary was Derinkuyu's."""
from pathlib import Path

from src import graphic_match, scene_planner as sp
from src.lineage import record_graphic


def make_graphic(tmp_path, name, inputs):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    graphic = tmp_path / name
    graphic.write_bytes(b"graphic")
    graphic.with_suffix(".jpg").write_bytes(b"poster")
    record_graphic(graphic, code, inputs)
    return graphic


def test_substrings_do_not_match(tmp_path):
    vent = make_graphic(tmp_path, "graphic_ventilation.mp4", {"builder": "ventilation", "payload": {
        "ventilation_airflow": {"title": "Air shafts", "labels": {"shaft": "AIR SHAFT"}}}})
    assert graphic_match.score(vent, "The stairs descend several levels to repair rooms.") < graphic_match.THRESHOLD
    assert graphic_match.score(vent, "Dozens of ventilation shafts carried fresh air down.") >= graphic_match.THRESHOLD


def test_graphics_lock_to_the_beat_that_says_their_content_for_any_topic(tmp_path):
    timeline = make_graphic(tmp_path, "graphic_timeline.mp4", {"builder": "timeline", "payload": {
        "timeline": [{"year": 1901, "label": "Recovered by sponge divers"}, {"year": 2005, "label": "CT scans"}]}})
    numbers = make_graphic(tmp_path, "graphic_numbers.mp4", {"builder": "numbers", "payload": {
        "stat_cards": [{"value": "At least 30", "label": "Bronze gears"}]}})
    place = make_graphic(tmp_path, "graphic_map.mp4", {"builder": "map", "payload": {},
                         "place": {"label": "Antikythera", "country": "Greece"}})
    beats = [
        {"start": 0, "end": 3, "text": "In 1901, divers off Antikythera pulled up a lump of bronze."},  # hook: never a graphic
        {"start": 3, "end": 7, "text": "The wreck lay off the Greek island of Antikythera."},
        {"start": 7, "end": 11, "text": "Inside were at least 30 bronze gears."},
        {"start": 11, "end": 15, "text": "Sponge divers had found it in 1901; scans in 2005 read its text."},
    ]
    locked = sp.lock_narrative_graphics(beats, [timeline, numbers, place])
    assert locked == {1: place, 2: numbers, 3: timeline}


def test_numbers_are_whole_numbers(tmp_path):
    numbers = make_graphic(tmp_path, "graphic_numbers.mp4", {"builder": "numbers", "payload": {
        "stat_cards": [{"value": "Up to 20,000", "label": "People sheltered"}]}})
    assert graphic_match.score(numbers, "It held 20,000 people.") >= graphic_match.THRESHOLD
    assert graphic_match.score(numbers, "It was 200 feet deep.") < graphic_match.THRESHOLD  # "200" is not "20000"


def test_unmatched_graphic_becomes_a_photo_but_unjudgeable_one_stays(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    timeline = make_graphic(tmp_path, "graphic_timeline.mp4", {"builder": "timeline", "payload": {
        "timeline": [{"year": 1923, "label": "Abandoned"}]}})
    beats = [{"start": 0, "end": 3, "text": "A hidden city lay below the village."},
             {"start": 3, "end": 6, "text": "Its farmers kept pigeons for fertiliser."}]
    assert sp.keep_if_it_matches(timeline, beats[1]["text"], photo) == photo
    unknown = tmp_path / "graphic_other.mp4"  # no declared spec: nothing to judge it by
    assert sp.keep_if_it_matches(unknown, beats[1]["text"], photo) == unknown


def test_defense_and_ventilation_diagrams_need_a_declared_spec(tmp_path, monkeypatch):
    from src import graphics
    monkeypatch.setattr(graphics.geo, "locate", lambda topic: None)
    made = []
    monkeypatch.setattr(graphics, "render_rolling_stone_defense", lambda *a, **k: made.append("defense"))
    monkeypatch.setattr(graphics, "render_ventilation_airflow", lambda *a, **k: made.append("ventilation"))
    for topic_id in ("derinkuyu_underground_city", "hal_saflieni_hypogeum", "door_of_hell"):
        graphics.make_all({}, {"id": topic_id}, tmp_path)
    assert made == []


def _storyboard_setup(tmp_path, sb_texts):
    import json
    imgs = [tmp_path / f"img_{i}.jpg" for i in range(1, 4)]
    for p in imgs:
        p.write_bytes(b"data")
    sb = [{"beat": i + 1, "file": imgs[i].name, "text": t} for i, t in enumerate(sb_texts)]
    (tmp_path / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return imgs


def test_storyboard_follows_the_words_not_the_beat_numbers(tmp_path):
    imgs = _storyboard_setup(tmp_path, ["Divers found a corroded lump of bronze in 1901.",
                                        "Inside were at least thirty interlocking gears.",
                                        "It predicted eclipses decades ahead."])
    # The narration reached the planner in a different order and split differently.
    beats = [{"start": 0, "end": 3, "text": "It predicted eclipses decades ahead."},
             {"start": 3, "end": 6, "text": "Divers found a corroded lump"},
             {"start": 6, "end": 9, "text": "of bronze in 1901."},
             {"start": 9, "end": 12, "text": "Inside were at least thirty interlocking gears."}]
    scenes, _, notes = sp.plan_scenes(beats, imgs)
    assert scenes == [imgs[2], imgs[0], imgs[0], imgs[1]] and "storyboard" in notes


def test_storyboard_for_other_words_is_ignored(tmp_path):
    imgs = _storyboard_setup(tmp_path, ["A man renovating his house knocked through a wall.",
                                        "Rolling stone doors sealed each level.",
                                        "A whole city lay forgotten."])
    beats = [{"start": 0, "end": 3, "text": "Modern concrete crumbles within decades."},
             {"start": 3, "end": 6, "text": "Roman harbours still stand after two thousand years."}]
    _, _, notes = sp.plan_scenes(beats, imgs)
    assert "storyboard" not in notes
