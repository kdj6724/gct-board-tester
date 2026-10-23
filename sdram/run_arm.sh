#!/bin/bash
# 10/12 VCC_ARM 시험. GDM7275X 데이터시트 9.2: VCC_ARM = "Digital Power for ARM+DDR core", 0.808~0.893V (typ 0.85).
#   SBL Makefile 빌드 인자 ARMVCC (기본 0.850) → main.c pll_reinit() 에서 pmic_vcc_arm_set(), ddr_init() 보다 먼저.
#   부팅 로그 'arm=1600(vcc=N(0.xxx))' 로 확인 (캠페인이 기대값과 비교).
#   기준 설정 (run_ch.sh 와 같음) = VDD2 1.100 + VDDQ 0.725 + DQ VREF 0x0d + PU-CAL 0 + adj test-0, 3732, 같은 설정 3번씩.
#   a) ARMVCC 0.875 (사양 안, 위쪽)   b) ARMVCC 0.825 (사양 안, 아래쪽 - 방향 확인용)
#   비교값: 같은 기준 설정 0.850 = 40/49/56/18/11/104/7
#
#   cd ~/gbt-local && bash sdram/run_arm.sh      (run_ch.sh / run_train.sh 가 돌고 있으면 끝날 때까지 기다림)
#   ARM_LIST='0.875' bash sdram/run_arm.sh       (값 바꾸기)
# 멈추기: touch logs/STOP_ALL. 200번 통과가 나오면 거기서 멈춤. 끝나면 전원 OFF.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
COMMON=(--port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 --variants sdram/none
        --files "${FILES[@]}" --explore none --repeat 3)
BASE="dsvref=0x0d,pdds=1,vddq=725,pucal=0,adj=test-0"
STOP=logs/STOP_ALL
trap 'echo "### run_arm: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

while pgrep -f "sdram/run_ch.sh|sdram/run_train.sh" >/dev/null; do
    [ -z "$waited" ] && echo "### run_arm: run_ch/run_train 이 끝나길 기다리는 중 ($(date '+%m-%d %H:%M'))" && waited=1
    sleep 60
done
[ -e "$STOP" ] && { echo "### run_arm: $STOP 있음 → 시작 안 함 (지우고 다시 실행)"; exit 0; }

finish() {
    if mountpoint -q ~/gbt 2>/dev/null; then
        timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_arm: 결과를 ~/gbt/logs 로 복사함"
    fi
    echo "### run_arm: 끝"; exit 0
}
for v in ${ARM_LIST:-0.875 0.825}; do
    if [ -e "$STOP" ]; then echo "### run_arm: $STOP 있음 → 멈춤"; finish; fi
    echo "### run_arm: ===== ARMVCC $v ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    w=$!
    "$PY" cli/sdram_campaign.py "${COMMON[@]}" --build-args "DDRVDD2=1.100 ARMVCC=$v" --seed "$BASE"
    rc=$?
    kill $w 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_arm: ★ ARMVCC $v 에서 200번 통과 → 멈춤"; finish; fi
    sleep 2
done
finish
