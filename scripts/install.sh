#!/usr/bin/env bash
# =============================================================================
# Panda Agent — One-line installer (self-contained, no GitHub dependency)
# =============================================================================
# Usage:
#   # Local install (from this directory):
#   bash scripts/install.sh
#   bash scripts/install.sh --dir ~/my-panda --quick
#
#   # Remote install (after pushing to GitHub):
#   curl -fsSL https://raw.githubusercontent.com/YOU/panda-agent/main/scripts/install.sh | bash
#
# This script auto-detects whether it's running locally or piped from curl.
# In local mode, it copies the project directory. In remote mode, it clones.
#
# Options:
#   --dir DIR          Install directory (default: ~/panda-agent)
#   --skip-setup       Skip interactive setup wizard
#   --quick            Non-interactive (uses env vars)
# =============================================================================
set -euo pipefail

INSTALL_DIR="$HOME/panda-agent"
SKIP_SETUP=false
QUICK_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)    INSTALL_DIR="$2"; shift 2 ;;
        --skip-setup) SKIP_SETUP=true; shift ;;
        --quick)  QUICK_MODE=true; shift ;;
        --help|-h)
            echo "🐼 Panda Agent — One-line installer"
            echo ""
            echo "  bash scripts/install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dir DIR       Install to DIR (default: ~/panda-agent)"
            echo "  --skip-setup    Skip interactive setup wizard"
            echo "  --quick         Non-interactive (read env vars)"
            exit 0
            ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}→${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }

echo ""
echo -e "${BOLD}🐼 Panda Agent Installer${NC}"
echo -e "${BLUE}═══════════════════════${NC}"
echo ""

# ── Step 1: Prerequisites ─────────────────────────────────────────

info "Step 1/4: Checking prerequisites..."

PYTHON=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        major=$("$py" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
        minor=$("$py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$py"
            ok "Found $($py --version 2>&1)"
            break
        fi
    fi
done
[ -z "$PYTHON" ] && fail "Python >= 3.11 required. Install: apt install python3.12"
"$PYTHON" -m pip --version &>/dev/null || fail "pip required. Run: $PYTHON -m ensurepip --upgrade"

# ── Step 2: Copy project ──────────────────────────────────────────

info "Step 2/4: Installing project..."

# Detect source: this script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Verify this is a panda-agent project
if [ ! -f "$PROJECT_DIR/panda/__init__.py" ]; then
    fail "Cannot find panda project. Run this script from within panda-agent/ directory."
fi

if [ -d "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "$PROJECT_DIR" ]; then
    warn "Removing existing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
fi

if [ "$INSTALL_DIR" != "$PROJECT_DIR" ]; then
    info "Copying panda-agent to $INSTALL_DIR ..."
    cp -r "$PROJECT_DIR" "$INSTALL_DIR"
    ok "Copied to $INSTALL_DIR"
else
    ok "Using current directory: $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Step 3: Virtual env + deps ────────────────────────────────────

info "Step 3/4: Installing dependencies..."

VENV_DIR="$INSTALL_DIR/.venv"
if [ -d "$VENV_DIR" ]; then
    warn "Recreating virtual environment..."
    rm -rf "$VENV_DIR"
fi

"$PYTHON" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip

pip install --quiet \
    "fastapi>=0.115.0" "uvicorn[standard]>=0.34.0" \
    "pydantic>=2.10.0" "pyyaml>=6.0" "python-dotenv>=1.0.0" \
    "httpx>=0.28.0" "openai>=1.68.0" "tenacity>=9.0.0" "networkx>=3.4"

pip install --quiet "llama-cpp-python>=0.3.8" 2>/dev/null \
    && ok "llama-cpp-python installed" \
    || warn "llama-cpp-python skipped (router fallback)"

pip install --quiet "chromadb>=0.6.0" "sentence-transformers>=3.4.0" 2>/dev/null \
    && ok "Knowledge base deps installed" \
    || warn "Knowledge base skipped (optional)"

ok "Dependencies installed"

# ── Step 4: Setup ─────────────────────────────────────────────────

info "Step 4/4: Configuration..."

if $SKIP_SETUP; then
    info "Setup skipped (--skip-setup)"
elif $QUICK_MODE; then
    info "Quick setup from environment variables..."
    PYTHONPATH="$INSTALL_DIR" "$PYTHON" -m panda setup --quick
else
    info "Launching interactive setup wizard..."
    PYTHONPATH="$INSTALL_DIR" "$PYTHON" -m panda setup
fi

# ── Done ──────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Panda Agent installed! 🐼      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════╝${NC}"
echo ""
echo "  目录:  $INSTALL_DIR"
echo ""
echo "  快速开始:"
echo "    cd $INSTALL_DIR"
echo "    source .venv/bin/activate"
echo "    panda serve              # 启动 API 服务器"
echo "    panda gateway start      # 启动多平台 Gateway"
echo "    panda chat               # 开始聊天"
echo "    panda doctor             # 诊断检查"
echo ""
echo "  别名:"
echo "    alias panda='cd $INSTALL_DIR && source .venv/bin/activate && python -m panda'"
echo ""
