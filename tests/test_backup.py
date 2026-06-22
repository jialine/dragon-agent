"""
Tests for dragon.backup module — BackupManager, BackupManifest, helpers, and BackupConfig.

Covers: BackupConfig defaults, DragonBackup constructor, create/restore/list/cleanup
backups, scheduler, locks, and from_config factory.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from dragon.config import BackupConfig
from dragon.backup import (
    BackupError,
    BackupLockError,
    BackupUploadError,
    BackupRestoreError,
    BackupNotFoundError,
    BackupManifest,
    DragonBackup,
    _sha256_file,
    _safe_remove,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_backup_instance(tmpdir: str, **kwargs) -> DragonBackup:
    """Create a DragonBackup pointed at a temp data dir."""
    defaults = dict(
        data_dir=os.path.join(tmpdir, "dragon_data"),
        config_path=os.path.join(tmpdir, "config.yaml"),
    )
    defaults.update(kwargs)
    return DragonBackup(**defaults)


def _create_fake_tar_gz(path: str, content: bytes = b"fake backup data"):
    """Create a simple tar.gz file for testing."""
    with tarfile.open(path, "w:gz") as tar:
        # Add a manifest.json inside
        tmpdir = tempfile.mkdtemp()
        try:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            import json
            with open(manifest_path, "w") as f:
                json.dump({
                    "backup_id": "dragon_backup_20260520_120000",
                    "timestamp": "2026-05-20T12:00:00+00:00",
                    "size_bytes": len(content),
                    "checksum": "",
                    "collections": [],
                    "config_version": "1.0.0",
                }, f)
            # Also add some fake data
            data_path = os.path.join(tmpdir, "vectordb")
            os.makedirs(data_path, exist_ok=True)
            with open(os.path.join(data_path, "data.txt"), "w") as f:
                f.write(content.decode())
            tar.add(manifest_path, arcname="manifest.json")
            tar.add(data_path, arcname="vectordb")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── BackupConfig ───────────────────────────────────────────────────────

class TestBackupConfigDefaults:
    """Test BackupConfig dataclass defaults from dragon.config."""

    def test_default_endpoint(self):
        cfg = BackupConfig()
        assert cfg.endpoint == ""

    def test_default_bucket(self):
        cfg = BackupConfig()
        assert cfg.bucket == "dragon-backups"

    def test_default_prefix(self):
        cfg = BackupConfig()
        assert cfg.prefix == "dragon/backups/"

    def test_default_interval_hours(self):
        cfg = BackupConfig()
        assert cfg.interval_hours == 6

    def test_default_keep_last(self):
        cfg = BackupConfig()
        assert cfg.keep_last == 7

    def test_default_access_key_env(self):
        cfg = BackupConfig()
        assert cfg.access_key_env == "DRAGON_BACKUP_ACCESS_KEY"

    def test_default_secret_key_env(self):
        cfg = BackupConfig()
        assert cfg.secret_key_env == "DRAGON_BACKUP_SECRET_KEY"

    def test_custom_values(self):
        cfg = BackupConfig(
            endpoint="https://oss.example.com",
            access_key_env="MY_KEY_ENV",
            secret_key_env="MY_SECRET_ENV",
            bucket="my-bucket",
            prefix="my/backups/",
            interval_hours=12,
            keep_last=10,
        )
        assert cfg.endpoint == "https://oss.example.com"
        assert cfg.access_key_env == "MY_KEY_ENV"
        assert cfg.secret_key_env == "MY_SECRET_ENV"
        assert cfg.bucket == "my-bucket"
        assert cfg.prefix == "my/backups/"
        assert cfg.interval_hours == 12
        assert cfg.keep_last == 10


# ── BackupManifest ─────────────────────────────────────────────────────

class TestBackupManifest:
    """Test the BackupManifest dataclass."""

    def test_create_manifest(self):
        m = BackupManifest(
            backup_id="dragon_backup_20260520_120000",
            timestamp="2026-05-20T12:00:00+00:00",
            size_bytes=12345,
            checksum="abc123def456",
            collections=["default", "custom"],
        )
        assert m.backup_id == "dragon_backup_20260520_120000"
        assert m.timestamp == "2026-05-20T12:00:00+00:00"
        assert m.size_bytes == 12345
        assert m.checksum == "abc123def456"
        assert m.collections == ["default", "custom"]
        assert m.config_version == "1.0.0"

    def test_to_dict(self):
        m = BackupManifest(
            backup_id="test_id",
            timestamp="2026-01-01T00:00:00+00:00",
            size_bytes=100,
            checksum="checksum123",
            collections=["col1"],
        )
        d = m.to_dict()
        assert d["backup_id"] == "test_id"
        assert d["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert d["size_bytes"] == 100
        assert d["checksum"] == "checksum123"
        assert d["collections"] == ["col1"]
        assert d["config_version"] == "1.0.0"

    def test_from_dict(self):
        d = {
            "backup_id": "from_dict_id",
            "timestamp": "2026-06-01T12:00:00+00:00",
            "size_bytes": 999,
            "checksum": "sha123",
            "collections": ["a", "b"],
            "config_version": "2.0.0",
        }
        m = BackupManifest.from_dict(d)
        assert m.backup_id == "from_dict_id"
        assert m.timestamp == "2026-06-01T12:00:00+00:00"
        assert m.size_bytes == 999
        assert m.checksum == "sha123"
        assert m.collections == ["a", "b"]
        assert m.config_version == "2.0.0"

    def test_from_dict_partial(self):
        """from_dict should handle missing keys gracefully."""
        d = {"backup_id": "partial", "timestamp": "now"}
        m = BackupManifest.from_dict(d)
        assert m.backup_id == "partial"
        assert m.timestamp == "now"
        assert m.size_bytes == 0  # default
        assert m.checksum == ""  # default
        assert m.collections == []  # default


# ── Helper Functions ───────────────────────────────────────────────────

class TestHelperFunctions:
    """Test _sha256_file and _safe_remove."""

    def test_sha256_file(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "test.txt")
            with open(path, "w") as f:
                f.write("hello world")
            result = _sha256_file(path)
            # Known SHA256 of "hello world"
            import hashlib
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert result == expected
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_sha256_file_empty(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "empty.txt")
            with open(path, "w") as f:
                f.write("")
            result = _sha256_file(path)
            import hashlib
            expected = hashlib.sha256(b"").hexdigest()
            assert result == expected
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_safe_remove_file(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "to_remove.txt")
            with open(path, "w") as f:
                f.write("data")
            assert os.path.exists(path)
            _safe_remove(path)
            assert not os.path.exists(path)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_safe_remove_directory(self):
        tmpdir = tempfile.mkdtemp()
        try:
            subdir = os.path.join(tmpdir, "subdir")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "file.txt"), "w") as f:
                f.write("data")
            assert os.path.isdir(subdir)
            _safe_remove(subdir)
            assert not os.path.exists(subdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_safe_remove_nonexistent(self):
        """Should not raise on nonexistent path."""
        _safe_remove("/nonexistent/path/12345")

    def test_safe_remove_empty_string(self):
        """Should not raise on empty string."""
        _safe_remove("")


# ── DragonBackup Constructor ────────────────────────────────────────────

class TestDragonBackupConstructor:
    """Test DragonBackup initialization and attributes."""

    def test_default_constructor(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(tmpdir)
            assert backup.endpoint == ""
            assert backup.access_key == ""
            assert backup.secret_key == ""
            assert backup.bucket == "dragon-backups"
            assert backup.prefix == "dragon/backups/"
            assert backup.interval_hours == 6
            assert backup.keep_last == 7
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_custom_constructor(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(
                tmpdir,
                endpoint="https://s3.custom.com",
                access_key="ak",
                secret_key="sk",
                bucket="my-bucket",
                prefix="my/prefix",
                interval_hours=24,
                keep_last=5,
            )
            assert backup.endpoint == "https://s3.custom.com"
            assert backup.access_key == "ak"
            assert backup.secret_key == "sk"
            assert backup.bucket == "my-bucket"
            assert backup.prefix == "my/prefix/"
            assert backup.interval_hours == 24
            assert backup.keep_last == 5
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_prefix_strips_trailing_slash(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(tmpdir, prefix="my/prefix/")
            assert backup.prefix == "my/prefix/"

            backup2 = _make_backup_instance(tmpdir, prefix="no-slash")
            assert backup2.prefix == "no-slash/"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_data_dir_is_path(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(tmpdir)
            assert isinstance(backup.data_dir, Path)
            assert backup.data_dir.name == "dragon_data"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lock_path_set(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(tmpdir)
            assert backup._lock_path.name == "backup.lock"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_scheduler_not_started_initially(self):
        tmpdir = tempfile.mkdtemp()
        try:
            backup = _make_backup_instance(tmpdir)
            assert backup._scheduler_thread is None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Backup / Restore / List ────────────────────────────────────────────

class TestBackupOperations:
    """Test create, list, restore, cleanup with mocked S3 client."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.backup = _make_backup_instance(self.tmpdir)
        # Create fake data directory
        os.makedirs(self.backup.data_dir / "vectordb", exist_ok=True)
        with open(self.backup.data_dir / "vectordb" / "data.txt", "w") as f:
            f.write("fake chroma data")
        # Create config
        with open(os.path.join(self.tmpdir, "config.yaml"), "w") as f:
            f.write("server:\n  port: 8000\n")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_create_backup_creates_tar_gz_and_uploads(self, mock_client_prop):
        """backup() creates archive, computes checksum, uploads to S3."""
        mock_client = MagicMock()
        mock_client_prop.return_value = mock_client

        manifest = self.backup.backup()

        assert manifest is not None
        assert manifest.backup_id.startswith("dragon_backup_")
        assert manifest.size_bytes > 0
        assert manifest.checksum != ""
        assert len(manifest.checksum) == 64  # SHA256 hex

        # Verify upload was called
        mock_client.upload_file.assert_called_once()
        call_args = mock_client.upload_file.call_args
        assert call_args[0][1] == "dragon-backups"  # bucket
        assert call_args[0][2].startswith("dragon/backups/dragon_backup_")
        assert call_args[0][2].endswith(".tar.gz")

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_list_backups_returns_list(self, mock_client_prop):
        """list_backups returns BackupManifest list from S3 listing."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "dragon/backups/dragon_backup_20260520_120000.tar.gz",
                    "Size": 1024,
                },
                {
                    "Key": "dragon/backups/dragon_backup_20260519_110000.tar.gz",
                    "Size": 2048,
                },
            ]
        }
        mock_client_prop.return_value = mock_client

        backups = self.backup.list_backups()

        assert len(backups) == 2
        assert backups[0].backup_id == "dragon_backup_20260520_120000"
        assert backups[1].backup_id == "dragon_backup_20260519_110000"
        # Newest first
        assert backups[0].timestamp > backups[1].timestamp

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_list_backups_empty(self, mock_client_prop):
        """list_backups returns empty list when no backups exist."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {}
        mock_client_prop.return_value = mock_client

        backups = self.backup.list_backups()
        assert backups == []

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_list_backups_handles_error(self, mock_client_prop):
        """list_backups returns empty list on exception."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.side_effect = Exception("Network error")
        mock_client_prop.return_value = mock_client

        backups = self.backup.list_backups()
        assert backups == []

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_restore_backup(self, mock_client_prop):
        """restore() downloads, verifies, and extracts backup."""
        mock_client = MagicMock()
        mock_client_prop.return_value = mock_client

        # Create a fake tar.gz to serve as the downloaded backup
        archive_path = os.path.join(self.tmpdir, "backup.tar.gz")
        _create_fake_tar_gz(archive_path)

        # Mock download_file to copy our fake archive
        def fake_download(bucket, key, dest):
            shutil.copy2(archive_path, dest)

        mock_client.download_file.side_effect = fake_download

        # Mock list_backups to return a known backup
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "dragon/backups/dragon_backup_20260520_120000.tar.gz",
                    "Size": os.path.getsize(archive_path),
                }
            ]
        }

        manifest = self.backup.restore()

        assert manifest is not None
        assert manifest.backup_id == "dragon_backup_20260520_120000"

        # Verify restoration directory exists
        restored_dir = self.backup.data_dir / "restored"
        assert restored_dir.exists()
        assert (restored_dir / "vectordb").exists()

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_restore_specific_backup_id(self, mock_client_prop):
        """restore(backup_id='specific') downloads that specific backup."""
        mock_client = MagicMock()
        mock_client_prop.return_value = mock_client

        archive_path = os.path.join(self.tmpdir, "backup.tar.gz")
        _create_fake_tar_gz(archive_path)

        def fake_download(bucket, key, dest):
            assert "dragon_backup_20260519_110000" in key
            shutil.copy2(archive_path, dest)

        mock_client.download_file.side_effect = fake_download

        manifest = self.backup.restore(backup_id="dragon_backup_20260519_110000")
        assert manifest is not None

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_restore_no_backups_raises(self, mock_client_prop):
        """restore() raises BackupNotFoundError when no backups exist."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {}
        mock_client_prop.return_value = mock_client

        with pytest.raises(BackupNotFoundError, match="No backups found"):
            self.backup.restore()

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_delete_backup(self, mock_client_prop):
        """delete_backup deletes from S3."""
        mock_client = MagicMock()
        mock_client_prop.return_value = mock_client

        result = self.backup.delete_backup("dragon_backup_20260520_120000")
        assert result is True
        mock_client.delete_object.assert_called_once_with(
            Bucket="dragon-backups",
            Key="dragon/backups/dragon_backup_20260520_120000.tar.gz",
        )

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_delete_backup_error(self, mock_client_prop):
        """delete_backup returns False on error."""
        mock_client = MagicMock()
        mock_client.delete_object.side_effect = Exception("Delete failed")
        mock_client_prop.return_value = mock_client

        result = self.backup.delete_backup("some_id")
        assert result is False

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_cleanup_old_backups(self, mock_client_prop):
        """_cleanup_old removes backups beyond keep_last."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": f"dragon/backups/dragon_backup_20260520_12000{i}.tar.gz", "Size": 100}
                for i in range(10)  # 10 backups, keep_last=7 → delete 3 oldest
            ]
        }
        mock_client_prop.return_value = mock_client

        # We need to mock the full cleanup flow
        # list_backups will sort by timestamp (newest first)
        # keep_last=7 means we keep 0-6, delete 7-9
        self.backup.keep_last = 7
        self.backup._cleanup_old()

        # Should have called delete_object 3 times (for the 3 oldest)
        assert mock_client.delete_object.call_count == 3

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_get_latest_backup(self, mock_client_prop):
        """get_latest_backup returns the most recent backup."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "dragon/backups/dragon_backup_20260520_120000.tar.gz", "Size": 100},
                {"Key": "dragon/backups/dragon_backup_20260519_110000.tar.gz", "Size": 200},
            ]
        }
        mock_client_prop.return_value = mock_client

        latest = self.backup.get_latest_backup()
        assert latest is not None
        assert latest.backup_id == "dragon_backup_20260520_120000"

    @patch("dragon.backup.DragonBackup.client", new_callable=PropertyMock)
    def test_get_latest_backup_empty(self, mock_client_prop):
        """get_latest_backup returns None when no backups."""
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {}
        mock_client_prop.return_value = mock_client

        latest = self.backup.get_latest_backup()
        assert latest is None


# ── Lock Tests ─────────────────────────────────────────────────────────

class TestBackupLock:
    """Test file locking mechanism."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.backup = _make_backup_instance(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_lock_succeeds(self):
        assert self.backup._acquire_lock() is True
        self.backup._release_lock()

    def test_acquire_lock_creates_lock_file(self):
        self.backup._acquire_lock()
        assert self.backup._lock_path.exists()
        self.backup._release_lock()

    def test_release_lock(self):
        self.backup._acquire_lock()
        self.backup._release_lock()
        # Lock file should exist but be unlocked
        assert self.backup._lock_path.exists()

    def test_double_acquire_fails(self):
        """Second acquire on same instance should fail."""
        assert self.backup._acquire_lock() is True
        # Second acquire on same lock should fail
        assert self.backup._acquire_lock() is False
        self.backup._release_lock()

    def test_backup_raises_lock_error_if_locked(self):
        """backup() raises BackupLockError if lock is already held."""
        # Acquire lock first
        self.backup._acquire_lock()
        try:
            with pytest.raises(BackupLockError, match="Another backup is in progress"):
                self.backup.backup()
        finally:
            self.backup._release_lock()


# ── Scheduler Tests ────────────────────────────────────────────────────

class TestBackupScheduler:
    """Test start_scheduler, stop_scheduler."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.backup = _make_backup_instance(
            self.tmpdir,
            interval_hours=1,  # Short interval for testing
        )

    def teardown_method(self):
        self.backup.stop_scheduler(timeout=1.0)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("dragon.backup.DragonBackup.backup")
    def test_start_scheduler_starts_thread(self, mock_backup):
        """start_scheduler creates and starts a background thread."""
        # Make backup do nothing
        mock_backup.return_value = BackupManifest(
            backup_id="test", timestamp="", size_bytes=0, checksum="", collections=[]
        )

        self.backup.start_scheduler()

        assert self.backup._scheduler_thread is not None
        assert self.backup._scheduler_thread.is_alive()

        # Stop it
        self.backup.stop_scheduler(timeout=2.0)
        assert not self.backup._scheduler_thread.is_alive()

    def test_start_scheduler_when_already_running(self):
        """Calling start_scheduler twice should be a no-op."""
        self.backup._scheduler_thread = threading.Thread(target=lambda: None)
        self.backup._scheduler_thread.start()
        try:
            # Should not raise or double-start
            self.backup.start_scheduler()
        finally:
            self.backup.stop_scheduler(timeout=1.0)

    def test_stop_scheduler_when_not_started(self):
        """stop_scheduler should not raise when no scheduler is running."""
        self.backup.stop_scheduler()  # Should be a no-op


# ── from_config Factory ────────────────────────────────────────────────

class TestFromConfig:
    """Test DragonBackup.from_config factory method."""

    def test_from_config(self):
        cfg = BackupConfig(
            endpoint="https://oss.example.com",
            access_key_env="MY_AK_ENV",
            secret_key_env="MY_SK_ENV",
            bucket="my-bucket",
            prefix="my/prefix",
            interval_hours=12,
            keep_last=10,
        )
        # Set env vars for the factory to read
        with patch.dict(os.environ, {
            "MY_AK_ENV": "test-access-key",
            "MY_SK_ENV": "test-secret-key",
        }):
            from dragon.config import DragonConfig
            # Create a minimal DragonConfig with our BackupConfig
            dragon_cfg = DragonConfig(backup=cfg)
            backup = DragonBackup.from_config(dragon_cfg)

        assert backup.endpoint == "https://oss.example.com"
        assert backup.access_key == "test-access-key"
        assert backup.secret_key == "test-secret-key"
        assert backup.bucket == "my-bucket"
        assert backup.prefix == "my/prefix/"
        assert backup.interval_hours == 12
        assert backup.keep_last == 10

    def test_from_config_missing_env_vars(self):
        """Factory uses empty string when env var is not set."""
        cfg = BackupConfig(
            access_key_env="NONEXISTENT_ENV_VAR",
            secret_key_env="ALSO_NONEXISTENT",
        )
        with patch.dict(os.environ, {}, clear=True):
            from dragon.config import DragonConfig
            dragon_cfg = DragonConfig(backup=cfg)
            backup = DragonBackup.from_config(dragon_cfg)

        assert backup.access_key == ""
        assert backup.secret_key == ""


# ── Exception Hierarchy ────────────────────────────────────────────────

class TestBackupExceptions:
    """Test exception hierarchy."""

    def test_backup_error_is_exception(self):
        assert issubclass(BackupError, Exception)

    def test_lock_error_is_backup_error(self):
        assert issubclass(BackupLockError, BackupError)

    def test_upload_error_is_backup_error(self):
        assert issubclass(BackupUploadError, BackupError)

    def test_restore_error_is_backup_error(self):
        assert issubclass(BackupRestoreError, BackupError)

    def test_not_found_error_is_backup_error(self):
        assert issubclass(BackupNotFoundError, BackupError)
