"""
Read PokerStars hand histories.

The third site, and the first added after `sites.py` existed: this file,
one registry entry, and nothing else changed. What it gives is what ACR
gives -- names that are the same person next week, the rake written on
every pot, the button stated outright -- so the registry entry reads the
same as ACR's, and everything that asks "does this site have people" gets
the same answer.

The text is the dialect the Winning Poker Network copied, so the shape of
this parser is `acr.py`'s. The differences are all surface: the header
names the site and puts the stakes in brackets, the table name is quoted,
a player's actions are written "name: verb" with a colon, and what came
back from the pot is "name collected $X from pot" on its own line rather
than in the summary. Zoom hands announce themselves in the header and
are `fmt='ZOOM'`; a name persists across Zoom hands exactly as across
ring ones, so a Zoom seat is still a person here.

This is the parser and nothing else: the header a hand begins with, how a
file splits into hands, and one hand as the dict shape every site's parser
returns. Loading, the schema and the checks are `importer.py` and
`sites.py`.

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re

from acr import _all_money, _money, name_positions

# PokerStars names itself on the first line of every hand. The header is
# the only thing a file is identified by -- never the folder it was in.
HEADER = lambda line: line.startswith("PokerStars ") and " Hand #" in line[:40]

# "PokerStars Hand #261810334287:  Hold'em No Limit ($0.50/$1.00 USD) -
# 2026/08/20 9:43:54 ET". The hour is not zero-padded, and a Zoom hand
# reads "PokerStars Zoom Hand #".
HAND_RE = re.compile(
    r"^PokerStars (Zoom )?Hand #(\d+):\s+(.+?)\s+\(\$?([\d.,]+)/\$?([\d.,]+)"
    r"(?: [A-Z]{3})?\)\s+-\s+(\d{4}/\d{2}/\d{2}) (\d{1,2}:\d{2}:\d{2})", re.M)
# "Table 'Pemba III' 6-max Seat #3 is the button"
TABLE_RE = re.compile(r"^Table '(.+?)' (\d+)-max(?: Zoom)? Seat #(\d+) is the button", re.M)
# "Seat 1: eodh ($193.94 in chips)" and "... ($100 in chips) is sitting out"
SEAT_RE = re.compile(r"^Seat (\d+): (.+) \(\$?([\d.,]+) in chips\)(.*)$", re.M)
CARDS_RE = re.compile(r"\[([2-9TJQKA][cdhs](?:\s+[2-9TJQKA][cdhs])*)\]")
STREET_RE = re.compile(r"^\*\*\* (HOLE CARDS|FLOP|TURN|RIVER|SHOW DOWN|SUMMARY) \*\*\*(.*)$", re.M)
DEALT_RE = re.compile(r"^Dealt to (.+?) \[", re.M)
RETURN_RE = re.compile(r"^Uncalled bet \(\$?([\d.,]+)\) returned to (.+)$")
# "red2652 collected $4.28 from pot", "... from side pot", "... from main pot-2"
COLLECTED_RE = re.compile(r"^collected \$?([\d.,]+) from (?:main |side )?pot")
# "Seat 4: eL2P TRabbit showed [Ts Ac] and won ($112.78) with two pair ...
# (pot not awarded as player cashed out)". A player who takes the All-in
# Cash Out is paid by the house and the "collected" line never appears,
# but the pot still went where the cards said and the summary says so.
# Read only when the body gave nobody anything, so it never counts twice.
SUMMARY_WON_RE = re.compile(r"^Seat (\d+): .*? (?:and won|collected) \(\$?([\d.,]+)\)", re.M)
# "Total pot $4.50 | Rake $0.22" -- with side pots the line runs "Total pot
# $50 Main pot $40. Side pot $10. | Rake $2", so the rake is found after
# the bar rather than right after the total.
POT_RE = re.compile(r"^Total pot \$?([\d.,]+).*?\|\s*Rake \$?([\d.,]+)", re.M)
# "posts small blind $0.50", "posts big blind $1", "posts the ante $0.10",
# "posts small & big blinds $1.50" -- a returning player's out-of-turn post
# -- and a bare "posts $1". All of it is live money in the pot.
POST_RE = re.compile(r"^posts (?:small blind|big blind|the ante|small & big blinds|)\s*\$?([\d.,]+)")

STREETS = {"HOLE CARDS": "preflop", "FLOP": "flop",
           "TURN": "turn", "RIVER": "river"}


def parse_hand(text, source=""):
    """One hand, as the same dict shape ignition.parse_hand returns."""
    m = HAND_RE.search(text)
    if not m:
        return None
    zoom, hand_id, game_desc, sb, bb, day, clock = m.groups()
    if "hold'em" not in game_desc.lower():
        return None                      # Omaha files live in the same folder
    hh, mm, ss = clock.split(":")
    played_at = "{} {:0>2}:{}:{}".format(day.replace("/", "-"), hh, mm, ss)

    tm = TABLE_RE.search(text)
    if not tm:
        return None
    table_name, max_seats, button = tm.group(1), int(tm.group(2)), int(tm.group(3))

    seats, by_name = [], {}
    for seat_no, name, stack, trailing in SEAT_RE.findall(text):
        s = {"seat": int(seat_no), "label": name, "stack": _money(stack),
             "is_hero": False, "cards": None, "won": 0.0, "posted": 0.0,
             "invested": 0.0, "returned": 0.0}
        seats.append(s)
        by_name[name] = s
    if len(seats) < 2:
        return None

    dm = DEALT_RE.search(text)
    hero = by_name.get(dm.group(1)) if dm else None
    if hero is not None:
        hero["is_hero"] = True

    # Names may contain spaces and even the words the verbs use, so a line
    # is attributed by matching the longest seat name it starts with, never
    # by splitting on whitespace. An action line is "name: verb"; the
    # collected line is "name collected"; both are tried, colon first.
    names_longest = sorted(by_name, key=len, reverse=True)

    board, actions, street, order = [], [], "preflop", 0
    sb_seat = None
    for raw in text.splitlines():
        sm = STREET_RE.match(raw)
        if sm:
            marker, rest = sm.groups()
            if marker == "SUMMARY":
                break                    # what came back was read as it came
            if marker == "SHOW DOWN":
                continue
            street = STREETS[marker]
            if street != "preflop":
                got = CARDS_RE.findall(rest)
                if got:
                    board += got[-1].split()
            continue
        rm = RETURN_RE.match(raw)
        if rm:
            s = by_name.get(rm.group(2).strip())
            if s is not None:
                s["returned"] += _money(rm.group(1)) or 0.0
            continue
        if raw.startswith("Dealt to "):
            cm = CARDS_RE.search(raw)
            if cm and hero is not None:
                hero["cards"] = cm.group(1)
            continue

        who = next((n for n in names_longest
                    if raw.startswith(n + ": ") or raw.startswith(n + " collected ")),
                   None)
        if who is None:
            continue
        s = by_name[who]
        rest = raw[len(who) + 1:].strip()
        if rest.startswith(": "):
            rest = rest[2:]

        cm_ = COLLECTED_RE.match(rest)
        if cm_:
            # A player can collect from a main pot and a side pot in one
            # hand; `won` is everything that came back, so they add.
            s["won"] += _money(cm_.group(1)) or 0.0
            continue
        pm = POST_RE.match(rest)
        if pm:
            s["posted"] += _money(pm.group(1)) or 0.0
            if rest.startswith("posts small blind"):
                sb_seat = s["seat"]
            continue
        if rest.startswith(("shows", "mucks", "doesn't show")):
            cm = CARDS_RE.search(rest)
            if cm and len(cm.group(1).split()) == 2:
                s["cards"] = cm.group(1)
            continue
        if rest.startswith(("sits out", "is sitting out", "leaves", "joins",
                            "is connected", "is disconnected", "has timed out",
                            "was removed", "will be allowed", "said,",
                            "has returned", "stands up", "re-buys",
                            "doesn't show")):
            continue

        allin = "and is all-in" in rest
        verb = amount = total = None
        if rest.startswith("folds"):
            verb = "F"
        elif rest.startswith("checks"):
            verb = "X"
        elif rest.startswith("calls"):
            verb, amount = "C", _money(rest)
        elif rest.startswith("bets"):
            verb, amount = "B", _money(rest)
        elif rest.startswith("raises"):
            # "raises $1.51 to $2.51" -- added first, street total second.
            nums = _all_money(rest)
            verb = "R"
            amount = nums[0] if nums else None
            total = nums[1] if len(nums) > 1 else None
        if verb is None:
            continue
        order += 1
        actions.append({"street": street, "n": order, "position": None,
                        "seat": s["seat"], "action": verb, "amount": amount,
                        "total": total, "allin": int(allin)})

    if not any(s["won"] for s in seats):
        summary = text[text.find("*** SUMMARY ***"):]
        for seat_no, amount in SUMMARY_WON_RE.findall(summary):
            for s in seats:
                if s["seat"] == int(seat_no):
                    s["won"] += _money(amount) or 0.0

    # Who was actually in the hand. A seat listed as sitting out is at the
    # table and not in the deal; left in, it never folds and reaches every
    # showdown. The seat list is the room; the hand is whoever put money
    # in, acted, or was dealt cards.
    acted = {a["seat"] for a in actions}
    seats = [s for s in seats
             if s["seat"] in acted or s["posted"] or s["cards"] or s["is_hero"]]
    if len(seats) < 2:
        return None

    # Positions, from the stated button, confirmed by who posted the small
    # blind -- a player dealt in out of turn can otherwise put every name
    # one seat off.
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

    # What each player put in by choice. "raises $1 to $2" means the bet
    # went UP by $1 to stand at $2, and a player who had nothing in yet put
    # in the whole $2 -- so a raise adds its total less what that seat had
    # already committed on the street, never its first figure. WPN writes
    # the added amount first and `acr.py` can take it as it comes; taking
    # it that way here left every open a big blind short and the money
    # check at 0% on the first hand it saw. Blinds count as committed:
    # the big blind who "calls $1" a $2 open has $2 in, not $1.
    street_in = {s["seat"]: s["posted"] for s in seats}
    on_street = "preflop"
    for a in actions:
        if a["street"] != on_street:
            on_street = a["street"]
            street_in = {s["seat"]: 0.0 for s in seats}
        if a["action"] == "R" and a["total"] is not None:
            added = max(0.0, a["total"] - street_in.get(a["seat"], 0.0))
            a["amount"] = round(added, 4)
            street_in[a["seat"]] = a["total"]
        elif a["action"] in ("C", "B") and a["amount"]:
            added = a["amount"]
            street_in[a["seat"]] = street_in.get(a["seat"], 0.0) + added
        else:
            continue
        for s in seats:
            if s["seat"] == a["seat"]:
                s["invested"] += added
    for s in seats:
        s["invested"] = round(s["invested"] - s["returned"], 4)

    posted_bb = any(s["posted"] and s["position"] == "BB" for s in seats)
    standard = int(sb_seat is not None and posted_bb)

    pot_m = POT_RE.search(text)
    return {
        "hand": {"hand_id": "ps-" + hand_id, "played_at": played_at,
                 "table_id": table_name, "game": "HOLDEM",
                 "fmt": "ZOOM" if zoom else "RING",
                 "sb": _money(sb), "bb": _money(bb), "n_players": len(seats),
                 "board": " ".join(board),
                 "pot": _money(pot_m.group(1)) if pot_m else None,
                 "rake": _money(pot_m.group(2)) if pot_m else None,
                 "jp_fee": None,
                 "hero_seat": hero["seat"] if hero else None,
                 "standard": standard, "source": source,
                 "max_seats": max_seats},
        "seats": seats,
        "actions": actions,
    }


def split_hands(text):
    starts = [m.start() for m in HAND_RE.finditer(text)]
    for i, a in enumerate(starts):
        yield text[a:starts[i + 1] if i + 1 < len(starts) else len(text)]
