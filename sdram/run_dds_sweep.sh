#!/bin/bash
# SBL DDS sweep (2026-10-23): DDRRATE 3732 / VDDQ 0.650V / 1ch 고정, PHY DDS·PDDS 만 바꿔가며 kern reboot 반복.
#   후보 = sdram/sbl_dds/DDS<n>.patch (Codex ddr-profile-20261022-194224/sbl.patch 에서 DDS 만 바꾼 것)
#   preloader = uboot.patch(1ch/3732/650mV/DDS4) 로 빌드한 이미지 (SBL 다운로드용, 고정)
#
#   후보 하나: SBL 원본 복원 → 패치 → ./m2-wbd 빌드 → [전원 OFF/ON → BootROM USB → preloader → flash sbl(+sbl2)]
#              를 실패 시 최대 DL_RETRIES 번 → 전원 재인가 → 첫 부팅 로그에서 DDR_PROFILE=SBLSWEEP_DDS<n> 확인
#              → DM> → kern reboot REBOOTS 번 (매번 Starting kernel + DM>) → SBL 원본 복원 → 전원 OFF
#   - 후보 FAIL(DDR 문제로 보이는 실패): 로그 남기고 전원 OFF → 다음 DDS
#   - ERROR(빌드 실패, 다운로드 DL_RETRIES 번 모두 실패, 후보 표시 없이 다른 SBL 로 부팅 등 장비/인프라 문제):
#     sweep 전체 중단 (전원 OFF)
#
#   tmux 세션 gdm-test 안에서:
#     cd ~/gdm-autotest/gct-board-tester && bash sdram/run_dds_sweep.sh
#   설정 (환경변수, 모두 선택):
#     DDS="4 5 6 7"   REBOOTS=100   DL_RETRIES=10   FLASH_SBL2=1   BOARD=<보드번호, 폴더 이름용>
#     PORT=/dev/serial/by-id/...-if00-port0   BAUD=921600
#     PRELOAD=/home/test/gdm-autotest/sdk/uboot/preloader/u-boot_7275x_preload.bin
#     SBL=/home/test/gdm-autotest/sdk/sbl/7275x   PY=~/.venv-gbt/bin/python
#   멈추기: 다른 창에서 touch ~/gdm-autotest/gct-board-tester/logs/STOP_ALL  (지금 후보 끝나면 멈춤, Ctrl-C 는 즉시)
#   결과: logs/sdram_<시각>_dds_<BOARD>_DDS<n>/ , 한 줄 요약 logs/dds_sweep.txt

cd "$(dirname "$0")/.." || exit 1
DDS=${DDS:-4 5 6 7}
REBOOTS=${REBOOTS:-100}
DL_RETRIES=${DL_RETRIES:-10}
FLASH_SBL2=${FLASH_SBL2:-1}
BOARD=${BOARD:-b}
PORT=${PORT:-/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_0165E00C-if00-port0}
BAUD=${BAUD:-921600}
PRELOAD=${PRELOAD:-/home/test/gdm-autotest/sdk/uboot/preloader/u-boot_7275x_preload.bin}
SBL=${SBL:-/home/test/gdm-autotest/sdk/sbl/7275x}
PY=${PY:-$HOME/.venv-gbt/bin/python}
VD=sdram/sbl_dds
FILES=(Makefile m2-wbd base/sys/main.c base/sys/ddr_main.c base/drivers/orbit_ddram/sphy_v9r8_functions.c)
STOP=logs/STOP_ALL
SUM=logs/dds_sweep.txt
mkdir -p logs
trap 'echo "### dds_sweep: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

say() { echo "### dds_sweep: $*"; }

# ---- 시작 전 확인 ----------------------------------------------------
[ -x "$PY" ] || PY=python3
if pgrep -f "sdram/run_[a-z0-9_]+\.sh|cli/sdram_campaign.py|sbl_ddr_sweep" | grep -qv "^$$\$"; then
    say "다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행"; pgrep -af "sdram_campaign|sbl_ddr_sweep|run_[a-z0-9_]+\.sh"; exit 1
fi
if ! git -C "$SBL" diff --quiet || ! git -C "$SBL" diff --cached --quiet; then
    say "SBL 소스에 커밋 안 된 변경이 있음 → 확인 후 다시 실행"; git -C "$SBL" status --short; exit 1
fi
[ -f "$PRELOAD" ] || { say "preloader 이미지 없음: $PRELOAD"; exit 1; }
if ! grep -aq "DDR_PROFILE=20261022_194224 preloader" "$PRELOAD"; then
    say "preloader 이미지에 uboot.patch 표시(DDR_PROFILE=20261022_194224 preloader)가 없음 → 패치 적용해 다시 빌드 필요"
    exit 1
fi
[ -e "$PORT" ] || { say "시리얼 포트 없음: $PORT"; exit 1; }
for n in $DDS; do
    [ -f "$VD/DDS$n.patch" ] || { say "후보 패치 없음: $VD/DDS$n.patch"; exit 1; }
    git -C "$SBL" apply --check "$PWD/$VD/DDS$n.patch" || { say "DDS$n 패치가 현재 SBL 에 안 맞음"; exit 1; }
done
say "SBL $(git -C "$SBL" log -1 --format='%h %s')"
say "preloader $(sha256sum "$PRELOAD" | cut -c1-16)…  ($PRELOAD)"
say "DDS=[$DDS]  REBOOTS=$REBOOTS  DL_RETRIES=$DL_RETRIES  FLASH_SBL2=$FLASH_SBL2  PORT=$PORT"
rm -f "$STOP"

# ---- 후보 하나 --------------------------------------------------------
one() {   # $1 = 후보 이름 (DDS4 ...)
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    local w=$!
    "$PY" cli/sdram_campaign.py --sbl "$SBL" --preset sbl-dds-cycle --port "$PORT" --baud "$BAUD" \
        --reboots "$REBOOTS" --variants "$VD" --no-auto --only "$1" --files "${FILES[@]}" \
        --full-log --max-errors 1 \
        --var "dl_retries=$DL_RETRIES" --var "flash_sbl2=$FLASH_SBL2" --var "preload=$PRELOAD"
    local rc=$?
    kill $w 2>/dev/null
    local d; d=$(ls -dt logs/sdram_*/ | head -1); d=${d%/}
    local nd="${d}_dds_${BOARD}_$1"
    mv "$d" "$nd" && echo "$BOARD" > "$nd/BOARD.txt"
    STATUS=$("$PY" -c 'import csv,sys
r=list(csv.DictReader(open(sys.argv[1],encoding="utf-8")))
print(r[-1]["status"] if r else "NONE")' "$nd/results.csv" 2>/dev/null || echo NONE)
    local line; line=$(tail -n +2 "$nd/results.csv" 2>/dev/null | head -1 | cut -d, -f3,4,5)
    echo "$(date '+%F %T')  $1  ${line:-결과 없음}  ($nd)" | tee -a "$SUM"
    return $rc
}

for n in $DDS; do
    [ -e "$STOP" ] && { say "STOP_ALL → 멈춤"; break; }
    say "DDS$n 시작 (kern reboot ${REBOOTS}번)"
    one "DDS$n"; rc=$?
    [ $rc -eq 130 ] && exit 130
    case "$STATUS" in
        PASS) say "DDS$n PASS" ;;
        FAIL|MISMATCH) say "DDS$n $STATUS → 다음 DDS 로" ;;
        *) say "DDS$n $STATUS (장비/빌드/다운로드 문제) → sweep 전체 중단"; break ;;
    esac
done
say "끝 (보드 전원 OFF). 요약: $SUM"
