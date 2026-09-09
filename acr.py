"""
Load ACR hand histories into the same database Ignition writes to.

The two sites are worth having together because each has exactly what the
other lacks.

  Ignition  shows every player's hole cards, folds included -- so a range
            can be counted rather than inferred -- but names nobody, so a
            player is only "table:seat" and only within one session.
  ACR       names everyone. A player followed across months is a profile,
            which is the thing an opponent report is for. What it will not
            show is a folded hand, so ranges here are inferred, not seen.

This loader reads the Winning Poker Network format, which ACR is on. It was
written believing the hands were ACR's, on the evidence that a
ACR client was installed on the machine -- the site was inferred from
the computer rather than from the data, which is exactly the assumption
`EmpiricalRigor` exists to forbid. The data says otherwise and says it
plainly: the fast-fold game is called "Blitz Poker", the tables are named
after American towns, the pot loses a "JP Fee", and the stakes are dollars
rather than chips. All four are ACR and none of them is ACR.

So Ignition measures the pool and ACR measures the person, and the
interesting work is using the first as the prior for the second. That only
happens if both land in one schema, which is what this does: the same hands
/ seats / actions tables, the same position names, the same money
convention (`won` is what came back from the pot, never profit).

Two things ACR gives that Ignition does not: the button is stated
outright, so positions are read rather than reconstructed from labels, and
rake is written on every pot.

    python acr.py <folder>     load, or top up, the database
    python acr.py --stats      what is in there, per site
"""

import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "hands.db"

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
                 "standard": standard, "source": source, "site": "acr",
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


def migrate(con):
    """
    Room for a second site in tables that were written for one.

    Existing Ignition hand ids are left exactly as they are. Rewriting 3,942
    hands across four tables to gain a prefix they do not need would be
    churn; ACR ids carry a "cp-" instead, which makes a collision
    between the two sites impossible either way.
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info(hands)")}
    if "site" not in cols:
        con.execute("ALTER TABLE hands ADD COLUMN site TEXT")
        con.execute("UPDATE hands SET site='ignition' WHERE site IS NULL")
    if "rake" not in cols:
        con.execute("ALTER TABLE hands ADD COLUMN rake REAL")
    if "max_seats" not in cols:
        con.execute("ALTER TABLE hands ADD COLUMN max_seats INT")
    if "jp_fee" not in cols:
        con.execute("ALTER TABLE hands ADD COLUMN jp_fee REAL")
    acols = {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    if "allin" not in acols:
        con.execute("ALTER TABLE actions ADD COLUMN allin INT")
    con.commit()


def build(folder, db_path=DB, files=None):
    con = sqlite3.connect(db_path)
    # The tables before the columns. `migrate` only ALTERs, so against a
    # database that does not have them yet it asked SQLite to add a column
    # to a table that was not there -- which is what an ACR-first import
    # into a fresh database is, and this machine holds three hundred ACR
    # files and one Ignition folder. Ignition's loader has always created
    # them; both write the same tables, so both must be able to.
    from ignition import SCHEMA
    con.executescript(SCHEMA)
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
            con.execute(
                "INSERT INTO hands (hand_id, played_at, table_id, game, fmt,"
                " sb, bb, n_players, board, pot, hero_seat, standard, source,"
                " site, rake, max_seats, jp_fee)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h["hand_id"], h["played_at"], h["table_id"], h["game"],
                 h["fmt"], h["sb"], h["bb"], h["n_players"], h["board"],
                 h["pot"], h["hero_seat"], h["standard"], h["source"],
                 h["site"], h["rake"], h["max_seats"], h["jp_fee"]))
            con.executemany(
                "INSERT OR REPLACE INTO seats VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(h["hand_id"], s["seat"], s["label"], s["position"],
                  s["stack"], s["cards"], int(s["is_hero"]), s["won"],
                  s["posted"], s["invested"]) for s in got["seats"]])
            con.executemany(
                "INSERT INTO actions (hand_id, street, n, position, seat,"
                " action, amount, total, allin) VALUES (?,?,?,?,?,?,?,?,?)",
                [(h["hand_id"], a["street"], a["n"], a["position"], a["seat"],
                  a["action"], a["amount"], a["total"], a["allin"])
                 for a in got["actions"]])
            added += 1
        if n_files % 25 == 0:
            con.commit()
            print(f"  ...{n_files} files, {added} hands", flush=True)
    con.commit()
    con.close()
    return n_files, added, skipped


def stats(db_path=DB):
    con = sqlite3.connect(db_path)
    migrate(con)
    print("hands by site and format:")
    for site, fmt, bb, n in con.execute(
            "SELECT COALESCE(site,'ignition'), fmt, bb, COUNT(*) FROM hands "
            "GROUP BY 1, 2, 3 ORDER BY 1, COUNT(*) DESC"):
        print(f"  {site:10} {fmt:6} {('$%.2f' % bb) if bb else '-':>7}  {n:6d}")

    print("\ncoverage of what each site actually shows:")
    for site in ("ignition", "acr"):
        row = con.execute(
            "SELECT COUNT(*), SUM(s.cards IS NOT NULL), COUNT(DISTINCT s.label) "
            "FROM seats s JOIN hands h USING(hand_id) "
            "WHERE COALESCE(h.site,'ignition')=?", (site,)).fetchone()
        if not row[0]:
            continue
        print(f"  {site:10} {row[0]:7d} seats, {row[1] or 0:7d} with cards "
              f"({100 * (row[1] or 0) / row[0]:5.1f}%), "
              f"{row[2]:6d} distinct names")

    print("\nyour results, by site and stake:")
    for site, fmt, bb, n, profit in con.execute(
            "SELECT COALESCE(h.site,'ignition'), h.fmt, h.bb, COUNT(*), "
            "SUM(s.won - s.posted - s.invested) FROM seats s "
            "JOIN hands h USING(hand_id) WHERE s.is_hero=1 AND h.bb IS NOT NULL "
            "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"):
        profit = profit or 0.0
        print(f"  {site:10} {fmt:6} ${bb:.2f}  {n:6d} hands  ${profit:+9.2f}"
              f"  {100 * profit / bb / max(1, n):+8.1f} bb/100")
    con.close()


CP = "h.site='acr'"


def check(db_path=DB):
    """
    Four ways this loader could be wrong, each one a number that would move.

    A hand history parser fails quietly. It does not crash on a line it
    misreads; it drops the line and every figure downstream comes out
    slightly wrong and entirely plausible. So the import is not believed
    because it ran -- it is believed because the money adds up, the button
    goes round, and the blinds are posted by the seats named as blinds.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    fails = []

    # 1. Money. Every chip that went in came back to somebody, minus what the
    #    house took. This is the check that catches a misread bet size, a
    #    missed call, an uncalled bet counted twice.
    rows = q(f"""SELECT h.hand_id, SUM(s.won) w,
                   SUM(s.posted + s.invested) inp,
                   COALESCE(h.rake,0) + COALESCE(h.jp_fee,0) house
                 FROM seats s JOIN hands h USING(hand_id)
                 WHERE {CP} GROUP BY h.hand_id""")
    off = [r for r in rows if abs((r["inp"] or 0) - r["house"] - (r["w"] or 0)) > 0.011]
    ok = 100 * (1 - len(off) / max(1, len(rows)))
    print(f"money adds up          {ok:6.2f}%  "
          f"({len(rows) - len(off)}/{len(rows)} hands within a cent)")
    if ok < 99.0:
        fails.append("money")
        for r in off[:5]:
            print(f"    {r['hand_id']}  in {r['inp']:.2f}  house {r['house']:.2f}"
                  f"  won {r['w']:.2f}")

    # 2. The button goes round. Over thousands of hands at a full table every
    #    seat is every position equally often. A skew means the rotation was
    #    read wrong, which would silently rewrite every positional stat.
    counts = q(f"""SELECT s.position, COUNT(*) n FROM seats s
                   JOIN hands h USING(hand_id)
                   WHERE {CP} AND h.n_players=6 AND h.standard=1
                   GROUP BY 1""")
    ns = [r["n"] for r in counts]
    spread = (max(ns) - min(ns)) / max(1, sum(ns) / len(ns)) if ns else 1
    print(f"positions balanced     {100 * (1 - spread):6.2f}%  "
          f"(6 positions, {min(ns) if ns else 0}-{max(ns) if ns else 0} each)")
    if len(ns) != 6 or spread > 0.02:
        fails.append("positions")

    # 3. The blinds. Whoever the button says is the small blind is whoever
    #    the history says posted it -- otherwise positions are one seat out.
    bad = q(f"""SELECT COUNT(*) n FROM seats s JOIN hands h USING(hand_id)
                WHERE {CP} AND h.standard=1 AND s.posted > 0
                  AND s.position NOT IN ('SB','BB')""")[0]["n"]
    total = q(f"""SELECT COUNT(*) n FROM seats s JOIN hands h USING(hand_id)
                  WHERE {CP} AND h.standard=1 AND s.posted > 0""")[0]["n"]
    # A bare "posts $0.05" from a returning player is a real post from a
    # non-blind seat, so this is never expected to be exactly 100%.
    print(f"blinds posted by blinds{100 * (1 - bad / max(1, total)):6.2f}%  "
          f"({bad} of {total} posts were dead posts from other seats)")
    if bad / max(1, total) > 0.02:
        fails.append("blinds")

    # 4. Identity. The point of this site is that players have names, so a
    #    profile is only worth building if names recur.
    seen = q(f"""SELECT s.label, COUNT(*) n FROM seats s JOIN hands h USING(hand_id)
                 WHERE {CP} AND s.is_hero=0 GROUP BY 1""")
    over = {k: sum(1 for r in seen if r["n"] >= k) for k in (30, 100, 500)}
    print(f"named opponents        {len(seen):6d}  "
          f"({over[30]} with 30+ hands, {over[100]} with 100+, {over[500]} with 500+)")
    if over[100] < 20:
        fails.append("identity")

    con.close()
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    if "--stats" in sys.argv:
        stats()
        sys.exit(0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    files, added, skipped = build(sys.argv[1])
    print(f"\n{files} files, {added} hands added, {skipped} already known\n")
    stats()
