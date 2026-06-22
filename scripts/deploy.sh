#!/usr/bin/env bash
# =============================================================================
# Dragon Agent — one-click deployment script
# =============================================================================
# Usage:
#   bash scripts/deploy.sh           # interactive deploy
#   bash scripts/deploy.sh --quick   # non-interactive, skip confirmations
#   bash scripts/deploy.sh --test    # deploy then run tests
#
# What this does:
#   1. Check Python >= 3.11
#   2. Create/activate virtual environment
#   3. Install all dependencies
#   4. Generate config.yaml if missing
#   5. Download router model (Qwen3-0.6B GGUF) if missing
#   6. Start the API server
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/.venv"
CONFIG_FILE="$PROJECT_ROOT/config.yaml"
MODEL_DIR="$PROJECT_ROOT/models"
ROUTER_MODEL="$MODEL_DIR/qwen3-0.6b-q4_k_m.gguf"
ROUTER_MODEL_URL="https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/qwen3-0.6b-q4_k_m.gguf"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

QUICK_MODE=false
RUN_TESTS=false

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK_MODE=true ;;
        --test)  RUN_TESTS=true ;;
        --help|-h)
            echo "Usage: bash scripts/deploy.sh [--quick] [--test]"
            echo "  --quick   Skip confirmations"
            echo "  --test    Run tests after deployment"
            exit 0
            ;;
    esac
done

section() { echo -e "\n${BLUE}═══ $1 ═══${NC}"; }
ok()     { echo -e "${GREEN}✓${NC} $1"; }
warn()   { echo -e "${YELLOW}⚠${NC} $1"; }
fail()   { echo -e "${RED}✗${NC} $1"; exit 1; }

confirm() {
    if $QUICK_MODE; then return 0; fi
    read -rp "$1 [Y/n] " ans
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

# ── Step 1: Preflight ──────────────────────────────────────────────────────

section "Step 1/6: Checking environment"

PYTHON=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        version=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$("$py" -c 'import sys; print(sys.version_info.major)')
        minor=$("$py" -c 'import sys; print(sys.version_info.minor)')
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$py"
            ok "Found $PYTHON ($version)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python >= 3.11 required. Install with: apt install python3.12"
fi

if ! command -v pip &>/dev/null && ! "$PYTHON" -m pip --version &>/dev/null; then
    fail "pip not found. Install with: $PYTHON -m ensurepip"
fi

# ── Step 2: Virtual Environment ────────────────────────────────────────────

section "Step 2/6: Virtual environment"

if [ -d "$VENV_DIR" ]; then
    warn "Virtual env already exists at $VENV_DIR"
    if confirm "Recreate virtual environment?"; then
        rm -rf "$VENV_DIR"
        ok "Removed old venv"
    else
        ok "Using existing venv"
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created virtual environment at $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
ok "Activated virtual environment"

# ── Step 3: Install Dependencies ───────────────────────────────────────────

section "Step 3/6: Installing dependencies"

echo "Upgrading pip..."
pip install --quiet --upgrade pip

echo "Installing core dependencies..."
pip install --quiet \
    "fastapi>=0.115.12" \
    "uvicorn[standard]>=0.34.2" \
    "pydantic>=2.12.5" \
    "pyyaml>=6.0.2" \
    "python-dotenv>=1.2.1" \
    "httpx>=0.28.1" \
    "openai>=2.21.0" \
    "tenacity>=9.1.4" \
    "networkx>=3.2"

echo "Installing router dependency (llama-cpp-python)..."
pip install --quiet "llama-cpp-python>=0.3.8"

ok "Core dependencies installed"

# ChromaDB and sentence-transformers are heavy — offer to skip
if confirm "Install knowledge base dependencies? (ChromaDB + sentence-transformers, ~500MB)"; then
    echo "Installing knowledge base dependencies..."
    pip install --quiet "chromadb>=0.5.0" "sentence-transformers>=3.3.0"
    ok "Knowledge base dependencies installed"
else
    warn "Skipped — knowledge base features disabled"
fi

# ── Step 4: Configuration ──────────────────────────────────────────────────

section "Step 4/6: Configuration"

if [ -f "$CONFIG_FILE" ]; then
    warn "config.yaml already exists"
    if confirm "Overwrite with default config?"; then
        generate_config=true
    else
        generate_config=false
        ok "Keeping existing config.yaml"
    fi
else
    generate_config=true
fi

if $generate_config; then
    # Auto-detect AgileMind Engine API
    DEFAULT_PROVIDER="deepseek"
    DEFAULT_MODEL="deepseek-chat"
    if [ -n "${AGILEMIND_API_KEY:-}" ]; then
        DEFAULT_PROVIDER="agilemind"
        DEFAULT_MODEL="qwen3.5-122b-a10b"
        echo -e "${GREEN}🐉${NC} AgileMind API Key detected — set as default"
    else
        echo -e "${YELLOW}⚠${NC} AgileMind API Key not set — defaulting to DeepSeek cloud"
    fi
    
    cat > "$CONFIG_FILE" << YAMLEOF
# Dragon Agent Configuration
# See dragon/config.py for all options

router:
  model_path: "models/qwen3-0.6b-q4_k_m.gguf"
  n_threads: 4
  n_ctx: 512
  temperature: 0.1
  max_tokens: 128

server:
  host: "0.0.0.0"
  port: 8000
  log_level: "info"

memory:
  persist_dir: "dragon_data/vectordb"
  embedding_model: "BAAI/bge-small-zh-v1.5"
  search_top_k: 5
  search_threshold: 0.5

guard:
  max_consecutive_repeats: 3
  max_loop_rounds: 2
  max_ineffective_retries: 3
  window_size: 50
  task_timeout_secs: 300

provider:
  default: "$DEFAULT_PROVIDER"
  $DEFAULT_PROVIDER:
    model: "$DEFAULT_MODEL"

dispatch:
  industries: {}

backup:
  endpoint: ""
  bucket: "dragon-backups"
  interval_hours: 6
YAMLEOF
    ok "Created config.yaml"
fi

# ── Step 5: Model Download ─────────────────────────────────────────────────

section "Step 5/6: Router model"

if [ -f "$ROUTER_MODEL" ]; then
    ok "Router model found: $ROUTER_MODEL"
else
    warn "Router model not found"
    if confirm "Download Qwen3-0.6B Q4_K_M GGUF (~400MB)?"; then
        mkdir -p "$MODEL_DIR"
        echo "Downloading from HuggingFace..."
        if command -v wget &>/dev/null; then
            wget -q --show-progress -O "$ROUTER_MODEL" "$ROUTER_MODEL_URL"
        elif command -v curl &>/dev/null; then
            curl -#L -o "$ROUTER_MODEL" "$ROUTER_MODEL_URL"
        else
            fail "Need wget or curl to download model"
        fi
        ok "Downloaded router model to $ROUTER_MODEL"
    else
        warn "Skipped — router will use fallback classification"
    fi
fi

# ── Step 6: Launch ─────────────────────────────────────────────────────────

section "Step 6/6: Starting server"

if $RUN_TESTS; then
    echo "Installing test dependencies..."
    pip install --quiet "pytest>=9.0.2" "pytest-asyncio>=1.3.0"

    echo "Running tests..."
    cd "$PROJECT_ROOT"
    PYTHONPATH="$PROJECT_ROOT" python -m pytest tests/ -v --tb=short
    ok "Tests passed"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       Dragon Agent deployment complete!       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Start server:"
echo "    cd $PROJECT_ROOT"
echo "    source .venv/bin/activate"
echo "    python -m uvicorn dragon.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "  API endpoints:"
echo "    GET  http://localhost:8000/health"
echo "    POST http://localhost:8000/v1/chat"
echo "    GET  http://localhost:8000/v1/consult/assess?q=..."
echo "    POST http://localhost:8000/v1/consult"
echo ""
echo "  Docs: http://localhost:8000/docs"
echo ""

if confirm "Start server now?"; then
    cd "$PROJECT_ROOT"
    python -m uvicorn dragon.main:app --host 0.0.0.0 --port 8000 --log-level info
fi
