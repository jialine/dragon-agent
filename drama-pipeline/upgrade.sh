#!/usr/bin/env bash
# =============================================================================
# Dragon 短剧流水线 — 一键升级脚本
# =============================================================================
# 用法：
#   ./upgrade.sh                          # 升级到最新版（git pull 或本地覆盖）
#   ./upgrade.sh --from-git <repo_url>    # 从指定 git 仓库拉取
#   ./upgrade.sh --src /path/to/new/pkg   # 从本地新包目录覆盖
#   ./upgrade.sh --yes                    # 跳过确认
#   ./upgrade.sh --keep-data              # 备份并保留数据（默认已保留）
#
# 做什么：
#   1. 备份 config.yaml / .env / drama.db / assets（升级失败可回滚）
#   2. 拉取/覆盖最新代码（git pull 或从源目录 rsync）
#   3. 更新 Python 依赖
#   4. 重启 WebUI
#   5. 健康检查 + 版本确认
#
# ⚠️ 升级绝不覆盖用户的 config.yaml / .env / drama.db / assets
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR"          # upgrade.sh 就装在流水线根目录
FROM_GIT=""
SRC_DIR=""
ASSUME_YES=false
BACKUP_DIR="$INSTALL_DIR/.upgrade_backup_$(date +%Y%m%d_%H%M%S)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}▶ $1${NC}"; }

# ── 参数解析 ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-git) FROM_GIT="$2"; shift 2 ;;
        --src)      SRC_DIR="$2"; shift 2 ;;
        --yes|-y)   ASSUME_YES=true; shift ;;
        --help|-h)
            echo "用法: ./upgrade.sh [--from-git URL | --src DIR] [--yes]"
            exit 0 ;;
        *) err "未知参数: $1" ;;
    esac
done

# ── 确认 ────────────────────────────────────────────────────────────────────
if ! $ASSUME_YES; then
    echo -e "${CYAN}即将升级 Dragon 短剧流水线（保留 config/.env/db/assets）${NC}"
    read -rp "继续？[Y/n] " ans
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]] || { echo "已取消"; exit 0; }
fi

# ── 备份 ────────────────────────────────────────────────────────────────────
step "备份数据"
mkdir -p "$BACKUP_DIR"
for f in config.yaml .env; do
    [ -f "$INSTALL_DIR/$f" ] && cp "$INSTALL_DIR/$f" "$BACKUP_DIR/" && info "备份 $f"
done
[ -f "$INSTALL_DIR/webui/drama.db" ] && cp "$INSTALL_DIR/webui/drama.db" "$BACKUP_DIR/drama.db" && info "备份 webui/drama.db"
[ -d "$INSTALL_DIR/assets" ] && cp -r "$INSTALL_DIR/assets" "$BACKUP_DIR/assets" 2>/dev/null && info "备份 assets/"
info "备份目录: $BACKUP_DIR"

# ── 拉取/覆盖代码 ───────────────────────────────────────────────────────────
step "更新代码"

if [ -n "$FROM_GIT" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        cd "$INSTALL_DIR" && git pull --ff-only
    else
        warn "目录不是 git 仓库，改用 clone 覆盖（保留数据文件）"
        tmp_clone=$(mktemp -d)
        git clone "$FROM_GIT" "$tmp_clone"
        rsync -a --delete \
            --exclude='.venv' --exclude='config.yaml' --exclude='.env' \
            --exclude='drama.db' --exclude='assets' --exclude='.git' \
            --exclude='*.pyc' --exclude='__pycache__' \
            "$tmp_clone/" "$INSTALL_DIR/"
        rm -rf "$tmp_clone"
    fi
elif [ -n "$SRC_DIR" ]; then
    [ -d "$SRC_DIR" ] || err "源目录不存在: $SRC_DIR"
    rsync -a --delete \
        --exclude='.venv' --exclude='config.yaml' --exclude='.env' \
        --exclude='drama.db' --exclude='assets' --exclude='.git' \
        --exclude='*.pyc' --exclude='__pycache__' \
        "$SRC_DIR/" "$INSTALL_DIR/"
    info "已从 $SRC_DIR 覆盖更新"
elif [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR" && git pull --ff-only
    info "git pull 完成"
else
    warn "未提供 --from-git / --src，且非 git 仓库 —— 跳过代码更新"
    warn "请用 --src /path/to/new/package 指定新版本代码"
fi

# ── 更新依赖 ────────────────────────────────────────────────────────────────
step "更新依赖"
if [ -f "$INSTALL_DIR/.venv/bin/activate" ]; then
    source "$INSTALL_DIR/.venv/bin/activate"
    PIP="pip"
else
    PIP="pip3"
    export PIP_BREAK_SYSTEM_PACKAGES=1
fi
"$PIP" install -r "$INSTALL_DIR/requirements.txt" -q
info "依赖已更新"

# ── 重启 WebUI ───────────────────────────────────────────────────────────────
step "重启 WebUI"

# 杀掉旧进程
pkill -f "webui/app.py" 2>/dev/null && info "已停止旧 WebUI" || warn "无运行中的 WebUI"
sleep 1

# 启动新进程
cd "$INSTALL_DIR/webui"
if [ -f "$INSTALL_DIR/.venv/bin/python3" ]; then
    PYTHON="$INSTALL_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi
PORT="$(grep -E '^PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 5000)"
export PORT
export DRAGON_API_KEY="$(grep -E '^DRAGON_API_KEY=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'"'')"
nohup "$PYTHON" -B app.py > /tmp/dragon-drama-webui.log 2>&1 &
info "WebUI 已重启 (PID $!)"

# ── 健康检查 ────────────────────────────────────────────────────────────────
step "健康检查"
sleep 2
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qE "200|302"; then
    info "升级成功，WebUI 健康: http://127.0.0.1:$PORT/"
else
    warn "WebUI 未响应，回滚请恢复备份: $BACKUP_DIR"
    warn "日志: tail -f /tmp/dragon-drama-webui.log"
fi

# ── 完成 ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🎬 升级完成！${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo "  WebUI:    http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
echo "  备份:      $BACKUP_DIR"
echo ""
echo "  如需回滚:"
echo "    cp $BACKUP_DIR/config.yaml $INSTALL_DIR/"
echo "    cp $BACKUP_DIR/drama.db $INSTALL_DIR/webui/ 2>/dev/null"
echo ""
