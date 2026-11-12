import logging
import tkinter as tk
import unittest
from datetime import date, datetime

from grit.activity_panel import ActivityPanel
from grit.core.models import ActivityTimeline, AuthorStats, BranchReport, CommitSummary, RepositoryReport
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
