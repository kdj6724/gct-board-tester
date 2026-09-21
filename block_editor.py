"""
드래그 앤 드롭 방식의 블록 시퀀스 에디터 (플로우차트 스타일).

- 왼쪽 팔레트에서 블록을 캔버스로 드래그하면 화살표로 이어지는 순서 중
  원하는 위치에 삽입된다. 클릭만 하면 맨 끝에 추가.
- 캔버스에 놓인 블록을 클릭하면 바로 그 블록의 세부 설정 창이 열린다.
- Loop 블록은 굵은 사각 테두리로 자신이 감싼 블록들을 표시한다. 테두리
  라벨(왼쪽 위)을 드래그하면 안의 블록들과 함께 통째로 이동한다. 다른
  블록을 Loop 테두리 안쪽(세로 범위)에 드래그해서 놓으면 그 안에 들어간다.
- 팔레트의 Loop 를 눌러서 드래그하면(클릭만 하면 예전처럼 빈 Loop 추가)
  캔버스 위로 뗀 순간 "wrap 모드"가 켜진다(안내 토스트가 뜸). 이미 놓여
  있는 블록들을 새 Loop 로 감싸려면, 그 상태에서 캔버스 위를 다시 한번
  누르고 위→아래로 드래그해서 뗀다. 시작/끝 둘 다 캔버스 위에서 직접
  누르고 뗀 지점이라 정확하다(팔레트에서 뗀 지점을 그대로 시작점으로
  쓰면, 팔레트→캔버스로 넘어오는 마우스 경로에 따라 들쭉날쭉해져서 이
  방식으로 바꿨다). Esc 로 wrap 모드 취소 가능. 항상 최상위(중첩 안 된)
  블록/컨테이너 단위로만 감싸진다 - top_level_ranges() 참고.
- Loop 테두리 오른쪽 위의 ✕ 는 "벗기기"다: LOOP_START/LOOP_END 마커만
  지우고 안에 있던 블록들은 그대로 남겨서 한 단계 밖으로 꺼낸다(내용물은
  안 지워짐 - unwrap_container()). 안에 있는 블록 하나만 지우고 싶으면
  그 블록 자신의 ✕ 를 눌러야 한다. If 테두리의 ✕ 는 다르다: 참/거짓 두
  갈래를 하나의 순서로 합칠 방법이 없어서 If 는 안의 내용까지 통째로
  삭제된다(delete_block()).
- If 블록도 Loop 와 같은 방식(테두리 박스)으로 표시하되, 박스 안을 점선
  구분선("else")으로 위/아래 나눠서 위쪽엔 참(있으면), 아래쪽엔 거짓(없으면)
  경로를 세로로 쌓아 보여준다. 좌우로 갈라지는 진짜 화살표 분기 대신 이
  방식을 쓴 건 캔버스 레이아웃 복잡도를 Loop 수준으로 유지하기 위함.
- Input 블록에 "응답 Check 붙이기" 를 설정하면 옆으로 작은 Check 박스가
  붙어서 "입력을 보내고 바로 그 응답을 확인한다" 는 하나의 짝으로 보인다.
- 상단의 "저장" 버튼은 별도 파일이 아니라 지금 열려 있는 프로파일에 바로
  저장한다(main_app.py 의 on_save 콜백 -> cm.save_profile). 예전엔 이거랑
  별개로 "파일로 저장"/"파일 불러오기" 로 saved_blocks/ 폴더에 내보내는
  기능도 있었는데, 저장 위치가 두 군데로 나뉘어 헷갈린다는 의견으로 없앴다
  - 이제 저장은 프로파일 하나로 통일. 다른 PC 로 옮기고 싶으면 profiles/
  폴더 안의 해당 .json 을 탐색기에서 그대로 복사하면 된다(포맷이 같음).
- 팔레트 맨 아래엔 저장된 "프리셋" 목록도 함께 뜬다(다른 블록처럼 드래그/클릭
  으로 삽입). 프리셋은 PRESET 타입의 블록 하나로 들어가는데, 위치 이동/삭제는
  Power/Send 같은 다른 단일 블록과 완전히 동일하게 취급된다 - 안에 저장된
  블록들을 params["blocks"] 로 통째로 들고 있을 뿐이다(놓는 순간의 스냅샷).
  캔버스 위 라벨은 타입 이름 대신 그 프리셋의 이름을 보여준다(block_label()).
  클릭하면 파라미터 창 대신 안내 토스트만 뜨고, ▦ 아이콘을 누르면 그 자리에서
  개별 블록들로 "펼치기"(_unpack_preset - 껍데기만 벗기고 내용은 그대로 남김,
  Loop 벗기기와 같은 개념) 할 수 있다.
  기존 캔버스에 놓인 블록 구간을 새 프리셋으로 저장할 땐(상단의 "프리셋으로
  저장" 버튼) Loop 로 "둘러싸기" 할 때와 똑같은 선택 방식(arm 모드 + 캔버스
  위 드래그)을 재사용한다(_arm_wrap_mode 의 action 파라미터로 구분).
  프리셋 자체를 고치거나 새로 만들 땐 "프리셋 관리" 버튼으로 작은 목록 창을
  열고, 편집을 누르면 이 BlockEditorWindow 를 그 프리셋의 블록으로 다시 열어서
  재사용한다(exclude_preset 로 편집 중인 프리셋 자기 자신은 팔레트에서 빼서
  무의미한 자기 자신 중첩만 막아준다 - 그 외 프리셋 안에 다른 프리셋을 넣는
  중첩은 데이터 구조상 자연스럽게 지원되고 특별히 막지 않는다).

캔버스 드로잉/좌표 계산과 순수 리스트 조작 로직(move_range, delete_block,
unwrap_container)은 분리되어 있어 tkinter 이벤트 없이도 단위 테스트할 수 있다.
"""
from __future__ import annotations
import copy
import tkinter as tk
from tkinter import simpledialog, messagebox

import block_dialogs as bd
import config_manager as cm
import step_types as st

BG = "#1e1e2e"; PANEL = "#181825"; CANVAS_BG = "#11111b"
FG = "#cdd6f4"; MUTE = "#6c7086"
ACC = "#7c6af7"; FIELD = "#313244"; SEL = "#f5c2e7"

NODE_W, NODE_H = 240, 46
ATTACH_W, ATTACH_GAP = 150, 22
V_GAP = 40
TOP_MARGIN = 30
LOOP_LABEL_H = 34
LOOP_FOOTER_H = 20
EMPTY_LOOP_CONTENT_H = 56
IF_LABEL_H = 34
IF_ELSE_H = 26
EMPTY_IF_BRANCH_H = 40
NEST_PAD_BASE, NEST_PAD_STEP = 26, 20
CENTER_X = 280
DRAG_THRESHOLD = 5
ACTIVE_COLOR = "#f9e2af"  # 실행 중 블록 하이라이트 색 (노랑)


# ─────────────────────────────────────────────
# 순수 로직 (tkinter 비의존) - 테스트 가능
# ─────────────────────────────────────────────

def move_range(blocks: list[dict], start: int, end: int, target: int) -> list[dict]:
    """blocks[start:end+1] 를 떼어내 target 위치(제거 전 인덱스 기준)로 옮긴 새 리스트를 반환.
    target 이 이동 구간 내부에 있으면(무의미한 이동) 원본을 그대로 반환한다."""
    if start <= target <= end + 1:
        return blocks
    chunk = blocks[start:end + 1]
    rest = blocks[:start] + blocks[end + 1:]
    new_target = target - (end - start + 1) if target > end else target
    new_target = max(0, min(len(rest), new_target))
    return rest[:new_target] + chunk + rest[new_target:]


def delete_block(blocks: list[dict], index: int) -> list[dict]:
    """단일 블록 삭제. Loop/If 컨테이너의 시작/종료 블록이면 그 안의 자식들도 함께 삭제한다.
    (If 는 참/거짓 두 갈래를 하나의 평평한 순서로 합칠 방법이 없어 통째로 삭제한다.
    Loop 는 자식만 남기고 테두리만 벗겨내는 unwrap_container() 를 대신 쓴다 - 아래 참고.)"""
    b = blocks[index]
    if b["type"] in (st.LOOP_START, st.IF_START):
        end = st.find_matching_end(blocks, index)
        return blocks[:index] + blocks[end + 1:]
    if b["type"] in (st.LOOP_END, st.IF_END):
        start = index
        depth = 0
        for i in range(index, -1, -1):
            if blocks[i]["type"] in (st.LOOP_END, st.IF_END):
                depth += 1
            elif blocks[i]["type"] in (st.LOOP_START, st.IF_START):
                depth -= 1
                if depth == 0:
                    start = i
                    break
        return blocks[:start] + blocks[index + 1:]
    return blocks[:index] + blocks[index + 1:]


def unwrap_container(blocks: list[dict], index: int) -> list[dict]:
    """LOOP_START 인덱스를 받아 그 시작/종료 마커만 제거하고, 안에 있던 블록들은
    원래 순서 그대로 한 단계 밖으로 꺼낸다(테두리만 벗겨내기 - 내용물은 삭제 안 됨).
    Loop 테두리의 ✕ 버튼에서 쓴다. 개별 블록을 지우고 싶으면 그 블록 자신의 ✕ 를 쓰면 된다."""
    end = st.find_matching_end(blocks, index)
    inner = blocks[index + 1:end]
    return blocks[:index] + inner + blocks[end + 1:]


def unpack_preset(blocks: list[dict], index: int) -> list[dict]:
    """PRESET 블록 하나(index)를 그 안에 담겨있던 개별 블록들로 그 자리에서 펼친다
    (껍데기만 벗기고 내용은 그대로 - Loop 의 unwrap_container() 와 같은 개념이지만
    PRESET 은 START/END 짝이 아니라 단일 블록이라 훨씬 단순하다). 안의 블록들은
    이미 프리셋을 놓을 때(make_preset_block) id 를 새로 발급받은 것들이라 여기서
    또 복제할 필요는 없다."""
    inner = blocks[index]["params"].get("blocks", [])
    return blocks[:index] + copy.deepcopy(inner) + blocks[index + 1:]


def block_label(block: dict) -> str:
    """캔버스에 굵은 글씨로 보여줄 블록 이름. 프리셋은 고정된 타입 이름("Preset")
    대신 저장할 때 지어준 프리셋 이름을 그대로 보여준다 - 어떤 프리셋인지 한눈에
    알아보려고(사용자 요청)."""
    if block["type"] == st.PRESET:
        return "\U0001f4e6 " + (block["params"].get("preset_name") or "(이름 없음)")
    return st.BLOCK_META[block["type"]]["label"]


def summarize(block: dict) -> str:
    t, p = block["type"], block["params"]
    if t == st.PRESET:
        n = len(p.get("blocks", []))
        return f"프리셋 — {n}개 블록"
    if t == st.POWER:
        return f"{p.get('state','on').upper()}  (+{p.get('delay_after',0):g}s)"
    if t == st.WAIT_STRING:
        s = p.get("pattern", "")
        return f"“{s[:22]}{'…' if len(s) > 22 else ''}”"
    if t == st.SEND:
        s = p.get("text", "") or "(Enter)"
        return f"{s[:22]!r}"
    if t == st.KNOCK:
        s = p.get("text", "") or "(Enter)"
        pat = p.get("pattern", "")
        return f"{s[:12]!r} /{p.get('interval', 2):g}s → “{pat[:14]}{'…' if len(pat) > 14 else ''}”"
    if t == st.CTRL:
        return f"Ctrl+{p.get('key', 'C')}"
    if t == st.DELAY:
        return f"{p.get('seconds',0):g}s"
    if t == st.UPLOAD_SCRIPT:
        return p.get("target_path", "")
    if t == st.EXEC:
        c = p.get("cmd", "")
        # 경로가 기니까 파일명(맨 뒤 구간)만 보여주는 게 오히려 한눈에 안 들어와서,
        # 그냥 앞부분을 잘라서 보여준다 - 긴 fastboot 명령어도 어떤 커맨드인지는 보임.
        return f"$ {c[:26]}{'…' if len(c) > 26 else ''}" if c else "(명령어 없음)"
    return ""


def loop_label(block: dict, progress: tuple[int, int] | None = None) -> str:
    p = block["params"]
    label = p.get("label", "Loop")
    if progress:
        cur, total = progress
        return f"{label}  {cur}/{total if total else '∞'}"
    return f"{label} — {st.for_loop_repr(p)}"


def if_label(block: dict) -> str:
    p = block["params"]
    label = p.get("label", "If")
    pattern = p.get("pattern", "")
    shown = f"{pattern[:20]}{'…' if len(pattern) > 20 else ''}"
    return f"{label} — “{shown}” (timeout {p.get('timeout', 10):g}s)"


def contains_container_count(blocks: list[dict], start: int, end: int) -> int:
    """중첩 폭(패딩) 계산용: 구간 안에 Loop/If 컨테이너가 몇 개 더 있는지."""
    return sum(1 for i in range(start + 1, end)
               if blocks[i]["type"] in (st.LOOP_START, st.IF_START))


def has_attach_child(blocks: list[dict], start: int, end: int) -> bool:
    return any(blocks[i]["type"] == st.SEND and blocks[i]["params"].get("check")
               for i in range(start + 1, end))


def top_level_ranges(blocks: list[dict]) -> list[tuple[int, int]]:
    """중첩되지 않은(depth 0) 블록/컨테이너 단위로 (start_idx, end_idx) 범위 리스트를 반환.
    Loop/If 컨테이너는 시작~끝 마커를 통째로 하나의 항목으로 묶는다.
    기존 블록들을 새 Loop 로 "둘러싸기" 할 때 어느 범위를 통째로 감쌀지 고를 때 쓴다."""
    ranges = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        if b["type"] in (st.LOOP_START, st.IF_START):
            end = st.find_matching_end(blocks, i)
            ranges.append((i, end))
            i = end + 1
        elif b["type"] in (st.LOOP_END, st.IF_END, st.IF_ELSE):
            # 정상적인 flat list 에서는 START 처리 시 건너뛰므로 여기 단독으로 오지 않지만
            # 방어적으로 스킵.
            i += 1
        else:
            ranges.append((i, i))
            i += 1
    return ranges


def compute_layout(blocks: list[dict]) -> dict:
    """블록 리스트 하나로부터 캔버스에 그릴 좌표를 전부 계산한다(순수 함수, tkinter
    비의존). BlockEditorWindow(편집 가능)와 BlockDiagramView(메인 화면의 읽기 전용
    미리보기)가 이 결과를 공유해서 쓴다 - 레이아웃 알고리즘이 한 군데만 있으면 되게.
    반환: {"slots": [...], "loop_boxes": [...], "if_boxes": [...], "total_height": int}"""
    slots = []
    y = TOP_MARGIN
    n = len(blocks)
    i = 0
    while i < n:
        b = blocks[i]
        if b["type"] == st.LOOP_START:
            top = y
            y += LOOP_LABEL_H
            border_top = y
            if i + 1 < n and blocks[i + 1]["type"] == st.LOOP_END:
                y += EMPTY_LOOP_CONTENT_H
            slots.append({"index": i, "kind": "loop_start", "top": top,
                         "bottom": border_top, "border_top": border_top})
        elif b["type"] == st.LOOP_END:
            border_bottom = y
            top = y
            y += LOOP_FOOTER_H
            bottom = y
            y += V_GAP
            slots.append({"index": i, "kind": "loop_end", "top": top,
                         "bottom": bottom, "border_bottom": border_bottom})
        elif b["type"] == st.IF_START:
            top = y
            y += IF_LABEL_H
            border_top = y
            if i + 1 < n and blocks[i + 1]["type"] == st.IF_ELSE:
                y += EMPTY_IF_BRANCH_H
            slots.append({"index": i, "kind": "if_start", "top": top,
                         "bottom": border_top, "border_top": border_top})
        elif b["type"] == st.IF_ELSE:
            top = y
            y += IF_ELSE_H
            bottom = y
            if i + 1 < n and blocks[i + 1]["type"] == st.IF_END:
                y += EMPTY_IF_BRANCH_H
            slots.append({"index": i, "kind": "if_else", "top": top, "bottom": bottom})
        elif b["type"] == st.IF_END:
            border_bottom = y
            top = y
            y += LOOP_FOOTER_H
            bottom = y
            y += V_GAP
            slots.append({"index": i, "kind": "if_end", "top": top,
                         "bottom": bottom, "border_bottom": border_bottom})
        else:
            top = y
            y += NODE_H
            bottom = y
            y += V_GAP
            slots.append({"index": i, "kind": "action", "top": top, "bottom": bottom, "block": b})
        i += 1
    total_height = y + 20

    loop_boxes = []
    for s in slots:
        if s["kind"] != "loop_start":
            continue
        start_i = s["index"]
        end_i = st.find_matching_end(blocks, start_i)
        end_slot = next(sl for sl in slots if sl["index"] == end_i)
        nest = contains_container_count(blocks, start_i, end_i)
        pad = NEST_PAD_BASE + nest * NEST_PAD_STEP
        attach = has_attach_child(blocks, start_i, end_i)
        x0 = CENTER_X - NODE_W / 2 - pad
        x1 = CENTER_X + NODE_W / 2 + pad + (ATTACH_W + ATTACH_GAP if attach else 0)
        empty = (end_i == start_i + 1)
        loop_boxes.append({"start": start_i, "end": end_i, "top": s["border_top"],
                           "bottom": end_slot["border_bottom"], "x0": x0, "x1": x1,
                           "pad": pad, "empty": empty, "block": blocks[start_i]})
    loop_boxes.sort(key=lambda lb: -lb["pad"])

    if_boxes = []
    for s in slots:
        if s["kind"] != "if_start":
            continue
        start_i = s["index"]
        end_i = st.find_matching_end(blocks, start_i)
        else_i = st.find_if_else(blocks, start_i)
        end_slot = next(sl for sl in slots if sl["index"] == end_i)
        else_slot = next((sl for sl in slots if sl["index"] == else_i), None) \
            if else_i is not None else None
        nest = contains_container_count(blocks, start_i, end_i)
        pad = NEST_PAD_BASE + nest * NEST_PAD_STEP
        attach = has_attach_child(blocks, start_i, end_i)
        x0 = CENTER_X - NODE_W / 2 - pad
        x1 = CENTER_X + NODE_W / 2 + pad + (ATTACH_W + ATTACH_GAP if attach else 0)
        else_y = (else_slot["top"] + else_slot["bottom"]) / 2 if else_slot else None
        true_empty = (else_i == start_i + 1) if else_i is not None else (end_i == start_i + 1)
        false_empty = (end_i == else_i + 1) if else_i is not None else False
        if_boxes.append({"start": start_i, "end": end_i, "else_i": else_i,
                         "top": s["border_top"], "bottom": end_slot["border_bottom"],
                         "else_y": else_y, "x0": x0, "x1": x1, "pad": pad,
                         "true_empty": true_empty, "false_empty": false_empty,
                         "block": blocks[start_i]})
    if_boxes.sort(key=lambda ib: -ib["pad"])

    return {"slots": slots, "loop_boxes": loop_boxes, "if_boxes": if_boxes,
            "total_height": total_height}


def draw_diagram(canvas, blocks: list[dict], layout: dict, *, active_index=None,
                  active_progress=None, owner=None):
    """compute_layout() 결과를 캔버스에 그린다. owner 가 주어지면(BlockEditorWindow)
    노드/테두리 클릭-드래그 바인딩도 같이 건다 - 메인 화면의 읽기 전용
    BlockDiagramView 는 owner=None 으로 호출해서 그림만 그리고 아무 것도 클릭 안 되게 한다."""
    c = canvas
    c.delete("all")
    slots, loop_boxes, if_boxes = layout["slots"], layout["loop_boxes"], layout["if_boxes"]

    for lb in loop_boxes:
        color = st.BLOCK_META[st.LOOP_START]["color"]
        dash = (5, 3) if lb["empty"] else None
        c.create_rectangle(lb["x0"], lb["top"], lb["x1"], lb["bottom"],
                           outline=color, width=3, dash=dash, tags=(f"loopbox{lb['start']}",))
        progress = active_progress.get(lb["start"]) if active_progress else None
        label_text = loop_label(lb["block"], progress)
        c.create_text(lb["x0"] + 6, lb["top"] - 14, anchor="w", text=label_text,
                      font=("Consolas", 10, "bold"), fill=color,
                      tags=(f"loophandle{lb['start']}",))
        c.create_text(lb["x1"] - 10, lb["top"] - 14, anchor="e", text="✕",
                      font=("Consolas", 11, "bold"), fill=color,
                      tags=(f"loopdel{lb['start']}",))
        if lb["empty"]:
            c.create_text((lb["x0"] + lb["x1"]) / 2, (lb["top"] + lb["bottom"]) / 2,
                          text="여기에 블록을 드래그하세요",
                          font=("Consolas", 9), fill=MUTE)
        if owner is not None:
            c.tag_bind(f"loophandle{lb['start']}", "<ButtonPress-1>",
                      lambda e, s=lb["start"], en=lb["end"]: owner._press(e, s, en))
            c.tag_bind(f"loophandle{lb['start']}", "<B1-Motion>", owner._motion)
            c.tag_bind(f"loophandle{lb['start']}", "<ButtonRelease-1>", owner._release)
            c.tag_bind(f"loopdel{lb['start']}", "<Button-1>",
                      lambda e, s=lb["start"]: owner._unwrap(s))

    for ib in if_boxes:
        color = st.BLOCK_META[st.IF_START]["color"]
        c.create_rectangle(ib["x0"], ib["top"], ib["x1"], ib["bottom"],
                           outline=color, width=3, tags=(f"ifbox{ib['start']}",))
        # 헤더 옆 작은 마름모 아이콘 (순서도의 조건 분기 느낌)
        dcx, dcy, dw, dh = ib["x0"] + 14, ib["top"] - 14, 8, 7
        c.create_polygon(dcx, dcy - dh, dcx + dw, dcy, dcx, dcy + dh, dcx - dw, dcy,
                         fill=color, outline=color, tags=(f"ifhandle{ib['start']}",))
        label_text = if_label(ib["block"])
        c.create_text(ib["x0"] + 28, ib["top"] - 14, anchor="w", text=label_text,
                      font=("Consolas", 10, "bold"), fill=color,
                      tags=(f"ifhandle{ib['start']}",))
        c.create_text(ib["x1"] - 10, ib["top"] - 14, anchor="e", text="✕",
                      font=("Consolas", 11, "bold"), fill=color,
                      tags=(f"ifdel{ib['start']}",))
        if ib["else_y"] is not None:
            c.create_line(ib["x0"], ib["else_y"], ib["x1"], ib["else_y"],
                          fill=color, width=2, dash=(5, 3))
            c.create_text((ib["x0"] + ib["x1"]) / 2, ib["else_y"], text=" else ",
                          font=("Consolas", 9, "bold"), fill=color, anchor="center")
            c.create_text(ib["x0"] + 8, ib["top"] + 6, anchor="nw", text="✓ 있으면",
                          font=("Consolas", 8), fill=MUTE)
            c.create_text(ib["x0"] + 8, ib["else_y"] + 6, anchor="nw", text="✗ 없으면",
                          font=("Consolas", 8), fill=MUTE)
        if ib["true_empty"]:
            mid_y = ((ib["top"] + ib["else_y"]) / 2) if ib["else_y"] is not None \
                else (ib["top"] + ib["bottom"]) / 2
            c.create_text((ib["x0"] + ib["x1"]) / 2, mid_y,
                          text="여기에 블록을 드래그하세요 (있으면)",
                          font=("Consolas", 9), fill=MUTE)
        if ib["false_empty"] and ib["else_y"] is not None:
            c.create_text((ib["x0"] + ib["x1"]) / 2, (ib["else_y"] + ib["bottom"]) / 2,
                          text="여기에 블록을 드래그하세요 (없으면)",
                          font=("Consolas", 9), fill=MUTE)
        if owner is not None:
            c.tag_bind(f"ifhandle{ib['start']}", "<ButtonPress-1>",
                      lambda e, s=ib["start"], en=ib["end"]: owner._press(e, s, en))
            c.tag_bind(f"ifhandle{ib['start']}", "<B1-Motion>", owner._motion)
            c.tag_bind(f"ifhandle{ib['start']}", "<ButtonRelease-1>", owner._release)
            c.tag_bind(f"ifdel{ib['start']}", "<Button-1>",
                      lambda e, s=ib["start"]: owner._delete(s))

    action_slots = [s for s in slots if s["kind"] == "action"]
    prev_bottom = None
    for s in action_slots:
        if prev_bottom is not None:
            c.create_line(CENTER_X, prev_bottom, CENTER_X, s["top"], fill=FG, width=2, arrow="last")
        prev_bottom = s["bottom"]

    for s in action_slots:
        i, b = s["index"], s["block"]
        meta = st.BLOCK_META[b["type"]]
        x0, y0, x1, y1 = CENTER_X - NODE_W / 2, s["top"], CENTER_X + NODE_W / 2, s["bottom"]
        is_active = (i == active_index)
        if is_active:
            c.create_rectangle(x0 - 5, y0 - 5, x1 + 5, y1 + 5, outline=ACTIVE_COLOR, width=3,
                               tags=(f"node{i}", "activeglow"))
        c.create_rectangle(x0, y0, x1, y1, fill=meta["color"],
                           outline=(ACTIVE_COLOR if is_active else "#1e1e2e"),
                           width=4 if is_active else 2, tags=(f"node{i}",))
        label_text = block_label(b)
        summ = summarize(b)
        if summ:
            c.create_text(x0 + 10, (y0 + y1) / 2 - 9, anchor="w", text=label_text,
                          font=("Consolas", 11, "bold"), fill=meta["text_color"], tags=(f"node{i}",))
            c.create_text(x0 + 10, (y0 + y1) / 2 + 10, anchor="w", text=summ,
                          font=("Consolas", 9), fill=meta["text_color"], tags=(f"node{i}",))
        else:
            c.create_text(x0 + 10, (y0 + y1) / 2, anchor="w", text=label_text,
                          font=("Consolas", 11, "bold"), fill=meta["text_color"], tags=(f"node{i}",))
        if owner is not None:
            c.create_text(x1 - 10, y0 + 12, anchor="e", text="✕", font=("Consolas", 10, "bold"),
                         fill=meta["text_color"], tags=(f"del{i}",))
            c.tag_bind(f"node{i}", "<ButtonPress-1>", lambda e, ii=i: owner._press(e, ii, ii))
            c.tag_bind(f"node{i}", "<B1-Motion>", owner._motion)
            c.tag_bind(f"node{i}", "<ButtonRelease-1>", owner._release)
            c.tag_bind(f"del{i}", "<Button-1>", lambda e, ii=i: owner._delete(ii))
            if b["type"] == st.PRESET:
                # 프리셋은 ✕(삭제) 옆에 "펼치기" 아이콘도 하나 더 - 이 인스턴스만
                # 개별 블록들로 풀어서 그 자리에서 커스터마이즈하고 싶을 때 쓴다.
                c.create_text(x1 - 32, y0 + 12, anchor="e", text="▣",
                             font=("Consolas", 11, "bold"), fill=meta["text_color"],
                             tags=(f"unpack{i}",))
                c.tag_bind(f"unpack{i}", "<Button-1>", lambda e, ii=i: owner._unpack_preset(ii))

        check = b["params"].get("check") if b["type"] == st.SEND else None
        if check:
            ax0 = x1 + ATTACH_GAP
            ax1 = ax0 + ATTACH_W
            cmeta = st.BLOCK_META[st.WAIT_STRING]
            c.create_line(x1, (y0 + y1) / 2, ax0, (y0 + y1) / 2, fill=FG, width=2)
            c.create_rectangle(ax0, y0, ax1, y1, fill=cmeta["color"], outline="#1e1e2e", width=2,
                               tags=(f"node{i}",))
            c.create_text(ax0 + 8, (y0 + y1) / 2 - 9, anchor="w", text=cmeta["label"],
                          font=("Consolas", 10, "bold"), fill=cmeta["text_color"], tags=(f"node{i}",))
            c.create_text(ax0 + 8, (y0 + y1) / 2 + 10, anchor="w",
                          text=f"“{check.get('pattern','')[:16]}”",
                          font=("Consolas", 8), fill=cmeta["text_color"], tags=(f"node{i}",))

    width = max(c.winfo_width(), CENTER_X + NODE_W + ATTACH_W + 260)
    c.configure(scrollregion=(0, 0, width, max(layout["total_height"], c.winfo_height())))


# ─────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────

class BlockEditorWindow(tk.Toplevel):
    def __init__(self, parent, blocks: list[dict], on_save, exclude_preset: str | None = None):
        super().__init__(parent)
        self.title("테스트 시퀀스 편집 (블록 조립)")
        self.geometry("980x680")
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()

        self.blocks: list[dict] = copy.deepcopy(blocks)
        self.on_save = on_save
        # 지금 이 창 자체가 어떤 프리셋을 편집하는 중이면(프리셋 관리 -> 편집),
        # 팔레트의 프리셋 목록에서 그 프리셋 자기 자신은 빼둔다 - 편집 중인 프리셋을
        # 자기 안에 다시 집어넣는 무의미한 자기 중첩만 막기 위함(그 외의 중첩은 허용).
        self._exclude_preset = exclude_preset
        self._slots: list[dict] = []
        self._loop_boxes: list[dict] = []
        self._if_boxes: list[dict] = []
        self._drag = None       # {"start":i,"end":i,"moved":False,"press_y":..}
        self._pal_drag = None   # {"type":..., "ghost":Toplevel|None}
        self._insert_line = None
        self._span_box = None       # Loop/프리셋 "선택" 드래그 중 보여주는 미리보기 사각형
        self._span_selected = []    # 그 사각형이 현재 감싸고 있는 최상위 항목들
        self._wrap_armed = False    # 캔버스에서 범위를 고르길 기다리는 중(Loop 감싸기/프리셋 저장 공용)
        self._wrap_action = "loop"  # 그 범위를 고른 뒤 뭘 할지: "loop"(Loop로 감싸기) / "save_preset"
        self._wrap_press_y = None   # wrap 모드에서 캔버스 위에 실제로 마우스를 누른 지점(정확한 시작점)
        self._active_index = None   # 실행 중 강조 표시할 블록의 flat index (Run 연결 후 사용)
        self._active_progress = None  # Loop 진행률 표시용 {loop_start_index: (cur, total)}
        self._preset_palette_frame = None  # 팔레트 안, 프리셋 버튼들만 담는 프레임(새로고침용)

        self._build_ui()
        self.redraw()

    # ---- UI 구성 ------------------------------------------------------
    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(top, text="왼쪽 블록을 캔버스로 드래그하고, 놓인 블록을 클릭하면 세부 설정이 열립니다",
                 font=("Consolas", 10), bg=BG, fg=MUTE).pack(side="left")
        tk.Button(top, text="저장", command=self._save, font=("Consolas", 10, "bold"),
                  bg=ACC, fg="#1e1e2e", relief="flat", bd=0, padx=14, pady=5,
                  cursor="hand2").pack(side="right")
        tk.Button(top, text="취소", command=self.destroy, font=("Consolas", 10),
                  bg=FIELD, fg=FG, relief="flat", bd=0, padx=14, pady=5,
                  cursor="hand2").pack(side="right", padx=(0, 6))
        tk.Button(top, text="\U0001f4e6 프리셋으로 저장",
                  command=lambda: self._arm_wrap_mode("save_preset"),
                  font=("Consolas", 10), bg=FIELD, fg=FG, relief="flat", bd=0,
                  padx=10, pady=5, cursor="hand2").pack(side="right", padx=(0, 16))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        palette = tk.Frame(body, bg=PANEL, width=170)
        palette.pack(side="left", fill="y")
        tk.Label(palette, text="팔레트 (드래그 / 클릭)", font=("Consolas", 9),
                 bg=PANEL, fg=MUTE).pack(anchor="w", padx=10, pady=(10, 4))
        for t in st.PALETTE_TYPES:
            meta = st.BLOCK_META[self._palette_real_type(t)]
            btn = tk.Label(palette, text=meta["label"], font=("Consolas", 10, "bold"),
                           bg=meta["color"], fg=meta["text_color"], relief="flat",
                           padx=10, pady=8, cursor="hand2")
            btn.pack(fill="x", padx=10, pady=4)
            btn.bind("<ButtonPress-1>", lambda e, tt=t: self._palette_press(e, tt))
            btn.bind("<B1-Motion>", self._palette_motion)
            btn.bind("<ButtonRelease-1>", lambda e, tt=t: self._palette_release(e, tt))

        sep = tk.Frame(palette, bg=MUTE, height=1); sep.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(palette, text="프리셋 (드래그 / 클릭)", font=("Consolas", 9),
                 bg=PANEL, fg=MUTE).pack(anchor="w", padx=10, pady=(8, 4))
        self._preset_palette_frame = tk.Frame(palette, bg=PANEL)
        self._preset_palette_frame.pack(fill="x")
        self._refresh_preset_palette()
        tk.Button(palette, text="\U0001f4e6 프리셋 관리", command=self._manage_presets,
                  font=("Consolas", 9), bg=BG, fg=MUTE, relief="flat", bd=0,
                  padx=10, pady=5, cursor="hand2").pack(fill="x", padx=10, pady=(6, 10))

        canvas_frame = tk.Frame(body, bg=BG)
        canvas_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.canvas = tk.Canvas(canvas_frame, bg=CANVAS_BG, highlightthickness=0)
        vsb = tk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 40), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        # Loop "둘러싸기" 모드(_wrap_armed) 동안만 실제로 뭔가를 하는 캔버스 전체 바인딩.
        # add="+" 로 걸어서 기존 노드별 tag_bind(_press 등)는 그대로 살아있게 둔다 -
        # armed 가 아니면 이 핸들러들은 아무 것도 안 하고 바로 리턴한다.
        self.canvas.bind("<ButtonPress-1>", self._canvas_wrap_press, add="+")
        self.canvas.bind("<B1-Motion>", self._canvas_wrap_motion, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._canvas_wrap_release, add="+")
        self.bind("<Escape>", self._cancel_wrap_mode)

    @staticmethod
    def _palette_real_type(t: str) -> str:
        """팔레트 표시용 타입("LOOP"/"IF"/"PRESET:이름")을 실제 블록 타입으로 변환."""
        if t == "LOOP":
            return st.LOOP_START
        if t == "IF":
            return st.IF_START
        if isinstance(t, str) and t.startswith("PRESET:"):
            return st.PRESET
        return t

    def _refresh_preset_palette(self):
        """팔레트 안의 프리셋 버튼 목록을 저장된 presets/ 로 다시 그린다
        (프리셋을 새로 저장/삭제/수정한 직후에 호출)."""
        frame = self._preset_palette_frame
        for w in frame.winfo_children():
            w.destroy()
        meta = st.BLOCK_META[st.PRESET]
        names = [n for n in cm.list_presets() if n != self._exclude_preset]
        if not names:
            tk.Label(frame, text="(저장된 프리셋 없음)", font=("Consolas", 9),
                     bg=PANEL, fg=MUTE).pack(anchor="w", padx=10, pady=2)
            return
        for name in names:
            btn = tk.Label(frame, text=f"\U0001f4e6 {name}", font=("Consolas", 10, "bold"),
                           bg=meta["color"], fg=meta["text_color"], relief="flat",
                           padx=10, pady=8, cursor="hand2")
            btn.pack(fill="x", padx=10, pady=4)
            block_type = f"PRESET:{name}"
            btn.bind("<ButtonPress-1>", lambda e, tt=block_type: self._palette_press(e, tt))
            btn.bind("<B1-Motion>", self._palette_motion)
            btn.bind("<ButtonRelease-1>", lambda e, tt=block_type: self._palette_release(e, tt))

    # ---- 레이아웃 계산 ---------------------------------------------------
    def _layout(self):
        layout = compute_layout(self.blocks)
        self._slots = layout["slots"]
        self._loop_boxes = layout["loop_boxes"]
        self._if_boxes = layout["if_boxes"]
        self._total_height = layout["total_height"]

    # ---- 렌더링 --------------------------------------------------------
    def redraw(self):
        self._layout()
        layout = {"slots": self._slots, "loop_boxes": self._loop_boxes,
                  "if_boxes": self._if_boxes, "total_height": self._total_height}
        draw_diagram(self.canvas, self.blocks, layout, active_index=self._active_index,
                     active_progress=self._active_progress, owner=self)

    # ---- 실행 중 강조 표시 (Run 로직과 연결해서 사용) ------------------------
    def set_active(self, flat_index: int | None, loop_progress: dict | None = None):
        """현재 실행 중인 블록을 강조 표시한다.
        flat_index: 지금 실행 중인 블록의 인덱스 (self.blocks 기준), 없으면 None.
        loop_progress: {loop_start_index: (현재 반복, 전체 반복)} - 진행 중인 Loop 표시용."""
        self._active_index = flat_index
        self._active_progress = loop_progress
        self.redraw()
        if flat_index is not None:
            slot = next((s for s in self._slots if s["index"] == flat_index), None)
            if slot and self._total_height:
                frac = max(0.0, min(1.0, (slot["top"] - 60) / self._total_height))
                self.canvas.yview_moveto(frac)

    def clear_active(self):
        self.set_active(None, None)

    def _draw_insert_line(self, y):
        c = self.canvas
        if self._insert_line:
            c.delete(self._insert_line)
        width = max(c.winfo_width(), 600)
        self._insert_line = c.create_line(20, y, width - 20, y, fill=SEL, width=3,
                                          dash=(4, 2), tags=("insertline",))

    def _clear_insert_line(self):
        if self._insert_line:
            self.canvas.delete(self._insert_line)
            self._insert_line = None

    # ---- 삽입 위치 계산 ---------------------------------------------------
    def _find_insertion(self, y):
        prev_bottom = TOP_MARGIN - 20
        for s in self._slots:
            mid = (s["top"] + s["bottom"]) / 2
            if y < mid:
                boundary = (prev_bottom + s["top"]) / 2
                return s["index"], boundary
            prev_bottom = s["bottom"]
        return len(self.blocks), prev_bottom + 20

    # ---- 팔레트 드래그 --------------------------------------------------
    def _palette_press(self, event, block_type):
        self._pal_drag = {"type": block_type, "moved": False, "ghost": None,
                           "x0": event.x_root, "y0": event.y_root}

    def _palette_motion(self, event):
        pd = self._pal_drag
        if pd is None:
            return
        if not pd["moved"] and (abs(event.x_root - pd["x0"]) > DRAG_THRESHOLD or
                                 abs(event.y_root - pd["y0"]) > DRAG_THRESHOLD):
            pd["moved"] = True
            meta = st.BLOCK_META[self._palette_real_type(pd["type"])]
            ghost_text = (pd["type"][len("PRESET:"):] if isinstance(pd["type"], str)
                          and pd["type"].startswith("PRESET:") else meta["label"])
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-alpha", 0.85)
            tk.Label(ghost, text=ghost_text, font=("Consolas", 10, "bold"),
                     bg=meta["color"], fg=meta["text_color"], padx=10, pady=6).pack()
            pd["ghost"] = ghost
        if pd["moved"] and pd["ghost"] is not None:
            pd["ghost"].geometry(f"+{event.x_root+6}+{event.y_root+6}")
        if pd["type"] == "LOOP":
            # Loop 는 팔레트에서 뗀 그 자리에서 바로 범위를 잡지 않는다 (캔버스 밖인 팔레트에서
            # 누른 지점은 캔버스 좌표로 의미 있게 변환할 수 없어서, 어디를 "시작"으로 볼지
            # 마우스가 지나온 경로에 따라 들쭉날쭉해짐). 대신 놓는 순간 wrap 모드로 들어가서,
            # 캔버스 위에서 다시 한번 눌러서 드래그하게 한다 - 그러면 시작점도 끝점처럼
            # "누른/뗀 지점 그대로" 로 정확해진다.
            return
        y = self._canvas_y_from_root(event.y_root)
        if y is not None:
            _, boundary = self._find_insertion(y)
            self._draw_insert_line(boundary)
        else:
            self._clear_insert_line()

    def _palette_release(self, event, block_type):
        pd = self._pal_drag
        self._pal_drag = None
        self._clear_insert_line()
        if pd is None:
            return
        if pd["ghost"] is not None:
            pd["ghost"].destroy()
        if not pd["moved"]:
            self._add_block(block_type, len(self.blocks))
            return
        if block_type == "LOOP":
            self._arm_wrap_mode()
            return
        y = self._canvas_y_from_root(event.y_root)
        if y is None:
            return
        idx, _ = self._find_insertion(y)
        self._add_block(block_type, idx)

    # ---- Loop 로 기존 블록 구간 "둘러싸기" / 기존 구간을 "프리셋으로 저장" ----------
    # 팔레트에서 Loop 를 드래그해서 떼거나 "프리셋으로 저장" 버튼을 누르면(1)
    # wrap 모드가 켜지고(_arm_wrap_mode), 그 다음 캔버스 위에서 직접 누르고(2)
    # 끌고(3) 떼는(4) 것으로 범위를 잡는다 - 둘 다 "범위 선택" 동작은 완전히
    # 같고, 그 범위를 고른 뒤 뭘 할지(action: "loop" | "save_preset")만 다르다.
    # (2)~(4) 는 전부 캔버스 위젯 좌표라서 시작/끝 모두 "누른/뗀 그 지점"이 정확히 맞는다.
    def _arm_wrap_mode(self, action="loop"):
        self._wrap_armed = True
        self._wrap_action = action
        self._wrap_press_y = None
        self._clear_span_box()
        if action == "save_preset":
            self._toast("프리셋으로 저장할 블록들을 캔버스에서 위→아래로 눌러서 드래그하세요 (Esc: 취소)")
        else:
            self._toast("Loop 로 감쌀 블록들을 캔버스에서 위→아래로 눌러서 드래그하세요 (Esc: 취소)")

    def _cancel_wrap_mode(self, event=None):
        if not self._wrap_armed:
            return
        self._wrap_armed = False
        self._wrap_press_y = None
        self._clear_span_box()
        self._clear_insert_line()

    def _canvas_wrap_press(self, event):
        if not self._wrap_armed:
            return
        self._wrap_press_y = self.canvas.canvasy(event.y)
        self._preview_single_item_at(self._wrap_press_y)

    def _canvas_wrap_motion(self, event):
        if not self._wrap_armed or self._wrap_press_y is None:
            return
        y = self.canvas.canvasy(event.y)
        if abs(y - self._wrap_press_y) < DRAG_THRESHOLD:
            # 거의 안 움직였으면(클릭에 가까움) 지금 커서가 걸쳐있는 블록 하나만 미리보기
            self._preview_single_item_at(y)
        else:
            self._update_span_preview(self._wrap_press_y, y)

    def _canvas_wrap_release(self, event):
        if not self._wrap_armed:
            return
        y = self.canvas.canvasy(event.y)
        selected = self._span_selected
        if not selected:
            item = self._item_at(self._wrap_press_y if self._wrap_press_y is not None else y)
            selected = [item] if item else []
        self._wrap_armed = False
        self._wrap_press_y = None
        self._clear_span_box()
        if selected:
            if self._wrap_action == "save_preset":
                self._save_as_preset(selected[0]["start"], selected[-1]["end"])
            else:
                self._wrap_in_loop(selected[0]["start"], selected[-1]["end"])

    def _top_level_item_boxes(self):
        """self._slots (직전 _layout() 결과) 기준으로 최상위 항목별 (start,end,top,bottom) 목록."""
        items = []
        for start, end in top_level_ranges(self.blocks):
            top_slot = next(s for s in self._slots if s["index"] == start)
            bottom_slot = next(s for s in self._slots if s["index"] == end)
            items.append({"start": start, "end": end,
                          "top": top_slot["top"], "bottom": bottom_slot["bottom"]})
        return items

    def _item_at(self, y):
        """캔버스 y 좌표 하나가 어느 최상위 항목의 박스 안에 들어가는지 찾는다(없으면 None)."""
        for it in self._top_level_item_boxes():
            if it["top"] <= y <= it["bottom"]:
                return it
        return None

    def _draw_span_box(self, top, bottom):
        c = self.canvas
        if self._span_box is not None:
            c.delete(self._span_box)
            self._span_box = None
        width = max(c.winfo_width(), 600)
        self._span_box = c.create_rectangle(30, top - 8, width - 30, bottom + 8,
                                            outline=SEL, width=3, dash=(4, 2), tags=("spanbox",))

    def _preview_single_item_at(self, y):
        self._clear_insert_line()
        item = self._item_at(y)
        if item:
            self._span_selected = [item]
            self._draw_span_box(item["top"], item["bottom"])
        else:
            self._span_selected = []
            if self._span_box is not None:
                self.canvas.delete(self._span_box)
                self._span_box = None

    def _update_span_preview(self, y0, y1):
        top, bottom = min(y0, y1), max(y0, y1)
        items = self._top_level_item_boxes()
        selected = [it for it in items if top <= (it["top"] + it["bottom"]) / 2 <= bottom]
        self._span_selected = selected
        self._clear_insert_line()
        if selected:
            self._draw_span_box(selected[0]["top"], selected[-1]["bottom"])
        else:
            # 감쌀 만한 블록이 아직 없으면(빈 공간/틈) 기존처럼 삽입 위치로 안내
            if self._span_box is not None:
                self.canvas.delete(self._span_box)
                self._span_box = None
            _, boundary = self._find_insertion((y0 + y1) / 2)
            self._draw_insert_line(boundary)

    def _clear_span_box(self):
        if self._span_box is not None:
            self.canvas.delete(self._span_box)
            self._span_box = None
        self._span_selected = []

    def _wrap_in_loop(self, start, end):
        """이미 캔버스에 놓인 blocks[start:end+1] 구간을 새 Loop 로 통째로 감싼다."""
        start_b, end_b = st.make_loop_pair()
        new_params = bd.edit_loop(self, start_b["params"])
        if new_params is None:
            return
        start_b["params"] = new_params
        self.blocks = (self.blocks[:start] + [start_b] + self.blocks[start:end + 1] +
                       [end_b] + self.blocks[end + 1:])
        self.redraw()

    def _save_as_preset(self, start, end):
        """이미 캔버스에 놓인 blocks[start:end+1] 구간을 이름 붙여 presets/ 에 저장한다.
        (이 창 자체의 self.blocks 는 안 건드린다 - 저장만 하고 현재 시퀀스는 그대로.)"""
        name = simpledialog.askstring("프리셋으로 저장", "프리셋 이름:", parent=self)
        if not name:
            return
        snippet = copy.deepcopy(self.blocks[start:end + 1])
        cm.save_preset(name, snippet)
        self._refresh_preset_palette()
        self._toast(f"프리셋 저장됨: {name} ({len(snippet)}개 블록)")

    def _manage_presets(self):
        """프리셋 목록 창: 새로 만들기 / 편집 / 삭제. 편집은 이 BlockEditorWindow 를
        그 프리셋의 블록으로 다시 열어서(on_save 가 프로파일 대신 그 프리셋에 저장)
        재사용한다 - 프리셋 전용 편집 화면을 따로 만들지 않는다."""
        win = tk.Toplevel(self)
        win.title("프리셋 관리")
        win.configure(bg=BG)
        win.transient(self)
        win.geometry("380x360")
        tk.Label(win, text="편집을 누르면 그 프리셋의 블록을 바로 수정할 수 있습니다",
                 font=("Consolas", 9), bg=BG, fg=MUTE, wraplength=350,
                 justify="left").pack(anchor="w", padx=12, pady=(12, 6))
        list_frame = tk.Frame(win, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=12)

        def _rebuild():
            for w in list_frame.winfo_children():
                w.destroy()
            names = cm.list_presets()
            if not names:
                tk.Label(list_frame, text="(저장된 프리셋 없음)", font=("Consolas", 9),
                         bg=BG, fg=MUTE).pack(anchor="w", pady=4)
            for name in names:
                row = tk.Frame(list_frame, bg=FIELD)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=name, font=("Consolas", 10), bg=FIELD, fg=FG).pack(
                    side="left", padx=8, pady=4)
                tk.Button(row, text="삭제", command=lambda n=name: _delete(n), font=("Consolas", 9),
                          bg=BG, fg=FG, relief="flat", bd=0, padx=8, cursor="hand2").pack(
                    side="right", padx=(0, 4), pady=2)
                tk.Button(row, text="편집", command=lambda n=name: _edit(n), font=("Consolas", 9),
                          bg=ACC, fg="#1e1e2e", relief="flat", bd=0, padx=8, cursor="hand2").pack(
                    side="right", padx=4, pady=2)

        def _on_preset_saved(name, new_blocks):
            cm.save_preset(name, new_blocks)
            self._refresh_preset_palette()
            _rebuild()

        def _edit(name):
            BlockEditorWindow(win, cm.load_preset(name),
                              lambda b, n=name: _on_preset_saved(n, b), exclude_preset=name)

        def _new():
            new_name = simpledialog.askstring("새 프리셋", "프리셋 이름:", parent=win)
            if not new_name:
                return
            BlockEditorWindow(win, [], lambda b, n=new_name: _on_preset_saved(n, b),
                              exclude_preset=new_name)

        def _delete(name):
            if not messagebox.askyesno("삭제 확인", f"'{name}' 프리셋을 삭제합니까?", parent=win):
                return
            cm.delete_preset(name)
            self._refresh_preset_palette()
            _rebuild()

        tk.Button(win, text="+ 새 프리셋", command=_new, font=("Consolas", 10, "bold"),
                  bg=ACC, fg="#1e1e2e", relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2").pack(anchor="w", padx=12, pady=(6, 12))
        _rebuild()

    def _canvas_y_from_root(self, y_root) -> float | None:
        c = self.canvas
        cy = y_root - c.winfo_rooty()
        if cy < 0 or cy > c.winfo_height():
            return None
        return c.canvasy(cy)

    def _add_block(self, block_type, idx):
        if isinstance(block_type, str) and block_type.startswith("PRESET:"):
            # 프리셋은 Power/Send 처럼 파라미터 다이얼로그 없이 바로 그 자리에 놓인다
            # (설정할 게 없음 - 안의 내용은 이미 저장된 프리셋 그대로).
            name = block_type[len("PRESET:"):]
            block = st.make_preset_block(name, cm.load_preset(name))
            self.blocks = self.blocks[:idx] + [block] + self.blocks[idx:]
            self.redraw()
            return
        if block_type == "LOOP":
            start, end = st.make_loop_pair()
            new_params = bd.edit_loop(self, start["params"])
            if new_params is None:
                return
            start["params"] = new_params
            self.blocks = self.blocks[:idx] + [start, end] + self.blocks[idx:]
        elif block_type == "IF":
            start, else_, end = st.make_if_triple()
            new_params = bd.edit_if(self, start["params"])
            if new_params is None:
                return
            start["params"] = new_params
            self.blocks = self.blocks[:idx] + [start, else_, end] + self.blocks[idx:]
        else:
            block = st.make_block(block_type)
            new_params = bd.edit_block(self, block)
            if new_params is not None:
                block["params"] = new_params
            self.blocks = self.blocks[:idx] + [block] + self.blocks[idx:]
        self.redraw()

    # ---- 기존 블록/Loop 드래그 (순서 변경) --------------------------------
    def _press(self, event, start, end):
        if self._wrap_armed:
            # wrap 모드 중엔 노드 클릭이 재배치/편집이 아니라 감쌀 범위 지정으로 쓰여야 하므로,
            # 여긴 아무 것도 안 하고 캔버스 전체 바인딩(_canvas_wrap_press)에 맡긴다.
            return
        self._drag = {"start": start, "end": end, "moved": False,
                      "press_y": self.canvas.canvasy(event.y)}

    def _motion(self, event):
        d = self._drag
        if d is None:
            return
        y = self.canvas.canvasy(event.y)
        if abs(y - d["press_y"]) > DRAG_THRESHOLD:
            d["moved"] = True
        if d["moved"]:
            _, boundary = self._find_insertion(y)
            self._draw_insert_line(boundary)

    def _release(self, event):
        d = self._drag
        self._drag = None
        self._clear_insert_line()
        if d is None:
            return
        y = self.canvas.canvasy(event.y)
        if not d["moved"]:
            self._edit_block(d["start"])
            return
        idx, _ = self._find_insertion(y)
        self.blocks = move_range(self.blocks, d["start"], d["end"], idx)
        self.redraw()

    # ---- 편집 / 삭제 -----------------------------------------------------
    def _edit_block(self, index):
        block = self.blocks[index]
        # LOOP_END/IF_ELSE/IF_END 는 편집할 파라미터가 없는 순수 마커.
        # LOOP_START/IF_START 는 헤더를 클릭하면 다시 설정을 열 수 있게 한다.
        if block["type"] in (st.LOOP_END, st.IF_ELSE, st.IF_END):
            return
        if block["type"] == st.PRESET:
            # 프리셋 블록은 클릭해도 파라미터 창이 없다(설정할 게 없음) - 대신
            # 안내만 보여준다. 개별적으로 손보고 싶으면 ▦ 아이콘으로 펼치면 된다.
            n = len(block["params"].get("blocks", []))
            name = block["params"].get("preset_name", "")
            self._toast(f"프리셋 '{name}' ({n}개 블록) — 펼치려면 ▦ 아이콘을 누르세요")
            return
        new_params = bd.edit_block(self, block)
        if new_params is not None:
            block["params"] = new_params
            self.redraw()

    def _delete(self, index):
        if self._wrap_armed:
            return
        self.blocks = delete_block(self.blocks, index)
        self.redraw()

    def _unpack_preset(self, index):
        """PRESET 블록 하나를 그 자리에서 개별 블록들로 펼친다(껍데기만 벗기고
        내용은 남김 - Loop 의 _unwrap 과 같은 개념)."""
        if self._wrap_armed:
            return
        self.blocks = unpack_preset(self.blocks, index)
        self.redraw()

    def _unwrap(self, index):
        """Loop 테두리의 ✕: 테두리(LOOP_START/LOOP_END)만 제거하고 안의 블록들은 남긴다."""
        if self._wrap_armed:
            return
        self.blocks = unwrap_container(self.blocks, index)
        self.redraw()

    # ---- 저장 ------------------------------------------------------------
    def _save(self):
        err = st.validate(self.blocks)
        if err:
            self._toast(err)
            return
        self.on_save(self.blocks)
        self.destroy()

    def _toast(self, msg):
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-alpha", 0.95)
        x = self.winfo_rootx() + self.winfo_width() // 2 - 150
        y = self.winfo_rooty() + 60
        win.geometry(f"300x40+{x}+{y}")
        tk.Label(win, text=msg, bg="#f38ba8", fg="#1e1e2e", font=("Consolas", 10, "bold")).pack(
            fill="both", expand=True)
        win.after(1800, win.destroy)


# ─────────────────────────────────────────────
# 메인 화면에 박히는 읽기 전용 다이어그램
# ─────────────────────────────────────────────

class BlockDiagramView(tk.Frame):
    """메인 화면에 항상 떠 있는, 편집은 안 되는 블록 다이어그램 미리보기.

    BlockEditorWindow 와 같은 compute_layout()/draw_diagram() 을 그대로 재사용해서
    그림은 완전히 똑같이 그려지지만, owner=None 으로 그리기 때문에 클릭/드래그로
    편집은 안 된다(팔레트도 없음) - 편집은 여전히 "블록 편집" 버튼으로 여는
    BlockEditorWindow 팝업에서 한다.

    실행 중에는 main_app.py 가 step_engine.StepContext(on_active=...) 콜백을 통해
    set_active_by_id() 를 불러줘서, 지금 실행 중인 블록이 이 화면에 실시간으로
    하이라이트된다(BlockEditorWindow 의 set_active() 와 동일한 시각 효과)."""

    def __init__(self, parent, blocks: list[dict] | None = None):
        super().__init__(parent, bg=BG)
        self.blocks: list[dict] = blocks if blocks is not None else []
        self._active_index = None
        self._active_progress = None
        self._total_height = 0
        self._slots: list[dict] = []

        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0)
        vsb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 40), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        self.redraw()

    def update_blocks(self, blocks: list[dict]):
        """프로필 전환/새로 만들기/블록 편집 저장 등으로 시퀀스가 바뀌었을 때 호출."""
        self.blocks = blocks
        self.redraw()

    def redraw(self):
        layout = compute_layout(self.blocks)
        self._slots = layout["slots"]
        self._total_height = layout["total_height"]
        draw_diagram(self.canvas, self.blocks, layout, active_index=self._active_index,
                     active_progress=self._active_progress, owner=None)

    # ---- 실행 중 강조 표시 -------------------------------------------------
    def set_active(self, flat_index: int | None, loop_progress: dict | None = None):
        """BlockEditorWindow.set_active() 와 동일한 시그니처(인덱스 기준).
        flat_index: self.blocks 안에서의 인덱스, loop_progress: {loop_start_index:(cur,total)}."""
        self._active_index = flat_index
        self._active_progress = loop_progress
        self.redraw()
        if flat_index is not None:
            slot = next((s for s in self._slots if s["index"] == flat_index), None)
            if slot and self._total_height:
                frac = max(0.0, min(1.0, (slot["top"] - 60) / self._total_height))
                self.canvas.yview_moveto(frac)

    def set_active_by_id(self, block_id: str | None, loop_progress_by_id: dict | None = None):
        """step_engine 이 넘겨주는 건 화면의 flat index 가 아니라 블록 자신의 "id" 다
        (실행 엔진은 GUI 리스트 순서를 몰라도 되게 하려고). 여기서 현재 self.blocks
        순서에 맞춰 id -> index 로 변환해서 set_active() 를 호출한다."""
        flat_index = None
        if block_id is not None:
            flat_index = next((i for i, b in enumerate(self.blocks) if b.get("id") == block_id), None)
        progress = None
        if loop_progress_by_id:
            progress = {}
            for i, b in enumerate(self.blocks):
                bid = b.get("id")
                if bid in loop_progress_by_id:
                    progress[i] = loop_progress_by_id[bid]
        self.set_active(flat_index, progress)

    def clear_active(self):
        self.set_active(None, None)
