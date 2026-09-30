"""
fc_sweep4.sh (2단계 스윕) 결과 처리기 (시퀀스 파일의 "post": ["fc_sweep4"]).

보드 스크립트 출력에서
    #################### PHASE 1 : screening (-t3 -i1 -w4M x 50) ####################
    [3/57] s440   PauseAll=480 Sys=440/400 Shared=300/260 Port=180/140
      verify OK (7 regs, try 1)
      verify mismatch (try 1): 0x1220:w400/r398
      ==> VERIFY_FAIL s440 : 0x1220:w400/r398  (측정 건너뜀)
      run   1/50 : A=  925.3  B=  912.1  total= 1837.4  pass
      run   2/50 : iperf 실패 (A=0 B=912.1) - 집계 제외
      ==> pass 48/50 (96%)  avg=... med=... p10=... min=... max=... sd=...
    #################### PHASE 2 : top 5 (-t10 -i1 -w4M x 50) ####################
    TOP5 by avg : L440 L460 ...
를 모아서
  - <base>_fc_sweep4.csv : phase,setting,run,a,b,total,mark (iperf 실패 run 은 mark=ERROR)
  - PHASE 별 세팅 통계 + avg 순위 (스크립트와 같은 계산) + 스크립트 "==> pass" 줄과 비교
  - 레지스터 검증 결과 (verify OK / mismatch / VERIFY_FAIL)
중간에 끊긴 실행이어도 그때까지 나온 run 은 전부 집계된다.
"""
from __future__ import annotations

import csv
import math
import re

_PHASE_RE = re.compile(r"#+ PHASE (?P<ph>[12]) :")
_TOP_RE = re.compile(r"TOP\d+ by \w+ :(?P<names>.*)$")
_DEAD_RE = re.compile(r"==> TRAFFIC_DEAD (?P<name>\S+)")
_SET_RE = re.compile(
    r"\[(?P<idx>\d+)/(?P<nset>\d+)\] (?P<name>\S+)\s+PauseAll=(?P<pa>\d+) "
    r"Sys=(?P<son>\d+)/(?P<soff>\d+) Shared=(?P<shon>\d+)/(?P<shoff>\d+) "
    r"Port=(?P<pton>\d+)/(?P<ptoff>\d+)")
_RUN_RE = re.compile(
    r"run\s+(?P<run>\d+)/(?P<runs>\d+) : A=\s*(?P<a>[\d.]+)\s+B=\s*(?P<b>[\d.]+)\s+"
    r"total=\s*(?P<total>[\d.]+)\s+(?P<mark>pass|FAIL)")
_ERR_RE = re.compile(r"run\s+(?P<run>\d+)/(?P<runs>\d+) : iperf .*?\(A=(?P<a>[\d.]+)\S* B=(?P<b>[\d.]+)")
_CNT_RE = re.compile(r"cnt\s+(?P<run>\d+)/\d+ : (?P<kv>.*)$")
_SUM_RE = re.compile(
    r"==> pass (?P<pass>\d+)/(?P<valid>\d+) \((?P<rate>\d+)%\)\s+avg=(?P<avg>[\d.]+) "
    r"med=(?P<med>[\d.]+) p10=(?P<p10>[\d.]+)")
_VOK_RE = re.compile(r"verify OK \(7 regs, try (?P<try>\d+)\)")
_VMIS_RE = re.compile(r"verify mismatch \(try (?P<try>\d+)\):(?P<bad>.*)$")
_VFAIL_RE = re.compile(r"==> VERIFY_FAIL (?P<name>\S+) :(?P<bad>.*?)\s*\(")
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


def _table_lines(title: str, order: list[str], table: dict, limit: int | None) -> list[str]:
    out = [title,
           f"  {'setting':<10} {'pass':>7} {'rate':>5} {'avg':>7} {'med':>7} {'p10':>7} {'min':>7} {'sd':>6}"]
    show = order[:limit] if limit else order
    for n in show:
        t = table[n]
        flag = "" if t["board_check"] in ("ok", "incomplete") else f"  ⚠ {t['board_check']}"
        err = f" err={t['errors']}" if t["errors"] else ""
        vr = f" verify-retry={t['verify_retry']}" if t.get("verify_retry") else ""
        out.append(f"  {n:<10} {t['pass']:>3}/{t['n']:<3} {t['rate']:>4}% {t['avg']:>7.1f} {t['median']:>7.1f} "
                   f"{t['p10']:>7.1f} {t['min']:>7.1f} {t['sd']:>6.1f}{err}{vr}{flag}")
    if limit and len(order) > limit:
        out.append(f"  ... (상위 {limit}개만 표시, 전체는 JSON/CSV)")
    return out


def counter_compare(rows: list[dict]) -> list[str]:
    """pass(빠른) run 과 FAIL(느린) run 에서 카운터/CPU 평균 증가량 비교."""
    fast = [r for r in rows if r["mark"] == "pass" and "cnt" in r]
    slow = [r for r in rows if r["mark"] == "FAIL" and "cnt" in r]
    if not fast or not slow:
        return []
    keys = sorted({k for r in fast + slow for k in r["cnt"]})
    def avg(rs, k):
        return sum(r["cnt"].get(k, 0) for r in rs) / len(rs)
    def cpu(rs):
        v = [int(r["cpu"]) for r in rs if str(r.get("cpu", "")).isdigit()]
        return sum(v) / len(v) if v else float("nan")
    out = [f" 카운터 비교 (run 당 평균 증가량)  빠른(pass) {len(fast)}회 vs 느린(FAIL) {len(slow)}회",
           f"  {'':<28}{'빠른':>11}{'느린':>11}",
           f"  {'cpu%':<28}{cpu(fast):>12.1f}{cpu(slow):>12.1f}"]
    ranked = sorted(keys, key=lambda k: -abs(avg(slow, k) - avg(fast, k)))
    for k in ranked[:12]:
        out.append(f"  {k:<28}{avg(fast, k):>12.1f}{avg(slow, k):>12.1f}")
    return out


def process(lines: list[str], result: dict, base: str) -> dict:
    pass_mbps = float(result.get("vars", {}).get("pass_mbps", 1500))
    rows: list[dict] = []
    order = {1: [], 2: []}
    regs: dict = {}
    board = {1: {}, 2: {}}
    verify = {1: {}, 2: {}}      # name -> {"ok_try": n} | {"fail": "..."}; mismatch 기록 포함
    top_board: list[str] = []
    dead: list[dict] = []
    nset = {1: None, 2: None}
    ph, cur = 1, None
    for raw in lines:
        line = raw.strip()
        m = _PASS_LINE_RE.search(line)
        if m:
            pass_mbps = float(m.group("p"))
            continue
        m = _PHASE_RE.search(line)
        if m:
            ph, cur = int(m.group("ph")), None
            continue
        m = _DEAD_RE.search(line)
        if m:
            dead.append({"phase": ph, "setting": m.group("name")})
            continue
        m = _TOP_RE.search(line)
        if m:
            top_board = m.group("names").split()
            continue
        m = _SET_RE.search(line)
        if m:
            cur = m.group("name")
            nset[ph] = int(m.group("nset"))
            if cur not in order[ph]:
                order[ph].append(cur)
            regs.setdefault(cur, {k: int(m.group(k)) for k in ("pa", "son", "soff", "shon", "shoff", "pton", "ptoff")})
            continue
        if cur is None:
            continue
        m = _VMIS_RE.search(line)
        if m:
            verify[ph].setdefault(cur, {}).setdefault("mismatch", []).append(m.group("bad").strip())
            continue
        m = _VOK_RE.search(line)
        if m:
            verify[ph].setdefault(cur, {})["ok_try"] = int(m.group("try"))
            continue
        m = _VFAIL_RE.search(line)
        if m:
            verify[ph].setdefault(m.group("name"), {})["fail"] = m.group("bad").strip()
            continue
        m = _RUN_RE.search(line)
        if m:
            rows.append({"phase": ph, "setting": cur, "run": int(m.group("run")), "a": float(m.group("a")),
                         "b": float(m.group("b")), "total": float(m.group("total")), "mark": m.group("mark")})
            continue
        m = _ERR_RE.search(line)
        if m:
            rows.append({"phase": ph, "setting": cur, "run": int(m.group("run")), "a": float(m.group("a")),
                         "b": float(m.group("b")), "total": "", "mark": "ERROR"})
            continue
        m = _CNT_RE.search(line)
        if m:
            kv = dict(x.split("=", 1) for x in m.group("kv").split() if "=" in x)
            for r in reversed(rows):
                if r["phase"] == ph and r["setting"] == cur and r["run"] == int(m.group("run")):
                    r["cpu"] = kv.pop("cpu", "")
                    r["cnt"] = {k: int(v) for k, v in kv.items() if v.lstrip("-").isdigit()}
                    break
            continue
        m = _SUM_RE.search(line)
        if m:
            board[ph][cur] = {"pass": int(m.group("pass")), "valid": int(m.group("valid")),
                              "avg": float(m.group("avg"))}

    csv_path = base + "_fc_sweep4.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["phase", "setting", "run", "a", "b", "total", "mark",
                                          "cpu", "counters"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "counters": " ".join(f"{k}={v}" for k, v in sorted((r.get("cnt") or {}).items()))})

    phases = {}
    for p in (1, 2):
        table = {}
        for name in order[p]:
            vals = [r["total"] for r in rows if r["phase"] == p and r["setting"] == name and r["mark"] != "ERROR"]
            st = _stats(vals, pass_mbps)
            st["errors"] = sum(1 for r in rows if r["phase"] == p and r["setting"] == name and r["mark"] == "ERROR")
            st["regs"] = regs[name]
            v = verify[p].get(name, {})
            st["verify"] = ("FAIL: " + v["fail"]) if "fail" in v else ("ok" if "ok_try" in v else "unknown")
            st["verify_retry"] = len(v.get("mismatch", [])) if "fail" not in v else 0
            if name in board[p]:
                b = board[p][name]
                ok = b["valid"] == st["n"] and b["pass"] == st["pass"] and abs(b["avg"] - st.get("avg", 0)) < 0.11
                st["board_check"] = "ok" if ok else f"mismatch(board {b['pass']}/{b['valid']} avg={b['avg']})"
            elif "fail" in v:
                st["board_check"] = "verify_fail"
            elif any(d["phase"] == p and d["setting"] == name for d in dead):
                st["board_check"] = "traffic_dead"
            else:
                st["board_check"] = "incomplete"
            table[name] = st
        ranked = sorted((n for n in order[p] if table[n]["n"]), key=lambda n: -table[n]["avg"])
        done = sum(1 for n in order[p] if table[n]["board_check"] not in ("incomplete",))
        vfail = {n: table[n]["verify"] for n in order[p] if table[n]["verify"].startswith("FAIL")}
        phases[p] = {"settings_done": done, "settings_total": nset[p], "ranking": ranked,
                     "verify_fail": vfail, "table": table}

    top_calc = phases[1]["ranking"][:len(top_board)] if top_board else []
    summary = [f"fc_sweep4: run {len(rows)}개 파싱, pass 기준 {pass_mbps:g} Mbps → {csv_path}"]
    p1, p2 = phases[1], phases[2]
    summary += _table_lines(
        f" PHASE 1 (스크리닝) 세팅 {p1['settings_done']}/{p1['settings_total'] or '?'}, avg 순",
        p1["ranking"], p1["table"], 10)
    if top_board and not p1["ranking"]:
        summary.append(f"  → PHASE 2 대상(지정): {' '.join(top_board)}")
    elif top_board:
        chk = "ok" if sorted(top_board) == sorted(top_calc) else f"⚠ 보드 선정 {top_board} ≠ 재계산 {top_calc}"
        summary.append(f"  → PHASE 2 선정: {' '.join(top_board)}  ({chk})")
    if order[2]:
        summary += _table_lines(
            f" PHASE 2 (정밀측정) 세팅 {p2['settings_done']}/{p2['settings_total'] or '?'}, avg 순",
            p2["ranking"], p2["table"], None)
    for p in (1, 2):
        for n, why in phases[p]["verify_fail"].items():
            summary.append(f"  ⚠ PHASE {p} VERIFY_{why}  [{n}] (측정 안 함)")

    summary += counter_compare(rows)
    for d in dead:
        summary.append(f"  ⚠ PHASE {d['phase']} TRAFFIC_DEAD [{d['setting']}] (iperf 연속 실패로 중단)")
    out = {"runs": len(rows), "dead": dead, "pass_mbps": pass_mbps, "csv": csv_path,
           "phase1": p1, "phase2": p2, "top_board": top_board, "top_recalc": top_calc,
           "summary_lines": summary}
    if not rows and not dead:
        out["status"], out["reason"] = "FAIL", "fc_sweep4: run 결과 줄을 하나도 못 찾음"
    return out
