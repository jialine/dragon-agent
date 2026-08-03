#!/bin/bash
# Drama Studio WebUI launcher
cd "$(dirname "$0")"

# Install deps if needed
pip3 install --break-system-packages -q flask flask-cors pyyaml 2>/dev/null

# Start server
PORT="${PORT:-5000}"
echo "🎬 Drama Studio starting on http://0.0.0.0:${PORT}"
python3 app.py
