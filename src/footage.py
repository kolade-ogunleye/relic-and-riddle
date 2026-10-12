"""
src/footage.py
==============
Real public footage as supporting cuts (plan item 6; rules and sources in docs/sources.md).

Sources, best first: Wikimedia Commons video (CC0/PD/CC BY; BY-SA only with the opt-in),
Pexels and Pixabay (free API keys; their licences allow modification and social use, and
their API terms ask for a visible credit), NASA (public domain; NASA-produced items only).

Rules that keep it within Meta's originality guidelines and honest:
- Footage is evidence for our narration, never the video: the scene planner caps it at
  FOOTAGE_MAX_SHARE of the runtime, one segment per source video, original audio removed.
- A clip may illustrate a *specific* place only if its metadata names that place AND a
  vision check agrees; otherwise it is offered as generic atmosphere and labelled so.
  Clips with burned-in text, watermarks, logos or identifiable people as the focus are dropped.
- Quality: at least FOOTAGE_MIN_CROP_WIDTH px wide after the 9:16 crop (4K landscape or
  vertical footage); lower resolutions are skipped, never upscaled.
- Security: downloads are HTTPS from each provider's media hosts only, streamed with a
  size cap; ffmpeg reads local files only (-protocol_whitelist file) and every clip is
  re-encoded; API keys never appear in logs.
"""

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from . import commons, config
from .credits import license_allowed, normalize
from .llm import LLMError, claude_available, image_block, structured_call, task as llm_task

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 6.0          # longest beat plus transitions (matches graphics.CLIP_SECONDS)
MAX_BYTES = config.FOOTAGE_MAX_MB * 1_000_000  # originals are deleted once the segment is cut
PER_QUERY = 2                  # candidates examined per query, after filters
ALLOWED_HOSTS = {
    "commons": {"upload.wikimedia.org"},
    "pexels": {"videos.pexels.com", "player.vimeo.com"},
    "pixabay": {"cdn.pixabay.com"},
    "nasa": {"images-assets.nasa.gov"},
}
CACHE_DIR = config.BASE_DIR / "cache" / "footage_api"  # Pixabay asks for 24 h response caching
CREDIT_PREFIX = "🎥 Footage:"


class FootageCheck(BaseModel):
    shows_subject: bool               # does it show what the query asked for?
    place_matches: Literal["yes", "no", "unsure"]  # is it recognisably the named place?
    burned_in_text_or_watermark: bool
    brand_logo: bool
    identifiable_person_focus: bool
    note: str


# ------------------------------------------------------------------ providers
def _crop_width(w: int, h: int) -> float:
    return min(w, h * 9 / 16)


def _good_size(w: int, h: int) -> bool:
    return _crop_width(w, h) >= config.FOOTAGE_MIN_CROP_WIDTH


def _cached_get(url: str, params: dict, headers: Optional[dict] = None) -> dict:
    """GET with a 24-hour on-disk cache (Pixabay's API terms; kind to the others too)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in params.items() if k != "key"}  # never key the cache (or logs) on secrets
    key = hashlib.sha1(json.dumps([url, safe], sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    resp = requests.get(url, params=params, headers=headers or {}, timeout=30)
    if resp.status_code == 429:
        raise requests.HTTPError(f"{urlparse(url).netloc} rate limit reached")
    resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def search_commons(query: str, keywords: List[str]) -> List[Dict[str, Any]]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:video",
              "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
              "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|AttributionRequired|ImageDescription"}
    pages = commons._get(commons.API, params=params, timeout=30, max_wait=60).json().get("query", {}).get("pages", {})
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if not license_allowed(licence, config.BEAT_IMAGES_ALLOW_SHARE_ALIKE):
            continue
        if not _good_size(info.get("width", 0), info.get("height", 0)):
            continue
        text = f"{p['title']} {commons._plain(meta.get('ImageDescription', {}).get('value', ''))}"
        out.append({"provider": "commons", "title": p["title"], "text": text, "download_url": info["url"],
                    "source_url": info.get("descriptionurl", ""), "license": licence,
                    "artist": commons._plain(meta.get("Artist", {}).get("value", "")) or "Unknown",
                    "attribution_required": meta.get("AttributionRequired", {}).get("value", "true") != "false",
                    "width": info.get("width", 0), "height": info.get("height", 0), "duration": float(info.get("duration") or 0)})
    return out


def search_pexels(query: str, keywords: List[str]) -> List[Dict[str, Any]]:
    if not config.is_configured(config.PEXELS_API_KEY):
        return []
    data = _cached_get("https://api.pexels.com/v1/videos/search", {"query": query, "per_page": 20, "size": "large"},
                       headers={"Authorization": config.PEXELS_API_KEY})
    out = []
    for v in data.get("videos", []):
        files = sorted((f for f in v.get("video_files", []) if f.get("link") and f.get("width") and f.get("height")
                        and _good_size(f["width"], f["height"])), key=lambda f: f["width"] * f["height"])
        if not files:
            continue
        f = files[0]  # the smallest rendition that still meets the bar
        slug = urlparse(v.get("url", "")).path.replace("-", " ")
        out.append({"provider": "pexels", "title": f"Pexels video {v['id']}", "text": slug, "download_url": f["link"],
                    "source_url": v.get("url", ""), "license": "Pexels License", "artist": v.get("user", {}).get("name", "Unknown"),
                    "attribution_required": True, "width": f["width"], "height": f["height"], "duration": float(v.get("duration") or 0)})
    return out


def search_pixabay(query: str, keywords: List[str]) -> List[Dict[str, Any]]:
    if not config.is_configured(config.PIXABAY_API_KEY):
        return []
    data = _cached_get("https://pixabay.com/api/videos/", {"key": config.PIXABAY_API_KEY, "q": query[:100], "per_page": 20,
                                                           "safesearch": "true"})
    out = []
    for h in data.get("hits", []):
        renditions = [r for r in (h.get("videos") or {}).values() if r.get("url") and _good_size(r.get("width", 0), r.get("height", 0))]
        if not renditions:
            continue
        r = min(renditions, key=lambda r: r["width"] * r["height"])
        out.append({"provider": "pixabay", "title": f"Pixabay video {h['id']}", "text": h.get("tags", ""), "download_url": r["url"],
                    "source_url": h.get("pageURL", ""), "license": "Pixabay Content License", "artist": h.get("user", "Unknown"),
                    "attribution_required": True, "width": r["width"], "height": r["height"], "duration": float(h.get("duration") or 0)})
    return out


def search_nasa(query: str, keywords: List[str]) -> List[Dict[str, Any]]:
    data = _cached_get("https://images-api.nasa.gov/search", {"q": query, "media_type": "video"})
    out = []
    for item in data.get("collection", {}).get("items", [])[:10]:
        meta = (item.get("data") or [{}])[0]
        blob = json.dumps(meta).lower()
        if "courtesy" in blob or "©" in blob or "copyright" in blob:  # third-party material isn't covered
            continue
        try:
            assets = _cached_get(item["href"], {})
        except (requests.RequestException, ValueError):
            continue
        mp4s = [a for a in assets if isinstance(a, str) and a.endswith(".mp4") and "~orig" in a]
        if not mp4s:
            continue
        # Size is unknown until probed; resolution is checked after download.
        out.append({"provider": "nasa", "title": meta.get("title", "NASA video"), "text": f"{meta.get('title', '')} {meta.get('description', '')}",
                    "download_url": mp4s[0].replace("http://", "https://"), "source_url": f"https://images.nasa.gov/details/{meta.get('nasa_id', '')}",
                    "license": "Public domain (NASA)", "artist": "NASA", "attribution_required": True,
                    "width": 0, "height": 0, "duration": 0.0})
    return out


PROVIDERS = [("commons", search_commons), ("pexels", search_pexels), ("pixabay", search_pixabay), ("nasa", search_nasa)]


# ----------------------------------------------------------- download & clip
def download(url: str, provider: str, dest: Path) -> Path:
    """HTTPS from the provider's media hosts only, streamed with a size cap."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS[provider]:
        raise ValueError(f"refusing to download from {parsed.scheme}://{parsed.hostname}")
    headers = commons.HEADERS if provider == "commons" else {"User-Agent": commons.HEADERS["User-Agent"]}
    with requests.get(url, stream=True, timeout=(15, 120), headers=headers, allow_redirects=False) as resp:
        if resp.is_redirect:
            target = urlparse(resp.headers.get("Location", ""))
            if target.scheme != "https" or target.hostname not in ALLOWED_HOSTS[provider]:
                raise ValueError(f"refusing redirect to {target.hostname}")
            return download(resp.headers["Location"], provider, dest)
        resp.raise_for_status()
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > MAX_BYTES:  # don't start a download that would only hit the cap
            raise ValueError(f"download exceeds the size cap ({declared / 1e6:.0f} MB)")
        ctype = resp.headers.get("Content-Type", "")
        if not (ctype.startswith("video/") or ctype in ("application/ogg", "application/octet-stream", "binary/octet-stream")):
            raise ValueError(f"unexpected content type {ctype!r}")
        size = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
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


def make_segment(src: Path, info: Dict[str, Any], out: Path) -> Path:
    """A muted 1080x1920 H.264 segment from the middle part of the video, plus a poster frame."""
    dur = info["duration"]
    if dur < SEGMENT_SECONDS:
        raise ValueError(f"too short ({dur:.1f}s)")
    start = dur * 0.1 if dur * 0.8 >= SEGMENT_SECONDS else (dur - SEGMENT_SECONDS) / 2  # skip intros/outros
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                    "-i", str(src), "-an", "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
                   check=True, timeout=600, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", f"{SEGMENT_SECONDS / 2:.2f}", "-i", str(out),
                    "-frames:v", "1", str(out.with_suffix(".jpg"))], check=True, timeout=60, capture_output=True)
    return out


def _frames(clip: Path) -> List[Path]:
    paths = []
    for k, t in enumerate((1.0, 3.0, 5.0)):
        p = clip.with_name(f"{clip.stem}_check{k}.jpg")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", str(t), "-i", str(clip),
                        "-frames:v", "1", "-vf", "scale=540:-2", str(p)], check=True, timeout=60, capture_output=True)
        paths.append(p)
    return paths


CHECK = """You vet stock and archive video for a history documentary. Judge only what you see in these
three frames from one clip. Be strict about watermarks, burned-in text or subtitles, channel logos
and brand logos. "place_matches" is "yes" only if the frames are recognisably the named place
(distinctive architecture or landscape); "unsure" if it could be anywhere similar."""


@llm_task("footage_check")
def verify(clip: Path, query: str, place: str) -> FootageCheck:
    content: List[dict] = []
    for p in _frames(clip):
        content.append(image_block(p, 768))
    content.append({"type": "text", "text": f"Search: {query}\nNamed place: {place}"})
    return structured_call(CHECK, content, FootageCheck, effort="low", max_tokens=1500)


# --------------------------------------------------------------------- main
def source(beats: List[Dict[str, Any]], topic: Dict[str, Any], work_dir: Path, plan=None) -> Tuple[List[Path], List[Dict[str, Any]]]:
    """Returns (clip paths, their credit records). Never raises: footage is optional.
    `plan`: the beat query plan already made for beat images (one Claude call serves both)."""
    if not config.FOOTAGE_ENABLED or not claude_available():
        return [], []
    loc = topic.get("location", {})
    place = ", ".join(v for v in (loc.get("name"), loc.get("country")) if v)
    site_words = [normalize(w) for w in (loc.get("name") or "").split() if len(w) > 3]
    # Place b-roll first: the site, then its town and region (regional footage can only ever be
    # "generic": its metadata won't name the site), then the per-beat subjects.
    places = [loc.get("name"), loc.get("city")] + [r.strip() for r in (loc.get("region") or "").split("/")]
    queries: List[Tuple[str, List[str]]] = []
    for q in places:
        if q and q.lower() not in {x.lower() for x, _ in queries}:
            queries.append((q, site_words))
    if plan is not None:
        queries += [(item.query, item.keywords) for item in plan.items]
    work_dir.mkdir(parents=True, exist_ok=True)
    clips, credits, seen = [], [], set()
    for query, keywords in queries[:8]:
        found = 0
        for provider, search in PROVIDERS:
            if found >= PER_QUERY:
                break
            try:
                candidates = search(query, keywords)
            except (requests.RequestException, ValueError) as e:
                log.warning("Footage search via %s failed (%s).", provider, e)
                continue
            for cand in candidates:
                if found >= PER_QUERY or cand["source_url"] in seen:
                    continue
                seen.add(cand["source_url"])
                n = len(clips) + 1
                raw = work_dir / f"raw_{n:02d}"
                clip = work_dir / f"footage_{n:02d}.mp4"
                try:
                    download(cand["download_url"], cand["provider"], raw)
                    info = probe(raw)
                    if not _good_size(info["width"], info["height"]):
                        raise ValueError(f"{info['width']}x{info['height']} is below the quality bar")
                    make_segment(raw, info, clip)
                    check = verify(clip, query, place)
                except (requests.RequestException, ValueError, OSError, subprocess.SubprocessError, LLMError) as e:
                    log.info("Footage candidate skipped (%s): %s", cand["title"][:60], e)
                    continue
                finally:
                    raw.unlink(missing_ok=True)  # never keep the original download
                if not check.shows_subject or check.burned_in_text_or_watermark or check.brand_logo or check.identifiable_person_focus:
                    log.info("Footage rejected by the check (%s): %s", cand["title"][:60], check.note)
                    clip.unlink(missing_ok=True)
                    continue
                named = any(w and w in normalize(cand["text"]) for w in site_words)
                cand.update(file=str(clip), query=query, place_specific=named and check.place_matches == "yes",
                            check_note=check.note, downloaded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                cand.pop("text", None)
                clip.with_suffix(".json").write_text(json.dumps(cand, indent=1, ensure_ascii=False), encoding="utf-8")
                clips.append(clip)
                credits.append(cand)
                found += 1
    (work_dir / "credits.json").write_text(json.dumps(credits, indent=1, ensure_ascii=False), encoding="utf-8")  # provenance
    log.info("Footage: %d clip(s) passed (%d place-specific).", len(clips), sum(c["place_specific"] for c in credits))
    return clips, credits


def credit_line(used: List[Dict[str, Any]]) -> str:
    """One caption line naming each used clip's creator and source, as the licences/API terms ask."""
    parts, seen = [], set()
    for c in used:
        if c["provider"] == "commons":
            text = f"{c['artist']} ({c['license']}) via Wikimedia Commons"
        elif c["provider"] == "nasa":
            text = "NASA"
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
