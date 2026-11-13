"""ClubWPT Gold OCR records: a hand loads only when its own money adds up."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import clubwptocr
import importer
import sites

T0 = "2026-10-29T09:45:26+00:00"


def act(seq, street, seat, verb, total=None, t=T0):
    return {"sequence": seq, "street": street, "seat": seat, "action": verb,
            "amount": None, "street_total": total, "order_certain": False,
            "timestamp": t}


def record():
    """Four-handed, 0.01/0.02 in big blinds: BTN opens, BB calls, BTN wins the flop."""
    return {
        "schema_version": 1, "id": "local-1", "table_id": "HL7780", "hand_id": None,
        "table_title": " Ring Game (NLHE) | 0.01/0.02 CHIPS | HL6333",
        "started_at": T0, "ended_at": T0, "end_reason": "hand_boundary",
        "status": "needs_review", "review_reasons": ["starting_stacks_unverified"],
        "stakes": {"small_blind": "0.5", "big_blind": "1", "currency": "BB"},
        "button_seat": 4, "hero_seat": 1, "hero_cards": ["As", "8s"],
        "board": ["5h", "6d", "8c"], "street": "flop", "pot": "6",
        "players": [{"seat": s, "name": n, "starting_stack": "100", "stack": None,
                     "bet": None, "in_hand": None}
                    for s, n in ((1, "plsfoldy"), (2, "RG3 Rider"), (3, None),
                                 (4, "BruteAlien"), (6, "SatOut"))],
        "actions": [act(1, "preflop", 1, "post_small_blind", "0.5"),
                    act(2, "preflop", 2, "post_big_blind", "1"),
                    act(3, "preflop", 3, "fold"),
                    act(4, "preflop", 4, "raise", "2.5"),
                    act(5, "preflop", 1, "fold"),
                    act(6, "preflop", 2, "call", "2.5"),
                    act(7, "flop", 2, "check"),
                    act(8, "flop", 4, "bet", "3"),
                    act(9, "flop", 2, "fold")],
        "settlement": {"pot": "5.5", "rake": "0.5", "source": "stacks",
                       "collected": [{"seat": 4, "amount": "5"}],
                       "uncalled": {"seat": 4, "amount": "3"}},
    }


def parse(rec):
    clubwptocr.REJECTED.clear()
    return clubwptocr.parse_hand(json.dumps(rec), source="t.jsonl")


class Proves(unittest.TestCase):
    def test_a_hand_that_adds_up_is_stored_in_the_tables_unit(self):
        got = parse(record())
        hand = got["hand"]
        self.assertEqual(hand["hand_id"], "cwo-HL6333-20261029T094526")
        self.assertEqual((hand["sb"], hand["bb"]), (0.01, 0.02))
        self.assertAlmostEqual(hand["pot"], 0.11)
        self.assertAlmostEqual(hand["rake"], 0.01)
        self.assertEqual(hand["standard"], 1)
        seats = {s["seat"]: s for s in got["seats"]}
        # Seat 6 is listed and never acted: sitting out, not dealt in.
        self.assertEqual(sorted(seats), [1, 2, 3, 4])
        self.assertEqual(seats[4]["position"], "BTN")
        self.assertAlmostEqual(seats[4]["invested"], 0.05)   # the 0.06 bet came back
        self.assertAlmostEqual(seats[4]["won"], 0.10)
        self.assertEqual(seats[1]["cards"], "As 8s")
        self.assertEqual(seats[3]["label"], "~HL6333#3")
        letters = [a["action"] for a in got["actions"]]
        self.assertEqual(letters, ["F", "R", "F", "C", "X", "B", "F"])

    def test_money_in_must_equal_money_out(self):
        rec = record()
        rec["settlement"]["collected"][0]["amount"] = "4"
        self.assertIsNone(parse(rec))
        self.assertIn("pot is not what came out", clubwptocr.REJECTED)

    def test_an_unread_bet_turns_the_hand_away(self):
        rec = record()
        rec["actions"][3]["street_total"] = None
        self.assertIsNone(parse(rec))
        self.assertIn("amount unread", clubwptocr.REJECTED)

    def test_a_missed_action_shows_as_an_impossible_one(self):
        rec = record()
        rec["actions"].append(act(10, "flop", 1, "call", "3"))
        self.assertIsNone(parse(rec))
        self.assertIn("action after folding", clubwptocr.REJECTED)

    def test_a_showdown_without_a_settlement_has_no_winner(self):
        rec = record()
        rec["actions"][-1] = act(9, "flop", 2, "call", "3")
        del rec["settlement"]
        self.assertIsNone(parse(rec))

    def test_an_uncontested_preflop_pot_settles_itself_unraked(self):
        rec = record()
        rec["board"], rec["street"] = [], "preflop"
        rec["actions"] = rec["actions"][:5] + [act(6, "preflop", 2, "fold")]
        del rec["settlement"]
        got = parse(rec)
        self.assertEqual(got["hand"]["rake"], 0.0)
        # 0.5 + 1 + the 2.5 raise less the 1.5 nobody called: 2.5 BB.
        self.assertAlmostEqual({s["seat"]: s for s in got["seats"]}[4]["won"], 0.05)

    def test_a_garbled_or_doubted_name_names_nobody(self):
        rec = record()
        rec["players"][1]["name"] = "StumpCc:ayoi:eZ..I"
        rec["identity_conflicts"] = [{"seat": 4, "observed_name": "Brute"}]
        labels = {s["seat"]: s["label"] for s in parse(rec)["seats"]}
        self.assertEqual(labels[2], "~HL6333#2")
        self.assertEqual(labels[4], "~HL6333#4")
        self.assertEqual(labels[1], "plsfoldy")

    def test_a_new_players_post_is_money_in_and_the_hand_not_standard(self):
        # stream5 17:20:44: seat 8 posts 1 BB out of turn and checks its option.
        rec = record()
        rec["players"].append({"seat": 5, "name": "NewGuy", "starting_stack": "100", "stack": None,
                               "bet": None, "in_hand": None})
        rec["actions"].insert(2, act(3, "preflop", 5, "post_unknown", "1"))
        rec["actions"].insert(6, act(6, "preflop", 5, "fold"))
        for n, a in enumerate(rec["actions"], 1):
            a["sequence"] = n
        rec["settlement"]["pot"], rec["settlement"]["collected"][0]["amount"] = "6.5", "6"
        got = parse(rec)
        self.assertEqual(got["hand"]["standard"], 0)
        self.assertAlmostEqual({s["seat"]: s for s in got["seats"]}[5]["posted"], 0.02)
        self.assertEqual(clubwptocr.UNKNOWN.get("post_unknown"), None)

    def test_an_unread_post_is_money_of_unknown_size(self):
        rec = record()
        rec["actions"].insert(2, act(3, "preflop", 3, "post_unknown"))
        for n, a in enumerate(rec["actions"], 1):
            a["sequence"] = n
        self.assertIsNone(parse(rec))
        self.assertIn("unresolved post", clubwptocr.REJECTED)

    def test_a_capture_that_stopped_is_half_a_hand(self):
        rec = record()
        rec["end_reason"] = "tracking_stopped"
        self.assertIsNone(parse(rec))


class Loads(unittest.TestCase):
    def test_tracker_output_is_sniffed_loaded_and_read_again_as_known(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "2026-10-29 HL6333.jsonl"
            bad = record()
            bad["end_reason"] = "tracking_stopped"
            # Sorted keys, as the tracker writes them; the sniffer reads the
            # first key because a record is far longer than what it reads.
            path.write_text(json.dumps(record(), sort_keys=True) + "\n" +
                            json.dumps(bad, sort_keys=True) + "\n", encoding="utf-8")
            self.assertEqual(importer.sniff(path), "clubwptocr")
            db = Path(folder) / "t.db"
            first = importer.load([folder], db)
            self.assertEqual(first["by_site"], {"clubwptocr": 1})
            self.assertEqual(importer.load([folder], db)["known"], 1)
            with closing(sqlite3.connect(db)) as con:
                site, = con.execute("SELECT site FROM hands").fetchone()
            self.assertEqual(site, "clubwptocr")

    def test_reviewed_histories_still_go_to_their_own_parser(self):
        self.assertFalse(clubwptocr.HEADER('{"format":"clubwptgold","version":1}'))
        self.assertIs(sites.BY_KEY["clubwptocr"].module, clubwptocr)


if __name__ == "__main__":
    unittest.main()
