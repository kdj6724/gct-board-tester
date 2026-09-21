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


def read_until_with_poke(ser, pattern: str, regex: bool, timeout: float,
                          poke_bytes: bytes, poke_interval: float,
                          on_line=None, running_check=None) -> tuple[bool, str]:
    """Knock 블록용: `poke_bytes` 를 즉시 한 번 보내고, 그 뒤로 poke_interval 초마다
    응답 여부와 상관없이 다시 보내면서(리부팅 직후처럼 한 번의 Enter 로는 프롬프트가
    안 깨어나는 경우를 대비) pattern 이 나타나는지 최대 timeout 초 동안 확인한다.

    read_until 의 idle_timeout/poke_on_idle 과는 다르다 - 그쪽은 무응답 상태에서
    ~0.1초 간격으로 계속 찔러대는 것이고, 이쪽은 poke_interval 로 지정한 주기마다만
    보낸다.

    반환값: (found: bool, buffer: str)
    """
    deadline = time.time() + timeout if timeout else None
    buf = ""
    line_buf = ""
    matcher = re.compile(pattern) if regex else None

    def _matched(text: str) -> bool:
        return bool(matcher.search(text)) if matcher else (pattern in text)

    ser.write(poke_bytes)
    next_poke = time.time() + max(0.1, poke_interval)

    while True:
        if running_check is not None and not running_check():
            return False, buf
        if deadline is not None and time.time() >= deadline:
            return False, buf
        raw = ser.read(256)
        if raw:
            chunk = raw.decode("utf-8", errors="replace")
            buf += chunk
            line_buf += chunk
            while "\n" in line_buf:
                line, line_buf = line_buf.split("\n", 1)
                line = line.rstrip("\r")
                if on_line and line.strip():
                    on_line(line)
            if _matched(buf):
                return True, buf
        else:
            time.sleep(0.05)
        if time.time() >= next_poke:
            ser.write(poke_bytes)
            next_poke = time.time() + max(0.1, poke_interval)


def send_text(ser, text: str, append_enter: bool = True, delay_after: float = 0.0, on_send=None):
    """text 안에 줄바꿈(여러 줄 입력창에 여러 명령을 나눠 쓴 경우)이 있으면, 예전처럼
    한 번의 write() 로 통째로 쏘지 않고 한 줄씩 순서대로 보내면서 줄 사이마다
    delay_after 만큼 대기한다 - 보드가 이전 줄을 처리할 시간을 준 뒤에 다음 줄을
    보내기 위함이다. 한 줄짜리 text 는 기존과 완전히 동일하게 동작한다.
    on_send(line) 이 주어지면 각 줄을 실제로 쓰기 직전에 호출한다 (로그용 - 여러 줄일 때
    "다 합쳐서 한 번에 나갔나" 오해 없이 줄마다 실시간으로 로그가 찍히게 하기 위함)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        is_last = (i == len(lines) - 1)
        payload = line
        if not is_last or append_enter:
            payload += "\n"
        if on_send is not None:
            on_send(line)
        ser.write(payload.encode("utf-8", errors="replace"))
        if delay_after:
            time.sleep(delay_after)


def read_until_idle(ser, timeout: float, idle_timeout: float = 0.3,
                     on_line=None, running_check=None) -> str:
    """특정 문자열을 몰라도(=Check 를 따로 설정 안 해도) 쓸 수 있는 범용 "응답 캡처" 함수.
    아무 pattern 도 기다리지 않고, idle_timeout 초 동안 새로 들어오는 데이터가 없으면
    (=보드가 응답을 다 뱉고 조용해졌다고 보고) 그때까지 받은 내용을 반환한다.
    idle 상태가 안 되고 계속 뭔가 들어오는 중이면(예: 로그 폭주) timeout 을 넘긴 시점의
    내용을 그냥 반환한다 - 에러가 아니라 안전장치일 뿐이다.
    Input 으로 뭘 보내든 Check 없이도 자동으로 응답이 로그에 보이게 하기 위해 쓴다."""
    deadline = time.time() + timeout if timeout else None
    last_rx = time.time()
    buf = ""
    line_buf = ""
    while True:
        if running_check is not None and not running_check():
            break
        if deadline is not None and time.time() >= deadline:
            break
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
        else:
            if (time.time() - last_rx) > idle_timeout:
                break
            time.sleep(0.02)
    return buf


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
