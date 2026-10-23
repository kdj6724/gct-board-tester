#!/bin/bash
# 10/16 git 에 push 된 SBL 로 DDS 0x6 / 0x5 를 번갈아 kern reboot (기본 1000번씩), 마감 시각까지 계속.
#   dds6 : 소스 수정 없음 (push 된 그대로: SPHY_V9R8_WINBOND_ZQ_DDS 0x6)
#   dds5 : sphy_v9r8_functions.c 의 '#define SPHY_V9R8_WINBOND_ZQ_DDS 0x6' 한 줄만 0x5 로 바꿈
#          (시작할 때 테스트 PC 의 SBL 원본에서 패치를 만들고, 바뀐 줄이 정확히 1줄인지 확인)
#   빌드 = 프리셋 그대로 ./m2 DDR_WINBOND=y (빌드 인자 없음), 트레이닝 dump 패치 안 얹음
#   실패하면 그 세트는 거기서 끝나고 (로그 남음) 다음 세트(반대 DDS)가 1번부터 다시 셈
#   마감 시각이 되면 돌던 세트도 멈추고 끝 (보드 전원 OFF)
#   세트마다 logs/alt56_<BOARD>.txt 에 한 줄 기록
#
#   cd ~/gbt-local && BOARD=05 bash sdram/run_alt56.sh
#   UNTIL="2026-10-19 09:30" REBOOTS=1000 BOARD=05 bash sdram/run_alt56.sh   (기본값)
#   멈추기: touch ~/gbt-local/logs/STOP_ALL
# 결과 폴더: logs/sdram_<시각>_alt56_<BOARD>_<N>_dds6 / _dds5

cd "$(dirname "$0")/.." || exit 1
: "${BOARD:?BOARD=<보드번호> 를 붙여서 실행}"
UNTIL=${UNTIL:-2026-10-19 09:30}
REBOOTS=${REBOOTS:-1000}
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
F=base/drivers/orbit_ddram/sphy_v9r8_functions.c
FILES=($F base/drivers/orbit_ddram/mr_config.h base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c base/sys/ddr_main.c)
STOP=logs/STOP_ALL
SUM=logs/alt56_${BOARD}.txt
VD=sdram/alt56
END=$(date -d "$UNTIL" +%s) || { echo "### run_alt56: UNTIL 시각 형식 오류: $UNTIL"; exit 1; }
trap 'echo "### run_alt56: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

if pgrep -f "sdram/run_[a-z0-9]+\.sh|cli/sdram_campaign.py" | grep -qv "^$$\$"; then
    echo "### run_alt56: 다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행"; exit 1
fi
if ! git -C "$SBL" diff --quiet; then
    echo "### run_alt56: SBL 소스가 git 과 다름 → 확인 후 다시 실행"; git -C "$SBL" status --short; exit 1
fi
echo "### run_alt56: SBL $(git -C "$SBL" log -1 --format='%h %s')"

# 후보 만들기: dds6 = 빈 패치 (변경 없음), dds5 = define 한 줄만 0x5
mkdir -p "$VD"
echo "# no change: SBL as in git (DDS 0x6)" > "$VD/dds6.patch"
PAT='^\(#define[[:space:]]\+SPHY_V9R8_WINBOND_ZQ_DDS[[:space:]]\+\)0x6\b'
n=$(grep -c "^#define[[:space:]]\+SPHY_V9R8_WINBOND_ZQ_DDS[[:space:]]\+0x6\b" "$SBL/$F")
[ "$n" = 1 ] || { echo "### run_alt56: $F 에 '#define SPHY_V9R8_WINBOND_ZQ_DDS 0x6' 줄이 $n 개 (1개여야 함) → 확인"; exit 1; }
tmp=$(mktemp)
sed "s/$PAT/\10x5/" "$SBL/$F" > "$tmp"
diff -u --label "a/$F" --label "b/$F" "$SBL/$F" "$tmp" > "$VD/dds5.patch"
rm -f "$tmp"
ch=$(grep -c '^[-+]#define' "$VD/dds5.patch")
[ "$ch" = 2 ] || { echo "### run_alt56: dds5 패치가 예상과 다름"; cat "$VD/dds5.patch"; exit 1; }
grep '^[-+]#define' "$VD/dds5.patch"
printf "dds6\ndds5\n" > "$VD/ORDER.txt"
rm -f "$STOP"

one() {   # $1 = 후보 이름, $2 = 세트 번호
    ( while sleep 20; do
        if [ -e "$STOP" ] || [ "$(date +%s)" -ge "$END" ]; then
            for d in logs/sdram_*/; do touch "$d/stop"; done; break
        fi
      done ) &
    local w=$!
    "$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --reboots "$REBOOTS" \
        --variants "$VD" --no-auto --only "$1" --files "${FILES[@]}" --full-log
    local rc=$?
    kill $w 2>/dev/null
    local d; d=$(ls -dt logs/sdram_*/ | head -1); d=${d%/}
    local nd="${d}_alt56_${BOARD}_$2_$1"
    mv "$d" "$nd" && echo "$BOARD" > "$nd/BOARD.txt"
    local line; line=$(tail -n +2 "$nd/results.csv" 2>/dev/null | head -1 | cut -d, -f3,4,5)
    echo "$(date '+%F %T')  #$2 $1  ${line:-결과 없음}  ($nd)" | tee -a "$SUM"
    if mountpoint -q ~/gbt 2>/dev/null; then timeout 300 rsync -a logs/ ~/gbt/logs/; fi
    return $rc
}

i=1
while [ ! -e "$STOP" ] && [ "$(date +%s)" -lt "$END" ]; do
    for v in dds6 dds5; do
        [ -e "$STOP" ] || [ "$(date +%s)" -ge "$END" ] && break 2
        echo "### run_alt56: #$i $v (${REBOOTS}번, 마감 $UNTIL)"
        one "$v" "$i"; [ $? -eq 130 ] && exit 130
    done
    i=$((i+1))
done
echo "### run_alt56: 끝 (보드 전원 OFF). 기록: $SUM"
