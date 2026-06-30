#!/usr/bin/env python3
"""
短剧全自动生产线 — 编剧 + 导演 + 摄像
========================================
输入：一句话主题
输出：720×1280 竖屏 MP4 完整短剧
"""
import asyncio, json, sys, os, re, uuid, time, tempfile, subprocess, logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("drama_director")

# ============================================================
# 配置
# ============================================================
LLM_BASE_URL = "https://api2.sangyuye.com/v1"
LLM_MODEL = "Qwen3.5-122B-A10B"
COMFYUI_HOST = "http://192.168.0.30:8188"
OUTPUT_DIR = Path("/tmp/drama_output")
OUTPUT_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """你是一位经验丰富的短剧导演兼编剧。你需要创作一部完整的竖屏短剧。

你必须输出以下 JSON 格式（只输出 JSON，不要任何其他内容）：
{
  "title": "剧名（吸引人）",
  "genre": "类型",
  "logline": "一句话梗概（30字内）",
  "total_duration_sec": 预估总时长秒数（60-180）,
  "shots": [
    {
      "id": 1,
      "scene": "场景描述",
      "duration_sec": 时长秒数（3-8）,
      "camera": "镜头描述（特写/中景/远景/跟拍/推拉）",
      "dialogue": "台词（角色名：台词内容）或空字符串",
      "visual_prompt": "英文AI视频生成提示词（描述画面、光线、氛围、动作，适合AnimateDiff）",
      "negative_prompt": "负面提示词",
      "transition": "转场方式（cut/fade/dissolve）"
    }
  ]
}

创作要求：
1. 前3秒必须有钩子，抓住观众
2. 每句台词不超过15字，符合短视频节奏
3. visual_prompt 必须详细描述画面和动作，英文
4. 镜头要有变化：特写→中景→远景循环
5. 包含至少一个反转/高潮
6. 结尾留悬念或余韵"""


@dataclass
class Shot:
    id: int
    scene: str
    duration_sec: float
    camera: str
    dialogue: str
    visual_prompt: str
    negative_prompt: str
    transition: str = "cut"
    video_path: Optional[str] = None


@dataclass
class DramaScript:
    title: str
    genre: str
    logline: str
    total_duration_sec: int
    shots: List[Shot] = field(default_factory=list)


# ============================================================
# Phase 1: 编剧 — LLM 生成剧本+分镜
# ============================================================
async def write_script(topic: str) -> DramaScript:
    """编剧：输入主题，输出完整分镜剧本"""
    import aiohttp

    prompt = f"""根据以下主题创作一部竖屏短剧：

主题：{topic}

要求：
- 竖屏 9:16 格式
- 总时长 60-120 秒
- 每镜头 3-8 秒
- 强钩子开头，快节奏
- 至少一个反转"""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.8,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"}
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{LLM_BASE_URL}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            data = await resp.json()
            content = data["choices"][0]["message"]["content"]
    
    # Parse JSON
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r'```\w*\n?', '', content).strip()
    
    script_data = json.loads(content)
    
    shots = []
    for s in script_data.get("shots", []):
        shots.append(Shot(
            id=s["id"],
            scene=s.get("scene", ""),
            duration_sec=float(s.get("duration_sec", 5)),
            camera=s.get("camera", ""),
            dialogue=s.get("dialogue", ""),
            visual_prompt=s.get("visual_prompt", ""),
            negative_prompt=s.get("negative_prompt", "blurry, low quality, distorted, bad anatomy"),
            transition=s.get("transition", "cut"),
        ))
    
    drama = DramaScript(
        title=script_data["title"],
        genre=script_data.get("genre", "短剧"),
        logline=script_data.get("logline", ""),
        total_duration_sec=script_data.get("total_duration_sec", 90),
        shots=shots,
    )
    
    logger.info(f"剧本完成：《{drama.title}》{len(shots)}镜/{drama.total_duration_sec}秒")
    return drama


# ============================================================
# Phase 2: 摄像 — AnimateDiff 生成视频片段
# ============================================================
async def generate_shot_video(shot: Shot, shot_index: int) -> str:
    """为单个镜头生成视频"""
    import aiohttp

    width, height = 360, 640  # 竖屏 9:16（先用半分辨率跑通，后续升 720p）
    frames = int(shot.duration_sec * 8)  # 8fps
    client_id = str(uuid.uuid4())

    # AnimateDiff Evolved Gen1 workflow (VRAM optimized with context_options)
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}
        },
        "2": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": frames}
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": shot.visual_prompt, "clip": ["1", 1]}
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": shot.negative_prompt, "clip": ["1", 1]}
        },
        "5": {
            "class_type": "ADE_StandardUniformContextOptions",
            "inputs": {
                "context_length": 16,
                "context_stride": 1,
                "context_overlap": 4,
                "fuse_method": "pyramid",
                "use_on_equal_length": False,
                "start_percent": 0.0,
                "guarantee_steps": 1,
                "prev_context": "standard",
                "view_opts": "standard"
            }
        },
        "6": {
            "class_type": "ADE_AnimateDiffLoaderGen1",
            "inputs": {
                "model": ["1", 0],
                "model_name": "mm_sd_v15_v2.ckpt",
                "beta_schedule": "sqrt_linear (AnimateDiff)",
                "context_options": ["5", 0]
            }
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42 + shot_index,
                "steps": 20,
                "cfg": 7.5,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 0.8,
                "model": ["6", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["2", 0]
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["1", 2]}
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": f"shot_{shot.id:03d}"
            }
        }
    }

    output_file = str(OUTPUT_DIR / f"shot_{shot.id:03d}.mp4")
    frames_dir = OUTPUT_DIR / f"frames_{shot.id:03d}"
    frames_dir.mkdir(exist_ok=True)
    
    async with aiohttp.ClientSession() as session:
        # Submit
        async with session.post(
            f"{COMFYUI_HOST}/api/prompt",
            json={"prompt": workflow, "client_id": client_id}
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"Shot {shot.id}: ComfyUI error {resp.status}: {body[:300]}")
                return None
            data = await resp.json()
            prompt_id = data.get("prompt_id")
        
        logger.info(f"Shot {shot.id}: 生成中 (prompt={prompt_id[:8]}... {frames} frames)")
        
        # Poll (up to 10 min)
        for i in range(120):
            await asyncio.sleep(5)
            async with session.get(f"{COMFYUI_HOST}/api/history/{prompt_id}") as hr:
                if hr.status != 200:
                    continue
                history = await hr.json()
                if prompt_id not in history:
                    continue
                h = history[prompt_id]
                status = h.get("status", {}).get("status_str", "")
                
                if status == "success":
                    outputs = h.get("outputs", {})
                    # Find SaveImage output
                    for node_id, node_out in outputs.items():
                        images = node_out.get("images", [])
                        if images:
                            logger.info(f"Shot {shot.id}: 下载 {len(images)} 帧...")
                            # Download all frames
                            frame_paths = []
                            for idx, img_info in enumerate(images):
                                src = f"{COMFYUI_HOST}/api/view?filename={img_info['filename']}&subfolder={img_info.get('subfolder','')}&type=output"
                                frame_path = str(frames_dir / f"frame_{idx:05d}.png")
                                async with session.get(src) as img_resp:
                                    if img_resp.status == 200:
                                        content = await img_resp.read()
                                        with open(frame_path, "wb") as f:
                                            f.write(content)
                                        frame_paths.append(frame_path)
                            
                            if frame_paths:
                                # Combine frames into MP4 with FFmpeg
                                import imageio_ffmpeg
                                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                                result = subprocess.run([
                                    ffmpeg, "-y",
                                    "-framerate", "8",
                                    "-i", str(frames_dir / "frame_%05d.png"),
                                    "-c:v", "libx264", "-preset", "fast",
                                    "-pix_fmt", "yuv420p",
                                    output_file
                                ], capture_output=True, text=True, timeout=60)
                                
                                if result.returncode == 0:
                                    file_size = os.path.getsize(output_file)
                                    logger.info(f"Shot {shot.id}: ✓ {file_size/1024:.0f}KB ({len(frame_paths)} frames)")
                                    shot.video_path = output_file
                                    return output_file
                                else:
                                    logger.error(f"Shot {shot.id}: FFmpeg failed: {result.stderr[-200:]}")
                    
                    logger.warning(f"Shot {shot.id}: 无输出帧")
                    return None
                
                elif status == "error":
                    logger.error(f"Shot {shot.id}: 生成失败")
                    return None
        
        logger.warning(f"Shot {shot.id}: 超时")
        return None


# ============================================================
# Phase 3: 配音 — Edge TTS 全文朗读
# ============================================================
async def generate_narration(drama: DramaScript) -> str:
    """生成全文旁白配音"""
    import sys as _sys
    
    # Build full narration text
    lines = []
    for shot in drama.shots:
        if shot.dialogue:
            lines.append(shot.dialogue)
    
    full_text = "。".join(lines)
    if len(full_text) > 500:
        full_text = full_text[:500]  # Edge TTS limit
    
    output_file = str(OUTPUT_DIR / "narration.mp3")
    
    # Use edge-tts via python module
    result = subprocess.run(
        [_sys.executable, "-m", "edge_tts", "--text", full_text,
         "--voice", "zh-CN-XiaoxiaoNeural", "--write-media", output_file],
        capture_output=True, text=True, timeout=60
    )
    
    if result.returncode == 0:
        size = os.path.getsize(output_file)
        logger.info(f"配音完成: {size/1024:.0f}KB")
        return output_file
    else:
        logger.error(f"配音失败: {result.stderr[:200]}")
        return None


# ============================================================
# Phase 4: 剪辑 — FFmpeg 合成最终 MP4
# ============================================================
def composite_final(drama: DramaScript, audio_path: str, output_path: str):
    """合成最终 720×1280 MP4"""
    import imageio_ffmpeg
    
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    
    # Get audio duration
    probe = subprocess.run(
        [ffmpeg, "-i", audio_path],
        capture_output=True, text=True
    )
    # Extract duration from stderr
    dur_match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', probe.stderr)
    if dur_match:
        h, m, s = dur_match.groups()
        audio_duration = float(h)*3600 + float(m)*60 + float(s)
    else:
        audio_duration = drama.total_duration_sec
    
    # Build concat file for shots
    concat_file = str(OUTPUT_DIR / "concat.txt")
    valid_shots = []
    for shot in drama.shots:
        if shot.video_path and os.path.exists(shot.video_path):
            valid_shots.append(shot)
    
    if not valid_shots:
        logger.error("没有有效的视频片段！")
        return None
    
    # Calculate per-shot duration to match audio
    total_dur = sum(s.duration_sec for s in valid_shots)
    
    with open(concat_file, "w") as f:
        for shot in valid_shots:
            f.write(f"file '{shot.video_path}'\n")
            f.write(f"duration {shot.duration_sec}\n")
        # Last entry repeated for concat
        f.write(f"file '{valid_shots[-1].video_path}'\n")
    
    # FFmpeg command
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-i", audio_path,
        "-vf", f"scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    if result.returncode == 0:
        size = os.path.getsize(output_path)
        logger.info(f"成片: {output_path} ({size/1024/1024:.1f}MB)")
        return output_path
    else:
        logger.error(f"合成失败: {result.stderr[-500:]}")
        return None


# ============================================================
# 主流程
# ============================================================
async def produce_drama(topic: str) -> Optional[str]:
    """完整短剧生产线"""
    start = time.time()
    
    # Phase 1: 编剧
    logger.info(f"🎬 开始创作短片：{topic}")
    drama = await write_script(topic)
    
    # Save script
    script_path = OUTPUT_DIR / "script.json"
    script_path.write_text(json.dumps({
        "title": drama.title,
        "genre": drama.genre,
        "logline": drama.logline,
        "total_duration_sec": drama.total_duration_sec,
        "shots": [
            {
                "id": s.id, "scene": s.scene, "duration_sec": s.duration_sec,
                "camera": s.camera, "dialogue": s.dialogue,
                "visual_prompt": s.visual_prompt, "transition": s.transition
            }
            for s in drama.shots
        ]
    }, ensure_ascii=False, indent=2))
    
    # Phase 2: 摄像 — 限流并发生成（最多2路，防显存溢出）
    semaphore = asyncio.Semaphore(2)
    
    async def generate_with_limit(shot, idx):
        async with semaphore:
            return await generate_shot_video(shot, idx)
    
    logger.info(f"🎥 生成 {len(drama.shots)} 个镜头 (最多2路并发)...")
    tasks = [generate_with_limit(s, i) for i, s in enumerate(drama.shots)]
    video_paths = await asyncio.gather(*tasks)
    
    for shot, path in zip(drama.shots, video_paths):
        if path:
            shot.video_path = path
    
    ok_shots = sum(1 for s in drama.shots if s.video_path)
    logger.info(f"镜头: {ok_shots}/{len(drama.shots)} 成功")
    
    if ok_shots == 0:
        logger.error("所有镜头生成失败！")
        return None
    
    # Phase 3: 配音
    logger.info("🔊 生成配音...")
    audio_path = await generate_narration(drama)
    if not audio_path:
        logger.error("配音生成失败！")
        return None
    
    # Phase 4: 剪辑合成
    logger.info("✂️ 剪辑合成...")
    final_path = str(OUTPUT_DIR / f"{drama.title.replace(' ', '_')}.mp4")
    result = composite_final(drama, audio_path, final_path)
    
    elapsed = time.time() - start
    logger.info(f"✅ 完成！耗时 {elapsed/60:.1f} 分钟 → {result}")
    
    return result


async def main():
    topic = sys.argv[1] if len(sys.argv) > 1 else "深夜便利店，一个失眠的程序员遇到一个只喝热牛奶的神秘女孩"
    result = await produce_drama(topic)
    if result:
        print(f"\n🎬 成片: {result}")
    else:
        print("\n❌ 生产失败")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
