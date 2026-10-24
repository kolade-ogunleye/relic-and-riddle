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
    # Anywhere else, a graphic well after its words is still late.
    assert _timing_issues(tmp_path, graphic, 2, said_at=7.0)
