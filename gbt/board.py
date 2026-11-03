"""보드 한 대 제어: Tapo 전원 / UART / fastboot 진입 / 부팅 로그 / fastboot 명령

반드시 rig.acquire(보드) 로 lock 을 잡은 뒤에 Board(...) 를 만든다 (docs/TEST_SPEC.md 0장)
"""
import os
import re
import subprocess
import time

from gbt.util import log, out_line, uart_line

FASTBOOT = "/home/test/bin/fastboot"
BAUD = 921600


class BoardError(RuntimeError):
    pass


class Board:
    def __init__(self, name, info):
        import serial
        import config_manager as cm
        from tapo_control import TapoController
        env = cm.load_env()
        self.name, self.info = name, info
        self.tapo = TapoController()
        ok, msg = self.tapo.connect(info["tapo"], env.get("TAPO_EMAIL"), env.get("TAPO_PASSWORD"))
        if not ok:
            raise BoardError(f"Tapo {info['tapo']} 연결 실패: {msg}")
        self.ser = serial.Serial(info["uart"], BAUD, timeout=0, exclusive=True)
        self.buf = ""
        self._line = ""          # 아직 줄바꿈이 안 온 UART 조각
        self.show_uart = True    # UART 출력을 화면/로그에 줄 단위로 보여줌

    # ---- 전원 / UART
    def power(self, on):
        ok, msg = self.tapo.set_power(on)
        if not ok:
            raise BoardError(f"Tapo {'ON' if on else 'OFF'} 실패: {msg}")

    def poll(self):
        n = self.ser.in_waiting
        if n:
            chunk = self.ser.read(n).decode("utf-8", errors="replace")
            self.buf += chunk
            if self.show_uart:
                self._line += chunk
                while "\n" in self._line:
                    ln, self._line = self._line.split("\n", 1)
                    ln = ln.rstrip("\r")
                    if ln.strip():
                        uart_line(self.name, ln)
        return self.buf

    def drain(self, seconds):
        """seconds 동안 UART 를 읽어서 보여줌 (fastboot 명령이 도는 동안 보드가 뭐라고 하는지 보기)"""
        t_end = time.time() + seconds
        while time.time() < t_end:
            self.poll()
            time.sleep(0.05)
        if self.show_uart and self._line.strip():
            uart_line(self.name, self._line.rstrip("\r") + " …")
            self._line = ""

    def flush(self):
        self.ser.reset_input_buffer()
        self.buf = ""
        self._line = ""

    def power_cycle(self, off_sec=2.5):
        self.power(False)
        time.sleep(off_sec)
        self.flush()
        self.power(True)

    # ---- fastboot
    def enter_fastboot(self):
        """전원 OFF→ON → 'u' 연타 → total 3 char → usb → usb_dnl_fastboot: ready"""
        self.power_cycle()
        t_end, nxt = time.time() + 15, 0
        while time.time() < t_end:
            if time.time() >= nxt:
                self.ser.write(b"u")
                nxt = time.time() + 0.02
            if "total 3 char" in self.poll():
                break
            time.sleep(0.002)
        else:
            raise BoardError("'total 3 char' 안 나옴 (UART / 전원 확인)")
        time.sleep(0.3)
        self.poll()
        m = re.search(r"u+[ \t]*$", self.buf.replace("\r", "").split("\n")[-1])
        stray = len(m.group(0).strip()) if m else 0
        if stray % 3:
            self.ser.write(b"x" * (3 - stray % 3))
            time.sleep(0.5)
        self.ser.write(b"usb\r")
        t_end = time.time() + 15
        while time.time() < t_end:
            if "usb_dnl_fastboot: ready" in self.poll():
                return
            time.sleep(0.05)
        raise BoardError("'usb_dnl_fastboot: ready' 안 나옴")

    def fastboot(self, args, expect, timeout):
        """fastboot -s <이 보드> <args>. 출력에 expect(정규식)가 있으면 True.
        fastboot 가 도는 동안에도 UART 를 계속 읽어서 보여줌 (어디서 멈췄는지 보이게)"""
        return fastboot(["-s", self.info["fastboot"], *args], expect, timeout, during=self.poll)

    # ---- 부팅
    def boot_log(self, until="Starting kernel", timeout=40):
        """전원 껐다 켜고 until 이 나올 때까지(최대 timeout 초) UART 출력을 모아서 돌려줌"""
        self.power_cycle()
        t_end = time.time() + timeout
        while time.time() < t_end and until not in self.poll():
            time.sleep(0.05)
        return self.buf


def fastboot(args, expect, timeout, during=None):
    """during: fastboot 가 도는 동안 0.05초마다 부를 함수 (보드 UART 읽기)"""
    cmd = [FASTBOOT, *args]
    log("$ " + " ".join(cmd), "info")
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fd = pr.stdout.fileno()
    os.set_blocking(fd, False)     # 줄바꿈 없이 멈춰 있는 출력("Writing 'sbl'" 등)에서도 막히지 않게
    out, part, t_end = "", "", time.time() + timeout

    def _read():
        nonlocal out, part
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            return False
        if not data:
            return False
        s = data.decode(errors="replace").replace("\r", "\n")
        out += s
        part += s
        while "\n" in part:
            ln, part = part.split("\n", 1)
            if ln.strip():
                out_line(ln)
        return True

    while True:
        while _read():
            pass
        if during:
            during()
        if pr.poll() is not None:
            while _read():
                pass
            break
        if time.time() > t_end:
            pr.kill()
            pr.wait()
            out += f"\n(시간 초과 {timeout}초)"
            if part.strip():
                out_line(part + " …")
            log(f"   fastboot 시간 초과 {timeout}초 - 위 UART 로그의 마지막 줄이 보드가 멈춘 곳", "err")
            part = ""
            break
        time.sleep(0.05)
    if part.strip():
        out_line(part)
    out = out.strip()
    ok = re.search(expect, out) is not None
    if not ok:
        log(f"   → 기대한 출력({expect}) 없음", "err")
    return ok
