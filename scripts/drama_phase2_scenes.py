#!/usr/bin/env python3
"""drama_phase2_scenes.py — 场景画面生成"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SCENE_PROMPT = """为以下场景生成画面描述和 AI 生图 prompt。

场景信息：
- 名称：{name}
- 地点：{location}
- 时间：{time}
- 氛围：{mood}
- 光线：{lighting}
- 相关剧情摘要：{context}

输出：
description: （中文场景画面描述，50字）
prompt: （英文 AI 生图 prompt，包含构图、光线、色调、氛围关键词）
"""

def main():
    p = arg_parser("场景画面设计")
    p.add_argument("--manifest", required=True)
    p.add_argument("--scripts", required=True)
    args = p.parse_args()

    manifest = yaml.safe_load(read(args.manifest))
    scenes = manifest.get("scenes", [])

    # 读取剧本获取上下文
    scripts_text = ""
    for f in sorted(Path(args.scripts).glob("*.md")):
        scripts_text += read(str(f))

    output_dir = Path(args.workspace) / "05_assets" / "scenes"
    output_dir.mkdir(parents=True, exist_ok=True)

    for scene in scenes:
        sid = scene.get("id", f"scene_{len(os.listdir(str(output_dir)))}")
        print(f"🏞 {sid}: {scene.get('name', '未知')}")

        context = scripts_text[:3000]  # 用剧本前3000字作为上下文

        prompt_text = SCENE_PROMPT.format(
            name=scene.get("name",""), location=scene.get("location",""),
            time=scene.get("time",""), mood=scene.get("mood",""),
            lighting=scene.get("lighting",""), context=context
        )

        resp = call_llm(prompt_text, "你是一个影视美术指导。", max_tokens=512)
        write(str(output_dir / f"{sid}.md"), resp)

        # 提取 prompt 并生成图
        eng_prompt = ""
        for line in resp.split("\n"):
            if line.lower().startswith("prompt:"):
                eng_prompt = line.split(":", 1)[1].strip()
                break

        if eng_prompt:
            try:
                img_resp = requests.post(
                    f"{API_BASE}/images/generations",
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    json={"model": "flux", "prompt": eng_prompt, "size": "1280*720", "n": 1},
                    timeout=120, verify=VERIFY
                )
                if img_resp.status_code == 200:
                    url = img_resp.json().get("data", [{}])[0].get("url", "")
                    if url:
                        download_image(url, str(output_dir / f"{sid}.jpg"))
                        print(f"  ✅ {sid}.jpg")
            except Exception as e:
                print(f"  ⚠️ {e}")

    print(f"🎉 Phase 2.3 完成 → {output_dir}")

def download_image(url, path):
    import requests as req
    r = req.get(url, timeout=60, verify=VERIFY)
    with open(path, "wb") as f:
        f.write(r.content)

if __name__ == "__main__":
    main()
