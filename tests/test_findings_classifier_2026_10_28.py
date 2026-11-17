"""The improvement ledger files each finding by what it says (ledger audit, 2026-10-28; the texts below
are real 'detail' rows from the production and development ledgers): the script and video-check
classifiers, self-healing lines by their text and only while still reported, a second-checker rejection
apart from an unavailable check, run failures by cause, no re-proposal of a decided class from the same
findings, old misfilings re-filed, one row per (run, class, detail), the eval on the free quota with the
daemon's setting restored, and proposal cards that say "gate correct" or "policy, not a defect"."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src import free_llm, improve


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(improve, "PATCHES_FILE", tmp_path / "prompt_patches.json")
    monkeypatch.setattr(improve, "QUEUE_FILE", tmp_path / "queue.md")
    monkeypatch.setattr(improve.config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr("src.free_llm.ready", lambda: False)
    improve._buffer.clear()


# --------------------------------------------------------------- 2. the classifiers, from real rows
SCRIPT_TEXTS = [
    # a repetition hint mentions "disputed fact" and "cover": it is a repetition (prod #13 was script.facts)
    ("The narration repeats the phrase 'ancient artisans engineered', 'engineered bronze gearwork', 'gearwork to "
     "track'; say it once (one hedge can cover two related figures) or reword the second, keeping every disputed "
     "fact hedged.", "script.repetition"),
    # a Story slide's line count says "words": it is the slides (prod #69, dev #105 were script.length)
    ("Story slide 2's body takes 4 lines on the frame (at most 3); rewrite it in about 9 words (14 now), keeping "
     "one complete sentence.", "script.slides"),
    ("Story slide 3's body takes 4 lines on the frame (at most 3); rewrite it in about 10 words (15 now), keeping "
     "one complete sentence.", "script.slides"),
    ("Narration ends on trailing word 'that'.", "script.trailing_word"),  # dev #1 was script.other
    ("emphasis_words needs 1-3 entries.", "graphics.emphasis_words"),
    ("emphasis word 'Mauritanian desert' does not appear in the narration.", "graphics.emphasis_words"),
    # the ones that were right stay right
    ("Narration runs 40.0s when spoken (minimum 42s). This draft is 102 words; extend the narration to 110-122 "
     "words with more verified facts from the research notes, keeping the closing sentence.", "script.length"),
    ("Narration runs 53.4s when spoken (limit 51.5s); cut about 5 words or use shorter, plainer words.", "script.length"),
    ("Caption's first line is 544 characters; keep it within the 125 shown before 'more'.", "script.caption_first_line"),
    ("The poll asks a yes/no question ('Did the Amber Room survive?') but its options (Burned in 1944 / Hidden "
     "somewhere) are not yes/no answers; ask an either/or question the two options answer, e.g. 'Which took more "
     "work?'.", "script.poll"),
    ("Contains pseudo-archaeology trope 'extraterrestrial'.", "script.trope"),
    ("Expected 3 story slides, got 2.", "script.slides"),
    ("2 untraceable claim(s): 'Kailasa is the world's largest rock-cut temple.'", "script.facts"),
]

QA_TEXTS = [
    # "but the image shows" / "rather than" / "while the narration": the picture is not what is said
    ("The narration discusses modern concrete structures crumbling, but the visual shows an ancient stone/brick "
     "Roman aqueduct ruin.", "video.picture_relevance"),
    ("The narration mentions 'modern infrastructure', but the image displays ancient Roman coffered ceiling "
     "architecture.", "video.picture_relevance"),
    ("The narration explicitly mentions Roman aqueducts delivering water, but the image repeats the view of the "
     "Pantheon's dome.", "video.picture_relevance"),
    ("Visual shows an ancient/medieval wooden galley model while the narration specifically introduces modern "
     "napalm and flamethrowers.", "video.picture_relevance"),
    ("The visual shows the interior of the ancient Roman Pantheon, which contradicts the narration discussing "
     "failing modern infrastructure.", "video.picture_relevance"),
    ("The image shows a medieval illuminated Latin manuscript page, which mismatches the narration discussing "
     "modern concrete cracking and structural degradation.", "video.picture_relevance"),
    # "covering" is not the cover; a patch on the picture is its quality (prod #90 was video.cover)
    ("There is a conspicuous grey oval patch/artifact covering the candle sconce in the bottom right corner.",
     "video.picture_quality"),
    ("There is a flat, unblended grey circular patch obscuring part of the reflection and candlestick in the "
     "bottom-right mirror.", "video.picture_quality"),
    ("A dark blurry obstruction (likely fingers or hands partially blocking the camera lens) is visible along the "
     "bottom edge of the frame.", "video.picture_quality"),
    ("The background image is an empty/blank dark gradient showing only text graphics, with no visual imagery.",
     "video.picture_quality"),
    # on-screen text against the narration (dev #92 was video.length)
    ("The on-screen graphic reads '93-104M YEARS AGO' while the spoken narration states 'between 94 and 104 "
     "million'.", "video.text_mismatch"),
    ("The graphic on screen states '29 bronze gears visible today', whereas the spoken narration says 30.",
     "video.text_mismatch"),
    ("Automated face-blur circles are applied over the historical manuscript illustration, obscuring the figures' "
     "heads.", "video.face_blur"),
    ("Multiple automated face-detection blur circles cover all human faces across the manuscript illumination.",
     "video.face_blur"),
    ("The visual displays an informational museum text placard cropped awkwardly, which is visually unengaging and "
     "hard to read behind the caption.", "video.picture_text"),
    ("The narration repeats the phrase 'By one estimate' which was already spoken in beat 3.", "script.repetition"),
    ("The map showing the Catherine Palace location appears during the narration about the room's composition, "
     "whereas it would have been more logically placed during beat 1.", "video.timing"),
    ("Caption text runs under the lower safe zone.", "video.captions"),
    ("The cover's title is too small to read.", "video.cover"),
    ("Something else entirely.", "video.other"),
]


@pytest.mark.parametrize("text,klass", SCRIPT_TEXTS)
def test_script_problems_are_filed_by_what_they_say(text, klass):
    assert improve.classify(text) == klass


@pytest.mark.parametrize("text,klass", QA_TEXTS)
def test_video_check_findings_are_filed_by_what_they_say(text, klass):
    assert improve.classify_qa(text) == klass


# --------------------------------------------------------------- 1. self-healing lines
def _run(db, run_id):
    db.create_run(run_id, "01_topic")
    return run_id


def test_self_healing_lines_are_classed_by_text_and_a_repaired_beat_is_not_counted(db, tmp_path):
    run_id = _run(db, "run_r")
    (tmp_path / "qa_report.json").write_text(json.dumps({"issues": [
        {"beat": 8, "severity": "minor", "issue": "The background image is extremely blurred."}]}))
    payload = {"self_healing_actions": [{"attempt": 1, "fixing": [
        "Beat 7: The visual shows the interior dome/oculus of the ancient Roman Pantheon rather than modern concrete "
        "suffering from cracking or structural degradation as described in the narration.",
        "Beat 8: The background image is a museum wall placard detailing the Antikythera shipwreck rather than an "
        "illustration or photograph of fourteenth-century European astronomical clocks referenced in the narration.",
        "Beat 2: the graphic appears away from the words that name it (graphic_map.mp4); put it on the beat where "
        "those words are spoken, or choose a different picture for this beat."]}],
        "self_healing": {"issues": [{"type": "temporal_desync", "scene_index": 1}]}}
    improve.collect_run(db, run_id, tmp_path, payload)
    with db._conn() as conn:
        rows = {r["klass"]: r["detail"] for r in conn.execute("SELECT klass, detail FROM findings WHERE source = 'self_healing'")}
    # beat 7 was repaired (the final report no longer says anything about it); 8 and 2 are still reported
    assert set(rows) == {"video.picture_text", "video.timing"}
    assert rows["video.picture_text"].startswith("Beat 8:")  # the beat stays in the detail
    assert not any(k.startswith("video.beat_") for k in rows)


def test_self_healing_lines_are_all_kept_when_there_is_no_final_report(db, tmp_path):
    run_id = _run(db, "run_n")
    payload = {"self_healing_actions": [{"attempt": 1, "fixing": [
        "Beat 1: The narration refers to modern concrete structures crumbling, but the visual shows the ancient "
        "Roman Pantheon."]}]}
    assert improve.collect_run(db, run_id, tmp_path, payload) == 1
    assert improve.recurring(db, min_count=1)[0]["klass"] == "video.picture_relevance"


# --------------------------------------------------------------- 3. second check
def test_a_second_checker_rejection_is_not_filed_as_unavailable(db):
    improve.collect_run(db, _run(db, "run_f"), None, {"second_check": {
        "status": "fail", "summary": "[evidence re-check (gemini-3.7-flash)] 1 blocking claim(s): 'The bronze lump "
                                     "was pulled specifically from the Aegean Sea.'"}})
    improve.collect_run(db, _run(db, "run_u"), None, {"second_check": {"status": "unavailable", "summary": "429"}})
    with db._conn() as conn:
        got = {r["run_id"]: (r["klass"], r["detail"]) for r in conn.execute("SELECT run_id, klass, detail FROM findings")}
    assert got["run_f"][0] == "check.second_rejected" and "Aegean" in got["run_f"][1]
    assert got["run_u"] == ("check.second_unavailable", "Second fact check unavailable")
    assert improve.plain_name("check.second_rejected") == "Second fact check rejected the script"


# --------------------------------------------------------------- 4. run failures by cause
FAILURES = [
    ("RuntimeError: Voiceover is 53.7s; hard ceiling is 52s.", "run.failed.voice_length"),
    ("RuntimeError: Voiceover is 41.1s; too short for the 42s minimum.", "run.failed.voice_length"),
    ("RuntimeError: Script rejected by guardrail: REJECTED by second fact checker: [evidence re-check "
     "(gemini-3.7-flash)] 1 blocking claim(s)", "run.failed.guardrail"),
    ("MissingAssetsError: Only 4 of the 6 approved photos of 'greek_fire_siphon' are strong enough for the video "
     "(need 5): 2 Topic, 4 Context.", "run.failed.missing_photos"),
    ("MissingAssetsError: Fetched images for 'roman_self_healing_concrete' are not reviewed.", "run.failed.missing_photos"),
    ("ScriptUnavailableError: The script writer (gemini-free) made 3 attempts for 'hal_saflieni_hypogeum' and each "
     "failed its checks (last: The poll asks a yes/no question", "run.failed.script_unavailable"),
    ("ScriptUnavailableError: No script source for topic 'kailasa_temple_monolith': configure ANTHROPIC_API_KEY",
     "run.failed.no_script_source"),
    ("ValueError: Publication is in progress; its owner still holds the upload lock.", "run.failed.publication_locked"),
    ("ValueError: Release blocked: visual_checks: The background image is an empty/blank dark gradient",
     "run.failed.release_blocked"),
    ("ValueError: Encoded peak correction cannot satisfy loudness and peak limits", "run.failed.audio_master"),
    ("FreeQuotaUnavailable: The free Gemini quota is used up for now (gemini-3.7-flash)", "run.failed.provider_quota"),
    ("KeyError: 'beats'", "run.failed.KeyError"),  # an unknown cause keeps its exception type
]


@pytest.mark.parametrize("error,klass", FAILURES)
def test_run_failures_are_filed_by_cause_with_the_exception_in_the_detail(db, error, klass):
    run_id = _run(db, "run_e")
    improve.collect_run(db, run_id, None, {}, error=error)
    with db._conn() as conn:
        row = conn.execute("SELECT klass, detail FROM findings").fetchone()
    assert row["klass"] == klass and row["detail"] == error  # the type ("RuntimeError: ...") is kept
    assert improve.plain_name(klass).startswith("Runs stopped: ")


# --------------------------------------------------------------- 5. decided classes
def _age(db, days):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE findings SET created_at = ?", (stamp,))


def test_a_declined_class_is_proposed_again_only_from_findings_after_the_decision(db):
    for i in range(3):
        improve.record(db, "release.ambient_bed_rights", f'"Audio changed" {i}', "release", run_id=_run(db, f"run_{i}"))
    _age(db, 2)
    pid = improve.make_proposals(db)[0]["id"]
    improve.decline(db, pid, "op")
    assert improve.make_proposals(db) == []  # the next night: the same findings are not a new problem
    for i in range(3, 5):
        improve.record(db, "release.ambient_bed_rights", f'"Audio changed" {i}', "release", run_id=_run(db, f"run_{i}"))
    assert improve.make_proposals(db) == []  # two since the decision: not yet recurring
    improve.record(db, "release.ambient_bed_rights", '"Audio changed" 5', "release", run_id=_run(db, "run_5"))
    again = improve.make_proposals(db)
    assert len(again) == 1 and "3× in 3 runs" in again[0]["title"]
    # the /improve list still shows everything that happened
    assert improve.recurring(db, min_count=1)[0]["count"] == 6


# --------------------------------------------------------------- 6. old misfilings, duplicates
def test_old_misfiled_rows_are_refiled_and_passed_claims_retired(db, tmp_path):
    rid = _run(db, "run_old")
    for i in range(4):  # dev: 63 "traced:" passes filed as script.facts before 54cf7f6
        improve.record(db, "script.facts", f"traced: claim {i}", "fact_check", run_id=rid)
    improve.record(db, "script.facts", "untraceable: a claim", "fact_check", run_id=rid)
    improve.record(db, "script.facts", SCRIPT_TEXTS[0][0], "structure", run_id=rid)
    improve.record(db, "video.beat_8", "Beat 8: The narration refers to 'modern infrastructure', but the image "
                   "displays an ancient Roman coffered vault/structure.", "self_healing", run_id=rid)
    improve.record(db, "run.failed.RuntimeError", "RuntimeError: Voiceover is 52.9s; hard ceiling is 52s.", "run", run_id=rid)
    improve.record(db, "review.other", "Other", "decision:rejected", run_id=rid)
    assert improve.reclassify_ledger(db) == 7
    assert improve.reclassify_ledger(db) == 0  # idempotent
    counts = {r["klass"]: r["count"] for r in improve.recurring(db, min_count=1)}
    assert counts == {"script.facts": 1, "script.repetition": 1, "video.picture_relevance": 1,
                      "run.failed.voice_length": 1, "review.other": 1}
    with db._conn() as conn:  # kept, not deleted
        assert conn.execute("SELECT COUNT(*) FROM findings WHERE klass = 'retired.script.facts'").fetchone()[0] == 4
    assert "5 findings" in improve.digest_text(db)


def test_an_old_self_healing_row_for_a_repaired_beat_is_retired(db, tmp_path):
    rid = _run(db, "run_fixed")
    (tmp_path / "output" / rid).mkdir(parents=True)
    (tmp_path / "output" / rid / "qa_report.json").write_text(json.dumps({"issues": []}))
    improve.record(db, "video.beat_9", "Beat 9: The narration refers to Roman concrete aqueducts, but the visual "
                   "displays the interior dome of the Pantheon instead of an aqueduct.", "self_healing", run_id=rid)
    improve.reclassify_ledger(db)
    assert improve.recurring(db, min_count=1) == []


def test_the_same_finding_twice_in_one_run_is_one_row(db, tmp_path):
    rid = _run(db, "run_dup")
    text = "An automated face-blur circle obscures the figure's head in the manuscript illustration."
    (tmp_path / "qa_report.json").write_text(json.dumps({"issues": [{"beat": 3, "issue": text}, {"beat": 5, "issue": text}]}))
    assert improve.collect_run(db, rid, tmp_path, {}) == 1
    assert improve.collect_run(db, rid, tmp_path, {}) == 0  # collected again: nothing new
    improve.record(db, "issue.bot", "same words", "issue:a")
    improve.record(db, "issue.bot", "same words", "issue:b")  # reports without a run are each kept
    assert {r["klass"]: r["count"] for r in improve.recurring(db, min_count=1)} == {"video.face_blur": 1, "issue.bot": 2}


# --------------------------------------------------------------- 7. the eval: free only, restored, logged
def test_the_eval_restores_the_paid_fallback_and_logs_its_pause(db, monkeypatch, caplog, tmp_path):
    from scripts import eval_prompts as ep
    from src.llm import LLMError
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", True)
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(ep, "OUT", tmp_path / "evals")
    monkeypatch.setattr(ep, "RESULTS", tmp_path / "evals" / "results.jsonl")
    monkeypatch.setattr("src.asset_pipeline.pool_ready", lambda tid: True)
    seen = []

    def draft(topic, engine):
        seen.append(free_llm.paid_fallback_allowed())
        raise LLMError("Free model gemini-3.8-flash failed: overloaded")
    monkeypatch.setattr(ep, "score_draft", draft)
    monkeypatch.setattr(ep.script_engine, "ScriptEngine", lambda: object())
    improve.record(db, "script.poll", "x", "structure", run_id=_run(db, "run_p"))
    with db._conn() as conn:
        conn.execute("INSERT INTO proposals (id, klass, tier, title, change_json, status, created_at) VALUES "
                     "('imp_1','script.poll','prompt','t','{\"rule\": \"r\"}','testing','2026-10-28')")
    with caplog.at_level("WARNING"):
        ep.run_candidates(db)
    assert seen == [False]                       # the drafts ran with the billed key off
    assert free_llm.ALLOW_PAID_FALLBACK is True  # and the daemon's setting is back
    assert any("Candidate eval paused" in r.getMessage() for r in caplog.records)


def test_the_eval_does_not_run_on_a_billed_engine(db, monkeypatch):
    from scripts import eval_prompts as ep
    monkeypatch.setattr(free_llm, "enabled", lambda: False)
    monkeypatch.setattr(ep, "score_draft", lambda *a: pytest.fail("a billed draft"))
    ep.run_candidates(db)


def test_a_rule_is_drafted_on_the_free_quota_only_and_the_setting_restored(monkeypatch):
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", True)
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    seen = []

    def call(*a, **k):
        seen.append(free_llm.paid_fallback_allowed())
        return improve._Rule(rule="Always ask an either/or question that the two options answer.")
    monkeypatch.setattr(free_llm, "call", call)
    assert improve._draft_rule("script.poll", ["ex"]).startswith("Always ask")
    assert seen == [False] and free_llm.ALLOW_PAID_FALLBACK is True
    monkeypatch.setattr(free_llm, "enabled", lambda: False)  # the Claude engine is billed: no draft
    assert improve._draft_rule("script.poll", ["ex"]) is None and len(seen) == 1


# --------------------------------------------------------------- 8. gate confirmed, policy classes
def test_a_confirmed_gate_says_fix_upstream_and_a_policy_class_is_information(db, tmp_path):
    for i in range(3):
        rid = _run(db, f"run_g{i}")
        improve.record(db, "release.visual_checks", json.dumps([{"type": "video_check", "beat": 8, "severity": "major",
                       "issue": "The visual shows the interior of the ancient Roman Pantheon, which contradicts the "
                                "narration discussing failing modern infrastructure."}]), "release", run_id=rid)
        improve.record(db, "video.picture_relevance", "The visual shows the interior of the ancient Roman Pantheon, "
                       "which contradicts the narration discussing failing modern infrastructure.", "qa", run_id=rid)
        improve.record(db, "pictures.licence_excluded", f"beat {i}: 3 Commons result(s), none usable without "
                       "share-alike licences (IMAGES_ALLOW_SHARE_ALIKE)", "pictures", run_id=rid)
    made = {p["klass"]: p for p in improve.make_proposals(db)}
    gate = made["release.visual_checks"]
    assert "gate correct, fix upstream" in gate["title"] and "needs a fix" not in gate["title"]
    assert "3 of these 3 run(s)" in gate["evidence"]["advice"]
    text, _ = improve.card(gate)
    assert "not the gate" in text
    policy = made["pictures.licence_excluded"]
    assert policy["tier"] == "info" and "not a defect" in policy["title"]
    text, buttons = improve.card(policy)
    assert "IMAGES_ALLOW_SHARE_ALIKE" in text and [b["text"] for b in buttons[0]] == ["✔ Noted"]
    assert improve.decline(db, policy["id"], "op").startswith("Noted")


def test_an_unconfirmed_gate_keeps_the_plain_title(db):
    for i in range(3):
        improve.record(db, "release.visual_checks", json.dumps([{"type": "temporal_desync", "severity": "minor",
                       "issue": "Scene 1: Graphic 'graphic_map.mp4' keyword is 1.43s outside its cut (>1.2s)"}]),
                       "release", run_id=_run(db, f"run_h{i}"))
    (p,) = improve.make_proposals(db)
    assert p["title"].endswith("needs a fix") and "advice" not in p["evidence"]


def test_free_only_holds_for_its_own_thread_and_a_run_beside_it_keeps_the_paid_fallback(monkeypatch):
    """The rule draft runs outside run_lock: a production run on another thread must keep its fallback."""
    import threading
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", True)
    inside, beside = [], []
    entered, checked = threading.Event(), threading.Event()

    def run_beside():
        entered.wait(5)
        beside.append(free_llm.paid_fallback_allowed())
        checked.set()
    worker = threading.Thread(target=run_beside)
    worker.start()
    with improve.free_only():
        entered.set()
        checked.wait(5)
        inside.append(free_llm.paid_fallback_allowed())
    worker.join(5)
    assert inside == [False] and beside == [True] and free_llm.paid_fallback_allowed() is True


def test_a_policy_no_longer_in_force_is_not_proposed(db, monkeypatch):
    """Share-alike turned on: findings made while it was off are not put to the operator again (prod, 2026-10-28)."""
    from src import config
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", True)
    for i in range(3):
        improve.record(db, "pictures.licence_excluded", f"beat {i}: 2 of 3 Commons result(s) usable only with "
                       "share-alike licences (IMAGES_ALLOW_SHARE_ALIKE is off)", "pictures", run_id=_run(db, f"run_sa{i}"))
    assert "pictures.licence_excluded" not in {p["klass"] for p in improve.make_proposals(db)}
