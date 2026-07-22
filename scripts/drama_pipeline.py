"""
Dragon Drama Pipeline — Standalone script for Dragon Agent
Usage: python3 drama_pipeline.py [script.json] [--shot N] [--shots N-M]

Reads API key from ANDLAPI_KEY env var (in .env).
"""
import requests, json, urllib3, os, time, subprocess, sys, argparse
from pathlib import Path

urllib3.disable_warnings()

# === Config ===
API_BASE = os.environ.get("ANDLAPI_BASE", "https://api.andlapi.cn/v1")
API_KEY = os.environ.get("ANDLAPI_KEY", "")
OSS_BASE = "https://ossuploadimages.oss-cn-hangzhou.aliyuncs.com/DRAGON-DLLJEL5DMQHKAJM/characters"

REF_URLS = {
    "lingchen": f"{OSS_BASE}/lingchen_fullbody.png",
    "liuyan": f"{OSS_BASE}/liuyan_fullbody.png",
    "liufeng": f"{OSS_BASE}/liufeng_fullbody.png",
}

VOICES = {
    "凌尘": "zh-CN-YunxiNeural",
    "柳烟": "zh-CN-XiaoxiaoNeural",
    "柳风": "zh-CN-YunjianNeural",
    "系统": "zh-CN-YunyangNeural",
    "旁白": "zh-CN-YunjianNeural",
}


def submit_shot(shot):
    """Submit a single shot to WAN API with correct format."""
    payload = {
        "model": f"wan2.7-{shot['model']}",
        "parameters": {
            "resolution": "1080P",
            "ratio": "9:16",
            "duration": min(shot["duration"], 8),
        },
        "prompt": shot["visual_prompt"],
    }
    
    if shot["model"] == "r2v":
        refs = shot["ref"].split("+")
        payload["media"] = [{"type": "reference_image", "url": REF_URLS[r]} for r in refs]
        ref_desc = "; ".join(f"Reference image {i+1}: {r}" for i, r in enumerate(refs))
        payload["prompt"] = f"{ref_desc}. {shot['visual_prompt']}"
    
    resp = requests.post(
        f"{API_BASE}/video/generations", json=payload,
        headers={"Authorization": f"Bearer {API_KEY}"}, verify=False, timeout=30
    )
    return resp.json().get("task_id", "")


def poll_shot(task_id, timeout=600):
    """Poll until complete, return video_url or None."""
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(
            f"{API_BASE}/video/generations/{task_id}",
            headers={"Authorization": f"Bearer {API_KEY}"}, verify=False, timeout=30
        )
        r = resp.json()
        d = r.get("data", {})
        status = d.get("status", "")
        
        if status == "SUCCESS":
            result = d.get("data", {}).get("result", {})
            return result.get("video_url", "")
        elif status == "FAILURE":
            return None
        
        time.sleep(10)
    return None


def download_video(url, path):
    """Download video from URL to path."""
    resp = requests.get(url, verify=False, timeout=120)
    with open(path, "wb") as f:
        f.write(resp.content)
    return os.path.getsize(path)


def combine_videos(video_paths, output_path):
    """Combine videos with xfade transitions + audio mix."""
    parts = []
    for v in video_paths:
        parts.extend(["-i", v])
    
    dur = 8
    filters = []
    prev = "[0:v]"
    
    for i in range(1, len(video_paths)):
        offset = i * dur - 0.5
        out = f"[xf{i-1}]"
        filters.append(f"{prev}[{i}:v]xfade=transition=fade:duration=0.5:offset={offset}{out}")
        prev = out
    
    # Audio mix
    audio_inputs = "".join(f"[{i}:a]" for i in range(len(video_paths)))
    filters.append(f"{audio_inputs}amix=inputs={len(video_paths)}:duration=longest[aout]")
    
    cmd = ["ffmpeg", "-y"] + parts + [
        "-filter_complex", ";".join(filters),
        "-map", prev, "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0


def generate_tts(shot, out_dir):
    """Generate TTS audio for a shot's dialogue using edge-tts."""
    dialogue = shot.get("dialogue", "")
    if not dialogue:
        return None
    
    # Determine voice from dialogue prefix or scene context
    scene = shot.get("scene", "")
    voice = VOICES.get("旁白", "zh-CN-YunjianNeural")
    for char_name, char_voice in VOICES.items():
        if char_name in scene or (shot.get("dialogue", "").startswith(char_name)):
            voice = char_voice
            # Strip character prefix
            dialogue = dialogue.replace(f"{char_name}:", "").replace(f"{char_name}：", "").strip()
            break
    
    # Also check notes for character info
    ref = shot.get("ref", "")
    ref_map = {"lingchen": "凌尘", "liuyan": "柳烟", "liufeng": "柳风"}
    for ref_key, char_name in ref_map.items():
        if ref_key in ref:
            voice = VOICES.get(char_name, voice)
    
    out_path = f"{out_dir}/S{shot['id']:02d}_tts.mp3"
    cmd = ["edge-tts", "--voice", voice, "--text", dialogue, "--write-media", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return out_path
    return None


def main():
    parser = argparse.ArgumentParser(description="Dragon Drama Pipeline")
    parser.add_argument("script", help="Path to script JSON file")
    parser.add_argument("--shots", help="Shot range e.g. 1-5 or single shot 3")
    parser.add_argument("--out", default="/tmp/drama_eps", help="Output directory")
    parser.add_argument("--no-tts", action="store_true", help="Skip TTS generation")
    parser.add_argument("--no-combine", action="store_true", help="Skip final combine")
    args = parser.parse_args()
    
    if not API_KEY:
        print("ERROR: ANDLAPI_KEY not set. Add to Dragon .env file.")
        sys.exit(1)
    
    # Load script
    with open(args.script) as f:
        script = json.load(f)
    
    # Filter shots
    shots = script["shots"]
    if args.shots:
        if "-" in args.shots:
            start, end = args.shots.split("-")
            shots = [s for s in shots if int(start) <= s["id"] <= int(end)]
        else:
            sid = int(args.shots)
            shots = [s for s in shots if s["id"] == sid]
    
    out_dir = Path(args.out) / f"ep{script['episode']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"=== {script['title']} E{script['episode']}「{script['ep_title']}」===")
    print(f"Shots: {len(shots)} | Out: {out_dir}")
    
    # Phase 1: Submit
    print("\n--- Submitting ---")
    task_ids = {}
    for s in shots:
        sid = s["id"]
        tid = submit_shot(s)
        if tid:
            task_ids[sid] = tid
            print(f"  S{sid:02d} [{s['model']}] -> {tid}")
        else:
            print(f"  S{sid:02d} FAILED to submit")
    
    # Phase 2: Poll & Download
    print(f"\n--- Polling {len(task_ids)} tasks ---")
    video_files = []
    for sid, tid in task_ids.items():
        print(f"  S{sid:02d} polling...", end=" ", flush=True)
        vu = poll_shot(tid)
        if vu:
            path = str(out_dir / f"S{sid:02d}.mp4")
            sz = download_video(vu, path) // 1024
            video_files.append(path)
            print(f"✅ {sz}KB")
        else:
            print(f"❌ FAILED")
    
    # Phase 3: TTS (optional)
    if not args.no_tts:
        print(f"\n--- Generating TTS ---")
        for s in shots:
            tts_path = generate_tts(s, str(out_dir))
            if tts_path:
                print(f"  S{s['id']:02d} TTS ✅")
    
    # Phase 4: Combine
    if not args.no_combine and len(video_files) >= 2:
        output = str(out_dir / f"E{script['episode']}_final.mp4")
        print(f"\n--- Combining {len(video_files)} videos -> {output} ---")
        if combine_videos(video_files, output):
            sz = os.path.getsize(output) // 1024
            print(f"✅ Final: {sz}KB")
        else:
            print("❌ Combine failed")
    
    print(f"\nDone. Output: {out_dir}/")
    # Return output path for Dragon to consume
    final = str(out_dir / f"E{script['episode']}_final.mp4")
    if os.path.exists(final):
        print(f"FINAL_VIDEO={final}")


if __name__ == "__main__":
    main()
