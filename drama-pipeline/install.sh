#!/usr/bin/env bash
# =============================================================================
# Dragon 短剧流水线 — 一键开箱即用安装脚本（支持 curl | bash）
# =============================================================================
# 三种用法：
#   1) curl 一键（推荐，开箱即用）：
#        curl -fsSL https://gitee.com/jialine/dragon-agent/raw/main/drama-pipeline/install.sh | bash
#
#   2) 本地包安装（解压 tar 包后）：
#        ./install.sh
#
#   3) 指定参数：
#        ./install.sh --dir /opt/dragon-drama --port 5000 --api-key sk-xxx --yes
#        ./install.sh --repo git@gitee.com:xxx/dragon-agent.git --subdir drama-pipeline
#
# 做什么：
#   1. 预检环境（python3 / git / ffmpeg）
#   2. 部署代码（curl|bash 模式自动 git clone 后进入 drama-pipeline 子目录；
#                本地模式就地/rsync 复制）
#   3. 创建虚拟环境并安装依赖
#   4. 生成 config.yaml + .env（保留已有，不覆盖）
#   5. 创建数据目录结构
#   6. 后台启动 Drama Studio WebUI
#   7. 健康检查 + 输出使用指引
# =============================================================================

set -euo pipefail

# ── 默认配置 ────────────────────────────────────────────────────────────────
INSTALL_DIR="${DRAGON_HOME:-$HOME/dragon-agent}"   # 仓库根（curl 模式 clone 目标）
PIPELINE_DIR=""                                     # 产品包根（= 仓库根/drama-pipeline）
PORT="${PORT:-5000}"
API_KEY=""
ASSUME_YES=false
REPO_URL="${REPO_URL:-git@gitee.com:jialine/dragon-agent.git}"
DRAMA_SUBDIR="${DRAMA_SUBDIR:-drama-pipeline}"
BRANCH="${BRANCH:-main}"

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
        --port)     PORT="$2"; shift 2 ;;
        --api-key)  API_KEY="$2"; shift 2 ;;
        --repo)     REPO_URL="$2"; shift 2 ;;
        --subdir)   DRAMA_SUBDIR="$2"; shift 2 ;;
        --branch)   BRANCH="$2"; shift 2 ;;
        --yes|-y)   ASSUME_YES=true; shift ;;
        --help|-h)
            echo "用法:"
            echo "  curl -fsSL <raw_url> | bash                              # curl 一键"
            echo "  ./install.sh [--dir DIR] [--port PORT] [--api-key KEY] [--repo URL] [--yes]"
            exit 0 ;;
        *) err "未知参数: $1" ;;
    esac
done

# ── 检测代码来源 ────────────────────────────────────────────────────────────
# curl|bash 模式：脚本从 stdin 读取，本地没有 scripts/ 目录 → 走 git clone
# 本地包模式：脚本旁边有完整代码 → 就地/rsync 复制
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null || true)"
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/scripts" ] && [ -d "$SCRIPT_DIR/webui" ]; then
    SOURCE="local"
    PIPELINE_DIR="$SCRIPT_DIR"
else
    SOURCE="git"
fi

# ── 预检 ────────────────────────────────────────────────────────────────────
step "预检环境"

command -v python3 >/dev/null 2>&1 || err "需要 python3（>= 3.8）"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PY_VER"

if [ "$SOURCE" = "git" ] && ! command -v git >/dev/null 2>&1; then
    err "curl 模式需要 git（用于 clone 代码）。请先安装: apt install git"
fi
command -v git >/dev/null 2>&1 && info "git $(git --version | awk '{print $3}')" || warn "未检测到 git"

if command -v ffmpeg >/dev/null 2>&1; then
    info "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
else
    warn "未检测到 ffmpeg —— 视频合成/片头阶段需要，生成阶段不受影响"
    warn "  Ubuntu/Debian: sudo apt install -y ffmpeg"
fi

# ── 部署代码 ────────────────────────────────────────────────────────────────
step "部署代码"

if [ "$SOURCE" = "git" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "已存在仓库，git pull 更新..."
        cd "$INSTALL_DIR" && git pull --ff-only
    else
        info "从 $REPO_URL 克隆 ($BRANCH)..."
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
    # 产品包在仓库的子目录里
    if [ -d "$INSTALL_DIR/$DRAMA_SUBDIR" ]; then
        PIPELINE_DIR="$INSTALL_DIR/$DRAMA_SUBDIR"
    else
        err "仓库内未找到产品包子目录 $DRAMA_SUBDIR，请确认 --subdir 参数"
    fi
    info "产品包目录: $PIPELINE_DIR"
else
    if [ -d "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "$SCRIPT_DIR" ]; then
        if [ -d "$INSTALL_DIR/scripts" ]; then
            warn "目标目录已存在且含代码，仅同步更新代码文件（保留 config/.env/db）"
            rsync -a --delete \
                --exclude='.venv' --exclude='config.yaml' --exclude='.env' \
                --exclude='drama.db' --exclude='assets' --exclude='.git' \
                --exclude='*.pyc' --exclude='__pycache__' \
                "$SCRIPT_DIR/" "$INSTALL_DIR/"
            PIPELINE_DIR="$INSTALL_DIR"
        else
            err "目标目录 $INSTALL_DIR 已存在但不是流水线目录，请换 --dir"
        fi
    elif [ "$INSTALL_DIR" != "$SCRIPT_DIR" ]; then
        info "复制代码到 $INSTALL_DIR"
        mkdir -p "$INSTALL_DIR"
        rsync -a --exclude='.venv' --exclude='config.yaml' --exclude='.env' \
            --exclude='drama.db' --exclude='.git' --exclude='*.pyc' --exclude='__pycache__' \
            "$SCRIPT_DIR/" "$INSTALL_DIR/"
        PIPELINE_DIR="$INSTALL_DIR"
    else
        info "就地安装（当前目录）"
    fi
fi

cd "$PIPELINE_DIR"
info "安装目录: $PIPELINE_DIR"

# ── 虚拟环境 + 依赖 ─────────────────────────────────────────────────────────
step "安装依赖"

VENV_DIR="$PIPELINE_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    if python3 -m venv "$VENV_DIR" 2>/dev/null; then
        info "创建虚拟环境 .venv"
    else
        warn "venv 创建失败，回退到系统级 pip 安装"
        VENV_DIR=""
    fi
fi

if [ -n "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
    PIP="pip"
else
    PIP="pip3"
    export PIP_BREAK_SYSTEM_PACKAGES=1
fi

# pip 安装辅助：官方源优先，失败自动回退清华镜像（国内网络必需）
pip_install() {
    if "$PIP" install "$@" -q 2>/dev/null; then
        return 0
    fi
    warn "官方 PyPI 超时/失败，回退清华镜像..."
    "$PIP" install "$@" -q -i https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null \
        || err "依赖安装失败（官方源和清华镜像都失败），请检查网络"
}

"$PIP" install --upgrade pip -q 2>/dev/null || true
pip_install -r requirements.txt
info "核心依赖已安装"

if ! "$PIP" show edge-tts >/dev/null 2>&1; then
    warn "edge-tts 未安装（TTS 配音阶段需要，可稍后手动装：$PIP install edge-tts）"
fi

# ── 配置生成 ────────────────────────────────────────────────────────────────
step "配置"

if [ ! -f "$PIPELINE_DIR/config.yaml" ]; then
    cp "$PIPELINE_DIR/config.example.yaml" "$PIPELINE_DIR/config.yaml"
    info "生成 config.yaml（从模板）"
else
    info "config.yaml 已存在，保留"
fi

if [ ! -f "$PIPELINE_DIR/.env" ]; then
    cp "$PIPELINE_DIR/.env.example" "$PIPELINE_DIR/.env"
    info "生成 .env（从模板）"
else
    info ".env 已存在，保留"
fi

if [ -n "$API_KEY" ]; then
    if grep -q '^DRAGON_API_KEY=' "$PIPELINE_DIR/.env" 2>/dev/null; then
        sed -i "s|^DRAGON_API_KEY=.*|DRAGON_API_KEY=$API_KEY|" "$PIPELINE_DIR/.env"
    else
        echo "DRAGON_API_KEY=$API_KEY" >> "$PIPELINE_DIR/.env"
    fi
    info "已写入 API Key 到 .env"
else
    if grep -qE '^DRAGON_API_KEY=sk-你的|^DRAGON_API_KEY=$' "$PIPELINE_DIR/.env" 2>/dev/null; then
        warn "⚠️  请编辑 $PIPELINE_DIR/.env 填入 DRAGON_API_KEY（获取: https://api.andlapi.cn）"
    fi
fi

# ── 数据目录 ────────────────────────────────────────────────────────────────
step "数据目录"
mkdir -p "$PIPELINE_DIR"/{assets/videos,assets/characters,output,dragon_data}
info "数据目录就绪"

# ── 启动 WebUI ───────────────────────────────────────────────────────────────
step "启动 Drama Studio WebUI"

if pgrep -f "webui/app.py" >/dev/null 2>&1; then
    warn "WebUI 已在运行，跳过启动"
else
    cd "$PIPELINE_DIR/webui"
    if [ -n "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python3" ]; then
        PYTHON="$VENV_DIR/bin/python3"
    else
        PYTHON="python3"
    fi
    export PORT="$PORT"
    export DRAGON_API_KEY="$(grep -E '^DRAGON_API_KEY=' "$PIPELINE_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"'')"
    nohup "$PYTHON" -B app.py > /tmp/dragon-drama-webui.log 2>&1 &
    WEBUI_PID=$!
    info "WebUI 已启动 (PID $WEBUI_PID)，日志 /tmp/dragon-drama-webui.log"
fi

# ── 健康检查 ────────────────────────────────────────────────────────────────
step "健康检查"
sleep 2
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qE "200|302"; then
    info "WebUI 健康检查通过: http://127.0.0.1:$PORT/"
else
    warn "WebUI 未响应，请查看日志: tail -f /tmp/dragon-drama-webui.log"
fi

# ── 完成 ────────────────────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🎬 Dragon 短剧流水线 安装完成！${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo "  WebUI:    http://$IP:$PORT/"
echo "  安装目录:  $PIPELINE_DIR"
echo "  日志:      tail -f /tmp/dragon-drama-webui.log"
echo ""
echo "  流水线脚本:"
echo "    cd $PIPELINE_DIR/scripts"
echo "    python3 gen_video.py \"提示词\" --model happyhorse-1.1-r2v"
echo ""
echo "  升级:     cd $PIPELINE_DIR && ./upgrade.sh"
echo "  卸载:     cd $PIPELINE_DIR && ./uninstall.sh"
echo ""
echo "  ⚠️ 下一步：编辑 $PIPELINE_DIR/.env 填入 DRAGON_API_KEY"
echo ""
