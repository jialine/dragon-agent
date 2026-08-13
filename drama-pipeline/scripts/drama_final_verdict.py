#!/usr/bin/env python3
"""drama_final_verdict.py — 终审结论"""

import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("终审结论")
    p.add_argument("--reviews", required=True)
    args = p.parse_args()

    review_files = sorted(glob.glob(args.reviews))
    if not review_files:
        print("⚠️ 无审阅文件")
        return

    all_reviews = []
    for rf in review_files:
        all_reviews.append(read(rf))

    combined = "\n\n---\n\n".join(all_reviews)
    output = Path(review_files[0]).parent / "FINAL_VERDICT.md"

    fail_count = combined.count("NEED_FIX") + combined.count("❌")
    verdict = "PASS" if fail_count == 0 else "FAIL"

    summary = f"""# 终审结论

**结果：{verdict}**

## 检查项
{chr(10).join([f'- {Path(rf).stem}' for rf in review_files])}

## 通过数/问题数
- NEED_FIX: {fail_count}

## 详细报告
{combined[:3000]}
"""
    write(str(output), summary)
    print(f"  {'✅' if verdict == 'PASS' else '❌'} {verdict}")
    print(f"  → {output}")

if __name__ == "__main__":
    main()
