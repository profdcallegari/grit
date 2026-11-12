import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import Dict, Optional

from grit.core.models import AttentionConfig, AttentionFinding, AttentionReport


CATEGORY_LABELS = {
    "commit": "Commits",
    "arquivo": "Arquivos",
    "testes": "Testes",
    "segurança": "Segurança",
    "prazo": "Prazo",
    "branch": "Branches",
    "identidade": "Identidades",
}
KIND_LABELS = {
    "large_commit_files": "Muitos arquivos no commit",
    "large_commit_churn": "Muitas linhas no commit",
    "many_directories": "Muitos diretórios",
    "immediate_fix": "Correção imediata",
    "possible_revert": "Possível reversão",
    "large_file": "Arquivo grande",
    "generated_file": "Arquivo possivelmente gerado",
    "conflict_marker": "Possível conflito",
    "possible_secret": "Possível segredo",
    "code_without_tests": "Código sem testes no commit",
    "generic_identity": "Identidade genérica",
    "multiple_emails": "Múltiplos e-mails",
    "shared_email": "E-mail compartilhado",
    "deadline_concentration": "Concentração perto do prazo",
    "possibly_abandoned": "Possível branch abandonada",
    "long_lived_branch": "Branch prolongada",
}


def _parse_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use o formato AAAA-MM-DD.")
    return parsed


class AttentionPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(0, 6, 0, 0))
        self.report: Optional[AttentionReport] = None
        self._rows: Dict[str, AttentionFinding] = {}
        self.summary = tk.StringVar(value="Execute uma análise para ver os pontos de atenção.")
        ttk.Label(self, textvariable=self.summary, style="StatTitle.TLabel").pack(anchor=tk.W)

        views = ttk.Notebook(self)
        views.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        occurrences = ttk.Frame(views, padding=4)
        coverage = ttk.Frame(views, padding=4)
        settings = ttk.Frame(views, padding=4)
        views.add(occurrences, text=" Ocorrências ")
        views.add(coverage, text=" Cobertura e limites ")
        views.add(settings, text=" Configuração ")

        filters = ttk.Frame(occurrences)
        filters.pack(fill=tk.X)
        self.category = tk.StringVar(value="Todas")
        self.author = tk.StringVar()
        self.from_date = tk.StringVar()
        self.to_date = tk.StringVar()
        ttk.Label(filters, text="Categoria:").pack(side=tk.LEFT)
        self.category_box = ttk.Combobox(filters, textvariable=self.category, state="readonly", width=13)
        self.category_box.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(filters, text="Autor:").pack(side=tk.LEFT)
        ttk.Entry(filters, textvariable=self.author, width=12).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(filters, text="De:").pack(side=tk.LEFT)
        ttk.Entry(filters, textvariable=self.from_date, width=10).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(filters, text="Até:").pack(side=tk.LEFT)
        ttk.Entry(filters, textvariable=self.to_date, width=10).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(filters, text="Filtrar", command=self._apply_filters).pack(side=tk.LEFT)
        ttk.Label(occurrences, text="Datas: AAAA-MM-DD. Os alertas são indícios, não avaliações.",
                  style="StatTitle.TLabel").pack(anchor=tk.W, pady=(3, 0))

        pane = ttk.PanedWindow(occurrences, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)
        table_frame = ttk.Frame(pane)
        pane.add(table_frame, weight=3)
        columns = ("category", "kind", "date", "author", "evidence", "reference")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for key, label, width in (
            ("category", "Categoria", 100), ("kind", "Tipo", 150), ("date", "Data", 105),
            ("author", "Autor", 120), ("evidence", "Evidência", 220), ("reference", "Commit / arquivo", 160),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70, anchor=tk.W)
        scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._show_details)

        detail_frame = ttk.LabelFrame(pane, text=" Evidência e limitações ", padding=4)
        pane.add(detail_frame, weight=1)
        self.detail = tk.Text(detail_frame, height=3, wrap=tk.WORD, state=tk.DISABLED)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail.pack(fill=tk.BOTH, expand=True)

        cards = ttk.Frame(coverage)
        cards.pack(fill=tk.X, pady=(0, 6))
        self.category_counts = {}
        for index, (category, label) in enumerate(CATEGORY_LABELS.items()):
            card = ttk.Frame(cards, style="Card.TFrame", padding=5)
            card.grid(row=index // 4, column=index % 4, sticky=tk.EW, padx=2, pady=2)
            ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor=tk.W)
            value = tk.StringVar(value="—")
            ttk.Label(card, textvariable=value, style="StatValue.TLabel").pack(anchor=tk.W)
            self.category_counts[category] = value
        for column in range(4):
            cards.columnconfigure(column, weight=1, uniform="attention-cards")
        coverage_body = ttk.Frame(coverage)
        coverage_body.pack(fill=tk.BOTH, expand=True)
        self.coverage_text = tk.Text(coverage_body, wrap=tk.WORD, state=tk.DISABLED)
        coverage_scroll = ttk.Scrollbar(coverage_body, orient=tk.VERTICAL, command=self.coverage_text.yview)
        self.coverage_text.configure(yscrollcommand=coverage_scroll.set)
        coverage_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.coverage_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        defaults = AttentionConfig()
        self.config_vars = {
            "max_commit_files": tk.StringVar(value=str(defaults.max_commit_files)),
            "max_commit_churn": tk.StringVar(value=str(defaults.max_commit_churn)),
            "max_directories": tk.StringVar(value=str(defaults.max_directories)),
            "max_file_bytes": tk.StringVar(value=str(defaults.max_file_bytes)),
            "deadline": tk.StringVar(),
            "deadline_days": tk.StringVar(value=str(defaults.deadline_days)),
            "test_paths": tk.StringVar(value=", ".join(defaults.test_paths)),
        }
        fields = (
            ("max_commit_files", "Arquivos por commit"),
            ("max_commit_churn", "Linhas alteradas por commit"),
            ("max_directories", "Diretórios por commit"),
            ("max_file_bytes", "Tamanho de arquivo (bytes)"),
            ("deadline", "Prazo opcional (AAAA-MM-DD)"),
            ("deadline_days", "Dias anteriores ao prazo"),
            ("test_paths", "Caminhos de teste (separados por vírgula)"),
        )
        for row, (key, label) in enumerate(fields):
            ttk.Label(settings, text=label + ":").grid(row=row, column=0, sticky=tk.W, padx=(0, 8), pady=2)
            ttk.Entry(settings, textvariable=self.config_vars[key], width=35).grid(
                row=row, column=1, sticky=tk.EW, pady=2
            )
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="Alterações passam a valer ao executar uma nova análise.",
                  style="StatTitle.TLabel").grid(row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=4)

    def get_config(self) -> AttentionConfig:
        values = {key: var.get().strip() for key, var in self.config_vars.items()}
        numbers = {}
        for key, label in (
            ("max_commit_files", "Arquivos por commit"), ("max_commit_churn", "Linhas por commit"),
            ("max_directories", "Diretórios por commit"), ("max_file_bytes", "Tamanho de arquivo"),
            ("deadline_days", "Dias anteriores ao prazo"),
        ):
            try:
                number = int(values[key])
            except ValueError as exc:
                raise ValueError(f"{label} deve ser um número inteiro positivo.") from exc
            if number <= 0:
                raise ValueError(f"{label} deve ser um número inteiro positivo.")
            numbers[key] = number
        try:
            deadline = _parse_date(values["deadline"]) if values["deadline"] else None
        except ValueError as exc:
            raise ValueError("O prazo deve estar no formato AAAA-MM-DD.") from exc
        paths = tuple(path.strip() for path in values["test_paths"].split(",") if path.strip())
        if not paths:
            raise ValueError("Informe ao menos um caminho de teste.")
        return AttentionConfig(**numbers, deadline=deadline, test_paths=paths)

    @staticmethod
    def _set_text(widget: tk.Text, value: str):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

    def show(self, report: Optional[AttentionReport]):
        self.report = report
        findings = report.findings if report else []
        for key, value in self.category_counts.items():
            value.set(str(sum(item.category == key for item in findings)) if report else "—")
        categories = sorted({CATEGORY_LABELS.get(item.category, item.category) for item in findings})
        self.category_box.configure(values=("Todas", *categories))
        self.category.set("Todas")
        self.author.set("")
        self.from_date.set("")
        self.to_date.set("")
        if report is None:
            self.summary.set("Análise de pontos de atenção indisponível.")
            self._set_text(self.coverage_text, "Nenhuma análise de pontos de atenção foi executada.")
        else:
            self.summary.set(f"{len(findings)} ocorrência(s) · Consulte cobertura e limites.")
            self._set_text(self.coverage_text, "\n\n".join(report.limitations) or "Nenhuma limitação informada.")
        self._populate(findings)

    def _apply_filters(self):
        try:
            start = _parse_date(self.from_date.get().strip()) if self.from_date.get().strip() else None
            end = _parse_date(self.to_date.get().strip()) if self.to_date.get().strip() else None
            if start and end and start > end:
                raise ValueError("A data inicial deve ser anterior ou igual à final.")
        except ValueError as exc:
            messagebox.showwarning("Filtro inválido", f"Use datas AAAA-MM-DD. {exc}")
            return
        findings = self.report.findings if self.report else []
        author = self.author.get().strip().casefold()
        category = self.category.get()
        self._populate([
            item for item in findings
            if (category == "Todas" or CATEGORY_LABELS.get(item.category, item.category) == category)
            and (not author or author in item.author.casefold())
            and (not start or (item.timestamp is not None and item.timestamp.date() >= start))
            and (not end or (item.timestamp is not None and item.timestamp.date() <= end))
        ])

    def _populate(self, findings):
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        self._set_text(self.detail, "Selecione uma ocorrência para ver os detalhes.")
        for item in findings:
            iid = self.tree.insert("", tk.END, values=(
                CATEGORY_LABELS.get(item.category, item.category), KIND_LABELS.get(item.kind, item.kind),
                item.timestamp.strftime("%Y-%m-%d") if item.timestamp else "—",
                item.author or "—", item.evidence, item.reference or "—",
            ))
            self._rows[iid] = item

    def _show_details(self, _event=None):
        selected = self.tree.selection()
        item = self._rows.get(selected[0]) if selected else None
        self._set_text(self.detail, item.details if item else "Selecione uma ocorrência para ver os detalhes.")
