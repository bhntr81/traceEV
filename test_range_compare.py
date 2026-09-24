"""Known-count range comparisons, including unseen cards and all-in calls."""
import contextlib
import inspect
import io
import queue
import sqlite3
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
import query
import stats


class RangeComparisonChecks(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""CREATE TABLE decisions (
            hand_id TEXT, seat INTEGER, combo TEXT, street TEXT, facing TEXT,
            agg INTEGER, action TEXT, to_call REAL, is_hero INTEGER)""")
        self.con.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?)", [
            ("h1", 1, "AKs", "preflop", "open", 1, "R", 2, 1),
            ("h2", 1, "AKs", "preflop", "open", 0, "C", 2, 1),
            ("h3", 1, "AKs", "preflop", "open", 0, "F", 2, 0),
            ("h4", 1, "QQ", "preflop", "open", 1, "A", 2, 0),
            ("h5", 1, "QQ", "preflop", "open", 0, "A", 2, 0),
            ("h6", 1, None, "preflop", "open", 0, "C", 2, 0),
            ("h7", 1, "JTs", "preflop", "3bet", 1, "R", 6, 0),
            ("h1", 1, "AKs", "flop", "check", 1, "B", 0, 1),
        ])

    def tearDown(self):
        self.con.close()

    def chart(self, where="1=1", stat="threebet", alternative="call"):
        self.assertIn("alternative", inspect.signature(query.chart_of).parameters,
                      "The chart needs a base-stat / alternative-action comparison")
        return query.chart_of(self.con, where, stat, alternative=alternative)

    def test_shared_chance_totals_include_unseen_cards(self):
        g = self.chart()
        self.assertEqual("comparison", g["mode"])
        self.assertEqual((5, 6), (g["seen"], g["total"]))
        self.assertEqual({"AKs": (3, 1), "QQ": (2, 1)}, g["cells"])
        self.assertEqual({"AKs": (3, 1), "QQ": (2, 1)}, g["alternative_cells"])
        self.assertEqual((6, 2, 3), g["totals"])
        self.assertEqual("call", g["alternative"])

    def test_same_filters_apply_to_both_actions(self):
        g = self.chart("is_hero=1")
        self.assertEqual((2, 1, 1), g["totals"])
        self.assertEqual({"AKs": (2, 1)}, g["alternative_cells"])
        self.assertEqual((2, 2), (g["seen"], g["total"]))

    def test_allin_raises_and_calls_are_disjoint(self):
        g = self.chart("combo='QQ'")
        self.assertEqual((2, 1, 1), g["totals"])

    def test_invalid_or_overlapping_comparisons_are_refused(self):
        for stat, alternative in ((None, "call"), ("vpip", "fold"),
                                  ("threebet", "raise"), ("threebet", "nonsense")):
            with self.subTest(stat=stat, alternative=alternative):
                with self.assertRaises(ValueError):
                    self.chart(stat=stat, alternative=alternative)

    def test_empty_filter_and_original_charts(self):
        g = self.chart("0=1")
        self.assertEqual({}, g["cells"])
        self.assertEqual((0, 0, 0), g["totals"])
        original = query.chart_of(self.con, "1=1", "threebet")
        self.assertEqual("rate", original["mode"])
        self.assertEqual(original["cells"], self.chart()["cells"])
        self.assertEqual("composition", query.chart_of(self.con, "1=1")["mode"])

    def test_zero_minimum_still_leaves_absent_combos_blank(self):
        self.chart()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            query.show_chart(self.con, "1=1", "test", "threebet", min_n=0,
                             alternative="call")
        self.assertIn(". n=0", output.getvalue())

    def test_cli_reports_counts_denominator_and_visibility(self):
        self.chart()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            query.show_chart(self.con, "1=1", "test", "threebet", alternative="call")
        text = output.getvalue()
        self.assertIn("5 of 6", text)
        self.assertIn("2/6", text)
        self.assertIn("3/6", text)
        self.assertIn("33/33", text)

    def test_canvas_draws_both_actions_and_labels_sample(self):
        g = self.chart()
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        canvas = tk.Canvas(root, width=700, height=700)
        host = app.App.__new__(app.App)
        host.chart_canvas, host.chart = canvas, g
        with patch.object(canvas, "winfo_width", return_value=700), \
                patch.object(canvas, "winfo_height", return_value=700):
            host._draw_chart()
        kinds = [canvas.type(i) for i in canvas.find_all()]
        self.assertEqual(171, kinds.count("rectangle"))
        fills = [canvas.itemcget(i, "fill") for i in canvas.find_all()
                 if canvas.type(i) == "rectangle"]
        self.assertEqual(1, fills.count(app.GOOD))
        self.assertEqual(1, fills.count(app.ACCENT))
        text = " ".join(canvas.itemcget(i, "text") for i in canvas.find_all()
                        if canvas.type(i) == "text")
        self.assertIn("2/6", text)
        self.assertIn("3/6", text)
        self.assertIn("5 of 6", text)
        left, top, size = host._chart_geometry
        event = SimpleNamespace(x=left + 1.5 * size, y=top + .5 * size)
        with patch.object(canvas, "winfo_width", return_value=700), \
                patch.object(canvas, "winfo_height", return_value=700):
            host._chart_hover(event)
        hint = " ".join(canvas.itemcget(i, "text") for i in canvas.find_withtag("hint")
                        if canvas.type(i) == "text")
        self.assertIn("AKs: n=3", hint)
        self.assertIn("3bet: 1/3", hint)
        self.assertIn("call: 1/3", hint)

    def test_command_handoff_keeps_chart_and_alternative(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        host = app.App.__new__(app.App)
        host.flags, host.multi, host.vals = {}, {}, {}
        host.tabs = {"chart": "chart"}
        host.nb, host.refresh, host.clear_filters = Mock(), Mock(), Mock()
        host.of, host.alternative = app.ttk.Combobox(root), app.ttk.Combobox(root)
        host.apply_argv(["--alternative", "call", "--show", "threebet", "--chart"])
        self.assertEqual("threebet", host.chart_stat())
        self.assertEqual("call", host.alternative.get())
        host.nb.select.assert_called_with("chart")

    def test_unseen_only_still_reports_both_totals(self):
        g = self.chart("combo IS NULL")
        self.assertEqual((0, 1), (g["seen"], g["total"]))
        self.assertEqual((1, 0, 1), g["totals"])
        self.assertTrue(app.App._any(g))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
