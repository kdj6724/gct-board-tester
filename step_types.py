"""
블록(스텝) 정의 모듈
====================
테스트 시퀀스를 이루는 "블록"들의 타입, 기본 파라미터, 표시용 메타데이터를 정의한다.

시퀀스는 항상 "평평한 리스트(flat list)"로 저장된다. LOOP 블록은
LOOP_START / LOOP_END 두 개의 마커로 표현되며, 그 사이에 놓인 블록들이
반복 대상이 된다. 중첩(nested) 루프도 이 방식으로 표현 가능하다.

예)
    [POWER(on), WAIT_STRING(boot), SEND(enter),
     LOOP_START(count=5),
         SEND(cmd1), WAIT_STRING(#),
     LOOP_END,
     SAVE_RESULT(label), POWER(off)]
"""
from __future__ import annotations
import itertools
import uuid

# ─────────────────────────────────────────────
# 블록 타입 상수
# ─────────────────────────────────────────────
POWER = "POWER"
WAIT_STRING = "WAIT_STRING"
SEND = "SEND"
KNOCK = "KNOCK"
CTRL = "CTRL"
SAVE_RESULT = "SAVE_RESULT"
DELAY = "DELAY"
UPLOAD_SCRIPT = "UPLOAD_SCRIPT"
LOOP_START = "LOOP_START"
LOOP_END = "LOOP_END"
IF_START = "IF_START"
IF_ELSE = "IF_ELSE"
IF_END = "IF_END"

# 팔레트(사용자가 새 블록을 추가할 때 고르는 목록)에 노출되는 타입
# LOOP_END 는 LOOP_START 와, IF_ELSE/IF_END 는 IF_START 와 항상 세트로 생성되므로
# 팔레트에는 없다.
# 확정된 블록 목록: Power / Check / Input / Knock / Delay / Script / Loop / If
# (Save 블록은 없음 - 로그 저장은 메인 화면의 저장 on/off 버튼으로 처리)
# Knock 은 Input 과 달리 응답이 올 때까지 일정 간격으로 같은 입력을 반복 전송한다
# (한 번 찔러서는 안 깨어나는 프롬프트를 대비 - 보통 Input+Check 조합으로 되지만,
# "한 번만 보내고 끝"인 Input 과 구분하려고 별도 블록으로 분리함).
PALETTE_TYPES = [POWER, WAIT_STRING, SEND, KNOCK, CTRL, DELAY, UPLOAD_SCRIPT, "LOOP", "IF"]

# 블록 타입별 표시 정보 (라벨 / 색상)
BLOCK_META = {
    POWER:         {"label": "⏻ Power",  "color": "#f38ba8", "text_color": "#1e1e2e"},
    WAIT_STRING:   {"label": "\U0001f50d Check", "color": "#89b4fa", "text_color": "#1e1e2e"},
    SEND:          {"label": "⌨  Input",  "color": "#a6e3a1", "text_color": "#1e1e2e"},
    KNOCK:         {"label": "\U0001f44a Knock",  "color": "#b4befe", "text_color": "#1e1e2e"},
    CTRL:          {"label": "⌃ Ctrl",  "color": "#eba0ac", "text_color": "#1e1e2e"},
    SAVE_RESULT:   {"label": "\U0001f4be Save",  "color": "#f9e2af", "text_color": "#1e1e2e"},
    DELAY:         {"label": "⏱  Delay",          "color": "#6c7086", "text_color": "#cdd6f4"},
    UPLOAD_SCRIPT: {"label": "\U0001f4c4 Script", "color": "#94e2d5", "text_color": "#1e1e2e"},
    LOOP_START:    {"label": "\U0001f501 Loop",         "color": "#cba6f7", "text_color": "#1e1e2e"},
    LOOP_END:      {"label": "└─ End Loop",   "color": "#cba6f7", "text_color": "#1e1e2e"},
    IF_START:      {"label": "\U0001f500 If",           "color": "#fab387", "text_color": "#1e1e2e"},
    IF_ELSE:       {"label": "── Else ──",    "color": "#fab387", "text_color": "#1e1e2e"},
    IF_END:        {"label": "└─ End If",     "color": "#fab387", "text_color": "#1e1e2e"},
}

_counter = itertools.count(1)


def new_id(prefix="b"):
    return f"{prefix}{next(_counter)}_{uuid.uuid4().hex[:6]}"


def default_params(block_type: str) -> dict:
    if block_type == POWER:
        return {"state": "on", "delay_after": 2.0}
    if block_type == WAIT_STRING:
        return {"pattern": "bcfg: cmn nv load complete", "regex": False,
                "timeout": 40.0, "on_timeout": "stop"}
    if block_type == SEND:
        return {"text": "", "append_enter": True, "delay_after": 0.3, "check": None}
    if block_type == KNOCK:
        # Input 과 다르게 "한 번 보내고 끝"이 아니라, pattern 이 나타날 때까지
        # interval 초마다 text 를 반복 전송한다.
        return {"text": "", "append_enter": True, "interval": 2.0,
                "pattern": "", "regex": False, "timeout": 30.0, "on_timeout": "stop"}
    if block_type == CTRL:
        # ping 처럼 스스로 안 끝나는 명령을 강제로 멈출 때 쓰는 raw 제어 문자 전송.
        # (Enter 를 덧붙이는 일반 텍스트가 아니라 단일 제어 바이트 하나만 보낸다)
        return {"key": "C", "delay_after": 0.3}
    if block_type == SAVE_RESULT:
        return {"label": "result", "pass_pattern": "", "fail_pattern": "", "regex": False}
    if block_type == DELAY:
        return {"seconds": 1.0}
    if block_type == UPLOAD_SCRIPT:
        return {"script": "#!/bin/sh\necho \"TEST_DONE\"\n", "target_path": "/tmp/runtest.sh",
                "source_path": ""}
    if block_type == LOOP_START:
        # for (var_name = start; var_name OP end; var_name += step) 와 동일한 개념.
        # infinite=True 면 조건 무시하고 Stop 누를 때까지 반복.
        return {"label": "Loop", "var_name": "i", "start": "0", "op": "<", "end": "100",
                "step": "1", "infinite": False}
    if block_type == LOOP_END:
        return {}
    if block_type == IF_START:
        # Check 블록과 동일한 개념: pattern 이 timeout 안에 나타나면 참(True) 경로,
        # 못 찾으면 거짓(False) 경로로 분기한다 (stop/continue 개념 없음 - 항상 둘 중 하나로 진행).
        return {"label": "If", "pattern": "", "regex": False, "timeout": 10.0}
    if block_type in (IF_ELSE, IF_END):
        return {}
    raise ValueError(f"unknown block type: {block_type}")


def make_block(block_type: str, params: dict | None = None, loop_id: str | None = None) -> dict:
    """새 블록 dict 생성. block_type == 'LOOP' 이면 LOOP_START 를 만든다."""
    real_type = LOOP_START if block_type == "LOOP" else block_type
    b = {"id": new_id(), "type": real_type, "params": params or default_params(real_type)}
    if real_type == LOOP_START:
        b["loop_id"] = loop_id or new_id("loop")
    return b


def make_loop_pair(params: dict | None = None) -> tuple[dict, dict]:
    lid = new_id("loop")
    start = {"id": new_id(), "type": LOOP_START, "loop_id": lid,
             "params": params or default_params(LOOP_START)}
    end = {"id": new_id(), "type": LOOP_END, "loop_id": lid, "params": {}}
    return start, end


def make_if_triple(params: dict | None = None) -> tuple[dict, dict, dict]:
    """If 블록 세트(IF_START, IF_ELSE, IF_END)를 만든다. 항상 세 개가 함께 생성/삭제된다."""
    fid = new_id("if")
    start = {"id": new_id(), "type": IF_START, "if_id": fid,
             "params": params or default_params(IF_START)}
    else_ = {"id": new_id(), "type": IF_ELSE, "if_id": fid, "params": {}}
    end = {"id": new_id(), "type": IF_END, "if_id": fid, "params": {}}
    return start, else_, end


# ─────────────────────────────────────────────
# 평평한 리스트 <-> 깊이(depth) / 매칭 계산 유틸
# ─────────────────────────────────────────────

_CONTAINER_START = (LOOP_START, IF_START)
_CONTAINER_END = (LOOP_END, IF_END)


def compute_depths(blocks: list[dict]) -> list[int]:
    """각 블록의 들여쓰기 깊이를 계산한다. LOOP_START/IF_START 는 자신의 깊이,
    LOOP_END/IF_END 는 대응하는 시작 블록과 동일한 깊이를 갖는다.
    IF_ELSE 는 구분자일 뿐 깊이를 바꾸지 않는다(자식과 같은 깊이)."""
    depths = []
    depth = 0
    for b in blocks:
        if b["type"] in _CONTAINER_END:
            depth -= 1
            depths.append(depth)
        else:
            depths.append(depth)
            if b["type"] in _CONTAINER_START:
                depth += 1
    return depths


def validate(blocks: list[dict]) -> str | None:
    """LOOP_START/LOOP_END, IF_START/IF_ELSE/IF_END 짝이 맞는지 검사.
    문제 있으면 에러 메시지, 없으면 None."""
    stack: list[dict] = []
    for b in blocks:
        t = b["type"]
        if t == LOOP_START:
            stack.append({"type": "LOOP"})
        elif t == IF_START:
            stack.append({"type": "IF", "seen_else": False})
        elif t == LOOP_END:
            if not stack or stack[-1]["type"] != "LOOP":
                return "짝이 맞지 않는 Loop 종료 블록이 있습니다."
            stack.pop()
        elif t == IF_ELSE:
            if not stack or stack[-1]["type"] != "IF":
                return "If 블록 밖에 Else 구분선이 있습니다."
            if stack[-1]["seen_else"]:
                return "If 블록에 Else 구분선이 중복되어 있습니다."
            stack[-1]["seen_else"] = True
        elif t == IF_END:
            if not stack or stack[-1]["type"] != "IF":
                return "짝이 맞지 않는 If 종료 블록이 있습니다."
            stack.pop()
    if stack:
        return "닫히지 않은 Loop/If 블록이 있습니다."
    return None


def find_matching_end(blocks: list[dict], start_index: int) -> int:
    """blocks[start_index] 가 LOOP_START/IF_START 일 때 대응하는 종료 블록의 인덱스를 반환."""
    assert blocks[start_index]["type"] in _CONTAINER_START
    depth = 0
    for i in range(start_index, len(blocks)):
        if blocks[i]["type"] in _CONTAINER_START:
            depth += 1
        elif blocks[i]["type"] in _CONTAINER_END:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("matching end block not found")


def find_if_else(blocks: list[dict], start_index: int) -> int | None:
    """IF_START 에 대응하는 (같은 깊이의) IF_ELSE 인덱스. 없으면 None."""
    assert blocks[start_index]["type"] == IF_START
    depth = 0
    for i in range(start_index, len(blocks)):
        t = blocks[i]["type"]
        if t in _CONTAINER_START:
            depth += 1
        elif t in _CONTAINER_END:
            depth -= 1
            if depth == 0:
                return None
        elif t == IF_ELSE and depth == 1:
            return i
    return None


def loop_span(blocks: list[dict], start_index: int) -> tuple[int, int]:
    """LOOP_START/IF_START 블록이 차지하는 [start, end] 인덱스 범위(inclusive)."""
    return start_index, find_matching_end(blocks, start_index)


def build_tree(blocks: list[dict]) -> list[dict]:
    """평평한 리스트를 실행용 트리로 변환한다.
    노드: {"kind": "action", "block": b}
       또는 {"kind": "loop", "start": b, "end": b, "children": [...]}
       또는 {"kind": "if", "start": b, "else_": b|None, "end": b,
             "true_children": [...], "false_children": [...]}"""
    err = validate(blocks)
    if err:
        raise ValueError(err)
    root: list = []
    stack = [root]
    frames: list[tuple[str, dict]] = []
    for b in blocks:
        t = b["type"]
        if t == LOOP_START:
            node = {"kind": "loop", "start": b, "end": None, "children": []}
            stack[-1].append(node)
            stack.append(node["children"])
            frames.append(("loop", node))
        elif t == LOOP_END:
            _, node = frames.pop()
            node["end"] = b
            stack.pop()
        elif t == IF_START:
            node = {"kind": "if", "start": b, "else_": None, "end": None,
                    "true_children": [], "false_children": []}
            stack[-1].append(node)
            stack.append(node["true_children"])
            frames.append(("if", node))
        elif t == IF_ELSE:
            _, node = frames[-1]
            node["else_"] = b
            stack.pop()
            stack.append(node["false_children"])
        elif t == IF_END:
            _, node = frames.pop()
            node["end"] = b
            stack.pop()
        else:
            stack[-1].append({"kind": "action", "block": b})
    return root


def parse_num(s) -> int:
    s = str(s).strip()
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    v = int(s, 16) if s.lower().startswith("0x") else int(s)
    return -v if neg else v


_CMP_OPS = {
    "<": lambda v, end: v < end,
    "<=": lambda v, end: v <= end,
    ">": lambda v, end: v > end,
    ">=": lambda v, end: v >= end,
    "!=": lambda v, end: v != end,
}

_FOR_LOOP_SAFETY_CAP = 200_000


def for_loop_values(params: dict) -> list[int]:
    """for (var = start; var OP end; var += step) 를 그대로 흉내낸 값 목록을 만든다.
    infinite=True 이면 빈 리스트를 반환한다 (호출부에서 무한 루프로 별도 처리)."""
    if params.get("infinite"):
        return []
    start = parse_num(params.get("start", 0))
    end = parse_num(params.get("end", 100))
    step = parse_num(params.get("step", 1) or 1)
    op = params.get("op", "<")
    cmp = _CMP_OPS.get(op, _CMP_OPS["<"])
    values = []
    v = start
    guard = 0
    while cmp(v, end):
        values.append(v)
        v += step
        guard += 1
        if guard >= _FOR_LOOP_SAFETY_CAP:
            break
        if step == 0:
            break
    return values


def fmt_var(v: int) -> str:
    return hex(v) if v >= 0 else "-" + hex(-v)


def for_loop_repr(params: dict) -> str:
    """for(i=0; i<100; i+=1) 형태의 사람이 읽기 쉬운 문자열."""
    var = params.get("var_name", "i")
    if params.get("infinite"):
        return f"for ({var}={params.get('start',0)}; Stop까지; {var}+={params.get('step',1)})"
    return (f"for ({var}={params.get('start',0)}; {var}{params.get('op','<')}"
            f"{params.get('end',100)}; {var}+={params.get('step',1)})")
