"""The surfaces, each driven through its real renderer, and the case builders that feed them hostile inputs.

A case is a JSON dict {"surface", "id", "fmt", ...inputs}; build(surface, count, seed) draws them (deterministic).
drive(case, work) renders one case in `work` (a fresh run folder) and returns a Ctx: what the renderer drew (its own
records, the captured plates, the delivered images), what it was asked to draw (`expect`) and the signed payload the
figure gate reads. invariants.check(ctx) judges it.

Harness substitutions (never a drawing path): the carousel's and Story's photo matching (the scene planner's vision
call) is replaced by rotation, so no paid API or network is reached; graphics, callouts, then-and-now and the cards
hand their frames to capture's collector instead of the encoder; the end card's ground is memoised exactly as the
board's board_speed.py does (same pixels). The geo cache is a private copy (never the dev checkout's file).
"""
from __future__ import annotations

import json
import logging
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import generators as gen
import grounds as gr

SURFACES = ("cover", "youtube", "carousel_ig", "carousel_fb", "story", "captions_916", "captions_169", "badge_916",
            "badge_169", "hook", "chapter_916", "chapter_169", "myth_916", "myth_169", "callout_916", "callout_169",
            "endcard", "bridge", "endscreen", "map_916", "map_169", "route_916", "timeline_916", "timeline_169",
            "scale_916", "scale_169", "section_916", "section_169", "numbers_916", "numbers_169", "thennow_916",
            "thennow_169")
# the lane each surface belongs to (FREEZE-PLAYBOOK.md section 2)
LANE = {"cover": "B", "youtube": "B", "carousel_ig": "D", "carousel_fb": "D", "story": "D", "captions_916": "C",
        "captions_169": "C", "badge_916": "F", "badge_169": "F", "hook": "F", "chapter_916": "F", "chapter_169": "F",
        "myth_916": "F", "myth_169": "F", "callout_916": "F", "callout_169": "F", "endcard": "E", "bridge": "E",
        "endscreen": "E"}
for _s in SURFACES:
    LANE.setdefault(_s, "A")
# relative cost (seconds per case, measured on 72ac97a8): run.py scales --count by it so a slow surface runs fewer
COST = {"cover": 3, "youtube": 2, "carousel_ig": 25, "carousel_fb": 25, "story": 6, "captions_916": 0.5,
        "captions_169": 0.5, "badge_916": 0.5, "badge_169": 0.5, "hook": 4, "chapter_916": 3, "chapter_169": 3,
        "myth_916": 3, "myth_169": 3, "callout_916": 110, "callout_169": 150, "endcard": 105, "bridge": 13,
        "endscreen": 650, "map_916": 150, "map_169": 120, "route_916": 900, "timeline_916": 30, "timeline_169": 30,
        "scale_916": 50, "scale_169": 50, "section_916": 30, "section_169": 30, "numbers_916": 50, "numbers_169": 30,
        "thennow_916": 50, "thennow_169": 50}
REFUSALS = ("LayoutError",)
BENIGN = ("len_2", "len_3", "len_3", "len_5", "apostrophe", "hyphen", "accented", "mixed_case", "all_caps")        # the one refusal type and every subclass (checked by isinstance in drive)


@dataclass
class Ctx:
    case: dict
    work: Path
    payload: dict = field(default_factory=dict)
    expect: list = field(default_factory=list)          # (field, text) the surface must draw whole
    images: list = field(default_factory=list)          # {"name", "im", "safe", "floor", "kind"}
    records: list = field(default_factory=list)         # element dicts {"surface","name","text","box","px",...}
    recorded_drops: list = field(default_factory=list)  # what the renderer itself says it left out, and why
    problems: list = field(default_factory=list)        # the renderer's own recorded layout problems
    notes: list = field(default_factory=list)
    status: str = "pass"
    refusal: dict | None = None
    error: str | None = None
    lines: list = field(default_factory=list)           # (surface, [line texts]) for the line-end rule
    contrast_rows: list = field(default_factory=list)   # {"surface","name","value","gate"}
    figure_gate: bool = True
    clips: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ case builders
def _fmt(surface: str) -> str:
    return "169" if surface.endswith("_169") or surface in ("youtube", "endscreen") else "916"


def _signed(*texts: str) -> str:
    """A narration that signs every text (each its own sentence)."""
    return " ".join(f"{t.strip().rstrip('.')}." for t in texts if t and t.strip())


def build(surface: str, count: int, seed: int) -> list[dict]:
    rnd = random.Random(f"{surface}:{seed}")
    copies = gen.copy_cases(count * 6, seed)
    figs = gen.figure_cases(count, seed)
    grounds = gr.ground_specs(count * 4, seed)
    out = []
    benign = gen.copy_cases(count * 8, seed + 7, BENIGN)
    for i in range(count):
        # field 0 carries the case's hostile class (the first rounds walk one class per case, so every class is met
        # even at a small count); the other fields are ordinary copy, so a refusal or a failure is that class's,
        # and about one case in four carries a second hostile field elsewhere
        c = [benign[(i * 8 + k) % len(benign)] for k in range(8)]
        hostile = (i // len(gen.COPY_CLASSES)) % 6       # later rounds move the hostile copy to another field
        c[0], c[hostile] = c[hostile], copies[i % len(copies)]
        if rnd.random() < 0.25:
            c[1 + rnd.randrange(5)] = copies[(i * 6 + 3) % len(copies)]
        f = figs[i]
        g = [grounds[(i * 4 + k) % len(grounds)] for k in range(4)]
        g[0] = grounds[i % len(grounds)]
        case = {"surface": surface, "id": f"{surface}-{seed}-{i:04d}", "fmt": _fmt(surface), "copy": c,
                "figure": f, "ground": g, "rnd": rnd.randrange(1 << 30)}
        BUILD_EXTRA.get(surface.split("_")[0], lambda case, i, rnd: None)(case, i, rnd)
        case["hostile_field"] = hostile
        out.append(case)
    return out


def _cover_extra(case, i, rnd):
    from_styles = ("badge", "monument", "minimal", "depth_sandwich", "lower_third")
    case["style"] = from_styles[i % len(from_styles)]


def _scale_extra(case, i, rnd):
    case["scale"] = gen.scale_cases(i + 1, case["rnd"])[i]


def _map_extra(case, i, rnd):
    case["map"] = gen.map_cases(i + 1, case["rnd"])[i]


BUILD_EXTRA: dict[str, Callable] = {"cover": _cover_extra, "youtube": _cover_extra, "scale": _scale_extra,
                                    "map": _map_extra}


# ------------------------------------------------------------------------------------------------ helpers
def _ground(case, k: int, work: Path) -> Path:
    return gr.make(case["ground"][k % len(case["ground"])], work.parent.parent / "_grounds")


def _t(case, k: int) -> str:
    return case["copy"][k % len(case["copy"])]["text"]


def _size(case) -> tuple[int, int]:
    return (1920, 1080) if case["fmt"] == "169" else (1080, 1920)


def _boxes(w: int, h: int, kind: str):
    from src import safe_zones
    from src.graphics import get_text_box
    if kind == "graphics":
        return safe_zones.graphics_box(w, h) if h > w else get_text_box(w, h)
    if kind == "reel":
        return safe_zones.safe_box("reel", w, h) if h > w else (0, 0, w, h)
    if kind == "story":
        return safe_zones.safe_box("story", w, h)
    return (0, 0, w, h)


def _floor(w: int, h: int, role: str = "credit") -> float:
    from src.brand_system import label_floor_px
    return label_floor_px(w, h, role)


def _still_records(ctx: Ctx, rec_file: Path, surface: str) -> dict:
    """still_layout.json entries: every element, its zone, the renderer's own problems."""
    if not rec_file.is_file():
        return {}
    data = json.loads(rec_file.read_text(encoding="utf-8"))
    for still, entry in data.items():
        for e in entry.get("elements") or []:
            ctx.records.append({**e, "surface": f"{surface}/{still}", "zone": e.get("zone") or entry.get("zone"),
                                "canvas": entry.get("size")})
        for p in entry.get("problems") or []:
            ctx.problems.append(f"{surface}/{still}: {p}")
        for s in entry.get("shortened") or []:
            ctx.recorded_drops.append(f"{surface}/{still}: shortened {s}")
    return data


def _sidecar_records(ctx: Ctx, side: Path, surface: str, safe=None, w=None, h=None) -> dict | None:
    if not side.is_file():
        return None
    data = json.loads(side.read_text(encoding="utf-8"))
    gate = data.get("target")
    for e in data.get("elements") or []:
        ctx.records.append({**e, "surface": surface, "zone": list(safe) if safe else None,
                            "canvas": [w, h] if w else None})
        v = e.get("graded") if e.get("graded") is not None else e.get("drawn")
        if gate is not None:
            ctx.contrast_rows.append({"surface": surface, "name": e.get("name"), "value": v, "gate": gate,
                                      "text": e.get("text"), "planned": e.get("planned"), "drawn": e.get("drawn"),
                                      "graded": e.get("graded")})
    for d in data.get("dropped") or []:
        ctx.recorded_drops.append(f"{surface}: dropped {d}")
    if data.get("nontext_min") is not None and data.get("nontext_target") is not None:
        for r in data.get("nontext") or []:
            v = r.get("graded") if r.get("graded") is not None else r.get("drawn")
            ctx.contrast_rows.append({"surface": surface, "name": f"mark {r.get('name')}", "value": v,
                                      "gate": data["nontext_target"], "text": None, "drawn": r.get("drawn"),
                                      "graded": r.get("graded")})
    return data


def _clip(ctx: Ctx, out: Path, name: str, safe, floor) -> None:
    import capture
    rec = capture.CLIPS.get(Path(out).name)
    if rec and rec.get("last") is not None:
        ctx.images.append({"name": name, "im": rec["last"], "safe": safe, "floor": floor, "kind": "clip"})
        ctx.clips[name] = rec["frames"]


# ------------------------------------------------------------------------------------------------ drivers
def d_cover(case, ctx: Ctx):
    from PIL import Image

    from src.visual_engine import VisualEngine, cover_text_zone
    copy = {"eyebrow": _t(case, 0), "line1": _t(case, 1), "line2": _t(case, 2),
            "tagline": case["figure"]["text"] if case["rnd"] % 2 else _t(case, 3)}
    ctx.payload = {"title": f"{copy['line1']} {copy['line2']}", "thumbnail": copy, "topic_id": "stress_topic",
                   "narration_script": _signed(*copy.values(), case["figure"]["signed"]),
                   "hook_sentence": _signed(copy["line1"]), "closing_sentence": _signed(_t(case, 4))}
    ctx.expect = [(k, v) for k, v in copy.items()]
    hero = _ground(case, 0, ctx.work)
    if case["surface"] == "cover":
        reel, grid = VisualEngine().generate_thumbnails(hero, ctx.payload, ctx.work, style=case["style"])
        data = _still_records(ctx, ctx.work / "still_layout.json", "cover")
        zone = cover_text_zone()
        with Image.open(reel) as im:
            ctx.images.append({"name": "reel", "im": im.convert("RGB"), "safe": zone,
                               "floor": _floor(1080, 1920), "kind": "still"})
        with Image.open(grid) as im:
            ctx.images.append({"name": "grid", "im": im.convert("RGB"), "safe": None, "floor": None,
                               "kind": "still", "derived": True})
        for still, entry in data.items():
            for key in ("grid_hold",):
                if (entry.get("recipe") or {}).get(key):
                    ctx.notes.append(f"{still}: {key}: {entry['recipe'][key]}")
    else:
        out = VisualEngine().generate_youtube_thumbnail(hero, ctx.payload, ctx.work / "thumbnail_youtube.jpg")
        qa = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
        from src.visual_engine import youtube_cover_copy
        used = youtube_cover_copy(dict(ctx.payload))
        # the thumbnail sets ONE headline (a field of the copy, recorded as headline_from) under the masthead: that
        # field is drawn whole or its drop is recorded (headline_dropped / headline_fragment); the other fields are
        # not the thumbnail's to draw
        hf = qa.get("headline_from")
        ctx.expect = [(hf, used[hf])] if hf in used else []
        if hf and hf not in used:
            ctx.notes.append(f"youtube: headline_from {hf!r}")
        for e in qa.get("elements") or []:
            ctx.records.append({**e, "surface": "youtube", "zone": [0, 0, 1920, 1080], "canvas": [1920, 1080]})
        for k in ("tagline_dropped", "headline_dropped", "headline_lines_dropped", "figure_withheld",
                  "figure_declined", "headline_fragment"):
            if qa.get(k):
                ctx.recorded_drops.append(f"youtube: {k}: {qa[k]}")
        if qa.get("text_over_focus"):
            ctx.problems.append(f"youtube: text_over_focus: {qa['text_over_focus']}")
        if qa.get("title_overlap"):
            ctx.problems.append(f"youtube: title_overlap: {qa['title_overlap']}")
        if qa.get("downscale_ok") is False:
            ctx.notes.append(f"youtube: downscale fails: {qa.get('downscale_fails')}")
        with Image.open(out) as im:
            ctx.images.append({"name": "youtube", "im": im.convert("RGB"), "safe": (0, 0, 1920, 1080),
                               "floor": _floor(1920, 1080), "kind": "still"})


def _no_match(self, cards, stills, strict=False):
    """Harness: the photo match by rotation (the scene planner's vision call is never reached)."""
    return [stills[i % len(stills)] for i in range(len(cards))]


def d_carousel(case, ctx: Ctx):
    from PIL import Image

    from src.multi_format import MultiFormatGenerator
    MultiFormatGenerator._match_photos = _no_match
    n = 5 + case["rnd"] % 4
    slides = []
    for k in range(n):
        body = case["figure"]["text"] + " " + _t(case, k + 1) if k == 2 else _t(case, k + 1)
        slides.append({"heading": _t(case, k), "body": body})
    fb = case["surface"] == "carousel_fb"
    ctx.payload = {"title": _t(case, 0), "carousel_slides": slides, "topic_id": "stress_topic",
                   "narration_script": _signed(*(f"{s['heading']}. {s['body']}" for s in slides),
                                               case["figure"]["signed"]),
                   "share_line": "Share this with a friend who loves a riddle.",
                   "closing_sentence": _signed(slides[-1]["body"]), "poll": {}}
    for k, s in enumerate(slides):
        ctx.expect += [(f"slide{k + 1}.heading", s["heading"]), (f"slide{k + 1}.body", s["body"])]
    photos = gr.credited([_ground(case, k, ctx.work) for k in range(4)], ctx.work / "photos")
    paths = MultiFormatGenerator().generate_carousel_sequence(photos, ctx.payload, ctx.work, facebook=fb)
    sub = "carousel_fb" if fb else "carousel"
    data = _still_records(ctx, ctx.work / sub / "still_layout.json", sub)
    for p in paths:
        entry = data.get(Path(p).name) or {}
        with Image.open(p) as im:
            ctx.images.append({"name": Path(p).stem, "im": im.convert("RGB"), "safe": entry.get("zone"),
                               "floor": _floor(im.width, im.height), "kind": "still"})


def d_story(case, ctx: Ctx):
    from PIL import Image

    from src.multi_format import MultiFormatGenerator
    MultiFormatGenerator._match_photos = _no_match
    slides = [{"kicker": _t(case, 3 * k), "line1": _t(case, 3 * k + 1), "line2": _t(case, 3 * k + 2),
               "body": (case["figure"]["text"] + " " if k == 1 else "") + _t(case, 3 * k + 3)} for k in range(3)]
    ctx.payload = {"title": _t(case, 0), "story_slides": slides, "topic_id": "stress_topic",
                   "narration_script": _signed(*(f"{s['line1']} {s['line2']}. {s['body']}" for s in slides),
                                               case["figure"]["signed"]), "poll": {}}
    for k, s in enumerate(slides[:2]):    # STORY_FRAMES: hook and reveal (the poll frame is not drawn: QF-80)
        ctx.expect += [(f"story{k + 1}.{f}", s[f]) for f in ("kicker", "line1", "line2", "body")]
    photos = gr.credited([_ground(case, k, ctx.work) for k in range(3)], ctx.work / "photos")
    paths = MultiFormatGenerator().generate_story_sequence(photos[0], ctx.payload, ctx.work, scene_images=photos)
    data = _still_records(ctx, ctx.work / "stories" / "still_layout.json", "stories")
    for p in paths:
        with Image.open(p) as im:
            ctx.images.append({"name": Path(p).stem, "im": im.convert("RGB"), "safe": _boxes(1080, 1920, "story"),
                               "floor": _floor(1080, 1920), "kind": "still"})
    _ = data


def _words(text: str, t0: float = 0.1, rate: float = 2.6) -> list[dict]:
    out, t = [], t0
    for w in text.split():
        d = max(0.12, len(w) / (rate * 5.0))
        out.append({"word": w, "start": round(t, 3), "dur": round(d, 3)})
        t += d + 0.04
    return out


def d_captions(case, ctx: Ctx):
    from src import video_composer as vc
    text = " ".join([_t(case, 0), case["figure"]["text"] + ".", _t(case, 1), _t(case, 2) + "?", _t(case, 3)])
    words = _words(text)
    if not words:
        words = _words("Silence")
        ctx.notes.append("every caption field was empty: one placeholder word")
    ctx.payload = {"narration_script": " ".join(w["word"] for w in words), "topic_id": "stress_topic"}
    ctx.expect = []      # judged as the spoken sequence, in order (invariants: expect_sequence)
    wide = case["fmt"] == "169"
    kw = {"width": 1920, "height": 1080} if wide else {}
    badge = case["surface"].startswith("badge")
    end = words[-1]["start"] + words[-1]["dur"]
    ass = ctx.work / "captions.ass"
    emph = [w["word"].strip(".,?") for w in words[:: max(1, len(words) // 3)]][:3]
    comp = vc.VideoComposer()
    comp.build_subtitles(words, ass, emphasis=emph, reconstruction_intervals=[(0.0, end)] if badge else None, **kw)
    ctx.ass = ass
    side = ass.with_suffix(".contrast.json")
    if side.is_file():
        data = json.loads(side.read_text(encoding="utf-8"))
        tgt = ctx.work / "captions.contrast.json"
        if side != tgt:
            shutil.copy(side, tgt)
        from src.brand import TEXT_CONTRAST
        for e in data.get("elements") or []:
            ctx.contrast_rows.append({"surface": "captions", "name": e.get("name"), "value": e.get("drawn"),
                                      "gate": TEXT_CONTRAST, "text": e.get("text")})
        from src.figure_gate import caption_sequence
        ctx.records.append({"surface": "captions", "name": "captions", "text": caption_sequence(data)})
    else:
        ctx.problems.append("captions: no caption contrast record was written beside the ASS file")
    events = _ass_events(ass)
    bursts: list[list[str]] = []      # each burst's lines, once (a 9:16 burst repeats per spoken word)
    frames: dict[tuple, list] = {}    # the events on screen together (a two-line burst is two events)
    for ev in events:
        if ev["style"] == "CapText":
            frames.setdefault((ev["start"], ev["end"]), []).append(ev)
    for evs in frames.values():
        lines = [ln for ev in sorted(evs, key=lambda e: (e["pos"] or (0, 0))[1]) for ln in ev["lines"]]
        if not bursts or lines != bursts[-1]:
            bursts.append(lines)
    ctx.bursts = bursts
    ctx.lines.append(("captions", bursts))
    ctx.records.append({"surface": "captions.ass", "name": "ass_text",
                        "text": " ".join(" ".join(b) for b in bursts)})
    ctx.expect_sequence = ctx.payload["narration_script"]
    ctx.drawn_sequence = " ".join(" ".join(b) for b in bursts)
    w, h = (1920, 1080) if wide else (1080, 1920)
    if badge:
        marks = [ev for ev in events if ev["style"] == "ReconBadge"]
        if not marks:
            ctx.problems.append("badge: the reconstruction interval drew no badge event in the ASS file")
        for ev in marks:
            ctx.badge_text = " ".join(ev["lines"])
            if ev.get("pos") and h > w:
                x, y = ev["pos"]
                box = _boxes(w, h, "reel")
                if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                    ctx.problems.append(f"badge: anchored at {ev['pos']} outside the Reel box {list(box)}")


def _ass_events(ass: Path) -> list[dict]:
    """Every Dialogue event: its style, start/end, \\pos and its visible lines (override tags removed)."""
    import re
    out = []
    for ln in ass.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        parts = ln[len("Dialogue:"):].split(",", 9)
        style, raw = parts[3].strip(), parts[9]
        m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", raw)
        if "\\p1" in raw:
            continue      # a drawing (the plate), not text
        text = re.sub(r"\{[^}]*\}", "", raw)
        lines = [p.strip() for p in re.split(r"\\[Nn]", text) if p.strip()]
        out.append({"style": style, "start": parts[1].strip(), "end": parts[2].strip(), "lines": lines,
                    "pos": (float(m.group(1)), float(m.group(2))) if m else None})
    return out


# ---- cards: a sheet whose facts sign the generated copy (the tests' mocked checkers and throwaway key)
CARD_FIELD_KEY = "test-only-fact-record-key-0123456789abcdef"


def _card_env():
    from src import config
    config.FACT_RECORD_KEY = CARD_FIELD_KEY
    import tests.test_design_cards_2026_11_03 as T
    return T


def d_card(case, ctx: Ctx):
    from src import design_cards as dc
    T = _card_env()
    sheet = {"topic": "Stress topic", "facts": []}    # only the case's own facts: nothing else can contradict it
    w, h = _size(case)
    kind = case["surface"].split("_")[0]
    photo = _ground(case, 0, ctx.work)
    out_dir = ctx.work / "graphics"
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "hook":
        claim = (case["figure"]["text"] if case["rnd"] % 2 else _t(case, 0)) or _t(case, 0)
        sheet["facts"].append(T.fact("s_hook", claim if claim.strip() else "x"))
        eyebrow = _t(case, 1) if case["rnd"] % 3 == 0 else ""
        spec = T.hook(photo, sheet=sheet, claim=claim, fact_ids=("s_hook",), eyebrow=eyebrow,
                      first_burst=_t(case, 2) or "In the desert")
        ctx.expect = [("claim", claim)] + ([("eyebrow", eyebrow)] if eyebrow else [])
        ctx.payload = {"narration_script": _signed(claim, eyebrow), "topic_id": "stress_topic"}
        made = dc.render_hook_frame(spec, sheet, out_dir)
        out = out_dir / "hook_frame.mp4"
    elif kind == "chapter":
        title = _t(case, 0)
        num = 1 + case["rnd"] % 9
        spec = T.chapter(sheet=sheet, number=num, total=max(num, 1 + (case["rnd"] >> 4) % 12), title=title,
                         photo=photo)
        ctx.expect = [("title", title)]
        ctx.payload = {"narration_script": _signed(title), "topic_id": "stress_topic"}
        out = out_dir / "graphic_chapter_01.mp4"
        made = dc.render_chapter_card(spec, sheet, out, w, h)
    else:
        myth_t = _t(case, 0)
        # the correction shares a content word with the myth (the card refuses a pair that shares none): the myth's
        # longest word leads the generated fact copy
        lead = max(myth_t.split(), key=len) if myth_t.split() else ""
        fact_t = f"{lead} {_t(case, 1)}".strip()
        sheet = {"topic": "Stress topic", "facts": []}
        sheet["facts"].append(T.fact("s_myth", fact_t if fact_t.strip() else "x",
                                     evidence=[T.ev("https://www.britannica.com/s_myth", 2,
                                                    f"It is a popular belief that {myth_t}. {fact_t}.")],
                                     misconception=myth_t))
        spec = T.myth(sheet=sheet, myth_text=myth_t, fact_text=fact_t, fact_id="s_myth", photo=photo)
        ctx.expect = [("myth", myth_t), ("fact", fact_t)]
        ctx.payload = {"narration_script": _signed(myth_t, fact_t), "topic_id": "stress_topic"}
        out = out_dir / "graphic_mythfact_01.mp4"
        made = dc.render_myth_fact(spec, sheet, out, w, h)
    design = Path(str(out) + ".design.json")
    if made is None:
        rec = json.loads(design.read_text(encoding="utf-8")) if design.is_file() else {}
        if rec.get("refused"):
            ctx.status, ctx.refusal = "refused", {"class": "CardRefused", "code": "", "reason": rec["refused"]}
            return
        raise RuntimeError("the card renderer returned None with no refusal record")
    safe = _boxes(w, h, "graphics")
    _sidecar_records(ctx, Path(str(made) + ".contrast.json"), f"graphics/{Path(made).name}", safe, w, h)
    rec = json.loads(design.read_text(encoding="utf-8")) if design.is_file() else {}
    for e in rec.get("elements") or []:
        e_text = (rec.get("drawn_text") or {}).get(e["name"])
        if e_text is not None:
            ctx.records.append({"surface": "card", "name": e["name"], "text": e_text, "box": e["box"], "px": e["px"],
                                "zone": list(safe), "canvas": [w, h], "_card": True})
    # the last frame (cards are pure: frame_at)
    plan_last = None
    import capture
    if capture.CLIPS:
        recd = next(iter(capture.CLIPS.values()))
        plan_last = recd.get("last")
    if plan_last is not None:
        ctx.images.append({"name": kind, "im": plan_last, "safe": safe, "floor": _floor(w, h), "kind": "clip"})


def d_callout(case, ctx: Ctx):
    from src import callouts
    w, h = _size(case)
    sx, sy = w / 1080, h / 1920

    def pt(label, x, y, fw, fh, kind="point"):
        x, y, fw, fh = x * sx, y * sy, fw * sx, fh * sy
        box = (x - fw / 2, y - fh / 2, x + fw / 2, y + fh / 2)
        return (label, x, y, fw * fh / (w * h), fw, fh, kind, tuple(round(v) for v in box))
    labels = [_t(case, 0), _t(case, 1), _t(case, 2)]
    pts = [pt(labels[0], 600, 900, 260, 300), pt(labels[1], 300, 1010, 300, 380, "extent"),
           pt(labels[2], 760, 640, 160, 200)]
    ctx.expect = [(f"label{k + 1}", t) for k, t in enumerate(labels)]
    ctx.payload = {"narration_script": _signed(*labels), "topic_id": "stress_topic", "photo_labels": labels}
    kept = callouts.declutter(callouts.clear_of_captions(pts, w, h), w, h)
    if kept is None:
        ctx.status, ctx.refusal = "refused", {"class": "declutter", "code": "no_layout",
                                              "reason": "declutter: no valid label layout (no graphic)"}
        return
    kept_labels = {p[0] for p in kept}
    ctx.expect = [(k, t) for k, t in ctx.expect if t in kept_labels]
    for p in pts:
        if p[0] not in kept_labels:
            ctx.recorded_drops.append(f"callout: label {p[0]!r} left out by clear_of_captions/declutter")
    out = ctx.work / "graphics" / "graphic_callouts_01.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    callouts.render(_ground(case, 0, ctx.work), kept, out, width=w, height=h, reveal=[0.6, 1.4, 2.2][: len(kept)])
    safe = _boxes(w, h, "graphics")
    _sidecar_records(ctx, Path(str(out) + ".contrast.json"), f"graphics/{out.name}", safe, w, h)
    _clip(ctx, out, "callout", safe, _floor(w, h))


def _memo_end_card():
    from src import end_card
    real = end_card._gate_lines
    if getattr(real, "stress_memo", False):
        return

    def gate_lines(ground, *a, **k):
        memo = {}

        def once(t):
            im = memo.get(t)
            if im is None:
                im = memo[t] = ground(t)
            return im
        try:
            return real(once, *a, **k)
        finally:
            memo.clear()
    gate_lines.stress_memo = True
    end_card._gate_lines = gate_lines


def d_endcard(case, ctx: Ctx):
    from PIL import Image, ImageOps

    from src import end_card
    _memo_end_card()
    share = " ".join(x for x in (_t(case, 0), _t(case, 1)) if x.strip()) or _t(case, 2)
    ctx.payload = {"share_line": share, "narration_script": _signed(share), "topic_id": "stress_topic"}
    with Image.open(_ground(case, 0, ctx.work)) as src:
        last = src.convert("RGB")
    stats: dict[str, Any] = {}
    surf = case["surface"]
    if surf == "endscreen":
        last = ImageOps.fit(last, (1920, 1080), Image.LANCZOS)
        frames = end_card.frames(last, share, stats)
        w, h = 1920, 1080
    elif surf == "bridge":
        last = ImageOps.fit(last, (1080, 1920), Image.LANCZOS)
        with Image.open(_ground(case, 1, ctx.work)) as src2:
            first = ImageOps.fit(src2.convert("RGB"), (1080, 1920), Image.LANCZOS)
        frames = end_card.loop_bridge(last, first, share, stats)
        w, h = 1080, 1920
    else:
        last = ImageOps.fit(last, (1080, 1920), Image.LANCZOS)
        frames = end_card.frames(last, share, stats)
        w, h = 1080, 1920
    n, final = 0, None
    for f in frames:
        n += 1
        final = f
    ctx.clips[surf] = n
    drawn = stats.get("text")
    if surf != "endscreen":
        ctx.expect = [("share", share)]
    ctx.records.append({"surface": surf, "name": "end_card", "text": drawn if isinstance(drawn, str) else "",
                        "_stats": True})
    for k, v in stats.items():
        if k.startswith("contrast") and isinstance(v, (int, float)):
            gate = 5.5 if "masthead" in k else 4.5
            ctx.contrast_rows.append({"surface": surf, "name": k, "value": v, "gate": gate, "text": None})
    mast = stats.get("masthead") or {}
    if isinstance(mast, dict) and mast.get("shown") is False:
        ctx.recorded_drops.append(f"{surf}: masthead not shown: {mast.get('reason')}")
    if final is not None:
        safe = _boxes(w, h, "reel") if h > w else (0, 0, w, h)
        ctx.images.append({"name": surf, "im": final.convert("RGB"), "safe": safe, "floor": _floor(w, h),
                           "kind": "clip", "any_frame": True})
    ctx.stats = {k: v for k, v in stats.items() if isinstance(v, (str, int, float, bool, type(None)))}
    # a composition record as the master writes it, so the figure gate reads the end card's line
    (ctx.work / "master.composition.json").write_text(json.dumps({"end_card": {"text": drawn or ""}}),
                                                      encoding="utf-8")


def _graphic(case, ctx: Ctx, kind: str):
    from src import graphics
    from src.photo_grade import using
    w, h = _size(case)
    gdir = ctx.work / "graphics"
    gdir.mkdir(parents=True, exist_ok=True)
    out = gdir / f"graphic_{kind}.mp4"
    narration = ""
    with using(0.0):
        if kind == "map":
            place = dict(case["map"]["place"])
            ctx.expect = [("label", place["label"])] if place.get("label") else []
            ctx.payload = {"narration_script": _signed(place["label"]), "topic_id": "stress_topic",
                           "location": {"name": place["label"], "country": place.get("country") or ""}}
            made = graphics.render_map(place, out, width=w, height=h, narration="")
        elif kind == "route":
            stops = _route_stops(case)
            ctx.expect = [(f"stop{k + 1}", s["label"]) for k, s in enumerate(stops)]
            ctx.payload = {"narration_script": _signed(*(s["label"] for s in stops)), "route": stops,
                           "topic_id": "stress_topic"}
            made = graphics.render_route(stops, out, width=w, height=h, narration="")
        elif kind == "timeline":
            evs = _timeline_events(case)
            ctx.expect = [(f"event{k + 1}", e["label"]) for k, e in enumerate(evs)]
            ctx.payload = {"narration_script": _signed(*(e["label"] for e in evs)), "timeline": evs,
                           "topic_id": "stress_topic"}
            made = graphics.render_timeline(evs, out, width=w, height=h, photo=_ground(case, 0, ctx.work))
        elif kind == "scale":
            fact = dict(case["scale"]["fact"])
            ctx.expect = [("subject", fact["subject"])]
            narration = f"The {fact['subject']} is {'about ' if fact['approximate'] else ''}{fact['value']:g} " \
                        f"{'metres' if fact['unit'] == 'm' else 'feet'} tall."
            ctx.payload = {"narration_script": narration, "scale_fact": fact, "topic_id": "stress_topic"}
            made = graphics.render_scale(fact, out, width=w, height=h)
        elif kind == "section":
            cs = _section(case)
            ctx.expect = [("subject", cs["subject"])] + [(f"level{k + 1}", lv["label"])
                                                         for k, lv in enumerate(cs["levels"])]
            ctx.payload = {"narration_script": _signed(cs["subject"], *(lv["label"] for lv in cs["levels"])),
                           "cross_section": cs, "topic_id": "stress_topic"}
            made = graphics.render_cross_section(cs, out, width=w, height=h)
        elif kind == "numbers":
            cards = _stat_cards(case)
            ctx.expect = [(f"card{k + 1}.label", c["label"]) for k, c in enumerate(cards)]
            narration = _signed(*(f"{c['label']} {c['value']}" for c in cards))
            ctx.payload = {"narration_script": narration, "stat_cards": cards, "topic_id": "stress_topic"}
            made = graphics.render_stat_cards(cards, out, _ground(case, 0, ctx.work), width=w, height=h,
                                              narration=narration)
        else:
            raise ValueError(kind)
    if made is None:
        ctx.status = "none"
        return
    safe = _boxes(w, h, "graphics")
    _sidecar_records(ctx, Path(str(made) + ".contrast.json"), f"graphics/{Path(made).name}", safe, w, h)
    lay = Path(str(made) + ".layout.json")
    if lay.is_file():
        data = json.loads(lay.read_text(encoding="utf-8"))
        ctx.layout_record = data
        for k in ("dropped", "skipped", "names_dropped"):
            for d in data.get(k) or []:
                ctx.recorded_drops.append(f"{kind}: {k} {d}")
    _clip(ctx, made, kind, safe, _floor(w, h, "label"))


def _route_stops(case) -> list[dict]:
    rnd = random.Random(case["rnd"])
    names = ROUTE_PLACES[:]
    rnd.shuffle(names)
    n = 2 + case["rnd"] % 3
    return [{"place": p, "country": c, "label": (_t(case, k) or p)} for k, (p, c) in enumerate(names[:n])]


# places the offline cache holds (geo.locate reads it first; a stop it cannot locate is skipped, logged)
ROUTE_PLACES = [("Catherine Palace", "Russia"), ("Kholat Syakhl", "Russia"), ("Podkamennaya Tunguska", "Russia"),
                ("Topkapi Palace", "Turkey"), ("Syracuse", "Italy"), ("Odessa", "Ukraine"), ("Luoyang", "China"),
                ("Bijapur", "India"), ("British Museum", "UK"), ("Flannan Isles", "Scotland"),
                ("Ouadane", "Mauritania"), ("Paracas Peninsula", "Peru")]


def _timeline_events(case) -> list[dict]:
    rnd = random.Random(case["rnd"])
    n = 2 + case["rnd"] % 5
    cls = case["figure"]["cls"]
    lo = -9000 if cls in ("bc", "bce", "span_era") else (0 if cls in ("ad", "ce") else 1000)
    years = sorted(rnd.sample(range(lo, 2025), n))
    if cls == "span_descending":
        years = years[::-1]          # given in descending order: the renderer orders or refuses
    evs = []
    for k, y in enumerate(years):
        ev = {"year": y or 1, "label": _t(case, k), "approximate": rnd.random() < 0.3}
        if cls == "span" and k == 0:
            ev["end_year"] = y + rnd.randint(1, 40)
        evs.append(ev)
    return evs


def _section(case) -> dict:
    rnd = random.Random(case["rnd"])
    n = 1 + case["rnd"] % 6
    total = rnd.choice((0.5, 3.0, 40.0, 280.0, 1200.0, 40000.0))
    depths = sorted(round(rnd.uniform(0, total), 1) for _ in range(n))
    if case["figure"]["cls"] == "negative_depth":
        depths = [-d for d in depths]
    return {"subject": _t(case, 0) or "Shaft", "total_depth_ft": total, "approximate": rnd.random() < 0.5,
            "levels": [{"depth_ft": d, "label": _t(case, k + 1)} for k, d in enumerate(depths)]}


def _stat_cards(case) -> list[dict]:
    figs = gen.figure_cases(2, case["rnd"])
    return [{"value": case["figure"]["text"], "label": _t(case, 0)}] + \
        ([{"value": figs[1]["text"], "label": _t(case, 1)}] if case["rnd"] % 2 else [])


def d_thennow(case, ctx: Ctx):
    from src import then_now
    w, h = _size(case)
    rnd = random.Random(case["rnd"])
    old_y = rnd.choice((1826, 1850, 1900, 1944, 2019, 1))
    new_y = rnd.choice((2024, 2000, old_y, old_y - 10 if old_y > 20 else 2024))
    gdir = ctx.work / "graphics"
    gdir.mkdir(parents=True, exist_ok=True)
    out = gdir / "graphic_thennow.mp4"
    ctx.payload = {"narration_script": f"In {old_y} and in {new_y}.", "topic_id": "stress_topic"}
    then_now.render(_ground(case, 0, ctx.work), old_y, _ground(case, 1, ctx.work), new_y, out, w, h)
    safe = _boxes(w, h, "graphics")
    _sidecar_records(ctx, Path(str(out) + ".contrast.json"), f"graphics/{out.name}", safe, w, h)
    _clip(ctx, out, "thennow", safe, _floor(w, h, "label"))
    ctx.expect = [("then_year", str(old_y)), ("now_year", str(new_y))]


def driver(surface: str) -> Callable:
    base = surface.split("_")[0]
    return {"cover": d_cover, "youtube": d_cover, "carousel": d_carousel, "story": d_story, "captions": d_captions,
            "badge": d_captions, "hook": d_card, "chapter": d_card, "myth": d_card, "callout": d_callout,
            "endcard": d_endcard, "bridge": d_endcard, "endscreen": d_endcard, "thennow": d_thennow,
            "map": lambda c, x: _graphic(c, x, "map"), "route": lambda c, x: _graphic(c, x, "route"),
            "timeline": lambda c, x: _graphic(c, x, "timeline"), "scale": lambda c, x: _graphic(c, x, "scale"),
            "section": lambda c, x: _graphic(c, x, "section"),
            "numbers": lambda c, x: _graphic(c, x, "numbers")}[base]


class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.rows: list[str] = []

    def emit(self, record):
        try:
            self.rows.append(f"{record.name}: {record.getMessage()}"[:400])
        except Exception:  # noqa: BLE001, S110  a log line that cannot be formatted is not the case's
            pass


def drive(case: dict, work: Path) -> Ctx:
    import traceback

    import capture

    from src.refusal import LayoutError
    capture.reset()
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(case=case, work=work)
    handler = _Warnings()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        driver(case["surface"])(case, ctx)
    except LayoutError as e:
        ctx.status = "refused"
        ctx.refusal = {"class": type(e).__name__, "code": str(getattr(e, "code", "") or ""),
                       "reason": str(getattr(e, "reason", None) or e)[:400]}
    except Exception as e:  # noqa: BLE001  anything else is a failure, recorded with its traceback
        ctx.status = "error"
        # raised inside the tree's src (a product failure) or by the harness itself (said so: never a finding)
        frames = traceback.extract_tb(e.__traceback__)
        in_src = any(Path(f.filename).parent.name == "src" for f in frames)
        ctx.error = ("" if in_src else "HARNESS: ") + f"{type(e).__name__}: {str(e)[:400]}\n" + \
            traceback.format_exc()[-1500:]
    finally:
        root.removeHandler(handler)
    ctx.warnings = handler.rows
    if ctx.status == "none":
        warn = [w for w in handler.rows if w.startswith("src.")]
        if warn:
            ctx.status = "refused"
            ctx.refusal = {"class": "None+logged", "code": "", "reason": warn[-1]}
        else:
            ctx.status = "error"
            ctx.error = "SILENT_NONE: the renderer returned None with no refusal and no warning logged"
    ctx.plates = capture.plates()
    ctx.lines_drawn = list(capture.LINES)
    return ctx
