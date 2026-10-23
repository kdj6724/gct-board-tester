#!/bin/bash
# 10/16 git 에 push 된 SBL 순수 버전 그대로 kern reboot 시험 (소스 수정 없음, dump 패치도 안 얹음).
#   빌드 = 프리셋 그대로 ./m2 DDR_WINBOND=y (빌드 인자 없음 → DDRRATE / DDS 는 push 된 소스 기본값)
#   1단계: 100번 x 10세트
#   2단계: SOAK(기본 100000)번 = 죽을 때까지. 실패하면 그 세트 로그 남기고, 다음 세트가 1번부터 다시 셈.
#   세트 끝날 때마다 logs/soak_<BOARD>.txt 에 한 줄 기록 (시각, 세트, 결과, 몇 번째)
#
#   cd ~/gbt-local && BOARD=05 bash sdram/run_soak.sh
#   멈추기: touch ~/gbt-local/logs/STOP_ALL  (다른 창에서. 끝나면 보드 전원 OFF)
# 결과 폴더: logs/sdram_<시각>_pure_<BOARD>_r1~r10, 그 다음 ..._pure_<BOARD>_inf1, inf2, ...

cd "$(dirname "$0")/.." || exit 1
: "${BOARD:?BOARD=<보드번호> 를 붙여서 실행}"
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c base/sys/ddr_main.c)
STOP=logs/STOP_ALL
SUM=logs/soak_${BOARD}.txt
trap 'echo "### run_soak: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

if pgrep -f "sdram/run_[a-z]+\.sh|cli/sdram_campaign.py" | grep -qv "^$$\$"; then
    echo "### run_soak: 다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행"; exit 1
fi
if ! git -C "$SBL" diff --quiet; then
    echo "### run_soak: SBL 소스가 git 과 다름 → 확인 후 다시 실행"; git -C "$SBL" status --short; exit 1
fi
echo "### run_soak: SBL $(git -C "$SBL" log -1 --format='%h %s')"

# 빈 후보 = 변경 없음 (sdram_campaign.py 는 '--- ' 없는 패치를 적용 안 함)
mkdir -p sdram/pure
echo pure > sdram/pure/ORDER.txt
echo "# no change: SBL as in git" > sdram/pure/pure.patch
rm -f "$STOP"

one() {   # $1 = reboot 수, $2 = 폴더 꼬리표
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    local w=$!
    "$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --reboots "$1" \
        --variants sdram/pure --no-auto --only pure --files "${FILES[@]}" --full-log
    local rc=$?
    kill $w 2>/dev/null
    local d; d=$(ls -dt logs/sdram_*/ | head -1); d=${d%/}
    local nd="${d}_pure_${BOARD}_$2"
    mv "$d" "$nd" && echo "$BOARD" > "$nd/BOARD.txt"
    local line; line=$(tail -n +2 "$nd/results.csv" 2>/dev/null | head -1 | cut -d, -f3,4,5)
    echo "$(date '+%F %T')  $2  ${line:-결과 없음}  ($nd)" | tee -a "$SUM"
    if mountpoint -q ~/gbt 2>/dev/null; then timeout 300 rsync -a logs/ ~/gbt/logs/; fi
    return $rc
}

for r in $(seq 1 10); do
    [ -e "$STOP" ] && break
    echo "### run_soak: 1단계 $r/10 (100번)"
    one 100 "r$r"; [ $? -eq 130 ] && exit 130
done
n=1
while [ ! -e "$STOP" ]; do
    echo "### run_soak: 2단계 $n 번째 (${SOAK:-100000}번, 죽을 때까지)"
    one "${SOAK:-100000}" "inf$n"; [ $? -eq 130 ] && exit 130
    n=$((n+1))
done
echo "### run_soak: 끝 (보드 전원 OFF). 기록: $SUM"
