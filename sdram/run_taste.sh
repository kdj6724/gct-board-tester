#!/bin/bash
# 맛보기 1시간 (10/08 저녁). 속도 3732, VDD2 1.150(기본), VDDQ 0.725 (지금까지 전압 중 가장 나음).
# 데이터시트 재점검으로 새로 넣은 값 3가지를 하나씩, 각 2번:
#   refx=2   리프레시 2배 (tREFI 3.904us → 1.952us). 데이터시트 114쪽, 더 자주 리프레시는 항상 허용
#   pucal=0  MR3 PU-CAL VDDQ*0.6 (원본 1 = VDDQ*0.5). LPDDR4X 모드 274쪽
#   tsense=1 OMC 가 MR4(온도) 를 읽어 리프레시를 맞춤 (원본 0)
# 1시간이 지나면 새 후보를 시작하지 않음. 끝나면 결과를 ~/gbt/logs 로 복사.
#   cd ~/gbt-local && bash sdram/run_taste.sh
cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
UNTIL=$(date -d "+${MINUTES:-60} min" "+%Y-%m-%d %H:%M")
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h base/sys/main.c
       base/drivers/orbit_ddram/test_lpddr4.c)
trap 'echo "### taste: 중단됨"; exit 130' INT TERM HUP
echo "### taste: --until $UNTIL"
for seed in "vddq=725,refx=2" "vddq=725,pucal=0" "vddq=725,tsense=1"; do
    [ "$(date +%s)" -ge "$(date -d "$UNTIL" +%s)" ] && break
    echo "### taste: ===== $seed ====="
    "$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --confirm 1 --reboots 200 --until "$UNTIL" \
        --variants sdram/none --files "${FILES[@]}" --seed "$seed" --explore none --repeat 2
    [ $? -eq 0 ] && { echo "### taste: ★ $seed 통과"; break; }
done
# 노트북 공유가 죽어 있으면 경로 확인부터 멈출 수 있어서 통째로 시간 제한 (시험·전원 OFF 는 이미 끝난 뒤)
timeout -k 10 120 sh -c 'test -d ~/gbt/logs && rsync -a logs/ ~/gbt/logs/' && echo "### taste: 결과를 ~/gbt/logs 로 복사함" \
    || echo "### taste: ~/gbt/logs 복사 못 함 (노트북 공유 확인) - 결과는 ~/gbt-local/logs 에 있음"
echo "### taste: 끝"
