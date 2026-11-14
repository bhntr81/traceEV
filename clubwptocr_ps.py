"""
ClubWPT Gold OCR hands written as PokerStars text, for trackers that read only that.

Hand2Note, like most trackers, has no ClubWPT Gold reader; what it does read
is PokerStars' text, which is also what the converters for app rooms write.
So the hands `clubwptocr` accepts -- and only those -- are written in that
dialect, one file per table, and the same hands can then be put into TraceEV
and into another tracker and the two compared on the same evidence.

Nothing is added in the writing. Each hand is written from the dict
`clubwptocr.parse_hand` returns, then read back with TraceEV's own
PokerStars parser, and it is written only if the reading gives the same
seats, positions, money and actions. A hand that would not survive the
round trip is counted in SKIPPED with the reason and left out.

The text says PokerStars, because that is the header such trackers accept,
and dollars, because the format has no other unit for a cash table; the
amounts are the table's own (ClubWPT Gold chips), the table is named
"ClubWPT <table>", and the hand number is derived from the hand's own id,
so writing the same hand twice gives the same number.

    python clubwptocr.py FILE... --pokerstars OUT_DIR
"""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

import clubwptocr
import pokerstars

SKIPPED = Counter()
STREET_HEADS = {"flop": "FLOP", "turn": "TURN", "river": "RIVER"}
CENT = Decimal("0.01")


def money(value):
    """$0.50, $1, $12.25: cents, and no trailing .00, as PokerStars writes them."""
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    text = format(amount, "f")
    return "$" + (text[:-3] if text.endswith(".00") else text)


def hand_number(hand_id):
    """A stable 12-digit number for the hand: PokerStars' numbers are all digits."""
    return int(hashlib.sha256(hand_id.encode()).hexdigest(), 16) % 10 ** 12


def _button(seats):
    by_position = {s["position"]: s["seat"] for s in seats}
    return by_position.get("BTN", by_position.get("SB"))


def _post_lines(hand, seats):
    """The posts, blinds first: small blind, big blind, then any other post."""
    # The antes first, every dealt seat, as PokerStars writes them.
    lines = ["%s: posts the ante %s" % (s["label"], money(s["ante"])) for s in seats if s.get("ante")]
    order = {"SB": 0, "BB": 1}
    blinds = [dict(s, posted=s["posted"] - (s.get("ante") or 0)) for s in seats]
    for s in sorted((s for s in blinds if s["posted"] > 1e-9), key=lambda s: order.get(s["position"], 2)):
        if s["position"] == "SB":
            verb = "posts small blind"
        elif s["position"] == "BB":
            verb = "posts big blind"
        elif hand.get("straddle") and abs(s["posted"] - hand["straddle"]) < 1e-9:
            verb = "posts straddle"
        else:
            # A new or returning player's post: PokerStars writes it as a big blind.
            verb = "posts big blind"
        lines.append("%s: %s %s" % (s["label"], verb, money(s["posted"])))
    return lines


def write_hand(parsed):
    """One hand as PokerStars text, or None with the reason counted in SKIPPED."""
    hand, seats, actions = parsed["hand"], parsed["seats"], parsed["actions"]
    if any(s["stack"] is None for s in seats):
        SKIPPED["a starting stack is unknown"] += 1
        return None
    names = {s["seat"]: s["label"] for s in seats}
    day, clock = hand["played_at"].split(" ")
    hh, mm, ss = clock.split(":")
    lines = [
        "PokerStars Hand #%d:  Hold'em No Limit (%s/%s USD) - %s %d:%s:%s UTC"
        % (hand_number(hand["hand_id"]), money(hand["sb"]), money(hand["bb"]),
           day.replace("-", "/"), int(hh), mm, ss),
        "Table 'ClubWPT %s' %d-max Seat #%d is the button"
        % (hand["table_id"], hand["max_seats"], _button(seats)),
    ]
    lines += ["Seat %d: %s (%s in chips)" % (s["seat"], s["label"], money(s["stack"])) for s in seats]
    lines += _post_lines(hand, seats)
    lines.append("*** HOLE CARDS ***")
    hero = next((s for s in seats if s["is_hero"] and s["cards"]), None)
    if hero:
        lines.append("Dealt to %s [%s]" % (hero["label"], hero["cards"]))
    board = hand["board"].split() if hand["board"] else []
    commit = defaultdict(Decimal)
    for s in seats:
        # An ante is dead money: it counts toward no bet, so the street starts at the blinds.
        commit[s["seat"]] = Decimal(str(s["posted"])) - Decimal(str(s.get("ante") or 0))
    street = "preflop"
    for action in actions:
        if action["street"] != street:
            street = action["street"]
            commit = defaultdict(Decimal)
            shown = {"flop": 3, "turn": 4, "river": 5}[street]
            old = "[%s]" % " ".join(board[:shown - (0 if street == "flop" else 1)])
            lines.append("*** %s *** %s" % (STREET_HEADS[street], old if street == "flop"
                                             else old + " [%s]" % board[shown - 1]))
        seat, letter = action["seat"], action["action"]
        name = names[seat]
        if letter == "F":
            lines.append("%s: folds" % name)
            continue
        if letter == "X":
            lines.append("%s: checks" % name)
            continue
        added = Decimal(str(action["amount"]))
        price = max(commit.values()) if commit else Decimal(0)
        commit[seat] += added
        tail = " and is all-in" if action.get("allin") else ""
        if letter == "C":
            lines.append("%s: calls %s%s" % (name, money(added), tail))
        elif letter == "B":
            lines.append("%s: bets %s%s" % (name, money(added), tail))
        else:
            lines.append("%s: raises %s to %s%s" % (name, money(commit[seat] - price),
                                                    money(commit[seat]), tail))
    # The bet nobody called comes back before the pot is pushed.
    ranked = sorted(commit.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] > ranked[1][1]:
        lines.append("Uncalled bet (%s) returned to %s" % (money(ranked[0][1] - ranked[1][1]), names[ranked[0][0]]))
    elif len(ranked) == 1 and ranked[0][1] > 0:
        lines.append("Uncalled bet (%s) returned to %s" % (money(ranked[0][1]), names[ranked[0][0]]))
    for s in seats:
        if s["won"]:
            lines.append("%s collected %s from pot" % (s["label"], money(s["won"])))
    lines.append("*** SUMMARY ***")
    lines.append("Total pot %s | Rake %s" % (money(hand["pot"]), money(hand["rake"] or 0)))
    if board:
        lines.append("Board [%s]" % " ".join(board))
    for s in seats:
        lines.append("Seat %d: %s %s" % (s["seat"], s["label"],
                                          "collected (%s)" % money(s["won"]) if s["won"] else "folded"))
    return "\n".join(lines) + "\n"


def _same(original, again):
    """Did the PokerStars reading give back the hand that was written?"""
    if again is None:
        return "the PokerStars reader did not recognise it"
    mine = {s["seat"]: s for s in original["seats"]}
    theirs = {s["seat"]: s for s in again["seats"]}
    if set(mine) != set(theirs):
        return "seats differ on reading back"
    for seat, s in mine.items():
        t = theirs[seat]
        if s["position"] != t["position"]:
            return "positions differ on reading back"
        for key in ("posted", "invested", "won", "stack"):
            if abs((s[key] or 0) - (t[key] or 0)) > 0.0051:
                return "%s differs on reading back" % key
    if [(a["street"], a["seat"], a["action"]) for a in original["actions"]] != \
            [(a["street"], a["seat"], a["action"]) for a in again["actions"]]:
        return "actions differ on reading back"
    return None


def export(paths, out_dir):
    """Write every hand that loads and survives the round trip; return (written, skipped)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    by_table = defaultdict(list)
    skipped = 0
    for path in paths:
        with open(path, encoding="utf-8-sig") as fh:
            for block in clubwptocr.split_hands(fh.read()):
                parsed = clubwptocr.parse_hand(block, source=str(path))
                if parsed is None:
                    continue
                text = write_hand(parsed)
                problem = None if text else "skipped"
                if text:
                    problem = _same(parsed, pokerstars.parse_hand(text, source="round-trip"))
                    if problem:
                        SKIPPED[problem] += 1
                if problem:
                    skipped += 1
                    continue
                by_table[parsed["hand"]["table_id"]].append((parsed["hand"]["played_at"], text))
    written = 0
    for table, hands in by_table.items():
        hands.sort()
        target = out / ("ClubWPT %s.txt" % table)
        known = target.read_text(encoding="utf-8") if target.exists() else ""
        with open(target, "a", encoding="utf-8") as fh:
            for _, text in hands:
                if text.splitlines()[0] in known:
                    continue
                fh.write(text + "\n\n")
                written += 1
    return written, skipped
