import os
from types import SimpleNamespace

import pytest

from src.backup_crypto import BackupError, decrypt_json
from src.onedrive_backup import OneDriveAuth

CLIENT_ID = "11111111-1111-4111-8111-111111111111"


def fake_msal(monkeypatch):
    import msal
    class Cache:
        has_state_changed = False
        def deserialize(self, value):
            self.data = value
        def serialize(self):
            return "refresh-token-private"
    app = SimpleNamespace(device_calls=0, silent_calls=0, accounts=[], cache=None)
    def factory(client_id, authority, token_cache):
        assert client_id == CLIENT_ID and authority.endswith("/consumers")
        app.cache = token_cache
        return app
    def initiate(scopes):
        assert scopes == ["Files.ReadWrite.AppFolder"]
        app.device_calls += 1
        return {"user_code": "device-code", "message": "Sign in through Microsoft"}
    def acquire(flow):
        app.accounts = [{"home_account_id": "personal-account"}]
        app.cache.has_state_changed = True
        return {"access_token": "access-private"}
    def silent(scopes, account, force_refresh):
        app.silent_calls += 1
        return {"access_token": "refreshed-access-private"}
    app.initiate_device_flow = initiate
    app.acquire_token_by_device_flow = acquire
    app.acquire_token_silent = silent
    app.get_accounts = lambda: app.accounts
    monkeypatch.setattr(msal, "SerializableTokenCache", Cache)
    monkeypatch.setattr(msal, "PublicClientApplication", factory)
    return app


def test_auth_is_explicit_and_token_cache_is_encrypted(monkeypatch, tmp_path):
    app = fake_msal(monkeypatch)
    key = os.urandom(32)
    cache = tmp_path / "credentials" / "tokens.enc"
    auth = OneDriveAuth(CLIENT_ID, cache, key, environment="production")
    with pytest.raises(BackupError, match="authorization required"):
        auth.token()
    assert app.device_calls == 0  # a daemon must never start a browser/device flow itself
    messages = []
    assert auth.login(messages.append)["authenticated"]
    assert messages == ["Sign in through Microsoft"]
    assert b"refresh-token-private" not in cache.read_bytes()
    decrypted = decrypt_json(cache.read_bytes(), key, auth.context)
    assert decrypted["cache"] == "refresh-token-private"
    assert auth.token() == "refreshed-access-private" and app.silent_calls == 1
    assert app.device_calls == 1
    with pytest.raises(BackupError, match="authenticated"):
        OneDriveAuth(CLIENT_ID, cache, key, environment="development")


def test_unregistered_client_is_reported_before_any_network_call(tmp_path):
    with pytest.raises(BackupError, match="ONEDRIVE_CLIENT_ID"):
        OneDriveAuth("", tmp_path / "cache", os.urandom(32), environment="production")


def test_auth_refuses_concurrent_cache_mutation(monkeypatch, tmp_path):
    from src.publication_lock import exclusive
    app = fake_msal(monkeypatch)
    cache = tmp_path / "credentials" / "tokens.enc"
    auth = OneDriveAuth(CLIENT_ID, cache, os.urandom(32), environment="production")
    with exclusive(cache, "onedrive-auth:" + str(cache.resolve())) as acquired:
        assert acquired
        with pytest.raises(BackupError, match="credentials are in use"):
            auth.login()
        with pytest.raises(BackupError, match="credentials are in use"):
            auth.token()
    assert app.device_calls == app.silent_calls == 0


def test_export_managed_cache_preserves_readonly_enrollment_and_existing_cache(monkeypatch, tmp_path):
    fake_msal(monkeypatch)
    key = os.urandom(32)
    source = tmp_path / "custody" / "enrollment.enc"
    destination = tmp_path / "service" / "tokens.enc"
    auth = OneDriveAuth(CLIENT_ID, source, key, environment="production")
    auth.login(lambda _: None)
    original = source.read_bytes()
    auth.export_cache(destination)
    assert source.read_bytes() == original
    assert decrypt_json(destination.read_bytes(), key, auth.context) == decrypt_json(original, key, auth.context)
    assert b"refresh-token-private" not in destination.read_bytes()
    saved = destination.read_bytes()
    auth.export_cache(destination)
    assert destination.read_bytes() == saved
    with pytest.raises(BackupError, match="separate"):
        auth.export_cache(source)
