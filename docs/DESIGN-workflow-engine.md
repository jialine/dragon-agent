# Dragon Agent 工作流引擎 — 设计文档

> **参考：** MoneyPrinterTurbo 架构（pipeline + checkpoint + state machine）
> **核心理念：** 每个工作流先制定方案，再根据行业特征组装合适的工具和技能去执行

---

## 1. 核心概念

```
用户请求 "如何分析一只股票的估值？"
  │
  ▼
Router 分类 → industry=finance, difficulty=medium
  │
  ▼
加载 finance.yaml 工作流
  │
  ▼
╔══════════════════════════════════════════════╗
║  Step 0: plan（方案制定）                      ║
║  "这是一个中等难度的金融分析问题，               ║
║   需要：1)估值术语解析 2)实时数据查询             ║
║        3)多模型辩论 4)风险提示"                  ║
║  从行业工具池中选择：                           ║
║    tools:  [web_search]                        ║
║    skills: [jury_debate]                       ║
╚══════════════════════════════════════════════╝
  │
  ▼
按方案执行：
  ├─ Step 1: tool(web_search) → 实时PE/PB数据
  ├─ Step 2: skill(jury_debate) → 3模型辩论估值
  └─ Step 3: llm → 汇总 + 风险提示
```

**关键区别：** 不是固定步骤链，而是先规划后执行。简单问题可能只走一步，复杂问题走全流程。

---

## 2. 架构

```
dragon-agent/
├── dragon/
│   └── workflow/              ← 引擎模块（一等模块，与 router/skill 平级）
│       ├── __init__.py        # WorkflowEngine, 数据类, 顶层 API
│       ├── runner.py          # 顺序执行 + checkpoint + progress
│       ├── planner.py         # 方案制定（Step 0 专用）
│       └── steps.py           # 步骤执行器：tool / skill / llm / transform
│
├── workflows/                 ← 行业工作流定义（纯 YAML）
│   ├── finance.yaml
│   ├── medical.yaml
│   ├── legal.yaml
│   ├── education.yaml
│   └── general.yaml
```

**Workflow 和 Skill 的关系：**

```
Skill（技能）                     Workflow（工作流）
─────────────────────────       ─────────────────────────
定义"怎么做一件事"                 定义"选哪些技能去完成一个目标"
可复用，跨行业                     行业专属
jury_debate, fact_check...       finance.yaml, medical.yaml...
被 workflow 调用                  编排 tool + skill + llm
```

---

## 3. 工作流定义格式 (YAML)

### 3.1 金融行业工作流

```yaml
# workflows/finance.yaml
name: 金融行业工作流
industry: finance
version: "1.0"
timeout_secs: 120

# ── 行业工具池：该行业可用的所有 tool 和 skill ──
toolbox:
  tools:
    - web_search       # 实时行情/数据
    - vision           # 图表分析
    - maps             # 企业位置
  skills:
    - jury_debate      # 多模型辩论
    - fact_check       # 事实核查
    - consensus        # 共识输出

# ── plan 步骤：每个工作流的第一步，必须执行 ──
plan:
  prompt: |
    你是一个金融咨询方案制定者。分析用户问题，制定执行方案。

    可用工具：{available_tools}
    可用技能：{available_skills}

    用户问题：{query}
    难度评估：{difficulty}（{difficulty_score}/10）

    输出JSON格式的执行方案：
    {{
      "approach": "用一段中文简述整体思路",
      "need_data": true/false,        # 是否需要实时数据
      "need_debate": true/false,      # 是否需要多模型辩论
      "need_fact_check": true/false,  # 是否需要事实核查
      "selected_tools": ["tool_name"],    # 选中的工具
      "selected_skills": ["skill_name"],  # 选中的技能
      "sub_questions": ["需要回答的子问题1", "子问题2"],
      "risk_level": "low/medium/high"  # 回答风险等级
    }}

# ── 执行步骤模板 ──
steps:
  # Step 1: 如果 plan 决定需要数据，调用选中的 tools
  - id: gather_data
    name: 获取实时数据
    condition: "plan.need_data == true"
    type: tool
    tools_from: plan.selected_tools   # 动态选择
    input: "{query} {plan.sub_questions}"
    on_failure: skip

  # Step 2: 如果 plan 决定需要辩论，调用选中的 skills
  - id: expert_analysis
    name: 专业分析
    condition: "plan.need_debate == true"
    type: skill
    skills_from: plan.selected_skills # 动态选择
    context:
      query: "{query}"
      data: "{gather_data}"
    on_failure: abort

  # Step 3: 事实核查（如果需要）
  - id: verify
    name: 事实核查
    condition: "plan.need_fact_check == true"
    type: skill
    skill: fact_check
    input_from: expert_analysis
    on_failure: skip

  # Step 4: 最终汇总（必执行）
  - id: summarize
    name: 生成最终回答
    type: llm
    prompt: |
      根据以下信息，生成金融咨询回答：

      方案思路：{plan.approach}
      风险等级：{plan.risk_level}
      数据：{gather_data}
      分析：{expert_analysis}
      核查：{verify}

      ⚠️ 必须附加风险提示：以上内容仅供参考，不构成投资建议。
    on_failure: abort
```

### 3.2 医疗行业工作流

```yaml
# workflows/medical.yaml
name: 医疗行业工作流
industry: medical
version: "1.0"
timeout_secs: 120

toolbox:
  tools:
    - web_search       # 医学文献检索
    - vision           # 影像分析
  skills:
    - fact_check       # 医学事实核查
    - consensus        # 多源共识

plan:
  prompt: |
    你是一个医疗咨询方案制定者。分析用户问题，制定执行方案。

    可用工具：{available_tools}
    可用技能：{available_skills}

    ⚠️ 医疗咨询必须遵循"先查询，不诊断"原则。

    用户问题：{query}
    难度评估：{difficulty}（{difficulty_score}/10）

    输出JSON：
    {{
      "approach": "整体思路",
      "is_emergency": true/false,       # 是否为急诊症状
      "need_literature": true/false,    # 是否需要查文献
      "need_fact_check": true/false,
      "selected_tools": [],
      "selected_skills": [],
      "sub_questions": [],
      "risk_level": "low/medium/high/critical"
    }}

steps:
  - id: triage
    name: 急诊分诊
    condition: "plan.is_emergency == true"
    type: llm
    prompt: |
      用户描述：{query}
      立即判断是否需要急诊，如是，输出就医指引。
    on_failure: abort

  - id: search_literature
    name: 医学文献检索
    condition: "plan.need_literature == true"
    type: tool
    tools_from: plan.selected_tools
    input: "{query} {plan.sub_questions}"
    on_failure: skip

  - id: fact_verify
    name: 医学事实核查
    condition: "plan.need_fact_check == true"
    type: skill
    skill: fact_check
    input_from: search_literature
    on_failure: skip

  - id: respond
    name: 生成回答（附免责声明）
    type: llm
    prompt: |
      方案：{plan.approach}
      文献：{search_literature}
      核查：{fact_verify}

      ⚠️ 必须包含：本回复仅供参考，不能替代专业医疗诊断。
      如有不适请及时就医。
    on_failure: abort
```

### 3.3 通用工作流

```yaml
# workflows/general.yaml
name: 通用工作流
industry: general
version: "1.0"
timeout_secs: 60

toolbox:
  tools:
    - web_search
  skills: []

plan:
  prompt: |
    分析用户问题。可用工具：{available_tools}
    用户问题：{query}
    输出JSON：{{"need_search": true/false, "approach": "思路"}}

steps:
  - id: search
    name: 搜索
    condition: "plan.need_search == true"
    type: tool
    tool: web_search
    input: "{query}"
    on_failure: skip

  - id: respond
    name: 直接回答
    type: llm
    prompt: "回答以下问题：{query}\n参考资料：{search}"
    on_failure: abort
```

---

## 4. Plan 步骤的职责

**Plan 是每个工作流的"大脑"，负责回答三个问题：**

| 问题 | 决策 | 影响后续步骤 |
|------|------|-------------|
| 这个任务有多难？ | 简单/中等/复杂 | 简单可能跳过 debate |
| 需要什么数据？ | 实时行情/医学文献/不需要 | 决定是否调 search |
| 用什么工具和技能？ | 从 toolbox 中选择 | 步骤动态组装 |

**Plan 输出 → Runner 解释 → 跳过不满足 condition 的步骤 → 执行选中步骤**

---

## 5. 步骤类型

| 类型 | 说明 | 谁来执行 |
|------|------|---------|
| `plan` | **每个工作流的第一步**，分析任务制定方案 | LLM |
| `tool` | 调用 Dragon 内置工具（原子能力） | Tool Registry |
| `skill` | 调用已有技能（可复用流程） | Skill Engine |
| `llm` | 原始 LLM 推理 | 远端/本地模型 |
| `condition` | 根据 plan 结果决定是否执行此步 | 表达式求值 |

---

## 6. 执行引擎核心 API

```python
class WorkflowEngine:
    """工作流执行引擎"""

    async def run(
        self,
        industry: str,
        query: str,
        route_result: RouteResult,
        callbacks: WorkflowCallbacks = None,
    ) -> WorkflowResult:
        """
        1. 加载 workflows/{industry}.yaml
        2. 执行 plan 步骤 → 获得执行方案
        3. 按方案执行后续步骤（condition 过滤 + 动态组装）
        4. 返回最终结果
        """
```

---

## 7. 与现有代码的集成

```python
# dragon/main.py
async def process_message(user_msg: str):
    # 1. Router 分类（已有）
    classification = await router.classify(user_msg)

    # 2. Workflow 引擎（新增，替代直接调 dispatcher）
    result = await workflow_engine.run(
        industry=classification.industry,
        query=user_msg,
        route_result=classification,
        callbacks=GatewayCallbacks(chat_id),
    )

    # 3. 返回
    return result.final_response
```

**不替换 Dispatcher——** Dispatcher 仍然是底层"调模型"的通道，Workflow 是上层编排层。

---

## 8. 与 MoneyPrinterTurbo 的对应

| MoneyPrinterTurbo | Dragon Workflow |
|-------------------|-----------------|
| `task.py:start()` 固定步骤链 | `runner.py:run()` 动态步骤（plan 驱动） |
| `stop_at` 调试用 | `stop_at` 同 |
| `services/llm.py` | `steps: llm` |
| `services/material.py` | `steps: tool` |
| 无 plan 概念 | **plan 是核心创新** |

---

## 9. 实现优先级

| 优先级 | 模块 | 工作量 |
|:--:|------|:--:|
| **P0** | `workflow/__init__.py` — 数据类 + 顶层 API | 小 |
| **P0** | `workflow/planner.py` — plan 步骤执行（LLM 生成方案） | 中 |
| **P0** | `workflow/runner.py` — 顺序执行 + condition + checkpoint | 中 |
| **P0** | `workflow/steps.py` — tool / skill / llm 执行器 | 中 |
| **P0** | `workflows/general.yaml` — 通用工作流 | 小 |
| **P1** | `workflows/finance.yaml` — 金融工作流 | 小 |
| **P1** | `workflows/medical.yaml` — 医疗工作流 | 小 |
| **P1** | `workflows/legal.yaml` — 法律工作流 | 小 |
| **P1** | 集成到 `main.py` | 中 |
| **P2** | 飞书进度回调 | 小 |
