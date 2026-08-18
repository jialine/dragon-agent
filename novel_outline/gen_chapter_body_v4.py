#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《三国求生指南》正文生成脚本 v4（状态预演版，L2 升级）
在 v3（生命周期管理）基础上新增「状态预演」：
  1. 查库 → 组装硬约束
  2. 状态预演 → 规划本章认知值/人物登场/兵力/时间变化
  3. 注入规划 → 生成正文（严格按规划写）
  4. 提取变化 → 校验（生命周期 + 历史 + 偏离规划）
  5. 违规重写 → 回写数据库

用法:
  python3 gen_chapter_body_v4.py init
  python3 gen_chapter_body_v4.py 2 150
"""
import json
import os
import re
import sys
import time

import requests

from lifecycle import LifecycleManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DETAIL_JSON = os.path.join(BASE_DIR, "detail_outline_sample.json")
CH1_BODY = os.path.join(BASE_DIR, "ch1_body.txt")
OUT_DIR = os.path.join(BASE_DIR, "chapters")
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
    raise RuntimeError("no key")


KEY = load_key()


def call_llm(messages, max_tokens=MAX_TOKENS, temperature=0.8, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages,
                      "max_tokens": max_tokens, "enable_thinking": False,
                      "temperature": temperature},
                timeout=600, verify=False)
            r.raise_for_status()
            c = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if c:
                return c
        except Exception as e:
            print(f"  [LLM重试 {attempt+1}/{retries}] {e}")
            time.sleep(5 * (attempt + 1))
    return None


def extract_json(text):
    t = re.sub(r"```json\s*|```", "", text).strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i:j + 1])
    return json.loads(t)


SYSTEM_HEAD = """你是资深网文写手，正在续写长篇历史穿越小说《三国求生指南》。

【世界观】主角王铁柱，26岁996程序员，加班猝死穿越到东汉中平元年（184年黄巾起义爆发年），绑定「现代知识系统」。金手指：现代知识检索（消耗认知值，认知值<60短期失忆、<30永久记忆损伤）、军事/科技/人际模块（后续解锁）。主角职业病：见什么都能用程序员/产品经理视角吐槽。

【文风铁律】
1. 短句快节奏，多分段，单段不超过5行，白描克制。
2. 对话占比约45%，主角内心独白密集、幽默。
3. 程序员梗全程贯穿，把历史事件套互联网黑话，但别生硬。
4. 系统面板用【】框，主角看到面板必先吐槽UI/交互/产品逻辑。
5. 每拍都有具体事件+信息增量，不注水、不空转、不重复。
6. 章末必须有强钩子，让人想点下一章。
7. 历史人物言行/职位/坐骑/兵器符合史实。
8. 第三人称叙述，全程不要切换第一人称。
9. 认知值变化必须明确标注【认知值：X → Y】，且 X、Y 必须与状态规划一致。

【历史设定硬约束（严禁违背）】
- 刘焉：幽州太守（演义设定），不许写成涿郡太守/宗正/益州牧。
- 刘备：28岁（演义设定），年龄全程统一。
- 关羽：河东解良人，杀人亡命，卖枣为生，184年尚未成名。
- 张飞：涿县屠户，家资殷实。
- 卢植：北中郎将，刘备的老师。
- 皇甫嵩：左中郎将；朱儁：右中郎将。
- 张角：太平道领袖，184年病死于广宗（广宗之战前）。
- 董卓：河东太守（184年），尚未入京，189年才入京、190年才被刺。
- 曹操：29岁（155年生），骑都尉，讨黄巾。
- 赤兔马：董卓→吕布（约190年），184年关羽绝不可能骑。
- 周仓：184年尚未登场，严禁提前出现。
- 七星刀：刺董（约189年）时才出现，184年没有。

【字数】每章 {target} 字左右，写实不硬凑。

【风格参照】以下是第1章正文，严格模仿其节奏、对话密度、梗的风格：

{ch1_body}"""


def plan_block(plan):
    """把状态规划格式化成硬约束片段。"""
    pv = plan.get("认知值", {})
    cog = f"{pv.get('from','?')}→{pv.get('to','?')}（{pv.get('reason','')}）" if pv else "无变化"
    appear = "、".join(plan.get("新登场人物", [])) or "无"
    dead = "、".join(plan.get("死亡人物", [])) or "无"
    return (
        "【本章状态规划（正文必须严格符合，不得偏离）】\n"
        f"- 认知值变化：{cog}\n"
        f"- 新登场人物：{appear}\n"
        f"- 死亡人物：{dead}\n"
        f"- 兵力变化：{plan.get('兵力变化','无')}\n"
        f"- 时间推进：{plan.get('时间推进','无变化')}\n"
        f"- 解锁模块：{'、'.join(plan.get('解锁模块',[])) or '无'}\n"
    )


def generate_body(ch, context, plan, prev_ending, ch1_body, extra_errors=None):
    beats = ch["beats"]
    system_prompt = SYSTEM_HEAD.format(target=TARGET_WORDS, ch1_body=ch1_body)
    err_text = ""
    if extra_errors:
        err_text = ("\n\n【上一版违反的硬约束，必须修正】\n" + "\n".join(f"- {e}" for e in extra_errors))
    user_prompt = (
        "以下是当前世界的既成事实（硬约束），正文严禁违背：\n\n"
        + (context or "（暂无）")
        + "\n\n" + plan_block(plan)
        + err_text
        + f"\n\n请写第{ch['chapter']}章《{ch['title']}》。\n\n"
        + f"上一章结尾：{prev_ending}\n\n"
        + "本章节拍细纲（四拍，严格按此顺序展开，不跳拍、不加无关支线）：\n"
        + f"【起因】{beats['起因']}\n【冲突】{beats['冲突']}\n【高潮】{beats['高潮']}\n【钩子】{beats['钩子']}\n\n"
        + "【细纲硬约束】本章只上演上述细纲里的事件，严禁新增细纲未提及的人物/地点/事件，"
          "严禁提前上演后续章节剧情。\n\n"
        + f"要求：完整写成 {TARGET_WORDS} 字左右正文。认知值、人物登场/死亡、兵力、时间"
          "必须与【本章状态规划】完全一致。章末停在钩子处留悬念。直接输出正文，不要标题、解释、「第X章完」标注。"
    )
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    body = call_llm(messages, temperature=0.8) or ""
    guard = 0
    while len(re.sub(r"\s", "", body)) < 2800 and guard < 3:
        guard += 1
        more = call_llm([{"role": "system", "content": system_prompt},
                         {"role": "assistant", "content": body},
                         {"role": "user", "content": "（继续写，直到本章完整结束，章末停在钩子处）"}],
                        temperature=0.8)
        if not more:
            break
        body += more
    return body.strip()


def seed(lm):
    lm.upsert_person("王铁柱", status="活跃", gender="男", age=26,
                     title="穿越者/天机阁传人", location="涿郡城外破庙附近",
                     relation_to_protagonist="本人", first_appear=1, last_seen=1,
                     traits="程序员，职业病，见什么都用互联网黑话吐槽")
    lm.set_value("认知值", 92, unit="点",
                 rule="检索消耗8-10点/次；<60短期失忆；<30永久记忆损伤；自然恢复1点/天；重大事件有奖励", chapter=1)
    lm.set_timeline(1, "中平元年（184年）二月", "涿郡城外破庙")
    lm.add_event(1, "王铁柱猝死穿越到184年破庙，绑定现代知识系统", "王铁柱")
    lm.add_event(1, "检索黄巾起义，认知值100→92", "王铁柱")
    lm.upsert_faction("黄巾军", leader="张角", location="冀州等地", status="壮大")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        lm = LifecycleManager()
        lm.init_db()
        seed(lm)
        lm.close()
        print("✅ 初始化完成")
        return

    detail = json.load(open(DETAIL_JSON, encoding="utf-8"))
    ch1 = open(CH1_BODY, encoding="utf-8").read().strip()
    chapters_by_no = {c["chapter"]: c for c in detail}

    lm = LifecycleManager()
    if not os.path.exists(lm.db):
        lm.init_db()
        seed(lm)

    start, end = 2, 150
    if len(sys.argv) >= 3:
        start, end = int(sys.argv[1]), int(sys.argv[2])

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"从第{start}章开始（v4 状态预演版）")

    for n in range(start, end + 1):
        out_path = os.path.join(OUT_DIR, f"ch_{n:03d}.md")
        if n not in chapters_by_no:
            continue
        ch = chapters_by_no[n]

        prev_ending = ""
        prev_path = os.path.join(OUT_DIR, f"ch_{n-1:03d}.md")
        if os.path.exists(prev_path):
            prev_ending = open(prev_path, encoding="utf-8").read().strip()[-150:]

        context = lm.get_context(n)
        beats_text = f"起因：{ch['beats']['起因']}\n冲突：{ch['beats']['冲突']}\n高潮：{ch['beats']['高潮']}\n钩子：{ch['beats']['钩子']}"

        # 状态预演
        plan_raw = call_llm([{"role": "user", "content": lm.build_plan_prompt(n, beats_text, context)}],
                            max_tokens=2000, temperature=0.2)
        plan = extract_json(plan_raw) if plan_raw else {}

        t0 = time.time()
        body = generate_body(ch, context, plan, prev_ending, ch1)
        changes = {}
        errors = ["生成失败"]
        for attempt in range(3):
            if not body:
                break
            raw = call_llm([{"role": "user", "content": lm.build_extract_prompt(n, body, context)}],
                           max_tokens=3000, temperature=0.2)
            changes = extract_json(raw) if raw else {}
            errors = (lm.validate(changes, n)
                      + lm.validate_history(changes, n)
                      + lm.validate_plan(plan, changes, n))
            if not errors:
                break
            print(f"  第{n}章 第{attempt+1}次违反: {'; '.join(errors[:3])}")
            body = generate_body(ch, context, plan, prev_ending, ch1, extra_errors=errors)

        if errors:
            print(f"第{n}章: 3次重写仍失败，跳过（{'; '.join(errors[:3])}）")
            continue

        lm.apply_changes(changes, n)
        pure = re.sub(r"\s", "", body)
        open(out_path, "w", encoding="utf-8").write(body)
        cog = plan.get("认知值", {}).get("to", "?")
        print(f"第{n}章《{ch['title']}》: {len(pure)}字 / {time.time()-t0:.0f}s / 认知值→{cog}")

    lm.close()
    print(f"\n完成到第{end}章。")


if __name__ == "__main__":
    main()
