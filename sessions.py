"""
When you sat down, when you got up, and what happened in between.

A tracker's stats describe what people do; its sessions describe when *you*
did it, and that is a different question with different answers. Three of
the nine worked examples in Hand2Note's own manual are session questions --
whether you win more in the morning or the evening, whether the fourth
hour of a session is worth playing, whether it matters how many tables were
open -- and none of them could be asked here, because nothing here knew
where one sitting ended and the next began.

**A session is a run of your own hands with no gap longer than `GAP`
minutes**, on one site. Hand2Note's rule is the same one and its default is
the same ten minutes; the threshold barely matters on this data, where
21,171 of the gaps between consecutive hands are under two minutes and 53
are over an hour, with almost nothing in between.

**Per site, not merged.** Two sites open at once are one sitting in real
life, but `played_at` is whatever clock the site wrote, and nothing says
ACR's clock and Ignition's agree. Merging by timestamp across sites would
splice sessions that never overlapped or split ones that did, silently.
Within a site the clock is at least consistent with itself.

Which also means: **hour of day is the site's hour**, not yours. A filter
for "evening" is evening on the site's clock. That is stated on every view
that uses it rather than left to be discovered.

Writes onto `decisions`:

    session_id    which sitting this hand was part of
    session_min   minutes into that sitting when the hand was dealt
    session_len   how long the whole sitting lasted, in minutes
    tables_now    how many tables were dealing you hands at the time

The last is Hand2Note's "multi-tabling count" -- distinct tables with one
of your hands inside the previous `TABLING_WINDOW` minutes -- and is what
"do I play worse six-tabling" needs.

    python sessions.py            derive the sessions and stamp the columns
    python sessions.py --check    every hand is in exactly one sitting
    python sessions.py --list     the sittings, newest first
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).parent / "hands.db"

# Minutes of nothing that ends a sitting. Hand2Note's default, and on this
# data almost any number from three to fifty gives the same sessions.
GAP = 10

# A table counts as open if it dealt you a hand this recently. Five minutes
# is about three orbits at a full ring table, which is longer than any pause
# a live table has, and shorter than the gap that ends a session.
TABLING_WINDOW = 5

COLUMNS = (("session_id", "INT"), ("session_min", "REAL"),
           ("session_len", "REAL"), ("tables_now", "INT"))
NAMES = tuple(c for c, _t in COLUMNS)

SCHEMA = """
DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions (
  session_id INT PRIMARY KEY,
  site TEXT, started TEXT, ended TEXT, minutes REAL,
  hands INT, tables INT, net_bb REAL, ev_bb REAL, bb100 REAL, ev100 REAL);
CREATE INDEX sessions_site ON sessions(site, started);
"""


def migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    for name, kind in COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {name} {kind}")
    con.commit()


def hero_hands(con):
    """
    Your hands, in the order you played them, with the money each made.

    `net_bb` is hero's result from `spots`, which is `won - posted -
    invested` -- profit, and not what came back from the pot. `ev_bb` is
    the all-in adjusted figure where one has been computed and the actual
    result where it has not, so a session's EV line differs from its result
    line only in the hands where the difference is real.
    """
    return con.execute("""
        SELECT h.hand_id, h.site, h.played_at, h.table_id, h.fmt,
               s.net_bb,
               COALESCE(e.ev_bb, s.net_bb) AS ev_bb
        FROM hands h
        JOIN spots s ON s.hand_id = h.hand_id AND s.seat = h.hero_seat
        LEFT JOIN hand_ev e ON e.hand_id = h.hand_id AND e.seat = h.hero_seat
        WHERE h.hero_seat IS NOT NULL AND h.game = 'HOLDEM'
        ORDER BY h.site, h.played_at, h.hand_id""").fetchall()


def split(rows, gap=GAP):
    """
    Cut the ordered hands into sittings wherever the clock stops for `gap`.

    Yields (site, [rows]) in play order. The rows are already ordered by
    site then time, so a change of site is a cut too -- a session never
    spans two clocks.
    """
    current, site, last = [], None, None
    for r in rows:
        when = datetime.fromisoformat(r[2])
        if current and (r[1] != site or
                        (when - last) > timedelta(minutes=gap)):
            yield site, current
            current = []
        current.append((r, when))
        site, last = r[1], when
    if current:
        yield site, current


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS hand_ev ("
                "hand_id TEXT, seat INT, ev_bb REAL, PRIMARY KEY (hand_id, seat))")
    con.executescript(SCHEMA)
    migrate(con)

    rows = hero_hands(con)
    sessions, stamps = [], []
    for sid, (site, hands) in enumerate(split(rows), start=1):
        started, ended = hands[0][1], hands[-1][1]
        minutes = (ended - started).total_seconds() / 60
        # A one-hand sitting has no duration, and a rate over zero minutes
        # is not a rate. It is kept -- it happened -- but its bb/100 is the
        # money over the hands, which is well defined however long it took.
        money = [r for r, _w in hands if r[4] != "MTT" and r[5] is not None]
        net = sum(r[5] for r in money)
        ev = sum(r[6] for r in money)
        n = len(hands)
        sessions.append((
            sid, site, started.isoformat(sep=" "), ended.isoformat(sep=" "),
            round(minutes, 1), n, len({r[3] for r, _w in hands}),
            round(net, 2), round(ev, 2),
            round(100 * net / len(money), 2) if money else None,
            round(100 * ev / len(money), 2) if money else None))

        # Tables open at each hand: distinct tables dealt within the window
        # before it. The window slides over hands already in time order, so
        # this is one pass and not one query per hand.
        recent = []
        for r, when in hands:
            recent = [(t, w) for t, w in recent
                      if when - w <= timedelta(minutes=TABLING_WINDOW)]
            recent.append((r[3], when))
            open_now = len({t for t, _w in recent})
            stamps.append((sid, round((when - started).total_seconds() / 60, 1),
                           round(minutes, 1), open_now, r[0]))

    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    sessions)

    # Onto every decision in the hand, not only hero's: the sitting is a
    # property of the hand, and "how does the pool play against me in my
    # fourth hour" is a question about the other seats.
    con.execute("CREATE TEMP TABLE staged (session_id INT, session_min REAL, "
                "session_len REAL, tables_now INT, hand_id TEXT)")
    con.executemany("INSERT INTO staged VALUES (?,?,?,?,?)", stamps)
    con.execute("CREATE INDEX temp.staged_h ON staged(hand_id)")
    con.execute("UPDATE decisions SET "
                + ", ".join(f"{c} = staged.{c}" for c in NAMES)
                + " FROM staged WHERE decisions.hand_id = staged.hand_id")
    # Length first, because "sessions of two to five hours" is a range on
    # it and a range can only seek on the leading column; minutes-in and the
    # id ride along so the other two questions read this index end to end
    # rather than the fifty-column table.
    con.execute("DROP INDEX IF EXISTS dec_session")
    con.execute("CREATE INDEX IF NOT EXISTS dec_session "
                "ON decisions(session_len, session_min, session_id)")
    con.execute("CREATE INDEX IF NOT EXISTS dec_tables ON decisions(tables_now)")
    con.execute("ANALYZE")
    con.commit()
    print(f"{len(sessions):,} sessions over {len(rows):,} of your hands, "
          f"{sum(1 for s in sessions if s[5] >= 50):,} of them 50 hands or more")
    con.close()
    return len(sessions)


def listing(db_path=DB, limit=30):
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT session_id, site, started, minutes, hands, tables, net_bb, "
        "ev_bb, bb100 FROM sessions ORDER BY started DESC LIMIT ?",
        (limit,)).fetchall()
    print(f"{'#':>4} {'site':10} {'started':17} {'mins':>6} {'hands':>6} "
          f"{'tbl':>4} {'net bb':>9} {'ev bb':>9} {'bb/100':>8}")
    for sid, site, started, mins, n, tables, net, ev, bb100 in rows:
        print(f"{sid:4} {site:10} {started[:16]:17} {mins:6.0f} {n:6,} "
              f"{tables:4} {net:+9.1f} {ev:+9.1f} "
              f"{'' if bb100 is None else f'{bb100:+8.1f}'}")
    print(f"\nThe clock is the site's, not yours -- see the module note.")


def check(db_path=DB):
    """
    Every hand you played is in exactly one sitting, and the sittings add up.

    The split is a derivation with nothing else to compare against, so it is
    checked by its own arithmetic: hands in sessions equal your hands, money
    in sessions equals your money, no session contains a gap longer than the
    rule, and no two sessions on a site overlap. Any one of these failing
    is a session boundary in the wrong place, which is a report about the
    wrong sitting.
    """
    con = sqlite3.connect(db_path)
    fails = []
    n_sess = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if not n_sess:
        print("FAIL: no sessions -- run `python sessions.py`")
        return False

    mine = con.execute(
        "SELECT COUNT(*) FROM hands WHERE hero_seat IS NOT NULL "
        "AND game = 'HOLDEM'").fetchone()[0]
    counted = con.execute("SELECT SUM(hands) FROM sessions").fetchone()[0]
    print(f"your hands, in sessions      {counted:,}/{mine:,}"
          f"{'' if counted == mine else '   <-- MISSED'}")
    if counted != mine:
        fails.append("hands in sessions do not add up to your hands")

    stamped = con.execute(
        "SELECT COUNT(DISTINCT hand_id) FROM decisions "
        "WHERE session_id IS NOT NULL").fetchone()[0]
    print(f"hands stamped on decisions   {stamped:,}/{mine:,}"
          f"{'' if stamped == mine else '   <-- MISSED'}")
    if stamped != mine:
        fails.append("decisions were not all stamped with a session")

    # Money. Sessions were summed from the same rows the results view sums,
    # so the totals must agree to the cent.
    net_s = con.execute("SELECT SUM(net_bb) FROM sessions").fetchone()[0] or 0
    net_h = con.execute(
        "SELECT SUM(s.net_bb) FROM hands h JOIN spots s "
        "ON s.hand_id = h.hand_id AND s.seat = h.hero_seat "
        "WHERE h.hero_seat IS NOT NULL AND h.game='HOLDEM' AND h.fmt <> 'MTT'"
    ).fetchone()[0] or 0
    same = abs(net_s - net_h) < 0.05
    print(f"money in sessions            {net_s:+,.1f} bb  vs your total "
          f"{net_h:+,.1f}{'' if same else '   <-- DISAGREE'}")
    if not same:
        fails.append("session money does not add up to your money")

    # No sitting holds a gap longer than the rule. Read back from the stamps
    # rather than trusted from the split.
    worst = 0.0
    for sid, in con.execute("SELECT session_id FROM sessions"):
        mins = [r[0] for r in con.execute(
            "SELECT DISTINCT session_min FROM decisions WHERE session_id=? "
            "ORDER BY 1", (sid,))]
        for a, b in zip(mins, mins[1:]):
            worst = max(worst, b - a)
    print(f"longest gap inside a session {worst:.1f} min  (rule: {GAP})"
          f"{'' if worst <= GAP else '   <-- a session spans a break'}")
    if worst > GAP:
        fails.append("a session contains a gap longer than the rule")

    overlap = con.execute("""
        SELECT COUNT(*) FROM sessions a JOIN sessions b
        ON a.site = b.site AND a.session_id < b.session_id
        AND a.ended >= b.started""").fetchone()[0]
    print(f"sessions overlap on a site   {overlap}"
          f"{'' if not overlap else '   <-- they must not'}")
    if overlap:
        fails.append("two sessions on one site overlap")

    # And what the tool is for: something to look at.
    big = con.execute("SELECT COUNT(*) FROM sessions WHERE hands >= 50").fetchone()[0]
    print(f"sessions of 50+ hands        {big} of {n_sess}")
    tabling = con.execute(
        "SELECT MIN(tables_now), MAX(tables_now) FROM decisions "
        "WHERE tables_now IS NOT NULL").fetchone()
    print(f"tables open at once          {tabling[0]} to {tabling[1]}")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--list" in argv:
        listing()
        return 0
    if "--help" in argv:
        print(__doc__)
        return 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
