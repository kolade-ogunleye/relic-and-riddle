"""
src/beat_images.py
==================
Per-beat image sourcing, so the picture matches what the narrator is saying.

For beats that name a concrete, photographable subject ("ventilation shafts", "rolling
stone doors"), Claude writes a precise Wikimedia Commons search; openly licensed,
title-matched, high-resolution results are downloaded into the run folder. The scene
planner then picks between the topic's reviewed images and these candidates, and the
reviewer sees them in the video before approving. CC BY images are credited.
"""

import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image
from pydantic import BaseModel

from . import commons
from . import config
from .llm import LLMError, structured_call, task as llm_task, verdict_reason
from .models import BeatQueries

log = logging.getLogger(__name__)

# Photos come from Wikimedia Commons and partner archives (Europeana, Openverse, The Met...); the old wording is
# still recognised so a caption written before rc68 has its line replaced, never doubled (rc68 review 3)
CREDIT_PREFIX = "📷 Images via Wikimedia Commons and partner archives:"
CREDIT_PREFIXES = (CREDIT_PREFIX, "📷 Images via Wikimedia Commons:")
MAX_BEATS = 5          # only the most concrete beats get their own search
PER_BEAT = 3           # candidates per beat (2 found "2 candidates for 5 beats" on three Kailasa runs, 2026-10-24)

SYSTEM = """You plan picture research for a short history documentary. For each narration beat that names a concrete, photographable subject (a specific place, structure, object, artefact or document), write one precise Wikimedia Commons search for it, plus 1-4 lowercase keywords that a relevant file title would contain (proper nouns are best). Skip abstract beats (ideas, numbers, open questions). Prefer the specific site, archaeological drill core, museum specimen, or scientific micrograph over generic scenic landscape look-alikes.
When a beat's subject stands somewhere other than the documentary's own site (a prototype or model it was based on, a museum holding the object, a rival city), set place to that place's name, exactly as a Commons category would spell it; otherwise leave place null."""


def verify_visual_relevance(cand_title: str, query: str = "") -> bool:
    """Rejects modern tourist photos, gift shops, unrelated street scenes, or generic signage."""
    text = (cand_title + " " + query).lower()
    reject_patterns = ("tourist", "souvenir", "ticket", "restaurant", "hotel", "cafe", "bus", "parking", "gift shop", "selfie", "modern street")
    return not any(p in text for p in reject_patterns)


class PictureVerdict(BaseModel):
    shows_subject: bool       # the picture shows what was searched for
    burned_in_text: bool = False  # a caption strip, scale bar, watermark, logo or other text printed into the picture
    # What the text is and where (2026-10-28): the run log said "set aside, it does not show the subject (burned-in
    # text: <a sentence about the subject>)" for a photo set aside for its museum label (Trepanation, Hal Saflieni).
    text_reason: str = ""
    text_edge: str = "none"   # "top"/"bottom"/"left"/"right": all of the text lies in a band along that edge; "inside"
    text_box: List[float] = []  # [ymin, xmin, ymax, xmax] around all of the text, 0-1000
    # Something that is not part of the scene intruding along one edge (Nazca photo 6 on production, 2026-10-28: an
    # aircraft wing across the top 9%, which the judge after the render read as a "dark red strip" in the video).
    # Defaults keep verdicts written before these fields valid.
    edge_object: str = ""     # what it is: "an aircraft wing", "a window frame", "a finger", "a lens hood"
    object_edge: str = "none"  # "top"/"bottom"/"left"/"right": the edge it comes in from
    object_box: List[float] = []  # [ymin, xmin, ymax, xmax] around all of it, 0-1000
    reason: str               # one short sentence, from the picture itself


PICTURE_SYSTEM = ("You judge whether a photograph shows the subject a documentary beat was looking for. Judge the picture "
                  "itself, not its title. A reconstruction, model, diagram, excavation or museum display of the subject "
                  "counts; an unrelated place or building, a modern street, people posing, signage or a souvenir does not. "
                  "Also say whether text is printed into or shown in the picture itself (a caption strip, a scale bar with "
                  "units, a watermark, a logo, a date stamp, a museum label, placard or specimen tag, an accession or "
                  "catalogue number on a tag): such a picture is cropped on screen and the text shows cut off. "
                  "An inscription that is part of the subject (carved on a monument, written on a manuscript page) is not "
                  "printed text. When there is text: text_reason says what it is and where; text_edge is \"top\", "
                  "\"bottom\", \"left\" or \"right\" when all of it lies in a band along that edge of the picture, "
                  "otherwise \"inside\"; text_box is [ymin, xmin, ymax, xmax] around all of it, as integers from 0 to "
                  "1000. Also say whether something that is not part of the scene intrudes along an edge of the picture: "
                  "part of the aircraft, vehicle or window the photo was taken from (a wing, a strut, a window frame, "
                  "a car's bonnet or mirror), a finger, a camera strap or a lens hood. When "
                  "there is: edge_object names it in a few words; object_edge is \"top\", \"bottom\", \"left\" or "
                  "\"right\", the edge it comes in from; object_box is [ymin, xmin, ymax, xmax] around all of it, as "
                  "integers from 0 to 1000. Part of the subject or its setting (a wall, a tree, the ground, the sky) "
                  "is not an intrusion: then edge_object stays empty. reason is about the subject only.")


UNAVAILABLE = "not looked at: the picture check could not run"


def _box(box) -> List[int]:
    box = list(box or [])
    ok = len(box) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in box) \
        and box[0] < box[2] and box[1] < box[3]
    return [int(v) for v in box] if ok else []


def _text_fields(verdict: "PictureVerdict") -> Dict[str, Any]:
    edge = (verdict.text_edge or "none").strip().lower()
    obj_edge = (verdict.object_edge or "none").strip().lower()
    return {"text_reason": verdict_reason(verdict.text_reason),
            "text_edge": edge if edge in ("top", "bottom", "left", "right", "inside") else "none",
            "text_box": _box(verdict.text_box),
            "edge_object": (verdict.edge_object or "").strip(),
            "object_edge": obj_edge if obj_edge in ("top", "bottom", "left", "right") else "none",
            "object_box": _box(verdict.object_box)}


def picture_look(path: Path, query: str, size: int = 512, ask: Optional[str] = None) -> Dict[str, Any]:
    """One look at the pixels: {"checked", "shows", "burned_in_text", "reason", "text_reason", "text_edge",
    "text_box", "edge_object", "object_edge", "object_box"}, the picture sent at `size` px on its long side. A
    check that fails is asked once more; one that still cannot run says checked False and shows False: a picture
    nobody looked at is not used (operator, 2026-10-27: it was kept as showing its subject, and the gate is to be
    enforced). Turning the check off (PICTURE_VISION_CHECK=false) is the only way a picture goes unlooked at.
    `ask` replaces the question put with the picture (the line check before the render asks about a narration
    line, not a search)."""
    if not config.PICTURE_VISION_CHECK:
        return {"checked": False, "shows": True, "burned_in_text": False, "reason": "not checked"}
    from . import free_llm
    from .llm import image_block
    import time
    question = ask or f"Searched for: {query}\nDoes this picture show it?"
    content = [{"type": "text", "text": question}, image_block(path, size)]
    for attempt in (1, 2):
        try:
            if free_llm.claude_engine():
                verdict = structured_call(PICTURE_SYSTEM, content, PictureVerdict, effort="low", max_tokens=400,
                                          models=config.LIGHT_TASK_MODELS)
            else:
                verdict = free_llm.structured(PICTURE_SYSTEM, content, PictureVerdict, role="checker", task_class="light")
            break
        except Exception as e:  # unavailable or malformed
            if attempt == 1:
                log.info("Picture check for %s failed (%s); asking again in %.0f s.", path.name, e, config.PICTURE_CHECK_RETRY_SECONDS)
                time.sleep(config.PICTURE_CHECK_RETRY_SECONDS)
                continue
            log.warning("Picture check for %s could not run (%s); the picture is not used.", path.name, e)
            return {"checked": False, "shows": False, "burned_in_text": False, "reason": UNAVAILABLE}
    return {"checked": True, "shows": bool(verdict.shows_subject), "burned_in_text": bool(verdict.burned_in_text),
            # whole (the card folds it), less a model's stray working notes (verdict_reason)
            "reason": verdict_reason(verdict.reason), **_text_fields(verdict)}


UNCHECKED_FILE = "set_aside_unchecked.json"


def _note_unchecked(work_dir: Path, beat: int, cand: Dict[str, Any]) -> None:
    import json as _json
    path = Path(work_dir) / UNCHECKED_FILE
    try:
        found = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        found = []
    found.append({"beat": beat, "title": cand.get("title"), "source_url": cand.get("source_url")})
    path.write_text(_json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")


def unchecked(work_dir: Path) -> List[Dict[str, Any]]:
    """The pictures this run left out because their check could not run."""
    import json as _json
    try:
        return _json.loads((Path(work_dir) / UNCHECKED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def picture_shows(path: Path, query: str) -> Tuple[bool, str]:
    """A look at the pixels before a found picture is kept (quality step 1, 2026-10-24): the title and
    metadata checks let a photograph of the wrong temple through. A picture with text printed into it (a
    caption strip, a scale bar) is set aside too: cropped on screen, the text shows cut off (a carousel
    slide, 2026-10-25). (shows, why) of screened_picture, which may keep a cropped copy instead."""
    shows, why, _ = screened_picture(path, query)
    return shows, why


def _text_verdict(look: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A picture_look as the screen's verdict ({"text", "reason", "edge", "box", "object", "object_edge",
    "object_box", "shows", "subject"}), None when nobody looked. "object" is "" when nothing intrudes."""
    if not look.get("checked"):
        return None
    text = bool(look["burned_in_text"])
    obj = str(look.get("edge_object") or "").strip()
    return {"text": text, "reason": (look.get("text_reason") or look["reason"]) if text else look["reason"],
            "edge": look.get("text_edge", "none"), "box": look.get("text_box") or [],
            "object": obj, "object_edge": look.get("object_edge", "none") if obj else "none",
            "object_box": (look.get("object_box") or []) if obj else [],
            "shows": bool(look["shows"]), "subject": look["reason"]}


def screened_picture(path: Path, query: str) -> Tuple[bool, str, Path]:
    """(shows, why, the file to use) for a picture found for a beat. The pool screen's look (audit 2026-10-28): at
    TEXT_SCREEN_SIZE, where a museum label or accession number is seen (at 512 px Trepanation photo 14's label
    was missed), and text in an edge band, or a foreign object along an edge, is cropped away and the copy looked
    at again (clear_edges), so a found photo with a label strip or a wing tip is kept cropped rather than set
    aside. The crop's own look decides whether it shows the subject; it is kept beside its original
    (src/lineage.py), whose credit it resolves to."""
    path = Path(path)
    look = picture_look(path, query, TEXT_SCREEN_SIZE)
    seen = _text_verdict(look)
    if seen is None or not (seen["text"] or seen["object"]):
        return look["shows"], look["reason"], path
    if not look["shows"]:  # the wrong picture: no crop makes it right
        return False, (TEXT_PREFIX + seen["reason"]) if seen["text"] else look["reason"], path
    use, final, why = clear_edges(path, seen, lambda c: _text_verdict(picture_look(c, query, TEXT_SCREEN_SIZE)))
    if use is None:
        return False, why, path
    return final["shows"], final["subject"], use


TEXT_PREFIX = "burned-in text: "
OBJECT_PREFIX = "foreign object along an edge: "


# The picture against its narration line, before the render (render audit 2026-10-28): Roman concrete x3 on
# production was blocked after a full render for Pantheon photos under "modern infrastructure" lines, and the
# repair after the render picked the Pantheon again. The same light-model look as above, asked about the line.
# Each verdict is kept by the photo's hash and the line's words, so a resume, a re-render or a regeneration
# that keeps the script pays nothing again. FIT_VERSION changes when the question does.
FIT_VERSION = 1
FIT_CACHE_FILE = config.BASE_DIR / "cache" / "picture_fit.json"
_fit_lock = threading.Lock()


def fit_question(line: str, subject: str) -> str:
    """What the light model is asked about a photo and its line: what the line says, not merely the topic."""
    return (f"Narration line of a short documentary about {subject or 'history'}: \"{line.strip()}\"\n"
            "Does this picture show what this line says? A line that names something that can be seen (a place, "
            "building, object, material, event or process) needs a picture of that thing itself; a picture of the "
            "documentary's subject is not enough, and a different building, place or era than the one the line names "
            "does not show it (shows_subject false). A line about an idea, a number or a question, with nothing named "
            "to see, is shown by a picture of the documentary's subject. Judge the picture, not its title.")


def _line_words(line: str) -> str:
    return " ".join((line or "").lower().split())


def fit_key(digest: str, line: str) -> str:
    import hashlib
    return hashlib.sha256(f"{FIT_VERSION}|{digest}|{_line_words(line)}".encode("utf-8")).hexdigest()


def load_fit_cache() -> Dict[str, Any]:
    import json as _json
    path = Path(FIT_CACHE_FILE)
    try:
        found = _json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def save_fit_cache(cache: Dict[str, Any]) -> None:
    """Merged with what is on disk (another run may have added verdicts since), then replaced atomically."""
    from .atomic_files import write_json
    with _fit_lock:
        try:
            write_json(FIT_CACHE_FILE, {**load_fit_cache(), **cache})
        except OSError as e:
            log.debug("Could not save the picture-line verdicts: %s", e)


def line_fit(path: Path, line: str, subject: str, cache: Dict[str, Any],
             digest: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """{"fits", "reason", "cached"} for a photo under a narration line, or None when nobody could look (the check
    is off, or the light model could not run: the caller keeps its plan, and the judge after the render still
    looks). A verdict in `cache` costs nothing; a new one is one light-model call, added to `cache`. `digest`: the
    photo's sha256 when the caller already has it."""
    from .credits import sha256_file
    digest = digest or sha256_file(path)
    if not digest:
        return None
    key = fit_key(digest, line)
    hit = cache.get(key)
    if isinstance(hit, dict) and "fits" in hit:
        return {"fits": bool(hit["fits"]), "reason": verdict_reason(hit.get("reason")), "cached": True}
    try:
        look = picture_look(Path(path), line, ask=fit_question(line, subject))
    except (OSError, ValueError) as e:  # an unreadable picture: not looked at
        log.info("Picture %s could not be looked at against its line (%s).", Path(path).name, e)
        return None
    if not look.get("checked"):
        return None
    reason = str(look.get("reason") or "")
    cache[key] = {"fits": bool(look["shows"]), "reason": reason, "file": Path(path).name,
                  "line": _line_words(line)[:160], "v": FIT_VERSION}
    return {"fits": bool(look["shows"]), "reason": reason, "cached": False}


@llm_task("beat_queries")
def plan_queries(beats: List[Dict[str, Any]], topic: Dict[str, Any]) -> BeatQueries:
    lines = "\n".join(f"Beat {i}: {b['text']}" for i, b in enumerate(beats, 1))
    loc = topic.get("location", {})
    prompt = f"Topic: {topic.get('title', '')} ({loc.get('name', '')}, {loc.get('country', '')})\n{lines}\n\nAt most {MAX_BEATS} beats."
    from . import free_llm
    if free_llm.claude_engine():
        return structured_call(SYSTEM, prompt, BeatQueries, effort="low", max_tokens=3000,
                               models=config.LIGHT_TASK_MODELS)
    return free_llm.structured(SYSTEM, prompt, BeatQueries, role="writer", task_class="light")


def source(beats: List[Dict[str, Any]], topic: Dict[str, Any], work_dir: Path, plan=None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Returns (downloaded image paths, their credit records). Never raises: extra images are optional."""
    if not config.BEAT_IMAGES_ENABLED:
        return [], []
    from . import free_llm
    if not free_llm.ready():
        return [], []
    if plan is None:
        try:
            plan = plan_queries(beats, topic)
        except LLMError as e:
            log.warning("Beat image planning failed: %s", e)
            return [], []
    paths, credits = _download(plan.items[:MAX_BEATS], topic, work_dir, set(), "beat")
    log.info("Beat images: %d candidates for %d beats.", len(paths), len(plan.items))
    return paths, credits


GAP_SYSTEM = """You plan picture research for a short history documentary. The video check found that these beats' pictures do not show what the narration says. For each beat, write one precise Wikimedia Commons search for what that sentence is about, plus 1-4 lowercase keywords that a relevant file title would contain. Stay on the documentary's subject: the thing named, or the same kind of thing from the same civilisation or period (for "Roman aqueducts", a Roman aqueduct; for "modern concrete cracking", a photograph of cracked concrete), never a look-alike from another subject. Prefer a specific named example (a named aqueduct, harbour, specimen or document) over a generic scene.
When the thing named stands somewhere other than the documentary's own site (a prototype it was modelled on, a museum, another city), set place to that place's name as a Commons category would spell it; otherwise leave place null."""


@llm_task("beat_gap_queries")
def plan_gap_queries(beats: List[Dict[str, Any]], gap_beats: List[int], topic: Dict[str, Any], notes: str = "") -> BeatQueries:
    lines = "\n".join(f"Beat {i}: {beats[i - 1]['text']}" for i in gap_beats if 1 <= i <= len(beats))
    loc = topic.get("location", {})
    prompt = (f"Topic: {topic.get('title', '')} ({loc.get('name', '')}, {loc.get('country', '')})\n{lines}"
              + (f"\n\nWhat the video check found:\n{notes}" if notes else "") + "\n\nOne search per beat listed.")
    from . import free_llm
    if free_llm.claude_engine():
        return structured_call(GAP_SYSTEM, prompt, BeatQueries, effort="low", max_tokens=3000,
                               models=config.LIGHT_TASK_MODELS)
    return free_llm.structured(GAP_SYSTEM, prompt, BeatQueries, role="writer", task_class="light")


def source_gaps(beats: List[Dict[str, Any]], gap_beats: List[int], topic: Dict[str, Any], work_dir: Path,
                seen: set, notes: str = "", prefix: str = "gap",
                deadline: Optional[float] = None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Pictures for the beats the video check found mismatched, when the first pictures did not cover them:
    a search for what each of those sentences says, from Wikimedia Commons, through the same licence,
    title-keyword, relevance and visual filters as the first search. `prefix` names the files (the check before
    the voice searches for narration sentences: "line"). Never raises."""
    if not gap_beats or not config.BEAT_IMAGES_ENABLED:
        return [], []
    from . import free_llm
    if not free_llm.ready():
        return [], []
    try:
        plan = plan_gap_queries(beats, gap_beats, topic, notes)
    except LLMError as e:
        log.warning("Gap picture planning failed: %s", e)
        return [], []
    wanted = set(gap_beats)
    # A repair comes at the end of a long run: its downloads may wait out a rate limit (90 s, not 30).
    paths, credits = _download([i for i in plan.items if i.beat in wanted], topic, work_dir, seen, prefix, wait=90,
                               **({"deadline": deadline} if deadline is not None else {}))
    log.info("Gap pictures: %d candidates for beats %s.", len(paths), ", ".join(map(str, sorted(wanted))))
    return paths, credits


def _pool_records(topic: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The credit records of the topic's usable photos (Topic, Kept, Context): which Commons category is the
    topic's own is read from them (commons.topic_anchor). [] without a pool."""
    try:
        from .credits import load_credits
        from .image_review import tier
        images = load_credits(Path(config.ASSETS_DIR) / str(topic.get("id") or "")).get("images", [])
        return [im for im in images if tier(im) in ("topic", "kept", "context")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _other_names(topic: Dict[str, Any]) -> List[str]:
    """The topic's other names: the Commons searches its photo pool was fetched with (image_queries.json), so a
    category named after the object rather than the title is found (commons.topic_anchor). [] without them."""
    try:
        import json as _json
        specs = _json.loads((Path(config.TOPICS_DIR) / "image_queries.json").read_text(encoding="utf-8"))
        spec = specs.get(str(topic.get("id") or "")) or {}
        return [str(q) for q in spec.get("queries") or [] if isinstance(q, str)]
    except (OSError, ValueError, AttributeError):
        return []


def _download(items, topic: Dict[str, Any], work_dir: Path, seen: set, prefix: str,
              wait: int = 30, deadline: Optional[float] = None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Downloads the best openly licensed, on-subject Commons results for each planned search; records them
    in the folder's credits.json (added to, so a later search keeps the earlier records). `deadline`
    (time.monotonic()): no further search or look once it has passed; what was found by then is kept."""
    import time as _time
    late = lambda: deadline is not None and _time.monotonic() > deadline  # noqa: E731
    work_dir.mkdir(parents=True, exist_ok=True)
    loc = topic.get("location") or {}
    place = ({"lat": loc["latitude"], "lon": loc["longitude"]}
             if loc.get("latitude") and loc.get("longitude") else None)
    paths, credits = [], []
    for item in items:
        if late():
            log.info("Picture search stopped at its time budget; %d photo(s) found by then.", len(paths))
            break
        try:
            found = 0
            # A beat about a place other than the topic's site (a prototype, a museum) is ranked by that
            # place's name, not by distance from the site: the right photo of the Virupaksha Temple at
            # Pattadakal, 430 km from Ellora, scored 3/10 for being where Pattadakal is (2026-10-24).
            keywords, near = item.keywords, place
            elsewhere = (getattr(item, "place", None) or "").strip()
            if elsewhere and elsewhere.lower() not in (loc.get("name") or "").lower():
                keywords = list(item.keywords) + [w for w in commons.normalize(elsewhere).split() if len(w) > 3]
                near = None
            # Short wait cap: a rate-limited Commons shouldn't stall a production run. Results are
            # ranked by what their metadata says they show (commons.relevance), best first. A precise query that
            # finds almost nothing is tried shorter, within the topic's (or the beat's place's) Commons category or
            # coordinates (commons.search_widened, dev Nazca 2026-10-28): more candidates only, through the same
            # filters and looks below.
            results, tried = commons.search_widened(
                item.query, lambda: commons.topic_anchor(topic, elsewhere, photos=_pool_records(topic),
                                                         aliases=_other_names(topic)),
                list(item.keywords), limit=30, max_wait=60)
            ranked = commons.best_first(list(commons.candidates(results, keywords, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE)),
                                        keywords, near)
            relevant, verified, looked_away = 0, 0, 0
            for cand in ranked:
                if late():
                    break
                if cand["title"] in seen or cand["relevance"] <= 0:
                    continue
                relevant += 1
                if not verify_visual_relevance(cand["title"], item.query):
                    continue
                verified += 1
                dest = work_dir / f"{prefix}{item.beat:02d}_{found + 1}.jpg"
                commons.download_jpeg(cand["download_url"], dest, max_wait=wait)
                # The pool screen's look (1024 px, an edge band of text cropped away and looked at again): a found
                # photo with a label strip was set aside at 512 px, uncropped (audit 2026-10-28).
                shows, why, use = screened_picture(dest, item.query)
                if why == UNAVAILABLE:  # nobody looked: left out, and said on the card (found again next run)
                    _note_unchecked(work_dir, item.beat, cand)
                    dest.unlink(missing_ok=True)
                    continue
                if not shows:  # the pixels say otherwise: not this one
                    if why.startswith(TEXT_PREFIX):
                        log.info("Beat %d: %r set aside, it has text printed into it (%s).", item.beat, cand["title"][:70],
                                 why[len(TEXT_PREFIX):])
                    else:
                        log.info("Beat %d: %r set aside, it does not show the subject (%s).", item.beat, cand["title"][:70], why)
                    dest.unlink(missing_ok=True)
                    looked_away += 1
                    continue
                seen.add(cand["title"])
                # A cropped copy is what the video uses; its original stays here, where the folder's credits.json
                # names it and the copy's lineage points (its credit and rights resolve to the original).
                cand.update(file=str(use), beat=item.beat, looked=why, **({"cropped_from": str(dest)} if use != dest else {}))
                paths.append(use)
                credits.append(cand)
                found += 1
                if found >= PER_BEAT:
                    break
            # The run's record of why a beat got pictures or none: "0 candidates for 5 beats" on production
            # (2026-10-24, Antikythera) could not be told apart from a broken search without these counts.
            searches = "; ".join(f"{q!r}: {n}" for q, n in tried)
            log.debug("Beat %d %r: %d Commons results (%s), %d usable (share-alike %s), %d relevant, %d verified, "
                      "%d set aside by the look, %d kept.",
                      item.beat, item.query[:80], len(results), searches, len(ranked),
                      "allowed" if config.BEAT_IMAGES_ALLOW_SHARE_ALIKE else "excluded", relevant, verified, looked_away, found)
            # Only when the licence was the reason: results can also fail on keyword, size or author (audit, 2026-10-28)
            share_alike = 0 if ranked or config.BEAT_IMAGES_ALLOW_SHARE_ALIKE else len(list(
                commons.candidates(results, keywords, True)))
            if share_alike:
                from . import improve  # the operator's lever, filed where the digest will show it
                improve.note("pictures.licence_excluded", f"beat {item.beat}: {share_alike} of {len(results)} Commons result(s) usable only "
                             "with share-alike licences (IMAGES_ALLOW_SHARE_ALIKE is off)", source="pictures")
        except (requests.RequestException, OSError, Image.DecompressionBombError) as e:
            log.warning("Beat %d image search failed (%s); continuing.", item.beat, e)
    if credits:  # the same record a topic folder keeps, so the scene planner can caption these too
        import json
        record = work_dir / "credits.json"
        try:
            earlier = json.loads(record.read_text(encoding="utf-8")).get("images", []) if record.exists() else []
        except (OSError, ValueError):
            earlier = []
        record.write_text(json.dumps(
            {"reviewed": False, "images": earlier + [{**c, "file": Path(c["file"]).name} for c in credits]},
            indent=2, ensure_ascii=False), encoding="utf-8")
        from .scene_planner import _folder_credits
        _folder_credits.cache_clear()  # the planner reads captions through a cache: show it the new ones
    return paths, credits


def used_credits(scenes: List[Path], credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Credit records for the beat images the scene plan actually put in the video."""
    chosen = {Path(p) for p in scenes}
    return [c for c in credits if Path(c["file"]) in chosen or Path(c.get("upscaled") or c["file"]) in chosen]


def credit_caption(caption: str, topic_credits: Dict[str, Any], extra: List[Dict[str, Any]]) -> str:
    """Replaces the caption's image credit line with one covering the topic and beat images used.
    Idempotent, so a resumed run doesn't stack credit lines."""
    from .credits import caption_credit_line
    new = caption_credit_line({"images": topic_credits.get("images", []) + extra})
    kept = [ln for ln in caption.split("\n") if not ln.startswith(CREDIT_PREFIXES)]
    body = "\n".join(kept).rstrip()
    return f"{body}\n\n{new}" if new else body


# "visual"/"display": Roman concrete's beat 9 ("the visual displays the interior dome") matched none of the
# older words, so the mismatch was never repaired.
# Word stems: "shows", "showing" and "show" all count (a finding that said "the video continues to show
# the Kailasa Temple ... rather than the prototype temple mentioned" matched nothing and went unrepaired,
# production 2026-10-24).
PICTURE_WORDS = ("image", "picture", "photo", "show", "depict", "match", "contradict", "unrelated", "irrelevant",
                 "visual", "display", "footage", "scene", "video", "frame", "b-roll", "clip")
CAPTION_ONLY = ("caption", "subtitle")  # a finding about the text on screen, unless it is also about the picture


# A minor finding counts only when it says the picture contradicts what is said (Roman concrete's beat 8:
# "...the Pantheon while the narration describes modern concrete cracking"), not a quality note ("soft").
MISMATCH_WORDS = ("narration", "voiceover", "contradict", "unrelated", "irrelevant", "instead of", "does not show")


def _picture_findings(report) -> list:
    """QA findings about what a beat shows (not its captions or text): major/critical ones, and minor ones
    that report a mismatch with the narration."""
    if report is None:
        return []
    out = []
    for i in report.issues:
        text = i.issue.lower()
        if i.beat <= 0 or not any(w in text for w in PICTURE_WORDS):
            continue
        if any(w in text for w in CAPTION_ONLY) and not any(w in text for w in ("video", "footage", "frame", "image", "photo", "picture", "show")):
            continue
        if i.severity in ("major", "critical") or any(w in text for w in MISMATCH_WORDS):
            out.append(i)
    return out


def picture_issues(report) -> str:
    """QA findings about what a beat shows, as planner feedback. Minor ones count too: a picture that does
    not show what is said is a mismatch whatever its rating (beat 8 of Roman concrete was "minor")."""
    return "\n".join(f"Beat {i.beat}: {i.issue}" for i in _picture_findings(report))


def mismatched_beats(report) -> List[int]:
    """The beats whose picture does not show what is said, for a targeted picture search."""
    return sorted({i.beat for i in _picture_findings(report)})


# A finding that the picture is shown again ("an identical reuse of the wide shot from Beat 1 and Beat 5", Nazca
# 2026-10-28) is about the plan, not the photo: the photo was right where it was first used.
_REPEAT = re.compile(r"\b(?:re-?us(?:e|ed|es|ing)|repeat\w*|identical|duplicat\w*|(?:shown|used|appears?) (?:twice|again)"
                     r"|same (?:\w+ )?(?:shot|image|photo|photograph|picture|footage|frame|view|still|clip))\b", re.I)


def repeat_finding(text: str) -> bool:
    """True when a video-check finding says a picture is shown again (a repeat), whatever else it says."""
    return bool(_REPEAT.search(text or ""))


def wrong_subject_beats(report) -> List[int]:
    """The mismatched beats (mismatched_beats) with a finding that is not about a repeat: only these say the photo
    itself shows the wrong thing, so only these may withdraw it (src/scene_planner.withdrawals)."""
    return sorted({i.beat for i in _picture_findings(report) if not repeat_finding(i.issue)})


TEXT_SCREEN_FILE = ".picture_text.json"  # per topic folder: {sha256: {"text": bool, "reason": str, ...}}
# Screen 2 (2026-10-28): the picture is sent at 1024 px, not 512, and museum labels and accession numbers are named:
# at 512 px Trepanation photo 14 (Wellcome L0035690, "A650847" on a label at the bottom edge) was screened text: False
# and the late judge blocked the run. Verdicts from an older screen are looked at again, once.
# Screen 3 (2026-10-28): the same look also names a foreign object along an edge (Nazca photo 6's aircraft wing), so
# every photo is looked at once more; a screen-2 verdict stands (no object) while the new look cannot run.
TEXT_SCREEN_VERSION = 3
TEXT_SCREEN_SIZE = 1024   # the same light-model call; the picture costs about four times the image tokens
# Text in a band along one edge (a label under a specimen, a logo in a corner strip, a scale bar) is cropped away
# rather than the photo dropped (production Richat x3, Hal Saflieni, dev Trepanation, 2026-10-26/27). Trepanation
# photo 14's label starts 87% of the way down, so the band is 15%, not 12%.
TEXT_CROP_MAX = 0.15       # of the side the band is cut from
TEXT_CROP_MARGIN = 0.015   # beyond the text box: its estimate is approximate
TEXT_CROP_DIR = config.BASE_DIR / "cache" / "text_crop"
# A foreign object along an edge (a wing, a window frame, a finger) is cut the same way, within the same band: a
# larger one takes too much of the picture, which is then not used (Nazca photo 6's wing: the top 9%).
EDGE_OBJECT_CROP = "edge_object_crop"


# Bands tried, in turn, when the screen names an object's edge but not its extent; the last is TEXT_CROP_MAX.
OBJECT_TRY_BANDS = (0.06, 0.10, TEXT_CROP_MAX)


def _edge_box(edge: str, fraction: float) -> List[int]:
    """The box (0-1000) whose band along `edge` is `fraction` of the picture, margin included (_crop_band)."""
    k = int(round((fraction - TEXT_CROP_MARGIN) * 1000))
    return {"top": [0, 0, k, 1000], "bottom": [1000 - k, 0, 1000, 1000],
            "left": [0, 0, 1000, k], "right": [0, 1000 - k, 1000, 1000]}[edge]


def intrusion_noted(path: Path) -> bool:
    """Whether the screen saw a foreign object in this very file and it was not cropped away (a cropped copy has its
    own file): such a photo may still fill a beat, but never opens the video or is animated (dev run, 2026-10-28:
    the wing photo opened as "the widest recorded view")."""
    from .credits import sha256_file
    from .lineage import sidecar
    import json as _json
    path = Path(path)
    for _ in range(6):  # an upscaled or blurred copy is judged by the photo it was made from; an edge crop is clear
        try:
            record = _json.loads(sidecar(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            break
        if record.get("operation") == EDGE_OBJECT_CROP:
            return False
        try:
            path = Path(record["sources"][0]["path"])
        except (KeyError, IndexError, TypeError):
            break
    try:
        cache = _json.loads((path.parent / TEXT_SCREEN_FILE).read_text(encoding="utf-8"))
        return bool((cache.get(sha256_file(path)) or {}).get("object"))
    except (OSError, ValueError, AttributeError):
        return False


def _crop_band(edge: str, box: List[int]) -> float:
    """The fraction of the picture to cut from `edge` to remove all of the text in `box` (0-1000)."""
    ymin, xmin, ymax, xmax = (v / 1000 for v in box)
    return {"bottom": 1 - ymin, "top": ymax, "right": 1 - xmin, "left": xmax}[edge] + TEXT_CROP_MARGIN


def crop_text_band(path: Path, edge: str, box: List[int], operation: str = "text_crop",
                   why: str = "") -> Optional[Path]:
    """A copy of `path` without the band along `edge` that holds the text in `box`, or None when the text is not
    in an edge band of at most TEXT_CROP_MAX, or the copy would fall below the resolution a found photo needs
    (src/commons.py) while the original met it. The copy keeps its file name (an approved storyboard and the
    strong-photo list match by name and stem), sits in its own folder under cache/text_crop, and records its
    source (src/lineage.py), so its credit and rights evidence resolve to the original. `operation` names the cut
    in that record (EDGE_OBJECT_CROP for a foreign object along the edge, `why` saying what it is)."""
    from . import commons
    from .credits import sha256_file
    from .lineage import record_derived, source_chain
    import hashlib
    path = Path(path)
    if edge not in ("top", "bottom", "left", "right") or len(box) != 4:
        return None
    cut = round(_crop_band(edge, box), 4)  # 0.135 + 0.015 is a hair over 0.15 in floating point
    if cut <= TEXT_CROP_MARGIN or cut > TEXT_CROP_MAX:
        return None
    with Image.open(path) as im:
        w, h = im.size
        k = int(round(cut * (h if edge in ("top", "bottom") else w)))
        area = {"bottom": (0, 0, w, h - k), "top": (0, k, w, h), "right": (0, 0, w - k, h), "left": (k, 0, w, h)}[edge]
        cw, ch = area[2] - area[0], area[3] - area[1]
        met = w * h >= commons.MIN_PIXELS and h >= commons.MIN_HEIGHT
        if met and not (cw * ch >= commons.MIN_PIXELS and ch >= commons.MIN_HEIGHT):
            return None
        settings = {"edge": edge, "fraction": round(cut, 4), "text_box": list(box), "pixels": list(area)}
        if operation != "text_crop":
            settings.update(object=why, object_box=settings.pop("text_box"))
        # A text crop keeps the key it always had (copies made before stay valid); another cut is its own copy.
        key = hashlib.sha256((f"{sha256_file(path)}|{edge}|{k}" + ("" if operation == "text_crop" else f"|{operation}")
                              ).encode()).hexdigest()[:16]
        out = TEXT_CROP_DIR / key / path.name
        if out.is_file():
            try:
                if source_chain(out):
                    return out
            except (ValueError, KeyError, OSError):
                pass
        out.parent.mkdir(parents=True, exist_ok=True)
        cropped = im.crop(area)
        extra = {k2: im.info[k2] for k2 in ("exif", "icc_profile") if im.info.get(k2)}  # same orientation, colours
        if path.suffix.lower() in (".jpg", ".jpeg"):
            cropped.convert("RGB").save(out, "JPEG", quality=95, **extra)
        else:
            cropped.save(out, **extra)
    record_derived(out, [path], operation, settings)
    return out


def cropped_clear(path: Path, edge: str, box: List[int], look_again, operation: str = "text_crop",
                  why: str = "") -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """(crop, its verdict): crop_text_band's copy of `path` (None when no band could be cut) and look_again(copy), a
    verdict with "text" and "reason", or None when nobody looked. The caller uses the copy only when that second
    look finds no text left. One rule for the pool screen and the photos found per beat (audit 2026-10-28)."""
    try:
        crop = crop_text_band(Path(path), edge, box, operation, why)
    except (OSError, ValueError) as e:
        log.info("Photo %s: its %s band could not be cropped (%s).", Path(path).name,
                 "text" if operation == "text_crop" else "edge", e)
        return None, None
    return (crop, look_again(crop)) if crop is not None else (None, None)


def clear_edges(path: Path, verdict: Dict[str, Any], look_again) -> Tuple[Optional[Path], Dict[str, Any], str]:
    """(the file to use, its verdict, why not) for a photo whose screen verdict (_text_verdict's keys) found text
    or a foreign object: text in an edge band is cropped away first, then a foreign object along an edge, each copy
    looked at again (look_again) and kept only when that look finds neither left. None and the reason when text
    could not be cropped away (inside the picture, a band larger than TEXT_CROP_MAX, the copy too small, the look
    still finds it: "burned-in text: ...") or a foreign object lies in a valid edge band larger than TEXT_CROP_MAX
    ("foreign object along an edge: ..."). An object that cannot be placed in a band, or whose crop fails or is
    not looked at again, leaves the photo as it is: a doubtful intrusion never costs a good photo. One rule for the
    pool screen and the photos found per beat."""
    use = Path(path)
    if verdict.get("text"):
        why = TEXT_PREFIX + str(verdict.get("reason", ""))
        crop, again = cropped_clear(use, verdict.get("edge", "none"), verdict.get("box") or [], look_again)
        if crop is None or not again or again.get("text"):
            if crop is not None:
                log.info("Photo %s: text is still there after its %s band was cropped (%s).", use.name,
                         verdict.get("edge"), (again or {}).get("reason", "not looked at"))
            return None, verdict, why
        log.info("Photo %s has text printed into it (%s); cropped away: the %s %.0f%%.", use.name, why[len(TEXT_PREFIX):],
                 verdict["edge"], 100 * _crop_band(verdict["edge"], verdict["box"]))
        use, verdict = crop, again
    if verdict.get("object"):
        what, edge, box = str(verdict["object"]), verdict.get("object_edge", "none"), verdict.get("object_box") or []
        if edge in ("top", "bottom", "left", "right") and len(box) != 4:
            # The edge without its extent: the light model names the side but often gives no box (Nazca photo 6's
            # wing, top edge, empty box, then opened the dev run of 2026-10-28). Cut a growing band from that edge,
            # looking again after each, up to the band that may be cut; still seen there, the photo is not used.
            for fraction in OBJECT_TRY_BANDS:
                crop, again = cropped_clear(use, edge, _edge_box(edge, fraction), look_again, EDGE_OBJECT_CROP, what)
                if crop is None or not again:
                    log.info("Photo %s: %s along the %s edge could not be cropped or looked at again; kept as it is.",
                             use.name, what, edge)
                    return use, verdict, ""
                if not again.get("object"):
                    log.info("Photo %s: %s along the %s edge; cropped away: the %s %.0f%%.", use.name, what, edge,
                             edge, 100 * fraction)
                    return crop, again, ""
            log.info("Photo %s: %s along the %s edge is still seen with the %s %.0f%% cut; not used.", use.name, what,
                     edge, edge, 100 * OBJECT_TRY_BANDS[-1])
            return None, verdict, OBJECT_PREFIX + what
        if edge not in ("top", "bottom", "left", "right"):
            # Named but on no edge: the photo stays as it is (a wrong guess must not cost a good photo), but it
            # never opens the video or is animated (intrusion_noted).
            log.info("Photo %s: %s noted, not along one edge in a band that can be cut; kept as it is.", use.name, what)
            return use, verdict, ""
        if _crop_band(edge, box) > TEXT_CROP_MAX:   # it takes too much of the picture: not used
            log.info("Photo %s: %s along the %s edge takes %.0f%% of it, more than the %.0f%% that may be cut.",
                     use.name, what, edge, 100 * _crop_band(edge, box), 100 * TEXT_CROP_MAX)
            return None, verdict, OBJECT_PREFIX + what
        crop, again = cropped_clear(use, edge, box, look_again, EDGE_OBJECT_CROP, what)
        if crop is None or not again or again.get("object"):
            log.info("Photo %s: %s along the %s edge %s; kept as it is.", use.name, what, edge,
                     "is still seen after its band was cropped" if crop is not None and again else
                     "could not be cropped away or looked at again")
            return use, verdict, ""
        log.info("Photo %s: %s along the %s edge; cropped away: the %s %.0f%%.", use.name, what, edge, edge,
                 100 * _crop_band(edge, box))
        use, verdict = crop, again
    return use, verdict, ""


def without_burned_in_text(photos: List[Path], subject: str, minimum: int) -> List[Path]:
    """The topic's reviewed photos without those that carry text printed into the picture (a museum placard,
    a caption strip, a scale bar): cropped on screen, the text shows cut off or under the captions (the
    Tunguska development run, 2026-10-25). Text in a band along one edge is cropped away instead (the copy is
    looked at again, and used only when its text is gone), and so is a foreign object along an edge (a wing, a
    window frame, a finger; Nazca 2026-10-28), when it lies within the band that may be cut (clear_edges); a larger
    one sets the photo aside. Each photo is looked at once; the verdict is cached beside it by its hash, so later
    runs pay nothing. Videos are kept. Never leaves fewer than `minimum` items: when it would, the photos stay and
    the run log says which carry text or an intrusion."""
    from .credits import sha256_file
    import json as _json
    if not config.PICTURE_VISION_CHECK or not photos:
        return list(photos)
    folder = Path(photos[0]).parent
    cache_path = folder / TEXT_SCREEN_FILE
    try:
        cache = _json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, ValueError):
        cache = {}
    changed, down = False, False

    def verdict_for(p: Path) -> Optional[Dict[str, Any]]:
        nonlocal changed, down
        digest = sha256_file(p)
        verdict = cache.get(digest)
        if (verdict is None or verdict.get("screen", 1) < TEXT_SCREEN_VERSION) and not down:
            seen = _text_verdict(picture_look(p, subject, TEXT_SCREEN_SIZE))
            if seen is None:
                # Not looked at: an older verdict stands, or none (kept, asked again next run). The rest of this
                # pool is not asked now: each look would wait out its retry (a screen-version bump asks them all).
                down = True
                log.info("Picture screen could not run for %s; the other photos keep their verdicts this run.", p.name)
                return verdict
            verdict = {"text": seen["text"], "reason": seen["reason"], "edge": seen["edge"], "box": seen["box"],
                       "screen": TEXT_SCREEN_VERSION}
            if seen["object"]:   # written only when there is one: the record stays as it was for the rest
                verdict.update(object=seen["object"], object_edge=seen["object_edge"], object_box=seen["object_box"])
            cache[digest], changed = verdict, True
        return verdict

    flagged, cropped = [], {}
    for p in photos:
        p = Path(p)
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or not p.is_file():
            continue
        verdict = verdict_for(p)
        if not verdict or not (verdict.get("text") or verdict.get("object")):
            continue
        use, _, why = clear_edges(p, verdict, verdict_for)
        if use is not None:
            cropped[p] = use
        else:
            flagged.append((p, why))
    if changed:
        try:
            with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_json.dumps(cache, indent=1))
        except OSError as e:
            log.debug("Could not save the picture-text screen: %s", e)
    out = [cropped.get(Path(p), p) for p in photos]
    kept = [p for p in out if Path(p) not in {f for f, _ in flagged}]
    for p, why in flagged:
        log.info("Photo %s: %s%s.", p.name, why, "; set aside" if len(kept) >= minimum else "; kept, too few photos otherwise")
    return kept if len(kept) >= minimum else out
