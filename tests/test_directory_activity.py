import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grit.core.analyzer import RepositoryAnalyzer
from grit.core.git_client import GitClient, _nul_tokens
from grit.core.metrics import file_history_status, history_description


class DirectoryActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        for directory in ("src", "docs", "vendor"):
            (self.repo / directory).mkdir()
        (self.repo / "GRIT_RECORD").write_text("root\n", encoding="utf-8")
        (self.repo / "src" / "ação.py").write_text("first\n", encoding="utf-8")
        (self.repo / "src" / "other.py").write_text("other\n", encoding="utf-8")
        (self.repo / "src" / "binary.bin").write_bytes(b"\0first")
        (self.repo / "vendor" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        self.commit("Ana", "first")
        (self.repo / "src" / "ação.py").write_text("first\nsecond\n", encoding="utf-8")
        (self.repo / "src" / "other.py").write_text("other\nsecond\n", encoding="utf-8")
        (self.repo / "src" / "binary.bin").write_bytes(b"\0second")
        (self.repo / "docs" / "guide.txt").write_text("guide\n", encoding="utf-8")
        self.commit("Bia", "subject ==GRIT_FIELD_SPLIT== still intact")
        self.git("mv", "src/ação.py", "docs/ação.py")
        self.commit("Bia", "rename")

    def git(self, *args, env=None):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8", env=env,
        )

    def commit(self, name, message, allow_empty=False):
        self.git("add", ".")
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": f"{name.lower()}@example.com",
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": f"{name.lower()}@example.com",
        })
        options = ["--allow-empty"] if allow_empty else []
        self.git("commit", *options, "-m", message, env=env)

    def test_map_counts_commits_once_and_marks_historical_renames(self):
        report = RepositoryAnalyzer(str(self.repo), include_blame=False).analyze()
        self.assertEqual(report.directory_activity, {"Ana": {".": 1, "src": 1}, "Bia": {"src": 1, "docs": 2}})
        self.assertFalse(report.history_shallow)
        self.assertEqual(report.history_renames, 1)
        self.assertTrue(any(c.message == "subject ==GRIT_FIELD_SPLIT== still intact" for c in report.commits))
        files = {item.path: item for item in report.hotspots}
        self.assertNotIn("vendor/ignored.txt", files)
        self.assertEqual(files["src/ação.py"].authors, {"Ana", "Bia"})
        self.assertEqual((files["src/ação.py"].commits, files["src/ação.py"].churn), (2, 2))
        self.assertTrue(files["src/ação.py"].renamed)
        self.assertFalse(files["src/ação.py"].present_in_default)
        self.assertTrue(files["docs/ação.py"].renamed)
        self.assertTrue(files["docs/ação.py"].present_in_default)
        self.assertEqual(files["docs/ação.py"].authors, {"Bia"})
        self.assertEqual((files["src/binary.bin"].authors, files["src/binary.bin"].churn), ({"Ana", "Bia"}, 0))
        self.assertIn("fora de", file_history_status(files["src/ação.py"], report.history_ref))
        self.assertIn("renomeação", file_history_status(files["docs/ação.py"], report.history_ref))

    def test_log_errors_and_nul_delimited_names_are_not_hidden(self):
        self.assertEqual(list(_nul_tokens(io.BytesIO(b"path\r\nname\0"))), ["path\r\nname"])
        client = GitClient(str(self.repo))
        client.repo_dir = Path(self.temp.name)
        with self.assertRaisesRegex(RuntimeError, "git log falhou"):
            list(client.stream_commits())

    def test_empty_commit_does_not_change_directory_counts(self):
        self.commit("Ana", "empty commit", allow_empty=True)
        report = RepositoryAnalyzer(str(self.repo), include_blame=False).analyze()
        self.assertEqual(report.total_commits, 4)
        self.assertEqual(report.directory_activity, {"Ana": {".": 1, "src": 1}, "Bia": {"src": 1, "docs": 2}})

    @unittest.skipIf(os.name == "nt", "O Windows não permite quebras de linha em nomes de arquivos.")
    def test_git_paths_with_newlines_are_preserved(self):
        path = "src/line\r\nname.py"
        (self.repo / path).write_text("test\n", encoding="utf-8")
        self.commit("Ana", "unusual path")
        report = RepositoryAnalyzer(str(self.repo), include_blame=False).analyze()
        self.assertIn(path, {file.path for file in report.hotspots})

    def test_shallow_history_and_unknown_status_are_explicit(self):
        clone = Path(self.temp.name) / "shallow"
        subprocess.run(["git", "clone", "--depth", "1", self.repo.as_uri(), str(clone)],
                       check=True, capture_output=True)
        shallow = RepositoryAnalyzer(str(clone), include_blame=False).analyze()
        self.assertTrue(shallow.history_shallow)
        self.assertIn("Histórico raso", history_description(shallow))
        with patch.object(GitClient, "is_shallow", side_effect=ValueError("erro")):
            with patch.object(GitClient, "list_files", side_effect=subprocess.CalledProcessError(1, ["git"])):
                with self.assertLogs("grit.core.analyzer", level="WARNING"):
                    report = RepositoryAnalyzer(str(self.repo), include_blame=False).analyze()
        self.assertIsNone(report.history_shallow)
        self.assertIsNone(report.history_ref)
        self.assertIn("desconhecido", history_description(report))
        self.assertIn("desconhecida", file_history_status(report.hotspots[0], report.history_ref))

    def test_cli_lists_shared_files_and_directory_counts(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "grit", "--cli", str(self.repo), "--no-blame"],
            cwd=Path(__file__).resolve().parents[1], env=env, check=True,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("Ana: .=1, src=1", result.stdout)
        self.assertIn("Bia: docs=2, src=1", result.stdout)
        self.assertIn("src/ação.py: Ana, Bia", result.stdout)
        self.assertIn("renomeação", result.stdout)


if __name__ == "__main__":
    unittest.main()
