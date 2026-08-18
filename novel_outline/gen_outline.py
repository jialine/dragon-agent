#!/usr/bin/env python3
"""三国求生指南 — 2100章大纲生成器（在 .100 上跑，需 source .env 拿到 DEEPSEEK_API_KEY）

逐卷调用 deepseek-v4-pro 生成每卷 50 章的章名+梗概。
维护全局人物生死/登场状态，保证不出现「死人复活」「人物前后矛盾」。
断点续跑：每卷结果独立存盘 vol_XX.json，全部完成后合并成 full_outline.json。
"""
import json
import os
import re
import subprocess
import sys
import time

API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = os.environ.get("DRAMA_MODEL", "deepseek-v4-pro")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

BASE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK = json.load(open(os.path.join(BASE, "framework.json"), encoding="utf-8"))
OUT_DIR = os.path.join(BASE, "outline_parts")
os.makedirs(OUT_DIR, exist_ok=True)


def curl_llm(system, user, max_tokens=12000, temperature=0.7, retries=3):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(1, retries + 1):
        try:
            cmd = ["curl", "-s", "-k", "--max-time", "300", API_URL,
                   "-H", f"Authorization: Bearer {API_KEY}",
                   "-H", "Content-Type: application/json",
                   "-d", json.dumps(payload, ensure_ascii=False)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=310)
            resp = json.loads(r.stdout)
            if "error" in resp:
                raise RuntimeError(f"API error: {resp['error']}")
            return resp["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    [重试 {attempt}/{retries}] {e}", flush=True)
            if attempt == retries:
                raise
            time.sleep(5)


def extract_json(text):
    text = re.sub(r"```json\s*|```", "", text).strip()
    i = text.find("{")
    j = text.rfind("}")
    if i >= 0 and j > i:
        text = text[i:j + 1]
    return json.loads(text)


SYSTEM = """你是资深网文策划 + 三国历史专家。根据【小说设定】和【本卷要求】，为该卷 50 章逐章生成章名和一句话梗概，严格输出 JSON。

硬性规则：
1. 每章 summary 一句话 20~45 字，讲清核心剧情；title 4~8 字，有网文吸引力、可含搞笑梗。
2. 严格遵循三国时间线与历史事件进程，不得穿越未来事件。
3. 【此前已故】名单里的人物，本卷及之后任何章节绝不能再次登场（台词、回忆除外需注明）。
4. 人物年龄、位置、立场随剧情连贯推进，前后不矛盾。
5. 卷内 50 章要有起承转合，章与章情节咬合，卷末留钩子。
6. 融入搞笑元素（主角王铁柱用现代知识/梗吐槽）与指定的英雄救美情节。
7. 主角王铁柱贯穿每一章。

只输出 JSON，不要任何解释：
{"volume": 卷号, "chapters": [{"chapter": 全局章号, "title": "章名", "summary": "梗概"}]}"""


def build_prev_tail(prev_vol_data):
    """前卷最后 3 章摘要，用于衔接。"""
    if not prev_vol_data:
        return "（本卷是全篇开头，无前文）"
    tail = prev_vol_data["chapters"][-3:]
    return "\n".join(f"第{c['chapter']}章《{c['title']}》：{c['summary']}" for c in tail)


def main():
    vols = FRAMEWORK["volumes"]
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = int(sys.argv[2])
        vols = [v for v in vols if v["vol"] == only]
        print(f"仅生成卷 {only}", flush=True)
    key_deaths = FRAMEWORK["key_deaths"]

    accumulated_dead = set()   # 此前已故（绝不能出现）
    alive = set(["王铁柱"])     # 当前活跃（主角常驻）
    prev_vol_data = None

    for v in vols:
        vol_no = v["vol"]
        out_file = os.path.join(OUT_DIR, f"vol_{vol_no:02d}.json")
        if os.path.exists(out_file):
            print(f"[卷{vol_no}] 已存在，跳过", flush=True)
            prev_vol_data = json.load(open(out_file, encoding="utf-8"))
            # 更新状态
            alive.update(v["new_chars"].replace("、", " ").replace("，", " ").split() if v["new_chars"] else [])
            for d in (v["dead_chars"].replace("、", " ").replace("，", " ").split() if v["dead_chars"] else []):
                accumulated_dead.add(d)
                alive.discard(d)
            continue

        # 根据 era 年份，把 key_deaths 中已死的计入 accumulated_dead
        era = v["era"]
        m = re.match(r"(\d{3,4})", era)
        year = int(m.group(1)) if m else 999
        for name, death_year in key_deaths.items():
            if death_year < year:
                accumulated_dead.add(name)
                alive.discard(name)

        new_list = [x.strip() for x in re.split(r"[、，,]", v["new_chars"]) if x.strip()] if v["new_chars"] else []
        dead_this = [x.strip() for x in re.split(r"[、，,]", v["dead_chars"]) if x.strip()] if v["dead_chars"] else []

        # 本卷章号范围
        start_ch = (vol_no - 1) * 50 + 1
        end_ch = vol_no * 50

        user = f"""【小说设定】
标题：{FRAMEWORK['title']}
类型：{FRAMEWORK['genre']}
一句话梗概：{FRAMEWORK['logline']}
世界观：{FRAMEWORK['worldview']}
主角：{json.dumps(FRAMEWORK['protagonist'], ensure_ascii=False)}
总纲：{FRAMEWORK['synopsis']}

【本卷信息】第{vol_no}卷（{v['part']}《{v['title']}》）
时间：{v['era']}
主题：{v['theme']}
对应历史事件：{v['events']}

【本卷新登场人物】{('、'.join(new_list)) if new_list else '无'}
【本卷内会死亡的人物】{('、'.join(dead_this)) if dead_this else '无'}
【此前已故（绝不能登场）】{'、'.join(sorted(accumulated_dead)) if accumulated_dead else '无'}
【当前活跃重要人物】{'、'.join(sorted(alive)) if alive else '王铁柱'}
【英雄救美情节】{v['hero_save'] if v['hero_save'] else '本卷无'}
【搞笑方向】{v['comedy']}
【卷末钩子】{v['hook']}

【前卷结尾衔接】
{build_prev_tail(prev_vol_data)}

请生成第 {vol_no} 卷，章节范围：第 {start_ch}~{end_ch} 章（共 50 章），直接输出 JSON："""

        print(f"[卷{vol_no}] {v['part']}《{v['title']}》 第{start_ch}-{end_ch}章 生成中...", flush=True)
        data = None
        for attempt in range(1, 4):
            try:
                content = curl_llm(SYSTEM, user)
                data = extract_json(content)
                chapters = data.get("chapters", [])
                if len(chapters) >= 45:
                    break
                print(f"    ⚠️ 章节数不足({len(chapters)})，第{attempt}次重试", flush=True)
                data = None
            except Exception as e:
                print(f"    ⚠️ 解析失败: {e}，第{attempt}次重试", flush=True)
                data = None
            time.sleep(3)
        if not data:
            with open(os.path.join(OUT_DIR, "failed.log"), "a") as f:
                f.write(f"卷{vol_no} 生成失败\n")
            print(f"    ❌ 卷{vol_no} 重试3次仍失败，记录并跳过", flush=True)
            continue

        # 校验
        chapters = data.get("chapters", [])
        if len(chapters) != 50:
            print(f"    ⚠️ 卷{vol_no} 返回 {len(chapters)} 章（期望50），仍存盘", flush=True)
        # 补全局章号（以 chapter 字段为准，若缺失则按序）
        for i, c in enumerate(chapters):
            if not c.get("chapter"):
                c["chapter"] = start_ch + i

        data["volume"] = vol_no
        data["part"] = v["part"]
        data["vol_title"] = v["title"]
        data["era"] = v["era"]
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"    ✅ 卷{vol_no} 已存盘 ({len(chapters)}章)", flush=True)

        # 更新状态
        prev_vol_data = data
        alive.update(new_list)
        for d in dead_this:
            accumulated_dead.add(d)
            alive.discard(d)

        time.sleep(1)

    # 合并
    print("\n合并所有卷...", flush=True)
    all_chapters = []
    for v in vols:
        p = os.path.join(OUT_DIR, f"vol_{v['vol']:02d}.json")
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            all_chapters.extend(d.get("chapters", []))
    all_chapters.sort(key=lambda c: c.get("chapter", 0))
    full = {
        "title": FRAMEWORK["title"],
        "genre": FRAMEWORK["genre"],
        "logline": FRAMEWORK["logline"],
        "worldview": FRAMEWORK["worldview"],
        "protagonist": FRAMEWORK["protagonist"],
        "synopsis": FRAMEWORK["synopsis"],
        "ending": FRAMEWORK["ending"],
        "total_chapters": len(all_chapters),
        "chapters": all_chapters,
    }
    out = os.path.join(BASE, "full_outline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    print(f"✅ 完成：{len(all_chapters)} 章 → {out}", flush=True)


if __name__ == "__main__":
    main()
