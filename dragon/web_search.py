"""
Dragon Agent — Web Search Tool

Lightweight web search using DuckDuckGo Instant Answer API (no API key required).
Falls back to HTML scraping for richer results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("dragon.web_search")


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str
    relevance: float = 0.0  # 0-1, higher = more relevant
    source: str = "web"


@dataclass
class SearchResponse:
    """Aggregated search results."""

    query: str
    results: List[SearchResult] = field(default_factory=list)
    total_results: int = 0
    latency_ms: float = 0.0
    source: str = "duckduckgo"


class WebSearcher:
    """DuckDuckGo-based web searcher (no API key needed)."""

    def __init__(
        self,
        max_results: int = 5,
        timeout_secs: float = 10.0,
        language: str = "zh-cn",
    ) -> None:
        self.max_results = max_results
        self.timeout_secs = timeout_secs
        self.language = language
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_secs),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
        return self._client

    async def search(self, query: str) -> SearchResponse:
        """Execute a web search and return results."""
        start = time.monotonic()
        try:
            # Primary: DuckDuckGo Instant Answer API
            results = await self._search_duckduckgo(query)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed: %s", exc)
            results = []

        elapsed = (time.monotonic() - start) * 1000
        return SearchResponse(
            query=query,
            results=results[: self.max_results],
            total_results=len(results),
            latency_ms=elapsed,
        )

    async def _search_duckduckgo(self, query: str) -> List[SearchResult]:
        """Search via DuckDuckGo HTML (no official API needed)."""
        client = await self._get_client()

        url = "https://html.duckduckgo.com/html/"
        data = {"q": query, "kl": self.language}

        resp = await client.post(url, data=data)
        resp.raise_for_status()

        results = self._parse_duckduckgo_html(resp.text)
        return results

    @staticmethod
    def _parse_duckduckgo_html(html: str) -> List[SearchResult]:
        """Extract results from DuckDuckGo HTML response."""
        import re

        results: List[SearchResult] = []

        # Match result blocks: <a class="result__a" href="...">title</a>
        # followed by <a class="result__snippet">snippet</a>
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
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

            if title_clean and url.startswith("http"):
                results.append(
                    SearchResult(
                        title=title_clean,
                        url=url,
                        snippet=snippet,
                    )
                )

        return results

    async def verify_claim(
        self, claim: str, top_k: int = 3
    ) -> List[SearchResult]:
        """Search for evidence supporting or contradicting a claim."""
        # Use exact phrase search for precision
        if len(claim) > 80:
            # Truncate long claims to key terms
            query = claim[:80]
        else:
            query = f'"{claim}"'

        response = await self.search(query)

        # Sort by snippet relevance (simple heuristic: snippet length)
        for r in response.results:
            if claim.lower() in (r.title + r.snippet).lower():
                r.relevance = 0.8
            elif any(
                word in (r.title + r.snippet).lower()
                for word in claim.lower().split()[:5]
            ):
                r.relevance = 0.4
            else:
                r.relevance = 0.1

        response.results.sort(key=lambda r: r.relevance, reverse=True)
        return response.results[:top_k]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Convenience function
async def web_search(query: str, max_results: int = 5) -> SearchResponse:
    """Quick one-shot web search."""
    searcher = WebSearcher(max_results=max_results)
    try:
        return await searcher.search(query)
    finally:
        await searcher.close()
