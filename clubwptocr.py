"""
ClubWPT Gold as the tracker's OCR read it: one JSON record per line.

`clubwptgold.py` takes a hand a person has reviewed against the client and
nothing else, and after a month of recording that was no hands at all. This
is the other road: every record the tracker writes is offered, and a hand
is stored when the record ALONE proves itself -- the seats are known, every
chip that went in has a number, and what went in is what came out. A hand
that cannot show that is turned away with its reason counted, and the count
is the reader's to-do list. Nothing is filled in to make a hand pass.

What "proves itself" means, and why each line is there:

  finished    the tracker closed the hand at the next deal. One it stopped
              watching halfway is half a hand, which stored once is skipped
              as known for ever.
  priced      every post, call, bet and raise has an amount. One unread bet
              box makes the pot, the price of every later call and whoever
              was all-in unknowable.
  ordered     nobody acts after folding, checks facing a bet, or calls more
              than the bet. Each is the mark of an action the camera missed.
  settled     the tracker's own settlement (it reads the stacks before and
              after), or a pot nobody contested before the flop, which this
              room does not rake. A showdown without a settlement has no
              winner, and a winner is never guessed.
  conserved   what the seats put in is the pot, and the pot is what was
              collected plus the rake.

The tracker counts in big blinds and the table's title carries the stake, so
amounts are turned back into the table's own unit when the title can be read
and are left in big blinds when it cannot. No currency is claimed either
way: the unit is whatever the table says, CHIPS included.

    python clubwptocr.py FILE...     what would load, and why the rest would not
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sys

FORMAT = {"format": "clubwptgold-ocr", "version": 1}
EXPORT_HEADER = json.dumps(FORMAT, separators=(",", ":"))
STREETS = ("preflop", "flop", "turn", "river")
CARD = re.compile(r"[2-9TJQKA][cdhs]\Z")
POSTS = ("post_small_blind", "post_big_blind")
LETTER = {"fold": "F", "check": "X"}

# A raw tracker line begins with its first key, and the tracker writes keys
# in order, so that is one of two. The sniffer reads 4 KB and a record is
# eighty, which is why this cannot simply parse the line and look.
RAW = re.compile(r'\{"(?:action_candidates|actions)":\s*\[')
# " Ring Game (NLHE) | 0.10/0.20/0.40 (0.10) CHIPS | HL7638": small blind,
# big blind, a straddle when there is one, the ante in brackets likewise.
TITLE = re.compile(r"\|\s*([0-9.]+)/([0-9.]+)(?:/([0-9.]+))?\s*(?:\(([0-9.]+)\))?"
                   r"\s*([A-Za-z$]+)?\s*\|")
TABLE = re.compile(r"\bHL[0-9]+\b")
# The shape of a username, the tracker's own rule since d81e99b: words of
# letters, digits, `_` and `-`, single spaces, at most a closing `..` where
# the client cut it short. Records read before that rule carry readings like
# "StumpCc:ayoi:eZ..I", which as a person would be a villain nobody is.
USERNAME = re.compile(r"[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*(?:\.\.)?\Z")

# Why hands were turned away, and the verbs the action loop did not know.
# `fixtures.py --check` fails on any UNKNOWN; REJECTED is expected to be
# non-empty and is what `python clubwptocr.py` prints.
REJECTED = Counter()
UNKNOWN = Counter()


class Reject(Exception):
    """This record does not prove itself; the message is the tally's key."""


def need(condition, reason):
    if not condition:
        raise Reject(reason)


def HEADER(line):
    line = line.lstrip("﻿").strip()
    if RAW.match(line):
        return True
    try:
        return json.loads(line) == FORMAT
    except ValueError:
        return False


def split_hands(text):
    for line in text.splitlines():
        line = line.lstrip("﻿").strip()
        if not line or not line.startswith("{") or line == EXPORT_HEADER:
            continue
        yield line


def parse_hand(block, source=""):
    """The shared dict, or None with the reason counted in REJECTED."""
    try:
        record = json.loads(block)
        need(isinstance(record, dict), "not a tracker record")
        if record == FORMAT:
            return None
        # A reviewed envelope dropped in the same folder carries the record
        # inside; the review is the other parser's business.
        if set(record) == {"record", "review"}:
            record = record["record"]
        return _parse(record, source)
    except Reject as why:
        REJECTED[str(why)] += 1
    except (ValueError, KeyError, TypeError, IndexError, AttributeError,
            InvalidOperation):
        REJECTED["malformed record"] += 1
    return None


def _dec(value):
    if value is None or value == "":
        return None
    number = Decimal(str(value))
    need(number.is_finite() and number >= 0, "malformed amount")
    return number


def _when(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _cards(value):
    cards = list(value or [])
    ok = all(isinstance(c, str) and CARD.fullmatch(c) for c in cards)
    return cards if ok and len(set(cards)) == len(cards) else None


def positions(ring, button):
    """Seat -> position, the names the rest of the program expects."""
    at = ring.index(button)
    from_button = ring[at:] + ring[:at]
    order = from_button if len(ring) == 2 else from_button[1:] + from_button[:1]
    early = len(ring) - 3
    names = (["UTG", "HJ", "CO"][3 - early:] if early <= 3 else
             ["UTG"] * (early - 3) + ["UTG", "HJ", "CO"])
    names = ["SB", "BB"] if len(ring) == 2 else ["SB", "BB"] + names + ["BTN"]
    return dict(zip(order, names))


def _parse(record, source):
    need(record.get("end_reason") == "hand_boundary", "capture ended mid-hand")
    title = record.get("table_title") or ""
    need("NLHE" in title or not title, "not a hold'em table")
    found = TABLE.search(title)
    table = found.group(0) if found else str(record["table_id"])
    started = _when(record["started_at"])

    stakes = record["stakes"]
    sb, bb = _dec(stakes["small_blind"]), _dec(stakes["big_blind"])
    need(sb is not None and bb is not None and 0 < sb <= bb, "blinds unread")
    # Back into the table's unit. Only when the record is in big blinds and
    # the title's two blinds stand in the record's ratio; a title that says
    # 0.01/0.02 over a record that says 1/3 is one of them misread.
    scale, ante, straddle = Decimal(1), None, None
    stake = TITLE.search(title)
    if stake and stakes.get("currency") == "BB":
        t_sb, t_bb = Decimal(stake.group(1)), Decimal(stake.group(2))
        if t_bb > 0 and t_sb * bb == t_bb * sb:
            scale = t_bb / bb
            straddle = Decimal(stake.group(3)) if stake.group(3) else None
            ante = Decimal(stake.group(4)) if stake.group(4) else None

    actions = sorted(record["actions"], key=lambda a: a["sequence"])
    need(actions, "no actions read")
    # Dealt in is having done something. A seat listed at the table that
    # never posts, folds or acts is sitting out, and left in it never folds
    # and reaches every showdown.
    acted = {a["seat"] for a in actions}
    listed = {p["seat"]: p for p in record["players"]}
    need(acted <= set(listed), "action by a seat not at the table")
    ring = sorted(acted)
    need(len(ring) >= 2, "fewer than two seats dealt in")
    button = record.get("button_seat")
    need(button in ring, "button unread or not dealt in")
    hero = record.get("hero_seat")
    hero = hero if hero in ring else None
    where = positions(ring, button)

    board = _cards(record.get("board"))
    need(board is not None and len(board) in (0, 3, 4, 5), "board unread")
    hole = _cards(record.get("hero_cards"))
    hole = hole if hole and len(hole) == 2 and not set(hole) & set(board) else None
    last_street = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(board)]

    # A name the reader is not sure of is not a person. An unread seat, a
    # seat whose name was read two ways, and the second of two seats read
    # alike each get a label that says so and names nobody else.
    doubted = {c.get("seat") for c in record.get("identity_conflicts") or []}
    seats, taken = {}, set()
    for seat in ring:
        player = listed[seat]
        name = (player.get("name") or "").strip()
        if (not USERNAME.fullmatch(name) or seat in doubted or name in taken):
            name = f"~{table}#{seat}"
        taken.add(name)
        stack = _dec(player.get("starting_stack"))
        seats[seat] = {"seat": seat, "label": name, "position": where[seat],
                       "stack": stack * scale if stack is not None else None,
                       "cards": " ".join(hole) if hole and seat == hero else None,
                       "is_hero": seat == hero, "won": Decimal(0),
                       "posted": Decimal(0), "invested": Decimal(0)}

    street, commit = "preflop", {s: Decimal(0) for s in ring}
    folded, allin, rows, posts = set(), set(), [], []
    for action in actions:
        seat, verb = action["seat"], action["action"]
        need(seat not in folded, "action after folding")
        current = action.get("street") or street
        need(current in STREETS, "action on an unknown street")
        if current != street:
            need(STREETS.index(current) > STREETS.index(street), "streets out of order")
            street, commit = current, {s: Decimal(0) for s in ring}
        need(STREETS.index(street) <= STREETS.index(last_street),
             "action on a street the board never reached")
        total, amount = _dec(action.get("street_total")), _dec(action.get("amount"))
        price, prior = max(commit.values()), commit[seat]

        if verb == "post_unknown":
            # A POST badge on a seat that is neither blind, its box read: a
            # new or returning player's post (all five on stream5 were 1 BB,
            # and the poster checked when the limps came round, as a live
            # post does). The tracker leaves its kind open on purpose; for
            # tracking the kind does not matter, only that the money went in
            # before the action -- what the Ignition parser does with a dead
            # post. Unread, it is money of unknown size and the hand goes.
            need(total is not None or amount is not None, "unresolved post")
        if verb in POSTS or verb == "post_unknown":
            if total is None and amount is None:
                total = sb if verb == "post_small_blind" else bb
            new = total if total is not None else prior + amount
            need(new > prior, "post of nothing")
            seats[seat]["posted"] += (new - prior) * scale
            commit[seat] = new
            posts.append((verb, seat))
            continue
        if verb in LETTER:
            need(verb != "check" or prior == price, "check facing a bet")
            if verb == "fold":
                folded.add(seat)
            rows.append((street, seat, LETTER[verb], None, None, 0))
            continue
        if verb not in ("call", "bet", "raise", "all_in"):
            # Whatever the tracker learns to write next. Money of an unknown
            # kind: the hand goes, and the verb is kept for fixtures.py.
            UNKNOWN[str(verb)] += 1
            raise Reject("action of an unknown kind")

        need(total is not None or amount is not None, "amount unread")
        new = total if total is not None else prior + amount
        added = new - prior
        need(added > 0, "bet of nothing")
        shove = verb == "all_in"
        # The letter is what the money did, not what the badge said: an
        # all-in badge is a call when it does not pass the bet, and 95 of
        # 236 all-ins on another site were exactly that.
        if new > price:
            need(verb != "call", "call of more than the bet")
            letter = "B" if price == 0 else "R"
        else:
            need(verb != "raise" and verb != "bet", "raise that does not raise")
            need(new == price or shove, "call of less than the bet")
            letter = "C"
        stack = seats[seat]["stack"]
        spent = seats[seat]["posted"] + seats[seat]["invested"] + added * scale
        need(stack is None or spent <= stack, "more in than the stack")
        seats[seat]["invested"] += added * scale
        commit[seat] = new
        if shove:
            allin.add(seat)
        rows.append((street, seat, letter, added * scale,
                     new * scale if letter == "R" else None, int(shove)))

    live = [s for s in ring if s not in folded]
    need(live, "everybody folded")
    need(street == last_street or len(live) == 1 or allin,
         "betting stops before the board does")
    need(len(live) == 1 or len(board) == 5 or allin, "no fold and no river")

    # The bet nobody called comes back, and it is not money that went in.
    ranked = sorted(commit.values(), reverse=True)
    excess = ranked[0] - ranked[1]
    returned = None
    if excess > 0:
        returned = next(s for s in ring if commit[s] == ranked[0])
        need(returned in live, "uncalled bet from a folded seat")
        seats[returned]["invested"] -= excess * scale
        need(seats[returned]["invested"] >= 0, "uncalled bet larger than the bet")
    put_in = sum(p["posted"] + p["invested"] for p in seats.values())

    settlement = record.get("settlement")
    if settlement:
        pot, rake = _dec(settlement["pot"]) * scale, _dec(settlement["rake"]) * scale
        back = settlement.get("uncalled")
        if back:
            need(returned == back["seat"] and _dec(back["amount"]) == excess,
                 "uncalled bet disagrees with the settlement")
        for share in settlement["collected"]:
            need(share["seat"] in live, "pot collected by a folded seat")
            # Added, never set: a seat collects twice with a side pot.
            seats[share["seat"]]["won"] += _dec(share["amount"]) * scale
    else:
        need(len(live) == 1, "no settlement: winner unknown")
        need(street == "preflop", "no settlement: rake unknown")
        pot, rake = put_in, Decimal(0)
        seats[live[0]]["won"] = pot
    won = sum(p["won"] for p in seats.values())
    need(put_in == pot, "pot is not what went in")
    need(won + rake == pot and won > 0, "pot is not what came out")

    # Standard is the two blinds and nothing else, as on the other sites; a
    # hand with a third post keeps its money but stays out of the checks and
    # stats that assume two.
    blinds = {where[seat] for verb, seat in posts}
    standard = int(len(posts) == 2 and blinds == {"SB", "BB"} and
                   {verb for verb, _ in posts} == set(POSTS))
    stamp = started.strftime("%Y%m%dT%H%M%S")
    for player in seats.values():
        for key in ("stack", "won", "posted", "invested"):
            player[key] = float(player[key]) if player[key] is not None else None
    return {
        # The table and the second the deal was first seen: the same for a
        # recording read twice, which is what makes a re-read add nothing.
        "hand": {"hand_id": f"cwo-{table}-{stamp}",
                 "played_at": started.strftime("%Y-%m-%d %H:%M:%S"),
                 "table_id": table, "game": "HOLDEM", "fmt": "RING",
                 "sb": float(sb * scale), "bb": float(bb * scale),
                 "n_players": len(ring), "board": " ".join(board),
                 "pot": float(pot), "rake": float(rake), "jp_fee": None,
                 "hero_seat": hero, "standard": standard, "source": source,
                 "max_seats": max(8, max(listed)),
                 "ante": float(ante) if ante is not None else None,
                 "straddle": float(straddle) if straddle is not None else None},
        "seats": [seats[s] for s in ring],
        "actions": [{"street": st, "n": n, "position": where[seat], "seat": seat,
                     "action": letter, "amount": float(a) if a is not None else None,
                     "total": float(t) if t is not None else None, "allin": shove}
                    for n, (st, seat, letter, a, t, shove) in enumerate(rows, 1)]}


def report(paths):
    """What would load from these files, and why the rest would not."""
    REJECTED.clear()
    good = total = 0
    for path in paths:
        with open(path, encoding="utf-8-sig") as fh:
            for block in split_hands(fh.read()):
                total += 1
                good += parse_hand(block, source=str(path)) is not None
    print(f"{good} of {total} hands prove themselves and would load")
    for reason, n in REJECTED.most_common():
        print(f"  {n:4d}  {reason}")
    return good, total


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    report(sys.argv[1:])
