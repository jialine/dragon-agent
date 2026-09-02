#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""猩火 Ember 第一季 EP09-50 每集大纲生成器

从 10 万字完整剧本(猩火_完整剧本.txt)按集分割, 逐批调用 deepseek-v4-pro
生成每集大纲(中文梗概 + 关键场景 + 英文对白 + 分镜要点), 服务后续 H3 批量分镜。

用法:
  python3 gen_ep_outline.py                 # 全部 7 批
  python3 gen_ep_outline.py --batch 0       # 只跑第 0 批
  python3 gen_ep_outline.py --batch 0 --only 9 10 11   # 指定集号

输入: /tmp/episodes/EP{XX}.txt
输出: /tmp/outline_parts/batch_{i}.json  ->  汇总 /tmp/ember_s1_ep09_50_outline.json
"""
import json, os, re, subprocess, sys, time, argparse

API_URL = "https://api.andlapi.cn/v1/chat/completions"
MODEL = "deepseek-v4-pro"
EP_DIR = "/tmp/episodes"
OUT_DIR = "/tmp/outline_parts"
os.makedirs(OUT_DIR, exist_ok=True)

# key 从 /tmp/.andlapi_key 读取 (从 .100 同步而来)
_key = None
for line in open("/tmp/.andlapi_key"):
    if line.strip().startswith("DEEPSEEK_API_KEY"):
        _key = line.split("=", 1)[1].strip()
        break
if not _key:
    _key = os.environ.get("DEEPSEEK_API_KEY", "")
assert _key, "缺少 API key"

# 7 大板块定义 (用户定稿)
BOARDS = """1. 白宫陷落(White House Falls) —— 华盛顿特区, 白宫/国会, 鹰国总统与高层溃败
2. 奇袭费城(Philadelphia Raid) —— 费城突袭, 约翰·亚当斯、兄弟之城
3. 美洲崩溃(Americas Collapse) —— 纽约、波士顿、亚特兰大、新奥尔良等城市依次沦陷, 大陆秩序瓦解
4. 洛杉矶与旧金山的反抗(LA & SF Resistance) —— 西海岸困守, 金门大桥, 生还者的反击
5. 偷袭珍珠港(Pearl Harbor) —— 昆仑舰队东征, 跨太平洋进攻珍珠港, 全球直播
6. 覆灭英法联合舰队(Anglo-French Fleet Destroyed) —— 大西洋、英吉利海峡, 英法联军舰队覆灭
7. 伦敦上空的鹰(Eagle over London) —— 伦敦空战, 泰晤士河, 大本钟, 伦敦倒计时"""

SYSTEM = """你是资深短剧编剧, 负责把剧本逐集压缩成"分镜前大纲"。铁律:
1. 绝对忠于原文——只提炼剧本里已有的剧情、角色、对白; 禁止编造剧本没有的情节/角色/台词。
2. 英文对白必须口语化、精简、有力(短句, 像真正的电影对白), 可直接用于配音。剧本是中文, 你负责翻译成地道的英文对白, 不直译、去掉停顿和结巴。
3. 中文梗概 180-250 字, 讲清本集"发生了什么、谁、在哪、结果如何、悬念是什么"。
4. 严格输出合法 JSON, 不要输出任何 JSON 之外的文字或注释。"""

EPISODE_SET = list(range(9, 51))  # 42 集

def curl_llm(user, max_tokens=6000, temperature=0.4, retries=4):
    payload = {"model": MODEL,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature,
               "enable_thinking": False}
    for a in range(1, retries + 1):
        try:
            r = subprocess.run(["curl", "-s", "-k", "--max-time", "300", API_URL,
                                "-H", "Authorization: Bearer " + _key,
                                "-H", "Content-Type: application/json",
                                "-d", json.dumps(payload, ensure_ascii=False)],
                               capture_output=True, text=True, timeout=310)
            resp = json.loads(r.stdout)
            if "error" in resp:
                print(f"  [API错误] {resp['error']}", flush=True)
                time.sleep(a * 5)
                continue
            return resp["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  [重试{a}] {e}", flush=True)
            time.sleep(a * 5)
    return None

def extract_json(text):
    """从 LLM 输出里抠出 JSON (容忍 markdown 代码块包裹)。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)

def one_batch(eps, batch_idx):
    """生成一批 (多集) 大纲。"""
    # 组装 user prompt
    parts = ["请为以下剧集, 逐集生成大纲。7 大板块定义如下:\n" + BOARDS + "\n",
             "每集输出一个 JSON 对象, 字段: episode(数字), board(板块名, 从7大板块中选最贴切的一个), "
             "title_cn, title_en, summary_cn(中文梗概180-250字), "
             "key_scenes(数组, 每项 {\"scene\":场景名,\"event\":一句话事件}, 3-6个), "
             "dialogue_en(数组, 形如 \"角色名: 英文对白\", 3-6条关键对白), "
             "visual_notes(短句, 视觉风格+关键镜头提示)。\n"
             "输出一个 JSON 数组, 按集号升序。\n\n"]
    for ep in eps:
        fn = os.path.join(EP_DIR, f"EP{ep:02d}.txt")
        if not os.path.exists(fn):
            print(f"  [跳过] {fn} 不存在", flush=True)
            continue
        text = open(fn, encoding="utf-8").read()
        parts.append(f"===== 第{ep}集 剧本原文 =====\n{text}\n\n")

    user = "\n".join(parts)
    out = curl_llm(user)
    if not out:
        print(f"  batch {batch_idx} 失败", flush=True)
        return None
    try:
        data = extract_json(out)
    except Exception as e:
        print(f"  batch {batch_idx} JSON 解析失败: {e}", flush=True)
        open(os.path.join(OUT_DIR, f"batch_{batch_idx}_raw.txt"), "w").write(out)
        return None
    fn = os.path.join(OUT_DIR, f"batch_{batch_idx}.json")
    json.dump(data, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  batch {batch_idx} 完成: {len(data)} 集 -> {fn}", flush=True)
    return data

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=-1, help="只跑指定批次 (0-6)")
    ap.add_argument("--only", type=int, nargs="*", help="只跑指定集号")
    ap.add_argument("--size", type=int, default=6, help="每批集数")
    args = ap.parse_args()

    if args.only:
        eps = args.only
        batches = [(-1, eps)]
    else:
        batches = [(i, EPISODE_SET[i * args.size:(i + 1) * args.size])
                   for i in range((len(EPISODE_SET) + args.size - 1) // args.size)]

    all_out = []
    for idx, eps in batches:
        if args.batch >= 0 and idx != args.batch:
            continue
        print(f"=== batch {idx}: EP{min(eps):02d}-EP{max(eps):02d} ({len(eps)}集) ===", flush=True)
        data = one_batch(eps, idx)
        if data:
            all_out.extend(data)
        time.sleep(2)

    # 汇总
    if all_out:
        all_out.sort(key=lambda x: x.get("episode", 0))
        out_fn = "/tmp/ember_s1_ep09_50_outline.json"
        json.dump(all_out, open(out_fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n=== 汇总完成: {len(all_out)} 集 -> {out_fn} ===", flush=True)

if __name__ == "__main__":
    main()