#!/bin/bash
# Dragon Agent Remote Start Script — run via SSH
set -e
cd /home/jialine/code/dragon-agent
source .venv/bin/activate
exec python -m uvicorn dragon.main:app --host 0.0.0.0 --port 8780 --log-level info
