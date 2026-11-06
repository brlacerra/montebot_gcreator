"""
Painel de Controle CNC / GRBL em White Mode (Substituto integrado do UGS Platform).
Oferece conexão com portas COM, mostrador digital (DRO X/Y/Z), Jogging manual com seletor de passo,
zeramento de origem (Work Zero), calibração rápida de caneta, streaming de G-code com barra de
progresso e terminal MDI.
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from montebot.config import (
    FONTS,
    GRBL_BAUDRATES,
    GRBL_DEFAULT_BAUD,
    GRBL_STATE_STYLE,
    THEME,
)
from montebot.core.grbl_controller import GrblController, GrblStatus
from montebot.ui.widgets import ModernButton


class GrblControlPanel(tk.Frame):
    """Painel de controle completo da controladora GRBL."""

    def __init__(
        self,
        parent: tk.Widget,
        controller: GrblController,
        get_gcode_path_cb: Callable[[], Optional[str]],
        get_machine_params_cb: Callable[[], Dict[str, object]],
        on_load_external_gcode_cb: Callable[[], None],
    ):
        super().__init__(parent, bg=THEME["bg_sidebar"])
        self.controller = controller
        self._get_gcode_path = get_gcode_path_cb
        self._get_machine_params = get_machine_params_cb
        self._on_load_external_gcode = on_load_external_gcode_cb

        self._port_map: Dict[str, str] = {}
        self.step_var = tk.DoubleVar(value=5.0)
        self.jog_feed_var = tk.StringVar(value="1500")

        self._build_ui()
        self.refresh_ports()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── 1. CONEXÃO SERIAL (PORTA COM & BAUD RATE)
        conn_sec = tk.Frame(self, bg=THEME["bg_sidebar"], padx=14, pady=4)
        conn_sec.grid(row=0, column=0, sticky="ew")
        conn_sec.columnconfigure(0, weight=1)

        tk.Label(
            conn_sec,
            text="1. CONEXÃO SERIAL (PORTA COM)",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        port_row = tk.Frame(conn_sec, bg=THEME["bg_sidebar"])
        port_row.grid(row=1, column=0, sticky="ew")
        port_row.columnconfigure(0, weight=1)

        self.cmb_port = ttk.Combobox(port_row, state="readonly", font=FONTS["small"])
        self.cmb_port.grid(row=0, column=0, sticky="ew", padx=(0, 4), ipady=2)

        ModernButton(
            port_row,
            text="🔄",
            command=self.refresh_ports,
            variant="secondary",
            font_key="toolbar_btn",
            padx=8,
            pady=4,
        ).grid(row=0, column=1)

        baud_row = tk.Frame(conn_sec, bg=THEME["bg_sidebar"])
        baud_row.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        baud_row.columnconfigure(1, weight=1)

        tk.Label(
            baud_row,
            text="Baud:",
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, sticky="w")

        self.cmb_baud = ttk.Combobox(
            baud_row, values=GRBL_BAUDRATES, state="readonly", width=9, font=FONTS["mono_small"]
        )
        self.cmb_baud.set(GRBL_DEFAULT_BAUD)
        self.cmb_baud.grid(row=0, column=1, sticky="w", padx=(6, 8), ipady=2)

        self.btn_connect = ModernButton(
            baud_row,
            text="🔌 Conectar GRBL",
            command=self._toggle_connection,
            variant="primary",
            font_key="toolbar_btn",
            padx=12,
            pady=5,
        )
        self.btn_connect.grid(row=0, column=2, sticky="ew")

        self._make_divider(row=1)

        # ── 2. DRO (MOSTRADOR DIGITAL X, Y, Z) & ZERAMENTO
        dro_sec = tk.Frame(self, bg=THEME["bg_sidebar"], padx=14, pady=2)
        dro_sec.grid(row=2, column=0, sticky="ew")
        dro_sec.columnconfigure(0, weight=1)
        dro_sec.columnconfigure(1, weight=1)
        dro_sec.columnconfigure(2, weight=1)

        hdr_dro = tk.Frame(dro_sec, bg=THEME["bg_sidebar"])
        hdr_dro.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))

        tk.Label(
            hdr_dro,
            text="2. POSIÇÃO ATUAL (DRO)",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left")

        self.lbl_state_badge = tk.Label(
            hdr_dro,
            text="DESCONECTADO",
            font=("Consolas", 8, "bold"),
            bg=GRBL_STATE_STYLE["DISCONNECTED"][0],
            fg=GRBL_STATE_STYLE["DISCONNECTED"][1],
            padx=8,
            pady=1,
        )
        self.lbl_state_badge.pack(side="right")

        # Caixas X, Y, Z
        self.lbl_dro_x = self._create_axis_box(dro_sec, "EIXO X", col=0)
        self.lbl_dro_y = self._create_axis_box(dro_sec, "EIXO Y", col=1)
        self.lbl_dro_z = self._create_axis_box(dro_sec, "EIXO Z", col=2)

        # Botões de Zeramento e Origem
        zero_row = tk.Frame(dro_sec, bg=THEME["bg_sidebar"])
        zero_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        for i in range(4):
            zero_row.columnconfigure(i, weight=1)

        self._ctrl_buttons: List[ModernButton] = []

        btn_zxy = ModernButton(
            zero_row,
            text="📍 Zerar XY",
            command=lambda: self.controller.zero_axes(x=True, y=True, z=False),
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        btn_zxy.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        btn_zz = ModernButton(
            zero_row,
            text="📍 Zerar Z",
            command=lambda: self.controller.zero_axes(x=False, y=False, z=True),
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        btn_zz.grid(row=0, column=1, sticky="ew", padx=2)

        btn_go0 = ModernButton(
            zero_row,
            text="🎯 Ir X0 Y0",
            command=self._go_to_work_zero,
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        btn_go0.grid(row=0, column=2, sticky="ew", padx=2)

        btn_unlock = ModernButton(
            zero_row,
            text="🔓 $X",
            command=self.controller.unlock_alarm,
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        btn_unlock.grid(row=0, column=3, sticky="ew", padx=(2, 0))

        self._ctrl_buttons.extend([btn_zxy, btn_zz, btn_go0, btn_unlock])

        self._make_divider(row=3)

        # ── 3. JOGGING MANUAL (MOVIMENTAÇÃO DOS EIXOS & CANETA)
        jog_sec = tk.Frame(self, bg=THEME["bg_sidebar"], padx=14, pady=2)
        jog_sec.grid(row=4, column=0, sticky="ew")
        jog_sec.columnconfigure(0, weight=1)

        tk.Label(
            jog_sec,
            text="3. CONTROLE MANUAL (JOG) & CANETA",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        # Seletor de Passo (mm)
        step_row = tk.Frame(jog_sec, bg=THEME["bg_sidebar"])
        step_row.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        tk.Label(
            step_row,
            text="Passo (mm):",
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left", padx=(0, 4))

        for step_val in (0.1, 1.0, 5.0, 10.0, 50.0):
            lbl = str(int(step_val)) if step_val >= 1.0 else str(step_val)
            rb = tk.Radiobutton(
                step_row,
                text=lbl,
                variable=self.step_var,
                value=step_val,
                font=FONTS["mono_small"],
                fg=THEME["text_primary"],
                bg=THEME["bg_sidebar"],
                activebackground=THEME["bg_sidebar"],
                selectcolor=THEME["bg_input"],
                cursor="hand2",
            )
            rb.pack(side="left", padx=2)

        # D-Pad de Jogging (X, Y, Z) + Botões de Teste da Caneta
        pad_grid = tk.Frame(jog_sec, bg=THEME["bg_sidebar"])
        pad_grid.grid(row=2, column=0, sticky="ew")
        for c in range(5):
            pad_grid.columnconfigure(c, weight=1)

        b_yp = self._make_jog_btn(pad_grid, "▲ Y+", 0, 1, dx=0, dy=1, dz=0)
        b_xm = self._make_jog_btn(pad_grid, "◀ X−", 1, 0, dx=-1, dy=0, dz=0)
        b_hm = ModernButton(
            pad_grid,
            text="🏠 $H",
            command=self.controller.home_machine,
            variant="secondary",
            font_key="small",
            padx=4,
            pady=5,
        )
        b_hm.grid(row=1, column=1, sticky="nsew", padx=2, pady=2)
        b_xp = self._make_jog_btn(pad_grid, "X+ ▶", 1, 2, dx=1, dy=0, dz=0)
        b_ym = self._make_jog_btn(pad_grid, "▼ Y−", 2, 1, dx=0, dy=-1, dz=0)

        # Coluna Z+ / Z-
        b_zp = self._make_jog_btn(pad_grid, "▲ Z+", 0, 3, dx=0, dy=0, dz=1)
        b_zm = self._make_jog_btn(pad_grid, "▼ Z−", 2, 3, dx=0, dy=0, dz=-1)

        # Coluna de Teste Rápido de Caneta
        b_pup = ModernButton(
            pad_grid,
            text="🔼 Subir\nCaneta",
            command=self._test_pen_up,
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        b_pup.grid(row=0, column=4, rowspan=1, sticky="nsew", padx=(4, 0), pady=2)

        b_pdn = ModernButton(
            pad_grid,
            text="🔽 Testar\nPapel",
            command=self._test_pen_down,
            variant="secondary",
            font_key="small",
            padx=4,
            pady=4,
        )
        b_pdn.grid(row=1, column=4, rowspan=2, sticky="nsew", padx=(4, 0), pady=2)

        self._ctrl_buttons.extend([b_yp, b_xm, b_hm, b_xp, b_ym, b_zp, b_zm, b_pup, b_pdn])

        self._make_divider(row=5)

        # ── 4. EXECUÇÃO / STREAMING DO G-CODE
        stream_sec = tk.Frame(self, bg=THEME["bg_sidebar"], padx=14, pady=2)
        stream_sec.grid(row=6, column=0, sticky="ew")
        stream_sec.columnconfigure(0, weight=1)
        stream_sec.columnconfigure(1, weight=1)
        stream_sec.columnconfigure(2, weight=1)

        hdr_str = tk.Frame(stream_sec, bg=THEME["bg_sidebar"])
        hdr_str.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        tk.Label(
            hdr_str,
            text="4. ENVIO DO G-CODE PARA A PLACA",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left")

        btn_load_ext = tk.Label(
            hdr_str,
            text="📂 Abrir .gcode",
            font=FONTS["small"],
            fg=THEME["primary"],
            bg=THEME["bg_sidebar"],
            cursor="hand2",
        )
        btn_load_ext.pack(side="right")
        btn_load_ext.bind("<Button-1>", lambda _e: self._on_load_external_gcode())

        # Barra de progresso do envio
        self.progress_bar = ttk.Progressbar(stream_sec, orient="horizontal", mode="determinate", maximum=100.0)
        self.progress_bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 3))

        self.lbl_progress = tk.Label(
            stream_sec,
            text="Pronto para envio (0%)",
            font=FONTS["mono_small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_sidebar"],
            anchor="w",
        )
        self.lbl_progress.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 5))

        self.btn_play = ModernButton(
            stream_sec,
            text="▶ Iniciar",
            command=self._start_stream,
            variant="accent",
            font_key="toolbar_btn",
            padx=6,
            pady=7,
        )
        self.btn_play.grid(row=3, column=0, sticky="ew", padx=(0, 3))

        self.btn_pause = ModernButton(
            stream_sec,
            text="⏸ Pausar",
            command=self._toggle_pause,
            variant="secondary",
            font_key="toolbar_btn",
            padx=6,
            pady=7,
        )
        self.btn_pause.grid(row=3, column=1, sticky="ew", padx=3)

        self.btn_stop = ModernButton(
            stream_sec,
            text="🛑 Parar",
            command=self.controller.stop_stream,
            variant="secondary",
            font_key="toolbar_btn",
            padx=6,
            pady=7,
        )
        self.btn_stop.grid(row=3, column=2, sticky="ew", padx=(3, 0))

        self._make_divider(row=7)

        # ── 5. COMANDO MANUAL (MDI)
        mdi_sec = tk.Frame(self, bg=THEME["bg_sidebar"], padx=14, pady=2)
        mdi_sec.grid(row=8, column=0, sticky="ew")
        mdi_sec.columnconfigure(0, weight=1)

        tk.Label(
            mdi_sec,
            text="5. TERMINAL MDI (COMANDO DIRETO GRBL)",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))

        self.entry_mdi = tk.Entry(
            mdi_sec,
            font=FONTS["input"],
            bg=THEME["bg_input"],
            fg=THEME["text_primary"],
            insertbackground=THEME["primary"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=THEME["border_strong"],
            highlightcolor=THEME["border_focus"],
        )
        self.entry_mdi.grid(row=1, column=0, sticky="ew", ipady=4, ipadx=6, padx=(0, 4))
        self.entry_mdi.bind("<Return>", lambda _e: self._send_mdi_cmd())

        self.btn_mdi = ModernButton(
            mdi_sec,
            text="Enviar",
            command=self._send_mdi_cmd,
            variant="secondary",
            font_key="toolbar_btn",
            padx=10,
            pady=4,
        )
        self.btn_mdi.grid(row=1, column=1)

        self._set_controls_enabled(False)

    # ─────────────────────────────────────────────
    #  HELPERS DE CONSTRUÇÃO VISUAL
    # ─────────────────────────────────────────────

    def _create_axis_box(self, parent: tk.Widget, title: str, col: int) -> tk.Label:
        pad_x = (0, 3) if col == 0 else ((3, 3) if col == 1 else (3, 0))
        box = tk.Frame(
            parent,
            bg=THEME["bg_card"],
            highlightthickness=1,
            highlightbackground=THEME["border_strong"],
            padx=6,
            pady=4,
        )
        box.grid(row=1, column=col, sticky="ew", padx=pad_x)
        tk.Label(
            box,
            text=title,
            font=("Segoe UI", 7, "bold"),
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
        ).pack(anchor="w")
        lbl_val = tk.Label(
            box,
            text="0.00",
            font=("Consolas", 10, "bold"),
            fg=THEME["text_primary"],
            bg=THEME["bg_card"],
        )
        lbl_val.pack(anchor="e")
        return lbl_val

    def _make_jog_btn(self, parent: tk.Widget, text: str, r: int, c: int, dx: int, dy: int, dz: int) -> ModernButton:
        btn = ModernButton(
            parent,
            text=text,
            command=lambda: self._do_jog(dx, dy, dz),
            variant="secondary",
            font_key="toolbar_btn",
            padx=4,
            pady=5,
        )
        btn.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
        return btn

    def _make_divider(self, row: int):
        sep = tk.Frame(self, bg=THEME["border"], height=1)
        sep.grid(row=row, column=0, sticky="ew", padx=14, pady=5)

    # ─────────────────────────────────────────────
    #  LÓGICA DE PORTAS, CONEXÃO E CONTROLES
    # ─────────────────────────────────────────────

    def refresh_ports(self):
        ports: List[Tuple[str, str]] = self.controller.list_ports()
        self._port_map = {desc: dev for dev, desc in ports}
        labels = list(self._port_map.keys())
        self.cmb_port["values"] = labels
        if labels:
            self.cmb_port.set(labels[0])

    def _toggle_connection(self):
        if self.controller.is_connected:
            self.controller.disconnect()
            return

        selected_label = self.cmb_port.get()
        port_dev = self._port_map.get(selected_label, selected_label)
        if not port_dev:
            messagebox.showwarning("Porta COM", "Selecione uma porta COM válida.")
            return

        try:
            baud = int(self.cmb_baud.get() or 115200)
            self.controller.connect(port_dev, baudrate=baud)
        except Exception as exc:
            messagebox.showerror("Erro de Conexão Serial", f"Não foi possível abrir {port_dev}:\n{exc}")

    def _do_jog(self, dir_x: int, dir_y: int, dir_z: int):
        step = float(self.step_var.get() or 1.0)
        try:
            feed = float(self.jog_feed_var.get() or 1500.0)
        except ValueError:
            feed = 1500.0
        self.controller.jog(dx=dir_x * step, dy=dir_y * step, dz=dir_z * step, feed_rate=feed)

    def _go_to_work_zero(self):
        params = self._get_machine_params()
        try:
            lift = float(str(params.get("lift_height", 5.0)).replace(",", "."))
        except ValueError:
            lift = 5.0
        self.controller.go_to_zero(lift_height=lift)

    def _test_pen_up(self):
        params = self._get_machine_params()
        try:
            lift = float(str(params.get("lift_height", 5.0)).replace(",", "."))
        except ValueError:
            lift = 5.0
        self.controller.send_mdi(f"G90\nG0 Z{lift:.2f}")

    def _test_pen_down(self):
        params = self._get_machine_params()
        try:
            depth = float(str(params.get("cut_depth", -1.0)).replace(",", "."))
            plunge = float(str(params.get("plunge_rate", 600.0)).replace(",", "."))
        except ValueError:
            depth, plunge = -1.0, 600.0
        self.controller.send_mdi(f"G90\nG1 Z{depth:.2f} F{int(plunge)}")

    def _start_stream(self):
        gcode_path = self._get_gcode_path()
        if not gcode_path:
            messagebox.showwarning(
                "Nenhum G-code Carregado",
                "Gere o G-code a partir de um SVG na aba 'Gerador' ou clique em '📂 Abrir .gcode' primeiro.",
            )
            return
        try:
            self.controller.start_stream(gcode_path)
        except Exception as exc:
            messagebox.showerror("Erro ao iniciar envio", str(exc))

    def _toggle_pause(self):
        st = self.controller.get_status()
        if not st.streaming:
            return
        if st.paused:
            self.controller.resume_stream()
        else:
            self.controller.pause_stream()

    def _send_mdi_cmd(self):
        cmd = self.entry_mdi.get().strip()
        if not cmd:
            return
        self.controller.send_mdi(cmd)
        self.entry_mdi.delete(0, "end")

    def _set_controls_enabled(self, connected: bool, streaming: bool = False, paused: bool = False):
        manual_state = "normal" if (connected and (not streaming or paused)) else "disabled"
        for btn in self._ctrl_buttons:
            btn.configure(state=manual_state)

        self.btn_mdi.configure(state=manual_state)
        self.btn_play.configure(state="normal" if (connected and not streaming) else "disabled")
        self.btn_pause.configure(state="normal" if (connected and streaming) else "disabled")
        self.btn_stop.configure(state="normal" if connected else "disabled")

        if paused:
            self.btn_pause.configure(text="⏯ Retomar")
            self.btn_pause.set_variant("primary")
        else:
            self.btn_pause.configure(text="⏸ Pausar")
            self.btn_pause.set_variant("secondary")

    def update_from_status(self, status: GrblStatus):
        """Atualiza todos os indicadores visuais (DRO, estado, barra de progresso e botões)."""
        if status.connected:
            self.btn_connect.configure(text="❌ Desconectar")
            self.btn_connect.set_variant("secondary")
        else:
            self.btn_connect.configure(text="🔌 Conectar GRBL")
            self.btn_connect.set_variant("primary")

        bg_st, fg_st = GRBL_STATE_STYLE.get(status.state, GRBL_STATE_STYLE["DISCONNECTED"])
        self.lbl_state_badge.configure(text=status.state, bg=bg_st, fg=fg_st)

        wx, wy, wz = status.wpos
        self.lbl_dro_x.configure(text=f"{wx:6.2f}")
        self.lbl_dro_y.configure(text=f"{wy:6.2f}")
        self.lbl_dro_z.configure(text=f"{wz:6.2f}")

        self.progress_bar["value"] = status.progress_pct
        if status.streaming:
            eta_m, eta_s = divmod(int(round(status.eta_sec)), 60)
            eta_str = f"{eta_m}m {eta_s:02d}s" if eta_m > 0 else f"{eta_s}s"
            self.lbl_progress.configure(
                text=f"{status.progress_pct:5.1f}% • {status.ack_lines:,}/{status.total_lines:,} linhas • Restante: ~{eta_str}"
            )
        elif status.total_lines > 0 and status.ack_lines >= status.total_lines:
            self.lbl_progress.configure(
                text=f"100.0% • Concluído ({status.total_lines:,} linhas enviadas)"
            )
        else:
            self.lbl_progress.configure(text="Pronto para envio (0%)")

        self._set_controls_enabled(
            connected=status.connected,
            streaming=status.streaming,
            paused=status.paused,
        )

