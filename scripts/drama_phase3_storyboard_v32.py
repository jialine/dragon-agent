#!/usr/bin/env python3
"""drama_phase3_storyboard.py — 分镜表生成（逐集生成，JSON合并）"""

import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位资深分镜师和AI视频导演。输出必须是合法的JSON数组。
关键决策：
- R2V: 有主角/配角出现（需要角色参考图）
- I2V: 紧接上一镜头尾帧的延续镜头
- T2V: 纯场景/空镜/无人物
- 种子：同场景同角色用相同种子保证一致性"""

EPISODE_PROMPT = """为第{ep_idx}集生成完整分镜表。目标{target_shots}个镜头，总时长~{duration}秒。

输出纯JSON数组（不要markdown代码块，不要外层对象）：

[
  {{
    "id": "ep{ep_idx}_s01",
    "episode": {ep_idx},
    "scene": "场景名",
    "location": "具体位置描述",
    "characters": ["角色名"],
    "model": "r2v|i2v|t2v",
    "ref_image": "characters/角色名/portrait.jpg",
    "prev_frame": "",
    "prompt": "英文视频生成prompt，40-80词",
    "seed": 0,
    "duration_secs": 5,
    "dialogue": "中文对话文本",
    "dialogue_voice": "角色配音名",
    "sfx": "音效描述",
    "sfx_prompt": "英文音效prompt",
    "camera": "镜头描述",
    "transition": "cut|fade|dissolve",
    "notes": "情绪/叙事备注"
  }}
]

约束：
- 每集{target_shots}个镜头，首个镜头必须是R2V引入角色
- 模型选型：R2V(有人物)→ref_image必填, I2V(接上镜)→prev_frame指向前一镜ID, T2V(纯场景/空镜)
- dialogue填中文对话文本，空对话留空字符串
- 种子seed填0，脚本统一分配

## 本集剧本
{script}

## 角色表
{characters}

## 场景表
{scenes}
"""

def extract_json(text: str) -> str:
    """从LLM输出中提取JSON数组，含容错修复"""
    # 去掉markdown代码块
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
    
    # 找到JSON数组
    match = re.search(r"\[\s*\{.*", text, re.DOTALL)
    if match:
        text = match.group(0)
    
    # 修复常见LLM输出bug：双引号转义残留
    text = text.replace('""', '"')
    
    # 尝试找到最后一个完整的对象，截断不完整的尾部
    bracket_depth = 0
    in_string = False
    escape_next = False
    last_valid = 0
    
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[" or ch == "{":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
            if bracket_depth == 0:
                last_valid = i + 1
                break
        elif ch == "}":
            bracket_depth -= 1
            last_valid = i + 1
    
    if last_valid > 0 and last_valid < len(text):
        text = text[:last_valid]
        stripped = text.rstrip()
        if stripped.endswith(","):
            last_brace = stripped.rfind("}")
            if last_brace > 0:
                text = text[:last_brace + 1] + "\n]"
            else:
                text = stripped.rstrip(",").rstrip() + "\n]"
    
    # 确保以 ] 结尾
    text = text.rstrip()
    if not text.endswith("]"):
        text += "\n]"
    
    return text.strip()


def generate_episode(ep_idx: int, script: str, characters: str, scenes: str, 
                     duration: int, target_shots: int) -> list:
    """为单集生成分镜表"""
    import time as _time
    
    prompt = EPISODE_PROMPT.format(
        ep_idx=ep_idx, script=script, characters=characters, scenes=scenes,
        duration=duration, target_shots=target_shots
    )
    
    for attempt in range(3):
        try:
            response = call_llm(prompt, SYSTEM, max_tokens=16384, temperature=0.6)
            json_text = extract_json(response)
            shots = json.loads(json_text)
            if isinstance(shots, list) and len(shots) > 0:
                return shots
            print(f"  ⚠️ 第{attempt+1}次尝试：解析成功但无镜头数据")
        except Exception as e:
            print(f"  ⚠️ 第{attempt+1}次尝试失败: {e}")
            if attempt < 2:
                _time.sleep(2)
    
    raise RuntimeError(f"第{ep_idx}集分镜表生成失败（3次重试耗尽）")


def main():
    p = arg_parser("生成分镜表")
    p.add_argument("--scripts", required=True)
    p.add_argument("--assets", required=True)
    p.add_argument("--episodes", type=int, required=True)
    p.add_argument("--aspect", default="16:9")
    p.add_argument("--duration", type=int, default=120)
    args = p.parse_args()

    ws = Path(args.workspace)
    scripts_dir = Path(args.scripts)
    assets_dir = Path(args.assets)

    # 收集剧本（按集分割）
    ep_scripts = {}
    for f in sorted(scripts_dir.glob("*.md")):
        content = read(str(f))
        ep_match = re.search(r"[eE][pP]?\s*(\d+)", f.stem)
        if ep_match:
            ep_idx = int(ep_match.group(1))
            ep_scripts[ep_idx] = content
        else:
            for m in re.finditer(r"#+\s*第\s*(\d+)\s*[集幕]", content):
                ep_scripts[int(m.group(1))] = content
                break
    
    if not ep_scripts:
        all_text = "\n\n".join(read(str(f)) for f in sorted(scripts_dir.glob("*.md")))
        ep_scripts = {1: all_text}

    # 资产
    manifest = yaml.safe_load(read(str(assets_dir / "asset_manifest.yaml")))
    char_text = json.dumps(manifest.get("characters", []), ensure_ascii=False, indent=2)
    scene_text = json.dumps(manifest.get("scenes", []), ensure_ascii=False, indent=2)

    # 逐集生成
    per_ep_duration = max(30, args.duration // args.episodes)
    per_ep_shots = max(10, 120 // args.episodes)

    all_shots = []
    for ep_idx in range(1, args.episodes + 1):
        script = ep_scripts.get(ep_idx, "")
        if not script:
            print(f"  ⚠️ 第{ep_idx}集无剧本，跳过")
            continue
        
        print(f"🎬 第{ep_idx}集分镜 ({per_ep_shots}镜 x ~{per_ep_duration}秒)...")
        shots = generate_episode(ep_idx, script, char_text, scene_text, 
                                 per_ep_duration, per_ep_shots)
        print(f"  ✅ {len(shots)} 个镜头")
        all_shots.extend(shots)

    if not all_shots:
        raise RuntimeError("未生成任何镜头")

    # 种子分配
    storyboard_dir = ws / "06_storyboard"
    storyboard_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = ws / "07_shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    seed_registry = SeedRegistry(str(shots_dir / "seeds.yaml"))

    model_counts = {}
    for shot in all_shots:
        key = f"{shot.get('scene','')}_{','.join(sorted(shot.get('characters',[])))}"
        if not shot.get("seed") or shot["seed"] == 0:
            shot["seed"] = seed_registry.get(key)
        m = shot.get("model", "?")
        model_counts[m] = model_counts.get(m, 0) + 1
    seed_registry.save()

    # 保存 YAML + JSON
    output_yaml = storyboard_dir / "storyboard.yaml"
    write(str(output_yaml), yaml.dump({"shots": all_shots}, allow_unicode=True, default_flow_style=False))
    
    output_json = storyboard_dir / "storyboard.json"
    write(str(output_json), json.dumps({"shots": all_shots}, ensure_ascii=False, indent=2))

    print(f"\n📊 总计 {len(all_shots)} 个镜头 | {model_counts}")
    print(f"  → {output_yaml}")
    print(f"  → {output_json}")
    print(f"🎉 Phase 3.1 完成")


if __name__ == "__main__":
    main()
