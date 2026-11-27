"""The catalogue's carousel decks drawn by production's own entry (cycle 5, review item 5).

The board's social job (render_surfaces.py) used to redraw each recorded slide through _render_carousel_slide with the
run's saved recipe. That replay is not what the tree ships: no deck kicker set, the run's old lead unit, the recorded
slide kind where today's deck draws a diagram slide, the recorded heading frame and no window for a repeated
photograph. Here both copies of a deck are drawn as src/carousel_lazy.draw draws them at the carousel's slot:

    MultiFormatGenerator().generate_carousel_sequence(stills, payload, <work>, graphics=<the run's posters>,
                                                      strict_match=True)             # Instagram
    ... the same with facebook=True                                                  # Facebook

and the board shows the files that call delivers (the JPEG production writes, decoded).

The one input production does not keep in the run folder is the photo match: at the slot it is a vision call. The
board never makes it (no paid API, no network). The run's own record of its match (<run>/carousel_photos.json: the
photo pool and the photograph per slide) is given to the generator as the kept match it reads back, so the picker
(deck_photo_guard) and everything after it are the tree's. When that record cannot stand in (no record, a photograph
no longer on disk, another slide count, an AI-made picture among them) the deck is NOT driven: NotDriven says which
input is missing in which run, and the caller draws the replay with a note on the tile. The matcher itself is blocked
while a deck is drawn, so a path that would reach it stops here instead of calling out.

Nothing is written outside `work`. Nothing here changes what src draws.
"""
from __future__ import annotations

import contextlib
import copy as _copy
import inspect
import json
import traceback
from pathlib import Path
from typing import Any

PATH = "multi_format.generate_carousel_sequence"
REPLAY = "replay of the run's saved recipe (multi_format._render_carousel_slide)"
# folder under the output dir, the surface name's kind, facebook=, the surface production names
COPIES = (("carousel", "carousel", False, "carousel"), ("carousel_fb", "fb", True, "Facebook carousel"))


class NotDriven(RuntimeError):
    """The run's saved data cannot drive production's path: str(e) names the missing input and the run."""


@contextlib.contextmanager
def no_vision():
    """The photo matcher (MultiFormatGenerator._match_photos: scene_planner's vision planner, a paid call) is not run
    while a deck is drawn: a path that reaches it raises NotDriven, as the review's own production draw blocked it."""
    from src.multi_format import MultiFormatGenerator
    real = MultiFormatGenerator._match_photos

    def blocked(self, cards, stills, strict=False):
        raise NotDriven("the kept photo match was not accepted and the photo matcher (a paid vision call) is not run "
                        "on the board")
    MultiFormatGenerator._match_photos = blocked
    try:
        yield
    finally:
        MultiFormatGenerator._match_photos = real


def card_key(cards: list[dict[str, Any]]) -> list[str]:
    """The cards' part of carousel_photos.json's key, as multi_format._carousel_photos builds it."""
    return [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards]


def photo_record(run: Path, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """{"pool": the run's photo pool, "record": the kept match generate_carousel_sequence reads back for `cards`,
    "own_copy": whether `cards` are the run's own}: the run's carousel_photos.json with its key rebuilt for the cards
    drawn (a copy set's words keep the run's photographs), or NotDriven with the reason."""
    from src.ai_label import is_ai_picture, real_stills
    from src.multi_format import CAROUSEL_PHOTOS
    run = Path(run)
    rec = run / CAROUSEL_PHOTOS
    if not rec.is_file():
        raise NotDriven(f"{run.name} has no {CAROUSEL_PHOTOS} (the run's photo match); matching its slides again is "
                        "a paid vision call")
    try:
        kept = json.loads(rec.read_text(encoding="utf-8"))
    except ValueError as e:
        raise NotDriven(f"{run.name}: {CAROUSEL_PHOTOS} is unreadable ({e})") from e
    pool = [Path(s) for s in (kept.get("key") or {}).get("stills") or []]
    photos = [Path(p) for p in kept.get("photos") or []]
    if not pool:
        raise NotDriven(f"{run.name}: {CAROUSEL_PHOTOS} records no photo pool (key.stills)")
    missing = sorted({p.name for p in pool + photos if not p.is_file()})
    if missing:
        raise NotDriven(f"{run.name}: {len(missing)} photograph(s) of its recorded match are no longer on disk "
                        f"({', '.join(missing[:4])})")
    if len(photos) != len(cards):
        raise NotDriven(f"{run.name}: its recorded match holds {len(photos)} photograph(s) for {len(cards)} slide(s)")
    made = sorted({p.name for p in photos if is_ai_picture(p)})
    if made:
        raise NotDriven(f"{run.name}: its recorded match shows an AI-made picture ({', '.join(made[:4])}); production "
                        "matches the slides again")
    stills, _left = real_stills(pool)
    key = {"cards": card_key(cards), "stills": [str(Path(p)) for p in stills]}
    record = {"key": key, "photos": [str(p) for p in photos],
              **{k: kept[k] for k in ("matched", "choice", "rule") if k in kept}}
    return {"pool": pool, "record": record, "own_copy": (kept.get("key") or {}).get("cards") == key["cards"]}


def _refused(e: BaseException) -> dict[str, str]:
    """render_surfaces.py's one refusal record shape."""
    return {"refused": str(getattr(e, "reason", None) or e)[:200], "class": type(e).__name__,
            "code": str(getattr(e, "code", "") or "")}


def _ledger() -> list[dict[str, Any]]:
    try:
        from src.refusal import omissions
    except ImportError:
        return []
    return omissions()


def _rgb(p: Path):
    from PIL import Image
    with Image.open(p) as im:
        return im.convert("RGB")


def stable_rows(rows: list[dict[str, Any]], work: Path) -> None:
    """A drawing's omission rows name the drawing they belong to by its path (multi_format.record_drawing_omissions),
    and the board draws into a temporary folder: the path is rewritten, in place, relative to that folder
    (graphics_redrawn/<brand + code key>/graphic_<kind>.jpg), so the record is the same on every render of the same
    tree and names no machine folder. Done once the deck is drawn (the generator reads the path while it draws)."""
    for r in rows:
        if isinstance(r, dict) and r.get("drawing"):
            try:
                r["drawing"] = Path(str(r["drawing"])).resolve().relative_to(Path(work).resolve()).as_posix()
            except (OSError, ValueError):
                r["drawing"] = Path(str(r["drawing"])).name


def recorded_recipes(run: Path, folder: str) -> dict[str, dict[str, Any]]:
    """{frame file name: the run's recorded recipe} of one copy ({} when the run kept none)."""
    p = Path(run) / folder / "still_layout.json"
    if not p.is_file():
        return {}
    try:
        recs = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {name: (v.get("recipe") or {}) for name, v in recs.items() if isinstance(v, dict)}


def slide_rows(rows: list[dict[str, Any]], diagram_stems: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Omission-ledger rows of a deck -> the slide they are drawn into: a part the diagram slide's drawing left out
    (multi_format.DIAGRAM_SLIDE_SURFACE) and a comparator line left off it belong to the deck's diagram slide; every
    other row (a dropped slide has no surface) stays with the job. Each row once per slide."""
    try:
        from src.multi_format import COMPARATOR_RULE, DIAGRAM_SLIDE_SURFACE
    except ImportError:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        mine = str(r.get("surface", "")).startswith(DIAGRAM_SLIDE_SURFACE) or r.get("rule") == COMPARATOR_RULE
        for stem in diagram_stems if mine else []:
            if r not in out.setdefault(stem, []):
                out[stem].append(dict(r))
    return out


def production_deck(run: Path, payload: dict[str, Any], work: Path, generator: Any = None) -> dict[str, Any]:
    """Both copies of `run`'s deck drawn by generate_carousel_sequence on `payload` (the run's own, or a copy set's
    words on it) into `work`, as carousel_lazy.draw calls it. NotDriven when the run's saved match cannot stand in
    for the vision match (photo_record).

    Returns {"own_copy", "choice": the picker's reason per slide (carousel_photos.json "choice" as the tree left it),
    "copies": {kind: {"slides": [{"stem", "file", "image", "index", "archetype" (as drawn), "routed" (today's
    archetype routing of the card, before a drawing takes the slide), "photo", "masthead", "recorded", "omissions",
    and on the Facebook copy "facebook_post": its place in the multi-photo post or None}], "held": None}} or
    {"held": {"refused" ...} | {"error": traceback}, "stems": the run's recorded frame stems}}. The Facebook copy
    also carries "post": the files multi_format.facebook_pick posts, in order (the publisher's own pick, over the
    copy as drawn). A copy production would not ship (the Instagram copy refused or failed: the carousel is held and
    no Facebook copy is drawn; the Facebook copy refused or failed: Facebook gets the Instagram slides) is `held`,
    never drawn another way."""
    from src import multi_format as mf
    from src import still_layout
    try:
        from src.font_engine import on_surface
    except ImportError:   # a tree before the glyph ledger: the call itself
        def on_surface(_where, fn, *a, **k):
            return fn(*a, **k)
    try:
        from src.refusal import LayoutError
    except ImportError:
        LayoutError = ValueError
    run, work = Path(run), Path(work)
    payload = _copy.deepcopy(payload)   # the generator records on it (layout_holds); the caller's stays as it was
    got = photo_record(run, payload["carousel_slides"])
    work.mkdir(parents=True, exist_ok=True)
    (work / mf.CAROUSEL_PHOTOS).write_text(json.dumps(got["record"], indent=1, ensure_ascii=False), encoding="utf-8",
                                           newline="\n")
    gen = generator if generator is not None else mf.MultiFormatGenerator()
    try:   # today's routing of the run's cards (a drawing may then take one slide: the slide's own `archetype`)
        routed = list(mf.MultiFormatGenerator.sequence_archetypes(payload["carousel_slides"]))
    except Exception:  # noqa: BLE001  a tree without the routing: nothing is said about it
        routed = []
    kw: dict[str, Any] = {"graphics": sorted((run / "graphics").glob("graphic_*.jpg"))}   # as carousel_lazy.draw
    if "strict_match" in inspect.signature(gen.generate_carousel_sequence).parameters:
        kw["strict_match"] = True
    metas: dict[tuple[str, str], dict[str, Any]] = {}   # (folder, frame) -> the delivered frame's layout meta
    real_save = still_layout.save

    def spy(still, layout, *a, **k):
        still = Path(still)
        metas[(still.parent.name, still.name)] = dict(getattr(layout, "meta", None) or {})
        return real_save(still, layout, *a, **k)
    out: dict[str, Any] = {"own_copy": got["own_copy"], "copies": {}, "path": PATH}
    rows_by_copy: dict[str, list[dict[str, Any]]] = {}
    held_first: dict[str, Any] | None = None
    still_layout.save = spy
    try:
        with no_vision():
            for folder, kind, fb, surface in COPIES:
                stems = sorted(Path(n).stem for n in recorded_recipes(run, folder))
                if held_first is not None:   # carousel_lazy.draw returns before the Facebook copy
                    out["copies"][kind] = {"slides": [], "stems": stems, "held": {
                        "refused": "the Instagram copy is held, so production draws no Facebook copy",
                        "class": "CarouselHeld", "code": "instagram_copy_held"}}
                    continue
                n0 = len(_ledger())
                try:
                    paths = on_surface(surface, gen.generate_carousel_sequence, list(got["pool"]), payload, work,
                                       facebook=fb, **kw)
                except NotDriven:
                    raise
                except LayoutError as e:
                    held = _refused(e)
                    paths = None
                except Exception:  # noqa: BLE001  production holds the carousel on any failure to draw (carousel_lazy)
                    held = {"error": traceback.format_exc()[-1200:]}
                    paths = None
                stable_rows(_ledger()[n0:], work)   # the ledger's own rows (the job's record) and the copies below
                rows_by_copy[kind] = [dict(r) for r in _ledger()[n0:]]
                if not paths:
                    held = held if paths is None else {"refused": "no slide was drawn", "class": "CarouselHeld",
                                                       "code": "no_slide"}
                    out["copies"][kind] = {"slides": [], "stems": stems, "held": held}
                    if not fb:
                        held_first = held
                    continue
                layouts = json.loads((work / folder / "still_layout.json").read_text(encoding="utf-8"))
                recorded = recorded_recipes(run, folder)
                slides = []
                for p in paths:
                    p = Path(p)
                    entry = layouts.get(p.name) or {}
                    r = entry.get("recipe") or {}
                    meta = metas.get((folder, p.name)) or {}
                    k = r.get("index")
                    slides.append({"stem": p.stem, "file": p, "image": _rgb(p), "index": k,
                                   "archetype": r.get("archetype"), "photo": r.get("photo"),
                                   # only while the deck kept every card (a dropped slide renumbers the rest)
                                   "routed": routed[int(k)] if k is not None and len(routed) == len(paths) else None,
                                   "masthead": entry["masthead"] if entry.get("masthead") is not None
                                   else meta.get("masthead"),
                                   "recorded": recorded.get(p.name) or {}, "omissions": []})
                out["copies"][kind] = {"slides": slides, "stems": stems, "held": None}
                if fb:   # what the publisher posts of this copy (publisher.py: facebook_pick over the copy's slides)
                    try:
                        post = [Path(x).name for x in mf.facebook_pick([str(p) for p in paths])]
                    except Exception:  # noqa: BLE001  recorded as unknown; the board then says the pick is not known
                        post = None
                    out["copies"][kind]["post"] = post
                    for s in slides:
                        name = Path(s["file"]).name
                        s["facebook_post"] = "unknown" if post is None else (
                            post.index(name) + 1 if name in post else None)
    finally:
        still_layout.save = real_save
    # the drawing's rows are recorded once for the deck's two copies (record_drawing_omissions): each copy's diagram
    # slide takes them; a copy's own rows (its comparator line) stay on that copy
    shared = [r for rows in rows_by_copy.values() for r in rows]
    for kind, c in out["copies"].items():
        diagram = [s["stem"] for s in c["slides"] if s["archetype"] == "diagram"]
        try:
            from src.multi_format import DIAGRAM_SLIDE_SURFACE
            rows = rows_by_copy.get(kind, []) + [r for r in shared
                                                 if str(r.get("surface", "")).startswith(DIAGRAM_SLIDE_SURFACE)]
        except ImportError:
            rows = rows_by_copy.get(kind, [])
        placed = slide_rows(rows, diagram)
        for s in c["slides"]:
            s["omissions"] = placed.get(s["stem"], [])
    try:
        after = json.loads((work / mf.CAROUSEL_PHOTOS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        after = {}
    out["choice"] = [dict(c) for c in after.get("choice") or [] if isinstance(c, dict)]
    out["photos"] = [str(p) for p in after.get("photos") or []]
    return out


def choice_record(deck: dict[str, Any], slide: dict[str, Any]) -> dict[str, Any]:
    """render_surfaces.py's photo_choice record for one slide of a production-drawn deck: the picker's reason for the
    slide (by its 1-based slide number, else its index), the photograph the run recorded there, the archetype the
    slide is drawn with and whether that is a drawing in the photograph's place, the path that drew it, and for a
    Facebook slide its place in the multi-photo post (None: not posted; "unknown": the pick could not be made)."""
    index = slide.get("index")
    rows = deck.get("choice") or []
    row = next((c for c in rows if index is not None and c.get("slide") == int(index) + 1), None)
    if row is None and index is not None and int(index) < len(rows) and "slide" not in rows[int(index)]:
        row = rows[int(index)]
    rec = {k: (str(v) if isinstance(v, Path) else v) for k, v in (row or {}).items()}
    rec.update({"recorded": str((slide.get("recorded") or {}).get("photo") or ""),
                "archetype": slide.get("archetype"), "diagram": slide.get("archetype") == "diagram",
                "drawn_through": PATH})
    if "facebook_post" in slide:
        rec["facebook_post"] = slide["facebook_post"]
    return rec


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit("imported by render_surfaces.py and board_extras.py")
