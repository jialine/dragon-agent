#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《三国求生指南》正文生成脚本 v2（状态追踪版）
维护 state.json 跨章状态（认知值/兵力/人物关系/时间线/解锁模块/关键事件），
每章生成前注入状态、生成后 LLM 提取状态变化回写。从根上解决：
认知值断裂、人物失忆、兵力乱跳、剧情重复。

用法:
  python3 gen_chapter_body_v2.py            # 从 current_chapter+1 续跑到 150
  python3 gen_chapter_body_v2.py 2 150      # 指定范围
"""
import json
import os
import re
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DETAIL_JSON = os.path.join(BASE_DIR, "detail_outline_sample.json")
CH1_BODY = os.path.join(BASE_DIR, "ch1_body.txt")
OUT_DIR = os.path.join(BASE_DIR, "chapters")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"
TARGET_WORDS = 3500
MAX_TOKENS = 8192
ENV_FILE = "/home/jialine/dragon-agent/.env"


def load_key():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("export DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DEEPSEEK_API_KEY not found")


KEY = load_key()

INITIAL_STATE = {
    "current_chapter": 1,
    "认知值": 92,
    "兵力": "无（尚未组织武装）",
    "人物关系": "未遇见刘关张",
    "时间": "中平元年（184年）二月",
    "已解锁": [],
    "关键事件": [
        "王铁柱（26岁程序员）猝死穿越到东汉184年破庙",
        "绑定「现代知识系统」",
        "检索黄巾起义，认知值100→92",
    ],
}


def call_llm(messages, max_tokens=MAX_TOKENS, temperature=0.8, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": "Bearer " + KEY,
                         "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages,
                      "max_tokens": max_tokens, "enable_thinking": False,
                      "temperature": temperature},
                timeout=600, verify=False,
            )
            r.raise_for_status()
            data = r.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
            raise RuntimeError("empty content")
        except Exception as e:
            print(f"  [LLM重试 {attempt+1}/{retries}] {e}")
            time.sleep(5 * (attempt + 1))
    return None


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE, encoding="utf-8"))
    return dict(INITIAL_STATE)


def save_state(s):
    json.dump(s, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def state_block(state):
    """把状态档案格式化成 prompt 片段。"""
    events = "；".join(state.get("关键事件", [])[-5:])
    unlock = "、".join(state.get("已解锁", [])) or "无"
    return (
        "【当前状态（必须严格遵循，不得违背）】\n"
        "- 认知值：{0}（检索知识消耗8-10点/次；<60短期失忆；<30永久记忆损伤；自然恢复约1点/天）\n"
        "- 兵力：{1}\n"
        "- 人物关系：{2}\n"
        "- 当前时间：{3}\n"
        "- 已解锁模块：{4}\n"
        "- 最近事件：{5}\n"
    ).format(
        state.get("认知值", 92), state.get("兵力", "无"),
        state.get("人物关系", "未遇刘关张"), state.get("时间", "中平元年二月"),
        unlock, events,
    )


SYSTEM_HEAD = """你是资深网文写手，正在续写长篇历史穿越小说《三国求生指南》。

【世界观】主角王铁柱，26岁996程序员，加班猝死穿越到东汉中平元年（184年黄巾起义爆发年），绑定「现代知识系统」。金手指：现代知识检索（消耗认知值，认知值<60短期失忆、<30永久记忆损伤）、军事/科技/人际模块（后续解锁）。主角职业病：见什么都能用程序员/产品经理视角吐槽（UI、需求评审、KPI、AB测试、迭代、bug、解绑、灰度、上线、宕机）。

【文风铁律】
1. 短句快节奏，多分段，单段不超过5行，白描克制不堆砌形容词。
2. 对话占比约45%，对话自然有网感；主角内心独白密集、幽默。
3. 程序员梗全程贯穿，把历史事件套互联网黑话，但别生硬。
4. 系统面板用【】框，主角看到面板必先吐槽UI/交互/产品逻辑。
5. 每拍（起因/冲突/高潮/钩子）都有具体事件+信息增量，不注水、不空转、不重复前文。
6. 章末必须有强钩子（悬念/反转/金句），让人想点下一章。
7. 保持与历史主线（三国演义进程）一致，历史人物言行/职位符合史实。
8. 认知值每次变化必须在正文里明确标注，格式：【认知值：X → Y】。
9. 第三人称叙述，全程不要切换成第一人称"我"。

【字数】每章 {target} 字左右，写实不硬凑，宁短勿水。

【风格参照】以下是第1章正文，严格模仿其节奏、对话密度、梗的风格：

{ch1_body}"""


def generate_body(ch, state, prev_ending, ch1_body):
    beats = ch["beats"]
    system_prompt = SYSTEM_HEAD.format(target=TARGET_WORDS, ch1_body=ch1_body)
    user_prompt = (
        state_block(state) + "\n"
        + f"请写第{ch['chapter']}章《{ch['title']}》。\n\n"
        + f"上一章结尾：{prev_ending}\n\n"
        + "本章节拍细纲（四拍，严格按此顺序展开，不要跳拍、不要新增无关支线）：\n"
        + f"【起因】{beats['起因']}\n"
        + f"【冲突】{beats['冲突']}\n"
        + f"【高潮】{beats['高潮']}\n"
        + f"【钩子】{beats['钩子']}\n\n"
        + f"要求：完整写成 {TARGET_WORDS} 字左右正文。所有状态变化（认知值/兵力/人物/时间）"
          "必须与【当前状态】衔接、合理自洽。章末停在钩子处留悬念。"
          "直接输出正文，不要标题、不要任何解释、不要「第X章完」标注。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    body = call_llm(messages, temperature=0.8) or ""
    # 截断续写
    guard = 0
    while len(re.sub(r"\s", "", body)) < 2800 and guard < 3:
        guard += 1
        more = call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": body},
            {"role": "user", "content": "（继续写，直到本章完整结束，章末停在钩子处）"},
        ], temperature=0.8)
        if not more:
            break
        body += more
    return body.strip()


def extract_state(body, prev_state, chapter_no, title):
    """让 LLM 从正文提取本章结束状态。返回更新后的 state dict。"""
    prompt = (
        "你是小说状态追踪器。读下面这章正文，提取本章结束时的状态，只输出 JSON。\n\n"
        f"【上一章结束状态】{json.dumps(prev_state, ensure_ascii=False)}\n\n"
        f"【本章正文】\n{body}\n\n"
        "输出 JSON（字段：认知值为数字=本章结束时认知值；"
        "兵力/人物关系/时间 若本章无变化输出\"无变化\"，否则输出完整新值；"
        "新事件为本章新增的关键事件一句话；新解锁为本章新解锁的模块名，没有则输出[]）：\n"
        '{"认知值": 78, "兵力": "无变化", "人物关系": "无变化", "时间": "无变化", '
        '"新事件": "击溃程远志", "新解锁": []}\n'
        "注意：系统面板会列出所有模块名（军事/科技/人际），但只有正文明确说「已解锁」「解锁成功」「可用了」的模块才记入新解锁；"
        "仅是面板列出、仍标注「未解锁」的模块不要记。\n"
        "只输出 JSON，不要其他文字。"
    )
    out = call_llm([{"role": "user", "content": prompt}], max_tokens=2000, temperature=0.2)
    if not out:
        return prev_state
    # 解析 JSON
    try:
        t = re.sub(r"```json\s*|```", "", out).strip()
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            upd = json.loads(t[i:j + 1])
        else:
            upd = json.loads(t)
    except Exception:
        return prev_state

    new_state = dict(prev_state)
    # 认知值
    try:
        cv = int(upd.get("认知值"))
        if 0 <= cv <= 100:
            new_state["认知值"] = cv
    except (TypeError, ValueError):
        pass
    # 覆盖式字段
    for field in ("兵力", "人物关系", "时间"):
        v = upd.get(field)
        if isinstance(v, str) and v.strip() and v.strip() != "无变化":
            new_state[field] = v.strip()
    # 新解锁
    nu = upd.get("新解锁") or []
    if isinstance(nu, str):
        nu = [nu] if nu.strip() else []
    for m in nu:
        if m and m not in new_state["已解锁"]:
            new_state["已解锁"].append(m)
    # 新事件
    ne = upd.get("新事件")
    if isinstance(ne, str) and ne.strip():
        new_state["关键事件"] = (new_state.get("关键事件", []) + [ne.strip()])[-6:]
    new_state["current_chapter"] = chapter_no
    return new_state


def main():
    detail = json.load(open(DETAIL_JSON, encoding="utf-8"))
    ch1 = open(CH1_BODY, encoding="utf-8").read().strip()
    chapters_by_no = {c["chapter"]: c for c in detail}

    state = load_state()
    start = state.get("current_chapter", 1) + 1
    end = 150
    if len(sys.argv) >= 3:
        start, end = int(sys.argv[1]), int(sys.argv[2])

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"从第{start}章续跑（state.current_chapter={state['current_chapter']}，认知值={state['认知值']}）")

    for n in range(start, end + 1):
        out_path = os.path.join(OUT_DIR, f"ch_{n:03d}.md")
        if n not in chapters_by_no:
            print(f"第{n}章: 细纲缺失，跳过")
            continue
        ch = chapters_by_no[n]

        # 上一章结尾（用于衔接）
        prev_ending = ""
        prev_path = os.path.join(OUT_DIR, f"ch_{n-1:03d}.md")
        if os.path.exists(prev_path):
            prev_ending = open(prev_path, encoding="utf-8").read().strip()[-150:]

        t0 = time.time()
        body = generate_body(ch, state, prev_ending, ch1)
        if not body:
            print(f"第{n}章: 生成失败，停止")
            break
        pure = re.sub(r"\s", "", body)

        # 状态提取
        state = extract_state(body, state, n, ch["title"])

        open(out_path, "w", encoding="utf-8").write(body)
        save_state(state)
        print(f"第{n}章《{ch['title']}》: {len(pure)}字 / {time.time()-t0:.0f}s / "
              f"认知值→{state['认知值']} 兵力[{state['兵力']}] 关系[{state['人物关系']}]")

    print(f"\n完成到第{state.get('current_chapter', start)}章。")


if __name__ == "__main__":
    main()
