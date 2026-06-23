"""
Dragon Agent — Airtable API Integration
========================================

Tools for listing and creating Airtable records via the Airtable REST API.

Auth: Requires AIRTABLE_API_KEY in environment.
API: https://api.airtable.com/v0/{base_id}/{table_name}

Tools:
    - airtable_list_records: List records from an Airtable table
    - airtable_create_record: Create a new record in an Airtable table
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("dragon.tool.airtable")

AIRTABLE_API_BASE = "https://api.airtable.com/v0"


def _airtable_headers() -> dict:
    """Build headers for Airtable API requests."""
    return {
        "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


def _check_auth() -> Optional[str]:
    """Verify AIRTABLE_API_KEY is set. Returns error JSON string or None."""
    if not os.getenv("AIRTABLE_API_KEY"):
        return json.dumps({"error": "AIRTABLE_API_KEY environment variable is not set"})
    return None


# ────────────────────────────────────────────────────────────────────
# Tool: List Airtable Records
# ────────────────────────────────────────────────────────────────────


async def tool_airtable_list_records(
    base_id: str,
    table_name: str,
    limit: int = 10,
) -> str:
    """List records from an Airtable table.

    Fetches records with all fields and includes the record ID.

    Args:
        base_id: The Airtable base ID (found in the API docs for your base).
        table_name: Name or ID of the table to list records from.
        limit: Maximum records to return (default: 10, max: 100).

    Returns:
        JSON with records list containing id, createdTime, and fields.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not base_id or not base_id.strip():
        return json.dumps({"error": "base_id is required"})
    if not table_name or not table_name.strip():
        return json.dumps({"error": "table_name is required"})

    base_id = base_id.strip()
    table_name = table_name.strip()
    limit = max(1, min(limit, 100))

    # URL-encode table name (Airtable APIs accept URL-encoded names)
    from urllib.parse import quote

    url = f"{AIRTABLE_API_BASE}/{base_id}/{quote(table_name)}?maxRecords={limit}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_airtable_headers())

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Airtable API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()
            records_raw = data.get("records", [])

            records = []
            for rec in records_raw:
                records.append({
                    "id": rec.get("id", ""),
                    "created_time": rec.get("createdTime", ""),
                    "fields": rec.get("fields", {}),
                })

            return json.dumps({
                "base_id": base_id,
                "table": table_name,
                "records": records,
                "total": len(records),
                "has_more": "offset" in data,
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Airtable] List records failed: %s", exc)
        return json.dumps({
            "error": f"List records failed: {type(exc).__name__}: {str(exc)}",
        })


# ────────────────────────────────────────────────────────────────────
# Tool: Create an Airtable Record
# ────────────────────────────────────────────────────────────────────


async def tool_airtable_create_record(
    base_id: str,
    table_name: str,
    fields: str,
) -> str:
    """Create a new record in an Airtable table.

    Args:
        base_id: The Airtable base ID.
        table_name: Name or ID of the table.
        fields: JSON string of field names to values.
                Example: '{"Name": "Task 1", "Status": "Todo"}'

    Returns:
        JSON with the created record's id, createdTime, and fields.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not base_id or not base_id.strip():
        return json.dumps({"error": "base_id is required"})
    if not table_name or not table_name.strip():
        return json.dumps({"error": "table_name is required"})
    if not fields or not fields.strip():
        return json.dumps({"error": "fields is required"})

    base_id = base_id.strip()
    table_name = table_name.strip()
    fields = fields.strip()

    # Parse the fields JSON
    try:
        fields_dict = json.loads(fields)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "error": f"Invalid fields JSON: {str(exc)}",
            "fields": fields,
        })

    if not isinstance(fields_dict, dict):
        return json.dumps({
            "error": "fields must be a JSON object (dictionary)",
        })

    from urllib.parse import quote

    url = f"{AIRTABLE_API_BASE}/{base_id}/{quote(table_name)}"
    body = {"records": [{"fields": fields_dict}]}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers=_airtable_headers(),
                json=body,
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Airtable API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()
            records = data.get("records", [])

            if not records:
                return json.dumps({"error": "No record returned from Airtable"})

            created = records[0]
            return json.dumps({
                "id": created.get("id", ""),
                "created_time": created.get("createdTime", ""),
                "fields": created.get("fields", {}),
                "base_id": base_id,
                "table": table_name,
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Airtable] Create record failed: %s", exc)
        return json.dumps({
            "error": f"Create record failed: {type(exc).__name__}: {str(exc)}",
        })
