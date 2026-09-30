#!/bin/sh
# ==================================================================
# RTL8363SC flow-control threshold sweep - 2단계판 (2026-09-30)
#
#   PHASE 1 (스크리닝) : 전체 세팅을 짧게(-t3) RUNS1 회씩 → avg 순 상위 TOPN 선정
#   PHASE 2 (정밀측정) : 상위 TOPN 세팅만 길게(-t10) RUNS2 회씩
#
#   (VERIFY=1 일 때만, 기본 끔) 세팅 적용 후 7개 레지스터를 "kern rtl8363 rd" 로 읽어서 쓴 값과 비교.
#   (DUMP=1 일 때만, 기본 끔) 세팅마다 "kern rtl8363 dump" 를 detail.txt 에 남김.
#     불일치 -> 1회 다시 쓰고 재검증 -> 그래도 불일치면 VERIFY_FAIL 로 기록하고
#     그 세팅은 측정 없이 건너뜀 (PHASE 1 순위에서도 빠짐).
#     rd 출력 형식 (보드에서 확인한 실제 출력):
#       rtl8363: rd 0x121f -> 0x01b8 (440)
#     -> 괄호 안 10진수를 쓴 값(10진수)과 비교.
#     kern 명령의 stdout 에서 먼저 찾고, 없으면 dmesg 에서 찾는다(printk).
#     dmesg 에서 찾을 때는 rd 직전에 /dev/kmsg 로 표식을 남기고 그 뒤 줄만 봄
#     (예전 rd 결과를 잘못 집어서 통과되는 일 방지).
#
#   BusyBox ash 호환 (배열/bc 미사용)
#
#   실행 예:
#     sh fc_sweep4.sh 2>&1 | tee /tmp/sweep_log.txt
#   중단 후 이어서 (끝난 세팅은 건너뜀, PHASE 별로 따로 기록):
#     RESUME=1 sh fc_sweep4.sh 2>&1 | tee -a /tmp/sweep_log.txt
# ==================================================================

# ---- config (gct-board-tester 의 --var 로 덮어씀) -----------------
IF=${IF:-eth0}                   # 보드 쪽 인터페이스 (카운터 수집용)
IP_A=${IP_A:-192.168.5.113} ;  PORT_A=${PORT_A:-5113}
IP_B=${IP_B:-192.168.5.112} ;  PORT_B=${PORT_B:-5112}

OPT1=${OPT1:-"-t3 -i1 -w4M"}     # PHASE 1 iperf 옵션
RUNS1=${RUNS1:-50}               # PHASE 1 세팅당 반복
OPT2=${OPT2:-"-t10 -i1 -w4M"}    # PHASE 2 iperf 옵션
RUNS2=${RUNS2:-50}               # PHASE 2 세팅당 반복
TOPN=${TOPN:-5}                  # PHASE 2 로 올릴 세팅 수 (PHASE 1 avg 순)
SETLIST=${SETLIST:-all}          # all = 57세팅 / sweep2 = fc_sweep2.sh 의 34세팅
                                 #   (L560p600 ~ L1000p1023 23개 제외)

PASS_MBPS=${PASS_MBPS:-1500}     # 두 스트림 합계 pass 기준 (두 PHASE 공통)
STAGGER=0                        # 두 번째 iperf 지연(초). 0 = 동시 출발
SETTLE=2                         # 레지스터 적용 후 대기(초)
RESUME=${RESUME:-0}              # 1 이면 이미 끝난 세팅 건너뜀
VERIFY=${VERIFY:-0}              # 1 이면 쓰고 나서 rd 로 readback 검증 (기본 끔)
DUMP=${DUMP:-0}                  # 1 이면 세팅마다 kern rtl8363 dump 를 detail.txt 에 (기본 끔)

# ---- 자동 복구(재부팅 후 이어하기)용 - 보통은 cli/sweep_campaign.py 가 넘겨줌 ----
PHASES=${PHASES:-12}             # 12 = PHASE 1,2 모두 / 1 = PHASE 1 만 / 2 = PHASE 2 만
SKIP1=${SKIP1:-}                 # PHASE 1 에서 건너뛸 세팅 (이전 부팅에서 끝난 것, 공백 구분)
SKIP2=${SKIP2:-}                 # PHASE 2 에서 건너뛸 세팅
TOPLIST=${TOPLIST:-}             # PHASE 2 대상 (비우면 이번 PHASE 1 결과로 선정)
DEAD_N=${DEAD_N:-3}              # 한 세팅에서 iperf 가 이만큼 연속 실패하면 트래픽 죽은 걸로 보고
                                 #   "==> TRAFFIC_DEAD <세팅>" 찍고 종료 (exit 3)
IPERF_GRACE=${IPERF_GRACE:-}     # iperf 가 -t 초 + 이만큼 지나도 안 끝나면 강제 종료(실패 처리)
                                 #   비우면 -t/2+2 초 (-t3→3, -t10→7, -t20→12) : PC 쪽 무출력
                                 #   한도(-t×2)보다 먼저 끊고 "재시도" 줄을 찍게 하려는 값

KERN="kern rtl8363"              # 레지스터 명령 접두어
COUNTERS=${COUNTERS:-1}          # 1 이면 run 마다 인터페이스 카운터 변화량 + CPU% 를 "cnt" 줄로 출력
CNT_RE=${CNT_RE:-"pause|drop|err|fifo|over|miss|coll|crc|disc|busy|full|retr"}   # 출력할 카운터 이름 패턴
MIB=${MIB:-0}                    # 1 이면 run 직전 "kern rtl cntrst", 직후 "kern rtl status" (스위치 MIB)
                                 #   출력은 "mib i/N begin" ~ "mib i/N end" 사이에 그대로 찍힘
OUT=/tmp/fc_sweep4
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

if [ "$SETLIST" = "sweep2" ]; then
    SETTINGS=$(echo "$SETTINGS" | grep -v -E '^L(5[6-9]0|[6-9][0-9]0|1000)p')
fi

mkdir -p "$OUT"
RESULT1="$OUT/result1.txt"     # PHASE 1 세팅별 통계
RESULT2="$OUT/result2.txt"     # PHASE 2 세팅별 통계
VFAIL="$OUT/verify_fail.txt"   # 검증 실패 세팅
DETAIL="$OUT/detail.txt"
if [ "$RESUME" != "1" ]; then
    : > "$RESULT1"; : > "$RESULT2"; : > "$VFAIL"; : > "$DETAIL"
fi
touch "$RESULT1" "$RESULT2" "$VFAIL" "$DETAIL"

NSET=$(echo "$SETTINGS" | grep -c '|')
T_START=$(date +%s)
MARKSEQ=0

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

# ---- 레지스터 읽기: "rtl8363: rd 0x121f -> 0x01b8 (440)" 의 440 을 출력 ----
#      못 읽으면 빈 문자열
read_reg() {
    REG=$1
    # 표식은 호출마다 달라야 함. read_reg 는 $( ) 서브셸에서 돌아서 여기서
    # 카운터를 올리면 부모에 반영이 안 됨 -> 호출하는 쪽(verify_all)에서 올려서 $2 로 받음
    MARK="fcsweep4-mark-$$-$(date +%s)-$2"
    # <7> = KERN_DEBUG : 콘솔에는 안 찍히고 dmesg 에만 남음
    echo "<7>$MARK" > /dev/kmsg 2>/dev/null
    LINE=$($KERN rd $REG 2>&1 | grep "rd $REG ->" | tail -n 1)
    if [ -z "$LINE" ]; then
        LINE=$(dmesg 2>/dev/null | sed -n "/$MARK/,\$p" | grep "rd $REG ->" | tail -n 1)
    fi
    echo "$LINE" | sed -n 's/.*rd [0-9a-fA-Fx]* -> *0x[0-9a-fA-F]* *(\([0-9]*\)).*/\1/p'
}

write_all() {
    $KERN wr 0x121e $1 >/dev/null 2>&1
    $KERN wr 0x121f $2 >/dev/null 2>&1
    $KERN wr 0x1220 $3 >/dev/null 2>&1
    $KERN wr 0x1221 $4 >/dev/null 2>&1
    $KERN wr 0x1222 $5 >/dev/null 2>&1
    $KERN wr 0x1227 $6 >/dev/null 2>&1
    $KERN wr 0x1228 $7 >/dev/null 2>&1
}

# 쓴 값 검증. 불일치 목록을 BADLIST 에 남기고, 전부 맞으면 0 리턴
verify_all() {
    BADLIST=""
    for KV in 0x121e=$1 0x121f=$2 0x1220=$3 0x1221=$4 0x1222=$5 0x1227=$6 0x1228=$7; do
        R=${KV%%=*}; W=${KV#*=}
        MARKSEQ=$((MARKSEQ+1))
        V=$(read_reg $R $MARKSEQ)
        [ "$V" = "$W" ] || BADLIST="$BADLIST $R:w$W/r${V:-none}"
    done
    [ -z "$BADLIST" ]
}

# 쓰기 + 검증, 불일치면 1회 재시도. 성공 0 / 실패 1
apply_verified() {
    TRY=1
    while [ $TRY -le 2 ]; do
        write_all "$@"
        if verify_all "$@"; then
            echo "  verify OK (7 regs, try $TRY)"
            return 0
        fi
        echo "  verify mismatch (try $TRY):$BADLIST"
        TRY=$((TRY+1))
    done
    return 1
}

# 두 iperf 동시 실행. -t + IPERF_GRACE 초 안에 안 끝나면 죽이고 TIMEDOUT=1
#  (트래픽이 죽으면 iperf 가 연결 대기로 2분 넘게 멈춰 있는 걸 막음)
run_one() {
    TIMEDOUT=0
    [ "$COUNTERS" = "1" ] && snap "$OUT/.c0"
    [ "$MIB" = "1" ] && kern rtl cntrst >/dev/null 2>&1
    iperf -c $IP_A $IPERF_OPT -p $PORT_A > "$OUT/.a" 2>&1 &
    PID_A=$!
    [ "$STAGGER" != "0" ] && sleep $STAGGER
    iperf -c $IP_B $IPERF_OPT -p $PORT_B > "$OUT/.b" 2>&1 &
    PID_B=$!
    TS=$(tsec "$IPERF_OPT")
    LIM=$(( TS + ${IPERF_GRACE:-$(( TS / 2 + 2 ))} ))
    W=0
    while kill -0 $PID_A 2>/dev/null || kill -0 $PID_B 2>/dev/null; do
        if [ $W -ge $LIM ]; then
            kill $PID_A $PID_B 2>/dev/null
            TIMEDOUT=1
            break
        fi
        sleep 1
        W=$((W+1))
    done
    wait $PID_A 2>/dev/null
    wait $PID_B 2>/dev/null
    [ "$COUNTERS" = "1" ] && snap "$OUT/.c1"
}

# ---- 카운터 스냅샷: /sys/class/net/$IF/statistics/* + ethtool -S (있으면) + /proc/stat ----
snap() {
    {
        for f in /sys/class/net/$IF/statistics/*; do
            echo "${f##*/} $(cat $f 2>/dev/null)"
        done
        if command -v ethtool >/dev/null 2>&1; then
            ethtool -S $IF 2>/dev/null | awk -F: 'NF==2 { k=$1; v=$2; gsub(/[ \t]/,"",k); gsub(/[ \t]/,"",v); print "e." k, v }'
        fi
        awk '/^cpu / { print "cpu_busy", $2+$3+$4+$7+$8; print "cpu_total", $2+$3+$4+$5+$6+$7+$8 }' /proc/stat
    } > "$1" 2>/dev/null
}

# 두 스냅샷 차이 -> "cpu=NN 이름=증가량 ..." (CNT_RE 에 맞고 0 이 아닌 것만)
cnt_delta() {
    awk -v re="$CNT_RE" '
        FNR==NR { a[$1]=$2; next }
        { d[$1]=$2-a[$1] }
        END {
            if (d["cpu_total"] > 0) printf "cpu=%d", 100*d["cpu_busy"]/d["cpu_total"]
            for (k in d) if (k !~ /^cpu_/ && k ~ re && d[k] != 0) printf " %s=%d", k, d[k]
        }' "$1" "$2"
}

# run_one 결과 -> A, B, BAD (BAD=1 : 0 이거나 시간초과)
get_ab() {
    A=$(parse_bw "$OUT/.a")
    B=$(parse_bw "$OUT/.b")
    if [ "$TIMEDOUT" = "1" ]; then
        BAD=1; A="$A(timeout)"
    else
        BAD=$(awk -v a="$A" -v b="$B" 'BEGIN{print (a<=0||b<=0)?"1":"0"}')
    fi
}

hms() {  # 초 -> Hh Mm
    awk -v s="$1" 'BEGIN{printf "%dh%02dm", int(s/3600), int((s%3600)/60)}'
}

tsec() {  # "-t3 -i1 ..." -> 3  (없으면 iperf 기본 10)
    T=$(echo "$1" | sed -n 's/.*-t *\([0-9][0-9]*\).*/\1/p')
    echo "${T:-10}"
}

# ---- 한 세팅 측정 ------------------------------------------------
# measure PHASE IDX N NAME PA SON SOFF SHON SHOFF PTON PTOFF
#   전역 IPERF_OPT, RUNS, RESULT 사용
measure() {
    PH=$1; IDX=$2; N=$3; NAME=$4; shift 4
    PA=$1; SON=$2; SOFF=$3; SHON=$4; SHOFF=$5; PTON=$6; PTOFF=$7

    if [ "$RESUME" = "1" ] && grep -q "^$NAME|" "$RESULT" 2>/dev/null; then
        echo "[$IDX/$N] $NAME  -- 이미 완료, 건너뜀"
        return
    fi
    if [ "$PH" = "1" ]; then SK=" $SKIP1 "; else SK=" $SKIP2 "; fi
    case "$SK" in *" $NAME "*)
        echo "[$IDX/$N] $NAME  -- 이전 부팅에서 완료, 건너뜀"
        return ;;
    esac

    ELAPSED=$(( $(date +%s) - T_START ))
    echo ""
    echo "--------------------------------------------------------------------"
    echo "[$IDX/$N] $NAME   PauseAll=$PA Sys=$SON/$SOFF Shared=$SHON/$SHOFF Port=$PTON/$PTOFF"
    echo "         PHASE $PH   경과 $(hms $ELAPSED)   $(date '+%H:%M:%S')"
    echo "--------------------------------------------------------------------"

    if [ "$VERIFY" = "1" ]; then
        if ! apply_verified $PA $SON $SOFF $SHON $SHOFF $PTON $PTOFF; then
            echo "  ==> VERIFY_FAIL $NAME :$BADLIST  (측정 건너뜀)"
            echo "$PH|$NAME|$BADLIST" >> "$VFAIL"
            echo "P$PH $NAME VERIFY_FAIL$BADLIST" >> "$DETAIL"
            return
        fi
    else
        write_all $PA $SON $SOFF $SHON $SHOFF $PTON $PTOFF
    fi
    sleep $SETTLE

    echo "" >> "$DETAIL"
    echo "=== P$PH [$NAME] PauseAll=$PA Sys=$SON/$SOFF Shared=$SHON/$SHOFF Port=$PTON/$PTOFF" >> "$DETAIL"
    [ "$DUMP" = "1" ] && $KERN dump >> "$DETAIL" 2>&1

    VALS="$OUT/vals${PH}_$NAME.txt"
    : > "$VALS"
    PASS=0
    ERRS=0
    CONSEC=0
    DN=$DEAD_N; [ $RUNS -lt $DN ] && DN=$RUNS     # 회수가 DEAD_N 보다 적어도 판정되게
    i=1
    while [ $i -le $RUNS ]; do
        run_one
        get_ab

        # iperf 실패(0 또는 시간초과) 감지 -> 1회 재시도
        if [ "$BAD" = "1" ]; then
            echo "  retry $i/$RUNS : iperf 0/시간초과 (A=$A B=$B) -> 재시도"
            sleep 2
            run_one
            get_ab
            if [ "$BAD" = "1" ]; then
                ERRS=$((ERRS+1))
                CONSEC=$((CONSEC+1))
                echo "  run $i/$RUNS : iperf 실패 (A=$A B=$B) - 집계 제외"
                echo "P$PH $NAME run$i ERROR A=$A B=$B" >> "$DETAIL"
                if [ $CONSEC -ge $DN ]; then
                    echo "  ==> TRAFFIC_DEAD $NAME  (PHASE $PH, iperf ${CONSEC}회 연속 실패)"
                    echo "$PH|$NAME" > "$OUT/dead"
                    return
                fi
                i=$((i+1))
                continue
            fi
        fi
        CONSEC=0

        TOT=$(awk -v a="$A" -v b="$B" 'BEGIN{printf "%.1f", a+b}')
        OK=$(awk -v t="$TOT" -v p="$PASS_MBPS" 'BEGIN{print (t>=p)?"1":"0"}')
        if [ "$OK" = "1" ]; then PASS=$((PASS+1)); MK="pass"; else MK="FAIL"; fi

        echo "$TOT" >> "$VALS"
        printf "  run %3d/%d : A=%7s  B=%7s  total=%7s  %s\n" \
               "$i" "$RUNS" "$A" "$B" "$TOT" "$MK"
        [ "$COUNTERS" = "1" ] && echo "  cnt $i/$RUNS : $(cnt_delta "$OUT/.c0" "$OUT/.c1")"
        if [ "$MIB" = "1" ]; then
            echo "  mib $i/$RUNS begin"
            kern rtl status 2>&1
            sleep 0.3                    # printk 로 나오는 경우 콘솔에 다 찍힐 시간
            echo "  mib $i/$RUNS end"
        fi
        echo "P$PH $NAME run$i A=$A B=$B total=$TOT $MK" >> "$DETAIL"
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

    ERRSTR=""; [ $ERRS -gt 0 ] && ERRSTR="  err=$ERRS"
    echo "  ==> pass $PASS/$VALID (${RATE}%)  avg=$AVG med=$MED p10=$P10 min=$MIN max=$MAX sd=$SD$ERRSTR"
    echo "$NAME|$PASS|$VALID|$RATE|$AVG|$MED|$P10|$MIN|$MAX|$SD|$PA|$SON/$SOFF|$SHON/$SHOFF|$PTON/$PTOFF" >> "$RESULT"
}

# ---- 결과표 (avg 내림차순) ---------------------------------------
summary() {
    printf "%-10s %9s %7s %8s %8s %8s %8s %8s %7s   %s\n" \
           "setting" "pass" "rate" "avg" "med" "p10" "min" "max" "sd" "PauseAll Sys Shared Port"
    echo "-------------------------------------------------------------------------------------------------------------"
    sort -t'|' -k5,5rn "$1" | while IFS='|' read N P T R AVG MED P10 MN MX SD RPA RS RSH RPT; do
        printf "%-10s %5s/%-3s %6s%% %8s %8s %8s %8s %8s %7s   %-8s %-9s %-9s %s\n" \
               "$N" "$P" "$T" "$R" "$AVG" "$MED" "$P10" "$MN" "$MX" "$SD" "$RPA" "$RS" "$RSH" "$RPT"
    done
}

T1=$(tsec "$OPT1"); T2=$(tsec "$OPT2")

echo "===================================================================="
echo " RTL8363SC FC sweep #4  (2단계: 스크리닝 -> 상위 ${TOPN}개 정밀측정, 레지스터 검증)"
echo "   settings  : $NSET ($SETLIST)      top : $TOPN"
echo "   phase 1   : $NSET x $RUNS1 회, iperf $OPT1   (~$(hms $(( NSET * RUNS1 * (T1 + 1) ))))"
echo "   phase 2   : $TOPN x $RUNS2 회, iperf $OPT2   (~$(hms $(( TOPN * RUNS2 * (T2 + 1) ))))"
echo "   pass line : ${PASS_MBPS} Mbps (A+B 합계)"
echo "   stream A  : $IP_A:$PORT_A"
echo "   stream B  : $IP_B:$PORT_B"
echo "   verify    : $VERIFY    dump : $DUMP    phases : $PHASES    dead_n : $DEAD_N"
[ -n "$SKIP1$SKIP2" ] && echo "   skip      : P1[$SKIP1]  P2[$SKIP2]"
[ -n "$TOPLIST" ] && echo "   toplist   : $TOPLIST"
echo "   resume    : $RESUME    시작: $(date '+%Y-%m-%d %H:%M:%S')"
echo "===================================================================="

# 트래픽 죽음 -> 표식 찍고 바로 종료 (PC 쪽이 전원 재인가 후 이어서 돌림)
bail_if_dead() {
    if [ -f "$OUT/dead" ]; then
        echo ""
        echo "!!!! TRAFFIC_DEAD 로 중단: $(cat "$OUT/dead")   경과 $(hms $(( $(date +%s) - T_START )))"
        echo "per-run    : aborted"
        exit 3
    fi
}
rm -f "$OUT/dead"

# ---- PHASE 1 -----------------------------------------------------
case "$PHASES" in *1*)
echo ""
echo "#################### PHASE 1 : screening ($OPT1 x $RUNS1) ####################"
IPERF_OPT="$OPT1"; RUNS=$RUNS1; RESULT="$RESULT1"
IDX=0
echo "$SETTINGS" | tr -d ' ' | grep '|' | while IFS='|' read NAME PA SON SOFF SHON SHOFF PTON PTOFF; do
    IDX=$((IDX+1))
    measure 1 $IDX $NSET $NAME $PA $SON $SOFF $SHON $SHOFF $PTON $PTOFF
    [ -f "$OUT/dead" ] && break
done
bail_if_dead

echo ""
echo "===================================================================="
echo " PHASE 1 SUMMARY  (세팅당 $RUNS1 회, $OPT1, avg 순)   경과 $(hms $(( $(date +%s) - T_START )))"
echo "===================================================================="
summary "$RESULT1"
if [ -s "$VFAIL" ]; then
    echo ""
    echo "[VERIFY_FAIL - 측정 안 함]"
    sed 's/^/  P/' "$VFAIL"
fi
;; esac

# ---- PHASE 2 : PHASE 1 avg 상위 TOPN (또는 TOPLIST) --------------
case "$PHASES" in *2*)
if [ -n "$TOPLIST" ]; then
    TOP=$TOPLIST
    TOPSRC="given"
else
    TOP=$(sort -t'|' -k5,5rn "$RESULT1" | awk -F'|' '$3>0 {print $1}' | head -n "$TOPN")
    TOPSRC="avg"
fi
NTOP=$(echo $TOP | wc -w)
echo ""
echo "#################### PHASE 2 : top $NTOP ($OPT2 x $RUNS2) ####################"
echo "TOP$TOPN by $TOPSRC :" $TOP
IPERF_OPT="$OPT2"; RUNS=$RUNS2; RESULT="$RESULT2"
IDX=0
for NAME in $TOP; do
    IDX=$((IDX+1))
    ROW=$(echo "$SETTINGS" | tr -d ' ' | grep "^$NAME|")
    [ -z "$ROW" ] && { echo "[$IDX/$NTOP] $NAME  -- 목록에 없는 세팅, 건너뜀"; continue; }
    OLDIFS=$IFS; IFS='|'; set -- $ROW; IFS=$OLDIFS
    measure 2 $IDX $NTOP $1 $2 $3 $4 $5 $6 $7 $8
    bail_if_dead
done
;; esac

# ---- final summary -----------------------------------------------
TOTAL=$(( $(date +%s) - T_START ))
echo ""
echo "===================================================================="
echo " FINAL (PHASE 2)  세팅당 $RUNS2 회, $OPT2, avg 순   총 소요 $(hms $TOTAL)"
echo "===================================================================="
summary "$RESULT2"
echo "===================================================================="
echo ""
echo "detail log : $DETAIL"
echo "raw result : $RESULT1 , $RESULT2"
echo "verify fail: $VFAIL"
echo "per-run    : $OUT/vals<phase>_<setting>.txt"
