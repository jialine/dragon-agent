#!/usr/bin/env python3
"""Drama Studio WebUI — complete short drama production pipeline."""
import json
import os
import sys
import threading
import subprocess
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Add parent to path for pipeline imports
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # webui/ for database.py

from database import (
    create_project, get_project, list_projects, update_project_script,
    add_character, update_character_view, get_characters,
    add_shot, update_shot_prompt, update_shot_video, get_shots,
    add_video_task, update_video_task, get_video_task
)
from pipelines.script_writer import generate_script, optimize_prompt
from pipelines.character_gen import check_comfyui, check_wan, check_hy, generate_character_views, generate_character_views_comfyui, generate_character_views_wan, generate_character_views_hy
from pipelines.shot_planner import extract_shots_from_script, build_shot_prompts
from pipelines.video_gen import submit_video, merge_videos

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

BASE_DIR = Path(__file__).parent
ASSETS_DIR = Path("/home/jialine/dragon-agent/assets")


# ─── Frontend ───────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "index.html")


# ─── Project APIs ───────────────────────────────────────
@app.route("/api/projects", methods=["GET"])
def api_list_projects():
    return jsonify(list_projects())


@app.route("/api/projects", methods=["POST"])
def api_create_project():
    data = request.json
    pid = create_project(
        name=data.get("name", "未命名项目"),
        genre=data.get("genre", ""),
        logline=data.get("logline", ""),
        worldview=data.get("worldview", ""),
        synopsis=data.get("synopsis", "")
    )
    return jsonify({"id": pid, "name": data.get("name")})


@app.route("/api/projects/<int:pid>", methods=["GET"])
def api_get_project(pid):
    proj = get_project(pid)
    if not proj:
        return jsonify({"error": "Not found"}), 404
    proj["characters"] = get_characters(pid)
    proj["shots"] = get_shots(pid)
    return jsonify(proj)


# ─── Script APIs ────────────────────────────────────────
@app.route("/api/script/generate", methods=["POST"])
def api_generate_script():
    """Generate script via LLM, parse characters and shots into DB."""
    data = request.json
    project_id = data.get("project_id")
    topic = data.get("topic", "")
    genre = data.get("genre", "科幻")
    episode_count = data.get("episode_count", 1)
    duration = data.get("duration_per_ep", 120)
    worldview = data.get("worldview", "")
    synopsis = data.get("synopsis", "")

    if not topic:
        return jsonify({"error": "topic is required"}), 400

    try:
        script = generate_script(topic, genre, episode_count, duration, worldview=worldview, synopsis=synopsis)
    except Exception as e:
        return jsonify({"error": f"Script generation failed: {str(e)}"}), 500

    # If no project, create one
    if not project_id:
        project_id = create_project(
            name=script.get("title", topic),
            genre=script.get("genre", genre),
            logline=script.get("logline", ""),
            worldview=script.get("worldbuilding", worldview),
            synopsis=script.get("synopsis", synopsis)
        )

    # Store script
    update_project_script(project_id, json.dumps(script, ensure_ascii=False, indent=2))

    # Import characters
    characters = []
    for c in script.get("characters", []):
        cid = add_character(
            project_id=project_id,
            name=c.get("name", ""),
            role_type=c.get("role_type", "human"),
            description=c.get("description", ""),
            traits=c.get("traits", {})
        )
        characters.append({"id": cid, **c})

    # Import shots
    shots = extract_shots_from_script(script)
    shot_records = []
    for s in shots:
        sid = add_shot(
            project_id=project_id,
            episode=s["episode"],
            shot_number=s["shot_number"],
            scene_desc=s["scene_desc"],
            prompt_raw=s["scene_desc"],
            model="happyhorse-1.1-t2v",
            duration=s.get("duration_sec", 8)
        )
        shot_records.append({"id": sid, **s})

    return jsonify({
        "project_id": project_id,
        "title": script.get("title"),
        "logline": script.get("logline"),
        "character_count": len(characters),
        "shot_count": len(shot_records),
        "script": script
    })


# ─── Character APIs ─────────────────────────────────────
@app.route("/api/characters/generate", methods=["POST"])
def api_generate_characters():
    """Generate 4-view portraits for all characters in a project."""
    data = request.json
    project_id = data.get("project_id")
    views = data.get("views", ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"])
    backend = data.get("backend", "comfyui")  # 'comfyui' or 'wan'

    proj = get_project(project_id)
    if not proj:
        return jsonify({"error": "Project not found"}), 404

    # Check backend availability
    if backend == "wan":
        if not check_wan():
            return jsonify({"error": "Wan2.7 API key not configured"}), 503
    else:
        if not check_comfyui():
            return jsonify({"error": "ComfyUI (192.168.0.30:8188) is not reachable"}), 503

    characters = get_characters(project_id)
    if not characters:
        return jsonify({"error": "No characters found in project"}), 400

    # Filter by character_name if provided
    char_name = data.get("character_name")
    if char_name:
        characters = [c for c in characters if c["name"] == char_name]
        if not characters:
            return jsonify({"error": f"Character '{char_name}' not found"}), 404

    results = {}

    def _gen_one(char):
        try:
            views_map = generate_character_views(
                proj["name"], char["name"], char["description"], views, backend=backend,
                model="sd_xl_base_1.0.safetensors"
            )
            for view_type, local_path in views_map.items():
                update_character_view(char[0], view_type, local_path)
            results[char["name"]] = {"status": "done", "views": views_map}
        except Exception as e:
            results[char["name"]] = {"status": "failed", "error": str(e)}

    # Sequential generation (ComfyUI queues jobs, but we track per-character)
    for c in characters:
        _gen_one(c)

    return jsonify({"characters": results})


@app.route("/api/characters/<int:project_id>", methods=["GET"])
def api_get_characters(project_id):
    return jsonify(get_characters(project_id))


# ─── SignOSS Upload ─────────────────────────────────────
SIGNOSS_URL = "https://api.andlapi.cn/signoss/upload"
# SIGNOSS key from happyhorse_api.py (separate from global andlapi key)
_HH_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
sys.path.insert(0, _HH_DIR)
try:
    from happyhorse_api import SIGNOSS_KEY as _SIGNOSS_KEY
    SIGNOSS_KEY = _SIGNOSS_KEY
except ImportError:
    SIGNOSS_KEY = os.environ.get("SIGNOSS_API_KEY", "")
sys.path.pop(0)


def signoss_upload_file(filepath, category="characters"):
    """Upload a single file to SignOSS, return public URL."""
    if not SIGNOSS_KEY:
        raise RuntimeError("SIGNOSS_API_KEY not configured")
    cmd = ["curl", "-s", "-k", "--max-time", "60",
           "-X", "POST", SIGNOSS_URL,
           "-H", f"X-API-Key: {SIGNOSS_KEY}",
           "-F", f"category={category}",
           "-F", f"file=@{filepath};filename={os.path.basename(filepath)}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=65)
    if r.returncode != 0:
        raise RuntimeError(f"SignOSS upload failed: {r.stderr[:200]}")
    result = json.loads(r.stdout)
    if not result.get("success"):
        raise RuntimeError(f"SignOSS error: {result.get('error', 'unknown')}")
    return result["files"][0]["url"]


@app.route("/api/characters/upload-oss", methods=["POST"])
def api_upload_characters_oss():
    """Upload all character images for a project to SignOSS and store OSS URLs."""
    data = request.json
    project_id = data.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    proj = get_project(project_id)
    if not proj:
        return jsonify({"error": "Project not found"}), 404

    characters = get_characters(project_id)
    if not characters:
        return jsonify({"error": "No characters found"}), 400

    views = ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"]
    proj_name = proj["name"]
    assets_base = os.path.join(ASSETS_DIR, "characters", proj_name)

    import sqlite3
    from datetime import datetime, timedelta
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "drama.db"))
    expires_at = (datetime.now() + timedelta(days=30)).isoformat()

    results = {}
    for char in characters:
        char_name = char["name"]
        char_results = {}
        for view in views:
            img_path = os.path.join(assets_base, char_name, f"{view}.png")
            if not os.path.exists(img_path):
                alt_path = os.path.join(assets_base, f"{char_name}_{view}", f"{view}.png")
                if os.path.exists(alt_path):
                    img_path = alt_path
                else:
                    char_results[view] = {"status": "not_found"}
                    continue

            try:
                url = signoss_upload_file(img_path, category="characters")
                col_map = {
                    "portrait": ("portrait_local", "portrait_oss"),
                    "fullbody_front": ("fullbody_front_local", "fullbody_front_oss"),
                    "fullbody_side": ("fullbody_side_local", "fullbody_side_oss"),
                    "fullbody_back": ("fullbody_back_local", "fullbody_back_oss"),
                }
                local_col, oss_col = col_map[view]
                conn.execute(
                    f"UPDATE characters SET {local_col}=?, {oss_col}=?, oss_expires_at=?, status='done' WHERE id=?",
                    (img_path, url, expires_at, char["id"])
                )
                conn.commit()
                char_results[view] = {"status": "uploaded", "url": url}
            except Exception as e:
                char_results[view] = {"status": "failed", "error": str(e)[:200]}

        results[char_name] = char_results

    conn.close()
    return jsonify({
        "project_id": project_id,
        "expires_at": expires_at,
        "characters": results
    })


# ─── Shot / Prompt APIs ─────────────────────────────────
@app.route("/api/shots/plan", methods=["POST"])
def api_plan_shots():
    """Auto-assign models (T2V/R2V) and optimize prompts for all shots."""
    data = request.json
    project_id = data.get("project_id")

    shots = get_shots(project_id)
    characters = get_characters(project_id)

    if not shots:
        return jsonify({"error": "No shots found"}), 400

    # Build character context for prompt optimization
    char_context = "\n".join([
        f"{c['name']}: {c.get('description', '')}"
        for c in characters
    ])

    # Check which characters have reference images → can use R2V
    chars_with_refs = [c for c in characters if c.get("portrait_oss")]

    results = []
    for shot in shots:
        # Decide model
        model = "happyhorse-1.1-t2v"
        ref_image = None
        shot_chars = json.loads(shot.get("character_ids", "[]"))
        if shot_chars and chars_with_refs:
            # Use first character's portrait as ref
            for c in chars_with_refs:
                if str(c["id"]) in [str(x) for x in shot_chars]:
                    model = "happyhorse-1.1-r2v"
                    ref_image = c["portrait_oss"]
                    break

        # Optimize prompt
        try:
            optimized = optimize_prompt(
                shot.get("scene_desc", ""), model, char_context
            )
        except Exception:
            optimized = shot.get("prompt_raw", "")

        # Update DB
        update_shot_prompt(
            shot["id"], optimized,
            model=model, duration=shot.get("duration", 8),
            ref_image=ref_image
        )

        results.append({
            "id": shot["id"],
            "shot": f"EP{shot['episode']}-S{shot['shot_number']}",
            "model": model,
            "prompt_optimized": optimized,
            "ref_image": ref_image,
            "duration": shot.get("duration", 8)
        })

    return jsonify({"shots": results})


@app.route("/api/shots/optimize-prompt", methods=["POST"])
def api_optimize_single_prompt():
    """Optimize a single prompt."""
    data = request.json
    shot_desc = data.get("shot_desc", "")
    model = data.get("model", "happyhorse-1.1-t2v")
    char_context = data.get("character_context", "")

    try:
        optimized = optimize_prompt(shot_desc, model, char_context)
        return jsonify({"prompt": optimized})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ─── Shots listing (with episode filter) ─────────────────
@app.route("/api/shots", methods=["GET"])
def api_list_shots():
    """List shots for a project, optionally filtered by episode."""
    project_id = request.args.get("project_id", type=int)
    episode = request.args.get("episode", type=int)
    if not project_id:
        return jsonify({"error": "project_id required"}), 400
    shots = get_shots(project_id, episode=episode)
    return jsonify({"shots": shots})


@app.route("/api/shots/optimize-batch", methods=["POST"])
def api_optimize_batch():
    """Optimize prompts for specific shot IDs."""
    data = request.json
    shot_ids = data.get("shot_ids", [])
    if not shot_ids:
        return jsonify({"error": "shot_ids required"}), 400
    project_id = data.get("project_id")
    characters = get_characters(project_id) if project_id else []
    char_context = "\n".join([
        f"{c['name']}: {c.get('description', '')}"
        for c in characters
    ])
    results = []
    for sid in shot_ids:
        shots = get_shots(project_id)
        shot = next((s for s in shots if s["id"] == sid), None)
        if not shot:
            results.append({"id": sid, "error": "not found"})
            continue
        try:
            optimized = optimize_prompt(
                shot.get("scene_desc", ""),
                shot.get("model", "happyhorse-1.1-t2v"),
                char_context
            )
        except Exception:
            optimized = shot.get("prompt_raw", "")
        update_shot_prompt(sid, optimized)
        results.append({
            "id": sid,
            "prompt_optimized": optimized,
        })
    return jsonify({"shots": results})


@app.route("/api/shots/insert", methods=["POST"])
def api_insert_shot():
    """Insert a new shot at a specific position."""
    data = request.json
    project_id = data.get("project_id")
    episode = data.get("episode", 1)
    after_shot_id = data.get("after_shot_id")  # insert after this shot, or at beginning if None
    shot_number = data.get("shot_number")
    scene_desc = data.get("scene_desc", "新分镜")
    duration = data.get("duration", 8)
    model = data.get("model", "happyhorse-1.1-t2v")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    # Determine shot_number if not provided
    if shot_number is None:
        if after_shot_id:
            existing = get_shots(project_id, episode=episode)
            target = next((s for s in existing if s["id"] == after_shot_id), None)
            shot_number = (target["shot_number"] + 0.5) if target else len(existing) + 1
        else:
            existing = get_shots(project_id, episode=episode)
            shot_number = 0.5  # insert at beginning

    sid = add_shot(
        project_id=project_id,
        episode=episode,
        shot_number=shot_number,
        scene_desc=scene_desc,
        prompt_raw=scene_desc,
        model=model,
        duration=duration
    )
    # Renumber shots in this episode
    shots = get_shots(project_id, episode=episode)
    for i, s in enumerate(sorted(shots, key=lambda x: x["shot_number"])):
        update_shot_prompt(s["id"], s.get("prompt_optimized", ""), model=s.get("model"), duration=s.get("duration"))
        # Use raw SQL to update shot_number
        import sqlite3
        conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "drama.db"))
        conn.execute("UPDATE shots SET shot_number=? WHERE id=?", (i+1, s["id"]))
        conn.commit()
        conn.close()

    return jsonify({"id": sid, "shot_number": shot_number})


@app.route("/api/shots/delete", methods=["POST"])
def api_delete_shot():
    """Delete a shot and renumber remaining."""
    data = request.json
    shot_id = data.get("shot_id")
    project_id = data.get("project_id")
    episode = data.get("episode")

    if not shot_id:
        return jsonify({"error": "shot_id required"}), 400

    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "drama.db"))
    # Get shot info before deleting
    row = conn.execute("SELECT episode FROM shots WHERE id=?", (shot_id,)).fetchone()
    ep = row[0] if row else episode
    conn.execute("DELETE FROM shots WHERE id=?", (shot_id,))
    conn.commit()

    # Renumber
    rows = conn.execute("SELECT id FROM shots WHERE project_id=? AND episode=? ORDER BY shot_number",
                        (project_id, ep)).fetchall()
    for i, r in enumerate(rows):
        conn.execute("UPDATE shots SET shot_number=? WHERE id=?", (i+1, r[0]))
    conn.commit()
    conn.close()

    return jsonify({"deleted": shot_id})


@app.route("/api/shots/update", methods=["POST"])
def api_update_shot():
    """Update shot fields: duration, model, prompt, scene_desc."""
    data = request.json
    shot_id = data.get("shot_id")
    if not shot_id:
        return jsonify({"error": "shot_id required"}), 400

    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "drama.db"))
    updates = []
    params = []
    for field in ["duration", "model", "prompt_optimized", "prompt_raw", "scene_desc"]:
        if field in data:
            updates.append(f"{field}=?")
            params.append(data[field])
    if updates:
        params.append(shot_id)
        conn.execute(f"UPDATE shots SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    conn.close()
    return jsonify({"updated": shot_id})


# ─── Video APIs ─────────────────────────────────────────
@app.route("/api/video/submit", methods=["POST"])
def api_submit_video():
    """Submit a single shot for video generation."""
    data = request.json
    shot_id = data.get("shot_id")

    # Get shot from DB
    shots = get_shots(data.get("project_id", 0))
    shot = next((s for s in shots if s["id"] == shot_id), None)
    if not shot:
        return jsonify({"error": "Shot not found"}), 404

    prompt = shot.get("prompt_optimized") or shot.get("prompt_raw") or shot.get("scene_desc", "")
    model = data.get("model") or shot.get("model", "happyhorse-1.1-t2v")
    duration = data.get("duration") or shot.get("duration", 8)
    ref_image = data.get("ref_image") or shot.get("ref_image", "")
    resolution = data.get("resolution") or shot.get("resolution", "1920*1080")

    # Build consistent filename with version tracking
    project = get_project(data.get("project_id"))
    project_name = project.get("name", "未命名").replace(" ", "_") if project else "unknown"
    proj_video_dir = os.path.join(str(ASSETS_DIR), "videos", project_name)
    os.makedirs(proj_video_dir, exist_ok=True)
    
    import glob, random
    seed = random.randint(100000, 999999)
    prefix = f"{project_name}_EP{shot['episode']:02d}_S{shot['shot_number']:02d}"
    pattern = os.path.join(proj_video_dir, f"{prefix}_V*_T*_s*.mp4")
    existing = sorted(glob.glob(pattern))
    version = len(existing) + 1
    take_number = 1
    
    output_path = os.path.join(
        proj_video_dir,
        f"{prefix}_V{version:03d}_T{take_number:02d}_s{seed}.mp4"
    )

    try:
        result = submit_video(
            prompt=prompt,
            model=model,
            size=resolution,
            duration=duration,
            ref_image=ref_image if ref_image else None,
            output_path=output_path
        )
    except Exception as e:
        return jsonify({"error": str(e), "shot_id": shot_id}), 500

    # Update DB
    update_shot_video(
        shot_id,
        task_id=result.get("task_id", ""),
        video_local=result.get("local_path", ""),
        status="done" if result.get("success") else "failed"
    )

    if result.get("task_id"):
        add_video_task(shot_id, result["task_id"], model, 
                       take_number=take_number, seed=seed,
                       filename=os.path.basename(output_path),
                       resolution=resolution)

    return jsonify({
        "shot_id": shot_id,
        "task_id": result.get("task_id"),
        "success": result.get("success"),
        "local_path": result.get("local_path"),
        "output": result.get("output", "")[-500:]
    })


@app.route("/api/video/submit-batch", methods=["POST"])
def api_submit_batch():
    """Submit all shots in a project for video generation (sequential)."""
    data = request.json
    project_id = data.get("project_id")
    shots = get_shots(project_id)

    if not shots:
        return jsonify({"error": "No shots found"}), 400

    results = []
    for shot in shots:
        # Call submit endpoint internally
        with app.test_client() as client:
            r = client.post("/api/video/submit", json={
                "shot_id": shot["id"],
                "project_id": project_id
            })
            results.append(r.get_json())

    return jsonify({"results": results, "total": len(results)})


@app.route("/api/video/status/<task_id>", methods=["GET"])
def api_video_status(task_id):
    task = get_video_task(task_id)
    if not task:
        return jsonify({"task_id": task_id, "status": "unknown"})
    return jsonify(dict(task))


@app.route("/api/video/merge", methods=["POST"])
def api_merge_videos():
    """Merge all completed shot videos into one."""
    data = request.json
    project_id = data.get("project_id")

    shots = get_shots(project_id)
    video_paths = [s["video_local"] for s in shots if s.get("video_local") and os.path.exists(s["video_local"])]

    if not video_paths:
        return jsonify({"error": "No completed videos to merge"}), 400

    output_path = os.path.join(str(ASSETS_DIR), "videos", f"merged_{project_id}.mp4")
    result = merge_videos(video_paths, output_path)

    return jsonify(result)


# ─── Status Check ───────────────────────────────────────
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "comfyui": check_comfyui(),
        "wan_image_pro": check_wan(),
        "assets_dir": str(ASSETS_DIR),
    })


# ─── Validation & Dashboard ─────────────────────────────
@app.route("/api/shots/validate", methods=["GET"])
def api_validate_shots():
    """Validate DB consistency: check filename ↔ episode/shot match, missing files, orphan tasks."""
    project_id = request.args.get("project_id", type=int)
    episode = request.args.get("episode", type=int)
    
    shots = get_shots(project_id, episode=episode)
    import glob, re
    
    issues = []
    for shot in shots:
        vid = shot.get("video_local", "")
        if not vid:
            if shot.get("status") == "done":
                issues.append({
                    "shot_id": shot["id"],
                    "shot": f"EP{shot['episode']}-S{shot['shot_number']}",
                    "type": "missing_video",
                    "detail": "Status is 'done' but no video_local path"
                })
            continue
        
        # Check file exists
        if not os.path.exists(vid):
            issues.append({
                "shot_id": shot["id"],
                "shot": f"EP{shot['episode']}-S{shot['shot_number']}",
                "type": "file_missing",
                "detail": f"File not found: {vid}"
            })
            continue
        
        # Check filename matches DB metadata
        basename = os.path.basename(vid)
        match = re.match(r'.*_EP(\d+)_S(\d+)_V\d+_T\d+_s\d+\.mp4$', basename)
        if match:
            file_ep, file_shot = int(match.group(1)), int(match.group(2))
            if file_ep != shot["episode"] or file_shot != shot["shot_number"]:
                issues.append({
                    "shot_id": shot["id"],
                    "shot": f"EP{shot['episode']}-S{shot['shot_number']}",
                    "type": "filename_mismatch",
                    "detail": f"DB says EP{shot['episode']}-S{shot['shot_number']} but file is EP{file_ep}-S{file_shot}: {basename}"
                })
    
    # Check duplicates
    from collections import Counter
    eps_shots = [(s["episode"], s["shot_number"]) for s in shots]
    dupes = [k for k, v in Counter(eps_shots).items() if v > 1]
    for ep, sn in dupes:
        issues.append({
            "shot": f"EP{ep}-S{sn}",
            "type": "duplicate",
            "detail": f"Duplicate shot_number {sn} in episode {ep} ({Counter(eps_shots)[(ep,sn)]} entries)"
        })
    
    return jsonify({
        "project_id": project_id,
        "episode": episode,
        "total_shots": len(shots),
        "issues": issues,
        "clean": len(issues) == 0
    })


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """Project dashboard: progress, status breakdown, estimated cost."""
    project_id = request.args.get("project_id", type=int)
    if not project_id:
        return jsonify({"error": "project_id required"}), 400
    
    proj = get_project(project_id)
    if not proj:
        return jsonify({"error": "Project not found"}), 404
    
    shots = get_shots(project_id)
    
    # Status breakdown by episode
    from collections import defaultdict
    episodes = defaultdict(lambda: {"total": 0, "done": 0, "pending": 0, "generating": 0, "failed": 0})
    total_duration = 0
    models_used = defaultdict(int)
    
    for s in shots:
        ep = s["episode"]
        episodes[ep]["total"] += 1
        status = s.get("status", "pending")
        episodes[ep][status] = episodes[ep].get(status, 0) + 1
        total_duration += s.get("duration", 8)
        models_used[s.get("model", "unknown")] += 1
    
    # Estimate cost (rough: ¥0.5/s for T2V, ¥0.8/s for R2V)
    estimated_cost = 0
    for s in shots:
        rate = 0.8 if "r2v" in s.get("model", "") else 0.5
        estimated_cost += rate * s.get("duration", 8)
    
    return jsonify({
        "project": proj["name"],
        "total_shots": len(shots),
        "episodes": {str(k): dict(v) for k, v in sorted(episodes.items())},
        "total_duration_seconds": total_duration,
        "estimated_cost_yuan": round(estimated_cost, 1),
        "models_used": dict(models_used)
    })


# ─── Asset Serving ──────────────────────────────────────
@app.route("/assets/<path:filepath>")
def serve_assets(filepath):
    return send_from_directory(str(ASSETS_DIR), filepath)


# ─── Batch Review ────────────────────────────────────────
@app.route("/api/shots/review", methods=["POST"])
def api_review_shots():
    """Batch approve/reject shots. Rejected shots set to 'pending' for re-generation."""
    data = request.json
    actions = data.get("actions", [])  # [{shot_id: 1, action: "approve"|"reject"}]
    project_id = data.get("project_id")
    
    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "drama.db"))
    
    results = []
    for a in actions:
        sid = a.get("shot_id")
        action = a.get("action")
        if action == "approve":
            conn.execute("UPDATE shots SET status='done' WHERE id=?", (sid,))
            results.append({"shot_id": sid, "action": "approved"})
        elif action == "reject":
            conn.execute("UPDATE shots SET status='pending', video_local='', task_id='' WHERE id=?", (sid,))
            results.append({"shot_id": sid, "action": "rejected"})
    
    conn.commit()
    conn.close()
    return jsonify({"results": results})


@app.route("/api/shots/regenerate-failed", methods=["POST"])
def api_regenerate_failed():
    """Re-submit all failed shots in a project/episode."""
    data = request.json
    project_id = data.get("project_id")
    episode = data.get("episode")
    
    shots = get_shots(project_id, episode=episode)
    failed = [s for s in shots if s.get("status") == "failed"]
    
    if not failed:
        return jsonify({"message": "No failed shots", "regenerated": 0})
    
    results = []
    for shot in failed:
        shot["status"] = "pending"
        update_shot_video(shot["id"], "", "", "pending")
        
        # Re-submit via internal call
        with app.test_client() as client:
            r = client.post("/api/video/submit", json={
                "shot_id": shot["id"],
                "project_id": project_id
            })
            results.append(r.get_json())
    
    return jsonify({
        "regenerated": len(failed),
        "results": results
    })


# ─── Cost Dashboard ──────────────────────────────────────
@app.route("/api/cost", methods=["GET"])
def api_cost():
    """Cost breakdown by project. Reads from andlapi cost.db if available."""
    project_id = request.args.get("project_id", type=int)
    
    try:
        import sqlite3
        cost_db = os.path.join(os.path.dirname(__file__), "..", "..", "dragon_data", "cost.db")
        if os.path.exists(cost_db):
            conn = sqlite3.connect(cost_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM usage_logs ORDER BY timestamp DESC LIMIT 500"
            ).fetchall()
            conn.close()
            total = sum(r["cost"] for r in rows if r["cost"])
            return jsonify({
                "total_cost": round(total, 4),
                "recent_logs": [dict(r) for r in rows[:100]],
                "source": "cost.db"
            })
    except Exception:
        pass
    
    # Fallback: estimate from shots
    if project_id:
        shots = get_shots(project_id)
        estimated = sum(
            (0.8 if "r2v" in s.get("model", "") else 0.5) * s.get("duration", 8)
            for s in shots if s.get("status") == "done"
        )
        return jsonify({
            "estimated_cost_yuan": round(estimated, 1),
            "source": "estimate"
        })
    
    return jsonify({"error": "No cost data available"}), 404


# ─── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🎬 Drama Studio WebUI → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
