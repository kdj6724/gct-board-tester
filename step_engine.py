"""
블록 시퀀스 실행 엔진.

GUI(Tk)와 분리되어 있어 실제 하드웨어 없이도(가짜 serial/tapo 객체로) 단위
테스트가 가능하다. main_app.py 는 이 모듈의 StepContext/run() 을 통해서만
테스트를 수행한다.
"""
from __future__ import annotations
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import config_manager as cm
import serial_session as ss
import step_types as st


class StopRequested(Exception):
    pass


class LoopBreak(Exception):
    """Break 블록이 실행되면 발생 - 파이썬 for/while 의 break 처럼 가장 가까운
    바깥쪽 Loop 하나만 즉시 빠져나가고, 그 Loop 뒤에 오는 블록들은 정상적으로
    이어서 실행된다. _run_loop() 가 자기 children 을 도는 자리에서 이 예외를
    잡아서 파이썬 루프 자체를 break 하는 방식으로 구현한다 - 그래서 중첩된
    Loop 안에서 터뜨려도 자동으로 "가장 안쪽" Loop 만 멈춘다(그 예외가 처음
    걸리는 _run_loop 호출이 항상 가장 안쪽이므로).

    Loop 로 안 감싸인 곳(또는 Loop 가 하나도 없는 프리셋/시퀀스 최상위)에서
    실행되면 아무도 못 잡고 run()/_exec_preset() 까지 올라오는데, 이 경우는
    에러로 죽이지 않고 "시퀀스가 여기서 끝난 것"으로 조용히 처리한다."""
    pass


@dataclass
class StepContext:
    serial_factory: Callable[[], object]        # () -> 새 serial 객체 (port/baud 는 클로저로 캡슝)
    tapo: object                                  # .set_power(bool) -> (ok, msg)
    log: Callable[[str, str], None]               # (msg, tag) - 화면 로그
    log_result: Callable[[str], None]             # (msg) - 결과 파일 로그
    is_running: Callable[[], bool]                # Stop 버튼 눌리면 False
    boot_reset_buffer: bool = True

    ser: object = None
    vars: dict = field(default_factory=dict)
    _capture: list = field(default_factory=list)

    # 메인 화면 다이어그램 하이라이트용 (GUI 와 분리하려고 콜백으로 뺐다 - GUI 없이
    # 테스트할 때는 None 이라 그냥 아무 일도 안 일어난다). on_active(block_id, loop_progress)
    # - block_id: 지금 막 실행을 시작하는 블록의 고유 id(없으면 None), loop_id 는 각 블록
    #   dict 의 "id" 필드를 그대로 쓴다(화면의 flat index 와는 무관 - GUI 쪽에서 알아서 매핑).
    # - loop_progress: {loop_block_id: (현재 반복, 전체 반복)} 스냅샷(진행 중인 모든 Loop,
    #   중첩 포함). worker 스레드에서 호출되므로 GUI 쪽 콜백은 스스로 thread-safe 해야 한다
    #   (main_app 은 self.after(0, ...) 로 감싸서 넘겨받는다).
    on_active: Optional[Callable[[Optional[str], dict], None]] = None
    loop_progress: dict = field(default_factory=dict)

    # ---- 내부 유틸 ---------------------------------------------------
    def check_running(self):
        if not self.is_running():
            raise StopRequested()

    def report_active(self, block_id: str | None):
        if self.on_active is not None:
            self.on_active(block_id, dict(self.loop_progress))

    def subst(self, text: str) -> str:
        if not text:
            return text
        out = text
        for k, v in self.vars.items():
            out = out.replace("{" + k + "}", st.fmt_var(v) if isinstance(v, int) else str(v))
        return out

    def ensure_serial(self):
        if self.ser is None:
            self.ser = self.serial_factory()
        return self.ser

    def _on_line(self, line: str):
        self.log("  " + line, "mute")
        self.log_result(line)
        self._capture.append(line)

    def pop_capture(self) -> str:
        text = "\n".join(self._capture)
        self._capture = []
        return text


# ─────────────────────────────────────────────
# 블록별 실행
# ─────────────────────────────────────────────

def _exec_power(block, ctx: StepContext):
    p = block["params"]
    state = p.get("state", "on") == "on"
    ctx.log(f"Tapo: power {'ON' if state else 'OFF'}", "info")
    ok, msg = ctx.tapo.set_power(state)
    ctx.log(f"  → {msg}", "ok" if ok else "err")
    if not ok:
        ctx.log("전원 제어 실패. 연결 상태를 확인하세요.", "err")
        raise StopRequested()
    if state and ctx.boot_reset_buffer and ctx.ser is not None:
        try:
            ctx.ser.reset_input_buffer()
        except Exception:  # noqa: BLE001
            pass
    delay = float(p.get("delay_after", 0) or 0)
    _sleep_interruptible(delay, ctx)


def _exec_wait_string(block, ctx: StepContext):
    p = block["params"]
    pattern = ctx.subst(p.get("pattern", ""))
    timeout = float(p.get("timeout", 30) or 30)
    regex = bool(p.get("regex", False))
    ctx.log(f"Waiting: '{pattern}' (timeout {timeout:g}s)", "mute")
    ser = ctx.ensure_serial()
    found, buf, idle = ss.read_until(
        ser, pattern, regex, timeout,
        on_line=ctx._on_line, running_check=ctx.is_running,
    )
    ctx.check_running()
    if found:
        ctx.log(f"→ 감지됨: '{pattern}'", "ok")
    else:
        action = p.get("on_timeout", "stop")
        ctx.log(f"⚠ Timeout: '{pattern}' 미검출", "err")
        if action == "stop":
            raise StopRequested()
        # "continue" 인 경우 다음 블록으로 진행


# Input/Ctrl 전송 뒤 Check 를 따로 설정 안 했어도 자동으로 응답을 캡처해서 보여주는
# 구간의 기본값 - 조용해지면(idle) 바로 다음으로 넘어가고, 계속 뭔가 들어오는 중이면
# (로그 폭주 등) 최대 이 시간까지만 보고 넘어간다.
_AUTO_CAPTURE_TIMEOUT = 3.0
_AUTO_CAPTURE_IDLE = 0.3


def _exec_send(block, ctx: StepContext):
    p = block["params"]
    text = ctx.subst(p.get("text", ""))
    append_enter = bool(p.get("append_enter", True))
    delay_after = float(p.get("delay_after", 0) or 0)
    check = p.get("check")
    ser = ctx.ensure_serial()

    lines = text.split("\n")
    for i, line in enumerate(lines):
        is_last = (i == len(lines) - 1)
        payload = line + ("\n" if (not is_last or append_enter) else "")
        ctx.log(f"$ {line!r}", "info")
        ctx.log_result(f"$ {line}")
        ser.write(payload.encode("utf-8", errors="replace"))
        ctx.check_running()
        # Check 를 따로 안 붙여도, 보내고 나서 짧게 응답을 자동으로 읽어서 로그에
        # 보여준다 (조용해질 때까지 최대 _AUTO_CAPTURE_TIMEOUT 초). 단, 마지막 줄이고
        # 뒤이어 Check 가 붙어있으면 여기서 먼저 읽어버리지 않는다 - Check 가 기다리는
        # pattern(예: 다시 뜨는 프롬프트)을 auto-capture 가 가로채서 Check 가 못 보게
        # 되는 걸 막기 위함이다.
        if not (is_last and check):
            ss.read_until_idle(ser, timeout=_AUTO_CAPTURE_TIMEOUT, idle_timeout=_AUTO_CAPTURE_IDLE,
                                on_line=ctx._on_line, running_check=ctx.is_running)
            ctx.check_running()
        if delay_after:
            _sleep_interruptible(delay_after, ctx)

    if check:
        pattern = ctx.subst(check.get("pattern", ""))
        timeout = float(check.get("timeout", 30) or 30)
        regex = bool(check.get("regex", False))
        ctx.log(f"  ↳ Check: '{pattern}' (timeout {timeout:g}s)", "mute")
        found, buf, idle = ss.read_until(
            ser, pattern, regex, timeout,
            on_line=ctx._on_line, running_check=ctx.is_running,
        )
        ctx.check_running()
        if found:
            ctx.log(f"  → 감지됨: '{pattern}'", "ok")
        else:
            ctx.log(f"  ⚠ Timeout: '{pattern}' 미검출", "err")
            if check.get("on_timeout", "stop") == "stop":
                raise StopRequested()


def _exec_knock(block, ctx: StepContext):
    """Input 과 달리 한 번 보내고 끝이 아니라, pattern 이 나타날 때까지 interval
    초마다 같은 text 를 반복 전송한다 (한 번의 Enter 로는 프롬프트가 안 깨는 보드용)."""
    p = block["params"]
    text = ctx.subst(p.get("text", ""))
    append_enter = bool(p.get("append_enter", True))
    interval = float(p.get("interval", 2.0) or 2.0)
    pattern = ctx.subst(p.get("pattern", ""))
    regex = bool(p.get("regex", False))
    timeout = float(p.get("timeout", 30) or 30)

    payload = text + ("\n" if append_enter else "")
    poke_bytes = payload.encode("utf-8", errors="replace")

    ctx.log(f"\U0001f44a Knock: {text!r} 를 {interval:g}초 간격으로 보내며 "
            f"'{pattern}' 대기 (최대 {timeout:g}s)", "info")
    ctx.log_result(f"$ (knock every {interval:g}s) {text}")
    ser = ctx.ensure_serial()
    found, buf = ss.read_until_with_poke(
        ser, pattern, regex, timeout, poke_bytes, interval,
        on_line=ctx._on_line, running_check=ctx.is_running,
    )
    ctx.check_running()
    if found:
        ctx.log(f"  → 감지됨: '{pattern}'", "ok")
    else:
        ctx.log(f"  ⚠ Timeout: '{pattern}' 미검출", "err")
        if p.get("on_timeout", "stop") == "stop":
            raise StopRequested()


_CTRL_BYTES = {
    "C": 0x03,   # Ctrl+C (SIGINT/break) - ping 처럼 스스로 안 끝나는 명령 멈출 때
    "D": 0x04,   # Ctrl+D (EOF)
    "Z": 0x1a,   # Ctrl+Z (suspend)
    "\\": 0x1c,  # Ctrl+\ (SIGQUIT)
}


def _exec_ctrl(block, ctx: StepContext):
    """text 를 보내는 Input 과 달리, 줄바꿈 없이 raw 제어 문자 1바이트만 보낸다."""
    p = block["params"]
    key = (p.get("key") or "C").upper()
    code = _CTRL_BYTES.get(key, (ord(key) - 64) if len(key) == 1 and key.isalpha() else 0x03)
    ctx.log(f"⌃ Ctrl+{key} 전송", "info")
    ctx.log_result(f"$ (ctrl) Ctrl+{key}")
    ser = ctx.ensure_serial()
    ser.write(bytes([code & 0xFF]))
    ctx.check_running()
    # Input 과 마찬가지로, 보내고 나서 짧게 응답(예: 프롬프트가 다시 뜨는 것)을
    # 자동으로 읽어서 로그에 보여준다.
    ss.read_until_idle(ser, timeout=_AUTO_CAPTURE_TIMEOUT, idle_timeout=_AUTO_CAPTURE_IDLE,
                        on_line=ctx._on_line, running_check=ctx.is_running)
    ctx.check_running()
    delay = float(p.get("delay_after", 0) or 0)
    _sleep_interruptible(delay, ctx)


def _exec_delay(block, ctx: StepContext):
    _sleep_interruptible(float(block["params"].get("seconds", 1) or 0), ctx)


def _exec_break(block, ctx: StepContext):
    """파이썬의 break 와 동일 - 가장 가까운 바깥쪽 Loop 를 즉시 빠져나간다.
    실제 탈출은 LoopBreak 예외를 던져서 _run_loop() 가 자기 반복문에서 잡아
    처리한다(여기서는 그냥 던지기만 함)."""
    ctx.log("⏹ Break: 가장 가까운 Loop 를 빠져나갑니다", "info")
    raise LoopBreak()


def _exec_upload_script(block, ctx: StepContext):
    p = block["params"]
    target = p.get("target_path", "/tmp/runtest.sh")
    ctx.log(f"Uploading {target} ...", "info")
    ser = ctx.ensure_serial()
    ss.upload_script(ser, p.get("script", ""), target, running_check=ctx.is_running)
    ctx.check_running()
    ctx.log("Script uploaded OK", "ok")


def _kill_process(proc: subprocess.Popen):
    """shell=True 로 띄운 프로세스(Windows 는 cmd.exe, POSIX 는 /bin/sh)뿐 아니라
    그 밑에서 실제로 돌고 있는 자식 프로세스(fastboot.exe 등)까지 통째로 죽인다.

    proc.kill() 만 하면 Windows 에서는 cmd.exe 만 죽고 fastboot.exe 는 안 죽은 채
    그대로 남아있는 문제가 있었다(taskkill 이 없으면 TerminateProcess 는 자식까지
    안 내려감) - 그래서 Windows 에서는 taskkill /T(프로세스 트리 전체)로, POSIX 는
    새 프로세스 그룹 전체를 죽이는 방식으로 확실히 정리한다. _exec_run() 에서
    Popen 할 때 이 방식이 통하도록 creationflags/start_new_session 을 같이 설정함."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        pass


def _apply_file_placeholders(cmd: str, files) -> str:
    """Exec 블록의 명령어 안에 있는 <파일1>~<파일5> 를 편집창에서 "파일 선택"으로
    고른 실제 경로 문자열로 바꾼다. 비어있는 슬롯은 그냥 건너뛴다(치환 안 함 -
    명령어에 그 플레이스홀더가 없으면 어차피 상관없고, 있는데 슬롯이 비어있으면
    사용자가 알아차릴 수 있게 그대로 남겨둔다)."""
    if not cmd or not files:
        return cmd
    for i, path in enumerate(files, start=1):
        if path:
            cmd = cmd.replace(f"<파일{i}>", path)
    return cmd


def _run_process(cmd: str, timeout: float, ctx: StepContext):
    """cmd(shell=True)를 띄워서 표준출력/표준에러를 한 줄씩 실시간으로 로그에
    흘려보내면서(ctx._on_line 재사용) 끝날 때까지, 또는 timeout 까지 기다린다.

    _exec_run() 과 If 블록의 exec 분기(_run_if_exec_check)가 이 함수를 공유한다 -
    "PC 에서 로컬 명령을 돌리고 출력/exit code 를 본다"는 동작 자체는 완전히
    같고, 그 결과로 뭘 할지(중단할지 vs 참/거짓 분기할지)만 다르기 때문에 그
    차이만 호출부에서 처리하도록 분리했다.

    반환: (lines, code, timed_out, spawn_failed)
    - spawn_failed=True 면 애초에 프로세스 실행 자체가 안 된 것(code/timed_out 의미 없음).
    - timed_out=True 면 강제 종료했고 code 는 None.
    """
    # 자식 프로세스(fastboot.exe 등)까지 한 번에 죽일 수 있도록 새 프로세스
    # 그룹/세션으로 띄운다 - _kill_process() 가 이걸 전제로 트리 전체를 정리한다.
    # cwd 를 프로젝트 루트(config_manager.BASE_DIR)로 고정해서, 명령어 안에 상대
    # 경로(예: "utils\iperf-3.1.3-win32\iperf3.exe")를 써도 이 도구를 어느 폴더에서
    # 실행했든, 어느 드라이브 문자에 복사해놨든 항상 똑같이 동작하게 한다 - 절대
    # 경로(Y:\...)를 안 쓰고 싶다는 요청으로 추가함.
    popen_kwargs: dict = {"cwd": cm.BASE_DIR}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        # 인코딩을 안 정해주면 파이썬 기본 인코딩으로 디코딩을 시도하다가 한글이
        # 섞인 출력(예: netsh wlan show networks 의 "인증"/"암호화" 항목들)에서
        # UnicodeDecodeError 가 나고, 그 순간 아래 _reader() 스레드가 예외를 삼키고
        # 조용히 멈춰버려서 그 이후 줄이 통째로 유실되는 문제가 있었다(예: SSID 9번인
        # SYNC4384_5G 를 check_pattern 으로 찾는데, 그 앞의 SSID 들 중 하나에서 이미
        # 끊겨서 실제로는 존재하는데도 "못 찾음"으로 판정됨).
        # cmd.exe 콘솔 출력은 예전 한글 Windows 기준으로는 CP949(EUC-KR) 지만,
        # "유니코드 UTF-8 사용" 옵션이 켜진 최신 Windows 에서는 UTF-8 로 나온다 -
        # 실제로 CP949 로 시도했더니 한글이 깨져서(치환 문자로) 나오는 걸 확인해서
        # UTF-8 로 바꿈. errors="replace" 는 그래도 같이 둬서, 혹시 또 인코딩이
        # 안 맞는 환경이어도 죽지 않고 그 글자만 깨진 채로 넘어가고 나머지 줄은
        # 계속 읽게 한다(캐시 매칭 대상인 ASCII 텍스트는 어느 인코딩으로 읽어도
        # 안전하게 보존됨).
        popen_kwargs["encoding"] = "utf-8"
        popen_kwargs["errors"] = "replace"
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1,
                                 **popen_kwargs)
    except OSError as e:
        ctx.log(f"  → ⚠ 실행 자체가 안 됨: {e}", "err")
        return [], None, False, True

    lines: list[str] = []

    def _reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
        except Exception:  # noqa: BLE001
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    start = time.time()
    flushed = 0
    timed_out = False
    while True:
        while flushed < len(lines):
            ctx._on_line(lines[flushed])
            flushed += 1
        if proc.poll() is not None and not reader.is_alive():
            break
        if time.time() - start > timeout:
            timed_out = True
            break
        try:
            ctx.check_running()
        except StopRequested:
            _kill_process(proc)
            raise
        time.sleep(0.05)
    while flushed < len(lines):  # 루프 빠져나온 뒤 남은 줄 마저 흘려보내기
        ctx._on_line(lines[flushed])
        flushed += 1

    if timed_out:
        _kill_process(proc)
        return lines, None, True, False

    return lines, proc.returncode, False, False


def _check_output(lines: list[str], code: int | None, pattern: str, regex: bool, ctx: StepContext) -> bool:
    """check_pattern 이 있으면 출력 전체에서 매칭 여부로, 없으면 exit code==0 으로 판정."""
    if pattern:
        pattern_sub = ctx.subst(pattern)
        captured = "\n".join(lines)
        if regex:
            import re
            return re.search(pattern_sub, captured) is not None
        return pattern_sub in captured
    return code == 0


def _log_check_reason(pattern: str, code: int | None, found: bool, ctx: StepContext, prefix: str = "  "):
    """Exec 실행 결과(성공/실패)의 "이유"를 명시적으로 로그에 남긴다 - check_pattern
    매칭 여부인지 exit code 인지, 실제 값이 뭐였는지까지 같이 보여준다(그냥
    "성공"/"실패"만 찍으면 나중에 로그만 보고는 왜 그렇게 판정됐는지 알 수가 없어서
    사람이 직접 원본 출력을 뒤져야 했던 문제가 있었음 - If 블록의 exec 분기(참/거짓
    경로)에서 특히 그랬다). _exec_run 과 _run_if_exec_check 가 공유해서 쓴다."""
    if pattern:
        pattern_sub = ctx.subst(pattern)
        if found:
            ctx.log(f"{prefix}→ ✅ 출력에서 '{pattern_sub}' 확인됨 (exit code {code})", "ok")
        else:
            ctx.log(f"{prefix}→ ❌ 출력에서 '{pattern_sub}' 못 찾음 (exit code {code})", "err")
    else:
        if found:
            ctx.log(f"{prefix}→ ✅ exit code {code} (성공)", "ok")
        else:
            ctx.log(f"{prefix}→ ❌ exit code {code} (0 이 아니라서 실패)", "err")


def _exec_run(block, ctx: StepContext):
    """PC 에서 로컬 프로그램(.exe 등)을 명령어 한 줄 그대로 실행한다(예: fastboot.exe
    로 USB 로 붙은 보드에 이미지 flash). UART 가 아니라 subprocess 라서 시리얼 포트와는
    완전히 별개 경로다 - 시리얼과 동시에/사이사이에 넣어 써도 서로 간섭 안 한다.

    표준출력/표준에러를 한 줄씩 실시간으로 로그에 흘려보낸다(ctx._on_line 재사용 -
    그래서 이 출력도 뒤에 오는 Save 블록의 PASS/FAIL 패턴 검사 대상에 들어간다).

    성공/실패 판정은 두 가지 방식이 있다:
    - check_pattern 을 채워두면 프로세스가 끝난 뒤 전체 출력에서 그 문자열이
      있는지로 판정한다(Wait String/Check 블록과 완전히 같은 방식 - 툴 안에서
      성공/실패를 확인하는 방법을 일관되게 가져가고 싶다는 요청으로 추가함).
    - check_pattern 을 비워두면 exit code 로만 판정한다(0 이면 성공) - Windows
      프로그램도 파이썬 subprocess 에서 리눅스와 동일하게 exit code 를 그대로
      읽을 수 있어서, fastboot.exe 처럼 관례상 성공 시 0 을 반환하는 프로그램은
      패턴을 안 정해줘도 기본적인 성공/실패가 잡힌다.
    시간 안에 안 끝나면 강제 종료하고, 실패/타임아웃났을 때 on_timeout(stop/continue)
    설정에 따라 테스트를 중단할지 다음 블록으로 넘어갈지 정한다(항상 "다음 블록"은
    하나뿐이다 - 성공/실패에 따라 서로 다른 경로를 타고 싶으면 If 블록을 "Exec 결과"
    모드로 쓴다 - _run_if_exec_check 참고).

    <파일1>~<파일5> 는 편집창에서 "파일 선택"으로 고른 경로를 그대로 문자열
    치환한다 - {var}(Loop 변수)와는 별개 문법으로, 매번 날짜 등으로 바뀌는 긴
    경로를 파일 탐색기로 골라서 명령어에 넣고 싶다는 요청으로 추가함."""
    p = block["params"]
    cmd = ctx.subst(p.get("cmd", ""))
    cmd = _apply_file_placeholders(cmd, p.get("files"))
    timeout = float(p.get("timeout", 60) or 60)
    on_timeout = p.get("on_timeout", "stop")
    if not cmd.strip():
        ctx.log("⚠ 실행할 명령어가 비어있습니다.", "err")
        return
    ctx.log(f"\U0001f4bb Exec: {cmd}", "info")
    ctx.log_result(f"$ (exec) {cmd}")

    lines, code, timed_out, spawn_failed = _run_process(cmd, timeout, ctx)

    if spawn_failed:
        if on_timeout == "stop":
            raise StopRequested()
        return

    if timed_out:
        ctx.log(f"  → ⚠ Timeout ({timeout:g}s) - 프로세스를 강제 종료했습니다.", "err")
        if on_timeout == "stop":
            raise StopRequested()
        return

    pattern = p.get("check_pattern", "")
    regex = bool(p.get("regex", False))
    found = _check_output(lines, code, pattern, regex, ctx)
    if pattern:
        pattern_sub = ctx.subst(pattern)
        if found:
            ctx.log(f"  → ✅ 성공 (출력에서 '{pattern_sub}' 확인됨, exit code {code})", "ok")
        else:
            ctx.log(f"  → ❌ 실패 (출력에서 '{pattern_sub}' 못 찾음, exit code {code})", "err")
            if on_timeout == "stop":
                raise StopRequested()
    elif found:
        ctx.log("  → ✅ 완료 (exit code 0)", "ok")
    else:
        ctx.log(f"  → ❌ 실패 (exit code {code})", "err")
        if on_timeout == "stop":
            raise StopRequested()


def _run_if_exec_check(p: dict, ctx: StepContext) -> bool:
    """If 블록의 source="exec" 분기: cmd 를 실행해서 check_pattern(없으면 exit
    code==0)으로 참/거짓을 정한다 - Exec 블록과 판정 로직은 동일하지만, 여기서는
    stop/continue 로 테스트를 중단하는 대신 항상 참/거짓 경로 중 하나로 진행한다
    (netsh wlan show interfaces 같은 걸로 WiFi 접속 성공/실패에 따라 다음에 어떤
    블록을 탈지 나누고 싶다는 요청으로 추가함)."""
    cmd = ctx.subst(p.get("cmd", ""))
    cmd = _apply_file_placeholders(cmd, p.get("files"))
    timeout = float(p.get("timeout", 10) or 10)
    if not cmd.strip():
        ctx.log("  ⚠ 실행할 명령어가 비어있습니다.", "err")
        return False
    ctx.log(f"  $ (exec) {cmd}", "mute")
    ctx.log_result(f"$ (exec) {cmd}")

    lines, code, timed_out, spawn_failed = _run_process(cmd, timeout, ctx)
    if spawn_failed:
        return False
    if timed_out:
        ctx.log(f"  ⚠ Timeout ({timeout:g}s) - 프로세스를 강제 종료했습니다.", "err")
        return False

    pattern = p.get("check_pattern", "")
    regex = bool(p.get("regex", False))
    found = _check_output(lines, code, pattern, regex, ctx)
    _log_check_reason(pattern, code, found, ctx)
    return found


def _exec_save_result(block, ctx: StepContext):
    p = block["params"]
    label = ctx.subst(p.get("label", "result"))
    captured = ctx.pop_capture()
    pass_pat, fail_pat = p.get("pass_pattern", ""), p.get("fail_pattern", "")
    regex = bool(p.get("regex", False))

    def _hit(pat):
        if not pat:
            return False
        import re
        return bool(re.search(pat, captured)) if regex else (pat in captured)

    verdict = None
    if fail_pat and _hit(fail_pat):
        verdict = "FAIL"
    elif pass_pat and _hit(pass_pat):
        verdict = "PASS"
    elif pass_pat or fail_pat:
        verdict = "FAIL"  # 패턴이 지정됐는데 둘 다 안 맞으면 실패로 간주

    tag = "ok" if verdict == "PASS" else ("err" if verdict == "FAIL" else "info")
    suffix = f" [{verdict}]" if verdict else ""
    ctx.log(f"\U0001f4be Save: {label}{suffix}", tag)
    ctx.log_result(f"=== SAVE [{label}]{suffix} ===\n{captured}")


def _exec_preset(block, ctx: StepContext):
    """PRESET 블록: params["blocks"] 에 통째로 담겨있는 하위 시퀀스를 그대로 실행한다.
    (LOOP/IF 처럼 진짜 제어 흐름이 있는 게 아니라 "여기에 이 블록들이 있다"는
    표시일 뿐이라 재귀적으로 그냥 실행하면 됨 - 중첩된 프리셋도 이 함수가 다시
    호출되므로 몇 겹이든 자동으로 처리된다.)

    다이어그램 하이라이트는 이 프리셋 블록 하나로 통째로 표시한다(이미 _run_nodes
    가 이 함수를 부르기 전에 report_active 를 호출해뒀음) - 안의 개별 스텝까지
    하나씩 쫓아가며 하이라이트하지 않는다. 안의 블록들이 갖고 있는 id 는 화면의
    최상위 블록 리스트엔 없는 id 라서, 그대로 report_active 가 불리면 "못 찾음"
    으로 처리돼 하이라이트가 꺼져버린다 - 그래서 이 구간 동안은 on_active 콜백을
    잠깐 꺼뒀다가 끝나면 되돌린다."""
    p = block["params"]
    name = p.get("preset_name", "")
    inner_blocks = p.get("blocks", [])
    ctx.log(f"\U0001f4e6 Preset: {name} 실행 ({len(inner_blocks)}개 블록)", "info")
    tree = st.build_tree(inner_blocks)
    saved_on_active = ctx.on_active
    ctx.on_active = None
    try:
        _run_nodes(tree, ctx)
    finally:
        ctx.on_active = saved_on_active
    ctx.log(f"\U0001f4e6 Preset: {name} 완료", "mute")


_ACTIONS = {
    st.POWER: _exec_power,
    st.WAIT_STRING: _exec_wait_string,
    st.SEND: _exec_send,
    st.KNOCK: _exec_knock,
    st.CTRL: _exec_ctrl,
    st.DELAY: _exec_delay,
    st.BREAK: _exec_break,
    st.UPLOAD_SCRIPT: _exec_upload_script,
    st.EXEC: _exec_run,
    st.SAVE_RESULT: _exec_save_result,
    st.PRESET: _exec_preset,
}


def _run_if(node, ctx: StepContext):
    """If 블록 실행: source 에 따라 두 가지 방식으로 참/거짓을 정하고, 찾으면
    참(true_children) 경로, 못 찾으면 거짓(false_children) 경로를 실행한다.
    Check/Exec 의 stop/continue 와 달리 항상 둘 중 하나로 진행한다(테스트를 멈추지 않음).

    - source="serial"(기본): 기존 방식 그대로 - UART 에서 pattern 을 timeout 안에 기다린다.
    - source="exec": PC 에서 cmd 를 실행해서 check_pattern(또는 exit code)으로 판정한다
      (netsh 같은 로컬 명령 결과로 분기하고 싶다는 요청으로 추가함 - 예: WiFi 접속
      성공/실패에 따라 다음에 어떤 블록을 탈지 나누기)."""
    block = node["start"]
    ctx.report_active(block.get("id"))
    p = block["params"]
    label = p.get("label", "If")
    source = p.get("source", "serial")

    if source == "exec":
        cmd_preview = ctx.subst(p.get("cmd", ""))
        ctx.log(f"\U0001f500 {label} (Exec 결과로 분기): {cmd_preview}", "info")
        found = _run_if_exec_check(p, ctx)
        ctx.check_running()
    else:
        pattern = ctx.subst(p.get("pattern", ""))
        timeout = float(p.get("timeout", 10) or 10)
        regex = bool(p.get("regex", False))
        send_text = ctx.subst(p.get("send_text", "") or "")
        ser = ctx.ensure_serial()
        if send_text:
            # send_text 가 있으면 이 자리에서 바로 보내고 곧바로 이어서 응답을
            # 기다린다(SEND 블록을 따로 앞에 두면 SEND 의 auto-capture 가 응답을
            # 먼저 가로채가서 이 If 가 빈 버퍼만 보게 되는 문제가 있었음 -
            # default_params(IF_START) 주석 참고). SEND 블록과 같은 방식으로
            # write 만 하고, 중간에 별도로 읽어서 흘려보내는 단계 없이 바로
            # read_until 로 넘어간다.
            #
            # 실측으로 확인된 두 번째 문제: wifi-connect 등을 먼저 거쳐서 이미
            # 한참 열려있던 시리얼 연결로 이 probe 를 보내면, 그 사이에 쌓인
            # WiFi 드라이버 백그라운드 로그([dhd] ...flow_ring_create... 등)가
            # OS 수신 버퍼에 안 읽힌 채 남아있다가 이 read_until 의 2초 타임아웃을
            # 백로그 소진에만 다 써버려서, 정작 probe 응답("Unknown command")이
            # 뒤늦게 도착했을 땐 이미 시간 초과로 거짓 판정이 난 뒤였다(preset을
            # 단독 실행하면 시리얼을 그때 막 새로 열어서 백로그가 없으니 항상
            # 되고, 전체 시퀀스에 묶어서 실행하면 wifi-connect 가 남긴 백로그
            # 때문에 재현되는 것으로 실측 확인됨). probe 를 보내기 직전에 입력
            # 버퍼를 비워서, 이 probe 에 대한 응답만 깨끗하게 기다리게 한다.
            if hasattr(ser, "reset_input_buffer"):
                try:
                    ser.reset_input_buffer()
                except Exception:  # noqa: BLE001
                    pass
            ctx.log(f"$ {send_text!r}", "info")
            ctx.log_result(f"$ {send_text}")
            ser.write((send_text + "\n").encode("utf-8", errors="replace"))
            ctx.check_running()
        ctx.log(f"\U0001f500 {label}: '{pattern}' (timeout {timeout:g}s)", "info")
        found, buf, idle = ss.read_until(
            ser, pattern, regex, timeout,
            on_line=ctx._on_line, running_check=ctx.is_running,
        )
        ctx.check_running()

    if found:
        ctx.log("  → 참 경로 실행", "ok")
        _run_nodes(node["true_children"], ctx)
    else:
        ctx.log("  → 거짓 경로 실행", "mute")
        _run_nodes(node["false_children"], ctx)


def _sleep_interruptible(seconds: float, ctx: StepContext):
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        ctx.check_running()
        time.sleep(min(0.1, max(0.0, end - time.time())))


def _run_nodes(nodes, ctx: StepContext):
    for node in nodes:
        ctx.check_running()
        if node["kind"] == "loop":
            _run_loop(node, ctx)
        elif node["kind"] == "if":
            _run_if(node, ctx)
        else:
            block = node["block"]
            ctx.report_active(block.get("id"))
            fn = _ACTIONS.get(block["type"])
            if fn is None:
                raise ValueError(f"unknown block type {block['type']}")
            fn(block, ctx)


def _run_loop(node, ctx: StepContext):
    """for (var = start; var OP end; var += step) 와 동일하게 실행한다.
    infinite=True 면 Stop 누를 때까지 var 를 계속 증가시키며 반복한다."""
    p = node["start"]["params"]
    loop_id = node["start"].get("id")
    label = p.get("label") or "Loop"
    var_name = p.get("var_name", "i")
    ctx.log(f"\U0001f501 {label}: {st.for_loop_repr(p)}", "info")

    try:
        if p.get("infinite"):
            step = st.parse_num(p.get("step", 1) or 1)
            v = st.parse_num(p.get("start", 0))
            i = 0
            while True:
                ctx.check_running()
                i += 1
                ctx.vars[var_name] = v
                ctx.loop_progress[loop_id] = (i, 0)  # total=0 -> 화면엔 "∞" 로 표시됨
                ctx.log(f"─── {label} #{i} ({var_name}={st.fmt_var(v)}) ───", "info")
                try:
                    _run_nodes(node["children"], ctx)
                except LoopBreak:
                    ctx.log(f"  ⏹ {label}: Break 로 종료", "mute")
                    break
                v += step
        else:
            values = st.for_loop_values(p)
            total = len(values)
            for i, v in enumerate(values, start=1):
                ctx.check_running()
                ctx.vars[var_name] = v
                ctx.loop_progress[loop_id] = (i, total)
                ctx.log(f"─── {label} {i}/{total} ({var_name}={st.fmt_var(v)}) ───", "info")
                try:
                    _run_nodes(node["children"], ctx)
                except LoopBreak:
                    ctx.log(f"  ⏹ {label}: Break 로 종료", "mute")
                    break
    finally:
        # Stop/에러로 중간에 빠져나가도 이 Loop 의 진행률 표시는 지워야
        # 바깥/다음 블록 하이라이트가 엉뚱한 "3/10" 배지를 계속 달고 있지 않는다.
        ctx.loop_progress.pop(loop_id, None)
        ctx.vars.pop(var_name, None)


def run(blocks: list[dict], ctx: StepContext):
    """블록 시퀀스를 처음부터 끝까지 한 번 실행한다.
    (전체를 반복하고 싶으면 맨 바깥을 LOOP 블록으로 감싸면 된다.)

    Break 블록이 Loop 로 안 감싸인 채(맨 바깥, 또는 Loop 없는 If 경로 등) 실행돼서
    LoopBreak 이 여기까지 올라오는 경우 - 에러로 죽이는 대신 그냥 시퀀스가 거기서
    끝난 것으로 조용히 처리한다(사용자가 실수로 Loop 밖에 Break 를 둔 경우를 대비)."""
    tree = st.build_tree(blocks)
    try:
        _run_nodes(tree, ctx)
    except LoopBreak:
        ctx.log("⏹ Break: 감싸는 Loop 가 없어서 시퀀스를 여기서 종료합니다", "mute")
