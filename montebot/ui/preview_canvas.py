"""
Componente de Preview Interativo (Canvas Vetorial/Raster + Inspetor de Código G-code) em White Mode.
Inclui:
  - Representação da folha física A4 (210x297 mm) com réguas milimétricas
  - Progressão visual em tempo real no Canvas: trechos já percorridos pela caneta ficam em cinza claro/opaco
  - Progressão visual em tempo real no Código .gcode: linhas pendentes em azul escuro e linhas já executadas em cinza/opaco
  - Ponteiro da máquina ao vivo (Live Toolhead), controle de viagens (G0) e Zoom/Pan sem lag.
"""

import io
import re
import tkinter as tk
from typing import List, Optional, Tuple

from montebot.config import A4_HEIGHT_MM, A4_WIDTH_MM, FONTS, THEME, gradient_color
from montebot.ui.widgets import ModernButton

try:
    from PIL import Image, ImageTk
    from reportlab.graphics import renderPM
    from svglib.svglib import svg2rlg
    HAS_SVGLIB = True
except ImportError:
    HAS_SVGLIB = False


class PreviewCanvas(tk.Frame):
    """Painel direito completo com barra de ferramentas, legenda, canvas interativo e visualizador G-code."""

    def __init__(self, parent: tk.Widget, log_callback=None):
        super().__init__(parent, bg=THEME["bg_app"])
        self._log = log_callback or (lambda _msg: None)

        # Estados de dados
        self.svg_path: Optional[str] = None
        self.gcode_path: Optional[str] = None
        self.cut_paths: List[List[Tuple[float, float]]] = []
        self.travel_paths: List[List[Tuple[float, float]]] = []
        self.gcode_bounds: Tuple[float, float, float, float] = (0.0, 0.0, A4_WIDTH_MM, A4_HEIGHT_MM)

        # Cache de imagem do SVG
        self.svg_pil_base: Optional["Image.Image"] = None
        self.tk_img: Optional["ImageTk.PhotoImage"] = None

        # Modo atual: "none" | "pure_svg" | "gcode" | "code_view"
        self.preview_mode: str = "none"

        # Opções de visualização
        self.show_travel_var = tk.BooleanVar(value=True)
        self.show_paper_var = tk.BooleanVar(value=True)

        # Estado de navegação (Zoom & Pan)
        self.zoom_level: float = 1.0
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0
        self.drag_start_x: int = 0
        self.drag_start_y: int = 0

        # Transformação atual (mm <-> canvas px) para leitura de coordenadas do mouse
        self._transform_ox: float = 0.0
        self._transform_oy: float = 0.0
        self._transform_scale: float = 1.0
        self._ref_vx: float = 0.0
        self._ref_vy: float = 0.0
        self._ref_vh: float = A4_HEIGHT_MM

        # Posição do ponteiro da máquina em tempo real (GRBL Live Toolhead)
        self._toolhead_visible: bool = False
        self._toolhead_pos: Tuple[float, float, float] = (0.0, 0.0, 5.0)

        # Rastreamento de progresso de execução (Canvas + Código .gcode)
        self._num_progress_buckets: int = 300
        self._progress_ratio: float = 0.0
        self._rendered_done_bucket: int = 0
        self._code_total_lines: int = 0
        self._code_rendered_done_line: int = 0
        self._code_truncated: bool = False

        self._build_ui()
        self._bind_navigation_events()

    # ─────────────────────────────────────────────
    #  CONSTRUÇÃO DA INTERFACE DO PAINEL DIREITO
    # ─────────────────────────────────────────────

    def _build_ui(self):
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        # ── 1. Barra de Ferramentas Superior (Toolbar)
        toolbar = tk.Frame(
            self,
            bg=THEME["bg_header"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=14,
            pady=8,
        )
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)

        # Botões seletores de modo (Abas de visualização)
        tabs_frame = tk.Frame(toolbar, bg=THEME["bg_header"])
        tabs_frame.grid(row=0, column=0, sticky="w")

        self.btn_tab_svg = ModernButton(
            tabs_frame,
            text="📄 SVG Original",
            command=lambda: self.switch_mode("pure_svg"),
            variant="toolbar",
            font_key="toolbar_btn",
            padx=10,
            pady=5,
        )
        self.btn_tab_svg.pack(side="left", padx=(0, 6))
        self.btn_tab_svg.configure(state="disabled")

        self.btn_tab_gcode = ModernButton(
            tabs_frame,
            text="🎯 Rotas G-code",
            command=lambda: self.switch_mode("gcode"),
            variant="toolbar",
            font_key="toolbar_btn",
            padx=10,
            pady=5,
        )
        self.btn_tab_gcode.pack(side="left", padx=(0, 6))
        self.btn_tab_gcode.configure(state="disabled")

        self.btn_tab_code = ModernButton(
            tabs_frame,
            text="📝 Código .gcode",
            command=lambda: self.switch_mode("code_view"),
            variant="toolbar",
            font_key="toolbar_btn",
            padx=10,
            pady=5,
        )
        self.btn_tab_code.pack(side="left")
        self.btn_tab_code.configure(state="disabled")

        # Controles de filtro e Zoom à direita
        ctrl_frame = tk.Frame(toolbar, bg=THEME["bg_header"])
        ctrl_frame.grid(row=0, column=2, sticky="e")

        self.chk_travel = tk.Checkbutton(
            ctrl_frame,
            text="Mostrar Viagens (G0)",
            variable=self.show_travel_var,
            command=self._update_view,
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_header"],
            activebackground=THEME["bg_header"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        )
        self.chk_travel.pack(side="left", padx=(0, 10))

        self.chk_paper = tk.Checkbutton(
            ctrl_frame,
            text="Folha A4 (210×297 mm)",
            variable=self.show_paper_var,
            command=self._update_view,
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_header"],
            activebackground=THEME["bg_header"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        )
        self.chk_paper.pack(side="left", padx=(0, 14))

        # Controles de Zoom
        ModernButton(
            ctrl_frame,
            text=" − ",
            command=lambda: self._step_zoom(0.8),
            variant="secondary",
            font_key="toolbar_btn",
            padx=6,
            pady=3,
        ).pack(side="left", padx=2)

        self.lbl_zoom = tk.Label(
            ctrl_frame,
            text="100%",
            font=FONTS["mono_bold"],
            fg=THEME["text_primary"],
            bg=THEME["bg_header"],
            width=6,
        )
        self.lbl_zoom.pack(side="left", padx=2)

        ModernButton(
            ctrl_frame,
            text=" + ",
            command=lambda: self._step_zoom(1.25),
            variant="secondary",
            font_key="toolbar_btn",
            padx=6,
            pady=3,
        ).pack(side="left", padx=2)

        ModernButton(
            ctrl_frame,
            text="⛶ Ajustar",
            command=self.reset_and_fit,
            variant="secondary",
            font_key="toolbar_btn",
            padx=8,
            pady=3,
        ).pack(side="left", padx=(6, 0))

        # ── 2. Sub-barra de Legenda e Dicas de Navegação
        subbar = tk.Frame(
            self,
            bg=THEME["bg_card"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=14,
            pady=5,
        )
        subbar.grid(row=1, column=0, sticky="ew")
        subbar.columnconfigure(1, weight=1)

        self.lbl_preview_title = tk.Label(
            subbar,
            text="ÁREA DE VISUALIZAÇÃO",
            font=FONTS["section"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_card"],
        )
        self.lbl_preview_title.grid(row=0, column=0, sticky="w")

        self.lbl_nav_hint = tk.Label(
            subbar,
            text="Scroll: Zoom  •  Arrastar: Mover folha",
            font=FONTS["small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
        )
        self.lbl_nav_hint.grid(row=0, column=1, padx=12, sticky="w")

        # Legenda de cores (exibida no modo G-code)
        self.legend_frame = tk.Frame(subbar, bg=THEME["bg_card"])
        self.legend_frame.grid(row=0, column=2, sticky="e")

        # Indicador de linha já executada (Cinza claro/opaco)
        tk.Label(
            self.legend_frame,
            text="Executado:",
            font=FONTS["small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
        ).pack(side="left")
        dc = tk.Canvas(self.legend_frame, width=22, height=12, bg=THEME["bg_card"], highlightthickness=0)
        dc.pack(side="left", padx=(4, 10))
        dc.create_line(0, 6, 22, 6, fill=THEME["done_cut_color"], width=3, capstyle="round")

        # Indicador de viagem G0
        tk.Label(
            self.legend_frame,
            text="Viagem (G0):",
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_card"],
        ).pack(side="left")
        tc = tk.Canvas(self.legend_frame, width=22, height=12, bg=THEME["bg_card"], highlightthickness=0)
        tc.pack(side="left", padx=(4, 10))
        tc.create_line(0, 6, 22, 6, fill=THEME["travel_color"], dash=(3, 2), width=2)

        # Indicador de corte G1 pendente (gradiente)
        tk.Label(
            self.legend_frame,
            text="Corte (G1) Início",
            font=FONTS["small"],
            fg=THEME["start_marker"],
            bg=THEME["bg_card"],
        ).pack(side="left")

        grad_canvas = tk.Canvas(self.legend_frame, width=64, height=10, bg=THEME["bg_card"], highlightthickness=0)
        grad_canvas.pack(side="left", padx=5)
        for i in range(64):
            grad_canvas.create_line(i, 0, i, 10, fill=gradient_color(i / 63.0))

        tk.Label(
            self.legend_frame,
            text="Fim",
            font=FONTS["small"],
            fg=THEME["end_marker"],
            bg=THEME["bg_card"],
        ).pack(side="left")

        # ── 3. Área Central (Canvas Vetorial/Raster + Visualizador de Texto G-code)
        self.center_container = tk.Frame(self, bg=THEME["bg_canvas"])
        self.center_container.grid(row=2, column=0, sticky="nsew", padx=12, pady=(10, 6))
        self.center_container.rowconfigure(0, weight=1)
        self.center_container.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self.center_container,
            bg=THEME["bg_canvas"],
            highlightthickness=1,
            highlightbackground=THEME["border_strong"],
            cursor="fleur",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # Frame de inspeção do código .gcode bruto com destaque de execução
        self.code_frame = tk.Frame(
            self.center_container,
            bg=THEME["bg_sidebar"],
            highlightthickness=1,
            highlightbackground=THEME["border_strong"],
        )
        self.code_frame.rowconfigure(1, weight=1)
        self.code_frame.columnconfigure(0, weight=1)

        code_hdr = tk.Frame(self.code_frame, bg=THEME["bg_card"], padx=12, pady=6)
        code_hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.lbl_code_info = tk.Label(
            code_hdr,
            text="Inspeção do Arquivo G-code",
            font=FONTS["label_bold"],
            fg=THEME["text_primary"],
            bg=THEME["bg_card"],
        )
        self.lbl_code_info.pack(side="left")

        # Legenda rápida de cores do código G-code no cabeçalho do inspetor
        code_legend = tk.Frame(code_hdr, bg=THEME["bg_card"])
        code_legend.pack(side="left", padx=20)
        tk.Label(
            code_legend,
            text="● Pendente (Azul Escuro)",
            font=FONTS["small"],
            fg=THEME["code_pending_fg"],
            bg=THEME["bg_card"],
        ).pack(side="left", padx=(0, 12))
        tk.Label(
            code_legend,
            text="● Já Executado (Cinza/Opaco)",
            font=FONTS["small"],
            fg=THEME["code_done_fg"],
            bg=THEME["bg_card"],
        ).pack(side="left")

        ModernButton(
            code_hdr,
            text="📋 Copiar Código",
            command=self._copy_gcode_to_clipboard,
            variant="secondary",
            font_key="toolbar_btn",
            padx=10,
            pady=3,
        ).pack(side="right")

        ModernButton(
            code_hdr,
            text="🔄 Resetar Cores",
            command=self.reset_execution_progress,
            variant="secondary",
            font_key="toolbar_btn",
            padx=10,
            pady=3,
        ).pack(side="right", padx=(0, 6))

        self.code_text = tk.Text(
            self.code_frame,
            font=FONTS["input"],
            bg=THEME["bg_sidebar"],
            fg=THEME["code_pending_fg"],
            relief="flat",
            bd=0,
            padx=12,
            pady=8,
            wrap="none",
            state="disabled",
        )
        # Tags de estilização das linhas executadas vs pendentes no código G-code
        self.code_text.tag_configure("pending", foreground=THEME["code_pending_fg"])
        self.code_text.tag_configure("done", foreground=THEME["code_done_fg"])
        self.code_text.tag_configure(
            "current",
            foreground=THEME["code_current_fg"],
            background=THEME["code_current_bg"],
            font=FONTS["mono_bold"],
        )

        code_scroll_y = tk.Scrollbar(self.code_frame, orient="vertical", command=self.code_text.yview)
        code_scroll_x = tk.Scrollbar(self.code_frame, orient="horizontal", command=self.code_text.xview)
        self.code_text.configure(yscrollcommand=code_scroll_y.set, xscrollcommand=code_scroll_x.set)

        self.code_text.grid(row=1, column=0, sticky="nsew")
        code_scroll_y.grid(row=1, column=1, sticky="ns")
        code_scroll_x.grid(row=2, column=0, sticky="ew")

        # ── 4. Barra Inferior de Coordenadas e Status do Canvas
        footer = tk.Frame(self, bg=THEME["bg_app"], padx=14, pady=2)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.lbl_bounds_info = tk.Label(
            footer,
            text="Folha padrão: A4 (210.0 × 297.0 mm)  •  Origem (0,0) no canto inferior esquerdo",
            font=FONTS["mono_small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_app"],
        )
        self.lbl_bounds_info.grid(row=0, column=0, sticky="w")

        self.lbl_cursor_coords = tk.Label(
            footer,
            text="Cursor: X — mm | Y — mm",
            font=FONTS["mono_bold"],
            fg=THEME["text_secondary"],
            bg=THEME["bg_app"],
        )
        self.lbl_cursor_coords.grid(row=0, column=1, sticky="e")

        self._draw_placeholder("Selecione um arquivo SVG no painel lateral para iniciar.")

    # ─────────────────────────────────────────────
    #  NAVEGAÇÃO INTERATIVA (PAN 0ms & ZOOM)
    # ─────────────────────────────────────────────

    def _bind_navigation_events(self):
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._execute_pan)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", lambda _e: self.lbl_cursor_coords.configure(text="Cursor: X — mm | Y — mm"))

        self.canvas.bind("<MouseWheel>", self._on_zoom)
        self.canvas.bind("<Button-4>", self._on_zoom)
        self.canvas.bind("<Button-5>", self._on_zoom)

    def _start_pan(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def _execute_pan(self, event):
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        self.pan_x += dx
        self.pan_y += dy
        self._transform_ox += dx
        self._transform_oy += dy
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.canvas.move("all", dx, dy)
        self._on_mouse_move(event)

    def _on_mouse_move(self, event):
        if self.preview_mode not in ("gcode", "pure_svg") or self._transform_scale <= 0:
            return
        x_mm = self._ref_vx + (event.x - self._transform_ox) / self._transform_scale
        y_mm = self._ref_vy + (self._ref_vh - (event.y - self._transform_oy) / self._transform_scale)
        self.lbl_cursor_coords.configure(text=f"Cursor: X {x_mm:6.1f} mm | Y {y_mm:6.1f} mm")

    def _on_zoom(self, event):
        if self.preview_mode not in ("gcode", "pure_svg"):
            return
        if event.num == 4 or event.delta > 0:
            factor = 1.15
        elif event.num == 5 or event.delta < 0:
            factor = 0.85
        else:
            return

        new_zoom = self.zoom_level * factor
        if 0.15 <= new_zoom <= 35.0:
            cx, cy = event.x, event.y
            self.pan_x = cx - (cx - self.pan_x) * factor
            self.pan_y = cy - (cy - self.pan_y) * factor
            self.zoom_level = new_zoom
            self._update_zoom_label()
            self._update_view()

    def _step_zoom(self, factor: float):
        if self.preview_mode not in ("gcode", "pure_svg"):
            return
        new_zoom = max(0.15, min(35.0, self.zoom_level * factor))
        cw = self.canvas.winfo_width() or 600
        ch = self.canvas.winfo_height() or 400
        cx, cy = cw / 2.0, ch / 2.0
        actual_factor = new_zoom / self.zoom_level
        self.pan_x = cx - (cx - self.pan_x) * actual_factor
        self.pan_y = cy - (cy - self.pan_y) * actual_factor
        self.zoom_level = new_zoom
        self._update_zoom_label()
        self._update_view()

    def reset_and_fit(self):
        self.zoom_level = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._update_zoom_label()
        self._update_view()

    def _update_zoom_label(self):
        self.lbl_zoom.configure(text=f"{int(round(self.zoom_level * 100))}%")

    def _on_canvas_resize(self, _event):
        if self.preview_mode != "code_view":
            self._update_view()

    # ─────────────────────────────────────────────
    #  CARREGAMENTO DE DADOS E TROCA DE MODOS
    # ─────────────────────────────────────────────

    def load_svg_file(self, svg_path: str):
        """Carrega e cria o cache de imagem do arquivo SVG selecionado."""
        self.svg_path = svg_path
        self.svg_pil_base = None

        if HAS_SVGLIB:
            try:
                drawing = svg2rlg(svg_path)
                if drawing:
                    img_data = io.BytesIO()
                    renderPM.drawToFile(drawing, img_data, fmt="PNG", dpi=120)
                    img_data.seek(0)
                    self.svg_pil_base = Image.open(img_data).convert("RGBA")
            except Exception as exc:
                self._log(f"⚠ Aviso ao renderizar imagem do SVG: {exc}")

        self.btn_tab_svg.configure(state="normal")
        self.reset_and_fit()
        self.switch_mode("pure_svg")

    def load_gcode_result(
        self,
        gcode_path: str,
        cut_paths: List[List[Tuple[float, float]]],
        travel_paths: List[List[Tuple[float, float]]],
        bounds: Tuple[float, float, float, float],
    ):
        """Recebe os caminhos processados do G-code e atualiza o preview."""
        self.gcode_path = gcode_path
        self.cut_paths = cut_paths
        self.travel_paths = travel_paths
        self.gcode_bounds = bounds

        self._progress_ratio = 0.0
        self._rendered_done_bucket = 0
        self._code_rendered_done_line = 0

        self.btn_tab_gcode.configure(state="normal")
        self.btn_tab_code.configure(state="normal")

        vx, vy, vw, vh = bounds
        self.lbl_bounds_info.configure(
            text=f"Área desenhada: {vw:.1f} × {vh:.1f} mm  (X: {vx:.1f}..{vx+vw:.1f} mm | Y: {vy:.1f}..{vy+vh:.1f} mm)"
        )

        self._populate_code_viewer()
        self.reset_and_fit()
        self.switch_mode("gcode")

    def switch_mode(self, mode: str):
        """Alterna entre 'pure_svg', 'gcode' e 'code_view'."""
        self.preview_mode = mode

        if self.svg_path:
            self.btn_tab_svg.set_variant("toolbar_active" if mode == "pure_svg" else "toolbar")
        if self.gcode_path:
            self.btn_tab_gcode.set_variant("toolbar_active" if mode == "gcode" else "toolbar")
            self.btn_tab_code.set_variant("toolbar_active" if mode == "code_view" else "toolbar")

        if mode == "code_view":
            self.canvas.grid_remove()
            self.code_frame.grid(row=0, column=0, sticky="nsew")
            self.lbl_preview_title.configure(text="INSPEÇÃO DO CÓDIGO G-CODE (.GCODE)")
        else:
            self.code_frame.grid_remove()
            self.canvas.grid(row=0, column=0, sticky="nsew")
            if mode == "pure_svg":
                self.lbl_preview_title.configure(text="PREVIEW — ARQUIVO ORIGINAL (SVG)")
            elif mode == "gcode":
                self.lbl_preview_title.configure(text="PREVIEW — SIMULAÇÃO DE ROTAS G-CODE (CORTES + VIAGENS)")
            self._update_view()

    # ─────────────────────────────────────────────
    #  PROGRESSO VISUAL EM TEMPO REAL (CANVAS & CÓDIGO)
    # ─────────────────────────────────────────────

    def reset_execution_progress(self):
        """Restaura todas as linhas do Canvas e do Código .gcode para o estado pendente (não rodado)."""
        self._progress_ratio = 0.0
        self._rendered_done_bucket = 0
        self._code_rendered_done_line = 0

        # Restaura cores originais no Canvas
        nb = self._num_progress_buckets
        for b in range(nb):
            t = (b + 0.5) / max(1, nb)
            self.canvas.itemconfigure(f"cut_step_{b}", fill=gradient_color(t))
            self.canvas.itemconfigure(f"trav_step_{b}", fill=THEME["travel_color"])

        # Restaura todas as linhas do Código .gcode para azul escuro ("pending")
        self.code_text.tag_remove("done", "1.0", "end")
        self.code_text.tag_remove("current", "1.0", "end")
        self.code_text.tag_add("pending", "1.0", "end")

    def update_execution_progress(self, ack_lines: int, total_lines: int, is_streaming: bool):
        """
        Atualiza em tempo real (sem redesenhar o canvas do zero):
          1. As rotas já percorridas no Canvas para cinza claro/opaco (THEME['done_cut_color'])
          2. As linhas já executadas no Código .gcode para cinza/opaco (THEME['code_done_fg'])
             mantendo o que ainda não rodou em azul escuro (THEME['code_pending_fg']).
        """
        if total_lines <= 0:
            return

        new_ratio = max(0.0, min(1.0, float(ack_lines) / float(total_lines)))

        # Se iniciou um novo envio do zero após já ter progresso anterior, reseta primeiro
        if new_ratio < self._progress_ratio and ack_lines <= 5:
            self.reset_execution_progress()

        self._progress_ratio = new_ratio
        nb = self._num_progress_buckets
        target_bucket = min(nb, int(round(new_ratio * nb)))

        # 1. Atualiza incrementalmente os buckets do Canvas (0.05 ms, zero flicker)
        if target_bucket > self._rendered_done_bucket:
            for b in range(self._rendered_done_bucket, target_bucket):
                self.canvas.itemconfigure(f"cut_step_{b}", fill=THEME["done_cut_color"])
                self.canvas.itemconfigure(f"trav_step_{b}", fill=THEME["done_travel_color"])
            self._rendered_done_bucket = target_bucket

        # 2. Atualiza incrementalmente as cores das linhas no Inspetor de Código .gcode
        if self._code_total_lines > 0:
            if not self._code_truncated:
                done_line = min(self._code_total_lines, ack_lines)
            else:
                done_line = min(self._code_total_lines, int(round(new_ratio * self._code_total_lines)))

            if done_line > self._code_rendered_done_line:
                start_idx = f"{max(1, self._code_rendered_done_line)}.0"
                end_idx = f"{done_line + 1}.0"
                self.code_text.tag_remove("pending", start_idx, end_idx)
                self.code_text.tag_remove("current", "1.0", "end")
                self.code_text.tag_add("done", start_idx, end_idx)

                if is_streaming and done_line < self._code_total_lines:
                    cur_start = f"{done_line + 1}.0"
                    cur_end = f"{done_line + 2}.0"
                    self.code_text.tag_add("current", cur_start, cur_end)
                    if self.preview_mode == "code_view":
                        self.code_text.see(cur_start)

                self._code_rendered_done_line = done_line
            elif is_streaming and done_line == 0 and not self.code_text.tag_ranges("current"):
                cur_start = "1.0"
                cur_end = "2.0"
                self.code_text.tag_add("current", cur_start, cur_end)
                if self.preview_mode == "code_view":
                    self.code_text.see(cur_start)
            elif not is_streaming and self.code_text.tag_ranges("current"):
                self.code_text.tag_remove("current", "1.0", "end")

    def _populate_code_viewer(self):
        """Carrega as linhas executáveis do G-code no visualizador em azul escuro ('pending')."""
        if not self.gcode_path:
            return
        try:
            clean_lines: List[str] = []
            with open(self.gcode_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.split(";")[0].strip()
                    line = re.sub(r"\(.*?\)", "", line).strip()
                    if line:
                        clean_lines.append(line)

            total = len(clean_lines)
            max_display_lines = 40000
            self.code_text.configure(state="normal")
            self.code_text.delete("1.0", "end")

            if total <= max_display_lines:
                self._code_truncated = False
                self._code_total_lines = total
                numbered = [f"{idx+1:6d} │ {line}\n" for idx, line in enumerate(clean_lines)]
                self.code_text.insert("1.0", "".join(numbered))
                self.lbl_code_info.configure(text=f"Inspeção do Arquivo G-code ({total:,} linhas)")
            else:
                self._code_truncated = True
                head_n = 25000
                tail_n = 5000
                head = [f"{idx+1:6d} │ {line}\n" for idx, line in enumerate(clean_lines[:head_n])]
                tail_start = total - tail_n
                tail = [f"{tail_start+idx+1:6d} │ {line}\n" for idx, line in enumerate(clean_lines[tail_start:])]
                sep_line = f"   ... │ [{total - (head_n + tail_n):,} linhas intermediárias omitidas]\n"
                combined = head + [sep_line] + tail
                self._code_total_lines = len(combined)
                self.code_text.insert("1.0", "".join(combined))
                self.lbl_code_info.configure(
                    text=f"Inspeção do Arquivo G-code ({total:,} linhas totais)"
                )

            # Aplica o estilo inicial 'pending' (azul meio escuro) para todo o código que ainda não rodou
            self.code_text.tag_remove("done", "1.0", "end")
            self.code_text.tag_remove("current", "1.0", "end")
            self.code_text.tag_add("pending", "1.0", "end")
            self._code_rendered_done_line = 0

            self.code_text.configure(state="disabled")
        except Exception as exc:
            self._log(f"Erro ao carregar preview textual do G-code: {exc}")

    def _copy_gcode_to_clipboard(self):
        if not self.gcode_path:
            return
        try:
            with open(self.gcode_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log("✓ Conteúdo G-code copiado para a área de transferência.")
        except Exception as exc:
            self._log(f"Erro ao copiar G-code: {exc}")

    # ─────────────────────────────────────────────
    #  RENDERIZAÇÃO DO CANVAS (WHITE MODE)
    # ─────────────────────────────────────────────

    def _update_view(self):
        if self.preview_mode == "pure_svg":
            self._render_pure_svg()
        elif self.preview_mode == "gcode":
            self._render_gcode_preview()
        elif self.preview_mode == "none":
            self._draw_placeholder("Selecione um arquivo SVG no painel lateral para iniciar.")

    def _draw_placeholder(self, text: str):
        self.canvas.delete("all")
        cw = self.canvas.winfo_width() or 700
        ch = self.canvas.winfo_height() or 500

        for x in range(0, cw, 32):
            self.canvas.create_line(x, 0, x, ch, fill=THEME["desk_grid"], width=1)
        for y in range(0, ch, 32):
            self.canvas.create_line(0, y, cw, y, fill=THEME["desk_grid"], width=1)

        bw, bh = 420, 110
        bx1, by1 = (cw - bw) // 2, (ch - bh) // 2
        bx2, by2 = bx1 + bw, by1 + bh
        self.canvas.create_rectangle(
            bx1 + 3, by1 + 3, bx2 + 3, by2 + 3,
            fill=THEME["paper_shadow"], outline=""
        )
        self.canvas.create_rectangle(
            bx1, by1, bx2, by2,
            fill=THEME["bg_paper"], outline=THEME["border_strong"], width=1
        )
        self.canvas.create_text(
            cw // 2,
            ch // 2,
            text=text,
            font=FONTS["label_bold"],
            fill=THEME["text_secondary"],
            justify="center",
            width=bw - 40,
        )

    def _draw_desk_and_paper(
        self,
        cw: int,
        ch: int,
        ref_vx: float,
        ref_vy: float,
        ref_vw: float,
        ref_vh: float,
    ) -> Tuple[float, float, float]:
        """
        Desenha a mesa técnica e a folha A4 (ou área do desenho) em branco puro com grade milimétrica.
        Retorna (ox, oy, scale) para mapeamento de coordenadas em milímetros para pixels do canvas.
        """
        pad = 48.0
        base_scale = min((cw - pad * 2.0) / max(1.0, ref_vw), (ch - pad * 2.0) / max(1.0, ref_vh))
        scale = base_scale * self.zoom_level

        ox = pad + (cw - pad * 2.0 - ref_vw * scale) / 2.0 + self.pan_x
        oy = pad + (ch - pad * 2.0 - ref_vh * scale) / 2.0 + self.pan_y

        self._transform_ox = ox
        self._transform_oy = oy
        self._transform_scale = scale
        self._ref_vx = ref_vx
        self._ref_vy = ref_vy
        self._ref_vh = ref_vh

        def mm_to_canvas(x_mm: float, y_mm: float) -> Tuple[float, float]:
            cx = ox + (x_mm - ref_vx) * scale
            cy = oy + (ref_vh - (y_mm - ref_vy)) * scale
            return cx, cy

        p_x1, p_y2 = mm_to_canvas(0.0, 0.0)
        p_x2, p_y1 = mm_to_canvas(A4_WIDTH_MM, A4_HEIGHT_MM)

        if self.show_paper_var.get():
            self.canvas.create_rectangle(
                p_x1 + 5, p_y1 + 5, p_x2 + 5, p_y2 + 5,
                fill=THEME["paper_shadow"], outline=""
            )
            self.canvas.create_rectangle(
                p_x1, p_y1, p_x2, p_y2,
                fill=THEME["bg_paper"], outline=THEME["paper_border"], width=1
            )

            if 10.0 * scale >= 6.0:
                for xm in range(10, int(A4_WIDTH_MM), 10):
                    if xm % 50 != 0:
                        gx, _ = mm_to_canvas(xm, 0)
                        self.canvas.create_line(gx, p_y1, gx, p_y2, fill=THEME["grid_minor"], width=1)
                for ym in range(10, int(A4_HEIGHT_MM), 10):
                    if ym % 50 != 0:
                        _, gy = mm_to_canvas(0, ym)
                        self.canvas.create_line(p_x1, gy, p_x2, gy, fill=THEME["grid_minor"], width=1)

            for xm in range(0, int(A4_WIDTH_MM) + 1, 50):
                gx, _ = mm_to_canvas(xm, 0)
                if 0 < xm < A4_WIDTH_MM:
                    self.canvas.create_line(gx, p_y1, gx, p_y2, fill=THEME["grid_major"], width=1)
                self.canvas.create_text(
                    gx, p_y2 + 12, text=f"{xm}", font=("Consolas", 7), fill=THEME["text_muted"]
                )

            for ym in range(0, int(A4_HEIGHT_MM) + 1, 50):
                _, gy = mm_to_canvas(0, ym)
                if 0 < ym < A4_HEIGHT_MM:
                    self.canvas.create_line(p_x1, gy, p_x2, gy, fill=THEME["grid_major"], width=1)
                self.canvas.create_text(
                    p_x1 - 14, gy, text=f"{ym}", font=("Consolas", 7), fill=THEME["text_muted"], anchor="e"
                )

            ox0, oy0 = p_x1, p_y2
            self.canvas.create_line(ox0 - 8, oy0, ox0 + 8, oy0, fill=THEME["origin_marker"], width=2)
            self.canvas.create_line(ox0, oy0 - 8, ox0, oy0 + 8, fill=THEME["origin_marker"], width=2)
            self.canvas.create_text(
                ox0 + 6, oy0 - 10,
                text="(0,0)",
                font=("Consolas", 7, "bold"),
                fill=THEME["origin_marker"],
                anchor="w",
            )
        else:
            b_x1, b_y2 = mm_to_canvas(ref_vx, ref_vy)
            b_x2, b_y1 = mm_to_canvas(ref_vx + ref_vw, ref_vy + ref_vh)
            self.canvas.create_rectangle(
                b_x1, b_y1, b_x2, b_y2,
                fill=THEME["bg_paper"], outline=THEME["paper_border"], width=1
            )

        return ox, oy, scale

    def _render_pure_svg(self):
        self.canvas.delete("all")
        if not self.svg_pil_base:
            self._draw_placeholder("SVG carregado.\nClique em 'Gerar G-code' para processar e visualizar as rotas.")
            return

        cw = self.canvas.winfo_width() or 700
        ch = self.canvas.winfo_height() or 500

        ox, oy, scale = self._draw_desk_and_paper(
            cw, ch, 0.0, 0.0, A4_WIDTH_MM, A4_HEIGHT_MM
        )

        try:
            img = self.svg_pil_base
            target_w = int(round(A4_WIDTH_MM * scale))
            target_h = int(round(A4_HEIGHT_MM * scale))

            max_tex = 2400
            if max(target_w, target_h) > max_tex:
                ratio = max_tex / float(max(target_w, target_h))
                target_w = int(target_w * ratio)
                target_h = int(target_h * ratio)

            target_w = max(12, target_w)
            target_h = max(12, target_h)

            resample_filter = getattr(Image, "Resampling", Image).BILINEAR
            img_resized = img.resize((target_w, target_h), resample_filter)
            self.tk_img = ImageTk.PhotoImage(img_resized)

            center_x = ox + (A4_WIDTH_MM * scale) / 2.0
            center_y = oy + (A4_HEIGHT_MM * scale) / 2.0
            self.canvas.create_image(center_x, center_y, image=self.tk_img, anchor="center")
        except Exception as exc:
            self._log(f"Erro ao renderizar preview SVG: {exc}")
            self._draw_placeholder("Erro ao renderizar SVG. Gere o G-code para visualizar os vetores.")

    def _render_gcode_preview(self):
        self.canvas.delete("all")
        if not self.cut_paths and not self.travel_paths:
            self._draw_placeholder("Nenhuma rota encontrada no G-code gerado.")
            return

        cw = self.canvas.winfo_width() or 700
        ch = self.canvas.winfo_height() or 500

        vx, vy, vw, vh = self.gcode_bounds
        if self.show_paper_var.get():
            min_x = min(0.0, vx)
            min_y = min(0.0, vy)
            max_x = max(A4_WIDTH_MM, vx + vw)
            max_y = max(A4_HEIGHT_MM, vy + vh)
            ref_vx, ref_vy = min_x, min_y
            ref_vw, ref_vh = max(1.0, max_x - min_x), max(1.0, max_y - min_y)
        else:
            ref_vx, ref_vy, ref_vw, ref_vh = vx, vy, max(1.0, vw), max(1.0, vh)

        ox, oy, scale = self._draw_desk_and_paper(cw, ch, ref_vx, ref_vy, ref_vw, ref_vh)

        def to_canvas(x_mm: float, y_mm: float) -> Tuple[float, float]:
            cx = ox + (x_mm - ref_vx) * scale
            cy = oy + (ref_vh - (y_mm - ref_vy)) * scale
            return cx, cy

        num_buckets = self._num_progress_buckets
        done_bucket = min(num_buckets, int(round(self._progress_ratio * num_buckets)))
        self._rendered_done_bucket = done_bucket

        # 1. Desenha rotas de transição/viagem rápida (G0) com tag de progresso
        if self.show_travel_var.get() and self.travel_paths:
            n_trav = len(self.travel_paths)
            for t_idx, t_path in enumerate(self.travel_paths):
                b_trav = min(num_buckets - 1, int((t_idx / max(1, n_trav)) * num_buckets))
                t_color = THEME["done_travel_color"] if b_trav < done_bucket else THEME["travel_color"]
                x1, y1 = to_canvas(*t_path[0])
                x2, y2 = to_canvas(*t_path[1])
                self.canvas.create_line(
                    x1, y1, x2, y2,
                    fill=t_color,
                    dash=(3, 3),
                    width=1,
                    tags=("travel_path", f"trav_step_{b_trav}"),
                )

        # 2. Desenha rotas de corte/desenho (G1) em lotes cronológicos (cinza se já executado, gradiente se pendente)
        total_segs = sum(max(0, len(pl) - 1) for pl in self.cut_paths)
        line_w = max(1, min(3, int(round(1.2 * (self.zoom_level ** 0.35)))))

        seg_idx = 0

        for pl in self.cut_paths:
            if len(pl) < 2:
                continue
            pts_c = [to_canvas(px, py) for px, py in pl]
            n_seg = len(pts_c) - 1

            b_start = min(num_buckets - 1, int((seg_idx / max(1, total_segs)) * num_buckets))
            b_end = min(num_buckets - 1, int(((seg_idx + n_seg) / max(1, total_segs)) * num_buckets))

            if b_start == b_end:
                t = (b_start + 0.5) / max(1, num_buckets)
                c_fill = THEME["done_cut_color"] if b_start < done_bucket else gradient_color(t)
                flat_coords = [coord for pt in pts_c for coord in pt]
                self.canvas.create_line(
                    *flat_coords,
                    fill=c_fill,
                    width=line_w,
                    capstyle="round",
                    joinstyle="round",
                    tags=("cut_path", f"cut_step_{b_start}"),
                )
                seg_idx += n_seg
            else:
                chunk_start = 0
                cur_bucket = b_start
                for i in range(1, len(pts_c)):
                    b_i = min(num_buckets - 1, int(((seg_idx + i) / max(1, total_segs)) * num_buckets))
                    if b_i != cur_bucket or i == len(pts_c) - 1:
                        sub_pts = pts_c[chunk_start : i + 1]
                        t = (cur_bucket + 0.5) / max(1, num_buckets)
                        c_fill = THEME["done_cut_color"] if cur_bucket < done_bucket else gradient_color(t)
                        flat_coords = [coord for pt in sub_pts for coord in pt]
                        self.canvas.create_line(
                            *flat_coords,
                            fill=c_fill,
                            width=line_w,
                            capstyle="round",
                            joinstyle="round",
                            tags=("cut_path", f"cut_step_{cur_bucket}"),
                        )
                        chunk_start = i
                        cur_bucket = b_i
                seg_idx += n_seg

        # 3. Marcadores de Início e Fim
        if self.cut_paths and self.cut_paths[0]:
            sx, sy = to_canvas(*self.cut_paths[0][0])
            self.canvas.create_oval(
                sx - 5, sy - 5, sx + 5, sy + 5,
                fill=THEME["start_marker"], outline=THEME["bg_paper"], width=2
            )
            self.canvas.create_text(
                sx + 9, sy - 8,
                text="Início",
                font=FONTS["mono_bold"],
                fill=THEME["start_marker"],
                anchor="w",
            )

        if self.cut_paths and self.cut_paths[-1]:
            ex, ey = to_canvas(*self.cut_paths[-1][-1])
            self.canvas.create_oval(
                ex - 5, ey - 5, ex + 5, ey + 5,
                fill=THEME["end_marker"], outline=THEME["bg_paper"], width=2
            )
            self.canvas.create_text(
                ex + 9, ey + 8,
                text="Fim",
                font=FONTS["mono_bold"],
                fill=THEME["end_marker"],
                anchor="w",
            )

        self._draw_live_toolhead()

    def update_live_toolhead(self, visible: bool, x_mm: float = 0.0, y_mm: float = 0.0, z_mm: float = 5.0):
        """Atualiza a posição da ferramenta/caneta reportada pelo GRBL sem redesenhar as rotas."""
        self._toolhead_visible = visible
        self._toolhead_pos = (x_mm, y_mm, z_mm)
        if self.preview_mode in ("gcode", "pure_svg"):
            self._draw_live_toolhead()

    def _draw_live_toolhead(self):
        """Desenha apenas a camada 'live_toolhead' no Canvas (0.1 ms, zero flicker)."""
        self.canvas.delete("live_toolhead")
        if not self._toolhead_visible or self._transform_scale <= 0:
            return

        tx_mm, ty_mm, tz_mm = self._toolhead_pos
        cx = self._transform_ox + (tx_mm - self._ref_vx) * self._transform_scale
        cy = self._transform_oy + (self._ref_vh - (ty_mm - self._ref_vy)) * self._transform_scale

        pen_down = tz_mm <= 0.05
        color = THEME["toolhead_pen_down"] if pen_down else THEME["toolhead_pen_up"]

        r_out = 10
        r_in = 4
        self.canvas.create_oval(
            cx - r_out, cy - r_out, cx + r_out, cy + r_out,
            outline=color, width=2, tags="live_toolhead"
        )
        self.canvas.create_line(
            cx - 15, cy, cx + 15, cy,
            fill=color, width=1, tags="live_toolhead"
        )
        self.canvas.create_line(
            cx, cy - 15, cx, cy + 15,
            fill=color, width=1, tags="live_toolhead"
        )
        self.canvas.create_oval(
            cx - r_in, cy - r_in, cx + r_in, cy + r_in,
            fill=color if pen_down else THEME["bg_paper"],
            outline=color,
            width=2,
            tags="live_toolhead",
        )

        state_lbl = "PEN DOWN" if pen_down else f"Z:{tz_mm:.1f}"
        self.canvas.create_text(
            cx + 14,
            cy - 12,
            text=f"🔧 ({tx_mm:.1f}, {ty_mm:.1f}) [{state_lbl}]",
            font=("Consolas", 8, "bold"),
            fill=color,
            anchor="w",
            tags="live_toolhead",
        )
