"""
PartyPoker hand histories, into the same shape as the other parsers.

The format is the oldest still in use: a hand begins "***** Hand History
for Game N *****", the stakes are on the next line in prose, a raise is
written as the amount it was raised TO, the winner's line includes any bet
that was never called, and nothing says what the rake was -- so the money
proof for this site is the one Ignition uses, in = pot, with the pot
being what went in and the winnings bounded below it. The 2016 client
lowercased "seat", the 2013 Mac client wrote a summary with the rake in
it, and a decade of exports carry a period after every verb or none; all
of it is one format with a loose hand, and FPDB's thirty-one fixtures are
the specification.

Some hands a PartyPoker history writes cannot be placed at the table: a
player who posts a blind without a seat line has no seat number, and a
seat number is how the ring is walked to name positions. Those hands are
refused rather than given a position by guesswork.

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re
from collections import Counter

from acr import name_positions

AMOUNT_RE = re.compile(r"\d[\d,.]*")


def _money(text):
    """
    The first figure in a line, in either of the two ways this client
    writes one. A European install writes "$0,25 USD" with a decimal comma
    and an American one "$1,000.50" with a thousands comma; a comma that
    is the last separator and has one or two digits after it is the point.
    """
    m = AMOUNT_RE.search(text or "")
    if not m:
        return None
    raw = m.group(0)
    if re.fullmatch(r"\d+,\d{1,2}", raw):
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _all_money(text):
    out = []
    for raw in AMOUNT_RE.findall(text or ""):
        got = _money(raw)
        if got is not None:
            out.append(got)
    return out

# Verbs the action loop met and did not know, by first word -- the fixture
# check reads it, because a verb dropped here is money dropped silently.
UNKNOWN = Counter()

# A hand announces itself with a row of asterisks; the client also writes
# "#Game No : N" above it and "Game #N starts." before that, and an export
# can begin with any of the three.
HEADER = lambda line: (line.startswith(("***** Hand History", "#Game No"))
                       or bool(re.match(r"^Game #\d+ starts", line)))
HAND_RE = re.compile(r"^\*{5} Hand History [Ff]or Game (\d+) \*{5}", re.M)

CUR = r"[$€£]?"
# "$0.02/$0.04 USD NL Texas Hold'em - Tuesday, July 31, 21:08:37 YEKST
# 2012", or "$4 USD NL Texas Hold'em - ..." with only the buy-in, or the
# Mac client's "0.50/1 Texas Hold'em Game Table (NL)  -  Sun Jan 20
# 13:58:07 EST 2013". The blinds are taken from the posts when the line
# has none.
STAKES_RE = re.compile(CUR + r"([\d.]+)/" + CUR + r"([\d.]+)")
DATE_RE = re.compile(r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s*(\w+) (\d+), (\d+):(\d+):(\d+) \w+ (\d{4})")
DATE_MAC_RE = re.compile(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d+) (\d+):(\d+):(\d+) \w+ (\d{4})")
MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
TABLE_RE = re.compile(r"^Table (.+?)\s*\(\s*(?:Real|Play) Money\s*\)", re.M)
BUTTON_RE = re.compile(r"Seat (\d+) is the button", re.M)
PLAYERS_RE = re.compile(r"^Total number of players : (\d+)(?:/(\d+))?", re.M)
# "Seat 6: Player0 ( $3.96 USD )", "seat 1: Player0 ( $11.84 USD )",
# "Seat 1: Player0 ($30)", "Seat 2: x ( €5 EUR )", and a converter's
# "Seat 1: x ( $5 USD ) - VPIP: 21 PFR: ..." after it.
SEAT_RE = re.compile(r"^\s*[Ss]eat (\d+): (.+?) \(\s*" + CUR + r"\s*([\d,.]+)\s*(?:USD|EUR|€)?\s*\)", re.M)
CARDS_RE = re.compile(r"\[\s*([2-9TJQKA][cdhs](?:,?\s+[2-9TJQKA][cdhs])*)\s*\]")
STREET_RE = re.compile(r"^\*\* Dealing (down cards|Flop|Turn|River) \*\*")
DEALT_RE = re.compile(r"^Dealt to (.+?) \[", re.M)
STREETS = {"down cards": "preflop", "Flop": "flop", "Turn": "turn", "River": "river"}
# "Main Pot: $7.13 Rake: $0.37" -- the 2013 client's summary, the only
# place this site ever wrote the rake.
POT_RE = re.compile(r"^Main Pot: " + CUR + r"([\d,.]+) Rake: " + CUR + r"([\d,.]+)", re.M)

# Lines about a player that are neither money nor a decision.
IGNORE = ("has joined", "has left", "is sitting out", "sits out", "will be",
          "has been", "is the button", "balance", "has returned", "is back",
          "sitting out", "joins", "leaves", "did not respond",
          "could not respond", "is disconnected", "has timed out")

# The words a player's line may begin with. The Mac client seated "Hero"
# and then wrote "Hero Name raises", so a line is read from the longest
# seated name it begins with and then skips forward to the first of these.
VERBS = ("posts", "shows", "wins", "does", "doesn't", "mucks", "folds",
         "checks", "calls", "bets", "raises", "is", "has", "will", "sits",
         "balance", "joins", "leaves", "did", "could")


def _when(line):
    m = DATE_RE.search(line)
    if m:
        mon, day, hh, mm, ss, year = m.groups()
        month = MONTHS.get(mon[:3].lower())
    else:
        m = DATE_MAC_RE.search(line)
        if not m:
            return None
        mon, day, hh, mm, ss, year = m.groups()
        month = MONTHS.get(mon.lower())
    if not month:
        return None
    return f"{year}-{month:02d}-{int(day):02d} {int(hh):02d}:{mm}:{ss}"


def parse_hand(text, source=""):
    """One hand, as the same dict shape the other parsers return."""
    m = HAND_RE.search(text)
    if not m:
        return None
    hand_id = m.group(1)
    lines = text[m.end():].splitlines()
    game = next((l.strip() for l in lines if "Hold'em" in l or "Holdem" in l), None)
    if not game or "Double" in game:
        return None                      # Omaha, stud and Double Hold'em share the folder
    played_at = _when(game)
    if not played_at:
        return None
    sm = STAKES_RE.search(game.split(" - ")[0])
    sb, bb = (float(sm.group(1)), float(sm.group(2))) if sm else (None, None)
    fmt = ("MTT" if "Trny:" in game else
           "ZOOM" if "fastforward" in game.lower() else "RING")

    tm = TABLE_RE.search(text)
    bm = BUTTON_RE.search(text)
    if not tm or not bm:
        return None
    table_name, button = tm.group(1).strip(), int(bm.group(1))
    pm = PLAYERS_RE.search(text)
    max_seats = int(pm.group(2) or pm.group(1)) if pm else None

    seats, by_name = [], {}
    for seat_no, name, stack in SEAT_RE.findall(text):
        name = name.strip()
        s = {"seat": int(seat_no), "label": name, "stack": _money(stack),
             "is_hero": False, "cards": None, "won": 0.0, "posted": 0.0,
             "invested": 0.0, "returned": 0.0}
        seats.append(s)
        by_name[name] = s
    if len(seats) < 2:
        return None
    dm = DEALT_RE.search(text)
    hero = None
    if dm:
        # The Mac client dealt to "Hero Name" and seated "Hero": the seat
        # list's name is a prefix of the dealt-to name, and the longest
        # seated name the line starts with is the player.
        hero = next((by_name[n] for n in sorted(by_name, key=len, reverse=True)
                     if dm.group(1) == n or dm.group(1).startswith(n + " ")), None)
    if hero is not None:
        hero["is_hero"] = True
    names_longest = sorted(by_name, key=len, reverse=True)

    board, actions, street, order = [], [], "preflop", 0
    blinds, street_in, sb_seat = {}, {}, None
    seen_action = False
    # A queue rather than the lines themselves, because the client has
    # twice glued two players' lines together with no break between them
    # -- "...with two pairs.Player4 wins $27,36 USD" -- and the second
    # half is a line too. It is cut at the first seated name that follows
    # a period and pushed back to be read on its own.
    pending = list(lines)
    while pending:
        raw = pending.pop(0).strip()
        if not raw or raw.startswith(("#Game", "*****", "Total number", "Table ",
                                      "Seat ", "seat ", "Board:", "** Summary",
                                      "Main Pot", "Game #", "Dealt to")):
            continue
        for n in names_longest:
            cut = raw.find("." + n + " ")
            if cut > 0:
                pending.insert(0, raw[cut + 1:])
                raw = raw[:cut + 1]
                break
        sm_ = STREET_RE.match(raw)
        if sm_:
            street = STREETS[sm_.group(1)]
            if street != "preflop":
                street_in = {}
                cm = CARDS_RE.search(raw)
                if cm:
                    board += cm.group(1).replace(",", " ").split()
            continue

        who = next((n for n in names_longest
                    if raw == n or raw.startswith(n + " ")), None)
        if who is None:
            # A player with no seat line posting or acting -- the client
            # wrote "has joined the table" and nothing else about where.
            # There is no seat to walk the ring from, so the hand cannot
            # name positions and is refused rather than guessed at.
            if re.search(r" (posts|folds|checks|calls|bets|raises|is all-In) ", raw + " "):
                return None
            continue
        s = by_name[who]
        rest = raw[len(who) + 1:].strip().rstrip(".")
        for _ in range(2):
            if rest and not rest.startswith(VERBS) and " " in rest:
                rest = rest.split(" ", 1)[1]
        # "Forward Folds" is the fastforward client's fold in advance.
        if rest.startswith("Forward Fold"):
            rest = "folds"
        # The 2011 client once glued "is sitting out" to the next player's
        # post with no line break between them; the post is still a post.
        if rest.startswith("is sitting out") and " posts " in rest:
            rest = rest[len("is sitting out"):]
            who2 = next((n for n in names_longest if rest.startswith(n + " ")), None)
            if who2 is None:
                continue
            s = by_name[who2]
            rest = rest[len(who2) + 1:].strip().rstrip(".")

        low = rest.lower()
        if low.startswith("balance") and "collected" in low:
            # The 2013 client's summary is the only place it says who won:
            # "balance $100.57, bet $3, collected $3.57, net +$0.57".
            s["won"] += _money(rest.split("collected", 1)[1]) or 0.0
            continue
        if rest.startswith("posts"):
            put = _money(rest) or 0.0
            s["posted"] += put
            # A dead post is a small blind thrown away plus a live big
            # blind; only the live part stands toward a raise "to".
            live = put - (blinds.get("sb") or 0.0) if "dead" in rest else put
            street_in[s["seat"]] = street_in.get(s["seat"], 0.0) + live
            if "small blind" in rest:
                blinds.setdefault("sb", put)
                sb_seat = s["seat"]
            elif "big blind" in rest and "dead" not in rest:
                blinds.setdefault("bb", put)
            continue
        if rest.startswith("shows"):
            cm = CARDS_RE.search(rest)
            if cm and len(cm.group(1).replace(",", " ").split()) == 2:
                s["cards"] = cm.group(1).replace(",", " ")
            continue
        if rest.startswith("wins"):
            # Includes any bet that was never called: the client hands it
            # back inside the winnings rather than on a line of its own.
            s["won"] += _money(rest) or 0.0
            continue
        if rest.startswith(("does not show", "doesn't show", "mucks")):
            continue
        if rest.startswith(IGNORE):
            continue

        verb = amount = total = None
        allin = False
        if low.startswith("folds"):
            verb = "F"
        elif low.startswith("checks"):
            verb = "X"
        elif low.startswith("calls"):
            verb, amount = "C", _money(rest)
        elif low.startswith("bets"):
            verb, amount = "B", _money(rest)
        elif low.startswith("raises"):
            # "raises [$15 USD]" is the amount ADDED -- a $15/$30 limit hand
            # reads "raises [$15]" where the bet stood at $15 and now stands
            # at $30 -- and the converted "raises 3.0 to 3.0" names both.
            nums = _all_money(rest)
            verb = "R"
            if " to " in rest and len(nums) >= 2:
                amount, total = nums[0], nums[1]
            else:
                amount = nums[0] if nums else None
                total = round(street_in.get(s["seat"], 0.0) + amount, 2) if amount is not None else None
        elif low.startswith("is all-in"):
            # The amount put in, for the last of a stack; whether it was a
            # raise or a call is what the street's tally decides.
            amount = _money(rest)
            allin = True
            here = street_in.get(s["seat"], 0.0) + (amount or 0.0)
            high = max(street_in.values(), default=0.0)
            verb = "R" if here > high + 1e-9 else "C"
            total = round(here, 2)
        if verb is None:
            UNKNOWN[rest.split(" ")[0] if rest else "?"] += 1
            continue
        seen_action = True
        if amount:
            street_in[s["seat"]] = street_in.get(s["seat"], 0.0) + amount
        order += 1
        actions.append({"street": street, "n": order, "position": None,
                        "seat": s["seat"], "action": verb, "amount": amount,
                        "total": total, "allin": int(allin)})

    if not seen_action and not any(s["posted"] for s in seats):
        return None

    # The hand is whoever put money in, acted, or was dealt cards; the
    # seat list is the room. A listed seat that never acted never folds
    # and would reach every showdown.
    acted = {a["seat"] for a in actions}
    seats = [s for s in seats
             if s["seat"] in acted or s["posted"] or s["cards"] or s["is_hero"]]
    if len(seats) < 2:
        return None

    ring = sorted(s["seat"] for s in seats)
    start = ring.index(button) if button in ring else len(ring) - 1
    from_btn = ring[start:] + ring[:start]
    order_from_sb = from_btn[1:] + from_btn[:1] if len(ring) > 2 else from_btn
    if sb_seat is not None and sb_seat in order_from_sb:
        i = order_from_sb.index(sb_seat)
        order_from_sb = order_from_sb[i:] + order_from_sb[:i]
    naming = name_positions(order_from_sb)
    for s in seats:
        s["position"] = naming.get(s["seat"], "?")
    pos_of = {s["seat"]: s["position"] for s in seats}
    actions = [a for a in actions if a["seat"] in pos_of]
    for a in actions:
        a["position"] = pos_of[a["seat"]]

    for a in actions:
        if a["action"] in ("C", "B", "R") and a["amount"]:
            for s in seats:
                if s["seat"] == a["seat"]:
                    s["invested"] += a["amount"]
    for s in seats:
        s["invested"] = round(s["invested"], 4)

    posted_bb = any(s["posted"] and s["position"] == "BB" for s in seats)
    standard = int(sb_seat is not None and posted_bb)

    # No pot is written and no rake, except by the 2013 client. What went
    # in is the pot; what came out is bounded by it and by the rake a room
    # takes, which is the identity `sites.check` applies to a site with
    # `rake=False`.
    pot_m = POT_RE.search(text)
    went_in = round(sum(s["posted"] + s["invested"] for s in seats), 2)
    return {
        "hand": {"hand_id": "pp-" + hand_id, "played_at": played_at,
                 "table_id": table_name, "game": "HOLDEM", "fmt": fmt,
                 "sb": sb if sb is not None else blinds.get("sb"),
                 "bb": bb if bb is not None else blinds.get("bb"),
                 "n_players": len(seats), "board": " ".join(board),
                 "pot": went_in,
                 "rake": _money(pot_m.group(2)) if pot_m else None,
                 "jp_fee": None,
                 "hero_seat": hero["seat"] if hero else None,
                 "standard": standard, "source": source,
                 "max_seats": max_seats},
        "seats": seats,
        "actions": actions,
    }


def split_hands(text):
    """Each hand in a file, as its own block of text."""
    starts = [m.start() for m in HAND_RE.finditer(text)]
    for i, a in enumerate(starts):
        yield text[a:starts[i + 1] if i + 1 < len(starts) else len(text)]
