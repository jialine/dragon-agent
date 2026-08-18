#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三国求生指南 — 增强版并行生成器（.100 上跑）
相比 gen_outline.py 的增强：
1. enable_thinking:false —— deepseek-v4-pro 关闭推理，快且不空返回
2. 事件去重 —— 传【已发生历史事件】清单，杜绝跨卷重复写同一事件
3. 并行 —— 按卷范围多 worker
用法: gen_outline_v2.py <start_vol> <end_vol> <worker_id>
"""
import json, os, re, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK = json.load(open(os.path.join(BASE, "framework.json"), encoding="utf-8"))
OUT_DIR = os.path.join(BASE, "outline_parts")
os.makedirs(OUT_DIR, exist_ok=True)
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"

start_vol = int(sys.argv[1]) if len(sys.argv) > 1 else 1
end_vol = int(sys.argv[2]) if len(sys.argv) > 2 else 43
WID = sys.argv[3] if len(sys.argv) > 3 else "w0"

with open(os.path.join(os.path.dirname(BASE), ".env")) as f:  # /home/jialine/dragon-agent/.env
    for line in f:
        if line.strip().startswith("DEEPSEEK_API_KEY"):
            API_KEY = line.split("=", 1)[1].strip(); break

def log(msg):
    print("[%s][%s] %s" % (time.strftime("%H:%M:%S"), WID, msg), flush=True)

def curl_llm(system, user, max_tokens=8000, temperature=0.7, retries=4):
    payload = {"model": MODEL,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature,
               "enable_thinking": False}
    for a in range(1, retries+1):
        try:
            r = subprocess.run(["curl", "-s", "-k", "--max-time", "300", API_URL,
                "-H", "Authorization: Bearer " + API_KEY,
                "-H", "Content-Type: application/json",
                "-d", json.dumps(payload, ensure_ascii=False)],
                capture_output=True, text=True, timeout=310)
            resp = json.loads(r.stdout)
            if "error" in resp:
                raise RuntimeError(str(resp["error"])[:200])
            c = (resp["choices"][0]["message"].get("content") or "").strip()
            if c:
                return c
            raise RuntimeError("empty content")
        except Exception as e:
            log("  重试%d/%d %s" % (a, retries, e)); time.sleep(4)
    return None

def extract_json(text):
    text = re.sub(r"```json\s*|```", "", text).strip()
    i = text.find("{"); j = text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    return json.loads(text)

SYSTEM = """你是资深网文策划 + 三国历史专家。根据【小说设定】和【本卷要求】，为该卷 50 章逐章生成章名和一句话梗概，严格输出 JSON。

硬性规则：
1. 每章 summary 一句话 20~45 字讲清核心剧情；title 4~8 字有网文吸引力、可含搞笑梗。
2. 严格遵循【本卷时间】的年份与三国历史进程，绝不写未来事件。
3. 【已发生历史事件】清单里的事件，前面卷次已经写完了，本卷绝不能把它们再作为主线重写——本卷只推进【本卷历史事件】。
4. 【此前已故】名单里的人物，本卷及之后绝不能登场（回忆需注明）。
5. 人物年龄、位置、立场随剧情连贯推进，前后不矛盾。
6. 卷内 50 章起承转合，章章咬合，卷末留钩子。
7. 融入搞笑（王铁柱用现代知识/梗吐槽）与指定的英雄救美情节。
8. 主角王铁柱贯穿每一章。

只输出 JSON：{"volume": 卷号, "chapters": [{"chapter": 全局章号, "title": "章名", "summary": "梗概"}]}"""

def main():
    key_deaths = FRAMEWORK.get("key_deaths", {})
    vols = [v for v in FRAMEWORK["volumes"] if start_vol <= v["vol"] < end_vol]

    for v in vols:
        vol_no = v["vol"]
        out_file = os.path.join(OUT_DIR, "vol_%02d.json" % vol_no)
        if os.path.exists(out_file):
            log("卷%d 已存在，跳过" % vol_no); continue

        # 规范状态：此前已故 + 已发生事件（从框架前序卷推导，确定性）
        era = v.get("era", "")
        m = re.match(r"(\d{3,4})", era)
        year = int(m.group(1)) if m else 999
        accumulated_dead = set()
        for name, dy in key_deaths.items():
            if dy < year:
                accumulated_dead.add(name)
        accumulated_events = []
        for pv in FRAMEWORK["volumes"]:
            if pv["vol"] >= vol_no:
                break
            if pv.get("events"):
                accumulated_events.append("第%d卷《%s》：%s" % (pv["vol"], pv["title"], pv["events"]))
            if pv.get("dead_chars"):
                for d in re.split(r"[、，,]", pv["dead_chars"]):
                    d = d.strip()
                    if d: accumulated_dead.add(d)

        new_list = [x.strip() for x in re.split(r"[、，,]", v["new_chars"]) if x.strip()] if v.get("new_chars") else []
        dead_this = [x.strip() for x in re.split(r"[、，,]", v["dead_chars"]) if x.strip()] if v.get("dead_chars") else []
        start_ch = (vol_no - 1) * 50 + 1
        end_ch = vol_no * 50

        # 前卷结尾衔接（确定性：用框架前序卷的 hook）
        prev_hook = ""
        for pv in FRAMEWORK["volumes"]:
            if pv["vol"] == vol_no - 1:
                prev_hook = "第%d卷结尾钩子：%s" % (pv["vol"], pv.get("hook", ""))
                break

        user = """【小说设定】
标题：%s
类型：%s
一句话梗概：%s
世界观：%s
主角：%s
总纲：%s

【本卷信息】第%d卷（%s《%s》）
时间：%s
主题：%s
本卷历史事件：%s

【本卷新登场】%s
【本卷内死亡】%s
【此前已故（绝不能登场）】%s
【已发生历史事件（勿重复）】%s
【英雄救美】%s
【搞笑方向】%s
【卷末钩子】%s
【前卷结尾】%s

请生成第%d卷，章节范围第%d~%d章（共50章），直接输出 JSON：""" % (
            FRAMEWORK["title"], FRAMEWORK["genre"], FRAMEWORK["logline"],
            FRAMEWORK["worldview"], json.dumps(FRAMEWORK["protagonist"], ensure_ascii=False),
            FRAMEWORK["synopsis"],
            vol_no, v["part"], v["title"], v.get("era",""), v.get("theme",""), v.get("events",""),
            "、".join(new_list) if new_list else "无",
            "、".join(dead_this) if dead_this else "无",
            "、".join(sorted(accumulated_dead)) if accumulated_dead else "无",
            "\n".join(accumulated_events) if accumulated_events else "无",
            v.get("hero_save") or "本卷无",
            v.get("comedy",""), v.get("hook",""), prev_hook,
            vol_no, start_ch, end_ch)

        log("卷%d %s《%s》 第%d-%d章 生成中..." % (vol_no, v["part"], v["title"], start_ch, end_ch))
        t0 = time.time()
        data = None
        for attempt in range(1, 4):
            try:
                content = curl_llm(SYSTEM, user)
                if not content:
                    raise RuntimeError("empty")
                data = extract_json(content)
                if len(data.get("chapters", [])) >= 45:
                    break
                log("  章节数不足(%d) 重试%d" % (len(data.get("chapters", [])), attempt))
                data = None
            except Exception as e:
                log("  解析失败 重试%d: %s" % (attempt, e)); data = None
            time.sleep(3)
        if not data:
            log("❌ 卷%d 3次失败，跳过" % vol_no); continue

        chapters = data.get("chapters", [])
        for i, c in enumerate(chapters):
            if not c.get("chapter"):
                c["chapter"] = start_ch + i
        data["volume"] = vol_no; data["part"] = v["part"]
        data["vol_title"] = v["title"]; data["era"] = v.get("era","")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        log("✅ 卷%d 存盘 %d章 %.0fs" % (vol_no, len(chapters), time.time()-t0))
        time.sleep(1)

    log("worker 完成")

if __name__ == "__main__":
    main()
