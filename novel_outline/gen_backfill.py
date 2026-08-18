#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《三国求生指南》补数据脚本（backfill）
读已有的 68~150 章 md 正文，逐章提取实体变化回写数据库，不重写正文。

与 gen_chapter_body_v3/v4 的区别：
- 不生成正文，直接读 chapters/ch_XXX.md
- 校验矛盾只记录告警，不阻断 apply（正文已定，无法重写）
- 逐章串行推进，让数据库从 67 章状态连续推进到 150 章

用法:
  python3 gen_backfill.py 68 150
"""
import json
import os
import re
import sys
import time

import requests

from lifecycle import LifecycleManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "chapters")
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"
ENV_FILE = "/home/jialine/dragon-agent/.env"


def load_key():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("export DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DEEPSEEK_API_KEY not found")


KEY = load_key()


def call_llm(messages, max_tokens=3000, temperature=0.2, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages,
                      "max_tokens": max_tokens, "enable_thinking": False,
                      "temperature": temperature},
                timeout=600, verify=False)
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
            raise RuntimeError("empty")
        except Exception as e:
            print(f"  [LLM重试 {attempt+1}/{retries}] {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    return None


def extract_json(text):
    t = re.sub(r"```json\s*|```", "", text).strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i:j + 1])
    return json.loads(t)


def main():
    start, end = 68, 150
    if len(sys.argv) >= 3:
        start, end = int(sys.argv[1]), int(sys.argv[2])

    lm = LifecycleManager()
    log_lines = []
    contradiction_count = 0
    skipped = []

    for n in range(start, end + 1):
        path = os.path.join(OUT_DIR, f"ch_{n:03d}.md")
        if not os.path.exists(path):
            skipped.append(n)
            print(f"第{n}章: md 缺失，跳过", flush=True)
            continue
        body = open(path, encoding="utf-8").read().strip()
        if not body:
            skipped.append(n)
            print(f"第{n}章: md 为空，跳过", flush=True)
            continue

        context = lm.get_context(n)
        t0 = time.time()

        # 提取变化
        try:
            raw = call_llm([{"role": "user", "content": lm.build_extract_prompt(n, body, context)}],
                           max_tokens=3000, temperature=0.2)
            if not raw:
                print(f"第{n}章: 提取失败（LLM无返回）", flush=True)
                skipped.append(n)
                continue
            changes = extract_json(raw)
        except Exception as e:
            print(f"第{n}章: 提取异常 {e}", flush=True)
            skipped.append(n)
            continue

        # 校验（只记录，不阻断 apply）
        try:
            errs = lm.validate(changes, n) + lm.validate_history(changes, n)
        except Exception as e:
            errs = [f"校验异常: {e}"]
        if errs:
            contradiction_count += len(errs)
            print(f"第{n}章: ⚠ {len(errs)} 条矛盾 -> {'; '.join(errs[:2])}", flush=True)

        # apply（回写数据库）
        try:
            lm.apply_changes(changes, n)
        except Exception as e:
            print(f"第{n}章: apply 异常 {e}", flush=True)
            skipped.append(n)
            continue

        cog = next((v.get("new_value") for v in changes.get("数值变化", [])
                    if isinstance(v, dict) and v.get("name") == "认知值"), "?")
        print(f"第{n}章: 认知值={cog} / {time.time()-t0:.0f}s", flush=True)

    lm.close()
    print(f"\n=== 完成 ===", flush=True)
    print(f"范围 {start}~{end}，跳过 {len(skipped)} 章: {skipped if skipped else '无'}", flush=True)
    print(f"记录矛盾 {contradiction_count} 条（未阻断，正文已定）", flush=True)


if __name__ == "__main__":
    main()
