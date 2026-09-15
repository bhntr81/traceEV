"""
Ask the database anything, about anybody, in any spot.

The stat engine has always taken an arbitrary filter -- every `rate()` call
ends in a WHERE clause -- and nothing has ever been able to supply one. So
the database could answer "how often does this pool fold to a cbet on a
monotone flop, in a 3-bet pot, a hundred big blinds deep" and there was no
way to ask it. This is the asking.

Three questions, one filter:

    --stats     what everybody did in the spot the filter describes
    --hands     which hands those were
    --range     what the hands that got there actually were
    --sessions  which sittings those hands were part of
    --results   what the money did in them
    --graph     the four-line results graph, written as an HTML file

and one hand on its own:

    python query.py --hand cp-2459218653

The third needs care and is the reason this module exists rather than
another flag on `stats.py`. Money is a property of a HAND; "in position on a
monotone flop" is a property of a DECISION. Filtering the money table by a
decision's conditions is not possible, and filtering it loosely instead --
dropping the conditions it cannot express -- would answer a different
question under the same heading. So the filter selects decisions, and the
money is summed over the hands those decisions happened in.

    python query.py --pool --pos BB --vs BTN --pot 3bet
    python query.py --hero --pos BB --vs BTN --vs-pool --pot 3bet
    python query.py --pool --pot 3bet --street flop --ip
    python query.py --player dblj32 --pos BTN --stats
    python query.py --cohort --hands ">=500" --vpip ">=28" --pfr "<18" \
        --street flop --stats
    python query.py --hero --board mono --results
    python query.py --hero --pot 3bet --hands
    python query.py --where "eff_bb > 150 AND fl_paired=1" --stats
    python query.py --help
"""

import sqlite3
import sys
from pathlib import Path

import json
import shlex
import tempfile

import lines
import players
import sites
import stats
import strength
from stats import (BY_KEY, STATS, detectable, difference, fmt, holm,
                   rate, rates as stat_rates, rates_by, wilson)

DB = Path(__file__).parent / "hands.db"

STREETS = ("preflop", "flop", "turn", "river")
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
POT_TYPES = ("unopened", "limped", "raised", "3bet", "4bet", "5bet+")
FACINGS = ("unopened", "open", "3bet", "4bet", "5bet+",
           "check", "bet", "raise")

# Each flag becomes a predicate over `decisions`. Keeping them here rather
# than scattered through the argument parsing means the vocabulary is one
# readable list, and `--help` can print it without drifting from the truth.
#
# A value flag takes an argument; a switch does not.
VALUE_FLAGS = {
    "--site": "site = {v}",
    "--player": "player = {v}",
    # The other seat, by name. Only meaningful while one opponent is left --
    # in a three-way pot there is no "the other player" -- so this selects
    # heads-up decisions by construction.
    "--vs-player": "vs_player = {v}",
    "--pos": "position IN ({list})",
    # The matchup. `--pos BB --vs BTN --pot 3bet` is "big blind against a
    # button open, in a 3-bet pot", which is the shape most real questions
    # about a pool actually have. Add `--vs-hero` or `--vs-pool` to say
    # which side of the table the other seat is.
    "--vs": "vs_pos IN ({list})",
    "--opener": "opener_pos IN ({list})",
    "--raiser": "pfa_pos IN ({list})",
    "--street": "street IN ({list})",
    "--pot": "pot_type IN ({list})",
    "--facing": "facing IN ({list})",
    "--combo": "combo IN ({list})",
    # What the hand became once the board came. Only where the cards were
    # shown, which is most of Ignition and a quarter of ACR -- so these
    # narrow hard, and `why_empty` says so rather than leaving an empty
    # table looking like a broken filter.
    "--made": "made IN ({list})",
    "--kicker": "kicker IN ({list})",
    "--fd": "fd IN ({list})",
    "--sd": "sd IN ({list})",
    "--stake": "bb = {n}",
    "--deep": "eff_bb >= {n}",
    "--short": "eff_bb < {n}",
    "--players": "n_players = {n}",
    "--live": "n_live = {n}",
    "--since": "played_at >= {v}",
    "--until": "played_at <= {v}",
    # When you were playing, from `sessions.py`. The hour is the SITE's
    # hour -- `played_at` is whatever clock the site wrote -- and every view
    # that uses it says so. Ranges are "a-b", inclusive, so `--hour 18-23`
    # is the evening and `--session-len 120-300` is Hand2Note's own finding
    # that two to five hours is where the win rate lives.
    "--hour": "CAST(strftime('%H', played_at) AS INT) BETWEEN {lo} AND {hi}",
    "--weekday": "strftime('%w', played_at) IN ({list})",
    "--session-len": "session_len BETWEEN {lo} AND {hi}",
    "--session-min": "session_min BETWEEN {lo} AND {hi}",
    "--tables": "tables_now BETWEEN {lo} AND {hi}",
    # The shape of the betting rather than one decision in it. Each takes a
    # GLOB pattern over the strings `lines.py` derives, so `--flop "XB*"` is
    # "checked to somebody, who bet, and then anything at all".
    "--line": None, "--node": None,
    "--pre": None, "--flop": None, "--turn": None, "--river": None,
    "--board": None,        # handled separately: named textures
    "--turn-card": None,    # what the turn did to the board
    "--river-card": None,   # and the river
    "--quick": None,        # one or more named filters from `quick_filters`
    "--where": None,        # raw SQL escape hatch
}

# Which columns each line flag can read: the actions alone, or the actions
# with their bet sizes. A node is the line cut short at the moment somebody
# had to act, so `--node` asks "who was standing here" and `--line` asks
# "how did the whole hand go".
#
# Which of the two a pattern goes to is decided by the pattern itself. Size
# buckets (s m l p o) and action verbs (F X C B R A) share no letter -- that
# is the whole reason they were chosen from different halves of the
# alphabet -- so "XBC" can only mean actions and "XBmC" can only mean
# actions with a half-pot bet, and neither needs a second flag or a mode
# switch to say which was meant.
LINE_FLAGS = {
    "--line": ("line", "sized"), "--node": ("node", "node_sz"),
    "--pre": ("pre", "pre_sz"), "--flop": ("flop", "flop_sz"),
    "--turn": ("turn", "turn_sz"), "--river": ("river", "river_sz"),
}

# strftime('%w') numbers the week from Sunday. Spelled out once, here,
# for both the filter and the dimension.
WEEKDAYS = {"sun": "0", "mon": "1", "tue": "2", "wed": "3",
            "thu": "4", "fri": "5", "sat": "6"}
WEEKDAY_NAME = {v: k for k, v in WEEKDAYS.items()}

# Not filters -- they change what is shown, not what is selected.
OPTIONS = ("--by", "--show", "--min", "--out", "--hand", "--versus",
           "--preset",
           # Naming a stat rather than selecting rows: the filter beside
           # these becomes the stat's chance, so they are skipped by `build`
           # exactly as the reporting options are.
           "--define", "--forget", "--label", "--do", "--per", "--save")

SWITCHES = {
    "--hero": "is_hero = 1",
    "--pool": "is_hero = 0",
    "--ip": "is_ip = 1",
    "--oop": "is_ip = 0",
    "--pfa": "is_pfa = 1",
    "--not-pfa": "is_pfa = 0",
    "--allin": "allin = 1",
    "--aggressive": "agg = 1",
    "--multiway": "n_live > 2",
    "--headsup": "n_live = 2",
    "--vs-pfa": "vs_pfa = 1",
    "--vs-hero": "vs_hero = 1",
    "--vs-pool": "vs_hero = 0",
    "--standard": "standard = 1",
    # Who is playing, which is the distortion this whole tracker averaged
    # over until now: people isolate wider and value-bet thinner against a
    # recreational player, so a pool number that mixes the two describes
    # neither. `--reg --vs-reg` is how regulars play each other.
    "--reg": "player_class = 'reg'",
    "--fish": "player_class = 'fish'",
    "--vs-reg": "vs_class = 'reg'",
    "--vs-fish": "vs_class = 'fish'",
    # The company, rather than the single opponent. `--vs-reg` needs the pot
    # heads up, which is 17% of the database; these ask the same question of
    # a pot of any size, which is most of the rest.
    "--with-fish": "n_fish > 0",
    "--regs-only": "n_fish = 0 AND n_reg > 0",
    "--drawing": "(fd IS NOT NULL OR sd IS NOT NULL)",
    # Both at once, which is the hand that plays like neither: a flush draw
    # with a straight draw beside it is usually a favourite against a pair.
    "--combo-draw": "(fd IS NOT NULL AND sd IS NOT NULL)",
    "--shown": "cards IS NOT NULL",
}

# What a report can be split by. A tracker's value is mostly here: one
# number for "how often do I 3-bet" is a fact, the same number broken down
# by position is a plan.
#
# Each is an SQL expression over `decisions`, plus how to order the rows,
# since a report sorted alphabetically puts August before February and the
# big blind before the button.
DIMENSIONS = {
    "position": ("position", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
    "stake": ("bb", lambda k: float(k or 0)),
    "site": ("site", str),
    "player": ("player", str),
    "month": ("substr(played_at, 1, 7)", str),
    "day": ("substr(played_at, 1, 10)", str),
    "pot": ("pot_type", lambda k: (
        ["unopened", "limped", "raised", "3bet", "4bet", "5bet+"].index(k)
        if k in ("unopened", "limped", "raised", "3bet", "4bet", "5bet+")
        else 99)),
    "street": ("street", lambda k: (
        ["preflop", "flop", "turn", "river"].index(k)
        if k in ("preflop", "flop", "turn", "river") else 99)),
    "facing": ("facing", str),
    "vs": ("vs_pos", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
    "opener": ("opener_pos", lambda k: (
        ["UTG", "HJ", "CO", "BTN", "SB", "BB"].index(k)
        if k in ("UTG", "HJ", "CO", "BTN", "SB", "BB") else 99)),
    "players": ("n_players", lambda k: float(k or 0)),
    "hi": ("fl_hi", str),
    # These are the first MDA dimensions: they describe the spot around a
    # decision rather than the player alone, so the same report can say what
    # the pool does by texture, stack depth, or opponent class.
    "texture": ("CASE WHEN street = 'preflop' THEN NULL "
                "WHEN fl_mono = 1 THEN 'monotone' "
                "WHEN fl_twotone = 1 THEN 'two-tone' "
                "WHEN fl_paired = 1 THEN 'paired' "
                "WHEN fl_conn = 1 THEN 'connected' ELSE 'dry' END", str),
    "stack": ("CASE WHEN eff_bb < 40 THEN 'short (<40bb)' "
              "WHEN eff_bb < 100 THEN 'medium (40-99bb)' "
              "WHEN eff_bb < 200 THEN 'deep (100-199bb)' "
              "ELSE 'very deep (200bb+)' END", lambda k: (
                  ["short (<40bb)", "medium (40-99bb)",
                   "deep (100-199bb)", "very deep (200bb+)"]
                  .index(k) if k in ("short (<40bb)", "medium (40-99bb)",
                                     "deep (100-199bb)",
                                     "very deep (200bb+)") else 99)),
    "class": ("player_class", str),
    "vs_class": ("vs_class", str),
    # When. The hour is the site's hour, and the report heading says so.
    "hour": ("CAST(strftime('%H', played_at) AS INT)", lambda k: int(k or 0)),
    "weekday": ("CASE strftime('%w', played_at) "
                + " ".join(f"WHEN '{n}' THEN '{d}'" for d, n in WEEKDAYS.items())
                + " END", lambda k: int(WEEKDAYS.get(k, 99))),
    # How far into the sitting, and how long the sitting was, in whole
    # hours -- the buckets Hand2Note's session-length finding is stated in.
    "session_hour": ("CAST(session_min / 60 AS INT) + 1",
                     lambda k: int(k or 0)),
    "session_len": ("CASE WHEN session_len < 60 THEN 'under 1h' "
                    "WHEN session_len < 120 THEN '1-2h' "
                    "WHEN session_len < 180 THEN '2-3h' "
                    "WHEN session_len < 240 THEN '3-4h' "
                    "WHEN session_len < 300 THEN '4-5h' "
                    "ELSE '5h+' END", lambda k: (
                        ["under 1h", "1-2h", "2-3h", "3-4h", "4-5h", "5h+"]
                        .index(k) if k in ("under 1h", "1-2h", "2-3h",
                                           "3-4h", "4-5h", "5h+") else 99)),
    "tables": ("tables_now", lambda k: int(k or 0)),
    "hand": ("made", str),
    "flush_draw": ("fd", str),
    "straight_draw": ("sd", str),
}

SMART_REPORTS = {
    "3-bet pots": ("--pot", "3bet"),
    "Flop c-bets": ("--street", "flop", "--pfa", "--facing", "check"),
    "Blind defense": ("--pos", "SB,BB", "--facing", "open"),
    "River bets": ("--street", "river", "--facing", "bet"),
    "All-in decisions": ("--allin",),
}

# Where a filter somebody built keeps its name.
#
# The five above are the reports this project guessed at. The sixth is
# whatever you were looking at last Tuesday, and there is no list of five
# that contains it -- the same argument that put custom stats in
# `stats.json`, and the same answer. A saved filter IS a preset, so it goes
# into the same namespace: `--preset`, the window's report box and
# `preset_argv` all pick it up without being told anything.
SAVED = Path(__file__).parent / "filters.json"

# Saved filters that no longer build, kept rather than dropped, for the
# reason `stats.BROKEN` exists: a report that quietly stops appearing in the
# box is a report whose absence nobody notices.
UNREADABLE = []


def raw_saved(path=None):
    """The saved filters exactly as they are on disk, unvalidated."""
    path = Path(path or SAVED)
    if not path.exists():
        return {}
    return {str(k): list(v) for k, v in
            json.loads(path.read_text(encoding="utf-8")).items()}


def write_saved(saved, path=None):
    """Write them back, indented, because people read this file."""
    path = Path(path or SAVED)
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")


def saved_filters(path=None):
    """
    The saved filters, each one proved to still build.

    Proved rather than trusted: a filter is a list of flags, and a flag
    renamed here turns every report that used it into a stack trace over
    somebody's window. The ones that fail are named in `UNREADABLE` and the
    rest are usable, because one bad entry taking the report box with it is
    the worse failure of the two.
    """
    UNREADABLE[:] = []
    try:
        raw = raw_saved(path)
    except (OSError, ValueError) as e:
        UNREADABLE.append(("(the file itself)", str(e)))
        return {}
    out = {}
    for name, argv in raw.items():
        if name in SMART_REPORTS:
            UNREADABLE.append((name, "shadows a report that is built in"))
            continue
        try:
            build(list(argv))
        except SystemExit as e:
            UNREADABLE.append((name, str(e)))
            continue
        out[name] = list(argv)
    return out


def reports(path=None):
    """Every named report: the ones built in, then the ones saved here."""
    known = dict(SMART_REPORTS)
    known.update(saved_filters(path))
    return known


def preset_argv(name):
    """Return the validated situation filters for a named report."""
    known = reports()
    try:
        return list(known[name])
    except KeyError:
        raise SystemExit(f"unknown report {name!r} -- choose from: "
                         f"{', '.join(known)}")


def situation_only(argv):
    """
    The filter flags alone, with the reporting options taken out.

    `--by`, `--show`, `--min` and the rest say how to draw an answer, not
    which rows it is about. A report that remembered `--show vpip,pfr` would
    silently rewrite the columns of every view it was opened in, which reads
    as the window having forgotten what you asked for rather than as the
    report having an opinion.
    """
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in OPTIONS:
            i += 2
        elif a in VALUE_FLAGS:
            out += argv[i:i + 2]
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def save_filter(name, argv, path=None, db=None):
    """
    The filter on the screen, saved under a name beside the built-in reports.

    Returns (n, description): how many decisions it selects, and the filter
    in words. The count is there for the same reason `--define` counts a new
    stat -- a report that matches nothing saves exactly as happily as one
    that matches everything, and the moment to find out is now.

    A cohort is refused rather than saved. A cohort chooses PEOPLE and a
    report describes a SITUATION, and one that quietly carried "regs with
    over 500 hands" inside it would describe a different population from the
    one its own name claims. It is also the wrong shape: presets are
    expanded after the cohort flags have already been taken off the command
    line, so a saved one would arrive too late to be read at all.
    """
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("a report needs a name")
    if name in SMART_REPORTS:
        raise ValueError(f"{name!r} is one of the reports built in, and two "
                         f"things under one name in the report box is one "
                         f"thing nobody can pick")
    if "--cohort" in argv:
        raise ValueError("a player cohort cannot be part of a report -- a "
                         "cohort chooses people and a report describes a "
                         "situation. Save the situation and pick the players "
                         "beside it.")
    keep = situation_only(argv)
    if not keep:
        raise ValueError("that filter is empty, and a report of every hand "
                         "is the view you already get without one")
    where, described, _parts = build(keep)
    saved = raw_saved(path)
    saved[name] = keep
    write_saved(saved, path)
    con = sqlite3.connect(str(db or DB))
    try:
        n = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    finally:
        con.close()
    return n, described


def forget_filter(name, path=None):
    """Remove a saved report. The ones built in are not the file's to remove."""
    if name in SMART_REPORTS:
        raise ValueError(f"{name!r} is built in, and lives in this file")
    saved = raw_saved(path)
    if name not in saved:
        raise ValueError(f"no saved report called {name!r}")
    del saved[name]
    write_saved(saved, path)

# Eight columns is what fits and what gets read. Anything else is available
# with --show.
DEFAULT_COLUMNS = ["vpip", "pfr", "rfi", "threebet", "fold_to_3bet",
                   "cbet_flop", "fold_to_cbet", "flop_agg"]

BOARDS = {
    "mono": "fl_mono = 1",
    "twotone": "fl_twotone = 1",
    "rainbow": "fl_mono = 0 AND fl_twotone = 0",
    "paired": "fl_paired = 1",
    "unpaired": "fl_paired = 0",
    "connected": "fl_conn = 1",
    "dry": "fl_conn = 0 AND fl_paired = 0",
    "ace": "fl_hi = 'A'",
    "broadway": "fl_hi IN ('A','K','Q','J','T')",
    "low": "fl_hi IN ('2','3','4','5','6','7','8','9')",
}

# What a later card did. `--board` describes the flop, which arrives all at
# once; these describe a single card arriving on a board that was already
# there, which is a different kind of fact and needs its own words.
#
# "Straight" means different things on the two streets and deliberately so:
# on the turn it is the board coming to one card off a straight, and on the
# river it is that card arriving. Those are the events that matter on each,
# and one definition covering both would describe neither.
RUNOUT = {
    "over": "{p}_over = 1",
    "pair": "{p}_pair = 1",
    "flush": "{p}_flush = 1",
    "straight": "{p}_straight = 1",
    "brick": "{p}_over = 0 AND {p}_pair = 0 AND {p}_flush = 0 "
             "AND {p}_straight = 0",
}
RUNOUT_FLAG = {"--turn-card": "tn", "--river-card": "rv"}


# A statistic and a hand filter are the same object seen twice. A `Stat` is
# a chance and an action -- "the times a continuation bet was possible" and
# "the times one was made" -- so the filter "hands where a continuation bet
# was made" is simply both of them at once. Every stat in the registry is
# therefore a one-click filter already, named the way a player names it, and
# a new stat brings a new filter with it for nothing.
#
# The negatives are worth naming separately because "did not" is a different
# question from "did", and a player asking about missed continuation bets is
# not asking about continuation bets.
NEGATIVES = {
    "cbet_flop": "Missed Continuation Bet Flop",
    "cbet_turn": "Missed 2nd Barrel Turn",
    "threebet": "Did Not 3-Bet",
    "steal": "Did Not Steal",
    "fold_to_cbet": "Continued vs Continuation Bet",
    "fold_to_3bet": "Continued vs 3-Bet",
}


def quick_filters():
    """Named one-click filters, in the order they should be shown."""
    out = []
    for st in STATS:
        if st.source != "d":
            continue
        out.append({"group": st.group, "label": st.label, "key": st.key,
                    "sql": f"({st.chance}) AND ({st.action})",
                    "note": st.note})
        if st.key in NEGATIVES:
            out.append({"group": st.group, "label": NEGATIVES[st.key],
                        "key": st.key + "_not",
                        "sql": f"({st.chance}) AND NOT ({st.action})",
                        "note": f"had the chance and did not"})
    return out


def quick_by_key():
    """
    The quick filters by name, worked out each time rather than held.

    A saved stat brings a one-click filter with it, and saved stats can
    arrive after this module is imported -- the packaged application loads
    them once it knows where the user's files are, which is not where the
    code is. A dict built at import time would have been built before they
    existed, so `--quick` would refuse the very filter the window had just
    offered.
    """
    return {q["key"]: q for q in quick_filters()}


# The switches that say what the player DID rather than what they could have
# done. A filter is a situation; these are outcomes, and a stat defined over
# one of them is 100% by construction -- see `define_stat`.
ACTION_FLAGS = ("--aggressive", "--allin", "--quick")


def define_stat(argv, key, label=None, do="aggressive", per="decision",
                path=None):
    """
    A filter and an action, saved together under a name.

    This is the whole of the custom-stat feature and it is small, because
    both halves already existed: `build` turns the flags into the chance,
    and `stats.ACTIONS` names the action. What the name adds is the one
    thing a query cannot do. `--show`, `--by position` and the opponent
    leaderboard all pick their columns by key, so an unnamed filter can be
    looked at once and a named one can be compared -- across positions,
    between players, against the pool, over months.

    The filter must not contain the action it is about to count.
    `--aggressive --define x --do bet` saves a stat that reads 100% for
    ever, because its chance is already the times somebody bet -- and it
    looks exactly like a stat rather than like a mistake, which is why the
    flags that would do it are refused here rather than explained in a
    document.

    Returns (n, k) from `stats.define`, which has already tested the
    definition against the database.
    """
    banned = [a for a in argv if a in ACTION_FLAGS]
    if banned:
        raise SystemExit(
            f"{' and '.join(banned)} says what the player did, and a stat's "
            f"chance is what they could have done -- a stat defined over it "
            f"would read 100% by construction. Say it with --do instead.")
    if do not in stats.ACTIONS:
        raise SystemExit(f"unknown action {do!r} -- one of: "
                         f"{', '.join(stats.ACTIONS)}")
    chance, described, _parts = build(argv)
    action, doing = stats.ACTIONS[do]
    try:
        return stats.define(key, label, chance, action, per=per,
                            note=f"{described}, {doing}", path=path, db=DB)
    except ValueError as e:
        raise SystemExit(str(e))


def q(value):
    """A value as a SQL literal, with quotes doubled so a name cannot break out."""
    return "'" + str(value).replace("'", "''") + "'"


def build(argv):
    """
    The command line as one WHERE clause over `decisions`.

    Returns the clause and a human description of it, because a report whose
    heading does not say what was filtered is a report that will eventually
    be read as though it covered everything.
    """
    parts, described = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in SWITCHES:
            parts.append(SWITCHES[a])
            described.append(a.lstrip("-"))
            i += 1
            continue
        if a in OPTIONS:
            i += 2
            continue
        if a in VALUE_FLAGS:
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value")
            v = argv[i + 1]
            i += 2
            if a == "--where":
                parts.append("(" + v + ")")
                described.append(v)
                continue
            if a == "--quick":
                known = quick_by_key()
                for name in v.split(","):
                    if name not in known:
                        raise SystemExit(f"unknown quick filter {name!r}")
                    parts.append("(" + known[name]["sql"] + ")")
                    described.append(known[name]["label"])
                continue
            if a in LINE_FLAGS:
                # Normalised rather than taken as typed, because the columns
                # are stored in one case and nobody holds shift for half a
                # filter. A pattern with no wildcard means exactly that line
                # and not a prefix of it -- "XBC" is a flop that ended, not
                # every flop that started that way.
                pattern = lines.normalise(v)
                plain, sized = LINE_FLAGS[a]
                with_sizes = any(c in lines.BUCKETS for c in pattern)
                parts.append(f"{sized if with_sizes else plain} "
                             f"GLOB {q(pattern)}")
                described.append(f"{a.lstrip('-')} {pattern}"
                                 + (" (sizes)" if with_sizes else ""))
                continue
            if a in RUNOUT_FLAG:
                pre = RUNOUT_FLAG[a]
                for name in v.split(","):
                    if name not in RUNOUT:
                        raise SystemExit(
                            f"unknown runout {name!r} -- one of: "
                            f"{', '.join(RUNOUT)}")
                    parts.append("(" + RUNOUT[name].format(p=pre) + ")")
                described.append(f"{a.lstrip('-')} {v}")
                continue
            if a == "--board":
                for name in v.split(","):
                    if name not in BOARDS:
                        raise SystemExit(
                            f"unknown board texture {name!r} -- "
                            f"one of: {', '.join(BOARDS)}")
                    parts.append(BOARDS[name])
                described.append("board " + v)
                continue
            tpl = VALUE_FLAGS[a]
            if a == "--weekday":
                # Names, because nobody remembers that Sunday is 0.
                days = [WEEKDAYS.get(x.strip().lower()[:3], x.strip())
                        for x in v.split(",")]
                bad = [d for d in days if d not in WEEKDAYS.values()]
                if bad:
                    raise SystemExit(f"unknown weekday {bad[0]!r} -- "
                                     f"one of: {', '.join(WEEKDAYS)}")
                parts.append(tpl.format(list=", ".join(q(d) for d in days)))
                described.append(f"{a.lstrip('-')} {v}")
                continue
            if "{lo}" in tpl:
                lo, _, hi = v.partition("-")
                try:
                    lo, hi = float(lo), float(hi or lo)
                except ValueError:
                    raise SystemExit(f"{a} wants a range like 18-23, "
                                     f"not {v!r}") from None
                parts.append(tpl.format(lo=lo, hi=hi))
                described.append(f"{a.lstrip('-')} {v}")
                continue
            if "{list}" in tpl:
                items = ", ".join(q(x.strip()) for x in v.split(","))
                parts.append(tpl.format(list=items))
            elif "{n}" in tpl:
                parts.append(tpl.format(n=float(v)))
            else:
                parts.append(tpl.format(v=q(v)))
            described.append(f"{a.lstrip('-')} {v}")
            continue
        raise SystemExit(f"unknown option {a!r} -- try --help")
    return (" AND ".join(parts) if parts else "1=1",
            ", ".join(described) if described else "everything",
            list(zip(described, parts)))


# Columns that do not exist before the flop, and the reason each one does
# not. A filter combining any of these with "preflop" is not broken -- it is
# asking for something that cannot have happened -- but it returns nothing
# and looks broken, so the reason is kept here to be said out loud.
# Columns that are null when the pot is not heads up, which is a
# legitimate emptiness rather than a mistake and so gets its reason too.
MULTIWAY_NULL = {
    "vs_pos": "there is only an 'opponent' while one player is left in the "
              "pot -- a multiway decision has no single other seat",
    "vs_hero": "there is only an 'opponent' while one player is left in the "
               "pot -- a multiway decision has no single other seat",
    "opener_pos": "nobody opened -- that is a limped pot",
}
# Columns that are only filled where the cards were actually shown. This is
# the emptiness most likely to be mistaken for a broken filter: three
# quarters of decisions have no cards to name a hand from, and a report that
# does not say so reads as though nobody ever had a set.
UNSHOWN_NULL = {
    "made": "the hand is only named where the cards were shown -- all of "
            "Ignition's, and 23% of ACR's",
    "kicker": "there is no kicker to judge without the cards",
    "fd": "a draw cannot be seen in a hand that was never shown",
    "sd": "a draw cannot be seen in a hand that was never shown",
}
ONLY_POSTFLOP = {
    "tn_": "the turn had not come",
    "rv_": "the river had not come",
    "made": "a hand becomes something when the flop comes",
    "fd": "there is nothing to draw to before the flop",
    "sd": "there is nothing to draw to before the flop",
    "is_ip": "who acts last is only settled once the flop is out",
    "is_pfa": "there is no preflop aggressor until preflop is over",
    "fl_mono": "the flop had not come",
    "fl_twotone": "the flop had not come",
    "fl_paired": "the flop had not come",
    "fl_conn": "the flop had not come",
    "fl_hi": "the flop had not come",
}
# The two ladders use different words, and a word from one never appears in
# the other.
PREFLOP_FACING = ("unopened", "open", "3bet", "4bet", "5bet+")
POSTFLOP_FACING = ("check", "bet", "raise")


def why_empty(con, parts):
    """
    Why a filter matched nothing, in a sentence.

    An empty table is the least informative thing an interface can show, and
    most of the empty ones here are not mistakes: a preflop decision has no
    position-to-act and no flop texture, and "facing a bet" is a postflop
    word where "facing an open" is a preflop one. Every one of those is a
    reasonable thing to click and none of them can return a row. So rather
    than a blank page, the filter is taken apart and the first term or pair
    that kills it is named.
    """
    if not parts:
        return "the database is empty"
    count = lambda w: con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {w}").fetchone()[0]

    alone = [(label, sql, count(sql)) for label, sql in parts]
    dead = [f"'{label}'" for label, _sql, n in alone if not n]
    if dead:
        return f"{', '.join(dead)} matches nothing at all in this database"

    for i, (l1, s1, _n1) in enumerate(alone):
        for l2, s2, _n2 in alone[i + 1:]:
            if count(f"({s1}) AND ({s2})"):
                continue
            hint = ""
            pre = "preflop" in (s1 + s2)
            for col, reason in MULTIWAY_NULL.items():
                if col in s1 + s2:
                    hint = f" -- {reason}"
                    break
            for col, reason in ONLY_POSTFLOP.items():
                if col in s1 + s2 and pre:
                    hint = f" -- {reason}"
                    break
            for col, reason in UNSHOWN_NULL.items():
                if not hint and col in s1 + s2:
                    hint = f" -- {reason}"
                    break
            if not hint and "pot_type" in s1 + s2 and pre:
                # A pot is only "limped" once it is settled that nobody
                # raised, which is a fact about the pot AFTER preflop. While
                # preflop is still happening the pot is "unopened".
                hint = (" -- a pot is not limped until preflop is over; "
                        "during it the pot type is 'unopened'")
            if not hint and "facing" in s1 + s2:
                if any(f"'{w}'" in s1 + s2 for w in POSTFLOP_FACING) and pre:
                    hint = (" -- 'bet', 'raise' and 'check' describe postflop "
                            "streets; preflop uses 'open', '3bet', '4bet'")
                elif any(f"'{w}'" in s1 + s2 for w in PREFLOP_FACING):
                    hint = (" -- 'open', '3bet' and '4bet' describe preflop; "
                            "after the flop it is 'bet', 'raise', 'check'")
            return f"'{l1}' and '{l2}' never occur together{hint}"

    return ("no two of these conflict on their own, but together they select "
            "nothing -- drop one at a time to find the pair that does")


def stats_of(con, where):
    """
    Every stat that has anything to say under this filter, as data.

    Separated from the printing because a second front end wants the same
    numbers in a different shape, and two front ends computing them their own
    way is how they come to disagree.
    """
    n_dec = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    # All of them in one pass. Asking each stat its own question meant
    # thirty scans of the same rows to fill one table.
    counted = stat_rates(con, where)
    rows = []
    for s in STATS:
        # A spots-sourced stat cannot see a decision's conditions -- there is
        # no street or position-to-act in a per-hand row -- so it is skipped
        # rather than silently answered over a different population.
        if s.source == "s":
            continue
        n, k = counted[s.key]
        if not n:
            continue
        p, lo, hi = wilson(k, n)
        rows.append({"key": s.key, "label": s.label, "group": s.group,
                     "note": s.note, "n": n, "k": k, "pct": 100 * p,
                     "band": 100 * (hi - lo) / 2})
    return n_dec, rows


def show_versus(con, argv_a, argv_b, min_n=30, only=None):
    """
    Two filters, every stat, and whether the gap between them is real.

    The point of this is the last two columns. A rate on its own invites the
    reader to eyeball two numbers and decide; what settles it is the
    interval on the DIFFERENCE, which is not the same question as whether
    the two rates' own intervals overlap and is much less strict. And since
    thirty stats are compared at once, one of them will clear 5% with
    nothing going on, so the p-values are corrected for having asked thirty
    questions rather than one.

    Where nothing is significant the footer says how big a difference this
    much data COULD have found. "No difference" and "not enough hands to
    see one" are different findings, and the second is usually the true one.
    """
    where_a, label_a, _pa = build([a for a in argv_a if a not in OPTIONS
                                   and not _is_value_of(argv_a, a)])
    where_b, label_b, _pb = build(argv_b)
    a_counts = stat_rates(con, where_a)
    b_counts = stat_rates(con, where_b)

    rows, ps = [], []
    for st in STATS:
        if st.source == "s" or st.key not in a_counts:
            continue
        # `--show` is not cosmetic here, it is the difference between an
        # exploratory scan and a test. Comparing thirty stats and reporting
        # the best one is how a finding gets manufactured; naming the stat
        # first and comparing only that is how one gets established. The
        # correction charges by how many questions were actually asked, so
        # asking one is worth far more than asking thirty and picking.
        if only and st.key not in only:
            continue
        n1, k1 = a_counts[st.key]
        n2, k2 = b_counts.get(st.key, (0, 0))
        # Too thin to compare, and worse than useless: an iso-raise on
        # four chances contributes a meaningless row AND makes the
        # correction for multiple questions stricter for every other stat.
        if n1 < min_n or n2 < min_n:
            continue
        d, lo, hi, pv = difference(k1, n1, k2, n2)
        rows.append([st, n1, k1, n2, k2, d, lo, hi, pv])
        ps.append((st.key, pv))
    adjusted_p = holm(ps)

    print(f"\nA: {label_a}")
    print(f"B: {label_b}")
    print("=" * max(len(label_a), len(label_b), 40))
    print(f"{'':24} {'A':>17} {'B':>17} {'A - B':>20}")
    real = []
    for st, n1, k1, n2, k2, d, lo, hi, pv in rows:
        ap = adjusted_p.get(st.key)
        mark = "  <-- real" if ap is not None and ap < 0.05 else ""
        if mark:
            real.append(st.label)
        print(f"{st.label[:23]:24} "
              f"{100 * k1 / n1:6.1f}% n={n1:<7,} "
              f"{100 * k2 / n2:6.1f}% n={n2:<7,} "
              f"{100 * d:+6.1f} [{100 * lo:+5.1f},{100 * hi:+5.1f}]{mark}")

    print()
    print(f"{len(rows)} stat{'' if len(rows) == 1 else 's'} compared, "
          f"both sides at n >= {min_n}."
          + ("" if only else "  Name one with --show to test it on its own."))

    # Two lists, because they answer different questions and conflating them
    # is how a report comes to be confidently wrong. The first is what the
    # data says about each stat on its own. The second is what survives the
    # fact that thirty questions were asked, and only the second is a
    # finding.
    apart = [(st, d, lo, hi, adjusted_p.get(st.key))
             for st, _n1, _k1, _n2, _k2, d, lo, hi, _p in rows
             if lo is not None and (lo > 0 or hi < 0)]
    if apart:
        print("\nDifferences that clear zero on their own:")
        for st, d, lo, hi, ap in apart:
            verdict = ("survives" if ap is not None and ap < 0.05
                       else f"but not one of {len(rows)}")
            print(f"  {st.label[:24]:26} {100 * d:+6.1f} points   "
                  f"corrected p = {ap:.3f}   {verdict}")
    if real:
        print(f"\nReal: {', '.join(real)}")
    else:
        floors = [detectable(n1, n2, k1 / n1)
                  for _st, n1, k1, n2, k2, *_r in rows if n1 and n2]
        if floors:
            floor = 100 * sorted(floors)[len(floors) // 2]
            print(f"\nNothing survives being one of {len(rows)} questions "
                  f"asked at once.")
            print(f"A difference has to be about {floor:.0f} points before "
                  f"this much data could see it, so \"no difference\" here "
                  f"means")
            print("\"no difference big enough to find\" -- which is a "
                  "statement about the sample, not about poker.")


def _is_value_of(argv, token):
    """True if this token is the value belonging to the option before it."""
    i = argv.index(token)
    return i > 0 and argv[i - 1] in OPTIONS


def range_of(con, where):
    """
    What the range that got here actually holds, and how much of it is air.

    This is the question a frequency cannot answer. "He bets the river 40%"
    is a number about him; "and 55% of it is a hand that cannot call" is a
    number about what to do. Hand2Note draws it as the postflop diagram, and
    it needs nothing this database has not had since `strength.py`: the
    hands are already named, so a range is a GROUP BY over the filter.

    **It is a range of the hands that were SEEN.** Ignition shows every hand
    at showdown including the folds, and ACR shows 23% -- so on ACR this
    describes the hands that got to showdown, which is a stronger set than
    the range that reached the spot. The number that says so is returned
    beside the breakdown and is not optional: a diagram of a quarter of a
    range, presented as the range, is worse than no diagram.
    """
    total = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    seen = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({where}) "
        f"AND made IS NOT NULL").fetchone()[0]
    counts = dict(con.execute(
        f"SELECT made, COUNT(*) FROM decisions WHERE ({where}) "
        f"AND made IS NOT NULL GROUP BY made").fetchall())

    rows = []
    for name in strength.ORDER:
        n = counts.get(name, 0)
        if not n:
            continue
        rows.append({"made": name, "n": n,
                     "pct": 100.0 * n / seen if seen else 0.0,
                     "weak": name in strength.WEAK})
    weak = sum(r["n"] for r in rows if r["weak"])

    # Draws are counted separately and deliberately overlap the categories
    # above: a hand is one made hand and may also be drawing, and adding
    # "flush draw" to the list of made hands would make the column stop
    # summing to a hundred while quietly reclassifying every pair that
    # happens to have one.
    draws = []
    for label, sql in (("a flush draw", "fd IS NOT NULL"),
                       ("a straight draw", "sd IS NOT NULL"),
                       ("both at once", "fd IS NOT NULL AND sd IS NOT NULL"),
                       ("weak, but drawing",
                        f"made IN ({', '.join(q(w) for w in strength.WEAK)}) "
                        f"AND (fd IS NOT NULL OR sd IS NOT NULL)")):
        n = con.execute(f"SELECT COUNT(*) FROM decisions WHERE ({where}) "
                        f"AND made IS NOT NULL AND ({sql})").fetchone()[0]
        if n:
            draws.append({"label": label, "n": n,
                          "pct": 100.0 * n / seen if seen else 0.0})

    return {"rows": rows, "draws": draws, "n": seen, "total": total,
            "weak": 100.0 * weak / seen if seen else 0.0,
            "strong": 100.0 * (seen - weak) / seen if seen else 0.0}


def show_range(con, where, label, parts=()):
    """The range breakdown, printed."""
    out = range_of(con, where)
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    if not out["n"]:
        print("no hand in this filter was ever shown, so there is no range "
              "to break down.")
        if parts:
            why = why_empty(con, parts)
            if why:
                print(why)
        return
    print(f"{out['n']:,} of {out['total']:,} decisions had cards to read "
          f"({100 * out['n'] / out['total']:.0f}%)\n")
    for r in out["rows"]:
        bar = "#" * int(round(r["pct"] / 2))
        print(f"  {r['made']:14} {r['pct']:5.1f}%  {r['n']:6,}  "
              f"{'weak' if r['weak'] else '    '}  {bar}")
    print(f"\n  {'WEAK':14} {out['weak']:5.1f}%   -- hands that cannot call")
    print(f"  {'STRONG':14} {out['strong']:5.1f}%")
    if out["draws"]:
        print("\n  and, overlapping the above:")
        for d in out["draws"]:
            print(f"    {d['label']:20} {d['pct']:5.1f}%  {d['n']:6,}")
    print("\nThis is the range that was SEEN. Ignition shows every hand "
          "including folds; ACR shows 23%,")
    print("so an ACR-heavy filter here describes the hands that reached "
          "showdown, which is the stronger half.")


# The 13x13 chart, in the order every range chart has ever been drawn:
# aces top left, suited above the diagonal, offsuit below. Descending,
# because a chart with the deuces in the corner is not one anybody can read.
# `population.py` draws the same shape from its own hardcoded SQL over
# `spots`; this one is over `decisions` and takes the whole filter
# vocabulary, and that copy is the next one to retire.
CHART_RANKS = "AKQJT98765432"


def combo_at(i, j):
    """The combo in row i, column j of the chart."""
    hi, lo = CHART_RANKS[i], CHART_RANKS[j]
    if i == j:
        return hi + hi
    return (hi + lo + "s") if i < j else (lo + hi + "o")


def chart_of(con, where, stat=None, min_n=3):
    """
    The preflop chart: what the range that reached this spot is made of.

    `range_of` answers what the hands BECAME -- top pair, a flush draw --
    which is the postflop half of the same question. This is the other half
    and the one a chart is for: not "he bets the river 40%" and not "55% of
    it cannot call", but which 169 squares that 40% is.

    Two charts, from the same shape. With no stat it is the range's
    COMPOSITION -- each combo's share of the hands that got here, which is
    the diagram H2N draws. Given a stat it is that stat per combo -- how
    often each hand 3-bet, folded, barrelled -- which is the chart you read
    to decide whether he can have it.

    **Composition counts each player-hand once, not each decision.** A hand
    that went to the river has four rows in `decisions` and one that folded
    preflop has one, so counting rows would weight the chart towards the
    hands that went furthest and quietly draw a range far stronger than the
    one that actually arrived. The DISTINCT is the whole correctness of this
    function. A stat does not need it: `rates_by` already counts per
    decision or per hand as the stat itself says.

    **And it is the range that was SEEN.** Ignition shows every hand at
    showdown including the folds; ACR shows 23%. The fraction is returned
    beside the chart and every view prints it, because 169 confident squares
    drawn from a quarter of a range is the most convincing wrong picture
    this program can produce.
    """
    total = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat "
        f"FROM decisions WHERE {where})").fetchone()[0]
    seen = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat "
        f"FROM decisions WHERE ({where}) AND combo IS NOT NULL)").fetchone()[0]

    if stat is None:
        rows = con.execute(
            f"SELECT combo, COUNT(*) FROM (SELECT DISTINCT hand_id, seat, "
            f"combo FROM decisions WHERE ({where}) AND combo IS NOT NULL) "
            f"GROUP BY combo").fetchall()
        cells = {c: (n, None) for c, n in rows}
    else:
        stat = BY_KEY[stat] if isinstance(stat, str) else stat
        cells = {c: (n, k) for c, (n, k)
                 in rates_by(con, stat, "combo", where).items() if c}

    return {"mode": "composition" if stat is None else "rate",
            "stat": None if stat is None else stat.label,
            "cells": cells, "seen": seen, "total": total, "min_n": min_n,
            "peak": max((n for n, _k in cells.values()), default=0)}


def show_chart(con, where, label, stat=None, parts=(), min_n=3):
    """The chart, as 169 numbers, in the shape it is always drawn in."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    g = chart_of(con, where, stat, min_n)
    if not g["total"]:
        print("  " + why_empty(con, parts))
        return
    if not g["seen"]:
        print(f"  {g['total']:,} player-hands match and none of them showed "
              f"cards, so there is no range to draw.")
        shows = ", ".join(sites.revealing())
        print(f"  (Only {shows} shows every hand, folds included; "
              f"--site {sites.revealing()[0]} is the filter that fixes this.)")
        return

    share = 100.0 * g["seen"] / g["total"]
    print(f"{g['seen']:,} of {g['total']:,} player-hands showed cards "
          f"({share:.1f}%)")
    if g["mode"] == "composition":
        print("each cell is that combo's share of the range, in percent\n")
    else:
        print(f"each cell is {g['stat']}, in percent, for that combo\n")

    print("      " + " ".join(f"{r:>4}" for r in CHART_RANKS))
    for i, hi in enumerate(CHART_RANKS):
        row = []
        for j in range(len(CHART_RANKS)):
            n, k = g["cells"].get(combo_at(i, j), (0, None))
            if g["mode"] == "composition":
                # No minimum here, and deliberately: a combo dealt twice
                # really is 0.1% of the range. The minimum belongs to a
                # RATE, where two hands cannot say how often anything is
                # done.
                row.append("   ." if not n else f"{100.0 * n / g['seen']:4.1f}")
            else:
                row.append("   ." if n < min_n else f"{100.0 * k / n:4.0f}")
        print(f"  {hi:>2}  " + " ".join(row))

    if g["mode"] == "composition":
        top = sorted(g["cells"].items(), key=lambda kv: -kv[1][0])[:6]
        print("\n  most of it: " + ", ".join(
            f"{c} {100.0 * n / g['seen']:.1f}%" for c, (n, _k) in top))
    else:
        print(f"\n  (blank = dealt fewer than {min_n} times in this spot -- "
              f"one hand dealt twice is not a frequency)")


def sessions_of(con, where):
    """
    The sittings that contain what the filter selected, newest first.

    A session is the unit you actually remember -- "Tuesday night" -- and
    a filter's hands usually belong to a few of them. Each row carries the
    whole session's figures beside how many of its hands the filter hit, so
    a bad night and a filter that happens to land on it are told apart.
    """
    keys = ("session_id", "site", "started", "minutes", "hands", "tables",
            "net_bb", "ev_bb", "bb100", "ev100", "matched")
    return [dict(zip(keys, r)) for r in con.execute(f"""
        SELECT s.session_id, s.site, s.started, s.minutes, s.hands,
               s.tables, s.net_bb, s.ev_bb, s.bb100, s.ev100,
               COUNT(DISTINCT d.hand_id)
        FROM sessions s JOIN decisions d ON d.session_id = s.session_id
        WHERE ({where})
        GROUP BY s.session_id ORDER BY s.started DESC""")]


def show_sessions(con, where, label, parts=()):
    rows = sessions_of(con, where)
    print()
    print(f"filter: {label}")
    print("=" * (len(label) + 8))
    if not rows:
        print("no session holds a hand this filter selects")
        return
    print(f"{'#':>4} {'site':10} {'started':17} {'mins':>5} {'hands':>6} "
          f"{'hit':>5} {'tbl':>4} {'net bb':>8} {'ev bb':>8} {'bb/100':>7}")
    for r in rows:
        rate = "" if r["bb100"] is None else f"{r['bb100']:+7.1f}"
        print(f"{r['session_id']:4} {r['site']:10} {r['started'][:16]:17} "
              f"{r['minutes']:5.0f} {r['hands']:6,} {r['matched']:5,} "
              f"{r['tables']:4} {r['net_bb']:+8.1f} {r['ev_bb']:+8.1f} {rate}")
    n = sum(r["hands"] for r in rows)
    net = sum(r["net_bb"] for r in rows)
    print()
    print(f"{len(rows)} sessions, {n:,} hands, {net:+,.1f} bb")
    print("The clock is the site's, not yours.")


def show_stats(con, where, label, parts=()):
    """Every stat that has anything to say under this filter."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    n_dec, rows = stats_of(con, where)
    print(f"{n_dec} decisions match\n")
    if not n_dec:
        print("  " + why_empty(con, parts))
        return
    last = None
    for r in rows:
        if r["group"] != last:
            print(f"  [{r['group']}]")
            last = r["group"]
        thin = " ?" if r["n"] < 30 else "  "
        print(f"  {r['label']:22} {r['pct']:6.1f}% "
              f"{'+/-%.0f' % r['band']:>7}{thin} n={r['n']:<6d}")
    if not rows:
        print("  no stat has a chance to occur inside this filter.")
        print("  (asking for a preflop stat inside --street flop does this)")


# The four lines every tracker draws, and what each one is for.
#
#   green   every big blind won or lost. The bottom line.
#   blue    the part of it won at showdown.
#   red     the part won without one -- pots taken by betting.
#   yellow  green again, with all-in pots scored by what they were worth
#           rather than by what the deck did afterwards.
#
# Blue and red add up to green exactly: a hand either reached a showdown or
# it did not. Read apart they say different things -- a winning red line
# with a losing blue one is somebody who takes pots away and pays off when
# called, and the reverse is somebody too passive to win without a hand.
LINES = [
    ("total", "#22a35a", "every bb won or lost"),
    ("showdown", "#2f7fd6", "won at showdown"),
    ("nonshowdown", "#d1443c", "won without a showdown"),
    ("allin_ev", "#e0b020", "all-in pots at their equity"),
]

# A graph is drawn once and looked at; half a point of sampling error on one
# preflop all-in cannot move a line anybody can see, so preflop runouts are
# sampled more coarsely here than `equity`'s own default.
GRAPH_SAMPLES = 2000


def adjusted(con, pairs):
    """
    Each hand's result, and its result had the all-ins run at their equity.

    Only the clean case is adjusted: two players left, both hands known, and
    chips in with cards still to come. Side pots and three-handed all-ins are
    left at their actual result and COUNTED, because an EV line that quietly
    drops the hands it cannot price is an EV line about a different set of
    hands than the one beside it.
    """
    from equity import equity

    # An all-in's equity is a fact about a hand that happened: the cards are
    # dealt, the board is known, and nothing about it will ever be different.
    # Recomputing it every time a graph is drawn cost twenty seconds a view,
    # which is most of what the graph cost at all, so it is worked out once
    # and kept. Rebuilding the derived tables does not invalidate it, because
    # what it measures is in the hand history rather than in the derivation.
    con.execute("CREATE TABLE IF NOT EXISTS hand_ev ("
                "hand_id TEXT, seat INT, ev_bb REAL, "
                "PRIMARY KEY (hand_id, seat))")
    known_ev = {(h, st): v for h, st, v in con.execute(
        "SELECT hand_id, seat, ev_bb FROM hand_ev")}
    fresh = []

    rows = con.execute(
        "SELECT s.hand_id, s.seat, s.played_at, s.net_bb, s.wtsd, s.bb, "
        "       s.put_in, h.board "
        "FROM spots s JOIN hands h USING(hand_id) JOIN _sel "
        "  ON _sel.hand_id = s.hand_id AND _sel.seat = s.seat "
        "WHERE s.fmt <> 'MTT' AND s.net_bb IS NOT NULL "
        "ORDER BY s.played_at, s.hand_id").fetchall()

    # Which hands had an all-in with cards to come, and on what street.
    #
    # The street of the LAST ALL-IN, not of an all-in that happens to be the
    # hand's last action. Those are different sets and the difference is most
    # of them: a shove is nearly always called, and the call is what ends the
    # hand, so requiring the all-in to be last found 148 hands where 681 have
    # one. Of the pre-river hands with two known hands still live, it priced
    # 99 and should have priced 293 -- so two thirds of the all-in EV line
    # was simply the actual result, and on this database the two came out
    # equal to the pound and the green line vanished under the yellow one.
    allin_street = dict(con.execute(
        "SELECT hand_id, street FROM decisions d WHERE allin = 1 "
        "AND d.n = (SELECT MAX(n) FROM decisions x "
        "           WHERE x.hand_id = d.hand_id AND x.allin = 1)"
    ).fetchall())

    out, adjusted_n, skipped = [], 0, 0
    cache = {}
    for hid, seat, when, net_bb, wtsd, bb, put_in, board in rows:
        ev_bb = net_bb
        street = allin_street.get(hid)
        if (hid, seat) in known_ev:
            ev_bb = known_ev[(hid, seat)]
            adjusted_n += 1 if ev_bb != net_bb else 0
            out.append((when, net_bb, bool(wtsd), ev_bb))
            continue
        if street and street != "river" and bb:
            live = con.execute(
                "SELECT seat, cards, won FROM spots WHERE hand_id=? "
                "AND folded_on IS NULL AND cards IS NOT NULL", (hid,)).fetchall()
            if len(live) == 2 and all(len(c.split()) == 2 for _, c, _ in live):
                # Whether this player is one of the two comes first. They may
                # have folded long before the other two got it in, and pricing
                # a runout they were not in is work thrown away -- work that
                # was being redone on every view, because a result that is
                # discarded is never cached.
                mine = next((i for i, (st, _, _) in enumerate(live)
                             if st == seat), None)
                pot = sum(r[2] or 0 for r in live) or 0.0
                if mine is not None and pot:
                    take = {"preflop": 0, "flop": 3, "turn": 4}[street]
                    at_allin = " ".join((board or "").split()[:take])
                    key = (tuple(sorted(c for _, c, _ in live)), at_allin)
                    if key not in cache:
                        cache[key] = equity([c for _, c, _ in live], at_allin,
                                            samples=GRAPH_SAMPLES)
                    ev_bb = round((cache[key][mine] * pot - put_in) / bb, 3)
                    adjusted_n += 1
                    fresh.append((hid, seat, ev_bb))
            else:
                skipped += 1
        out.append((when, net_bb, bool(wtsd), ev_bb))
    if fresh:
        con.executemany("INSERT OR REPLACE INTO hand_ev VALUES (?,?,?)", fresh)
        con.commit()
    return out, adjusted_n, skipped


def svg(series, label, note, dark=False):
    """The four lines as one standalone SVG, no library and no dependency."""
    W, H, L, R, T, B = 960, 440, 70, 210, 46, 40
    # The line colours read on either ground; everything around them does not.
    ink, grid, dim, paper = (("#d8dbe0", "#2a2f38", "#8b929c", "#14161a")
                             if dark else
                             ("#111111", "#e6e6e3", "#888888", "#fbfbfa"))
    n = len(series["total"])
    if n < 2:
        return "<p>not enough hands to draw a line</p>"
    lo = min(min(v) for v in series.values())
    hi = max(max(v) for v in series.values())
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = (hi - lo) or 1.0

    def x(i):
        return L + (W - L - R) * i / (n - 1)

    def y(v):
        return T + (H - T - B) * (1 - (v - lo) / span)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="100%" style="max-width:{W}px;font-family:system-ui,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{paper}"/>',
        f'<text x="{L}" y="24" font-size="15" font-weight="600" fill="{ink}">{label}</text>',
        f'<text x="{L}" y="40" font-size="11" fill="{dim}">{note}</text>',
    ]
    # Horizontal guides, and the zero line drawn darker because crossing it
    # is the only thing on this chart that changes the answer.
    for frac in range(5):
        v = lo + span * frac / 4
        parts.append(
            f'<line x1="{L}" y1="{y(v):.1f}" x2="{W - R}" y2="{y(v):.1f}" '
            f'stroke="{grid}"/>'
            f'<text x="{L - 8}" y="{y(v) + 4:.1f}" font-size="11" fill="{dim}" '
            f'text-anchor="end">{v:,.0f}</text>')
    parts.append(f'<line x1="{L}" y1="{y(0):.1f}" x2="{W - R}" y2="{y(0):.1f}" '
                 f'stroke="{dim}" stroke-dasharray="3,3"/>')
    for i, (key, colour, why) in enumerate(LINES):
        pts = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in
                       enumerate(series[key]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                     f'stroke-width="1.8"/>')
        end = series[key][-1]
        ly = T + 14 + i * 34
        parts.append(
            f'<line x1="{W - R + 6}" y1="{ly - 4}" x2="{W - R + 26}" '
            f'y2="{ly - 4}" stroke="{colour}" stroke-width="2.5"/>'
            f'<text x="{W - R + 32}" y="{ly}" font-size="12" fill="{ink}">'
            f'{key.replace("_", " ")}  <tspan font-weight="600">{end:+,.0f}'
            f'</tspan></text>'
            f'<text x="{W - R + 32}" y="{ly + 14}" font-size="10" fill="{dim}">'
            f'{why}</text>')
    parts.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" font-size="11" '
                 f'fill="{dim}" text-anchor="middle">{n:,} hands</text>')
    parts.append(f'<text x="{L - 52}" y="{(T + H - B) / 2}" font-size="11" '
                 f'fill="{dim}" transform="rotate(-90 {L - 52} '
                 f'{(T + H - B) / 2})" text-anchor="middle">big blinds</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def show_graph(con, where, label, out_path="graph.html"):
    """The results graph, over whatever the filter selected."""
    pairs = matching_seats(con, where)
    if not pairs:
        print("nothing matches")
        return
    select_into(con, pairs)
    hands, adj, skipped = adjusted(con, pairs)
    if len(hands) < 2:
        print("not enough hands to draw a line")
        return

    series = {k: [] for k, _, _ in LINES}
    total = sd = nsd = ev = 0.0
    for _when, net, was_sd, ev_net in hands:
        total += net or 0.0
        ev += ev_net or 0.0
        if was_sd:
            sd += net or 0.0
        else:
            nsd += net or 0.0
        series["total"].append(total)
        series["showdown"].append(sd)
        series["nonshowdown"].append(nsd)
        series["allin_ev"].append(ev)

    note = (f"{len(hands):,} hands  ·  {adj} all-in pots scored at equity"
            + (f"  ·  {skipped} left unadjusted (side pots or three-handed)"
               if skipped else ""))
    body = svg(series, label, note)
    path = Path(out_path)
    path.write_text(
        "<!doctype html><meta charset='utf-8'>"
        f"<title>results: {label}</title>"
        "<body style='margin:24px;background:#fff'>" + body + "</body>",
        encoding="utf-8")

    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    print(f"  hands              {len(hands):>10,}")
    for key, _c, why in LINES:
        print(f"  {key.replace('_', ' '):18} {series[key][-1]:>+10,.1f} bb"
              f"   {why}")
    print(f"\n  {adj} all-in pots scored at their equity"
          + (f"; {skipped} left alone (side pots or three-handed)"
             if skipped else ""))
    print(f"  written to {path.resolve()}")
    return str(path.resolve())


# What each action code means when it is read back rather than counted.
VERBS = {"F": "folds", "X": "checks", "C": "calls", "B": "bets",
         "R": "raises to", "A": "all-in"}


def hand_detail(con, hand_id, seat=None):
    """
    One hand, replayed: who sat where, what they held, and what they did.

    A list of hands you cannot open is a dead end, and filtering down to
    fourteen interesting hands is only useful if the fourteenth can then be
    looked at. The pot before each action comes from `decisions` rather than
    being replayed again here -- there is one pot reconstruction in this
    project and this is not a second one.

    Ignition shows every player's cards including the folded ones, so on
    those hands this is the whole deal. ACR shows them at showdown only, and
    the difference is visible: a seat with no cards was not seen, rather than
    dealt nothing.
    """
    con.row_factory = sqlite3.Row
    h = con.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
    if h is None:
        return None
    seats = [dict(r) for r in con.execute(
        "SELECT * FROM seats WHERE hand_id=? ORDER BY seat", (hand_id,))]
    pots = {r["n"]: (r["pot_before"], r["to_call"], r["pot_bb"])
            for r in con.execute(
                "SELECT n, pot_before, to_call, pot_bb FROM decisions "
                "WHERE hand_id=?", (hand_id,))}
    by_seat = {r["seat"]: r for r in seats}

    streets, order = [], {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
    board = (h["board"] or "").split()
    shown = {"preflop": "", "flop": " ".join(board[:3]),
             "turn": " ".join(board[:4]), "river": " ".join(board[:5])}
    for st in ("preflop", "flop", "turn", "river"):
        acts = [dict(r) for r in con.execute(
            "SELECT * FROM actions WHERE hand_id=? AND street=? ORDER BY n",
            (hand_id, st))]
        if not acts:
            continue
        lines = []
        for a in acts:
            pot, to_call, pot_bb = pots.get(a["n"], (None, None, None))
            who = by_seat.get(a["seat"], {})
            size = a["total"] if a["action"] in ("R", "A") and a["total"] \
                else a["amount"]
            lines.append({
                "seat": a["seat"], "position": a["position"],
                "name": who.get("label"), "is_hero": who.get("is_hero"),
                "verb": VERBS.get(a["action"], a["action"]),
                "action": a["action"], "amount": size,
                "pot_before": pot, "to_call": to_call, "pot_bb": pot_bb})
        streets.append({"street": st, "board": shown[st], "actions": lines})

    con.row_factory = None
    return {
        "hand_id": hand_id, "site": h["site"], "played_at": h["played_at"],
        "table": h["table_id"], "fmt": h["fmt"], "sb": h["sb"], "bb": h["bb"],
        "n_players": h["n_players"], "board": h["board"], "pot": h["pot"],
        "rake": (h["rake"] if "rake" in h.keys() else None),
        "focus": seat,
        "seats": [{"seat": r["seat"], "name": r["label"],
                   "position": r["position"], "stack": r["stack"],
                   "cards": r["cards"], "is_hero": r["is_hero"],
                   "won": r["won"], "put_in": (r["posted"] or 0) + (r["invested"] or 0)}
                  for r in seats],
        "streets": streets}


def show_hand(con, hand_id, seat=None):
    """The same hand, for a terminal."""
    d = hand_detail(con, hand_id, seat)
    if d is None:
        print(f"no hand {hand_id!r}")
        return
    stake = f"${d['sb']}/${d['bb']}" if d["bb"] else "-"
    print(f"\n{d['hand_id']}   {d['site']}  {d['fmt']}  {stake}  "
          f"{d['played_at']}  ({d['table']})")
    print("=" * 78)
    for s in d["seats"]:
        mark = "*" if s["seat"] == seat else (">" if s["is_hero"] else " ")
        net = (s["won"] or 0) - s["put_in"]
        print(f" {mark} {s['position'] or '?':4} {(s['name'] or '')[:16]:16} "
              f"{s['stack'] or 0:9.2f}  {s['cards'] or '--':>7}  "
              f"{net:+8.2f}")
    for st in d["streets"]:
        head = st["street"].upper()
        if st["board"]:
            head += f"  [{st['board']}]"
        first = st["actions"][0] if st["actions"] else None
        if first and first["pot_before"] is not None:
            head += f"   pot {first['pot_before']:.2f}"
        print(f"\n{head}")
        for a in st["actions"]:
            amt = f" {a['amount']:.2f}" if a["amount"] else ""
            print(f"    {a['position'] or '?':4} {a['verb']}{amt}")
    if d["pot"]:
        rake = f"  rake {d['rake']:.2f}" if d["rake"] else ""
        print(f"\nTOTAL POT {d['pot']:.2f}{rake}")


def show_report(con, where, label, dim, columns, min_n=30):
    """
    One row per value of the dimension, one column per stat.

    Cells below `min_n` chances are printed but marked, because dropping
    them would hide that the split ran out of data and leaving them unmarked
    would let a 100% on four hands be read as a tendency.
    """
    expr, order = DIMENSIONS[dim]
    print(f"\nfilter: {label}")
    print(f"by {dim}")
    print("=" * (len(label) + 8))

    stats = [BY_KEY[c] for c in columns]
    grid = {s.key: rates_by(con, s, expr, where) for s in stats}
    counts = rates_by(con, BY_KEY["vpip"], expr, where)
    keys = sorted({k for g in grid.values() for k in g},
                  key=lambda k: order(k) if k is not None else "")
    if not keys:
        print("nothing matches")
        return

    width = max(12, min(22, max(len(str(k)) for k in keys) + 1))
    head = f"  {dim[:width - 1]:<{width}}" + "".join(
        f"{BY_KEY[c].label[:9]:>11}" for c in columns)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for k in keys:
        cells = []
        for c in columns:
            n, kk = grid[c].get(k, (0, 0))
            if not n:
                cells.append(f"{'--':>11}")
            else:
                mark = "?" if n < min_n else " "
                cells.append(f"{100 * kk / n:9.1f}%{mark}")
        print(f"  {str(k)[:width - 1]:<{width}}" + "".join(cells))

    # The denominators, on their own line rather than beside every cell:
    # a percentage without its n is not a number anybody should act on, and
    # a table with n beside every cell is a table nobody can read.
    print("\n  chances behind each row (VPIP's denominator):")
    for k in keys:
        n = counts.get(k, (0, 0))[0]
        print(f"    {str(k)[:width - 1]:<{width}} n={n}")
    print("\n  '?' marks a cell measured on fewer than "
          f"{min_n} chances -- ignore it.")


def show_results_by(con, where, label, dim):
    """Money, split by the dimension. The tracking half of a tracker."""
    expr, order = DIMENSIONS[dim]
    print(f"\nfilter: {label}")
    print(f"by {dim}")
    print("=" * (len(label) + 8))
    values = [r[0] for r in con.execute(
        f"SELECT DISTINCT {expr} FROM decisions WHERE ({where}) "
        f"AND ({expr}) IS NOT NULL")]
    if not values:
        print("nothing matches")
        return
    print(f"  {dim:<14}{'hands':>8}{'net bb':>11}{'bb/100':>10}"
          f"{'+/-':>8}")
    print("  " + "-" * 50)
    for v in sorted(values, key=order):
        lit = q(v) if isinstance(v, str) else str(v)
        pairs = matching_seats(con, f"({where}) AND ({expr}) = {lit}")
        if not pairs:
            continue
        con.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
        con.execute("DELETE FROM _sel")
        con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)
        n, net = con.execute(
            "SELECT COUNT(*), SUM(s.net_bb) FROM spots s JOIN _sel "
            "ON _sel.hand_id = s.hand_id AND _sel.seat = s.seat "
            "WHERE s.fmt <> 'MTT'").fetchone()
        if not n:
            continue
        # 1170/sqrt(n) is the 95% error on a win rate, from one hand's
        # standard deviation of about 11.7bb. It is printed beside every row
        # because it is usually larger than the differences between them.
        print(f"  {str(v)[:14]:<14}{n:>8}{net or 0:>11.1f}"
              f"{100 * (net or 0) / n:>10.1f}{1170 / n ** 0.5:>8.0f}")


def matching_seats(con, where):
    """The (hand, seat) pairs that had a decision matching the filter."""
    return con.execute(
        f"SELECT DISTINCT hand_id, seat FROM decisions WHERE {where}"
    ).fetchall()


def select_into(con, pairs):
    """
    Park a set of (hand, seat) pairs in a temp table to join against.

    The index is not an optimisation, it is the difference between the graph
    taking half a second and taking half a minute: without it every join
    against this table is a scan, and the graph joins it to spots and hands
    for every one of eleven thousand rows.
    """
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _sel (hand_id TEXT, seat INT)")
    con.execute("DELETE FROM _sel")
    con.executemany("INSERT INTO _sel VALUES (?,?)", pairs)
    con.execute("CREATE INDEX IF NOT EXISTS _sel_ix ON _sel(hand_id, seat)")
    con.execute("ANALYZE _sel")


def select_cohort(con, spec):
    """
    Park a cohort's players in a temp table for every report to join against.

    Built the same way as `select_into` above, and for the same reason: a
    second call on one connection used to raise "table _cohort already
    exists". Nothing does that today because every view opens its own
    connection, which makes it a trap rather than a bug -- the first caller
    to reuse one would meet it, and would have no reason to look here.
    """
    conditions, site, klass, durable = spec
    rows = players.cohort(con, conditions, site, klass, durable)
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _cohort "
                "(site TEXT, player TEXT)")
    con.execute("DELETE FROM _cohort")
    con.executemany("INSERT INTO _cohort VALUES (?, ?)",
                    [(row[0], row[1]) for row in rows])
    con.execute("CREATE INDEX IF NOT EXISTS _cohort_ix "
                "ON _cohort(site, player)")
    return len(rows)


def results_of(con, pairs):
    """The money over a set of (hand, seat) pairs, tournaments excluded."""
    select_into(con, pairs)
    n, net_bb, money, saw, wtsd, wwsf, mean, msq = con.execute(
        "SELECT COUNT(*), SUM(s.net_bb), SUM(s.won - s.put_in), "
        "       SUM(s.saw_flop), SUM(s.wtsd), SUM(s.wwsf), "
        "       AVG(s.net_bb), AVG(s.net_bb * s.net_bb) "
        "FROM spots s JOIN _sel ON _sel.hand_id = s.hand_id "
        "AND _sel.seat = s.seat WHERE s.fmt <> 'MTT'").fetchone()
    if not n:
        return None
    # The error on bb/100 is measured from these hands rather than taken
    # as 1170/sqrt(n), which assumed every spot's hand has an 11.7bb
    # standard deviation. A line that is always a fold has almost none; a
    # line that is always a shove has three times that; and the leak map
    # sorts lines by whether their loss clears this bar, so the bar has to
    # be the line's own. Sample variance, so a small n is not flattered.
    var = ((msq or 0.0) - (mean or 0.0) ** 2) * n / (n - 1) if n > 1 else 0.0
    return {"hands": n, "net_bb": net_bb or 0.0, "money": money or 0.0,
            "saw_flop": saw or 0, "wtsd": wtsd or 0, "wwsf": wwsf or 0,
            "bb100": 100 * (net_bb or 0.0) / n,
            "error": 100 * max(var, 0.0) ** 0.5 / n ** 0.5 if n > 1 else 1170 / n ** 0.5}


def show_results(con, where, label, parts=()):
    """
    What the money did in the hands this filter selects.

    Summed over whole hands, not over the filtered decisions, and the
    difference matters: a player who was in position on a monotone flop won
    or lost the WHOLE pot, not the part of it that happened after the flop.
    """
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    pairs = matching_seats(con, where)
    if not pairs:
        print("nothing matches -- " + why_empty(con, parts))
        return
    got = results_of(con, pairs)
    if not got:
        print("nothing matches outside tournaments")
        return
    n, net_bb, money, saw, wtsd, wwsf = (
        got["hands"], got["net_bb"], got["money"], got["saw_flop"],
        got["wtsd"], got["wwsf"])
    print(f"  hands            {n:8d}")
    print(f"  net              {net_bb or 0:+8.1f} bb   (${money or 0:+.2f})")
    print(f"  per 100 hands    {100 * (net_bb or 0) / n:+8.1f} bb/100")
    # A win rate over a few hundred hands is noise wearing a number's
    # clothing: one hand's result has a standard deviation around 11.7bb,
    # so the error on bb/100 is of the order of 1170/sqrt(n) -- measured
    # here from these hands -- and it is usually larger than anything
    # being compared.
    print(f"  error on that    {got['error']:8.0f} bb/100"
          f"   <- and this is why")
    if saw:
        print(f"  saw a flop       {saw:8d}   ({100 * saw / n:.1f}%)")
        print(f"  won at showdown  {wtsd or 0:8d}")
        print(f"  won after flop   {100 * (wwsf or 0) / saw:8.1f}%")


def show_hands(con, where, label, limit=40, parts=()):
    """The hands themselves, most recent first."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    # The filter names bare columns, and `spots` shares several of them
    # with `decisions` -- is_hero, position, combo -- so it is applied inside
    # a subquery where there is only one table for a name to mean.
    rows = con.execute(
        f"SELECT DISTINCT d.hand_id, d.seat, d.played_at, d.site, d.bb, "
        f"       d.position, d.combo, d.board, s.net_bb "
        f"FROM (SELECT * FROM decisions WHERE {where}) d "
        f"LEFT JOIN spots s "
        f"  ON s.hand_id = d.hand_id AND s.seat = d.seat "
        f"ORDER BY d.played_at DESC").fetchall()
    print(f"{len(rows)} hands match; showing up to {limit}\n")
    if not rows:
        print("  " + why_empty(con, parts))
        return
    print(f"  {'when':17} {'site':10} {'bb':>5} {'pos':4} {'hand':5} "
          f"{'net bb':>7}  board")
    print("  " + "-" * 74)
    for hid, seat, when, site, bb, pos, combo, board, net in rows[:limit]:
        print(f"  {when[:16]:17} {site:10} {bb or 0:5.2f} {pos or '?':4} "
              f"{combo or '--':5} {net if net is not None else 0:7.1f}  "
              f"{board or ''}")


def usage():
    print(__doc__)
    print("FILTERS -- combine as many as you like\n")
    print("  switches:")
    for k, v in SWITCHES.items():
        print(f"    {k:14} {v}")
    print("\n  taking a value (comma-separate lists):")
    for k, v in VALUE_FLAGS.items():
        if v:
            print(f"    {k:14} {v}")
    print(f"    {'--board':14} one of: {', '.join(BOARDS)}")
    print(f"    {'--quick':14} named filters: "
          f"{', '.join(sorted(quick_by_key())[:6])}, ... (see --quick-list)")
    print(f"    {'--where':14} raw SQL over `decisions`, for anything above")
    print("\n  Multiple Players cohort filters:")
    print("    --cohort       select players before filtering situations")
    print("    --hands VALUE  player hand count, e.g. >=500")
    print("    --vpip VALUE   player VPIP percentage, e.g. >=28")
    print("    --pfr VALUE    player PFR percentage, e.g. <18")
    print("    --gap VALUE    player VPIP-PFR gap, e.g. >=10")
    print("    --threebet VALUE  player 3-bet percentage")
    print("    --fold-to-threebet VALUE  player fold-to-3-bet percentage")
    print("    --wwsf VALUE    player WWSF percentage")
    print("    --wtsd VALUE    player WTSD percentage")
    print("    --wsd VALUE     player W$SD percentage")
    print("    --bb100 VALUE  player win rate, e.g. >=0")
    print("    --class VALUE  reg, fish, or unknown")
    print("    --durable 0|1  durable named player identity")
    print("\n  reports (not filters):")
    print(f"    {'--by':14} split into a table by one of: "
          f"{', '.join(DIMENSIONS)}")
    print(f"    {'--show':14} which stats are the columns "
          f"(default: {','.join(DEFAULT_COLUMNS)})")
    print(f"    {'--min':14} mark cells below this many chances (default 30)")
    print(f"    {'--hand':14} replay one hand by id, ignoring every filter")
    print(f"    {'--chart':14} the 13x13 chart: what the range holds, or "
          f"one stat per combo with --show")
    print("\n  saving the filter as a stat of its own:")
    print(f"    {'--define':14} a key to save this filter under, so it can "
          f"be a column")
    print(f"    {'--label':14} what to call it in a report (default: the key)")
    print(f"    {'--do':14} what it counts: {', '.join(stats.ACTIONS)}")
    print(f"    {'--per':14} decision (default) or hand -- once per player "
          f"per hand")
    print(f"    {'--forget':14} delete a saved stat or report by name")
    print("\n  saving the filter as a report of its own:")
    print(f"    {'--save':14} a name to keep this filter under")
    print(f"    {'--preset':14} open a saved or built-in report: "
          f"{', '.join(list(reports())[:3])}, ... (see --presets)")
    print("\n  positions: " + ", ".join(POSITIONS))
    print("  streets:   " + ", ".join(STREETS))
    print("  pot types: " + ", ".join(POT_TYPES))
    print("  facing:    " + ", ".join(FACINGS))


# Filters allowed to read the whole table, and the reason each cannot be
# helped. A scan is not automatically a fault -- an index is only worth
# reading when it narrows -- but the reason has to be written down, or the
# check degrades into a number nobody questions.
SCAN_OK = {
    "--line with sizes":
        "a pattern for the whole hand with bet sizes in it starts with a "
        "wildcard -- there is no prefix to seek on, so `sized` is left "
        "unindexed rather than carrying a B-tree nothing can read",
    "--node with sizes":
        "the same, for `node_sz`. The per-street sized columns ARE indexed, "
        "because `--flop XBmC` has no wildcard in it at all",
}


def check(db_path=DB):
    """
    Every filter is valid SQL, it filters, and it reaches an index.

    The second half is the one that matters. A predicate with a typo in a
    column name raises, and gets noticed. A predicate that is merely WRONG
    -- comparing a text column to a number, naming a value that never
    occurs, or accidentally being always-true -- returns a row count and no
    complaint, and every figure computed under it silently describes a
    different population than its heading claims. So each filter must both
    run and select strictly fewer rows than no filter at all.
    """
    con = sqlite3.connect(db_path)
    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    fails, checked = [], 0

    # A representative value for each value-taking flag, chosen to be one
    # that really occurs, so "selects nothing" means the predicate is wrong
    # rather than the value being absent.
    # The date sample is taken from the middle of the corpus rather than
    # written in. A date outside the data passes "runs without error" and
    # fails "actually narrows", which is a fault in the test rather than in
    # the filter -- and a test that cries wolf is one that stops being read.
    midpoint = con.execute(
        "SELECT played_at FROM decisions ORDER BY played_at "
        "LIMIT 1 OFFSET (SELECT COUNT(*) / 2 FROM decisions)").fetchone()[0]
    samples = {
        "--site": "acr", "--pos": "BTN", "--street": "flop",
        "--pot": "3bet", "--facing": "bet", "--combo": "AKs",
        "--stake": "0.1", "--deep": "50", "--short": "200",
        "--hour": "18-23", "--weekday": "sat,sun",
        "--session-len": "60-180", "--session-min": "0-60", "--tables": "1-2",
        "--made": "top pair", "--kicker": "top", "--fd": "nut",
        "--sd": "oesd",
        "--players": "6", "--live": "2",
        "--since": midpoint, "--until": midpoint,
        "--vs-player": con.execute(
            "SELECT vs_player FROM decisions WHERE vs_player IS NOT NULL "
            "LIMIT 1").fetchone()[0],
        # Line patterns that really occur, for the same reason the date is
        # taken from the data: a pattern nobody played would fail the
        # "actually narrows" half of this check without anything being wrong.
        "--line": "*/XBC*", "--node": "*/XB", "--pre": "*R*",
        "--flop": "XBC", "--turn": "XX", "--river": "*B*",
    }
    cases = [(k, [k]) for k in SWITCHES]
    cases += [(k, [k, v]) for k, v in samples.items()]
    cases += [("--board " + b, ["--board", b]) for b in BOARDS]
    cases += [(f"{flag} {name}", [flag, name])
              for flag in RUNOUT_FLAG for name in RUNOUT]
    cases.append(("--where", ["--where", "eff_bb > 100"]))
    cases += [("--flop with sizes", ["--flop", "XBmC"]),
              ("--line with sizes", ["--line", "*/XBm*"]),
              ("--node with sizes", ["--node", "*/XBm"]),
              ("--pre with sizes", ["--pre", "*Rl*"])]
    cases += [("--quick " + f["key"], ["--quick", f["key"]])
              for f in quick_filters()]
    cases.append(("--player", ["--player", con.execute(
        "SELECT player FROM decisions WHERE player IS NOT NULL LIMIT 1"
    ).fetchone()[0]]))

    for name, argv in cases:
        where, _, _p = build(argv)
        checked += 1
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        except sqlite3.Error as e:
            fails.append(f"{name}: {e}")
            continue
        if n == 0:
            fails.append(f"{name}: selects nothing")
        elif n == total:
            fails.append(f"{name}: selects everything -- it is not filtering")

    print(f"filters that run and narrow  {checked - len(fails)}/{checked}")
    for f in fails:
        print(f"    {f}")

    # And each one must reach an index rather than read every row. At ninety
    # thousand decisions a scan is a fifth of a second and nobody notices;
    # the same scan at three million is seven seconds, so the filter that
    # quietly stopped using an index has to be caught now, while the corpus
    # is small enough to hide it.
    scanning = []
    for name, argv in cases:
        where, _l, _p = build(argv)
        try:
            plan = " ".join(str(r[3]) for r in con.execute(
                f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM decisions "
                f"WHERE {where}"))
        except sqlite3.Error:
            continue                    # already reported by the loop above
        if "INDEX" not in plan.upper():
            scanning.append(name)
    print(f"filters that reach an index  {len(cases) - len(scanning)}/{len(cases)}")
    for name in scanning:
        print(f"    {name}: reads every row"
              f"{' -- ' + SCAN_OK[name] if name in SCAN_OK else ''}")
    unexplained = [n for n in scanning if n not in SCAN_OK]
    if unexplained:
        fails.append("these read the whole table and nothing says why: "
                     + ", ".join(unexplained))

    # A replayed hand must show every action the hand had. A viewer that
    # drops one is worse than no viewer: the reader sees a complete-looking
    # hand and reasons about a line that nobody took.
    sample = [r[0] for r in con.execute(
        "SELECT hand_id FROM hands WHERE game='HOLDEM' "
        "ORDER BY RANDOM() LIMIT 200")]
    lost = []
    for hid in sample:
        d = hand_detail(con, hid)
        shown = sum(len(st["actions"]) for st in d["streets"])
        real = con.execute(
            "SELECT COUNT(*) FROM actions WHERE hand_id=?", (hid,)).fetchone()[0]
        if shown != real:
            lost.append(f"{hid}: {shown} shown of {real}")
    print(f"replayed hands keep every action  {len(sample) - len(lost)}"
          f"/{len(sample)}")
    for x in lost[:4]:
        print(f"    {x}")
    if lost:
        fails.append("hand viewer drops actions")

    # The three modes must survive a filter that legitimately matches nothing,
    # since a user will type one within a day of being given the tool.
    empty, _, _ep = build(["--pos", "BTN", "--street", "preflop",
                           "--facing", "check"])
    for mode, fn in (("stats", show_stats), ("results", show_results),
                     ("hands", show_hands), ("chart", show_chart)):
        try:
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                fn(con, empty, "empty")
        except Exception as e:
            fails.append(f"--{mode} on an empty filter: {e}")
    # The all-in EV line has to actually adjust something. It looked right
    # for weeks while doing almost nothing: the query that found all-in
    # hands required the all-in to be the hand's LAST action, and a shove is
    # nearly always called, so it found 148 hands out of 681. The line came
    # out equal to the actual result to the pound, and the green line on the
    # graph disappeared underneath the yellow one -- reported as "why is
    # there no green line".
    pairs = matching_seats(con, build(["--hero"])[0])
    if pairs:
        select_into(con, pairs)
        rows, priced, _skipped = adjusted(con, pairs)
        gap = sum(r[3] for r in rows) - sum(r[1] for r in rows)
        qualify = con.execute(
            "SELECT COUNT(*) FROM (SELECT d.hand_id FROM decisions d "
            "WHERE d.allin = 1 AND d.n = (SELECT MAX(n) FROM decisions x "
            "  WHERE x.hand_id = d.hand_id AND x.allin = 1) "
            "AND d.street <> 'river' AND (SELECT COUNT(*) FROM spots s "
            "  WHERE s.hand_id = d.hand_id AND s.folded_on IS NULL "
            "  AND s.cards IS NOT NULL) = 2 GROUP BY d.hand_id)").fetchone()[0]
        print(f"all-in EV adjusts something  {priced} hands priced of "
              f"{qualify} that can be, gap {gap:+,.0f} bb")
        if not priced:
            fails.append("the all-in EV line prices nothing -- it is just "
                         "the actual result wearing another colour")
        elif abs(gap) < 1:
            fails.append("the all-in EV line is identical to the actual "
                         "result, which is what a broken adjustment looks like")

    print(f"modes survive an empty result "
          f"{'yes' if not any('empty filter' in f for f in fails) else 'NO'}")

    # The chart's composition must count each player-hand once. Counting
    # decisions instead is the one way this function can be wrong without
    # looking wrong: a hand that reached the river has four rows and one
    # that folded preflop has one, so the chart would be drawn from a
    # population weighted towards the hands that went furthest -- a range
    # visibly stronger than the one that actually arrived, in a picture
    # nobody would think to doubt. If the cells sum to the number of
    # player-hands seen, each was counted once.
    charted = 0
    for name, argv in (("everything", []),
                       ("one position", ["--pos", "BTN"]),
                       ("a spot that spans streets", ["--pot", "3bet"])):
        g = chart_of(con, build(argv)[0])
        total = sum(n for n, _k in g["cells"].values())
        charted += 1
        if total != g["seen"]:
            fails.append(f"the chart of {name} counts {total} where "
                         f"{g['seen']} player-hands were seen")
    print(f"the chart counts each player-hand once  "
          f"{charted - len([f for f in fails if 'the chart of' in f])}"
          f"/{charted}")

    # Every square of the chart must be a real combo and every real combo
    # must have a square. 169 of them, and a chart that quietly omits one
    # row of offsuit hands still looks like a chart.
    squares = {combo_at(i, j) for i in range(13) for j in range(13)}
    dealt = {c for (c,) in con.execute(
        "SELECT DISTINCT combo FROM decisions WHERE combo IS NOT NULL")}
    print(f"squares on the chart          {len(squares)}/169")
    if len(squares) != 169 or not dealt <= squares:
        fails.append(f"the chart has {len(squares)} squares and misses "
                     f"{sorted(dealt - squares)[:5]}")

    # A saved report must come back as the filter that was saved. Saved to a
    # file of its own: a check that writes to somebody's own reports is a
    # check they stop running.
    scratch = Path(tempfile.mkdtemp()) / "filters.json"
    trips = []
    try:
        want = ["--pot", "3bet", "--ip"]
        n, _described = save_filter("check round trip", want, path=scratch)
        back = saved_filters(scratch)
        trips.append(("a saved report comes back",
                      back.get("check round trip") == want))
        trips.append(("and it selects what it selected", n > 0))
        # The reporting options are not part of a filter, or a report would
        # rewrite the columns of every view it was opened in.
        save_filter("with options", want + ["--by", "position", "--show",
                                            "vpip"], path=scratch)
        trips.append(("reporting options are left out",
                      saved_filters(scratch).get("with options") == want))
        shadowed = True
        try:
            save_filter("3-bet pots", want, path=scratch)
            shadowed = False
        except ValueError:
            pass
        trips.append(("a built-in report cannot be shadowed", shadowed))
        forget_filter("check round trip", path=scratch)
        trips.append(("forgetting removes it",
                      "check round trip" not in saved_filters(scratch)))
    finally:
        scratch.unlink(missing_ok=True)
        scratch.parent.rmdir()
        saved_filters()
    print(f"a saved report behaves like a built-in  "
          f"{sum(1 for _w, ok in trips if ok)}/{len(trips)}")
    for what, ok in trips:
        if not ok:
            print(f"    {what}: NO")
            fails.append(what)
    if UNREADABLE:
        print("saved reports that no longer build:")
        for name, why in UNREADABLE:
            print(f"    {name}: {why}")

    con.close()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if not argv or "--help" in argv or "-h" in argv:
        usage()
        return 0
    if "--presets" in argv:
        known = reports()
        for name, flags in known.items():
            where, described, _p = build(list(flags))
            print(f"\n  {name}" + ("" if name in SMART_REPORTS else "   (saved)"))
            print(f"      {' '.join(flags)}")
            print(f"      {described}")
        for name, why in UNREADABLE:
            print(f"\n  {name}   BROKEN -- {why}")
        return 1 if UNREADABLE else 0
    if "--quick-list" in argv:
        group = None
        for f in quick_filters():
            if f["group"] != group:
                group = f["group"]
                print(f"\n[{group}]")
            print(f"  {f['key']:22} {f['label']}")
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    cohort_spec, argv = players.parse_cohort(argv)
    mode = "--stats"
    for m in ("--stats", "--hands", "--results", "--graph", "--range",
              "--chart", "--sessions"):
        if m in argv:
            mode = m
            argv = [a for a in argv if a != m]

    def opt(name, default=None):
        if name not in argv:
            return default
        i = argv.index(name) + 1
        if i >= len(argv):
            raise SystemExit(f"{name} needs a value")
        return argv[i]

    preset = opt("--preset") if "--preset" in argv else None
    if preset:
        argv = preset_argv(preset) + argv

    # Naming a stat and asking a question are different verbs, so both of
    # these return rather than falling through into a report. Printing a
    # table under a definition would bury the only number that matters at
    # that moment, which is whether the definition ever occurs at all.
    if "--forget" in argv:
        name = opt("--forget")
        # One verb for two namespaces, because a name is a name. They can
        # only collide if somebody deliberately used the same one twice --
        # a stat key cannot contain a space and a report name usually does
        # -- and guessing which was meant would delete the other.
        is_stat = name in BY_KEY and BY_KEY[name].custom
        is_report = name in saved_filters()
        if is_stat and is_report:
            raise SystemExit(f"{name!r} is both a saved stat and a saved "
                             f"report. Rename one of them; deleting the "
                             f"wrong one is not recoverable.")
        try:
            if is_report:
                forget_filter(name)
            else:
                stats.forget(name)
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"forgot the {'report' if is_report else 'stat'} {name!r}")
        return 0
    if "--save" in argv:
        name = opt("--save")
        try:
            n, described = save_filter(name, argv)
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"saved the report {name!r}")
        print(f"  filter: {described}")
        if n:
            print(f"  {n:,} decisions match it today")
        else:
            print("  NOTHING MATCHES -- it is saved, and every view opened "
                  "under it will be empty")
        print(f"  open it with --preset {name!r}, or from the report box in "
              f"the window")
        return 0
    if "--define" in argv:
        key = opt("--define")
        n, k = define_stat(argv, key, opt("--label"), opt("--do", "aggressive"),
                           opt("--per", "decision"))
        st = BY_KEY[key]
        print(f"saved {key!r} -- {st.label}")
        print(f"  from:   {st.note}")
        print(f"  chance: {st.chance}")
        print(f"  action: {st.action}")
        if n:
            pct, lo, hi = wilson(k, n)
            print(f"  {fmt(n, k, pct, lo, hi)}")
        else:
            # This is what Hand2Note's own manual means by pressing Test
            # Stat before using one. A definition nothing matches saves
            # perfectly happily and then reads as a blank column in every
            # report, which looks like a broken report rather than like a
            # filter that describes a spot nobody has played.
            print("  NOTHING MATCHES -- the chance never came up in this "
                  "database, so the stat will be blank wherever it is shown")
        print(f"  it is a column now (--show {key}) and a filter "
              f"(--quick {key})")
        return 0

    dim = opt("--by")
    if dim is not None and dim not in DIMENSIONS:
        raise SystemExit(f"unknown dimension {dim!r} -- "
                         f"one of: {', '.join(DIMENSIONS)}")
    columns = (opt("--show") or ",".join(DEFAULT_COLUMNS)).split(",")
    for c in columns:
        if c not in BY_KEY:
            raise SystemExit(f"unknown stat {c!r} -- see `stats.py --list`")
    min_n = int(opt("--min", "30"))

    if opt("--hand"):
        con = sqlite3.connect(DB)
        show_hand(con, opt("--hand"))
        con.close()
        return 0

    other = opt("--versus")
    if other is not None:
        con = sqlite3.connect(DB)
        show_versus(con, argv, shlex.split(other), min_n,
                     only=set(columns) if opt("--show") else None)
        con.close()
        return 0

    where, label, _parts = build(argv)
    if preset:
        label = f"{preset}: {label}"
    con = sqlite3.connect(DB)
    if cohort_spec is not None:
        count = select_cohort(con, cohort_spec)
        label += (f", cohort: {players.describe_cohort(cohort_spec)} "
                  f"({count} players)")
        where = (f"({where}) AND EXISTS (SELECT 1 FROM _cohort c "
                 "WHERE c.site = decisions.site AND "
                 "c.player = decisions.player)")
    if mode == "--graph":
        show_graph(con, where, label, opt("--out", "graph.html"))
    elif mode == "--hands":
        show_hands(con, where, label, parts=_parts)
    elif mode == "--range":
        show_range(con, where, label, _parts)
    elif mode == "--sessions":
        show_sessions(con, where, label, _parts)
    elif mode == "--chart":
        # `--show` names the columns of a report, and here it names the one
        # stat the chart is of. Without it the chart is the range itself,
        # which is the question a chart is usually asked.
        show_chart(con, where, label, columns[0] if opt("--show") else None,
                  _parts)
    elif mode == "--results":
        if dim:
            show_results_by(con, where, label, dim)
        else:
            show_results(con, where, label, _parts)
    elif dim:
        show_report(con, where, label, dim, columns, min_n)
    else:
        show_stats(con, where, label, _parts)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
