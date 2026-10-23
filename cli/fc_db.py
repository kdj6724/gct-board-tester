#!/usr/bin/env python3
"""
FC 스윕 누적 결과 DB (data/fc_db.csv, git 으로 관리) - 캠페인이 끝난 세팅의 iperf run 을 한 줄씩 쌓는다.

같은 레지스터 값 조합은 이름이 달라도 같은 key (PauseAll-SysOn-SysOff-ShOn-ShOff-PtOn-PtOff).
조건(iperf -t 초, cpus)이 같은 것끼리 합쳐서 본다.

  python cli/fc_db.py report                 # -t10(집중) 누적 결과, 빠른 회차 비율 순
  python cli/fc_db.py report --t 3           # -t3(탐색) 누적 결과
  python cli/fc_db.py import logs/campaign_* # 예전 캠페인 결과를 DB 에 넣기 (중복은 알아서 건너뜀)
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import math
import os
import re
import sys

CLI_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CLI_DIR)
DB_PATH = os.path.join(ROOT, "data", "fc_db.csv")   # git 으로 관리 (logs/ 는 .gitignore)
FIELDS = ["ts", "campaign", "run", "mode", "phase", "tsec", "cpus", "key", "name",
          "iperf_run", "a", "b", "total", "mark", "cpu", "pin", "opt"]   # pin = iperf A,B 코어 지정 ("1,3", 없으면 "")


def key_of(vals) -> str:
    return "-".join(str(int(x)) for x in vals)


def tsec_of(opt: str) -> int:
    m = re.search(r"-t\s*(\d+)", opt or "")
    return int(m.group(1)) if m else 10


def load(path: str = DB_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append(rows: list[dict], path: str = DB_PATH):
    if os.path.exists(path):                        # 예전 헤더(열 부족)면 새 헤더로 옮겨 적기
        with open(path, encoding="utf-8") as f:
            head = f.readline().strip().split(",")
        if head != FIELDS:
            old = load(path)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                w.writeheader()
                for r in old:
                    r = {k: (r.get(k) or "") for k in FIELDS}
                    if not r["opt"]:                # opt 열 생기기 전 데이터는 전부 -w4M 이었음
                        r["opt"] = f"-t{r['tsec']} -i1 -w4M"
                    w.writerow(r)
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def norm_opt(opt: str) -> str:
    return " ".join((opt or "").split())


def pooled(rows: list[dict], tsec: int | None = None, cpus: str | None = None,
           pin: str | None = None, opt: str | None = None) -> dict:
    """key -> {n, pass, avg, names, campaigns}. ERROR run 은 제외."""
    out: dict = {}
    for r in rows:
        if r["mark"] not in ("pass", "FAIL"):
            continue
        if tsec is not None and int(r["tsec"]) != tsec:
            continue
        if cpus is not None and str(r["cpus"]) != str(cpus):
            continue
        if pin is not None and (r.get("pin") or "") != pin:
            continue
        if opt is not None and norm_opt(r.get("opt") or f"-t{r['tsec']} -i1 -w4M") != norm_opt(opt):
            continue
        d = out.setdefault(r["key"], {"n": 0, "pass": 0, "sum": 0.0, "sq": 0.0, "min": 1e9,
                                      "names": set(), "campaigns": set()})
        v = float(r["total"])
        d["n"] += 1
        d["pass"] += r["mark"] == "pass"
        d["sum"] += v
        d["sq"] += v * v
        d["min"] = min(d["min"], v)
        d["names"].add(r["name"])
        d["campaigns"].add(r["campaign"])
    for d in out.values():
        d["avg"] = d["sum"] / d["n"]
        d["rate"] = d["pass"] / d["n"]
        d["sd"] = math.sqrt(max(0.0, (d["sq"] - d["n"] * d["avg"] ** 2) / (d["n"] - 1))) if d["n"] > 1 else 0.0
    return out


def wilson(p: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    ph = p / n
    c = (ph + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), min(1.0, c + h)


def tested_keys(tsec: int, cpus: str, min_n: int = 1, pin: str = "", opt: str | None = None) -> set[str]:
    return {k for k, d in pooled(load(), tsec, cpus, pin, opt).items() if d["n"] >= min_n}


def report(tsec: int, cpus: str | None, min_n: int, control_key: str | None, pin: str | None = None,
           opt: str | None = None, sort: str = "min") -> str:
    rows = load()
    P = pooled(rows, tsec, cpus, pin, opt)
    if not P:
        return f"DB 에 -t{tsec}" + (f" cpus={cpus}" if cpus else "") + " 결과 없음"
    L = [f"누적 결과 (-t{tsec}" + (f", cpus={cpus}" if cpus else ", cpus 전체")
         + (f", pin={pin or '없음'}" if pin is not None else ", pin 전체")
         + (f", opt='{opt}'" if opt else ", opt 전체") + f")  {DB_PATH}",
         f"  {'key(PA-SOn-SOff-ShOn-ShOff-POn-POff)':<38}{'avg':>8}{'±95%':>7}{'sd':>7}{'min':>6}{'pass':>10}  이름 / 캠페인수"]
    base = P.get(control_key) if control_key else None
    for k, d in sorted(P.items(), key=lambda kv: (-kv[1][sort], -kv[1]["avg"])):
        if d["n"] < min_n:
            continue
        ci = 1.96 * d["sd"] / math.sqrt(d["n"]) if d["n"] > 1 else 0
        mark = "  ← 기준" if k == control_key else ""
        L.append(f"  {k:<38}{d['avg']:>8.1f}{ci:>7.1f}{d['sd']:>7.1f}{d['min']:>6.0f}{d['pass']:>5}/{d['n']:<4}"
                 f"  {','.join(sorted(d['names']))[:28]} /{len(d['campaigns'])}{mark}")
    if base:
        L.append(f"  기준 {control_key}: avg {base['avg']:.1f}  n={base['n']}")
    return "\n".join(L)


# ---- 예전 캠페인 가져오기 ----------------------------------------------
def import_campaign(d: str) -> int:
    sys.path.insert(0, CLI_DIR)
    from sweep_campaign import find_preset, load_preset, parse_settings, setting_names  # noqa: E402
    stp = os.path.join(d, "state.json")
    if not os.path.exists(stp):
        return 0
    with open(stp, encoding="utf-8") as f:
        st = json.load(f)
    camp = os.path.basename(os.path.abspath(d))
    if any(r["campaign"] == camp for r in load()):
        print(f"  {camp}: 이미 DB 에 있음 - 건너뜀")
        return 0
    v = st["vars"]
    sets = st.get("sets")
    if not sets:
        b = parse_settings(load_preset(find_preset(st.get("base_preset", st["preset"])))[1])
        sets = b
    out = []
    for run in st.get("runs", []):
        c = run.get("csv")
        if not c:
            continue
        c = os.path.join(d, os.path.basename(c))
        if not os.path.exists(c):
            continue
        credited = {(ph, n) for ph in ("1", "2") for n, t in st["done"][ph].items() if t.get("run") == run["idx"]}
        with open(c, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r["phase"], r["setting"]) not in credited or r["setting"] not in sets:
                    continue
                opt = v.get("opt1") if r["phase"] == "1" else v.get("opt2")
                out.append({"ts": st.get("started", ""), "campaign": camp, "run": run["idx"],
                            "mode": st.get("mode", "2stage"), "phase": r["phase"], "tsec": tsec_of(opt),
                            "cpus": v.get("cpus", ""), "key": key_of(sets[r["setting"]]), "name": r["setting"],
                            "iperf_run": r["run"], "a": r["a"], "b": r["b"], "total": r["total"],
                            "mark": r["mark"], "cpu": r.get("cpu", ""), "pin": "", "opt": opt or ""})
    append(out)
    print(f"  {camp}: {len(out)}행 추가")
    return len(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--t", type=int, default=10, help="iperf -t 초 (기본 10 = 집중 테스트)")
    r.add_argument("--cpus", help="cpus 조건 (기본: 전체)")
    r.add_argument("--min-n", type=int, default=1)
    r.add_argument("--control", default="496-240-204-190-154-112-100", help="기준 key (기본 def)")
    r.add_argument("--pin", help='코어 지정 조건 ("1,3", 지정 안 한 것은 "")')
    r.add_argument("--opt", help='iperf 옵션 조건 (예: "-t10 -i1 -w1M")')
    r.add_argument("--sort", choices=["min", "avg"], default="min", help="정렬 기준 (기본 min)")
    i = sub.add_parser("import")
    i.add_argument("dirs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(report(a.t, a.cpus, a.min_n, a.control, a.pin, a.opt, a.sort))
    else:
        for d in a.dirs:
            for x in sorted(glob.glob(d)):
                import_campaign(x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
