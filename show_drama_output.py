"""Run drama workflow and print full LLM outputs"""
import asyncio
import sys, os, json, logging

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
            name=industry, provider="sangyuye", model=ga.model,
            api_key_env=ga.api_key_env, base_url=ga.base_url,
            system_prompt=ic.system_prompt, timeout=ga.timeout_secs,
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
    
    result = await engine.run(wf, test_input)
    
    # Print full output for each LLM step
    for s in result.steps:
        if s.step_type.value in ("llm_call",) and s.output:
            print(f"\n{'='*60}")
            print(f"STEP: {s.step_id}")
            print(f"{'='*60}")
            print(s.output)
    
    # Print tool call results
    for s in result.steps:
        if s.step_type.value in ("tool_call",) and s.output:
            print(f"\nTOOL: {s.step_id} → {s.output}")

asyncio.run(main())
