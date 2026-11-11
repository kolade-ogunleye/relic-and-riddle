"""
src/publisher.py
================
Reel publishing with safe failover.

  1. Upload-Post.com  (Facebook + Instagram)   docs.upload-post.com/api/upload-video/
  2. Instagram Graph API direct (Instagram only; resumable upload of the local file)

Every outcome is explicit:
  published  - the platform(s) confirmed the post
  simulated  - dry run; nothing was sent
  failed     - nothing was published (safe to retry)
  unknown    - the request may have been accepted (timeout / server error mid-publish,
               or partial success). Failover stops here, because trying the next
               publisher could post the same Reel twice. A human must check.
Only "published" counts toward a topic's publish history.
"""

import contextlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests

from . import config
from .config import is_configured
from .db import PipelineDatabase

log = logging.getLogger(__name__)


# One timeout for a request that sends a file: with a (connect, read) pair, urllib3 keeps the short connect value
# while the body is sent, so a write that stalls 15 s on a slow uplink aborted the upload ("The write operation timed
# out": the YouTube Short, production, 2026-10-26; the Telegram review videos had the same, telegram_bot._post).
UPLOAD_TIMEOUT = 900


def _assert_recovery_unlocked():
    if (config.BASE_DIR / ".restore-isolated").exists():
        raise PublishFailed("Publishing is disabled in this isolated recovery copy")


class NotConfigured(Exception):
    pass


class PublishFailed(Exception):
    """Definitely not published; safe to try the next publisher."""

    def __init__(self, message: str = "", status: Optional[int] = None, code: Optional[int] = None,
                 subcode: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code, self.subcode = code, subcode  # Graph's error code and error_subcode, when it gave them


class PublishUnknown(Exception):
    """May or may not have been published; do not fail over."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UploadPostPublisher:
    name = "upload-post"
    platforms = ("facebook", "instagram", "youtube")

    def __init__(self, api_key: str = None, user: str = None, base_url: str = None, facebook_page_id: str = None,
                 poll_interval: float = 10.0, poll_timeout: float = 900.0):
        self.api_key = api_key if api_key is not None else config.UPLOAD_POST_API_KEY
        self.user = user if user is not None else config.UPLOAD_POST_USER
        self.base_url = (base_url or config.UPLOAD_POST_BASE_URL).rstrip("/")
        self.facebook_page_id = facebook_page_id if facebook_page_id is not None else config.UPLOAD_POST_FACEBOOK_PAGE_ID
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def configured(self) -> bool:
        return is_configured(self.api_key) and is_configured(self.user)

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        h = {"Authorization": f"Apikey {self.api_key}"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def publish(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        _assert_recovery_unlocked()
        if not self.configured():
            raise NotConfigured("UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set")
        meta, media = envelope["metadata"], envelope["media"]
        if envelope["platforms"] == ["youtube"]:
            return self._publish_youtube(envelope)
        data: List[Tuple[str, str]] = [
            ("user", self.user),
            *[("platform[]", p) for p in envelope["platforms"]],
            # docs.upload-post.com/api/upload-video: Instagram's caption is instagram_title (falls back
            # to title); Facebook's Reel title is facebook_title (falls back to title; max 255 chars,
            # docs .../resources/character-limits) and its text is facebook_description.
            ("title", reel_fields(meta)["title"]),
            ("instagram_title", reel_fields(meta)["caption"]),
            ("facebook_title", reel_fields(meta)["title"]),
            ("facebook_description", reel_fields(meta)["caption"]),
            ("first_comment", reel_fields(meta)["first_comment"]),
            ("media_type", "REELS"),
            ("share_to_feed", "true"),
            ("facebook_media_type", "REELS"),
            # The AI label needs the Instagram and the Facebook flag: on for the synthetic narration (the setting) and
            # whenever the Reel shows AI media or media whose origin cannot be traced (ai_label.assess_video, QF-65a).
            ("is_ai_generated", "true" if meta["is_ai_generated"] else "false"),
            ("facebook_is_ai_generated", "true" if meta["is_ai_generated"] else "false"),
            ("async_upload", "true"),
            ("request_id", envelope.get("request_id") or envelope["run_id"]),  # a retry names its own key
        ]
        if self.facebook_page_id:
            data.append(("facebook_page_id", self.facebook_page_id))

        response = None
        for attempt in (1, 2):  # the Idempotency-Key makes a retry after a timeout safe
            try:
                with open(media["video_path"], "rb") as vf, open(media["thumbnail_path"], "rb") as tf:
                    response = requests.post(
                        f"{self.base_url}/upload",
                        headers=self._headers(envelope.get("request_id") or envelope["run_id"]),
                        data=data,
                        files={"video": (Path(media["video_path"]).name, vf, "video/mp4"),
                               "cover_image": (Path(media["thumbnail_path"]).name, tf, "image/jpeg")},
                        timeout=UPLOAD_TIMEOUT,
                    )
                break
            except requests.ConnectionError as e:
                if attempt == 2:
                    raise PublishFailed(f"Could not connect to Upload-Post: {e}") from e
            except requests.Timeout as e:
                if attempt == 2:
                    raise PublishUnknown(f"Upload-Post timed out after upload started: {e}") from e
            log.warning("Upload-Post request failed (attempt %d); retrying with the same idempotency key.", attempt)

        body = self._json(response)
        if response.status_code >= 500:
            raise PublishUnknown(f"Upload-Post server error {response.status_code}: {body}")
        if response.status_code >= 400 or not body.get("success"):
            raise PublishFailed(f"Upload-Post rejected the upload ({response.status_code}): {body.get('message') or body}")

        if "results" in body:  # synchronous response
            return self._summarize(body["results"], body)
        request_id = body.get("request_id") or envelope["run_id"]
        return self._poll(request_id)

    def _publish_youtube(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """The 16:9 documentary to YouTube (docs.upload-post.com/api/upload-video, YouTube fields): the
        YouTube title (100 characters), description, tags, Education category, the AI-content disclosure,
        not made for kids, and the 16:9 thumbnail as a file (2 MB maximum; needs a verified channel)."""
        meta, media = envelope["metadata"], envelope["media"]
        yt = youtube_fields(meta)
        data: List[Tuple[str, str]] = [
            ("user", self.user), ("platform[]", "youtube"),
            ("title", yt["title"]),
            ("youtube_title", yt["title"]),
            ("youtube_description", yt["description"]),
            *[("tags[]", tag) for tag in yt["tags"]],
            ("categoryId", "27"),  # Education
            ("privacyStatus", config.YOUTUBE_PRIVACY),
            ("selfDeclaredMadeForKids", "false"),
            ("containsSyntheticMedia", "true" if meta["is_ai_generated"] else "false"),
            ("defaultLanguage", "en"), ("defaultAudioLanguage", "en"),
            ("async_upload", "true"), ("request_id", envelope["run_id"]),
        ]
        files = {}
        handles = []
        try:
            video = open(media["video_path"], "rb")
            handles.append(video)
            files["video"] = (Path(media["video_path"]).name, video, "video/mp4")
            thumb = Path(media["thumbnail_path"])
            if thumb.is_file() and thumb.stat().st_size <= 2 * 1024 * 1024:
                handle = open(thumb, "rb")
                handles.append(handle)
                files["thumbnail"] = (thumb.name, handle, "image/jpeg")
            else:
                log.warning("YouTube thumbnail missing or over 2 MB (%s); YouTube will pick a frame.", thumb.name)
            response = requests.post(f"{self.base_url}/upload", headers=self._headers(envelope["run_id"]),
                                     data=data, files=files, timeout=1800)
        except requests.ConnectionError as e:
            raise PublishFailed(f"Could not connect to Upload-Post: {e}") from e
        except requests.Timeout as e:
            raise PublishUnknown(f"Upload-Post timed out after the YouTube upload started: {e}") from e
        finally:
            for handle in handles:
                handle.close()
        body = self._json(response)
        if response.status_code >= 500:
            raise PublishUnknown(f"Upload-Post server error {response.status_code}: {body}")
        if response.status_code >= 400 or not body.get("success"):
            raise PublishFailed(f"Upload-Post rejected the YouTube upload ({response.status_code}): {body.get('message') or body}")
        if "results" in body:
            return self._summarize(body["results"], body)
        return self._poll(body.get("request_id") or envelope["run_id"])

    def publish_short(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """The approved 9:16 Reel to YouTube. YouTube makes a vertical video of 3 minutes or less a Short
        by itself (no flag exists); a custom thumbnail is not sent, since Shorts take a frame. Its own
        idempotency key: reusing the Reel's would return the Reel's upload instead of making this one."""
        _assert_recovery_unlocked()
        if not self.configured():
            raise NotConfigured("UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set")
        meta, media = envelope["metadata"], envelope["media"]
        key = envelope["run_id"] + ":youtube-short"
        short = youtube_short_fields(meta)
        data: List[Tuple[str, str]] = [
            ("user", self.user), ("platform[]", "youtube"),
            ("title", short["title"]), ("youtube_title", short["title"]),
            ("youtube_description", short["description"]),
            *[("tags[]", t) for t in short["tags"]],
            ("categoryId", "27"),  # Education
            ("privacyStatus", config.YOUTUBE_SHORTS_PRIVACY),
            ("selfDeclaredMadeForKids", "false"),
            ("containsSyntheticMedia", "true" if meta["is_ai_generated"] else "false"),
            ("defaultLanguage", "en"), ("defaultAudioLanguage", "en"),
            ("async_upload", "true"), ("request_id", key),
        ]
        if meta.get("first_comment"):
            data.append(("youtube_first_comment", meta["first_comment"]))
        # Our own captions, word for word (YouTube's automatic ones misspell names): Upload-Post attaches the file
        # through the YouTube Captions API once the video is processed (docs.upload-post.com/api/upload-video,
        # youtube_subtitle_file; operator, 2026-10-27).
        srt = Path(media["video_path"]).with_suffix(".srt")
        if srt.is_file():
            data += [("youtube_subtitle_language", "en"), ("youtube_subtitle_name", "English")]
        try:
            with open(media["video_path"], "rb") as vf, (open(srt, "rb") if srt.is_file() else contextlib.nullcontext()) as sf:
                files = {"video": (Path(media["video_path"]).name, vf, "video/mp4")}
                if sf is not None:
                    files["youtube_subtitle_file"] = (srt.name, sf, "text/plain")
                response = requests.post(f"{self.base_url}/upload", headers=self._headers(key), data=data,
                                         files=files, timeout=UPLOAD_TIMEOUT)
        except requests.ConnectionError as e:
            raise PublishFailed(f"Could not connect to Upload-Post: {e}") from e
        except requests.Timeout as e:
            raise PublishUnknown(f"Upload-Post timed out after the Short's upload started: {e}") from e
        body = self._json(response)
        if response.status_code >= 500:
            raise PublishUnknown(f"Upload-Post server error {response.status_code}: {body}")
        if response.status_code >= 400 or not body.get("success"):
            raise PublishFailed(f"Upload-Post rejected the Short ({response.status_code}): {body.get('message') or body}")
        if "results" in body:
            return self._summarize(body["results"], body)
        return self._poll(body.get("request_id") or key)

    def set_youtube_thumbnail(self, video_id: str, image: Path) -> Dict[str, Any]:
        """A custom thumbnail for a published YouTube video or Short (Upload-Post's youtube/thumbnail endpoint:
        JPG/PNG at most 2 MB, 1080x1920 recommended for a Short, verified channel required). Best-effort:
        returns {"set": bool, "error": str|None}; never raises (operator, 2026-10-24: the first Short had none)."""
        image = Path(image)
        if not image.is_file():
            return {"set": False, "error": "thumbnail file missing"}
        if image.stat().st_size > 2 * 1024 * 1024:
            return {"set": False, "error": f"thumbnail over 2 MB ({image.stat().st_size // 1024} KB)"}
        try:
            with open(image, "rb") as handle:
                response = requests.post(f"{self.base_url}/uploadposts/youtube/thumbnail", headers=self._headers(),
                                         data=[("user", self.user), ("video_id", video_id)],
                                         files={"thumbnail": (image.name, handle, "image/jpeg")}, timeout=300)
        except requests.RequestException as e:
            return {"set": False, "error": f"could not reach Upload-Post: {e}"}
        body = self._json(response)
        if response.status_code < 400 and body.get("success"):
            return {"set": True, "error": None, "thumbnails": body.get("thumbnails")}
        return {"set": False, "error": f"HTTP {response.status_code}: {body.get('error') or body.get('message') or body}"[:300]}

    def publish_photos(self, kind: str, photos: List[str], caption: str, platforms: List[str], key: str,
                       alt_texts: Optional[List[str]] = None, first_comment: str = "",
                       ai_generated: bool = False) -> Dict[str, Any]:
        _assert_recovery_unlocked()
        """A carousel (several photos, one post) or a single Story frame (kind="story", one photo).
        Docs: docs.upload-post.com/api/upload-photo/ (media_type / facebook_media_type = STORIES).
        A carousel's `alt_texts` (one per photo, in order) go as instagram_alt_text / facebook_alt_text, each a
        JSON list (the documented per-photo form; at most 1,000 characters an item); Stories take none. Its
        `first_comment` goes as each platform's own <platform>_first_comment (a failed first comment never
        fails the post, Upload-Post reports it under `warnings`). `ai_generated`: Instagram's AI label
        (is_ai_generated, documented for photos and carousels, the whole post; QF-65a). Facebook photo posts take no
        AI label (Upload-Post's AI-labelling guide: a no-op there), so MetaPublisher.publish_photos sends them no
        AI media."""
        if not self.configured():
            raise NotConfigured("UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set")
        data: List[Tuple[str, str]] = [("user", self.user), *[("platform[]", p) for p in platforms],
                                       ("async_upload", "true"), ("request_id", key)]
        if ai_generated:
            data.append(("is_ai_generated", "true"))
        if kind == "story":
            data += [("media_type", "STORIES"), ("facebook_media_type", "STORIES")]
        else:
            # On Facebook a carousel is a multi-photo post (Pages have no organic swipe carousel). It is not sent
            # facebook_no_story: Upload-Post documents it only as "do not publish a story from this post", and the
            # Graph field it mirrors (no_story) suppresses the post's feed and timeline story, which would keep the
            # carousel out of followers' feeds (operator review, 2026-10-25).
            data += [("title", caption), ("description", caption)]
            alts = [str(a or "").strip()[:1000] for a in (alt_texts or [])]
            if len(alts) == len(photos) and all(alts):
                data += [(f"{p}_alt_text", json.dumps(alts, ensure_ascii=False)) for p in platforms
                         if p in ("instagram", "facebook")]
            if first_comment.strip():
                data += [(f"{p}_first_comment", first_comment.strip()) for p in platforms]
        if self.facebook_page_id:
            data.append(("facebook_page_id", self.facebook_page_id))

        response = None
        for attempt in (1, 2):  # same Idempotency-Key on retry, as for Reels
            handles = [open(p, "rb") for p in photos]
            try:
                response = requests.post(
                    f"{self.base_url}/upload_photos", headers=self._headers(key), data=data,
                    files=[("photos[]", (Path(p).name, h, "image/jpeg")) for p, h in zip(photos, handles)],
                    timeout=UPLOAD_TIMEOUT)
                break
            except requests.ConnectionError as e:
                if attempt == 2:
                    raise PublishFailed(f"Could not connect to Upload-Post: {e}") from e
            except requests.Timeout as e:
                if attempt == 2:
                    raise PublishUnknown(f"Upload-Post timed out after upload started: {e}") from e
            finally:
                for h in handles:
                    h.close()
            log.warning("Upload-Post photo request failed (attempt %d); retrying with the same idempotency key.", attempt)

        body = self._json(response)
        if response.status_code >= 500:
            raise PublishUnknown(f"Upload-Post server error {response.status_code}: {body}")
        if response.status_code >= 400 or not body.get("success"):
            raise PublishFailed(f"Upload-Post rejected the {kind} ({response.status_code}): {body.get('message') or body}")
        if "results" in body:
            return self._summarize(body["results"], body)
        return self._poll(body.get("request_id") or key)

    @staticmethod
    def _json(resp: requests.Response) -> Dict[str, Any]:
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text[:500]}

    def _poll(self, request_id: str) -> Dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        last: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            try:
                resp = requests.get(f"{self.base_url}/uploadposts/status", headers=self._headers(),
                                    params={"request_id": request_id}, timeout=30)
                last = self._json(resp)
            except requests.RequestException as e:
                log.warning("Upload-Post status poll failed: %s", e)
                continue
            status = last.get("status")
            log.info("Upload-Post status: %s (%s/%s)", status, last.get("completed"), last.get("total"))
            if status in ("completed", "failed"):
                return self._summarize(last.get("results", []), last)
        raise PublishUnknown(f"Upload-Post did not finish within {self.poll_timeout:.0f}s (last: {last.get('status')})")

    @staticmethod
    def _summarize(results: Any, raw: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(results, dict):  # sync form: {"instagram": {...}, ...}
            results = [{"platform": k, **v} for k, v in results.items()]
        live = [r for r in results if r.get("success") and not r.get("skipped")]
        skipped = [r for r in results if r.get("skipped")]
        failed = [r for r in results if not r.get("success") and not r.get("skipped")]
        if live and not failed:
            return {"status": "published", "post_urls": {r["platform"]: r.get("post_url") or r.get("url") for r in live},
                    "skipped": [r["platform"] for r in skipped], "raw": raw}
        if live and failed:
            # The failed platform's own reason travels with the outcome (the first Stories, 2026-10-24: only
            # "failed=['instagram']" was kept and the cause was lost), and the record lets a retry target it.
            reasons = {r["platform"]: str(r.get("error") or r.get("message") or r.get("detail") or "no reason given")[:300] for r in failed}
            why = "; ".join(f"{p}: {reason}" for p, reason in reasons.items())
            error = PublishUnknown(f"Partial publish: ok={[r['platform'] for r in live]} failed={list(reasons)} ({why})")
            error.failed = reasons  # type: ignore[attr-defined]
            error.ok = {r["platform"]: r.get("post_url") or r.get("url") for r in live}  # type: ignore[attr-defined]
            raise error
        raise PublishFailed(f"Upload-Post reported no successful platform: {raw}")


class InstagramGraphPublisher:
    """Instagram Reels via Content Publishing API resumable upload (Facebook Login for Business token)."""

    name = "instagram-graph"
    platforms = ("instagram",)

    def __init__(self, token: str = None, ig_user_id: str = None, version: str = None,
                 poll_interval: float = 15.0, poll_timeout: float = 600.0):
        self.token = token if token is not None else config.META_ACCESS_TOKEN
        self.ig_user_id = ig_user_id if ig_user_id is not None else config.META_IG_ACCOUNT_ID
        self.version = version or config.META_GRAPH_VERSION
        self.graph = f"https://graph.facebook.com/{self.version}"
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def configured(self) -> bool:
        return is_configured(self.token) and is_configured(self.ig_user_id)

    def _call(self, method: str, url: str, timeout: float = 60, **kwargs) -> Dict[str, Any]:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if not resp.ok or "error" in body:
            raise PublishFailed(f"Graph {method} {url.split('?')[0].replace(self.token, '***')} -> {resp.status_code}: {body.get('error', body)}",
                                status=resp.status_code)
        return body

    def publish(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured():
            raise NotConfigured("META_ACCESS_TOKEN / META_IG_ACCOUNT_ID not set")
        _assert_recovery_unlocked()
        if "instagram" not in envelope["platforms"]:
            raise NotConfigured("Instagram not among requested platforms")
        meta, media = envelope["metadata"], envelope["media"]
        video_path = Path(media["video_path"])

        # Steps 1-3 can fail without anything being posted.
        try:
            container = self._call("POST", f"{self.graph}/{self.ig_user_id}/media", data={
                "media_type": "REELS", "upload_type": "resumable", "caption": meta["caption"],
                "share_to_feed": "true", "access_token": self.token,
                # The AI label (Content Publishing API, is_ai_generated on the container; QF-65a)
                "is_ai_generated": "true" if meta.get("is_ai_generated", True) else "false",
            })
            container_id = container["id"]
            upload_uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.version}/{container_id}"
            with open(video_path, "rb") as vf:
                self._call("POST", upload_uri, timeout=600, data=vf, headers={
                    "Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(os.path.getsize(video_path)),
                })
            self._wait_until_ready(container_id)
        except requests.RequestException as e:
            raise PublishFailed(f"Instagram container/upload failed: {e}") from e

        # Step 4 is the point of no return.
        try:
            published = self._call("POST", f"{self.graph}/{self.ig_user_id}/media_publish",
                                   data={"creation_id": container_id, "access_token": self.token})
        except (requests.Timeout, requests.ConnectionError) as e:
            raise PublishUnknown(f"media_publish outcome unknown: {e}") from e
        except PublishFailed as e:
            if e.status is not None and e.status >= 500:  # a server error here may still have posted
                raise PublishUnknown(f"media_publish outcome unknown (server error): {e}") from e
            raise
        media_id = published["id"]

        comment_status = "skipped"
        if meta.get("first_comment"):
            try:
                self._call("POST", f"{self.graph}/{media_id}/comments",
                           data={"message": meta["first_comment"], "access_token": self.token})
                comment_status = "posted"
            except Exception as e:
                comment_status = f"failed: {e}"
                log.warning("Reel published but first comment failed: %s", e)

        return {"status": "published", "post_ids": {"instagram": media_id}, "first_comment": comment_status,
                "note": "Facebook not covered by this publisher; cover image uses Instagram's default frame."}

    def _wait_until_ready(self, container_id: str):
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            status = self._call("GET", f"{self.graph}/{container_id}",
                                params={"fields": "status_code,status", "access_token": self.token})
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishFailed(f"Instagram processing {code}: {status.get('status')}")
            time.sleep(self.poll_interval)
        raise PublishFailed("Instagram container not ready in time; it expires unpublished after 24h.")


class FacebookPageReelsPublisher:
    """Facebook Page Reels via the Graph API (developers.facebook.com/docs/video-api/guides/reels-publishing).
    Needs a Page access token with pages_show_list, pages_read_engagement and pages_manage_posts."""

    name = "facebook-page"
    platforms = ("facebook",)

    def __init__(self, token: str = None, page_id: str = None, version: str = None,
                 poll_interval: float = 10.0, poll_timeout: float = 600.0):
        self.token = token if token is not None else (config.META_PAGE_ACCESS_TOKEN or config.META_ACCESS_TOKEN)
        self.page_id = page_id if page_id is not None else config.META_PAGE_ID
        self.version = version or config.META_GRAPH_VERSION
        self.graph = f"https://graph.facebook.com/{self.version}"
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def configured(self) -> bool:
        return is_configured(self.token) and is_configured(self.page_id)

    def _call(self, method: str, url: str, timeout: float = 60, **kwargs) -> Dict[str, Any]:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if not resp.ok or "error" in body:
            err = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise PublishFailed(f"Graph {method} {url.split('?')[0]} -> {resp.status_code}: {body.get('error', body)}",
                                status=resp.status_code, code=err.get("code"), subcode=err.get("error_subcode"))
        return body

    CAPTION_LOCALE = "en_US"

    def upload_captions(self, video_id: str, srt_path: Path, locale: str = CAPTION_LOCALE) -> Dict[str, Any]:
        """Closed captions for a posted Facebook video: POST /{video-id}/captions with an SRT named
        name.<locale>.srt (Graph reference: video/captions). Upload-Post sends caption files to YouTube and
        LinkedIn only, so a Reel on Facebook had none (operator, 2026-10-25). Meta documents the endpoint for
        videos; a Reel is a Page video, so this is tried on one. A locale already on the video is replaced."""
        if not self.configured():
            raise NotConfigured("META_PAGE_ID / page access token not set")
        srt_path = Path(srt_path)
        if not srt_path.is_file():
            raise PublishFailed(f"No caption file at {srt_path.name}")
        url = f"{self.graph}/{video_id}/captions"

        def send() -> Dict[str, Any]:
            with open(srt_path, "rb") as fh:
                return self._call("POST", url, timeout=120, data={"access_token": self.token, "default_locale": locale},
                                  files={"captions_file": (f"captions.{locale}.srt", fh, "text/plain")})  # Graph refuses application/x-subrip (400 #100, 2026-10-27)
        try:
            return {"status": "published", "video_id": str(video_id), "locale": locale, "result": send()}
        except PublishFailed as e:
            if 482 not in (e.code, e.subcode):  # anything but "this locale is already on the video"
                raise
        self._call("POST", url, timeout=60, data={"access_token": self.token, "locales_to_delete": json.dumps([locale])})
        return {"status": "published", "video_id": str(video_id), "locale": locale, "replaced": True, "result": send()}

    def set_cover(self, video_id: str, image_path: Path) -> Dict[str, Any]:
        """The cover of a posted Facebook video: POST /{video-id}/thumbnails with the image as `source` and
        is_preferred=true (Graph reference: video/thumbnails; a video on a Page, at most 10 MB, the video's aspect
        ratio). Meta documents it for Page videos; a Reel is one, so it is tried on one (operator, 2026-10-26)."""
        if not self.configured():
            raise NotConfigured("META_PAGE_ID / page access token not set")
        image_path = Path(image_path)
        if not image_path.is_file():
            raise PublishFailed(f"No cover image at {image_path.name}")
        if image_path.stat().st_size > 10 * 1024 * 1024:
            raise PublishFailed(f"The cover is {image_path.stat().st_size // 1048576} MB; Facebook takes at most 10 MB")
        with open(image_path, "rb") as fh:
            result = self._call("POST", f"{self.graph}/{video_id}/thumbnails", timeout=120,
                                data={"access_token": self.token, "is_preferred": "true"},
                                files={"source": (image_path.name, fh, "image/jpeg")})
        return {"status": "published", "video_id": str(video_id), "result": result}

    def publish(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured():
            raise NotConfigured("META_PAGE_ID / page access token not set")
        _assert_recovery_unlocked()
        meta, video_path = envelope["metadata"], Path(envelope["media"]["video_path"])
        reels = f"{self.graph}/{self.page_id}/video_reels"

        # Start, upload and processing can fail without anything being posted.
        try:
            session = self._call("POST", reels, data={"upload_phase": "start", "access_token": self.token})
            video_id = session["video_id"]
            upload_url = session.get("upload_url") or f"https://rupload.facebook.com/video-upload/{self.version}/{video_id}"
            with open(video_path, "rb") as vf:
                self._call("POST", upload_url, timeout=600, data=vf, headers={
                    "Authorization": f"OAuth {self.token}", "offset": "0",
                    "file_size": str(os.path.getsize(video_path)),
                })
            self._wait_until_ready(video_id)
        except requests.RequestException as e:
            raise PublishFailed(f"Facebook Reel upload failed: {e}") from e

        # Finishing with PUBLISHED is the point of no return.
        try:
            self._call("POST", reels, data={
                "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
                "description": meta["caption"], "access_token": self.token,
                # The AI label (Reels publishing guide, is_ai_generated on the finish call; QF-65a)
                "is_ai_generated": "true" if meta.get("is_ai_generated", True) else "false",
            })
        except (requests.Timeout, requests.ConnectionError) as e:
            raise PublishUnknown(f"Facebook Reel finish outcome unknown: {e}") from e
        except PublishFailed as e:
            if e.status is not None and e.status >= 500:  # a server error here may still have posted
                raise PublishUnknown(f"Facebook Reel finish outcome unknown (server error): {e}") from e
            raise
        return {"status": "published", "post_ids": {"facebook": video_id},
                "note": "First comment is not posted by this publisher."}

    def _wait_until_ready(self, video_id: str):
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            status = self._call("GET", f"{self.graph}/{video_id}",
                                params={"fields": "status", "access_token": self.token}).get("status", {})
            processed = (status.get("uploading_phase", {}).get("status") == "complete"
                         and status.get("processing_phase", {}).get("status") == "complete")
            if status.get("video_status") == "ready" or processed:
                return
            if status.get("video_status") == "error" or status.get("processing_phase", {}).get("status") == "error":
                raise PublishFailed(f"Facebook processing failed: {status}")
            time.sleep(self.poll_interval)
        raise PublishFailed("Facebook video not ready in time; nothing was published.")


def reel_fields(meta: dict[str, Any]) -> dict[str, str]:
    """The Reel's text as it is sent (Facebook's title at most 255 characters; the caption is Instagram's and
    Facebook's text). The review card shows these strings (publishable.card_copy)."""
    return {"title": meta["title"][:255], "caption": meta["caption"], "first_comment": meta.get("first_comment", "")}


def youtube_fields(meta: dict[str, Any]) -> dict[str, Any]:
    """The documentary's YouTube title (100 characters), description (5,000) and tags (30)."""
    return {"title": (meta.get("youtube_title") or meta["title"])[:100],
            "description": (meta.get("description") or meta["caption"])[:5000],
            "tags": list((meta.get("tags") or [])[:30])}


def youtube_short_fields(meta: dict[str, Any]) -> dict[str, Any]:
    """The Short's title, description (the caption, with the hashtags when it does not carry them) and tags."""
    tags = " ".join(meta.get("hashtags") or [])
    return {"title": meta["title"][:100],
            "description": (meta["caption"] + ("\n\n" + tags if tags and tags not in meta["caption"] else ""))[:5000],
            "tags": [t.lstrip("#") for t in (meta.get("hashtags") or [])[:30]]}


def _caption_for(kind: str, caption: str, platforms: List[str]) -> str:
    """The caption a carousel request carries (operator, 2026-10-28: a posted Facebook carousel's pointing hand
    pointed right while its caption sat above the photos). No request carries a direction cue (neutral_cues:
    the script stage removes them, and an operator's edit is held to the same rule). Facebook's own request,
    a multi-photo post shown as a grid, also says nothing about swiping (facebook_photo_caption)."""
    if kind == "story" or not caption:
        return caption
    from .credits import is_attribution_line
    from .script_engine import facebook_photo_caption, neutral_cues
    clean = facebook_photo_caption if platforms == ["facebook"] else neutral_cues
    # Credit lines go as they are: a photo's own title is quoted in its credit, whatever words it has.
    lines = caption.splitlines()
    credits = [ln for ln in lines if is_attribution_line(ln)]
    if not credits:
        return clean(caption)
    body = "\n".join(ln for ln in lines if not is_attribution_line(ln)).rstrip()
    if clean(body) == body:
        return caption
    return (clean(body).rstrip() + "\n\n" + "\n".join(credits)).strip()


ILLUSTRATION_DISCLOSURE = "Contains AI-generated illustrations where no photograph exists."


def used_illustrations(script_payload: dict[str, Any]) -> bool:
    """Whether the run put a generated illustration on screen (the picture check's record, payload["picture_fit"])."""
    fit = script_payload.get("picture_fit") or {}
    return bool(isinstance(fit, dict) and fit.get("generated"))


def with_disclosure(text: str, script_payload: dict[str, Any]) -> str:
    """`text` with ILLUSTRATION_DISCLOSURE appended when the run used an illustration (once; unchanged otherwise)."""
    text = text or ""
    if not used_illustrations(script_payload) or ILLUSTRATION_DISCLOSURE in text:
        return text
    return (text.rstrip() + "\n\n" + ILLUSTRATION_DISCLOSURE) if text.strip() else ILLUSTRATION_DISCLOSURE


def attempted_platforms(publishers: list | None = None, documentary: bool = False) -> list[str]:
    """What a live post of a run attempts, as MetaPublisher would: "facebook" and "instagram" where a configured
    publisher covers them, "youtube-short" when YOUTUBE_SHORTS is on and Upload-Post (the only Shorts uploader) is
    configured; a documentary goes to "youtube" alone. A platform no configured publisher covers is not attempted."""
    pubs = publishers if publishers is not None else [UploadPostPublisher(), InstagramGraphPublisher(),
                                                      FacebookPageReelsPublisher()]
    ready = [p for p in pubs if not hasattr(p, "configured") or p.configured()]
    covered = {x for p in ready for x in getattr(p, "platforms", ())}
    if documentary:
        return ["youtube"] if "youtube" in covered else []
    out = [x for x in ("facebook", "instagram") if x in covered]
    if out and config.YOUTUBE_SHORTS and any(hasattr(p, "publish_short") for p in ready):
        out.append("youtube-short")
    return out


class MetaPublisher:
    def __init__(self, db: Optional[PipelineDatabase] = None, publishers: Optional[list] = None):
        self.db = db or PipelineDatabase()
        self.publishers = publishers if publishers is not None else [
            UploadPostPublisher(), InstagramGraphPublisher(), FacebookPageReelsPublisher()]

    def build_envelope(self, run_id: str, video_path: Path, thumbnail_916_path: Path, thumbnail_1x1_path: Path,
                       script_payload: Dict[str, Any], platforms: Optional[List[str]] = None) -> Dict[str, Any]:
        """The Reel's (or the documentary's) request. Its text comes from the allowlisted fields only
        (publishable.ENVELOPE_FIELDS) and must be what the run's review card showed word for word: a field
        outside the allowlist, or a string the card did not show, raises ValueError (QF-43)."""
        from . import ai_label, publishable
        # The AI label from what the post shows (QF-65a): the master's scenes, the illustrated beats, the cover; and
        # on whenever the review card said it would be (the card's promise is kept even if a record changed since).
        label = ai_label.assess_video(video_path, script_payload, cover=thumbnail_916_path)
        record = publishable.load_record(self.db, run_id)
        shown = (record or {}).get("ai_label") if isinstance(record, dict) else None
        if isinstance(shown, dict) and shown.get("on") and not label["on"]:
            label = dict(label, on=True, reasons=list(label["reasons"]) + ["the review card showed the AI label on"])
        metadata = publishable.envelope_metadata(script_payload,
                                                 publishable.topic_for(self.db, script_payload["topic_id"]),
                                                 ai_label=label)
        publishable.check_envelope(metadata, record, youtube=(platforms or []) == ["youtube"])
        return {
            "run_id": run_id,
            "topic_id": script_payload["topic_id"],
            "platforms": platforms or ["facebook", "instagram"],
            "media": {
                "video_path": str(video_path.resolve()),
                "thumbnail_path": str(thumbnail_916_path.resolve()),
                "thumbnail_1x1_path": str(thumbnail_1x1_path.resolve()),
                "video_size_mb": round(os.path.getsize(video_path) / (1024 * 1024), 2),
            },
            "metadata": metadata,
            "ai_label": label,
            "created_at": _now(),
        }

    def publish_reel(self, envelope: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        if not dry_run:
            _assert_recovery_unlocked()
        tid, run_id = envelope["topic_id"], envelope["run_id"]

        if dry_run:
            res = {"status": "simulated", "channel": "dry-run", "at": _now()}
            self.db.record_publication(tid, run_id, "dry-run", "simulated", {"envelope": envelope})
            log.info("Dry run: nothing was published.")
            return res

        # Zero-Trust Commercial Voice License Gate
        if getattr(config, "VOICE_REQUIRE_LICENSED", True):
            from .config import OUTPUT_DIR
            voice_path = OUTPUT_DIR / run_id / "voice.json"
            voice_meta, unreadable = {}, ""
            if not voice_path.is_file():
                unreadable = "its voice record (voice.json) is missing"
            else:
                try:
                    voice_meta = json.loads(voice_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:  # still blocked (fails closed), but for the true reason
                    unreadable = f"its voice record (voice.json) could not be read ({e})"
            is_licensed = voice_meta.get("licensed", False)
            provider = voice_meta.get("provider", "unknown")
            if not is_licensed:
                err_msg = (
                    f"Commercial Voice License Required: the licence of this Reel's voice cannot be confirmed: "
                    f"{unreadable}. Re-voice or resend the run." if unreadable else
                    f"Commercial Voice License Required: This Reel was voiced using {provider} (unlicensed). "
                    "Meta Content Monetization Program (CMP) policies prohibit commercial publishing without "
                    "a commercial license. Please configure a valid Azure Speech key or ElevenLabs paid plan, "
                    "or execute a simulated publish."
                )
                log.warning("Publishing BLOCKED for %s: %s", run_id, err_msg)
                try:
                    self.db.record_audit_event(
                        user_id="system",
                        action="publish_blocked_unlicensed_voice",
                        status="blocked",
                        payload=json.dumps({"run_id": run_id, "provider": provider, "reason": err_msg}),
                        username="publisher_gate",
                    )
                except Exception as e:  # auditing must not change the publish outcome, but say so
                    log.warning("Could not record the blocked publish in the audit log: %s", e)
                return {"status": "blocked_unlicensed", "error": err_msg, "provider": provider}

        # Each requested platform is posted at most once: a publisher only handles the
        # platforms still remaining that it supports (Upload-Post: both; direct APIs: one each).
        remaining = set(envelope["platforms"])
        errors, done = [], []
        failures: dict[str, str] = {}   # each platform's latest failure, for the receipt's own line (UI-02)
        for pub in self.publishers:
            covers = remaining & set(getattr(pub, "platforms", envelope["platforms"]))
            if not covers:
                continue
            try:
                log.info("Publishing %s via %s...", "+".join(sorted(covers)), pub.name)
                res = pub.publish(dict(envelope, platforms=sorted(covers)))
                res["channel"] = pub.name
                self.db.record_publication(tid, run_id, pub.name, "published", res)
                log.info("Published via %s.", pub.name)
                remaining -= covers
                done.append(res)
            except NotConfigured as e:
                log.info("%s skipped: %s", pub.name, e)
            except PublishFailed as e:
                log.warning("%s failed (nothing published): %s", pub.name, e)
                errors.append(f"{pub.name}: {e}")
                failures.update({p: str(e) for p in covers})
                self.db.record_publication(tid, run_id, pub.name, "failed", {"error": str(e)})
            except PublishUnknown as e:
                log.error("%s outcome UNKNOWN; not failing over to avoid a duplicate post: %s", pub.name, e)
                failed, ok = getattr(e, "failed", {}) or {}, getattr(e, "ok", {}) or {}  # a partial names its sides
                self.db.record_publication(tid, run_id, pub.name, "unknown", {"error": str(e), "failed": failed, "ok": ok,
                                                                               "platforms": sorted(covers)})
                return {"status": "unknown", "channel": pub.name, "error": str(e), "published": done, "failed": failed, "ok": ok}
            if not remaining:
                break

        if done:
            merged = {"status": "published", "channel": "+".join(r["channel"] for r in done),
                      "post_urls": {}, "post_ids": {}, "results": done}
            for r in done:
                merged["post_urls"].update(r.get("post_urls") or {})
                merged["post_ids"].update(r.get("post_ids") or {})
            if remaining:
                merged["missing_platforms"] = sorted(remaining)
                merged["error"] = "; ".join(errors) or "no publisher configured for " + ", ".join(sorted(remaining))
                merged["platform_errors"] = {p: failures.get(p) or "no account is set up for it" for p in sorted(remaining)}
            return merged
        msg = "; ".join(errors) if errors else "no publisher is configured"
        return {"status": "failed", "error": msg}

    def publish_short(self, envelope: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        """The Reel as a YouTube Short, after Facebook and Instagram. Its outcome is its own: recorded as
        channel "youtube-shorts" and never changing the Reel's status. Only Upload-Post can do it."""
        tid, run_id = envelope["topic_id"], envelope["run_id"]
        if dry_run:
            self.db.record_publication(tid, run_id, "youtube-shorts", "simulated", {"privacy": config.YOUTUBE_SHORTS_PRIVACY})
            log.info("Dry run: the YouTube Short was not uploaded.")
            return {"status": "simulated"}
        uploader = next((p for p in self.publishers if hasattr(p, "publish_short")), None)
        try:
            if uploader is None:
                raise NotConfigured("no publisher can upload YouTube Shorts")
            res = uploader.publish_short(envelope)
        except NotConfigured as e:
            log.info("YouTube Short skipped: %s", e)
            return {"status": "skipped", "error": str(e)}
        except PublishFailed as e:
            self.db.record_publication(tid, run_id, "youtube-shorts", "failed", {"error": str(e)})
            return {"status": "failed", "error": str(e)}
        except PublishUnknown as e:
            self.db.record_publication(tid, run_id, "youtube-shorts", "unknown", {"error": str(e)})
            return {"status": "unknown", "error": str(e)}
        # No thumbnail request follows (QF-83): the Shorts feed shows a frame of the video, which only the YouTube
        # app can change; the standard thumbnail stays a manual button (main.set_youtube_thumbnail).
        self.db.record_publication(tid, run_id, "youtube-shorts", "published", res)
        return {"status": "published", "post_urls": res.get("post_urls") or {}}

    def publish_photos(self, run_id: str, topic_id: str, kind: str, photos: List[str], caption: str = "",
                       platforms: Optional[List[str]] = None, dry_run: bool = False, retry_tag: str = "",
                       first_comment: str = "") -> Dict[str, Any]:
        """Stories (one post per frame, in order) or a carousel, via Upload-Post only (the direct
        Graph publishers here handle Reels only). Same outcome rules as Reels: an unknown outcome
        stops there so nothing is posted twice. `retry_tag` names a deliberate re-send of a failed
        platform (operator, 2026-10-24) so its idempotency keys differ from the first attempt's.
        A carousel's slides carry alt text built from what each shows (multi_format.slide_alt_text);
        `first_comment` (the credits, when they do not fit the caption) is posted under it on each platform.
        Stories post the sequence's frames only (multi_format.story_posting_frames, QF-80): a retired poll frame
        booked by an older run is not sent, and each request keeps its frame's number in the sequence."""
        platforms = platforms or ["facebook", "instagram"]
        parts: list[int] = []
        if kind == "story":
            from .multi_format import story_posting_frames
            numbered = story_posting_frames(photos)
            parts, photos = [n for n, _ in numbered], [p for _, p in numbered]
            if not photos:
                return {"status": "failed", "error": "no Story frame to post (the poll frame is no longer posted)",
                        "posted_parts": 0}
        if not dry_run:
            _assert_recovery_unlocked()
        channel = f"upload-post:{kind}"
        tag = f"-{retry_tag}" if retry_tag else ""
        # (photos, platforms, key) per request. Stories: one frame per request, in order. A carousel goes as
        # one post per platform group; Facebook gets its own request, from its copy when the run made one
        # (carousel_fb/: no "swipe" in its text), with at most FACEBOOK_PHOTOS (5): its collage put "+3" over the
        # fourth of seven, hiding the closing slide (multi_format.facebook_pick; audit 2026-10-28; 4 until 2026-11-03).
        # Upload-Post puts a Facebook multi-photo post's caption on its first photo, which is the cover.
        if kind == "story":
            batches = [([p], platforms, f"{run_id}-{kind}-{n}{tag}") for n, p in zip(parts, photos)]
        else:
            from .multi_format import facebook_pick
            slides = photos[:10]  # Instagram carousels: max 10
            fb_copy = [Path(p).parent.parent / "carousel_fb" / Path(p).name for p in slides]
            fb_slides = facebook_pick([str(f) for f in fb_copy] if all(f.is_file() for f in fb_copy) else slides)
            others = [x for x in platforms if x != "facebook"]
            if "facebook" in platforms and others:
                batches = [(slides, others, f"{run_id}-{kind}-1{tag}"),
                           (fb_slides, ["facebook"], f"{run_id}-{kind}-facebook-1{tag}")]
            elif "facebook" in platforms:
                batches = [(fb_slides, ["facebook"], f"{run_id}-{kind}-1{tag}")]
            else:
                batches = [(slides, platforms, f"{run_id}-{kind}-1{tag}")]
        parts = parts or list(range(1, len(batches) + 1))  # a Story request's part is its frame's number
        # The AI label (QF-65a): a request whose stills show AI media, or media whose origin cannot be traced
        # (ai_label.assess_photos, fails closed), carries Instagram's flag. Facebook has none for photo posts, so a
        # request showing AI media is not sent there (held_platforms says why); one whose stills only lack a record
        # (a run made before still records) still is, as before.
        from . import ai_label
        held: dict[str, str] = {}
        flagged: dict[str, bool] = {}
        kept = []
        for i, (batch, batch_platforms, key) in zip(parts, batches):
            label = ai_label.assess_photos(batch)
            flagged[key] = bool(label["on"])
            if label["ai_media"] and "facebook" in batch_platforms:
                held["facebook"] = ("not posted to Facebook, which has no AI label for photo posts: "
                                    + "; ".join(label["reasons"][:3]))
                batch_platforms = [x for x in batch_platforms if x != "facebook"]
            if batch_platforms:
                kept.append((i, (batch, batch_platforms, key)))
        parts, batches = [i for i, _ in kept], [b for _, b in kept]
        if not batches:
            if not dry_run:
                self.db.record_publication(topic_id, run_id, channel, "failed", {"error": held["facebook"], "held": held})
            return {"status": "failed", "error": held["facebook"], "posted_parts": 0, "held_platforms": held}
        alt_texts = {}
        if kind != "story":  # Stories take no alt text through Upload-Post
            from .multi_format import slide_alt_text
            alt_texts = {key: [slide_alt_text(Path(p)) for p in batch] for batch, _, key in batches}
        if dry_run:  # the real plan, each request recorded as it would be sent, nothing sent
            for i, (batch, batch_platforms, key) in zip(parts, batches):
                self.db.record_publication(topic_id, run_id, channel, "simulated",
                                           {"part": i, "platforms": batch_platforms, "request_id": key, "photos": batch,
                                            "caption": _caption_for(kind, caption, batch_platforms),
                                            "is_ai_generated": flagged.get(key, False),
                                            **({"alt_text": alt_texts.get(key), "first_comment": first_comment}
                                               if kind != "story" else {}),
                                            **({"held": held} if held else {})})
            log.info("Dry run: %s not posted (%d request(s) planned).", kind, len(batches))
            return {"status": "simulated", "posted_parts": 0, "planned_parts": len(batches),
                    **({"held_platforms": held} if held else {})}
        up = next((p for p in self.publishers if isinstance(p, UploadPostPublisher)), None)
        if up is None or not up.configured():
            return {"status": "failed", "error": "Upload-Post is not configured (needed for Stories and carousels)"}
        done = []
        for i, (batch, batch_platforms, key) in zip(parts, batches):
            try:
                res = up.publish_photos(kind, batch, _caption_for(kind, caption, batch_platforms), batch_platforms, key,
                                        alt_texts=alt_texts.get(key),
                                        first_comment=first_comment if kind != "story" else "",
                                        **({"ai_generated": True} if flagged.get(key) else {}))
            except PublishFailed as e:
                self.db.record_publication(topic_id, run_id, channel, "failed", {"error": str(e), "part": i})
                status = "unknown" if done else "failed"  # earlier frames already went out
                return {"status": status, "error": str(e), "posted_parts": len(done),
                        **({"part": i} if kind == "story" else {})}
            except PublishUnknown as e:
                failed, ok = getattr(e, "failed", {}) or {}, getattr(e, "ok", {}) or {}
                self.db.record_publication(topic_id, run_id, channel, "unknown",
                                           {"error": str(e), "part": i, "failed": failed, "ok": ok, "platforms": batch_platforms})
                return {"status": "unknown", "error": str(e), "posted_parts": len(done), "part": i, "failed": failed, "ok": ok}
            # The request id and platforms are kept: audience metrics are fetched by them later.
            self.db.record_publication(topic_id, run_id, channel, "published",
                                       dict(res, part=i, request_id=key, platforms=batch_platforms))
            done.append(res)
        urls: Dict[str, Any] = {}
        for r in done:
            urls.update(r.get("post_urls") or {})
        return {"status": "published", "post_urls": urls, "posted_parts": len(done),
                **({"held_platforms": held} if held else {})}
