#!/usr/bin/env python3
"""Direct tool-calling agent loop — v6 with correct parameter names."""
import asyncio, json, os, re, yaml, time, subprocess
from pathlib import Path

# Pre-create output directory
os.makedirs("/tmp/dragon_drama", exist_ok=True)
print("Pre-created: /tmp/dragon_drama")

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
print(f"Model: {dc['m']}, Base: {dc['b']}")

from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

registry = ProviderRegistry()
registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai', api_key=dc['k'], base_url=dc['b'],
    default_model=dc['m'], timeout_secs=dc['t'])))

tool_registry = ToolRegistry()
register_builtins(tool_registry)

# CORRECT parameter names matching Dragon's actual tool signatures
TOOLS = [
    {"type":"function","function":{
        "name":"execute",
        "description":"Execute a shell command. Returns stdout/stderr/exit_code.",
        "parameters":{"type":"object","properties":{
            "command":{"type":"string","description":"Shell command to execute"},
            "workdir":{"type":"string","description":"Working directory (default: current)"},
            "timeout_secs":{"type":"integer","description":"Timeout in seconds (default: 60)"}
        },"required":["command"]}
    }},
    {"type":"function","function":{
        "name":"file_write",
        "description":"Write content to a file, creating parent directories as needed.",
        "parameters":{"type":"object","properties":{
            "filepath":{"type":"string","description":"Path to write to"},
            "content":{"type":"string","description":"Content to write"},
            "append":{"type":"boolean","description":"Append instead of overwrite"}
        },"required":["filepath","content"]}
    }},
    {"type":"function","function":{
        "name":"wan_video",
        "description":"Generate AI video using Wan2.7 T2V. Auto-submit, poll until done, download. Returns video_path.",
        "parameters":{"type":"object","properties":{
            "prompt":{"type":"string","description":"English video description"},
            "model":{"type":"string","description":"wan2.7-t2v"},
            "size":{"type":"string","description":"720x1280 for vertical"},
            "duration":{"type":"integer","description":"Seconds (min 5)"},
            "output_path":{"type":"string","description":"Save path e.g. /tmp/dragon_drama/scene_01.mp4"}
        },"required":["prompt"]}
    }},
    {"type":"function","function":{
        "name":"web_download",
        "description":"Download a file from URL to a local path.",
        "parameters":{"type":"object","properties":{
            "url":{"type":"string","description":"URL to download"},
            "save_path":{"type":"string","description":"Local file path to save"}
        },"required":["url","save_path"]}
    }},
]

SYSTEM = """你是全自主执行引擎。通过JSON工具调用完成任务。不要输出解释——只输出JSON。

工具: execute(command, workdir?, timeout_secs?), file_write(filepath, content), wan_video(prompt, model?, size?, duration?, output_path?), web_download(url, save_path)

JSON格式（严格）: {"tool":"TOOL_NAME","params":{...}}
完成后: {"status":"DONE"}

每轮只输出一个JSON。等结果再继续。"""

TASK = """真人修仙竖屏短剧《凡尘仙途》第1集。720x1280竖屏。目录/tmp/dragon_drama已创建。

步骤（严格按顺序执行）:

1. file_write: /tmp/dragon_drama/outline_50ep.txt，内容为《凡尘仙途》50集大纲。主角林玄，外门弟子得古玉简，炼气→化神。每集一行核心冲突，中国修仙风格。

2. file_write: /tmp/dragon_drama/ep01_storyboard.txt，内容为第1集《坠崖得古卷》5镜分镜脚本，每镜含英文画面描述：

镜01: A young Chinese man Lin Xuan in worn gray robes stands alone on misty cliff edge at sunrise, golden light piercing clouds, ancient sect pavilions on distant peaks, expression of despair and determination, cinematic live-action movie photography, vertical 9:16

镜02: Three arrogant inner disciples in luxurious blue robes sneer and shove Lin Xuan backward toward cliff edge, dramatic low angle shot, wind whipping their robes, towering ancient pine trees, live-action cinematography, 9:16 vertical

镜03: Lin Xuan falling through swirling mist and clouds, arms outstretched, gray robes billowing violently, jagged cliff walls rushing past, loose stones falling alongside him, shock turning to fierce will to survive, dramatic sun rays through fog, photorealistic live-action, 9:16 vertical

镜04: Lin Xuan crashes through ancient tree branches at bottom of abyss, landing hard on moss-covered rocks next to glowing turquoise underground pool, ancient jade tablet half-buried in crystal formations emitting faint golden light, bioluminescent flora glowing, mystical atmosphere, live-action cinematic, 9:16 vertical

镜05: Extreme close-up of Lin Xuan's bloodied hand reaching toward ancient jade slip as brilliant golden light erupts, illuminating his bruised face, ancient Chinese runes floating in air around him, eyes wide with wonder, spiritual energy visibly flowing into body, cinematic lighting with golden glow, photorealistic, 9:16 vertical

3. wan_video x5（每镜一次，等完成再下一镜）:
   - prompt: 上面分镜的完整英文描述
   - model: "wan2.7-t2v"
   - size: "720x1280"  
   - duration: 5
   - output_path: "/tmp/dragon_drama/scene_01.mp4" 到 scene_05.mp4

4. execute: cd /tmp/dragon_drama && rm -f files.txt && for f in scene_0*.mp4; do echo "file '$f'" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y

5. execute: ls -la /tmp/dragon_drama/ep01_final.mp4

开始！立即输出第1步JSON。"""

async def agent_loop(max_iter=30):
    history = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK},
    ]
    
    for i in range(max_iter):
        print(f"\n--- Step {i+1} ---", flush=True)
        
        try:
            result = await registry.call(
                'openai', dc['m'],
                messages=history,
                max_tokens=2048,
                tools=TOOLS,
            )
            response = (result.content or "").strip()
        except Exception as e:
            print(f"LLM call failed: {e}")
            # Try to recover by trimming history
            if len(history) > 8:
                history = history[:2] + history[-6:]
                print("Trimmed history, retrying...")
                continue
            break
        
        print(f"LLM: {response[:400]}", flush=True)
        
        if not response:
            history.append({"role": "user", "content": "请输出下一步工具调用JSON。"})
            continue
        
        # Parse JSON
        tc = None
        try:
            tc = json.loads(response)
        except:
            m = re.search(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', response)
            if m:
                s = m.group().rstrip(',')
                if not s.endswith('}'): s += '}'
                try: tc = json.loads(s)
                except: pass
        
        if tc is None:
            if 'DONE' in response.upper():
                print("DONE")
                break
            history.append({"role": "user", "content": "格式错误。严格输出JSON: {\"tool\":\"name\",\"params\":{...}}"})
            continue
        
        tool_name = tc.get("tool","")
        if not tool_name:
            if tc.get("status") == "DONE":
                print("DONE!")
                break
            history.append({"role": "user", "content": "缺少tool字段。"})
            continue
        
        params = tc.get("params",{})
        print(f"  -> {tool_name}: {str(params)[:150]}", flush=True)
        
        # Execute and get result
        try:
            tr = await tool_registry.call(tool_name, params)
            if tr.success:
                out = str(tr.output)[:500]
                print(f"  <- OK: {out[:200]}", flush=True)
            else:
                out = f"ERROR: {tr.error}"
                print(f"  <- FAIL: {out[:200]}", flush=True)
        except Exception as e:
            out = f"Exception: {type(e).__name__}: {e}"
            print(f"  <- EXC: {out[:200]}", flush=True)
        
        # Only add successful tool interactions to history to avoid context pollution
        history.append({"role": "assistant", "content": response})
        history.append({"role": "tool", "content": out[:500], "name": tool_name})
        
        # Check for completion
        if os.path.exists("/tmp/dragon_drama/ep01_final.mp4"):
            sz = os.path.getsize("/tmp/dragon_drama/ep01_final.mp4")
            if sz > 1000:
                print(f"\n*** SUCCESS! Final video: {sz} bytes ***")
                break
    
    import glob
    files = sorted(glob.glob("/tmp/dragon_drama/*"))
    print(f"\n=== OUTPUT ({len(files)} files) ===")
    for f in files:
        sz = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({sz} bytes)")

asyncio.run(agent_loop())
