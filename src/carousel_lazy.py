"""Carousels drawn only when they are previewed or take a carousel slot (QF-81).

Every approved episode used to draw its carousel twice (the Instagram copy and Facebook's grid copy, with the
photo match and the per-slide vision look of the quality audit) while the run was being made, although the
calendar has three carousel slots a week (CAROUSEL_DAYS) and a carousel waits at most CAROUSEL_MAX_AGE_DAYS: most
of them expired unposted (review 2026-11-05, QF-81). Now:

- While the run is made, `plan` records what its carousel will be drawn from (carousel_plan.json): the topic's own
  photos and the diagram posters, each with its SHA-256, and a digest of the script fields the drawing reads
  (DRAW_FIELDS).
- `draw` draws both copies into the run's staging folder (STAGE; never carousel/, which the review fingerprint binds
  and the publisher posts from), with a strict photo match (a failed match holds; photos are never rotated in
  silently). It audits them as the run's quality audit and release gate did: the slide audit
  (QualityAuditor.audit_carousel_slides, the per-slide vision look) on the Instagram copy, and the delivered text
  contrast of both copies (video_qa.still_contrast_problems, major under brand.TEXT_CONTRAST, as
  text_contrast_audit), held by the release gate's own severity rule (`blocking`). Its glyph fallbacks go to the
  run's glyph ledger (font_engine.glyph_ledger_for_thread). The result, with each slide's alt text, the vision look's
  findings, the Facebook collage notes and the glyph notes, is kept in STAGE/draw.json, bound by `digest` to the
  plan, the photos, the posters, the fields and the drawing code: a "Preview carousel" tap on the review card and the
  slot reuse it while nothing changed, so it is drawn and paid for once.
- At the slot, `render` installs an unheld drawing into carousel/ and carousel_fb/ and returns the slides to post.
  A held drawing is never installed: its slides stay in STAGE beside the record that says why, for the preview and
  the hold alert, until "Redraw" (`discard`) or a new make of the run clears them.

The slides' words are the run's checked carousel cards (script_payload["carousel_slides"]); the caption is still held
to the review card's (publishable.carousel_post_base). Facebook's grid copy is drawn as before: whether Facebook gets
a gallery at all is the operator's decision once twelve real carousels are measured (rulings 2026-11-10, Q4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PLAN = "carousel_plan.json"
STAGE = "carousel_draw"
RECORD = "draw.json"
SHEET = "preview_sheet.jpg"
COPIES = ("carousel", "carousel_fb")
MIN_STILLS = 1   # the fewest photos of the topic a deck is drawn from (the photo match cycles what it has)
SLIDES = "carousel_slide_*.jpg"
COPY_FILES = ("still_layout.json", "collage_report.json", "collage_board.jpg")
DRAW_FIELDS = ("carousel_slides", "poll", "narration_script", "share_line")   # what generate_carousel_sequence reads
CODE = ("src/multi_format.py", "src/typography.py", "src/still_layout.py", "src/safe_zones.py", "src/config.py",
        "src/script_engine.py", "src/visual_engine.py", "src/still_vision.py", "src/video_qa.py", "src/brand.py",
        "src/font_engine.py", "src/carousel_lazy.py")


class CarouselHeld(RuntimeError):
    """The carousel cannot be posted for its slot; the message says why, in plain words; `record` is its drawing."""

    def __init__(self, reason: str, record: dict[str, Any] | None = None):
        super().__init__(reason)
        self.record = record or {}


def planned(run_dir: Path | str) -> bool:
    """True when the run's carousel is drawn on demand (a run made from QF-81 on)."""
    return (Path(run_dir) / PLAN).is_file()


def drawn(run_dir: Path | str) -> list[str]:
    """The Instagram copy's slides installed in carousel/, in order ([] before the carousel is posted)."""
    return sorted(str(p) for p in (Path(run_dir) / "carousel").glob(SLIDES))


def _sha256(path: Path) -> str:
    from .credits import sha256_file
    return sha256_file(path)


def _fields(payload: dict[str, Any]) -> str:
    text = json.dumps({k: payload.get(k) for k in DRAW_FIELDS}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _posters(run_dir: Path) -> dict[str, str]:
    return {p.name: _sha256(p) for p in sorted((run_dir / "graphics").glob("graphic_*.jpg"))}


def _clear_copies(run_dir: Path) -> None:
    from .still_layout import SIDECAR
    for copy in COPIES:
        folder = run_dir / copy
        for old in folder.glob(SLIDES):
            old.unlink(missing_ok=True)
        for name in {SIDECAR, *COPY_FILES}:
            (folder / name).unlink(missing_ok=True)


def discard(run_dir: Path | str) -> None:
    """Removes the staged drawing (a held carousel's slides and its record): the next draw starts afresh."""
    shutil.rmtree(Path(run_dir) / STAGE, ignore_errors=True)


def plan(run_dir: Path | str, stills: list[Path], payload: dict[str, Any]) -> dict[str, Any] | None:
    """Records, while the run is made, what its carousel will be drawn from: the photos it may use (the topic's own,
    never a context shot: image_review.only_strong), each with its SHA-256, in order; the diagram posters
    (graphics/graphic_*.jpg) with theirs; and the digest of DRAW_FIELDS. Slides an earlier make of this run folder
    drew (installed or staged) are removed. None (and no plan) for a script without carousel cards."""
    from .multi_format import is_video
    run_dir = Path(run_dir)
    _clear_copies(run_dir)
    discard(run_dir)
    (run_dir / "carousel_audit.json").unlink(missing_ok=True)   # written by the first QF-81 build
    if not payload.get("carousel_slides"):
        (run_dir / PLAN).unlink(missing_ok=True)
        return None
    photos = [Path(p) for p in stills if not is_video(Path(p))]
    record = {"version": 2, "stills": [{"path": str(p), "sha256": _sha256(p)} for p in photos if p.is_file()],
              "posters": _posters(run_dir), "fields_sha256": _fields(payload)}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / PLAN).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return record


def _read_plan(run_dir: Path) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    """The plan and its photos (path, SHA-256), in order; raises CarouselHeld when it cannot be read or is corrupt."""
    try:
        record = json.loads((run_dir / PLAN).read_text(encoding="utf-8"))
        entries = [(Path(e["path"]), str(e["sha256"])) for e in record["stills"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CarouselHeld(f"its plan ({PLAN}) cannot be read: {e}") from e
    return record, entries


def _no_deck(entries: list[tuple[Path, str]]) -> str | None:
    """Why the plan's photos cannot make a deck, or None: fewer stills than MIN_STILLS once the entries the renderer
    drops (a video, an empty path) are left out. generate_carousel_sequence draws stills only."""
    from .typography import is_video
    usable = [p for p, _ in entries if str(p) not in ("", ".") and not is_video(p)]
    if len(usable) >= MIN_STILLS:
        return None
    if not usable:
        return "its plan records no photo of the topic" + (f" (its {len(entries)} entries are not stills)"
                                                           if entries else "")
    return f"its plan records {len(usable)} photos of the topic, and its slides need {MIN_STILLS}"


def plan_problem(run_dir: Path | str) -> str | None:
    """Why an undrawn carousel (booked from its plan) cannot be drawn at all, or None (QF-81c): its plan is missing,
    unreadable or corrupt, or records too few photos to make a deck (none, as `plan` writes it when a run's scene
    media are all video or none is on disk). A Redraw draws from the same plan, so it cannot help."""
    run_dir = Path(run_dir)
    if not planned(run_dir):
        return f"its plan ({PLAN}) is missing, so its slides cannot be drawn"
    try:
        _, entries = _read_plan(run_dir)
    except CarouselHeld as e:
        return str(e)
    return _no_deck(entries)


def _checked_stills(run_dir: Path, payload: dict[str, Any]) -> list[Path]:
    """The plan's photos, after checking that nothing it recorded changed; raises CarouselHeld otherwise."""
    record, entries = _read_plan(run_dir)
    no_deck = _no_deck(entries)
    if no_deck:
        raise CarouselHeld(no_deck)
    for path, digest in entries:
        if not path.is_file():
            raise CarouselHeld(f"its photo {path.name} is no longer on disk")
        if _sha256(path) != digest:
            raise CarouselHeld(f"its photo {path.name} changed since the run was made")
    if "posters" in record:   # plans of the first QF-81 build carry neither
        now = _posters(run_dir)
        changed = sorted(n for n in set(now) | set(record["posters"]) if now.get(n) != record["posters"].get(n))
        if changed:
            raise CarouselHeld(f"its diagram poster {changed[0]} changed since the run was made")
    if "fields_sha256" in record and record["fields_sha256"] != _fields(payload):
        raise CarouselHeld("its carousel cards changed since the run was made")
    return [path for path, _ in entries]


def _code_digest(root: Path) -> dict[str, str]:
    from .typography import FALLBACK_FILES, FONT_FILES, FONTS_DIR
    files = [root / name for name in CODE] + [FONTS_DIR / n for n in (*FONT_FILES.values(), *FALLBACK_FILES.values())]
    return {str(p.name): _sha256(p) for p in files if p.is_file()}


def digest(run_dir: Path | str, payload: dict[str, Any]) -> str:
    """What a drawing is bound to: the plan (photos, posters, fields), the photos and posters as they are now, the
    script fields and the drawing code and fonts. A changed one of them means a new drawing."""
    run_dir = Path(run_dir)
    root = Path(__file__).resolve().parent.parent
    parts = {"plan": _sha256(run_dir / PLAN), "fields": _fields(payload), "posters": _posters(run_dir),
             "code": _code_digest(root)}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def blocking(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The audit findings that hold the carousel: the release gate's rule (release.release_report,
    visual_checks): critical and major always, minor when MINOR_FINDINGS_BLOCK_RELEASE is on, info never."""
    from . import config
    found = [i for i in issues if isinstance(i, dict) and i.get("severity") != "info"]
    return [i for i in found if i.get("severity") != "minor" or config.MINOR_FINDINGS_BLOCK_RELEASE]


def contrast_issues(stage: Path) -> list[dict[str, Any]]:
    """Both copies' delivered text contrast, as video_qa.text_contrast_audit raises it for the release gate: major
    under brand.TEXT_CONTRAST, minor when a record cannot be read."""
    from .video_qa import still_contrast_problems
    found = [f for copy in COPIES for f in still_contrast_problems(stage / copy)]
    return [{"type": "text_contrast", "severity": "minor" if "unreadable" in f else "major", "issue": f}
            for f in found]


def _glyph_notes(ledger: Path, start: int) -> list[str]:
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()[start:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("part_of"):
            continue
        what = {"fallback": f"set in {ev.get('fallback')}", "refused": "refused (no bundled face draws it)",
                "emoji_removed": "emoji removed"}.get(ev.get("kind"), str(ev.get("kind")))
        text = " / ".join(" ".join(x.split()) for x in str(ev.get("text") or "").splitlines() if x.strip())
        note = f"{ev.get('where') or 'carousel'}: “{text[:80]}” {what} ({' '.join(str(ev.get('chars') or ''))})"
        if note not in out:
            out.append(note)
    return out


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _sheet(stage: Path) -> None:
    """A contact sheet of both copies (Instagram above, Facebook's grid copy below) for the preview panel."""
    from PIL import Image
    rows = [sorted((stage / copy).glob(SLIDES)) for copy in COPIES]
    rows = [r for r in rows if r]
    if not rows:
        return
    w, h, gap = 240, 300, 8
    n = max(len(r) for r in rows)
    sheet = Image.new("RGB", (n * w + (n + 1) * gap, len(rows) * h + (len(rows) + 1) * gap), (24, 24, 24))
    for y, row in enumerate(rows):
        for x, p in enumerate(row):
            with Image.open(p) as im:
                sheet.paste(im.convert("RGB").resize((w, h), Image.LANCZOS), (gap + x * (w + gap), gap + y * (h + gap)))
    sheet.save(stage / SHEET, quality=85)


def _write(stage: Path, record: dict[str, Any]) -> dict[str, Any]:
    stage.mkdir(parents=True, exist_ok=True)
    (stage / RECORD).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str), encoding="utf-8",
                                newline="\n")
    return record


def load(run_dir: Path | str) -> dict[str, Any] | None:
    try:
        return json.loads((Path(run_dir) / STAGE / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _intact(stage: Path, record: dict[str, Any]) -> bool:
    for copy, slides in (record.get("slides") or {}).items():
        for s in slides:
            path = stage / copy / s["name"]
            if not path.is_file() or _sha256(path) != s["sha256"]:
                return False
    return bool(record.get("slides"))


def draw(run_dir: Path | str, payload: dict[str, Any], generator: Any, auditor: Any, reuse: bool = True) -> dict[str, Any]:
    """Both copies drawn into STAGE and audited (module docstring), or the drawing kept there for the same digest
    (`reuse`). Returns its record: "held" (the reason, "" when it may post), "issues", "held_by", "notes" (look,
    collage, glyphs, facebook_copy), "alts" per copy, "slides" and "photos" (the photos the slides use, with their
    SHA-256). A drawing that failed for a reason a new attempt may not meet (a photo match, an exception, an audit
    that could not run) is recorded but never reused."""
    from .font_engine import LEDGER_NAME, glyph_ledger_for_thread, on_surface
    from .multi_format import CAROUSEL_PHOTOS, PhotoMatchFailed, slide_alt_text
    from .typography import LayoutError
    run_dir = Path(run_dir)
    stage = run_dir / STAGE
    record: dict[str, Any] = {"version": 1, "drawn_at": datetime.now(UTC).isoformat(), "held": "", "cacheable": True,
                              "issues": [], "held_by": [], "notes": {}, "slides": {}, "photos": [], "alts": {}}
    try:
        stills = _checked_stills(run_dir, payload)
    except CarouselHeld as e:
        return {**record, "held": str(e), "cacheable": False}
    record["digest"] = digest(run_dir, payload)
    kept = load(run_dir) if reuse else None
    if kept and kept.get("cacheable") and kept.get("digest") == record["digest"] and _intact(stage, kept):
        return kept
    discard(run_dir)
    stage.mkdir(parents=True)
    graphics = sorted((run_dir / "graphics").glob("graphic_*.jpg"))   # the run's posters (default: <output>/graphics)
    ledger = run_dir / LEDGER_NAME
    before = _line_count(ledger)
    with glyph_ledger_for_thread(ledger):
        try:
            slides = on_surface("carousel", generator.generate_carousel_sequence, stills, payload, stage,
                                graphics=graphics, strict_match=True)
        except PhotoMatchFailed as e:
            return _write(stage, {**record, "held": str(e), "cacheable": False})
        except LayoutError as e:   # QF-23: checked copy no permitted layout sets whole is held, never drawn cut
            return _write(stage, {**record, "held": f"its text cannot be set whole ({e})", "cacheable": False,
                                  "layout_hold": True})
        except Exception as e:  # noqa: BLE001 - any failure to draw holds the carousel, in plain words
            return _write(stage, {**record, "held": f"its slides could not be drawn: {e}", "cacheable": False})
        if not slides:
            return _write(stage, {**record, "held": "no slide was drawn", "cacheable": False})
        try:
            on_surface("Facebook carousel", generator.generate_carousel_sequence, stills, payload, stage,
                       facebook=True, graphics=graphics, strict_match=True)
        except Exception as e:  # noqa: BLE001 - as before QF-81: any failure leaves Facebook the Instagram slides
            # The frames it saved before it stopped (a typography.LayoutError, QF-23, refuses mid-deck) are removed,
            # so a partial Facebook copy is never installed, audited or credited (main.hold_layout_error's rule).
            shutil.rmtree(stage / "carousel_fb", ignore_errors=True)
            why = "its text cannot be set whole" if isinstance(e, LayoutError) else "it could not be drawn"
            log.warning("Facebook carousel slides not made (%s); Facebook gets the Instagram slides.", e)
            record["notes"]["facebook_copy"] = f"Facebook's copy was not made, {why} ({e}): Facebook gets the Instagram slides"
        try:
            audit = auditor.audit_carousel_slides(stage / "carousel", payload=payload)
        except Exception as e:  # noqa: BLE001 - an audit that cannot run holds the carousel: nothing unaudited posts
            return _write(stage, {**record, "held": f"its audit could not run: {e}", "cacheable": False})
    issues = [i for i in (audit.get("issues") or []) if isinstance(i, dict)] + contrast_issues(stage)
    record["issues"], record["held_by"] = issues, blocking(issues)
    record["notes"]["look"] = [str(i.get("issue")) for i in issues if i.get("severity") == "info"]
    try:
        report = json.loads((stage / "carousel_fb" / "collage_report.json").read_text(encoding="utf-8"))
        record["notes"]["collage"] = [str(p) for p in report.get("problems") or []]
    except (OSError, ValueError):
        record["notes"]["collage"] = []
    record["notes"]["glyphs"] = _glyph_notes(ledger, before)
    for copy in COPIES:
        files = sorted((stage / copy).glob(SLIDES))
        if files:
            record["slides"][copy] = [{"name": p.name, "sha256": _sha256(p)} for p in files]
            record["alts"][copy] = [slide_alt_text(p) for p in files]
    try:
        used = json.loads((stage / CAROUSEL_PHOTOS).read_text(encoding="utf-8")).get("photos") or []
        record["photos"] = [{"path": str(p), "sha256": _sha256(Path(p))} for p in used if Path(p).is_file()]
    except (OSError, ValueError):
        pass
    if record["held_by"]:
        record["held"] = "its audit found " + "; ".join(str(i.get("issue") or i.get("type"))
                                                        for i in record["held_by"][:3])
    try:
        _sheet(stage)
    except OSError as e:
        log.warning("Carousel preview sheet not made (%s).", e)
    return _write(stage, record)


def install(run_dir: Path | str, record: dict[str, Any]) -> list[str]:
    """Copies an unheld drawing's two copies from STAGE into carousel/ and carousel_fb/ (each copy's records with
    it), checking every slide's SHA-256 against the record; returns the Instagram slides there."""
    run_dir = Path(run_dir)
    stage = run_dir / STAGE
    if record.get("held") or not _intact(stage, record):
        raise CarouselHeld(record.get("held") or "its drawing changed after it was checked", record)
    _clear_copies(run_dir)
    try:
        for copy in COPIES:
            source = stage / copy
            if not source.is_dir():
                continue
            (run_dir / copy).mkdir(parents=True, exist_ok=True)
            for f in source.iterdir():
                if f.is_file():
                    shutil.copyfile(f, run_dir / copy / f.name)
    except OSError:   # never half a carousel where the publisher posts from; the caller holds it with the reason
        try:
            _clear_copies(run_dir)
        except OSError as e:
            log.warning("A partly installed carousel was not cleared (%s).", e)
        raise
    return drawn(run_dir)


def render(run_dir: Path | str, payload: dict[str, Any], generator: Any, auditor: Any) -> list[str]:
    """The carousel for the slot it has taken: the drawing (reused when previewed and nothing changed), installed
    when nothing holds it; raises CarouselHeld (with the record) otherwise."""
    record = draw(run_dir, payload, generator, auditor)
    if record.get("held"):
        raise CarouselHeld(record["held"], record)
    return install(run_dir, record)


def summary(record: dict[str, Any], limit: int = 900) -> str:
    """The drawing's findings for the operator, plain lines: what holds it, then the vision look's findings, the
    Facebook collage notes, the glyph notes and a Facebook copy that could not be drawn."""
    notes = record.get("notes") or {}
    lines = [f"• {i.get('issue') or i.get('type')}" for i in record.get("held_by") or []]
    lines += [f"• look: {x}" for x in notes.get("look") or []]
    lines += [f"• Facebook collage: {x}" for x in notes.get("collage") or []]
    lines += [f"• lettering: {x}" for x in notes.get("glyphs") or []]
    if notes.get("facebook_copy"):
        lines.append(f"• {notes['facebook_copy']}")
    out, used = [], 0
    for line in lines:
        if used + len(line) > limit:
            out.append(f"• … and {len(lines) - len(out)} more (carousel_draw/{RECORD})")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)
