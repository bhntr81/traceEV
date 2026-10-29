"""Native ClubWPT Gold imports: synthetic, audited histories; no live database."""
import copy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import importer
import sites


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fixture():
    timestamp = "2026-10-14T18:00:00+00:00"
    moves = [
        ("preflop", 1, "post_small_blind", ".5", ".5"),
        ("preflop", 2, "post_big_blind", "1", "1"),
        ("preflop", 1, "call", ".5", "1"),
        ("preflop", 2, "check", None, None),
        ("flop", 2, "check", None, None),
        ("flop", 1, "bet", "1", "1"),
        ("flop", 2, "call", "1", "1"),
        ("turn", 2, "check", None, None),
        ("turn", 1, "check", None, None),
        ("river", 2, "check", None, None),
        ("river", 1, "bet", "2", "2"),
        ("river", 2, "fold", None, None),
    ]
    record = {
        "schema_version": 1, "id": "local-synthetic-1", "table_id": "test-table",
        "table_title": "synthetic only", "hand_id": "123456", "started_at": timestamp,
        "ended_at": "2026-10-14T18:02:00+00:00", "street": "river",
        "board": ["Ah", "7c", "2d", "5s", "9h"], "hero_cards": ["Kc", "Kd"],
        "hero_seat": 1, "button_seat": 1,
        "stakes": {"small_blind": "0.5", "big_blind": "1", "currency": "USD"},
        "pot": "4", "players": [
            {"seat": 1, "name": "Alice", "starting_stack": "10",
             "stack": None, "bet": None, "in_hand": True},
            {"seat": 2, "name": "Bob", "starting_stack": "10",
             "stack": None, "bet": None, "in_hand": False}],
        "actions": [{"sequence": i, "street": street, "seat": seat, "action": action,
                     "amount": ("0" + amount if amount and amount.startswith(".") else amount),
                     "street_total": ("0" + total if total and total.startswith(".") else total),
                     "timestamp": timestamp, "order_certain": True}
                    for i, (street, seat, action, amount, total) in enumerate(moves, 1)],
        "review_reasons": [], "status": "reviewed",
    }
    review = {
        "record_sha256": digest(record), "site": "clubwptgold", "complete": True,
        "reference": "SYNTHETIC TEST ONLY", "played_at": timestamp,
        "game": "HOLDEM", "limit": "NL", "format": "RING", "max_seats": 2,
        "dealt_seats": [1, 2], "amount_unit": "currency", "currency": "USD", "all_in_sequences": [],
        "settlement": {"pot": "4", "rake": "0.2", "jp_fee": "0",
                       "payouts": [{"seat": 1, "amount": "3.8"}],
                       "returns": [{"seat": 1, "street": "river", "amount": "2"}],
                       "shown_cards": []},
    }
    return {"record": record, "review": review}


def encoded(envelope):
    return json.dumps({"format": "clubwptgold", "version": 1}) + "\n" + json.dumps(envelope) + "\n"


class ClubWPTGoldTests(unittest.TestCase):
    def parser(self):
        self.assertIsNotNone(importlib.util.find_spec("clubwptgold"),
                             "native ClubWPT Gold parser has not been implemented")
        import clubwptgold
        return clubwptgold

    def test_native_history_is_discovered_imported_and_deduplicated(self):
        envelope = fixture()
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder) / "verified.jsonl"
            db = Path(folder) / "temporary.db"
            history.write_text(encoded(envelope), encoding="utf-8")
            self.assertEqual(importer.sniff(history), "clubwptgold")
            self.assertIn(history, importer.files_under([folder]))
            first = importer.load([folder], db)
            self.assertEqual(first["by_site"]["clubwptgold"], 1)
            self.assertEqual(importer.load([history], db)["known"], 1)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute(
                    "SELECT hand_id,site,pot,rake,board FROM hands").fetchone(),
                    ("cwg-123456", "clubwptgold", 4.0, 0.2, "Ah 7c 2d 5s 9h"))
                self.assertEqual(con.execute(
                    "SELECT seat,label,position,posted,invested,won FROM seats ORDER BY seat").fetchall(),
                    [(1, "Alice", "SB", .5, 1.5, 3.8), (2, "Bob", "BB", 1.0, 1.0, 0.0)])
                self.assertEqual(con.execute(
                    "SELECT street,seat,action,amount,total FROM actions ORDER BY n").fetchall(),
                    [("preflop", 1, "C", .5, None), ("preflop", 2, "X", None, None),
                     ("flop", 2, "X", None, None), ("flop", 1, "B", 1.0, None),
                     ("flop", 2, "C", 1.0, None), ("turn", 2, "X", None, None),
                     ("turn", 1, "X", None, None), ("river", 2, "X", None, None), ("river", 1, "B", 2.0, None),
                     ("river", 2, "F", None, None)])

    def test_parser_keeps_unknown_cards_and_does_not_mutate_evidence(self):
        parser = self.parser()
        envelope = fixture()
        before = copy.deepcopy(envelope)
        result = parser.parse_hand(json.dumps(envelope), source="verified.jsonl")
        self.assertEqual(envelope, before)
        self.assertIsNone(result["seats"][1]["cards"])
        self.assertEqual(result["hand"]["source"], "verified.jsonl")
        self.assertTrue(sites.of("clubwptgold").names)
        self.assertFalse(sites.of("clubwptgold").reveals)

    def test_unresolved_candidates_cannot_be_cleared_by_review_flags(self):
        parser = self.parser()
        for candidates in ([{"action": "post_straddle"}],
                           [{"action": "post_unknown"}],
                           [{"action": "call"}], None, {}, False):
            data = fixture()
            data["record"]["action_candidates"] = candidates
            data["review"]["record_sha256"] = digest(data["record"])
            with self.subTest(candidates=candidates), self.assertRaisesRegex(
                    ValueError, "unresolved action candidates"):
                parser.parse_hand(json.dumps(data))
        data = fixture()
        data["record"]["action_candidates"] = []
        data["review"]["record_sha256"] = digest(data["record"])
        self.assertEqual(parser.parse_hand(json.dumps(data))["hand"]["pot"], 4.0)

    def test_boundary_or_identity_metadata_cannot_be_cleared_by_review_flags(self):
        parser = self.parser()
        for key in ("spectator_tracking", "identity_conflicts"):
            for metadata in ({"version": 1}, None, {}, [], False):
                data = fixture()
                data["record"][key] = metadata
                data["review"]["record_sha256"] = digest(data["record"])
                with self.subTest(key=key, metadata=metadata), self.assertRaisesRegex(
                        ValueError, "spectator tracking|player identity conflicts"):
                    parser.parse_hand(json.dumps(data))

    def test_rejects_stale_review_and_wrong_source_identity(self):
        parser = self.parser()
        for mutation in ("stale", "site", "id", "unreviewed", "reason", "unit", "reference"):
            data = fixture()
            if mutation == "stale":
                data["record"]["players"][0]["name"] = "Changed"
            elif mutation == "site":
                data["review"]["site"] = "pokerstars"
            elif mutation == "id":
                data["record"]["hand_id"] = None
            elif mutation == "unreviewed":
                data["record"]["status"] = "needs_review"
            elif mutation == "reason":
                data["record"]["review_reasons"] = ["missed_action"]
            elif mutation == "unit":
                data["review"]["amount_unit"] = "bb"
            else:
                data["review"]["reference"] = ""
            if mutation != "stale":
                data["review"]["record_sha256"] = digest(data["record"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parser.parse_hand(json.dumps(data))

    def test_rejects_plausible_but_incomplete_or_inconsistent_histories(self):
        parser = self.parser()
        for mutation in ("missing_action", "bad_order", "uncertain", "money",
                         "return", "unknown_amount", "allin", "missing_stack",
                         "duplicate_card", "missing_payout", "missing_rake",
                         "unknown_field", "unsupported_game", "missing_terminal"):
            data = fixture()
            record, review = data["record"], data["review"]
            if mutation == "missing_action":
                del record["actions"][6]
            elif mutation == "bad_order":
                record["actions"][4]["seat"] = 1
            elif mutation == "uncertain":
                record["actions"][4]["order_certain"] = False
            elif mutation == "money":
                review["settlement"]["payouts"][0]["amount"] = "3.9"
            elif mutation == "return":
                review["settlement"]["returns"] = []
            elif mutation == "unknown_amount":
                record["actions"][5]["amount"] = None
                record["actions"][5]["street_total"] = None
            elif mutation == "allin":
                record["actions"][5]["action"] = "all_in"
            elif mutation == "missing_stack":
                record["players"][0]["starting_stack"] = None
            elif mutation == "duplicate_card":
                record["hero_cards"][0] = "Ah"
            elif mutation == "missing_payout":
                review["settlement"]["payouts"] = []
            elif mutation == "missing_rake":
                del review["settlement"]["rake"]
            elif mutation == "unknown_field":
                review["settlement"]["side_pots"] = []
            elif mutation == "unsupported_game":
                review["game"] = "OMAHA"
            else:
                record["actions"].pop()
            review["record_sha256"] = digest(record)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parser.parse_hand(json.dumps(data))

    def test_exported_street_totals_are_incremental_amounts_not_added_twice(self):
        parser = self.parser()
        data = fixture()
        # The visible bet box is a street total; hero adds only another .5.
        data["record"]["actions"][2]["amount"] = None
        data["review"]["record_sha256"] = digest(data["record"])
        parsed = parser.parse_hand(json.dumps(data))
        self.assertEqual(parsed["actions"][0]["amount"], .5)
        self.assertEqual(parsed["seats"][0]["invested"], 1.5)

    def test_importer_rolls_back_bad_native_batch(self):
        parser = self.parser()
        good, bad = fixture(), fixture()
        bad["record"]["hand_id"] = "123457"
        bad["review"]["record_sha256"] = digest(bad["record"])
        bad["review"]["settlement"]["rake"] = None
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "batch.jsonl"
            db = Path(folder) / "temporary.db"
            source.write_text(encoded(good) + json.dumps(bad) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                importer.load([source], db)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM hands").fetchone()[0], 0)

    def test_original_history_reexport_remains_importable(self):
        self.parser()
        with tempfile.TemporaryDirectory() as folder:
            source, output, db = [Path(folder) / name for name in ("original.jsonl", "selected.jsonl", "temporary.db")]
            source.write_text(encoded(fixture()), encoding="utf-8")
            importer.load([source], db)
            with closing(sqlite3.connect(db)) as con, patch("importer.source_index", return_value={source.name: source}):
                self.assertEqual(importer.export(con, ["cwg-123456"], output, log=lambda _: None), (1, []))
            self.assertEqual(importer.sniff(output), "clubwptgold")
            self.assertEqual(importer.load([output], Path(folder) / "roundtrip.db")["added"], 1)

    def test_native_mixed_site_reexport_fails_before_overwriting_output(self):
        self.parser()
        with tempfile.TemporaryDirectory() as folder:
            source, output, db = [Path(folder) / name for name in ("original.jsonl", "selected.txt", "temporary.db")]
            source.write_text(encoded(fixture()), encoding="utf-8")
            output.write_text("preserve existing export", encoding="utf-8")
            importer.load([source], db)
            with closing(sqlite3.connect(db)) as con:
                con.execute("INSERT INTO hands(hand_id,site,source) VALUES ('ps-1','pokerstars','old.txt')")
                con.commit()
                with patch("importer.source_index", return_value={source.name: source}), self.assertRaises(ValueError):
                    importer.export(con, ["cwg-123456", "ps-1"], output)
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve existing export")

    def test_jsonl_discovery_preserves_shallow_drop_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "nested").mkdir()
            top = root / "native.JSONL"
            nested = root / "nested" / "native.jsonl"
            top.write_text(encoded(fixture()), encoding="utf-8")
            nested.write_text(encoded(fixture()), encoding="utf-8")
            with patch("importer.shallow", return_value=True):
                self.assertEqual(importer.files_under([root]), [top])
            with patch("importer.shallow", return_value=False):
                self.assertEqual(set(importer.files_under([root])), {top, nested})

    def test_original_source_index_finds_native_jsonl(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "native.jsonl"
            source.write_text(encoded(fixture()), encoding="utf-8")
            with patch("importer.places", return_value=(folder,)):
                self.assertEqual(importer.source_index().get(source.name), source)

    def test_rejected_file_does_not_advance_import_watermark(self):
        data = fixture()
        data["review"]["settlement"]["rake"] = None
        with tempfile.TemporaryDirectory() as folder:
            source, db = Path(folder) / "bad.jsonl", Path(folder) / "temporary.db"
            source.write_text(encoded(data), encoding="utf-8")
            importer.high_water(db, 100.0)
            with self.assertRaises(ValueError):
                importer.load([source], db)
            self.assertEqual(importer.high_water(db), 100.0)

    def test_rejects_non_all_in_underraise_even_when_money_balances(self):
        data = fixture()
        record = data["record"]
        record["board"], record["street"] = [], "preflop"
        record["actions"] = record["actions"][:3]
        record["actions"][2].update(action="raise", amount="0.6", street_total="1.1")
        record["actions"].append(dict(record["actions"][2], sequence=4, seat=2, action="fold",
                                      amount=None, street_total=None))
        data["review"]["record_sha256"] = digest(record)
        data["review"]["settlement"].update(
            pot="2", rake="0", payouts=[{"seat": 1, "amount": "2"}],
            returns=[{"seat": 1, "street": "preflop", "amount": "0.1"}])
        with self.assertRaises(ValueError):
            self.parser().parse_hand(json.dumps(data))


if __name__ == "__main__":
    unittest.main(argv=["test_clubwptgold"])
