#!/bin/bash
# 10/13 PHY(GDM -> DDR) driver strength sweep. NOKIASUB6-275 메일 마지막 (Dean) 요청:
#   ZQ_CON0 : zq_mode_pdds, zq_mode_dds
#   ZQ_CON3 : zq_ck_dds, zq_ck_pdds, zq_ds1_dds, zq_ds1_pdds, zq_ds0_dds, zq_ds0_pdds
#   전부 같은 값으로 0x7(reset default = 원본과 같음), 0x6, 0x5, 0x4
#   TRM: 3'b111 30Ω / 110 34Ω / 101 40Ω / 100 48Ω (dds 설명 기준. pdds 의 값별 Ω 은 ZQ_CON3 설명에 없음)
#   원본 소스: ZQ_CON3 는 쓰는 곳 없음(reset 0x7), ZQ_CON0 shadow 초기값 0xff807404 (dds/pdds 7)
#   3732 는 PHY DVFS mode 0(normal) → ZQ_CON0/3 가 쓰임. DVFS0/1_CON3/CON6 (낮은 주파수용, 원본 7) 은 안 바꿈
# 원본 설정 (빌드 인자 없음, 다른 DDR 설정 안 바꿈) + 위 값만 바꿈 (sdram/zqdds/zqdds_N.patch, sphy_v9r8_functions.c)
#   + 트레이닝 dump 출력(sdram/dump/trn_dump_late.patch: DDR init 재시도 루프 끝에서 출력 → DDR RETRY FAIL 때도 찍힘),
#     전체 로그 저장. 부팅 실패 시 그 후보 로그의 마지막 dump = 실패한 부팅의 트레이닝 결과. 부팅 로그에 'phy zq dds/pdds=N' 확인용 출력
# 7 → 6 → 5 → 4 를 한 바퀴로 ROUNDS 바퀴 (기본 3), 후보당 kern reboot 200번
#
#   cd ~/gbt-local && bash sdram/run_zqdds.sh
#   ROUNDS=1 bash sdram/run_zqdds.sh
#   BOARD=B2 bash sdram/run_zqdds.sh     (보드 이름 → 결과 폴더 이름 ..._zqdds_B2_r1, BOARD.txt)
#   SET=zqdds_vq650 BOARD=B2 bash sdram/run_zqdds.sh
#     10/13 KJ 요청: VDDQ 0.650 (main.c DDR_WINBOND 쪽 VCC_MCP_0_650) 에서
#       vq650_both_7/6/5/4 : CH0/CH1 둘 다 같은 값 (위와 같은 방식)
#       vq650_ch1_6/5/4    : CH0 = 0x7(default), CH1(omc_cfg_1, num_ch 1) 만 변경  (ch1_7 = both_7 이라 생략)
#     → 결과 폴더 ..._zqdds_vq650_B2_r1
#   SET=zqdds_6comb BOARD=B2 bash sdram/run_zqdds.sh
#     10/13: VDDQ 0.700 / 0.650 × (둘 다 / CH0 만 / CH1 만) × DDS 6,5,4 + 기준(둘 다 7) = 한 바퀴 20개
#       vq700_* = main.c 원본(0.700), vq650_* = VCC_MCP_0_650
#       both = CH0/CH1 같은 값, ch0 = CH0(omc_cfg) 만 N·CH1 7, ch1 = CH1(omc_cfg_1) 만 N·CH0 7
#   SET=zqdds_3200 BOARD=17 bash sdram/run_zqdds.sh
#     10/14: DDRRATE=3200 (sdram/zqdds_3200/BUILD_ARGS), VDDQ 0.700(원본) × (둘 다 / CH0 만 / CH1 만) × DDS 7,6,5,4 = 한 바퀴 10개 (vq650_* 패치도 폴더에 있지만 ORDER.txt 에서 뺌)
#   세트 폴더에 BUILD_ARGS 파일이 있으면 그 내용을 --build-args 로 넘김 (없으면 빌드 인자 없음 = 3732)
#   10/14 3200 3단계 (각 세트 3바퀴씩, reboot 100):
#     SET="zq3200_def zq3200_both zq3200_ch1 zq3200_ch0" BOARD=17 bash sdram/run_zqdds.sh
#       zq3200_def  : DDS default(둘 다 0x7) × VDDQ 0.70 / 0.65
#       zq3200_both : 둘 다 0x6/0x5/0x4 × VDDQ 0.70 / 0.65
#       zq3200_ch1  : CH1 만 0x6/0x5/0x4 (CH0 default) × VDDQ 0.70 / 0.65
#       zq3200_ch0  : CH0 만 0x6/0x5/0x4 (CH1 default) × VDDQ 0.70 / 0.65
#   SET=zq3200_v700 BOARD=17 bash sdram/run_zqdds.sh
#     10/14: 3200 + VDDQ 0.70 + 둘 다 DDS 0x4 → 0x5 → 0x6 → 0x7(default, 마지막) 한 바퀴, 3바퀴
#   SET=zq3200_56 BOARD=<보드> bash sdram/run_zqdds.sh
#     10/15: 보드 17 에서 3번 모두 통과한 조합만 - 3200 + VDDQ 0.70 + 둘 다 DDS 0x5 / 0x6, 3바퀴
#   REBOOTS=200 으로 바꿀 수 있음 (기본 100)
# 멈추기: touch logs/STOP_ALL (다른 창에서). 끝나면 전원 OFF.

cd "$(dirname "$0")/.." || exit 1
PY=~/.venv-gbt/bin/python
SBL=/home/test/gct-board-tester/sbl
FILES=(base/drivers/orbit_ddram/sphy_v9r8_functions.c base/drivers/orbit_ddram/mr_config.h
       base/sys/main.c base/drivers/orbit_ddram/test_lpddr4.c base/sys/ddr_main.c)
STOP=logs/STOP_ALL
trap 'echo "### run_zqdds: 중단됨 (Ctrl-C)"; exit 130' INT TERM HUP

if pgrep -f "sdram/run_(ch|train|arm|focus|dump|ref|dump_board).sh|cli/sdram_campaign.py" >/dev/null; then
    echo "### run_zqdds: 다른 시험이 아직 돌고 있음 → 먼저 멈추고 다시 실행"; exit 1
fi
if ! git -C "$SBL" diff --quiet -- "${FILES[@]}"; then
    echo "### run_zqdds: SBL 소스가 원본이 아님 → 확인 후 다시 실행"; git -C "$SBL" diff --stat -- "${FILES[@]}"; exit 1
fi
SETS=${SET:-zqdds}            # 여러 세트를 공백으로: 앞 세트를 ROUNDS 바퀴 다 돌고 다음 세트
REBOOTS=${REBOOTS:-100}       # 10/14: 기본 100번 (이전 200)
for SET in $SETS; do
    [ -f "sdram/$SET/ORDER.txt" ] || { echo "### run_zqdds: sdram/$SET/ORDER.txt 없음"; exit 1; }
done
rm -f "$STOP"
for SET in $SETS; do
  BA=""; [ -f "sdram/$SET/BUILD_ARGS" ] && BA=$(grep -v "^#" "sdram/$SET/BUILD_ARGS" | tr "\n" " " | xargs)
  NAMES=$(grep -v "^#" "sdram/$SET/ORDER.txt" | xargs)
  # --only 로 후보를 넘김 → 통과(PASS)가 나와도 캠페인을 끝내지 않고 나머지 후보 계속 (10/14)
  for r in $(seq 1 "${ROUNDS:-3}"); do
    if [ -e "$STOP" ]; then echo "### run_zqdds: $STOP 있음 → 멈춤"; break 2; fi
    echo "### run_zqdds: ===== $SET $r 바퀴 (reboot $REBOOTS, ${BA:-빌드 인자 없음}: $NAMES) ====="
    ( while sleep 20; do [ -e "$STOP" ] && for d in logs/sdram_*/; do touch "$d/stop"; done && break; done ) &
    w=$!
    "$PY" cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600 --reboots "$REBOOTS" \
        --variants "sdram/$SET" --no-auto --only $NAMES --files "${FILES[@]}" \
        --extra-patch sdram/dump/trn_dump_late.patch --full-log ${BA:+--build-args "$BA"}
    rc=$?
    kill $w 2>/dev/null
    d=$(ls -dt logs/sdram_*/ | head -1); d=${d%/}
    nd="${d}_${SET}${BOARD:+_$BOARD}_r$r"
    mv "$d" "$nd" && { [ -n "$BOARD" ] && echo "$BOARD" > "$nd/BOARD.txt"; "$PY" cli/trn_parse.py "$nd" >/dev/null 2>&1; }
    if mountpoint -q ~/gbt 2>/dev/null; then timeout 300 rsync -a logs/ ~/gbt/logs/; fi   # 바퀴마다 복사
    [ $rc -eq 130 ] && { echo "### run_zqdds: 중단"; break 2; }
    sleep 2
  done
done
if mountpoint -q ~/gbt 2>/dev/null; then
    timeout 300 rsync -a logs/ ~/gbt/logs/ && echo "### run_zqdds: 결과를 ~/gbt/logs 로 복사함"
fi
echo "### run_zqdds: 끝 (보드 전원 OFF)"
