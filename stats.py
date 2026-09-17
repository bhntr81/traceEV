"""
Every statistic, defined once, askable of anybody.

A tracker's stat is not code. It is two questions about a situation: how
often did this come up, and how often did they do it. "Fold to cbet" is
`street='flop' AND facing='bet' AND prev_agg=0` over `action='F'`, and
"delayed cbet" is a different pair of the same shape. Once that is true,
adding a stat is adding a line to a list, and every stat automatically
works for one player, for a pool, for a position, at a stack depth, on a
monotone flop -- because the filter is separate from the definition.

That is the whole design. `decisions` supplies the situations; this supplies
the vocabulary; everything above -- player reports, pool reports, a HUD --
is a choice of which stats and which filter, and writes no SQL of its own.

Three rules are built in rather than left to whoever reads the output:

  * **A rate always carries its n.** Not as decoration -- 3-bet on 12
    chances is not a 3-bet number, and the output has to make that obvious
    rather than leave it to be noticed.
  * **A rate always carries an interval.** A Wilson interval, not the
    textbook one, because at the sample sizes that matter here the textbook
    one puts the bounds outside 0-100 and quietly lies about small counts.
  * **Two rates are only different if their intervals do not overlap.**
    `compare` enforces it; the reports use `compare`.

    python stats.py --list                    every stat, with its definition
    python stats.py --custom                  the ones saved from a filter
    python stats.py --pool                    both pools side by side
    python stats.py --player NAME             one opponent
    python stats.py --check                   agree with spots, PASS or FAIL
"""

import json
import re
import sqlite3
import sys
import tempfile
from math import erfc, sqrt
from pathlib import Path

import sites

DB = Path(__file__).parent / "hands.db"


class Stat:
    """
    One statistic: the chance to do a thing, and the doing of it.

    `source` is which table the situation lives in -- "d" for decisions,
    which is nearly everything, and "s" for spots, which holds the handful
    of facts that are properties of a whole hand rather than of a decision
    (seeing a flop, reaching showdown, money won). Mixing them in one
    registry is deliberate: the caller should not have to know which kind a
    stat is in order to ask for it.

    `per` is what the denominator counts. "decision" counts rows -- a player
    who faces three bets on three streets had three chances to fold. "hand"
    counts players-in-hands, because a player VPIPs once however many times
    they act.

    `custom` says the definition came out of `stats.json` rather than out
    of the registry below, which is the one thing the rest of the module
    needs to know about a saved stat: `load_custom` replaces exactly those
    and leaves the shipped ones alone.
    """

    def __init__(self, key, label, chance, action, source="d", per="decision",
                 group="", note="", custom=False):
        self.key, self.label = key, label
        self.chance, self.action = chance, action
        self.source, self.per, self.group, self.note = source, per, group, note
        self.custom = custom


# The registry. Everything below is a definition, not code -- which is the
# point. Anything expressible as two filters over a situation belongs here
# rather than in a module of its own.
#
# Preflop "facing" runs unopened / open / 3bet / 4bet, counting raises, and a
# limp is not a raise -- so an unopened pot with limpers in it is still
# "unopened", and the difference is told by the size of the pot instead.
LIMPED = "pot_bb > 1.6"
UNLIMPED = "pot_bb <= 1.6"

STATS = [
    # ---- preflop, the shape of somebody's game -------------------------
    Stat("vpip", "VPIP", "street='preflop'", "agg=1 OR action IN ('C','A')",
         per="hand", group="preflop",
         note="put money in voluntarily, once per hand"),
    Stat("pfr", "PFR", "street='preflop'", "agg=1",
         per="hand", group="preflop", note="raised preflop, once per hand"),
    Stat("rfi", "RFI", f"street='preflop' AND facing='unopened' AND {UNLIMPED}",
         "agg=1", group="preflop",
         note="first in, nobody yet in the pot -- not counting limped pots"),
    Stat("limp", "limp", f"street='preflop' AND facing='unopened' AND {UNLIMPED}",
         "action='C'", group="preflop"),
    Stat("iso", "iso-raise", f"street='preflop' AND facing='unopened' AND {LIMPED}",
         "agg=1", group="preflop", note="raising over limpers"),
    Stat("threebet", "3bet", "street='preflop' AND facing='open'", "agg=1",
         group="preflop"),
    Stat("coldcall", "cold call",
         "street='preflop' AND facing='open' AND acted_before=0 "
         "AND position NOT IN ('SB','BB')",
         "action IN ('C','A')", group="preflop",
         note="calling a raise cold -- first action, and not from a blind"),
    Stat("call_open", "call vs open", "street='preflop' AND facing='open'",
         "action IN ('C','A')", group="preflop",
         note="calling a raise from any seat, blinds included -- the pool "
              "leak map's line, which `coldcall` is too strict for"),
    Stat("squeeze", "squeeze",
         "street='preflop' AND facing='open' AND n_live>=4 AND pot_bb>4",
         "agg=1", group="preflop",
         note="3betting with a caller already in"),
    Stat("fold_to_3bet", "fold to 3bet",
         "street='preflop' AND facing='3bet' AND was_agg=1", "action='F'",
         group="preflop", note="as the original raiser, not as a cold seat"),
    Stat("fourbet", "4bet", "street='preflop' AND facing='3bet' AND was_agg=1",
         "agg=1", group="preflop"),
    Stat("fold_to_4bet", "fold to 4bet", "street='preflop' AND facing='4bet'",
         "action='F'", group="preflop"),
    Stat("steal", "steal",
         f"street='preflop' AND facing='unopened' AND {UNLIMPED} "
         "AND position IN ('CO','BTN','SB')", "agg=1", group="preflop"),
    Stat("fold_to_steal", "fold to steal",
         "street='preflop' AND facing='open' AND position IN ('SB','BB') "
         "AND pot_bb <= 5", "action='F'", group="preflop"),
    Stat("bb_defend", "BB defend",
         "street='preflop' AND facing='open' AND position='BB'",
         "action<>'F'", group="preflop",
         note="calling or raising rather than giving up the blind"),

    # ---- the flop ------------------------------------------------------
    Stat("cbet_flop", "cbet flop",
         "street='flop' AND is_pfa=1 AND facing='check' AND was_agg=0",
         "agg=1", group="flop"),
    Stat("fold_to_cbet", "fold to cbet",
         "street='flop' AND is_pfa=0 AND facing='bet' AND vs_pfa=1",
         "action='F'", group="flop",
         note="folding to the PREFLOP RAISER's bet, not to anyone's bet"),
    Stat("raise_cbet", "raise cbet",
         "street='flop' AND is_pfa=0 AND facing='bet' AND vs_pfa=1", "agg=1",
         group="flop"),
    Stat("fold_to_donk", "fold to donk",
         "street='flop' AND is_pfa=1 AND facing='bet' AND vs_pfa=0",
         "action='F'", group="flop",
         note="the preflop raiser, bet into before they could continue"),
    Stat("donk_flop", "donk flop",
         "street='flop' AND is_pfa=0 AND first_in=1 AND facing='check' "
         "AND pot_type<>'limped'",
         "agg=1", group="flop",
         note="betting into the player who raised preflop -- so a limped "
              "pot is not a chance to do it, there being no raiser to donk "
              "into, and 903 of 3,782 chances used to be limped pots"),
    Stat("checkraise_flop", "check-raise flop",
         "street='flop' AND facing='bet' AND first_in=0 AND is_ip=0",
         "agg=1", group="flop"),
    Stat("flop_agg", "flop aggression", "street='flop'", "agg=1", group="flop"),

    # ---- turn and river, where spots could say nothing at all ----------
    Stat("cbet_turn", "cbet turn",
         "street='turn' AND prev_agg=1 AND facing='check'", "agg=1",
         group="turn", note="barrelling, having bet the flop"),
    Stat("delayed_cbet", "delayed cbet",
         "street='turn' AND is_pfa=1 AND checked_to=1 AND facing='check'",
         "agg=1", group="turn",
         note="the preflop raiser betting a turn after checking the flop"),
    # The barrels by the names people use, exact rather than by whoever was
    # last aggressive: the raiser's own line says they bet the flop, and
    # then the turn. `cbet_turn` beside them is the older, looser measure.
    Stat("double_barrel", "double barrel",
         "street='turn' AND is_pfa=1 AND facing='check' AND own_node GLOB '*/B/'",
         "agg=1", group="turn",
         note="the raiser bet the flop and bets the turn"),
    Stat("fold_to_double_barrel", "fold to double barrel",
         "street='turn' AND facing='bet' AND vs_pfa=1 AND own_node GLOB '*/*C/*'",
         "action='F'", group="turn",
         note="called the flop bet, faces the turn bet"),
    Stat("probe_turn", "probe turn",
         "street='turn' AND is_pfa=0 AND checked_to=1 AND first_in=1 "
         "AND facing='check' AND pot_type<>'limped'", "agg=1", group="turn",
         note="betting a turn the preflop raiser gave up on -- which needs "
              "there to have been a preflop raiser"),
    Stat("float_turn", "float turn",
         "street='turn' AND prev_agg=0 AND is_ip=1 AND facing='check'",
         "agg=1", group="turn",
         note="taking the turn having only called the flop"),
    Stat("fold_to_turn_bet", "fold to turn bet",
         "street='turn' AND facing='bet'", "action='F'", group="turn"),
    Stat("cbet_river", "cbet river",
         "street='river' AND prev_agg=1 AND facing='check'", "agg=1",
         group="river"),
    Stat("triple_barrel", "triple barrel",
         "street='river' AND is_pfa=1 AND facing='check' AND own_node GLOB '*/B/B/'",
         "agg=1", group="river",
         note="the raiser bet the flop and the turn and bets the river"),
    Stat("fold_to_triple_barrel", "fold to triple barrel",
         "street='river' AND facing='bet' AND vs_pfa=1 AND own_node GLOB '*/*C/*C/*'",
         "action='F'", group="river",
         note="called the flop and the turn, faces the river bet"),
    Stat("delayed_river", "delayed river bet",
         "street='river' AND is_pfa=1 AND facing='check' AND own_node GLOB '*/*/X/'",
         "agg=1", group="river",
         note="the raiser checked the turn and bets the river"),
    Stat("fold_to_river_bet", "fold to river bet",
         "street='river' AND facing='bet'", "action='F'", group="river"),
    Stat("river_agg", "river aggression", "street='river'", "agg=1",
         group="river"),

    # ---- sizing and stack depth ----------------------------------------
    Stat("overbet", "overbets",
         "street<>'preflop' AND agg=1", "pot_frac > 1.0", group="sizing",
         note="of their bets and raises, how many exceed the pot"),
    Stat("small_bet", "bets a third or less",
         "street<>'preflop' AND agg=1 AND to_call=0", "pot_frac <= 0.34",
         group="sizing"),
    Stat("faces_overbet", "faced an overbet",
         "street<>'preflop' AND to_call>0", "to_call > pot_before - to_call",
         group="sizing"),

    # ---- hand-level facts, which live in spots --------------------------
    Stat("wtsd", "WTSD", "saw_flop", "wtsd", source="s", group="showdown",
         note="of flops seen, how often a showdown was reached"),
    Stat("wsd", "W$SD", "wtsd", "wsd", source="s", group="showdown"),
    Stat("wwsf", "WWSF", "saw_flop", "wwsf", source="s", group="showdown",
         note="won money when seeing the flop"),
]

BY_KEY = {s.key: s for s in STATS}


# ---- stats nobody had to edit this file to get -------------------------
#
# The registry above is the vocabulary this project ships with, and it is
# not the vocabulary any particular game needs. The question that actually
# comes up is "how often do I bet the river in a 3-bet pot in position,
# having been checked to and barrelled twice" -- and there is no list of
# thirty stats that contains it. There is no list of any length that
# contains it, because the number of shapes a hand can have is the number
# of strings the action letters spell.
#
# Hand2Note answers that with a stat builder: click the actions on each
# street, name the result, and it becomes a column like any other. The same
# thing falls out of what is already here without building a builder,
# because a stat IS a filter and an action. `query.build` already turns
#
#     --pot 3bet --ip --headsup --street river --node '*/XBC/XBC/X'
#
# into the chance, and the action is one of the verbs below. So a saved stat
# is that pair written to a file and loaded back into this registry, after
# which nothing above it can tell the difference: it appears in the stats
# table, in `--show`, in `--by position`, in the opponent leaderboard and as
# a one-click filter, and not one of those had to learn it exists.
#
# The file sits beside the database rather than in the code because it is
# the user's and not the project's -- a stat somebody built has to survive a
# `git pull`, and must not turn up in anybody else's checkout.
CUSTOM = Path(__file__).parent / "stats.json"
CUSTOM_GROUP = "custom"

# A key is looked up by name in three places -- `--show a,b,c`, a report's
# columns, `--quick` -- and one of them splits on commas. So the characters
# a key may contain are the ones that survive all three.
KEY_OK = re.compile(r"^[a-z][a-z0-9_]*$")

# What a numerator can be. Written out rather than left as raw SQL because
# these are what a stat's action ever is, and because bet and raise are the
# pair everybody runs together: an aggressive action with nothing to call is
# a bet, with something to call it is a raise, and `agg=1` alone silently
# means both at once.
#
# A call has to name the all-in that is a call. 95 of the 236 all-ins here
# are a player putting their last chips in to CALL, and `action='C'` alone
# misses every one of them -- the same defect `lines.letter` exists to
# correct, arrived at from the other direction.
ACTIONS = {
    "bet": ("agg=1 AND to_call=0", "bet, with nothing to call"),
    "raise": ("agg=1 AND to_call>0", "raised a bet"),
    "aggressive": ("agg=1", "bet or raised"),
    "call": ("action IN ('C','A') AND agg=0", "called, all-in calls included"),
    "check": ("action='X'", "checked"),
    "fold": ("action='F'", "folded"),
    "continue": ("action<>'F'", "called or raised rather than folding"),
    "allin": ("allin=1", "put the last of a stack in"),
}

# Saved definitions the database will not accept, kept rather than dropped.
# A stat that quietly vanishes because a column it names was renamed is a
# stat whose absence from a report nobody notices; one that is listed as
# broken is a thing somebody fixes.
BROKEN = []


def definitions(path=None):
    """The saved definitions exactly as they are on disk."""
    path = Path(path or CUSTOM)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_custom(defs, path=None):
    """Write the definitions back, indented, because people read this file."""
    path = Path(path or CUSTOM)
    path.write_text(json.dumps(defs, indent=2) + "\n", encoding="utf-8")


def _sql_error(chance, action, db=None):
    """
    Whether SQLite will accept the pair, asked of the real table.

    EXPLAIN compiles the statement and runs none of it, which is exactly the
    question being asked: a chance naming a column that does not exist has
    to be caught when it is written, not on the next report, where it
    arrives as a stack trace on top of somebody's stats table.

    Returns None when there is no database to ask. A definition cannot be
    checked against a table that is not there, and refusing to load it would
    leave a fresh install unable to open its own saved stats until the first
    import had finished.
    """
    path = Path(db or DB)
    if not path.exists():
        return None
    con = sqlite3.connect(str(path))
    try:
        con.execute(f"EXPLAIN SELECT 1 FROM decisions "
                    f"WHERE ({chance}) AND ({action})")
        return None
    except sqlite3.Error as e:
        return str(e)
    finally:
        con.close()


def _refuse(d):
    """Why this definition cannot become a stat, in a sentence, or None."""
    key = str(d.get("key", ""))
    if not KEY_OK.match(key):
        return (f"{key!r} is not a usable key -- lowercase letters, digits "
                f"and underscores, because `--show` splits its argument on "
                f"commas and columns are selected by this name")
    if key in BY_KEY:
        if BY_KEY[key].custom:
            return f"{key!r} is defined twice in the same file"
        return (f"{key!r} is already a built-in stat, and shadowing one "
                f"would make `--show {key}` quietly mean something else")
    if not d.get("chance") or not d.get("action"):
        return "a stat is a chance and an action, and needs both"
    if d.get("per", "decision") not in ("decision", "hand"):
        return "`per` is 'decision' or 'hand'"
    return None




# ----------------------------------------------------------- expressions
#
# Hand2Note's expression stats, in its own language, so that a formula
# from its manual or from a commercial pack drops in unchanged. Ten
# functions over any plain stat, arithmetic, comparisons, `if`, AND, OR,
# NOT -- that is the whole of what its documentation publishes, and it
# is enough for everything its manual shows: WTSD after a c-bet, WWSF,
# aggression factor, a stat's profit per case, the 4-bet range.
#
# A plain stat here "hits" a player-hand when any of that player's
# decisions in the hand satisfies the stat's chance and action, and the
# player-hand is an opportunity when any decision satisfies the chance.
# That is Hand2Note's per-hand counting, and it is why `Cases` of a stat
# is not the row count the stats table shows for a per-decision stat.
#
#   Value(s)             100 * Cases / Opps
#   Cases(s)             player-hands in which the action was taken
#   Opps(s)              player-hands in which it could have been
#   VsHeroCases(s)       cases with hero still in the pot at the moment
#   VsHeroOpps(s)        opportunities with hero still in the pot
#   WonHandCases(s)      cases in which the player won the hand
#   WentToSDCases(s)     cases that reached showdown
#   WonHandAtSDCases(s)  cases that reached showdown and won it
#   AmountWon(s)         big blinds won over the cases, cash only
#   ActionProfit(s)      the action's profit over the cases, cash only:
#                        stack at the end less stack before the action
#
# The argument is a stat key, or a filter in quotes -- Cases("--street
# flop") is how Hand2Note's "Flop Any Action" is said here.

import ast

FUNCTIONS = ("Value", "Cases", "Opps", "VsHeroCases", "VsHeroOpps",
             "WonHandCases", "WentToSDCases", "WonHandAtSDCases",
             "AmountWon", "ActionProfit")


class Expression:
    """A named formula over plain stats, evaluated under any filter."""

    def __init__(self, key, label, formula, group="expression", note="",
                 custom=False):
        self.key, self.label, self.formula = key, label, formula
        self.group, self.note, self.custom = group, note, custom
        self.source = "e"
        self.tree = compile_formula(formula)




def compile_formula(formula):
    """
    The formula as a tree, having refused everything that is not the
    language. Python's parser reads the arithmetic; the walk below allows
    only numbers, the ten functions, `if`, and the operators -- a formula
    is data from a file and must not be able to call anything else.
    """
    text = formula.strip()
    for word, py in ((" AND ", " and "), (" OR ", " or "), ("NOT ", "not ")):
        text = text.replace(word, py)
    text = text.replace("if(", "IF(").replace("If(", "IF(")
    tree = ast.parse(text, mode="eval")
    for node in ast.walk(tree):
        ok = isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                               ast.Compare, ast.BoolOp, ast.Load,
                               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                               ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And,
                               ast.Or, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                               ast.Eq, ast.NotEq))
        if isinstance(node, ast.Constant):
            ok = isinstance(node.value, (int, float, str))
        elif isinstance(node, ast.Name):
            ok = True                        # a stat key inside a call
        elif isinstance(node, ast.Call):
            ok = (isinstance(node.func, ast.Name)
                  and (node.func.id in FUNCTIONS or node.func.id == "IF")
                  and not node.keywords)
        if not ok:
            raise ValueError(f"not in the expression language: "
                             f"{type(node).__name__} in {formula!r}")
    return tree


def formula_terms(tree):
    """The (function, argument) pairs a formula asks for."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.func.id in FUNCTIONS:
            arg = node.args[0]
            key = arg.id if isinstance(arg, ast.Name) else str(arg.value)
            out.add(key)
    return out


def stat_terms(con, ref, where="1=1"):
    """
    The ten aggregates for one stat, or one quoted filter, under a filter.

    One query per stat, which is one query per distinct argument of a
    formula: the per-hand grouping is done once and every function reads
    it. Money is cash only, as everywhere.
    """
    if ref in BY_KEY:
        st = BY_KEY[ref]
        if st.source != "d":
            raise ValueError(f"{ref} is a per-hand stat and has no decisions to count")
        chance, action = st.chance, st.action
    else:
        import shlex
        import query
        # Split as a shell would but keep the quotes a `--where` value
        # needs, then take one layer of quoting off each word: the
        # formula's own quotes are around the whole filter.
        words = [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w
                 for w in shlex.split(ref, posix=False)]
        chance = query.build(words)[0]
        action = "1=1"
    profit = "((se.stack + se.won - se.posted - se.invested) - f.stack_before) / f.bb"
    row = con.execute(f"""
        WITH x AS (
          SELECT hand_id, seat,
                 MAX(CASE WHEN ({action}) THEN 1 ELSE 0 END) hit,
                 MAX(CASE WHEN ({action}) AND hero_in = 1 THEN 1 ELSE 0 END) hit_vh,
                 MAX(CASE WHEN hero_in = 1 THEN 1 ELSE 0 END) opp_vh,
                 MIN(CASE WHEN ({action}) THEN n END) first_n
          FROM decisions WHERE ({where}) AND ({chance})
          GROUP BY hand_id, seat)
        SELECT COUNT(*), SUM(x.hit), SUM(x.opp_vh), SUM(x.hit_vh),
               SUM(CASE WHEN x.hit AND se.won > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.hit AND sp.wtsd THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.hit AND sp.wtsd AND se.won > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.hit AND sp.fmt <> 'MTT' THEN sp.net_bb END),
               SUM(CASE WHEN x.hit AND sp.fmt <> 'MTT' AND f.bb > 0
                        THEN {profit} END)
        FROM x
        JOIN seats se ON se.hand_id = x.hand_id AND se.seat = x.seat
        JOIN spots sp ON sp.hand_id = x.hand_id AND sp.seat = x.seat
        LEFT JOIN decisions f ON f.hand_id = x.hand_id AND f.n = x.first_n
        """).fetchone()
    opps, cases, opp_vh, hit_vh, won, sd, wonsd, amount, ap = [v or 0 for v in row]
    return {"Opps": opps, "Cases": cases, "VsHeroOpps": opp_vh,
            "VsHeroCases": hit_vh, "WonHandCases": won, "WentToSDCases": sd,
            "WonHandAtSDCases": wonsd, "AmountWon": amount, "ActionProfit": ap,
            "Value": 100.0 * cases / opps if opps else None}


def evaluate(con, expr, where="1=1"):
    """
    The formula's value under a filter, and the smallest sample it stood
    on. None where a division met zero or a term had no opportunities --
    a number from nothing is the thing a formula must never print.
    """
    terms = {ref: stat_terms(con, ref, where) for ref in formula_terms(expr.tree)}
    n = min((t["Opps"] for t in terms.values()), default=0)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Call):
            if node.func.id == "IF":
                c = ev(node.args[0])
                return None if c is None else ev(node.args[1] if c else node.args[2])
            arg = node.args[0]
            ref = arg.id if isinstance(arg, ast.Name) else str(arg.value)
            return terms[ref][node.func.id]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if v is None:
                return None
            return -v if isinstance(node.op, ast.USub) else \
                (not v) if isinstance(node.op, ast.Not) else v
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            if any(v is None for v in vals):
                return None
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            a = ev(node.left)
            for op, right in zip(node.ops, node.comparators):
                b = ev(right)
                if a is None or b is None:
                    return None
                ok = {ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b,
                      ast.GtE: a >= b, ast.Eq: a == b, ast.NotEq: a != b}[type(op)]
                if not ok:
                    return False
                a = b
            return True
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if a is None or b is None:
                return None
            if isinstance(node.op, ast.Div):
                return a / b if b else None
            if isinstance(node.op, ast.Mod):
                return a % b if b else None
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b,
                    ast.Pow: a ** b}[type(node.op)]
        raise ValueError(f"cannot evaluate {type(node).__name__}")

    return ev(expr.tree), n


def define_expression(key, formula, label="", note="", path=None, db=None):
    """Save a formula, having first compiled it and evaluated it once."""
    if key in BY_KEY or key in {e.key for e in EXPRESSIONS if not e.custom}:
        raise ValueError(f"{key!r} is a built-in; pick another name")
    expr = Expression(key, label or key.replace("_", " "), formula, custom=True)
    for ref in formula_terms(expr.tree):
        if ref not in BY_KEY and not ref.startswith("-"):
            raise ValueError(f"{ref!r} is not a stat key -- see `stats.py --list`")
    con = sqlite3.connect(db or DB)
    value, n = evaluate(con, expr)
    con.close()
    saved = [d for d in definitions(path) if d.get("key") != key]
    saved.append({"key": key, "label": expr.label, "formula": formula, "note": note})
    (Path(path) if path else CUSTOM).write_text(
        json.dumps(saved, indent=1), encoding="utf-8")
    load_custom(path, db)
    return value, n


# The built-ins, in the manual's own examples where it has them. After the
# functions above because each is compiled as it is made.
EXPRESSIONS = [
    Expression("wtsd_after_cbet", "WTSD after cbet",
               "WentToSDCases(cbet_flop) / Cases(cbet_flop) * 100",
               note="of the hands c-bet on the flop, how many reached showdown"),
    Expression("won_after_cbet", "won hand after cbet",
               "WonHandCases(cbet_flop) / Cases(cbet_flop) * 100"),
    Expression("cbet_profit", "cbet profit, bb per cbet",
               "ActionProfit(cbet_flop) / Cases(cbet_flop)",
               note="Hand2Note's Action Profit of the flop c-bet, per case"),
    Expression("threebet_profit", "3bet profit, bb per 3bet",
               "ActionProfit(threebet) / Cases(threebet)"),
    Expression("af_flop", "aggression factor, flop",
               "Cases(flop_agg) / Cases(\"--street flop --where \\\"action='C'\\\"\")",
               note="bets and raises over calls, Hand2Note's AF"),
    Expression("fourbet_range", "4bet range",
               "Value(rfi) * Value(fourbet) / 100",
               note="the manual's example: open rate times 4-bet rate"),
]
EXPR_BY_KEY = {e.key: e for e in EXPRESSIONS}


def load_custom(path=None, db=None):
    """
    Replace the saved stats in the registry with whatever the file now holds.

    The registry is mutated in place and never rebuilt. Every other module
    did `from stats import STATS` at import time, so binding a new list here
    would leave all of them holding the old one, and the saved stats would
    exist in this module alone -- present in `stats.py --list` and absent
    from every report, which is the worse of the two ways to be wrong.

    Idempotent, because it is called twice: once here, and again by the
    packaged application once it knows where the user's files actually are.
    Frozen, that is beside the executable and not beside code that
    PyInstaller deletes on the way out.
    """
    global CUSTOM
    if path is not None:
        CUSTOM = Path(path)
    BROKEN[:] = []
    for s in [s for s in STATS if s.custom]:
        STATS.remove(s)
        BY_KEY.pop(s.key, None)
    for e in [e for e in EXPRESSIONS if e.custom]:
        EXPRESSIONS.remove(e)
        EXPR_BY_KEY.pop(e.key, None)
    try:
        saved = definitions()
    except (OSError, ValueError) as e:
        BROKEN.append(("(the file itself)", f"{CUSTOM}: {e}"))
        return []
    loaded = []
    for d in saved:
        if "formula" in d:
            # A saved expression: compiled here, so a formula that no
            # longer parses is listed as broken rather than left out.
            try:
                e = Expression(d["key"], d.get("label") or d["key"], d["formula"],
                               note=d.get("note", ""), custom=True)
            except (ValueError, SyntaxError) as err:
                BROKEN.append((str(d.get("key")) or "(unnamed)", str(err)))
                continue
            EXPRESSIONS.append(e)
            EXPR_BY_KEY[e.key] = e
            loaded.append(e)
            continue
        why = _refuse(d) or _sql_error(d.get("chance"), d.get("action"), db)
        if why:
            BROKEN.append((str(d.get("key")) or "(unnamed)", why))
            continue
        st = Stat(d["key"], d.get("label") or d["key"],
                  d["chance"], d["action"],
                  per=d.get("per", "decision"),
                  group=d.get("group") or CUSTOM_GROUP,
                  note=d.get("note", ""), custom=True)
        STATS.append(st)
        BY_KEY[st.key] = st
        loaded.append(st)
    return loaded


def define(key, label, chance, action, per="decision", group=CUSTOM_GROUP,
           note="", path=None, db=None):
    """
    Save a stat, having first made the database agree that it is one.

    The check is not ceremony, and it is the same thing Hand2Note's own
    manual tells you to press before putting a stat in a HUD: a definition
    that does not compile is not discovered when it is written, it is
    discovered weeks later, on top of a report, by somebody who no longer
    remembers writing it.

    Returns (n, k): how often the chance came up and how often the action
    was taken. A saved stat with n=0 describes nothing, and the moment to
    find that out is while the filter that made it is still on the screen.
    """
    d = {"key": key, "label": label or key.replace("_", " "),
         "chance": chance, "action": action, "per": per, "group": group,
         "note": note}
    # Redefining is allowed and shadowing a built-in is not, so the name
    # being replaced comes out of the registry before the new definition is
    # judged -- otherwise a saved stat could never be corrected, its own
    # previous version being the thing that refused it.
    was = BY_KEY.pop(key, None) if key in BY_KEY and BY_KEY[key].custom else None
    try:
        why = _refuse(d) or _sql_error(chance, action, db)
    finally:
        if was is not None:
            BY_KEY[key] = was
    if why:
        raise ValueError(why)
    save_custom([e for e in definitions(path) if e.get("key") != key] + [d],
                path)
    load_custom(path, db)
    con = sqlite3.connect(str(db or DB))
    try:
        n, k, _p, _lo, _hi = rate(con, BY_KEY[key])
    finally:
        con.close()
    return n, k


def forget(key, path=None):
    """Remove a saved stat. A built-in is not the file's to remove."""
    if key in BY_KEY and not BY_KEY[key].custom:
        raise ValueError(f"{key!r} is built in, and lives in this file")
    saved = definitions(path)
    remaining = [d for d in saved if d.get("key") != key]
    if len(remaining) == len(saved):
        raise ValueError(f"no saved stat named {key!r}")
    save_custom(remaining, path)
    load_custom(path)


load_custom()


def wilson(k, n, z=1.96):
    """
    The interval a proportion actually has, rather than the one in the book.

    The textbook interval is p +- z*sqrt(p(1-p)/n). At the sample sizes that
    decide things here it is wrong in the way that matters most: a player who
    has folded 3 of 3 gets an interval of zero width, and one who has folded
    0 of 8 gets bounds below zero. Wilson's has neither failure, which is why
    it is the one used everywhere in this project.
    """
    if not n:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def split_point(con, where="1=1", table="decisions"):
    """
    The moment that divides a filter's rows in half by time, or None.

    Split-half is the rule for any population finding, and splitting by
    DATE rather than at random is the harder test: a random split shares
    tables and opponents between the halves, so a quirk of one table shows
    up in both and looks like a population truth. Rows before the point are
    half A, from it on are half B.
    """
    row = con.execute(
        f"SELECT played_at FROM {table} WHERE {where} ORDER BY played_at "
        f"LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM {table} WHERE {where})"
    ).fetchone()
    return row[0] if row else None


def rate(con, stat, where="1=1", params=()):
    """One stat, one filter: (n, k, p, lo, hi). n=0 is a legitimate answer."""
    if isinstance(stat, str):
        stat = BY_KEY[stat]
    table = "decisions" if stat.source == "d" else "spots"
    if stat.source == "s":
        # In spots the chance and the action are columns rather than
        # predicates, so both are summed over the rows the filter allows.
        sql = (f"SELECT SUM({stat.chance}), SUM({stat.chance} AND {stat.action}) "
               f"FROM spots WHERE {where}")
    elif stat.per == "hand":
        sql = (f"SELECT COUNT(*), SUM(did) FROM ("
               f"  SELECT hand_id, seat, MAX(CASE WHEN {stat.action} THEN 1 ELSE 0 END) did"
               f"  FROM {table} WHERE ({stat.chance}) AND ({where})"
               f"  GROUP BY hand_id, seat)")
    else:
        sql = (f"SELECT COUNT(*), SUM(CASE WHEN {stat.action} THEN 1 ELSE 0 END) "
               f"FROM {table} WHERE ({stat.chance}) AND ({where})")
    n, k = con.execute(sql, params).fetchone()
    n, k = n or 0, k or 0
    p, lo, hi = wilson(k, n)
    return n, k, p, lo, hi


def rates(con, where="1=1", params=(), stats=None):
    """
    Every stat under one filter, in one pass over the table instead of thirty.

    `rate` asks its own question, which is right for one stat and wrong for a
    table of them: thirty stats meant thirty full passes over the same rows,
    and drawing the unfiltered stats table took five and a half seconds. They
    all read the same rows and differ only in what they count, so they can be
    counted together -- one `SUM(CASE WHEN ...)` per stat, one scan.

    This is the same lesson `rates_by` already learned for groups; it had not
    been applied to the table the window opens on.

    Indexes are not the fix for this. An index was tried -- shaped exactly
    for the stat predicates -- and made the unfiltered table 15% *slower*,
    because when a filter selects most of the rows, seeking an index and
    then fetching each row costs more than reading the table straight
    through. Scanning once is the answer; scanning thirty times was the
    problem.

    Returns {key: (n, k)}. Spots-sourced stats are not here: their situation
    lives in a per-hand table and cannot be counted in the same pass.
    """
    stats = [s for s in (stats if stats is not None else STATS) if s.source == "d"]
    out = {}

    per_dec = [s for s in stats if s.per == "decision"]
    if per_dec:
        cols = []
        for s in per_dec:
            cols.append(f"SUM(CASE WHEN ({s.chance}) THEN 1 ELSE 0 END)")
            cols.append(f"SUM(CASE WHEN ({s.chance}) AND ({s.action}) "
                        f"THEN 1 ELSE 0 END)")
        row = con.execute(f"SELECT {', '.join(cols)} FROM decisions "
                          f"WHERE {where}", params).fetchone()
        for i, s in enumerate(per_dec):
            out[s.key] = (row[2 * i] or 0, row[2 * i + 1] or 0)

    # A player VPIPs once however many times they act, so these are counted
    # per player-in-hand. The chance moves inside the MAX rather than into
    # the WHERE: a group counts as a chance if any of its rows was one,
    # which is what the per-stat version says with its inner filter.
    per_hand = [s for s in stats if s.per == "hand"]
    if per_hand:
        inner, outer = [], []
        for i, s in enumerate(per_hand):
            inner.append(f"MAX(CASE WHEN ({s.chance}) THEN 1 ELSE 0 END) c{i}")
            inner.append(f"MAX(CASE WHEN ({s.chance}) AND ({s.action}) "
                         f"THEN 1 ELSE 0 END) k{i}")
            outer += [f"SUM(c{i})", f"SUM(k{i})"]
        row = con.execute(
            f"SELECT {', '.join(outer)} FROM (SELECT {', '.join(inner)} "
            f"FROM decisions WHERE {where} GROUP BY hand_id, seat)",
            params).fetchone()
        for i, s in enumerate(per_hand):
            out[s.key] = (row[2 * i] or 0, row[2 * i + 1] or 0)

    return out


def rates_by(con, stat, group, where="1=1", params=(), skip_null=True):
    """
    One stat, split by any expression, in a single pass over the table.

    Asking `rate` once per group is the obvious way to build a report, and it
    is why the first opponent leaderboard took eleven minutes: 48 players
    times 35 stats is 1,680 full scans of 93,600 rows to answer a question
    SQL will answer 35 times with a GROUP BY. A report nobody waits for is a
    report nobody reads, so the grouping happens in the database.

    `group` is any SQL expression over the same table -- a column name for a
    position or stake report, `substr(played_at,1,7)` for a monthly one.
    """
    if isinstance(stat, str):
        stat = BY_KEY[stat]
    table = "decisions" if stat.source == "d" else "spots"
    guard = f" AND ({group}) IS NOT NULL" if skip_null else ""
    if stat.source == "s":
        sql = (f"SELECT {group}, SUM({stat.chance}), "
               f"SUM({stat.chance} AND {stat.action}) FROM spots "
               f"WHERE ({where}){guard} GROUP BY 1")
    elif stat.per == "hand":
        # A per-hand stat counts a player once however often they acted, so
        # it has to collapse to one row per (hand, seat) before grouping.
        sql = (f"SELECT g, COUNT(*), SUM(did) FROM ("
               f"  SELECT ({group}) g, hand_id, seat,"
               f"  MAX(CASE WHEN {stat.action} THEN 1 ELSE 0 END) did"
               f"  FROM {table} WHERE ({stat.chance}) AND ({where}){guard}"
               f"  GROUP BY hand_id, seat) GROUP BY g")
    else:
        sql = (f"SELECT {group}, COUNT(*), "
               f"SUM(CASE WHEN {stat.action} THEN 1 ELSE 0 END) "
               f"FROM {table} WHERE ({stat.chance}) AND ({where}){guard} "
               f"GROUP BY 1")
    return {g: (n or 0, k or 0) for g, n, k in con.execute(sql, params)}


def rates_by_player(con, stat, where="1=1", params=()):
    """Every player's (n, k) for one stat -- `rates_by` on the player column."""
    return rates_by(con, stat, "player", where, params)


def difference(k1, n1, k2, n2, z=1.96):
    """
    An interval on the DIFFERENCE between two rates, and a p-value.

    Not two intervals held up beside each other. Asking whether two 95%
    intervals overlap is a test everybody reaches for and it is the wrong
    one: it is far too strict, behaving like a test at about the 99% level,
    so it throws away real differences and calls them nothing. Two rates can
    differ perfectly clearly while their intervals share a sliver.

    This project asked exactly that question and got the wrong answer from
    it. Regulars stole 41.1% against regulars and 51.0% against fish, the
    intervals overlapped by two points, and it was reported as no
    difference. The interval on the difference is 9.9 points wide of zero,
    and the difference is real.

    The interval is Newcombe's, built out of the two Wilson intervals this
    project already uses -- so the same asymmetry that makes Wilson right
    near 0 and 1 carries into the difference, and a rate of 0 out of 12
    still behaves.

    Returns (difference, low, high, p). The p-value is the ordinary pooled
    two-proportion test and is there for `holm` to correct, because asking
    thirty questions and reporting the best answer is its own error.
    """
    if not n1 or not n2:
        return None, None, None, None
    p1, l1, u1 = wilson(k1, n1, z)
    p2, l2, u2 = wilson(k2, n2, z)
    d = p1 - p2
    lo = d - sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)

    pooled = (k1 + k2) / (n1 + n2)
    se = sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if not se:
        return d, lo, hi, 1.0
    return d, lo, hi, erfc(abs(d / se) / sqrt(2))


# How far apart two rates have to be before this much data could see it,
# at the usual 95% confidence and 80% power. 1.96 is the confidence, 0.8416
# is the power, and they add because both tails have to be cleared.
Z_ALPHA, Z_BETA = 1.96, 0.8416


def detectable(n1, n2, p=0.5):
    """
    The smallest difference this much data could have found.

    The number to print when nothing is significant, because "no
    difference" and "no evidence either way" are different findings and only
    one of them is usually true. At n=548 against n=157 the answer is about
    9 points: anything smaller than that was never going to show up, and
    saying so is more use than saying nothing was found.
    """
    if not n1 or not n2:
        return None
    return (Z_ALPHA + Z_BETA) * sqrt(p * (1 - p) * (1 / n1 + 1 / n2))


def holm(pairs):
    """
    Adjusted p-values, for when many questions are asked at once.

    Thirty stats compared between two populations will throw up one or two
    at p < 0.05 with nothing going on at all -- that is what p < 0.05 means.
    Holm's is the plainest correction that is not Bonferroni's
    over-strictness: sort, and charge each p-value by how many were still
    unresolved when it was reached.

    Returns {key: adjusted p}, and adjusted p-values never decrease down the
    sorted order, which is what keeps the answer coherent.
    """
    live = sorted((p, k) for k, p in pairs if p is not None)
    out, running = {}, 0.0
    for i, (p, key) in enumerate(live):
        running = max(running, min(1.0, p * (len(live) - i)))
        out[key] = running
    for key, p in pairs:
        out.setdefault(key, None)
    return out


def compare(con, stat, where_a, where_b, params_a=(), params_b=()):
    """
    Two populations on one stat: both rates, and the gap between them.

    Returns (a, b, (difference, low, high, p)) where a and b are whatever
    `rate` returns. Whether the gap is real is a question about the third
    element and about how many other stats were asked at the same time --
    see `holm` -- and is deliberately not answered here as a bare boolean.
    """
    a = rate(con, stat, where_a, params_a)
    b = rate(con, stat, where_b, params_b)
    if not a[0] or not b[0]:
        return a, b, (None, None, None, None)
    return a, b, difference(a[1], a[0], b[1], b[0])


def fmt(n, k, p, lo, hi, min_n=30):
    """A rate as it should always be shown: value, spread, and its n."""
    if n == 0:
        return f"{'--':>7}          n=0"
    band = f"+/-{100 * (hi - lo) / 2:.0f}"
    thin = " ?" if n < min_n else "  "
    return f"{100 * p:6.1f}% {band:>7}{thin} n={n:<6d}"


def report(con, where, title, groups=None, min_n=30):
    """Every stat in the registry, for one filter."""
    print(f"\n{title}")
    print("-" * len(title))
    last = None
    for s in STATS:
        if groups and s.group not in groups:
            continue
        if s.group != last:
            print(f"\n  [{s.group}]")
            last = s.group
        n, k, p, lo, hi = rate(con, s, where)
        print(f"  {s.label:22} {fmt(n, k, p, lo, hi, min_n)}")


def by_position(con, keys, where, title):
    """One row per position -- the shape a tracker's popup has."""
    positions = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
    print(f"\n{title}")
    head = "  " + "pos".ljust(6) + "".join(BY_KEY[k].label.center(18) for k in keys)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for pos in positions:
        cells = []
        for k in keys:
            n, kk, p, lo, hi = rate(con, k, f"({where}) AND position='{pos}'")
            cells.append(("--" if not n else
                          f"{100 * p:.1f}% n={n}").center(18))
        print("  " + pos.ljust(6) + "".join(cells))


POOL = f"is_hero=0 AND {sites.CASH} AND n_players>=5 AND standard=1"


def check(db_path=DB):
    """
    The engine must reproduce what the old hardcoded modules produce.

    These stats are already computed by `spots`, its own way, from its own
    columns. If the engine's definition of "3bet" and spots' definition of
    "3bet" disagree, one of them is wrong and every report built on the
    engine inherits it -- so the overlap is checked before anything is built
    on top. The stats with no counterpart in spots are exactly what the
    engine is for, and have nothing to check against.
    """
    con = sqlite3.connect(db_path)
    # The fourth field is why the two are allowed to differ. Where it is
    # None they must agree. Where it is a reason, the difference is a defect
    # in the OLD derivation that the engine deliberately does not copy --
    # and it is printed every run, so it stays a decision rather than
    # becoming a thing everybody stopped looking at.
    pairs = [
        ("vpip", "vpip", None, None),
        ("pfr", "pfr", None, None),
        ("rfi", "rfi", "rfi_chance", None),
        ("threebet", "threebet", "threebet_chance", None),
        ("fourbet", "fourbet", "faced_threebet",
         "spots counts cold seats facing a 3bet as the raiser (64 rows)"),
        ("fold_to_3bet", "fold_to_threebet", "faced_threebet",
         "spots counts cold seats facing a 3bet as the raiser (64 rows)"),
        ("cbet_flop", "cbet", "cbet_chance",
         "spots gives the raiser a cbet chance when they were bet into"),
        ("fold_to_cbet", "fold_to_cbet", "faced_cbet",
         "spots counts a player who folded to a RAISE of the cbet as having "
         "folded to the cbet -- they never acted against the bet alone (27)"),
        ("raise_cbet", "raised_cbet", "faced_cbet", None),
    ]
    print(f"{'stat':16} {'engine':>20}   {'spots':>20}   agree")
    fails = []
    for key, col, chance, why in pairs:
        n, k, p, _, _ = rate(con, key, POOL)
        if chance:
            sn, sk = con.execute(
                f"SELECT SUM({chance}), SUM({col}) FROM spots WHERE {POOL}"
            ).fetchone()
        else:
            sn, sk = con.execute(
                f"SELECT COUNT(*), SUM({col}) FROM spots WHERE {POOL}"
            ).fetchone()
        sp = 100 * (sk or 0) / (sn or 1)
        # The two are built from different tables by different code, so they
        # are allowed to disagree on the margins of a definition -- but a
        # point apart means the definitions themselves differ.
        agree = abs(100 * p - sp) < 1.0
        if not agree and not why:
            fails.append(key)
        mark = "OK" if agree else ("KNOWN" if why else "DIFFERS")
        print(f"{key:16} {100 * p:6.2f}% n={n:<8d}   {sp:6.2f}% n={sn or 0:<8d}   {mark}")
        if not agree and why:
            print(f"{'':16} why: {why}")
    print()
    print("stats with no counterpart in spots -- what the engine adds:")
    added = [s for s in STATS if s.key not in {p[0] for p in pairs}]
    for s in added:
        n, k, p, lo, hi = rate(con, s, POOL)
        print(f"  {s.label:24} {fmt(n, k, p, lo, hi)}")
    # One pass and thirty passes must produce the same numbers. There are now
    # two ways to count every stat, and two ways to compute one thing is how
    # they come to disagree -- silently, since both return a plausible
    # number. Checked over several filters, because the per-hand form counts
    # differently from the per-decision one and only differs where a player
    # acted twice.
    off = 0
    for where in ("1=1", "is_hero = 1", "street='flop' AND pot_type='3bet'",
                  "position = 'BB' AND vs_pos = 'BTN'"):
        batch = rates(con, where)
        for st in STATS:
            if st.source == "s":
                continue
            n, k, _p, _lo, _hi = rate(con, st, where)
            if (n, k) != batch[st.key]:
                off += 1
                if off <= 3:
                    print(f"    {st.key} under {where}: "
                          f"one at a time {(n, k)}, one pass {batch[st.key]}")
    counted = 4 * len([st for st in STATS if st.source != "s"])
    print(f"one pass agrees with thirty   {counted - off}/{counted}")
    if off:
        fails.append("the batched and per-stat counts disagree")

    # A saved stat must be indistinguishable from a shipped one, and the way
    # it would fail to be is silent: `load_custom` mutating a copy of the
    # registry rather than the registry itself leaves it listed here and
    # missing from every report. So the round trip is driven end to end --
    # define it, find it in the registry, count it both ways, forget it --
    # against a file of its own, because a check that writes to the user's
    # saved stats is a check nobody dares run twice. In a fresh directory
    # rather than under a fixed name: `check.py` runs sixteen of these, and
    # two of them sharing a scratch file would fail for a reason that has
    # nothing to do with stats.
    scratch = Path(tempfile.mkdtemp()) / "stats.json"
    was_custom, saved_before = CUSTOM, definitions()
    trips = []
    try:
        n, k = define("roundtrip_check", "round trip",
                      "street='flop' AND facing='check'",
                      ACTIONS["aggressive"][0], path=scratch, db=db_path)
        trips.append(("saved stat reaches the registry",
                      "roundtrip_check" in BY_KEY
                      and BY_KEY["roundtrip_check"] in STATS))
        trips.append(("it has a chance to occur", n > 0))
        one = rate(con, "roundtrip_check")
        batch = rates(con, "1=1")["roundtrip_check"]
        trips.append(("one at a time agrees with one pass",
                      (one[0], one[1]) == batch == (n, k)))
        shadow = True
        try:
            define("cbet_flop", "shadow", "1=1", "agg=1", path=scratch,
                   db=db_path)
            shadow = False
        except ValueError:
            pass
        trips.append(("a built-in name cannot be shadowed", shadow))
        forget("roundtrip_check", path=scratch)
        trips.append(("forgetting removes it everywhere",
                      "roundtrip_check" not in BY_KEY
                      and not [s for s in STATS if s.key == "roundtrip_check"]))
    finally:
        scratch.unlink(missing_ok=True)
        scratch.parent.rmdir()
        load_custom(was_custom)
    print(f"a saved stat behaves like a built-in   "
          f"{sum(1 for _, ok in trips if ok)}/{len(trips)}")
    for what, ok in trips:
        if not ok:
            print(f"    {what}: NO")
            fails.append(what)
    if definitions() != saved_before:
        fails.append("the check disturbed the saved stats")
    if BROKEN:
        print("saved definitions the database will not accept:")
        for key, why in BROKEN:
            print(f"    {key}: {why}")

    # The expression language says the same thing the engine says when
    # asked the same question: WWSF written as Hand2Note's manual writes
    # it, over hero, against the built-in stat. Two answers to one
    # question that drift apart is the failure every check here is for.
    e = Expression("_wwsf", "WWSF, as a formula",
                   'WonHandCases("--street flop") / Cases("--street flop") * 100')
    got, n_e = evaluate(con, e, "is_hero = 1")
    n, k, p, _lo, _hi = rate(con, BY_KEY["wwsf"], "is_hero = 1")
    same = got is not None and n and abs(got - 100 * p) < 1.5
    print()
    print(f"WWSF as a formula {got if got is None else round(got, 1)} vs the "
          f"engine's {round(100 * p, 1) if n else '--'}   {'agree' if same else 'DISAGREE'}")
    if not same:
        fails.append("the expression language disagrees with the engine")
    for e in EXPRESSIONS:
        try:
            evaluate(con, e, "is_hero = 1")
        except Exception as err:               # a formula that errors is a finding
            print(f"    {e.key}: {type(err).__name__}: {err}")
            fails.append(f"expression {e.key}")
    try:
        compile_formula("__import__('os')")
        fails.append("the formula language accepts a call it should not")
    except (ValueError, SyntaxError):
        pass
    print(f"{len(EXPRESSIONS)} expression stats evaluate; foreign calls are refused")

    con.close()

    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    con = sqlite3.connect(DB)
    if "--list" in argv:
        for s in STATS:
            print(f"\n{s.key:18} {s.label}   [{s.group}, per {s.per}, "
                  f"{'decisions' if s.source == 'd' else 'spots'}"
                  f"{', saved' if s.custom else ''}]")
            if s.note:
                print(f"  {s.note}")
            print(f"  chance: {s.chance}")
            print(f"  action: {s.action}")
        return 0
    if "--custom" in argv:
        # Where the file is, said out loud. Frozen it sits beside the
        # executable and not beside the code, and somebody looking for their
        # saved stats should not have to work out which of those they have.
        print(f"saved stats: {CUSTOM}")
        mine = [s for s in STATS if s.custom]
        if not mine and not BROKEN:
            print("  none yet -- `python query.py <filter> --define KEY` "
                  "makes one")
        for s in mine:
            n, k, p, lo, hi = rate(con, s)
            print(f"\n  {s.key:18} {s.label}   {fmt(n, k, p, lo, hi)}")
            if s.note:
                print(f"    from: {s.note}")
            print(f"    chance: {s.chance}")
            print(f"    action: {s.action}")
        for key, why in BROKEN:
            print(f"\n  {key:18} BROKEN -- {why}")
        return 1 if BROKEN else 0
    if "--check" in argv:
        return 0 if check() else 1
    if "--player" in argv:
        name = argv[argv.index("--player") + 1]
        n = con.execute("SELECT COUNT(*) FROM decisions WHERE player=?",
                        (name,)).fetchone()[0]
        if not n:
            print(f"no decisions recorded for {name!r}")
            return 1
        safe = name.replace("'", "''")
        report(con, f"player='{safe}' AND standard=1", f"{name}")
        return 0
    for site in sites.loaded(con):
        report(con, f"{POOL} AND site='{site}'", f"pool: {site}")
    for site in sites.loaded(con):
        by_position(con, ["rfi", "threebet", "fold_to_cbet"],
                    f"{POOL} AND site='{site}'",
                    f"{site} pool, by position")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
