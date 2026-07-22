#!/usr/bin/env python3
"""drama_review_summary.py — 审核汇总 → PASS/FAIL"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("审核汇总")
    p.add_argument("--report", required=True)
    args = p.parse_args()

    report = read(args.report)
    output = Path(args.report).parent / "PASS_or_FAIL.yaml"

    fail_count = report.count("❌") + report.count("NEED_FIX")
    pass_count = report.count("✅") + report.count("PASS") - (report.count("NEED_FIX"))

    verdict = "PASS" if fail_count == 0 else "FAIL"
    summary = {
        "verdict": verdict,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "report": str(args.report)
    }
    write(str(output), yaml.dump(summary, allow_unicode=True))
    print(f"  📊 {verdict}: ✅{pass_count} ❌{fail_count}")
    print(f"  → {output}")

if __name__ == "__main__":
    main()
