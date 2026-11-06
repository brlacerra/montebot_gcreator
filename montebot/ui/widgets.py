"""
Widgets customizados e reutilizáveis em White Mode (Botões com hover, Inputs com foco, Cards de métricas).
"""

import tkinter as tk
from typing import Callable, Optional

from montebot.config import FONTS, THEME


class ModernButton(tk.Button):
    """Botão flat moderno com estados de hover e desabilitado consistentes no White Mode."""

    VARIANTS = {
        "primary": (THEME["primary"], THEME["primary_fg"], THEME["primary_hover"]),
        "accent": (THEME["accent"], THEME["accent_fg"], THEME["accent_hover"]),
        "secondary": (THEME["secondary_bg"], THEME["secondary_fg"], THEME["secondary_hover"]),
        "toolbar": (THEME["secondary_bg"], THEME["text_secondary"], THEME["secondary_hover"]),
        "toolbar_active": (THEME["primary"], THEME["primary_fg"], THEME["primary_hover"]),
    }

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command: Optional[Callable] = None,
        variant: str = "primary",
        font_key: str = "button",
        padx: int = 12,
        pady: int = 8,
        **kwargs,
    ):
        self._variant = variant
        bg, fg, hover_bg = self.VARIANTS.get(variant, self.VARIANTS["secondary"])

        super().__init__(
            parent,
            text=text,
            command=command,
            font=FONTS.get(font_key, FONTS["button"]),
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            disabledforeground=THEME["disabled_fg"],
            relief="flat",
            bd=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            highlightthickness=1 if variant in ("secondary", "toolbar") else 0,
            highlightbackground=THEME["border_strong"],
            highlightcolor=THEME["border_focus"],
            **kwargs,
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def set_variant(self, variant: str):
        self._variant = variant
        self._apply_colors(hover=False)

    def _apply_colors(self, hover: bool = False):
        state = str(self.cget("state"))
        if state == "disabled":
            super().configure(
                bg=THEME["disabled_bg"],
                fg=THEME["disabled_fg"],
                cursor="arrow",
                highlightthickness=1,
                highlightbackground=THEME["border"],
            )
            return

        bg, fg, hover_bg = self.VARIANTS.get(self._variant, self.VARIANTS["secondary"])
        use_bg = hover_bg if hover else bg
        super().configure(
            bg=use_bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            cursor="hand2",
            highlightthickness=1 if self._variant in ("secondary", "toolbar") else 0,
            highlightbackground=THEME["border_strong"],
        )

    def configure(self, cnf=None, **kw):
        super().configure(cnf, **kw)
        if "state" in kw or (isinstance(cnf, dict) and "state" in cnf):
            self._apply_colors(hover=False)

    config = configure

    def _on_enter(self, _event):
        if str(self.cget("state")) != "disabled":
            self._apply_colors(hover=True)

    def _on_leave(self, _event):
        if str(self.cget("state")) != "disabled":
            self._apply_colors(hover=False)


class LabeledEntry(tk.Frame):
    """Campo de entrada rotulado com borda reativa ao foco (White Mode)."""

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        default: str = "",
        bg: str = THEME["bg_sidebar"],
    ):
        super().__init__(parent, bg=bg)
        self.columnconfigure(0, weight=1)

        self.lbl = tk.Label(
            self,
            text=label,
            font=FONTS["small"],
            fg=THEME["text_secondary"],
            bg=bg,
            anchor="w",
        )
        self.lbl.grid(row=0, column=0, sticky="w", pady=(0, 2))

        self.entry = tk.Entry(
            self,
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
        self.entry.insert(0, default)
        self.entry.grid(row=1, column=0, sticky="ew", ipady=5, ipadx=6)

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, value: str):
        self.entry.delete(0, "end")
        self.entry.insert(0, str(value))


class MetricBox(tk.Frame):
    """Pequeno card para exibição de uma métrica individual do G-code."""

    def __init__(self, parent: tk.Widget, title: str, initial_value: str = "—"):
        super().__init__(
            parent,
            bg=THEME["bg_card"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            padx=8,
            pady=6,
        )
        self.columnconfigure(0, weight=1)

        self.lbl_title = tk.Label(
            self,
            text=title,
            font=("Segoe UI", 7, "bold"),
            fg=THEME["text_muted"],
            bg=THEME["bg_card"],
            anchor="w",
        )
        self.lbl_title.grid(row=0, column=0, sticky="w")

        self.lbl_value = tk.Label(
            self,
            text=initial_value,
            font=FONTS["mono_bold"],
            fg=THEME["text_primary"],
            bg=THEME["bg_card"],
            anchor="w",
        )
        self.lbl_value.grid(row=1, column=0, sticky="w", pady=(1, 0))

    def set_value(self, value: str, fg: Optional[str] = None):
        self.lbl_value.configure(text=value, fg=fg or THEME["text_primary"])

