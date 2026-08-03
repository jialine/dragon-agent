#!/bin/bash
cd /home/jialine/dragon-agent
python3 drama_eps_v2.py
echo "Pushing to Feishu..."
python3 drama_feishu_push.py "$(cat /tmp/drama_eps/script.json | python3 -c "import sys,json;print(json.load(sys.stdin).get('title', '短剧'))")" "2集修仙短剧·1080P竖屏" /tmp/drama_eps
echo "Done"
