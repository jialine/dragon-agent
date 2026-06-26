"""
Dragon Agent — Spotify Music Tools
===================================

Search for tracks, albums, and artists on Spotify, plus check
what's currently playing (requires user authorization).

Tools:
    - spotify_search: Search tracks/albums/artists on Spotify
    - spotify_now_playing: Get the currently playing track
    - spotify_play: Start/resume playback
    - spotify_pause: Pause playback
    - spotify_skip: Skip to next track
    - spotify_previous: Go to previous track
    - spotify_queue: Add track to queue
    - spotify_devices: List available devices
    - spotify_volume: Set playback volume
    - spotify_playlists: List user's playlists

APIs:
    - Spotify Web API: https://developer.spotify.com/documentation/web-api
    - Auth: Client Credentials (server-to-server) for search
    - Auth: Authorization Code + Refresh Token for now-playing

Environment Variables:
    - SPOTIFY_CLIENT_ID (required)
    - SPOTIFY_CLIENT_SECRET (required)
    - SPOTIFY_REFRESH_TOKEN (required for now_playing only)

Dependencies:
    - httpx (already in Dragon Agent deps)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.spotify")

# ── Constants ────────────────────────────────────────────────────────

SPOTIFY_ACCOUNTS_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# In-memory token cache (module lifetime)
_token_cache: Dict[str, Any] = {}  # {access_token, expires_at, token_type}


# ── Auth Helpers ─────────────────────────────────────────────────────


async def _get_client_credentials_token() -> Optional[str]:
    """Get an access token using client_credentials flow.

    Caches the token in memory until it expires.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return None

    # Check cache
    now = time.time()
    cache_key = "client_credentials"
    if cache_key in _token_cache:
        entry = _token_cache[cache_key]
        if entry.get("expires_at", 0) > now + 60:  # 60s buffer
            return entry["access_token"]

    # Request new token
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                SPOTIFY_ACCOUNTS_URL,
                headers={
                    "Authorization": f"Basic {auth_b64}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
            if resp.status_code != 200:
                logger.warning(
                    "Spotify client_credentials auth failed: HTTP %d: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            data = resp.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)

            _token_cache[cache_key] = {
                "access_token": access_token,
                "expires_at": now + expires_in,
                "token_type": data.get("token_type", "Bearer"),
            }
            return access_token

    except Exception as e:
        logger.warning("Spotify client_credentials auth error: %s", e)
        return None


async def _get_user_token() -> Optional[str]:
    """Get an access token using refresh_token flow (for user-scoped endpoints).

    Requires SPOTIFY_REFRESH_TOKEN.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()

    if not client_id or not client_secret or not refresh_token:
        return None

    # Check cache
    now = time.time()
    cache_key = f"user_{refresh_token[:8]}"
    if cache_key in _token_cache:
        entry = _token_cache[cache_key]
        if entry.get("expires_at", 0) > now + 60:
            return entry["access_token"]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                SPOTIFY_ACCOUNTS_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "Spotify refresh_token auth failed: HTTP %d: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            data = resp.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)

            _token_cache[cache_key] = {
                "access_token": access_token,
                "expires_at": now + expires_in,
                "token_type": data.get("token_type", "Bearer"),
            }
            return access_token

    except Exception as e:
        logger.warning("Spotify refresh_token auth error: %s", e)
        return None


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_spotify_search(
    query: str,
    search_type: str = "track",
    limit: int = 5,
) -> str:
    """Search Spotify for tracks, albums, or artists.

    Uses client credentials flow (server-to-server). Requires the
    SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables.

    Args:
        query: Search query string (e.g., "Bohemian Rhapsody").
        search_type: What to search for. One of "track", "album", "artist",
            or "track,album,artist" for combined results. Default: "track".
        limit: Maximum number of results (1-50). Default: 5.

    Returns:
        JSON with query, search_type, and results list. Each result
        includes name, artist(s), album, url (Spotify URI), and
        preview_url (30s audio preview, if available). Returns error
        field if credentials are missing or the API fails.
    """
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})

    query = query.strip()

    # Validate search_type
    valid_types = {"track", "album", "artist", "playlist", "show", "episode"}
    types = [t.strip() for t in search_type.split(",")]
    for t in types:
        if t and t not in valid_types:
            return json.dumps({
                "error": f"Invalid search_type: '{t}'. Must be one of {sorted(valid_types)}.",
            })

    limit = max(1, min(50, limit))

    # Get token
    token = await _get_client_credentials_token()
    if not token:
        return json.dumps({
            "error": (
                "Spotify credentials not configured. "
                "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables. "
                "Get them at https://developer.spotify.com/dashboard"
            ),
        })

    # Make search request
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/search",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": query,
                    "type": search_type,
                    "limit": limit,
                },
            )

            if resp.status_code == 401:
                # Token might be expired; clear cache and retry once
                _token_cache.pop("client_credentials", None)
                token = await _get_client_credentials_token()
                if not token:
                    return json.dumps({"error": "Spotify authentication failed (401)"})
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/search",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "q": query,
                        "type": search_type,
                        "limit": limit,
                    },
                )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Spotify API error (HTTP {resp.status_code})",
                    "detail": resp.text[:500],
                })

            data = resp.json()

    except httpx.TimeoutException:
        return json.dumps({"error": "Spotify API request timed out"})
    except Exception as e:
        logger.warning("Spotify search failed: %s", e)
        return json.dumps({"error": f"Spotify API request failed: {type(e).__name__}: {str(e)}"})

    # ── Normalize results ──────────────────────────────────────────
    results: list = []

    # Tracks
    for item in data.get("tracks", {}).get("items", []):
        results.append({
            "type": "track",
            "name": item.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", ""),
            "url": item.get("external_urls", {}).get("spotify", ""),
            "preview_url": item.get("preview_url"),
            "duration_ms": item.get("duration_ms"),
            "popularity": item.get("popularity"),
        })

    # Albums
    for item in data.get("albums", {}).get("items", []):
        results.append({
            "type": "album",
            "name": item.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "album": item.get("name", ""),
            "url": item.get("external_urls", {}).get("spotify", ""),
            "release_date": item.get("release_date", ""),
            "total_tracks": item.get("total_tracks"),
        })

    # Artists
    for item in data.get("artists", {}).get("items", []):
        results.append({
            "type": "artist",
            "name": item.get("name", ""),
            "artist": item.get("name", ""),
            "url": item.get("external_urls", {}).get("spotify", ""),
            "followers": item.get("followers", {}).get("total"),
            "genres": item.get("genres", []),
            "popularity": item.get("popularity"),
        })

    # Playlists
    for item in data.get("playlists", {}).get("items", []):
        results.append({
            "type": "playlist",
            "name": item.get("name", ""),
            "owner": item.get("owner", {}).get("display_name", ""),
            "url": item.get("external_urls", {}).get("spotify", ""),
            "description": item.get("description", ""),
            "total_tracks": item.get("tracks", {}).get("total"),
        })

    return json.dumps({
        "query": query,
        "search_type": search_type,
        "results": results,
        "total": len(results),
    })


async def tool_spotify_now_playing() -> str:
    """Get the currently playing track on Spotify.

    Requires user authorization via SPOTIFY_REFRESH_TOKEN in addition
    to SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.

    To obtain a refresh token:
        1. Go to https://developer.spotify.com/dashboard
        2. Create an app, note the Client ID and Client Secret
        3. Use the Authorization Code flow with scopes:
           user-read-currently-playing user-read-playback-state
        4. Exchange the authorization code for a refresh token

    Returns:
        JSON with track, artist, album, progress_ms, duration_ms,
        is_playing, and url. If nothing is currently playing, returns
        is_playing=false with an empty track. Returns error field if
        credentials are missing or the API fails.
    """
    token = await _get_user_token()
    if not token:
        return json.dumps({
            "error": (
                "Spotify user credentials not configured. "
                "Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and "
                "SPOTIFY_REFRESH_TOKEN environment variables. "
                "See the tool docstring for instructions on obtaining "
                "a refresh token."
            ),
        })

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code == 401:
                # Token expired; clear cache and retry once
                refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
                _token_cache.pop(f"user_{refresh_token[:8]}", None)
                token = await _get_user_token()
                if not token:
                    return json.dumps({"error": "Spotify user authentication failed (401)"})
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/player/currently-playing",
                    headers={"Authorization": f"Bearer {token}"},
                )

            if resp.status_code == 204:
                # Nothing currently playing
                return json.dumps({
                    "track": "",
                    "artist": "",
                    "album": "",
                    "progress_ms": 0,
                    "duration_ms": 0,
                    "is_playing": False,
                    "url": "",
                })

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Spotify API error (HTTP {resp.status_code})",
                    "detail": resp.text[:500],
                })

            data = resp.json()

    except httpx.TimeoutException:
        return json.dumps({"error": "Spotify API request timed out"})
    except Exception as e:
        logger.warning("Spotify now_playing failed: %s", e)
        return json.dumps({"error": f"Spotify API request failed: {type(e).__name__}: {str(e)}"})

    item = data.get("item", {})
    if not item:
        return json.dumps({
            "track": "",
            "artist": "",
            "album": "",
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": 0,
            "is_playing": data.get("is_playing", False),
            "url": "",
        })

    return json.dumps({
        "track": item.get("name", ""),
        "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
        "album": item.get("album", {}).get("name", ""),
        "progress_ms": data.get("progress_ms", 0),
        "duration_ms": item.get("duration_ms", 0),
        "is_playing": data.get("is_playing", False),
        "url": item.get("external_urls", {}).get("spotify", ""),
    })


# ── Playback Controls ─────────────────────────────────────────────────

async def _spotify_put(path: str, body: dict = None) -> dict:
    """Helper for Spotify PUT requests with user auth."""
    token = await _get_user_token()
    if not token:
        return {"error": "Spotify user credentials not configured"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
            if body:
                kwargs["json"] = body
            resp = await client.put(f"{SPOTIFY_API_BASE}{path}", **kwargs)
            if resp.status_code == 401:
                refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
                _token_cache.pop(f"user_{refresh_token[:8]}", None)
                token = await _get_user_token()
                if token:
                    kwargs["headers"] = {"Authorization": f"Bearer {token}"}
                    resp = await client.put(f"{SPOTIFY_API_BASE}{path}", **kwargs)
            if resp.status_code in (200, 202, 204):
                return {"success": True, "status": resp.status_code}
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}


async def _spotify_post(path: str, body: dict = None) -> dict:
    """Helper for Spotify POST requests with user auth."""
    token = await _get_user_token()
    if not token:
        return {"error": "Spotify user credentials not configured"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
            if body:
                kwargs["json"] = body
            resp = await client.post(f"{SPOTIFY_API_BASE}{path}", **kwargs)
            if resp.status_code == 401:
                refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
                _token_cache.pop(f"user_{refresh_token[:8]}", None)
                token = await _get_user_token()
                if token:
                    kwargs["headers"] = {"Authorization": f"Bearer {token}"}
                    resp = await client.post(f"{SPOTIFY_API_BASE}{path}", **kwargs)
            if resp.status_code in (200, 201, 202, 204):
                try:
                    return {"success": True, "data": resp.json()}
                except Exception:
                    return {"success": True}
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}


async def tool_spotify_play(
    uri: str = "",
    context_uri: str = "",
    device_id: str = "",
) -> str:
    """Start or resume Spotify playback.

    Args:
        uri: Track URI to play (e.g. spotify:track:xxx). If empty, resumes.
        context_uri: Album/playlist URI to play (e.g. spotify:album:xxx).
        device_id: Target device ID. If empty, uses active device.

    Returns:
        JSON with success status or error details.
    """
    body = {}
    if uri:
        body["uris"] = [uri]
    if context_uri:
        body["context_uri"] = context_uri
    path = "/me/player/play"
    if device_id:
        path += f"?device_id={device_id}"
    result = await _spotify_put(path, body if body else None)
    return json.dumps(result)


async def tool_spotify_pause(device_id: str = "") -> str:
    """Pause Spotify playback.

    Args:
        device_id: Target device ID. If empty, uses active device.

    Returns:
        JSON with success status.
    """
    path = "/me/player/pause"
    if device_id:
        path += f"?device_id={device_id}"
    result = await _spotify_put(path)
    return json.dumps(result)


async def tool_spotify_skip(device_id: str = "") -> str:
    """Skip to the next track.

    Args:
        device_id: Target device ID.

    Returns:
        JSON with success status.
    """
    path = "/me/player/next"
    if device_id:
        path += f"?device_id={device_id}"
    result = await _spotify_post(path)
    return json.dumps(result)


async def tool_spotify_previous(device_id: str = "") -> str:
    """Go back to the previous track.

    Args:
        device_id: Target device ID.

    Returns:
        JSON with success status.
    """
    path = "/me/player/previous"
    if device_id:
        path += f"?device_id={device_id}"
    result = await _spotify_post(path)
    return json.dumps(result)


async def tool_spotify_queue(uri: str, device_id: str = "") -> str:
    """Add a track to the Spotify playback queue.

    Args:
        uri: Track URI to queue (e.g. spotify:track:xxx).
        device_id: Target device ID.

    Returns:
        JSON with success status.
    """
    path = f"/me/player/queue?uri={uri}"
    if device_id:
        path += f"&device_id={device_id}"
    result = await _spotify_post(path)
    return json.dumps(result)


async def tool_spotify_devices() -> str:
    """List available Spotify devices.

    Returns:
        JSON with list of devices (id, name, type, is_active, volume).
    """
    token = await _get_user_token()
    if not token:
        return json.dumps({"error": "Spotify user credentials not configured"})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me/player/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
                _token_cache.pop(f"user_{refresh_token[:8]}", None)
                token = await _get_user_token()
                if token:
                    resp = await client.get(
                        f"{SPOTIFY_API_BASE}/me/player/devices",
                        headers={"Authorization": f"Bearer {token}"},
                    )
            if resp.status_code != 200:
                return json.dumps({"error": f"HTTP {resp.status_code}"})
            data = resp.json()
            devices = []
            for d in data.get("devices", []):
                devices.append({
                    "id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "type": d.get("type", ""),
                    "is_active": d.get("is_active", False),
                    "volume": d.get("volume_percent", 0),
                })
            return json.dumps({"devices": devices, "total": len(devices)})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


async def tool_spotify_volume(volume: int, device_id: str = "") -> str:
    """Set Spotify playback volume.

    Args:
        volume: Volume 0-100.
        device_id: Target device ID.

    Returns:
        JSON with success status.
    """
    volume = max(0, min(100, volume))
    path = f"/me/player/volume?volume_percent={volume}"
    if device_id:
        path += f"&device_id={device_id}"
    result = await _spotify_put(path)
    return json.dumps(result)


async def tool_spotify_playlists(limit: int = 20) -> str:
    """List the current user's Spotify playlists.

    Args:
        limit: Maximum number of playlists to return (default 20, max 50).

    Returns:
        JSON with list of playlists.
    """
    token = await _get_user_token()
    if not token:
        return json.dumps({"error": "Spotify user credentials not configured"})
    try:
        limit = max(1, min(50, limit))
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me/playlists?limit={limit}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
                _token_cache.pop(f"user_{refresh_token[:8]}", None)
                token = await _get_user_token()
                if token:
                    resp = await client.get(
                        f"{SPOTIFY_API_BASE}/me/playlists?limit={limit}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
            if resp.status_code != 200:
                return json.dumps({"error": f"HTTP {resp.status_code}"})
            data = resp.json()
            playlists = []
            for p in data.get("items", []):
                playlists.append({
                    "id": p.get("id", ""),
                    "name": p.get("name", ""),
                    "owner": p.get("owner", {}).get("display_name", ""),
                    "tracks_total": p.get("tracks", {}).get("total", 0),
                    "uri": p.get("uri", ""),
                })
            return json.dumps({"playlists": playlists, "total": len(playlists)})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})
