"""
CLI integration tests for Dragon Agent commands.

Tests the NEW subcommands: config init/validate, profile clone/export/import/rename,
sessions export/stats, gateway status, doctor, and top-level help.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest
import yaml

from dragon.cli import (
    _cmd_sessions_export,
    _cmd_sessions_stats,
)
from dragon.profile import ProfileManager
from dragon.session import SessionStore


# ── Helpers ────────────────────────────────────────────────────────────

# Project root so subprocess can find the dragon module
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def _run_dragon(*args, **kwargs):
    """Run dragon as a subprocess. Returns CompletedProcess."""
    env = os.environ.copy()
    # Prevent accidental config/env pollution
    env.pop("DRAGON_SERVER_PORT", None)
    env.pop("DRAGON_ROUTER_MODEL_PATH", None)
    env.pop("DRAGON_GENERAL_API_KEY", None)
    # Ensure dragon module is discoverable
    env["PYTHONPATH"] = _PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if "env" in kwargs:
        env.update(kwargs.pop("env"))
    return subprocess.run(
        [sys.executable, "-m", "dragon", *args],
        capture_output=True,
        text=True,
        env=env,
        **kwargs,
    )


# ───────────────────────────────────────────────────────────────────────
# Help Tests
# ───────────────────────────────────────────────────────────────────────

class TestHelpOutput:
    """Verify --help works on subcommands."""

    def test_config_init_help(self):
        """dragon config init --help shows usage."""
        result = _run_dragon("config", "init", "--help")
        # argparse returns 0 for --help
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "config" in result.stdout.lower()

    def test_config_validate_help(self):
        """dragon config validate --help shows usage."""
        result = _run_dragon("config", "validate", "--help")
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "config" in result.stdout.lower()

    def test_help_shows_all_commands(self):
        """dragon --help shows all 12+ commands."""
        result = _run_dragon("--help")
        assert result.returncode == 0
        stdout = result.stdout
        # All 12 top-level commands should appear
        expected_commands = [
            "chat", "serve", "gateway", "mcp", "config", "skills",
            "tools", "sessions", "cron", "profile", "test", "doctor",
        ]
        for cmd in expected_commands:
            assert cmd in stdout, f"Command '{cmd}' not found in --help output"


# ───────────────────────────────────────────────────────────────────────
# Config Validate Tests
# ───────────────────────────────────────────────────────────────────────

class TestConfigValidate:
    """Test dragon config validate with valid and invalid configs."""

    def test_validate_valid_config(self):
        """Validate a well-formed config.yaml — should exit 0."""
        tmpdir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(tmpdir, "config.yaml")
            valid_config = {
                "router": {
                    "model_path": "models/test.gguf",
                    "n_threads": 4,
                    "n_ctx": 512,
                    "temperature": 0.1,
                    "max_tokens": 128,
                    "fallback_on_failure": True,
                },
                "server": {
                    "host": "0.0.0.0",
                    "port": 8000,
                    "log_level": "info",
                },
                "memory": {
                    "persist_dir": "dragon_data/vectordb",
                    "embedding_model": "BAAI/bge-small-zh-v1.5",
                    "search_top_k": 5,
                    "search_threshold": 0.5,
                },
                "guard": {
                    "max_consecutive_repeats": 3,
                    "max_loop_rounds": 2,
                    "max_ineffective_retries": 3,
                    "task_timeout_secs": 300,
                },
            }
            with open(config_path, "w") as f:
                yaml.dump(valid_config, f)

            result = _run_dragon("config", "validate", cwd=tmpdir)
            assert result.returncode == 0, f"validate failed: {result.stderr}"
            assert "✓" in result.stdout or "correct" in result.stdout.lower()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_validate_invalid_config(self):
        """Validate a broken config.yaml — should report error in output."""
        tmpdir = tempfile.mkdtemp()
        try:
            # Write an invalid config (bad YAML structure: not a mapping)
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w") as f:
                f.write(": : : invalid yaml structure\n")

            result = _run_dragon("config", "validate", cwd=tmpdir)
            # CLI catches exceptions and prints error; check for error in output
            assert "配置验证失败" in result.stdout or "✗" in result.stdout, \
                f"Expected validation error in output, got: {result.stdout}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_validate_missing_fields(self):
        """Validate a config missing required sections."""
        tmpdir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(tmpdir, "config.yaml")
            # Missing 'router' section entirely
            partial_config = {
                "server": {
                    "host": "0.0.0.0",
                    "port": 8000,
                },
            }
            with open(config_path, "w") as f:
                yaml.dump(partial_config, f)

            # Should still be valid YAML but may warn about missing fields
            result = _run_dragon("config", "validate", cwd=tmpdir)
            # Pydantic with defaults should still load successfully
            # Exit 0 because it uses defaults for missing fields
            assert result.returncode == 0, f"validate should use defaults: {result.stderr}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_validate_broken_router_type(self):
        """Validate config with wrong type for router.n_threads — reports error."""
        tmpdir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(tmpdir, "config.yaml")
            invalid_config = {
                "router": {
                    "model_path": "models/test.gguf",
                    "n_threads": "not_an_integer",  # Should be int
                    "n_ctx": 512,
                },
                "server": {"host": "0.0.0.0", "port": 8000},
            }
            with open(config_path, "w") as f:
                yaml.dump(invalid_config, f)

            result = _run_dragon("config", "validate", cwd=tmpdir)
            # Pydantic validation error should appear in output
            assert "配置验证失败" in result.stdout or "validation error" in result.stdout, \
                f"Expected type validation error in output, got: {result.stdout}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────
# Profile Tests (direct API with temp dirs)
# ───────────────────────────────────────────────────────────────────────

class TestProfileCLI:
    """Test profile subcommands using ProfileManager with temp directories."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pm = ProfileManager(base_dir=self.tmpdir)
        # Create a source profile to work with
        self.pm.create("source")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_profile_clone(self):
        """Clone a profile and verify the clone exists."""
        self.pm.create("target", clone_from="source")
        target = self.pm.get("target")
        assert target is not None
        assert target.name == "target"
        # Verify it has its own directory
        assert target.base_dir.exists()

    def test_profile_export(self):
        """Export a profile to tar.gz and verify the file exists."""
        output_path = os.path.join(self.tmpdir, "source_export.tar.gz")
        success = self.pm.export_profile("source", output_path)
        assert success, "export_profile returned False"
        assert os.path.exists(output_path), f"Export file not created: {output_path}"
        assert os.path.getsize(output_path) > 0, "Export file is empty"

    def test_profile_import(self):
        """Export then re-import a profile under a new name."""
        # First export
        archive = os.path.join(self.tmpdir, "export.tar.gz")
        self.pm.export_profile("source", archive)

        # Create a fresh ProfileManager for import
        import_dir = tempfile.mkdtemp()
        try:
            pm2 = ProfileManager(base_dir=import_dir)
            imported = pm2.import_profile(archive, new_name="imported")
            assert imported is not None, "import_profile returned None"
            assert imported.name == "imported"
            assert pm2.get("imported") is not None
        finally:
            shutil.rmtree(import_dir, ignore_errors=True)

    def test_profile_rename(self):
        """Rename a profile and verify old is gone, new exists."""
        result = self.pm.rename("source", "renamed")
        assert result, "rename returned False"
        assert self.pm.get("source") is None, "Old profile should not exist"
        renamed = self.pm.get("renamed")
        assert renamed is not None, "New profile should exist"
        assert renamed.name == "renamed"

    def test_profile_list(self):
        """Profile list returns created profiles."""
        self.pm.create("profile-a")
        self.pm.create("profile-b")
        profiles = self.pm.list_profiles()
        names = [p.name for p in profiles]
        assert "source" in names
        assert "profile-a" in names
        assert "profile-b" in names

    def test_profile_create_default(self):
        """Creating a profile with set_default makes it the default."""
        self.pm.create("default-me", set_default=True)
        default = self.pm.get_default()
        assert default is not None
        assert default.name == "default-me"


# ───────────────────────────────────────────────────────────────────────
# Sessions Tests (direct API with temp DB)
# ───────────────────────────────────────────────────────────────────────

class TestSessionsCLI:
    """Test sessions export and stats via direct function calls."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "sessions.db")
        self.store = SessionStore(db_path=self.db_path)
        # Create a session with messages for export/stats
        self.session = self.store.create(title="Test Session", platform="cli")
        self.store.add_message(self.session.id, "user", "Hello world")
        self.store.add_message(self.session.id, "assistant", "Hi there!")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sessions_export(self):
        """Export a session to JSON and verify file contents."""
        output_path = os.path.join(self.tmpdir, "export.json")

        # Build argparse namespace
        args = argparse.Namespace(
            action="export",
            session_id=self.session.id,
            output=output_path,
            query=None,
            since=None,
            until=None,
        )
        _cmd_sessions_export(args, self.store)

        assert os.path.exists(output_path), f"Export file not created: {output_path}"
        with open(output_path) as f:
            data = json.load(f)

        assert "session" in data, "Export missing 'session' key"
        assert "messages" in data, "Export missing 'messages' key"
        assert data["session"]["id"] == self.session.id
        assert len(data["messages"]) == 2

    def test_sessions_export_nonexistent(self):
        """Export a nonexistent session should print error message."""
        output_path = os.path.join(self.tmpdir, "ghost.json")
        args = argparse.Namespace(
            action="export",
            session_id="nonexistent-id",
            output=output_path,
            query=None,
            since=None,
            until=None,
        )
        _cmd_sessions_export(args, self.store)
        # Should not create file for nonexistent session
        assert not os.path.exists(output_path), "Should not export nonexistent session"

    def test_sessions_stats(self):
        """Get session stats — should show totals."""
        args = argparse.Namespace(
            action="stats",
            session_id=None,
            output=None,
            query=None,
            since=None,
            until=None,
        )
        # Capture stdout to verify output
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            _cmd_sessions_stats(args, self.store)
        output = buf.getvalue()

        assert "总会话数" in output or "Session Statistics" in output
        # Should have at least 1 session
        assert "1" in output or "sessions" in output.lower()

    def test_sessions_stats_empty_store(self):
        """Stats on an empty store should return zeros."""
        empty_db = os.path.join(self.tmpdir, "empty.db")
        empty_store = SessionStore(db_path=empty_db)

        args = argparse.Namespace(
            action="stats",
            session_id=None,
            output=None,
            query=None,
            since=None,
            until=None,
        )
        st = empty_store.stats()
        assert st["sessions"] == 0
        assert st["messages"] == 0


# ───────────────────────────────────────────────────────────────────────
# Gateway Status Test
# ───────────────────────────────────────────────────────────────────────

class TestGatewayCLI:
    """Test gateway status command."""

    def test_gateway_status(self):
        """dragon gateway status runs without error."""
        result = _run_dragon("gateway", "status")
        assert result.returncode == 0, f"gateway status failed: {result.stderr}"
        assert "Dragon Gateway Status" in result.stdout
        # Should mention adapters
        assert "Feishu" in result.stdout or "feishu" in result.stdout.lower()


# ───────────────────────────────────────────────────────────────────────
# Doctor Tests
# ───────────────────────────────────────────────────────────────────────

class TestDoctorCLI:
    """Test doctor command."""

    def test_doctor_runs_and_reports_python_version(self):
        """dragon doctor runs and reports Python version."""
        result = _run_dragon("doctor")
        assert result.returncode == 0, f"doctor failed: {result.stderr}"
        # Should contain "Python" or "诊断报告"
        assert "Python" in result.stdout or "诊断" in result.stdout
        # Should report the Python version
        import platform
        py_ver = platform.python_version()
        assert py_ver in result.stdout, f"Expected Python version {py_ver} in output"

    def test_doctor_json_outputs_valid_json(self):
        """dragon doctor --json outputs valid JSON."""
        result = _run_dragon("doctor", "--json")
        assert result.returncode == 0, f"doctor --json failed: {result.stderr}"

        data = json.loads(result.stdout)
        assert isinstance(data, list), "JSON output should be a list"
        assert len(data) > 0, "JSON output should have at least one check"

        # Verify structure: each item has name, ok, detail, icon
        for item in data:
            assert "name" in item
            assert "ok" in item
            assert "detail" in item
            assert "icon" in item

        # Python version check should be first
        assert "Python" in data[0]["name"], f"First check should be Python, got: {data[0]['name']}"

    def test_doctor_json_contains_all_checks(self):
        """dragon doctor --json includes all diagnostic categories."""
        result = _run_dragon("doctor", "--json")
        data = json.loads(result.stdout)

        names = [item["name"] for item in data]
        # Should include Python, deps, config, model, API keys, data dir, OS
        assert any("Python" in n for n in names), "Missing Python version check"
        assert any("依赖" in n or "dep" in n.lower() for n in names), "Missing dependency check"
        assert any("配置" in n or "config" in n.lower() for n in names), "Missing config check"
        assert any("模型" in n or "model" in n.lower() for n in names), "Missing model check"
        assert any("API" in n or "api" in n.lower() for n in names), "Missing API key check"
        assert any("数据" in n or "data" in n.lower() for n in names), "Missing data dir check"
        assert any("操作" in n or "system" in n.lower() for n in names), "Missing OS check"


# ───────────────────────────────────────────────────────────────────────
# Help Tests for ALL subcommands
# ───────────────────────────────────────────────────────────────────────

class TestAllHelpOutput:
    """Verify --help works on every subcommand."""

    def test_chat_help(self):
        """dragon chat --help shows usage."""
        result = _run_dragon("chat", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "chat" in stdout_lower, f"Missing usage in: {result.stdout}"

    def test_serve_help(self):
        """dragon serve --help shows usage."""
        result = _run_dragon("serve", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "serve" in stdout_lower, f"Missing usage in: {result.stdout}"

    def test_mcp_help(self):
        """dragon mcp --help shows usage."""
        result = _run_dragon("mcp", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "mcp" in stdout_lower, f"Missing usage in: {result.stdout}"

    def test_skills_help(self):
        """dragon skills --help shows usage with list, search, create, delete."""
        result = _run_dragon("skills", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "skills" in stdout_lower
        # Should mention available actions
        assert "list" in stdout_lower, f"Expected 'list' in help: {result.stdout[:300]}"
        assert "search" in stdout_lower, f"Expected 'search' in help: {result.stdout[:300]}"

    def test_tools_help(self):
        """dragon tools --help shows usage with list, search, call."""
        result = _run_dragon("tools", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "tools" in stdout_lower
        assert "list" in stdout_lower, f"Expected 'list' in help: {result.stdout[:300]}"

    def test_cron_help(self):
        """dragon cron --help shows usage with list, add, pause, resume, remove, run."""
        result = _run_dragon("cron", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "cron" in stdout_lower
        # Check for key actions
        for action in ["list", "add", "remove", "run"]:
            assert action in stdout_lower, f"Expected '{action}' in cron help: {result.stdout[:300]}"

    def test_profile_help(self):
        """dragon profile --help shows usage with list, create, delete, use."""
        result = _run_dragon("profile", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "profile" in stdout_lower
        for action in ["list", "create", "delete"]:
            assert action in stdout_lower, f"Expected '{action}' in profile help: {result.stdout[:300]}"


# ───────────────────────────────────────────────────────────────────────
# Gateway Adapters List Tests
# ───────────────────────────────────────────────────────────────────────

class TestGatewayStatusAdapters:
    """Test that gateway status shows adapter information."""

    def test_gateway_status_adapters_list(self):
        """dragon gateway status lists Feishu adapter and other integrations."""
        result = _run_dragon("gateway", "status")
        assert result.returncode == 0
        # Check for adapter mentions
        stdout_lower = result.stdout.lower()
        adapters_found = any(
            name in stdout_lower
            for name in ["feishu", "telegram", "discord", "wechat"]
        )
        assert adapters_found, f"No adapter found in gateway status: {result.stdout[:300]}"

    def test_gateway_status_has_structured_output(self):
        """Gateway status output contains recognizable sections."""
        result = _run_dragon("gateway", "status")
        assert result.returncode == 0
        # Should have "Status" or "状态" header
        assert ("status" in result.stdout.lower() or "状态" in result.stdout)


# ───────────────────────────────────────────────────────────────────────
# Version Tests
# ───────────────────────────────────────────────────────────────────────

class TestCLIVersion:
    """Test the --version flag."""

    def test_cli_version(self):
        """dragon --version shows version string."""
        result = _run_dragon("--version")
        assert result.returncode == 0
        assert "Dragon Agent" in result.stdout, f"Expected 'Dragon Agent' in version output: {result.stdout}"
        # Should contain a version number like 1.2.0
        import re
        assert re.search(r'\d+\.\d+\.\d+', result.stdout), f"No semver found: {result.stdout}"

    def test_cli_version_is_not_error(self):
        """dragon --version exits 0 and has clean output."""
        result = _run_dragon("--version")
        assert result.returncode == 0
        assert result.stderr == "" or "warning" not in result.stderr.lower()


# ───────────────────────────────────────────────────────────────────────
# Doctor JSON Structure (detailed)
# ───────────────────────────────────────────────────────────────────────

class TestDoctorJSONDetailed:
    """Detailed tests for doctor --json output structure."""

    def test_doctor_json_valid_structure(self):
        """dragon doctor --json has valid structure with all required fields."""
        result = _run_dragon("doctor", "--json")
        assert result.returncode == 0, f"doctor --json failed: {result.stderr}"

        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 5, f"Expected at least 5 checks, got {len(data)}"

        required_fields = {"name", "ok", "detail", "icon"}
        for i, item in enumerate(data):
            assert isinstance(item, dict), f"Item {i} is not a dict"
            assert required_fields.issubset(set(item.keys())), \
                f"Item {i} missing fields: {required_fields - set(item.keys())}. Got: {item.keys()}"
            assert isinstance(item["name"], str), f"Item {i} 'name' is not str"
            assert isinstance(item["ok"], bool), f"Item {i} 'ok' is not bool"
            assert isinstance(item["detail"], str), f"Item {i} 'detail' is not str"
            assert isinstance(item["icon"], str), f"Item {i} 'icon' is not str"

    def test_doctor_json_python_check_always_first(self):
        """Doctor JSON output has Python version as first check."""
        result = _run_dragon("doctor", "--json")
        data = json.loads(result.stdout)
        # Python check should be first or near the top
        python_checks = [i for i, item in enumerate(data) if "python" in item["name"].lower()]
        assert len(python_checks) > 0, "No Python check found"
        assert python_checks[0] <= 2, f"Python check should be early, but found at index {python_checks[0]}"


# ───────────────────────────────────────────────────────────────────────
# No-command / Empty Args Test
# ───────────────────────────────────────────────────────────────────────

class TestNoCommand:
    """Test behavior with no command given."""

    def test_dragon_with_no_args(self):
        """Running dragon with no args shows help/version and exits 0."""
        result = _run_dragon()
        assert result.returncode == 0
        output = result.stdout
        assert "Dragon Agent" in output or "usage" in output.lower()


# ───────────────────────────────────────────────────────────────────────
# NEW tests (14): tools list, cron list, profile create/delete via CLI,
# sessions list, config validate missing file, and more.
# ───────────────────────────────────────────────────────────────────────

class TestToolsList:
    """Test dragon tools list subcommand."""

    def test_tools_list(self):
        """dragon tools list prints registered tools."""
        result = _run_dragon("tools", "list")
        assert result.returncode == 0, f"tools list failed: {result.stderr}"
        output = result.stdout
        assert "Tools" in output, f"Expected 'Tools' in output: {output[:200]}"


class TestCronList:
    """Test dragon cron list subcommand."""

    def test_cron_list(self):
        """dragon cron list shows job list (empty or with jobs)."""
        tmpdir = tempfile.mkdtemp()
        try:
            result = _run_dragon("cron", "list", cwd=tmpdir)
            assert result.returncode == 0, f"cron list failed: {result.stderr}"
            assert "Cron jobs" in result.stdout, f"Expected 'Cron jobs': {result.stdout[:200]}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestProfileSubprocessCLI:
    """Test profile create/delete via CLI subprocess with isolated HOME."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        # Override HOME so ProfileManager uses a temp base_dir
        self._home_env = {"HOME": self.tmpdir}

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_profile_create_cli(self):
        """dragon profile create <name> creates a profile."""
        result = _run_dragon("profile", "create", "cli-test-prof", env=self._home_env)
        assert result.returncode == 0, f"profile create failed: {result.stderr}"
        assert "Created profile" in result.stdout or "cli-test-prof" in result.stdout, \
            f"Expected creation message: {result.stdout[:200]}"

    def test_profile_delete_cli(self):
        """dragon profile delete <name> removes a profile."""
        # Create first
        _run_dragon("profile", "create", "to-delete", env=self._home_env)
        # Then delete
        result = _run_dragon("profile", "delete", "to-delete", env=self._home_env)
        assert result.returncode == 0, f"profile delete failed: {result.stderr}"
        # Deletion should succeed
        assert "Deleted" in result.stdout or "deleted" in result.stdout.lower(), \
            f"Expected deletion message: {result.stdout[:200]}"


class TestSessionsListCLI:
    """Test dragon sessions list subcommand."""

    def test_sessions_list(self):
        """dragon sessions list shows recent sessions."""
        result = _run_dragon("sessions", "list")
        assert result.returncode == 0, f"sessions list failed: {result.stderr}"
        assert "Recent sessions" in result.stdout, f"Expected 'Recent sessions': {result.stdout[:200]}"


class TestConfigValidateMissingFile:
    """Test config validate when config.yaml is missing."""

    def test_config_validate_missing_file(self):
        """dragon config validate with no config.yaml shows helpful message."""
        tmpdir = tempfile.mkdtemp()
        try:
            result = _run_dragon("config", "validate", cwd=tmpdir)
            # Should not crash; should report missing file
            assert result.returncode == 0, f"validate should exit 0: {result.stderr}"
            assert "未找到" in result.stdout or "config" in result.stdout.lower(), \
                f"Expected missing-file message: {result.stdout[:200]}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestMCPToolsList:
    """Test dragon mcp tools subcommand."""

    def test_mcp_tools_list(self):
        """dragon mcp tools lists MCP tool names."""
        result = _run_dragon("mcp", "tools")
        assert result.returncode == 0, f"mcp tools failed: {result.stderr}"
        assert "MCP Tools" in result.stdout, f"Expected 'MCP Tools': {result.stdout[:200]}"


class TestSkillsListCLI:
    """Test dragon skills list subcommand."""

    def test_skills_list_cli(self):
        """dragon skills list prints skills or 'No skills' message."""
        result = _run_dragon("skills", "list")
        assert result.returncode == 0, f"skills list failed: {result.stderr}"
        output = result.stdout
        assert "Skills" in output or "No skills" in output, \
            f"Expected skills output: {output[:200]}"


class TestConfigShowCLI:
    """Test dragon config show subcommand."""

    def test_config_show_output(self):
        """dragon config show prints configuration details."""
        result = _run_dragon("config", "show")
        assert result.returncode == 0, f"config show failed: {result.stderr}"
        assert "Configuration" in result.stdout or "Router" in result.stdout or "model" in result.stdout, \
            f"Expected config output: {result.stdout[:200]}"


class TestConfigCheckCLI:
    """Test dragon config check subcommand."""

    def test_config_check_output(self):
        """dragon config check runs without error and gives status."""
        result = _run_dragon("config", "check")
        assert result.returncode == 0, f"config check failed: {result.stderr}"
        output = result.stdout
        assert "Issues" in output or "looks good" in output or "✓" in output, \
            f"Expected check output: {output[:200]}"


class TestGatewayHelpCLI:
    """Test dragon gateway --help subcommand."""

    def test_gateway_help(self):
        """dragon gateway --help shows usage with start/status actions."""
        result = _run_dragon("gateway", "--help")
        assert result.returncode == 0
        stdout_lower = result.stdout.lower()
        assert "usage" in stdout_lower or "gateway" in stdout_lower
        assert "start" in stdout_lower and "status" in stdout_lower, \
            f"Expected start/status in help: {result.stdout[:300]}"


class TestDoctorHelpCLI:
    """Test dragon doctor --help subcommand."""

    def test_doctor_help(self):
        """dragon doctor --help shows usage."""
        result = _run_dragon("doctor", "--help")
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "doctor" in result.stdout.lower() or "诊断" in result.stdout, \
            f"Expected help output: {result.stdout[:200]}"


class TestTestHelpCLI:
    """Test dragon test --help subcommand."""

    def test_test_help(self):
        """dragon test --help shows usage."""
        result = _run_dragon("test", "--help")
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "test" in result.stdout.lower(), \
            f"Expected help output: {result.stdout[:200]}"


class TestSessionsSearchCLI:
    """Test dragon sessions search subcommand."""

    def test_sessions_search(self):
        """dragon sessions search runs without error."""
        result = _run_dragon("sessions", "search", "--query", "test")
        assert result.returncode == 0, f"sessions search failed: {result.stderr}"
