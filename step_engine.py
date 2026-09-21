"""
블록 시퀀스 실행 엔진.

GUI(Tk)와 분리되어 있어 실제 하드웨어 없이도(가짜 serial/tapo 객체로) 단위
테스트가 가능하다. main_app.py 는 이 모듈의 StepContext/run() 을 통해서만
테스트를 수행한다.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import serial_session as ss
import step_types as st


class StopRequested(Exception):
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


def _exec_upload_script(block, ctx: StepContext):
    p = block["params"]
    target = p.get("target_path", "/tmp/runtest.sh")
    ctx.log(f"Uploading {target} ...", "info")
    ser = ctx.ensure_serial()
    ss.upload_script(ser, p.get("script", ""), target, running_check=ctx.is_running)
    ctx.check_running()
    ctx.log("Script uploaded OK", "ok")


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


_ACTIONS = {
    st.POWER: _exec_power,
    st.WAIT_STRING: _exec_wait_string,
    st.SEND: _exec_send,
    st.KNOCK: _exec_knock,
    st.CTRL: _exec_ctrl,
    st.DELAY: _exec_delay,
    st.UPLOAD_SCRIPT: _exec_upload_script,
    st.SAVE_RESULT: _exec_save_result,
}


def _run_if(node, ctx: StepContext):
    """If 블록 실행: Check 와 동일하게 pattern 을 timeout 안에서 기다렸다가,
    찾으면 참(true_children) 경로, 못 찾으면 거짓(false_children) 경로를 실행한다.
    Check 의 stop/continue 와 달리 항상 둘 중 하나로 진행한다(테스트를 멈추지 않음)."""
    block = node["start"]
    ctx.report_active(block.get("id"))
    p = block["params"]
    pattern = ctx.subst(p.get("pattern", ""))
    timeout = float(p.get("timeout", 10) or 10)
    regex = bool(p.get("regex", False))
    label = p.get("label", "If")
    ctx.log(f"\U0001f500 {label}: '{pattern}' (timeout {timeout:g}s)", "info")
    ser = ctx.ensure_serial()
    found, buf, idle = ss.read_until(
        ser, pattern, regex, timeout,
        on_line=ctx._on_line, running_check=ctx.is_running,
    )
    ctx.check_running()
    if found:
        ctx.log(f"  → 있음: '{pattern}' → 참 경로 실행", "ok")
        _run_nodes(node["true_children"], ctx)
    else:
        ctx.log(f"  → 없음(timeout): '{pattern}' → 거짓 경로 실행", "mute")
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
                _run_nodes(node["children"], ctx)
                v += step
        else:
            values = st.for_loop_values(p)
            total = len(values)
            for i, v in enumerate(values, start=1):
                ctx.check_running()
                ctx.vars[var_name] = v
                ctx.loop_progress[loop_id] = (i, total)
                ctx.log(f"─── {label} {i}/{total} ({var_name}={st.fmt_var(v)}) ───", "info")
                _run_nodes(node["children"], ctx)
    finally:
        # Stop/에러로 중간에 빠져나가도 이 Loop 의 진행률 표시는 지워야
        # 바깥/다음 블록 하이라이트가 엉뚱한 "3/10" 배지를 계속 달고 있지 않는다.
        ctx.loop_progress.pop(loop_id, None)
        ctx.vars.pop(var_name, None)


def run(blocks: list[dict], ctx: StepContext):
    """블록 시퀀스를 처음부터 끝까지 한 번 실행한다.
    (전체를 반복하고 싶으면 맨 바깥을 LOOP 블록으로 감싸면 된다.)"""
    tree = st.build_tree(blocks)
    _run_nodes(tree, ctx)
