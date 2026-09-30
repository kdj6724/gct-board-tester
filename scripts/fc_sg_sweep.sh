#!/bin/sh
# fc_sg_sweep.sh - sweep RTL8363 FC threshold settings with SG(TSO/GSO) ON
#                  dual iperf2 (BusyBox ash, no arrays, no bc)
#
# SG is forced ON once at start. Each set runs every setting RUNS times;
# setting order is reversed every other set so time drift is spread evenly.
#
# Usage:  ./fc_sg_sweep.sh [SETS] [RUNS]          (default 5 x 10)
#         WOPT="-w 1M" ./fc_sg_sweep.sh 5 10      (iperf window, default none)
#         REGWR="kern rtl8363 wr" ...             (switch register write cmd)
#
# Settings (hex): name PauseAll SysOn SysOff ShOn ShOff PortOn PortOff
#   def   : shipping default (control)
#   ss440 : best of 2,600-run sweep (Sys 440/400, Shared 420/380)
#   s470..s540 : Sys+Shared ladder above 440/420, PauseAll raised to 560
#   (On-Off gap 40 kept, Port 180/140 kept - Port was a null result)

SETS=${1:-5}
RUNS=${2:-10}
T=${T:-10}
IF=${IF:-eth0}
WOPT=${WOPT:-}
REGWR=${REGWR:-kern rtl8363 wr}
A_IP=${A_IP:-192.168.5.112}; A_PORT=${A_PORT:-5112}
B_IP=${B_IP:-192.168.5.113}; B_PORT=${B_PORT:-5113}
FAST=${FAST:-1800}
TMPA=/tmp/sw_a.$$
TMPB=/tmp/sw_b.$$

#          PA    SysOn SysOff ShOn  ShOff PortOn PortOff
SETTINGS="def   0x1F0 0x0F0 0x0CC 0x0BE 0x09A 0x070 0x064
ss440 0x1E0 0x1B8 0x190 0x1A4 0x17C 0x0B4 0x08C
s470  0x230 0x1D6 0x1AE 0x1C2 0x19A 0x0B4 0x08C
s500  0x230 0x1F4 0x1CC 0x1E0 0x1B8 0x0B4 0x08C
s540  0x230 0x21C 0x1F4 0x208 0x1E0 0x0B4 0x08C"

NAMES=$(echo "$SETTINGS" | awk '{print $1}')
NAMES_REV=$(echo "$SETTINGS" | awk '{a[NR]=$1} END {for (i=NR;i>=1;i--) print a[i]}')

parse_mbps() {
    awk '{
        for (i = 2; i <= NF; i++) {
            if ($i == "Gbits/sec") v = $(i-1) * 1000
            else if ($i == "Mbits/sec") v = $(i-1)
            else if ($i == "Kbits/sec") v = $(i-1) / 1000
            else if ($i == "bits/sec") v = $(i-1) / 1000000
        }
    } END { if (v == "") v = 0; printf "%d\n", v + 0.5 }' "$1"
}

apply_setting() {   # $1 = name
    line=$(echo "$SETTINGS" | awk -v n="$1" '$1 == n')
    if [ -z "$line" ]; then echo "ERROR: no setting $1"; exit 1; fi
    set -- $line
    $REGWR 0x121E "$2" >/dev/null 2>&1
    $REGWR 0x121F "$3" >/dev/null 2>&1
    $REGWR 0x1220 "$4" >/dev/null 2>&1
    $REGWR 0x1221 "$5" >/dev/null 2>&1
    $REGWR 0x1222 "$6" >/dev/null 2>&1
    $REGWR 0x1227 "$7" >/dev/null 2>&1
    $REGWR 0x1228 "$8" >/dev/null 2>&1
    sleep 1
}

run_one() {
    iperf -c "$A_IP" -p "$A_PORT" -t "$T" $WOPT > "$TMPA" 2>&1 &
    PID_A=$!
    iperf -c "$B_IP" -p "$B_PORT" -t "$T" $WOPT > "$TMPB" 2>&1 &
    PID_B=$!
    wait $PID_A
    wait $PID_B
    a=$(parse_mbps "$TMPA")
    b=$(parse_mbps "$TMPB")
    echo $((a + b)) "$a" "$b"
}

stats() {   # $1 = label, $2 = values
    echo "$2" | tr ' ' '\n' | grep -v '^$' | sort -n | awk -v name="$1" -v fast="$FAST" '
        { v[NR] = $1; s += $1; if ($1 >= fast) f++ }
        END {
            n = NR
            if (n == 0) { printf "%-6s n=0\n", name; exit }
            i10 = int(n * 0.1); if (i10 < 1) i10 = 1
            med = (n % 2) ? v[(n + 1) / 2] : (v[n / 2] + v[n / 2 + 1]) / 2
            printf "%-6s n=%-3d avg=%7.1f median=%7.1f p10=%4d min=%4d max=%4d FAST=%d/%d\n",
                   name, n, s / n, med, v[i10], v[1], v[n], f + 0, n
        }'
}

# SG always ON
ethtool -K "$IF" sg on >/dev/null 2>&1
sleep 2
cur=$(ethtool -k "$IF" | awk '/^scatter-gather:/ {print $2}')
if [ "$cur" != "on" ]; then echo "ERROR: sg is '$cur', expected 'on'"; exit 1; fi

echo "SWEEP (SG on): settings=[$(echo $NAMES)] sets=$SETS runs=$RUNS t=$T wopt='$WOPT'"
echo "REGWR='$REGWR'  A=$A_IP:$A_PORT B=$B_IP:$B_PORT"

s=1
while [ $s -le "$SETS" ]; do
    if [ $((s % 2)) -eq 1 ]; then ORDER=$NAMES; else ORDER=$NAMES_REV; fi
    for name in $ORDER; do
        apply_setting "$name"
        r=1
        while [ $r -le "$RUNS" ]; do
            out=$(run_one)
            sum=${out%% *}
            echo "set=$s reg=$name run=$r sum=$sum (a/b=${out#* })"
            eval "RES_${name}=\"\$RES_${name} $sum\""
            r=$((r + 1))
        done
    done
    s=$((s + 1))
done

apply_setting def
rm -f "$TMPA" "$TMPB"

echo
echo "===== RESULT (SG on, wopt='$WOPT') ====="
for name in $NAMES; do
    eval "vals=\$RES_${name}"
    stats "$name" "$vals"
done
echo
echo "===== CSV (reg,sum) ====="
for name in $NAMES; do
    eval "vals=\$RES_${name}"
    for v in $vals; do echo "$name,$v"; done
done
