#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三国求生指南 — 分块3模型审阅 v2（.100 上跑）
v1 把整部700章一次性塞给模型导致空返回；v2 按 300 章分块，3模型并行审。
"""
import json, os, re, subprocess, time, concurrent.futures

BASE = os.path.dirname(os.path.abspath(__file__))
OUTLINE = json.load(open(os.path.join(BASE, "full_outline.json"), encoding="utf-8"))
OUT_DIR = os.path.join(BASE, "review_parts")
os.makedirs(OUT_DIR, exist_ok=True)
API_URL = "https://api.andlapi.cn/v1/chat/completions"
REVIEW_MODELS = ["deepseek-v4-pro", "qwen3.7-max", "hy3-preview"]
REASONING = {"deepseek-v4-pro", "glm-5.2"}

with open(os.path.join(os.path.dirname(BASE), ".env")) as f:
    for line in f:
        if line.strip().startswith("DEEPSEEK_API_KEY"):
            API_KEY = line.split("=", 1)[1].strip(); break

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def curl_llm(model, system, user, max_tokens=8000, retries=3):
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": 0.2}
    if model in REASONING:
        payload["enable_thinking"] = False
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
            log("  重试 %s %d: %s" % (model, a, e)); time.sleep(4)
    return None

def extract_json(text):
    text = re.sub(r"```json\s*|```", "", text).strip()
    i = text.find("{"); j = text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    return json.loads(text)

SYSTEM = """你是资深网文总编辑，专门审查超长篇历史小说的连贯性与逻辑。逐条找出以下问题，严格输出 JSON：
1. 【剧情矛盾】事件顺序颠倒、因果倒置、前文已发生的事后文又重复或冲突、历史时间线错乱。
2. 【人物矛盾】已死人物后续再次登场（无复活设定）、年龄/位置不合理、立场突变、人物关系冲突。
输出：{"issues":[{"chapter":章号,"type":"剧情矛盾|人物矛盾","issue":"描述","suggestion":"建议"}]}
无问题输出 {"issues":[]}。只输出 JSON。"""

def review_chunk(chunk):
    (lo, hi), chs = chunk
    text = "\n".join("第%d章《%s》：%s" % (c["chapter"], c["title"], c["summary"]) for c in chs)
    out = []
    for model in REVIEW_MODELS:
        key = model.replace("/", "_").replace(".", "_")
        fname = os.path.join(OUT_DIR, "review_%d-%d_%s.json" % (lo, hi, key))
        if os.path.exists(fname):
            d = json.load(open(fname, encoding="utf-8"))
            log("  [%d-%d][%s] 缓存跳过" % (lo, hi, model))
        else:
            user = "小说《%s》大纲第%d~%d章：\n\n【世界观/主角】%s\n%s\n\n【章节】\n%s\n\n请审查剧情矛盾与人物矛盾，输出 JSON：" % (
                OUTLINE["title"], lo, hi, OUTLINE["worldview"],
                json.dumps(OUTLINE["protagonist"], ensure_ascii=False), text)
            d = None
            for a in range(1, 4):
                try:
                    c = curl_llm(model, SYSTEM, user)
                    if not c:
                        continue
                    d = extract_json(c)
                    d = {"model": model, "part": "%d-%d" % (lo, hi), **d}
                    break
                except Exception as e:
                    log("  [%d-%d][%s] 失败%d: %s" % (lo, hi, model, a, e)); d = None; time.sleep(3)
            if not d:
                d = {"model": model, "part": "%d-%d" % (lo, hi), "issues": [], "error": "fail"}
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        for iss in d.get("issues", []):
            iss["model"] = model
            iss["part"] = "%d-%d" % (lo, hi)
            out.append(iss)
        log("  [%d-%d][%s] %d issues" % (lo, hi, model, len(d.get("issues", []))))
    return out

def main():
    chapters = OUTLINE["chapters"]
    chunks = []
    for lo in range(1, 2101, 300):
        hi = min(lo + 299, 2100)
        chs = [c for c in chapters if lo <= c["chapter"] <= hi]
        chunks.append(((lo, hi), chs))
    log("分 %d 块审阅" % len(chunks))

    all_issues = []
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        for r in ex.map(review_chunk, chunks):
            all_issues.extend(r)

    seen = set(); dedup = []
    for iss in sorted(all_issues, key=lambda x: x.get("chapter", 0) or 0):
        k = (iss.get("chapter"), iss.get("type"), (iss.get("issue") or "")[:30])
        if k in seen:
            continue
        seen.add(k); dedup.append(iss)
    report = {"title": OUTLINE["title"], "total_issues": len(dedup),
              "models": REVIEW_MODELS, "issues": dedup}
    json.dump(report, open(os.path.join(BASE, "review_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("✅ 审阅完成：共 %d 条矛盾" % len(dedup))

if __name__ == "__main__":
    main()
