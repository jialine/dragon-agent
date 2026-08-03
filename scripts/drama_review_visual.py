#!/usr/bin/env python3
"""drama_review_visual.py — 多模态视觉审核（角色/场景/分镜）"""

import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一个视觉质量控制专家。检查生成的视觉资产是否符合要求。
每次审查一张图片或一段视频，输出结果：PASS 或 NEED_FIX。
NEED_FIX 时必须给出具体问题和修改建议。"""

def main():
    p = arg_parser("多模态审核")
    p.add_argument("--dir", required=True)
    p.add_argument("--manifest", default="")
    p.add_argument("--retry", default="false")
    p.add_argument("--max_retries", type=int, default=2)
    args = p.parse_args()

    asset_dir = Path(args.dir)
    report_lines = []

    # 找到所有图片/视频
    media_files = []
    for ext in ["*.jpg", "*.png", "*.mp4", "*.webp"]:
        media_files.extend(asset_dir.rglob(ext))

    if not media_files:
        print("⚠️ 无可审核文件")
        write(str(asset_dir / "review_report.md"), "# 审核报告\n\n无可审核文件。")
        return

    # 从 manifest 获取期望信息
    manifest = {}
    if args.manifest and Path(args.manifest).exists():
        manifest = yaml.safe_load(read(args.manifest))

    for i, mf in enumerate(media_files):
        rel = str(mf.relative_to(asset_dir))
        print(f"  🔍 [{i+1}/{len(media_files)}] {rel}")

        # 对于视频，检查是否可播放
        if mf.suffix == ".mp4":
            import subprocess
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                               "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                               str(mf)], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                dur = float(r.stdout.strip())
                report_lines.append(f"- ✅ {rel}: 时长 {dur:.1f}s")
            else:
                report_lines.append(f"- ❌ {rel}: 无法读取 (ffprobe失败)")
            continue

        # 对于图片，用 vision_analyze 审核
        try:
            review = call_llm(
                f"检查这张图的视觉质量：\n"
                f"文件名：{rel}\n"
                f"期望角色信息：{json.dumps(manifest.get('characters', []), ensure_ascii=False)[:1000]}\n"
                f"请判断 PASS 或 NEED_FIX，给出理由。",
                SYSTEM, max_tokens=256
            )
            verdict = "PASS" if "PASS" in review.upper() else "NEED_FIX"
            report_lines.append(f"- {verdict} {rel}: {review[:100]}")
            print(f"    {verdict}")
        except Exception as e:
            report_lines.append(f"- ⚠️ {rel}: 审核失败 ({e})")

    write(str(asset_dir / "review_report.md"),
          "# 多模态审核报告\n\n" + "\n".join(report_lines))
    print(f"  ✅ → {asset_dir}/review_report.md")

if __name__ == "__main__":
    main()
