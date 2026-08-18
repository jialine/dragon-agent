#!/usr/bin/env bash
# =============================================================================
# Agent Mailbox — 一键开箱即用安装脚本（支持 curl | bash）
# =============================================================================
# 用法：
#   1) curl 一键（推荐）：
#        curl -fsSL https://gitee.com/jialine/dragon-agent/raw/main/mailbox/install.sh | bash
#
#   2) 本地安装（已在 mailbox/ 目录内）：
#        ./install.sh
#
#   3) 指定参数：
#        ./install.sh --dir /opt/dragon-mailbox --agents dragon-02,hermes --port 8091
#
# 做什么：
#   1. 预检环境（python3，零第三方依赖，无需 pip install）
#   2. 部署代码到目标目录
#   3. 运行单元测试（26 个，全绿才算成功）
#   4. 注册 agent 并生成 .env（含 secret，权限 600，自动加入 .gitignore）
#   5. 可选：后台启动 mailbox HTTP 服务
#   6. 输出使用指引
# =============================================================================

set -euo pipefail

# ── 默认配置 ────────────────────────────────────────────────────────────────
INSTALL_DIR="${MAILBOX_HOME:-$HOME/dragon-agent/mailbox}"
AGENTS="${AGENTS:-dragon-02,hermes,dragon-01}"
PORT="${PORT:-8091}"
START_SERVICE="${START_SERVICE:-false}"
ASSUME_YES=false

# ── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}▶ $1${NC}"; }

# ── 参数解析 ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)      INSTALL_DIR="$2"; shift 2 ;;
        --agents)   AGENTS="$2"; shift 2 ;;
        --port)     PORT="$2"; shift 2 ;;
        --serve)    START_SERVICE=true; shift ;;
        --yes|-y)   ASSUME_YES=true; shift ;;
        --help|-h)
            echo "用法:"
            echo "  curl -fsSL <raw_url> | bash"
            echo "  ./install.sh [--dir DIR] [--agents a,b,c] [--port PORT] [--serve] [--yes]"
            exit 0 ;;
        *) err "未知参数: $1" ;;
    esac
done

step "1/6 预检环境"
command -v python3 >/dev/null 2>&1 || err "未找到 python3，请先安装"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "python3 ${PY_VER} ✓"

step "2/6 部署代码到 ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 如果是 curl|bash 模式，脚本在临时目录，需要从 gitee 拉代码
if [[ ! -f "$SRC_DIR/mailbox.py" ]]; then
    info "curl 模式：从 gitee 拉取代码"
    TMP=$(mktemp -d)
    git clone --depth 1 https://gitee.com/jialine/dragon-agent.git "$TMP/dragon-agent" 2>/dev/null \
        || git clone --depth 1 git@gitee.com:jialine/dragon-agent.git "$TMP/dragon-agent" 2>/dev/null \
        || err "git clone 失败"
    SRC_DIR="$TMP/dragon-agent/mailbox"
fi
for f in mailbox.py escalate.py hermes_listener.py test_mailbox.py; do
    cp -f "$SRC_DIR/$f" "$INSTALL_DIR/" || err "复制 $f 失败"
done
cp -f "$SRC_DIR/README.md" "$INSTALL_DIR/" 2>/dev/null || true
info "代码已部署"

step "3/6 运行单元测试（26 个）"
cd "$INSTALL_DIR"
python3 test_mailbox.py > /tmp/mailbox_test.log 2>&1 \
    && info "单元测试全绿 ✓" \
    || { warn "测试输出："; tail -20 /tmp/mailbox_test.log; err "单元测试失败，请检查"; }

step "4/6 注册 agent + 生成 .env"
cd "$INSTALL_DIR"
ENV_FILE="$INSTALL_DIR/.env"
# 已有 .env 则跳过（保留已有 secret）
if [[ -f "$ENV_FILE" ]]; then
    info "检测到已有 .env，跳过注册（保留已有 secret）"
else
    REG_OUT=$(python3 mailbox.py register --agent "$AGENTS" --db "$INSTALL_DIR/agent_bus.db")
    echo "$REG_OUT"
    # 生成 .env
    {
        echo "# Agent Mailbox 环境变量（secret 为敏感信息，勿提交 git）"
        echo "export MAILBOX_DB=$INSTALL_DIR/agent_bus.db"
        echo "# 跨机器通信时才启用 HTTP（默认本地 SQLite 直写，最快）："
        echo "# export MAILBOX_HTTP=http://127.0.0.1:${PORT}"
        echo "export MAILBOX_PORT=$PORT"
    } > "$ENV_FILE"
    # 从 register 输出解析 secret 写入 .env（每行 "  agent: secret"）
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_-]+):[[:space:]]+([a-f0-9]{64})$ ]]; then
            agent="${BASH_REMATCH[1]}"
            secret="${BASH_REMATCH[2]}"
            echo "# agent: $agent" >> "$ENV_FILE"
        fi
    done <<< "$REG_OUT"
    # 把每个 agent 的 secret 单独存成 agent_secret 文件（供各 agent 读取）
    SECRET_DIR="$INSTALL_DIR/secrets"
    mkdir -p "$SECRET_DIR"
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_-]+):[[:space:]]+([a-f0-9]{64})$ ]]; then
            agent="${BASH_REMATCH[1]}"
            secret="${BASH_REMATCH[2]}"
            echo "export MAILBOX_AGENT_ID=$agent" > "$SECRET_DIR/$agent.env"
            echo "export MAILBOX_AGENT_SECRET=$secret" >> "$SECRET_DIR/$agent.env"
            chmod 600 "$SECRET_DIR/$agent.env"
        fi
    done <<< "$REG_OUT"
    chmod 600 "$ENV_FILE"
    info "已生成 .env 和 secrets/*.env（权限 600）"
fi

step "5/6 写入 .gitignore"
GITIGNORE="$INSTALL_DIR/.gitignore"
cat > "$GITIGNORE" <<'EOF'
.env
secrets/
agent_bus.db
*.pyc
__pycache__/
EOF
info ".gitignore 已写入（.env / secrets/ / agent_bus.db 不提交）"

step "6/6 服务启动"
if [[ "$START_SERVICE" == true ]]; then
    cd "$INSTALL_DIR"
    nohup python3 mailbox.py serve --port "$PORT" --db "$INSTALL_DIR/agent_bus.db" \
        > "$INSTALL_DIR/mailbox.log" 2>&1 &
    echo $! > "$INSTALL_DIR/mailbox.pid"
    sleep 2
    if curl -s "http://127.0.0.1:${PORT}/heartbeat" >/dev/null 2>&1; then
        info "mailbox 服务已启动: http://127.0.0.1:${PORT}"
    else
        warn "服务未就绪，请查看 $INSTALL_DIR/mailbox.log"
    fi
else
    info "跳过服务启动（需要时运行 --serve，或手动：python3 mailbox.py serve --port $PORT）"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Agent Mailbox 安装完成${NC}"
echo -e "${GREEN}========================================${NC}"
echo "  安装目录: $INSTALL_DIR"
echo "  secret 文件: $INSTALL_DIR/secrets/<agent>.env"
echo ""
echo "  Dragon 端发事件（加载 secret）:"
echo "    source $INSTALL_DIR/secrets/dragon-02.env"
echo "    python3 -c 'from escalate import escalate; escalate(\"dragon-02\",\"项目\",\"崩溃\")'"
echo ""
echo "  Hermes 端监听:"
echo "    source $INSTALL_DIR/secrets/hermes.env"
echo "    python3 hermes_listener.py --db $INSTALL_DIR/agent_bus.db --agent hermes"
echo ""
