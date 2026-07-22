#!/usr/bin/env python3
"""Submit drama task to Dragon with native wan_video tool."""
import asyncio, os, yaml
from pathlib import Path

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
    return {'api_key':'not-needed','base_url':None,'model':'gpt-4o','timeout_secs':60}

from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig
from dragon.gateway.server import MessageProcessor
from dragon.gateway.base import PlatformMessage
from dragon.session import SessionStore
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

dc = load_dispatch_config()
registry = ProviderRegistry()
registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai', api_key=dc['api_key'], base_url=dc['base_url'],
    default_model=dc['model'], timeout_secs=dc['timeout_secs'])))

session_store = SessionStore()
tool_registry = ToolRegistry()
register_builtins(tool_registry)

processor = MessageProcessor(
    provider_registry=registry, session_store=session_store,
    tool_registry=tool_registry, max_tool_iterations=90)

SYSTEM = """你是 Dragon Agent，全自主执行引擎。使用工具调用实际完成任务。

工具规则：
- execute: 运行 shell 命令。优先用这个创建目录、移动文件、ffmpeg 合成
- file_write: 写入文本文件（大纲、剧本）
- wan_video: 生成视频。参数: prompt(英文), model="wan2.7-t2v", size="720x1280", duration=5
  工具自动处理提交→轮询→下载，返回 success + video_path
- web_download: 下载文件
- 每轮调用1个工具，等结果再继续
- 不要描述要做什么，直接调用工具
- Token 无限制"""

TASK = """真人修仙竖屏短剧《凡尘仙途》第1集。竖屏720x1280，真人写实风格。每步实际调用工具。

## 第1步：创建目录
execute: mkdir -p /tmp/dragon_drama

## 第2步：写50集大纲
file_write: /tmp/dragon_drama/outline_50ep.txt
主角林玄，资质平庸外门弟子得古玉简传承。炼气→筑基→金丹→元婴→化神。
50集，每集一行概括核心冲突。中国修仙，真人风格。

## 第3步：写第1集5镜分镜（先用5镜测试管道）
file_write: /tmp/dragon_drama/ep01_storyboard.txt
第1集《坠崖得古卷》：林玄被同门欺凌坠落悬崖，山洞得玉简。
5镜×5秒=25秒。每镜含：
- 镜号 01-05
- 英文画面描述（cinematic realistic Chinese xianxia, live-action movie style, detailed character appearance, costume, lighting, camera angle）

## 第4步：逐镜生成视频（用wan_video工具）
对每镜调用 wan_video:
- model: "wan2.7-t2v"
- prompt: 该镜的英文画面描述
- size: "720x1280"
- duration: 5
- output_path: "/tmp/dragon_drama/scene_NN.mp4" (NN=01,02,03,04,05)

工具返回 JSON，含 video_path。等每个完成后继续下一个。

## 第5步：合成
execute: cd /tmp/dragon_drama && rm -f files.txt && for f in scene_0*.mp4; do echo "file '$f'" >> files.txt; done && ffmpeg -f concat -safe 0 -i files.txt -c copy ep01_final.mp4 -y

## 第6步：验证
execute: ls -la /tmp/dragon_drama/ep01_final.mp4 && echo DONE

开始。现在立即执行第1步。"""

message = PlatformMessage(platform="cli", chat_id="drama_v3", user_id="__system__", content=TASK)

async def main():
    result = await processor.process(message, system_prompt=SYSTEM, output_mode="text")
    print("=== FINAL ===")
    print(result.content if result else "None")
    import glob
    files = sorted(glob.glob("/tmp/dragon_drama/*"))
    print(f"\n=== FILES ({len(files)}) ===")
    for f in files:
        sz = os.path.getsize(f) if os.path.exists(f) else 0
        print(f"  {f} ({sz} bytes)")

asyncio.run(main())
