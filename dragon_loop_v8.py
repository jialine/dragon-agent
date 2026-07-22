#!/usr/bin/env python3
"""Direct tool-calling agent loop — v8, simplified message format."""
import asyncio, json, os, re, yaml, time
from pathlib import Path

os.makedirs("/tmp/dragon_drama", exist_ok=True)
print("Pre-created: /tmp/dragon_drama")

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
print(f"Model: {dc['m']}")

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
    {"type":"function","function":{"name":"execute","description":"Execute shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
    {"type":"function","function":{"name":"file_write","description":"Write file","parameters":{"type":"object","properties":{"filepath":{"type":"string"},"content":{"type":"string"},"append":{"type":"boolean"}},"required":["filepath","content"]}}},
    {"type":"function","function":{"name":"wan_video","description":"Generate AI video via Wan2.7 T2V","parameters":{"type":"object","properties":{"prompt":{"type":"string"},"model":{"type":"string"},"size":{"type":"string"},"duration":{"type":"integer"},"output_path":{"type":"string"}},"required":["prompt"]}}},
]

SYSTEM = "全自主执行引擎。用函数调用完成任务。可用: execute, file_write, wan_video。完成后回复DONE。"

TASK = "目录/tmp/dragon_drama已建好。严格按顺序执行:\n1. file_write filepath=/tmp/dragon_drama/outline_50ep.txt 写《凡尘仙途》50集大纲\n2. file_write filepath=/tmp/dragon_drama/ep01_storyboard.txt 写第1集5镜分镜(每镜英文prompt)\n3. 用wan_video生成5镜 video (model=wan2.7-t2v, size=720x1280, duration=5, output_path=/tmp/dragon_drama/scene_01~05.mp4)\n4. execute command='cd /tmp/dragon_drama && for f in scene_0*.mp4; do echo \"file \\$f\" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y'\n5. execute command='ls -la /tmp/dragon_drama/ep01_final.mp4'\n\n开始第1步。"

async def agent_loop(max_iter=25):
    history = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK},
    ]
    
    for i in range(max_iter):
        print(f"\n--- Step {i+1} ---", flush=True)
        
        result = await registry.call(
            'openai', dc['m'],
            messages=history,
            max_tokens=2048,
            tools=TOOLS,
        )
        
        # Read tool_calls from raw
        msg = result.raw.get("choices", [{}])[0].get("message", {}) if result.raw else {}
        tc_list = msg.get("tool_calls", [])
        content = result.content or ""
        
        print(f"content: {content[:150]}", flush=True)
        
        if not tc_list and not content:
            print("Empty, retry")
            continue
        
        if not tc_list:
            if "DONE" in content.upper():
                print("DONE!")
                break
            history.append({"role": "assistant", "content": content})
            history.append({"role": "user", "content": "请调用下一个函数"})
            continue
        
        # Execute all tool calls
        for tc in tc_list:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except:
                args = {}
            
            print(f"  -> {name}({str(args)[:150]})", flush=True)
            
            # Add assistant message (simplified format — no extra fields)
            history.append({"role": "assistant", "content": content or f"Calling {name} with {json.dumps(args, ensure_ascii=False)[:200]}"})
            
            # Execute tool
            try:
                tr = await tool_registry.call(name, args)
                if tr.success:
                    out = str(tr.output)[:300]
                    print(f"  <- OK: {out[:150]}", flush=True)
                else:
                    out = f"Error: {tr.error}"
                    print(f"  <- FAIL: {out[:150]}", flush=True)
            except Exception as e:
                out = f"Exception: {e}"
                print(f"  <- EXC: {out[:120]}", flush=True)
            
            history.append({"role": "user", "content": f"Tool {name} result: {out[:400]}"})
        
        # Check completion
        if os.path.exists("/tmp/dragon_drama/ep01_final.mp4"):
            sz = os.path.getsize("/tmp/dragon_drama/ep01_final.mp4")
            if sz > 1000:
                print(f"\n*** SUCCESS! Video: {sz} bytes ***")
                break
    
    import glob
    files = sorted(glob.glob("/tmp/dragon_drama/*"))
    print(f"\n=== OUTPUT ({len(files)} files) ===")
    for f in files:
        sz = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({sz} bytes)")

asyncio.run(agent_loop())
