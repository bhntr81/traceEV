"""The overlay, over a stand-in table window. Needs a display; skips without."""

import contextlib
import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import hud_overlay
import importer

try:
    import tkinter as tk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception:            # no display: nothing here can run
    ROOT = None

SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"
COLD = SYNTHETIC / "NLHE-6max-USD-0.05-0.10-202610.cold.fourbet.txt"


@unittest.skipIf(ROOT is None, "no display")
class OverlayChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = root / "hands.db"
        with contextlib.redirect_stdout(io.StringIO()):
            importer.load(sorted(SYNTHETIC.glob("*.txt")), db_path=self.db,
                          progress=None)
            importer.rebuild(db_path=self.db, progress=None)
        # The table window: a plain window titled the way a client titles one.
        self.rect = (100, 100, 900, 700)
        self.open = True
        self.overlay = hud_overlay.Overlay(
            ROOT, self.db, windows=self.windows, layout_path=root / "hud.json")
        self.addCleanup(self.overlay.stop)

    def windows(self):
        return ([(1, "ColdFour - $0.05/$0.10 USD - No Limit Hold'em", self.rect),
                 (2, "TraceEV", (0, 0, 50, 50))] if self.open else [])

    def wait(self, test, seconds=5.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ROOT.update()
            if test():
                return True
            time.sleep(0.02)
        return False

    def table(self):
        return self.overlay.tables.get(("pokerstars", "ColdFour"))

    def texts(self):
        t = self.table()
        return {s: p.label.cget("text") for s, p in t["panels"].items()} if t else {}

    def test_panels_sit_round_the_table_and_follow_it(self):
        self.assertTrue(self.wait(lambda: len(self.texts()) == 6))
        self.assertNotIn(("pokerstars", "TraceEV"), self.overlay.tables)
        # Small, seat 5: VPIP 2 of 5 hands, so "5" closes the first line.
        self.assertTrue(self.texts()[5].splitlines()[0].endswith("  5"))
        p = self.table()["panels"][1]
        fx, fy = hud_overlay.ring(6, 1)
        self.assertTrue(self.wait(lambda: (p.winfo_x(), p.winfo_y()) ==
                                  (int(100 + fx * 800), int(100 + fy * 600))))
        self.rect = (300, 200, 1100, 800)
        self.assertTrue(self.wait(lambda: (p.winfo_x(), p.winfo_y()) ==
                                  (int(300 + fx * 800), int(200 + fy * 600))))

    def test_a_dragged_panel_keeps_its_place(self):
        self.assertTrue(self.wait(lambda: len(self.texts()) == 6))
        self.overlay.moved(("pokerstars", "ColdFour", 2), 500, 400)
        self.assertEqual([0.5, 0.5], self.overlay.layout["pokerstars|6|2"])
        again = hud_overlay.load_layout(Path(self.tmp.name) / "hud.json")
        self.assertEqual([0.5, 0.5], again["pokerstars|6|2"])

    def test_a_new_hand_redraws_and_a_closed_table_goes(self):
        self.assertTrue(self.wait(lambda: len(self.texts()) == 6))
        new = COLD.read_text().split("PokerStars Game #900000000008")[0]
        new = new.replace("900000000007", "930000000001").replace(
            "Carol", "Zed").replace("12:00:00", "12:30:00")
        f = Path(self.tmp.name) / "next.txt"
        f.write_text(new)
        with contextlib.redirect_stdout(io.StringIO()):
            got = importer.load([f], db_path=self.db, progress=None)
            importer.update(got["ids"], db_path=self.db, progress=None)
        self.assertTrue(self.wait(
            lambda: self.table()["hand"] == "ps-930000000001"
            and self.texts()[2].splitlines()[0].endswith("  1")))
        self.open = False
        self.assertTrue(self.wait(lambda: self.table() is None))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
