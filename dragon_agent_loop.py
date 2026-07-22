#!/usr/bin/env python3
"""Direct tool-calling agent loop — bypass Dragon orchestrator."""
import asyncio, json, os, re, yaml, time
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    if (Path.home() / ".dragon" / ".env").exists():
        load_dotenv(Path.home() / ".dragon" / ".env")
except ImportError:
    pass

def load_dispatch_config():
    for p in ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]:
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f) or {}
            api = cfg.get('dispatch', {}).get('global_api', {})
            if api:
                return {"api_key": os.getenv(api.get('api_key_env',''),''),
                        "base_url": api.get('base_url'),
                        "model": api.get('model','gpt-4o'),
                        "timeout_secs": api.get('timeout_secs',300)}
    return {'api_key':'','base_url':None,'model':'gpt-4o','timeout_secs':60}

dc = load_dispatch_config()

from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

# Setup
registry = ProviderRegistry()
registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai', api_key=dc['api_key'], base_url=dc['base_url'],
    default_model=dc['model'], timeout_secs=dc['timeout_secs'])))

tool_registry = ToolRegistry()
register_builtins(tool_registry)
tool_schemas = tool_registry.get_openai_schemas()

SYSTEM = """你是 Dragon Agent，一个全自主执行引擎。

你有工具可以调用，用来实际完成任务：
- execute: 运行 shell 命令
- file_write: 写入文件  
- wan_video: 生成 AI 视频 (model="wan2.7-t2v", size="720x1280", duration=5)
- web_download: 下载文件

规则：
1. 每次回复包含工具调用，格式: <tool_calls><invoke name="TOOL_NAME"><parameter name="PARAM">VALUE</parameter></invoke></tool_calls>
2. 每次只调用一个工具
3. 不要只描述，直接调用工具
4. 任务完成后说 DONE
5. Token无限制"""

TASK = """执行以下任务。每步调用一个工具。

## 第1步
execute: mkdir -p /tmp/dragon_drama

## 第2步
file_write 写入 /tmp/dragon_drama/outline_50ep.txt
内容：《凡尘仙途》50集大纲。主角林玄，资质平庸外门弟子获古玉简传承。
炼气→筑基→金丹→元婴→化神。中国修仙真人风格。每集一行核心冲突。

## 第3步  
file_write 写入 /tmp/dragon_drama/ep01_storyboard.txt
第1集《坠崖得古卷》5镜分镜脚本。每镜：
- 镜号 01-05
- 英文画面描述 (cinematic realistic Chinese xianxia, live-action movie, 9:16 vertical, detailed character/costume/lighting/camera)

## 第4步
对每镜调用 wan_video:
model="wan2.7-t2v", prompt="该镜英文描述", size="720x1280", duration=5, output_path="/tmp/dragon_drama/scene_NN.mp4"
等每个完成再下一个。

## 第5步
execute: cd /tmp/dragon_drama && rm -f files.txt && for f in scene_0*.mp4; do echo "file '$f'" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y

## 第6步
execute: ls -la /tmp/dragon_drama/ep01_final.mp4

开始！立即调用第1步工具。"""

async def agent_loop(max_iter=60):
    history = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK},
    ]
    
    for i in range(max_iter):
        print(f"\n--- Iteration {i+1} ---")
        
        # Call LLM with tools
        result = await registry.call(
            'openai', dc['model'],
            messages=history,
            max_tokens=4096,
            tools=tool_schemas,
        )
        
        response = result.content
        print(f"LLM: {response[:300]}...")
        
        # Parse tool calls from response
        tc_pattern = r'<invoke name="([^"]+)">(.*?)</invoke>'
        matches = re.findall(tc_pattern, response, re.DOTALL)
        
        if not matches:
            # Check for JSON format tool calls
            try:
                # Try to find JSON in the response
                json_match = re.search(r'\{[^{}]*"tool"[^{}]*\}', response)
                if json_match:
                    tc_data = json.loads(json_match.group())
                    matches = [(tc_data.get("tool",""), json.dumps(tc_data.get("params",{})))]
            except:
                pass
        
        if not matches:
            print("No tool calls found. Checking for DONE...")
            if "DONE" in response.upper():
                print("Task complete!")
                break
            # Push for more action
            history.append({"role": "assistant", "content": response})
            history.append({"role": "user", "content": "继续。调用下一个工具。不要只输出文本。如果所有步骤完成，说 DONE。"})
            continue
        
        # Execute tools
        history.append({"role": "assistant", "content": response})
        
        for tool_name, params_str in matches:
            print(f"  -> Calling tool: {tool_name}")
            
            # Parse params
            params = {}
            for pm in re.finditer(r'<parameter name="([^"]+)">(.*?)</parameter>', params_str, re.DOTALL):
                params[pm.group(1)] = pm.group(2).strip()
            
            if not params:
                # Try JSON params
                try:
                    params = json.loads(params_str.strip())
                except:
                    params = {"prompt": params_str.strip()}
            
            print(f"     params: {str(params)[:200]}")
            
            # Execute
            try:
                tr = await tool_registry.call(tool_name, params)
                out = str(tr.output) if tr.success else f"ERROR: {tr.error}"
            except Exception as e:
                out = f"Exception: {e}"
            
            print(f"     result: {out[:300]}")
            history.append({"role": "tool", "content": out[:2000], "name": tool_name})
        
        # Check if we have the final video
        if os.path.exists("/tmp/dragon_drama/ep01_final.mp4"):
            print("\nFinal video exists! Checking...")
            sz = os.path.getsize("/tmp/dragon_drama/ep01_final.mp4")
            if sz > 1000:
                print(f"SUCCESS: /tmp/dragon_drama/ep01_final.mp4 ({sz} bytes)")
                break
    
    # Final report
    import glob
    files = sorted(glob.glob("/tmp/dragon_drama/*"))
    print(f"\n=== OUTPUT ({len(files)} files) ===")
    for f in files:
        sz = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({sz} bytes)")

asyncio.run(agent_loop())
