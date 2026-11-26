"""The board's evidence records (cycle 0 round 8, lane BOARD; panel consensus 8, AD E3/E4, CD-20, BC-7, PL-E1, MO-1):

- pairing(): every tile's pairing class, REAL / STAND-IN / FORCED, from its name: a probe that puts one run's claim,
  labels or badge on another photo (or a test's copy on a real photo) is never shown unlabelled. board.py labels every
  tile through it and stops on a probe name that has no rule (UNCLASSIFIED).
- caption_ground(): a caption case's ground at narration time t, re-drawn by THIS tree when the run's timeline puts a
  stored graphic there (the Amber Room's graphic_numbers at 2.09 s read "EST. OVER 11,722 LBS" from retired code).
- caption_rates(): each burst as the built ASS shows it, every line of it (on-screen seconds, characters, shown CPS,
  the spoken window, the word it ends on and whether that word is on the never-end list), and each line of it (its
  text, characters and the word it ends on, checked against brand_system.NEVER_END_WORDS), beside the composer's own
  record (VideoComposer.last_reading_rate).
- refusals(): one table per palette: every surface or element refused, raised or not drawn, with its reason.
- peer_feed(): a feed sheet per feed surface at real pixel size, our tile among 8 peer slots; peer posts come from
  --peers <dir>/<feed>/ when supplied, else the slots hold the channel's other runs' tiles labelled STAND-IN.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REAL, STAND_IN, FORCED = "REAL", "STAND-IN", "FORCED"
# cycle 3 (lane E): a 16:9 end-screen case's ground as board_motion.wide_last found it: index -> (class, why); filled
# where the ground is drawn (board_motion._end_jobs) and from render_surfaces.py's "wide_grounds" record
WIDE_GROUNDS: dict[int, tuple[str, str]] = {}


# ---------------------------------------------------------------------------------------------- refusals as records
REFUSED: list[tuple[str, str, str]] = []   # (surface, what, reason): every refusal the board's own calls met


def _refusal_type():
    """The tree's one refusal type (src/refusal.py, round 8); on an older tree its typography.TextFitError."""
    try:
        from src.refusal import LayoutError
        return LayoutError
    except ImportError:
        from src.typography import TextFitError
        return TextFitError


def guarded(surface: str, fn, *a, default=None, **k):
    """fn(*a, **k), or `default` when the renderer refuses (the tree's LayoutError and its subclasses): the refusal
    is recorded in REFUSED (the palette's refusal table, and a refusal tile on its row) and the board goes on. Every
    board call into a renderer goes through here; anything else that raises is a real failure, left to the caller."""
    try:
        return fn(*a, **k)
    except _refusal_type() as e:
        try:
            from src.refusal import record
            rec = record(e, surface)
        except ImportError:
            rec = {"refused": type(e).__name__, "reason": str(e)}
        REFUSED.append((surface, f"refused ({rec['refused']})", str(rec["reason"])[:400]))
        print(f"board: {surface} refused: {rec['reason']}", flush=True)
        return default


def captured(fn, *a, **k):
    """fn(*a, **k) in a board.py --workers process: (its value, the refusals it recorded in this process's REFUSED),
    so the board process records them (replay) where the one-process board would have; cycle 3: and the data files
    this worker process has read so far (board_inputs.py), for the board's input record."""
    n = len(REFUSED)
    value = fn(*a, **k)
    try:
        import board_inputs
        read = board_inputs.files()
    except ImportError:
        read = {}
    return value, REFUSED[n:], read


WORKER_INPUTS: dict[str, str] = {}   # the union of the worker processes' reads (board_inputs.py), for board.json


def replay(future):
    """A captured call's value; its refusals appended to REFUSED now (an exception in the worker is raised here)."""
    value, refused, *rest = future.result()
    REFUSED.extend(refused)
    if rest:
        import board_inputs
        board_inputs.merge(WORKER_INPUTS, rest[0])
    return value


def refusal_tile(surface: str, reason: str, size=(960, 540), word: str = "REFUSED"):
    """A grey tile that says the surface was refused (or, `word` RAISED, failed) and why (its row shows the
    refusal, never a gap)."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", size, (70, 70, 70))
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=26)
    words, lines, cur = f"{word}  {surface}:  {reason}".split(), [], ""
    for w in words:
        if cur and d.textlength(f"{cur} {w}", font=f) > size[0] - 40:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    for i, ln in enumerate(lines[:16]):
        d.text((20, 20 + 32 * i), ln, fill=(255, 220, 120), font=f)
    return im
CARD_GROUNDS = {"richat": "Richat Structure", "amber": "Amber Room", "obelisk": "unfinished obelisk (Aswan)"}
# the card grounds' topics (render_surfaces.py's cards job draws these three heroes): where a run of that topic
# records a card field, its copy is the real pairing (run_card_fields says whether one exists)
CARD_GROUND_TOPICS = {"richat": "richat_structure_sahara_eye", "amber": "amber_room_heist",
                      "obelisk": "unfinished_obelisk_aswan"}
RICHAT_COPY = ("myth_richat", "chapter_act1", "chapter_act4")
# names whose kind is a probe by construction: each must match a STAND-IN / FORCED rule or a stated REAL rule
PROBE_PREFIXES = ("card__", "callouts__", "captions__", "route__", "timeline_dense__", "cross_section__",
                  "cover_badge__", "thumbnail_badge__", "endscreen__")


def _endcard_labels() -> dict[int, str]:
    try:
        import fixtures as fx
        return {i: c.get("label") or "" for i, c in enumerate(fx.load("endcard"))}
    except Exception:  # noqa: BLE001
        return {}


def run_topic(run: str) -> str:
    """A run folder's topic (run_<topic>_<id>[_suffix], or the same without run_)."""
    return re.sub(r"_\d{9,}(_[a-z]+)?$", "", run[4:] if run.startswith("run_") else run)


def run_card_fields(topic: str) -> tuple[int, int]:
    """(runs of `topic` in the dev checkout, card fields their design-card sheets record): read, never assumed, so
    a STAND-IN card says why no real pairing was drawn (cycle 3: real copy wherever a run has it)."""
    key = ("card_fields", topic)
    if key in _CACHE:
        return _CACHE[key]
    dev = Path(os.environ.get("RR_FIXTURE_REPO") or ".")
    runs = fields = 0
    for p in sorted((dev / "output").glob(f"run_{topic}_*/verified_script.json")):
        if run_topic(p.parent.name) != topic:
            continue
        runs += 1
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            sp = d.get("design_card_sheet") or (d.get("fact_review") or {}).get("sheet_path")
            cards = (json.loads(Path(sp).read_text(encoding="utf-8")).get("cards") or {}) if sp else {}
            fields += sum(len(v) for k, v in cards.items() if k != "dropped" and isinstance(v, list))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    _CACHE[key] = (runs, fields)
    return runs, fields


_CACHE: dict = {}


def _no_card_field(g: str) -> str:
    runs, fields = run_card_fields(CARD_GROUND_TOPICS[g])
    return (f"{runs} run(s) of this ground in the dev checkout, {fields} card field(s) in their sheets"
            + ("" if fields else ": no real card copy exists for this ground"))


def pairing(name: str) -> tuple[str, str]:
    """(class, why) for a tile name."""
    n = name.split(" | ")[0]
    m = re.search(r"__copy_(\w+)$", n)   # round 8i (N4): board_extras.STANDIN_COPY_TAG, a refused surface's probe
    if m:
        forced = " (the illustration flag forced on)" if n.startswith(("cover_badge__", "thumbnail_badge__")) else ""
        if m.group(1) == "none":
            return STAND_IN, f"cross-run copy probe{forced}: no board hero's copy passes cover_fit for this surface"
        if run_topic(m.group(1)) == n.split("__")[1]:   # cycle 3: another run of this ground's own topic
            return (FORCED if forced else REAL), (
                f"the ground's own topic: run {m.group(1)}'s verified copy{forced} (the board run's own copy is "
                "refused for this surface; this run of the same topic passes cover_fit)")
        return STAND_IN, (f"cross-run copy: run {m.group(1)}'s verified copy on this ground{forced}, a board hero's "
                          "copy that passes cover_fit for this surface, taken in board order (the run's own copy is "
                          "refused and no other run of this topic passes)")
    m = re.match(r"card__(\w+?)__(richat|amber|obelisk)__", n)
    if m:
        maker, g = m.groups()
        if maker == "myth_long_et":
            return STAND_IN, f"test stress copy (LONG_ET) on the {CARD_GROUNDS[g]} photo"
        if maker.startswith("hook"):   # cycle 3: the claim is the tests' own sheet's (f_len), never a run's field
            return STAND_IN, (f"the tests' hook claim (their own fact sheet, signed with the test key) on the "
                              f"{CARD_GROUNDS[g]} photo; {_no_card_field(g)}")
        if maker in RICHAT_COPY:
            if g == "richat":
                return REAL, "the Richat card copy on the Richat photo (tests' committed card fields)"
            return STAND_IN, (f"the Richat card copy on the {CARD_GROUNDS[g]} photo (cross-run pairing); "
                              f"{_no_card_field(g)}")
    if n.startswith("callouts__reference_pair"):
        return STAND_IN, "the operator's reference pair labels (tests) on the Amber Room photo; no run drew a callout"
    if n.startswith("captions__") and "__badge" in n:
        return FORCED, "the AI-reconstruction badge forced on from 0 s over the run's real photos (no reconstruction)"
    if n.startswith("captions__") and n.endswith("__frame"):
        return STAND_IN, "flat grey ground (QF-35a libass frame), not the run's picture"
    if n.startswith("captions__"):
        return REAL, "the run's own words on its own ground"
    if n.startswith("route__") and "new_stops" in n:
        return STAND_IN, "stops that are not the run's own (its second stop is not in the offline place cache)"
    if n.startswith("timeline_dense__"):
        return STAND_IN, "the tests' five Rhodes events on another run's photo"
    if n.startswith("cross_section__"):
        return STAND_IN, "the tests' cross-section spec (no run carries one)"
    if n.startswith(("cover_badge__", "thumbnail_badge__")):
        return FORCED, "illustration flag forced on a real photo (no run had an illustrated hero)"
    for topic, ground in STRESS_GROUNDS.items():   # cycle 4: a stress ground's tile says why it is one, read
        if topic in n:                             # from the dev checkout, never assumed
            runs = len(topic_runs(topic))
            if runs:
                return STAND_IN, (f"STAND-IN NO LONGER NEEDED: {runs} run(s) of {topic} now exist in the dev checkout; "
                                  "draw this ground with its own run's copy (board_extras)")
            return STAND_IN, (f"stress case ({ground} ground): the {topic} photo with the bright ground's run copy; no "
                              f"run of {topic} exists in the dev checkout")
    m = re.match(r"(endscreen|endcard|bridge|bridge_follow)__(\d+)_", n)
    if m and m.group(1) == "endscreen" and int(m.group(2)) in WIDE_GROUNDS:   # cycle 3: what the ground really is
        return WIDE_GROUNDS[int(m.group(2))]
    if m:
        lab = _endcard_labels().get(int(m.group(2)), "")
        if "stands in" in lab:
            return STAND_IN, "the run's own landscape last-scene photo stands in for a 16:9 narration's last frame"
        return REAL, "the run's own last frame and share line"
    if n.startswith(PROBE_PREFIXES):
        return "UNCLASSIFIED", "a probe kind with no pairing rule"
    return REAL, ""


# cycle 4: grounds the board draws with another run's copy because their topic has no run (a stress case by
# construction): topic -> what the ground probes
STRESS_GROUNDS = {"antikythera_mechanism": "near-white"}


def topic_runs(topic: str) -> list[str]:
    """The dev checkout's run folders of `topic` (RR_FIXTURE_REPO/output/run_<topic>_<id>[_suffix])."""
    dev = Path(os.environ.get("RR_FIXTURE_REPO") or ".")
    return sorted(p.name for p in (dev / "output").glob(f"run_{topic}_*") if p.is_dir() and run_topic(p.name) == topic)


def label(name: str, text: str = "") -> str:
    """A tile's label with its pairing class first when it is not REAL (board.py's one labelling path)."""
    cls, why = pairing(name)
    if cls == "UNCLASSIFIED":
        raise ValueError(f"board: {name} is a probe kind with no pairing rule (board_evidence.pairing)")
    head = name.split(" | ")[0]
    rest = text or (name.split(" | ", 1)[1] if " | " in name else "")
    tag = f"{cls}: {why}" if cls != REAL else ""
    return " | ".join(x for x in (head, tag, rest) if x)


# ---------------------------------------------------------------------------------------------- caption grounds
def _stat_photo(run: Path):
    prov = run / "provenance.json"
    if not prov.is_file():
        return None
    try:
        src = json.loads(prov.read_text(encoding="utf-8"))
        for item in (src.get("scenes") or src.get("items") or []):
            p = Path(str(item.get("source") or item.get("photo") or ""))
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file():
                return p
    except (ValueError, AttributeError):
        return None
    return None


def current_graphic(run: Path, kind: str, size: tuple[int, int], work: Path) -> list:
    """The run's graphic `kind` re-drawn by this tree from its recorded inputs (as render_surfaces.py draws it), all
    frames."""
    from src import graphics
    from src.art_direction import use_palette
    from src.photo_grade import using
    inp = json.loads((run / "graphics" / f"graphic_{kind}.mp4.lineage.json").read_text(encoding="utf-8"))["settings"]["inputs"]
    p = inp["payload"]
    pal = run / "palette.json"
    use_palette(json.loads(pal.read_text(encoding="utf-8")) if pal.is_file() else None)
    frames: list = []
    real = graphics._encode

    def keep(fr, out, *_a, **_k):
        frames.extend(im.convert("RGB") for im in fr)
        return Path(out)
    graphics._encode = keep
    w, h = size
    out = work / f"ground_{kind}_{run.name}.mp4"
    from src.refusal import collecting, hold_rows
    try:
        # SYS-A-c4-4: the omission ledger open round the redraw, as graphics.make_all opens it (a part the renderer
        # leaves out is recorded, never a refused ground); the rows beside the ground's clip
        with collecting() as omitted, using(0.0):
            if kind == "map":
                graphics.render_map(inp["place"], out, width=w, height=h, narration=p.get("narration_script") or "")
            elif kind == "numbers":
                graphics.render_stat_cards(p["stat_cards"], out, _stat_photo(run), width=w, height=h,
                                           narration=p.get("narration_script") or "")
            elif kind == "timeline":
                graphics.render_timeline(p["timeline"], out, width=w, height=h, photo=_stat_photo(run))
            elif kind == "scale":
                graphics.render_scale(p["scale_fact"], out, width=w, height=h)
            else:
                raise ValueError(kind)
        Path(str(out) + ".omissions.json").write_text(json.dumps(hold_rows(omitted), indent=1, ensure_ascii=False),
                                                      encoding="utf-8", newline="\n")
    finally:
        graphics._encode = real
        use_palette(None)
    return frames


def caption_ground(case: dict[str, Any], t: float, size: tuple[int, int], work: Path):
    """(image, note): the case's ground at t; a stored graphic frame (graphic_<kind>_<s>.png) is re-drawn by this
    tree at that frame, never shown as the run stored it."""
    import fixtures as fx
    from PIL import Image, ImageOps
    src = Path(str(fx.background_at(case, t)))
    m = re.match(r"graphic_([a-z_]+?)_(\d+\.\d+)$", src.stem)
    if m:
        kind, at = m.group(1), float(m.group(2))
        frames = current_graphic(Path(str(case["run"])), kind, size, work)
        if not frames:
            raise RuntimeError(f"{case['topic']}: graphic_{kind} drew nothing on this tree")
        k = min(len(frames) - 1, round(at * 30))
        return frames[k], (f"the run's graphic_{kind} re-drawn by this tree at frame {k} ({k / 30:.2f} s; the stored "
                           f"{src.name} is from retired code)")
    with Image.open(src) as im:
        return ImageOps.fit(im.convert("RGB"), size, Image.LANCZOS), f"the run's ground {src.name}"


# ---------------------------------------------------------------------------------------------- caption rates
def _ts(s: str) -> float:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


_POS_Y = re.compile(r"\\pos\(\s*[-\d.]+\s*,\s*([-\d.]+)\s*\)")


def ass_bursts(ass_text: str) -> list[dict[str, Any]]:
    """The bursts as the ASS shows them: each CapBox event's start and end, and its whole text: every line of the
    burst, joined in reading order. A 9:16 two-line burst sets each line as its own CapText event at its own \\pos y
    (cycle 0 round 8i, N3: the record read only the last event, so it dropped line 1), and each line is re-emitted
    as its words are said (the words not yet said at alpha 0), so a line's text is that of the last event at its y
    inside the box. Each burst also carries `lines` (top to bottom: y, text)."""
    boxes, texts = [], []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:"):].split(",", 9)
        start, end, style, text = _ts(parts[1].strip()), _ts(parts[2].strip()), parts[3].strip(), parts[9]
        if style == "CapBox":
            boxes.append((start, end))
        elif style == "CapText":
            m = _POS_Y.search(text)
            texts.append((start, end, float(m.group(1)) if m else 0.0,
                          " ".join(re.sub(r"\{[^}]*\}", "", text).split())))
    out = []
    for s, e in boxes:
        at_y: dict[float, str] = {}
        for _s, _e, y, t in (x for x in texts if s - 1e-3 <= x[0] < e - 1e-3):   # in file order: the last one wins
            at_y[y] = t
        lines = [{"y": y, "text": at_y[y]} for y in sorted(at_y) if at_y[y]]
        out.append({"show": round(s, 3), "hide": round(e, 3), "text": " ".join(ln["text"] for ln in lines),
                    "lines": lines})
    return out


def line_never_end(word: str) -> bool:
    """A caption LINE ending on `word` ends on the derived never-end set (brand_system.NEVER_END_WORDS: the closed
    class RUBRIC TY2 names, "no line ends on an article or preposition"), unless punctuation closes it (as
    video_composer.never_end_end reads a burst's end)."""
    from src.brand_system import NEVER_END_WORDS
    from src.video_composer import CLAUSE_PUNCT, TERMINAL_PUNCT
    word = str(word or "")
    if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word):
        return False
    return re.sub(r"[^\w]", "", word).lower() in NEVER_END_WORDS


def caption_rates(words: list[dict[str, Any]], ass_text: str, record: dict[str, Any] | None) -> dict[str, Any]:
    # rules2 F4: the composer's own never-end predicate (video_composer.never_end_end, the closed class the breakers
    # weigh), never a second list: the record counts what the breaker sees (it counted LINE_END_NEVER only)
    from src.video_composer import _line_break_defect, never_end_end
    # cycle 0 round 8i (N3): each burst is all of its lines (ass_bursts), so its characters and shown CPS are the
    # whole burst's, and every line ending is checked too (line_never_end: OF / ARMY GROUP NORTH ends line 1 on OF)
    def bare(word: str) -> str:
        return re.sub(r"[^\w'-]", "", word).lower()
    raw = [str(w.get("word", "")) for w in words]
    cursor = [0]

    def spoken_form(shown: str) -> str:
        """The narration's own word for the next shown word (the caption drops its punctuation; the never-end
        predicates read it, so "both." closes a line as the breaker reads it); the shown word if they part."""
        k = cursor[0]
        cursor[0] += 1
        return raw[k] if k < len(raw) and re.sub(r"[^\w]", "", raw[k]).lower() == re.sub(r"[^\w]", "", shown).lower() \
            else shown
    rows = []
    for b in ass_bursts(ass_text):
        secs = b["hide"] - b["show"]
        said = [w for w in words if b["show"] - 0.2 <= float(w["start"]) < b["hide"]]
        spoken = (float(said[-1]["start"]) + float(said[-1].get("dur", 0.0)) - float(said[0]["start"])) if said else 0
        ends, shown, cuts = [], [], []
        for ln in b["lines"]:
            forms = [spoken_form(w) for w in ln["text"].split()]
            ends.append(forms[-1] if forms else "")
            shown += forms
            cuts.append(len(shown))
        last_word = ends[-1] if ends else ""
        lines = []
        for k, ln in enumerate(b["lines"]):
            end = ends[k]
            last = k == len(b["lines"]) - 1
            # never_end: at a line BREAK, the composer's own break predicate (SYS-C-1: video_composer._line_break_defect,
            # the never-end class and the figure-unit bond, a whole hedge phrase accepted: MORE THAN / 830 SQUARE
            # MILES), never a second list; at the burst's end, the derived set (brand_system.NEVER_END_WORDS).
            # dangling: the breakers' own burst-end predicate (video_composer.never_end_end), read at a line
            never_end = line_never_end(end) if last else _line_break_defect(shown, cuts[k])
            lines.append({**ln, "chars": len(ln["text"]), "ends_on": bare(end), "never_end": never_end,
                          "dangling": never_end_end(end), "burst_end": last})
        rows.append({**b, "lines": lines, "on_screen_s": round(secs, 3), "chars": len(b["text"]),
                     "shown_cps": round(len(b["text"]) / secs, 1) if secs > 0 else None,
                     "spoken_window_s": round(spoken, 3), "ends_on": bare(last_word),
                     "never_end": never_end_end(last_word)})

    def breaks(key: str) -> list[str]:   # the bursts whose line break (not the burst's end) falls on `key`
        return [" / ".join(ln["text"] for ln in r["lines"]) for r in rows
                if any(ln[key] for ln in r["lines"] if not ln["burst_end"])]
    inner = [ln for r in rows for ln in r["lines"] if not ln["burst_end"]]
    return {"bursts": rows, "max_shown_cps": max((r["shown_cps"] or 0 for r in rows), default=None),
            "over_17": sum((r["shown_cps"] or 0) > 17 for r in rows), "never_end_count": sum(r["never_end"] for r in rows),
            "lines": sum(len(r["lines"]) for r in rows), "two_line_bursts": sum(len(r["lines"]) > 1 for r in rows),
            "one_word_lines_in_two_line_bursts": sum(len(ln["text"].split()) == 1 for r in rows if len(r["lines"]) > 1
                                                     for ln in r["lines"]),
            "line_never_end_count": sum(ln["never_end"] for r in rows for ln in r["lines"]),
            "line_break_never_end_count": sum(ln["never_end"] for ln in inner), "line_break_never_end": breaks("never_end"),
            "line_break_dangling_count": sum(ln["dangling"] for ln in inner), "line_break_dangling": breaks("dangling"),
            "composer_line_defect": (record or {}).get("line_defect"),   # SYS-C-1: the composer's own count, beside
            "composer_record": record}


# ---------------------------------------------------------------------------------------------- refusals
def refusals(surfaces: dict[str, Any] | None, manifest: dict[str, Any], extra: list[tuple[str, str, str]]) -> list[tuple]:
    """(surface, what, reason) for every refusal, exception and undrawn element this palette's board recorded."""
    rows: list[tuple] = []

    def last_line(tb: str) -> str:
        lines = [ln for ln in str(tb).strip().splitlines() if ln.strip()]
        return lines[-1].strip()[:400] if lines else ""
    for name, rec in sorted(((surfaces or {}).get("surfaces") or {}).items()):
        if isinstance(rec, dict) and "refused" in rec:
            rows.append((name, "refused (recorded)", rec["refused"]))
    for name, tb in sorted(((surfaces or {}).get("errors") or {}).items()):
        rows.append((name, "exception (render_surfaces)", last_line(tb)))
    for name, tb in sorted((manifest.get("errors") or {}).items()):
        rows.append((name, "exception (board)", last_line(tb)))
    for name, lab in sorted((manifest.get("labels") or {}).items()):
        if str(lab).startswith("REFUSED:"):
            rows.append((name, "refused (recorded)", str(lab)[len("REFUSED:"):].strip()))
    rows += [r for r in REFUSED if r not in rows]
    rows += omission_table_rows(surfaces)
    rows += extra
    return rows


# ---------------------------------------------------------------------------------------------- recorded omissions
OMISSION_WHAT = "drawn without a part (recorded omission)"


def surface_omissions(surfaces: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Cycle 4 (carry-in 6): surface stem -> the omission ledger's rows render_surfaces.py attributed to it (each row
    recorded while that surface was drawn: a map without a neighbour's name, a timeline without an event)."""
    return {k: list(v) for k, v in ((surfaces or {}).get("surface_omissions") or {}).items() if v}


def omission_note(stem: str, surfaces: dict[str, Any] | None) -> str:
    """The tile's record note for a surface drawn without a part ("" when nothing was left out): every omitted
    element with its rule, so a reviewer sees a recorded drop on the tile itself, not only in the table."""
    rows = surface_omissions(surfaces).get(stem) or []
    if not rows:
        return ""
    return "RECORDED OMISSION: " + "; ".join(f"drawn without {r.get('omitted')!r} (rule: {r.get('rule')})"
                                               for r in rows)


def masthead_note(rec: dict[str, Any] | None) -> str:
    """Cycle 5 (SYS-plates-c5-1): a frame whose masthead the gate withheld says so on its tile (a masthead a rule
    leaves off, a Facebook interior's, is no omission)."""
    if not rec or rec.get("shown", True) is not False or rec.get("fb_interior"):
        return ""
    return f"RECORDED OMISSION: drawn without the masthead ({str(rec.get('reason') or 'withheld')[:160]})"


def record_note(stem: str, surfaces: dict[str, Any] | None) -> str:
    """Cycle 5: what render_surfaces.py recorded about a social frame beyond its pixels: a withheld masthead, the
    Story pointer's decision (withheld by rule is not a defect), a photograph the picker replaced."""
    s = surfaces or {}
    parts = [masthead_note((s.get("masthead_records") or {}).get(stem))]
    p = (s.get("pointer_records") or {}).get(stem)
    if p:
        parts.append("Story pointer: " + ("drawn" if p.get("shown", True) is not False else
                                          f"withheld ({str(p.get('reason') or 'by rule')[:140]})"))
    c = (s.get("photo_choice") or {}).get(stem)
    if c and c.get("replay"):   # cycle 5 (review item 5): a deck production's own entry could not be driven for
        parts.append(f"REPLAY, not production's draw: the slide is redrawn from the run's saved recipe "
                     f"({str(c['replay'])[:220]})")
    if c and c.get("replaced") and not c.get("diagram"):   # a diagram slide shows its drawing, not the photograph
        parts.append(f"photo picked by deck_photo_guard: {Path(str(c.get('photo'))).name} for "
                     f"{Path(str(c['replaced'])).name} ({str(c.get('reason'))[:100]})")
    return " | ".join(x for x in parts if x)


TIER_WHAT = {"T1": "published / every run", "T2": "data-gated in a normal run",
             "T3": "behind a flag that is off by default (documentary / 16:9, callouts): no real run produced it",
             "T4": "internal, never seen, or nothing in the pipeline calls it"}
# the surface kinds tier() has a rule for (a name's first part); every other name is an internal sheet (T4)
TIER_HEADS = ("cover", "cover_badge", "thumbnail_badge", "captions", "captions_run", "social", "bridge",
              "bridge_follow", "endscreen", "endcard", "timeline_dense", "map", "timeline", "numbers", "scale",
              "thennow", "route", "cross_section", "callouts", "card", "hook", "act_dissolve")
TIER_COLOURS = {"T1": (120, 220, 130), "T2": (240, 215, 110), "T3": (240, 160, 90), "T4": (150, 190, 235)}
# a row-12 evidence sheet's kind -> a surface name of the kind it measures (the sheet itself is internal, T4)
SHEET_MEASURES = {"endscreen": "endscreen__x__169", "endcard916": "endcard__x", "rise": "endcard__x",
                  "bridge": "bridge__x", "bridge_nofollow": "bridge__x", "hook": "hook__x__916",
                  "dissolve": "act_dissolve__x__916"}
SHEET_SUFFIXES = ("_curves", "_curve", "_timing", "_steps", "_alpha", "_bursts")


def tier_known(name: str) -> bool:
    """Whether `name` is a surface case tier() has a rule for (not an internal sheet, a strip frame or a peer)."""
    return "__" in name and name.split("__")[0] in TIER_HEADS


def sheet_measures(stem: str, wide_captions: tuple[str, ...] = ()) -> str | None:
    """The tier of the production surface an evidence sheet measures (row12_motion_bridge_curves: the loop bridge,
    T1), or None for a sheet of no one surface (a specimen, a table, a feed). The sheet's own tier stays T4: nobody
    sees a strip or a curve; the note lets a score be read against the surface it is about."""
    m = re.match(r"row\d+_(?:motion_)?(.+)$", stem)
    if not m:
        return None
    rest = m.group(1)
    for suffix in SHEET_SUFFIXES:
        if rest.endswith(suffix):
            rest = rest[:-len(suffix)]
            break
    if tier_known(rest):
        return tier(rest, "", wide_captions)
    kind = rest.split("__")[0]
    return tier(SHEET_MEASURES[kind], "", wide_captions) if kind in SHEET_MEASURES else None


def tile_tier(name: str, sheet: str = "", wide_captions: tuple[str, ...] = ()) -> tuple[str, str]:
    """(tier, the text drawn in the tile's label strip) for one tile of the sheet `sheet`: the tier of the surface
    case the tile's label names (any ` | ` part of it that is a case name), else the sheet's own (T4, with the tier
    of the surface the sheet measures where it measures one). Every tile of every sheet gets one."""
    case = next((part for part in name.split(" | ") if tier_known(part.strip())), None)
    if case is not None:
        t = tier(case.strip(), name, wide_captions)
        return t, f"TIER {t[1:]}: {TIER_WHAT[t]}"
    t = tier(sheet or name, name, wide_captions)
    about = sheet_measures(sheet or name, wide_captions)
    return t, f"TIER {t[1:]}: {TIER_WHAT[t]}" + (
        f" | measures a tier {about[1:]} surface ({TIER_WHAT[about]})" if about and about != t else "")


def case_tiers(manifest: dict[str, Any], wide_captions: tuple[str, ...] = ()) -> dict[str, str]:
    """board.json "case_tiers" completed: every case of every row (rows 1, 2, 12, 13 and the peer feeds too, which
    are assigned outside the surface rows' loop) has a tier; a case the loop tiered with its label keeps it. Also
    "sheet_measures": the tier of the surface each internal sheet measures. Returns the case tiers."""
    rec = manifest.setdefault("case_tiers", {})
    labels = manifest.get("labels") or {}
    for cases in (manifest.get("rows") or {}).values():
        for case in cases or []:
            name = str(case).split(" | ")[0]
            if name not in rec:
                lab = labels.get(name)
                rec[name] = tier(name, f"{case} | {lab}" if isinstance(lab, str) else str(case), wide_captions)
            about = sheet_measures(name, wide_captions) if not tier_known(name) else None
            if about:
                manifest.setdefault("sheet_measures", {})[name] = about
    return rec


def tier(name: str, label: str = "", wide_captions: tuple[str, ...] = ()) -> str:
    """A board case's production tier (lander ruling 2026-11-26: tiers follow queue SURFACE-USAGE-2026-11-26
    exactly; results are reported by tier and a verdict is set by tiers 1-2). T1 published or made by every run:
    Reel cover, 9:16 captions, Story, Instagram / Facebook carousel, 9:16 map, timeline, number card, loop bridge.
    T2 data-gated in a normal run: scale, then-and-now, hook, myth, diagram slide, route, cross-section, the AI
    badges on a 9:16 surface. T3 behind a flag that is off by default, so no real run produced it: the documentary /
    16:9 flag (YouTube thumbnail and its badge, every 16:9 caption, graphic and card) and CALLOUTS_ENABLED (the photo
    callout: code default false). T4 never seen, or nothing in the pipeline calls it: the grid tile, the dense
    timeline, the 9:16 chapter card, the 9:16 end card, the 16:9 end screen (end_card.frames has no caller in src:
    tests and the board only), anything else (internal sheets). `label` tells a diagram slide; `wide_captions` the
    topics whose caption fixture is 16:9."""
    n = name
    head = n.split("__")[0]
    wide = "__169" in n or n.endswith("_169")
    if head in ("cover", "cover_badge", "thumbnail_badge"):
        if n.endswith("__youtube") or head == "thumbnail_badge":
            return "T3"
        if n.endswith("__grid"):
            return "T4"
        return "T2" if head == "cover_badge" else "T1"
    if head == "captions":
        if any(f"captions__{t}__" in n + "__" for t in wide_captions):
            return "T3"
        return "T2" if "__badge" in n else "T1"
    if head == "captions_run":
        return "T3" if wide else "T1"
    if head == "social":
        return "T2" if "diagram" in label.lower() else "T1"
    if head in ("bridge", "bridge_follow"):
        return "T1"
    if head in ("endscreen", "endcard"):   # inventory: end_card.frames has no production caller at either size
        return "T4"
    if head == "timeline_dense":
        return "T4"
    if head in ("map", "timeline", "numbers"):
        return "T3" if wide else "T1"
    if head == "callouts":   # inventory: flag-gated (CALLOUTS_ENABLED, code default false); no run drew one
        return "T3"
    if head in ("scale", "thennow", "route", "cross_section"):
        return "T3" if wide else "T2"
    if head == "card":
        kind = n.split("__")[1] if "__" in n else ""
        if kind.startswith("chapter"):
            return "T3" if wide else "T4"
        return "T3" if wide else "T2"   # hook (9:16 only), myth
    if head == "hook":
        return "T2"
    if head == "act_dissolve":
        return "T1"   # the Reel's own transition between its acts
    return "T4"


def omission_table_rows(surfaces: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """The refusal table's rows for recorded omissions: one per row attributed to a surface, and one per job-level
    row no surface took (a tree whose render_surfaces.py records per job only, or a row recorded after the job's last
    surface), named by the job."""
    out, placed = [], []
    for stem, rows in sorted(surface_omissions(surfaces).items()):
        for r in rows:
            out.append((stem, OMISSION_WHAT, str(r.get("reason") or r.get("omitted"))[:400]))
            placed.append(r)
    for job, rows in sorted(((surfaces or {}).get("omissions") or {}).items()):
        for r in rows:
            if r not in placed:
                out.append((f"{job} (job)", OMISSION_WHAT, str(r.get("reason") or r.get("omitted"))[:400]))
    return out


# ---------------------------------------------------------------------------------------------- peer feed
# Real pixel sizes (CSS px at 1x on a phone; the sizes the system's own feed proofs use): the Instagram profile grid
# tile 124 px wide (3:4, safe_zones / masthead.grid_cap), the Instagram and Facebook feed post 390 px wide (an iPhone's
# CSS width), YouTube home at 246 px and the suggested column at 168 px (youtube_thumbnail's feed proof).
FEEDS = {
    "ig_grid": {"title": "Instagram profile grid", "w": 124, "aspect": (3, 4), "cols": 3, "gap": 2, "bg": (255, 255, 255)},
    "ig_feed": {"title": "Instagram feed", "w": 390, "aspect": (4, 5), "cols": 3, "gap": 24, "bg": (255, 255, 255)},
    "yt_home": {"title": "YouTube home", "w": 246, "aspect": (16, 9), "cols": 3, "gap": 16, "bg": (255, 255, 255)},
    "yt_suggested": {"title": "YouTube suggested", "w": 168, "aspect": (16, 9), "cols": 1, "gap": 8, "bg": (255, 255, 255)},
    "fb_feed": {"title": "Facebook feed", "w": 390, "aspect": None, "cols": 3, "gap": 24, "bg": (240, 242, 245)},
    # cycle 3: a Facebook Page's Reels tab (the operator's captures: 12 tiles, ~312 x 545 px each, every one the first
    # frame of a comparable Reel); our designed Reel cover and our Reel's first frame among them at that tile size
    "fb_reels": {"title": "Facebook Page Reels tab", "w": 312, "aspect": (312, 545), "cols": 3, "gap": 4,
                 "bg": (240, 242, 245)},
}
PEER_SLOTS = 8


# Facebook's multi-photo post in the feed (operator evidence, evidence/peers/fb_feed/peer_01.png, a 1137 px wide
# capture): the first 5 photos as a collage, 2 square halves on top and 3 square thirds below, a 4 px gap at 1137 px
# (measured: top tiles x 0-566 / 570-1137, y 230-796; bottom x 0-376 / 380-756 / 760-1137, y 800-1177), "+N" over the
# 5th when the post has more. The feed never shows the first photo alone.
FB_COLLAGE_GAP = 4 / 1137
FB_COLLAGE_PHOTOS = 5


def fb_post_slides(run_dir: Path, current: dict[str, str] | None = None) -> list[str]:
    """The Facebook copy's slide files (carousel_fb/carousel_slide_NN.jpg) the publisher posts, in its order:
    multi_format.facebook_pick over the run's Facebook slides (at most FACEBOOK_PHOTOS: the cover, the strongest
    middles, the closing slide), reading each slide's record with today's archetype (`current`: file name ->
    archetype, render_surfaces.py's "rerouted"), as production records it now. [] when the run has no Facebook copy."""
    from src import still_layout
    from src.multi_format import facebook_pick
    d = Path(run_dir) / "carousel_fb"
    slides = sorted(str(p) for p in d.glob("carousel_slide_*.jpg"))
    if not slides:
        return []
    real = still_layout.load

    def load(still):
        rec = real(still)
        name = Path(still).name
        if rec and current and name in current and (rec.get("recipe") or {}).get("archetype") != current[name]:
            rec = {**rec, "recipe": {**(rec.get("recipe") or {}), "archetype": current[name]}}
        return rec
    still_layout.load = load
    try:
        return [Path(p).name for p in facebook_pick(slides)]
    finally:
        still_layout.load = real


def fb_collage_boxes(w: int) -> list[tuple[int, int, int, int]]:
    """The 5 photo boxes of a Facebook multi-photo post `w` px wide: two square halves, then three square thirds."""
    g = max(1, round(w * FB_COLLAGE_GAP))
    top = (w - g) // 2
    bot = (w - 2 * g) // 3
    boxes = [(0, 0, top, top), (top + g, 0, w, top)]
    y = top + g
    xs = [0, bot + g, 2 * (bot + g)]
    boxes += [(x, y, (x + bot) if k < 2 else w, y + bot) for k, x in enumerate(xs)]
    return boxes


def tree_collage_boxes(n: int, w: int) -> list[tuple[int, int, int, int]] | None:
    """The boxes of an `n`-photo post `w` px wide from the tree's own collage model (multi_format.FACEBOOK_COLLAGE:
    tiles in shares of the width), or None when the tree has no model for `n` (a post of 4 photos is a deck of 4
    slides or fewer, where facebook_pick posts them all: no real run has one yet, cycle 4)."""
    try:
        from src.multi_format import FACEBOOK_COLLAGE
    except ImportError:
        return None
    tiles = FACEBOOK_COLLAGE.get(n)
    if not tiles:
        return None
    return [(round(x * w), round(y * w), round((x + tw) * w), round((y + th) * w)) for x, y, tw, th in tiles]


def fb_collage(photos: list, w: int = 1137):
    """`photos` (a deck's Facebook slides, in order) as Facebook's feed draws a multi-photo post: the first 5 each
    centre-cropped into its box (fb_collage_boxes), "+N" over the 5th when there are N more."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    boxes = fb_collage_boxes(w)
    if len(photos) < FB_COLLAGE_PHOTOS:   # cycle 4: a smaller post as the tree's own collage model lays it out
        model = tree_collage_boxes(len(photos), w)
        if model:
            boxes = model
    out = Image.new("RGB", (w, max(b[3] for b in boxes)), (39, 39, 39))   # the gap colour of the capture (dark mode)
    for box, im in zip(boxes, photos[:FB_COLLAGE_PHOTOS]):
        size = (box[2] - box[0], box[3] - box[1])
        out.paste(ImageOps.fit(im.convert("RGB"), size, Image.LANCZOS), box[:2])
    more = len(photos) - FB_COLLAGE_PHOTOS
    if more > 0:
        x0, y0, x1, y1 = boxes[-1]
        tile = out.crop(boxes[-1]).convert("RGBA")
        tile.alpha_composite(Image.new("RGBA", tile.size, (0, 0, 0, 90)))
        d = ImageDraw.Draw(tile)
        f = ImageFont.load_default(size=max(10, round((y1 - y0) * 0.2)))
        d.text((tile.width / 2, tile.height / 2), f"+{more}", fill=(255, 255, 255), font=f, anchor="mm")
        out.paste(tile.convert("RGB"), (x0, y0))
    return out


def feed_tile(im, feed: str):
    """`im` as `feed` shows it: the platform's crop (the grid's centre 3:4, the feed's 4:5) at its real width."""
    from PIL import Image, ImageOps
    spec = FEEDS[feed]
    if spec["aspect"]:
        aw, ah = spec["aspect"]
        return ImageOps.fit(im.convert("RGB"), (spec["w"], round(spec["w"] * ah / aw)), Image.LANCZOS)
    return im.convert("RGB").resize((spec["w"], round(spec["w"] * im.height / im.width)), Image.LANCZOS)


def _topic(name: str) -> str:
    return re.sub(r"_\d{9,}.*$", "", name.split("__")[1]) if "__" in name else name


PEER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def supplied_peers(peers_dir: Path | None, feed: str) -> list[tuple[str, Any]]:
    """The peer posts supplied for `feed` (<peers_dir>/<feed>/*.jpg|png|webp, sorted); none when that folder is
    missing or empty (cycle 3: a partial --peers dir fills the feeds it has files for)."""
    from PIL import Image
    d = peers_dir / feed if peers_dir else None
    if d is None or not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in PEER_SUFFIXES:
            with Image.open(p) as im:
                out.append((p.name, im.convert("RGB")))
    return out


def peer_note(feed: str, n_supplied: int, peers_dir: Path | None) -> str:
    """What fills a feed's peer slots: supplied posts, STAND-INs (the channel's own other runs) or both."""
    if n_supplied >= PEER_SLOTS:
        return (f"PEERS: {n_supplied} supplied posts ({peers_dir / feed})" + (
            f", {PEER_SLOTS} per block, each block the next {PEER_SLOTS} in turn" if n_supplied > PEER_SLOTS else ""))
    why = (f"no peer posts supplied: board.py --peers <dir> with <dir>/{feed}/*.jpg" if peers_dir is None else
           f"{peers_dir / feed} holds {n_supplied} post(s)")
    if n_supplied:
        return (f"PEERS: {n_supplied} supplied posts ({peers_dir / feed}); the other {PEER_SLOTS - n_supplied} "
                f"slots STAND-IN, the channel's own other runs ({why})")
    return f"PEER SLOTS: STAND-IN, the channel's own other runs ({why})"


def peer_feed(feed: str, ours: list[tuple[str, Any]], pool: list[tuple[str, Any]], peers_dir: Path | None):
    """(tiles for board.sheet, note): per our tile, a mocked feed block of PEER_SLOTS + 1 tiles with ours in the
    middle, at real size. The slots take the posts supplied in peers_dir/<feed>/ first, each once, and the rest the
    channel's other runs (pool); every slot's legend says PEER or STAND-IN (cycle 3: a partial --peers dir fills
    what it has, a feed without posts stays STAND-IN and says so)."""
    from PIL import Image, ImageDraw, ImageFont
    spec = FEEDS[feed]
    every = supplied_peers(peers_dir, feed)
    note = peer_note(feed, len(every), peers_dir)
    tiles = []
    f = ImageFont.load_default(size=14)
    for j, (name, im) in enumerate(ours):
        # more posts than slots: each block takes the next PEER_SLOTS in turn, so every supplied post is shown
        start = (j * PEER_SLOTS) % len(every) if len(every) > PEER_SLOTS else 0
        supplied = (every[start:] + every[:start])[:PEER_SLOTS]
        mixed = 0 < len(supplied) < PEER_SLOTS   # each slot says which it is (all STAND-IN: the title says so)
        peers = [(f"PEER {n}" if mixed else n, p) for n, p in supplied]
        others = [(f"STAND-IN {n}" if mixed else n, p) for n, p in pool if _topic(n) != _topic(name)]
        block_note = note
        if len(peers) < PEER_SLOTS:
            if not others and not peers:
                continue
            if others:
                peers += [others[k % len(others)] for k in range(PEER_SLOTS - len(peers))]
            else:   # no other run to stand in: the supplied posts once each, never repeated as if more peers
                block_note = (f"PEERS: {len(peers)} supplied post(s) ({peers_dir / feed}), each once; no STAND-IN "
                              "for this block (no other run of the channel has this surface)")
        slots = [feed_tile(p, feed) for _n, p in peers]
        names = [n for n, _p in peers]
        mid = min(PEER_SLOTS // 2, len(peers))
        slots.insert(mid, feed_tile(im, feed))
        names.insert(mid, f"OURS {name}")
        cols = spec["cols"]
        rows = [slots[i:i + cols] for i in range(0, len(slots), cols)]
        g = spec["gap"]
        W = cols * spec["w"] + (cols - 1) * g + 2 * g
        H = sum(max(t.height for t in r) for r in rows) + (len(rows) + 1) * g
        block = Image.new("RGB", (W, H), spec["bg"])
        y = g
        for r in rows:
            x = g
            for t in r:
                block.paste(t, (x, y))
                x += t.width + g
            y += max(t.height for t in r) + g
        legend = Image.new("RGB", (W, 18 * len(names) + 8), (255, 255, 255))
        d = ImageDraw.Draw(legend)
        for k, n in enumerate(names):
            d.text((6, 4 + 18 * k), f"slot {k + 1}: {n}"[:max(20, W // 7)], fill=(0, 0, 0), font=f)
        out = Image.new("RGB", (W, H + legend.height), (255, 255, 255))
        out.paste(block, (0, 0))
        out.paste(legend, (0, H))
        what = name.split(" | ")[0]   # cycle 3: which of ours the block holds (a Reel's first frame says so first)
        if what.startswith("REEL FIRST FRAME"):
            what = f"{what}: {name.split(' | ')[1]}"
        tiles.append((f"{spec['title']} at {spec['w']} px | ours: slot {mid + 1}, {what} | {block_note}", out))
    return tiles, note
