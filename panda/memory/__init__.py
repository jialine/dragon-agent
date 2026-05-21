"""
Panda Memory — 持久化实体-关系图谱 + 语义检索

Architecture:
    ┌─────────────────┐     ┌──────────────────┐     ┌───────────────────────┐
    │  upsert_entity  │────▶│  NetworkX DiGraph │     │  ChromaDB             │
    │  add_relation   │     │  (in-memory graph)│     │  (vector embeddings)  │
    │  get_context    │     │  → graph.json     │     │  → persist_dir/       │
    └─────────────────┘     └──────────────────┘     └───────────────────────┘

Storage backends:
  - NetworkX DiGraph: fast in-memory graph traversal, persisted as JSON on shutdown
  - ChromaDB PersistentClient: semantic search via sentence-transformers embeddings
  - Embedding model: BAAI/bge-small-zh-v1.5 (configurable via PandaConfig)

Thread safety: ChromaDB write operations are guarded by ``threading.Lock``.

This module does NOT import ``panda.router`` at module level to avoid circular
imports.  The lazy import is performed inside ``extract_from_conversation()``
only when the extraction feature is actually invoked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx

logger = logging.getLogger("panda.memory")

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

_VALID_ENTITY_TYPES = frozenset(
    {"person", "project", "technology", "event", "concept", "organization", "other"}
)

_VALID_RELATION_TYPES = frozenset(
    {"prefers", "uses", "fixed", "caused", "depends_on", "related_to", "part_of", "created_by"}
)


@dataclass
class Entity:
    """A node in the memory graph.

    Attributes:
        id:          Unique identifier (auto-generated if not provided).
        type:        Semantic type — ``person``, ``project``, ``technology``,
                     ``event``, ``concept``, ``organization``, ``other``.
        name:        Human-readable display name.
        properties:  Arbitrary key-value metadata (tags, timestamps, etc.).
        embedding:   Dense vector produced by the embedding model.
        created_at:  ISO-8601 creation timestamp.
        updated_at:  ISO-8601 last-update timestamp.
        importance:  0.0–1.0 importance score (used for pruning / ranking).
    """

    id: str
    type: str = "concept"
    name: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    embedding: List[float] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    importance: float = 0.5

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if self.type not in _VALID_ENTITY_TYPES:
            logger.warning("Unknown entity type %r, defaulting to 'concept'", self.type)
            self.type = "concept"
        self.importance = max(0.0, min(1.0, self.importance))


@dataclass
class Relation:
    """A directed edge connecting two entities in the memory graph.

    Attributes:
        id:          Unique identifier (auto-generated).
        source_id:   ID of the source ``Entity``.
        target_id:   ID of the target ``Entity``.
        type:        Relation type — ``prefers``, ``uses``, ``fixed``, ``caused``,
                     ``depends_on``, ``related_to``, ``part_of``, ``created_by``.
        properties:  Arbitrary key-value metadata.
        created_at:  ISO-8601 creation timestamp.
    """

    id: str
    source_id: str
    target_id: str
    type: str = "related_to"
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if self.type not in _VALID_RELATION_TYPES:
            logger.warning("Unknown relation type %r, defaulting to 'related_to'", self.type)
            self.type = "related_to"


# ---------------------------------------------------------------------------
# Unique ID generation
# ---------------------------------------------------------------------------

def _make_entity_id(name: str, entity_type: str) -> str:
    """Deterministic entity ID: SHA-256 hex of ``name::type`` truncated to 12 chars."""
    raw = f"{name}::{entity_type}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _make_relation_id(source_id: str, target_id: str, rel_type: str) -> str:
    """Deterministic relation ID."""
    raw = f"{source_id}::{target_id}::{rel_type}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


# ---------------------------------------------------------------------------
# JSON extraction helpers (mirrors router's approach)
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Try to pull a JSON object out of model output."""
    if not text:
        return None
    # 1. Direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 2. Find JSON block inside text
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Entity extraction prompt (Chinese, requests JSON output)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """你是一个知识图谱提取器。请从以下对话中提取所有实体及其关系，并输出 JSON。

输出格式**必须严格遵守**，只输出 JSON，不要包含任何其他文本、解释或 Markdown 标记。

JSON 格式：
{{
  "entities": [
    {{
      "name": "实体名称",
      "type": "person" | "project" | "technology" | "event" | "concept" | "organization" | "other",
      "properties": {{"key": "value"}},
      "importance": 0.0-1.0 之间的浮点数
    }}
  ],
  "relations": [
    {{
      "source_name": "源实体名称（必须与 entities 中的某个 name 完全一致）",
      "target_name": "目标实体名称（必须与 entities 中的某个 name 完全一致）",
      "type": "prefers" | "uses" | "fixed" | "caused" | "depends_on" | "related_to" | "part_of" | "created_by",
      "properties": {{"key": "value"}}
    }}
  ]
}}

提取规则：
- 只提取对话中明确提到的实体，不要凭空创造。
- 实体类型根据语义判断：person=人物, project=项目, technology=技术/工具, event=事件, concept=概念/术语, organization=组织/公司, other=其他。
- 关系类型：prefers=偏好/推荐, uses=使用/采用, fixed=修复/解决, caused=导致/引起, depends_on=依赖, related_to=相关, part_of=组成部分, created_by=创建者。
- importance：根据实体在对话中的重要程度给分，核心话题 0.8-1.0，次要提及 0.3-0.5，背景信息 0.0-0.2。
- 如果对话中没有明确的实体或关系，返回空的 entities 和 relations 数组。

对话内容：
{messages_text}

请输出 JSON："""


# ---------------------------------------------------------------------------
# MemoryGraph — main class
# ---------------------------------------------------------------------------


class MemoryGraph:
    """Persistent entity-relation graph with semantic search.

    Combines:
      * NetworkX ``DiGraph`` for graph operations (path finding, 1-hop neighbors)
      * ChromaDB ``PersistentClient`` for embedding-based semantic search
      * ``sentence-transformers`` for generating embeddings

    Typical usage::

        from panda.config import PandaConfig

        cfg = PandaConfig.load()
        memory = MemoryGraph(
            persist_dir=cfg.memory.persist_dir,
            embedding_model=cfg.memory.embedding_model,
            search_top_k=cfg.memory.search_top_k,
            graph_path="panda_data/memory/graph.json",
        )
        await memory.initialize()

        # Upsert entities
        mem.upsert_entity("e1", name="Qwen3", type="technology",
                          properties={"version": "0.6B"})
        mem.add_relation("e1", "e2", "depends_on", {"since": "2025"})

        # Get context for prompt injection
        ctx = mem.get_context("What is Qwen3?")
        print(ctx["relevant_entities"])
    """

    # ChromaDB collection name
    _COLLECTION_NAME = "panda_memory_entities"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        persist_dir: str = "panda_data/vectordb",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        search_top_k: int = 5,
        search_threshold: float = 0.5,
        graph_path: str = "panda_data/memory/graph.json",
        router: Optional[Any] = None,
    ) -> None:
        """
        Args:
            persist_dir:      Directory for ChromaDB persistent storage.
            embedding_model:  HuggingFace sentence-transformers model name.
            search_top_k:     Default number of results for semantic search.
            search_threshold: Minimum similarity score (cosine) for search results.
            graph_path:       Path to the graph JSON file for persistence.
            router:           Optional PandaRouter reference for entity extraction.
                              If not provided, ``extract_from_conversation`` will
                              attempt to use the global singleton via lazy import.
        """
        self._persist_dir = Path(persist_dir)
        self._embedding_model_name = embedding_model
        self._search_top_k = search_top_k
        self._search_threshold = search_threshold
        self._graph_path = Path(graph_path)
        self._router = router

        # Internal state ---------------------------------------------------
        self._graph: nx.DiGraph = nx.DiGraph()
        self._embedding_model: Optional[Any] = None  # SentenceTransformer
        self._chroma_client: Optional[Any] = None     # chromadb.PersistentClient
        self._collection: Optional[Any] = None         # chromadb.Collection

        # Thread safety — ChromaDB is NOT thread-safe for writes
        self._chroma_lock = threading.Lock()
        self._graph_lock = threading.Lock()

        # Lifecycle tracking
        self._initialized: bool = False
        self._shutdown: bool = False

        logger.info(
            "MemoryGraph created (persist_dir=%s, embedding=%s, graph=%s)",
            self._persist_dir,
            embedding_model,
            self._graph_path,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialise backends: load embedding model, ChromaDB, and graph from disk.

        Idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        # 1. Load sentence-transformers model
        logger.info("Loading embedding model: %s …", self._embedding_model_name)
        try:
            from sentence_transformers import SentenceTransformer

            self._embedding_model = SentenceTransformer(
                self._embedding_model_name, device="cpu"
            )
            logger.info("Embedding model loaded (dim=%d)", self._embedding_model.get_sentence_embedding_dimension())
        except Exception:
            logger.exception("Failed to load embedding model")
            raise

        # 2. Initialise ChromaDB
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Initialising ChromaDB at %s …", self._persist_dir)
        try:
            import chromadb
            from chromadb.config import Settings

            self._chroma_client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            # Get or create the collection
            try:
                self._collection = self._chroma_client.get_collection(
                    name=self._COLLECTION_NAME
                )
                logger.info("Reusing existing ChromaDB collection '%s'", self._COLLECTION_NAME)
            except Exception:
                self._collection = self._chroma_client.create_collection(
                    name=self._COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info("Created ChromaDB collection '%s'", self._COLLECTION_NAME)
        except Exception:
            logger.exception("Failed to initialise ChromaDB")
            raise

        # 3. Load graph from disk
        self._load_graph()

        self._initialized = True
        logger.info("MemoryGraph initialised (%d entities, %d relations)",
                     self._graph.number_of_nodes(), self._graph.number_of_edges())

    def shutdown(self) -> None:
        """Persist graph to disk and release resources.

        Safe to call multiple times.
        """
        if self._shutdown:
            return
        self._shutdown = True

        logger.info("Shutting down MemoryGraph …")

        # Persist graph
        self._save_graph()

        # Release embedding model (free memory)
        if self._embedding_model is not None:
            del self._embedding_model
            self._embedding_model = None

        # ChromaDB PersistentClient doesn't need explicit close, but clear refs
        self._collection = None
        self._chroma_client = None

        logger.info("MemoryGraph shut down.")

    # ------------------------------------------------------------------
    # Graph persistence (JSON)
    # ------------------------------------------------------------------

    def _load_graph(self) -> None:
        """Load the NetworkX graph from ``graph.json`` if it exists."""
        if not self._graph_path.exists():
            logger.info("No existing graph file at %s — starting fresh.", self._graph_path)
            return

        try:
            with open(self._graph_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read graph file %s: %s — starting fresh.", self._graph_path, exc)
            return

        with self._graph_lock:
            self._graph = nx.DiGraph()

            # Restore nodes
            for node_data in data.get("nodes", []):
                node_id = node_data["id"]
                attrs = {k: v for k, v in node_data.items() if k != "id"}
                self._graph.add_node(node_id, **attrs)

            # Restore edges
            for edge_data in data.get("edges", []):
                self._graph.add_edge(
                    edge_data["source"],
                    edge_data["target"],
                    type=edge_data.get("type", "related_to"),
                    properties=edge_data.get("properties", {}),
                    id=edge_data.get("id", ""),
                )

        logger.info(
            "Loaded graph from %s: %d nodes, %d edges",
            self._graph_path,
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
        )

    def _save_graph(self) -> None:
        """Persist the NetworkX graph to ``graph.json``."""
        self._graph_path.parent.mkdir(parents=True, exist_ok=True)

        with self._graph_lock:
            # Serialise nodes
            nodes = []
            for node_id, attrs in self._graph.nodes(data=True):
                node_entry = {"id": node_id}
                node_entry.update(attrs)
                # Don't serialise embedding vectors (they're in ChromaDB)
                node_entry.pop("embedding", None)
                nodes.append(node_entry)

            # Serialise edges
            edges = []
            for u, v, attrs in self._graph.edges(data=True):
                edges.append({
                    "source": u,
                    "target": v,
                    "type": attrs.get("type", "related_to"),
                    "properties": attrs.get("properties", {}),
                    "id": attrs.get("id", ""),
                })

            data = {"nodes": nodes, "edges": edges}

        try:
            # Atomic write: write to temp file, then rename
            tmp_path = self._graph_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, self._graph_path)
            logger.info("Graph saved to %s", self._graph_path)
        except OSError as exc:
            logger.error("Failed to save graph: %s", exc)

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        entity_id: Optional[str] = None,
        *,
        name: str = "",
        type: str = "concept",
        properties: Optional[Dict[str, Any]] = None,
        importance: float = 0.5,
    ) -> Entity:
        """Create or update an entity in the graph and ChromaDB.

        If ``entity_id`` is ``None``, a deterministic ID is generated from
        ``name`` and ``type``.

        Args:
            entity_id:  Unique entity ID. Auto-generated if None.
            name:       Human-readable display name.
            type:       Entity type (person, project, technology, etc.).
            properties: Arbitrary metadata dict.
            importance: 0.0–1.0 importance score.

        Returns:
            The created or updated ``Entity`` dataclass.
        """
        self._assert_initialised()

        if entity_id is None:
            entity_id = _make_entity_id(name, type)

        props = dict(properties or {})
        now = datetime.now(timezone.utc).isoformat()

        # Generate embedding
        embedding = self._embed(name, type, props)

        entity = Entity(
            id=entity_id,
            type=type,
            name=name,
            properties=props,
            embedding=embedding,
            created_at=now,
            updated_at=now,
            importance=importance,
        )

        # Update graph
        with self._graph_lock:
            self._graph.add_node(
                entity_id,
                name=name,
                type=type,
                properties=props,
                embedding=embedding,
                importance=importance,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
            )

        # Upsert in ChromaDB
        self._chroma_upsert(entity)

        logger.debug("Upserted entity: id=%s name=%r type=%s", entity_id, name, type)
        return entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Retrieve a single entity by ID.

        Returns ``None`` if the entity does not exist.
        """
        self._assert_initialised()

        with self._graph_lock:
            if entity_id not in self._graph:
                return None
            attrs = self._graph.nodes[entity_id]

        return Entity(
            id=entity_id,
            type=attrs.get("type", "concept"),
            name=attrs.get("name", ""),
            properties=dict(attrs.get("properties", {})),
            embedding=list(attrs.get("embedding", [])),
            created_at=attrs.get("created_at", ""),
            updated_at=attrs.get("updated_at", ""),
            importance=float(attrs.get("importance", 0.5)),
        )

    def delete_entity(self, entity_id: str) -> bool:
        """Remove an entity from the graph and ChromaDB.

        Returns ``True`` if the entity existed and was removed.
        """
        self._assert_initialised()

        with self._graph_lock:
            if entity_id not in self._graph:
                return False
            self._graph.remove_node(entity_id)

        # Remove from ChromaDB
        with self._chroma_lock:
            try:
                self._collection.delete(ids=[entity_id])
            except Exception as exc:
                logger.warning("Failed to delete entity %s from ChromaDB: %s", entity_id, exc)

        logger.debug("Deleted entity: %s", entity_id)
        return True

    # ------------------------------------------------------------------
    # Relation CRUD
    # ------------------------------------------------------------------

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        rel_type: str = "related_to",
        properties: Optional[Dict[str, Any]] = None,
        relation_id: Optional[str] = None,
    ) -> Relation:
        """Add a directed relation between two entities.

        Both entities must already exist in the graph.

        Args:
            source_id:   Source entity ID.
            target_id:   Target entity ID.
            rel_type:    Relation type (prefers, uses, depends_on, etc.).
            properties:  Arbitrary metadata dict.
            relation_id: Custom relation ID. Auto-generated if None.

        Returns:
            The created ``Relation`` dataclass.

        Raises:
            ValueError: If either entity does not exist.
        """
        self._assert_initialised()

        with self._graph_lock:
            if source_id not in self._graph:
                raise ValueError(f"Source entity not found: {source_id}")
            if target_id not in self._graph:
                raise ValueError(f"Target entity not found: {target_id}")

        if relation_id is None:
            relation_id = _make_relation_id(source_id, target_id, rel_type)

        props = dict(properties or {})
        now = datetime.now(timezone.utc).isoformat()

        relation = Relation(
            id=relation_id,
            source_id=source_id,
            target_id=target_id,
            type=rel_type,
            properties=props,
            created_at=now,
        )

        with self._graph_lock:
            self._graph.add_edge(
                source_id,
                target_id,
                id=relation_id,
                type=rel_type,
                properties=props,
            )

        logger.debug(
            "Added relation: %s -[%s]-> %s", source_id, rel_type, target_id
        )
        return relation

    def get_relations(
        self,
        entity_id: str,
        *,
        direction: str = "both",
    ) -> List[Relation]:
        """Get all relations involving an entity.

        Args:
            entity_id:  The entity to query.
            direction:  ``"outgoing"``, ``"incoming"``, or ``"both"`` (default).

        Returns:
            List of ``Relation`` dataclasses.
        """
        self._assert_initialised()

        relations: List[Relation] = []

        with self._graph_lock:
            if entity_id not in self._graph:
                return relations

            if direction in ("outgoing", "both"):
                for _, target, attrs in self._graph.out_edges(entity_id, data=True):
                    relations.append(
                        Relation(
                            id=attrs.get("id", ""),
                            source_id=entity_id,
                            target_id=target,
                            type=attrs.get("type", "related_to"),
                            properties=dict(attrs.get("properties", {})),
                        )
                    )

            if direction in ("incoming", "both"):
                for source, _, attrs in self._graph.in_edges(entity_id, data=True):
                    relations.append(
                        Relation(
                            id=attrs.get("id", ""),
                            source_id=source,
                            target_id=entity_id,
                            type=attrs.get("type", "related_to"),
                            properties=dict(attrs.get("properties", {})),
                        )
                    )

        return relations

    # ------------------------------------------------------------------
    # Semantic search (ChromaDB)
    # ------------------------------------------------------------------

    def search_entities(
        self,
        query: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> List[Entity]:
        """Semantic search for entities via ChromaDB embeddings.

        Args:
            query:     Natural-language search query.
            top_k:     Max results to return (default from config).
            threshold: Minimum cosine similarity (default from config).

        Returns:
            List of matching ``Entity`` dataclasses, sorted by relevance.
        """
        self._assert_initialised()

        top_k = top_k or self._search_top_k
        threshold = threshold or self._search_threshold

        # Generate query embedding
        query_embedding = self._embed_query(query)

        with self._chroma_lock:
            try:
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_k,
                    include=["embeddings", "metadatas", "distances"],
                )
            except Exception as exc:
                logger.error("ChromaDB query failed: %s", exc)
                return []

        entities: List[Entity] = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return entities

        for i, entity_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            # ChromaDB with cosine space returns cosine distance = 1 - similarity
            similarity = 1.0 - float(distance)

            if similarity < threshold:
                continue

            metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
            embedding = results["embeddings"][0][i] if results.get("embeddings") else []

            entities.append(
                Entity(
                    id=entity_id,
                    type=metadata.get("type", "concept"),
                    name=metadata.get("name", ""),
                    properties=json.loads(metadata.get("properties", "{}")),
                    embedding=list(embedding),
                    created_at=metadata.get("created_at", ""),
                    updated_at=metadata.get("updated_at", ""),
                    importance=float(metadata.get("importance", 0.5)),
                )
            )

        return entities

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def query_graph(self, cypher_like: str) -> Dict[str, Any]:
        """Simple graph traversal with a pseudo-Cypher-like query language.

        Supported operations (case-insensitive):

        - ``NEIGHBORS <entity_id>`` — 1-hop neighbors of an entity.
        - ``NEIGHBORS <entity_id> OUT`` — outgoing 1-hop neighbors only.
        - ``NEIGHBORS <entity_id> IN`` — incoming 1-hop neighbors only.
        - ``PATH <source_id> TO <target_id>`` — shortest path between two entities.
        - ``ALL ENTITIES`` — list all entity IDs in the graph.
        - ``ALL RELATIONS`` — list all relations (source → type → target).

        Args:
            cypher_like: A simplified query string.

        Returns:
            A dict with ``operation``, ``result``, and ``entity_count`` keys.
        """
        self._assert_initialised()

        q = cypher_like.strip()
        q_upper = q.upper()

        # NEIGHBORS <entity_id> [OUT|IN]
        if q_upper.startswith("NEIGHBORS "):
            parts = q.split()
            if len(parts) < 2:
                return {"operation": "neighbors", "result": [], "error": "Missing entity_id"}
            entity_id = parts[1]
            direction = "both"
            if len(parts) >= 3:
                if parts[2].upper() == "OUT":
                    direction = "outgoing"
                elif parts[2].upper() == "IN":
                    direction = "incoming"

            relations = self.get_relations(entity_id, direction=direction)
            neighbors: List[Dict[str, Any]] = []
            seen: set = set()
            for rel in relations:
                neighbor_id = rel.target_id if rel.source_id == entity_id else rel.source_id
                if neighbor_id not in seen:
                    seen.add(neighbor_id)
                    entity = self.get_entity(neighbor_id)
                    neighbors.append({
                        "entity_id": neighbor_id,
                        "name": entity.name if entity else "",
                        "type": entity.type if entity else "",
                        "relation": rel.type,
                        "relation_direction": "outgoing" if rel.source_id == entity_id else "incoming",
                    })
            return {"operation": "neighbors", "result": neighbors, "entity_count": len(neighbors)}

        # PATH <source> TO <target>
        elif " TO " in q_upper and q_upper.startswith("PATH "):
            parts = q.split()
            if len(parts) < 4 or parts[2].upper() != "TO":
                return {"operation": "path", "result": [], "error": "Usage: PATH <source_id> TO <target_id>"}
            source_id = parts[1]
            target_id = parts[3]

            with self._graph_lock:
                try:
                    path = nx.shortest_path(self._graph, source=source_id, target=target_id)
                except nx.NetworkXNoPath:
                    return {"operation": "path", "result": [], "entity_count": 0, "error": "No path found"}
                except nx.NodeNotFound as exc:
                    return {"operation": "path", "result": [], "error": str(exc)}

            path_entities = []
            for node_id in path:
                entity = self.get_entity(node_id)
                path_entities.append({
                    "entity_id": node_id,
                    "name": entity.name if entity else "",
                    "type": entity.type if entity else "",
                })
            return {"operation": "path", "result": path_entities, "entity_count": len(path_entities)}

        # ALL ENTITIES
        elif q_upper == "ALL ENTITIES":
            with self._graph_lock:
                node_ids = list(self._graph.nodes())
            entities = []
            for nid in node_ids:
                attrs = self._graph.nodes[nid]
                entities.append({
                    "entity_id": nid,
                    "name": attrs.get("name", ""),
                    "type": attrs.get("type", ""),
                })
            return {"operation": "all_entities", "result": entities, "entity_count": len(entities)}

        # ALL RELATIONS
        elif q_upper == "ALL RELATIONS":
            with self._graph_lock:
                edges = []
                for u, v, attrs in self._graph.edges(data=True):
                    edges.append({
                        "source_id": u,
                        "target_id": v,
                        "type": attrs.get("type", "related_to"),
                    })
            return {"operation": "all_relations", "result": edges, "entity_count": len(edges)}

        else:
            return {"operation": "unknown", "result": [], "error": f"Unrecognised query: {cypher_like!r}"}

    # ------------------------------------------------------------------
    # Context retrieval (for system prompt injection)
    # ------------------------------------------------------------------

    def get_context(
        self,
        query: str,
        max_entities: int = 10,
    ) -> Dict[str, Any]:
        """Build a memory context dict for system prompt injection.

        Performs:
          1. Semantic search for top-k relevant entities.
          2. For each, fetches 1-hop neighbors and their relations.
          3. Returns a structured dict suitable for inclusion in a system prompt.

        Args:
            query:         The user query to contextualise.
            max_entities:  Max entities to include in the context.

        Returns:
            A dict with keys:
              - ``relevant_entities``: list of entity objects with neighbors
              - ``relations``: list of relations among the entities found
              - ``total_entities``: total count of entities included
              - ``query``: the original query
        """
        self._assert_initialised()

        # Step 1: Semantic search
        entities = self.search_entities(query, top_k=max_entities)
        if not entities:
            return {
                "relevant_entities": [],
                "relations": [],
                "total_entities": 0,
                "query": query,
            }

        entity_ids = {e.id for e in entities}
        all_relations: List[Dict[str, Any]] = []
        enriched_entities: List[Dict[str, Any]] = []

        for entity in entities[:max_entities]:
            relations = self.get_relations(entity.id, direction="both")
            neighbors: List[Dict[str, Any]] = []

            for rel in relations:
                neighbor_id = rel.target_id if rel.source_id == entity.id else rel.source_id
                neighbor = self.get_entity(neighbor_id)
                neighbor_info = {
                    "entity_id": neighbor_id,
                    "name": neighbor.name if neighbor else "",
                    "type": neighbor.type if neighbor else "",
                    "relation": rel.type,
                    "relation_direction": "outgoing" if rel.source_id == entity.id else "incoming",
                }
                neighbors.append(neighbor_info)
                entity_ids.add(neighbor_id)

                # Collect unique relations for the summary
                rel_key = tuple(sorted([rel.source_id, rel.target_id]) + [rel.type])
                all_relations.append({
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "type": rel.type,
                })

            enriched_entities.append({
                "entity_id": entity.id,
                "name": entity.name,
                "type": entity.type,
                "importance": entity.importance,
                "properties": entity.properties,
                "neighbors": neighbors,
            })

        # Deduplicate relations
        seen_rels: set = set()
        unique_relations = []
        for rel in all_relations:
            key = (rel["source_id"], rel["target_id"], rel["type"])
            if key not in seen_rels:
                seen_rels.add(key)
                # Add names for readability
                src = self.get_entity(rel["source_id"])
                tgt = self.get_entity(rel["target_id"])
                unique_relations.append({
                    **rel,
                    "source_name": src.name if src else rel["source_id"],
                    "target_name": tgt.name if tgt else rel["target_id"],
                })

        return {
            "relevant_entities": enriched_entities,
            "relations": unique_relations,
            "total_entities": len(entity_ids),
            "query": query,
        }

    # ------------------------------------------------------------------
    # Entity extraction from conversations
    # ------------------------------------------------------------------

    def extract_from_conversation(
        self,
        messages: List[Dict[str, str]],
        session_id: str = "",
    ) -> List[Entity]:
        """Extract entities and relations from a conversation using the local router model.

        This method performs a **lazy import** of ``panda.router`` to avoid
        circular import issues.  The router must have been initialised and
        its underlying Llama model must be ``LOADED``.

        Args:
            messages:    List of message dicts with ``role`` and ``content`` keys.
            session_id:  Optional session identifier for tracking.

        Returns:
            List of extracted (and upserted) ``Entity`` dataclasses.
        """
        self._assert_initialised()

        # Lazy import to avoid circular dependency
        from panda.router import get_router, RouterStatus

        # Try to get the global router singleton
        router = self._router
        if router is None:
            try:
                router = get_router()
            except ValueError:
                logger.warning(
                    "No router available for entity extraction. "
                    "Pass a router to MemoryGraph or initialise the router singleton first."
                )
                return []

        # Check router status
        if router.status != RouterStatus.LOADED:
            logger.warning(
                "Router not loaded (status=%s). Cannot extract entities.",
                router.status.value,
            )
            return []

        # Build conversation text
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content}")
        messages_text = "\n".join(lines)

        # Build prompt
        prompt = EXTRACTION_PROMPT.format(messages_text=messages_text)

        # Run inference via the router's Llama instance
        llm = getattr(router, "_llm", None)
        if llm is None:
            logger.error("Router has no _llm attribute — cannot extract entities.")
            return []

        try:
            output = llm(
                prompt,
                max_tokens=1024,
                temperature=0.1,
                top_p=0.95,
                stop=["<|im_end|>", "<|endoftext|>"],
                echo=False,
            )
        except Exception:
            logger.exception("LLM inference failed during entity extraction")
            return []

        # Extract generated text
        text = ""
        if isinstance(output, dict):
            text = output.get("choices", [{}])[0].get("text", "")
        elif isinstance(output, str):
            text = output

        logger.debug("Entity extraction raw output: %s", text[:512])

        # Parse JSON
        parsed = _extract_json(text)
        if parsed is None:
            logger.warning("Failed to parse entity extraction JSON from model output")
            return []

        entities_data = parsed.get("entities", [])
        relations_data = parsed.get("relations", [])

        if not entities_data:
            logger.debug("No entities extracted from conversation (session=%s)", session_id)
            return []

        # --- Upsert entities ---
        extracted_entities: List[Entity] = []
        name_to_id: Dict[str, str] = {}

        for ent in entities_data:
            name = str(ent.get("name", "")).strip()
            if not name:
                continue
            ent_type = str(ent.get("type", "concept")).strip()
            props = ent.get("properties", {})
            if not isinstance(props, dict):
                props = {}
            importance = float(ent.get("importance", 0.5))

            # Generate deterministic ID
            entity_id = _make_entity_id(name, ent_type)
            name_to_id[name] = entity_id

            entity = self.upsert_entity(
                entity_id=entity_id,
                name=name,
                type=ent_type,
                properties=props,
                importance=importance,
            )
            extracted_entities.append(entity)

        # --- Upsert relations ---
        for rel in relations_data:
            source_name = str(rel.get("source_name", "")).strip()
            target_name = str(rel.get("target_name", "")).strip()
            rel_type = str(rel.get("type", "related_to")).strip()
            props = rel.get("properties", {})
            if not isinstance(props, dict):
                props = {}

            source_id = name_to_id.get(source_name)
            target_id = name_to_id.get(target_name)

            if not source_id or not target_id:
                logger.debug(
                    "Skipping relation %s -> %s: entity not found in extracted set",
                    source_name,
                    target_name,
                )
                continue

            try:
                self.add_relation(
                    source_id=source_id,
                    target_id=target_id,
                    rel_type=rel_type,
                    properties=props,
                )
            except ValueError as exc:
                logger.debug("Skipping relation: %s", exc)

        logger.info(
            "Extracted %d entities and %d relations from conversation (session=%s)",
            len(extracted_entities),
            len(relations_data),
            session_id,
        )

        return extracted_entities

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics about the memory graph."""
        with self._graph_lock:
            node_count = self._graph.number_of_nodes()
            edge_count = self._graph.number_of_edges()

        # Count by type
        type_counts: Dict[str, int] = {}
        with self._graph_lock:
            for _, attrs in self._graph.nodes(data=True):
                t = attrs.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "total_entities": node_count,
            "total_relations": edge_count,
            "entity_types": type_counts,
            "persist_dir": str(self._persist_dir),
            "graph_path": str(self._graph_path),
            "embedding_model": self._embedding_model_name,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_initialised(self) -> None:
        """Raise RuntimeError if not yet initialised."""
        if not self._initialized:
            raise RuntimeError("MemoryGraph not initialised. Call .initialize() first.")

    def _embed(self, name: str, entity_type: str, properties: Dict[str, Any]) -> List[float]:
        """Generate an embedding vector for an entity.

        The embedding is computed from a concatenation of name, type, and
        JSON-serialised properties for maximum semantic coverage.
        """
        if self._embedding_model is None:
            raise RuntimeError("Embedding model not loaded.")

        props_json = json.dumps(properties, ensure_ascii=False, default=str)
        text = f"{name} [{entity_type}]: {props_json}" if props_json.strip() != "{}" else f"{name} [{entity_type}]"

        embedding = self._embedding_model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def _embed_query(self, query: str) -> List[float]:
        """Generate an embedding vector for a search query."""
        if self._embedding_model is None:
            raise RuntimeError("Embedding model not loaded.")

        embedding = self._embedding_model.encode(query, normalize_embeddings=True)
        return embedding.tolist()

    def _chroma_upsert(self, entity: Entity) -> None:
        """Insert or update an entity's vector in ChromaDB."""
        if self._collection is None:
            logger.warning("ChromaDB collection not available — skipping upsert for %s", entity.id)
            return

        metadata = {
            "name": entity.name,
            "type": entity.type,
            "properties": json.dumps(entity.properties, ensure_ascii=False, default=str),
            "importance": entity.importance,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

        with self._chroma_lock:
            try:
                self._collection.upsert(
                    ids=[entity.id],
                    embeddings=[entity.embedding],
                    metadatas=[metadata],
                )
            except Exception as exc:
                logger.error("ChromaDB upsert failed for entity %s: %s", entity.id, exc)


# ---------------------------------------------------------------------------
# Convenience: create from PandaConfig
# ---------------------------------------------------------------------------


def create_memory_from_config(
    config: Optional[Any] = None,
    *,
    graph_path: Optional[str] = None,
    router: Optional[Any] = None,
) -> MemoryGraph:
    """Factory that creates a ``MemoryGraph`` from a ``PandaConfig`` object.

    Args:
        config:     A ``PandaConfig`` instance.  If ``None``, loads from default path.
        graph_path: Override the graph JSON path.
        router:     Optional PandaRouter for entity extraction.

    Returns:
        An *initialised* ``MemoryGraph`` ready for use.
    """
    if config is None:
        from panda.config import PandaConfig
        config = PandaConfig.load()

    memory = MemoryGraph(
        persist_dir=config.memory.persist_dir,
        embedding_model=config.memory.embedding_model,
        search_top_k=config.memory.search_top_k,
        search_threshold=config.memory.search_threshold,
        graph_path=graph_path or "panda_data/memory/graph.json",
        router=router,
    )
    memory.initialize()
    return memory
