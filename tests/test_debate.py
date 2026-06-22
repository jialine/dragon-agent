"""
Unit tests for dragon.debate — GoalBackwardEngine and supporting types.

Pure unit tests: no LLM calls, no async, no network.
Tests cover:
  - GoalState: creation, to_prompt_text, __hash__
  - ActionNode: creation, post_init, tree methods, serialization, repr
  - _extract_json: valid/invalid JSON, markdown fences, noise, edge cases
  - _score_plan: cost, depth, action count scoring
  - GoalBackwardEngine: constructor, constants, tree utilities
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from dragon.debate import (
    ActionNode,
    DECOMPOSITION_PROMPT,
    DECOMPOSITION_SYSTEM_PROMPT,
    GoalBackwardEngine,
    GoalState,
)
from dragon.debate.__init__ import (
    _extract_json,
    _score_plan,
    COST_SCORE_MAP,
)


# ═══════════════════════════════════════════════════════════════════════
# GoalState
# ═══════════════════════════════════════════════════════════════════════

class TestGoalState:
    """Tests for the GoalState dataclass."""

    def test_create_with_all_fields(self):
        gs = GoalState(
            description="提高客户复购率20%",
            measurable_criteria=["复购率 >= 20%", "月度复购人数 >= 500"],
            constraints=["预算不超过10万", "3个月内完成"],
        )
        assert gs.description == "提高客户复购率20%"
        assert gs.measurable_criteria == ["复购率 >= 20%", "月度复购人数 >= 500"]
        assert gs.constraints == ["预算不超过10万", "3个月内完成"]

    def test_create_defaults(self):
        gs = GoalState(description="一个简单目标")
        assert gs.description == "一个简单目标"
        assert gs.measurable_criteria == []
        assert gs.constraints == []

    def test_to_prompt_text_no_criteria_or_constraints(self):
        gs = GoalState(description="提高员工满意度")
        text = gs.to_prompt_text()
        assert text == "目标: 提高员工满意度"

    def test_to_prompt_text_with_criteria_only(self):
        gs = GoalState(
            description="降低客诉率",
            measurable_criteria=["客诉率 < 5%", "响应时间 < 2小时"],
        )
        text = gs.to_prompt_text()
        assert "目标: 降低客诉率" in text
        assert "可衡量标准: 客诉率 < 5%, 响应时间 < 2小时" in text
        assert "约束条件:" not in text

    def test_to_prompt_text_with_constraints_only(self):
        gs = GoalState(
            description="上线新功能",
            constraints=["Q3之前完成", "预算50万以内"],
        )
        text = gs.to_prompt_text()
        assert "目标: 上线新功能" in text
        assert "约束条件: Q3之前完成, 预算50万以内" in text
        assert "可衡量标准:" not in text

    def test_to_prompt_text_with_both(self):
        gs = GoalState(
            description="提升品牌知名度",
            measurable_criteria=["搜索量提升30%"],
            constraints=["6个月", "预算200万"],
        )
        text = gs.to_prompt_text()
        assert "目标: 提升品牌知名度" in text
        assert "可衡量标准: 搜索量提升30%" in text
        assert "约束条件: 6个月, 预算200万" in text

    def test_hash_same_description(self):
        a = GoalState(description="目标A", constraints=["x"])
        b = GoalState(description="目标A", constraints=["y"])
        assert hash(a) == hash(b)

    def test_hash_different_description(self):
        a = GoalState(description="目标A")
        b = GoalState(description="目标B")
        assert hash(a) != hash(b)

    def test_hashable_in_set(self):
        """GoalState can be used in a set (dedup by description)."""
        s = {
            GoalState(description="A"),
            GoalState(description="A"),
            GoalState(description="B"),
        }
        assert len(s) == 2


# ═══════════════════════════════════════════════════════════════════════
# ActionNode
# ═══════════════════════════════════════════════════════════════════════

class TestActionNodeCreation:
    """Tests for ActionNode instantiation and post_init."""

    def test_create_minimal(self):
        node = ActionNode(goal="测试目标")
        assert node.goal == "测试目标"
        assert node.depth == 0
        assert node.prerequisites == []
        assert node.actions == []
        assert node.children == []
        assert node.estimated_cost == "中"
        assert node.estimated_time == "未知"
        assert node.can_execute_directly is False

    def test_create_full(self):
        node = ActionNode(
            goal="子目标",
            prerequisites=["条件1", "条件2"],
            actions=["行动A", "行动B"],
            satisfied=["条件1"],
            missing=["条件2"],
            estimated_cost="高",
            estimated_time="3天",
            depth=2,
            can_execute_directly=True,
            reasoning="这是一个测试",
        )
        assert node.goal == "子目标"
        assert node.prerequisites == ["条件1", "条件2"]
        assert node.actions == ["行动A", "行动B"]
        assert node.satisfied == ["条件1"]
        assert node.missing == ["条件2"]
        assert node.estimated_cost == "高"
        assert node.estimated_time == "3天"
        assert node.depth == 2
        assert node.can_execute_directly is True
        assert node.reasoning == "这是一个测试"

    def test_post_init_generates_node_id(self):
        node = ActionNode(goal="目标", depth=1)
        assert node.node_id != ""
        assert len(node.node_id) == 12  # md5 hexdigest[:12]

    def test_post_init_preserves_explicit_node_id(self):
        node = ActionNode(goal="目标", depth=0, node_id="custom-123")
        assert node.node_id == "custom-123"

    def test_same_goal_depth_produces_same_node_id(self):
        a = ActionNode(goal="目标X", depth=3)
        b = ActionNode(goal="目标X", depth=3)
        assert a.node_id == b.node_id

    def test_different_depth_produces_different_node_id(self):
        a = ActionNode(goal="目标X", depth=0)
        b = ActionNode(goal="目标X", depth=1)
        assert a.node_id != b.node_id


class TestActionNodeTree:
    """Tests for ActionNode tree manipulation methods."""

    def test_add_child_sets_parent(self):
        parent = ActionNode(goal="父节点")
        child = ActionNode(goal="子节点", depth=1)
        parent.add_child(child)
        assert child.parent is parent
        assert len(parent.children) == 1
        assert parent.children[0] is child

    def test_add_child_appends(self):
        parent = ActionNode(goal="父节点")
        c1 = ActionNode(goal="子1", depth=1)
        c2 = ActionNode(goal="子2", depth=1)
        parent.add_child(c1)
        parent.add_child(c2)
        assert parent.children == [c1, c2]

    def test_is_leaf_no_children(self):
        node = ActionNode(goal="叶子")
        assert node.is_leaf is True

    def test_is_leaf_with_children(self):
        parent = ActionNode(goal="父")
        parent.add_child(ActionNode(goal="子", depth=1))
        assert parent.is_leaf is False

    def test_is_root_no_parent(self):
        node = ActionNode(goal="根")
        assert node.is_root is True

    def test_is_root_with_parent(self):
        parent = ActionNode(goal="父")
        child = ActionNode(goal="子", depth=1)
        parent.add_child(child)
        assert parent.is_root is True
        assert child.is_root is False

    def test_path_from_root_single_node(self):
        root = ActionNode(goal="根")
        path = root.path_from_root()
        assert path == [root]

    def test_path_from_root_three_levels(self):
        root = ActionNode(goal="根")
        mid = ActionNode(goal="中", depth=1)
        leaf = ActionNode(goal="叶", depth=2)
        root.add_child(mid)
        mid.add_child(leaf)
        path = leaf.path_from_root()
        assert path == [root, mid, leaf]
        assert [n.goal for n in path] == ["根", "中", "叶"]

    def test_flatten_actions_single_node(self):
        node = ActionNode(goal="根", actions=["A", "B"])
        assert node.flatten_actions() == ["A", "B"]

    def test_flatten_actions_multi_level(self):
        root = ActionNode(goal="根", actions=["A"])
        child = ActionNode(goal="子", depth=1, actions=["B", "C"])
        grandchild = ActionNode(goal="孙", depth=2, actions=["D"])
        root.add_child(child)
        child.add_child(grandchild)
        assert grandchild.flatten_actions() == ["A", "B", "C", "D"]

    def test_flatten_actions_leaf_no_actions(self):
        root = ActionNode(goal="根", actions=["A"])
        child = ActionNode(goal="子", depth=1)
        root.add_child(child)
        assert child.flatten_actions() == ["A"]


class TestActionNodeSerialization:
    """Tests for to_dict, print_tree, __repr__."""

    def test_to_dict_basic(self):
        node = ActionNode(goal="目标", depth=1, estimated_cost="低")
        d = node.to_dict()
        assert d["node_id"] == node.node_id
        assert d["goal"] == "目标"
        assert d["depth"] == 1
        assert d["estimated_cost"] == "低"
        assert d["children"] == []

    def test_to_dict_with_children(self):
        parent = ActionNode(goal="父", depth=0)
        child = ActionNode(goal="子", depth=1)
        parent.add_child(child)
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["goal"] == "子"
        assert d["children"][0]["depth"] == 1
        assert "children" in d["children"][0]

    def test_to_dict_includes_all_top_level_keys(self):
        expected_keys = {
            "node_id", "goal", "prerequisites", "actions", "satisfied",
            "missing", "estimated_cost", "estimated_time", "depth",
            "can_execute_directly", "reasoning", "children",
        }
        node = ActionNode(goal="全字段", depth=0)
        d = node.to_dict()
        assert set(d.keys()) == expected_keys

    def test_print_tree_contains_goal(self):
        node = ActionNode(goal="测试目标", actions=["行动A"])
        output = node.print_tree()
        assert "├─" in output
        assert "测试目标" in output
        assert "▶ 行动A" in output

    def test_print_tree_with_missing(self):
        node = ActionNode(goal="目标", missing=["缺失1"])
        output = node.print_tree()
        assert "缺失: 缺失1" in output

    def test_print_tree_with_satisfied(self):
        node = ActionNode(goal="目标", satisfied=["已具备1"])
        output = node.print_tree()
        assert "已具备: 已具备1" in output

    def test_print_tree_recursive(self):
        parent = ActionNode(goal="父", actions=["P"], estimated_cost="低")
        child = ActionNode(goal="子", depth=1, actions=["C"], estimated_cost="中")
        parent.add_child(child)
        output = parent.print_tree()
        assert "├─ [低] 父" in output
        assert "  ├─ [中] 子" in output
        assert "▶ P" in output
        assert "▶ C" in output

    def test_repr_format(self):
        node = ActionNode(goal="目标", depth=3, estimated_cost="高")
        r = repr(node)
        assert "ActionNode" in r
        assert "目标" in r
        assert "depth=3" in r
        assert "cost=高" in r
        assert "children=0" in r

    def test_repr_with_children(self):
        parent = ActionNode(goal="父")
        parent.add_child(ActionNode(goal="子1", depth=1))
        parent.add_child(ActionNode(goal="子2", depth=1))
        r = repr(parent)
        assert "children=2" in r


# ═══════════════════════════════════════════════════════════════════════
# _extract_json
# ═══════════════════════════════════════════════════════════════════════

class TestExtractJson:
    """Tests for _extract_json helper."""

    def test_valid_json_dict(self):
        result = _extract_json('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_valid_json_list(self):
        result = _extract_json('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_valid_json_nested(self):
        result = _extract_json(
            '{"outer": {"inner": [1, 2], "deep": {"x": true}}}'
        )
        assert result == {"outer": {"inner": [1, 2], "deep": {"x": True}}}

    def test_markdown_fenced_json(self):
        text = '```json\n{"result": "ok"}\n```'
        result = _extract_json(text)
        assert result == {"result": "ok"}

    def test_markdown_fenced_no_lang(self):
        text = '```\n{"result": "ok"}\n```'
        result = _extract_json(text)
        assert result == {"result": "ok"}

    def test_markdown_fenced_with_noise(self):
        text = 'Here is my response:\n```json\n{"answer": 42}\n```\nHope this helps!'
        result = _extract_json(text)
        assert result == {"answer": 42}

    def test_leading_and_trailing_noise(self):
        text = 'Some text before {"valid": "json"} and some after'
        result = _extract_json(text)
        assert result == {"valid": "json"}

    def test_nested_braces_deep(self):
        text = 'noise {"a": {"b": {"c": 3, "d": [1,2]}}} more noise'
        result = _extract_json(text)
        assert result == {"a": {"b": {"c": 3, "d": [1, 2]}}}

    def test_empty_string(self):
        assert _extract_json("") is None

    def test_none_input(self):
        assert _extract_json(None) is None  # type: ignore[arg-type]

    def test_whitespace_only(self):
        assert _extract_json("   \n\t  ") is None

    def test_invalid_json(self):
        assert _extract_json("{invalid json!!!") is None

    def test_almost_valid_json(self):
        # Missing closing brace — balanced brace extraction should fail
        assert _extract_json('{"key": "value"') is None

    def test_multiple_json_objects_picks_first_fenced(self):
        text = '```json\n{"first": 1}\n```\n```json\n{"second": 2}\n```'
        result = _extract_json(text)
        assert result == {"first": 1}

    def test_string_with_brace_literals(self):
        """Braces inside strings should not confuse extraction."""
        text = '{"key": "value with {brace} inside"}'
        result = _extract_json(text)
        assert result == {"key": "value with {brace} inside"}


# ═══════════════════════════════════════════════════════════════════════
# _score_plan
# ═══════════════════════════════════════════════════════════════════════

class TestScorePlan:
    """Tests for _score_plan function."""

    def test_empty_actions_defaults(self):
        """Empty actions: count penalty on 1 (max(1, 0)), avg cost 0.5, depth 0."""
        score = _score_plan(actions=[], total_depth=0, cost_labels=[])
        # depth_score = 0/5 = 0.0
        # cost_score = 0.5
        # count_penalty = min(0.1*(1/5), 0.3) = 0.02
        # combined = 0.4*0 + 0.5*0.5 + 0.02 = 0.27
        assert score == pytest.approx(0.27)

    def test_shallow_low_cost(self):
        score = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["低"]
        )
        assert score < 0.3  # should be very low (good)

    def test_deep_high_cost(self):
        score = _score_plan(
            actions=["A"], total_depth=5, cost_labels=["高"]
        )
        assert score > 0.6  # should be high (bad)

    def test_depth_zero_vs_depth_five(self):
        score_shallow = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["中"]
        )
        score_deep = _score_plan(
            actions=["A"], total_depth=5, cost_labels=["中"]
        )
        assert score_shallow < score_deep

    def test_all_low_cost_vs_all_high_cost(self):
        score_low = _score_plan(
            actions=["A", "B"], total_depth=0,
            cost_labels=["低", "低"],
        )
        score_high = _score_plan(
            actions=["A", "B"], total_depth=0,
            cost_labels=["高", "高"],
        )
        assert score_low < score_high

    def test_many_actions_higher_penalty(self):
        score_few = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["中"]
        )
        score_many = _score_plan(
            actions=["A"] * 10, total_depth=0,
            cost_labels=["中"] * 10,
        )
        assert score_few < score_many

    def test_empty_cost_labels_uses_default(self):
        score = _score_plan(
            actions=["A", "B"], total_depth=0, cost_labels=[]
        )
        # cost_score = 0.5 (default)
        assert 0.2 < score < 0.4

    def test_score_in_range_0_to_1(self):
        """Score should always be in [0, 1]."""
        for depth in (0, 3, 5, 10):
            for costs in (["低"], ["中"], ["高"], ["低", "中", "高"]):
                score = _score_plan(
                    actions=["A"] * len(costs),
                    total_depth=depth,
                    cost_labels=costs,
                )
                assert 0.0 <= score <= 1.0, (
                    f"depth={depth}, costs={costs}, score={score}"
                )

    def test_unknown_cost_label_treated_as_medium(self):
        """Unknown cost labels default to 0.5 (中)."""
        score_known = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["中"]
        )
        score_unknown = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["UNKNOWN"]
        )
        assert score_known == score_unknown

    def test_max_possible_depth_custom(self):
        score_default = _score_plan(
            actions=["A"], total_depth=5, cost_labels=["中"],
            max_possible_depth=5,
        )
        score_custom = _score_plan(
            actions=["A"], total_depth=5, cost_labels=["中"],
            max_possible_depth=10,
        )
        # With max_possible_depth=5, depth_score=1.0; with 10, depth_score=0.5
        assert score_default > score_custom

    def test_single_action(self):
        score = _score_plan(
            actions=["一个行动"], total_depth=1, cost_labels=["低"]
        )
        assert 0.0 < score < 0.5

    def test_max_depth_capped_at_1(self):
        """Depth beyond max_possible_depth should be capped at 1.0."""
        score = _score_plan(
            actions=["A"], total_depth=100, cost_labels=["中"],
            max_possible_depth=5,
        )
        # depth_score = min(100/5, 1.0) = 1.0 — capped
        # Should still be ≤ 1.0
        assert score <= 1.0


# ═══════════════════════════════════════════════════════════════════════
# COST_SCORE_MAP
# ═══════════════════════════════════════════════════════════════════════

class TestCostScoreMap:
    """Tests for the COST_SCORE_MAP constant."""

    def test_low_is_smallest(self):
        assert COST_SCORE_MAP["低"] < COST_SCORE_MAP["中"]
        assert COST_SCORE_MAP["低"] < COST_SCORE_MAP["高"]

    def test_high_is_largest(self):
        assert COST_SCORE_MAP["高"] > COST_SCORE_MAP["中"]
        assert COST_SCORE_MAP["高"] > COST_SCORE_MAP["低"]

    def test_medium_is_between(self):
        assert COST_SCORE_MAP["低"] < COST_SCORE_MAP["中"] < COST_SCORE_MAP["高"]

    def test_all_values_in_range(self):
        for v in COST_SCORE_MAP.values():
            assert 0.0 <= v <= 1.0


# ═══════════════════════════════════════════════════════════════════════
# GoalBackwardEngine — constructor & constants
# ═══════════════════════════════════════════════════════════════════════

class TestGoalBackwardEngineConstructor:
    """Tests for GoalBackwardEngine initialization and constants."""

    def test_max_depth_constant(self):
        assert GoalBackwardEngine.MAX_DECOMPOSITION_DEPTH == 8

    def test_constructor_sets_dispatcher(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        assert engine.dispatcher is dispatcher

    def test_constructor_defaults(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        assert engine.default_industry == "general"
        assert engine.default_temperature == pytest.approx(0.3)

    def test_constructor_custom_industry(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher, default_industry="legal")
        assert engine.default_industry == "legal"

    def test_constructor_custom_temperature(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher, default_temperature=0.7)
        assert engine.default_temperature == pytest.approx(0.7)

    def test_constructor_initializes_visited_cache(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        assert engine._visited == {}

    def test_default_industry_class_attr(self):
        assert GoalBackwardEngine.DEFAULT_INDUSTRY == "general"

    def test_default_temperature_class_attr(self):
        assert GoalBackwardEngine.DEFAULT_TEMPERATURE == pytest.approx(0.3)


# ═══════════════════════════════════════════════════════════════════════
# GoalBackwardEngine — static/utility methods
# ═══════════════════════════════════════════════════════════════════════

class TestGoalBackwardEngineUtilities:
    """Tests for GoalBackwardEngine utility methods (no LLM needed)."""

    def test_count_nodes_single(self):
        root = ActionNode(goal="根")
        count = GoalBackwardEngine._count_nodes(root)
        assert count == 1

    def test_count_nodes_tree(self):
        root = ActionNode(goal="根")
        c1 = ActionNode(goal="子1", depth=1)
        c2 = ActionNode(goal="子2", depth=1)
        gc = ActionNode(goal="孙", depth=2)
        root.add_child(c1)
        root.add_child(c2)
        c1.add_child(gc)
        # 根 + 子1 + 孙 + 子2 = 4
        count = GoalBackwardEngine._count_nodes(root)
        assert count == 4

    def test_get_all_nodes_flat(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根")
        c1 = ActionNode(goal="子1", depth=1)
        c2 = ActionNode(goal="子2", depth=1)
        root.add_child(c1)
        root.add_child(c2)
        nodes = engine.get_all_nodes(root)
        assert len(nodes) == 3
        goals = {n.goal for n in nodes}
        assert goals == {"根", "子1", "子2"}

    def test_get_all_nodes_single(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="唯一")
        nodes = engine.get_all_nodes(root)
        assert len(nodes) == 1
        assert nodes[0].goal == "唯一"

    def test_get_leaves_all_leaves(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        leaf1 = ActionNode(goal="叶1", depth=1)
        leaf2 = ActionNode(goal="叶2", depth=1)
        root = ActionNode(goal="根")
        root.add_child(leaf1)
        root.add_child(leaf2)
        leaves = engine.get_leaves(root)
        assert len(leaves) == 2
        assert {l.goal for l in leaves} == {"叶1", "叶2"}

    def test_get_leaves_deep(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根")
        mid = ActionNode(goal="中", depth=1)
        leaf = ActionNode(goal="叶", depth=2)
        root.add_child(mid)
        mid.add_child(leaf)
        leaves = engine.get_leaves(root)
        assert len(leaves) == 1
        assert leaves[0].goal == "叶"

    def test_to_json(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根", actions=["A"])
        result = engine.to_json(root)
        parsed = json.loads(result)
        assert parsed["goal"] == "根"
        assert parsed["actions"] == ["A"]

    def test_to_dict_method(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根", depth=0)
        child = ActionNode(goal="子", depth=1)
        root.add_child(child)
        result = engine.to_dict(root)
        assert result["goal"] == "根"
        assert len(result["children"]) == 1
        assert result["children"][0]["goal"] == "子"

    def test_print_tree_delegates(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根")
        output = engine.print_tree(root)
        assert output == root.print_tree()

    def test_generate_plans_none_root(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        plans = engine.generate_plans(None)  # type: ignore[arg-type]
        assert plans == []

    def test_generate_plans_single_leaf(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根", actions=["行动A", "行动B"])
        plans = engine.generate_plans(root)
        assert len(plans) >= 1
        assert plans[0] == ["行动A", "行动B"]

    def test_generate_plans_multi_branch(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        root = ActionNode(goal="根", actions=["根行动"])
        branch1 = ActionNode(goal="分支1", depth=1, actions=["行动1"])
        branch2 = ActionNode(goal="分支2", depth=1, actions=["行动2"])
        root.add_child(branch1)
        root.add_child(branch2)
        plans = engine.generate_plans(root)
        # Two root-to-leaf paths
        assert len(plans) == 2
        # Each plan should contain root action + branch action
        all_actions = {tuple(p) for p in plans}
        assert ("根行动", "行动1") in all_actions
        assert ("根行动", "行动2") in all_actions

    def test_generate_plans_sorted_by_score(self):
        """Better plans (lower score) come first."""
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        # Branch 1: deep + high cost → worse
        # Branch 2: shallow + low cost → better
        root = ActionNode(goal="根")
        deep_leaf = ActionNode(goal="深叶", depth=3, actions=["D"],
                               estimated_cost="高")
        shallow_leaf = ActionNode(goal="浅叶", depth=1, actions=["S"],
                                  estimated_cost="低")
        root.add_child(deep_leaf)
        root.add_child(shallow_leaf)
        plans = engine.generate_plans(root)
        # Shallow plan should come first
        assert plans[0] == ["S"]


# ═══════════════════════════════════════════════════════════════════════
# Prompt Templates
# ═══════════════════════════════════════════════════════════════════════

class TestPromptTemplates:
    """Smoke tests for prompt template constants."""

    def test_decomposition_system_prompt_exists(self):
        assert isinstance(DECOMPOSITION_SYSTEM_PROMPT, str)
        assert len(DECOMPOSITION_SYSTEM_PROMPT) > 100

    def test_decomposition_prompt_exists(self):
        assert isinstance(DECOMPOSITION_PROMPT, str)
        assert len(DECOMPOSITION_PROMPT) > 100

    def test_decomposition_prompt_has_placeholders(self):
        assert "{goal}" in DECOMPOSITION_PROMPT
        assert "{criteria}" in DECOMPOSITION_PROMPT
        assert "{constraints}" in DECOMPOSITION_PROMPT
        assert "{current_state}" in DECOMPOSITION_PROMPT
        assert "{depth}" in DECOMPOSITION_PROMPT
        assert "{max_depth}" in DECOMPOSITION_PROMPT

    def test_decomposition_prompt_format_works(self):
        formatted = DECOMPOSITION_PROMPT.format(
            goal="测试",
            criteria="标准",
            constraints="约束",
            current_state="当前",
            depth=0,
            max_depth=5,
        )
        assert "测试" in formatted
        assert "标准" in formatted
        assert "约束" in formatted
        assert "当前" in formatted
        assert "0" in formatted
        assert "5" in formatted


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases & Regression
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case and regression tests across multiple components."""

    def test_action_node_with_unicode_goal(self):
        node = ActionNode(goal="🎯 提升客户满意度 👨‍💼")
        assert node.goal == "🎯 提升客户满意度 👨‍💼"
        assert node.node_id != ""

    def test_action_node_deeply_nested_to_dict(self):
        """Verify to_dict handles 5-level deep nesting."""
        nodes = []
        for i in range(5):
            nodes.append(ActionNode(goal=f"L{i}", depth=i))
        for i in range(4):
            nodes[i].add_child(nodes[i + 1])
        d = nodes[0].to_dict()
        # Should not raise
        assert d["children"][0]["children"][0]["children"][0]["children"] is not None

    def test_extract_json_real_llm_response_chinese(self):
        """Simulate a realistic Chinese LLM JSON response."""
        text = """
        好的，让我分析一下...

        ```json
        {
            "prerequisites": ["需要市场调研数据", "需要技术团队支持"],
            "satisfied": ["已有客户基础"],
            "missing": ["缺乏竞品分析"],
            "actions": [
                {
                    "for": "缺乏竞品分析",
                    "action": "进行竞品调研",
                    "cost": "中",
                    "time": "1周"
                }
            ],
            "can_execute_directly": false,
            "reasoning": "需要先完成市场调研"
        }
        ```

        以上是我的分析结果。
        """
        result = _extract_json(text)
        assert result is not None
        assert len(result["prerequisites"]) == 2
        assert result["can_execute_directly"] is False

    def test_extract_json_string_with_json_like_content(self):
        """String containing json-like content that isn't valid."""
        text = "The output should look like: {'key': value}"
        result = _extract_json(text)
        assert result is None

    def test_score_plan_zero_depth_max_possible_one(self):
        """max_possible_depth might be 1; ensure no ZeroDivisionError."""
        score = _score_plan(
            actions=["A"], total_depth=0, cost_labels=["低"],
            max_possible_depth=1,
        )
        # depth_score = 0/1 = 0
        assert score >= 0.0

    def test_goal_backward_engine_repr(self):
        dispatcher = MagicMock()
        engine = GoalBackwardEngine(dispatcher)
        r = repr(engine)
        assert "GoalBackwardEngine" in r or "object" in r  # default repr at minimum
