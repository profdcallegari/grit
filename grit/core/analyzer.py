import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Generator, List, Optional, Set, Tuple

from .git_client import GitClient
from .metrics import build_activity, compute_concentration, week_start
from .models import AttentionConfig, AuthorStats, BlameDetails, BranchReport, CommitInfo, CommitSummary, FileHotspot, RepositoryReport
from .attention import AttentionAnalyzer, redact_sensitive
from .normalizer import StudentNormalizer
from .progress import Progress

logger = logging.getLogger(__name__)


def _module_for_path(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "."


MAX_BLAME_FILES = 1500
BLAME_WORKERS = 4
ABANDONED_AFTER = timedelta(days=30)  # relativo ao commit mais recente do repositório
CONCENTRATION_WARNING = 0.6
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar",
    ".7z", ".rar", ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib", ".bin", ".woff",
    ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".pyc", ".o", ".a",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".sqlite", ".db",
}


class RepositoryAnalyzer:
    CONVENTIONAL_REGEX = re.compile(
        r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-zA-Z0-9_\-\./]+\))?!?: .+"
    )
    SUSPICIOUS_MESSAGES = {
        "update", "updates", "ajustes", "corrigindo bug", "fix", "fix bug",
        "commit", "changes", "test", "teste", "wip", "subindo alteracoes", "."
    }
    IGNORED_PATHS = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "poetry.lock", "Pipfile.lock", "composer.lock"
    }

    def __init__(
        self,
        target_path_or_url: str,
        roster_path: Optional[Path] = None,
        include_blame: bool = True,
        attention_config: Optional[AttentionConfig] = None,
        progress_id: Optional[object] = None,
    ):
        if attention_config is not None:
            if not isinstance(attention_config, AttentionConfig):
                raise ValueError("attention_config deve ser AttentionConfig ou None")
            attention_config.validate()
        self.attention_config = attention_config
        self.include_blame = include_blame
        self.progress_id = progress_id
        self.client = GitClient(target_path_or_url, progress_id=progress_id)
        raw_mailmap = self.client.get_raw_mailmap()
        if roster_path:
            logger.info("Arquivo de turma: %s", roster_path)
        self.normalizer = StudentNormalizer(raw_mailmap=raw_mailmap, roster_file=roster_path)

    def _is_suspicious_message(self, message: str) -> bool:
        clean = message.strip().lower()
        return clean in self.SUSPICIOUS_MESSAGES or len(clean) <= 3

    def _should_ignore_file(self, filepath: str) -> bool:
        path_obj = Path(filepath)
        if path_obj.name in self.IGNORED_PATHS:
            return True
        # Ignora dependências embutidas acidentalmente no commit
        parts = set(path_obj.parts)
        if parts.intersection({"node_modules", "vendor", ".venv", "venv", "__pycache__"}):
            return True
        return False

    def _track_identity(self, author: AuthorStats, email: str, warned: Set[str]):
        status = self.normalizer.classify_email(email)
        if status == "ok":
            return
        if status == "generic":
            author.generic_emails.add(email)
        else:
            author.unmapped_emails.add(email)
        if email not in warned:
            warned.add(email)
            reason = "e-mail genérico" if status == "generic" else "fora do roster/.mailmap"
            logger.warning("Identidade não confiável: %s <%s> (%s).", author.canonical_name, email, reason)

    def _analyze_branches(self, default_branch: str) -> List[BranchReport]:
        logger.info("Analisando branches (mesclagem e última atividade)...")
        default_ref = self.client.resolve_ref(default_branch)
        details = self.client.get_branch_details()
        try:
            merged = self.client.get_merged_branches(default_ref)
        except Exception as e:
            merged = set()
            logger.warning("Não foi possível calcular branches mescladas em '%s' (%s).", default_ref, e)

        dates = [d.date for d in details.values() if d.date is not None]
        reference_date = max(dates) if dates else None
        default_names = {default_branch, f"origin/{default_branch}"}

        reports: List[BranchReport] = []
        for bname, is_remote in self.client.get_branches():
            detail = details.get(bname)
            is_merged = bname in merged
            if bname in default_names:
                status = "principal"
            elif is_merged:
                status = "mesclada"
            elif (
                detail and detail.date and reference_date
                and reference_date - detail.date > ABANDONED_AFTER
            ):
                status = "abandonada"
            else:
                status = "ativa"

            reports.append(
                BranchReport(
                    name=bname,
                    is_remote=is_remote,
                    is_merged=is_merged,
                    last_commit_hash=detail.commit_hash if detail else "",
                    last_commit_date=detail.date if detail else None,
                    last_author=detail.author if detail else "",
                    status=status,
                )
            )
            logger.debug("Branch %s: %s", bname, status)

        counts = {s: sum(1 for r in reports if r.status == s) for s in ("principal", "mesclada", "ativa", "abandonada")}
        logger.info(
            "Branches: %d mescladas, %d ativas, %d abandonadas (sem commits há mais de %d dias do último commit do repositório).",
            counts["mesclada"], counts["ativa"], counts["abandonada"], ABANDONED_AFTER.days,
        )
        return reports

    def _compute_surviving_authorship(
        self, authors: Dict[str, AuthorStats], default_branch: str
    ) -> Tuple[str, int, BlameDetails]:
        """Conta, via git blame, quantas linhas atuais da branch padrão pertencem a cada autor."""
        ref = self.client.resolve_ref(default_branch)
        files = [
            f for f in self.client.list_files(ref)
            if not self._should_ignore_file(f) and Path(f).suffix.lower() not in BINARY_EXTENSIONS
        ]
        eligible_files = len(files)
        if eligible_files > MAX_BLAME_FILES:
            logger.warning(
                "%d arquivos elegíveis; analisando apenas os primeiros %d para limitar o tempo.",
                len(files), MAX_BLAME_FILES,
            )
            files = files[:MAX_BLAME_FILES]

        if self.client.temp_dir:
            logger.warning("Repositório clonado sem blobs (--filter=blob:none): o git blame pode ser lento.")
        logger.info("Autoria sobrevivente: git blame em %d arquivos de '%s'.", len(files), ref)

        totals: Dict[str, int] = {}
        modules: Dict[str, Dict[str, int]] = {}
        skipped = 0
        with Progress(logger, "Autoria sobrevivente", "arquivos processados",
                      total=len(files), run_id=self.progress_id) as progress, \
                ThreadPoolExecutor(max_workers=BLAME_WORKERS) as pool:
            results = pool.map(lambda path: self.client.blame_authors(ref, path), files)
            for done, (path, result) in enumerate(zip(files, results), start=1):
                if result is None:
                    skipped += 1
                else:
                    module = _module_for_path(path)
                    module_totals = modules.setdefault(module, {})
                    for (name, email), lines in result.items():
                        canonical, _ = self.normalizer.get_canonical_identity(name, email)
                        totals[canonical] = totals.get(canonical, 0) + lines
                        module_totals[canonical] = module_totals.get(canonical, 0) + lines
                progress.update(done)

        total_lines = sum(totals.values())
        for name, lines in totals.items():
            author = authors.get(name)
            if author is None:
                logger.debug("Autor do blame ausente do histórico: %s (%d linhas).", name, lines)
                continue
            author.surviving_lines = lines

        logger.info(
            "Autoria sobrevivente concluída: %d linhas em %d arquivos (%d ignorados por erro).",
            total_lines, len(files) - skipped, skipped,
        )
        return ref, total_lines, BlameDetails(
            modules=modules,
            eligible_files=eligible_files,
            analyzed_files=len(files) - skipped,
            failed_files=skipped,
        )

    def analyze(self) -> RepositoryReport:
        started = time.perf_counter()
        commits: Optional[Generator[CommitInfo, None, None]] = None
        try:
            default_branch = self.client.get_default_branch()
            authors: Dict[str, AuthorStats] = {}
            total_commits = 0
            suspicious_count = 0
            direct_main_commits = 0
            commit_summaries: List[CommitSummary] = []
            weekly_changes: Dict[date, Tuple[int, int]] = {}
            hotspots: Dict[str, FileHotspot] = {}
            directory_activity: Dict[str, Dict[str, int]] = {}
            renamed_paths: Set[str] = set()
            rename_count = 0
            warned_emails: Set[str] = set()
            attention = AttentionAnalyzer(self.client, self.attention_config) if self.attention_config else None
            links = self.client.get_remote_links()
            try:
                history_shallow: Optional[bool] = self.client.is_shallow()
            except (subprocess.CalledProcessError, ValueError) as exc:
                history_shallow = None
                logger.warning("Não foi possível verificar se o histórico é raso: %s", exc)
            try:
                history_ref: Optional[str] = self.client.resolve_ref(default_branch)
                current_files: Optional[Set[str]] = set(self.client.list_files(history_ref))
            except subprocess.CalledProcessError as exc:
                history_ref = None
                current_files = None
                logger.warning("Não foi possível verificar arquivos na branch padrão: %s", exc)
            # Obtém commits acessíveis na branch default
            default_branch_commits: Set[str] = set()
            try:
                out = self.client._run_git(["rev-list", default_branch])
                default_branch_commits = set(out.splitlines())
                logger.info("%d commits alcançáveis a partir de '%s'.", len(default_branch_commits), default_branch)
            except Exception as e:
                logger.warning(
                    "Não foi possível listar commits de '%s' (%s); 'Commits na Main' ficará zerado.",
                    default_branch, e,
                )

            logger.info("Processando commits...")
            commits = self.client.stream_commits()
            for commit in commits:
                total_commits += 1
                canonical_name, email = self.normalizer.get_canonical_identity(
                    commit.author_name, commit.author_email
                )
                if attention is None:
                    logger.debug(
                        "%s | %s <%s> -> %s | %s",
                        commit.hash[:8], commit.author_name, commit.author_email,
                        canonical_name, commit.message,
                    )
                else:
                    attention.add_commit(commit, canonical_name)

                if canonical_name not in authors:
                    logger.debug("Novo autor identificado: %s", canonical_name)
                    authors[canonical_name] = AuthorStats(canonical_name=canonical_name)

                author = authors[canonical_name]
                commit_summaries.append(
                    CommitSummary(
                        hash=commit.hash,
                        author=canonical_name,
                        timestamp=commit.timestamp,
                        message=redact_sensitive(commit.message) if attention else commit.message,
                        is_merge=commit.is_merge,
                        parents=commit.parents,
                    )
                )
                author.emails.add(email)
                self._track_identity(author, email, warned_emails)
                author.total_commits += 1
                author.commit_dates.append(commit.timestamp)

                # Heurística de mensagem
                if self.CONVENTIONAL_REGEX.match(commit.message):
                    author.conventional_commits += 1
                if self._is_suspicious_message(commit.message):
                    suspicious_count += 1

                # Commits na main
                if commit.hash in default_branch_commits and not commit.is_merge:
                    author.direct_main_commits += 1
                    direct_main_commits += 1

                # Métricas de arquivos e linhas (churn)
                additions = deletions = 0
                touched_in_commit: Set[str] = set()
                touched_directories: Set[str] = set()
                for file_change in commit.files:
                    if self._should_ignore_file(file_change.filepath):
                        continue
                    author.additions += file_change.additions
                    author.deletions += file_change.deletions
                    author.files_touched.add(file_change.filepath)
                    additions += file_change.additions
                    deletions += file_change.deletions
                    hotspot = hotspots.setdefault(file_change.filepath, FileHotspot(path=file_change.filepath))
                    hotspot.additions += file_change.additions
                    hotspot.deletions += file_change.deletions
                    hotspot.authors.add(canonical_name)
                    touched_directories.add(_module_for_path(file_change.filepath))
                    if file_change.previous_path is not None:
                        rename_count += 1
                        renamed_paths.update((file_change.previous_path, file_change.filepath))
                    if file_change.filepath not in touched_in_commit:
                        hotspot.commits += 1
                        touched_in_commit.add(file_change.filepath)

                author_directories = directory_activity.setdefault(canonical_name, {})
                for directory in touched_directories:
                    author_directories[directory] = author_directories.get(directory, 0) + 1

                week = week_start(commit.timestamp.date())
                week_additions, week_deletions = weekly_changes.get(week, (0, 0))
                weekly_changes[week] = (week_additions + additions, week_deletions + deletions)

            with Progress(logger, "Análise de branches", run_id=self.progress_id):
                branch_reports = self._analyze_branches(default_branch)

            blame_ref: Optional[str] = None
            blame_total = 0
            blame_status = "disabled"
            blame_details = None
            if self.include_blame:
                try:
                    blame_ref, blame_total, blame_details = self._compute_surviving_authorship(authors, default_branch)
                    blame_status = "completed"
                except Exception:
                    blame_status = "failed"
                    logger.exception("Falha ao calcular a autoria sobrevivente; seguindo sem ela.")
            else:
                logger.info("Autoria sobrevivente (git blame) desativada.")

            activity = build_activity(authors, weekly_changes)
            for path, hotspot in hotspots.items():
                hotspot.renamed = path in renamed_paths
                hotspot.present_in_default = path in current_files if current_files is not None else None
            concentration = compute_concentration(authors)
            logger.info(
                "Concentração: Gini de commits %.2f, Gini de linhas %.2f; maior participação: %s (%.0f%% dos commits).",
                concentration.gini_commits, concentration.gini_churn,
                concentration.top_author or "-", concentration.top_share * 100,
            )
            if len(authors) > 1 and concentration.top_share >= CONCENTRATION_WARNING:
                logger.warning(
                    "Trabalho concentrado: %s fez %.0f%% dos commits.",
                    concentration.top_author, concentration.top_share * 100,
                )
            flagged = [a for a in authors.values() if a.identity_note]
            if flagged:
                logger.warning(
                    "%d autor(es) com identidade não confiável: %s",
                    len(flagged), ", ".join(a.canonical_name for a in flagged),
                )

            attention_report = None
            if attention:
                with Progress(logger, "Análise de pontos de atenção", run_id=self.progress_id):
                    attention_report = attention.finish(history_ref, history_shallow, branch_reports)

            logger.info(
                "Análise concluída em %.1fs: %d commits, %d autores, %d mensagens vagas, %d commits diretos na main.",
                time.perf_counter() - started, total_commits, len(authors),
                suspicious_count, direct_main_commits,
            )
            for a in sorted(authors.values(), key=lambda x: x.total_commits, reverse=True):
                logger.info(
                    "  %s: %d commits (+%d / -%d linhas, %d e-mail(s))",
                    a.canonical_name, a.total_commits, a.additions, a.deletions, len(a.emails),
                )

            return RepositoryReport(
                total_commits=total_commits,
                authors=authors,
                branches=branch_reports,
                direct_main_commits_count=direct_main_commits,
                suspicious_messages_count=suspicious_count,
                commits=commit_summaries,
                default_branch=default_branch,
                links=links,
                activity=activity,
                concentration=concentration,
                blame_ref=blame_ref,
                blame_total_lines=blame_total,
                blame_status=blame_status,
                blame_details=blame_details,
                hotspots=sorted(hotspots.values(), key=lambda item: (-item.churn, -item.commits, item.path)),
                directory_activity=directory_activity,
                history_shallow=history_shallow,
                history_ref=history_ref,
                history_renames=rename_count,
                attention=attention_report,
            )
        finally:
            try:
                if commits is not None:
                    commits.close()
            finally:
                self.client.cleanup()

            