#!/usr/bin/env python3
"""
SDRAM(SBL DDR 설정) 후보를 차례로 시험하는 캠페인.

후보 하나마다:
  1) SBL 소스의 대상 파일들을 캠페인 시작 때 보관한 원본으로 되돌림
  2) 후보 패치 적용 (git apply)
  3) cli/run.py sdram-sbl-cycle 실행 = SBL 빌드 → SBL 다운로드 → 부팅 → kern reboot N번
  4) N번 모두 성공(PASS) → 그 후보가 정답, 캠페인 끝
     중간에 한 번이라도 실패(FAIL) → 실패 로그 + 패치 + 사유를 후보 폴더에 남기고 다음 후보

  ~/.venv-gbt/bin/python cli/sdram_campaign.py --port /dev/ttyUSB0 --baud 921600
  ~/.venv-gbt/bin/python cli/sdram_campaign.py --resume logs/sdram_20261001_213000 ...

후보 목록: sdram/variants/ORDER.txt 순서 (없으면 *.patch 이름 순). 캠페인 도중에 .patch 를 넣고
ORDER.txt 에 이름을 추가하면 다음 후보부터 반영된다.

목록을 다 쓰면 자동으로 새 후보를 만든다 (--no-auto 로 끔): 지금까지 가장 오래 버틴(실패 전까지
reboot 성공 횟수가 많은) 후보부터, 손잡이 하나만 한 칸 바꾼 이웃을 차례로 시험한다 (sdram_knobs.py).
--until "2026-10-06 09:00" 이면 그 시각이 지나면 새 후보를 시작하지 않는다.
캠페인 폴더에 'stop' 파일을 만들면 지금 후보까지만 하고 멈춘다.

결과: logs/sdram_<시각>/
  summary.txt          후보별 결과 한눈에
  results.csv          후보, 결과, 몇 번째 reboot 에서 실패, 사유, 소요시간
  <후보>/<후보>.patch  적용한 패치 (기준 대비)
  <후보>/*_FAIL.log    실패한 부팅 (이전 부팅 / 실패한 부팅 / 실패 이후)
  <후보>/*.json        run.py 결과
  orig/                시작 때 보관한 원본 (끝나면 SBL 소스는 이 원본으로 되돌려 둔다)

끝나면(통과, 후보 소진, --until, ERROR 연속, Ctrl-C, 터미널 닫힘) 보드 전원을 끈다 (--no-power-off 로 끔).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import difflib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdram_knobs as kn  # noqa: E402

CLI_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CLI_DIR)
# 2026-10-23: 서버 구조 변경 (/home/test/gdm-autotest/sdk/sbl/7275x)
DEFAULT_SBL = "/home/test/gdm-autotest/sdk/sbl/7275x"
DEFAULT_FILES = ["base/drivers/orbit_ddram/sphy_v9r8_functions.c",
                 "base/drivers/orbit_ddram/mr_config.h"]


# 모뎀 이미지 압축 해제 실패 → 모뎀 IS-ALIVE 타임아웃 → modem wdt 리셋 (rr=0x2) 로 이어지는 실패.
# DRAM 오류일 수도, eMMC 읽기 문제일 수도 있어서 이 실패면 같은 설정을 한 번 더 돌린다.
CP_ERR = "CP decompress error"
# SBL Makefile 의 기본값 (DDRRATE ?= 3732). --build-args 로 DDRRATE 를 바꾸면 폴더/후보 이름에 _ddr<값> 을 붙여 구분
DEFAULT_DDRRATE = 3732


DEFAULT_DDRVDD2 = "1.150"   # SBL Makefile 기본값 (DDRVDD2 ?= 1.150)


def rate_of(build_args) -> int:
    m = re.search(r"DDRRATE=(\d+)", build_args or "")
    return int(m.group(1)) if m else DEFAULT_DDRRATE


def vdd2_of(build_args) -> str:
    m = re.search(r"DDRVDD2=(\d\.\d+)", build_args or "")
    return m.group(1) if m else DEFAULT_DDRVDD2


def build_tag(build_args) -> str:
    """빌드 인자 중 기본값이 아닌 것을 이름에 붙일 꼬리표로: _ddr3200, _v2_1125 ..."""
    tag = ""
    r = rate_of(build_args)
    if r != DEFAULT_DDRRATE:
        tag += f"_ddr{r}"
    v = vdd2_of(build_args)
    if v != DEFAULT_DDRVDD2:
        tag += "_v2_" + v.replace(".", "")[:4]
    if ch_of(build_args) == 1:
        tag += "_1ch"
    if opt0(build_args):
        tag += "_opt0"
    a = armvcc_of(build_args)
    if a != DEFAULT_ARMVCC:
        tag += "_arm" + a.replace(".", "")[1:4]
    return tag


DEFAULT_ARMVCC = "0.850"    # SBL Makefile 기본값 (ARMVCC ?= 0.850)


def armvcc_of(build_args) -> str:
    """VCC_ARM = 'Digital Power for ARM+DDR core' (GDM7275X 데이터시트 9.2, 0.808~0.893V)."""
    m = re.search(r"ARMVCC=(\d\.\d+)", build_args or "")
    return m.group(1) if m else DEFAULT_ARMVCC


def opt0(build_args) -> bool:
    """DDR_OPTI_SZ=0 빌드 (Makefile 기본 1 → -DDDR_OPTI_SZ 로 CBT/PHY VREF 트레이닝 코드가 빠짐)."""
    return re.search(r"DDR_OPTI_SZ=0\b", build_args or "") is not None


def ch_of(build_args) -> int:
    """RETRY_DDR_CH (m2 기본 2). 1 = CH0 만 초기화 (test_lpddr4.c ddr_sweep_ch != 2 → omc_cfg 만)."""
    m = re.search(r"RETRY_DDR_CH=(\d)", build_args or "")
    return int(m.group(1)) if m else 2


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Campaign:
    def __init__(self, args):
        self.a = args
        if args.resume:
            self.dir = os.path.abspath(args.resume)
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                self.st = json.load(f)
            if args.reboots and args.reboots != self.st["reboots"]:
                # 이어하면서 횟수를 바꾸면 이후 후보부터 적용 (이미 끝난 후보 결과는 그대로)
                self.st["reboots"] = args.reboots
        else:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.join(ROOT, "logs", f"sdram_{stamp}" + build_tag(args.build_args))
            self.dir, n = base, 1
            while os.path.exists(self.dir):          # 같은 초에 두 캠페인이 시작돼도 겹치지 않게
                n += 1
                self.dir = f"{base}_{n}"
            os.makedirs(self.dir)
            self.st = {"started": now(), "sbl": os.path.abspath(args.sbl), "files": args.files,
                       "variants_dir": os.path.abspath(args.variants), "reboots": args.reboots or 100,
                       "done": {}, "order": []}
            self._backup()
        # 시작 설정 / 탐색할 손잡이 (새 캠페인이나 --resume 때 주면 저장)
        if args.seed:
            self.st["seed"] = self._seed(args.seed)
        if args.skip is not None:
            sk = {}
            for part in args.skip.split(","):
                k, _, v = part.strip().partition("=")
                if k not in kn.KNOBS:
                    raise SystemExit(f"--skip: 모르는 손잡이 '{k}'")
                sk.setdefault(k, []).append(v if k == "adj" else int(v, 0))
            self.st["skip"] = sk
        if args.build_args is not None:
            self.st["build_args"] = args.build_args
        if args.extra_patch:
            dst = os.path.join(self.dir, "extra.patch")
            shutil.copy2(args.extra_patch, dst)
            self.st["extra_patch"] = dst
        if args.explore is not None:
            bad = [k for k in args.explore if k not in kn.KNOBS]
            if bad:
                raise SystemExit(f"--explore: 모르는 손잡이 {bad} (가능: {', '.join(kn.KNOBS)})")
            self.st["explore"] = args.explore
        self._save()
        self.sbl = self.st["sbl"]

    @property
    def rate(self) -> int:
        return rate_of(self.st.get("build_args"))

    def _lbl(self, s: dict) -> str:
        """후보 이름용 라벨. 기본(3732)이 아닌 속도로 빌드하면 _ddr<값> 을 붙인다."""
        return kn.label(s) + build_tag(self.st.get("build_args"))

    def _seed(self, text: str) -> dict:
        """'dsvref=0x20,pdds=1,adj=test-5' → 원본 설정에 덮어쓴 전체 설정."""
        s = kn.normalize(kn.parse(self._orig()))
        for part in text.split(","):
            k, _, v = part.strip().partition("=")
            if k not in kn.KNOBS:
                raise SystemExit(f"--seed: 모르는 손잡이 '{k}' (가능: {', '.join(kn.KNOBS)})")
            s[k] = v if k == "adj" else int(v, 0)
        s["cavref"] = kn.CAVREF_FIXED
        if not kn.in_range(s):
            raise SystemExit(f"--seed: 탐색 목록 밖 값이 있음 ({kn.key(s)})")
        if not kn.valid(s):
            raise SystemExit(f"--seed: 데이터시트상 같이 못 쓰는 조합 ({kn.key(s)})")
        ba = (getattr(self, "st", None) or {}).get("build_args") or self.a.build_args or ""
        if (s.get("cbt") or s.get("pvref")) and not opt0(ba):
            raise SystemExit("--seed: cbt/pvref 는 DDR_OPTI_SZ 로 빌드에서 빠지는 코드 → --build-args 에 DDR_OPTI_SZ=0 필요")
        return s

    # ---- 상태 ----------------------------------------------------------
    def _save(self):
        with open(os.path.join(self.dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=2)

    def log(self, msg: str):
        line = f"[{now()}] {msg}"
        print(f"### sdram: {msg}", flush=True)
        with open(os.path.join(self.dir, "campaign.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- 원본 보관/복원 --------------------------------------------------
    def _backup(self):
        for rel in self.st["files"]:
            src = os.path.join(self.st["sbl"], rel)
            dst = os.path.join(self.dir, "orig", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    def restore(self):
        for rel in self.st["files"]:
            shutil.copy2(os.path.join(self.dir, "orig", rel), os.path.join(self.sbl, rel))

    def apply(self, patch: str) -> str | None:
        """패치 적용. 실패하면 에러 문자열."""
        with open(patch, encoding="utf-8", errors="replace") as f:
            if not any(ln.startswith("--- ") for ln in f):
                return None                       # 변경 없음 (기준 그대로)
        for cmd in (["git", "apply", "--check", patch], ["git", "apply", patch]):
            r = subprocess.run(cmd, cwd=self.sbl, capture_output=True, text=True)
            if r.returncode != 0:
                return (r.stderr or r.stdout).strip()
        return None

    # ---- 후보 목록 -------------------------------------------------------
    def variants(self) -> list[str]:
        vd = self.st["variants_dir"]
        order = os.path.join(vd, "ORDER.txt")
        if os.path.exists(order):
            with open(order, encoding="utf-8") as f:
                names = [ln.split("#")[0].strip() for ln in f]
            names = [n[:-6] if n.endswith(".patch") else n for n in names if n]
        else:
            names = []
        extra = sorted(os.path.basename(p)[:-6] for p in glob.glob(os.path.join(vd, "*.patch")))
        names += [n for n in extra if n not in names and not os.path.exists(order)]
        return names

    # ---- 후보 하나 시험 ---------------------------------------------------
    def _orig(self) -> dict:
        out = {}
        for rel in self.st["files"]:
            with open(os.path.join(self.dir, "orig", rel), encoding="utf-8", newline="") as f:
                out[rel] = f.read()
        return out

    def _current(self) -> dict:
        out = {}
        for rel in self.st["files"]:
            with open(os.path.join(self.sbl, rel), encoding="utf-8", newline="") as f:
                out[rel] = f.read()
        return out

    def run_variant(self, item: dict) -> dict:
        name = item["name"]
        vd = os.path.join(self.dir, name)
        os.makedirs(vd, exist_ok=True)
        t0 = time.time()
        res = {"variant": name, "started": now(), "status": "", "reason": "", "reboot": "", "ddr_rate": self.rate,
               "dur_min": 0, "auto": bool(item.get("settings"))}
        self.restore()
        if item.get("settings"):                   # 자동 후보: 원본에 설정을 써 넣음
            try:
                new = kn.build(self._orig(), item["settings"])
            except ValueError as e:
                res.update(status="PATCH_FAIL", reason=str(e))
                return res
            orig = self._orig()
            diff = []
            for rel, text in new.items():
                with open(os.path.join(self.sbl, rel), "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                diff += difflib.unified_diff(orig[rel].splitlines(keepends=True),
                                             text.splitlines(keepends=True), "a/" + rel, "b/" + rel)
            with open(os.path.join(vd, name + ".patch"), "w", encoding="utf-8", newline="") as f:
                f.write(f"# variant: {name} (자동 생성)\n# {kn.key(item['settings'])}\n"
                        f"# {item.get('why', '')}\n#\n" + "".join(diff))
        else:
            patch = os.path.join(self.st["variants_dir"], name + ".patch")
            if not os.path.exists(patch):
                res.update(status="SKIP", reason="패치 파일 없음")
                return res
            shutil.copy2(patch, os.path.join(vd, name + ".patch"))
            err = self.apply(patch)
            if err:
                res.update(status="PATCH_FAIL", reason=err.splitlines()[0] if err else "")
                self.log(f"{name}: 패치 적용 실패 - {err}")
                return res
        xp = self.st.get("extra_patch")
        if xp:                                     # 설정과 별개로 항상 얹는 패치 (예: 트레이닝 결과 dump 출력)
            err = self.apply(xp)
            if err:
                res.update(status="PATCH_FAIL", reason="extra patch: " + (err.splitlines()[0] if err else ""))
                self.log(f"{name}: extra patch 적용 실패 - {err}")
                return res
        try:
            res["settings"] = kn.parse(self._current())
        except Exception as e:  # noqa: BLE001
            res["settings"] = {}
            self.log(f"{name}: 설정 읽기 실패 ({e}) - 자동 탐색 기준에서 제외")
        # 실제로 들어간 변경(기준 원본 대비)도 남긴다
        diff = []
        for rel in self.st["files"]:
            r = subprocess.run(["diff", "-u", os.path.join(self.dir, "orig", rel),
                                os.path.join(self.sbl, rel)], capture_output=True, text=True)
            diff.append(r.stdout)
        with open(os.path.join(vd, "applied.diff"), "w", encoding="utf-8") as f:
            f.write("".join(diff))

        cmd = [sys.executable, os.path.join(CLI_DIR, "run.py"), self.a.preset,
               "--var", f"reboots={self.st['reboots']}", "--var", f"variant={name}",
               "--var", f"sbl_dir={self.sbl}", "--out-dir", vd, "--post", "sdram"]
        for kv in (self.a.var or []):              # 프리셋 변수 추가 지정 (예: dl_retries=10, preload=...)
            cmd += ["--var", kv]
        if self.st.get("build_args"):
            cmd += ["--var", f"build_args={self.st['build_args']}"]
        if self.a.full_log:
            cmd += ["--log", "full"]               # 부팅마다 전체 콘솔 로그 (트레이닝 결과 dump 등)
        if self.a.port:
            cmd += ["--port", self.a.port]
        if self.a.baud:
            cmd += ["--baud", str(self.a.baud)]
        if self.a.run_cmd:                         # 시험용: run.py 대신 다른 명령
            cmd = self.a.run_cmd.split() + ["--out-dir", vd]
        self.log(f"{name}: 시작 ({' '.join(cmd[1:3])} ... reboots={self.st['reboots']})"
                 + (f" 빌드 추가 인자: {self.st['build_args']}" if self.st.get("build_args") else ""))
        try:
            rc = subprocess.call(cmd, cwd=ROOT)
        except KeyboardInterrupt:
            rc = 130
        js = sorted(glob.glob(os.path.join(vd, "*.json")), key=os.path.getmtime)
        out = {}
        if js:
            with open(js[-1], encoding="utf-8") as f:
                out = json.load(f)
        res["status"] = out.get("status") or {0: "PASS", 1: "FAIL", 130: "INTERRUPTED"}.get(rc, "ERROR")
        res["reason"] = out.get("reason", "")
        # DDR 설정과 상관없는 단계(빌드/다운로드)에서 실패한 건 FAIL 이 아니라 ERROR 로 본다
        blk = out.get("block", "")
        if res["status"] == "FAIL" and blk.startswith("sb_build"):
            res["status"], res["reason"] = "ERROR", "빌드 실패: " + res["reason"]
        elif res["status"] == "FAIL" and (blk.startswith("fu_") or blk.startswith("sb_retry")):
            res["status"], res["reason"] = "ERROR", "SBL 다운로드 실패: " + res["reason"]
        elif res["status"] == "FAIL" and blk.startswith("sb_verify"):
            # 첫 부팅 로그에 이 후보의 표시(DDR_PROFILE=SBLSWEEP_<후보>)가 없음 = 다른 SBL(예: 반대 슬롯의
            # 예전 이미지)로 부팅. DDR 결과로 세면 안 되므로 ERROR.
            res["status"], res["reason"] = "ERROR", "후보 SBL 로 부팅 안 됨(표시 없음): " + res["reason"]
        it = out.get("iteration", "")
        m = re.match(r"(\d+)/(\d+)", it or "")
        res["reboot"] = f"{m.group(1)}/{m.group(2)}" if m else it
        res["dur_min"] = round((time.time() - t0) / 60, 1)
        self.verify(res, out)
        res["score"] = self.score(res)
        fails = sorted(glob.glob(os.path.join(vd, "*_FAIL.log")), key=os.path.getmtime)
        res["fail_log"] = os.path.relpath(fails[-1], self.dir) if fails else ""
        m = re.search(r"DDRRATE=(\d+)", self.st.get("build_args") or "")
        if m and fails:
            with open(fails[-1], encoding="utf-8", errors="replace") as f:
                seen = sorted(set(re.findall(r"ddr=(\d+)", f.read())))
            res["ddr_rate_seen"] = seen
            self.log(f"{name}: 부팅 로그 ddr= {seen or '출력 없음'} (빌드 DDRRATE={m.group(1)})")
        exp_v2 = vdd2_of(self.st.get("build_args"))
        if fails:
            with open(fails[-1], encoding="utf-8", errors="replace") as f:
                seen2 = sorted(set(re.findall(r"vdd2=\d+\((\d\.\d+)\)", f.read())))
            res["vdd2_seen"] = seen2
            if seen2 != [exp_v2]:
                self.log(f"{name}: 부팅 로그 vdd2= {seen2 or '출력 없음'} (기대 {exp_v2})" + (" ⚠ 다름" if seen2 else ""))
        if fails:
            with open(fails[-1], encoding="utf-8", errors="replace") as f:
                chs = sorted(set(re.findall(r"CH\((\d)\), period", f.read())))
            res["ch_seen"] = chs
            exp_ch = ["0"] if ch_of(self.st.get("build_args")) == 1 else ["0", "1"]
            if chs and chs != exp_ch:
                self.log(f"{name}: 부팅 로그 CH {chs} (기대 {exp_ch}) ⚠ 다름")
        exp_arm = armvcc_of(self.st.get("build_args"))
        if fails:
            with open(fails[-1], encoding="utf-8", errors="replace") as f:
                seen_a = sorted(set(re.findall(r"arm=\d+\(vcc=\d+\((\d\.\d+)\)\)", f.read())))
            res["armvcc_seen"] = seen_a
            if seen_a != [exp_arm]:
                self.log(f"{name}: 부팅 로그 arm vcc= {seen_a or '출력 없음'} (기대 {exp_arm})" + (" ⚠ 다름" if seen_a else ""))
        exp_vq = (res.get("settings") or {}).get("vddq")
        if exp_vq and fails:
            with open(fails[-1], encoding="utf-8", errors="replace") as f:
                seen = sorted({round(float(v) * 1000) for v in re.findall(r"vddq=\d+\((\d\.\d+)\)", f.read())})
            res["vddq_seen"] = seen
            ok = seen == [exp_vq]
            self.log(f"{name}: 부팅 로그 vddq= {seen or '출력 없음'} mV (기대 {exp_vq})" + ("" if ok or not seen else " ⚠ 다름"))
        if res["status"] == "FAIL" and fails and self._cp_decompress_error(fails[-1]):
            res["cp_err"] = True
            res["reason"] += " [CP decompress error]"
        return res

    @staticmethod
    def _cp_decompress_error(path: str) -> bool:
        """실패한 부팅에서 모뎀 이미지 gzip 해제 실패가 있었나 (DDR 말고 eMMC 읽기 등일 수도 있는 실패)."""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return False
        i = text.find("##### 실패한 부팅")
        j = text.find("=== AFTER FAIL")
        part = text[i if i >= 0 else 0: j if j > i else len(text)]
        return CP_ERR in part

    def verify(self, res: dict, out: dict):
        """패치한 값이 실제로 보드에 들어갔는지: 부팅 로그의 DDR 설정 출력(post_sdram)과 비교."""
        s = res.get("settings") or {}
        if not kn.complete(s):
            return
        exp = kn.expected_print(self._orig(), s)
        got = ((out.get("post") or {}).get("sdram") or {}).get("ddr") or {}
        if not got:
            res["verify"] = "확인 불가 (부팅 로그에 DDR 설정 출력 없음)"
            self.log(f"{res['variant']}: ⚠ {res['verify']}")
            return
        diff = [f"{k}: 기대 {exp[k]} / 실제 {got.get(k)}" for k in exp if got.get(k) != exp[k]]
        res["verify"] = "ok" if not diff else "불일치 " + "; ".join(diff)
        self.log(f"{res['variant']}: 설정 확인 {res['verify']}")
        # cbt_ca 는 반영이 안 되는 값이라 경고만. 나머지가 다르고, 보드가 첫 부팅 이후 reboot 까지
        # 했으면 패치가 안 들어간 것(MISMATCH). 첫 부팅에서 DDR 초기화부터 실패한 경우
        # (예: DDR RETRY FAIL 뒤 dvfs0-ds=0x0)는 그 설정의 실패로 본다.
        if any(not d.startswith("cbt_ca") for d in diff):
            if res["status"] == "FAIL" and not re.match(r"\d+/", res.get("reboot") or ""):
                res["verify"] += " (첫 부팅 실패라 설정 출력이 정상이 아닐 수 있음)"
            else:
                res["status"] = "MISMATCH"

    def score(self, res: dict):
        """얼마나 버텼나 = 실패 전까지 성공한 kern reboot 횟수 (PASS 면 전체). ERROR 는 None."""
        if res["status"] == "PASS":
            return self.st["reboots"]
        if res["status"] != "FAIL":
            return None
        m = re.match(r"(\d+)/", res.get("reboot") or "")
        return int(m.group(1)) - 1 if m else 0     # 회차가 없으면 첫 부팅(전원 ON)에서 실패

    @staticmethod
    def ekey(s: dict) -> str:
        """실제로 효과가 있는 설정만으로 만든 key (cavref 는 반영이 안 돼서 무시)."""
        return kn.key(kn.normalize({**s, "cavref": kn.CAVREF_FIXED}))

    def _tested(self) -> dict:
        """효과 있는 설정 key → {settings, scores, mean, n, variants, first}.
        같은 설정을 여러 번 돌렸으면 평균으로 본다 (한 번 결과는 편차가 커서).
        ERROR/PATCH_FAIL/MISMATCH 처럼 점수가 없는 건 뺀다."""
        out = {}
        for i, r in enumerate(self.st["done"].values()):
            s = r.get("settings") or {}
            if r.get("score") is None or not kn.complete(s):
                continue
            g = out.setdefault(self.ekey(s), {"settings": kn.normalize({**s, "cavref": kn.CAVREF_FIXED}),
                                              "scores": [], "variants": [], "first": i})
            g["scores"].append(r["score"])
            g["variants"].append(r["variant"])
        for g in out.values():
            g["n"] = len(g["scores"])
            g["mean"] = sum(g["scores"]) / g["n"]
        return out

    def auto_next(self) -> dict | None:
        tested = self._tested()
        if not tested:
            return None
        # 탐색 목록 밖 값(예: DDR 초기화가 안 되는 ds 0x26)이 들어간 결과는 기준으로 안 씀
        centers = [g for g in sorted(tested.values(), key=lambda g: (-g["mean"], g["first"]))
                   if kn.in_range(g["settings"])]
        if not centers:
            return None
        top = centers[0]
        # 1등 설정이 한 번만 돌린 거면 먼저 같은 설정으로 다시 돌려 평균을 낸다 (운으로 1등일 수 있음)
        if top["n"] < self.a.repeat:
            self.st["auto_n"] = self.st.get("auto_n", 0) + 1
            return {"name": f"a{self.st['auto_n']:03d}_{self._lbl(top['settings'])}_r{top['n'] + 1}",
                    "settings": top["settings"],
                    "why": f"재확인: 지금 1등 (평균 {top['mean']:.1f}번, {top['n']}회) 같은 설정 반복"}
        for g in centers:
            for s in kn.neighbors(g["settings"], self.st.get("explore"), self.st.get("skip")):
                if self.ekey(s) in tested:
                    continue
                self.st["auto_n"] = self.st.get("auto_n", 0) + 1
                return {"name": f"a{self.st['auto_n']:03d}_{self._lbl(s)}", "settings": s,
                        "why": f"기준: {'/'.join(g['variants'])} (평균 {g['mean']:.1f}번, {g['n']}회)의 이웃"}
        return None

    def next_item(self) -> dict | None:
        if self.a.only:
            for v in self.a.only:
                if v not in self.st["done"]:
                    return {"name": v}
            return None
        for v in self.variants():
            if v not in self.st["done"]:
                return {"name": v}
        if self.a.no_auto:
            return None
        seed = self.st.get("seed")
        if seed and self.ekey(seed) not in self._tested():
            # 시작 설정 (--seed) 을 아직 안 돌렸으면 그것부터
            self.st["auto_n"] = self.st.get("auto_n", 0) + 1
            return {"name": f"a{self.st['auto_n']:03d}_{self._lbl(seed)}", "settings": seed,
                    "why": "시작 설정 (--seed)"}
        return self.auto_next()

    # ---- 결과 기록 -------------------------------------------------------
    def record(self, res: dict):
        self.st["done"][res["variant"]] = res
        self._save()
        path = os.path.join(self.dir, "results.csv")
        new = not os.path.exists(path)
        cols = ["variant", "status", "reboot", "reason", "dur_min", "started", "fail_log"]
        if new:
            cols.insert(1, "ddr_rate")
        else:
            with open(path, encoding="utf-8") as f:
                if "ddr_rate" in f.readline():
                    cols.insert(1, "ddr_rate")
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(res)
        self.write_summary()

    def write_summary(self, final: str = ""):
        L = [f"SDRAM campaign  {self.st['started']} ~ {now()}",
             f"  DDRRATE: {self.rate}  DDRVDD2: {vdd2_of(self.st.get('build_args'))}"
             + (f"  (빌드 추가 인자: {self.st['build_args']})" if self.st.get("build_args") else "  (SBL 기본값)"),
             f"  SBL: {self.sbl}   reboots/후보: {self.st['reboots']}",
             f"  파일: {', '.join(self.st['files'])}", ""]
        L.append(f"  {'후보':<34}{'결과':<11}{'버틴 reboot':>11}{'시간(분)':>9}  설정 / 사유")
        for name, r in self.st["done"].items():
            s = r.get("settings") or {}
            sk = kn.key(s) if kn.complete(s) else str(s)
            sc = "" if r.get("score") is None else r["score"]
            L.append(f"  {name:<34}{r['status']:<11}{str(sc):>11}{r.get('dur_min', 0):>9}  {sk}")
            if r.get("reason"):
                L.append(f"  {'':<34}{'':<11}{'':>11}{'':>9}    {r['reason']}")
            if r.get("verify") and r["verify"] != "ok":
                L.append(f"  {'':<34}{'':<11}{'':>11}{'':>9}    설정 확인: {r['verify']}")
        tested = self._tested()
        if tested:
            L += ["", "  설정별 평균 (같은 설정 여러 번이면 평균, cavref 무시) 상위 5:"]
            for g in sorted(tested.values(), key=lambda g: (-g["mean"], g["first"]))[:5]:
                L.append(f"    평균 {g['mean']:5.1f}번  n={g['n']}  {self.ekey(g['settings'])}  "
                         f"({', '.join(map(str, g['scores']))})  {'/'.join(g['variants'])}")
        if final:
            L += ["", final]
        with open(os.path.join(self.dir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
        return L

    def power_off(self):
        """캠페인이 어떻게 끝나든 보드 전원을 끈다 (presets/power-off.json 을 run.py 로 실행)."""
        if self.a.no_power_off:
            return
        cmd = [sys.executable, os.path.join(CLI_DIR, "run.py"), "power-off", "--log", "fail",
               "--out-dir", self.dir, "--quiet"]
        if self.a.port:
            cmd += ["--port", self.a.port]
        if self.a.baud:
            cmd += ["--baud", str(self.a.baud)]
        for attempt in range(3):
            try:
                r = subprocess.run(cmd, cwd=ROOT, timeout=90)
                if r.returncode == 0:
                    self.log("보드 전원 OFF")
                    return
            except Exception as e:  # noqa: BLE001
                self.log(f"전원 OFF 실행 오류: {e}")
            time.sleep(10)
        self.log("!! 보드 전원 OFF 실패 (Tapo 확인 필요)")

    # ---- 본체 -----------------------------------------------------------
    def run(self) -> int:
        self.log(f"캠페인 폴더 {self.dir}")
        errors = 0
        final = ""
        item = None          # ERROR 로 다시 시도할 후보 (None 이면 다음 후보)
        n_new = 0            # 이번 실행에서 기록한 후보 결과 수 (--max-new)
        confirm_left = 0     # PASS 후 같은 후보 재확인 남은 횟수
        try:
            while True:
                if self.a.max_new and n_new >= self.a.max_new and confirm_left == 0 and item is None:
                    final = f"--max-new {self.a.max_new} 도달 → 멈춤 (--resume 으로 이어서)"
                    break
                if os.path.exists(os.path.join(self.dir, "stop")):
                    final = "stop 파일 → 중단"
                    break
                if self.a.until and datetime.datetime.now() >= self.a.until:
                    final = f"--until {self.a.until:%Y-%m-%d %H:%M} 지남 → 새 후보 시작 안 함"
                    break
                if item is None:
                    item = self.next_item()
                    if item is None and self.a.only:
                        final = "--only 로 지정한 후보 시험 끝"
                        break
                    if item is None:
                        final = ("후보를 모두 시험했지만 통과한 후보 없음"
                                 + ("" if self.a.no_auto else " (자동 탐색할 이웃도 없음)"))
                        break
                    self._save()
                name = item["name"]
                res = self.run_variant(item)
                if res["status"] == "INTERRUPTED":
                    self.log(f"{name}: 사용자 중단 (--resume 으로 이 후보부터 다시)")
                    final = f"사용자 중단 ({name})"
                    break
                if res["status"] == "MISMATCH":
                    self.record(res)
                    final = (f"!! {name}: 패치한 값과 부팅 로그의 DDR 설정이 다름 → 빌드/다운로드가 반영 안 된 것 같아 "
                             f"중단 ({res.get('verify')})")
                    break
                if res["status"] == "ERROR":
                    # 빌드/다운로드/장비 문제 - DDR 결과가 아니므로 기록하지 않고 잠시 뒤 같은 후보 재시도
                    errors += 1
                    self.st.setdefault("errors", []).append({**res, "attempt": errors})
                    self._save()
                    self.log(f"{name}: {res['status']} ({errors}번째 연속) {res.get('reason', '')}")
                    if errors >= self.a.max_errors:
                        final = (f"ERROR {errors}번 연속 → 중단 (Tapo/USB/빌드 확인 후 --resume). "
                                 f"마지막: {res.get('reason', '')}")
                        break
                    self.log(f"  {self.a.error_wait}초 기다렸다가 같은 후보를 다시 시도")
                    time.sleep(self.a.error_wait)
                    continue
                errors = 0
                if confirm_left and res["status"] == "PASS":
                    res["variant"] = name = f"{name}#confirm{self.a.confirm - confirm_left + 1}"
                self.record(res)
                n_new += 1
                self.log(f"{name}: {res['status']} {res.get('reboot', '')} {res.get('reason', '')}")
                if (res["status"] == "FAIL" and res.get("cp_err") and item.get("settings")
                        and not item.get("cp_retry") and not self.a.no_cp_retry):
                    self.log(f"  CP decompress error 로 실패 (DDR 이 아닐 수도 있음) → 같은 설정으로 한 번 더")
                    item = {**item, "name": f"{name}_cp", "cp_retry": True,
                            "why": f"{name} 이 CP decompress error 로 실패해서 같은 설정 재시험"}
                    continue
                if res["status"] == "PASS" and self.a.only:
                    item = None                       # --only: 통과해도 나머지 후보 계속
                    continue
                if res["status"] == "PASS":
                    if confirm_left == 0 and self.a.confirm:
                        confirm_left = self.a.confirm
                        self.log(f"  통과 → 같은 설정으로 {self.a.confirm}번 더 확인")
                        continue                     # item 그대로 재시험
                    if confirm_left > 1:
                        confirm_left -= 1
                        continue
                    base = name.split("#")[0]
                    final = (f"★ 통과: {base} (부팅 로그 설정 확인: {res.get('verify', '-')}) - reboot {self.st['reboots']}번 연속 성공"
                             + (f" x {self.a.confirm + 1}회" if self.a.confirm else "")
                             + f"\n  설정: {kn.key(res.get('settings') or {}) if res.get('settings') else '-'}"
                             f"\n  패치: {os.path.join(self.dir, base, base + '.patch')}")
                    break
                confirm_left = 0
                item = None
        finally:
            self.restore()                         # SBL 소스는 항상 원본으로 돌려둔다
            self.log("SBL 소스 원본으로 복원")
            self._save()
            self.power_off()                       # 끝나면(통과/실패/중단/에러) 항상 전원 OFF
        for ln in self.write_summary(final):
            print(ln)
        return 0 if final.startswith("★") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sbl", default=DEFAULT_SBL, help=f"SBL 소스 폴더 (기본 {DEFAULT_SBL})")
    ap.add_argument("--variants", default=os.path.join(ROOT, "sdram", "variants"),
                    help="후보 패치 폴더 (기본 sdram/variants)")
    ap.add_argument("--files", nargs="+", default=DEFAULT_FILES, help="후보가 고치는 파일 (원본 보관/복원 대상)")
    ap.add_argument("--reboots", type=int, help="후보당 kern reboot 횟수 (기본 100, --resume 때 주면 이후 후보부터 변경)")
    ap.add_argument("--preset", default="sdram-sbl-cycle")
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--resume", metavar="DIR", help="이전 캠페인 폴더에서 이어가기")
    ap.add_argument("--no-auto", action="store_true", help="목록을 다 쓰면 끝 (자동 후보 생성 안 함)")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="이 후보들만 차례로 시험 (통과해도 계속, 자동 후보 없음) - 수정/빌드 확인용")
    ap.add_argument("--seed", metavar="'k=v,...'",
                    help="자동 탐색 시작 설정. 원본에서 이 값만 바꿈. 예: 'dsvref=0x20,pdds=1,adj=test-5'")
    ap.add_argument("--explore", type=lambda t: [x.strip() for x in t.split(",") if x.strip() and x.strip() != "none"],
                    metavar="k1,k2", help=f"자동 탐색에서 바꿀 손잡이만 (기본 전부: {','.join(kn.KNOBS)}). "
                                         "'none' 이면 이웃 탐색 없이 --seed 만 (--repeat 횟수만큼) 돌림")
    ap.add_argument("--build-args", metavar="'K=V ...'",
                    help="SBL 빌드에 추가로 줄 make 변수 (예: 'DDRRATE=3200'). 소스 파일은 안 바꿈. 캠페인 폴더에 저장")
    ap.add_argument("--max-new", type=int, help="이번 실행에서 이만큼 후보 결과가 쌓이면 멈춤")
    ap.add_argument("--skip", metavar="'k=v,...'", help="자동 탐색 이웃에서 뺄 값 (예: 'vddq=700'). 캠페인 폴더에 저장")
    ap.add_argument("--repeat", type=int, default=2,
                    help="자동 탐색에서 1등 설정을 이 횟수가 될 때까지 반복해 평균을 냄 (기본 2)")
    ap.add_argument("--confirm", type=int, default=0,
                    help="통과하면 같은 설정으로 몇 번 더 재확인할지 (기본 0 = 첫 통과에서 끝)")
    ap.add_argument("--max-errors", type=int, default=6,
                    help="빌드/다운로드/장비 ERROR 가 이만큼 연속되면 중단 (기본 6)")
    ap.add_argument("--error-wait", type=int, default=300, help="ERROR 뒤 재시도까지 대기 초 (기본 300)")
    ap.add_argument("--until", metavar="'YYYY-MM-DD HH:MM'",
                    type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M"),
                    help="이 시각이 지나면 새 후보를 시작하지 않음")
    ap.add_argument("--no-power-off", action="store_true", help="끝날 때 보드 전원을 끄지 않음")
    ap.add_argument("--no-cp-retry", action="store_true",
                    help="CP decompress error 로 실패해도 같은 설정을 다시 돌리지 않음")
    ap.add_argument("--full-log", action="store_true", help="후보마다 전체 콘솔 로그 저장 (기본: 실패 구간만)")
    ap.add_argument("--extra-patch", metavar="PATCH", help="모든 후보에 설정과 함께 얹는 패치 (git apply). 캠페인 폴더에 저장")
    ap.add_argument("--var", action="append", metavar="KEY=VALUE",
                    help="프리셋 변수 추가 지정 (여러 번 가능, 예: --var dl_retries=10 --var flash_sbl2=0)")
    ap.add_argument("--run-cmd", help=argparse.SUPPRESS)   # 시험용
    a = ap.parse_args(argv)
    if not a.resume and os.path.abspath(a.sbl) != DEFAULT_SBL:
        print(f"주의: --sbl 이 프리셋({a.preset})의 빌드 경로 {DEFAULT_SBL} 와 다릅니다", file=sys.stderr)
    # 터미널이 닫히거나(SIGHUP) kill(SIGTERM) 돼도 finally(원본 복원, 전원 OFF)가 돌도록 예외로 바꾼다
    def _sig(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt(f"signal {signum}")
    for s in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(s, _sig)
        except (AttributeError, ValueError):
            pass
    return Campaign(a).run()


if __name__ == "__main__":
    sys.exit(main())
