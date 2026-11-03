#!/usr/bin/env python3
"""장비 자동 구분 (페어 찾기) - docs/TEST_SPEC.md 1단계

연결된 Tapo 플러그 / UART / fastboot USB 가 어떻게 짝지어져 있는지 자동으로 찾아서
페어마다 이름(= 그 페어에 꽂힌 보드 이름)을 붙이고 rig.json 에 저장한다.

순서
  1) Tapo 전부 OFF → 전부 ON : 부팅 출력이 올라오는 UART 를 확인 (참고용, 후보에서 빼지는 않음)
  2) 전부 OFF
  3) 남은 Tapo 를 하나씩 ON
       - 남은 UART 전부에 'u' 연타 → "total 3 char" 가 뜨는 UART = 그 Tapo 의 짝
       - 그 UART 에 "usb" → "usb_dnl_fastboot: ready" → fastboot devices -l 에 새로 생긴 usb:경로 = 짝
       - 전원 껐다 켜서 정상 부팅 출력에서 PBL / SBL / U-Boot 버전, 채널 수, DDR 속도 읽기
       - 찾은 Tapo / UART / USB 는 목록에서 빼고 다음 Tapo
  4) 페어마다 이름 입력: 이름을 입력할 때까지 그 페어의 Tapo 를 계속 껐다 켬 (켜짐 4초/꺼짐 3초) (전에 같은 자리 + 같은 PBL 이면 그 이름을 기본값으로 제안)
  5) rig.json 저장 + logs/rig/<시각>.json 에 사본, 전원은 전부 OFF
  시험 중인 보드(logs/.lock_B* 가 잡힘, 또는 다른 프로그램이 UART 를 연 것)는 건드리지 않음:
    그 Tapo 는 끄지/켜지 않고, 그 UART 에는 아무것도 안 보내고, rig.json 에는 이전 값을 "in_use" 로 남김
    (어느 Tapo 인지 sdram/boards.conf / rig.json 으로 알 수 없으면 안전을 위해 시작하지 않음)

사용 (gct-board-tester 폴더에서):
  ~/.venv-gbt/bin/python tools/discover_rig.py
  옵션: --tapo-file tools/rig_tapo.txt  --out rig.json  --keep-names (묻지 않고 이전 이름/자리 이름 사용)
        --blink-on 4 --blink-off 3 (이름 물을 때 깜빡임 간격, 이름을 입력할 때까지 계속)
  진단: --probe <Tapo IP>  그 Tapo 만 껐다 켜고 모든 UART 출력을 보여줌 (921600)
Tapo 계정은 remote_power.env 의 TAPO_EMAIL / TAPO_PASSWORD
"""
import argparse
import datetime as dt
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import serial  # noqa: E402

import config_manager as cm  # noqa: E402
import rig  # noqa: E402
from tapo_control import TapoController  # noqa: E402

FASTBOOT = "/home/test/bin/fastboot"
BAUD = 921600


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- 준비
def busy_test_locks():
    """돌고 있는 시험(보드 lock 을 잡은 것)이 있으면 그 이름 목록"""
    busy = []
    for f in glob.glob("logs/.lock_B*"):
        try:
            fd = os.open(f, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                busy.append(os.path.basename(f)[len(".lock_"):])
            finally:
                os.close(fd)
        except OSError:
            pass
    return busy


def uart_users():
    """다른 프로세스가 열어 둔 tty: {실제 장치 경로: ["pid 명령", ...]}"""
    me, users = os.getpid(), {}
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = int(fd_dir.split("/")[2])
        if pid == me:
            continue
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                tgt = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if tgt.startswith("/dev/tty"):
                try:
                    cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
                except OSError:
                    cmd = "?"
                users.setdefault(tgt, []).append(f"{pid} {cmd[:60]}")
    return users


def boards_conf(path="sdram/boards.conf"):
    """기존 시험 스크립트의 보드 자리 표: {이름: {uart, tapo, fastboot}}"""
    rows = {}
    try:
        for line in open(path, encoding="utf-8"):
            f = line.split("#", 1)[0].split()
            if len(f) >= 4:
                rows[f[0]] = {"uart": f[1], "tapo": f[2], "fastboot": f[3]}
    except OSError:
        pass
    return rows


def read_tapo_list(path):
    ips = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                ips.append(line)
    return ips


def fastboot_paths():
    try:
        out = subprocess.run([FASTBOOT, "devices", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        log(f"fastboot 실행 실패: {e}")
        return set()
    return set(re.findall(r"\b(usb:\S+)", out))


class Port:
    def __init__(self, path):
        self.path = path
        self.ser = None
        self.err = ""
        self.buf = ""

    def open(self):
        try:
            self.ser = serial.Serial(self.path, BAUD, timeout=0, exclusive=True)
        except Exception as e:  # noqa: BLE001
            self.err = str(e)
        return self.ser is not None

    def flush(self):
        try:
            self.ser.reset_input_buffer()
        except Exception:  # noqa: BLE001
            pass
        self.buf = ""

    def poll(self):
        try:
            n = self.ser.in_waiting
            if n:
                self.buf += self.ser.read(n).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return self.buf

    def write(self, b):
        try:
            self.ser.write(b)
        except Exception:  # noqa: BLE001
            pass


def short(path):
    return path.replace("/dev/serial/by-path/", "")


# ---------------------------------------------------------------- 부팅 출력 해석
def board_info(text):
    info = {}
    m = re.search(r"PBL: (\S+ \S+) (\S+?)\(", text)
    if m:
        info["pbl"] = f"{m.group(2)} ({m.group(1)})"
    m = re.search(r"SBL: (\S+ \S+) (\S+?)\(", text)
    if m:
        info["sbl"] = f"{m.group(2)} ({m.group(1)})"
    m = re.search(r"U-Boot: (\S+ \S+) (\S+?)\(", text)
    if m:
        info["uboot"] = f"{m.group(2)} ({m.group(1)})"
    m = re.search(r"CFG=(\d+) ch=(\d+)", text)
    if m:
        info["ch"] = int(m.group(2))
        info["type_guess"] = {2: "winbond-2ch", 1: "keymos-1ch"}.get(info["ch"], "?")
    m = re.search(r"ddr=(\d+) vdd1=\S+ vdd2=\S+ vddq=(\S+)", text)
    if m:
        info["ddr"] = int(m.group(1))
        info["vddq"] = m.group(2)
    return info


# ---------------------------------------------------------------- 진단
def probe(ip, email, pw, skip_real=()):
    """Tapo 하나만 OFF → ON 하면서 모든 UART 에 무엇이 들어오는지 보여준다 (921600)"""
    t = TapoController()
    ok, msg = t.connect(ip, email, pw)
    if not ok:
        sys.exit(f"Tapo {ip} 연결 실패: {msg}")
    uniq = {}
    for p in sorted(glob.glob("/dev/serial/by-path/*"), key=lambda s: ("usbv2" in s, s)):
        uniq.setdefault(os.path.realpath(p), p)
    for baud in (BAUD,):
        log(f"Tapo {ip} OFF → ON, 10초 동안 UART 읽기 (보드레이트 {baud})")
        t.set_power(False)
        time.sleep(3)
        ports = {}
        for p in sorted(uniq.values()):
            if os.path.realpath(p) in skip_real:
                log(f"   {short(p)}: 시험 중이라 건너뜀")
                continue
            try:
                ports[p] = serial.Serial(p, baud, timeout=0, exclusive=True)
                ports[p].reset_input_buffer()
            except Exception as e:  # noqa: BLE001
                log(f"   {short(p)}: 열기 실패 ({e})")
        data = {p: b"" for p in ports}
        t.set_power(True)
        t_end = time.time() + 10
        while time.time() < t_end:
            for p, s in ports.items():
                try:
                    n = s.in_waiting
                    if n:
                        data[p] += s.read(n)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.05)
        for p, s in ports.items():
            s.close()
            d = data[p]
            txt = d.decode("utf-8", errors="replace").replace("\r", "")
            head = " | ".join(x.strip() for x in txt.split("\n") if x.strip())[:160]
            log(f"   {short(p)}: {len(d)} bytes  {head}")
    t.set_power(False)
    log("전원 OFF. 출력이 하나도 없으면: 그 보드의 UART 선 위치 / TX-RX / GND, 또는 보드 전원(플러그에 꽂힌 게 맞는지) 확인")


# ---------------------------------------------------------------- 본체
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapo-file", default="tools/rig_tapo.txt")
    ap.add_argument("--out", default="rig.json")
    ap.add_argument("--keep-names", action="store_true", help="이름을 묻지 않음 (이전 이름 또는 S1, S2 ...)")
    ap.add_argument("--probe", metavar="TAPO_IP", help="진단: 이 Tapo 하나만 껐다 켜고 모든 UART 에 들어오는 출력을 보여줌 (921600)")
    ap.add_argument("--blink-on", type=float, default=4.0, help="켜 두는 시간 초 (기본 4)")
    ap.add_argument("--blink-off", type=float, default=3.0, help="꺼 두는 시간 초 (기본 3)")
    a = ap.parse_args()

    # ---- 시험 중인 보드는 건드리지 않음 (그 Tapo 는 끄지 않고, 그 UART 에는 아무것도 안 보냄) ----
    prev = {}
    if os.path.exists(a.out):
        try:
            prev = json.load(open(a.out, encoding="utf-8")).get("pairs", {})
        except Exception:  # noqa: BLE001
            prev = {}
    conf = boards_conf()
    busy = {}  # 이름 → {tapo, uart, fastboot, why}

    def _rig_name(tapo, uart):
        for n, e in prev.items():
            if e.get("tapo") == tapo or (uart and os.path.realpath(e.get("uart", "")) == os.path.realpath(uart)):
                return n
        return None

    # 보드 사용 중 여부는 rig.py 한 곳에서 (rig.json 보드 lock + 기존 스크립트 lock)
    for n, b, info in rig.status():
        if info:
            busy[n] = {"tapo": b.get("tapo"), "uart": b.get("uart"), "fastboot": b.get("fastboot"), "why": rig.describe(info)}
    held = []  # 찾는 동안 다른 시험이 끼어들지 못하게, 비어 있는 기존 보드는 lock 을 잡아 둠
    for n in prev:
        if n in busy:
            continue
        try:
            held.append(rig.acquire(n, "장비 자동 구분 (discover_rig)"))
        except rig.Busy as e:
            busy[n] = {"tapo": prev[n].get("tapo"), "uart": prev[n].get("uart"), "fastboot": prev[n].get("fastboot"),
                       "why": rig.describe(e.info)}
    for n, b in list(busy.items()):
        if not b.get("tapo") or not b.get("uart"):
            sys.exit(f"보드 {n} 이 사용 중인데 Tapo/UART 를 모름 ({b['why']}) → 그 시험을 멈춘 뒤 다시 실행")
    # 추가 안전장치: lock 없이 UART 를 연 프로그램(picocom 등)
    users = uart_users()
    for p in glob.glob("/dev/serial/by-path/*"):
        real = os.path.realpath(p)
        if real not in users or any(os.path.realpath(b["uart"]) == real for b in busy.values()):
            continue
        # 이전 rig.json(실제로 찾은 값)을 먼저 보고, 없을 때만 boards.conf. boards.conf 에서 한 UART 가 Tapo 여러 개에
        # 걸려 있으면(예: 01 과 N 이 같은 13:1.0) 어느 쪽인지 모르므로 멈춤 - 엉뚱한 Tapo 를 끄면 시험이 죽음
        cand = {e["tapo"] for e in prev.values() if os.path.realpath(e.get("uart", "")) == real}
        if not cand:
            cand = {e["tapo"] for e in conf.values() if os.path.realpath(e.get("uart", "")) == real}
        tapo = cand.pop() if len(cand) == 1 else None
        if not tapo:
            sys.exit(f"UART {short(p)} 를 다른 프로그램이 쓰는 중 ({'; '.join(users[real])}) 인데 어느 Tapo 인지 모름 "
                     f"→ 그 프로그램을 끄거나, 끄면 안 되는 시험이면 sdram/boards.conf 에 자리를 적고 다시 실행")
        n = _rig_name(tapo, p) or f"U{len(busy) + 1}"
        busy[n] = {"tapo": tapo, "uart": p, "fastboot": None, "why": f"UART 사용 중 ({users[real][0]})"}
    busy_tapo = {b["tapo"] for b in busy.values()}
    busy_uart = {os.path.realpath(b["uart"]) for b in busy.values()}
    busy_fb = {b["fastboot"] for b in busy.values() if b.get("fastboot")}
    for n, b in busy.items():
        log(f"시험 중이라 건드리지 않음: {n} (Tapo {b['tapo']}, UART {short(b['uart'])}) - {b['why']}")

    env = cm.load_env()
    email, pw = env.get("TAPO_EMAIL"), env.get("TAPO_PASSWORD")
    if not (email and pw):
        sys.exit("remote_power.env 에 TAPO_EMAIL / TAPO_PASSWORD 필요")
    if a.probe:
        if a.probe in busy_tapo:
            sys.exit(f"Tapo {a.probe} 는 시험 중인 보드라 끄고 켤 수 없음")
        return probe(a.probe, email, pw, busy_uart)
    ips = [ip for ip in read_tapo_list(a.tapo_file) if ip not in busy_tapo]
    log(f"Tapo 목록 ({a.tapo_file}, 시험 중 제외): {', '.join(ips) or '(없음)'}")

    tapos, bad_tapo = {}, {}
    for ip in ips:
        t = TapoController()
        ok, msg = t.connect(ip, email, pw)
        if ok:
            tapos[ip] = t
        else:
            bad_tapo[ip] = f"연결 실패: {msg}"
            log(f"Tapo {ip} 연결 실패 → 제외 ({msg})")

    ports, bad_uart = {}, {}
    # by-path 에는 같은 장치가 이름 두 개(usb-0:… / usbv2-0:…)로 보일 수 있음 → 실제 장치로 묶어서 하나만 (usb-0 쪽 이름 우선)
    uniq = {}
    for p in sorted(glob.glob("/dev/serial/by-path/*"), key=lambda s: ("usbv2" in s, s)):
        uniq.setdefault(os.path.realpath(p), p)
    for p in sorted(uniq.values()):
        if os.path.realpath(p) in busy_uart:
            continue
        po = Port(p)
        if po.open():
            ports[p] = po
        else:
            bad_uart[p] = f"열기 실패 (다른 프로그램이 사용 중?): {po.err}"
            log(f"UART {short(p)} 열기 실패 → 제외 ({po.err})")
    log(f"UART {len(ports)}개, Tapo {len(tapos)}개")

    def all_power(state):
        for ip, t in tapos.items():
            ok, msg = t.set_power(state)
            if not ok:
                log(f"Tapo {ip} {'ON' if state else 'OFF'} 실패: {msg}")

    # 1) 전부 OFF → 전부 ON : 살아 있는 UART
    log("1) Tapo 전부 OFF → 전부 ON, 부팅 출력이 올라오는 UART 확인")
    all_power(False)
    time.sleep(3)
    for po in ports.values():
        po.flush()
    all_power(True)
    t_end = time.time() + 8
    while time.time() < t_end:
        for po in ports.values():
            po.poll()
        time.sleep(0.05)
    alive = [p for p, po in ports.items() if "ROM(" in po.buf or len(po.buf.strip()) > 40]
    for p, po in ports.items():
        log(f"   {short(p)}: {'출력 있음' if p in alive else '출력 없음'}")
    # 참고용으로만 씀: 출력 없던 UART 도 빼지 않고 3단계에서 같이 찾음
    # (전부 동시에 켰을 때 한 보드가 안 올라오는 경우가 있었음 - 2026-11-03 02 보드)
    all_power(False)
    time.sleep(3)

    # 2~3) 하나씩
    pairs, rem_uart, rem_fb_seen = [], sorted(ports, key=lambda p: p not in alive), set()
    base_fb = fastboot_paths() | busy_fb
    if base_fb:
        log(f"주의: 전원 OFF 상태인데 fastboot 장치가 이미 보임 {sorted(base_fb)} → 비교에서 제외")
    for ip in list(tapos):
        if not rem_uart:
            bad_tapo.setdefault(ip, "남은 UART 없음 (이 Tapo 에 연결된 보드의 UART 를 못 찾음)")
            continue
        log(f"3) Tapo {ip} ON → 남은 UART {len(rem_uart)}개에 'u' 연타")
        for p in rem_uart:
            ports[p].flush()
        tapos[ip].set_power(True)
        hit, t_end, nxt = None, time.time() + 15, 0
        while time.time() < t_end and not hit:
            now = time.time()
            if now >= nxt:
                for p in rem_uart:
                    ports[p].write(b"u")
                nxt = now + 0.02
            for p in rem_uart:
                if "total 3 char" in ports[p].poll():
                    hit = p
                    break
            time.sleep(0.002)
        if not hit:
            tapos[ip].set_power(False)
            outs = [short(p) for p in rem_uart if ports[p].buf.strip()]
            bad_tapo[ip] = "켜도 'total 3 char' 가 뜨는 UART 없음" + (f" (출력은 있었음: {', '.join(outs)})" if outs else "")
            log(f"   → 짝 UART 못 찾음")
            time.sleep(2)
            continue
        po = ports[hit]
        log(f"   → UART {short(hit)}")
        # 여분 'u' 정리 후 usb
        time.sleep(0.3)
        po.poll()
        last = po.buf.replace("\r", "").split("\n")[-1]
        m = re.search(r"u+[ \t]*$", last)
        stray = len(m.group(0).strip()) if m else 0
        if stray % 3:
            po.write(b"x" * (3 - stray % 3))
            time.sleep(0.5)
        po.write(b"usb\r")
        ready, t_end = False, time.time() + 15
        while time.time() < t_end and not ready:
            ready = "usb_dnl_fastboot: ready" in po.poll()
            time.sleep(0.05)
        fb = None
        if ready:
            t_end = time.time() + 10
            while time.time() < t_end and not fb:
                new = fastboot_paths() - base_fb - rem_fb_seen
                if new:
                    fb = sorted(new)[0]
                    if len(new) > 1:
                        log(f"   주의: 새 fastboot 장치가 여러 개 {sorted(new)} → {fb} 사용")
                time.sleep(1)
        log(f"   → fastboot {fb or '못 찾음 (' + ('usb_dnl_fastboot ready 안 나옴' if not ready else 'USB 장치가 안 보임 - USB 선 확인') + ')'}")
        if fb:
            rem_fb_seen.add(fb)
        # 정상 부팅 출력으로 보드 정보
        tapos[ip].set_power(False)
        time.sleep(2.5)
        po.flush()
        tapos[ip].set_power(True)
        t_end = time.time() + 15
        while time.time() < t_end and "Starting kernel" not in po.poll():
            time.sleep(0.05)
        info = board_info(po.buf)
        tapos[ip].set_power(False)
        log(f"   → 보드: {info or '정보 못 읽음'}")
        pairs.append({"tapo": ip, "uart": hit, "fastboot": fb, "fastboot_ready": ready, **info})
        rem_uart.remove(hit)
        time.sleep(2)
    for p in rem_uart:
        bad_uart.setdefault(p, "출력은 있었지만 짝 Tapo 를 못 찾음 (Tapo 목록에 없는 플러그 / 상시 전원?)" if p in alive
                            else "어느 Tapo 를 켜도 출력 없음 (보드 없음 / UART 선·TX-RX·GND / 보드 전원)")

    # 4) 이름
    named, used = {}, set(busy)
    for n, b in busy.items():   # 시험 중인 보드: 이전 rig.json 값을 그대로 두고 표시만
        named[n] = {**prev.get(n, {"tapo": b["tapo"], "uart": b["uart"], "fastboot": b.get("fastboot")}),
                    "in_use": True, "note": f"{b['why']} - 이번에 확인 안 함 (이전 값 유지)"}
    print()
    for i, pr in enumerate(pairs, 1):
        sugg = None
        for name, old in prev.items():
            if old.get("tapo") == pr["tapo"] and old.get("uart") == pr["uart"] and old.get("pbl") == pr.get("pbl"):
                sugg = name
        if sugg in used:
            sugg = None
        sugg = sugg or f"S{i}"
        desc = f"Tapo {pr['tapo']} / {short(pr['uart'])} / {pr['fastboot'] or 'fastboot 없음'} / {pr.get('type_guess', '?')} / PBL {pr.get('pbl', '?')}"
        if a.keep_names or not sys.stdin.isatty():
            name = sugg
        else:
            # 어느 보드인지 눈으로 알 수 있게: 이름을 입력할 때까지 그 Tapo 를 계속 껐다 켬 (켜짐 blink_on 초 / 꺼짐 blink_off 초)
            import threading
            t = tapos[pr["tapo"]]
            stop = threading.Event()

            def _blink():
                while not stop.is_set():
                    t.set_power(True)
                    if stop.wait(a.blink_on):
                        break
                    t.set_power(False)
                    stop.wait(a.blink_off)

            print(f"\n페어 {i}: {desc}")
            print(f"  → Tapo {pr['tapo']} 를 계속 껐다 켭니다 (켜짐 {a.blink_on:g}초 / 꺼짐 {a.blink_off:g}초). 깜빡이는 보드의 이름을 입력하면 다음으로")
            th = threading.Thread(target=_blink, daemon=True)
            th.start()
            try:
                name = input(f"  깜빡이는 보드 이름 [{sugg}]: ").strip() or sugg
                while name in used:
                    name = input(f"  '{name}' 은 이미 씀. 다른 이름: ").strip()
            finally:
                stop.set()
                th.join(timeout=10)
                t.set_power(False)
        used.add(name)
        named[name] = pr

    out = {"created": dt.datetime.now().isoformat(timespec="seconds"), "pairs": named,
           "unmatched": {"tapo": bad_tapo, "uart": {short(k): v for k, v in bad_uart.items()}}}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.makedirs("logs/rig", exist_ok=True)
    with open(f"logs/rig/{dt.datetime.now():%Y%m%d_%H%M%S}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 5) 표
    print("\n=== 결과 (rig.json) ===")
    print(f"{'이름':<6} {'Tapo':<15} {'UART':<44} {'fastboot':<10} {'종류(추정)':<12} {'DDR':<5} PBL / SBL")
    for name, pr in named.items():
        if pr.get("in_use"):
            print(f"{name:<6} {pr['tapo']:<15} {short(pr['uart']):<44} {str(pr.get('fastboot') or '-'):<10} (시험 중 - 확인 안 함, 이전 값)")
            continue
        print(f"{name:<6} {pr['tapo']:<15} {short(pr['uart']):<44} {str(pr['fastboot'] or '-'):<10} "
              f"{pr.get('type_guess', '?'):<12} {str(pr.get('ddr', '?')):<5} {pr.get('pbl', '?')} / {pr.get('sbl', '?')}")
    for k, v in bad_tapo.items():
        print(f"  짝 없음 Tapo {k}: {v}")
    for k, v in bad_uart.items():
        print(f"  짝 없음 UART {short(k)}: {v}")
    print("전원: 찾은 보드는 전부 OFF" + (" (시험 중인 보드는 그대로)" if busy else ""))
    for lk in held:
        lk.release()


if __name__ == "__main__":
    main()
