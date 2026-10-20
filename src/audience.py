"""
src/audience.py
===============
How the published videos actually did, measured where the platforms measure it.

Snapshots are taken 1, 3, 7 and 28 days after posting (Meta's insights can lag up to 48 hours,
so the first one is a first look, not a verdict), from:
- Instagram Graph API media insights (our IG token): views, reach, average watch time, skip rate
  in the first 3 seconds, shares, saves, likes, comments. Metrics Meta marks "in development" are
  requested one by one, so one unsupported metric never empties the rest.
- Facebook video insights, when a Page token is configured: plays, average time watched,
  retention curve, follows.
- Upload-Post post analytics (by the request id it published with) as the fallback for both.

What is derived per video: watch ratio (average watch time / video length), skip rate, shares and
saves per reached account. With few videos these are descriptions, not conclusions; the digest says
so until there are at least MIN_FOR_PATTERNS videos.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from . import config
from .config import is_configured

log = logging.getLogger(__name__)

SNAPSHOTS = (("1d", 1), ("3d", 3), ("7d", 7), ("28d", 28))
IG_METRICS = ("views", "reach", "ig_reels_avg_watch_time", "reels_skip_rate", "shares", "saved",
              "likes", "comments", "total_interactions")
FB_METRICS = ("blue_reels_play_count", "fb_reels_total_plays", "post_impressions_unique",
              "post_video_avg_time_watched", "post_video_followers", "post_video_retention_graph")
MIN_FOR_PATTERNS = 12
PUBLISHED_CHANNELS = ("upload-post", "instagram-graph", "facebook-page")


def ensure_schema(db) -> None:
    with db._conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS audience_metrics (
            run_id TEXT NOT NULL, platform TEXT NOT NULL, snapshot TEXT NOT NULL, source TEXT,
            metrics_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
            PRIMARY KEY (run_id, platform, snapshot))""")


def _get(url: str, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    resp = requests.get(url, params=params, headers=headers or {}, timeout=30)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok or "error" in body:
        raise RuntimeError(f"HTTP {resp.status_code}: {(body.get('error') or {}).get('message', '') if isinstance(body.get('error'), dict) else body.get('error', '')}")
    return body


# ------------------------------------------------------------------ sources
def instagram_media_id(post: Dict[str, Any]) -> Optional[str]:
    """The IG media id: recorded by the Graph publisher, or found by permalink among recent media."""
    if (post.get("post_ids") or {}).get("instagram"):
        return post["post_ids"]["instagram"]
    url = (post.get("post_urls") or {}).get("instagram")
    if not url or not is_configured(config.META_ACCESS_TOKEN) or not is_configured(config.META_IG_ACCOUNT_ID):
        return None
    shortcode = url.rstrip("/").split("/")[-1]
    body = _get(f"https://graph.facebook.com/{config.META_GRAPH_VERSION}/{config.META_IG_ACCOUNT_ID}/media",
                {"fields": "id,permalink", "limit": 50, "access_token": config.META_ACCESS_TOKEN})
    return next((m["id"] for m in body.get("data", []) if shortcode and shortcode in (m.get("permalink") or "")), None)


def instagram_insights(media_id: str) -> Dict[str, Any]:
    base = f"https://graph.facebook.com/{config.META_GRAPH_VERSION}/{media_id}/insights"
    out: Dict[str, Any] = {}
    try:
        body = _get(base, {"metric": ",".join(IG_METRICS), "access_token": config.META_ACCESS_TOKEN})
        rows = body.get("data", [])
    except RuntimeError:
        rows = []
        for m in IG_METRICS:  # one unsupported metric fails the whole request; ask one by one
            try:
                rows += _get(base, {"metric": m, "access_token": config.META_ACCESS_TOKEN}).get("data", [])
            except RuntimeError as e:
                log.debug("IG metric %s unavailable: %s", m, e)
    for r in rows:
        values = r.get("values") or [{}]
        out[r.get("name")] = values[0].get("value") if values else r.get("total_value", {}).get("value")
    return out


def facebook_insights(video_id: str) -> Dict[str, Any]:
    token = config.META_PAGE_ACCESS_TOKEN or ""
    if not is_configured(token):
        return {}
    body = _get(f"https://graph.facebook.com/{config.META_GRAPH_VERSION}/{video_id}/video_insights",
                {"metric": ",".join(FB_METRICS), "access_token": token})
    return {r.get("name"): (r.get("values") or [{}])[0].get("value") for r in body.get("data", [])}


def upload_post_analytics(request_id: str) -> Dict[str, Dict[str, Any]]:
    if not is_configured(config.UPLOAD_POST_API_KEY):
        return {}
    body = _get(f"{config.UPLOAD_POST_BASE_URL.rstrip('/')}/uploadposts/post-analytics/{request_id}", {},
                headers={"Authorization": f"Apikey {config.UPLOAD_POST_API_KEY}"})
    out = {}
    for platform in ("instagram", "facebook"):
        data = body.get(platform) or (body.get("results") or {}).get(platform)
        if isinstance(data, dict):
            out[platform] = data.get("metrics", data)
    return out


# ------------------------------------------------------------------ collection
def due_snapshots(db, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """(run, publication, snapshot) triples old enough and not yet taken."""
    ensure_schema(db)
    now = now or datetime.now(timezone.utc)
    with db._conn() as conn:
        pubs = conn.execute("SELECT * FROM publications WHERE status = 'published' AND channel IN (%s)"
                            % ",".join("?" * len(PUBLISHED_CHANNELS)), PUBLISHED_CHANNELS).fetchall()
        taken = {(r["run_id"], r["snapshot"]) for r in conn.execute("SELECT run_id, snapshot FROM audience_metrics")}
    due = []
    for p in pubs:
        posted = datetime.fromisoformat(p["created_at"])
        posted = posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)
        for name, days in SNAPSHOTS:
            if now - posted >= timedelta(days=days) and (p["run_id"], name) not in taken:
                due.append({"run_id": p["run_id"], "channel": p["channel"], "snapshot": name,
                            "response": json.loads(p["response_payload_json"] or "{}")})
                break  # one snapshot per publication per pass: the most overdue one
    return due


def collect(db) -> int:
    """Takes every due snapshot; returns how many were stored. Failures are logged and retried next time."""
    stored = 0
    for item in due_snapshots(db):
        res, run_id = item["response"], item["run_id"]
        metrics: Dict[str, Dict[str, Any]] = {}
        try:
            media_id = instagram_media_id(res)
            if media_id:
                metrics["instagram"] = {"source": "instagram-graph", **instagram_insights(media_id)}
            fb_video = (res.get("post_ids") or {}).get("facebook")
            if fb_video:
                fb = facebook_insights(fb_video)
                if fb:
                    metrics["facebook"] = {"source": "facebook-graph", **fb}
            if item["channel"] == "upload-post" and any(p not in metrics for p in ("instagram", "facebook")):
                for platform, data in upload_post_analytics(res.get("request_id") or run_id).items():
                    metrics.setdefault(platform, {"source": "upload-post", **data})
        except (RuntimeError, requests.RequestException) as e:
            log.warning("Audience metrics for %s (%s) unavailable: %s", run_id, item["snapshot"], e)
            continue
        with db._conn() as conn:
            for platform, data in metrics.items():
                conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                             (run_id, platform, item["snapshot"], data.get("source"), json.dumps(data),
                              datetime.now(timezone.utc).isoformat()))
                stored += 1
    return stored


# ------------------------------------------------------------------ reading
def derived(metrics: Dict[str, Any], duration_s: Optional[float]) -> Dict[str, Optional[float]]:
    def num(k):
        v = metrics.get(k)
        return float(v) if isinstance(v, (int, float)) else None
    reach = num("reach") or num("post_impressions_unique")
    avg_ms = num("ig_reels_avg_watch_time") or num("post_video_avg_time_watched")
    return {
        "views": num("views") or num("fb_reels_total_plays"),
        "reach": reach,
        "watch_ratio": round(avg_ms / 1000 / duration_s, 3) if avg_ms and duration_s else None,
        "skip_rate": num("reels_skip_rate"),
        "share_rate": round(num("shares") / reach, 4) if num("shares") is not None and reach else None,
        "save_rate": round(num("saved") / reach, 4) if num("saved") is not None and reach else None,
    }


def measured_duration(run: Dict[str, Any]) -> Optional[float]:
    """The master's length as the release gate measured it."""
    import re
    from pathlib import Path
    if not run.get("master_video_path"):
        return None
    try:
        report = json.loads((Path(run["master_video_path"]).parent / "release_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    detail = next((str(c.get("detail")) for c in report.get("checks", []) if c.get("name") == "duration"), "")
    m = re.search(r"Measured ([\d.]+)s", detail)
    return float(m.group(1)) if m else None


def table(db) -> List[Dict[str, Any]]:
    """Latest snapshot per published run and platform, with derived rates."""
    ensure_schema(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM audience_metrics ORDER BY fetched_at").fetchall()
    latest: Dict[tuple, Any] = {}
    for r in rows:
        latest[(r["run_id"], r["platform"])] = r
    out = []
    for (run_id, platform), r in latest.items():
        run = db.get_run(run_id) or {}
        duration = measured_duration(run)
        out.append({"run_id": run_id, "platform": platform, "snapshot": r["snapshot"],
                    "title": (run.get("script_payload") or {}).get("title", run_id),
                    **derived(json.loads(r["metrics_json"]), duration)})
    return out


def digest_lines(db) -> List[str]:
    import html
    rows = [r for r in table(db) if r["platform"] == "instagram"]
    if not rows:
        return ["\n<b>Audience:</b> no published videos measured yet."]
    lines = [f"\n<b>Audience</b> (Instagram, latest snapshot, {len(rows)} video(s)):"]
    for r in sorted(rows, key=lambda r: -(r["views"] or 0))[:6]:
        parts = [f"{int(r['views'])} views" if r["views"] is not None else None,
                 f"watched {r['watch_ratio']:.0%}" if r["watch_ratio"] is not None else None,
                 f"skip {r['skip_rate']:.0f}%" if r["skip_rate"] is not None else None,
                 f"shares {r['share_rate']:.1%}" if r["share_rate"] is not None else None]
        lines.append(f"• {html.escape(r['title'][:40])} ({r['snapshot']}): " + ", ".join(p for p in parts if p))
    if len(rows) < MIN_FOR_PATTERNS:
        lines.append(f"<i>Fewer than {MIN_FOR_PATTERNS} videos: read these as descriptions, not patterns.</i>")
    return lines
