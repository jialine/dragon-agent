"""Test drama workflow end-to-end"""
import asyncio
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
os.environ["FRAMEWORK_WORKFLOW_LOGGING"] = "main"
os.environ["DRAGON_LOG_LEVEL"] = "DEBUG"

from dragon.workflow import WorkflowEngine

async def main():
    engine = WorkflowEngine(workflows_dir="workflows")
    wf = engine.load("drama")
    
    test_input = {
        "topic": "深夜便利店，一个失眠的程序员遇到一个只喝热牛奶的神秘女孩",
        "genre": "都市情感微短剧",
        "duration": "3分钟",
        "style": "王家卫风格",
        "audience": "年轻白领",
        "need_visual": True,
        "need_bgm": True,
    }
    
    print("=" * 60)
    print("Running drama workflow...")
    print("=" * 60)
    
    result = await engine.run(wf, test_input)
    print(f"\n{'='*60}")
    print(f"Result: {len(result.steps)} steps, success={result.success}")
    for s in result.steps:
        status = "✅" if s.success else "❌"
        print(f"  {status} {s.step_id} ({s.step_type.value}): {str(s.output)[:100]}...")

asyncio.run(main())
