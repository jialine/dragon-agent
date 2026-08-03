"""Workflow management tools for Dragon Agent.

Exposes WorkflowStore as tools so the model can create, list, and update
workflows autonomously — enabling cross-session task orchestration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from dragon.workflow_store import WorkflowStore

logger = logging.getLogger("dragon.tool.workflows")

# Global workflow store reference (set during startup)
_workflow_store: Optional[WorkflowStore] = None


def set_workflow_store(store: WorkflowStore) -> None:
    """Set the global workflow store (called during gateway startup)."""
    global _workflow_store
    _workflow_store = store


async def tool_create_workflow(
    name: str,
    description: str = "",
    context: str = "{}",
) -> str:
    """Create a new workflow for multi-step task orchestration.

    Parameters
    ----------
    name : str
        Workflow name (e.g., "lottery-analysis-26071").
    description : str
        What this workflow does.
    context : str
        JSON string with initial context data.

    Returns
    -------
    str
        Workflow ID or error message.
    """
    if _workflow_store is None:
        return '{"error": "Workflow store not initialized"}'

    try:
        import json

        ctx = json.loads(context) if context else {}
        wf_id = _workflow_store.start_workflow(name, ctx)
        return json.dumps({
            "workflow_id": wf_id,
            "name": name,
            "status": "running",
            "message": f"Workflow '{name}' created successfully",
        }, ensure_ascii=False)
    except Exception as e:
        logger.exception("create_workflow failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def tool_list_workflows(status: str = "", limit: int = 10) -> str:
    """List active or recent workflows.

    Parameters
    ----------
    status : str
        Filter by status: "active", "done", "failed", or "" for all.
    limit : int
        Max workflows to return.
    """
    if _workflow_store is None:
        return '{"error": "Workflow store not initialized"}'

    try:
        import json

        if status == "active":
            workflows = _workflow_store.get_active_workflows(limit)
        else:
            workflows = _workflow_store.list_workflows(limit)

        result = []
        for wf in workflows:
            result.append({
                "id": wf.id,
                "name": wf.name,
                "status": wf.status,
                "summary": wf.summary[:100] if wf.summary else "",
            })

        return json.dumps({"workflows": result, "count": len(result)}, ensure_ascii=False)
    except Exception as e:
        logger.exception("list_workflows failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def tool_update_workflow(workflow_id: str, status: str = "", summary: str = "") -> str:
    """Update workflow status and summary.

    Parameters
    ----------
    workflow_id : str
        The workflow ID to update.
    status : str
        New status: "done" or "failed".
    summary : str
        Brief summary of the outcome.
    """
    if _workflow_store is None:
        return '{"error": "Workflow store not initialized"}'

    try:
        import json

        _workflow_store.update_workflow(workflow_id, status=status, summary=summary)
        return json.dumps({"workflow_id": workflow_id, "status": status, "message": "Updated"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("update_workflow failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
