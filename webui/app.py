#!/usr/bin/env python3
"""Drama Studio WebUI — complete short drama production pipeline."""
import json
import os
import sys
import sqlite3
import threading
import requests
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Add parent to path for pipeline imports
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    create_project, get_project, list_projects, update_project_script,
    add_character, update_character_view, update_character_seed, update_character_prompt, get_characters,
    add_shot, update_shot_prompt, update_shot_video, get_shots,
    add_video_task, update_video_task, get_video_task,
    add_scene, update_scene_image, get_scenes, delete_scene, update_shot_scene,
    get_db
)
from pipelines.script_writer import generate_script, optimize_prompt
from pipelines.character_gen import check_comfyui, check_wan, generate_character_views, generate_character_views_comfyui, generate_character_views_wan
from pipelines.shot_planner import extract_shots_from_script, build_shot_prompts
from pipelines.continuity import build_continuity_context, check_shot_coherence, inject_continuity_constraints
from pipelines.signoss_upload import is_configured as signoss_ready, upload_character_views
from pipelines.video_gen import submit_video, merge_videos
from pipelines.scene_gen import generate_scene_full

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
        logline=data.get("logline", "")
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
    worldbuilding = data.get("worldbuilding", "")
    synopsis = data.get("synopsis", "")

    if not topic:
        return jsonify({"error": "topic is required"}), 400

    try:
        script = generate_script(topic, genre, episode_count, duration, worldbuilding, synopsis)
    except Exception as e:
        return jsonify({"error": f"Script generation failed: {str(e)}"}), 500

    # If no project, create one
    if not project_id:
        project_id = create_project(
            name=script.get("title", topic),
            genre=script.get("genre", genre),
            logline=script.get("logline", "")
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

    # Build character name -> DB id mapping
    char_name_to_id = {}
    for c in characters:
        char_name_to_id[c["name"]] = c["id"]

    # Import shots
    shots = extract_shots_from_script(script)
    shot_records = []
    for s in shots:
        shot_char_ids = []
        for cname in s.get("characters", []):
            cid = char_name_to_id.get(cname)
            if cid:
                shot_char_ids.append(cid)
        sid = add_shot(
            project_id=project_id,
            episode=s["episode"],
            shot_number=s["shot_number"],
            scene_desc=s["scene_desc"],
            prompt_raw=s["scene_desc"],
            model="happyhorse-1.1-r2v",
            duration=s.get("duration_sec", 8),
            character_ids=json.dumps(shot_char_ids)
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

    results = {}

    def _gen_one(char):
        try:
            views_map = generate_character_views(
                proj["name"], char["name"], char["description"], views, backend=backend
            )
            # Auto-upload to OSS if configured
            oss_urls = {}
            if signoss_ready():
                oss_urls = upload_character_views(proj["name"], char["name"], views_map)

            for view_type, local_path in views_map.items():
                oss_url = oss_urls.get(view_type, "")
                update_character_view(char["id"], view_type, local_path, oss_url)
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


@app.route("/api/characters", methods=["POST"])
def api_create_character():
    """Manually add a character to a project."""
    data = request.json
    project_id = data.get("project_id")
    name = data.get("name", "").strip()
    role_type = data.get("role_type", "human")
    description = data.get("description", "")
    if not project_id or not name:
        return jsonify({"error": "project_id and name required"}), 400
    cid = add_character(int(project_id), name, role_type, description)
    return jsonify({"id": cid, "name": name, "role_type": role_type, "description": description})


@app.route("/api/characters/<int:char_id>", methods=["DELETE"])
def api_delete_character(char_id):
    """Delete a character."""
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "drama.db"))
    conn.execute("DELETE FROM characters WHERE id=?", (char_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/characters/prompt", methods=["POST"])
def api_update_character_prompt():
    """Update a character's user-editable generation prompt."""
    data = request.json
    char_id = data.get("character_id")
    prompt_raw = data.get("prompt_raw", "")
    if not char_id:
        return jsonify({"error": "character_id required"}), 400
    update_character_prompt(char_id, prompt_raw)
    return jsonify({"success": True, "character_id": char_id, "prompt_raw": prompt_raw})


@app.route("/api/characters/regenerate", methods=["POST"])
def api_regenerate_character():
    """Regenerate views for a single character with custom model/prompt/seed."""
    data = request.json
    char_id = data.get("character_id")
    backend = data.get("backend", "comfyui")
    model = data.get("model", "")  # custom checkpoint name
    prompt_override = data.get("prompt", "")  # custom prompt
    seed = data.get("seed")  # optional fixed seed
    views = data.get("views", ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"])

    # Find the character
    char = None
    proj = None
    for p in list_projects():
        chars = get_characters(p["id"])
        for c in chars:
            if c["id"] == char_id:
                char = c
                proj = p
                break
        if char:
            break

    if not char or not proj:
        return jsonify({"error": "Character not found"}), 404

    if backend == "wan":
        if not check_wan():
            return jsonify({"error": "Wan2.7 API key not configured"}), 503
    else:
        if not check_comfyui():
            return jsonify({"error": "ComfyUI not reachable"}), 503

    try:
        views_map = generate_character_views(
            proj["name"], char["name"], char["description"], views,
            backend=backend, model=model, prompt_override=prompt_override, seed=seed
        )
        oss_urls = {}
        if signoss_ready():
            oss_urls = upload_character_views(proj["name"], char["name"], views_map)

        for view_type, local_path in views_map.items():
            oss_url = oss_urls.get(view_type, "")
            update_character_view(char["id"], view_type, local_path, oss_url)

        # Save seed to DB
        if seed is not None:
            from database import update_character_seed
            update_character_seed(char["id"], seed)

        return jsonify({
            "success": True,
            "character": char["name"],
            "views": views_map,
            "oss": oss_urls,
            "seed": seed
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/models", methods=["GET"])
def api_list_models():
    """List available checkpoints from ComfyUI."""
    try:
        r = requests.get(f"http://192.168.0.30:8188/object_info", timeout=5)
        info = r.json()
        ckpt_info = info.get("CheckpointLoaderSimple", {})
        input_info = ckpt_info.get("input", {}).get("required", {})
        ckpt_input = input_info.get("ckpt_name", [])
        # ckpt_input is [list_of_names] or [list, default]
        models = ckpt_input[0] if ckpt_input and isinstance(ckpt_input[0], list) else []
        return jsonify({"models": models})
    except Exception as e:
        return jsonify({"error": str(e), "models": []}), 500


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

    # Build character lookup for per-shot filtering
    char_map = {str(c["id"]): c for c in characters}

    # Check which characters have reference images → can use R2V
    chars_with_refs = [c for c in characters if c.get("portrait_oss")]
    scenes = get_scenes(project_id)
    scenes_with_oss = {s["id"]: s for s in scenes if s.get("scene_image_oss")}

    results = []
    for shot in shots:
        # Decide model
        model = "happyhorse-1.1-r2v"
        ref_image = None
        scene_ref_image = None

        # Character ref
        shot_chars = json.loads(shot.get("character_ids", "[]"))
        if shot_chars and chars_with_refs:
            for c in chars_with_refs:
                if str(c["id"]) in [str(x) for x in shot_chars]:
                    model = "happyhorse-1.1-r2v"
                    ref_image = c["portrait_oss"]
                    break

        # Per-shot character context — only characters appearing in THIS shot
        shot_char_context = "\n".join([
            f"{char_map[str(cid)]['name']}: {char_map[str(cid)].get('description', '')}"
            for cid in shot_chars
            if str(cid) in char_map
        ])

        # Scene ref
        sid = shot.get("scene_id")
        if sid and sid in scenes_with_oss:
            scene_ref_image = scenes_with_oss[sid]["scene_image_oss"]
            if not ref_image:  # if no character ref, still switch to R2V for scene
                model = "happyhorse-1.1-r2v"

        # Optimize prompt
        try:
            optimized = optimize_prompt(
                shot.get("scene_desc", ""), model, shot_char_context
            )
        except Exception:
            optimized = shot.get("prompt_raw", "")

        # Update DB
        update_shot_prompt(
            shot["id"], optimized,
            model=model, duration=shot.get("duration", 8),
            ref_image=ref_image,
            scene_ref_image=scene_ref_image
        )

        results.append({
            "id": shot["id"],
            "shot": f"EP{shot['episode']}-S{shot['shot_number']}",
            "model": model,
            "prompt_optimized": optimized,
            "ref_image": ref_image,
            "scene_ref_image": scene_ref_image,
            "duration": shot.get("duration", 8)
        })

    return jsonify({"shots": results})


@app.route("/api/shots/optimize-prompt", methods=["POST"])
def api_optimize_single_prompt():
    """Optimize a single prompt. Supports both direct params and shot_id lookup."""
    data = request.json
    shot_id = data.get("shot_id")
    project_id = data.get("project_id")

    # Direct mode: caller provides shot_desc/model/character_context
    if not shot_id:
        shot_desc = data.get("shot_desc", "")
        model = data.get("model", "happyhorse-1.1-r2v")
        char_context = data.get("character_context", "")
        try:
            optimized = optimize_prompt(shot_desc, model, char_context)
            return jsonify({"prompt": optimized})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # shot_id mode: look up shot from DB, build full context
    conn = get_db()
    row = conn.execute("SELECT * FROM shots WHERE id=? AND project_id=?", (shot_id, project_id)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Shot not found"}), 404

    shot = dict(row)
    shot_desc = shot.get("scene_desc", "") or shot.get("prompt_raw", "")
    model = shot.get("model", "happyhorse-1.1-r2v")

    # Build character context from shot's character_ids
    char_context = ""
    try:
        char_ids = json.loads(shot.get("character_ids", "[]"))
        if char_ids:
            chars = get_characters(project_id)
            char_context = "\\n".join([
                f"{c['name']}: {c.get('description', '')}"
                for c in chars if c["id"] in char_ids
            ])
    except Exception:
        pass

    try:
        optimized = optimize_prompt(shot_desc, model, char_context)
        update_shot_prompt(shot_id, optimized, model=model, duration=shot.get("duration", 8))
        return jsonify({"prompt_optimized": optimized, "id": shot_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Shot CRUD APIs ─────────────────────────────────────
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
    project_id = data.get("project_id")
    if not shot_ids or not project_id:
        return jsonify({"error": "shot_ids and project_id required"}), 400

    characters = get_characters(project_id)
    char_map = {str(c["id"]): c for c in characters}

    results = []
    shots = get_shots(project_id)
    for sid in shot_ids:
        shot = next((s for s in shots if s["id"] == sid), None)
        if not shot:
            results.append({"id": sid, "error": "not found"})
            continue

        # Per-shot character context
        import json
        shot_chars = json.loads(shot.get("character_ids", "[]"))
        shot_char_context = "\n".join([
            f"{char_map[str(cid)]['name']}: {char_map[str(cid)].get('description', '')}"
            for cid in shot_chars if str(cid) in char_map
        ])

        try:
            optimized = optimize_prompt(
                shot.get("scene_desc", ""),
                shot.get("model", "happyhorse-1.1-r2v"),
                shot_char_context
            )
        except Exception:
            optimized = shot.get("prompt_raw", "")
        update_shot_prompt(sid, optimized)
        results.append({"id": sid, "prompt_optimized": optimized})
    return jsonify({"shots": results})


DB_PATH = os.path.join(os.path.dirname(__file__), "drama.db")

@app.route("/api/shots/insert", methods=["POST"])
def api_insert_shot():
    """Insert a new shot at a specific position."""
    data = request.json
    project_id = data.get("project_id")
    episode = data.get("episode", 1)
    after_shot_id = data.get("after_shot_id")
    shot_number = data.get("shot_number")
    scene_desc = data.get("scene_desc", "新分镜")
    duration = data.get("duration", 8)
    model = data.get("model", "happyhorse-1.1-r2v")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    if shot_number is None:
        existing = get_shots(project_id, episode=episode)
        if after_shot_id:
            target = next((s for s in existing if s["id"] == after_shot_id), None)
            shot_number = (target["shot_number"] + 0.5) if target else len(existing) + 1
        else:
            shot_number = 0.5

    sid = add_shot(
        project_id=project_id,
        episode=episode,
        shot_number=shot_number,
        scene_desc=scene_desc,
        prompt_raw=scene_desc,
        model=model,
        duration=duration
    )

    # Renumber
    shots = get_shots(project_id, episode=episode)
    conn = sqlite3.connect(DB_PATH)
    for i, s in enumerate(sorted(shots, key=lambda x: x["shot_number"])):
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

    if not shot_id:
        return jsonify({"error": "shot_id required"}), 400

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT episode FROM shots WHERE id=?", (shot_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Shot not found"}), 404

    ep = row[0]
    conn.execute("DELETE FROM shots WHERE id=?", (shot_id,))
    conn.commit()

    # Renumber
    rows = conn.execute(
        "SELECT id FROM shots WHERE project_id=? AND episode=? ORDER BY shot_number",
        (project_id, ep)
    ).fetchall()
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

    conn = sqlite3.connect(DB_PATH)
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


# ─── Continuity APIs ─────────────────────────────────────
@app.route("/api/shots/continuity-check", methods=["POST"])
def api_continuity_check():
    """LLM-powered coherence review across all shots."""
    data = request.json
    project_id = data.get("project_id")

    shots = get_shots(project_id)
    characters = get_characters(project_id)

    if len(shots) < 2:
        return jsonify({"issues": [], "message": "少于2个镜头，无需连续性检查"})

    # Build continuity context
    char_list = [{"name": c["name"], "description": c.get("description", "")} for c in characters]
    continuity_chain = build_continuity_context(shots, char_list)

    # LLM coherence review
    issues = check_shot_coherence(shots, continuity_chain)

    return jsonify({
        "shot_count": len(shots),
        "issues": issues,
        "ok": len([i for i in issues if i.get("severity") == "high"]) == 0
    })


@app.route("/api/shots/continuity-inject", methods=["POST"])
def api_continuity_inject():
    """Inject continuity constraints into all shot prompts."""
    data = request.json
    project_id = data.get("project_id")

    shots = get_shots(project_id)
    characters = get_characters(project_id)

    if not shots:
        return jsonify({"error": "No shots found"}), 400

    char_list = [{"name": c["name"], "description": c.get("description", "")} for c in characters]
    continuity_chain = build_continuity_context(shots, char_list)
    enriched = inject_continuity_constraints(shots, continuity_chain, char_list)

    # Update DB with enriched prompts
    results = []
    for es in enriched:
        shot = es  # enriched shot dict
        constraints = es.get("continuity_constraints", [])
        ctx = es.get("continuity_context", "")

        # Build enriched prompt
        base_prompt = shot.get("prompt_optimized") or shot.get("prompt_raw") or shot.get("scene_desc", "")
        if constraints:
            base_prompt += "，" + "，".join(constraints)

        # Update DB
        update_shot_prompt(
            shot["id"], base_prompt,
            model=shot.get("model"), duration=shot.get("duration"),
            ref_image=shot.get("ref_image")
        )

        results.append({
            "id": shot["id"],
            "shot": f"EP{shot.get('episode',1)}-S{shot.get('shot_number',1)}",
            "constraints": constraints,
            "context": ctx[:80] if ctx else ""
        })

    return jsonify({"enriched": results, "total": len(results)})


@app.route("/api/shots/fix-all", methods=["POST"])
def api_fix_all():
    """One-click: continuity check → fix → upload refs → OSS URLs."""
    data = request.json
    project_id = data.get("project_id")

    proj = get_project(project_id)
    if not proj:
        return jsonify({"error": "Project not found"}), 404

    shots = get_shots(project_id)
    characters = get_characters(project_id)

    if not shots:
        return jsonify({"error": "No shots found"}), 400

    results = {"steps": [], "issues_found": 0, "issues_fixed": 0, "oss_uploaded": 0}

    # Step 1: Continuity check
    char_list = [{"name": c["name"], "description": c.get("description", "")} for c in characters]
    continuity_chain = build_continuity_context(shots, char_list)

    if len(shots) >= 2:
        issues = check_shot_coherence(shots, continuity_chain)
        high_issues = [i for i in issues if i.get("severity") == "high"]
        results["steps"].append({
            "step": "continuity_check",
            "total_issues": len(issues),
            "high_issues": len(high_issues),
            "issues": issues
        })
        results["issues_found"] = len(high_issues)
    else:
        results["steps"].append({"step": "continuity_check", "message": "单镜头，跳过"})

    # Step 2: Inject continuity constraints
    enriched = inject_continuity_constraints(shots, continuity_chain, char_list)
    for es in enriched:
        constraints = es.get("continuity_constraints", [])
        base_prompt = es.get("prompt_optimized") or es.get("prompt_raw") or es.get("scene_desc", "")
        if constraints:
            base_prompt += "，" + "，".join(constraints)
        update_shot_prompt(
            es["id"], base_prompt,
            model=es.get("model"), duration=es.get("duration"),
            ref_image=es.get("ref_image")
        )
    results["steps"].append({"step": "continuity_inject", "shots_enriched": len(enriched)})
    results["issues_fixed"] = len(enriched)

    # Step 3: Upload character refs to OSS
    if signoss_ready():
        oss_count = 0
        for char in characters:
            views_map = {}
            for view in ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"]:
                local_key = f"{view}_local"
                if char.get(local_key) and os.path.exists(char[local_key]):
                    views_map[view] = char[local_key]

            if views_map:
                oss_urls = upload_character_views(proj["name"], char["name"], views_map)
                for view_type, url in oss_urls.items():
                    update_character_view(char["id"], view_type, char.get(f"{view_type}_local", ""), url)
                    oss_count += 1

        results["steps"].append({"step": "oss_upload", "uploaded": oss_count})
        results["oss_uploaded"] = oss_count

        # Step 4: Update shot ref_images to OSS URLs
        chars_refreshed = get_characters(project_id)
        for shot in shots:
            shot_chars = json.loads(shot.get("character_ids", "[]"))
            for c in chars_refreshed:
                if str(c["id"]) in [str(x) for x in shot_chars]:
                    if c.get("portrait_oss"):
                        update_shot_prompt(
                            shot["id"],
                            shot.get("prompt_optimized") or shot.get("prompt_raw", ""),
                            model="happyhorse-1.1-r2v",
                            ref_image=c["portrait_oss"]
                        )
                        break
        results["steps"].append({"step": "ref_update", "message": "分镜参考图已切换为OSS URL"})
    else:
        results["steps"].append({"step": "oss_upload", "message": "SignOSS未配置，跳过"})

    return jsonify(results)


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

    prompt = data.get("prompt") or shot.get("prompt_optimized") or shot.get("prompt_raw") or shot.get("scene_desc", "")
    model = data.get("model") or shot.get("model", "happyhorse-1.1-r2v")
    duration = data.get("duration") or shot.get("duration", 8)
    resolution = data.get("resolution") or shot.get("resolution", "1920*1080")

    # Collect ref images: character OSS + scene OSS
    ref_images = []
    scene_ref = shot.get("scene_ref_image", "")
    if scene_ref:
        ref_images.append(scene_ref)
    char_ref = data.get("ref_image") or shot.get("ref_image", "")
    if char_ref:
        ref_images.append(char_ref)

    # Slate: calculate take number, generate seed
    import random
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT COUNT(*) FROM video_tasks WHERE shot_id=?", (shot_id,)).fetchone()
    take_number = (row[0] if row else 0) + 1
    seed = data.get("seed") or random.randint(1, 999999)
    conn.close()

    # Unique filename: EP01_S01_V180_T02_s42.mp4
    proj = get_project(data.get("project_id", 0))
    proj_name = proj.get("name", "unnamed") if proj else "unnamed"
    safe_name = proj_name.replace(" ", "_").replace("：", "_").replace(":", "_")[:20]
    filename = f"EP{shot['episode']:02d}_S{shot['shot_number']:02d}_V{shot_id}_T{take_number:02d}_s{seed}.mp4"
    output_path = os.path.join(str(ASSETS_DIR), "videos", safe_name, filename)

    try:
        result = submit_video(
            prompt=prompt,
            model=model,
            size=resolution,
            duration=duration,
            ref_images=ref_images if ref_images else None,
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
                       resolution=resolution, filename=filename)

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


# ─── Scene APIs ──────────────────────────────────────────
@app.route("/api/scenes", methods=["GET"])
def api_list_scenes():
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400
    return jsonify(get_scenes(int(project_id)))


@app.route("/api/scenes", methods=["POST"])
def api_create_scene():
    data = request.json
    project_id = data.get("project_id")
    name = data.get("name", "").strip()
    description = data.get("description", "")
    if not project_id or not name:
        return jsonify({"error": "project_id and name required"}), 400
    sid = add_scene(int(project_id), name, description)
    return jsonify({"id": sid, "name": name})


@app.route("/api/scenes/<int:scene_id>", methods=["DELETE"])
def api_delete_scene(scene_id):
    delete_scene(scene_id)
    return jsonify({"ok": True})


@app.route("/api/scenes/<int:scene_id>/assign", methods=["POST"])
def api_assign_scene_to_shot(scene_id):
    """Assign a scene to a shot — copies scene_ref to the shot."""
    data = request.json
    shot_id = data.get("shot_id")
    conn = None
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "drama.db"))
        conn.row_factory = sqlite3.Row
        scene = conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        if not scene:
            return jsonify({"error": "Scene not found"}), 404
        update_shot_scene(shot_id, scene_id, scene["scene_image_oss"] or "")
        return jsonify({"ok": True, "scene_name": scene["name"], "scene_image": scene["scene_image_oss"]})
    finally:
        if conn:
            conn.close()


@app.route("/api/scenes/<int:scene_id>/generate", methods=["POST"])
def api_generate_scene_image(scene_id):
    """Generate concept art for a scene → upload to OSS."""
    data = request.json
    project_name = data.get("project_name", "default")

    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "drama.db"))
    conn.row_factory = sqlite3.Row
    scene = conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
    conn.close()

    if not scene:
        return jsonify({"error": "Scene not found"}), 404

    try:
        local_path, oss_url, expires = generate_scene_full(
            scene["name"], scene["description"] or scene["name"], project_name
        )
        update_scene_image(scene_id, local_path, oss_url, expires)
        return jsonify({
            "ok": True,
            "scene_id": scene_id,
            "local_path": local_path,
            "oss_url": oss_url,
            "expires_at": expires,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Status Check ───────────────────────────────────────
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "comfyui": check_comfyui(),
        "wan_image_pro": check_wan(),
        "signoss": signoss_ready(),
        "assets_dir": str(ASSETS_DIR),
    })


# ─── Asset Serving ──────────────────────────────────────
@app.route("/assets/<path:filepath>")
def serve_assets(filepath):
    return send_from_directory(str(ASSETS_DIR), filepath)


# ─── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🎬 Drama Studio WebUI → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
