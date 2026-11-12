import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from grit.core.models import ActivityTimeline
from grit import ui_theme as theme

LABEL_W = 180
HEADER_H = 28
CELL_W = 36
CELL_H = 26
PAD = 8
EMPTY_FILL = "#e7edf4"
LOW = (207, 232, 255)
HIGH = (8, 81, 156)


def _blend(t: float) -> str:
    r, g, b = (round(lo + (hi - lo) * t) for lo, hi in zip(LOW, HIGH))
    return f"#{r:02x}{g:02x}{b:02x}"


class ActivityPanel(ttk.Frame):
    """Mapa de calor: commits por semana (colunas) e por aluno (linhas)."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)

        self.canvas = tk.Canvas(self, background=theme.SURFACE, highlightthickness=0)
        scroll_x = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)

        legend = ttk.Frame(self)
        legend.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        ttk.Label(legend, text="Commits por semana (segunda-feira) · Intensidade relativa:",
                  style="StatTitle.TLabel").pack(side=tk.LEFT)
        ttk.Label(legend, text="  nenhum", style="StatTitle.TLabel").pack(side=tk.LEFT)
        for color in (EMPTY_FILL, _blend(0.25), _blend(0.5), _blend(0.75), _blend(1)):
            tk.Label(legend, background=color, width=2, height=1).pack(side=tk.LEFT, padx=(3, 0))
        ttk.Label(legend, text="  mais", style="StatTitle.TLabel").pack(side=tk.LEFT)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.show(None, [])

    def show(self, timeline: Optional[ActivityTimeline], author_order: List[str], analyzed: bool = False):
        self.canvas.delete("all")

        if timeline is None or not timeline.weeks or not author_order:
            self.canvas.create_text(
                PAD, PAD, anchor=tk.NW, fill=theme.MUTED,
                text=("Nenhuma atividade semanal disponível nesta análise." if analyzed
                      else "Execute uma análise para ver a atividade semanal."),
            )
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        peak = max((c for per_week in timeline.counts.values() for c in per_week.values()), default=1)

        for col, week in enumerate(timeline.weeks):
            x = PAD + LABEL_W + col * CELL_W
            self.canvas.create_text(
                x + CELL_W / 2, PAD + HEADER_H / 2, text=week.strftime("%d/%m"),
                font=("TkDefaultFont", 9, "bold"), fill=theme.MUTED,
            )

        for row, name in enumerate(author_order):
            y = PAD + HEADER_H + row * CELL_H
            label = name if len(name) <= 24 else name[:23] + "…"
            self.canvas.create_text(
                PAD, y + CELL_H / 2, text=label, anchor=tk.W, font=("TkDefaultFont", 9), fill=theme.TEXT,
            )
            per_week = timeline.counts.get(name, {})
            for col, week in enumerate(timeline.weeks):
                x = PAD + LABEL_W + col * CELL_W
                count = per_week.get(week, 0)
                fill, text_color = self._colors(count, peak)
                self.canvas.create_rectangle(
                    x + 1, y + 1, x + CELL_W - 1, y + CELL_H - 1, fill=fill, outline=theme.SURFACE,
                )
                if count:
                    self.canvas.create_text(
                        x + CELL_W / 2, y + CELL_H / 2, text=str(count),
                        font=("TkDefaultFont", 8, "bold"), fill=text_color,
                    )

        width = PAD * 2 + LABEL_W + len(timeline.weeks) * CELL_W
        height = PAD * 2 + HEADER_H + len(author_order) * CELL_H
        self.canvas.configure(scrollregion=(0, 0, width, height))

    @staticmethod
    def _colors(count: int, peak: int) -> Tuple[str, str]:
        if count <= 0:
            return EMPTY_FILL, "black"
        t = count / peak
        fill = _blend(t)
        channels = [int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        return fill, ("white" if luminance < 0.179 else "black")
