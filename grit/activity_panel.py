import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from grit.core.models import ActivityTimeline

LABEL_W = 180
HEADER_H = 28
CELL_W = 36
CELL_H = 26
PAD = 8
EMPTY_FILL = "#f2f2f2"
LOW = (207, 232, 255)
HIGH = (8, 81, 156)


def _blend(t: float) -> str:
    r, g, b = (round(lo + (hi - lo) * t) for lo, hi in zip(LOW, HIGH))
    return f"#{r:02x}{g:02x}{b:02x}"


class ActivityPanel(ttk.Frame):
    """Mapa de calor: commits por semana (colunas) e por aluno (linhas)."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)

        self.canvas = tk.Canvas(self, background="white", highlightthickness=0)
        scroll_x = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)

        self.legend = ttk.Label(
            self,
            text="Cada célula mostra os commits do aluno na semana iniciada na segunda-feira indicada.",
            style="StatTitle.TLabel",
        )

        self.legend.pack(side=tk.BOTTOM, anchor=tk.W, pady=(4, 0))
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.show(None, [])

    def show(self, timeline: Optional[ActivityTimeline], author_order: List[str]):
        self.canvas.delete("all")

        if timeline is None or not timeline.weeks or not author_order:
            self.canvas.create_text(
                PAD, PAD, anchor=tk.NW, fill="#666",
                text="Execute uma análise para ver a atividade semanal.",
            )
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        peak = max((c for per_week in timeline.counts.values() for c in per_week.values()), default=1)

        for col, week in enumerate(timeline.weeks):
            x = PAD + LABEL_W + col * CELL_W
            self.canvas.create_text(
                x + CELL_W / 2, PAD + HEADER_H / 2, text=week.strftime("%d/%m"),
                font=("Helvetica", 8), fill="#444",
            )

        for row, name in enumerate(author_order):
            y = PAD + HEADER_H + row * CELL_H
            label = name if len(name) <= 24 else name[:23] + "…"
            self.canvas.create_text(
                PAD, y + CELL_H / 2, text=label, anchor=tk.W, font=("Helvetica", 9),
            )
            per_week = timeline.counts.get(name, {})
            for col, week in enumerate(timeline.weeks):
                x = PAD + LABEL_W + col * CELL_W
                count = per_week.get(week, 0)
                fill, text_color = self._colors(count, peak)
                self.canvas.create_rectangle(
                    x + 1, y + 1, x + CELL_W - 1, y + CELL_H - 1, fill=fill, outline="white",
                )
                if count:
                    self.canvas.create_text(
                        x + CELL_W / 2, y + CELL_H / 2, text=str(count),
                        font=("Helvetica", 8, "bold"), fill=text_color,
                    )

        width = PAD * 2 + LABEL_W + len(timeline.weeks) * CELL_W
        height = PAD * 2 + HEADER_H + len(author_order) * CELL_H
        self.canvas.configure(scrollregion=(0, 0, width, height))

    @staticmethod
    def _colors(count: int, peak: int) -> Tuple[str, str]:
        if count <= 0:
            return EMPTY_FILL, "black"
        t = count / peak
        return _blend(t), ("white" if t > 0.55 else "black")
