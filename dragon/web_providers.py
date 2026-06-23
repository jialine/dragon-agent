"""Pluggable web search providers for Dragon Agent.

Supports multiple search backends:
- DuckDuckGo (no API key required, always available)
- Brave Search API (set BRAVE_API_KEY env var)
- SearXNG self-hosted (set SEARXNG_URL env var)

The WebSearchRouter auto-registers available providers and falls back
gracefully if a provider fails.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

import httpx

logger = logging.getLogger("dragon.web_providers")


# ────────────────────────────────────────────────────────────────────
# Data types
# ────────────────────────────────────────────────────────────────────


@dataclass
class WebSearchResult:
    """A single search result from any provider."""

    title: str
    url: str
    snippet: str


# ────────────────────────────────────────────────────────────────────
# Provider abstraction
# ────────────────────────────────────────────────────────────────────


class SearchProvider(ABC):
    """Abstract base for a search provider."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> List[WebSearchResult]:
        """Execute a web search and return results."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider name (e.g. 'brave', 'searxng', 'duckduckgo')."""
        ...


# ────────────────────────────────────────────────────────────────────
# Brave Search provider
# ────────────────────────────────────────────────────────────────────


class BraveSearchProvider(SearchProvider):
    """Brave Search API provider.

    Free tier: 2,000 queries/month.
    Set ``BRAVE_API_KEY`` environment variable to enable.
    Docs: https://api.search.brave.com/app/documentation/web-search/
    """

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("BRAVE_API_KEY", "")

    @property
    def name(self) -> str:
        return "brave"

    async def search(self, query: str, max_results: int = 10) -> List[WebSearchResult]:
        if not self._api_key:
            logger.warning("BraveSearchProvider: BRAVE_API_KEY not set")
            return []

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        params = {"q": query, "count": min(max_results, 20)}

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(self.BASE_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        web = data.get("web", {})
        raw_results = web.get("results", [])

        result_list: List[WebSearchResult] = []
        for item in raw_results[:max_results]:
            result_list.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )

        return result_list


# ────────────────────────────────────────────────────────────────────
# SearXNG provider
# ────────────────────────────────────────────────────────────────────


class SearXNGSProvider(SearchProvider):
    """Self-hosted SearXNG instance provider.

    Set ``SEARXNG_URL`` environment variable (e.g. ``https://search.example.com``).
    Does NOT include a trailing ``/search`` — the provider appends it.
    Docs: https://docs.searxng.org/dev/search_api.html
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or os.getenv("SEARXNG_URL", "")).rstrip("/")

    @property
    def name(self) -> str:
        return "searxng"

    async def search(self, query: str, max_results: int = 10) -> List[WebSearchResult]:
        if not self._base_url:
            logger.warning("SearXNGSProvider: SEARXNG_URL not set")
            return []

        url = f"{self._base_url}/search"
        params = {"q": query, "format": "json"}

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        raw_results = data.get("results", [])

        result_list: List[WebSearchResult] = []
        for item in raw_results[:max_results]:
            result_list.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", "") or item.get("snippet", ""),
                )
            )

        return result_list


# ────────────────────────────────────────────────────────────────────
# DuckDuckGo provider (HTML scraping, no API key needed)
# ────────────────────────────────────────────────────────────────────


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo HTML-scraping provider.

    No API key required.  Uses the legacy HTML endpoint with
    regex-based result extraction.  Falls back to the Lite version
    when the standard HTML endpoint returns no results.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
                follow_redirects=True,
            )
        return self._client

    async def search(self, query: str, max_results: int = 10) -> List[WebSearchResult]:
        client = await self._get_client()

        # Try DuckDuckGo HTML search (POST)
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
        )

        results: List[WebSearchResult] = []
        if resp.status_code == 200:
            results = _parse_duckduckgo_html(resp.text, max_results)

        # Fall back to DuckDuckGo Lite
        if not results:
            resp2 = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
            )
            if resp2.status_code == 200:
                results = _parse_duckduckgo_lite(resp2.text, max_results)

        return results

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ────────────────────────────────────────────────────────────────────
# HTML parsers (kept as module-level functions so they can be reused)
# ────────────────────────────────────────────────────────────────────


def _parse_duckduckgo_html(html: str, max_results: int) -> List[WebSearchResult]:
    """Extract results from DuckDuckGo HTML response using regex."""
    results: List[WebSearchResult] = []

    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (url, title) in enumerate(links):
        if len(results) >= max_results:
            break
        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        if title_clean and url.startswith("http"):
            results.append(
                WebSearchResult(
                    title=title_clean,
                    url=url,
                    snippet=snippet,
                )
            )

    return results


def _parse_duckduckgo_lite(html: str, max_results: int) -> List[WebSearchResult]:
    """Extract results from DuckDuckGo Lite HTML (fallback)."""
    results: List[WebSearchResult] = []

    row_pattern = re.compile(
        r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        re.DOTALL,
    )

    links = row_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    seen_urls: set = set()
    for i, (url, title) in enumerate(links):
        if len(results) >= max_results:
            break
        if "duckduckgo.com" in url or url in seen_urls:
            continue
        seen_urls.add(url)

        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        for j in range(i, min(i + 2, len(snippets))):
            s = re.sub(r"<[^>]+>", "", snippets[j]).strip()
            if s and s != title_clean:
                snippet = s
                break

        if title_clean and url.startswith("http"):
            results.append(
                WebSearchResult(
                    title=title_clean,
                    url=url,
                    snippet=snippet,
                )
            )

    return results


# ────────────────────────────────────────────────────────────────────
# WebSearchRouter — auto-register + fallback
# ────────────────────────────────────────────────────────────────────


class WebSearchRouter:
    """Routes queries to the best available provider with fallback.

    Auto-registers providers based on environment variables:
    - DuckDuckGo: always available (no key)
    - Brave: enabled when ``BRAVE_API_KEY`` is set
    - SearXNG: enabled when ``SEARXNG_URL`` is set

    Usage::

        router = WebSearchRouter()
        provider_name, results = await router.search("python async")
        # Falls back through all registered providers
    """

    def __init__(self) -> None:
        self.providers: dict[str, SearchProvider] = {}
        self._auto_register()

    def _auto_register(self) -> None:
        """Register all available providers based on environment."""
        # Always register DuckDuckGo (no credentials needed)
        self.providers["duckduckgo"] = DuckDuckGoProvider()

        # Register Brave if API key is set
        if os.getenv("BRAVE_API_KEY"):
            self.providers["brave"] = BraveSearchProvider()
            logger.info("WebSearchRouter: Brave provider registered")

        # Register SearXNG if base URL is set
        if os.getenv("SEARXNG_URL"):
            self.providers["searxng"] = SearXNGSProvider()
            logger.info("WebSearchRouter: SearXNG provider registered")

        logger.info(
            "WebSearchRouter: %d provider(s) available — %s",
            len(self.providers),
            list(self.providers.keys()),
        )

    async def search(
        self,
        query: str,
        max_results: int = 10,
        provider: str | None = None,
    ) -> tuple[str, List[WebSearchResult]]:
        """Execute a search, optionally specifying a provider.

        Args:
            query: Search query string.
            max_results: Max results to return.
            provider: Name of provider to use, or ``None`` to try all.

        Returns:
            Tuple of ``(provider_name, list_of_WebSearchResult)``.
            Provider name is ``"none"`` if all providers failed.
        """
        # Explicit provider requested
        if provider and provider in self.providers:
            try:
                results = await self.providers[provider].search(query, max_results)
                return provider, results
            except Exception as exc:
                logger.warning(
                    "Provider '%s' failed for query '%s': %s",
                    provider, query, exc,
                )
                return provider, []

        # Auto-fallback through all providers (in registration order)
        for name, prov in self.providers.items():
            try:
                results = await prov.search(query, max_results)
                if results:
                    return name, results
            except Exception as exc:
                logger.warning(
                    "Provider '%s' failed for query '%s': %s",
                    name, query, exc,
                )
                continue

        return "none", []

    def list_providers(self) -> list[dict[str, object]]:
        """Return a list of available provider metadata dicts."""
        return [{"name": name, "available": True} for name in self.providers]
