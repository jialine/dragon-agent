#!/usr/bin/env python3
"""三国求生指南 — 大纲入库 .32（在 .100 上跑，通过 HTTP 调 .32 webui API）

1. 创建项目（小说简介写入 projects.synopsis）
2. 逐章写入 chapters（title + summary = 每集大纲）
"""
import json
import os
import sys
import time

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
OUTLINE = json.load(open(os.path.join(BASE, "full_outline.json"), encoding="utf-8"))

WEBUI = os.environ.get("DRAGON_WEBUI", "http://192.168.0.32:5000")


def main():
    # 简介：总纲 + 三部曲 + 结局
    synopsis = f"{OUTLINE['synopsis']}\n\n【结局】{OUTLINE['ending']}"

    # 1. 创建项目
    print("创建项目...", flush=True)
    r = requests.post(f"{WEBUI}/api/projects", json={
        "name": OUTLINE["title"],
        "genre": OUTLINE["genre"],
        "logline": OUTLINE["logline"],
        "worldview": OUTLINE["worldview"],
        "synopsis": synopsis,
    }, timeout=30)
    r.raise_for_status()
    proj = r.json()
    project_id = proj.get("id")
    if not project_id:
        print("❌ 创建项目失败:", proj, flush=True)
        sys.exit(1)
    print(f"✅ 项目已创建 id={project_id} name={OUTLINE['title']}", flush=True)

    # 2. 逐章写入
    chapters = OUTLINE["chapters"]
    total = len(chapters)
    ok = 0
    fail = 0
    t0 = time.time()
    for i, c in enumerate(chapters, 1):
        payload = {
            "project_id": project_id,
            "chapter_number": c["chapter"],
            "title": c.get("title", ""),
            "summary": c.get("summary", ""),
        }
        try:
            resp = requests.post(f"{WEBUI}/api/chapters", json=payload, timeout=30)
            if resp.status_code == 200:
                ok += 1
            else:
                fail += 1
                if fail <= 5:
                    print(f"  ⚠️ 第{c['chapter']}章写入失败 {resp.status_code}: {resp.text[:100]}", flush=True)
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  ⚠️ 第{c['chapter']}章异常: {e}", flush=True)
        if i % 200 == 0:
            print(f"  进度 {i}/{total} (ok={ok} fail={fail}) 耗时{time.time()-t0:.0f}s", flush=True)

    print(f"\n✅ 入库完成：成功 {ok} 章，失败 {fail} 章，共 {total} 章", flush=True)
    print(f"   项目 id={project_id} → {WEBUI} 可查看", flush=True)


if __name__ == "__main__":
    main()
