"""
fc_sg_sweep.sh 결과 처리기 (cli/run.py --post fc_sg, 또는 시퀀스 파일의 "post": ["fc_sg"]).

보드 스크립트가 매 run 마다 찍는
    set=1 reg=def run=3 sum=1850 (a/b=925 925)
줄을 UART 로그에서 모아서
  - <base>_fc_sg.csv : set,reg,run,sum,a,b (원본 한 줄 = 한 행)
  - 설정(reg)별 통계 : n, avg, median, p10, min, max, FAST(sum >= FAST 기준) 개수
를 만든다. 통계 계산 방식은 스크립트의 stats() 와 같게 맞췄다
(정렬 후 p10 = v[max(1, int(n*0.1))] (1-based), median = 가운데 값/두 값 평균).

보드 스크립트 자체의 "===== RESULT" 표도 같이 파싱해서 우리 계산과 다르면
mismatch 로 표시한다 (파싱 누락 여부 확인용).
"""
from __future__ import annotations

import csv
import re

_RUN_RE = re.compile(
    r"set=(?P<set>\d+) reg=(?P<reg>\S+) run=(?P<run>\d+) sum=(?P<sum>-?\d+) "
    r"\(a/b=(?P<a>-?\d+) (?P<b>-?\d+)\)")
_BOARD_STAT_RE = re.compile(
    r"^(?P<reg>\S+)\s+n=(?P<n>\d+)\s+avg=\s*(?P<avg>[\d.]+)\s+median=\s*(?P<median>[\d.]+)")
_HEADER_RE = re.compile(r"^SWEEP \(SG on\): settings=\[(?P<names>[^\]]*)\]")


def _stats(vals: list[int], fast: int) -> dict:
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return {"n": 0}
    i10 = max(1, int(n * 0.1))
    med = v[(n + 1) // 2 - 1] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"n": n, "avg": round(sum(v) / n, 1), "median": med, "p10": v[i10 - 1],
            "min": v[0], "max": v[-1], "fast": sum(1 for x in v if x >= fast)}


def process(lines: list[str], result: dict, base: str) -> dict:
    fast = int(result.get("vars", {}).get("fast", 1800))
    rows, order, board = [], [], {}
    for raw in lines:
        line = raw.strip()
        m = _RUN_RE.search(line)
        if m:
            rows.append({k: (m.group(k) if k == "reg" else int(m.group(k)))
                         for k in ("set", "reg", "run", "sum", "a", "b")})
            continue
        h = _HEADER_RE.search(line)
        if h and not order:
            order = h.group("names").split()
            continue
        b = _BOARD_STAT_RE.search(line)
        if b:
            board[b.group("reg")] = {"n": int(b.group("n")), "avg": float(b.group("avg"))}

    if not order:
        order = list(dict.fromkeys(r["reg"] for r in rows))

    csv_path = base + "_fc_sg.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["set", "reg", "run", "sum", "a", "b"])
        w.writeheader()
        w.writerows(rows)

    table = {}
    for reg in order:
        st = _stats([r["sum"] for r in rows if r["reg"] == reg], fast)
        if reg in board:
            bs = board[reg]
            st["board_check"] = ("ok" if bs["n"] == st["n"] and abs(bs["avg"] - st.get("avg", 0)) < 0.11
                                 else f"mismatch(board n={bs['n']} avg={bs['avg']})")
        table[reg] = st

    summary = [f"fc_sg: {len(rows)} runs 파싱, FAST 기준 {fast} Mbps → {csv_path}",
               f"  {'reg':<6} {'n':>3} {'avg':>7} {'median':>7} {'p10':>5} {'min':>5} {'max':>5}  FAST"]
    for reg, st in table.items():
        if st["n"] == 0:
            summary.append(f"  {reg:<6}   0")
            continue
        chk = "" if st.get("board_check", "ok") == "ok" else f"  ⚠ {st['board_check']}"
        summary.append(f"  {reg:<6} {st['n']:>3} {st['avg']:>7.1f} {st['median']:>7} "
                       f"{st['p10']:>5} {st['min']:>5} {st['max']:>5}  {st['fast']}/{st['n']}{chk}")

    out = {"runs": len(rows), "fast_threshold": fast, "csv": csv_path, "table": table,
           "summary_lines": summary}
    if not rows:
        out["status"], out["reason"] = "FAIL", "fc_sg: 결과 줄(set=.. reg=.. sum=..)을 하나도 못 찾음"
    elif any(r["sum"] == 0 for r in rows):
        zeros = sum(1 for r in rows if r["sum"] == 0)
        out["warning"] = f"sum=0 인 run 이 {zeros}개 (iperf 연결 실패 가능성)"
        summary.append(f"  ⚠ {out['warning']}")
    return out
