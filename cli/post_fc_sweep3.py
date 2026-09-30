"""
fc_sweep3.sh 결과 처리기 (시퀀스 파일의 "post": ["fc_sweep3"]).

보드 스크립트 출력에서
    [3/57] s440   PauseAll=480 Sys=440/400 Shared=300/260 Port=180/140
      run   1/70 : A=  925.3  B=  912.1  total= 1837.4  pass
      run   2/70 : iperf 실패 (A=0 B=912.1) - 집계 제외
      ==> pass 68/70 (97%)  avg=... med=... p10=... min=... max=... sd=...
를 모아서
  - <base>_fc_sweep3.csv : setting,run,a,b,total,mark (iperf 실패 run 은 mark=ERROR)
  - 세팅별 통계 (스크립트와 같은 방식: p10 = 정렬 후 v[int(n*0.1)+1] (1-based),
    sd = 표본표준편차) + 스크립트가 찍은 "==> pass" 줄과 비교(board_check)
를 만든다. 중간에 끊긴 실행(Ctrl-C 등)이어도 그때까지 나온 run 은 전부 집계된다.
"""
from __future__ import annotations

import csv
import math
import re

_SET_RE = re.compile(
    r"\[(?P<idx>\d+)/(?P<nset>\d+)\] (?P<name>\S+)\s+PauseAll=(?P<pa>\d+) "
    r"Sys=(?P<son>\d+)/(?P<soff>\d+) Shared=(?P<shon>\d+)/(?P<shoff>\d+) "
    r"Port=(?P<pton>\d+)/(?P<ptoff>\d+)")
_RUN_RE = re.compile(
    r"run\s+(?P<run>\d+)/(?P<runs>\d+) : A=\s*(?P<a>[\d.]+)\s+B=\s*(?P<b>[\d.]+)\s+"
    r"total=\s*(?P<total>[\d.]+)\s+(?P<mark>pass|FAIL)")
_ERR_RE = re.compile(r"run\s+(?P<run>\d+)/(?P<runs>\d+) : iperf .*?\(A=(?P<a>[\d.]+) B=(?P<b>[\d.]+)\)")
_SUM_RE = re.compile(
    r"==> pass (?P<pass>\d+)/(?P<valid>\d+) \((?P<rate>\d+)%\)\s+avg=(?P<avg>[\d.]+) "
    r"med=(?P<med>[\d.]+) p10=(?P<p10>[\d.]+)")
_PASS_LINE_RE = re.compile(r"pass line : (?P<p>\d+) Mbps")


def _stats(vals: list[float], pass_mbps: float) -> dict:
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return {"n": 0, "pass": 0, "rate": 0}
    avg = sum(v) / n
    sd = math.sqrt(sum((x - avg) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    med = v[(n + 1) // 2 - 1] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    p = sum(1 for x in v if x >= pass_mbps)
    return {"n": n, "pass": p, "rate": round(p * 100 / n), "avg": round(avg, 1),
            "median": round(med, 1), "p10": v[int(n * 0.10)], "min": v[0], "max": v[-1],
            "sd": round(sd, 1)}


def process(lines: list[str], result: dict, base: str) -> dict:
    pass_mbps = float(result.get("vars", {}).get("pass_mbps", 1500))
    rows, order, regs, board = [], [], {}, {}
    cur = None
    for raw in lines:
        line = raw.strip()
        m = _PASS_LINE_RE.search(line)
        if m:
            pass_mbps = float(m.group("p"))
            continue
        m = _SET_RE.search(line)
        if m:
            cur = m.group("name")
            if cur not in regs:
                order.append(cur)
                regs[cur] = {k: int(m.group(k)) for k in ("pa", "son", "soff", "shon", "shoff", "pton", "ptoff")}
            continue
        if cur is None:
            continue
        m = _RUN_RE.search(line)
        if m:
            rows.append({"setting": cur, "run": int(m.group("run")), "a": float(m.group("a")),
                         "b": float(m.group("b")), "total": float(m.group("total")),
                         "mark": m.group("mark")})
            continue
        m = _ERR_RE.search(line)
        if m:
            rows.append({"setting": cur, "run": int(m.group("run")), "a": float(m.group("a")),
                         "b": float(m.group("b")), "total": "", "mark": "ERROR"})
            continue
        m = _SUM_RE.search(line)
        if m:
            board[cur] = {"pass": int(m.group("pass")), "valid": int(m.group("valid")),
                          "avg": float(m.group("avg"))}

    csv_path = base + "_fc_sweep3.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["setting", "run", "a", "b", "total", "mark"])
        w.writeheader()
        w.writerows(rows)

    table = {}
    for name in order:
        vals = [r["total"] for r in rows if r["setting"] == name and r["mark"] != "ERROR"]
        st = _stats(vals, pass_mbps)
        st["errors"] = sum(1 for r in rows if r["setting"] == name and r["mark"] == "ERROR")
        st["regs"] = regs[name]
        if name in board:
            b = board[name]
            ok = b["valid"] == st["n"] and b["pass"] == st["pass"] and abs(b["avg"] - st.get("avg", 0)) < 0.11
            st["board_check"] = "ok" if ok else f"mismatch(board {b['pass']}/{b['valid']} avg={b['avg']})"
        else:
            st["board_check"] = "incomplete"   # 세팅 도중에 끝남
        table[name] = st

    ranked = sorted((n for n in order if table[n]["n"]),
                    key=lambda n: (-table[n]["rate"], -table[n]["avg"]))
    done = sum(1 for n in order if table[n]["board_check"] != "incomplete")
    nset = None
    for raw in lines:
        m = _SET_RE.search(raw)
        if m:
            nset = int(m.group("nset"))
            break
    summary = [f"fc_sweep3: 세팅 {done}/{nset or '?'} 완료, run {len(rows)}개 파싱, pass 기준 {pass_mbps:g} Mbps → {csv_path}",
               f"  {'setting':<10} {'pass':>7} {'rate':>5} {'avg':>7} {'med':>7} {'p10':>7} {'min':>7} {'sd':>6}"]
    show = ranked[:10] + (["def"] if "def" in table and "def" not in ranked[:10] else [])
    for n in show:
        t = table[n]
        flag = "" if t["board_check"] in ("ok", "incomplete") else f"  ⚠ {t['board_check']}"
        err = f" err={t['errors']}" if t["errors"] else ""
        summary.append(f"  {n:<10} {t['pass']:>3}/{t['n']:<3} {t['rate']:>4}% {t['avg']:>7.1f} {t['median']:>7.1f} "
                       f"{t['p10']:>7.1f} {t['min']:>7.1f} {t['sd']:>6.1f}{err}{flag}")
    if len(ranked) > 10:
        summary.append(f"  ... (상위 10개만 표시, 전체는 JSON/CSV)")

    out = {"settings_done": done, "settings_total": nset, "runs": len(rows),
           "pass_mbps": pass_mbps, "csv": csv_path, "ranking": ranked, "table": table,
           "summary_lines": summary}
    if not rows:
        out["status"], out["reason"] = "FAIL", "fc_sweep3: run 결과 줄을 하나도 못 찾음"
    return out
