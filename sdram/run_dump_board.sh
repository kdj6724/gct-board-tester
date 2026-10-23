#!/bin/bash
# 10/12 보드 바꿔가며 트레이닝 결과 dump. 원본 SBL + dump 출력만 (sdram/dump/trn_dump_a.patch).
#   빌드 인자 없음 (3732, VDD2 1.150, VDDQ 0.700 등 전부 원본), 다른 DDR 설정 안 바꿈.
#   kern reboot N 번 (기본 10) 하면서 매 부팅 dump 를 전체 로그로 저장 → 끝나면 trn_summary.txt / trn_dump.csv 생성
#   결과 폴더 이름: logs/sdram_<시각>_board-<이름>
#
#   cd ~/gbt-local && bash sdram/run_dump_board.sh B1        (보드 이름은 마음대로, 예: B1, A, nokia2)
#   cd ~/gbt-local && bash sdram/run_dump_board.sh B2 20     (부팅 20번)
#   보드 교체 순서: 이 스크립트 끝(전원 OFF 메시지) → 보드 바꾸고 UART/전원 연결 → 다음 이름으로 다시 실행
# 멈추기: Ctrl-C (원본 복원 + 전원 OFF)

cd "$(dirname "$0")/.." || exit 1
BOARD=${1:?"사용법: bash sdram/run_dump_board.sh <보드이름> [부팅횟수]"}
N=${2:-10}
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
trap 'echo "### dump_board: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

if pgrep -f "sdram/run_(ch|train|arm|focus|dump|ref).sh|cli/sdram_campaign.py" >/dev/null; then
    echo "### dump_board: 다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행 (그 창에서 Ctrl-C)"; exit 1
fi
if ! git -C "$SBL" diff --quiet -- "${FILES[@]}"; then
    echo "### dump_board: SBL 소스가 원본이 아님 → 확인 후 다시 실행"; git -C "$SBL" diff --stat -- "${FILES[@]}"; exit 1
fi
echo "### dump_board: 보드 '$BOARD', 부팅 $N 번, SBL 원본 + dump 출력"
"$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots "$N" \
    --variants sdram/dump --only trn_dump_a --files "${FILES[@]}" --no-auto --full-log --no-cp-retry
rc=$?
d=$(ls -dt logs/sdram_*/ | head -1); d=${d%/}
nd="${d}_board-${BOARD}"
mv "$d" "$nd" && echo "$BOARD" > "$nd/BOARD.txt"
"$PY" cli/trn_parse.py "$nd" || echo "### dump_board: dump 정리 실패 (로그 확인)"
echo "### dump_board: 결과 폴더 $nd  (trn_summary.txt)"
if mountpoint -q ~/gbt 2>/dev/null; then
    timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### dump_board: 결과를 ~/gbt/logs 로 복사함"
fi
echo "### dump_board: 끝 (rc=$rc). 보드 전원 OFF 상태 → 보드 교체 후 다음 실행"
