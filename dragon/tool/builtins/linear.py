"""
Dragon Agent — Linear API Integration (GraphQL)
================================================

Tools for listing and creating Linear issues via the Linear GraphQL API.

Auth: Requires LINEAR_API_KEY in environment.
API: POST https://api.linear.app/graphql

Tools:
    - linear_list_issues: List Linear issues with optional team filter
    - linear_create_issue: Create a new Linear issue
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("dragon.tool.linear")

LINEAR_API_URL = "https://api.linear.app/graphql"


def _linear_headers() -> dict:
    """Build headers for Linear API requests."""
    return {
        "Authorization": os.getenv("LINEAR_API_KEY", ""),
        "Content-Type": "application/json",
    }


def _check_auth() -> Optional[str]:
    """Verify LINEAR_API_KEY is set. Returns error JSON string or None."""
    if not os.getenv("LINEAR_API_KEY"):
        return json.dumps({"error": "LINEAR_API_KEY environment variable is not set"})
    return None


# ────────────────────────────────────────────────────────────────────
# Tool: List Linear Issues
# ────────────────────────────────────────────────────────────────────


async def tool_linear_list_issues(team: str = "", limit: int = 10) -> str:
    """List Linear issues, optionally filtered by team.

    Uses the Linear GraphQL API to fetch issues with their title,
    state, and assignee.

    Args:
        team: Optional team key or name to filter by (e.g., 'ENG').
              If empty, returns issues across all teams.
        limit: Maximum number of issues to return (default: 10, max: 100).

    Returns:
        JSON with issues list containing id, title, identifier, state,
        assignee, and url.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    limit = max(1, min(limit, 100))

    # Build the GraphQL query
    # We use a simpler query that always works:
    # If no team filter, just list issues sorted by updatedAt desc
    if team and team.strip():
        team = team.strip()
        query = """
        query ListIssues($teamKey: String!, $limit: Int!) {
            team(id: $teamKey) {
                id
                name
                key
                issues(first: $limit, orderBy: updatedAt) {
                    nodes {
                        id
                        title
                        identifier
                        state { name }
                        assignee { name }
                        url
                        updatedAt
                    }
                }
            }
        }
        """
        variables = {"teamKey": team, "limit": limit}
    else:
        query = """
        query ListIssues($limit: Int!) {
            issues(first: $limit, orderBy: updatedAt) {
                nodes {
                    id
                    title
                    identifier
                    state { name }
                    assignee { name }
                    url
                    updatedAt
                    team { name key }
                }
            }
        }
        """
        variables = {"limit": limit}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                LINEAR_API_URL,
                headers=_linear_headers(),
                json={"query": query, "variables": variables},
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Linear API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()

            if "errors" in data:
                error_msgs = [
                    e.get("message", str(e)) for e in data["errors"]
                ]
                return json.dumps({
                    "error": "GraphQL errors: " + "; ".join(error_msgs),
                    "detail": data["errors"],
                })

            # Extract issues from response
            issues_raw = []
            if team:
                team_data = data.get("data", {}).get("team", {})
                if not team_data:
                    return json.dumps({
                        "error": f"Team '{team}' not found",
                    })
                issues_raw = team_data.get("issues", {}).get("nodes", [])
            else:
                issues_raw = data.get("data", {}).get("issues", {}).get("nodes", [])

            issues = []
            for issue in issues_raw:
                issues.append({
                    "id": issue.get("id", ""),
                    "title": issue.get("title", ""),
                    "identifier": issue.get("identifier", ""),
                    "state": (
                        issue.get("state", {}).get("name", "")
                        if issue.get("state") else ""
                    ),
                    "assignee": (
                        issue.get("assignee", {}).get("name", "")
                        if issue.get("assignee") else "Unassigned"
                    ),
                    "url": issue.get("url", ""),
                    "updated_at": issue.get("updatedAt", ""),
                })

            result = {
                "issues": issues,
                "total": len(issues),
            }

            if team:
                result["team"] = team

            return json.dumps(result)

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Linear] List issues failed: %s", exc)
        return json.dumps({
            "error": f"List issues failed: {type(exc).__name__}: {str(exc)}",
        })


# ────────────────────────────────────────────────────────────────────
# Tool: Create a Linear Issue
# ────────────────────────────────────────────────────────────────────


async def tool_linear_create_issue(
    title: str,
    description: str = "",
    team_id: str = "",
) -> str:
    """Create a new Linear issue.

    Uses the Linear GraphQL API to create an issue with a title,
    optional description, and optional team assignment.

    Args:
        title: Issue title (required).
        description: Optional issue description (supports Markdown).
        team_id: Optional team ID or key (e.g., 'ENG'). If empty,
                 the issue is created in the default team.

    Returns:
        JSON with issue id, title, identifier, url, and state.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not title or not title.strip():
        return json.dumps({"error": "title is required"})

    title = title.strip()
    description = (description or "").strip()

    # Build the mutation
    mutation = """
    mutation CreateIssue($title: String!, $description: String, $teamId: String) {
        issueCreate(input: {
            title: $title
            description: $description
            teamId: $teamId
        }) {
            success
            issue {
                id
                title
                identifier
                url
                state { name }
                team { name key }
            }
        }
    }
    """

    variables = {
        "title": title,
        "description": description or None,
        "teamId": team_id if team_id and team_id.strip() else None,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                LINEAR_API_URL,
                headers=_linear_headers(),
                json={"query": mutation, "variables": variables},
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Linear API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()

            if "errors" in data:
                error_msgs = [
                    e.get("message", str(e)) for e in data["errors"]
                ]
                return json.dumps({
                    "error": "GraphQL errors: " + "; ".join(error_msgs),
                    "detail": data["errors"],
                })

            result_data = data.get("data", {}).get("issueCreate", {})
            if not result_data.get("success"):
                return json.dumps({
                    "error": "Issue creation failed",
                })

            issue = result_data.get("issue", {})
            return json.dumps({
                "id": issue.get("id", ""),
                "title": issue.get("title", ""),
                "identifier": issue.get("identifier", ""),
                "url": issue.get("url", ""),
                "state": (
                    issue.get("state", {}).get("name", "")
                    if issue.get("state") else "Unknown"
                ),
                "team": (
                    f"{issue.get('team', {}).get('key', '')} - {issue.get('team', {}).get('name', '')}"
                    if issue.get("team") else ""
                ),
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Linear] Create issue failed: %s", exc)
        return json.dumps({
            "error": f"Create issue failed: {type(exc).__name__}: {str(exc)}",
        })
