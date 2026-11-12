from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .models import ActivityTimeline, AuthorStats, Concentration, RepositoryReport, WeeklyActivity


def week_start(day: date) -> date:
    """Segunda-feira da semana de `day`."""
    return day - timedelta(days=day.weekday())


def build_activity(
    authors: Dict[str, AuthorStats],
    weekly_changes: Optional[Dict[date, Tuple[int, int]]] = None,
) -> ActivityTimeline:
    counts: Dict[str, Dict[date, int]] = {}
    first = last = None

    for name, author in authors.items():
        per_week: Dict[date, int] = {}
        for ts in author.commit_dates:
            week = week_start(ts.date())
            per_week[week] = per_week.get(week, 0) + 1
            first = week if first is None or week < first else first
            last = week if last is None or week > last else last
        counts[name] = per_week

    weeks: List[date] = []
    if first is not None and last is not None:
        current = first
        while current <= last:
            weeks.append(current)
            current += timedelta(days=7)
    changes = weekly_changes or {}
    weekly = []
    for week in weeks:
        activity = [per_week.get(week, 0) for per_week in counts.values()]
        additions, deletions = changes.get(week, (0, 0))
        weekly.append(WeeklyActivity(week, sum(activity), additions, deletions, sum(c > 0 for c in activity)))
    return ActivityTimeline(weeks=weeks, counts=counts, weekly=weekly)


def active_weeks(timeline: ActivityTimeline, author: Optional[str] = None) -> int:
    if author is not None:
        return sum(count > 0 for count in timeline.counts.get(author, {}).values())
    return sum(any(per_week.get(week, 0) > 0 for per_week in timeline.counts.values())
               for week in timeline.weeks)


def blame_description(report: RepositoryReport) -> str:
    if report.blame_status == "disabled":
        return "Autoria por módulo indisponível: git blame desativado."
    if report.blame_status == "failed" or report.blame_details is None:
        return "Autoria por módulo indisponível: falha no git blame; consulte o log."
    details = report.blame_details
    omitted = details.eligible_files - details.analyzed_files - details.failed_files
    partial = details.failed_files > 0 or omitted > 0
    prefix = "Autoria parcial" if partial else "Autoria"
    return (
        f"{prefix} em {report.blame_ref}: {details.analyzed_files}/{details.eligible_files} arquivos; "
        f"{details.failed_files} com falha, {omitted} fora do limite."
    )


def gini(values: List[float]) -> float:
    """Índice de Gini normalizado em [0, 1] (1 = um só autor concentra tudo)."""
    n = len(values)
    total = sum(values)
    if n < 2 or total <= 0:
        return 0.0
    ordered = sorted(values)
    weighted = sum(i * v for i, v in enumerate(ordered, start=1))
    raw = (2 * weighted) / (n * total) - (n + 1) / n
    return max(0.0, min(1.0, raw * n / (n - 1)))


def compute_concentration(authors: Dict[str, AuthorStats]) -> Concentration:
    total_commits = sum(a.total_commits for a in authors.values())
    total_churn = sum(a.churn for a in authors.values())

    commit_share = {
        name: (a.total_commits / total_commits if total_commits else 0.0)
        for name, a in authors.items()
    }
    churn_share = {
        name: (a.churn / total_churn if total_churn else 0.0)
        for name, a in authors.items()
    }

    top_author, top_share = "", 0.0
    if commit_share:
        top_author = max(commit_share, key=commit_share.get)
        top_share = commit_share[top_author]

    return Concentration(
        commit_share=commit_share,
        churn_share=churn_share,
        gini_commits=gini([a.total_commits for a in authors.values()]),
        gini_churn=gini([a.churn for a in authors.values()]),
        top_author=top_author,
        top_share=top_share,
    )
