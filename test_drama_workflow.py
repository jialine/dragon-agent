"""Test drama workflow end-to-end with real dispatcher"""
import asyncio
import sys, os, logging

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from dragon.workflow import WorkflowEngine

async def main():
    from dragon.config import DragonConfig
    config = DragonConfig.load()
    
    from dragon.dispatch import DragonDispatcher, ProviderProfile
    dispatcher = DragonDispatcher()
    ga = config.dispatch.global_api
    for industry, ic in config.dispatch.industries.items():
        dispatcher.register(industry, profile=ProviderProfile(
            name=industry,
            provider="sangyuye",
            model=ga.model,
            api_key_env=ga.api_key_env,
            base_url=ga.base_url,
            system_prompt=ic.system_prompt,
            timeout=ga.timeout_secs,
            max_retries=ga.max_retries,
        ))
    
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
        "_dispatcher": dispatcher,
    }
    
    print("=" * 60)
    print("Running drama workflow...")
    print("=" * 60)
    
    result = await engine.run(wf, test_input)
    print(f"\n{'='*60}")
    print(f"Result: {len(result.steps)} steps, success={result.success}")
    for s in result.steps:
        status = "✅" if s.success else "❌"
        output_str = str(s.output)[:120] if s.output else "None"
        print(f"  {status} {s.step_id} ({s.step_type.value}): {output_str}")

asyncio.run(main())
