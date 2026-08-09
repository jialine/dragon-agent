#!/bin/bash
unset FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_DOMAIN FEISHU_CONNECTION_MODE FEISHU_HOME_CHANNEL FEISHU_ALLOWED_USERS FEISHU_GROUP_POLICY FEISHU_ALLOW_ALL_USERS FEISHU_HOME_CHANNEL_THREAD_ID
unset OPENAI_API_KEY OPENAI_BASE_URL DEEPSEEK_API_KEY DRAGON_API_KEY DRAGON_BASE_URL DRAGON_MODEL

export FEISHU_APP_ID=cli_aab694730bb8dcd6
export FEISHU_APP_SECRET=3lxuIiJiTwxwYaXYXhdSZUe4YdY1ssZP
export FEISHU_DOMAIN=feishu
export FEISHU_CONNECTION_MODE=websocket
export HOME=/root
export PATH=/usr/bin:/usr/local/bin:/root/.hermes/hermes-agent/venv/bin

cd /home/jialine/dragon-agent
exec /root/.hermes/hermes-agent/venv/bin/python -m dragon.cli gateway --feishu
