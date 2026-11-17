"""
tests/test_integrity_gates.py
=============================
Checks that used to pass while silently doing nothing (false positives).
"""

import json
from types import SimpleNamespace

import pytest


def test_blocked_unlicensed_publish_is_audited(db, monkeypatch, tmp_path):
    from src import config
    from src.publisher import MetaPublisher
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "voice.json").write_text(json.dumps({"provider": "edge-tts", "licensed": False}))
    res = MetaPublisher(db=db, publishers=[]).publish_reel({"topic_id": "01_topic", "run_id": "r1", "platforms": []})
    assert res["status"] == "blocked_unlicensed"
    events = db.list_audit_logs()
    assert events and events[0]["action"] == "publish_blocked_unlicensed_voice" and events[0]["status"] == "blocked"


def test_unavailable_second_check_blocks_the_script(monkeypatch, curated_payload):
    # Fail closed: a second check that could not run is not a pass.
    from src.script_engine import ScriptEngine
    eng = ScriptEngine()
    curated_payload.update(origin="claude", human_reviewed=True)
    monkeypatch.setattr(eng.fact_checker, "verify", lambda *a, **k: (True, 100.0, [], "first ok"))
    monkeypatch.setattr(eng.fact_checker, "second_opinion", lambda p: ("unavailable", 0.0, [], "quota used up"))
    passed, payload, notes = eng.verify_claims(curated_payload)
    assert not passed and "second fact check" in notes and payload["second_check"]["status"] == "unavailable"


class _GeminiReply:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self.ok, self._body, self.text = status, status < 400, body or {}, text

    def json(self):
        return self._body


def _image_prompt():
    return [{"type": "text", "text": "Beat 1: a door."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}]


def test_gemini_vision_failure_never_falls_back_to_text(monkeypatch):
    from pydantic import BaseModel
    from src import gemini

    class Out(BaseModel):
        ok: bool
    calls = []
    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers))
        return _GeminiReply(500, text="server error")
    monkeypatch.setattr(gemini.requests, "post", post)
    with pytest.raises(gemini.GeminiError):
        gemini.structured("sys", _image_prompt(), Out, model="gemini-checker", key="AIza-secret")
    assert len(calls) == 1  # no second, text-only call that would judge frames it never saw
    url, headers = calls[0]
    assert "gemini-checker" in url  # the requested model (the checker), not a hardcoded writer model
    assert "AIza-secret" not in url and headers.get("x-goog-api-key") == "AIza-secret"  # key in a header, not the URL


def test_gemini_vision_rate_limit_is_a_quota_error(monkeypatch):
    from pydantic import BaseModel
    from src import gemini

    class Out(BaseModel):
        ok: bool
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: _GeminiReply(429, text="rate limited"))
    with pytest.raises(gemini.QuotaExhausted):
        gemini.structured("sys", _image_prompt(), Out, model="m", key="AIza-x")


def test_gemini_research_ignores_urls_the_model_typed(monkeypatch):
    from src import gemini
    monkeypatch.setattr(gemini, "GEMINI_API_KEY", "AIza-test")
    body = {"status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "text", "text": "Derinkuyu is 85 m deep (see https://made-up.example/derinkuyu)."}]}]}
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: _GeminiReply(200, body))
    text, sources = gemini.research("sys", "p")
    assert "85 m" in text and sources == []  # no search-returned citation, so no source


def test_privacy_vision_key_travels_in_a_header(monkeypatch):
    import numpy as np
    from src import privacy
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "AIza-secret")
    seen = []
    def post(url, json=None, timeout=None, headers=None):
        seen.append((url, headers))
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
    monkeypatch.setattr("requests.post", post)
    privacy._detect_faces_cognitive_gemini(np.zeros((64, 64, 3), dtype=np.uint8))
    assert seen and "AIza-secret" not in seen[0][0] and seen[0][1]["x-goog-api-key"] == "AIza-secret"


def _draft(**kw):
    from src.models import DraftFact
    base = dict(id="F1", statement="The city reached 85 meters deep.", kind="number", quote_ids=[1], disputed=False, hedge="")
    return DraftFact(**{**base, **kw})


def test_a_doi_link_alone_is_not_tier_one():
    from src.fact_sheet import _apply_rules
    quotes = [{"url": "https://doi.org/10.1049/et.2023.0110", "quote": "The city reached 85 meters deep."}]
    kept, _ = _apply_rules([_draft()], quotes, auto_hedge=True)
    assert kept[0]["evidence"][0]["tier"] == 3 and kept[0]["disputed"] is True


def test_quotes_carry_their_own_tier():
    from src.fact_sheet import _apply_rules
    quotes = [{"url": "https://doi.org/10.1038/nature05357", "quote": "The city reached 85 meters deep.", "tier": 1}]
    kept, _ = _apply_rules([_draft()], quotes, auto_hedge=True)
    assert kept[0]["evidence"][0]["tier"] == 1 and kept[0]["disputed"] is False


def test_auto_hedge_never_invents_an_attribution():
    from src.fact_sheet import _apply_rules
    quotes = [{"url": "https://some-travel-blog.example/x", "quote": "The city reached 85 meters deep."}]
    kept, _ = _apply_rules([_draft()], quotes, auto_hedge=True)
    hedge = kept[0]["hedge"].lower()
    assert "researchers" not in hedge and "by one estimate" in hedge


def test_a_date_from_one_low_tier_site_is_hedged_but_reference_dates_stand():
    from src.fact_sheet import _apply_rules
    blog = [{"url": "https://some-travel-blog.example/x", "quote": "It was rediscovered in 1963."}]
    kept, _ = _apply_rules([_draft(statement="It was rediscovered in 1963.", kind="date")], blog, auto_hedge=True)
    assert kept[0]["disputed"] is True and "by one account" in kept[0]["hedge"].lower()
    wiki = [{"url": "https://en.wikipedia.org/wiki/Derinkuyu", "quote": "It was rediscovered in 1963."}]
    kept, _ = _apply_rules([_draft(statement="It was rediscovered in 1963.", kind="date")], wiki, auto_hedge=True)
    assert kept[0]["disputed"] is False  # an established reference work is a chronological anchor


def test_openalex_tier_depends_on_venue_and_relevance(monkeypatch):
    import requests
    from src.free_evidence import fetch_openalex_scholarly_evidence
    abstract = {"Roman": [0], "concrete": [1], "heals": [2], "its": [3], "own": [4], "cracks": [5], "over": [6], "centuries.": [7]}
    works = [
        {"title": "Hot mixing: Roman concrete", "doi": "https://doi.org/10.1/journal", "type": "article",
         "primary_location": {"source": {"type": "journal", "display_name": "Science Advances"}},
         "abstract_inverted_index": abstract},
        {"title": "Roman concrete secrets", "doi": "https://doi.org/10.1/mag", "type": "article",
         "primary_location": {"source": {"type": "trade magazine", "display_name": "E&T"}},
         "abstract_inverted_index": abstract},
        {"title": "Deep learning for traffic", "doi": "https://doi.org/10.1/offtopic", "type": "article",
         "primary_location": {"source": {"type": "journal", "display_name": "IEEE"}},
         "abstract_inverted_index": {"Traffic": [0], "prediction": [1], "with": [2], "transformers": [3], "works.": [4]}},
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"results": works}))
    quotes, sources = fetch_openalex_scholarly_evidence("Roman Self-Healing Concrete")
    by_url = {q["url"]: q for q in quotes}
    assert by_url["https://doi.org/10.1/journal"]["tier"] == 1
    assert by_url["https://doi.org/10.1/mag"]["tier"] == 3 and "Peer-Reviewed" not in by_url["https://doi.org/10.1/mag"]["title"]
    assert "https://doi.org/10.1/offtopic" not in by_url  # irrelevant works are not evidence


@pytest.mark.real_unesco
def test_unesco_evidence_reaches_the_fact_sheet():
    # Regression: a NameError inside the optional UNESCO block was swallowed, so no topic got this evidence.
    from src.fact_sheet import _unesco_evidence
    from src.unesco import UNESCOEngine
    if len(UNESCOEngine().load_sites()) < 1000:
        pytest.skip("UNESCO registry cache not available")
    topic = {"title": "Kailasa Temple", "location": {"name": "Kailasa Temple, Ellora Caves", "country": "India",
                                                     "latitude": 20.0238, "longitude": 75.1793}}
    quotes, source = _unesco_evidence(topic)
    assert quotes and "Ellora" in source.title and all(q["tier"] == 1 for q in quotes)


def test_a_rejected_ai_clip_is_still_counted_against_the_budget(db, monkeypatch, tmp_path):
    # The provider bills for a clip it generated even if our geometry check then rejects it.
    from src import motion
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 50.0)
    still = tmp_path / "s.jpg"
    from PIL import Image
    Image.new("RGB", (1080, 1920), (90, 90, 90)).save(still)

    def generated_then_rejected(img, out, on_billed=None):
        on_billed()  # the provider reported a finished (billable) clip
        raise motion.MotionError("Anti-hallucination homography check failed")
    monkeypatch.setattr(motion, "providers", lambda *a, **k: [("kling-3.0", lambda: True, generated_then_rejected, 0.84)])
    assert motion.animate(db, still, tmp_path / "m.mp4") is None
    assert db.media_spend(motion.media_budget._month()) == pytest.approx(0.84)


def test_a_clip_the_provider_refused_costs_nothing(db, monkeypatch, tmp_path):
    from src import motion
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 50.0)
    still = tmp_path / "s.jpg"
    from PIL import Image
    Image.new("RGB", (1080, 1920), (90, 90, 90)).save(still)

    def refused(img, out, on_billed=None):
        raise motion.MotionError("Veo rejected the request (400)")
    monkeypatch.setattr(motion, "providers", lambda *a, **k: [("veo", lambda: True, refused, 3.2)])
    assert motion.animate(db, still, tmp_path / "m.mp4") is None
    assert db.media_spend(motion.media_budget._month()) == 0


def test_sheets_built_under_older_evidence_rules_are_rebuilt(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from src import fact_sheet
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    sheet = {"topic_id": "t", "built_at": datetime.now(timezone.utc).isoformat(), "facts": [], "dropped": [], "sources": []}
    (tmp_path / "t.json").write_text(json.dumps(sheet), encoding="utf-8")
    assert fact_sheet.load("t") is None  # no rules_version: built before the current evidence rules
    fact_sheet.save(dict(sheet))
    assert fact_sheet.load("t") is not None and json.loads((tmp_path / "t.json").read_text())["rules_version"] == fact_sheet.RULES_VERSION


def test_a_reel_counts_its_music_track_once(db, monkeypatch, tmp_path, curated_payload):
    import json as _json
    from src import music
    from tests.test_concurrency import _resumable_run
    lib = music.LIBRARY
    tracks = _json.loads(lib.read_text(encoding="utf-8"))
    from src import sound_plan
    track = next(t for t in tracks if t.get("reviewed"))
    # QF-84: the mood comes from the sound plan (was music.choose_mood), and a dry run is not a use (was before + 1):
    # the Reel's track is recorded once as its selection, and counts once only when the Reel is published.
    monkeypatch.setattr(sound_plan, "plan_and_mood", lambda words, title="": (None, {"mood": track["mood"], "reason": ""}))
    before = track.get("uses", 0)
    coord = _resumable_run(db, monkeypatch, tmp_path, curated_payload)
    monkeypatch.setattr(music, "pick_library_track", lambda mood, seconds=0, exclude=None, **k: track)
    monkeypatch.setattr(coord, "_render_approve_publish", lambda *a: {"status": "simulated"})
    coord.resume_run("r5", dry_run=True)
    after = next(t for t in _json.loads(lib.read_text(encoding="utf-8")) if t["file"] == track["file"]).get("uses", 0)
    assert after == before
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM music_selections WHERE file = ?", (track["file"],)).fetchone()[0] == 1
    db.record_publication("01_topic", "r5", "upload-post", "published", {})
    db.record_publication("01_topic", "r5", "youtube-shorts", "published", {})
    assert db.music_published_uses() == {track["file"]: 1}


@pytest.mark.parametrize("status, claimable", [("rejected", False), ("regenerate", False), ("approved", True),
                                               ("scheduled", True), ("failed", True), ("published", False)])
def test_a_human_rejection_cannot_be_published(db, status, claimable):
    db.create_run("r1", "01_topic")
    db.update_run("r1", status)
    assert db.claim_publication("r1") is claimable


def _graph_reply(status, body):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: body, text=json.dumps(body))


@pytest.mark.parametrize("status, outcome", [(500, "PublishUnknown"), (503, "PublishUnknown"), (400, "PublishFailed")])
def test_server_error_at_the_point_of_no_return_is_unknown(monkeypatch, tmp_path, status, outcome):
    from src import publisher
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    replies = iter([_graph_reply(200, {"id": "c1"}), _graph_reply(200, {}), _graph_reply(200, {"status_code": "FINISHED"}),
                    _graph_reply(status, {"error": {"message": "boom"}})])
    monkeypatch.setattr(publisher.requests, "request", lambda *a, **k: next(replies))
    pub = publisher.InstagramGraphPublisher(token="t" * 20, ig_user_id="123", poll_interval=0)
    env = {"platforms": ["instagram"], "metadata": {"caption": "c", "first_comment": ""}, "media": {"video_path": str(video)}}
    with pytest.raises(getattr(publisher, outcome)):
        pub.publish(env)


def test_backup_keeps_documentaries_and_audio_evidence(tmp_path):
    from src.backup import backup_run
    run = tmp_path / "run_x"
    run.mkdir()
    names = ["t_documentary_16x9.mp4", "t_documentary_16x9.srt", "t_documentary_16x9_captions.ass",
             "thumbnail_youtube_16x9.jpg", "voiceover.mp3", "voiceover.mp3.rights.json", "ambient_bed.wav",
             "ambient_bed.wav.rights.json", "words.json", "t_master.mp4"]
    for n in names:
        (run / n).write_bytes(b"x")
    res = backup_run(run, dest_base=tmp_path / "backup")
    archived = {p.name for p in (tmp_path / "backup" / "run_x").iterdir()}
    assert res["status"] == "staged" and set(names) <= archived


def test_purge_keeps_the_audio_a_release_check_needs(tmp_path):
    import os, time
    from src.ops import AssetLifecycleManager
    run = tmp_path / "run_old"
    (run / "parallax").mkdir(parents=True)
    for name in ("voiceover.mp3", "ambient_bed.wav", "x_master.mp4", "render.tmp"):
        (run / name).write_bytes(b"x")
    (run / "parallax" / "scene_01.mp4").write_bytes(b"x")
    old = time.time() - 30 * 86400
    os.utime(run, (old, old))
    AssetLifecycleManager(output_dir=tmp_path).purge_intermediate_assets()
    assert (run / "voiceover.mp3").exists() and (run / "ambient_bed.wav").exists() and (run / "x_master.mp4").exists()
    assert not (run / "render.tmp").exists() and not (run / "parallax").exists()  # true scratch still goes


def test_masker_hides_every_configured_secret(monkeypatch):
    from src import config
    from src.ops import SecretMasker
    secrets = {"KLING_API_KEY": "kling-secret-123456", "GEMINI_FREE_API_KEY": "AIzaFreeKey1234567890",
               "HEALTHCHECK_PING_URL": "https://hc-ping.com/very-secret-uuid"}
    for k, v in secrets.items():
        monkeypatch.setattr(config, k, v)
    masked = SecretMasker().mask(" ".join(secrets.values()) + " https://x.googleapis.com/v1/m?key=AIzaSyOtherKey98765432109876543210abcde")
    for v in secrets.values():
        assert v not in masked
    assert "AIzaSyOtherKey98765432109876543210abcde" not in masked  # any Gemini-style key in a URL


def test_logs_persist_to_a_masked_rotating_file(monkeypatch, tmp_path):
    import logging
    from src import config, ops
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "KLING_API_KEY", "kling-secret-123456")
    root = logging.getLogger()
    monkeypatch.setattr(root, "_pipeline_configured", False, raising=False)
    before = list(root.handlers)
    try:
        ops.setup_logging()
        logging.getLogger("t").warning("token kling-secret-123456 used")
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "logs" / "pipeline.log").read_text(encoding="utf-8")
        assert "token" in text and "kling-secret-123456" not in text
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_rewriting_the_music_library_never_drops_tracks_whose_audio_is_missing(tmp_path, monkeypatch):
    # A fresh machine has library.json (tracked) but not the audio (git-ignored) until it is copied.
    import json as _json
    from src import music
    lib = tmp_path / "library.json"
    lib.write_text(_json.dumps([{"file": "a/x.mp3", "title": "X", "mood": "a", "reviewed": True},
                                {"file": "a/y.mp3", "title": "Y", "mood": "a", "reviewed": True}]), encoding="utf-8")
    monkeypatch.setattr(music, "LIBRARY", lib)
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    assert music.load_library() == []                      # nothing to pick here...
    music.mark_used("a/x.mp3")
    kept = _json.loads(lib.read_text(encoding="utf-8"))
    assert [t["file"] for t in kept] == ["a/x.mp3", "a/y.mp3"] and kept[0]["uses"] == 1   # ...but nothing lost
