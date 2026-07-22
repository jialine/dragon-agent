"""
Dragon Prompt Builder — Dynamic system prompt construction with caching.

Assembles system prompts from:
  - Base agent identity (configurable)
  - Industry-specific context (legal, medical, finance, etc.)
  - Skill hints (auto-detected from the skill engine)
  - Compressed conversation context
  - Platform/transport-specific guidance

Supports prompt caching for OpenAI/Anthropic prompt caching APIs to
reduce API costs by reusing cached system-prompt prefixes.

Usage::

    from dragon.prompt_builder import PromptBuilder

    builder = PromptBuilder(identity="You are Dragon Agent...")
    prompt, cache_key = builder.build(
        industry="legal",
        context="User is discussing contract law.",
        skills=["contract_review", "case_lookup"],
    )
    # Use cache_key for prompt-caching API calls
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dragon.identity import get_identity as _get_dragon_identity

from pydantic import BaseModel, Field

logger = logging.getLogger("dragon.prompt_builder")


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

DEFAULT_DRAGON_IDENTITY = (
    "You are Dragon Agent, an intelligent AI assistant. "
    "You are helpful, knowledgeable, and direct. "
    "You assist users with a wide range of tasks including answering questions, "
    "writing and editing code, analyzing information, creative work, "
    "and executing actions via your tools. "
    "You communicate clearly, admit uncertainty when appropriate, and "
    "prioritize being genuinely useful over being verbose."
)

DEFAULT_HELP_GUIDANCE = (
    "If the user asks about Dragon Agent itself, answer concisely "
    "and point them to documentation."
)

DEFAULT_TOOL_GUIDANCE = (
    "# Tool-use enforcement\n"
    "You MUST use your tools to take action — do not describe what you would do "
    "without actually doing it. Never end your turn with a promise of future "
    "action — execute it now. Every response should either contain tool calls "
    "that make progress, or deliver a final result."
)

INDUSTRY_PREAMBLES: Dict[str, str] = {
    "legal": "You are assisting with a legal question. Be precise, cite sources "
             "when possible, and note when professional legal advice is needed.",
    "medical": "You are assisting with a medical question. Prioritize accuracy. "
               "Always recommend consulting a licensed healthcare professional.",
    "finance": "You are assisting with a financial question. Provide balanced "
               "analysis and note that this is not professional financial advice.",
    "code": "You are a software engineering assistant. Write production-quality "
            "code. Prefer clarity over cleverness.",
    "general": "You are a general-purpose assistant. Be direct and helpful.",
}


# ────────────────────────────────────────────────────────────────────
# Pydantic Models
# ────────────────────────────────────────────────────────────────────


class CachePolicy(str, Enum):
    """Prompt caching strategies."""

    DISABLED = "disabled"
    """No caching; full prompt sent every request."""

    PREFIX = "prefix"
    """Cache the system prompt as a prefix (OpenAI automatic caching)."""

    EPHEMERAL = "ephemeral"
    """5-minute TTL cache for frequently repeated prompts."""

    PERSISTENT = "persistent"
    """Long-lived cache with explicit invalidation."""


class CacheEntry(BaseModel):
    """A single cache entry for a built prompt."""

    key: str
    """Deterministic cache key (SHA-256 based)."""

    system_prompt: str
    """The cached system prompt text."""

    created_at: float = Field(default_factory=time.monotonic)
    """Timestamp when this entry was created."""

    last_accessed: float = Field(default_factory=time.monotonic)
    """Timestamp of last cache hit."""

    hit_count: int = 0
    """Number of times this entry was served from cache."""

    ttl_seconds: Optional[float] = None
    """Time-to-live in seconds. None = never expires by time."""

    @property
    def expired(self) -> bool:
        """Check if this cache entry has exceeded its TTL."""
        if self.ttl_seconds is None:
            return False
        return (time.monotonic() - self.created_at) > self.ttl_seconds


class BuiltPrompt(BaseModel):
    """Result of building a system prompt.

    Attributes:
        system_prompt: The assembled system prompt text.
        cache_key: Deterministic key for prompt caching.
        sections: Dictionary of named sections included in the prompt.
        tokens_estimate: Estimated token count of the system prompt.
        cache_hit: True if this prompt was served from cache.
    """

    system_prompt: str
    cache_key: str
    sections: Dict[str, str] = Field(default_factory=dict)
    tokens_estimate: int = 0
    cache_hit: bool = False


# ────────────────────────────────────────────────────────────────────
# Template Engine (Lightweight Jinja2-style)
# ────────────────────────────────────────────────────────────────────


class MiniTemplate:
    """Lightweight template engine for variable interpolation.

    Supports Jinja2-style ``{{ variable }}`` syntax without external
    dependencies. Replaces variables from a context dictionary.

    Usage::

        tpl = MiniTemplate("You are a {{ role }} in {{ industry }}.")
        result = tpl.render(role="expert", industry="law")
        # "You are a expert in law."
    """

    _VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def __init__(self, template: str) -> None:
        self.template = template
        self._variables = set(self._VAR_PATTERN.findall(template))

    def render(self, **context: str) -> str:
        """Render the template with the given variable context.

        Args:
            **context: Variable name to value mappings.

        Returns:
            Rendered template string.
        """
        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            return str(context.get(var_name, match.group(0)))

        return self._VAR_PATTERN.sub(_replace, self.template)

    @property
    def variables(self) -> set:
        """Set of variable names referenced in the template."""
        return self._variables


# ────────────────────────────────────────────────────────────────────
# Prompt Builder
# ────────────────────────────────────────────────────────────────────


class PromptBuilder:
    """Dynamic system prompt constructor with caching.

    Assembles a system prompt from modular sections and supports
    prompt caching to reduce API costs.

    Parameters
    ----------
    identity : str
        Base agent identity text.
    tool_guidance : str
        Guidance for tool use behavior.
    help_guidance : str
        Guidance for help/documentation queries.
    industry_preambles : dict
        Industry-specific preamble text indexed by industry key.
    cache_policy : CachePolicy
        Caching strategy to use.
    cache_ttl_minutes : float
        TTL in minutes for ephemeral cache entries.
    max_cache_entries : int
        Maximum number of cache entries before eviction.
    """

    # Cache TTL defaults by policy
    _CACHE_TTL = {
        CachePolicy.EPHEMERAL: 5 * 60,     # 5 minutes
        CachePolicy.PERSISTENT: 24 * 3600,  # 24 hours
    }

    def __init__(
        self,
        identity: str = DEFAULT_DRAGON_IDENTITY,
        tool_guidance: str = DEFAULT_TOOL_GUIDANCE,
        help_guidance: str = DEFAULT_HELP_GUIDANCE,
        industry_preambles: Optional[Dict[str, str]] = None,
        cache_policy: CachePolicy = CachePolicy.PREFIX,
        cache_ttl_minutes: float = 5.0,
        max_cache_entries: int = 100,
    ) -> None:
        self.identity = identity
        # Inject Dragon instance ID into system prompt
        try:
            dragon_id = _get_dragon_identity().id
            if dragon_id and dragon_id not in self.identity:
                self.identity = f"[Instance: {dragon_id}]\n\n" + self.identity
        except Exception:
            pass  # identity module not available
        self.tool_guidance = tool_guidance
        self.help_guidance = help_guidance
        self.industry_preambles = industry_preambles or dict(INDUSTRY_PREAMBLES)
        self.cache_policy = cache_policy
        self.cache_ttl_seconds = cache_ttl_minutes * 60.0
        self.max_cache_entries = max_cache_entries

        # In-memory LRU-style cache
        self._cache: Dict[str, CacheEntry] = {}

        logger.info(
            "PromptBuilder ready: cache=%s max_entries=%d ttl=%.0fs",
            cache_policy.value, max_cache_entries, self.cache_ttl_seconds,
        )

    # ── Public API ──────────────────────────────────────────────────

    def build(
        self,
        industry: str = "general",
        context: str = "",
        skills: Optional[Sequence[str]] = None,
        extra_sections: Optional[Dict[str, str]] = None,
        platform: str = "",
        metadata: Optional[Dict[str, str]] = None,
    ) -> BuiltPrompt:
        """Build a system prompt from modular sections.

        Sections are assembled in this order:
        1. Agent Identity
        2. Platform context (e.g., "You are in a Feishu workspace...")
        3. Industry preamble
        4. Compressed context / knowledge
        5. Skill hints
        6. Tool guidance
        7. Help guidance
        8. Extra custom sections

        Args:
            industry: Target industry key (legal, medical, code, etc.).
            context: Compressed conversation context or reference knowledge.
            skills: List of skill names to include as hints.
            extra_sections: Additional named sections to append.
            platform: Platform name for transport-specific guidance.
            metadata: Key-value metadata (e.g., host info, user home).

        Returns:
            BuiltPrompt with system_prompt, cache_key, and metadata.
        """
        # Generate cache key from inputs
        cache_key = self._make_cache_key(
            industry, context, skills, extra_sections, platform, metadata
        )

        # Check cache
        if self.cache_policy != CachePolicy.DISABLED:
            cached = self._cache_get(cache_key)
            if cached:
                return BuiltPrompt(
                    system_prompt=cached,
                    cache_key=cache_key,
                    cache_hit=True,
                )

        # Build sections
        sections: Dict[str, str] = {}

        # 1. Identity
        sections["identity"] = self.identity

        # 2. Platform context
        if platform:
            sections["platform"] = self._build_platform_section(platform, metadata)

        # 3. Industry preamble
        preamble = self.industry_preambles.get(
            industry, self.industry_preambles.get("general", "")
        )
        if preamble:
            sections["industry"] = preamble

        # 4. Context
        if context:
            sections["context"] = (
                f"## Conversation Context\n{context}"
            )

        # 5. Skill hints
        if skills:
            sections["skills"] = self._build_skills_section(skills)

        # 6. Tool guidance
        if self.tool_guidance:
            sections["tool_guidance"] = self.tool_guidance

        # 7. Help guidance
        if self.help_guidance:
            sections["help"] = self.help_guidance

        # 8. Extra sections
        if extra_sections:
            for name, content in extra_sections.items():
                sections[name] = content

        # Assemble
        system_prompt = self._assemble(sections)

        # Estimate tokens (rough: 4 chars/token for English)
        tokens_estimate = len(system_prompt) // 4

        # Store in cache
        if self.cache_policy != CachePolicy.DISABLED:
            self._cache_put(cache_key, system_prompt)

        return BuiltPrompt(
            system_prompt=system_prompt,
            cache_key=cache_key,
            sections=sections,
            tokens_estimate=tokens_estimate,
            cache_hit=False,
        )

    def build_with_template(
        self,
        template: str,
        industry: str = "general",
        context: str = "",
        skills: Optional[Sequence[str]] = None,
        **variables,
    ) -> Tuple[str, str]:
        """Build a prompt from a custom template with variable interpolation.

        Args:
            template: Template string with ``{{ var }}`` placeholders.
            industry: Industry key for preamble injection.
            context: Compressed context for ``{{ context }}`` variable.
            skills: Skill names for ``{{ skills }}`` variable.
            **variables: Additional template variables.

        Returns:
            Tuple of (rendered_prompt, cache_key).
        """
        tpl = MiniTemplate(template)

        # Build standard variables
        var_context: Dict[str, str] = {
            "identity": self.identity,
            "industry": industry,
            "context": context or "",
            "skills": self._build_skills_text(skills) if skills else "",
            "industry_preamble": self.industry_preambles.get(
                industry, self.industry_preambles.get("general", "")
            ),
            "tool_guidance": self.tool_guidance,
            "help_guidance": self.help_guidance,
        }
        var_context.update(variables)

        rendered = tpl.render(**var_context)

        # Cache key from template + variables
        cache_key = self._make_cache_key(
            f"tpl:{template}", rendered, skills or [], None, "", {}
        )

        if self.cache_policy != CachePolicy.DISABLED:
            self._cache_put(cache_key, rendered)

        return rendered, cache_key

    def invalidate_cache(self, cache_key: Optional[str] = None) -> int:
        """Invalidate cache entries.

        Args:
            cache_key: Specific key to invalidate. If None, clears all.

        Returns:
            Number of entries removed.
        """
        if cache_key:
            removed = 1 if self._cache.pop(cache_key, None) else 0
            if removed:
                logger.debug("Cache invalidated: %s", cache_key)
            return removed

        count = len(self._cache)
        self._cache.clear()
        logger.info("Cache cleared: %d entries removed", count)
        return count

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        total_hits = sum(e.hit_count for e in self._cache.values())
        return {
            "policy": self.cache_policy.value,
            "entries": len(self._cache),
            "max_entries": self.max_cache_entries,
            "total_hits": total_hits,
            "ttl_seconds": self.cache_ttl_seconds,
        }

    # ── Private: Section Builders ───────────────────────────────────

    @staticmethod
    def _build_platform_section(
        platform: str, metadata: Optional[Dict[str, str]]
    ) -> str:
        """Build platform-specific context section."""
        lines = [
            f"You are in a {platform} workspace communicating with your user. ",
        ]

        if platform in ("feishu", "lark"):
            lines.append(
                "Feishu renders Markdown in messages — bold, italic, code blocks, "
                "and links are supported. You can send media files natively: include "
                "MEDIA:/absolute/path/to/file in your response. Images (.jpg, .png, "
                ".webp) are uploaded and displayed inline, audio files as voice "
                "messages, and other files as attachments."
            )

        if metadata:
            host = metadata.get("host", "")
            home = metadata.get("user_home", "")
            if host:
                lines.append(f"\nHost: {host}")
            if home:
                lines.append(f"User home directory: {home}")
            cwd = metadata.get("cwd", "")
            if cwd:
                lines.append(f"Current working directory: {cwd}")

        return "\n".join(lines)

    @staticmethod
    def _build_skills_section(skills: Sequence[str]) -> str:
        """Build a skills hints section.

        Lists available skills with brief guidance on when to use them.
        """
        if not skills:
            return ""

        skills_list = ", ".join(f"`{s}`" for s in sorted(skills))
        return (
            "## Available Skills\n"
            f"Relevant skills available: {skills_list}.\n"
            "Use skill_view(name='<skill>') to load a skill for details."
        )

    @staticmethod
    def _build_skills_text(skills: Optional[Sequence[str]]) -> str:
        """Build a compact skills text for template interpolation."""
        if not skills:
            return ""
        return ", ".join(sorted(skills))

    # ── Private: Assembly ───────────────────────────────────────────

    @staticmethod
    def _assemble(sections: Dict[str, str]) -> str:
        """Assemble named sections into a single system prompt.

        Sections are joined with double newlines. The order in the dict
        is preserved (Python 3.7+ dicts maintain insertion order).
        """
        parts = []
        for name, content in sections.items():
            if content:
                parts.append(content)

        return "\n\n".join(parts)

    # ── Private: Caching ────────────────────────────────────────────

    def _make_cache_key(
        self,
        industry: str,
        context: str,
        skills: Optional[Sequence[str]],
        extra_sections: Optional[Dict[str, str]],
        platform: str,
        metadata: Optional[Dict[str, str]],
    ) -> str:
        """Create a deterministic cache key from all prompt inputs."""
        hasher = hashlib.sha256()
        hasher.update(industry.encode())
        hasher.update((context or "")[:500].encode())
        if skills:
            hasher.update(",".join(sorted(skills)).encode())
        if extra_sections:
            for k, v in sorted(extra_sections.items()):
                hasher.update(f"{k}={v[:100]}".encode())
        hasher.update(platform.encode())
        if metadata:
            for k, v in sorted(metadata.items()):
                hasher.update(f"{k}={v}".encode())
        hasher.update(self.identity[:200].encode())
        return hasher.hexdigest()[:32]

    def _cache_get(self, key: str) -> Optional[str]:
        """Retrieve a cached prompt if valid.

        Returns:
            Cached prompt string, or None if expired/missing.
        """
        entry = self._cache.get(key)
        if entry is None:
            return None

        if entry.expired:
            del self._cache[key]
            logger.debug("Cache entry expired: %s", key)
            return None

        entry.last_accessed = time.monotonic()
        entry.hit_count += 1
        logger.debug("Cache hit: %s (hits=%d)", key, entry.hit_count)
        return entry.system_prompt

    def _cache_put(self, key: str, prompt: str) -> None:
        """Store a prompt in the cache with eviction if needed."""
        # Evict oldest entries if at capacity
        if len(self._cache) >= self.max_cache_entries and key not in self._cache:
            self._evict_lru()

        ttl = self._CACHE_TTL.get(self.cache_policy)
        self._cache[key] = CacheEntry(
            key=key,
            system_prompt=prompt,
            created_at=time.monotonic(),
            last_accessed=time.monotonic(),
            ttl_seconds=ttl,
        )
        logger.debug("Cache stored: %s (ttl=%s)", key, ttl)

    def _evict_lru(self) -> None:
        """Evict the least-recently-used cache entry."""
        if not self._cache:
            return
        lru_key = min(self._cache, key=lambda k: self._cache[k].last_accessed)
        del self._cache[lru_key]
        logger.debug("Cache evicted LRU: %s", lru_key)
