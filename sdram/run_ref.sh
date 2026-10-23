#!/bin/bash
# 10/12 PHY TRM(OpenEdges SPHY v9r8) 권장값으로 하나씩 맞춰 보기. 속도 3732, 같은 설정 3번씩.
#   근거: TRM Table 7.6 (LPDDR4X 1066<f≤2133MHz: gate/readadj 0 = test-0, 나머지 SBL 값과 일치),
#         TRM 7.4.1 "Initial VREF Settings without VREF Training and with ODT":
#           SoC(PHY) DQ VREF 0x1F(19.3%), MR22 SoC ODT RZQ/6, PHY term 60Ω(PHY_ODT 4, 이미 같음),
#           MR14 DQ VREF 19.2%(code 23), DQ ODT RZQ/4(이미 같음), CA ODT RZQ/1 + MR12 27.2% / CA ODT off 면 MR12 33.6%
#   공통: VDD2 1.100, VDDQ 0.725, PDDS 1, adj test-0, PU-CAL 1(원본, SoC ODT RZQ/6 에선 PU-CAL 0 못 씀)
#   0) 대조: DQ VREF 0x14(원본)
#   a) DQ VREF 0x1F
#   b) a + MR22 SoC ODT RZQ/6 (PHY_ODT 4 유지)
#   c) b + MR14 DQ VREF 19.2% (code 23)
#   d) c + CA ODT RZQ/1 (MR12 27.2% 그대로)
#   e) c + MR12 CA VREF 33.6% (code 29, CA ODT off 그대로)
#   모든 후보에 트레이닝 결과 dump 출력(sdram/dump/trn_dump_a.patch)을 같이 얹고 전체 콘솔 로그 저장
#     → 부팅마다 omc_cfg / omc_cfg_1 의 prbs_result, train_result 가 로그에 남음 (설정별 마진 비교용)
#
#   cd ~/gbt-local && bash sdram/run_ref.sh          (다른 시험이 돌고 있으면 끝날 때까지 기다림)
#   STEPS='a c' bash sdram/run_ref.sh                (일부만)
# 멈추기: touch logs/STOP_ALL (다른 창에서). 200번 통과가 나오면 거기서 멈춤. 끝나면 전원 OFF.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c)
COMMON=(--port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 --variants sdram/none
        --files "${FILES[@]}" --explore none --repeat 3 --build-args "DDRVDD2=1.100"
        --extra-patch sdram/dump/trn_dump_a.patch --full-log)
B="pdds=1,vddq=725,adj=test-0,pucal=1"
declare -A SEED=(
  [0]="$B,dsvref=0x14"
  [a]="$B,dsvref=0x1f"
  [b]="$B,dsvref=0x1f,socodt=6,phyodt=4"
  [c]="$B,dsvref=0x1f,socodt=6,phyodt=4,mr14=23"
  [d]="$B,dsvref=0x1f,socodt=6,phyodt=4,mr14=23,caodt=1"
  [e]="$B,dsvref=0x1f,socodt=6,phyodt=4,mr14=23,mr12=29"
)
STOP=logs/STOP_ALL
trap 'echo "### run_ref: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

while pgrep -f "sdram/run_(ch|train|arm|focus|dump).sh" >/dev/null; do
    [ -z "$waited" ] && echo "### run_ref: 다른 시험이 끝나길 기다리는 중 ($(date '+%m-%d %H:%M'))" && waited=1
    sleep 60
done
if ! git -C "$SBL" diff --quiet -- "${FILES[@]}"; then
    echo "### run_ref: SBL 소스가 원본이 아님 → 확인 후 다시 실행"; git -C "$SBL" diff --stat -- "${FILES[@]}"; exit 1
fi
rm -f "$STOP"

finish() {
    if mountpoint -q ~/gbt 2>/dev/null; then
        timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_ref: 결과를 ~/gbt/logs 로 복사함"
    fi
    echo "### run_ref: 끝"; exit 0
}
for st in ${STEPS:-0 a b c d e}; do
    if [ -e "$STOP" ]; then echo "### run_ref: $STOP 있음 → 멈춤"; finish; fi
    echo "### run_ref: ===== $st) ${SEED[$st]} ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    w=$!
    "$PY" cli/sdram_campaign.py "${COMMON[@]}" --seed "${SEED[$st]}"
    rc=$?
    kill $w 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_ref: ★ $st) 에서 200번 통과 → 멈춤"; finish; fi
    sleep 2
done
finish
