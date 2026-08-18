#!/usr/bin/env python3
"""三国求生指南 — 3模型交叉审阅（在 .100 上跑，需 source .env）

用 3 个不同厂商模型分别审阅 2100 章大纲，找：
1. 剧情前后矛盾
2. 人物出现前后矛盾（死亡后复活、年龄/位置不合理、时间线错乱）
按部（上/中/下）分批审阅，汇总去重输出 review_report.json。
"""
import json
import os
import re
import subprocess
import time

API_URL = "https://api.andlapi.cn/v1/chat/completions"
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

REVIEW_MODELS = [
    "deepseek-v4-pro",
    "qwen3.7-max",
    "hy3-preview",
]

# 推理模型需要关闭 thinking，否则 reasoning 吃光 token 导致 content 空
REASONING_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash", "glm-5.2"}

BASE = os.path.dirname(os.path.abspath(__file__))
OUTLINE = json.load(open(os.path.join(BASE, "full_outline.json"), encoding="utf-8"))
OUT_DIR = os.path.join(BASE, "review_parts")
os.makedirs(OUT_DIR, exist_ok=True)


def curl_llm(model, system, user, max_tokens=16000, temperature=0.2, retries=3):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if model in REASONING_MODELS:
        payload["enable_thinking"] = False
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


SYSTEM = """你是资深网文总编辑，专门审查超长篇历史小说的连贯性与逻辑。逐条找出以下问题，严格输出 JSON：

1. 【剧情矛盾】事件顺序颠倒、因果倒置、前文已解决/已发生的事后文又重复或冲突、历史时间线错乱（如官渡之战发生在赤壁之前、人物在错误的历史事件中）。
2. 【人物矛盾】已死人物在后续章节再次登场（无复活设定）、人物年龄不合理（出场年龄与时间跨度矛盾）、位置瞬移（同一时间出现在两地）、立场/阵营突变无铺垫、人物关系前后冲突。

输出 JSON 格式：
{"issues": [{"chapter": 章号, "type": "剧情矛盾|人物矛盾", "issue": "问题描述（具体到人物名/事件）", "suggestion": "修改建议"}]}
没有问题输出 {"issues": []}。只输出 JSON，不要解释。"""


def split_parts(chapters):
    """按部拆分：上部 1-700，中部 701-1400，下部 1401-2100。"""
    return {
        "上部(1-700)": [c for c in chapters if c["chapter"] <= 700],
        "中部(701-1400)": [c for c in chapters if 700 < c["chapter"] <= 1400],
        "下部(1401-2100)": [c for c in chapters if c["chapter"] > 1400],
    }


def fmt_chapters(chapters):
    return "\n".join(f"第{c['chapter']}章《{c['title']}》：{c['summary']}" for c in chapters)


def main():
    chapters = OUTLINE["chapters"]
    parts = split_parts(chapters)

    all_issues = []
    for part_name, part_chapters in parts.items():
        part_text = fmt_chapters(part_chapters)
        for model in REVIEW_MODELS:
            model_key = model.replace("/", "_").replace(".", "_")
            out_file = os.path.join(OUT_DIR, f"review_{part_name[:1]}_{model_key}.json")
            if os.path.exists(out_file):
                print(f"[{part_name}][{model}] 已审阅，跳过", flush=True)
                d = json.load(open(out_file, encoding="utf-8"))
            else:
                user = f"""以下是小说《{OUTLINE['title']}》的大纲（{part_name}部分）。

【世界观与主角】
{OUTLINE['worldview']}
{json.dumps(OUTLINE['protagonist'], ensure_ascii=False)}

【本部分章节大纲】
{part_text}

请逐条审查上述大纲的剧情矛盾与人物矛盾（特别注意：人物死亡时间线、年龄、位置连贯性、历史事件先后顺序），输出 JSON："""
                print(f"[{part_name}][{model}] 审阅中...", flush=True)
                d = None
                for attempt in range(1, 4):
                    try:
                        content = curl_llm(model, SYSTEM, user)
                        if not content or not content.strip():
                            print(f"    ⚠️ 空返回，第{attempt}次重试", flush=True)
                            time.sleep(3)
                            continue
                        d = extract_json(content)
                        d = {"model": model, "part": part_name, **d}
                        break
                    except Exception as e:
                        print(f"    ⚠️ 解析失败: {e}，第{attempt}次重试", flush=True)
                        d = None
                        time.sleep(3)
                if not d:
                    d = {"model": model, "part": part_name, "issues": [], "error": "审阅失败(空返回/解析失败)"}
                    print(f"    ❌ {model} 审阅失败，issues 记空", flush=True)
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                print(f"    ✅ 发现 {len(d.get('issues', []))} 条", flush=True)
                time.sleep(1)

            for iss in d.get("issues", []):
                iss["model"] = model
                iss["part"] = part_name
                all_issues.append(iss)

    # 汇总去重（按 chapter+issue 文本）
    def _ch_num(x):
        try:
            return int(x.get("chapter", 0))
        except (TypeError, ValueError):
            return 0

    seen = set()
    dedup = []
    for iss in sorted(all_issues, key=_ch_num):
        key = (iss.get("chapter"), iss.get("type"), iss.get("issue", "")[:30])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(iss)

    report = {
        "title": OUTLINE["title"],
        "total_issues": len(dedup),
        "models": REVIEW_MODELS,
        "issues": dedup,
    }
    out = os.path.join(BASE, "review_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 审阅完成：共 {len(dedup)} 条矛盾 → {out}", flush=True)


if __name__ == "__main__":
    main()
