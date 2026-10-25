"""Quality step 5 (2026-10-25): each rendered Story frame and carousel slide is looked at with its own words;
findings are informational (shown, kept, never a block) until the check is calibrated on real episodes."""
from PIL import Image

from src import config, still_vision
from src.video_qa import QualityAuditor

PAYLOAD = {
    "story_slides": [{"kicker": "DESERT ANOMALY", "line1": "ORBITAL BEACON", "line2": "VISIBLE FROM SPACE", "body": "Crews use it as a landmark."},
                     {"kicker": "RING SIZES", "line1": "WHICH IS", "line2": "WIDER?", "body": "Rings of 30 and 70 metres."}],
    "poll": {"question": "Which feature measures wider?", "options": ["Outer dike", "Inner dike"]},
    "carousel_slides": [{"heading": "The Eye of the Sahara", "body": "A dome, not a crater."}, {"heading": "Thirty miles", "body": "Across."}],
}


def test_the_words_sent_with_each_still_are_its_own():
    assert still_vision.story_copy(PAYLOAD, 0) == "DESERT ANOMALY / ORBITAL BEACON / VISIBLE FROM SPACE / Crews use it as a landmark."
    assert still_vision.story_copy(PAYLOAD, 1).endswith("Which feature measures wider? / A: Outer dike / B: Inner dike")
    assert still_vision.carousel_copy(PAYLOAD, 1) == "Thirty miles / Across." and still_vision.carousel_copy(PAYLOAD, 9) == ""


def test_switched_off_or_unavailable_the_still_is_kept(monkeypatch, tmp_path):
    frame = tmp_path / "f.jpg"
    Image.new("RGB", (108, 192), (40, 40, 40)).save(frame)
    monkeypatch.setattr(config, "STILL_VISION_CHECK", False)
    assert still_vision.look(frame, "Story frame", "x") == ([], "not checked")
    monkeypatch.setattr(config, "STILL_VISION_CHECK", True)
    monkeypatch.setattr("src.free_llm.claude_engine", lambda: False)
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    assert still_vision.look(frame, "Story frame", "x") == ([], "check unavailable")


def test_findings_reach_the_audit_as_information_not_as_a_block(monkeypatch, tmp_path):
    (tmp_path / "stories").mkdir()
    (tmp_path / "carousel").mkdir()
    from PIL import ImageDraw
    for i in (1, 2):
        for size, path in (((1080, 1920), tmp_path / "stories" / f"story_0{i}.jpg"), ((1080, 1350), tmp_path / "carousel" / f"carousel_slide_0{i}.jpg")):
            im = Image.new("RGB", size, (60, 50, 40))                        # a textured frame: the blank-frame check must not fire
            d = ImageDraw.Draw(im)
            for y in range(0, size[1], 8):
                d.line([(0, y), (size[0], y)], fill=(60 + y % 90, 50 + y % 60, 40 + y % 30))
            d.text((size[0] // 2, size[1] // 2), "RELIC", fill=(255, 255, 255))
            im.save(path)
    seen = []

    def fake_look(path, kind, copy=""):
        seen.append((path.name, kind, copy))
        return (["text_over_subject"], "The title sits across the dome.") if path.name.endswith("01.jpg") else ([], "")
    monkeypatch.setattr(still_vision, "look", fake_look)
    auditor = QualityAuditor()
    stories = auditor.audit_story_cards(tmp_path / "stories", payload=PAYLOAD)
    slides = auditor.audit_carousel_slides(tmp_path / "carousel", payload=PAYLOAD)
    looks = [i for i in stories["issues"] + slides["issues"] if i["type"] in ("story_frame_look", "carousel_slide_look")]
    assert [i["severity"] for i in looks] == ["info", "info"] and all("text over the subject" in i["issue"] for i in looks)
    assert stories["certified"] and slides["certified"] and stories["score"] == slides["score"] == 100  # information only
    assert ("story_01.jpg", "Story frame", still_vision.story_copy(PAYLOAD, 0)) in seen
    assert ("carousel_slide_02.jpg", "carousel slide", "Thirty miles / Across.") in seen
