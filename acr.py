"""
Read ACR hand histories -- the Winning Poker Network format.

The two sites are worth having together because each has exactly what the
other lacks.

  Ignition  shows every player's hole cards, folds included -- so a range
            can be counted rather than inferred -- but names nobody, so a
            player is only "table:seat" and only within one session.
  ACR       names everyone. A player followed across months is a profile,
            which is the thing an opponent report is for. What it will not
            show is a folded hand, so ranges here are inferred, not seen.

Hold'em and Omaha (4-card and 5-card) share this format. The variant is
`games.of` on the header, not a second skip. A folder of PLO files used
to be "unrecognised" because HEADER required `" - Holdem"`.

This is the parser and nothing else: the header a hand begins with, how a
file splits into hands, and one hand as the dict shape every site's parser
returns. Loading, the schema and the checks are `importer.py` and
`sites.py`. The site this parser belongs to, and what is true of it, is the
registry entry in `sites.py`.

    python acr.py --check           the PLO4/PLO5 fixtures, and Hold'em still loads

Two things ACR gives that Ignition does not: the button is stated
outright, so positions are read rather than reconstructed from labels, and
rake is written on every pot -- plus a jackpot fee, which the money check
found when one hand in five came up short.

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re
import sys

import games

# The Winning Poker Network writes a bare hand number followed by the game.
# The header is the only thing a file is identified by -- never the folder
# it was found in, never which client is installed on the machine. Eight
# thousand hands were once loaded and reported on under the wrong site
# because the site was inferred from the computer instead of the text.
#
# The game word used to be part of this test (" - Holdem"), so a folder of
# Omaha files was "unrecognised" rather than loaded and tagged. The site is
# the format, not the variant; `games.of` reads the variant off the same
# line. Stud and anything else this registry does not name still return
# None from parse_hand.

# "Hand #2459218653 - Holdem (No Limit) - $0.01/$0.02 - 2025/05/18 22:43:28 UTC"
# "Hand #2459808909 - Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:18:05 UTC"
HAND_RE = re.compile(
    r"^Hand #(\d+)\s*-\s*(.+?)\s*-\s*\$?([\d.,]+)/\$?([\d.,]+)\s*-\s*"
    r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", re.M)
HEADER = lambda line: bool(line.startswith("Hand #") and HAND_RE.match(line))
# "Mount Shasta 6-max Seat #5 is the button" -- the table name may itself
# contain digits or spaces, so it is whatever precedes the size.
TABLE_RE = re.compile(r"^(.*?)\s*(\d+)-max\s+Seat #(\d+) is the button", re.M)
# "Seat 3: M3dus4 ($2.00)" and "Seat 6: what now? ($1.20) is sitting out".
# The name runs to the last "(" so that names with brackets or spaces --
# "what now?", "eYe Vee" -- survive.
SEAT_RE = re.compile(r"^Seat (\d+): (.+) \(\$?([\d.,]+)\)(.*)$", re.M)
CARDS_RE = re.compile(r"\[([2-9TJQKA][cdhs](?:\s+[2-9TJQKA][cdhs])*)\]")
MONEY_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)")
STREET_RE = re.compile(r"^\*\*\* (HOLE CARDS|FLOP|TURN|RIVER|SHOW DOWN|SUMMARY) \*\*\*(.*)$", re.M)
DEALT_RE = re.compile(r"^Dealt to (.+?) \[", re.M)
RETURN_RE = re.compile(r"^Uncalled bet \(\$?([\d.,]+)\) returned to (.+)$")
# "Seat 3: M3dus4 did not show and won $0.04", "Seat 2: X showed [..] and won $1.10"
WON_RE = re.compile(r"^Seat (\d+): .* and won \$?([\d.,]+)")
RAKE_RE = re.compile(r"Rake \$?([\d.,]+)")
# "Total pot $1.52 | Rake $0.05 | JP Fee $0.02" -- the summary line is
# authoritative, and the pot loses BOTH deductions. Counting only the rake
# leaves a jackpot-sized hole in one hand in five, which reads as a parsing
# bug in whatever is checking that the money adds up.
POT_RE = re.compile(r"^Total pot \$?([\d.,]+)"
                    r"(?:\s*\|\s*Rake \$?([\d.,]+))?"
                    r"(?:\s*\|\s*JP Fee \$?([\d.,]+))?", re.M)

STREETS = {"HOLE CARDS": "preflop", "FLOP": "flop",
           "TURN": "turn", "RIVER": "river"}

# Money a player is made to put up rather than chooses to. The bare "posts
# $0.05" is a returning player buying back in to the blinds, and it is easy
# to miss because it names nothing -- but it is live money in the pot, and
# leaving it out makes one hand in fifty fail to add up.
POST_RE = re.compile(r"^posts (?:the small blind|the big blind|ante|dead|)\s*\$?([\d.,]+)")


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


def name_positions(order_from_sb):
    """
    Seats in order starting at the small blind, turned into position names.

    Deliberately the same rule Ignition's loader uses, so that a CO on one
    site means the same thing as a CO on the other: a short table loses its
    EARLY seats, not its late ones, because the first player to act at a
    five-handed table sits closer to the button than a full-ring UTG and is
    not playing a UTG range.
    """
    n = len(order_from_sb)
    if n == 2:
        # Heads-up: the button posts the small blind and acts first preflop.
        return {order_from_sb[0]: "SB", order_from_sb[1]: "BB"}
    n_early = max(0, n - 3)
    early = ["UTG", "HJ", "CO"][3 - n_early:] if n_early <= 3 else \
        ["UTG"] * (n_early - 3) + ["UTG", "HJ", "CO"]
    names = ["SB", "BB"] + early + ["BTN"]
    return dict(zip(order_from_sb, names))


def parse_hand(text, source=""):
    """One hand, as the same dict shape ignition.parse_hand returns."""
    m = HAND_RE.search(text)
    if not m:
        return None
    hand_id, game_desc, sb, bb, played_at = m.groups()
    game = games.of(game_desc)
    if game is None:
        return None                      # Stud and the rest live in the same folder
    played_at = played_at.replace("/", "-")          # match Ignition's spelling

    tm = TABLE_RE.search(text)
    if not tm:
        return None
    table_name, max_seats, button = tm.group(1).strip(), int(tm.group(2)), int(tm.group(3))

    seats, by_name = [], {}
    for seat_no, name, stack, trailing in SEAT_RE.findall(text):
        if name.startswith("Seat ") or " folded on the " in name:
            continue                     # a SUMMARY line, not a seat line
        s = {"seat": int(seat_no), "label": name, "stack": _money(stack),
             "is_hero": False, "cards": None, "won": 0.0, "posted": 0.0,
             "invested": 0.0, "returned": 0.0,
             "sitting_out": "sitting out" in trailing}
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
    # by splitting on whitespace.
    names_longest = sorted(by_name, key=len, reverse=True)

    # The action, street by street.
    board, actions, street, order = [], [], "preflop", 0
    in_summary = False

    for raw in text.splitlines():
        sm = STREET_RE.match(raw)
        if sm:
            marker, rest = sm.groups()
            if marker == "SUMMARY":
                in_summary = True
                continue
            if marker == "SHOW DOWN":
                continue
            street = STREETS[marker]
            if street != "preflop":
                got = CARDS_RE.findall(rest)
                if got:
                    board += got[-1].split()
            continue
        if in_summary:
            wm = WON_RE.match(raw)
            if wm:
                seat_no = int(wm.group(1))
                for s in seats:
                    if s["seat"] == seat_no:
                        s["won"] = _money(wm.group(2)) or 0.0
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

        who = next((n for n in names_longest if raw.startswith(n + " ")), None)
        if who is None:
            continue
        s = by_name[who]
        rest = raw[len(who) + 1:].strip()

        pm = POST_RE.match(rest)
        if pm:
            s["posted"] += _money(pm.group(1)) or 0.0
            continue
        if rest.startswith(("shows", "mucks", "does not show")):
            cm = CARDS_RE.search(rest)
            # Two, four or five -- Hold'em, Omaha, 5-card Omaha. A count
            # this game does not deal is a misread, not a hand.
            if cm and len(cm.group(1).split()) == games.holes(game):
                s["cards"] = cm.group(1)
            continue
        if rest.startswith(("collected", "sits out", "joins", "leaves", "waits for",
                            "is disconnected", "is connected", "has timed out",
                            "will be allowed", "was removed")):
            continue

        # "and is all-in" is a suffix on an ordinary verb here rather than a
        # verb of its own, so the action keeps its real name and only the
        # all-in flag is extra. That is a better shape than Ignition's, where
        # every all-in reads "All-in" and the amounts have to settle what it
        # actually was.
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
            # "raises $0.03 to $0.04" -- added first, street total second.
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

    # Who was actually in the hand. A seat can be listed at the table and
    # not dealt in -- "waits for big blind", "is sitting out" -- and if that
    # seat is left in it is a player who never folded, so it reaches every
    # showdown and drives the pool's showdown rate up by twenty points. It
    # also shifts every position by one, because the ring it is counted in
    # is one seat too big. So the seat list is the room; the hand is whoever
    # put money in, acted, or was dealt cards.
    acted = {a["seat"] for a in actions}
    seats = [s for s in seats
             if s["seat"] in acted or s["posted"] or s["cards"] or s["is_hero"]]
    if len(seats) < 2:
        return None
    by_name = {s["label"]: s for s in seats}

    # Positions, now that the ring is the right size. The button is stated
    # outright, so the order is read off the seat numbers going round from
    # it; which seat posted the small blind then confirms it, because a
    # player joining late can be dealt in out of turn.
    ring = sorted(s["seat"] for s in seats)
    start = ring.index(button) if button in ring else len(ring) - 1
    from_btn = ring[start:] + ring[:start]
    order_from_sb = from_btn[1:] + from_btn[:1] if len(ring) > 2 else from_btn
    sb_seat = None
    for line in text.splitlines():
        if " posts the small blind" in line:
            who = next((n for n in names_longest if line.startswith(n + " ")), None)
            if who and who in by_name:
                sb_seat = by_name[who]["seat"]
            break
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
        s["invested"] = round(s["invested"] - s["returned"], 4)

    # A hand is standard when both blinds were posted by the seats the
    # button says should have posted them. Anything else -- a dead blind, a
    # player dealt in out of turn -- gets flagged rather than silently given
    # position names that are wrong.
    posted_bb = any(s["posted"] and s["position"] == "BB" for s in seats)
    standard = int(sb_seat is not None and posted_bb)

    pot_m = POT_RE.search(text)
    rake = _money(pot_m.group(2)) if pot_m and pot_m.group(2) else None
    jp = _money(pot_m.group(3)) if pot_m and pot_m.group(3) else None
    fmt = "BLITZ" if "blitz" in table_name.lower() else "RING"

    return {
        "hand": {"hand_id": "cp-" + hand_id, "played_at": played_at,
                 "table_id": table_name, "game": game, "fmt": fmt,
                 "sb": _money(sb), "bb": _money(bb), "n_players": len(seats),
                 "board": " ".join(board),
                 "pot": _money(pot_m.group(1)) if pot_m else None,
                 "rake": rake, "jp_fee": jp,
                 "hero_seat": hero["seat"] if hero else None,
                 "standard": standard, "source": source,
                 "max_seats": max_seats},
        "seats": seats,
        "actions": actions,
    }


def text_lines_for(text, name):
    """Every line this player is the subject of -- used only to find the SB."""
    return "\n".join(l for l in text.splitlines() if l.startswith(name + " "))


def split_hands(text):
    starts = [m.start() for m in HAND_RE.finditer(text)]
    for i, a in enumerate(starts):
        yield text[a:starts[i + 1] if i + 1 < len(starts) else len(text)]


# Hands whose answer is written down, not invented. The PLO4 block is a
# real history; the PLO5 block is the header and the dealt cards from one,
# finished with a fold-to-the-blind so the money identity can be checked
# without inventing an equity. Hold'em is here so the HEADER change cannot
# quietly stop loading the game this parser was built for.
PLO4 = """\
Hand #2459808909 - Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:18:05 UTC
Lincolnwood 6-max Seat #5 is the button
Seat 1: LLjr ($9.84)
Seat 2: M3dus4 ($10.00)
Seat 3: poker-1970 ($3.90)
Seat 4: Maestroea ($3.45)
Seat 5: LetsGeIt2821 ($44.76)
Seat 6: PierreRenard will be allowed to play after the button
LLjr posts the small blind $0.05
M3dus4 posts the big blind $0.10
*** HOLE CARDS ***
Dealt to M3dus4 [3c 2h Kd 2d]
poker-1970 folds
Maestroea folds
LetsGeIt2821 raises $0.35 to $0.35
LLjr calls $0.30
M3dus4 folds
*** FLOP *** [8h Ks 8c]
Main pot $0.76 | Rake $0.04
LLjr checks
LetsGeIt2821 checks
*** TURN *** [8h Ks 8c] [Qc]
Main pot $0.76 | Rake $0.04
LLjr checks
LetsGeIt2821 bets $0.60
LLjr folds
Uncalled bet ($0.60) returned to LetsGeIt2821
LetsGeIt2821 does not show
*** SUMMARY ***
Total pot $0.76 | Rake $0.04
Board [8h Ks 8c Qc]
Seat 1: LLjr (small blind) folded on the Turn
Seat 2: M3dus4 (big blind) folded on the Pre-Flop
Seat 3: poker-1970 folded on the Pre-Flop and did not bet
Seat 4: Maestroea folded on the Pre-Flop and did not bet
Seat 5: LetsGeIt2821 did not show and won $0.76
"""

PLO5 = """\
Hand #2459651456 - 5Card Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:00:00 UTC
Lincolnwood 6-max Seat #1 is the button
Seat 1: Alice ($10.00)
Seat 2: Bob ($10.00)
Seat 3: Carol ($10.00)
Bob posts the small blind $0.05
Carol posts the big blind $0.10
*** HOLE CARDS ***
Dealt to Bob [Qh Jh Qd Kc 2c]
Alice folds
Bob folds
Uncalled bet ($0.05) returned to Carol
Carol does not show
*** SUMMARY ***
Total pot $0.10 | Rake $0
Seat 1: Alice folded on the Pre-Flop and did not bet
Seat 2: Bob (small blind) folded on the Pre-Flop
Seat 3: Carol (big blind) did not show and won $0.10
"""

# Showdown so strength can name a 2+3 hand after a real import, not
# only against a typed tuple. Hero holds the overpair that Hold'em
# of all four would call two pair (aces and kings).
PLO4_SHOW = """\
Hand #2459808999 - Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:30:00 UTC
Lincolnwood 6-max Seat #1 is the button
Seat 1: Alice ($10.00)
Seat 2: Bob ($10.00)
Alice posts the small blind $0.05
Bob posts the big blind $0.10
*** HOLE CARDS ***
Dealt to Bob [As Ad Kh 7d]
Alice calls $0.05
*** FLOP *** [Kc 2h 3s]
Alice checks
Bob checks
*** TURN *** [Kc 2h 3s] [9c]
Alice checks
Bob checks
*** RIVER *** [Kc 2h 3s 9c] [4d]
Alice checks
Bob checks
*** SHOW DOWN ***
Alice shows [Qs Js Tc 8d]
Bob shows [As Ad Kh 7d]
*** SUMMARY ***
Total pot $0.20 | Rake $0
Board [Kc 2h 3s 9c 4d]
Seat 1: Alice (small blind) showed [Qs Js Tc 8d] and lost
Seat 2: Bob (big blind) showed [As Ad Kh 7d] and won $0.20
"""

# The 1-heart trap: Hold'em nut flush draw, Omaha ace-high. Golden
# for verify-parity -- if `made` comes back flush, the 2+3 walk died.
PLO4_TRAP = """\
Hand #2459809001 - Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:40:00 UTC
Lincolnwood 6-max Seat #1 is the button
Seat 1: Alice ($10.00)
Seat 2: Bob ($10.00)
Alice posts the small blind $0.05
Bob posts the big blind $0.10
*** HOLE CARDS ***
Dealt to Bob [Ah Kd 7c 2s]
Alice calls $0.05
*** FLOP *** [5h 9h Qh]
Alice checks
Bob checks
*** TURN *** [5h 9h Qh] [3c]
Alice checks
Bob checks
*** RIVER *** [5h 9h Qh 3c] [Kd]
Alice checks
Bob checks
*** SHOW DOWN ***
Alice shows [Qs Js Tc 8d]
Bob shows [Ah Kd 7c 2s]
*** SUMMARY ***
Total pot $0.20 | Rake $0
Board [5h 9h Qh 3c Kd]
Seat 1: Alice (small blind) showed [Qs Js Tc 8d] and lost
Seat 2: Bob (big blind) showed [Ah Kd 7c 2s] and won $0.20
"""

# Ace-high wrap + nut FD. Hold'em of all four is a royal. Histogram
# must land on Combo, never a straight-flush bar.
PLO4_COMBO = """\
Hand #2459809002 - Omaha (Pot Limit) - $0.05/$0.10 - 2025/05/19 17:45:00 UTC
Lincolnwood 6-max Seat #1 is the button
Seat 1: Alice ($10.00)
Seat 2: Bob ($10.00)
Alice posts the small blind $0.05
Bob posts the big blind $0.10
*** HOLE CARDS ***
Dealt to Bob [As Ks Qs 2d]
Alice calls $0.05
*** FLOP *** [Js Ts 3c]
Alice checks
Bob checks
*** TURN *** [Js Ts 3c] [2h]
Alice checks
Bob checks
*** RIVER *** [Js Ts 3c 2h] [7d]
Alice checks
Bob checks
*** SHOW DOWN ***
Alice shows [9c 8d 4h 3d]
Bob shows [As Ks Qs 2d]
*** SUMMARY ***
Total pot $0.20 | Rake $0
Board [Js Ts 3c 2h 7d]
Seat 1: Alice (small blind) showed [9c 8d 4h 3d] and lost
Seat 2: Bob (big blind) showed [As Ks Qs 2d] and won $0.20
"""

HOLDEM = """\
Hand #2459218653 - Holdem (No Limit) - $0.05/$0.10 - 2025/05/18 22:43:28 UTC
Mount Shasta 6-max Seat #1 is the button
Seat 1: Alice ($10.00)
Seat 2: Bob ($10.00)
Seat 3: Carol ($10.00)
Bob posts the small blind $0.05
Carol posts the big blind $0.10
*** HOLE CARDS ***
Dealt to Bob [As Kd]
Alice folds
Bob folds
Uncalled bet ($0.05) returned to Carol
Carol does not show
*** SUMMARY ***
Total pot $0.10 | Rake $0
Seat 3: Carol (big blind) did not show and won $0.10
"""


def _money_identity(parsed):
    """in - house = won, the same test sites.py runs on a loaded database."""
    inp = sum((s["posted"] or 0) + (s["invested"] or 0) for s in parsed["seats"])
    won = sum(s["won"] or 0 for s in parsed["seats"])
    house = (parsed["hand"]["rake"] or 0) + (parsed["hand"]["jp_fee"] or 0)
    return abs(inp - house - won) <= 0.011


def check():
    """
    Against hands whose text is written down here.

    A parser fails silently -- it drops a line and every figure downstream
    comes out slightly wrong. These blocks are the ones that would have
    been skipped; if the money, the game tag, the hole cards or the
    waiting seat come out wrong, the skip was safer than the load.
    """
    fails = []

    for line, site in (
            (PLO4.splitlines()[0], True),
            (PLO5.splitlines()[0], True),
            (HOLDEM.splitlines()[0], True),
            ("Hand #1 - Stud (Limit) - $0.05/$0.10 - 2025/05/19 17:00:00 UTC", True),
            ("PokerStars Hand #1:  Hold'em No Limit ($0.50/$1.00 USD)", False),
            ("Ignition Hand #1: TBL#1 HOLDEM No Limit - 2025-05-19 17:18:05", False)):
        got = bool(HEADER(line))
        if got != site:
            fails.append(f"HEADER {line[:40]!r} -> {got}")
    print(f"HEADER takes WPN and refuses the others  "
          f"{'yes' if not any(f.startswith('HEADER') for f in fails) else 'NO'}")

    p4 = parse_hand(PLO4, source="plo4.txt")
    if p4 is None:
        fails.append("PLO4 did not parse")
        print("PLO4 parsed                      NO")
    else:
        h, seats = p4["hand"], p4["seats"]
        by = {s["label"]: s for s in seats}
        want = [
            (h["game"] == "OMAHA", "game is OMAHA"),
            (h["hand_id"] == "cp-2459808909", "hand id"),
            (h["sb"] == 0.05 and h["bb"] == 0.10, "blinds"),
            (h["n_players"] == 5, "waiting seat is out of the hand"),
            ("PierreRenard" not in by, "PierreRenard was not dealt in"),
            (by["M3dus4"]["is_hero"] and by["M3dus4"]["cards"] == "3c 2h Kd 2d",
             "hero's four hole cards"),
            (h["board"] == "8h Ks 8c Qc", "board to the turn"),
            (by["LLjr"]["position"] == "SB"
             and by["M3dus4"]["position"] == "BB"
             and by["LetsGeIt2821"]["position"] == "BTN",
             "positions from the button"),
            (abs((by["LetsGeIt2821"]["won"] or 0) - 0.76) < 0.001, "winner"),
            (_money_identity(p4), "in - house = won"),
            (h["standard"] == 1, "standard blinds"),
        ]
        bad = [why for ok, why in want if not ok]
        print(f"PLO4 fixture                    {len(want) - len(bad)}/{len(want)}")
        for why in bad:
            print(f"    {why}")
            fails.append(f"PLO4: {why}")

    p5 = parse_hand(PLO5, source="plo5.txt")
    if p5 is None:
        fails.append("PLO5 did not parse")
        print("PLO5 parsed                      NO")
    else:
        h, seats = p5["hand"], p5["seats"]
        hero = next(s for s in seats if s["is_hero"])
        want = [
            (h["game"] == "OMAHA5", "game is OMAHA5"),
            (hero["cards"] == "Qh Jh Qd Kc 2c", "five hole cards"),
            (h["n_players"] == 3, "three seats in"),
            (_money_identity(p5), "in - house = won"),
        ]
        bad = [why for ok, why in want if not ok]
        print(f"PLO5 fixture                    {len(want) - len(bad)}/{len(want)}")
        for why in bad:
            print(f"    {why}")
            fails.append(f"PLO5: {why}")

    nl = parse_hand(HOLDEM, source="nlhe.txt")
    if nl is None or nl["hand"]["game"] != "HOLDEM":
        fails.append("Hold'em fixture did not parse as HOLDEM")
        print("Hold'em still parses             NO")
    else:
        hero = next(s for s in nl["seats"] if s["is_hero"])
        ok = hero["cards"] == "As Kd" and _money_identity(nl)
        print(f"Hold'em still parses            {'yes' if ok else 'NO'}")
        if not ok:
            fails.append("Hold'em fixture money or cards")

    stud = "Hand #1 - Stud (Limit) - $0.05/$0.10 - 2025/05/19 17:00:00 UTC\n"
    print(f"Stud is still refused            "
          f"{'yes' if parse_hand(stud) is None else 'NO'}")
    if parse_hand(stud) is not None:
        fails.append("Stud was parsed")

    # The path that used to skip the whole file: HEADER required
    # " - Holdem", so sniff returned None and the loader never asked.
    import sqlite3
    import tempfile
    from pathlib import Path

    import importer
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "plo4.txt"
        f.write_text(PLO4)
        sniffed = importer.sniff(f)
        print(f"PLO4 file sniffed as acr        "
              f"{'yes' if sniffed == 'acr' else 'NO -> ' + str(sniffed)}")
        if sniffed != "acr":
            fails.append(f"PLO4 sniffed as {sniffed}")
        db = Path(tmp) / "t.db"
        loaded = importer.load([f], db)
        con = sqlite3.connect(db)
        row = con.execute("SELECT game, n_players FROM hands").fetchone()
        cards = con.execute(
            "SELECT cards FROM seats WHERE is_hero=1").fetchone()
        con.close()
        ok = (loaded.get("added") == 1 and row and row[0] == "OMAHA"
              and cards and cards[0] == "3c 2h Kd 2d")
        print(f"PLO4 loads through importer     {'yes' if ok else 'NO'}")
        if not ok:
            fails.append("importer.load did not store the PLO4 hand")

        # A shown PLO hand has to come out an Omaha label after the
        # ordinary derive, or `--game plo --hist-postflop` is still
        # reading NULL and every bar is empty.
        show = Path(tmp) / "plo4show.txt"
        show.write_text(PLO4_SHOW)
        (Path(tmp) / "plo4trap.txt").write_text(PLO4_TRAP)
        (Path(tmp) / "plo4combo.txt").write_text(PLO4_COMBO)
        db2 = Path(tmp) / "show.db"
        importer.load([show, Path(tmp) / "plo4trap.txt",
                       Path(tmp) / "plo4combo.txt"], db2)
        import decisions
        import lines
        import spots
        import strength
        spots.build(db2)
        decisions.build(db2)
        lines.build(db2)
        strength.build(db2)
        named = sqlite3.connect(db2).execute(
            "SELECT made, fd, sd FROM decisions "
            "WHERE cards = 'As Ad Kh 7d' AND street = 'flop'"
        ).fetchone()
        want = strength.classify("As Ad Kh 7d", "Kc 2h 3s")
        ok = named and tuple(named) == (want[0], want[2], want[3])
        print(f"PLO4 showdown classifies        "
              f"{'yes' if ok else 'NO -> ' + str(named)}")
        if not ok:
            fails.append(f"imported PLO4 flop was {named}, want {want}")
        trap = sqlite3.connect(db2).execute(
            "SELECT made, fd, sd FROM decisions "
            "WHERE cards = 'Ah Kd 7c 2s' AND street = 'flop'"
        ).fetchone()
        trap_ok = trap and trap[0] == "high card" and trap[1] is None
        print(f"1-heart trap is not a flush     "
              f"{'yes' if trap_ok else 'NO -> ' + str(trap)}")
        if not trap_ok:
            fails.append(f"1-heart trap imported as {trap}")
        combo = sqlite3.connect(db2).execute(
            "SELECT made, fd, sd FROM decisions "
            "WHERE cards = 'As Ks Qs 2d' AND street = 'flop'"
        ).fetchone()
        combo_ok = (combo and combo[0] == "high card"
                    and combo[1] == "nut" and combo[2] == "wrap")
        print(f"royal-looking flop is combo     "
              f"{'yes' if combo_ok else 'NO -> ' + str(combo)}")
        if not combo_ok:
            fails.append(f"combo golden imported as {combo}")

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

