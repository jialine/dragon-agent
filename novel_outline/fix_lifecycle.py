#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 lifecycle.py 两个 bug：
1. validate() 第4条「关系重复」崩溃：新关系元素是 str 而非 dict → 加 isinstance 防御
2. 「标为死亡但未记录死亡章节」死循环：
   - validate() 第2条接受「死亡」独立数组作为 death_chapter 来源
   - apply_changes() 新增处理「死亡」数组回写 death_chapter
   同时给所有遍历点加 isinstance 防御，防 LLM 输出脏格式再次崩溃。
"""
import sys

path = "/home/jialine/dragon-agent/novel_outline/lifecycle.py"
src = open(path, encoding="utf-8").read()
orig = src

# ---------- 修复1：validate 第1条「死人不能复活」加 isinstance ----------
old = '''        dead = set(r["name"] for r in self.conn.execute("SELECT name FROM persons WHERE status='死亡'"))
        for p in changes.get("人物变化", []):
            if p.get("name") in dead and p.get("status") in ("活跃", "暂离"):'''
new = '''        dead = set(r["name"] for r in self.conn.execute("SELECT name FROM persons WHERE status='死亡'"))
        for p in changes.get("人物变化", []):
            if not isinstance(p, dict):
                continue
            if p.get("name") in dead and p.get("status") in ("活跃", "暂离"):'''
assert old in src, "fix1 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复2：validate 第2条「死亡必须记录 death_chapter」 ----------
old = '''        # 2. 死亡必须记录 death_chapter
        for p in changes.get("人物变化", []):
            if p.get("status") == "死亡" and not p.get("death_chapter"):
                errors.append(f"人物矛盾：{p['name']} 标为死亡但未记录死亡章节")'''
new = '''        # 2. 死亡必须记录 death_chapter（"死亡"独立数组也可提供）
        death_names = set()
        for d in changes.get("死亡", []):
            if isinstance(d, dict) and d.get("name"):
                death_names.add(d["name"])
        for p in changes.get("人物变化", []):
            if not isinstance(p, dict):
                continue
            if (p.get("status") == "死亡" and not p.get("death_chapter")
                    and p.get("name") not in death_names):
                errors.append(f"人物矛盾：{p['name']} 标为死亡但未记录死亡章节")'''
assert old in src, "fix2 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复3：validate 第3条「数值范围」加 isinstance ----------
old = '''        for v in changes.get("数值变化", []):
            if v.get("name") == "认知值":'''
new = '''        for v in changes.get("数值变化", []):
            if not isinstance(v, dict):
                continue
            if v.get("name") == "认知值":'''
assert old in src, "fix3 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复4：validate 第4条「关系重复」加 isinstance（崩溃点） ----------
old = '''        for r in changes.get("新关系", []):
            key = (r.get("a"), r.get("b"), r.get("relation"))
            if key in existing:'''
new = '''        for r in changes.get("新关系", []):
            if not isinstance(r, dict):
                continue
            key = (r.get("a"), r.get("b"), r.get("relation"))
            if key in existing:'''
assert old in src, "fix4 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复5：apply_changes 关系循环加 isinstance + 新增死亡数组回写 ----------
old = '''        # 关系
        for r in changes.get("新关系", []):
            if r.get("a") and r.get("b") and r.get("relation"):
                self.add_relation(r["a"], r["b"], r["relation"], chapter)'''
new = '''        # 关系
        for r in changes.get("新关系", []):
            if isinstance(r, dict) and r.get("a") and r.get("b") and r.get("relation"):
                self.add_relation(r["a"], r["b"], r["relation"], chapter)

        # 死亡（独立"死亡"数组，回写 death_chapter）
        for d in changes.get("死亡", []):
            if isinstance(d, dict) and d.get("name"):
                self.upsert_person(d["name"], status="死亡",
                                   death_chapter=d.get("death_chapter") or chapter)'''
assert old in src, "fix5 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复6：apply_changes 人物变化循环加 isinstance ----------
old = '''        for p in changes.get("人物变化", []):
            name = p.get("name")
            if not name:
                continue'''
new = '''        for p in changes.get("人物变化", []):
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if not name:
                continue'''
assert old in src, "fix6 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复7：apply_changes 数值变化循环加 isinstance ----------
old = '''        for v in changes.get("数值变化", []):
            name = v.get("name"); nv = v.get("new_value")'''
new = '''        for v in changes.get("数值变化", []):
            if not isinstance(v, dict):
                continue
            name = v.get("name"); nv = v.get("new_value")'''
assert old in src, "fix7 未匹配"
src = src.replace(old, new, 1)

# ---------- 修复8：apply_changes 势力变化循环加 isinstance ----------
old = '''        for f in changes.get("势力变化", []):
            name = f.get("name")
            if not name:
                continue'''
new = '''        for f in changes.get("势力变化", []):
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            if not name:
                continue'''
assert old in src, "fix8 未匹配"
src = src.replace(old, new, 1)

if src == orig:
    print("ERROR: 无任何改动")
    sys.exit(1)

open(path, "w", encoding="utf-8").write(src)
print("OK: 8 处修复完成")
