from datetime import date, timedelta
from typing import Dict, List

from .models import ActivityTimeline, AuthorStats, Concentration


def week_start(day: date) -> date:
    """Segunda-feira da semana de `day`."""
    return day - timedelta(days=day.weekday())


def build_activity(authors: Dict[str, AuthorStats]) -> ActivityTimeline:
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
    return ActivityTimeline(weeks=weeks, counts=counts)


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
