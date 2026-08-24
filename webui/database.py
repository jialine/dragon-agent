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

        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_number INTEGER NOT NULL,
            title TEXT DEFAULT '',
            content TEXT DEFAULT '',
            summary TEXT DEFAULT '',          -- 章节摘要，用于续写/连贯性
            status TEXT DEFAULT 'draft',       -- draft / done
            word_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS comic_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER,              -- 关联小说章节（可选）
            episode_number INTEGER DEFAULT 1, -- 漫画话数
            page_number INTEGER DEFAULT 1,    -- 页面编号
            panel_number INTEGER NOT NULL,    -- 面板编号
            scene_desc TEXT DEFAULT '',       -- 场景描述（中文）
            dialogue TEXT DEFAULT '',         -- 对白
            sfx TEXT DEFAULT '',              -- 音效文字
            camera TEXT DEFAULT '中景',        -- 镜头角度
            prompt_raw TEXT DEFAULT '',       -- 原始提示词（中文）
            prompt_optimized TEXT DEFAULT '', -- 优化后提示词（英文）
            model TEXT DEFAULT 'GuoFeng3.4',  -- 生图模型
            image_local TEXT DEFAULT '',      -- 本地图片路径
            image_oss TEXT DEFAULT '',        -- OSS 图片 URL
            seed INTEGER DEFAULT NULL,        -- 生图种子
            page_file TEXT DEFAULT '',        -- 合并后的页面文件路径
            status TEXT DEFAULT 'pending',    -- pending / generating / done / failed
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (chapter_id) REFERENCES chapters(id)
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


# --- Chapter CRUD (小说模式) ---
def add_chapter(project_id, chapter_number, title="", content="", summary="", status="draft"):
    conn = get_db()
    conn.execute(
        "INSERT INTO chapters (project_id, chapter_number, title, content, summary, status, word_count) "
        "VALUES (?,?,?,?,?,?,?)",
        (project_id, chapter_number, title, content, summary, status, len(content or "")))
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return cid


def update_chapter(chapter_id, title=None, content=None, summary=None, status=None):
    conn = get_db()
    fields = []
    params = []
    if title is not None:
        fields.append("title=?")
        params.append(title)
    if content is not None:
        fields.append("content=?")
        params.append(content)
        fields.append("word_count=?")
        params.append(len(content))
    if summary is not None:
        fields.append("summary=?")
        params.append(summary)
    if status is not None:
        fields.append("status=?")
        params.append(status)
    if not fields:
        conn.close()
        return
    fields.append("updated_at=datetime('now')")
    params.append(chapter_id)
    conn.execute(f"UPDATE chapters SET {', '.join(fields)} WHERE id=?", params)
    conn.commit()
    conn.close()


def get_chapters(project_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM chapters WHERE project_id=? ORDER BY chapter_number", (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_chapter(chapter_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_chapter(chapter_id):
    conn = get_db()
    conn.execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
    conn.commit()
    conn.close()


# --- Comic Panel CRUD ---
def add_comic_panel(project_id, panel_number, chapter_id=None, episode_number=1,
                    page_number=1, scene_desc="", dialogue="", sfx="",
                    camera="中景", prompt_raw="", model="GuoFeng3.4"):
    conn = get_db()
    conn.execute(
        """INSERT INTO comic_panels (project_id, chapter_id, episode_number, page_number,
        panel_number, scene_desc, dialogue, sfx, camera, prompt_raw, model)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (project_id, chapter_id, episode_number, page_number, panel_number,
         scene_desc, dialogue, sfx, camera, prompt_raw, model))
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return pid


def add_comic_panels_batch(panels):
    """批量插入漫画面板。每个 panel 是 dict，字段同 add_comic_panel 参数。"""
    conn = get_db()
    ids = []
    for p in panels:
        conn.execute(
            """INSERT INTO comic_panels (project_id, chapter_id, episode_number, page_number,
            panel_number, scene_desc, dialogue, sfx, camera, prompt_raw, model)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (p.get("project_id"), p.get("chapter_id"), p.get("episode_number", 1),
             p.get("page_number", 1), p.get("panel_number"), p.get("scene_desc", ""),
             p.get("dialogue", ""), p.get("sfx", ""), p.get("camera", "中景"),
             p.get("prompt_raw", ""), p.get("model", "GuoFeng3.4")))
        ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return ids


def update_comic_panel(panel_id, **kwargs):
    """更新漫画面板字段。"""
    conn = get_db()
    allowed = {"scene_desc", "dialogue", "sfx", "camera", "prompt_raw",
               "prompt_optimized", "model", "image_local", "image_oss",
               "seed", "page_file", "status", "page_number", "episode_number",
               "panel_number"}
    fields = []
    params = []
    for k, v in kwargs.items():
        if k in allowed:
            fields.append(f"{k}=?")
            params.append(v)
    if not fields:
        conn.close()
        return
    fields.append("updated_at=datetime('now')")
    params.append(panel_id)
    conn.execute(f"UPDATE comic_panels SET {', '.join(fields)} WHERE id=?", params)
    conn.commit()
    conn.close()


def update_comic_panel_image(panel_id, image_local, image_oss="", seed=None, status="done"):
    """更新漫画面板生成的图片。"""
    conn = get_db()
    conn.execute(
        """UPDATE comic_panels SET image_local=?, image_oss=?, seed=?,
        status=?, updated_at=datetime('now') WHERE id=?""",
        (image_local, image_oss, seed, status, panel_id))
    conn.commit()
    conn.close()


def get_comic_panels(project_id, episode_number=None):
    """获取项目的漫画面板列表。"""
    conn = get_db()
    if episode_number is not None:
        rows = conn.execute(
            """SELECT * FROM comic_panels WHERE project_id=? AND episode_number=?
            ORDER BY page_number, panel_number""",
            (project_id, episode_number)).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM comic_panels WHERE project_id=?
            ORDER BY episode_number, page_number, panel_number""",
            (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_comic_panel(panel_id):
    """获取单个漫画面板。"""
    conn = get_db()
    row = conn.execute("SELECT * FROM comic_panels WHERE id=?", (panel_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_comic_panels(project_id, episode_number=None):
    """删除项目的漫画面板（可选指定话数）。"""
    conn = get_db()
    if episode_number is not None:
        conn.execute("DELETE FROM comic_panels WHERE project_id=? AND episode_number=?",
                     (project_id, episode_number))
    else:
        conn.execute("DELETE FROM comic_panels WHERE project_id=?", (project_id,))
    conn.commit()
    conn.close()


def get_comic_episodes(project_id):
    """获取项目的话数列表（去重）。"""
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT episode_number, COUNT(*) as panel_count, 
        SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done_count,
        MIN(page_number) as min_page, MAX(page_number) as max_page
        FROM comic_panels WHERE project_id=?
        GROUP BY episode_number ORDER BY episode_number""",
        (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Auto-init on import
init_db()
