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
import re
from pathlib import Path
from typing import Any

REAL, STAND_IN, FORCED = "REAL", "STAND-IN", "FORCED"


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
    so the board process records them (replay) where the one-process board would have."""
    n = len(REFUSED)
    value = fn(*a, **k)
    return value, REFUSED[n:]


def replay(future):
    """A captured call's value; its refusals appended to REFUSED now (an exception in the worker is raised here)."""
    value, refused = future.result()
    REFUSED.extend(refused)
    return value


def refusal_tile(surface: str, reason: str, size=(960, 540)):
    """A grey tile that says the surface was refused and why (its row shows the refusal, never a gap)."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", size, (70, 70, 70))
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=26)
    words, lines, cur = f"REFUSED  {surface}:  {reason}".split(), [], ""
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
RICHAT_COPY = ("myth_richat", "chapter_act1", "chapter_act4", "hook")
# names whose kind is a probe by construction: each must match a STAND-IN / FORCED rule or a stated REAL rule
PROBE_PREFIXES = ("card__", "callouts__", "captions__", "route__", "timeline_dense__", "cross_section__",
                  "cover_badge__", "thumbnail_badge__", "endscreen__")


def _endcard_labels() -> dict[int, str]:
    try:
        import fixtures as fx
        return {i: c.get("label") or "" for i, c in enumerate(fx.load("endcard"))}
    except Exception:  # noqa: BLE001
        return {}


def pairing(name: str) -> tuple[str, str]:
    """(class, why) for a tile name."""
    n = name.split(" | ")[0]
    m = re.search(r"__copy_(\w+)$", n)   # round 8i (N4): board_extras.STANDIN_COPY_TAG, a refused surface's probe
    if m:
        forced = " (the illustration flag forced on)" if n.startswith(("cover_badge__", "thumbnail_badge__")) else ""
        if m.group(1) == "none":
            return STAND_IN, f"cross-run copy probe{forced}: no board hero's copy passes cover_fit for this surface"
        return STAND_IN, (f"cross-run copy: run {m.group(1)}'s verified copy on this ground{forced}, a board hero's "
                          "copy that passes cover_fit for this surface, taken in board order (the run's own copy is "
                          "refused)")
    m = re.match(r"card__(\w+?)__(richat|amber|obelisk)__", n)
    if m:
        maker, g = m.groups()
        if maker == "myth_long_et":
            return STAND_IN, f"test stress copy (LONG_ET) on the {CARD_GROUNDS[g]} photo"
        if maker in RICHAT_COPY or maker.startswith("hook"):
            if g == "richat":
                return REAL, "the Richat card copy on the Richat photo (tests' committed card fields)"
            return STAND_IN, f"the Richat card copy on the {CARD_GROUNDS[g]} photo (cross-run pairing)"
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
    if "antikythera" in n:
        return STAND_IN, "the Antikythera photo with the Aswan run's copy (no Antikythera run)"
    m = re.match(r"(endscreen|endcard|bridge|bridge_follow)__(\d+)_", n)
    if m:
        lab = _endcard_labels().get(int(m.group(2)), "")
        if "stands in" in lab:
            return STAND_IN, "the run's own landscape last-scene photo stands in for a 16:9 narration's last frame"
        return REAL, "the run's own last frame and share line"
    if n.startswith(PROBE_PREFIXES):
        return "UNCLASSIFIED", "a probe kind with no pairing rule"
    return REAL, ""


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
    try:
        with using(0.0):
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
    from src.video_composer import never_end_end
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
        ends = []
        for ln in b["lines"]:
            forms = [spoken_form(w) for w in ln["text"].split()]
            ends.append(forms[-1] if forms else "")
        last_word = ends[-1] if ends else ""
        lines = []
        for k, ln in enumerate(b["lines"]):
            end = ends[k]
            # never_end: the derived set (brand_system.NEVER_END_WORDS); dangling: the breakers' own burst-end
            # predicate (video_composer.never_end_end: that set with DANGLING_ENDS, which holds WHEN), read at a line
            lines.append({**ln, "chars": len(ln["text"]), "ends_on": bare(end), "never_end": line_never_end(end),
                          "dangling": never_end_end(end), "burst_end": k == len(b["lines"]) - 1})
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
    rows += extra
    return rows


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
}
PEER_SLOTS = 8


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


def peer_feed(feed: str, ours: list[tuple[str, Any]], pool: list[tuple[str, Any]], peers_dir: Path | None):
    """(tiles for board.sheet, note): per our tile, a mocked feed block of PEER_SLOTS + 1 tiles with ours in the
    middle, at real size. Peers from peers_dir/<feed>/ when it holds images, else the channel's other runs (pool),
    labelled STAND-IN."""
    from PIL import Image, ImageDraw, ImageFont
    spec = FEEDS[feed]
    supplied = []
    if peers_dir and (peers_dir / feed).is_dir():
        supplied = [(p.name, Image.open(p).convert("RGB")) for p in sorted((peers_dir / feed).iterdir())
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    note = (f"PEERS: {len(supplied)} supplied posts ({peers_dir / feed})" if supplied else
            "PEER SLOTS: STAND-IN, the channel's own other runs (no peer posts supplied: board.py --peers "
            f"<dir> with <dir>/{feed}/*.jpg)")
    tiles = []
    f = ImageFont.load_default(size=14)
    for name, im in ours:
        peers = supplied or [(n, p) for n, p in pool if _topic(n) != _topic(name)]
        if not peers:
            continue
        slots = [feed_tile(peers[k % len(peers)][1], feed) for k in range(PEER_SLOTS)]
        names = [peers[k % len(peers)][0] for k in range(PEER_SLOTS)]
        mid = PEER_SLOTS // 2
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
        tiles.append((f"{spec['title']} at {spec['w']} px | ours: slot {mid + 1} | {note}", out))
    return tiles, note
