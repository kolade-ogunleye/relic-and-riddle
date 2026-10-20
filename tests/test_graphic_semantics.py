from src.lineage import record_graphic
from src.video_qa import ClosedLoopAuditor


def make_graphic(tmp_path, name, inputs):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    graphic = tmp_path / name
    graphic.write_bytes(b"graphic")
    record_graphic(graphic, code, inputs)
    return graphic


def semantic_issues(tmp_path, graphic, text, word, timestamp):
    timeline = [{"asset": str(graphic), "start": 0, "end": 8, "text": text}]
    report = ClosedLoopAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline,
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
