"""
src/prevoice_lines.py
=====================
Each narration sentence against the photos the video can show, before the paid voice (audit 2026-10-28).

Production's Roman concrete x3 (2026-10-26) narrated "modern concrete / infrastructure" when no licensed photo
showed it; the voice was already recorded, so the line could only be flagged after the render. Here one light,
text-only call reads the verified sentences beside the descriptions of the video's approved photos (their archive
captions and the photo review's reasons, from the topic's credits.json) and says for each sentence: "shown" (a
photo shows what it names), "abstract" (an idea, number or question our graphics or any topic photo carry) or
"unshown" (it names something to see that no photo shows). The caller sends the unshown sentences back to the
writer once, through the same revision path as any fact-check rewrite. Nothing here decides alone: no verdict
(the check is off, or the model could not run) means the script goes on as verified.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from . import commons, config
from .checkpoints import fingerprint

log = logging.getLogger(__name__)

VERSION = 2            # changes when the question does: an older verdict is asked again
STAGE = "picture_lines"
RECORD_FILE = "picture_lines.json"
NOTE_LABEL = "🖼 Lines no photo shows"
POOL_MAX = 24          # image_review.MAX_PHOTOS: the pool a video can draw on


class LineVerdict(BaseModel):
    index: int                                        # 1-based, as numbered in the request
    kind: Literal["shown", "abstract", "generable", "unshown"]
    photo: Optional[int] = None                       # the photo that shows it, for "shown"
    modality: Optional[Literal["historical_scene", "technical_schematic", "modern_scientific"]] = None
    reason: str = ""                                  # one short sentence


class LineReport(BaseModel):
    lines: List[LineVerdict]


SYSTEM = """You check the narration of a short, fact-checked history documentary against the licensed photos its video can show, before the narration is recorded. You get the numbered sentences and a description of every photo. For each sentence answer:
- "shown": it names something that can be seen (a place, building, object, material, person, event or process) and one of the photos shows that very thing; give the photo's number.
- "abstract": it names nothing in particular to see: an idea, a number or date, a comparison, a question, a feeling or a conclusion. Our own graphics or any photo of the documentary's subject can carry it.
- "generable": it names an unphotographed historical scene (a 70 BC shipwreck, ancient tomb sealing), artisan craft/metallurgy (Roman furnace melting, casting), or microscopic/forensic science (embedded nanoparticles, chemical bonds, modern laboratory replication). Give modality ("historical_scene", "technical_schematic", or "modern_scientific"). Our Era Illustration and Schematic Engine will illustrate it.
- "unshown": it names something that can be seen, no photo shows it, and it should NOT be illustrated: an irrelevant or out-of-context modern object or setting (modern highway/bridges when the video is about an ancient temple).
Judge from the descriptions only. A photo of the documentary's subject does not show a different thing the sentence names. reason: one short sentence."""


def sentences(narration: str) -> List[str]:
    """The narration's sentences, split as the blocked-sentence trim splits them (src/script_engine.py)."""
    import re
    return [x for x in re.split(r"(?<=[.!?])\s+", (narration or "").strip()) if x.strip()]


def pool_descriptions(topic_dir: Path, media: List[Path]) -> List[Dict[str, str]]:
    """[{"file", "text"}] for each photo the video can use that has a record in the topic's credits.json: its
    archive caption and the photo review's reason. Photos without a record (the shared suite, a clip) have
    nothing to say and are left out; a cropped copy (cache/text_crop) keeps its original's name."""
    from .credits import load_credits
    from .llm import verdict_reason
    try:
        records = {r.get("file"): r for r in load_credits(Path(topic_dir)).get("images", []) if isinstance(r, dict)}
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for p in media:
        rec = records.get(Path(str(p)).name)
        if not rec or rec["file"] in seen:
            continue
        seen.add(rec["file"])
        reason = verdict_reason((rec.get("vision_review") or {}).get("reason"))  # a stored stray note too
        text = commons.caption(rec, 200) + (f" (the photo review: {reason})" if reason else "")
        out.append({"file": rec["file"], "text": text})
    return out[:POOL_MAX]


def keys(narration: str, photos: List[Dict[str, str]]) -> Dict[str, Any]:
    """The cache key: the sentences' words and the photos' descriptions, so a resume, a re-render or a regeneration
    that keeps both pays nothing, and a new photo or a reworded line is asked about again."""
    return {"v": VERSION, "script": fingerprint([" ".join(s.lower().split()) for s in sentences(narration)]),
            "pool": fingerprint([[p["file"], p["text"]] for p in photos])}


def judge(narration: str, photos: List[Dict[str, str]], subject: str) -> Optional[List[Dict[str, Any]]]:
    """[{"sentence", "kind", "photo", "reason"}] per sentence (one light, text-only call), or None when the model
    could not run. A sentence the answer skips is "abstract": never sent back on no evidence."""
    from . import free_llm
    from .llm import LLMError, verdict_reason
    lines = sentences(narration)
    if not lines or not photos:
        return None
    prompt = (f"Documentary: {subject or 'history'}\n\nPhotos the video can show:\n"
              + "\n".join(f"Photo {i}: {p['text']}" for i, p in enumerate(photos, 1))
              + "\n\nNarration sentences:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(lines, 1))
              + f"\n\nReturn one verdict for each of the {len(lines)} sentences.")
    try:
        # One call on the pipeline's own thread, before any parallel stage starts: the free tier stays serial.
        report = free_llm.call(SYSTEM, prompt, LineReport, role="checker", task_class="light",
                               effort="low", max_tokens=3000)
    except (LLMError, OSError, ValueError) as e:
        log.warning("Line-picture check before the voice could not run (%s); the script goes on as verified.", e)
        return None
    by = {v.index: v for v in report.lines}
    out = []
    for i, s in enumerate(lines, 1):
        v = by.get(i)
        out.append({"sentence": s, "kind": v.kind if v else "abstract", "reason": verdict_reason(v.reason) if v else "",
                    "photo": v.photo if v and v.kind == "shown" else None,
                    "modality": getattr(v, "modality", None) if v else None})
    return out


def unshown(verdicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The concrete sentences no photo shows; abstract, shown and generable ones are never sent back."""
    return [v for v in verdicts or [] if v.get("kind") == "unshown"]


def generable(verdicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sentences approved for era-conditioned illustration or scientific schematics."""
    return [v for v in verdicts or [] if v.get("kind") == "generable"]


def revision_note(lines: List[Dict[str, Any]], photos: List[Dict[str, str]], minimal: bool = False) -> str:
    """The writer's brief: just these sentences, toward what the photos show; every other sentence word for word.
    `minimal`: a regeneration for the pictures, whose script was otherwise approved as it is: the fewest words
    changed, the same facts."""
    named = "; ".join(f"“{v['sentence']}”" + (f" ({v['reason']})" if v.get("reason") else "") for v in lines)
    return ("Before the voiceover: these narration sentences name something none of the video's licensed photos "
            f"shows, so the picture under them would show something else: {named}. Reword just these sentences "
            "toward what the photos do show, or say them as an idea, number or question with nothing in particular "
            "to see. Keep every other sentence word for word, keep the length, and use only the verified facts. "
            + ("The reviewer sent this version back for its pictures only: change as few words as you can in these "
               "sentences, keep the same facts in them, and change nothing else at all. " if minimal else "")
            + "The photos: " + "; ".join(f"{i}. {p['text']}" for i, p in enumerate(photos, 1)) + ".")


# A regeneration whose reason is the pictures (production Derinkuyu re-render, 2026-10-29): the script it kept word
# for word left three lines that no photo showed, and nothing could reword them. It may now reword just those lines.
PICTURES_REASON = "pictures"


def rewords_for(feedback: Dict[str, Any]) -> bool:
    """Whether a regeneration with this feedback may reword the lines no picture shows in the script it keeps: only
    one sent back for its pictures (a sound, voice or cover review keeps the words, and their voice, as before)."""
    return (feedback or {}).get("reason") == PICTURES_REASON


def other_lines_changed(before: str, after: str, allowed: List[str]) -> List[str]:
    """The sentences of `before` other than `allowed` that `after` no longer says word for word (spacing aside), in
    order: [] when the rewording changed only the allowed sentences."""
    def norm(x: str) -> str:
        return " ".join((x or "").split())
    now = {norm(x) for x in sentences(after)}
    free = {norm(x) for x in allowed}
    return [x for x in sentences(before) if norm(x) not in free and norm(x) not in now]


def note(rewritten: List[Dict[str, Any]], left: List[Dict[str, Any]], why_left: str = "",
         pictured: Optional[List[str]] = None, regeneration: str = "") -> str:
    """The review card's line ("" when every line has a picture). `pictured`: the lines a photo was found for
    before the voice (the picture side), said so the operator can look at those photos. `regeneration`: the reason
    of the regeneration that kept this script (PICTURES_REASON), said with a rewording so it is not a surprise."""
    def said(vs):
        return "; ".join(f"“{clip(v['sentence'])}”" for v in vs)
    parts = []
    if pictured:
        parts.append(f"a photo found before the voice for: {said([{'sentence': s} for s in pictured])}")
    if rewritten:
        parts.append(f"reworded before the voice{' (you sent it back for its pictures; every other line is kept word for word, and the voiceover is new)' if regeneration else ''}: "
                     f"{said(rewritten)}")
    if left:
        parts.append(f"left as written{f' ({why_left})' if why_left else ''}: {said(left)}. Reword or drop the "
                     "line(s) with Regenerate, or add a photo that shows it")
    return f"{NOTE_LABEL}: " + "; ".join(parts) + "." if parts else ""


def findings(rewritten: List[Dict[str, Any]], left: List[Dict[str, Any]], why_left: str = "") -> List[str]:
    """One finding text per line, for improve.note (class video.picture_relevance)."""
    return ([f"before the voice, reworded: '{clip(v['sentence'], 120)}': none of the topic's photos shows it "
             f"({clip(v.get('reason', ''), 160)})" for v in rewritten]
            + [f"before the voice, left as written{f' ({why_left})' if why_left else ''}: '{clip(v['sentence'], 120)}': "
               f"none of the topic's photos shows it ({clip(v.get('reason', ''), 160)})" for v in left])


# The picture side first (production Derinkuyu, 2026-10-29: four lines no photo showed cost a writer call, both fact
# checks, a length edit and a second fact-check round, 20 minutes, and the rewording was then refused). Before a line
# goes back to the writer, the pictures are looked for: the pool's reserve context shots (descriptions only, no call)
# and a Commons search for just those lines, bounded as the render's repair search is (GAP_LINES lines, PER_BEAT
# photos each, through the same licence, relevance, text and look screens). The lines are judged again against the
# larger pool; only what is still unshown goes to the writer. A photo that shows a line is recorded (SIDE_FILE) and
# offered to the render, where it is checked against its line again like any other.
SIDE_FILE = "picture_side.json"
GAP_LINES = 5          # beat_images.MAX_BEATS: lines a search is made for
SEARCH_SECONDS = 180   # the picture side's time budget a run (review rc72): the search stops there
GAP_PREFIX = "line"    # the found photos' names, beside the render's beat and gap photos (run_dir/beat_images)


def found_description(cred: Dict[str, Any]) -> Dict[str, str]:
    """A photo the line search found, described as pool_descriptions describes the pool's: its Commons caption and
    what the look saw."""
    from .llm import verdict_reason
    looked = verdict_reason(cred.get("looked"))
    return {"file": Path(str(cred.get("file") or "")).name,
            "text": commons.caption(cred, 200) + (f" (the photo review: {looked})" if looked else "")}


def load_side(run_dir: Path) -> Dict[str, Any]:
    """This run's picture-side record ({"photos", "reserve", "found", ...}), {} when none or unreadable."""
    import json
    try:
        data = json.loads((Path(run_dir) / SIDE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cited(verdicts: List[Dict[str, Any]], photos: List[Dict[str, str]], first: int) -> List[str]:
    """The files of photos numbered after `first` (the added ones) that a "shown" verdict names."""
    out = []
    for v in verdicts or []:
        n = v.get("photo")
        if v.get("kind") == "shown" and isinstance(n, int) and first < n <= len(photos):
            name = photos[n - 1]["file"]
            if name not in out:
                out.append(name)
    return out


def clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def enabled() -> bool:
    return bool(config.PICTURE_VISION_CHECK) and bool(getattr(config, "PRE_VOICE_LINE_CHECK", True))
