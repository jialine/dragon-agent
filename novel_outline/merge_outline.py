#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 vol_XX.json -> full_outline.json（.100 上跑）"""
import json, os, glob

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "outline_parts")
FW = json.load(open(os.path.join(BASE, "framework.json"), encoding="utf-8"))

all_chapters = []
missing = []
for vol in FW["volumes"]:
    p = os.path.join(OUT_DIR, "vol_%02d.json" % vol["vol"])
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        all_chapters.extend(d.get("chapters", []))
    else:
        missing.append(vol["vol"])

all_chapters.sort(key=lambda c: c.get("chapter", 0))
# 去重（按 chapter 号，保留第一个）
seen = set()
dedup = []
for c in all_chapters:
    n = c.get("chapter")
    if n in seen:
        continue
    seen.add(n)
    dedup.append(c)
all_chapters = dedup

full = {
    "title": FW["title"], "genre": FW["genre"], "logline": FW["logline"],
    "worldview": FW["worldview"], "protagonist": FW["protagonist"],
    "synopsis": FW["synopsis"], "ending": FW["ending"],
    "total_chapters": len(all_chapters),
    "chapters": all_chapters,
}
json.dump(full, open(os.path.join(BASE, "full_outline.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# 校验章号连续性
nums = sorted(c.get("chapter", 0) for c in all_chapters)
gaps = []
for i in range(len(nums) - 1):
    if nums[i+1] != nums[i] + 1:
        gaps.append((nums[i], nums[i+1]))
print("总章节:", len(all_chapters))
print("章号范围:", nums[0] if nums else 0, "-", nums[-1] if nums else 0)
print("缺失卷:", missing if missing else "无")
print("章号断点:", gaps if gaps else "无（连续）")
