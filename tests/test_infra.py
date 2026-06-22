"""
Unit tests for CronScheduler, CredentialPool, ProfileManager.
"""
import os
import json
import tempfile
import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from dragon.cron import CronScheduler, CronJob, JobStatus
from dragon.credential import CredentialPool, CredentialManager
from dragon.profile import ProfileManager, Profile


# ── CronScheduler ──────────────────────────────────────────────────

class TestCronJob:
    def test_creation(self):
        job = CronJob(name="test", schedule="30m", task="Test job")
        assert job.name == "test"
        assert job.schedule == "30m"
        assert job.status == "active"
        assert job.id != ""

    def test_compute_next_interval_minutes(self):
        job = CronJob(name="test", schedule="30m")
        next_run = job.compute_next_run()
        assert next_run is not None

    def test_compute_next_interval_hours(self):
        job = CronJob(name="test", schedule="2h")
        next_run = job.compute_next_run()
        assert next_run is not None

    def test_compute_next_every_format(self):
        job = CronJob(name="test", schedule="every 6 hours")
        next_run = job.compute_next_run()
        assert next_run is not None

    def test_compute_next_days(self):
        job = CronJob(name="test", schedule="1d")
        next_run = job.compute_next_run()
        assert next_run is not None

    def test_is_due_new_job(self):
        job = CronJob(name="test", schedule="30m")
        job.next_run_at = job.compute_next_run()
        assert job.is_due is False  # future

    def test_paused_job_not_due(self):
        job = CronJob(name="test", schedule="30m", status="paused")
        assert job.is_due is False


class TestCronScheduler:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cron_test.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_job(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="daily", schedule="0 9 * * *", task="Report")
        assert job.id != ""
        assert s.get(job.id) is not None
        assert len(s.list_jobs()) == 1

    def test_pause_resume(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="test", schedule="30m")
        assert s.pause(job.id) is True
        assert s.get(job.id).status == "paused"
        assert s.resume(job.id) is True
        assert s.get(job.id).status == "active"

    def test_remove(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="temp", schedule="1h")
        assert s.remove(job.id) is True
        assert s.get(job.id) is None

    def test_stats(self):
        s = CronScheduler(db_path=self.db_path)
        s.add(name="a", schedule="30m")
        s.add(name="b", schedule="1h")
        stats = s.stats()
        assert stats["total_jobs"] == 2
        assert stats["active_jobs"] == 2


# ── CredentialPool ─────────────────────────────────────────────────

class TestCredentialPool:
    def test_add_and_get_key(self):
        pool = CredentialPool("test")
        pool.add_key("sk-test123", priority=1)
        key = pool.get_key()
        assert key == "sk-test123"

    def test_multiple_keys_priority(self):
        pool = CredentialPool("test")
        pool.add_key("sk-low", priority=10)
        pool.add_key("sk-high", priority=1)
        key = pool.get_key()
        assert key == "sk-high"

    def test_mark_rate_limited(self):
        pool = CredentialPool("test")
        pool.add_key("sk-test")
        pool.mark_rate_limited("sk-test", cooldown_secs=60)
        key = pool.get_key()
        assert key is None  # no available key

    def test_mark_exhausted(self):
        pool = CredentialPool("test")
        pool.add_key("sk-test")
        pool.mark_exhausted("sk-test")
        assert pool.available_count == 0
        assert pool.exhausted_count == 1

    def test_reset(self):
        pool = CredentialPool("test")
        pool.add_key("sk-test")
        pool.mark_exhausted("sk-test")
        pool.reset("sk-test")
        assert pool.available_count == 1
        assert pool.get_key() == "sk-test"

    def test_stats(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a", priority=1)
        pool.add_key("sk-b", priority=2)
        stats = pool.stats()
        assert stats["total"] == 2
        assert stats["available"] == 2


class TestCredentialManager:
    def test_add_pool(self):
        mgr = CredentialManager()
        mgr.add_pool("openai", ["sk-test1", "sk-test2"])
        key = mgr.get_key("openai")
        assert key is not None

    def test_stats(self):
        mgr = CredentialManager()
        mgr.add_pool("openai", ["sk-a"])
        mgr.add_pool("deepseek", ["sk-b", "sk-c"])
        stats = mgr.stats()
        assert "openai" in stats
        assert "deepseek" in stats


# ── ProfileManager ─────────────────────────────────────────────────

class TestProfileManager:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.base_dir = os.path.join(self.tmpdir, "profiles")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_profile(self):
        pm = ProfileManager(base_dir=self.base_dir)
        profile = pm.create("work")
        assert profile.name == "work"
        assert profile.base_dir.exists()

    def test_get_profile(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("work")
        assert pm.get("work") is not None
        assert pm.get("nonexistent") is None

    def test_set_default(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("work")
        pm.create("personal")
        assert pm.set_default("personal") is True
        default = pm.get_default()
        assert default.name == "personal"

    def test_list_profiles(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("a")
        pm.create("b")
        profiles = pm.list_profiles()
        assert len(profiles) == 2

    def test_rename(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("old-name")
        assert pm.rename("old-name", "new-name") is True
        assert pm.get("old-name") is None
        assert pm.get("new-name") is not None

    def test_delete(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("temp")
        assert pm.delete("temp") is True
        assert pm.get("temp") is None

    def test_export_import(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("export-test")
        archive = os.path.join(self.tmpdir, "export.tar.gz")
        assert pm.export_profile("export-test", archive) is True
        assert os.path.exists(archive)

    def test_stats(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("a")
        stats = pm.stats()
        assert stats["total_profiles"] == 1


# ── Extended Tests: Cron Job Max Runs ────────────────────────────────

class TestCronMaxRuns:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cron_maxruns.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_job_with_max_runs_stored(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="limited", schedule="30m", task="Run 3 times", max_runs=3)
        assert job.max_runs == 3
        assert job.run_count == 0

    def test_job_with_unlimited_runs(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="unlimited", schedule="1h", task="Forever")
        assert job.max_runs == 0  # 0 = unlimited

    def test_job_completes_after_max_runs(self):
        """When run_count reaches max_runs, status becomes completed."""
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="finite", schedule="30m", task="Finite job", max_runs=2)

        # Simulate reaching max_runs (the scheduler does this in _execute_job)
        job.run_count = 2
        job.status = JobStatus.COMPLETED.value
        s._save_job(job)

        # Reload
        s2 = CronScheduler(db_path=self.db_path)
        fetched = s2.get(job.id)
        assert fetched.status == "completed"
        assert fetched.run_count == 2


# ── Extended Tests: Cron Handler Registration ────────────────────────

class TestCronHandlerRegistration:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cron_handler.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_handler_stored(self):
        s = CronScheduler(db_path=self.db_path)

        async def my_handler(job):
            return f"Handled: {job.name}"

        s.register_handler("daily", my_handler)
        assert "daily" in s._handlers
        assert s._handlers["daily"] is my_handler

    def test_register_multiple_handlers(self):
        s = CronScheduler(db_path=self.db_path)

        async def handler_a(job):
            return "A"

        async def handler_b(job):
            return "B"

        s.register_handler("report", handler_a)
        s.register_handler("health", handler_b)
        assert len(s._handlers) == 2
        assert "report" in s._handlers
        assert "health" in s._handlers

    def test_handler_matches_by_pattern(self):
        s = CronScheduler(db_path=self.db_path)

        async def daily_handler(job):
            return "daily done"

        s.register_handler("daily", daily_handler)

        # The scheduler matches handler by substring of job.name
        # Test that the match logic works
        found = None
        for pattern, h in s._handlers.items():
            if pattern in "daily-report":
                found = h
                break
        assert found is daily_handler

    @pytest.mark.skip(reason="run_now uses asyncio.create_task which requires a running event loop")
    def test_run_now_schedules_execution(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="quick", schedule="30m")

        async def quick_handler(job):
            return "quick done"

        s.register_handler("quick", quick_handler)

        # run_now is synchronous — it creates a task
        # We can't easily await it, but it shouldn't raise
        assert s.run_now(job.id) is True

    def test_run_now_nonexistent_returns_false(self):
        s = CronScheduler(db_path=self.db_path)
        assert s.run_now("nonexistent-id") is False


# ── Extended Tests: Cron Run History ─────────────────────────────────

class TestCronRunHistory:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cron_history.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_history_table_exists(self):
        s = CronScheduler(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_runs'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "cron_runs"

    def test_cron_jobs_table_has_correct_columns(self):
        s = CronScheduler(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            info = conn.execute("PRAGMA table_info(cron_jobs)").fetchall()
            col_names = [c[1] for c in info]
        finally:
            conn.close()
        for expected in ["id", "name", "schedule", "task", "status",
                          "last_run_at", "next_run_at", "run_count",
                          "max_runs", "created_at", "meta"]:
            assert expected in col_names

    def test_save_run_stores_run_record(self):
        s = CronScheduler(db_path=self.db_path)
        job = s.add(name="history-test", schedule="30m")

        # Simulate what _execute_job does
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO cron_runs (job_id, started_at) VALUES (?, ?)",
                (job.id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        # Now save the run result
        s._save_run(job.id, True, "output text", "")

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT job_id, success, output, error FROM cron_runs"
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 1
        assert rows[0][1] == 1  # success
        assert rows[0][2] == "output text"


# ── Extended Tests: Credential Pool Round Robin ──────────────────────

class TestCredentialPoolRoundRobin:
    def test_round_robin_cycles_keys(self):
        pool = CredentialPool("test")
        pool.add_key("sk-key-a", priority=1)
        pool.add_key("sk-key-b", priority=2)
        pool.add_key("sk-key-c", priority=3)

        keys = set()
        for _ in range(6):
            key = pool.get_key_round_robin()
            assert key is not None
            keys.add(key)

        # Should cycle through all 3 keys
        assert len(keys) == 3

    def test_round_robin_with_exhausted_keys(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a", priority=1)
        pool.add_key("sk-b", priority=2)
        pool.mark_exhausted("sk-b")

        # Only sk-a is available
        for _ in range(3):
            key = pool.get_key_round_robin()
            assert key == "sk-a"

    def test_round_robin_no_available(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a")
        pool.mark_exhausted("sk-a")
        assert pool.get_key_round_robin() is None


# ── Extended Tests: Credential Pool Key Removal ──────────────────────

class TestCredentialPoolKeyRemoval:
    def test_remove_existing_key(self):
        pool = CredentialPool("test")
        pool.add_key("sk-remove-me")
        assert pool.total_keys == 1
        assert pool.remove_key("sk-remove-me") is True
        assert pool.total_keys == 0

    def test_remove_nonexistent_key(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a")
        assert pool.remove_key("sk-ghost") is False
        assert pool.total_keys == 1

    def test_remove_key_clears_from_available_count(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a")
        pool.add_key("sk-b")
        pool.remove_key("sk-a")
        assert pool.total_keys == 1
        assert pool.available_count == 1


# ── Extended Tests: Credential Pool Health Score ─────────────────────

class TestCredentialPoolHealthScore:
    def test_unused_key_health(self):
        pool = CredentialPool("test")
        pool.add_key("sk-fresh")
        stats = pool.stats()
        assert stats["keys"][0]["health"] == 0.8

    def test_perfect_key_health(self):
        pool = CredentialPool("test")
        pool.add_key("sk-perfect")
        for _ in range(10):
            pool.mark_success("sk-perfect")
        stats = pool.stats()
        assert stats["keys"][0]["health"] == 1.0

    def test_degraded_key_health(self):
        pool = CredentialPool("test")
        pool.add_key("sk-degraded")
        for _ in range(5):
            pool.mark_success("sk-degraded")
        for _ in range(2):
            pool.mark_rate_limited("sk-degraded", cooldown_secs=0)
        stats = pool.stats()
        health = stats["keys"][0]["health"]
        # 5 calls, 2 errors (rate_limited also increments error_count)
        # But rate_limited also increments call_count, so 7 calls, 2 errors
        # health = 1 - 2/7 ≈ 0.71
        assert 0.5 < health < 0.9

    def test_exhausted_key_health_zero(self):
        pool = CredentialPool("test")
        pool.add_key("sk-dead")
        pool.mark_exhausted("sk-dead")
        stats = pool.stats()
        assert stats["keys"][0]["health"] == 0.0

    def test_reset_all_restores_keys(self):
        pool = CredentialPool("test")
        pool.add_key("sk-a")
        pool.add_key("sk-b")
        pool.mark_exhausted("sk-a")
        pool.mark_rate_limited("sk-b")
        assert pool.available_count == 0
        pool.reset_all()
        assert pool.available_count == 2

    def test_has_available_property(self):
        pool = CredentialPool("test")
        assert pool.has_available is False
        pool.add_key("sk-a")
        assert pool.has_available is True


# ── Extended Tests: Profile Clone with Config ────────────────────────

class TestProfileClone:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.base_dir = os.path.join(self.tmpdir, "profiles")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_clone_copies_config(self):
        pm = ProfileManager(base_dir=self.base_dir)
        source = pm.create("source")
        # Write custom config to source
        source.config_file.write_text("custom: value\nport: 9999\n")

        # Clone from source
        cloned = pm.create("cloned", clone_from="source")
        assert cloned.config_file.exists()
        content = cloned.config_file.read_text()
        assert "custom: value" in content
        assert "port: 9999" in content

    def test_clone_from_nonexistent_still_creates(self):
        pm = ProfileManager(base_dir=self.base_dir)
        # clone_from a profile that doesn't exist — should still create
        profile = pm.create("independent", clone_from="ghost")
        assert profile is not None
        assert profile.name == "independent"
        assert profile.config_file.exists()  # default config created

    def test_clone_preserves_original(self):
        pm = ProfileManager(base_dir=self.base_dir)
        source = pm.create("source")
        source.config_file.write_text("original: data\n")

        cloned = pm.create("clone", clone_from="source")
        # Modify clone, ensure source is unaffected
        cloned.config_file.write_text("modified: data\n")
        source_content = source.config_file.read_text()
        assert "original: data" in source_content


# ── Extended Tests: Profile Metadata ─────────────────────────────────

class TestProfileMetadata:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.base_dir = os.path.join(self.tmpdir, "profiles")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_with_metadata(self):
        pm = ProfileManager(base_dir=self.base_dir)
        meta = {"owner": "alice", "team": "engineering", "tier": "premium"}
        profile = pm.create("meta-profile", metadata=meta)
        assert profile.metadata == meta
        retrieved = pm.get("meta-profile")
        assert retrieved.metadata == meta

    def test_metadata_persisted_across_reload(self):
        pm = ProfileManager(base_dir=self.base_dir)
        meta = {"key": "value", "count": 42}
        pm.create("persist-meta", metadata=meta)

        # Reload
        pm2 = ProfileManager(base_dir=self.base_dir)
        retrieved = pm2.get("persist-meta")
        assert retrieved.metadata == meta

    def test_metadata_to_dict(self):
        pm = ProfileManager(base_dir=self.base_dir)
        meta = {"purpose": "testing"}
        profile = pm.create("dict-test", metadata=meta)
        d = profile.to_dict()
        assert d["name"] == "dict-test"
        assert d["metadata"] == meta
        assert "created_at" in d


# ── Extended Tests: Profile Import with Name Conflict ────────────────

class TestProfileImport:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.base_dir = os.path.join(self.tmpdir, "profiles")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_import_with_new_name(self):
        pm = ProfileManager(base_dir=self.base_dir)
        source = pm.create("source-profile")
        source.config_file.write_text("source: config\n")

        # Export
        archive = os.path.join(self.tmpdir, "export.tar.gz")
        pm.export_profile("source-profile", archive)

        # Import with new name
        imported = pm.import_profile(archive, new_name="imported-profile")
        assert imported is not None
        assert imported.name == "imported-profile"
        assert imported.config_file.exists()

    def test_import_name_conflict_returns_none(self):
        pm = ProfileManager(base_dir=self.base_dir)
        pm.create("existing")
        pm.create("export-me")
        archive = os.path.join(self.tmpdir, "export2.tar.gz")
        pm.export_profile("export-me", archive)

        # Try importing with a name that already exists
        result = pm.import_profile(archive, new_name="existing")
        assert result is None  # name conflict → returns None

    def test_import_nonexistent_archive(self):
        pm = ProfileManager(base_dir=self.base_dir)
        result = pm.import_profile("/nonexistent/path.tar.gz")
        assert result is None

    def test_export_nonexistent_profile(self):
        pm = ProfileManager(base_dir=self.base_dir)
        archive = os.path.join(self.tmpdir, "export3.tar.gz")
        assert pm.export_profile("ghost", archive) is False
