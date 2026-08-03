#!/usr/bin/env python3
"""Direct tool-calling agent loop — v5 with pre-created dir and better error handling."""
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

TOOLS = [
    {"type":"function","function":{"name":"execute","description":"Execute shell command. Returns stdout/stderr.","parameters":{"type":"object","properties":{"command":{"type":"string","description":"Shell command"}},"required":["command"]}}},
    {"type":"function","function":{"name":"file_write","description":"Write content to file. Creates parent dirs.","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}},
    {"type":"function","function":{"name":"wan_video","description":"Generate AI video via Wan2.7 T2V. Auto-submit, poll, download. Returns video_path on success.","parameters":{"type":"object","properties":{"prompt":{"type":"string"},"model":{"type":"string"},"size":{"type":"string"},"duration":{"type":"integer"},"output_path":{"type":"string"}},"required":["prompt"]}}},
    {"type":"function","function":{"name":"web_download","description":"Download file from URL.","parameters":{"type":"object","properties":{"url":{"type":"string"},"path":{"type":"string"}},"required":["url","path"]}}},
]

SYSTEM = """你是全自主执行引擎。通过JSON工具调用完成任务，不要输出解释。

JSON格式: {"tool":"NAME","params":{...}}
可用: execute, file_write, wan_video, web_download
完成后: {"status":"DONE"}

规则：每轮只输出一个JSON，等工具结果再继续。"""

TASK = """真人修仙竖屏短剧《凡尘仙途》第1集。720x1280竖屏。目录/tmp/dragon_drama已创建。

1. file_write: /tmp/dragon_drama/outline_50ep.txt — 《凡尘仙途》50集大纲。主角林玄外门弟子得古玉简，炼气→化神。每集一行核心冲突。中国修仙。

2. file_write: /tmp/dragon_drama/ep01_storyboard.txt — 第1集《坠崖得古卷》5镜分镜：
镜01: 清晨悬崖边，林玄独立，金光照云海 — A young Chinese man Lin Xuan in worn gray robes stands on misty cliff at sunrise, golden light piercing clouds, ancient sect pavilions behind, despair mixed with determination, cinematic live-action, vertical 9:16
镜02: 三内门弟子推搡挑衅 — Three arrogant inner sect disciples in blue robes sneer and push Lin Xuan, dramatic low angle, wind whipping robes, ancient pines, live-action
镜03: 林玄坠落深渊 — Lin Xuan falling through swirling mist, robes billowing, jagged cliffs rushing past, shock turning to fierce will, sun rays through fog, photorealistic
镜04: 坠入洞底潭边 — Lin Xuan crashes through branches, lands on mossy rocks by glowing turquoise pool, ancient jade tablet emitting golden light, crystal formations, bioluminescent flora
镜05: 触碰玉简，金光爆发 — Close-up of bloodied hand touching jade slip, golden light erupting, ancient runes floating, bruised face illuminated with awe, spiritual energy flowing, cinematic

3. wan_video 生成5镜。参数: model="wan2.7-t2v", size="720x1280", duration=5, output_path="/tmp/dragon_drama/scene_01.mp4" 到 scene_05.mp4。prompt用上面分镜的英文描述。每镜完成后再下一镜。

4. execute: cd /tmp/dragon_drama && rm -f files.txt && for f in scene_0*.mp4; do echo "file '$f'" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y

5. execute: ls -la /tmp/dragon_drama/ep01_final.mp4

现在立即输出第1步的JSON。"""

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
            break
        
        print(f"LLM: {response[:500]}", flush=True)
        
        if not response:
            print("Empty response, retrying...")
            history.append({"role": "user", "content": "输出下一个工具调用JSON。只输出JSON，不要其他内容。"})
            continue
        
        # Parse JSON — try multiple strategies
        tc = None
        # Strategy 1: direct JSON parse
        try:
            tc = json.loads(response)
        except:
            pass
        
        # Strategy 2: regex extract
        if tc is None:
            m = re.search(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', response)
            if m:
                try:
                    tc = json.loads(m.group())
                except:
                    # Try to fix truncated JSON
                    s = m.group().rstrip(',')
                    if not s.endswith('}'):
                        s += '}'
                    try:
                        tc = json.loads(s)
                    except:
                        pass
        
        if tc is None:
            # Check DONE
            if 'DONE' in response.upper():
                print("DONE signal received")
                break
            print("Could not parse tool call, retrying...")
            history.append({"role": "assistant", "content": response})
            history.append({"role": "user", "content": "格式错误。请严格输出: {\"tool\":\"TOOL_NAME\",\"params\":{...}}"})
            continue
        
        tool_name = tc.get("tool", "")
        if not tool_name:
            if tc.get("status") == "DONE":
                print("DONE!")
                break
            print(f"No tool name in: {tc}")
            history.append({"role": "assistant", "content": response})
            history.append({"role": "user", "content": "需要包含tool字段。格式: {\"tool\":\"execute\",\"params\":{\"command\":\"...\"}}"})
            continue
        
        params = tc.get("params", {})
        print(f"  -> {tool_name}: {str(params)[:200]}", flush=True)
        
        history.append({"role": "assistant", "content": response})
        
        # Execute tool
        try:
            tr = await tool_registry.call(tool_name, params)
            if tr.success:
                out = str(tr.output)
                print(f"  <- OK: {out[:300]}", flush=True)
            else:
                out = f"ERROR: {tr.error}"
                print(f"  <- FAIL: {out[:300]}", flush=True)
        except Exception as e:
            out = f"Tool Exception: {type(e).__name__}: {e}"
            print(f"  <- EXCEPTION: {out[:300]}", flush=True)
        
        history.append({"role": "tool", "content": out[:2000], "name": tool_name})
        
        # Check for final video
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
