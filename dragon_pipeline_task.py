#!/usr/bin/env python3
"""Submit a task to Dragon Gateway's message processor with full tool access."""
import asyncio, os, sys, yaml
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    env_path = Path.home() / ".dragon" / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

def load_dispatch_config():
    paths = ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f) or {}
            api = cfg.get('dispatch', {}).get('global_api', {})
            if api:
                return {
                    "api_key": os.getenv(api.get('api_key_env', ''), ''),
                    "base_url": api.get('base_url'),
                    "model": api.get('model', 'gpt-4o'),
                    "timeout_secs": api.get('timeout_secs', 300),
                }
    return {'api_key': 'not-needed', 'base_url': None, 'model': 'gpt-4o', 'timeout_secs': 60}

from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig
from dragon.gateway.server import MessageProcessor
from dragon.gateway.types import PlatformMessage
from dragon.session import SessionStore
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

dc = load_dispatch_config()
registry = ProviderRegistry()

registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai',
    api_key=dc['api_key'],
    base_url=dc['base_url'],
    default_model=dc['model'],
    timeout_secs=dc['timeout_secs'],
)))

session_store = SessionStore()
tool_registry = ToolRegistry()
register_builtins(tool_registry)

processor = MessageProcessor(
    provider_registry=registry,
    session_store=session_store,
    tool_registry=tool_registry,
    max_tool_iterations=90,
)

task = """你是Dragon Agent。自主完成以下任务的全流程，使用工具调用（subprocess+curl）来实际执行每一步，不要只输出文本描述。

## 任务：真人修仙竖屏短剧 - 第1集

### 第1步（必须实际执行）：创建目录
```bash
mkdir -p /tmp/dragon_drama
```

### 第2步（必须实际执行）：生成50集大纲并写入文件
用 write_file 工具写入 /tmp/dragon_drama/outline_50ep.txt

### 第3步（必须实际执行）：用 subprocess+curl 逐镜生成视频
WAN API 参数:
POST https://api.lingyuncx.com/v1/videos/generations
Authorization: Bearer sk-nveh4vt2hm1ewfthbazmm6a3nsxdktjd
Content-Type: application/json
Body: {"model":"wan2.7-t2v","prompt":"ENGLISH PROMPT","duration":3,"parameters":{"resolution":"720P","ratio":"9:16","prompt_extend":true},"watermark":false}

流程:
1. POST 获取 task_id
2. 每3秒 GET https://api.lingyuncx.com/v1/tasks/{task_id} 查状态
3. status=completed 后用 curl -o 下载 video_url 到 /tmp/dragon_drama/scene_NN.mp4

第1集20镜，每镜3秒。Prompt 用英文描述真人写实修仙场景。

### 第4步：合成
```bash
cd /tmp/dragon_drama
for f in scene_*.mp4; do echo "file '$f'" >> files.txt; done
ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4
```

现在是实战时间，直接动手！"""

message = PlatformMessage(
    platform="cli",
    chat_id="drama_task",
    user_id="__system__",
    content=task,
)

async def main():
    result = await processor.process(
        message,
        system_prompt="你是一个全自主AI，使用工具调用来实际执行任务。每次tool_call后继续下一轮，直到任务完成。Token无限制。",
        output_mode="text",
    )
    print("=== RESULT ===")
    print(result.content if result else "No result")
    
    # Check output files
    import glob
    files = glob.glob("/tmp/dragon_drama/*")
    print(f"\n=== OUTPUT FILES ({len(files)}) ===")
    for f in sorted(files):
        size = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({size} bytes)")

asyncio.run(main())
