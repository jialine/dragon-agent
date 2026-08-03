#!/usr/bin/env python3
"""Submit a task to Dragon Agent using the dispatch config (same as gateway)."""
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

# Load dispatch config
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

dc = load_dispatch_config()
registry = ProviderRegistry()

# Register openai provider using dispatch config
registry.register('openai', OpenAIProvider(ProviderConfig(
    provider='openai',
    api_key=dc['api_key'],
    base_url=dc['base_url'],
    default_model=dc['model'],
    timeout_secs=dc['timeout_secs'],
)))

async def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "你好"

    result = await registry.call(
        'openai', dc['model'],
        messages=[{"role": "user", "content": task}],
    )
    print(result.content)

asyncio.run(main())
