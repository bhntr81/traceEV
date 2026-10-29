"""The HUD's numbers, against the synthetic tables counted by hand."""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import hud
import importer
import players
import stats


SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"


class HudChecks(unittest.TestCase):
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
        self.table = hud.seated(self.con, "pokerstars", "ColdFour")
        self.panels = {p["player"]: p for p in hud.panels(self.con, self.table)}

    def test_the_table_is_its_latest_hand(self):
        self.assertEqual("ps-900000000008", self.table["hand_id"])
        self.assertEqual(
            [(1, "Hero", True), (2, "Carol", False), (3, "Dave", False),
             (4, "Button", False), (5, "Small", False), (6, "Big", False)],
            [(s["seat"], s["player"], s["is_hero"]) for s in self.table["seats"]])

    def test_counts_are_the_players_own(self):
        # "Small" is in the small blind in hands 1, 2, 7 and 8: 3-bets in 1,
        # folds in 2, cold 4-bets in 7, folds to the squeeze in 8.
        small = self.panels["Small"]
        self.assertEqual(4, small["hands"])
        self.assertEqual((4, 2), (small["stats"]["vpip"]["n"],
                                  small["stats"]["vpip"]["k"]))

    def test_the_pool_is_pool_for_exactly(self):
        # Subtracting a player from the totals must give what asking for
        # everybody but them gives -- for every seat and every stat.
        for name, p in self.panels.items():
            if p["is_hero"]:
                continue
            where, params = players.pool_for(self.con, name, "pokerstars")
            want = hud.counts(self.con, where, params)
            for key, got in p["stats"].items():
                pn, pk = want[key]
                self.assertEqual(pk / pn if pn else None, got["pool"],
                                 f"{name} {key}")
                if got["n"]:
                    self.assertEqual(stats.shrunk(got["k"], got["n"], got["pool"]),
                                     got["shown"])

    def test_no_chance_is_a_dash_not_the_pool(self):
        # Carol opened both ColdFour hands and folded to the 3-bet in hand 8
        # but never had a c-bet chance anywhere.
        carol = self.panels["Carol"]
        self.assertEqual(0, carol["stats"]["cbet_flop"]["n"])
        self.assertIsNone(carol["stats"]["cbet_flop"]["shown"])
        self.assertEqual("-", hud.text(carol)[1].split("/")[1])

    def test_a_seat_without_a_person_shows_nothing(self):
        table = {"site": "ignition", "seats": [
            {"seat": 1, "player": "t:1:0", "is_hero": False}]}
        p = hud.panels(self.con, table)[0]
        self.assertEqual({}, p["stats"])
        self.assertEqual(["-"], hud.text(p))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
