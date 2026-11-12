import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, NamedTuple, Optional, Set, Tuple

from .links import RepoLinks, parse_remote_url
from .models import CommitInfo, FileChange

logger = logging.getLogger(__name__)


class BranchRef(NamedTuple):
    commit_hash: str
    date: Optional[datetime]
    author: str


class GitClient:
    COMMIT_DELIMITER = "==GRIT_COMMIT_START=="
    FIELD_DELIMITER = "==GRIT_FIELD_SPLIT=="

    def __init__(self, target_path_or_url: str):
        self.target = target_path_or_url
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.repo_dir: Path = self._resolve_target(target_path_or_url)

    def _resolve_target(self, target: str) -> Path:
        local_path = Path(target).resolve()
        if local_path.exists() and (local_path / ".git").is_dir():
            logger.info("Repositório local encontrado: %s", local_path)
            return local_path

        # Se for remoto ou não existir localmente, clona em diretório temporário
        logger.info("'%s' não é um repositório local; será clonado.", target)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="grit_repo_")
        dest_path = Path(self.temp_dir.name)

        try:
            logger.info("Clonando repositório (git clone --filter=blob:none) em %s ...", dest_path)
            subprocess.run(
                ["git", "clone", "--filter=blob:none", target, str(dest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            logger.info("Clone concluído.")
        except subprocess.CalledProcessError as e:
            logger.error("git clone falhou (código %s): %s", e.returncode, (e.stderr or "").strip())
            self.cleanup()
            raise RuntimeError(f"Falha ao clonar repositório: {e.stderr.strip()}")

        return dest_path

    def _run_git(self, args: List[str]) -> str:
        logger.debug("Executando: git %s", " ".join(args))
        res = subprocess.run(
            ["git", "-C", str(self.repo_dir)] + args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return res.stdout

    def get_default_branch(self) -> str:
        try:
            ref = self._run_git(["symbolic-ref", "refs/remotes/origin/HEAD"]).strip()
            branch = ref.split("/")[-1]
            logger.info("Branch padrão detectada via origin/HEAD: %s", branch)
            return branch
        except subprocess.CalledProcessError:
            logger.debug("origin/HEAD indisponível; testando 'main' e 'master'.")
            for candidate in ["main", "master"]:
                try:
                    self._run_git(["rev-parse", "--verify", candidate])
                    logger.info("Branch padrão detectada: %s", candidate)
                    return candidate
                except subprocess.CalledProcessError:
                    continue
        logger.warning("Não foi possível detectar a branch padrão; assumindo 'main'.")
        return "main"

    def get_branches(self) -> List[Tuple[str, bool]]:
        """Retorna lista de tuplas (nome_da_branch, is_remote)."""
        output = self._run_git(["branch", "-a", "--format=%(refname:short)"])
        branches = []
        for line in output.splitlines():
            line = line.strip()
            if not line or "->" in line or line in ("origin", "origin/HEAD"):
                continue
            is_remote = line.startswith("origin/")
            branches.append((line, is_remote))
        logger.info("%d branches encontradas.", len(branches))
        return branches

    def resolve_ref(self, branch: str) -> str:
        """Primeira referência existente entre a branch, origin/<branch> e HEAD."""
        for candidate in (branch, f"origin/{branch}", "HEAD"):
            try:
                self._run_git(["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
                return candidate
            except subprocess.CalledProcessError:
                continue
        return "HEAD"

    def get_branch_details(self) -> Dict[str, BranchRef]:
        """Último commit (hash, data e autor) de cada branch, por nome curto."""
        fmt = "%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)%09%(authorname)"
        output = self._run_git(["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes"])
        details: Dict[str, BranchRef] = {}
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            try:
                when: Optional[datetime] = datetime.fromisoformat(parts[2])
            except ValueError:
                when = None
            details[parts[0]] = BranchRef(commit_hash=parts[1], date=when, author=parts[3])
        return details

    def get_merged_branches(self, ref: str) -> Set[str]:
        """Nomes curtos das branches cujo último commit já está contido em `ref`."""
        output = self._run_git(["branch", "-a", "--merged", ref, "--format=%(refname:short)"])
        return {line.strip() for line in output.splitlines() if line.strip()}

    def list_files(self, ref: str) -> List[str]:
        output = self._run_git(["ls-tree", "-r", "-z", "--name-only", ref])
        return [path for path in output.split("\0") if path]

    def blame_authors(self, ref: str, path: str) -> Optional[Dict[Tuple[str, str], int]]:
        """Linhas atuais do arquivo por (nome, e-mail) do autor; None se o blame falhar."""
        cmd = ["git", "-C", str(self.repo_dir), "blame", "--line-porcelain", "-w", ref, "--", path]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            logger.debug("Falha ao iniciar git blame em %s: %s", path, e)
            return None

        counts: Dict[Tuple[str, str], int] = {}
        name = ""
        for line in proc.stdout:
            if line.startswith("author "):
                name = line[len("author "):].rstrip("\r\n")
            elif line.startswith("author-mail "):
                email = line[len("author-mail "):].strip().strip("<>").lower()
                key = (name, email)
                counts[key] = counts.get(key, 0) + 1

        if proc.wait() != 0:
            logger.debug("git blame falhou em %s (código %s); arquivo ignorado.", path, proc.returncode)
            return None
        return counts

    def get_remote_links(self) -> Optional[RepoLinks]:
        """URLs web do remoto 'origin' (GitHub/GitLab), ou None se indisponível."""
        try:
            remote_url = self._run_git(["remote", "get-url", "origin"]).strip()
        except subprocess.CalledProcessError:
            logger.info("Repositório sem remoto 'origin'; links para o navegador indisponíveis.")
            return None

        links = parse_remote_url(remote_url)
        if links:
            logger.info("Links web habilitados (%s): %s", links.kind, links.base_url)
        else:
            logger.info("Remoto 'origin' não é GitHub/GitLab reconhecido; links para o navegador indisponíveis.")
        return links

    def get_raw_mailmap(self) -> Optional[str]:
        mailmap_path = self.repo_dir / ".mailmap"
        if mailmap_path.is_file():
            logger.info("Arquivo .mailmap encontrado.")
            return mailmap_path.read_text(encoding="utf-8", errors="replace")
        logger.info("Nenhum .mailmap no repositório.")
        return None

    def stream_commits(self) -> Generator[CommitInfo, None, None]:
        """
        Lê todo o histórico com logs estruturados e numstat via streaming.
        """
        # %H: hash, %an: author name, %ae: author email, %aI: ISO-8601 strict date, %p: parent hashes, %B: body
        fmt = (
            f"{self.COMMIT_DELIMITER}%n"
            f"%H{self.FIELD_DELIMITER}"
            f"%an{self.FIELD_DELIMITER}"
            f"%ae{self.FIELD_DELIMITER}"
            f"%aI{self.FIELD_DELIMITER}"
            f"%p{self.FIELD_DELIMITER}"
            f"%s"
        )

        cmd = [
            "git",
            "-C",
            str(self.repo_dir),
            "log",
            "--all",
            f"--pretty=format:{fmt}",
            "--numstat",
            "--date=iso-strict",
        ]

        logger.info("Lendo histórico completo: git log --all --numstat ...")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        current_commit: Optional[CommitInfo] = None
        yielded = 0

        for line in proc.stdout:
            line_str = line.strip()
            if not line_str:
                continue

            if line_str == self.COMMIT_DELIMITER:
                if current_commit:
                    yielded += 1
                    yield current_commit
                current_commit = None
                continue

            if self.FIELD_DELIMITER in line:
                parts = line.split(self.FIELD_DELIMITER)
                if len(parts) >= 6:
                    chash, aname, aemail, adate_str, parents, subject = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5].strip()
                    try:
                        timestamp = datetime.fromisoformat(adate_str)
                    except ValueError:
                        logger.warning("Data inválida no commit %s (%r); usando data atual.", chash[:8], adate_str)
                        timestamp = datetime.utcnow()

                    is_merge = len(parents.strip().split()) > 1

                    current_commit = CommitInfo(
                        hash=chash,
                        author_name=aname,
                        author_email=aemail,
                        timestamp=timestamp,
                        message=subject,
                        is_merge=is_merge,
                        files=[],
                    )
            elif current_commit is not None:
                # Trata as linhas do --numstat: <adds> <dels> <file>
                match = re.match(r"^(\d+|-)\s+(\d+|-)\s+(.+)$", line_str)
                if match:
                    adds_raw, dels_raw, path = match.groups()
                    adds = int(adds_raw) if adds_raw != "-" else 0
                    dels = int(dels_raw) if dels_raw != "-" else 0
                    current_commit.files.append(
                        FileChange(filepath=path, additions=adds, deletions=dels)
                    )

        if current_commit:
            yielded += 1
            yield current_commit

        proc.wait()
        if proc.returncode != 0:
            logger.error("git log terminou com código %s: %s", proc.returncode, proc.stderr.read().strip())
        else:
            logger.info("Leitura do histórico concluída: %d commits.", yielded)

    def cleanup(self):
        if self.temp_dir:
            logger.info("Removendo clone temporário.")
            self.temp_dir.cleanup()
            self.temp_dir = None
            