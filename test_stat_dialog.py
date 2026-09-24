"""Exercise the desktop stat editor against the engine, using temporary saves."""
import queue
from contextlib import closing
import sqlite3
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
import stats


class StatDialogChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep complete real hands but not the whole user's corpus: these
        # checks prove the UI's contract, not population estimates.
        cls.fixture = tempfile.TemporaryDirectory()
        cls.db = Path(cls.fixture.name) / "hands.db"
        with closing(sqlite3.connect(cls.db)) as con:
            con.execute("ATTACH DATABASE ? AS source", (str(app.DB),))
            con.execute("CREATE TEMP TABLE chosen AS SELECT hand_id FROM source.hands LIMIT 100")
            for table in ("hands", "seats", "spots", "decisions"):
                sql = con.execute("SELECT sql FROM source.sqlite_master WHERE type='table' AND name=?",
                                  (table,)).fetchone()[0]
                con.execute(sql)
                column = "hand_id"
                con.execute(f"INSERT INTO {table} SELECT * FROM source.{table} "
                            f"WHERE {column} IN (SELECT hand_id FROM chosen)")
            con.commit()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        app.dark(self.root)
        self.root.update_idletasks()
        self.previous = stats.CUSTOM
        for module in (app, app.query, stats):
            patch.object(module, "DB", self.db).start()
        self.temp = tempfile.TemporaryDirectory()
        stats.load_custom(Path(self.temp.name) / "stats.json")
        self.host = SimpleNamespace(master=self.root,
                                    argv=lambda: ["--street", "river"],
                                    refresh=Mock(), reload_stat_choices=Mock(),
                                    cache={"old": "answer"},
                                    requests=queue.Queue())
        self.dialog = app.StatDialog(self.host)
        self.dialog.withdraw()
        self.errors = patch.object(app.messagebox, "showerror").start()

    def tearDown(self):
        patch.stopall()
        self.dialog.destroy()
        self.root.destroy()
        stats.load_custom(self.previous)
        self.temp.cleanup()

    def expression(self, formula="Value(vpip)", key="test_formula"):
        self.assertTrue(hasattr(self.dialog, "kind"),
                        "Save as Stat must offer an expression mode")
        self.dialog.kind.set("expression")
        self.dialog._mode()
        self.dialog.key.set(key)
        self.dialog.formula.set(formula)
        self.dialog.save()
        self.finish_save()

    def finish_save(self):
        while not self.host.requests.empty():
            self.host.requests.get_nowait()()
        deadline = time.monotonic() + 5
        while self.dialog.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.dialog.busy, "Save result was not delivered to the dialog")

    def test_save_is_queued_and_duplicate_clicks_do_not_queue_twice(self):
        self.dialog.kind.set("expression")
        self.dialog.key.set("test_queued")
        self.dialog.formula.set("Value(vpip)")
        self.dialog.save()
        self.assertTrue(self.dialog.busy)
        self.assertFalse(stats.CUSTOM.exists())
        self.assertEqual(1, self.host.requests.qsize())
        self.dialog.save()
        self.assertEqual(1, self.host.requests.qsize())
        self.finish_save()
        self.errors.assert_not_called()

    def test_worker_preserves_saves_while_coalescing_views(self):
        saved = Mock()
        old = (1, "old", "stats")
        new = (2, "new", "stats")
        requests = Mock()
        requests.get.side_effect = [old, StopIteration]
        requests.get_nowait.side_effect = [saved, new, queue.Empty]
        worker = SimpleNamespace(requests=requests, pending=2, cache={},
                                 _work=Mock(return_value={"rows": []}))
        with self.assertRaises(StopIteration):
            app.App._worker(worker)
        saved.assert_called_once_with()
        worker._work.assert_called_once_with(2, "stats")

    def test_expression_choices_refresh_after_save_and_forget(self):
        self.expression()
        self.host.expression = app.ttk.Combobox(self.root)
        self.host.of = app.ttk.Combobox(self.root)
        self.host.expression.set("test_formula")
        app.App.reload_stat_choices(self.host)
        self.assertIn("test_formula", self.host.expression.cget("values"))
        self.assertEqual("test_formula", self.host.expression.get())
        stats.forget("test_formula")
        app.App.reload_stat_choices(self.host)
        self.assertEqual("", self.host.expression.get())
        self.assertNotIn("test_formula", self.host.expression.cget("values"))

    def test_expression_round_trip_and_forget(self):
        self.expression()
        self.errors.assert_not_called()
        saved = stats.EXPR_BY_KEY["test_formula"]
        with closing(sqlite3.connect(app.DB)) as con:
            value, n = stats.evaluate(con, saved)
        self.assertIn(f"{value:.2f}", self.dialog.result.cget("text"))
        self.assertIn(f"{n:,}", self.dialog.result.cget("text"))
        self.assertIn("test_formula", self.dialog.saved.cget("values"))
        self.assertEqual({}, self.host.cache)
        stats.load_custom()
        self.assertEqual("Value(vpip)", stats.EXPR_BY_KEY["test_formula"].formula)
        self.dialog.saved.set("test_formula")
        self.dialog.forget()
        self.assertNotIn("test_formula", stats.EXPR_BY_KEY)

    def test_invalid_formula_does_not_replace_saved_stat(self):
        self.expression()
        before = stats.CUSTOM.read_bytes()
        for formula in ("", "Value(", "Value(no_such_stat)", "__import__('os')"):
            self.errors.reset_mock()
            self.dialog.formula.set(formula)
            self.dialog.save()
            self.finish_save()
            self.errors.assert_called_once()
            self.assertEqual(before, stats.CUSTOM.read_bytes())

    def test_bad_names_and_builtin_collisions_are_refused(self):
        for key in ("", "not a key", "vpip", "cbet_profit"):
            self.errors.reset_mock()
            self.expression(key=key)
            self.errors.assert_called_once()
            self.assertFalse(stats.CUSTOM.exists())

    def test_undefined_result_is_not_reported_as_zero(self):
        self.expression("Cases(vpip) / 0")
        self.errors.assert_not_called()
        self.assertIn("undefined", self.dialog.result.cget("text").lower())

    def test_plain_stat_still_uses_screen_filter(self):
        self.dialog.key.set("test_plain")
        self.dialog.save()
        self.errors.assert_not_called()
        self.assertEqual(app.query.build(self.host.argv())[0],
                          stats.BY_KEY["test_plain"].chance)

    def test_stats_worker_and_render_use_filtered_formula_without_percent(self):
        self.expression("Cases(vpip) / Opps(vpip)")
        host = app.App.__new__(app.App)
        host.results = queue.Queue()
        out = host._work(1, "stats", "street='preflop' AND is_hero=1", "hero",
                         [], "position", None, "test_formula")
        self.assertNotIn("error", out)
        self.assertIn("expression", out,
                      "Stats view must return the requested expression")
        with closing(sqlite3.connect(app.DB)) as con:
            expected = stats.evaluate(con, stats.EXPR_BY_KEY["test_formula"],
                                      "street='preflop' AND is_hero=1")
        self.assertEqual(expected, out["expression"][1:])
        tv = app.ttk.Treeview(self.root)
        host._render_stats(tv, out)
        values = [tv.item(i, "values") for i in tv.get_children()]
        row = next(r for r in values if r[0] == "test formula")
        self.assertEqual(f"{expected[0]:.2f}", row[1])
        self.assertEqual("", row[2])


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
