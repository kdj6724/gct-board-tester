#!/usr/bin/env python3
"""
트레이닝 dump(sdram/dump/trn_dump_a.patch 출력) 로그 → 표 정리.

입력: 전체 콘솔 로그(.log) 하나 이상 또는 캠페인 폴더. 로그 안의
    ==== omc_cfg.train_result[0][3] ====
      rd_deskew_center_cs0[0..7]  = 0x.. (8개)
      lock_value_init             = 0x112
  형식을 읽는다. 'omc_cfg.prbs_result[0]' 제목이 나올 때마다 새 부팅으로 센다.

출력 (첫 번째 인자 폴더 또는 --out 경로에):
  trn_dump.csv     : boot, cfg, struct, field, idx, value(hex)  - 모든 값
  trn_summary.txt  : 구조체·필드별로 부팅마다 값을 한 줄씩 (값이 부팅마다 달라지는지 보기 쉽게)
값의 의미(16비트 2개로 나뉘는지 등)는 해석하지 않고 저장된 값 그대로 둔다.

    python3 cli/trn_parse.py logs/sdram_..._board-B2
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys

HDR = re.compile(r"==== (\S+?)\.(prbs_result\[\d+\]|train_result\[\d+\]\[\d+\]) ====")
FLD = re.compile(r"^\s{2}(\w+)(\[0\.\.7\])?\s*=\s*(.*)$")
PRB = re.compile(r"(offset\w+)=(0x[0-9a-fA-F]+)")
TS = re.compile(r"^\[[0-9:.]+\]\s?")


def parse(paths):
    boots = []          # [{(cfg, struct): {field: [values]}}]
    cur = None
    key = None
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = TS.sub("", raw.rstrip("\r\n"))
                m = HDR.search(line)
                if m:
                    cfg, st = m.group(1), m.group(2)
                    if cfg == "omc_cfg" and st.startswith("prbs_result"):
                        cur = {}
                        boots.append({"file": os.path.basename(p), "data": cur})
                    if cur is None:
                        continue
                    key = (cfg, st)
                    cur.setdefault(key, {})
                    continue
                if cur is None or key is None:
                    continue
                if key[1].startswith("prbs"):
                    for name, v in PRB.findall(line):
                        cur[key][name] = [v]
                    continue
                m = FLD.match(line)
                if m:
                    cur[key][m.group(1)] = m.group(3).split()
                elif line.strip() and not line.startswith("  "):
                    key = None          # dump 블록 끝
    return boots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="캠페인 폴더 또는 .log 파일")
    ap.add_argument("--out", help="결과를 쓸 폴더 (기본: 첫 번째 입력 폴더)")
    a = ap.parse_args()
    paths = []
    for x in a.inputs:
        if os.path.isdir(x):
            paths += sorted(p for p in glob.glob(os.path.join(x, "**", "*.log"), recursive=True)
                            if not p.endswith("_FAIL.log") and os.path.basename(p) != "campaign.log")
        else:
            paths.append(x)
    boots = parse(paths)
    if not boots:
        sys.exit("dump 출력을 못 찾음")
    out = a.out or (a.inputs[0] if os.path.isdir(a.inputs[0]) else os.path.dirname(a.inputs[0]))
    with open(os.path.join(out, "trn_dump.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["boot", "file", "cfg", "struct", "field", "idx", "value"])
        for i, b in enumerate(boots, 1):
            for (cfg, st), fields in b["data"].items():
                for name, vals in fields.items():
                    for j, v in enumerate(vals):
                        w.writerow([i, b["file"], cfg, st, name, j if len(vals) > 1 else "", v])
    # 요약: 구조체/필드별로 부팅마다 한 줄
    keys = []
    for b in boots:
        for k, fields in b["data"].items():
            for name in fields:
                if (k, name) not in keys:
                    keys.append((k, name))
    lines = [f"부팅 {len(boots)}회 dump (파일: {', '.join(sorted({b['file'] for b in boots}))})", ""]
    last = None
    for (cfg, st), name in keys:
        if (cfg, st) != last:
            lines.append(f"==== {cfg}.{st} ====")
            last = (cfg, st)
        vals = [" ".join(b["data"].get((cfg, st), {}).get(name, ["-"])) for b in boots]
        same = len(set(vals)) == 1
        lines.append(f"  {name}{'  (모든 부팅 같음)' if same else ''}")
        for i, v in enumerate(vals, 1):
            if same and i > 1:
                break
            lines.append(f"    #{i:<3} {v}")
    with open(os.path.join(out, "trn_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"부팅 {len(boots)}회 → {os.path.join(out, 'trn_dump.csv')}, trn_summary.txt")


if __name__ == "__main__":
    main()
