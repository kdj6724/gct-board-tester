"""
UART(시리얼) 입출력 도우미.

read_until 은 지정한 문자열/정규식이 나타날 때까지, 혹은 idle timeout 이
지날 때까지 시리얼을 읽으며 수신한 줄마다 on_line 콜백을 호출한다.
반환값: (found: bool, buffer: str, timed_out_idle: bool)
"""
from __future__ import annotations
import re
import time


def open_serial(serial_module, port: str, baud: int, timeout: float = 1.0):
    return serial_module.Serial(port, baud, timeout=timeout)


def read_until(ser, pattern: str, regex: bool, timeout: float,
               on_line=None, poke_on_idle: bool = False,
               idle_timeout: float | None = None, running_check=None) -> tuple[bool, str, bool]:
    """`ser` 에서 읽어 pattern 이 나타나면 True 로 반환.

    - regex=True 이면 pattern 을 정규식으로 취급(re.search), 아니면 부분 문자열 포함 검사.
    - timeout: 전체 대기 시간(초). 초과 시 (False, buf, False) 반환.
    - idle_timeout 지정 시, 그 시간 동안 아무 수신도 없으면 (False, buf, True) 로 idle-timeout 반환.
    - running_check: 콜러블, False 반환 시 즉시 중단 (False, buf, False).
    """
    deadline = time.time() + timeout if timeout else None
    last_rx = time.time()
    buf = ""
    line_buf = ""
    matcher = re.compile(pattern) if regex else None

    def _matched(text: str) -> bool:
        return bool(matcher.search(text)) if matcher else (pattern in text)

    while True:
        if running_check is not None and not running_check():
            return False, buf, False
        if deadline is not None and time.time() >= deadline:
            return False, buf, False
        raw = ser.read(256)
        if raw:
            last_rx = time.time()
            chunk = raw.decode("utf-8", errors="replace")
            buf += chunk
            line_buf += chunk
            while "\n" in line_buf:
                line, line_buf = line_buf.split("\n", 1)
                line = line.rstrip("\r")
                if on_line and line.strip():
                    on_line(line)
            if _matched(buf):
                return True, buf, False
        else:
            if idle_timeout is not None and (time.time() - last_rx) > idle_timeout:
                return False, buf, True
            if poke_on_idle:
                ser.write(b"\n")
            time.sleep(0.1)


def send_text(ser, text: str, append_enter: bool = True, delay_after: float = 0.0):
    payload = text
    if append_enter:
        payload += "\n"
    ser.write(payload.encode("utf-8", errors="replace"))
    if delay_after:
        time.sleep(delay_after)


def upload_script(ser, script: str, target_path: str, running_check=None):
    """heredoc 방식으로 원격 셸에 스크립트 파일을 생성한다 (기존 auto_script.py 방식과 동일)."""
    lines = script.splitlines()
    ser.write(f"cat > {target_path} << 'SCRIPTEOF'\n".encode())
    time.sleep(0.1)
    for line in lines:
        if running_check is not None and not running_check():
            return
        ser.write((line + "\n").encode())
        time.sleep(0.02)
    ser.write(b"SCRIPTEOF\n")
    time.sleep(0.3)
    ser.write(f"chmod +x {target_path}\n".encode())
    time.sleep(0.1)
