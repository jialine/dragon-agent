#!/usr/bin/env python3
"""drama_export_pdf.py — 剧本导出为 PDF"""

import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    import argparse
    p = argparse.ArgumentParser(description="导出剧本为PDF")
    p.add_argument("--scripts", required=True, help="剧本目录")
    p.add_argument("--outline", default="", help="大纲文件")
    p.add_argument("--output", required=True, help="输出PDF路径")
    args = p.parse_args()

    # 收集所有剧本 + 大纲
    md_content = []
    if args.outline and os.path.exists(args.outline):
        with open(args.outline) as f:
            md_content.append(f.read())

    for f in sorted(glob.glob(f"{args.scripts}/*.md")):
        with open(f) as fh:
            md_content.append(fh.read())

    full_md = "\n\n---\n\n".join(md_content)

    # 尝试多种方式生成 PDF
    # 方式1: markdown → html → pdf (weasyprint)
    try:
        import markdown
        from weasyprint import HTML
        html = markdown.markdown(full_md, extensions=["tables", "fenced_code"])
        styled = f"""<html><head><meta charset="utf-8">
        <style>body{{font-family: 'Noto Sans CJK SC', sans-serif; max-width:800px; margin:40px auto; line-height:1.8}}
        h1{{border-bottom:2px solid #333; padding-bottom:8px}} h2{{color:#555}}
        table{{border-collapse:collapse; width:100%}} td,th{{border:1px solid #ddd; padding:8px}}
        </style></head><body>{html}</body></html>"""
        HTML(string=styled).write_pdf(args.output)
        print(f"✅ PDF → {args.output}")
        return
    except ImportError:
        pass

    # 方式2: pandoc
    import subprocess
    result = subprocess.run(["pandoc", "-f", "markdown", "-o", args.output,
                              "--pdf-engine=xelatex",
                              "-V", "mainfont=Noto Sans CJK SC",
                              "-V", "CJKmainfont=Noto Sans CJK SC"],
                             input=full_md, capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        print(f"✅ PDF (pandoc) → {args.output}")
    else:
        # 降级：保存 markdown
        md_output = args.output.replace(".pdf", ".md")
        with open(md_output, "w") as f:
            f.write(full_md)
        print(f"⚠️ PDF生成失败，已保存Markdown: {md_output}")
        print(f"   错误: {result.stderr[:200]}")

if __name__ == "__main__":
    main()
