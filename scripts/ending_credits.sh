#!/bin/bash
# 片尾字卡叠加脚本 — 可复用
# 用法: ./ending_credits.sh 输入视频 [输出视频] [片名] [小字] [时长秒]
# 默认: 最后 5 秒，「猩族纪元」+「未完待续」

INPUT="${1:?用法: $0 <输入视频> [输出] [片名] [小字] [秒数]}"
OUTPUT="${2:-${INPUT%.*}_credits.mp4}"
TITLE="${3:-猩 族 纪 元}"
SUBTITLE="${4:-未 完 待 续}"
DURATION="${5:-5}"
FONT_TITLE="/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
FONT_SUB="/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"

TAIL="${INPUT%.*}_tail.mp4"
ffmpeg -y -sseof -"${DURATION}" -i "$INPUT" -c copy "$TAIL" 2>/dev/null

ffmpeg -y -i "$TAIL" \
  -vf "fade=t=out:st=3:d=2, \
       drawtext=fontfile=${FONT_TITLE}: \
         text='${TITLE}': \
         fontcolor=white:fontsize=64: \
         x=(w-text_w)/2:y=(h-text_h)/2-70: \
         enable='between(t,0.5,${DURATION})', \
       drawtext=fontfile=${FONT_SUB}: \
         text='${SUBTITLE}': \
         fontcolor=#aaaaaa:fontsize=40: \
         x=(w-text_w)/2:y=(h-text_h)/2+40: \
         enable='between(t,1.5,${DURATION})'" \
  -c:a copy "$OUTPUT" 2>/dev/null

rm -f "$TAIL"
echo "✅ $OUTPUT"
