#!/usr/bin/env python3
"""drama_review_single.py — 单模型审阅（剧情/逻辑/人设/台词等不同角度）"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位专业审稿人。你的职责是找出内容中的问题。
审阅要具体、可执行。每个问题标注：
- 🔴 严重：逻辑矛盾、角色崩坏、致命硬伤
- 🟡 中等：节奏问题、对话生硬、信息缺失
- 🟢 轻微：措辞优化、细节补充
只提出真正的问题，不要为了凑数写无关建议。"""

REVIEW_PROMPT = """请从以下角度审阅内容：

## 审阅角度
{role}

## 内容
{content}

## 上轮审阅意见（如适用，检查是否已修正）
{previous}

## 输出格式

### 严重问题（🔴）
- [ ] 问题描述 → 建议修改

### 中等问题（🟡）
- [ ] 问题描述 → 建议修改

### 轻微问题（🟢）
- [ ] 问题描述 → 建议修改

### 总结
- 整体评分（1-10）
- 是否通过（PASS / NEED_FIX）
- 一句话核心建议

如果没有问题，直接输出 "PASS - 无问题"。
"""

def main():
    p = arg_parser("单模型审阅")
    p.add_argument("--content", required=True, help="待审阅文件路径")
    p.add_argument("--role", required=True, help="审阅角度描述")
    p.add_argument("--previous", default="(首次审阅)", help="上轮审阅文件路径")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    content = read(args.content)
    prev_text = "(首次审阅)"
    try:
        prev_text = read(args.previous)
    except Exception:
        pass

    print(f"🔍 审阅中：{args.role}")
    review = call_llm(
        REVIEW_PROMPT.format(role=args.role, content=content, previous=prev_text),
        SYSTEM, max_tokens=2048, temperature=0.4
    )
    write(args.output, review)
    # Output PASS or NEED_FIX for the aggregator
    if "PASS" in review and "NEED_FIX" not in review:
        print("  ✅ PASS")
    else:
        print("  ⚠️ NEED_FIX")
    print(f"  → {args.output}")

if __name__ == "__main__":
    main()
