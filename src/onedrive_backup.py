"""Personal OneDrive transport: delegated MSAL auth, resumable uploads, read-back verification."""
import hashlib
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from .backup_crypto import BackupError, decrypt_json, encrypt_json, private_write, sha256_file

GRAPH = "https://graph.microsoft.com/v1.0"
CHUNK = 16 * 320 * 1024  # 5 MiB; Graph fragments must be multiples of 320 KiB.


class OneDriveAuth:
    def __init__(self, client_id, cache_path, key, *, environment, scope="appfolder"):
        try:
            uuid.UUID(client_id)
        except (ValueError, TypeError, AttributeError):
            raise BackupError("Configure ONEDRIVE_CLIENT_ID with a registered personal-account application ID") from None
        if scope not in {"appfolder", "files"}:
            raise BackupError("ONEDRIVE_SCOPE must be appfolder or the explicitly consented files scope")
        if environment not in {"development", "production"}:
            raise BackupError("Invalid backup environment")
        self.path, self.key = Path(cache_path), key
        self.context = f"msal:{client_id}:{environment}:{scope}"
        self.scopes = ["Files.ReadWrite.AppFolder" if scope == "appfolder" else "Files.ReadWrite"]
        import msal
        self.cache = msal.SerializableTokenCache()
        if self.path.exists():
            data = decrypt_json(self.path.read_bytes(), key, self.context)
            self.cache.deserialize(data["cache"])
        # MSAL adds openid/profile/offline_access itself. Never use a client secret
        # or a personal account password for this delegated consumer-account flow.
        self.app = msal.PublicClientApplication(client_id, authority="https://login.microsoftonline.com/consumers",
                                                token_cache=self.cache)

    def _save(self):
        if self.cache.has_state_changed:
            private_write(self.path, encrypt_json({"cache": self.cache.serialize()}, self.key, self.context))

    def export_cache(self, destination):
        """Provision a separate same-account cache without replacing existing custody.

        The application provider owns credential serialization. Launchers should
        never mutate MSAL's private cache state or copy plaintext token values.
        """
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise BackupError("Managed token cache must be separate from enrollment custody")
        from .publication_lock import exclusive
        with exclusive(destination, "onedrive-cache-provision") as acquired:
            if not acquired:
                raise BackupError("Another process is provisioning the managed token cache")
            if destination.exists():
                decrypt_json(destination.read_bytes(), self.key, self.context)
                return
            if not self.path.is_file() or len(self.app.get_accounts()) != 1:
                raise BackupError("OneDrive authorization required before provisioning a managed token cache")
            value = decrypt_json(self.path.read_bytes(), self.key, self.context)
            private_write(destination, encrypt_json(value, self.key, self.context), exclusive=True)

    @contextmanager
    def _ownership(self):
        from .publication_lock import exclusive
        with exclusive(self.path, "onedrive-auth:" + str(self.path.resolve())) as acquired:
            if not acquired:
                raise BackupError("OneDrive credentials are in use; retry after the other backup command finishes")
            # Another process may have refreshed the cache since construction.
            if self.path.exists():
                data = decrypt_json(self.path.read_bytes(), self.key, self.context)
                self.cache.deserialize(data["cache"])
            yield

    def login(self, display=print):
        with self._ownership():
            return self._login(display)

    def _login(self, display):
        flow = self.app.initiate_device_flow(scopes=self.scopes)
        if "user_code" not in flow:
            raise BackupError("Microsoft could not start device sign-in; check the app's public-client configuration")
        display(flow["message"])
        result = self.app.acquire_token_by_device_flow(flow)
        self._save()
        if not result or "access_token" not in result:
            raise BackupError("Microsoft sign-in was not completed; no backup connection established")
        if len(self.app.get_accounts()) != 1:
            raise BackupError("Token cache contains multiple accounts; use a separate cache per backup account")
        return {"authenticated": True, "scope": self.scopes[0]}

    def token(self, force_refresh=False):
        with self._ownership():
            return self._token(force_refresh)

    def _token(self, force_refresh):
        accounts = self.app.get_accounts()
        if len(accounts) != 1:
            raise BackupError("OneDrive authorization required: run the explicit backupctl auth command")
        result = self.app.acquire_token_silent(self.scopes, account=accounts[0], force_refresh=force_refresh)
        self._save()
        if not result or "access_token" not in result:
            raise BackupError("OneDrive authorization expired or was revoked; run backupctl auth again")
        return result["access_token"]


def _signed_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    suffixes = (".1drv.com", ".onedrive.com", ".sharepoint.com", ".sharepoint-df.com",
                ".storage.live.com", ".1drvusercontent.com", ".onedrive.live.com",
                ".microsoftpersonalcontent.com")
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or
            not any(host.endswith(suffix) for suffix in suffixes)):
        raise BackupError("OneDrive returned an unexpected upload/download host")
    return url


class OneDriveStore:
    def __init__(self, auth, *, environment, key, session=None, sleep=time.sleep, backup_path=""):
        if environment not in {"development", "production"}:
            raise BackupError("Invalid backup environment")
        self.auth, self.environment, self.key = auth, environment, key
        self.backup_path = backup_path
        self.path_parts = backup_path.split("/") if backup_path else []
        for name in self.path_parts:
            self._validate_name(name)
        if self.path_parts and getattr(auth, "scopes", []) != ["Files.ReadWrite"]:
            raise BackupError("A custom OneDrive destination requires explicit Files.ReadWrite consent (ONEDRIVE_SCOPE=files)")
        self.session, self.sleep = session or requests.Session(), sleep
        self.folder_id = None

    def _request(self, method, path, *, signed=False, accepted=(), **kwargs):
        if signed:
            url = _signed_url(path)
        else:
            if not path.startswith("/") or path.startswith("//"):
                raise BackupError("Invalid Microsoft Graph resource path")
            url = GRAPH + path
        supplied = kwargs.pop("headers", {})
        for attempt in range(4):
            headers = dict(supplied)
            if not signed:
                headers["Authorization"] = "Bearer " + self.auth.token(force_refresh=attempt > 0)
            try:
                response = self.session.request(method, url, headers=headers, timeout=(15, 90),
                                                allow_redirects=False, **kwargs)
            except requests.RequestException:
                # Do not expose exceptions: Graph signed URLs are credentials.
                raise BackupError("OneDrive request interrupted; the backup remains queued for retry") from None
            if response.status_code in accepted or 200 <= response.status_code < 300:
                return response
            if response.status_code == 401 and not signed and attempt == 0:
                response.close()
                continue
            if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                response.close()
                # Never retry sooner than Retry-After. Longer delays are left to
                # the persistent retry queue rather than blocking a worker.
                if attempt < 3 and 0 <= delay <= 30:
                    self.sleep(delay)
                    continue
                raise BackupError("OneDrive is throttled/unavailable; the backup remains queued")
            code = response.status_code
            response.close()
            if code == 403:
                raise BackupError("OneDrive access denied (HTTP 403); verify consent. Permissions were not widened")
            raise BackupError(f"OneDrive request failed (HTTP {code}); no verified backup claimed")
        raise BackupError("OneDrive authentication failed; authorize again")

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not value:
            raise BackupError("OneDrive returned no item identity")
        return quote(value, safe="")

    @staticmethod
    def _validate_name(name):
        if (not name or name in {".", ".."} or name != name.strip() or name.endswith(".") or
                any(ord(c) < 32 or c in '\\/:*?"<>|#%' for c in name)):
            raise BackupError("Unsafe OneDrive backup name")

    def _child(self, parent, name):
        self._validate_name(name)
        response = self._request("GET", f"/me/drive/items/{self._id(parent)}:/{quote(name, safe='')}", accepted={404})
        if response.status_code == 404:
            response.close()
            return None
        try:
            return response.json()
        finally:
            response.close()

    def _folder(self, parent, name):
        child = self._child(parent, name)
        if child is None:
            response = self._request("POST", f"/me/drive/items/{self._id(parent)}/children", accepted={409},
                                     json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
            try:
                child = self._child(parent, name) if response.status_code == 409 else response.json()
            finally:
                response.close()
        if not child or "folder" not in child:
            raise BackupError("OneDrive backup directory is missing or is not a folder")
        return child["id"]

    def connect(self):
        response = self._request("GET", "/me/drive/root" if self.path_parts else "/me/drive/special/approot")
        try:
            root = response.json()
        finally:
            response.close()
        parent = root["id"]
        for name in self.path_parts:
            parent = self._folder(parent, name)
        parent = self._folder(parent, self.environment)
        self.folder_id = self._folder(parent, "snapshots")
        return {"connected": True, "environment": self.environment, "folder_id": self.folder_id,
                "destination": (self.backup_path or "[app folder]") + "/" + self.environment + "/snapshots"}

    def _ensure(self):
        if self.folder_id is None:
            self.connect()

    def verify_item(self, item, expected_hash, expected_size):
        if item.get("size") != expected_size:
            raise BackupError("OneDrive stored size differs from the staged backup")
        digest, size = self._download(item["id"])
        if (digest, size) != (expected_hash, expected_size):
            raise BackupError("OneDrive read-back checksum failed; local recovery data retained")
        return {"item_id": item["id"], "etag": item.get("eTag"),
                "size": size, "sha256": digest, "verification": "download-sha256"}

    def _download(self, item_id, target=None):
        digest, size = hashlib.sha256(), 0
        output, complete = None, False
        try:
            output = Path(target).open("xb") if target is not None else None
            for attempt in range(5):
                response = None
                try:
                    # Refresh the signed URL on retry. Range belongs on the storage
                    # request, never on Graph's /content redirect endpoint.
                    response = self._request("GET", f"/me/drive/items/{self._id(item_id)}/content", accepted={302}, stream=True)
                    redirects = 0
                    while response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        response.close()
                        redirects += 1
                        if redirects > 5:
                            raise BackupError("Too many OneDrive download redirects")
                        headers = {"Range": f"bytes={size}-"} if size else {}
                        response = self._request("GET", location, signed=True, headers=headers,
                                                 accepted={301, 302, 303, 307, 308}, stream=True)
                    expected_end = total = None
                    if response.status_code == 206:
                        content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not content_range:
                            raise BackupError("OneDrive returned an invalid download range")
                        start, end, total = map(int, content_range.groups())
                        if start != size or not start <= end < total:
                            raise BackupError("OneDrive returned the wrong download offset")
                        expected_end = end + 1
                    elif response.status_code == 200 and size:
                        # A server may ignore Range. Restart hashing and our own
                        # partial file instead of appending duplicate bytes.
                        digest, size = hashlib.sha256(), 0
                        if output is not None:
                            output.seek(0)
                            output.truncate()
                    for block in response.iter_content(1024 * 1024):
                        if block:
                            if expected_end is not None and size + len(block) > expected_end:
                                raise BackupError("OneDrive exceeded the declared download range")
                            if output is not None:
                                output.write(block)
                            digest.update(block)
                            size += len(block)
                    if expected_end is not None and size != expected_end:
                        raise requests.ConnectionError("Incomplete range")
                    if total is not None and size < total:
                        continue
                    complete = True
                    return digest.hexdigest(), size
                except requests.RequestException:
                    if attempt == 4:
                        break
                    self.sleep(min(2 ** attempt, 8))
                finally:
                    if response is not None:
                        response.close()
            raise BackupError("OneDrive download interrupted after bounded retries; no verification or restore claimed")
        finally:
            if output is not None:
                output.close()
                if not complete:
                    Path(target).unlink(missing_ok=True)

    def upload(self, path, snapshot_id, *, session_path, on_uploaded=lambda item: None):
        self._ensure()
        path, session_path = Path(path), Path(session_path)
        name, size, digest = snapshot_id + ".enc", path.stat().st_size, sha256_file(path)
        context = "upload-session:" + self.environment + ":" + snapshot_id
        state = decrypt_json(session_path.read_bytes(), self.key, context) if session_path.exists() else None
        if state and (state.get("sha256") != digest or state.get("size") != size):
            raise BackupError("Staged backup changed during an upload session")
        existing = self._child(self.folder_id, name)
        # Personal OneDrive can expose a zero-byte placeholder while our saved
        # upload session is active. It is not a completed immutable snapshot.
        if existing and not (existing.get("size") == 0 and state):
            on_uploaded(existing)
            result = self.verify_item(existing, digest, size)
            session_path.unlink(missing_ok=True)
            return result
        if state:
            response = self._request("GET", state["upload_url"], signed=True, accepted={404, 410})
            try:
                if response.status_code in {404, 410}:
                    state = None
                else:
                    state["offset"] = int(response.json()["nextExpectedRanges"][0].split("-")[0])
            finally:
                response.close()
        if state is None:
            response = self._request("POST", f"/me/drive/items/{self._id(self.folder_id)}:/{quote(name)}:/createUploadSession",
                                     json={"item": {"@microsoft.graph.conflictBehavior": "fail", "name": name}})
            try:
                data = response.json()
                state = {"upload_url": _signed_url(data["uploadUrl"]), "offset": 0, "size": size, "sha256": digest}
            finally:
                response.close()
            private_write(session_path, encrypt_json(state, self.key, context))
        item = None
        with path.open("rb") as stream:
            while state["offset"] < size:
                offset = state["offset"]
                if not 0 <= offset < size or offset % (320 * 1024):
                    raise BackupError("OneDrive returned an invalid resumable upload offset")
                stream.seek(offset)
                data = stream.read(CHUNK)
                response = self._request("PUT", state["upload_url"], signed=True, accepted={416}, data=data,
                                         headers={"Content-Length": str(len(data)),
                                                  "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}"})
                try:
                    if response.status_code == 416:
                        # Leave the same encrypted session for the next retry to
                        # query its authoritative nextExpectedRanges.
                        raise BackupError("Upload offset changed; the saved session will be queried on retry")
                    if response.status_code in {200, 201}:
                        item = response.json()
                        break
                    if response.status_code != 202:
                        raise BackupError("Unexpected upload session response")
                    next_offset = int(response.json()["nextExpectedRanges"][0].split("-")[0])
                    if not offset < next_offset <= size:
                        raise BackupError("Upload made no confirmed progress")
                    state["offset"] = next_offset
                    private_write(session_path, encrypt_json(state, self.key, context))
                finally:
                    response.close()
        if item is None:
            item = self._child(self.folder_id, name)
        if not item:
            raise BackupError("OneDrive has not confirmed the completed upload")
        on_uploaded(item)
        result = self.verify_item(item, digest, size)
        session_path.unlink(missing_ok=True)
        return result

    def download(self, snapshot_id, destination):
        if Path(destination).exists():
            raise BackupError("Download destination must not already exist")
        self._ensure()
        item = self._child(self.folder_id, snapshot_id + ".enc")
        if not item:
            raise BackupError("Snapshot not found in this environment's OneDrive backup folder")
        digest, size = self._download(item["id"], destination)
        if size != item.get("size"):
            Path(destination).unlink(missing_ok=True)
            raise BackupError("Incomplete OneDrive snapshot download")
        return {"sha256": digest, "size": size, "item_id": item["id"]}

    def list_snapshots(self):
        self._ensure()
        path = f"/me/drive/items/{self._id(self.folder_id)}/children"
        items = []
        while path:
            response = self._request("GET", path)
            try:
                data = response.json()
            finally:
                response.close()
            items.extend({"snapshot_id": item["name"][:-4], "size": item.get("size"), "item_id": item["id"]}
                         for item in data.get("value", []) if item.get("name", "").endswith(".enc"))
            link = data.get("@odata.nextLink")
            if link and not link.startswith(GRAPH + "/"):
                raise BackupError("Unexpected OneDrive pagination host")
            path = link[len(GRAPH):] if link else None
        return items
