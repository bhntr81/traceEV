"""
What one opponent does differently from everybody else, and what to do about it.

A page of a player's stats is not a read. Every number on it looks like
something, and most of them are what the whole pool does -- a 22% VPIP is
not a tell, it is Tuesday. The only figures worth a decision are the ones
where THIS player is measurably unlike the pool, and "measurably" has to
mean something stricter than "the number is bigger".

So every line here has to clear one bar, and it is the bar the rest of the
project uses: the interval on the DIFFERENCE between the player and their
pool must not contain zero, and the p-value must survive being one of
thirty-six questions asked about the same player. Until 14 Sep 2026 the
rule here was that the two intervals must not overlap -- the test
CLAUDE.md retired on 5 Sep because it behaves like a test at about the 99%
level and throws away real differences. It also threw away time: the check
re-derived every reported deviation with two full-table queries each, and
at three sites took five minutes of CPU. Everything is one pass per stat
now, for the leaderboard and for the check.

The pool a player is measured against is their own site's pool WITHOUT
them in it. A regular with two thousand hands is a noticeable fraction of
the pool, and measuring them against a baseline they are part of shrinks
every difference they have.

What comes out is short. That is the point: three real reads beat thirty
plausible ones.

    python opponents.py                  who deviates most, ranked
    python opponents.py NAME             one opponent in full
    python opponents.py --check          PASS or FAIL on this run's goal
"""

import sqlite3
import sys
from pathlib import Path

import notes
import sites
from stats import (BY_KEY, POOL, STATS, difference, fmt, holm, rate,
                   rates_by_player)

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


def deviations(con, site, min_hands=MIN_HANDS):
    """
    Every player's reads on one site, from one pass per stat.

    Returns {player: [(stat, n, p, pool_p, "high"|"low", gap)]}, each list
    sorted with the surest read first, and {player: hands} beside it.

    The baseline is the pool ON THEIR OWN SITE, and without them in it.
    Comparing an ACR player against a mixed baseline would make every one
    of them look tight, since the Ignition pool here is looser -- the
    deviation would be between two populations rather than between a player
    and their peers. And a regular with two thousand hands is a noticeable
    part of the pool, so leaving them in the baseline shrinks every
    difference they have; their own chances and cases are subtracted out.

    A read has to clear two bars. The interval on the difference must not
    contain zero. And since thirty-six stats are asked about every player,
    the p-values are corrected per player with Holm -- one player with one
    read at p = 0.04 is what thirty-six questions about nothing look like.

    Ranked by how far the difference interval sits from zero, not by the gap
    between the point estimates. Sorting on the estimates ranks by sample
    size in disguise: a 73% on 26 chances beats a 40% on 600 every time, so
    the least reliable figure becomes the headline. That is the failure the
    project's own post-mortem named -- quoting the extreme of a distribution
    as though it were typical. The interval's near edge charges a small
    sample for its width.
    """
    everyone = con.execute(
        "SELECT player, COUNT(DISTINCT hand_id) h FROM decisions "
        "WHERE site=? AND is_hero=0 AND player IS NOT NULL "
        "GROUP BY player", (site,)).fetchall()
    hands_of = {p: h for p, h in everyone if h >= min_hands}
    base = f"{POOL} AND site='{site}'"
    where = f"standard=1 AND site='{site}' AND is_hero=0"

    tested = {p: [] for p in hands_of}           # (stat, n, k, on, ok, pv)
    for s in STATS:
        bn, bk, _bp, _blo, _bhi = rate(con, s, base)
        if not bn:
            continue
        for player, (n, k) in rates_by_player(con, s, where).items():
            if player not in tested or n < MIN_CHANCES:
                continue
            # Them, against everybody who is not them.
            on, ok = bn - n, bk - k
            if on < MIN_CHANCES:
                continue
            _d, _lo, _hi, pv = difference(k, n, ok, on)
            tested[player].append((s, n, k, on, ok, pv))

    out = {}
    for player, rows in tested.items():
        adjusted = holm([(s.key, pv) for s, _n, _k, _on, _ok, pv in rows])
        reads = []
        for s, n, k, on, ok, _pv in rows:
            if adjusted.get(s.key, 1.0) >= 0.05:
                continue
            d, lo, hi, _p = difference(k, n, ok, on)
            way = "high" if d > 0 else "low"
            # The near edge of the interval: how far from zero it surely is.
            gap = lo if d > 0 else -hi
            reads.append((s, n, k / n, ok / on, way, gap))
        reads.sort(key=lambda r: -r[5])
        out[player] = reads
    return out, hands_of


def profile(con, player, site):
    """One opponent's reads, by the same rule as everybody else's."""
    reads, _hands = deviations(con, site, min_hands=1)
    return reads.get(player, [])


def show(con, player, site):
    hands, is_hero = con.execute(
        "SELECT COUNT(DISTINCT hand_id), MAX(is_hero) FROM decisions "
        "WHERE player=?", (player,)).fetchone()
    print(f"\n{player}   ({site}, {hands} hands)")
    print("=" * (len(player) + 24))
    # Your own words about them, above the numbers, because that is the
    # order they are useful in at the table.
    written = notes.note_of(con, site, player)
    if written:
        print(f"  note: {written}")

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
        print()
        print("  This is YOU against the pool, so read the last column as")
        print("  what the pool could do about you -- not as a list of leaks.")
        print("  A leak is a difference from correct play, and the pool is")
        print("  not correct, so being unlike it is often right. This says")
        print("  where you are unlike it; it does not say which way is better.")
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
    seen = con.execute(
        "SELECT COUNT(DISTINCT player) FROM decisions "
        "WHERE site=? AND is_hero=0 AND player IS NOT NULL", (site,)).fetchone()[0]
    devs_of, hands_of = deviations(con, site)
    print(f"\n{site}: {seen} opponents seen, "
          f"{len(hands_of)} with {MIN_HANDS}+ hands")

    rows = [(len(devs), hands_of[player], player, devs)
            for player, devs in devs_of.items()]
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

    (a) a sample of reported reads, re-derived independently, clears zero
        on the interval of the difference;
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

    # (a) re-derive a sample of the reported reads the long way -- one
    #     query for the player, one for the pool -- and confirm the interval
    #     on the difference really clears zero. The report is not trusted to
    #     have applied its own rule. A sample, because re-deriving all of
    #     them is thousands of full-table queries: the check took five
    #     minutes of CPU at three sites, which is a check nobody runs.
    every = [(site, p, dev) for site, (_c, _h, p, devs) in rows for dev in devs]
    sample = every[:: max(1, len(every) // 40)][:40]
    bad = 0
    for site, p, (s, n, pr, bp, way, _gap) in sample:
        n1, k1, _p1, _lo1, _hi1 = rate(con, s, "player=? AND standard=1", (p,))
        bn, bk, _bp, _blo, _bhi = rate(con, s, f"{POOL} AND site='{site}'")
        d, lo, hi, _pv = difference(k1, n1, bk - k1, bn - n1)
        if not (lo > 0 or hi < 0):
            bad += 1
    print(f"reads reported          {len(every)}")
    print(f"re-derived, clear zero  {len(sample) - bad}/{len(sample)}"
          f"{'' if not bad else '   <-- the rule is not being applied'}")
    if bad:
        fails.append("difference intervals")

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
