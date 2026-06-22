"""
Dragon Agent — Goal Backward Engine (目标倒推引擎)

Recursive decomposition of complex goals into executable action plans
via LLM-powered backward chaining with tree-structured reasoning.

Key components:
  - GoalState: structured goal representation
  - ActionNode: tree node for recursive decomposition
  - GoalBackwardEngine: orchestrates decomposition + plan generation
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Union
import json
import logging
import asyncio
import re
import hashlib

from dragon.dispatch import DragonDispatcher, DispatchResult

# ────────────────────────────────────────────────────────────────────
# Structured logging
# ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("dragon.debate")


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════

@dataclass
class GoalState:
    """
    Structured representation of a goal for backward reasoning.

    Attributes:
        description: Human-readable goal description (e.g., "提高客户复购率20%")
        measurable_criteria: Quantifiable success criteria (e.g., ["复购率 >= 20%", "月度复购人数 >= 500"])
        constraints: Hard constraints that must be respected (e.g., ["预算不超过10万", "3个月内完成"])
    """

    description: str
    measurable_criteria: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Render goal state as prompt-friendly text."""
        parts = [f"目标: {self.description}"]
        if self.measurable_criteria:
            parts.append(f"可衡量标准: {', '.join(self.measurable_criteria)}")
        if self.constraints:
            parts.append(f"约束条件: {', '.join(self.constraints)}")
        return "\n".join(parts)

    def __hash__(self) -> int:
        """Hash based on description for deduplication in visited sets."""
        return hash(self.description)


@dataclass
class ActionNode:
    """
    Tree node in the goal-backward decomposition graph.

    Each node represents a sub-goal with its prerequisites, the actions
    needed to satisfy them, and estimated cost/time metadata.

    Attributes:
        goal: The (sub-)goal this node represents
        prerequisites: All prerequisite conditions for achieving this goal
        actions: Concrete actions to satisfy the missing prerequisites
        satisfied: Which prerequisites are already met in current state
        missing: Which prerequisites still need to be addressed
        estimated_cost: Overall cost estimate ("低"/"中"/"高")
        estimated_time: Overall time estimate (human-readable)
        depth: Current depth in the decomposition tree
        can_execute_directly: Whether this node can be acted on without further decomposition
        reasoning: LLM's reasoning trace for this decomposition step
        parent: Parent node in the tree (None for root)
        children: Child nodes representing decomposed missing prerequisites
        node_id: Unique identifier for this node (hash of goal + depth)
    """

    goal: str
    prerequisites: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    satisfied: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    estimated_cost: str = "中"
    estimated_time: str = "未知"
    depth: int = 0
    can_execute_directly: bool = False
    reasoning: str = ""
    parent: Optional["ActionNode"] = None
    children: List["ActionNode"] = field(default_factory=list)
    node_id: str = ""

    def __post_init__(self):
        if not self.node_id:
            self.node_id = hashlib.md5(
                f"{self.goal}:{self.depth}".encode()
            ).hexdigest()[:12]

    def add_child(self, child: "ActionNode") -> None:
        """Attach a child node (decomposed prerequisite)."""
        child.parent = self
        self.children.append(child)

    @property
    def is_leaf(self) -> bool:
        """A leaf node has no children — it is directly executable or at max depth."""
        return len(self.children) == 0

    @property
    def is_root(self) -> bool:
        """Whether this node is the root of the decomposition tree."""
        return self.parent is None

    def path_from_root(self) -> List["ActionNode"]:
        """Return the chain of nodes from root to self (inclusive)."""
        path: List[ActionNode] = []
        current: Optional[ActionNode] = self
        while current is not None:
            path.append(current)
            current = current.parent
        path.reverse()
        return path

    def flatten_actions(self) -> List[str]:
        """Collect all actions along the path from root to this node."""
        all_actions: List[str] = []
        for node in self.path_from_root():
            all_actions.extend(node.actions)
        return all_actions

    def to_dict(self) -> dict:
        """Serialize node to a dictionary (for debugging/export)."""
        return {
            "node_id": self.node_id,
            "goal": self.goal,
            "prerequisites": self.prerequisites,
            "actions": self.actions,
            "satisfied": self.satisfied,
            "missing": self.missing,
            "estimated_cost": self.estimated_cost,
            "estimated_time": self.estimated_time,
            "depth": self.depth,
            "can_execute_directly": self.can_execute_directly,
            "reasoning": self.reasoning,
            "children": [c.to_dict() for c in self.children],
        }

    def print_tree(self, indent: int = 0) -> str:
        """Pretty-print the decomposition tree."""
        prefix = "  " * indent
        lines = [
            f"{prefix}├─ [{self.estimated_cost}] {self.goal}",
        ]
        if self.actions:
            for action in self.actions:
                lines.append(f"{prefix}│  ▶ {action}")
        if self.missing:
            lines.append(f"{prefix}│  缺失: {', '.join(self.missing)}")
        if self.satisfied:
            lines.append(f"{prefix}│  已具备: {', '.join(self.satisfied)}")
        for child in self.children:
            lines.append(child.print_tree(indent + 1))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"ActionNode(goal={self.goal!r}, depth={self.depth}, "
            f"cost={self.estimated_cost}, children={len(self.children)})"
        )


# ════════════════════════════════════════════════════════════════════
# Decomposition Prompt Template
# ════════════════════════════════════════════════════════════════════

DECOMPOSITION_SYSTEM_PROMPT = """你是一个目标倒推分析专家。你的任务是将复杂目标递归分解为可执行的具体行动步骤。

核心原则:
1. 前置条件思维: 要达成目标X，必须先满足哪些条件？
2. 务实判断: 区分"已具备"的条件和"缺失"的条件
3. 可操作性: 每个行动必须具体、可执行，而非抽象建议
4. 成本意识: 对每个行动评估资源消耗(低/中/高)
5. 直接执行判断: 如果某个条件已经可以直接行动达成，标记can_execute_directly=true

输出必须是严格的JSON格式，不要添加任何其他文字。"""

DECOMPOSITION_PROMPT = """你现在采用目标倒推法解决问题。

最终目标: {goal}
可衡量标准: {criteria}
约束条件: {constraints}
当前已具备: {current_state}
当前深度: {depth}/{max_depth}

请回答:
1. 要达成这个目标，需要满足哪些前置条件？
2. 这些前置条件中，哪些已经具备(satisfied)？哪些缺失(missing)？
3. 对每个缺失的条件，需要采取什么具体行动？
4. 每个行动预估成本(低/中/高)和时间

返回JSON: {{"prerequisites": ["条件1", "条件2", ...], "satisfied": ["已具备的条件", ...], "missing": ["缺失的条件", ...], "actions": [{{"for": "条件", "action": "行动描述", "cost": "低", "time": "1小时"}}], "can_execute_directly": true/false, "reasoning": "分析过程简述"}}"""


# ════════════════════════════════════════════════════════════════════
# JSON Extraction Helpers
# ════════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> Optional[dict]:
    """
    Extract a JSON object from LLM response text.

    Handles:
      - Raw JSON: ``{...}``
      - Markdown-fenced JSON: `` ```json {...} ``` ``
      - Leading/trailing noise before or after the JSON block
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Attempt 1: direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract from markdown ```json ... ``` fence
    fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    matches = re.findall(fence_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # Attempt 3: find first { ... } pair (balanced braces)
    try:
        start = text.index("{")
        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            candidate = text[start:end]
            return json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        pass

    logger.warning("Failed to extract JSON from response: %.200s...", text)
    return None


# ════════════════════════════════════════════════════════════════════
# Plan Scoring
# ════════════════════════════════════════════════════════════════════

# Cost → numeric score (lower is better)
COST_SCORE_MAP: Dict[str, float] = {
    "低": 0.2,
    "中": 0.5,
    "高": 0.9,
}


def _score_plan(
    actions: List[str],
    total_depth: int,
    cost_labels: List[str],
    max_possible_depth: int = 5,
) -> float:
    """
    Score a plan on [0, 1] where lower is better.

    Factors:
      - depth_score: shallower plans score better
      - cost_score: average cost across all actions (低 < 中 < 高)
      - action_count_penalty: slight penalty for excessive actions

    Returns a combined weighted score.
    """
    # Depth score: 0 at depth=0, approaches 1 at max depth
    depth_score = min(total_depth / max(1, max_possible_depth), 1.0)

    # Cost score: average of cost labels, normalized
    if cost_labels:
        avg_cost_raw = sum(COST_SCORE_MAP.get(c, 0.5) for c in cost_labels) / len(cost_labels)
    else:
        avg_cost_raw = 0.5
    cost_score = avg_cost_raw  # already in [0, 1]

    # Action count penalty (logarithmic to avoid over-penalizing)
    action_count = max(1, len(actions))
    count_penalty = min(0.1 * (action_count / 5.0), 0.3)

    # Weighted combination: depth 40%, cost 50%, count penalty 10%
    combined = 0.4 * depth_score + 0.5 * cost_score + count_penalty

    return round(combined, 4)


# ════════════════════════════════════════════════════════════════════
# GoalBackwardEngine
# ════════════════════════════════════════════════════════════════════

class GoalBackwardEngine:
    """
    Goal-backward reasoning engine with recursive LLM-powered decomposition.

    Given a complex goal, this engine recursively breaks it down into
    prerequisite conditions, determines which are already satisfied vs.
    missing, and generates concrete action steps for each missing condition.
    The result is a tree of :class:`ActionNode` objects that can be
    traversed to produce ranked execution plans.

    Quickstart::

        dispatcher = DragonDispatcher()
        # ... register providers ...

        engine = GoalBackwardEngine(dispatcher)
        root = await engine.decompose(
            goal_description="提高客户复购率20%",
            current_state="现有CRM系统，月活用户5000人",
            constraints=["预算不超过10万", "3个月内"],
            max_depth=4,
        )
        plans = engine.generate_plans(root)
        for i, plan in enumerate(plans[:3]):
            print(f"Plan {i+1}:")
            for step in plan:
                print(f"  → {step}")

    Parameters:
        dispatcher: A configured :class:`DragonDispatcher` for LLM calls.
        default_industry: Industry key to use for decomposition calls.
        default_model_temp: Temperature for decomposition LLM calls.
    """

    # Default dispatch settings
    DEFAULT_INDUSTRY = "general"
    DEFAULT_TEMPERATURE = 0.3  # Lower temperature for structured reasoning
    MAX_DECOMPOSITION_DEPTH = 8  # Hard ceiling on recursion

    def __init__(
        self,
        dispatcher: DragonDispatcher,
        default_industry: str = "general",
        default_temperature: float = 0.3,
    ):
        """
        Initialize the goal-backward engine.

        Args:
            dispatcher: Configured DragonDispatcher instance for LLM calls.
            default_industry: Industry key registered in the dispatcher.
            default_temperature: Sampling temperature for decomposition calls.
        """
        self.dispatcher = dispatcher
        self.default_industry = default_industry
        self.default_temperature = default_temperature
        self._visited: Dict[str, ActionNode] = {}  # goal-hash → node for cycle detection
        logger.info(
            "GoalBackwardEngine initialized (industry=%s, temp=%.2f)",
            default_industry,
            default_temperature,
        )

    # ── Public API ────────────────────────────────────────────────────

    async def decompose(
        self,
        goal_description: str,
        current_state: Union[str, List[str]],
        constraints: Optional[List[str]] = None,
        max_depth: int = 5,
    ) -> ActionNode:
        """
        Recursively decompose a goal into an ActionNode tree.

        Args:
            goal_description: The goal to decompose (e.g., "提高客户复购率20%").
            current_state: Description of what is already available/satisfied.
                Can be a string or list of strings.
            constraints: Hard constraints for the decomposition.
            max_depth: Maximum recursion depth (capped at 8).

        Returns:
            The root :class:`ActionNode` of the decomposition tree.

        Raises:
            ValueError: If goal_description is empty or max_depth <= 0.
            RuntimeError: If the LLM consistently fails to produce parseable output.
        """
        if not goal_description or not goal_description.strip():
            raise ValueError("goal_description must be non-empty")
        if max_depth <= 0:
            raise ValueError(f"max_depth must be positive, got {max_depth}")

        max_depth = min(max_depth, self.MAX_DECOMPOSITION_DEPTH)

        # Normalize current_state
        if isinstance(current_state, list):
            current_state_str = "；".join(current_state)
        else:
            current_state_str = str(current_state)

        constraints = constraints or []

        # Reset visited cache for each top-level decomposition
        self._visited.clear()

        logger.info(
            "Starting decomposition: goal=%r, max_depth=%d, constraints=%d",
            goal_description,
            max_depth,
            len(constraints),
        )

        # Build the root GoalState
        goal_state = GoalState(
            description=goal_description,
            measurable_criteria=[],  # Can be expanded later
            constraints=constraints,
        )

        root = ActionNode(
            goal=goal_description,
            depth=0,
        )

        # Kick off recursive decomposition
        await self._decompose_recursive(
            node=root,
            goal_state=goal_state,
            current_state=current_state_str,
            max_depth=max_depth,
        )

        logger.info(
            "Decomposition complete: root=%s, total_nodes=%d",
            root.goal,
            self._count_nodes(root),
        )

        return root

    def generate_plans(self, root_node: ActionNode) -> List[List[str]]:
        """
        Generate ranked action plans from a decomposition tree.

        Performs DFS traversal from root to each leaf. Each root-to-leaf
        path produces one plan (a sequence of action strings). Plans are
        scored by depth (shallower = better), estimated cost (lower = better),
        and action count, then sorted from best to worst.

        Args:
            root_node: The root of the decomposition tree from :meth:`decompose`.

        Returns:
            List of plans, each plan being a list of action strings, sorted
            from highest to lowest quality (lowest score first).

        Example::

            plans = engine.generate_plans(root)
            # plans[0] = ["行动A", "行动B", "行动C"]  # Best plan
            # plans[1] = ["行动A", "行动X", "行动Y"]  # Second best
        """
        if root_node is None:
            return []

        raw_plans: List[Tuple[List[str], int, List[str]]] = []
        # (actions, depth, cost_labels)

        self._collect_plans_dfs(root_node, [], raw_plans)

        if not raw_plans:
            logger.warning("No plans generated from tree — root may have no actions")
            return []

        # Score and sort plans (lower score = better)
        max_depth = max(d for _, d, _ in raw_plans) if raw_plans else 5
        scored: List[Tuple[float, List[str]]] = []
        for actions, depth, cost_labels in raw_plans:
            score = _score_plan(actions, depth, cost_labels, max_depth)
            scored.append((score, actions))

        scored.sort(key=lambda x: x[0])  # ascending score = best first

        logger.info(
            "Generated %d plans from tree (best score=%.4f, worst=%.4f)",
            len(scored),
            scored[0][0] if scored else 0,
            scored[-1][0] if scored else 0,
        )

        return [actions for _, actions in scored]

    # ── Recursive Decomposition Core ───────────────────────────────────

    async def _decompose_recursive(
        self,
        node: ActionNode,
        goal_state: GoalState,
        current_state: str,
        max_depth: int,
    ) -> None:
        """
        Recursive decomposition step.

        1. Stop if max_depth reached
        2. Check visited cache for cycles
        3. Call LLM for decomposition analysis
        4. Parse JSON response → populate node
        5. For each missing prerequisite, create child node and recurse
        """
        depth = node.depth

        # ── Termination condition 1: max depth ──
        if depth >= max_depth:
            logger.debug(
                "Max depth reached for goal=%r at depth=%d", node.goal, depth
            )
            node.reasoning = f"达到最大深度限制({max_depth})，停止分解"
            node.can_execute_directly = True
            return

        # ── Cycle detection via visited cache ──
        cache_key = hashlib.md5(
            f"{node.goal}:{depth}".encode()
        ).hexdigest()
        if cache_key in self._visited:
            cached = self._visited[cache_key]
            logger.debug("Cycle detected for goal=%r — reusing cached node", node.goal)
            # Copy cached data but preserve parent relationship
            node.prerequisites = list(cached.prerequisites)
            node.actions = list(cached.actions)
            node.satisfied = list(cached.satisfied)
            node.missing = list(cached.missing)
            node.estimated_cost = cached.estimated_cost
            node.estimated_time = cached.estimated_time
            node.can_execute_directly = cached.can_execute_directly
            node.reasoning = cached.reasoning
            for child in cached.children:
                # Deep-copy children (they'll get new parent references)
                child_copy = ActionNode(
                    goal=child.goal,
                    prerequisites=list(child.prerequisites),
                    actions=list(child.actions),
                    satisfied=list(child.satisfied),
                    missing=list(child.missing),
                    estimated_cost=child.estimated_cost,
                    estimated_time=child.estimated_time,
                    depth=depth + 1,
                    can_execute_directly=child.can_execute_directly,
                    reasoning=child.reasoning,
                )
                node.add_child(child_copy)
            return

        self._visited[cache_key] = node

        # ── Call LLM for decomposition ──
        try:
            response_data = await self._call_decomposition_llm(
                goal=node.goal,
                criteria=goal_state.measurable_criteria,
                constraints=goal_state.constraints,
                current_state=current_state,
                depth=depth,
                max_depth=max_depth,
            )
        except Exception as exc:
            logger.error(
                "LLM decomposition failed for goal=%r at depth=%d: %s",
                node.goal,
                depth,
                exc,
            )
            # Fallback: mark as directly executable
            node.reasoning = f"LLM调用失败({exc})，标记为可直接执行"
            node.can_execute_directly = True
            return

        if response_data is None:
            logger.warning(
                "Empty/unparseable LLM response for goal=%r — stopping recursion",
                node.goal,
            )
            node.reasoning = "LLM返回内容无法解析，标记为可直接执行"
            node.can_execute_directly = True
            return

        # ── Populate node from LLM response ──
        raw_prereqs = response_data.get("prerequisites", [])
        raw_satisfied = response_data.get("satisfied", [])
        raw_missing = response_data.get("missing", [])
        raw_actions = response_data.get("actions", [])
        can_execute = response_data.get("can_execute_directly", False)
        reasoning = response_data.get("reasoning", "")

        # Ensure they're lists
        if not isinstance(raw_prereqs, list):
            raw_prereqs = [str(raw_prereqs)]
        if not isinstance(raw_satisfied, list):
            raw_satisfied = [str(raw_satisfied)]
        if not isinstance(raw_missing, list):
            raw_missing = [str(raw_missing)]
        if not isinstance(raw_actions, list):
            raw_actions = []

        node.prerequisites = [str(p) for p in raw_prereqs]
        node.satisfied = [str(s) for s in raw_satisfied]
        node.missing = [str(m) for m in raw_missing]
        node.can_execute_directly = bool(can_execute)
        node.reasoning = str(reasoning) if reasoning else ""

        # ── Process action mappings ──
        action_strings: List[str] = []
        action_costs: List[str] = []

        for action_entry in raw_actions:
            if isinstance(action_entry, dict):
                action_desc = action_entry.get("action", str(action_entry))
                action_cost = action_entry.get("cost", "中")
                action_time = action_entry.get("time", "未知")
                action_for = action_entry.get("for", "")

                if action_for:
                    action_str = f"[{action_for}] {action_desc} (成本:{action_cost}, 时间:{action_time})"
                else:
                    action_str = f"{action_desc} (成本:{action_cost}, 时间:{action_time})"

                action_strings.append(action_str)
                action_costs.append(str(action_cost))
            elif isinstance(action_entry, str):
                action_strings.append(action_entry)
                action_costs.append("中")

        node.actions = action_strings

        # ── Compute node-level cost aggregate ──
        if action_costs:
            # Majority cost or highest
            cost_counts: Dict[str, int] = {}
            for c in action_costs:
                cost_counts[c] = cost_counts.get(c, 0) + 1
            node.estimated_cost = max(cost_counts, key=cost_counts.get)  # type: ignore[arg-type]
            # Estimate time from first action (simplification)
            if raw_actions and isinstance(raw_actions[0], dict):
                node.estimated_time = raw_actions[0].get("time", "未知")
        else:
            node.estimated_cost = "低"

        # ── Termination condition 2: directly executable ──
        if node.can_execute_directly:
            logger.debug(
                "Node marked directly executable: goal=%r, depth=%d",
                node.goal,
                depth,
            )
            return

        # ── Termination condition 3: no missing prerequisites ──
        if not node.missing:
            logger.debug(
                "All prerequisites satisfied for goal=%r — no further decomposition",
                node.goal,
            )
            node.can_execute_directly = True
            return

        # ── Recursive decomposition of each missing prerequisite ──
        for i, missing_condition in enumerate(node.missing):
            child_node = ActionNode(
                goal=missing_condition,
                depth=depth + 1,
            )

            # Find the matching action(s) for this missing condition
            child_actions: List[str] = []
            child_costs: List[str] = []
            for action_entry in raw_actions:
                if isinstance(action_entry, dict):
                    if action_entry.get("for") == missing_condition:
                        action_desc = action_entry.get("action", "")
                        action_cost = action_entry.get("cost", "中")
                        action_time = action_entry.get("time", "未知")
                        child_actions.append(
                            f"{action_desc} (成本:{action_cost}, 时间:{action_time})"
                        )
                        child_costs.append(str(action_cost))

            if child_actions:
                child_node.actions = child_actions
                if child_costs:
                    cost_counts = {}
                    for c in child_costs:
                        cost_counts[c] = cost_counts.get(c, 0) + 1
                    child_node.estimated_cost = max(cost_counts, key=cost_counts.get)  # type: ignore[arg-type]
                if raw_actions and isinstance(raw_actions[0], dict):
                    child_node.estimated_time = raw_actions[0].get("time", "未知")

            node.add_child(child_node)

            # Build updated current_state for the child (include satisfied + parent's satisfied)
            child_current_state = current_state
            additional_satisfied = node.satisfied + [
                p for p in node.prerequisites if p not in node.missing
            ]
            if additional_satisfied:
                child_current_state = (
                    current_state + "；" + "；".join(additional_satisfied)
                )

            # Create a sub-goal state for the child
            child_goal_state = GoalState(
                description=missing_condition,
                measurable_criteria=goal_state.measurable_criteria,
                constraints=goal_state.constraints,
            )

            # Recurse
            await self._decompose_recursive(
                node=child_node,
                goal_state=child_goal_state,
                current_state=child_current_state,
                max_depth=max_depth,
            )

    # ── LLM Interaction ────────────────────────────────────────────────

    async def _call_decomposition_llm(
        self,
        goal: str,
        criteria: List[str],
        constraints: List[str],
        current_state: str,
        depth: int,
        max_depth: int,
    ) -> Optional[dict]:
        """
        Call the LLM for a single decomposition step.

        Returns parsed JSON dict on success, None on failure.
        """
        prompt = DECOMPOSITION_PROMPT.format(
            goal=goal,
            criteria=", ".join(criteria) if criteria else "未指定",
            constraints=", ".join(constraints) if constraints else "无",
            current_state=current_state or "无",
            depth=depth,
            max_depth=max_depth,
        )

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": DECOMPOSITION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        logger.debug(
            "Calling LLM for decomposition: goal=%r, depth=%d/%d",
            goal,
            depth,
            max_depth,
        )

        try:
            result: DispatchResult = await self.dispatcher.dispatch(
                industry=self.default_industry,
                messages=messages,
                knowledge=None,
                stream=False,
            )

            content = result.content
            logger.debug(
                "LLM response for goal=%r: %d chars, latency=%.0fms, tokens=%d",
                goal,
                len(content),
                result.latency_ms,
                result.usage.total_tokens,
            )

            parsed = _extract_json(content)
            if parsed is None:
                logger.warning(
                    "Failed to parse JSON from LLM response for goal=%r. "
                    "Raw (first 200 chars): %.200s",
                    goal,
                    content,
                )
                return None

            # Validate required fields
            for field in ("prerequisites", "satisfied", "missing", "actions"):
                if field not in parsed:
                    logger.warning(
                        "Missing required field '%s' in LLM response for goal=%r",
                        field,
                        goal,
                    )
                    return None

            return parsed

        except Exception as exc:
            logger.error(
                "LLM dispatch failed for goal=%r: %s",
                goal,
                exc,
            )
            raise

    # ── Plan Collection (DFS) ──────────────────────────────────────────

    def _collect_plans_dfs(
        self,
        node: ActionNode,
        path_actions: List[str],
        results: List[Tuple[List[str], int, List[str]]],
    ) -> None:
        """
        DFS traversal collecting root-to-leaf plans.

        Each leaf yields one complete plan (accumulated actions along the path).

        Args:
            node: Current node in traversal.
            path_actions: Actions accumulated from root to parent.
            results: Output list of (actions, depth, cost_labels) tuples.
        """
        # Accumulate this node's actions
        current_actions = list(path_actions) + list(node.actions)

        # Extract cost labels for this node's actions
        node_cost_labels: List[str] = []
        for action_str in node.actions:
            # Parse cost from action string like "... (成本:低, 时间:1小时)"
            cost_match = re.search(r"成本[:：]\s*(低|中|高)", action_str)
            if cost_match:
                node_cost_labels.append(cost_match.group(1))
            else:
                node_cost_labels.append(node.estimated_cost)

        # If leaf, emit a plan
        if node.is_leaf:
            if current_actions:
                # Determine cost labels for all actions in this plan
                all_cost_labels: List[str] = []
                for action_str in current_actions:
                    cm = re.search(r"成本[:：]\s*(低|中|高)", action_str)
                    if cm:
                        all_cost_labels.append(cm.group(1))
                    else:
                        all_cost_labels.append("中")

                results.append((current_actions, node.depth, all_cost_labels))
            return

        # Recurse into children
        for child in node.children:
            self._collect_plans_dfs(child, current_actions, results)

    # ── Utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _count_nodes(root: ActionNode) -> int:
        """Count total nodes in the decomposition tree."""
        count = 1
        for child in root.children:
            count += GoalBackwardEngine._count_nodes(child)
        return count

    def print_tree(self, root: ActionNode) -> str:
        """
        Return a pretty-printed string representation of the decomposition tree.

        Args:
            root: Root ActionNode from :meth:`decompose`.

        Returns:
            Formatted string tree.
        """
        return root.print_tree()

    def get_all_nodes(self, root: ActionNode) -> List[ActionNode]:
        """
        Flatten all nodes in the tree into a list (BFS order).

        Args:
            root: Root ActionNode.

        Returns:
            List of all ActionNodes in the tree.
        """
        result: List[ActionNode] = []
        queue: List[ActionNode] = [root]
        while queue:
            node = queue.pop(0)
            result.append(node)
            queue.extend(node.children)
        return result

    def get_leaves(self, root: ActionNode) -> List[ActionNode]:
        """
        Get all leaf nodes in the tree.

        Args:
            root: Root ActionNode.

        Returns:
            List of leaf ActionNodes.
        """
        leaves: List[ActionNode] = []
        stack: List[ActionNode] = [root]
        while stack:
            node = stack.pop()
            if node.is_leaf:
                leaves.append(node)
            else:
                stack.extend(reversed(node.children))
        return leaves

    def to_dict(self, root: ActionNode) -> dict:
        """
        Export the full decomposition tree as a dictionary.

        Args:
            root: Root ActionNode.

        Returns:
            Nested dictionary representation.
        """
        return root.to_dict()

    def to_json(self, root: ActionNode, indent: int = 2) -> str:
        """
        Export the full decomposition tree as a JSON string.

        Args:
            root: Root ActionNode.
            indent: JSON indentation level.

        Returns:
            JSON string.
        """
        return json.dumps(root.to_dict(), ensure_ascii=False, indent=indent)


# ════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════

__all__ = [
    # Core engine
    "GoalBackwardEngine",
    # Data classes
    "GoalState",
    "ActionNode",
    # Constants
    "DECOMPOSITION_PROMPT",
    "DECOMPOSITION_SYSTEM_PROMPT",
]
