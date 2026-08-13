"""HappyHorse video generation, download, and merge pipeline."""
import json
import subprocess
import time
import os
import re
import threading
from pathlib import Path

# Import submit/query/download directly from gen_video.py
import sys
sys.path.insert(0, "/home/jialine/dragon-agent/scripts")
from gen_video import submit, query_task, download as download_video_url  # type: ignore

VIDEO_DIR = "/home/jialine/dragon-agent/assets/videos"
Path(VIDEO_DIR).mkdir(parents=True, exist_ok=True)


def _build_video_filename(project_name, episode, shot_number, model, seed=None, take_number=1):
    """Generate consistent video filename: {project}_EP{ep}_S{shot}_V{ver}_T{take}_s{seed}.mp4
    Version auto-increments by counting existing files for this shot."""
    import glob, random
    if seed is None:
        seed = random.randint(100000, 999999)
    
    project_dir = os.path.join(VIDEO_DIR, project_name)
    os.makedirs(project_dir, exist_ok=True)
    
    prefix = f"{project_name}_EP{episode:02d}_S{shot_number:02d}"
    pattern = os.path.join(project_dir, f"{prefix}_V*_T*_s*.mp4")
    existing = sorted(glob.glob(pattern))
    version = len(existing) + 1
    
    filename = f"{prefix}_V{version:03d}_T{take_number:02d}_s{seed}.mp4"
    return os.path.join(project_dir, filename), seed


def _get_next_video_filename(project_name, episode, shot_number, model):
    """Get next filename for a shot, auto-incrementing version."""
    import glob
    project_dir = os.path.join(VIDEO_DIR, project_name)
    os.makedirs(project_dir, exist_ok=True)
    
    prefix = f"{project_name}_EP{episode:02d}_S{shot_number:02d}"
    pattern = os.path.join(project_dir, f"{prefix}_V*_T*_s*.mp4")
    existing = sorted(glob.glob(pattern))
    version = len(existing) + 1
    return os.path.join(project_dir, f"{prefix}_V{version:03d}.mp4")


def submit_video(prompt, model="happyhorse-1.1-t2v", size="1920*1080",
                 duration=8, ref_images=None, output_path=None):
    """
    Submit a video generation task. Returns immediately with task_id.
    Downloads in background thread when ready.
    ref_images: list of OSS URLs (for R2V)
    Returns: {"task_id": str, "submitted": bool}
    """
    if output_path is None:
        output_path = os.path.join(VIDEO_DIR, f"shot_{int(time.time())}.mp4")

    try:
        task_id = submit(prompt, model, size, duration, ref_images or [])
        # Start background download thread
        t = threading.Thread(
            target=_download_when_ready,
            args=(task_id, output_path),
            daemon=True
        )
        t.start()
        return {
            "task_id": task_id,
            "submitted": True,
            "local_path": "",
            "success": True
        }
    except Exception as e:
        return {
            "task_id": "",
            "submitted": False,
            "local_path": "",
            "success": False,
            "error": str(e)
        }


def _download_when_ready(task_id, output_path, timeout=600):
    """Background: poll until SUCCESS then download."""
    try:
        url = wait_for_result(task_id, timeout)
        if url:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            download_video_url(url, output_path)
            # Update video_tasks status
            _update_task_status(task_id, "done", output_path)
        else:
            _update_task_status(task_id, "failed", "")
    except Exception as e:
        _update_task_status(task_id, "failed", "")


def _update_task_status(task_id, status, local_path):
    """Update video_tasks + shots table with retry for SQLite locking."""
    import sqlite3, time
    for attempt in range(10):
        try:
            db = sqlite3.connect("/home/jialine/dragon-agent/webui/drama.db")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "UPDATE video_tasks SET status=?, video_local=?, completed_at=datetime('now','localtime') WHERE task_id=?",
                (status, local_path, task_id)
            )
            row = db.execute("SELECT shot_id FROM video_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row:
                db.execute("UPDATE shots SET status=?, video_local=? WHERE id=?", (status, local_path, row[0]))
            db.commit()
            db.close()
            return
        except Exception as e:
            try: db.close()
            except: pass
            if attempt < 9:
                time.sleep(1 + attempt * 0.5)
            else:
                print(f"[video_gen] _update_task_status FAILED after 10 attempts: {e}")


def wait_for_result(task_id, timeout=600):
    """Poll until task completes. Returns video URL or None."""
    for i in range(0, timeout, 15):
        time.sleep(15)
        status, url, reason = query_task(task_id)
        if status in ("SUCCESS", "SUCCEEDED", "COMPLETED", "succeeded", "DONE"):
            return url
        elif status in ("FAILED", "FAILURE", "CANCELED"):
            return None
    return None


def poll_task_status(task_id):
    """Poll a video task status directly via API."""
    status, url, reason = query_task(task_id)
    return {"task_id": task_id, "status": status or "unknown", "url": url or "", "reason": reason}


def submit_video_with_retry(prompt, model="happyhorse-1.1-t2v", size="1920*1080",
                            duration=8, ref_images=None, output_path=None,
                            max_retries=3, project_name="", episode=1, shot_number=1):
    """
    Submit video with auto-retry on failure.
    On retry: changes seed, increments take_number.
    Returns: {"task_id": str, "submitted": bool, "take_number": int, "seed": int, "retries": int}
    """
    import random
    
    for attempt in range(max_retries):
        seed = random.randint(100000, 999999)
        take_number = attempt + 1
        
        # Build filename for this attempt if output_path not provided
        if output_path is None and project_name:
            output_path, _ = _build_video_filename(
                project_name, episode, shot_number, model, seed=seed, take_number=take_number
            )
        
        try:
            task_id = submit(prompt, model, size, duration, ref_images or [])
            t = threading.Thread(
                target=_download_when_ready,
                args=(task_id, output_path),
                daemon=True
            )
            t.start()
            return {
                "task_id": task_id,
                "submitted": True,
                "success": True,
                "take_number": take_number,
                "seed": seed,
                "retries": attempt,
                "output_path": output_path
            }
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[video_gen] Attempt {attempt+1} failed: {e}, retrying with new seed...")
                time.sleep(2)
            else:
                return {
                    "task_id": "",
                    "submitted": False,
                    "success": False,
                    "take_number": take_number,
                    "seed": seed,
                    "retries": attempt,
                    "error": str(e),
                    "output_path": output_path or ""
                }


def merge_videos(video_paths, output_path, transition="fade", transition_dur=0.5):
    """
    Merge multiple video files using FFmpeg.
    Uses concat demuxer for simple cuts, xfade for transitions.
    """
    if not video_paths:
        return {"success": False, "error": "No videos to merge"}

    ffmpeg = "ffmpeg"
    # Try imageio_ffmpeg as fallback
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    if len(video_paths) == 1:
        # Single file, just copy
        subprocess.run(["cp", video_paths[0], output_path], check=True)
        return {"success": True, "output": output_path, "merged_count": 1}

    # Build concat file list
    concat_file = output_path + ".txt"
    with open(concat_file, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_path
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Cleanup
    try:
        os.remove(concat_file)
    except OSError:
        pass

    return {
        "success": r.returncode == 0,
        "output": output_path,
        "error": r.stderr[-500:] if r.returncode != 0 else "",
        "merged_count": len(video_paths)
    }


def download_video(video_url, output_path):
    """Download a video from URL to local path (via curl)."""
    try:
        download_video_url(video_url, output_path)
        return os.path.exists(output_path)
    except Exception:
        cmd = ["curl", "-s", "-L", "-o", output_path, video_url, "--max-time", "300"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=310)
        return r.returncode == 0 and os.path.exists(output_path)
