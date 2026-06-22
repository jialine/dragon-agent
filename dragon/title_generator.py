"""
Auto-generate short session titles from the first user/assistant exchange.

Uses a lightweight LLM call (cheapest available provider/model) to generate
a concise, descriptive title for a conversation. Runs asynchronously so it
never adds latency to the user-facing reply.

Titles are cached per-session to avoid re-generation.

Usage::

    from dragon.title_generator import TitleGenerator
    gen = TitleGenerator(provider_registry)
    title = await gen.generate(user_message, assistant_response)
    # Or sync: title = gen.generate_sync(user_message, assistant_response)
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("dragon.title_generator")

# ────────────────────────────────────────────────────────────────────
# Title generation prompt
# ────────────────────────────────────────────────────────────────────

_TITLE_SYSTEM_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation "
    "that starts with the following exchange. The title should capture "
    "the main topic or intent. Return ONLY the title text, nothing else. "
    "No quotes, no punctuation at the end, no prefixes like 'Title: '. "
    "The title should be at most 40 characters. "
    "You may generate titles in Chinese or English depending on the "
    "conversation language."
)

# ────────────────────────────────────────────────────────────────────
# Title Generator
# ────────────────────────────────────────────────────────────────────


class TitleGenerator:
    """Auto-generates session titles from the first exchange.

    Uses the cheapest available provider for cost efficiency.
    Caches generated titles to avoid redundant API calls.

    Usage::

        gen = TitleGenerator(provider_registry)
        title = await gen.generate("What is AI?", "AI is...")

        # Auto-title after first exchange
        gen.auto_title(session_id, first_user_msg, first_assistant_msg)
    """

    # Preferred providers for title generation (cheapest first)
    _CHEAP_PROVIDER_ORDER = [
        "deepseek",       # ~$0.27/M input, $1.10/M output
        "openai",         # Use gpt-4o-mini: $0.15/M input
        "google",         # Gemini Flash: $0.15/M input
        "groq",           # Fast + cheap
        "together",       # Competitive pricing
        "mistral",        # Small model pricing
        "cohere",         # Command-R
        "openrouter",     # Route to cheapest
    ]

    _CHEAP_MODELS = {
        "deepseek": "deepseek-chat",
        "openai": "gpt-4o-mini",
        "google": "gemini-2.0-flash",
        "groq": "llama-4-maverick-17b-128e-instruct",
        "together": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "mistral": "mistral-small-latest",
        "cohere": "command-r",
        "openrouter": "openai/gpt-4o-mini",
    }

    def __init__(
        self,
        provider_registry=None,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_chars: int = 40,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the title generator.

        Args:
            provider_registry: A ProviderRegistry instance for making LLM calls.
            provider: Preferred provider for title generation.
            model: Preferred model for title generation.
            max_chars: Maximum title length in characters.
            timeout: Timeout for the LLM call in seconds.
        """
        self._registry = provider_registry
        self._provider = provider
        self._model = model
        self._max_chars = max_chars
        self._timeout = timeout
        self._cache: Dict[str, str] = {}
        self._cache_lock = threading.Lock()

    @property
    def registry(self):
        """Get or lazy-import the provider registry."""
        if self._registry is not None:
            return self._registry
        # Lazy import to avoid circular dependency
        try:
            from dragon.provider import auto_setup_providers
            self._registry = auto_setup_providers()
        except Exception as exc:
            logger.debug("Could not auto-setup providers: %s", exc)
            return None
        return self._registry

    def _resolve_provider_model(self) -> tuple[str, str]:
        """Resolve the best provider and model for title generation."""
        if self._provider and self._model:
            return self._provider, self._model

        registry = self.registry
        if registry is None:
            return ("openai", "gpt-4o-mini")

        # If user specified provider, use it with appropriate model
        if self._provider:
            model = self._model or self._CHEAP_MODELS.get(self._provider, "gpt-4o-mini")
            return self._provider, model

        # Find first available provider from the cheap list
        for provider in self._CHEAP_PROVIDER_ORDER:
            model = self._CHEAP_MODELS.get(provider, "")
            if registry.get_provider(provider) is not None:
                return provider, model

        # Fallback
        return ("openai", "gpt-4o-mini")

    # ── Cache Helpers ──────────────────────────────────────────────

    def _cache_key(self, user_msg: str, assistant_msg: str) -> str:
        """Generate a cache key from the first exchange."""
        # Use first 200 chars of each for the key
        u = (user_msg or "")[:200]
        a = (assistant_msg or "")[:200]
        return f"{hash(u + '||' + a)}"

    def _get_cached(self, user_msg: str, assistant_msg: str) -> Optional[str]:
        """Get a cached title."""
        with self._cache_lock:
            return self._cache.get(self._cache_key(user_msg, assistant_msg))

    def _set_cache(self, user_msg: str, assistant_msg: str, title: str) -> None:
        """Cache a generated title."""
        with self._cache_lock:
            self._cache[self._cache_key(user_msg, assistant_msg)] = title

    # ── Title Generation ───────────────────────────────────────────

    def _clean_title(self, title: str) -> Optional[str]:
        """Clean and validate a generated title."""
        if not title or not title.strip():
            return None

        title = title.strip()

        # Remove quotes
        title = title.strip('"\'')

        # Remove common prefixes
        for prefix in ("Title:", "标题:", "Title：", "标题：", "title:"):
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()

        # Remove trailing punctuation
        title = re.sub(r'[。，！？,.!?;；:：]+$', '', title)

        # Remove markdown formatting
        title = title.replace("**", "").replace("__", "").replace("`", "")

        # Enforce max length
        if len(title) > self._max_chars:
            title = title[: self._max_chars - 3] + "..."

        # Reject empty or meaningless titles
        if not title or len(title) < 2:
            return None

        # Reject titles that are just the truncated message
        return title

    async def generate(
        self,
        user_message: str,
        assistant_response: str,
        *,
        skip_cache: bool = False,
    ) -> Optional[str]:
        """Generate a session title from the first exchange.

        Args:
            user_message: The first user message in the conversation.
            assistant_response: The first assistant response.
            skip_cache: If True, bypass the title cache.

        Returns:
            Generated title string, or None on failure.
        """
        if not user_message and not assistant_response:
            return None

        # Check cache
        if not skip_cache:
            cached = self._get_cached(user_message, assistant_response)
            if cached:
                return cached

        # Prepare messages
        user_snippet = (user_message or "")[:500]
        assistant_snippet = (assistant_response or "")[:500]

        messages = [
            {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}",
            },
        ]

        try:
            provider, model = self._resolve_provider_model()
            registry = self.registry

            if registry is None:
                logger.warning("No provider registry available for title generation")
                return None

            result = await registry.call(
                provider,
                model,
                messages=messages,
                temperature=0.3,
                max_tokens=128,
            )

            title = self._clean_title(result.content)
            if title:
                self._set_cache(user_message, assistant_response, title)
                logger.debug("Generated title: %s", title)
                return title
            else:
                logger.debug("Title generation produced empty/invalid result")
                return None

        except Exception as exc:
            logger.warning("Title generation failed: %s", exc)
            return None

    def generate_sync(
        self,
        user_message: str,
        assistant_response: str,
        *,
        skip_cache: bool = False,
    ) -> Optional[str]:
        """Synchronous version of generate().

        Blocks until title is generated or fails.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In a running event loop, create a new one in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.generate(user_message, assistant_response, skip_cache=skip_cache),
                    )
                    return future.result(timeout=self._timeout + 5)
            else:
                return loop.run_until_complete(
                    self.generate(user_message, assistant_response, skip_cache=skip_cache)
                )
        except RuntimeError:
            return asyncio.run(
                self.generate(user_message, assistant_response, skip_cache=skip_cache)
            )

    # ── Auto-Title (background) ────────────────────────────────────

    def auto_title(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        session_store=None,
        on_title: Optional[Callable] = None,
    ) -> None:
        """Generate and set a session title in the background.

        Args:
            session_id: The session ID to title.
            user_message: First user message.
            assistant_response: First assistant response.
            session_store: SessionStore instance to update.
            on_title: Optional callback(title: str) when title is set.
        """
        if not session_id or not session_store:
            return

        # Check if session already has a meaningful title
        try:
            existing = session_store.get(session_id)
            if existing and existing.title and existing.title not in ("New Session", ""):
                # Already titled (user-set or previously auto-generated)
                return
        except Exception:
            pass

        def _do_title():
            try:
                title = self.generate_sync(user_message, assistant_response)
                if not title:
                    return

                # Update the session title
                try:
                    session_store.update_meta(session_id, title=title)
                    logger.info("Auto-titled session %s: %s", session_id, title)
                except Exception as exc:
                    logger.debug("Failed to set auto-title: %s", exc)
                    return

                if on_title:
                    try:
                        on_title(title)
                    except Exception:
                        logger.debug("on_title callback failed", exc_info=True)

            except Exception as exc:
                logger.debug("Auto-title thread failed: %s", exc)

        thread = threading.Thread(
            target=_do_title,
            daemon=True,
            name=f"title-gen-{session_id[:8]}",
        )
        thread.start()

    def clear_cache(self) -> None:
        """Clear the title cache."""
        with self._cache_lock:
            self._cache.clear()


# ────────────────────────────────────────────────────────────────────
# Convenience function
# ────────────────────────────────────────────────────────────────────


def generate_title(
    user_message: str,
    assistant_response: str,
    provider_registry=None,
    max_chars: int = 40,
) -> Optional[str]:
    """Generate a session title (synchronous convenience function).

    Args:
        user_message: First user message.
        assistant_response: First assistant response.
        provider_registry: Optional ProviderRegistry instance.
        max_chars: Maximum title length.

    Returns:
        Generated title or None.
    """
    gen = TitleGenerator(provider_registry=provider_registry, max_chars=max_chars)
    return gen.generate_sync(user_message, assistant_response)


__all__ = [
    "TitleGenerator",
    "generate_title",
]
