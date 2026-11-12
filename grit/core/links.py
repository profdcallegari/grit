import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlsplit

_SCP_LIKE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>(?!//).+)$")


@dataclass(frozen=True)
class RepoLinks:
    """Gera URLs web (GitHub/GitLab) para itens de um repositório."""

    kind: str  # "github" ou "gitlab"
    base_url: str  # ex.: https://github.com/dono/repo (sem credenciais)

    def repo_url(self) -> str:
        return self.base_url

    def branch_url(self, branch: str) -> str:
        name = quote(_strip_origin(branch), safe="/")
        prefix = "/-/tree/" if self.kind == "gitlab" else "/tree/"
        return f"{self.base_url}{prefix}{name}"

    def commit_url(self, commit_hash: str) -> str:
        prefix = "/-/commit/" if self.kind == "gitlab" else "/commit/"
        return f"{self.base_url}{prefix}{quote(commit_hash)}"

    def author_url(self, name: str, email: Optional[str], default_branch: str) -> str:
        if self.kind == "gitlab":
            ref = quote(default_branch, safe="/")
            return f"{self.base_url}/-/commits/{ref}?author={quote(name)}"
        return f"{self.base_url}/commits?author={quote(email or name)}"


def _strip_origin(branch: str) -> str:
    return branch[len("origin/"):] if branch.startswith("origin/") else branch


def parse_remote_url(remote_url: str) -> Optional[RepoLinks]:
    """Converte a URL do remoto (https, ssh ou scp-like) em RepoLinks.

    Retorna None se o formato ou a hospedagem não forem reconhecidos.
    Credenciais embutidas na URL são descartadas.
    """
    remote_url = remote_url.strip()
    if not remote_url:
        return None

    if "://" in remote_url:
        parts = urlsplit(remote_url)
        if parts.scheme in ("http", "https"):
            netloc = parts.netloc.rsplit("@", 1)[-1]
            host = parts.hostname or ""
        else:  # ssh:// e git://: a porta não é a do site web
            netloc = host = parts.hostname or ""
        path = parts.path
    else:
        match = _SCP_LIKE.match(remote_url)
        if not match:
            return None
        netloc = host = match.group("host")
        path = "/" + match.group("path")

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if not host or not path:
        return None

    lowered = host.lower()
    if "github" in lowered:
        kind = "github"
    elif "gitlab" in lowered:
        kind = "gitlab"
    else:
        return None

    return RepoLinks(kind=kind, base_url=f"https://{netloc}/{path}")
