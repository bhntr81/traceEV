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

Every number comes from the stat engine: a spot is a registry key, a rate
is `stats.rate`, the money is `query.results_of`, the chart is
`query.chart_of`. This module had its own SQL over `spots` until 14 Sep
2026, the last of six that did, and the cost was not tidiness: its cbet
rate came from a column that gave the raiser a cbet chance when they had
been bet into, a defect the engine's check had recorded against that
column for a week while this report went on printing it.

Tournaments are left out of everything involving money: MTT stacks are
tournament chips, and adding them to dollars gives a pool that appears to
have lost seventy thousand.

    python population.py           the report
    python population.py --check   split-half validation, PASS or FAIL
"""

import sqlite3
import sys
from pathlib import Path

import query
import sites
import stats

DB = Path(__file__).parent / "hands.db"

# The pool, as opposed to hero: cash ring games of a size where the position
# names mean what they usually mean.
#
# The revealing sites only, and that is not a default -- it is the premise.
# Everything below counts the combos the pool folded, which can only be
# done on a site that shows folded hands. ACR shows 23% of them. Loading it
# made `fmt='RING'` match both sites, and this filter silently went from a
# pool with 100% of its hole cards to one with 33%, which would have
# quietly rewritten every revealed range in this module.
POOL = f"is_hero=0 AND fmt='RING' AND n_players>=5 AND {sites.sql_in(sites.revealing())}"

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
RANKS = "AKQJT98765432"

# Each spot is a registry key, so a rate is always "of the times this was
# available" as the engine defines it -- and the same figure `query.py
# --pool` prints for the same filter. Counting 3-bets per hand dealt
# instead of per chance to 3-bet is how a tight table looks like a passive
# one; "call vs open" rather than `coldcall` because the leak map wants
# every seat that called a raise, blinds included.
SPOTS = [
    ("open (RFI)",    "rfi"),
    ("limp",          "limp"),
    ("3bet",          "threebet"),
    ("call vs open",  "call_open"),
    ("fold to 3bet",  "fold_to_3bet"),
    ("4bet",          "fourbet"),
    ("fold to steal", "fold_to_steal"),
    ("cbet flop",     "cbet_flop"),
    ("fold to cbet",  "fold_to_cbet"),
    ("raise cbet",    "raise_cbet"),
]

# A rate on a handful of chances is not a read. Both halves must clear this
# before a line is allowed to be called a finding.
MIN_HALF = 150
# Two halves of the same truth will not land on the same number. This is how
# far apart they may be, in percentage points, and still be one finding.
TOLERANCE = 8.0
GOAL = 5                # findings that must survive, for the run to pass


def rate(con, key, where="", cut=None, half=None):
    """How often the action was taken, of the times it was available."""
    clause = POOL + (" AND " + where if where else "")
    if half == "A":
        clause += f" AND played_at < '{cut}'"
    elif half == "B":
        clause += f" AND played_at >= '{cut}'"
    n, _k, p, _lo, _hi = stats.rate(con, key, clause)
    return n, 100.0 * (p or 0.0)          # p is None when nothing was seen


def money(con, key, taken, where=""):
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
    stat = stats.BY_KEY[key]
    did = stat.action if taken else f"NOT ({stat.action})"
    clause = f"({stat.chance}) AND ({did}) AND {POOL}"
    if where:
        clause += " AND " + where
    got = query.results_of(con, query.matching_seats(con, clause))
    if not got:
        return 0, 0.0, 0.0
    return got["hands"], got["bb100"], got["error"]


def grid(con, key, where=""):
    """The 13x13 chart: how often each combo takes the action."""
    clause = POOL + (" AND " + where if where else "")
    cells = query.chart_of(con, clause, key)["cells"]
    return {c: (k or 0, n) for c, (n, k) in cells.items()}


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

    print("=" * 68)
    print("POOL LEAK MAP -- ring cash, 5-6 handed, hero excluded")
    print("=" * 68)
    print("\n{:16} {:5} {:>7} {:>6}   {:>18}".format(
        "spot", "pos", "chances", "freq", "bb/100 when taken"))
    rows = []
    for label, key in SPOTS:
        for pos in POSITIONS:
            where = "position='{}'".format(pos)
            n, r = rate(con, key, where)
            if n < 60:
                continue
            n_did, bb_did, se_did = money(con, key, True, where)
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
    for title, key, where in (
            ("BTN opens, folded to them", "rfi", "position='BTN'"),
            ("BB folds to a steal", "fold_to_steal", "position='BB'"),
            ("anyone 3bets", "threebet", ""),
            ("anyone calls a raise", "call_open", "")):
        print("\n{}  (% of times dealt that combo)".format(title))
        print_grid(grid(con, key, where))
    con.close()


def check(db_path=DB):
    """Every line, split in two by time. Only the ones that agree count."""
    con = sqlite3.connect(db_path)
    cut = stats.split_point(con, POOL)
    print("split at {}\n".format(cut))
    print("{:16} {:5} {:>6} {:>6} {:>7} {:>7}  {}".format(
        "spot", "pos", "n A", "n B", "rate A", "rate B", "verdict"))

    survived = 0
    for label, key in SPOTS:
        for pos in POSITIONS:
            where = "position='{}'".format(pos)
            n_a, r_a = rate(con, key, where, cut, "A")
            n_b, r_b = rate(con, key, where, cut, "B")
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
        # The verdict is the exit code, so `check.py` can read it. It
        # never was: this check printed FAIL and returned success, and the
        # suite would have stayed green through a pool that fell apart.
        sys.exit(0 if check() >= GOAL else 1)
    else:
        report()
