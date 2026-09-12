"""
Read Ignition hand histories -- the Bodog network format.

Ignition gives away something almost no site does: the hole cards of every
player in every hand, including everyone who folded preflop. Not only the
showdowns -- the whole deal. That removes the usual problem with population
work, which is that you only ever see the hands somebody was willing to
show you, and those are not a fair sample of the hands they held.

What it does not give is identity. Players are labelled by position, and
position rotates every hand, so there is no name to follow. Seat numbers do
persist within a table, so on RING tables a player can be followed across a
session; on ZONE the client moves you after every hand, so every seat is a
stranger and only aggregates mean anything there.

Omaha is tagged from the header through `games.of` -- including
5-card, whose name contains "OMAHA" and used to be stored as OMAHA.
Four or five hole cards are kept.

This is the parser and nothing else: the header a hand begins with, how a
file splits into hands, and one hand as the dict shape every site's parser
returns. Loading, the schema and the checks are `importer.py` and
`sites.py`; what is true of this site is its registry entry there.

    python ignition.py --check      Omaha tagged, four and five hole cards kept

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re
import sys

import games

# Ignition names itself on the first line of every hand. The header is the
# only thing a file is identified by -- never the folder it was found in.
HEADER = lambda line: line.startswith("Ignition Hand #")

# The format and the stakes are not in the hand text at all; they are in
# the file's name -- "HH20260825-052504 - 800 - RING - $0.10-$0.25 - HOLDEM
# - NL - TBL No..." -- which is why `parse_hand` needs `source` for more
# than provenance.
FILENAME_RE = re.compile(r" - (RING|ZONE|MTT) - (?:\$([\d.]+)-\$([\d.]+))?", re.I)

# The three formats write their middle section differently -- a ring hand
# says "TBL#37661151 HOLDEM No Limit", a Zone hand "Zone Poker ID#2138
# HOLDEM Zone Poker No Limit", a tournament "HOLDEM Tournament #74344980
# TBL#63, Normal- Level 1 (10/20)" -- so the middle is taken whole and
# picked apart afterwards. Insisting on TBL# silently dropped every Zone
# and tournament hand, which was a fifth of the collection.
HAND_RE = re.compile(
    r"^Ignition Hand #(\d+)\s*:?\s*(.*?)\s+-\s+"
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.M)
TABLE_RE = re.compile(r"(?:TBL#|ID#|Tournament #)(\w+)")
SEAT_RE = re.compile(
    r"^Seat (\d+): (.+?)\s*(\[ME\])?\s*\(\$?([\d.,]+) in chips\)", re.M)
# "Big Blind  [ME] : Raises $1.45 to $1.55" -- the marker takes one space or
# two depending on the line, so this cannot be a fixed split.
LINE_RE = re.compile(r"^(.+?)\s*(\[ME\])?\s*:\s*(.+)$")
STREET_RE = re.compile(r"^\*\*\* (HOLE CARDS|FLOP|TURN|RIVER|SUMMARY) \*\*\*(.*)$")
CARDS_RE = re.compile(r"\[([2-9TJQKA][cdhs](?:\s+[2-9TJQKA][cdhs])*)\]")
MONEY_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)")
POT_RE = re.compile(r"Total Pot\(\$?([\d,.]+)\)")

# Lines that are housekeeping rather than something a player chose to do.
IGNORE = ("Set dealer", "Enter", "Leave", "Seat sit", "Seat stand", "Seat re",
          "Table leave", "Table deposit", "Table enter")

# "Dealer : Return uncalled portion of bet $0.60" -- money bet but never
# matched, handed straight back. It has to come off what the player put in,
# or a raise that took the pot down looks like a raise that was called.
RETURN_RE = re.compile(r"Return uncalled portion of bet\s*\$?([\d,.]+)")

# Money a player is made to put up rather than chooses to: blinds, the post
# a returning player owes, antes. Not decisions, so not actions -- but they
# have to be counted, because profit is what came back minus everything
# that went in. "Posts dead chip" is the dead post, and it was missing from
# this list until the money check first ran against Ignition: a verb not
# listed here falls through the parser without a sound, and the pot had
# money come out of it that never went in.
POSTS = ("Small Blind", "Small blind", "Big Blind", "Big blind",
         "Posts chip", "Posts dead chip", "Ante chip")

REVEALS = ("Card dealt", "Showdown", "Mucks", "Does not show")

STREETS = {"HOLE CARDS": "preflop", "FLOP": "flop",
           "TURN": "turn", "RIVER": "river"}


def _money(text):
    m = MONEY_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _all_money(text):
    out = []
    for raw in MONEY_RE.findall(text or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return out


def positions_for(n):
    """
    Ignition's labels turned into the usual position names.

    A short table loses its EARLY seats, not its late ones: five players are
    HJ, CO, BTN, SB and BB, because the first to act is closer to the button
    than a full-ring UTG is and is not playing a UTG range. Ignition names
    them UTG, UTG+1, ... whatever the table size, so the early labels are
    mapped onto the TAIL of the early positions rather than the head.
    """
    if n <= 2:
        return {"Dealer": "SB", "Small Blind": "SB", "Big Blind": "BB"}
    out = {"Dealer": "BTN", "Small Blind": "SB", "Big Blind": "BB"}
    n_early = max(0, n - 3)
    early = ["UTG", "HJ", "CO"][3 - n_early:] if n_early <= 3 else \
        ["UTG"] * (n_early - 3) + ["UTG", "HJ", "CO"]
    for i, name in enumerate(early):
        out["UTG" if i == 0 else f"UTG+{i}"] = name
    return out


def parse_hand(text, source=""):
    """One hand, as a dict of hand / seats / actions."""
    m = HAND_RE.search(text)
    if not m:
        return None
    hand_id, middle, played_at = m.groups()
    tm = TABLE_RE.search(middle)
    table_id = tm.group(1) if tm else ""
    # The filename used to be the only place the variant was written, and
    # "OMAHA" in the middle was a boolean. Five-card Omaha contains
    # "OMAHA" too, so the registry decides, and a header that names
    # nothing it knows is Hold'em -- that is still the game this site
    # almost always writes.
    game = games.of(middle) or "HOLDEM"

    seats, hero_seat = [], None
    for seat_no, label, me, stack in SEAT_RE.findall(text):
        seats.append({"seat": int(seat_no), "label": label.strip(),
                      "stack": _money(stack), "is_hero": bool(me),
                      "cards": None, "won": 0.0, "posted": 0.0,
                      "invested": 0.0, "returned": 0.0})
        if me:
            hero_seat = int(seat_no)
    if not seats:
        return None

    naming = positions_for(len(seats))
    by_label = {}
    for s in seats:
        s["position"] = naming.get(s["label"], s["label"])
        by_label[s["label"]] = s

    board, actions, street, order = [], [], "preflop", 0
    for raw in text.splitlines():
        sm = STREET_RE.match(raw)
        if sm:
            marker, rest = sm.groups()
            if marker == "SUMMARY":
                break
            street = STREETS[marker]
            if street != "preflop":
                # "*** TURN *** [Ac 7d 2s] [2h]" -- the last bracket is the
                # new card, the first repeats what is already down.
                got = CARDS_RE.findall(rest)
                if got:
                    board += got[-1].split()
            continue

        lm = LINE_RE.match(raw)
        if not lm:
            continue
        label, rest = lm.group(1).strip(), lm.group(3).strip()
        seat = by_label.get(label)
        if seat is None:
            continue

        # Cards turn up in four different lines. "Card dealt" is the whole
        # deal and is authoritative; the others only confirm it, and matter
        # for the rare hand where the deal line is missing.
        if rest.startswith(REVEALS):
            cm = CARDS_RE.search(rest)
            if cm:
                got = cm.group(1).split()
                # Two, four or five. The count used to stop at four, so a
                # 5-card Omaha deal was stored as no cards and looked like
                # a fold that was never shown.
                if len(got) in games.HOLE_COUNTS and (
                        seat["cards"] is None
                        or rest.startswith("Card dealt")):
                    seat["cards"] = " ".join(got)
            continue
        if rest.startswith("Hand result"):
            # What came back from the pot, NOT profit -- a player who wins a
            # pot they built themselves shows a large "Hand result" and may
            # have lost money on the hand. Summing these alone said hero was
            # up $996 over 2568 hands of 10NL, which is about 390bb/100.
            seat["won"] = _money(rest) or 0.0
            continue
        if rest.startswith(POSTS):
            seat["posted"] += _money(rest) or 0.0
            continue
        rm = RETURN_RE.match(rest)
        if rm:
            seat["returned"] += _money(rm.group(1)) or 0.0
            continue
        if rest.startswith(IGNORE):
            continue

        verb = amount = total = None
        if rest.startswith("Folds"):
            verb = "F"
        elif rest.startswith("Checks"):
            verb = "X"
        elif rest.startswith("Calls"):
            verb, amount = "C", _money(rest)
        elif rest.startswith("Bets"):
            verb, amount = "B", _money(rest)
        elif rest.startswith("Raises"):
            # "Raises $1.45 to $1.55" -- the second figure is what the bet
            # now stands at, which is the one worth comparing across hands.
            nums = _all_money(rest)
            verb, amount = "R", (nums[0] if nums else None)
            total = nums[-1] if nums else None
        elif rest.startswith("All-in"):
            nums = _all_money(rest)
            verb = "R" if "(raise)" in rest else "A"
            amount = nums[0] if nums else None
            total = nums[-1] if nums else amount
        if verb is None:
            continue
        order += 1
        actions.append({"street": street, "n": order,
                        "position": seat["position"], "seat": seat["seat"],
                        "action": verb, "amount": amount, "total": total})

    # What each player put in by choice. Ignition writes calls, bets and
    # the first figure of a raise as the amount ADDED, so these sum
    # correctly; the "to" figure is the street total and must not be added.
    for a in actions:
        if a["action"] in ("C", "B", "R", "A") and a["amount"]:
            by_seat = next((s for s in seats if s["seat"] == a["seat"]), None)
            if by_seat is not None:
                by_seat["invested"] += a["amount"]
    for st in seats:
        st["invested"] = round(st["invested"] - st["returned"], 2)

    # A hand where somebody missed a blind has a label set that no table
    # size explains -- "UTG, Big Blind, Dealer" with no Small Blind -- so
    # the position names cannot be trusted and the hand is marked rather
    # than quietly mapped to something wrong.
    labels = {s["label"] for s in seats}
    standard = ("Small Blind" in labels and "Big Blind" in labels
                and ("Dealer" in labels or len(seats) == 2))

    pm = POT_RE.search(text)
    fm = FILENAME_RE.search(source)
    return {
        "hand": {"hand_id": hand_id, "played_at": played_at,
                 "table_id": table_id, "game": game.strip(),
                 "fmt": fm.group(1).upper() if fm else "?",
                 "sb": float(fm.group(2)) if fm and fm.group(2) else None,
                 "bb": float(fm.group(3)) if fm and fm.group(3) else None,
                 "n_players": len(seats), "board": " ".join(board),
                 "pot": _money(pm.group(1)) if pm else None,
                 "hero_seat": hero_seat, "standard": int(standard),
                 "source": source},
        "seats": seats,
        "actions": actions,
    }


def split_hands(text):
    """Each hand in a file, as its own block of text."""
    starts = [m.start() for m in HAND_RE.finditer(text)]
    for i, a in enumerate(starts):
        yield text[a:starts[i + 1] if i + 1 < len(starts) else len(text)]


# The WPN Omaha histories in Downloads are `acr.py`'s. These blocks are
# the Ignition-network dialect of the same games, so a 5-card deal on
# this site is stored rather than dropped, and so the tag is OMAHA5
# rather than "contains the word OMAHA".
PLO4 = """\
Ignition Hand #2459808909: TBL#1 OMAHA Pot Limit - 2025-05-19 17:18:05
Seat 1: Small Blind ($9.84 in chips)
Seat 2: Big Blind [ME] ($10.00 in chips)
Seat 3: UTG ($3.90 in chips)
Small Blind : Posts chip $0.05
Big Blind : Posts chip $0.10
*** HOLE CARDS ***
Small Blind : Card dealt [Ah Kh Qd Jd]
Big Blind : Card dealt [3c 2h Kd 2d]
UTG : Card dealt [5s 6s 7s 8s]
UTG : Folds
Small Blind : Folds
Big Blind : Return uncalled portion of bet $0.05
Big Blind : Hand result $0.10
*** SUMMARY ***
Total Pot($0.10)
"""

PLO5 = """\
Ignition Hand #2459651456: TBL#1 5CARD OMAHA Pot Limit - 2025-05-19 17:00:00
Seat 1: Small Blind ($10.00 in chips)
Seat 2: Big Blind [ME] ($10.00 in chips)
Small Blind : Posts chip $0.05
Big Blind : Posts chip $0.10
*** HOLE CARDS ***
Small Blind : Card dealt [Ah Kh Qd Jd Td]
Big Blind : Card dealt [Qh Jh Qd Kc 2c]
Small Blind : Folds
Big Blind : Return uncalled portion of bet $0.05
Big Blind : Hand result $0.10
*** SUMMARY ***
Total Pot($0.10)
"""


def check():
    """Ignition Omaha is tagged, and four or five hole cards are kept."""
    fails = []
    p4 = parse_hand(PLO4, source="HH - RING - $0.05-$0.10 - OMAHA.txt")
    if p4 is None:
        fails.append("PLO4 did not parse")
        print("PLO4 parsed                      NO")
    else:
        h, seats = p4["hand"], {s["label"]: s for s in p4["seats"]}
        inp = sum((s["posted"] or 0) + (s["invested"] or 0)
                  for s in p4["seats"])
        want = [
            (h["game"] == "OMAHA", "game is OMAHA"),
            (seats["Big Blind"]["cards"] == "3c 2h Kd 2d", "hero's four cards"),
            (seats["UTG"]["cards"] == "5s 6s 7s 8s", "folded four cards kept"),
            (h["fmt"] == "RING" and h["bb"] == 0.10, "stakes from the filename"),
            (abs(inp - (h["pot"] or 0)) <= 0.011, "in = stated pot"),
        ]
        bad = [why for ok, why in want if not ok]
        print(f"PLO4 fixture                    {len(want) - len(bad)}/{len(want)}")
        for why in bad:
            print(f"    {why}")
            fails.append(f"PLO4: {why}")

    p5 = parse_hand(PLO5, source="HH - RING - $0.05-$0.10 - OMAHA.txt")
    if p5 is None:
        fails.append("PLO5 did not parse")
        print("PLO5 parsed                      NO")
    else:
        hero = next(s for s in p5["seats"] if s["is_hero"])
        want = [
            (p5["hand"]["game"] == "OMAHA5", "game is OMAHA5, not OMAHA"),
            (hero["cards"] == "Qh Jh Qd Kc 2c", "five hole cards"),
        ]
        bad = [why for ok, why in want if not ok]
        print(f"PLO5 fixture                    {len(want) - len(bad)}/{len(want)}")
        for why in bad:
            print(f"    {why}")
            fails.append(f"PLO5: {why}")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

