"""
The HUD's numbers: who is sitting at a table, and what each of them does.

No window is drawn here, and nothing here knows what platform it is on.
The overlay asks two questions -- who sat at this table in its latest hand,
and what is on each of their panels -- and both are answered from the same
derived tables and the same stat engine the reports read. The HUD is a view
over `stats.py`, not a second engine (ROADMAP, 3 Oct 2026), and a number on
it is held to every rule a number in a report is.

    python hud.py SITE TABLE          the panels for that table, as text
    python hud.py --check             every panel's pool against the engine's
"""

import sqlite3
import sys
from pathlib import Path

import players
import sites
import stats

DB = Path(__file__).parent / "hands.db"

# The panel, a line at a time. Hand2Note's and Hold'em Manager's default
# HUDs open on VPIP / PFR / 3-bet beside the hands count, and follow it with
# the post-flop pair, c-bet and fold to c-bet; went-to-showdown is the one
# showdown tendency every default carries. Convention, not invention: the
# point of a HUD is that it reads at a glance, and a glance is trained on
# the layout every tracker opens with.
PANEL = (("vpip", "pfr", "threebet"),
         ("fold_to_3bet", "cbet_flop", "fold_to_cbet", "wtsd"))
KEYS = tuple(k for line in PANEL for k in line)
ABBREV = {"vpip": "VPIP", "pfr": "PFR", "threebet": "3B",
          "fold_to_3bet": "F3B", "cbet_flop": "CB", "fold_to_cbet": "FCB",
          "wtsd": "WTSD"}


def seated(con, site, table_id):
    """
    Who sat at this table in its latest hand, by seat.

    The latest hand is the table as the HUD can know it: a player who sat
    down since has not been dealt in yet and has nothing to show. Identity
    is read from `spots`, where `spots.identify` already decided it --
    aliases merged, Ignition's seats turned into table:seat:segment -- and
    is never decided a second time here.
    """
    row = con.execute(
        "SELECT hand_id, max_seats FROM hands WHERE site=? AND table_id=? "
        "ORDER BY played_at DESC, hand_id DESC LIMIT 1",
        (site, table_id)).fetchone()
    if not row:
        return None
    hand_id, max_seats = row
    seats = [{"seat": s, "player": p, "is_hero": bool(h)}
             for s, p, h in con.execute(
                 "SELECT seat, player, is_hero FROM spots WHERE hand_id=? "
                 "ORDER BY seat", (hand_id,))]
    return {"site": site, "table": table_id, "hand_id": hand_id,
            "max_seats": max_seats, "seats": seats}


def counts(con, where, params):
    """Every panel stat's (n, k) under one filter: one pass, then WTSD."""
    want = [stats.BY_KEY[k] for k in KEYS]
    out = stats.rates(con, where, params, want)
    for s in want:
        if s.key not in out:
            # Spots-sourced: its situation lives in the per-hand table and
            # cannot join the pass above.
            n, k, *_ = stats.rate(con, s, where, params)
            out[s.key] = (n, k)
    return out


def panels(con, table):
    """
    Each seat's panel: the stats, shrunk towards the pool, and the hands.

    The shown figure is the rate with the pool behind it (`stats.shrunk`),
    not the raw one every other tracker shows, because this project's rule
    beats the convention here: a 3-bet of 100% on two chances is printed as
    100% and believed, and a HUD is read faster and believed harder than
    any report. The hands count beside it is raw, as everywhere.

    The pool is `players.pool_for`'s -- the player's own games, minus them,
    hero left out -- but computed once per set of games rather than once
    per seat: the totals for everybody at those stakes, less the player's
    own rows among them. Subtraction gives exactly the same counts, and a
    table of six reads the pool once instead of six times.
    """
    site = table["site"]
    named = sites.of(site).names
    bases = {}
    out = []
    for s in table["seats"]:
        panel = {"seat": s["seat"], "player": s["player"],
                 "is_hero": s["is_hero"], "hands": 0, "stats": {}}
        out.append(panel)
        if not named or s["player"] is None:
            # A seat with no person in it -- Ignition's, or one nobody could
            # name -- shows nothing rather than a stranger's numbers.
            continue
        mine = counts(con, "player=? AND site=?", (s["player"], site))
        games = tuple(sorted(con.execute(
            "SELECT DISTINCT fmt, bb FROM decisions WHERE player=? AND site=?",
            (s["player"], site)).fetchall()))
        if not games:
            # Dealt in and never acted, in every hand we hold: nothing to
            # count and, as in `players.pool_for`, no pool to borrow from.
            panel["stats"] = {key: {"n": 0, "k": 0, "pool": None, "shown": None}
                              for key in KEYS}
            continue
        if games not in bases:
            ors = " OR ".join("(fmt=? AND bb=?)" for _g in games)
            params = [site] + [v for g in games for v in g]
            bases[games] = counts(
                con, f"site=? AND is_hero=0 AND ({ors})", params)
        base = bases[games]
        # What comes out of the totals is the player's rows that went into
        # them, which is every row they have NOT played as hero. Usually that
        # is all or nothing, but a name can be hero in one file and a seat in
        # another -- two PartyPoker players on FPDB's corpus were -- and
        # taking all of it out of a pool that never held their hero hands
        # left 48 of 1,603 pools wrong.
        roles = {h for (h,) in con.execute(
            "SELECT DISTINCT is_hero FROM decisions WHERE player=? AND site=?",
            (s["player"], site))}
        if roles == {0}:
            theirs = mine
        elif roles == {1}:
            theirs = None
        else:
            theirs = counts(con, "player=? AND site=? AND is_hero=0",
                            (s["player"], site))
        for key in KEYS:
            n, k = mine[key]
            pn, pk = base[key] if theirs is None else (
                base[key][0] - theirs[key][0], base[key][1] - theirs[key][1])
            pool = pk / pn if pn else None
            # No chances shows "-", as on every tracker's HUD. Shrunk, it
            # would be the pool's own rate printed in this player's place --
            # a number about everybody else, read as a fact about them.
            panel["stats"][key] = {"n": n, "k": k, "pool": pool, "shown": (
                stats.shrunk(k, n, pool) if n else None)}
        panel["hands"] = mine["vpip"][0]
    return out


def text(panel):
    """A panel as the lines the overlay draws."""
    if not panel["stats"]:
        return ["-"]
    lines = []
    for i, line in enumerate(PANEL):
        cells = []
        for key in line:
            v = panel["stats"][key]["shown"]
            cells.append("-" if v is None else f"{100 * v:.0f}")
        lines.append("/".join(cells) + (f"  {panel['hands']}" if i == 0 else ""))
    return lines


def check(db_path=DB):
    """
    Every panel's pool against `players.pool_for`, on every named table.

    The subtraction in `panels` is the one place the HUD does not ask the
    engine its question directly, so it is held to the answer the engine
    gives: for every seat at the latest hand of every table on a site with
    names, every stat's pool rate must be `pool_for`'s to the last digit.
    """
    con = sqlite3.connect(db_path)
    try:
        named = [s.key for s in sites.SITES if s.names]
        tables = con.execute(
            f"SELECT DISTINCT site, table_id FROM hands WHERE site IN "
            f"({', '.join('?' * len(named))}) AND fmt <> 'MTT'", named
        ).fetchall()
        checked, wrong = 0, []
        for site, table_id in tables:
            for p in panels(con, seated(con, site, table_id)):
                where, params = players.pool_for(con, p["player"], site)
                if where is None:
                    continue
                want = counts(con, where, params)
                for key, got in p["stats"].items():
                    pn, pk = want[key]
                    exp = pk / pn if pn else None
                    checked += 1
                    if (exp is None) != (got["pool"] is None) or (
                            exp is not None and abs(exp - got["pool"]) > 1e-12):
                        wrong.append(f"{site} {table_id} {p['player']} {key}")
        print(f"a panel's pool is pool_for's   {checked - len(wrong)}/{checked} "
              f"seat-stats over {len(tables)} tables")
        for w in wrong[:10]:
            print(f"    {w}")
        print("PASS" if not wrong else "FAIL")
        return not wrong
    finally:
        con.close()


def show(site, table_id, db_path=DB):
    con = sqlite3.connect(db_path)
    try:
        table = seated(con, site, table_id)
        if table is None:
            print(f"no hands at {table_id!r} on {site}")
            return
        print(f"{site}  {table_id}  {table['max_seats']}-max  "
              f"latest hand {table['hand_id']}")
        print("  " + " / ".join(ABBREV[k] for k in PANEL[0]) + "  hands;  "
              + " / ".join(ABBREV[k] for k in PANEL[1]))
        for p in panels(con, table):
            who = (p["player"] or "?") + (" (you)" if p["is_hero"] else "")
            lines = text(p)
            print(f"  seat {p['seat']:>2}  {who[:22]:22} {lines[0]}")
            for line in lines[1:]:
                print(f"  {'':31} {line}")
    finally:
        con.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if check() else 1)
    if len(sys.argv) == 3:
        show(sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
