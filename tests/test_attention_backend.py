import logging
import os
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from grit.core.analyzer import RepositoryAnalyzer
from grit.core.attention import AttentionAnalyzer
from grit.core.models import AttentionConfig


class AttentionBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="attention-test-", dir=".")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-b", "main")

    def git(self, *args, env=None):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], env=env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def commit(self, message, day, name="Ana", email="ana@school.edu"):
        self.git("add", "-A")
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": day, "GIT_COMMITTER_DATE": day,
        })
        self.git("commit", "-m", message, env=env)
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def analyze(self, **kwargs):
        return RepositoryAnalyzer(str(self.repo), include_blame=False,
                                  attention_config=AttentionConfig(**kwargs)).analyze()

    def test_real_git_findings_and_no_secret_disclosure(self):
        self.write("src/app.py", "print('first')\n")
        self.write("other/code.py", "x = 1\n")
        self.commit("feat: start", "2026-01-01T10:00:00-03:00")
        self.write("src/app.py", "print('fixed')\n")
        self.write("tests/test_app.py", "assert True\n")
        fix_commit = self.commit("fix: adjust", "2026-01-01T11:00:00-03:00")
        secret = "a_secret_value_987654"
        self.write("src/config.txt", 'password = "' + secret + '"\n<<<<<<< HEAD\n=======\n>>>>>>> branch\n')
        self.write("dist/bundle.min.js", "a" * 100)
        self.commit('revert: password = "' + secret + '"', "2026-01-02T10:00:00-03:00", email="root@localhost")
        with self.assertLogs("grit.core", level=logging.DEBUG) as logs:
            report = self.analyze(max_commit_files=1, max_commit_churn=1, max_directories=1,
                                  max_file_bytes=50, deadline=date(2026, 1, 3), deadline_days=3)
        attention = report.attention
        kinds = {finding.kind for finding in attention.findings}
        self.assertTrue({
            "large_commit_files", "large_commit_churn", "many_directories", "immediate_fix",
            "possible_revert", "large_file", "generated_file", "conflict_marker",
            "possible_secret", "code_without_tests", "generic_identity", "deadline_concentration",
            "multiple_emails",
        }.issubset(kinds), kinds)
        self.assertFalse(any(f.kind == "code_without_tests" and f.reference == fix_commit
                             for f in attention.findings))
        self.assertNotIn(secret, repr(report))
        self.assertNotIn(secret, "\n".join(logs.output))
        self.assertTrue(any("conteúdo:" in text and "limites:" in text for text in attention.limitations))
        self.assertEqual(next(f for f in attention.findings if f.kind == "immediate_fix").timestamp.utcoffset().total_seconds(), -10800)

    def test_disabled_config_validation_and_mutation(self):
        self.write("x.py", "x = 1\n")
        self.commit("first", "2026-01-01T10:00:00+00:00")
        with patch.object(AttentionAnalyzer, "finish", side_effect=AssertionError):
            self.assertIsNone(RepositoryAnalyzer(str(self.repo), include_blame=False).analyze().attention)
        for field, value in (("max_commit_files", 0), ("max_commit_churn", True),
                             ("max_directories", -1), ("max_file_bytes", 0),
                             ("deadline_days", 0), ("deadline", datetime.now()),
                             ("test_paths", ()), ("test_paths", ("../tests",)),
                             ("test_paths", ("C:tests",))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                AttentionConfig(**{field: value})
        config = AttentionConfig()
        config.max_file_bytes = -1
        with self.assertRaises(ValueError):
            RepositoryAnalyzer(str(self.repo), attention_config=config)

    def test_immediate_fix_requires_parent_overlap_and_nonnegative_time(self):
        self.write("a.py", "a\n")
        first = self.commit("start", "2026-01-01T10:00:00+00:00")
        self.write("b.py", "b\n")
        self.commit("fix: unrelated", "2026-01-01T11:00:00+00:00")
        self.write("b.py", "b changed\n")
        self.commit("fix: wrong clock", "2026-01-01T09:00:00+00:00")
        report = self.analyze()
        self.assertFalse(any(f.kind == "immediate_fix" for f in report.attention.findings))
        self.assertTrue(any(f.reference == first for f in report.attention.findings))

    def test_partial_clone_never_reads_blob(self):
        self.write("a.py", "print('x')\n")
        self.commit("initial", "2026-01-01T10:00:00+00:00")
        self.git("config", "remote.origin.promisor", "true")
        original = AttentionAnalyzer._git

        def guarded(instance, args):
            self.assertNotEqual(args[0], "cat-file")
            return original(instance, args)

        with patch.object(AttentionAnalyzer, "_git", guarded), \
                patch.object(AttentionAnalyzer, "_bounded_git", side_effect=AssertionError("unexpected diff")):
            report = self.analyze()
        self.assertTrue(any("clone parcial" in text for text in report.attention.limitations))
        self.assertTrue(any("Segredos históricos indisponíveis" in text for text in report.attention.limitations))

    def test_blob_size_and_scan_limit_are_reported(self):
        self.write("one.txt", "a" * 50)
        self.write("two.txt", "b" * 50)
        self.commit("initial", "2026-01-01T10:00:00+00:00")
        with patch("grit.core.attention.MAX_SCAN_FILES", 1):
            report = self.analyze(max_file_bytes=20)
        self.assertEqual(sum(f.kind == "large_file" and f.reference in {"one.txt", "two.txt"}
                             for f in report.attention.findings), 2)
        self.assertTrue(any("1 lidos, 1 ignorados" in s for s in report.attention.limitations))

    def test_branch_inference_and_shallow_limitations(self):
        self.write("a.py", "a\n")
        self.commit("base", "2026-01-01T10:00:00+00:00")
        self.git("checkout", "-b", "old")
        self.write("a.py", "old\n")
        self.commit("feature", "2026-02-05T10:00:00+00:00")
        self.git("checkout", "main")
        self.write("a.py", "main\n")
        self.commit("main", "2026-04-01T10:00:00+00:00")
        report = self.analyze()
        kinds = {f.kind for f in report.attention.findings}
        self.assertIn("possibly_abandoned", kinds)
        self.assertIn("long_lived_branch", kinds)
        with patch("grit.core.git_client.GitClient.is_shallow", return_value=True):
            report = self.analyze()
        self.assertFalse(any(f.kind in {"possibly_abandoned", "long_lived_branch"} for f in report.attention.findings))
        self.assertTrue(any("Histórico raso" in s for s in report.attention.limitations))

    def test_removed_historical_content_with_safe_evidence(self):
        secret = "retired-secret-12345"
        self.write("dist/old.min.js", 'password = "' + secret + '"\n<<<<<<< HEAD\n=======\n>>>>>>> old\n')
        self.commit("initial", "2026-01-01T10:00:00+00:00")
        (self.repo / "dist" / "old.min.js").unlink()
        self.write("new.txt", "safe\n")
        removed = self.commit("remove old", "2026-01-02T10:00:00+00:00")
        with self.assertLogs("grit.core", level=logging.DEBUG) as logs:
            report = self.analyze(max_file_bytes=20)
        for kind in ("possible_secret", "conflict_marker", "large_file", "generated_file"):
            matches = [f for f in report.attention.findings if f.kind == kind and f.reference == removed]
            self.assertTrue(matches, kind)
            self.assertIn("dist/old.min.js", matches[0].details)
            self.assertIn("versão anterior", matches[0].details)
        self.assertNotIn(secret, repr(report))
        self.assertNotIn(secret, "\n".join(logs.output))
        self.assertTrue(any("2/2 commits" in s and "blobs distintos" in s
                            for s in report.attention.limitations))

    def test_historical_limits_and_errors_are_disclosed(self):
        self.write("a.txt", 'password = "retired-secret-12345"\n')
        self.commit("first", "2026-01-01T10:00:00+00:00")
        self.write("a.txt", "safe\n")
        self.commit("second", "2026-01-02T10:00:00+00:00")
        with patch("grit.core.attention.MAX_HISTORY_COMMITS", 1):
            report = self.analyze()
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s and "1/2 commits" in s
                            for s in report.attention.limitations))
        with patch("grit.core.attention.MAX_HISTORY_BLOBS", 1):
            report = self.analyze()
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s and "1 blobs distintos" in s
                            for s in report.attention.limitations))
        with patch("grit.core.attention.MAX_HISTORY_DIFF_BYTES", 20):
            report = self.analyze()
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s
                            for s in report.attention.limitations))
        with patch.object(AttentionAnalyzer, "_bounded_git", side_effect=OSError("não imprimir")):
            report = self.analyze()
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s and "2 erros" in s
                            for s in report.attention.limitations))
        original = AttentionAnalyzer._git

        def failed_blob_size(instance, args):
            if args[:2] == ["cat-file", "-s"]:
                return subprocess.CompletedProcess(args, 1, b"", "conteúdo sensível não exibido".encode("utf-8"))
            return original(instance, args)

        with patch.object(AttentionAnalyzer, "_git", failed_blob_size):
            report = self.analyze()
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s and "2 erros" in s
                            for s in report.attention.limitations))
        self.assertNotIn("conteúdo sensível não exibido", repr(report))

    def test_budget_starts_at_finish_not_during_commit_processing(self):
        self.write("old.txt", 'password = "retired-secret-12345"\n')
        self.commit("initial", "2026-01-01T10:00:00+00:00")
        original = AttentionAnalyzer.add_commit

        def delayed(analyzer, commit, author):
            original(analyzer, commit, author)
            analyzer.started = -1000000000

        with patch.object(AttentionAnalyzer, "add_commit", delayed):
            report = self.analyze()
        self.assertTrue(any(f.kind == "possible_secret" and f.reference == "old.txt"
                            for f in report.attention.findings))
        self.assertTrue(any("1/1 commits" in s and "1 lidos" in s for s in report.attention.limitations))

    def test_merge_history_scans_parent_diffs(self):
        self.write("base.txt", "base\n")
        self.commit("base", "2026-01-01T10:00:00+00:00")
        self.git("checkout", "-b", "feature")
        self.write("feature.txt", 'password = "feature-secret-12345"\n')
        self.commit("feature", "2026-01-02T10:00:00+00:00")
        self.git("checkout", "main")
        self.write("main.txt", "other\n")
        self.commit("main", "2026-01-03T10:00:00+00:00")
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": "Ana", "GIT_AUTHOR_EMAIL": "ana@school.edu",
            "GIT_COMMITTER_NAME": "Ana", "GIT_COMMITTER_EMAIL": "ana@school.edu",
            "GIT_AUTHOR_DATE": "2026-01-04T10:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-01-04T10:00:00+00:00",
        })
        self.git("merge", "--no-ff", "feature", "-m", "merge", env=env)
        merge = self.git("rev-parse", "HEAD").stdout.decode().strip()
        report = self.analyze()
        self.assertTrue(any(f.kind == "possible_secret" and f.reference == merge
                            and "feature.txt" in f.details for f in report.attention.findings))
        self.assertTrue(any("Em merges" in s for s in report.attention.limitations))

    def test_actual_shallow_clone_reports_partial_history(self):
        self.write("a.py", "one\n")
        self.commit("first", "2026-01-01T10:00:00+00:00")
        self.write("a.py", "two\n")
        self.commit("second", "2026-01-02T10:00:00+00:00")
        clone = self.repo / "shallow-clone"
        subprocess.run(["git", "clone", "--depth", "1", self.repo.as_uri(), str(clone)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        report = RepositoryAnalyzer(str(clone), include_blame=False,
                                    attention_config=AttentionConfig()).analyze()
        self.assertTrue(report.history_shallow)
        self.assertEqual(report.total_commits, 1)
        self.assertTrue(any("Histórico raso" in s for s in report.attention.limitations))
        self.assertTrue(any("Conteúdo histórico" in s and "parcial" in s
                            for s in report.attention.limitations))

    def test_binary_and_tree_truncation_are_disclosed(self):
        (self.repo / "binary.dat").write_bytes(b"binary\0" + b"x" * 50)
        self.write("other.txt", "safe\n")
        self.commit("first", "2026-01-01T10:00:00+00:00")
        report = self.analyze()
        self.assertTrue(any("1 ignorados" in s and "Binários" in s for s in report.attention.limitations))
        with patch("grit.core.attention.MAX_TREE_ENTRIES", 1):
            report = self.analyze()
        self.assertTrue(any("árvore" in s.lower() and "parcial por limite" in s for s in report.attention.limitations))


if __name__ == "__main__":
    unittest.main()
