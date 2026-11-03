#!/usr/bin/env python3
"""이미지 빌드 + fastboot 기록 - docs/TEST_SPEC.md 2장

  bash gbt.sh build <이미지...>                     빌드만 (config/images.json)
  bash gbt.sh flash <보드> <이미지...> [--no-build]  빌드 → 그 보드에 기록 → 부팅 로그로 버전 확인
  옵션: --set <이미지>.<make변수>=<값>  (여러 번 가능. 예: --set sbl.AFE_POWER_DOWN=n --set sbl.DDRRATE=2662)
        --preload <파일>               preload 직접 지정 (기본: config/images.json _preload 의 보드 종류별 값)
        --patch <이미지>:<패치 파일>    빌드할 때만 적용하고 끝나면 되돌림 (여러 번 가능). 패치 머리말의 gbt-expect: 글자를 부팅 로그에서 확인

flash 순서
  1) rig.acquire(보드)  - 다른 도구가 쓰는 중이면 시작 안 함
  2) 이미지마다 빌드 (--no-build 면 건너뜀). 결과 파일이 새로 생겨야 함
  3) fastboot 진입 (Tapo OFF→ON → 'u' 연타 → usb → usb_dnl_fastboot: ready)
  4) fastboot --preload <보드 종류별 preload> boot  ("OKAY")
  5) 파일마다 fastboot flash <파티션> <파일>  ("Writing '<파티션>' ... OKAY")   - 실패하면 3)부터 다시 (최대 --tries)
  6) 전원 껐다 켜서 "Starting kernel" 까지의 부팅 로그에서 이미지별 커밋을 읽어 소스 HEAD 와 비교
  7) 전원은 켠 채로 둠. 로그: logs/flash/<시각>_<보드>_<이미지>.log
"""
import argparse
import datetime as dt
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from gbt import images, rig  # noqa: E402
from gbt.board import Board, BoardError  # noqa: E402
from gbt.util import log, set_logfile  # noqa: E402


def parse_sets(sets):
    out = {}
    for s in sets or []:
        m = re.match(r"([^.=]+)\.([A-Za-z_][A-Za-z0-9_]*)=(.*)$", s)
        if not m:
            sys.exit(f"--set 형식: <이미지>.<변수>=<값> (받은 것: {s})")
        out.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    return out


def parse_patches(items):
    out = {}
    for s in items or []:
        if ":" not in s:
            sys.exit(f"--patch 형식: <이미지>:<패치 파일> (받은 것: {s})")
        n, p = s.split(":", 1)
        if not os.path.exists(images.expand(p)):
            sys.exit(f"패치 파일 없음: {p}")
        out.setdefault(n, []).append(p)
    return out


def flash(board, names, no_build, tries, sets=None, preload=None, patches=None):
    for n in names:
        if n not in images.names():
            sys.exit(f"config/images.json 에 이미지 {n} 없음 (있는 것: {', '.join(images.names())})")
    b = rig.lookup(board)
    if not b.get("fastboot"):
        sys.exit(f"보드 {board} 의 fastboot USB 를 모름 (rig.json) → bash gbt.sh discover")
    sets, patches = sets or {}, patches or {}
    if no_build and patches:
        sys.exit("--patch 는 빌드할 때 적용하는 것이라 --no-build 와 같이 못 씀")
    for n in list(sets) + list(patches):
        if n not in names:
            sys.exit(f"--set / --patch 의 이미지 {n} 은 이번에 기록할 이미지({' '.join(names)})가 아님")
    pre = images.expand(preload) if preload else images.preload_for(b.get("type_guess"))
    if not pre:
        sys.exit(f"보드 {board} 종류({b.get('type_guess')}) 에 맞는 preload 가 config/images.json _preload 에 없음 → --preload <파일>")
    if not os.path.exists(pre):
        sys.exit(f"preload 없음: {pre}" + (" → bash sdram/build_winbond_preload.sh" if "winbond" in pre else ""))
    os.makedirs("logs/flash", exist_ok=True)
    lp = f"logs/flash/{dt.datetime.now():%Y%m%d_%H%M%S}_{board}_{'_'.join(names)}.log"
    set_logfile(lp)
    try:
        lock = rig.acquire(board, f"기록 {' '.join(names)}" + "".join(f" {n}.{k}={v}" for n, kv in sets.items() for k, v in kv.items())
                           + "".join(f" {n}+{os.path.basename(p)}" for n, ps in patches.items() for p in ps))
    except rig.Busy as e:
        sys.exit(str(e))
    with lock:
        log(f"보드 {board}: Tapo {b['tapo']} / UART {b['uart']} / fastboot {b['fastboot']} / {b.get('type_guess')} / preload {pre}")
        if not no_build:
            for n in names:
                if not images.build(n, sets.get(n), patches.get(n)):
                    sys.exit(1)
        files = [fp for n in names for fp in images.files(n)]
        for part, p in files:
            if not os.path.exists(p):
                sys.exit(f"기록할 파일 없음: {p}")
        bd = Board(board, b)
        done = False
        for t in range(1, tries + 1):
            log(f"기록 시도 {t}/{tries}", "info")
            try:
                bd.enter_fastboot()
            except BoardError as e:
                log(f"fastboot 진입 실패: {e}", "err")
                continue
            time.sleep(2)
            if not bd.fastboot(["--preload", pre, "boot"], r"OKAY", 30):
                log("preload 부팅 실패", "err")
                continue
            time.sleep(2)
            ok = True
            for part, p in files:
                if not bd.fastboot(["flash", part, p], rf"Writing '{re.escape(part)}'[^\n]*OKAY", 120):
                    log(f"{part} 기록 실패", "err")
                    ok = False
                    break
            if ok:
                done = True
                break
        if not done:
            bd.power(False)
            log(f"기록 실패 ({tries}번) → 전원 OFF. 로그 {lp}", "err")
            sys.exit(1)
        log("기록 완료 → 전원 껐다 켜서 버전 확인", "ok")
        text = bd.boot_log()
        allok = True
        for n in names:
            _, _, head = images.git_head(images.expand(images.load()[n]["dir"]))
            m = re.findall(images.load()[n]["version_re"], text)
            got = m[-1] if m else None
            same = bool(got and head and head.startswith(got.replace("-dirty", "")[:7]))
            allok &= same
            log(f"   {n}: 부팅 로그 {got or '(못 읽음)'} / 소스 HEAD {head[:8] or '?'} → {'일치' if same else '불일치 - 기록이 반영 안 됐을 수 있음'}", "ok" if same else "err")
        for n in names:
            for exp in images.patch_expects(patches.get(n)):
                seen = exp in text
                allok &= seen
                log(f"   {n} 패치 확인: '{exp}' → {'부팅 로그에 있음' if seen else '부팅 로그에 없음 - 패치가 반영 안 됐을 수 있음'}", "ok" if seen else "err")
        log(("완료" if allok else "기록은 OK 였지만 버전 확인 실패") + f". 전원은 켠 채로 둠. 로그 {lp}", "ok" if allok else "err")
        sys.exit(0 if allok else 2)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build")
    pb.add_argument("images", nargs="+")
    pf = sub.add_parser("flash")
    pf.add_argument("board")
    pf.add_argument("images", nargs="+")
    pf.add_argument("--no-build", action="store_true")
    pf.add_argument("--tries", type=int, default=3)
    pf.add_argument("--set", action="append", default=[], help="<이미지>.<make변수>=<값>")
    pf.add_argument("--preload", help="preload 파일 직접 지정")
    pb.add_argument("--set", action="append", default=[], help="<이미지>.<make변수>=<값>")
    pb.add_argument("--patch", action="append", default=[], help="<이미지>:<패치 파일>")
    pf.add_argument("--patch", action="append", default=[], help="<이미지>:<패치 파일>")
    a = ap.parse_args()
    sets, patches = parse_sets(a.set), parse_patches(a.patch)
    if a.cmd == "build":
        sys.exit(0 if all(images.build(n, sets.get(n), patches.get(n)) for n in a.images) else 1)
    flash(a.board, a.images, a.no_build, a.tries, sets, a.preload, patches)


if __name__ == "__main__":
    main()
