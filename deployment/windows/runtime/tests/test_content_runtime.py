"""Offline tests of the maintained launcher adapter (`runtime/content_runtime.py`, the file the release
deployer installs); no actual application or secret store is accessed. Run from `deployment/windows/runtime`:
`python -m unittest discover -s tests -p "test_*.py" -v` (CI does)."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import content_runtime as runtime


class ContentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.settings = runtime.LaunchSettings(
            app_root=root / "app", maintenance_root=root / "maintenance",
            runtime_root=root / "managed", backup_key_file=root / "custody" / "recovery.key",
            source_token_cache=root / "original" / "cache.enc",
            token_cache=root / "managed" / "secrets" / "cache.enc",
            legacy_archive=root / "original" / "legacy-archive",
            client_id="d7259f42-57c3-4613-913e-871737c91faf", expected_bot_id="12345")
        self.manifest = root / "launch.json"

    def write_manifest(self, **updates: str) -> None:
        fields = {key: str(value) for key, value in asdict(self.settings).items()}
        fields.update(updates)
        self.manifest.write_text(json.dumps(fields), encoding="utf-8")

    def test_accepts_explicit_paths_without_using_the_logon_profile(self) -> None:
        self.write_manifest()
        self.assertEqual(runtime.LaunchSettings.load(self.manifest), self.settings)

    def test_rejects_a_cache_outside_the_protected_runtime(self) -> None:
        self.write_manifest(token_cache=str(self.settings.app_root / "cache.enc"))
        with self.assertRaisesRegex(ValueError, "inside"):
            runtime.LaunchSettings.load(self.manifest)

    def test_rejects_overwriting_the_original_cache(self) -> None:
        self.write_manifest(token_cache=str(self.settings.source_token_cache))
        with self.assertRaisesRegex(ValueError, "separate"):
            runtime.LaunchSettings.load(self.manifest)

    def test_rejects_relative_paths_and_invalid_client_ids(self) -> None:
        for update in ({"app_root": "relative"}, {"client_id": "not-a-uuid"}):
            with self.subTest(update=update):
                self.write_manifest(**update)
                with self.assertRaises(ValueError):
                    runtime.LaunchSettings.load(self.manifest)

    def test_refuses_a_different_application_identity(self) -> None:
        config = ModuleType("src.config")
        config.TELEGRAM_BOT_TOKEN = "99999:fixture"
        config.BASE_DIR = self.settings.app_root
        with patch.object(runtime.importlib, "import_module", side_effect=[ModuleType("src"), config]), \
                patch.object(runtime.sys, "path", []), patch.dict(runtime.os.environ):
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                runtime.load_application(self.settings)

    def test_backup_configuration_uses_explicit_paths_and_api_retry_only(self) -> None:
        package = ModuleType("src")
        package.__path__ = [str(self.settings.app_root / "src")]
        config = ModuleType("src.config")
        runtime.configure_backup(self.settings, package, config)
        self.assertEqual(config.BACKUP_KEY_FILE, self.settings.backup_key_file)
        self.assertEqual(config.ONEDRIVE_TOKEN_CACHE, self.settings.token_cache)
        self.assertEqual(config.BACKUP_SPOOL_DIR, self.settings.runtime_root / "spool" / "Backup")
        self.assertFalse(config.BACKUP_SYNC_FOLDER_FALLBACK)

    def test_backup_runs_the_released_application_modules_not_a_maintenance_overlay(self) -> None:
        # QF-72: hourly copies, retention and the status receipt live in the application's backup modules; an
        # overlay first on the package path would shadow them with older code.
        package = ModuleType("src")
        package.__path__ = [str(self.settings.app_root / "src")]
        runtime.configure_backup(self.settings, package, ModuleType("src.config"))
        self.assertEqual(package.__path__, [str(self.settings.app_root / "src")])

    def test_cache_bootstrap_leaves_the_original_untouched(self) -> None:
        self.settings.source_token_cache.parent.mkdir()
        self.settings.source_token_cache.write_bytes(b"fixture-encrypted-provider-input")
        auth = SimpleNamespace(path=self.settings.source_token_cache,
                               cache=SimpleNamespace(has_state_changed=False), _save=Mock())
        crypto = SimpleNamespace(load_key=Mock(return_value=b"fixture-provider-key"))
        provider = SimpleNamespace(OneDriveAuth=Mock(return_value=auth))
        with patch.object(runtime.importlib, "import_module", side_effect=[crypto, provider]):
            runtime.bootstrap_backup_cache(self.settings)
        self.assertEqual(auth.path, self.settings.token_cache)
        self.assertTrue(auth.cache.has_state_changed)
        auth._save.assert_called_once_with()
        self.assertEqual(self.settings.source_token_cache.read_bytes(), b"fixture-encrypted-provider-input")
        self.assertEqual(provider.OneDriveAuth.call_args.args[1], self.settings.source_token_cache)

    def test_existing_managed_cache_does_not_reimport_credentials(self) -> None:
        self.settings.token_cache.parent.mkdir(parents=True)
        self.settings.token_cache.touch()
        with patch.object(runtime.importlib, "import_module") as importer:
            runtime.bootstrap_backup_cache(self.settings)
        importer.assert_not_called()

    def test_missing_enrollment_fails_before_provider_access(self) -> None:
        with patch.object(runtime.importlib, "import_module") as importer:
            with self.assertRaisesRegex(ValueError, "enrollment"):
                runtime.bootstrap_backup_cache(self.settings)
        importer.assert_not_called()

    def test_pipeline_runs_the_daemon_live(self) -> None:
        # Production was activated by the operator on 2026-10-25: no dry run, every run is a posting
        # candidate and approval still decides (QF-47: the obsolete handoff copy still said --dry-run).
        with patch.object(runtime, "load_application", return_value=(ModuleType("src"), ModuleType("src.config"))), \
                patch.object(runtime.os, "chdir"), patch.object(runtime.runpy, "run_path") as runner, \
                patch.object(runtime.sys, "argv", []):
            self.assertEqual(runtime.execute("pipeline", self.settings), 0)
            self.assertEqual(runtime.sys.argv[1:], ["daemon"])
        runner.assert_called_once_with(str(self.settings.app_root / "main.py"), run_name="__main__")

    def test_restore_rejects_traversal_before_loading_application_secrets(self) -> None:
        with patch.object(runtime, "load_application") as loader:
            with self.assertRaisesRegex(ValueError, "snapshot identity"):
                runtime.execute("backup-restore", self.settings, "../../production")
        loader.assert_not_called()

    def test_sdk_exception_text_never_reaches_output(self) -> None:
        stderr = io.StringIO()
        with patch.object(runtime, "watch_launcher"), \
                patch.object(runtime.LaunchSettings, "load", side_effect=RuntimeError("credential-bearing-url")), \
                redirect_stderr(stderr):
            self.assertEqual(runtime.main(["backup-check"]), 1)
        self.assertNotIn("credential-bearing-url", stderr.getvalue())
        self.assertEqual(json.loads(stderr.getvalue())["error_type"], "RuntimeError")

    def test_parent_watcher_uses_only_a_synchronize_handle_and_ends_its_own_tree(self) -> None:
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=42), WaitForSingleObject=Mock(return_value=0), CloseHandle=Mock())
        process_os = SimpleNamespace(name="nt", getppid=Mock(return_value=123), getpid=Mock(return_value=456),
                                     environ={"SystemRoot": "C:/Windows", "HNSHL_CONTENT_LAUNCHER_PID": "789"}, _exit=Mock())
        with patch.object(runtime, "os", process_os), \
                patch.object(runtime.ctypes, "WinDLL", Mock(return_value=kernel), create=True), \
                patch.object(runtime.threading, "Thread") as thread, \
                patch.object(runtime.subprocess, "run") as kill:
            runtime.watch_launcher()
            kernel.OpenProcess.assert_called_once_with(0x00100000, False, 789)
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.call_args.kwargs["target"]()
            self.assertEqual(kill.call_args.args[0][-4:], ["/PID", "456", "/T", "/F"])
            kernel.CloseHandle.assert_called_once_with(42)
            process_os._exit.assert_called_once_with(1)

    def test_pipeline_custody_check_accepts_denied_access_without_reading_data(self) -> None:
        with patch.object(Path, "open", side_effect=PermissionError) as opener:
            runtime.check_backup_isolation(self.settings)
        self.assertEqual(opener.call_count, 2)

    def test_parent_watcher_rejects_a_self_referencing_launcher(self) -> None:
        kernel = SimpleNamespace(OpenProcess=Mock(), WaitForSingleObject=Mock(), CloseHandle=Mock())
        process_os = SimpleNamespace(name="nt", getppid=Mock(return_value=123), getpid=Mock(return_value=456),
                                     environ={"HNSHL_CONTENT_LAUNCHER_PID": "456"})
        with patch.object(runtime, "os", process_os), \
                patch.object(runtime.ctypes, "WinDLL", Mock(return_value=kernel), create=True):
            with self.assertRaisesRegex(ValueError, "launcher identity"):
                runtime.watch_launcher()
        kernel.OpenProcess.assert_not_called()

    def test_pipeline_custody_check_rejects_an_overbroad_read_grant(self) -> None:
        with patch.object(Path, "open", return_value=io.BytesIO(b"not-read")):
            with self.assertRaisesRegex(PermissionError, "excessive access"):
                runtime.check_backup_isolation(self.settings)

    def execute_backup(self, command: str, provider: Mock, snapshot_id: str = "") -> int:
        with patch.object(runtime, "load_application", return_value=(ModuleType("src"), ModuleType("src.config"))), \
                patch.object(runtime, "configure_backup"), patch.object(runtime, "bootstrap_backup_cache"), \
                patch.object(runtime.importlib, "import_module", return_value=provider), redirect_stdout(io.StringIO()):
            return runtime.execute(command, self.settings, snapshot_id)

    def test_cycle_requires_fresh_download_verified_snapshot_and_no_pending_jobs(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot = {"snapshot_id": f"production-{stamp}-012345abcdef", "environment": "production",
                    "state": "verified", "sha256": "fixture-digest",
                    "receipt": {"verification": "download-sha256", "sha256": "fixture-digest"}}
        provider = Mock()
        provider.run_cycle.return_value = [{"state": "busy"}]
        provider.pending_jobs.return_value = [snapshot]
        self.assertEqual(self.execute_backup("backup-cycle", provider), 0)
        for jobs in ([], [dict(snapshot, snapshot_id="production-20200101T000000Z-012345abcdef")],
                     [dict(snapshot, receipt={"verification": "download-sha256", "sha256": "wrong"})],
                     [snapshot, {"environment": "production", "state": "queued"}]):
            with self.subTest(jobs=jobs):
                provider.pending_jobs.return_value = jobs
                self.assertEqual(self.execute_backup("backup-cycle", provider), 2)

    def test_restore_requires_cloud_hash_database_and_publish_safety_evidence(self) -> None:
        identity = "production-20261022T000000Z-012345abcdef"
        report = {"state": "restore-tested", "source": "onedrive-api", "snapshot_id": identity,
                  "database_integrity": "ok", "publishing_disabled": True, "checksums_verified": 5}
        provider = Mock()
        provider.restore_job.return_value = report
        self.assertEqual(self.execute_backup("backup-restore", provider, identity), 0)
        provider.restore_job.assert_called_once_with(identity, self.settings.runtime_root / "restore-drill" / identity,
                                                    cloud=True, relocate=True)
        for changes in ({"source": "local"}, {"database_integrity": "failed"},
                        {"publishing_disabled": False}, {"checksums_verified": 0}, {"snapshot_id": "other"}):
            with self.subTest(changes=changes):
                provider.restore_job.return_value = dict(report, **changes)
                self.assertEqual(self.execute_backup("backup-restore", provider, identity), 1)


if __name__ == "__main__":
    unittest.main()
