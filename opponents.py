"""
What one opponent does differently from everybody else, and what to do about it.

A page of a player's stats is not a read. Every number on it looks like
something, and most of them are what the whole pool does -- a 22% VPIP is
not a tell, it is Tuesday. The only figures worth a decision are the ones
where THIS player is measurably unlike the pool, and "measurably" has to
mean something stricter than "the number is bigger".

So every line here has to clear one bar: the player's 95% interval and the
pool's 95% interval must not overlap. That is a blunt test and it throws
away a lot of true differences. It also throws away every false one, which
at 700 hands a player is the trade worth making -- the alternative is a
report full of confident nonsense about people who did something twice.

What comes out is short. That is the point: three real reads beat thirty
plausible ones.

    python opponents.py                  who deviates most, ranked
    python opponents.py NAME             one opponent in full
    python opponents.py --check          PASS or FAIL on this run's goal
"""

import sqlite3
import sys
from pathlib import Path

import sites
from stats import BY_KEY, POOL, STATS, fmt, rate, rates_by_player, wilson

DB = Path(__file__).parent / "hands.db"

# A stat needs this many chances before a deviation is worth reading. It is
# not a statistical threshold -- the interval test is that -- it is a guard
# against reporting a 100% that came from one hand and happens to clear the
# test because the pool's interval is narrow.
MIN_CHANCES = 25

# A player needs this many hands before they are profiled at all.
MIN_HANDS = 150

# What a deviation means at the table. The point of a profile is the last
# column; without it the report is a list of numbers that agree they are
# unusual and say nothing about what to do.
EXPLOIT = {
    "vpip":         ("plays too many hands", "plays very few hands"),
    "pfr":          ("raises constantly -- their range is wide, not strong",
                     "raises rarely -- when they do, believe it"),
    "rfi":          ("opens too wide; 3bet them light",
                     "opens tight; fold more, and respect their opens"),
    "limp":         ("limps -- isolate wide and bet flops",
                     "never limps"),
    "iso":          ("attacks limpers hard", "lets limpers see flops"),
    "threebet":     ("3bets light; 4bet and call wider",
                     "3bets only value; fold everything marginal"),
    "coldcall":     ("calls raises cold -- squeeze them",
                     "will not call cold"),
    "squeeze":      ("squeezes often; flat less in front of them", ""),
    "fold_to_3bet": ("folds to 3bets -- 3bet them relentlessly",
                     "never folds to a 3bet; value 3bet only"),
    "fourbet":      ("4bets often; 3bet for value only",
                     "never 4bets; 3bet them wide with impunity"),
    "fold_to_4bet": ("folds to 4bets -- 4bet bluff them", ""),
    "steal":        ("steals relentlessly; defend blinds wider", ""),
    "fold_to_steal": ("folds blinds to steals -- open every button",
                      "defends blinds; steal tighter"),
    "bb_defend":    ("defends the big blind wide", "gives up the big blind"),
    "cbet_flop":    ("cbets everything -- float and raise them",
                     "cbets only when they hit; fold to their bets"),
    "fold_to_cbet": ("folds to cbets -- cbet every flop",
                     "will not fold to a cbet; cbet for value only"),
    "raise_cbet":   ("raises cbets as a bluff; call down lighter", ""),
    "donk_flop":    ("donks flops -- their check means weakness", ""),
    "checkraise_flop": ("check-raises often; cbet thinner",
                        "never check-raises; bet freely in position"),
    "cbet_turn":    ("barrels turns; call the flop wider",
                     "gives up on turns -- float their flop bets"),
    "delayed_cbet": ("bets turns after checking flops", ""),
    "probe_turn":   ("attacks checked flops", "lets checked flops go"),
    "float_turn":   ("floats and takes turns away", ""),
    "fold_to_turn_bet": ("folds turns -- double barrel them", ""),
    "fold_to_river_bet": ("folds rivers -- triple barrel them",
                          "calls rivers down; value bet, never bluff"),
    "river_agg":    ("bets rivers aggressively; call wider", ""),
    "overbet":      ("overbets; their big bets are not all value", ""),
    "wtsd":         ("goes to showdown too often -- value bet thin",
                     "folds before showdown -- bluff rivers"),
    "wwsf":         ("wins after the flop often -- a real postflop player",
                     "gives up after the flop"),
    "flop_agg":     ("bets and raises flops constantly -- call and raise back "
                     "wider", "passive on the flop; bet into them freely"),
    "cbet_river":   ("fires rivers after betting the turn; call wider",
                     "shuts down on rivers -- their turn bet is not a "
                     "commitment"),
    "squeeze":      ("squeezes often; flat less in front of them",
                     "never squeezes; flat in front of them freely"),
}


def profile(con, player, site, baseline=None):
    """
    Every stat where this player is measurably not the pool.

    The baseline is the pool ON THEIR OWN SITE. Comparing a ACR player
    against a mixed baseline would make every one of them look tight, since
    the Ignition pool in this database is looser -- the deviation would be
    between two populations rather than between a player and their peers.
    """
    where = f"player=? AND standard=1"
    base = baseline or f"{POOL} AND site='{site}'"
    out = []
    for s in STATS:
        n, k, p, lo, hi = rate(con, s, where, (player,))
        if n < MIN_CHANCES:
            continue
        bn, bk, bp, blo, bhi = rate(con, s, base)
        if not bn:
            continue
        if lo > bhi:
            out.append((s, n, p, bp, "high", lo - bhi))
        elif hi < blo:
            out.append((s, n, p, bp, "low", blo - hi))

    # Ranked by how far apart the two INTERVALS are, not by the gap between
    # the point estimates. Sorting on the point estimates ranks by sample
    # size in disguise: a 73% on 26 chances beats a 40% on 600 every time,
    # so the least reliable figure becomes the headline. That is the exact
    # failure this project's own post-mortem named -- quoting the extreme of
    # a distribution as though it were typical. Interval separation charges
    # a small sample for its width, so a large gap measured on nothing sinks
    # and a modest gap measured on plenty rises.
    out.sort(key=lambda r: -r[5])
    return out


def show(con, player, site):
    hands, is_hero = con.execute(
        "SELECT COUNT(DISTINCT hand_id), MAX(is_hero) FROM decisions "
        "WHERE player=?", (player,)).fetchone()
    print(f"\n{player}   ({site}, {hands} hands)")
    print("=" * (len(player) + 24))

    devs = profile(con, player, site)
    if not devs:
        print("\n  nothing clears the bar -- this player is the pool, as far")
        print("  as this many hands can tell. That is a finding, not a gap.")
        return 0

    # The same sentence means opposite things depending on who is reading it.
    # "raises constantly -- their range is wide, not strong" is advice for
    # playing AGAINST this player; pointed at yourself it is a description of
    # how the pool can play against you. Useful either way, and dishonest if
    # the heading does not say which.
    if is_hero:
        print("\n  This is YOU against the pool, so read the last column as")
        print("  what the pool could do about you -- not as a list of leaks.")
        print("\n  A leak is a difference from correct play, and the pool is")
        print("  not correct: it under-raises at every depth (see poptree.py).")
        print("  Being unlike it is usually right. `leaks.py` prices hero")
        print("  against the solver, which is the report that finds money.")
        header = "how the pool could use it"
    else:
        header = "read"

    print(f"\n  {'stat':22} {'them':>8} {'pool':>8}   {header}")
    print("  " + "-" * 72)
    for s, n, p, bp, way, _gap in devs:
        note = EXPLOIT.get(s.key, ("", ""))[0 if way == "high" else 1]
        arrow = "^" if way == "high" else "v"
        print(f"  {s.label:22} {100 * p:6.1f}% {100 * bp:7.1f}% {arrow}  "
              f"{note or '(no standard adjustment)'}")
        print(f"  {'':22} {'n=' + str(n):>8}")
    return len(devs)


def leaderboard(con, site, limit=15):
    """
    Who is worth having a plan for.

    Ranked by how many stats clear the bar, because a player who is unusual
    in six ways is both more exploitable and more reliably measured than one
    who is unusual in one. Players below the hand threshold are counted, not
    hidden -- how much of the pool cannot be profiled is itself the answer to
    whether this is working yet.
    """
    everyone = con.execute(
        "SELECT player, COUNT(DISTINCT hand_id) h FROM decisions "
        "WHERE site=? AND is_hero=0 AND player IS NOT NULL "
        "GROUP BY player ORDER BY h DESC", (site,)).fetchall()
    eligible = [(p, h) for p, h in everyone if h >= MIN_HANDS]
    hands_of = dict(eligible)
    print(f"\n{site}: {len(everyone)} opponents seen, "
          f"{len(eligible)} with {MIN_HANDS}+ hands")

    # One pass per stat, not one per player per stat, and the pool baseline
    # computed once rather than recomputed for all 48 of them.
    base = f"{POOL} AND site='{site}'"
    where = f"standard=1 AND site='{site}' AND is_hero=0"
    devs_of = {p: [] for p, _ in eligible}
    for s in STATS:
        bn, bk, bp, blo, bhi = rate(con, s, base)
        if not bn:
            continue
        for player, (n, k) in rates_by_player(con, s, where).items():
            if player not in devs_of or n < MIN_CHANCES:
                continue
            p, lo, hi = wilson(k, n)
            if lo > bhi:
                devs_of[player].append((s, n, p, bp, "high", lo - bhi))
            elif hi < blo:
                devs_of[player].append((s, n, p, bp, "low", blo - hi))

    rows = []
    for player, devs in devs_of.items():
        devs.sort(key=lambda r: -r[5])
        rows.append((len(devs), hands_of[player], player, devs))
    rows.sort(reverse=True, key=lambda r: (r[0], r[1]))

    none = sum(1 for r in rows if r[0] == 0)
    print(f"{len(rows) - none} of them deviate from the pool on at least one "
          f"stat; {none} do not.\n")
    print(f"  {'player':22} {'hands':>6} {'reads':>6}   biggest")
    print("  " + "-" * 72)
    for count, h, p, devs in rows[:limit]:
        if not devs:
            top = "-- plays like the pool --"
        else:
            s, n, pr, bp, way, _gap = devs[0]
            top = (f"{s.label} {100 * pr:.0f}% vs {100 * bp:.0f}% "
                   f"({'high' if way == 'high' else 'low'}, n={n})")
        print(f"  {p:22} {h:6d} {count:6d}   {top}")
    return rows


def check(db_path=DB):
    """
    The goal for this run, checked rather than asserted.

    (a) every reported deviation clears non-overlapping 95% intervals;
    (b) the count of players clearing the bar is reported, zero included;
    (c) any player under the hand threshold is excluded and counted.
    """
    con = sqlite3.connect(db_path)
    fails = []
    # Only a site with names has opponents to profile; the registry says
    # which those are, and each is held to the same test.
    # A player is measured against THEIR site's pool, and so must the
    # check be. Re-deriving against the pool of every named site together
    # agreed with the report for as long as there was one named site, and
    # called 29 of PokerStars' deviations wrong the day there were two.
    rows = [(site, r) for site in sites.named()
            for r in leaderboard(con, site, limit=0)]

    # (a) re-derive every reported deviation the long way and confirm the
    #     intervals really are disjoint. The report is not trusted to have
    #     applied its own rule.
    checked = bad = 0
    for site, (count, h, p, devs) in rows:
        for s, n, pr, bp, way, _gap in devs:
            _, _, _, lo, hi = rate(con, s, "player=? AND standard=1", (p,))
            _, _, _, blo, bhi = rate(con, s, f"{POOL} AND site='{site}'")
            checked += 1
            if not (lo > bhi or hi < blo):
                bad += 1
    print(f"deviations reported     {checked}")
    print(f"intervals really disjoint {checked - bad}/{checked}"
          f"{'' if not bad else '   <-- the rule is not being applied'}")
    if bad:
        fails.append("intervals")

    # (b) and (c)
    total = con.execute(
        "SELECT COUNT(*) FROM (SELECT player FROM decisions WHERE "
        f"{sites.sql_in(sites.named())} AND is_hero=0 AND player IS NOT NULL "
        "GROUP BY player)").fetchone()[0]
    print(f"opponents seen          {total}")
    print(f"profiled ({MIN_HANDS}+ hands)  {len(rows)}")
    print(f"excluded as too thin    {total - len(rows)}")
    with_reads = sum(1 for _site, r in rows if r[0])
    print(f"with at least one read  {with_reads}")

    # A profile that fires on everybody is not measuring a player, it is
    # measuring a mistake -- most likely a baseline that includes the player
    # themselves, or one drawn from the wrong site.
    if rows and with_reads == len(rows):
        print("  every single player deviates -- the baseline is wrong")
        fails.append("baseline")
    con.close()
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    con = sqlite3.connect(DB)
    if "--check" in args:
        sys.exit(0 if check() else 1)
    if args:
        name = args[0]
        site = con.execute(
            "SELECT site FROM decisions WHERE player=? LIMIT 1", (name,)).fetchone()
        if not site:
            print(f"no hands recorded for {name!r}")
            sys.exit(1)
        show(con, name, site[0])
    else:
        for site in sites.named():
            leaderboard(con, site)
