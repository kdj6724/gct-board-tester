"""보드(장비 페어) 정보와 사용 중 관리 - 모든 도구가 이 모듈을 통해서만 보드를 쓴다 (docs/TEST_SPEC.md 0장)

  장비 정보 : rig.json (tools/discover_rig.py 가 만듦)  - 보드 이름 → Tapo / UART / fastboot / 보드 정보
  사용 중   : logs/locks/<보드>.lock 에 flock + 누가 쓰는지 JSON 기록. 프로세스가 죽으면 OS 가 lock 을 풀어줌

  사용 예:
      from gbt import rig
      b = rig.lookup("08")                     # {'tapo': ..., 'uart': ..., 'fastboot': ...}
      with rig.acquire("08", "nokia sweep"):   # 다른 도구가 쓰는 중이면 rig.Busy 예외
          ...                                  # 여기서만 Tapo 켜고 끄기 / UART 열기
      for row in rig.status(): ...             # 전체 현황 (gbt status)

  기존 스크립트(sdram/run_dds_sweep.sh 등)는 아직 logs/.lock_B<자리> + sdram/boards.conf 를 쓴다.
  옮기기 전까지는 그 lock 도 "사용 중" 으로 같이 본다 (legacy_busy).
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RIG_JSON = os.path.join(ROOT, "rig.json")
LOCK_DIR = os.path.join(ROOT, "logs", "locks")
BOARDS_CONF = os.path.join(ROOT, "sdram", "boards.conf")


class Busy(Exception):
    """보드를 다른 도구가 쓰는 중"""

    def __init__(self, name, info):
        self.name, self.info = name, info
        super().__init__(f"보드 {name} 사용 중: {describe(info)}")


def describe(info):
    if not info:
        return "?"
    s = info.get("purpose", "?")
    if info.get("pid"):
        s += f" (pid {info['pid']}"
        if info.get("since"):
            s += f", {info['since'][5:16].replace('T', ' ')}부터"
        s += ")"
    return s


# ------------------------------------------------------------------ 장비 정보
def load(path=RIG_JSON):
    """rig.json 전체 (없으면 빈 구조)"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"pairs": {}, "unmatched": {}}


def boards():
    return load().get("pairs", {})


def lookup(name):
    b = boards().get(name)
    if not b:
        raise KeyError(f"rig.json 에 보드 {name} 없음 → tools/discover_rig.py 로 페어를 먼저 찾기")
    return b


# ------------------------------------------------------------------ 사용 중 관리
def _lock_path(name):
    return os.path.join(LOCK_DIR, f"{name}.lock")


class Lock:
    def __init__(self, name, fd):
        self.name, self.fd = name, fd

    def release(self):
        if self.fd is not None:
            try:
                os.ftruncate(self.fd, 0)
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def acquire(name, purpose, force_unknown=False):
    """보드 lock 을 잡는다. 이미 누가 쓰면 Busy. rig.json 에 없는 이름은 force_unknown=True 일 때만"""
    if not force_unknown and name not in boards():
        raise KeyError(f"rig.json 에 보드 {name} 없음")
    lb = legacy_busy().get(name)
    if lb:
        raise Busy(name, lb)
    os.makedirs(LOCK_DIR, exist_ok=True)
    fd = os.open(_lock_path(name), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        info = _read_info(fd)
        os.close(fd)
        raise Busy(name, info) from None
    info = {"purpose": purpose, "pid": os.getpid(), "since": _dt.datetime.now().isoformat(timespec="seconds")}
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(info, ensure_ascii=False).encode())
    os.fsync(fd)
    return Lock(name, fd)


def _read_info(fd):
    try:
        os.lseek(fd, 0, 0)
        return json.loads(os.read(fd, 4096).decode() or "{}")
    except (OSError, ValueError):
        return {}


def who(name):
    """보드를 쓰는 중이면 그 정보(dict), 비어 있으면 None"""
    lb = legacy_busy().get(name)
    if lb:
        return lb
    p = _lock_path(name)
    if not os.path.exists(p):
        return None
    fd = os.open(p, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        except OSError:
            return _read_info(fd) or {"purpose": "?"}
    finally:
        os.close(fd)


# ------------------------------------------------------------------ 기존 스크립트 lock (옮기기 전까지)
def boards_conf(path=BOARDS_CONF):
    rows = {}
    try:
        for line in open(path, encoding="utf-8"):
            f = line.split("#", 1)[0].split()
            if len(f) >= 4:
                rows[f[0]] = {"uart": f[1], "tapo": f[2], "fastboot": f[3]}
    except OSError:
        pass
    return rows


def legacy_busy():
    """logs/.lock_B<자리> 가 잡혀 있는 기존 시험 → {rig 보드 이름: info}. 자리(boards.conf)의 Tapo 로 rig 이름을 찾음"""
    conf, pairs, out = boards_conf(), boards(), {}
    for f in glob.glob(os.path.join(ROOT, "logs", ".lock_B*")):
        try:
            fd = os.open(f, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            continue
        except OSError:
            pass
        finally:
            os.close(fd)
        slot = os.path.basename(f)[len(".lock_B"):]
        e = conf.get(slot)
        name = None
        if e:
            name = next((n for n, b in pairs.items() if b.get("tapo") == e["tapo"]), None)
        info = {"purpose": f"기존 시험 스크립트 (lock B{slot})", "legacy": True,
                "tapo": e and e["tapo"], "uart": e and e["uart"], "fastboot": e and e["fastboot"]}
        out[name or f"(자리 {slot})"] = info
    return out


# ------------------------------------------------------------------ 현황
def status():
    """[(이름, 보드 정보, 사용 중 info 또는 None)] - rig.json 에 없는 기존 시험도 포함"""
    rows = []
    pairs = boards()
    for name in sorted(pairs):
        rows.append((name, pairs[name], who(name)))
    for name, info in legacy_busy().items():
        if name not in pairs:
            rows.append((name, {"tapo": info.get("tapo"), "uart": info.get("uart"), "fastboot": info.get("fastboot")}, info))
    return rows
