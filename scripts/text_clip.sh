#!/bin/bash
# 生成黑底文字片段
# 用法: text_clip.sh "文字" 时长秒 输出文件 [字号] [颜色]
TEXT="$1"
DURATION="${2:-3}"
OUTPUT="$3"
FONTSIZE="${4:-48}"
COLOR="${5:-white}"
FONT="/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"

ffmpeg -y -f lavfi -i "color=c=black:s=1920x1080:d=${DURATION}:r=24" \
  -vf "drawtext=fontfile=${FONT}:text='${TEXT}':fontcolor=${COLOR}:fontsize=${FONTSIZE}:x=(w-text_w)/2:y=(h-text_h)/2" \
  -c:v libx264 -pix_fmt yuv420p "$OUTPUT" 2>/dev/null
echo "$OUTPUT"
