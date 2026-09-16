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
SAVE_RESULT = "SAVE_RESULT"
DELAY = "DELAY"
UPLOAD_SCRIPT = "UPLOAD_SCRIPT"
LOOP_START = "LOOP_START"
LOOP_END = "LOOP_END"

# 팔레트(사용자가 새 블록을 추가할 때 고르는 목록)에 노출되는 타입
# LOOP_END 는 LOOP_START 와 항상 쌍으로 생성되므로 팔레트에는 없다.
# 확정된 블록 목록: Power / Check / Input / Delay / Script / Loop (Save 블록은 없음 -
# 로그 저장은 메인 화면의 저장 on/off 버튼으로 처리)
PALETTE_TYPES = [POWER, WAIT_STRING, SEND, DELAY, UPLOAD_SCRIPT, "LOOP"]

# 블록 타입별 표시 정보 (라벨 / 색상)
BLOCK_META = {
    POWER:         {"label": "⏻ Power",  "color": "#f38ba8", "text_color": "#1e1e2e"},
    WAIT_STRING:   {"label": "\U0001f50d Check", "color": "#89b4fa", "text_color": "#1e1e2e"},
    SEND:          {"label": "⌨  Input",  "color": "#a6e3a1", "text_color": "#1e1e2e"},
    SAVE_RESULT:   {"label": "\U0001f4be Save",  "color": "#f9e2af", "text_color": "#1e1e2e"},
    DELAY:         {"label": "⏱  Delay",          "color": "#6c7086", "text_color": "#cdd6f4"},
    UPLOAD_SCRIPT: {"label": "\U0001f4c4 Script", "color": "#94e2d5", "text_color": "#1e1e2e"},
    LOOP_START:    {"label": "\U0001f501 Loop",         "color": "#cba6f7", "text_color": "#1e1e2e"},
    LOOP_END:      {"label": "└─ End Loop",   "color": "#cba6f7", "text_color": "#1e1e2e"},
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


# ─────────────────────────────────────────────
# 평평한 리스트 <-> 깊이(depth) / 매칭 계산 유틸
# ─────────────────────────────────────────────

def compute_depths(blocks: list[dict]) -> list[int]:
    """각 블록의 들여쓰기 깊이를 계산한다. LOOP_START 는 자신의 깊이,
    LOOP_END 는 대응하는 LOOP_START 와 동일한 깊이를 갖는다."""
    depths = []
    depth = 0
    for b in blocks:
        if b["type"] == LOOP_END:
            depth -= 1
            depths.append(depth)
        else:
            depths.append(depth)
            if b["type"] == LOOP_START:
                depth += 1
    return depths


def validate(blocks: list[dict]) -> str | None:
    """LOOP_START / LOOP_END 짝이 맞는지 검사. 문제 있으면 에러 메시지, 없으면 None."""
    depth = 0
    for b in blocks:
        if b["type"] == LOOP_START:
            depth += 1
        elif b["type"] == LOOP_END:
            depth -= 1
            if depth < 0:
                return "짝이 맞지 않는 Loop 종료 블록이 있습니다."
    if depth != 0:
        return "닫히지 않은 Loop 블록이 있습니다."
    return None


def find_matching_end(blocks: list[dict], start_index: int) -> int:
    """blocks[start_index] 가 LOOP_START 일 때 대응하는 LOOP_END 의 인덱스를 반환."""
    assert blocks[start_index]["type"] == LOOP_START
    depth = 0
    for i in range(start_index, len(blocks)):
        if blocks[i]["type"] == LOOP_START:
            depth += 1
        elif blocks[i]["type"] == LOOP_END:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("matching LOOP_END not found")


def loop_span(blocks: list[dict], start_index: int) -> tuple[int, int]:
    """LOOP_START 블록이 차지하는 [start, end] 인덱스 범위(inclusive)."""
    return start_index, find_matching_end(blocks, start_index)


def build_tree(blocks: list[dict]) -> list[dict]:
    """평평한 리스트를 실행용 트리로 변환한다.
    노드: {"kind": "action", "block": b}  또는
          {"kind": "loop", "start": b, "end": b, "children": [...]}"""
    err = validate(blocks)
    if err:
        raise ValueError(err)
    root: list = []
    stack = [root]
    starts = []
    for b in blocks:
        if b["type"] == LOOP_START:
            node = {"kind": "loop", "start": b, "end": None, "children": []}
            stack[-1].append(node)
            stack.append(node["children"])
            starts.append(node)
        elif b["type"] == LOOP_END:
            node = starts.pop()
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
