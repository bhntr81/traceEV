"""
The path a hand took, so a filter can ask about the shape of the betting.

Every filter here so far asks about *one decision*: the street, the pot
type, who raised last, what the flop looks like. None of them can ask about
the *sequence* -- "the flop went check, bet, call and the turn checked
through" -- because a sequence is not a property of a row. That is the one
thing a tracker like Hand2Note does that this could not, and it is the
shape most real questions actually have.

H2N answers it with a directed graph of action nodes and an index built at
import time from each node to the hands that reached it. The same query
surface falls out of a much smaller idea: write each street's actions in
order as a short string, and a node is a **prefix** of one. "Every hand that
reached raise-call and then a flop check" is `node GLOB 'RC/X*'`, which
SQLite answers from a B-tree without scanning -- the same complexity as the
pre-computed offsets, for a table instead of a graph engine. Nothing has to
be held in memory, and there is no second copy of the data in a format only
this module understands.

Two strings per street, because size matters and does not always:

    flop     XBC        what was done
    flop_sz  XBmC       the same, with the bet bucketed s m l p o

and two per decision:

    line     FFRC/XBC/XX     the whole hand, streets separated by /
    node     FFRC/X          everything that had already happened when it
                             was this player's turn -- where they stood

Sizes are buckets and not percentages because a filter written against an
exact percentage matches almost nothing: half-pot bets land on 49% and 51%
as often as they land on 50%. The boundaries sit *between* the sizes people
use, so a bet anybody actually makes falls in the middle of a bucket rather
than on its edge.

Positions are deliberately not in these strings. An eight-handed table is
recorded against six position names, so UTG, UTG+1 and UTG+5 all arrive as
"UTG" -- 1,076 hands have one position label covering two seats, and a
string keyed on position would silently merge them. The order of action is
unambiguous; who sat where is already a column.

Derived from `decisions`, so run it after that table is rebuilt or the
columns are empty and every line filter matches nothing.

    python lines.py            derive them onto `decisions`
    python lines.py --check    the strings agree with the columns already there
    python lines.py --common flop    the lines that actually occur
"""

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "hands.db"

# The order streets are played in, which is also the order they are joined
# in. Which streets a hand has is taken from the hand rather than assumed:
# writing an empty segment for a street that was never dealt would make "the
# flop checked through" and "there was no flop" into the same string.
STREETS = ("preflop", "flop", "turn", "river")
COLUMN = {"preflop": "pre", "flop": "flop", "turn": "turn", "river": "river"}

# Where one bet size stops and the next begins, as a fraction of the pot in
# front of the player. The edges are placed between the standard sizes -- a
# third, a half, two-thirds and three-quarters, pot -- so that a bet
# somebody actually makes lands in the middle of a bucket.
SIZES = ((0.40, "s"), (0.60, "m"), (0.90, "l"), (1.20, "p"))
OVERBET = "o"

# The verbs a hand history uses, and the letters a size bucket uses. They
# share no character, which is what makes a typed pattern safe to normalise:
# "xbmc" and "XBMC" can both become "XBmC" without having to guess.
VERBS = "FXCBRA"
BUCKETS = "smlpo"

# The columns worth a B-tree. `node_sz` and `sized` are not among them:
# every pattern anybody writes against those starts with a wildcard, so
# there is no prefix to seek on and the index would be built and never read.
# The columns worth a B-tree. `sized` and `node_sz` are not among them: a
# pattern against the whole hand's line effectively always begins with a
# wildcard, so there is no prefix to seek on and the index would be built
# and never read. The per-street sized columns are a different matter and
# were left out twice by that same reasoning, wrongly -- `--flop XBmC` has
# no wildcard in it at all, and without an index it reads every row.
INDEXED = ("line", "node",
           "pre", "flop", "turn", "river",
           "pre_sz", "flop_sz", "turn_sz", "river_sz",
           "own", "own_node")

# `own` is one player's actions and nobody else's, per street: the shape
# a player talks about -- "check-call, check-call, check-fold" is
# XC/XC/XF, a triple barrel is B/B/B after the preflop -- and the shape
# Hand2Note's popups are written in. `own_node` is the same cut short of
# this decision, so "checked the flop and now facing a bet" is a node that
# ends in X. The whole-table strings above cannot say either, because
# they interleave every seat's actions.
LINE_COLUMNS = ("pre", "flop", "turn", "river",
                "pre_sz", "flop_sz", "turn_sz", "river_sz",
                "line", "sized", "node", "node_sz", "own", "own_node")


def letter(action, agg):
    """
    The verb, corrected for the one that means two different things.

    "A" is written for an all-in, and an all-in is sometimes a raise and
    sometimes a call for the last of a stack -- 95 of the 236 here are
    calls. Counting them all as raises put 31 preflop decisions in a
    different pot type from the one `decisions` had already worked out, and
    would have made "he shoved over my bet" match hands where he called.
    An all-in call is a call; that it was for everything is in `allin`.
    """
    if action == "A" and not agg:
        return "C"
    return action


def bucket(frac):
    """The size letter for a bet, and nothing at all for a fold or a check."""
    if frac is None:
        return ""
    for edge, name in SIZES:
        if frac <= edge:
            return name
    return OVERBET


def normalise(pattern):
    """
    A typed pattern, in the case the columns are actually stored in.

    Nobody holds the shift key for half a filter. Verbs and size buckets use
    disjoint letters precisely so this can be done without ambiguity, and
    without it `--flop xbc` matches nothing and reads as a broken filter
    rather than as a typo.
    """
    out = []
    # A dash between streets is how lines are written by hand and in
    # Hand2Note's popups -- "XC-XC-XF" -- and it means the slash here.
    for ch in pattern.replace("-", "/").replace(" ", ""):
        if ch.upper() in VERBS:
            out.append(ch.upper())
        elif ch.lower() in BUCKETS:
            out.append(ch.lower())
        else:
            out.append(ch)          # / * ? [ ] pass through to GLOB
    return "".join(out)


def migrate(con):
    """Add the columns if they are not there, and leave any that are."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    for name in LINE_COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {name} TEXT")
    con.commit()


def strings_for(acts):
    """
    Every string this module produces, for one hand, in one pass.

    The node is the line cut short, so it is built from the same walk. Two
    walks would be two answers to one question, and they would drift apart
    the first time either was changed.
    """
    plain, sized, order = {}, {}, []
    for a in acts:
        st = a["street"]
        if st not in plain:
            order.append(st)
            plain[st] = sized[st] = ""
        v = letter(a["action"], a["agg"])
        plain[st] += v
        sized[st] += v + bucket(a["pot_frac"])

    # The same actions again, stopping short of each one: a node is what the
    # player could see, so their own action is not part of it.
    nodes = []
    done, done_sz, cur, acc, acc_sz = [], [], None, "", ""
    for a in acts:
        if a["street"] != cur:
            if cur is not None:
                done.append(acc)
                done_sz.append(acc_sz)
            cur, acc, acc_sz = a["street"], "", ""
        nodes.append(("/".join(done + [acc]), "/".join(done_sz + [acc_sz])))
        v = letter(a["action"], a["agg"])
        acc += v
        acc_sz += v + bucket(a["pot_frac"])

    # And each player's own walk. A street they took no action on is left
    # out of their line rather than written empty, so a player who folded
    # preflop has a one-segment line and never matches a flop pattern.
    own_by, own_nodes = {}, []
    for a in acts:
        mine = own_by.setdefault(a["seat"], [])
        if not mine or mine[-1][0] != a["street"]:
            mine.append([a["street"], ""])
        own_nodes.append("/".join(seg for _st, seg in mine))
        mine[-1][1] += letter(a["action"], a["agg"])
    own = {seat: "/".join(seg for _st, seg in segs) for seat, segs in own_by.items()}
    own_rows = [(own[a["seat"]], node) for a, node in zip(acts, own_nodes)]

    return plain, sized, order, nodes, own_rows


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    migrate(con)

    acts_by = {}
    for r in con.execute("SELECT hand_id, n, seat, street, action, agg, pot_frac "
                         "FROM decisions ORDER BY hand_id, n"):
        acts_by.setdefault(r["hand_id"], []).append(r)

    rows = []
    for hid, acts in acts_by.items():
        plain, sized, order, nodes, own_rows = strings_for(acts)
        line = "/".join(plain[s] for s in order)
        line_sz = "/".join(sized[s] for s in order)
        street_cols = (tuple(plain.get(s, "") for s in STREETS)
                       + tuple(sized.get(s, "") for s in STREETS))
        for a, (node, node_sz), (own, own_node) in zip(acts, nodes, own_rows):
            rows.append(street_cols + (line, line_sz, node, node_sz,
                                       own, own_node, hid, a["n"]))

    # Ninety thousand separate UPDATEs against an eighty-megabyte database is
    # a minute of work for something that should take a second. Staged in a
    # temporary table and joined instead, which SQLite does in one pass.
    # Dropped before the update and rebuilt after it. Maintaining six
    # B-trees while rewriting every row took the rebuild from 8 seconds to
    # 42; building them once at the end costs three.
    for col in INDEXED:
        con.execute(f"DROP INDEX IF EXISTS dec_{col}")

    con.execute("CREATE TEMP TABLE staged (" +
                ", ".join(f"{c} TEXT" for c in LINE_COLUMNS) +
                ", hand_id TEXT, n INT)")
    con.executemany(
        "INSERT INTO staged VALUES ("
        + ",".join("?" * (len(LINE_COLUMNS) + 2)) + ")", rows)
    con.execute("CREATE INDEX temp.staged_key ON staged(hand_id, n)")
    con.execute(
        "UPDATE decisions SET "
        + ", ".join(f"{c} = staged.{c}" for c in LINE_COLUMNS)
        + " FROM staged WHERE decisions.hand_id = staged.hand_id "
          "AND decisions.n = staged.n")

    # The whole argument for strings rather than a graph is that a prefix
    # match is an index seek. Without these it is a scan of every row, which
    # works, and is slow in a way nobody would connect to a missing index --
    # which is exactly what happened: the four per-street columns shipped
    # with filters and without indexes, and `--flop XBC` scanned all 94,017
    # rows in 197ms where it now seeks in 3.6ms.
    #
    # `node_sz` and `sized` are deliberately not indexed. Every pattern
    # anybody writes against them starts with a wildcard, so there is no
    # prefix to seek on and the index would be built and never read.
    for col in INDEXED:
        con.execute(f"CREATE INDEX IF NOT EXISTS dec_{col} ON decisions({col})")
    con.execute("ANALYZE")
    con.commit()
    print(f"{len(acts_by):,} hands, {len(rows):,} decisions")
    return len(rows)


def common(db_path=DB, street="flop", limit=15, where="1=1"):
    """The lines that actually occur, commonest first."""
    con = sqlite3.connect(db_path)
    col = COLUMN[street]
    distinct = con.execute(
        f"SELECT COUNT(DISTINCT {col}) FROM decisions "
        f"WHERE {col} <> ''").fetchone()[0]
    rows = con.execute(
        f"SELECT {col}, COUNT(DISTINCT hand_id) n FROM decisions "
        f"WHERE ({where}) AND {col} <> '' GROUP BY {col} "
        f"ORDER BY n DESC LIMIT {int(limit)}").fetchall()
    hands = con.execute(
        f"SELECT COUNT(DISTINCT hand_id) FROM decisions "
        f"WHERE ({where}) AND {col} <> ''").fetchone()[0]
    print(f"{distinct} different {street} lines over {hands:,} hands\n")
    for text, n in rows:
        print(f"  {text:<18} {n:6,}  {100 * n / max(1, hands):5.1f}%")


def check(db_path=DB):
    """
    The strings say what the columns already say.

    Each of these compares the new derivation against a column derived a
    different way from the same hands, which is the only kind of check worth
    having: a sequence written down wrongly is still a perfectly well-formed
    string, and there is nothing about it that looks wrong on inspection.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fails = []

    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    filled = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE line IS NOT NULL").fetchone()[0]
    print(f"every decision has a line         {filled:,}/{total:,}"
          f"{'' if filled == total else '   <-- rows missed'}")
    if filled != total:
        fails.append("coverage")

    # 1. The node is the line, cut short. If these disagree the two walks
    #    over the actions have drifted apart, which is the failure this
    #    module is most likely to have.
    bad = con.execute(
        "SELECT COUNT(*) FROM decisions "
        "WHERE SUBSTR(line, 1, LENGTH(node)) <> node").fetchone()[0]
    print(f"the node is a prefix of the line  {total - bad:,}/{total:,}"
          f"{'' if not bad else '   <-- DISAGREE'}")
    if bad:
        fails.append("a node is not a prefix of its own line")

    # 2. `facing` was derived in decisions.py from the pot and not from any
    #    string. Postflop, facing nothing means no bet in front of you,
    #    which means this street's part of the node holds no B, R or A.
    rows = con.execute(
        "SELECT facing, node FROM decisions WHERE street <> 'preflop'"
    ).fetchall()
    disagree = 0
    for r in rows:
        seg = (r["node"] or "").rsplit("/", 1)[-1]
        quiet = not any(c in seg for c in "BRA")
        if quiet != (r["facing"] == "check"):
            disagree += 1
    print(f"facing agrees with the node       {len(rows) - disagree:,}/"
          f"{len(rows):,}{'' if not disagree else '   <-- DISAGREE'}")
    if disagree:
        fails.append("facing and the node describe different situations")

    # 3. The pot type counts raises. It came from the betting, and the
    #    preflop string is the betting, so they must count the same.
    # Against the node, not against `pre`: a pot type describes the state a
    # decision was made in, so it counts the raises that had already
    # happened, not the ones that came later in the same street.
    rows = con.execute(
        "SELECT pot_type, node, COUNT(*) n FROM decisions "
        "WHERE street = 'preflop' AND pot_type IS NOT NULL "
        "GROUP BY pot_type, node").fetchall()
    want = {"unopened": 0, "limped": 0, "raised": 1, "3bet": 2, "4bet": 3,
            "5bet+": 4}
    off = ok = 0
    for r in rows:
        raises = sum((r["node"] or "").count(c) for c in "RA")
        floor = want.get(r["pot_type"])
        # 5bet+ is the top of the ladder and swallows everything above it.
        good = raises >= floor if r["pot_type"] == "5bet+" else raises == floor
        if good:
            ok += r["n"]
        else:
            off += r["n"]
    print(f"pot type counts the raises        {ok:,}/{ok + off:,}"
          f"{'' if not off else '   <-- DISAGREE'}")
    if off:
        fails.append("pot_type and the preflop line count raises differently")

    # 4. A prefix match really is an index seek. The whole argument for
    #    strings instead of a graph is that it is, so it is checked rather
    #    than believed.
    plan = " ".join(str(r[3]) for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM decisions "
        "WHERE node GLOB 'RC/X*'"))
    up = plan.upper()
    seek = "INDEX" in up and "SEARCH" in up
    print(f"a node lookup uses the index      "
          f"{'yes' if seek else 'NO -- it scans every row'}")
    if not seek:
        fails.append("node lookups scan the whole table")

    # 5. Normalising must fix a lowercase pattern and leave a right one be.
    for text in ("XBC", "RC/XBmC", "*B*", "XB?C"):
        if normalise(text) != text:
            fails.append(f"normalise({text!r}) changed it to "
                         f"{normalise(text)!r}")
    print(f"patterns normalise                "
          f"{'lowercase reaches the same rows' if normalise('xbmc') == 'XBmC' else 'BROKEN'}")
    if normalise("xbmc") != "XBmC":
        fails.append("normalise does not fix a lowercase pattern")

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
        i = argv.index("--common")
        common(street=argv[i + 1] if i + 1 < len(argv) else "flop")
        return 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
