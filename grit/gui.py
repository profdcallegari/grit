import logging
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional

from grit.activity_panel import ActivityPanel
from grit.core.analyzer import RepositoryAnalyzer
from grit.core.models import AuthorStats, BranchReport, RepositoryReport
from grit.log_panel import LogPanel, QueueLogHandler
from grit import ui_theme as theme

logger = logging.getLogger("grit.gui")

LINK_HINT = "Duplo clique em um autor, branch ou commit abre o item no navegador."
NO_LINKS_HINT = "Links indisponíveis: o remoto 'origin' não é um repositório GitHub/GitLab reconhecido."
class GritApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GRIT - Git Repository Inspector for Teachers")
        self.geometry("980x680")
        self.minsize(850, 550)

        self.current_report: Optional[RepositoryReport] = None
        self._sort_reverse = False
        self._row_authors: Dict[str, AuthorStats] = {}
        self._part_row_authors: Dict[str, AuthorStats] = {}

        self._setup_logging()
        self._configure_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("GRIT iniciado. Informe um repositório e clique em 'Analisar Repositório'.")

    def _setup_logging(self):
        self.log_handler = QueueLogHandler()
        grit_logger = logging.getLogger("grit")
        grit_logger.setLevel(logging.DEBUG)
        grit_logger.addHandler(self.log_handler)

    def _on_close(self):
        logging.getLogger("grit").removeHandler(self.log_handler)
        self.destroy()

    def _configure_styles(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure(background=theme.BACKGROUND)

        self.style.configure(".", background=theme.BACKGROUND, foreground=theme.TEXT, font="TkDefaultFont")
        self.style.configure("TFrame", background=theme.BACKGROUND)
        self.style.configure("TLabel", background=theme.BACKGROUND, foreground=theme.TEXT)
        self.style.configure("TLabelframe", background=theme.BACKGROUND, bordercolor=theme.BORDER)
        self.style.configure("TLabelframe.Label", background=theme.BACKGROUND, foreground=theme.TEXT,
                             font=("TkDefaultFont", 10, "bold"))
        self.style.configure("TCheckbutton", background=theme.BACKGROUND, foreground=theme.TEXT)
        self.style.map("TCheckbutton", background=[("active", theme.BACKGROUND)])
        self.style.configure("TButton", padding=(10, 6), background=theme.ACCENT_PALE,
                             foreground=theme.TEXT, bordercolor=theme.BORDER)
        self.style.map("TButton", background=[("active", theme.ACCENT_LIGHT)])
        self.style.configure("Accent.TButton", background=theme.ACCENT, foreground=theme.SURFACE,
                             bordercolor=theme.ACCENT, font=("TkDefaultFont", 10, "bold"))
        self.style.map("Accent.TButton",
                       background=[("disabled", theme.BORDER), ("pressed", theme.ACCENT_HOVER),
                                   ("active", theme.ACCENT_HOVER)],
                       foreground=[("disabled", theme.MUTED)])
        self.style.configure("TEntry", fieldbackground=theme.SURFACE, foreground=theme.TEXT,
                             bordercolor=theme.BORDER, padding=4)
        self.style.map("TEntry", bordercolor=[("focus", theme.ACCENT)])
        self.style.configure("Card.TFrame", background=theme.SURFACE, relief=tk.SOLID, borderwidth=1)
        self.style.configure("StatValue.TLabel", background=theme.SURFACE, foreground=theme.ACCENT,
                             font=("TkDefaultFont", 17, "bold"))
        self.style.configure("CardTitle.TLabel", background=theme.SURFACE, foreground=theme.MUTED,
                             font=("TkDefaultFont", 9))
        self.style.configure("StatTitle.TLabel", foreground=theme.MUTED, font=("TkDefaultFont", 9))
        self.style.configure("Status.TLabel", background=theme.ACCENT_LIGHT, foreground=theme.TEXT, padding=(12, 7))
        self.style.configure("TNotebook", background=theme.BACKGROUND, borderwidth=0)
        self.style.configure("TNotebook.Tab", padding=(14, 8), background=theme.ACCENT_LIGHT, foreground=theme.TEXT)
        self.style.map("TNotebook.Tab",
                       padding=[("selected", (14, 8)), ("!selected", (14, 8))],
                       background=[("selected", theme.ACCENT), ("active", theme.ACCENT_PALE)],
                       foreground=[("selected", theme.SURFACE)])
        self.style.configure("Treeview", background=theme.SURFACE, fieldbackground=theme.SURFACE,
                             foreground=theme.TEXT, rowheight=27, bordercolor=theme.BORDER)
        self.style.map("Treeview", background=[("selected", theme.ACCENT)],
                       foreground=[("selected", theme.SURFACE)])
        self.style.configure("Treeview.Heading", background=theme.ACCENT_LIGHT, foreground=theme.TEXT,
                             font=("TkDefaultFont", 9, "bold"), padding=(6, 6))
        self.style.map("Treeview.Heading", background=[("active", theme.BORDER)])
        self.style.configure("Horizontal.TProgressbar", background=theme.ACCENT, troughcolor=theme.BORDER)

    def _build_ui(self):
        # 1. Painel Superior: Configuração e Entradas
        input_frame = ttk.LabelFrame(self, text=" Configurações do Repositório ", padding=10)
        input_frame.pack(fill=tk.X, padx=12, pady=8)

        # Repositório URL / Caminho
        ttk.Label(input_frame, text="Repositório (Local ou URL):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.repo_entry = ttk.Entry(input_frame, width=55)
        self.repo_entry.grid(row=0, column=1, padx=6, pady=2, sticky=tk.EW)

        self.btn_browse = ttk.Button(input_frame, text="Selecionar Pasta...", command=self._browse_folder)
        self.btn_browse.grid(row=0, column=2, padx=4, pady=2)

        # Arquivo de Turma / Roster
        ttk.Label(input_frame, text="Arquivo Turma (JSON opcional):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.roster_entry = ttk.Entry(input_frame, width=55)
        self.roster_entry.grid(row=1, column=1, padx=6, pady=2, sticky=tk.EW)

        self.btn_browse_roster = ttk.Button(input_frame, text="Selecionar JSON...", command=self._browse_roster)
        self.btn_browse_roster.grid(row=1, column=2, padx=4, pady=2)

        # Opções da análise
        self.blame_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            input_frame,
            text="Calcular autoria sobrevivente (git blame — mais lento em repositórios grandes)",
            variable=self.blame_var,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))

        # Botão de Execução
        self.btn_run = ttk.Button(input_frame, text="Analisar Repositório",
                                  command=self._start_analysis, style="Accent.TButton")
        self.btn_run.grid(row=0, column=3, rowspan=3, padx=10, sticky=tk.EW)

        input_frame.columnconfigure(1, weight=1)

        # 2. Painel de Status / Barra de Progresso
        self.status_var = tk.StringVar(value="Pronto para analisar.")
        self.status_bar = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.progress_bar = ttk.Progressbar(self, mode="indeterminate")

        # 3. Painel de Métricas Gerais (Cards)
        self.stats_frame = ttk.Frame(self, padding=8)
        self.stats_frame.pack(fill=tk.X, padx=12)

        self.card_total_commits = self._create_metric_card(self.stats_frame, "Commits Totais", "—", 0)
        self.card_main_commits = self._create_metric_card(self.stats_frame, "Commits na Main", "—", 1)
        self.card_suspicious = self._create_metric_card(self.stats_frame, "Mensagens Vagas", "—", 2)
        self.card_authors_count = self._create_metric_card(self.stats_frame, "Integrantes", "—", 3)
        self.card_gini = self._create_metric_card(self.stats_frame, "Desigualdade (Gini)", "—", 4)

        # 4. Área Central com Abas
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        results_tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(results_tab, text=" Resultados ")

        participation_tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(participation_tab, text=" Participação ")

        self.activity_panel = ActivityPanel(self.notebook)
        self.notebook.add(self.activity_panel, text=" Atividade ")

        branches_tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(branches_tab, text=" Branches ")

        commits_tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(commits_tab, text=" Commits ")

        self.log_panel = LogPanel(self.notebook, self.log_handler)
        self.notebook.add(self.log_panel, text=" Log ")

        self._build_participation_tab(participation_tab)
        self._build_branches_tab(branches_tab)
        self._build_commits_tab(commits_tab)

        self.results_summary = tk.StringVar(value="Execute uma análise para ver os resultados.")
        ttk.Label(results_tab, textvariable=self.results_summary, style="StatTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 6)
        )
        main_paned = ttk.PanedWindow(results_tab, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # Painel Alunos (Tabela Principal)
        table_frame = ttk.Frame(main_paned)
        main_paned.add(table_frame, weight=4)

        columns = ("name", "commits", "adds", "dels", "net", "conv_rate", "main_rate")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("name", text="Aluno / Autor", command=lambda: self._sort_column("name"))
        self.tree.heading("commits", text="Commits", command=lambda: self._sort_column("commits", is_num=True))
        self.tree.heading("adds", text="+ Linhas", command=lambda: self._sort_column("adds", is_num=True))
        self.tree.heading("dels", text="- Linhas", command=lambda: self._sort_column("dels", is_num=True))
        self.tree.heading("net", text="Saldo", command=lambda: self._sort_column("net", is_num=True))
        self.tree.heading("conv_rate", text="% Conv. Commits", command=lambda: self._sort_column("conv_rate", is_num=True))
        self.tree.heading("main_rate", text="Direto na Main", command=lambda: self._sort_column("main_rate", is_num=True))

        self.tree.column("name", width=190, anchor=tk.W)
        self.tree.column("commits", width=75, anchor=tk.CENTER)
        self.tree.column("adds", width=75, anchor=tk.E)
        self.tree.column("dels", width=75, anchor=tk.E)
        self.tree.column("net", width=75, anchor=tk.E)
        self.tree.column("conv_rate", width=110, anchor=tk.CENTER)
        self.tree.column("main_rate", width=95, anchor=tk.CENTER)

        tree_scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # Painel Lateral: Branches
        branch_frame = ttk.LabelFrame(main_paned, text=" Branches Detectadas ", padding=6)
        main_paned.add(branch_frame, weight=1)

        self.branch_listbox = tk.Listbox(
            branch_frame, font="TkDefaultFont", bd=0, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
            background=theme.SURFACE, foreground=theme.TEXT,
            selectbackground=theme.ACCENT, selectforeground=theme.SURFACE,
        )
        branch_scroll = ttk.Scrollbar(branch_frame, orient=tk.VERTICAL, command=self.branch_listbox.yview)
        self.branch_listbox.configure(yscrollcommand=branch_scroll.set)

        self.branch_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        branch_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Double-1>", self._on_author_double_click)
        self.branch_listbox.bind("<Double-1>", self._on_branch_double_click)
        self._configure_rows(self.tree)

    def _build_participation_tab(self, parent: ttk.Frame):
        self.participation_summary = tk.StringVar(value="Execute uma análise para ver a participação.")
        ttk.Label(parent, textvariable=self.participation_summary, style="StatTitle.TLabel").pack(
            side=tk.TOP, anchor=tk.W, pady=(0, 6)
        )

        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "commit_share", "churn_share", "alive", "alive_share", "identity")
        self.part_tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        headings = {
            "name": "Aluno / Autor",
            "commit_share": "% dos Commits",
            "churn_share": "% das Linhas",
            "alive": "Linhas Vivas",
            "alive_share": "% do Código Atual",
            "identity": "Identidade",
        }
        for col, text in headings.items():
            self.part_tree.heading(col, text=text)
        self.part_tree.column("name", width=190, anchor=tk.W)
        self.part_tree.column("commit_share", width=100, anchor=tk.CENTER)
        self.part_tree.column("churn_share", width=100, anchor=tk.CENTER)
        self.part_tree.column("alive", width=100, anchor=tk.E)
        self.part_tree.column("alive_share", width=120, anchor=tk.CENTER)
        self.part_tree.column("identity", width=220, anchor=tk.W)
        self._configure_rows(self.part_tree)

        scroll_y = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.part_tree.yview)
        self.part_tree.configure(yscrollcommand=scroll_y.set)
        self.part_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.part_tree.bind("<Double-1>", self._on_participation_double_click)

    def _build_branches_tab(self, parent: ttk.Frame):
        self.branches_summary = tk.StringVar(value="Execute uma análise para ver as branches.")
        ttk.Label(parent, textvariable=self.branches_summary, style="StatTitle.TLabel").pack(anchor=tk.W, pady=(0, 6))
        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)
        columns = ("name", "kind", "status", "date", "author")
        self.branches_tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        headings = {
            "name": "Branch",
            "kind": "Tipo",
            "status": "Estado",
            "date": "Último commit",
            "author": "Autor",
        }
        for col, text in headings.items():
            self.branches_tree.heading(col, text=text)
        self.branches_tree.column("name", width=260, anchor=tk.W)
        self.branches_tree.column("kind", width=70, anchor=tk.CENTER, stretch=False)
        self.branches_tree.column("status", width=100, anchor=tk.CENTER, stretch=False)
        self.branches_tree.column("date", width=130, anchor=tk.W, stretch=False)
        self.branches_tree.column("author", width=170, anchor=tk.W)
        self._configure_rows(self.branches_tree)
        self.branches_tree.tag_configure("abandonada", foreground=theme.WARNING_TEXT)
        self.branches_tree.tag_configure("mesclada", foreground=theme.SUCCESS_TEXT)

        scroll_y = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.branches_tree.yview)
        self.branches_tree.configure(yscrollcommand=scroll_y.set)
        self.branches_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.branches_tree.bind("<Double-1>", self._on_branches_tab_double_click)

    def _build_commits_tab(self, parent: ttk.Frame):
        self.commits_summary = tk.StringVar(value="Execute uma análise para ver os commits.")
        ttk.Label(parent, textvariable=self.commits_summary, style="StatTitle.TLabel").pack(anchor=tk.W, pady=(0, 6))
        body = ttk.Frame(parent)
        body.pack(fill=tk.BOTH, expand=True)
        columns = ("hash", "author", "date", "message")
        self.commits_tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        self.commits_tree.heading("hash", text="Commit")
        self.commits_tree.heading("author", text="Aluno / Autor")
        self.commits_tree.heading("date", text="Data")
        self.commits_tree.heading("message", text="Mensagem")

        self.commits_tree.column("hash", width=80, anchor=tk.W, stretch=False)
        self.commits_tree.column("author", width=170, anchor=tk.W, stretch=False)
        self.commits_tree.column("date", width=130, anchor=tk.W, stretch=False)
        self.commits_tree.column("message", width=450, anchor=tk.W)

        self._configure_rows(self.commits_tree)
        scroll_y = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.commits_tree.yview)
        self.commits_tree.configure(yscrollcommand=scroll_y.set)
        self.commits_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.commits_tree.bind("<Double-1>", self._on_commit_double_click)

    @staticmethod
    def _configure_rows(tree: ttk.Treeview):
        tree.tag_configure("stripe", background=theme.STRIPE)
        tree.tag_configure("flagged", background=theme.WARNING_BACKGROUND)

    def _open_in_browser(self, url_builder):
        """Abre no navegador a URL gerada por url_builder(links, report)."""
        report = self.current_report
        if report is None:
            return
        if report.links is None:
            messagebox.showinfo("Links indisponíveis", NO_LINKS_HINT)
            return
        url = url_builder(report.links, report)
        logger.info("Abrindo no navegador: %s", url)
        if not webbrowser.open(url):
            logger.warning("Não foi possível abrir o navegador para: %s", url)
            messagebox.showwarning("Navegador", f"Não foi possível abrir o navegador.\n\n{url}")

    def _clicked_row(self, tree: ttk.Treeview, event) -> Optional[str]:
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        return tree.identify_row(event.y) or None

    def _open_author(self, author: AuthorStats):
        email = min(author.emails) if author.emails else None
        self._open_in_browser(
            lambda links, report: links.author_url(author.canonical_name, email, report.default_branch)
        )

    def _on_author_double_click(self, event):
        iid = self._clicked_row(self.tree, event)
        author = self._row_authors.get(iid) if iid else None
        if author is not None:
            self._open_author(author)

    def _on_participation_double_click(self, event):
        iid = self._clicked_row(self.part_tree, event)
        author = self._part_row_authors.get(iid) if iid else None
        if author is not None:
            self._open_author(author)

    def _open_branch(self, branch: BranchReport):
        self._open_in_browser(lambda links, _report: links.branch_url(branch.name))

    def _on_branch_double_click(self, event):
        report = self.current_report
        selection = self.branch_listbox.nearest(event.y)
        if report is None or not (0 <= selection < len(report.branches)):
            return
        self._open_branch(report.branches[selection])

    def _on_branches_tab_double_click(self, event):
        report = self.current_report
        iid = self._clicked_row(self.branches_tree, event)
        if report is None or iid is None:
            return
        self._open_branch(report.branches[int(iid)])

    def _on_commit_double_click(self, event):
        commit_hash = self._clicked_row(self.commits_tree, event)
        if commit_hash:
            self._open_in_browser(lambda links, _report: links.commit_url(commit_hash))

    def _create_metric_card(self, parent: ttk.Frame, title: str, value: str, col: int) -> tk.StringVar:
        var = tk.StringVar(value=value)
        card = ttk.Frame(parent, style="Card.TFrame", padding=(10, 8))
        card.grid(row=0, column=col, sticky=tk.NSEW, padx=4)
        parent.columnconfigure(col, weight=1)

        lbl_val = ttk.Label(card, textvariable=var, style="StatValue.TLabel", anchor=tk.CENTER)
        lbl_val.pack()
        lbl_title = ttk.Label(card, text=title, style="CardTitle.TLabel", anchor=tk.CENTER)
        lbl_title.pack()
        return var

    def _browse_folder(self):
        selected = filedialog.askdirectory(title="Selecione a pasta do repositório Git")
        if selected:
            self.repo_entry.delete(0, tk.END)
            self.repo_entry.insert(0, selected)

    def _browse_roster(self):
        selected = filedialog.askopenfilename(
            title="Selecione o arquivo de turma JSON",
            filetypes=[("Arquivos JSON", "*.json"), ("Todos os arquivos", "*.*")]
        )
        if selected:
            self.roster_entry.delete(0, tk.END)
            self.roster_entry.insert(0, selected)

    def _set_ui_busy(self, busy: bool):
        if busy:
            self.btn_run.config(state=tk.DISABLED)
            self.progress_bar.pack(side=tk.BOTTOM, fill=tk.X, before=self.status_bar)
            self.progress_bar.start(10)
        else:
            self.btn_run.config(state=tk.NORMAL)
            self.progress_bar.stop()
            self.progress_bar.pack_forget()

    def _start_analysis(self):
        repo_target = self.repo_entry.get().strip()
        roster_target = self.roster_entry.get().strip()

        if not repo_target:
            messagebox.showwarning("Atenção", "Por favor, indique um repositório Git (local ou URL).")
            return

        roster_path = Path(roster_target) if roster_target else None
        include_blame = self.blame_var.get()

        logger.info("Análise solicitada para: %s", repo_target)
        self._set_ui_busy(True)
        self.status_var.set("Analisando repositório... Isso pode levar alguns segundos.")

        # Executa a coleta em background para manter a UI responsiva
        threading.Thread(
            target=self._run_analysis_worker,
            args=(repo_target, roster_path, include_blame),
            daemon=True
        ).start()

    def _run_analysis_worker(self, repo_target: str, roster_path: Optional[Path], include_blame: bool):
        try:
            analyzer = RepositoryAnalyzer(repo_target, roster_path=roster_path, include_blame=include_blame)
            report = analyzer.analyze()
            self.after(0, self._render_results, report)
        except Exception as e:
            logger.exception("A análise falhou")
            self.after(0, self._handle_error, str(e))

    def _handle_error(self, err_msg: str):
        self._set_ui_busy(False)
        self.status_var.set("Erro na análise.")
        messagebox.showerror("Erro ao Analisar", f"Ocorreu um erro:\n\n{err_msg}")

    def _render_results(self, report: RepositoryReport):
        self.current_report = report
        self._set_ui_busy(False)
        self.status_var.set(f"Análise concluída com sucesso. {LINK_HINT if report.links else NO_LINKS_HINT}")

        cursor = "hand2" if report.links else ""
        for widget in (self.tree, self.branch_listbox, self.commits_tree, self.part_tree, self.branches_tree):
            widget.configure(cursor=cursor)

        # Atualiza Cards
        self.card_total_commits.set(str(report.total_commits))
        self.card_main_commits.set(str(report.direct_main_commits_count))
        self.card_suspicious.set(str(report.suspicious_messages_count))
        self.card_authors_count.set(str(len(report.authors)))
        concentration = report.concentration
        self.card_gini.set(f"{concentration.gini_commits:.2f}" if concentration else "—")

        sorted_authors = sorted(report.authors.values(), key=lambda a: a.total_commits, reverse=True)
        self.results_summary.set(
            "Identidades destacadas em amarelo requerem atenção; veja detalhes em Participação."
            if sorted_authors else "Nenhum autor encontrado nesta análise."
        )
        self.branches_summary.set(
            f"{len(report.branches)} branch(es) encontrada(s)." if report.branches
            else "Nenhuma branch encontrada nesta análise."
        )
        self.commits_summary.set(
            f"{len(report.commits)} commit(s) encontrado(s)." if report.commits
            else "Nenhum commit encontrado nesta análise."
        )

        # Limpa e popula Treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_authors.clear()

        for index, author in enumerate(sorted_authors):
            iid = self.tree.insert(
                "",
                tk.END,
                values=(
                    author.canonical_name,
                    author.total_commits,
                    f"+{author.additions:,}",
                    f"-{author.deletions:,}",
                    f"{author.net_lines:,}",
                    f"{author.conventional_adherence_rate:.1f}%",
                    author.direct_main_commits,
                ),
                tags=("flagged",) if author.identity_note else (("stripe",) if index % 2 else ()),
            )
            self._row_authors[iid] = author

        self._render_participation(report, sorted_authors)
        self.activity_panel.show(report.activity, [a.canonical_name for a in sorted_authors], analyzed=True)
        self._render_branches(report)

        # Atualiza lista de Commits (o hash completo é o identificador da linha)
        self.commits_tree.delete(*self.commits_tree.get_children())
        for index, commit in enumerate(report.commits):
            self.commits_tree.insert(
                "",
                tk.END,
                iid=commit.hash,
                values=(
                    commit.hash[:8],
                    commit.author,
                    commit.timestamp.strftime("%Y-%m-%d %H:%M"),
                    commit.message,
                ),
                tags=("stripe",) if index % 2 else (),
            )

    def _render_participation(self, report: RepositoryReport, sorted_authors):
        concentration = report.concentration
        if concentration:
            self.participation_summary.set(
                f"Gini de commits: {concentration.gini_commits:.2f} · Gini de linhas: {concentration.gini_churn:.2f} "
                f"(0 = trabalho igualmente distribuído, 1 = concentrado em uma pessoa). "
                f"Maior participação: {concentration.top_author} ({concentration.top_share * 100:.0f}% dos commits)."
            )
        else:
            self.participation_summary.set("Nenhum dado de participação disponível nesta análise.")

        self.part_tree.delete(*self.part_tree.get_children())
        self._part_row_authors.clear()
        for index, author in enumerate(sorted_authors):
            commit_share = concentration.commit_share.get(author.canonical_name, 0.0) if concentration else 0.0
            churn_share = concentration.churn_share.get(author.canonical_name, 0.0) if concentration else 0.0
            if report.blame_ref and report.blame_total_lines:
                alive = f"{author.surviving_lines:,}"
                alive_share = f"{author.surviving_lines / report.blame_total_lines * 100:.1f}%"
            else:
                alive, alive_share = "—", "—"

            iid = self.part_tree.insert(
                "",
                tk.END,
                values=(
                    author.canonical_name,
                    f"{commit_share * 100:.1f}%",
                    f"{churn_share * 100:.1f}%",
                    alive,
                    alive_share,
                    author.identity_note or "OK",
                ),
                tags=("flagged",) if author.identity_note else (("stripe",) if index % 2 else ()),
            )
            self._part_row_authors[iid] = author

    def _render_branches(self, report: RepositoryReport):
        self.branch_listbox.delete(0, tk.END)
        self.branches_tree.delete(*self.branches_tree.get_children())

        for index, branch in enumerate(report.branches):
            prefix = "[remota] " if branch.is_remote else "[local] "
            suffix = f" · {branch.status}" if branch.status else ""
            self.branch_listbox.insert(tk.END, f"{prefix}{branch.name}{suffix}")

            when = branch.last_commit_date.strftime("%Y-%m-%d %H:%M") if branch.last_commit_date else "—"
            self.branches_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    branch.name,
                    "remota" if branch.is_remote else "local",
                    branch.status or "—",
                    when,
                    branch.last_author or "—",
                ),
                tags=(("stripe",) if index % 2 else ())
                     + ((branch.status,) if branch.status in ("abandonada", "mesclada") else ()),
            )

    def _sort_column(self, col: str, is_num: bool = False):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def convert(val):
            clean = val.replace("%", "").replace("+", "").replace("-", "").replace(",", "")
            return float(clean) if is_num else clean.lower()

        items.sort(key=lambda t: convert(t[0]), reverse=self._sort_reverse)
        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)
            tags = tuple(tag for tag in self.tree.item(k, "tags") if tag != "stripe")
            if index % 2 and "flagged" not in tags:
                tags += ("stripe",)
            self.tree.item(k, tags=tags)

        self._sort_reverse = not self._sort_reverse
        