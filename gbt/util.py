"""공통 도우미: 로그 (화면은 색깔, 파일은 글자만)

색은 cli/run.py 와 같게: ok 초록 / err 빨강 / info 청록 / mute 회색 / exec 파랑 (빌드·fastboot 출력)
"""
import datetime as dt
import re
import sys

COLORS = {"ok": "\033[32m", "err": "\033[31m", "info": "\033[36m", "mute": "\033[90m", "exec": "\033[34m", "warn": "\033[33m"}
RESET = "\033[0m"
_ERR_RE = re.compile(r"\berror\b|FAILED|fatal|No such file|not found|undefined reference", re.I)
_WARN_RE = re.compile(r"\bwarning\b", re.I)
_logf = None
_color = sys.stdout.isatty()


def set_logfile(path):
    """log() 내용을 화면과 함께 이 파일에도 남김"""
    global _logf
    _logf = open(path, "w", encoding="utf-8") if path else None


def log(msg, tag="info"):
    """tag: ok / err / warn / info / mute / exec"""
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(f"{COLORS.get(tag, '')}{line}{RESET}" if _color else line, flush=True)
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


def out_line(text, logfile=None):
    """빌드 / fastboot 출력 한 줄: 화면에 파란색(에러 줄은 빨강, warning 은 노랑)으로 바로 보여주고 파일에도 남김"""
    tag = "err" if _ERR_RE.search(text) else "warn" if _WARN_RE.search(text) else "exec"
    print(f"{COLORS[tag]}  {text}{RESET}" if _color else f"  {text}", flush=True)
    for f in (logfile, _logf):
        if f:
            f.write(text + "\n")
            f.flush()


def uart_line(board, text):
    """보드 UART 한 줄: 화면에 회색으로 'UART <보드>| ...' (에러성 줄은 빨강), 파일에도 남김"""
    tag = "err" if re.search(r"Error|FAIL|panic|Oops|Unable to handle|illegal", text) else "mute"
    line = f"[{dt.datetime.now():%H:%M:%S}] UART {board}| {text}"
    print(f"{COLORS[tag]}{line}{RESET}" if _color else line, flush=True)
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()
