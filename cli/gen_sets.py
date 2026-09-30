#!/usr/bin/env python3
"""
세팅 목록 파일 생성기 (sweep_campaign.py --sets 용).

값 지정: "시작:끝:간격" 또는 "a,b,c" 또는 숫자 하나
  --pa      PauseAll
  --son     Sys-On          --sgap   Sys-On - Sys-Off       (Off = On - gap)
  --shon    Shared-On       --shgap  Shared-On - Shared-Off
  --pton    Port-On         --ptgap  Port-On - Port-Off
지정 안 한 축은 --center 세팅 값 그대로.

방식
  --how grid     : 모든 조합 (개수가 --max 넘으면 거부 → --how random 권장)
  --how random   : 모든 조합 중 --n 개를 무작위 (--seed 로 재현)
  --how ofat     : --center 에서 한 축씩만 바꾼 것 (축마다 지정한 값들)

제약 (기본)
  - 모든 값 0~511  (PauseAll=512 는 09-30 캠페인에서 5/5 멈춤. 512 이상 허용하려면 --max-val 1023)
  - Off < On (gap > 0)

예)
  # def 기준 한 축씩
  python cli/gen_sets.py --center def --how ofat --pa 470:511:10 --son 240:480:40 --sgap 20:120:20 \\
        --shon 190:460:30 --pton 112:320:40 -o scripts/sets/ofat_def.txt
  # 넓게 무작위 80개
  python cli/gen_sets.py --center ss440 --how random --n 80 --pa 470:511:8 --son 380:500:20 \\
        --sgap 20:100:20 --shon 360:480:20 --shgap 20:100:20 -o scripts/sets/rand80.txt
"""
from __future__ import annotations

import argparse
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep_campaign import find_preset, load_preset, parse_settings  # noqa: E402

AXES = ["pa", "son", "sgap", "shon", "shgap", "pton", "ptgap"]


def parse_vals(spec: str) -> list[int]:
    if ":" in spec:
        a, b, st = (int(x) for x in spec.split(":"))
        return list(range(a, b + 1, st))
    return [int(x) for x in spec.split(",") if x.strip()]


def to_regs(v: dict) -> list[int]:
    return [v["pa"], v["son"], v["son"] - v["sgap"], v["shon"], v["shon"] - v["shgap"],
            v["pton"], v["pton"] - v["ptgap"]]


def from_regs(r: list[int]) -> dict:
    return {"pa": r[0], "son": r[1], "sgap": r[1] - r[2], "shon": r[3], "shgap": r[3] - r[4],
            "pton": r[5], "ptgap": r[5] - r[6]}


def ok(regs: list[int], maxv: int) -> bool:
    return all(0 <= x <= maxv for x in regs) and regs[2] < regs[1] and regs[4] < regs[3] and regs[6] < regs[5]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default="fc-sweep-2stage")
    ap.add_argument("--center", default="def", help="기준 세팅 이름 (기본 def)")
    ap.add_argument("--how", choices=["grid", "random", "ofat"], default="ofat")
    ap.add_argument("--n", type=int, default=50, help="random 개수")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max", type=int, default=300, help="grid 최대 개수")
    ap.add_argument("--max-val", type=int, default=511)
    ap.add_argument("--prefix", default="x")
    ap.add_argument("-o", "--out", required=True)
    for a in AXES:
        ap.add_argument(f"--{a}")
    args = ap.parse_args(argv)

    base = parse_settings(load_preset(find_preset(args.preset))[1])
    if args.center not in base:
        print(f"--center '{args.center}' 세팅 없음", file=sys.stderr)
        return 2
    c = from_regs(base[args.center])
    axes = {a: parse_vals(getattr(args, a)) for a in AXES if getattr(args, a)}
    if not axes:
        print("바꿀 축을 하나 이상 지정하세요 (--pa --son --sgap --shon --shgap --pton --ptgap)", file=sys.stderr)
        return 2

    combos: list[dict] = []
    if args.how == "ofat":
        for a, vals in axes.items():
            for x in vals:
                if x != c[a]:
                    combos.append({**c, a: x})
    else:
        keys = list(axes)
        allc = [dict(c, **dict(zip(keys, t))) for t in itertools.product(*(axes[k] for k in keys))]
        allc = [v for v in allc if ok(to_regs(v), args.max_val)]
        if args.how == "grid":
            if len(allc) > args.max:
                print(f"조합 {len(allc)}개 > --max {args.max}. 간격을 넓히거나 --how random --n N", file=sys.stderr)
                return 2
            combos = allc
        else:
            random.seed(args.seed)
            combos = random.sample(allc, min(args.n, len(allc)))

    seen, rows, skipped = set(), [], 0
    for v in combos:
        r = to_regs(v)
        if not ok(r, args.max_val) or tuple(r) in seen or r == base[args.center]:
            skipped += 1
            continue
        seen.add(tuple(r))
        rows.append(r)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"# gen_sets.py --how {args.how} --center {args.center} "
                + " ".join(f"--{a} {getattr(args, a)}" for a in AXES if getattr(args, a)) + "\n")
        f.write("# 이름       PauseAll SysOn SysOff ShOn ShOff PtOn PtOff\n")
        for i, r in enumerate(rows, 1):
            f.write(f"{args.prefix}{i:03d}".ljust(11) + " ".join(f"{x:>4}" for x in r) + "\n")
    print(f"{len(rows)}개 → {args.out}" + (f"  (제약 위반/중복 {skipped}개 제외)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
