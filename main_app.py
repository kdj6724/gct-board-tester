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
from block_editor import BlockEditorWindow, BlockDiagramView

LOG_DIR = os.path.join(cm.BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Board Test Automation")
        self.configure(bg="#1e1e2e")
        self.resizable(True, True)

        self._settings = cm.load_settings()
        self._settings.pop("tapo_ip", None)  # 옛 설정 잔재 정리 - Tapo IP는 이제 remote_power.env 에서만 읽음
        cm.migrate_legacy_if_needed()
        if not cm.list_profiles():
            cm.save_profile("default", cm.default_blocks())
        self._profile_name = self._settings.get("last_profile") or cm.list_profiles()[0]
        if self._profile_name not in cm.list_profiles():
            self._profile_name = cm.list_profiles()[0]
        self.blocks = cm.load_profile(self._profile_name) or cm.default_blocks()

        # 마지막으로 저장된 창 크기/위치로 띄운다(없으면 기본값). <Configure> 로
        # 리사이즈/이동을 감지해서 디바운스 저장하고, 창을 닫을 때도 한 번 더
        # 확실히 저장한다(_on_close) - 아래 _build_ui()/__init__ 끝부분 참고.
        self.geometry(self._settings.get("window_geometry") or "1000x760")
        self._geometry_after_id = None

        self._tapo = TapoController()
        self._running = False
        self._log_file = None
        self._log_path = None
        self._last_ser = None  # 직전 실행에서 열어둔 시리얼 커넥션 (완료 후에도 안 닫고 유지)

        self._build_ui()
        self.bind("<Configure>", self._on_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
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
        lbl(r1, "COM Port").grid(row=0, column=0, sticky="w", padx=(0, 6))
        e_port = ent(r1, self._v_port, 8); e_port.grid(row=0, column=1, padx=(0, 20))
        lbl(r1, "Baud").grid(row=0, column=2, sticky="w", padx=(0, 6))
        e_baud = ent(r1, self._v_baud, 10); e_baud.grid(row=0, column=3, padx=(0, 20))
        # Tapo IP 는 여기서 입력받지 않는다 - remote_power.env 에서만 읽는다
        # (settings.json 값과 .env 값이 어긋나 헷갈리던 문제가 있었음). 지금 어떤
        # 값이 쓰이는지만 참고용으로 표시.
        lbl(r1, "Tapo IP").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self._tapo_ip_lbl = tk.Label(r1, text=(cm.get_tapo_ip() or "(remote_power.env 없음)"),
                                     font=("Consolas", 11), bg=CARD, fg=FG)
        self._tapo_ip_lbl.grid(row=0, column=5, sticky="w")
        for e in (e_port, e_baud):
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

        r3 = tk.Frame(cfg, bg=CARD); r3.pack(fill="x", padx=14, pady=(0, 10))
        lbl(r3, "저장 폴더").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._v_save_dir = tk.StringVar(value=self._settings.get("save_dir", cm.DEFAULT_SAVE_DIR))
        e_save_dir = ent(r3, self._v_save_dir, 46)
        e_save_dir.grid(row=0, column=1, padx=(0, 8))
        e_save_dir.bind("<FocusOut>", lambda _: self._save_settings())
        e_save_dir.bind("<Return>", lambda _: self._save_settings())
        pbtn(r3, "찾아보기", self._browse_save_dir).grid(row=0, column=2)

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
        # 메인 화면은 이제 블록 다이어그램이 기본이고(실행 중인 블록 하이라이트),
        # 텍스트 로그는 이 버튼으로 토글해서 다이어그램 아래에 접었다 폈다 한다.
        self._btn_log_toggle = btn(br, "\U0001f4dc  로그 보기", self._toggle_log, "#313244")
        self._btn_log_toggle.pack(side="left", padx=(0, 8))
        self._log_lbl = tk.Label(br, text="", font=("Consolas", 9), bg=BG, fg=MUTE)
        self._log_lbl.pack(side="left")

        # 다이어그램(위, 항상 보임) / 로그(아래, 토글) 를 위아래로 나누는 PanedWindow.
        # 로그를 켜면 add(), 끄면 forget() 해서 접었다 폈다 한다 - 다이어그램은 항상
        # 그 자리에 그대로 있고, 켜져 있는 동안은 경계선(sash)을 드래그해서 비율도
        # 조절할 수 있다.
        self._body_pane = tk.PanedWindow(self, orient=tk.VERTICAL, bg=BG, bd=0,
                                         sashwidth=6, sashrelief="flat")
        self._body_pane.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self._diagram_view = BlockDiagramView(self._body_pane, self.blocks)
        self._body_pane.add(self._diagram_view, stretch="always", minsize=200)

        self._log_frame = tk.Frame(self._body_pane, bg=BG)
        self._txt = scrolledtext.ScrolledText(self._log_frame, font=("Consolas", 10), bg="#11111b",
                                              fg=FG, insertbackground=FG, relief="flat", bd=0,
                                              wrap="word", state="disabled")
        self._txt.pack(fill="both", expand=True)
        self._txt.tag_config("info", foreground=ACC)
        self._txt.tag_config("ok", foreground=GRN)
        self._txt.tag_config("err", foreground=RED)
        self._txt.tag_config("data", foreground=FG)
        self._txt.tag_config("mute", foreground=MUTE)
        self._log_visible = False

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

    def _toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self._body_pane.add(self._log_frame, minsize=120, height=220)
            self._btn_log_toggle.config(text="\U0001f4dc  로그 숨기기")
        else:
            self._body_pane.forget(self._log_frame)
            self._btn_log_toggle.config(text="\U0001f4dc  로그 보기")

    # ------------------------------------------------------------------
    def _auto_connect_tapo(self):
        env = cm.load_env()
        email, pwd, ip = env.get("TAPO_EMAIL", ""), env.get("TAPO_PASSWORD", ""), env.get("TAPO_IP", "")
        self._tapo_ip_lbl.config(text=(ip or "(remote_power.env 없음)"))
        if not email or not pwd or not ip:
            self._log("⚠ remote_power.env 파일에 TAPO_EMAIL / TAPO_PASSWORD / TAPO_IP 을 모두 설정하세요",
                      "err")
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
        self._settings.pop("tapo_ip", None)
        self._settings.update({
            "com_port": self._v_port.get(),
            "baud_rate": self._v_baud.get(),
            "save_dir": self._v_save_dir.get() or cm.DEFAULT_SAVE_DIR,
            "last_profile": self._profile_name,
        })
        cm.save_settings(self._settings)

    # ---- 창 크기/위치 저장 -------------------------------------------------
    def _on_configure(self, event):
        # 루트 창 자신의 리사이즈/이동일 때만 반응한다(자식 위젯들의 Configure 는
        # 각자 따로 바인딩되므로 여기로 안 올라오지만, 혹시 몰라 방어적으로 체크).
        if event.widget is not self:
            return
        if self._geometry_after_id is not None:
            self.after_cancel(self._geometry_after_id)
        # 드래그로 계속 리사이즈하는 동안은 매 프레임 저장하지 않고, 잠깐 멈췄을 때
        # (500ms) 한 번만 저장한다 - 안 그러면 드래그 중에 디스크에 계속 쓰게 됨.
        self._geometry_after_id = self.after(500, self._save_geometry)

    def _save_geometry(self):
        self._geometry_after_id = None
        self._settings["window_geometry"] = self.geometry()
        cm.save_settings(self._settings)

    def _on_close(self):
        # 디바운스(500ms)가 끝나기 전에 바로 닫아버리는 경우를 위해, 닫을 때
        # 한 번 더 확실히 저장한다.
        if self._geometry_after_id is not None:
            self.after_cancel(self._geometry_after_id)
            self._geometry_after_id = None
        self._save_geometry()
        self.destroy()

    def _browse_save_dir(self):
        path = filedialog.askdirectory(initialdir=self._v_save_dir.get() or cm.DEFAULT_SAVE_DIR,
                                       title="블록 저장 폴더 선택")
        if path:
            self._v_save_dir.set(path)
            self._save_settings()

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
        self._diagram_view.update_blocks(self.blocks)
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
            self._diagram_view.update_blocks(self.blocks)
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
        # 새로 시작하니 직전 실행에서 남아있던 하이라이트부터 지운다(다이어그램은
        # 프로필 변경/편집 때 이미 최신 self.blocks 로 갱신돼 있지만 한 번 더 보정).
        self._diagram_view.update_blocks(self.blocks)
        self._diagram_view.clear_active()
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
        # 직전 실행에서 닫지 않고 남겨둔 연결이 있으면, 새로 열기 전에 먼저 정리한다.
        # (완료 후 포트를 안 닫고 두는 대신, 다음 실행 시작할 때 여기서 닫아줘야
        # 같은 COM 포트를 다시 열 때 PermissionError 가 안 난다.)
        if self._last_ser is not None:
            try:
                self._last_ser.close()
            except Exception:  # noqa: BLE001
                pass
            self._last_ser = None
        port, baud = self._v_port.get(), int(self._v_baud.get())
        self._log_threadsafe(f"Opening {port} @ {baud}", "info")
        return ss.open_serial(serial, port, baud, timeout=1.0)

    def _on_active(self, block_id, loop_progress):
        # step_engine 은 worker 스레드에서 이걸 부른다 - Tk 는 메인 스레드에서만
        # 건드려야 하므로 다른 로그 콜백들과 마찬가지로 after(0, ...) 로 넘긴다.
        self.after(0, self._diagram_view.set_active_by_id, block_id, loop_progress)

    def _worker(self):
        ctx = se.StepContext(
            serial_factory=self._serial_factory,
            tapo=self._tapo,
            log=self._log_threadsafe,
            log_result=self._log_result,
            is_running=self._is_running,
            on_active=self._on_active,
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
            # 시리얼 연결도 전원과 마찬가지로 여기서 자동으로 안 닫는다 - 시퀀스가
            # 끝난 시점에 화면에 나온 상태를 그대로 유지하기 위함. 대신 다음 실행
            # 시작할 때(_serial_factory) 이 연결을 닫고 새로 연다.
            self._last_ser = ctx.ser
            # 전원은 더 이상 여기서 자동으로 끄지 않는다. Power OFF 블록을
            # 시퀀스에 직접 넣은 경우에만 꺼지도록 하고, 나머지는 디버깅을 위해
            # 켜진 상태로 남겨둔다.
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
