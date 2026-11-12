import logging
import queue
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Tuple

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
MAX_LINES = 5000
DRAIN_BATCH = 300
POLL_INTERVAL_MS = 100
DEFAULT_LEVEL = "INFO"


class QueueLogHandler(logging.Handler):
    """Enfileira registros de log (seguro entre threads) para a UI consumir."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: "queue.Queue[Tuple[int, str]]" = queue.Queue()
        self.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))

    def emit(self, record: logging.LogRecord):
        try:
            self.records.put((record.levelno, self.format(record)))
        except Exception:
            self.handleError(record)


class LogPanel(ttk.Frame):
    LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}

    def __init__(self, parent: tk.Misc, handler: QueueLogHandler):
        super().__init__(parent, padding=6)
        self.handler = handler
        self.handler.setLevel(self.LEVELS[DEFAULT_LEVEL])
        self.autoscroll = tk.BooleanVar(value=True)
        self.level_var = tk.StringVar(value=DEFAULT_LEVEL)

        self._build()
        self.after(POLL_INTERVAL_MS, self._drain)

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(toolbar, text="Nível:").pack(side=tk.LEFT)
        level_box = ttk.Combobox(
            toolbar, textvariable=self.level_var, values=list(self.LEVELS),
            state="readonly", width=8,
        )
        level_box.pack(side=tk.LEFT, padx=(4, 12))
        level_box.bind("<<ComboboxSelected>>", self._on_level_change)

        ttk.Checkbutton(toolbar, text="Rolar automaticamente", variable=self.autoscroll).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Salvar...", command=self._save).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Limpar", command=self.clear).pack(side=tk.RIGHT, padx=6)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(
            body, wrap=tk.NONE, state=tk.DISABLED, font=("Consolas", 9),
            background="#1e1e1e", foreground="#d4d4d4", bd=0, highlightthickness=0,
        )
        scroll_y = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        scroll_x = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_x.pack(fill=tk.X)

        self.text.tag_configure("DEBUG", foreground="#808080")
        self.text.tag_configure("INFO", foreground="#d4d4d4")
        self.text.tag_configure("WARNING", foreground="#e5a800")
        self.text.tag_configure("ERROR", foreground="#f14c4c")

    def _on_level_change(self, _event=None):
        self.handler.setLevel(self.LEVELS[self.level_var.get()])

    def _drain(self):
        lines = []
        try:
            while len(lines) < DRAIN_BATCH:
                lines.append(self.handler.records.get_nowait())
        except queue.Empty:
            pass

        if lines:
            self._append(lines)
        self.after(POLL_INTERVAL_MS, self._drain)

    def _append(self, lines):
        self.text.configure(state=tk.NORMAL)
        for levelno, message in lines:
            tag = logging.getLevelName(levelno)
            if tag not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                tag = "ERROR" if levelno > logging.WARNING else "INFO"
            self.text.insert(tk.END, message + "\n", tag)

        total = int(self.text.index("end-1c").split(".")[0])
        if total > MAX_LINES:
            self.text.delete("1.0", f"{total - MAX_LINES + 1}.0")
        self.text.configure(state=tk.DISABLED)

        if self.autoscroll.get():
            self.text.see(tk.END)

    def clear(self):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)

    def _save(self):
        path = filedialog.asksaveasfilename(
            title="Salvar log",
            defaultextension=".log",
            filetypes=[("Arquivos de log", "*.log"), ("Texto", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.get("1.0", "end-1c") + "\n")
