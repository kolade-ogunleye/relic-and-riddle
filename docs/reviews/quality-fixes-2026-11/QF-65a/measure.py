"""QF-65a measurement: AI media force the AI label on every platform path; missing provenance sets it.

    python docs/reviews/quality-fixes-2026-11/QF-65a/measure.py <repo root> [--runs DIR ...]

<repo root> is the code measured (the parent or this row). Prints JSON; writes nothing in either tree (fixtures go to a
temporary folder that is removed). No network: sockets are refused and every request is recorded locally. The
narration setting (META_IS_AI_GENERATED) is measured OFF, so only the media decide (with it on, every Reel was and is
labelled; Stories and carousels never were).

- "fixture": a run folder built here (a credited photo, its parallax clip, our own map graphic, a cover drawn from the
  photo) plus one AI item per case, published through the measured code's own builders with the requests recorded.
- "runs" (--runs, any number of output folders, read only; e.g. the dev output and a COPY of the production sample's):
  COUNTS only. Per folder: runs with a master manifest; runs whose media include an AI item (an AI motion clip,
  an illustration, a hook clip, an illustrated beat in the picture check); and, with the measured code's
  src/ai_label.py when it exists, how many runs it labels (AI media / unknown provenance), and how many photo posts
  (Story frame sets, carousels) it labels. The parent has no derivation: with the setting off it labels none.
- "cards" (--cards, output folders, read only): counts only. Every run's review card as the measured code renders
  it with its record: narration shortened, narration start shown, narration characters shown (sum, median), caption
  previews and captions moved to Edit, the AI line, the "Also posted" count line, the longest card. Per card (in run
  order, no names) the same numbers and a short hash of the card's body without its AI and slot lines, so
  --parent-cards <the parent's JSON output> compares each card with the parent's: how many cards show less
  narration, by how much, how many bodies are unchanged, how many moved a caption to Edit. "decided": each card also
  rebuilt as _render_decision does on a decision (reserve = the badge, then the badge), with the Approved and Rejected
  badges and a 3- and a 64-character name, against the same render without the AI label: captions lost, narration
  newly cut, previews lost, publish lines moved, over PAGE_ABOVE (with and without the line), over CARD_PAGE_LIMIT
  (the card with its badge; QF-43 keeps the decided card in the badge's room), renders without an AI line, the
  longest.
- "card_sweep" (--sweep): the same counts on 860 fixture shapes (card_sweep), waiting and decided.
"""

import argparse
import contextlib
import hashlib
import json
import re
import socket
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _refuse(*_a, **_k):
    raise RuntimeError("measure.py makes no network call")


class _Resp:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _photo(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(FAKE_JPEG + name.encode())
    (folder / "credits.json").write_text(json.dumps({"images": [{"file": name, "license": "CC0"}]}), encoding="utf-8")
    return p


def _still(path, recipe):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_JPEG + path.name.encode())
    side = path.parent / "still_layout.json"
    data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
    data[path.name] = {"recipe": recipe}
    side.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(work, extra):
    from src.lineage import record_derived
    pool = work / "assets" / "topic"
    photo = _photo(pool, "01_commons.jpg")
    run = work / "output" / "run_x"
    (run / "parallax").mkdir(parents=True)
    (run / "graphics").mkdir()
    clip = run / "parallax" / "scene_01.mp4"
    clip.write_bytes(b"clip1")
    record_derived(clip, [photo], "parallax", {})
    gen = work / "generator.py"
    gen.write_text("# generator\n", encoding="utf-8")
    graphic = run / "graphics" / "graphic_map.mp4"
    graphic.write_bytes(b"map")
    record_derived(graphic, [gen], "procedural_graphic", {"original_drawing": True})
    scenes = [clip, graphic]
    if extra == "ai_motion_clip":
        (run / "motion").mkdir()
        motion = run / "motion" / "motion_01.mp4"
        motion.write_bytes(b"ai clip")
        record_derived(motion, [photo], "ai_motion", {"provider": "kling", "ai_generated": True})
        scenes.append(motion)
    if extra == "illustration_scene":
        art = run / "gap_gen_beat04.jpg"
        art.write_bytes(FAKE_JPEG + b"art")
        px = run / "parallax" / "scene_04.mp4"
        px.write_bytes(b"clip4")
        record_derived(px, [art], "parallax", {})
        scenes.append(px)
    if extra == "hook_clip_no_record":
        hook = pool / "hook_motion.mp4"
        hook.write_bytes(b"hook")
        scenes.insert(0, hook)
    master = run / "topic_master.mp4"
    master.write_bytes(b"master")
    if extra != "no_media_record":
        master.with_suffix(".composition.json").write_text(json.dumps({"scenes": {str(s): "x" for s in scenes}}),
                                                           encoding="utf-8")
    cover = _still(run / "thumbnail_cover.jpg", {"hero": str(photo)})
    return run, master, cover, photo


CASES = ["real_photos_only", "ai_motion_clip", "illustration_scene", "illustrated_beat_in_picture_check",
         "hook_clip_no_record", "no_media_record"]


def fixture():
    import requests

    from src import config, publisher
    out = {}
    payload = {"topic_id": "topic", "title": "T", "caption": "A caption.", "description": "Long description."}
    sent = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data))
        names = [v for k, v in data if k == "platform[]"]
        return _Resp({"success": True, "results": {p: {"success": True, "url": f"https://{p}/x"} for p in names}})

    for case in CASES:
        with tempfile.TemporaryDirectory() as d, patch.object(config, "META_IS_AI_GENERATED", False), \
                patch.object(requests, "post", fake_post):
            _, master, cover, _ = _run(Path(d), case)
            body = dict(payload, picture_fit={"generated": [{"beat": 6}]}) if "picture_check" in case else payload
            mp = publisher.MetaPublisher(db=types.SimpleNamespace(), publishers=[])
            sent.clear()
            up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
            reel = mp.build_envelope("run_x", master, cover, cover, dict(body))
            up.publish(reel)
            up.publish_short(reel)
            up.publish(mp.build_envelope("run_x", master, cover, cover, dict(body), platforms=["youtube"]))
            r, s, y = (dict(x) for x in sent)
            out[case] = {"instagram_reel": r.get("is_ai_generated") == "true",
                         "facebook_reel": r.get("facebook_is_ai_generated") == "true",
                         "youtube_short": s.get("containsSyntheticMedia") == "true",
                         "youtube_documentary": y.get("containsSyntheticMedia") == "true"}
    return out


def photos_fixture():
    """A Story frame and a carousel slide drawn from an illustration, and a carousel of a credited photo."""
    import requests

    from src import publisher
    out = {}
    for case in ("story_from_illustration", "carousel_from_illustration", "carousel_from_real_photo"):
        sent = []

        def fake_post(url, headers=None, data=None, files=None, timeout=None, _sent=sent):
            _sent.append(list(data))
            names = [v for k, v in data if k == "platform[]"]
            return _Resp({"success": True, "results": {p: {"success": True} for p in names}})
        with tempfile.TemporaryDirectory() as d, patch.object(requests, "post", fake_post):
            work = Path(d)
            from src.db import PipelineDatabase
            bank = work / "topic_bank.json"
            bank.write_text(json.dumps({"topics": [{"id": "topic", "title": "T", "hook": "h",
                                                    "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
            db = PipelineDatabase(db_path=work / "m.db", topic_bank_file=bank)
            db.create_run("run_x", "topic")
            if "illustration" in case:
                src_pic = work / "output" / "run_x" / "gap_gen_beat04.jpg"
                src_pic.parent.mkdir(parents=True)
                src_pic.write_bytes(FAKE_JPEG)
            else:
                src_pic = _photo(work / "assets" / "topic", "01_commons.jpg")
            kind = "story" if case.startswith("story") else "carousel"
            folder, name = ("stories", "story_01_hook.jpg") if kind == "story" else ("carousel", "carousel_slide_01.jpg")
            still = _still(work / "output" / "run_x" / folder / name, {"photo": str(src_pic)})
            up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
            res = publisher.MetaPublisher(db=db, publishers=[up]).publish_photos("run_x", "topic", kind, [str(still)],
                                                                                 caption="c")
            ig = [x for x in sent if ("platform[]", "instagram") in x]
            fb = [x for x in sent if ("platform[]", "facebook") in x]
            out[case] = {"status": res.get("status"),
                         "instagram_flagged": bool(ig) and all(("is_ai_generated", "true") in x for x in ig),
                         "sent_to_facebook": bool(fb),
                         "facebook_sent_unlabelled_with_ai": bool(fb) and "illustration" in case}
            with contextlib.suppress(Exception):
                db._conn.close()
    return out


def graph_fixture():
    import requests

    from src import publisher
    calls = []

    def fake_request(method, url, timeout=None, **kw):
        calls.append((url, kw.get("data")))
        if url.endswith("/media"):
            return _Resp({"id": "c1"})
        if "fields" in str(kw.get("params") or ""):
            return _Resp({"status_code": "FINISHED", "status": {"video_status": "ready"}})
        if isinstance(kw.get("data"), dict) and kw["data"].get("upload_phase") == "start":
            return _Resp({"video_id": "v1", "upload_url": "https://rupload.example/v1"})
        return _Resp({"id": "m1", "success": True})
    with tempfile.TemporaryDirectory() as d, patch.object(requests, "request", fake_request):
        video = Path(d) / "v.mp4"
        video.write_bytes(b"0" * 10)
        env = {"run_id": "r", "topic_id": "t", "platforms": ["instagram"], "media": {"video_path": str(video)},
               "metadata": {"caption": "c", "is_ai_generated": True}}
        publisher.InstagramGraphPublisher(token="t" * 20, ig_user_id="1", poll_interval=0).publish(env)
        publisher.FacebookPageReelsPublisher(token="t" * 20, page_id="2", poll_interval=0).publish(
            dict(env, platforms=["facebook"]))
    container = next(data for url, data in calls if url.endswith("/media"))
    finish = next(data for url, data in calls if isinstance(data, dict) and data.get("upload_phase") == "finish")
    return {"instagram_graph_container_flag": container.get("is_ai_generated") == "true",
            "facebook_graph_finish_flag": finish.get("is_ai_generated") == "true"}


def card_fixture():
    from src import config, publishable
    from src.telegram_bot import TelegramApprovalBot
    with tempfile.TemporaryDirectory() as d, patch.object(config, "META_IS_AI_GENERATED", False):
        run, master, cover, _ = _run(Path(d), "ai_motion_clip")
        payload = {"topic_id": "topic", "title": "T", "caption": "A caption.", "narration_script": "n"}
        try:
            record = publishable.card_record(dict(payload), types.SimpleNamespace(), run_dir=run, master=master,
                                             cover=cover)
        except TypeError:   # the parent: no master argument, nothing derived from the media
            record = publishable.card_record(dict(payload), types.SimpleNamespace(), run_dir=run)
        card = TelegramApprovalBot.format_card_text(dict(payload, _publish_copy=record), "run_x")
        line = next((ln for ln in card.splitlines() if "AI transparency" in ln), "")
        return {"card_says_label_on_with_reason": "AI label on" in line and "AI motion clip" in line}


_AI_SCENE = re.compile(r"(^|[\\/])(motion_\d+\.mp4|gap_gen_[^\\/]*|hook_motion\.mp4)$")


def runs(folder: Path) -> dict:
    """Counts only, per output folder."""
    try:
        from src import ai_label
    except ImportError:
        ai_label = None
    counts = {"runs_with_master_manifest": 0, "runs_with_ai_media": 0, "of_which_ai_motion_clip": 0,
              "of_which_illustration": 0, "of_which_hook_clip": 0,
              "labelled_with_setting_off_parent_rule": 0}
    if ai_label is not None:
        counts.update({"labelled_with_setting_off": 0, "labelled_for_ai_media": 0,
                       "labelled_for_unknown_provenance_only": 0, "ai_media_runs_labelled": 0,
                       "photo_posts_checked": 0, "photo_posts_labelled": 0, "photo_posts_with_ai_media": 0,
                       "photo_posts_unknown_only": 0, "photo_posts_with_a_still_without_record": 0})
    from src import config
    for manifest in sorted(Path(folder).glob("run_*/*.composition.json")):
        run = manifest.parent
        try:
            scenes = list(json.loads(manifest.read_text(encoding="utf-8")).get("scenes") or [])
        except (OSError, ValueError, AttributeError):
            scenes = []
        try:
            payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        counts["runs_with_master_manifest"] += 1
        names = [re.split(r"[\\/]", s)[-1] for s in scenes]
        motion = any(re.fullmatch(r"motion_\d+\.mp4", n) for n in names)
        fit = payload.get("picture_fit") if isinstance(payload, dict) else None
        art = any(n.startswith("gap_gen_") for n in names) or bool(isinstance(fit, dict) and fit.get("generated"))
        hook = "hook_motion.mp4" in names
        if motion or art or hook:
            counts["runs_with_ai_media"] += 1
        counts["of_which_ai_motion_clip"] += motion
        counts["of_which_illustration"] += art
        counts["of_which_hook_clip"] += hook
        if ai_label is None:
            continue
        with patch.object(config, "META_IS_AI_GENERATED", False):
            master = manifest.with_name(manifest.name.removesuffix(".composition.json") + ".mp4")
            label = ai_label.assess_video(master, payload, cover=run / "thumbnail_cover.jpg")
        counts["labelled_with_setting_off"] += label["on"]
        counts["labelled_for_ai_media"] += label["ai_media"]
        counts["labelled_for_unknown_provenance_only"] += label["unknown"] and not label["ai_media"]
        counts["ai_media_runs_labelled"] += bool((motion or art or hook) and label["on"])
        from src.publishable import photo_posts
        stills = photo_posts(run)
        for group in ([p for p in stills if p.parent.name == "stories"],
                      [p for p in stills if p.parent.name == "carousel"],
                      [p for p in stills if p.parent.name == "carousel_fb"]):
            if not group:
                continue
            photos = ai_label.assess_photos(group)
            counts["photo_posts_checked"] += 1
            counts["photo_posts_labelled"] += photos["on"]
            counts["photo_posts_with_ai_media"] += photos["ai_media"]
            counts["photo_posts_unknown_only"] += photos["unknown"] and not photos["ai_media"]
            counts["photo_posts_with_a_still_without_record"] += any(ai_label.still_sources(p) is None for p in group)
    return counts


NARRATION_OPEN = "<b>Narration and timings:</b>\n<i>"
NARRATION_CLOSE = "</i>\n\n<b>First comment:"
SEE_EDIT = "(too long for this card: see Edit)"


def _narration_shown(card: str) -> str:
    """The narration text a card shows (its folded block, the shortening note included)."""
    at = card.find(NARRATION_OPEN)
    end = card.find(NARRATION_CLOSE, at) if at >= 0 else -1
    return card[at + len(NARRATION_OPEN):end] if end >= 0 else ""


def _copy_shown(card: str) -> str:
    """The card's folded copy section: narration, first comment, captions and hashtags as the card shows them."""
    at = card.find("<b>Narration and timings:</b>")
    end = card.find("</blockquote>", at) if at >= 0 else -1
    return card[at:end] if end >= 0 else ""


def _body(card: str) -> str:
    """The card without its AI line (old or new) and slot line: what the narration, captions and findings show."""
    return "\n".join(ln for ln in card.splitlines()
                     if not ln.startswith(("🤖 <b>AI transparency:</b>", "🗓 <b>Slot:</b>")))


def _items_hidden(card: str) -> int:
    """How many list items a shortened card leaves to the reply ("…and N more", or a list shown as its count)."""
    return (sum(int(n) for n in re.findall(r"<i>…and (\d+) more", card))
            + sum(int(n) for n in re.findall(r"<i>(\d+) items?(?::|</i>)", card)))


def _captions_named(card: str) -> int:
    """How many captions the card names: their text, or the "see Edit" line saying where to read them."""
    return sum(card.count(f"<b>{label}:</b>") for label in ("Caption", "Carousel caption"))


def _publish_form(card: str) -> str:
    """What the card shows of Approve's lines: the slot line (yes/no) and the "Also posted" form."""
    slot = any(ln.startswith("🗓 <b>Slot:</b>") for ln in card.splitlines())
    also = ("list" if "📤 <b>Also posted, word for word</b>" in card
            else "count" if "📤 <b>Also posted:</b>" in card else "none")
    return f"slot={'yes' if slot else 'no'} also={also}"


def _median(values: list) -> float:
    v = sorted(values)
    return 0 if not v else (v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2)


def compare_cards(new: dict, parent: dict) -> dict:
    """Card by card (same folder, same run order), this commit against the parent's --cards output."""
    a, b = parent.get("per_card") or [], new.get("per_card") or []
    if len(a) != len(b):
        return {"error": f"card counts differ: parent {len(a)}, this commit {len(b)}"}
    delta = [y["narration_chars"] - x["narration_chars"] for x, y in zip(a, b)]
    shorter = [d for d in delta if d < 0]
    out = {"cards_compared": len(delta), "cards_body_unchanged": sum(x["body"] == y["body"] for x, y in zip(a, b))}
    for key, what in (("copy_chars", "copy_section"), ("body_chars", "body")):   # captions included; everything
        d = [y[key] - x[key] for x, y in zip(a, b)]
        lost = [-v for v in d if v < 0]
        out.update({f"cards_{what}_shorter": len(lost), f"cards_{what}_longer": sum(v > 0 for v in d),
                    f"{what}_chars_change_total": sum(d), f"{what}_chars_lost_median_where_shorter": _median(lost),
                    f"{what}_chars_lost_worst": max(lost, default=0)})
    return {**out,
            "cards_narration_shorter": len(shorter), "cards_narration_longer": sum(d > 0 for d in delta),
            "cards_narration_same": sum(d == 0 for d in delta),
            "narration_chars_change_total": sum(delta), "narration_chars_change_median_all": _median(delta),
            "narration_chars_lost_median_where_shorter": _median([-d for d in shorter]),
            "narration_chars_lost_worst": -min(delta, default=0),
            "narration_chars_gained_best": max([d for d in delta if d > 0], default=0),
            "cards_fewer_captions_named": sum(y["captions_named"] < x["captions_named"] for x, y in zip(a, b)),
            "cards_narration_newly_shortened": sum(y["shortened"] and not x["shortened"] for x, y in zip(a, b)),
            "cards_publish_lines_changed": sum(y["publish"] != x["publish"] for x, y in zip(a, b)),
            "cards_more_captions_to_edit": sum(y["captions_see_edit"] > x["captions_see_edit"] for x, y in zip(a, b)),
            "cards_fewer_caption_previews": sum(y["caption_previews"] < x["caption_previews"] for x, y in zip(a, b)),
            "cards_hiding_more_list_items": sum(y["items_hidden"] > x["items_hidden"] for x, y in zip(a, b)),
            "cards_hiding_fewer_list_items": sum(y["items_hidden"] < x["items_hidden"] for x, y in zip(a, b))}


# The decided card (round 3): format_card_text(..., reserve=_tg_len(badge)) + badge, as _render_decision builds it,
# with the Approved and Rejected badges and a short and a long name (64: Telegram's longest first name).
NAMES = ("Ada", "N" * 64)


def _badges() -> list:
    out = [("waiting", "")]
    for who in NAMES:
        out.append((f"approved_name_{len(who)}",
                    f"\n\n✅ <b>Approved</b> by {who} · release check and publishing under way"))
        out.append((f"rejected_name_{len(who)}", f"\n\n❌ <b>Rejected</b> by {who}"))
    return out


def _bare(record: dict) -> dict:
    """The record without the AI label (what the card shows without the line; the parent records none)."""
    return {k: v for k, v in record.items() if k not in ("ai_label", "ai_label_photos")}


def _decided(payload: dict, record: dict, run_id: str, badge: str) -> str:
    from src.telegram_bot import TelegramApprovalBot, _tg_len
    return TelegramApprovalBot.format_card_text(dict(payload, _publish_copy=record), run_id,
                                                reserve=_tg_len(badge)) + badge


def _loss(card: str, base: str) -> dict:
    """What the card with the AI line loses against the same card without it."""
    from src.telegram_bot import _caption_previews
    return {"captions_lost": _captions_named(card) < _captions_named(base),
            "narration_newly_cut": "[shortened:" in card and "[shortened:" not in base,
            "previews_lost": _caption_previews(card) < _caption_previews(base),
            "publish_lines_moved": _publish_form(card) != _publish_form(base)}


def _tally(into: dict, card: str, base: str) -> None:
    from src.telegram_bot import CARD_PAGE_LIMIT, _tg_len
    from src.telegram_pages import PAGE_ABOVE
    into["renders"] += 1
    for k, v in _loss(card, base).items():
        into[k] += v
    into["over_page_above"] += _tg_len(card) > PAGE_ABOVE
    into["over_page_above_without_the_line"] += _tg_len(base) > PAGE_ABOVE   # QF-42's own fit floor, for comparison
    into["over_card_page_limit"] += _tg_len(card) > CARD_PAGE_LIMIT            # the card and its badge
    into["no_ai_line"] += "AI transparency" not in card
    into["longest"] = max(into["longest"], _tg_len(card))


def _counter() -> dict:
    return {"renders": 0, "captions_lost": 0, "narration_newly_cut": 0, "previews_lost": 0, "publish_lines_moved": 0,
            "over_page_above": 0, "over_page_above_without_the_line": 0, "over_card_page_limit": 0, "no_ai_line": 0,
            "longest": 0}


SWEEP_TOPIC = {"id": "stone_city", "title": "The Stone City", "sub_theme": "Lost Cities", "hook": "h",
               "location": {"name": "Stone City", "city": "Rockford", "country": "Narnia"},
               "hashtags": ["#StoneCity", "#AncientMysteries", "#Archaeology", "#ReelsViral"]}
SWEEP_PAYLOAD = {"topic_id": "stone_city", "title": "The City Inside the Hill",
                 "first_comment": "Which level would you explore first?",
                 "hashtags": ["#StoneCity", "#Archaeology", "#BuyFollowersNow"],
                 "picture_fit": {"generated": [{"beat": 3}]}}


def card_sweep() -> dict:
    """Fixture shapes (tests/test_qf43's card): findings 0/2/5/9/14 x caption and carousel caption x3/10/18/30 x
    narration 120-1,100 words in steps of 23 (860 shapes), each rendered waiting and decided (the badges above, and
    the review's reserves 44/75/140 as a badge of that length), with the record and without its AI label. Counts
    only."""
    from src import publishable
    db = types.SimpleNamespace(get_topic_by_id=lambda t: SWEEP_TOPIC if t == "stone_city" else None)
    forms = _badges() + [(f"reserve_{r}", "\n\n" + "x" * (r - 2)) for r in (44, 75, 140)]
    out = {name: _counter() for name, _ in forms}
    for findings in (0, 2, 5, 9, 14):
        for times in (3, 10, 18, 30):
            for words in range(120, 1101, 23):
                payload = dict(SWEEP_PAYLOAD,
                               caption="A sentence of ordinary length for the caption. " * times + "\n#StoneCity",
                               carousel_caption="Swipe through the rooms. " * times,
                               narration_script="Word " * words,
                               qa_summary="QA findings\n" + "\n".join(f"· finding {n} " + "detail " * 12
                                                                      for n in range(findings)))
                record = publishable.card_record(dict(payload), db)
                for name, badge in forms:
                    _tally(out[name], _decided(payload, record, "run_sweep", badge),
                           _decided(payload, _bare(record), "run_sweep", badge))
    return out


def cards(folder: Path) -> dict:
    """Counts only: every real run's review card as the measured code renders it, with the card's record (the
    publishable.card_record the bot sends; no database: the topic is not looked up), the setting as configured."""
    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _caption_previews, _tg_len
    c = {"cards": 0, "lists_shortened": 0, "narration_start_shown": 0, "narration_chars_shown_total": 0,
         "narration_chars_shown_median": 0, "caption_previews": 0, "captions_see_edit": 0, "cards_with_see_edit": 0,
         "list_items_left_to_reply": 0, "captions_named": 0,
         "ai_line": 0, "ai_label_on_line": 0, "also_posted_count_line": 0, "longest_card": 0,
         "over_telegram_4096": 0, "per_card": [], "decided": _counter()}
    for meta in sorted(Path(folder).glob("run_*/script_metadata.json")):
        run = meta.parent
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        masters = sorted(run.glob("*_master.mp4"))
        try:
            try:
                rec = publishable.card_record(dict(payload), types.SimpleNamespace(), run_dir=run,
                                              master=masters[0] if masters else None, cover=run / "thumbnail_cover.jpg")
            except TypeError:   # the parent: no media arguments
                rec = publishable.card_record(dict(payload), types.SimpleNamespace(), run_dir=run)
            card = TelegramApprovalBot.format_card_text(dict(payload, _publish_copy=rec), run.name)
        except Exception:  # noqa: BLE001, S112 - a run the card cannot be built for is not counted
            continue
        for _, badge in _badges()[1:]:   # the decided card, against the same card without the AI line
            _tally(c["decided"], _decided(payload, rec, run.name, badge), _decided(payload, _bare(rec), run.name, badge))
        line = next((ln for ln in card.splitlines() if "AI transparency" in ln), "")
        c["cards"] += 1
        c["lists_shortened"] += "[shortened:" in card
        c["narration_start_shown"] += str(payload.get("narration_script") or "")[:40] in card
        c["ai_line"] += bool(line)
        c["ai_label_on_line"] += "AI label on" in line
        c["also_posted_count_line"] += "in the reply</i>" in card and "Also posted:" in card
        c["longest_card"] = max(c["longest_card"], _tg_len(card))
        c["over_telegram_4096"] += _tg_len(card) > 4096
        shown = _tg_len(_narration_shown(card))
        see_edit = card.count(SEE_EDIT)
        c["narration_chars_shown_total"] += shown
        c["caption_previews"] += _caption_previews(card)
        c["captions_see_edit"] += see_edit
        c["cards_with_see_edit"] += bool(see_edit)
        c["list_items_left_to_reply"] += _items_hidden(card)
        c["captions_named"] += _captions_named(card)
        c["per_card"].append({"narration_chars": shown, "copy_chars": _tg_len(_copy_shown(card)),
                              "body_chars": _tg_len(_body(card)), "caption_previews": _caption_previews(card),
                              "captions_named": _captions_named(card), "shortened": "[shortened:" in card,
                              "publish": _publish_form(card),
                              "captions_see_edit": see_edit, "items_hidden": _items_hidden(card),
                              "body": hashlib.sha256(_body(card).encode("utf-8")).hexdigest()[:10]})
    c["narration_chars_shown_median"] = _median([x["narration_chars"] for x in c["per_card"]])
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--runs", action="append", default=[])
    ap.add_argument("--cards", action="append", default=[], help="output folder whose runs' review cards are rendered")
    ap.add_argument("--sweep", action="store_true", help="the fixture card sweep, waiting and decided (card_sweep)")
    ap.add_argument("--parent-cards", help="the parent's JSON output of the same --cards folders, compared card by card")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    socket.socket.connect = _refuse
    import logging
    logging.disable(logging.CRITICAL)
    result = {"fixture_reel_short_documentary": fixture(), "fixture_photo_posts": photos_fixture(),
              "fixture_graph_publishers": graph_fixture(), "fixture_review_card": card_fixture()}
    ai_cases = [c for c in CASES if c != "real_photos_only"]
    reel = result["fixture_reel_short_documentary"]
    photos = result["fixture_photo_posts"]
    result["acceptance_met"] = (
        all(all(reel[c].values()) for c in ai_cases) and not any(reel["real_photos_only"].values())
        and photos["story_from_illustration"]["instagram_flagged"]
        and photos["carousel_from_illustration"]["instagram_flagged"]
        and not any(p["facebook_sent_unlabelled_with_ai"] for p in photos.values())
        and photos["carousel_from_real_photo"]["sent_to_facebook"]
        and not photos["carousel_from_real_photo"]["instagram_flagged"]
        and all(result["fixture_graph_publishers"].values())
        and result["fixture_review_card"]["card_says_label_on_with_reason"])
    result["runs"] = {f"folder_{i + 1}": runs(Path(f)) for i, f in enumerate(args.runs)}
    result["cards"] = {f"folder_{i + 1}": cards(Path(f)) for i, f in enumerate(args.cards)}
    if args.sweep:
        result["card_sweep"] = card_sweep()
    if args.parent_cards:
        parent = json.loads(Path(args.parent_cards).read_text(encoding="utf-8")).get("cards") or {}
        result["cards_against_parent"] = {k: compare_cards(v, parent.get(k) or {}) for k, v in result["cards"].items()}
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
