#!/usr/bin/env python3
"""
H3 本地批量出片器 — 0成本短剧生产线
=====================================
用 5 台本地 ComfyUI (MiniMax H3) 集群批量生成短剧镜头。

能力:
  1. FL2VA 首尾帧: first_frame + last_frame 硬锚定, 镜头无缝衔接
  2. Ref2VA 参考图: 单人/多人角色参考图 (身份一致性)
  3. 5 台工作池调度, 自动负载均衡
  4. 首尾帧衔接链: 上一镜尾帧 -> 下一镜 first_frame
  5. 场记命名 {project}_EP{ep}_S{shot}_V{ver}_T{take}_s{seed}.mp4
  6. 无 BGM (ffmpeg 砍音轨): 中文版砍音轨; 英文版 shot 带 keep_audio=True 保留 H3 原生对白音轨

用法:
  python3 h3_batch_gen.py --project 猩族纪元 --episode 1 \
      --shots /path/to/shots.json [--dry-run]

shots.json 格式 (一镜一个对象):
[
  {
    "shot_number": 1,
    "mode": "ref2va",            # ref2va(参考图) 或 fl2va(首尾帧)
    "prompt": "英文/中文动作描述",
    "ref_images": ["/path/ref1.jpg", "/path/ref2.jpg"],  # ref2va 模式
    "first_frame": "/path/first.jpg",   # fl2va 模式
    "last_frame": "/path/last.jpg",     # fl2va 模式 (可选)
    "duration": 8,               # 秒
    "steps": 4,                  # 4=ref2v turbo, 8=fl2v turbo
    "seed": 0                    # 0=随机
  },
  ...
]
"""
import json
import sys
import time
import os
import random
import subprocess
import argparse
import urllib.request
import urllib.error
import urllib.parse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 配置 ====================
COMFY_NODES = [
    "192.168.0.21",   # RTX 5080
    "192.168.0.17",   # 4070TiS
    "192.168.0.22",   # 5070Ti
    "192.168.0.28",   # 5060Ti
    "192.168.0.30",   # 3080 Laptop
]
COMFY_PORT = 8188
VIDEO_DIR = "/home/jialine/dragon-agent/assets/videos"

# 模型常量 (与 .21 已验证环境一致)
CLIP_QWEN = "qwen3vl_text_encoder.safetensors"
VAE_VIDEO = "minimax_h3_video_vae_fp16.safetensors"
VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
UNET_FL2VA = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
UNET_REF2VA = "minimax_h3_ref2va_dit_16g.safetensors"
LORA_FL2V_8S = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA_FL2V_4S = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
LORA_REF2V_4S = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"

# ==================== HTTP ====================
def _http_json(base, path, data=None, timeout=900):
    url = f"http://{base}:{COMFY_PORT}{path}"
    req = (urllib.request.Request(url) if data is None else
           urllib.request.Request(url, data=json.dumps(data).encode(),
                                   headers={'Content-Type': 'application/json'}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _comfy_ready(base):
    """节点健康检查: 是否在线 + 队列是否空闲."""
    try:
        s = _http_json(base, "/system_stats", timeout=5)
        q = _http_json(base, "/queue", timeout=5)
        running = len(q.get("queue_running", []))
        pending = len(q.get("queue_pending", []))
        return True, running, pending
    except Exception:
        return False, 0, 0


# ==================== 时长换算 ====================
def seconds_to_length(seconds):
    """秒 -> H3 length (17k+5 网格, 24fps). 124=5s, 362=15s."""
    frames = round(seconds * 24)
    # 对齐到 17k+5: 找最接近的 length
    length = 124  # 5s 起点
    while length + 17 < frames:
        length += 17
    return max(124, length)


# ==================== 工作流构建 ====================
def build_fl2va_workflow(prompt, first_frame, last_frame, width, height,
                         length, seed, steps=8, with_solattn=True):
    """首尾帧 FL2VA 工作流 (first_frame+last_frame 硬锚定)."""
    lora = LORA_FL2V_8S if steps == 8 else LORA_FL2V_4S
    wf = {
        "1_clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_QWEN, "type": "minimax"}},
        "2_unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_FL2VA, "weight_dtype": "default"}},
        "3_vae_video": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_VIDEO}},
        "4_vae_audio": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_AUDIO}},
        "5_first": {"class_type": "LoadImage", "inputs": {"image": os.path.basename(first_frame)}},
        "6_lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2_unet", 0], "lora_name": lora, "strength_model": 1.0}},
    }
    node_idx = 7
    prev_model = ["6_lora", 0]

    # sol-attn 加速链 (可选)
    if with_solattn:
        wf[f"{node_idx}_sol"] = {"class_type": "MiniMaxH3MemoryEfficientSolAttentionPatch",
            "inputs": {"model": prev_model, "enabled": True, "tau": 1.0, "min_tokens": 4096,
                       "strict": True, "thresh_type": "diag", "int8_qk": False, "int8_pv": False,
                       "sink_conditioning": "exact_kv", "dense_blocks": ""}}
        prev_model = [f"{node_idx}_sol", 0]; node_idx += 1
        wf[f"{node_idx}_fused"] = {"class_type": "MiniMaxH3FusedModulation",
            "inputs": {"model": prev_model, "enabled": True}}
        prev_model = [f"{node_idx}_fused", 0]; node_idx += 1
        wf[f"{node_idx}_chunk"] = {"class_type": "MiniMaxH3ChunkFeedForward",
            "inputs": {"model": prev_model, "enabled": True, "chunks": 4, "min_tokens": 8192}}
        prev_model = [f"{node_idx}_chunk", 0]; node_idx += 1
        wf[f"{node_idx}_cache"] = {"class_type": "EasyCache",
            "inputs": {"model": prev_model, "reuse_threshold": 0.1, "start_percent": 0.15,
                       "end_percent": 0.85, "verbose": False}}
        prev_model = [f"{node_idx}_cache", 0]; node_idx += 1

    wf[f"{node_idx}_shift"] = {"class_type": "MiniMaxH3SigmaShift",
        "inputs": {"model": prev_model, "shift_video": 12.0, "shift_audio": 3.0}}
    shift = [f"{node_idx}_shift", 0]; node_idx += 1

    i2v_inputs = {"clip": ["1_clip", 0], "vae": ["3_vae_video", 0], "prompt": prompt,
                  "width": width, "height": height, "length": length,
                  "first_frame": ["5_first", 0]}
    if last_frame:
        wf[f"{node_idx}_last"] = {"class_type": "LoadImage", "inputs": {"image": os.path.basename(last_frame)}}
        i2v_inputs["last_frame"] = [f"{node_idx}_last", 0]; node_idx += 1

    wf[f"{node_idx}_i2v"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": i2v_inputs}
    i2v = f"{node_idx}_i2v"; node_idx += 1

    wf[f"{node_idx}_noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    noise = f"{node_idx}_noise"; node_idx += 1
    wf[f"{node_idx}_sam"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    sam = f"{node_idx}_sam"; node_idx += 1
    wf[f"{node_idx}_sched"] = {"class_type": "BasicScheduler", "inputs": {"model": shift, "scheduler": "simple", "steps": steps, "denoise": 1.0}}
    sched = f"{node_idx}_sched"; node_idx += 1
    wf[f"{node_idx}_guide"] = {"class_type": "BasicGuider", "inputs": {"model": shift, "conditioning": [i2v, 0]}}
    guide = f"{node_idx}_guide"; node_idx += 1
    wf[f"{node_idx}_samp"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [noise, 0], "guider": [guide, 0], "sampler": [sam, 0], "sigmas": [sched, 0], "latent_image": [i2v, 1]}}
    samp = f"{node_idx}_samp"; node_idx += 1

    wf[f"{node_idx}_decv"] = {"class_type": "VAEDecode", "inputs": {"samples": [samp, 0], "vae": ["3_vae_video", 0]}}
    decv = f"{node_idx}_decv"; node_idx += 1
    wf[f"{node_idx}_deca"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": [samp, 0], "vae": ["4_vae_audio", 0]}}
    deca = f"{node_idx}_deca"; node_idx += 1
    wf[f"{node_idx}_make"] = {"class_type": "CreateVideo", "inputs": {"images": [decv, 0], "audio": [deca, 0], "fps": 24.0}}
    make = f"{node_idx}_make"; node_idx += 1
    wf[f"{node_idx}_save"] = {"class_type": "SaveVideo", "inputs": {"video": [make, 0], "filename_prefix": "H3_BATCH/shot", "format": "auto"}}

    return wf


def build_ref2va_workflow(prompt, ref_images, width, height, length, seed,
                          steps=4, with_solattn=True):
    """参考图 Ref2VA 工作流 (单人/多人身份一致性)."""
    wf = {
        "1_clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_QWEN, "type": "minimax"}},
        "2_unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_REF2VA, "weight_dtype": "default"}},
        "3_vae_video": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_VIDEO}},
        "4_vae_audio": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_AUDIO}},
        "6_lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2_unet", 0], "lora_name": LORA_REF2V_4S, "strength_model": 1.0}},
    }
    node_idx = 7
    prev_model = ["6_lora", 0]

    # 参考图 LoadImage
    ref_nodes = {}
    for i, img in enumerate(ref_images):
        wf[f"5_ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": os.path.basename(img)}}
        ref_nodes[f"ref_image_{i}"] = [f"5_ref{i}", 0]

    if with_solattn:
        wf[f"{node_idx}_sol"] = {"class_type": "MiniMaxH3MemoryEfficientSolAttentionPatch",
            "inputs": {"model": prev_model, "enabled": True, "tau": 1.0, "min_tokens": 4096,
                       "strict": True, "thresh_type": "diag", "int8_qk": False, "int8_pv": False,
                       "sink_conditioning": "exact_kv", "dense_blocks": ""}}
        prev_model = [f"{node_idx}_sol", 0]; node_idx += 1
        wf[f"{node_idx}_fused"] = {"class_type": "MiniMaxH3FusedModulation",
            "inputs": {"model": prev_model, "enabled": True}}
        prev_model = [f"{node_idx}_fused", 0]; node_idx += 1
        wf[f"{node_idx}_chunk"] = {"class_type": "MiniMaxH3ChunkFeedForward",
            "inputs": {"model": prev_model, "enabled": True, "chunks": 4, "min_tokens": 8192}}
        prev_model = [f"{node_idx}_chunk", 0]; node_idx += 1
        wf[f"{node_idx}_cache"] = {"class_type": "EasyCache",
            "inputs": {"model": prev_model, "reuse_threshold": 0.1, "start_percent": 0.15,
                       "end_percent": 0.85, "verbose": False}}
        prev_model = [f"{node_idx}_cache", 0]; node_idx += 1

    wf[f"{node_idx}_shift"] = {"class_type": "MiniMaxH3SigmaShift",
        "inputs": {"model": prev_model, "shift_video": 12.0, "shift_audio": 3.0}}
    shift = [f"{node_idx}_shift", 0]; node_idx += 1

    wf[f"{node_idx}_ref2v"] = {"class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {"clip": ["1_clip", 0], "vae": ["3_vae_video", 0], "audio_vae": ["4_vae_audio", 0],
                   "prompt": prompt, "width": width, "height": height, "length": length,
                   "ref_image_size": "match", "ref_images": ref_nodes}}
    ref2v = f"{node_idx}_ref2v"; node_idx += 1

    wf[f"{node_idx}_noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    noise = f"{node_idx}_noise"; node_idx += 1
    wf[f"{node_idx}_sam"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    sam = f"{node_idx}_sam"; node_idx += 1
    wf[f"{node_idx}_sched"] = {"class_type": "BasicScheduler", "inputs": {"model": shift, "scheduler": "simple", "steps": steps, "denoise": 1.0}}
    sched = f"{node_idx}_sched"; node_idx += 1
    wf[f"{node_idx}_guide"] = {"class_type": "BasicGuider", "inputs": {"model": shift, "conditioning": [ref2v, 0]}}
    guide = f"{node_idx}_guide"; node_idx += 1
    wf[f"{node_idx}_samp"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [noise, 0], "guider": [guide, 0], "sampler": [sam, 0], "sigmas": [sched, 0], "latent_image": [ref2v, 1]}}
    samp = f"{node_idx}_samp"; node_idx += 1

    wf[f"{node_idx}_decv"] = {"class_type": "VAEDecode", "inputs": {"samples": [samp, 0], "vae": ["3_vae_video", 0]}}
    decv = f"{node_idx}_decv"; node_idx += 1
    wf[f"{node_idx}_deca"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": [samp, 0], "vae": ["4_vae_audio", 0]}}
    deca = f"{node_idx}_deca"; node_idx += 1
    wf[f"{node_idx}_make"] = {"class_type": "CreateVideo", "inputs": {"images": [decv, 0], "audio": [deca, 0], "fps": 24.0}}
    make = f"{node_idx}_make"; node_idx += 1
    wf[f"{node_idx}_save"] = {"class_type": "SaveVideo", "inputs": {"video": [make, 0], "filename_prefix": "H3_BATCH/shot", "format": "auto"}}

    return wf


# ==================== 工具函数 ====================
def _ffmpeg():
    """返回 ffmpeg 可执行路径: 优先系统 ffmpeg, 否则用 imageio_ffmpeg 自带二进制。

    这样 .32 等无系统 ffmpeg 的机器只需 `pip install imageio-ffmpeg` 即可。
    """
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return [p]
    try:
        import imageio_ffmpeg
        return [imageio_ffmpeg.get_ffmpeg_exe()]
    except ImportError:
        return ["ffmpeg"]  # 兜底, 会因找不到而失败


def extract_last_frame(video_path, out_jpg):
    """ffmpeg 抽视频最后一帧 (用于首尾帧衔接)."""
    subprocess.run(
        _ffmpeg() + ["-y", "-v", "error", "-sseof", "-0.1", "-i", video_path,
                     "-vframes", "1", "-q:v", "2", out_jpg],
        check=False
    )
    return out_jpg if os.path.exists(out_jpg) else None


def strip_audio(video_path, out_path):
    """砍掉音轨 (无 BGM)."""
    subprocess.run(_ffmpeg() + ["-y", "-v", "error", "-i", video_path,
                                "-an", "-c:v", "copy", out_path], check=False)
    return out_path if os.path.exists(out_path) else video_path


def shot_filename(project, episode, shot_number, seed, take=1, version=None):
    """场记命名: {project}_EP{ep}_S{shot}_V{ver}_T{take}_s{seed}.mp4"""
    project_dir = Path(VIDEO_DIR) / project
    project_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{project}_EP{episode:02d}_S{shot_number:02d}"
    if version is None:
        existing = sorted(project_dir.glob(f"{prefix}_V*_T*_s*.mp4"))
        version = len(existing) + 1
    return str(project_dir / f"{prefix}_V{version:03d}_T{take:02d}_s{seed}.mp4")


# ==================== 单镜生成 ====================
def _submit_and_wait(base, workflow, timeout=1800):
    """提交到指定节点并等待完成, 返回 (filename, subfolder)."""
    r = _http_json(base, "/prompt", {'prompt': workflow, 'client_id': f"h3batch_{int(time.time())}_{random.randint(0,9999)}"})
    pid = r.get('prompt_id')
    if not pid:
        raise RuntimeError(f"提交失败: {r}")
    start = time.time()
    while time.time() - start < timeout:
        try:
            h = _http_json(base, f"/history/{pid}", timeout=30)
            if pid in h:
                st = h[pid].get('status', {})
                if st.get('completed') or st.get('status_str') == 'success':
                    # SaveVideo 输出结构: {"images": [{"filename":...}], "animated": [...]}
                    for nid, out in h[pid].get('outputs', {}).items():
                        if not isinstance(out, dict):
                            continue
                        for items_key in ('images', 'gifs', 'videos'):
                            items = out.get(items_key)
                            if isinstance(items, list):
                                for it in items:
                                    if isinstance(it, dict) and 'filename' in it:
                                        return it['filename'], it.get('subfolder', '')
                    raise RuntimeError("生成完成但无输出文件")
                if st.get('status_str') == 'error':
                    raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(st, ensure_ascii=False)[:600]}")
        except urllib.error.URLError:
            pass
        time.sleep(10)
    raise TimeoutError(f"生成超时 ({timeout}s)")


def _download(base, filename, subfolder, out_path):
    """从 ComfyUI 下载成片."""
    params = f"filename={urllib.parse.quote(filename)}&subfolder={urllib.parse.quote(subfolder or '')}&type=output"
    url = f"http://{base}:{COMFY_PORT}/view?{params}"
    with urllib.request.urlopen(url, timeout=300) as r, open(out_path, 'wb') as f:
        f.write(r.read())
    return out_path


def _upload_image(base, local_path):
    """上传参考图到 ComfyUI input 目录 (multipart/form-data)."""
    name = os.path.basename(local_path)
    url = f"http://{base}:{COMFY_PORT}/upload/image"
    import mimetypes
    ctype = mimetypes.guess_type(local_path)[0] or 'application/octet-stream'
    with open(local_path, 'rb') as f:
        file_data = f.read()
    boundary = "----h3batch" + str(random.randint(100000, 999999))
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + file_data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    return name


def generate_shot(base, shot, project, episode, workdir):
    """在指定节点生成单个镜头, 返回 (场记路径, 尾帧jpg).

    shot 可带 keep_audio=True 保留 H3 原生对白音轨 (英文版);
    默认 False = 砍音轨 (无 BGM, 符合中文版铁律)。
    """
    Path(workdir).mkdir(parents=True, exist_ok=True)
    shot_num = shot['shot_number']
    mode = shot.get('mode', 'ref2va')
    duration = shot.get('duration', 8)
    steps = shot.get('steps', 4 if mode == 'ref2va' else 8)
    seed = shot.get('seed') or random.randint(100000, 999999)
    keep_audio = shot.get('keep_audio', False)
    length = seconds_to_length(duration)

    # 上传参考图/首尾帧到该节点
    local_video = shot_filename(project, episode, shot_num, seed)

    if mode == 'fl2va':
        first_frame = shot['first_frame']
        last_frame = shot.get('last_frame')
        _upload_image(base, first_frame)
        if last_frame:
            _upload_image(base, last_frame)
        wf = build_fl2va_workflow(shot['prompt'], first_frame, last_frame,
                                  1344, 768, length, seed, steps=steps)
    else:
        ref_images = shot.get('ref_images', [])
        for img in ref_images:
            _upload_image(base, img)
        wf = build_ref2va_workflow(shot['prompt'], ref_images,
                                   1344, 768, length, seed, steps=steps)

    filename, subfolder = _submit_and_wait(base, wf)
    raw = os.path.join(workdir, f"raw_{shot_num}_{seed}.mp4")
    _download(base, filename, subfolder, raw)

    if keep_audio:
        # 保留 H3 原生对白音轨 (英文版)
        os.rename(raw, local_video)
    else:
        # 无 BGM: 砍音轨
        muted = os.path.join(workdir, f"muted_{shot_num}_{seed}.mp4")
        strip_audio(raw, muted)
        os.rename(muted, local_video)

    # 抽尾帧用于衔接
    tail_jpg = os.path.join(workdir, f"tail_{shot_num}_{seed}.jpg")
    extract_last_frame(local_video, tail_jpg)

    return local_video, tail_jpg, seed


# ==================== 调度器 ====================
class Scheduler:
    def __init__(self, nodes):
        self.nodes = nodes
        self.lock = threading.Lock()

    def pick_node(self):
        """选最空闲的节点."""
        best = None
        best_load = 10**9
        for n in self.nodes:
            ok, running, pending = _comfy_ready(n)
            if not ok:
                continue
            load = running * 10 + pending
            if load < best_load:
                best_load = load
                best = n
        return best


# ==================== 主流程 ====================
def generate_episode(shots, project, episode, workdir, max_workers=5):
    """批量生成一集所有镜头, 5 台并行调度.

    所有镜头必须带显式 first_frame/last_frame (fl2va) 或 ref_images (ref2va),
    这样彼此无依赖可完全并行。连续动作衔接通过在分镜阶段预生成首尾帧图实现。
    """
    Path(workdir).mkdir(parents=True, exist_ok=True)
    sched = Scheduler(COMFY_NODES)
    results: list = [None] * len(shots)  # type: ignore[assignment]

    def _run(task):
        i, shot = task
        shot_num = shot['shot_number']
        for attempt in range(1, 3):  # 最多重试2次
            base = sched.pick_node()
            if base is None:
                print(f"  [S{shot_num}] 无可用节点, 等待...")
                time.sleep(30)
                continue
            try:
                print(f"  [S{shot_num}] -> {base} (attempt {attempt})")
                video, tail, seed = generate_shot(base, shot, project, episode, workdir)
                print(f"  [S{shot_num}] 完成: {video}")
                return i, {"shot": shot_num, "video": video, "seed": seed,
                           "tail": tail, "status": "done"}
            except Exception as e:
                print(f"  [S{shot_num}] 失败: {e}")
                if attempt == 2:
                    return i, {"shot": shot_num, "status": "failed", "error": str(e)}
        return i, {"shot": shot_num, "status": "failed", "error": "no available node"}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, r in ex.map(_run, enumerate(shots)):
            results[i] = r

    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--shots", required=True, help="shots.json 路径")
    ap.add_argument("--workdir", default=None, help="临时目录, 默认 /tmp/h3batch_{project}_EP{ep}")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不生成")
    args = ap.parse_args()

    with open(args.shots) as f:
        shots = json.load(f)

    workdir = args.workdir or f"/tmp/h3batch_{args.project}_EP{args.episode:02d}"
    total_sec = sum(s.get('duration', 8) for s in shots)
    print(f"=== H3 批量出片: {args.project} EP{args.episode} ===")
    print(f"镜头数: {len(shots)}, 总时长约 {total_sec}s (~{total_sec/60:.1f}分钟)")
    print(f"节点池: {len(COMFY_NODES)} 台")

    if args.dry_run:
        for s in shots:
            print(f"  S{s['shot_number']}: {s.get('mode','ref2va')} {s.get('duration',8)}s")
        sys.exit(0)

    results = generate_episode(shots, args.project, args.episode, workdir)
    done = sum(1 for r in results if r['status'] == 'done')
    print(f"\n=== 完成 {done}/{len(shots)} 镜 ===")
    with open(os.path.join(workdir, "results.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)