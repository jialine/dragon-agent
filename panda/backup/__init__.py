"""
Panda Agent Backup Module — full backup + restore.

Supports: AWS S3, Alibaba OSS, MinIO (via boto3 endpoint_url).
"""

import fcntl
import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Callable

logger = logging.getLogger("panda.backup")


# ── Exceptions ─────────────────────────────────────────────────

class BackupError(Exception):
    """Base exception for backup operations."""

class BackupLockError(BackupError):
    """Cannot acquire backup lock — another backup in progress."""

class BackupUploadError(BackupError):
    """S3/OSS upload failed after retries."""

class BackupRestoreError(BackupError):
    """Restore failed — checksum mismatch or extraction error."""

class BackupNotFoundError(BackupError):
    """No backup found to restore."""


# ── Data types ─────────────────────────────────────────────────

@dataclass
class BackupManifest:
    backup_id: str
    timestamp: str           # ISO 8601
    size_bytes: int = 0
    checksum: str = ""       # SHA256 hex
    collections: List[str] = field(default_factory=list)  # ChromaDB collections backed up
    config_version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "backup_id": self.backup_id,
            "timestamp": self.timestamp,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "collections": self.collections,
            "config_version": self.config_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BackupManifest":
        fields = ["backup_id", "timestamp", "size_bytes", "checksum", "collections", "config_version"]
        kwargs = {k: d[k] for k in fields if k in d}
        return cls(**kwargs)


# ── Helpers ────────────────────────────────────────────────────

def _sha256_file(path: str) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _safe_remove(path: str):
    """Remove file or directory, ignoring errors."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.unlink(path)
    except Exception:
        pass


# ── PandaBackup ────────────────────────────────────────────────

class PandaBackup:
    """
    Cloud backup + restore for Panda Agent data.
    
    Backs up:
      - ChromaDB (panda_data/vectordb/)
      - Memory graph (panda_data/memory/graph.json)
      - Config (config.yaml)
      - Manifest (backup metadata)
    
    Supports:
      - AWS S3, Alibaba OSS, MinIO (via boto3 endpoint_url)
      - Scheduled backups (background thread)
      - Restore to panda_data/restored/ (never overwrites live data)
    """

    def __init__(
        self,
        endpoint: str = "",
        access_key: str = "",
        secret_key: str = "",
        bucket: str = "panda-backups",
        prefix: str = "panda/backups/",
        interval_hours: int = 6,
        keep_last: int = 7,
        data_dir: str = "panda_data",
        config_path: str = "config.yaml",
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.interval_hours = interval_hours
        self.keep_last = keep_last
        self.data_dir = Path(data_dir)
        self.config_path = Path(config_path)
        
        self._lock_path = self.data_dir / "backup.lock"
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()
        
        # Lazy boto3 client
        self._client = None

    @property
    def client(self):
        """Lazy boto3 S3 client."""
        if self._client is None:
            import boto3
            kwargs = {}
            if self.endpoint:
                kwargs["endpoint_url"] = self.endpoint
            if self.access_key:
                kwargs["aws_access_key_id"] = self.access_key
            if self.secret_key:
                kwargs["aws_secret_access_key"] = self.secret_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    # ── Lock ────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Try to acquire file lock. Returns True on success."""
        # If we already hold the lock, refuse
        if hasattr(self, '_lock_fd') and self._lock_fd is not None:
            return False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fd = open(self._lock_path, "w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (IOError, OSError):
            self._lock_fd.close()
            self._lock_fd = None
            return False

    def _release_lock(self):
        """Release file lock."""
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
        except Exception:
            pass
        finally:
            self._lock_fd = None

    # ── Backup ───────────────────────────────────────────────

    def backup(self) -> BackupManifest:
        """
        Full backup to cloud.
        
        Returns BackupManifest on success.
        Raises BackupLockError if another backup is running.
        Raises BackupUploadError if upload fails after retries.
        """
        if not self._acquire_lock():
            raise BackupLockError("Another backup is in progress")

        tmpdir = None
        tarpath = None
        try:
            # 1. Create temp working directory
            tmpdir = tempfile.mkdtemp(prefix="panda_backup_")
            backup_dir = os.path.join(tmpdir, "backup")
            os.makedirs(backup_dir, exist_ok=True)

            # 2. Copy ChromaDB
            vectordb_src = self.data_dir / "vectordb"
            if vectordb_src.exists():
                vectordb_dst = os.path.join(backup_dir, "vectordb")
                shutil.copytree(vectordb_src, vectordb_dst)
                collections = self._detect_collections(vectordb_src)
            else:
                collections = []
                logger.warning("No vectordb found to backup")

            # 3. Copy memory graph
            graph_src = self.data_dir / "memory" / "graph.json"
            if graph_src.exists():
                graph_dst = os.path.join(backup_dir, "memory")
                os.makedirs(graph_dst, exist_ok=True)
                shutil.copy2(graph_src, graph_dst)

            # 4. Copy config
            if self.config_path.exists():
                shutil.copy2(self.config_path, os.path.join(backup_dir, "config.yaml"))

            # 5. Generate manifest
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_id = f"panda_backup_{timestamp}"
            manifest = BackupManifest(
                backup_id=backup_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                size_bytes=0,
                checksum="",
                collections=collections,
            )
            with open(os.path.join(backup_dir, "manifest.json"), "w") as f:
                json.dump(manifest.to_dict(), f, indent=2)

            # 6. Create archive
            tarpath = os.path.join(tmpdir, f"{backup_id}.tar.gz")
            with tarfile.open(tarpath, "w:gz") as tar:
                tar.add(backup_dir, arcname="")

            # 7. Compute checksum
            manifest.size_bytes = os.path.getsize(tarpath)
            manifest.checksum = _sha256_file(tarpath)

            # Update manifest in archive
            with open(os.path.join(backup_dir, "manifest.json"), "w") as f:
                json.dump(manifest.to_dict(), f, indent=2)
            os.unlink(tarpath)
            with tarfile.open(tarpath, "w:gz") as tar:
                tar.add(backup_dir, arcname="")

            # 8. Upload with retry
            key = f"{self.prefix}{backup_id}.tar.gz"
            self._upload_with_retry(tarpath, key)

            # 9. Cleanup old backups
            self._cleanup_old()

            logger.info("Backup complete: %s (%d bytes)", backup_id, manifest.size_bytes)
            return manifest

        finally:
            _safe_remove(tmpdir or "")
            self._release_lock()

    def _upload_with_retry(self, filepath: str, key: str, max_retries: int = 3):
        """Upload to S3 with exponential backoff."""
        import time as _time
        for attempt in range(max_retries):
            try:
                self.client.upload_file(filepath, self.bucket, key)
                return
            except Exception as e:
                if attempt == max_retries - 1:
                    raise BackupUploadError(f"Upload failed after {max_retries} attempts: {e}")
                wait = 2 ** attempt
                logger.warning("Upload attempt %d failed: %s. Retrying in %ds...", attempt + 1, e, wait)
                _time.sleep(wait)

    def _detect_collections(self, vectordb_dir: Path) -> List[str]:
        """Detect ChromaDB collection names from directory structure."""
        collections = []
        try:
            import sqlite3
            db_path = vectordb_dir / "chroma.sqlite3"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                rows = conn.execute("SELECT name FROM collections").fetchall()
                collections = [r[0] for r in rows]
                conn.close()
        except Exception:
            pass
        return collections

    def _cleanup_old(self):
        """Delete backups beyond keep_last limit."""
        try:
            backups = self.list_backups()
            for b in backups[self.keep_last:]:
                self.delete_backup(b.backup_id)
        except Exception as e:
            logger.warning("Cleanup old backups failed: %s", e)

    # ── Restore ──────────────────────────────────────────────

    def restore(self, backup_id: Optional[str] = None) -> BackupManifest:
        """
        Restore from cloud backup.
        
        Args:
            backup_id: Specific backup to restore. None = latest.
        
        Returns:
            BackupManifest of the restored backup.
        
        Raises:
            BackupNotFoundError: No backup found.
            BackupRestoreError: Checksum mismatch or extraction failure.
        
        IMPORTANT: Restores to panda_data/restored/ — NEVER overwrites live data.
        """
        # 1. Find backup
        if backup_id:
            key = f"{self.prefix}{backup_id}.tar.gz"
        else:
            backups = self.list_backups()
            if not backups:
                raise BackupNotFoundError("No backups found in bucket")
            key = f"{self.prefix}{backups[0].backup_id}.tar.gz"

        tmpdir = tempfile.mkdtemp(prefix="panda_restore_")
        tarpath = os.path.join(tmpdir, "backup.tar.gz")
        extract_dir = os.path.join(tmpdir, "extracted")

        try:
            # 2. Download
            logger.info("Downloading: s3://%s/%s", self.bucket, key)
            self.client.download_file(self.bucket, key, tarpath)

            # 3. Verify checksum (from manifest inside archive)
            with tarfile.open(tarpath, "r:gz") as tar:
                tar.extractall(extract_dir)

            manifest_path = os.path.join(extract_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                raise BackupRestoreError("Archive missing manifest.json")

            with open(manifest_path) as f:
                manifest = BackupManifest.from_dict(json.load(f))

            expected_checksum = manifest.checksum
            actual_checksum = _sha256_file(tarpath)
            if expected_checksum and actual_checksum != expected_checksum:
                raise BackupRestoreError(
                    f"Checksum mismatch: expected {expected_checksum[:16]}..., got {actual_checksum[:16]}..."
                )
            logger.info("Checksum verified: %s...", actual_checksum[:16])

            # 4. Extract to restored directory (NEVER overwrite live data)
            restore_dir = self.data_dir / "restored"
            # Rotate: if restored exists, rename to restored.old
            if restore_dir.exists():
                old_dir = self.data_dir / "restored.old"
                _safe_remove(str(old_dir))
                shutil.move(str(restore_dir), str(old_dir))
            
            restore_dir.mkdir(parents=True, exist_ok=True)

            for item in os.listdir(extract_dir):
                src = os.path.join(extract_dir, item)
                dst = os.path.join(str(restore_dir), item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            logger.info(
                "Restore complete: %s → %s\n"
                "  WARNING: Data extracted to panda_data/restored/ — NOT live.\n"
                "  To activate: stop Panda, move panda_data/restored/* → panda_data/, restart.",
                manifest.backup_id, restore_dir
            )

            return manifest

        except (BackupNotFoundError, BackupRestoreError):
            raise
        except Exception as e:
            raise BackupRestoreError(f"Restore failed: {e}") from e
        finally:
            _safe_remove(tmpdir)

    # ── List / Delete ────────────────────────────────────────

    def list_backups(self) -> List[BackupManifest]:
        """List all backups, newest first."""
        try:
            resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix)
            contents = resp.get("Contents", [])
            
            backups = []
            for obj in contents:
                key = obj["Key"]
                if not key.endswith(".tar.gz"):
                    continue
                # Extract backup_id from key: panda/backups/panda_backup_20260519_120000.tar.gz
                filename = key.rsplit("/", 1)[-1]
                backup_id = filename.replace(".tar.gz", "")
                timestamp_str = backup_id.replace("panda_backup_", "")
                try:
                    ts = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                except ValueError:
                    ts = datetime.min
                
                backups.append(BackupManifest(
                    backup_id=backup_id,
                    timestamp=ts.isoformat(),
                    size_bytes=obj.get("Size", 0),
                    checksum="",  # would need HEAD request to get
                    collections=[],  # would need to download manifest
                ))
            
            backups.sort(key=lambda b: b.timestamp, reverse=True)
            return backups
            
        except Exception as e:
            logger.error("List backups failed: %s", e)
            return []

    def delete_backup(self, backup_id: str) -> bool:
        """Delete a backup from cloud storage."""
        key = f"{self.prefix}{backup_id}.tar.gz"
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("Deleted: s3://%s/%s", self.bucket, key)
            return True
        except Exception as e:
            logger.error("Delete backup failed: %s", e)
            return False

    def get_latest_backup(self) -> Optional[BackupManifest]:
        """Get the most recent backup."""
        backups = self.list_backups()
        return backups[0] if backups else None

    # ── Scheduler ────────────────────────────────────────────

    def start_scheduler(self):
        """Start periodic backup in a background thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            logger.warning("Scheduler already running")
            return

        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="panda-backup-scheduler",
        )
        self._scheduler_thread.start()
        
        interval_secs = self.interval_hours * 3600
        logger.info("Backup scheduler started (every %.1f hours)", self.interval_hours)

    def stop_scheduler(self, timeout: float = 5.0):
        """Stop the backup scheduler."""
        if not self._scheduler_thread:
            return
        
        self._scheduler_stop.set()
        self._scheduler_thread.join(timeout=timeout)
        
        if self._scheduler_thread.is_alive():
            logger.warning("Scheduler thread did not stop within timeout")
        else:
            logger.info("Backup scheduler stopped")

    def _scheduler_loop(self):
        """Background backup loop."""
        interval_secs = self.interval_hours * 3600
        while not self._scheduler_stop.is_set():
            # Wait for interval or stop signal
            self._scheduler_stop.wait(timeout=interval_secs)
            if self._scheduler_stop.is_set():
                break
            
            try:
                self.backup()
            except BackupLockError:
                logger.debug("Skipping scheduled backup — lock held")
            except Exception as e:
                logger.error("Scheduled backup failed: %s", e)

    # ── Convenience factory ──────────────────────────────────

    @classmethod
    def from_config(cls, config) -> "PandaBackup":
        """Create PandaBackup from PandaConfig object."""
        import os as _os
        return cls(
            endpoint=config.backup.endpoint,
            access_key=_os.environ.get(config.backup.access_key_env, ""),
            secret_key=_os.environ.get(config.backup.secret_key_env, ""),
            bucket=config.backup.bucket,
            prefix=config.backup.prefix,
            interval_hours=config.backup.interval_hours,
            keep_last=config.backup.keep_last,
        )
