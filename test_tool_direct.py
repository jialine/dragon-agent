"""Directly test tool execution without full workflow."""
import asyncio
import sys
sys.path.insert(0, "/home/jialine/code/dragon-agent")
from dragon.workflow.steps import StepExecutor
from dragon.workflow import StepDefinition, StepType

async def main():
    executor = StepExecutor(dispatcher=None)
    
    # Test comfyui_generate
    step = StepDefinition(
        id="test_img",
        name="Test Image Gen",
        type=StepType.TOOL_CALL,
        config={"tool": "comfyui_generate", "input": '{"prompt": "a beautiful sunset over mountains, cinematic lighting", "steps": 5, "width": 256, "height": 256}'},
        condition=None
    )
    ctx = {}
    print("=== Testing comfyui_generate ===")
    result = await executor._execute_tool(step, ctx)
    print(f"Result: {result}")
    
    # Test edge_tts
    step2 = StepDefinition(
        id="test_tts",
        name="Test TTS",
        type=StepType.TOOL_CALL,
        config={"tool": "edge_tts", "input": '{"text": "你好世界", "voice": "zh-CN-XiaoxiaoNeural"}'},
        condition=None
    )
    print("\n=== Testing edge_tts ===")
    result2 = await executor._execute_tool(step2, ctx)
    print(f"Result: {result2}")

asyncio.run(main())
