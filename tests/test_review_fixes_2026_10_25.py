"""Fixes from the 2026-10-25 review of that day's releases (rc49-rc51) and of the day's run records: each one
named by what went wrong, so the regression is plain."""
import json
from pathlib import Path

from src import config, release


def test_a_major_video_check_finding_blocks_the_gate_and_info_notes_never_do(tmp_path, monkeypatch):
    # Nazca (dev, 2026-10-25): "⚠️ beat 6: a modern car on an asphalt highway" on the card, "all checks pass" above it.
    from src.credits import sha256_file
    master = tmp_path / "m.mp4"
    master.write_bytes(b"video")
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", True)
    certified = {"master_sha256": sha256_file(master), "certified": True, "issues": []}
    car = {"run": True, "issues": [{"beat": 6, "severity": "major", "issue": "a modern car on an asphalt highway"}]}
    report = release.write_release_report(_run(master, {"self_healing": certified, "video_check": car}), dry_run=True)
    visual = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert visual["status"] == "fail" and "modern car" in json.dumps(visual["detail"])
    # Info-level notes (the look at the stills) are shown, never a block.
    notes = dict(certified, issues=[{"type": "story_frame_look", "severity": "info", "issue": "title over the blade"}])
    report = release.write_release_report(_run(master, {"self_healing": notes, "video_check": {"run": True, "issues": []}}), dry_run=True)
    assert next(c for c in report["checks"] if c["name"] == "visual_checks")["status"] == "pass"
    # A minor look finding follows the minor-findings setting, as the audit's own minor findings do.
    dup = {"run": True, "issues": [{"beat": 11, "severity": "minor", "issue": "the same shot as beat 3"}]}
    report = release.write_release_report(_run(master, {"self_healing": certified, "video_check": dup}), dry_run=True)
    assert next(c for c in report["checks"] if c["name"] == "visual_checks")["status"] == "fail"
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", False)
    report = release.write_release_report(_run(master, {"self_healing": certified, "video_check": dup}), dry_run=True)
    visual = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert visual["status"] == "pass" and visual.get("minor")


def _run(master, payload):
    return {"run_id": "run_g", "topic_id": "t", "master_video_path": str(master), "script_payload": payload,
            "thumbnail_cover_path": "", "thumbnail_grid_path": "", "status": "pending_approval"}


def test_the_review_card_lists_the_look_at_the_stills(curated_payload):
    from src.telegram_bot import TelegramApprovalBot
    payload = dict(curated_payload, self_healing={"issues": [
        {"type": "story_frame_look", "card": "story_02_reveal.jpg", "severity": "info", "issue": "The title crosses the blade it names."},
        {"type": "semantic_desync", "severity": "major", "issue": "not an info note"}]})
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "Look at the stills and cover: 1 note(s)" in card and "crosses the blade" in card and "not an info note" not in card


def test_a_re_review_keeps_the_photos_you_kept(tmp_path, monkeypatch):
    from PIL import Image
    from src import image_review
    from src.credits import load_credits
    imgs = []
    for i in (1, 2):
        Image.new("RGB", (64, 48), (40 * i, 0, 0)).save(tmp_path / f"{i:02d}_commons.jpg")  # two different photos
        imgs.append({"file": f"{i:02d}_commons.jpg", "title": f"File:{i}.jpg", "license": "CC0"})
    imgs[1]["operator_keep"] = True
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": False, "images": imgs}), encoding="utf-8")
    def verdict(i, shows):
        return image_review.PhotoVerdict(quality=3, depiction="original", index=i, shows_topic=shows, usable=True, safe=True, reason="r")
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: image_review.PoolReview(verdicts=[verdict(1, "yes"), verdict(2, "no")]))
    monkeypatch.setattr(image_review, "image_block", lambda p, *a: {"type": "image"})
    monkeypatch.setattr(image_review, "MIN_SCENE_IMAGES", 1)
    image_review.review_pool({"id": "t", "title": "T"}, tmp_path)
    cr = load_credits(tmp_path)
    assert [im["file"] for im in cr["images"]] == ["01_commons.jpg", "02_commons.jpg"] and not cr.get("rejected_by_review")


def test_a_draft_measured_again_is_one_pace_sample_not_five(tmp_path, monkeypatch):
    from src import script_engine
    monkeypatch.setattr(script_engine, "WORD_PACE_FILE", tmp_path / "word_pace.json")
    for _ in range(5):
        script_engine.record_word_pace(0.40, "The same draft, measured again and again.")
    script_engine.record_word_pace(0.46, "Another draft.")
    assert [e[1] for e in script_engine._word_pace_entries()] == [0.40, 0.46]
    (tmp_path / "word_pace.json").write_text("[0.45, 0.47]", encoding="utf-8")          # the older bare list
    assert [e[1] for e in script_engine._word_pace_entries()] == [0.45, 0.47]


def test_a_space_before_a_full_stop_is_tidied_before_the_checks(curated_payload):
    from src.models import ScriptPackage
    from src.script_engine import ScriptEngine
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script="In 1908 a cataclysm struck . The forest fell ,and bent."))
    out = ScriptEngine._finalize(pkg, {"id": "t"}, origin="gemini-free")
    assert out["narration_script"] == "In 1908 a cataclysm struck. The forest fell,and bent."


def test_graph_errors_carry_their_codes_so_482_is_not_a_substring_match(monkeypatch):
    from src import publisher
    calls = []

    class Resp:
        ok, status_code = False, 400

        def json(self):
            return {"error": {"message": "bad token", "code": 190, "fbtrace_id": "A482x"}}
    monkeypatch.setattr(publisher.requests, "request", lambda *a, **k: calls.append(a) or Resp())
    pub = publisher.FacebookPageReelsPublisher(token="t", page_id="48200")
    import pytest
    with pytest.raises(publisher.PublishFailed) as e:
        pub._call("POST", "https://graph.facebook.com/v1/2448200482/captions")
    assert e.value.code == 190 and e.value.subcode is None
    srt = Path(__file__)  # any existing file
    with pytest.raises(publisher.PublishFailed):
        pub.upload_captions("2448200482", srt)                                      # not treated as "locale exists"
    assert len(calls) == 2                                                          # no delete-and-retry pass


def test_a_typed_requeue_refusal_is_still_said(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    from src.telegram_state import initialize
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    initialize(db)
    monkeypatch.setattr(bot, "_api", lambda m, **kw: [{"update_id": 1, "message": {"text": "/queue requeue 1", "from": {"id": 555}, "chat": {"id": 555}, "message_id": 3}}] if m == "getUpdates" else {"message_id": 9})
    monkeypatch.setattr(bot, "delete_message", lambda mid: True)
    nudges = []
    monkeypatch.setattr(bot, "nudge", lambda text: nudges.append(text) or True)
    bot._offset = 0
    bot.poll_once(lambda c, a: ("This item cannot be requeued from its current state.", [[{"text": "x", "callback_data": "cdash:"}]]))
    bot.drain()
    assert nudges == ["This item cannot be requeued from its current state."]


def test_a_number_card_writes_a_year_as_written_and_does_not_count_it_up():
    # Voynich (dev, 2026-10-25): the card drew "1,404-1438" for "1404-1438" and would count a year up from zero.
    from src.graphics import _shown_value
    assert _shown_value("1404-1438", 0.3) == "1404-1438"
    assert _shown_value("1404-1438", 1.0) == "1404-1438"
    assert _shown_value("20,000", 0.5) == "10,000" and _shown_value("20,000", 1.0) == "20,000"
    assert _shown_value("about 280 ft", 0.5) == "about 140 ft"
    assert _shown_value("8000 tons", 1.0) == "8000 tons"                           # as the writer wrote it
    assert _shown_value("1.5 million", 1.0) == "1.5 million"
