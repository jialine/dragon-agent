#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史硬伤扫描器——扫描 novel.db，对照 history_facts.json 找出历史硬伤。
用法: python3 history_check.py
"""
import json
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "novel.db")
FACTS = os.path.join(BASE, "history_facts.json")

# 年号 → 公元年映射（三国早期）
ERA_MAP = {
    "中平元年": 184, "中平二年": 185, "中平三年": 186,
    "中平四年": 187, "中平五年": 188, "中平六年": 189,
    "光熹": 189, "昭宁": 189, "永汉": 189, "初平元年": 190,
    "初平二年": 191, "兴平元年": 194, "建安元年": 196,
}


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


def main():
    facts = json.load(open(FACTS, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 章节 → 年份
    ch_year = {}
    for r in conn.execute("SELECT chapter, date FROM timeline"):
        ch_year[r["chapter"]] = extract_year(r["date"])

    issues = []

    # 1. 人物登场时间硬伤
    appear = facts.get("人物登场时间", {})
    for name, info in appear.items():
        row = conn.execute("SELECT first_appear, last_seen FROM persons WHERE name=?", (name,)).fetchone()
        ch = None
        if row:
            ch = row["first_appear"] or row["last_seen"]
        if ch:
            year = ch_year.get(ch)
            if year and year < info["appear_after_year"]:
                issues.append(f"第{ch}章({year}年): 人物「{name}」登场过早，"
                              f"应{info['appear_after_year']}年后（{info['note']}）")

    # 2. 物品登场时间硬伤
    appear_obj = facts.get("物品登场时间", {})
    for name, info in appear_obj.items():
        row = conn.execute("SELECT acquired_chapter FROM objects WHERE name=?", (name,)).fetchone()
        if row and row["acquired_chapter"]:
            ch = row["acquired_chapter"]
            year = ch_year.get(ch)
            if year and year < info["appear_after_year"]:
                issues.append(f"第{ch}章({year}年): 物品「{name}」出现过早，"
                              f"应{info['appear_after_year']}年后（{info['note']}）")

    # 3. 职位硬伤
    titles = facts.get("职位权威值", {})
    for name, info in titles.items():
        row = conn.execute("SELECT title FROM persons WHERE name=?", (name,)).fetchone()
        if row and row["title"] and row["title"] in info.get("forbidden", []):
            issues.append(f"人物「{name}」官职「{row['title']}」错误，应为「{info['title']}」（{info['note']}）")

    # 4. 年龄硬伤
    ages = facts.get("人物年龄权威值", {})
    for name, correct_age in ages.items():
        row = conn.execute("SELECT age FROM persons WHERE name=?", (name,)).fetchone()
        if row and row["age"] and int(row["age"]) != correct_age:
            issues.append(f"人物「{name}」年龄{row['age']}岁错误，应为{correct_age}岁")

    conn.close()

    print(f"=== 历史硬伤扫描结果：{len(issues)} 处 ===")
    for i in issues:
        print(f"  ⚠️ {i}")
    if not issues:
        print("  ✅ 无历史硬伤")


if __name__ == "__main__":
    main()
