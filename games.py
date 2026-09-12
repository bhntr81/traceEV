"""
What a game is, decided once.

Mixing Hold'em VPIP with Omaha VPIP is the same class of error as mixing
two sites under `fmt='RING'`: the number that comes out describes neither
game and looks like a finding. So a variant is named here, and the rest
of the program asks. A parser reads the header's game words through
`of(...)` and writes the stored value; it never decides what "plo" means
a second time.

    HOLDEM    two hole cards. The 13x13 and all-in equity stay here.
    OMAHA     four hole cards, pot-limit Omaha. Strength uses two of
              four plus three board cards -- never all four as Hold'em.
    OMAHA5    five hole cards. Same two-plus-three rule; the extra
              card is another pair to choose from. Equity stays refused.

The default pool is Hold'em. `--game plo` is how you ask for the other
one; `--game all` is how you opt in to mixing them, and that has to be
a choice.

    python games.py            the registry
    python games.py --check    aliases, headers, hole counts
"""

import sys

# The value in the `game` column, and the word a parser writes.
GAMES = ("HOLDEM", "OMAHA", "OMAHA5")

# How many hole cards each game deals. Strength, combo and equity all
# assume two; asking them about four is how a river range comes back
# a third "straight draw" to a card that was never coming, only for a
# different reason -- the evaluator would use all four as if they were
# Hold'em.
HOLES = {"HOLDEM": 2, "OMAHA": 4, "OMAHA5": 5}
HOLE_COUNTS = frozenset(HOLES.values())

# What `--game` takes, mapped to the stored value. Several words for
# each, because "plo" is what a person types and "OMAHA" is what the
# history writes, and those have to be one fact.
ALIASES = {
    "holdem": "HOLDEM", "nlhe": "HOLDEM", "hold'em": "HOLDEM",
    "hold’em": "HOLDEM", "he": "HOLDEM",
    "plo": "OMAHA", "plo4": "OMAHA", "omaha": "OMAHA", "omaha4": "OMAHA",
    "p4": "OMAHA",
    "plo5": "OMAHA5", "omaha5": "OMAHA5", "5card": "OMAHA5",
    "5-card": "OMAHA5", "p5": "OMAHA5",
}

# The default pool. Every population query that does not name a game
# is this, for the same reason `population.POOL` pins itself to
# `sites.revealing()`: the alternative is averaging two games into a
# number that describes neither.
HOLD = "game = 'HOLDEM'"


def of(desc):
    """
    HOLDEM / OMAHA / OMAHA5 from a header's game words, or None.

    Five-card Omaha is checked first because its name contains
    "Omaha". A header that names nothing this registry knows is
    refused rather than guessed at -- the same rule as a file no
    parser recognises.
    """
    t = (desc or "").lower().replace("'", "'").replace("’", "'")
    compact = t.replace("-", "").replace(" ", "")
    if "5cardomaha" in compact or "omaha5" in compact:
        return "OMAHA5"
    if "omaha" in t:
        return "OMAHA"
    if "hold'em" in t or "holdem" in t:
        return "HOLDEM"
    return None


def holes(game):
    """How many hole cards this game deals."""
    try:
        return HOLES[game]
    except KeyError:
        raise KeyError(f"no game {game!r}; the registry knows "
                       f"{', '.join(GAMES)}") from None


def resolve(words):
    """
    `--game plo,plo5` as the stored values, or None for `--game all`.

    None means no filter: the caller asked to mix. An unknown word is
    a KeyError naming the real ones, never a silent miss.
    """
    raw = (words or "").strip().lower()
    if raw in ("all", "any"):
        return None
    out = []
    for w in raw.split(","):
        w = w.strip().lower()
        if not w:
            continue
        try:
            g = ALIASES[w]
        except KeyError:
            raise KeyError(f"no game {w!r}; --game takes "
                           f"{', '.join(sorted(ALIASES))} "
                           f"(or all)") from None
        if g not in out:
            out.append(g)
    if not out:
        raise KeyError("no game named; --game takes "
                       f"{', '.join(sorted(ALIASES))} (or all)")
    return tuple(out)


def sql(words):
    """A `game = ...` clause for `--game`, or `1=1` for all."""
    got = resolve(words)
    if got is None:
        return "1=1"
    if len(got) == 1:
        return f"game = '{got[0]}'"
    return "game IN (" + ", ".join(f"'{g}'" for g in got) + ")"


def check():
    """Headers we have seen, and words a person will type."""
    fails = []
    known = [
        ("Holdem (No Limit)", "HOLDEM"),
        ("Hold'em No Limit", "HOLDEM"),
        ("HOLDEM No Limit", "HOLDEM"),
        ("Omaha (Pot Limit)", "OMAHA"),
        ("OMAHA Pot Limit", "OMAHA"),
        ("5Card Omaha (Pot Limit)", "OMAHA5"),
        ("5-Card Omaha (Pot Limit)", "OMAHA5"),
        ("5CARD OMAHA Pot Limit", "OMAHA5"),
        ("Stud (Limit)", None),
    ]
    wrong = 0
    for desc, want in known:
        got = of(desc)
        if got != want:
            wrong += 1
            print(f"    {desc!r}: wanted {want}, got {got}")
    print(f"headers name the right game    {len(known) - wrong}/{len(known)}")
    if wrong:
        fails.append(f"{wrong} headers named wrongly")

    aliases = [
        ("holdem", ("HOLDEM",)),
        ("plo", ("OMAHA",)),
        ("plo4", ("OMAHA",)),
        ("omaha", ("OMAHA",)),
        ("plo5", ("OMAHA5",)),
        ("plo,plo5", ("OMAHA", "OMAHA5")),
        ("all", None),
        ("any", None),
    ]
    awrong = 0
    for word, want in aliases:
        got = resolve(word)
        if got != want:
            awrong += 1
            print(f"    --game {word}: wanted {want}, got {got}")
    print(f"--game words resolve           {len(aliases) - awrong}/{len(aliases)}")
    if awrong:
        fails.append(f"{awrong} --game aliases wrong")

    try:
        resolve("stud")
        fails.append("unknown --game word was accepted")
        print("unknown --game word refused     NO")
    except KeyError:
        print("unknown --game word refused     yes")

    for game, n in (("HOLDEM", 2), ("OMAHA", 4), ("OMAHA5", 5)):
        if holes(game) != n:
            fails.append(f"{game} deals {holes(game)}, not {n}")
    print(f"hole counts                    "
          f"{'yes' if not any('deals' in f for f in fails) else 'NO'}")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    print(f"{'game':8} cards  --game")
    by_game = {}
    for word, g in ALIASES.items():
        by_game.setdefault(g, []).append(word)
    for g in GAMES:
        print(f"{g:8} {HOLES[g]:5}  {', '.join(sorted(by_game[g]))}")
    print("  all                  no filter -- mixes games on purpose")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
