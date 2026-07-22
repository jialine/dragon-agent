"""Quick end-to-end tool verification"""
import asyncio, json, sys, os
sys.path.insert(0, '.')
os.chdir(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.')
from dragon.workflow.steps import StepExecutor

async def test():
    exec = StepExecutor()
    
    # Test 1: edge_tts with fixed path
    print('=== Test edge_tts ===')
    q = json.dumps({"text": "你好世界测试", "voice": "zh-CN-XiaoxiaoNeural", "output": "/tmp/tts_fix_test.mp3"})
    result = await exec._call_tool('edge_tts', q)
    print(f'edge_tts: {result}')
    if result.get('status') == 'ok':
        fsize = os.path.getsize(result['file']) if os.path.exists(result['file']) else 0
        print(f'File: {result["file"]} ({fsize} bytes)')
    
    # Test 2: comfyui with simple prompt
    print('\n=== Test comfyui_generate ===')
    q2 = json.dumps({"prompt": "a beautiful mountain landscape, cinematic", "steps": 5, "width": 256, "height": 256})
    result2 = await exec._call_tool('comfyui_generate', q2)
    print(f'comfyui: {json.dumps(result2, ensure_ascii=False)[:400]}')

asyncio.run(test())
