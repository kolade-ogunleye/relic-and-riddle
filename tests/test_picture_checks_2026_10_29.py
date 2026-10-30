"""
Picture checks, run audit 2026-10-29 (docs/reviews/run-audit-2026-10-29/README.md items 2, 3 and 11):
  2. picture-text verdicts were cached without a box or an edge, and degenerate answers ("Well alright, let's wrap
     this up...", "Done well done perfectly done...", prompt echoes) were taken as looks;
  3. a widened Commons search returned the whole category, and each of its files was looked at on the topic's
     name alone; one failed billed look cost about 250 s;
 11. every light call hit the free 429 first, and a reply with no output was logged as a schema mismatch.
Stubs only: no network, no spend.
"""

import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from src import beat_images, commons, config, free_llm, gemini
from src.llm import current_task, degenerate_reason, verdict_reason
from src.models import BeatQueries, BeatQuery

PV = beat_images.PictureVerdict


@pytest.fixture(autouse=True)
def looks_on(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "PICTURE_CHECK_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(free_llm, "_free_down_until", {})


def _jpg(path: Path, size=(60, 40), shade=0) -> Path:
    Image.new("RGB", size, (90 + shade, 80, 70)).save(path, "JPEG")
    return path


def _answers(monkeypatch, *verdicts):
    """free_llm.structured answering `verdicts` in turn (the last again); the calls' keyword arguments recorded."""
    calls = []

    def answer(system, content, schema, **kw):
        calls.append({**kw, "task": current_task()})
        return verdicts[min(len(calls), len(verdicts)) - 1]
    monkeypatch.setattr(free_llm, "structured", answer)
    return calls


# ------------------------------------------------------------------ A. the verdict and its schema
def test_every_field_is_required_and_the_edges_are_enums():
    schema = PV.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])          # defaults let the model leave edge/box out
    assert schema["properties"]["text_edge"]["enum"] == ["none", "top", "bottom", "left", "right", "inside"]
    assert schema["properties"]["object_edge"]["enum"] == ["none", "top", "bottom", "left", "right"]
    assert "25 words" in schema["properties"]["reason"]["description"]
    assert PV(shows_subject=True, reason="ok").text_edge == "none"      # older verdicts and stubs still read


def test_text_with_neither_box_nor_edge_is_asked_again(monkeypatch, tmp_path):
    p = _jpg(tmp_path / "05_commons.jpg")
    calls = _answers(monkeypatch, PV(shows_subject=True, burned_in_text=True, reason="A rolling stone door."),
                     PV(shows_subject=True, burned_in_text=True, reason="A rolling stone door.",
                        text_reason="a museum label", text_edge="bottom", text_box=[900, 100, 960, 400]))
    look = beat_images.picture_look(p, "rolling stone door")
    assert len(calls) == 2 and "invalid" not in look
    assert look["text_edge"] == "bottom" and look["text_box"] == [900, 100, 960, 400]


@pytest.mark.parametrize("reason", [
    "Well alright, let's wrap this up and answer.",
    "Done well done perfectly done.",
    "Wait, make sure no control tokens or formatting errors inside JSON.",
    "The stone door " + "is carved from the rock " * 4,
    "x" * 320,
    "text_edge is bottom and text_box is given.",
    # the question repeated: eight words in a row of the system prompt
    "An inscription that is part of the subject (carved on a monument, written on a manuscript page) is not printed.",
])
def test_a_degenerate_reason_is_asked_again_and_never_kept(monkeypatch, tmp_path, reason):
    p = _jpg(tmp_path / "11_commons.jpg")
    calls = _answers(monkeypatch, PV(shows_subject=True, reason=reason))
    look = beat_images.picture_look(p, "rolling stone door")
    assert len(calls) == 2 and look["checked"] and look["invalid"]        # asked once more, then used, flagged
    assert len(look["reason"]) <= 301 and "control tokens" not in look["reason"]


def test_ordinary_reasons_stand():
    for fine in ("The photo shows a carved stone door in a tunnel.", "OK, the photo shows a Roman bridge.",
                 "It does not show what the prompt describes.", "I think it shows the harbour core."):
        assert degenerate_reason(fine, beat_images.PICTURE_SYSTEM) == ""
        assert verdict_reason(fine) == fine


def test_verdict_reason_drops_wind_ups_repeats_and_run_ons():
    assert verdict_reason("A door. Well alright, let's wrap this up.") == "A door."
    assert verdict_reason("Done well done perfectly done.") == ""
    assert verdict_reason("A shaft. A shaft. A door.") == "A shaft. A door."
    looped = verdict_reason("Logo in the corner " + "watermark logo text " * 50)
    assert looped.startswith("Logo in the corner watermark logo text") and looped.endswith("…") and len(looped) < 80
    assert len(verdict_reason("word " * 200)) <= 301


def test_the_look_asks_at_a_low_thinking_level_with_a_short_timeout_and_its_own_task_name(monkeypatch, tmp_path):
    p = _jpg(tmp_path / "x.jpg")
    calls = _answers(monkeypatch, PV(shows_subject=True, reason="A tunnel."))
    beat_images.picture_look(p, "tunnel")
    assert calls == [{"role": "checker", "task_class": "light", "thinking": "low",
                      "request_timeout": beat_images.PICTURE_LOOK_TIMEOUT, "task": "picture_check"}]
    assert beat_images.PICTURE_LOOK_TIMEOUT <= 45


def test_a_failure_after_two_billed_tries_is_not_asked_again(monkeypatch, tmp_path):
    p = _jpg(tmp_path / "x.jpg")
    calls = []

    def failed(*a, **k):
        calls.append(1)
        raise free_llm.PaidFallbackFailed("The paid fallback also failed: timed out")
    monkeypatch.setattr(free_llm, "structured", failed)
    assert beat_images.picture_look(p, "q")["reason"] == beat_images.UNAVAILABLE and calls == [1]


# ------------------------------------------------------------------ A. the screen's cache
def test_an_answer_that_cannot_stand_is_never_cached(monkeypatch, tmp_path):
    p = _jpg(tmp_path / "05_commons.jpg")
    _answers(monkeypatch, PV(shows_subject=True, burned_in_text=True, reason="Done well done perfectly done."))
    beat_images.without_burned_in_text([p], "Derinkuyu", minimum=0)
    cache = tmp_path / beat_images.TEXT_SCREEN_FILE
    assert not cache.exists() or json.loads(cache.read_text(encoding="utf-8")) == {}


def test_a_cached_verdict_with_text_but_no_box_or_edge_is_looked_at_again(monkeypatch, tmp_path):
    from src.credits import sha256_file
    door, shaft = _jpg(tmp_path / "05_commons.jpg"), _jpg(tmp_path / "06_commons.jpg", shade=40)
    cache = tmp_path / beat_images.TEXT_SCREEN_FILE
    cache.write_text(json.dumps({   # Derinkuyu's rolling-stone-door photo, as production cached it (screen 3)
        sha256_file(door): {"text": True, "reason": "Wait, make sure no control tokens.", "edge": "none", "box": [],
                            "screen": 3},
        sha256_file(shaft): {"text": False, "reason": "A ventilation shaft.", "edge": "none", "box": [], "screen": 3}}))
    calls = _answers(monkeypatch, PV(shows_subject=True, reason="A rolling stone door in a tunnel."))
    kept = beat_images.without_burned_in_text([door, shaft], "Derinkuyu", minimum=0)
    assert kept == [door, shaft] and len(calls) == 1                     # only the door looked at again
    saved = json.loads(cache.read_text(encoding="utf-8"))[sha256_file(door)]
    assert saved["text"] is False and saved["answer_check"] == beat_images.ANSWER_CHECK
    calls.clear()
    beat_images.without_burned_in_text([door, shaft], "Derinkuyu", minimum=0)
    assert calls == []                                                   # now cached for good


def test_a_cached_run_on_reason_is_logged_short(monkeypatch, tmp_path, caplog):
    from src.credits import sha256_file
    p = _jpg(tmp_path / "03_commons.jpg")
    run_on = "Copernicus logo in the lower left corner " + "watermark logo text footer " * 300
    (tmp_path / beat_images.TEXT_SCREEN_FILE).write_text(json.dumps({sha256_file(p): {
        "text": True, "reason": run_on, "edge": "inside", "box": [], "screen": 3, "answer_check": 1}}))
    monkeypatch.setattr(free_llm, "structured", lambda *a, **k: pytest.fail("a trusted verdict is not looked at"))
    with caplog.at_level(logging.INFO, logger="src.beat_images"):
        beat_images.without_burned_in_text([p], "Richat", minimum=0)
    line = next(r.getMessage() for r in caplog.records if "03_commons.jpg: burned-in text" in r.getMessage())
    assert len(line) < 400 and "Copernicus logo" in line


def test_text_at_an_edge_without_a_box_is_cropped_in_growing_bands(tmp_path):
    p = _jpg(tmp_path / "14_commons.jpg", size=(400, 600))
    bands = []

    def look_again(crop):
        with Image.open(crop) as im:
            bands.append(round(1 - im.size[1] / 600, 2))
        return {"text": len(bands) < 2, "reason": "a label", "object": "", "shows": True, "subject": "a skull"}
    use, verdict, why = beat_images.clear_edges(p, {"text": True, "reason": "a label", "edge": "bottom", "box": [],
                                                    "object": ""}, look_again)
    assert bands == [0.06, 0.10] and use != p and why == "" and verdict["text"] is False


def test_text_at_an_edge_still_seen_in_the_largest_band_sets_the_photo_aside(tmp_path):
    p = _jpg(tmp_path / "14_commons.jpg", size=(400, 600))
    use, _, why = beat_images.clear_edges(p, {"text": True, "reason": "a label", "edge": "top", "box": [], "object": ""},
                                          lambda crop: {"text": True, "reason": "a label", "object": ""})
    assert use is None and why == beat_images.TEXT_PREFIX + "a label"


# ------------------------------------------------------------------ B. the search
DERINKUYU = {"id": "derinkuyu_underground_city", "title": "Derinkuyu Underground City",
             "location": {"name": "Derinkuyu", "country": "Turkey"}}
CATEGORY = 'incategory:"Underground City of Derinkuyu"'


def test_a_widened_form_naming_only_the_topic_is_the_whole_category():
    names = beat_images._topic_names(DERINKUYU)
    assert commons.anchor_only(CATEGORY, names) and commons.anchor_only(f"Derinkuyu {CATEGORY}", names)
    assert not commons.anchor_only(f"church {CATEGORY}", names)
    assert commons.beat_words("Derinkuyu church exterior", ["derinkuyu", "church"], names + [CATEGORY]) == [
        "church", "exterior"]
    assert commons.mentions({"title": "File:Churches of Cappadocia.jpg"}, ["church"])
    assert not commons.mentions({"title": "File:Derinkuyu tunnel 3.jpg", "categories": ["Underground City of Derinkuyu"]},
                                ["church", "exterior"])


def test_search_widened_says_which_form_found_each_file(monkeypatch):
    found = {"Derinkuyu church exterior": [{"pageid": 1, "title": "File:Derinkuyu church.jpg"}],
             CATEGORY: [{"pageid": 2, "title": "File:Derinkuyu tunnel.jpg"}]}
    monkeypatch.setattr(commons, "search", lambda q, limit=50, max_wait=60: found.get(q, []))
    pages, _ = commons.search_widened("Derinkuyu church exterior", {"kind": "category", "name": "Underground City of "
                                      "Derinkuyu"}, ["church"], max_extra=1)
    assert [p.get("widened") for p in pages] == [None, CATEGORY]


def _cand(title, widened=""):
    return {"title": f"File:{title}.jpg", "download_url": "u", "source_url": "s", "relevance": 3.0,
            "categories": ["Underground City of Derinkuyu"], **({"widened": widened} if widened else {})}


def test_files_from_the_whole_category_must_name_the_beats_subject_before_a_look(monkeypatch, tmp_path):
    monkeypatch.setattr(commons, "search_widened", lambda *a, **k: (["r"], [("q", 1)]))
    monkeypatch.setattr(commons, "candidates", lambda pages, kw, sa: iter([
        _cand("Derinkuyu tunnel 1", CATEGORY), _cand("Derinkuyu tunnel 2", CATEGORY),
        _cand("Derinkuyu church of St Theodore", CATEGORY), _cand("Derinkuyu stairs")]))
    monkeypatch.setattr(commons, "download_jpeg", lambda url, dest, max_wait=30: _jpg(dest))
    looked = []
    monkeypatch.setattr(beat_images, "screened_picture", lambda path, q: looked.append(path.name) or (True, "ok", path))
    items = [BeatQuery(beat=10, query="Derinkuyu church exterior", keywords=["derinkuyu", "church"])]
    paths, credits = beat_images._download(items, DERINKUYU, tmp_path, set(), "beat")
    assert [c["title"] for c in credits] == ["File:Derinkuyu church of St Theodore.jpg", "File:Derinkuyu stairs.jpg"]
    assert len(looked) == 2 and all("widened" not in c for c in credits)   # the precise query's file is looked at


def test_a_beat_stops_after_four_photos_in_a_row_are_set_aside(monkeypatch, tmp_path):
    monkeypatch.setattr(commons, "search_widened", lambda *a, **k: (["r"], [("q", 1)]))
    monkeypatch.setattr(commons, "candidates", lambda pages, kw, sa: iter([_cand(f"Derinkuyu {i}") for i in range(20)]))
    monkeypatch.setattr(commons, "download_jpeg", lambda url, dest, max_wait=30: _jpg(dest))
    looked = []
    monkeypatch.setattr(beat_images, "screened_picture",
                        lambda path, q: looked.append(path.name) or (False, "an underground room", path))
    beat_images._download([BeatQuery(beat=10, query="church exterior", keywords=["derinkuyu"])], DERINKUYU, tmp_path,
                          set(), "beat")
    assert len(looked) == beat_images.SET_ASIDE_IN_A_ROW == 4


# ------------------------------------------------------------------ B/C. the provider
@pytest.fixture
def keys(monkeypatch, tmp_path):
    from src.db import PipelineDatabase
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key-1029")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "paid-key-1029")
    monkeypatch.setattr(config, "PAID_FALLBACK_LIGHT_MONTHLY_USD", 5.0)
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 5.0)
    db = PipelineDatabase(db_path=tmp_path / "t.db", topic_bank_file=tmp_path / "none.json")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: db)
    monkeypatch.setattr(free_llm.time, "sleep", lambda s: None)
    calls = []

    def fake(system, prompt, schema, model=None, key=None, **kw):
        calls.append((key, kw.get("timeout")))
        if key == "free-key-1029":
            raise gemini.QuotaExhausted("Gemini quota exceeded (HTTP 429).")
        return "ok"
    monkeypatch.setattr(gemini, "structured", fake)
    return calls


def test_after_a_free_429_light_calls_go_straight_to_the_billed_key_for_a_while(keys, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(free_llm.time, "monotonic", lambda: clock[0])
    assert free_llm.structured("s", "p", object, task_class="light") == "ok"
    assert [k for k, _ in keys] == ["free-key-1029", "paid-key-1029"]
    keys.clear()
    free_llm.structured("s", "p", object, task_class="light")
    assert [k for k, _ in keys] == ["paid-key-1029"]                     # the free key is not asked again yet
    keys.clear()
    free_llm.structured("s", "p", object, task_class="heavy")
    assert [k for k, _ in keys] == ["free-key-1029", "paid-key-1029"]   # a heavy call still asks the free key first
    keys.clear()
    clock[0] += free_llm.FREE_COOLDOWN_SECONDS + 1
    free_llm.structured("s", "p", object, task_class="light")
    assert [k for k, _ in keys] == ["free-key-1029", "paid-key-1029"]   # asked again once the cooldown is over


def test_no_cooldown_without_a_billed_key_to_go_to(keys, monkeypatch):
    monkeypatch.setattr(config, "PAID_FALLBACK_LIGHT_MONTHLY_USD", 0.0)
    for _ in range(2):
        with pytest.raises(free_llm.FreeQuotaUnavailable):
            free_llm.structured("s", "p", object, task_class="light")
    assert [k for k, _ in keys] == ["free-key-1029", "free-key-1029"]


def test_the_request_timeout_reaches_the_billed_key_and_its_two_tries(keys, monkeypatch):
    def slow(system, prompt, schema, model=None, key=None, **kw):
        keys.append((key, kw.get("timeout")))
        if key == "free-key-1029":
            raise gemini.QuotaExhausted("429")
        raise gemini.RequestTimedOut("Read timed out.")
    monkeypatch.setattr(gemini, "structured", slow)
    with pytest.raises(free_llm.PaidFallbackFailed):
        free_llm.structured("s", "p", object, task_class="light", request_timeout=45)
    assert keys == [("free-key-1029", 45), ("paid-key-1029", 45), ("paid-key-1029", 45)]


def test_a_reply_with_no_output_says_why(monkeypatch, caplog):
    class Reply:
        status_code, text = 200, ""

        def json(self):
            return {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}],
                    "usageMetadata": {"promptTokenCount": 900, "thoughtsTokenCount": 380}}
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: Reply())
    content = [{"type": "text", "text": "q"}, {"type": "image", "source": {"media_type": "image/jpeg", "data": "x"}}]
    with caplog.at_level(logging.INFO, logger="src.gemini"), pytest.raises(gemini.EmptyReply, match="MAX_TOKENS"):
        gemini.structured("s", content, PV, key="k")
    assert any("had no output (finishReason MAX_TOKENS, 380 thinking tokens)" in r.getMessage() for r in caplog.records)


def test_an_empty_billed_reply_is_sent_once_more(keys, monkeypatch):
    answers = iter([gemini.EmptyReply("no output"), "ok"])

    def flaky(system, prompt, schema, model=None, key=None, **kw):
        keys.append((key, None))
        if key == "free-key-1029":
            raise gemini.QuotaExhausted("429")
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(gemini, "structured", flaky)
    assert free_llm.structured("s", "p", object, task_class="light") == "ok"
    assert [k for k, _ in keys] == ["free-key-1029", "paid-key-1029", "paid-key-1029"]


def test_a_line_outside_the_topic_is_searched_by_its_own_general_words(monkeypatch, tmp_path):
    """Production, 30 Oct (Roman concrete): "modern concrete crumbles" found nothing, every wider form being in the
    topic's category. When the planner marks a line outside the topic, its broad search is asked unrestricted;
    a line about the subject itself never is."""
    asked = []
    monkeypatch.setattr(commons, "search_widened", lambda *a, **k: ([], [("q", 0)]))
    monkeypatch.setattr(commons, "search", lambda q, limit=50, max_wait=60: asked.append(q) or ["r"])
    monkeypatch.setattr(commons, "candidates", lambda pages, kw, sa: iter([_cand("Cracked concrete wall")] if pages else []))
    monkeypatch.setattr(commons, "download_jpeg", lambda url, dest, max_wait=30: _jpg(dest))
    monkeypatch.setattr(beat_images, "screened_picture", lambda path, q: (True, "ok", path))
    out = BeatQuery(beat=1, query="cracked concrete bridge spalling", keywords=["concrete"], outside_topic=True,
                    broad="cracked concrete")
    _, credits = beat_images._download([out], DERINKUYU, tmp_path, set(), "beat")
    assert asked == ["cracked concrete"] and credits
    asked.clear()
    inside = BeatQuery(beat=2, query="whispering gallery dome interior", keywords=["gallery"], broad="gallery")
    beat_images._download([inside], DERINKUYU, tmp_path / "b", set(), "beat")
    assert asked == []                                            # the subject itself stays in its category
