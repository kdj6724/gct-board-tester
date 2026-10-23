#!/bin/bash
# SDRAM 추가 시험 3가지를 차례로 돌린다 (10/07).
#   3) DDRRATE=3200 비교   - 새 캠페인 2개 (원본 설정 / 지금 1등 ODT 설정), 각각 같은 설정 2번
#                            소스 파일은 안 바꾸고 빌드 인자로만 3200. 다음 단계부터는 다시 3732 로 빌드/다운로드되므로
#                            보드 SBL 도 자동으로 원래 속도로 돌아간다.
#   1) ODT 1등/2등 주변에서 DQ VREF, PDDS, adj 다시 맞추기  (sdram_20261007_164832 이어서, 최대 30 후보)
#   2) SoC ODT(MR22) 와 PHY_ODT 를 따로 바꿔 보기          (같은 폴더 이어서, 최대 20 후보)
#
#   cd ~/gbt && bash sdram/run_all.sh
#
# 중간에 전부 멈추려면: touch ~/gbt/logs/STOP_ALL  (지금 단계의 지금 후보까지 하고 멈춤)
# Ctrl-C 로 멈추면 그 단계는 전원 OFF 후 끝나고, 다음 단계로 넘어가지 않는다.
# 어느 단계든 200번 통과가 나오면 거기서 멈춘다.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
COMMON=(--port /dev/ttyUSB0 --baud 921600 --confirm 1)
ODT=logs/sdram_20261007_164832
STOP=logs/STOP_ALL
trap 'echo "### run_all: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

run() {   # $1 = 단계 이름, 나머지 = sdram_campaign.py 인자
    local title=$1; shift
    if [ -e "$STOP" ]; then echo "### run_all: $STOP 있음 → '$title' 부터 안 함"; exit 0; fi
    echo "### run_all: ===== $title ====="
    # STOP_ALL 이 생기면 지금 단계 캠페인에도 stop 파일을 넣어 후보 경계에서 멈추게 한다
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    local watcher=$!
    "$PY" cli/sdram_campaign.py "${COMMON[@]}" "$@"
    local rc=$?
    kill $watcher 2>/dev/null
    if [ $rc -eq 0 ]; then echo "### run_all: ★ '$title' 에서 통과 → 여기서 멈춤"; exit 0; fi
    return 0
}

# 3) DDRRATE=3200 (새 캠페인 폴더 2개)
run "3-a DDRRATE=3200 / 원본 설정" --variants sdram/none --reboots 200 --build-args "DDRRATE=3200" \
    --seed "pdds=1" --explore none
run "3-b DDRRATE=3200 / ODT 1등 설정" --variants sdram/none --reboots 200 --build-args "DDRRATE=3200" \
    --seed "dsvref=0x20,pdds=1,adj=test-5,dqodt=0,socodt=5,phyodt=5" --explore none

# 1) ODT 1등/2등 주변 DQ VREF, PDDS, adj
rm -f "$ODT/stop"
run "1 ODT 고정, DQ VREF/PDDS/adj" --resume "$ODT" --explore dsvref,pdds,adj --max-new 30

# 2) SoC ODT 와 PHY_ODT 따로
rm -f "$ODT/stop"
run "2 SoC ODT / PHY_ODT 따로" --resume "$ODT" --explore socodt,phyodt --max-new 20

echo "### run_all: 전부 끝"
