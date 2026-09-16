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

    # ---- 내부 유틸 ---------------------------------------------------
    def check_running(self):
        if not self.is_running():
            raise StopRequested()

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


def _exec_send(block, ctx: StepContext):
    p = block["params"]
    text = ctx.subst(p.get("text", ""))
    ctx.log(f"$ {text!r}", "info")
    ctx.log_result(f"$ {text}")
    ser = ctx.ensure_serial()
    ss.send_text(ser, text, append_enter=bool(p.get("append_enter", True)),
                 delay_after=float(p.get("delay_after", 0) or 0))
    check = p.get("check")
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
    st.DELAY: _exec_delay,
    st.UPLOAD_SCRIPT: _exec_upload_script,
    st.SAVE_RESULT: _exec_save_result,
}


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
        else:
            block = node["block"]
            fn = _ACTIONS.get(block["type"])
            if fn is None:
                raise ValueError(f"unknown block type {block['type']}")
            fn(block, ctx)


def _run_loop(node, ctx: StepContext):
    """for (var = start; var OP end; var += step) 와 동일하게 실행한다.
    infinite=True 면 Stop 누를 때까지 var 를 계속 증가시키며 반복한다."""
    p = node["start"]["params"]
    label = p.get("label") or "Loop"
    var_name = p.get("var_name", "i")
    ctx.log(f"\U0001f501 {label}: {st.for_loop_repr(p)}", "info")

    if p.get("infinite"):
        step = st.parse_num(p.get("step", 1) or 1)
        v = st.parse_num(p.get("start", 0))
        i = 0
        while True:
            ctx.check_running()
            i += 1
            ctx.vars[var_name] = v
            ctx.log(f"─── {label} #{i} ({var_name}={st.fmt_var(v)}) ───", "info")
            _run_nodes(node["children"], ctx)
            v += step
    else:
        values = st.for_loop_values(p)
        total = len(values)
        for i, v in enumerate(values, start=1):
            ctx.check_running()
            ctx.vars[var_name] = v
            ctx.log(f"─── {label} {i}/{total} ({var_name}={st.fmt_var(v)}) ───", "info")
            _run_nodes(node["children"], ctx)
    ctx.vars.pop(var_name, None)


def run(blocks: list[dict], ctx: StepContext):
    """블록 시퀀스를 처음부터 끝까지 한 번 실행한다.
    (전체를 반복하고 싶으면 맨 바깥을 LOOP 블록으로 감싸면 된다.)"""
    tree = st.build_tree(blocks)
    _run_nodes(tree, ctx)
