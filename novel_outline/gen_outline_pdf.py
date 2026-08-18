#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三国求生指南 — 大纲 PDF 生成 v3（.32 上跑）
乱码根因：Noto CJK 是 CFF(PostScript) 字体，fpdf2 子集化 CFF 会错乱 glyph 映射。
改用 AR PL UMing CN（TrueType glyf），fpdf2 完美支持。加粗用字号区分。
"""
import json, os
from fontTools.ttLib import TTFont
from fpdf import FPDF

BASE = "/home/jialine/dragon-agent/novel_outline"
FONT_TTF = "/tmp/uming_cn.ttf"

# 提取 UMing CN (face 0, TrueType)
if not os.path.exists(FONT_TTF):
    TTFont("/usr/share/fonts/truetype/arphic/uming.ttc", fontNumber=0).save(FONT_TTF)

fw = json.load(open(os.path.join(BASE, "framework.json"), encoding="utf-8"))
out = json.load(open(os.path.join(BASE, "full_outline.json"), encoding="utf-8"))
chapters = out["chapters"]
protag = out.get("protagonist", {})

pdf = FPDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_font("CJK", "", FONT_TTF)

def H(txt, size=16):
    pdf.set_font("CJK", "", size)
    pdf.cell(0, size * 0.8, txt, new_x="LMARGIN", new_y="NEXT")

# 封面
pdf.add_page()
pdf.ln(40)
pdf.set_font("CJK", "", 28)
pdf.multi_cell(0, 16, out["title"], align="C")
pdf.ln(6)
pdf.set_font("CJK", "", 14)
pdf.multi_cell(0, 9, "类型：%s" % out.get("genre", ""), align="C")
pdf.ln(2)
pdf.set_font("CJK", "", 12)
pdf.multi_cell(0, 8, out.get("logline", ""), align="C")
pdf.ln(14)
pdf.multi_cell(0, 8, "共 %d 章 · 上中下三部曲 · 42卷" % out.get("total_chapters", len(chapters)), align="C")

# 主角与世界观
pdf.add_page()
H("主角设定")
for k, label in [("name", "姓名"), ("modern_name", "现代名"), ("age", "年龄"),
                 ("identity", "身份"), ("personality", "性格"),
                 ("golden_finger", "金手指"), ("comedy_engine", "搞笑引擎")]:
    v = protag.get(k)
    if v:
        pdf.set_font("CJK", "", 12)
        pdf.cell(0, 8, "%s：%s" % (label, v), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("CJK", "", 11)
        pdf.ln(1)

pdf.ln(4)
H("世界观")
pdf.set_font("CJK", "", 11)
pdf.multi_cell(0, 7, out.get("worldview", ""))
pdf.ln(4)
H("总纲")
pdf.multi_cell(0, 7, out.get("synopsis", ""))
pdf.ln(4)
H("结局")
pdf.multi_cell(0, 7, out.get("ending", ""))

# 卷目
pdf.add_page()
H("卷目")
pdf.set_font("CJK", "", 10)
for v in fw.get("volumes", []):
    pdf.cell(0, 7, "第%02d卷 %s（%s）— %s" % (v["vol"], v.get("title", ""), v.get("chapters", ""), v.get("era", "")),
             new_x="LMARGIN", new_y="NEXT")

# 逐卷逐章
by_vol = {}
for c in chapters:
    n = c.get("chapter", 0)
    by_vol.setdefault((n - 1) // 50 + 1, []).append(c)

for v in fw.get("volumes", []):
    vol = v["vol"]
    pdf.add_page()
    pdf.set_font("CJK", "", 15)
    pdf.cell(0, 10, "第%d卷 · %s（%s）" % (vol, v.get("title", ""), v.get("era", "")),
             new_x="LMARGIN", new_y="NEXT")
    if v.get("theme"):
        pdf.set_font("CJK", "", 10)
        pdf.multi_cell(0, 6, "主线：%s" % v.get("theme", ""))
        pdf.ln(2)
    for c in by_vol.get(vol, []):
        pdf.set_font("CJK", "", 11)
        pdf.cell(0, 7, "第%d章 %s" % (c.get("chapter"), c.get("title", "")),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("CJK", "", 9)
        pdf.multi_cell(0, 6, "　　%s" % c.get("summary", ""))

out_pdf = os.path.join(BASE, "三国求生指南_大纲.pdf")
pdf.output(out_pdf)
print("OK ->", out_pdf, "| pages:", pdf.pages_count, "| chapters:", len(chapters))
