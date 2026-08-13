#!/usr/bin/env python3
"""drama_phase2_characters.py — 角色设计图生成（正面肖像/正面全身/侧面全身/背面全身）"""

import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

CHARACTER_PROMPT = """为角色生成四视图设计。

角色信息：
- 名称：{name}
- 性别：{gender}
- 年龄：{age}
- 外貌：{appearance}
- 服装：{costume}
- 标志特征：{signature}
- 角色定位：{role}

## 四视图

### 1. 正面肖像（portrait）
全身肖像，正面，表情自然，摄影棚灯光，高清

### 2. 正面全身（front_full）
正面全身站立，展示完整服装，中性站姿，纯色背景

### 3. 侧面全身（side_full）
右侧面全身，展示轮廓和服装侧面，保持一致性

### 4. 背面全身（back_full）
背面全身，展示背面服装细节

## 一致性要求
- 四张图必须是同一个角色（长相、服装、体型一致）
- 使用相同的艺术风格
- 分辨率：{size}
- 风格：{style}

对每个视图输出一行英文 prompt（适合 AI 生图）：
portrait: <prompt>
front_full: <prompt>
side_full: <prompt>
back_full: <prompt>
"""

IMAGE_SYSTEM = "你是一个角色设计师。输出高质量的 AI 生图 prompt，英文。"

VIEW_MAP = {
    "portrait": "正面肖像，胸部以上，柔和摄影棚灯光",
    "front_full": "正面全身，站立中性姿态，纯白背景",
    "side_full": "右侧面全身，展示服装侧面线条",
    "back_full": "背面全身，展示服装背面细节"
}

def main():
    p = arg_parser("角色设计")
    p.add_argument("--manifest", required=True, help="资源清单路径")
    p.add_argument("--views", default="portrait,front_full,side_full,back_full")
    p.add_argument("--size", default="1024*1024")
    p.add_argument("--style", default="cinematic, photorealistic, studio lighting")
    args = p.parse_args()

    manifest = yaml.safe_load(read(args.manifest))
    characters = manifest.get("characters", [])
    views = [v.strip() for v in args.views.split(",")]
    output_dir = Path(args.workspace) / "05_assets" / "characters"

    for char in characters:
        name = char["name"]
        safe_name = name.replace(" ", "_").replace("/", "_")
        char_dir = output_dir / safe_name
        char_dir.mkdir(parents=True, exist_ok=True)
        print(f"🎨 {name} ({char.get('role', '未知')})")

        # Step 1: 生成 prompts
        prompt_text = CHARACTER_PROMPT.format(
            name=name, gender=char.get("gender",""), age=char.get("age_range",""),
            appearance=char.get("appearance",""), costume=char.get("costume",""),
            signature=char.get("signature",""), role=char.get("role",""),
            size=args.size, style=args.style
        )
        response = call_llm(prompt_text, IMAGE_SYSTEM, max_tokens=1024)
        write(str(char_dir / "design_prompt.md"), response)

        # Step 2: 解析 prompts 并生成图片
        prompts = {}
        for line in response.strip().split("\n"):
            for v in views:
                if line.lower().startswith(f"{v}:"):
                    prompts[v] = line.split(":", 1)[1].strip()

        for view, prompt in prompts.items():
            output_path = char_dir / f"{view}.jpg"
            if output_path.exists():
                print(f"  ⏭ {view} (已存在)")
                continue

            print(f"  🖼 {view}...")
            try:
                # 调用 image_generate API
                resp = requests.post(
                    f"{API_BASE}/images/generations",
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    json={"model": "flux", "prompt": prompt, "size": args.size, "n": 1},
                    timeout=120, verify=VERIFY
                )
                if resp.status_code == 200:
                    img_url = resp.json().get("data", [{}])[0].get("url", "")
                    if img_url:
                        download_image(img_url, str(output_path))
                        print(f"    ✅ {output_path}")
                else:
                    # 仅保存 prompt，后续手动生成
                    write(str(char_dir / f"{view}.txt"), prompt)
                    print(f"    ⚠️ 保存prompt (API: {resp.status_code})")
            except Exception as e:
                write(str(char_dir / f"{view}.txt"), prompt)
                print(f"    ⚠️ {e}")

        # 保存角色元数据
        meta = {k: char.get(k, "") for k in ["name","role","gender","age_range","traits","appearance","costume","signature"]}
        meta["views"] = views
        write(str(char_dir / "meta.json"), json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"🎉 Phase 2.2 完成 → {output_dir}")

def download_image(url, path):
    r = requests.get(url, timeout=60, verify=VERIFY)
    with open(path, "wb") as f:
        f.write(r.content)

if __name__ == "__main__":
    main()
