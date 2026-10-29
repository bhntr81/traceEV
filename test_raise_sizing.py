"""Raise sizes and effective stacks against chips counted by hand."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import importer
import query
import sites


FIXTURE = (Path(__file__).parent / "fixtures" / "synthetic" /
           "NLHE-2max-USD-0.05-0.10-202610.raise.sizing.txt")


class RaiseSizingChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hands.db"
        got = importer.load([FIXTURE], db_path=self.db, progress=None)
        self.assertEqual(3, got["added"])
        with contextlib.redirect_stdout(io.StringIO()):
            importer.rebuild(db_path=self.db, progress=None)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def fractions(self, hand, street):
        return self.con.execute(
            "SELECT pot_frac FROM decisions WHERE hand_id=? AND street=? "
            "AND agg=1 ORDER BY n", (hand, street)).fetchall()

    def test_blinds_and_previous_raises_are_not_new_raise_money(self):
        # The BB adds 0.80: 0.20 calls and 0.60 raises into a 0.60 pot.
        # The SB then adds 1.80: 0.60 calls and 1.20 raises into 1.80.
        # The opening SB adds 0.25: 0.05 calls and 0.20 raises into 0.20.
        self.assertEqual([(1.0,), (1.0,), (0.6667,)],
                         self.fractions("ps-900000000003", "preflop"))

    def test_flop_reraise_uses_only_money_above_the_call(self):
        # Pot 1.80, call 0.60, another 0.90 on top: 0.90 / 2.40.
        self.assertEqual([(0.5,), (0.5,), (0.375,)],
                         self.fractions("ps-900000000004", "flop"))

    def test_legacy_bets_have_the_same_hand_worked_sizes(self):
        self.assertEqual([(1.0,), (1.0,), (0.6667,), (1.0,),
                          (0.5,), (0.5,), (0.375,), (1.0,), (1.0,)], self.con.execute(
            "SELECT pot_frac FROM bets ORDER BY hand_id, n").fetchall())

    def test_small_raise_filter_and_sized_line_select_the_reraise(self):
        where, _label, _parts = query.build(["--street", "flop", "--size", "0.3-0.4"])
        self.assertEqual([("ps-900000000004", 5)], self.con.execute(
            f"SELECT hand_id, n FROM decisions WHERE {where}").fetchall())
        self.assertEqual([("BmRmRsF",)], self.con.execute(
            "SELECT DISTINCT flop_sz FROM decisions "
            "WHERE hand_id='ps-900000000004'").fetchall())

    def test_incremental_import_matches_the_hand_worked_sizes(self):
        self.con.close()
        # A two-hand update on three fixtures normally chooses a rebuild.
        # Raise only that performance threshold to exercise the real update.
        with patch.object(importer, "BULK", 1.0), contextlib.redirect_stdout(io.StringIO()):
            mode = importer.update(["ps-900000000003", "ps-900000000004"],
                                   db_path=self.db, progress=None)
        self.assertEqual("updated", mode)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.assertEqual([(1.0,), (1.0,), (0.6667,)],
                         self.fractions("ps-900000000003", "preflop"))
        self.assertEqual([(0.5,), (0.5,), (0.375,)],
                         self.fractions("ps-900000000004", "flop"))
        self.assertEqual([(9.7,), (9.4,)], self.con.execute(
            "SELECT eff_stack FROM decisions "
            "WHERE hand_id='ps-900000000004' AND n IN (4,5) ORDER BY n").fetchall())

    def test_allin_without_raise_to_uses_the_added_money(self):
        # The same shove can arrive as R with a total or A with only the
        # 0.80 added. Both raise 0.60 above a 0.20 call into a 0.60 pot.
        self.assertEqual([(1.0, 1)], self.con.execute(
            "SELECT pot_frac, allin FROM decisions "
            "WHERE hand_id='ps-900000000005' AND n=2").fetchall())
        self.con.execute("UPDATE actions SET action='A', total=NULL "
                         "WHERE hand_id='ps-900000000005' AND n=2")
        self.con.commit()
        self.con.close()
        with patch.object(importer, "BULK", 1.0), contextlib.redirect_stdout(io.StringIO()):
            mode = importer.update(["ps-900000000005"], db_path=self.db, progress=None)
        self.assertEqual("updated", mode)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.assertEqual([(1.0, 1)], self.con.execute(
            "SELECT pot_frac, allin FROM decisions "
            "WHERE hand_id='ps-900000000005' AND n=2").fetchall())
        self.assertEqual([(1.0,)], self.con.execute(
            "SELECT pot_frac FROM bets "
            "WHERE hand_id='ps-900000000005' AND n=2").fetchall())
        self.assertEqual([(0.6,)], self.con.execute(
            "SELECT eff_stack FROM decisions "
            "WHERE hand_id='ps-900000000005' AND n=3").fetchall())

    def test_fixture_money_positions_and_blinds_prove(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(sites.check(db_path=self.db))

    def test_facing_a_shove_has_the_call_left_at_risk(self):
        # Villain has shoved to 0.90 and hero already has 0.30 in.
        # Villain has no chips behind, but hero can still lose 0.60.
        self.assertEqual([(0.6, 6.0, 0.5)], self.con.execute(
            "SELECT eff_stack, eff_bb, spr FROM decisions "
            "WHERE hand_id='ps-900000000005' AND n=3").fetchall())
        self.assertEqual([(0.8,)], self.con.execute(
            "SELECT eff_stack FROM decisions "
            "WHERE hand_id='ps-900000000005' AND n=2").fetchall())
        where, _label, _parts = query.build(["--depth", "5-7", "--street", "preflop"])
        self.assertEqual([("ps-900000000005", 3)], self.con.execute(
            f"SELECT hand_id, n FROM decisions WHERE {where}").fetchall())

    def test_effective_stack_counts_bets_but_not_previous_streets(self):
        # Both start the flop with 9.70. The first bet remains available
        # to the responder; after hero's bet only 9.40 of hero's stack is
        # left at risk facing the raise. Preflop's 0.30 is already spent.
        self.assertEqual([(3, 9.7), (4, 9.7), (5, 9.4)], self.con.execute(
            "SELECT n, eff_stack FROM decisions "
            "WHERE hand_id='ps-900000000004' AND street='flop' "
            "AND agg=1 ORDER BY n").fetchall())


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
