#!/usr/bin/env python3
"""drama_phase3_storyboard_from_expanded.py — 从扩展JSON直接生成分镜表（无需LLM）"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

# 模型选型规则
def classify_model(scene: dict) -> str:
    """根据人物判断模型类型"""
    chars = scene.get("characters", [])
    if chars and len(chars) > 0 and chars[0]:
        return "r2v"  # 有人物 → R2V
    return "t2v"      # 纯场景 → T2V

def get_ref_image(char_name: str, assets_dir: str) -> str:
    """查找角色参考图"""
    if not char_name:
        return ""
    # 检查 chars 目录
    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
        p = Path(assets_dir) / "chars" / f"{char_name}{ext}"
        if p.exists():
            return f"characters/{char_name}/portrait{ext}"
        # 也查中文名
    # 查旧目录
    for d in [Path(assets_dir) / "chars", Path(assets_dir) / "wan_chars"]:
        if d.exists():
            for f in d.iterdir():
                if char_name in f.stem:
                    return f"characters/{char_name}/{f.name}"
    return f"characters/{char_name}/portrait.jpg"

def main():
    p = arg_parser("从扩展剧本生成分镜表")
    p.add_argument("--expanded", required=True, help="扩展剧本目录 (03_expanded/)")
    p.add_argument("--assets", default="")
    p.add_argument("--episodes", type=int, default=5)
    args = p.parse_args()

    ws = Path(args.workspace)
    expanded_dir = Path(args.expanded)
    assets_dir = Path(args.assets) if args.assets else ws / "05_assets"

    # 读取扩展剧本
    all_shots = []
    ep_files = sorted(expanded_dir.glob("episode_*.json"))

    for ep_file in ep_files:
        data = json.loads(read(str(ep_file)))
        ep_idx = data["episode"]
        scenes = data.get("scenes", [])

        prev_shot_id = ""
        for si, scene in enumerate(scenes):
            sid = f"ep{ep_idx:02d}_s{si+1:02d}"
            model_type = classify_model(scene)
            chars = scene.get("characters", [])
            main_char = chars[0] if chars else ""

            shot = {
                "id": sid,
                "episode": ep_idx,
                "scene": scene.get("location", ""),
                "location": scene.get("location", ""),
                "characters": chars,
                "model": model_type,
                "ref_image": get_ref_image(main_char, str(assets_dir)) if main_char else "",
                "prev_frame": prev_shot_id if model_type == "i2v" else "",
                "prompt": scene.get("visual", scene.get("prompt", "")),
                "seed": 0,
                "duration_secs": scene.get("duration_secs", 6),
                "dialogue": scene.get("dialogue", ""),
                "dialogue_voice": scene.get("dialogue_voice", main_char),
                "sfx": scene.get("sfx", ""),
                "sfx_prompt": scene.get("sfx_prompt", ""),
                "camera": scene.get("camera", ""),
                "transition": scene.get("transition", "cut"),
                "notes": scene.get("emotion", ""),
            }
            all_shots.append(shot)
            prev_shot_id = sid

        print(f"  ✅ ep{ep_idx:02d}: {len(scenes)} 场景 → {sum(s.get('duration_secs',0) for s in scenes)}秒")

    # 种子分配
    storyboard_dir = ws / "06_storyboard"
    storyboard_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = ws / "07_shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    seed_registry = SeedRegistry(str(shots_dir / "seeds.yaml"))

    model_counts = {}
    for shot in all_shots:
        key = f"{shot.get('scene','')}_{','.join(sorted(shot.get('characters',[])))}"
        shot["seed"] = seed_registry.get(key)
        m = shot.get("model", "?")
        model_counts[m] = model_counts.get(m, 0) + 1
    seed_registry.save()

    # 保存
    output_yaml = storyboard_dir / "storyboard.yaml"
    write(str(output_yaml), yaml.dump({"shots": all_shots}, allow_unicode=True, default_flow_style=False))
    output_json = storyboard_dir / "storyboard.json"
    write(str(output_json), json.dumps({"shots": all_shots}, ensure_ascii=False, indent=2))

    total_dur = sum(s["duration_secs"] for s in all_shots)
    print(f"\n📊 总计 {len(all_shots)} 个镜头 / {total_dur}秒 | {model_counts}")
    print(f"  → {output_yaml}")
    print(f"🎉 Phase 3.1 完成")


if __name__ == "__main__":
    main()
