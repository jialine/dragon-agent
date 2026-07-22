#!/usr/bin/env python3
"""Direct tool-calling agent loop — minimal tools, bypass orchestrator."""
import asyncio, json, os, re, yaml, time
from pathlib import Path

# Load .env
from dotenv import load_dotenv
env_path = Path.home() / ".dragon" / ".env"
if env_path.exists():
    load_dotenv(env_path)

def load_dc():
    for p in ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]:
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f) or {}
            api = cfg.get('dispatch', {}).get('global_api', {})
            if api:
                return {"k": os.getenv(api.get('api_key_env',''),''),
                        "b": api.get('base_url'),
                        "m": api.get('model','gpt-4o'),
                        "t": api.get('timeout_secs',300)}
    return {'k':'','b':None,'m':'gpt-4o','t':60}

dc = load_dc()

from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

registry = ProviderRegistry()
registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai', api_key=dc['k'], base_url=dc['b'],
    default_model=dc['m'], timeout_secs=dc['t'])))

# Minimal tool registry with only what we need
tool_registry = ToolRegistry()
register_builtins(tool_registry)

# Only keep these 4 tools
NEEDED = {'execute', 'file_write', 'wan_video', 'web_download'}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute",
            "description": "Execute a shell command. Returns stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file, creating parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wan_video",
            "description": "Generate AI video using Wan2.7 T2V. Async — waits for completion, downloads result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "English video description"},
                    "model": {"type": "string", "description": "wan2.7-t2v"},
                    "size": {"type": "string", "description": "720x1280 for vertical"},
                    "duration": {"type": "integer", "description": "Seconds (min 5)"},
                    "output_path": {"type": "string", "description": "Save path e.g. /tmp/dragon_drama/scene_01.mp4"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_download",
            "description": "Download a file from URL to local path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to download"},
                    "path": {"type": "string", "description": "Local save path"}
                },
                "required": ["url", "path"]
            }
        }
    },
]

SYSTEM = """你是 Dragon Agent，一个全自主执行引擎。你通过工具调用来实际完成任务。

可用工具：
- execute(command): 运行 shell 命令
- file_write(path, content): 写入文件
- wan_video(prompt, model, size, duration, output_path): 生成AI视频
- web_download(url, path): 下载文件

规则：
1. 每次回复只输出一个工具调用，格式为JSON:
   {"tool": "TOOL_NAME", "params": {"key": "value", ...}}
2. 不要输出解释或描述——只输出JSON
3. 收到工具结果后继续下一个工具调用
4. 所有步骤完成后输出: {"status": "DONE"}

现在开始执行任务。"""

TASK = """真人修仙竖屏短剧《凡尘仙途》第1集。竖屏720x1280，真人写实。

步骤：
1. execute: mkdir -p /tmp/dragon_drama
2. file_write: /tmp/dragon_drama/outline_50ep.txt — 《凡尘仙途》50集大纲，主\角色林玄外门弟子得古玉简传承，炼气到化神，每集一行核心冲突
3. file_write: /tmp/dragon_drama/ep01_storyboard.txt — 第1集《坠崖得古卷》5镜分镜，每镜含英文画面描述(cinematic realistic Chinese xianxia, live-action movie style, vertical 9:16)
4. wan_video x5: model="wan2.7-t2v", size="720x1280", duration=5, output_path="/tmp/dragon_drama/scene_01.mp4" ~ scene_05.mp4, prompt=对应分镜英文描述
5. execute: cd /tmp/dragon_drama && rm -f files.txt && for f in scene_0*.mp4; do echo \"file '\$f'\" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y
6. execute: ls -la /tmp/dragon_drama/ep01_final.mp4

开始！现在立即输出第1步的工具调用JSON。"""

async def agent_loop(max_iter=30):
    history = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK},
    ]
    
    for i in range(max_iter):
        print(f"\n--- Step {i+1} ---")
        
        result = await registry.call(
            'openai', dc['m'],
            messages=history,
            max_tokens=2048,
            tools=TOOLS,
            tool_choice="auto",
        )
        
        response = result.content or ""
        print(f"LLM: {response[:400]}")
        
        # Extract JSON tool call
        json_match = re.search(r'\{[^{}]*"tool"[^{}]*\}', response)
        if not json_match:
            # Check for DONE
            if '"DONE"' in response or '"status": "DONE"' in response or response.strip().upper() == "DONE":
                print("Task complete!")
                break
            history.append({"role": "assistant", "content": response})
            history.append({"role": "user", "content": "继续。输出下一个工具调用JSON。不要输出任何其他内容。"})
            continue
        
        tc = json.loads(json_match.group())
        tool_name = tc.get("tool", "")
        params = tc.get("params", {})
        
        print(f"  -> {tool_name}: {str(params)[:200]}")
        
        history.append({"role": "assistant", "content": response})
        
        try:
            tr = await tool_registry.call(tool_name, params)
            out = str(tr.output) if tr.success else f"ERROR: {tr.error}"
        except Exception as e:
            out = f"Tool Exception: {e}"
        
        print(f"  <- result: {out[:300]}")
        history.append({"role": "tool", "content": out[:2000], "name": tool_name})
        
        # Check for final video
        if os.path.exists("/tmp/dragon_drama/ep01_final.mp4"):
            sz = os.path.getsize("/tmp/dragon_drama/ep01_final.mp4")
            if sz > 1000:
                print(f"\nSUCCESS! Final video: {sz} bytes")
                break
    
    import glob
    files = sorted(glob.glob("/tmp/dragon_drama/*"))
    print(f"\n=== OUTPUT ({len(files)} files) ===")
    for f in files:
        sz = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({sz} bytes)")

asyncio.run(agent_loop())
