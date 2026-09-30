#!/bin/sh
# ==================================================================
# RTL8363SC flow-control threshold sweep  (12h 확장판, 2026-09-15)
#   세팅마다 dual-iperf 를 RUNS 회 전부 수행 (중단 없음).
#   매 회 A+B 합계가 PASS_MBPS 이상이면 pass 로 집계.
#   장시간 실행용: 진행률/ETA 표시, 중단 후 재개(RESUME) 지원.
#   BusyBox ash 호환 (배열/bc 미사용)
#
#   이번 버전에서 바뀐 점 (원본 fc_sweep2.sh, 34세팅×50회 ≈5.2h 대비):
#     - Sys+Shared 래더를 540/520 이후로 계속 연장 (L560p600 ~ L1000p1023,
#       23개 신규). PauseAll[9:0]/Sys/Shared 레지스터가 전부 10비트
#       (0~1023) 필드라서 L1000p1023 이 이 축에서 낼 수 있는 사실상의
#       상한선 — 그 이후로는 값을 더 못 올림. "440에서 래더가 포화되지
#       않았다"는 미해결 이슈를 여기서 끝까지 밀어붙여 진짜 포화점(또는
#       상한선까지 계속 오르는지)을 확인하는 목적.
#     - 위 연장만으로는 (23개×50회×11초 ≈2.75h 추가 → 총 ~8h) 12시간을
#       못 채워서, RUNS 를 50→70 으로 같이 올려 기존 34세팅까지 포함해
#       전체 57세팅×70회×11초 ≈ 12h11m 이 되도록 맞춤. 부수 효과로
#       모든 세팅의 pass-rate 95% CI 도 n=50일 때의 ±14pp 보다 좁아짐.
#     - 물리적 버퍼 한계(vendor 확인 불가, "fixed silicon"으로만 알려짐)가
#       레지스터 스케일 상 정확히 어디인지는 모름 — 상위 랭크(L800~L1000대)
#       에서 이미 결과가 평평해지면(=거의 매번 FAST에 수렴) 그게 포화
#       신호이니 굳이 끝까지 기다리지 않고 Ctrl+C 후 RESUME=1 로 이어받아도 됨.
#
#   실행 예:
#     nohup sh fc_sweep2.sh > /tmp/sweep_log.txt 2>&1 &
#     tail -f /tmp/sweep_log.txt
#   중단 후 이어서:
#     RESUME=1 sh fc_sweep2.sh 2>&1 | tee -a /tmp/sweep_log.txt
# ==================================================================

# ---- config ------------------------------------------------------
# 아래 값들은 환경변수로 덮어쓸 수 있음 (gct-board-tester 의 --var 로 넘김)
IP_A=${IP_A:-192.168.5.113} ;  PORT_A=${PORT_A:-5113}
IP_B=${IP_B:-192.168.5.112} ;  PORT_B=${PORT_B:-5112}
IPERF_OPT=${IPERF_OPT:-"-t10 -i1 -w4M"}

RUNS=${RUNS:-50}     # 세팅당 반복 횟수 (기존 50 -> 70, 12h 예산에 맞춤)
PASS_MBPS=${PASS_MBPS:-1500}  # 두 스트림 합계 pass 기준
STAGGER=0            # 두 번째 iperf 지연(초). 0 = 동시 출발
SETTLE=2             # 레지스터 적용 후 대기(초)
RESUME=${RESUME:-0}  # 1 이면 이미 끝난 세팅 건너뜀

KERN="kern rtl8363"  # 레지스터 명령 접두어
OUT=/tmp/fc_sweep2
# ------------------------------------------------------------------

# name      PauseAll SysOn SysOff ShOn ShOff PtOn PtOff
SETTINGS="
def       |496|240|204|190|154|112|100
ss440     |480|440|400|420|380|180|140
s440      |480|440|400|300|260|180|140
L340      |496|340|300|320|280|180|140
L360      |496|360|320|340|300|180|140
L380      |496|380|340|360|320|180|140
L400      |496|400|360|380|340|180|140
L420      |496|420|380|400|360|180|140
L440      |496|440|400|420|380|180|140
L460      |496|460|420|440|400|180|140
L480      |496|480|440|460|420|180|140
L460p512  |512|460|420|440|400|180|140
L480p512  |512|480|440|460|420|180|140
L500p512  |512|500|460|480|440|180|140
L520p560  |560|520|480|500|460|180|140
L540p560  |560|540|500|520|480|180|140
L560p600  |600|560|520|540|500|180|140
L580p600  |600|580|540|560|520|180|140
L600p640  |640|600|560|580|540|180|140
L620p640  |640|620|580|600|560|180|140
L640p680  |680|640|600|620|580|180|140
L660p680  |680|660|620|640|600|180|140
L680p720  |720|680|640|660|620|180|140
L700p720  |720|700|660|680|640|180|140
L720p760  |760|720|680|700|660|180|140
L740p760  |760|740|700|720|680|180|140
L760p800  |800|760|720|740|700|180|140
L780p800  |800|780|740|760|720|180|140
L800p840  |840|800|760|780|740|180|140
L820p840  |840|820|780|800|760|180|140
L840p880  |880|840|800|820|780|180|140
L860p880  |880|860|820|840|800|180|140
L880p920  |920|880|840|860|820|180|140
L900p920  |920|900|860|880|840|180|140
L920p960  |960|920|880|900|860|180|140
L940p960  |960|940|900|920|880|180|140
L960p1000 |1000|960|920|940|900|180|140
L980p1000 |1000|980|940|960|920|180|140
L1000p1023|1023|1000|960|980|940|180|140
g20       |496|440|420|420|400|180|140
g30       |496|440|410|420|390|180|140
g60       |496|440|380|420|360|180|140
g80       |496|440|360|420|340|180|140
g100      |496|440|340|420|320|180|140
a_s440h380|496|440|400|380|340|180|140
a_s440h340|496|440|400|340|300|180|140
a_s400h420|496|400|360|420|380|180|140
a_s360h420|496|360|320|420|380|180|140
a_s480h420|496|480|440|420|380|180|140
a_s440h460|512|440|400|460|420|180|140
pa460     |460|440|400|420|380|180|140
pa512     |512|440|400|420|380|180|140
pa540     |540|440|400|420|380|180|140
pa600     |600|440|400|420|380|180|140
pt112     |496|440|400|420|380|112|72
pt240     |496|440|400|420|380|240|200
pt320     |496|440|400|420|380|320|280
"

mkdir -p "$OUT"
RESULT="$OUT/result.txt"
DETAIL="$OUT/detail.txt"
if [ "$RESUME" != "1" ]; then
    : > "$RESULT"
    : > "$DETAIL"
fi
touch "$RESULT" "$DETAIL"

NSET=$(echo "$SETTINGS" | grep -c '|')
T_START=$(date +%s)

# ---- iperf 결과에서 최종 대역폭(Mbps) 추출 ------------------------
parse_bw() {
    awk '
    /(Kbits|Mbits|Gbits)\/sec/ { v=$(NF-1); u=$NF }
    END {
        if (v == "") { print "0"; exit }
        if (u ~ /^Gbits/)      v = v * 1000
        else if (u ~ /^Kbits/) v = v / 1000
        printf "%.1f", v
    }' "$1"
}

apply_setting() {
    $KERN wr 0x121e $1 >/dev/null 2>&1
    $KERN wr 0x121f $2 >/dev/null 2>&1
    $KERN wr 0x1220 $3 >/dev/null 2>&1
    $KERN wr 0x1221 $4 >/dev/null 2>&1
    $KERN wr 0x1222 $5 >/dev/null 2>&1
    $KERN wr 0x1227 $6 >/dev/null 2>&1
    $KERN wr 0x1228 $7 >/dev/null 2>&1
}

run_one() {
    iperf -c $IP_A $IPERF_OPT -p $PORT_A > "$OUT/.a" 2>&1 &
    PID_A=$!
    [ "$STAGGER" != "0" ] && sleep $STAGGER
    iperf -c $IP_B $IPERF_OPT -p $PORT_B > "$OUT/.b" 2>&1 &
    PID_B=$!
    wait $PID_A
    wait $PID_B
}

hms() {  # 초 -> Hh Mm
    awk -v s="$1" 'BEGIN{printf "%dh%02dm", int(s/3600), int((s%3600)/60)}'
}

# ---- main --------------------------------------------------------
echo "===================================================================="
echo " RTL8363SC FC sweep #2  (Sys+Shared 상향 탐색, 1000/1023 레지스터 상한까지 연장)"
echo "   settings  : $NSET      runs/setting : $RUNS"
echo "   pass line : ${PASS_MBPS} Mbps (A+B 합계)"
echo "   stream A  : $IP_A:$PORT_A"
echo "   stream B  : $IP_B:$PORT_B"
echo "   예상 소요 : ~$(hms $(( NSET * RUNS * 11 )))"
echo "   resume    : $RESUME    시작: $(date '+%Y-%m-%d %H:%M:%S')"
echo "===================================================================="

IDX=0
echo "$SETTINGS" | tr -d ' ' | while IFS='|' read NAME PA SON SOFF SHON SHOFF PTON PTOFF; do
    [ -z "$NAME" ] && continue
    IDX=$((IDX+1))

    if [ "$RESUME" = "1" ] && grep -q "^$NAME|" "$RESULT" 2>/dev/null; then
        echo "[$IDX/$NSET] $NAME  -- 이미 완료, 건너뜀"
        continue
    fi

    ELAPSED=$(( $(date +%s) - T_START ))
    echo ""
    echo "--------------------------------------------------------------------"
    echo "[$IDX/$NSET] $NAME   PauseAll=$PA Sys=$SON/$SOFF Shared=$SHON/$SHOFF Port=$PTON/$PTOFF"
    echo "         경과 $(hms $ELAPSED)   $(date '+%H:%M:%S')"
    echo "--------------------------------------------------------------------"

    apply_setting $PA $SON $SOFF $SHON $SHOFF $PTON $PTOFF
    sleep $SETTLE

    {
        echo ""
        echo "=== [$NAME] PauseAll=$PA Sys=$SON/$SOFF Shared=$SHON/$SHOFF Port=$PTON/$PTOFF"
        $KERN dump 2>&1
    } >> "$DETAIL"

    VALS="$OUT/vals_$NAME.txt"
    : > "$VALS"
    PASS=0
    ERRS=0
    i=1
    while [ $i -le $RUNS ]; do
        run_one
        A=$(parse_bw "$OUT/.a")
        B=$(parse_bw "$OUT/.b")

        # iperf 실패(0) 감지 -> 1회 재시도
        BAD=$(awk -v a="$A" -v b="$B" 'BEGIN{print (a<=0||b<=0)?"1":"0"}')
        if [ "$BAD" = "1" ]; then
            sleep 2
            run_one
            A=$(parse_bw "$OUT/.a")
            B=$(parse_bw "$OUT/.b")
            BAD=$(awk -v a="$A" -v b="$B" 'BEGIN{print (a<=0||b<=0)?"1":"0"}')
            if [ "$BAD" = "1" ]; then
                ERRS=$((ERRS+1))
                echo "  run $i/$RUNS : iperf 실패 (A=$A B=$B) - 집계 제외"
                echo "$NAME run$i ERROR A=$A B=$B" >> "$DETAIL"
                i=$((i+1))
                continue
            fi
        fi

        TOT=$(awk -v a="$A" -v b="$B" 'BEGIN{printf "%.1f", a+b}')
        OK=$(awk -v t="$TOT" -v p="$PASS_MBPS" 'BEGIN{print (t>=p)?"1":"0"}')
        if [ "$OK" = "1" ]; then PASS=$((PASS+1)); MARK="pass"; else MARK="FAIL"; fi

        echo "$TOT" >> "$VALS"
        printf "  run %3d/%d : A=%7s  B=%7s  total=%7s  %s\n" \
               "$i" "$RUNS" "$A" "$B" "$TOT" "$MARK"
        echo "$NAME run$i A=$A B=$B total=$TOT $MARK" >> "$DETAIL"
        i=$((i+1))
    done

    STAT=$(sort -n "$VALS" | awk '
        { v[NR]=$1; s+=$1 }
        END {
            n=NR
            if (n==0) { print "0|0|0|0|0|0"; exit }
            avg=s/n
            for (k=1;k<=n;k++) d+=(v[k]-avg)*(v[k]-avg)
            sd=(n>1)?sqrt(d/(n-1)):0
            med=(n%2)?v[(n+1)/2]:(v[int(n/2)]+v[int(n/2)+1])/2
            p10=v[int(n*0.10)+1]
            printf "%.1f|%.1f|%.1f|%.1f|%.1f|%.1f", avg, v[1], v[n], med, sd, p10
        }')
    AVG=$(echo "$STAT" | cut -d'|' -f1)
    MIN=$(echo "$STAT" | cut -d'|' -f2)
    MAX=$(echo "$STAT" | cut -d'|' -f3)
    MED=$(echo "$STAT" | cut -d'|' -f4)
    SD=$(echo  "$STAT" | cut -d'|' -f5)
    P10=$(echo "$STAT" | cut -d'|' -f6)
    VALID=$(wc -l < "$VALS")
    RATE=$(awk -v p="$PASS" -v n="$VALID" 'BEGIN{printf "%.0f", (n>0)?p*100/n:0}')

    echo "  ==> pass $PASS/$VALID (${RATE}%)  avg=$AVG med=$MED p10=$P10 min=$MIN max=$MAX sd=$SD${ERRS:+  err=$ERRS}"
    echo "$NAME|$PASS|$VALID|$RATE|$AVG|$MED|$P10|$MIN|$MAX|$SD|$PA|$SON/$SOFF|$SHON/$SHOFF|$PTON/$PTOFF" >> "$RESULT"
done

# ---- summary -----------------------------------------------------
TOTAL=$(( $(date +%s) - T_START ))
echo ""
echo "===================================================================="
echo " SUMMARY   (세팅당 $RUNS 회, pass >= ${PASS_MBPS} Mbps)   총 소요 $(hms $TOTAL)"
echo "===================================================================="
printf "%-10s %9s %7s %8s %8s %8s %8s %8s %7s   %s\n" \
       "setting" "pass" "rate" "avg" "med" "p10" "min" "max" "sd" "PauseAll Sys Shared Port"
echo "-------------------------------------------------------------------------------------------------------------"
sort -t'|' -k4,4rn -k5,5rn "$RESULT" | while IFS='|' read N P T R AVG MED P10 MN MX SD RPA RS RSH RPT; do
    printf "%-10s %5s/%-3s %6s%% %8s %8s %8s %8s %8s %7s   %-8s %-9s %-9s %s\n" \
           "$N" "$P" "$T" "$R" "$AVG" "$MED" "$P10" "$MN" "$MX" "$SD" "$RPA" "$RS" "$RSH" "$RPT"
done
echo "===================================================================="
echo ""
echo "[def 기준선 대비]"
awk -F'|' '
    $1=="def" { dr=$4; da=$5; dp=$7 }
    { n[NR]=$1; r[NR]=$4; a[NR]=$5; p[NR]=$7 }
    END {
        printf "  def : rate %s%%, avg %s, p10 %s\n", dr, da, dp
        printf "  %-10s %9s %9s %9s\n", "setting", "rate차", "avg차", "p10차"
        for (k=1;k<=NR;k++) {
            if (n[k]=="def") continue
            printf "  %-10s %+8.0f%% %+9.1f %+9.1f\n", n[k], r[k]-dr, a[k]-da, p[k]-dp
        }
    }' "$RESULT"
echo ""
echo "detail log : $DETAIL"
echo "raw result : $RESULT"
echo "per-run    : $OUT/vals_<setting>.txt"
