#!/bin/bash
# S07 批量拍摄 — 直接用 gen_video.py
# T2V: happyhorse-1.1-t2v (8镜)
# R2V: 需要参考图URL，单独处理

set -e
cd /home/jialine/dragon-agent
OUT="ember_s1/videos/S07"
mkdir -p "$OUT"

# 所有11镜的参数 (ID, MODEL, DURATION, PROMPT)
# T2V shots first (8 shots)
echo "🎬 S07 T2V Shots (8 shots)..."
python3 scripts/gen_video.py "纯黑背景，猩红色火焰爆燃凝结成燃烧中文大字猩火，火星飞溅，英文字幕EMBER浮现" --model happyhorse-1.1-t2v --size 1920*1080 --duration 5 --output "$OUT/S1-E07-00.mp4"
echo "[1/8] S1-E07-00 ✓"

python3 scripts/gen_video.py "六名海豹突击队员沿废弃维修隧道推进，夜视仪下绿色世界，红外七个热源排列整齐，队长举拳停止信号，紧张战术氛围" --model happyhorse-1.1-t2v --size 1920*1080 --duration 8 --output "$OUT/S1-E07-01.mp4"
echo "[2/8] S1-E07-01 ✓"

python3 scripts/gen_video.py "夜视仪POV沿昏暗隧道推进，红外显示七个猩猩热源纹丝不动如雕塑，队员吞咽声紧张，切换热成像模式体温无波动" --model happyhorse-1.1-t2v --size 1920*1080 --duration 12 --output "$OUT/S1-E07-02.mp4"
echo "[3/8] S1-E07-02 ✓"

python3 scripts/gen_video.py "科尔号驱逐舰滑入废弃布鲁克林船厂干船坞，船体碰撞码头，数百只实验白鼠组成灰毯从各角落涌向舰艇，沿锚链攀爬" --model happyhorse-1.1-t2v --size 1920*1080 --duration 12 --output "$OUT/S1-E07-05.mp4"
echo "[4/8] S1-E07-05 ✓"

python3 scripts/gen_video.py "科尔号甲板俯拍，数百只白鼠从舷侧涌上如灰色洪水，覆盖舰炮炮塔，沿雷达天线攀爬，月光下驱逐舰被灰色蠕动覆盖" --model happyhorse-1.1-t2v --size 1920*1080 --duration 13 --output "$OUT/S1-E07-06.mp4"
echo "[5/8] S1-E07-06 ✓"

python3 scripts/gen_video.py "科尔号船员舱，127名水手睡梦中，白鼠从通风口涌入覆盖船员身体，啮齿动物吱吱声合成警告，恐慌幽闭恐惧" --model happyhorse-1.1-t2v --size 1920*1080 --duration 10 --output "$OUT/S1-E07-07.mp4"
echo "[6/8] S1-E07-07 ✓"

python3 scripts/gen_video.py "船员舱内水手长从枕下拔手枪指向身上白鼠，鼠群全部静止数百只同时转头红光锁定，吱吱声合成警告，手枪掉落" --model happyhorse-1.1-t2v --size 1920*1080 --duration 13 --output "$OUT/S1-E07-08.mp4"
echo "[7/8] S1-E07-08 ✓"

python3 scripts/gen_video.py "科尔号舰桥屏幕所有系统逐一亮起武器在线火控在线，画面切方舟-0隔离舱银背睁开眼金色瞳孔，抬前掌五指缓缓合拢" --model happyhorse-1.1-t2v --size 1920*1080 --duration 12 --output "$OUT/S1-E07-09.mp4"
echo "[8/8] S1-E07-09 ✓"

echo "✅ All 8 T2V shots done!"
echo "Output: $OUT/"
ls -la "$OUT/"
