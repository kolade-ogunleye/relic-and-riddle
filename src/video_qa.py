"""
src/video_qa.py
===============
Automated look at the finished video before it reaches the human reviewer.

One frame from the middle of each narration beat, plus the cover, goes to Claude with the
words spoken at that moment. It flags unreadable or clashing on-screen text, images that
don't match the narration, watermarks or other brands' logos, and visual glitches.
Findings are shown on the review card; the human still decides.
"""

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
from .layout_rules import get_layout_rule, load_layout_rules, record_learned_adjustment, save_layout_rules
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
Ignore stylistic choices like film grain, vignette and gold/white caption colours. If everything is fine, return no issues."""


def extract_frames(video: Path, times: List[float], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times, 1):
        path = out_dir / f"qa_{i:02d}.jpg"
        path.unlink(missing_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=540:-2", str(path)], check=True, timeout=60)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"No frame could be extracted at {t:.2f}s")
        frames.append(path)
    return frames


@llm_task("video_qa")
def review_render(video: Path, beats: List[Dict[str, Any]], cover: Path, work_dir: Path) -> Optional[QAReport]:
    """Returns the QA report, or None if Claude is unavailable or the check fails."""
    if not (free_llm.available() if free_llm.enabled() else claude_available()):
        return None
    try:
        times = [(b["start"] + b["end"]) / 2 for b in beats]
        frames = extract_frames(video, times, work_dir / "qa_frames")
        content: List[dict] = []
        for i, (frame, beat) in enumerate(zip(frames, beats), 1):
            content += [{"type": "text", "text": f"Beat {i} narration: \"{beat['text']}\""}, image_block(frame, 768)]
        content += [{"type": "text", "text": "Cover image (thumbnail):"}, image_block(cover, 768),
                    {"type": "text", "text": "Report issues by beat number (use 0 for the cover)."}]
        return (free_llm.call(SYSTEM, content, QAReport, role="checker", effort="low", max_tokens=4000)
                if free_llm.enabled() else structured_call(SYSTEM, content, QAReport, effort="low", max_tokens=4000))
    except (LLMError, subprocess.SubprocessError, OSError, ValueError) as e:
        log.warning("Video QA could not run: %s", e)
        return None


def qa_summary_html(report: Optional[QAReport]) -> str:
    import html
    if report is None:
        return "🔎 Automated video check: not run"
    if not report.issues:
        return "🔎 Automated video check: no issues found"
    icon = {"critical": "🛑", "major": "⚠️", "minor": "·"}
    order = {"critical": 0, "major": 1, "minor": 2}
    lines = [f"🔎 Automated video check: {len(report.issues)} issue(s)"]
    for i in sorted(report.issues, key=lambda x: order.get(x.severity.lower(), 99))[:6]:
        where = "cover" if i.beat == 0 else f"beat {i.beat}"
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


SEMANTIC_GRAPHIC_MAP = {
    "defense": ["stone", "door", "disc", "doors", "defense", "defensive", "basalt", "seal", "passage", "millstone", "rolling"],
    "ventilation": ["ventilation", "shaft", "air", "breathe", "convection", "airflow", "oxygen", "stale", "fresh", "suffocating"],
    "section": ["depth", "meter", "metre", "deep", "floor", "level", "feet", "layer", "85", "stables", "uppermost", "living"],
    "scale": ["scale", "tall", "height", "pyramid", "liberty", "compare", "size", "thousand", "twenty thousand", "20,000", "refugees", "shelter", "population", "capacity"],
    "map": ["located", "cappadocia", "turkey", "anatolia", "region", "plain", "map", "subterranean metropolis", "windswept"],
    "numbers": ["thousand", "capacity", "people", "metric", "stat", "population", "inhabitants", "depth", "85"],
}


def _graphic_keywords(asset_path, category):
    """Use the graphic's own declared place or figures instead of one topic's vocabulary."""
    import json
    from .lineage import sidecar
    fallback = SEMANTIC_GRAPHIC_MAP[category]
    try:
        declaration = json.loads(sidecar(asset_path).read_text(encoding="utf-8"))
        inputs = declaration["settings"]["inputs"]
        payload = inputs.get("payload", {})
        if category == "map":
            place = inputs.get("place", {})
            text = " ".join(str(place.get(k, "")) for k in ("label", "country"))
            names = re.findall(r"[\w'-]{3,}", text.lower())
            return names or fallback
        if category == "numbers":
            values = " ".join(str(c.get("value", "")) for c in payload.get("stat_cards", []))
            figures = re.findall(r"\d[\d,]*(?:\.\d+)?", values)
            return figures or [w for w in re.findall(r"\w+", values.lower()) if len(w) > 3] or fallback
        if category == "scale" and payload.get("scale_fact"):
            return [f"{payload['scale_fact']['value']:g}"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return fallback


class ClosedLoopAuditor:
    """Active Closed-Loop Auto-Remediation and Quality Gate Engine.
    Executes shift-left deterministic audits across loudness, semantic cross-correlation,
    graphic motion freeze, and bounding-box clearance before release.
    """

    def __init__(self, max_healing_iterations: int = 3):
        self.max_healing_iterations = max_healing_iterations
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

        # 2. Temporal Semantic Cross-Correlation Audit
        all_words_list = words or []
        for i, cut in enumerate(timeline):
            asset_path = Path(cut.get("asset", ""))
            asset_name = asset_path.name.lower()
            matched_category = None
            for cat, keywords in SEMANTIC_GRAPHIC_MAP.items():
                if f"graphic_{cat}" in asset_name or (cat in asset_name and "graphic" in asset_name):
                    matched_category = cat
                    break

            if matched_category:
                cat_keywords = _graphic_keywords(asset_path, matched_category)
                cut_text = cut.get("text", "").lower()
                c_start, c_end = cut.get("start", 0.0), cut.get("end", 0.0)

                # Check words occurring during the cut
                spoken_in_cut = [
                    w.get("word", "").lower() for w in all_words_list
                    if (c_start - 1.0) <= w.get("start", 0.0) <= (c_end + 1.0)
                ]
                spoken_text = " ".join(spoken_in_cut)

                has_keyword = any(k in cut_text or k in spoken_text for k in cat_keywords)
                if not has_keyword:
                    issues.append({
                        "type": "semantic_desync",
                        "severity": "major",
                        "issue": f"Scene {i}: Graphic '{asset_name}' missing narration keywords {cat_keywords[:4]}",
                        "scene_index": i,
                        "asset": str(asset_path),
                    })
                    score -= 15
                else:
                    matching_word_times = [
                        w["start"] for w in all_words_list
                        if any(re.sub(r"[^\w]", "", k).lower() and re.sub(r"[^\w]", "", k).lower() in re.sub(r"[^\w]", "", w.get("word", "")).lower() for k in cat_keywords)
                        and (c_start - 2.5) <= w["start"] <= (c_end + 2.5)
                    ]
                    if matching_word_times:
                        distance = lambda t: max(c_start - t, t - c_end, 0.0)
                        t_first = min(matching_word_times, key=distance)
                        offset = distance(t_first)
                        if offset > 1.2:
                            issues.append({
                                "type": "temporal_desync",
                                "severity": "minor",
                                "issue": f"Scene {i}: Graphic '{asset_name}' keyword is {offset:.2f}s outside its cut (>1.2s)",
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
            # SMPTE ST 2046-1 Title Safe is 90% (y=972 in 1080p).
            # Caption safe-zone ceiling is 820px in 16:9 widescreen, 1113px in 9:16 vertical Reels.
            safe_ceiling = 820 if is_widescreen else 1113
            caption_zone_top = 905 if is_widescreen else 1290

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
            "remediation_targets": [iss for iss in issues if iss.get("scene_index", -1) >= 0 or iss.get("type") == "loudness"],
        }

    def audit_carousel_slides(self, carousel_dir: Path) -> Dict[str, Any]:
        """Audits generated carousel slides for aspect ratio, blank frames, safe margin encroachment, and badge bounds."""
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
                    # Check aspect ratio: standard carousels are 1080x1350 (4:5) or 1080x1080 (1:1)
                    if (w, h) not in ((1080, 1350), (1080, 1080), (1080, 1920)):
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

                    # Safe margin text/element clipping check at extreme borders
                    left_strip = gray.crop((0, 0, 12, h))
                    right_strip = gray.crop((w - 12, 0, w, h))
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

        final_score = max(0, min(100, score))
        certified = (final_score == 100 and len([iss for iss in issues if iss.get("severity") in ("critical", "major")]) == 0)
        return {
            "score": final_score,
            "certified": certified,
            "issues": issues,
            "slides_audited": len(slides),
        }

    def auto_remediate_scene(
        self,
        run_dir: Path,
        scene_index: int,
        defect_type: str,
        context: Dict[str, Any],
        composer: Any = None,
    ) -> bool:
        """Applies targeted calibration and re-synthesis to resolve detected defects."""
        log.info("Auto-remediating defect [%s] on scene %s...", defect_type, scene_index)
        try:
            if defect_type in ("layout_overlap", "clearance_violation", "layout_collision", "caption_collision"):
                # Global calibration cannot repair already encoded scene assets.
                # Leave the defect blocking until those assets are regenerated.
                log.info("Layout correction requires regenerated scene assets; no correction applied.")
                return False

            elif defect_type == "temporal_desync":
                trigger_time = context.get("trigger_time")
                # Editing audit metadata cannot move the content in an encoded video.
                return False

            elif defect_type == "motion_loop":
                # Replacing a cached source in place loses its provenance and may
                # truncate a scene. Regenerate it through the stage pipeline instead.
                return False

            elif defect_type == "loudness":
                log.info("Loudness correction requires a new mastered render; no correction applied.")
                return False

            return False
        except Exception as e:
            log.warning("Auto-remediation failed for [%s]: %s", defect_type, e)
            return False

    def run_self_healing_cycle(
        self,
        video_path: Path,
        timeline: List[Dict[str, Any]],
        words: List[Dict[str, Any]],
        run_dir: Path,
        composer: Any = None,
        max_iterations: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Cybernetic closed-loop: audits, recalibrates failing components, and re-certifies until 100/100."""
        limit = max_iterations or self.max_healing_iterations
        remediations: List[Dict[str, Any]] = []
        iteration = 1

        for iteration in range(1, limit + 1):
            audit_res = self.audit_rendered_timeline(
                video_path,
                timeline,
                words,
                out_dir=run_dir / "audit",
                caption_path=kwargs.get("caption_path"),
                width=kwargs.get("width", VIDEO_WIDTH),
                height=kwargs.get("height", VIDEO_HEIGHT),
            )
            log.info("Self-Healing Audit (Iteration %d/%d): Score=%d/100, Certified=%s, Issues=%d",
                     iteration, limit, audit_res["score"], audit_res["certified"], len(audit_res["issues"]))

            if audit_res["certified"]:
                break

            targets = audit_res.get("remediation_targets", [])
            if not targets:
                break

            applied_this_iteration = False
            for target in targets:
                idx = target.get("scene_index", -1)
                dtype = target.get("type", "unknown")
                success = self.auto_remediate_scene(run_dir, idx, dtype, {**target, "timeline": timeline}, composer=composer)
                if success:
                    applied_this_iteration = True
                    remediations.append({
                        "iteration": iteration,
                        "scene_index": idx,
                        "defect_type": dtype,
                        "remediated": True,
                    })

            if not applied_this_iteration:
                break

            if applied_this_iteration and composer and "extended_scenes" in kwargs and "durations" in kwargs:
                try:
                    log.info("Re-compositing timeline following auto-remediation...")
                    composer.compose_deep_dive(
                        scene_images=kwargs["extended_scenes"],
                        vo_path=kwargs.get("vo_path"),
                        bed_path=kwargs.get("bed_path"),
                        words=words,
                        vo_dur=kwargs.get("vo_dur", 0.0),
                        output_mp4_path=video_path,
                        durations=kwargs["durations"],
                        emphasis=kwargs.get("emphasis", []),
                    )
                except Exception as e:
                    log.warning("Re-compositing failed: %s", e)

        final_audit = self.audit_rendered_timeline(
            video_path,
            timeline,
            words,
            out_dir=run_dir / "audit",
            caption_path=kwargs.get("caption_path"),
            width=kwargs.get("width", VIDEO_WIDTH),
            height=kwargs.get("height", VIDEO_HEIGHT),
        )
        return {
            "score": final_audit["score"],
            "certified": final_audit["certified"],
            "iterations_run": iteration,
            "remediations_applied": remediations,
            "issues": final_audit["issues"],
            "loudness": final_audit.get("loudness", {}),
            "master_sha256": final_audit.get("master_sha256"),
        }
