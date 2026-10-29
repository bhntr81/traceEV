"""Import while playing: a session written into a folder a hand at a time."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importer


FIXTURE = (Path(__file__).parent / "fixtures" / "synthetic" /
           "NLHE-6max-USD-0.05-0.10-202610.cold.fourbet.txt")


class WatcherChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = root / "hands.db"
        self.place = root / "HandHistory"
        self.place.mkdir()
        self.file = self.place / "ColdFour.txt"
        text = FIXTURE.read_text()
        second = text.index("PokerStars Game #900000000008")
        self.first, self.second = text[:second], text[second:]
        self.watch = importer.Watcher(self.db)
        # The places themselves are `scan`'s business and are the user's
        # folders; the test names its own and says it was just found.
        self.watch.places, self.watch.found_at = [self.place], 0.0
        self.clock = 0.0

    def poll(self):
        self.clock += 2
        with contextlib.redirect_stdout(io.StringIO()):
            return self.watch.poll(now=self.clock)

    def write(self, text):
        with open(self.file, "a") as f:
            f.write(text)

    def hands(self):
        con = sqlite3.connect(self.db)
        try:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT hand_id FROM decisions ORDER BY 1")]
        finally:
            con.close()

    def test_a_session_arrives_a_hand_at_a_time(self):
        self.write(self.first)
        self.assertEqual(0, self.poll()["added"])   # just written: wait
        self.assertEqual(1, self.poll()["added"])   # held still: read
        self.assertEqual(["ps-900000000007"], self.hands())

        # The client is half way through writing the next hand.
        cut = self.second.index("*** SUMMARY ***")
        self.write(self.second[:cut])
        self.assertEqual(0, self.poll()["files"])   # moving: not read
        got = self.poll()                           # still, but unfinished
        self.assertEqual((0, 1), (got["added"], got["unfinished"]))

        self.write(self.second[cut:])
        self.poll()
        got = self.poll()
        self.assertEqual(1, got["added"])
        self.assertEqual(["ps-900000000007", "ps-900000000008"], self.hands())

        # Nothing moved, so nothing is read.
        self.assertEqual(0, self.poll()["files"])

    def test_files_an_earlier_import_read_are_left_alone(self):
        self.write(self.first + self.second)
        with contextlib.redirect_stdout(io.StringIO()):
            importer.load([self.file], db_path=self.db, progress=None)
        self.poll()
        self.assertEqual(0, self.poll()["files"])


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
