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

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests

from . import config
from .config import META_IS_AI_GENERATED, is_configured
from .db import PipelineDatabase

log = logging.getLogger(__name__)


class NotConfigured(Exception):
    pass


class PublishFailed(Exception):
    """Definitely not published; safe to try the next publisher."""


class PublishUnknown(Exception):
    """May or may not have been published; do not fail over."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UploadPostPublisher:
    name = "upload-post"
    platforms = ("facebook", "instagram")

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
        if not self.configured():
            raise NotConfigured("UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set")
        meta, media = envelope["metadata"], envelope["media"]
        data: List[Tuple[str, str]] = [
            ("user", self.user),
            *[("platform[]", p) for p in envelope["platforms"]],
            ("title", meta["caption"]),        # Instagram caption (instagram_title falls back to title)
            ("description", meta["caption"]),  # Facebook description
            ("first_comment", meta["first_comment"]),
            ("media_type", "REELS"),
            ("share_to_feed", "true"),
            ("facebook_media_type", "REELS"),
            ("is_ai_generated", "true" if meta["is_ai_generated"] else "false"),
            ("async_upload", "true"),
            ("request_id", envelope["run_id"]),
        ]
        if self.facebook_page_id:
            data.append(("facebook_page_id", self.facebook_page_id))

        response = None
        for attempt in (1, 2):  # the Idempotency-Key makes a retry after a timeout safe
            try:
                with open(media["video_path"], "rb") as vf, open(media["thumbnail_path"], "rb") as tf:
                    response = requests.post(
                        f"{self.base_url}/upload",
                        headers=self._headers(envelope["run_id"]),
                        data=data,
                        files={"video": (Path(media["video_path"]).name, vf, "video/mp4"),
                               "cover_image": (Path(media["thumbnail_path"]).name, tf, "image/jpeg")},
                        timeout=(15, 600),
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
            raise PublishUnknown(f"Partial publish: ok={[r['platform'] for r in live]} failed={[r['platform'] for r in failed]}")
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
            raise PublishFailed(f"Graph {method} {url.split('?')[0].replace(self.token, '***')} -> {resp.status_code}: {body.get('error', body)}")
        return body

    def publish(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured():
            raise NotConfigured("META_ACCESS_TOKEN / META_IG_ACCOUNT_ID not set")
        if "instagram" not in envelope["platforms"]:
            raise NotConfigured("Instagram not among requested platforms")
        meta, media = envelope["metadata"], envelope["media"]
        video_path = Path(media["video_path"])

        # Steps 1-3 can fail without anything being posted.
        try:
            container = self._call("POST", f"{self.graph}/{self.ig_user_id}/media", data={
                "media_type": "REELS", "upload_type": "resumable", "caption": meta["caption"],
                "share_to_feed": "true", "access_token": self.token,
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
            raise PublishFailed(f"Graph {method} {url.split('?')[0]} -> {resp.status_code}: {body.get('error', body)}")
        return body

    def publish(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured():
            raise NotConfigured("META_PAGE_ID / page access token not set")
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
            })
        except (requests.Timeout, requests.ConnectionError) as e:
            raise PublishUnknown(f"Facebook Reel finish outcome unknown: {e}") from e
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


class MetaPublisher:
    def __init__(self, db: Optional[PipelineDatabase] = None, publishers: Optional[list] = None):
        self.db = db or PipelineDatabase()
        self.publishers = publishers if publishers is not None else [
            UploadPostPublisher(), InstagramGraphPublisher(), FacebookPageReelsPublisher()]

    def build_envelope(self, run_id: str, video_path: Path, thumbnail_916_path: Path, thumbnail_1x1_path: Path,
                       script_payload: Dict[str, Any], platforms: Optional[List[str]] = None) -> Dict[str, Any]:
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
            "metadata": {
                "title": script_payload["title"],
                "caption": script_payload["caption"],
                "location": script_payload.get("location", {}),
                "hashtags": script_payload.get("hashtags", []),
                "meta_topic_categories": script_payload.get("meta_topic_categories", []),
                "first_comment": script_payload.get("first_comment", ""),
                "is_ai_generated": META_IS_AI_GENERATED,
            },
            "created_at": _now(),
        }

    def publish_reel(self, envelope: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        tid, run_id = envelope["topic_id"], envelope["run_id"]

        if dry_run:
            res = {"status": "simulated", "channel": "dry-run", "at": _now()}
            self.db.record_publication(tid, run_id, "dry-run", "simulated", {"envelope": envelope})
            log.info("Dry run: nothing was published.")
            return res

        # Each requested platform is posted at most once: a publisher only handles the
        # platforms still remaining that it supports (Upload-Post: both; direct APIs: one each).
        remaining = set(envelope["platforms"])
        errors, done = [], []
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
                self.db.record_publication(tid, run_id, pub.name, "failed", {"error": str(e)})
            except PublishUnknown as e:
                log.error("%s outcome UNKNOWN; not failing over to avoid a duplicate post: %s", pub.name, e)
                self.db.record_publication(tid, run_id, pub.name, "unknown", {"error": str(e)})
                return {"status": "unknown", "channel": pub.name, "error": str(e), "published": done}
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
            return merged
        msg = "; ".join(errors) if errors else "no publisher is configured"
        return {"status": "failed", "error": msg}
