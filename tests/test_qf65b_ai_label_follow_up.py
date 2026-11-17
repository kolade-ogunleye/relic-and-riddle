"""QF-65b: QF-65a's follow-ups.

- A Story frame or carousel slide with no record (a run made before still records) was sent to Facebook, which has no
  AI label for photo posts, whatever its run showed. Operator decision 2026-11-12 (fail closed): such a still is judged
  by its run's video (the master's scenes, the picture check's illustrated beats) and kept off Facebook unless every
  one of them traces to real media. Instagram's label is set for it as before.
- The decided card (rebuilt with its badge) dropped the AI line wherever the line's room would cost what QF-42 keeps,
  even where the card without it still had room for the line. It now keeps the line there.
No network: requests are recorded."""

import json
import types
from pathlib import Path

import pytest
import requests

from src import publisher
from tests.test_qf65a_ai_label import FAKE_JPEG, _capture, _photo, _run, _still


@pytest.fixture
def run_db(db):
    db.create_run("run_x", "01_topic")
    return db


def _bare_stills(run: Path, kind: str, n: int = 2) -> list[str]:
    """Stills with no record beside them (no still_layout.json), as a run made before still records has them."""
    folder = run / ("stories" if kind == "story" else "carousel")
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(1, n + 1):
        p = folder / (f"story_{i:02d}.jpg" if kind == "story" else f"carousel_slide_{i:02d}.jpg")
        p.write_bytes(FAKE_JPEG + p.name.encode())
        out.append(str(p))
    return out


def _publish(run_db, kind, stills, **kw):
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    return publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos(
        "run_x", "01_topic", kind, stills, **({"caption": "c"} if kind == "carousel" else {}), **kw)


def _platforms(sent):
    return sorted(v for s in sent for k, v in s["data"] if k == "platform[]")


@pytest.mark.parametrize("kind", ["story", "carousel"])
@pytest.mark.parametrize("video", ["motion", "illustration", "hook", "no video record"])
def test_a_still_without_a_record_from_a_run_whose_video_is_not_wholly_real_is_kept_off_facebook(
        tmp_path, run_db, monkeypatch, kind, video):
    if video == "no video record":
        run = tmp_path / "output" / "run_x"
        run.mkdir(parents=True)
    else:
        run, _, _ = _run(tmp_path, video)
    stills = _bare_stills(run, kind)
    sent = _capture(monkeypatch)
    res = _publish(run_db, kind, stills, platforms=["instagram", "facebook"])
    assert res["status"] == "published" and "facebook" in res.get("held_platforms", {})
    assert _platforms(sent) == ["instagram"] * (2 if kind == "story" else 1)
    assert all(("is_ai_generated", "true") in s["data"] for s in sent)
    why = res.get("held_platforms", {}).get("facebook", "")
    assert "a still with no record" in why
    assert ("no video record" in why) if video == "no video record" else ("from a video" in why)


@pytest.mark.parametrize("kind", ["story", "carousel"])
def test_a_still_without_a_record_from_a_wholly_real_video_still_goes_to_facebook(tmp_path, run_db, monkeypatch, kind):
    """Option B, not a blanket hold: the run's video traces wholly to real media (a credited photo's parallax clip and
    our own graphic), so its record-less stills are posted to Facebook; Instagram still carries the label (fail
    closed: the stills themselves cannot be traced)."""
    run, _, _ = _run(tmp_path)
    stills = _bare_stills(run, kind, 2 if kind == "story" else 12)
    sent, counts = _capture(monkeypatch), []
    record = requests.post

    def counting(url, headers=None, data=None, files=None, timeout=None):
        counts.append(len(files or []))
        return record(url, headers=headers, data=data, files=files, timeout=timeout)
    monkeypatch.setattr(requests, "post", counting)
    res = _publish(run_db, kind, stills, platforms=["instagram", "facebook"])
    assert res["status"] == "published" and "held_platforms" not in res
    assert "facebook" in _platforms(sent)
    assert all(("is_ai_generated", "true") in s["data"] for s in sent if ("platform[]", "instagram") in s["data"])
    if kind == "carousel":   # Instagram's ten, Facebook's grid of five (as test_publisher's carousel had them)
        assert [_platforms([s]) for s in sent] == [["instagram"], ["facebook"]] and counts == [10, 5]


def test_a_facebook_only_story_without_a_record_from_an_ai_video_is_not_sent(tmp_path, run_db, monkeypatch):
    run, _, _ = _run(tmp_path, "motion")
    sent = _capture(monkeypatch)
    res = _publish(run_db, "story", _bare_stills(run, "story", 1), platforms=["facebook"])
    assert res["status"] == "failed" and sent == [] and "facebook" in res.get("held_platforms", {})


def test_a_dry_run_of_an_older_carousel_plans_no_facebook_request(tmp_path, run_db):
    """The dry run records the real plan: an older run's carousel (no still records, an AI clip in its video) plans
    Instagram only, and says why Facebook is not sent."""
    run, _, _ = _run(tmp_path, "motion")
    slides = _bare_stills(run, "carousel")
    fb = run / "carousel_fb"
    fb.mkdir()
    for s in slides:
        (fb / Path(s).name).write_bytes(FAKE_JPEG)
    res = publisher.MetaPublisher(db=run_db, publishers=[]).publish_photos("run_x", "01_topic", "carousel", slides,
                                                                           dry_run=True)
    assert res["status"] == "simulated" and res["planned_parts"] == 1 and "facebook" in res.get("held_platforms", {})
    rows = [json.loads(p["response_payload_json"]) for p in run_db.list_publications("run_x")]
    assert [r["platforms"] for r in rows] == [["instagram"]] and rows[0]["is_ai_generated"] is True


def test_the_review_card_says_an_older_run_s_stills_are_kept_off_facebook(tmp_path, monkeypatch):
    """The card states what Approve posts: the record-less stills of a run whose video shows AI media are kept off
    Facebook (brief line on the card, the whole line in the reply), as for stills showing AI media."""
    from src import publishable
    from src.telegram_bot import _ai_label_line
    run, master, cover = _run(tmp_path, "motion")
    _bare_stills(run, "story")
    record = publishable.card_record({"topic_id": "topic", "title": "T", "caption": "c"}, types.SimpleNamespace(),
                                     run_dir=run, master=master, cover=cover)
    assert record["ai_label_photos"].get("facebook_hold") is True
    assert "Stories/carousel kept off Facebook" in _ai_label_line(record, brief=True)
    whole = _ai_label_line(record)
    assert "Stories/carousel: AI label on Instagram; not posted to Facebook" in whole
    assert "a still with no record (story_01.jpg) from a video showing AI media" in whole


def test_a_real_photo_still_with_its_record_is_not_held(tmp_path, run_db, monkeypatch):
    """The hold is for stills with no record only: a slide whose record names a credited photo goes to Facebook even
    when the run's video shows an AI clip (it is judged by its own record, QF-65a)."""
    run, _, _ = _run(tmp_path, "motion")
    photo = _photo(tmp_path / "assets" / "topic", "02_commons.jpg")
    slide = _still(run / "carousel" / "carousel_slide_01.jpg", {"photo": str(photo)})
    sent = _capture(monkeypatch)
    res = _publish(run_db, "carousel", [str(slide)], platforms=["instagram", "facebook"])
    assert res["status"] == "published" and "held_platforms" not in res
    assert _platforms(sent) == ["facebook", "instagram"]
    assert all(k != "is_ai_generated" for s in sent for k, _ in s["data"])


# QF-65a's card sweep (docs/reviews/quality-fixes-2026-11/QF-65a/measure.py, card_sweep), with the narration's old AI
# line on (is_generated_by_ai), as every real card has it.
SWEEP_TOPIC = {"id": "stone_city", "title": "The Stone City", "sub_theme": "Lost Cities", "hook": "h",
               "location": {"name": "Stone City", "city": "Rockford", "country": "Narnia"},
               "hashtags": ["#StoneCity", "#AncientMysteries", "#Archaeology", "#ReelsViral"]}
SWEEP_PAYLOAD = {"topic_id": "stone_city", "title": "The City Inside the Hill",
                 "first_comment": "Which level would you explore first?",
                 "hashtags": ["#StoneCity", "#Archaeology", "#BuyFollowersNow"],
                 "picture_fit": {"generated": [{"beat": 3}]}, "is_generated_by_ai": True}


def _sweep_card(findings, times, words):
    from src import publishable
    payload = dict(SWEEP_PAYLOAD, caption="A sentence of ordinary length for the caption. " * times + NL + "#StoneCity",
                   carousel_caption="Swipe through the rooms. " * times, narration_script="Word " * words,
                   qa_summary="QA findings" + NL + NL.join(f"· finding {n} " + "detail " * 12 for n in range(findings)))
    db = types.SimpleNamespace(get_topic_by_id=lambda t: SWEEP_TOPIC if t == "stone_city" else None)
    return payload, publishable.card_record(dict(payload), db)


NL = chr(10)
BADGES = {"approved_3": NL * 2 + "✅ <b>Approved</b> by Ada · release check and publishing under way",
          "rejected_3": NL * 2 + "❌ <b>Rejected</b> by Ada",
          "approved_64": NL * 2 + "✅ <b>Approved</b> by " + "N" * 64 + " · release check and publishing under way",
          "rejected_64": NL * 2 + "❌ <b>Rejected</b> by " + "N" * 64}


@pytest.mark.parametrize(("shape", "badge"), [
    ((0, 3, 603), "approved_3"), ((0, 3, 603), "approved_64"), ((0, 3, 603), "rejected_64"),
    ((0, 18, 534), "approved_3"), ((0, 18, 534), "rejected_3"), ((0, 18, 534), "rejected_64")])
def test_the_decided_card_keeps_the_ai_line_where_the_card_has_room_for_it(shape, badge):
    """QF-65a round 3, non-blocking 1: where the AI line's room would cost what QF-42 keeps, the decided card was
    QF-42's card with no AI line at all, its narration's old AI line dropped too, so the room that line freed went
    unused (3 of 11 real decided renders without the line had room for it). Now the line, whole or brief, is put back
    under the title there: it is the card's only change, and the card stays in the badge's room."""
    from src.telegram_bot import (
        CARD_PAGE_LIMIT,
        TelegramApprovalBot,
        _ai_label_line,
        _tg_len,
        _without_ai_line,
    )
    payload, posted = _sweep_card(*shape)
    bare = {k: v for k, v in posted.items() if k not in ("ai_label", "ai_label_photos")}
    reserve = _tg_len(BADGES[badge])
    without = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": bare}, "run_sweep", reserve=reserve)
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_sweep", reserve=reserve)
    lines = [ln for ln in card.splitlines() if ln.startswith("🤖 <b>AI transparency:</b>")]
    assert lines and lines[0] in (_ai_label_line(posted), _ai_label_line(posted, brief=True))
    assert _without_ai_line(card) == _without_ai_line(without)          # the line is the card's only change
    assert _tg_len(card) <= CARD_PAGE_LIMIT - reserve                    # in the badge's room (QF-43)
