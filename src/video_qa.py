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
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLMError, claude_available, image_block, structured_call
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
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=540:-2", str(path)], check=True, timeout=60)
        frames.append(path)
    return frames


def review_render(video: Path, beats: List[Dict[str, Any]], cover: Path, work_dir: Path) -> Optional[QAReport]:
    """Returns the QA report, or None if Claude is unavailable or the check fails."""
    if not claude_available():
        return None
    try:
        times = [(b["start"] + b["end"]) / 2 for b in beats]
        frames = extract_frames(video, times, work_dir / "qa_frames")
        content: List[dict] = []
        for i, (frame, beat) in enumerate(zip(frames, beats), 1):
            content += [{"type": "text", "text": f"Beat {i} narration: \"{beat['text']}\""}, image_block(frame, 768)]
        content += [{"type": "text", "text": "Cover image (thumbnail):"}, image_block(cover, 768),
                    {"type": "text", "text": "Report issues by beat number (use 0 for the cover)."}]
        return structured_call(SYSTEM, content, QAReport, effort="low", max_tokens=4000)
    except (LLMError, subprocess.SubprocessError, OSError) as e:
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
    for i in sorted(report.issues, key=lambda x: order[x.severity])[:6]:
        where = "cover" if i.beat == 0 else f"beat {i.beat}"
        lines.append(f"{icon[i.severity]} {html.escape(where)}: {html.escape(i.issue)}")
    return "\n".join(lines)
