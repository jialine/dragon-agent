#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史硬伤定向修复器——扫出硬伤后，逐章让 LLM 重写修复，保持剧情不变。
用法: python3 fix_history.py
流程: 扫描硬伤 → 按章节分组 → 对每章构造修复指令 → LLM 重写 → 写回
"""
import json
import os
import re
import sqlite3
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "novel.db")
FACTS = os.path.join(BASE, "history_facts.json")
CH_DIR = os.path.join(BASE, "chapters")
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"
ENV_FILE = "/home/jialine/dragon-agent/.env"

ERA_MAP = {
    "中平元年": 184, "中平二年": 185, "中平三年": 186,
    "中平四年": 187, "中平五年": 188, "中平六年": 189,
    "光熹": 189, "昭宁": 189, "永汉": 189, "初平元年": 190,
    "初平二年": 191, "兴平元年": 194, "建安元年": 196,
}


def load_key():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("export DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no key")


KEY = load_key()


def extract_year(date_str):
    if not date_str:
        return None
    m = re.search(r"(\d{3,4})年", date_str)
    if m:
        return int(m.group(1))
    for era, year in ERA_MAP.items():
        if era in date_str:
            return year
    return None


def scan_issues():
    """扫描硬伤，返回 [{chapter, type, issue, name}]"""
    facts = json.load(open(FACTS, encoding="utf-8"))
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    ch_year = {r["chapter"]: extract_year(r["date"]) for r in conn.execute("SELECT chapter, date FROM timeline")}
    issues = []

    appear = facts.get("人物登场时间", {})
    for name, info in appear.items():
        row = conn.execute("SELECT first_appear, last_seen FROM persons WHERE name=?", (name,)).fetchone()
        ch = None
        if row:
            ch = row["first_appear"] or row["last_seen"]
        if ch:
            year = ch_year.get(ch)
            if year and year < info["appear_after_year"]:
                issues.append({"chapter": ch, "type": "人物登场过早", "name": name,
                               "fix": f"人物「{name}」公元{year}年不该登场（应{info['appear_after_year']}年后）。"
                                      f"请把本章中「{name}」这个角色替换为一个合理的公元184年黄巾军无名小头目（如「黄巾降将」「黄巾偏将」），"
                                      f"或删除其姓名只保留泛称。其余剧情、台词、动作一律不改。"})

    titles = facts.get("职位权威值", {})
    for name, info in titles.items():
        row = conn.execute("SELECT title FROM persons WHERE name=?", (name,)).fetchone()
        if row and row["title"] and row["title"] in info.get("forbidden", []):
            issues.append({"chapter": None, "type": "职位错误", "name": name,
                           "fix": f"人物「{name}」的官职写成了「{row['title']}」，错误。"
                                  f"正确应为「{info['title']}」。请把全文中所有对 {name} 官职的错误称呼改成「{info['title']}」。"})

    ages = facts.get("人物年龄权威值", {})
    for name, correct in ages.items():
        row = conn.execute("SELECT age FROM persons WHERE name=?", (name,)).fetchone()
        if row and row["age"] and int(row["age"]) != correct:
            issues.append({"chapter": None, "type": "年龄错误", "name": name,
                           "fix": f"人物「{name}」年龄写成了 {row['age']} 岁，错误，正确应为 {correct} 岁。请改正文里的年龄表述。"})

    conn.close()
    return issues


def call_llm(prompt, max_tokens=4000):
    import requests
    for a in range(3):
        try:
            r = requests.post(API_URL,
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "enable_thinking": False, "temperature": 0.2},
                timeout=600, verify=False)
            r.raise_for_status()
            c = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if c:
                return c
        except Exception as e:
            print(f"  [重试{a+1}] {e}")
            import time; time.sleep(4)
    return None


def fix_chapter(chapter, fixes):
    """让 LLM 重写某章，修复该章的所有硬伤。"""
    path = os.path.join(CH_DIR, f"ch_{chapter:03d}.md")
    if not os.path.exists(path):
        print(f"  第{chapter}章: 文件不存在，跳过")
        return
    body = open(path, encoding="utf-8").read()
    prompt = (
        "你是小说校对编辑。下面是一章小说正文，需要修复其中的历史硬伤，**除此之外一个字都不许改动**。\n\n"
        "需要修复的问题：\n" + "\n".join(f"- {f['fix']}" for f in fixes) + "\n\n"
        "【正文】\n" + body + "\n\n"
        "要求：只修改与上述问题相关的文字，保持其余剧情、对话、节奏、字数完全不变。直接输出修改后的完整正文，不要任何解释。"
    )
    new_body = call_llm(prompt, max_tokens=8192)
    if new_body and len(re.sub(r"\s", "", new_body)) > 1000:
        open(path, "w", encoding="utf-8").write(new_body)
        print(f"  第{chapter}章: 已修复 {len(fixes)} 处")
    else:
        print(f"  第{chapter}章: 修复失败")


def main():
    issues = scan_issues()
    if not issues:
        print("✅ 无历史硬伤")
        return
    print(f"=== 扫描出 {len(issues)} 处硬伤 ===")
    for i in issues:
        print(f"  ⚠️ {i['type']}: {i['name']} (第{i['chapter']}章)")

    # 按章节分组
    by_chapter = {}
    global_fixes = []
    for i in issues:
        if i["chapter"] is None:
            global_fixes.append(i)
        else:
            by_chapter.setdefault(i["chapter"], []).append(i)

    # 修复有章节的
    for ch, fixes in by_chapter.items():
        fix_chapter(ch, fixes)

    # 全局修复（职位/年龄，可能跨多章）：找到相关章修复
    if global_fixes:
        # 职位/年龄错误：扫描所有章，找到提及该人物的章
        names = [f["name"] for f in global_fixes]
        import glob
        for f in sorted(glob.glob(os.path.join(CH_DIR, "ch_*.md"))):
            ch = int(os.path.basename(f).split("_")[1].split(".")[0])
            body = open(f, encoding="utf-8").read()
            # 找出本章涉及的全局硬伤人物
            relevant = [g for g in global_fixes if g["name"] in body]
            if relevant:
                fix_chapter(ch, relevant)

    print("=== 修复完成 ===")


if __name__ == "__main__":
    main()
