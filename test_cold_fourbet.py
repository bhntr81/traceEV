"""Cold 4-bets against two hands whose seats are counted by hand."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importer
import stats


FIXTURE = (Path(__file__).parent / "fixtures" / "synthetic" /
           "NLHE-6max-USD-0.05-0.10-202610.cold.fourbet.txt")


class ColdFourbetChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hands.db"
        got = importer.load([FIXTURE], db_path=self.db, progress=None)
        self.assertEqual(2, got["added"])
        with contextlib.redirect_stdout(io.StringIO()):
            importer.rebuild(db_path=self.db, progress=None)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def seats(self, key, took):
        s = stats.BY_KEY[key]
        cond = s.action if took else "1=1"
        return sorted(self.con.execute(
            f"SELECT hand_id, player FROM decisions "
            f"WHERE ({s.chance}) AND ({cond})").fetchall())

    def test_chances_are_the_seats_with_nothing_in_but_a_blind(self):
        # Hand 7: Dave's 3bet reaches Button and Small untouched; Big acts
        # after Small's 4bet, so Big is facing a 4bet and has no chance here,
        # and Carol opened, so she faces it as the raiser, not cold. Hand 8:
        # Carol opened and Dave called the open, so only the blinds are cold.
        self.assertEqual([("ps-900000000007", "Button"),
                          ("ps-900000000007", "Small"),
                          ("ps-900000000008", "Big"),
                          ("ps-900000000008", "Small")],
                         self.seats("cold_fourbet", took=False))

    def test_the_small_blind_cold_4bets_and_the_flatter_does_not(self):
        # Dave's raise in hand 8 is a 4bet, but he had called the open first:
        # it belongs to neither `cold_fourbet` nor `fourbet`.
        self.assertEqual([("ps-900000000007", "Small")],
                         self.seats("cold_fourbet", took=True))
        n, k, *_ = stats.rate(self.con, "cold_fourbet")
        self.assertEqual((4, 1), (n, k))

    def test_a_cold_seat_is_never_counted_as_the_3bet_raiser_too(self):
        cold = set(self.seats("cold_fourbet", took=False))
        hot = set(self.seats("fourbet", took=False))
        self.assertFalse(cold & hot)


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
