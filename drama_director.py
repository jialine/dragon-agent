#!/usr/bin/env python3
"""重写 drama_director.py — 用 subprocess+curl 替代 aiohttp"""
import json, sys, os, re, uuid, time, tempfile, subprocess, logging, asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("drama_director")

# ── Config ─────────────────────────────────────────────
LLM_BASE_URL = "https://api2.sangyuye.com/v1"
LLM_MODEL = "Qwen3.5-122B-A10B"
COMFYUI_HOST = "http://192.168.0.30:8188"
OUTPUT_DIR = Path("/tmp/drama_output")
OUTPUT_DIR.mkdir(exist_ok=True)

def _get_api_key():
    with open(os.path.expanduser("~/.dragon/.env")) as f:
        for line in f:
            if line.startswith("DRAGON_API_KEY="):
                return line.strip().split("=", 1)[1]
    return os.getenv("DRAGON_API_KEY", "")

API_KEY = _get_api_key()

def _curl_post(url, payload, timeout=120):
    """Reliable HTTP POST via curl subprocess."""
    cmd = [
        "curl", "-s", "--max-time", str(timeout),
        url,
        "-H", f"Authorization: Bearer {API_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload, ensure_ascii=False)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"curl failed: {r.stderr[:200]}")
    return json.loads(r.stdout)

def _curl_get(url, timeout=30):
    cmd = ["curl", "-s", "--max-time", str(timeout), url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout) if r.stdout.strip() else {}

SYSTEM_PROMPT = """竖屏短剧导演。只输出JSON：
{"title":"剧名","genre":"类型","logline":"梗概30字内","total_duration_sec":90,"shots":[{"id":1,"scene":"场景","duration_sec":5,"camera":"特写/中景/远景","dialogue":"台词","visual_prompt":"English prompt for AI video gen","negative_prompt":"blurry low quality","transition":"cut"}]}
要求：3秒钩子，反转高潮，英文visual_prompt，镜头变化，台词≤15字。"""

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

# ── Phase 1: 编剧 ──────────────────────────────────────

def write_script(topic: str) -> DramaScript:
    """编剧：输入主题，输出完整分镜剧本（同步版，用curl）"""
    # Qwen over-thinks with system prompts → use user-only message with "no think" directive
    user_msg = f"""直接输出以下JSON格式，不要任何解释、不要思考：

{{"title":"剧名","genre":"类型","logline":"梗概","total_duration_sec":90,"shots":[{{"id":1,"scene":"场景","duration_sec":5,"camera":"特写/中景/远景","dialogue":"台词","visual_prompt":"English AI video prompt with detailed visual description","negative_prompt":"blurry low quality","transition":"cut"}}]}}

创作主题：{topic}。5个镜头，竖屏9:16，有反转高潮，英文visual_prompt简短。"""

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": user_msg}],
        "temperature": 0.8,
        "max_tokens": 4096,
    }

    data = _curl_post(f"{LLM_BASE_URL}/chat/completions", payload, timeout=120)
    content = data["choices"][0]["message"]["content"]
    
    # Find JSON: strip think blocks then look for '{'
    cleaned = re.sub(r"<think>.*?(?:</think>|$)", "", content, flags=re.DOTALL).strip()
    # Fallback: if think regex ate everything (unclosed think), use raw content
    if not cleaned:
        cleaned = content
    cleaned = re.sub(r'```(?:json)?\s*', '', cleaned).strip()
    idx = cleaned.find("{")
    if idx < 0:
        raise ValueError(f"No JSON found in response: {cleaned[:300]}")
    cleaned = cleaned[idx:]
    
    # Try to parse; if truncated, repair by truncating to last valid shot
    try:
        script_data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error at char {e.pos}, attempting repair...")
        # Strategy: find the last complete shot (look for "},{" or last "}]")
        # Cut at last valid shot boundary and close the structure
        last_shot_end = cleaned.rfind('"},{"')
        if last_shot_end > 0:
            # Keep everything up to the last complete shot
            truncated = cleaned[:last_shot_end + 3]  # include the closing }
            # Close the shots array and the outer object
            truncated += ']}'
            script_data = json.loads(truncated)
            logger.info(f"Repaired: kept {len(script_data.get('shots', []))} shots (was truncated)")
        else:
            # Single shot, try to close it
            # Remove everything after the last complete string value
            last_quote = cleaned.rfind('"')
            if last_quote > 0:
                truncated = cleaned[:last_quote] + '"}]}'
                script_data = json.loads(truncated)
            else:
                raise ValueError(f"Cannot repair JSON: {e}")
    
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

# ── Phase 2: 摄像（同步版，用curl） ─────────────────────

def _comfyui_submit(workflow, client_id):
    """Submit workflow to ComfyUI, return prompt_id"""
    cmd = [
        "curl", "-s", "--max-time", "30",
        f"{COMFYUI_HOST}/api/prompt",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"prompt": workflow, "client_id": client_id})
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    data = json.loads(r.stdout)
    pid = data.get("prompt_id", "")
    if not pid:
        raise RuntimeError(f"ComfyUI submit failed: {r.stdout[:200]}")
    return pid

def _comfyui_wait(pid, timeout=600):
    """Poll ComfyUI until job completes, return history entry"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        cmd = ["curl", "-s", "--max-time", "10", f"{COMFYUI_HOST}/api/history/{pid}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if not r.stdout.strip():
            continue
        history = json.loads(r.stdout)
        if pid not in history:
            continue
        h = history[pid]
        status = h.get("status", {}).get("status_str", "")
        if status == "success":
            return h
        elif status == "error":
            raise RuntimeError(f"Generation failed: {h}")
    raise TimeoutError(f"ComfyUI timeout after {timeout}s")

def _download_frame(url, path):
    """Download single frame via curl"""
    cmd = ["curl", "-s", "--max-time", "30", "-o", path, url]
    subprocess.run(cmd, check=True, timeout=35)

def generate_shot_video(shot: Shot, shot_index: int) -> Optional[str]:
    """为单个镜头生成视频"""
    width, height = 360, 640
    frames = int(shot.duration_sec * 8)
    client_id = str(uuid.uuid4())

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": frames}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": shot.visual_prompt, "clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": shot.negative_prompt, "clip": ["1", 1]}},
        "5": {"class_type": "ADE_StandardUniformContextOptions", "inputs": {
            "context_length": 16, "context_stride": 1, "context_overlap": 4,
            "fuse_method": "pyramid", "use_on_equal_length": False,
            "start_percent": 0.0, "guarantee_steps": 1,
            "prev_context": None, "view_opts": None
        }},
        "6": {"class_type": "ADE_AnimateDiffLoaderGen1", "inputs": {
            "model": ["1", 0], "model_name": "mm_sd_v15_v2.ckpt",
            "beta_schedule": "sqrt_linear (AnimateDiff)", "context_options": ["5", 0]
        }},
        "7": {"class_type": "KSampler", "inputs": {
            "seed": 42 + shot_index, "steps": 20, "cfg": 7.5,
            "sampler_name": "euler", "scheduler": "normal", "denoise": 0.8,
            "model": ["6", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["2", 0]
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": f"shot_{shot.id:03d}"}}
    }

    output_file = str(OUTPUT_DIR / f"shot_{shot.id:03d}.mp4")
    frames_dir = OUTPUT_DIR / f"frames_{shot.id:03d}"
    frames_dir.mkdir(exist_ok=True)

    try:
        pid = _comfyui_submit(workflow, client_id)
        logger.info(f"Shot {shot.id}: 生成中 (pid={pid[:8]}... {frames} frames)")
        
        h = _comfyui_wait(pid)
        outputs = h.get("outputs", {})
        
        for node_id, node_out in outputs.items():
            images = node_out.get("images", [])
            if not images:
                continue
            
            logger.info(f"Shot {shot.id}: 下载 {len(images)} 帧...")
            frame_paths = []
            for idx, img_info in enumerate(images):
                src = f"{COMFYUI_HOST}/api/view?filename={img_info['filename']}&subfolder={img_info.get('subfolder','')}&type=output"
                frame_path = str(frames_dir / f"frame_{idx:05d}.png")
                _download_frame(src, frame_path)
                if os.path.getsize(frame_path) > 0:
                    frame_paths.append(frame_path)
            
            if frame_paths:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                result = subprocess.run([
                    ffmpeg, "-y", "-framerate", "8",
                    "-i", str(frames_dir / "frame_%05d.png"),
                    "-c:v", "libx264", "-preset", "fast",
                    "-pix_fmt", "yuv420p", output_file
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
    except Exception as e:
        logger.error(f"Shot {shot.id}: {e}")
        return None

# ── Phase 3: 配音 ──────────────────────────────────────

def generate_narration(drama: DramaScript) -> Optional[str]:
    lines = [s.dialogue for s in drama.shots if s.dialogue]
    full_text = "。".join(lines)[:500]
    if not full_text:
        full_text = drama.logline[:500]
    
    output_file = str(OUTPUT_DIR / "narration.mp3")
    result = subprocess.run(
        [sys.executable, "-m", "edge_tts", "--text", full_text,
         "--voice", "zh-CN-XiaoxiaoNeural", "--write-media", output_file],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0:
        logger.info(f"配音完成: {os.path.getsize(output_file)/1024:.0f}KB")
        return output_file
    logger.error(f"配音失败: {result.stderr[:200]}")
    return None

# ── Phase 4: 剪辑 ──────────────────────────────────────

def composite_final(drama: DramaScript, audio_path: str, output_path: str) -> Optional[str]:
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    
    valid_shots = [s for s in drama.shots if s.video_path and os.path.exists(s.video_path)]
    if not valid_shots:
        logger.error("没有有效的视频片段！")
        return None
    
    concat_file = str(OUTPUT_DIR / "concat.txt")
    with open(concat_file, "w") as f:
        for shot in valid_shots:
            f.write(f"file '{shot.video_path}'\nduration {shot.duration_sec}\n")
        f.write(f"file '{valid_shots[-1].video_path}'\n")
    
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-i", audio_path,
        "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
        "-shortest", "-movflags", "+faststart", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        size = os.path.getsize(output_path)
        logger.info(f"成片: {output_path} ({size/1024/1024:.1f}MB)")
        return output_path
    logger.error(f"合成失败: {result.stderr[-500:]}")
    return None

# ── 主流程 ─────────────────────────────────────────────

async def produce_drama(topic: str) -> Optional[str]:
    start = time.time()
    
    # Phase 1: 编剧（同步，线程池执行）
    logger.info(f"🎬 开始创作短片：{topic}")
    drama = await asyncio.to_thread(write_script, topic)
    
    script_path = OUTPUT_DIR / "script.json"
    script_path.write_text(json.dumps({
        "title": drama.title, "genre": drama.genre, "logline": drama.logline,
        "total_duration_sec": drama.total_duration_sec,
        "shots": [{"id": s.id, "scene": s.scene, "duration_sec": s.duration_sec,
                   "camera": s.camera, "dialogue": s.dialogue, "visual_prompt": s.visual_prompt,
                   "transition": s.transition} for s in drama.shots]
    }, ensure_ascii=False, indent=2))
    
    # Phase 2: 摄像 — 限流并发生成（最多1路，RTX 3080 16GB 不并发）
    semaphore = asyncio.Semaphore(1)
    
    async def generate_with_limit(shot, idx):
        async with semaphore:
            return await asyncio.to_thread(generate_shot_video, shot, idx)
    
    logger.info(f"🎥 生成 {len(drama.shots)} 个镜头 (RTX 3080 单路)...")
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
    audio_path = await asyncio.to_thread(generate_narration, drama)
    if not audio_path:
        logger.error("配音生成失败！")
        return None
    
    # Phase 4: 剪辑合成
    logger.info("✂️ 剪辑合成...")
    final_path = str(OUTPUT_DIR / f"{drama.title.replace(' ', '_')}.mp4")
    result = await asyncio.to_thread(composite_final, drama, audio_path, final_path)
    
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
