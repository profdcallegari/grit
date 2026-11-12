from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PureWindowsPath
from typing import Dict, List, Optional, Set, Tuple

from .links import RepoLinks


@dataclass
class FileChange:
    filepath: str
    additions: int
    deletions: int
    previous_path: Optional[str] = None


@dataclass
class CommitInfo:
    hash: str
    author_name: str
    author_email: str
    timestamp: datetime
    message: str
    files: List[FileChange] = field(default_factory=list)
    is_merge: bool = False
    parents: Tuple[str, ...] = ()
    timestamp_valid: bool = True


@dataclass
class AttentionConfig:
    max_commit_files: int = 30
    max_commit_churn: int = 1000
    max_directories: int = 5
    max_file_bytes: int = 1048576
    deadline: Optional[date] = None
    deadline_days: int = 7
    test_paths: Tuple[str, ...] = ("tests", "test")

    def __post_init__(self):
        self.validate()

    def validate(self):
        for name in ("max_commit_files", "max_commit_churn", "max_directories", "max_file_bytes", "deadline_days"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} deve ser um inteiro positivo")
        if self.deadline is not None and type(self.deadline) is not date:
            raise ValueError("deadline deve ser uma data ou None")
        if not isinstance(self.test_paths, tuple) or not self.test_paths:
            raise ValueError("test_paths deve ser uma tupla não vazia")
        for path in self.test_paths:
            if (not isinstance(path, str) or not path or "\\" in path
                    or "\0" in path or PureWindowsPath(path).drive
                    or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))):
                raise ValueError("test_paths deve conter caminhos relativos válidos separados por /")


@dataclass
class AttentionFinding:
    category: str
    kind: str
    timestamp: Optional[datetime]
    author: str
    evidence: str
    reference: str
    details: str


@dataclass
class AttentionReport:
    findings: List[AttentionFinding]
    limitations: List[str]


@dataclass
class AuthorStats:
    canonical_name: str
    emails: Set[str] = field(default_factory=set)
    total_commits: int = 0
    additions: int = 0
    deletions: int = 0
    files_touched: Set[str] = field(default_factory=set)
    conventional_commits: int = 0
    direct_main_commits: int = 0
    commit_dates: List[datetime] = field(default_factory=list)
    surviving_lines: int = 0
    unmapped_emails: Set[str] = field(default_factory=set)
    generic_emails: Set[str] = field(default_factory=set)

    @property
    def net_lines(self) -> int:
        return self.additions - self.deletions

    @property
    def churn(self) -> int:
        return self.additions + self.deletions

    @property
    def identity_note(self) -> str:
        notes = []
        if self.unmapped_emails:
            notes.append("Fora do roster/.mailmap")
        if self.generic_emails:
            notes.append("E-mail genérico")
        return "; ".join(notes)

    @property
    def conventional_adherence_rate(self) -> float:
        if self.total_commits == 0:
            return 0.0
        return (self.conventional_commits / self.total_commits) * 100.0


@dataclass
class BranchReport:
    name: str
    is_remote: bool
    is_merged: bool
    last_commit_hash: str
    last_commit_date: Optional[datetime] = None
    last_author: str = ""
    status: str = ""  # principal | mesclada | ativa | abandonada


@dataclass
class CommitSummary:
    hash: str
    author: str
    timestamp: datetime
    message: str
    is_merge: bool = False


@dataclass
class WeeklyActivity:
    week: date
    commits: int
    additions: int
    deletions: int
    active_authors: int


@dataclass
class ActivityTimeline:
    weeks: List[date]  # segundas-feiras, contíguas, da primeira à última semana com commits
    counts: Dict[str, Dict[date, int]]  # autor -> semana -> commits
    weekly: List[WeeklyActivity] = field(default_factory=list)


@dataclass
class FileHotspot:
    path: str
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    authors: Set[str] = field(default_factory=set)
    renamed: bool = False
    present_in_default: Optional[bool] = None

    @property
    def churn(self) -> int:
        return self.additions + self.deletions


@dataclass
class BlameDetails:
    modules: Dict[str, Dict[str, int]]
    eligible_files: int
    analyzed_files: int
    failed_files: int


@dataclass
class Concentration:
    commit_share: Dict[str, float]  # autor -> fração (0..1) dos commits
    churn_share: Dict[str, float]  # autor -> fração (0..1) das linhas adicionadas+removidas
    gini_commits: float  # 0 = igualitário, 1 = um autor concentra tudo
    gini_churn: float
    top_author: str = ""
    top_share: float = 0.0


@dataclass
class RepositoryReport:
    total_commits: int
    authors: Dict[str, AuthorStats]
    branches: List[BranchReport]
    direct_main_commits_count: int
    suspicious_messages_count: int
    analyzed_at: datetime = field(default_factory=datetime.utcnow)
    commits: List[CommitSummary] = field(default_factory=list)
    default_branch: str = "main"
    links: Optional[RepoLinks] = None
    activity: Optional[ActivityTimeline] = None
    concentration: Optional[Concentration] = None
    blame_ref: Optional[str] = None
    blame_total_lines: int = 0
    blame_status: str = "disabled"  # disabled | failed | completed
    blame_details: Optional[BlameDetails] = None
    hotspots: List[FileHotspot] = field(default_factory=list)
    directory_activity: Dict[str, Dict[str, int]] = field(default_factory=dict)
    history_shallow: Optional[bool] = None
    history_ref: Optional[str] = None
    history_renames: int = 0
    attention: Optional[AttentionReport] = None
    