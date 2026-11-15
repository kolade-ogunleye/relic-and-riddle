"""QF-65c acceptance metric: no illustration in Stories or carousels; full illustration provenance.

Usage: python measure.py <repo root> [--runs <folder of run_* folders> --pool <assets folder>]...
Prints the metric as JSON. Runs the real code of the repo given (MultiFormatGenerator.generate_story_sequence and
generate_carousel_sequence with their own still_layout.json records, image_review.only_strong,
scene_planner.strong_shot, carousel_lazy.plan / plan_problem / draw, main.hold_layout_error and the review card,
image_gen.illustrate_beat with the real vision rubric), with inputs made in a temporary folder. No network: the photo
match (a model call) takes the stills in turn, the image generator returns a drawn test engraving and the vision judge
returns a fixed structured answer. Nothing is written in the repo.

`fixtures`:
1. `story`: a Story from [gap_gen_x.jpg] (the spec's acceptance), from a prepared copy of an illustration (its name
   no longer says gap_gen_, its lineage does) beside a real photo with the illustration as hero, and from
   illustrations with a real hero: frames drawn on an illustration, frames drawn, held with a reason naming it.
2. `carousel`: a carousel from [gap_gen_x.jpg], and from an illustration and two real photos with an earlier kept
   match (carousel_photos.json) that put the illustration on slide 1: slides on an illustration, slides drawn.
3. `fallbacks`: only_strong with no strong photo (its fallback), with nothing known, with the illustration's stem
   "strong"; strong_shot of an illustration and of a copy of one; QF-81's carousel plan from an illustration and a
   real photo, and from the illustration alone (what it records, whether plan_problem says why), a plan made before
   the filter (what draw uses).
4. `card`: a Story held for no real photo (main.hold_layout_error, then format_card_text): the hold and its line.
5. `round2` (review 1's blockers): the Story hold's AI-picture count for one illustration that is also the hero, and
   the review card of a run whose carousel plan has no real photo (card_record, its "Also posted" list): whether it
   promises slides, says the carousel is held, offers 🖼 Preview carousel.
6. `provenance`: illustrate_beat with four candidates (the third scores best): the record's prompt, raw_sha256 against
   sha256(<key>_best.png), candidate_index, the chosen candidate's descriptor verdict; reuse of a record missing a
   provenance field.

With --runs (read only; repeat it with a --pool per runs folder), `real` counts for every run_* folder:
- `recorded_*`, `*_showing_ai`: the photos its Story frames and carousel slides show (their still_layout.json
  records, paths moved onto this machine), and how many of those are AI-made (ai_label.trace "ai", the same reader
  in both trees), per deck;
- `selection_*`: the root's only_strong over each deck's candidates (those recorded photos and the topic's pool, with
  the topic's strong stems): stills kept, of those AI-made, decks left with none;
- `strong_*`: every recorded still and pool photo on disk: how many are strong shots, and how many are AI-made;
- `probe_*`: the same selection with one illustration (gap_gen_probe.png, the planner's name; never written) added
  to each deck's candidates (`pool`), and as its only candidate (`alone`): decks whose stills would take it, and
  decks that would not be made (no real photo left).
Counts only; no names, copy or ids from the runs are printed.
"""

import argparse
import concurrent.futures
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

STORY = {
    "title": "The Eye of the Sahara", "narration_script": "Thirty miles across, and nobody built it.",
    "first_comment": "?", "hashtags": ["#x"], "caption": "c",
    "story_slides": [
        {"kicker": "VISIBLE FROM SPACE", "line1": "A BULLSEYE", "line2": "IN THE SAND", "body": "Thirty miles across."},
        {"kicker": "THE REVEAL", "line1": "NOT A CRATER", "line2": "A DOME", "body": "Erosion carved the rings."},
    ],
}
CAROUSEL = {"title": "T", "poll": {}, "carousel_slides": [
    {"heading": "The dome", "body": "Built in concrete."}, {"heading": "The mix", "body": "Lime clasts."},
    {"heading": "Your view", "body": "Which mattered more?"}]}
GEARS = "The bronze gears of the mechanism turned inside a wooden case."
SHEET = {"facts": [
    {"id": "F1", "statement": ("The mechanism had bronze gears housed in a wooden case about 34 cm wide. "
                               "The mechanism held its bronze gears in a wooden case."),
     "kind": "other", "disputed": False, "evidence": [{"url": "https://www.nature.com/x", "quote": "bronze gears"}]}]}
IMAGES = (".jpg", ".jpeg", ".png", ".webp")


class Patch:
    """monkeypatch for a script: set attributes, undo them in reverse."""

    def __init__(self):
        self.undo = []

    def set(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def close(self):
        for obj, name, value in reversed(self.undo):
            setattr(obj, name, value)


def _photo(path, colour=(90, 80, 70), size=(1200, 1500)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _copy_of(art, name):
    from src import lineage
    out = _photo(art.parent / name, colour=(30, 30, 30))
    lineage.record_derived(out, [art], "upscale", {})
    return out


def _shown(folder):
    from src import still_layout
    out = []
    for still in sorted(Path(folder).glob("*.jpg")):
        recipe = (still_layout.load(still) or {}).get("recipe") or {}
        out += [Path(str(recipe[k])).name for k in ("photo", "photo_next") if recipe.get(k)]
    return out


def _try(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except Exception as e:  # noqa: BLE001 - a hold is an outcome here
        return None, e


def _rotate(patch):
    from src import multi_format as mf
    patch.set(mf.MultiFormatGenerator, "_match_photos",
              lambda self, cards, stills, strict=False: [stills[i % len(stills)] for i in range(len(cards))])


def _illustrated(names):
    return sum(1 for n in names if n.startswith(("gap_gen_", "prepared_beat")))


def _deck(kind, tmp, hero, stills):
    from src import multi_format as mf
    gen = mf.MultiFormatGenerator()
    if kind == "story":
        slides, err = _try(gen.generate_story_sequence, hero, STORY, tmp, scene_images=stills)
        folder = tmp / "stories"
    else:
        slides, err = _try(gen.generate_carousel_sequence, stills, CAROUSEL, tmp)
        folder = tmp / "carousel"
    shown = _shown(folder)
    return {"drawn": len(slides or []), "on_illustration": _illustrated(shown),
            "held_with_reason": bool(err is not None and "AI" in str(err)),
            "error": type(err).__name__ if err is not None else ""}


def fixtures(work):
    from src import (
        carousel_lazy,
        credits,
        image_gen,
        image_quarantine,
        image_review,
        lineage,
        scene_planner,
    )
    from src import multi_format as mf
    out = {}
    patch = Patch()
    _rotate(patch)
    try:
        # 1. Stories
        t = work / "s1"
        art = _photo(t / "gap_gen_x.jpg", colour=(20, 20, 20))
        story = {"from_gap_gen_x": _deck("story", t, art, [art])}
        t = work / "s2"
        art = _photo(t / "run" / "gap_gen_beat03.png", colour=(20, 20, 20))
        copy = _copy_of(art, "prepared_beat03.jpg")
        real = _photo(t / "pool" / "01_commons.jpg")
        story["copy_and_real_illustrated_hero"] = _deck("story", t, art, [copy, real, art])
        t = work / "s3"
        art = _photo(t / "gap_gen_x.jpg", colour=(20, 20, 20))
        story["illustrations_real_hero"] = _deck("story", t, _photo(t / "hero.jpg"), [art])
        out["story"] = story
        # 2. carousels
        t = work / "c1"
        art = _photo(t / "gap_gen_x.jpg", colour=(20, 20, 20))
        carousel = {"from_gap_gen_x": _deck("carousel", t, None, [art])}
        t = work / "c2"
        art = _photo(t / "gap_gen_x.jpg", colour=(20, 20, 20))
        real = [_photo(t / f"0{i}.jpg", colour=(40 * i, 60, 90)) for i in range(2)]
        key = {"cards": [f"{c['heading']}|{c['body']}" for c in CAROUSEL["carousel_slides"]],
               "stills": [str(p) for p in real]}
        (t / mf.CAROUSEL_PHOTOS).write_text(json.dumps({"key": key, "photos": [str(art), str(real[0]), str(real[1])]}),
                                            encoding="utf-8")
        carousel["illustration_two_real_stale_match"] = _deck("carousel", t, None, [art, *real])
        out["carousel"] = carousel
        # 3. fallbacks
        t = work / "f"
        art = _photo(t / "gap_gen_a.jpg")
        copy = _copy_of(art, "a_up.jpg")
        ctx = _photo(t / "context.jpg")
        fb = {
            "only_strong_fallback_illustrations": sum(1 for p in image_review.only_strong(
                [art, copy, ctx], {"nothing_strong"}) if p.name in ("gap_gen_a.jpg", "a_up.jpg")),
            "only_strong_unknown_illustrations": sum(1 for p in image_review.only_strong([art, copy, ctx], None)
                                                     if p.name in ("gap_gen_a.jpg", "a_up.jpg")),
            "only_strong_stem_strong_illustrations": len(image_review.only_strong([art, copy], {"gap_gen_a"})),
            "strong_shot_illustration": bool(scene_planner.strong_shot(art)),
            "strong_shot_copy_of_illustration": bool(scene_planner.strong_shot(copy)),
            "strong_shot_supplied_photo": bool(scene_planner.strong_shot(_photo(t / "mine.jpg"))),
        }
        run = t / "run"
        art2 = _photo(run / "gap_gen_x.jpg", colour=(20, 20, 20))
        real2 = _photo(t / "pool" / "01_commons.jpg")
        carousel_lazy.plan(run, [art2, real2], CAROUSEL)
        plan = json.loads((run / carousel_lazy.PLAN).read_text(encoding="utf-8"))
        fb["plan_records_illustrations"] = sum(1 for e in plan["stills"] if Path(e["path"]).name.startswith("gap_gen_"))
        carousel_lazy.plan(run, [art2], CAROUSEL)
        why = carousel_lazy.plan_problem(run) or ""
        fb["plan_alone_held_with_reason"] = bool("no real photo" in why and "AI" in why)
        carousel_lazy.plan(run, [real2], CAROUSEL)
        plan = json.loads((run / carousel_lazy.PLAN).read_text(encoding="utf-8"))
        old = dict(plan, stills=[{"path": str(art2), "sha256": credits.sha256_file(art2)}, *plan["stills"]])
        (run / carousel_lazy.PLAN).write_text(json.dumps(old), encoding="utf-8")
        fb["old_plan_draws_illustrations"] = sum(1 for p in carousel_lazy._checked_stills(run, CAROUSEL)
                                                 if p.name.startswith("gap_gen_"))
        out["fallbacks"] = fb
        # 4. the hold on the review card
        import main
        from src.telegram_bot import TelegramApprovalBot
        cls = getattr(mf, "NoRealPhotos", None)
        card = {"hold_kind_exists": cls is not None}
        if cls is not None:
            t = work / "h"
            (t / "stories").mkdir(parents=True)
            (t / "stories" / "story_01_hook.jpg").write_bytes(b"frame")
            fut = concurrent.futures.Future()
            fut.set_exception(cls("no real photo to draw the Story frames on: 1 AI-made picture(s) left out "
                                  "(gap_gen_x.jpg); an AI picture is never a Story frame or a carousel slide"))
            payload = {"title": "The Richat Structure", "narration_script": "Sediment.", "first_comment": "Why?"}
            main.hold_layout_error(fut, t, "stories", payload)
            text = TelegramApprovalBot.format_card_text(payload, "run_x")
            card.update({"frames_left_to_post": len(list((t / "stories").glob("story_*.jpg"))),
                         "card_says_held_no_real_photo": "Held, no real photo to draw on: 1" in text,
                         "card_says_text_would_not_fit": "would not fit whole" in text})
        out["card"] = card
        out["round2"] = round2(work / "r2", mf, carousel_lazy)
    finally:
        patch.close()
    out["provenance"] = provenance(work / "p", image_gen, image_quarantine, lineage, credits)
    return out


def round2(work, mf, carousel_lazy):
    """Review 1's blockers: the Story hold's count for one illustration that is also the hero (it is normally the
    first scene still), and the review card of a run whose carousel plan has no real photo (publishable.card_record,
    then the card's "Also posted" list)."""
    import re

    from src import publishable
    from src.db import PipelineDatabase
    from src.telegram_bot import _also_posted
    run = work / "run"
    art = _photo(run / "gap_gen_beat02.png", colour=(20, 20, 20))
    _, err = _try(mf.MultiFormatGenerator().generate_story_sequence, art, STORY, work, scene_images=[art])
    m = re.search(r"(\d+) AI-made picture", str(err or ""))
    out = {"story_ai_pictures_counted_for_one": int(m.group(1)) if m else None}
    carousel_lazy.plan(run, [art], CAROUSEL)
    bank = work / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=work / "test.db", topic_bank_file=bank)
    payload = {**STORY, **CAROUSEL, "title": "The Eye of the Sahara", "topic_id": "01_topic",
               "carousel_caption": "Three cards."}
    record = publishable.card_record(payload, db, run_dir=run, topic_id="01_topic")
    also = _also_posted(record, record["copy"])
    out.update({"card_also_posted_promises_carousel_slides": any("drawn from this run's photos" in i for i in also),
                "card_also_posted_says_carousel_held": any("not posted, held" in i for i in also),
                "card_offers_carousel_preview": bool(record.get("carousel_at_slot") and not record.get("carousel_held"))})
    return out


def _engraving(shade):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1080, 1920), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for x in range(0, 1080, 10):
        d.line([(x, 0), (x, 1920)], fill=(245, 245, shade), width=2)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def provenance(work, image_gen, image_quarantine, lineage, credits):
    from src import free_llm
    work.mkdir(parents=True)
    patch = Patch()
    try:
        patch.set(image_gen, "is_available", lambda: True)
        patch.set(image_gen, "checkers_ready", lambda: (True, ""))
        patch.set(image_quarantine, "_vision_ready", lambda: True)
        prompts, shades, asked = [], iter([200, 210, 220, 230]), []
        patch.set(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None:
                  prompts.append(p) or _engraving(next(shades)))
        patch.set(free_llm, "structured", lambda system, prompt, schema, **k:
                  asked.append(1) or schema(reason=f"judge call {len(asked)}"))
        scores = iter([(False, ["text in the image"], 0.0), (True, [], 5.0), (True, [], 9.0), (True, [], 1.0)])

        def score(raw, final, spec, size, others=(), references=()):
            image_quarantine.vision_rubric(Path(final), spec)
            return next(scores)
        patch.set(image_quarantine, "score_candidate", score)
        spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
        art, info = image_gen.illustrate_beat(GEARS, spec, work)
        rec = json.loads(lineage.sidecar(art).read_text(encoding="utf-8"))
        inputs = rec["settings"]["inputs"]
        best = work / image_gen.CANDIDATE_DIR / f"{info['key']}_best.png"
        verdicts = inputs.get("judge_verdicts") or []
        res = {
            "fields_present": sum(1 for f in ("prompt", "raw_sha256", "candidate_index", "judge_verdicts")
                                  if f in inputs),
            "prompt_is_the_one_sent": inputs.get("prompt") == prompts[2],
            "raw_sha256_matches_best_png": bool(inputs.get("raw_sha256"))
            and inputs.get("raw_sha256") == credits.sha256_file(best),
            "candidate_index_is_the_chosen": inputs.get("candidate_index") == 2,
            "chosen_descriptor_verdict_kept": [v.get("verdict", {}).get("reason") for v in verdicts
                                               if v.get("judge") == "descriptor"] == ["judge call 3"],
            "candidates_recorded": len(inputs.get("candidates") or []),
        }
        # reuse of a record missing one provenance field: never reused
        patch.set(image_quarantine, "score_candidate", lambda *a, **k: (True, [], 1.0))
        patch.set(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None: _engraving(240))
        reused_without = 0
        for field in ("prompt", "raw_sha256", "candidate_index", "judge_verdicts"):
            stripped = dict(rec, settings=dict(rec["settings"], inputs={k: v for k, v in inputs.items()
                                                                       if k != field}))
            lineage.sidecar(art).write_text(json.dumps(stripped), encoding="utf-8")
            _, again = image_gen.illustrate_beat(GEARS, spec, work, candidates=1)
            reused_without += bool(again.get("reused"))
            lineage.sidecar(art).write_text(json.dumps(rec), encoding="utf-8")
        res["reused_without_a_provenance_field"] = reused_without
        return res
    finally:
        patch.close()


# ---------------------------------------------------------------- real runs (read only, counts)
def _recorded(folder):
    """The photos a deck folder's stills show, from their still_layout.json records."""
    try:
        data = json.loads((Path(folder) / "still_layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for entry in data.values() if isinstance(data, dict) else []:
        recipe = (entry or {}).get("recipe") if isinstance(entry, dict) else None
        for k in ("photo", "photo_next", "hero"):
            if isinstance(recipe, dict) and recipe.get(k):
                out.append(str(recipe[k]))
    return list(dict.fromkeys(out))


def _relocate(path, runs, pool):
    """A recorded path (the machine that made it) on this machine: run_*/... under `runs`, assets/... under `pool`."""
    parts = Path(str(path).replace("\\", "/")).parts
    for i, part in enumerate(parts):
        if part.startswith("run_") and i and parts[i - 1] == "output":
            return Path(runs, *parts[i:])
        if part == "assets" and i + 1 < len(parts):
            return Path(pool, *parts[i + 1:])
    return Path(path)


def real(pairs):
    from src import ai_label, image_review, scene_planner
    from src.typography import is_video
    tot = {"runs": 0, "story_sets": 0, "carousels": 0, "recorded_photos": 0, "recorded_ai_made": 0,
           "story_sets_showing_ai": 0, "carousels_showing_ai": 0,
           "selection_kept": 0, "selection_ai_made_kept": 0, "selection_empty": 0,
           "strong_checked": 0, "strong_checked_ai_made": 0, "strong_true": 0,
           "probe_pool_decks_taking_it": 0, "probe_pool_decks_not_made": 0, "probe_alone_decks_taking_it": 0,
           "probe_alone_decks_not_made": 0}
    for runs, pool in pairs:
        for run in sorted(Path(runs).glob("run_*")):
            if not run.is_dir():
                continue
            tot["runs"] += 1
            try:
                topic = json.loads((run / "script_metadata.json").read_text(encoding="utf-8")).get("topic_id")
            except (OSError, ValueError, AttributeError):
                topic = None
            topic = topic or run.name[4:].rsplit("_", 1)[0]
            topic_dir = Path(pool) / topic
            stems = image_review.strong_stems(topic_dir) if topic_dir.is_dir() else None
            pool_photos = sorted(p for p in topic_dir.glob("*") if p.suffix.lower() in IMAGES) if topic_dir.is_dir() else []
            for deck in ("stories", "carousel"):
                if not (run / deck).is_dir():
                    continue
                tot["story_sets" if deck == "stories" else "carousels"] += 1
                shown = [_relocate(p, runs, pool) for p in _recorded(run / deck)]
                shown = [p for p in shown if not is_video(p)]
                tot["recorded_photos"] += len(shown)
                ai = [p for p in shown if ai_label.trace(p, credits_needed=False)[0] == "ai"]
                tot["recorded_ai_made"] += len(ai)
                if ai:
                    tot["story_sets_showing_ai" if deck == "stories" else "carousels_showing_ai"] += 1
                cands = list(dict.fromkeys([*shown, *pool_photos]))
                picked = image_review.only_strong(cands, stems)
                tot["selection_kept"] += len(picked)
                tot["selection_ai_made_kept"] += sum(1 for p in picked if ai_label.trace(p, credits_needed=False)[0] == "ai")
                tot["selection_empty"] += not picked and bool(cands)
                probe = Path(run) / "gap_gen_probe.png"   # never written: the name alone marks it (ai_label.trace)
                with_probe = image_review.only_strong([probe, *cands], stems)
                with_probe_none = image_review.only_strong([probe], stems)
                tot["probe_pool_decks_taking_it"] += probe in with_probe
                tot["probe_pool_decks_not_made"] += not with_probe
                tot["probe_alone_decks_taking_it"] += probe in with_probe_none
                tot["probe_alone_decks_not_made"] += not with_probe_none
            for p in {*(_relocate(x, runs, pool) for d in ("stories", "carousel") for x in _recorded(run / d)),
                      *pool_photos}:
                if is_video(p) or not p.is_file():
                    continue
                tot["strong_checked"] += 1
                tot["strong_checked_ai_made"] += ai_label.trace(p, credits_needed=False)[0] == "ai"
                try:
                    tot["strong_true"] += bool(scene_planner.strong_shot(p))
                except (OSError, ValueError):   # a stale record: counted apart, the same in both trees
                    tot["strong_unreadable"] = tot.get("strong_unreadable", 0) + 1
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--runs", action="append", default=[])
    ap.add_argument("--pool", action="append", default=[])
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    import os
    os.chdir(root)
    import logging
    logging.disable(logging.WARNING)
    work = Path(tempfile.mkdtemp(prefix="qf65c-"))
    try:
        if args.runs:
            print(json.dumps({"real": real(list(zip(args.runs, args.pool)))}, indent=1, sort_keys=True))
            return
        fx = fixtures(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    s, c, f, card, p = fx["story"], fx["carousel"], fx["fallbacks"], fx["card"], fx["provenance"]
    fx["acceptance_met"] = bool(
        all(v["on_illustration"] == 0 for v in s.values()) and all(v["on_illustration"] == 0 for v in c.values())
        and s["from_gap_gen_x"]["held_with_reason"] and c["from_gap_gen_x"]["held_with_reason"]
        and s["copy_and_real_illustrated_hero"]["drawn"] == 2 and s["illustrations_real_hero"]["drawn"] == 2
        and c["illustration_two_real_stale_match"]["drawn"] == 3
        and f["only_strong_fallback_illustrations"] == 0 and f["only_strong_unknown_illustrations"] == 0
        and f["only_strong_stem_strong_illustrations"] == 0 and not f["strong_shot_illustration"]
        and not f["strong_shot_copy_of_illustration"] and f["strong_shot_supplied_photo"]
        and f["plan_records_illustrations"] == 0 and f["plan_alone_held_with_reason"]
        and f["old_plan_draws_illustrations"] == 0
        and card.get("card_says_held_no_real_photo") and card.get("frames_left_to_post") == 0
        and not card.get("card_says_text_would_not_fit")
        and p["fields_present"] == 4 and p["prompt_is_the_one_sent"] and p["raw_sha256_matches_best_png"]
        and p["candidate_index_is_the_chosen"] and p["chosen_descriptor_verdict_kept"]
        and p["reused_without_a_provenance_field"] == 0
        and fx["round2"]["story_ai_pictures_counted_for_one"] == 1
        and not fx["round2"]["card_also_posted_promises_carousel_slides"]
        and fx["round2"]["card_also_posted_says_carousel_held"]
        and not fx["round2"]["card_offers_carousel_preview"])
    print(json.dumps(fx, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
