#!/bin/bash
# 10/12 집중 탐색: 3일 시험 1등 설정 주변만 판다. 속도 3732(기본).
#   1등: VDD2 1.100 + VDDQ 0.725 + PU-CAL 0 + DQ VREF 0x06 + PDDS 1 + adj test-6  (132/58/30, 평균 73)
#   그 1등이 나온 캠페인(sdram_20261009_001016_v2_1100)을 이어서 돌린다 → 이미 돌린 이웃은 건너뜀.
#   DQ VREF 목록에 0x02, 0x04, 0x08, 0x0a 추가 (0x06 아래는 소스 주석 % 표에 없는 값, 의미 미확인).
#   탐색 knob: dsvref, adj, pdds, tsense, caodt, vddq  (VDD2 1.100, PU-CAL 0 은 고정. VDDQ 0.700 은 제외)
#
#   cd ~/gbt-local && bash sdram/run_focus.sh                 (기본: 48시간 뒤까지)
#   UNTIL='2026-10-14 09:00' bash sdram/run_focus.sh          (끝 시각 지정)
#
# 멈추기: touch logs/STOP_ALL  (지금 후보까지 하고 멈춤, 전원 OFF)
# 200번 통과하면 그 자리에서 멈춘다. 끝나면 결과를 ~/gbt/logs 로 복사 (공유 폴더가 살아 있을 때만).

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
CAMP=logs/sdram_20261009_001016_v2_1100
STOP=logs/STOP_ALL
UNTIL=${UNTIL:-$(date -d '+48 hours' '+%Y-%m-%d %H:%M')}
trap 'echo "### run_focus: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

[ -f "$CAMP/state.json" ] || { echo "### run_focus: $CAMP 없음 (~/gbt-local/logs 확인)"; exit 1; }
rm -f "$CAMP/stop" "$STOP"
echo "### run_focus: $CAMP 이어서, 끝 시각 $UNTIL"
( while sleep 20; do [ -e "$STOP" ] && touch "$CAMP/stop" && break; done ) &
w=$!
"$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 \
    --resume "$CAMP" --explore dsvref,adj,pdds,tsense,caodt,vddq --skip vddq=700 \
    --repeat 3 --until "$UNTIL"
rc=$?
kill $w 2>/dev/null
[ $rc -eq 0 ] && echo "### run_focus: ★ 200번 통과"
if mountpoint -q ~/gbt 2>/dev/null; then
    timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_focus: 결과를 ~/gbt/logs 로 복사함"
fi
echo "### run_focus: 끝 (rc=$rc)"
