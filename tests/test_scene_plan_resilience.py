"""A failed picture plan is retried, then degrades to captions visibly: never silently to filename order."""
from pathlib import Path

import pytest
import requests
from PIL import Image

import main
from src import config, gemini, improve, scene_planner
from src.llm import LLMError
from src.models import ScenePlan


@pytest.fixture
def photos(tmp_path, monkeypatch):
    names = ["01.jpg", "02.jpg", "03.jpg"]
    for n in names:
        Image.new("RGB", (8, 8)).save(tmp_path / n)
    captions = {"01.jpg": "Richat from orbit", "02.jpg": "Acheulean hand axe tools", "03.jpg": "stone burial mound"}
    monkeypatch.setattr(scene_planner, "_photo_caption", lambda p: captions[Path(p).name])
    monkeypatch.setattr(scene_planner.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(scene_planner.free_llm, "available", lambda: True)
    return [tmp_path / n for n in names]


BEATS = [{"text": "The Richat Structure.", "start": 0, "end": 3},
         {"text": "Burial mound builders left cairns.", "start": 3, "end": 6},
         {"text": "Acheulean tools lie on the ground.", "start": 6, "end": 9}]


def test_a_failed_plan_is_tried_again_within_the_run(photos, monkeypatch):
    calls = []

    def call(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise LLMError("Read timed out")
        return ScenePlan(images=[1, 2, 3], notes="planned")

    monkeypatch.setattr(scene_planner.free_llm, "call", call)
    monkeypatch.setattr(config, "SCENE_PLAN_ATTEMPTS", 3)
    _, _, notes = scene_planner.plan_scenes(BEATS, photos, use_storyboard=False)
    assert len(calls) == 3 and notes == "planned"
    assert scene_planner.degraded_note(notes) == "" and improve._buffer == []


def test_when_every_try_fails_pictures_follow_captions_and_say_so(photos, monkeypatch):
    monkeypatch.setattr(scene_planner.free_llm, "call", lambda *a, **k: (_ for _ in ()).throw(LLMError("quota")))
    chosen, _, notes = scene_planner.plan_scenes(BEATS, photos, use_storyboard=False)
    assert notes == scene_planner.CAPTION_NOTES and [p.name for p in chosen] == ["01.jpg", "03.jpg", "02.jpg"]
    assert "archive captions" in scene_planner.degraded_note(notes)
    assert [n["klass"] for n in improve._buffer] == ["pictures.plan_degraded"]  # the audit ledger sees it


def test_a_vision_request_gets_time_for_its_pictures_and_a_dropped_line_is_retryable(monkeypatch):
    assert gemini.vision_timeout(2) == 120 and gemini.vision_timeout(21) == 312 and gemini.vision_timeout(99) == 600
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen["timeout"] = timeout
        raise requests.ConnectionError("Remote end closed connection without response")

    monkeypatch.setattr(gemini.requests, "post", post)
    image = {"type": "image", "source": {"media_type": "image/jpeg", "data": "AA=="}}
    with pytest.raises(gemini.ConnectionDropped):
        gemini.structured("s", [image] * 21 + [{"type": "text", "text": "t"}], ScenePlan, model="m", key="k")
    assert seen["timeout"] == 312


def test_the_documentary_audit_notes_where_it_falls_short(tmp_path):
    still, graphic = tmp_path / "01.jpg", tmp_path / "graphic_map.mp4"
    main.PipelineCoordinator._audit_documentary(196.0, 5, [still, graphic, still], [20.0, 6.0, 9.0])
    assert {n["klass"] for n in improve._buffer} == {"documentary.length", "documentary.graphics",
                                                     "documentary.static_stills"}
