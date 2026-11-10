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

Per-line visual plan (2026-11-02, src/picture_coverage.py): each sentence also gets its planned visual class,
"named_subject" (it needs a real photo of that very thing), "kind_of_thing" (a photo of the same kind and period is
honest) or "abstract" (a number, time, comparison or question: a graphic or any topic photo). Only an unshown
named-subject line goes back to the writer (needs_rewrite): a kind-of-thing line is left to the render's fallback
cascade. It may go back up to rounds() times (config.PREVOICE_REWRITE_ROUNDS, default 2), a further round only while
the previous one left fewer such lines than it was given; still nothing blocks alone when the check cannot run.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from . import commons, config
from .checkpoints import fingerprint

log = logging.getLogger(__name__)

VERSION = 3            # changes when the question does: an older verdict is asked again (3: the visual plan)
STAGE = "picture_lines"
RECORD_FILE = "picture_lines.json"
NOTE_LABEL = "🖼 Lines no photo shows"
POOL_MAX = 24          # image_review.MAX_PHOTOS: the pool a video can draw on


class LineVerdict(BaseModel):
    index: int                                        # 1-based, as numbered in the request
    kind: Literal["shown", "abstract", "generable", "unshown"]
    photo: Optional[int] = None                       # the photo that shows it, for "shown"
    modality: Optional[Literal["historical_scene", "technical_schematic", "modern_scientific"]] = None
    # The planned visual class (src/picture_coverage.VISUAL_CLASSES); None when the answer leaves it out.
    visual: Literal["named_subject", "kind_of_thing", "abstract"] | None = None
    reason: str = ""                                  # one short sentence


class LineReport(BaseModel):
    lines: List[LineVerdict]


SYSTEM = """You check the narration of a short, fact-checked history documentary against the licensed photos its video can show, before the narration is recorded. You get the numbered sentences and a description of every photo. For each sentence answer:
- "shown": it names something that can be seen (a place, building, object, material, person, event or process) and one of the photos shows that very thing; give the photo's number.
- "abstract": it names nothing in particular to see: an idea, a number or date, a comparison, a question, a feeling or a conclusion. Our own graphics or any photo of the documentary's subject can carry it.
- "generable": it names an unphotographed historical scene (a 70 BC shipwreck, ancient tomb sealing), artisan craft/metallurgy (Roman furnace melting, casting), or microscopic/forensic science (embedded nanoparticles, chemical bonds, modern laboratory replication). Give modality ("historical_scene", "technical_schematic", or "modern_scientific"). Our Era Illustration and Schematic Engine will illustrate it.
- "unshown": it names something that can be seen, no photo shows it, and it should NOT be illustrated: an irrelevant or out-of-context modern object or setting (modern highway/bridges when the video is about an ancient temple).
Also give each sentence its planned visual, "visual":
- "named_subject": it names one particular thing to see (a named place, building, object, person or document); only a real photo of that very thing is honest.
- "kind_of_thing": it names a kind of thing to see (Roman harbour piers, lime lumps, a drill core, oil lamps) rather than one particular example; a real photo of the same kind and period is honest.
- "abstract": a number, date or duration, a comparison, a question, an idea or a conclusion; a graphic or any photo of the documentary's subject carries it.
Judge from the descriptions only. A photo of the documentary's subject does not show a different thing the sentence names. reason: one short sentence."""


def sentences(narration: str) -> List[str]:
    """The narration's sentences, split as the blocked-sentence trim splits them (src/script_engine.py)."""
    import re
    return [x for x in re.split(r"(?<=[.!?])\s+", (narration or "").strip()) if x.strip()]


def pool_descriptions(topic_dir: Path, media: List[Path]) -> List[Dict[str, str]]:
    """[{"file", "text"}] for each photo the video can use that has a record in the topic's credits.json: its
    archive caption and the photo review's reason. Photos without a record (the shared suite, a clip) have
    nothing to say and are left out; a prepared copy is resolved through checked source lineage."""
    from .credits import load_credits
    from .llm import verdict_reason
    try:
        records = {r.get("file"): r for r in load_credits(Path(topic_dir)).get("images", []) if isinstance(r, dict)}
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for p in media:
        rec = records.get(Path(str(p)).name)
        if rec is None:
            from .lineage import source_chain
            try:
                originals = {Path(row["path"]).resolve() for row in source_chain(p)
                             if row.get("kind") != "lineage"}
                matches = [row for name, row in records.items()
                           if name and (Path(topic_dir) / name).resolve() in originals]
                rec = matches[0] if len(matches) == 1 else None
            except (OSError, ValueError, KeyError):
                rec = None
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
                    "modality": getattr(v, "modality", None) if v else None,
                    "visual": plan_visual(v.kind if v else "abstract", getattr(v, "visual", None) if v else None)})
    return out


def plan_visual(kind: str, visual: str | None) -> str:
    """The sentence's planned visual class: the model's when it gave one, else from its kind (an abstract sentence
    is abstract, a generable one a kind of thing, a shown or unshown one a named subject, as before the plan)."""
    from .picture_coverage import ABSTRACT, KIND_OF_THING, NAMED_SUBJECT, VISUAL_CLASSES
    if visual in VISUAL_CLASSES:
        return str(visual)
    return {"abstract": ABSTRACT, "generable": KIND_OF_THING}.get(kind, NAMED_SUBJECT)


def needs_rewrite(verdicts: list[dict[str, Any]], payload: dict[str, Any] | None = None,
                  fact_sheet: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The sentences that go back to the writer: the unshown ones naming a particular thing (named_subject, or no
    class recorded); an unshown kind-of-thing line is left to the render's cascade (a same-kind photo, a detail,
    film, a map, a graphic, an illustration), and an abstract one is never unshown.

    With the script's payload (2026-11-02, illustration panel), also a "generable" named-subject line the fact
    sheet gives no physical descriptor for (no honest illustration can be drawn of it); and protected "marvel"
    lines (protect) are kept from the writer when the fact check supports them with a source: they are routed to
    the render's cascade, marked "route": "cascade", and get the illustration slots first. A protected line with
    no sourced support still goes to the writer, as before. Without a payload, a verdict protect() already marked
    protected and sourced is likewise kept from the writer."""
    from .picture_coverage import ABSTRACT, KIND_OF_THING, NAMED_SUBJECT
    if payload is not None:
        protect(verdicts, payload, fact_sheet)
    out = []
    for v in verdicts or []:
        named_unshown = v.get("kind") == "unshown" and v.get("visual") not in (KIND_OF_THING, ABSTRACT)
        undrawable = (payload is not None and v.get("kind") == "generable" and v.get("visual") == NAMED_SUBJECT
                      and not has_descriptors(v.get("sentence", ""), payload, fact_sheet))
        if not (named_unshown or undrawable):
            continue
        if v.get("protected") and v.get("sourced"):
            v["route"] = "cascade"
            continue
        out.append(v)
    return out


# ------------------------------------------------------------------ protected "marvel" lines (2026-11-02)
# The operator's intent: a striking fact is not rewritten away for lack of a photo. Protected: the hook and the closing
# sentence, and any sentence carrying a figure the packaging shows (a stat card, the title, the thumbnail's two lines,
# the second story slide).
_ENTITY_STOP = {"The", "A", "An", "This", "That", "These", "Those", "It", "Its", "In", "On", "At", "By", "For", "And",
                "But", "Or", "Yet", "So", "When", "Where", "What", "Why", "How", "Who", "Today", "Then", "There", "Here",
                "One", "Some", "Many", "Most", "Each", "Every", "No", "Not", "As", "If", "Of", "To", "From", "With"}
_NUMBER_WORDS = re.compile(r"\b(hundreds?|thousands?|millions?|billions?|dozens?|half|twice|thrice|one|two|three|four|"
                           r"five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
                           r"ninety)\b", re.IGNORECASE)


def _norm_sentence(x: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(x or "").lower()).split())


def _figures(text: str) -> set:
    from .fact_sheet import numbers_in
    return {round(n, 3) for n in numbers_in(text or "")}


def packaging_figures(payload: dict[str, Any]) -> set:
    """The figures the packaging shows: stat cards, the title, the thumbnail's two lines and story slide 2."""
    parts = [str(payload.get("title") or "")]
    for card in payload.get("stat_cards") or []:
        if isinstance(card, dict):
            parts += [str(card.get("value") or ""), str(card.get("label") or "")]
    thumb = payload.get("thumbnail") or {}
    if isinstance(thumb, dict):
        parts += [str(thumb.get("line1") or ""), str(thumb.get("line2") or "")]
    slides = payload.get("story_slides") or []
    if len(slides) > 1 and isinstance(slides[1], dict):
        parts += [str(slides[1].get(f) or "") for f in ("kicker", "line1", "line2", "body")]
    return set().union(*(_figures(x) for x in parts)) if parts else set()


def protected_sentences(payload: dict[str, Any]) -> list[str]:
    """The narration's protected sentences: the hook, the payoff (QF-15: reworded, it could lose the noun it shares
    with the hook), the closing sentence, and any sentence carrying a figure the packaging shows
    (packaging_figures)."""
    lines = sentences(payload.get("narration_script") or "")
    ends = [_norm_sentence(payload.get(k)) for k in ("hook_sentence", "payoff_sentence", "closing_sentence")
            if payload.get(k)]
    figures = packaging_figures(payload)
    out = []
    for s in lines:
        n = _norm_sentence(s)
        if (n and any(e and (e in n or n in e) for e in ends)) or (figures and _figures(s) & figures):
            out.append(s)
    return out


def _sheet_for(payload: dict[str, Any], fact_sheet: dict[str, Any] | None) -> dict[str, Any] | None:
    if fact_sheet is not None:
        return fact_sheet
    import json
    path = ((payload.get("fact_review") or {}).get("sheet_path")) or ""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def sourced(sentence: str, payload: dict[str, Any], fact_sheet: dict[str, Any] | None = None) -> bool:
    """Whether the fact check supports this sentence with a source: the payload's fact review is current
    (fact_evidence.current_fact_review) and the sentence's trace names facts of the sheet that carry evidence with
    a source address."""
    try:
        from .fact_evidence import current_fact_review
        if not current_fact_review(payload):
            return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    sheet = _sheet_for(payload, fact_sheet) or {}
    facts = {str(f.get("id")): f for f in sheet.get("facts") or [] if isinstance(f, dict)}
    target = _norm_sentence(sentence)
    for row in payload.get("sentence_trace") or []:
        if not isinstance(row, dict) or not target or _norm_sentence(row.get("sentence")) != target:
            continue
        for fid in row.get("fact_ids") or []:
            f = facts.get(str(fid))
            if f and any(isinstance(e, dict) and str(e.get("url") or "").startswith("http") for e in f.get("evidence") or []):
                return True
    return False


def protect(verdicts: list[dict[str, Any]], payload: dict[str, Any],
            fact_sheet: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Marks the verdicts of protected sentences "protected": True (and "sourced" when the fact check supports them
    with a source), in place; returns the verdicts."""
    keep = {_norm_sentence(s) for s in protected_sentences(payload or {})}
    for v in verdicts or []:
        if _norm_sentence(v.get("sentence")) in keep:
            v["protected"] = True
            v["sourced"] = sourced(v.get("sentence", ""), payload, fact_sheet)
    return verdicts


def has_descriptors(sentence: str, payload: dict[str, Any], fact_sheet: dict[str, Any] | None = None) -> bool:
    """Whether the fact sheet gives this line's subject the physical descriptors an honest illustration needs."""
    from .image_gen import spec_from_facts
    spec, _ = spec_from_facts(sentence, None, _sheet_for(payload or {}, fact_sheet), payload)
    return spec is not None


def entities(text: str) -> set:
    """Named entities, roughly: capitalized words not opening a sentence, and the stop words aside."""
    out = set()
    for s in sentences(text):
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*", s)
        out |= {_bare(w) for w in words[1:] if w[0].isupper() and w not in _ENTITY_STOP and len(w) > 1}
    return out


def _bare(word: str) -> str:
    """A word without its possessive ("Antikythera's" -> "Antikythera")."""
    return re.sub(r"['’]s$", "", word)


def lost_facts(before: str, after: str) -> list[str]:
    """The numbers (figures and number words) and named entities of `before` that `after` no longer says."""
    lost = [f"{n:g}" for n in sorted(_figures(before) - _figures(after))]
    said = {w.lower() for w in _NUMBER_WORDS.findall(after or "")}
    lost += [w for w in dict.fromkeys(x.lower() for x in _NUMBER_WORDS.findall(before or "")) if w not in said]
    lost += sorted(entities(before) - {_bare(w) for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", after or "")})
    return lost


def protected_rewrite_problem(before_narration: str, after_narration: str, protected: list[str]) -> str:
    """Why a rewording must be refused: it lost a number or a named entity of a protected sentence ("" if none)."""
    for s in protected or []:
        lost = lost_facts(s, after_narration)
        if lost:
            return f"the rewording lost {', '.join(lost[:4])} from the protected line “{clip(s, 60)}”"
    return ""


def rounds() -> int:
    """How many times a named-subject line no photo shows may go back to the writer (at least once)."""
    try:
        return max(1, int(getattr(config, "PREVOICE_REWRITE_ROUNDS", 2)))
    except (TypeError, ValueError):
        return 2


def unshown(verdicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The concrete sentences no photo shows; abstract, shown and generable ones are never sent back."""
    return [v for v in verdicts or [] if v.get("kind") == "unshown"]


def generable(verdicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sentences approved for era-conditioned illustration or scientific schematics."""
    return [v for v in verdicts or [] if v.get("kind") == "generable"]


def revision_note(lines: List[Dict[str, Any]], photos: List[Dict[str, str]], minimal: bool = False) -> str:
    """The writer's brief: just these sentences, toward what the photos show; every other sentence word for word.
    `minimal`: a regeneration for the pictures, whose script was otherwise approved as it is: the fewest words
    changed, the same facts. A protected line (protect: the hook, the closing line, a line carrying a packaging
    figure) is never asked to move toward the photos: only to be stated as the verified facts support it, with
    every number and name kept (a rewording that loses one is refused: protected_rewrite_problem)."""
    plain = [v for v in lines if not v.get("protected")]
    kept = [v for v in lines if v.get("protected")]
    note = ""
    if plain:
        named = "; ".join(f"“{v['sentence']}”" + (f" ({v['reason']})" if v.get("reason") else "") for v in plain)
        note += ("Before the voiceover: these narration sentences name something none of the video's licensed photos "
                 f"shows, so the picture under them would show something else: {named}. Reword just these sentences "
                 "toward what the photos do show, or say them as an idea, number or question with nothing in particular "
                 "to see. ")
    if kept:
        named = "; ".join(f"“{v['sentence']}”" for v in kept)
        note += ("These protected sentences carry the story's striking facts: " + named + ". Do not reword them toward "
                 "what the photos show; a labelled illustration will carry them. Change them only as far as the verified "
                 "facts require, and keep every number and every name in them. ")
    return (note + "Keep every other sentence word for word, keep the length, and use only the verified facts. "
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
