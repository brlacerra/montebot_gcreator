"""
Janela principal do MonteBot GCreator em White Mode.
Integra em abas dedicadas na barra lateral:
  1. Gerador SVG -> G-code (parâmetros, otimização vpype, métricas)
  2. Controlador CNC / GRBL (conexão COM, DRO X/Y/Z, Jogging, Zeramento e Streaming)
Mantendo o PreviewCanvas interativo e o console de log sempre visíveis.
"""

import atexit
import os
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict, Optional

from montebot.config import (
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    APP_NAME,
    APP_TITLE,
    APP_VERSION,
    DEFAULT_PARAMS,
    FONTS,
    THEME,
)
from montebot.core.gcode_parser import GCodeStats, parse_gcode_file
from montebot.core.grbl_controller import GrblController, GrblStatus
from montebot.core.pipeline import run_full_pipeline
from montebot.ui.grbl_panel import GrblControlPanel
from montebot.ui.preview_canvas import HAS_SVGLIB, PreviewCanvas
from montebot.ui.widgets import LabeledEntry, MetricBox, ModernButton


class MonteBotApp(tk.Tk):
    """Aplicação principal MonteBot GCreator (CAD/CAM + Controlador GRBL)."""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=THEME["bg_app"])
        self.minsize(1060, 700)
        try:
            self.state("zoomed")
        except tk.TclError:
            self.geometry("1320x820")

        # Estado da sessão
        self.svg_path: Optional[str] = None
        self.temp_gcode_path: Optional[str] = None
        self.active_gcode_path: Optional[str] = None
        self._processing: bool = False
        self._active_sidebar_tab: str = "generator"

        # Variáveis de opções
        self.z_mode_var = tk.StringVar(value=str(DEFAULT_PARAMS.get("z_mode", "servo")))
        self.page_mode_var = tk.StringVar(value=DEFAULT_PARAMS["page_mode"])
        self.return_origin_var = tk.BooleanVar(value=bool(DEFAULT_PARAMS["return_origin"]))

        # Controlador Serial GRBL
        self.grbl = GrblController(
            on_status_change=self._on_grbl_status_threadsafe,
            on_log=self._ui_log,
            on_stream_complete=self._on_grbl_stream_complete_threadsafe,
        )

        atexit.register(self._cleanup_resources)
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        self._build_ui()
        self._check_optional_deps()

    # ─────────────────────────────────────────────
    #  CONSTRUÇÃO DA INTERFACE (WHITE MODE)
    # ─────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Painel Lateral Esquerdo (Sidebar Branca com Scroll Vertical)
        sidebar_outer = tk.Frame(
            self,
            bg=THEME["bg_sidebar"],
            width=392,
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        sidebar_outer.grid(row=0, column=0, sticky="nsew")
        sidebar_outer.grid_propagate(False)
        sidebar_outer.columnconfigure(0, weight=1)
        sidebar_outer.rowconfigure(3, weight=1)

        # 1. Cabeçalho Institucional (Fixo no topo)
        hdr = tk.Frame(sidebar_outer, bg=THEME["bg_sidebar"], padx=16, pady=8)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")

        title_row = tk.Frame(hdr, bg=THEME["bg_sidebar"])
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text=APP_NAME,
            font=FONTS["title"],
            fg=THEME["primary"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left")

        tk.Label(
            hdr,
            text=APP_VERSION,
            font=FONTS["subtitle"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).pack(anchor="w")

        # 2. Seletor de Modo na Sidebar (Abas: Gerador G-code | Controle GRBL)
        mode_bar = tk.Frame(sidebar_outer, bg=THEME["bg_card"], padx=14, pady=6)
        mode_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        mode_bar.columnconfigure(0, weight=1)
        mode_bar.columnconfigure(1, weight=1)

        self.btn_side_gen = ModernButton(
            mode_bar,
            text="📐 1. Gerar G-code",
            command=lambda: self.switch_sidebar_tab("generator"),
            variant="toolbar_active",
            font_key="toolbar_btn",
            padx=8,
            pady=6,
        )
        self.btn_side_gen.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_side_grbl = ModernButton(
            mode_bar,
            text="🎮 2. Controle GRBL",
            command=lambda: self.switch_sidebar_tab("grbl"),
            variant="toolbar",
            font_key="toolbar_btn",
            padx=8,
            pady=6,
        )
        self.btn_side_grbl.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        sep_top = tk.Frame(sidebar_outer, bg=THEME["border"], height=1)
        sep_top.grid(row=2, column=0, columnspan=2, sticky="ew")

        # 3. Área Rolável da Sidebar (Canvas + Scrollbar)
        self.sidebar_canvas = tk.Canvas(
            sidebar_outer,
            bg=THEME["bg_sidebar"],
            bd=0,
            highlightthickness=0,
        )
        self.sidebar_vscroll = tk.Scrollbar(
            sidebar_outer,
            orient="vertical",
            command=self.sidebar_canvas.yview,
        )
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_vscroll.set)
        self.sidebar_canvas.grid(row=3, column=0, sticky="nsew")
        self.sidebar_vscroll.grid(row=3, column=1, sticky="ns")

        self.sidebar_inner = tk.Frame(self.sidebar_canvas, bg=THEME["bg_sidebar"])
        self.sidebar_inner.columnconfigure(0, weight=1)
        self._sidebar_win_id = self.sidebar_canvas.create_window(
            (0, 0), window=self.sidebar_inner, anchor="nw"
        )

        self.sidebar_inner.bind("<Configure>", self._on_sidebar_inner_configure)
        self.sidebar_canvas.bind("<Configure>", self._on_sidebar_canvas_configure)

        # Container das Abas Laterais dentro da área rolável
        self.tab_container = tk.Frame(self.sidebar_inner, bg=THEME["bg_sidebar"])
        self.tab_container.grid(row=0, column=0, sticky="nsew", pady=(4, 0))
        self.tab_container.columnconfigure(0, weight=1)

        self._build_generator_tab()
        self._build_grbl_tab()

        self._make_divider(self.sidebar_inner, row=1)

        # 4. Console de Log Compartilhado (com altura ampliada dentro da área rolável)
        log_hdr = tk.Frame(self.sidebar_inner, bg=THEME["bg_sidebar"], padx=16)
        log_hdr.grid(row=2, column=0, sticky="ew")
        tk.Label(
            log_hdr,
            text="LOG DO SISTEMA E SERIAL GRBL",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left")

        btn_clear_log = tk.Label(
            log_hdr,
            text="Limpar",
            font=FONTS["small"],
            fg=THEME["primary"],
            bg=THEME["bg_sidebar"],
            cursor="hand2",
        )
        btn_clear_log.pack(side="right")
        btn_clear_log.bind("<Button-1>", lambda _e: self._clear_log())

        btn_copy_log = tk.Label(
            log_hdr,
            text="Copiar  •  ",
            font=FONTS["small"],
            fg=THEME["primary"],
            bg=THEME["bg_sidebar"],
            cursor="hand2",
        )
        btn_copy_log.pack(side="right")
        btn_copy_log.bind("<Button-1>", lambda _e: self._copy_log())

        log_frame = tk.Frame(
            self.sidebar_inner,
            bg=THEME["bg_log"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        log_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(4, 14))

        self.log_text = tk.Text(
            log_frame,
            font=FONTS["log"],
            bg=THEME["bg_log"],
            fg=THEME["text_secondary"],
            relief="flat",
            bd=0,
            state="disabled",
            wrap="word",
            height=9,
        )
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        scrollbar.pack(side="right", fill="y")

        # Suporte a rolagem com a roda do mouse em qualquer ponto do menu lateral
        self._bind_sidebar_mousewheel(sidebar_outer)

        # ── Painel Direito (PreviewCanvas Interativo)
        self.preview_panel = PreviewCanvas(self, log_callback=self._log)
        self.preview_panel.grid(row=0, column=1, sticky="nsew")

    def _build_generator_tab(self):
        """Constrói o conteúdo da aba '1. Gerar G-code'."""
        self.gen_frame = tk.Frame(self.tab_container, bg=THEME["bg_sidebar"])
        self.gen_frame.grid(row=0, column=0, sticky="nsew")
        self.gen_frame.columnconfigure(0, weight=1)

        # Seleção de Arquivo SVG ou G-code
        file_sec = tk.Frame(self.gen_frame, bg=THEME["bg_sidebar"], padx=14, pady=4)
        file_sec.grid(row=0, column=0, sticky="ew")
        file_sec.columnconfigure(0, weight=1)
        file_sec.columnconfigure(1, weight=0)

        tk.Label(
            file_sec,
            text="1. ARQUIVO DE ENTRADA",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))

        self.btn_svg = ModernButton(
            file_sec,
            text="📂 Selecionar SVG",
            command=self._open_svg,
            variant="primary",
            pady=7,
        )
        self.btn_svg.grid(row=1, column=0, sticky="ew", padx=(0, 4))

        self.btn_open_gcode = ModernButton(
            file_sec,
            text="📄 Abrir .gcode",
            command=self._open_external_gcode,
            variant="secondary",
            font_key="toolbar_btn",
            padx=10,
            pady=7,
        )
        self.btn_open_gcode.grid(row=1, column=1, sticky="ew")

        file_card = tk.Frame(
            file_sec,
            bg=THEME["bg_card"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=8,
            pady=5,
        )
        file_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.lbl_file = tk.Label(
            file_card,
            text="Nenhum arquivo selecionado",
            font=FONTS["mono_small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
            wraplength=310,
            justify="left",
            anchor="w",
        )
        self.lbl_file.pack(fill="x")

        self._make_divider(self.gen_frame, row=1)

        # Parâmetros da Máquina e Otimização (Seletor de Eixo Z + Grid 2x3 compacto)
        param_sec = tk.Frame(self.gen_frame, bg=THEME["bg_sidebar"], padx=14, pady=2)
        param_sec.grid(row=2, column=0, sticky="ew")
        param_sec.columnconfigure(0, weight=1)
        param_sec.columnconfigure(1, weight=1)

        tk.Label(
            param_sec,
            text="2. PARÂMETROS DA MÁQUINA E OTIMIZAÇÃO",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Seletor de Plunge / Eixo Z (Motor Servo vs Motor de Passo)
        z_mode_bar = tk.Frame(
            param_sec,
            bg=THEME["bg_card"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=6,
            pady=4,
        )
        z_mode_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        tk.Label(
            z_mode_bar,
            text="Plunge Z:",
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_card"],
        ).pack(side="left", padx=(2, 6))

        tk.Radiobutton(
            z_mode_bar,
            text="Motor Servo (Z+)",
            variable=self.z_mode_var,
            value="servo",
            command=self._update_z_mode_fields,
            font=FONTS["small"],
            fg=THEME["text_primary"],
            bg=THEME["bg_card"],
            activebackground=THEME["bg_card"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))

        tk.Radiobutton(
            z_mode_bar,
            text="Motor de Passo",
            variable=self.z_mode_var,
            value="stepper",
            command=self._update_z_mode_fields,
            font=FONTS["small"],
            fg=THEME["text_primary"],
            bg=THEME["bg_card"],
            activebackground=THEME["bg_card"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).pack(side="left")

        self._param_entries: Dict[str, LabeledEntry] = {}

        # Campos fixos (sempre visíveis)
        fixed_fields = [
            ("Feed rate (mm/min)", "feed_rate", DEFAULT_PARAMS["feed_rate"], 2, 0),
            ("União / Merge (mm)", "tolerance", DEFAULT_PARAMS["tolerance"], 4, 0),
            ("Simplificação (mm)", "simplify_tol", DEFAULT_PARAMS["simplify_tol"], 4, 1),
        ]
        for label, key, default, r, c in fixed_fields:
            pad_x = (0, 4) if c == 0 else (4, 0)
            entry_widget = LabeledEntry(param_sec, label=label, default=default)
            entry_widget.grid(row=r, column=c, sticky="ew", padx=pad_x, pady=2)
            self._param_entries[key] = entry_widget

        # Campos específicos para Motor Servo (Z+ / M03 S...)
        self._servo_grid_specs = [
            ("Delay Servo G4 (s)", "servo_delay", DEFAULT_PARAMS["servo_delay"], 2, 1),
            ("Eixo UP (ex: 250)", "servo_up", DEFAULT_PARAMS["servo_up"], 3, 0),
            ("Eixo DOWN (ex: 0)", "servo_down", DEFAULT_PARAMS["servo_down"], 3, 1),
        ]
        for label, key, default, r, c in self._servo_grid_specs:
            pad_x = (0, 4) if c == 0 else (4, 0)
            entry_widget = LabeledEntry(param_sec, label=label, default=default)
            entry_widget.grid(row=r, column=c, sticky="ew", padx=pad_x, pady=2)
            self._param_entries[key] = entry_widget

        # Campos específicos para Motor de Passo (G0/G1 Z...)
        self._stepper_grid_specs = [
            ("Plunge rate (mm/min)", "plunge_rate", DEFAULT_PARAMS["plunge_rate"], 2, 1),
            ("Altura Lift Z (mm)", "lift_height", DEFAULT_PARAMS["lift_height"], 3, 0),
            ("Z de Desenho (mm)", "cut_depth", DEFAULT_PARAMS["cut_depth"], 3, 1),
        ]
        for label, key, default, r, c in self._stepper_grid_specs:
            pad_x = (0, 4) if c == 0 else (4, 0)
            entry_widget = LabeledEntry(param_sec, label=label, default=default)
            entry_widget.grid(row=r, column=c, sticky="ew", padx=pad_x, pady=2)
            self._param_entries[key] = entry_widget

        self._update_z_mode_fields()

        opts_frame = tk.Frame(param_sec, bg=THEME["bg_sidebar"])
        opts_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        radio_row = tk.Frame(opts_frame, bg=THEME["bg_sidebar"])
        radio_row.pack(fill="x")
        tk.Label(
            radio_row,
            text="Escala:",
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_sidebar"],
        ).pack(side="left", padx=(0, 6))

        tk.Radiobutton(
            radio_row,
            text="Original (1:1)",
            variable=self.page_mode_var,
            value="original",
            font=FONTS["small"],
            fg=THEME["text_primary"],
            bg=THEME["bg_sidebar"],
            activebackground=THEME["bg_sidebar"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))

        tk.Radiobutton(
            radio_row,
            text="Ajustar A4",
            variable=self.page_mode_var,
            value="a4_fit",
            font=FONTS["small"],
            fg=THEME["text_primary"],
            bg=THEME["bg_sidebar"],
            activebackground=THEME["bg_sidebar"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).pack(side="left")

        tk.Checkbutton(
            opts_frame,
            text="Retornar à origem (X0 Y0) ao finalizar",
            variable=self.return_origin_var,
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_sidebar"],
            activebackground=THEME["bg_sidebar"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).pack(anchor="w", pady=(1, 0))

        self._make_divider(self.gen_frame, row=3)

        # Botões de Ação (Gerar, Salvar e Enviar para GRBL)
        act_sec = tk.Frame(self.gen_frame, bg=THEME["bg_sidebar"], padx=14, pady=2)
        act_sec.grid(row=4, column=0, sticky="ew")
        act_sec.columnconfigure(0, weight=1)
        act_sec.columnconfigure(1, weight=1)

        self.btn_gen = ModernButton(
            act_sec,
            text="⚙ Gerar G-code",
            command=self._generate,
            variant="primary",
            pady=7,
        )
        self.btn_gen.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_gen.configure(state="disabled")

        self.btn_save = ModernButton(
            act_sec,
            text="💾 Salvar .gcode",
            command=self._save_gcode,
            variant="accent",
            pady=7,
        )
        self.btn_save.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.btn_save.configure(state="disabled")

        self.lbl_status = tk.Label(
            act_sec,
            text="Selecione um SVG ou abra um .gcode existente.",
            font=FONTS["small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
            wraplength=330,
            justify="left",
            anchor="w",
        )
        self.lbl_status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        self._make_divider(self.gen_frame, row=5)

        # Card de Métricas do G-code
        stats_sec = tk.Frame(self.gen_frame, bg=THEME["bg_sidebar"], padx=14, pady=2)
        stats_sec.grid(row=6, column=0, sticky="ew")
        stats_sec.columnconfigure(0, weight=1)
        stats_sec.columnconfigure(1, weight=1)

        tk.Label(
            stats_sec,
            text="3. RESUMO E MÉTRICAS DO G-CODE",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.metric_time = MetricBox(stats_sec, "TEMPO ESTIMADO", "—")
        self.metric_time.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(0, 4))

        self.metric_routes = MetricBox(stats_sec, "TRAÇOS / LINHAS", "—")
        self.metric_routes.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(0, 4))

        self.metric_cut = MetricBox(stats_sec, "PERCURSO CORTE (G1)", "—")
        self.metric_cut.grid(row=2, column=0, sticky="ew", padx=(0, 4))

        self.metric_travel = MetricBox(stats_sec, "VIAGEM RÁPIDA (G0)", "—")
        self.metric_travel.grid(row=2, column=1, sticky="ew", padx=(4, 0))

    def _build_grbl_tab(self):
        """Constrói o conteúdo da aba '2. Controle GRBL'."""
        self.grbl_panel = GrblControlPanel(
            self.tab_container,
            controller=self.grbl,
            get_gcode_path_cb=lambda: self.active_gcode_path,
            get_machine_params_cb=self._get_params,
            on_load_external_gcode_cb=self._open_external_gcode,
        )
        self.grbl_panel.grid(row=0, column=0, sticky="nsew")
        self.grbl_panel.grid_remove()

    def switch_sidebar_tab(self, tab_name: str):
        """Alterna entre a aba Gerador ('generator') e Controle GRBL ('grbl')."""
        self._active_sidebar_tab = tab_name
        if tab_name == "generator":
            self.grbl_panel.grid_remove()
            self.gen_frame.grid(row=0, column=0, sticky="nsew")
            self.btn_side_gen.set_variant("toolbar_active")
            self.btn_side_grbl.set_variant("toolbar")
        else:
            self.gen_frame.grid_remove()
            self.grbl_panel.grid(row=0, column=0, sticky="nsew")
            self.btn_side_gen.set_variant("toolbar")
            self.btn_side_grbl.set_variant("toolbar_active")

        self.sidebar_inner.update_idletasks()
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _on_sidebar_inner_configure(self, _event):
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _on_sidebar_canvas_configure(self, event):
        self.sidebar_canvas.itemconfigure(self._sidebar_win_id, width=event.width)

    def _bind_sidebar_mousewheel(self, root_widget: tk.Widget):
        """Permite rolar a barra lateral com o scroll do mouse quando o cursor estiver sobre ela."""
        self._sidebar_root = root_widget
        self.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_global_mousewheel, add="+")

    def _on_global_mousewheel(self, event):
        widget = event.widget
        if not widget:
            return
        # Verifica se o widget sob o cursor pertence ao painel lateral esquerdo
        cur = widget
        in_sidebar = False
        while cur is not None:
            if cur == self._sidebar_root:
                in_sidebar = True
                break
            cur = getattr(cur, "master", None)
        if not in_sidebar:
            return

        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            step = -1
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            step = 1
        else:
            return

        if widget == self.log_text:
            self.log_text.yview_scroll(step, "units")
        else:
            self.sidebar_canvas.yview_scroll(step, "units")

    def _make_divider(self, parent: tk.Widget, row: int):
        sep = tk.Frame(parent, bg=THEME["border"], height=1)
        sep.grid(row=row, column=0, sticky="ew", padx=14, pady=5)

    def _check_optional_deps(self):
        if not HAS_SVGLIB:
            self._log("⚠ 'svglib'/'pillow' não detectadas. O preview do SVG original ficará indisponível.")
        else:
            self._log("✓ Sistema MonteBot GCreator + Controlador GRBL prontos.")

    # ─────────────────────────────────────────────
    #  CALLBACKS THREAD-SAFE DO CONTROLADOR GRBL
    # ─────────────────────────────────────────────

    def _on_grbl_status_threadsafe(self, status: GrblStatus):
        try:
            self.after(0, self._apply_grbl_status, status)
        except Exception:
            pass

    def _apply_grbl_status(self, status: GrblStatus):
        self.grbl_panel.update_from_status(status)
        if status.connected:
            wx, wy, wz = status.wpos
            self.preview_panel.update_live_toolhead(True, wx, wy, wz)
        else:
            self.preview_panel.update_live_toolhead(False)

        # Atualiza em tempo real o progresso visual nas telas 'Rotas G-Code' e 'Código .gcode'
        if status.total_lines > 0:
            self.preview_panel.update_execution_progress(
                status.ack_lines, status.total_lines, status.streaming
            )

    def _on_grbl_stream_complete_threadsafe(self, success: bool, msg: str):
        try:
            self.after(0, self._on_grbl_stream_complete, success, msg)
        except Exception:
            pass

    def _on_grbl_stream_complete(self, success: bool, msg: str):
        if success:
            messagebox.showinfo("Envio GRBL Concluído", msg)
        else:
            self._log(f"ℹ {msg}")

    # ─────────────────────────────────────────────
    #  FLUXO DE TRABALHO (ABRIR SVG / GCODE, GERAR, SALVAR)
    # ─────────────────────────────────────────────

    def _open_svg(self):
        path = filedialog.askopenfilename(
            title="Selecionar arquivo SVG",
            filetypes=[("Arquivos SVG", "*.svg"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return

        self.svg_path = path
        short = os.path.basename(path)
        self.lbl_file.configure(text=f"SVG: {short}", fg=THEME["text_primary"])

        self.btn_gen.configure(state="normal", text="⚙ Gerar G-code")
        self.btn_gen.set_variant("primary")
        self.btn_save.configure(state="disabled")
        self._reset_metrics()

        self.lbl_status.configure(
            text="SVG carregado. Ajuste os parâmetros e clique em 'Gerar G-code'.",
            fg=THEME["primary"],
        )

        self._log(f"📂 SVG selecionado: {short}")
        self.preview_panel.load_svg_file(path)

    def _open_external_gcode(self):
        """Permite abrir um arquivo .gcode pronto diretamente para visualizar e enviar ao GRBL."""
        path = filedialog.askopenfilename(
            title="Abrir arquivo G-code existente",
            filetypes=[
                ("Arquivos G-code", "*.gcode *.nc *.cnc *.tap"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not path:
            return

        try:
            params = self._get_params()
            feed_val = float(str(params.get("feed_rate", 1000)).replace(",", "."))
            plunge_val = float(str(params.get("plunge_rate", 600)).replace(",", "."))
            cut_p, trav_p, bounds, stats = parse_gcode_file(
                path,
                default_feed_rate=feed_val,
                default_plunge_rate=plunge_val,
            )
            self.active_gcode_path = path
            short = os.path.basename(path)
            self.lbl_file.configure(text=f"G-code: {short}", fg=THEME["text_primary"])
            self.btn_save.configure(state="normal")
            self.btn_save.set_variant("accent")

            self._update_metrics_ui(stats)
            self.preview_panel.load_gcode_result(
                gcode_path=path,
                cut_paths=cut_p,
                travel_paths=trav_p,
                bounds=bounds,
            )
            self.lbl_status.configure(
                text=f"✓ G-code carregado ({stats.total_lines:,} linhas). Pronto para enviar ao GRBL.",
                fg=THEME["accent"],
            )
            self._log(f"📄 G-code externo carregado: {short} ({stats.total_lines:,} linhas)")
        except Exception as exc:
            messagebox.showerror("Erro ao ler G-code", str(exc))

    def _update_z_mode_fields(self):
        """Alterna dinamicamente os campos de parâmetro entre Motor Servo (Z+) e Motor de Passo."""
        is_servo = self.z_mode_var.get() == "servo"
        for _label, key, _default, r, c in self._servo_grid_specs:
            pad_x = (0, 4) if c == 0 else (4, 0)
            if is_servo:
                self._param_entries[key].grid(row=r, column=c, sticky="ew", padx=pad_x, pady=2)
            else:
                self._param_entries[key].grid_remove()

        for _label, key, _default, r, c in self._stepper_grid_specs:
            pad_x = (0, 4) if c == 0 else (4, 0)
            if not is_servo:
                self._param_entries[key].grid(row=r, column=c, sticky="ew", padx=pad_x, pady=2)
            else:
                self._param_entries[key].grid_remove()

    def _get_params(self) -> Dict[str, object]:
        params: Dict[str, object] = {k: e.get() for k, e in self._param_entries.items()}
        params["z_mode"] = self.z_mode_var.get()
        params["page_mode"] = self.page_mode_var.get()
        params["return_origin"] = self.return_origin_var.get()
        return params

    def _generate(self):
        if self._processing or not self.svg_path:
            return
        self._processing = True
        self.btn_gen.configure(state="disabled", text="⏳ Processando...")
        self.btn_save.configure(state="disabled")
        self.lbl_status.configure(
            text="Otimizando vetores e gerando G-code...",
            fg=THEME["text_secondary"],
        )

        params = self._get_params()
        worker = threading.Thread(target=self._do_generate, args=(params,), daemon=True)
        worker.start()

    def _do_generate(self, params: Dict[str, object]):
        fd, temp_gcode = tempfile.mkstemp(suffix=".gcode", prefix="montebot_")
        os.close(fd)
        try:
            run_full_pipeline(
                input_svg=self.svg_path,
                output_gcode=temp_gcode,
                params=params,
                log_callback=self._ui_log,
            )

            self._ui_log("▶ Calculando rotas e estatísticas de execução...")
            feed_val = float(str(params.get("feed_rate", 1000)).replace(",", "."))
            plunge_val = float(str(params.get("plunge_rate", 600)).replace(",", "."))
            cut_p, trav_p, bounds, stats = parse_gcode_file(
                temp_gcode,
                default_feed_rate=feed_val,
                default_plunge_rate=plunge_val,
            )

            self._cleanup_temp_files()
            self.temp_gcode_path = temp_gcode
            self.active_gcode_path = temp_gcode

            self.after(
                0,
                self._on_generate_done,
                True,
                "G-code pronto! Salve ou envie na aba 'Controle GRBL'.",
                (cut_p, trav_p, bounds, stats),
            )
        except Exception as exc:
            try:
                if os.path.exists(temp_gcode):
                    os.remove(temp_gcode)
            except OSError:
                pass
            self.after(0, self._on_generate_done, False, str(exc), None)

    def _on_generate_done(self, success: bool, msg: str, payload):
        self._processing = False
        self.btn_gen.configure(state="normal", text="⚙ Gerar Novamente")

        if success and payload is not None:
            cut_p, trav_p, bounds, stats = payload
            self.btn_gen.set_variant("secondary")
            self.btn_save.configure(state="normal")
            self.btn_save.set_variant("accent")

            self._update_metrics_ui(stats)
            self.preview_panel.load_gcode_result(
                gcode_path=self.active_gcode_path,
                cut_paths=cut_p,
                travel_paths=trav_p,
                bounds=bounds,
            )

            if stats.has_negative_coords:
                warn_msg = (
                    "⚠ Atenção: O desenho possui coordenadas negativas (X<0 ou Y<0). "
                    "Use 'Ajustar A4' para evitar colisão."
                )
                self.lbl_status.configure(text=warn_msg, fg=THEME["warning"])
                self._log(warn_msg)
            elif stats.max_x > A4_WIDTH_MM + 1.0 or stats.max_y > A4_HEIGHT_MM + 1.0:
                warn_msg = (
                    f"ℹ Desenho ({stats.max_x:.0f}×{stats.max_y:.0f} mm) excede o tamanho A4 "
                    f"({A4_WIDTH_MM:.0f}×{A4_HEIGHT_MM:.0f} mm)."
                )
                self.lbl_status.configure(text=warn_msg, fg=THEME["warning"])
                self._log(warn_msg)
            else:
                self.lbl_status.configure(text="✓ " + msg, fg=THEME["accent"])
                self._log("✓ Preview de rotas atualizado.")
        else:
            self.btn_gen.set_variant("primary")
            self.lbl_status.configure(text="✗ Falha na geração. Verifique o log.", fg=THEME["danger"])
            self._log("✗ Erro: " + msg)
            messagebox.showerror("Erro ao gerar G-code", msg)

    def _update_metrics_ui(self, stats: GCodeStats):
        self.metric_time.set_value(stats.format_time(), fg=THEME["primary"])
        self.metric_routes.set_value(f"{stats.pen_lifts:,} / {stats.total_lines:,}")
        if stats.cut_distance_mm >= 1000.0:
            cut_str = f"{stats.cut_distance_mm / 1000.0:.2f} m"
        else:
            cut_str = f"{stats.cut_distance_mm:.0f} mm"

        if stats.travel_distance_mm >= 1000.0:
            trav_str = f"{stats.travel_distance_mm / 1000.0:.2f} m"
        else:
            trav_str = f"{stats.travel_distance_mm:.0f} mm"

        self.metric_cut.set_value(cut_str, fg=THEME["accent"])
        self.metric_travel.set_value(trav_str, fg=THEME["warning"])

    def _reset_metrics(self):
        self.metric_time.set_value("—")
        self.metric_routes.set_value("—")
        self.metric_cut.set_value("—")
        self.metric_travel.set_value("—")

    def _save_gcode(self):
        if not self.active_gcode_path or not os.path.exists(self.active_gcode_path):
            messagebox.showwarning("Aviso", "Nenhum G-code na memória. Gere ou abra um arquivo primeiro.")
            return

        base_src = self.svg_path or self.active_gcode_path or "desenho"
        base_name = os.path.splitext(os.path.basename(base_src))[0]
        if not base_name.endswith("_otimizado"):
            default_name = f"{base_name}_otimizado.gcode"
        else:
            default_name = f"{base_name}.gcode"

        dest = filedialog.asksaveasfilename(
            title="Salvar arquivo G-code como",
            initialfile=default_name,
            defaultextension=".gcode",
            filetypes=[
                ("Arquivo G-code", "*.gcode *.nc *.cnc"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not dest:
            return

        try:
            shutil.copy2(self.active_gcode_path, dest)
            self.lbl_status.configure(text=f"✓ Salvo: {os.path.basename(dest)}", fg=THEME["accent"])
            self._log(f"💾 Arquivo salvo em: {dest}")
            messagebox.showinfo("Arquivo Salvo", f"G-code salvo com sucesso em:\n{dest}")
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", str(exc))

    # ─────────────────────────────────────────────
    #  UTILITÁRIOS DE LOG E LIMPEZA DE SESSÃO
    # ─────────────────────────────────────────────

    def _ui_log(self, msg: str):
        try:
            self.after(0, self._log, msg)
        except Exception:
            pass

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with open("montebot.log", "a", encoding="utf-8") as lf:
                lf.write(msg + "\n")
        except Exception:
            pass

    def _copy_log(self):
        content = self.log_text.get("1.0", "end").strip()
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log("✓ Log copiado para a área de transferência.")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _cleanup_temp_files(self):
        if self.temp_gcode_path and os.path.exists(self.temp_gcode_path):
            try:
                os.remove(self.temp_gcode_path)
            except OSError:
                pass
            self.temp_gcode_path = None

    def _cleanup_resources(self):
        try:
            if self.grbl.is_connected:
                self.grbl.disconnect()
        except Exception:
            pass
        self._cleanup_temp_files()

    def _on_close_window(self):
        self._cleanup_resources()
        self.destroy()
