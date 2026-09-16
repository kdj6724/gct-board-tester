"""시각적 컨셉 확인용 프로토타입 (실제 에디터에는 아직 연결 안 함).
화살표로 잇는 플로우차트 + Loop 굵은 테두리(진행률 표시) + Input-Check 옆으로 붙이기."""
import tkinter as tk

BG = "#11111b"
COLORS = {
    "Power": "#f38ba8", "Check": "#89b4fa", "Input": "#a6e3a1",
    "Delay": "#6c7086", "Script": "#94e2d5", "Loop": "#cba6f7",
}
NODE_W, NODE_H = 240, 46
V_GAP = 46
CENTER_X = 420

root = tk.Tk()
root.title("preview")
c = tk.Canvas(root, width=840, height=560, bg=BG, highlightthickness=0)
c.pack()


def node(x, y, w, h, label, sub, color):
    r = c.create_rectangle(x, y, x + w, y + h, fill=color, outline="#1e1e2e", width=2)
    if sub:
        c.create_text(x + 10, y + h / 2 - 9, anchor="w", text=label, font=("Consolas", 11, "bold"), fill="#1e1e2e")
        c.create_text(x + 10, y + h / 2 + 10, anchor="w", text=sub[:26], font=("Consolas", 9), fill="#1e1e2e")
    else:
        c.create_text(x + 10, y + h / 2, anchor="w", text=label, font=("Consolas", 11, "bold"), fill="#1e1e2e")
    return x, y, w, h


def arrow(x1, y1, x2, y2):
    c.create_line(x1, y1, x2, y2, fill="#cdd6f4", width=2, arrow="last")


rows = []
y = 30
# Power ON
rows.append(node(CENTER_X - NODE_W / 2, y, NODE_W, NODE_H, "Power", "ON", COLORS["Power"]))
y += NODE_H + V_GAP

loop_top = y - 14
# Check (boot string) - standalone
rows.append(node(CENTER_X - NODE_W / 2, y, NODE_W, NODE_H, "Check", '"bcfg: ...load complete"', COLORS["Check"]))
check1_y = y
y += NODE_H + V_GAP

# Input + attached Check (side pair)
input_x = CENTER_X - NODE_W / 2
rows.append(node(input_x, y, NODE_W, NODE_H, "Input", "'kern rtl th 0x1F0'", COLORS["Input"]))
attach_x = input_x + NODE_W + 26
node(attach_x, y, 150, NODE_H, "Check", "'#'", COLORS["Check"])
c.create_line(input_x + NODE_W, y + NODE_H / 2, attach_x, y + NODE_H / 2, fill="#cdd6f4", width=2)
input_y = y
y += NODE_H + V_GAP

# Delay
rows.append(node(CENTER_X - NODE_W / 2, y, NODE_W, NODE_H, "Delay", "1.0s", COLORS["Delay"]))
delay_y = y
y += NODE_H + V_GAP
loop_bottom = y - V_GAP + NODE_H + 14

# draw arrows between consecutive main-column rows
main_ys = [check1_y, input_y, delay_y]
prev_bottom = 30 + NODE_H
for ny in main_ys:
    arrow(CENTER_X, prev_bottom, CENTER_X, ny)
    prev_bottom = ny + NODE_H

# Loop border box (encloses check1..delay)
pad_x = 40
box_x0 = CENTER_X - NODE_W / 2 - pad_x
box_x1 = attach_x + 150 + 14
c.create_rectangle(box_x0, loop_top, box_x1, loop_bottom, outline="#cba6f7", width=4)
c.create_text(box_x0 + 8, loop_top - 12, anchor="w", text="Loop  3/10",
              font=("Consolas", 11, "bold"), fill="#cba6f7")

# Power OFF after loop
y = loop_bottom + V_GAP
arrow(CENTER_X, loop_bottom, CENTER_X, y)
node(CENTER_X - NODE_W / 2, y, NODE_W, NODE_H, "Power", "OFF", COLORS["Power"])

root.update()
import subprocess
subprocess.run(["import", "-window", "root", "/tmp/flow_preview.png"])
print("saved")
