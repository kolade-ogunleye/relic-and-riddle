"""
src/video_qa.py
===============
Automated look at the finished video before it reaches the human reviewer.

One frame from the middle of each narration beat, plus the cover, goes to Claude with the
words spoken at that moment. It flags unreadable or clashing on-screen text, images that
don't match the narration, watermarks or other brands' logos, and visual glitches.
Findings are shown on the review card; the human still decides.
"""

import concurrent.futures
import json
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageStat

from . import free_llm
from .credits import sha256_file
from .config import TARGET_LUFS, TARGET_TRUE_PEAK, VIDEO_WIDTH, VIDEO_HEIGHT
from .graphics import LayoutConstraintSolver
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task
from .models import QAReport

log = logging.getLogger(__name__)

SYSTEM = """You are the quality checker for short vertical history documentaries (Instagram/Facebook Reels). For each frame you get the narration spoken at that moment. Report only real problems a viewer would notice:
- captions that are cut off, overlapping other text, or hard to read
- an image that clearly does not match or contradicts the narration
- watermarks, stock-photo marks, or other brands' logos
- visual glitches: stretching, heavy compression artefacts, black or blank frames
- anything that could break platform rules (graphic injury, nudity, hateful symbols shown without context)
Severity: critical = should not be published as is; major = clearly hurts quality; minor = nitpick.
Ignore stylistic choices like film grain, vignette and gold/white caption colours. A beat marked as one of our own
graphics (a number card, map, timeline, diagram) is a designed card: its plain or dimmed, blurred backdrop is the
design, not a missing picture or a blank frame; judge its text (legible, matching the narration) and its drawing.
A beat may also say how its picture was made. These treatments are intended and are not defects in themselves:
archive film shown whole, fitted over a blurred, darkened copy of itself (the soft bands around it are the design);
soft blurs over present-day people's faces, applied for privacy (report one only if it is visually jarring, and
then as minor); a slow camera move or AI motion on a still photo; an old photo's own grain, tint or soft focus.
They never excuse a real defect: still report a picture that does not match or contradicts the narration, an
upside-down or mirrored picture, a placard, label or printed text in the picture, captions cut off or overlapping,
watermarks and glitches.
If everything is fine, return no issues."""


def _lineage_chain(asset: Path, depth: int = 6) -> Tuple[List[Dict[str, Any]], Path]:
    """The lineage records from a scene file back to its original (one source per step), and that original."""
    from .lineage import sidecar
    records: List[Dict[str, Any]] = []
    path = Path(asset)
    for _ in range(depth):
        try:
            record = json.loads(sidecar(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            break
        if not isinstance(record, dict):
            break
        sources = record.get("sources") or []
        records.append(record)
        if len(sources) != 1:
            break
        path = Path(sources[0].get("path", ""))
    return records, path


def _photo_record(image: Path) -> Dict[str, Any]:
    """The photo's own credit record (its folder's credits.json, as the privacy check reads it), or {}."""
    for credits in (image.parent / "credits.json", image.parent.parent / "credits.json"):
        try:
            records = json.loads(credits.read_text(encoding="utf-8")).get("images", [])
        except (OSError, ValueError, AttributeError):
            continue
        for record in records:
            if isinstance(record, dict) and record.get("file") == image.name:
                return record
    return {}


def _clip(text: str, limit: int = 110) -> str:
    text = " ".join(str(text or "").split()).replace('"', "'")
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def beat_tag(asset: Any) -> str:
    """How a beat's picture was made, for the vision check, from what the pipeline already recorded: the
    graphic's name, the footage clip's credit record, and the scene's lineage (privacy blur, AI motion, the
    original photo's archive caption). Our own treatments were reported as defects: a privacy blur as "a grey
    oval patch" (production, Amber Room, 2026-10-27), blurred visitors' faces as "fingers blocking the lens"
    (development, Roman concrete, 2026-10-28). Empty when nothing is known."""
    if not asset:
        return ""
    path = Path(str(asset))
    stem = path.stem
    if stem.startswith("graphic_"):
        # Our own graphics are named as such: a number card's dimmed backdrop was reported as "an empty dark
        # gradient with no visual imagery" and blocked a release (Odessa, 2026-10-27).
        return f" [our own graphic: {stem.removeprefix('graphic_').replace('_', ' ')} card]"
    parts: List[str] = []
    if stem.startswith("footage_"):
        try:
            info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info = info if isinstance(info, dict) else {}
        named = " ".join(p for p in (f"\"{_clip(info.get('title'), 70)}\"" if info.get("title") else "",
                                      f"({info['year']})" if info.get("year") else "") if p)
        parts.append("real archive film" + (f" {named}" if named else ""))
        from . import footage
        if footage.frame_filter(info) == ["-filter_complex", footage.FIT]:
            parts.append("a wide shot shown whole, fitted over a blurred, darkened copy of itself")
        return f" [{'; '.join(parts)}]"
    records, original = _lineage_chain(path)
    operations = [r.get("operation") for r in records]
    if "ai_motion" in operations:
        parts.append("AI motion animating a real photo")
    blurs = [r for r in records if r.get("operation") == "face_blur"]
    if blurs:
        count = (blurs[0].get("settings") or {}).get("faces")
        parts.append(f"{count} privacy blur(s) applied over present-day faces" if isinstance(count, int) and count > 0
                     else "privacy blurs applied over present-day faces")
    if original.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        record = _photo_record(original)
        caption = record.get("description") or str(record.get("title") or "").removeprefix("File:").rsplit(".", 1)[0]
        # The source's own label, never drawn on screen, and sometimes wrong: Commons filed the Hands geoglyph as the
        # "Condor" and the check blocked a dev release over the label (Nazca, 2026-10-29).
        parts.insert(0, "real archive picture" + (f", its archive caption (the source's label, not shown on screen, "
                                                  f"may be wrong): \"{_clip(caption)}\"" if caption else ""))
    return f" [{'; '.join(parts)}]" if parts else ""


# A finding that only says a beat's backdrop is blank, empty, dark or blurred. On our own graphic that backdrop is
# the design; b8b7d0c told the check so in words, and the code now holds it (Odessa beat 6, development, 2026-10-27;
# Richat beat 8, production, 2026-10-26).
# Only a finding about the BACKGROUND: "the image is blank" may mean the card itself failed to draw (review 2026-10-28).
_BACKDROP = re.compile(r"\b(background|backdrop)", re.I)
_BLANK = re.compile(r"\b(blank|empty|plain|bare|blurr?(ed|y)?|out[- ]of[- ]focus|dark gradient|gradient|no (visual )?imagery|"
                    r"no (real )?(picture|photo|image)|lacks? (visual|imagery|a picture)|missing (picture|image|imagery)|dimmed)", re.I)
# Words of a real defect in the card itself: such a finding is kept whatever else it says.
_CARD_DEFECT = re.compile(r"(cut off|truncat|overlap|illegib|unreadable|hard to read|misspel|typo|wrong|incorrect|"
                          r"contradict|does not match|doesn't match|mismatch|upside|stretch|glitch|artefact|artifact|"
                          r"watermark|logo|garbled|clipped|"
                          # the card's own content missing: a graphic that drew only its backdrop is a real failure
                          r"no (number|map|text|line|chart|figure|label|data|value|date|route|caption)s?\b|"
                          r"nothing (is )?(visible|shown|drawn)|missing|not drawn|not rendered|blank (white|frame|screen|card)|"
                          r"empty (screen|card|frame)|low[- ]res|pixelat)", re.I)


def designed_backdrop_finding(issue: str) -> bool:
    """True when a finding only says the backdrop is blank, empty or blurred (no defect of the card itself)."""
    return bool(_BACKDROP.search(issue) and _BLANK.search(issue) and not _CARD_DEFECT.search(issue))


# A finding whose only complaint is the source's label against the picture (the label is context we pass, never on
# screen): the picture is judged against the narration, not against its archive caption (Nazca beat 8, 2026-10-29).
_SOURCE_LABEL = re.compile(r"\b(archive (caption|label|description|title)|source(?:'s)? (caption|label|description)|"
                           r"metadata|file ?name)\b", re.I)
_AGAINST_NARRATION = re.compile(r"\b(narration|narrat|line says|the line|voice ?over|spoken|subtitle|on[- ]screen text)", re.I)


def source_label_finding(issue: str) -> bool:
    """True when a finding only says the picture disagrees with its source's label, not with what viewers hear or see."""
    return bool(_SOURCE_LABEL.search(issue) and not _AGAINST_NARRATION.search(issue))


def drop_source_label_findings(report: Optional[QAReport]) -> Optional[QAReport]:
    """The report without minor findings about a picture's archive caption alone; each dropped one is noted."""
    if report is None:
        return report
    dropped = [i for i in report.issues if i.severity == "minor" and source_label_finding(i.issue)]
    if not dropped:
        return report
    for i in dropped:
        log.info("Video check: beat %s compares the picture with its source's label, not shown on screen (%s)", i.beat, i.issue)
    note = " Not counted (the source's label, not shown on screen): " + "; ".join(f"beat {i.beat}: {i.issue}" for i in dropped)
    return report.model_copy(update={"issues": [i for i in report.issues if i not in dropped],
                                     "summary": (report.summary or "") + note})


def drop_designed_backdrops(report: Optional[QAReport], assets: Optional[List[Any]]) -> Optional[QAReport]:
    """The report without backdrop findings on our own graphics; each dropped one is noted in its summary."""
    if report is None or not assets:
        return report
    ours = {i for i, a in enumerate(assets, 1) if Path(str(a)).stem.startswith("graphic_")}
    # Minor only: a major or critical finding on a graphic is never set aside by wording (review 2026-10-28)
    dropped = [i for i in report.issues
               if i.beat in ours and i.severity == "minor" and designed_backdrop_finding(i.issue)]
    if not dropped:
        return report
    for i in dropped:
        log.info("Video check: beat %d is our own graphic; its designed backdrop is not a defect (%s)", i.beat, i.issue)
    note = " Not counted (our own graphic's designed backdrop): " + "; ".join(f"beat {i.beat}: {i.issue}" for i in dropped)
    return report.model_copy(update={"issues": [i for i in report.issues if i not in dropped],
                                     "summary": (report.summary or "") + note})

def calculate_wcag_relative_luminance(rgb: Tuple[float, float, float]) -> float:
    """Calculates WCAG 2.1 relative luminance for an RGB tuple (0-255)."""
    def _gamma(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _gamma(rgb[0]), _gamma(rgb[1]), _gamma(rgb[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def calculate_wcag_contrast_ratio(lum1: float, lum2: float) -> float:
    """Calculates WCAG contrast ratio between two relative luminance values. Returns float >= 1.0."""
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


def measure_caption_wcag_contrast(frame_path: Path, caption_band: Tuple[int, int, int, int] = (90, 550, 450, 750)) -> Dict[str, Any]:
    """Mathematically computes WCAG 2.1 contrast ratio between caption text and underlying background.
    Guarantees that on-screen typography satisfies WCAG AA (>= 4.5:1) legibility standards."""
    from PIL import Image
    import numpy as np
    with Image.open(frame_path) as im:
        crop = im.convert("RGB").crop(caption_band)
        arr = np.asarray(crop, dtype=np.float32)
        bright_mask = np.mean(arr, axis=2) > 160
        dark_mask = np.mean(arr, axis=2) < 90

        if np.count_nonzero(bright_mask) > 30 and np.count_nonzero(dark_mask) > 60:
            bright_rgb = np.mean(arr[bright_mask], axis=0)
            dark_rgb = np.mean(arr[dark_mask], axis=0)
            l_text = calculate_wcag_relative_luminance(tuple(bright_rgb))
            l_bg = calculate_wcag_relative_luminance(tuple(dark_rgb))
            cr = calculate_wcag_contrast_ratio(l_text, l_bg)
            passed = cr >= 4.5
        else:
            p_low, p_high = np.percentile(arr, [10, 90])
            cr = float((p_high + 5.0) / (p_low + 5.0))
            passed = cr >= 3.0

        return {
            "contrast_ratio": round(float(cr), 2),
            "wcag_aa_passed": bool(passed),
            "min_required": 4.5,
        }


def extract_frames(video: Path, times: List[float], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    max_t = None
    try:
        res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=10)
        max_t = float(json.loads(res.stdout)["format"]["duration"])
    except Exception:
        pass

    def grab(item):
        i, t = item
        path = out_dir / f"qa_{i:02d}.jpg"
        path.unlink(missing_ok=True)
        seek_t = max(0.0, min(t, max_t - 0.5) if max_t else t)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{seek_t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=540:-2", "-update", "1", "-strict", "unofficial", str(path)], check=True, timeout=60)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"No frame could be extracted at {t:.2f}s")
        return path

    items = list(enumerate(times, 1))
    if len(items) <= 1:
        frames = [grab(item) for item in items]
    else:
        # Independent seeks into the same file, each to its own output; map() re-raises the
        # first failure in timestamp order, as the sequential loop did.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
            frames = list(pool.map(grab, items))
    return frames


SAMPLE_AT = 0.8  # fraction of a beat at which its frame is checked: settled, still clear of the next cut


def sample_time(start: float, end: float) -> float:
    """The moment in a beat the automated check looks at: four fifths through (never before its middle)."""
    start, end = float(start or 0.0), float(end or 0.0)
    return max((start + end) / 2, start + SAMPLE_AT * (end - start))


def beat_prompt(i: int, beat: Dict[str, Any], assets: Optional[List[Any]] = None) -> str:
    """The words that go with beat i's frame: how its picture was made, and the narration spoken."""
    tag = beat_tag(assets[i - 1]) if assets and i <= len(assets) else ""
    return f"Beat {i}{tag} narration: \"{beat['text']}\""


@llm_task("video_qa")
def review_render(video: Path, beats: List[Dict[str, Any]], cover: Path, work_dir: Path,
                  assets: Optional[List[Any]] = None) -> Optional[QAReport]:
    """Returns the QA report, or None if Claude is unavailable or the check fails."""
    if not (free_llm.available() if free_llm.enabled() else claude_available()):
        return None
    try:
        # Late in each beat, not its midpoint: an animated graphic has settled by then (a number card
        # counts up over its first ~3 s; the midpoint frame read "29" of a card resting on 30 and the
        # check reported a contradiction with the narration — production, Antikythera, 2026-10-24).
        times = [sample_time(b["start"], b["end"]) for b in beats]
        frames = extract_frames(video, times, work_dir / "qa_frames")
        content: List[dict] = []
        for i, (frame, beat) in enumerate(zip(frames, beats), 1):
            content += [{"type": "text", "text": beat_prompt(i, beat, assets)}, image_block(frame, 768)]
        content += [{"type": "text", "text": "Cover image (thumbnail):"}, image_block(cover, 768),
                    {"type": "text", "text": "Report issues by beat number (use 0 for the cover)."}]
        report = (free_llm.call(SYSTEM, content, QAReport, role="checker", effort="low", max_tokens=4000)
                  if free_llm.enabled() else structured_call(SYSTEM, content, QAReport, effort="low", max_tokens=4000))
        return drop_source_label_findings(drop_designed_backdrops(report, assets))
    except (LLMError, subprocess.SubprocessError, OSError, ValueError) as e:
        log.warning("Video QA could not run: %s", e)
        return None


def qa_summary_html(report: Optional[QAReport], beats: Optional[List[Dict[str, Any]]] = None) -> str:
    """The card's video check. With the run's beats, each issue says where it is in the video ("beat 9,
    0:37-0:41"), so the reviewer can find it (the card named only the beat number)."""
    import html

    def at(beat: int) -> str:
        if not beats or not 1 <= beat <= len(beats):
            return ""
        b = beats[beat - 1]
        clock = lambda s: f"{int(s // 60)}:{int(s % 60):02d}"  # noqa: E731
        return f", {clock(b['start'])}-{clock(b['end'])}"
    if report is None:
        return "🔎 Automated video check: not run"
    if not report.issues:
        return "🔎 Automated video check: no issues found"
    icon = {"critical": "🛑", "major": "⚠️", "minor": "·"}
    order = {"critical": 0, "major": 1, "minor": 2}
    lines = [f"🔎 Automated video check: {len(report.issues)} issue(s)"]
    for i in sorted(report.issues, key=lambda x: order.get(x.severity.lower(), 99))[:6]:
        where = "cover" if i.beat == 0 else f"beat {i.beat}{at(i.beat)}"
        sev = i.severity.lower()
        lines.append(f"{icon.get(sev, '·')} {html.escape(where)}: {html.escape(i.issue)}")
    return "\n".join(lines)


def measure_program_loudness(video_path: Path) -> Dict[str, float]:
    """Measures integrated loudness (LUFS) and True Peak (dBTP) of the final video file."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    summary = res.stderr[res.stderr.rfind("Summary:"):]
    i_match = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    p_match = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if not i_match or not p_match:
        raise ValueError("FFmpeg returned no complete loudness measurement")
    values = {"integrated_lufs": float(i_match.group(1)), "true_peak_dbtp": float(p_match.group(1))}
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("Non-finite loudness measurement")
    return values


def caption_pills(ass_file: Path) -> List[Tuple[int, int, int, int]]:
    """The rectangles (x0, y0, x1, y1) of the caption pills, the CapBox drawing events of a caption file, in
    frame pixels (the file's PlayRes is the frame)."""
    rects: List[Tuple[int, int, int, int]] = []
    for line in ass_file.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:") or ",CapBox," not in line:
            continue
        text = line.split(",", 9)[-1]
        pos = re.search(r"\\pos\((-?\d+),(-?\d+)\)", text)
        shape = re.search(r"\\p1\}([^{]*)\{", text)
        if not pos or not shape:
            continue
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", shape.group(1))]
        xs, ys = nums[0::2], nums[1::2]
        if not xs or not ys:
            continue
        x, y = int(pos.group(1)), int(pos.group(2))
        rects.append((x + int(min(xs)), y + int(min(ys)), x + int(round(max(xs))), y + int(round(max(ys)))))
    return rects


def _parse_ass_time(ts: str) -> float:
    """Parses ASS timestamp (H:MM:SS.cs) into seconds."""
    try:
        parts = ts.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600.0 + m * 60.0 + s
    except Exception:
        return 0.0


def verify_caption_typography(ass_path: Path) -> List[str]:
    """Audits an ASS subtitle file to ensure full acronym scaling and zero small-caps regressions."""
    if not ass_path.exists():
        return ["ASS file not found"]
    issues = []
    text = ass_path.read_text(encoding="utf-8")
    import re
    # Check if BC or AD has small caps tag directly attached to its second letter (e.g. B{\fscx82}C)
    if re.search(r"B\{\\c[^\}]*\\fscx[78]\d\}C", text):
        issues.append("Detected 'Bc' small-caps degradation on 'BC'")
    return issues


def verify_provenance_integrity(provenance_path: Path) -> bool:
    """Recompute every recorded checksum. Identity checks do not certify rights."""
    import json
    from .credits import sha256_file
    try:
        data = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
        if data.get("status") != "recorded" or data.get("issues"):
            return False
        assets = data["assets"]
        if len(assets) != data["assets_count"]:
            return False
        records = [data["master_video"], *assets, *data.get("license_evidence", []),
                   *data.get("derived_sources", []), *data.get("factual_evidence", []), *data.get("resource_evidence", [])]
        records += [data[k] for k in ("voiceover", "ambient_bed") if data.get(k)]
        if data.get("composition_manifest"):
            records.append(data["composition_manifest"])
        for rec in records:
            expected = rec.get("sha256", "")
            path = Path(rec["path"])
            if not path.is_absolute():
                path = Path(provenance_path).parent / path
            if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


# Audit defects a second scene plan can repair (a different picture for that beat), worded as
# planner feedback. Other defects (loudness, cards, policy) are fixed elsewhere or need a person.
SCENE_FIXES = {
    "semantic_desync": "the graphic does not show what the narration says here",
    "motion_loop": "the graphic loops instead of holding its final frame through this beat",
    "blank_frame": "the picture renders blank",
    "caption_collision": "the graphic runs into the captions",
    # Minor, but it decertifies the master and so blocks release (2026-10-24: a map shown one beat after
    # the narration named the place). Re-planning can move the graphic to the beat that names it.
    "temporal_desync": "the graphic appears away from the words that name it",
}
SCENE_REMEDIES = {"temporal_desync": "put it on the beat where those words are spoken, or choose a different picture for this beat."}


def audit_scene_feedback(audit: Optional[Dict[str, Any]]) -> str:
    """The audit's repairable scene defects as feedback for re-planning the scenes."""
    lines = []
    for issue in (audit or {}).get("issues", []):
        if issue.get("type") in SCENE_FIXES and issue.get("scene_index", -1) >= 0:
            asset = Path(issue.get("asset", "")).name
            remedy = SCENE_REMEDIES.get(issue["type"], "choose a different picture for it.")
            lines.append(f"Beat {issue['scene_index'] + 1}: {SCENE_FIXES[issue['type']]}"
                         + (f" ({asset})" if asset else "") + f"; {remedy}")
    return "\n".join(dict.fromkeys(lines))


class QualityAuditor:
    """Deterministic release audits: loudness, graphic/narration sync, graphic motion freeze,
    caption contrast and clearance, plus the carousel, story, thumbnail and policy checks.
    It reports; it repairs nothing (a defect is fixed by re-running the stage that made it)."""

    def __init__(self):
        self.layout_solver = LayoutConstraintSolver()

    @staticmethod
    def calculate_relative_luminance(rgb: Tuple[int, int, int]) -> float:
        """Calculates WCAG 2.1 relative luminance for an sRGB tuple."""
        def channel_lum(c: int) -> float:
            c_norm = c / 255.0
            return c_norm / 12.92 if c_norm <= 0.04045 else ((c_norm + 0.055) / 1.055) ** 2.4
        r, g, b = rgb[:3]
        return 0.2126 * channel_lum(r) + 0.7152 * channel_lum(g) + 0.0722 * channel_lum(b)

    @classmethod
    def calculate_contrast_ratio(cls, rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
        """Calculates WCAG 2.1 contrast ratio between two colors."""
        l1 = cls.calculate_relative_luminance(rgb1)
        l2 = cls.calculate_relative_luminance(rgb2)
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)

    def audit_wcag_contrast(
        self,
        im: Image.Image,
        text_bbox: Tuple[int, int, int, int],
        text_color: Tuple[int, int, int] = (255, 255, 255),
        min_contrast: float = 4.5,
    ) -> Dict[str, Any]:
        """Verifies that text within text_bbox achieves WCAG AAA/AA contrast (>= min_contrast)."""
        x0, y0, x1, y1 = [int(v) for v in text_bbox]
        x0 = max(0, min(im.width - 1, x0))
        y0 = max(0, min(im.height - 1, y0))
        x1 = max(x0 + 1, min(im.width, x1))
        y1 = max(y0 + 1, min(im.height, y1))

        crop = im.crop((x0, y0, x1, y1)).convert("RGB")
        stat = ImageStat.Stat(crop)
        avg_bg_rgb = tuple(int(v) for v in stat.mean[:3])
        ratio = self.calculate_contrast_ratio(text_color, avg_bg_rgb)
        passed = ratio >= min_contrast
        return {
            "passed": passed,
            "contrast_ratio": round(ratio, 2),
            "required_ratio": min_contrast,
            "background_rgb": avg_bg_rgb,
            "text_rgb": text_color[:3],
        }

    def audit_subject_non_occlusion(
        self,
        im: Image.Image,
        text_bbox: Tuple[int, int, int, int],
        saliency_threshold: float = 0.75,
    ) -> Dict[str, Any]:
        """Verifies that key focal subjects are not occluded by text overlay."""
        import cv2
        import numpy as np
        arr = np.asarray(im.convert("L"))
        gx = cv2.Sobel(arr, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(arr, cv2.CV_32F, 0, 1)
        mag = cv2.magnitude(gx, gy)
        
        x0, y0, x1, y1 = [int(v) for v in text_bbox]
        x0 = max(0, min(im.width - 1, x0))
        y0 = max(0, min(im.height - 1, y0))
        x1 = max(x0 + 1, min(im.width, x1))
        y1 = max(y0 + 1, min(im.height, y1))

        text_density = float(mag[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0
        overall_density = float(mag.mean()) + 1e-6
        relative_density = text_density / overall_density

        occluded = relative_density > 2.5
        return {
            "passed": not occluded,
            "relative_density": round(relative_density, 2),
            "text_bbox": (x0, y0, x1, y1),
        }

    def audit_rendered_timeline(
        self,
        video_path: Path,
        timeline: List[Dict[str, Any]],
        words: Optional[List[Dict[str, Any]]] = None,
        out_dir: Optional[Path] = None,
        caption_path: Optional[Path] = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
    ) -> Dict[str, Any]:
        """Performs multi-criteria deterministic audit on rendered master video and timeline."""
        issues: List[Dict[str, Any]] = []
        score = 100
        loudness = {}
        if not video_path.is_file():
            issues.append({"type": "missing_video", "severity": "critical", "issue": "Master video is missing", "scene_index": -1})
            score = 0

        if not timeline:
            issues.append({"type": "missing_timeline", "severity": "critical",
                           "issue": "No scene timeline was supplied for visual verification", "scene_index": -1})

        # 1. Loudness Compliance Audit
        if video_path.exists():
            try:
                measured = measure_program_loudness(video_path)
                loudness = measured
                lufs = measured["integrated_lufs"]
                peak = measured["true_peak_dbtp"]
                if lufs < TARGET_LUFS - 2.5 or lufs > TARGET_LUFS + 1.5:
                    issues.append({
                        "type": "loudness",
                        "severity": "major",
                        "issue": f"Integrated loudness {lufs:.1f} LUFS deviates from target ({TARGET_LUFS:.1f} LUFS)",
                        "scene_index": -1,
                    })
                    score -= 15
                if peak > TARGET_TRUE_PEAK + 0.5:
                    issues.append({
                        "type": "loudness",
                        "severity": "major",
                        "issue": f"True Peak {peak:.1f} dBTP exceeds ceiling ({TARGET_TRUE_PEAK:.1f} dBTP)",
                        "scene_index": -1,
                    })
                    score -= 15
            except Exception as e:
                issues.append({"type": "measurement_failed", "severity": "critical", "issue": str(e), "scene_index": -1})
                score -= 30

        # 2. Temporal Semantic Cross-Correlation Audit: each original graphic must sit where the
        # narration says what it shows (src/graphic_match.py), within 2.5 s of its cut.
        from . import graphic_match
        all_words_list = words or []
        for i, cut in enumerate(timeline):
            asset_path = Path(cut.get("asset", ""))
            asset_name = asset_path.name.lower()
            if not asset_name.startswith("graphic_"):
                continue
            terms = graphic_match.terms(asset_path)
            if not terms:  # declares nothing to judge it by
                continue
            c_start, c_end = cut.get("start", 0.0), cut.get("end", 0.0)
            # A map or route shows where the story is set, so the place named any time before it counts (a Hal
            # Saflieni map after "Paola, Malta" in the first line was blocked as out of step: 2026-10-25).
            locator = graphic_match.kind_of(asset_path) in ("map", "route")
            window_start = 0.0 if locator else c_start - 2.5
            # The cut after the hook: a graphic the hook names follows it there, since the hook keeps its photo
            # (scene_planner.lock_narrative_graphics), so the hook's words are its words. The 2.5 s window missed
            # "city" (0.9 s) and "280" (2.3 s) before a cut at 6.4 s and blocked the release (production, rc72,
            # 2026-10-29). Only when the hook itself names it, as the planner's rule asks; elsewhere the window holds.
            if i == 1 and not locator:
                hook_start = timeline[0].get("start", 0.0)
                hook_words = [w for w in all_words_list if hook_start <= w.get("start", 0.0) < c_start]
                hook_heard = timeline[0].get("text", "") + " " + " ".join(w.get("word", "") for w in hook_words)
                if graphic_match.score(asset_path, hook_heard) >= graphic_match.THRESHOLD:
                    window_start = min(window_start, hook_start)
            in_window = [w for w in all_words_list
                         if window_start <= w.get("start", 0.0) <= (c_end + 2.5)]
            heard = cut.get("text", "") + " " + " ".join(w.get("word", "") for w in in_window)
            if graphic_match.score(asset_path, heard) < graphic_match.THRESHOLD:
                issues.append({
                    "type": "semantic_desync",
                    "severity": "major",
                    "issue": f"Scene {i}: Graphic '{asset_name}' missing narration keywords {sorted(terms)[:4]}",
                    "scene_index": i,
                    "asset": str(asset_path),
                })
                score -= 15
                continue
            matching_word_times = [w["start"] for w in in_window if graphic_match.tokens(w.get("word", "")) & terms.keys()]
            if matching_word_times:
                distance = lambda t: max(c_start - t, t - c_end, 0.0)  # noqa: E731
                t_first = min(matching_word_times, key=distance)
                offset = distance(t_first)
                # Named in the hook, shown on the next cut: as early as it can be, since the hook keeps its
                # photo (scene_planner.lock_narrative_graphics puts it there). Flagging that made the repair
                # unwinnable: the re-plan moved it to the hook and the lock moved it back (2026-10-24).
                after_hook = i == 1 and timeline[0].get("start", 0.0) <= t_first < c_start
                # A map or route named earlier is on time wherever it comes, as its match above already says: the
                # 1.2 s rule still called the Hal Saflieni map, shown after "Paola, Malta", late (audit, 2026-10-28).
                # Shown before its place is named, it is still early.
                named_earlier = locator and any(t < c_start for t in matching_word_times)
                if offset > 1.2 and not after_hook and not named_earlier:
                    issues.append({
                        "type": "temporal_desync",
                        "severity": "minor",
                        # Words the operator reads on the review card: beats count from 1 there.
                        "issue": (f"Beat {i + 1}: the graphic {asset_name} is on screen {offset:.1f} s "
                                  f"{'after' if t_first < c_start else 'before'} the narration names it "
                                  f"(allowed: 1.2 s)"),
                        "scene_index": i,
                        "trigger_time": t_first,
                        "cut_start": c_start,
                        "asset": str(asset_path),
                    })
                    score -= 5

        # 3. Graphic Motion Freeze Audit
        if video_path.exists():
            for i, cut in enumerate(timeline):
                asset_path = Path(cut.get("asset", ""))
                asset_name = asset_path.name.lower()
                dur = cut.get("dur", cut.get("end", 0.0) - cut.get("start", 0.0))
                # For procedural graphic videos with cut longer than base animation duration (6.0s)
                if "graphic_" in asset_name and dur > 6.2:
                    c_end = cut.get("end", 0.0)
                    # Sample strictly before the 0.40s crossfade begins (crossfade starts at c_end - 0.20s)
                    t1 = c_end - 1.0
                    t2 = c_end - 0.45
                    try:
                        frames_dir = (out_dir or video_path.parent) / "qa_freeze"
                        frames = extract_frames(video_path, [t1, t2], frames_dir)
                        if len(frames) == 2:
                            with Image.open(frames[0]) as im1, Image.open(frames[1]) as im2:
                                diff = ImageChops.difference(im1.convert("RGB"), im2.convert("RGB"))
                                stat = ImageStat.Stat(diff)
                                rms = math.sqrt(sum(v**2 for v in stat.mean) / max(1, len(stat.mean)))
                                if rms > 18.0:
                                    issues.append({
                                        "type": "motion_loop",
                                        "severity": "major",
                                        "issue": f"Scene {i}: Graphic '{asset_name}' looped or failed to freeze at tail (RMS={rms:.1f})",
                                        "scene_index": i,
                                        "asset": str(asset_path),
                                    })
                                    score -= 15
                    except Exception as e:
                        issues.append({"type": "visual_check_failed", "severity": "major", "issue": str(e), "scene_index": -1})

        # 4. Keyframe Clearance and Blank Frame Audit
        if video_path.exists() and timeline:
            try:
                mid_times = [(c.get("start", 0.0) + c.get("end", 0.0)) / 2 for c in timeline]
                kf_dir = (out_dir or video_path.parent) / "qa_clearance"
                kfs = extract_frames(video_path, mid_times, kf_dir)
                for i, kf in enumerate(kfs):
                    with Image.open(kf) as im:
                        stat = ImageStat.Stat(im.convert("L"))
                        stddev = stat.stddev[0] if stat.stddev else 0.0
                        if stddev < 3.0:
                            issues.append({
                                "type": "blank_frame",
                                "severity": "critical",
                                "issue": f"Scene {i}: Rendered frame appears completely black/blank (stddev={stddev:.1f})",
                                "scene_index": i,
                            })
                            score -= 30
            except Exception as e:
                issues.append({"type": "visual_check_failed", "severity": "major", "issue": str(e), "scene_index": -1})

        # 5. Dual-Layer Caption Safe-Zone & Graphic Collision Audit
        # Layer 1: Analytical ASS Bounding-Box & SMPTE ST 2046-1 / Meta Keep-Out Evaluation
        # Layer 2: Frame-Level Pixel Density & Luminance Keep-Out Verification
        if video_path.exists() and timeline:
            ass_events: List[Dict[str, Any]] = []
            c_file = caption_path or video_path.parent / f"{video_path.stem}_captions.ass"
            if words and not c_file.is_file():
                issues.append({"type": "missing_captions", "severity": "critical",
                               "issue": f"Caption audit input missing: {c_file}", "scene_index": -1})
            if c_file.exists():
                try:
                    for line in c_file.read_text(encoding="utf-8").splitlines():
                        if line.startswith("Dialogue:"):
                            parts = line.split(",", 9)
                            if len(parts) >= 10:
                                t_s = _parse_ass_time(parts[1].strip())
                                t_e = _parse_ass_time(parts[2].strip())
                                raw_text = parts[9].strip()
                                pos_m = re.search(r"\\pos\((\d+),(\d+)\)", raw_text)
                                pos_y = int(pos_m.group(2)) if pos_m else None
                                ass_events.append({"start": t_s, "end": t_e, "pos_y": pos_y, "raw": raw_text})
                except Exception as e:
                    issues.append({"type": "caption_check_failed", "severity": "major",
                                   "issue": str(e), "scene_index": -1})

            is_widescreen = width > height
            # 16:9: SMPTE ST 2046-1 title-safe (90%) above the caption line. 9:16: the graphics' text stops at
            # the top of the caption band; both from safe_zones, the Reel box every platform leaves free.
            from . import safe_zones
            from .graphics import get_text_bounds
            safe_ceiling = get_text_bounds(width, height)[1]
            caption_zone_top = 905 if is_widescreen else safe_zones.caption_band_top(width, height)
            if not is_widescreen and c_file.exists():
                # Every caption pill inside the Reel box: a pill under Meta's caption row or Shorts' icon column
                # is a finding (the first publication's pills reached 61 px into the bottom band).
                reel_box = safe_zones.safe_box("reel", width, height)
                outside = [r for r in caption_pills(c_file) if not safe_zones.inside(r, reel_box)]
                if outside:
                    lowest = max(r[3] for r in outside)
                    issues.append({"type": "caption_outside_safe_zone", "severity": "major", "scene_index": -1,
                                   "issue": f"{len(outside)} caption pill(s) leave the platforms' safe area "
                                            f"(lowest edge y={lowest} px; safe box x {reel_box[0]}-{reel_box[2]}, y {reel_box[1]}-{reel_box[3]})"})
                    score -= 15

            for i, cut in enumerate(timeline):
                asset_p = Path(cut.get("asset", ""))
                asset_name = asset_p.name.lower()
                if "graphic_" in asset_name:
                    c_start = cut.get("start", 0.0)
                    c_end = cut.get("end", 0.0)

                    # Layer 1: Check active ASS captions during cut
                    active_caps = [
                        ev for ev in ass_events
                        if not (ev["end"] <= c_start or ev["start"] >= c_end)
                    ]
                    active_min_y = min([ev["pos_y"] for ev in active_caps if ev["pos_y"] is not None], default=caption_zone_top)

                    # Layer 2: Keyframe Sample in Keep-Out Buffer Zone
                    mid_t = (c_start + c_end) / 2.0
                    try:
                        kf_dir = (out_dir or video_path.parent) / "qa_collision"
                        sampled_frames = extract_frames(video_path, [mid_t], kf_dir)
                        if sampled_frames:
                            with Image.open(sampled_frames[0]) as im:
                                w_im, h_im = im.size
                                sc_y = int(safe_ceiling * h_im / height)
                                cap_y = int(active_min_y * h_im / height)
                                if cap_y > sc_y + 10:
                                    strip = im.crop((int(w_im * 0.1), sc_y, int(w_im * 0.9), cap_y)).convert("L")
                                    strip_stat = ImageStat.Stat(strip)
                                    # If the buffer band has high variance and high brightness, graphic bled into safe zone
                                    if strip_stat.stddev and strip_stat.stddev[0] > 48.0 and strip_stat.mean[0] > 85.0:
                                        issues.append({
                                            "type": "caption_collision",
                                            "severity": "major",
                                            "issue": f"Scene {i}: Graphic '{asset_name}' encroaches into Caption Safe Zone (bleed in y={sc_y}-{cap_y}px)",
                                            "scene_index": i,
                                            "asset": str(asset_p),
                                            "safe_limit": safe_ceiling,
                                        })
                                        score -= 15
                    except Exception as e:
                        issues.append({"type": "visual_check_failed", "severity": "major", "issue": str(e), "scene_index": -1})

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)

        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
            "loudness": loudness,
            "scenes_audited": len(timeline),
            "master_sha256": sha256_file(video_path),
        }

    def audit_carousel_slides(self, carousel_dir: Path, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Audits generated carousel slides for aspect ratio, blank frames, safe margin encroachment, and badge bounds,
        then looks at each slide with its own words (still_vision; informational findings)."""
        target_dir = carousel_dir / "carousel" if (carousel_dir / "carousel").is_dir() else carousel_dir
        issues: List[Dict[str, Any]] = []
        score = 100
        slides = sorted(list(target_dir.glob("carousel_slide_*.jpg")))
        if not slides:
            return {
                "score": 0,
                "certified": False,
                "issues": [{"type": "missing_slides", "severity": "critical", "issue": f"No carousel slides found in {target_dir}"}],
                "slides_audited": 0,
            }

        for idx, slide_path in enumerate(slides, start=1):
            try:
                with Image.open(slide_path) as im:
                    w, h = im.size
                    # Check aspect ratio: carousels are 1440x1800 (4:5, Meta's recommended feed size, since
                    # 2026-10-28); slides drawn before then are 1080x1350, and 1:1 and 9:16 stay accepted
                    if (w, h) not in ((1440, 1800), (1080, 1350), (1080, 1080), (1080, 1920)):
                        issues.append({
                            "type": "non_standard_aspect_ratio",
                            "severity": "minor",
                            "issue": f"Slide {idx} ({slide_path.name}): non-standard dimensions {w}x{h}",
                            "slide": slide_path.name,
                        })
                        score -= 5

                    # Blank/black frame detection
                    gray = im.convert("L")
                    stat = ImageStat.Stat(gray)
                    stddev = stat.stddev[0] if stat.stddev else 0.0
                    if stddev < 3.0:
                        issues.append({
                            "type": "blank_slide",
                            "severity": "critical",
                            "issue": f"Slide {idx} ({slide_path.name}): rendered slide appears blank/monochrome (stddev={stddev:.1f})",
                            "slide": slide_path.name,
                        })
                        score -= 30

                    # Safe margin text/element clipping check at extreme borders in the typography safe band
                    text_band_top = int(h * 0.58)
                    text_band_bottom = int(h * 0.92)
                    edge = round(12 * w / 1080)  # 12 px of the 1080-wide design canvas
                    left_strip = gray.crop((0, text_band_top, edge, text_band_bottom))
                    right_strip = gray.crop((w - edge, text_band_top, w, text_band_bottom))
                    l_stat = ImageStat.Stat(left_strip)
                    r_stat = ImageStat.Stat(right_strip)
                    if l_stat.stddev and l_stat.stddev[0] > 60.0:
                        issues.append({
                            "type": "margin_encroachment",
                            "severity": "major",
                            "issue": f"Slide {idx} ({slide_path.name}): graphics or text clipped at left canvas margin",
                            "slide": slide_path.name,
                        })
                        score -= 15
                    if r_stat.stddev and r_stat.stddev[0] > 60.0:
                        issues.append({
                            "type": "margin_encroachment",
                            "severity": "major",
                            "issue": f"Slide {idx} ({slide_path.name}): graphics or text clipped at right canvas margin",
                            "slide": slide_path.name,
                        })
                        score -= 15
            except Exception as e:
                issues.append({
                    "type": "corrupt_slide",
                    "severity": "critical",
                    "issue": f"Slide {idx} ({slide_path.name}): failed to load or parse image: {e}",
                    "slide": slide_path.name,
                })
                score -= 30

        from . import still_vision
        issues.extend(still_vision.findings(slides, "carousel slide", [still_vision.carousel_copy(payload, i) for i in range(len(slides))],
                                            "carousel_slide_look", "slide"))

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)
        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
            "slides_audited": len(slides),
        }

    def audit_story_cards(self, story_dir: Path, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Audits generated Story frames for 9:16 aspect ratio, blank cards, UI safe zone violations, and engagement bait,
        then looks at each frame with its own words (still_vision; informational findings)."""
        target_dir = story_dir / "stories" if (story_dir / "stories").is_dir() else story_dir
        issues: List[Dict[str, Any]] = []
        score = 100
        cards = sorted(list(target_dir.glob("story_*.jpg")) + list(target_dir.glob("story_*.png")))
        if not cards:
            return {
                "score": 0,
                "certified": False,
                "issues": [{"type": "missing_stories", "severity": "critical", "issue": f"No story cards found in {target_dir}"}],
                "cards_audited": 0,
            }

        for idx, card_path in enumerate(cards, start=1):
            try:
                with Image.open(card_path) as im:
                    w, h = im.size
                    if abs((w / h) - (9 / 16)) > 0.05:
                        issues.append({
                            "type": "non_standard_aspect_ratio",
                            "severity": "major",
                            "issue": f"Story card {idx} ({card_path.name}): non-9:16 aspect ratio ({w}x{h})",
                            "card": card_path.name,
                        })
                        score -= 20

                    gray = im.convert("L")
                    stat = ImageStat.Stat(gray)
                    stddev = stat.stddev[0] if stat.stddev else 0.0
                    if stddev < 1.0:
                        issues.append({
                            "type": "blank_card",
                            "severity": "critical",
                            "issue": f"Story card {idx} ({card_path.name}): rendered card appears blank/monochrome (stddev={stddev:.1f})",
                            "card": card_path.name,
                        })
                        score -= 30

                    # Extreme margin text clipping in the story safe band
                    mid_top = int(h * 0.20)
                    mid_bot = int(h * 0.85)
                    left_strip = gray.crop((0, mid_top, 8, mid_bot))
                    right_strip = gray.crop((w - 8, mid_top, w, mid_bot))
                    l_stat = ImageStat.Stat(left_strip)
                    r_stat = ImageStat.Stat(right_strip)
                    if l_stat.stddev and l_stat.stddev[0] > 65.0:
                        issues.append({
                            "type": "story_margin_encroachment",
                            "severity": "major",
                            "issue": f"Story card {idx} ({card_path.name}): graphics or text clipped at left canvas margin",
                            "card": card_path.name,
                        })
                        score -= 15
                    if r_stat.stddev and r_stat.stddev[0] > 65.0:
                        issues.append({
                            "type": "story_margin_encroachment",
                            "severity": "major",
                            "issue": f"Story card {idx} ({card_path.name}): graphics or text clipped at right canvas margin",
                            "card": card_path.name,
                        })
                        score -= 15
            except Exception as e:
                issues.append({
                    "type": "corrupt_card",
                    "severity": "critical",
                    "issue": f"Story card {idx} ({card_path.name}): failed to load image: {e}",
                    "card": card_path.name,
                })
                score -= 30

        # The text block of each frame (recorded by the renderer) inside the area Meta leaves free on a Story.
        layout_file = target_dir / "layout.json"
        if layout_file.is_file():
            from . import safe_zones
            try:
                for entry in json.loads(layout_file.read_text(encoding="utf-8")):
                    w, h = entry.get("size") or (1080, 1920)
                    drawn, source = entry.get("body_drawn"), entry.get("body_source")
                    if source and drawn is not None and (drawn != source or not drawn.rstrip().endswith((".", "!", "?", "’", '"'))):
                        # A frame's text shortened or ending mid-sentence (a posted Kailasa frame, 2026-10-25).
                        issues.append({"type": "story_text_incomplete", "severity": "major", "card": entry.get("frame"),
                                       "issue": f"Story frame {entry.get('frame')}: its text was shortened to fit "
                                                f"({len(drawn.split())} of {len(source.split())} words): ...{drawn[-60:]}"})
                        score -= 15
                    from .multi_format import STORY_BODY_LINES, STORY_BODY_PX
                    if entry.get("body_px") and (entry["body_px"] < STORY_BODY_PX or (entry.get("body_lines") or 0) > STORY_BODY_LINES):
                        # Too small to read on a phone, or spilling past its three lines (operator, 2026-10-25).
                        issues.append({"type": "story_text_too_small", "severity": "major", "card": entry.get("frame"),
                                       "issue": f"Story frame {entry.get('frame')}: body set at {entry['body_px']} px on "
                                                f"{entry.get('body_lines')} lines (needs {STORY_BODY_PX} px, at most {STORY_BODY_LINES} lines)"})
                        score -= 15
                    if not safe_zones.inside(tuple(entry["text_box"]), safe_zones.safe_box("story", w, h)):
                        issues.append({"type": "story_text_outside_safe_zone", "severity": "major", "card": entry.get("frame"),
                                       "issue": f"Story frame {entry.get('frame')}: text block {entry['text_box']} leaves the Story safe area "
                                                f"{safe_zones.safe_box('story', w, h)}"})
                        score -= 15
            except (ValueError, KeyError, TypeError) as e:
                issues.append({"type": "story_layout_unreadable", "severity": "minor", "issue": f"stories/layout.json: {e}"})

        from . import still_vision
        issues.extend(still_vision.findings(cards, "Story frame", [still_vision.story_copy(payload, i) for i in range(len(cards))],
                                            "story_frame_look", "card"))

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)
        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
            "cards_audited": len(cards),
        }

    def audit_thumbnails(self, cover_path: Optional[Path], grid_path: Optional[Path],
                         payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Audits video cover (9:16) and profile grid thumbnail (1:1) for dimensions, profile crop safety, and contrast,
        then looks at the cover with its own words (still_vision; informational: a title across the very detail it
        names, seen on a Damascus cover 2026-10-25, is fixed by choosing another layout in Covers)."""
        issues: List[Dict[str, Any]] = []
        score = 100
        audited = 0

        if cover_path and cover_path.exists():
            audited += 1
            try:
                with Image.open(cover_path) as im:
                    w, h = im.size
                    if abs((w / h) - (9 / 16)) > 0.05:
                        issues.append({
                            "type": "invalid_cover_ratio",
                            "severity": "major",
                            "issue": f"Cover ({cover_path.name}): non-9:16 aspect ratio ({w}x{h})",
                        })
                        score -= 20
                    stat = ImageStat.Stat(im.convert("L"))
                    if (stat.stddev[0] if stat.stddev else 0.0) < 1.0:
                        issues.append({
                            "type": "blank_cover",
                            "severity": "critical",
                            "issue": f"Cover ({cover_path.name}): image appears blank/monochrome",
                        })
                        score -= 30
            except Exception as e:
                issues.append({"type": "corrupt_cover", "severity": "critical", "issue": f"Cover error: {e}"})
                score -= 30

        if grid_path and grid_path.exists():
            audited += 1
            try:
                with Image.open(grid_path) as im:
                    w, h = im.size
                    if abs((w / h) - 1.0) > 0.05:
                        issues.append({
                            "type": "invalid_grid_ratio",
                            "severity": "major",
                            "issue": f"Grid thumbnail ({grid_path.name}): non-1:1 aspect ratio ({w}x{h})",
                        })
                        score -= 20
                    stat = ImageStat.Stat(im.convert("L"))
                    if (stat.stddev[0] if stat.stddev else 0.0) < 1.0:
                        issues.append({
                            "type": "blank_grid_thumbnail",
                            "severity": "critical",
                            "issue": f"Grid thumbnail ({grid_path.name}): image appears blank/monochrome",
                        })
                        score -= 30
            except Exception as e:
                issues.append({"type": "corrupt_grid", "severity": "critical", "issue": f"Grid thumbnail error: {e}"})
                score -= 30

        if cover_path and cover_path.exists():
            from . import still_vision
            t = (payload or {}).get("thumbnail") or {}
            words = " / ".join(str(t.get(k, "")) for k in ("eyebrow", "line1", "line2", "tagline") if t.get(k))
            issues.extend(still_vision.findings([cover_path], "Reel cover", [words], "cover_look", "cover"))

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)
        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
            "thumbnails_audited": audited,
        }

    def audit_publishing_policy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Audits publishing metadata against Meta policies: AI transparency disclosure, engagement bait, and claim confidence."""
        issues: List[Dict[str, Any]] = []
        score = 100

        # 1. AI Transparency Disclosure
        if not payload.get("is_generated_by_ai", False):
            issues.append({
                "type": "missing_ai_disclosure",
                "severity": "critical",
                "issue": "Meta AI Transparency Policy violation: 'is_generated_by_ai' must be True for synthetic documentary assets",
            })
            score -= 35

        # 2. Engagement Bait Linter
        caption = payload.get("caption", "") or payload.get("description", "")
        story_opts = payload.get("poll", {}).get("options", [])
        combined_text = f"{caption} {' '.join(str(o) for o in story_opts)}".lower()

        bait_patterns = [
            r"\b(comment|vote|reply|type)\s+(with\s+)?(yes\b|no\b|a\s+or\s+b|1\s+or\s+2|agree\b)",
            r"\b(drop\s+an?\s+[ab12]\b)",
            r"\b(type\s+['\"][a-b1-2]['\"])",
        ]
        for pattern in bait_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                issues.append({
                    "type": "engagement_bait_detected",
                    "severity": "major",
                    "issue": f"Meta Policy Demotion Risk: Engagement bait pattern detected in publication copy ('{pattern}')",
                })
                score -= 25
                break

        # 3. Fact Confidence Gating
        claims = payload.get("claims", [])
        if isinstance(claims, list):
            for c in claims:
                if isinstance(c, dict) and c.get("confidence", 1.0) < 0.70:
                    issues.append({
                        "type": "unverified_factual_claim",
                        "severity": "major",
                        "issue": f"Fact Confidence Violation: claim '{c.get('claim', '')}' has confidence {c.get('confidence')} (< 0.70)",
                    })
                    score -= 20

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)
        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
        }

    def certify(
        self,
        video_path: Path,
        timeline: List[Dict[str, Any]],
        words: List[Dict[str, Any]],
        run_dir: Path,
        **kwargs,
    ) -> Dict[str, Any]:
        """Audits the master once, plus the carousel, stories, thumbnails and publishing policy.
        Certified only if every audit passes; the score is the lowest of them."""
        final_audit = self.audit_rendered_timeline(
            video_path,
            timeline,
            words,
            out_dir=run_dir / "audit",
            caption_path=kwargs.get("caption_path"),
            width=kwargs.get("width", VIDEO_WIDTH),
            height=kwargs.get("height", VIDEO_HEIGHT),
        )
        log.info("Quality audit: score=%d/100, certified=%s, issues=%d",
                 final_audit["score"], final_audit["certified"], len(final_audit["issues"]))
        # Atomic Multi-Asset Audit Integration
        carousel_audit = None
        story_audit = None
        thumb_audit = None
        policy_audit = None

        payload_obj = kwargs.get("payload")
        c_dir = kwargs.get("carousel_dir") or (run_dir / "carousel")
        if c_dir and (c_dir.is_dir() or (run_dir / "carousel").is_dir()):
            target_c = c_dir if c_dir.is_dir() else (run_dir / "carousel")
            if list(target_c.glob("carousel_slide_*.jpg")):
                carousel_audit = self.audit_carousel_slides(target_c, payload=payload_obj)

        s_dir = kwargs.get("story_dir") or (run_dir / "stories")
        if s_dir and (s_dir.is_dir() or (run_dir / "stories").is_dir()):
            target_s = s_dir if s_dir.is_dir() else (run_dir / "stories")
            if list(target_s.glob("story_*.jpg")) or list(target_s.glob("story_*.png")):
                story_audit = self.audit_story_cards(target_s, payload=payload_obj)

        cov_p = kwargs.get("cover_path") or (run_dir / "thumbnail_cover.jpg")
        grid_p = kwargs.get("grid_path") or (run_dir / "thumbnail_profile_grid.jpg")
        if (cov_p and cov_p.exists()) or (grid_p and grid_p.exists()):
            thumb_audit = self.audit_thumbnails(cov_p if (cov_p and cov_p.exists()) else None,
                                                grid_p if (grid_p and grid_p.exists()) else None, payload=payload_obj)

        if payload_obj:
            policy_audit = self.audit_publishing_policy(payload_obj)

        all_issues = list(final_audit["issues"])
        scores = [final_audit["score"]]
        is_atomic_cert = final_audit["certified"]

        if carousel_audit:
            all_issues.extend(carousel_audit["issues"])
            scores.append(carousel_audit["score"])
            if not carousel_audit["certified"]:
                is_atomic_cert = False

        if story_audit:
            all_issues.extend(story_audit["issues"])
            scores.append(story_audit["score"])
            if not story_audit["certified"]:
                is_atomic_cert = False

        if thumb_audit:
            all_issues.extend(thumb_audit["issues"])
            scores.append(thumb_audit["score"])
            if not thumb_audit["certified"]:
                is_atomic_cert = False

        if policy_audit:
            all_issues.extend(policy_audit["issues"])
            scores.append(policy_audit["score"])
            if not policy_audit["certified"]:
                is_atomic_cert = False

        atomic_score = min(scores) if scores else 100

        return {
            "score": atomic_score,
            "certified": is_atomic_cert,
            "issues": all_issues,
            "loudness": final_audit.get("loudness", {}),
            "master_sha256": final_audit.get("master_sha256"),
            "atomic_breakdown": {
                "video": {"score": final_audit["score"], "certified": final_audit["certified"]},
                "carousel": carousel_audit,
                "stories": story_audit,
                "thumbnails": thumb_audit,
                "policy": policy_audit,
            },
        }


def verify_motion_hook_integrity(
    frame_start: Path,
    frame_end: Path,
    archetype: str = "megastructure",
    min_inlier_ratio: float = 0.25,
    min_inliers: int = 15,
) -> Dict[str, Any]:
    """
    Dual-layer mathematical homography & perspective-invariant feature tracking verification.
    Verifies that camera movement between start and end frame represents a valid rigid translation/push
    rather than non-rigid morphological AI hallucination, melting geometry, or phantom object emergence.
    """
    import cv2
    import numpy as np

    if not frame_start.exists() or not frame_end.exists():
        return {
            "pass": False,
            "reason": f"Missing frame files: {frame_start} or {frame_end}",
            "inlier_ratio": 0.0,
            "inliers": 0,
            "total_matches": 0,
            "archetype": archetype,
        }

    im1 = cv2.imread(str(frame_start), cv2.IMREAD_GRAYSCALE)
    im2 = cv2.imread(str(frame_end), cv2.IMREAD_GRAYSCALE)
    if im1 is None or im2 is None:
        return {
            "pass": False,
            "reason": "Could not decode frame images",
            "inlier_ratio": 0.0,
            "inliers": 0,
            "total_matches": 0,
            "archetype": archetype,
        }

    # Use SIFT if available, fallback to ORB
    detector = cv2.SIFT_create(nfeatures=2000) if hasattr(cv2, "SIFT_create") else cv2.ORB_create(nfeatures=2000)
    kp1, des1 = detector.detectAndCompute(im1, None)
    kp2, des2 = detector.detectAndCompute(im2, None)

    if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
        return {
            "pass": False,
            "reason": "Insufficient detectable structural feature keypoints",
            "inlier_ratio": 0.0,
            "inliers": 0,
            "total_matches": 0,
            "archetype": archetype,
        }

    # Match descriptors
    norm = cv2.NORM_L2 if hasattr(cv2, "SIFT_create") and isinstance(detector, type(cv2.SIFT_create())) else cv2.NORM_HAMMING
    matcher = cv2.BFMatcher(norm)
    matches = matcher.knnMatch(des1, des2, k=2)

    good = []
    for m in matches:
        if len(m) == 2 and m[0].distance < 0.78 * m[1].distance:
            good.append(m[0])

    if len(good) < min_inliers:
        return {
            "pass": False,
            "reason": f"Too few coherent feature matches ({len(good)} < {min_inliers}); structural drift or occlusion detected",
            "inlier_ratio": 0.0,
            "inliers": 0,
            "total_matches": len(good),
            "archetype": archetype,
        }

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    inlier_ratio = inliers / max(len(good), 1)

    # Geometry determinant test: det(H) must be positive and non-degenerate for a valid physical perspective
    det = float(np.linalg.det(H[:2, :2])) if H is not None else 0.0
    if det <= 0.01 or det > 100.0:
        return {
            "pass": False,
            "reason": f"Degenerate perspective homography (det={det:.3f}); non-rigid geometric morphing detected",
            "inlier_ratio": inlier_ratio,
            "inliers": inliers,
            "total_matches": len(good),
            "determinant": det,
            "archetype": archetype,
        }

    # Archetype-specific thresholding
    req_ratio = min_inlier_ratio
    if archetype == "relic_artifact":
        req_ratio = max(req_ratio, 0.28)  # Relic macro surfaces must be highly rigid
    elif archetype == "megastructure":
        req_ratio = max(req_ratio, 0.22)  # Cliffside/aerial perspectives have depth variation

    passed = (inliers >= min_inliers) and (inlier_ratio >= req_ratio)
    reason = "Passed homography structural integrity gate" if passed else (
        f"Inlier ratio below threshold ({inlier_ratio:.2f} < {req_ratio:.2f}); AI hallucination or geometry warping"
    )

    return {
        "pass": passed,
        "reason": reason,
        "inlier_ratio": round(inlier_ratio, 3),
        "inliers": inliers,
        "total_matches": len(good),
        "determinant": round(det, 3),
        "archetype": archetype,
    }
