#!/usr/bin/env bash
# ================================================================
#  Dragon Agent — curl 一键部署
#  用法: curl -fsSL <raw-url> | bash
# ================================================================
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
BOLD='\033[1m'; DIM='\033[2m'

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║       🐉 Dragon Agent Installer     ║"
echo "  ║    默认 DeepSeek V4 Pro · 即装即用   ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

INSTALL_DIR="${INSTALL_DIR:-$HOME/dragon-agent}"; BRANCH="${BRANCH:-main}"
SKIP_TEST="${SKIP_TEST:-0}"; START_WEBUI="${START_WEBUI:-0}"
WEBUI_PORT="${WEBUI_PORT:-5000}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --skip-test) SKIP_TEST=1; shift ;;
        --start-webui) START_WEBUI=1; shift ;;
        --webui-port) WEBUI_PORT="$2"; shift 2 ;;
        -h|--help) echo "用法: curl -fsSL <url> | bash [--dir PATH] [--skip-test] [--start-webui] [--webui-port PORT]"; exit 0 ;;
        *) echo -e "${RED}未知参数: $1${NC}"; exit 1 ;;
    esac
done

# ── 1. 系统检查 ──────────────────────────────────────────────
echo -e "\n${BOLD}[1/5]${NC} 检查系统..."
command -v python3 &>/dev/null || { echo -e "${RED}需要 Python 3.11+${NC}"; exit 1; }
PY_OK=$(python3 -c 'import sys; print("OK" if sys.version_info>=(3,11) else "FAIL")')
[ "$PY_OK" = "OK" ] || { echo -e "${RED}Python >= 3.11 必需${NC}"; exit 1; }
echo -e "  ${GREEN}✓${NC} Python $(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
command -v git &>/dev/null || { sudo apt-get install -y -qq git 2>/dev/null || sudo dnf install -y -q git 2>/dev/null; }
echo -e "  ${GREEN}✓${NC} git"

# ── 2. 代码 ──────────────────────────────────────────────────
echo -e "\n${BOLD}[2/5]${NC} 获取代码..."
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"; git pull origin "$BRANCH" --quiet 2>/dev/null || true
else
    git clone --depth 1 --branch "$BRANCH" "https://gitee.com/jialine/dragon-agent.git" "$INSTALL_DIR" 2>/dev/null || \
    git clone --depth 1 "https://github.com/jialine/dragon-agent.git" "$INSTALL_DIR" 2>/dev/null || \
    { echo -e "${RED}克隆失败${NC}"; exit 1; }
fi
cd "$INSTALL_DIR"
echo -e "  ${GREEN}✓${NC} $(git rev-parse --short HEAD 2>/dev/null || echo latest)"

# ── 3. 依赖 ──────────────────────────────────────────────────
echo -e "\n${BOLD}[3/5]${NC} 安装依赖..."
[ ! -d ".venv" ] && python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q 2>/dev/null

# 测试框架
pip install pytest pytest-asyncio -q 2>/dev/null

# 核心依赖（无 llama-cpp，秒装）
pip install -r requirements.txt -q 2>/dev/null
pip install -e . -q 2>/dev/null || true
echo -e "  ${GREEN}✓${NC} 依赖完成"

# ── 4. 配置 ──────────────────────────────────────────────────
echo -e "\n${BOLD}[4/5]${NC} 默认配置 (DeepSeek V4 Pro)..."
if [ ! -f "config.yaml" ]; then
    cat > config.yaml << 'YAML'
# Dragon Agent — 默认 DeepSeek V4 Pro
# 前往 https://api.andlapi.cn 注册获取 API Key
gateway:
  host: "0.0.0.0"
  port: 8090

providers:
  deepseek:
    api_key: "${DEEPSEEK_API_KEY:-sk-your-key}"
    base_url: "https://api.andlapi.cn/v1"
    model: "deepseek-chat"

# 本地模型已弃用，统一走云端 API
YAML
    echo -e "  ${GREEN}✓${NC} config.yaml 已创建"
    echo -e "  ${YELLOW}⚠${NC}  前往 https://api.andlapi.cn 注册获取 API Key"
    echo -e "  ${YELLOW}⚠${NC}  然后: export DEEPSEEK_API_KEY=sk-xxx"
else
    echo -e "  ${GREEN}✓${NC} config.yaml 已存在"
fi

# ── 5. 测试 ──────────────────────────────────────────────────
echo -e "\n${BOLD}[5/5]${NC} 单元测试..."
if [ "$SKIP_TEST" = "1" ]; then
    echo -e "  ${YELLOW}跳过 (--skip-test)${NC}"
else
    python3 -m pytest \
        tests/test_tool.py \
        tests/test_rate_limiter.py \
        tests/test_credential_pool.py \
        tests/test_redact.py \
        tests/test_think_scrubber.py \
        tests/test_error_classifier.py \
        tests/test_usage_pricing.py \
        tests/test_feishu_pure.py \
        tests/test_prompt_builder.py \
        tests/test_guardrails.py \
        tests/test_file_safety.py \
        tests/test_orch_classifier.py \
        tests/test_factcheck.py \
        tests/test_hallmetrics.py \
        -q --tb=line 2>&1
fi

# ── 6. WebUI (可选) ──────────────────────────────────────────
if [ "$START_WEBUI" = "1" ]; then
    echo -e "\n${BOLD}[6/6]${NC} 启动 WebUI (端口 $WEBUI_PORT)..."
    if [ -f "webui/app.py" ]; then
        nohup python3 webui/app.py --port "$WEBUI_PORT" > webui.log 2>&1 &
        WEBUI_PID=$!
        sleep 1
        if kill -0 "$WEBUI_PID" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} WebUI 已启动 (PID: $WEBUI_PID)"
            echo -e "  ${GREEN}✓${NC} 地址: ${CYAN}http://localhost:$WEBUI_PORT${NC}"
        else
            echo -e "  ${RED}✗${NC} WebUI 启动失败，查看 webui.log"
        fi
    else
        echo -e "  ${YELLOW}⚠${NC}  webui/app.py 不存在"
    fi
fi

# ── 完成 ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   🐉 Dragon Agent 部署完成！        ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${NC}"
echo -e "  ${BOLD}目录:${NC} ${CYAN}$INSTALL_DIR${NC}"
echo -e "  ${DIM}激活:${NC}   source $INSTALL_DIR/.venv/bin/activate"
echo -e "  ${DIM}测试:${NC}   python3 -m pytest tests/ -v"
echo -e "  ${DIM}启动:${NC}   python3 -m dragon gateway start"
echo -e "  ${DIM}WebUI:${NC}  python3 webui/app.py --port 5000"
if [ "$START_WEBUI" = "1" ] && [ -n "${WEBUI_PID:-}" ] && kill -0 "$WEBUI_PID" 2>/dev/null; then
    echo -e ""
    echo -e "  ${BOLD}🌐 WebUI 运行中:${NC} ${CYAN}http://localhost:$WEBUI_PORT${NC} (PID: $WEBUI_PID)"
fi
echo -e ""
echo -e "  ${BOLD}🔑 获取 API Key:${NC} ${CYAN}https://api.andlapi.cn${NC}"
echo -e "  ${DIM}注册后设置:${NC} export DEEPSEEK_API_KEY=sk-xxx"
