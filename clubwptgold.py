"""Reviewed ClubWPT Gold native JSONL v1; stdlib only, no capture or DB writes.

The identical contract module ships in CWPTGTracker. Unsupported evidence
raises ValueError. Original records remain unchanged inside each envelope.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

FORMAT = {"format": "clubwptgold", "version": 1}
EXPORT_HEADER = json.dumps(FORMAT, separators=(",", ":"))
STREETS = ("preflop", "flop", "turn", "river")
CARD = re.compile(r"[2-9TJQKA][cdhs]\Z")
AMOUNT = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,4})?\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys.split()),
            label + " has missing or unsupported fields")


def _text(value, label):
    require(isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and len(value) <= 256 and not any(ord(c) < 32 for c in value),
            label + " must be verified text")
    return value


def _money(value, label):
    require(isinstance(value, str) and AMOUNT.fullmatch(value) is not None,
            label + " must be an explicit nonnegative decimal string")
    return Decimal(value)


def _time(value, label):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(label + " must be a timestamp") from exc
    require(result.tzinfo is not None, label + " must include a timezone")
    return result


def _cards(value, lengths, label):
    require(isinstance(value, list) and len(value) in lengths and
            all(isinstance(c, str) and CARD.fullmatch(c) for c in value) and
            len(set(value)) == len(value), label + " must contain verified cards")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite JSON number")


def loads(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid JSON") from exc


def record_sha256(record):
    try:
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("record is not JSON evidence") from exc
    return hashlib.sha256(raw).hexdigest()


def HEADER(line):
    try:
        value = loads(line)
        return value == FORMAT and type(value.get("version")) is int
    except ValueError:
        return False


def split_hands(text):
    lines = [line for line in text.splitlines() if line.strip()]
    require(lines and HEADER(lines[0].lstrip("\ufeff")), "missing ClubWPT Gold format header")
    # Prevalidate the whole file so a bad later hand cannot partially import it.
    for index, line in enumerate(lines[1:], 2):
        try:
            parse_hand(line)
        except ValueError as exc:
            raise ValueError(f"ClubWPT Gold line {index}: {exc}") from exc
    yield from lines[1:]


def parse_hand(block, source=""):
    try:
        return _parse(loads(block), source)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("incomplete or malformed ClubWPT Gold record") from exc


def _parse(envelope, source):
    _object(envelope, "record review", "history envelope")
    record, review = envelope["record"], envelope["review"]
    _object(review, "record_sha256 site complete reference played_at game limit format "
            "max_seats dealt_seats amount_unit currency all_in_sequences settlement", "review")
    require(isinstance(record, dict), "record must be an object")
    require(review["record_sha256"] == record_sha256(record), "review hash does not match original record")
    require(review["site"] == "clubwptgold", "review must identify clubwptgold")
    require(review["complete"] is True, "complete hand review is required")
    _text(review["reference"], "independent reference")
    require((review["game"], review["limit"], review["format"]) == ("HOLDEM", "NL", "RING"),
            "only NLHE cash hands are supported")
    require(review["amount_unit"] == "currency" and review["currency"] == "USD",
            "review must confirm actual USD denomination; BB/coins/conversion unsupported")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1,
            "unsupported original record version")
    require(record["status"] == "reviewed" and record["review_reasons"] == [],
            "hand must be reviewed with no unresolved review reasons")
    # A short-lived posting badge may survive only as a candidate. Clearing
    # review flags cannot make that unresolved action disappear from the hand.
    require(record.get("action_candidates", []) == [],
            "unresolved action candidates cannot be exported or imported")
    require("spectator_tracking" not in record,
            "spectator tracking cannot establish independently verified complete hands")
    require("identity_conflicts" not in record,
            "unresolved player identity conflicts cannot be exported or imported")
    _text(record["id"], "local record id")
    client_id = _text(record["hand_id"], "client hand id")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", client_id), "unsupported client hand id")
    table_id = _text(record["table_id"], "table id")
    started, ended = _time(record["started_at"], "started_at"), _time(record["ended_at"], "ended_at")
    require(ended >= started, "hand ends before it starts")
    played = _time(review["played_at"], "reviewed hand time")
    require(review["all_in_sequences"] == [], "all-in/side-pot histories are unsupported in version 1")
    stakes = record["stakes"]
    _object(stakes, "small_blind big_blind currency", "stakes")
    require(stakes["currency"] == review["currency"], "record and reviewed currency disagree")
    sb, bb = _money(stakes["small_blind"], "small blind"), _money(stakes["big_blind"], "big blind")
    require(0 < sb <= bb, "invalid verified blinds")
    max_seats, dealt = review["max_seats"], review["dealt_seats"]
    require(type(max_seats) is int and 2 <= max_seats <= 9, "max_seats must be 2 to 9")
    require(isinstance(dealt, list) and 2 <= len(dealt) <= max_seats and
            all(type(s) is int and 1 <= s <= max_seats for s in dealt) and
            len(set(dealt)) == len(dealt), "invalid verified dealt seats")
    ring = sorted(dealt)
    button, hero = record["button_seat"], record["hero_seat"]
    require(type(button) is int and button in ring and type(hero) is int and hero in ring,
            "verified button and hero must be dealt in")
    board, hero_cards = _cards(record["board"], (0, 3, 4, 5), "board"), _cards(record["hero_cards"], (2,), "hero cards")
    require(not set(board).intersection(hero_cards), "duplicate board/hole card")
    last_street = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(board)]
    require(record["street"] in (last_street, "showdown"), "street and board disagree")
    players = record["players"]
    require(isinstance(players, list) and len(players) == len(ring), "players must be exactly dealt seats")
    by_seat, names = {}, set()
    for player in players:
        _object(player, "seat name starting_stack stack bet in_hand", "player")
        seat = player["seat"]
        require(type(seat) is int and seat in ring and seat not in by_seat, "invalid/duplicate player seat")
        name, stack = _text(player["name"], "player name"), _money(player["starting_stack"], "starting stack")
        require(name not in names, "duplicate player identity")
        require(stack > bb, "short starting stacks/all-in blinds are unsupported")
        names.add(name)
        by_seat[seat] = {"seat": seat, "label": name, "stack": stack,
                         "cards": " ".join(hero_cards) if seat == hero else None, "is_hero": seat == hero,
                         "won": Decimal(0), "posted": Decimal(0), "invested": Decimal(0)}
    index = ring.index(button)
    from_button = ring[index:] + ring[:index]
    order = from_button if len(ring) == 2 else from_button[1:] + from_button[:1]
    n_early = len(ring) - 3
    early = (["UTG", "HJ", "CO"][3 - n_early:] if n_early <= 3 else
             ["UTG"] * (n_early - 3) + ["UTG", "HJ", "CO"])
    positions = ["SB", "BB"] if len(ring) == 2 else ["SB", "BB"] + early + ["BTN"]
    for seat, position in zip(order, positions):
        by_seat[seat]["position"] = position
    actions = record["actions"]
    require(isinstance(actions, list) and len(actions) >= 3, "missing actions")
    commitments = {seat: Decimal(0) for seat in ring}
    active, pending = set(ring), set(ring)
    street, previous_time, expected = "preflop", started, None
    last_raise = bb
    parsed_actions, previous_sequence = [], 0
    for offset, action in enumerate(actions):
        require(isinstance(action, dict) and set(action) in (
            set("sequence street seat action amount timestamp order_certain".split()),
            set("sequence street seat action amount timestamp order_certain street_total".split())),
            "action has missing or unsupported fields")
        sequence, seat, verb = action["sequence"], action["seat"], action["action"]
        require(type(sequence) is int and sequence == previous_sequence + 1, "action sequence has a gap")
        previous_sequence = sequence
        require(type(seat) is int and seat in active, "action by unknown/folded seat")
        require(action["order_certain"] is True, "action ordering is uncertain")
        timestamp = _time(action["timestamp"], "action time")
        require(previous_time <= timestamp <= ended, "action time is out of order")
        previous_time = timestamp
        amount = None if action["amount"] is None else _money(action["amount"], "action amount")
        total = None if action.get("street_total") is None else _money(action["street_total"], "street total")
        if offset < 2:
            blind_seat, blind_verb, blind = ((order[0], "post_small_blind", sb) if offset == 0 else
                                           (order[1], "post_big_blind", bb))
            require(action["street"] == "preflop" and seat == blind_seat and verb == blind_verb,
                    "both standard blind posts must begin the history")
            require((amount is not None or total is not None) and
                    (amount is None or amount == blind) and (total is None or total == blind),
                    "blind amount disagrees with stakes")
            commitments[seat], by_seat[seat]["posted"] = blind, blind
            expected = order[2] if len(order) > 2 else order[0]
            continue
        require(verb in ("fold", "check", "call", "bet", "raise"), "unsupported action: " + str(verb))
        current = action["street"]
        require(current in STREETS, "unsupported action street")
        if current != street:
            require(not pending and len(active) > 1 and
                    STREETS.index(current) == STREETS.index(street) + 1,
                    "missing actions or skipped street")
            street, commitments, pending = current, {s: Decimal(0) for s in ring}, set(active)
            last_raise = bb
            expected = next(s for s in from_button[1:] + from_button[:1] if s in pending)
        require(STREETS.index(street) <= STREETS.index(last_street), "actions beyond recorded board")
        require(pending and seat == expected and seat in pending, "missing or out-of-order action")
        price, prior = max(commitments.values()), commitments[seat]
        if verb in ("fold", "check"):
            require(amount is None and total is None, "non-monetary action carries money")
            require(verb != "check" or prior == price, "check while facing a bet")
            if verb == "fold":
                active.remove(seat)
            added = None
        else:
            require(amount is not None or total is not None, "missing action amount")
            added = total - prior if total is not None else amount
            require(added > 0 and (amount is None or amount == added),
                    "incremental amount and street total disagree")
            new_total = prior + added
            if verb == "call":
                require(new_total == price and prior < price, "invalid call amount")
            elif verb == "bet":
                require(price == 0 and added >= bb, "invalid opening bet")
                last_raise = added
            else:
                require(price > 0 and new_total - price >= last_raise, "invalid minimum raise")
                last_raise = new_total - price
            require(by_seat[seat]["posted"] + by_seat[seat]["invested"] + added < by_seat[seat]["stack"],
                    "all-in/over-stack action is unsupported")
            commitments[seat] = new_total
            by_seat[seat]["invested"] += added
            if verb in ("bet", "raise"):
                pending = set(active)
        pending.discard(seat)
        if len(active) == 1:
            pending.clear()
        if pending:
            turn = ring.index(seat)
            expected = next(s for s in ring[turn + 1:] + ring[:turn + 1] if s in pending)
        parsed_actions.append({
            "street": street, "n": len(parsed_actions) + 1, "position": by_seat[seat]["position"],
            "seat": seat, "action": {"fold": "F", "check": "X", "call": "C", "bet": "B", "raise": "R"}[verb],
            "amount": float(added) if added is not None else None,
            "total": float(commitments[seat]) if verb == "raise" else None, "allin": 0})
    require(not pending and street == last_street, "hand is missing terminal actions")
    require(len(active) == 1 or len(board) == 5, "unfinished showdown")
    settlement = review["settlement"]
    _object(settlement, "pot rake jp_fee payouts returns shown_cards", "settlement")
    pot, rake, fee = (_money(settlement[key], key) for key in ("pot", "rake", "jp_fee"))
    require(pot > 0, "missing settlement pot")
    returns = settlement["returns"]
    require(isinstance(returns, list), "returns must be an explicitly reviewed list")
    ranked = sorted(commitments.values(), reverse=True)
    excess = ranked[0] - ranked[1]
    if excess:
        require(len(active) == 1 and len(returns) == 1, "uncalled return is missing or unsupported")
        returned, winner = returns[0], next(iter(active))
        _object(returned, "seat street amount", "uncalled return")
        require(type(returned["seat"]) is int and returned["seat"] == winner and
                returned["street"] == street and commitments[winner] == ranked[0] and
                _money(returned["amount"], "return amount") == excess, "incorrect uncalled return")
        by_seat[winner]["invested"] -= excess
    else:
        require(returns == [], "unexpected uncalled return")
    payouts = settlement["payouts"]
    require(isinstance(payouts, list) and bool(payouts), "missing reviewed payouts")
    paid = set()
    for payout in payouts:
        _object(payout, "seat amount", "payout")
        seat, value = payout["seat"], _money(payout["amount"], "payout amount")
        require(type(seat) is int and seat in active and seat not in paid and value > 0, "invalid payout")
        by_seat[seat]["won"] = value
        paid.add(seat)
    invested = sum(p["posted"] + p["invested"] for p in by_seat.values())
    won = sum(p["won"] for p in by_seat.values())
    require(invested == pot and won + rake + fee == pot, "settlement does not conserve money")
    shown = settlement["shown_cards"]
    require(isinstance(shown, list), "shown_cards must be an explicitly reviewed list")
    seen_cards, shown_seats = set(board + hero_cards), set()
    for item in shown:
        _object(item, "seat cards", "shown cards")
        seat, cards = item["seat"], _cards(item["cards"], (2,), "shown cards")
        require(type(seat) is int and seat in active and seat not in shown_seats, "invalid shown seat")
        if seat == hero:
            require(cards == hero_cards, "hero shown cards disagree")
        else:
            require(not seen_cards.intersection(cards), "duplicate shown card")
            seen_cards.update(cards)
        shown_seats.add(seat)
        by_seat[seat]["cards"] = " ".join(cards)
    for player in by_seat.values():
        for key in ("stack", "won", "posted", "invested"):
            player[key] = float(player[key])
    return {
        "hand": {"hand_id": "cwg-" + client_id,
                 "played_at": played.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                 "table_id": table_id, "game": "HOLDEM", "fmt": "RING", "sb": float(sb), "bb": float(bb),
                 "n_players": len(ring), "board": " ".join(board), "pot": float(pot), "rake": float(rake),
                 "jp_fee": float(fee), "hero_seat": hero, "standard": 1, "source": source, "max_seats": max_seats},
        "seats": [by_seat[seat] for seat in ring], "actions": parsed_actions}
