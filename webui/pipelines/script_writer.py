"""Low-hallucination script generation via andlapi.cn LLM."""
import json
import subprocess
import os
import re

# Read config from Dragon's config.yaml + .env
import yaml
CONFIG_PATH = os.path.expanduser("~/.dragon/config.yaml")
ENV_PATH = os.path.expanduser("~/.dragon/.env")

API_KEY = ""
try:
    with open(CONFIG_PATH) as f:
        _config = yaml.safe_load(f)
    API_KEY = _config.get("global_api", {}).get("api_key", "")
except Exception:
    pass

if not API_KEY:
    try:
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("DRAGON_API_KEY="):
                    API_KEY = line.strip().split("=", 1)[1].strip('"').strip("'")
                    break
    except Exception:
        pass
# Domain from _domains.pyc — hardcode known working endpoint
API_URL = "https://api.andlapi.cn/v1/chat/completions"
LLM_MODEL = "deepseek-v3.2"  # 0 reasoning overhead, best for structured JSON


def _curl_post(payload, timeout=120):
    """Dragon machine MUST use subprocess+curl (httpx/requests hang)."""
    cmd = ["curl", "-s", "-k", "--max-time", str(timeout), API_URL,
           "-H", f"Authorization: Bearer {API_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr}")
    return json.loads(r.stdout)


def generate_script(topic, genre="科幻", episode_count=1, duration_per_ep=120, worldbuilding="", synopsis=""):
    """
    Generate a low-hallucination script with structured output.
    Uses 'no think' directive to maximize output tokens for JSON.
    """
    system_prompt = """你是一个专业短剧编剧。你必须严格输出JSON格式，不要任何解释。
    
剧本规则：
1. 每集{duration}秒，约{shots}个镜头
2. 每个镜头包含：场景描述、角色动作、台词、镜头方向
3. 角色必须具体：姓名、年龄、外貌特征、服装
4. 场景必须具体：地点、光线、氛围
5. 禁止模糊描述：不要"一个男人"、"某个房间"

输出JSON格式：
{{
  "title": "剧名",
  "genre": "类型",
  "logline": "一句话梗概",
  "worldbuilding": "世界观描述",
  "synopsis": "剧情简介",
  "total_episodes": {eps},
  "characters": [
    {{"name": "姓名", "role_type": "human/animal/other", "description": "年龄+外貌+服装+特征"}}
  ],
  "episodes": [
    {{
      "episode": 1,
      "title": "本集标题",
      "shots": [
        {{
          "shot_number": 1,
          "scene_desc": "中文场景描述（含动作、情绪、氛围）",
          "characters": ["角色名"],
          "camera": "特写/中景/远景/主观/跟拍",
          "dialogue": "台词（无台词则为空字符串）",
          "duration_sec": 8,
          "transition": "cut/fade/dissolve"
        }}
      ]
    }}
  ]
}}

重要：直接输出JSON，不要包裹在```json```中，不要任何前置或后置文字。"""

    user_prompt = f"创作一部{genre}短剧：{topic}。{episode_count}集，每集{duration_per_ep}秒。"
    if worldbuilding:
        user_prompt += f"\n\n世界观设定：{worldbuilding}"
    if synopsis:
        user_prompt += f"\n\n剧情简介：{synopsis}"

    shots_per_ep = max(5, duration_per_ep // 15)
    system = system_prompt.format(duration=duration_per_ep, shots=shots_per_ep, eps=episode_count)

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"}
    }

    resp = _curl_post(payload)
    content = resp["choices"][0]["message"]["content"]

    # Clean and parse
    content = re.sub(r"```json\s*|```", "", content).strip()
    idx = content.find("{")
    if idx > 0:
        content = content[idx:]
    idx = content.rfind("}")
    if idx > 0:
        content = content[:idx + 1]

    return json.loads(content)


def optimize_prompt(shot_desc, model="happyhorse-1.1-t2v", characters_context=""):
    """
    Optimize a shot description into a HappyHorse-compatible prompt.
    Enforces: Chinese, ≤2000字, no glasses for scientists.
    """
    system = """你是视频生成提示词优化器。输出一段紧凑的中文画面描述，不超过500字。

严格规则：
1. 只描述镜头里看得到的东西：场景环境、光线色调、角色外貌与动作、镜头角度与运动
2. 紧凑流畅的散文段落——用句号连接，禁止分点、禁止标题、禁止Markdown、禁止列表、禁止分段
3. 将角色信息中的外貌特征融入画面描述，忠实于角色描述（戴眼镜就写戴眼镜，不戴就不写）
4. 直接输出提示词正文，禁止任何前缀后缀

错误示例（绝对禁止）：
"**角色与外貌：**\n- 墨点：2岁白色短毛猫..."
"### 场景\n实验室..."

正确示例：
"深夜实验室，冷蓝光线洒在金属操作台上。一只体型魁梧的银背大猩猩身穿黑色战术背心，站在控制台前急促敲击键盘，眼神锐利。身后束缚舱内一只年轻大猩猩缓缓睁开眼睛，脑机接口的蓝色指示灯由暗转亮。镜头从远景缓慢推近到面部特写。" """

    user = f"镜头描述：{shot_desc}\n角色信息（仅提取外貌特征）：{characters_context}\n模型：{model}\n\n直接输出优化后的画面描述（纯文本段落，禁止Markdown）："

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": 0.3,
        "max_tokens": 800
    }

    resp = _curl_post(payload)
    prompt = resp["choices"][0]["message"]["content"].strip()
    
    # Post-process: strip markdown artifacts
    import re
    prompt = re.sub(r'\*\*[^*]+\*\*[：:]?\s*', '', prompt)  # Remove **headers**
    prompt = re.sub(r'^[#]+\s+.*$', '', prompt, flags=re.MULTILINE)  # Remove ### lines
    prompt = re.sub(r'^[-*]\s+', '', prompt, flags=re.MULTILINE)  # Remove list markers
    prompt = re.sub(r'\n+', '，', prompt)  # Collapse newlines
    prompt = prompt.strip('"\'""''， ')
    if len(prompt) > 500:
        prompt = prompt[:500]
    return prompt
