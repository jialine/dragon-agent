"""Tests for Spotify tools — playback controls, devices, playlists.

Uses proper httpx.AsyncClient constructor mocking for context-manager-based calls.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_client(status=204, json_data=None):
    """Create a mocked httpx.AsyncClient that works as async context manager."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json = MagicMock(return_value=json_data)
    mock_resp.text = ""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.put = AsyncMock(return_value=mock_resp)
    client.post = AsyncMock(return_value=mock_resp)
    client.get = AsyncMock(return_value=mock_resp)
    return client


def _mock_client_error(exc=Exception("timeout")):
    """Create a mocked client that raises on HTTP calls."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.put = AsyncMock(side_effect=exc)
    client.post = AsyncMock(side_effect=exc)
    client.get = AsyncMock(side_effect=exc)
    return client


# ── Test tool_spotify_play ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_play_resume():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_play
            result = json.loads(await tool_spotify_play())
            assert result["success"] is True

@pytest.mark.asyncio
async def test_play_with_uri():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_play
            result = json.loads(await tool_spotify_play(uri="spotify:track:abc"))
            assert result["success"] is True

@pytest.mark.asyncio
async def test_play_no_credentials():
    with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value=None)):
        from dragon.tool.builtins.spotify import tool_spotify_play
        result = json.loads(await tool_spotify_play())
        assert "error" in result


# ── Test tool_spotify_pause ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_pause():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_pause
            result = json.loads(await tool_spotify_pause())
            assert result["success"] is True


# ── Test tool_spotify_skip ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_skip
            result = json.loads(await tool_spotify_skip())
            assert result["success"] is True


# ── Test tool_spotify_previous ──────────────────────────────────────

@pytest.mark.asyncio
async def test_previous():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_previous
            result = json.loads(await tool_spotify_previous())
            assert result["success"] is True


# ── Test tool_spotify_queue ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_queue
            result = json.loads(await tool_spotify_queue(uri="spotify:track:xyz"))
            assert result["success"] is True


# ── Test tool_spotify_devices ───────────────────────────────────────

@pytest.mark.asyncio
async def test_devices():
    data = {"devices": [{"id": "d1", "name": "Phone", "type": "Smartphone", "is_active": True, "volume_percent": 80}]}
    with patch("httpx.AsyncClient", return_value=_mock_client(200, data)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_devices
            result = json.loads(await tool_spotify_devices())
            assert result["total"] == 1
            assert result["devices"][0]["name"] == "Phone"

@pytest.mark.asyncio
async def test_devices_no_credentials():
    with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value=None)):
        from dragon.tool.builtins.spotify import tool_spotify_devices
        result = json.loads(await tool_spotify_devices())
        assert "error" in result


# ── Test tool_spotify_volume ────────────────────────────────────────

@pytest.mark.asyncio
async def test_volume():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_volume
            result = json.loads(await tool_spotify_volume(volume=50))
            assert result["success"] is True

@pytest.mark.asyncio
async def test_volume_clamped():
    with patch("httpx.AsyncClient", return_value=_mock_client(204)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_volume
            r1 = json.loads(await tool_spotify_volume(volume=-10))
            r2 = json.loads(await tool_spotify_volume(volume=150))
            assert r1["success"] is True
            assert r2["success"] is True


# ── Test tool_spotify_playlists ─────────────────────────────────────

@pytest.mark.asyncio
async def test_playlists():
    data = {"items": [{"id": "p1", "name": "Chill", "owner": {"display_name": "me"}, "tracks": {"total": 42}, "uri": "spotify:playlist:p1"}]}
    with patch("httpx.AsyncClient", return_value=_mock_client(200, data)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_playlists
            result = json.loads(await tool_spotify_playlists())
            assert result["total"] == 1
            assert result["playlists"][0]["name"] == "Chill"


# ── Test error handling ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_play_http_error():
    with patch("httpx.AsyncClient", return_value=_mock_client(500)):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_play
            result = json.loads(await tool_spotify_play())
            assert "error" in result

@pytest.mark.asyncio
async def test_play_network_error():
    with patch("httpx.AsyncClient", return_value=_mock_client_error()):
        with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_play
            result = json.loads(await tool_spotify_play())
            assert "error" in result


# ── Test existing tools ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_now_playing_no_credentials():
    with patch("dragon.tool.builtins.spotify._get_user_token", new=AsyncMock(return_value=None)):
        from dragon.tool.builtins.spotify import tool_spotify_now_playing
        result = json.loads(await tool_spotify_now_playing())
        assert "error" in result

@pytest.mark.asyncio
async def test_search_no_credentials():
    with patch("dragon.tool.builtins.spotify._get_client_credentials_token", new=AsyncMock(return_value=None)):
        from dragon.tool.builtins.spotify import tool_spotify_search
        result = json.loads(await tool_spotify_search(query="test"))
        assert "error" in result

@pytest.mark.asyncio
async def test_search_with_results():
    data = {"tracks": {"items": [{"name": "Test", "artists": [{"name": "A"}], "album": {"name": "B"}, "external_urls": {"spotify": "u"}, "uri": "s:t:x", "duration_ms": 200000}]}}
    with patch("httpx.AsyncClient", return_value=_mock_client(200, data)):
        with patch("dragon.tool.builtins.spotify._get_client_credentials_token", new=AsyncMock(return_value="tok")):
            from dragon.tool.builtins.spotify import tool_spotify_search
            result = json.loads(await tool_spotify_search(query="test"))
            assert result["total"] >= 1
