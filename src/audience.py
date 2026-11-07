"""
src/audience.py
===============
How the published videos actually did, measured where the platforms measure it.

Snapshots are taken 1, 3, 7 and 28 days after posting (Meta's insights can lag up to 48 hours,
so the first one is a first look, not a verdict). A Story is measured once, STORY_SNAPSHOT_HOURS
after posting: Instagram keeps story insights for 24 hours only (developers.facebook.com/docs/
instagram-platform/reference/instagram-media/insights, "Story metrics are available for 24 hours",
read 2026-10-28), so the 1-day snapshot always came too late (production, every story snapshot
"HTTP 400", 2026-10-28). Sources, in order, for each post:
- Instagram Graph API media insights (META_ACCESS_TOKEN, META_IG_ACCOUNT_ID): views, reach, average
  watch time, skip rate in the first 3 seconds, shares, saves, likes, comments; for a Story views,
  reach, shares, follows, profile visits, replies, navigation. Metrics Meta marks "in development"
  are requested one by one, so one unsupported metric never empties the rest.
- Facebook video insights (META_PAGE_ACCESS_TOKEN, else META_ACCESS_TOKEN): plays, average time
  watched, retention curve, follows.
- Upload-Post post analytics (by the request id it published with): the fallback for Reels and
  Stories and the source for YouTube (the Short and the documentary) and carousels.

The Graph needs the post's own id. Upload-Post publishes our Reels, and the direct publishers'
post_ids were the only ids the Graph path ever read, so on production it never ran (2026-10-28:
every audience_metrics row came from "upload-post" though the Meta tokens are configured). The id
is now found, in order: post_ids recorded by a direct publisher; the platform_post_id in the
stored Upload-Post answer; Upload-Post's upload history by request id (docs.upload-post.com/api/
upload-history, GET /api/uploadposts/history?request_id=); the id in the post URL (Facebook's
/reel/<video id>); the account's recent media (Instagram /media or /stories, Facebook
/video_reels) matched by permalink, else by publication time. A found id is kept in
audience_media_ids, so later snapshots (and an expired Story's) do not search again.

Every source's outcome is logged per post (info when it answered, a warning with the API's own
message when not; tokens are redacted from every message) and kept in audience_sources, which
sources_health() summarises for the dashboard.

What is derived per video: watch ratio (average watch time / video length), skip rate, shares and
saves per reached account. With few videos these are descriptions, not conclusions; the digest says
so until there are at least MIN_FOR_PATTERNS videos.

Where it goes: the weekly digest (every platform), /analytics and the status panel's Audience button,
and the audit ledger (file_findings): from MIN_TO_COMPARE videos, a video clearly weaker than the
channel's own median files a hook, retention or shares finding; recurring ones become proposals.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import config
from .config import is_configured

log = logging.getLogger(__name__)

SNAPSHOTS = (("1d", 1), ("3d", 3), ("7d", 7), ("28d", 28))
# A Story's one snapshot: late enough for most of its views, early enough that the 6-hourly collector
# (main.py, minute 20) always has one pass before the 24-hour expiry. Instagram documents story
# insights for 24 hours (developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights).
STORY_SNAPSHOT = "story"
STORY_SNAPSHOT_HOURS = 18
STORY_LIFETIME_HOURS = 24
# developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights (read 2026-10-28):
# REELS: views, reach, ig_reels_avg_watch_time, reels_skip_rate, shares, saved, likes, comments,
# total_interactions; STORY: views, reach, shares, total_interactions, follows, profile_visits,
# replies, navigation (impressions is deprecated for media made after 2 July 2024).
IG_METRICS = ("views", "reach", "ig_reels_avg_watch_time", "reels_skip_rate", "shares", "saved",
              "likes", "comments", "total_interactions")
IG_STORY_METRICS = ("views", "reach", "shares", "total_interactions", "follows", "profile_visits",
                    "replies", "navigation")
# developers.facebook.com/docs/graph-api/reference/video/video_insights (Reels, lifetime period).
FB_METRICS = ("blue_reels_play_count", "fb_reels_total_plays", "post_impressions_unique",
              "post_video_avg_time_watched", "post_video_followers", "post_video_retention_graph")
MIN_FOR_PATTERNS = 12
PUBLISHED_CHANNELS = ("upload-post", "instagram-graph", "facebook-page", "youtube-shorts",
                      "upload-post:carousel", "upload-post:story")
VIDEO_PLATFORMS = ("instagram", "facebook", "youtube")
# How far a post's own timestamp may lie from our publication record (written after Upload-Post's
# asynchronous upload finished) for a timestamp match.
MATCH_WINDOW = {"reel": timedelta(minutes=45), "story": timedelta(minutes=20)}
GRAPH = "https://graph.facebook.com"


def ensure_schema(db) -> None:
    with db._conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS audience_metrics (
            run_id TEXT NOT NULL, platform TEXT NOT NULL, snapshot TEXT NOT NULL, source TEXT,
            metrics_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
            PRIMARY KEY (run_id, platform, snapshot))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audience_media_ids (
            run_id TEXT NOT NULL, platform TEXT NOT NULL, media_id TEXT NOT NULL, how TEXT,
            found_at TEXT NOT NULL, PRIMARY KEY (run_id, platform))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audience_sources (
            collected_at TEXT NOT NULL, run_id TEXT NOT NULL, snapshot TEXT NOT NULL, platform TEXT NOT NULL,
            source TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT)""")


def _secrets() -> List[str]:
    from . import youtube_analytics
    return [s for s in (config.META_ACCESS_TOKEN, config.META_PAGE_ACCESS_TOKEN, config.UPLOAD_POST_API_KEY,
                        *youtube_analytics.secrets()) if s and len(s) >= 6]


def _redact(text: Any) -> str:
    """Any message that may reach a log or the database, without a credential: requests' own errors
    carry the full URL, query string (access_token=...) included."""
    text = str(text)
    for s in _secrets():
        text = text.replace(s, "***")
    text = re.sub(r"(access_token=)[^&\s'\"]+", r"\1***", text)
    return re.sub(r"(Apikey\s+)\S+", r"\1***", text)


def _get(url: str, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    try:
        resp = requests.get(url, params=params, headers=headers or {}, timeout=30)
    except requests.RequestException as e:
        raise requests.RequestException(_redact(e)) from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok or "error" in body:
        err = body.get("error")
        if isinstance(err, dict):  # Graph: {"error": {"message", "code", "error_subcode"}}
            detail = f"{err.get('message', '')} (code {err.get('code')})" if err.get("code") is not None else err.get("message", "")
        else:
            detail = err or body.get("message") or ""
        raise RuntimeError(_redact(f"HTTP {resp.status_code}: {detail}"))
    return body


# ------------------------------------------------------------------ ids
def _is_story(label: str) -> bool:
    return "_story" in label


def _result_rows(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The per-platform rows of a stored Upload-Post answer (status or synchronous form)."""
    rows: List[Dict[str, Any]] = []
    for src in (res.get("results"), (res.get("raw") or {}).get("results")):
        if isinstance(src, dict):
            rows += [{"platform": k, **v} for k, v in src.items() if isinstance(v, dict)]
        elif isinstance(src, list):
            rows += [r for r in src if isinstance(r, dict)]
    return rows


def stored_post_id(res: Dict[str, Any], platform: str) -> Optional[str]:
    """The platform's post id recorded at publish time: a direct publisher's post_ids, else the
    platform_post_id of Upload-Post's answer (docs.upload-post.com/api/upload-history names the field;
    string, array or null)."""
    if (res.get("post_ids") or {}).get(platform):
        return str(res["post_ids"][platform])
    for row in _result_rows(res):
        if row.get("platform") != platform:
            continue
        for key in ("platform_post_id", "post_id", "media_id", "video_id"):
            value = row.get(key)
            value = (value[0] if value else None) if isinstance(value, list) else value
            if value:
                return str(value)
    return None


def _post_url(res: Dict[str, Any], platform: str) -> str:
    url = (res.get("post_urls") or {}).get(platform)
    if not url:
        url = next((r.get("post_url") or r.get("url") for r in _result_rows(res)
                    if r.get("platform") == platform and (r.get("post_url") or r.get("url"))), None)
    return str(url or "")


def instagram_shortcode(url: str) -> Optional[str]:
    """The shortcode of a post URL (/p/, /reel/, /reels/, /tv/); a profile or /stories/<user>/ URL has
    none. The old lookup took the URL's last segment, so a profile-style URL matched nothing, silently."""
    m = re.search(r"instagram\.com/(?:[^/?#]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else None


def facebook_video_id(url: str) -> Optional[str]:
    """The video id in a Facebook Reel or video URL (https://www.facebook.com/reel/1419040693748996)."""
    m = re.search(r"facebook\.com/(?:[^?#]*/)?(?:reels?|videos)/(\d+)", url or "") or \
        re.search(r"[?&](?:v|video_id)=(\d+)", url or "")
    return m.group(1) if m else None


def upload_post_post_id(request_id: str, platform: str) -> Optional[str]:
    """platform_post_id from Upload-Post's upload history, filtered by request id (exact match, one row
    per platform; docs.upload-post.com/api/upload-history)."""
    if not request_id or not is_configured(config.UPLOAD_POST_API_KEY):
        return None
    body = _get(f"{config.UPLOAD_POST_BASE_URL.rstrip('/')}/uploadposts/history",
                {"request_id": request_id, "platform": platform, "limit": 10},
                headers={"Authorization": f"Apikey {config.UPLOAD_POST_API_KEY}"})
    for row in body.get("history") or []:
        if row.get("platform") == platform and row.get("success") is not False:
            value = row.get("platform_post_id")
            value = (value[0] if value else None) if isinstance(value, list) else value
            if value:
                return str(value)
    return None


def _when(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).replace("Z", "+00:00")
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)  # Graph's 2026-10-27T18:02:11+0000
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _nearest(items: List[Dict[str, Any]], posted: Optional[datetime], window: timedelta,
             taken: set, ts_key: str) -> Optional[Dict[str, Any]]:
    if posted is None:
        return None
    best, gap = None, window
    for m in items:
        when = _when(m.get(ts_key))
        if when is None or str(m.get("id")) in taken:
            continue
        if abs(when - posted) <= gap:
            best, gap = m, abs(when - posted)
    return best


def _ig_configured() -> bool:
    return is_configured(config.META_ACCESS_TOKEN) and is_configured(config.META_IG_ACCOUNT_ID)


def _fb_token() -> str:
    return config.META_PAGE_ACCESS_TOKEN or config.META_ACCESS_TOKEN or ""


def instagram_media_id(post: Dict[str, Any], posted: Optional[datetime] = None, story: bool = False,
                       taken: Optional[set] = None) -> Tuple[Optional[str], str]:
    """(IG media id, how it was found) from the stored answer, else the account's recent media
    (/media for a Reel, /stories for a live Story) by permalink shortcode, else by publication time."""
    stored = stored_post_id(post, "instagram")
    if stored:
        return stored, "stored publish response"
    if not _ig_configured():
        return None, "not configured (META_ACCESS_TOKEN, META_IG_ACCOUNT_ID)"
    edge = "stories" if story else "media"
    body = _get(f"{GRAPH}/{config.META_GRAPH_VERSION}/{config.META_IG_ACCOUNT_ID}/{edge}",
                {"fields": "id,permalink,timestamp,media_product_type,media_type", "limit": 50,
                 "access_token": config.META_ACCESS_TOKEN})
    media = body.get("data") or []
    shortcode = instagram_shortcode(_post_url(post, "instagram"))
    if shortcode:
        hit = next((m for m in media if f"/{shortcode}" in (m.get("permalink") or "")), None)
        if hit:
            return str(hit["id"]), "graph permalink"
    pool = media if story else [m for m in media if m.get("media_product_type") in ("REELS", None)]
    hit = _nearest(pool, posted, MATCH_WINDOW["story" if story else "reel"], taken or set(), "timestamp")
    if hit:
        return str(hit["id"]), "graph timestamp"
    return None, f"no {edge} item matched (of {len(media)} recent)"


def facebook_video_id_for(post: Dict[str, Any], posted: Optional[datetime] = None,
                          taken: Optional[set] = None) -> Tuple[Optional[str], str]:
    """(Facebook video id, how): the stored answer, the Reel URL, else the Page's /video_reels by time."""
    stored = stored_post_id(post, "facebook")
    if stored:
        return stored, "stored publish response"
    from_url = facebook_video_id(_post_url(post, "facebook"))
    if from_url:
        return from_url, "post URL"
    if not is_configured(_fb_token()) or not is_configured(config.META_PAGE_ID):
        return None, "not configured (META_PAGE_ACCESS_TOKEN, META_PAGE_ID)"
    body = _get(f"{GRAPH}/{config.META_GRAPH_VERSION}/{config.META_PAGE_ID}/video_reels",
                {"fields": "id,created_time,permalink_url", "limit": 25, "access_token": _fb_token()})
    hit = _nearest(body.get("data") or [], posted, MATCH_WINDOW["reel"], taken or set(), "created_time")
    return (str(hit["id"]), "graph timestamp") if hit else (None, "no Page reel matched")


# ------------------------------------------------------------------ sources
def instagram_insights(media_id: str, story: bool = False) -> Dict[str, Any]:
    """The media's insights; raises RuntimeError (Meta's own message) when none could be read."""
    metrics = IG_STORY_METRICS if story else IG_METRICS
    base = f"{GRAPH}/{config.META_GRAPH_VERSION}/{media_id}/insights"
    out: Dict[str, Any] = {}
    errors: List[str] = []
    try:
        rows = _get(base, {"metric": ",".join(metrics), "access_token": config.META_ACCESS_TOKEN}).get("data", [])
    except RuntimeError as first:
        errors.append(str(first))
        rows = []
        for m in metrics:  # one unsupported metric fails the whole request; ask one by one
            try:
                rows += _get(base, {"metric": m, "access_token": config.META_ACCESS_TOKEN}).get("data", [])
            except RuntimeError as e:
                errors.append(f"{m}: {e}")
                log.debug("IG metric %s unavailable: %s", m, e)
    for r in rows:
        values = r.get("values")
        out[r.get("name")] = values[0].get("value") if values else (r.get("total_value") or {}).get("value")
    out = {k: v for k, v in out.items() if k and v is not None}
    if not out:
        # Meta: unavailable insights are an empty data set, not 0; a story under 5 viewers is error #10.
        raise RuntimeError(errors[0] if errors else "no insights returned yet (Meta can lag up to 48 hours)")
    out["media_id"] = media_id
    return out


def facebook_insights(video_id: str) -> Dict[str, Any]:
    """A Facebook video's insights (Page token; raises RuntimeError with Meta's message)."""
    token = _fb_token()
    if not is_configured(token):
        return {}
    body = _get(f"{GRAPH}/{config.META_GRAPH_VERSION}/{video_id}/video_insights",
                {"metric": ",".join(FB_METRICS), "access_token": token})
    out = {r.get("name"): (r.get("values") or [{}])[0].get("value") for r in body.get("data", [])}
    out = {k: v for k, v in out.items() if k and v is not None}
    if not out:
        raise RuntimeError("no video insights returned yet (Meta can lag up to 48 hours)")
    out["video_id"] = video_id
    return out


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
            out[name] = {"unavailable": _redact(data["post_metrics_error"])[:200]}
    return out


def targets(pub: Dict[str, Any], run: Dict[str, Any]) -> List[Tuple[str, str, Optional[str]]]:
    """What one publication is measured as: (label stored as its platform, Upload-Post request id,
    platform filter). A Reel is instagram + facebook, its Short youtube, a documentary youtube, a
    carousel <platform>_carousel and each Story frame <platform>_story<part>."""
    channel, res, run_id = pub["channel"], pub["response"], pub["run_id"]
    rid = res.get("request_id") or (res.get("raw") or {}).get("request_id") or run_id
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
def _posted_at(created_at: str) -> datetime:
    posted = datetime.fromisoformat(created_at)
    return posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)


def due_snapshots(db, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """(publication, snapshot) pairs old enough with a target not yet measured: the earliest one each.
    A Story's frames have one snapshot, STORY_SNAPSHOT, due STORY_SNAPSHOT_HOURS after posting."""
    ensure_schema(db)
    now = now or datetime.now(timezone.utc)
    with db._conn() as conn:
        pubs = conn.execute("SELECT * FROM publications WHERE status = 'published' AND channel IN (%s)"
                            % ",".join("?" * len(PUBLISHED_CHANNELS)), PUBLISHED_CHANNELS).fetchall()
        taken = {(r["run_id"], r["platform"], r["snapshot"])
                 for r in conn.execute("SELECT run_id, platform, snapshot FROM audience_metrics")}
    due = []
    keep = timedelta(days=getattr(db, "RETENTION_DAYS", {}).get("audience_metrics", 730))
    for p in pubs:
        posted = _posted_at(p["created_at"])
        if now - posted >= keep:
            continue  # past the metrics' retention (db.prune_history): its pruned rows are not measured again
        pub = {"run_id": p["run_id"], "channel": p["channel"], "response": json.loads(p["response_payload_json"] or "{}"),
               "posted_at": posted.isoformat()}
        wanted = targets(pub, db.get_run(p["run_id"]) or {})
        stories = [t for t in wanted if _is_story(t[0])]
        others = [t for t in wanted if not _is_story(t[0])]
        measured_before = {plat for run, plat, _snap in taken if run == p["run_id"]}  # pre-rc68 1d/3d rows
        missing = [t for t in stories if (p["run_id"], t[0], STORY_SNAPSHOT) not in taken and t[0] not in measured_before]
        if missing and now - posted >= timedelta(hours=STORY_SNAPSHOT_HOURS):
            due.append({**pub, "snapshot": STORY_SNAPSHOT, "targets": missing})
        for name, days in SNAPSHOTS:
            missing = [t for t in others if (p["run_id"], t[0], name) not in taken]
            if now - posted >= timedelta(days=days) and missing:
                due.append({**pub, "snapshot": name, "targets": missing})
                break  # one snapshot per publication per pass: the most overdue one
    return due


def _known_ids(db, run_id: str) -> Dict[str, str]:
    with db._conn() as conn:
        return {r["platform"]: r["media_id"] for r in
                conn.execute("SELECT platform, media_id FROM audience_media_ids WHERE run_id = ?", (run_id,))}


def _remember_id(db, run_id: str, label: str, media_id: str, how: str) -> None:
    with db._conn() as conn:
        conn.execute("INSERT OR IGNORE INTO audience_media_ids VALUES (?,?,?,?,?)",
                     (run_id, label, media_id, how, datetime.now(timezone.utc).isoformat()))


def _graph_id(db, label: str, item: Dict[str, Any], rid: str) -> Tuple[Optional[str], str]:
    """The Meta id of one target: remembered, stored, from Upload-Post's history, else matched."""
    run_id, res = item["run_id"], item["response"]
    known = _known_ids(db, run_id)
    if known.get(label):
        return known[label], "remembered"
    base = label.split("_")[0]
    found, how = (stored_post_id(res, base), "stored publish response")
    if not found and base == "facebook":
        found, how = facebook_video_id(_post_url(res, "facebook")), "post URL"
    if not found:  # Upload-Post published it: its history names the platform's own post id
        try:
            found, how = upload_post_post_id(rid, base), "upload-post history"
        except (RuntimeError, requests.RequestException) as e:
            log.warning("Audience %s %s: Upload-Post history lookup failed: %s", run_id, label, _redact(e))
            found = None
    if not found:
        posted = _when(item.get("posted_at"))
        taken = {v for k, v in known.items() if k.split("_")[0] == base}
        if base == "instagram":
            found, how = instagram_media_id(res, posted, story=_is_story(label), taken=taken)
        elif base == "facebook":
            found, how = facebook_video_id_for(res, posted, taken=taken)
    if found:
        _remember_id(db, run_id, label, found, how)
    return found, how


def _graph_metrics(label: str, res: Dict[str, Any], db=None, item: Optional[Dict[str, Any]] = None,
                   rid: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """(metrics, outcome): a post's metrics from Meta's own insights, or (None, why not)."""
    base = label.split("_")[0]
    if base not in ("instagram", "facebook") or label.endswith("_carousel"):
        return None, "skipped: no Graph source for this post type"
    if base == "facebook" and _is_story(label):
        # Page stories: the Page Stories API lists them (developers.facebook.com/docs/page-stories-api) but
        # documents no insights endpoint (checked 2026-10-28), so Upload-Post stays their only source.
        return None, "skipped: Meta documents no insights for Page stories"
    if base == "instagram" and not _ig_configured():
        return None, "skipped: not configured (META_ACCESS_TOKEN, META_IG_ACCOUNT_ID)"
    if base == "facebook" and not is_configured(_fb_token()):
        return None, "skipped: not configured (META_PAGE_ACCESS_TOKEN)"
    item = item or {"run_id": "", "response": res}
    if db is not None:
        media_id, how = _graph_id(db, label, item, rid)
    elif base == "instagram":
        media_id, how = instagram_media_id(res, _when(item.get("posted_at")), story=_is_story(label))
    else:
        media_id, how = facebook_video_id_for(res, _when(item.get("posted_at")))
    if not media_id:
        raise RuntimeError(f"post id not found ({how})")
    data = instagram_insights(media_id, story=_is_story(label)) if base == "instagram" else facebook_insights(media_id)
    return {"source": f"{base}-graph", **data}, f"ok (id {how})"


GRAPH_NAMES = {"instagram": "Instagram Graph", "facebook": "Facebook Graph"}


def _record_sources(db, collected_at: str, run_id: str, snapshot: str, outcomes: List[Tuple[str, str, str, str]]) -> None:
    with db._conn() as conn:
        conn.executemany("INSERT INTO audience_sources VALUES (?,?,?,?,?,?,?)",
                         [(collected_at, run_id, snapshot, label, source, outcome, _redact(detail)[:300])
                          for label, source, outcome, detail in outcomes])


def _add_youtube_retention(data: dict[str, Any], res: dict[str, Any], item: dict[str, Any], outcomes: list,
                           label: str, run_id: str, snapshot: str) -> None:
    """A Short's retention curve from YouTube Analytics (src/youtube_analytics.py), into its metrics as
    yt_retention_graph, when the owner's consent is configured; its outcome is logged and kept like any
    source's, and a failure never costs the counts already measured."""
    from . import youtube_analytics
    if not youtube_analytics.configured():
        return
    vid = youtube_analytics.video_id(res)
    if not vid:
        outcomes.append((label, "youtube-analytics", "skipped", "no video id recorded"))
        return
    posted = _when(item.get("posted_at"))
    try:
        curve = youtube_analytics.retention(vid, posted.date() if posted else None)
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        outcomes.append((label, "youtube-analytics", "failed", _redact(e)))
        log.warning("Audience %s %s %s: YouTube Analytics failed: %s", run_id, snapshot, label, _redact(e))
        return
    if curve:
        data["yt_retention_graph"] = curve
        outcomes.append((label, "youtube-analytics", "ok", f"retention curve ({len(curve)} points)"))
    else:
        outcomes.append((label, "youtube-analytics", "skipped", "no retention rows yet"))


def collect(db, now: Optional[datetime] = None) -> int:
    """Takes every due snapshot; returns how many rows were stored. Meta's Graph first, Upload-Post when
    the Graph cannot answer. A platform that cannot be measured is stored as unavailable (so it never
    holds back the later snapshots); a network failure of the fallback is retried. A Story collected
    after its 24 hours is recorded as expired, without asking an API that can no longer answer."""
    now = now or datetime.now(timezone.utc)
    collected_at = now.isoformat()
    stored = 0
    for item in due_snapshots(db, now):
        res, run_id, snapshot = item["response"], item["run_id"], item["snapshot"]
        rows: Dict[str, Dict[str, Any]] = {}
        outcomes: List[Tuple[str, str, str, str]] = []
        cache: Dict[tuple, Dict[str, Dict[str, Any]]] = {}
        age = now - (_when(item.get("posted_at")) or now)
        try:
            for label, rid, platform in item["targets"]:
                base = label.split("_")[0]
                if _is_story(label) and age >= timedelta(hours=STORY_LIFETIME_HOURS):
                    why = (f"story expired before it was measured ({age.total_seconds() / 3600:.0f} h after posting; "
                           f"Meta keeps story insights {STORY_LIFETIME_HOURS} h)")
                    rows[label] = {"source": "expired", "unavailable": why}
                    outcomes.append((label, "expired", "failed", why))
                    log.warning("Audience %s %s %s: %s", run_id, snapshot, label, why)
                    continue
                graph_error = ""
                try:
                    data, outcome = _graph_metrics(label, res, db, item, rid)
                except (RuntimeError, requests.RequestException) as e:
                    data, graph_error = None, _redact(e)
                    outcomes.append((label, f"{base}-graph", "failed", graph_error))
                    log.warning("Audience %s %s %s: %s failed: %s; falling back to Upload-Post",
                                run_id, snapshot, label, GRAPH_NAMES.get(base, base), graph_error)
                else:
                    if data is not None:
                        outcomes.append((label, data["source"], "ok", outcome))
                        log.info("Audience %s %s %s: %s answered, %s", run_id, snapshot, label,
                                 GRAPH_NAMES.get(base, base), outcome)
                    else:
                        outcomes.append((label, f"{base}-graph", "skipped", outcome))
                        log.info("Audience %s %s %s: Graph %s", run_id, snapshot, label, outcome)
                if data is None:
                    key = (rid, platform)
                    if key not in cache:
                        try:
                            cache[key] = upload_post_analytics(rid, platform)
                        except RuntimeError as e:
                            if "HTTP 404" not in str(e):
                                raise
                            cache[key] = {}  # Upload-Post has no such post: nothing will ever be measured
                    found = cache[key].get(base)
                    if found and not found.get("unavailable"):
                        data = {"source": "upload-post", **found}
                        outcomes.append((label, "upload-post", "ok", "post analytics"))
                        log.info("Audience %s %s %s: Upload-Post answered", run_id, snapshot, label)
                    else:
                        reason = (found or {}).get("unavailable") or f"no {base} metrics for {rid}"
                        if graph_error:
                            reason = f"{GRAPH_NAMES.get(base, base)}: {graph_error}; Upload-Post: {reason}"
                        data = {"source": "upload-post", "unavailable": reason[:300]}
                        outcomes.append((label, "upload-post", "failed", (found or {}).get("unavailable") or "no metrics"))
                        log.warning("Audience %s %s %s: no source answered: %s", run_id, snapshot, label, reason)
                if base == "youtube" and not data.get("unavailable"):
                    _add_youtube_retention(data, res, item, outcomes, label, run_id, snapshot)
                rows[label] = data
        except (RuntimeError, requests.RequestException) as e:
            log.warning("Audience metrics for %s (%s) unavailable: %s", run_id, snapshot, _redact(e))
            _record_sources(db, collected_at, run_id, snapshot,
                            outcomes + [("*", "upload-post", "failed", f"retried next pass: {_redact(e)}")])
            continue
        with db._conn() as conn:
            for label, data in rows.items():
                conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                             (run_id, label, snapshot, data.get("source"), json.dumps(data),
                              datetime.now(timezone.utc).isoformat()))
                stored += 1
        _record_sources(db, collected_at, run_id, snapshot, outcomes)
    return stored


def sources_health(db) -> Dict[str, Any]:
    """Which source answered for which platform on the last collection that measured anything:
    {"collected_at", "platforms": {platform: {source: {"ok", "failed", "skipped", "last_error"}}},
    "lines": [...]} for a dashboard (platform is the base: instagram, facebook, youtube)."""
    ensure_schema(db)
    with db._conn() as conn:
        last = conn.execute("SELECT MAX(collected_at) AS t FROM audience_sources").fetchone()["t"]
        rows = conn.execute("SELECT * FROM audience_sources WHERE collected_at = ? ORDER BY rowid",
                            (last,)).fetchall() if last else []
    platforms: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        base = r["platform"].split("_")[0]
        entry = platforms.setdefault(base, {}).setdefault(r["source"], {"ok": 0, "failed": 0, "skipped": 0, "last_error": None})
        entry[r["outcome"]] = entry.get(r["outcome"], 0) + 1
        if r["outcome"] == "failed":
            entry["last_error"] = r["detail"]
        elif r["outcome"] == "skipped" and not entry["last_error"]:
            entry["last_error"] = r["detail"]
    lines = []
    for base, sources in platforms.items():
        parts = []
        for source, e in sources.items():
            state = (f"{e['ok']} ok" if e["ok"] else "") + (f"{', ' if e['ok'] else ''}{e['failed']} failed" if e["failed"] else "")
            state = state or "not used"
            note = f" ({e['last_error'][:80]})" if e["last_error"] and not e["ok"] else ""
            parts.append(f"{source}: {state}{note}")
        lines.append(f"{PLATFORM_NAMES.get(base, base)}: " + "; ".join(parts))
    return {"collected_at": last, "platforms": platforms, "lines": lines}


# ------------------------------------------------------------------ reading
def derived(metrics: Dict[str, Any], duration_s: Optional[float]) -> Dict[str, Optional[float]]:
    def num(k):
        v = metrics.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def first(*keys):
        # The first figure the platform reported, a real 0 included (2026-10-29): `a or b` turned a stored 0
        # views into None, so the dashboard and digest showed a Reel nobody watched as a gap.
        for k in keys:
            if num(k) is not None:
                return num(k)
        return None
    reach = first("reach", "post_impressions_unique")
    avg_ms = num("ig_reels_avg_watch_time") or num("post_video_avg_time_watched")
    saves = num("saved") if num("saved") is not None else num("saves")  # Meta's name, then Upload-Post's
    return {
        "views": first("views", "fb_reels_total_plays", "plays"),
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


LEGACY_ROTATION = ("badge", "lower_third", "minimal", "depth_sandwich")   # main.py's rotation until 2026-11-01


def cover_style(db, run_id: str, run: dict[str, Any]) -> str | None:
    """The cover style a run's Reel went out with: the style recorded beside its cover (still_layout recipe),
    else the per-run rotation that chose it before cover_selector (LEGACY_ROTATION by run key). None when neither
    is known."""
    import sqlite3
    from pathlib import Path

    from . import still_layout
    from .visual_engine import RENDERABLE_STYLES
    if run.get("master_video_path"):
        entry = still_layout.load(Path(run["master_video_path"]).parent / "thumbnail_cover.jpg") or {}
        style = (entry.get("recipe") or {}).get("style")
        if style in RENDERABLE_STYLES:
            return style
    try:
        key = db.run_key(run_id)
    except (sqlite3.Error, ValueError, TypeError):  # no run row: no rotation to recover
        return None
    # Runs without a record predate cover_selector: they were rotated through the order in use then.
    return LEGACY_ROTATION[int(key) % len(LEGACY_ROTATION)] if str(key).isdigit() else None


def _curve(metrics: dict[str, Any]) -> dict[int, float] | None:
    """A video's retention curve as {point: share watching}, the last point being the video's end: Facebook's
    graph (fortieths) or YouTube's (hundredths, audienceWatchRatio: above 1 where Shorts loop); None without one."""
    for key in ("post_video_retention_graph", "yt_retention_graph"):
        graph = metrics.get(key)
        if isinstance(graph, dict) and graph:
            try:
                return {int(k): float(v) for k, v in graph.items()}
            except (TypeError, ValueError):
                return None
    return None


def hold_at(metrics: dict[str, Any], duration_s: float | None, seconds: float = 3.0) -> float | None:
    """The share of viewers still watching `seconds` in, from the video's retention curve (Facebook's or
    YouTube's), at most 1 (a Short's loops count once); None without a curve or a length."""
    points = _curve(metrics)
    if not points or not duration_s:
        return None
    last = max(points)
    at = min(last, max(0, round(seconds / duration_s * last)))
    value = points.get(at, points[min(points, key=lambda k: abs(k - at))])
    return round(min(1.0, value), 3)


BEAT_DROP_FLAG = 0.15   # a beat that loses this share of the viewers it started with is filed as a finding
HOOK_SECONDS = 3.0      # the opening is the hook's (hook_learning); beats are read after it


def _retention_at(points: dict[int, float], duration_s: float, t: float) -> float:
    """The share still watching at `t` seconds, linearly between the graph's fortieths."""
    last = max(points)
    x = min(last, max(0.0, t / duration_s * last))
    lo, hi = int(x), min(last, int(x) + 1)
    a = points.get(lo, points[min(points, key=lambda k: abs(k - lo))])
    b = points.get(hi, points[min(points, key=lambda k: abs(k - hi))])
    return a + (b - a) * (x - lo)


def beat_drops(metrics: dict[str, Any], duration_s: float | None, beats: list[dict[str, Any]],
               end_s: float | None = None) -> list[dict[str, Any]]:
    """Each narration beat after the hook with the share of its starting viewers it lost, from the video's
    retention curve (Facebook's or YouTube's): [{"text", "start", "end", "lost"}], in order; [] without a curve,
    a length or beats. A beat runs to the next one's start (the last to `end_s`, the end card's start, else the
    video's end)."""
    points = _curve(metrics)
    if not points or not duration_s or not beats:
        return []
    out = []
    starts = [float(b.get("start") or 0.0) for b in beats]
    for i, b in enumerate(beats):
        start = max(starts[i], HOOK_SECONDS)
        end = starts[i + 1] if i + 1 < len(beats) else float(end_s or duration_s)
        if end <= start:
            continue
        before, after = _retention_at(points, duration_s, start), _retention_at(points, duration_s, end)
        if before <= 0:
            continue
        out.append({"text": str(b.get("text") or ""), "start": round(start, 1), "end": round(end, 1),
                    "lost": round(max(0.0, (before - after) / before), 3)})
    return out


def opening_fields(run: dict[str, Any], metrics: dict[str, Any], duration_s: float | None) -> dict[str, Any]:
    """How a Reel opened and how its first three seconds did: the hook's form as written (hook_learning.classify:
    read from the sentence itself, so earlier runs are measured too), the opening shot recorded at render, and
    the share still watching at 3 s."""
    from .hook_learning import classify
    payload = run.get("script_payload") or {}
    opening = payload.get("opening") if isinstance(payload.get("opening"), dict) else {}
    card = payload.get("end_card") if isinstance(payload.get("end_card"), dict) else {}
    drops = beat_drops(metrics, duration_s, payload.get("beat_times") or [], card.get("start_s"))
    from .engagement_learning import caption_question, classify_question, classify_share
    return {"hook_form": classify(str(payload.get("hook_sentence") or "")), "opening_shot": opening.get("shot"),
            "hold_3s": hold_at(metrics, duration_s),
            "worst_beat": max(drops, key=lambda d: d["lost"]) if drops else None,
            "hook_plan": payload.get("hook_plan") if isinstance(payload.get("hook_plan"), dict) else None,
            "share_form": classify_share(str(payload.get("share_line") or "")),
            "question_form": classify_question(caption_question(str(payload.get("caption") or "")))}


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
                    "end_card": card.get("variant") if card else None, **card_retention(metrics, duration, card),
                    "cover_style": cover_style(db, run_id, run), **opening_fields(run, metrics, duration)})
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


MIN_PER_STYLE = 8   # videos per cover style on a platform before its comparison is more than an early signal


def cover_style_comparison(rows: list[dict[str, Any]]) -> list[str]:
    """Digest lines comparing the cover styles, one per video platform (Instagram, Facebook, YouTube) on which two
    styles have been measured, by the rates that do not depend on a topic's reach: 3-second skip rate where
    Instagram reports it (lowest first), shares and saves per account reached, and watch ratio. Each style's
    median, so one viral topic does not decide it; an early signal until each has MIN_PER_STYLE videos there."""
    from statistics import median

    def med(group, key):
        vals = [r[key] for r in group if r.get(key) is not None]
        return median(vals) if vals else None

    def describe(style, group):
        skip, share, save, watch = (med(group, k) for k in ("skip_rate", "share_rate", "save_rate", "watch_ratio"))
        bits = [f"skip {skip:.0f}%" if skip is not None else None,
                f"shares {share:.1%}" if share is not None else None,
                f"saves {save:.1%}" if save is not None else None,
                f"watched {watch:.0%}" if watch is not None else None]
        return f"{style} ({len(group)}): " + (", ".join(b for b in bits if b) or "no rates yet")

    def order(item):
        skip, watch = med(item[1], "skip_rate"), med(item[1], "watch_ratio")
        return (skip is None, skip if skip is not None else -(watch or 0.0))

    lines = []
    for platform in VIDEO_PLATFORMS:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            if r["platform"] == platform and not r.get("unavailable") and r.get("cover_style"):
                groups.setdefault(r["cover_style"], []).append(r)
        if len(groups) < 2:
            continue
        parts = [describe(style, group) for style, group in sorted(groups.items(), key=order)]
        early = min(len(g) for g in groups.values()) < MIN_PER_STYLE
        note = f" <i>Early signal: fewer than {MIN_PER_STYLE} videos per style.</i>" if early else ""
        lines.append(f"• Cover styles · {PLATFORM_NAMES.get(platform, platform)} (median, best first): "
                     + "; ".join(parts) + "." + note)
    return lines


def opening_comparison(rows: list[dict[str, Any]]) -> list[str]:
    """Digest lines comparing how Reels opened, per video platform, by their first three seconds only: the skip
    rate where Instagram reports it (lowest first) and the share still watching at 3 s where Facebook's
    retention curve gives it. One line for the hook's form, one for the opening shot, each once two kinds are
    measured on that platform; an early signal until each has MIN_PER_STYLE videos there."""
    from statistics import median

    def med(group, key):
        vals = [r[key] for r in group if r.get(key) is not None]
        return median(vals) if vals else None

    lines = []
    for field, title in (("hook_form", "Hook forms"), ("opening_shot", "Opening shots")):
        for platform in VIDEO_PLATFORMS:
            groups: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                if r["platform"] == platform and not r.get("unavailable") and r.get(field):
                    if r.get("skip_rate") is not None or r.get("hold_3s") is not None:
                        groups.setdefault(r[field], []).append(r)
            if len(groups) < 2:
                continue

            def order(item):
                skip, hold = med(item[1], "skip_rate"), med(item[1], "hold_3s")
                return (skip is None, skip if skip is not None else -(hold or 0.0))

            parts = []
            for kind, group in sorted(groups.items(), key=order):
                skip, hold = med(group, "skip_rate"), med(group, "hold_3s")
                bits = [f"skip {skip:.0f}%" if skip is not None else None,
                        f"{hold:.0%} still watching at 3 s" if hold is not None else None]
                parts.append(f"{kind} ({len(group)}): " + ", ".join(b for b in bits if b))
            early = min(len(g) for g in groups.values()) < MIN_PER_STYLE
            note = f" <i>Early signal: fewer than {MIN_PER_STYLE} videos per kind.</i>" if early else ""
            lines.append(f"• {title} · {PLATFORM_NAMES.get(platform, platform)} (first 3 s, best first): "
                         + "; ".join(parts) + "." + note)
    return lines


def asks_comparison(rows: list[dict[str, Any]]) -> list[str]:
    """Digest lines comparing how Reels asked, per video platform: the share line's form by shares plus saves per
    account reached, the caption question's form by comments per account reached (each kind's median, best
    first), once two kinds are measured there; an early signal until each has MIN_PER_STYLE videos."""
    from statistics import median

    def rate(r, kind):
        reach = r.get("reach")
        if kind == "share":
            if r.get("share_rate") is None and r.get("save_rate") is None:
                return None
            return (r.get("share_rate") or 0.0) + (r.get("save_rate") or 0.0)
        return r["comments"] / reach if r.get("comments") is not None and reach else None

    lines = []
    for field, kind, title, unit in (("share_form", "share", "Share asks", "shared or saved"),
                                     ("question_form", "comment", "Caption questions", "commented")):
        for platform in VIDEO_PLATFORMS:
            groups: dict[str, list[float]] = {}
            for r in rows:
                if r["platform"] == platform and not r.get("unavailable") and r.get(field):
                    v = rate(r, kind)
                    if v is not None:
                        groups.setdefault(r[field], []).append(v)
            if len(groups) < 2:
                continue
            ranked = sorted(groups.items(), key=lambda kv: -median(kv[1]))
            parts = [f"{k} ({len(v)}): {median(v):.2%} {unit}" for k, v in ranked]
            early = min(len(v) for v in groups.values()) < MIN_PER_STYLE
            note = f" <i>Early signal: fewer than {MIN_PER_STYLE} videos per kind.</i>" if early else ""
            lines.append(f"• {title} · {PLATFORM_NAMES.get(platform, platform)} (per account reached, best first): "
                         + "; ".join(parts) + "." + note)
    return lines


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
    from .hook_learning import follow_rate
    for comparison in (end_card_comparison(rows), *opening_comparison(rows), follow_rate(rows),
                       *asks_comparison(rows), *cover_style_comparison(rows)):
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
                "after posting (Instagram, Facebook and YouTube, Reels, Shorts and carousels), and each Story "
                f"{STORY_SNAPSHOT_HOURS} hours after posting, before it expires.")
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


# ------------------------------------------------------------------ dashboard (read side)
# The analytics dashboard (operator, 2026-10-28: "a consolidated analytics for all the platforms and separate
# for each platform ... useful at a glance and easy to dive deeper"). These read audience_metrics only;
# the screens are drawn in src/control.py.
SNAPSHOT_ORDER = {STORY_SNAPSHOT: 0, "1d": 1, "3d": 2, "7d": 3, "28d": 4}
# (key, name, icon) of each video channel. Both YouTube videos are stored as "youtube"; the run's
# content_format tells the Short from the documentary.
CHANNELS = (("ig", "Instagram", "📸"), ("fb", "Facebook", "📘"), ("yt", "YouTube Shorts", "▶️"),
            ("ytlong", "YouTube long", "🎬"))
EXTRAS = ("stories", "Stories & carousels", "🗂")
MIN_FOR_TREND = 3  # videos at the same snapshot before the newest is compared with the platform's median


def channel_of(label: str, content_format: Optional[str]) -> str:
    """The dashboard channel a stored platform label belongs to: ig, fb, yt, ytlong, or stories (Stories
    frames and carousels)."""
    if "_" in label:
        return "stories"
    if label == "youtube":
        return "ytlong" if content_format == "deep_dive" else "yt"
    return {"instagram": "ig", "facebook": "fb"}.get(label, label)


def compact(value: Any, unit: str = "") -> str:
    """121, 1.2k, 13k, 1.4M; '—' for no number."""
    if not isinstance(value, (int, float)):
        return "—"
    v, a = float(value), abs(float(value))
    trim = lambda s: s.rstrip("0").rstrip(".") if "." in s else s  # noqa: E731
    if a < 100:
        s = trim(f"{v:.1f}")
    elif a < 1000:
        s = f"{v:.0f}"
    elif a < 10_000:
        s = trim(f"{v / 1000:.1f}") + "k"
    elif a < 1_000_000:
        s = f"{v / 1000:.0f}k"
    else:
        s = trim(f"{v / 1_000_000:.1f}") + "M"
    return s + unit


def measures(metrics: Dict[str, Any], duration_s: Optional[float]) -> Dict[str, Any]:
    """derived() plus the counts a dashboard shows: average time watched (s), shares, saves, follows."""
    def num(*keys):
        return next((float(metrics[k]) for k in keys if isinstance(metrics.get(k), (int, float))), None)
    avg_ms = num("ig_reels_avg_watch_time", "post_video_avg_time_watched")
    out = {**derived(metrics, duration_s), "avg_s": round(avg_ms / 1000, 1) if avg_ms else None,
           "shares": num("shares"), "saves": num("saved", "saves"), "follows": num("follows", "post_video_followers"),
           "unavailable": metrics.get("unavailable")}
    out["measured"] = not out["unavailable"] and any(out[k] is not None for k in ("views", "reach", "likes", "avg_s"))
    return out


def latest(snapshots: Dict[str, Dict[str, Any]], measured: bool = True) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(snapshot, measures) of the latest snapshot; with measured, the latest one that has numbers."""
    names = sorted((s for s, m in snapshots.items() if m.get("measured") or not measured),
                   key=lambda s: SNAPSHOT_ORDER.get(s, 9))
    return (names[-1], snapshots[names[-1]]) if names else None


def dashboard(db) -> List[Dict[str, Any]]:
    """Every measured publication, newest posting first: {"run_id", "key", "title", "format", "duration_s",
    "posted_at", "channels": {ig|fb|yt|ytlong: {snapshot: measures}}, "extras": {label: {snapshot: measures}}}."""
    ensure_schema(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM audience_metrics ORDER BY fetched_at").fetchall()
        posted = {r["run_id"]: r["t"] for r in conn.execute(
            "SELECT run_id, MIN(created_at) AS t FROM publications WHERE status = 'published' GROUP BY run_id")}
    videos: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        rid = r["run_id"]
        v = videos.get(rid)
        if v is None:
            run = db.get_run(rid) or {}
            v = videos[rid] = {
                "run_id": rid, "key": db.run_key(rid), "title": (run.get("script_payload") or {}).get("title") or rid,
                "format": run.get("content_format") or "reel", "duration_s": measured_duration(run),
                "posted_at": _when(posted.get(rid)) or _when(r["fetched_at"]), "channels": {}, "extras": {}}
        channel = channel_of(r["platform"], v["format"])
        m = {**measures(json.loads(r["metrics_json"] or "{}"), v["duration_s"]),
             "source": r["source"], "fetched_at": r["fetched_at"]}
        home = v["extras"].setdefault(r["platform"], {}) if channel == "stories" else v["channels"].setdefault(channel, {})
        home[r["snapshot"]] = m
    return sorted(videos.values(), key=lambda v: v["posted_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def trend(videos: List[Dict[str, Any]], channel: str) -> Optional[str]:
    """▲ / ▼ / ≈: the newest video's views on `channel` against the median of the channel's other videos
    at the same snapshot (a 1-day count is never set against 28-day ones); None under MIN_FOR_TREND videos."""
    from statistics import median
    mine = [v for v in videos if latest(v["channels"].get(channel, {}))]
    if len(mine) < MIN_FOR_TREND:
        return None
    snap, newest = latest(mine[0]["channels"][channel])
    others = [v["channels"][channel][snap]["views"] for v in mine[1:]
              if (v["channels"][channel].get(snap) or {}).get("measured") and v["channels"][channel][snap].get("views") is not None]
    if newest.get("views") is None or len(others) + 1 < MIN_FOR_TREND:
        return None
    mid = median(others)
    if not mid:
        return "▲" if newest["views"] > 0 else "≈"
    ratio = newest["views"] / mid
    return "▲" if ratio > 1.1 else "▼" if ratio < 0.9 else "≈"


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
    # The beat that lost the most viewers, on its own measure (not against the channel's median): a beat that
    # loses BEAT_DROP_FLAG of the viewers it started with is where the script or the edit lets them go.
    for r in rows:
        beat = r.get("worst_beat")
        if beat and beat["lost"] >= BEAT_DROP_FLAG and (r["run_id"], "audience.beat_drop") not in filed:
            run = db.get_run(r["run_id"]) or {}
            words = beat["text"] if len(beat["text"]) <= 90 else beat["text"][:87] + "..."
            improve.record(db, "audience.beat_drop",
                           f"{PLATFORM_NAMES[r['platform']]}: lost {beat['lost']:.0%} of viewers during "
                           f"\"{words}\" ({beat['start']:.1f}-{beat['end']:.1f} s)",
                           "audience", run_id=r["run_id"], topic_id=run.get("topic_id"))
            filed.add((r["run_id"], "audience.beat_drop"))
            n += 1
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
