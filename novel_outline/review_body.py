#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《三国求生指南》正文审阅脚本（.100 上跑）
读 chapters/ch_*.md 正文 → 10章一块 → 3模型并发审矛盾 → 输出 review_report_body.json

用法: python3 review_body.py
"""
import json, os, re, time, concurrent.futures
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CH_DIR = os.path.join(BASE, "chapters")
OUT_DIR = os.path.join(BASE, "review_body_parts")
os.makedirs(OUT_DIR, exist_ok=True)
API_URL = "https://api.andlapi.cn/v1/chat/completions"
REVIEW_MODELS = ["deepseek-v4-pro", "qwen3.7-max", "hy3-preview"]
REASONING = {"deepseek-v4-pro", "glm-5.2"}
CHUNK = 10  # 每块章数

with open(os.path.join(os.path.dirname(BASE), ".env")) as f:
    for line in f:
        if line.strip().startswith("DEEPSEEK_API_KEY"):
            API_KEY = line.strip().split("=", 1)[1].strip()
            break

# 读标题
try:
    detail = json.load(open(os.path.join(BASE, "detail_outline_sample.json"), encoding="utf-8"))
    TITLES = {c["chapter"]: c["title"] for c in detail}
except Exception:
    TITLES = {}

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def curl_llm(model, system, user, max_tokens=8000, retries=3):
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": 0.2}
    if model in REASONING:
        payload["enable_thinking"] = False
    for a in range(1, retries + 1):
        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": "Bearer " + API_KEY,
                         "Content-Type": "application/json"},
                json=payload,
                timeout=600,
                verify=False,
            )
            r.raise_for_status()
            resp = r.json()
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

SYSTEM = """你是资深网文总编辑，专门审查超长篇历史穿越小说的连贯性与逻辑。逐条找出以下问题，严格输出 JSON：

1.【剧情矛盾】事件顺序颠倒、因果倒置、前文已发生的事后文又重复或冲突。
2.【人物矛盾】已死人物再次登场（无复活设定）、人物位置不合理、立场/性格突变、人物关系冲突。
3.【设定矛盾】认知值数值跳变（如某章78下一章92且无解释）、系统规则前后不一致（消耗、解锁、模块能力）。
4.【时间线矛盾】穿越时间、事件先后顺序、季节/昼夜/路程时间不合理。

输出：{"issues":[{"chapter":章号,"type":"剧情矛盾|人物矛盾|设定矛盾|时间线矛盾","issue":"描述(注明涉及的第X章和第Y章)","suggestion":"修改建议"}]}
无问题输出 {"issues":[]}。只输出 JSON，不要任何其他文字。"""

def review_chunk(chunk):
    (lo, hi), chs = chunk
    text = "\n\n".join("第%d章《%s》正文：\n%s" % (c["chapter"], c["title"], c["body"]) for c in chs)
    out = []
    for model in REVIEW_MODELS:
        key = model.replace("/", "_").replace(".", "_")
        fname = os.path.join(OUT_DIR, "review_%d-%d_%s.json" % (lo, hi, key))
        if os.path.exists(fname):
            d = json.load(open(fname, encoding="utf-8"))
            log("  [%d-%d][%s] 缓存跳过" % (lo, hi, model))
        else:
            user = "小说《三国求生指南》正文第%d~%d章。请通读以下正文，找出剧情/人物/设定/时间线矛盾，输出 JSON：\n\n%s" % (lo, hi, text)
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
    import sys
    lo_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    hi_arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    files = sorted([f for f in os.listdir(CH_DIR) if f.startswith("ch_") and f.endswith(".md")])
    chapters = []
    for f in files:
        n = int(f.split("_")[1].split(".")[0])
        if lo_arg is not None and n < lo_arg:
            continue
        if hi_arg is not None and n > hi_arg:
            continue
        body = open(os.path.join(CH_DIR, f), encoding="utf-8").read().strip()
        chapters.append({"chapter": n, "title": TITLES.get(n, ""), "body": body})
    chapters.sort(key=lambda c: c["chapter"])
    log("共 %d 章正文" % len(chapters))

    chunks = []
    for lo in range(chapters[0]["chapter"], chapters[-1]["chapter"] + 1, CHUNK):
        hi = lo + CHUNK - 1
        chs = [c for c in chapters if lo <= c["chapter"] <= hi]
        if chs:
            chunks.append(((lo, hi), chs))
    log("分 %d 块审阅（每块%d章）" % (len(chunks), CHUNK))

    all_issues = []
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        for r in ex.map(review_chunk, chunks):
            all_issues.extend(r)

    # 统一 chapter 为 int（模型可能输出字符串）
    for iss in all_issues:
        try:
            iss["chapter"] = int(iss.get("chapter", 0))
        except (TypeError, ValueError):
            iss["chapter"] = 0
    seen = set(); dedup = []
    for iss in sorted(all_issues, key=lambda x: x["chapter"]):
        k = (iss["chapter"], iss.get("type"), (iss.get("issue") or "")[:40])
        if k in seen:
            continue
        seen.add(k); dedup.append(iss)
    lo_r = chapters[0]["chapter"]; hi_r = chapters[-1]["chapter"]
    report = {"title": "三国求生指南", "range": "%d-%d" % (lo_r, hi_r),
              "total_issues": len(dedup), "models": REVIEW_MODELS, "issues": dedup}
    rname = "review_report_body_%d-%d.json" % (lo_r, hi_r)
    json.dump(report, open(os.path.join(BASE, rname), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("✅ 审阅完成（%d-%d章）：共 %d 条矛盾（去重后）" % (lo_r, hi_r, len(dedup)))

if __name__ == "__main__":
    main()
