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


class Antes(unittest.TestCase):
    def ante_record(self):
        # 0.05/0.10 (0.05): half a big blind each, in the pot before any frame.
        rec = record()
        rec['table_title'] = ' Ring Game (NLHE) | 0.05/0.10 (0.05) CHIPS | HL2644'
        # Four dealt seats, 0.5 BB each: the settled pot holds 2 BB of antes.
        rec['settlement']['pot'] = '7.5'
        rec['settlement']['collected'][0]['amount'] = '7'
        return rec

    def test_every_dealt_seat_posts_the_ante_and_the_money_adds_up(self):
        got = parse(self.ante_record())
        self.assertIsNotNone(got, clubwptocr.REJECTED)
        seats = {s['seat']: s for s in got['seats']}
        self.assertEqual(sorted(s['ante'] for s in seats.values()), [0.05] * 4)
        self.assertAlmostEqual(seats[3]['posted'], 0.05)         # folded, posted only the ante
        self.assertAlmostEqual(seats[1]['posted'], 0.10)         # small blind 0.05 + ante
        self.assertAlmostEqual(got['hand']['pot'], 0.75)
        self.assertAlmostEqual(got['hand']['ante'], 0.05)

    def test_without_the_antes_in_the_settled_pot_the_hand_is_refused(self):
        rec = self.ante_record()
        rec['settlement']['pot'], rec['settlement']['collected'][0]['amount'] = '5.5', '5'
        self.assertIsNone(parse(rec))

    def test_ante_hands_are_written_with_ante_lines_and_read_back_the_same(self):
        import clubwptocr_ps
        import pokerstars
        parsed = parse(self.ante_record())
        text = clubwptocr_ps.write_hand(parsed)
        self.assertEqual(text.count('posts the ante $0.05'), 4)
        self.assertIn('plsfoldy: posts small blind $0.05', text)
        self.assertIsNone(clubwptocr_ps._same(parsed, pokerstars.parse_hand(text)))


class PokerStarsText(unittest.TestCase):
    def test_a_hand_is_written_as_pokerstars_and_reads_back_the_same(self):
        import clubwptocr_ps
        import pokerstars
        parsed = parse(record())
        text = clubwptocr_ps.write_hand(parsed)
        self.assertTrue(text.startswith('PokerStars Hand #'))
        self.assertIn("Table 'ClubWPT HL6333' 8-max Seat #4 is the button", text)
        self.assertIn('BruteAlien: raises $0.03 to $0.05', text)
        self.assertIn('Uncalled bet ($0.06) returned to BruteAlien', text)
        self.assertIn('Total pot $0.11 | Rake $0.01', text)
        self.assertIsNone(clubwptocr_ps._same(parsed, pokerstars.parse_hand(text)))

    def test_the_hand_number_is_stable_and_all_digits(self):
        import clubwptocr_ps
        one = clubwptocr_ps.hand_number('cwo-HL6333-20261029T094526')
        self.assertEqual(one, clubwptocr_ps.hand_number('cwo-HL6333-20261029T094526'))
        self.assertTrue(0 <= one < 10 ** 12)

    def test_a_missing_start_is_worked_back_from_the_settled_close(self):
        rec = record()
        rec['players'][3]['starting_stack'] = None   # BruteAlien, who won
        rec['players'][3]['stack'] = '102.5'          # 100 - 2.5 - 3 + 3 back + 5 won... settled close
        got = {s['seat']: s for s in parse(rec)['seats']}
        # close 102.5 + put in 2.5 (the 3 bet came back) - won 5 = 100, in the table's unit.
        self.assertAlmostEqual(got[4]['stack'], 2.0)

    def test_refused_records_are_kept_by_reason_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'h.jsonl'
            bad = record()
            bad['end_reason'] = 'tracking_stopped'
            path.write_text(json.dumps(bad, sort_keys=True) + '\n' + json.dumps(record(), sort_keys=True) + '\n',
                            encoding='utf-8')
            doubt = Path(folder) / 'doubt'
            clubwptocr.report([str(path)], doubt)
            clubwptocr.report([str(path)], doubt)
            kept = (doubt / 'capture-ended-mid-hand.jsonl').read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(kept), 1)
            self.assertEqual(json.loads(kept[0])['end_reason'], 'tracking_stopped')


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

    def test_both_ways_the_tracker_writes_a_record_are_recognised(self):
        rec = record()
        self.assertTrue(clubwptocr.HEADER(json.dumps(rec, sort_keys=True)))      # hand histories
        self.assertTrue(clubwptocr.HEADER(json.dumps(rec, separators=(",", ":"))))  # --export
        self.assertFalse(clubwptocr.HEADER('{"record": {}, "review": {}}'))

    def test_reviewed_histories_still_go_to_their_own_parser(self):
        self.assertFalse(clubwptocr.HEADER('{"format":"clubwptgold","version":1}'))
        self.assertIs(sites.BY_KEY["clubwptocr"].module, clubwptocr)


if __name__ == "__main__":
    unittest.main()
