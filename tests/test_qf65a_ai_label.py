"""QF-65a: any post whose media holds an AI-generated or AI-edited picture or clip carries the AI label on every platform
path, derived from the run's media records (an illustration, an AI motion clip, a hook clip with no record); missing or
unreadable records set it (fail closed); the review card says it is on, and why. No network: requests are recorded."""

import json
import shutil
import types
from pathlib import Path

import pytest
import requests

from src import config, publisher
from src.lineage import record_derived

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class _Resp:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _photo(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(FAKE_JPEG + name.encode())
    credits = folder / "credits.json"
    data = json.loads(credits.read_text(encoding="utf-8")) if credits.is_file() else {"images": []}
    data["images"].append({"file": name, "license": "CC0"})
    credits.write_text(json.dumps(data), encoding="utf-8")
    return p


def _still(path: Path, recipe: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_JPEG + path.name.encode())
    side = path.parent / "still_layout.json"
    data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
    data[path.name] = {"recipe": recipe}
    side.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(tmp_path: Path, extra: str = "") -> tuple[Path, Path, Path]:
    """A run folder: a master whose scenes are a parallax clip of a credited photo and our own map graphic, plus
    `extra` ("motion", "illustration", "hook"), and a cover drawn from the photo."""
    pool = tmp_path / "assets" / "topic"
    photo = _photo(pool, "01_commons.jpg")
    run = tmp_path / "output" / "run_x"
    (run / "parallax").mkdir(parents=True)
    (run / "graphics").mkdir()
    scenes = []
    clip = run / "parallax" / "scene_01.mp4"
    clip.write_bytes(b"clip1")
    record_derived(clip, [photo], "parallax", {"preset": "push"})
    scenes.append(clip)
    gen = tmp_path / "generator.py"
    gen.write_text("# generator\n", encoding="utf-8")
    graphic = run / "graphics" / "graphic_map.mp4"
    graphic.write_bytes(b"map")
    record_derived(graphic, [gen], "procedural_graphic", {"original_drawing": True, "media_sources": []})
    scenes.append(graphic)
    if extra == "motion":
        (run / "motion").mkdir()
        motion = run / "motion" / "motion_01.mp4"
        motion.write_bytes(b"ai clip")
        record_derived(motion, [photo], "ai_motion", {"provider": "kling", "ai_generated": True})
        scenes.append(motion)
    if extra == "illustration":
        art = run / "gap_gen_beat04.jpg"
        art.write_bytes(FAKE_JPEG + b"art")
        px = run / "parallax" / "scene_04.mp4"
        px.write_bytes(b"clip4")
        record_derived(px, [art], "parallax", {"preset": "drift"})
        scenes.append(px)
    if extra == "hook":
        hook = pool / "hook_motion.mp4"
        hook.write_bytes(b"hook")
        scenes.insert(0, hook)
    master = run / "topic_master.mp4"
    master.write_bytes(b"master")
    master.with_suffix(".composition.json").write_text(
        json.dumps({"scenes": {str(s): "x" for s in scenes}}), encoding="utf-8")
    cover = _still(run / "thumbnail_cover.jpg", {"renderer": "visual_engine.redraw_cover", "hero": str(photo)})
    return run, master, cover


PAYLOAD = {"topic_id": "topic", "title": "T", "caption": "A caption.", "description": "Long description."}


@pytest.fixture
def setting_off(monkeypatch):
    """The narration setting off, so only the media decide (the gap: nothing forced the label)."""
    monkeypatch.setattr(config, "META_IS_AI_GENERATED", False)


def _envelope(master, cover, payload=None, db=None, platforms=None):
    mp = publisher.MetaPublisher(db=db or types.SimpleNamespace(), publishers=[])
    return mp.build_envelope("run_x", master, cover, cover, dict(payload or PAYLOAD), platforms=platforms)


@pytest.mark.parametrize("extra", ["motion", "illustration", "hook"])
def test_ai_media_force_the_label_with_the_setting_off(tmp_path, setting_off, extra):
    _, master, cover = _run(tmp_path, extra)
    env = _envelope(master, cover)
    assert env["metadata"]["is_ai_generated"] is True
    why = " ".join(env["ai_label"]["reasons"])
    assert {"motion": "AI motion clip (motion_01.mp4, kling)", "illustration": "AI illustration (gap_gen_beat04.jpg)",
            "hook": "a clip with no record of where it came from (hook_motion.mp4)"}[extra] in why


def test_an_illustrated_beat_in_the_picture_check_forces_the_label(tmp_path, setting_off):
    _, master, cover = _run(tmp_path)
    env = _envelope(master, cover, dict(PAYLOAD, picture_fit={"generated": [{"beat": 6}]}))
    assert env["metadata"]["is_ai_generated"] is True
    assert "AI illustration (beat 6)" in env["ai_label"]["reasons"]


def test_real_photos_footage_and_graphics_do_not_set_it(tmp_path, setting_off):
    _, master, cover = _run(tmp_path)
    env = _envelope(master, cover)
    assert env["metadata"]["is_ai_generated"] is False and env["ai_label"]["reasons"] == []


def test_the_setting_still_sets_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "META_IS_AI_GENERATED", True)
    _, master, cover = _run(tmp_path)
    env = _envelope(master, cover)
    assert env["metadata"]["is_ai_generated"] is True
    assert env["ai_label"]["reasons"] == ["synthetic narration (setting)"]


@pytest.mark.parametrize("damage", ["no manifest", "bad manifest", "scene gone", "bad lineage", "no cover record",
                                    "uncredited photo"])
def test_missing_or_unreadable_provenance_sets_it(tmp_path, setting_off, damage):
    run, master, cover = _run(tmp_path)
    manifest = master.with_suffix(".composition.json")
    if damage == "no manifest":
        manifest.unlink()
    elif damage == "bad manifest":
        manifest.write_text("{not json", encoding="utf-8")
    elif damage == "scene gone":
        (run / "parallax" / "scene_01.mp4").unlink()
    elif damage == "bad lineage":
        (run / "parallax" / "scene_01.mp4.lineage.json").write_text("[", encoding="utf-8")
    elif damage == "no cover record":
        (run / "still_layout.json").unlink()
    else:
        (tmp_path / "assets" / "topic" / "credits.json").write_text('{"images": []}', encoding="utf-8")
    env = _envelope(master, cover)
    assert env["metadata"]["is_ai_generated"] is True and env["ai_label"]["unknown"] is True


def _capture(monkeypatch):
    sent = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append({"url": url, "data": list(data)})
        names = [v for k, v in data if k == "platform[]"]
        return _Resp({"success": True, "results": {p: {"success": True, "url": f"https://{p}/x"} for p in names}})
    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_reel_short_and_documentary_requests_carry_it(tmp_path, setting_off, monkeypatch):
    _, master, cover = _run(tmp_path, "motion")
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    reel = _envelope(master, cover)
    up.publish(reel)
    up.publish_short(reel)
    up.publish(_envelope(master, cover, platforms=["youtube"]))
    reel_data, short_data, doc_data = (dict(s["data"]) for s in sent)
    assert reel_data["is_ai_generated"] == "true" and reel_data["facebook_is_ai_generated"] == "true"
    assert short_data["containsSyntheticMedia"] == "true" and doc_data["containsSyntheticMedia"] == "true"


def test_the_direct_graph_publishers_send_it(monkeypatch):
    calls = []

    def fake_request(method, url, timeout=None, **kw):
        calls.append((url, kw.get("data")))
        if url.endswith("/media"):
            return _Resp({"id": "c1"})
        if "fields" in str(kw.get("params") or ""):
            return _Resp({"status_code": "FINISHED", "status": {"video_status": "ready"}})
        if "video_reels" in url and isinstance(kw.get("data"), dict) and kw["data"].get("upload_phase") == "start":
            return _Resp({"video_id": "v1", "upload_url": "https://rupload.example/v1"})
        return _Resp({"id": "m1", "success": True})
    monkeypatch.setattr(requests, "request", fake_request)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        video = Path(d) / "v.mp4"
        video.write_bytes(b"0" * 10)
        env = {"run_id": "r", "topic_id": "t", "platforms": ["instagram"], "media": {"video_path": str(video)},
               "metadata": {"caption": "c", "is_ai_generated": True}}
        publisher.InstagramGraphPublisher(token="t" * 20, ig_user_id="1", poll_interval=0).publish(env)
        publisher.FacebookPageReelsPublisher(token="t" * 20, page_id="2", poll_interval=0).publish(
            dict(env, platforms=["facebook"]))
    container = next(data for url, data in calls if url.endswith("/media"))
    finish = next(data for url, data in calls if isinstance(data, dict) and data.get("upload_phase") == "finish")
    assert container["is_ai_generated"] == "true" and finish["is_ai_generated"] == "true"


@pytest.fixture
def run_db(db):
    db.create_run("run_x", "01_topic")
    return db


def _frames(tmp_path, photo: Path, folder: str, names: list[str]) -> list[str]:
    return [str(_still(tmp_path / "output" / "run_x" / folder / n, {"photo": str(photo)})) for n in names]


def test_a_story_showing_an_illustration_is_labelled_on_instagram_and_kept_off_facebook(tmp_path, run_db, monkeypatch):
    art = tmp_path / "output" / "run_x" / "gap_gen_beat04.jpg"
    art.parent.mkdir(parents=True)
    art.write_bytes(FAKE_JPEG)
    frames = _frames(tmp_path, art, "stories", ["story_01_hook.jpg"])
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    res = publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos("run_x", "01_topic", "story", frames)
    assert res["status"] == "published" and "facebook" in res["held_platforms"]
    data = sent[0]["data"]
    assert [v for k, v in data if k == "platform[]"] == ["instagram"] and ("is_ai_generated", "true") in data
    assert "AI illustration (gap_gen_beat04.jpg)" in res["held_platforms"]["facebook"]


def test_a_carousel_of_real_photos_goes_to_both_unflagged(tmp_path, run_db, monkeypatch):
    photo = _photo(tmp_path / "assets" / "topic", "01_commons.jpg")
    slides = _frames(tmp_path, photo, "carousel", ["carousel_slide_01.jpg", "carousel_slide_02.jpg"])
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    res = publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos("run_x", "01_topic", "carousel", slides,
                                                                             caption="c")
    assert res["status"] == "published" and "held_platforms" not in res
    assert sorted(v for s in sent for k, v in s["data"] if k == "platform[]") == ["facebook", "instagram"]
    assert all(k != "is_ai_generated" for s in sent for k, _ in s["data"])


def test_a_facebook_only_request_with_ai_media_is_not_sent(tmp_path, run_db, monkeypatch):
    art = tmp_path / "output" / "run_x" / "gap_gen_beat04.jpg"
    art.parent.mkdir(parents=True)
    art.write_bytes(FAKE_JPEG)
    frames = _frames(tmp_path, art, "stories", ["story_01_hook.jpg"])
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    res = publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos("run_x", "01_topic", "story", frames,
                                                                             platforms=["facebook"])
    assert res["status"] == "failed" and sent == [] and "facebook" in res["held_platforms"]


def test_a_still_without_a_record_is_labelled_on_instagram(tmp_path, run_db, monkeypatch):
    """Fail closed: a slide whose record is missing cannot be traced, so Instagram's label is set. (Facebook, which has
    no label for photos, is judged by the run's video since QF-65b: tests/test_qf65b_ai_label_follow_up.py.)"""
    slide = tmp_path / "output" / "run_x" / "carousel" / "carousel_slide_01.jpg"
    slide.parent.mkdir(parents=True)
    slide.write_bytes(FAKE_JPEG)
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    res = publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos("run_x", "01_topic", "carousel",
                                                                             [str(slide)], caption="c")
    assert res["status"] == "published"
    ig = next(s["data"] for s in sent if ("platform[]", "instagram") in s["data"])
    assert ("is_ai_generated", "true") in ig


def test_the_review_card_says_the_label_is_on_and_why(tmp_path, setting_off, monkeypatch):
    from src import publishable
    from src.telegram_bot import TelegramApprovalBot
    run, master, cover = _run(tmp_path, "motion")
    record = publishable.card_record(dict(PAYLOAD), types.SimpleNamespace(), run_dir=run, master=master, cover=cover)
    assert record["ai_label"]["on"] is True
    card = TelegramApprovalBot.format_card_text(dict(PAYLOAD, narration_script="n", _publish_copy=record), "run_x")
    assert "AI label on (Instagram, Facebook, YouTube): AI motion clip (motion_01.mp4, kling)" in card
    shutil.rmtree(run / "motion")
    master.with_suffix(".composition.json").write_text(
        json.dumps({"scenes": {str(run / "parallax" / "scene_01.mp4"): "x"}}), encoding="utf-8")
    off = publishable.card_record(dict(PAYLOAD), types.SimpleNamespace(), run_dir=run, master=master, cover=cover)
    card = TelegramApprovalBot.format_card_text(dict(PAYLOAD, narration_script="n", _publish_copy=off), "run_x")
    assert "no AI label: no AI picture or clip in this video" in card


def test_the_envelope_keeps_the_label_the_card_showed(tmp_path, setting_off, monkeypatch):
    from src import publishable
    _, master, cover = _run(tmp_path)
    shown = publishable.card_record(dict(PAYLOAD), types.SimpleNamespace(), master=master, cover=cover)
    shown["ai_label"] = dict(shown["ai_label"], on=True)
    monkeypatch.setattr(publishable, "load_record", lambda db, run_id: shown)
    assert _envelope(master, cover)["metadata"]["is_ai_generated"] is True


def test_a_full_card_shows_the_ai_line_and_the_also_posted_count_together(tmp_path, setting_off):
    """The 900-word card of QF-43's room test: the AI line is laid out in the room the fitted card leaves, as the slot
    line and the "Also posted" count are; it costs the narration nothing, the count still shows, the line says the
    label is on and why (briefly when that is the room there is), and the reply under the card has it whole."""
    import test_qf43_publishable_card as q

    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _tg_len, needs_publish_reply
    from src.telegram_pages import PAGE_ABOVE
    _, master, cover = _run(tmp_path, "motion")
    payload = dict(q.PAYLOAD, caption="A sentence of ordinary length for the caption. " * 16 + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * 12, narration_script="Word " * 900,
                   qa_summary="QA findings\n" + "\n".join(f"· finding {n} " + "detail " * 12 for n in range(12)))
    posted = publishable.card_record(payload, q._TopicOnly(), master=master, cover=cover)
    posted["copy"].update({f"alt.instagram.{n}": "A carved stone stair winds down into a dark hall. " * 4
                           for n in range(1, 11)})
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt")
    assert "📤 <b>Also posted:</b> <i>14 items in the reply</i>" in card and _tg_len(card) <= PAGE_ABOVE
    line = next(ln for ln in card.splitlines() if ln.startswith("🤖 <b>AI transparency:</b>"))
    assert "AI label on" in line and "AI illustration (beat 3)" in line
    assert needs_publish_reply(card, posted)
    full = TelegramApprovalBot._card_text({**payload, "_publish_copy": posted}, "run_alt", None, None, 10 ** 6,
                                          extras=True)
    assert "AI label on (Instagram, Facebook, YouTube): AI illustration (beat 3); AI motion clip (motion_01.mp4" in full


@pytest.mark.parametrize("narration_words", [590, 660, 670])
def test_the_ai_line_never_costs_a_caption_or_the_whole_narration(narration_words):
    """Review of QF-65a round 1 measured on 35 real cards; round 2 found one real card whose caption lost its
    "see Edit" line (and these fixture cards: a caption gone, the narration cut) when the brief AI line took its room
    from a card QF-42 had already fitted to the minimum. The line takes the page's remaining room then, and when no
    fit keeps what QF-42 keeps, the slot line or the "Also posted" count goes to the reply instead (Approve's lines
    are whole there): every caption stays named, the narration whole, the AI line on the card."""
    import test_qf43_publishable_card as q

    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _tg_len, needs_publish_reply
    from src.telegram_pages import PAGE_ABOVE

    def named(card):
        return sum(card.count(f"<b>{label}:</b>") for label in ("Caption", "Carousel caption"))

    payload = dict(q.PAYLOAD, caption="A sentence of ordinary length for the caption. " * 16 + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * 12, narration_script="Word " * narration_words,
                   qa_summary="QA findings\n")
    posted = publishable.card_record(payload, q._TopicOnly())
    bare = {k: v for k, v in posted.items() if k not in ("ai_label", "ai_label_photos")}
    without = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": bare}, "run_alt")
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt")
    assert named(without) >= 1 and "[shortened:" not in without     # what QF-42 keeps on the card without the line
    assert named(card) >= named(without)                             # each of those captions still named
    assert "[shortened:" not in card                                 # the narration still whole
    assert any(ln.startswith("🤖 <b>AI transparency:</b> AI label on") for ln in card.splitlines())
    assert _tg_len(card) <= PAGE_ABOVE and needs_publish_reply(card, posted)


DECIDED_BADGES = {"approved": "\n\n✅ <b>Approved</b> by Ada · release check and publishing under way",
                  "rejected": "\n\n❌ <b>Rejected</b> by Ada"}


@pytest.mark.parametrize(("times", "narration_words", "decision"),
                         [(3, 626, "approved"), (10, 649, "approved"), (10, 603, "approved"),
                          (3, 649, "rejected"), (3, 672, "rejected"), (10, 603, "rejected")])
def test_the_decided_card_never_gives_a_caption_or_the_narration_to_the_ai_line(times, narration_words, decision):
    """Review of QF-65a round 2: the decided card (the card rebuilt with its badge, _render_decision) has no slot
    line or "Also posted" count to give way, and it stays in CARD_PAGE_LIMIT less the badge (QF-43), so the brief AI
    line took a caption (the caption x3, 626-word card with the Approved badge) or cut the narration (x3, 672 words,
    Rejected). Where the line would cost what QF-42 keeps, the decided card is QF-42's card without the line: every
    caption the card without the line names stays named, the narration whole, and the card in the badge's room."""
    import test_qf43_publishable_card as q

    from src import publishable
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len
    from src.telegram_pages import PAGE_ABOVE

    def named(card):
        return sum(card.count(f"<b>{label}:</b>") for label in ("Caption", "Carousel caption"))

    badge = DECIDED_BADGES[decision]
    payload = dict(q.PAYLOAD, caption="A sentence of ordinary length for the caption. " * times + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * times, narration_script="Word " * narration_words,
                   qa_summary="QA findings\n")
    posted = publishable.card_record(payload, q._TopicOnly())
    bare = {k: v for k, v in posted.items() if k not in ("ai_label", "ai_label_photos")}
    without = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": bare}, "run_alt",
                                                   reserve=_tg_len(badge))
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt",
                                                reserve=_tg_len(badge))
    assert named(without) >= 1 or "[shortened:" not in without      # what the card without the line keeps
    assert named(card) >= named(without)                             # each caption it names still named
    assert "[shortened:" not in card or "[shortened:" in without     # the narration not newly cut
    assert (any(ln.startswith("🤖 <b>AI transparency:</b> AI label on") for ln in card.splitlines())
            or card == without)                                      # the line, or QF-42's card exactly
    assert _tg_len(card) <= CARD_PAGE_LIMIT - _tg_len(badge)         # in the badge's room (QF-43)
    assert _tg_len(card) + _tg_len(badge) <= PAGE_ABOVE              # the card and its badge: one message


def test_the_decided_card_keeps_one_message_when_the_fit_cannot_shrink(monkeypatch):
    """Round 3, 2 of 140 real decided renders (the Approved badge with a 64-character name): QF-42's fit has a floor
    above the page less the badge (it cannot shrink further), so the brief AI line pushed the card and its badge past
    PAGE_ABOVE. The floor is simulated here: the card and its badge stay one message under PAGE_ABOVE, and the line,
    which no longer fits, is in the reply under the card (needs_publish_reply)."""
    import test_qf43_publishable_card as q

    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _tg_len, needs_publish_reply
    from src.telegram_pages import PAGE_ABOVE

    badge = "\n\n✅ <b>Approved</b> by " + "N" * 64 + " · release check and publishing under way"
    fitted = TelegramApprovalBot._fitted_card
    monkeypatch.setattr(TelegramApprovalBot, "_fitted_card",
                        staticmethod(lambda p, r, rel, limit: fitted(p, r, rel, max(limit, PAGE_ABOVE - 160))))
    payload = dict(q.PAYLOAD, caption="A sentence of ordinary length for the caption. " * 16 + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * 12, narration_script="Word " * 900,
                   qa_summary="QA findings\n")
    posted = publishable.card_record(payload, q._TopicOnly())
    bare = {k: v for k, v in posted.items() if k not in ("ai_label", "ai_label_photos")}
    without = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": bare}, "run_alt",
                                                   reserve=_tg_len(badge))
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt",
                                                reserve=_tg_len(badge))
    assert _tg_len(without) + _tg_len(badge) <= PAGE_ABOVE                # the floor alone fits the page
    assert _tg_len(card) + _tg_len(badge) <= PAGE_ABOVE                   # and with the record: one message
    assert needs_publish_reply(card, posted)                                # the line is whole in the reply


def test_a_decided_card_without_the_line_changes_no_post_s_ai_label(tmp_path, setting_off, run_db, monkeypatch):
    """Round 3 (lander): the decided card may show no AI line (where it would cost what QF-42 keeps, or past QF-42's
    floor). That is only the operator's Telegram text. The label is decided from the record and the media at publish
    time, never from the card: the Reel, Short and documentary requests still carry it, and a Story showing an
    illustration is still flagged on Instagram and held off Facebook."""
    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _tg_len
    from src.telegram_pages import PAGE_ABOVE
    run, master, cover = _run(tmp_path, "motion")
    payload = dict(PAYLOAD, caption="A sentence of ordinary length for the caption. " * 16 + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * 12, narration_script="Word " * 900,
                   qa_summary="QA findings\n")
    record = publishable.card_record(dict(payload), types.SimpleNamespace(), run_dir=run, master=master, cover=cover)
    assert record["ai_label"]["on"] is True
    badge = "\n\n✅ <b>Approved</b> by " + "N" * 64 + " · release check and publishing under way"
    fitted = TelegramApprovalBot._fitted_card
    with monkeypatch.context() as floor:   # QF-42's floor above the badge's room: the decided card has no line
        floor.setattr(TelegramApprovalBot, "_fitted_card",
                      staticmethod(lambda p, r, rel, limit: fitted(p, r, rel, max(limit, PAGE_ABOVE - 160))))
        decided = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": record}, "run_x",
                                                       reserve=_tg_len(badge)) + badge
    assert "AI transparency" not in decided and "✅ <b>Approved</b>" in decided
    monkeypatch.setattr(publishable, "load_record", lambda db, run_id: record)   # what the card was built from
    sent = _capture(monkeypatch)
    up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
    reel = _envelope(master, cover, payload=payload)
    assert reel["metadata"]["is_ai_generated"] is True
    up.publish(reel)
    up.publish_short(reel)
    monkeypatch.setattr(publishable, "load_record", lambda db, run_id: None)   # the documentary: its own copy check
    up.publish(_envelope(master, cover, payload=dict(PAYLOAD), platforms=["youtube"]))
    reel_data, short_data, doc_data = (dict(s["data"]) for s in sent)
    assert reel_data["is_ai_generated"] == "true" and reel_data["facebook_is_ai_generated"] == "true"
    assert short_data["containsSyntheticMedia"] == "true" and doc_data["containsSyntheticMedia"] == "true"
    art = run / "gap_gen_beat04.jpg"
    art.write_bytes(FAKE_JPEG)
    frames = _frames(tmp_path, art, "stories", ["story_01_hook.jpg"])
    res = publisher.MetaPublisher(db=run_db, publishers=[up]).publish_photos("run_x", "01_topic", "story", frames)
    story = sent[3]["data"]
    assert res["status"] == "published" and "facebook" in res["held_platforms"]
    assert [v for k, v in story if k == "platform[]"] == ["instagram"] and ("is_ai_generated", "true") in story


def test_a_carousel_drawn_at_its_slot_is_not_claimed_by_the_cards_photo_line():
    """Port onto QF-81: a planned carousel has no slides at review, so the card's photo assessment holds the Stories
    only; the line names the Stories (the slot labels the carousel from its slides, and its alert names a Facebook
    hold), and it is never longer than the line it replaces."""
    from src.telegram_bot import _ai_label_line
    label = {"on": True, "ai_media": False, "unknown": False, "setting": True, "reasons": ["synthetic narration (setting)"],
             "ai_reasons": [], "unknown_reasons": []}
    photos = {"on": True, "ai_media": True, "unknown": False, "setting": False,
              "reasons": ["AI illustration (gap_gen_beat04.jpg)"], "ai_reasons": ["AI illustration (gap_gen_beat04.jpg)"],
              "unknown_reasons": []}
    for brief in (False, True):
        drawn = _ai_label_line({"ai_label": label, "ai_label_photos": photos}, brief=brief)
        at_slot = _ai_label_line({"ai_label": label, "ai_label_photos": photos, "carousel_at_slot": True}, brief=brief)
        assert "Stories/carousel" in drawn and "Stories/carousel" not in at_slot and "Stories" in at_slot
        assert len(at_slot) <= len(drawn)
