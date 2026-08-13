#!/usr/bin/env bash
# =============================================================================
# Dragon 短剧流水线 — 卸载脚本
# =============================================================================
# 用法：./uninstall.sh [--keep-data] [--yes]
#   --keep-data  保留 config.yaml/.env/drama.db/assets
#   --yes        跳过确认
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEEP_DATA=false
ASSUME_YES=false

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data) KEEP_DATA=true; shift ;;
        --yes|-y)    ASSUME_YES=true; shift ;;
        *) shift ;;
    esac
done

if ! $ASSUME_YES; then
    echo -e "${CYAN}即将卸载 Dragon 短剧流水线: $SCRIPT_DIR${NC}"
    if $KEEP_DATA; then echo "  （保留 config/.env/db/assets）"; else echo "  （删除全部）"; fi
    read -rp "确认卸载？[y/N] " ans
    [[ "$ans" =~ ^[Yy] ]] || { echo "已取消"; exit 0; }
fi

# 停止 WebUI
pkill -f "webui/app.py" 2>/dev/null && info "已停止 WebUI" || warn "无运行中的 WebUI"

if $KEEP_DATA; then
    # 只删代码，保留数据
    for d in scripts workflows webui/pipelines; do
        rm -rf "$SCRIPT_DIR/$d" 2>/dev/null || true
    done
    rm -rf "$SCRIPT_DIR/.venv" 2>/dev/null || true
    info "代码已删除，数据已保留"
    info "保留: config.yaml .env webui/drama.db assets/"
else
    rm -rf "$SCRIPT_DIR"
    info "已完全卸载"
fi

echo -e "${GREEN}卸载完成${NC}"
