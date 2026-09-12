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

A **game type** is the variant plus cash-or-MTT -- `{variant, cash|mtt}`.
The three v1 defaults are NLHE Cash, PLO4 Cash and PLO5 Cash. Site and
stake stay ordinary `--site` / `--stake` filters; inventing a type per
room would be a second copy of those flags. `--game-type plo4-cash`
is the bundle; `--game plo` is still the variant alone (MTT included).

    python games.py            the registry
    python games.py --check    aliases, headers, hole counts, types
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

# User-facing variant tokens, stored on `hands.variant`. The `game`
# column stays HOLDEM / OMAHA / OMAHA5 -- that is what the history
# writes and what `--game` already filters. These words are what a
# person types and what a Game Type is built from.
VARIANTS = {"HOLDEM": "nlhe", "OMAHA": "plo4", "OMAHA5": "plo5"}

# Cash vs tournament. The same fact as `sites.CASH`. This module
# cannot import sites -- sites imports the parsers, and the parsers
# import the registry -- so the string is written twice and
# `check()` refuses a drift.
CASH = "fmt != 'MTT'"

# The three types v1 ships. A key is the `game_type` column; the
# spec is how to recognise one from `game` + `fmt` when the column
# is not there yet (in-memory fixtures, a database that has not
# been rebuilt).
TYPES = {
    "nlhe-cash": {"game": "HOLDEM", "cash": True, "label": "NLHE Cash"},
    "plo4-cash": {"game": "OMAHA", "cash": True, "label": "PLO4 Cash"},
    "plo5-cash": {"game": "OMAHA5", "cash": True, "label": "PLO5 Cash"},
}

TYPE_ALIASES = {
    "nlhe-cash": "nlhe-cash", "nlhe": "nlhe-cash",
    "holdem-cash": "nlhe-cash", "holdem": "nlhe-cash",
    "plo4-cash": "plo4-cash", "plo": "plo4-cash", "plo4": "plo4-cash",
    "omaha-cash": "plo4-cash", "omaha": "plo4-cash",
    "plo5-cash": "plo5-cash", "plo5": "plo5-cash",
    "omaha5-cash": "plo5-cash", "omaha5": "plo5-cash",
}


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


def variant(game):
    """nlhe / plo4 / plo5 from the stored `game` value, or None."""
    return VARIANTS.get(game)


def type_id(game, fmt=None):
    """
    `nlhe-cash` / `plo4-mtt` / ... from the two facts a hand already has.

    MTT is named rather than dropped, so a cash default cannot silently
    include tournament chips and a later `--game-type` can still find
    the row. Unknown games stay None -- they are not guessed into NLHE.
    """
    v = variant(game)
    if not v:
        return None
    kind = "mtt" if (fmt or "").upper() == "MTT" else "cash"
    return f"{v}-{kind}"


def resolve_type(words):
    """
    `--game-type plo4-cash` as the registry keys.

    Several words for each default, because "plo" is what a person
    types and "plo4-cash" is the stored id. An unknown word is a
    KeyError naming the real ones, never a silent miss.
    """
    raw = (words or "").strip().lower().replace("_", "-")
    if not raw:
        raise KeyError("no game type named; --game-type takes "
                       + ", ".join(TYPES) + " (or plo, plo5, nlhe)")
    out = []
    for w in raw.split(","):
        w = w.strip().lower().replace("_", "-")
        if not w:
            continue
        try:
            key = TYPE_ALIASES[w]
        except KeyError:
            raise KeyError(f"no game type {w!r}; --game-type takes "
                           f"{', '.join(TYPES)} "
                           f"(or plo, plo5, nlhe)") from None
        if key not in out:
            out.append(key)
    if not out:
        raise KeyError("no game type named; --game-type takes "
                       + ", ".join(TYPES))
    return tuple(out)


def type_sql(words):
    """
    A `game` + cash clause for `--game-type`.

    Written against `game` and `fmt`, not `game_type`, so a filter
    compiled before a rebuild still matches -- the column is a
    convenience, the two facts it was derived from are the source.
    """
    parts = []
    for key in resolve_type(words):
        spec = TYPES[key]
        clause = f"game = '{spec['game']}'"
        if spec.get("cash"):
            clause += f" AND {CASH}"
        parts.append(f"({clause})" if " AND " in clause else clause)
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def type_label(key):
    """The words on the dropdown, or the key itself if it is unknown."""
    spec = TYPES.get(key)
    return spec["label"] if spec else key


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

    if variant("OMAHA") != "plo4" or variant("OMAHA5") != "plo5" \
            or variant("HOLDEM") != "nlhe":
        fails.append("variant() drifted from nlhe/plo4/plo5")
    if type_id("OMAHA", "RING") != "plo4-cash":
        fails.append(f"PLO4 RING typed {type_id('OMAHA', 'RING')!r}")
    if type_id("HOLDEM", "MTT") != "nlhe-mtt":
        fails.append(f"NLHE MTT typed {type_id('HOLDEM', 'MTT')!r}")
    if type_id("STUD", "RING") is not None:
        fails.append("unknown game was guessed into a type")

    types = [
        ("plo4-cash", "game = 'OMAHA'", True),
        ("plo", "game = 'OMAHA'", True),
        ("plo5", "game = 'OMAHA5'", True),
        ("nlhe-cash", "game = 'HOLDEM'", True),
    ]
    twrong = 0
    for word, want_game, want_cash in types:
        got = type_sql(word)
        if want_game not in got or (want_cash and CASH not in got):
            twrong += 1
            print(f"    --game-type {word}: {got!r}")
    print(f"--game-type words resolve      {len(types) - twrong}/{len(types)}")
    if twrong:
        fails.append(f"{twrong} --game-type aliases wrong")

    try:
        resolve_type("stud")
        fails.append("unknown --game-type word was accepted")
        print("unknown --game-type refused    NO")
    except KeyError:
        print("unknown --game-type refused    yes")

    # The cash test is written twice (this module cannot import
    # sites). A drift here would make `--game-type` and `--fmt cash`
    # mean two different things.
    import sites
    if CASH != sites.CASH:
        fails.append(f"games.CASH {CASH!r} drifted from sites.CASH "
                     f"{sites.CASH!r}")
        print("cash test matches sites.CASH   NO")
    else:
        print("cash test matches sites.CASH   yes")

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
    print()
    print(f"{'type':12} filter")
    for key, spec in TYPES.items():
        print(f"{key:12} {type_sql(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
