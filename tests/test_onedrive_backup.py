import hashlib
import os
from pathlib import Path
from urllib.parse import unquote

import pytest
import requests

from src.backup_crypto import BackupError
from src.onedrive_backup import GRAPH, CHUNK, OneDriveStore


class Response:
    def __init__(self, status=200, body=None, *, content=b"", headers=None):
        self.status_code, self.body, self.content = status, body or {}, content
        self.headers = headers or {}

    def json(self):
        return self.body

    def close(self):
        pass

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            yield self.content[start:start + size]


class Auth:
    def token(self, force_refresh=False):
        return "access-token-must-not-reach-storage"


def test_custom_destination_walks_only_configured_path_and_encodes_names():
    class FilesAuth(Auth):
        scopes = ["Files.ReadWrite"]
    class FolderCloud:
        def __init__(self):
            self.names = []
            self.urls = []
        def request(self, method, url, **kwargs):
            self.urls.append(url)
            if method == "GET" and url == GRAPH + "/me/drive/root":
                return Response(body={"id": "drive-root"})
            if method == "GET":
                return Response(404)
            assert method == "POST" and url.endswith("/children")
            self.names.append(kwargs["json"]["name"])
            return Response(201, body={"id": f"folder-{len(self.names)}", "folder": {}})
    cloud = FolderCloud()
    store = OneDriveStore(FilesAuth(), environment="production", key=os.urandom(32), session=cloud,
                         backup_path="Backups/Content Creation/Relic & Riddle")
    result = store.connect()
    assert cloud.names == ["Backups", "Content Creation", "Relic & Riddle", "production", "snapshots"]
    assert any("Relic%20%26%20Riddle" in url for url in cloud.urls)
    assert not any("approot" in url for url in cloud.urls)
    assert result["destination"] == "Backups/Content Creation/Relic & Riddle/production/snapshots"


@pytest.mark.parametrize("path", ["/Backups", "Backups/../Other", "Backups//Other", "Backups\\Other", "Backups/Other?"])
def test_unsafe_custom_destination_is_refused_before_network(path):
    class FilesAuth(Auth):
        scopes = ["Files.ReadWrite"]
    with pytest.raises(BackupError, match="Unsafe"):
        OneDriveStore(FilesAuth(), environment="production", key=os.urandom(32), backup_path=path)


def test_custom_destination_never_silently_widens_appfolder_permission():
    with pytest.raises(BackupError, match="explicit Files.ReadWrite consent"):
        OneDriveStore(Auth(), environment="production", key=os.urandom(32), backup_path="Backups")


def test_personal_content_transfer_host_is_allowed_without_bearer_token(tmp_path):
    cloud = Cloud()
    cloud.upload_url = "https://my.microsoftpersonalcontent.com/upload/signed-secret"
    cloud.download_url = "https://my.microsoftpersonalcontent.com/download/signed-secret"
    encrypted = tmp_path / "snapshot.enc"
    encrypted.write_bytes(b"encrypted backup data")
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud)
    assert store.upload(encrypted, "snapshot-a", session_path=tmp_path / "session.enc")["verification"] == "download-sha256"
    assert all("Authorization" not in headers for _, url, headers in cloud.calls if url.startswith("https://my.microsoftpersonalcontent.com/"))


class Cloud:
    """A protocol fake: tracks committed byte ranges and accepted-but-lost replies."""
    upload_url = "https://storage.up.1drv.com/upload/credential-secret"
    download_url = "https://storage.files.1drv.com/download/credential-secret"

    def __init__(self, *, disconnect=False, corrupt=False, throttle=False):
        self.body = bytearray()
        self.item = None
        self.disconnect, self.corrupt, self.throttle = disconnect, corrupt, throttle
        self.calls = []
        self.session_creations = 0

    def request(self, method, url, *, headers, **kwargs):
        self.calls.append((method, url, headers))
        if url.startswith(GRAPH):
            assert headers["Authorization"] == "Bearer access-token-must-not-reach-storage"
            path = unquote(url[len(GRAPH):])
            if path == "/me/drive/special/approot":
                return Response(body={"id": "app-root", "folder": {}})
            if path == "/me/drive/items/app-root:/production":
                return Response(body={"id": "prod", "folder": {}})
            if path == "/me/drive/items/prod:/snapshots":
                return Response(body={"id": "snapshots", "folder": {}})
            if path.endswith("/createUploadSession"):
                assert kwargs["json"]["item"]["@microsoft.graph.conflictBehavior"] == "fail"
                self.session_creations += 1
                return Response(body={"uploadUrl": self.upload_url})
            if path == "/me/drive/items/object-id/content":
                return Response(302, headers={"Location": self.download_url})
            if path.startswith("/me/drive/items/snapshots:/"):
                return Response(body=self.item) if self.item else Response(404)
            if path == "/me/drive/items/snapshots/children":
                return Response(body={"value": [self.item] if self.item else []})
            raise AssertionError(path)
        assert "Authorization" not in headers, "Bearer token leaked to a preauthenticated URL"
        if url == self.download_url:
            blob = bytes(self.body)
            if self.corrupt:
                blob = bytes([blob[0] ^ 1]) + blob[1:]
            return Response(content=blob)
        assert url == self.upload_url
        if method == "GET":
            return Response(body={"nextExpectedRanges": [f"{len(self.body)}-"]})
        assert method == "PUT"
        if self.throttle:
            self.throttle = False
            return Response(429, headers={"Retry-After": "0"})
        start_end, total = headers["Content-Range"].split(" ")[1].split("/")
        start, end = map(int, start_end.split("-"))
        assert start == len(self.body)
        assert end - start + 1 == len(kwargs["data"])
        self.body.extend(kwargs["data"])
        if len(self.body) == int(total):
            self.item = {"id": "object-id", "name": "snapshot-a.enc", "size": len(self.body), "eTag": "etag-1"}
        if self.disconnect:
            self.disconnect = False
            raise requests.ConnectionError("must not expose " + self.upload_url)
        if self.item:
            return Response(201, body=self.item)
        return Response(202, body={"nextExpectedRanges": [f"{len(self.body)}-"]})


def test_interrupted_upload_resumes_confirmed_offset_and_verifies_download(tmp_path):
    data = b"x" * CHUNK + b"last chunk"
    encrypted, session_path = tmp_path / "backup.enc", tmp_path / "session.enc"
    encrypted.write_bytes(data)
    cloud = Cloud(disconnect=True, throttle=True)
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud, sleep=lambda _: None)
    with pytest.raises(BackupError, match="remains queued") as caught:
        store.upload(encrypted, "snapshot-a", session_path=session_path)
    assert "credential-secret" not in str(caught.value)
    assert session_path.exists() and b"credential-secret" not in session_path.read_bytes()
    assert len(cloud.body) == CHUNK
    receipt = store.upload(encrypted, "snapshot-a", session_path=session_path)
    assert cloud.session_creations == 1
    assert bytes(cloud.body) == data
    assert receipt["sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["verification"] == "download-sha256"
    assert not session_path.exists()


def test_lost_completion_reply_reuses_existing_object_without_duplicate_upload(tmp_path):
    encrypted = tmp_path / "backup.enc"
    encrypted.write_bytes(b"small backup")
    cloud = Cloud(disconnect=True)
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud)
    with pytest.raises(BackupError):
        store.upload(encrypted, "snapshot-a", session_path=tmp_path / "session.enc")
    assert cloud.item
    store.upload(encrypted, "snapshot-a", session_path=tmp_path / "session.enc")
    assert cloud.session_creations == 1
    assert sum(method == "PUT" for method, _, _ in cloud.calls) == 1


def test_active_session_resumes_past_personal_onedrive_empty_placeholder(tmp_path):
    class PersonalCloud(Cloud):
        def request(self, method, url, **kwargs):
            if method == "GET" and url.startswith(GRAPH + "/me/drive/items/snapshots:/") and self.body and not self.item:
                return Response(body={"id": "placeholder", "size": 0})
            return super().request(method, url, **kwargs)
    cloud = PersonalCloud(disconnect=True)
    encrypted = tmp_path / "snapshot.enc"
    encrypted.write_bytes(b"x" * CHUNK + b"tail")
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud)
    session = tmp_path / "session.enc"
    with pytest.raises(BackupError, match="remains queued"):
        store.upload(encrypted, "snapshot-a", session_path=session)
    assert store.upload(encrypted, "snapshot-a", session_path=session)["verification"] == "download-sha256"
    assert cloud.session_creations == 1
    assert bytes(cloud.body) == encrypted.read_bytes()


def test_corrupt_readback_cannot_be_reported_verified(tmp_path):
    encrypted = tmp_path / "backup.enc"
    encrypted.write_bytes(b"a backup")
    cloud = Cloud(corrupt=True)
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud)
    with pytest.raises(BackupError, match="checksum"):
        store.upload(encrypted, "snapshot-a", session_path=tmp_path / "session.enc")
    assert encrypted.read_bytes() == b"a backup"


def test_unexpected_signed_host_is_rejected_without_forwarding_a_token(tmp_path):
    cloud = Cloud()
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud)
    with pytest.raises(BackupError, match="unexpected"):
        store._request("PUT", "https://attacker.invalid/", signed=True, data=b"encrypted")
    assert not cloud.calls


def test_download_never_deletes_an_existing_destination(tmp_path):
    path = tmp_path / "existing"
    path.write_bytes(b"keep")
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=Cloud())
    with pytest.raises(BackupError, match="already exist"):
        store.download("snapshot-a", path)
    assert path.read_bytes() == b"keep"


@pytest.mark.parametrize("mode", ["resume", "ignored-range", "wrong-range", "always-fails"])
def test_interrupted_cloud_download_resumes_safely_without_token_leak(tmp_path, mode):
    data = b"first confirmed bytes" + b"remaining bytes"
    split = 21
    class Broken(Response):
        def iter_content(self, size):
            yield data[:split]
            raise requests.ConnectionError("signed URL must never appear in the error")
    class DownloadCloud(Cloud):
        def __init__(self):
            super().__init__()
            self.downloads = 0
            self.ranges = []
        def request(self, method, url, *, headers, **kwargs):
            if url == GRAPH + "/me/drive/items/object-id/content":
                assert "Range" not in headers
            if url == self.download_url:
                assert "Authorization" not in headers
                self.downloads += 1
                self.ranges.append(headers.get("Range"))
                if self.downloads == 1 or mode == "always-fails":
                    return Broken()
                if mode == "ignored-range":
                    return Response(content=data)
                return Response(206, content=data[split:], headers={
                    "Content-Range": f"bytes {0 if mode == 'wrong-range' else split}-{len(data)-1}/{len(data)}"})
            return super().request(method, url, headers=headers, **kwargs)
    cloud = DownloadCloud()
    store = OneDriveStore(Auth(), environment="production", key=os.urandom(32), session=cloud, sleep=lambda _: None)
    target = tmp_path / "cloud.enc"
    if mode in {"wrong-range", "always-fails"}:
        with pytest.raises(BackupError):
            store._download("object-id", target)
        assert not target.exists()
    else:
        digest, size = store._download("object-id", target)
        assert target.read_bytes() == data
        assert size == len(data) and digest == hashlib.sha256(data).hexdigest()
    assert cloud.ranges[0] is None
    assert cloud.ranges[1] == f"bytes={split}-"
    assert cloud.downloads <= 5
