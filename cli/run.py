"""
GUI 없이 블록 시퀀스(JSON)를 실행하는 CLI 러너.

GUI(main_app.py)와 완전히 같은 엔진(step_engine.run)을 쓰기 때문에, 블록 에디터에서
만든 프로파일/프리셋/저장 파일을 그대로 Linux PC 나 Jenkins 에서 돌릴 수 있다.

사용 예:
    python cli/run.py openwrt-boot                       # profiles/ → presets/ 순으로 이름 검색
    python cli/run.py saved_blocks/iperf.json            # 파일 경로 직접 지정
    python cli/run.py iperf-test --port /dev/serial/by-id/...-if00-port0 --baud 921600
    python cli/run.py boot-loop --var count=100 --junit out/junit.xml

포트/baud 결정 순서: --port/--baud 인자 > 환경변수 BOARD_PORT/BOARD_BAUD
                      > remote_power.env 의 BOARD_PORT/BOARD_BAUD > settings.json
Tapo 접속 정보는 GUI 와 같이 remote_power.env(TAPO_EMAIL/TAPO_PASSWORD/TAPO_IP)에서
읽고, 시퀀스 안에 POWER 블록이 있을 때만 접속한다.

로그(--log):
  full (기본) : 실행 전체를 <시각>_<이름>.log 로 저장
  fail        : 전체 로그는 남기지 않고, 실패했을 때만 "마지막 전원 ON ~ 실패 지점"
                구간을 <시각>_<이름>_FAIL.log 로 저장 (부팅 반복 테스트용 - 앞서 정상
                부팅된 회차들의 로그는 버린다). 전원 ON 이 없는 시퀀스는 시작부터.

종료 코드: 0=PASS, 1=FAIL(타임아웃 stop / Save 판정 FAIL / 전원 제어 실패),
           2=ERROR(설정 오류, 예외), 130=사용자 중단(Ctrl-C)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import signal
import sys
import threading
import time
from xml.sax.saxutils import escape as xml_escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config_manager as cm  # noqa: E402
import serial_session as ss  # noqa: E402
import step_engine as se  # noqa: E402
import step_types as st  # noqa: E402

EXIT_PASS, EXIT_FAIL, EXIT_ERROR, EXIT_INTERRUPTED = 0, 1, 2, 130

_SAVE_RE = re.compile(r"^=== SAVE \[(?P<label>.*?)\](?: \[(?P<verdict>PASS|FAIL)\])? ===")

_COLORS = {"ok": "\033[32m", "err": "\033[31m", "info": "\033[36m",
           "mute": "\033[90m", "exec": "\033[34m"}
_RESET = "\033[0m"


# ─────────────────────────────────────────────
# 시퀀스 / 설정 로드
# ─────────────────────────────────────────────
def load_sequence(target: str) -> tuple[str, list[dict], dict]:
    """파일 경로면 그 파일을, 아니면 profiles/ → presets/ 에서 같은 이름을 찾는다.
    세 번째 반환값(meta)은 시퀀스 파일 최상위의 선택 항목들:
      "vars": {"key": "기본값"}  - --var 로 안 주면 이 값으로 {key} 치환
      "post": ["fc_sg"]          - 실행 후 돌릴 결과 처리기 (cli/post_<이름>.py)
      "log": "fail"              - --log 를 안 줬을 때의 기본값 (긴 반복 테스트용)
      "after_fail": {"timeout": 120, "idle": 30, "until": "DM>"}
                                 - 실패 뒤에도 보드 출력을 더 받아서(워치독 리셋 후 재부팅 등)
                                   실패 로그에 "실패 이후" 로 남긴다"""
    path = target if os.path.isfile(target) else None
    if path is None:
        for d in (cm.PROFILES_DIR, cm.PRESETS_DIR):
            cand = os.path.join(d, target + ".json")
            if os.path.isfile(cand):
                path = cand
                break
    if path is None:
        raise FileNotFoundError(
            f"'{target}' 를 찾을 수 없습니다 (파일 경로도 아니고 profiles/, presets/ 에도 없음)")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    default_name = os.path.splitext(os.path.basename(path))[0]
    if isinstance(data, list):
        return default_name, data, {}
    meta = {"vars": data.get("vars") or {}, "post": data.get("post") or [],
            "log": data.get("log"), "after_fail": data.get("after_fail")}
    return data.get("name") or default_name, data["blocks"], meta


def run_post(name: str, lines: list[str], result: dict, base: str) -> dict:
    """cli/post_<name>.py 의 process(lines, result, base) 를 불러 결과를 받는다."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"post_{name}.py")
    spec = importlib.util.spec_from_file_location(f"post_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.process(lines, result, base)


def has_power_block(blocks: list[dict]) -> bool:
    for b in blocks:
        if b.get("type") == st.POWER:
            return True
        if b.get("type") == st.PRESET and has_power_block(b.get("params", {}).get("blocks", [])):
            return True
    return False


def resolve_serial(args, env: dict) -> tuple[str, int]:
    settings = cm.load_settings() if os.path.exists(cm.SETTINGS_PATH) else {}
    port = (args.port or os.environ.get("BOARD_PORT") or env.get("BOARD_PORT")
            or settings.get("com_port"))
    baud = (args.baud or os.environ.get("BOARD_BAUD") or env.get("BOARD_BAUD")
            or settings.get("baud_rate") or 115200)
    if not port:
        raise ValueError("시리얼 포트가 지정되지 않았습니다 (--port 또는 BOARD_PORT)")
    return port, int(baud)


def parse_vars(items: list[str]) -> dict:
    """값은 항상 문자열 그대로 둔다 - 엔진(StepContext.subst)은 int 변수를 hex
    (fmt_var, Loop 레지스터 스윕용)로 치환하기 때문에, --var n=7 이 '0x7' 로
    바뀌지 않게 하려면 문자열이어야 한다."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--var 형식 오류: '{item}' (key=value)")
        k, v = item.split("=", 1)
        out[k] = v
    return out


class NoTapo:
    """POWER 블록이 없는 시퀀스용 - 혹시 불리면 명확한 에러로 실패시킨다."""
    def set_power(self, state):
        return False, "Tapo 가 설정되지 않았습니다 (remote_power.env 확인)"


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
class Runner:
    def __init__(self, args):
        self.args = args
        self.running = True
        self.saves: list[dict] = []
        self.stop_reason = ""
        self._last_err = ""
        self._log_file = None
        self._segment: list[str] = []   # 마지막 부팅(전원 ON / reboot) 이후 줄들 (--log fail 용)
        self._prev_segment: list[str] = []  # 그 직전 부팅의 줄들 (실패 로그에 "이전 부팅" 으로 같이 남김)
        self.fail_reason = ""           # 블록이 알려준 실패 사유 (멈춤 등)
        self._all: list[str] = []       # 전체 줄 (결과 처리기 post_* 용, 타임스탬프 없이)
        self._progress: dict = {}       # 마지막으로 본 Loop 진행 상황 {loop_id: (i, total)}
        self.fail_match = ""
        self._lock = threading.Lock()
        self._color = (sys.stdout.isatty() or args.color) and not args.no_color

    # ---- 콜백 -------------------------------------------------------
    def log(self, msg: str, tag: str = "info"):
        if tag == "err":
            self._last_err = msg.strip()
        if self.args.quiet and tag in ("mute", "exec"):
            return
        line = f"{_COLORS.get(tag, '')}{msg}{_RESET}" if self._color else msg
        with self._lock:
            print(line, flush=True)

    def log_result(self, msg: str):
        m = _SAVE_RE.match(msg)
        if m:
            self.saves.append({"label": m.group("label"), "verdict": m.group("verdict")})
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        with self._lock:
            self._segment.append(line)
            self._all.append(msg)
            if self._log_file:
                self._log_file.write(line)
                self._log_file.flush()

    def on_event(self, name: str, data: dict):
        if name == "power" and data.get("state") == "on":
            with self._lock:
                # 새 부팅 시작 - 직전 부팅 하나만 남기고 그 이전 회차 로그는 버린다
                self._prev_segment, self._segment = self._segment, []
            it = self.iteration_text()
            self.log_result(f"=== POWER ON{' (' + it + ')' if it else ''} ===")
        elif name == "boot":
            # 전원 ON 없이 명령으로 재부팅(kern reboot 등)한 경우도 새 부팅으로 본다.
            with self._lock:
                self._prev_segment, self._segment = self._segment, []
            it = self.iteration_text()
            self.log_result(f"=== REBOOT ({data.get('label', '')}){' (' + it + ')' if it else ''} ===")
        elif name == "fail_pattern":
            self.fail_match = data.get("match", "")
        elif name == "reason":
            self.fail_reason = data.get("text", "")

    def on_active(self, block_id, loop_progress: dict):
        self._progress = loop_progress   # Loop 밖이면 {} (끝난 Loop 의 회차가 남지 않게)
        if block_id:
            self._last_block = block_id  # 실패 시 어느 블록에서였는지 결과에 남김

    def iteration_text(self) -> str:
        return ", ".join(f"{i}/{t}" if t else f"{i}/∞" for i, t in self._progress.values())

    # ---- 실패 뒤처리 ---------------------------------------------------
    _RR_RE = re.compile(r"rr=(0x[0-9a-fA-F]+\([^)]*\))")

    def _capture_after_fail(self, ctx, cfg):
        """실패 뒤에도 보드 출력을 조금 더 받아서 실패 로그에 남긴다 (멈춘 뒤 워치독 리셋 →
        재부팅되는 과정 등). until 이 나오거나, idle 초 동안 조용하거나, timeout 이 되면 끝."""
        if not cfg:
            return
        timeout = float(cfg.get("timeout", 120))
        idle = float(cfg.get("idle", 30))
        until = cfg.get("until") or "DM>"
        self.log(f"실패 이후 로그 수집 (최대 {timeout:g}s, '{until}' 나오거나 {idle:g}s 조용하면 끝)", "mute")
        self.log_result(f"=== AFTER FAIL (최대 {timeout:g}s) ===")
        self._after_start = len(self._segment)
        try:
            ss.read_until(ctx.ser, until, False, timeout, on_line=ctx._on_line,
                          idle_timeout=idle, running_check=self.is_running)
        except Exception as e:  # noqa: BLE001
            self.log(f"실패 이후 로그 수집 중단: {type(e).__name__}: {e}", "err")

    def _fail_reason_text(self, fallback: str) -> str:
        m = self.fail_match
        if self.fail_reason:
            reason = self.fail_reason
        elif m and m.startswith("rr="):
            reason = f"워치독 리셋 ({m})"
        elif m and m.startswith("U-Boot"):
            reason = f"예상치 못한 재부팅 ({m} 다시 출력)"
        elif m:
            reason = f"실패 패턴 감지: {m}"
        else:
            reason = fallback
        # 실패한 부팅에서 커널이 올라온 뒤에 다시 찍힌 리셋 원인(rr=...)이 있으면 = 그 뒤에
        # 보드가 리셋됨 (멈춤 → 워치독 등). 실패 로그만 보고도 원인을 알 수 있게 사유에 붙인다.
        text = "".join(self._segment)
        k = text.find("Starting kernel")
        rr = self._RR_RE.findall(text[k:]) if k >= 0 else []
        if rr and "rr=" not in reason:
            reason += f" → 이후 리셋 원인 rr={rr[0]}"
        return reason

    def is_running(self) -> bool:
        return self.running

    # ---- 본체 -------------------------------------------------------
    def run(self) -> int:
        args = self.args
        try:
            name, blocks, meta = load_sequence(args.target)
            env = cm.load_env()
            port, baud = resolve_serial(args, env)
            init_vars = {k: str(v) for k, v in meta["vars"].items()}
            init_vars.update(parse_vars(args.var))
            posts = list(meta["post"]) + [x for x in (args.post or []) if x not in meta["post"]]
            if args.log is None:
                args.log = meta.get("log") or "full"
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_ERROR

        out_dir = os.path.abspath(args.out_dir or os.path.join(cm.BASE_DIR, "logs"))
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(out_dir, f"{stamp}_{name}")
        log_path = base + ".log"
        if args.log == "full":
            self._log_file = open(log_path, "w", encoding="utf-8")
        else:
            log_path = None

        started = time.time()
        status, reason = "ERROR", ""
        ctx = None
        try:
            tapo = NoTapo()
            if has_power_block(blocks):
                tapo = self._connect_tapo(env)
                if tapo is None:
                    raise se.StopRequested()

            def serial_factory():
                import serial  # pyserial
                self.log(f"Opening {port} @ {baud}", "info")
                return ss.open_serial(serial, port, baud, timeout=1.0)

            ctx = se.StepContext(
                serial_factory=serial_factory, tapo=tapo,
                log=self.log, log_result=self.log_result, is_running=self.is_running,
                on_active=self.on_active, on_event=self.on_event,
            )
            ctx.vars.update(init_vars)
            self.log_result(f"=== TEST START === sequence: {name}")
            self.log(f"▶ {name} ({len(blocks)} blocks) → log: {log_path or '실패 시에만 저장'}", "info")
            se.run(blocks, ctx)
            status = "PASS"
        except se.StopRequested:
            if self.running:
                status, reason = "FAIL", self._last_err or "stop"
            else:
                status, reason = "INTERRUPTED", "사용자 중단"
        except Exception as e:  # noqa: BLE001
            status, reason = "ERROR", f"{type(e).__name__}: {e}"
            self.log(f"ERROR: {reason}", "err")
        finally:
            if status == "FAIL" and ctx is not None and ctx.ser is not None:
                self._capture_after_fail(ctx, meta.get("after_fail"))
            if ctx is not None and ctx.ser is not None:
                try:
                    ctx.ser.close()
                except Exception:  # noqa: BLE001
                    pass

        failed_saves = [s["label"] for s in self.saves if s["verdict"] == "FAIL"]
        if status == "PASS" and failed_saves:
            status, reason = "FAIL", "Save 판정 FAIL: " + ", ".join(failed_saves)

        if status == "FAIL":
            reason = self._fail_reason_text(reason)
        iteration = self.iteration_text()
        if iteration and status != "PASS":
            reason = f"[{iteration}회차] {reason}"

        duration = time.time() - started
        self.log_result(f"=== TEST END === {status} {reason}".rstrip())
        if self._log_file:
            self._log_file.close()
        fail_log = None
        if status in ("FAIL", "ERROR") and args.log == "fail":
            fail_log = base + "_FAIL.log"
            with open(fail_log, "w", encoding="utf-8") as f:
                if self._prev_segment:
                    f.write("##### 이전 부팅 (정상) #####\n")
                    f.writelines(self._prev_segment)
                    f.write("\n##### 실패한 부팅 #####\n")
                f.writelines(self._segment)
            log_path = fail_log

        result = {
            "sequence": name, "target": args.target, "status": status, "reason": reason,
            "started": datetime.datetime.fromtimestamp(started).isoformat(timespec="seconds"),
            "duration_sec": round(duration, 1), "port": port, "baud": baud,
            "vars": init_vars, "iteration": iteration, "saves": self.saves, "log": log_path,
            "block": getattr(self, "_last_block", ""),
        }
        for pname in posts:
            try:
                out = run_post(pname, self._all, result, base)
                result.setdefault("post", {})[pname] = out
                if out.get("status") == "FAIL" and result["status"] == "PASS":
                    result["status"], result["reason"] = "FAIL", out.get("reason", pname)
                    status, reason = result["status"], result["reason"]
                for ln in out.get("summary_lines", []):
                    self.log(ln, "info")
            except Exception as e:  # noqa: BLE001
                self.log(f"post '{pname}' 처리 실패: {type(e).__name__}: {e}", "err")
                result.setdefault("post", {})[pname] = {"error": f"{type(e).__name__}: {e}"}

        json_path = args.json or (base + ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if args.junit:
            write_junit(args.junit, result)

        tag = "ok" if status == "PASS" else "err"
        self.log(f"■ {status} ({duration:.1f}s){' - ' + reason if reason else ''}", tag)
        if log_path:
            self.log(f"  log: {log_path}", "mute")
        self.log(f"  result: {json_path}", "mute")
        return {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL,
                "INTERRUPTED": EXIT_INTERRUPTED}.get(status, EXIT_ERROR)

    def _connect_tapo(self, env: dict):
        from tapo_control import TapoController
        ip, email, pw = env.get("TAPO_IP"), env.get("TAPO_EMAIL"), env.get("TAPO_PASSWORD")
        if not (ip and email and pw):
            self.log("remote_power.env 에 TAPO_EMAIL / TAPO_PASSWORD / TAPO_IP 가 필요합니다", "err")
            return None
        tapo = TapoController()
        ok, msg = tapo.connect(ip, email, pw)
        self.log(f"Tapo {ip}: {msg}", "ok" if ok else "err")
        return tapo if ok else None


def write_junit(path: str, r: dict):
    """Jenkins junit 플러그인용 최소 JUnit XML. 시퀀스 1개 = testcase 1개,
    Save 블록마다 testcase 를 하나씩 더 만든다(판정 있는 것만)."""
    cases = []
    name = xml_escape(r["sequence"])
    body = ""
    if r["status"] == "FAIL":
        body = f'<failure message="{xml_escape(r["reason"], {chr(34): "&quot;"})}"/>'
    elif r["status"] in ("ERROR", "INTERRUPTED"):
        body = f'<error message="{xml_escape(r["reason"], {chr(34): "&quot;"})}"/>'
    cases.append(f'<testcase classname="gbt" name="{name}" time="{r["duration_sec"]}">{body}'
                 f'<system-out>{xml_escape(r["log"] or "")}</system-out></testcase>')
    for s in r["saves"]:
        if not s["verdict"]:
            continue
        fail = '<failure message="FAIL"/>' if s["verdict"] == "FAIL" else ""
        cases.append(f'<testcase classname="gbt.{name}" name="{xml_escape(s["label"])}">{fail}</testcase>')
    n_fail = sum(1 for c in cases if "<failure" in c)
    n_err = sum(1 for c in cases if "<error" in c)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<testsuite name="gct-board-tester" tests="{len(cases)}" failures="{n_fail}" '
           f'errors="{n_err}" time="{r["duration_sec"]}">\n  ' + "\n  ".join(cases) + "\n</testsuite>\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="블록 시퀀스 headless 실행기")
    p.add_argument("target", help="프로파일/프리셋 이름 또는 시퀀스 JSON 파일 경로")
    p.add_argument("--port", help="시리얼 포트 (예: /dev/serial/by-id/...-if00-port0, COM4)")
    p.add_argument("--baud", type=int, help="baud rate (기본: settings.json 또는 115200)")
    p.add_argument("--var", action="append", metavar="KEY=VALUE",
                   help="시퀀스 안의 {KEY} 를 치환할 변수 (여러 번 지정 가능)")
    p.add_argument("--out-dir", help="로그/결과 저장 폴더 (기본: logs/)")
    p.add_argument("--json", help="결과 JSON 경로 (기본: <out-dir>/<시각>_<이름>.json)")
    p.add_argument("--log", choices=["full", "fail"], default=None,
                   help="full=전체 로그 저장(기본), fail=실패 시 마지막 부팅 시작(전원 ON 또는 "
                        "reboot)~실패 구간만 저장. 안 주면 시퀀스 파일의 \"log\" 값, 그것도 없으면 full")
    p.add_argument("--post", action="append", metavar="NAME",
                   help="실행 후 결과 처리기 (cli/post_NAME.py). 시퀀스 파일의 \"post\" 에도 지정 가능")
    p.add_argument("--junit", help="JUnit XML 출력 경로 (Jenkins 용)")
    p.add_argument("-q", "--quiet", action="store_true", help="UART/Exec 원본 줄은 화면에 안 찍음(파일엔 남음)")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--color", action="store_true", help="파이프로 넘겨도 색 유지 (sweep_campaign.py 가 씀)")
    args = p.parse_args(argv)

    runner = Runner(args)

    def _sigint(signum, frame):  # noqa: ARG001
        if not runner.running:  # 두 번째 Ctrl-C 는 즉시 종료
            raise KeyboardInterrupt
        runner.running = False
        print("\n중단 요청됨 - 현재 블록 정리 후 종료합니다 (한 번 더 누르면 강제 종료)", file=sys.stderr)

    signal.signal(signal.SIGINT, _sigint)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
