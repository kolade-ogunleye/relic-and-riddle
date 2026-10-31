from src.lineage import record_graphic
from src.video_qa import QualityAuditor


def make_graphic(tmp_path, name, inputs):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    graphic = tmp_path / name
    graphic.write_bytes(b"graphic")
    record_graphic(graphic, code, inputs)
    return graphic


def semantic_issues(tmp_path, graphic, text, word, timestamp):
    timeline = [{"asset": str(graphic), "start": 0, "end": 8, "text": text}]
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline,
        [{"word": word, "start": timestamp, "dur": 0.3}])
    return [i for i in report["issues"] if i["type"] in ("semantic_desync", "temporal_desync")]


def test_map_uses_declared_place_and_accepts_keyword_within_cut(tmp_path):
    graphic = make_graphic(tmp_path, "graphic_map.mp4", {
        "place": {"label": "Kailasa Temple, Ellora Caves", "country": "India"}})
    assert not semantic_issues(tmp_path, graphic, "The Ellora Caves", "Ellora", 5)
    assert semantic_issues(tmp_path, graphic, "Harvesting grain", "grain", 1)


def test_number_graphic_cannot_match_an_unrelated_population(tmp_path):
    graphic = make_graphic(tmp_path, "graphic_numbers.mp4", {
        "payload": {"stat_cards": [{"value": "estimated 107 ft", "label": "Height"}]}})
    assert not semantic_issues(tmp_path, graphic, "107 feet above the courtyard", "107", 4)
    issues = semantic_issues(tmp_path, graphic, "20,000 people", "people", 4)
    assert any(i["type"] == "semantic_desync" for i in issues)


def _timing_issues(tmp_path, graphic, at_index, said_at):
    hook = tmp_path / "hook.jpg"
    timeline = [{"asset": str(hook), "start": 0.0, "end": 4.64, "text": "Kailasa Temple in the Ellora Caves of India"},
                {"asset": str(tmp_path / "photo.jpg"), "start": 4.64, "end": 9.0, "text": "Carved from one rock"},
                {"asset": str(tmp_path / "photo2.jpg"), "start": 9.0, "end": 14.0, "text": "Carved from one rock"}]
    timeline[at_index] = {**timeline[at_index], "asset": str(graphic)}
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline,
                                                      [{"word": "Ellora", "start": said_at, "dur": 0.3}])
    return [i for i in report["issues"] if i["type"] == "temporal_desync"]


def test_a_graphic_named_in_the_hook_may_open_the_next_cut(tmp_path):
    # Production 2026-10-24: the hook keeps its photo, so the planner locks the map to the next beat;
    # the audit flagged that placement 2.4 s late and the repair could never pass.
    graphic = make_graphic(tmp_path, "graphic_map.mp4", {
        "place": {"label": "Kailasa Temple, Ellora Caves", "country": "India"}})
    assert not _timing_issues(tmp_path, graphic, 1, said_at=2.28)
    # A map later on names a place already said, so it is on time (audit 2026-10-28: the locator rule); a
    # non-locator graphic well after its words is still late (tests/test_visual_gate_2026_10_28.py).
    assert not _timing_issues(tmp_path, graphic, 2, said_at=7.0)


def test_a_late_graphic_is_moved_to_its_words_without_a_second_plan(tmp_path):
    from src.scene_planner import placement_only, repair_placement
    photo = lambda n: tmp_path / f"photo_{n}.jpg"  # noqa: E731
    planned = [photo(0), tmp_path / "graphic_map.mp4", photo(2), photo(3)]
    durations = [4.6, 4.0, 5.0, 5.0]
    late = {"type": "temporal_desync", "scene_index": 1, "trigger_time": 10.2, "issue": "Beat 2: late"}
    assert repair_placement(planned, durations, [late]) == [photo(0), photo(2), tmp_path / "graphic_map.mp4", photo(3)]
    # Named in the hook: the next beat is the earliest it can go (the hook keeps its photo).
    assert repair_placement([photo(0), photo(1), tmp_path / "graphic_map.mp4"], [4.6, 4.0, 5.0],
                            [dict(late, scene_index=2, trigger_time=2.0)]) == [photo(0), tmp_path / "graphic_map.mp4", photo(1)]
    # Not clean (the target holds a graphic, or no time): the planner decides.
    assert repair_placement([photo(0), tmp_path / "graphic_map.mp4", tmp_path / "graphic_timeline.mp4"], [4.6, 4.0, 5.0],
                            [dict(late, scene_index=1, trigger_time=9.0)]) is None
    assert repair_placement(planned, durations, [dict(late, trigger_time=None)]) is None
    # Only when placement is all there is to repair.
    assert placement_only([late], "") == [late]
    assert placement_only([late, {"type": "semantic_desync", "scene_index": 2}], "") == []
    assert placement_only([late], "Beat 3: the picture shows a different site") == []


def test_a_map_counts_the_place_named_earlier_in_the_narration(tmp_path):
    # Hal Saflieni (dev, 2026-10-25): the map came second, after "Beneath the town of Paola, Malta"; the
    # check only heard its own few seconds and blocked the release.
    graphic = make_graphic(tmp_path, "graphic_map.mp4", {"place": {"label": "Hal Saflieni Hypogeum, Paola", "country": "Malta"}})
    timeline = [{"asset": str(tmp_path / "hook.jpg"), "start": 0.0, "end": 5.0, "text": "Beneath the town of Paola, Malta"},
                {"asset": str(graphic), "start": 12.0, "end": 16.0, "text": "three superimposed levels"}]
    words = [{"word": "Paola,", "start": 2.0, "dur": 0.4}, {"word": "Malta,", "start": 2.5, "dur": 0.4},
             {"word": "levels", "start": 13.0, "dur": 0.4}]
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline, words)
    assert not [i for i in report["issues"] if i["type"] == "semantic_desync"]
    # a map whose place is never named is still flagged
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline[1:], words[2:])
    assert [i for i in report["issues"] if i["type"] == "semantic_desync"]
