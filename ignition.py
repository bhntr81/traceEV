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

This is the parser and nothing else: the header a hand begins with, how a
file splits into hands, and one hand as the dict shape every site's parser
returns. Loading, the schema and the checks are `importer.py` and
`sites.py`; what is true of this site is its registry entry there.

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re
from collections import Counter

# Verbs the action loop met and did not know, by first word. A parser drops
# what it cannot read without a sound, and every figure downstream comes out
# slightly wrong and entirely plausible; this tally is how the fixture check
# hears it.
UNKNOWN = Counter()

# Ignition names itself on the first line of every hand. The header is the
# only thing a file is identified by -- never the folder it was found in.
# Ignition, Bovada and Bodog are one network and one format; the name on
# the first line is the skin. Fourteen of sixteen Bovada fixtures went
# unrecognised for the want of this line.
HEADER = lambda line: bool(BRAND_RE.match(line))
BRAND_RE = re.compile(r"^(?:Ignition|Bovada|Bodog(?:\.\w+| UK)?) Hand #")

# The format and the stakes are in the file's name -- "HH20260825-052504 -
# 800 - RING - $0.10-$0.25 - HOLDEM - NL - TBL No..." -- which is why
# `parse_hand` needs `source` for more than provenance. The hand text says
# less clearly, and is believed second.
FILENAME_RE = re.compile(r" - (RING|ZONE|MTT) - (?:\$([\d.]+)-\$([\d.]+))?", re.I)

# The three formats write their middle section differently -- a ring hand
# says "TBL#37661151 HOLDEM No Limit", a Zone hand "Zone Poker ID#2138
# HOLDEM Zone Poker No Limit", a tournament "HOLDEM Tournament #74344980
# TBL#63, Normal- Level 1 (10/20)" -- so the middle is taken whole and
# picked apart afterwards. Insisting on TBL# silently dropped every Zone
# and tournament hand, which was a fifth of the collection.
HAND_RE = re.compile(
    r"^(?:Ignition|Bovada|Bodog(?:\.\w+| UK)?) Hand #(\d+)\s*:?\s*(.*?)\s+-\s+"
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
# "Total Pot($56) | Rake ($2)" -- the 2012 client wrote the rake; the
# current one does not, and `Site.rake` says so. Read when it is there.
POT_RE = re.compile(r"Total Pot\(\$?([\d,.]+)\)(?:\s*\|\s*Rake \(\$?([\d,.]+)\))?")

# Lines that are housekeeping rather than something a player chose to do.
IGNORE = ("Set dealer", "Enter", "Leave", "Seat sit", "Seat stand", "Seat re",
          "Sitout", "Sit out", "Re-join", "Stand", "Ranking", "Draw for",
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
# The 2012 client wrote the blinds as "Ante/Small Blind" and "Big
# blind/Bring in", stud's words on a hold'em table; a decade of Bovada
# histories has them.
POSTS = ("Small Blind", "Small blind", "Big Blind", "Big blind",
         "Ante/Small Blind", "Big blind/Bring in", "Big Blind/Bring in",
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
    game = "OMAHA" if "OMAHA" in middle.upper() else "HOLDEM"

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
    # A seat number listed twice is two hands under one header -- an
    # export cut off mid-summary and glued to the next hand. Read as one
    # hand it puts the second hand's actions on the first hand's seats and
    # every figure in it is fiction; refused, it is one hand lost.
    if len({s["seat"] for s in seats}) < len(seats):
        return None

    naming = positions_for(len(seats))
    by_label = {}
    for s in seats:
        # A tournament table's labels can run past what the seats dealt
        # in explain -- "UTG+5" at a seven-handed table with two empty
        # chairs -- and a label the map does not know is an early seat,
        # which the program's six names call UTG.
        s["position"] = naming.get(s["label"],
                                   "UTG" if s["label"].startswith("UTG") else s["label"])
        by_label[s["label"]] = s

    board, actions, street, order = [], [], "preflop", 0
    # The blinds as the hand posted them, for a file whose name has lost
    # the stakes; and whether the cards are out yet, because nothing before
    # the deal is a decision.
    blinds, dealt = {}, False
    # What each seat has put in on the current street, blinds included. The
    # 2012 client wrote a raise as one figure, the street total -- "Raises
    # $24" from a small blind with $4 in is $20 added -- and the amount
    # added is what `invested` sums. FPDB keeps a file named
    # "raise.to.format.change" for the month this switched.
    street_in = {}
    for raw in text.splitlines():
        sm = STREET_RE.match(raw)
        if sm:
            marker, rest = sm.groups()
            if marker == "SUMMARY":
                break
            street = STREETS[marker]
            dealt = True
            if street != "preflop":
                # "*** TURN *** [Ac 7d 2s] [2h]" -- the last bracket is the
                # new card, the first repeats what is already down. The
                # blinds were posted before the HOLE CARDS marker, so that
                # one must not clear the street's tally.
                street_in = {}
                got = CARDS_RE.findall(rest)
                if got:
                    board += got[-1].split()
            continue
        if raw.startswith("Card dealt to table"):
            # The 2012 client had no street markers; the board arrived as
            # "Card dealt to table [As 9d 2c]" and then one card at a time,
            # and the street is however many cards are down.
            cm = CARDS_RE.search(raw)
            if cm:
                board += cm.group(1).split()
                street = {3: "flop", 4: "turn", 5: "river"}.get(len(board), street)
                street_in = {}
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
                if len(got) in (2, 4) and (seat["cards"] is None
                                           or rest.startswith("Card dealt")):
                    seat["cards"] = " ".join(got)
            continue
        # "Hand result", "Hand Result", "Hand result-Side pot": the
        # tournament client capitalises differently from the ring client.
        if rest[:11].lower() == "hand result":
            # What came back from the pot, NOT profit -- a player who wins a
            # pot they built themselves shows a large "Hand result" and may
            # have lost money on the hand. Summing these alone said hero was
            # up $996 over 2568 hands of 10NL, which is about 390bb/100.
            # And added, not set: a hand with a side pot writes "Hand
            # result-Side pot" and "Hand result" to the same seat.
            seat["won"] += _money(rest) or 0.0
            continue
        # A player all-in for less than the blind is written "All-in $8"
        # where the post would be, before the deal. It is money the player
        # was made to put up, not a shove; counted as a decision it is a
        # raise from the big blind before anybody has cards.
        if rest.startswith(POSTS) or (not dealt and rest.startswith("All-in")):
            put = _money(rest) or 0.0
            seat["posted"] += put
            # A dead post is the small blind's worth thrown away plus a live
            # big blind, and only the live part stands toward a raise "to".
            live = put - blinds.get("sb", 0.0) if rest.startswith("Posts dead") else put
            street_in[seat["seat"]] = street_in.get(seat["seat"], 0.0) + live
            forced = rest.startswith("All-in")
            if "Big" in rest[:20] or (forced and label == "Big Blind"):
                blinds.setdefault("bb", put)
            elif "Small" in rest[:20] or (forced and label == "Small Blind"):
                blinds.setdefault("sb", put)
            continue
        rm = RETURN_RE.match(rest)
        if rm:
            seat["returned"] += _money(rm.group(1)) or 0.0
            continue
        if rest.startswith(IGNORE):
            continue

        # The tournament client writes "Call 20" for the ring client's
        # "Calls $0.25", and a disconnected player's forced fold as
        # "Fold(Blind Disconnected)". Both were unknown verbs and so
        # dropped: 391 calls, 367 folds, in one tournament folder. A fold
        # not recorded is a seat that never folds, and reaches every
        # showdown.
        verb = amount = total = None
        if rest.startswith(("Folds", "Fold(")):
            verb = "F"
        elif rest.startswith("Checks"):
            verb = "X"
        elif rest.startswith(("Calls", "Call ")):
            verb, amount = "C", _money(rest)
        elif rest.startswith("Bets"):
            verb, amount = "B", _money(rest)
        elif rest.startswith("Raises"):
            # "Raises $1.45 to $1.55" -- the second figure is what the bet
            # now stands at, which is the one worth comparing across hands.
            nums = _all_money(rest)
            verb, amount = "R", (nums[0] if nums else None)
            total = nums[-1] if nums else None
            if len(nums) == 1:
                amount = round(total - street_in.get(seat["seat"], 0.0), 2)
        elif rest.startswith("All-in"):
            nums = _all_money(rest)
            verb = "R" if "(raise)" in rest else "A"
            amount = nums[0] if nums else None
            total = nums[-1] if nums else amount
            if verb == "R" and len(nums) == 1:
                # The 2012 one-figure form again: the street total.
                amount = round(total - street_in.get(seat["seat"], 0.0), 2)
        if verb is None:
            # Not silently. A verb this parser does not know is money it did
            # not count, and the tally is what the fixture check reads.
            UNKNOWN[rest.split(" ")[0]] += 1
            continue
        if amount:
            street_in[seat["seat"]] = street_in.get(seat["seat"], 0.0) + amount
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
    # The format and the stakes are in the client's filename, and a file
    # that has been renamed, forwarded, or kept by somebody else's tracker
    # has lost them. The hand itself still says: "Zone Poker" and
    # "Tournament" are in the header, and the blinds are posted in the
    # first two actions. The filename is believed first, because it is the
    # client's own word, and the hand second, because it is always there.
    fmt = fm.group(1).upper() if fm else (
        "ZONE" if "Zone" in game else "MTT" if "Tournament" in game else "RING")
    sb = float(fm.group(2)) if fm and fm.group(2) else blinds.get("sb")
    bb = float(fm.group(3)) if fm and fm.group(3) else blinds.get("bb")
    # No summary, no stated pot. It happens on a truncated export; the
    # money that went in is still the money that went in, and it is the
    # best statement of the pot there is.
    pot = _money(pm.group(1)) if pm else round(
        sum(s["posted"] + s["invested"] for s in seats), 2)
    return {
        "hand": {"hand_id": hand_id, "played_at": played_at,
                 "table_id": table_id, "game": game.strip(),
                 "fmt": fmt, "sb": sb, "bb": bb,
                 "n_players": len(seats), "board": " ".join(board),
                 "pot": pot,
                 "rake": _money(pm.group(2)) if pm and pm.group(2) else None,
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
