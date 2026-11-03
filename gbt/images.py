"""이미지 등록 표(config/images.json)와 빌드 - docs/TEST_SPEC.md 2장"""
import datetime as dt
import json
import os
import re
import subprocess
import time

from gbt.util import log, out_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_JSON = os.path.join(ROOT, "config", "images.json")


def load():
    with open(IMAGES_JSON, encoding="utf-8") as f:
        return json.load(f)


def names():
    return [k for k in load() if not k.startswith("_")]


def expand(p):
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def preload_for(board_type):
    p = load().get("_preload", {}).get(board_type or "")
    return expand(p) if p else None


def files(name):
    """[(fastboot 파티션, 절대 경로)]"""
    img = load()[name]
    d = expand(img["dir"])
    return [(part, os.path.join(d, f)) for part, f in img["files"]]


def git_head(d):
    r = subprocess.run(["git", "-C", d, "log", "-1", "--format=%h %ci %s"], capture_output=True, text=True)
    st = subprocess.run(["git", "-C", d, "status", "--short", "-uno"], capture_output=True, text=True).stdout.strip()
    full = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return r.stdout.strip(), st, full


def patch_expects(patches):
    """패치 파일 머리말의 'gbt-expect: <글자>' → 부팅 로그에 나와야 하는 글자 목록"""
    out = []
    for p in patches or []:
        for ln in open(expand(p), encoding="utf-8", errors="replace"):
            if ln.startswith("diff ") or ln.startswith("--- "):
                break
            if ln.startswith("gbt-expect:"):
                out.append(ln.split(":", 1)[1].strip())
    return out


def source_lock(d):
    """소스 폴더 lock - 기존 sdram 스크립트(board_env.sh)와 같은 파일 이름이라 sweep 과도 서로 막힘"""
    import fcntl
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    path = os.path.join(ROOT, "logs", ".lock_sbl_" + d.replace("/", "_").replace(" ", "_"))
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def build(name, make_vars=None, patches=None):
    """빌드하고 결과 파일이 새로 생겼는지 확인. 성공하면 True
    make_vars: {"AFE_POWER_DOWN": "n", ...} → 빌드 명령 뒤에 VAR=VAL 로 붙임 (make 명령줄 값이 Makefile 값을 덮어씀)
    patches: [패치 파일, ...] → 빌드 전에 git apply, 빌드 끝나면(실패해도) git apply -R 로 되돌림. 소스는 깨끗하게 유지"""
    img = load()[name]
    d = expand(img["dir"])
    patches = [expand(p) for p in (patches or [])]
    fd = source_lock(d)
    if fd is None:
        log(f"소스 폴더 {d} 를 다른 시험(빌드/sweep)이 쓰는 중 → 끝난 뒤 다시", "err")
        return False
    applied = []
    try:
        for p in patches:
            if subprocess.call(["git", "-C", d, "apply", "--check", p]) != 0:
                log(f"패치가 지금 소스에 안 맞음: {p}", "err")
                return False
            subprocess.check_call(["git", "-C", d, "apply", p])
            applied.append(p)
            log(f"패치 적용: {os.path.relpath(p, ROOT)}", "warn")
        return _build(name, img, d, make_vars or {})
    finally:
        for p in reversed(applied):
            rc = subprocess.call(["git", "-C", d, "apply", "-R", p])
            log(f"패치 되돌림: {os.path.relpath(p, ROOT)}" if rc == 0 else f"패치 되돌리기 실패 → 직접 확인: git -C {d} status", "mute" if rc == 0 else "err")
        os.close(fd)


def _build(name, img, d, make_vars):
    cmd = img["build"] + "".join(f" {k}={v}" for k, v in make_vars.items())
    head, dirty, _ = git_head(d)
    log(f"빌드 {name}: {d}  ({head}){'  ※ 커밋 안 된 변경 있음' if dirty else ''}", "warn" if dirty else "info")
    for ln in dirty.splitlines():
        log(f"   {ln}", "warn")
    t0 = time.time()
    os.makedirs(os.path.join(ROOT, "logs", "flash"), exist_ok=True)
    blog = os.path.join(ROOT, "logs", "flash", f"{dt.datetime.now():%Y%m%d_%H%M%S}_build_{name}.log")
    log(f"$ cd {d} && {cmd}", "info")
    with open(blog, "w") as bf:      # 빌드 출력을 화면에 바로 보여주면서 파일에도 남김
        pr = subprocess.Popen(cmd, shell=True, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", bufsize=1)
        for ln in pr.stdout:
            out_line(ln.rstrip("\n"), bf)
        rc = pr.wait()
    if rc != 0:
        log(f"빌드 실패 (rc={rc}) → {blog}", "err")
        return False
    # 넘긴 변수가 실제로 먹었는지: SBL Makefile 은 켜진 옵션을 "Makefile:NNN: 이름" 으로 찍음 (=n 인데 찍히면 안 먹은 것)
    text = open(blog, errors="replace").read()
    for k, v in make_vars.items():
        shown = re.search(rf"Makefile:\d+: {re.escape(k)}(=(\S*))?\s*$", text, re.M)
        if v in ("n", "", "0") and shown and not shown.group(2):
            log(f"{k}={v} 로 넘겼는데 빌드 로그에 '{shown.group(0).strip()}' 가 찍힘 → 안 꺼졌음", "err")
            return False
        if shown and shown.group(2) is not None and shown.group(2) != v:
            log(f"{k}={v} 로 넘겼는데 빌드 로그에는 '{shown.group(0).strip()}'", "err")
            return False
        log(f"   {k}={v} 확인 ({'빌드 로그에 ' + shown.group(0).strip() if shown else '빌드 로그에 안 찍힘 = 꺼짐'})", "ok")
    for part, p in files(name):
        if not os.path.exists(p) or os.path.getmtime(p) < t0:
            log(f"빌드는 끝났는데 결과 파일이 새로 안 생김: {p} → {blog}", "err")
            return False
        log(f"   → {part}: {p} ({os.path.getsize(p)} bytes)", "ok")
    log(f"빌드 OK ({time.time() - t0:.0f}초, 로그 {blog})", "ok")
    return True
