from types import SimpleNamespace

import pytest

from src import llm
from src.models import Poll
from src.video_composer import VideoComposer, _ass_escape
from src.visual_engine import MissingAssetsError, VisualEngine


def test_ass_escape_blocks_override_injection():
    assert _ass_escape(r"{\pos(0,0)}HI\N") == "(pos(0,0))HIN"


def test_ass_time_rounding_carries():
    assert VideoComposer._format_ass_time(59.999) == "0:01:00.00"
    assert VideoComposer._format_ass_time(3661.5) == "1:01:01.50"


def test_subtitles_never_overlap(tmp_path):
    words = [{"word": w, "start": i * 0.3, "dur": 0.35} for i, w in enumerate("one two three four five".split())]
    path = VideoComposer().build_subtitles(words, tmp_path / "c.ass")
    events = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]
    spans = [tuple(e.split(",")[1:3]) for e in events]
    assert len(events) == 5
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_topic_without_own_assets_fails_fast(tmp_path):
    with pytest.raises(MissingAssetsError):
        VisualEngine(assets_dir=tmp_path).get_scene_sequence({"id": "02_roman_self_healing_concrete"})


def test_derinkuyu_uses_shared_suite():
    scenes = VisualEngine().get_scene_sequence({"id": "01_derinkuyu_underground_city"})
    assert len(scenes) >= 5


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)

    parse = create


def _text(text, urls=()):
    cites = [SimpleNamespace(url=u, title=u) for u in urls]
    return SimpleNamespace(type="text", text=text, citations=cites)


def _resp(stop, parsed=None, content=None):
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed, content=content or [_text("x")], model="m",
                           _request_id="req", stop_details=SimpleNamespace(category="cyber"))


def _patch_client(monkeypatch, responses):
    fake = _FakeMessages(responses)
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    return fake


def test_research_resumes_after_pause_turn_and_collects_api_citations(monkeypatch):
    final = [_text("Fact A. ", ["https://a.org"]), _text("Fact B.", ["https://b.org", "https://a.org"])]
    fake = _patch_client(monkeypatch, [_resp("pause_turn", content=["partial"]), _resp("end_turn", content=final)])
    text, sources = llm.research("s", "p", web_search_uses=3)
    assert text == "Fact A. Fact B."
    assert [s.url for s in sources] == ["https://a.org", "https://b.org"]
    assert fake.calls[1]["messages"][-1] == {"role": "assistant", "content": ["partial"]}
    assert fake.calls[0]["fallbacks"] == "default"
    assert fake.calls[0]["tools"][0]["type"] == "web_search_20260209"


def test_structured_call_uses_no_tools(monkeypatch):
    out = Poll(question="q", options=["a", "b"])
    fake = _patch_client(monkeypatch, [_resp("end_turn", out)])
    assert llm.structured_call("s", "p", Poll) == out
    assert "tools" not in fake.calls[0] and fake.calls[0]["output_format"] is Poll


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_unusable_stop_reasons_raise(monkeypatch, stop):
    _patch_client(monkeypatch, [_resp(stop), _resp(stop)])
    with pytest.raises(llm.LLMError):
        llm.structured_call("s", "p", Poll)
    with pytest.raises(llm.LLMError):
        llm.research("s", "p", web_search_uses=1)


def test_generated_script_sources_are_limited_to_retrieved(monkeypatch, curated_payload):
    from src import script_engine
    from src.models import ScriptPackage, Source

    pkg = ScriptPackage.model_validate(curated_payload)
    pkg.sources = [Source(title="real", url="https://real.org"), Source(title="fake", url="https://made-up.org")]
    monkeypatch.setattr(script_engine, "research", lambda *a, **k: ("notes", [Source(title="real", url="https://real.org")]))
    monkeypatch.setattr(script_engine, "structured_call", lambda *a, **k: pkg)
    out = script_engine.ScriptEngine()._generate_with_claude({"id": "t", "title": "T"}, "")
    assert [s.url for s in out.sources] == ["https://real.org"]
