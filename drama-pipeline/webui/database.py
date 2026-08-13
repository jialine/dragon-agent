"""SQLite asset & project database for Drama WebUI."""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "drama.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            genre TEXT DEFAULT '',
            logline TEXT DEFAULT '',
            worldview TEXT DEFAULT '',
            synopsis TEXT DEFAULT '',
            script_raw TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            role_type TEXT DEFAULT 'human',       -- human / animal / other
            description TEXT DEFAULT '',
            traits TEXT DEFAULT '{}',             -- JSON: {age, gender, hair, eyes, ...}
            portrait_local TEXT DEFAULT '',       -- local path: front portrait
            fullbody_front_local TEXT DEFAULT '', -- local path: front fullbody
            fullbody_side_local TEXT DEFAULT '',  -- local path: side fullbody
            fullbody_back_local TEXT DEFAULT '',  -- local path: back fullbody
            portrait_oss TEXT DEFAULT '',         -- SignOSS URL for R2V
            fullbody_front_oss TEXT DEFAULT '',
            fullbody_side_oss TEXT DEFAULT '',
            fullbody_back_oss TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',        -- pending / generating / done / failed
            seed INTEGER DEFAULT NULL,             -- last regeneration seed
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS shots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            episode INTEGER DEFAULT 1,
            shot_number INTEGER NOT NULL,
            scene_desc TEXT DEFAULT '',
            character_ids TEXT DEFAULT '[]',     -- JSON array of character IDs
            prompt_raw TEXT DEFAULT '',          -- user/LLM raw prompt
            prompt_optimized TEXT DEFAULT '',    -- auto-optimized prompt
            model TEXT DEFAULT 'happyhorse-1.1-t2v',  -- t2v / r2v / i2v
            ref_image TEXT DEFAULT '',           -- for R2V: OSS URL
            duration INTEGER DEFAULT 5,          -- seconds
            resolution TEXT DEFAULT '1920*1080',
            video_local TEXT DEFAULT '',         -- downloaded video path
            video_oss TEXT DEFAULT '',           -- OSS URL of result
            task_id TEXT DEFAULT '',             -- HappyHorse task ID
            status TEXT DEFAULT 'pending',       -- pending / generating / done / failed
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS video_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shot_id INTEGER,
            task_id TEXT NOT NULL UNIQUE,
            model TEXT DEFAULT '',
            status TEXT DEFAULT 'submitted',
            video_url TEXT DEFAULT '',
            video_local TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            submitted_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (shot_id) REFERENCES shots(id)
        );
    """)
    conn.commit()
    
    # Migration: add worldview/synopsis columns if not exist
    try:
        conn.execute("ALTER TABLE projects ADD COLUMN worldview TEXT DEFAULT ''")
    except:
        pass
    try:
        conn.execute("ALTER TABLE projects ADD COLUMN synopsis TEXT DEFAULT ''")
    except:
        pass
    conn.commit()
    
    conn.close()


# --- Project CRUD ---
def create_project(name, genre="", logline="", worldview="", synopsis=""):
    conn = get_db()
    conn.execute("INSERT INTO projects (name, genre, logline, worldview, synopsis) VALUES (?,?,?,?,?)",
                 (name, genre, logline, worldview, synopsis))
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return pid


def get_project(project_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_projects():
    conn = get_db()
    rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_project_script(project_id, script_raw):
    conn = get_db()
    conn.execute("UPDATE projects SET script_raw=?, updated_at=datetime('now') WHERE id=?",
                 (script_raw, project_id))
    conn.commit()
    conn.close()


# --- Character CRUD ---
def add_character(project_id, name, role_type="human", description="", traits=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO characters (project_id, name, role_type, description, traits) VALUES (?,?,?,?,?)",
        (project_id, name, role_type, description, json.dumps(traits or {}, ensure_ascii=False)))
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return cid


def update_character_view(char_id, view_type, local_path, oss_url=""):
    """view_type: portrait / fullbody_front / fullbody_side / fullbody_back"""
    col_map = {
        "portrait": ("portrait_local", "portrait_oss"),
        "fullbody_front": ("fullbody_front_local", "fullbody_front_oss"),
        "fullbody_side": ("fullbody_side_local", "fullbody_side_oss"),
        "fullbody_back": ("fullbody_back_local", "fullbody_back_oss"),
    }
    if view_type not in col_map:
        raise ValueError(f"Unknown view_type: {view_type}")
    local_col, oss_col = col_map[view_type]
    conn = get_db()
    conn.execute(f"UPDATE characters SET {local_col}=?, {oss_col}=?, status='done' WHERE id=?",
                 (local_path, oss_url, char_id))
    conn.commit()
    conn.close()


def update_character_seed(char_id, seed):
    """Save the last used seed for a character."""
    conn = get_db()
    conn.execute("UPDATE characters SET seed=? WHERE id=?", (int(seed), char_id))
    conn.commit()
    conn.close()


def update_character_prompt(char_id, prompt_raw):
    """Save prompt_raw to character (not a real column — just store in traits for now)."""
    conn = get_db()
    import json
    row = conn.execute("SELECT traits FROM characters WHERE id=?", (char_id,)).fetchone()
    if row:
        traits = json.loads(row[0]) if row[0] else {}
        traits["prompt_raw"] = prompt_raw
        conn.execute("UPDATE characters SET traits=? WHERE id=?", (json.dumps(traits, ensure_ascii=False), char_id))
    conn.commit()
    conn.close()


def get_characters(project_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM characters WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Shot CRUD ---
def add_shot(project_id, episode, shot_number, scene_desc="", prompt_raw="", model="happyhorse-1.1-t2v",
             duration=5, resolution="1920*1080", character_ids=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO shots (project_id, episode, shot_number, scene_desc, prompt_raw, model, duration, resolution, character_ids) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, episode, shot_number, scene_desc, prompt_raw, model, duration, resolution,
         json.dumps(character_ids or [], ensure_ascii=False)))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return sid


def update_shot_prompt(shot_id, prompt_optimized, model=None, duration=None, ref_image=None, scene_ref_image=None):
    conn = get_db()
    fields = ["prompt_optimized=?"]
    params = [prompt_optimized]
    if model:
        fields.append("model=?")
        params.append(model)
    if duration:
        fields.append("duration=?")
        params.append(duration)
    if ref_image is not None:
        fields.append("ref_image=?")
        params.append(ref_image)
    if scene_ref_image is not None:
        fields.append("scene_ref_image=?")
        params.append(scene_ref_image)
    params.append(shot_id)
    conn.execute(f"UPDATE shots SET {', '.join(fields)} WHERE id=?", params)
    conn.commit()
    conn.close()


def update_shot_video(shot_id, task_id, video_local="", status="generating"):
    conn = get_db()
    conn.execute("UPDATE shots SET task_id=?, video_local=?, status=? WHERE id=?",
                 (task_id, video_local, status, shot_id))
    conn.commit()
    conn.close()


def get_shots(project_id, episode=None):
    conn = get_db()
    if episode is not None:
        rows = conn.execute("SELECT * FROM shots WHERE project_id=? AND episode=? ORDER BY shot_number",
                            (project_id, episode)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM shots WHERE project_id=? ORDER BY episode, shot_number",
                            (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Video Task CRUD ---
def add_video_task(shot_id, task_id, model="", take_number=1, seed=None,
                   resolution="1920*1080", filename="", status="submitted", error_msg=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO video_tasks (shot_id, task_id, model, take_number, seed, resolution, filename, status, error_msg) VALUES (?,?,?,?,?,?,?,?,?)",
        (shot_id, task_id, model, take_number, seed, resolution, filename, status, error_msg))
    conn.commit()
    conn.close()


def update_video_task(task_id, status, video_url="", video_local="", error_msg=""):
    conn = get_db()
    conn.execute(
        "UPDATE video_tasks SET status=?, video_url=?, video_local=?, error_msg=?, completed_at=datetime('now') WHERE task_id=?",
        (status, video_url, video_local, error_msg, task_id))
    conn.commit()
    conn.close()


def get_video_task(task_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM video_tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Scene CRUD ---
def add_scene(project_id, name, description=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO scenes (project_id, name, description) VALUES (?,?,?)",
        (project_id, name, description))
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return sid


def update_scene_image(scene_id, local_path, oss_url="", expires_at=None):
    conn = get_db()
    params = [local_path, oss_url, scene_id]
    if expires_at:
        conn.execute("UPDATE scenes SET scene_image_local=?, scene_image_oss=?, oss_expires_at=?, status='done' WHERE id=?",
                     (local_path, oss_url, expires_at, scene_id))
    else:
        conn.execute("UPDATE scenes SET scene_image_local=?, scene_image_oss=?, status='done' WHERE id=?",
                     (local_path, oss_url, scene_id))
    conn.commit()
    conn.close()


def get_scenes(project_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_scene(scene_id):
    conn = get_db()
    conn.execute("DELETE FROM scenes WHERE id=?", (scene_id,))
    conn.commit()
    conn.close()


def update_shot_scene(shot_id, scene_id, scene_ref_image=""):
    conn = get_db()
    conn.execute("UPDATE shots SET scene_id=?, scene_ref_image=? WHERE id=?",
                 (scene_id, scene_ref_image, shot_id))
    conn.commit()
    conn.close()


# Auto-init on import
init_db()
