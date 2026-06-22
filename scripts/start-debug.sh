#!/bin/bash
# Dragon Agent start script — writes to log file
cd /home/jialine/code/dragon-agent
source .venv/bin/activate
exec python -m uvicorn dragon.main:app --host 0.0.0.0 --port 8780 --log-level debug 2>&1 | tee /tmp/dragon.log
