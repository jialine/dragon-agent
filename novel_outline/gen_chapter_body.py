#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《三国求生指南》正文批量生成脚本
读 detail_outline_sample.json（150章节拍细纲）→ 按第1章样板风格 → 逐章扩写正文。

用法:
  python3 gen_chapter_body.py 2 3        # 只生成第2、3章
  python3 gen_chapter_body.py 2 50       # 生成第2~50章（断点续跑，已有文件跳过）
"""
import json
import os
import re
import sys
import time

import requests

# ============ 配置 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DETAIL_JSON = os.path.join(BASE_DIR, "detail_outline_sample.json")
CH1_BODY = os.path.join(BASE_DIR, "ch1_body.txt")
OUT_DIR = os.path.join(BASE_DIR, "chapters")
API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"
TARGET_WORDS = 3500          # 目标字数/章（网文标准短章 3000-4000）
MAX_TOKENS = 8192            # 输出上限，够 4000 字
ENV_FILE = "/home/jialine/dragon-agent/.env"

# 从 .env 读 key
def load_key():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
            if line.startswith("export DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DEEPSEEK_API_KEY not found in .env")

KEY = load_key()

SYSTEM_TEMPLATE = """你是资深网文写手，正在续写长篇历史穿越小说《三国求生指南》。

【世界观】主角王铁柱，26岁996程序员，加班猝死穿越到东汉中平元年（184年黄巾起义爆发年），绑定「现代知识系统」。金手指：现代知识检索（消耗认知值，认知值<60短期失忆、<30永久记忆损伤）、军事/科技/人际模块（后续解锁）。主角职业病：见什么都能用程序员/产品经理视角吐槽（UI、需求评审、KPI、AB测试、迭代、bug、解绑、灰度、上线、宕机）。

【文风铁律】
1. 短句快节奏，多分段，单段不超过5行，白描克制不堆砌形容词。
2. 对话占比约45%，对话自然有网感；主角内心独白密集、幽默。
3. 程序员梗全程贯穿，把历史事件套互联网黑话，但别生硬。
4. 系统面板用【】框，主角看到面板必先吐槽UI/交互/产品逻辑。
5. 每拍（起因/冲突/高潮/钩子）都有具体事件+信息增量，不注水、不空转、不重复前文。
6. 章末必须有强钩子（悬念/反转/金句），让人想点下一章。
7. 保持与历史主线（三国演义进程）一致，历史人物言行符合设定。

【字数】每章 {target} 字左右，写实不硬凑，宁短勿水。

【风格参照】以下是第1章正文，严格模仿其节奏、对话密度、梗的风格：

{ch1_body}"""


def call_llm(messages, max_tokens=MAX_TOKENS, retries=3):
    """调用 deepseek-v4-pro，带重试。返回 content 字符串。"""
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "enable_thinking": False,
                    "temperature": 0.8,
                },
                timeout=600,
                verify=False,
            )
            r.raise_for_status()
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            finish = choice.get("finish_reason")
            return content, finish
        except Exception as e:
            last_err = e
            print(f"  [重试 {attempt+1}/{retries}] {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")


def generate_chapter(ch, prev_ending, system_prompt):
    """生成单章正文，带截断续写。返回 (正文, 结尾)。"""
    beats = ch["beats"]
    user_prompt = f"""请写第{ch['chapter']}章《{ch['title']}》。

上一章结尾：{prev_ending}

本章节拍细纲（四拍，严格按此顺序展开，不要跳拍、不要新增无关支线）：
【起因】{beats['起因']}
【冲突】{beats['冲突']}
【高潮】{beats['高潮']}
【钩子】{beats['钩子']}

要求：完整写成 {TARGET_WORDS} 字左右的正文章节，章末停在钩子处留悬念。直接输出正文，不要任何解释、不要标题、不要"第X章完"标注。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    body, finish = call_llm(messages)
    # 截断续写
    guard = 0
    while finish == "length" and guard < 4:
        guard += 1
        cont_msg = [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": body},
            {"role": "user", "content": "（继续写，直到本章完整结束，章末停在钩子处）"},
        ]
        more, finish = call_llm(cont_msg)
        body += more

    body = body.strip()
    return body


def main():
    if len(sys.argv) < 3:
        print("用法: python3 gen_chapter_body.py <起始章> <结束章>")
        sys.exit(1)
    start, end = int(sys.argv[1]), int(sys.argv[2])

    detail = json.load(open(DETAIL_JSON, encoding="utf-8"))
    ch1 = open(CH1_BODY, encoding="utf-8").read().strip()
    # 第1章结尾（用于衔接第2章）
    prev_ending = ch1[-150:]

    system_prompt = SYSTEM_TEMPLATE.format(target=TARGET_WORDS, ch1_body=ch1)

    os.makedirs(OUT_DIR, exist_ok=True)

    # 按 chapter 序号索引
    chapters_by_no = {c["chapter"]: c for c in detail}
    if 1 not in chapters_by_no:
        print("警告: detail json 无第1章")
    # 第1章直接落盘（来自样板）
    ch1_out = os.path.join(OUT_DIR, "ch_001.md")
    if not os.path.exists(ch1_out):
        open(ch1_out, "w", encoding="utf-8").write(ch1)
        print(f"第1章: 已从样板落盘 ({len(ch1)}字)")

    total_words = 0
    for n in range(start, end + 1):
        out_path = os.path.join(OUT_DIR, f"ch_{n:03d}.md")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 500:
            print(f"第{n}章: 已存在，跳过")
            prev_ending = open(out_path, encoding="utf-8").read()[-150:]
            continue
        if n not in chapters_by_no:
            print(f"第{n}章: detail json 无此章，跳过")
            continue

        ch = chapters_by_no[n]
        t0 = time.time()
        try:
            body = generate_chapter(ch, prev_ending, system_prompt)
        except Exception as e:
            print(f"第{n}章: 失败 {e}")
            continue

        pure = re.sub(r"\s", "", body)
        open(out_path, "w", encoding="utf-8").write(body)
        total_words += len(pure)
        prev_ending = body[-150:]
        print(f"第{n}章《{ch['title']}》: 完成 {len(pure)}字 / {time.time()-t0:.0f}s")

    print(f"\n完成。本次新增 {total_words} 字。")


if __name__ == "__main__":
    main()
