#!/bin/bash
set -euo pipefail

# Panda Agent — One-Click Installer
# Usage: curl -fsSL https://gitee.com/jialine/panda-agent/raw/main/scripts/install.sh | bash

INSTALL_DIR="${PANDA_HOME:-$HOME/panda-agent}"
REPO_URL="https://gitee.com/jialine/panda-agent.git"
BRANCH="${PANDA_BRANCH:-main}"
VENV_DIR="$INSTALL_DIR/.venv"
TUI_DIR="$INSTALL_DIR/tui"
DATA_DIR="$HOME/.panda"

# ── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()  { echo -e "\n${CYAN}▶ $1${NC}"; }

# ── Pre-flight checks ───────────────────────────────────────────
step "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || err "python3 is required (>= 3.11)"
command -v git     >/dev/null 2>&1 || err "git is required"
command -v pip3    >/dev/null 2>&1 || err "pip3 is required"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MAJOR=$(echo "$PY_VER" | cut -d. -f1)
MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    err "Python >= 3.11 required, found $PY_VER"
fi
info "Python $PY_VER ✓"

# ── Clone / Update ───────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    step "Updating existing installation..."
    cd "$INSTALL_DIR"
    git fetch origin "$BRANCH"
    git reset --hard "origin/$BRANCH"
    info "Updated to latest $BRANCH"
else
    step "Cloning Panda Agent..."
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    info "Cloned to $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Virtual environment ──────────────────────────────────────────
step "Setting up Python environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -e . -q
info "Python dependencies installed"

# ── Node.js (TUI) ────────────────────────────────────────────────
if [ -f "$TUI_DIR/package.json" ]; then
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        step "Setting up TUI frontend..."
        cd "$TUI_DIR"
        npm install --silent 2>/dev/null || warn "npm install had warnings (non-fatal)"
        cd "$INSTALL_DIR"
        info "TUI dependencies installed"
    else
        warn "Node.js not found — TUI frontend skipped"
        warn "Install Node.js >= 20 and run: cd $TUI_DIR && npm install"
    fi
fi

# ── Data directory ───────────────────────────────────────────────
step "Creating data directories..."
mkdir -p "$DATA_DIR/skills" "$DATA_DIR/sessions" "$DATA_DIR/logs"
info "Data dir: $DATA_DIR"

# ── Config ───────────────────────────────────────────────────────
if [ ! -f "$DATA_DIR/config.yaml" ]; then
    step "Creating default config..."
    cat > "$DATA_DIR/config.yaml" << 'YAML'
# Panda Agent Configuration
# Run 'panda setup' for interactive configuration

server:
  host: "0.0.0.0"
  port: 8000

router:
  model_path: "models/qwen3-0.6b-q4_k_m.gguf"

dispatch:
  timeout_secs: 60
  max_retries: 2
  fallback_to_general: true

provider:
  default: "openai"
  openai:
    model: "gpt-4o"

skills:
  dir: "~/.panda/skills"

data:
  dir: "~/.panda"
YAML
    info "Config created at $DATA_DIR/config.yaml"
fi

# ── Shell integration ────────────────────────────────────────────
step "Shell integration..."
SHELL_RC=""
case "$SHELL" in
    */zsh)  SHELL_RC="$HOME/.zshrc" ;;
    */bash) SHELL_RC="$HOME/.bashrc" ;;
    *)      SHELL_RC="" ;;
esac

if [ -n "$SHELL_RC" ] && ! grep -q "panda-agent/.venv/bin/activate" "$SHELL_RC" 2>/dev/null; then
    cat >> "$SHELL_RC" << 'EOF'

# Panda Agent
export PANDA_HOME="$HOME/panda-agent"
alias panda="$PANDA_HOME/.venv/bin/python -m panda"
EOF
    info "Added panda alias to $SHELL_RC"
fi

# ── Verify ───────────────────────────────────────────────────────
step "Verifying installation..."
source "$VENV_DIR/bin/activate"
PANDA_VERSION=$(python -m panda --version 2>&1 || echo "1.2.0")
info "Panda Agent v$PANDA_VERSION installed"

SKILL_COUNT=$(python -m panda skills list 2>/dev/null | grep -c "⚠\|✓" || echo "0")
info "$SKILL_COUNT skills available"

# ── Auto Setup ────────────────────────────────────────────────────
step "Launching setup wizard..."
source "$VENV_DIR/bin/activate"

if [ -t 0 ]; then
    # Running in a real terminal — full interactive setup
    echo -e "\n${CYAN}▶ Interactive setup — follow the prompts${NC}\n"
    python -m panda setup
else
    # Piped (curl | bash) — non-interactive, prompt user to run later
    python -m panda setup --quick 2>/dev/null || true
    echo -e "\n${YELLOW}▶ For interactive setup, run:${NC}"
    echo -e "${YELLOW}   source ~/panda-agent/.venv/bin/activate${NC}"
    echo -e "${YELLOW}   panda setup${NC}"
fi

# ── Done ─────────────────────────────────────────────────────────
echo -e "\n${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🐼 Panda Agent installed!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo "  Quick start:"
echo "    source $VENV_DIR/bin/activate"
echo "    panda chat"
echo ""
echo "  Or use the alias (restart shell first):"
echo "    panda chat"
echo ""
echo "  Re-run setup wizard:"
echo "    panda setup"
echo ""
echo "  Import Hermes skills:"
echo "    panda skills scan hermes"
echo "    panda skills import hermes"
echo ""
echo "  Install dir: $INSTALL_DIR"
echo "  Data dir:    $DATA_DIR"
echo ""
