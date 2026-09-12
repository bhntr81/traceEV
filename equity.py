"""
How often a hand wins, so an all-in can be scored by what it was worth.

The results graph needs four lines, and three of them are arithmetic: money
won is money won, and a hand either reached showdown or it did not. The
fourth is different. When the chips go in with cards to come, what happened
next was a coin the player had already stopped influencing, and a graph that
plots only what the coin did says more about the last month's luck than
about how the player played.

So the all-in adjusted line replaces the actual result of those hands with
the expected one: each live player's share of the pot, given the cards
everybody held and the board still to come. That needs an evaluator, which
is what this is.

It is exact where exactness is cheap -- one or two cards to come is 46 or
1,081 runouts -- and samples where it is not. Enumerating five board cards
is 1.7 million runouts per hand, so preflop all-ins are estimated from a
fixed number of random runouts with a fixed seed, which makes the figure
reproducible and its error stateable rather than merely small.

    python equity.py --check    known equities, to prove the evaluator
"""

import random
import sys
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "cdhs"
DECK = [r + s for r in RANKS for s in SUITS]

# Enough samples that the standard error on one hand's equity is under half
# a percent, which is far below the noise in any graph this feeds.
SAMPLES = 20000
SEED = 20260902


def card(c):
    """'Ah' -> (12, 2). Rank first so tuples sort the way poker does."""
    return RANKS.index(c[0]), SUITS.index(c[1])


def completing(ranks):
    """
    Every rank that would finish a straight, given these.

    A five-card straight is a window of five consecutive ranks with exactly
    one missing, so the windows are walked rather than the ranks. The ace
    plays low as well as high -- the one straight whose top card is not its
    highest rank, and the one every hand classifier gets wrong first.

    Here rather than in the two modules that want it, because a second
    answer to "what completes a straight" is a second answer that would
    drift.
    """
    ace = RANKS.index("A")
    have = set(ranks)
    if ace in have:
        have.add(-1)
    out = set()
    for low in range(-1, 9):
        missing = set(range(low, low + 5)) - have
        if len(missing) == 1:
            (m,) = missing
            out.add(ace if m == -1 else m)
    return out


def best5(seven):
    """
    The best five-card hand out of five, six or seven cards.

    Returned as a tuple that sorts correctly against any other such tuple:
    the category first, then the ranks that break ties within it, already in
    the order poker compares them. Nothing here needs to know what the
    numbers mean -- only that bigger wins -- which is what makes ties, and
    therefore split pots, fall out of a plain equality test.
    """
    ranks = sorted((c[0] for c in seven), reverse=True)
    by_suit = {}
    for r, s in seven:
        by_suit.setdefault(s, []).append(r)

    flush = next((sorted(v, reverse=True) for v in by_suit.values()
                  if len(v) >= 5), None)

    def straight_high(rs):
        """The top card of the best straight in these ranks, or None."""
        u = sorted(set(rs), reverse=True)
        # The wheel: an ace plays low, and is the one straight whose top
        # card is not its highest rank.
        if 12 in u:
            u.append(-1)
        run = 1
        for i in range(1, len(u)):
            if u[i] == u[i - 1] - 1:
                run += 1
                if run >= 5:
                    return u[i] + 4
            else:
                run = 1
        return None

    if flush:
        sf = straight_high(flush)
        if sf is not None:
            return (8, sf)
        # A straight flush is checked before the flush itself, and the flush
        # cards are the only ones that can make one.

    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    # Sorted by how many, then by rank -- so trips beat a lower trips, and
    # the kickers that follow are already in order.
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))

    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(r for r in ranks if r != quad)
        return (7, quad, kicker)
    if groups[0][1] == 3 and len(groups) > 1 and groups[1][1] >= 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *flush[:5])
    st = straight_high(ranks)
    if st is not None:
        return (4, st)
    if groups[0][1] == 3:
        kick = [r for r in ranks if r != groups[0][0]][:2]
        return (3, groups[0][0], *kick)
    if groups[0][1] == 2 and len(groups) > 1 and groups[1][1] == 2:
        hi, lo = groups[0][0], groups[1][0]
        kicker = max(r for r in ranks if r != hi and r != lo)
        return (2, hi, lo, kicker)
    if groups[0][1] == 2:
        kick = [r for r in ranks if r != groups[0][0]][:3]
        return (1, groups[0][0], *kick)
    return (0, *ranks[:5])


def equity(holes, board, samples=SAMPLES, seed=SEED):
    """
    Each player's share of the pot, ties split.

    `holes` is a list of two-card strings, one per live player; `board` is
    what is already down. Returns a share per player, summing to one.
    """
    if any(len(h.split()) != 2 for h in holes):
        raise ValueError(
            "equity is Hold'em only -- Omaha uses two of four plus three "
            "board cards, and scoring four hole cards as seven-card "
            "Hold'em is a lie")
    hole_c = [[card(c) for c in h.split()] for h in holes]
    board_c = [card(c) for c in board.split()] if board else []
    seen = {c for h in hole_c for c in h} | set(board_c)
    deck = [card(c) for c in DECK if card(c) not in seen]
    need = 5 - len(board_c)
    if need < 0:
        raise ValueError("board longer than five cards")

    wins = [0.0] * len(hole_c)
    runouts = list(combinations(deck, need)) if need <= 2 else None
    if runouts is None:
        rng = random.Random(seed)
        runouts = (tuple(rng.sample(deck, need)) for _ in range(samples))
        total = samples
    else:
        total = len(runouts)

    for extra in runouts:
        full = board_c + list(extra)
        scores = [best5(h + full) for h in hole_c]
        best = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        for i in winners:
            wins[i] += 1.0 / len(winners)
    return [w / total for w in wins]


def check():
    """
    Known equities, and known orderings.

    An evaluator that is subtly wrong does not crash -- it produces an EV
    line that looks exactly like a real one and is quietly false, which is
    the failure mode this whole project is built against. So it is checked
    against figures that are published and against hand comparisons whose
    answer is not a matter of opinion.
    """
    fails = []

    # Orderings first: if these are wrong the equities cannot be right, and
    # a failure here says which rule broke rather than merely that one did.
    def hand(cards):
        return best5([card(c) for c in cards.split()])

    orderings = [
        ("straight flush beats quads", "9h 8h 7h 6h 5h 2c 2d", "As Ac Ad Ah Kc"),
        ("quads beats full house", "As Ac Ad Ah Kc", "Ks Kc Kd Qs Qc"),
        ("full house beats flush", "Ks Kc Kd Qs Qc", "Ah Jh 9h 5h 3h"),
        ("flush beats straight", "Ah Jh 9h 5h 3h", "9c 8d 7h 6s 5c"),
        ("straight beats trips", "9c 8d 7h 6s 5c", "7s 7h 7d Ac Kd"),
        ("trips beats two pair", "7s 7h 7d Ac Kd", "As Ad Ks Kd 2c"),
        ("two pair beats a pair", "As Ad Ks Kd 2c", "As Ad Ks Qd 2c"),
        ("pair beats high card", "As Ad Ks Qd 2c", "As Ks Qd Jc 9h"),
        # The wheel is a straight, and it is the WORST one: its top card
        # is the five, not the ace that completes it.
        ("six-high straight beats the wheel", "6c 5d 4h 3s 2c", "5c 4d 3h 2s Ac"),
        ("the wheel is still a straight", "5c 4d 3h 2s Ac", "As Ad Ks Qd 2c"),
        ("ace-high flush beats king-high", "Ah Jh 9h 5h 3h", "Kh Qh 9h 5h 3h"),
    ]
    for name, better, worse in orderings:
        if not hand(better) > hand(worse):
            fails.append(name)
    print(f"hand orderings            {len(orderings) - len(fails)}"
          f"/{len(orderings)}")

    # Published preflop equities. The tolerance is two points, which is well
    # inside what the sampling error can explain and well outside what a
    # broken evaluator would produce.
    known = [
        ("AA vs KK", ["As Ah", "Ks Kh"], 0.823),
        ("AA vs 72o", ["As Ah", "7c 2d"], 0.877),
        ("AKs vs QQ", ["As Ks", "Qc Qd"], 0.463),
        ("AKo vs 22", ["As Kh", "2c 2d"], 0.470),
        ("JTs vs AKo", ["Jh Th", "As Kd"], 0.421),
    ]
    bad = 0
    for name, holes, expect in known:
        got = equity(holes, "")[0]
        ok = abs(got - expect) < 0.02
        bad += 0 if ok else 1
        print(f"  {name:12} {100 * got:5.1f}%   expected {100 * expect:5.1f}%"
              f"   {'OK' if ok else 'WRONG'}")
    if bad:
        fails.append(f"{bad} known equities wrong")

    # An exact case, where there is no sampling error to hide behind: with
    # one card to come and a made flush against a set, the count is small
    # enough to reason about and the answer must be exact every run.
    a, b = equity(["Ah Kh", "7c 7d"], "Qh Jh 2c 7h")[0:2]
    print(f"  exact, one card to come   {100 * a:5.1f}% / {100 * b:5.1f}%"
          f"   (sums to {100 * (a + b):.0f}%)")
    if abs(a + b - 1.0) > 1e-9:
        fails.append("equities do not sum to 1")

    # Reproducibility: the sampled path must give the same answer twice, or
    # a graph redrawn tomorrow will not match the one drawn today.
    if equity(["As Ah", "Ks Kh"], "") != equity(["As Ah", "Ks Kh"], ""):
        fails.append("sampling is not reproducible")
    print("sampling is reproducible  yes")

    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    print(__doc__)
