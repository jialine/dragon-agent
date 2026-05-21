"""
Unit tests for MemoryGraph — entity/relation CRUD, graph queries, and semantic search.
Mocked: ChromaDB and sentence-transformers (no heavy deps for test).
"""
import os
import json
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import networkx as nx

from panda.memory import (
    MemoryGraph, Entity, Relation,
    _make_entity_id, _make_relation_id, _extract_json,
    _VALID_ENTITY_TYPES, _VALID_RELATION_TYPES,
)


# ── ID Generation ────────────────────────────────────────────────────

class TestEntityIDGeneration:
    def test_deterministic_id(self):
        id1 = _make_entity_id("Qwen3", "technology")
        id2 = _make_entity_id("Qwen3", "technology")
        assert id1 == id2

    def test_different_name_produces_different_id(self):
        id1 = _make_entity_id("Qwen3", "technology")
        id2 = _make_entity_id("GPT-4", "technology")
        assert id1 != id2

    def test_different_type_produces_different_id(self):
        id1 = _make_entity_id("Qwen3", "technology")
        id2 = _make_entity_id("Qwen3", "project")
        assert id1 != id2

    def test_id_is_12_chars(self):
        eid = _make_entity_id("Something Long Here", "concept")
        assert len(eid) == 12


class TestRelationIDGeneration:
    def test_deterministic_id(self):
        rid1 = _make_relation_id("a", "b", "depends_on")
        rid2 = _make_relation_id("a", "b", "depends_on")
        assert rid1 == rid2

    def test_id_is_12_chars(self):
        rid = _make_relation_id("src123", "tgt456", "uses")
        assert len(rid) == 12


# ── JSON Extraction ──────────────────────────────────────────────────

class TestJSONExtraction:
    def test_clean_json(self):
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_text(self):
        result = _extract_json('Here is some text {"key": "value"} and more text')
        assert result == {"key": "value"}

    def test_invalid_json_returns_none(self):
        result = _extract_json("not json at all")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _extract_json("")
        assert result is None


# ── Entity Dataclass ─────────────────────────────────────────────────

class TestEntityDataclass:
    def test_entity_creation_defaults(self):
        e = Entity(id="e1", name="Test")
        assert e.id == "e1"
        assert e.name == "Test"
        assert e.type == "concept"
        assert e.importance == 0.5
        assert e.created_at != ""

    def test_entity_created_at_is_set(self):
        e = Entity(id="e1", name="Test")
        assert e.created_at != ""
        assert e.updated_at != ""

    def test_invalid_type_falls_back_to_concept(self):
        e = Entity(id="e1", name="Test", type="invalid_type_xyz")
        assert e.type == "concept"

    def test_valid_type_preserved(self):
        for t in _VALID_ENTITY_TYPES:
            e = Entity(id="e1", name="Test", type=t)
            assert e.type == t

    def test_importance_clamped(self):
        e = Entity(id="e1", name="Test", importance=1.5)
        assert e.importance == 1.0
        e2 = Entity(id="e2", name="Test2", importance=-0.5)
        assert e2.importance == 0.0


# ── Relation Dataclass ───────────────────────────────────────────────

class TestRelationDataclass:
    def test_relation_creation(self):
        r = Relation(id="r1", source_id="a", target_id="b", type="depends_on")
        assert r.id == "r1"
        assert r.source_id == "a"
        assert r.target_id == "b"
        assert r.type == "depends_on"

    def test_invalid_type_falls_back(self):
        r = Relation(id="r1", source_id="a", target_id="b", type="weird_rel")
        assert r.type == "related_to"

    def test_created_at_is_set(self):
        r = Relation(id="r1", source_id="a", target_id="b")
        assert r.created_at != ""


# ── MemoryGraph Uninitialized Checks ──────────────────────────────────

class TestMemoryGuardRails:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph_path = os.path.join(self.tmpdir, "graph.json")
        self.vectordb_path = os.path.join(self.tmpdir, "vectordb")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_upsert_before_init_raises(self):
        mem = MemoryGraph(persist_dir=self.vectordb_path, graph_path=self.graph_path)
        with pytest.raises(RuntimeError):
            mem.upsert_entity(name="Test")

    def test_search_before_init_raises(self):
        mem = MemoryGraph(persist_dir=self.vectordb_path, graph_path=self.graph_path)
        with pytest.raises(RuntimeError):
            mem.search_entities("test")


# ── Graph Queries (pure NetworkX, no ChromaDB) ────────────────────────

class TestMemoryGraphQueries:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph_path = os.path.join(self.tmpdir, "graph.json")
        self.vectordb_path = os.path.join(self.tmpdir, "vectordb")

        # Build a memory graph with pre-populated NetworkX graph (bypass init)
        self.mem = MemoryGraph(
            persist_dir=self.vectordb_path,
            graph_path=self.graph_path,
        )
        self.mem._initialized = True
        self.mem._graph = nx.DiGraph()

        self.mem._graph.add_node("a", name="Alpha", type="technology",
                                  importance=0.9, properties={}, embedding=[],
                                  created_at="2025-01-01T00:00:00+00:00",
                                  updated_at="2025-01-01T00:00:00+00:00")
        self.mem._graph.add_node("b", name="Beta", type="project",
                                  importance=0.7, properties={}, embedding=[],
                                  created_at="2025-01-01T00:00:00+00:00",
                                  updated_at="2025-01-01T00:00:00+00:00")
        self.mem._graph.add_node("c", name="Gamma", type="concept",
                                  importance=0.5, properties={}, embedding=[],
                                  created_at="2025-01-01T00:00:00+00:00",
                                  updated_at="2025-01-01T00:00:00+00:00")
        self.mem._graph.add_edge("a", "b", id="r1", type="depends_on", properties={})
        self.mem._graph.add_edge("b", "c", id="r2", type="uses", properties={})

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_entity_exists(self):
        e = self.mem.get_entity("a")
        assert e is not None
        assert e.name == "Alpha"
        assert e.type == "technology"

    def test_get_entity_not_exists(self):
        e = self.mem.get_entity("nonexistent")
        assert e is None

    def test_get_relations_outgoing(self):
        rels = self.mem.get_relations("a", direction="outgoing")
        assert len(rels) == 1
        assert rels[0].type == "depends_on"
        assert rels[0].target_id == "b"

    def test_get_relations_incoming(self):
        rels = self.mem.get_relations("b", direction="incoming")
        assert len(rels) == 1
        assert rels[0].source_id == "a"

    def test_get_relations_both(self):
        rels = self.mem.get_relations("b", direction="both")
        assert len(rels) == 2

    def test_get_relations_nonexistent(self):
        rels = self.mem.get_relations("nonexistent")
        assert rels == []

    def test_query_neighbors(self):
        result = self.mem.query_graph("NEIGHBORS a")
        assert result["operation"] == "neighbors"
        assert result["entity_count"] == 1

    def test_query_all_entities(self):
        result = self.mem.query_graph("ALL ENTITIES")
        assert result["operation"] == "all_entities"
        assert result["entity_count"] == 3

    def test_query_all_relations(self):
        result = self.mem.query_graph("ALL RELATIONS")
        assert result["operation"] == "all_relations"
        assert result["entity_count"] == 2

    def test_query_path_exists(self):
        result = self.mem.query_graph("PATH a TO c")
        assert result["operation"] == "path"
        assert result["entity_count"] == 3
        names = [e["name"] for e in result["result"]]
        assert names == ["Alpha", "Beta", "Gamma"]

    def test_query_path_no_path(self):
        self.mem._graph.add_node("d", name="Delta", type="concept")
        result = self.mem.query_graph("PATH a TO d")
        assert "No path found" in result.get("error", "")

    def test_query_unknown_command(self):
        result = self.mem.query_graph("WEIRD COMMAND xyz")
        assert result["operation"] == "unknown"
        assert "error" in result

    def test_query_neighbors_out(self):
        result = self.mem.query_graph("NEIGHBORS a OUT")
        assert result["operation"] == "neighbors"

    def test_query_neighbors_in(self):
        result = self.mem.query_graph("NEIGHBORS b IN")
        assert result["operation"] == "neighbors"

    def test_delete_entity(self):
        with patch.object(self.mem, '_collection', create=True):
            ok = self.mem.delete_entity("a")
            assert ok is True
            assert self.mem.get_entity("a") is None

    def test_delete_nonexistent_entity(self):
        with patch.object(self.mem, '_collection', create=True):
            ok = self.mem.delete_entity("no_such")
            assert ok is False


# ── Graph Persistence ────────────────────────────────────────────────

class TestMemoryGraphPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph_path = os.path.join(self.tmpdir, "graph.json")
        self.vectordb_path = os.path.join(self.tmpdir, "vectordb")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        mem1 = MemoryGraph(
            persist_dir=self.vectordb_path,
            graph_path=self.graph_path,
        )
        mem1._initialized = True
        mem1._graph = nx.DiGraph()
        mem1._graph.add_node("n1", name="N1", type="concept", properties={},
                              embedding=[], importance=0.5,
                              created_at="2025-01-01T00:00:00+00:00",
                              updated_at="2025-01-01T00:00:00+00:00")
        mem1._graph.add_node("n2", name="N2", type="concept", properties={},
                              embedding=[], importance=0.5,
                              created_at="2025-01-01T00:00:00+00:00",
                              updated_at="2025-01-01T00:00:00+00:00")
        mem1._graph.add_edge("n1", "n2", id="e1", type="related_to", properties={})
        mem1._save_graph()
        assert os.path.exists(self.graph_path)

        mem2 = MemoryGraph(
            persist_dir=self.vectordb_path,
            graph_path=self.graph_path,
        )
        mem2._initialized = True
        mem2._load_graph()
        assert mem2._graph.number_of_nodes() == 2
        assert mem2._graph.number_of_edges() == 1

    def test_load_nonexistent_starts_fresh(self):
        mem = MemoryGraph(
            persist_dir=self.vectordb_path,
            graph_path="/nonexistent/graph.json",
        )
        mem._initialized = True
        mem._load_graph()
        assert mem._graph.number_of_nodes() == 0


# ── Valid Constants ──────────────────────────────────────────────────

class TestValidConstants:
    def test_entity_types(self):
        for t in _VALID_ENTITY_TYPES:
            assert isinstance(t, str)
            assert len(t) > 0

    def test_relation_types(self):
        for r in _VALID_RELATION_TYPES:
            assert isinstance(r, str)
            assert len(r) > 0
