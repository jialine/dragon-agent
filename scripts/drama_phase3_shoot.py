#!/usr/bin/env python3
"""drama_phase3_shoot.py — 逐镜头生成（R2V/I2V/T2V + 种子管理 + 语音/音效同步）"""

import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

MODEL_MAP = {
    "r2v": "wan2.7-r2v",
    "i2v": "wan2.7-i2v",
    "t2v": "wan2.7-t2v",
}

def generate_audio(text: str, voice: str, output: str):
    """使用 Edge TTS 生成语音"""
    try:
        import subprocess
        voice_map = {"陈默": "zh-CN-YunxiNeural", "王芳": "zh-CN-XiaoxiaoNeural",
                     "李明": "zh-CN-YunyangNeural", "default": "zh-CN-XiaoxiaoNeural"}
        v = voice_map.get(voice, voice_map["default"])
        subprocess.run(["edge-tts", "--text", text, "--voice", v, "--write-media", output],
                       capture_output=True, timeout=30, check=True)
        return True
    except Exception as e:
        print(f"    ⚠️ TTS失败: {e}")
        return False

def main():
    p = arg_parser("分镜头生成")
    p.add_argument("--storyboard", required=True)
    p.add_argument("--audio", required=True, help="音频输出目录")
    p.add_argument("--aspect", default="16:9")
    args = p.parse_args()

    ws = Path(args.workspace)
    storyboard = yaml.safe_load(read(args.storyboard))
    shots = storyboard.get("shots", [])

    shots_dir = ws / "07_shots"
    audio_dir = Path(args.audio)
    shots_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    seed_registry = SeedRegistry(str(ws / "07_shots" / "seeds.yaml"))

    total = len(shots)
    success = 0
    failed = []

    for i, shot in enumerate(shots):
        sid = shot.get("id", f"shot_{i}")
        ep = shot.get("episode", 1)
        model_type = shot.get("model", "t2v")
        model = MODEL_MAP.get(model_type, "wan2.7-t2v")
        seed = shot.get("seed", seed_registry.get(f"{sid}"))

        # 确定输出路径
        ep_dir = shots_dir / f"ep{ep:02d}"
        ep_dir.mkdir(parents=True, exist_ok=True)
        video_path = ep_dir / f"{sid}.mp4"
        prompt_path = ep_dir / f"{sid}_prompt.md"

        if video_path.exists():
            print(f"  ⏭ [{i+1}/{total}] {sid} (已存在)")
            success += 1
            continue

        # 处理对话：先生成语音
        dialogue = shot.get("dialogue", "")
        voice = shot.get("dialogue_voice", "")
        if dialogue and voice:
            audio_path = audio_dir / f"ep{ep:02d}" / f"{sid}_dialogue.mp3"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"  🎙 [{i+1}/{total}] {sid} 配音: {voice}...")
            generate_audio(dialogue, voice, str(audio_path))
            shot["dialogue_audio"] = str(audio_path)

        # 构建 prompt
        prompt = shot.get("prompt", "")
        ref_image = shot.get("ref_image", "")
        prev_frame = shot.get("prev_frame", "")

        # I2V: 使用上一帧尾帧
        if model_type == "i2v" and prev_frame:
            prev_path = ep_dir / f"{prev_frame}_last.jpg"
            if prev_path.exists():
                ref_image = str(prev_path)

        print(f"  🎥 [{i+1}/{total}] {sid} ({model_type} seed={seed})")

        try:
            task_id = submit_video(
                prompt=prompt, model=model,
                ref_image=ref_image, seed=seed
            )
            print(f"    task: {task_id[:30]}...")

            # 轮询
            result = poll_video(task_id, max_wait=300, interval=15)
            if result.get("status") == "SUCCESS" and result.get("url"):
                download_video(result["url"], str(video_path))
                # 保存 prompt 记录
                record = f"# {sid}\n\nmodel: {model}\nseed: {seed}\nprompt: {prompt}\nref: {ref_image}\ntask: {task_id}\n"
                write(str(prompt_path), record)
                # 记录种子
                seed_registry.set(f"{sid}", seed)
                success += 1
                print(f"    ✅ [{success}/{total}]")
            else:
                failed.append({"id": sid, "error": str(result)})
                print(f"    ❌ {result.get('status')}")

        except Exception as e:
            failed.append({"id": sid, "error": str(e)})
            print(f"    ❌ {e}")

    seed_registry.save()

    # 汇总
    print(f"\n{'='*50}")
    print(f"✅ {success}/{total} 成功")
    if failed:
        print(f"❌ {len(failed)} 失败:")
        for f in failed:
            print(f"  - {f['id']}: {f['error']}")
    print(f"🎉 Phase 3.3 完成")

if __name__ == "__main__":
    main()
