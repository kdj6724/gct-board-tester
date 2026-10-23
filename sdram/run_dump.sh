#!/bin/bash
# 10/12 트레이닝 결과 dump (Dean 요청). **원본 SBL 소스 그대로** + dump 출력만 추가해서 kern reboot 반복.
#   - 바꾸는 파일: base/drivers/orbit_ddram/test_lpddr4.c 하나 (sdram/dump/trn_dump_*.patch)
#       · ddr_main() 바로 위에 trn_dump() 함수 추가
#       · ddr_main() 마지막 'return ret;' 직전에 ret == 0 이면 trn_dump(&omc_cfg, 0), 2CH 면 trn_dump(&omc_cfg_1, 1)
#     → DDR 초기화(모든 DFS 레벨 트레이닝) 끝난 뒤, DM> 보다 훨씬 앞(SBL 단계)에 'TRN ch.. ' 줄로 출력
#   - 빌드 인자 없음 (DDRRATE 3732, VDD2 1.150, VDDQ 0.700 등 전부 원본), 다른 DDR 설정도 안 바꿈
#   - 같은 것을 3번 (trn_dump_a/b/c). 후보마다 전체 콘솔 로그 저장 (--full-log) → 매 부팅 dump 가 남음
#
#   cd ~/gbt-local && bash sdram/run_dump.sh
# 멈추기: touch logs/STOP_ALL 또는 Ctrl-C (원본 복원 + 전원 OFF)

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
STOP=logs/STOP_ALL
trap 'echo "### run_dump: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

if pgrep -f "sdram/run_(ch|train|arm|focus).sh|cli/sdram_campaign.py" >/dev/null; then
    echo "### run_dump: 다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행 (Ctrl-C 또는 touch logs/STOP_ALL)"
    exit 1
fi
# 원본 확인: SBL 4개 파일에 git 상 수정이 없어야 함
if ! git -C "$SBL" diff --quiet -- "${FILES[@]}"; then
    echo "### run_dump: SBL 소스가 원본이 아님 (아래 파일 수정됨) → 확인 후 다시 실행"
    git -C "$SBL" diff --stat -- "${FILES[@]}"
    exit 1
fi
rm -f "$STOP"
echo "### run_dump: SBL 원본 확인 OK. 트레이닝 dump 시작"
( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
w=$!
"$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 \
    --variants sdram/dump --files "${FILES[@]}" --no-auto --full-log --no-cp-retry
rc=$?
kill $w 2>/dev/null
d=$(ls -dt logs/sdram_*/ | head -1)
echo "### run_dump: 결과 폴더 $d  (TRN 줄 수: $(cat "$d"/trn_dump_*/*.log 2>/dev/null | grep -c 'TRN ch'))"
if mountpoint -q ~/gbt 2>/dev/null; then
    timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_dump: 결과를 ~/gbt/logs 로 복사함"
fi
echo "### run_dump: 끝 (rc=$rc)"
