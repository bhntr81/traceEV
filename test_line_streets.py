"""Line patterns keep each wildcard inside its own street."""

import sqlite3
import unittest

import query


OWN = ["R", "RC/XR", "RC/XR/B", "C/XC/XR", "C/XC/X/XR", "C/B", "C/B/B/B",
       "R/BF", "C/XC/BF", "C/BC/BC/XC", "C/B/X", "R/BR/B/B"]


class LineStreetChecks(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.execute("CREATE TABLE decisions (own TEXT, own_node TEXT)")
        self.con.executemany("INSERT INTO decisions (own) VALUES (?)",
                             [(o,) for o in OWN])

    def match(self, *argv):
        where = query.build(list(argv))[0]
        return sorted(r[0] for r in self.con.execute(
            f"SELECT own FROM decisions WHERE {where}"))

    def test_check_raise_flop_is_the_flop(self):
        # `C/XC/XR` and `C/XC/X/XR` check-raised the turn and the river;
        # a bare GLOB let the leading `*` swallow the flop to reach them.
        self.assertEqual(["RC/XR", "RC/XR/B"], self.match("--my-line", "*/XR*"))

    def test_bet_fold_flop_ended_on_the_flop(self):
        self.assertEqual(["R/BF"], self.match("--my-line", "*/BF"))

    def test_a_street_written_whole_was_the_last_action_on_it(self):
        # `*/B/` is "bet the flop, and that was all on the flop": a flop
        # bet that was raised and re-raised (`BR`) is not in it, and a turn
        # bet after a flop check is not either. `*/B*` is "began the flop
        # with a bet", whatever followed it on the flop.
        self.assertEqual(["C/B", "C/B/B/B", "C/B/X"],
                         self.match("--my-line", "*/B/"))
        self.assertEqual(["C/B", "C/B/B/B", "C/B/X", "C/BC/BC/XC", "R/BF",
                          "R/BR/B/B"],
                         self.match("--my-line", "*/B*"))

    def test_streets_in_the_middle_hold_still(self):
        self.assertEqual(["C/BC/BC/XC"], self.match("--my-line", "*/*C/*C/*C"))
        self.assertEqual(["C/B/B/B"], self.match("--my-line", "*/B/B/B"))

    def test_a_pattern_without_streets_is_unchanged(self):
        self.assertEqual(["C/XC/X/XR", "C/XC/XR", "RC/XR", "RC/XR/B"],
                         self.match("--my-line", "*XR*"))
        self.assertEqual(["R"], self.match("--my-line", "R"))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
