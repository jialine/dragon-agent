#!/usr/bin/env python3
"""drama_phase1_expand.py — 将小说/散文体剧本扩展为结构化拍摄脚本（≥120秒/集）"""

import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位资深影视编剧和分镜师。你的任务是将小说片段扩展为可直接拍摄的结构化剧本。
输出必须是合法的JSON数组，包含完整的场景描述、角色动作、对话和镜头指示。"""

EXPAND_PROMPT = """将以下小说片段扩展为可直接拍摄的结构化剧本。目标是 {target_duration} 秒视频内容。

输出 JSON 数组格式（不要 markdown 代码块）：

[
  {{
    "scene_id": "s01",
    "location": "场景位置描述（环境、光线、氛围）",
    "duration_secs": 8,
    "characters": ["角色名"],
    "camera": "镜头类型和运动（如：中景推进、特写仰拍）",
    "visual": "画面描述，英文，50-80词，适合AI视频生成",
    "dialogue": "中文对话（如有）",
    "dialogue_voice": "说话角色名（如有对话）",
    "sfx": "音效描述（如有）",
    "sfx_prompt": "英文音效 prompt",
    "emotion": "情绪/氛围关键词",
    "transition": "转场方式"
  }}
]

要求：
1. 总时长 ≥ {target_duration} 秒
2. 每个场景 5-12 秒
3. 尊重原著情节和角色设定
4. visual 字段用英文（AI视频生成需要），要详细描述人物动作、表情、环境
5. 对话保留原意，可适当扩展
6. 覆盖开端→发展→高潮→结尾

## 角色设定
{characters}

## 小说原文
{script}
"""


def expand_episode(script: str, characters: str, target_duration: int = 120) -> list:
    """扩展单集为结构化剧本"""
    import time as _time
    
    prompt = EXPAND_PROMPT.format(
        script=script, characters=characters, target_duration=target_duration
    )
    
    for attempt in range(3):
        try:
            response = call_llm(prompt, SYSTEM, max_tokens=8192, temperature=0.7)
            
            # Extract JSON
            json_text = response
            if "```json" in json_text:
                json_text = json_text.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in json_text:
                json_text = json_text.split("```", 1)[1].split("```", 1)[0]
            
            # Find array
            match = re.search(r"\[\s*\{.*\}\s*\]", json_text, re.DOTALL)
            if match:
                json_text = match.group(0)
            
            scenes = json.loads(json_text)
            if isinstance(scenes, list) and len(scenes) > 0:
                total_dur = sum(s.get("duration_secs", 0) for s in scenes)
                return scenes, total_dur
            print(f"  ⚠️ 第{attempt+1}次：解析成功但无场景")
        except Exception as e:
            print(f"  ⚠️ 第{attempt+1}次失败: {str(e)[:80]}")
            if attempt < 2:
                _time.sleep(2)
    
    raise RuntimeError("扩展失败（3次重试耗尽）")


def main():
    p = arg_parser("扩展剧本为结构化拍摄脚本")
    p.add_argument("--scripts", required=True)
    p.add_argument("--assets", default="")
    p.add_argument("--duration", type=int, default=120)
    p.add_argument("--episodes", type=int, default=5)
    args = p.parse_args()

    ws = Path(args.workspace)
    scripts_dir = Path(args.scripts)
    
    # 读取角色
    char_text = "未提供"
    if args.assets:
        asset_path = Path(args.assets) / "asset_manifest.yaml"
        if asset_path.exists():
            manifest = yaml.safe_load(read(str(asset_path)))
            char_text = json.dumps(manifest.get("characters", []), ensure_ascii=False, indent=2)
    
    # 处理每一集
    expanded_dir = ws / "03_expanded"
    expanded_dir.mkdir(parents=True, exist_ok=True)
    
    total_scenes = 0
    for ep_idx in range(1, args.episodes + 1):
        script_file = scripts_dir / f"episode_{ep_idx:02d}.md"
        if not script_file.exists():
            print(f"  ⚠️ episode_{ep_idx:02d}.md 不存在，跳过")
            continue
        
        script = read(str(script_file))
        print(f"\n📖 第{ep_idx}集 ({len(script)}字) → 展开为≥{args.duration}秒...")
        
        try:
            scenes, total_dur = expand_episode(script, char_text, args.duration)
            print(f"  ✅ {len(scenes)} 个场景 / {total_dur}秒")
            total_scenes += len(scenes)
            
            # 保存
            output = expanded_dir / f"episode_{ep_idx:02d}.json"
            write(str(output), json.dumps({
                "episode": ep_idx,
                "target_duration": args.duration,
                "actual_duration": total_dur,
                "scenes": scenes
            }, ensure_ascii=False, indent=2))
            print(f"  → {output}")
        except Exception as e:
            print(f"  ❌ 第{ep_idx}集失败: {e}")
    
    print(f"\n📊 总计 {total_scenes} 个场景")
    print(f"🎉 Phase 1.5 完成 → {expanded_dir}/")


if __name__ == "__main__":
    main()
