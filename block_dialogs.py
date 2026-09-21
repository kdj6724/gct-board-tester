"""
블록 파라미터 편집용 모달 다이얼로그들.
각 edit_* 함수는 (parent, params:dict) 를 받아 사용자가 OK 를 누르면 새 params
dict 를, Cancel 을 누르면 None 을 반환한다.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import scrolledtext, filedialog

BG = "#1e1e2e"; CARD = "#2a2a3e"; FG = "#cdd6f4"; MUTE = "#6c7086"
ACC = "#7c6af7"; FIELD = "#313244"


class _Dialog:
    def __init__(self, parent, title, width=440, height=320):
        self.result = None
        self.width = width
        self.height = height
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.configure(bg=BG)
        self.win.geometry(f"{width}x{height}")
        self.win.minsize(width, 160)
        self.win.transient(parent)
        self.win.grab_set()
        self.body = tk.Frame(self.win, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=(14, 6))
        self.btns = tk.Frame(self.win, bg=BG)
        self.btns.pack(fill="x", padx=16, pady=(0, 12))
        tk.Button(self.btns, text="Cancel", command=self._cancel,
                  font=("Consolas", 10), bg=FIELD, fg=FG, relief="flat", bd=0,
                  padx=14, pady=5, cursor="hand2").pack(side="right", padx=(8, 0))
        tk.Button(self.btns, text="OK", command=self._ok,
                  font=("Consolas", 10, "bold"), bg=ACC, fg="#1e1e2e", relief="flat",
                  bd=0, padx=14, pady=5, cursor="hand2").pack(side="right")

    def _label(self, text):
        # wraplength 를 안 주면 긴 문장이 창 밖으로 잘려서 겹쳐 보인다 - 창 너비에 맞춰
        # 줄바꿈되게 한다.
        tk.Label(self.body, text=text, font=("Consolas", 10), bg=BG, fg=MUTE,
                 wraplength=self.width - 48, justify="left").pack(
            anchor="w", pady=(8, 2), fill="x")

    def _entry(self, var, width=30):
        e = tk.Entry(self.body, textvariable=var, width=width, font=("Consolas", 11),
                     bg=FIELD, fg=FG, insertbackground=FG, relief="flat", bd=5)
        e.pack(anchor="w", fill="x")
        return e

    def _check(self, text, var):
        tk.Checkbutton(self.body, text=text, variable=var, font=("Consolas", 10),
                        bg=BG, fg=FG, activebackground=BG, selectcolor=FIELD,
                        wraplength=self.width - 48, justify="left").pack(
            anchor="w", pady=(6, 0), fill="x")

    def _radio_row(self, var, options):
        row = tk.Frame(self.body, bg=BG)
        row.pack(anchor="w", pady=(2, 0))
        for val, label in options:
            tk.Radiobutton(row, text=label, variable=var, value=val, font=("Consolas", 10),
                           bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG).pack(
                side="left", padx=(0, 12))

    def _ok(self):
        try:
            self.result = self.collect()
        except Exception as e:  # noqa: BLE001
            tk.Label(self.body, text=f"입력 오류: {e}", fg="#f38ba8", bg=BG,
                     font=("Consolas", 9)).pack(anchor="w")
            return
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()

    def collect(self):  # override
        raise NotImplementedError

    def show(self):
        self.win.wait_window()
        return self.result


def edit_power(parent, params: dict):
    d = _Dialog(parent, "Power 블록", height=230)
    state = tk.StringVar(value=params.get("state", "on"))
    d._label("전원 상태")
    d._radio_row(state, [("on", "ON"), ("off", "OFF")])
    delay = tk.StringVar(value=str(params.get("delay_after", 2.0)))
    d._label("동작 후 대기 시간 (초)")
    d._entry(delay)

    def collect():
        return {"state": state.get(), "delay_after": float(delay.get())}
    d.collect = collect
    return d.show()


def edit_wait_string(parent, params: dict):
    d = _Dialog(parent, "String Check 블록", height=340)
    pattern = tk.StringVar(value=params.get("pattern", ""))
    d._label("검사할 문자열 (regex 체크 시 정규식, '|' 로 복수 매칭 가능)")
    d._entry(pattern, width=40)
    regex = tk.BooleanVar(value=params.get("regex", False))
    d._check("정규식으로 취급", regex)
    timeout = tk.StringVar(value=str(params.get("timeout", 30)))
    d._label("타임아웃 (초)")
    d._entry(timeout)
    on_timeout = tk.StringVar(value=params.get("on_timeout", "stop"))
    d._label("타임아웃 시 동작")
    d._radio_row(on_timeout, [("stop", "테스트 중단"), ("continue", "다음 블록 진행")])

    def collect():
        return {"pattern": pattern.get(), "regex": bool(regex.get()),
                 "timeout": float(timeout.get()), "on_timeout": on_timeout.get()}
    d.collect = collect
    return d.show()


def edit_send(parent, params: dict):
    d = _Dialog(parent, "Input 블록", height=570)
    d._label("보낼 문자열 / 키 입력 ({var} 변수 사용 가능, 비워두면 Enter만 전송) - "
             "여러 줄이면 한 줄씩 순서대로 보냅니다")
    # width 를 안 주면 Text 위젯 기본값(80자)이 다이얼로그 폭보다 훨씬 넓어져서
    # 창 밖으로 잘려나가고 그 아래 위젯들이 겹쳐 보이는 문제가 생긴다 - 다이얼로그
    # 폭에 맞는 글자 수로 명시해준다.
    txt = scrolledtext.ScrolledText(d.body, height=4, width=44, font=("Consolas", 11), bg=FIELD, fg=FG,
                                     insertbackground=FG, relief="flat", bd=4, wrap="none")
    txt.pack(fill="x")
    txt.insert("1.0", params.get("text", ""))
    enter = tk.BooleanVar(value=params.get("append_enter", True))
    d._check("전송 후 Enter(\\n) 추가", enter)
    delay = tk.StringVar(value=str(params.get("delay_after", 0.3)))
    d._label("줄(명령) 사이 대기 시간 (초) - 여러 줄이면 각 줄 전송 뒤마다 이만큼 기다립니다")
    d._entry(delay, width=10)

    check_params = params.get("check") or {}
    attach = tk.BooleanVar(value=params.get("check") is not None)
    sep = tk.Frame(d.body, bg=MUTE, height=1); sep.pack(fill="x", pady=(14, 6))
    d._label("※ 응답은 Check 없이도 자동으로 캡처됩니다. 아래는 특정 문자열이 뜰 때까지 "
             "더 오래 기다려야 할 때만(예: 새 프롬프트) 켜세요.")
    d._check("응답 Check 붙이기 (특정 문자열까지 더 오래 대기)", attach)

    check_frame = tk.Frame(d.body, bg=BG)
    check_frame.pack(anchor="w", fill="x", pady=(4, 0))
    tk.Label(check_frame, text="확인할 문자열", font=("Consolas", 9), bg=BG, fg=MUTE).pack(anchor="w")
    c_pattern = tk.StringVar(value=check_params.get("pattern", "#"))
    tk.Entry(check_frame, textvariable=c_pattern, width=30, font=("Consolas", 11), bg=FIELD, fg=FG,
             insertbackground=FG, relief="flat", bd=5).pack(anchor="w", pady=(0, 6))
    c_regex = tk.BooleanVar(value=check_params.get("regex", False))
    tk.Checkbutton(check_frame, text="정규식으로 취급", variable=c_regex, font=("Consolas", 10),
                    bg=BG, fg=FG, activebackground=BG, selectcolor=FIELD).pack(anchor="w")
    row = tk.Frame(check_frame, bg=BG); row.pack(anchor="w", pady=(6, 0))
    tk.Label(row, text="타임아웃(초)", font=("Consolas", 9), bg=BG, fg=MUTE).pack(side="left")
    c_timeout = tk.StringVar(value=str(check_params.get("timeout", 30)))
    tk.Entry(row, textvariable=c_timeout, width=8, font=("Consolas", 11), bg=FIELD, fg=FG,
             insertbackground=FG, relief="flat", bd=5).pack(side="left", padx=(6, 16))
    c_on_timeout = tk.StringVar(value=check_params.get("on_timeout", "stop"))
    tk.Radiobutton(row, text="중단", variable=c_on_timeout, value="stop", font=("Consolas", 10),
                    bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG).pack(side="left")
    tk.Radiobutton(row, text="다음 진행", variable=c_on_timeout, value="continue", font=("Consolas", 10),
                    bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG).pack(side="left", padx=(8, 0))

    def _sync(*_):
        if attach.get():
            check_frame.pack(anchor="w", fill="x", pady=(4, 0))
        else:
            check_frame.pack_forget()
    attach.trace_add("write", _sync)
    _sync()

    def collect():
        check = None
        if attach.get():
            check = {"pattern": c_pattern.get(), "regex": bool(c_regex.get()),
                      "timeout": float(c_timeout.get()), "on_timeout": c_on_timeout.get()}
        return {"text": txt.get("1.0", "end").rstrip("\n"), "append_enter": bool(enter.get()),
                 "delay_after": float(delay.get()), "check": check}
    d.collect = collect
    return d.show()


def edit_knock(parent, params: dict):
    d = _Dialog(parent, "Knock 블록", height=590)
    d._label("보낼 문자열 / 키 입력 ({var} 등 루프 변수 사용 가능, 비워두면 Enter만 전송) - "
             "Input과 달리 아래 문자열을 찾을 때까지 반복 전송합니다")
    txt = scrolledtext.ScrolledText(d.body, height=3, width=44, font=("Consolas", 11), bg=FIELD, fg=FG,
                                     insertbackground=FG, relief="flat", bd=4, wrap="none")
    txt.pack(fill="x")
    txt.insert("1.0", params.get("text", ""))
    enter = tk.BooleanVar(value=params.get("append_enter", True))
    d._check("전송 후 Enter(\\n) 추가", enter)

    d._label("재전송 간격 (초) - 응답이 오든 안 오든 이 주기마다 다시 보냅니다")
    interval = tk.StringVar(value=str(params.get("interval", 2.0)))
    d._entry(interval, width=10)

    sep = tk.Frame(d.body, bg=MUTE, height=1); sep.pack(fill="x", pady=(14, 6))
    d._label("확인할 문자열 (regex 체크 시 정규식, '|' 로 복수 매칭 가능)")
    pattern = tk.StringVar(value=params.get("pattern", ""))
    d._entry(pattern, width=40)
    regex = tk.BooleanVar(value=params.get("regex", False))
    d._check("정규식으로 취급", regex)
    timeout = tk.StringVar(value=str(params.get("timeout", 30)))
    d._label("전체 타임아웃 (초) - 이 시간 안에 못 찾으면 실패")
    d._entry(timeout, width=10)
    on_timeout = tk.StringVar(value=params.get("on_timeout", "stop"))
    d._label("타임아웃 시 동작")
    d._radio_row(on_timeout, [("stop", "테스트 중단"), ("continue", "다음 블록 진행")])

    def collect():
        return {"text": txt.get("1.0", "end").rstrip("\n"), "append_enter": bool(enter.get()),
                 "interval": float(interval.get()), "pattern": pattern.get(),
                 "regex": bool(regex.get()), "timeout": float(timeout.get()),
                 "on_timeout": on_timeout.get()}
    d.collect = collect
    return d.show()


def edit_ctrl(parent, params: dict):
    d = _Dialog(parent, "Ctrl 블록", width=480, height=260)
    d._label("보낼 제어 키 - Input과 달리 Enter 없이 이 키 하나만 raw 로 전송합니다\n"
             "(ping 처럼 스스로 안 끝나는 명령을 강제로 멈출 때 사용)")
    key = tk.StringVar(value=params.get("key", "C"))
    d._radio_row(key, [("C", "Ctrl+C (중단)"), ("D", "Ctrl+D (EOF)"),
                        ("Z", "Ctrl+Z (일시정지)")])
    d._label("전송 후 대기 시간 (초)")
    delay = tk.StringVar(value=str(params.get("delay_after", 0.3)))
    d._entry(delay, width=10)

    def collect():
        return {"key": key.get() or "C", "delay_after": float(delay.get())}
    d.collect = collect
    return d.show()


def edit_save_result(parent, params: dict):
    d = _Dialog(parent, "Save 블록", height=380)
    d._label("결과 라벨")
    label = tk.StringVar(value=params.get("label", "result"))
    d._entry(label)
    d._label("PASS 판정 문자열 (직전 String Check 이후 수신 내용에서 검색, 비우면 사용 안 함)")
    pass_pat = tk.StringVar(value=params.get("pass_pattern", ""))
    d._entry(pass_pat)
    d._label("FAIL 판정 문자열 (있으면 PASS 보다 우선)")
    fail_pat = tk.StringVar(value=params.get("fail_pattern", ""))
    d._entry(fail_pat)
    regex = tk.BooleanVar(value=params.get("regex", False))
    d._check("정규식으로 취급", regex)

    def collect():
        return {"label": label.get(), "pass_pattern": pass_pat.get(),
                 "fail_pattern": fail_pat.get(), "regex": bool(regex.get())}
    d.collect = collect
    return d.show()


def edit_delay(parent, params: dict):
    d = _Dialog(parent, "Delay 블록", height=180)
    d._label("대기 시간 (초)")
    sec = tk.StringVar(value=str(params.get("seconds", 1.0)))
    d._entry(sec)

    def collect():
        return {"seconds": float(sec.get())}
    d.collect = collect
    return d.show()


def edit_upload_script(parent, params: dict):
    d = _Dialog(parent, "Script 블록", height=460)
    d._label("업로드 대상 경로 (보드 쪽 파일 경로)")
    target = tk.StringVar(value=params.get("target_path", "/tmp/runtest.sh"))
    d._entry(target)

    head = tk.Frame(d.body, bg=BG)
    head.pack(fill="x", pady=(10, 2))
    tk.Label(head, text="스크립트 내용", font=("Consolas", 10), bg=BG, fg=MUTE).pack(side="left")

    txt = scrolledtext.ScrolledText(d.body, height=10, width=44, font=("Consolas", 11), bg=FIELD, fg=FG,
                                     insertbackground=FG, relief="flat", bd=4, wrap="none")
    txt.pack(fill="both", expand=True)
    txt.insert("1.0", params.get("script", ""))
    path_lbl = tk.Label(d.body, text=params.get("source_path", ""), font=("Consolas", 8),
                        bg=BG, fg=MUTE)
    path_lbl.pack(anchor="w", pady=(2, 0))

    def _load_file():
        path = filedialog.askopenfilename(title="스크립트 파일 불러오기",
                                          filetypes=[("Shell script", "*.sh"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:  # noqa: BLE001
            path_lbl.config(text=f"불러오기 실패: {e}")
            return
        txt.delete("1.0", "end")
        txt.insert("1.0", content)
        path_lbl.config(text=f"불러온 파일: {path}")
        d._loaded_source_path = path

    tk.Button(head, text="\U0001f4c2 파일에서 불러오기", command=_load_file, font=("Consolas", 9),
              bg=FIELD, fg=FG, relief="flat", bd=0, padx=10, pady=3, cursor="hand2").pack(side="right")

    def collect():
        result = {"target_path": target.get(), "script": txt.get("1.0", "end").rstrip("\n") + "\n"}
        loaded = getattr(d, "_loaded_source_path", params.get("source_path", ""))
        if loaded:
            result["source_path"] = loaded
        return result
    d.collect = collect
    return d.show()


def edit_if(parent, params: dict):
    d = _Dialog(parent, "If 블록", height=350)
    d._label("라벨")
    label = tk.StringVar(value=params.get("label", "If"))
    d._entry(label, width=20)
    d._label("검사할 문자열 (regex 체크 시 정규식, '|' 로 복수 매칭 가능)")
    pattern = tk.StringVar(value=params.get("pattern", ""))
    d._entry(pattern, width=40)
    regex = tk.BooleanVar(value=params.get("regex", False))
    d._check("정규식으로 취급", regex)
    timeout = tk.StringVar(value=str(params.get("timeout", 10)))
    d._label("타임아웃 (초) - 이 안에 문자열이 나타나면 참, 아니면 거짓 경로로 진행")
    d._entry(timeout, width=10)

    def collect():
        return {"label": label.get() or "If", "pattern": pattern.get(),
                 "regex": bool(regex.get()), "timeout": float(timeout.get())}
    d.collect = collect
    return d.show()


def edit_loop(parent, params: dict):
    d = _Dialog(parent, "Loop 블록", width=500, height=480)
    d._label("라벨")
    label = tk.StringVar(value=params.get("label", "Loop"))
    d._entry(label, width=20)

    d._label("for ( 변수 = 시작 ; 변수 조건 끝 ; 변수 += 증가 ) 처럼 설정합니다")

    var_name = tk.StringVar(value=params.get("var_name", "i"))
    start = tk.StringVar(value=str(params.get("start", 0)))
    op = tk.StringVar(value=params.get("op", "<"))
    end = tk.StringVar(value=str(params.get("end", 100)))
    step = tk.StringVar(value=str(params.get("step", 1)))
    infinite = tk.BooleanVar(value=params.get("infinite", False))

    def entry(p, var, w):
        tk.Entry(p, textvariable=var, width=w, font=("Consolas", 12), bg=FIELD, fg=FG,
                 insertbackground=FG, relief="flat", bd=5, justify="center").pack(side="left", padx=(2, 2))

    def lbl(p, text, bold=False):
        tk.Label(p, text=text, font=("Consolas", 12, "bold" if bold else "normal"),
                 bg=BG, fg=(MUTE if bold else FG)).pack(side="left")

    def var_lbl(p):
        tk.Label(p, textvariable=var_name, font=("Consolas", 12, "bold"), bg=BG, fg=FG,
                 width=3).pack(side="left")

    row1 = tk.Frame(d.body, bg=BG); row1.pack(anchor="w", pady=(8, 2))
    lbl(row1, "for (", True)
    entry(row1, var_name, 4)
    lbl(row1, " = ")
    entry(row1, start, 8)
    lbl(row1, " ;")

    row2 = tk.Frame(d.body, bg=BG); row2.pack(anchor="w", pady=2)
    lbl(row2, "     ", True)
    var_lbl(row2)
    op_menu = tk.OptionMenu(row2, op, "<", "<=", ">", ">=", "!=")
    op_menu.configure(font=("Consolas", 11), bg=FIELD, fg=FG, relief="flat", highlightthickness=0, width=2)
    op_menu.pack(side="left", padx=(4, 4))
    entry(row2, end, 8)
    lbl(row2, " ;")

    row3 = tk.Frame(d.body, bg=BG); row3.pack(anchor="w", pady=2)
    lbl(row3, "     ", True)
    var_lbl(row3)
    lbl(row3, " += ")
    entry(row3, step, 8)
    lbl(row3, " )", True)

    tk.Checkbutton(d.body, text="무한 반복 (Stop 누를 때까지 - 조건 무시, 값은 계속 증가)",
                    variable=infinite, font=("Consolas", 10), bg=BG, fg=FG,
                    activebackground=BG, selectcolor=FIELD).pack(anchor="w", pady=(10, 0))

    preview = tk.Label(d.body, text="", font=("Consolas", 11, "bold"), bg=BG, fg="#a6e3a1")
    preview.pack(anchor="w", pady=(14, 0))
    hint = tk.Label(d.body, text="시작/끝/증가값은 0x1F0 처럼 16진수도 가능합니다. 다른 블록에서 {변수명} 으로 현재 값을 쓸 수 있습니다.",
                    font=("Consolas", 9), bg=BG, fg=MUTE, wraplength=380, justify="left")
    hint.pack(anchor="w", pady=(6, 0))

    def _update_preview(*_):
        p = {"var_name": var_name.get() or "i", "start": start.get(), "op": op.get(),
             "end": end.get(), "step": step.get(), "infinite": infinite.get()}
        try:
            import step_types as st
            text = st.for_loop_repr(p)
            if not infinite.get():
                n = len(st.for_loop_values(p))
                text += f"   → {n}번 반복"
        except Exception as e:  # noqa: BLE001
            text = f"(설정 확인 중: {e})"
        preview.config(text=text)

    for v in (var_name, start, op, end, step, infinite):
        v.trace_add("write", _update_preview)
    _update_preview()

    def collect():
        return {"label": label.get() or "Loop", "var_name": var_name.get() or "i",
                 "start": start.get(), "op": op.get(), "end": end.get(),
                 "step": step.get(), "infinite": bool(infinite.get())}
    d.collect = collect
    return d.show()


EDITORS = {
    "POWER": edit_power,
    "WAIT_STRING": edit_wait_string,
    "SEND": edit_send,
    "KNOCK": edit_knock,
    "CTRL": edit_ctrl,
    "SAVE_RESULT": edit_save_result,
    "DELAY": edit_delay,
    "UPLOAD_SCRIPT": edit_upload_script,
    "LOOP_START": edit_loop,
    "IF_START": edit_if,
}


def edit_block(parent, block: dict):
    fn = EDITORS.get(block["type"])
    if fn is None:
        return None
    return fn(parent, block["params"])
