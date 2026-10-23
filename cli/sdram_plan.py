#!/usr/bin/env python3
"""
SDRAM 장기 시험 계획 (10/08 저녁, 3일 무인용). 속도는 SBL 기본 3732.

  1단계 선별   지금까지 가장 나은 VDDQ 0.725 에 데이터시트 재점검으로 찾은 값을 하나씩 얹어 각 3번
               (SCREEN 목록: 대조군, CA ODT 3~6, 리프레시 2배, PU-CAL VDDQ*0.6, 온도 감지, VDD2 1.125/1.100,
                지금까지 1등 ODT 조합 + CA ODT)
  2단계 조합   1단계 평균 상위 3개의 설정을 둘씩/셋 다 합쳐서 각 3번 (데이터시트상 같이 못 쓰는 조합은 건너뜀)
  3단계 탐색   평균 1등 캠페인부터 주변을 한 칸씩 (캠페인당 최대 30 후보), --until 까지 반복. VDDQ 0.700 은 안 감

어느 단계든 200번 통과가 나오면(같은 설정 재확인까지) 전체를 멈춘다.
결과 없이 끝나는 캠페인이 2번 연속이면(UART/Tapo/빌드 문제) 전체를 멈춘다.
멈추기: touch logs/STOP_ALL  (지금 후보까지 하고 멈춤) / Ctrl-C
각 캠페인이 끝날 때마다 보드 전원 OFF, SBL 소스 원본 복원 (sdram_campaign.py 가 함).
진행 기록: logs/plan_<시각>.log, 캠페인마다 ~/gbt/logs 로 복사 (노트북 공유가 죽어 있으면 건너뜀).

  cd ~/gbt-local && ~/.venv-gbt/bin/python cli/sdram_plan.py --until "2026-10-11 20:30"
"""
from __future__ import annotations

import argparse
import shlex
import datetime
import glob
import json
import os
import subprocess
import sys
import time

CLI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CLI)
LOGS = os.path.join(ROOT, "logs")
FILES = ["base/drivers/orbit_ddram/sphy_v9r8_functions.c", "base/drivers/orbit_ddram/mr_config.h", "base/sys/main.c",
         "base/drivers/orbit_ddram/test_lpddr4.c"]
# 1단계 선별: (이름, VDD2, seed)
SCREEN = [
    ("대조군 VDDQ0.725", "1.150", "vddq=725"),
    ("CA ODT 4 (60Ω)", "1.150", "vddq=725,caodt=4"),
    ("CA ODT 5 (48Ω)", "1.150", "vddq=725,caodt=5"),
    ("CA ODT 6 (40Ω)", "1.150", "vddq=725,caodt=6"),
    ("CA ODT 3 (80Ω)", "1.150", "vddq=725,caodt=3"),
    ("리프레시 2배", "1.150", "vddq=725,refx=2"),
    ("PU-CAL VDDQ*0.6", "1.150", "vddq=725,pucal=0"),
    ("온도 감지", "1.150", "vddq=725,tsense=1"),
    ("VDD2 1.125", "1.125", "vddq=725"),
    ("VDD2 1.100", "1.100", "vddq=725"),
    ("ODT0/5+current+CA ODT4", "1.150", "vddq=725,dsvref=0x20,pdds=1,adj=current,dqodt=0,socodt=5,phyodt=5,caodt=4"),
]
EXPLORE = "dsvref,pdds,adj,dqodt,vddq,caodt,refx,pucal,tsense"


def seed_valid(seed):
    sys.path.insert(0, CLI)
    import sdram_knobs as kn
    d = {}
    for part in filter(None, seed.split(",")):
        k, _, v = part.partition("=")
        d[k] = v if k == "adj" else int(v, 0)
    return kn.valid(d)


def merge(*seeds):
    """'k=v,...' 여러 개를 합침 (뒤가 우선). 같은 손잡이 값이 서로 다르면 None."""
    out = {}
    for sd in seeds:
        for part in filter(None, sd.split(",")):
            k, _, v = part.partition("=")
            if k in out and out[k] != v and k != "vddq":
                return None
            out[k] = v
    return ",".join(f"{k}={v}" for k, v in out.items())


class Plan:
    def __init__(self, a):
        self.a = a
        self.stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(LOGS, exist_ok=True)
        self.logf = os.path.join(LOGS, f"plan_{self.stamp}.log")
        self.done = []        # [{"stage","dir","vdd2","seed","rc"}]
        self.empty_streak = 0  # 결과 없이 끝난 캠페인 연속 수 (UART/Tapo/빌드 문제)

    def log(self, msg):
        line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(f"### plan: {msg}", flush=True)
        with open(self.logf, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def stop_reason(self):
        if os.path.exists(os.path.join(LOGS, "STOP_ALL")):
            return "logs/STOP_ALL 있음"
        if datetime.datetime.now() >= self.a.until:
            return f"--until {self.a.until:%Y-%m-%d %H:%M} 지남"
        return None

    def campaign(self, stage, args, vdd2=None, seed=None):
        """sdram_campaign.py 실행. 새로 생긴 캠페인 폴더를 기록. 통과(rc 0)면 True."""
        before = set(glob.glob(os.path.join(LOGS, "sdram_*")))
        rd = next((args[i + 1] for i, x in enumerate(args) if x == "--resume"), None)
        self._n_before = len(self.results(rd)) if rd else 0
        cmd = [sys.executable, os.path.join(CLI, "sdram_campaign.py"),
               "--port", self.a.port, "--baud", str(self.a.baud), "--confirm", "1",
               "--until", self.a.until.strftime("%Y-%m-%d %H:%M")] + args + shlex.split(self.a.extra or "")
        self.log(f"[{stage}] 시작: {' '.join(args)}")
        try:
            rc = subprocess.call(cmd, cwd=ROOT)
        except KeyboardInterrupt:
            rc = 130
        new = sorted(set(glob.glob(os.path.join(LOGS, "sdram_*"))) - before, key=os.path.getmtime)
        d = new[-1] if new else next((args[i + 1] for i, x in enumerate(args) if x == "--resume"), "")
        self.done.append({"stage": stage, "dir": os.path.abspath(d) if d else "", "vdd2": vdd2, "seed": seed, "rc": rc})
        self.log(f"[{stage}] 끝 rc={rc} 폴더={os.path.basename(d)}  {self.summary(d)}")
        self.sync()
        if rc == 130:
            raise KeyboardInterrupt
        # 이번 실행에서 결과가 하나도 안 쌓였으면 장비 문제(UART/Tapo/빌드)일 가능성 → 2번 연속이면 전체 멈춤
        n_now = len(self.results(d)) if d else 0
        if n_now == self._n_before:
            self.empty_streak += 1
            if self.empty_streak >= 2:
                raise RuntimeError("결과 없이 끝난 캠페인 2번 연속 - UART/Tapo/빌드 확인 필요")
        else:
            self.empty_streak = 0
        return rc == 0

    @staticmethod
    def results(d):
        try:
            with open(os.path.join(d, "state.json"), encoding="utf-8") as f:
                st = json.load(f)
        except OSError:
            return []
        return [r for r in st.get("done", {}).values() if r.get("score") is not None]

    def summary(self, d):
        rs = self.results(d) if d else []
        sc = [r["score"] for r in rs]
        return f"결과 {sc} 평균 {sum(sc) / len(sc):.1f}" if sc else "결과 없음"

    def mean(self, d):
        sc = [r["score"] for r in self.results(d)]
        return sum(sc) / len(sc) if sc else -1

    def sync(self):
        """~/gbt/logs (노트북 공유) 로 복사. 노트북이 꺼져 공유가 죽어 있으면 파일 접근 자체가 멈출 수 있어서
        경로 확인까지 전부 별도 프로세스에서 하고, 2분 안에 안 끝나면 버리고 시험을 계속한다."""
        dst = os.path.expanduser("~/gbt/logs")
        if dst.rstrip("/") == LOGS.rstrip("/") or self.a.no_sync:
            return
        p = subprocess.Popen(["sh", "-c", f'test -d "{dst}" && rsync -a "{LOGS}/" "{dst}/"'])
        try:
            p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()             # 공유가 멈춰 있으면 kill 도 바로 안 될 수 있음 - 기다리지 않고 진행
            self.log("~/gbt/logs 복사가 2분 안에 안 끝남 (노트북 공유 끊김?) - 건너뛰고 계속")

    # ---- 단계 -------------------------------------------------------------
    def new_campaign(self, stage, vdd2, seed):
        return self.campaign(stage, ["--reboots", "200", "--variants", "sdram/none", "--files", *FILES,
                                     "--build-args", f"DDRVDD2={vdd2}", "--seed", seed,
                                     "--explore", "none", "--repeat", "3"], vdd2, seed)

    def stage1(self):
        for name, v2, seed in SCREEN:
            if (r := self.stop_reason()):
                return r
            if self.new_campaign(f"1 선별 {name}", v2, seed):
                return "통과"
        return None

    def stage2(self):
        s1 = [x for x in self.done if x["stage"].startswith("1 ") and x["dir"]]
        ranked = sorted(s1, key=lambda x: -self.mean(x["dir"]))
        # 서로 합칠 수 있는 것끼리 평균 순으로 최대 3개 (예: CA ODT 4/5/6 처럼 같은 손잡이끼리는 못 합침)
        top = []
        for x in ranked:
            sd = merge(*(y["seed"] for y in top), x["seed"])
            if sd and seed_valid(sd):
                top.append(x)
            if len(top) == 3:
                break
        self.log("[2 조합] 1단계 상위: " + ", ".join(f"{x['stage'][5:]} (평균 {self.mean(x['dir']):.1f})" for x in top))
        combos = [(top[a], top[b]) for a in range(len(top)) for b in range(a + 1, len(top))]
        if len(top) == 3:
            combos.append(tuple(top))
        for grp in combos:
            if (r := self.stop_reason()):
                return r
            seed = merge(*(x["seed"] for x in grp))
            if seed and not seed_valid(seed):
                self.log(f"[2 조합] 데이터시트상 같이 못 쓰는 조합이라 건너뜀: {seed}")
                continue
            if not seed:
                self.log(f"[2 조합] 같은 손잡이 값이 달라 못 합침: {[x['seed'] for x in grp]}")
                continue
            v2 = grp[0]["vdd2"]            # VDD2 가 다르면 평균이 더 높은 쪽 (top 은 평균 순)
            if self.new_campaign("2 조합 " + " + ".join(x["stage"][5:] for x in grp), v2, seed):
                return "통과"
        return None

    def stage3(self):
        tried = set()
        while not (r := self.stop_reason()):
            cands = [x for x in self.done if x["dir"] and x["dir"] not in tried and self.mean(x["dir"]) >= 0]
            if not cands:
                return "탐색할 캠페인 없음"
            best = max(cands, key=lambda x: self.mean(x["dir"]))
            tried.add(best["dir"])
            if self.campaign("3 탐색", ["--resume", best["dir"], "--explore", EXPLORE,
                                        "--skip", "vddq=700", "--max-new", "30"], best["vdd2"], best["seed"]):
                return "통과"
        return r

    def run(self):
        self.log(f"계획 시작 - --until {self.a.until:%Y-%m-%d %H:%M}, 기록 {self.logf}")
        why = None
        try:
            for st in (self.stage1, self.stage2, self.stage3):
                why = st()
                if why:
                    break
        except KeyboardInterrupt:
            why = "Ctrl-C"
        except RuntimeError as e:
            why = str(e)
        self.log(f"계획 끝: {why or '모든 단계 완료'}")
        uniq = {x["dir"]: x for x in self.done if x["dir"]}      # 같은 폴더(3단계 이어하기)는 한 번만
        ranked = sorted(uniq.values(), key=lambda x: -self.mean(x["dir"]))
        for x in ranked[:10]:
            self.log(f"  평균 {self.mean(x['dir']):6.1f}  VDD2 {x['vdd2']}  {x['seed']}  {os.path.basename(x['dir'])}")
        self.sync()
        return 0 if why == "통과" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", required=True, type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M"),
                    help="이 시각이 지나면 새 후보를 시작하지 않고 끝냄 (예: '2026-10-11 17:00')")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--no-sync", action="store_true", help="캠페인마다 ~/gbt/logs 로 복사하지 않음")
    ap.add_argument("--extra", help=argparse.SUPPRESS)   # 시험용: 모든 캠페인에 덧붙일 인자
    a = ap.parse_args(argv)
    sys.exit(Plan(a).run())


if __name__ == "__main__":
    main()
