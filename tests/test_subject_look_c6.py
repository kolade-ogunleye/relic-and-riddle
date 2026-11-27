"""SUBJECT-VISION prototype (design freeze c6): the picture check also says where the subject is, behind
config.PICTURE_SUBJECT_LOOK (off). Off, the request and the answer are exactly as without it; on, the answer carries a
mechanically validated subject record that nothing consumes."""
import json

from PIL import Image

from src import beat_images, config, subject_look


def _photo(tmp_path):
    p = tmp_path / "x.jpg"
    Image.new("RGB", (40, 30)).save(p, "JPEG")
    return p


def _capture(monkeypatch, answer):
    sent = []

    def fake(system, content, schema, **k):
        sent.append({"system": system, "question": content[0]["text"], "schema": schema})
        return answer(schema)
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr("src.free_llm.claude_engine", lambda: False)
    monkeypatch.setattr("src.free_llm.structured", fake)
    return sent


CONTEXT = {"topic": "Colossi of Memnon", "title": "File:Colossi.jpg", "caption": "The colossi in the flood",
           "credit": "David Roberts", "claim": "The statues sang at dawn."}


def test_off_the_request_and_the_answer_are_as_without_the_prototype(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PICTURE_SUBJECT_LOOK", False)
    sent = _capture(monkeypatch, lambda schema: schema(shows_subject=True, reason="the two colossi"))
    look = beat_images.picture_look(_photo(tmp_path), "Colossi of Memnon", context=CONTEXT)
    assert sent[0]["system"] == beat_images.PICTURE_SYSTEM
    assert sent[0]["schema"] is beat_images.PictureVerdict
    assert sent[0]["question"] == "Searched for: Colossi of Memnon\nDoes this picture show it?"
    assert "subject" not in look


def test_the_switch_ships_off():
    assert config.PICTURE_SUBJECT_LOOK is False


def test_on_the_same_call_asks_where_the_subject_is_with_its_context(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PICTURE_SUBJECT_LOOK", True)
    sent = _capture(monkeypatch, lambda schema: schema(
        shows_subject=True, reason="the two colossi", subject_name="the two seated colossi", scene=False,
        subject_box=[400, 330, 610, 620], subject_confidence=0.8,
        not_subject=[{"name": "colour chart", "box": [900, 0, 1000, 300]}]))
    look = beat_images.picture_look(_photo(tmp_path), "Colossi of Memnon", context=CONTEXT)
    assert len(sent) == 1                                     # one call, not a second one
    assert sent[0]["system"].startswith(beat_images.PICTURE_SYSTEM) and "subject_box" in sent[0]["system"]
    assert "The slide says: The statues sang at dawn." in sent[0]["question"]
    assert "Photograph title: File:Colossi.jpg" in sent[0]["question"]
    schema = sent[0]["schema"].model_json_schema()
    assert {"subject_name", "scene", "subject_box", "not_subject", "subject_confidence", "shows_subject"} <= \
        set(schema["required"])
    rec = look["subject"]
    assert rec["problem"] == "" and rec["box"] == [0.33, 0.4, 0.62, 0.61]   # [x0, y0, x1, y1] in 0-1
    assert rec["not_subject"] == [{"name": "colour chart", "box": [0.0, 0.9, 0.3, 1.0]}]
    assert look["shows"] is True and look["reason"] == "the two colossi"


def test_the_line_check_never_asks_the_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PICTURE_SUBJECT_LOOK", True)
    sent = _capture(monkeypatch, lambda schema: schema(shows_subject=True, reason="the dome"))
    look = beat_images.picture_look(_photo(tmp_path), "a line", ask="Does this picture show what this line says?")
    assert sent[0]["schema"] is beat_images.PictureVerdict and "subject" not in look


def _v(**k):
    base = dict(shows_subject=True, reason="r", subject_name="the obelisk", scene=False,
                subject_box=[250, 120, 880, 1000], subject_confidence=0.7, not_subject=[])
    base.update(k)
    return subject_look.verdict_schema(beat_images.PictureVerdict)(**base)


def test_the_answer_is_validated_mechanically():
    assert subject_look.subject_record(_v())["box"] == [0.12, 0.25, 1.0, 0.88]
    cases = {
        "no subject box": _v(subject_box=[]),
        "no subject box inside the picture": _v(subject_box=[0, 0, 1200, 500]),
        "inverted": _v(subject_box=[500, 500, 400, 600]),
        "degenerate": _v(subject_box=[100, 100, 105, 600]),
        "whole frame": _v(subject_box=[0, 0, 1000, 1000]),
        "confidence": _v(subject_confidence=1.7),
        "no subject named": _v(subject_name="  "),
    }
    for why, verdict in cases.items():
        rec = subject_look.subject_record(verdict)
        assert rec["box"] == [] and rec["problem"], why
    scene = subject_look.subject_record(_v(scene=True, subject_box=[0, 0, 1000, 1000]))
    assert scene["problem"] == "" and scene["box"] == [0.0, 0.0, 1.0, 1.0]
    rec = subject_look.subject_record(_v(not_subject=[{"name": "wing", "box": [0, 0, 90, 1000]},
                                                      {"name": "bad", "box": [5, 5]}]))
    assert rec["not_subject"] == [{"name": "wing", "box": [0.0, 0.0, 1.0, 0.09]}] and rec["not_subject_dropped"] == 1


def test_context_is_clipped_and_only_present_fields_are_said():
    text = subject_look.context_text({"topic": "Nazca", "caption": "x " * 1000, "claim": ""})
    assert "Documentary topic: Nazca" in text and "The slide says" not in text
    assert len(text) < subject_look.CONTEXT_CHARS + 80
    assert subject_look.context_text(None) == ""
    json.dumps(subject_look.subject_record(_v()))             # the record is plain data
