"""
Unit tests for SkillEngine — registration, discovery, pipelines, evolution.
"""
import os
import json
import tempfile

import pytest

from dragon.skill.engine import (
    SkillEngine, SkillPipeline, PipelineStep, PipelineResult,
)
from dragon.skill.skill import (
    DragonSkill, SkillMeta, SkillMatch, ExecutionMode,
)


class TestSkillEngineRegistration:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_skill(self):
        skill = self.engine.register(
            name="test-skill",
            description="A test skill for testing",
            content="# Test\nDo the thing.",
            tags=["test"],
        )
        assert skill.name == "test-skill"
        assert skill.meta.version == "1.0.0"

    def test_get_skill(self):
        self.engine.register("test-skill", "desc", "content")
        skill = self.engine.get("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"

    def test_get_missing_skill(self):
        assert self.engine.get("nonexistent") is None

    def test_list_skills(self):
        self.engine.register("a", "desc a", "content a", tags=["tag1"])
        self.engine.register("b", "desc b", "content b", tags=["tag2"])
        skills = self.engine.list_skills()
        assert len(skills) == 2
        names = [s["name"] for s in skills]
        assert "a" in names
        assert "b" in names

    def test_delete_skill(self):
        self.engine.register("temp", "desc", "content")
        assert self.engine.delete("temp") is True
        assert self.engine.get("temp") is None

    def test_delete_missing_skill(self):
        assert self.engine.delete("ghost") is False


class TestKeywordDiscovery:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)

        self.engine.register(
            "python-testing",
            "How to write and run Python unit tests with pytest",
            "# Testing\nUse pytest.",
            tags=["python", "testing", "pytest"],
        )
        self.engine.register(
            "docker-deploy",
            "Deploy applications using Docker containers",
            "# Docker\nBuild and push.",
            tags=["docker", "deploy", "devops"],
        )
        self.engine.register(
            "git-workflow",
            "Git branching and merge workflow for teams",
            "# Git\nFeature branches.",
            tags=["git", "workflow"],
        )

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_by_name_match(self):
        matches = self.engine._keyword_discover("python testing", top_k=3)
        assert len(matches) >= 1
        assert matches[0].skill_name == "python-testing"

    def test_discover_by_tag_match(self):
        matches = self.engine._keyword_discover("docker deployment", top_k=3)
        assert len(matches) >= 1
        assert any(m.skill_name == "docker-deploy" for m in matches)

    def test_discover_no_match(self):
        matches = self.engine._keyword_discover("kubernetes helm chart", top_k=3)
        assert len(matches) == 0

    def test_discover_returns_skill_match_objects(self):
        matches = self.engine._keyword_discover("git workflow", top_k=3)
        for m in matches:
            assert isinstance(m, SkillMatch)
            assert m.similarity > 0
            assert m.skill is not None


class TestSkillEngineExecution:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)
        self.engine.register("test-skill", "desc", "content")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_success(self):
        self.engine.record("test-skill", success=True, latency_ms=100.0)
        skill = self.engine.get("test-skill")
        assert skill.success_rate == 1.0
        assert skill.total_uses == 1

    def test_record_failure(self):
        self.engine.record("test-skill", success=False, latency_ms=50.0)
        skill = self.engine.get("test-skill")
        assert skill.success_rate == 0.0

    def test_record_missing_skill_silently_ignored(self):
        # Should not raise
        self.engine.record("no-such-skill", success=True)


class TestSkillPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_skill_persisted_to_disk(self):
        self.engine.register("persistent", "desc", "content", tags=["test"])
        assert os.path.exists(os.path.join(self.skills_dir, "persistent.json"))

    def test_skill_loaded_from_disk(self):
        self.engine.register("persistent", "desc", "content")
        self.engine.record("persistent", success=True)

        # Create a new engine pointing at same dir
        engine2 = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)
        skill = engine2.get("persistent")
        assert skill is not None
        assert skill.name == "persistent"
        assert skill.total_uses == 1  # metrics persisted


class TestSkillEngineStats:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)
        self.engine.register("s1", "desc 1", "content 1")
        self.engine.register("s2", "desc 2", "content 2")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stats_structure(self):
        stats = self.engine.stats()
        assert stats["total_skills"] == 2
        assert stats["active_skills"] == 2
        assert stats["total_executions"] == 0
        assert "avg_success_rate" in stats
        assert "evolution_proposals" in stats


class TestSkillCompose:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.engine = SkillEngine(skills_dir=self.skills_dir, auto_evolve=False)
        self.engine.register("step1", "First step", "# Step 1")
        self.engine.register("step2", "Second step", "# Step 2")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_compose_returns_pipeline(self):
        pipeline = self.engine.compose(["step1", "step2"])
        assert isinstance(pipeline, SkillPipeline)
        assert len(pipeline.steps) == 2
        assert pipeline.steps[0].skill_name == "step1"
        assert pipeline.steps[1].skill_name == "step2"

    def test_compose_parallel_mode(self):
        pipeline = self.engine.compose(["step1", "step2"], mode="parallel")
        assert pipeline.steps[0].mode == ExecutionMode.PARALLEL
