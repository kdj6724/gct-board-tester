#!/bin/bash
# 10/12 SBL 하나만 빌드: 원본 소스 + 트레이닝 dump 출력(sdram/dump/trn_dump_late.patch), ./m2 (DDR_WINBOND 없이).
#   dump 위치: sys/ddr_main.c 의 DDR init 재시도 루프 끝 (DDR RETRY FAIL 뒤) → 성공/실패 상관없이 1번 출력
#   DDR 설정은 안 바꿈 (출력만 추가)
#   원본 소스는 DDR_WINBOND 없이 빌드하면 sphy_v9r8_functions.c 의 show_dfs_adj() 에서 컴파일 에러
#   (2차원 배열용 함수에 1차원 adj 배열을 넘김, -Werror) → 출력용 함수만 고치는 sdram/dump/m2_build_fix.patch 도 같이 적용
#   빌드 후 소스는 원본으로 되돌리고, 이미지를 이름 붙여 따로 복사해 둔다 (굽기는 직접).
#
#   cd ~/gbt-local && bash sdram/build_dump_sbl.sh              → images/sbl_7275x_c_dump_m2.bin
#   cd ~/gbt-local && bash sdram/build_dump_sbl.sh tethys3      → images/sbl_7275x_c_dump_m2_tethys3.bin
#   cd ~/gbt-local && bash sdram/build_dump_sbl.sh tethys3_1ch RETRY_DDR_CH=1   (이름 뒤 인자는 ./m2 에 그대로 전달)

cd "$(dirname "$0")/.." || exit 1
SBL=/home/test/gct-board-tester/sbl
PATCH=$(pwd)/sdram/dump/trn_dump_late.patch
FIX=$(pwd)/sdram/dump/m2_build_fix.patch
NAME=${1:+_$1}; shift 2>/dev/null
OUT=$SBL/images/sbl_7275x_c_dump_m2${NAME}.bin
F="base/drivers/orbit_ddram/test_lpddr4.c base/drivers/orbit_ddram/sphy_v9r8_functions.c base/sys/ddr_main.c"

if pgrep -f "cli/sdram_campaign.py" >/dev/null; then
    echo "### build_dump: 시험이 돌고 있음 → 먼저 멈추고 다시 실행"; exit 1
fi
if ! git -C "$SBL" diff --quiet -- $F; then
    echo "### build_dump: $F 가 원본이 아님 → 확인 후 다시 실행"; git -C "$SBL" diff --stat -- $F; exit 1
fi
cd "$SBL" || exit 1
git apply "$PATCH" || { echo "### build_dump: dump 패치 적용 실패"; exit 1; }
git apply "$FIX" || { echo "### build_dump: build fix 패치 적용 실패"; git apply -R "$PATCH"; exit 1; }
echo "### build_dump: dump 패치 적용 → ./m2 빌드"
touch /tmp/gbt_dump_stamp; sleep 1
echo "### build_dump: ./m2 $*"
./m2 "$@" > /tmp/gbt_dump_build.log 2>&1
if [ images/sbl_7275x_c.bin -nt /tmp/gbt_dump_stamp ]; then
    cp images/sbl_7275x_c.bin "$OUT"
    echo "### build_dump: 빌드 OK → $OUT ($(stat -c %s "$OUT") bytes)"
    grep -m1 -o "sbl_7275x_v[^ ]*_raw_[0-9]*\.bin" /tmp/gbt_dump_build.log | sed 's/^/### build_dump: 이미지 이름 /'
    ok=1
else
    echo "### build_dump: 빌드 실패 (로그 /tmp/gbt_dump_build.log)"
    grep -n -i -E "error|undefined" /tmp/gbt_dump_build.log | head -15
fi
git apply -R "$FIX"; git apply -R "$PATCH" && echo "### build_dump: 소스 원본으로 되돌림"
git diff --quiet -- $F && echo "### build_dump: 원본 확인 OK ($F)" || echo "### build_dump: ⚠ 아직 원본과 다름 - 확인 필요 ($F)"
[ -n "$ok" ]
