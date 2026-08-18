#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四拍细纲样板 PDF v3（.32 上跑）——改进排版：大字号、标签着色、章间分隔线"""
import json, os
from fpdf import FPDF

BASE = "/home/jialine/dragon-agent/novel_outline"
FONT_TTF = "/tmp/uming_cn.ttf"
data = json.load(open(os.path.join(BASE, "detail_outline_sample.json"), encoding="utf-8"))

W = 180
pdf = FPDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_font("CJK", "", FONT_TTF)

# 颜色
C_TITLE = (25, 50, 110)      # 章标题 深蓝
C_VOL = (0, 0, 0)            # 卷标题 黑
C_LABEL = (150, 150, 150)    # 标签 灰
C_BODY = (20, 20, 20)        # 正文 深灰
C_LINE = (210, 210, 210)     # 分隔线 浅灰
BEAT_COLOR = {"起因": (0, 90, 170), "冲突": (200, 100, 0),
              "高潮": (180, 30, 30), "钩子": (120, 40, 150)}

# 封面
pdf.add_page()
pdf.ln(45)
pdf.set_text_color(*C_TITLE)
pdf.set_font("CJK", "", 26)
pdf.multi_cell(W, 15, "《三国求生指南》细纲样板", align="C")
pdf.ln(8)
pdf.set_text_color(*C_LABEL)
pdf.set_font("CJK", "", 14)
pdf.multi_cell(W, 9, "上部前 3 卷 · 第 1~150 章", align="C")
pdf.multi_cell(W, 9, "四拍式细纲（起因 → 冲突 → 高潮 → 钩子）", align="C")

vols = {}
for c in data:
    n = c.get("chapter", 0)
    vols.setdefault((n - 1) // 50 + 1, []).append(c)

vol_titles = {1: ("穿越黄巾", "184年"), 2: ("桃园结义", "184-189年"), 3: ("洛阳乱局", "189年")}

for vol, chs in sorted(vols.items()):
    title, era = vol_titles.get(vol, ("", ""))
    pdf.add_page()
    pdf.set_text_color(*C_VOL)
    pdf.set_font("CJK", "", 17)
    pdf.multi_cell(W, 13, "第%d卷 · %s（%s）" % (vol, title, era))
    pdf.ln(3)
    for c in chs:
        # 章标题（深蓝，大字号）
        pdf.set_text_color(*C_TITLE)
        pdf.set_font("CJK", "", 13.5)
        pdf.multi_cell(W, 10, "第%d章  %s" % (c.get("chapter"), c.get("title", "")))
        pdf.ln(1)
        # 四拍
        b = c.get("beats", {})
        for k, label in [("起因", "起因"), ("冲突", "冲突"), ("高潮", "高潮"), ("钩子", "钩子")]:
            txt = (b.get(k, "") or "").strip()
            if not txt:
                continue
            pdf.set_text_color(*BEAT_COLOR.get(k, C_LABEL))
            pdf.set_font("CJK", "", 11)
            pdf.cell(20, 8, "【%s】" % label)
            pdf.set_text_color(*C_BODY)
            pdf.multi_cell(W - 20, 8, txt, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        # 分隔线
        pdf.set_draw_color(*C_LINE)
        pdf.line(15, pdf.get_y(), 15 + W, pdf.get_y())
        pdf.ln(3)

out = os.path.join(BASE, "细纲样板_1-150章.pdf")
pdf.output(out)
print("OK ->", out, "| pages:", pdf.pages_count, "| chapters:", len(data))
