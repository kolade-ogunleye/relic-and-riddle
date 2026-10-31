"""Run audit of 29 Oct (docs/reviews/run-audit-2026-10-29, items 1 and 13): a graphic the hook names, placed on
the beat after the hook, blocked the release; the blocked reasons listed a minor note; the card showed the
checker's raw string; a one-item list said where the full list is instead of showing it, and said it twice."""
from src import config
from src.lineage import record_graphic
from src.release import check_problems, release_problems, write_release_report
from src.scene_planner import lock_narrative_graphics
from src.telegram_bot import CARD_LIST_MORE, TelegramApprovalBot, _capped
from src.video_qa import QualityAuditor

HOOK = "A subterranean city plunges roughly 280 feet beneath the plains of central Turkey."
BEAT_1 = "Rediscovered in 1963 by a man knocking through a wall of his house."
BEAT_2 = "Its doors were stone wheels rolled across the passages from inside."
# The production run's word timings (rc72): the hook ends at 6.4 s, where the next cut begins.
WORDS = [("A", 0.0), ("subterranean", 0.1), ("city", 0.9), ("plunges", 1.4), ("roughly", 1.9), ("280", 2.3),
         ("feet", 2.5), ("beneath", 2.8), ("the", 3.1), ("plains", 3.3), ("of", 3.7), ("central", 4.2),
         ("Turkey.", 5.5), ("Rediscovered", 6.4), ("in", 7.1), ("1963", 7.3), ("by", 8.0), ("a", 8.1),
         ("man", 8.3), ("knocking", 8.6), ("through", 9.0), ("a", 9.3), ("wall", 9.5), ("of", 9.8),
         ("his", 9.9), ("house.", 10.2), ("Its", 11.0), ("doors", 11.3), ("were", 11.7), ("stone", 12.0),
         ("wheels", 12.4), ("rolled", 12.9), ("across", 13.3), ("the", 13.7), ("passages", 13.9),
         ("from", 14.5), ("inside.", 14.8)]


def _words(pairs=WORDS):
    return [{"word": w, "start": t, "dur": 0.3} for w, t in pairs]


def _scale(tmp_path):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    graphic = tmp_path / "graphic_scale.mp4"
    graphic.write_bytes(b"graphic")
    record_graphic(graphic, code, {"payload": {"scale_fact": {
        "value": 280, "unit": "ft", "subject": "Underground city", "dimension": "depth"}}})
    return graphic


def _timeline(tmp_path, scenes, texts=(HOOK, BEAT_1, BEAT_2)):
    bounds = [(0.0, 6.4), (6.4, 11.0), (11.0, 15.5)]
    return [{"asset": str(a), "start": s, "end": e, "text": t} for a, (s, e), t in zip(scenes, bounds, texts)]


def _graphic_issues(tmp_path, timeline, words):
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline, words)
    return [i for i in report["issues"] if i["type"] in ("semantic_desync", "temporal_desync")]


def test_the_planner_places_a_graphic_the_hook_names_after_it_and_the_video_check_accepts_it(tmp_path):
    graphic = _scale(tmp_path)
    beats = [{"text": HOOK}, {"text": BEAT_1}, {"text": BEAT_2}]
    locked = lock_narrative_graphics(beats, [graphic])
    assert locked == {1: graphic}  # only the hook names it, and the hook keeps its photo
    scenes = [tmp_path / "hook.jpg", tmp_path / "house.jpg", tmp_path / "door.jpg"]
    for at, g in locked.items():
        scenes[at] = g
    assert _graphic_issues(tmp_path, _timeline(tmp_path, scenes), _words()) == []


def test_the_check_stays_strict_away_from_the_hook(tmp_path):
    graphic = _scale(tmp_path)
    # The hook says nothing the graphic shows: the beat after it gets no wider window.
    other_hook = "Beneath a quiet village, a wall gave way to a dark passage nobody expected."
    words = [{"word": w, "start": 0.5 * k, "dur": 0.3} for k, w in enumerate(other_hook.split())] + _words(WORDS[13:])
    scenes = [tmp_path / "hook.jpg", graphic, tmp_path / "door.jpg"]
    issues = _graphic_issues(tmp_path, _timeline(tmp_path, scenes, (other_hook, BEAT_1, BEAT_2)), words)
    assert [i["type"] for i in issues] == ["semantic_desync"]
    # Named in the hook but shown two cuts later: its words are nowhere near, so it still fails.
    scenes = [tmp_path / "hook.jpg", tmp_path / "house.jpg", graphic]
    assert [i["type"] for i in _graphic_issues(tmp_path, _timeline(tmp_path, scenes), _words())] == ["semantic_desync"]


def _report(tmp_path, monkeypatch, issues):
    from src.credits import sha256_file
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", False)
    master = tmp_path / "master.mp4"
    master.write_bytes(b"master")
    run = {"master_video_path": str(master), "script_payload": {
        "self_healing": {"master_sha256": sha256_file(master), "issues": issues}}}
    return write_release_report(run, dry_run=True)


MAJOR = {"type": "semantic_desync", "severity": "major", "scene_index": 1, "asset": "x/graphic_scale.mp4",
         "issue": "Scene 1: Graphic 'graphic_scale.mp4' missing narration keywords ['280', '85', 'city', 'depth']"}
MINOR = {"type": "video_check", "severity": "minor", "where": "beat 10, 0:48-0:53",
         "issue": "the turnstile photo is soft"}


def test_the_blocked_reasons_are_the_blocking_findings_and_minor_ones_are_notes(tmp_path, monkeypatch):
    report = _report(tmp_path, monkeypatch, [MAJOR, MINOR])
    visual = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert visual["status"] == "fail" and visual["detail"] == [MAJOR] and visual["notes"] == [MINOR]
    problems = [p for p in release_problems(report, dry_run=True) if p.startswith("visual_checks")]
    assert problems == ["visual_checks: Beat 2: the scale graphic appears without the words it shows nearby "
                        "(280, 85, city, depth)"]
    assert check_problems(visual, "notes") == ["beat 10, 0:48-0:53 (minor): the turnstile photo is soft"]


def test_the_card_shows_the_finding_in_plain_words_and_the_minor_note_apart(tmp_path, monkeypatch):
    lines = TelegramApprovalBot.release_lines(_report(tmp_path, monkeypatch, [MAJOR, MINOR]))
    assert "missing narration keywords" not in lines and "graphic_scale.mp4" not in lines
    assert "Beat 2: the scale graphic appears without the words it shows nearby" in lines
    reason = lines.index("Beat 2: the scale graphic")
    note = lines.index("visual_checks (minor, not blocking): beat 10, 0:48-0:53 (minor): the turnstile photo is soft")
    assert reason < note


def test_a_one_item_list_is_shown_and_where_the_rest_is_is_said_once():
    assert _capped(["only one"], 0) == ["only one"]
    assert _capped(["a", "b"], 1) == ["a", "b"]
    assert _capped(["a", "b", "c"], 1) == ["a", f"<i>…and 2 more: {CARD_LIST_MORE}</i>"]
    payload = {"title": "T", "narration_script": "word " * 20,
               "qa_summary": "🔎 Automated video check: 3 issue(s)\n· one\n· two\n· three",
               "self_healing": {"issues": [{"severity": "info", "card": "cover", "issue": f"note {n}"} for n in range(3)]},
               "beat_image_note": "🖼 Photos: 1 line", "beat_image_lines": ["line 2: the only photo"]}
    card = TelegramApprovalBot._card_text(payload, "run_x", None, 0)
    assert "line 2: the only photo" in card and "1 item" not in card
    assert card.count(CARD_LIST_MORE) == 1
    assert "3 items:" in card and "<i>3 items</i>" in card  # the second list counts; the first says where
