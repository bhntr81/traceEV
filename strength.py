"""
What a hand actually is once the flop is out, and what it might still become.

`combo` says AKs, which is everything before the flop and almost nothing
after it. Every postflop question a tracker is really asked -- how the pool
plays top pair with a weak kicker out of position, how often a gutshot
continues on a paired board, whether anybody folds a set -- needs the hand
named against the board, and nothing here has ever named it.

The evaluator exists already: `equity.best5` ranks five cards out of five,
six or seven and is checked against eleven known orderings and five
published preflop equities. Hold'em uses best-five of seven. Omaha uses
exactly two hole cards and three board cards -- scoring all four as
Hold'em is the lie `classify` used to refuse by returning NULL, and
now refuses by doing the 2+3 walk instead. This is classification, not
evaluation. It writes four columns:

    made     top pair, set, boat, ...   what the hand is now
    kicker   top, good, weak            only where a pair uses a hole card
    fd       nut, second, weak, backdoor    the flush draw, if any
    sd       oesd, double gutshot, gutshot  the straight draw, if any

Four rather than one, because a combo draw is not a fourteenth category --
it is a flush draw and a straight draw at once, and "pair plus a flush draw"
is `made` and `fd` together. One column per independent fact keeps the
number of filters that can be asked multiplicative instead of listing every
combination somebody thought of in advance.

**A draw has to be yours.** Four hearts on the board is not a flush draw,
it is a board everybody shares, and a hand that only plays the board has no
draw at all. Every test here requires a hole card to be part of the four
cards, or of the run of ranks -- which is also what stops "board pair" being
counted as a pair the player holds.

**Only where the cards are known**, which is 20,465 postflop decisions:
Ignition shows every hand at showdown including folds, ACR shows 23%. The
columns are NULL elsewhere, and NULL means "not known" and never "no draw".

Derived from `decisions`, so run it after that table is rebuilt.

    python strength.py            derive the four columns
    python strength.py --check    against hands whose answer is written down
    python strength.py --common   what the pool actually turns up with
"""

import sqlite3
import sys
from itertools import combinations
from pathlib import Path

from equity import RANKS, best5, card, completing

DB = Path(__file__).parent / "hands.db"

COLUMNS = ("made", "kicker", "fd", "sd")

# What `best5` returns first, in words. Trips is split afterwards -- a set
# and trips are the same five cards and completely different hands, because
# one of them is invisible to everybody else.
CATEGORY = {8: "straight flush", 7: "quads", 6: "boat", 5: "flush",
            4: "straight", 3: "trips", 2: "two pair", 1: "pair",
            0: "high card"}

# A kicker is judged against the board it plays on rather than against a
# fixed rank: what matters is whether anything beats it, and on a king-high
# board an ace is the top kicker while a queen is not.
ACE = RANKS.index("A")
GOOD = RANKS.index("T")

# Strongest first, which is the order a range wants to be read down.
ORDER = ("straight flush", "quads", "boat", "flush", "straight", "set",
         "trips", "two pair", "overpair", "top pair", "middle pair",
         "weak pair", "under pair", "board pair", "high card")

# Which of those count as weak -- the one judgement in this module that is
# opinion rather than cards. It is a single list for the reason Hand2Note
# puts it in a settings page with a checkbox per row: somebody will
# disagree, and the disagreement should be a line changed rather than an
# argument with the program.
#
# The line is drawn under middle pair, because a middle pair calls a river
# bet and a bottom pair does not, and what the number is for is exactly how
# much of a betting range is hands that cannot call.
WEAK = ("high card", "board pair", "weak pair", "under pair")

# PLO Weak %. One pair is not a calling hand in Omaha the way a
# middle pair is in Hold'em -- that is the line, and it lives here
# so disagreeing is a list change. Magnum AA classes are deferred.
OMAHA_WEAK = ("high card", "board pair", "weak pair", "under pair",
              "middle pair", "top pair")

# Hold'em placement names. On a PLO filter these are not offered
# as `--made` chips -- the histogram speaks in nuts+/strong/medium/
# weak made, not "top pair". Shared labels (flush, set, high card)
# stay, because they mean the same 2+3 hand.
HOLDEM_ONLY_MADE = frozenset((
    "top pair", "middle pair", "weak pair", "under pair", "overpair",
    "board pair", "two pair", "trips",
))

# Postflop histogram groups. First match wins; Other is implicit and
# never listed here -- a leftover (an unexpected `made`, a future
# label) has to land somewhere, and inventing a fifteenth bar for
# each one would hide that the classifier missed it.
#
# is_weak is the Weak % line: H2N's "how often they bluff" readout
# on a betting range. Air / Draws / Weak pair default to weak
# because those are the hands that cannot call a river bet -- air
# and semi-bluffs, and pairs below middle. The same opinion WEAK
# encodes, one layer up: which BARS count, so flipping a checkbox
# changes the percentage without rewriting the classifier.
#
# A made pair keeps its pair bar even when it also has a draw.
# Draws is only air-like hands (high card, board pair) that are
# still drawing -- otherwise "how does the pool play a gutshot"
# would include the top pairs that happen to have one, the
# failure `range_of` already refuses.
HIST_OTHER = "other"

# key, label, is_weak, made names, draw (None = ignore, False = none, True = any)
HIST_SPEC = (
    ("air", "Air", True, ("high card", "board pair"), False),
    ("draws", "Draws", True, ("high card", "board pair"), True),
    ("weak_pair", "Weak pair", True, ("weak pair", "under pair"), None),
    ("middle_pair", "Middle pair", False, ("middle pair",), None),
    ("top_pair", "Top pair", False, ("top pair",), None),
    ("overpair", "Overpair", False, ("overpair",), None),
    ("two_pair", "Two pair", False, ("two pair",), None),
    ("trips", "Trips", False, ("trips",), None),
    ("set", "Set", False, ("set",), None),
    ("straight", "Straight", False, ("straight",), None),
    ("flush", "Flush", False, ("flush",), None),
    ("boat", "Boat", False, ("boat",), None),
    ("quads", "Quads", False, ("quads",), None),
    ("straight_flush", "Straight flush", False, ("straight flush",), None),
)

# Omaha default postflop groups. First match wins. Made hands stay
# on a made bar even with a draw -- same rule as Hold'em -- so
# "how does the pool play a wrap" is air-like hands, not the sets
# that happen to have one. Combo is FD+SD; wrap is any straight
# draw without a flush draw (gutshot/oesd sit here so they do not
# vanish into Other); FD is a flush draw with no straight draw.
#
# Nuts+ / strong / medium / weak made are opinion, one list, the
# same way WEAK is. Full Magnum AA taxonomy is deferred.
OMAHA_HIST_SPEC = (
    ("combo", "Combo", True, ("high card", "board pair"), "combo"),
    ("wrap", "Wrap", True, ("high card", "board pair"), "wrap"),
    ("fd", "FD", True, ("high card", "board pair"), "fd"),
    ("air", "Air", True, ("high card", "board pair"), False),
    ("weak_made", "Weak made", True,
     ("weak pair", "under pair", "middle pair", "top pair"), None),
    ("medium", "Medium", False, ("overpair", "two pair", "trips"), None),
    ("strong", "Strong", False, ("set", "straight", "flush"), None),
    ("nuts", "Nuts+", False, ("boat", "quads", "straight flush"), None),
)


def _quote_sql(name):
    return "'" + str(name).replace("'", "''") + "'"


def _draw_sql(draw):
    if draw is True:
        return "(fd IS NOT NULL OR sd IS NOT NULL)"
    if draw is False:
        return "(fd IS NULL AND sd IS NULL)"
    if draw == "combo":
        return "(fd IS NOT NULL AND sd IS NOT NULL)"
    if draw == "wrap":
        return "(sd IS NOT NULL AND fd IS NULL)"
    if draw == "fd":
        return "(fd IS NOT NULL AND sd IS NULL)"
    return None


def _draw_match(draw, fd, sd):
    """Same facts as `_draw_sql`, for an in-memory hand."""
    drawing = bool(fd or sd)
    if draw is True:
        return drawing
    if draw is False:
        return not drawing
    if draw == "combo":
        return bool(fd and sd)
    if draw == "wrap":
        return bool(sd and not fd)
    if draw == "fd":
        return bool(fd and not sd)
    return True


def group_key(name, specs=None):
    """The registry key for a typed group, or None."""
    raw = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in (HIST_OTHER, "other"):
        return HIST_OTHER
    if raw in ("nuts+", "nuts"):
        raw = "nuts"
    search = list(specs or []) + ([] if specs else list(HIST_SPEC))
    if specs is None:
        # A typed key from either family, so `--help` and a leftover
        # click still resolve. The filter that *uses* the key picks
        # the spec; this only names it.
        search = list(HIST_SPEC) + list(OMAHA_HIST_SPEC)
    for key, label, _w, _made, _draw in search:
        lab = label.lower().replace(" ", "_").replace("+", "")
        if raw in (key, lab, label.lower()):
            return key
    return None


def group_sql(key, specs=None):
    """Predicate for one named group. Other is leftover_sql."""
    specs = specs or HIST_SPEC
    if key == HIST_OTHER:
        return leftover_sql(specs)
    for k, _lab, _w, made, draw in specs:
        if k != key:
            continue
        sql = "made IN (" + ", ".join(_quote_sql(m) for m in made) + ")"
        extra = _draw_sql(draw)
        return f"{sql} AND {extra}" if extra else sql
    raise ValueError(f"unknown hist group {key!r}")


def leftover_sql(specs=None):
    """Shown hands that matched no group -- the Other bar."""
    specs = specs or HIST_SPEC
    return " AND ".join(
        f"NOT ({group_sql(k, specs)})" for k, *_rest in specs)


def group_filter_sql(key, specs=None):
    """Shown hands in this group. Other is the leftover."""
    specs = specs or HIST_SPEC
    if key == HIST_OTHER:
        return f"made IS NOT NULL AND ({leftover_sql(specs)})"
    return f"made IS NOT NULL AND ({group_sql(key, specs)})"


def group_of(made, fd=None, sd=None, specs=None):
    """
    Which histogram bar this shown hand belongs to.

    None if the cards were not named -- a preflop row, or a site
    that hid the hole cards. Other if they were named and still
    matched nothing, so a new `made` label cannot silently vanish.
    """
    if not made:
        return None
    for key, _lab, _w, names, draw in (specs or HIST_SPEC):
        if made not in names:
            continue
        if not _draw_match(draw, fd, sd):
            continue
        return key
    return HIST_OTHER


def hist_spec_for(argv=None, where=None):
    """Hold'em bars, or the Omaha family, from the filter that was asked."""
    import games
    if games.omaha_asked(argv) or games.omaha_where(where):
        return OMAHA_HIST_SPEC
    return HIST_SPEC


def weak_names_for(specs=None):
    """The `made` labels that count as weak under this family."""
    if specs is OMAHA_HIST_SPEC:
        return OMAHA_WEAK
    return WEAK


def holdem_made_refused(name):
    """True if this `--made` word is a Hold'em group on a PLO filter."""
    raw = (name or "").strip().lower()
    return raw in HOLDEM_ONLY_MADE


def hist_groups(specs=None, weak=None):
    """
    Ordered groups with is_weak applied. Other is not in this list.

    `weak` is an override set of keys -- the flip that changes Weak %
    without a group editor. None keeps the defaults.
    """
    specs = specs or HIST_SPEC
    out = []
    for key, label, default, _made, _draw in specs:
        is_weak = (key in weak) if weak is not None else default
        out.append({"key": key, "label": label, "is_weak": bool(is_weak),
                    "sql": group_sql(key, specs)})
    return out


def with_weak(groups, key, on):
    """A copy with one bar's is_weak flipped. Other is not a group."""
    out = []
    for g in groups:
        row = dict(g)
        if row["key"] == key:
            row["is_weak"] = bool(on)
        out.append(row)
    return out


def parse(text):
    return [card(c) for c in (text or "").split()]



def straight_draw(hole, board):
    """
    The straight draw the player holds, and not the one the board holds.

    A completing rank only counts when the five cards it would make include
    a rank the player has and the board has not -- otherwise the draw
    belongs to everybody at the table and describes nothing about this hand.

    And nothing draws on the river. `flush_draw` has always said so and this
    did not, so a river range came back 29.6% "straight draw" -- a third of
    it holding a draw to a card that was never coming.
    """
    if len(board) >= 5:
        return None
    hr, br = {r for r, _s in hole}, {r for r, _s in board}
    mine = set()
    for r in completing(hr | br):
        for low in range(-1, 9):
            window = set(range(low, low + 5))
            target = {ACE if x == -1 else x for x in window}
            if r in target and target <= (hr | br | {r}) and (target & hr) - br:
                mine.add(r)
                break
    if not mine:
        return None
    if len(mine) == 1:
        return "gutshot"
    # Open-ended and a double gutshot both need one of two ranks, and they
    # are told apart by whether the four cards already sit in a row.
    have = sorted((hr | br) | set())
    run = 1
    best = 1
    for i in range(1, len(have)):
        run = run + 1 if have[i] == have[i - 1] + 1 else 1
        best = max(best, run)
    return "oesd" if best >= 4 else "double gutshot"


def flush_draw(hole, board):
    """Four to a flush with a hole card in it, or three of them on the flop."""
    if len(board) >= 5:
        return None                 # nothing left to come
    counts, mine = {}, {}
    for r, s in board:
        counts[s] = counts.get(s, 0) + 1
    for r, s in hole:
        counts[s] = counts.get(s, 0) + 1
        mine.setdefault(s, []).append(r)
    for suit, n in counts.items():
        if suit not in mine:
            continue            # the board's flush, not the player's
        if n >= 5:
            return None         # already made
        if n == 4:
            top = max(mine[suit])
            return ("nut" if top == ACE
                    else "second" if top == ACE - 1 else "weak")
        if n == 3 and len(board) == 3:
            return "backdoor"
    return None


def pair_kind(pair_rank, hole, board):
    """
    Which pair it is, from the board's point of view.

    Top, middle and weak are positions among the board's own ranks, so the
    same two cards are top pair on one flop and nothing on another. A pocket
    pair over the board is an overpair; below it, it is ranked the same way
    every other pair is, because that is what it plays like.
    """
    hr = [r for r, _s in hole]
    br = sorted({r for r, _s in board}, reverse=True)
    held = [r for r in hr if r == pair_rank]

    if len(held) == 2:                       # a pocket pair
        if not br or pair_rank > br[0]:
            return "overpair", None
        kind = _place(pair_rank, br)
        return ("under" if kind == "weak" else kind) + " pair", None
    if not held:
        # The pair is entirely on the board. The player holds two unpaired
        # cards, so what they really have is high card -- calling it a pair
        # would count every hand on a paired board as having hit it.
        return "board pair", None
    kicker = max((r for r in hr if r != pair_rank), default=None)
    return _place(pair_rank, br) + " pair", _kicker(kicker, pair_rank, br)


def _place(rank, board_ranks):
    if not board_ranks or rank >= board_ranks[0]:
        return "top"
    if rank <= board_ranks[-1]:
        return "weak"
    return "middle"


def _kicker(rank, pair_rank, board_ranks):
    """Top, good or weak -- judged against what could beat it here."""
    if rank is None:
        return None
    better = [r for r in board_ranks if r > rank and r != pair_rank]
    if rank == ACE or not better:
        return "top"
    return "good" if rank >= GOOD else "weak"


def _named(shape, hole, table):
    """made / kicker from a five-card shape and the hole cards that made it."""
    name = CATEGORY[shape[0]]
    kicker = None
    if shape[0] == 3:
        # Trips from a pocket pair is a set: two of the three are hidden,
        # and being unreadable is most of what the hand is worth.
        name = "set" if all(r == shape[1] for r, _s in hole) else "trips"
    elif shape[0] == 1:
        name, kicker = pair_kind(shape[1], hole, table)
    elif shape[0] == 0:
        name = "high card"
    return name, kicker


def best_omaha_hand(hole, board):
    """
    Best PLO hand: exactly two hole cards and exactly three board cards.

    PLO5 still uses exactly two of five -- the extra card is another
    pair to choose from, not a third hole card in the five. Scoring
    all four (or five) as seven-card Hold'em is the lie this exists
    to refuse -- a royal on the felt with three suited hole cards is
    ace-high in Omaha, and one hole heart on a three-heart board is
    not a flush.
    """
    return best_omaha(hole, board)


def best_omaha(hole, board):
    """
    Best PLO hand: exactly two hole cards and exactly three board cards.

    Scoring all four (or five) hole cards as seven-card Hold'em is the
    lie this exists to refuse -- a royal on the felt with three suited
    hole cards is ace-high in Omaha, and calling it a straight flush
    would put every `--made` filter on fiction.
    """
    best = None
    for h2 in combinations(hole, 2):
        for b3 in combinations(board, 3):
            shape = best5(list(h2) + list(b3))
            if best is None or shape > best[0]:
                best = (shape, h2, b3)
    return best


def omaha_flush_draw(hole, board):
    """
    A flush draw that Omaha can actually make.

    The made hand uses two hole cards and three board cards, so a
    flush needs two hole cards of the suit. One hole heart on a
    four-heart board is a Hold'em flush and nothing in PLO -- the
    failure a 1-card flush-draw label would repeat for every range.
    """
    if len(board) >= 5:
        return None
    mine, board_n = {}, {}
    for r, s in board:
        board_n[s] = board_n.get(s, 0) + 1
    for r, s in hole:
        mine.setdefault(s, []).append(r)
    best = None
    rank = {"nut": 4, "second": 3, "weak": 2, "backdoor": 1}
    for suit, ranks in mine.items():
        if len(ranks) < 2:
            continue
        n = board_n.get(suit, 0)
        if n >= 3:
            continue                    # already a flush, if it was the best
        if n == 2:
            top = max(ranks)
            label = ("nut" if top == ACE
                     else "second" if top == ACE - 1 else "weak")
        elif n == 1 and len(board) == 3:
            label = "backdoor"
        else:
            continue
        if best is None or rank[label] > rank[best]:
            best = label
    return best


def _open_four(ranks):
    """Four consecutive ranks that complete both ways (not JQKA, not A234)."""
    u = sorted(set(ranks))
    if len(u) != 4 or u[-1] - u[0] != 3:
        return False
    # JQKA only completes with a ten. A234 only completes with a five.
    if u[-1] == ACE or u == [0, 1, 2, ACE]:
        return False
    return True


def omaha_straight_draw(hole, board):
    """
    Straight draw using two hole cards and three board cards.

    Hold'em's `completing` over unique ranks would call a pocket pair
    an open-ender -- both nines cannot sit in a five-card straight.
    Each 2+3 is scored with `best5` instead. Three or more completing
    ranks is a wrap, the PLO draw Hold'em has no word for.
    """
    if len(board) >= 5:
        return None
    used = set(hole) | set(board)
    outs = set()
    for rank in range(13):
        nxt = next(((rank, s) for s in range(4)
                    if (rank, s) not in used), None)
        if nxt is None:
            continue
        extended = list(board) + [nxt]
        found = False
        for h2 in combinations(hole, 2):
            for b3 in combinations(extended, 3):
                if nxt not in b3:
                    continue
                cat = best5(list(h2) + list(b3))[0]
                if cat in (4, 8):
                    outs.add(rank)
                    found = True
                    break
            if found:
                break
    if not outs:
        return None
    if len(outs) == 1:
        return "gutshot"
    if len(outs) >= 3:
        return "wrap"
    for h2 in combinations(hole, 2):
        for b2 in combinations(board, 2):
            if _open_four([r for r, _s in h2] + [r for r, _s in b2]):
                return "oesd"
    return "double gutshot"


def classify_holdem(hole, table):
    """Hold'em: best five of two hole plus the board."""
    shape = best5(hole + table)
    name, kicker = _named(shape, hole, table)
    fd = flush_draw(hole, table) if shape[0] < 5 else None
    sd = straight_draw(hole, table) if shape[0] < 4 else None
    return (name, kicker, fd, sd)


def classify_omaha(hole, table):
    """PLO4 / PLO5: best two hole cards and three board cards."""
    picked = best_omaha(hole, table)
    if picked is None:
        return (None, None, None, None)
    shape, h2, _b3 = picked
    # pair_kind is judged against the whole board -- "top pair" means
    # the top card of the flop, not of the three cards this combo used.
    name, kicker = _named(shape, h2, table)
    fd = omaha_flush_draw(hole, table) if shape[0] < 5 else None
    sd = omaha_straight_draw(hole, table) if shape[0] < 4 else None
    return (name, kicker, fd, sd)


def classify(cards, board):
    """(made, kicker, fd, sd) for one hand on one board, or all None."""
    hole, table = parse(cards), parse(board)
    if len(table) < 3:
        return (None, None, None, None)
    n = len(hole)
    if n == 2:
        return classify_holdem(hole, table)
    if n in (4, 5):
        # Five-card Omaha uses the same two-plus-three rule as four-card.
        # The extra hole card is another pair to choose from, not a third
        # card in the made hand. Gating PLO5 would leave those rows NULL
        # and every `--game plo5` histogram empty; classifying them as
        # Hold'em would be the other lie.
        return classify_omaha(hole, table)
    return (None, None, None, None)


def migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    for name in COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {name} TEXT")
    con.commit()


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    migrate(con)

    # The same two cards on the same board always classify the same way, and
    # a player sees the same board on three streets, so the work collapses
    # by about two thirds against a cache.
    seen = {}
    rows = []
    for r in con.execute(
            "SELECT hand_id, n, cards, board FROM decisions "
            "WHERE cards IS NOT NULL AND street <> 'preflop'"):
        key = (r["cards"], r["board"])
        if key not in seen:
            seen[key] = classify(r["cards"], r["board"])
        rows.append(seen[key] + (r["hand_id"], r["n"]))

    for name in ("dec_made", "dec_draw", "dec_shown"):
        con.execute(f"DROP INDEX IF EXISTS {name}")
    con.execute("CREATE TEMP TABLE hands_at ("
                + ", ".join(f"{c} TEXT" for c in COLUMNS) + ", hand_id TEXT, n INT)")
    con.executemany("INSERT INTO hands_at VALUES (?,?,?,?,?,?)", rows)
    con.execute("CREATE INDEX temp.hands_at_key ON hands_at(hand_id, n)")
    con.execute(
        "UPDATE decisions SET " + ", ".join(f"{c} = hands_at.{c}" for c in COLUMNS)
        + " FROM hands_at WHERE decisions.hand_id = hands_at.hand_id "
          "AND decisions.n = hands_at.n")
    # `kicker` is in the made-hand index rather than one of its own: it is
    # only ever asked alongside a pair, and on its own it read every row.
    con.execute("CREATE INDEX IF NOT EXISTS dec_made "
                "ON decisions(made, kicker, street)")
    con.execute("CREATE INDEX IF NOT EXISTS dec_draw ON decisions(fd, sd, street)")
    # Partial, because "the cards are known" is a quarter of the table and
    # the index only has to hold that quarter.
    con.execute("CREATE INDEX IF NOT EXISTS dec_shown "
                "ON decisions(cards, street) WHERE cards IS NOT NULL")
    con.execute("ANALYZE")
    con.commit()
    print(f"{len(rows):,} decisions classified, "
          f"{len(seen):,} distinct hand-and-board combinations")
    con.close()
    return len(rows)


def common(db_path=DB):
    con = sqlite3.connect(db_path)
    for col, title in (("made", "what people have"), ("fd", "flush draws"),
                       ("sd", "straight draws")):
        print(f"\n{title}, on the flop:")
        rows = con.execute(
            f"SELECT {col}, COUNT(*) n FROM decisions WHERE street='flop' "
            f"AND made IS NOT NULL GROUP BY {col} ORDER BY n DESC").fetchall()
        total = sum(r[1] for r in rows)
        for name, n in rows:
            print(f"  {str(name or '-'):20} {n:6,}  {100 * n / total:5.1f}%")


# Hands whose answer is not a matter of opinion. Every one of these was
# worked out by hand; if the code and the table disagree, one of them is
# wrong and it is worth finding out which before trusting a report.
KNOWN = [
    ("As Ks", "Ah 7d 2c", "top pair", "top", None, None),
    ("As Ks", "Kh 7d 2c", "top pair", "top", None, None),
    ("Qs Js", "Kh 7d 2c", "high card", None, None, None),
    ("7s 6s", "Kh 7d 2c", "middle pair", "weak", None, None),
    ("2s 3d", "Kh 7d 2c", "weak pair", "weak", None, None),
    ("As Ad", "Kh 7d 2c", "overpair", None, None, None),
    ("5s 5d", "Kh 7d 2c", "middle pair", None, None, None),
    # Two in the hand and one on the board is a set, however the board is
    # paired: what makes it a set rather than trips is that two of the three
    # are hidden.
    ("2s 2d", "Kh 7d 2c", "set", None, None, None),
    ("As Kd", "7h 7d 2c", "board pair", None, None, None),
    ("7s 7d", "7h 2d 3c", "set", None, None, None),
    ("As Ks", "Qs Js 2c", "high card", None, "nut", "gutshot"),
    ("9s 8s", "7s 6d 2c", "high card", None, "backdoor", "oesd"),
    ("9h 8d", "7s 6d 2c", "high card", None, None, "oesd"),
    ("9h 5d", "7s 6d 2c", "high card", None, None, "gutshot"),
    ("Ah Kh", "Qh Jh 2c", "high card", None, "nut", "gutshot"),
    ("As 2s", "Ks Qs 7s", "flush", None, None, None),
    ("Ah Kd", "Qs Js Tc", "straight", None, None, None),
    ("6h 5d", "7s 8d 9c", "straight", None, None, None),
    ("Ah 2d", "3s 4d 5c", "straight", None, None, None),
    ("Ah 3d", "2s 4d Kc", "high card", None, None, "gutshot"),
    ("Kh Kd", "Ks Kc 2h", "quads", None, None, None),
    ("Ks Qs", "Js Ts 9s", "straight flush", None, None, None),
    ("8h 8d", "8s 2d 2c", "boat", None, None, None),
    ("Ah Kh", "Qh Jd 2h", "high card", None, "nut", "gutshot"),
    ("7h 6h", "Ah Kh 2c", "high card", None, "weak", None),
    # Four to a flush on the board with nothing of that suit in hand is the
    # board's flush and not a draw; four to a straight likewise.
    # Three hearts and none of them held: the board's flush, not a draw.
    # The ace is a real wheel gutshot though, and it is the player's.
    ("Ac Kd", "2h 3h 4h", "high card", None, None, "gutshot"),
    ("Ac Kd", "5h 6d 7c 8s", "high card", None, None, None),
    # Nothing draws on the river. This hand is an open-ender on the turn and
    # is nothing at all once the fifth card is out -- a river range came back
    # a third "straight draw" until this was tested.
    ("9h 8d", "7s 6d 2c Kh", "high card", None, None, "oesd"),
    ("9h 8d", "7s 6d 2c Kh 3s", "high card", None, None, None),
]


# Omaha. Every one is a hand where the Hold'em reading of all four
# (or five) cards is a different category, or the sample history
# whose four cards used to stay unnamed. If the code and the table
# disagree, one of them is wrong -- naming these as Hold'em was how
# a `--game plo` histogram would have lied.
KNOWN_OMAHA = [
    # Hold'em of all four is two pair (aces and kings). Omaha must
    # use two hole cards: the aces plus the king-high flop is an
    # overpair, and that is the best 2+3.
    ("As Ad Kh 7d", "Kc 2h 3s", "overpair", None, None, None),
    # Three suited broadway cards plus a junker on JT3. Hold'em is a
    # royal flush. Omaha is ace-high with the nut flush draw and a
    # wrap -- Q, A and 9 each complete a straight for some 2+3.
    ("As Ks Qs 2d", "Js Ts 3c", "high card", None, "nut", "wrap"),
    # One hole heart on a three-heart flop: Hold'em nut flush draw.
    # Omaha cannot make a flush without two hole cards of the suit.
    ("Ah Kd 7c 2s", "5h 9h Qh", "high card", None, None, None),
    # Four hearts on the board, ace of hearts in hand. Hold'em is a
    # flush. Omaha uses three board cards and two hole cards, so the
    # ace of hearts plus a offsuit kicker is four hearts -- ace high.
    # A wheel gutshot is real (2s plus 3h-5h) and is the player's.
    ("Ah Kd 7c 2s", "5h 9h Qh 3h", "high card", None, None, "gutshot"),
    # Two hole hearts and three on the flop is a flush in both games,
    # and must stay one -- the classifier is not "refuse four cards".
    ("Ah Kh 7c 2s", "2h 5h 9h", "flush", None, None, None),
    # Set: two hole aces plus one on the board.
    ("Ah Ad 7c 2s", "As Kh 3d", "set", None, None, None),
    # Pocket deuces plus a deuce on the board -- still a set.
    ("2h 2d 3c 4s", "2s Kh 7d", "set", None, None, None),
    # Wheel with A2 and 345. Both hole cards sit in the straight.
    ("Ah 2d 8c 9s", "3h 4d 5c", "straight", None, None, None),
    # The imported ACR sample. Hold'em of all four is two pair
    # (kings and deuces). Omaha's best 2+3 is kings and eights.
    # Same category, different cards -- unnamed used to be the
    # honest answer; two pair is the true one.
    ("3c 2h Kd 2d", "8h Ks 8c", "two pair", None, None, None),
    # 9876 on 54x. Three or more ranks complete a 2+3 straight
    # (3, 6, 8, ...). 9876 on a disconnected flop is not a wrap --
    # the connectors are in the hole and two more board cards would
    # have to come, which is a backdoor we do not name.
    ("9h 8h 7d 6c", "5s 4c 2d", "high card", None, None, "wrap"),
    # PLO5, same two-plus-three rule. Two queens over T34 is an
    # overpair; Qh Jh is a backdoor flush, not a made hand. The
    # fifth card is another pair to choose from, not a third hole
    # card in the five.
    ("Qh Jh Qd Kc 2c", "Th 3d 4s", "overpair", None, "backdoor", None),
]


def check(db_path=DB):
    """
    Against hands whose answer was worked out by hand, and against showdowns.

    The table below is the real check: a classifier is a pile of special
    cases and every one of them looks right until the hand it gets wrong
    turns up. The showdown test is the second, independent one -- these
    labels were derived without ever looking at who won, so if they mean
    anything then a better label has to win more often when the money goes
    in. Nothing about the code enforces that.
    """
    fails = []
    wrong = 0
    for cards, board, made, kicker, fd, sd in KNOWN:
        got = classify(cards, board)
        want = (made, kicker, fd, sd)
        if got != want:
            wrong += 1
            print(f"    {cards} on {board}: wanted {want}, got {got}")
    print(f"hands worked out by hand     {len(KNOWN) - wrong}/{len(KNOWN)}")
    if wrong:
        fails.append(f"{wrong} known hands classified wrongly")

    omaha_wrong = 0
    for cards, board, made, kicker, fd, sd in KNOWN_OMAHA:
        got = classify(cards, board)
        want = (made, kicker, fd, sd)
        if got != want:
            omaha_wrong += 1
            print(f"    {cards} on {board}: wanted {want}, got {got}")
    print(f"Omaha hands worked out       "
          f"{len(KNOWN_OMAHA) - omaha_wrong}/{len(KNOWN_OMAHA)}")
    if omaha_wrong:
        fails.append(f"{omaha_wrong} known Omaha hands classified wrongly")
    # The royal-flush lie: three suited broadway cards on JT is
    # Hold'em's straight flush and Omaha's ace-high. If this ever
    # comes back a flush, the 2+3 rule has been lost.
    lie = classify("As Ks Qs 2d", "Js Ts 3c")
    if lie[0] in ("straight flush", "flush", "straight"):
        fails.append(f"Omaha royal-looking hand was named {lie[0]}")

    # One hole heart on a three-heart flop is the trap: Hold'em
    # flush-draw, Omaha nothing. best_omaha_hand must walk 2+3.
    trap_cards, trap_board = "Ah Kd 7c 2s", "5h 9h Qh"
    trap = classify(trap_cards, trap_board)
    if trap[0] != "high card" or trap[2] is not None:
        fails.append(f"1-heart trap was {trap}, not ace-high without an FD")
    picked = best_omaha_hand(parse(trap_cards), parse(trap_board))
    if picked is None or picked[0][0] != 0:
        fails.append("best_omaha_hand used more than two hole cards "
                     "on the 1-heart flop")
    if len(picked[1]) != 2 or len(picked[2]) != 3:
        fails.append("best_omaha_hand did not return a 2+3")

    # Histogram groups do not need a corpus. They have to pass on a
    # machine that has not imported yet, the same way query.py's
    # fixture checks do -- otherwise --check invents a failure that
    # is really "no hands.db".
    hist_ok = len(fails)
    grouped = 0
    for cards, board, made, kicker, fd, sd in KNOWN:
        got = group_of(made, fd, sd)
        if got is None:
            fails.append(f"{cards} on {board} was shown and grouped None")
            continue
        grouped += 1
        drawing = bool(fd or sd)
        if made in ("high card", "board pair"):
            want = "draws" if drawing else "air"
            if got != want:
                fails.append(f"{cards} on {board}: group {got}, want {want}")
        elif made in ("weak pair", "under pair") and got != "weak_pair":
            fails.append(f"{cards} on {board}: group {got}, want weak_pair")
    omaha_grouped = 0
    want_omaha = {
        ("As Ad Kh 7d", "Kc 2h 3s"): "medium",
        ("As Ks Qs 2d", "Js Ts 3c"): "combo",
        ("Ah Kd 7c 2s", "5h 9h Qh"): "air",
        ("Ah Kd 7c 2s", "5h 9h Qh 3h"): "wrap",
        ("Ah Kh 7c 2s", "2h 5h 9h"): "strong",
        ("Ah Ad 7c 2s", "As Kh 3d"): "strong",
        ("9h 8h 7d 6c", "5s 4c 2d"): "wrap",
        ("Qh Jh Qd Kc 2c", "Th 3d 4s"): "medium",
    }
    for cards, board, made, kicker, fd, sd in KNOWN_OMAHA:
        got = group_of(made, fd, sd, OMAHA_HIST_SPEC)
        if got is None:
            fails.append(f"Omaha {cards} on {board} grouped None")
            continue
        omaha_grouped += 1
        want = want_omaha.get((cards, board))
        if want and got != want:
            fails.append(f"Omaha {cards} on {board}: group {got}, want {want}")
    print(f"known hands grouped          {grouped}/{len(KNOWN)}")
    print(f"Omaha hands grouped          {omaha_grouped}/{len(KNOWN_OMAHA)}")
    if group_of(None) is not None:
        fails.append("ungrouped cards were not None")
    if group_of("mystery pair") != HIST_OTHER:
        fails.append("an unknown made label did not fall into Other")
    weak_made = set()
    for key, _lab, is_weak, names, _draw in HIST_SPEC:
        if is_weak:
            weak_made.update(names)
    if weak_made != set(WEAK):
        fails.append(f"default hist weak made {sorted(weak_made)}, "
                     f"not WEAK {list(WEAK)}")
    if group_of("top pair", "nut", "oesd") != "top_pair":
        fails.append("top pair plus a draw left its pair bar")
    if group_of("top pair", None, None, OMAHA_HIST_SPEC) != "weak_made":
        fails.append("PLO top pair did not sit on Weak made")
    if group_of("top pair", "nut", "oesd", OMAHA_HIST_SPEC) != "weak_made":
        fails.append("PLO top pair plus a draw left Weak made")
    if "top_pair" in {k for k, *_ in OMAHA_HIST_SPEC}:
        fails.append("Omaha hist still has a Hold'em top_pair bar")
    print(f"hist groups / Other / weak   "
          f"{'yes' if len(fails) == hist_ok else 'NO'}")

    db = Path(db_path)
    if not db.exists() or db.stat().st_size == 0:
        print()
        print("FAIL: " + "; ".join(fails) if fails else
              "PASS (no hands.db -- known hands and groups only)")
        return not fails
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "decisions" not in tables:
        con.close()
        print()
        print("FAIL: " + "; ".join(fails) if fails else
              "PASS (no decisions -- known hands and groups only)")
        return not fails
    n = con.execute("SELECT COUNT(*) FROM decisions "
                    "WHERE made IS NOT NULL").fetchone()[0]
    # Hold'em, PLO4 and PLO5. Three cards, or a game this classifier
    # does not know, stay unnamed -- NULL means "not this game", not
    # "high card".
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    game_sql = ("AND game IN ('HOLDEM', 'OMAHA', 'OMAHA5')"
                if "game" in cols else "")
    known = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE cards IS NOT NULL "
        f"AND street <> 'preflop' {game_sql}").fetchone()[0]
    print(f"decisions with a hand named  {n:,}/{known:,}")
    if n != known:
        fails.append("some postflop decisions with known cards were not named")
    if not n:
        print("\nFAIL: nothing classified -- run `python strength.py`")
        return False

    # Never on a preflop row. A hand becomes something when the flop comes;
    # stamping it earlier is the mistake flop texture already made once.
    early = con.execute("SELECT COUNT(*) FROM decisions WHERE street='preflop' "
                        "AND made IS NOT NULL").fetchone()[0]
    print(f"nothing named before the flop  "
          f"{'yes' if not early else f'NO -- {early:,} rows'}")
    if early:
        fails.append("preflop rows have a made hand")

    # A stronger hand must win more at showdown. `wsd` lives in spots and was
    # derived from who took the pot, which these labels never saw.
    order = ["high card", "board pair", "weak pair", "under pair",
             "middle pair", "top pair", "overpair", "two pair", "trips",
             "set", "straight", "flush", "boat", "quads"]
    rates = {}
    for r in con.execute("""
            SELECT d.made, COUNT(*) n, AVG(s.wsd) won
            FROM decisions d JOIN spots s USING (hand_id, seat)
            WHERE d.street='river' AND d.made IS NOT NULL AND s.wtsd = 1
            GROUP BY d.made"""):
        if r["n"] >= 30:
            rates[r["made"]] = (r["won"], r["n"])
    ladder = [(m, rates[m]) for m in order if m in rates]
    print("won at showdown, by what they held (river, 30+ each):")
    for name, (won, cnt) in ladder:
        print(f"  {name:14} {100 * won:5.1f}%  n={cnt:,}")
    inversions = sum(1 for i in range(len(ladder) - 1)
                     if ladder[i][1][0] > ladder[i + 1][1][0] + 0.05)
    print(f"stronger hands win more      "
          f"{len(ladder) - 1 - inversions}/{max(len(ladder) - 1, 1)} steps rise")
    if inversions > 1:
        fails.append(f"{inversions} places where a better hand won less often")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    if "--common" in argv:
        common()
        return 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
