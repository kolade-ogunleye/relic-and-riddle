"""
src/footage.py
==============
Real footage as supporting cuts, from clips an operator supplies (Telegram: send the video file
with the caption "/footage <topic>"). They live in assets/<topic>/footage/ and the operator vouches
for their rights. Automated stock/YouTube scrapers were retired: they could not prove a licence
for what they fetched, and downloading from YouTube conflicts with its Terms of Service.

Also the shared, guarded downloader and ffprobe used for AI motion results (src/motion.py).

Rules that keep footage within Meta's originality guidelines and honest:
- Footage is evidence for our narration, never the video: the scene planner caps it at
  FOOTAGE_MAX_SHARE of the runtime, one segment per source video, original audio removed.
- Security: downloads are HTTPS from each provider's media hosts only, streamed with a
  size cap; ffmpeg reads local files only (-protocol_whitelist file) and every clip is
  re-encoded.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from . import commons, config, url_guard

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 6.0          # longest beat plus transitions (matches graphics.CLIP_SECONDS)
MAX_BYTES = config.FOOTAGE_MAX_MB * 1_000_000  # originals are deleted once the segment is cut
ALLOWED_HOSTS = {
    "commons": {"upload.wikimedia.org"},
    # AI motion results (src/motion.py). A leading dot allows that domain's subdomains.
    "kling": {".klingai.com"},
    # Archive film (src/archive_film.py): archive.org/download redirects to its ia*.us.archive.org servers.
    "archive": {"archive.org", ".archive.org"},
    # Europeana's holders serve their own files: any https host, still size-capped, video-only and re-encoded.
    "europeana": {"*"},
    # Still images from the further open-access sources (src/museum_sources.py, 2026-11-02): a candidate whose file
    # is on another host is not taken. Openverse indexes files where their holders keep them: the hosts allowed are
    # Flickr's static servers and Wikimedia's upload server (the bulk of its CC/PD images); others are left out.
    "openverse": {".staticflickr.com", "upload.wikimedia.org"},
    "loc": {"tile.loc.gov", "www.loc.gov", "cdn.loc.gov"},
    "artic": {"www.artic.edu"},
    # The museum still-image searches (src/museum_sources.py): each serves its files from its own image host (QF-44).
    "met": {"images.metmuseum.org"},
    "nasa": {"images-assets.nasa.gov"},
    "smithsonian": {"ids.si.edu"},
}
# Still-image providers: a picture whose provider is not recorded (a Commons search result, a credits.json entry)
# may come from any of their fixed hosts, never from a wildcard one.
IMAGE_PROVIDERS = ("commons", "openverse", "loc", "artic", "met", "nasa", "smithsonian", "europeana")
CREDIT_PREFIX = "🎥 Footage:"


def _host_allowed(host: str, provider: str) -> bool:
    return bool(host) and any(h == "*" or host == h or (h.startswith(".") and host.endswith(h)) for h in ALLOWED_HOSTS[provider])


def url_allowed(url: str, provider: str | None) -> bool:
    """An https URL on the provider's hosts (any image provider's fixed hosts when `provider` is None) whose host is
    public (src/url_guard: no private, loopback or link-local address, no local name). A wildcard provider's host is
    also resolved and refused when it points inside (QF-44)."""
    if provider is None:
        return url_guard.checked(url, lambda h: any(_host_allowed(h, p) and "*" not in ALLOWED_HOSTS[p]
                                                    for p in IMAGE_PROVIDERS), resolve=False)
    if provider not in ALLOWED_HOSTS:
        return False
    return url_guard.checked(url, lambda h: _host_allowed(h, provider), resolve="*" in ALLOWED_HOSTS[provider])


# ----------------------------------------------------------- download & clip
def download(url: str, provider: str, dest: Path, extra_headers: Optional[dict] = None, max_bytes: Optional[int] = None) -> Path:
    """HTTPS from the provider's media hosts only, streamed with a size cap (archive film has its own, larger one)."""
    cap = max_bytes or MAX_BYTES
    parsed = urlparse(url)
    if not url_allowed(url, provider):
        raise ValueError(f"refusing to download from {parsed.scheme}://{parsed.hostname}")
    headers = commons.HEADERS if provider == "commons" else {"User-Agent": commons.HEADERS["User-Agent"]}
    headers = {**headers, **(extra_headers or {})}
    with requests.get(url, stream=True, timeout=(15, 120), headers=headers, allow_redirects=False) as resp:
        if resp.is_redirect:
            location = urljoin(url, resp.headers.get("Location", ""))
            if not url_allowed(location, provider):
                raise ValueError(f"refusing redirect to {urlparse(location).hostname}")
            return download(location, provider, dest, max_bytes=cap)  # credentials are not forwarded
        resp.raise_for_status()
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > cap:  # don't start a download that would only hit the cap
            raise ValueError(f"download exceeds the size cap ({declared / 1e6:.0f} MB)")
        ctype = resp.headers.get("Content-Type", "")
        if not (ctype.startswith("video/") or ctype in ("application/ogg", "application/octet-stream", "binary/octet-stream")):
            raise ValueError(f"unexpected content type {ctype!r}")
        size = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                size += len(chunk)
                if size > cap:
                    raise ValueError("download exceeds the size cap")
                f.write(chunk)
    return dest


def probe(path: Path) -> Dict[str, Any]:
    """Local-only ffprobe of the first video stream."""
    out = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration,format_name", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ValueError(f"not a readable video: {out.stderr[-200:]}")
    data = json.loads(out.stdout)
    fmt = data.get("format", {}).get("format_name", "")
    if any(bad in fmt for bad in ("hls", "concat", "playlist", "m3u", "dash")):
        raise ValueError(f"refusing container format {fmt}")
    s = (data.get("streams") or [{}])[0]
    return {"width": int(s.get("width") or 0), "height": int(s.get("height") or 0),
            "duration": float(data.get("format", {}).get("duration") or 0)}


FILL = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"
# A wide shot (archive film is 4:3 at 640x480) is shown whole, fitted to the width over a blurred, darkened copy of
# itself: filling the frame cropped a 270 px strip and blew it up four times (Pompeii And Vesuvius, 1950: half a
# sculpture, soft; 2026-10-28).
FIT = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.18[bg];"
       "[0:v]scale=1080:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30")


def frame_filter(info: Dict[str, Any]) -> List[str]:
    """ffmpeg's filter arguments for a 1080x1920 frame: a vertical source fills it; a wide one is fitted whole."""
    w, h = info.get("width") or 0, info.get("height") or 0
    if w and h and w / h > 0.8:  # wider than 4:5: a fill would crop most of it away
        return ["-filter_complex", FIT]
    return ["-vf", FILL]


def make_segment(src: Path, info: Dict[str, Any], out: Path) -> Path:
    """A muted 1080x1920 H.264 segment from the core b-roll part of the video, plus a poster frame."""
    dur = info["duration"]
    if dur < SEGMENT_SECONDS:
        raise ValueError(f"too short ({dur:.1f}s)")
    # Target 35% into video to capture core documentary b-roll (skipping intros and outros)
    if dur * 0.7 >= SEGMENT_SECONDS:
        start = dur * 0.35
    elif dur * 0.8 >= SEGMENT_SECONDS:
        start = dur * 0.15
    else:
        start = max(0.0, (dur - SEGMENT_SECONDS) / 2)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                    "-i", str(src), "-an", *frame_filter(info),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
                   check=True, timeout=600, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", f"{SEGMENT_SECONDS / 2:.2f}", "-i", str(out),
                    "-frames:v", "1", str(out.with_suffix(".jpg"))], check=True, timeout=60, capture_output=True)
    return out


def get_curated_footage(topic_id: str, work_dir: Path) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Ingests human-curated video clips dropped into assets/<topic_id>/footage/."""
    if not topic_id:
        return [], []
    curated_dir = config.ASSETS_DIR / topic_id / "footage"
    if not curated_dir.exists():
        return [], []
    video_files = sorted(p for p in curated_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"))
    if not video_files:
        return [], []

    work_dir.mkdir(parents=True, exist_ok=True)
    clips, credits = [], []
    for idx, p in enumerate(video_files):
        n = len(clips) + 1
        final_clip = work_dir / f"footage_{n:02d}.mp4"
        try:
            info = probe(p)
            make_segment(p, info, final_clip)
        except Exception as e:
            log.warning("Failed to prepare curated footage %s: %s", p.name, e)
            continue

        meta_file = p.with_suffix(".json")
        if meta_file.exists():
            try:
                cand = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                cand = {}
        else:
            cand = {}

        cand.setdefault("provider", "curated")
        cand.setdefault("title", p.stem.replace("_", " ").title())
        cand.setdefault("artist", cand.get("uploader") or cand.get("channel") or "Curated Archive")
        cand.setdefault("license", cand.get("license") or "Curated Documentary Footage")
        cand.setdefault("source_url", cand.get("source_url") or f"assets/{topic_id}/footage/{p.name}")
        cand.setdefault("attribution_required", cand.get("attribution_required", True))
        cand.update(
            file=str(final_clip),
            place_specific=True,
            check_note="Human curated and approved footage",
            downloaded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        cand.pop("text", None)
        final_clip.with_suffix(".json").write_text(json.dumps(cand, indent=1, ensure_ascii=False), encoding="utf-8")
        clips.append(final_clip)
        credits.append(cand)

    if credits:
        (work_dir / "credits.json").write_text(json.dumps(credits, indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("Curated Footage: %d clip(s) loaded from assets/%s/footage/.", len(clips), topic_id)
    return clips, credits


def source(beats: List[Dict[str, Any]], topic: Dict[str, Any], work_dir: Path, plan=None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Returns (clip paths, their credit records) for the topic's operator-supplied footage.
    Never raises: footage is optional. `beats` and `plan` are accepted for the caller's interface."""
    if not config.FOOTAGE_ENABLED:
        return [], []
    return get_curated_footage(topic.get("id", ""), work_dir)


def credit_line(used: List[Dict[str, Any]]) -> str:
    """One caption line naming each used clip's creator and source, as the licences/API terms ask."""
    from .credits import author_name
    parts, seen = [], set()
    for c in used:
        # The author's name, not a free-text artist field printed whole (a 778-character notice, 2026-10-29)
        c = dict(c, artist=author_name(c)) if c.get("artist") else c
        if c["provider"] == "commons":
            text = f"{c['artist']} ({c['license']}) via Wikimedia Commons"
        elif c["provider"] == "nasa":
            text = "NASA"
        elif c["provider"] == "youtube":
            text = f"{c['artist']} via YouTube (CC-BY)"
        elif c["provider"] == "archive.org":
            text = f"{c['artist']} ({c['license']}) via the Internet Archive"
        elif c["provider"] == "europeana":
            text = f"{c['artist']} ({c['license']}) via Europeana"
        elif c["provider"] == "curated":
            text = f"{c['artist']} ({c.get('license', 'Documentary Footage')})"
        else:
            text = f"{c['artist']} / {c['provider'].capitalize()}"
        if text not in seen:
            seen.add(text)
            parts.append(text)
    return f"{CREDIT_PREFIX} " + "; ".join(parts) if parts else ""


def with_credits(caption: str, used: List[Dict[str, Any]]) -> str:
    """Replaces any earlier footage line (idempotent on resume) with one for the clips used."""
    kept = [ln for ln in caption.split("\n") if not ln.startswith(CREDIT_PREFIX)]
    body = "\n".join(kept).rstrip()
    line = credit_line(used)
    return f"{body}\n{line}" if line else body
