import logging
import tkinter as tk
import unittest
from datetime import date, datetime
from unittest.mock import patch

from grit.activity_panel import ActivityPanel
from grit.core.models import (
    ActivityTimeline, AttentionFinding, AttentionReport, AuthorStats, BlameDetails, BranchReport, CommitSummary,
    FileHotspot, RepositoryReport, WeeklyActivity,
)
from grit.gui import GritApp


class GritAppPresentationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.app = GritApp()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tkinter indisponível: {exc}") from exc
        self.app.withdraw()
        self.addCleanup(self.app._on_close)

    def test_initial_and_empty_report_states(self):
        self.assertEqual(self.app.card_total_commits.get(), "—")
        self.assertIn("Execute uma análise", self.app.results_summary.get())
        self.assertIn("Execute uma análise", self.app.activity_panel.canvas.itemcget(1, "text"))

        report = RepositoryReport(0, {}, [], 0, 0)
        self.app._render_results(report)

        self.assertEqual(self.app.card_total_commits.get(), "0")
        self.assertIn("Nenhum autor", self.app.results_summary.get())
        self.assertIn("Nenhum dado", self.app.participation_summary.get())
        self.assertIn("Nenhuma branch", self.app.branches_summary.get())
        self.assertIn("Nenhum commit", self.app.commits_summary.get())
        item = self.app.activity_panel.canvas.find_all()[0]
        self.assertIn("Nenhuma atividade", self.app.activity_panel.canvas.itemcget(item, "text"))

    def test_log_defaults_to_info_and_can_switch_to_debug(self):
        handler = self.app.log_handler
        panel = self.app.log_panel
        while not handler.records.empty():
            handler.records.get_nowait()

        logger = logging.Logger("test.log", logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.assertEqual(panel.level_var.get(), "INFO")
        logger.debug("não deve aparecer")
        logger.info("mensagem informativa")
        self.assertIn("mensagem informativa", handler.records.get_nowait()[1])
        self.assertTrue(handler.records.empty())

        panel.level_var.set("DEBUG")
        panel._on_level_change()
        logger.debug("mensagem de depuração")
        self.assertIn("mensagem de depuração", handler.records.get_nowait()[1])

        panel.level_var.set("INFO")
        panel._on_level_change()
        logger.debug("não deve aparecer novamente")
        self.assertTrue(handler.records.empty())

    def test_progress_updates_status_on_ui_thread_and_ignores_old_runs(self):
        run_id = object()
        self.app._active_analysis_id = run_id
        logger = logging.Logger("test.progress.ui", logging.INFO)
        logger.addHandler(self.app.log_handler)
        logger.info("progresso", extra={
            "grit_progress_id": run_id, "grit_progress_text": "Lendo: 5 commits processados.",
        })
        self.app.log_panel._drain()
        self.assertEqual(self.app.status_var.get(), "Lendo: 5 commits processados.")
        self.app._set_ui_busy(False)
        self.app.status_var.set("Concluído")
        logger.info("antigo", extra={
            "grit_progress_id": run_id, "grit_progress_text": "Progresso antigo",
        })
        self.app.log_panel._drain()
        self.assertEqual(self.app.status_var.get(), "Concluído")

    def test_selected_tab_has_same_height_and_distinct_background(self):
        self.app.deiconify()
        self.app.update()
        notebook = self.app.notebook
        self.assertNotEqual(
            self.app.style.lookup("TNotebook.Tab", "background", ("selected",)),
            self.app.style.lookup("TNotebook.Tab", "background", ("!selected",)),
        )

        heights = {}
        for selected in range(len(notebook.tabs())):
            notebook.select(selected)
            self.app.update()
            centers = {}
            for x in range(notebook.winfo_width()):
                if notebook.identify(x, 5):
                    index = notebook.index(f"@{x},5")
                    centers.setdefault(index, []).append(x)
            self.assertEqual(len(centers), len(notebook.tabs()))
            for index, positions in centers.items():
                x = positions[len(positions) // 2]
                height = sum(
                    1 for y in range(60)
                    if notebook.identify(x, y) and notebook.index(f"@{x},{y}") == index
                )
                heights.setdefault(index, set()).add(height)
        self.assertTrue(all(len(values) == 1 for values in heights.values()), heights)

    def test_report_rows_and_states_update_after_sort_and_new_report(self):
        authors = {
            "Ana": AuthorStats("Ana", total_commits=2, generic_emails={"root@localhost"}),
            "Bia": AuthorStats("Bia", total_commits=1),
        }
        week = date(2026, 1, 5)
        report = RepositoryReport(
            3, authors,
            [BranchReport("main", False, False, "abc", status="principal")],
            0, 0,
            commits=[CommitSummary("abc", "Ana", datetime(2026, 1, 5), "feat: exemplo")],
            activity=ActivityTimeline([week], {"Ana": {week: 2}}),
        )
        self.app._render_results(report)
        self.app._sort_column("name")

        rows = self.app.tree.get_children()
        self.assertEqual(len(rows), 2)
        self.assertIn("flagged", self.app.tree.item(rows[0], "tags"))
        self.assertIn("stripe", self.app.tree.item(rows[1], "tags"))
        self.assertEqual(len(self.app.commits_tree.get_children()), 1)
        self.assertEqual(len(self.app.branches_tree.get_children()), 1)
        self.assertGreater(len(self.app.activity_panel.canvas.find_all()), 1)

        self.app._render_results(RepositoryReport(0, {}, [], 0, 0))
        self.assertFalse(self.app.tree.get_children())
        self.assertFalse(self.app.commits_tree.get_children())
        self.assertFalse(self.app.branches_tree.get_children())
        self.assertEqual(len(self.app.activity_panel.canvas.find_all()), 1)

    def test_constellation_and_commit_list_stay_in_sync(self):
        first = CommitSummary(
            "a" * 40, "Ana", datetime(2026, 1, 5), "first commit",
        )
        main_commit = CommitSummary(
            "b" * 40, "Ana", datetime(2026, 1, 6), "main change",
            parents=(first.hash,),
        )
        feature_commit = CommitSummary(
            "c" * 40, "Bia", datetime(2026, 1, 6), "feature change",
            parents=(first.hash,),
        )
        merge = CommitSummary(
            "d" * 40, "Ana", datetime(2026, 1, 7), "merge feature",
            is_merge=True, parents=(main_commit.hash, feature_commit.hash),
        )
        report = RepositoryReport(
            4, {}, [], 0, 0, commits=[merge, feature_commit, main_commit, first],
        )
        self.app._render_results(report)

        panel = self.app.constellation_panel
        self.assertEqual(self.app.commits_views.tab(1, "text").strip(), "Constelação")
        self.assertEqual(len(self.app.commits_tree.get_children()), 4)
        self.assertEqual(len(panel.canvas.find_withtag("commit-star")), 4)
        self.assertEqual(len(panel.canvas.find_withtag("parent-edge")), 4)
        self.assertEqual(len(panel.canvas.coords(panel._star_items[merge.hash])), 8)
        self.assertEqual(panel.canvas.itemcget(panel._star_items[merge.hash], "fill"), panel.STAR_DIM)

        self.app.commits_tree.selection_set(merge.hash)
        self.app.update()
        self.assertIn("merge feature", panel.details_var.get())
        self.assertNotEqual(panel.canvas.itemcget(panel._star_items[merge.hash], "fill"), panel.STAR_DIM)

        panel._select_hash(first.hash, notify=True)
        self.app.update()
        self.assertEqual(self.app.commits_tree.selection(), (first.hash,))
        self.assertIn("first commit", panel.details_var.get())

        self.app._render_results(RepositoryReport(0, {}, [], 0, 0))
        self.assertEqual(len(panel.canvas.find_all()), 1)
        self.assertFalse(self.app.commits_tree.get_children())

    def test_rhythm_and_code_views_update_and_clear(self):
        week = date(2026, 1, 5)
        report = RepositoryReport(
            1, {"Ana": AuthorStats("Ana", total_commits=1)}, [], 0, 0,
            activity=ActivityTimeline([week], {"Ana": {week: 1}}, [WeeklyActivity(week, 1, 3, 1, 1)]),
            hotspots=[FileHotspot("src/a.py", 1, 3, 1), FileHotspot("README.md", 2, 1, 0)],
            blame_ref="main", blame_total_lines=3, blame_status="completed",
            blame_details=BlameDetails({"src": {"Ana": 3}}, 2, 1, 1),
        )
        self.app._render_results(report)
        values = self.app.weekly_tree.item(self.app.weekly_tree.get_children()[0], "values")
        self.assertEqual(tuple(map(str, values[1:])), ("1", "3", "1", "1"))
        self.assertIn("1/1", self.app.rhythm_summary.get())
        self.assertIn("Autoria parcial", self.app.code_summary.get())
        self.assertEqual(len(self.app.module_tree.get_children()), 1)
        self.assertEqual(self.app.hotspot_tree.item(self.app.hotspot_tree.get_children()[0], "values")[0], "src/a.py")
        self.app._render_hotspots("commits")
        self.assertEqual(self.app.hotspot_tree.item(self.app.hotspot_tree.get_children()[0], "values")[0], "README.md")

        self.app._render_results(RepositoryReport(0, {}, [], 0, 0))
        self.assertFalse(self.app.weekly_tree.get_children())
        self.assertFalse(self.app.regularity_tree.get_children())
        self.assertFalse(self.app.hotspot_tree.get_children())
        self.assertFalse(self.app.module_tree.get_children())
        self.assertIn("desativado", self.app.code_summary.get())

    def test_metrics_tables_are_usable_at_minimum_window_size(self):
        self.app.geometry("850x550")
        self.app.deiconify()
        for first, second in (
            (self.app.weekly_tree, self.app.regularity_tree),
            (self.app.hotspot_tree, self.app.module_tree),
        ):
            self.app.notebook.select(first.master.master.master)
            self.app.update()
            self.assertGreaterEqual(first.winfo_height(), 55)
            self.assertGreaterEqual(second.winfo_height(), 55)

    def test_directory_map_shared_filter_sort_and_reset(self):
        report = RepositoryReport(
            3, {"Ana": AuthorStats("Ana"), "Bia": AuthorStats("Bia")}, [], 0, 0,
            directory_activity={"Ana": {".": 1, "src": 2}, "Bia": {"src": 1}},
            hotspots=[
                FileHotspot("src/shared.py", 2, 4, 1, authors={"Ana", "Bia"}, renamed=True,
                            present_in_default=False),
                FileHotspot("one.py", 3, 1, 0, authors={"Ana"}, present_in_default=True),
            ],
            history_shallow=True, history_ref="main", history_renames=1,
        )
        self.app._render_results(report)
        canvas = self.app.directory_panel.canvas
        texts = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
        self.assertIn("src", texts)
        self.assertIn("2", texts)
        self.assertIn("0", texts)
        self.assertIn("Histórico raso", self.app.directories_summary.get())
        rows = self.app.shared_tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertIn("renomeação", self.app.shared_tree.item(rows[0], "values")[-1])

        self.app.shared_only.set(False)
        self.app._sort_shared_files("commits")
        rows = self.app.shared_tree.get_children()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.app.shared_tree.item(rows[0], "values")[0], "one.py")

        self.app._render_results(RepositoryReport(0, {}, [], 0, 0))
        self.assertFalse(self.app.shared_tree.get_children())
        self.assertEqual(len(canvas.find_all()), 1)

    def test_directory_views_are_usable_at_minimum_window_size(self):
        self.app.geometry("850x550")
        self.app.deiconify()
        map_frame = self.app.directory_panel.master
        views = map_frame.master
        self.app.notebook.select(views.master)
        views.select(map_frame)
        self.app.update()
        self.assertGreaterEqual(self.app.directory_panel.canvas.winfo_height(), 55)
        views.select(self.app.shared_tree.master.master)
        self.app.update()
        self.assertGreaterEqual(self.app.shared_tree.winfo_height(), 55)

    def test_attention_filters_details_and_resets_with_new_report(self):
        panel = self.app.attention_panel
        report = RepositoryReport(0, {}, [], 0, 0, attention=AttentionReport(
            findings=[
                AttentionFinding("commit", "large_commit_files", datetime(2026, 1, 5), "Ana", "30 arquivos", "abc", "Detalhes"),
                AttentionFinding("arquivo", "large_file", None, "", "2 MB", "file.bin", "Limitação"),
            ],
            limitations=["Histórico raso."],
        ))
        self.app._render_results(report)
        self.assertIn("2 ocorrência", panel.summary.get())
        self.assertEqual(panel.category_counts["commit"].get(), "1")
        self.assertEqual(panel.category_counts["segurança"].get(), "0")
        self.assertIn("Histórico raso", panel.coverage_text.get("1.0", "end"))
        panel.category.set("Commits")
        panel.author.set("ana")
        panel.from_date.set("2026-01-05")
        panel.to_date.set("2026-01-05")
        panel._apply_filters()
        rows = panel.tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(panel.tree.item(rows[0], "values")[1], "Muitos arquivos no commit")
        panel.tree.selection_set(rows[0])
        panel._show_details()
        self.assertIn("Detalhes", panel.detail.get("1.0", "end"))
        panel.from_date.set("2026-02-01")
        with patch("grit.attention_panel.messagebox.showwarning") as warning:
            panel._apply_filters()
        warning.assert_called_once()
        self.assertEqual(len(panel.tree.get_children()), 1)

        self.app._render_results(RepositoryReport(0, {}, [], 0, 0))
        self.assertFalse(panel.tree.get_children())
        self.assertIn("indisponível", panel.summary.get())
        self.assertEqual(panel.category_counts["commit"].get(), "—")

    def test_attention_configuration_rejects_invalid_values(self):
        panel = self.app.attention_panel
        panel.config_vars["deadline"].set("20260101")
        with self.assertRaises(ValueError):
            panel.get_config()
        panel.config_vars["deadline"].set("2026-01-01")
        panel.config_vars["max_commit_files"].set("0")
        with self.assertRaises(ValueError):
            panel.get_config()
        panel.config_vars["max_commit_files"].set("5")
        config = panel.get_config()
        self.assertEqual(config.deadline, date(2026, 1, 1))
        self.assertEqual(config.max_commit_files, 5)

    def test_attention_is_usable_at_minimum_window_size(self):
        self.app.geometry("850x550")
        self.app.deiconify()
        self.app.notebook.select(self.app.attention_panel)
        self.app.update()
        self.assertGreaterEqual(self.app.attention_panel.tree.winfo_height(), 55)


class ActivityColorsTests(unittest.TestCase):
    def test_count_labels_have_sufficient_contrast(self):
        for count in range(1, 101):
            fill, text = ActivityPanel._colors(count, 100)
            channels = [int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
            contrast = (1.05 / (luminance + 0.05) if text == "white" else (luminance + 0.05) / 0.05)
            with self.subTest(count=count):
                self.assertGreaterEqual(contrast, 4.5)


if __name__ == "__main__":
    unittest.main()
