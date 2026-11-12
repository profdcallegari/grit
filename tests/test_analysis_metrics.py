import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grit.core.analyzer import RepositoryAnalyzer
from grit.core.git_client import GitClient
from grit.core.metrics import active_weeks, blame_description


class AnalysisMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.temp_dir.name)
        cls.addClassCleanup(cls.temp_dir.cleanup)
        cls.git("init", "-b", "main")
        (cls.repo / "src").mkdir()
        (cls.repo / "vendor").mkdir()
        (cls.repo / "README.md").write_text("one\n", encoding="utf-8")
        (cls.repo / "src" / "a.py").write_text("a\nb\n", encoding="utf-8")
        cls.commit("Ana", "ana@example.com", "2026-01-05T12:00:00+00:00", "first")
        (cls.repo / "src" / "a.py").write_text("a\nc\n", encoding="utf-8")
        (cls.repo / "src" / "ação.py").write_text("x\n", encoding="utf-8")
        (cls.repo / "vendor" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        cls.commit("Bia", "bia@example.com", "2026-01-19T12:00:00+00:00", "second")

    @classmethod
    def git(cls, *args, env=None):
        return subprocess.run(
            ["git", "-C", str(cls.repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8", env=env,
        )

    @classmethod
    def commit(cls, name, email, when, message):
        cls.git("add", ".")
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": when,
        })
        cls.git("commit", "-m", message, env=env)

    def test_weekly_metrics_hotspots_and_module_authorship(self):
        report = RepositoryAnalyzer(str(self.repo)).analyze()
        activity = report.activity
        self.assertIsNotNone(activity)
        self.assertEqual(
            [(w.commits, w.additions, w.deletions, w.active_authors) for w in activity.weekly],
            [(1, 3, 0, 1), (0, 0, 0, 0), (1, 2, 1, 1)],
        )
        self.assertEqual(active_weeks(activity), 2)
        self.assertEqual(active_weeks(activity, "Ana"), 1)
        self.assertEqual(len(activity.weeks), 3)
        hotspots = {item.path: item for item in report.hotspots}
        self.assertEqual(set(hotspots), {"README.md", "src/a.py", "src/ação.py"})
        self.assertEqual((hotspots["src/a.py"].commits, hotspots["src/a.py"].churn), (2, 4))
        self.assertEqual(report.hotspots[0].path, "src/a.py")
        self.assertEqual(report.blame_status, "completed")
        self.assertIsNotNone(report.blame_details)
        self.assertEqual(report.blame_details.modules, {".": {"Ana": 1}, "src": {"Ana": 1, "Bia": 2}})
        self.assertEqual(report.blame_details.eligible_files, 3)
        self.assertEqual(report.blame_details.analyzed_files, 3)

    def test_disabled_and_partial_blame_are_distinguished(self):
        disabled = RepositoryAnalyzer(str(self.repo), include_blame=False).analyze()
        self.assertIsNone(disabled.blame_details)
        self.assertIn("desativado", blame_description(disabled))

        with patch.object(RepositoryAnalyzer, "_compute_surviving_authorship", side_effect=RuntimeError("erro")):
            with self.assertLogs("grit.core.analyzer", level="ERROR"):
                failed = RepositoryAnalyzer(str(self.repo)).analyze()
        self.assertEqual(failed.blame_status, "failed")
        self.assertIsNone(failed.blame_details)
        self.assertIn("falha no git blame", blame_description(failed))

        original = GitClient.blame_authors

        def partial(client, ref, path):
            return None if path == "src/a.py" else original(client, ref, path)

        with patch("grit.core.analyzer.MAX_BLAME_FILES", 2), patch.object(GitClient, "blame_authors", partial):
            report = RepositoryAnalyzer(str(self.repo)).analyze()
        self.assertIsNotNone(report.blame_details)
        self.assertEqual(
            (report.blame_details.eligible_files, report.blame_details.analyzed_files, report.blame_details.failed_files),
            (3, 1, 1),
        )
        self.assertIn("Autoria parcial", blame_description(report))
        self.assertIn("1 fora do limite", blame_description(report))

    def test_cli_displays_metrics_without_blame(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "grit", "--cli", str(self.repo), "--no-blame"],
            cwd=Path(__file__).resolve().parents[1], env=env, check=True,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("Equipe: 2/3 semanas ativas", result.stdout)
        self.assertIn("2026-01-12: 0 commits", result.stdout)
        self.assertIn("src/ação.py", result.stdout)
        self.assertIn("git blame desativado", result.stdout)


if __name__ == "__main__":
    unittest.main()
