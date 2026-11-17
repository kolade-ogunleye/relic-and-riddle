"""QF-65b measurement: QF-65a's follow-ups.

    python docs/reviews/quality-fixes-2026-11/QF-65b/measure.py <repo root> [--runs DIR ...] [--cards DIR ...]

<repo root> is the code measured (the parent or this row). Prints JSON; writes nothing in either tree (fixtures go to a
temporary folder that is removed). No network: sockets are refused and every request is recorded locally. It reuses
QF-65a's fixture builders (docs/reviews/quality-fixes-2026-11/QF-65a/measure.py in the measured tree).

- "fixture_record_less_stills": a Story frame and a carousel slide with no record (a run made before still records),
  published to Instagram and Facebook through the measured code's MetaPublisher.publish_photos (live and dry run),
  for each kind of run video: an AI motion clip, an illustration scene, a hook clip with no record, a master with no
  media record, no video at all, and real media only. Per case: sent (or planned) to Facebook, Instagram flagged.
- "fixture_still_with_record": the guard: a slide whose record names a credited photo, in a run whose video shows
  an AI clip, goes to both platforms unflagged (judged by its own record).
- "fixture_decided_card": a fixture card whose decided form (rebuilt with its badge, as _render_decision does) cannot
  give the AI line's room from the fit but has room for the line once the narration's old AI line is dropped: does
  the decided card show the AI line, per badge.
- "runs" (--runs, output folders, read only): COUNTS only. Photo posts (Story frame sets, carousels, Facebook's
  carousel copy): checked, with a still without a record, sent to Facebook by the measured code.
- "cards" (--cards, output folders, read only): counts only, QF-65a's card measure (cards(); per-card rows left out):
  the waiting card and the decided card (140 renders on 35 dev cards: 2 badges x 2 names), no AI line among them.
"""

import argparse
import contextlib
import importlib.util
import json
import socket
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

VIDEOS = ["ai_motion_clip", "illustration_scene", "hook_clip_no_record", "no_media_record", "no_video",
          "real_photos_only"]


def _refuse(*_a, **_k):
    raise RuntimeError("measure.py makes no network call")


def _qf65a(root: Path):
    spec = importlib.util.spec_from_file_location(
        "qf65a_measure", root / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-65a" / "measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(work: Path):
    from src.db import PipelineDatabase
    bank = work / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "topic", "title": "T", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=work / "m.db", topic_bank_file=bank)
    db.create_run("run_x", "topic")
    return db


def _bare(run: Path, kind: str) -> list[str]:
    folder = run / ("stories" if kind == "story" else "carousel")
    folder.mkdir(parents=True, exist_ok=True)
    name = "story_01_hook.jpg" if kind == "story" else "carousel_slide_01.jpg"
    (folder / name).write_bytes(m65a.FAKE_JPEG + name.encode())
    return [str(folder / name)]


def _publish(work: Path, kind: str, stills: list[str], dry_run: bool = False) -> dict:
    import requests

    from src import publisher
    sent = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data))
        names = [v for k, v in data if k == "platform[]"]
        return m65a._Resp({"success": True, "results": {p: {"success": True} for p in names}})
    db = _db(work)
    try:
        with patch.object(requests, "post", fake_post):
            up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
            res = publisher.MetaPublisher(db=db, publishers=[up]).publish_photos(
                "run_x", "topic", kind, stills, caption="c", platforms=["instagram", "facebook"], dry_run=dry_run)
        if dry_run:
            rows = [json.loads(p["response_payload_json"]) for p in db.list_publications("run_x")]
            return {"facebook": any("facebook" in r.get("platforms", []) for r in rows),
                    "instagram_flagged": any("instagram" in r.get("platforms", []) and r.get("is_ai_generated")
                                             for r in rows), "status": res.get("status")}
        ig = [x for x in sent if ("platform[]", "instagram") in x]
        return {"facebook": any(("platform[]", "facebook") in x for x in sent),
                "instagram_flagged": bool(ig) and all(("is_ai_generated", "true") in x for x in ig),
                "status": res.get("status")}
    finally:
        with contextlib.suppress(Exception):
            db._conn.close()


def record_less_stills() -> dict:
    out = {}
    for video in VIDEOS:
        for kind in ("story", "carousel"):
            case = {}
            for dry in (False, True):
                with tempfile.TemporaryDirectory() as d:
                    work = Path(d)
                    if video == "no_video":
                        run = work / "output" / "run_x"
                        run.mkdir(parents=True)
                    else:
                        run = m65a._run(work, "" if video == "real_photos_only" else video)[0]
                    res = _publish(work, kind, _bare(run, kind), dry_run=dry)
                key = "dry_run" if dry else "live"
                case[f"{key}_facebook"] = res["facebook"]
                case[f"{key}_instagram_flagged"] = res["instagram_flagged"]
            out[f"{kind}_{video}"] = case
    return out


def still_with_record() -> dict:
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        run, _, _, photo = m65a._run(work, "ai_motion_clip")
        slide = m65a._still(run / "carousel" / "carousel_slide_01.jpg", {"photo": str(photo)})
        res = _publish(work, "carousel", [str(slide)])
    return {"facebook": res["facebook"], "instagram_flagged": res["instagram_flagged"]}


# Two fixture cards (QF-65a's sweep shapes, with the narration's old AI line, is_generated_by_ai, as every real card
# has it) whose decided form is QF-42's card without the AI line on the parent for 3 of the 4 badges each: findings,
# caption repeats, narration words. tests/test_qf65b_ai_label_follow_up.py has the same shapes.
DECIDED_SHAPES = [(0, 3, 603), (0, 18, 534)]


def decided_card() -> dict:
    from src import publishable
    from src.telegram_bot import CARD_PAGE_LIMIT, _tg_len, _without_ai_line
    db = types.SimpleNamespace(get_topic_by_id=lambda t: m65a.SWEEP_TOPIC if t == "stone_city" else None)
    out = {}
    for findings, times, words in DECIDED_SHAPES:
        payload = dict(m65a.SWEEP_PAYLOAD, is_generated_by_ai=True,
                       caption="A sentence of ordinary length for the caption. " * times + "\n#StoneCity",
                       carousel_caption="Swipe through the rooms. " * times, narration_script="Word " * words,
                       qa_summary="QA findings\n" + "\n".join(f"· finding {n} " + "detail " * 12
                                                              for n in range(findings)))
        record = publishable.card_record(dict(payload), db)
        for name, badge in m65a._badges()[1:]:
            card = m65a._decided(payload, record, "run_fixture", badge)
            base = m65a._decided(payload, m65a._bare(record), "run_fixture", badge)
            out[f"findings_{findings}_captions_x{times}_words_{words}_{name}"] = {
                "ai_line": "AI label on" in card, **m65a._loss(card, base),
                # the card without the record, but for its AI line (true where the line is put back; false where QF-65a's
                # own fit gives the line its room, as on the parent)
                "only_the_ai_line_differs": _without_ai_line(card) == _without_ai_line(base),
                "within_card_page_limit": _tg_len(card) <= CARD_PAGE_LIMIT}
    return out


def runs(folder: Path) -> dict:
    """Counts only: every photo post of every run in `folder`, as the measured code would send it to Facebook."""
    from src import ai_label
    from src.publishable import photo_posts
    c = {"photo_posts_checked": 0, "with_a_still_without_record": 0, "sent_to_facebook": 0,
         "kept_off_facebook": 0, "kept_off_facebook_with_a_still_without_record": 0}
    for run in sorted(p for p in Path(folder).glob("run_*") if p.is_dir()):
        stills = photo_posts(run)
        for group in ([p for p in stills if p.parent.name == f] for f in ("stories", "carousel", "carousel_fb")):
            if not group:
                continue
            label = ai_label.assess_photos(group)
            held = bool(label.get("facebook_hold", label["ai_media"]))   # the parent: AI media only
            bare = any(ai_label.still_sources(p) is None for p in group)
            c["photo_posts_checked"] += 1
            c["with_a_still_without_record"] += bare
            c["sent_to_facebook"] += not held
            c["kept_off_facebook"] += held
            c["kept_off_facebook_with_a_still_without_record"] += held and bare
    return c


def main() -> None:
    global m65a
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--runs", action="append", default=[])
    ap.add_argument("--cards", action="append", default=[])
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    socket.socket.connect = _refuse
    import logging
    logging.disable(logging.CRITICAL)
    m65a = _qf65a(root)
    result = {"fixture_record_less_stills": record_less_stills(), "fixture_still_with_record": still_with_record(),
              "fixture_decided_card": decided_card()}
    stills = result["fixture_record_less_stills"]
    result["acceptance_met"] = (
        all(not v["live_facebook"] and not v["dry_run_facebook"] and v["live_instagram_flagged"]
            for k, v in stills.items() if not k.endswith("real_photos_only"))
        and all(v["live_facebook"] and v["dry_run_facebook"] for k, v in stills.items() if k.endswith("real_photos_only"))
        and result["fixture_still_with_record"]["facebook"]
        and not result["fixture_still_with_record"]["instagram_flagged"]
        and all(v["ai_line"] and v["within_card_page_limit"] and not any(v[k] for k in (
            "captions_lost", "narration_newly_cut", "previews_lost", "publish_lines_moved"))
            for v in result["fixture_decided_card"].values()))
    result["runs"] = {f"folder_{i + 1}": runs(Path(f)) for i, f in enumerate(args.runs)}
    result["cards"] = {}
    for i, f in enumerate(args.cards):
        counts = m65a.cards(Path(f))
        counts.pop("per_card", None)
        result["cards"][f"folder_{i + 1}"] = counts
    print(json.dumps(result, indent=1))


m65a = None   # QF-65a's measure module of the measured tree (main)

if __name__ == "__main__":
    main()
