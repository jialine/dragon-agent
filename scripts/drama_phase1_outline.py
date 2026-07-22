#!/usr/bin/env python3
"""drama_phase1_outline.py — 生成故事大纲 + 世界观"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位资深短剧编剧和世界观架构师。你的任务是为短剧创作完整的大纲和世界观。
风格要求：
- 开篇必须有强力钩子（前3秒抓住观众）
- 每集要有小的情绪高潮，整体有大的情绪弧线
- 角色鲜明，对话简洁（每句≤30字）
- 世界观设定必须自洽，不能有逻辑漏洞
- 输出为结构化 Markdown"""

OUTLINE_PROMPT = """请为以下短剧创作故事大纲和世界观。

类型：{genre}
风格：{style}
核心冲突：{conflict}
语调：{tone}

请按以下结构输出：

# 《{title}》

## 一、一句话梗概
（用一句话说清楚这个故事讲什么）

## 二、世界观设定
- **时代背景**：
- **核心规则**：（这个世界有什么特殊规则/设定）
- **势力/阵营**：（如果有的话）
- **世界观边界**：（什么能做、什么不能做）

## 三、人物弧线
### 主角
- **起点**：（开始时的状态/困境）
- **转折**：（改变的关键事件）
- **终点**：（结局时的状态）

### 配角1（如有）
- **与主角关系**：
- **独立动机**：

## 四、故事大纲
### 开篇钩子（前3秒/第一场戏）
（具体画面+台词，让观众立刻被吸引）

### 第一幕：建立
（人物出场 + 冲突引入，1-2个关键场景）

### 第二幕：对抗
（冲突升级 + 转折点，2-3个关键场景）

### 第三幕：高潮
（冲突爆发 + 情感顶点，1-2个关键场景）

### 结尾
（收束 + 钩子/反转/留白）

## 五、核心看点
- 3个最吸引观众的点
- 适合的目标受众
- 对标作品（如果有类似的）

输出完整、具体、可拍摄。每个场景要有画面感。"""

WORLDBUILDING_PROMPT = """基于以下故事大纲，扩展详细的世界观设定。

{outline}

请补充：

# 世界观详细设定

## 1. 视觉风格
- **色调**：（暖/冷/中性，具体颜色）
- **美术参考**：（可以参照的电影/作品风格）
- **服装风格**：
- **场景特色**：

## 2. 社会结构
- **权力关系**：（谁有权力，谁没有）
- **日常规则**：（这个世界的人每天怎么生活）
- **禁忌/冲突源**：

## 3. 科技/魔法水平（如适用）

## 4. 时间线
- **故事发生的具体时间段**
- **关键历史事件**（影响当前故事的背景事件）

## 5. 世界观与故事的关联
- 世界观如何推动冲突
- 世界观如何塑造角色
"""

def main():
    p = arg_parser("生成故事大纲和世界观")
    p.add_argument("--genre", required=True)
    p.add_argument("--style", required=True)
    p.add_argument("--title", default="未命名短剧")
    p.add_argument("--conflict", default="")
    p.add_argument("--tone", default="")

    args = p.parse_args()
    ws = Path(args.workspace)
    outline_dir = ws / "01_outline"
    outline_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 生成大纲
    print("📝 生成故事大纲...")
    outline = call_llm(
        OUTLINE_PROMPT.format(genre=args.genre, style=args.style, conflict=args.conflict, tone=args.tone, title=args.title),
        SYSTEM
    )
    write(str(outline_dir / "outline.md"), outline)
    print(f"  ✅ 大纲 → {outline_dir}/outline.md")

    # Step 2: 扩展世界观
    print("🌍 扩展世界观...")
    world = call_llm(
        WORLDBUILDING_PROMPT.format(outline=outline),
        SYSTEM
    )
    write(str(outline_dir / "worldbuilding.md"), world)
    print(f"  ✅ 世界观 → {outline_dir}/worldbuilding.md")

    # Step 3: 更新 meta
    meta = {
        "title": args.title,
        "genre": args.genre,
        "style": args.style,
        "status": "phase1_outline_done",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write(str(ws / "00_meta.yaml"), json.dumps(meta, ensure_ascii=False, indent=2))
    print("🎉 Phase 1.1 完成")

if __name__ == "__main__":
    main()
