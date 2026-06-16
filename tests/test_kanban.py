"""
Tests for Dragon Agent Kanban Project Management Tools.

Uses pytest-asyncio for async tool functions.
Storage directory is set to a temporary location via monkeypatch.
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_kanban_dir(tmp_path):
    """Temporary storage directory for Kanban data."""
    kanban_dir = tmp_path / "kanban"
    kanban_dir.mkdir(parents=True, exist_ok=True)
    return kanban_dir


@pytest.fixture
def patch_storage(temp_kanban_dir, monkeypatch):
    """Monkey-patch the kanban module to use a temp storage dir."""
    import dragon.tool.builtins.kanban as kmod

    monkeypatch.setattr(kmod, "STORAGE_DIR", temp_kanban_dir)
    return temp_kanban_dir


def parse(result: str) -> dict:
    """Parse JSON tool result."""
    return json.loads(result)


# ────────────────────────────────────────────────────────────────────
# Test: Create Board
# ────────────────────────────────────────────────────────────────────


class TestKanbanCreateBoard:
    @pytest.mark.asyncio
    async def test_create_board_success(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board

        result = parse(await tool_kanban_create_board("My Project"))
        assert result["board"] == "My Project"
        assert result["status"] == "created"
        assert result["task_count"] == 0
        assert Path(result["file"]).exists()
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_create_board_already_exists(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board

        await tool_kanban_create_board("Existing Board")
        result = parse(await tool_kanban_create_board("Existing Board"))
        assert result["status"] == "exists"
        assert result["board"] == "Existing Board"

    @pytest.mark.asyncio
    async def test_create_board_empty_name(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board

        result = parse(await tool_kanban_create_board(""))
        assert "error" in result

        result = parse(await tool_kanban_create_board("   "))
        assert "error" in result


# ────────────────────────────────────────────────────────────────────
# Test: Add Task
# ────────────────────────────────────────────────────────────────────


class TestKanbanAddTask:
    @pytest.mark.asyncio
    async def test_add_task_success(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Dev Board")
        result = parse(await tool_kanban_add_task(
            board="Dev Board",
            title="Set up CI/CD",
            description="Configure GitHub Actions",
            status="todo",
            priority="high",
        ))
        assert result["board"] == "Dev Board"
        assert result["task"]["title"] == "Set up CI/CD"
        assert result["task"]["description"] == "Configure GitHub Actions"
        assert result["task"]["status"] == "todo"
        assert result["task"]["priority"] == "high"
        # Validate UUID
        uuid.UUID(result["task"]["id"])
        assert "created_at" in result["task"]
        assert "updated_at" in result["task"]

    @pytest.mark.asyncio
    async def test_add_task_defaults(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Default Board")
        result = parse(await tool_kanban_add_task(
            board="Default Board",
            title="A simple task",
        ))
        assert result["task"]["status"] == "todo"
        assert result["task"]["priority"] == "medium"
        assert result["task"]["description"] == ""

    @pytest.mark.asyncio
    async def test_add_task_board_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_add_task

        result = parse(await tool_kanban_add_task(
            board="Ghost Board",
            title="Some task",
        ))
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_add_task_invalid_status(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Status Board")
        result = parse(await tool_kanban_add_task(
            board="Status Board",
            title="Bad status task",
            status="backlog",
        ))
        assert "error" in result
        assert "Invalid status" in result["error"]

    @pytest.mark.asyncio
    async def test_add_task_invalid_priority(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Priority Board")
        result = parse(await tool_kanban_add_task(
            board="Priority Board",
            title="Bad priority task",
            priority="urgent",
        ))
        assert "error" in result
        assert "Invalid priority" in result["error"]

    @pytest.mark.asyncio
    async def test_add_task_empty_title(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Title Board")
        result = parse(await tool_kanban_add_task(
            board="Title Board",
            title="",
        ))
        assert "error" in result
        assert "title" in result["error"].lower()


# ────────────────────────────────────────────────────────────────────
# Test: List Tasks
# ────────────────────────────────────────────────────────────────────


class TestKanbanList:
    @pytest.mark.asyncio
    async def test_list_all_tasks(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_list,
        )

        await tool_kanban_create_board("List Board")
        await tool_kanban_add_task("List Board", "Task 1", status="todo")
        await tool_kanban_add_task("List Board", "Task 2", status="in_progress")
        await tool_kanban_add_task("List Board", "Task 3", status="done")

        result = parse(await tool_kanban_list("List Board"))
        assert result["board"] == "List Board"
        assert result["total_tasks"] == 3
        assert result["count"] == 3
        assert len(result["tasks"]) == 3
        assert result["status_counts"]["todo"] == 1
        assert result["status_counts"]["in_progress"] == 1
        assert result["status_counts"]["done"] == 1

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_list,
        )

        await tool_kanban_create_board("Filter Board")
        await tool_kanban_add_task("Filter Board", "Todo task", status="todo")
        await tool_kanban_add_task("Filter Board", "Done task", status="done")

        result = parse(await tool_kanban_list("Filter Board", status="done"))
        assert result["count"] == 1
        assert result["tasks"][0]["title"] == "Done task"
        assert result["filter"] == "done"

    @pytest.mark.asyncio
    async def test_list_empty_board(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_list

        await tool_kanban_create_board("Empty Board")
        result = parse(await tool_kanban_list("Empty Board"))
        assert result["total_tasks"] == 0
        assert result["count"] == 0
        assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_list_board_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_list

        result = parse(await tool_kanban_list("Missing Board"))
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_list_invalid_status_filter(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_list

        await tool_kanban_create_board("Bad Filter")
        result = parse(await tool_kanban_list("Bad Filter", status="archived"))
        assert "error" in result
        assert "Invalid status" in result["error"]


# ────────────────────────────────────────────────────────────────────
# Test: Move Task
# ────────────────────────────────────────────────────────────────────


class TestKanbanMove:
    @pytest.mark.asyncio
    async def test_move_task_success(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_move,
            tool_kanban_list,
        )

        await tool_kanban_create_board("Move Board")
        add = parse(await tool_kanban_add_task("Move Board", "Movable task"))
        task_id = add["task"]["id"]

        result = parse(await tool_kanban_move(task_id, "Move Board", "in_progress"))
        assert result["board"] == "Move Board"
        assert result["old_status"] == "todo"
        assert result["new_status"] == "in_progress"
        assert result["task"]["status"] == "in_progress"

        # Verify persistence
        listed = parse(await tool_kanban_list("Move Board"))
        assert listed["tasks"][0]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_move_task_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_move

        await tool_kanban_create_board("Move Board 2")
        result = parse(await tool_kanban_move(
            "00000000-0000-0000-0000-000000000000",
            "Move Board 2",
            "done",
        ))
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_move_task_invalid_status(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_move,
        )

        await tool_kanban_create_board("Move Board 3")
        add = parse(await tool_kanban_add_task("Move Board 3", "Bad move"))
        result = parse(await tool_kanban_move(
            add["task"]["id"], "Move Board 3", "archived",
        ))
        assert "error" in result
        assert "Invalid status" in result["error"]

    @pytest.mark.asyncio
    async def test_move_task_board_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_move

        result = parse(await tool_kanban_move("some-id", "No Board", "done"))
        assert "error" in result
        assert "not found" in result["error"]


# ────────────────────────────────────────────────────────────────────
# Test: Delete Task
# ────────────────────────────────────────────────────────────────────


class TestKanbanDeleteTask:
    @pytest.mark.asyncio
    async def test_delete_task_success(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_delete_task,
            tool_kanban_list,
        )

        await tool_kanban_create_board("Delete Board")
        add = parse(await tool_kanban_add_task("Delete Board", "Task to delete"))
        task_id = add["task"]["id"]

        result = parse(await tool_kanban_delete_task(task_id, "Delete Board"))
        assert result["board"] == "Delete Board"
        assert result["task"]["title"] == "Task to delete"
        assert result["remaining_tasks"] == 0

        # Verify it's gone
        listed = parse(await tool_kanban_list("Delete Board"))
        assert listed["total_tasks"] == 0

    @pytest.mark.asyncio
    async def test_delete_task_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_delete_task

        await tool_kanban_create_board("Delete Board 2")
        result = parse(await tool_kanban_delete_task(
            "00000000-0000-0000-0000-000000000000",
            "Delete Board 2",
        ))
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_task_board_not_found(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_delete_task

        result = parse(await tool_kanban_delete_task("some-id", "Ghost"))
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_task_empty_id(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_delete_task

        await tool_kanban_create_board("Empty ID Board")
        result = parse(await tool_kanban_delete_task("", "Empty ID Board"))
        assert "error" in result
        assert "Task ID" in result["error"]


# ────────────────────────────────────────────────────────────────────
# Test: List Boards
# ────────────────────────────────────────────────────────────────────


class TestKanbanListBoards:
    @pytest.mark.asyncio
    async def test_list_boards_empty(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_list_boards

        result = parse(await tool_kanban_list_boards())
        assert result["count"] == 0
        assert result["boards"] == []

    @pytest.mark.asyncio
    async def test_list_boards_multiple(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_list_boards,
        )

        await tool_kanban_create_board("Project Alpha")
        await tool_kanban_create_board("Project Beta")
        await tool_kanban_add_task("Project Alpha", "Task A1")
        await tool_kanban_add_task("Project Alpha", "Task A2")
        await tool_kanban_add_task("Project Beta", "Task B1")

        result = parse(await tool_kanban_list_boards())
        assert result["count"] == 2
        names = [b["name"] for b in result["boards"]]
        assert "Project Alpha" in names
        assert "Project Beta" in names

        # Check task counts
        alpha = next(b for b in result["boards"] if b["name"] == "Project Alpha")
        beta = next(b for b in result["boards"] if b["name"] == "Project Beta")
        assert alpha["task_count"] == 2
        assert beta["task_count"] == 1


# ────────────────────────────────────────────────────────────────────
# Test: Integration / End-to-End Flow
# ────────────────────────────────────────────────────────────────────


class TestKanbanIntegration:
    @pytest.mark.asyncio
    async def test_full_workflow(self, patch_storage):
        """Simulate the complete lifecycle of a Kanban board."""
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_list,
            tool_kanban_move,
            tool_kanban_delete_task,
            tool_kanban_list_boards,
        )

        # 1. Create board
        r = parse(await tool_kanban_create_board("Sprint 42"))
        assert r["status"] == "created"

        # 2. Add tasks
        t1 = parse(await tool_kanban_add_task(
            "Sprint 42", "Login page", priority="high",
        ))
        t2 = parse(await tool_kanban_add_task(
            "Sprint 42", "API docs", status="in_progress", priority="medium",
        ))
        t3 = parse(await tool_kanban_add_task(
            "Sprint 42", "Bug #1234", status="review", priority="critical",
        ))
        t4 = parse(await tool_kanban_add_task(
            "Sprint 42", "Deploy v2", status="done", priority="low",
        ))
        task_ids = [t["task"]["id"] for t in (t1, t2, t3, t4)]

        # 3. List all
        r = parse(await tool_kanban_list("Sprint 42"))
        assert r["total_tasks"] == 4
        assert r["status_counts"] == {
            "todo": 1, "in_progress": 1, "review": 1, "done": 1,
        }

        # 4. Filter by status
        r = parse(await tool_kanban_list("Sprint 42", status="critical"))
        # 'critical' is a priority, not a status — filter by valid status
        r = parse(await tool_kanban_list("Sprint 42", status="done"))
        assert r["count"] == 1
        assert r["tasks"][0]["title"] == "Deploy v2"

        # 5. Move tasks
        r = parse(await tool_kanban_move(task_ids[0], "Sprint 42", "in_progress"))
        assert r["new_status"] == "in_progress"
        assert r["old_status"] == "todo"

        r = parse(await tool_kanban_move(task_ids[0], "Sprint 42", "review"))
        assert r["new_status"] == "review"

        r = parse(await tool_kanban_move(task_ids[0], "Sprint 42", "done"))
        assert r["new_status"] == "done"
        assert r["old_status"] == "review"

        # 6. Verify final state
        r = parse(await tool_kanban_list("Sprint 42"))
        assert r["status_counts"]["done"] == 2  # Login page + Deploy v2
        assert r["status_counts"].get("todo", 0) == 0  # todo is now empty
        assert r["status_counts"]["in_progress"] == 1  # API docs

        # 7. Delete a task
        r = parse(await tool_kanban_delete_task(task_ids[3], "Sprint 42"))
        assert r["remaining_tasks"] == 3

        r = parse(await tool_kanban_list("Sprint 42"))
        assert r["total_tasks"] == 3

        # 8. List all boards
        r = parse(await tool_kanban_list_boards())
        assert r["count"] == 1


# ────────────────────────────────────────────────────────────────────
# Test: File Persistence
# ────────────────────────────────────────────────────────────────────


class TestKanbanPersistence:
    @pytest.mark.asyncio
    async def test_data_persists_to_disk(self, patch_storage):
        from dragon.tool.builtins.kanban import tool_kanban_create_board, tool_kanban_add_task

        await tool_kanban_create_board("Persist Board")
        add = parse(await tool_kanban_add_task("Persist Board", "Persistent task"))
        task_id = add["task"]["id"]

        kanban_file = patch_storage / "Persist_Board.json"
        assert kanban_file.exists()

        on_disk = json.loads(kanban_file.read_text())
        assert on_disk["name"] == "Persist Board"
        assert len(on_disk["tasks"]) == 1
        assert on_disk["tasks"][0]["id"] == task_id
        assert on_disk["tasks"][0]["title"] == "Persistent task"

    @pytest.mark.asyncio
    async def test_task_has_emoji_fields(self, patch_storage):
        from dragon.tool.builtins.kanban import (
            tool_kanban_create_board,
            tool_kanban_add_task,
            tool_kanban_list,
        )

        await tool_kanban_create_board("Emoji Board")
        await tool_kanban_add_task("Emoji Board", "Critical bug", priority="critical")
        await tool_kanban_add_task("Emoji Board", "Nice to have", priority="low", status="done")

        r = parse(await tool_kanban_list("Emoji Board"))
        t1, t2 = r["tasks"]

        assert t1["priority_emoji"] == "🔴"
        assert t1["status_emoji"] == "📋"
        assert t2["priority_emoji"] == "🟢"
        assert t2["status_emoji"] == "✅"
