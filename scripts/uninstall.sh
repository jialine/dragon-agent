#!/usr/bin/env bash
# ================================================================
#  Dragon Agent — 卸载脚本
#  用法: bash uninstall.sh [--keep-data] [--dir PATH]
# ================================================================
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
BOLD='\033[1m'

INSTALL_DIR="${INSTALL_DIR:-$HOME/dragon-agent}"
KEEP_DATA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --keep-data|--keep-config) KEEP_DATA=1; shift ;;  # --keep-config still works (legacy)
        -h|--help)
            echo "用法: bash uninstall.sh [--keep-data] [--dir PATH]"
            echo ""
            echo "  --keep-data   保留所有数据资产（剧本、分镜头、数据库、媒体、配置）"
            echo "  --dir PATH    指定安装目录（默认 ~/dragon-agent）"
            echo ""
            echo "  --keep-data 保护的内容："
            echo "    • config.yaml / .env                配置文件"
            echo "    • webui/drama.db                    分镜头数据库"
            echo "    • ~/episodes/                       剧本"
            echo "    • ~/.hermes/media/                  生成的视频/图片"
            echo "    • ~/.dragon/                        技能和配置"
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

if [ ! -d "$INSTALL_DIR" ] && [ "$KEEP_DATA" != "1" ]; then
    echo -e "  ${YELLOW}⚠${NC}  目录不存在: $INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} 无需卸载"
    exit 0
fi

# ── 1. 停止 WebUI ──────────────────────────────────────────────
echo -e "\n${BOLD}[1/4]${NC} 停止 WebUI 进程..."
WEBUI_KILLED=0
while IFS= read -r pid; do
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null && WEBUI_KILLED=$((WEBUI_KILLED + 1))
    fi
done < <(pgrep -f "webui/app.py" 2>/dev/null || true)

if [ -f "$INSTALL_DIR/webui.pid" ] 2>/dev/null; then
    pid=$(cat "$INSTALL_DIR/webui.pid" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null && WEBUI_KILLED=$((WEBUI_KILLED + 1))
    fi
fi

if [ "$WEBUI_KILLED" -gt 0 ]; then
    echo -e "  ${GREEN}✓${NC} 已停止 $WEBUI_KILLED 个 WebUI 进程"
else
    echo -e "  没有运行中的 WebUI 进程"
fi

# ── 2. 备份数据（--keep-data 时）───────────────────────────────
BACKUP_DIR=""
if [ "$KEEP_DATA" = "1" ]; then
    echo -e "\n${BOLD}[2/4]${NC} 备份数据资产..."
    BACKUP_DIR=$(mktemp -d /tmp/dragon_keep_data_XXXXXX)

    # 配置文件
    for f in config.yaml .env config.example.yaml; do
        [ -f "$INSTALL_DIR/$f" ] && cp "$INSTALL_DIR/$f" "$BACKUP_DIR/" 2>/dev/null
    done

    # 数据库
    if [ -f "$INSTALL_DIR/webui/drama.db" ]; then
        mkdir -p "$BACKUP_DIR/webui"
        cp "$INSTALL_DIR/webui/drama.db" "$BACKUP_DIR/webui/" 2>/dev/null
    fi

    # 剧本
    if [ -d "$HOME/episodes" ]; then
        cp -r "$HOME/episodes" "$BACKUP_DIR/" 2>/dev/null
    fi

    # 生成的媒体
    if [ -d "$HOME/.hermes/media" ]; then
        mkdir -p "$BACKUP_DIR/.hermes"
        cp -r "$HOME/.hermes/media" "$BACKUP_DIR/.hermes/" 2>/dev/null
    fi

    # 技能/配置
    if [ -d "$HOME/.dragon" ]; then
        cp -r "$HOME/.dragon" "$BACKUP_DIR/.dragon" 2>/dev/null
    fi

    # 向量库
    for d in "$HOME/dragon_data" "$HOME/panda_data"; do
        if [ -d "$d" ]; then
            cp -r "$d" "$BACKUP_DIR/$(basename "$d")" 2>/dev/null
        fi
    done

    echo -e "  ${GREEN}✓${NC} 数据已备份到 $BACKUP_DIR"
    echo -e "  ${CYAN}已保护:${NC}"
    [ -f "$BACKUP_DIR/config.yaml" ]       && echo -e "    • config.yaml"
    [ -f "$BACKUP_DIR/webui/drama.db" ]    && echo -e "    • 分镜头数据库 (drama.db)"
    [ -d "$BACKUP_DIR/episodes" ]          && echo -e "    • 剧本 (~/episodes/)"
    [ -d "$BACKUP_DIR/.hermes/media" ]     && echo -e "    • 媒体文件 (~/.hermes/media/)"
    [ -d "$BACKUP_DIR/.dragon" ]           && echo -e "    • 技能/配置 (~/.dragon/)"
    [ -d "$BACKUP_DIR/dragon_data" ]       && echo -e "    • 向量库 (dragon_data/)"
    [ -d "$BACKUP_DIR/panda_data" ]        && echo -e "    • 历史数据 (panda_data/)"
fi

# ── 3. 删除安装目录 ────────────────────────────────────────────
echo -e "\n${BOLD}[3/4]${NC} 删除安装目录..."
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} 已删除 $INSTALL_DIR"
else
    echo -e "  ${YELLOW}⚠${NC}  $INSTALL_DIR 不存在"
fi

# ── 4. 恢复数据（--keep-data 时）───────────────────────────────
if [ "$KEEP_DATA" = "1" ] && [ -n "$BACKUP_DIR" ]; then
    echo -e "\n${BOLD}[4/4]${NC} 恢复数据资产..."

    # 配置
    mkdir -p "$INSTALL_DIR"
    for f in config.yaml .env config.example.yaml; do
        [ -f "$BACKUP_DIR/$f" ] && cp "$BACKUP_DIR/$f" "$INSTALL_DIR/" 2>/dev/null
    done

    # 数据库
    if [ -d "$BACKUP_DIR/webui" ]; then
        mkdir -p "$INSTALL_DIR/webui"
        cp -r "$BACKUP_DIR/webui/"* "$INSTALL_DIR/webui/" 2>/dev/null
    fi

    # 剧本
    if [ -d "$BACKUP_DIR/episodes" ]; then
        mkdir -p "$HOME/episodes"
        cp -r "$BACKUP_DIR/episodes/"* "$HOME/episodes/" 2>/dev/null
    fi

    # 媒体
    if [ -d "$BACKUP_DIR/.hermes/media" ]; then
        mkdir -p "$HOME/.hermes/media"
        cp -r "$BACKUP_DIR/.hermes/media/"* "$HOME/.hermes/media/" 2>/dev/null
    fi

    # 技能/配置
    if [ -d "$BACKUP_DIR/.dragon" ]; then
        mkdir -p "$HOME/.dragon"
        cp -r "$BACKUP_DIR/.dragon/"* "$HOME/.dragon/" 2>/dev/null
    fi

    # 向量库/遗留数据
    for d in dragon_data panda_data; do
        if [ -d "$BACKUP_DIR/$d" ]; then
            mkdir -p "$HOME/$d"
            cp -r "$BACKUP_DIR/$d/"* "$HOME/$d/" 2>/dev/null
        fi
    done

    # 清理备份
    rm -rf "$BACKUP_DIR"
    echo -e "  ${GREEN}✓${NC} 数据资产已恢复"
else
    # 没有 --keep-data：删除所有运行时数据
    echo -e "\n${BOLD}[4/4]${NC} 清理运行时数据..."
    for d in "$HOME/.dragon" "$HOME/dragon_data" "$HOME/panda_data"; do
        if [ -d "$d" ]; then
            rm -rf "$d"
            echo -e "  ${GREEN}✓${NC} 已删除 $d"
        fi
    done
fi

# ── 清理日志 ──────────────────────────────────────────────────
rm -f "$INSTALL_DIR/webui.log" nohup.out 2>/dev/null || true

# ── 完成 ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   🐉 Dragon Agent 已卸载            ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${NC}"

if [ "$KEEP_DATA" = "1" ]; then
    echo -e "  ${GREEN}✓${NC} 数据资产已保留"
else
    echo -e "  ${YELLOW}💡 提示: 使用 --keep-data 保留剧本、数据库、媒体${NC}"
fi
echo -e "  重新安装: curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash"
