"""Heurísticas limitadas; evidências nunca incluem conteúdo dos blobs."""

import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from .git_client import GitClient
from .models import AttentionConfig, AttentionFinding, AttentionReport, BranchReport, CommitInfo
from .normalizer import GENERIC_EMAIL

MAX_TREE_BYTES = 4 * 1024 * 1024
MAX_TREE_ENTRIES = 10000
MAX_SCAN_FILES = 250
MAX_SCAN_FILE_BYTES = 256 * 1024
MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_BRANCHES = 100
MAX_TRACKED_COMMITS = 20000
MAX_FINDINGS = 10000
MAX_ANALYSIS_SECONDS = 30
MAX_HISTORY_COMMITS = 100
MAX_HISTORY_RECORDS = 10000
MAX_HISTORY_DIFF_BYTES = 1024 * 1024
MAX_HISTORY_BLOBS = 250
MAX_HISTORY_BYTES = 8 * 1024 * 1024
GIT_TIMEOUT = 10
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".vue",
}
GENERATED_DIRS = {"node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"(?i)\b(?:api[_-]?key|secret[_-]?key|password|access[_-]?token)\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]"),
)
CONFLICT_PATTERN = re.compile(rb"(?m)^(?:<{7} |={7}\r?$|>{7} )")


def redact_sensitive(text: str) -> str:
    data = text.encode("utf-8", "replace")
    for pattern in SECRET_PATTERNS:
        data = pattern.sub(b"[REDACTED]", data)
    return data.decode("utf-8", "replace")


def is_generated_path(path: str) -> bool:
    parts = path.lower().split("/")
    return bool(set(parts[:-1]) & GENERATED_DIRS or parts[-1].endswith((".min.js", ".min.css", ".pyc")))


class AttentionAnalyzer:
    def __init__(self, client: GitClient, config: AttentionConfig):
        config.validate()
        self.client = client
        self.config = config
        self.findings: List[AttentionFinding] = []
        self.limitations = [
            "Histórico: commits alcançáveis pelas refs locais (--all); refs remotas ausentes ou desatualizadas não são buscadas.",
            "Datas são as datas locais registradas por autores/commits, não prova de tempo real ou causalidade.",
            "Renomeações são heurísticas; nomes antigos e histórico anterior podem não ser relacionados.",
            "Merges não são avaliados para arquivos, churn ou ausência de testes; diffs binários não informam churn.",
            "Testes são identificados por caminhos e nomes, não por execução ou cobertura; ausência no commit não prova falta de testes.",
            "Segredos, conflitos e arquivos gerados são heurísticas sobre conteúdo atual e histórico limitado; não são auditoria completa e valores nunca são exibidos.",
        ]
        self.commits: Dict[str, Tuple[CommitInfo, str, Set[str]]] = {}
        self.names: Dict[str, Set[str]] = {}
        self.emails: Dict[str, Set[str]] = {}
        self.deadline_total = 0
        self.deadline_near = 0
        self.invalid_dates = 0
        self.started = 0.0
        self.omitted_commits = 0
        self.omitted_findings = 0

    def _finding(self, category: str, kind: str, timestamp: Optional[datetime],
                 author: str, evidence: str, reference: str, details: str) -> None:
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(AttentionFinding(
                category, kind, timestamp, redact_sensitive(author), redact_sensitive(evidence),
                redact_sensitive(reference), redact_sensitive(details),
            ))
        else:
            self.omitted_findings += 1

    def _is_test(self, path: str) -> bool:
        parts = path.lower().split("/")
        for configured in self.config.test_paths:
            target = configured.lower().split("/")
            if any(parts[i:i + len(target)] == target for i in range(len(parts) - len(target) + 1)):
                return True
        name = parts[-1]
        return name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))

    def add_commit(self, commit: CommitInfo, author: str) -> None:
        paths = {f.filepath for f in commit.files}
        if len(self.commits) < MAX_TRACKED_COMMITS:
            self.commits[commit.hash] = (commit, author, paths)
        else:
            self.omitted_commits += 1
        email = commit.author_email.strip().lower()
        self.names.setdefault(commit.author_name.strip().casefold(), set()).add(email)
        self.emails.setdefault(email, set()).add(commit.author_name.strip().casefold())
        timestamp = commit.timestamp if commit.timestamp_valid else None
        if timestamp is None:
            self.invalid_dates += 1
        if GENERIC_EMAIL.search(email):
            self._finding("identidade", "generic_identity", timestamp, author,
                          "E-mail genérico ou placeholder", commit.hash, "Não identifica necessariamente uma pessoa.")
        if self.config.deadline is not None and timestamp is not None:
            self.deadline_total += 1
            days = (self.config.deadline - timestamp.date()).days
            if 0 <= days < self.config.deadline_days:
                self.deadline_near += 1
        if commit.is_merge:
            return
        churn = sum(f.additions + f.deletions for f in commit.files)
        directories = {str(PurePosixPath(path).parent) for path in paths}
        if len(paths) > self.config.max_commit_files:
            self._finding("commit", "large_commit_files", timestamp, author, f"{len(paths)} arquivos", commit.hash,
                          f"Limite: {self.config.max_commit_files}; inclui arquivos binários e renomeações.")
        if churn > self.config.max_commit_churn:
            self._finding("commit", "large_commit_churn", timestamp, author, f"{churn} linhas", commit.hash,
                          f"Adições mais remoções; limite: {self.config.max_commit_churn}.")
        if len(directories) > self.config.max_directories:
            self._finding("commit", "many_directories", timestamp, author, f"{len(directories)} diretórios", commit.hash,
                          f"Diretórios pais distintos; limite: {self.config.max_directories}.")
        if any(PurePosixPath(p).suffix.lower() in CODE_EXTENSIONS and not self._is_test(p) for p in paths):
            if not any(self._is_test(p) for p in paths):
                self._finding("testes", "code_without_tests", timestamp, author, "Código sem testes no mesmo commit",
                              commit.hash, "Heurística de caminhos; testes existentes não foram verificados.")
        if re.search(r"(?i)\b(?:revert|revers[aã]o|reverter)\b", commit.message):
            self._finding("commit", "possible_revert", timestamp, author, "Mensagem sugere reversão", commit.hash,
                          "A mensagem não comprova que uma mudança foi revertida.")

    def _git(self, args: List[str]) -> subprocess.CompletedProcess:
        remaining = MAX_ANALYSIS_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired("git", MAX_ANALYSIS_SECONDS)
        env = os.environ.copy()
        env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
        return subprocess.run(["git", "-C", str(self.client.repo_dir)] + args, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=min(GIT_TIMEOUT, remaining))

    def _partial_or_unknown(self) -> bool:
        try:
            config = self._git(["config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$"])
            if config.returncode not in (0, 1) or config.returncode == 0:
                return True
            pack = self._git(["rev-parse", "--git-path", "objects/pack"])
            if pack.returncode:
                return True
            from pathlib import Path
            path = Path(os.fsdecode(pack.stdout.strip()))
            if not path.is_absolute():
                path = self.client.repo_dir / path
            return any(path.glob("*.promisor"))
        except (OSError, subprocess.TimeoutExpired):
            return True

    def _bounded_git(self, args: List[str], limit: int) -> Tuple[bytes, bool, int]:
        remaining = MAX_ANALYSIS_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired("git", MAX_ANALYSIS_SECONDS)
        env = os.environ.copy()
        env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
        proc = subprocess.Popen(
            ["git", "-C", str(self.client.repo_dir)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
        assert proc.stdout is not None
        data = bytearray()

        def read_output() -> None:
            while len(data) <= limit:
                chunk = proc.stdout.read(min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            reader.join(min(GIT_TIMEOUT, remaining))
            if reader.is_alive():
                proc.kill()
                reader.join()
                raise subprocess.TimeoutExpired("git", GIT_TIMEOUT)
            truncated = len(data) > limit
            if truncated:
                proc.kill()
            proc.stdout.close()
            code = proc.wait(timeout=min(GIT_TIMEOUT, max(0.01, remaining)))
            return bytes(data[:limit]), truncated, code
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def _scan_history(self, shallow: Optional[bool]) -> None:
        if self._partial_or_unknown():
            self.limitations.append(
                "Segredos históricos indisponíveis: clone parcial/promisor ou configuração desconhecida; "
                "nenhum blob histórico é solicitado nem buscado pela rede."
            )
            return
        candidates = list(self.commits.values())
        total_commits = len(candidates) + self.omitted_commits
        processed = records = unique = scanned = skipped = failed = binary = byte_count = duplicates = 0
        seen: Set[bytes] = set()
        sizes: Dict[bytes, int] = {}
        reported_paths: Set[Tuple[bytes, str]] = set()
        partial = shallow is not False or self.omitted_commits > 0 or len(candidates) > MAX_HISTORY_COMMITS
        stopped = False
        for commit, author, _ in candidates[:MAX_HISTORY_COMMITS]:
            if time.monotonic() - self.started >= MAX_ANALYSIS_SECONDS:
                partial = True
                break
            try:
                raw, truncated, code = self._bounded_git([
                    "diff-tree", "--root", "-m", "-r", "--raw", "-z", "--no-renames",
                    "--no-abbrev", "--no-commit-id", commit.hash,
                ], MAX_HISTORY_DIFF_BYTES)
            except (OSError, subprocess.TimeoutExpired):
                failed += 1
                partial = True
                continue
            processed += 1
            if code and not truncated:
                failed += 1
                partial = True
                continue
            if truncated:
                partial = True
            tokens = raw.split(b"\0")
            if truncated:
                tokens.pop()
            if tokens and tokens[-1] == b"":
                tokens.pop()
            if len(tokens) % 2:
                tokens.pop()
                partial = True
            for index in range(0, len(tokens), 2):
                if records >= MAX_HISTORY_RECORDS or time.monotonic() - self.started >= MAX_ANALYSIS_SECONDS:
                    partial = stopped = True
                    break
                records += 1
                try:
                    header = tokens[index].split()
                    if len(header) != 5 or not header[0].startswith(b":"):
                        raise ValueError
                    old_mode, new_mode = header[0][1:], header[1]
                    old_oid, new_oid = header[2], header[3]
                    if any(not re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", oid)
                           for oid in (old_oid, new_oid)):
                        raise ValueError
                    path = tokens[index + 1].decode("utf-8", "replace")
                except ValueError:
                    failed += 1
                    partial = True
                    continue
                for mode, oid, version in ((old_mode, old_oid, "anterior"), (new_mode, new_oid, "posterior")):
                    if mode not in (b"100644", b"100755", b"120000") or not oid.strip(b"0"):
                        continue
                    location = f"Caminho: {path}; versão {version} do commit; blob {oid.decode('ascii')}."
                    path_key = (oid, path)
                    new_path = path_key not in reported_paths
                    if new_path:
                        reported_paths.add(path_key)
                        if is_generated_path(path):
                            self._finding(
                                "arquivo", "generated_file", commit.timestamp if commit.timestamp_valid else None,
                                "", "Caminho histórico sugere arquivo gerado ou dependência", commit.hash,
                                location + " Nome não comprova geração automática.",
                            )
                    if oid in seen:
                        duplicates += 1
                        if new_path and sizes.get(oid, -1) > self.config.max_file_bytes:
                            self._finding(
                                "arquivo", "large_file", commit.timestamp if commit.timestamp_valid else None,
                                "", f"{sizes[oid]} bytes", commit.hash,
                                location + f" Limite: {self.config.max_file_bytes} bytes.",
                            )
                        continue
                    if unique >= MAX_HISTORY_BLOBS:
                        partial = stopped = True
                        break
                    seen.add(oid)
                    unique += 1
                    try:
                        size_result = self._git(["cat-file", "-s", oid.decode("ascii")])
                        size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
                        if size < 0:
                            raise ValueError
                        sizes[oid] = size
                        if size > self.config.max_file_bytes:
                            self._finding(
                                "arquivo", "large_file", commit.timestamp if commit.timestamp_valid else None,
                                "", f"{size} bytes", commit.hash,
                                location + f" Limite: {self.config.max_file_bytes} bytes.",
                            )
                        if size > MAX_SCAN_FILE_BYTES or byte_count + size > MAX_HISTORY_BYTES:
                            skipped += 1
                            partial = True
                            continue
                        blob = self._git(["cat-file", "blob", oid.decode("ascii")])
                        if blob.returncode or len(blob.stdout) != size:
                            raise ValueError
                    except (OSError, subprocess.TimeoutExpired, ValueError):
                        failed += 1
                        partial = True
                        continue
                    scanned += 1
                    byte_count += size
                    if b"\0" in blob.stdout:
                        binary += 1
                        partial = True
                        continue
                    if CONFLICT_PATTERN.search(blob.stdout):
                        self._finding(
                            "arquivo", "conflict_marker", commit.timestamp if commit.timestamp_valid else None,
                            "", "Possível marcador de conflito em blob histórico", commit.hash,
                            location + " Padrão textual; pode ser exemplo.",
                        )
                    if any(pattern.search(blob.stdout) for pattern in SECRET_PATTERNS):
                        self._finding(
                            "segurança", "possible_secret",
                            commit.timestamp if commit.timestamp_valid else None, "",
                            "Padrão de possível segredo em blob histórico", commit.hash,
                            location + " "
                            "Valor omitido; pode ser falso positivo e o commit não identifica necessariamente a origem.",
                        )
                if stopped:
                    break
            if stopped:
                break
        self.limitations.append(
            f"Conteúdo histórico (segredos, conflitos, arquivos grandes/gerados) {'parcial' if partial else 'limitado por heurística'}: "
            f"{processed}/{total_commits} commits processados, {records} alterações, {unique} blobs distintos, "
            f"{scanned} lidos ({byte_count} bytes), {binary} binários, {skipped} ignorados por tamanho/orçamento de bytes, "
            f"{failed} erros, {duplicates} referências repetidas. "
            f"Limites: {MAX_HISTORY_COMMITS} commits, {MAX_HISTORY_RECORDS} alterações, "
            f"{MAX_HISTORY_DIFF_BYTES} bytes de diff por commit, {MAX_HISTORY_BLOBS} blobs distintos, "
            f"{MAX_SCAN_FILE_BYTES} bytes por blob e {MAX_HISTORY_BYTES} bytes no total; "
            f"{MAX_ANALYSIS_SECONDS}s compartilhados. Refs inacessíveis e blobs binários não são examinados; "
            "cada blob é lido uma vez, com o primeiro caminho/commit encontrado. "
            "Em merges, a versão anterior pode pertencer a qualquer pai."
        )

    def _scan_snapshot(self, ref: Optional[str]) -> None:
        if not ref:
            self.limitations.append("Arquivos atuais indisponíveis: referência padrão não resolvida.")
            return
        if self._partial_or_unknown():
            self.limitations.append("Conteúdo e árvore indisponíveis: clone parcial/promisor ou configuração desconhecida; nenhuma busca de blobs pela rede.")
            self.limitations.append("O histórico geral e o blame existentes podem provocar lazy-fetch em clones parciais; a inspeção de conteúdo de atenção não o faz.")
            return
        env = os.environ.copy()
        env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
        try:
            remaining = MAX_ANALYSIS_SECONDS - (time.monotonic() - self.started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired("git ls-tree", MAX_ANALYSIS_SECONDS)
            proc = subprocess.Popen(
                ["git", "-C", str(self.client.repo_dir), "ls-tree", "-r", "-l", "-z", ref],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            )
            assert proc.stdout is not None
            data = bytearray()
            def read_tree() -> None:
                while len(data) <= MAX_TREE_BYTES:
                    chunk = proc.stdout.read(min(65536, MAX_TREE_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            reader = threading.Thread(target=read_tree, daemon=True)
            reader.start()
            reader.join(min(GIT_TIMEOUT, remaining))
            if reader.is_alive():
                proc.kill()
                reader.join()
                raise subprocess.TimeoutExpired("git ls-tree", GIT_TIMEOUT)
            truncated = len(data) > MAX_TREE_BYTES
            if truncated:
                proc.kill()
            proc.stdout.close()
            rc = proc.wait(timeout=GIT_TIMEOUT)
            if rc and not truncated:
                self.limitations.append("Árvore de arquivos atual indisponível.")
                return
        except (OSError, subprocess.TimeoutExpired):
            if "proc" in locals():
                proc.kill()
                proc.wait()
            self.limitations.append("Árvore de arquivos atual indisponível por erro ou tempo excedido.")
            return
        records = bytes(data[:MAX_TREE_BYTES]).split(b"\0")
        if truncated:
            records.pop()
        records = [r for r in records if r]
        if len(records) > MAX_TREE_ENTRIES:
            truncated = True
            records = records[:MAX_TREE_ENTRIES]
        scanned = skipped = failed = total_bytes = blobs = 0
        for record in records:
            if time.monotonic() - self.started >= MAX_ANALYSIS_SECONDS:
                truncated = True
                break
            try:
                meta, raw_path = record.split(b"\t", 1)
                mode, objtype, oid, size_raw = meta.split()
                if objtype != b"blob":
                    continue
                size = int(size_raw)
                path = raw_path.decode("utf-8", "replace")
            except (ValueError, UnicodeError):
                failed += 1
                continue
            blobs += 1
            if size > self.config.max_file_bytes:
                self._finding("arquivo", "large_file", None, "", f"{size} bytes", path,
                              f"Blob atual; limite: {self.config.max_file_bytes} bytes.")
            if is_generated_path(path):
                self._finding("arquivo", "generated_file", None, "", "Caminho sugere arquivo gerado ou dependência",
                              path, "Nome não comprova geração automática.")
            if (scanned >= MAX_SCAN_FILES or size > MAX_SCAN_FILE_BYTES or total_bytes + size > MAX_SCAN_BYTES):
                skipped += 1
                continue
            try:
                result = self._git(["cat-file", "blob", oid.decode("ascii")])
                if result.returncode or len(result.stdout) != size:
                    failed += 1
                    continue
            except (OSError, subprocess.TimeoutExpired):
                failed += 1
                continue
            scanned += 1
            total_bytes += size
            content = result.stdout
            if b"\0" in content:
                skipped += 1
                continue
            if CONFLICT_PATTERN.search(content):
                self._finding("arquivo", "conflict_marker", None, "", "Possível marcador de conflito", path,
                              "Padrão textual no blob atual; pode ser exemplo.")
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                self._finding("segurança", "possible_secret", None, "", "Padrão de possível segredo", path,
                              "Conteúdo omitido; pode ser falso positivo. Consulte a cobertura histórica separada.")
        self.limitations.append(
            f"Árvore: {blobs} blobs listados{' (parcial por limite)' if truncated else ''}; "
            f"conteúdo: {scanned} lidos, {skipped} ignorados, {failed} erros; "
            f"limites: {MAX_TREE_ENTRIES} entradas/{MAX_TREE_BYTES} bytes de árvore, "
            f"{MAX_SCAN_FILES} blobs/{MAX_SCAN_FILE_BYTES} bytes por blob/{MAX_SCAN_BYTES} bytes no total; "
            f"{MAX_ANALYSIS_SECONDS}s compartilhados para branches e conteúdo. "
            "Binários e arquivos acima dos limites não são examinados."
        )

    def finish(self, ref: Optional[str], shallow: Optional[bool], branches: List[BranchReport]) -> AttentionReport:
        self.started = time.monotonic()
        if shallow is not False:
            self.limitations.append("Histórico raso ou estado desconhecido: análises históricas e de branches podem ser parciais.")
        if self.config.deadline is None:
            self.limitations.append("Concentração perto do prazo indisponível: prazo não informado.")
        elif not self.deadline_total:
            self.limitations.append("Concentração perto do prazo indisponível: nenhum commit com data válida.")
        elif self.deadline_total and self.deadline_near >= 2 and self.deadline_near / self.deadline_total >= 0.5:
            self._finding("prazo", "deadline_concentration", None, "", f"{self.deadline_near}/{self.deadline_total} commits",
                          "", f"Nos {self.config.deadline_days} dias até {self.config.deadline.isoformat()}, inclusive; datas locais.")
        if self.invalid_dates:
            self.limitations.append(f"{self.invalid_dates} commits sem data válida foram excluídos de análises temporais.")
        for commit, author, paths in self.commits.values():
            if commit.is_merge or len(commit.parents) != 1 or not commit.timestamp_valid:
                continue
            if not re.search(r"(?i)\b(?:fix|hotfix|corrige|corre[cç][aã]o|ajuste)\b", commit.message):
                continue
            parent = self.commits.get(commit.parents[0])
            if parent and parent[0].timestamp_valid and not parent[0].is_merge and paths.intersection(parent[2]):
                try:
                    seconds = (commit.timestamp - parent[0].timestamp).total_seconds()
                except TypeError:
                    continue
                if 0 <= seconds <= 86400:
                    self._finding("commit", "immediate_fix", commit.timestamp, author, "Mensagem de correção e arquivos em comum com o pai em até 24h",
                                  commit.hash, "Proximidade e mensagem não comprovam que o pai causou o problema.")
        for name, emails in self.names.items():
            if name and len(emails) > 1:
                self._finding("identidade", "multiple_emails", None, name, f"{len(emails)} e-mails para o mesmo nome",
                              "", "Pode representar aliases ou homônimos.")
        for email, names in self.emails.items():
            if email and len(names) > 1:
                self._finding("identidade", "shared_email", None, "", f"{len(names)} nomes para o mesmo e-mail",
                              "", "Pode representar aliases, conta compartilhada ou configuração.")
        self._scan_history(shallow)
        self._scan_snapshot(ref)
        self._branches(ref, shallow, branches)
        if self.omitted_commits:
            self.limitations.append(f"Correções imediatas parciais: {self.omitted_commits} commits excedem o limite de {MAX_TRACKED_COMMITS}.")
        if self.omitted_findings:
            self.limitations.append(f"Achados parciais: {self.omitted_findings} excedem o limite de {MAX_FINDINGS}.")
        return AttentionReport(self.findings, self.limitations)

    def _branches(self, ref: Optional[str], shallow: Optional[bool], branches: List[BranchReport]) -> None:
        self.limitations.append("Branches: somente refs locais conhecidas; inatividade não comprova abandono, e duração usa merge-base e datas de committer.")
        if not ref or shallow is not False or self._partial_or_unknown():
            self.limitations.append("Análise de duração e abandono de branches indisponível: referência, histórico completo ou objetos locais não confirmados.")
            return
        dates = [b.last_commit_date for b in branches if b.last_commit_date is not None]
        if not dates:
            self.limitations.append("Análise de branches indisponível: datas das refs ausentes.")
            return
        try:
            latest = max(dates)
        except TypeError:
            self.limitations.append("Análise de branches indisponível: datas incompatíveis.")
            return
        candidates = [b for b in branches if b.status != "principal" and b.last_commit_hash]
        if len(candidates) > MAX_BRANCHES:
            self.limitations.append(f"Análise de branches parcial: limite de {MAX_BRANCHES} refs.")
        failures = 0
        for branch in candidates[:MAX_BRANCHES]:
            if time.monotonic() - self.started >= MAX_ANALYSIS_SECONDS:
                self.limitations.append("Análise de branches parcial: limite de tempo atingido.")
                break
            try:
                base = self._git(["merge-base", ref, branch.last_commit_hash])
                if base.returncode or not base.stdout.strip():
                    failures += 1
                    continue
                base_hash = base.stdout.strip().decode("ascii")
                if base_hash == branch.last_commit_hash:
                    continue
                if branch.last_commit_date is None:
                    failures += 1
                    continue
                age = (latest - branch.last_commit_date).days
                if age > 30:
                    self._finding("branch", "possibly_abandoned", branch.last_commit_date, branch.last_author,
                                  f"Sem atividade por {age} dias em relação à ref mais recente", branch.name,
                                  "Branch não contida na referência padrão; inatividade não comprova abandono.")
                date_result = self._git(["show", "-s", "--format=%cI", base_hash])
                if date_result.returncode:
                    failures += 1
                    continue
                base_date = datetime.fromisoformat(date_result.stdout.decode("ascii").strip())
                duration = (branch.last_commit_date - base_date).days
                if duration > 30:
                    self._finding("branch", "long_lived_branch", branch.last_commit_date, branch.last_author,
                                  f"{duration} dias entre merge-base e último commit", branch.name,
                                  "Estimativa por datas de commits, não data de criação da branch.")
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                failures += 1
        if failures:
            self.limitations.append(f"Análise de branches parcial: {failures} refs sem ancestral comum, data ou leitura confiável.")
