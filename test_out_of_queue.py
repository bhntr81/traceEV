"""Out-of-queue posts, against the synthetic hands read by hand."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importer
import query


SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"


class OutOfQueueChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hands.db"
        with contextlib.redirect_stdout(io.StringIO()):
            importer.load(sorted(SYNTHETIC.glob("*.txt")), db_path=self.db,
                          progress=None)
            importer.rebuild(db_path=self.db, progress=None)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)

    def hands(self, flag):
        where = query.build([flag])[0]
        return sorted(r[0] for r in self.con.execute(
            f"SELECT DISTINCT hand_id FROM decisions WHERE {where}"))

    def test_a_returning_big_blind_is_out_of_queue(self):
        # Hand 9: Dave posts a big blind from the cutoff to come back in.
        self.assertEqual(["ps-900000000009"], self.hands("--out-of-queue"))

    def test_a_straddle_is_not(self):
        # Hand 2: Carol straddles from UTG. That is a post outside the
        # blinds, and it is the straddle, not a post out of queue.
        self.assertIn("ps-900000000002", self.hands("--straddle"))
        self.assertIn("ps-900000000002", self.hands("--no-out-of-queue"))

    def test_the_two_halves_are_every_hand(self):
        both = set(self.hands("--out-of-queue")) | set(self.hands("--no-out-of-queue"))
        everything = {r[0] for r in self.con.execute(
            "SELECT DISTINCT hand_id FROM decisions")}
        self.assertEqual(everything, both)
        self.assertFalse(set(self.hands("--out-of-queue"))
                         & set(self.hands("--no-out-of-queue")))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
