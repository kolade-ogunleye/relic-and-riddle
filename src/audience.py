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
- Upload-Post post analytics (by the request id it published with): the fallback for Reels and the
  source for YouTube (the Short and the documentary), carousels and Story frames.

What is derived per video: watch ratio (average watch time / video length), skip rate, shares and
saves per reached account. With few videos these are descriptions, not conclusions; the digest says
so until there are at least MIN_FOR_PATTERNS videos.

Where it goes: the weekly digest (every platform), /analytics and the status panel's Audience button,
and the audit ledger (file_findings): from MIN_TO_COMPARE videos, a video clearly weaker than the
channel's own median files a hook, retention or shares finding; recurring ones become proposals.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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
PUBLISHED_CHANNELS = ("upload-post", "instagram-graph", "facebook-page", "youtube-shorts",
                      "upload-post:carousel", "upload-post:story")
VIDEO_PLATFORMS = ("instagram", "facebook", "youtube")


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


def upload_post_analytics(request_id: str, platform: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Per-post metrics from Upload-Post, by the request id the post was published with
    (docs.upload-post.com/api/get-analytics, GET /api/uploadposts/post-analytics/{request_id}).
    The answer is keyed platforms.<name>.post_metrics; a platform it could not measure carries
    post_metrics_error, kept as {"unavailable": reason}."""
    if not is_configured(config.UPLOAD_POST_API_KEY):
        return {}
    body = _get(f"{config.UPLOAD_POST_BASE_URL.rstrip('/')}/uploadposts/post-analytics/{request_id}",
                {"platform": platform} if platform else {},
                headers={"Authorization": f"Apikey {config.UPLOAD_POST_API_KEY}"})
    out = {}
    for name, data in (body.get("platforms") or {}).items():
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("post_metrics"), dict):
            out[name] = data["post_metrics"]
        elif data.get("post_metrics_error"):
            out[name] = {"unavailable": str(data["post_metrics_error"])[:200]}
    return out


def targets(pub: Dict[str, Any], run: Dict[str, Any]) -> List[Tuple[str, str, Optional[str]]]:
    """What one publication is measured as: (label stored as its platform, Upload-Post request id,
    platform filter). A Reel is instagram + facebook, its Short youtube, a documentary youtube, a
    carousel <platform>_carousel and each Story frame <platform>_story<part>."""
    channel, res, run_id = pub["channel"], pub["response"], pub["run_id"]
    rid = res.get("request_id") or run_id
    if channel == "youtube-shorts":
        return [("youtube", f"{run_id}:youtube-short", "youtube")]
    if channel.startswith("upload-post:"):
        kind = channel.split(":", 1)[1]
        if not res.get("request_id"):  # recorded before request ids were kept: nothing to look it up by
            return []
        suffix = "carousel" if kind == "carousel" else f"story{res.get('part', 1)}"
        return [(f"{p}_{suffix}", rid, p) for p in (res.get("platforms") or ["instagram", "facebook"])]
    if run.get("content_format") == "deep_dive":
        return [("youtube", rid, "youtube")]
    if channel == "instagram-graph":
        return [("instagram", rid, "instagram")]
    if channel == "facebook-page":
        return [("facebook", rid, "facebook")]
    return [("instagram", rid, None), ("facebook", rid, None)]


# ------------------------------------------------------------------ collection
def due_snapshots(db, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """(publication, snapshot) pairs old enough with a target not yet measured: the earliest one each."""
    ensure_schema(db)
    now = now or datetime.now(timezone.utc)
    with db._conn() as conn:
        pubs = conn.execute("SELECT * FROM publications WHERE status = 'published' AND channel IN (%s)"
                            % ",".join("?" * len(PUBLISHED_CHANNELS)), PUBLISHED_CHANNELS).fetchall()
        taken = {(r["run_id"], r["platform"], r["snapshot"])
                 for r in conn.execute("SELECT run_id, platform, snapshot FROM audience_metrics")}
    due = []
    for p in pubs:
        posted = datetime.fromisoformat(p["created_at"])
        posted = posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)
        pub = {"run_id": p["run_id"], "channel": p["channel"], "response": json.loads(p["response_payload_json"] or "{}")}
        wanted = targets(pub, db.get_run(p["run_id"]) or {})
        for name, days in SNAPSHOTS:
            missing = [t for t in wanted if (p["run_id"], t[0], name) not in taken]
            if now - posted >= timedelta(days=days) and missing:
                due.append({**pub, "snapshot": name, "targets": missing})
                break  # one snapshot per publication per pass: the most overdue one
    return due


def _graph_metrics(label: str, res: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A Reel's metrics from Meta's own insights, when our tokens can read them (else None)."""
    if label == "instagram":
        media_id = instagram_media_id(res)
        return {"source": "instagram-graph", **instagram_insights(media_id)} if media_id else None
    if label == "facebook":
        video = (res.get("post_ids") or {}).get("facebook")
        fb = facebook_insights(video) if video else {}
        return {"source": "facebook-graph", **fb} if fb else None
    return None


def collect(db) -> int:
    """Takes every due snapshot; returns how many rows were stored. A platform that cannot be measured
    is stored as unavailable (so it never holds back the later snapshots); a network failure is retried."""
    stored = 0
    for item in due_snapshots(db):
        res, run_id = item["response"], item["run_id"]
        rows: Dict[str, Dict[str, Any]] = {}
        cache: Dict[tuple, Dict[str, Dict[str, Any]]] = {}
        try:
            for label, rid, platform in item["targets"]:
                data = _graph_metrics(label, res)
                if data is None:
                    key = (rid, platform)
                    if key not in cache:
                        try:
                            cache[key] = upload_post_analytics(rid, platform)
                        except RuntimeError as e:
                            if "HTTP 404" not in str(e):
                                raise
                            cache[key] = {}  # Upload-Post has no such post: nothing will ever be measured
                    base = label.split("_")[0]
                    found = cache[key].get(base)
                    data = ({"source": "upload-post", **found} if found
                            else {"source": "upload-post", "unavailable": f"no {base} metrics for {rid}"})
                rows[label] = data
        except (RuntimeError, requests.RequestException) as e:
            log.warning("Audience metrics for %s (%s) unavailable: %s", run_id, item["snapshot"], e)
            continue
        with db._conn() as conn:
            for label, data in rows.items():
                conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                             (run_id, label, item["snapshot"], data.get("source"), json.dumps(data),
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
    saves = num("saved") if num("saved") is not None else num("saves")  # Meta's name, then Upload-Post's
    return {
        "views": num("views") or num("fb_reels_total_plays") or num("plays"),
        "reach": reach,
        "watch_ratio": round(avg_ms / 1000 / duration_s, 3) if avg_ms and duration_s else None,
        "skip_rate": num("reels_skip_rate"),
        "share_rate": round(num("shares") / reach, 4) if num("shares") is not None and reach else None,
        "save_rate": round(saves / reach, 4) if saves is not None and reach else None,
        "likes": num("likes"),
        "comments": num("comments"),
    }


def card_retention(metrics: Dict[str, Any], duration_s: Optional[float],
                   card: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Facebook's retention graph (the share of viewers still watching at each fortieth of the video) read at
    the end card's start and at the end: how many viewers the card itself loses."""
    graph = metrics.get("post_video_retention_graph")
    out: Dict[str, Optional[float]] = {"card_retention": None, "card_drop": None}
    if not isinstance(graph, dict) or not card or not duration_s or card.get("start_s") is None:
        return out
    try:
        points = {int(k): float(v) for k, v in graph.items()}
    except (TypeError, ValueError):
        return out
    if not points:
        return out
    last = max(points)
    at = min(last, max(0, round(float(card["start_s"]) / duration_s * last)))
    start = points.get(at, points[min(points, key=lambda k: abs(k - at))])
    out["card_retention"] = round(start, 3)
    out["card_drop"] = round(start - points[last], 3)
    return out


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
        metrics = json.loads(r["metrics_json"])
        card = (run.get("script_payload") or {}).get("end_card")
        card = card if isinstance(card, dict) else None
        out.append({"run_id": run_id, "platform": platform, "snapshot": r["snapshot"], "fetched_at": r["fetched_at"],
                    "title": (run.get("script_payload") or {}).get("title", run_id),
                    "unavailable": metrics.get("unavailable"), **derived(metrics, duration),
                    "end_card": card.get("variant") if card else None, **card_retention(metrics, duration, card)})
    return out


def end_card_comparison(rows: List[Dict[str, Any]]) -> Optional[str]:
    """One digest line comparing videos with and without the end card (share rate, watch ratio), once both
    kinds have been measured."""
    def mean(group, key):
        vals = [r[key] for r in group if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None
    videos = [r for r in rows if r["platform"] in VIDEO_PLATFORMS and not r.get("unavailable")]
    with_card, without = [r for r in videos if r.get("end_card")], [r for r in videos if not r.get("end_card")]
    if not with_card or not without:
        return None

    def describe(group):
        share, watch = mean(group, "share_rate"), mean(group, "watch_ratio")
        return (f"{len({r['run_id'] for r in group})} video(s), shares {share:.1%}" if share is not None else
                f"{len({r['run_id'] for r in group})} video(s), shares n/a") + (f", watched {watch:.0%}" if watch is not None else "")
    drops = [r["card_drop"] for r in with_card if r.get("card_drop") is not None]
    drop = f"; the card loses {sum(drops) / len(drops):.0%} of viewers" if drops else ""
    return f"• End card: with it {describe(with_card)}; without {describe(without)}{drop}."


PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


def _parts(r: Dict[str, Any]) -> str:
    parts = [f"{int(r['views'])} views" if r["views"] is not None else None,
             f"watched {r['watch_ratio']:.0%}" if r["watch_ratio"] is not None else None,
             f"skip {r['skip_rate']:.0f}%" if r["skip_rate"] is not None else None,
             f"shares {r['share_rate']:.1%}" if r["share_rate"] is not None else None,
             f"{int(r['likes'])} likes" if r["likes"] is not None and r["views"] is None else None]
    return ", ".join(x for x in parts if x) or "no numbers yet"


def digest_lines(db) -> List[str]:
    """The weekly digest's audience part: each video platform's best videos at their latest snapshot."""
    import html
    rows = [r for r in table(db) if r["platform"] in VIDEO_PLATFORMS and not r.get("unavailable")]
    if not rows:
        return ["\n<b>Audience:</b> no published videos measured yet."]
    lines = [f"\n<b>Audience</b> (latest snapshot, {len({r['run_id'] for r in rows})} video(s)):"]
    for platform in VIDEO_PLATFORMS:
        mine = sorted((r for r in rows if r["platform"] == platform), key=lambda r: -(r["views"] or 0))[:4]
        for r in mine:
            lines.append(f"• {PLATFORM_NAMES[platform]} · {html.escape(r['title'][:40])} ({r['snapshot']}): {_parts(r)}")
    comparison = end_card_comparison(rows)
    if comparison:
        lines.append(comparison)
    if len({r["run_id"] for r in rows}) < MIN_FOR_PATTERNS:
        lines.append(f"<i>Fewer than {MIN_FOR_PATTERNS} videos: read these as descriptions, not patterns.</i>")
    return lines


def analytics_text(db, limit: int = 8) -> str:
    """/analytics: the latest published posts, every platform and post type, newest first."""
    import html
    rows = table(db)
    if not rows:
        return ("📈 <b>Audience</b>\n\nNothing measured yet. Each published post is measured 1, 3, 7 and 28 days "
                "after posting (Instagram, Facebook and YouTube, Reels, Shorts, carousels and Stories).")
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_run.setdefault(r["run_id"], []).append(r)
    order = sorted(by_run, key=lambda rid: max(x["fetched_at"] for x in by_run[rid]), reverse=True)[:limit]
    lines = ["📈 <b>Audience</b> (latest snapshot per post)"]
    for rid in order:
        group = by_run[rid]
        lines.append(f"\n<b>{html.escape(group[0]['title'][:60])}</b>")
        for r in sorted(group, key=lambda x: x["platform"]):
            platform, _, kind = r["platform"].partition("_")
            label = PLATFORM_NAMES.get(platform, platform) + (f" {kind}" if kind else "")
            value = "not measurable: " + html.escape(r["unavailable"][:80]) if r.get("unavailable") else _parts(r)
            lines.append(f"• {label} ({r['snapshot']}): {value}")
    if len(by_run) < MIN_FOR_PATTERNS:
        lines.append(f"\n<i>Fewer than {MIN_FOR_PATTERNS} videos: descriptions, not patterns.</i>")
    return "\n".join(lines)


# ------------------------------------------------------------------ feedback
MIN_TO_COMPARE = 5  # videos measured before one is called weak against the channel's own median


def file_findings(db) -> int:
    """Files weak results into the audit ledger (src/improve.py), each video measured against the channel's
    own median at its 7-day snapshot or later: a high early skip rate is a hook finding, a low watch ratio
    a retention finding, few shares a shareability finding. Recurring classes become proposals there.
    Nothing is filed until MIN_TO_COMPARE videos exist, and each finding is filed once per video."""
    from statistics import median
    from . import improve
    rows = [r for r in table(db) if r["platform"] in ("instagram", "facebook", "youtube")
            and r["snapshot"] in ("7d", "28d") and not r.get("unavailable")]
    if len({r["run_id"] for r in rows}) < MIN_TO_COMPARE:
        return 0
    improve.ensure_schema(db)
    with db._conn() as conn:
        filed = {(f["run_id"], f["klass"]) for f in conn.execute("SELECT run_id, klass FROM findings WHERE klass LIKE 'audience.%'")}
    checks = (("skip_rate", "audience.hook", lambda v, m: v > m * 1.25, "skip rate {v:.0f}% (channel median {m:.0f}%)"),
              ("watch_ratio", "audience.retention", lambda v, m: v < m * 0.8, "watched {v:.0%} (channel median {m:.0%})"),
              ("share_rate", "audience.shares", lambda v, m: v < m * 0.5, "shares {v:.2%} (channel median {m:.2%})"))
    n = 0
    for metric, klass, weak, detail in checks:
        for platform in ("instagram", "facebook", "youtube"):
            values = [r for r in rows if r["platform"] == platform and r.get(metric) is not None]
            if len(values) < MIN_TO_COMPARE:
                continue
            m = median(r[metric] for r in values)
            for r in values:
                if (r["run_id"], klass) not in filed and weak(r[metric], m):
                    run = db.get_run(r["run_id"]) or {}
                    improve.record(db, klass, f"{PLATFORM_NAMES[platform]}: " + detail.format(v=r[metric], m=m),
                                   "audience", run_id=r["run_id"], topic_id=run.get("topic_id"))
                    filed.add((r["run_id"], klass))
                    n += 1
    return n
