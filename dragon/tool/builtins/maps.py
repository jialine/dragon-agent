"""
Dragon Agent — Maps & Geolocation Tools
========================================

OpenStreetMap-based geolocation tools using Nominatim for geocoding
and OSRM for routing. No API key required.

Tools:
    - geocode: Convert address to coordinates
    - reverse_geocode: Convert coordinates to address
    - get_route: Get a route between two points
    - search_poi: Search for points of interest near a location

APIs:
    - Nominatim: https://nominatim.openstreetmap.org (1 req/s rate limit)
    - OSRM Public: https://router.project-osrm.org

Dependencies:
    - httpx (already in Dragon Agent deps)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.maps")

# ── Constants ────────────────────────────────────────────────────────

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
OSRM_BASE = "https://router.project-osrm.org"
USER_AGENT = "DragonAgent/1.0"

# Nominatim rate limit: 1 request per second (OSM policy)
# We use a module-level asyncio.Lock to serialize Nominatim calls
_nominatim_lock = asyncio.Lock()
_nominatim_last_call = 0.0  # monotonic timestamp

# OSRM mode mapping
OSRM_MODES = {
    "car": "driving",
    "bike": "cycling",
    "foot": "walking",
}


# ── Helpers ──────────────────────────────────────────────────────────


async def _nominatim_request(url: str, params: dict) -> httpx.Response:
    """Make a rate-limited request to Nominatim.

    Enforces the 1 req/s rate limit using a lock and sleep.
    """
    async with _nominatim_lock:
        global _nominatim_last_call
        now = asyncio.get_event_loop().time()
        elapsed = now - _nominatim_last_call
        if elapsed < 1.1:
            await asyncio.sleep(1.1 - elapsed)
        _nominatim_last_call = asyncio.get_event_loop().time()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            return await client.get(url, params=params)


def _parse_lat_lon(text: str) -> Optional[tuple]:
    """Parse 'lat,lon' or 'lon,lat' string into (lat, lon).

    Nominatim uses lat,lon. OSRM uses lon,lat in URLs.
    We standardize on returning (lat, lon).
    Returns None if unparseable.
    """
    text = text.strip()
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        a = float(parts[0].strip())
        b = float(parts[1].strip())
    except ValueError:
        return None
    # Heuristic: lat is in [-90, 90], lon in [-180, 180]
    if -90 <= a <= 90 and -180 <= b <= 180:
        return (a, b)  # a is lat, b is lon
    if -90 <= b <= 90 and -180 <= a <= 180:
        return (b, a)  # b is lat, a is lon
    return None


def _resolve_coords(text: str) -> Optional[tuple]:
    """Try to parse coordinates from text; return (lat, lon) or None."""
    return _parse_lat_lon(text)


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute distance in meters between two lat/lon points using Haversine formula."""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_geocode(address: str) -> str:
    """Convert an address or place name to latitude/longitude coordinates.

    Uses OpenStreetMap Nominatim for forward geocoding.

    Args:
        address: Address, place name, or location description (e.g., "Shanghai", "1600 Amphitheatre Parkway, Mountain View").

    Returns:
        JSON with lat, lon, display_name, and address components. Returns error field on failure.
    """
    if not address or not address.strip():
        return json.dumps({"error": "Address cannot be empty"})

    address = address.strip()

    try:
        resp = await _nominatim_request(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": 1},
        )
        if resp.status_code != 200:
            return json.dumps({
                "error": f"Nominatim returned HTTP {resp.status_code}",
                "address": address,
            })

        data = resp.json()
        if not data:
            return json.dumps({
                "error": f"No results found for address: {address}",
                "address": address,
            })

        result = data[0]
        return json.dumps({
            "address": address,
            "lat": float(result["lat"]),
            "lon": float(result["lon"]),
            "display_name": result.get("display_name", ""),
            "osm_type": result.get("osm_type", ""),
            "category": result.get("category", ""),
        })

    except httpx.TimeoutException:
        return json.dumps({"error": "Geocoding request timed out", "address": address})
    except Exception as e:
        logger.warning("Geocode failed for '%s': %s", address, e)
        return json.dumps({"error": f"Geocoding failed: {type(e).__name__}: {str(e)}", "address": address})


async def tool_reverse_geocode(lat: float, lon: float) -> str:
    """Convert latitude/longitude coordinates to a human-readable address.

    Uses OpenStreetMap Nominatim reverse geocoding.

    Args:
        lat: Latitude (e.g., 31.23).
        lon: Longitude (e.g., 121.47).

    Returns:
        JSON with display_name, address components, and original coordinates. Returns error field on failure.
    """
    # Validate coordinates
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return json.dumps({
            "error": f"Invalid coordinates: lat={lat}, lon={lon}. Lat must be in [-90, 90], lon in [-180, 180].",
        })

    try:
        resp = await _nominatim_request(
            f"{NOMINATIM_BASE}/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
        )
        if resp.status_code != 200:
            return json.dumps({
                "error": f"Nominatim returned HTTP {resp.status_code}",
                "lat": lat,
                "lon": lon,
            })

        data = resp.json()
        if not data or "display_name" not in data:
            return json.dumps({
                "error": f"No results found for coordinates: {lat}, {lon}",
                "lat": lat,
                "lon": lon,
            })

        # Extract structured address if available
        address_components = data.get("address", {})

        return json.dumps({
            "lat": lat,
            "lon": lon,
            "display_name": data.get("display_name", ""),
            "address": address_components,
            "osm_type": data.get("osm_type", ""),
            "osm_id": data.get("osm_id"),
        })

    except httpx.TimeoutException:
        return json.dumps({"error": "Reverse geocoding request timed out", "lat": lat, "lon": lon})
    except Exception as e:
        logger.warning("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
        return json.dumps({
            "error": f"Reverse geocoding failed: {type(e).__name__}: {str(e)}",
            "lat": lat,
            "lon": lon,
        })


async def tool_get_route(
    origin: str,
    destination: str,
    mode: str = "car",
) -> str:
    """Get a route between two points with turn-by-turn directions.

    Uses the public OSRM routing engine. Both origin and destination can
    be either coordinates ("lat,lon") or address strings (which will be
    geocoded first).

    Args:
        origin: Starting point as "lat,lon" or address string.
        destination: Ending point as "lat,lon" or address string.
        mode: Travel mode: "car" (driving), "bike" (cycling), or "foot" (walking). Default: "car".

    Returns:
        JSON with distance_km, duration_min, steps (turn-by-turn), and polyline.
        Returns error field on failure.
    """
    if not origin or not origin.strip():
        return json.dumps({"error": "Origin cannot be empty"})
    if not destination or not destination.strip():
        return json.dumps({"error": "Destination cannot be empty"})

    origin = origin.strip()
    destination = destination.strip()

    # Validate mode
    mode_lower = mode.lower().strip()
    if mode_lower not in OSRM_MODES:
        valid_modes = ", ".join(OSRM_MODES.keys())
        return json.dumps({"error": f"Invalid mode '{mode}'. Valid modes: {valid_modes}"})
    osrm_mode = OSRM_MODES[mode_lower]

    # Resolve coordinates for origin and destination
    async def _get_coords(text: str, label: str) -> Optional[tuple]:
        """Try to parse text as coordinates; if that fails, geocode it."""
        coords = _resolve_coords(text)
        if coords:
            return coords
        # Try geocoding
        try:
            result_json = await tool_geocode(text)
            data = json.loads(result_json)
            if "lat" in data and "lon" in data:
                return (data["lat"], data["lon"])
        except Exception as e:
            logger.warning("Geocode failed for %s '%s': %s", label, text, e)
        return None

    origin_coords = await _get_coords(origin, "origin")
    if origin_coords is None:
        return json.dumps({"error": f"Could not resolve origin: {origin}"})

    dest_coords = await _get_coords(destination, "destination")
    if dest_coords is None:
        return json.dumps({"error": f"Could not resolve destination: {destination}"})

    lat1, lon1 = origin_coords
    lat2, lon2 = dest_coords

    # OSRM expects: /route/v1/{mode}/{lon},{lat};{lon},{lat}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            url = f"{OSRM_BASE}/route/v1/{osrm_mode}/{lon1},{lat1};{lon2},{lat2}"
            resp = await client.get(url, params={
                "overview": "full",
                "steps": "true",
                "geometries": "polyline",
            })

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"OSRM returned HTTP {resp.status_code}",
                    "origin": f"{lat1},{lon1}",
                    "destination": f"{lat2},{lon2}",
                })

            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                return json.dumps({
                    "error": f"No route found: {data.get('code', 'unknown')}",
                    "origin": f"{lat1},{lon1}",
                    "destination": f"{lat2},{lon2}",
                })

            route = data["routes"][0]
            distance_m = route.get("distance", 0)
            duration_s = route.get("duration", 0)

            # Extract steps (turn-by-turn directions)
            steps = []
            for leg in route.get("legs", []):
                for step in leg.get("steps", []):
                    steps.append({
                        "instruction": step.get("name", ""),
                        "maneuver": step.get("maneuver", {}).get("type", ""),
                        "distance_m": step.get("distance", 0),
                        "duration_s": step.get("duration", 0),
                    })

            return json.dumps({
                "origin": f"{lat1},{lon1}",
                "destination": f"{lat2},{lon2}",
                "mode": mode_lower,
                "distance_km": round(distance_m / 1000, 2),
                "duration_min": round(duration_s / 60, 1),
                "distance_m": distance_m,
                "duration_s": duration_s,
                "polyline": route.get("geometry", ""),
                "steps": steps,
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Routing request timed out"})
    except Exception as e:
        logger.warning("Route failed: %s", e)
        return json.dumps({"error": f"Routing failed: {type(e).__name__}: {str(e)}"})


async def tool_search_poi(
    query: str,
    near: str = "",
    radius_m: int = 1000,
    limit: int = 5,
) -> str:
    """Search for points of interest (POI) near a location.

    Uses OpenStreetMap Nominatim search with bounding box constraint
    around a center point. If 'near' is an address, it will be geocoded first.

    Args:
        query: What to search for (e.g., "restaurant", "hospital", "hotel", "cafe").
        near: Location as "lat,lon" coordinates or address string. Required.
        radius_m: Search radius in meters (default: 1000).
        limit: Maximum number of results to return (default: 5).

    Returns:
        JSON with query, near info, total count, and results list with name,
        lat, lon, type, distance_m, and display_name. Returns error field on failure.
    """
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})
    if not near or not near.strip():
        return json.dumps({"error": "Near location cannot be empty"})

    query = query.strip()
    near = near.strip()

    # Resolve 'near' to coordinates
    coords = _resolve_coords(near)
    if coords is None:
        # Try geocoding
        result_json = await tool_geocode(near)
        data = json.loads(result_json)
        if "lat" in data and "lon" in data:
            coords = (data["lat"], data["lon"])
        else:
            return json.dumps({"error": f"Could not resolve location: {near}"})

    center_lat, center_lon = coords

    # Compute bounding box from center + radius
    # Approximate: 1 degree lat ≈ 111.32 km, 1 degree lon ≈ 111.32 * cos(lat) km
    lat_offset = (radius_m / 1000) / 111.32
    lon_offset = (radius_m / 1000) / (111.32 * math.cos(math.radians(center_lat)))

    # Nominatim viewbox: left,top,right,bottom = lon1,lat1,lon2,lat2
    lon_min = center_lon - lon_offset
    lat_max = center_lat + lat_offset
    lon_max = center_lon + lon_offset
    lat_min = center_lat - lat_offset

    viewbox = f"{lon_min},{lat_max},{lon_max},{lat_min}"

    try:
        resp = await _nominatim_request(
            f"{NOMINATIM_BASE}/search",
            params={
                "q": query,
                "format": "json",
                "limit": limit,
                "bounded": 1,
                "viewbox": viewbox,
            },
        )

        if resp.status_code != 200:
            return json.dumps({
                "error": f"Nominatim returned HTTP {resp.status_code}",
                "query": query,
                "near": {"lat": center_lat, "lon": center_lon},
            })

        raw_results = resp.json()

        # Build results with distance
        results = []
        for item in raw_results:
            poi_lat = float(item["lat"])
            poi_lon = float(item["lon"])
            distance = _haversine_distance(center_lat, center_lon, poi_lat, poi_lon)
            results.append({
                "name": item.get("display_name", "").split(",")[0] if item.get("display_name") else "",
                "display_name": item.get("display_name", ""),
                "lat": poi_lat,
                "lon": poi_lon,
                "type": item.get("type", ""),
                "category": item.get("category", ""),
                "distance_m": round(distance, 1),
                "osm_type": item.get("osm_type", ""),
                "osm_id": item.get("osm_id"),
            })

        return json.dumps({
            "query": query,
            "near": {"lat": center_lat, "lon": center_lon},
            "radius_m": radius_m,
            "total": len(results),
            "results": results,
        })

    except httpx.TimeoutException:
        return json.dumps({"error": "POI search request timed out", "query": query})
    except Exception as e:
        logger.warning("POI search failed for '%s' near %s: %s", query, near, e)
        return json.dumps({
            "error": f"POI search failed: {type(e).__name__}: {str(e)}",
            "query": query,
        })
