#!/usr/bin/env python3
"""drama_review_aggregate.py — 汇总三模型审阅意见并修正内容"""

import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位主编。你的任务是汇总多位审稿人的意见，对内容进行精准修正。
原则：
1. 只修改确实有问题的地方，不过度修改
2. 严重问题（🔴）必须修改
3. 中等问题（🟡）有选择地修改，评审人数≥2人提到则必须改
4. 轻微问题（🟢）酌情修改
5. 保持原文的整体结构和风格
6. 修正后输出完整的新版本"""

FIX_PROMPT = """汇总以下审阅意见，修正内容。

## 原始内容
{content}

## 审阅意见
{reviews}

## 要求
- 根据审阅意见逐条修改
- 输出修正后的完整内容
- 修改处用注释标注：[修订：原问题描述]

如果是最终轮（final=true），确保所有🔴问题都已修正，不再输出审核意见标记。
"""

def main():
    p = arg_parser("汇总审阅修正")
    p.add_argument("--reviews", required=True, help="审阅文件 glob 模式")
    p.add_argument("--content", required=True, help="待修正文件路径")
    p.add_argument("--diff", required=True, help="修正对照输出路径")
    p.add_argument("--final", action="store_true")
    args = p.parse_args()

    # 收集所有审阅意见
    review_files = sorted(glob.glob(args.reviews))
    if not review_files:
        print("⚠️ 未找到审阅文件，跳过修正")
        return

    all_reviews = []
    for rf in review_files:
        all_reviews.append(f"## 审稿人 {len(all_reviews)+1}\n{read(rf)}")

    reviews_text = "\n\n".join(all_reviews)

    # 统计严重问题数
    critical_count = reviews_text.count("🔴")
    medium_count = reviews_text.count("🟡")
    print(f"📋 汇总 {len(review_files)} 份审阅：🔴{critical_count} 🟡{medium_count}")

    if critical_count == 0 and medium_count == 0 and args.final:
        print("✅ 全部通过，无需修正")
        write(args.diff, "# 终审通过 - 无问题")
        return

    # 修正
    content = read(args.content)
    print("🔧 修正中...")
    fixed = call_llm(
        FIX_PROMPT.format(content=content, reviews=reviews_text),
        SYSTEM, max_tokens=8192, temperature=0.5
    )

    # 保存修正版
    write(args.content, fixed)
    write(args.diff, f"# 修正记录\n\n原问题数：🔴{critical_count} 🟡{medium_count}\n修正后输出已覆盖原文件。")
    print(f"  ✅ 修正完成 → {args.content}")

if __name__ == "__main__":
    main()
