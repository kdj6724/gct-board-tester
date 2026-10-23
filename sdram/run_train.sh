#!/bin/bash
# 10/12 트레이닝 켜기 시험 (Receiver3 길이표: CA/CS 가 길이 기준을 크게 벗어남 + 지금 SBL 은 CBT 가 빌드에서 빠져 있음)
#   - command_bus_training / phy_vref_trn_mode 코드는 #if !defined(DDR_OPTI_SZ) 안이고 Makefile 기본 DDR_OPTI_SZ ?= 1
#     → 빌드 인자 DDR_OPTI_SZ=0 이 있어야 켜짐. dram_vref_trn_mode 는 DDR_OPTI_SZ 와 무관.
#   기준 설정 (run_ch.sh 와 같음) = VDD2 1.100 + VDDQ 0.725 + DQ VREF 0x0d + PU-CAL 0 + adj test-0, 속도 3732, 같은 설정 3번씩
#
#   0) DDR_OPTI_SZ=0 빌드가 되는지, 이미지 크기 비교 (원본 소스로 한 번씩 빌드만)
#   a) DDR_OPTI_SZ=0, 트레이닝 그대로       (빌드 옵션만 바뀐 대조군)
#   b) DDR_OPTI_SZ=0 + CBT                  (CA/CS 길이 차이를 트레이닝으로 보정)
#   c) 기본 빌드 + DRAM VREF 트레이닝
#   d) DDR_OPTI_SZ=0 + CBT + DRAM VREF 트레이닝
#   e) DDR_OPTI_SZ=0 + CBT + DRAM VREF + PHY VREF 트레이닝
#   a) 가 첫 부팅부터 실패하면 DDR_OPTI_SZ=0 쪽(a,b,d,e)은 건너뜀.
#
#   cd ~/gbt-local && bash sdram/run_train.sh       (run_ch.sh 가 돌고 있으면 끝날 때까지 기다렸다 시작)
# 멈추기: touch logs/STOP_ALL. 200번 통과가 나오면 거기서 멈춤. 끝나면 전원 OFF.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
COMMON=(--port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 --variants sdram/none
        --files "${FILES[@]}" --explore none --repeat 3)
BASE="dsvref=0x0d,pdds=1,vddq=725,pucal=0,adj=test-0"
STOP=logs/STOP_ALL
trap 'echo "### run_train: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

while pgrep -f "sdram/run_ch.sh" >/dev/null; do
    [ -z "$waited" ] && echo "### run_train: run_ch.sh 가 끝나길 기다리는 중 ($(date '+%m-%d %H:%M'))" && waited=1
    sleep 60
done
[ -e "$STOP" ] && { echo "### run_train: $STOP 있음 → 시작 안 함 (지우고 다시 실행)"; exit 0; }

finish() {
    if mountpoint -q ~/gbt 2>/dev/null; then
        timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_train: 결과를 ~/gbt/logs 로 복사함"
    fi
    echo "### run_train: 끝"; exit 0
}
run() {
    local title=$1; shift
    if [ -e "$STOP" ]; then echo "### run_train: $STOP 있음 → 멈춤"; finish; fi
    echo "### run_train: ===== $title ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    local w=$!
    "$PY" cli/sdram_campaign.py "${COMMON[@]}" "$@"
    local rc=$?
    kill $w 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_train: ★ '$title' 에서 200번 통과 → 멈춤"; finish; fi
    sleep 2
}
first_boot_failed() {
    local d; d=$(ls -dt logs/sdram_*/ | head -1)
    "$PY" - "$d" <<'EOF'
import json, sys
st = json.load(open(sys.argv[1] + "state.json"))
r = list(st["done"].values())[:1]
sys.exit(0 if r and (r[0].get("status") in ("ERROR", "MISMATCH") or (r[0].get("status") == "FAIL" and not (r[0].get("reboot") or "").split("/")[0].isdigit())) else 1)
EOF
}

# 0) 빌드 크기 비교 (소스는 원본 상태 - 캠페인은 끝날 때 항상 원본으로 복원)
echo "### run_train: 0) 빌드 크기 비교"
# m2 의 종료 코드는 마지막 pbl 빌드 결과라 SBL 빌드 실패를 못 잡음 → SBL 이미지가 새로 생겼는지로 판단 (프리셋과 같은 방식)
for opt in 1 0; do
    touch /tmp/gbt_opt_stamp; sleep 1
    ( cd "$SBL" && ./m2 DDR_WINBOND=y DDRVDD2=1.100 DDR_OPTI_SZ=$opt >/tmp/gbt_opt$opt.log 2>&1 )
    [ "$SBL/images/sbl_7275x_c.bin" -nt /tmp/gbt_opt_stamp ] && rc=0 || rc=1
    sz=$(stat -c %s "$SBL/images/sbl_7275x_c.bin" 2>/dev/null)
    [ $rc = 1 ] && grep -n -i -E "error|overflow|region|undefined" /tmp/gbt_opt$opt.log | head -15
    echo "### run_train:   DDR_OPTI_SZ=$opt 빌드 rc=$rc  images/sbl_7275x_c.bin = ${sz:-없음} bytes  (로그 /tmp/gbt_opt$opt.log)"
    [ $opt = 0 ] && OPT0_RC=$rc
done

if [ "$OPT0_RC" = 0 ]; then
    run "a) DDR_OPTI_SZ=0 대조군" --build-args "DDRVDD2=1.100 DDR_OPTI_SZ=0" --seed "$BASE"
    if first_boot_failed; then
        echo "### run_train: DDR_OPTI_SZ=0 첫 부팅 실패 → a/b/d/e 건너뜀 (로그 확인 필요)"
        OPT0_RC=1
    fi
else
    echo "### run_train: DDR_OPTI_SZ=0 빌드 실패 → a/b/d/e 건너뜀"
fi
[ "$OPT0_RC" = 0 ] && run "b) CBT" --build-args "DDRVDD2=1.100 DDR_OPTI_SZ=0" --seed "$BASE,cbt=1"
run "c) DRAM VREF 트레이닝" --build-args "DDRVDD2=1.100" --seed "$BASE,dvref=1"
[ "$OPT0_RC" = 0 ] && run "d) CBT + DRAM VREF" --build-args "DDRVDD2=1.100 DDR_OPTI_SZ=0" --seed "$BASE,cbt=1,dvref=1"
[ "$OPT0_RC" = 0 ] && run "e) CBT + DRAM VREF + PHY VREF" --build-args "DDRVDD2=1.100 DDR_OPTI_SZ=0" --seed "$BASE,cbt=1,dvref=1,pvref=1"
finish
