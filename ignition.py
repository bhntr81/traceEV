"""
Load Ignition hand histories into a database that can be counted.

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

    python ignition.py <folder>     load, or top up, the database
    python ignition.py --stats      what is in there
"""

import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "hands.db"

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
# that went in.
POSTS = ("Small Blind", "Small blind", "Big Blind", "Big blind",
         "Posts chip", "Ante chip")

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
                if len(got) in (2, 4) and (seat["cards"] is None
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
    return {
        "hand": {"hand_id": hand_id, "played_at": played_at,
                 "table_id": table_id, "game": game.strip(),
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS hands (
  hand_id TEXT PRIMARY KEY, played_at TEXT, table_id TEXT, game TEXT,
  fmt TEXT, sb REAL, bb REAL, n_players INT, board TEXT, pot REAL,
  hero_seat INT, standard INT, source TEXT);
CREATE TABLE IF NOT EXISTS seats (
  hand_id TEXT, seat INT, label TEXT, position TEXT, stack REAL,
  cards TEXT, is_hero INT, won REAL, posted REAL, invested REAL,
  PRIMARY KEY (hand_id, seat));
CREATE TABLE IF NOT EXISTS actions (
  hand_id TEXT, street TEXT, n INT, position TEXT, seat INT,
  action TEXT, amount REAL, total REAL);
CREATE INDEX IF NOT EXISTS actions_hand ON actions(hand_id);
CREATE INDEX IF NOT EXISTS actions_spot ON actions(street, position, action);
CREATE INDEX IF NOT EXISTS hands_fmt ON hands(fmt, bb);
"""

# "HH20260825-052504 - 800 - RING - $0.10-$0.25 - HOLDEM - NL - TBL No..."
FILENAME_RE = re.compile(r" - (RING|ZONE|MTT) - (?:\$([\d.]+)-\$([\d.]+))?", re.I)


def build(folder, db_path=DB, files=None):
    """
    Load every hand not already stored.

    Safe to re-run as the database grows: hands are keyed by Ignition's own
    id, so re-exported files, or exports that overlap ones already loaded,
    contribute only what is new. That matters because the intended use is to
    keep pointing this at the same folder as more sessions are played.
    """
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    # Both loaders write the same tables, so both must bring them up to date.
    from acr import migrate
    migrate(con)
    known = {r[0] for r in con.execute("SELECT hand_id FROM hands")}

    added = skipped = n_files = 0
    # `files=` lets a caller hand over an explicit list, which is what the
    # importer does when one folder holds two sites and each file has to go
    # to the parser that wrote it. The counter beside it is deliberately not
    # called `files`: it used to be, and being assigned first it overwrote
    # the parameter with 0 before the loop could read it -- so the list was
    # thrown away and the loop tried to iterate the number zero. Every
    # import through `importer.load` went that way and raised.
    for f in (files if files is not None else sorted(Path(folder).rglob("*.txt"))):
        n_files += 1
        fm = FILENAME_RE.search(f.name)
        fmt = fm.group(1).upper() if fm else "?"
        sb = float(fm.group(2)) if fm and fm.group(2) else None
        bb = float(fm.group(3)) if fm and fm.group(3) else None
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in split_hands(text):
            got = parse_hand(block, source=f.name)
            if not got:
                continue
            h = got["hand"]
            if h["hand_id"] in known:
                skipped += 1
                continue
            known.add(h["hand_id"])
            # Columns are named, not positional. They were positional until
            # a second site arrived and added `site`, `rake`, `max_seats` and
            # `jp_fee` to `hands` and `allin` to `actions` -- at which point
            # this loader stopped being able to insert anything at all, and
            # stayed that way silently because nobody loads a new session
            # every day. Naming them means the next column added here costs
            # nothing rather than breaking the other site's importer.
            con.execute(
                "INSERT INTO hands (hand_id, played_at, table_id, game, fmt,"
                " sb, bb, n_players, board, pot, hero_seat, standard, source,"
                " site) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h["hand_id"], h["played_at"], h["table_id"], h["game"],
                 fmt, sb, bb, h["n_players"], h["board"], h["pot"],
                 h["hero_seat"], h["standard"], h["source"], "ignition"))
            con.executemany(
                "INSERT OR REPLACE INTO seats (hand_id, seat, label, position,"
                " stack, cards, is_hero, won, posted, invested)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(h["hand_id"], s["seat"], s["label"], s["position"],
                  s["stack"], s["cards"], int(s["is_hero"]), s["won"],
                  s["posted"], s["invested"])
                 for s in got["seats"]])
            con.executemany(
                "INSERT INTO actions (hand_id, street, n, position, seat,"
                " action, amount, total) VALUES (?,?,?,?,?,?,?,?)",
                [(h["hand_id"], a["street"], a["n"], a["position"], a["seat"],
                  a["action"], a["amount"], a["total"])
                 for a in got["actions"]])
            added += 1
        if n_files % 25 == 0:
            con.commit()
            print(f"  ...{n_files} files, {added} hands", flush=True)
    con.commit()
    con.close()
    return n_files, added, skipped


def stats(db_path=DB):
    if not Path(db_path).exists():
        print("no database yet -- load a folder first")
        return
    con = sqlite3.connect(db_path)
    one = lambda s: con.execute(s).fetchone()[0]
    print(f"hands             {one('SELECT COUNT(*) FROM hands'):>8}")
    print(f"seats observed    {one('SELECT COUNT(*) FROM seats'):>8}")
    print(f"with hole cards   {one('SELECT COUNT(*) FROM seats WHERE cards IS NOT NULL'):>8}")
    print(f"actions           {one('SELECT COUNT(*) FROM actions'):>8}")

    # Profit is what came back less everything that went in, including the
    # blinds. "Hand result" on its own is the pot collected, which counts a
    # player's own money as winnings.
    net = con.execute(
        "SELECT h.fmt, h.bb, COUNT(*), SUM(s.won - s.posted - s.invested) "
        "FROM seats s JOIN hands h USING(hand_id) WHERE s.is_hero=1 "
        "AND h.bb IS NOT NULL GROUP BY h.fmt, h.bb "
        "ORDER BY h.fmt, h.bb").fetchall()
    if net:
        print("\nyour results:")
        for fmt, bb, n, profit in net:
            profit = profit or 0.0
            print(f"  {fmt:5} ${bb:.2f}  {n:6d} hands  ${profit:+8.2f}"
                  f"  {100 * profit / bb / max(1, n):+7.1f} bb/100")

    print("\nby format and stake:")
    for fmt, bb, n in con.execute(
            "SELECT fmt, bb, COUNT(*) FROM hands GROUP BY fmt, bb "
            "ORDER BY COUNT(*) DESC"):
        print(f"  {fmt:5} {('$%.2f' % bb) if bb else '-':>7}  {n:6d}")

    print("\npreflop, by position -- how often each seat does what:")
    rows = con.execute(
        "SELECT position, action, COUNT(*) FROM actions "
        "WHERE street='preflop' GROUP BY position, action").fetchall()
    by_pos = {}
    for pos, act, n in rows:
        by_pos.setdefault(pos, {})[act] = n
    for pos in ("UTG", "HJ", "CO", "BTN", "SB", "BB"):
        acts = by_pos.get(pos)
        if not acts:
            continue
        total = sum(acts.values())
        parts = "  ".join(f"{a}{100 * n // total:3d}%"
                          for a, n in sorted(acts.items()))
        print(f"  {pos:4} n={total:6d}   {parts}")
    con.close()


if __name__ == "__main__":
    if "--stats" in sys.argv:
        stats()
        sys.exit(0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    files, added, skipped = build(sys.argv[1])
    print(f"\n{files} files, {added} hands added, {skipped} already known\n")
    stats()
