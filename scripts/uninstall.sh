#!/usr/bin/env bash
# ================================================================
#  Dragon Agent — 卸载脚本
#  用法: bash uninstall.sh [--keep-config]
# ================================================================
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
BOLD='\033[1m'

INSTALL_DIR="${INSTALL_DIR:-$HOME/dragon-agent}"
KEEP_CONFIG=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --keep-config) KEEP_CONFIG=1; shift ;;
        -h|--help)
            echo "用法: bash uninstall.sh [--keep-config] [--dir PATH]"
            echo ""
            echo "  --keep-config   保留 config.yaml 和 .env，删除其余文件"
            echo "  --dir PATH      指定安装目录（默认 ~/dragon-agent）"
            exit 0
            ;;
        *) echo -e "${RED}未知参数: $1${NC}"; exit 1 ;;
    esac
done

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║     🐉 Dragon Agent — 卸载         ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "  ${YELLOW}⚠${NC}  目录不存在: $INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} 无需卸载"
    exit 0
fi

# ── 1. 停止 WebUI ──────────────────────────────────────────────
echo -e "\n${BOLD}[1/3]${NC} 停止 WebUI 进程..."
WEBUI_KILLED=0
# 查找 webui/app.py 进程
while IFS= read -r pid; do
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null && WEBUI_KILLED=$((WEBUI_KILLED + 1))
    fi
done < <(pgrep -f "webui/app.py" 2>/dev/null || true)

# 也检查 nohup 启动的后台进程
if [ -f "$INSTALL_DIR/webui.pid" ]; then
    pid=$(cat "$INSTALL_DIR/webui.pid" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null && WEBUI_KILLED=$((WEBUI_KILLED + 1))
    fi
fi

if [ "$WEBUI_KILLED" -gt 0 ]; then
    echo -e "  ${GREEN}✓${NC} 已停止 $WEBUI_KILLED 个 WebUI 进程"
else
    echo -e "  没有运行中的 WebUI 进程"
fi

# ── 2. 清理文件 ────────────────────────────────────────────────
echo -e "\n${BOLD}[2/3]${NC} 清理文件..."

if [ "$KEEP_CONFIG" = "1" ]; then
    # 只保留配置文件
    echo -e "  ${YELLOW}  保留配置文件 (--keep-config)${NC}"
    for f in config.yaml .env config.example.yaml; do
        if [ -f "$INSTALL_DIR/$f" ]; then
            cp "$INSTALL_DIR/$f" "/tmp/dragon_${f}.bak" 2>/dev/null || true
        fi
    done
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    for f in config.yaml .env config.example.yaml; do
        if [ -f "/tmp/dragon_${f}.bak" ]; then
            mv "/tmp/dragon_${f}.bak" "$INSTALL_DIR/$f"
        fi
    done
    echo -e "  ${GREEN}✓${NC} 配置已保留在 $INSTALL_DIR/"
else
    rm -rf "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} 已删除 $INSTALL_DIR"
fi

# ── 3. 清理日志 ────────────────────────────────────────────────
echo -e "\n${BOLD}[3/3]${NC} 清理日志..."
rm -f "$INSTALL_DIR/webui.log" nohup.out 2>/dev/null || true
echo -e "  ${GREEN}✓${NC} 完成"

# ── 完成 ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   🐉 Dragon Agent 已卸载            ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${NC}"

if [ "$KEEP_CONFIG" = "1" ]; then
    echo -e "  ${CYAN}配置文件已保留${NC}"
fi
echo -e "  重新安装: curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash"
