"""QF-110: an approved booking posts after a code update and a clean-up, and a block is said once, in plain words.

Production (2026-11-02 to 11-12): one approved Reel never posted. Its fact review was invalidated by a code update
(the checkers' rules fingerprint moved), 49 of 51 run folders lost their parallax clips to the pre-run purge, one
blocked booking raised out of post_due and so stopped every other due post, and the error was said about once a
minute. Each of those is reproduced here, and every real block (a changed fact sheet, changed copy, a missing or
altered master, failing rights evidence) is shown to still block."""
import json
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import main as main_module
from src import fact_evidence as fe

try:
    from src.release import ReleaseBlocked
except ImportError:   # the code before QF-110: a stand-in, so each test fails on its own assertion there
    class ReleaseBlocked(ValueError):
        pass

AUDIT = {"claim": "mocked", "where": "title", "fact_ids": [], "claim_type": "rhetorical_framing", "verdict": "traced",
         "note": ""}


# ------------------------------------------------------------------ facts: content-bound, not code-bound
def _mock_checkers(monkeypatch, trace=True, evidence="pass", calls=None):
    from src import trace_check

    def check(payload, sheet):
        if calls is not None:
            calls.append("trace")
        return trace, 100.0, [AUDIT], "re-check trace"

    def evidence_check(payload, sheet):
        if calls is not None:
            calls.append("evidence")
        return evidence, 100.0, [dict(AUDIT, verdict="supported", quote_ids=[])], "re-check evidence"
    monkeypatch.setattr(trace_check, "check", check)
    monkeypatch.setattr(trace_check, "evidence_check", evidence_check)
    monkeypatch.setattr(trace_check, "checker_models", lambda: {"trace_check": "mock-trace", "evidence_check": "mock-ev"})


def _approved_run(tmp_path, checked_stamp):
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    (run_dir / "x_master.mp4").write_bytes(b"master")
    (run_dir / "thumbnail_cover.jpg").write_bytes(b"cover")
    sheet = tmp_path / "sheet.json"
    sheet.write_text('{"facts": []}', encoding="utf-8")
    payload = {"guardrail_passed": True, "title": "Approved title", "narration_script": "Approved words.",
               "caption": "Approved caption"}
    checked_stamp(payload, sheet)
    run = {"run_id": "run_x", "master_video_path": str(run_dir / "x_master.mp4"),
           "thumbnail_cover_path": str(run_dir / "thumbnail_cover.jpg"), "script_payload": payload}
    return run, sheet


def _code_update(monkeypatch):
    """The checkers' prompts change in a release after the approval: the stamp's rules fingerprint is now old."""
    monkeypatch.setattr(fe, "review_rules_fingerprint", lambda: "f" * 64)


def _facts(run, monkeypatch):
    """The release gate's facts check as write_release_report records it (media measurement stubbed out)."""
    from src import release
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no ffprobe here")))
    report = release.write_release_report(run)
    return next(c for c in report["checks"] if c["name"] == "facts")


def test_a_review_approved_under_old_rules_posts_after_a_recheck_of_the_same_content(tmp_path, monkeypatch,
                                                                                       checked_stamp):
    run, _ = _approved_run(tmp_path, checked_stamp)
    assert _facts(run, monkeypatch)["status"] == "pass"
    _code_update(monkeypatch)
    payload = run["script_payload"]
    assert fe.fact_review_problem(payload)          # the production failure: stale under the new code
    assert fe.drift_only(payload)                   # ... only because the code changed
    assert _facts(run, monkeypatch)["status"] == "fail"   # not re-checked yet: still blocked
    from src.release import _approved_now
    approved = _approved_now(run)
    calls = []
    _mock_checkers(monkeypatch, calls=calls)
    rec = fe.run_recheck(payload, approved)
    (tmp_path / "run_x" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    facts = _facts(run, monkeypatch)
    assert facts["status"] == "pass" and "Checked again under the current rules" in facts["detail"]
    assert calls == ["trace", "evidence"]
    assert "fact_review" in payload and payload["fact_review"]["review_rules_fingerprint"] != "f" * 64  # untouched


def test_a_changed_fact_sheet_still_blocks_even_with_a_recheck(tmp_path, monkeypatch, checked_stamp):
    run, sheet = _approved_run(tmp_path, checked_stamp)
    _code_update(monkeypatch)
    from src.release import _approved_now
    _mock_checkers(monkeypatch)
    rec = fe.run_recheck(run["script_payload"], _approved_now(run))
    (tmp_path / "run_x" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    sheet.write_text('{"facts": ["changed after approval"]}', encoding="utf-8")
    assert not fe.drift_only(run["script_payload"])
    assert _facts(run, monkeypatch)["status"] == "fail"


def test_changed_public_copy_still_blocks_even_with_a_recheck(tmp_path, monkeypatch, checked_stamp):
    run, _ = _approved_run(tmp_path, checked_stamp)
    _code_update(monkeypatch)
    from src.release import _approved_now
    _mock_checkers(monkeypatch)
    rec = fe.run_recheck(run["script_payload"], _approved_now(run))
    (tmp_path / "run_x" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    run["script_payload"]["caption"] = "A caption nobody approved"
    facts = _facts(run, monkeypatch)
    assert facts["status"] == "fail"


def test_a_failing_recheck_blocks_and_is_kept_so_it_is_not_paid_again(tmp_path, monkeypatch, checked_stamp):
    run, _ = _approved_run(tmp_path, checked_stamp)
    _code_update(monkeypatch)
    from src.release import _approved_now
    _mock_checkers(monkeypatch, evidence="fail")
    approved = _approved_now(run)
    rec = fe.run_recheck(run["script_payload"], approved)
    assert rec["passed"] is False and fe.recheck_matches(run["script_payload"], rec, approved)
    (tmp_path / "run_x" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    assert "did not pass" in _facts(run, monkeypatch)["detail"]


def test_an_edited_recheck_record_is_refused(tmp_path, monkeypatch, checked_stamp):
    run, _ = _approved_run(tmp_path, checked_stamp)
    _code_update(monkeypatch)
    from src.release import _approved_now
    _mock_checkers(monkeypatch, evidence="fail")
    rec = fe.run_recheck(run["script_payload"], _approved_now(run))
    rec["passed"] = True                      # forged
    (tmp_path / "run_x" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    assert _facts(run, monkeypatch)["status"] == "fail"


def test_the_coordinator_rechecks_only_the_copy_on_the_approved_card(tmp_path, monkeypatch, checked_stamp, db):
    from src.telegram_state import initialize, put, review_fingerprint
    run, _ = _approved_run(tmp_path, checked_stamp)
    db.create_run("run_x", "01_topic")
    db.update_run("run_x", "scheduled", script_payload=run["script_payload"],
                  master_video_path=run["master_video_path"], thumbnail_cover_path=run["thumbnail_cover_path"])
    initialize(db)
    db.record_approval("run_x", "01_topic", 77)
    put(db, "review-card", "run_x:77", review_fingerprint(db, "run_x"))
    db.update_approval_decision("run_x", "approved")
    _code_update(monkeypatch)
    calls = []
    _mock_checkers(monkeypatch, calls=calls)
    c = main_module.PipelineCoordinator.__new__(main_module.PipelineCoordinator)
    c.db = db
    c._alert = lambda text, buttons=None: None
    c._recheck_facts(db.get_run("run_x"))
    c._recheck_facts(db.get_run("run_x"))          # once per copy and rules: never paid twice
    assert calls == ["trace", "evidence"] and (tmp_path / "run_x" / fe.RECHECK_FILE).is_file()
    assert _facts(db.get_run("run_x"), monkeypatch)["status"] == "pass"
    # copy changed after approval: no re-check is made for it, and the gate blocks
    (tmp_path / "run_x" / fe.RECHECK_FILE).unlink()
    payload = dict(run["script_payload"], title="Not the approved title")
    db.update_run("run_x", "scheduled", script_payload=payload)
    c._recheck_facts(db.get_run("run_x"))
    assert calls == ["trace", "evidence"] and not (tmp_path / "run_x" / fe.RECHECK_FILE).exists()


# ------------------------------------------------------------------ provenance: the master is the unit of trust
@pytest.fixture
def made_run(tmp_path):
    """A run folder as the pipeline leaves it: a photo outside the run, its parallax clip and lineage in the run."""
    from src.credits import generate_provenance_ledger
    from src.lineage import record_derived
    photo = tmp_path / "assets" / "photo.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"licensed photo")
    run_dir = tmp_path / "run_y"
    (run_dir / "parallax").mkdir(parents=True)
    clip = run_dir / "parallax" / "scene_01.mp4"
    clip.write_bytes(b"clip")
    record_derived(clip, [photo], "parallax")
    master = run_dir / "y_master.mp4"
    master.write_bytes(b"master")
    vo = run_dir / "voiceover.mp3"
    vo.write_bytes(b"voice")
    ledger = generate_provenance_ledger(run_dir, {"id": "t"}, {}, master, vo_path=vo, media_items=[clip])
    return SimpleNamespace(run_dir=run_dir, ledger=ledger, photo=photo, clip=clip, master=master, vo=vo)


def test_missing_parallax_with_a_verified_master_passes_provenance(made_run):
    import shutil

    from src.video_qa import provenance_problems, verify_provenance_integrity
    assert verify_provenance_integrity(made_run.ledger)
    shutil.rmtree(made_run.run_dir / "parallax")      # the production clean-up
    assert not verify_provenance_integrity(made_run.ledger)   # the strict identity check is unchanged
    found = provenance_problems(made_run.ledger)
    assert found["problems"] == [] and found["missing_intermediates"] == 2   # the clip and its lineage record


def test_a_missing_or_altered_master_still_blocks(made_run):
    from src.video_qa import provenance_problems
    made_run.master.write_bytes(b"another video")
    assert any("approved video" in p and "changed" in p for p in provenance_problems(made_run.ledger)["problems"])
    made_run.master.unlink()
    assert any("approved video" in p and "missing" in p for p in provenance_problems(made_run.ledger)["problems"])


def test_rights_evidence_still_must_verify(made_run):
    import shutil

    from src.video_qa import provenance_problems
    shutil.rmtree(made_run.run_dir / "parallax")
    made_run.photo.write_bytes(b"swapped photo")      # the source photo the licence is for
    assert provenance_problems(made_run.ledger)["problems"]
    made_run.photo.write_bytes(b"licensed photo")
    made_run.vo.unlink()                              # the narration, whose rights the gate inspects
    assert any("narration" in p for p in provenance_problems(made_run.ledger)["problems"])


def test_a_clip_still_on_disk_must_match_its_record(made_run):
    from src.video_qa import provenance_problems
    made_run.clip.write_bytes(b"edited clip")
    assert provenance_problems(made_run.ledger)["problems"]


def test_the_gate_accepts_freed_scene_clips_in_the_composition_record_only(made_run):
    import shutil

    from src.release import _freed_clip
    shutil.rmtree(made_run.run_dir / "parallax")
    assert _freed_clip("scenes", made_run.clip, made_run.run_dir)
    assert not _freed_clip("voiceover", made_run.vo, made_run.run_dir)        # never the narration
    assert not _freed_clip("scenes", made_run.photo, made_run.run_dir)        # nor a photo outside the run


# ------------------------------------------------------------------ post_due: one booking never stops another
@pytest.fixture
def two_bookings(db, tmp_path, monkeypatch):
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    c = main_module.PipelineCoordinator.__new__(main_module.PipelineCoordinator)
    c.db, c.schedule_posts, c.only_promoted = db, True, False
    c.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    c.alerts = []
    c._alert = lambda text, buttons=None: c.alerts.append((text, buttons))
    slot = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    for rid in ("run_bad", "run_good"):
        db.create_run(rid, "01_topic")
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "m.mp4").write_bytes(b"0")
        db.update_run(rid, "scheduled", master_video_path=tmp_path / rid / "m.mp4")
        db.add_scheduled_post(rid, "reel", slot.isoformat(), [])
    return c, slot


def test_one_blocked_booking_does_not_stop_another_due_one(two_bookings):
    c, slot = two_bookings
    posted = []

    def publish_run(rid, dry_run=False, previous_status="queued"):
        if rid == "run_bad":
            raise ReleaseBlocked("Release blocked: facts: The factual review must match")
        posted.append(rid)
        return {"status": "published", "publish_result": {}}
    c.publish_run = publish_run
    c.post_due(now=slot + timedelta(minutes=1))
    assert posted == ["run_good"]
    bad = next(p for p in c.db.list_scheduled_posts(("blocked",)))
    assert bad["run_id"] == "run_bad" and "factual review" in bad["detail"]["reason"]


def test_one_alert_per_blocked_booking_with_what_can_work(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: provenance: x"))
    for minute in range(1, 6):
        c.post_due(now=slot + timedelta(minutes=minute))
    assert len(c.alerts) == 2                                   # one per blocked booking, not one a minute
    text, buttons = c.alerts[0]
    labels = [b["text"] for row in buttons for b in row]
    assert "Not posted" in text and "Trying again won't change this" in text
    assert labels == ["♻️ Make a new version", "⏹ Cancel booking", "📋 Details"]
    assert [b["callback_data"].split(":")[0] for row in buttons for b in row] == ["newversionask", "cancelask", "cpost"]


def test_an_error_that_may_pass_keeps_the_booking_and_is_said_once(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ConnectionError("network down"))
    for minute in range(1, 6):
        c.post_due(now=slot + timedelta(minutes=minute))
    assert len(c.db.list_scheduled_posts(("pending",))) == 2   # still booked, tried again
    assert len(c.alerts) == 2


def test_a_posting_job_error_is_said_once_until_it_changes(db):
    said = []
    bot = SimpleNamespace(notify=lambda text, **k: said.append(text))
    for _ in range(5):
        main_module.say_posting_error(db, bot, RuntimeError("database is locked"))
    main_module.say_posting_error(db, bot, None)
    main_module.say_posting_error(db, bot, RuntimeError("database is locked"))
    assert len(said) == 2


def test_post_now_and_cancel_on_a_blocked_booking(two_bookings, monkeypatch):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: provenance: x"))
    c.post_due(now=slot + timedelta(minutes=1))
    monkeypatch.setattr(c, "_require_release",
                        lambda run, dry_run=False, retry_now=False: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: provenance: x")))
    reply = c.post_now("run_bad")
    assert reply.startswith("⚡ Not posted") and "Make a new version" in reply
    post = c.db.list_scheduled_posts(("blocked",), run_id="run_bad")[0]
    assert "cancelled" in c.cancel_post(post["id"])
    assert not c.db.list_scheduled_posts(("blocked",), run_id="run_bad")


def test_make_a_new_version_cancels_the_bookings_and_sends_the_run_back(two_bookings):
    c, slot = two_bookings
    def publish_run(rid, **k):
        if rid == "run_bad":
            raise ReleaseBlocked("Release blocked: facts: x")
        return {"status": "published", "publish_result": {}}
    c.publish_run = publish_run
    c.post_due(now=slot + timedelta(minutes=1))
    assert c.prepare_new_version("run_bad") is None
    assert c.db.get_run("run_bad")["status"] == "regenerate"
    assert not c.db.list_scheduled_posts(("pending", "waiting", "blocked"), run_id="run_bad")
    assert c.prepare_new_version("run_good") == "That episode has no blocked booking."


def test_the_episode_shows_a_blocked_booking_with_its_reasons_and_actions(two_bookings):
    from src.posting_view import posting_episode
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: provenance: x"))
    c.post_due(now=slot + timedelta(minutes=1))
    text, buttons = posting_episode(c.db, "run_bad")
    callbacks = [b["callback_data"].split(":")[0] for row in buttons for b in row]
    assert "held" in text and "provenance: x" in text
    assert "newversionask" in callbacks and "cancelask" in callbacks and "postnowask" not in callbacks
    from src.control import make_command_handler
    h = make_command_handler(c.db, lambda: None, lambda: "")
    asked, rows = h("newversionask", c.db.run_key("run_bad"))
    assert "Make a new version?" in asked
    assert any(b["callback_data"].startswith("newversion:") for row in rows for b in row)


# ------------------------------------------------------------------ clean-up never touches a pending run
def _old(path):
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))


def test_cleanup_never_touches_a_pending_run(db, tmp_path):
    from src.ops import AssetLifecycleManager
    out = tmp_path / "output"
    for rid, status in (("run_booked", "scheduled"), ("run_review", "pending_approval"), ("run_done", "published"),
                        ("run_dry", "simulated")):
        (out / rid / "parallax").mkdir(parents=True)
        (out / rid / "parallax" / "scene_01.mp4").write_bytes(b"clip")
        (out / rid / f"{rid}_master.mp4").write_bytes(b"master")
        _old(out / rid)
        db.create_run(rid, "01_topic")
        db.update_run(rid, status)
    db.add_scheduled_post("run_booked", "reel", datetime.now(UTC).isoformat(), [])
    db.record_approval("run_review", "01_topic", 5)
    result = AssetLifecycleManager(output_dir=out, db=db).purge_intermediate_assets()
    for rid in ("run_booked", "run_review", "run_dry"):
        assert (out / rid / "parallax" / "scene_01.mp4").is_file()
    assert not (out / "run_done" / "parallax").exists() and (out / "run_done" / "run_done_master.mp4").is_file()
    assert [r["run"] for r in result["runs"]] == ["run_done"] and result["protected"] == 3
    logged = [json.loads(ln) for ln in (out / "retention_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert logged[0]["run"] == "run_done" and logged[0]["removed"] == ["parallax/"]


def test_a_published_run_with_a_live_booking_is_kept(db, tmp_path):
    from src.retention import free_run_intermediates
    out = tmp_path / "output"
    (out / "run_p" / "parallax").mkdir(parents=True)
    (out / "run_p" / "parallax" / "scene_01.mp4").write_bytes(b"clip")
    _old(out / "run_p")
    db.create_run("run_p", "01_topic")
    db.update_run("run_p", "published")
    db.add_scheduled_post("run_p", "carousel", None, [], status="waiting")
    assert free_run_intermediates(out, db, 7)["runs"] == []
    assert free_run_intermediates(out, db, 7, apply=False)["protected"] == 1


def test_the_cache_keeps_every_entry_a_run_references(tmp_path):
    from src.retention import free_caches
    out, cache = tmp_path / "output", tmp_path / "cache"
    (cache / "upscaled").mkdir(parents=True)
    used, unused, fresh = (cache / "upscaled" / n for n in ("used.png", "unused.png", "fresh.png"))
    for f in (used, unused, fresh):
        f.write_bytes(b"x")
    for f in (used, unused):
        _old(f)
    (out / "run_k").mkdir(parents=True)
    (out / "run_k" / "provenance.json").write_text(json.dumps({"derived_sources": [{"path": str(used)}]}),
                                                   encoding="utf-8")
    dry = free_caches(cache, out, days=30, apply=False)
    assert dry["folders"]["upscaled"]["freed"] == 1 and unused.exists()
    free_caches(cache, out, days=30)
    assert used.exists() and fresh.exists() and not unused.exists()


def _legacy_run(tmp_path, monkeypatch):
    """A run approved before records were signed (production's code until 2026-11-03): its stamp has no signature,
    no checker record and no LF digest, and names the sheet by a path on another machine."""
    import hashlib

    from src import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    sheet = tmp_path / "data" / "fact_sheets" / "01_topic.json"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b'{"facts": []}\r\n')
    run_dir = tmp_path / "run_old"
    run_dir.mkdir()
    (run_dir / "x_master.mp4").write_bytes(b"master")
    (run_dir / "thumbnail_cover.jpg").write_bytes(b"cover")
    payload = {"guardrail_passed": True, "title": "Approved title", "topic_id": "01_topic",
               "fact_review": {"public_fingerprint": "0" * 64, "sheet_path": r"D:\elsewhere\data\fact_sheets\01_topic.json",
                               "sheet_sha256": hashlib.sha256(b'{"facts": []}\r\n').hexdigest(),
                               "review_rules_fingerprint": "1" * 64}}
    run = {"run_id": "run_old", "master_video_path": str(run_dir / "x_master.mp4"),
           "thumbnail_cover_path": str(run_dir / "thumbnail_cover.jpg"), "script_payload": payload, "topic_id": "01_topic"}
    return run, sheet


def test_a_review_stamped_before_signing_is_checked_again_and_posts(tmp_path, monkeypatch):
    from src.release import _approved_now
    run, _ = _legacy_run(tmp_path, monkeypatch)
    assert fe.fact_review_problem(run["script_payload"]) and fe.drift_only(run["script_payload"])
    assert _facts(run, monkeypatch)["status"] == "fail"
    _mock_checkers(monkeypatch)
    rec = fe.run_recheck(run["script_payload"], _approved_now(run))
    (tmp_path / "run_old" / fe.RECHECK_FILE).write_text(json.dumps(rec), encoding="utf-8")
    assert _facts(run, monkeypatch)["status"] == "pass"


def test_a_review_stamped_before_signing_still_blocks_on_a_changed_sheet_or_a_forged_field(tmp_path, monkeypatch):
    run, sheet = _legacy_run(tmp_path, monkeypatch)
    sheet.write_bytes(b'{"facts": ["changed"]}')
    assert not fe.drift_only(run["script_payload"])
    run, sheet = _legacy_run(tmp_path / "b", monkeypatch)
    run["script_payload"]["fact_review"]["check"] = {"forged": True}    # a signed field without its signature
    assert not fe.drift_only(run["script_payload"])
    run["script_payload"]["fact_review"].pop("check")
    run["script_payload"]["topic_id"] = "02_topic"                      # another topic's sheet is never used
    assert not fe.drift_only(run["script_payload"])


# ------------------------------------------------------------------ ruling B: repository-owned rights records by content
@pytest.fixture
def font_run(tmp_path):
    """A run whose ledger records the repository's font, its licence text (CRLF, as an older checkout had it) and the
    font licence catalogue, plus the episode's own voice rights record."""
    from src.credits import sha256_file
    fonts = tmp_path / "fonts"
    (fonts / "licenses").mkdir(parents=True)
    font = fonts / "Used-Regular.ttf"
    font.write_bytes(b"font bytes")
    licence = fonts / "licenses" / "Used-Regular.ttf.OFL.txt"
    licence.write_bytes(b"SIL Open Font License\r\nVersion 1.1\r\n")
    catalogue = fonts / "license_evidence.json"
    entry = {"file": font.name, "sha256": sha256_file(font), "license_file": "licenses/" + licence.name,
             "license_sha256": sha256_file(licence), "copyright": "c",
             "license_description": "SIL Open Font License, Version 1.1"}
    catalogue.write_text(json.dumps({"fonts": [entry]}), encoding="utf-8")
    run_dir = tmp_path / "run_f"
    run_dir.mkdir()
    master = run_dir / "f_master.mp4"
    master.write_bytes(b"master")
    rights = run_dir / "voiceover.mp3.rights.json"
    rights.write_text('{"licence": "voice"}', encoding="utf-8")
    comp = master.with_suffix(".composition.json")
    comp.write_text(json.dumps({"fonts": {str(font): sha256_file(font)}}), encoding="utf-8")
    rec = lambda p: {"path": str(p), "sha256": sha256_file(p)}
    ledger = run_dir / "provenance.json"
    ledger.write_text(json.dumps({"status": "recorded", "issues": [], "assets": [], "assets_count": 0,
                                  "master_video": rec(master), "composition_manifest": rec(comp),
                                  "license_evidence": [rec(rights)],
                                  "resource_evidence": [rec(font), rec(catalogue), rec(licence)]}), encoding="utf-8")
    return SimpleNamespace(ledger=ledger, catalogue=catalogue, entry=entry, licence=licence, rights=rights, font=font)


def _problems(run):
    from src.video_qa import provenance_problems
    return provenance_problems(run.ledger)["problems"]


def test_a_catalogue_change_that_leaves_the_used_fonts_entries_unchanged_still_posts(font_run):
    other = {"file": "Added-Later.ttf", "sha256": "a" * 64, "license_file": "licenses/Added-Later.ttf.OFL.txt",
             "license_sha256": "b" * 64}
    font_run.catalogue.write_text(json.dumps({"fonts": [font_run.entry, other]}, indent=2), encoding="utf-8")
    font_run.licence.write_bytes(b"SIL Open Font License\nVersion 1.1\n")      # re-saved with LF only
    data = json.loads(font_run.catalogue.read_text(encoding="utf-8"))
    data["fonts"][0]["license_sha256"] = __import__("src.credits").credits.sha256_file(font_run.licence)
    font_run.catalogue.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert _problems(font_run) == []


def test_a_change_to_a_used_fonts_entry_blocks(font_run):
    changed = dict(font_run.entry, sha256="c" * 64)
    font_run.catalogue.write_text(json.dumps({"fonts": [changed]}), encoding="utf-8")
    assert any("entry for Used-Regular.ttf" in p for p in _problems(font_run))
    font_run.catalogue.write_text(json.dumps({"fonts": []}), encoding="utf-8")      # the entry removed
    assert any("entry for Used-Regular.ttf" in p for p in _problems(font_run))


def test_a_changed_licence_text_blocks(font_run):
    font_run.licence.write_bytes(b"SIL Open Font License\r\nVersion 1.2\r\n")     # one character, not line endings
    assert any("licence text" in p for p in _problems(font_run))
    from src.video_qa import same_licence_text
    assert not same_licence_text(font_run.licence, json.loads(font_run.ledger.read_text())["resource_evidence"][2]["sha256"])


def test_a_per_episode_rights_change_still_blocks(font_run):
    font_run.rights.write_text('{"licence": "voice", "path": "a re-pointed folder"}', encoding="utf-8")  # a rebind too
    assert any("rights record" in p for p in _problems(font_run))
    font_run.rights.write_text('{"licence": "voice"}', encoding="utf-8")
    font_run.font.write_bytes(b"other font bytes")           # the font file itself stays byte-checked
    assert any("Used-Regular.ttf" in p for p in _problems(font_run))


# ------------------------------------------------------------------ review 1: a block is never a state that may pass
def _upload_raises_unknown(c):
    """An upload that started (the run was claimed) and raised: the run is publish_unknown, as publish_run leaves it."""
    calls = []

    def publish_run(rid, **k):
        calls.append(rid)
        if rid == "run_bad":
            c.db.update_run(rid, "publish_unknown", error="TimeoutError")
            raise TimeoutError("read timed out <socket object at 0x000001F2>")
        return {"status": "published", "publish_result": {}}
    c.publish_run = publish_run
    return calls


def test_an_unknown_outcome_is_reconciled_never_blocked_or_remade(two_bookings):
    c, slot = two_bookings
    calls = _upload_raises_unknown(c)
    c.post_due(now=slot + timedelta(minutes=1))
    assert c.db.list_scheduled_posts(("unknown",), run_id="run_bad") and not c.db.list_scheduled_posts(("blocked",))
    text, buttons = next(a for a in c.alerts if "unconfirmed" in a[0])
    assert [b["callback_data"].split(":")[0] for b in buttons] == ["pubok", "pubno"] and "new version" not in text
    for minute in range(2, 6):                                     # no path posts it again
        c.post_due(now=slot + timedelta(minutes=minute))
    assert calls.count("run_bad") == 1
    reply = c.post_now("run_bad")                                   # refused: not a run that may be posted
    assert calls.count("run_bad") == 1 and "is posted" not in reply
    assert "blocked booking" in c.prepare_new_version("run_bad")
    assert c.db.get_run("run_bad")["status"] == "publish_unknown"


def test_a_run_already_publish_unknown_is_not_blocked(two_bookings):
    c, slot = two_bookings
    c.db.update_run("run_bad", "publish_unknown")

    def publish_run(rid, **k):
        if rid == "run_bad":
            raise ValueError("Publication is in progress or its outcome is unknown; reconcile it before retrying.")
        return {"status": "published", "publish_result": {}}
    c.publish_run = publish_run
    c.post_due(now=slot + timedelta(minutes=1))
    assert c.db.list_scheduled_posts(("unknown",), run_id="run_bad") and not c.db.list_scheduled_posts(("blocked",))


def test_an_upload_in_progress_or_its_lock_held_is_held_not_blocked(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(
        ValueError("Publication is in progress; its owner still holds the upload lock."))
    for minute in range(1, 4):
        c.post_due(now=slot + timedelta(minutes=minute))
    assert len(c.db.list_scheduled_posts(("pending",))) == 2 and not c.db.list_scheduled_posts(("blocked",))
    assert len(c.alerts) == 2 and all(a[1] is None for a in c.alerts)      # one each, no remake button


def test_post_now_reports_an_upload_exception_as_unknown(two_bookings, monkeypatch):
    c, _slot = two_bookings
    _upload_raises_unknown(c)
    monkeypatch.setattr(c, "_require_release", lambda run, dry_run=False, retry_now=False: {"status": "pass"})
    reply = c.post_now("run_bad")
    assert "Outcome unknown" in reply and "Not posted" not in reply
    assert c.db.list_scheduled_posts(("unknown",), run_id="run_bad")


def test_make_a_new_version_refuses_first_when_the_reel_may_be_live(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: facts: x"))
    c.post_due(now=slot + timedelta(minutes=1))
    c.db.record_publication("01_topic", "run_bad", "coordinator", "unknown", {})
    assert "may already be live" in c.prepare_new_version("run_bad")
    assert c.db.list_scheduled_posts(("blocked",), run_id="run_bad")              # nothing was cancelled
    assert "in progress" in main_module.new_version(c, "run_good", lambda rid: "started", busy=lambda: True)
    assert c.db.list_scheduled_posts(("blocked",), run_id="run_good")


def test_a_new_version_never_waits_for_a_reason_chip(two_bookings):
    from src import improve
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(ReleaseBlocked("Release blocked: provenance: x"))
    c.post_due(now=slot + timedelta(minutes=1))
    assert c.prepare_new_version("run_bad") is None
    fb = improve.operator_feedback(c.db, "run_bad")
    assert fb["reason"] == "other" and fb["text"].startswith("Blocked at its posting slot")


def test_a_checker_outage_is_never_stored_as_a_verdict(tmp_path, monkeypatch, checked_stamp, db):
    from src.telegram_state import initialize, put, review_fingerprint
    run, _ = _approved_run(tmp_path, checked_stamp)
    db.create_run("run_x", "01_topic")
    db.update_run("run_x", "scheduled", script_payload=run["script_payload"],
                  master_video_path=run["master_video_path"], thumbnail_cover_path=run["thumbnail_cover_path"])
    initialize(db)
    db.record_approval("run_x", "01_topic", 77)
    put(db, "review-card", "run_x:77", review_fingerprint(db, "run_x"))
    db.update_approval_decision("run_x", "approved")
    _code_update(monkeypatch)
    calls = []
    _mock_checkers(monkeypatch, evidence="unavailable", calls=calls)
    c = main_module.PipelineCoordinator.__new__(main_module.PipelineCoordinator)
    c.db = db
    c.alerts = []
    c._alert = lambda text, buttons=None: c.alerts.append(text)
    with pytest.raises(fe.RecheckUnavailable):
        c._recheck_facts(db.get_run("run_x"))
    assert not (tmp_path / "run_x" / fe.RECHECK_FILE).exists() and calls == ["trace", "evidence"]
    with pytest.raises(fe.RecheckUnavailable):                     # backoff: no call before the wait is over
        c._recheck_facts(db.get_run("run_x"))
    assert calls == ["trace", "evidence"]
    _mock_checkers(monkeypatch, calls=calls)                       # the quota is back
    c._recheck_facts(db.get_run("run_x"), retry_now=True)
    assert json.loads((tmp_path / "run_x" / fe.RECHECK_FILE).read_text())["passed"] is True
    assert any("Fact check run again" in a and "Passed" in a for a in c.alerts)
    # while a run is being made the checkers are left to it
    (tmp_path / "run_x" / fe.RECHECK_FILE).unlink()
    db.create_run("run_live", "01_topic")
    db.update_run("run_live", "producing")
    db.set_setting("active_progress", json.dumps({"run_id": "run_live"}))
    with pytest.raises(fe.RecheckUnavailable, match="run is using the checkers"):
        c._recheck_facts(db.get_run("run_x"), retry_now=True)


def test_an_outage_holds_the_booking_and_is_said_once(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(fe.RecheckUnavailable("evidence check unavailable"))
    for minute in range(1, 5):
        c.post_due(now=slot + timedelta(minutes=minute))
    assert len(c.db.list_scheduled_posts(("pending",))) == 2 and len(c.alerts) == 2
    assert "run again under the current rules" in c.alerts[0][0]


def test_an_error_with_a_new_address_each_minute_is_said_once(two_bookings):
    c, slot = two_bookings
    n = iter(range(100))
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(
        ConnectionError(f"<HTTPSConnection object at 0x{next(n):08x}>: failed, request id {10 ** 7 + next(n)}"))
    for minute in range(1, 5):
        c.post_due(now=slot + timedelta(minutes=minute))
    assert len(c.alerts) == 2


def test_cleanup_never_deletes_a_temple_topics_master(db, tmp_path):
    from src.retention import free_run_intermediates
    out = tmp_path / "output"
    run = out / "run_kailasa_temple_monolith_1792524470"
    (run / "parallax").mkdir(parents=True)
    (run / "parallax" / "scene_01.mp4").write_bytes(b"clip")
    kept = ["kailasa_temple_monolith_master.mp4", "kailasa_temple_monolith_master.composition.json",
            "kailasa_temple_monolith_master.srt", "kailasa_temple_monolith_master.vtt",
            "kailasa_temple_monolith_master_captions.ass", "release_report.json", "provenance.json",
            "kailasa_temple_monolith_master.mp4.tmp"]
    for name in kept + ["render.tmp"]:
        (run / name).write_bytes(b"x")
    _old(run)
    db.create_run(run.name, "01_topic")
    db.update_run(run.name, "published")
    free_run_intermediates(out, db, 7)
    assert all((run / name).is_file() for name in kept)
    assert not (run / "parallax").exists() and not (run / "render.tmp").exists()


def test_a_used_fonts_entry_repointed_to_another_fonts_licence_blocks(font_run):
    from src.credits import sha256_file
    other = font_run.licence.with_name("Other-Font.ttf.OFL.txt")
    other.write_bytes(font_run.licence.read_bytes())
    ledger = json.loads(font_run.ledger.read_text())
    ledger["resource_evidence"].append({"path": str(other), "sha256": sha256_file(other)})
    font_run.ledger.write_text(json.dumps(ledger))
    moved = dict(font_run.entry, license_file="licenses/" + other.name, license_sha256=sha256_file(other))
    font_run.catalogue.write_text(json.dumps({"fonts": [moved]}), encoding="utf-8")
    assert any("entry for Used-Regular.ttf" in p for p in _problems(font_run))


def test_a_held_booking_moved_past_its_slot_says_why_once(two_bookings):
    c, slot = two_bookings
    c.publish_run = lambda rid, **k: (_ for _ in ()).throw(
        fe.RecheckUnavailable("the checkers gave no verdict 6 times; tap Post now to try once more"))
    c.post_due(now=slot + timedelta(minutes=1))              # held: one alert per booking
    held = len(c.alerts)
    for day in range(4):                                      # each day it misses its slot again
        for post in c.db.list_scheduled_posts(("pending",)):
            due = datetime.fromisoformat(post["due_at"])
            c.post_due(now=due + timedelta(hours=4))
    moved = [a[0] for a in c.alerts[held:]]
    assert len(moved) == 2 and all("still held" in m and "daemon was off" not in m for m in moved)
    assert all("no verdict 6 times" in m for m in moved)


def test_retry_reel_names_the_action_that_works_after_the_tries_run_out(two_bookings, monkeypatch):
    c, _slot = two_bookings
    seen = []

    def gate(run, dry_run=False, retry_now=False):
        seen.append(retry_now)
        raise fe.RecheckUnavailable("evidence check unavailable: FreeQuotaUnavailable")
    monkeypatch.setattr(c, "_require_release", gate)
    monkeypatch.setattr(c, "_reel_failed_platforms", lambda run_id: (["instagram"], 0))
    reply = c.retry_reel("run_bad")
    assert seen == [True] and "Retry Reel again later" in reply and "Post now" not in reply
