"""Quick test of media tools."""
import asyncio, json, sys
sys.path.insert(0, '.')
from dragon.workflow.steps import StepExecutor

async def test():
    exec = StepExecutor()
    
    # Test 1: edge_tts
    print('=== Test edge_tts ===')
    q = json.dumps({"text": "你好世界，这是短剧测试配音", "voice": "zh-CN-XiaoxiaoNeural", "output": "/tmp/tts_test_output.mp3"})
    result = await exec._call_tool('edge_tts', q)
    print(f'edge_tts result: {result}')
    
    # Test 2: comfyui_generate
    print('\n=== Test comfyui_generate ===')
    q2 = json.dumps({"prompt": "a cat sitting on a chair, cinematic lighting", "steps": 5, "width": 256, "height": 256})
    result2 = await exec._call_tool('comfyui_generate', q2)
    print(f'comfyui result: {json.dumps(result2, ensure_ascii=False)[:400]}')

asyncio.run(test())
