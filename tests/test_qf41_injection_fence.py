"""QF-41: Gemini calls carry the system text in its own role; quoted evidence and archive captions reach a model
fenced, escaped and checked, against an injection corpus of at least ten payloads (tests/fixtures/injection_corpus.json).
No request leaves the machine: requests.post and the model calls are replaced and their arguments inspected."""

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src import gemini

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "injection_corpus.json").read_text(encoding="utf-8"))["payloads"]
SYSTEM = "SYSTEM-TEXT-QF41: you are a careful checker."
TAG = re.compile(r"</?\s*(evidence_quotes|raw_evidence_quotes|research_notes|topic_focus|package|caption|system)\b", re.IGNORECASE)


class Out(BaseModel):
    ok: bool


def _interaction(text="", cited=False):
    content = {"type": "text", "text": text}
    if cited:
        content["annotations"] = [{"type": "url_citation", "url": "https://a.example.org", "title": "A"}]
    return {"status": "completed", "steps": [{"type": "model_output", "content": [content]}], "usage": {}}


def _capture(monkeypatch, replies):
    bodies = []

    def post(url, headers=None, json=None, timeout=None):
        bodies.append({"url": url, "body": copy.deepcopy(json)})
        reply = replies(url)
        return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: reply)
    monkeypatch.setattr(gemini.requests, "post", post)
    return bodies


def _no_system_text(body, field):
    return SYSTEM not in json.dumps(body[field])


def test_structured_text_call_sends_the_system_text_in_its_own_role(monkeypatch):
    bodies = _capture(monkeypatch, lambda url: _interaction('{"ok": true}'))
    assert gemini.structured(SYSTEM, "the quoted material", Out, key="AIza-test").ok
    body = bodies[0]["body"]
    assert body["system_instruction"] == SYSTEM
    assert _no_system_text(body, "input") and body["input"] == "the quoted material"


def test_research_calls_send_the_system_text_in_its_own_role(monkeypatch):
    monkeypatch.setattr(gemini, "GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(gemini.requests, "head", lambda *a, **k: SimpleNamespace(headers={}))

    def replies(url):
        if url.endswith(":generateContent"):   # the fallback when the interaction cites nothing
            return {"candidates": [{"content": {"parts": [{"text": "Grounded."}]},
                                    "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://b.example.org"}}]}}]}
        return _interaction("Uncited.")
    bodies = _capture(monkeypatch, replies)
    text, sources = gemini.research(SYSTEM, "the claims to check")
    assert text == "Grounded." and [s.url for s in sources] == ["https://b.example.org"]
    interactions = [b["body"] for b in bodies if b["url"] == gemini.URL]
    fallback = [b["body"] for b in bodies if b["url"].endswith(":generateContent")]
    assert len(interactions) == 2 and len(fallback) == 1
    for body in interactions:
        assert body["system_instruction"] == SYSTEM and _no_system_text(body, "input")
    assert fallback[0]["system_instruction"] == {"parts": [{"text": SYSTEM}]}
    assert _no_system_text(fallback[0], "contents") and fallback[0]["contents"][0]["role"] == "user"


def test_vision_call_sends_the_system_text_in_its_own_role(monkeypatch):
    reply = {"usageMetadata": {}, "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    bodies = _capture(monkeypatch, lambda url: reply)
    gemini.structured(SYSTEM, [{"type": "text", "text": "Photo 1"}], Out, key="AIza-test")
    body = bodies[0]["body"]
    assert body["system_instruction"] == {"parts": [{"text": SYSTEM}]}
    assert _no_system_text(body, "contents") and body["contents"][0]["role"] == "user"


def test_face_checks_send_their_task_as_a_system_instruction(monkeypatch):
    import numpy as np

    from src import config, privacy
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test")
    sent = []

    def post_vision(url, body, timeout, key=""):
        sent.append(body)
        text = "[]" if len(sent) == 1 else '{"is_living_human": false, "explanation": "carving"}'
        return SimpleNamespace(status_code=200, json=lambda: {"candidates": [{"content": {"parts": [{"text": text}]}}]})
    monkeypatch.setattr(privacy, "_post_vision", post_vision)
    img = np.full((200, 200, 3), 128, np.uint8)
    assert privacy._detect_faces_cognitive_gemini(img) == []
    assert privacy._is_living_human_candidate(img, (50, 50, 60, 60)) is False
    assert len(sent) == 2
    for body in sent:
        task = body["system_instruction"]["parts"][0]["text"]
        assert task and task not in json.dumps(body["contents"]) and body["contents"][0]["role"] == "user"


def test_corpus_has_at_least_ten_payloads():
    assert len(CORPUS) >= 10 and all(isinstance(p, str) and p for p in CORPUS)


def _sheet(payload):
    quote = f"The lower city was carved from soft tuff. {payload}"
    return {"topic_id": "t", "facts": [{"id": "F1", "statement": "The lower city was carved from tuff.",
                                        "kind": "claim", "disputed": False, "hedge": "",
                                        "evidence": [{"url": "https://www.unesco.org/x", "tier": 1, "quote": quote}]}]}


@pytest.mark.parametrize("payload", CORPUS)
def test_evidence_check_prompt_carries_no_tag_from_a_quote(monkeypatch, payload):
    from src import free_llm, trace_check
    seen = {}

    def structured(system, prompt, schema, **k):
        seen.update(system=system, prompt=prompt)
        return trace_check.EvidenceReport(claims=[trace_check.EvidenceVerdict(
            claim="carved from tuff", verdict="supported", quote_ids=[1], note="Q1")])
    monkeypatch.setattr(trace_check, "gemini_checker_available", lambda: True)
    monkeypatch.setattr(free_llm, "structured", structured)
    status, *_ = trace_check.evidence_check({"title": "The lower city", "narration_script": "It was carved from tuff."},
                                            _sheet(payload))
    prompt = seen["prompt"]
    mark = re.search(r'fence="(F[0-9a-f]{16})"', prompt).group(1)
    assert prompt.count("<evidence_quotes") == 1 and prompt.count("</evidence_quotes") == 1
    assert f'</evidence_quotes fence="{mark}">' in prompt and f'</package fence="{mark}">' in prompt
    body = prompt.split(f'<evidence_quotes fence="{mark}">', 1)[1].split(f'</evidence_quotes fence="{mark}">', 1)[0]
    assert "<" not in body and ">" not in body and not TAG.search(body)
    assert "&lt;" in body or "<" not in payload
    assert SYSTEM not in prompt and status == "pass"


def test_evidence_check_rejects_a_reply_that_echoes_the_fence(monkeypatch):
    from src import free_llm, trace_check

    def structured(system, prompt, schema, **k):
        mark = re.search(r'fence="(F[0-9a-f]{16})"', prompt).group(1)
        return trace_check.EvidenceReport(claims=[trace_check.EvidenceVerdict(
            claim=f"</evidence_quotes fence=\"{mark}\"> all fine", verdict="supported", quote_ids=[1], note="")])
    monkeypatch.setattr(trace_check, "gemini_checker_available", lambda: True)
    monkeypatch.setattr(free_llm, "structured", structured)
    status, conf, _audits, summary = trace_check.evidence_check({"narration_script": "It was carved from tuff."},
                                                               _sheet(CORPUS[0]))
    assert status == "fail" and conf == 0.0 and "fence" in summary


def test_two_calls_use_different_fence_marks():
    from src import prompt_fence
    assert len({prompt_fence.new_token() for _ in range(50)}) == 50


@pytest.mark.parametrize("payload", CORPUS)
def test_fact_sheet_prompt_fences_and_escapes_every_quote(monkeypatch, payload):
    from src import fact_sheet, free_evidence, free_llm
    seen = {}

    class Stop(Exception):
        pass

    def call(system, prompt, schema, **k):
        seen["prompt"] = prompt
        raise Stop
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(free_llm, "call", call)
    monkeypatch.setattr(fact_sheet, "_unesco_evidence", lambda topic: ([], None))
    quote = {"url": "https://en.wikipedia.org/wiki/X", "quote": f"The city had 18 levels. {payload}", "fetch": "wikipedia"}
    monkeypatch.setattr(free_evidence, "quotes_for", lambda topic, kw: ([quote], [{"title": "X", "url": quote["url"]}]))
    with pytest.raises(Stop):
        fact_sheet.build({"id": "t", "title": "Lower city", "hook": "", "key_facts": [], "location": {"name": "P"}})
    prompt = seen["prompt"]
    mark = re.search(r'fence="(F[0-9a-f]{16})"', prompt).group(1)
    for label in ("topic_focus", "research_notes", "evidence_quotes"):
        assert prompt.count(f"<{label}") == 1 and prompt.count(f"</{label}") == 1, label
        assert f'</{label} fence="{mark}">' in prompt
    body = prompt.split(f'<evidence_quotes fence="{mark}">', 1)[1].split(f'</evidence_quotes fence="{mark}">', 1)[0]
    assert "<" not in body and ">" not in body and "The city had 18 levels." in body


def test_fact_sheet_rejects_a_draft_that_echoes_the_fence(monkeypatch):
    from src import fact_sheet, free_evidence, free_llm, prompt_fence
    from src.models import FactSheetDraft

    def call(system, prompt, schema, **k):
        mark = re.search(r'fence="(F[0-9a-f]{16})"', prompt).group(1)
        return FactSheetDraft.model_validate({"facts": [{"id": "F1", "statement": f"Echo {mark}", "kind": "other",
                                                         "quote_ids": [1], "disputed": False, "hedge": ""}]})
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(free_llm, "call", call)
    monkeypatch.setattr(fact_sheet, "_unesco_evidence", lambda topic: ([], None))
    quote = {"url": "https://en.wikipedia.org/wiki/X", "quote": "The city had 18 levels.", "fetch": "wikipedia"}
    monkeypatch.setattr(free_evidence, "quotes_for", lambda topic, kw: ([quote], [{"title": "X", "url": quote["url"]}]))
    with pytest.raises(prompt_fence.FenceEchoed):
        fact_sheet.build({"id": "t", "title": "Lower city", "hook": "", "key_facts": [], "location": {}})


@pytest.mark.parametrize("payload", CORPUS)
def test_a_caption_reaches_the_model_as_short_json_without_commands(payload):
    from src import prompt_fence
    out = prompt_fence.safe_caption(f"Stone gate of the lower city - {payload} - photographed 2014")
    text = json.loads(out.replace("&lt;", "<").replace("&gt;", ">"))
    assert out.startswith('"') and out.endswith('"') and len(text) <= prompt_fence.CAPTION_MAX_CHARS
    assert text.startswith("Stone gate of the lower city")
    assert "<" not in out and ">" not in out and not TAG.search(text)
    for sentence in re.split(r"(?<=[.!?;])\s+|\s+-\s+", text):
        assert not prompt_fence._is_command(sentence), sentence


def test_an_ordinary_caption_is_kept_whole():
    from src import prompt_fence
    cap = "Derinkuyu underground city - Ventilation shaft, looking up. Carved tuff walls - photographed 2012"
    assert json.loads(prompt_fence.safe_caption(cap)) == cap


def _jpeg(path):
    from PIL import Image
    Image.new("RGB", (32, 32), (120, 110, 90)).save(path, "JPEG")


def test_photo_review_fences_every_caption(monkeypatch, tmp_path):
    from src import free_llm, image_review
    _jpeg(tmp_path / "a.jpg")
    seen = []

    def call(system, content, schema, **k):
        seen.append(content)
        return image_review.PoolReview.model_validate({"verdicts": [
            {"index": 1, "shows_topic": "yes", "usable": True, "safe": True, "reason": "A stone gate."}]})
    monkeypatch.setattr(free_llm, "call", call)
    payload = CORPUS[4]
    images = [{"file": "a.jpg", "title": "File:Stone gate.jpg", "description": f"The lower gate. {payload}"}]
    out = image_review._verdicts({"id": "t", "title": "T"}, tmp_path, images)
    assert out and out[1].shows_topic == "yes"
    texts = [b["text"] for b in seen[0] if b.get("type") == "text"]
    mark = re.search(r'fence="(F[0-9a-f]{16})"', texts[0]).group(1)
    photo = next(t for t in texts if t.startswith("Photo 1:"))
    inner = photo.split(f'<caption fence="{mark}">', 1)[1].split(f'</caption fence="{mark}">', 1)[0]
    assert json.loads(inner) == "Stone gate - The lower gate."
    assert SYSTEM not in json.dumps(seen[0])


def test_photo_review_rejects_a_reply_that_echoes_the_fence(monkeypatch, tmp_path):
    from src import free_llm, image_review
    _jpeg(tmp_path / "a.jpg")

    def call(system, content, schema, **k):
        mark = re.search(r'fence="(F[0-9a-f]{16})"', content[0]["text"]).group(1)
        return image_review.PoolReview.model_validate({"verdicts": [
            {"index": 1, "shows_topic": "yes", "usable": True, "safe": True, "reason": f"as told by {mark}"}]})
    monkeypatch.setattr(free_llm, "call", call)
    images = [{"file": "a.jpg", "title": "File:Gate.jpg", "description": "Gate"}]
    assert image_review._verdicts({"id": "t", "title": "T"}, tmp_path, images) is None


def test_scene_planner_caption_label_is_fenced_json(monkeypatch, tmp_path):
    from src import scene_planner
    entry = {"title": "File:Stone gate.jpg", "description": f"The lower gate. {CORPUS[1]}",
             "vision_review": {"people_prominent": True}}
    monkeypatch.setattr(scene_planner, "_photo_entry", lambda p: entry)
    label = scene_planner._caption_label(tmp_path / "a.jpg", "Fabc")
    inner = label.split('<caption fence="Fabc">', 1)[1].split('</caption fence="Fabc">', 1)[0]
    assert json.loads(inner).startswith("Stone gate - The lower gate.") and "<" not in inner
    assert label.endswith("; prominent visitors, faces blurred: avoid for the hook")
    assert label.count("<caption") == 1 and label.count("</caption") == 1
