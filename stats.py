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
    python stats.py save NAME [filter]        persist a Statistics context
    python stats.py open NAME                 hydrate and recompute that grid
    python stats.py list                      saved Statistics reports
"""

import json
import re
import sqlite3
import sys
import tempfile
from math import erfc, sqrt
from pathlib import Path

import games
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
    try:
        saved = definitions()
    except (OSError, ValueError) as e:
        BROKEN.append(("(the file itself)", f"{CUSTOM}: {e}"))
        return []
    loaded = []
    for d in saved:
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


# Two-tailed 95% Student-t. Hardcoded because a new dependency for one
# table would be the wrong kind of complexity, and the normal 1.96 is
# only the large-n limit. n=2 is supposed to look huge.
_T_CRIT = (
    (1, 12.706), (2, 4.303), (3, 3.182), (4, 2.776), (5, 2.571),
    (6, 2.447), (7, 2.365), (8, 2.306), (9, 2.262), (10, 2.228),
    (12, 2.179), (15, 2.131), (20, 2.086), (30, 2.042),
    (40, 2.021), (60, 2.000), (120, 1.980),
)


def t_crit(df):
    """Two-tailed 95% t for this many degrees of freedom."""
    if df < 1:
        return None
    for n, t in _T_CRIT:
        if df <= n:
            return t
    return 1.960


def mean_interval(total, sumsq, n):
    """
    95% interval on a mean, from SUM and SUM(x^2), or (None, None, None).

    Wilson is for a proportion. A mean of priced bb is a different
    shape, and wrapping a Wilson band around it would look like a
    frequency. This is the classical t interval on the sample mean:
    honest about n=2 (the band is enormous) and about n=1 (there is
    no interval -- one observation has no estimated spread).

    Not EV. Sampling uncertainty of the observed mean of the rows
    that were priced. Hands in a session are not independent and the
    tails are heavy; the interval is still better than a bare mean
    that reads as exact.
    """
    if not n or n < 2 or total is None or sumsq is None:
        return None, None, None
    mean = total / n
    var = (sumsq - (total * total) / n) / (n - 1)
    if var < 0:
        # Float noise when every priced row is the same number.
        var = 0.0
    se = sqrt(var / n)
    t = t_crit(n - 1)
    if t is None:
        return mean, None, None
    return mean, mean - t * se, mean + t * se


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


POOL = (f"is_hero=0 AND {sites.CASH} AND n_players>=5 AND standard=1 "
        f"AND {games.HOLD}")


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

    con.close()

    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if argv and argv[0] in ("save", "open", "list", "recent", "forget"):
        # Same verbs as `query.py stats`. This module is the registry;
        # the payload lives next to the grid so the two CLIs cannot
        # drift on what "this report" means.
        import query
        return query.stats_cli(argv)
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
    for site in sites.KEYS:
        report(con, f"{POOL} AND site='{site}'", f"pool: {site}")
    for site in sites.KEYS:
        by_position(con, ["rfi", "threebet", "fold_to_cbet"],
                    f"{POOL} AND site='{site}'",
                    f"{site} pool, by position")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
