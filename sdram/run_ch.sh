#!/bin/bash
# 10/12 채널 시험 (NOKIASUB6-275 메일 + 지금까지 결과 기반). 속도 3732.
#
# 1) 1CH 빌드 비교: RETRY_DDR_CH=1 → test_lpddr4.c 에서 omc_cfg(CH0)만 초기화 (ddr_sweep_ch != 2).
#    200번을 넘기면 CH1 쪽(또는 2CH 인터리빙)이 원인 쪽으로 좁혀진다.
#    Linux 가 1CH 메모리로 DM> 까지 뜨는지는 미확인 → 첫 후보가 첫 부팅에서 실패하면 1CH 는 건너뜀.
#    a) 원본 설정  b) 기준 설정 (2CH 비교값: 같은 설정 40/49/56)
# 2) 채널별 / gate·read 따로 adj (2CH): 배열이 [채널][DVFS 레벨] 인 것을 소스로 확인 → 태그 gABrCD
#    = gate CH0=A CH1=B, read CH0=C CH1=D. 기준 설정에서 태그마다 같은 설정 3번.
#
# 기준 설정 = VDD2 1.100 + VDDQ 0.725 + DQ VREF 0x0d + PU-CAL 0 (나머지 원본)
#
#   cd ~/gbt-local && bash sdram/run_ch.sh
#   ADJ_LIST='test-0 g11r00' bash sdram/run_ch.sh     (adj 목록 바꾸기)   SKIP_1CH=1 이면 1) 건너뜀
# 멈추기: touch logs/STOP_ALL  (지금 후보까지 하고 멈춤, 전원 OFF). 200번 통과가 나오면 거기서 멈춤.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
COMMON=(--port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 --variants sdram/none
        --files "${FILES[@]}" --explore none --repeat 3)
BASE="dsvref=0x0d,pdds=1,vddq=725,pucal=0"
STOP=logs/STOP_ALL
rm -f "$STOP"
trap 'echo "### run_ch: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

run() {   # $1 = 이름, 나머지 = sdram_campaign.py 인자. rc 0 = 200번 통과
    local title=$1; shift
    if [ -e "$STOP" ]; then echo "### run_ch: $STOP 있음 → 멈춤"; finish; fi
    echo "### run_ch: ===== $title ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    local w=$!
    "$PY" cli/sdram_campaign.py "${COMMON[@]}" "$@"
    local rc=$?
    kill $w 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_ch: ★ '$title' 에서 200번 통과 → 멈춤"; finish; fi
    sleep 2
}
finish() {
    if mountpoint -q ~/gbt 2>/dev/null; then
        timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_ch: 결과를 ~/gbt/logs 로 복사함"
    fi
    echo "### run_ch: 끝"; exit 0
}
first_boot_failed() {   # 가장 최근 캠페인의 첫 후보가 첫 부팅(회차 없음)에서 실패했나
    local d; d=$(ls -dt logs/sdram_*/ | head -1)
    "$PY" - "$d" <<'EOF'
import json, sys
st = json.load(open(sys.argv[1] + "state.json"))
r = list(st["done"].values())[:1]
sys.exit(0 if r and r[0].get("status") == "FAIL" and not (r[0].get("reboot") or "").split("/")[0].isdigit() else 1)
EOF
}

if [ -z "$SKIP_1CH" ]; then
    run "1-a 1CH / 원본 설정" --build-args "RETRY_DDR_CH=1" --seed "dsvref=0x14,pdds=1,adj=test-0"
    if first_boot_failed; then
        echo "### run_ch: 1CH 첫 부팅 실패 (Linux 가 1CH 로 안 뜨는 것일 수 있음) → 1CH 건너뜀. 로그 확인 필요"
    else
        run "1-b 1CH / 기준 설정" --build-args "DDRVDD2=1.100 RETRY_DDR_CH=1" --seed "$BASE,adj=test-0"
    fi
fi

for a in ${ADJ_LIST:-test-0 test-3 g11r00 g00r11 g99r00 g00r99 g19r19 g91r91}; do
    run "2 adj $a / 기준 설정" --build-args "DDRVDD2=1.100" --seed "$BASE,adj=$a"
done
finish
