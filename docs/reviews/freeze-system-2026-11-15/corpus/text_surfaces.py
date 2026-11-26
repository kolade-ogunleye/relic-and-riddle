"""TEXT stream corpus, the surfaces beyond the first three (freeze cycle 5, review D3): each drawn text of a dev run
as the RENDERER's own call path sets it, on one code tree (base or tip). Imported by text_collect.py.

Per surface, the call path (no approximated fit; every function is the production one):

- carousel body      MultiFormatGenerator._body on the 1440 canvas (3 lines on the first / last slide, 4 inside, one
                     more line when shortened: the stack's own retry);
- carousel heading   MultiFormatGenerator._set_heading (the locked cover spec on slide 1, the content ladder inside);
                     its record is the engine's Form.record, which the slide keeps in layout.meta only
                     (record_in_memory: still_layout.json holds no heading record; the judge counts these apart);
- Story frame        MultiFormatGenerator._render_story_frame through still_layout.drawn (the frame is drawn, never
                     saved): the title fields and the body as the frame's own layout record holds them; the ground is
                     the run's recorded Story photo, else a neutral ground (the text layout reads no pixel of it:
                     checked on the runs that kept their photos);
- chapter / myth / hook card   design_cards.plan_chapter_card (with the video's set_titles, as
                     card_composition.make_all passes them) / plan_myth_fact / plan_hook_frame on signed card fields,
                     9:16 and 16:9. No dev run holds a chapter or a myth field (34 runs: 0 chapters, 0 myth fields,
                     1 statement), so the fields are the committed ones the board draws
                     (tests/test_freeze_cards_2026_11_04.py), under the pseudo-run CARD_RUN;
- graphic labels     graphics.render_timeline / render_stat_cards / render_scale, the real renders with the encoder
                     stubbed and one frame a second (the layout does not read the frame rate): the plates the render
                     hands graphics.record_contrast (its own persisted record, <clip>.contrast.json) are the drawn
                     rows; map and route place names through graphics.marker_label_lines;
- end card / end screen   end_card.layout (9:16 and 16:9) of script_engine.end_card_share(payload);
- YouTube thumbnail  VisualEngine.generate_youtube_thumbnail on the run's recorded cover photo (else the run's carousel
                     cover photo), the lines read from the plates youtube_thumbnail.render returns;
- Reel cover         VisualEngine.generate_thumbnails in the run's recorded style on its recorded hero (the runs that
                     kept a cover recipe), the title rows read from the cover's own still_layout.json entry.

Each row: {surface, text, lines, size, heading, record}. `record` is the surface's own persisted record of a line
fault, where it has one (a card's plan.meta["anchors"], a Story frame's layout entry, line_policy.drawn_record for a
carousel body), never something this collector computes on the surface's behalf.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
import sys
import tempfile
import time
from pathlib import Path

CARD_RUN = "_committed_card_fields"
CARD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key (tests/test_freeze_cards_2026_11_04)
FORMATS = (("916", 1080, 1920), ("169", 1920, 1080))


def _err(surface: str, ex: BaseException) -> dict:
    return {"surface": surface, "error": f"{type(ex).__name__}: {ex}"[:300]}


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _refusal(ex: BaseException) -> bool:
    return type(ex).__name__ in ("TextFitError", "LayoutError", "CardRefused", "GlyphRefused", "Refused",
                                 "FigureRefused", "HeadingRefused", "Declined", "OwnRecordFailed")


# ------------------------------------------------------------------------------------------------- carousel
def carousel_rows(j: dict) -> list[dict]:
    from PIL import Image, ImageDraw
    from src import multi_format as mf
    lp = sys.modules.get("src.line_policy")
    gen = mf.MultiFormatGenerator
    g = object.__new__(gen)
    d = ImageDraw.Draw(Image.new("RGB", (16, 16)))
    canvas = 1440
    px = gen._px(canvas)
    width = px(1080 - 2 * gen.MARGIN)
    slides = [s for s in (j.get("carousel_slides") or []) if isinstance(s, dict)]
    rows: list[dict] = []
    for i, s in enumerate(slides, 1):
        head = _clean(s.get("heading")).upper()
        if head:
            name = f"carousel_s{i:02d}_{'cover_' if i == 1 else ''}heading"
            try:
                if i == 1:
                    form, font = g._set_heading(d, px, head, width, True, 1.0, (150, 72), False)
                else:
                    form, font = g._set_heading(d, px, head, width, "wrap", 1.0, gen.CONTENT_HEADING, True)
                rows.append({"surface": name, "text": head, "lines": list(form.lines), "size": form.size,
                             "widths": [round(d.textlength(t, font=font), 1) for t in form.lines],
                             "record": dict(form.record), "heading": True, "record_in_memory": True})
            except Exception as ex:   # noqa: BLE001
                rows.append({"surface": name, "text": head, "refused": True, "reason": str(ex)[:200]}
                            if _refusal(ex) else _err(name, ex))
        body = _clean(s.get("body"))
        if len(body.split()) < 2:
            continue
        name = f"carousel_s{i:02d}_body"
        edge = i in (1, len(slides))
        try:
            n = 3 if edge else 4
            if lp is not None and hasattr(lp, "take_notes"):
                lp.take_notes()
            lines, font, short = g._body(d, px, body, width, n, gen.BODY_SPEC[0], gen.BODY_SPEC[0], trial=True)
            if short:
                n += 1
                lines, font, short = g._body(d, px, body, width, n, gen.BODY_SPEC[0], gen.BODY_SPEC[0], trial=True)
            lines = [str(ln) for ln in lines]
            rec = None
            if lp is not None and hasattr(lp, "drawn_record"):
                rec = lp.drawn_record(lines, gen.BODY_MEASURE)
            rows.append({"surface": name, "text": body, "lines": lines, "size": font.size, "width": width,
                         "max_lines": n, "canvas": canvas, "chars": gen.BODY_MEASURE, "short": bool(short),
                         "body": True, "record": rec})
        except Exception as ex:   # noqa: BLE001
            rows.append({"surface": name, "text": body, "refused": True, "reason": str(ex)[:200]}
                        if _refusal(ex) else _err(name, ex))
    return rows


# ---------------------------------------------------------------------------------------------------- Story
_GROUND: dict[str, Path] = {}


def _neutral_ground() -> Path:
    if "p" not in _GROUND:
        from PIL import Image
        p = Path(tempfile.mkdtemp(prefix="text-c5-")) / "neutral_ground.jpg"
        Image.new("RGB", (1620, 2880), (96, 92, 86)).save(p, quality=90)
        _GROUND["p"] = p
    return _GROUND["p"]


def story_rows(run: Path, j: dict) -> list[dict]:
    from src import multi_format as mf
    from src import still_layout
    slides = [s for s in (j.get("story_slides") or []) if isinstance(s, dict)]
    if not slides:
        return []
    poll = j.get("poll") or {}
    recipes = {}
    side = run / "stories" / "still_layout.json"
    if side.is_file():
        try:
            for e in json.loads(side.read_text(encoding="utf-8")).values():
                r = e.get("recipe") or {}
                if r.get("photo") and Path(r["photo"]).is_file():
                    recipes[int(r.get("index", -1))] = Path(r["photo"])
        except (OSError, ValueError):
            pass
    gen = mf.MultiFormatGenerator()
    title_px = mf.story_title_px(slides, poll=poll)
    seen: list[tuple[str, list[str]]] = []
    real = {k: getattr(mf, k) for k in ("probe_text", "fit_text")}

    def spy(fn):
        def run_(d, text, *a, **k):
            got = fn(d, text, *a, **k)
            seen.append((_clean(text), [str(x) for x in got[0]]))
            return got
        return run_

    rows: list[dict] = []
    for k, fn in real.items():
        setattr(mf, k, spy(fn))
    try:
        for i, slide in enumerate(slides):
            name = f"story_{i + 1:02d}"
            ground = recipes.get(i) or _neutral_ground()
            del seen[:]
            try:
                _im, lay, attempts = still_layout.drawn(
                    lambda tight, i=i, slide=slide: gen._render_story_frame(
                        ground, copy.deepcopy(slide), i, len(slides), poll, tight=tight, title_px=title_px))
            except Exception as ex:   # noqa: BLE001
                text = _clean(" ".join(str(slide.get(k) or "") for k in ("line1", "line2", "body")))
                rows.append({"surface": name + "_frame", "text": text, "refused": True, "reason": str(ex)[:200]}
                            if _refusal(ex) else _err(name + "_frame", ex))
                continue
            meta = lay.meta if isinstance(getattr(lay, "meta", None), dict) else {}
            rec = {k: meta[k] for k in ("line_faults", "type_fallbacks", "heading", "title_form") if meta.get(k)}
            rec["shortened"] = list(lay.shortened)
            how = "recorded photo" if i in recipes else "neutral ground"
            fields: list[list[str]] = []
            for field in ("title_1", "title_2"):
                got = [str(e.get("text")) for e in lay.elements if e.get("name") == field and e.get("text")]
                if got:
                    fields.append(got)
            lines = [ln for f in fields for ln in f]
            if lines:
                rows.append({"surface": name + "_title", "text": " ".join(lines), "lines": lines,
                             "fields": [len(f) for f in fields], "size": title_px, "heading": True,
                             "authored_seam": len(fields) > 1, "record": rec, "ground": how,
                             "attempts": attempts})
            body = _clean(meta.get("body_drawn") or slide.get("body"))
            b_lines = next((ls for t, ls in reversed(seen) if _clean(" ".join(ls)) == body), None) or next(
                (ls for t, ls in reversed(seen) if t == body), None)
            if body and b_lines:
                rows.append({"surface": name + "_body", "text": body, "lines": b_lines, "size": meta.get("body_px"),
                             "width": mf.story_body_width(), "record": rec, "ground": how, "story_body": True})
    finally:
        for k, fn in real.items():
            setattr(mf, k, fn)
    return rows


# ---------------------------------------------------------------------------------------------------- cards
def _card_lines(plan, group: str) -> list[str]:
    dt = plan.drawn_text
    keys = [k for k in dt if k.startswith(group + "_") and k[len(group) + 1:].isdigit()]
    return [str(dt[k]) for k in sorted(keys, key=lambda n: int(n[len(group) + 1:]))]


def _card_record(plan, dc) -> dict:
    """The card's PERSISTED record of its line faults (<clip>.design.json, design_cards._design_record) and its
    sizes. The plan's in-memory anchors (conjunction_end_last_resort) are kept apart: a note no file holds is no
    record."""
    anchors = (plan.meta or {}).get("anchors") or {}
    rec = {k: anchors[k] for k in ("title_px", "px", "myth_px", "chapter_set") if k in anchors}
    memory = sorted(k for k, v in anchors.items() if k.endswith("last_resort") and v)
    if memory:
        rec["in_memory_only"] = memory
    try:
        design = dc._design_record(plan, "")
    except Exception:   # noqa: BLE001 - a tree whose record needs the render: nothing persisted is read
        design = {}
    if design.get("line_faults"):
        rec["line_faults"] = design["line_faults"]
    return rec


def card_rows() -> list[dict]:
    """The committed card fields (the board's own cards) through the cards' plan functions, both formats."""
    from src import config
    config.FACT_RECORD_KEY = CARD_KEY
    import tests.test_design_cards_2026_11_03 as T
    from src import design_cards as dc
    from tests import test_freeze_cards_2026_11_04 as fc
    rows: list[dict] = []

    def add(surface: str, make, groups) -> None:
        try:
            plan = make()
        except Exception as ex:   # noqa: BLE001
            rows.append({"surface": surface, "text": surface, "refused": True, "reason": str(ex)[:200]}
                        if _refusal(ex) else _err(surface, ex))
            return
        if plan is None:
            return
        rec = _card_record(plan, dc)
        for group in groups:
            lines = _card_lines(plan, group)
            if not lines:
                continue
            size = rec.get({"title": "title_px", "myth": "myth_px", "fact": "px"}.get(group, ""))
            rows.append({"surface": f"{surface}_{group}", "text": " ".join(lines), "lines": lines, "size": size,
                         "heading": True, "card": True, "record": rec})

    acts = tuple(fc.RICHAT_ACTS)
    richat = fc.real_sheet("richat_structure_sahara_eye")
    myths = {"richat_long": lambda size: fc.richat_myth(richat, size=size),
             "richat_short": lambda size: fc.richat_myth(richat, correction=fc.RICHAT_SHORT, size=size),
             "richat_long_nodate": lambda size: fc.richat_myth(richat, correction=fc.RICHAT_LONG_NODATE, size=size),
             "richat_longest": lambda size: fc.richat_myth(richat, correction=fc.RICHAT_LONGEST[1],
                                                           myth_text=fc.RICHAT_LONGEST[0], size=size),
             "long_et": lambda size: fc.plan_myth(fc.LONG_ET, size=size),
             "longest916": lambda size: fc.plan_myth(fc.LONGEST916, size=size),
             "longest169": lambda size: fc.plan_myth(fc.LONGEST169, size=size),
             "short": lambda size: fc.plan_myth(fc.SHORT, size=size)}
    for tag, w, h in FORMATS:
        size = (w, h)
        for n, title in enumerate(acts, 1):
            def chapter(n=n, title=title, size=size):
                spec = T.chapter(sheet=fc.SHEET, number=n, total=len(acts), title=title)
                if any(f.name == "set_titles" for f in dataclasses.fields(spec)):
                    spec = dataclasses.replace(spec, set_titles=acts)   # as card_composition.make_all passes them
                return dc.plan_chapter_card(spec, fc.SHEET, *size)
            add(f"card_chapter_act{n}_{tag}", chapter, ("title",))
        for name, make in myths.items():
            add(f"card_myth_{name}_{tag}", lambda make=make, size=size: make(size), ("myth", "fact"))
    return rows


# ------------------------------------------------------------------------------------------------- graphics
def _plate_groups(plates) -> list[tuple[str, list[str]]]:
    """The render's plates as texts: consecutive plates of one name are one text's rows (top to bottom)."""
    out: list[tuple[str, list[tuple[float, str]]]] = []
    for name, plate in plates:
        if plate is None or getattr(plate, "empty", False):
            continue
        text = _clean(getattr(plate, "text", ""))
        if not text:
            continue
        box = list(getattr(plate, "box", (0, 0, 0, 0)))
        if out and out[-1][0] == name:
            out[-1][1].append((box[1], text))
        else:
            out.append((name, [(box[1], text)]))
    return [(name, [t for _y, t in sorted(rows, key=lambda r: r[0])]) for name, rows in out]


def graphic_rows(run: Path, j: dict) -> list[dict]:
    from src import graphics
    rows: list[dict] = []
    got: list = []
    real = {k: getattr(graphics, k) for k in ("_encode", "record_contrast", "VIDEO_FPS")}

    def encode(frames, out, n, width=None, height=None, **_k):
        for _ in frames:
            pass
        return Path(out)

    def record(out, frame, plates, *a, **k):
        got.extend(plates)
        return {}

    graphics._encode, graphics.record_contrast, graphics.VIDEO_FPS = encode, record, 1
    work = Path(tempfile.mkdtemp(prefix="text-c5-g-"))
    try:
        jobs = []
        if j.get("timeline"):
            jobs.append(("timeline", lambda o, w, h: graphics.render_timeline(copy.deepcopy(j["timeline"]), o,
                                                                              width=w, height=h)))
        if j.get("stat_cards"):
            jobs.append(("numbers", lambda o, w, h: graphics.render_stat_cards(
                copy.deepcopy(j["stat_cards"]), o, None, width=w, height=h,
                narration=j.get("narration_script") or "")))
        if j.get("scale_fact"):
            jobs.append(("scale", lambda o, w, h: graphics.render_scale(copy.deepcopy(j["scale_fact"]), o,
                                                                        width=w, height=h)))
        for kind, make in jobs:
            for tag, w, h in FORMATS:
                surface = f"graphic_{kind}_{tag}"
                del got[:]
                try:
                    made = make(work / f"{kind}_{tag}.mp4", w, h)
                except Exception as ex:   # noqa: BLE001
                    rows.append({"surface": surface, "text": kind, "refused": True, "reason": str(ex)[:200]}
                                if _refusal(ex) else _err(surface, ex))
                    continue
                if made is None:
                    rows.append({"surface": surface, "text": kind, "refused": True,
                                 "reason": "the renderer drew nothing"})
                    continue
                for k, (name, lines) in enumerate(_plate_groups(got)):
                    if re.match(r"^(label|note|caption|title|heading|name|referent|subject|event|kicker)", name):
                        rows.append({"surface": f"{surface}_{name}", "text": " ".join(lines), "lines": lines,
                                     "size": None, "label": True, "record": None, "at": k})
        # place names: the map's site and the route's stops, through the marker label's own line setter
        places = []
        lineage = run / "graphics" / "graphic_map.mp4.lineage.json"
        if lineage.is_file():
            try:
                inp = (json.loads(lineage.read_text(encoding="utf-8")).get("settings") or {}).get("inputs") or {}
                place = inp.get("place") or {}
                for key in ("label", "name", "place"):
                    if isinstance(place, dict) and place.get(key):
                        places.append(("map_site", str(place[key])))
                        break
            except (OSError, ValueError):
                pass
        for k, stop in enumerate(j.get("route") or [], 1):
            if isinstance(stop, dict) and stop.get("place"):
                places.append((f"route_stop{k}", str(stop["place"])))
        for name, text in places:
            for tag, w, h in FORMATS:
                surface = f"graphic_{name}_{tag}"
                try:
                    lines, font = graphics.marker_label_lines(text, w, h)
                    rows.append({"surface": surface, "text": _clean(text).upper(), "lines": [str(x) for x in lines],
                                 "size": getattr(font, "size", None), "label": True, "record": None})
                except Exception as ex:   # noqa: BLE001
                    rows.append({"surface": surface, "text": _clean(text), "refused": True, "reason": str(ex)[:200]}
                                if _refusal(ex) else _err(surface, ex))
    finally:
        for k, v in real.items():
            setattr(graphics, k, v)
    return rows


# ------------------------------------------------------------------------------------------------- end card
def endcard_rows(j: dict) -> list[dict]:
    from src import end_card
    from src.script_engine import end_card_share
    rows: list[dict] = []
    try:
        share, fallback = end_card_share(copy.deepcopy(j))
    except Exception as ex:   # noqa: BLE001
        return [_err("endcard_share", ex)]
    for tag, w, h in FORMATS:
        surface = f"endcard_{tag}_share"
        try:
            lay = end_card.layout(share, w, h)
            lines = [" ".join(ln) for ln in lay["lines"]]
            rows.append({"surface": surface, "text": " ".join(lines), "lines": lines,
                         "size": getattr(lay.get("share_font"), "size", None), "endcard": True,
                         "fallback_line": bool(fallback),
                         "record": {k: lay[k] for k in ("line_faults", "line_end_last_resort") if lay.get(k)}})
        except Exception as ex:   # noqa: BLE001
            rows.append({"surface": surface, "text": share, "refused": True, "reason": str(ex)[:200]}
                        if _refusal(ex) else _err(surface, ex))
    return rows


# -------------------------------------------------------------------------------------------------- YouTube
def _run_photo(run: Path) -> tuple[Path | None, str]:
    for side, key, how in ((run / "still_layout.json", "thumbnail_cover.jpg", "recorded cover photo"),
                           (run / "carousel" / "still_layout.json", "carousel_slide_01.jpg",
                            "the run's carousel cover photo (no cover recipe kept)")):
        if not side.is_file():
            continue
        try:
            rec = (json.loads(side.read_text(encoding="utf-8")).get(key) or {}).get("recipe") or {}
        except (OSError, ValueError):
            continue
        photo = rec.get("photo") or rec.get("hero")
        if photo and Path(photo).is_file():
            return Path(photo), how
    return None, ""


def youtube_rows(run: Path, j: dict) -> list[dict]:
    from src import youtube_thumbnail
    from src.visual_engine import VisualEngine
    hero, how = _run_photo(run)
    if hero is None:
        return []
    kept: list = []
    real = youtube_thumbnail.render

    def spy(*a, **k):
        got = real(*a, **k)
        kept.append((got[1], dict(k.get("meta") or {})))
        return got

    youtube_thumbnail.render = spy
    surface = "youtube_headline"
    work = Path(tempfile.mkdtemp(prefix="text-c5-yt-"))
    try:
        VisualEngine().generate_youtube_thumbnail(hero, copy.deepcopy(j), work / "yt.jpg")
    except Exception as ex:   # noqa: BLE001
        th = j.get("thumbnail") or {}
        text = _clean(" ".join(str(th.get(k) or "") for k in ("line1", "line2")))
        return [{"surface": surface, "text": text, "refused": True, "reason": str(ex)[:200], "ground": how}
                if _refusal(ex) else {**_err(surface, ex), "ground": how}]
    finally:
        youtube_thumbnail.render = real
    if not kept:
        return []
    plates, meta = kept[-1]
    rows: list[dict] = []
    by: dict[str, list[tuple[float, float, str]]] = {}
    for name, plate in _named(plates):
        text = _clean(getattr(plate, "text", ""))
        if text and re.match(r"^(line|title|headline|tagline)", name):
            box = list(getattr(plate, "box", (0, 0, 0, 0)))
            by.setdefault("headline" if name.startswith(("line", "headline")) else name.split("_")[0], []).append(
                (round(box[1] / 8), box[0], text))
    rec = {k: meta[k] for k in ("lockup", "title_form", "headline_dropped", "headline_fragment",
                                "headline_lines_dropped", "headline_pair_kept") if meta.get(k)}
    for group, parts in by.items():
        ys: dict[float, list[tuple[float, str]]] = {}
        for y, x, text in parts:
            key = next((k for k in ys if abs(k - y) <= 2), y)
            ys.setdefault(key, []).append((x, text))
        lines = [" ".join(t for _x, t in sorted(v)) for _y, v in sorted(ys.items())]
        rows.append({"surface": f"youtube_{group}", "text": " ".join(lines), "lines": lines, "size": None,
                     "heading": True, "record": rec, "ground": how, "youtube": True})
    return rows


def cover_rows(run: Path, j: dict) -> list[dict]:
    """The Reel cover as VisualEngine.generate_thumbnails draws it, for the runs that kept their cover recipe (hero
    photo and style): the title rows from the cover's own still_layout.json entry, written to a scratch folder."""
    side = run / "still_layout.json"
    if not side.is_file():
        return []
    try:
        recipe = (json.loads(side.read_text(encoding="utf-8")).get("thumbnail_cover.jpg") or {}).get("recipe") or {}
    except (OSError, ValueError):
        return []
    hero = recipe.get("hero") or recipe.get("photo")
    if not hero or not Path(hero).is_file():
        return []
    from src.visual_engine import VisualEngine
    work = Path(tempfile.mkdtemp(prefix="text-c5-cover-"))
    surface = "cover_reel_title"
    th = j.get("thumbnail") or {}
    text = _clean(" ".join(str(th.get(k) or "") for k in ("line1", "line2"))).upper()
    try:
        VisualEngine().generate_thumbnails(Path(hero), copy.deepcopy(j), work, style=recipe.get("style") or "badge",
                                           variants=False)
        entry = json.loads((work / "still_layout.json").read_text(encoding="utf-8"))["thumbnail_cover.jpg"]
    except Exception as ex:   # noqa: BLE001
        return [{"surface": surface, "text": text, "refused": True, "reason": str(ex)[:200]}
                if _refusal(ex) else _err(surface, ex)]
    rows_: dict[int, list[tuple[int, str]]] = {}
    for e in entry.get("elements") or []:
        name, t = str(e.get("name") or ""), _clean(e.get("text"))
        if t and re.match(r"^(title|line|headline|heading)", name):
            y = e["box"][3]
            key = next((k for k in rows_ if abs(k - y) <= 12), y)
            rows_.setdefault(key, []).append((e["box"][0], t))
    lines = [" ".join(t for _x, t in sorted(v)) for _y, v in sorted(rows_.items())]
    if not lines:
        return []
    rec = {k: entry[k] for k in ("line_faults", "type_fallbacks", "heading", "lockup") if entry.get(k)}
    rec["shortened"] = entry.get("shortened") or []
    return [{"surface": surface, "text": " ".join(lines), "lines": lines, "size": None, "heading": True,
             "record": rec, "style": recipe.get("style"), "ground": "recorded cover photo"}]


def _named(plates):
    for item in plates or []:
        if isinstance(item, (tuple, list)) and len(item) >= 2 and isinstance(item[0], str):
            if isinstance(item[1], (list, tuple)):
                for p in item[1]:
                    yield item[0], p
            else:
                yield item[0], item[1]


SURFACES = ("carousel", "story", "graphics", "endcard", "youtube", "cards")


def rows_of_run(run: Path, j: dict, want) -> list[dict]:
    rows: list[dict] = []
    for name, make in (("carousel", lambda: carousel_rows(j)), ("story", lambda: story_rows(run, j)),
                       ("graphics", lambda: graphic_rows(run, j)), ("endcard", lambda: endcard_rows(j)),
                       ("youtube", lambda: youtube_rows(run, j) + cover_rows(run, j))):
        if name not in want:
            continue
        t0 = time.time()
        try:
            rows += make()
        except Exception as ex:   # noqa: BLE001
            rows.append(_err(name, ex))
        print(f"   {name} {time.time() - t0:.1f} s", file=sys.stderr, flush=True)
    return rows
