"""小说写作 pipeline：章节生成、续写、连贯性检查、改编短剧。

复用 script_writer.py 的 API 调用模式（subprocess+curl，Dragon 机器上 httpx 会 hang），
但小说正文创作用 deepseek-v4-pro（质量优先），结构化改编用 deepseek-v3.2（JSON 稳定）。
"""
import json
import os
import re
import subprocess

import yaml

CONFIG_PATH = os.path.expanduser("~/.dragon/config.yaml")
ENV_PATH = os.path.expanduser("~/.dragon/.env")

API_URL = "https://api.andlapi.cn/v1/chat/completions"
NOVEL_MODEL = "deepseek-v4-pro"   # 小说正文创作：质量优先
ADAPT_MODEL = "deepseek-v3.2"     # 结构化 JSON 改编：与 script_writer 一致


def _load_api_key():
    """读 API key：config.yaml global_api.api_key → api_key_env 指向的 env → .env 回退。"""
    key = ""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        global_api = cfg.get("global_api", {})
        key = global_api.get("api_key", "")
        if not key:
            env_name = global_api.get("api_key_env", "")
            if env_name:
                key = os.environ.get(env_name, "")
    except Exception:
        pass
    if not key:
        try:
            with open(ENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("DRAGON_API_KEY="):
                        key = line.split("=", 1)[1].strip('"').strip("'")
                        if key:
                            break
        except Exception:
            pass
    return key


API_KEY = _load_api_key()


def _curl_post(payload, timeout=180):
    """Dragon 机器必须用 subprocess+curl（httpx/requests 会 hang）。"""
    cmd = ["curl", "-s", "-k", "--max-time", str(timeout), API_URL,
           "-H", f"Authorization: Bearer {API_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr}")
    resp = json.loads(r.stdout)
    if isinstance(resp, dict) and "error" in resp:
        raise RuntimeError(f"API error: {resp['error']}")
    return resp


def _build_context(project, characters, prev_summaries):
    """构建生成上下文：类型 + 梗概 + 世界观 + 大纲 + 角色 + 前文摘要。"""
    parts = []
    if project.get("genre"):
        parts.append(f"【类型】{project['genre']}")
    if project.get("logline"):
        parts.append(f"【一句话梗概】{project['logline']}")
    if project.get("worldview"):
        parts.append(f"【世界观】{project['worldview']}")
    if project.get("synopsis"):
        parts.append(f"【剧情大纲】{project['synopsis']}")
    if characters:
        char_lines = [f"- {c['name']}：{c.get('description', '')}" for c in characters]
        parts.append("【角色设定】\n" + "\n".join(char_lines))
    if prev_summaries:
        parts.append("【前文章节摘要】\n" + "\n".join(
            f"第{i + 1}章：{s}" for i, s in enumerate(prev_summaries)))
    return "\n\n".join(parts)


def generate_chapter(project, characters, prev_summaries, chapter_number, chapter_title="", instruction=""):
    """生成一章小说正文，并附带摘要（供后续续写/连贯性）。"""
    context = _build_context(project, characters, prev_summaries)
    system = """你是一位专业网文小说作家，文笔流畅，擅长制造悬念和爽点，情节紧凑，人物鲜活。

写作要求：
1. 正文使用中文，篇幅 1500-2500 字
2. 章节结尾留下悬念或钩子，吸引读者追更
3. 人物言行符合其设定，不偏离世界观
4. 承接前文剧情，不重复、不矛盾
5. 直接输出正文，不要输出标题、不要"第X章"前缀、不要任何解释"""
    user = f"{context}\n\n请写第 {chapter_number} 章。"
    if chapter_title:
        user += f"\n本章标题建议：{chapter_title}"
    if instruction:
        user += f"\n\n本章要求：{instruction}"
    user += "\n\n直接输出正文："

    payload = {
        "model": NOVEL_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.85,
        "max_tokens": 4096,
    }
    resp = _curl_post(payload)
    content = resp["choices"][0]["message"]["content"].strip()
    summary = summarize_chapter(content)
    return {"title": chapter_title, "content": content, "summary": summary}


def summarize_chapter(content):
    """生成章节摘要（2-3 句，用于续写连贯性）。"""
    system = "你是小说编辑。用 2-3 句话概括以下章节的关键情节、人物变化、伏笔，输出纯文本摘要。"
    payload = {
        "model": NOVEL_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content[:3000]},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }
    resp = _curl_post(payload)
    return resp["choices"][0]["message"]["content"].strip()


def continue_chapter(project, characters, chapter, instruction=""):
    """续写当前章节（在现有内容末尾继续写，保持文风连贯）。"""
    system = """你是网文小说作家。基于本章已有内容继续往下写，保持文风、人物、情节连贯。

要求：
1. 续写 800-1500 字，直接承接原文末尾
2. 不要重复已写的内容
3. 直接输出续写正文，不要任何解释"""
    user = f"【已有正文】\n{chapter['content'][-2000:]}\n\n"
    if instruction:
        user += f"续写要求：{instruction}\n\n"
    user += "直接输出续写正文："
    payload = {
        "model": NOVEL_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.85,
        "max_tokens": 2048,
    }
    resp = _curl_post(payload)
    return resp["choices"][0]["message"]["content"].strip()


def check_continuity(project, characters, chapters):
    """检查多章节连贯性：角色一致性、时间线、情节矛盾、伏笔。"""
    chapter_text = "\n\n".join(
        f"【第{c['chapter_number']}章 {c.get('title', '')}】\n{c.get('summary', c.get('content', '')[:500])}"
        for c in chapters
    )
    char_json = json.dumps(
        [{"name": c["name"], "description": c.get("description", "")} for c in characters],
        ensure_ascii=False,
    )
    system = """你是资深小说编辑，负责检查连载小说的连贯性。找出以下问题：
1. 角色设定矛盾（外貌、性格、能力前后不一致）
2. 时间线错误（事件顺序、时间跨度不合理）
3. 情节矛盾（前文已发生/已解决的事后文又重复或冲突）
4. 伏笔未回收或遗漏

输出 JSON 对象 {"issues": [{"chapter": 章节号, "type": "角色/时间线/情节/伏笔", "issue": "问题描述", "suggestion": "修改建议"}]}。没有问题则 {"issues": []}。"""
    user = f"【世界观】{project.get('worldview', '')}\n\n【角色】{char_json}\n\n【章节摘要】\n{chapter_text}\n\n检查并输出问题列表："
    payload = {
        "model": NOVEL_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    resp = _curl_post(payload)
    content = resp["choices"][0]["message"]["content"]
    content = re.sub(r"```json\s*|```", "", content).strip()
    idx = content.find("{")
    if idx > 0:
        content = content[idx:]
    idx = content.rfind("}")
    if idx > 0:
        content = content[:idx + 1]
    try:
        return json.loads(content)
    except Exception:
        return {"issues": []}


def adapt_novel_to_script(project, chapters, episode_count=8):
    """小说 → 短剧剧本（输出格式与 script_writer.generate_script 一致，可直接导入分镜）。"""
    novel_text = "\n\n".join(
        f"第{c['chapter_number']}章 {c.get('title', '')}\n{c.get('summary', c.get('content', '')[:800])}"
        for c in chapters
    )
    system = """你是短剧编剧。把小说改编成短剧剧本，严格输出 JSON 格式，不要任何解释。

输出 JSON 格式：
{
  "title": "短剧名",
  "genre": "类型",
  "logline": "一句话梗概",
  "worldbuilding": "世界观",
  "synopsis": "剧情简介",
  "total_episodes": 8,
  "characters": [{"name":"姓名","role_type":"human","description":"年龄+外貌+服装+特征"}],
  "episodes": [
    {"episode":1,"title":"本集标题","shots":[
      {"shot_number":1,"scene_desc":"中文场景描述（含动作、情绪、氛围）","characters":["角色名"],"camera":"特写/中景/远景","dialogue":"台词","duration_sec":8,"transition":"cut"}
    ]}
  ]
}

规则：
1. 忠实于小说剧情，提取高潮和爽点改编，每集 8-12 个镜头
2. 角色外貌、性格必须与小说一致
3. 直接输出 JSON，不要包裹在 ```json``` 中"""
    user = f"【小说内容】\n{novel_text}\n\n改编成 {episode_count} 集短剧剧本，直接输出 JSON："
    payload = {
        "model": ADAPT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    resp = _curl_post(payload)
    content = resp["choices"][0]["message"]["content"]
    content = re.sub(r"```json\s*|```", "", content).strip()
    idx = content.find("{")
    if idx > 0:
        content = content[idx:]
    idx = content.rfind("}")
    if idx > 0:
        content = content[:idx + 1]
    return json.loads(content)
