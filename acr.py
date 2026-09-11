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

This is the parser and nothing else: the header a hand begins with, how a
file splits into hands, and one hand as the dict shape every site's parser
returns. Loading, the schema and the checks are `importer.py` and
`sites.py`. The site this parser belongs to, and what is true of it, is the
registry entry in `sites.py`.

Two things ACR gives that Ignition does not: the button is stated
outright, so positions are read rather than reconstructed from labels, and
rake is written on every pot -- plus a jackpot fee, which the money check
found when one hand in five came up short.

    python importer.py <folder>     load, whatever sites are in it
    python sites.py --check         prove the import
"""

import re

# The Winning Poker Network writes a bare hand number followed by the game.
# The header is the only thing a file is identified by -- never the folder
# it was found in, never which client is installed on the machine. Eight
# thousand hands were once loaded and reported on under the wrong site
# because the site was inferred from the computer instead of the text.
HEADER = lambda line: line.startswith("Hand #") and " - Holdem" in line[:80]

# "Hand #2459218653 - Holdem (No Limit) - $0.01/$0.02 - 2025/05/18 22:43:28 UTC"
HAND_RE = re.compile(
    r"^Hand #(\d+)\s*-\s*(.+?)\s*-\s*\$?([\d.,]+)/\$?([\d.,]+)\s*-\s*"
    r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", re.M)
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
    if "holdem" not in game_desc.lower():
        return None                      # Omaha files live in the same folder
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
            if cm and len(cm.group(1).split()) == 2:
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
                 "table_id": table_name, "game": "HOLDEM", "fmt": fmt,
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
