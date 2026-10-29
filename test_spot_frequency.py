"""Spot frequency per 1000 hands, against the synthetic hands counted by hand."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importer
import query


SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"


class SpotFrequencyChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hands.db"
        got = importer.load(sorted(SYNTHETIC.glob("*.txt")), db_path=self.db,
                            progress=None)
        self.assertEqual(7, got["added"])
        with contextlib.redirect_stdout(io.StringIO()):
            importer.rebuild(db_path=self.db, progress=None)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def frequency(self, argv):
        where, _label, _parts = query.build(argv)
        return query.frequency_of(self.con, where, query.sample_of(argv))

    def test_every_hand_first_in(self):
        # Hero is first in once in each of the seven hands: folds in hands
        # 1, 7 and 8, raises in 2, 3, 4 and 5.
        self.assertEqual({"hands": 7, "spot": 7,
                          "actions": {"raise": 4, "fold": 3}},
                         self.frequency(["--hero", "--street", "preflop",
                                         "--facing", "unopened"]))

    def test_the_hands_counted_are_the_same_games(self):
        # 5 Oct holds hands 7 and 8 only. Dividing by all seven of hero's
        # hands would say the spot comes up 286 times per 1000, not 1000.
        self.assertEqual({"hands": 2, "spot": 2, "actions": {"fold": 2}},
                         self.frequency(["--hero", "--street", "preflop",
                                         "--facing", "unopened",
                                         "--since", "2026-10-05"]))

    def test_only_game_flags_reach_the_sample(self):
        self.assertEqual("1=1", query.sample_of(["--hero", "--pos", "BTN"]))
        self.assertEqual(query.build(["--site", "acr", "--standard"])[0],
                         query.sample_of(["--site", "acr", "--pos", "BTN",
                                          "--standard", "--street", "flop"]))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
