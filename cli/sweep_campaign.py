#!/usr/bin/env python3
"""
fc-sweep-2stage 를 "멈추면 전원 껐다 켜고 이어서" 끝까지 돌리는 캠페인 러너.

동작
  1. cli/run.py <프리셋> 을 서브프로세스로 실행 (프리셋이 전원 재인가 → 부팅 → 스크립트 실행).
  2. 아래 중 하나면 그 회차를 끝내고 다시 1번 (= 전원 재인가 후 이어하기):
       - 보드 스크립트가 "==> TRAFFIC_DEAD <세팅>" 출력 (iperf DEAD_N 회 연속 실패)
       - 테스트 중 출력이 iperf -t × 2 초(최소 10초) 동안 한 줄도 없음 (-t3→10s, -t10→20s, -t20→40s)
         (부팅/업로드 단계는 --idle-boot, 기본 600초)
       - 그 밖의 실패 (부팅 실패, 링크업 실패 등)
  3. 이어할 때는 이미 끝난 세팅을 SKIP1/SKIP2 로 넘겨서 건너뛴다.
     PHASE 1 이 다 끝나면 PHASE 1 결과 avg 상위 TOPN 을 TOPLIST 로 넘겨 PHASE 2 만 돌린다.
  4. 같은 세팅에서 --max-dead(기본 1)번 죽으면 그 세팅은 DEAD 로 기록하고 건너뛴다(무한 반복 방지).

결과 (logs/campaign_<시각>/)
  campaign.log   : 회차별 시작/종료/재부팅 사유 기록
  state.json     : 진행 상태 (중간에 끊겨도 --resume 으로 이어감)
  summary.txt/json, merged.csv : 최종 결과 (재부팅 횟수, DEAD 세팅, PHASE 별 순위)
  run<N>_*.log/json/csv       : 회차별 run.py 결과

사용 (테스트 PC):
  cd ~/gbt && ~/.venv-gbt/bin/python cli/sweep_campaign.py --var setlist=sweep2 --var runs1=10
  이어가기: ... cli/sweep_campaign.py --resume logs/campaign_20260930_140000
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

CLI_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CLI_DIR)
RUN_PY = os.path.join(CLI_DIR, "run.py")
_SWEEP2_EXCLUDE = re.compile(r"^L(5[6-9]0|[6-9][0-9]0|1000)p")


# ─────────────────────────────────────────────
# 프리셋에서 세팅 목록/기본 변수 읽기
# ─────────────────────────────────────────────
def find_preset(name: str) -> str:
    if os.path.isfile(name):
        return name
    for d in ("profiles", "presets"):
        p = os.path.join(ROOT, d, name + ".json")
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(name)


def load_preset(path: str) -> tuple[dict, str]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    script = ""
    for b in d.get("blocks", []):
        if b.get("type") == "UPLOAD_SCRIPT":
            script = b["params"].get("script", "")
    return d.get("vars", {}), script


def setting_names(script: str, setlist: str) -> list[str]:
    m = re.search(r'^SETTINGS="\n(.*?)^"', script, re.S | re.M)
    if not m:
        raise ValueError("프리셋 스크립트에서 SETTINGS 목록을 못 찾음")
    names = [ln.split("|")[0].strip() for ln in m.group(1).splitlines() if "|" in ln]
    if setlist == "sweep2":
        names = [n for n in names if not _SWEEP2_EXCLUDE.match(n)]
    return names


def parse_settings(script: str) -> dict:
    """스크립트 SETTINGS 블록 -> {name: [pa, son, soff, shon, shoff, pton, ptoff]} (순서 유지)."""
    m = re.search(r'^SETTINGS="\n(.*?)^"', script, re.S | re.M)
    out = {}
    for ln in (m.group(1) if m else "").splitlines():
        if "|" in ln:
            f = [x.strip() for x in ln.split("|")]
            out[f[0]] = [int(x) for x in f[1:8]]
    return out


def read_sets_file(path: str, base: dict) -> dict:
    """세팅 파일 읽기. 한 줄에 하나:
         이름  PauseAll SysOn SysOff ShOn ShOff PtOn PtOff     (공백 또는 | 구분)
         이름                                                   (기존 세팅 이름만 쓰면 그 값 그대로)
       # 뒤는 주석."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            ln = raw.split("#", 1)[0].replace("|", " ").split()
            if not ln:
                continue
            name = ln[0]
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                raise ValueError(f"{path}:{n} 이름은 영문/숫자/_ 만: {name}")
            if len(ln) == 1:
                if name not in base:
                    raise ValueError(f"{path}:{n} '{name}' 은 기존 세팅에 없음 - 값 7개를 적어야 함")
                vals = base[name]
            elif len(ln) == 8:
                vals = [int(x) for x in ln[1:]]
            else:
                raise ValueError(f"{path}:{n} 값이 7개가 아님: {raw.strip()}")
            if any(v < 0 or v > 1023 for v in vals):
                raise ValueError(f"{path}:{n} 값은 0~1023: {raw.strip()}")
            if name in out:
                raise ValueError(f"{path}:{n} 이름 중복: {name}")
            out[name] = vals
    return out


def with_controls(sets: dict, control: str, every: int, base: dict) -> dict:
    """control 세팅을 맨 앞과 every 개마다 끼워 넣음 (이름 def_c1, def_c2 ...)."""
    if not control:
        return sets
    cv = sets.get(control) or base.get(control)
    if cv is None:
        raise ValueError(f"--control '{control}' 세팅을 찾을 수 없음")
    out, k = {}, 0
    items = [(n, v) for n, v in sets.items() if n != control]
    for i, (n, v) in enumerate(items):
        if i % every == 0:
            k += 1
            out[f"{control}_c{k}"] = cv
        out[n] = v
    k += 1
    out[f"{control}_c{k}"] = cv
    return out


def build_preset(src: str, sets: dict, dst: str):
    """프리셋 복사본을 만들고 업로드 스크립트의 SETTINGS 를 sets 로 바꾼 뒤 md5 갱신."""
    import hashlib
    with open(src, encoding="utf-8") as f:
        d = json.load(f)
    body = "\n".join(f"{n:<10}|" + "|".join(str(x) for x in v) for n, v in sets.items())
    for b in d["blocks"]:
        if b.get("type") == "UPLOAD_SCRIPT":
            sc = b["params"]["script"]
            sc2 = re.sub(r'^SETTINGS="\n.*?^"', 'SETTINGS="\n' + body.replace("\\", "\\\\") + '\n"', sc,
                         count=1, flags=re.S | re.M)
            b["params"]["script"] = sc2
            md5 = hashlib.md5(sc2.encode("utf-8")).hexdigest()
    for b in d["blocks"]:
        chk = (b.get("params") or {}).get("check") or {}
        if b.get("id") == "fs_verify" and chk:
            chk["pattern"] = md5
    d["name"] = os.path.splitext(os.path.basename(dst))[0]
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def fisher_greater(a: int, b: int, c: int, d: int) -> float:
    """2x2 [[a,b],[c,d]] 에서 첫 행의 성공 비율이 더 크다는 단측 Fisher 정확검정 p값."""
    from math import comb
    n1, n2, k = a + b, c + d, a + c
    tot = comb(n1 + n2, k)
    return sum(comb(n1, x) * comb(n2, k - x) for x in range(a, min(n1, k) + 1)) / tot


_LIVE_RE = re.compile(r"run(?P<run>\d+) P(?P<ph>[12]) (?P<name>\S+)\s+==> pass (?P<p>\d+)/(?P<n>\d+) \((?P<r>\d+)%\)\s+"
                      r"avg=(?P<avg>[\d.]+) med=(?P<med>[\d.]+) p10=(?P<p10>[\d.]+) min=(?P<min>[\d.]+) "
                      r"max=(?P<max>[\d.]+) sd=(?P<sd>[\d.]+)")


def read_live(path: str) -> dict:
    """live.txt -> {"1": {name: stats}, "2": {...}} (같은 세팅이 여러 번이면 마지막 것)."""
    out = {"1": {}, "2": {}}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = _LIVE_RE.search(ln)
            if m and int(m.group("n")) > 0:
                out[m.group("ph")][m.group("name")] = {
                    "n": int(m.group("n")), "pass": int(m.group("p")), "rate": int(m.group("r")),
                    "avg": float(m.group("avg")), "median": float(m.group("med")), "p10": float(m.group("p10")),
                    "min": float(m.group("min")), "max": float(m.group("max")), "sd": float(m.group("sd")),
                    "run": int(m.group("run")), "live": True}
    return out


def _db():
    sys.path.insert(0, CLI_DIR)
    import fc_db
    return fc_db


# ─────────────────────────────────────────────
class Campaign:
    def __init__(self, args):
        self.args = args
        if args.resume:
            self.dir = os.path.abspath(args.resume)
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                self.st = json.load(f)
            self.preset = self.st["preset"]
            self.vars = self.st["vars"]
        else:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.dir = os.path.join(ROOT, "logs", f"campaign_{stamp}")
            os.makedirs(self.dir, exist_ok=True)
            self.preset = args.preset
            pvars, _ = load_preset(find_preset(args.preset))
            self.vars = {k: str(v) for k, v in pvars.items()}
            for item in args.var or []:
                k, v = item.split("=", 1)
                self.vars[k] = v
            base_preset = find_preset(args.preset)
            base = parse_settings(load_preset(base_preset)[1])
            if args.sets:
                sets = read_sets_file(args.sets, base)
            else:
                names = setting_names(load_preset(base_preset)[1], self.vars.get("setlist", "all"))
                sets = {n: base[n] for n in names}
            self.preset = os.path.join(self.dir, "preset.json")
            self.vars["setlist"] = "all"
            user = {x.split("=", 1)[0] for x in (args.var or [])}
            if args.mode == "explore":                 # 넓게: -t3 x 10 (따로 안 줬으면)
                self.vars.setdefault("opt1", "-t3 -i1 -w4M")
                if "runs1" not in user:
                    self.vars["runs1"] = "10"
            elif args.mode == "focus":                 # 집중: PHASE 1 자리에서 -t10 x 50 으로
                if "opt1" not in user:
                    self.vars["opt1"] = self.vars.get("opt2", "-t10 -i1 -w4M")
                if "runs1" not in user:
                    self.vars["runs1"] = self.vars.get("runs2", "50")
            sets = self.dedupe(sets, args)
            if not [n for n in sets if n != args.control]:
                self.clog("새로 돌릴 세팅이 없음 (전부 중복/측정됨) → 종료. 누적 결과: python cli/fc_db.py report")
                raise SystemExit(0)
            sets = with_controls(sets, args.control, args.control_every, base)
            self.clog(f"세팅 {len(sets)}개로 시작 ({args.sets or '기본 목록'}"
                      + (f", 대조군 {args.control} {args.control_every}개마다" if args.control else "") + ")")
            self.st = {"preset": self.preset, "base_preset": base_preset, "sets": sets,
                       "open": bool(args.open), "mode": args.mode, "ctl_count": sum(1 for n in sets if args.control
                                                              and n.startswith(args.control + "_c")),
                       "vars": self.vars,
                       "started": datetime.datetime.now().isoformat(timespec="seconds"),
                       "runs": [], "done": {"1": {}, "2": {}}, "dead_count": {},
                       "dead_skip": {"1": [], "2": []}, "toplist": [], "finished": False,
                       "control": args.control or ""}
        if "base_preset" not in self.st:           # 예전 버전으로 시작한 캠페인을 --resume
            self.st["base_preset"] = find_preset(self.st["preset"])
            sc = load_preset(self.st["base_preset"])[1]
            b0 = parse_settings(sc)
            self.st["sets"] = {n: b0[n] for n in setting_names(sc, self.vars.get("setlist", "all"))}
            self.preset = self.st["preset"] = os.path.join(self.dir, "preset.json")
            self.vars["setlist"] = "all"
        self.base = parse_settings(load_preset(self.st["base_preset"])[1])
        build_preset(self.st["base_preset"], self.st["sets"], self.preset)
        self.names = list(self.st["sets"])
        self.topn = int(self.vars.get("topn", "5"))
        self.inbox_mtime = 0.0

    # ---- 중복 제거 / 누적 DB ------------------------------------------------
    def dedupe(self, sets: dict, args, already: dict | None = None) -> dict:
        """같은 값 조합(이름만 다른 것)과, DB 에 이미 같은 조건으로 돌린 조합을 뺀다."""
        db = _db()
        tsec, cpus = db.tsec_of(self.vars.get("opt1", "")), self.vars.get("cpus", "")
        mode = getattr(args, "mode", "2stage")
        keys_now = {db.key_of(v) for v in (already or {}).values()}
        if getattr(args, "retest", False):
            done_keys = set()
        elif mode == "focus":
            done_keys = db.tested_keys(tsec, cpus, args.until) if args.until else set()
        else:
            done_keys = db.tested_keys(tsec, cpus, 1)
        out, dup, old = {}, [], []
        for n, v in sets.items():
            k = db.key_of(v)
            if k in keys_now:
                dup.append(n)
                continue
            if k in done_keys and n != args.control:
                old.append(n)
                continue
            keys_now.add(k)
            out[n] = v
        if dup:
            self.clog(f"값이 같은 세팅 {len(dup)}개 제외: {' '.join(dup)}")
        if old:
            why = (f"누적 {args.until}회 이상" if mode == "focus" else "이미 측정됨")
            self.clog(f"DB 에 같은 조건(-t{tsec}, cpus={cpus})으로 {why} {len(old)}개 제외: {' '.join(old)}"
                      "  (다시 돌리려면 --retest)")
        return out

    def db_append(self, idx: int, csv_path: str | None, new_done: list[str]):
        if not csv_path or not os.path.exists(csv_path) or not new_done:
            return
        db = _db()
        want = {tuple(x[1:].split(":", 1)) for x in new_done}      # "P1:name" -> ("1","name")
        rows = []
        with open(csv_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r["phase"], r["setting"]) not in want or r["setting"] not in self.st["sets"]:
                    continue
                opt = self.vars.get("opt1") if r["phase"] == "1" else self.vars.get("opt2")
                rows.append({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                             "campaign": os.path.basename(self.dir), "run": idx,
                             "mode": self.st.get("mode", "2stage"), "phase": r["phase"],
                             "tsec": db.tsec_of(opt), "cpus": self.vars.get("cpus", ""),
                             "key": db.key_of(self.st["sets"][r["setting"]]), "name": r["setting"],
                             "iperf_run": r["run"], "a": r["a"], "b": r["b"], "total": r["total"],
                             "mark": r["mark"], "cpu": r.get("cpu", "")})
        db.append(rows)

    # ---- 기록 --------------------------------------------------------
    def clog(self, msg: str):
        line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(f"\n### campaign: {msg}", flush=True)
        with open(os.path.join(self.dir, "campaign.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save(self):
        with open(os.path.join(self.dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=2)

    # ---- 실행 중 세팅 추가 (add.txt) / 제어 파일 (phase2.txt, stop) -------------
    def check_inbox(self) -> bool:
        """캠페인 폴더의 add.txt 에 새 세팅이 있으면 목록에 추가하고 프리셋을 다시 만든다."""
        p = os.path.join(self.dir, "add.txt")
        if not os.path.exists(p) or os.path.getmtime(p) == self.inbox_mtime:
            return False
        self.inbox_mtime = os.path.getmtime(p)
        try:
            ref = {**self.base, **self.st["sets"]}
            new = {n: v for n, v in read_sets_file(p, ref).items() if n not in self.st["sets"]}
            new = self.dedupe(new, self.args, already=self.st["sets"])
        except ValueError as e:
            self.clog(f"add.txt 읽기 실패 (무시): {e}")
            return False
        if not new:
            return False
        ctl = self.st.get("control")
        self.st["sets"].update(new)
        if ctl:                                   # 새 묶음 뒤에 대조군 한 번 더
            self.st["ctl_count"] = self.st.get("ctl_count", 0) + 1
            self.st["sets"][f"{ctl}_c{self.st['ctl_count']}"] = self.st["sets"].get(ctl) or self.base[ctl]
        self.names = list(self.st["sets"])
        build_preset(self.st["base_preset"], self.st["sets"], self.preset)
        self.save()
        self.clog(f"add.txt 에서 세팅 {len(new)}개 추가: {' '.join(new)}  (총 {len(self.names)}개)")
        return True

    def ctl_file(self, name: str):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    # ---- 다음 회차 계획 ----------------------------------------------
    def plan(self):
        """(phase_mode, extra_vars), "wait"(open 모드에서 추가 대기) 또는 None(끝)."""
        self.check_inbox()
        if self.ctl_file("stop") is not None:
            self.clog("stop 파일 발견 → 종료")
            return None
        done1, done2 = self.st["done"]["1"], self.st["done"]["2"]
        skip1 = list(done1) + self.st["dead_skip"]["1"]
        left1 = [n for n in self.names if n not in skip1]
        if left1:
            if not skip1 and not self.st.get("control") and not self.st.get("open") \
                    and self.st.get("mode", "2stage") == "2stage":
                # 처음부터 -> 한 번 부팅으로 PHASE 1,2 (보드가 TOP 선정)
                return "12", {"phases": "12", "skip1": "", "skip2": "", "toplist": ""}
            return "1", {"phases": "1", "skip1": " ".join(skip1), "skip2": "", "toplist": ""}
        if self.st.get("mode", "2stage") != "2stage":      # explore / focus : PHASE 1 만
            return "wait" if self.st.get("open") else None
        if not self.st["toplist"] and self.st.get("open"):
            p2 = self.ctl_file("phase2.txt")
            if p2 is None:
                return "wait"                       # 세팅 추가(add.txt) 또는 phase2.txt/stop 기다림
            want = [n for n in p2.replace(",", " ").split() if not n.startswith("#")]
            if want:
                bad = [n for n in want if n not in self.names]
                if bad:
                    self.clog(f"phase2.txt 에 없는 세팅 이름 {bad} → 무시하고 나머지로")
                self.st["toplist"] = [n for n in want if n in self.names]
                self.clog(f"PHASE 2 대상(phase2.txt 지정): {' '.join(self.st['toplist'])}")
        if not self.st["toplist"]:
            ctl = self.st.get("control")
            is_ctl = (lambda n: bool(ctl) and re.fullmatch(re.escape(ctl) + r"_c\d+", n) is not None)
            ranked = sorted((n for n in done1 if done1[n].get("n") and not is_ctl(n)),
                            key=lambda n: -done1[n]["avg"])
            self.st["toplist"] = ranked[: self.topn]
            if ctl and f"{ctl}_c1" in self.names:          # 대조군 1개는 PHASE 2 에도 같이
                self.st["toplist"].append(f"{ctl}_c1")
            self.clog(f"PHASE 1 완료 → PHASE 2 대상(avg 상위 {self.topn}): {' '.join(self.st['toplist'])}")
        skip2 = list(done2) + self.st["dead_skip"]["2"]
        left2 = [n for n in self.st["toplist"] if n not in skip2]
        if not left2:
            return None
        return "2", {"phases": "2", "skip1": "", "skip2": " ".join(skip2),
                     "toplist": " ".join(self.st["toplist"])}

    # ---- run.py 한 회차 ----------------------------------------------
    def run_once(self, idx: int, extra: dict) -> dict:
        jpath = os.path.join(self.dir, f"run{idx:02d}.json")
        v = dict(self.vars)
        v.update(extra)
        cmd = [sys.executable, "-u", RUN_PY, self.preset, "--out-dir", self.dir,
               "--json", jpath, "--no-color"]
        for k, val in v.items():
            cmd += ["--var", f"{k}={val}"]
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, encoding="utf-8", errors="replace")
        q: queue.Queue = queue.Queue()

        def reader():
            for line in proc.stdout:
                q.put(line)
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        last = time.time()
        live = open(os.path.join(self.dir, "live.txt"), "a", encoding="utf-8")
        cur, cur_ph = "", "1"
        testing = False          # 보드 스크립트 헤더가 나온 뒤부터 = 테스트 중
        tsec = None              # 지금 PHASE 의 iperf -t 초 (PHASE 헤더에서 읽음)
        hung = False
        sent_int = 0
        while True:
            try:
                line = q.get(timeout=1)
                if line is None:
                    break
                sys.stdout.write(line)
                sys.stdout.flush()
                last = time.time()
                # 스크립트가 실제로 찍은 헤더 (업로드 중 printf 줄엔 $NSET 그대로라 안 걸림)
                if not testing and re.search(r"settings\s+:\s+\d+", line):
                    testing = True
                m = re.search(r"#+ PHASE ([12]) : .*?\(.*?-t\s*(\d+)", line)
                if m:
                    cur_ph, tsec = m.group(1), int(m.group(2))
                m = re.search(r"\[\d+/\d+\] (\S+)\s+PauseAll=", line)
                if m:
                    cur = m.group(1)
                if re.search(r"==> (pass |TRAFFIC_DEAD)", line):
                    live.write(f"{datetime.datetime.now():%H:%M:%S} run{idx:02d} P{cur_ph} {cur:<11} {line.strip()}\n")
                    live.flush()
            except queue.Empty:
                pass
            idle = time.time() - last
            if not testing:
                limit = self.args.idle_boot
            elif self.args.idle:                       # 고정값을 줬으면 그걸로
                limit = self.args.idle
            else:                                       # iperf -t 의 배수 (최소 idle_min)
                limit = max(self.args.idle_mult * (tsec or 10), self.args.idle_min)
            if proc.poll() is not None and q.empty():
                break
            if idle > limit and sent_int == 0:
                hung = True
                self.clog(f"run{idx:02d}: {'테스트' if testing else '부팅/준비'} 중 {limit}초 동안 출력 없음 → 중단 요청")
                proc.send_signal(signal.SIGINT)
                sent_int, t_int = 1, time.time()
            elif sent_int == 1 and time.time() - t_int > 90:
                proc.send_signal(signal.SIGINT)   # 두 번째 Ctrl-C = 강제 종료
                sent_int, t_int = 2, time.time()
            elif sent_int == 2 and time.time() - t_int > 30:
                proc.kill()
                sent_int = 3
        rc = proc.wait()
        if hung:
            live.write(f"{datetime.datetime.now():%H:%M:%S} run{idx:02d} P{cur_ph} {cur:<11} ==> HANG (출력 없음 → 재부팅)\n")
        live.close()
        result = {}
        if os.path.exists(jpath):
            with open(jpath, encoding="utf-8") as f:
                result = json.load(f)
        return {"rc": rc, "hung": hung, "json": jpath, "result": result}

    # ---- 회차 결과 반영 ----------------------------------------------
    def absorb(self, idx: int, mode: str, out: dict) -> dict:
        res = out["result"]
        post = (res.get("post") or {}).get("fc_sweep4") or {}
        new_done, dead = [], []
        for ph in ("1", "2"):
            table = (post.get(f"phase{ph}") or {}).get("table") or {}
            for name, t in table.items():
                if t.get("board_check") == "ok" and name not in self.st["done"][ph]:
                    keep = {k: t.get(k) for k in ("n", "pass", "rate", "avg", "median", "p10",
                                                  "min", "max", "sd", "errors", "regs")}
                    keep["run"] = idx
                    self.st["done"][ph][name] = keep
                    new_done.append(f"P{ph}:{name}")
        for d in post.get("dead") or []:
            dead.append((str(d["phase"]), d["setting"], "TRAFFIC_DEAD"))
        if not dead and (out["hung"] or res.get("status") != "PASS"):
            # 멈춘 세팅 = 이번 회차에서 시작했지만 못 끝낸 마지막 세팅
            for ph in ("2", "1"):
                table = (post.get(f"phase{ph}") or {}).get("table") or {}
                inc = [n for n, t in table.items() if t.get("board_check") == "incomplete"]
                if inc:
                    dead.append((ph, inc[-1], "HANG" if out["hung"] else "FAIL"))
                    break
        # PHASE 1,2 를 한 번에 돈 회차면 보드가 고른 TOP 을 그대로 씀
        if mode == "12" and not self.st["toplist"] and post.get("top_board") \
                and all(n in self.st["done"]["1"] or n in self.st["dead_skip"]["1"] for n in self.names):
            self.st["toplist"] = list(post["top_board"])
        for ph, name, why in dead:
            key = f"{ph}:{name}"
            self.st["dead_count"][key] = self.st["dead_count"].get(key, 0) + 1
            if self.st["dead_count"][key] >= self.args.max_dead and name not in self.st["dead_skip"][ph]:
                self.st["dead_skip"][ph].append(name)
                self.clog(f"PHASE {ph} {name}: {self.st['dead_count'][key]}번 멈춤 → DEAD 로 건너뜀")
        return {"new_done": new_done, "dead": [f"P{p}:{n}({w})" for p, n, w in dead],
                "status": res.get("status", f"no-json(rc={out['rc']})"), "log": res.get("log")}

    # ---- 결과 파일 ---------------------------------------------------
    def candidates(self, done1: dict, done2: dict) -> list[str]:
        """기준(대조군 합계 > def > 전체 합계) 대비 빠른 회차 비율로 유망/제외 후보를 뽑는다."""
        ctl = self.st.get("control")
        is_ctl = (lambda n: bool(ctl) and re.fullmatch(re.escape(ctl) + r"_c\d+", n) is not None)
        L = ["", "[살펴볼 후보]  기준 대비 '빠른 회차(pass) 비율' 단측 Fisher 검정 (p 작을수록 우연 아닐 가능성 큼)"]
        for ph, d in (("1", done1), ("2", done2)):
            meas = {n: t for n, t in d.items() if t.get("n")}
            if not meas:
                continue
            ctls = [t for n, t in meas.items() if is_ctl(n)]
            if ctls:
                bp, bn, bname = sum(t["pass"] for t in ctls), sum(t["n"] for t in ctls), f"대조군 {ctl} 합계"
            elif "def" in meas:
                bp, bn, bname = meas["def"]["pass"], meas["def"]["n"], "def"
            else:
                bp, bn = sum(t["pass"] for t in meas.values()), sum(t["n"] for t in meas.values())
                bname = "전체 평균"
            L.append(f" PHASE {ph}: 기준 = {bname} {bp}/{bn} ({round(bp * 100 / bn) if bn else 0}%)")
            rows = []
            for n, t in meas.items():
                if is_ctl(n) or n == "def":
                    continue
                pg = fisher_greater(t["pass"], t["n"] - t["pass"], bp, bn - bp)
                pl = fisher_greater(t["n"] - t["pass"], t["pass"], bn - bp, bp)
                rows.append((n, t, pg, pl))
            good = sorted([r for r in rows if r[1]["pass"] / r[1]["n"] > bp / max(bn, 1)],
                          key=lambda r: (r[2], -r[1]["avg"]))
            if ph == "1":
                self._cand1 = [r[0] for r in good]
            show = [r for r in good if r[2] < 0.2] or good[:5]
            L.append("   유망 (더 많이 돌려볼 것):" + ("" if any(r[2] < 0.2 for r in good)
                                                    else "  ※ 기준과 뚜렷한 차이는 아직 없음, 비율 상위만 표시"))
            for n, t, pg, _ in show[:8]:
                L.append(f"     {n:<11} {t['pass']:>3}/{t['n']:<3} ({t['rate']:>3}%)  avg {t['avg']:>7.1f}  p={pg:.3f}")
            bad = sorted([r for r in rows if r[3] < 0.05], key=lambda r: r[3])
            dead = self.st["dead_skip"][ph]
            if bad or dead:
                L.append("   제외 권장 (기준보다 뚜렷이 나쁨 / 멈춤):")
                for n, t, _, pl in bad:
                    L.append(f"     {n:<11} {t['pass']:>3}/{t['n']:<3} ({t['rate']:>3}%)  avg {t['avg']:>7.1f}  p={pl:.3f}")
                for n in dead:
                    v = self.st.get("sets", {}).get(n) or getattr(self, "base", {}).get(n)
                    L.append(f"     {n:<11} 멈춤(DEAD)" + (f"  PauseAll={v[0]}" if v else ""))
        return L

    def cumulative(self) -> list[str]:
        """이번 캠페인 세팅들의 누적(DB) 결과 - 같은 조건의 예전 캠페인까지 합산."""
        if "sets" not in self.st:
            return []
        db = _db()
        rows = db.load()
        if not rows:
            return []
        L = []
        for ph, opt in (("1", self.vars.get("opt1")), ("2", self.vars.get("opt2"))):
            t, cpus = db.tsec_of(opt), self.vars.get("cpus", "")
            P = db.pooled(rows, t, cpus)
            mine = {}
            for n, v in self.st["sets"].items():
                k = db.key_of(v)
                if k in P and (n in self.st["done"][ph] or ph == "1"):
                    mine.setdefault(k, n)
            if not mine or (ph == "2" and not self.st["done"]["2"]):
                continue
            ctl = self.st.get("control") or "def"
            ck = db.key_of(self.st["sets"].get(ctl) or self.base.get(ctl, [0] * 7))
            base = P.get(ck)
            L += ["", f"[누적 결과 -t{t} cpus={cpus}]  이번 세팅들을 예전 캠페인까지 합산 (data/fc_db.csv)"]
            if base:
                L.append(f"  기준 {ctl}: {base['pass']}/{base['n']} ({round(base['rate']*100)}%)  avg {base['avg']:.1f}")
            L.append(f"  {'setting':<11}{'pass':>10}{'rate':>6}{'95%CI':>11}{'avg':>8}  p(기준보다 좋음)  캠페인수")
            for k, n in sorted(mine.items(), key=lambda kv: (-P[kv[0]]["rate"], -P[kv[0]]["avg"])):
                d = P[k]
                lo, hi = db.wilson(d["pass"], d["n"])
                pv = ""
                if base and k != ck:
                    pv = f"{fisher_greater(d['pass'], d['n'] - d['pass'], base['pass'], base['n'] - base['pass']):.3f}"
                L.append(f"  {n:<11}{d['pass']:>5}/{d['n']:<4}{round(d['rate']*100):>5}%"
                         f"{f'{round(lo*100)}~{round(hi*100)}%':>11}{d['avg']:>8.1f}  {pv:>8}        {len(d['campaigns'])}")
        return L

    def write_candidates(self, names: list[str]):
        with open(os.path.join(self.dir, "candidates.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {os.path.basename(self.dir)} 의 유망 후보 (빠른 회차 비율 기준, 좋은 순)\n")
            f.write("# 이름       PauseAll SysOn SysOff ShOn ShOff PtOn PtOff\n")
            for n in names:
                v = self.st.get("sets", {}).get(n) or getattr(self, "base", {}).get(n)
                if v:
                    f.write(n.ljust(11) + " ".join(f"{x:>4}" for x in v) + "\n")

    def write_outputs(self, save: bool = True):
        live = read_live(os.path.join(self.dir, "live.txt"))
        # 끝난 회차 결과(state) + 지금 도는 회차에서 이미 끝난 세팅(live.txt)
        done1 = {**{n: t for n, t in live["1"].items() if n not in self.st["dead_skip"]["1"]}, **self.st["done"]["1"]}
        done2 = {**{n: t for n, t in live["2"].items() if n not in self.st["dead_skip"]["2"]}, **self.st["done"]["2"]}
        runs = self.st["runs"]
        recov = sum(1 for r in runs[1:] if r.get("reason", "").startswith(("after", "retry")))
        L = [f"FC sweep campaign  ({self.st['started']} ~ {datetime.datetime.now().isoformat(timespec='seconds')})",
             f"  preset={self.preset}  vars: " + " ".join(f"{k}={v}" for k, v in self.vars.items()
                                                        if k not in ("skip1", "skip2", "toplist", "phases")),
             f"  회차(전원 재인가) {len(runs)}번, 그중 멈춤 복구용 재부팅 {recov}번",
             f"  측정한 세팅: PHASE 1 {len(done1)}/{len(self.names)}개, PHASE 2 {len(done2)}개,"
             f"  iperf 유효 측정 {sum(t['n'] for t in done1.values()) + sum(t['n'] for t in done2.values())}회,"
             f"  멈춤(DEAD) {len(self.st['dead_skip']['1']) + len(self.st['dead_skip']['2'])}개",
             ""]
        for r in runs:
            L.append(f"  run{r['idx']:02d} {r['start']} [{r['mode']}] {r.get('reason', '')} → {r.get('status', '?')}"
                     f"  완료 {len(r.get('new_done', []))}개" + (f"  멈춤 {' '.join(r['dead'])}" if r.get("dead") else ""))

        def table(title, d, order=None):
            L.append("")
            L.append(title)
            L.append(f"  {'setting':<11}{'pass':>8}{'rate':>6}{'avg':>8}{'med':>8}{'p10':>8}{'min':>8}{'sd':>7}  run")
            names = order or sorted((n for n in d if d[n].get("n")), key=lambda n: -d[n]["avg"])
            for n in names:
                t = d.get(n)
                if not t or not t.get("n"):
                    L.append(f"  {n:<11}  (결과 없음)")
                    continue
                L.append(f"  {n:<11}{t['pass']:>4}/{t['n']:<3}{t['rate']:>5}%{t['avg']:>8.1f}{t['median']:>8.1f}"
                         f"{t['p10']:>8.1f}{t['min']:>8.1f}{t['sd']:>7.1f}  run{t['run']:02d}"
                         + ("  (진행 중 회차)" if t.get("live") else ""))

        table(f"[PHASE 1] {len(done1)}/{len(self.names)} 세팅 완료, avg 순", done1)
        ctl = self.st.get("control")
        if ctl:
            cs = [t for n, t in done1.items() if re.fullmatch(re.escape(ctl) + r"_c\d+", n) and t.get("n")]
            if cs:
                n = sum(t["n"] for t in cs)
                p = sum(t["pass"] for t in cs)
                a = sum(t["avg"] * t["n"] for t in cs) / n
                L.append(f"  대조군 {ctl} {len(cs)}번 합계: pass {p}/{n} ({round(p*100/n)}%)  avg {a:.1f}"
                         f"  (각 회: {' '.join(str(round(t['avg'])) for t in cs)})")
        if self.st["dead_skip"]["1"]:
            L.append("  DEAD(건너뜀): " + " ".join(self.st["dead_skip"]["1"]))
        if self.st["toplist"]:
            L.append(f"  → PHASE 2 대상: {' '.join(self.st['toplist'])}")
            table(f"[PHASE 2] {len(done2)}/{len(self.st['toplist'])} 세팅 완료, avg 순", done2)
            if self.st["dead_skip"]["2"]:
                L.append("  DEAD(건너뜀): " + " ".join(self.st["dead_skip"]["2"]))
        dc = {k: v for k, v in self.st["dead_count"].items() if v}
        if dc:
            L.append("")
            L.append("[멈춘 횟수] " + "  ".join(f"P{k}={v}" for k, v in dc.items()))
        self._cand1 = []
        L += self.candidates(done1, done2)
        L += self.cumulative()
        if save and self._cand1:
            self.write_candidates(self._cand1[: self.args.cand_max])
            L.append(f"  → 후보 {min(len(self._cand1), self.args.cand_max)}개를 candidates.txt 로 저장 "
                     f"(집중 테스트: --mode focus --sets {os.path.join(self.dir, 'candidates.txt')} --control def)")
        text = "\n".join(L) + "\n"
        if not save:
            return text
        with open(os.path.join(self.dir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(self.dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({**self.st, "names": self.names, "power_cycles": len(runs),
                       "recovery_reboots": recov}, f, ensure_ascii=False, indent=2)
        # merged.csv : 각 세팅을 완료한 회차의 run 행만 모음
        rows = []
        for r in runs:
            credited = {(ph, n) for ph in ("1", "2") for n, t in self.st["done"][ph].items() if t["run"] == r["idx"]}
            c = r.get("csv")
            if not c or not os.path.exists(c):
                continue
            with open(c, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if (row["phase"], row["setting"]) in credited:
                        rows.append({"campaign_run": r["idx"], "phase": row["phase"],
                                     "setting": row["setting"], "iperf_run": row["run"],
                                     "a": row["a"], "b": row["b"], "total": row["total"],
                                     "mark": row["mark"], "cpu": row.get("cpu", ""),
                                     "counters": row.get("counters", "")})
        with open(os.path.join(self.dir, "merged.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["campaign_run", "phase", "setting", "iperf_run",
                                              "a", "b", "total", "mark", "cpu", "counters"])
            w.writeheader()
            w.writerows(rows)
        return text

    # ---- 메인 루프 ---------------------------------------------------
    def go(self) -> int:
        self.clog(f"시작: dir={self.dir}  세팅 {len(self.names)}개  topn={self.topn}")
        noprog = 0
        waiting = False
        reason = "start" if not self.st["runs"] else "resume"
        while True:
            p = self.plan()
            if p is None:
                self.st["finished"] = True
                self.save()
                self.clog("완료")
                print("\n" + self.write_outputs())
                return 0
            if p == "wait":
                if not waiting:
                    self.clog("PHASE 1 목록 다 끝남 → 대기 중 (add.txt 에 세팅 추가"
                              + (" / phase2.txt 로 PHASE 2" if self.st.get("mode", "2stage") == "2stage" else "")
                              + " / stop 으로 종료)")
                    print("\n" + self.write_outputs())
                    waiting = True
                time.sleep(10)
                continue
            waiting = False
            mode, extra = p
            if len(self.st["runs"]) >= self.args.max_runs:
                self.clog(f"회차 한도 {self.args.max_runs} 도달 → 중단")
                break
            idx = len(self.st["runs"]) + 1
            rec = {"idx": idx, "mode": mode, "reason": reason,
                   "start": datetime.datetime.now().strftime("%H:%M:%S")}
            self.st["runs"].append(rec)
            self.save()
            self.clog(f"run{idx:02d} 시작 [PHASE {mode}] ({reason})"
                      + (f" skip1 {len(extra['skip1'].split())}개" if extra.get("skip1") else "")
                      + (f" skip2 {len(extra['skip2'].split())}개" if extra.get("skip2") else ""))
            out = self.run_once(idx, extra)
            info = self.absorb(idx, mode, out)
            post = (out["result"].get("post") or {}).get("fc_sweep4") or {}
            rec.update(info)
            rec["end"] = datetime.datetime.now().strftime("%H:%M:%S")
            rec["csv"] = post.get("csv")
            try:
                self.db_append(idx, rec["csv"], info["new_done"])
            except Exception as e:  # noqa: BLE001
                self.clog(f"누적 DB 기록 실패(무시): {e}")
            self.save()
            self.write_outputs()
            self.clog(f"run{idx:02d} 끝: {info['status']}  완료 {len(info['new_done'])}개"
                      + (f"  멈춤 {' '.join(info['dead'])}" if info["dead"] else ""))
            if out["rc"] == 130 and not out["hung"]:
                self.clog("사용자 중단 → 캠페인 중단 (--resume 으로 이어가기 가능)")
                print("\n" + self.write_outputs())
                return 130
            if info["new_done"] or info["dead"]:
                noprog = 0
            else:
                noprog += 1
                if noprog >= self.args.max_noprog:
                    self.clog(f"{noprog}회 연속 진척 없음(부팅/링크 실패 등) → 중단")
                    break
            if info["dead"]:
                reason = "after " + " ".join(info["dead"])
            elif info["status"] != "PASS":
                reason = f"retry after {info['status']}"
            else:
                reason = "next phase"
        self.save()
        print("\n" + self.write_outputs())
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="멈추면 전원 재인가 후 이어서 도는 FC 스윕 캠페인")
    ap.add_argument("--preset", default="fc-sweep-2stage")
    ap.add_argument("--sets", metavar="FILE",
                    help="세팅 목록 파일 (한 줄: 이름 PauseAll SysOn SysOff ShOn ShOff PtOn PtOff, 또는 기존 이름만)")
    ap.add_argument("--control", metavar="NAME", help="대조군 세팅 (예: def) 을 사이사이 끼워 넣음")
    ap.add_argument("--control-every", type=int, default=5, help="대조군을 몇 개마다 넣을지 (기본 5)")
    ap.add_argument("--open", action="store_true",
                    help="PHASE 1 목록이 끝나도 바로 PHASE 2 로 안 가고 기다림. 캠페인 폴더에 "
                         "add.txt(세팅 추가) / phase2.txt(PHASE 2 시작, 이름 적으면 그걸로) / stop(종료)")
    ap.add_argument("--var", action="append", metavar="KEY=VALUE", help="run.py 에 넘길 변수")
    ap.add_argument("--resume", metavar="DIR", help="이전 캠페인 폴더에서 이어가기")
    ap.add_argument("--idle", type=int, default=0,
                    help="테스트 중 무출력 한도(초)를 고정. 0(기본)이면 iperf -t × --idle-mult")
    ap.add_argument("--idle-mult", type=float, default=2.0,
                    help="테스트 중 무출력 한도 = 지금 PHASE 의 iperf -t × 이 값 (기본 2 → -t10 이면 20초)")
    ap.add_argument("--idle-min", type=int, default=10, help="무출력 한도 최소값 (기본 10초)")
    ap.add_argument("--idle-boot", type=int, default=600,
                    help="부팅/업로드 등 테스트 시작 전 무출력 한도 (기본 600)")
    ap.add_argument("--max-dead", type=int, default=1,
                    help="같은 세팅이 이만큼 멈추면 건너뜀 (기본 1 = 한 번 멈추면 재부팅 후 다음 세팅부터)")
    ap.add_argument("--max-runs", type=int, default=40, help="회차(전원 재인가) 최대 수 (기본 40)")
    ap.add_argument("--max-noprog", type=int, default=3, help="진척 없는 회차 연속 허용 수 (기본 3)")
    ap.add_argument("--mode", choices=["2stage", "explore", "focus"], default="2stage",
                    help="2stage(기본)=스크리닝 후 TOP 집중 / explore=-t3 x10 으로 넓게만, 끝나면 candidates.txt / "
                         "focus=목록을 -t10 x50 으로 집중 (보통 --sets candidates.txt --control def)")
    ap.add_argument("--cand-max", type=int, default=8, help="candidates.txt 에 넣을 최대 개수 (기본 8)")
    ap.add_argument("--retest", action="store_true", help="DB 에 이미 있는 값 조합도 다시 돌림")
    ap.add_argument("--until", type=int, default=0,
                    help="focus: 누적 -t10 측정이 이 횟수 이상인 후보는 건너뜀 (0=항상 추가로 쌓음)")
    ap.add_argument("--report", metavar="DIR", help="캠페인 폴더의 지금까지 결과만 출력 (돌고 있는 중에도 가능)")
    args = ap.parse_args(argv)
    if args.report:
        c = Campaign.__new__(Campaign)
        c.args, c.dir = args, os.path.abspath(args.report)
        with open(os.path.join(c.dir, "state.json"), encoding="utf-8") as f:
            c.st = json.load(f)
        c.vars, c.preset = c.st["vars"], c.st["preset"]
        c.base = parse_settings(load_preset(find_preset(c.st.get("base_preset", c.preset)))[1])
        c.st.setdefault("dead_skip", {"1": [], "2": []})
        if "sets" in c.st:
            c.names = list(c.st["sets"])
        else:
            c.names = setting_names(load_preset(find_preset(c.preset))[1], c.vars.get("setlist", "all"))
        print(c.write_outputs(save=False))
        return 0
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C 는 자식 run.py 가 받아서 정리
    return Campaign(args).go()


if __name__ == "__main__":
    sys.exit(main())
