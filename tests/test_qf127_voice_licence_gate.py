"""QF-127: the voice licence gate.

A real (non-dry) run is voiced only by a provider and tier recorded as commercially licensed (src/voice_licence.py):
ElevenLabs on a paid plan, or Azure Speech on a paid tier the operator declared (AZURE_SPEECH_TIER, default F0). A
configured key is never a licence. With no licensed voice the run is held with one Needs-you notice and nothing is
voiced, approved or posted; dry runs keep Azure and edge-tts; the run's records say who voiced it, on which tier and
with what licence status; a master voiced by Azure before the tier was recorded is flagged on the review card and
not posted. Every provider is a stand-in: nothing here calls a voice service."""
import json
from types import SimpleNamespace

import pytest

from src import config
from src.audio_engine import AudioEngine, NoLicensedVoice

WORDS = [{"word": "Hi", "start": 0.0, "dur": 0.3}]


def engine(monkeypatch, eleven_tier=None, eleven_ok=True, declared=None):
    """An engine with both keys set; `declared` is the AZURE_SPEECH_TIER the operator set (None: the default)."""
    if declared is not None:
        monkeypatch.setattr("src.audio_engine.AZURE_SPEECH_TIER", declared, raising=False)
        monkeypatch.setattr(config, "AZURE_SPEECH_TIER", declared, raising=False)
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v", azure_key="a" * 20, azure_region="eastus")
    calls = []
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": eleven_tier} if eleven_tier else None)

    def eleven(text, out):
        calls.append("elevenlabs")
        if not eleven_ok:
            raise RuntimeError("down")
        return WORDS

    def azure(text, out):
        calls.append("azure")
        return WORDS

    monkeypatch.setattr(eng, "_generate_elevenlabs", eleven)
    monkeypatch.setattr(eng, "_generate_azure", azure)
    return eng, calls


def real_take(eng, tmp_path):
    """A real run's narration: the NoLicensedVoice it raised, or None."""
    try:
        eng.generate_narration("x", tmp_path / "v.wav", licensed_only=True)
    except NoLicensedVoice as e:
        return e
    return None


# ---------------------------------------------------------------- the engine: who may voice a real run

@pytest.mark.parametrize("eleven_tier, eleven_ok, tried", [
    (None, True, []),                  # ElevenLabs' plan unknown: not licensed
    ("free", True, []),                # ElevenLabs Free
    ("creator", False, ["elevenlabs"]),   # the paid plan failed: no fallback to the free tier
])
def test_azures_free_tier_never_voices_a_real_run(monkeypatch, tmp_path, eleven_tier, eleven_ok, tried):
    eng, calls = engine(monkeypatch, eleven_tier, eleven_ok)
    held = real_take(eng, tmp_path)
    assert calls == tried                       # Azure was never asked, with its key and region set
    assert held is not None and "Azure Speech F0" in str(held)
    assert eng.last_provider == "" and not eng.last_licensed


def test_an_azure_key_alone_is_not_a_licence(monkeypatch, tmp_path):
    eng = AudioEngine(eleven_key="", eleven_voice="", azure_key="a" * 20, azure_region="eastus")
    calls = []
    monkeypatch.setattr(eng, "_generate_azure", lambda text, out: calls.append("azure") or WORDS)
    assert real_take(eng, tmp_path) is not None and calls == []
    assert getattr(eng, "azure_licensed", lambda: True)() is False


@pytest.mark.parametrize("declared, licensed", [("S0", True), ("s0", True), ("F0", False), ("", False),
                                                ("S1", False), ("paid", False), ("true", False)])
def test_azure_counts_only_on_the_paid_tier_the_operator_declared(monkeypatch, tmp_path, declared, licensed):
    eng, calls = engine(monkeypatch, eleven_tier="free", declared=declared)
    held = real_take(eng, tmp_path)
    assert (held is None) is licensed and calls == (["azure"] if licensed else [])
    if licensed:
        assert (eng.last_provider, getattr(eng, "last_tier", None), eng.last_licensed) == ("azure", "s0", True)
        assert "declared" in getattr(eng, "last_licence_basis", "")


def test_the_paid_tier_is_not_declared_by_default():
    import re
    from pathlib import Path
    assert getattr(config, "AZURE_SPEECH_TIER", None) == "F0"        # tests/conftest.py never declares a paid tier
    root = Path(__file__).resolve().parent.parent
    assert re.findall(r"^AZURE_SPEECH_TIER=(.*)$", (root / ".env.example").read_text(encoding="utf-8"), re.MULTILINE) == ["F0"]
    source = (root / "src" / "config.py").read_text(encoding="utf-8")
    assert 'AZURE_SPEECH_TIER = (os.getenv("AZURE_SPEECH_TIER") or "F0").strip().upper()' in source
    conftest = (root / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert not re.search(r"AZURE_SPEECH_TIER[^\n]*[\"']S0[\"']", conftest, re.IGNORECASE)


def test_the_licence_requirement_is_on_unless_the_operator_turns_it_off(monkeypatch):
    """VOICE_REQUIRE_LICENSED is an existing operator switch, unchanged by this row: on by default in the code and in
    .env.example; only with it on does a real run ask the engine for a licensed voice."""
    from pathlib import Path

    from main import PipelineCoordinator
    root = Path(__file__).resolve().parent.parent
    assert 'VOICE_REQUIRE_LICENSED = os.getenv("VOICE_REQUIRE_LICENSED", "true")' in (
        root / "src" / "config.py").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "VOICE_REQUIRE_LICENSED=true" in example.splitlines()
    assert "Never set it to false where real posts are made" in example
    coord = PipelineCoordinator.__new__(PipelineCoordinator)
    coord.audio_engine = AudioEngine(eleven_key="", eleven_voice="v", azure_key="", azure_region="")
    coord.paid_voice_in_dry_run = False
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    assert coord.voice_inputs("S", dry_run=False)["licensed_only"] is True
    assert coord.voice_inputs("S", dry_run=True)["licensed_only"] is False


def test_a_dry_run_is_still_voiced_by_azure_and_recorded_as_unlicensed(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, eleven_tier="creator")
    eng.generate_narration("x", tmp_path / "v.wav", licensed_only=False, allow_paid=False)   # as a dry run asks
    assert calls == ["azure"] and eng.last_provider == "azure"       # development's dry-run voice is unchanged
    assert eng.last_licensed is False                                # ... and no longer recorded as licensed
    assert getattr(eng, "last_tier", None) == "f0" and "F0" in getattr(eng, "last_licence_basis", "")


def test_elevenlabs_on_a_paid_plan_voices_a_real_run_as_before(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, eleven_tier="creator")
    assert real_take(eng, tmp_path) is None and calls == ["elevenlabs"]
    assert (eng.last_provider, eng.last_licensed) == ("elevenlabs", True)
    assert getattr(eng, "last_tier", None) == "creator"


# ---------------------------------------------------------------- the table

@pytest.mark.parametrize("provider, tier, licensed", [
    ("elevenlabs", "creator", True), ("elevenlabs", "starter", True), ("ElevenLabs", "Pro", True),
    ("elevenlabs", "free", False), ("elevenlabs", "", False), ("elevenlabs", None, False),
    ("azure", "s0", True), ("azure", "S0", True), ("azure", "f0", False), ("azure", "", False), ("azure", None, False),
    ("azure", "s1", False), ("edge-tts", "", False), ("edge-tts", "s0", False), ("", "s0", False),
    (None, None, False), ("someone-else", "paid", False),
])
def test_the_licence_table(provider, tier, licensed):
    from src import voice_licence
    ok, basis = voice_licence.licence(provider, tier)
    assert ok is licensed and basis


@pytest.mark.parametrize("record, licensed", [
    ({"provider": "elevenlabs", "licensed": True}, True),                       # made before the tier was saved
    ({"provider": "elevenlabs", "tier": "creator", "licensed": True}, True),
    ({"provider": "elevenlabs", "tier": "free", "licensed": True}, False),      # the table decides, not the flag
    ({"provider": "elevenlabs", "tier": "creator", "licensed": False}, False),
    ({"provider": "azure", "licensed": True}, False),                           # a key was taken for a licence
    ({"provider": "azure", "tier": "f0", "licensed": True}, False),
    ({"provider": "azure", "tier": "s0", "licensed": True}, True),
    ({"provider": "azure", "tier": "s0", "licensed": False}, False),
    ({"provider": "azure", "tier": "s0", "licensed": "true"}, False),           # only a real true
    ({"provider": "edge-tts", "licensed": True}, False),
    ({"licensed": True}, False), ({}, False), (None, False), ([], False),
])
def test_a_saved_record_is_read_against_the_table(record, licensed):
    from src import voice_licence
    assert voice_licence.record_licensed(record) is licensed


# ---------------------------------------------------------------- publishing: nothing unlicensed is posted

def live_publish(monkeypatch, db, tmp_path, voice):
    from unittest.mock import MagicMock

    from src.publisher import MetaPublisher
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("src.publisher._assert_recovery_unlocked", lambda: None)
    (tmp_path / "r1").mkdir(parents=True, exist_ok=True)
    if voice is not None:
        (tmp_path / "r1" / "voice.json").write_text(json.dumps(voice), encoding="utf-8")
    db.create_run("r1", "01_topic")
    video, cover = tmp_path / "v.mp4", tmp_path / "c.jpg"
    video.write_bytes(b"0" * 10)
    cover.write_bytes(b"0")
    pub = MagicMock()
    pub.name, pub.platforms = "a", ("facebook", "instagram")
    pub.publish.return_value = {"status": "published"}
    envelope = {"run_id": "r1", "topic_id": "01_topic", "platforms": ["facebook", "instagram"],
                "media": {"video_path": str(video), "thumbnail_path": str(cover), "thumbnail_1x1_path": str(cover)},
                "metadata": {"title": "T", "caption": "Cap", "first_comment": "Q?", "is_ai_generated": True}}
    return MetaPublisher(db, [pub]).publish_reel(envelope, dry_run=False), pub


@pytest.mark.parametrize("voice", [
    {"provider": "azure", "licensed": True},                    # every Azure master made before this row
    {"provider": "azure", "tier": "f0", "licensed": True},
    {"provider": "azure", "tier": "f0", "licensed": False},
    {"provider": "elevenlabs", "tier": "free", "licensed": True},
    {"licensed": True},
])
def test_a_master_whose_voice_is_not_licensed_is_blocked_at_publish(monkeypatch, db, tmp_path, voice):
    res, pub = live_publish(monkeypatch, db, tmp_path, voice)
    assert res["status"] == "blocked_unlicensed" and "Commercial Voice License Required" in res["error"]
    pub.publish.assert_not_called()
    events = db.list_audit_logs()
    assert events and events[0]["action"] == "publish_blocked_unlicensed_voice" and events[0]["status"] == "blocked"


@pytest.mark.parametrize("voice", [{"provider": "elevenlabs", "licensed": True},
                                   {"provider": "elevenlabs", "tier": "creator", "licensed": True},
                                   {"provider": "azure", "tier": "s0", "licensed": True}])
def test_a_licensed_master_is_posted(monkeypatch, db, tmp_path, voice):
    res, pub = live_publish(monkeypatch, db, tmp_path, voice)
    assert res["status"] == "published"
    pub.publish.assert_called_once()


def test_the_block_names_the_reason_and_no_longer_offers_an_azure_key(monkeypatch, db, tmp_path):
    res, _ = live_publish(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True})
    assert "Azure Speech, tier not recorded" in res["error"] and "ElevenLabs on a paid plan" in res["error"]
    assert "configure a valid Azure Speech key" not in res["error"]


# ---------------------------------------------------------------- preflight: a key is not a licensed voice

def live_settings(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-real")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "k")
    monkeypatch.setattr(config, "UPLOAD_POST_USER", "u")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setattr(config, "TELEGRAM_APPROVER_IDS", {"987654"})
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "a" * 32)
    monkeypatch.setattr(config, "AZURE_SPEECH_REGION", "eastus")


def test_live_credentials_need_a_licensed_voice_not_an_azure_key(monkeypatch):
    from src.ops import PreFlightHealthChecker
    live_settings(monkeypatch)
    ok, msg = PreFlightHealthChecker().check_live_credentials()
    assert not ok and "commercially licensed voice" in msg and "Azure F0" in msg
    monkeypatch.setattr(config, "AZURE_SPEECH_TIER", "S0", raising=False)      # the operator declared the paid tier
    assert PreFlightHealthChecker().check_live_credentials()[0]


def test_the_live_probe_does_not_count_a_working_free_tier_key(monkeypatch):
    from src.ops import PreFlightHealthChecker
    live_settings(monkeypatch)
    monkeypatch.setattr("src.ops.requests.post", lambda url, **kw: SimpleNamespace(status_code=200, ok=True, json=dict))
    monkeypatch.setattr("src.ops.requests.get", lambda url, **kw: SimpleNamespace(status_code=200, ok=True, json=dict))
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert not ok and "No working commercially licensed voice" in msg
    monkeypatch.setattr(config, "AZURE_SPEECH_TIER", "S0", raising=False)
    ok, msg = PreFlightHealthChecker().probe_live_services()
    assert "No working commercially licensed voice" not in msg


def test_the_status_screen_calls_a_free_tier_key_a_test_voice(monkeypatch):
    from src import control
    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "a" * 32)
    monkeypatch.setattr("src.config.AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setattr(control.requests, "post", lambda url, **kw: SimpleNamespace(status_code=200))
    status = control.check_azure_speech()
    assert status["ok"] and status.get("licensed") is False and "licensed fallback" not in status["status"]
    assert "test runs only" in status["status"] and "F0" in status["status"]
    low = control.quota_lines({"tier": "creator", "used": 9900, "limit": 10000, "resets": None})
    assert not any("Falls back to Azure" in line for line in low) and any("Licence alert" in line for line in low)
    monkeypatch.setattr(config, "AZURE_SPEECH_TIER", "S0", raising=False)
    assert control.check_azure_speech().get("licensed") is True
    assert any("Falls back to Azure" in line for line in control.quota_lines(
        {"tier": "creator", "used": 9900, "limit": 10000, "resets": None}))


# ---------------------------------------------------------------- the hold: one Needs-you notice, nothing posted

REASON = ("No commercially licensed voice is available: ElevenLabs is on the Free plan (no commercial licence); "
          "Azure Speech F0: no commercial licence recorded for this tier.")


def held_coordinator(db, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    coord = main.PipelineCoordinator(db)
    monkeypatch.setattr(coord.health_checker, "run_all", lambda require_live=False, dry_run=False: {"all_passed": True, "details": {}})
    posted = []

    def no_voice(*a, **k):
        raise NoLicensedVoice(REASON)
    monkeypatch.setattr(coord, "_produce_and_publish", no_voice)
    monkeypatch.setattr(coord, "_collect_findings", lambda *a, **k: None)
    monkeypatch.setattr(coord.publisher, "publish_reel", lambda *a, **k: posted.append(a) or {"status": "published"})
    return coord, posted


def needs_items(db):
    from src import notifications
    return [i for i in notifications.open_items(db) if i["section"] == notifications.NEEDS]


def test_a_real_run_with_no_licensed_voice_is_held_with_one_needs_you_notice(db, tmp_path, monkeypatch):
    from src.layout_repost import NEEDS_MARKS
    coord, posted = held_coordinator(db, tmp_path, monkeypatch)
    try:
        result = coord.run_cycle("01_topic", auto_approve=False, dry_run=False, run_id="run_01_topic_1")
    except NoLicensedVoice as e:     # before QF-127 the run ended as any failure: no hold, no notice of its own
        result = {"status": "raised", "error": str(e)}
    assert result["status"] == "held" and result["reason"] == "no_licensed_voice"
    run = db.get_run("run_01_topic_1")
    assert run["status"] == "failed" and run["error"].startswith("NoLicensedVoice: ")   # Re-render can continue it
    assert posted == [] and db.list_publications("run_01_topic_1") == []
    assert (db.get_approval("run_01_topic_1") or {}).get("decision") != "approved"
    items = needs_items(db)
    assert len(items) == 1                                             # one notice
    item = items[0]
    assert item["dedupe_key"] == "fail:run_01_topic_1" and item["run_id"] == "run_01_topic_1"
    assert str(item["emoji"]).startswith(NEEDS_MARKS) and item["title"] == "Held: no licensed voice"
    assert "Nothing was voiced by an unlicensed voice, approved or posted" in item["body"]
    assert "Azure Speech F0" in item["body"]


def test_the_hold_and_the_progress_panels_ending_are_one_item(db, tmp_path, monkeypatch):
    """With a progress panel open, its ending files the run's failure item (fail:<run>) and the hold replaces it."""
    from src import notifications
    coord, _ = held_coordinator(db, tmp_path, monkeypatch)
    coord.progress_msg_id = 41

    def finish(message_id, title, run_id, success=True, error=""):
        notifications.post(db, notifications.NEEDS, f"fail:{run_id}", "Production failed", error, emoji="❌",
                           subject=title, run=run_id, replace=True)
        return True
    monkeypatch.setattr(coord.telegram_bot, "finish_progress_card", finish)
    try:
        coord.run_cycle("01_topic", auto_approve=False, dry_run=False, run_id="run_01_topic_2")
    except NoLicensedVoice:
        pass
    items = needs_items(db)
    assert [i["dedupe_key"] for i in items] == ["fail:run_01_topic_2"]
    assert items[0]["title"] == "Held: no licensed voice" and coord.progress_msg_id is None


def test_a_resumed_run_is_held_the_same_way(db, tmp_path, monkeypatch):
    coord, posted = held_coordinator(db, tmp_path, monkeypatch)
    (tmp_path / "r7").mkdir()
    db.create_run("r7", "01_topic")
    db.update_run("r7", "failed", error="RenderError")
    try:
        result = coord.resume_run("r7", dry_run=False)
    except NoLicensedVoice as e:
        result = {"status": "raised", "error": str(e)}
    assert result["status"] == "held" and posted == []
    assert [i["dedupe_key"] for i in needs_items(db)] == ["fail:r7"]
    assert db.get_run("r7")["status"] == "failed"


def test_a_held_run_is_not_a_completed_one():
    import main
    assert "held" not in main.RUN_COMPLETED_STATUSES      # the run command exits 1; the daemon's batch stops


# ---------------------------------------------------------------- the records: who voiced it, truthfully

def test_the_rights_record_names_the_tier_and_never_licenses_the_free_tier(tmp_path):
    from src.audio_provenance import inspect_audio_rights, record_voice
    voice = tmp_path / "voiceover.wav"
    voice.write_bytes(b"voice")
    try:
        record = record_voice(voice, "azure", True, "script", tier="F0")     # a caller's "licensed" does not decide
    except TypeError:                                                          # before QF-127: no tier in the record
        record = record_voice(voice, "azure", True, "script")
    assert record["commercial_license_recorded"] is False
    assert record.get("tier") == "f0" and "F0" in record.get("licence_basis", "")
    assert not inspect_audio_rights(voice, "voice", "", script="script")[0]
    record = record_voice(voice, "azure", True, "script", tier="S0")
    assert record["commercial_license_recorded"] is True and inspect_audio_rights(voice, "voice", "", script="script")[0]
    record = record_voice(voice, "elevenlabs", True, "script", tier="creator")
    assert record["commercial_license_recorded"] is True and record["tier"] == "creator"
    assert inspect_audio_rights(voice, "voice", "", script="script")[0]


def test_an_azure_take_recorded_with_no_tier_never_passes_on_the_callers_word(tmp_path):
    """decisions QF-127b: the case tests/test_audio_provenance.py allowed before this row."""
    from src.audio_provenance import inspect_audio_rights, record_voice
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"voice")
    record = record_voice(voice, "azure", True, "script")                     # licensed=True, no tier
    assert record["commercial_license_recorded"] is False
    assert not inspect_audio_rights(voice, "voice", "")[0]
    assert not inspect_audio_rights(voice, "voice", "", script="script")[0]
    try:
        record_voice(voice, "azure", True, "script", tier="S0")               # the paid tier, explicitly declared
    except TypeError:                                                          # before QF-127: no tier to name
        pass
    assert json.loads(voice.with_suffix(".mp3.rights.json").read_text(encoding="utf-8")).get("tier") == "s0"
    assert inspect_audio_rights(voice, "voice", "", script="script")[0]
    record_voice(voice, "edge-tts", True, "script")                           # a test voice, whatever the caller says
    assert not inspect_audio_rights(voice, "voice", "")[0]


def old_rights_record(voice, provider):
    """A rights record as it was written before QF-127: no tier, licensed by the engine's word."""
    from src.checkpoints import fingerprint
    from src.credits import sha256_file
    record = {"path": str(voice.resolve()), "sha256": sha256_file(voice), "provider": provider,
              "commercial_license_recorded": True, "script_fingerprint": fingerprint("script")}
    with open(voice.with_suffix(voice.suffix + ".rights.json"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2))
    return record


def test_an_azure_rights_record_from_before_the_tier_fails_the_release_check_and_is_not_rewritten(tmp_path):
    from src.audio_provenance import inspect_audio_rights
    voice = tmp_path / "voiceover.mp3"
    voice.write_bytes(b"voice")
    old_rights_record(voice, "azure")
    before = voice.with_suffix(".mp3.rights.json").read_bytes()
    ok, why = inspect_audio_rights(voice, "voice", "", script="script")
    assert not ok and "not commercially licensed" in str(why)
    assert voice.with_suffix(".mp3.rights.json").read_bytes() == before        # history is read, never rewritten
    old_rights_record(voice, "elevenlabs")                                     # a paid ElevenLabs take still stands
    assert inspect_audio_rights(voice, "voice", "", script="script")[0]


def test_a_real_run_never_reuses_an_azure_take_saved_as_licensed(tmp_path, monkeypatch):
    from main import PipelineCoordinator as P
    from src.checkpoints import Checkpoints
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    made = {"script": "S", "voice": "v", "licensed_only": True, "allow_paid": True}
    (tmp_path / "voiceover.mp3").write_bytes(b"x")
    cp = Checkpoints(tmp_path)
    cp.complete("voice", made, [tmp_path / "voiceover.mp3"])
    old_azure = {"provider": "azure", "licensed": True}
    assert not P.voice_reusable(cp, made, old_azure, dry_run=False)            # re-voiced (or held), never carried
    assert P.voice_reusable(cp, made, old_azure, dry_run=True)                 # a dry run keeps it, as before
    assert P.voice_reusable(cp, made, {"provider": "elevenlabs", "licensed": True}, dry_run=False)
    assert P.voice_reusable(cp, made, {"provider": "azure", "tier": "s0", "licensed": True}, dry_run=False)


# ---------------------------------------------------------------- the review card: an old Azure master is flagged

FLAG = "voice not commercially licensed"


def old_master(db, tmp_path, monkeypatch, voice):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)      # the gate on, as the code and .env.example ship it
    (tmp_path / "r1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "r1" / "voice.json").write_text(json.dumps(voice), encoding="utf-8")
    return {"title": "T", "narration_script": "x", "caption": "c",
            "voice_note": f"🎙️ Voice: {voice['provider'].title()} (🟢 Commercially Licensed)"}


def test_the_review_card_flags_a_master_voiced_by_azure(db, tmp_path, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    payload = old_master(db, tmp_path, monkeypatch, {"provider": "azure", "licensed": True})
    card = TelegramApprovalBot.format_card_text(payload, "r1")
    assert FLAG in card and "Approve will not post it" in card
    assert "Commercially Licensed" not in card            # the saved note's claim is not repeated beside the flag


@pytest.mark.parametrize("voice", [{"provider": "elevenlabs", "licensed": True},
                                   {"provider": "elevenlabs", "tier": "creator", "licensed": True},
                                   {"provider": "azure", "tier": "s0", "licensed": True}])
def test_the_review_card_does_not_flag_a_licensed_master(db, tmp_path, monkeypatch, voice):
    from src.telegram_bot import TelegramApprovalBot
    payload = old_master(db, tmp_path, monkeypatch, voice)
    card = TelegramApprovalBot.format_card_text(payload, "r1")
    assert FLAG not in card and "Commercially Licensed" in card


def test_a_test_voice_that_never_claimed_a_licence_keeps_its_own_note(db, tmp_path, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    payload = old_master(db, tmp_path, monkeypatch, {"provider": "edge-tts", "licensed": False})
    payload["voice_note"] = "🎙️ Voice: Edge-Tts (🟡 Unlicensed (Review / Test Only))"
    card = TelegramApprovalBot.format_card_text(payload, "r1")
    assert FLAG not in card and "Unlicensed (Review / Test Only)" in card


def panel_review(db, tmp_path, monkeypatch, voice):
    """The clean chat's review panel on the real development run of tests/fixtures/ui02_review_v2 (voiced by Azure,
    saved as licensed, every check passing), with `voice` as its voice.json when given."""
    from src import telegram_bot
    from tests import test_ui01e_review_panel as panel
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_APPROVE_UNDO_LEAD_MINUTES", 15.0, raising=False)
    monkeypatch.setattr(telegram_bot, "_video_dimensions", lambda path: {"width": 720, "height": 1280})
    bot, fake = panel.make_bot(db, monkeypatch)
    root, payload = panel.a_review(db, tmp_path, monkeypatch, "rv1")
    if voice is not None:
        (root / "voice.json").write_text(json.dumps(voice), encoding="utf-8")
    mid = panel.deliver(bot, db, root, payload, "rv1")
    return panel.caption(fake, mid), panel.labels(fake, mid), payload


def test_the_review_panel_leads_with_the_flag_on_a_real_azure_master(db, tmp_path, monkeypatch):
    text, buttons, payload = panel_review(db, tmp_path, monkeypatch, None)
    assert "Commercially Licensed" in payload["voice_note"]          # what the run saved when it was made
    assert "Voice not commercially licensed</b> · Approve will not post it" in text
    assert "Ready to approve" not in text and "Commercially Licensed" not in text
    notes = text.split("Notes to check", 1)[1]
    assert notes.split("• ", 2)[1].startswith("⚠️ Voice: " + FLAG)   # the first note, never among "…and N more"
    assert buttons[0] == ["Approve & post"]                           # a dry-run review still decides as before


def test_the_review_panel_does_not_flag_a_licensed_master(db, tmp_path, monkeypatch):
    text, _, _ = panel_review(db, tmp_path, monkeypatch, {"provider": "elevenlabs", "tier": "creator", "licensed": True})
    assert "Ready to approve" in text and FLAG not in text.lower() and "Commercially Licensed" in text


# ================================================================ round 2 (review 1)
# ---------------------------------------------------------------- one gate for every upload of a master's audio

UNLICENSED_MASTERS = [
    {"provider": "azure", "licensed": True},          # an Azure master saved as licensed before the tier was recorded
    {"licensed": True},                               # a record that names no provider or tier
    None,                                             # no voice.json
]


def a_master(monkeypatch, db, tmp_path, voice, run_id="r1", gate=True):
    """A run folder with `voice` as its voice.json (None: no file), a stand-in uploader and the real publisher."""
    from unittest.mock import MagicMock

    from src.publisher import MetaPublisher
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", gate)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("src.publisher._assert_recovery_unlocked", lambda: None)
    (tmp_path / run_id).mkdir(parents=True, exist_ok=True)
    if voice is not None:
        (tmp_path / run_id / "voice.json").write_text(json.dumps(voice), encoding="utf-8")
    db.create_run(run_id, "01_topic")
    uploader = MagicMock()
    uploader.name, uploader.platforms = "a", ("facebook", "instagram")
    uploader.publish.return_value = {"post_urls": {"facebook": "https://f/1"}}
    uploader.publish_short.return_value = {"post_urls": {"youtube": "https://y/1"}}
    envelope = {"run_id": run_id, "topic_id": "01_topic", "platforms": ["facebook", "instagram"]}
    return MetaPublisher(db, [uploader]), uploader, envelope


def audit_actions(db):
    return [(e["action"], e["status"]) for e in db.list_audit_logs()]


@pytest.mark.parametrize("voice", UNLICENSED_MASTERS)
def test_the_youtube_short_of_a_blocked_master_is_never_uploaded(monkeypatch, db, tmp_path, voice):
    """Review 1, blocking 1: publish_short had no voice gate, so the Short of a master publish_reel blocks went up."""
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, voice)
    reel = publisher.publish_reel(envelope, dry_run=False)
    short = publisher.publish_short(envelope, dry_run=False)
    assert reel["status"] == "blocked_unlicensed" and short["status"] == "blocked_unlicensed"
    assert short["error"].startswith("not posted: voice not commercially licensed")
    uploader.publish.assert_not_called()
    uploader.publish_short.assert_not_called()
    assert audit_actions(db) == [("publish_blocked_unlicensed_voice", "blocked")] * 2    # the same event, once each
    assert [p for p in db.list_publications("r1") if p["status"] == "published"] == []


@pytest.mark.parametrize("voice", UNLICENSED_MASTERS)
def test_the_youtube_short_action_answers_not_posted_for_a_blocked_master(monkeypatch, db, tmp_path, voice):
    """The "YouTube Short" button (callback ytshort, both action maps) on a run whose master is not licensed."""
    import main
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, voice)
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / "r1" / name).write_bytes(b"offline fixture")
    db.update_run("r1", "published", master_video_path=tmp_path / "r1" / "master.mp4",
                  thumbnail_cover_path=tmp_path / "r1" / "cover.jpg", thumbnail_grid_path=tmp_path / "r1" / "cover.jpg",
                  script_payload={"title": "T", "caption": "c", "topic_id": "01_topic"})
    monkeypatch.setattr(publisher, "build_envelope", lambda *a, **k: envelope)
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.publisher = db, publisher
    answer = coord.post_youtube_short("r1")
    uploader.publish_short.assert_not_called()
    assert "not posted: voice not commercially licensed" in answer and "YouTube Short posted" not in answer
    assert "Regenerate" in answer and "Music / sound" in answer          # the recovery, by its buttons' names


def test_a_licensed_masters_short_is_uploaded(monkeypatch, db, tmp_path):
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "elevenlabs", "licensed": True})
    assert publisher.publish_short(envelope, dry_run=False)["status"] == "published"
    uploader.publish_short.assert_called_once()
    assert audit_actions(db) == []


def test_every_upload_of_a_masters_audio_goes_through_the_one_gate():
    """The uploaders' publish and publish_short are called in two places only, MetaPublisher.publish_reel and
    MetaPublisher.publish_short, each after self.voice_gate; every other module reaches them through
    coordinator.publisher (a MetaPublisher), and both action maps (the bot's and the daemon's) wire the same
    coordinator methods."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    files = [root / "main.py"] + sorted((root / "src").glob("*.py")) + sorted((root / "scripts").glob("*.py"))
    uploads, entries = [], []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            for call in calls:
                target = ast.unparse(call.func.value)
                if call.func.attr in ("publish", "publish_short", "_publish_youtube") and target != "self.publisher" \
                        and not (call.func.attr == "_publish_youtube" and target == "self"):
                    gate = [c.lineno for c in calls if ast.unparse(c.func) == "self.voice_gate"]
                    uploads.append((path.name, fn.name, call.func.attr, bool(gate) and min(gate) < call.lineno))
                if call.func.attr in ("publish_reel", "publish_short") and target == "self.publisher":
                    entries.append((path.name, fn.name, call.func.attr))
    assert sorted(uploads) == [("publisher.py", "publish_reel", "publish", True),
                               ("publisher.py", "publish_short", "publish_short", True)]
    assert sorted(entries) == [("main.py", "_publish_run_locked", "publish_reel"),
                               ("main.py", "_publish_run_locked", "publish_short"),
                               ("main.py", "post_youtube_short", "publish_short"),
                               ("main.py", "retry_reel", "publish_reel")]
    source = (root / "main.py").read_text(encoding="utf-8")
    assert source.count('"ytshort": getattr(coordinator, "post_youtube_short", None)') == 2
    assert source.count('"retryreel": getattr(coordinator, "retry_reel", None)') == 2


# ---------------------------------------------------------------- ElevenLabs: an allow-list of its paid plans

PAID_PLANS = ["starter", "creator", "pro", "scale", "business", "enterprise"]
NOT_PLANS = ["trial", "free_trial", "f0", "unknown", "none", "zzz"]          # review 1, blocking 2: the probe's names
API_TIER_IDS = ["growing_business", "scale_2024_08_10"]     # round 3: the pricing page's ids for Scale and Business


@pytest.mark.parametrize("tier", PAID_PLANS + [" Creator ", "PRO", "Starter\n"])
def test_the_paid_plans_elevenlabs_names_are_licensed(tier):
    from src import voice_licence
    assert voice_licence.licence("elevenlabs", tier) == (True, f"ElevenLabs {tier.strip().lower()} plan: paid plan, commercial use")


@pytest.mark.parametrize("tier", NOT_PLANS + ["growing_business2", "scale_2024", "creator2", "paid", 3, ["creator"], True])
def test_a_plan_name_the_table_does_not_know_is_not_licensed(monkeypatch, db, tmp_path, tier):
    from src import voice_licence
    ok, basis = voice_licence.licence("elevenlabs", tier)
    assert ok is False
    if isinstance(tier, str):
        assert basis == f"ElevenLabs plan {tier} not recognised: no commercial licence recorded"
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "elevenlabs", "tier": tier, "licensed": True})
    assert publisher.publish_reel(envelope, dry_run=False)["status"] == "blocked_unlicensed"
    assert publisher.publish_short(envelope, dry_run=False)["status"] == "blocked_unlicensed"
    uploader.publish.assert_not_called()
    uploader.publish_short.assert_not_called()


def test_the_allow_list_is_the_plans_elevenlabs_names_and_cites_its_source():
    from pathlib import Path

    from src import voice_licence
    assert sorted(getattr(voice_licence, "ELEVENLABS_LICENSED_TIERS", ())) == sorted(PAID_PLANS + API_TIER_IDS)
    sources = (Path(__file__).resolve().parent.parent / "docs" / "sources.md").read_text(encoding="utf-8")
    section = sources.split("## Voice licences", 1)[1].split("\n## ", 1)[0]
    for needed in ("https://elevenlabs.io/pricing", "https://elevenlabs.io/terms-of-use", "2026-11-14",
                   "https://elevenlabs.io/docs/api-reference/user/subscription/get",
                   "https://www.microsoft.com/licensing/terms/productoffering/MicrosoftAzure/MCA",
                   "Starter, Creator, Pro, Scale, Business, Enterprise", "prebuilt neural voices"):
        assert needed in section


@pytest.mark.parametrize("status, licensed", [
    ("active", True), (" Active ", True), (None, True),               # None: the caller has no status to give
    ("trialing", False), ("incomplete", False), ("past_due", False), ("free", False), ("free_disabled", False),
    ("", False), ("not reported", False), ("canceled", False), (1, False),
])
def test_only_an_active_subscription_is_a_paid_one(status, licensed):
    from src import voice_licence
    try:
        ok, basis = voice_licence.licence("elevenlabs", "creator", status)
    except TypeError:                                    # before round 2 the table never read the status
        ok, basis = voice_licence.licence("elevenlabs", "creator")
    assert ok is licensed
    if not licensed:
        assert "not an active paid subscription" in basis
    record = {"provider": "elevenlabs", "tier": "creator", "licensed": True,
              **({} if status is None else {"plan_status": status})}
    assert voice_licence.record_licensed(record) is licensed


@pytest.mark.parametrize("quota, reason", [
    ({"tier": "creator", "status": "trialing", "used": 0, "limit": 100000}, "subscription status trialing"),
    ({"tier": "creator", "status": "past_due", "used": 0, "limit": 100000}, "subscription status past_due"),
    ({"tier": "zzz", "status": "active", "used": 0, "limit": 100000}, "ElevenLabs plan zzz not recognised"),
    ({"tier": "trial", "used": 0, "limit": 100000}, "ElevenLabs plan trial not recognised"),
])
def test_a_real_run_is_not_voiced_on_an_unpaid_or_unknown_elevenlabs_plan(monkeypatch, tmp_path, quota, reason):
    eng, calls = engine(monkeypatch)
    monkeypatch.setattr(eng, "voice_quota", lambda: dict(quota))
    held = real_take(eng, tmp_path)
    assert calls == [] and held is not None and reason in str(held)       # neither ElevenLabs nor Azure F0 was asked
    assert eng.last_provider == "" and not eng.last_licensed


def test_the_take_records_the_plan_and_its_status(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch)
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": "creator", "status": "active", "used": 0, "limit": 100000})
    assert real_take(eng, tmp_path) is None and calls == ["elevenlabs"]
    assert (eng.last_provider, eng.last_tier, getattr(eng, "last_plan_status", "missing"), eng.last_licensed) == (
        "elevenlabs", "creator", "active", True)
    from src.audio_provenance import inspect_audio_rights, record_voice
    voice = tmp_path / "voiceover.wav"
    voice.write_bytes(b"voice")
    try:
        record = record_voice(voice, "elevenlabs", True, "script", tier="creator", plan_status="trialing")
    except TypeError:
        record = {}
    assert record.get("plan_status") == "trialing" and record["commercial_license_recorded"] is False
    assert not inspect_audio_rights(voice, "voice", "", script="script")[0]


def test_the_subscription_call_gives_the_plan_and_status_as_the_table_reads_them(monkeypatch):
    """GET /v1/user/subscription (stand-in answer, the fields its API reference documents): `tier` is a free-form
    plan name, `status` one of trialing, active, incomplete, past_due, free, free_disabled."""
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v", azure_key="", azure_region="")
    answer = {"tier": " Creator ", "status": "Active", "character_count": 10, "character_limit": 100,
              "next_character_count_reset_unix": 5}
    monkeypatch.setattr("src.audio_engine.requests.get", lambda url, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: dict(answer)))
    quota = eng.voice_quota()
    assert (quota["tier"], quota.get("status"), quota["used"], quota["limit"]) == ("creator", "active", 10, 100)
    assert eng.eleven_licensed()
    for status in ("trialing", "past_due", "free", None):          # None: an answer with no status is not a paid state
        answer["status"] = status
        assert not eng.eleven_licensed()
    assert eng.voice_quota()["status"] == "not reported"
    answer.update(tier="free", status="free")
    assert not eng.eleven_licensed()


# ---------------------------------------------------------------- VOICE_REQUIRE_LICENSED=false is never silent

def test_with_the_gate_off_an_unlicensed_upload_is_on_the_record(monkeypatch, db, tmp_path):
    """Review 1, blocking 3: the outcome stays "published", with an audit event and the fact on the result."""
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True}, gate=False)
    reel = publisher.publish_reel(envelope, dry_run=False)
    short = publisher.publish_short(envelope, dry_run=False)
    assert reel["status"] == "published" and short["status"] == "published"
    uploader.publish.assert_called_once()
    uploader.publish_short.assert_called_once()
    # round 3: "allowed" before each upload, "published" once it returned published (newest first)
    assert audit_actions(db) == [("published_unlicensed_voice_gate_off", "published"),
                                 ("publish_unlicensed_voice_gate_off", "allowed")] * 2
    assert set(reel) == {"status", "channel", "post_urls", "post_ids", "results"}       # the results keep their shape
    assert short == {"status": "published", "post_urls": {"youtube": "https://y/1"}}
    for what in ("Reel", "YouTube Short"):
        said = getattr(publisher, "voice_notes", {}).get("r1", {}).get(what, "")
        assert said.startswith(f"{what} posted with a voice not commercially licensed (azure: Azure Speech, tier not recorded")
        assert said.endswith("VOICE_REQUIRE_LICENSED=false: the voice licence gate is off")


@pytest.mark.parametrize("voice, dry_run", [({"provider": "elevenlabs", "licensed": True}, False),
                                            ({"provider": "azure", "licensed": True}, True), (None, True)])
def test_with_the_gate_off_a_licensed_upload_and_a_dry_run_say_nothing(monkeypatch, db, tmp_path, voice, dry_run):
    publisher, _, envelope = a_master(monkeypatch, db, tmp_path, voice, gate=False)
    reel = publisher.publish_reel(dict(envelope, media={}, metadata={}), dry_run=dry_run)
    short = publisher.publish_short(envelope, dry_run=dry_run)
    assert reel["status"] == short["status"] == ("simulated" if dry_run else "published")
    assert not getattr(publisher, "voice_notes", {}).get("r1") and audit_actions(db) == []


def published_with_the_gate_off(db, tmp_path, monkeypatch, v2, prepare=None, status="published"):
    """publish_run on an Azure master with the gate off: the real publisher and gate, a stand-in uploader
    (`prepare(uploader)` makes it end another way; `status` is the Reel's outcome then)."""
    import main
    from src import release
    from tests.test_ui02_receipt_and_v2_card import FakeBot
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True},
                                             run_id="ui02", gate=False)
    if prepare:
        prepare(uploader)
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", v2)
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / name).write_bytes(b"offline fixture")
    db.update_run("ui02", "approved", master_video_path=tmp_path / "master.mp4", thumbnail_cover_path=tmp_path / "cover.jpg",
                  thumbnail_grid_path=tmp_path / "cover.jpg",
                  script_payload={"title": "Fixture", "caption": "c", "topic_id": "01_topic"})
    monkeypatch.setattr(publisher, "build_envelope", lambda *a, **k: dict(envelope))
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.telegram_bot, coord.publisher, alerts = db, FakeBot(), publisher, []
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    coord._alert = lambda text, *a, **k: alerts.append(text)
    coord._facebook_finishing = lambda run_id: None
    result = coord.publish_run("ui02")
    assert result["status"] == status
    uploader.publish.assert_called_once()
    return coord, alerts


GATE_OFF_FACT = "posted with a voice not commercially licensed (azure: Azure Speech, tier not recorded"


def test_the_publish_receipt_says_a_post_went_out_unlicensed_with_the_gate_off(db, tmp_path, monkeypatch):
    from tests.test_ui02_receipt_and_v2_card import _final
    coord, _ = published_with_the_gate_off(db, tmp_path, monkeypatch, v2=True)
    receipt = _final(coord.telegram_bot)
    assert "⚠️ Reel " + GATE_OFF_FACT in receipt and "VOICE_REQUIRE_LICENSED=false" in receipt
    assert "⚠️ Reel " + GATE_OFF_FACT in coord.telegram_bot.stamp        # the review card's stamp says it too


def test_the_published_notice_says_it_too_without_the_v2_receipt(db, tmp_path, monkeypatch):
    _, alerts = published_with_the_gate_off(db, tmp_path, monkeypatch, v2=False)
    assert alerts[0].startswith("✅ <b>Published</b>") and "⚠️ Reel " + GATE_OFF_FACT in alerts[0]


def health_checker(monkeypatch):
    from src.ops import PreFlightHealthChecker
    checker = PreFlightHealthChecker()
    for name in ("check_ffmpeg", "check_ffprobe", "check_disk_space", "check_fonts", "check_network"):
        monkeypatch.setattr(checker, name, lambda: (True, "ok"))
    monkeypatch.setattr(config, "BACKUP_ENABLED", False, raising=False)
    return checker


def test_the_pre_flight_says_the_gate_is_off_for_a_real_run_only(monkeypatch):
    from src.audio_engine import AudioEngine as Engine
    live_settings(monkeypatch)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k" * 20)
    monkeypatch.setattr(Engine, "voice_quota", lambda self: {"tier": "creator", "status": "active", "used": 0, "limit": 10**5})
    checker = health_checker(monkeypatch)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", False)
    try:
        real = checker.run_all(require_live=False, dry_run=False)
    except TypeError:                                           # before round 2 the pre-flight was not told
        real = checker.run_all(require_live=False)
    note = real["details"].get("voice gate")
    assert note and note["msg"].startswith("VOICE_REQUIRE_LICENSED=false: the voice licence gate is off")
    assert note["required"] is False and real["all_passed"]          # said, never a reason not to start
    assert "voice gate" not in checker.run_all(require_live=False, dry_run=True)["details"]      # a dry run: nothing
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    assert "voice gate" not in checker.run_all(require_live=False, dry_run=False)["details"]


def test_the_status_screen_says_the_gate_is_off_on_a_real_service_only(db, monkeypatch):
    from src import control
    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "")
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", False)
    line = "🚨 <b>Licence alert:</b> VOICE_REQUIRE_LICENSED=false: the voice licence gate is off"

    def status():
        return control.status_text(db, None, None)
    control.record_run_mode(db, False)                 # the bot or the daemon started with real runs
    assert line in status() and "Set VOICE_REQUIRE_LICENSED=true in .env and restart" in status()
    control.record_run_mode(db, True)                  # a dry-run service
    assert "voice licence gate" not in status()
    control.record_run_mode(db, False)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    assert "voice licence gate" not in status()


def test_a_real_service_says_at_its_start_that_the_gate_is_off_as_one_needs_you_item(db, tmp_path, monkeypatch):
    import main
    from src import notifications, telegram_ui
    from src.layout_repost import NEEDS_MARKS
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", False)
    bot = main.PipelineCoordinator(db).telegram_bot
    say = getattr(main, "say_voice_gate", lambda *a, **k: None)
    say(db, bot, True)                                                  # a dry-run service: nothing
    assert needs_items(db) == []
    say(db, bot, False)
    say(db, bot, False)                                                 # a restart: replaced, never stacked
    items = needs_items(db)
    assert [i["dedupe_key"] for i in items] == ["voice-gate-off"]
    item = items[0]
    assert str(item["emoji"]).startswith(NEEDS_MARKS) and item["title"] == "Voice licence gate is off"
    assert len(item["title"]) <= notifications.TITLE_LIMIT
    assert "VOICE_REQUIRE_LICENSED=false" in item["body"] and "Set VOICE_REQUIRE_LICENSED=true" in item["body"]
    actions = json.loads(item["actions_json"]) if isinstance(item.get("actions_json"), str) else item.get("actions")
    assert actions and all(len(a["text"]) <= telegram_ui.BUTTON_LIMIT for a in actions)
    assert [a["callback_data"] for a in actions] == ["cstat:"]         # Status, where the line is
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    say(db, bot, False)                                                 # switched back on: the item resolves itself
    assert needs_items(db) == []


# ---------------------------------------------------------------- pre-flight: a dry run needs no licensed voice

def test_a_dry_run_started_with_require_live_accepts_an_azure_free_tier_key(monkeypatch):
    """Review 1, blocking 4: the bot starts every worker with --require-live, dry runs included."""
    live_settings(monkeypatch)                                  # an Azure key on F0, no ElevenLabs
    monkeypatch.setattr("src.ops.requests.post", lambda url, **kw: SimpleNamespace(status_code=200, ok=True, json=dict))
    monkeypatch.setattr("src.ops.requests.get", lambda url, **kw: SimpleNamespace(status_code=200, ok=True, json=dict))
    checker = health_checker(monkeypatch)
    try:
        dry = checker.run_all(require_live=True, dry_run=True)
    except TypeError:                                           # before round 2 the pre-flight was not told
        dry = checker.run_all(require_live=True)
    assert dry["all_passed"], dry["details"]                   # starts, as before the row
    real = checker.run_all(require_live=True)
    assert not real["all_passed"]
    assert "commercially licensed voice" in real["details"]["credentials"]["msg"]
    assert "No working commercially licensed voice" in real["details"]["services"]["msg"]


def test_a_dry_run_keeps_the_elevenlabs_rule_from_before_the_table(monkeypatch):
    from src.audio_engine import AudioEngine as Engine
    from src.ops import PreFlightHealthChecker
    live_settings(monkeypatch)
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k" * 20)
    monkeypatch.setattr(Engine, "voice_quota", lambda self: {"tier": "zzz", "status": "trialing"})

    def dry_check():
        try:
            return PreFlightHealthChecker().check_live_credentials(dry_run=True)[0]
        except TypeError:                                       # before round 2 the pre-flight was not told
            return PreFlightHealthChecker().check_live_credentials()[0]
    assert dry_check()
    assert not PreFlightHealthChecker().check_live_credentials()[0]
    monkeypatch.setattr(Engine, "voice_quota", lambda self: {"tier": "free"})
    assert not dry_check()


@pytest.mark.parametrize("dry_run", [True, False])
def test_run_cycle_tells_the_pre_flight_whether_the_run_is_a_dry_run(db, tmp_path, monkeypatch, dry_run):
    coord, _ = held_coordinator(db, tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(coord.health_checker, "run_all",
                        lambda **kw: asked.append(kw) or {"all_passed": True, "details": {}})
    coord.run_cycle("01_topic", auto_approve=False, dry_run=dry_run, require_live=True, run_id=f"run_01_topic_{int(dry_run)}")
    assert asked == [{"require_live": True, "dry_run": dry_run}]


# ---------------------------------------------------------------- the wording, the recovery, the real voice step

def test_the_recovery_names_buttons_that_exist():
    from pathlib import Path

    from src import improve, voice_licence
    root = Path(__file__).resolve().parent.parent
    assert improve.REASONS["sound"] == "Music / sound"
    recovery = getattr(voice_licence, "recovery", lambda on_card=False: "")
    for text in (recovery(), recovery(on_card=True)):
        assert "Regenerate › Music / sound" in text and "the script is kept" in text and "real run" in text
    assert "Resend review card" in recovery()
    assert '"♻️ Regenerate", "callback_data": f"regen:' in (root / "src" / "telegram_bot.py").read_text(encoding="utf-8")
    assert '"text": "Regenerate", "callback_data": f"rvg:' in (root / "src" / "review_panel.py").read_text(encoding="utf-8")
    assert '"📨 Resend review card", "callback_data": f"resend:' in (root / "main.py").read_text(encoding="utf-8")
    assert "sound" not in improve.SCRIPT_REASONS            # that reason keeps the script


def test_the_blocked_notice_names_the_recovery(db, tmp_path, monkeypatch):
    import main
    from src import release
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True}, run_id="ui02")
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / name).write_bytes(b"offline fixture")
    db.update_run("ui02", "approved", master_video_path=tmp_path / "master.mp4", thumbnail_cover_path=tmp_path / "cover.jpg",
                  thumbnail_grid_path=tmp_path / "cover.jpg",
                  script_payload={"title": "Fixture", "caption": "c", "topic_id": "01_topic"})
    monkeypatch.setattr(publisher, "build_envelope", lambda *a, **k: dict(envelope))
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.publisher, sent = db, publisher, []
    coord.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda *a, **k: None)
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    coord._alert = lambda text, *a, **k: sent.append((text, k))
    assert coord.publish_run("ui02")["status"] == "blocked_unlicensed"
    uploader.publish.assert_not_called()
    uploader.publish_short.assert_not_called()
    text, item = sent[0]
    assert "Tap Resend review card; then, on a real run, Regenerate › Music / sound" in text
    assert "📨 Resend review card" in [b["text"] for b in item["buttons"]]


def test_the_flag_names_the_recovery_and_what_approve_does(monkeypatch):
    from src import voice_licence
    old = {"provider": "azure", "licensed": True}
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    flag = voice_licence.overclaim_flag(old)
    assert "Approve will not post it." in flag and "On a real run, Regenerate › Music / sound voices it again" in flag
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", False)        # then "will not post" would be untrue
    flag = voice_licence.overclaim_flag(old)
    assert "Approve will not post it" not in flag and "VOICE_REQUIRE_LICENSED=false" in flag
    assert "a real post would go out with it" in flag


def test_the_real_voice_step_holds_a_real_run(db, tmp_path, monkeypatch):
    """The Reel's own voice step (main._produce_and_publish, through resume_run), with the real engine and stand-in
    providers: ElevenLabs on the Free plan, an Azure key on F0. Nothing is stubbed between the step and the hold."""
    import main
    from src.checkpoints import Checkpoints, file_inputs, topic_inputs
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    coord = main.PipelineCoordinator(db)
    eng, calls = engine(monkeypatch, eleven_tier="free")
    monkeypatch.setattr(eng, "_generate_edge_tts", lambda *a, **k: pytest.fail("a real run is never voiced by edge-tts"))
    coord.audio_engine = eng
    uploads = []
    monkeypatch.setattr(coord.publisher, "publish_reel", lambda *a, **k: uploads.append(a) or {"status": "published"})
    monkeypatch.setattr(coord, "_collect_findings", lambda *a, **k: None)
    monkeypatch.setattr(coord, "_upright_photos", lambda topic: set())
    monkeypatch.setattr(coord, "_screen_photos", lambda topic: None)
    coord.visual_engine = SimpleNamespace(get_scene_sequence=lambda t: [], hero_image=lambda m: None,
                                          assets_dir=tmp_path / "assets")
    monkeypatch.setattr(main.beat_images, "without_burned_in_text", lambda media, *a, **k: media)
    monkeypatch.setattr("src.image_review.strong_stems", lambda folder: set())
    monkeypatch.setattr("src.fact_evidence.current_fact_review", lambda payload: True)
    monkeypatch.setattr(coord, "_spoken_length_now", lambda saved: "")
    monkeypatch.setattr(coord, "_lines_against_pictures",
                        lambda topic, run_id, run_dir, dry_run, payload, *a, **k: payload)
    monkeypatch.setattr("src.audio_engine.measure_spoken_seconds", lambda *a, **k: None)
    topic = coord.topic_engine.get_topic_by_id("01_topic")
    run_dir = tmp_path / "r9"
    run_dir.mkdir()
    db.create_run("r9", "01_topic")
    db.update_run("r9", "failed", error="RenderError")
    script = "In 1963 a house wall gave way. Behind it lay a city cut into the rock, eighteen floors deep."
    saved = run_dir / "verified_script.json"
    saved.write_text(json.dumps({"title": "T", "topic_id": "01_topic", "narration_script": script, "caption": "c",
                                 "guardrail_passed": True}), encoding="utf-8")
    Checkpoints(run_dir).complete("script", {"topic": topic_inputs(topic), "engine": config.SCRIPT_ENGINE,
                                             "fact_sheet": file_inputs([config.DATA_DIR / "fact_sheets" / "01_topic.json"])},
                                  [saved])
    result = coord.resume_run("r9", dry_run=False)
    assert result["status"] == "held" and result["reason"] == "no_licensed_voice"
    assert calls == []                                                   # Azure F0 was never asked: nothing voiced
    assert not list(run_dir.glob("voiceover.*")) and not (run_dir / "voice.json").exists()
    assert uploads == [] and db.list_publications("r9") == []
    assert db.get_run("r9")["status"] == "failed" and db.get_run("r9")["error"].startswith("NoLicensedVoice: ")
    items = needs_items(db)
    assert [i["dedupe_key"] for i in items] == ["fail:r9"] and items[0]["title"] == "Held: no licensed voice"
    assert "Nothing was voiced by an unlicensed voice, approved or posted" in items[0]["body"]
    assert "ElevenLabs is on the Free plan" in items[0]["body"] and "Azure Speech F0" in items[0]["body"]
    actions = json.loads(items[0]["actions_json"]) if isinstance(items[0].get("actions_json"), str) else items[0].get("actions")
    assert actions and actions[0]["text"] == "Re-render"                # the script is kept: the notice says how


def test_prefer_azure_with_the_paid_tier_declared_voices_a_real_run_with_azure(monkeypatch, tmp_path):
    """Review 1, note 5: VOICE_PREFER=azure is still honoured where Azure is licensed (S0 declared)."""
    monkeypatch.setattr("src.audio_engine.VOICE_PREFER", "azure")
    eng, calls = engine(monkeypatch, eleven_tier="creator", declared="S0")
    assert real_take(eng, tmp_path) is None and calls == ["azure"]
    assert (eng.last_provider, eng.last_tier, eng.last_licensed) == ("azure", "s0", True)
    assert "prebuilt neural voices" in eng.last_licence_basis            # what S0 covers, said with the status


def test_ask_does_not_present_an_old_azure_masters_saved_note_as_licensed(db, tmp_path, monkeypatch):
    """Review 1, note 4: /ask's evidence (src/probe.py) passed the saved voice_note and voice.json on unread."""
    from src import probe
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "voice.json").write_text(json.dumps({"provider": "azure", "licensed": True}), encoding="utf-8")
    (tmp_path / "r1" / "release_report.json").write_text(json.dumps({"checks": [
        {"name": "voiceover_rights", "status": "pass", "detail": ""}, {"name": "asset_rights", "status": "pass"}]}),
        encoding="utf-8")
    db.create_run("r1", "01_topic")
    db.update_run("r1", "simulated", script_payload={"title": "T", "voice_note": "🎙️ Voice: Azure (🟢 Commercially Licensed)"})
    ev = probe.evidence(db, db.get_run("r1"))
    assert "Commercially Licensed" not in json.dumps(ev, ensure_ascii=False)
    assert FLAG in ev["review card notes"]["voice_note"]
    assert ev["voice.json"]["licensed"] is False and ev["voice.json"].get("licensed_as_saved") is True
    rights = {c["name"]: c for c in ev["release_report.json"]}
    assert rights["voiceover_rights"]["status"] == "needs_review" and rights["asset_rights"]["status"] == "pass"
    (tmp_path / "r1" / "voice.json").write_text(json.dumps({"provider": "elevenlabs", "licensed": True}), encoding="utf-8")
    ev = probe.evidence(db, db.get_run("r1"))                            # a licensed master: its records as they are
    assert "Commercially Licensed" in ev["review card notes"]["voice_note"] and ev["voice.json"]["licensed"] is True


def test_the_s0_entry_says_what_it_covers():
    from src import voice_licence
    assert "prebuilt neural voices only" in voice_licence.__doc__ and "custom" in voice_licence.__doc__
    assert voice_licence.licence("azure", "S0")[1].endswith("prebuilt neural voices")


# ================================================================ round 3 (review 2)
# ---------------------------------------------------------------- "Free plan" only when the plan is the Free plan

UNREAD = "ElevenLabs' plan could not be read (the subscription call failed or named no plan): no commercial licence confirmed"


@pytest.mark.parametrize("azure_key", [True, False])
@pytest.mark.parametrize("quota", [None, {}, {"tier": None}, {"tier": ""}, {"tier": None, "status": "not reported"}])
def test_a_plan_that_could_not_be_read_is_never_called_the_free_plan(monkeypatch, tmp_path, quota, azure_key):
    """Review 2, blocking 1: voice_quota() gives None when the subscription call fails (a timeout, a 401, a 5xx).
    The run is still held (fail closed), and its reason says what happened."""
    eng, calls = engine(monkeypatch)
    if not azure_key:                                   # production's shape: ElevenLabs only
        eng.azure_key = eng.azure_region = ""
    monkeypatch.setattr(eng, "voice_quota", lambda: quota)
    held = real_take(eng, tmp_path)
    assert held is not None and calls == [] and eng.last_provider == "" and not eng.last_licensed
    assert "Free plan" not in str(held)
    assert UNREAD in str(held)
    assert ("Azure Speech F0" in str(held)) is azure_key


def test_the_free_plan_is_still_called_the_free_plan(monkeypatch, tmp_path):
    eng, calls = engine(monkeypatch, eleven_tier="free")
    held = real_take(eng, tmp_path)
    assert held is not None and calls == []
    assert "ElevenLabs is on the Free plan (no commercial licence)" in str(held) and "could not be read" not in str(held)
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": "free", "status": "free", "used": 0, "limit": 10000})
    assert "ElevenLabs is on the Free plan (no commercial licence)" in str(real_take(eng, tmp_path))


def test_the_subscription_call_failing_holds_a_real_run_with_the_true_reason(monkeypatch, tmp_path):
    """The real voice_quota on a network fault (stand-in for requests.get: no call leaves the test)."""
    import requests
    eng = AudioEngine(eleven_key="k" * 20, eleven_voice="v", azure_key="", azure_region="")

    def timeout(url, **kw):
        raise requests.Timeout("timed out")
    monkeypatch.setattr("src.audio_engine.requests.get", timeout)
    monkeypatch.setattr(eng, "_generate_elevenlabs", lambda *a: pytest.fail("not voiced on an unread plan"))
    assert eng.voice_quota() is None
    held = real_take(eng, tmp_path)
    assert held is not None and "Free plan" not in str(held) and "could not be read" in str(held)


def test_the_live_probe_says_an_unread_plan_as_unread_not_as_an_empty_balance(monkeypatch):
    """The pre-flight's services line for a real run when the subscription call failed: before round 3 it read
    "(unknown plan, 0 characters left)", a balance nobody had read."""
    from src.audio_engine import AudioEngine as Engine
    live_settings(monkeypatch)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k" * 20)
    monkeypatch.setattr(config, "AZURE_SPEECH_KEY", "")
    monkeypatch.setattr(Engine, "voice_quota", lambda self: None)
    monkeypatch.setattr("src.ops.requests.get", lambda *a, **k: SimpleNamespace(status_code=200, json=dict, text=""))
    monkeypatch.setattr("src.ops.requests.post", lambda *a, **k: SimpleNamespace(status_code=200, json=dict, text=""))
    ok, msg = health_checker(monkeypatch).probe_live_services()
    assert not ok and "No working commercially licensed voice" in msg
    assert "ElevenLabs is not usable for a licensed voice: " + UNREAD in msg
    assert "0 characters left" not in msg and "Free plan" not in msg


# ---------------------------------------------------------------- the gate off: "posted" only once it is posted

def gate_off_record(db, publisher, run_id="r1"):
    """(audit events of the run oldest first as (action, status, reason), the notes kept for its receipt)."""
    events = [(e["action"], e["status"], json.loads(e["payload"]).get("reason", ""))
              for e in reversed(db.list_audit_logs(100)) if json.loads(e["payload"] or "{}").get("run_id") == run_id]
    return events, dict(getattr(publisher, "voice_notes", {}).get(run_id) or {})


def reel_fails(uploader):
    from src.publisher import PublishFailed
    uploader.publish.side_effect = PublishFailed("upload refused")


def reel_unknown(uploader):
    from src.publisher import PublishUnknown
    uploader.publish.side_effect = PublishUnknown("timed out at the last step")


def reel_not_set_up(uploader):
    from src.publisher import NotConfigured
    uploader.publish.side_effect = NotConfigured("no account")


def short_fails(uploader):
    from src.publisher import PublishFailed
    uploader.publish_short.side_effect = PublishFailed("upload refused")


def short_unknown(uploader):
    from src.publisher import PublishUnknown
    uploader.publish_short.side_effect = PublishUnknown("timed out")


def short_skipped(uploader):
    del uploader.publish_short                        # no account that can upload Shorts


@pytest.mark.parametrize("what, ending, status", [
    ("Reel", reel_fails, "failed"), ("Reel", reel_unknown, "unknown"), ("Reel", reel_not_set_up, "failed"),
    ("YouTube Short", short_fails, "failed"), ("YouTube Short", short_unknown, "unknown"),
    ("YouTube Short", short_skipped, "skipped"),
])
@pytest.mark.parametrize("voice", UNLICENSED_MASTERS)
def test_with_the_gate_off_an_upload_that_did_not_publish_is_never_recorded_as_posted(monkeypatch, db, tmp_path, voice,
                                                                                        what, ending, status):
    """Review 2, blocking 2: the gate's event is written before the upload. It says what is true then ("upload
    allowed"); nothing in the audit log and no kept note says "posted" for an upload that failed, was skipped or
    whose outcome is unknown."""
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, voice, gate=False)
    ending(uploader)
    result = (publisher.publish_reel if what == "Reel" else publisher.publish_short)(envelope, dry_run=False)
    assert result["status"] == status
    events, notes = gate_off_record(db, publisher)
    assert [(a, s) for a, s, _ in events] == [("publish_unlicensed_voice_gate_off", "allowed")]
    reason = events[0][2]
    assert reason.startswith(f"{what} upload allowed with a voice not commercially licensed (")
    assert reason.endswith("VOICE_REQUIRE_LICENSED=false: the voice licence gate is off")
    assert "posted" not in reason and notes == {}
    assert not any("posted" in json.dumps(e) for e in db.list_audit_logs(100))


def test_with_the_gate_off_a_published_upload_is_recorded_as_posted_after_it(monkeypatch, db, tmp_path):
    publisher, uploader, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True}, gate=False)
    order = []
    uploader.publish.side_effect = lambda env: order.append(len(db.list_audit_logs(100))) or {"post_urls": {}}
    assert publisher.publish_reel(envelope, dry_run=False)["status"] == "published"
    assert order == [1]                                  # at the upload only "allowed" was on the record
    events, notes = gate_off_record(db, publisher)
    assert [(a, s) for a, s, _ in events] == [("publish_unlicensed_voice_gate_off", "allowed"),
                                              ("published_unlicensed_voice_gate_off", "published")]
    assert events[0][2].startswith("Reel upload allowed with") and "posted" not in events[0][2]
    assert events[1][2].startswith("Reel posted with a voice not commercially licensed (azure: ")
    assert notes == {"Reel": events[1][2]}
    reel_fails(uploader)                                 # a later attempt that fails: the kept note does not outlive it
    assert publisher.publish_reel(envelope, dry_run=False)["status"] == "failed"
    events, notes = gate_off_record(db, publisher)
    assert notes == {} and [s for _, s, _ in events] == ["allowed", "published", "allowed"]


def test_a_reel_one_publisher_posted_before_another_ended_unknown_is_recorded_as_posted(monkeypatch, db, tmp_path):
    """The outcome is "unknown" and stays so; what one account did post is still on the record, with its channel."""
    from unittest.mock import MagicMock

    from src.publisher import MetaPublisher, PublishUnknown
    _, first, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True}, gate=False)
    first.name, first.platforms = "first", ("facebook",)
    second = MagicMock()
    second.name, second.platforms = "second", ("instagram",)
    second.publish.side_effect = PublishUnknown("timed out")
    publisher = MetaPublisher(db, [first, second])
    assert publisher.publish_reel(envelope, dry_run=False)["status"] == "unknown"
    events = [json.loads(e["payload"]) for e in db.list_audit_logs(100) if e["status"] == "published"]
    assert len(events) == 1 and events[0]["channel"] == "first" and events[0]["reason"].startswith("Reel posted with")


def test_the_receipt_says_posted_only_for_the_upload_that_published(db, tmp_path, monkeypatch):
    """publish_run, gate off, the Reel up and its Short refused: the Reel's line is there, none for the Short."""
    from tests.test_ui02_receipt_and_v2_card import _final
    coord, _ = published_with_the_gate_off(db, tmp_path, monkeypatch, v2=True, prepare=short_fails)
    receipt = _final(coord.telegram_bot)
    assert "⚠️ Reel " + GATE_OFF_FACT in receipt and "YouTube Short posted with" not in receipt
    events, notes = gate_off_record(db, coord.publisher, "ui02")
    assert list(notes) == ["Reel"]
    assert [r.split(" with ", 1)[0] for _, _, r in events] == ["Reel upload allowed", "Reel posted",
                                                               "YouTube Short upload allowed"]


@pytest.mark.parametrize("ending, status", [(reel_fails, "failed"), (reel_unknown, "unknown")])
def test_no_operator_text_says_posted_with_for_a_reel_that_did_not_publish(db, tmp_path, monkeypatch, ending, status):
    coord, alerts = published_with_the_gate_off(db, tmp_path, monkeypatch, v2=False, prepare=ending, status=status)
    bot = coord.telegram_bot
    said = "\n".join(str(t) for t in [*alerts, *bot.sent, *bot.edits, *bot.notes, getattr(bot, "stamp", "")])
    assert "posted with a voice" not in said
    events, notes = gate_off_record(db, coord.publisher, "ui02")
    assert notes == {} and [s for _, s, _ in events] == ["allowed"]


def test_an_unknown_upload_the_operator_confirms_is_recorded_as_posted_then(db, tmp_path, monkeypatch):
    coord, _ = published_with_the_gate_off(db, tmp_path, monkeypatch, v2=False, prepare=reel_unknown, status="unknown")
    assert db.get_run("ui02")["status"] == "publish_unknown"
    answer = coord.confirm_posted("ui02")
    assert answer.startswith("✅ Recorded as published") and "⚠️ Reel " + GATE_OFF_FACT in answer
    events, _ = gate_off_record(db, coord.publisher, "ui02")
    assert [(a, s) for a, s, _ in events] == [("publish_unlicensed_voice_gate_off", "allowed"),
                                              ("published_unlicensed_voice_gate_off", "published")]
    confirmed = [json.loads(e["payload"]) for e in db.list_audit_logs(100) if e["status"] == "published"]
    assert confirmed[0].get("confirmed_by") == "operator"


def test_confirming_a_licensed_reel_says_nothing_about_the_voice(db, tmp_path, monkeypatch):
    import main
    publisher, _, _ = a_master(monkeypatch, db, tmp_path, {"provider": "elevenlabs", "licensed": True}, gate=False)
    db.update_run("r1", "publish_unknown")
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.publisher = db, publisher
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    assert "voice" not in coord.confirm_posted("r1") and audit_actions(db) == []


# ---------------------------------------------------------------- the status screen: no Reels promised on an unlicensed plan

def status_voice_lines(monkeypatch, quota, gate=True, azure_tier=None):
    from src import control
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", gate)
    monkeypatch.setattr("src.config.AZURE_SPEECH_KEY", "a" * 32 if azure_tier else "")
    if azure_tier:
        monkeypatch.setattr("src.config.AZURE_SPEECH_REGION", "eastus")
        monkeypatch.setattr(config, "AZURE_SPEECH_TIER", azure_tier, raising=False)
        monkeypatch.setattr(control.requests, "post", lambda url, **kw: SimpleNamespace(status_code=200))
    return control.quota_lines(quota)


@pytest.mark.parametrize("quota, basis", [
    ({"tier": "free", "status": "free", "used": 0, "limit": 10000, "resets": None},
     "ElevenLabs Free plan: no commercial licence"),
    ({"tier": "creator", "status": "past_due", "used": 0, "limit": 100000, "resets": None},
     "ElevenLabs creator plan, subscription status past_due: not an active paid subscription"),
    ({"tier": "zzz", "status": "active", "used": 0, "limit": 100000, "resets": None},
     "ElevenLabs plan zzz not recognised: no commercial licence recorded"),
])
def test_the_status_screen_promises_no_reels_on_an_unlicensed_plan(monkeypatch, quota, basis):
    """Review 2, "Not done" 1: "(~133 Reels)" for a plan whose every real run is held, with no alert."""
    lines = status_voice_lines(monkeypatch, quota)
    text = "\n".join(lines)
    assert "Reels)" not in text and f"{quota['limit']:,} characters left" in lines[0]
    assert lines[0].startswith("⚠️ ElevenLabs (") and "Not used for real runs" in lines[0]
    alerts = [line for line in lines if "Licence alert" in line]
    assert len(alerts) == 1 and basis in alerts[0]                        # one alert: the basis and the consequence
    assert "Real runs are held (a licensed voice is required)" in alerts[0]
    low = status_voice_lines(monkeypatch, {**quota, "used": quota["limit"] - 100})   # nearly empty too: still one alert
    assert sum("Licence alert" in line for line in low) == 1 and "Reels)" not in "\n".join(low)


@pytest.mark.parametrize("tier", ["creator", "growing_business", "scale_2024_08_10"])
def test_the_status_screen_keeps_the_count_on_a_licensed_plan(monkeypatch, tier):
    lines = status_voice_lines(monkeypatch, {"tier": tier, "status": "active", "used": 0, "limit": 100000, "resets": None})
    assert lines == [f"ElevenLabs ({tier}): 100,000 of 100,000 characters left (~133 Reels)"]


def test_the_status_screen_says_what_happens_instead_with_a_licensed_azure_tier_or_the_gate_off(monkeypatch):
    quota = {"tier": "free", "status": "free", "used": 0, "limit": 10000, "resets": None}
    with_s0 = status_voice_lines(monkeypatch, quota, azure_tier="S0")
    alerts = [line for line in with_s0 if "Licence alert" in line]
    assert len(alerts) == 1 and "Real runs are voiced by Azure Speech (eastus) instead" in alerts[0]
    assert "held" not in "\n".join(with_s0) and "Reels)" not in "\n".join(with_s0)
    off = status_voice_lines(monkeypatch, quota, gate=False)
    alerts = [line for line in off if "Licence alert" in line]
    assert len(alerts) == 1 and "ElevenLabs Free plan: no commercial licence" in alerts[0]
    assert "The licence gate is off (VOICE_REQUIRE_LICENSED=false): real runs are voiced on this plan" in alerts[0]
    assert "held" not in "\n".join(off) and "Reels)" not in "\n".join(off)   # nothing is held with the gate off
    off_f0 = status_voice_lines(monkeypatch, quota, gate=False, azure_tier="F0")
    assert "When it runs out: runs use Azure Speech (eastus), which has no commercial licence on its tier." in off_f0[0]


# ---------------------------------------------------------------- the table lists API tier ids

@pytest.mark.parametrize("tier, plan", [("growing_business", "Scale"), ("scale_2024_08_10", "Business"),
                                        (" Growing_Business ", "Scale")])
def test_the_api_tier_ids_of_scale_and_business_are_licensed(monkeypatch, db, tmp_path, tier, plan):
    """Review 2, note 1: the pricing page's own "Choose" links carry tier=growing_business for Scale and
    tier=scale_2024_08_10 for Business; an account on either was held as "not recognised"."""
    import importlib.util
    from pathlib import Path
    assert importlib.util.find_spec("src.voice_licence") is not None
    from src import voice_licence
    assert voice_licence.licence("elevenlabs", tier, "active")[0] is True
    assert voice_licence.licence("elevenlabs", tier, "past_due")[0] is False            # only while active
    eng, calls = engine(monkeypatch)
    monkeypatch.setattr(eng, "voice_quota", lambda: {"tier": tier.strip().lower(), "status": "active", "used": 0, "limit": 10**6})
    assert real_take(eng, tmp_path) is None and calls == ["elevenlabs"] and eng.last_licensed
    sources = (Path(__file__).resolve().parent.parent / "docs" / "sources.md").read_text(encoding="utf-8")
    section = sources.split("## Voice licences", 1)[1].split("\n## ", 1)[0]
    assert f"`tier={tier.strip().lower()}` (Choose {plan})" in section and "API tier ids" in section
    assert "tier ids" in voice_licence.__doc__ and tier.strip().lower() in voice_licence.__doc__


# ---------------------------------------------------------------- the review panel's rights mark under the flag

def test_the_review_panel_does_not_tick_rights_under_the_voice_flag(db, tmp_path, monkeypatch):
    """Review 2, note 3: a release report saved before the row still passes the voice's rights; the facts line read
    "rights ✅" one line under "Voice not commercially licensed"."""
    flagged, _, _ = panel_review(db, tmp_path, monkeypatch, None)             # the real Azure master, saved as licensed
    assert "Voice not commercially licensed</b>" in flagged
    assert "rights ⚠️" in flagged and "rights ✅" not in flagged


def test_the_review_panel_keeps_the_rights_tick_for_a_licensed_master(db, tmp_path, monkeypatch):
    text, _, _ = panel_review(db, tmp_path, monkeypatch, {"provider": "elevenlabs", "tier": "creator", "licensed": True})
    assert "rights ✅" in text and "rights ⚠️" not in text


@pytest.mark.parametrize("gate", [True, False])
def test_the_review_panel_shows_the_voice_flag_and_the_sensitivity_chip_together(tmp_path, monkeypatch, gate):
    """Review 2, blocking 3 (CI run 43681536645): the flag, first among the notes and some 250 characters long (360
    with the gate off), used up the panel's room and the "Sensitive topic" chip QF-73 requires fell into "…and N
    more". A sensitive topic whose master was voiced by Azure shows both, whole, with the gate on and off."""
    from src import telegram_bot, topic_proposer
    from src.db import PipelineDatabase
    from src.notifications import plain
    from tests import test_ui01e_review_panel as panel
    bank = tmp_path / "topics.json"
    bank.write_text(json.dumps({"total": 2, "topics": [
        {"id": str(i), "title": f"Subject {i}", "hook": "Why build 40 rooms underground?", "location": {},
         "sources": [{"url": "https://source.example/record"}], "region_group": "sample",
         "sensitivity": ["Human remains"] if i == 0 else []} for i in range(2)]}), encoding="utf-8")
    db = PipelineDatabase(tmp_path / "sample.db", bank)
    for name, value in (("TELEGRAM_CLEAN_CHAT", True), ("PUSH_ALERT_SECONDS", 8.0), ("REVIEW_UNDO_MINUTES", 10.0),
                        ("REVIEW_APPROVE_UNDO_LEAD_MINUTES", 15.0), ("VOICE_REQUIRE_LICENSED", gate)):
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(telegram_bot, "_video_dimensions", lambda path: {"width": 720, "height": 1280})
    bot, fake = panel.make_bot(db, monkeypatch)
    root, payload = panel.a_review(db, tmp_path, monkeypatch, "sens")
    assert payload["topic_id"] == "0" and json.loads((root / "voice.json").read_text(encoding="utf-8"))["provider"] == "azure"
    payload["topic_origin_note"] = topic_proposer.origin_note(db.get_topic_by_id("0"))
    db.update_run("sens", "pending_approval", script_payload=payload)
    caption = panel.caption(fake, panel.deliver(bot, db, root, payload, "sens"))
    summary = plain(caption)
    assert "Sensitive topic · Human remains" in summary                    # QF-73's chip, whole
    flag = ("⚠️ Voice: voice not commercially licensed (Azure Speech, tier not recorded: no commercial licence "
            "recorded). " + ("On a real run, " if gate else "Set VOICE_REQUIRE_LICENSED=true and restart; then, on a real run, ")
            + "Regenerate › Music / sound re-voices it.")
    assert flag in summary                                                 # the flag, whole
    notes = summary.split("Notes to check", 1)[1].split("• ")
    assert notes[1].strip() == flag and "Sensitive topic · Human remains" in notes[2]   # before any note that may be cut
    assert "Voice not commercially licensed · " + ("Approve will not post it" if gate else "the licence gate is off") in summary
    assert len(summary) <= 1024


def test_the_panels_short_flag_names_a_reason_that_exists_and_the_cards_keep_the_whole_sentence(monkeypatch):
    import importlib.util
    assert importlib.util.find_spec("src.voice_licence") is not None
    from src import voice_licence
    from src.improve import REASONS
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    record = {"provider": "azure", "licensed": True}
    try:
        short = voice_licence.overclaim_flag(record, short=True)
    except TypeError:                                                      # before round 3 there was one form
        short = voice_licence.overclaim_flag(record)
    whole = voice_licence.overclaim_flag(record)
    assert len(short) <= 170 and f"Regenerate › {REASONS['sound']} re-voices it." in short
    assert "Approve will not post it." in whole and "the script is kept" in whole and len(whole) > len(short)
    assert voice_licence.overclaim_flag({"provider": "elevenlabs", "licensed": True}, short=True) == ""


# ---------------------------------------------------------------- the start notice: no blanket catch, no marker

def test_the_start_notice_never_stops_a_start_and_carries_no_lint_marker(db, monkeypatch):
    import inspect
    import sqlite3

    import main
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", False)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    say = getattr(main, "say_voice_gate", None)
    assert say is not None
    say(db, SimpleNamespace(notify=locked), False)                        # logged, not raised
    source = inspect.getsource(say)
    assert "noqa" not in source and "except Exception" not in source
