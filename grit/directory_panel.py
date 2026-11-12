import tkinter as tk
from tkinter import font, ttk
from typing import Dict

from grit import ui_theme as theme
from grit.activity_panel import ActivityPanel


class DirectoryPanel(ttk.Frame):
    """Mapa de commits por autor e diretório de primeiro nível."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=theme.SURFACE, highlightthickness=0)
        scroll_x = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.show({})

    def show(self, counts: Dict[str, Dict[str, int]]):
        self.canvas.delete("all")
        directories = sorted({directory for per_author in counts.values() for directory in per_author})
        if not directories:
            self.canvas.create_text(
                8, 8, anchor=tk.NW, fill=theme.MUTED,
                text="Nenhuma participação por diretório disponível.",
            )
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        authors = sorted(counts)
        text_font = font.nametofont("TkDefaultFont")
        label_width = max(180, max(text_font.measure(name) for name in authors) + 24)
        widths = [max(100, text_font.measure(directory) + 24) for directory in directories]
        peak = max(count for per_author in counts.values() for count in per_author.values())
        x = label_width
        for directory, width in zip(directories, widths):
            self.canvas.create_text(x + width / 2, 18, text=directory, fill=theme.MUTED)
            x += width
        for row, name in enumerate(authors):
            y = 36 + row * 28
            self.canvas.create_text(8, y + 14, anchor=tk.W, text=name, fill=theme.TEXT)
            x = label_width
            for directory, width in zip(directories, widths):
                count = counts[name].get(directory, 0)
                background, foreground = ActivityPanel._colors(count, peak)
                self.canvas.create_rectangle(x + 1, y + 1, x + width - 1, y + 27,
                                             fill=background, outline=theme.SURFACE)
                self.canvas.create_text(x + width / 2, y + 14, text=str(count), fill=foreground)
                x += width
        self.canvas.configure(scrollregion=(0, 0, x + 8, 44 + len(authors) * 28))
