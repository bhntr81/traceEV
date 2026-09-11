"""
What the Ignition pool actually does, and what it actually costs them.

Two things come out of this. The first is a leak map: for each spot, how
often the population takes each line and what they make when they do, so
the places the pool bleeds can be ranked by money rather than by opinion.
The second is the part no other site's data allows -- the revealed range.
Ignition shows every player's hole cards including the ones they folded, so
the combos the pool opens, folds and defends with can be counted straight
off, not inferred from the few hands that reached showdown.

Nothing here is called a finding until it survives being split in half.
Rates measured on a few thousand hands will happily produce exciting
fictions, so every line is recomputed on the first half of the sessions and
the second half separately, and only the ones that agree are reported as
real. `--check` prints that count against the goal.

Tournaments are left out of everything involving money: MTT stacks are
tournament chips, and adding them to dollars gives a pool that appears to
have lost seventy thousand.

    python population.py           the report
    python population.py --check   split-half validation, PASS or FAIL
"""

import sqlite3
import sys
from pathlib import Path

import sites

DB = Path(__file__).parent / "hands.db"

# The pool, as opposed to hero: cash ring games of a size where the position
# names mean what they usually mean.
#
# Ignition only, and that is not a default -- it is the premise. Everything
# below counts the combos the pool folded, which can only be done on a site
# that shows folded hands. ACR shows 23% of them. Loading it made
# `fmt='RING'` match both sites, and this filter silently went from a pool
# with 100% of its hole cards to one with 33%, which would have quietly
# rewritten every revealed range in this module.
POOL = f"is_hero=0 AND fmt='RING' AND n_players>=5 AND {sites.sql_in(sites.revealing())}"

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
RANKS = "AKQJT98765432"

# Each spot is the chance to do something and the doing of it, so a rate is
# always "of the times this was available". Counting 3-bets per hand dealt
# instead of per chance to 3-bet is how a tight table looks like a passive one.
SPOTS = [
    ("open (RFI)",        "rfi_chance",     "rfi"),
    ("limp",              "rfi_chance",     "limped"),
    ("3bet",              "threebet_chance", "threebet"),
    ("cold call",         "threebet_chance", "cold_call"),
    ("fold to 3bet",      "faced_threebet", "fold_to_threebet"),
    ("4bet",              "faced_threebet", "fourbet"),
    ("fold to steal",     "faced_steal",    "fold_to_steal"),
    ("cbet flop",         "cbet_chance",    "cbet"),
    ("fold to cbet",      "faced_cbet",     "fold_to_cbet"),
    ("raise cbet",        "faced_cbet",     "raised_cbet"),
]

# A rate on a handful of chances is not a read. Both halves must clear this
# before a line is allowed to be called a finding.
MIN_HALF = 150
# Two halves of the same truth will not land on the same number. This is how
# far apart they may be, in percentage points, and still be one finding.
TOLERANCE = 8.0
GOAL = 5                # findings that must survive, for the run to pass


def halves(con):
    """
    The session split in two by time.

    Splitting by date rather than at random is the harder test: a random
    split shares tables and opponents between the halves, so a quirk of one
    table shows up in both and looks like a population truth.
    """
    cut = con.execute(
        "SELECT played_at FROM spots WHERE {} ORDER BY played_at "
        "LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM spots WHERE {})"
        .format(POOL, POOL)).fetchone()
    return cut[0] if cut else None


def rate(con, chance, action, where="", cut=None, half=None):
    """How often the action was taken, of the times it was available."""
    sql = ("SELECT SUM({}), SUM({}) FROM spots WHERE {} AND {}=1"
           .format(action, chance, POOL, chance))
    args = []
    if where:
        sql += " AND " + where
    if half == "A":
        sql += " AND played_at < ?"
        args.append(cut)
    elif half == "B":
        sql += " AND played_at >= ?"
        args.append(cut)
    got, tot = con.execute(sql, args).fetchone()
    got, tot = got or 0, tot or 0
    return tot, (100.0 * got / tot if tot else 0.0)


def money(con, chance, action, taken, where=""):
    """
    bb/100 for the players who did, or did not, take the line, and its error.

    This is the whole hand's result for the seat, not just the street's, and
    that is deliberate: the cost of limping is not the blind, it is the hand
    that follows the limp.

    The error bar is not decoration. One hand's result has a standard
    deviation of nearly 12bb, so a win rate measured over 100 hands carries
    an error of about 117bb/100 -- larger than almost any real edge. Without
    it, a table of bb/100 figures is a table of noise sorted by size, and
    reads exactly like a discovery. Returned as one standard error; a figure
    is only worth repeating at two of them or more.
    """
    sql = ("SELECT COUNT(net_bb), AVG(net_bb), "
           "AVG(net_bb*net_bb) - AVG(net_bb)*AVG(net_bb) "
           "FROM spots WHERE {} AND {}=1 AND {}={}"
           .format(POOL, chance, action, 1 if taken else 0))
    if where:
        sql += " AND " + where
    n, mean, var = con.execute(sql).fetchone()
    if not n or mean is None:
        return 0, 0.0, 0.0
    # Sample variance from the population one, so a small n is not flattered.
    var = (var or 0.0) * n / (n - 1) if n > 1 else 0.0
    return n, 100.0 * mean, 100.0 * (var ** 0.5) / (n ** 0.5)


def grid(con, chance, action, where=""):
    """The 13x13 chart: how often each combo takes the action."""
    sql = ("SELECT combo, SUM({}), COUNT(*) FROM spots WHERE {} AND {}=1 "
           "AND combo IS NOT NULL".format(action, POOL, chance))
    if where:
        sql += " AND " + where
    sql += " GROUP BY combo"
    return {c: (got or 0, tot) for c, got, tot in con.execute(sql)}


def print_grid(cells, min_n=3):
    """
    Suited above the diagonal, offsuit below, the way a range chart reads.

    A cell with too few observations is left blank rather than shown as 0%
    or 100%, because one hand dealt twice is not a frequency.
    """
    print("      " + " ".join("{:>3}".format(r) for r in RANKS))
    for i, hi in enumerate(RANKS):
        row = []
        for j, lo in enumerate(RANKS):
            if i == j:
                combo = hi + hi
            elif i < j:
                combo = hi + lo + "s"
            else:
                combo = lo + hi + "o"
            got, tot = cells.get(combo, (0, 0))
            row.append("  ." if tot < min_n else "{:>3.0f}".format(100.0 * got / tot))
        print("  {:>2}  {}".format(hi, " ".join(row)))
    print("      (blank = fewer than {} times dealt in this spot)".format(min_n))


def report(db_path=DB):
    con = sqlite3.connect(db_path)
    cut = halves(con)

    print("=" * 68)
    print("POOL LEAK MAP -- ring cash, 5-6 handed, hero excluded")
    print("=" * 68)
    print("\n{:16} {:5} {:>7} {:>6}   {:>18}".format(
        "spot", "pos", "chances", "freq", "bb/100 when taken"))
    rows = []
    for label, chance, action in SPOTS:
        for pos in POSITIONS:
            where = "position='{}'".format(pos)
            n, r = rate(con, chance, action, where)
            if n < 60:
                continue
            n_did, bb_did, se_did = money(con, chance, action, True, where)
            rows.append((label, pos, n, r, n_did, bb_did, se_did))
    for label, pos, n, r, n_did, bb_did, se_did in rows:
        print("{:16} {:5} {:7d} {:5.1f}%   {:+9.0f} +/- {:<5.0f} {}".format(
            label, pos, n, r, bb_did, se_did,
            "" if abs(bb_did) > 2 * se_did else "(noise)"))

    print("\n" + "=" * 68)
    print("WHERE THE POOL LOSES MOST")
    print("=" * 68)
    print("Only lines whose loss is larger than twice its own error bar. A")
    print("bb/100 figure that does not clear that is a number, not a leak.\n")
    print("{:16} {:5} {:>6} {:>10} {:>8}".format(
        "spot", "pos", "n", "bb/100", "+/-"))
    # Folding is left out. What a fold costs is not a strategic result, it is
    # the blind you already posted, and it costs exactly that every time --
    # so it clears any error bar trivially and would top this table forever
    # while telling us nothing. "Folding the small blind loses 50bb/100" is
    # arithmetic, not a leak.
    real = [r for r in rows
            if r[4] >= 100 and r[5] < -2 * r[6] and not r[0].startswith("fold")]
    for label, pos, n, freq, n_did, bb_did, se_did in sorted(real, key=lambda r: r[5]):
        print("{:16} {:5} {:6d} {:+10.0f} {:8.0f}".format(
            label, pos, n_did, bb_did, se_did))
    if not real:
        print("  none yet -- every loss measured so far is inside its own error")
    print("\n{} of {} money lines survive; the rest need more hands, not more code."
          .format(len(real), sum(1 for r in rows if r[4] >= 100)))

    print("\n" + "=" * 68)
    print("REVEALED RANGES -- the combos the pool actually holds")
    print("=" * 68)
    for title, chance, action, where in (
            ("BTN opens, folded to them", "rfi_chance", "rfi", "position='BTN'"),
            ("BB folds to a steal", "faced_steal", "fold_to_steal", "position='BB'"),
            ("anyone 3bets", "threebet_chance", "threebet", ""),
            ("anyone cold calls a raise", "threebet_chance", "cold_call", "")):
        print("\n{}  (% of times dealt that combo)".format(title))
        print_grid(grid(con, chance, action, where))
    con.close()


def check(db_path=DB):
    """Every line, split in two by time. Only the ones that agree count."""
    con = sqlite3.connect(db_path)
    cut = halves(con)
    print("split at {}\n".format(cut))
    print("{:16} {:5} {:>6} {:>6} {:>7} {:>7}  {}".format(
        "spot", "pos", "n A", "n B", "rate A", "rate B", "verdict"))

    survived = 0
    for label, chance, action in SPOTS:
        for pos in POSITIONS:
            where = "position='{}'".format(pos)
            n_a, r_a = rate(con, chance, action, where, cut, "A")
            n_b, r_b = rate(con, chance, action, where, cut, "B")
            if n_a < MIN_HALF or n_b < MIN_HALF:
                continue
            ok = abs(r_a - r_b) <= TOLERANCE
            survived += ok
            print("{:16} {:5} {:6d} {:6d} {:6.1f}% {:6.1f}%  {}".format(
                label, pos, n_a, n_b, r_a, r_b,
                "holds" if ok else "SPLIT -- {:.1f}pt gap".format(abs(r_a - r_b))))

    print("\n{} findings survived the split; goal was {}".format(survived, GOAL))
    print("RUN 2: {}".format("PASS" if survived >= GOAL else "FAIL"))
    con.close()
    return survived


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        report()
