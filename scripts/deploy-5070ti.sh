#!/bin/bash
# Dragon Agent — Deploy to 5070 Ti (192.168.0.100)
# Run from local machine that has the code

set -e

TARGET="jialine@192.168.0.100"
SSH_OPTS="-o StrictHostKeyChecking=no"
DRAGON_DIR="/home/jialine/code/dragon-agent"
VENV_DIR="$DRAGON_DIR/.venv"

echo "=== 1. Sync code to 5070 Ti ==="
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
    --exclude '*.pyc' --exclude 'build/' \
    -e "sshpass -p 'Yuan0524!@#' ssh $SSH_OPTS" \
    /home/jialine/code/dragon-agent/ \
    $TARGET:$DRAGON_DIR/

echo "=== 2. Create venv ==="
sshpass -p 'Yuan0524!@#' ssh $SSH_OPTS $TARGET "
    cd $DRAGON_DIR
    python3 -m venv $VENV_DIR
    source $VENV_DIR/bin/activate
    pip install --upgrade pip -q
"

echo "=== 3. Install dependencies ==="
sshpass -p 'Yuan0524!@#' ssh $SSH_OPTS $TARGET "
    cd $DRAGON_DIR
    source $VENV_DIR/bin/activate
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
        fastapi uvicorn sqlalchemy passlib bcrypt PyJWT \
        python-multipart scikit-learn httpx pydantic \
        pyyaml python-dotenv 2>&1 | tail -5
"

echo "=== 4. Verify import ==="
sshpass -p 'Yuan0524!@#' ssh $SSH_OPTS $TARGET "
    cd $DRAGON_DIR
    source $VENV_DIR/bin/activate
    python -c '
from dragon.api import create_app, init_db
from dragon.confidence import ConfidenceCalibrator
app = create_app()
print(f\"OK: {len(app.routes)} routes\")
    '
"

echo "=== 5. Start server ==="
echo "Run manually on 5070 Ti:"
echo "  cd $DRAGON_DIR && source .venv/bin/activate"
echo "  uvicorn dragon.api.app:create_app --factory --host 0.0.0.0 --port 8780"
