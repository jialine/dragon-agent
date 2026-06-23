"""
Tests for maps/geolocation tools.

All tests require network access to Nominatim and OSRM APIs.
Run with: python -m pytest tests/test_maps.py -v --timeout 60
"""

import json

import pytest


class TestGeocode:
    @pytest.mark.asyncio
    async def test_shanghai(self):
        """Geocode Shanghai should return coords near 31.23, 121.47."""
        from dragon.tool.builtins.maps import tool_geocode
        result = await tool_geocode("上海")
        data = json.loads(result)
        assert "lat" in data
        assert "lon" in data
        assert 30 < data["lat"] < 32
        assert 120 < data["lon"] < 122

    @pytest.mark.asyncio
    async def test_empty_address(self):
        """Empty address should return error."""
        from dragon.tool.builtins.maps import tool_geocode
        result = await tool_geocode("")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_english_address(self):
        """Geocode an English address."""
        from dragon.tool.builtins.maps import tool_geocode
        result = await tool_geocode("Times Square, New York")
        data = json.loads(result)
        assert "lat" in data
        assert "lon" in data
        assert "display_name" in data


class TestReverseGeocode:
    @pytest.mark.asyncio
    async def test_shanghai_coords(self):
        """Reverse geocode Shanghai coordinates."""
        from dragon.tool.builtins.maps import tool_reverse_geocode
        result = await tool_reverse_geocode(31.23, 121.47)
        data = json.loads(result)
        assert "display_name" in data
        # Should contain Shanghai or 上海 in the display name
        name = data["display_name"]
        assert "Shanghai" in name or "上海" in name

    @pytest.mark.asyncio
    async def test_invalid_lat(self):
        """Invalid latitude should return error."""
        from dragon.tool.builtins.maps import tool_reverse_geocode
        result = await tool_reverse_geocode(200, 0)
        data = json.loads(result)
        assert "error" in data


class TestRoute:
    @pytest.mark.asyncio
    async def test_basic_route(self):
        """Route between two nearby Shanghai coordinates."""
        from dragon.tool.builtins.maps import tool_get_route
        result = await tool_get_route("31.23,121.47", "31.25,121.50")
        data = json.loads(result)
        assert "distance_km" in data
        assert "duration_min" in data
        assert data["distance_km"] > 0

    @pytest.mark.asyncio
    async def test_invalid_mode(self):
        """Invalid mode should return error."""
        from dragon.tool.builtins.maps import tool_get_route
        result = await tool_get_route("31.23,121.47", "31.25,121.50", mode="rocket")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_empty_origin(self):
        """Empty origin should return error."""
        from dragon.tool.builtins.maps import tool_get_route
        result = await tool_get_route("", "31.25,121.50")
        data = json.loads(result)
        assert "error" in data


class TestPOI:
    @pytest.mark.asyncio
    async def test_restaurant_near_shanghai(self):
        """Search for restaurants near Shanghai."""
        from dragon.tool.builtins.maps import tool_search_poi
        result = await tool_search_poi(
            "restaurant", "31.23,121.47", radius_m=2000, limit=3
        )
        data = json.loads(result)
        assert data["total"] <= 3
        if data["total"] > 0:
            assert "name" in data["results"][0]

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """Empty query should return error."""
        from dragon.tool.builtins.maps import tool_search_poi
        result = await tool_search_poi("", "31.23,121.47")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_geocode_near(self):
        """Search near address that needs geocoding."""
        from dragon.tool.builtins.maps import tool_search_poi
        result = await tool_search_poi(
            "hospital", "上海", radius_m=5000, limit=5
        )
        data = json.loads(result)
        assert "query" in data
        assert "results" in data
