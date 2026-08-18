#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小说实体生命周期管理器
为 AI 写长篇小说提供「事前防错」能力——用数据库管理每个人物/物品/势力/地点/数值的生命周期。

核心工作流（每章）：
1. get_context(chapter)  → 查库，组装当前所有活跃实体的硬约束
2. 生成正文（注入硬约束）
3. extract_changes(body) → LLM 从正文提取结构化实体变化
4. validate(changes)     → 校验是否违反生命周期规则（死人复活/关系重复/数值乱跳/位置突变）
5. apply_changes(changes)→ 事务性回写数据库
"""
import json
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "novel.db")
SCHEMA = os.path.join(BASE, "schema.sql")


class LifecycleManager:
    def __init__(self, db_path=DB_PATH):
        self.db = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    # ---------- 建库 ----------
    def init_db(self):
        sql = open(SCHEMA, encoding="utf-8").read()
        self.conn.executescript(sql)
        self.conn.commit()

    # ---------- 实体增删查 ----------
    def _entity_id(self, etype, name, create=True):
        cur = self.conn.execute(
            "SELECT id FROM entities WHERE type=? AND name=?", (etype, name))
        row = cur.fetchone()
        if row:
            return row["id"]
        if not create:
            return None
        cur = self.conn.execute(
            "INSERT INTO entities(type, name) VALUES(?,?)", (etype, name))
        self.conn.commit()
        return cur.lastrowid

    def upsert_person(self, name, **fields):
        eid = self._entity_id("person", name)
        allowed = {"status", "gender", "age", "title", "location", "faction",
                   "relation_to_protagonist", "first_appear", "last_seen",
                   "death_chapter", "traits", "notes"}
        cols = ["name"] + [k for k in fields if k in allowed]
        # 先确认记录存在
        cur = self.conn.execute("SELECT entity_id FROM persons WHERE entity_id=?", (eid,))
        if cur.fetchone() is None:
            self.conn.execute("INSERT INTO persons(entity_id, name) VALUES(?,?)", (eid, name))
        for k, v in fields.items():
            if k in allowed and v is not None:
                self.conn.execute(f"UPDATE persons SET {k}=? WHERE entity_id=?", (v, eid))
        self.conn.commit()
        return eid

    def upsert_faction(self, name, **fields):
        eid = self._entity_id("faction", name)
        allowed = {"leader", "strength", "location", "status",
                   "established_chapter", "dissolved_chapter", "notes"}
        cur = self.conn.execute("SELECT entity_id FROM factions WHERE entity_id=?", (eid,))
        if cur.fetchone() is None:
            self.conn.execute("INSERT INTO factions(entity_id, name) VALUES(?,?)", (eid, name))
        for k, v in fields.items():
            if k in allowed and v is not None:
                self.conn.execute(f"UPDATE factions SET {k}=? WHERE entity_id=?", (v, eid))
        self.conn.commit()
        return eid

    def set_value(self, name, value, unit="", rule="", chapter=None):
        cur = self.conn.execute("SELECT id FROM value_trackers WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE value_trackers SET current_value=?, unit=?, rule=?, updated_chapter=? WHERE name=?",
                (value, unit, rule, chapter, name))
        else:
            self.conn.execute(
                "INSERT INTO value_trackers(name, current_value, unit, rule, updated_chapter) VALUES(?,?,?,?,?)",
                (name, value, unit, rule, chapter))
        self.conn.commit()

    def add_event(self, chapter, summary, entity_names=""):
        self.conn.execute(
            "INSERT INTO events(chapter, summary, entity_names) VALUES(?,?,?)",
            (chapter, summary, entity_names))
        self.conn.commit()

    def add_relation(self, a, b, relation, chapter, status="存续"):
        # 查重
        cur = self.conn.execute(
            "SELECT id FROM relations WHERE entity_a=? AND entity_b=? AND relation=?",
            (a, b, relation))
        if cur.fetchone():
            return
        self.conn.execute(
            "INSERT INTO relations(entity_a, entity_b, relation, established_chapter, status) VALUES(?,?,?,?,?)",
            (a, b, relation, chapter, status))
        self.conn.commit()

    def set_timeline(self, chapter, date, location):
        self.conn.execute(
            "INSERT OR REPLACE INTO timeline(chapter, date, location) VALUES(?,?,?)",
            (chapter, date, location))
        self.conn.commit()

    # ---------- 查询当前上下文（注入 prompt 的硬约束） ----------
    def get_context(self, chapter):
        """返回注入生成 prompt 的硬约束文本。"""
        parts = []

        # 活跃人物
        cur = self.conn.execute(
            "SELECT * FROM persons WHERE status IN ('活跃','暂离') ORDER BY name")
        alive = cur.fetchall()
        if alive:
            lines = []
            for p in alive:
                age = f"{p['age']}岁" if p['age'] else ""
                title = p['title'] or ""
                loc = p['location'] or "未知"
                rel = p['relation_to_protagonist'] or ""
                extra = "、".join(x for x in [age, title, rel] if x)
                lines.append(f"  {p['name']}：{loc}{('，'+extra) if extra else ''}")
            parts.append("【人物当前状态】\n" + "\n".join(lines))

        # 已死亡人物（硬约束：不可复活）
        cur = self.conn.execute(
            "SELECT name, death_chapter FROM persons WHERE status='死亡'")
        dead = cur.fetchall()
        if dead:
            parts.append("【已死亡人物（严禁再登场）】\n" +
                         "\n".join(f"  {p['name']}（第{p['death_chapter']}章死亡）" for p in dead))

        # 数值
        cur = self.conn.execute("SELECT * FROM value_trackers")
        vals = cur.fetchall()
        if vals:
            parts.append("【核心数值（严格一致）】\n" +
                         "\n".join(f"  {v['name']}={v['current_value']}{v['unit']}（{v['rule']}）" for v in vals))

        # 势力
        cur = self.conn.execute("SELECT * FROM factions WHERE status IN ('兴起','壮大')")
        facs = cur.fetchall()
        if facs:
            parts.append("【活跃势力】\n" +
                         "\n".join(f"  {f['name']}：{f['leader']}，兵力{f['strength'] or '?'}，{f['location']}" for f in facs))

        # 最近事件（防剧情重复）
        cur = self.conn.execute(
            "SELECT * FROM events ORDER BY chapter DESC, id DESC LIMIT 8")
        evs = cur.fetchall()
        if evs:
            parts.append("【最近已发生事件（严禁重复描写）】\n" +
                         "\n".join(f"  第{e['chapter']}章：{e['summary']}" for e in reversed(evs)))

        # 已建立关系（防重复建立）
        cur = self.conn.execute("SELECT * FROM relations WHERE status='存续'")
        rels = cur.fetchall()
        if rels:
            parts.append("【已建立关系（严禁重复建立/当陌生人）】\n" +
                         "\n".join(f"  {r['entity_a']}—{r['entity_b']}：{r['relation']}" for r in rels))

        # 时间线
        cur = self.conn.execute(
            "SELECT date, location FROM timeline WHERE chapter < ? ORDER BY chapter DESC LIMIT 1",
            (chapter,))
        tl = cur.fetchone()
        if tl and tl["date"]:
            parts.append(f"【当前时间】{tl['date']}（主场景：{tl['location']}）")

        return "\n\n".join(parts)

    # ---------- 从正文提取变化（LLM） ----------
    def build_extract_prompt(self, chapter, body, prev_context):
        return (
            "你是小说实体生命周期提取器。读下面这章正文，提取本章发生的所有实体变化，只输出 JSON。\n\n"
            f"【上一章结束时的实体状态】\n{prev_context}\n\n"
            f"【本章正文】\n{body}\n\n"
            "输出 JSON（没有的字段输出空数组或 null）：\n"
            '{"人物变化":[{"name":"人名","status":"活跃|暂离|死亡|未登场","location":"所在地",'
            '"relation_to_protagonist":"与主角关系","title":"官职","age":数字}],'
            '"死亡":[{"name":"人名","death_chapter":章号}],'
            '"物品变化":[{"name":"物品名","owner":"持有者","status":"被持有|丢失|损毁"}],'
            '"势力变化":[{"name":"势力名","strength":兵力数字,"status":"兴起|壮大|溃散|灭亡","leader":"首领"}],'
            '"数值变化":[{"name":"数值名","new_value":数字,"reason":"变化原因"}],'
            '"新事件":[{"summary":"一句话事件","entity_names":"涉及实体逗号分隔"}],'
            '"新关系":[{"a":"人物A","b":"人物B","relation":"关系"}],'
            '"时间":"本章日期如中平元年三月","主场景":"本章主地点"}\n'
            "注意：只有正文明确发生的变化才提取；未变化的不输出。只输出 JSON。"
        )

    # ---------- 校验（硬约束） ----------
    def validate(self, changes, chapter):
        """校验变化是否违反生命周期规则，返回错误列表（空=通过）。"""
        errors = []
        # 1. 死人不能复活
        dead = set(r["name"] for r in self.conn.execute("SELECT name FROM persons WHERE status='死亡'"))
        for p in changes.get("人物变化", []):
            if p.get("name") in dead and p.get("status") in ("活跃", "暂离"):
                errors.append(f"人物矛盾：{p['name']} 已于前文死亡，本章却让其活跃登场")
        # 2. 死亡必须记录 death_chapter
        for p in changes.get("人物变化", []):
            if p.get("status") == "死亡" and not p.get("death_chapter"):
                errors.append(f"人物矛盾：{p['name']} 标为死亡但未记录死亡章节")
        # 3. 数值范围
        for v in changes.get("数值变化", []):
            if v.get("name") == "认知值":
                nv = v.get("new_value")
                if nv is not None and (nv < 0 or nv > 100):
                    errors.append(f"设定矛盾：认知值 {nv} 超出 0-100 范围")
        # 4. 关系重复
        existing = set(
            (r["entity_a"], r["entity_b"], r["relation"])
            for r in self.conn.execute("SELECT * FROM relations WHERE status='存续'"))
        for r in changes.get("新关系", []):
            key = (r.get("a"), r.get("b"), r.get("relation"))
            if key in existing:
                errors.append(f"关系重复：{r.get('a')}与{r.get('b')}的{r.get('relation')}关系此前已建立")
        # 5. 时间线倒流
        tl = self.conn.execute(
            "SELECT date FROM timeline WHERE chapter < ? ORDER BY chapter DESC LIMIT 1", (chapter,)).fetchone()
        new_date = changes.get("时间")
        if tl and tl["date"] and new_date:
            # 简单比较：提取年份数字
            m1 = re.search(r"(\d+)年", tl["date"]); m2 = re.search(r"(\d+)年", new_date)
            if m1 and m2 and int(m2.group(1)) < int(m1.group(1)):
                errors.append(f"时间线矛盾：本章时间 {new_date} 早于上一章 {tl['date']}")
        return errors

    # ---------- 历史硬伤校验（三国历史权威设定） ----------
    def validate_history(self, changes, chapter):
        """校验历史硬伤：人物/物品登场时间、职位权威值、年龄权威值。返回错误列表。"""
        errors = []
        facts_path = os.path.join(BASE, "history_facts.json")
        if not os.path.exists(facts_path):
            return errors
        facts = json.load(open(facts_path, encoding="utf-8"))

        # 提取当前年份
        year = None
        date_str = changes.get("时间") or ""
        m = re.search(r"(\d{3,4})年", date_str)
        if m:
            year = int(m.group(1))
        else:
            row = self.conn.execute(
                "SELECT date FROM timeline WHERE chapter < ? ORDER BY chapter DESC LIMIT 1", (chapter,)).fetchone()
            if row and row["date"]:
                m = re.search(r"(\d{3,4})年", row["date"])
                if m:
                    year = int(m.group(1))

        if year:
            # 人物登场时间
            appear = facts.get("人物登场时间", {})
            for p in changes.get("人物变化", []):
                name = p.get("name")
                if name in appear and appear[name].get("appear_after_year"):
                    if year < appear[name]["appear_after_year"]:
                        errors.append(
                            f"历史硬伤：{name} 公元{year}年尚未登场（应{appear[name]['appear_after_year']}年后），"
                            f"本章却让其登场（{appear[name]['note']}）")
            # 物品登场时间
            appear_obj = facts.get("物品登场时间", {})
            for o in changes.get("物品变化", []):
                name = o.get("name")
                if name in appear_obj and appear_obj[name].get("appear_after_year"):
                    if year < appear_obj[name]["appear_after_year"]:
                        errors.append(
                            f"历史硬伤：{name} 公元{year}年尚未出现，本章却让其登场（{appear_obj[name]['note']}）")

        # 职位权威值（不依赖年份）
        titles = facts.get("职位权威值", {})
        for p in changes.get("人物变化", []):
            name = p.get("name")
            title = p.get("title")
            if name in titles and title:
                forbidden = titles[name].get("forbidden", [])
                if title in forbidden:
                    errors.append(
                        f"历史硬伤：{name} 的官职「{title}」错误，应为「{titles[name]['title']}」"
                        f"（{titles[name]['note']}）")

        # 年龄权威值
        ages = facts.get("人物年龄权威值", {})
        for p in changes.get("人物变化", []):
            name = p.get("name")
            age = p.get("age")
            if name in ages and age and int(age) != ages[name]:
                errors.append(f"历史硬伤：{name} 年龄应为{ages[name]}岁，本章写成{age}岁")

        return errors

    # ---------- 应用变化（事务回写） ----------
    def apply_changes(self, changes, chapter):
        """把提取的变化写入数据库。"""
        # 人物变化
        for p in changes.get("人物变化", []):
            name = p.get("name")
            if not name:
                continue
            fields = {k: v for k, v in p.items()
                      if k in ("status", "location", "relation_to_protagonist", "title", "age") and v is not None}
            if p.get("status") in ("活跃", "暂离"):
                fields.setdefault("last_seen", chapter)
                if "first_appear" not in fields:
                    cur = self.conn.execute("SELECT first_appear FROM persons WHERE name=?", (name,))
                    row = cur.fetchone()
                    if row is None or row["first_appear"] is None:
                        fields["first_appear"] = chapter
            if p.get("status") == "死亡":
                fields["death_chapter"] = p.get("death_chapter") or chapter
            self.upsert_person(name, **fields)

        # 数值变化
        for v in changes.get("数值变化", []):
            name = v.get("name"); nv = v.get("new_value")
            if name and nv is not None:
                old = self.conn.execute(
                    "SELECT current_value FROM value_trackers WHERE name=?", (name,)).fetchone()
                old_v = old["current_value"] if old else None
                self.set_value(name, nv, chapter=chapter)
                self.conn.execute(
                    "INSERT INTO value_history(tracker_name, chapter, old_value, new_value, reason) VALUES(?,?,?,?,?)",
                    (name, chapter, old_v, nv, v.get("reason", "")))

        # 势力变化
        for f in changes.get("势力变化", []):
            name = f.get("name")
            if not name:
                continue
            eid = self._entity_id("faction", name)
            cur = self.conn.execute("SELECT entity_id FROM factions WHERE entity_id=?", (eid,))
            if cur.fetchone() is None:
                self.conn.execute("INSERT INTO factions(entity_id, name) VALUES(?,?)", (eid, name))
            for k in ("strength", "status", "leader", "location"):
                if f.get(k) is not None:
                    self.conn.execute(f"UPDATE factions SET {k}=? WHERE entity_id=?", (f[k], eid))

        # 事件
        for e in changes.get("新事件", []):
            summary = e.get("summary") if isinstance(e, dict) else e
            names = e.get("entity_names", "") if isinstance(e, dict) else ""
            if summary:
                self.add_event(chapter, summary, names)

        # 关系
        for r in changes.get("新关系", []):
            if r.get("a") and r.get("b") and r.get("relation"):
                self.add_relation(r["a"], r["b"], r["relation"], chapter)

        # 时间线
        if changes.get("时间"):
            self.set_timeline(chapter, changes["时间"], changes.get("主场景", ""))

        self.conn.commit()

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    lm = LifecycleManager()
    lm.init_db()
    print("数据库初始化完成:", lm.db)
    lm.close()
