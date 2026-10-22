"""
src/accounts.py
===============
Are the publishing accounts actually usable? A key being present is not enough: the Upload-Post
profile can have no Instagram connected, and a Meta token can be expired or malformed.

check() asks the services themselves (read-only), at most every CHECK_HOURS unless forced:
- Upload-Post: GET /uploadposts/users/{user} -> which social accounts the profile has connected
  (and whether one needs re-authentication); GET /uploadposts/facebook/pages -> Facebook Pages.
- Meta Graph: GET /{ig-user-id}?fields=username with META_ACCESS_TOKEN -> is the token valid.
The result is cached in the settings table; status, the dashboard and the nightly job read it.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests
from urllib.parse import quote

from . import config
from .config import is_configured

log = logging.getLogger(__name__)

CHECK_HOURS = 12
NEEDED = ("instagram", "facebook")
SETTING = "publishing_check"
SCHEMA_VERSION = 2


def _upload_post() -> Dict[str, Any]:
    if not (is_configured(config.UPLOAD_POST_API_KEY) and is_configured(config.UPLOAD_POST_USER)):
        return {"configured": False}
    base = config.UPLOAD_POST_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Apikey {config.UPLOAD_POST_API_KEY}"}
    out: Dict[str, Any] = {"configured": True}
    try:
        response = requests.get(f"{base}/uploadposts/users/{quote(config.UPLOAD_POST_USER, safe='')}", headers=headers, timeout=30)
        response.raise_for_status()
        body = response.json()
        profile = body.get("profile")
        if body.get("success") is False or not isinstance(profile, dict) or not isinstance(profile.get("social_accounts"), dict):
            raise ValueError("Unexpected profile response")
        accounts = profile["social_accounts"]
        out["connected"] = sorted(k for k, v in accounts.items() if isinstance(v, dict) and v)
        out["reauth"] = sorted(k for k, v in accounts.items() if isinstance(v, dict) and v.get("reauth_required"))
        page_response = requests.get(f"{base}/uploadposts/facebook/pages", params={"profile": config.UPLOAD_POST_USER},
                                    headers=headers, timeout=30)
        page_response.raise_for_status()
        pages = page_response.json()
        out["facebook_pages"] = len(pages.get("pages") or [])
        out["facebook_page_configured"] = bool(profile.get("facebook_page_id")) or is_configured(config.UPLOAD_POST_FACEBOOK_PAGE_ID)
    except (requests.RequestException, ValueError) as e:
        out["error"] = type(e).__name__
    return out


def _meta() -> Dict[str, Any]:
    if not (is_configured(config.META_ACCESS_TOKEN) and is_configured(config.META_IG_ACCOUNT_ID)):
        return {"configured": False}
    try:
        r = requests.get(f"https://graph.facebook.com/{config.META_GRAPH_VERSION}/{config.META_IG_ACCOUNT_ID}",
                         params={"fields": "username", "access_token": config.META_ACCESS_TOKEN}, timeout=30)
        body = r.json()
        if r.ok and body.get("username"):
            return {"configured": True, "valid": True}
        return {"configured": True, "valid": False, "error": ((body.get("error") or {}).get("message") or "")[:120]}
    except (requests.RequestException, ValueError) as e:
        return {"configured": True, "valid": None, "error": type(e).__name__}


def problems(result: Dict[str, Any]) -> List[str]:
    """Plain-language problems that would stop or degrade real publishing."""
    out = []
    up, meta = result.get("upload_post") or {}, result.get("meta") or {}
    if up.get("configured"):
        if up.get("error"):
            out.append(f"Upload-Post could not be checked ({up['error']}).")
        else:
            missing = [p for p in NEEDED if p not in up.get("connected", [])]
            if missing:
                out.append(f"Upload-Post profile has no {' or '.join(missing)} account connected: connect it in the Upload-Post dashboard.")
            if up.get("reauth"):
                out.append(f"Upload-Post needs you to re-authenticate: {', '.join(up['reauth'])}.")
            if "facebook" in up.get("connected", []) and not up.get("facebook_page_configured"):
                out.append("UPLOAD_POST_FACEBOOK_PAGE_ID is not set (Facebook posts and analytics need the Page ID).")
    if meta.get("configured") and meta.get("valid") is False:
        out.append("META_ACCESS_TOKEN is rejected by Meta: generate a new one (with instagram_manage_insights for audience data).")
    if not up.get("configured") and not meta.get("configured"):
        out.append("No publisher is configured; only dry runs are possible.")
    return out


def check(db, force: bool = False) -> Dict[str, Any]:
    raw = db.get_setting(SETTING)
    if raw and not force:
        try:
            cached = json.loads(raw)
            if cached.get("schema_version") == SCHEMA_VERSION and datetime.now(timezone.utc) - datetime.fromisoformat(cached["checked_at"]) < timedelta(hours=CHECK_HOURS):
                return cached
        except (ValueError, KeyError):
            pass
    result = {"schema_version": SCHEMA_VERSION, "checked_at": datetime.now(timezone.utc).isoformat(), "upload_post": _upload_post(), "meta": _meta()}
    result["problems"] = problems(result)
    db.set_setting(SETTING, json.dumps(result))
    return result


def cached(db) -> Dict[str, Any]:
    """The last check without calling anything (for fast screens); {} if never checked."""
    try:
        return json.loads(db.get_setting(SETTING) or "{}")
    except ValueError:
        return {}


def status_line(db) -> str:
    import html
    res = cached(db)
    if not res:
        return "Publishing accounts: not checked yet"
    up = res.get("upload_post") or {}
    when = res["checked_at"][:16].replace("T", " ")
    if not res.get("problems"):
        return f"Publishing accounts: ✅ {', '.join(up.get('connected', [])) or 'Meta direct'} ready (checked {when} UTC)"
    return "Publishing accounts: ⚠️ " + " ".join(html.escape(p) for p in res["problems"]) + f" (checked {when} UTC)"
