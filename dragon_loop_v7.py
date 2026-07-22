#!/usr/bin/env python3
"""Direct tool-calling agent loop — v7, reads tool_calls from raw response."""
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
    {"type":"function","function":{"name":"execute","description":"Execute shell command. Returns stdout/stderr.","parameters":{"type":"object","properties":{"command":{"type":"string","description":"Shell command"}},"required":["command"]}}},
    {"type":"function","function":{"name":"file_write","description":"Write content to file. Creates parent dirs.","parameters":{"type":"object","properties":{"filepath":{"type":"string","description":"File path"},"content":{"type":"string","description":"File content"},"append":{"type":"boolean","description":"Append?"}},"required":["filepath","content"]}}},
    {"type":"function","function":{"name":"wan_video","description":"Generate AI video via Wan2.7 T2V. Returns video_path.","parameters":{"type":"object","properties":{"prompt":{"type":"string","description":"English video prompt"},"model":{"type":"string"},"size":{"type":"string"},"duration":{"type":"integer"},"output_path":{"type":"string"}},"required":["prompt"]}}},
]

SYSTEM = "你是全自主执行引擎。用函数调用完成任务。工具: execute(command), file_write(filepath, content), wan_video(prompt, model, size, duration, output_path)。完成后调用 send_final_report。"

TASK = """真人修仙竖屏短剧《凡尘仙途》第1集。目录/tmp/dragon_drama已建好。720x1280竖屏。

严格按顺序执行:
1. file_write filepath=/tmp/dragon_drama/outline_50ep.txt 写《凡尘仙途》50集大纲。主角林玄，得古玉简，炼气→化神。每集一行核心冲突。
2. file_write filepath=/tmp/dragon_drama/ep01_storyboard.txt 写第1集《坠崖得古卷》5镜分镜，每镜英文:
镜01: A young Chinese man Lin Xuan in worn gray robes stands alone on misty cliff edge at sunrise, golden light piercing clouds, cinematic live-action movie photography, vertical 9:16
镜02: Three arrogant inner disciples in blue robes sneer and shove Lin Xuan toward cliff edge, dramatic low angle, wind whipping robes, live-action cinematography, 9:16 vertical
镜03: Lin Xuan falling through swirling mist, robes billowing, jagged cliffs rushing past, shock turning to fierce will, dramatic sun rays through fog, photorealistic live-action, 9:16 vertical
镜04: Lin Xuan crashes through tree branches, lands on mossy rocks by glowing turquoise pool, ancient jade tablet emitting golden light, bioluminescent flora, live-action cinematic, 9:16 vertical
镜05: Close-up of bloodied hand touching jade slip, golden light erupting, ancient runes floating, bruised face illuminated with awe, spiritual energy flowing, photorealistic, 9:16 vertical

3. wan_video x5: model="wan2.7-t2v", size="720x1280", duration=5, output_path="/tmp/dragon_drama/scene_01.mp4"~scene_05.mp4, prompt=上面分镜英文。等每镜完成。

4. execute command="cd /tmp/dragon_drama && for f in scene_0*.mp4; do echo \"file '\$f'\" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y"

5. execute command="ls -la /tmp/dragon_drama/ep01_final.mp4"

开始！先执行第1步。"""

async def agent_loop(max_iter=25):
    history = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK},
    ]
    
    # Map tool names for function call extraction
    TOOL_MAP = {t["function"]["name"]: t for t in TOOLS}
    
    for i in range(max_iter):
        print(f"\n--- Step {i+1} ---", flush=True)
        
        try:
            result = await registry.call(
                'openai', dc['m'],
                messages=history,
                max_tokens=2048,
                tools=TOOLS,
            )
        except Exception as e:
            print(f"LLM call failed: {e}")
            if len(history) > 6:
                history = history[:2] + history[-4:]
                continue
            break
        
        # Check for tool_calls in raw response
        tool_calls = []
        if result.raw:
            try:
                msg = result.raw.get("choices", [{}])[0].get("message", {})
                tc_list = msg.get("tool_calls", [])
                for tc in tc_list:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except:
                        args = {}
                    tool_calls.append((name, args))
            except Exception as e:
                print(f"  raw parse error: {e}")
        
        content = result.content or ""
        print(f"LLM content: {content[:200]}", flush=True)
        
        if tool_calls:
            for tc_name, tc_args in tool_calls:
                print(f"  -> tool_call: {tc_name}({str(tc_args)[:150]})", flush=True)
        elif not content:
            print("  empty response, retrying...")
            history.append({"role": "user", "content": "请调用下一个函数。"})
            continue
        
        # If no tool calls, check content for manual tool call
        if not tool_calls:
            if "DONE" in content.upper() or "完成" in content:
                print("Task complete!")
                break
            # Try to extract tool call from text
            m = re.search(r'"tool"\s*:\s*"(\w+)"', content)
            if m:
                tc_name = m.group(1)
                m2 = re.search(r'"params"\s*:\s*(\{[^}]+\})', content)
                if m2:
                    try: tc_args = json.loads(m2.group(1))
                    except: tc_args = {}
                    tool_calls = [(tc_name, tc_args)]
                    print(f"  -> text_parse: {tc_name}({str(tc_args)[:150]})", flush=True)
        
        if not tool_calls:
            history.append({"role": "assistant", "content": content})
            history.append({"role": "user", "content": "请调用下一个函数。不要输出文本，直接调用函数。"})
            continue
        
        # Execute tool calls
        for tc_name, tc_args in tool_calls:
            history.append({
                "role": "assistant",
                "content": content or f"Calling {tc_name}",
                "tool_calls": [{
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": tc_name, "arguments": json.dumps(tc_args, ensure_ascii=False)}
                }]
            })
            
            try:
                tr = await tool_registry.call(tc_name, tc_args)
                if tr.success:
                    out = str(tr.output)[:400]
                    print(f"  <- OK: {out[:150]}", flush=True)
                else:
                    out = f"ERROR: {tr.error}"
                    print(f"  <- FAIL: {out[:150]}", flush=True)
            except Exception as e:
                out = f"Exception: {e}"
                print(f"  <- EXC: {out[:150]}", flush=True)
            
            history.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": out[:500]
            })
        
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
