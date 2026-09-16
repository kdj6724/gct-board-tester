import tkinter as tk
from tkinter import scrolledtext, filedialog, simpledialog, messagebox
import threading
import time
import datetime
import os

import serial

import config_manager as cm
import step_types as st
import step_engine as se
import serial_session as ss
from tapo_control import TapoController
from block_editor import BlockEditorWindow

LOG_DIR = os.path.join(cm.BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Board Test Automation")
        self.geometry("1000x760")
        self.configure(bg="#1e1e2e")
        self.resizable(True, True)

        self._settings = cm.load_settings()
        cm.migrate_legacy_if_needed()
        if not cm.list_profiles():
            cm.save_profile("default", cm.default_blocks())
        self._profile_name = self._settings.get("last_profile") or cm.list_profiles()[0]
        if self._profile_name not in cm.list_profiles():
            self._profile_name = cm.list_profiles()[0]
        self.blocks = cm.load_profile(self._profile_name) or cm.default_blocks()

        self._tapo = TapoController()
        self._running = False
        self._log_file = None
        self._log_path = None

        self._build_ui()
        self.after(400, self._auto_connect_tapo)

    # ------------------------------------------------------------------
    def _build_ui(self):
        BG = "#1e1e2e"; CARD = "#2a2a3e"; ACC = "#7c6af7"
        FG = "#cdd6f4"; MUTE = "#6c7086"; RED = "#f38ba8"; GRN = "#a6e3a1"
        self._c = dict(BG=BG, CARD=CARD, ACC=ACC, FG=FG, MUTE=MUTE, RED=RED, GRN=GRN)

        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(hdr, text="⬡  Board Test Automation", font=("Consolas", 18, "bold"),
                 bg=BG, fg=ACC).pack(side="left")
        self._status_lbl = tk.Label(hdr, text="● idle", font=("Consolas", 11), bg=BG, fg=MUTE)
        self._status_lbl.pack(side="right")

        cfg = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground="#44475a")
        cfg.pack(fill="x", padx=20, pady=10)

        def lbl(p, t):
            return tk.Label(p, text=t, font=("Consolas", 10), bg=CARD, fg=MUTE)

        def ent(p, v, w=16):
            return tk.Entry(p, textvariable=v, width=w, font=("Consolas", 11),
                            bg="#313244", fg=FG, insertbackground=FG, relief="flat", bd=4)

        r1 = tk.Frame(cfg, bg=CARD); r1.pack(fill="x", padx=14, pady=(10, 4))
        self._v_port = tk.StringVar(value=self._settings["com_port"])
        self._v_baud = tk.StringVar(value=self._settings["baud_rate"])
        self._v_tapo_ip = tk.StringVar(value=self._settings["tapo_ip"])
        lbl(r1, "COM Port").grid(row=0, column=0, sticky="w", padx=(0, 6))
        e_port = ent(r1, self._v_port, 8); e_port.grid(row=0, column=1, padx=(0, 20))
        lbl(r1, "Baud").grid(row=0, column=2, sticky="w", padx=(0, 6))
        e_baud = ent(r1, self._v_baud, 10); e_baud.grid(row=0, column=3, padx=(0, 20))
        lbl(r1, "Tapo IP").grid(row=0, column=4, sticky="w", padx=(0, 6))
        e_ip = ent(r1, self._v_tapo_ip, 14); e_ip.grid(row=0, column=5)
        for e in (e_port, e_baud, e_ip):
            e.bind("<FocusOut>", lambda _: self._save_settings())
            e.bind("<Return>", lambda _: self._save_settings())

        r2 = tk.Frame(cfg, bg=CARD); r2.pack(fill="x", padx=14, pady=(4, 10))
        lbl(r2, "Profile").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._v_profile = tk.StringVar(value=self._profile_name)
        self._profile_menu = tk.OptionMenu(r2, self._v_profile, *cm.list_profiles(),
                                           command=self._on_profile_selected)
        self._profile_menu.configure(font=("Consolas", 10), bg="#313244", fg=FG,
                                     relief="flat", highlightthickness=0)
        self._profile_menu.grid(row=0, column=1, padx=(0, 10))

        def pbtn(p, t, cmd):
            b = tk.Button(p, text=t, command=cmd, font=("Consolas", 9), bg="#313244", fg=FG,
                          relief="flat", bd=0, padx=8, pady=4, cursor="hand2")
            return b
        pbtn(r2, "새로 만들기", self._new_profile).grid(row=0, column=2, padx=3)
        pbtn(r2, "다른 이름으로 저장", self._save_as_profile).grid(row=0, column=3, padx=3)
        pbtn(r2, "삭제", self._delete_profile).grid(row=0, column=4, padx=3)

        br = tk.Frame(self, bg=BG); br.pack(fill="x", padx=20, pady=(0, 8))

        def btn(p, t, c, color=ACC):
            return tk.Button(p, text=t, command=c, font=("Consolas", 11, "bold"), bg=color,
                             fg="#1e1e2e", relief="flat", bd=0, padx=16, pady=6,
                             cursor="hand2", activebackground=color)

        self._btn_run = btn(br, "▶  Run", self._run)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop = btn(br, "■  Stop", self._stop, RED)
        self._btn_stop.pack(side="left", padx=(0, 8))
        self._btn_stop.config(state="disabled")
        btn(br, "\U0001f9e9  블록 편집", self._edit_blocks, "#313244").pack(side="left", padx=(0, 8))
        btn(br, "\U0001f4c1  Open Log", self._open_log, "#313244").pack(side="left", padx=(0, 8))
        self._log_lbl = tk.Label(br, text="", font=("Consolas", 9), bg=BG, fg=MUTE)
        self._log_lbl.pack(side="left")

        self._txt = scrolledtext.ScrolledText(self, font=("Consolas", 10), bg="#11111b", fg=FG,
                                              insertbackground=FG, relief="flat", bd=0,
                                              wrap="word", state="disabled")
        self._txt.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self._txt.tag_config("info", foreground=ACC)
        self._txt.tag_config("ok", foreground=GRN)
        self._txt.tag_config("err", foreground=RED)
        self._txt.tag_config("data", foreground=FG)
        self._txt.tag_config("mute", foreground=MUTE)

    # ------------------------------------------------------------------
    def _log(self, msg, tag="data"):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        self._txt.config(state="normal")
        self._txt.insert("end", line, tag)
        self._txt.see("end")
        self._txt.config(state="disabled")

    def _log_threadsafe(self, msg, tag="data"):
        self.after(0, self._log, msg, tag)

    def _log_result(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if self._log_file:
            self._log_file.write(f"[{ts}] {msg}\n")
            self._log_file.flush()

    def _set_status(self, text, color=None):
        self._status_lbl.config(text=f"● {text}", fg=color or self._c["MUTE"])

    # ------------------------------------------------------------------
    def _auto_connect_tapo(self):
        env = cm.load_env()
        email, pwd = env.get("TAPO_EMAIL", ""), env.get("TAPO_PASSWORD", "")
        ip = self._v_tapo_ip.get()
        if not email or not pwd:
            self._log("⚠ .env 파일에 TAPO_EMAIL, TAPO_PASSWORD 을 설정하세요", "err")
            return
        self._log(f"Tapo 연결 중... ({ip})", "info")

        def _do():
            ok, msg = self._tapo.connect(ip, email, pwd)
            tag = "ok" if ok else "err"
            self.after(0, self._log, f"Tapo: {msg}", tag)
            if ok:
                self.after(0, self._set_status, "ready", self._c["GRN"])
        threading.Thread(target=_do, daemon=True).start()

    def _save_settings(self):
        self._settings.update({
            "com_port": self._v_port.get(),
            "baud_rate": self._v_baud.get(),
            "tapo_ip": self._v_tapo_ip.get(),
            "last_profile": self._profile_name,
        })
        cm.save_settings(self._settings)

    # ---- 프로파일 관리 --------------------------------------------------
    def _refresh_profile_menu(self):
        menu = self._profile_menu["menu"]
        menu.delete(0, "end")
        for name in cm.list_profiles():
            menu.add_command(label=name, command=lambda n=name: self._on_profile_selected(n))

    def _on_profile_selected(self, name):
        self._profile_name = name
        self._v_profile.set(name)
        self.blocks = cm.load_profile(name)
        self._save_settings()
        self._log(f"프로필 로드: {name}", "info")

    def _new_profile(self):
        name = simpledialog.askstring("새 프로필", "프로필 이름:", parent=self)
        if not name:
            return
        self.blocks = cm.default_blocks()
        cm.save_profile(name, self.blocks)
        self._refresh_profile_menu()
        self._on_profile_selected(name)

    def _save_as_profile(self):
        name = simpledialog.askstring("다른 이름으로 저장", "새 프로필 이름:",
                                       initialvalue=self._profile_name, parent=self)
        if not name:
            return
        cm.save_profile(name, self.blocks)
        self._refresh_profile_menu()
        self._on_profile_selected(name)

    def _delete_profile(self):
        names = cm.list_profiles()
        if len(names) <= 1:
            messagebox.showinfo("안내", "최소 1개의 프로필은 남아있어야 합니다.")
            return
        if not messagebox.askyesno("삭제 확인", f"'{self._profile_name}' 프로필을 삭제합니까?"):
            return
        cm.delete_profile(self._profile_name)
        self._refresh_profile_menu()
        self._on_profile_selected(cm.list_profiles()[0])

    # ---- 블록 편집 -------------------------------------------------------
    def _edit_blocks(self):
        def on_save(new_blocks):
            self.blocks = new_blocks
            cm.save_profile(self._profile_name, self.blocks)
            self._log(f"저장되었습니다. 블록 수={len(self.blocks)}", "ok")
        BlockEditorWindow(self, self.blocks, on_save)

    # ---- 실행 -------------------------------------------------------------
    def _run(self):
        err = st.validate(self.blocks)
        if err:
            messagebox.showerror("현재 시퀀스 오류", err)
            return
        self._btn_run.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._running = True
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(LOG_DIR, f"{ts}_{self._profile_name}.log")
        self._log_file = open(self._log_path, "w", encoding="utf-8")
        self._log_lbl.config(text=self._log_path)
        self._log_result(f"=== TEST START === profile: {self._profile_name}")
        threading.Thread(target=self._worker, daemon=True).start()

    def _stop(self):
        self._running = False
        self._btn_stop.config(state="disabled")

    def _is_running(self):
        return self._running

    def _serial_factory(self):
        port, baud = self._v_port.get(), int(self._v_baud.get())
        self._log_threadsafe(f"Opening {port} @ {baud}", "info")
        return ss.open_serial(serial, port, baud, timeout=1.0)

    def _worker(self):
        ctx = se.StepContext(
            serial_factory=self._serial_factory,
            tapo=self._tapo,
            log=self._log_threadsafe,
            log_result=self._log_result,
            is_running=self._is_running,
        )
        try:
            self.after(0, self._set_status, "running...", self._c["ACC"])
            se.run(self.blocks, ctx)
            self.after(0, self._log, "=== 시퀀스 완료 ===", "ok")
        except se.StopRequested:
            self.after(0, self._log, "=== Stopped ===", "err")
        except Exception as e:  # noqa: BLE001
            self.after(0, self._log, f"ERROR: {e}", "err")
            self._log_result(f"=== ERROR: {e} ===")
        finally:
            if ctx.ser is not None:
                try:
                    ctx.ser.close()
                except Exception:  # noqa: BLE001
                    pass
            self.after(0, self._log, "Tapo: power OFF", "info")
            self._tapo.set_power(False)
            self._log_result("=== TEST STOP ===")
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self._running = False
            self.after(0, self._btn_run.config, {"state": "normal"})
            self.after(0, self._btn_stop.config, {"state": "disabled"})
            self.after(0, self._set_status, "idle")

    def _open_log(self):
        path = filedialog.askopenfilename(initialdir=os.path.abspath(LOG_DIR), title="Open Log File",
                                           filetypes=[("Log files", "*.log"), ("All files", "*.*")])
        if path and hasattr(os, "startfile"):
            os.startfile(path)


if __name__ == "__main__":
    app = App()
    app.mainloop()
