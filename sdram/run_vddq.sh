#!/bin/bash
# VDDQ(MCP 레일) 시험 - 10/08. 속도는 SBL 기본(3732), 나머지 DDR 설정은 원본 그대로.
#
# 근거: base/sys/main.c ddr_init() 의 DDR_WINBOND 쪽이 pmic_vcc_mcp_set(VCC_MCP_0_700) (주석 //NG) 인데
#       같은 블록에 0.650 줄은 //OK 로 남아 있고, Winbond 데이터시트 285쪽 LPDDR4X VDDQ 범위는 0.57~0.65V.
#       부팅 로그의 'vddq=6(0.700)' 이 이 레일. (이 레일이 DRAM VDDQ 볼인지는 회로도로 미확인)
#
# 값마다 새 캠페인 폴더 (이름에 _vq<mV> 가 붙은 후보), 같은 설정 3번.
#   cd ~/gbt-local && bash sdram/run_vddq.sh        (끝나면 logs 를 ~/gbt/logs 로 복사)
#
# 전부 멈추려면: touch logs/STOP_ALL   (지금 후보까지 하고 멈춤)

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h base/sys/main.c)
STOP=logs/STOP_ALL
trap 'echo "### run_vddq: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

for v in ${VDDQ_LIST:-650 600 625}; do
    if [ -e "$STOP" ]; then echo "### run_vddq: $STOP 있음 → 멈춤"; break; fi
    echo "### run_vddq: ===== VDDQ ${v}mV ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    w=$!
    "$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 \
        --variants sdram/none --files "${FILES[@]}" --seed "vddq=$v" --explore none --repeat 3
    rc=$?
    kill $w 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_vddq: ★ VDDQ ${v}mV 에서 200번 통과 → 멈춤"; break; fi
done
[ -d ~/gbt/logs ] && rsync -a logs/ ~/gbt/logs/ && echo "### run_vddq: 결과를 ~/gbt/logs 로 복사함"
echo "### run_vddq: 끝"
