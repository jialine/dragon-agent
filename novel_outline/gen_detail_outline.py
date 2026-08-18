#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三国求生指南 — 四拍细纲生成（.100 上跑）
把每章一句话梗概扩写成「起因→冲突→高潮→钩子」四拍细纲（每章100-150字）。
本脚本生成上部前3卷(1-150章)样板。
用法: gen_detail_outline.py <start_vol> <end_vol>
"""
import json, os, re, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
FW = json.load(open(os.path.join(BASE, "framework.json"), encoding="utf-8"))
OUTLINE = json.load(open(os.path.join(BASE, "full_outline.json"), encoding="utf-8"))
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"

start_vol = int(sys.argv[1]) if len(sys.argv) > 1 else 1
end_vol = int(sys.argv[2]) if len(sys.argv) > 2 else 4

with open(os.path.join(os.path.dirname(BASE), ".env")) as f:
    for line in f:
        if line.strip().startswith("DEEPSEEK_API_KEY"):
            API_KEY = line.split("=", 1)[1].strip(); break

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def curl_llm(system, user, max_tokens=12000, retries=4):
    payload = {"model": MODEL,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": 0.7,
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
                raise RuntimeError(str(resp["error"])[:150])
            c = (resp["choices"][0]["message"].get("content") or "").strip()
            if c:
                return c
            raise RuntimeError("empty")
        except Exception as e:
            log("  重试 %d: %s" % (a, e)); time.sleep(4)
    return None

def extract_json(text):
    text = re.sub(r"```json\s*|```", "", text).strip()
    i = text.find("["); j = text.rfind("]")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    i = text.find("{"); j = text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    return json.loads(text)

SYSTEM = """你是资深网文策划 + 三国历史专家。把每一章的一句话梗概扩写成「四拍细纲」，严格输出 JSON。

四拍要求：
1. 起因：本章开场的局势/动机/引子（谁、在哪、为何）
2. 冲突：本章的核心矛盾或转折（可含搞笑点）
3. 高潮：冲突爆发或最精彩的一幕
4. 钩子：章末悬念/反转，勾人看下一章

每章四拍合计 100~150 字，有画面感、有冲突、有网文爽点，可直接指导写出 1 万字正文。主角王铁柱贯穿，融入现代知识/梗的搞笑。保留原标题。

只输出 JSON 数组：[{"chapter": 章号, "title": "标题", "beats": {"起因":"...", "冲突":"...", "高潮":"...", "钩子":"..."}}]"""

def main():
    chapters = OUTLINE["chapters"]
    vol_map = {}
    for c in chapters:
        n = c.get("chapter", 0)
        v = (n - 1) // 50 + 1
        if start_vol <= v < end_vol:
            vol_map.setdefault(v, []).append(c)

    result = {}
    for v in FW["volumes"]:
        if not (start_vol <= v["vol"] < end_vol):
            continue
        chs = vol_map.get(v["vol"], [])
        if not chs:
            continue
        chs = sorted(chs, key=lambda x: x.get("chapter", 0))
        oneline = "\n".join("第%d章《%s》：%s" % (c["chapter"], c["title"], c["summary"]) for c in chs)
        user = """【小说设定】
标题：%s
类型：%s
主角：%s
金手指：%s
世界观：%s

【本卷信息】第%d卷（%s《%s》）时间：%s
主线：%s
对应历史事件：%s

【本卷各章一句话梗概】
%s

请把以上每章扩写成四拍细纲（起因→冲突→高潮→钩子，每章100-150字），输出 JSON 数组：""" % (
            FW["title"], FW["genre"], json.dumps(FW["protagonist"], ensure_ascii=False),
            FW["protagonist"].get("golden_finger", ""), FW["worldview"],
            v["vol"], v["part"], v["title"], v.get("era", ""), v.get("theme", ""),
            v.get("events", ""), oneline)
        log("第%d卷《%s》扩写细纲中(%d章)..." % (v["vol"], v["title"], len(chs)))
        data = None
        for attempt in range(1, 4):
            try:
                c = curl_llm(SYSTEM, user)
                if not c:
                    continue
                arr = extract_json(c)
                if isinstance(arr, dict):
                    arr = arr.get("chapters", arr.get("list", []))
                if len(arr) >= 40:
                    data = arr; break
                log("  章节数不足(%d) 重试%d" % (len(arr), attempt)); data = None
            except Exception as e:
                log("  解析失败 重试%d: %s" % (attempt, e)); data = None
            time.sleep(3)
        if not data:
            log("❌ 第%d卷失败" % v["vol"]); continue
        result[v["vol"]] = data
        log("✅ 第%d卷 %d章细纲完成" % (v["vol"], len(data)))

    flat = []
    for v in sorted(result):
        flat.extend(result[v])
    out_file = os.path.join(BASE, "detail_outline_sample.json")
    json.dump(flat, open(out_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("== 完成 %d 章，存 %s ==" % (len(flat), out_file))

if __name__ == "__main__":
    main()
