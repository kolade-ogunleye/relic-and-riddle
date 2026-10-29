"""
src/scene_planner.py
====================
Matches pictures to what is being said.

1. segment_beats(): splits the timed narration into beats of roughly 2.5-4.5 seconds,
   cutting at sentence or clause ends (so cuts land on natural pauses).
2. plan_scenes(): Claude looks at small versions of the candidate images and picks the
   best image for each beat. Without Claude, images keep their filename order.
The result is one (image, duration) per beat for the video composer.
"""

import json
import re
from collections import Counter
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import commons, config, free_llm, graphic_match
from .config import FOOTAGE_MAX_SHARE
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .models import ScenePlan
from .operator_jobs import JobCancelled
from .typography import is_video

log = logging.getLogger(__name__)

MIN_BEAT, TARGET_BEAT, MAX_BEAT = 2.5, 3.2, 4.5

SYSTEM = """You are a documentary film editor. Choose the best picture for each line of narration in a short vertical history video. Prefer images that literally show what the line describes; for abstract lines, pick an atmospheric image that fits. Use each image at most once where possible, start strong (the first beat is the hook: open on the widest shot that shows the whole subject's scale at once, an aerial, top-down, wide or establishing view, never a detail or a close-up), and avoid placing two near-identical images back to back. Never pick an image that contradicts the narration; real footage labelled GENERIC must never stand in for the named place. A photo's label carries its archive caption (title, description, date): use it to know what and where the photo shows, and never place a photo whose caption names a different site, object or era where the narration names this one.
Some candidates are the channel's own animated graphics (a locator map, a timeline, a scale comparison). Use each at most once, on the beat it fits: the map where the place is introduced, the timeline where the dates are told, the scale graphic where the size is stated. They add information the photos can't, so use the ones that fit; don't open the video with one unless no photo makes a stronger hook."""


def segment_beats(words: List[Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    """Groups timed words into beats; each beat runs until the next beat starts."""
    beats: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for i, w in enumerate(words):
        current.append(w)
        start = current[0]["start"]
        end = w["start"] + w["dur"]
        length = end - start
        pause = w["word"].rstrip()[-1:] in ".!?;:,—–"
        last = i == len(words) - 1
        if last or (length >= MIN_BEAT and pause and length >= TARGET_BEAT * 0.8) or length >= MAX_BEAT:
            beats.append({"start": start, "text": " ".join(x["word"] for x in current)})
            current = []
    if not beats:
        return [{"start": 0.0, "end": total, "text": ""}]
    beats[0]["start"] = 0.0
    for b, nxt in zip(beats, beats[1:] + [None]):
        b["end"] = nxt["start"] if nxt else total
    # Fold a very short final beat into the previous one.
    if len(beats) > 1 and beats[-1]["end"] - beats[-1]["start"] < 1.5:
        beats[-2]["end"], beats[-2]["text"] = beats[-1]["end"], beats[-2]["text"] + " " + beats[-1]["text"]
        beats.pop()
    return beats


MIN_CUT, MAX_CUT = 5.0, 12.0  # documentary cuts: long enough to read a picture, short enough to keep pace


def documentary_cuts(words: List[Dict[str, Any]], total: float) -> List[Dict[str, Any]]:
    """Cuts for the 16:9 documentary, one picture each: the narration's sentences, with short
    ones merged until a cut lasts MIN_CUT seconds (never past MAX_CUT). Cuts are contiguous and
    cover the whole narration, so their durations add up to it exactly."""
    sentences: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for w in words:
        current.append(w)
        if w["word"].rstrip()[-1:] in ".!?" and len(current) > 0:
            sentences.append({"start": current[0]["start"], "end": w["start"] + w.get("dur", 0.0),
                              "text": " ".join(x["word"] for x in current)})
            current = []
    if current:
        sentences.append({"start": current[0]["start"], "end": current[-1]["start"] + current[-1].get("dur", 0.0),
                          "text": " ".join(x["word"] for x in current)})
    cuts: List[Dict[str, Any]] = []
    for s in sentences:
        last = cuts[-1] if cuts else None
        if last and last["end"] - last["start"] < MIN_CUT and s["end"] - last["start"] <= MAX_CUT:
            last["end"], last["text"] = s["end"], last["text"] + " " + s["text"]
        else:
            cuts.append(dict(s))
    if not cuts:
        return [{"start": 0.0, "end": total, "text": ""}]
    if len(cuts) > 1 and cuts[-1]["end"] - cuts[-1]["start"] < MIN_CUT / 2:  # a stub at the end joins the cut before
        stub = cuts.pop()
        cuts[-1]["text"] += " " + stub["text"]
    cuts[0]["start"] = 0.0
    for a, b in zip(cuts, cuts[1:]):
        a["end"] = b["start"]
    cuts[-1]["end"] = total
    return cuts


def _is_graphic(p: Path) -> bool:
    """A clip the planner may place: an original graphic (src/graphics.py) or a real footage
    segment (src/footage.py). Both have a poster frame beside them."""
    return is_video(p) and Path(p).stem.startswith(("graphic_", "footage_")) and Path(p).with_suffix(".jpg").exists()


def _photo_record(p: Path) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(credits.json entry, whether any folder on the way had a credits.json) of a sourced photo. A derived copy
    (text-band crop, privacy blur, upscale) has no credits beside it: its lineage leads back to the original (the
    planner chose cropped photos blind, audit 2026-10-28)."""
    p = Path(p)
    entry, credited = None, False
    for _ in range(4):
        credited = credited or (p.parent / "credits.json").is_file()
        entry = next((e for e in _folder_credits(str(p.parent)) if e.get("file") == p.name), None)
        if entry:
            break
        try:
            from .lineage import sidecar
            sources = json.loads(sidecar(p).read_text(encoding="utf-8")).get("sources") or []
            p = Path(sources[0]["path"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            break
    return entry, credited


def _photo_entry(p: Path) -> Optional[Dict[str, Any]]:
    """The credits.json entry of a sourced photo, through its lineage (None if none)."""
    return _photo_record(p)[0]


def strong_shot(p: Path) -> bool:
    """A still that may open the video or be animated: a Topic or Kept photo by its record, or a photo you supplied
    (no credits.json beside it or its sources). Never a context shot, a photo found for one line, or one never looked
    at: Nazca on production (2026-10-28) opened on, and animated, a context photo of the highway through the desert."""
    if is_video(Path(p)):
        return False
    from .beat_images import intrusion_noted
    if intrusion_noted(p):  # a wing or frame the screen saw and could not crop away (dev run, 2026-10-28)
        return False
    from .image_review import tier
    entry, credited = _photo_record(p)
    if entry:
        return tier(entry) in ("topic", "kept")
    return not credited


def picture_id(p: Path) -> str:
    """What a picture is, whatever copy of it is placed: the file its lineage starts from (a text-band crop, an
    upscale or a privacy blur is the same picture as its original; coverage audit 2026-10-28). Clips are their own
    path."""
    p = Path(p)
    if not is_video(p):
        from .lineage import sidecar
        for _ in range(6):
            try:
                sources = json.loads(sidecar(p).read_text(encoding="utf-8")).get("sources") or []
                p = Path(sources[0]["path"])
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                break
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _photo_caption(p: Path) -> str:
    """The archive caption of a sourced photo, from its credits entry ("" if none)."""
    entry = _photo_entry(p)
    if not entry:
        return ""
    crowded = (entry.get("vision_review") or {}).get("people_prominent")
    return commons.caption(entry) + ("; prominent visitors, faces blurred: avoid for the hook" if crowded else "")


# The opening shot (Meta's Creator assistant on the operator's Reel, 2026-10-28: "move that top-down aerial shot
# to the 0:00 mark; show the scale immediately"; the Reel opened on a side shrine). The hook beat takes the
# topic's strongest scale shot, judged from what is already recorded about each photo: the vision verdict's
# reason (what the picture shows), weighted above the archive title and description; resolution breaks ties.
_SCALE_WORDS = re.compile(
    r"\b(?:aerial|drone|bird'?s[\s-]eye|top[\s-]down|overhead|from\s+above|from\s+the\s+air|satellite|panoram(?!io)\w*"
    r"|wide(?:[\s-]angle)?|broad\s+view|elevated|establishing|overview|general\s+view|full\s+view|whole|entire"
    r"|vast|skyline|landscape|scale)\b", re.I)
_DETAIL_WORDS = re.compile(
    r"\b(?:close[\s-]?up|details?|detailed|macro|reliefs?|frieze|inscriptions?|panel|carvings?|interior|inside"
    r"|niches?|fragments?|texture|small)\b", re.I)


def scale_score(entry: Optional[Dict[str, Any]]) -> Optional[float]:
    """How strongly a photo's record says it shows the subject's whole scale (higher is wider); None when it
    may not open the video (not a Topic or Kept photo by its verdict, or prominent visitors in it)."""
    from .image_review import tier
    if not entry or tier(entry) not in ("topic", "kept"):
        return None
    verdict = entry.get("vision_review") or {}
    if verdict.get("people_prominent"):
        return None
    seen = str(verdict.get("reason") or "")
    archive = " ".join(str(entry.get(k) or "") for k in ("title", "description"))
    score = 2 * (len(_SCALE_WORDS.findall(seen)) - len(_DETAIL_WORDS.findall(seen)))
    score += len(_SCALE_WORDS.findall(archive)) - len(_DETAIL_WORDS.findall(archive))
    try:
        pixels = float(entry.get("width") or 0) * float(entry.get("height") or 0)
    except (TypeError, ValueError):
        pixels = 0.0
    return score + min(pixels, 50e6) / 1e9   # at most +0.05: resolution only breaks ties


def hook_shot(stills: List[Path]) -> Optional[Path]:
    """The still with the strongest recorded scale evidence, or None when no photo's record says it shows
    the scale (the plan's own opening then stands). Ties keep pool order (best-first)."""
    best, best_score = None, 0.5   # at least one scale mention, net of detail ones
    for p in stills:
        score = scale_score(_photo_entry(p))
        if score is not None and score > best_score:
            best, best_score = p, score
    return best


def open_on_scale(chosen: List[Path], stills: List[Path]) -> List[Path]:
    """`chosen` with the hook beat on the topic's strongest scale shot (hook_shot), when the planned opening
    is a still whose record shows less scale. A shot already placed later trades places with the opening, so
    the set of pictures (and their credits) stays the same; footage and clips at the opening already move
    and are kept. Whatever the scale evidence, the opening is a strong shot (strong_shot): a context photo or one found for a line gives way to the first strong
    still in pool order (best-first), one already placed before one that is not; none at all leaves the plan
    alone."""
    if not chosen or is_video(Path(chosen[0])):
        return chosen
    best, why = hook_shot(stills), "the widest recorded view"
    if best is None:
        if strong_shot(Path(chosen[0])):
            return chosen
        strong = [s for s in stills if strong_shot(Path(s))]
        best = next((s for s in strong if s in chosen), strong[0] if strong else None)
        why = "a Topic or Kept photo; the planned one is not"
        if best is None:
            return chosen
    elif Path(best) == Path(chosen[0]):
        return chosen
    else:
        current = scale_score(_photo_entry(Path(chosen[0])))
        if current is not None and current >= scale_score(_photo_entry(best)):
            return chosen
    out = list(chosen)
    if best in out:
        k = out.index(best)
        out[0], out[k] = out[k], out[0]
    else:
        out[0] = best
    log.info("Opening shot: %s (%s) instead of %s.", Path(best).name, why, Path(chosen[0]).name)
    return out


@lru_cache(maxsize=64)
def _folder_credits(folder: str) -> tuple:
    try:
        return tuple(json.loads((Path(folder) / "credits.json").read_text(encoding="utf-8")).get("images", []))
    except (OSError, ValueError):
        return ()


def _clip_label(p: Path) -> str:
    if p.stem.startswith("graphic_"):
        return f"animated {p.stem.replace('graphic_', '')} graphic, use at most once"
    try:
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if meta.get("place_specific"):
        return "real video footage of the actual site, use at most once"
    return "real video footage, GENERIC (not the actual site): only for atmosphere, never where the narration names the place"


MAX_GRAPHICS, GRAPHICS_MAX_SHARE = 3, 0.35


def _graphics_count(chosen: List[Path]) -> int:
    return sum(1 for c in chosen if Path(c).stem.startswith("graphic_"))


def _graphics_share(chosen: List[Path], durations: List[float]) -> float:
    total = sum(durations) or 1.0
    return sum(d for c, d in zip(chosen, durations) if Path(c).stem.startswith("graphic_")) / total


def _footage_share(chosen: List[Path], durations: List[float]) -> float:
    total = sum(durations) or 1.0
    return sum(d for c, d in zip(chosen, durations) if Path(c).stem.startswith("footage_")) / total


def lock_narrative_graphics(beats: List[Dict[str, Any]], graphics: List[Path]) -> Dict[int, Path]:
    """Puts each original graphic on the beat that says what it shows (its declared figures, names
    and labels, as whole words; see src/graphic_match.py), best matches first, one graphic per beat.
    Beat 0 is the hook and keeps its photo or video."""
    pairs = sorted(((graphic_match.score(g, b.get("text", "")), idx, gi)
                    for gi, g in enumerate(graphics) if Path(g).stem.startswith("graphic_")
                    for idx, b in enumerate(beats) if idx > 0), reverse=True)
    locked: Dict[int, Path] = {}
    placed: set = set()
    for s, idx, gi in pairs:
        if s < graphic_match.THRESHOLD:
            break
        if idx not in locked and gi not in placed:
            locked[idx] = graphics[gi]
            placed.add(gi)
    # A graphic that only the hook names (Richat's script opened with its location, so the map had
    # no beat) follows the hook on the next beat, while the narration is still on that subject.
    if len(beats) > 1 and 1 not in locked:
        for gi, g in enumerate(graphics):
            if (gi not in placed and Path(g).stem.startswith("graphic_")
                    and graphic_match.score(g, beats[0].get("text", "")) >= graphic_match.THRESHOLD):
                locked[1] = g
                break
    return locked


def repair_placement(planned: List[Path], durations: List[float], issues: List[Dict[str, Any]]) -> Optional[List[Path]]:
    """A graphic the audit found on screen away from the words that name it moves to the beat where they
    are spoken, and that beat's picture takes its place: the correction the planner makes, without a second
    planning call (two 3-minute plans for one moved map; production 2026-10-24). None when the move is not
    clean (the words fall in the hook, which keeps its photo, so the next beat is the earliest; the target
    already holds a graphic; the finding names no time): the planner is asked instead."""
    scenes = list(planned)
    starts = [sum(durations[:i]) for i in range(len(durations))]
    moved = False
    for issue in issues:
        if issue.get("type") != "temporal_desync":
            continue
        src, at = int(issue.get("scene_index", -1)), issue.get("trigger_time")
        if not (0 <= src < len(scenes)) or at is None or not starts:
            return None
        target = max((i for i, s in enumerate(starts) if s <= float(at)), default=0)
        if target == 0:
            target = 1
        if target >= len(scenes) or target == src or Path(scenes[target]).stem.startswith("graphic_"):
            return None
        scenes[src], scenes[target] = scenes[target], scenes[src]
        moved = True
    return scenes if moved else None


def placement_only(issues: List[Dict[str, Any]], picture_issues: str) -> List[Dict[str, Any]]:
    """The audit's placement findings, when they are the only thing to repair (no picture mismatch from
    the video check, no other repairable defect); else an empty list."""
    repairable = [i for i in issues or [] if i.get("type") in SCENE_FIXES_TYPES and i.get("scene_index", -1) >= 0]
    late = [i for i in repairable if i.get("type") == "temporal_desync"]
    return late if late and len(late) == len(repairable) and not picture_issues else []


SCENE_FIXES_TYPES = ("semantic_desync", "motion_loop", "blank_frame", "caption_collision", "temporal_desync")


def keep_if_it_matches(pick: Path, text: str, fallback: Path) -> Path:
    """An original graphic stays only on a beat that says what it shows; one that declares nothing
    to judge it by stays as chosen."""
    if not Path(pick).stem.startswith("graphic_") or not graphic_match.judgeable(pick):
        return pick
    return pick if graphic_match.score(pick, text) >= graphic_match.THRESHOLD else fallback


STORYBOARD_NOTES = "approved storyboard (matched by narration)"
# Each beat takes the picture of the storyboard line it shares most words with (a beat that straddles
# two sentences takes the closer one). A storyboard whose lines most beats don't clearly say was
# approved for other narration and is not used.
STORYBOARD_MIN_OVERLAP, STORYBOARD_MIN_BEATS = 0.6, 0.6


def storyboard_order(storyboard: List[Dict[str, Any]], beats: List[Dict[str, Any]], options: List[Path]) -> Optional[List[int]]:
    """Option index per beat from the approved storyboard, matched by what each beat says (the
    storyboard's lines are sentences; the planner's beats are timed segments, and a revision can
    reorder them). Beats sharing no words get unused options in order. None if it doesn't fit."""
    by_name = {p.name: i for i, p in enumerate(options)}
    lines = [(graphic_match.tokens(item.get("text", "")), by_name[item["file"]])
             for item in storyboard if item.get("file") in by_name]
    picks: List[Optional[int]] = []
    strong = 0
    for b in beats:
        said = graphic_match.tokens(b.get("text", ""))
        overlap, pick = max(((len(said & words) / max(1, len(said)), i) for words, i in lines),
                            key=lambda c: c[0], default=(0.0, None))
        picks.append(pick if overlap > 0 else None)
        strong += overlap >= STORYBOARD_MIN_OVERLAP
    if strong < STORYBOARD_MIN_BEATS * len(beats):
        return None
    spare = [i for i in range(len(options)) if i not in picks] or list(range(len(options)))
    return [p if p is not None else spare[k % len(spare)] for k, p in enumerate(picks)]


CAPTION_NOTES = "caption match (no picture plan)"


def degraded_note(notes: str) -> str:
    """The review card's warning when pictures were not planned against the narration ("" when they were)."""
    if notes == CAPTION_NOTES:
        return ("⚠️ Pictures: matched by their archive captions, because the picture planner was unavailable "
                "after every retry. Check each picture fits its sentence, or Regenerate.")
    if notes == "filename order":
        return ("⚠️ Pictures: in filename order, not matched to the narration (no picture planner and no "
                "captions). Regenerate before approving.")
    return ""


def caption_order(beats: List[Dict[str, Any]], stills: List[Path]) -> Optional[List[int]]:
    """A photo per beat from archive captions alone: the unused photo sharing the most distinctive
    words with the beat (a word every caption has, like the subject's name, counts least), else the
    next unused photo in pool order. The hook keeps the pool's first photo. None if no caption exists."""
    caps = [graphic_match.tokens(_photo_caption(p)) for p in stills]
    if not any(caps):
        return None
    df = Counter(t for c in caps for t in c)
    used: Counter = Counter()
    order: List[int] = []
    for k, b in enumerate(beats):
        said = graphic_match.tokens(b.get("text", ""))
        score = [sum(1 / df[t] for t in caps[i] & said) for i in range(len(stills))]
        pick = 0 if k == 0 else max(range(len(stills)), key=lambda i: (-used[i], score[i], -i))
        used[pick] += 1
        order.append(pick)
    return order


@llm_task("scene_plan")
def plan_scenes(beats: List[Dict[str, Any]], media: List[Path], feedback: str = "",
                use_storyboard: bool = True, reserve: Optional[List[Path]] = None) -> Tuple[List[Path], List[float], str]:
    """Returns (media per beat, duration per beat, notes). A motion hook clip stays first.
    `feedback` lists problems a reviewer found with a previous plan, so they are avoided.
    `use_storyboard`: honour an approved Reel storyboard (its beats are numbered for a Reel; a
    documentary's many cuts are planned from the narration instead). `reserve`: stills offered only where a
    picture would otherwise be shown twice (no_repeats), never to the planner or the opening."""
    durations = [b["end"] - b["start"] for b in beats]
    hook = media[0] if media and is_video(media[0]) and not _is_graphic(media[0]) else None
    stills = [p for p in media if not is_video(p)]
    graphics = [p for p in media if _is_graphic(p)]
    options = stills + graphics  # what the planner chooses from (graphics shown by their poster frame)
    n = len(beats)

    order = list(range(n))  # default: filename order of the photos, cycling if there are more beats than photos
    notes = "filename order"
    failure = "no picture planner is available"

    # 1. Deterministic Storyboard Contract:
    # If the creator approved a storyboard in Telegram, lock those exact approved assets directly,
    # ensuring zero API cost, zero flakiness, and 100% fidelity to what was reviewed.
    storyboard = None
    if not feedback and use_storyboard:
        for p in stills:
            sb_candidate = p.parent / "storyboard.json"
            if sb_candidate.is_file():
                try:
                    storyboard = json.loads(sb_candidate.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

    if storyboard and not feedback:
        locked_order = storyboard_order(storyboard, beats, options)
        if locked_order is not None:
            order = locked_order
            notes = STORYBOARD_NOTES
        else:
            log.info("The approved storyboard was made for different narration; planning scenes afresh.")

    # 2. LLM Multimodal Scene Planning (used when no approved storyboard exists or reviewer requested feedback)
    if notes != STORYBOARD_NOTES and (free_llm.available() if free_llm.enabled() else claude_available()) and stills:
        try:
            content: List[dict] = []
            for i, p in enumerate(options, 1):
                if p in graphics:
                    content += [{"type": "text", "text": f"Image {i} ({_clip_label(p)}):"}, image_block(p.with_suffix(".jpg"))]
                else:
                    cap = _photo_caption(p)
                    content += [{"type": "text", "text": f"Image {i}" + (f" ({cap}):" if cap else ":")}, image_block(p)]
            lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
            fix = f"\n\nA reviewer found these problems with the previous choice; fix them:\n{feedback}" if feedback else ""
            content.append({"type": "text", "text": (
                f"Narration beats ({n}):\n{lines}{fix}\n\n"
                f"Return exactly {n} image numbers (1-{len(options)}), one per beat, in order.")})
            # Pictures that follow the narration are the point of the video, so a failed plan is tried
            # again within the run (the stages before it are kept), waiting longer each time, before the
            # caption fallback below is used.
            wait = config.SCENE_PLAN_RETRY_SECONDS
            for attempt in range(1, config.SCENE_PLAN_ATTEMPTS + 1):
                try:
                    plan = (free_llm.call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000)
                            if free_llm.enabled() else structured_call(SYSTEM, content, ScenePlan, effort="low", max_tokens=4000))
                except LLMError as e:
                    failure = f"Scene planning attempt {attempt}/{config.SCENE_PLAN_ATTEMPTS} failed ({e})"
                else:
                    picks = [i - 1 for i in plan.images]
                    if len(picks) == n and all(0 <= i < len(options) for i in picks):
                        order, notes = picks, plan.notes
                        break
                    failure = f"Scene plan attempt {attempt} had {len(picks)} picks for {n} beats or bad numbers"
                log.warning("%s.", failure)
                if attempt < config.SCENE_PLAN_ATTEMPTS:
                    log.info("Trying the scene plan again in %.0fs.", wait)
                    time.sleep(wait)
                    wait *= 2
        except LLMError as e:  # preparing the pictures failed
            failure = f"Scene planning failed ({e})"
            log.warning("%s.", failure)

    # 3. Without a picture plan (quota used up, a vision timeout: Richat's documentary went out in
    # filename order), each beat takes the unused photo whose archive caption shares its words.
    if notes == "filename order" and stills:
        matched = caption_order(beats, stills)
        if matched is not None:
            order, notes = matched, CAPTION_NOTES
        # Recorded for the review card and the audit ledger: a degraded plan never passes silently.
        from . import improve
        improve.note("pictures.plan_degraded", f"{notes}: {failure}", source="scene_plan")

    planned = notes != "filename order"  # without a plan, photos only, in filename order
    chosen: List[Path] = []
    used_graphics = set()
    for k, i in enumerate(order):
        pick = options[i] if planned else stills[i % len(stills)]
        if pick in graphics:
            if pick in used_graphics:  # a graphic shown twice reads as padding: use a photo instead
                pick = stills[k % len(stills)]
            used_graphics.add(pick)
        chosen.append(pick)

    # Hard-lock narrative-matching graphics to their corresponding spoken beats
    locked = lock_narrative_graphics(beats, graphics)  # never beat 0: the opening shot is a picture (below)
    for idx, g in locked.items():
        if idx < len(chosen):
            chosen[idx] = g
    # A locked graphic appears only on its beat: any other copy (the planner's own pick) becomes a photo.
    if stills:
        for k, p in enumerate(chosen):
            if p in locked.values() and locked.get(k) != p:
                chosen[k] = stills[k % len(stills)]

    # A graphic the planner placed where the narration doesn't say what it shows becomes a photo.
    if stills:
        for k in range(len(chosen)):
            if k not in locked and k < len(beats):
                chosen[k] = keep_if_it_matches(Path(chosen[k]), beats[k].get("text", ""), stills[k % len(stills)])

    # The opening shot is a picture, never a graphic: the planner opened the Nazca video on the map, since the
    # first line names the place (2026-10-25). The map's own line still accepts it one cut later.
    if chosen and stills and _is_graphic(Path(chosen[0])):
        chosen[0] = next((s for s in stills if s not in chosen), stills[0])
    # The opening shows the scale at once, on a Topic or Kept photo (open_on_scale); an approved storyboard's
    # opening is the operator's and stands (a context shot there is said on the card: storyboard_opening_note).
    if notes != STORYBOARD_NOTES and stills:
        chosen = open_on_scale(chosen, stills)

    # Footage supports the narration, never dominates it (Meta's originality rules): over the
    # cap, the latest footage picks become photos.
    k = len(chosen) - 1
    while _footage_share(chosen, durations) > FOOTAGE_MAX_SHARE and k >= 0:
        if Path(chosen[k]).stem.startswith("footage_"):
            chosen[k] = stills[k % len(stills)]
        k -= 1
    # Our own graphics carry information, but a Reel of diagrams loses the place: at most
    # MAX_GRAPHICS of them and GRAPHICS_MAX_SHARE of the runtime; extras become photos.
    # Unlocked graphics go first; a graphic locked to the beat that names it goes only if still over.
    over = lambda: _graphics_count(chosen) > MAX_GRAPHICS or _graphics_share(chosen, durations) > GRAPHICS_MAX_SHARE  # noqa: E731
    for keep_locked in (True, False):
        k = len(chosen) - 1
        while over() and k >= 0:
            if Path(chosen[k]).stem.startswith("graphic_") and not (keep_locked and k in locked):
                chosen[k] = stills[k % len(stills)]
            k -= 1
    chosen = no_repeats(chosen, stills, reserve)
    if hook is not None:
        chosen[0] = hook
    return chosen, durations, notes


FIT_NOTE_LABEL = "🖼 Pictures checked against their lines before the render"
FIT_NOTE_MAX_BEATS = 4   # beats named on the card; the rest are counted


def fit_to_lines(beats: List[Dict[str, Any]], scenes: List[Path], pool: List[Path], subject: str,
                 durations: Optional[List[float]] = None, line_beat: Optional[Dict[Path, int]] = None,
                 cancel=None, max_calls: Optional[int] = None, swaps: Optional[int] = None) -> Tuple[List[Path], Dict[str, Any]]:
    """Each photo beat's picture judged against its own line before the render (render audit 2026-10-28), with a
    bounded repair. Returns (scenes, report).

    Judged: every beat after the hook whose pick is a still. Not judged: the hook (the opening shot is the
    topic's strongest picture whatever the first line says), our own graphics and real footage (clips: a
    graphic is placed by the words it declares, src/graphic_match.py), and AI motion (made after this).
    A picture that does not show what its line says is repaired, in order:
      1. another unused still from `pool` (what the planner chose from) that does show it, the photos found
         for that line first (`line_beat`: still -> 1-based beat it was fetched for), at most `swaps` tried;
      2. one of our unplaced graphics whose declared words the line says, within the graphics caps;
      3. the previous beat's still, held (it was judged right for its own line, and is not itself held),
         so the shot carries on rather than a picture of something else being asserted under the line;
    otherwise the pick stays. Every beat repaired by 3 or not at all is listed in report["unshown"], for the
    review card and the findings. The judge after the render stays the final gate.

    Verdicts are cached by photo hash and line (src/beat_images.py): a resumed or re-rendered run makes no new
    call. New calls are serial (the free tier's rule) and stop at `max_calls`; a check that cannot run stops the
    rest (the light model is down: each further try would wait out its retry) and the plan is kept."""
    from . import beat_images
    out = [Path(p) for p in scenes]
    report: Dict[str, Any] = {"judged": 0, "cached": 0, "swapped": [], "graphic": [], "held": [], "unshown": [],
                              "stopped": ""}
    if not config.PICTURE_VISION_CHECK or not getattr(config, "PRE_RENDER_PICTURE_FIT", True):
        report["stopped"] = "off"
        return out, report
    max_calls = int(getattr(config, "PICTURE_FIT_MAX_CALLS", 30) if max_calls is None else max_calls)
    swaps = int(getattr(config, "PICTURE_FIT_SWAPS", 3) if swaps is None else swaps)
    line_beat = {Path(k): v for k, v in (line_beat or {}).items()}
    durations = list(durations or [b.get("end", 0) - b.get("start", 0) for b in beats])
    cache = beat_images.load_fit_cache()
    fits_at: Dict[int, bool] = {}   # beat index -> its final picture was judged right for its line
    held: set = set()

    def still(p: Path) -> bool:
        return not is_video(p) and Path(p).is_file()

    def verdict(p: Path, line: str) -> Optional[Dict[str, Any]]:
        if report["stopped"]:
            return None
        key_known = beat_images.fit_key(_digest(p), line) in cache
        if not key_known:
            if report["judged"] >= max_calls:
                report["stopped"] = f"call budget ({max_calls}) reached"
                log.info("Picture-line check: %s; the remaining beats keep the plan.", report["stopped"])
                return None
            if cancel is not None:
                cancel()
        v = beat_images.line_fit(p, line, subject, cache)
        if v is None:
            if not key_known:
                report["stopped"] = "the picture check could not run"
                log.warning("Picture-line check stopped: %s; the plan is kept (the video check still looks).",
                            report["stopped"])
            return None
        report["cached" if v["cached"] else "judged"] += 1
        return v

    try:
        for k in range(1, min(len(out), len(beats))):
            line = (beats[k].get("text") or "").strip()
            pick = out[k]
            if not line or not still(pick):
                continue
            first = verdict(pick, line)
            if first is None:
                if report["stopped"]:
                    break
                continue
            if first["fits"]:
                fits_at[k] = True
                continue
            log.info("Beat %d: %s does not show its line %r (%s).", k + 1, pick.name, clip(line), first["reason"])
            # 1. another unused still that shows the line: those fetched for this line first, then pool order
            in_use = {picture_id(p) for p in out}   # a cropped copy of a placed photo is that photo
            spare = [Path(p) for p in pool if still(Path(p)) and picture_id(Path(p)) not in in_use]
            spare.sort(key=lambda p: 0 if line_beat.get(p) == k + 1 else 1)  # stable: pool order otherwise
            swapped = None
            for cand in spare[:swaps]:
                v = verdict(cand, line)
                if v is None:
                    if report["stopped"]:
                        break
                    continue
                if v["fits"]:
                    swapped = cand
                    break
            if swapped is not None:
                out[k], fits_at[k] = swapped, True
                report["swapped"].append({"beat": k + 1, "from": pick.name, "to": swapped.name})
                log.info("Beat %d: %s swapped in, it shows the line.", k + 1, swapped.name)
                continue
            if report["stopped"]:
                break
            # 2. one of our own graphics that says what the line says, if the caps allow it
            graphic = next((g for g in pool if _is_graphic(Path(g)) and Path(g).stem.startswith("graphic_")
                            and picture_id(Path(g)) not in in_use
                            and graphic_match.judgeable(Path(g))  # one that declares nothing is never "what the line says"
                            and graphic_match.score(Path(g), line) >= graphic_match.THRESHOLD), None)
            if graphic is not None:
                trial = list(out)
                trial[k] = Path(graphic)
                if _graphics_count(trial) <= MAX_GRAPHICS and _graphics_share(trial, durations) <= GRAPHICS_MAX_SHARE:
                    out[k] = Path(graphic)
                    report["graphic"].append({"beat": k + 1, "from": pick.name, "to": Path(graphic).name})
                    log.info("Beat %d: our %s graphic placed, it says what the line says.", k + 1, Path(graphic).stem)
                    continue
            # 3. the previous beat's still held, when it was right for its own line and is not itself held
            entry = {"beat": k + 1, "line": line, "picture": pick.name, "reason": first["reason"], "fallback": "kept"}
            prev = out[k - 1]
            if fits_at.get(k - 1) and still(prev) and (k - 1) not in held:
                out[k], entry["fallback"] = prev, "held"
                held.add(k)
                report["held"].append({"beat": k + 1, "from": pick.name, "to": prev.name})
                log.info("Beat %d: no picture shows its line; the previous shot (%s) is held.", k + 1, prev.name)
            report["unshown"].append(entry)
    finally:
        report["fits"] = sorted(k + 1 for k, ok in fits_at.items() if ok)   # beats whose picture was judged right
        if report["judged"]:
            beat_images.save_fit_cache(cache)
    return out, report


def _digest(p: Path) -> str:
    from .credits import sha256_file
    return sha256_file(p)


def clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def fit_note(report: Dict[str, Any]) -> str:
    """The review card's line for the beats no picture shows ("" when every judged beat has one)."""
    unshown = (report or {}).get("unshown") or []
    if not unshown:
        return ""
    said = []
    for u in unshown[:FIT_NOTE_MAX_BEATS]:
        how = {"held": "the previous shot is repeated, reframed", "held_over": "the previous shot is held over it"}.get(
            u.get("fallback"), f"{u.get('picture', 'its photo')} kept")
        said.append(f"beat {u['beat']}: “{clip(u.get('line', ''), 70)}”: no licensed photo shows it ({how})")
    more = len(unshown) - FIT_NOTE_MAX_BEATS
    return (f"{FIT_NOTE_LABEL}: " + "; ".join(said) + (f"; and {more} more" if more > 0 else "")
            + ". Reword or drop the line(s) with Regenerate, or add a photo that shows it.")


def fit_findings(report: Dict[str, Any]) -> List[str]:
    """One finding text per beat no picture shows, for improve.note (class video.picture_relevance)."""
    return [f"beat {u['beat']}: '{clip(u.get('line', ''), 120)}': no licensed photo shows it "
            f"({'previous shot held' if u.get('fallback') in ('held', 'held_over') else u.get('picture', '') + ' kept'}; "
            f"{clip(u.get('reason', ''), 160)})" for u in (report or {}).get("unshown") or []]


def no_repeats(chosen: List[Path], stills: List[Path], reserve: Optional[List[Path]] = None) -> List[Path]:
    """No picture is shown twice while the pool still has unused photos: a repeat reads as padding.
    The swaps above take stills[k % n], which can be a photo already on another beat. Later copies
    get the pool's next unused photo (the pool is ordered best-first), then the `reserve`'s (context shots
    beyond the usual cap: Nazca showed four photos twice while its fifth context photo was never used,
    2026-10-28); with fewer photos than beats, repeats stay (hold_for_short_pool holds them instead). A picture
    is what its lineage starts from (picture_id): a cropped copy and its original are one picture."""
    pid = _id_memo()
    placed = {pid(p) for p in chosen}
    unused: List[Path] = []
    for s in list(stills) + list(reserve or []):
        if pid(s) not in placed:
            placed.add(pid(s))   # each picture once in the unused list too
            unused.append(s)
    seen = set()
    out = []
    for p in chosen:
        if pid(p) in seen and unused:
            p = unused.pop(0)
        seen.add(pid(p))
        out.append(p)
    return out


def _id_memo():
    """picture_id, looked up once per path within one pass (each lookup reads lineage sidecars)."""
    memo: Dict[str, str] = {}

    def pid(p: Path) -> str:
        key = str(p)
        if key not in memo:
            memo[key] = picture_id(Path(p))
        return memo[key]
    return pid


# ------------------------------------------------------------------ fewer pictures than beats
# Production Nazca, 2026-10-28: six stills for nine photo beats. The planner cycled photos (four shown twice) and
# the fit check's fallback held the previous shot as a second, reframed cut. A picture held over two lines reads
# as one shot; the same picture cut back in later reads as padding. So a repeated picture's beat may join a
# neighbouring photo beat, at the beat boundaries the narration already has (cuts stay on its pauses), never past
# HELD_SHOT_MAX and never into the hook (its motion clip lasts six seconds). A line keeps a picture that was
# judged right for it (fit_to_lines): only a later copy, or a copy whose line no picture was found to show, may be
# joined to a neighbour, and only to one showing the same picture or one judged to show that line too (`check`).
# Otherwise the repeat stays, and the review card says so.
HELD_SHOT_MAX = 2 * MAX_BEAT


def still_shortfall(n_beats: int, media: List[Path]) -> int:
    """How many beats the distinct stills in `media` cannot cover, as if every beat took a photo: the reserve made
    from it is used only where a picture would otherwise repeat, so an estimate on the high side costs nothing."""
    return max(0, n_beats - len({picture_id(p) for p in media if not is_video(Path(p))}))


def hold_for_short_pool(beats: List[Dict[str, Any]], scenes: List[Path], durations: List[float],
                        fits: Optional[Dict[int, bool]] = None, check=None, available: Optional[int] = None,
                        merge: bool = True
                        ) -> Tuple[List[Dict[str, Any]], List[Path], List[float], List[int], Dict[str, Any]]:
    """(beats, scenes, durations, where each old beat went (old index -> new index), report) with repeated stills'
    beats joined to neighbouring photo beats where the rules above allow (see above); nothing changes when no picture
    repeats. `fits`: 0-based beat -> whether fit_to_lines judged its picture right for its line (absent: not judged).
    `check(picture, line)`: True/False/None (could not look), asked before a line takes a neighbour's different
    picture; without it, only a neighbour showing the same picture is joined. `available`: the distinct stills the
    plan could choose from (with the reserve), which says whether the pool was short. report: "merged" (1-based new
    beats carrying two or more lines), "shown" (1-based old beats whose line now shows a picture judged right for
    it), "repeats" ({"beat", "first", "picture"}, new numbering), "distinct", "still_beats", "short". Nothing is
    joined when the pool is not short (the fit check's held shot stays a reframed cut, as before) or with
    merge=False (an approved storyboard: its beats are the operator's; the repeats are only reported)."""
    lines = [str(b.get("text") or "") for b in beats]
    beats, scenes, durations = [dict(b) for b in beats], [Path(p) for p in scenes], list(durations)
    n = min(len(beats), len(scenes), len(durations))
    beats, scenes, durations = beats[:n], scenes[:n], durations[:n]
    groups = [[i] for i in range(n)]
    status: Dict[int, Optional[bool]] = {i: (fits or {}).get(i) for i in range(n)}   # old beat -> its line is shown
    shown: List[int] = []
    asked: Dict[Tuple[str, str], Optional[bool]] = {}

    pid = _id_memo()

    def ids() -> List[Optional[str]]:
        return [None if is_video(p) else pid(p) for p in scenes]

    still_beats = sum(1 for i in ids() if i is not None)
    distinct = len({i for i in ids() if i is not None})
    short = (available if available is not None else distinct) < still_beats

    def shows(picture: Path, old: int) -> bool:
        if check is None or not lines[old].strip():
            return False
        key = (pid(picture), lines[old])
        if key not in asked:
            try:
                asked[key] = check(picture, lines[old])
            except JobCancelled:
                raise
            except Exception as e:  # a check that fails keeps the repeat
                log.info("Held-shot check for %s could not run (%s).", Path(picture).name, e)
                asked[key] = None
        return asked[key] is True

    while short and merge:
        now = ids()
        options = []
        for k in range(len(scenes) - 1, 0, -1):   # the hook (0) is never joined
            if now[k] is None or now.count(now[k]) < 2:
                continue
            first = now.index(now[k]) == k
            if first and not any(status[o] is False for o in groups[k]):
                continue   # the first copy keeps the line it was judged right for (or not judged against)
            for j in (k - 1, k + 1):
                if 1 <= j < len(scenes) and now[j] is not None and durations[j] + durations[k] <= HELD_SHOT_MAX:
                    options.append((0 if now[j] == now[k] else 1, durations[j] + durations[k], -k, j))
        step = None
        for same, _, negk, j in sorted(options):
            k = -negk
            if same == 0 or all(shows(scenes[j], o) for o in groups[k]):
                step = (k, j, same == 0)
                break
        if step is None:
            break
        k, j, same = step
        if not same:
            for o in groups[k]:
                status[o] = True
                if o + 1 not in shown:
                    shown.append(o + 1)
        lo, hi = sorted((k, j))
        beats[lo] = {**beats[lo], "end": beats[hi].get("end", beats[lo].get("end")),
                     "text": " ".join(t for t in (beats[lo].get("text", ""), beats[hi].get("text", "")) if t)}
        scenes[lo], durations[lo], groups[lo] = scenes[j], durations[lo] + durations[hi], groups[lo] + groups[hi]
        del beats[hi], scenes[hi], durations[hi], groups[hi]
    where = [0] * n
    for new, group in enumerate(groups):
        for old in group:
            where[old] = new
    first_at: Dict[str, int] = {}
    repeats = []
    for k, p in enumerate(scenes):
        if is_video(p):
            continue
        here = pid(p)
        if here in first_at:
            repeats.append({"beat": k + 1, "first": first_at[here] + 1, "picture": p.name})
        first_at.setdefault(here, k)
    report = {"merged": [k + 1 for k, g in enumerate(groups) if len(g) > 1], "shown": sorted(set(shown)),
              "repeats": repeats, "distinct": distinct, "still_beats": still_beats, "short": short,
              "storyboard": not merge}
    if report["merged"] or repeats:
        log.info("Pictures: %d distinct for %d photo beats; %d beat(s) now hold one picture over two or more lines, "
                 "%d repeat(s) left.", distinct, still_beats, len(report["merged"]), len(repeats))
    return beats, scenes, durations, where, report


def withdrawals(planned: List[Path], wrong_beats: List[int], candidates: List[Path], floor: int = 3) -> set:
    """The stills of `planned` to stop offering for a re-plan, so it cannot move them to another line (Nazca,
    2026-10-25): those on `wrong_beats` (1-based; the video check found they show the wrong thing,
    beat_images.wrong_subject_beats: never a finding that a picture repeats, which withdrew a right photo from a
    short pool on 2026-10-28), with every copy of the same picture, while at least `floor` distinct stills stay in
    `candidates` (counted after the gap search has added its photos). Empty when nothing may go."""
    flagged = {planned[b - 1] for b in wrong_beats if 0 < b <= len(planned)
               and not is_video(Path(planned[b - 1])) and not _is_graphic(Path(planned[b - 1]))}
    if not flagged:
        return set()
    gone = {picture_id(f) for f in flagged}
    left = {picture_id(c) for c in candidates if not is_video(Path(c)) and picture_id(c) not in gone}
    if len(left) < floor:
        log.info("Not withdrawing %s: only %d distinct still(s) would be left.",
                 ", ".join(sorted(Path(f).name for f in flagged)), len(left))
        return set()
    return {c for c in candidates if picture_id(c) in gone}


def original_beats(numbers: List[int], where: List[int]) -> Dict[int, List[int]]:
    """1-based beats after hold_for_short_pool -> the 1-based original beats each one carries."""
    out: Dict[int, List[int]] = {}
    for old, new in enumerate(where):
        if new + 1 in numbers:
            out.setdefault(new + 1, []).append(old + 1)
    return out


# A beat number, not a count after it ("beat 3 and 4 seconds later", "beat 4 - 5 people"): a number followed by a
# unit or a noun of quantity is left alone, and only an unspaced dash or "to" makes a range.
_NOT_A_COUNT = (r"(?!\d)(?!\s*(?:%|(?:seconds?|secs?|s|minutes?|mins?|hours?|days?|weeks?|months?|years?|decades?"
                r"|centuries|people|persons?|men|women|children|visitors|workers|times|metres?|meters?|m|km|cm|mm"
                r"|feet|foot|ft|miles?|percent|tons?|tonnes?|kg)\b))")
_BEAT_ITEM = rf"\d+{_NOT_A_COUNT}(?:(?:[-–]|\s+to\s+)\d+{_NOT_A_COUNT})?"
_BEAT_LIST = re.compile(rf"\b([Bb]eats?\s+)({_BEAT_ITEM}(?:(?:\s*[,&]\s*|\s+(?:and|or)\s+){_BEAT_ITEM})*)")


def renumber_feedback(text: str, where: List[int]) -> str:
    """Video-check feedback, numbered by the beats the video had, in the narration's own beats (the re-plan works
    on those): "Beat 4" of a video whose beat 4 carried lines 4 and 5 becomes "Beat 4+5"; every number of a list
    ("Beats 4 and 5", "beats 2, 3") is mapped, and a range ("beats 2-3") runs from its first beat's first line to
    its last beat's last line. A count after a beat number ("4 seconds", "5 people") is not a beat."""
    groups = original_beats(list(range(1, max(where, default=-1) + 2)), where)

    def item(m: "re.Match") -> str:
        lo = groups.get(int(m.group(1)))
        if m.group(3) is None:
            return "+".join(map(str, lo)) if lo else m.group(0)
        hi = groups.get(int(m.group(3)))
        return f"{lo[0] if lo else m.group(1)}{m.group(2)}{hi[-1] if hi else m.group(3)}"

    def numbers(m: "re.Match") -> str:
        return m.group(1) + re.sub(r"(\d+)(?:([-–]|\s+to\s+)(\d+))?", item, m.group(2))
    return _BEAT_LIST.sub(numbers, text or "")


def storyboard_opening_note(notes: str, scenes: List[Path]) -> str:
    """The card's warning when an approved storyboard opens on a still that is not a Topic or Kept photo (the
    operator's choice stands; it is not animated either), else ""."""
    if notes != STORYBOARD_NOTES or not scenes or is_video(Path(scenes[0])) or strong_shot(Path(scenes[0])):
        return ""
    return (f"⚠️ Opening: the approved storyboard opens on {Path(scenes[0]).name}, which is not one of the topic's "
            "own photos (a context shot): it stays as approved, without AI motion. Edit the storyboard to open on "
            "the subject itself.")


HOLD_NOTE_LABEL = "🔁 Pictures"


def hold_note(report: Dict[str, Any], reserve_used: Optional[List[str]] = None,
              fit_report: Optional[Dict[str, Any]] = None) -> str:
    """The review card's line when the pool had fewer pictures than photo beats ("" otherwise). A beat the fit
    check's line already names as held over (fit_note, `fit_report`) is not said again here."""
    report = report or {}
    said_there = {u.get("beat") for u in (fit_report or {}).get("unshown") or [] if u.get("fallback") == "held_over"}
    merged = [m for m in report.get("merged") or [] if m not in said_there]
    repeats = report.get("repeats") or []
    if not (report.get("short") or reserve_used) or not (merged or repeats or reserve_used):
        return ""
    said = []
    if reserve_used:
        said.append(f"{len(reserve_used)} context photo(s) beyond the usual cap used rather than repeat a picture "
                    f"({', '.join(reserve_used)})")
    if merged:
        said.append(("beats " if len(merged) > 1 else "beat ") + ", ".join(map(str, merged))
                    + (" each hold" if len(merged) > 1 else " holds") + " one picture over two or more lines")
    if repeats:
        said.append("; ".join(f"beat {r['beat']} repeats beat {r['first']}'s picture ({r['picture']})"
                              for r in repeats[:FIT_NOTE_MAX_BEATS])
                    + (f"; and {len(repeats) - FIT_NOTE_MAX_BEATS} more" if len(repeats) > FIT_NOTE_MAX_BEATS else ""))
    head = f"{HOLD_NOTE_LABEL}: {report.get('distinct', '?')} distinct for {report.get('still_beats', '?')} photo beats"
    tail = (" (the approved storyboard is kept as it is: edit it, or add photos)" if report.get("storyboard") and repeats
            else "") + ". Add photos of the topic for a picture per line."
    return head + ": " + "; ".join(said) + tail


def remap_beats(report: Dict[str, Any], where: List[int], scenes: Optional[List[Path]] = None,
                shown: Optional[List[int]] = None, keys=("swapped", "graphic", "held", "unshown")) -> Dict[str, Any]:
    """A fit_to_lines report in the beat numbering after hold_for_short_pool (1-based old -> new). A line that now
    shows a picture judged right for it (`shown`, old numbering) leaves "unshown"; a row names the picture its beat
    now has (`scenes`, after the hold); a held line joined to the shot it held is said as held over, not repeated."""
    out = dict(report or {})
    shown = set(shown or [])
    for key in keys:
        rows = []
        for row in out.get(key) or []:
            row = dict(row)
            old = int(row.get("beat", 0)) - 1
            if key == "unshown" and old + 1 in shown:
                continue
            if 0 <= old < len(where):
                if row.get("fallback") == "held" and old > 0 and where[old] == where[old - 1]:
                    row["fallback"] = "held_over"
                row["beat"] = where[old] + 1
                if key == "unshown" and scenes is not None and where[old] < len(scenes):
                    row["picture"] = Path(scenes[where[old]]).name
            rows.append(row)
        out[key] = rows
    if out.get("fits"):
        out["fits"] = sorted({where[b - 1] + 1 for b in out["fits"] if 0 < b <= len(where)})
    return out
