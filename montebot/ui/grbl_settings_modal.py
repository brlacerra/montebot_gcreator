"""
Modais de Configurações da Aplicação e Editor de Parâmetros GRBL ($$) em White Mode.
Inspirado no Firmware Settings do Universal Gcode Sender (UGS).
"""

import re
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Dict, Optional

from montebot.config import FONTS, GRBL_SETTINGS_METADATA, THEME
from montebot.core.grbl_controller import GrblController
from montebot.ui.widgets import ModernButton


class GrblSettingsModal(tk.Toplevel):
    """Modal completo estilo UGS para leitura, edição e gravação de parâmetros $ do GRBL."""

    def __init__(
        self,
        parent: tk.Misc,
        controller: GrblController,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.controller = controller
        self._log = on_log or (lambda _m: None)

        self.title("Configurações do Firmware GRBL ($$)")
        self.configure(bg=THEME["bg_app"])
        self.geometry("760x580")
        self.minsize(640, 440)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self._loaded_settings: Dict[int, str] = {}
        self._entries: Dict[int, tk.Entry] = {}
        self._vars: Dict[int, tk.StringVar] = {}
        self._refresh_job: Optional[str] = None
        self._save_pending_notice = False

        # Preserva eventual callback existente no controlador e restaura ao fechar
        self._prev_on_settings_change = self.controller.on_settings_change
        self.controller.on_settings_change = self._on_controller_settings_threadsafe

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._bind_mousewheel()
        self._initial_load()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Cabeçalho Superior
        hdr = tk.Frame(
            self,
            bg=THEME["bg_header"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=16,
            pady=12,
        )
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(0, weight=1)

        tk.Label(
            hdr,
            text="🔧 Editor de Parâmetros GRBL ($$)",
            font=FONTS["title"],
            fg=THEME["primary"],
            bg=THEME["bg_header"],
        ).grid(row=0, column=0, sticky="w")

        self.lbl_conn_info = tk.Label(
            hdr,
            text="",
            font=FONTS["small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_header"],
        )
        self.lbl_conn_info.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.btn_reload = ModernButton(
            hdr,
            text="🔄 Ler da Placa ($$)",
            command=self._reload_from_board,
            variant="secondary",
            font_key="toolbar_btn",
            padx=12,
            pady=6,
        )
        self.btn_reload.grid(row=0, column=1, rowspan=2, sticky="e")

        # ── Tabela Rolável de Parâmetros $
        table_wrap = tk.Frame(
            self,
            bg=THEME["bg_sidebar"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        table_wrap.grid(row=1, column=0, sticky="nsew", padx=14, pady=12)
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(1, weight=1)

        # Cabeçalho das colunas
        col_hdr = tk.Frame(table_wrap, bg=THEME["bg_card"], padx=10, pady=7)
        col_hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        col_hdr.columnconfigure(1, weight=1)

        tk.Label(
            col_hdr,
            text="CÓD.",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
            width=7,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            col_hdr,
            text="PARÂMETRO E DESCRIÇÃO",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(8, 8))

        tk.Label(
            col_hdr,
            text="VALOR",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
            width=14,
            anchor="w",
        ).grid(row=0, column=2, sticky="e", padx=(0, 8))

        self.canvas = tk.Canvas(
            table_wrap,
            bg=THEME["bg_sidebar"],
            bd=0,
            highlightthickness=0,
        )
        self.vscroll = tk.Scrollbar(table_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.vscroll.grid(row=1, column=1, sticky="ns")

        self.rows_frame = tk.Frame(self.canvas, bg=THEME["bg_sidebar"])
        self.rows_frame.columnconfigure(0, weight=1)
        self._canvas_win = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")

        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._canvas_win, width=e.width),
        )

        # ── Rodapé com Ações e Contador de Modificações
        footer = tk.Frame(
            self,
            bg=THEME["bg_header"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=16,
            pady=10,
        )
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.lbl_changes = tk.Label(
            footer,
            text="Nenhuma alteração pendente.",
            font=FONTS["small"],
            fg=THEME["text_muted"],
            bg=THEME["bg_header"],
            anchor="w",
        )
        self.lbl_changes.grid(row=0, column=0, sticky="w")

        btn_box = tk.Frame(footer, bg=THEME["bg_header"])
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_close = ModernButton(
            btn_box,
            text="Fechar",
            command=self._on_close,
            variant="secondary",
            font_key="toolbar_btn",
            padx=14,
            pady=6,
        )
        self.btn_close.pack(side="right", padx=(8, 0))

        self.btn_save = ModernButton(
            btn_box,
            text="💾 Salvar no Arduino",
            command=self._save_changes,
            variant="accent",
            font_key="toolbar_btn",
            padx=14,
            pady=6,
        )
        self.btn_save.pack(side="right")
        self.btn_save.configure(state="disabled")

    def _bind_mousewheel(self):
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind("<Button-4>", self._on_mousewheel, add="+")
        self.bind("<Button-5>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            step = -1
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            step = 1
        else:
            return
        self.canvas.yview_scroll(step, "units")

    def _initial_load(self):
        st = self.controller.get_status()
        if not st.connected:
            self.lbl_conn_info.configure(
                text="⚠ Desconectado — Conecte-se à placa na aba '2. Controle GRBL' para ler e gravar.",
                fg=THEME["warning"],
            )
            self.btn_reload.configure(state="disabled")
            self._show_empty_state(
                "Nenhuma placa GRBL conectada.\n\n"
                "Conecte-se a uma porta COM (ou ao Simulador GRBL) na aba '2. Controle GRBL'\n"
                "e clique em 'Ler da Placa ($$)'."
            )
            return

        if st.streaming and not st.paused:
            self.lbl_conn_info.configure(
                text=f"🔌 Porta {st.port} — Envio em andamento. Pause o trabalho antes de editar o GRBL.",
                fg=THEME["warning"],
            )
            self.btn_reload.configure(state="disabled")
        else:
            self.lbl_conn_info.configure(
                text=f"🔌 Conectado em {st.port} — Carregando parâmetros atuais ($$)...",
                fg=THEME["primary"],
            )
            self._reload_from_board()

    def _reload_from_board(self):
        try:
            self.lbl_conn_info.configure(
                text=f"🔌 Lendo configurações atuais de {self.controller.get_status().port} ($$)...",
                fg=THEME["primary"],
            )
            self.controller.request_grbl_settings()
        except Exception as exc:
            messagebox.showwarning("Leitura GRBL", str(exc), parent=self)

    def _on_controller_settings_threadsafe(self, settings: Dict[int, str]):
        try:
            if not self.winfo_exists():
                return
            if self._refresh_job is not None:
                self.after_cancel(self._refresh_job)
            self._refresh_job = self.after(60, self._apply_loaded_settings, dict(settings))
        except Exception:
            pass

    def _apply_loaded_settings(self, settings: Dict[int, str]):
        self._refresh_job = None
        if not self.winfo_exists():
            return

        self._loaded_settings = dict(settings)
        self._populate_rows(settings)

        st = self.controller.get_status()
        if self._save_pending_notice:
            self._save_pending_notice = False
        if st.connected:
            self.lbl_conn_info.configure(
                text=f"✓ Conectado em {st.port} • {len(settings)} parâmetros carregados da EEPROM.",
                fg=THEME["accent"],
            )

    def _show_empty_state(self, text: str):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self._entries.clear()
        self._vars.clear()
        tk.Label(
            self.rows_frame,
            text=text,
            font=FONTS["label"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
            justify="center",
            pady=48,
        ).grid(row=0, column=0, sticky="ew")

    def _populate_rows(self, settings: Dict[int, str]):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self._entries.clear()
        self._vars.clear()

        if not settings:
            self._show_empty_state("Aguardando resposta '$$' do controlador GRBL...")
            return

        for idx, code in enumerate(sorted(settings.keys())):
            val = settings[code]
            meta = GRBL_SETTINGS_METADATA.get(
                code,
                (f"Parâmetro ${code}", "", "Configuração do firmware GRBL."),
            )
            name, unit, desc = meta
            row_bg = THEME["bg_sidebar"] if idx % 2 == 0 else THEME["bg_card"]

            row = tk.Frame(self.rows_frame, bg=row_bg, padx=10, pady=6)
            row.grid(row=idx, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)

            badge = tk.Label(
                row,
                text=f"${code}",
                font=FONTS["mono_bold"],
                fg=THEME["primary"],
                bg=THEME["primary_light"],
                width=6,
                pady=3,
            )
            badge.grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=1)

            info_box = tk.Frame(row, bg=row_bg)
            info_box.grid(row=0, column=1, sticky="ew", padx=(0, 10))
            info_box.columnconfigure(0, weight=1)

            title_text = f"{name} ({unit})" if unit else name
            tk.Label(
                info_box,
                text=title_text,
                font=FONTS["label_bold"],
                fg=THEME["text_primary"],
                bg=row_bg,
                anchor="w",
            ).grid(row=0, column=0, sticky="w")

            tk.Label(
                info_box,
                text=desc,
                font=FONTS["small"],
                fg=THEME["text_muted"],
                bg=row_bg,
                anchor="w",
                wraplength=430,
                justify="left",
            ).grid(row=1, column=0, sticky="w")

            var = tk.StringVar(value=str(val))
            entry = tk.Entry(
                row,
                textvariable=var,
                font=FONTS["input"],
                bg=THEME["bg_input"],
                fg=THEME["text_primary"],
                insertbackground=THEME["primary"],
                relief="flat",
                bd=0,
                width=12,
                justify="right",
                highlightthickness=1,
                highlightbackground=THEME["border_strong"],
                highlightcolor=THEME["border_focus"],
            )
            entry.grid(row=0, column=2, sticky="e", ipady=5, ipadx=6)

            var.trace_add("write", lambda *_args, c=code: self._on_field_edited(c))
            self._vars[code] = var
            self._entries[code] = entry

        self._update_pending_summary()

    def _on_field_edited(self, code: int):
        if code not in self._entries:
            return
        entry = self._entries[code]
        cur_val = self._vars[code].get().strip().replace(",", ".")
        orig_val = str(self._loaded_settings.get(code, "")).strip()

        if cur_val != orig_val:
            entry.configure(
                bg=THEME["primary_light"],
                highlightbackground=THEME["primary"],
            )
        else:
            entry.configure(
                bg=THEME["bg_input"],
                highlightbackground=THEME["border_strong"],
            )
        self._update_pending_summary()

    def _collect_changes(self) -> Dict[int, str]:
        changes: Dict[int, str] = {}
        for code, var in self._vars.items():
            cur = var.get().strip().replace(",", ".")
            orig = str(self._loaded_settings.get(code, "")).strip()
            if cur != orig:
                changes[code] = cur
        return changes

    def _update_pending_summary(self):
        changes = self._collect_changes()
        st = self.controller.get_status()
        can_save = st.connected and (not st.streaming or st.paused) and len(changes) > 0

        if not changes:
            self.lbl_changes.configure(text="Nenhuma alteração pendente.", fg=THEME["text_muted"])
        elif len(changes) == 1:
            code = next(iter(changes.keys()))
            self.lbl_changes.configure(
                text=f"1 alteração pendente (${code}={changes[code]}).",
                fg=THEME["primary"],
            )
        else:
            self.lbl_changes.configure(
                text=f"{len(changes)} alterações pendentes para gravar no Arduino.",
                fg=THEME["primary"],
            )

        self.btn_save.configure(state="normal" if can_save else "disabled")

    def _save_changes(self):
        changes = self._collect_changes()
        if not changes:
            return

        for code, val in changes.items():
            if not re.match(r"^[-+]?\d*\.?\d+$", val):
                messagebox.showerror(
                    "Valor Inválido",
                    f"O parâmetro ${code} recebeu um valor inválido ('{val}').\n"
                    "Informe apenas números (ex: 80 ou 80.000).",
                    parent=self,
                )
                return

        try:
            self._save_pending_notice = True
            self.controller.save_grbl_settings(changes)
            self._log(f"💾 Configurações GRBL enviadas ({len(changes)} parâmetro(s) atualizado(s)).")
            # Atualiza linha base local e re-solicita $$ para confirmar gravação real na EEPROM
            self._loaded_settings.update(changes)
            for code in list(changes.keys()):
                self._on_field_edited(code)
            messagebox.showinfo(
                "Configurações Salvas",
                f"{len(changes)} parâmetro(s) enviado(s) para o GRBL com sucesso!",
                parent=self,
            )
            if self.winfo_exists():
                self._reload_from_board()
        except Exception as exc:
            self._save_pending_notice = False
            messagebox.showerror("Erro ao salvar no GRBL", str(exc), parent=self)

    def _on_close(self):
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self.controller.on_settings_change = self._prev_on_settings_change
        self.destroy()


class SettingsModal(tk.Toplevel):
    """Modal geral de Configurações aberto pelo menu superior da aplicação."""

    def __init__(
        self,
        parent: tk.Misc,
        controller: GrblController,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.controller = controller
        self._log = on_log or (lambda _m: None)

        self.title("Configurações — MonteBot GCreator")
        self.configure(bg=THEME["bg_app"])
        self.geometry("460x310")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.invert_y_var = tk.BooleanVar(value=bool(self.controller.invert_y_motor))
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        hdr = tk.Frame(
            self,
            bg=THEME["bg_header"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=16,
            pady=12,
        )
        hdr.grid(row=0, column=0, sticky="ew")

        tk.Label(
            hdr,
            text="⚙ Configurações da Máquina e Sistema",
            font=FONTS["title"],
            fg=THEME["primary"],
            bg=THEME["bg_header"],
        ).pack(anchor="w")

        body = tk.Frame(self, bg=THEME["bg_app"], padx=16, pady=14)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        # Card 1: Editor do Firmware GRBL
        grbl_card = tk.Frame(
            body,
            bg=THEME["bg_sidebar"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=12,
            pady=10,
        )
        grbl_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        grbl_card.columnconfigure(0, weight=1)

        tk.Label(
            grbl_card,
            text="CONTROLADORA ARDUINO / GRBL",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, sticky="w")

        st = self.controller.get_status()
        conn_txt = (
            f"Conectado em {st.port} — Pronto para ler/editar parâmetros $."
            if st.connected
            else "Desconectado — Conecte na aba '2. Controle GRBL' para editar a EEPROM."
        )
        tk.Label(
            grbl_card,
            text=conn_txt,
            font=FONTS["small"],
            fg=THEME["accent"] if st.connected else THEME["text_muted"],
            bg=THEME["bg_sidebar"],
            wraplength=390,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))

        ModernButton(
            grbl_card,
            text="🔧 Editar GRBL ($$)",
            command=self._open_grbl_editor,
            variant="primary",
            font_key="button",
            pady=7,
        ).grid(row=2, column=0, sticky="ew")

        # Card 2: Preferências Locais da Máquina
        mach_card = tk.Frame(
            body,
            bg=THEME["bg_sidebar"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=12,
            pady=10,
        )
        mach_card.grid(row=1, column=0, sticky="ew")
        mach_card.columnconfigure(0, weight=1)

        tk.Label(
            mach_card,
            text="CINEMÁTICA E EIXOS",
            font=FONTS["section"],
            fg=THEME["text_muted"],
            bg=THEME["bg_sidebar"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        tk.Checkbutton(
            mach_card,
            text="Inverter direção física do eixo Y na comunicação GRBL",
            variable=self.invert_y_var,
            command=self._toggle_invert_y,
            font=FONTS["small"],
            fg=THEME["text_primary"],
            bg=THEME["bg_sidebar"],
            activebackground=THEME["bg_sidebar"],
            selectcolor=THEME["bg_input"],
            cursor="hand2",
        ).grid(row=1, column=0, sticky="w")

        # Rodapé
        footer = tk.Frame(self, bg=THEME["bg_app"], padx=16, pady=8)
        footer.grid(row=2, column=0, sticky="ew")
        ModernButton(
            footer,
            text="Fechar",
            command=self.destroy,
            variant="secondary",
            font_key="toolbar_btn",
            padx=16,
            pady=5,
        ).pack(side="right")

    def _toggle_invert_y(self):
        val = bool(self.invert_y_var.get())
        self.controller.invert_y_motor = val
        self._log(f"⚙ Inversão do eixo Y na comunicação GRBL: {'Ativada' if val else 'Desativada'}.")

    def _open_grbl_editor(self):
        parent = self.master
        self.destroy()
        GrblSettingsModal(parent, controller=self.controller, on_log=self._log)
