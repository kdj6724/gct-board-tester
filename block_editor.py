"""
드래그 앤 드롭 방식의 블록 시퀀스 에디터 (플로우차트 스타일).

- 왼쪽 팔레트에서 블록을 캔버스로 드래그하면 화살표로 이어지는 순서 중
  원하는 위치에 삽입된다. 클릭만 하면 맨 끝에 추가.
- 캔버스에 놓인 블록을 클릭하면 바로 그 블록의 세부 설정 창이 열린다.
- Loop 블록은 굵은 사각 테두리로 자신이 감싼 블록들을 표시한다. 테두리
  라벨(왼쪽 위)을 드래그하면 안의 블록들과 함께 통째로 이동한다. 다른
  블록을 Loop 테두리 안쪽(세로 범위)에 드래그해서 놓으면 그 안에 들어간다.
- Input 블록에 "응답 Check 붙이기" 를 설정하면 옆으로 작은 Check 박스가
  붙어서 "입력을 보내고 바로 그 응답을 확인한다" 는 하나의 짝으로 보인다.

캔버스 드로잉/좌표 계산과 순수 리스트 조작 로직(move_range, delete_block)은
분리되어 있어 tkinter 이벤트 없이도 단위 테스트할 수 있다.
"""
from __future__ import annotations
import copy
import tkinter as tk

import block_dialogs as bd
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
NEST_PAD_BASE, NEST_PAD_STEP = 26, 20
CENTER_X = 280
DRAG_THRESHOLD = 5
ACTIVE_COLOR = "#ffffff"


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
    """단일 블록 삭제. LOOP_START/END 면 그 안의 자식들도 함께 삭제한다."""
    b = blocks[index]
    if b["type"] == st.LOOP_START:
        end = st.find_matching_end(blocks, index)
        return blocks[:index] + blocks[end + 1:]
    if b["type"] == st.LOOP_END:
        start = index
        depth = 0
        for i in range(index, -1, -1):
            if blocks[i]["type"] == st.LOOP_END:
                depth += 1
            elif blocks[i]["type"] == st.LOOP_START:
                depth -= 1
                if depth == 0:
                    start = i
                    break
        return blocks[:start] + blocks[index + 1:]
    return blocks[:index] + blocks[index + 1:]


def summarize(block: dict) -> str:
    t, p = block["type"], block["params"]
    if t == st.POWER:
        return f"{p.get('state','on').upper()}  (+{p.get('delay_after',0):g}s)"
    if t == st.WAIT_STRING:
        s = p.get("pattern", "")
        return f"“{s[:22]}{'…' if len(s) > 22 else ''}”"
    if t == st.SEND:
        s = p.get("text", "") or "(Enter)"
        return f"{s[:22]!r}"
    if t == st.DELAY:
        return f"{p.get('seconds',0):g}s"
    if t == st.UPLOAD_SCRIPT:
        return p.get("target_path", "")
    return ""


def loop_label(block: dict, progress: tuple[int, int] | None = None) -> str:
    p = block["params"]
    label = p.get("label", "Loop")
    if progress:
        cur, total = progress
        return f"{label}  {cur}/{total if total else '∞'}"
    return f"{label} — {st.for_loop_repr(p)}"


def contains_loop_count(blocks: list[dict], start: int, end: int) -> int:
    return sum(1 for i in range(start + 1, end) if blocks[i]["type"] == st.LOOP_START)


def has_attach_child(blocks: list[dict], start: int, end: int) -> bool:
    return any(blocks[i]["type"] == st.SEND and blocks[i]["params"].get("check")
               for i in range(start + 1, end))


# ─────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────

class BlockEditorWindow(tk.Toplevel):
    def __init__(self, parent, blocks: list[dict], on_save):
        super().__init__(parent)
        self.title("테스트 시퀀스 편집 (블록 조립)")
        self.geometry("980x680")
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()

        self.blocks: list[dict] = copy.deepcopy(blocks)
        self.on_save = on_save
        self._slots: list[dict] = []
        self._loop_boxes: list[dict] = []
        self._drag = None       # {"start":i,"end":i,"moved":False,"press_y":..}
        self._pal_drag = None   # {"type":..., "ghost":Toplevel|None}
        self._insert_line = None
        self._active_index = None   # 실행 중 강조 표시할 블록의 flat index (Run 연결 후 사용)
        self._active_progress = None  # Loop 진행률 표시용 {loop_start_index: (cur, total)}

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

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        palette = tk.Frame(body, bg=PANEL, width=170)
        palette.pack(side="left", fill="y")
        tk.Label(palette, text="팔레트 (드래그 / 클릭)", font=("Consolas", 9),
                 bg=PANEL, fg=MUTE).pack(anchor="w", padx=10, pady=(10, 4))
        for t in st.PALETTE_TYPES:
            meta = st.BLOCK_META[st.LOOP_START if t == "LOOP" else t]
            btn = tk.Label(palette, text=meta["label"], font=("Consolas", 10, "bold"),
                           bg=meta["color"], fg=meta["text_color"], relief="flat",
                           padx=10, pady=8, cursor="hand2")
            btn.pack(fill="x", padx=10, pady=4)
            btn.bind("<ButtonPress-1>", lambda e, tt=t: self._palette_press(e, tt))
            btn.bind("<B1-Motion>", self._palette_motion)
            btn.bind("<ButtonRelease-1>", lambda e, tt=t: self._palette_release(e, tt))

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

    # ---- 레이아웃 계산 ---------------------------------------------------
    def _layout(self):
        blocks = self.blocks
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
            else:
                top = y
                y += NODE_H
                bottom = y
                y += V_GAP
                slots.append({"index": i, "kind": "action", "top": top, "bottom": bottom, "block": b})
            i += 1
        self._slots = slots
        self._total_height = y + 20

        loop_boxes = []
        for s in slots:
            if s["kind"] != "loop_start":
                continue
            start_i = s["index"]
            end_i = st.find_matching_end(blocks, start_i)
            end_slot = next(sl for sl in slots if sl["index"] == end_i)
            nest = contains_loop_count(blocks, start_i, end_i)
            pad = NEST_PAD_BASE + nest * NEST_PAD_STEP
            attach = has_attach_child(blocks, start_i, end_i)
            x0 = CENTER_X - NODE_W / 2 - pad
            x1 = CENTER_X + NODE_W / 2 + pad + (ATTACH_W + ATTACH_GAP if attach else 0)
            empty = (end_i == start_i + 1)
            loop_boxes.append({"start": start_i, "end": end_i, "top": s["border_top"],
                               "bottom": end_slot["border_bottom"], "x0": x0, "x1": x1,
                               "pad": pad, "empty": empty, "block": blocks[start_i]})
        loop_boxes.sort(key=lambda lb: -lb["pad"])
        self._loop_boxes = loop_boxes

    # ---- 렌더링 --------------------------------------------------------
    def redraw(self):
        self._layout()
        c = self.canvas
        c.delete("all")

        for lb in self._loop_boxes:
            color = st.BLOCK_META[st.LOOP_START]["color"]
            dash = (5, 3) if lb["empty"] else None
            c.create_rectangle(lb["x0"], lb["top"], lb["x1"], lb["bottom"],
                               outline=color, width=3, dash=dash, tags=(f"loopbox{lb['start']}",))
            progress = self._active_progress.get(lb["start"]) if self._active_progress else None
            label_text = loop_label(lb["block"], progress)
            lid = c.create_text(lb["x0"] + 6, lb["top"] - 14, anchor="w", text=label_text,
                                font=("Consolas", 10, "bold"), fill=color,
                                tags=(f"loophandle{lb['start']}",))
            did = c.create_text(lb["x1"] - 10, lb["top"] - 14, anchor="e", text="✕",
                                font=("Consolas", 11, "bold"), fill=color,
                                tags=(f"loopdel{lb['start']}",))
            if lb["empty"]:
                c.create_text((lb["x0"] + lb["x1"]) / 2, (lb["top"] + lb["bottom"]) / 2,
                              text="여기에 블록을 드래그하세요",
                              font=("Consolas", 9), fill=MUTE)
            c.tag_bind(f"loophandle{lb['start']}", "<ButtonPress-1>",
                      lambda e, s=lb["start"], en=lb["end"]: self._press(e, s, en))
            c.tag_bind(f"loophandle{lb['start']}", "<B1-Motion>", self._motion)
            c.tag_bind(f"loophandle{lb['start']}", "<ButtonRelease-1>", self._release)
            c.tag_bind(f"loopdel{lb['start']}", "<Button-1>",
                      lambda e, s=lb["start"]: self._delete(s))

        action_slots = [s for s in self._slots if s["kind"] == "action"]
        prev_bottom = None
        for s in action_slots:
            if prev_bottom is not None:
                c.create_line(CENTER_X, prev_bottom, CENTER_X, s["top"], fill=FG, width=2, arrow="last")
            prev_bottom = s["bottom"]

        for s in action_slots:
            i, b = s["index"], s["block"]
            meta = st.BLOCK_META[b["type"]]
            x0, y0, x1, y1 = CENTER_X - NODE_W / 2, s["top"], CENTER_X + NODE_W / 2, s["bottom"]
            is_active = (i == self._active_index)
            if is_active:
                c.create_rectangle(x0 - 5, y0 - 5, x1 + 5, y1 + 5, outline=ACTIVE_COLOR, width=3,
                                   tags=(f"node{i}", "activeglow"))
            c.create_rectangle(x0, y0, x1, y1, fill=meta["color"],
                               outline=(ACTIVE_COLOR if is_active else "#1e1e2e"),
                               width=4 if is_active else 2, tags=(f"node{i}",))
            summ = summarize(b)
            if summ:
                c.create_text(x0 + 10, (y0 + y1) / 2 - 9, anchor="w", text=meta["label"],
                              font=("Consolas", 11, "bold"), fill=meta["text_color"], tags=(f"node{i}",))
                c.create_text(x0 + 10, (y0 + y1) / 2 + 10, anchor="w", text=summ,
                              font=("Consolas", 9), fill=meta["text_color"], tags=(f"node{i}",))
            else:
                c.create_text(x0 + 10, (y0 + y1) / 2, anchor="w", text=meta["label"],
                              font=("Consolas", 11, "bold"), fill=meta["text_color"], tags=(f"node{i}",))
            c.create_text(x1 - 10, y0 + 12, anchor="e", text="✕", font=("Consolas", 10, "bold"),
                         fill=meta["text_color"], tags=(f"del{i}",))
            c.tag_bind(f"node{i}", "<ButtonPress-1>", lambda e, ii=i: self._press(e, ii, ii))
            c.tag_bind(f"node{i}", "<B1-Motion>", self._motion)
            c.tag_bind(f"node{i}", "<ButtonRelease-1>", self._release)
            c.tag_bind(f"del{i}", "<Button-1>", lambda e, ii=i: self._delete(ii))

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
        c.configure(scrollregion=(0, 0, width, max(self._total_height, c.winfo_height())))

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
            meta = st.BLOCK_META[st.LOOP_START if pd["type"] == "LOOP" else pd["type"]]
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-alpha", 0.85)
            tk.Label(ghost, text=meta["label"], font=("Consolas", 10, "bold"),
                     bg=meta["color"], fg=meta["text_color"], padx=10, pady=6).pack()
            pd["ghost"] = ghost
        if pd["moved"] and pd["ghost"] is not None:
            pd["ghost"].geometry(f"+{event.x_root+6}+{event.y_root+6}")
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
            idx = len(self.blocks)
        else:
            y = self._canvas_y_from_root(event.y_root)
            if y is None:
                return
            idx, _ = self._find_insertion(y)
        self._add_block(block_type, idx)

    def _canvas_y_from_root(self, y_root) -> float | None:
        c = self.canvas
        cy = y_root - c.winfo_rooty()
        if cy < 0 or cy > c.winfo_height():
            return None
        return c.canvasy(cy)

    def _add_block(self, block_type, idx):
        if block_type == "LOOP":
            start, end = st.make_loop_pair()
            new_params = bd.edit_loop(self, start["params"])
            if new_params is None:
                return
            start["params"] = new_params
            self.blocks = self.blocks[:idx] + [start, end] + self.blocks[idx:]
        else:
            block = st.make_block(block_type)
            new_params = bd.edit_block(self, block)
            if new_params is not None:
                block["params"] = new_params
            self.blocks = self.blocks[:idx] + [block] + self.blocks[idx:]
        self.redraw()

    # ---- 기존 블록/Loop 드래그 (순서 변경) --------------------------------
    def _press(self, event, start, end):
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
        if block["type"] in (st.LOOP_START, st.LOOP_END):
            return
        new_params = bd.edit_block(self, block)
        if new_params is not None:
            block["params"] = new_params
            self.redraw()

    def _delete(self, index):
        self.blocks = delete_block(self.blocks, index)
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
