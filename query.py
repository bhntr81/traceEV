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
    python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --filter 3bet
    python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --chart
    python query.py --cohort-hands 100 --cohort-vpip 40+ --cohort-pfr <=10 \
        --pos BTN --stats
    python query.py --cohort 'Value(3Bet) < 2 and Opps(3Bet) > 100' --filter 3bet
    python query.py --alias me --filter "Flop c-bets"
    python query.py --hero --vs-alias nits --street flop
    python query.py --villain-type fish --reg --stats
    python query.py --hero --board mono --results
    python query.py --hero --pot 3bet --hands
    python query.py --session FIRST_HAND --stats
    python query.py --statistics --fmt cash --last-sessions 10
    python query.py --statistics --exclude-reg-vs-fish --hit threebet
    python query.py --today --hero --results
    python query.py --hours 4 --start-of-day 6 --tz acr=-5
    python query.py --marked --hands
    python query.py --tag leak --hands
    python query.py --hand cp-2459218653 --mark --tag leak
    python query.py --where "eff_bb > 150 AND fl_paired=1" --stats
    python query.py --help
"""

import sqlite3
import sys
from pathlib import Path

import json
import re
import shlex
import tempfile

import aliases
import compact
import expr
import lines
import notes
import players
import sessions
import sites
import stats
import strength
from stats import (BY_KEY, STATS, detectable, difference, fmt, holm,
                   mean_interval, rate, rates as stat_rates, rates_by, wilson)

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
    # What THIS decision was. Faced Next / Next Actions (`--after` /
    # `--then`) are the next row; this is the verb on the row the
    # filter already selected. Clicking "call" in the study Next
    # Action pane is this flag, not `--then call`.
    "--action": None,
    # Whole-hand result of the (hand, seat), via spots. Money is a
    # property of a hand; MTT is refused the same way `results_of`
    # refuses it -- tournament chips are not dollars.
    "--result": None,
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
    # A sit-down, not a raw date. `--session` is the first hand of
    # the cluster `sessions.py` built; `--today` / `--hours` go
    # through the start-of-day hour and per-room HH offset. Raw
    # `--since` / `--until` stay literal `played_at` so a saved
    # report does not move when the clock prefs do.
    "--session": None,
    "--hours": None,
    # Cash | MTT on the Statistics tab, and transferable to Reports.
    # `cash` is everything that is not a tournament (RING, ZONE, BLITZ).
    "--fmt": None,
    # Last N hero cash sit-downs (`sessions` table). MTT has none.
    "--last-sessions": None,
    # The Call Range sibling of a named stat: same chance, a call.
    "--call-range": None,
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
    "--tag": None,          # study tag catalog, over notes.db
    # Named (username, room) groups from aliases.json. `--player me`
    # does not expand an alias -- the words can collide, and the
    # silent reading would drop the person. `--alias` merges as one
    # hero-side OR; `--vs-alias` is the other seat.
    "--alias": None,
    "--vs-alias": None,
    # The other seat's class, by name. `--vs-fish` is the checkbox;
    # this is the same column when the word is typed (`fish`, `reg`,
    # `unknown`, or a comma list).
    "--villain-type": None,
    "--vs-class": None,
    # What happened AFTER this decision. Hand2Note's Faced Next / Next
    # Actions: `--after fold` is "the next other seat folded", `--then
    # bet` is "this player bet the next time they acted". A node prefix
    # cannot say this -- it is cut short at THIS action -- so these look
    # at the next row, not at a string.
    "--after": None,
    "--then": None,
    # Hand2Note's custom action builder, over columns `decisions` already
    # has. `--players` / `--live` name how many sat and how many are left;
    # these name the raise and the size. A line pattern can say the same
    # thing (`--flop XBmC`) but only as a string; a first-in 2/3-pot bet
    # is a checkbox and a letter, which is what the builder is for.
    "--size": None,
    "--outcome": None,
    # Stack in bb as one range -- `--deep` / `--short` still work; this
    # is the custom-builder spelling (`100+`, `<40`, `80-200`).
    "--stack": None,
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

# First raise on this street: the open preflop, or the first raise of a
# bet after the flop. A flop cbet is first-in, not first-raise; a raise
# of that cbet is. `street_agg` is the count of earlier aggression, so
# this is a column predicate and not a second walk of the hand.
FIRST_RAISE_SQL = (
    "agg = 1 AND ("
    "(street = 'preflop' AND street_agg = 0) OR "
    "(street <> 'preflop' AND street_agg = 1 AND to_call > 0))"
)

# Last action on this street. There is no column for it -- n is unique
# per hand, not per street -- so this is the one correlated EXISTS the
# custom builder needs.
LAST_ACTION_SQL = (
    "NOT EXISTS (SELECT 1 FROM decisions y "
    "WHERE y.hand_id = decisions.hand_id "
    "AND y.street = decisions.street AND y.n > decisions.n)"
)

# Not filters -- they change what is shown, not what is selected.
OPTIONS = ("--by", "--show", "--min", "--out", "--hand", "--versus",
           "--preset",
           # `--filter` is the research-brief name for opening a spot:
           # a Smart Report, a --quick key, or a JSON argv list. `--pin`
           # freezes another named report, same person, for the two-
           # column compare. `--versus` is the Holm table of every
           # stat. `--compare` takes two names and is handled in main.
           "--filter", "--pin", "--from", "--branch",
           # Naming a stat rather than selecting rows: the filter beside
           # these becomes the stat's chance, so they are skipped by `build`
           # exactly as the reporting options are.
           "--define", "--forget", "--label", "--do", "--per", "--save",
           # Clock prefs for `--today` / `--hours`. Not predicates:
           # they change how those two flags are compiled.
           "--start-of-day", "--tz")

# Not filters, and they take no value. OPTIONS skip two tokens
# (flag + argument). Putting exclude there ate `--hero` and
# Reports opened without the person -- the check that caught it.
SKIP = ("--exclude-reg-vs-fish", "--statistics")

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
    # First to put chips in on this street, and the last to raise before
    # this decision. The custom builder asks for these by name; they have
    # been columns since `decisions` was written, and until now the only
    # way to say them was `--where first_in=1`.
    "--first-in": "first_in = 1",
    "--last-raise": "was_agg = 1",
    # First raise on this street, and the last action on it. `--last-raise`
    # is "this player already raised" (was_agg); these are about THIS
    # action -- H2N's custom-builder modifiers, not a second graph.
    "--first-raise": FIRST_RAISE_SQL,
    "--last-action": LAST_ACTION_SQL,
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

# Study filters. Not in SWITCHES: the live `--check` loop requires every
# switch to select some rows in `hands.db`, and a machine with no marks
# yet would fail "selects nothing" for a filter that is working.
STUDY_SWITCHES = {
    "--marked": ("hand_id IN (SELECT hand_id FROM study.hand_marks)",
                 "marked"),
    "--noted": ("hand_id IN (SELECT hand_id FROM study.note_hands)",
                "noted"),
}

# What a report can be split by. A tracker's value is mostly here: one
# number for "how often do I 3-bet" is a fact, the same number broken down
# by position is a plan.
#
# Each is an SQL expression over `decisions`, plus how to order the rows,
# since a report sorted alphabetically puts August before February and the
# big blind before the button.
#
# Size letters are `lines.bucket` as SQL, not a second set of edges.
# `--size m`, `--by size` and the Bet Sizes pane have to name the same
# pots; a CASE that drifted from `size_sql` would split a report and a
# filter onto different hands and look like a finding.


def size_expr():
    """SQL CASE matching lines.bucket and size_sql, one letter per pot-frac."""
    parts = ["CASE"]
    prev = None
    for edge, name in lines.SIZES:
        if prev is None:
            parts.append(
                f"WHEN pot_frac IS NOT NULL AND pot_frac <= {edge} "
                f"THEN '{name}'")
        else:
            parts.append(
                f"WHEN pot_frac > {prev} AND pot_frac <= {edge} "
                f"THEN '{name}'")
        prev = edge
    parts.append(f"WHEN pot_frac > {prev} THEN '{lines.OVERBET}'")
    parts.append("ELSE NULL END")
    return " ".join(parts)


SIZE_NAMES = {
    "s": "small (<=40%)",
    "m": "medium (40-60%)",
    "l": "large (60-90%)",
    "p": "pot (90-120%)",
    "o": "overbet (>120%)",
}

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
    "hand": ("made", str),
    "flush_draw": ("fd", str),
    "straight_draw": ("sd", str),
    "size": (size_expr(), lambda k: (
        list(lines.BUCKETS).index(k) if k in lines.BUCKETS else 99)),
}

# A Smart Report is a named situation, the way Hand2Note's tree is: not
# "how often did they 3-bet" (that is a stat) but "we are in a 3-bet pot"
# (that is a filter you can open, split, and walk away from). The five
# this project first guessed at are still here under the same names -- a
# saved report or a check that says `3-bet pots` has to keep meaning that
# -- and the rest are the spots a tracker user actually clicks between.
#
# `family` is the tree heading. `related` is the spots you would open next
# from here: the other seat, the next street, the same idea in a fatter
# or thinner pot. Generated variants (IP/OOP, the neighbouring street)
# sit on top of this list in `related_spots`; these are the ones that
# have a name of their own and belong in the report box.
SMART_REPORTS = {
    "Steal attempts": ("--street", "preflop", "--pos", "CO,BTN,SB",
                       "--facing", "unopened"),
    "Blind defense": ("--pos", "SB,BB", "--facing", "open"),
    "Facing a 3-bet": ("--street", "preflop", "--facing", "3bet"),
    "Facing a 4-bet": ("--street", "preflop", "--facing", "4bet"),
    "Single-raised pots": ("--pot", "raised"),
    "3-bet pots": ("--pot", "3bet"),
    "4-bet pots": ("--pot", "4bet"),
    "Limped pots": ("--pot", "limped"),
    "Flop c-bets": ("--street", "flop", "--pfa", "--facing", "check"),
    "Flop c-bets IP": ("--street", "flop", "--pfa", "--facing", "check",
                       "--ip"),
    "Flop c-bets OOP": ("--street", "flop", "--pfa", "--facing", "check",
                        "--oop"),
    "Flop vs c-bet": ("--street", "flop", "--not-pfa", "--facing", "bet",
                      "--vs-pfa"),
    "Raise C-bet": ("--quick", "raise_cbet"),
    "Donk flop": ("--street", "flop", "--not-pfa", "--facing", "check",
                  "--pot", "raised,3bet,4bet,5bet+"),
    "Check-raise flop": ("--street", "flop", "--oop", "--facing", "bet"),
    "Flop in 3-bet pots": ("--pot", "3bet", "--street", "flop"),
    "Turn c-bets": ("--street", "turn", "--pfa", "--facing", "check"),
    "2nd Barrel": ("--quick", "cbet_turn"),
    "Missed 2nd Barrel": ("--quick", "cbet_turn_not"),
    "Turn vs bet": ("--street", "turn", "--facing", "bet"),
    "Probe turn": ("--street", "turn", "--not-pfa", "--facing", "check"),
    "River bets": ("--street", "river", "--facing", "bet"),
    "River c-bets": ("--street", "river", "--pfa", "--facing", "check"),
    "3rd Barrel": ("--quick", "cbet_river"),
    "Missed 3rd Barrel": ("--quick", "cbet_river_not"),
    "River in 3-bet pots": ("--pot", "3bet", "--street", "river"),
    "All-in decisions": ("--allin",),
}

# The tree Hand2Note draws on the left of a Smart Report. A report with
# no entry here still works; it just has no siblings to offer.
REPORT_FAMILY = {
    "Steal attempts": "preflop",
    "Blind defense": "preflop",
    "Facing a 3-bet": "preflop",
    "Facing a 4-bet": "preflop",
    "Single-raised pots": "pots",
    "3-bet pots": "pots",
    "4-bet pots": "pots",
    "Limped pots": "pots",
    "Flop c-bets": "flop",
    "Flop c-bets IP": "flop",
    "Flop c-bets OOP": "flop",
    "Flop vs c-bet": "flop",
    "Raise C-bet": "flop",
    "Donk flop": "flop",
    "Check-raise flop": "flop",
    "Flop in 3-bet pots": "flop",
    "Turn c-bets": "turn",
    "2nd Barrel": "turn",
    "Missed 2nd Barrel": "turn",
    "Turn vs bet": "turn",
    "Probe turn": "turn",
    "River bets": "river",
    "River c-bets": "river",
    "3rd Barrel": "river",
    "Missed 3rd Barrel": "river",
    "River in 3-bet pots": "river",
    "All-in decisions": "other",
}

REPORT_RELATED = {
    "Steal attempts": ("Blind defense", "Single-raised pots"),
    "Blind defense": ("Steal attempts", "Facing a 3-bet"),
    "Facing a 3-bet": ("Facing a 4-bet", "3-bet pots", "Blind defense"),
    "Facing a 4-bet": ("Facing a 3-bet", "4-bet pots"),
    "Single-raised pots": ("3-bet pots", "Flop c-bets", "Limped pots"),
    "3-bet pots": ("4-bet pots", "Single-raised pots", "Flop in 3-bet pots"),
    "4-bet pots": ("3-bet pots", "Facing a 4-bet"),
    "Limped pots": ("Single-raised pots", "Donk flop"),
    "Flop c-bets": ("Flop vs c-bet", "Flop c-bets IP", "Flop c-bets OOP",
                    "Turn c-bets", "2nd Barrel", "Flop in 3-bet pots"),
    "Flop c-bets IP": ("Flop c-bets OOP", "Flop c-bets", "Flop vs c-bet"),
    "Flop c-bets OOP": ("Flop c-bets IP", "Flop c-bets", "Check-raise flop"),
    "Flop vs c-bet": ("Flop c-bets", "Raise C-bet", "Check-raise flop",
                      "Turn vs bet"),
    "Raise C-bet": ("Flop vs c-bet", "Check-raise flop", "2nd Barrel",
                    "3rd Barrel"),
    "Donk flop": ("Flop c-bets", "Check-raise flop", "Limped pots"),
    "Check-raise flop": ("Flop vs c-bet", "Flop c-bets OOP", "Turn vs bet"),
    "Flop in 3-bet pots": ("3-bet pots", "Flop c-bets", "River in 3-bet pots"),
    "Turn c-bets": ("Flop c-bets", "2nd Barrel", "Missed 2nd Barrel",
                    "Turn vs bet", "Probe turn", "River c-bets"),
    "2nd Barrel": ("Missed 2nd Barrel", "Turn c-bets", "3rd Barrel",
                   "Flop c-bets"),
    "Missed 2nd Barrel": ("2nd Barrel", "Turn c-bets", "3rd Barrel"),
    "Turn vs bet": ("Turn c-bets", "Flop vs c-bet", "River bets"),
    "Probe turn": ("Turn c-bets", "Turn vs bet", "Flop c-bets"),
    "River bets": ("River c-bets", "Turn vs bet", "River in 3-bet pots"),
    "River c-bets": ("Turn c-bets", "3rd Barrel", "Missed 3rd Barrel",
                     "River bets", "River in 3-bet pots"),
    "3rd Barrel": ("Missed 3rd Barrel", "River c-bets", "2nd Barrel"),
    "Missed 3rd Barrel": ("3rd Barrel", "River c-bets", "2nd Barrel"),
    "River in 3-bet pots": ("Flop in 3-bet pots", "3-bet pots", "River bets"),
    "All-in decisions": ("River bets", "Facing a 4-bet"),
}

FAMILY_ORDER = ("preflop", "pots", "flop", "turn", "river", "other")

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
        if a in SKIP:
            i += 1
        elif a in OPTIONS:
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
    if "--cohort" in argv or any(a in players.COHORT_ALIASES for a in argv):
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


def reports_by_family(path=None):
    """
    Named reports grouped the way Hand2Note's tree is, built-ins first.

    Saved reports have no family of their own -- they are whatever you
    were looking at -- so they land under 'saved' at the end rather than
    being guessed into a heading that will be wrong the first time the
    filter is something this list did not anticipate.
    """
    grouped = {fam: [] for fam in FAMILY_ORDER}
    grouped["saved"] = []
    for name in reports(path):
        if name in SMART_REPORTS:
            grouped.setdefault(REPORT_FAMILY.get(name, "other"), []).append(name)
        else:
            grouped["saved"].append(name)
    return [(fam, names) for fam, names in grouped.items() if names]


# Who / when / which site -- not the situation. A Smart Report is a
# spot; `--hero` is who you are measuring in it. Related-spot matching
# that kept `--hero` would fail to recognise "Flop c-bets" the moment
# you opened it on your own hands, and offer no neighbours.
WHO_SWITCHES = ("--hero", "--pool", "--vs-hero", "--vs-pool",
                "--reg", "--fish", "--vs-reg", "--vs-fish",
                "--with-fish", "--regs-only",
                "--today")
WHO_VALUES = ("--player", "--vs-player", "--site", "--stake",
              "--since", "--until",
              "--alias", "--vs-alias", "--villain-type", "--vs-class",
              "--session", "--hours",
              "--fmt", "--last-sessions")


def resolve_filter(name, path=None):
    """
    A filter by the name a person would type.

    The research brief wrote `report --filter <name|json>`. That is a
    Smart Report or a saved one, then a --quick key, then a JSON argv
    list or a FilterDef object -- the namespaces this file already has,
    not a fourth language. `--preset` still works; this is the same
    door with a wider lock.
    """
    name = str(name).strip()
    known = reports(path)
    if name in known:
        return list(known[name])
    if name in quick_by_key():
        return ["--quick", name]
    if name.startswith("[") or name.startswith("{"):
        try:
            obj = json.loads(name)
        except ValueError as e:
            raise SystemExit(f"--filter {name!r} is not JSON: {e}")
        if isinstance(obj, list):
            return [str(x) for x in obj]
        if isinstance(obj, dict):
            return FilterDef.from_dict(obj).to_argv()
        raise SystemExit("--filter JSON must be a list of flags or an object")
    raise SystemExit(
        f"unknown filter {name!r} -- a name from --presets, a key from "
        f"--quick-list, a JSON argv list, or a FilterDef object")


# The custom-builder modifiers, as flags. Everything else a filter can
# say is `rest` so a full situation still round-trips through argv.
_LINE_KEYS = ("--pre", "--flop", "--turn", "--river", "--line", "--node")
_CUSTOM_SWITCHES = ("--first-in", "--first-raise", "--last-raise",
                    "--last-action")
_CUSTOM_VALUES = ("--players", "--live", "--size", "--stack")


class FilterDef:
    """
    A custom filter as a value, not a string of flags.

    Hand2Note's builder is a street-by-street action graph with modifiers
    on the node. Here the graph is already a line string (`--flop XBmC`)
    and the modifiers are columns `decisions` already has. This object
    is that pair, so `--check` can name a modifier and a dialog can
    round-trip without each front end parsing flags its own way.

    Compiles to the same argv `build` already understands. Hits/Opps,
    stats, the hand list, Faced Next, `--save` and `--filter` stay one
    pipeline. There is no second language.
    """

    def __init__(self, lines=None, first_in=False, first_raise=False,
                 last_raise=False, last_action=False, players=None,
                 live=None, size=None, stack=None, rest=None):
        self.lines = dict(lines or {})
        self.first_in = bool(first_in)
        self.first_raise = bool(first_raise)
        self.last_raise = bool(last_raise)
        self.last_action = bool(last_action)
        self.players = players
        self.live = live
        self.size = size
        self.stack = stack
        self.rest = list(rest or [])

    def to_argv(self):
        argv = list(self.rest)
        for flag in _LINE_KEYS:
            if self.lines.get(flag):
                argv += [flag, self.lines[flag]]
        if self.players is not None:
            argv += ["--players", str(self.players)]
        if self.live is not None:
            argv += ["--live", str(self.live)]
        if self.size:
            argv += ["--size", str(self.size)]
        if self.stack:
            argv += ["--stack", str(self.stack)]
        if self.first_in:
            argv.append("--first-in")
        if self.first_raise:
            argv.append("--first-raise")
        if self.last_raise:
            argv.append("--last-raise")
        if self.last_action:
            argv.append("--last-action")
        return argv

    def to_dict(self):
        out = {}
        if self.lines:
            out["lines"] = dict(self.lines)
        if self.first_in:
            out["first_in"] = True
        if self.first_raise:
            out["first_raise"] = True
        if self.last_raise:
            out["last_raise"] = True
        if self.last_action:
            out["last_action"] = True
        if self.players is not None:
            out["players"] = self.players
        if self.live is not None:
            out["live"] = self.live
        if self.size:
            out["size"] = self.size
        if self.stack:
            out["stack"] = self.stack
        if self.rest:
            out["rest"] = list(self.rest)
        return out

    @classmethod
    def from_argv(cls, argv):
        """situation flags → the custom-builder fields plus leftover rest."""
        argv = situation_only(list(argv))
        d = cls()
        rest, i = [], 0
        while i < len(argv):
            a = argv[i]
            if a in _CUSTOM_SWITCHES:
                setattr(d, a.lstrip("-").replace("-", "_"), True)
                i += 1
                continue
            if a in _LINE_KEYS and i + 1 < len(argv):
                d.lines[a] = str(argv[i + 1])
                i += 2
                continue
            if a in _CUSTOM_VALUES and i + 1 < len(argv):
                v = argv[i + 1]
                if a == "--players":
                    d.players = int(float(v))
                elif a == "--live":
                    d.live = int(float(v))
                elif a == "--size":
                    d.size = str(v)
                else:
                    d.stack = str(v)
                i += 2
                continue
            if a in VALUE_FLAGS or a in OPTIONS:
                rest += argv[i:i + 2]
                i += 2
                continue
            rest.append(a)
            i += 1
        d.rest = rest
        return d

    @classmethod
    def from_dict(cls, obj):
        """JSON object → the same argv a typed filter would have produced.

        Two shapes, because people will write both: a compact AST
        (`first_raise`, `flop`, `size`) and a leftover `argv` / `rest`
        list for flags this object does not name.
        """
        if not isinstance(obj, dict):
            raise SystemExit("FilterDef JSON must be an object")
        if "argv" in obj and isinstance(obj["argv"], list):
            return cls.from_argv([str(x) for x in obj["argv"]])
        d = cls()
        lines = obj.get("lines") or {}
        for flag in _LINE_KEYS:
            key = flag.lstrip("-")
            if obj.get(key):
                d.lines[flag] = str(obj[key])
            elif lines.get(flag) or lines.get(key):
                d.lines[flag] = str(lines.get(flag) or lines.get(key))
        d.first_in = bool(obj.get("first_in"))
        d.first_raise = bool(obj.get("first_raise"))
        d.last_raise = bool(obj.get("last_raise"))
        d.last_action = bool(obj.get("last_action"))
        if obj.get("players") not in (None, ""):
            d.players = int(float(obj["players"]))
        if obj.get("live") not in (None, ""):
            d.live = int(float(obj["live"]))
        if obj.get("size"):
            d.size = str(obj["size"])
        if obj.get("stack"):
            d.stack = str(obj["stack"])
        rest = []
        for key, flag in (("street", "--street"), ("pot", "--pot"),
                          ("pos", "--pos"), ("facing", "--facing"),
                          ("vs", "--vs")):
            if obj.get(key):
                rest += [flag, str(obj[key])]
        for item in obj.get("flags") or []:
            flag = item if str(item).startswith("--") else "--" + str(item)
            rest.append(flag)
        if obj.get("rest"):
            rest += [str(x) for x in obj["rest"]]
        d.rest = rest
        return d


def who_only(argv):
    """The person flags alone -- the other half of `without_who`."""
    out = []
    i = 0
    argv = situation_only(list(argv))
    while i < len(argv):
        a = argv[i]
        if a in WHO_SWITCHES:
            out.append(a)
            i += 1
        elif a in WHO_VALUES:
            out += argv[i:i + 2]
            i += 2
        elif a in VALUE_FLAGS or a in OPTIONS:
            i += 2
        else:
            i += 1
    return out


def without_who(argv):
    """The situation flags alone, with the person taken off."""
    out = []
    i = 0
    argv = situation_only(list(argv))
    while i < len(argv):
        a = argv[i]
        if a in WHO_SWITCHES:
            i += 1
            continue
        if a in WHO_VALUES:
            i += 2
            continue
        if a in VALUE_FLAGS or a in OPTIONS:
            out += argv[i:i + 2]
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def flag_values(argv, flag):
    """Every value given to this flag, split on commas the way `build` does."""
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == flag and i + 1 < len(argv):
            out.extend(x.strip() for x in str(argv[i + 1]).split(",") if x.strip())
            i += 2
        elif a in VALUE_FLAGS or a in OPTIONS:
            i += 2
        else:
            i += 1
    return out


def _canonical(argv):
    """A filter as a set of (flag, value) pairs, so two writings of the
    same situation compare equal. `--pos BTN,CO` and `--pos CO,BTN` are
    the same report; comparing the raw lists would say they are not, and
    related-spot navigation would offer you the spot you are already in."""
    parts = []
    i = 0
    argv = situation_only(argv)
    while i < len(argv):
        a = argv[i]
        if a in VALUE_FLAGS and i + 1 < len(argv):
            v = str(argv[i + 1])
            if "{list}" in (VALUE_FLAGS[a] or "") or a in (
                    "--quick", "--board", "--turn-card", "--river-card",
                    "--size", "--outcome", "--action", "--result"):
                v = ",".join(sorted(x.strip() for x in v.split(",") if x.strip()))
            parts.append((a, v))
            i += 2
        else:
            parts.append((a, None))
            i += 1
    return frozenset(parts)


def matching_report(argv, path=None):
    """The built-in or saved report this filter already is, or None."""
    want = _canonical(without_who(argv))
    if not want:
        return None
    for name, flags in reports(path).items():
        if _canonical(flags) == want:
            return name
    return None


# Eight columns is what fits and what gets read. Anything else is available
# with --show. This is the UNFILTERED view -- VPIP and PFR, because that is
# what a HUD-less tracker opens on. A river filter that still leads with
# VPIP is a report of a different street than the one you asked about:
# VPIP's chance is preflop, so every cell under a `--street river` filter
# is empty and the n column prints as zero. `columns_for` is what stops
# that, and this list is what it returns when the filter names no street.
DEFAULT_COLUMNS = ["vpip", "pfr", "rfi", "threebet", "fold_to_3bet",
                   "cbet_flop", "fold_to_cbet", "flop_agg"]

# Situation -> the stats that situation is actually about. Street wins
# over pot type because "flop in a 3-bet pot" is a flop report; the pot
# is already in the filter.
_STREET_COLUMNS = {
    "preflop": ["vpip", "pfr", "rfi", "threebet", "fold_to_3bet",
                "fourbet", "steal", "fold_to_steal", "bb_defend", "squeeze"],
    "flop": ["cbet_flop", "fold_to_cbet", "raise_cbet", "donk_flop",
             "checkraise_flop", "fold_to_donk", "flop_agg"],
    "turn": ["cbet_turn", "delayed_cbet", "probe_turn", "float_turn",
             "fold_to_turn_bet"],
    "river": ["cbet_river", "fold_to_river_bet", "river_agg", "overbet",
              "small_bet", "faces_overbet"],
}

# A Quick Filter is a spot, and applying it should swap the columns to
# the stats that spot is about -- not leave VPIP sitting there. Decision-
# sourced only: a spots-sourced stat (WTSD, W$SD) cannot see a street
# filter, and putting one here would blank the report tab the same way
# VPIP blanks a river filter.
QUICK_PACKS = {
    "cbet_flop": ["cbet_flop", "fold_to_cbet", "raise_cbet", "cbet_turn",
                  "cbet_river", "donk_flop", "flop_agg"],
    "cbet_flop_not": ["cbet_flop", "delayed_cbet", "cbet_turn", "cbet_river",
                      "flop_agg", "donk_flop"],
    "raise_cbet": ["raise_cbet", "fold_to_cbet", "cbet_flop",
                   "cbet_turn", "cbet_river", "checkraise_flop", "flop_agg",
                   "fold_to_turn_bet"],
    "fold_to_cbet": ["fold_to_cbet", "raise_cbet", "cbet_flop",
                     "checkraise_flop", "float_turn", "fold_to_turn_bet"],
    "donk_flop": ["donk_flop", "fold_to_donk", "cbet_flop", "flop_agg",
                  "checkraise_flop"],
    "checkraise_flop": ["checkraise_flop", "fold_to_cbet", "raise_cbet",
                        "donk_flop", "flop_agg"],
    "cbet_turn": ["cbet_turn", "fold_to_turn_bet", "delayed_cbet",
                  "cbet_flop", "cbet_river", "probe_turn"],
    "cbet_turn_not": ["cbet_turn", "delayed_cbet", "cbet_river",
                      "fold_to_turn_bet"],
    "fold_to_turn_bet": ["fold_to_turn_bet", "cbet_turn", "float_turn",
                         "probe_turn", "cbet_river"],
    "cbet_river": ["cbet_river", "fold_to_river_bet", "cbet_turn",
                   "river_agg", "overbet"],
    "fold_to_river_bet": ["fold_to_river_bet", "cbet_river", "river_agg",
                          "overbet", "faces_overbet"],
    "threebet": ["threebet", "fold_to_3bet", "fourbet", "coldcall",
                 "squeeze"],
    "fold_to_3bet": ["fold_to_3bet", "fourbet", "threebet", "fold_to_4bet"],
    "fourbet": ["fourbet", "fold_to_4bet", "threebet", "fold_to_3bet"],
    "steal": ["steal", "fold_to_steal", "bb_defend", "rfi", "threebet"],
    "fold_to_steal": ["fold_to_steal", "steal", "bb_defend", "threebet"],
    "bb_defend": ["bb_defend", "fold_to_steal", "steal", "threebet",
                  "squeeze"],
    "squeeze": ["squeeze", "threebet", "coldcall", "fold_to_3bet"],
}


# Won$ / Won hand% belong on these packs. They are NOT Stat keys -- a
# spots-sourced Stat blanks under a street filter, which is the empty-
# column failure the pack exists to stop. The numbers come from a join
# onto the filtered (hand, seat) pairs, so a flop filter still has them.
CBET_PACKS = frozenset({
    "cbet_flop", "cbet_flop_not", "raise_cbet",
    "cbet_turn", "cbet_turn_not", "cbet_river",
})


def _known_columns(keys):
    """Drop names the registry does not have, keep the order, cap at eight."""
    out = []
    for key in keys:
        if key in BY_KEY and key not in out:
            out.append(key)
        if len(out) == 8:
            break
    return out or list(DEFAULT_COLUMNS)


def columns_for(argv):
    """
    The report columns this situation is about.

    `--show` still wins when the caller named the columns. Without it, a
    flop filter that prints VPIP is answering a preflop question under a
    flop heading -- and because the two streets cannot both be true, the
    cells come back empty. That is the report tab on 'Flop c-bets' today.
    Hand2Note's Smart Reports change the columns when the spot changes;
    this is that, over the registry we already have.
    """
    argv = situation_only(argv)
    streets = set(flag_values(argv, "--street"))
    pots = set(flag_values(argv, "--pot"))
    facing = set(flag_values(argv, "--facing"))
    quick = set(flag_values(argv, "--quick"))
    if len(quick) == 1:
        only = next(iter(quick))
        if only in QUICK_PACKS:
            return _known_columns(QUICK_PACKS[only])
    # A line flag is a street even when `--street` was not said.
    for flag, street in (("--pre", "preflop"), ("--flop", "flop"),
                         ("--turn", "turn"), ("--river", "river")):
        if flag in argv:
            streets.add(street)
    # A named stat used as a filter carries its own street. `--quick
    # cbet_flop` is a flop report; leaving it on DEFAULT_COLUMNS would
    # put VPIP next to a filter that already answered "they cbet".
    for key in quick:
        st = BY_KEY.get(key) or BY_KEY.get(key[:-4] if key.endswith("_not") else "")
        if st and st.group in _STREET_COLUMNS:
            streets.add(st.group)

    if "river" in streets:
        return _known_columns(_STREET_COLUMNS["river"])
    if "turn" in streets:
        return _known_columns(_STREET_COLUMNS["turn"])
    if "flop" in streets:
        return _known_columns(_STREET_COLUMNS["flop"])
    if "preflop" in streets or facing & {"unopened", "open", "3bet", "4bet", "5bet+"}:
        return _known_columns(_STREET_COLUMNS["preflop"])
    if pots & {"3bet", "4bet", "5bet+"}:
        return _known_columns(
            ["threebet", "fold_to_3bet", "fourbet", "cbet_flop",
             "fold_to_cbet", "cbet_turn", "fold_to_river_bet"])
    if "limped" in pots:
        return _known_columns(
            ["limp", "iso", "donk_flop", "flop_agg", "vpip", "pfr"])
    if "--pfa" in argv:
        return _known_columns(
            ["cbet_flop", "cbet_turn", "cbet_river", "delayed_cbet",
             "fold_to_donk"])
    if "--allin" in argv:
        return _known_columns(
            ["fourbet", "fold_to_4bet", "overbet", "faces_overbet",
             "river_agg"])
    return list(DEFAULT_COLUMNS)


def pack_wants_won(argv):
    """
    True when this filter is a c-bet / raise-cbet / barrel pack.

    Won$ lives here and not in QUICK_PACKS: putting a spots-sourced
    stat in the column list reprints the blank-column failure those
    packs were written to stop.
    """
    quick = set(flag_values(argv or [], "--quick"))
    return len(quick) == 1 and next(iter(quick)) in CBET_PACKS


# What they DID in the rows the filter already selected. A named stat asks
# a different question -- "of the times a cbet was possible" -- and is the
# table underneath. Hand2Note puts this mix at the top of a popup report
# because it is the answer to "what happens here", and without it a
# filtered stats page is a dump of leftover frequencies.
#
# The five verbs partition `decisions.action`. All-in is counted beside
# them rather than among them: 95 of 236 all-ins here are calls, and
# folding them into "raise" is the defect `lines.letter` exists to stop.
ACTION_MIX = (
    ("fold", "action = 'F'"),
    ("check", "action = 'X'"),
    ("call", "action IN ('C','A') AND agg = 0"),
    ("bet", "agg = 1 AND to_call = 0"),
    ("raise", "agg = 1 AND to_call > 0"),
)
ACTION_ALIAS = {
    "fold": "fold", "f": "fold",
    "check": "check", "x": "check",
    "call": "call", "c": "call",
    "bet": "bet", "b": "bet",
    "raise": "raise", "r": "raise", "3bet": "raise", "3-bet": "raise",
}


def actions_of(con, where):
    """
    Fold / check / call / bet / raise of the filtered decisions, one pass.

    Each rate carries its n (the same n -- how many decisions the filter
    selected) and a Wilson interval. The mix is a partition: the five
    counts sum to the row count, which `query.py --check` asserts, because
    a sixth verb slipping through would silently shrink every percentage
    and look like a tight pool.
    """
    # Aliases quoted: `check` is a SQLite keyword, and an unquoted
    # `AS check` is a syntax error -- the mix then never runs, which
    # is a blank "this spot" heading rather than a wrong number.
    bits = ", ".join(f'SUM({sql}) AS "{name}"' for name, sql in ACTION_MIX)
    row = con.execute(
        f'SELECT COUNT(*) AS n, {bits}, SUM(allin = 1) AS shove '
        f"FROM decisions WHERE {where}").fetchone()
    n = row[0] or 0
    mix = []
    for i, (name, _sql) in enumerate(ACTION_MIX):
        k = row[i + 1] or 0
        if not n:
            continue
        p, lo, hi = wilson(k, n)
        mix.append({"key": name, "label": name, "n": n, "k": k,
                    "pct": 100 * p, "band": 100 * (hi - lo) / 2})
    shove = row[-1] or 0
    extra = []
    if n and shove:
        p, lo, hi = wilson(shove, n)
        extra.append({"key": "allin", "label": "all-in (of these)",
                      "n": n, "k": shove, "pct": 100 * p,
                      "band": 100 * (hi - lo) / 2})
    return {"n": n, "mix": mix, "extra": extra}


def drop_flag(argv, flag):
    """argv with this flag and its value (if any) taken off."""
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == flag:
            i += 2 if (a in VALUE_FLAGS or a in OPTIONS) else 1
            continue
        if a in VALUE_FLAGS or a in OPTIONS:
            out += argv[i:i + 2]
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def who_where(argv):
    """The person only, so Hits/1000 is per thousand of THEIR hands."""
    out = []
    i = 0
    argv = situation_only(list(argv))
    while i < len(argv):
        a = argv[i]
        if a in WHO_SWITCHES:
            out.append(a)
            i += 1
        elif a in WHO_VALUES:
            out += argv[i:i + 2]
            i += 2
        elif a in VALUE_FLAGS or a in OPTIONS:
            i += 2
        else:
            i += 1
    if not out:
        return "1=1"
    return build(out)[0]


def spot_summary(con, where, argv):
    """
    Hits, opportunities, hits per 1000 hands, and the primary frequency.

    Hand2Note prints these on every filtered report. Hits and opportunities
    are the two halves of a stat: if the filter already names an action
    (`--quick cbet_flop`, `--aggressive`) then hits are the matching rows
    and opportunities are the same filter with the action taken off. If
    it is only a situation -- a Smart Report, a street -- opportunities
    are the matching rows and hits are the aggressive ones among them.

    Hits/1000 is per thousand player-hands of the person being measured,
    not per thousand opportunities. A rare spot with a high frequency
    still has a small Hits/1000; mixing the two up is how a 70% cbet
    on 12 flops looks like a leak you see every orbit.
    """
    argv = situation_only(argv)
    hands = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat "
        f"FROM decisions WHERE {with_cohort(con, who_where(argv))})"
    ).fetchone()[0]
    quick = flag_values(argv, "--quick")
    chained = "--after" in argv or "--then" in argv
    label = "aggressive"
    if len(quick) == 1 and quick[0] in BY_KEY and not chained:
        st = BY_KEY[quick[0]]
        rest, _, _ = build(drop_flag(argv, "--quick"))
        rest = with_cohort(con, rest)
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions "
            f"WHERE ({st.chance}) AND ({rest})").fetchone()[0]
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions "
            f"WHERE ({st.chance}) AND ({st.action}) AND ({rest})"
        ).fetchone()[0]
        label = st.label
    elif chained:
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        parent, _, _ = build(drop_flag(drop_flag(argv, "--after"), "--then"))
        parent = with_cohort(con, parent)
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {parent}").fetchone()[0]
        label = "this chain"
    elif "--outcome" in argv:
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        parent, _, _ = build(drop_flag(argv, "--outcome"))
        parent = with_cohort(con, parent)
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {parent}").fetchone()[0]
        label = "this outcome"
    elif "--size" in argv or "--first-raise" in argv or "--last-action" in argv \
            or "--stack" in argv:
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        parent_argv = argv
        label = "this size"
        for flag, word in (("--size", "this size"),
                           ("--first-raise", "first raise"),
                           ("--last-action", "last action"),
                           ("--stack", "this stack")):
            if flag in parent_argv:
                parent_argv = drop_flag(parent_argv, flag)
                label = word
        parent, _, _ = build(parent_argv)
        parent = with_cohort(con, parent)
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {parent}").fetchone()[0]
    elif "--aggressive" in argv or "--allin" in argv:
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        parent, _, _ = build(drop_flag(drop_flag(argv, "--aggressive"),
                                       "--allin"))
        parent = with_cohort(con, parent)
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {parent}").fetchone()[0]
        label = "aggressive" if "--aggressive" in argv else "all-in"
    else:
        opps = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE ({where}) AND agg = 1"
        ).fetchone()[0]
        label = "aggressive"
    p, lo, hi = wilson(hits, opps) if opps else (0.0, 0.0, 0.0)
    return {
        "hits": hits, "opps": opps, "hands": hands,
        "per_1k": (1000.0 * hits / hands) if hands else 0.0,
        "pct": 100 * p, "band": 100 * (hi - lo) / 2,
        "label": label,
    }


# One CASE, used by the aggregate and by each matching hand, so the
# mean on the report and the number beside a replay cannot drift.
#
# v1 prices three clean cases. Everything else is NULL (unpriced),
# not zero: stuffing 0 in for a called pot would look like the action
# was break-even and that is a finding, not a gap.
#
#   fold always 0
#   bet 5 into pot 10, everyone folds  →  +10  (pot_before; the bet
#       comes back, so it is not subtracted)
#   bet 5, face a raise, fold          →  −5   (amount on THIS action)
#
# Not Won$ of the hand, not all-in EV, not Call Profit Rate.
# Call Profit Rate is the sibling below: actual calls, when raise
# was also an option. It is not this CASE with later pot stuffed in.
ACTION_PROFIT_EDGES = (
    "called-and-played-on is unpriced -- later pot is not assigned "
    "back to this bet. That number, when they called instead, is "
    "Call Profit Rate",
    "multiway: priced only when every other seat folds; a call from "
    "any one of them, or a showdown, is unpriced",
    "later streets after a call are not this action's money",
    "uncalled bet: +pot_before is the dead money they take; the "
    "uncalled chips come back and are not subtracted",
    "rake is not subtracted from +pot; if rake ate the pot (won=0) "
    "the line is unpriced rather than guessed as a loss",
    "MTT chips are not dollars -- tournament rows stay unpriced",
)

# Call Profit Rate. Sibling to Action Profit in the same spot, used
# to compare the two things they could do when both were legal.
#
# This is accounting of ACTUAL calls, not the EV of calling when they
# raised. Inventing the other action's result is a solver; a tracker
# reports what happened. The two means sit next to each other so a
# "Flop vs c-bet" report can say: when I raised, Action Profit was X
# (priced raises); when I called, Call Profit Rate was Y (priced calls).
#
# A priced call is: to_call > 0 (facing a bet or raise, including a
# limp), not all-in (so a raise was still possible), cash game.
# Profit is (won − chips this seat put in from THIS action on) / bb.
# Later streets are assigned back on purpose -- that is the point of
# the number, and why Action Profit leaves the same pot unpriced.
# won already has rake out where the site writes it; we do not guess
# a rake that was not written.
CALL_PROFIT_EDGES = (
    "only actual calls are priced -- a raise is not scored as if they "
    "had called (that would be EV, and we do not have it)",
    "raise had to be an option: to_call > 0 and not all-in. An all-in "
    "call is a call with no raise behind it",
    "profit is (won − chips this seat put in from this action on) / bb. "
    "Later streets are assigned back; that is accounting, not equity",
    "a limp is a call with raise available and is priced",
    "mean over priced calls, not over opportunities, not Action Profit, "
    "not Won$ of the whole hand (blinds and earlier streets stay out "
    "of the cost, and in won if they win)",
    "MTT chips are not dollars -- tournament rows stay unpriced",
)


def action_profit_sql(d="d", s="s"):
    """bb attributed to this decision, or NULL if v1 will not guess."""
    other = (
        f"EXISTS (SELECT 1 FROM decisions x "
        f"WHERE x.hand_id = {d}.hand_id AND x.n > {d}.n "
        f"AND x.seat <> {d}.seat AND ")
    self = (
        f"EXISTS (SELECT 1 FROM decisions x "
        f"WHERE x.hand_id = {d}.hand_id AND x.n > {d}.n "
        f"AND x.seat = {d}.seat AND ")
    cash = (f"{s}.fmt IS NOT NULL AND {s}.fmt <> 'MTT' "
            f"AND {d}.bb")
    uncontested = (
        f"{d}.agg = 1 AND {cash} "
        f"AND {s}.wtsd = 0 AND IFNULL({s}.won, 0) > 0 "
        f"AND NOT {other}x.action <> 'F')")
    bet_fold = (
        f"{d}.agg = 1 AND {cash} "
        f"AND {self}x.action = 'F') "
        f"AND {other}x.agg = 1)")
    return (
        f"CASE WHEN {d}.action = 'F' THEN 0 "
        f"WHEN {uncontested} THEN {d}.pot_before / {d}.bb "
        f"WHEN {bet_fold} THEN -IFNULL({d}.amount, 0) / {d}.bb "
        f"ELSE NULL END"
    )


def action_profit_of(con, where):
    """
    Profit attributed to the filtered action, not the hand, in bb/hand.

    The mean is over priced hits -- the matching actions v1 can score --
    not over opportunities (times they could have acted and did not)
    and not over Won$ of those hands. Unpriced hits stay in `n` and
    out of the mean, so a called pot cannot drag the figure to zero
    and look like a finding.
    """
    expr = action_profit_sql()
    row = con.execute(
        f"""
        SELECT
          COUNT(*) AS n,
          SUM({expr} IS NOT NULL) AS priced,
          SUM({expr}) AS profit,
          SUM(CASE WHEN {expr} IS NOT NULL THEN ({expr})*({expr}) END)
        FROM (SELECT * FROM decisions WHERE {where}) d
        LEFT JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat
        """
    ).fetchone()
    n, priced, profit, sumsq = row[0] or 0, row[1] or 0, row[2], row[3]
    _mean, lo, hi = mean_interval(profit, sumsq, priced)
    return {
        "n": n, "priced": priced, "unpriced": n - priced,
        "total_bb": profit if profit is not None else 0.0,
        "bb_per_hand": ((profit or 0.0) / priced) if priced else None,
        "lo": lo, "hi": hi,
        "per": "priced hits",
        "note": ("fold = 0; bet 5 into 10, all fold = +10; "
                 "bet 5, raise, fold = −5. Mean over priced hits, "
                 "not opportunities, not Won$."),
        "interval_note": (
            "sampling interval on the observed mean of priced hits, "
            "not EV" if lo is not None else
            "no interval -- one priced hit has no estimated spread"
            if priced == 1 else
            "no interval -- nothing priced"),
        "edges": list(ACTION_PROFIT_EDGES),
    }


def _chips_from_here_sql(d="d"):
    """Chips this seat put in from this decision through the end of the hand."""
    return (
        f"(SELECT SUM(IFNULL(x.amount, 0)) FROM decisions x "
        f"WHERE x.hand_id = {d}.hand_id AND x.seat = {d}.seat "
        f"AND x.n >= {d}.n)"
    )


def call_profit_sql(d="d", s="s"):
    """
    bb attributed to an actual call when raise was also legal, or NULL.

    Not the EV of calling. A raise in the same spot stays NULL so the
    mean cannot be dragged by a counterfactual we do not have.
    """
    later = _chips_from_here_sql(d)
    cash = (f"{s}.fmt IS NOT NULL AND {s}.fmt <> 'MTT' "
            f"AND {d}.bb")
    # action='C' is the call (and the limp). All-in calls are written
    # 'A' with agg=0; those have no raise behind them. allin=0 is the
    # same fact from the other column, so a missing allin cannot sneak
    # a shove into the mean.
    priced = (
        f"{d}.action = 'C' AND {d}.to_call > 0 "
        f"AND IFNULL({d}.allin, 0) = 0 "
        f"AND IFNULL({d}.amount, 0) > 0 "
        f"AND {cash} AND {s}.won IS NOT NULL"
    )
    return (
        f"CASE WHEN {priced} THEN ({s}.won - {later}) / {d}.bb "
        f"ELSE NULL END"
    )


def call_profit_of(con, where):
    """
    Realized profit of calls in this spot, when raise was also an option.

    The mean is over priced calls -- not over opportunities, not over
    the raises in the same filter, not over Won$ of those hands. A
    filter that is only raises comes back unpriced, which is the
    answer: there is no call here to account.
    """
    expr = call_profit_sql()
    row = con.execute(
        f"""
        SELECT
          COUNT(*) AS n,
          SUM({expr} IS NOT NULL) AS priced,
          SUM({expr}) AS profit,
          SUM(CASE WHEN {expr} IS NOT NULL THEN ({expr})*({expr}) END)
        FROM (SELECT * FROM decisions WHERE {where}) d
        LEFT JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat
        """
    ).fetchone()
    n, priced, profit, sumsq = row[0] or 0, row[1] or 0, row[2], row[3]
    _mean, lo, hi = mean_interval(profit, sumsq, priced)
    return {
        "n": n, "priced": priced, "unpriced": n - priced,
        "total_bb": profit if profit is not None else 0.0,
        "bb_per_hand": ((profit or 0.0) / priced) if priced else None,
        "lo": lo, "hi": hi,
        "per": "priced calls",
        "note": ("call 5, win the pot, no more chips in = +pot_before; "
                 "call 5 and lose = −5. Mean over priced calls, not "
                 "opportunities, not Action Profit, not EV."),
        "interval_note": (
            "sampling interval on the observed mean of priced calls, "
            "not EV" if lo is not None else
            "no interval -- one priced call has no estimated spread"
            if priced == 1 else
            "no interval -- nothing priced"),
        "edges": list(CALL_PROFIT_EDGES),
    }


def _profit_cells(p):
    """One pair of display cells: the mean, and n plus interval if honest."""
    if p.get("bb_per_hand") is not None:
        n = f"{p['priced']:,} priced"
        if p.get("lo") is not None:
            n += f" [{p['lo']:+.1f}, {p['hi']:+.1f}]"
        elif p.get("priced") == 1:
            n += " (n=1, no interval)"
        return f"{p['bb_per_hand']:+.2f} bb", n
    if p.get("n"):
        return "–", f"{p['n']:,} unpriced"
    return "–", "–"


def pin_sides(argv, name):
    """
    This filter vs a named report, same person.

    A pinned report is another situation. Leaving `--hero` off B
    would compare hero's flop c-bets to the pool's vs-c-bet and
    call the gap a finding.
    """
    return (situation_only(argv),
            who_only(argv) + without_who(resolve_filter(name)))


def compare_sides(who_argv, name_a, name_b):
    """Two named reports, same person as the leftover flags."""
    who = who_only(who_argv)
    return (who + without_who(resolve_filter(name_a)),
            who + without_who(resolve_filter(name_b)))


def compare_of(con, argv_a, argv_b, name_a=None, name_b=None,
               dim=None, columns=None, bet_sizes=False, packs=False):
    """
    Two spots, the numbers Hand2Note pins next to each other.

    Hits/opps, the primary frequency, hits per 1k, Action Profit
    v1 when priced, and Call Profit Rate when a call with raise
    available is in the filter. The frequency gap is an interval on
    the DIFFERENCE (Newcombe), not whether the two bands overlap.
    Profit is two means sitting next to each other. Each carries its
    n and, when two or more rows were priced, a t interval on that
    mean -- sampling uncertainty, not EV. n=1 prints the mean and
    says why there is no band.

    The compact summary is always there. `--by` adds two breakdown
    grids of the same dimension; `--bet-sizes` / `--by size` adds
    two size tables; `packs` adds both sides' full stat lists. That
    is the richer pin -- two filters' breakdowns, not only THIS vs
    PINNED as six rows.
    """
    where_a, label_a, _ = build(situation_only(argv_a))
    where_b, label_b, _ = build(situation_only(argv_b))
    where_a, where_b = with_cohort(con, where_a), with_cohort(con, where_b)
    sa = spot_summary(con, where_a, argv_a)
    sb = spot_summary(con, where_b, argv_b)
    pa = action_profit_of(con, where_a)
    pb = action_profit_of(con, where_b)
    ca = call_profit_of(con, where_a)
    cb = call_profit_of(con, where_b)
    wa = amount_won_of(con, where_a) if pack_wants_won(argv_a) else None
    wb = amount_won_of(con, where_b) if pack_wants_won(argv_b) else None
    freq_diff = None
    if sa.get("opps") and sb.get("opps"):
        d, lo, hi, pv = difference(sa["hits"], sa["opps"],
                                   sb["hits"], sb["opps"])
        freq_diff = {"d": d, "lo": lo, "hi": hi, "p": pv}
    out = {
        "a": {"name": name_a, "label": label_a, "argv": list(argv_a),
              "summary": sa, "profit": pa, "call_profit": ca,
              "amount_won": wa},
        "b": {"name": name_b, "label": label_b, "argv": list(argv_b),
              "summary": sb, "profit": pb, "call_profit": cb,
              "amount_won": wb},
        "freq_diff": freq_diff,
    }
    want_sizes = bet_sizes or dim == "size" or packs
    if want_sizes:
        out["sizes"] = {
            "a": bet_sizes_of(con, where_a, argv_a),
            "b": bet_sizes_of(con, where_b, argv_b),
        }
    if dim and dim in DIMENSIONS and dim != "size":
        cols = list(columns or columns_for(argv_a))
        out["by"] = {
            "dim": dim,
            "a": report_of(con, where_a, dim, cols, argv_a),
            "b": report_of(con, where_b, dim, cols, argv_b),
        }
    if packs:
        _n_a, rows_a = stats_of(con, where_a)
        _n_b, rows_b = stats_of(con, where_b)
        out["packs"] = {"a": rows_a, "b": rows_b}
    return out


def show_compare(got):
    """The two-column pin summary, for a terminal."""
    a, b = got["a"], got["b"]
    head_a = a.get("name") or "this"
    head_b = b.get("name") or "pinned"
    print(f"\nTHIS  {head_a}")
    print(f"      {a['label']}")
    print(f"PIN   {head_b}")
    print(f"      {b['label']}")
    print("=" * 64)

    def cells(side):
        s, p = side["summary"], side["profit"]
        hits = (f"{s['hits']:,} / {s['opps']:,}" if s.get("opps") else "–")
        freq = f"{s['pct']:.1f}%" if s.get("opps") else "–"
        per = f"{s['per_1k']:.1f}" if s.get("hands") else "–"
        ap, priced = _profit_cells(p)
        cp, cpriced = _profit_cells(side.get("call_profit") or {})
        won = side.get("amount_won") or {}
        if won.get("bb_per_hand") is not None:
            wcell = f"{won['bb_per_hand']:+.2f} bb"
            wn = f"{won['hands']:,} hands"
            if won.get("won_pct") is not None:
                wn += f"  won {won['won_pct']:.0f}%"
        else:
            wcell, wn = "–", "–"
        return hits, freq, per, ap, priced, cp, cpriced, wcell, wn

    ca, cb = cells(a), cells(b)
    rows = (("hits / opps", ca[0], cb[0]),
            (f"freq  ({a['summary'].get('label') or 'hits'})", ca[1], cb[1]),
            ("hits / 1000", ca[2], cb[2]),
            ("action profit", ca[3], cb[3]),
            ("", ca[4], cb[4]),
            ("call profit", ca[5], cb[5]),
            ("", ca[6], cb[6]))
    if a.get("amount_won") or b.get("amount_won"):
        rows = rows + (("Won$", ca[7], cb[7]),
                       ("", ca[8], cb[8]))
    print(f"{'':20} {'THIS':>18} {'PINNED':>18}")
    for label, x, y in rows:
        print(f"{label:20} {x:>18} {y:>18}")
    diff = got.get("freq_diff")
    if diff and diff.get("d") is not None:
        print()
        print(f"freq THIS − PINNED   {100 * diff['d']:+.1f} pts  "
              f"[{100 * diff['lo']:+.1f}, {100 * diff['hi']:+.1f}]")
        print("  interval on the difference, not whether the two "
              "bands overlap")
    print()
    print("  Action Profit is v1 (priced hits). Call Profit is actual "
          "calls when raise was also legal. A dash is unpriced. "
          "A band next to n is a t interval on the observed mean, "
          "not EV; n=1 has no band. --versus is the Holm table.")
    if got.get("sizes"):
        show_compare_sizes(got)
    if got.get("by"):
        show_compare_by(got)
    if got.get("packs"):
        show_compare_packs(got)


def _ap_cell(p):
    if not p or p.get("bb_per_hand") is None:
        return "–"
    return f"{p['bb_per_hand']:+.2f}"


def show_compare_sizes(got):
    """Two Bet Sizes tables, aligned on the same letters."""
    a = (got.get("sizes") or {}).get("a") or {}
    b = (got.get("sizes") or {}).get("b") or {}
    by_a = {r["key"]: r for r in a.get("rows") or []}
    by_b = {r["key"]: r for r in b.get("rows") or []}
    keys = [k for k in lines.BUCKETS if k in by_a or k in by_b]
    if not keys:
        return
    print("BET SIZES")
    print(f"{'':22} {'THIS':^28} {'PINNED':^28}")
    print(f"{'':22} {'hits/opps':>12} {'freq':>8} {'AP':>7} "
          f"{'hits/opps':>12} {'freq':>8} {'AP':>7}")
    for k in keys:
        ra, rb = by_a.get(k) or {}, by_b.get(k) or {}
        print(f"{SIZE_NAMES[k]:22} "
              f"{_hits_cell(ra):>12} {_freq_cell(ra):>8} {_ap_cell(ra.get('profit')):>7} "
              f"{_hits_cell(rb):>12} {_freq_cell(rb):>8} {_ap_cell(rb.get('profit')):>7}")
    print()
    print("  freq is this size of the parent filter. Checks and folds "
          "have no size, so the column will not sum to 100% there.")
    print("  AP is Action Profit v1 on those hits. --size LETTER opens "
          "a row. Later pot on a called bet stays unpriced.")
    print()


def _hits_cell(r):
    if not r or r.get("opps") is None:
        return "–"
    return f"{r.get('hits', 0):,}/{r.get('opps', 0):,}"


def _freq_cell(r):
    if not r or not r.get("opps"):
        return "–"
    return f"{r.get('pct', 0):.1f}%"


def show_compare_by(got):
    """Two `--by` grids, one dimension, keys aligned."""
    blob = got.get("by") or {}
    ga, gb = blob.get("a") or {}, blob.get("b") or {}
    dim = blob.get("dim") or ga.get("dim") or "by"
    cols = ga.get("cols") or gb.get("cols") or []
    order = DIMENSIONS[dim][1] if dim in DIMENSIONS else str
    keys = sorted(set(ga.get("keys") or []) | set(gb.get("keys") or []),
                  key=lambda k: order(k) if k is not None else "")
    if not keys or not cols:
        return
    print(f"BY {dim}")
    head = f"{'':14}"
    for side in ("THIS", "PINNED"):
        head += f" {side:^{(11 * len(cols)) + 8}}"
    print(head)
    print(f"{dim[:13]:<14}" + "".join(
        f"{BY_KEY[c].label[:9]:>11}" for c in cols) + f"{'n':>8}"
          + "".join(f"{BY_KEY[c].label[:9]:>11}" for c in cols) + f"{'n':>8}")
    for k in keys:
        cells = []
        for grid, counts in ((ga.get("grid") or {}, ga.get("counts") or {}),
                             (gb.get("grid") or {}, gb.get("counts") or {})):
            for c in cols:
                n, kk = grid.get(c, {}).get(k, (0, 0))
                cells.append(f"{'--':>11}" if not n else f"{100 * kk / n:9.1f}% ")
            cells.append(f"{counts.get(k, 0):>8,}")
        print(f"{str(k)[:13]:<14}" + "".join(cells))
    print()
    print("  two filters, one split. '?' is not marked here -- n is "
          "the filter's own decisions in that bucket.")
    print()


def show_compare_packs(got):
    """Two full stat packs, keys aligned."""
    pa = {r["key"]: r for r in (got.get("packs") or {}).get("a") or []}
    pb = {r["key"]: r for r in (got.get("packs") or {}).get("b") or []}
    keys = []
    seen = set()
    for r in ((got.get("packs") or {}).get("a") or []) + \
             ((got.get("packs") or {}).get("b") or []):
        if r["key"] not in seen:
            seen.add(r["key"])
            keys.append(r["key"])
    if not keys:
        return
    print("STATS")
    print(f"{'':22} {'THIS':>18} {'PINNED':>18}")
    last = None
    for key in keys:
        a, b = pa.get(key), pb.get(key)
        group = (a or b or {}).get("group")
        if group != last:
            print(f"  [{group}]")
            last = group
        label = (a or b)["label"]
        print(f"{label:22} {_pack_cell(a):>18} {_pack_cell(b):>18}")
    print()


def _pack_cell(r):
    if not r or not r.get("n"):
        return "–"
    return f"{r['pct']:.1f}% n={r['n']:,}"


def show_bet_sizes(con, where, label, argv=None, parts=()):
    """The Bet Sizes pane, for a terminal."""
    print(f"\nfilter: {label}")
    print("bet sizes")
    print("=" * (len(label) + 8))
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    print(f"{n} decisions match")
    if not n:
        print("  " + why_empty(con, parts))
        return
    blob = bet_sizes_of(con, where, argv)
    _print_bet_sizes(blob)
    print("  apply a row with --size LETTER, or click it.")
    print("  freq is this size of the parent filter. Checks and folds")
    print("  have no size, so the column will not sum to 100% there.")
    print("  act bb is Action Profit v1 on those hits -- later pot on")
    print("  a called bet stays unpriced. A dash is unpriced.")


def matching_hands(con, where, limit=None):
    """
    Each (hand, seat) the filter selected, with hand net and action profit.

    `net_bb` is what the hand did. `act_bb` is what THIS action did.
    `call_bb` is Call Profit Rate on the same row -- only a call with
    raise available is priced. The three sitting next to each other
    is the point: a won hand whose cbet was called is +net, unpriced
    act, and (if they were the caller) a priced call, which is a
    different sentence from mixing them into one number.
    """
    expr = action_profit_sql()
    call = call_profit_sql()
    sql = (
        f"SELECT d.hand_id, d.seat, MAX(d.played_at), MAX(d.site), "
        f"       MAX(d.bb), MAX(d.position), MAX(d.combo), MAX(d.board), "
        f"       MAX(s.net_bb), SUM({expr}), "
        f"       SUM({expr} IS NOT NULL), COUNT(*), SUM({call}) "
        f"FROM (SELECT * FROM decisions WHERE {where}) d "
        f"LEFT JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat "
        f"GROUP BY d.hand_id, d.seat "
        f"ORDER BY MAX(d.played_at) DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for (hid, seat, when, site, bb, pos, combo, board, net, act, priced,
         hits, call_bb) in con.execute(sql):
        rows.append({
            "id": hid, "seat": seat, "when": when, "site": site, "bb": bb,
            "pos": pos, "combo": combo, "board": board, "net": net,
            "act": act, "priced": priced or 0, "hits": hits or 0,
            "call": call_bb,
        })
    return rows


def chain_of(con, where, same_seat=False):
    """
    The first later action, by the other seat or by this player.

    Faced Next is `same_seat=False`: what the table did after this
    decision. Next Actions is `same_seat=True`: what this player did
    the next time they acted. Each row is a `--after` / `--then` you
    can open, which is how Hand2Note walks a tree without a second
    filter language.
    """
    cmp = "=" if same_seat else "<>"
    rows = con.execute(
        f"""
        SELECT CASE
                 WHEN x.action = 'F' THEN 'fold'
                 WHEN x.action = 'X' THEN 'check'
                 WHEN x.action IN ('C','A') AND x.agg = 0 THEN 'call'
                 WHEN x.agg = 1 AND x.to_call = 0 THEN 'bet'
                 WHEN x.agg = 1 THEN 'raise'
                 ELSE 'other' END AS verb,
               COUNT(*)
        FROM (SELECT * FROM decisions WHERE {where}) d
        JOIN decisions x
          ON x.hand_id = d.hand_id AND x.seat {cmp} d.seat
         AND x.n = (SELECT MIN(y.n) FROM decisions y
                    WHERE y.hand_id = d.hand_id AND y.n > d.n
                      AND y.seat {cmp} d.seat)
        GROUP BY 1
        """
    ).fetchall()
    none = con.execute(
        f"""
        SELECT COUNT(*) FROM (SELECT * FROM decisions WHERE {where}) d
        WHERE NOT EXISTS (
          SELECT 1 FROM decisions y
          WHERE y.hand_id = d.hand_id AND y.n > d.n
            AND y.seat {cmp} d.seat)
        """
    ).fetchone()[0]
    total = sum(n for _v, n in rows) + none
    out = []
    order = ("fold", "check", "call", "bet", "raise", "other")
    by = {v: n for v, n in rows}
    for verb in order:
        n = by.get(verb, 0)
        if not n or not total:
            continue
        p, lo, hi = wilson(n, total)
        out.append({"key": verb, "label": verb, "n": total, "k": n,
                    "pct": 100 * p, "band": 100 * (hi - lo) / 2})
    if none and total:
        p, lo, hi = wilson(none, total)
        out.append({"key": "", "label": "nothing further",
                    "n": total, "k": none,
                    "pct": 100 * p, "band": 100 * (hi - lo) / 2})
    # Squeeze is any later squeeze, not the first later action, so it
    # can sit on an open (first later action = the call) without
    # stealing the raise row. It is an overlay: the first-action rows
    # still partition.
    squeeze_n = con.execute(
        f"""
        SELECT COUNT(*) FROM (SELECT * FROM decisions WHERE {where}) d
        WHERE {_later_any_sql(same_seat, AFTER["squeeze"], "d")}
        """
    ).fetchone()[0]
    if squeeze_n and total:
        p, lo, hi = wilson(squeeze_n, total)
        out.append({"key": "squeeze", "label": "squeeze",
                    "n": total, "k": squeeze_n,
                    "pct": 100 * p, "band": 100 * (hi - lo) / 2})
    return {"n": total, "rows": out,
            "flag": "--then" if same_seat else "--after"}


def chain_report(con, where, argv=None, same_seat=False):
    """
    Faced Next / Next Actions as a report, not just a mix.

    Each row is a child filter (`--after fold`, `--then bet`,
    `--after none`). Hits are the branch, opportunities are the
    parent spot, and Action Profit is v1 on the parent actions that
    took this branch -- "I cbet and they folded" is +pot, not Won$
    of the hand. Clicking a row is applying that flag.
    """
    blob = chain_of(con, where, same_seat)
    flag = blob["flag"]
    opps = blob["n"]
    argv = situation_only(list(argv or []))
    for r in blob["rows"]:
        key = r["key"] or "none"
        if key == "none":
            extra = _ended_sql(same_seat)
        elif key == "squeeze":
            extra = _later_any_sql(same_seat, AFTER["squeeze"])
        else:
            extra = _next_sql(same_seat, AFTER[key])
        child_where = f"({where}) AND ({extra})"
        r["hits"] = r["k"]
        r["opps"] = opps
        r["key"] = key
        r["how"] = f"{flag} {key}"
        r["profit"] = action_profit_of(con, child_where)
        r["argv"] = list(argv) + [flag, key]
    return blob


def _ended_sql(same_seat):
    """No later action by the other seat, or by this player."""
    cmp = "=" if same_seat else "<>"
    return (
        "NOT EXISTS (SELECT 1 FROM decisions x "
        "WHERE x.hand_id = decisions.hand_id AND x.n > decisions.n "
        f"AND x.seat {cmp} decisions.seat)"
    )


def outcomes_of(con, where):
    """
    What the table did with this bet: fold out / call / raise-back.

    Hand2Note's outcome block, and a different question from Faced Next.
    Faced Next is the first later action by another seat. This is the
    pot's answer: did every remaining player fold, did someone call
    without raising, or did someone raise. A check after the bet sits
    with call -- they continued passively. Only aggressive rows have
    an outcome; a fold you already made has none.
    """
    bits = ", ".join(
        f'SUM({sql}) AS "{key}"' for key, _label, sql in OUTCOME_MIX)
    row = con.execute(
        f"SELECT COUNT(*) AS n, SUM(agg = 1) AS bets, {bits} "
        f"FROM decisions WHERE {where}").fetchone()
    n, bets = row[0] or 0, row[1] or 0
    out = []
    if not bets:
        return {"n": n, "bets": 0, "rows": out}
    for i, (key, label, _sql) in enumerate(OUTCOME_MIX):
        k = row[i + 2] or 0
        if not k:
            continue
        p, lo, hi = wilson(k, bets)
        out.append({"key": key, "label": label, "n": bets, "k": k,
                    "pct": 100 * p, "band": 100 * (hi - lo) / 2})
    return {"n": n, "bets": bets, "rows": out, "flag": "--outcome"}


def bet_sizes_of(con, where, argv=None):
    """
    Hits/opps, freq, Action Profit by pot-frac bucket.

    The current filter is the opportunities. Each row is the matching
    decisions whose pot_frac lands in that letter -- the same edges as
    `--size m` and `lines.bucket`. Checks and folds have no size and
    are not a row; that is why the freqs will not sum to 100% on a
    situation that includes them.

    Clicking a row is `--size` of that letter. Action Profit is v1 on
    those hits -- later pot on a called bet stays unpriced.
    """
    argv = situation_only(list(argv or []))
    opps = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    rows = []
    for letter in lines.BUCKETS:
        child = f"({where}) AND ({size_sql(letter)})"
        hits = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {child}").fetchone()[0]
        if not hits:
            continue
        p, lo, hi = wilson(hits, opps) if opps else (0.0, 0.0, 0.0)
        rows.append({
            "key": letter,
            "label": SIZE_NAMES[letter],
            "hits": hits,
            "opps": opps,
            "n": opps,
            "k": hits,
            "pct": 100 * p,
            "band": 100 * (hi - lo) / 2,
            "profit": action_profit_of(con, child),
            "how": f"--size {letter}",
            "argv": list(argv) + ["--size", letter],
        })
    return {"n": opps, "rows": rows, "flag": "--size"}


def report_of(con, where, dim, columns, argv=None):
    """
    The `--by` grid as data, so pin can draw two of them.

    Same numbers `show_report` prints: rates per bucket, the filter's
    own n (not VPIP's), and Won$ when the pack carries it.
    """
    expr, order = DIMENSIONS[dim]
    grid = {c: rates_by(con, BY_KEY[c], expr, where) for c in columns}
    won_by = (amount_won_by(con, where, expr)
              if pack_wants_won(argv or []) else {})
    counts = counts_by(con, expr, where)
    keys = sorted({k for g in grid.values() for k in g} | set(counts),
                  key=lambda k: order(k) if k is not None else "")
    return {"dim": dim, "cols": list(columns), "grid": grid,
            "counts": counts, "keys": keys, "won_by": won_by}


def counts_by(con, expr, where):
    """Decisions per bucket of a dimension -- the n of a situational report."""
    return dict(con.execute(
        f"SELECT {expr}, COUNT(*) FROM decisions WHERE ({where}) "
        f"AND ({expr}) IS NOT NULL GROUP BY 1"))


# ---- study cockpit ----------------------------------------------------
# Hand2Note's Reports window is one filter, several panes, and a click
# that ANDs the row onto the filter. The CLI already has every number
# those panes show. This is the same data as a payload the window and
# the page can both draw, so a click is `drill_child` rather than a
# second WHERE clause either front end invents.

DRILL_MAX = 3

# Finer than `--by stack`. The coarse dimension stays for CLI reports;
# the study pane needs an 80-120 row because that is the cut people
# actually click, and folding it into 40-99 / 100-199 hid it.
STUDY_STACKS = (
    ("<20", None, 20),
    ("20-40", 20, 40),
    ("40-80", 40, 80),
    ("80-120", 80, 120),
    ("120-200", 120, 200),
    ("200+", 200, None),
)

# Whole-hand result. MTT is excluded in the SQL: chips are not dollars,
# and `results_of` already refuses those seats.
RESULTS = {
    "won": "s.net_bb > 0",
    "lost": "s.net_bb < 0",
    "even": "s.net_bb = 0",
    "showdown": "s.wtsd = 1",
    "no-showdown": "s.wtsd = 0",
}
RESULT_ALIAS = {
    "won": "won", "win": "won", "won$": "won",
    "lost": "lost", "lose": "lost", "loss": "lost",
    "even": "even", "chop": "even",
    "showdown": "showdown", "sd": "showdown", "wtsd": "showdown",
    "no-showdown": "no-showdown", "nosd": "no-showdown",
    "no_showdown": "no-showdown",
}
RESULT_LABELS = {
    "won": "Won",
    "lost": "Lost",
    "even": "Even",
    "showdown": "Showdown",
    "no-showdown": "No showdown",
}

# Preflop families the study strip offers. Axs is the one the demo
# clicks; the rest are the same idea one rank over, so a click is not
# a one-off.
COMBO_FAMILIES = (
    "Axs", "Kxs", "Qxs", "Jxs", "AKo", "AKs", "pairs", "broadways",
)

# When the filter has not already named a street or a facing, the Next
# Action pane also offers the vs-open-raise rows Hand2Note prints on
# an unfiltered report. Clicking one is street + facing + this action,
# not `--then call` -- that is the next row, and "Call vs OR" is this
# one.
STUDY_VS_OR = (
    ("Fold vs OR", "fold"),
    ("Call vs OR", "call"),
    ("3-bet vs OR", "raise"),
)

DEFAULT_STUDY_PANES = ("results", "stack", "position", "next")
PLUS_STUDY_PANES = ("size", "made", "board", "combo")
STUDY_PANE_LABELS = {
    "results": "Results",
    "stack": "Stack Sizes",
    "position": "Positions",
    "next": "Next Action",
    "size": "Bet Sizes",
    "made": "Flop Hand",
    "board": "Flop Board",
    "combo": "Combos",
}

RANKS = "AKQJT98765432"


def expand_combo(token):
    """
    A combo, or a family of them.

    `AKs` stays `AKs`. `Axs` is every suited ace, `Kxo` every offsuit
    king, `22+` every pair at or above twos, `broadways` the ten
    broadway suited+offsuit pairs people actually name. A family that
    did not expand would be `combo IN ('Axs')`, which matches nothing
    and looks like a broken click.
    """
    raw = (token or "").strip()
    if not raw:
        return []
    if raw.lower() == "pairs":
        return [r + r for r in RANKS]
    if raw.lower() == "broadways":
        hi = "AKQJT"
        out = []
        for i, a in enumerate(hi):
            for b in hi[i + 1:]:
                out.append(a + b + "s")
                out.append(a + b + "o")
        return out
    t = raw[0].upper() + raw[1:]
    if len(t) == 3 and t[1] in "xX" and t[2] in "so":
        hi = t[0]
        if hi not in RANKS:
            return [raw]
        return [hi + r + t[2] for r in RANKS[RANKS.index(hi) + 1:]]
    if len(t) >= 3 and t.endswith("+") and len(t) >= 3:
        pair = t[:2]
        if len(pair) == 2 and pair[0] == pair[1] and pair[0] in RANKS:
            return [r + r for r in RANKS if RANKS.index(r) <= RANKS.index(pair[0])]
    return [t]


def result_sql(key):
    """This (hand, seat) has that whole-hand result. Cash only."""
    pred = RESULTS[key]
    return (
        "EXISTS (SELECT 1 FROM spots s "
        "WHERE s.hand_id = decisions.hand_id AND s.seat = decisions.seat "
        f"AND s.fmt <> 'MTT' AND {pred})"
    )


def drill_child(parent_argv, step):
    """
    The child filter: parent ∧ row_key.

    A row that names the same flag as the parent replaces it -- two
    `--stack` values AND-ed match nothing and look like a broken
    pane. A composed row (Call vs OR) is several flags; each is
    replaced the same way. Who is being measured is left alone.
    """
    parent = situation_only(list(parent_argv or []))
    extra = list(step.get("argv") or [])
    if step.get("flag"):
        extra = [step["flag"], str(step.get("value", ""))]
    i = 0
    out = parent
    while i < len(extra):
        a = extra[i]
        if a in VALUE_FLAGS or a in OPTIONS:
            if i + 1 >= len(extra):
                break
            out = _replace_flag(out, a, extra[i + 1])
            i += 2
        elif a in SWITCHES or a in STUDY_SWITCHES:
            out = _replace_flag(out, a)
            i += 1
        else:
            out.append(a)
            i += 1
    return out


def drill_stack(parent_argv, crumbs):
    """Fold a breadcrumb list onto the parent. Caps at DRILL_MAX."""
    argv = situation_only(list(parent_argv or []))
    for step in list(crumbs or [])[:DRILL_MAX]:
        argv = drill_child(argv, step)
    return argv


def _row(key, label, n, opps, flag, value, argv, extra=None):
    """One clickable pane row, same shape every pane uses."""
    p, lo, hi = wilson(n, opps) if opps else (0.0, 0.0, 0.0)
    row = {
        "key": key, "label": label, "n": n, "k": n, "hits": n, "opps": opps,
        "pct": 100 * p, "band": 100 * (hi - lo) / 2,
        "flag": flag, "value": value,
        "argv": list(argv) + ([flag, value] if flag else []),
        "how": f"{flag} {value}" if flag else label,
    }
    if extra:
        row.update(extra)
    return row


def study_stacks_of(con, where, argv=None):
    """Hits per study stack bucket. Clicking a row is `--stack` of that cut."""
    argv = situation_only(list(argv or []))
    opps = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    rows = []
    for label, lo, hi in STUDY_STACKS:
        child = f"({where}) AND ({stack_sql(lo, hi)})"
        n = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {child}").fetchone()[0]
        if not n:
            continue
        rows.append(_row(label, label, n, opps, "--stack", label, argv,
                         {"profit": action_profit_of(con, child)}))
    return {"id": "stack", "flag": "--stack", "n": opps, "rows": rows}


def results_breakdown(con, where, argv=None):
    """Won / lost / showdown as clickable rows over the matching seats."""
    argv = situation_only(list(argv or []))
    pairs = matching_seats(con, where)
    tot = results_of(con, pairs) if pairs else None
    opps = tot["hands"] if tot else 0
    rows = []
    for key, label in RESULT_LABELS.items():
        child = f"({where}) AND ({result_sql(key)})"
        seats = matching_seats(con, child)
        got = results_of(con, seats) if seats else None
        if not got:
            continue
        extra = {"hands": got["hands"], "net_bb": got["net_bb"],
                 "bb100": got["bb100"], "error": got["error"],
                 "totals": got}
        rows.append(_row(key, label, got["hands"], opps or got["hands"],
                         "--result", key, argv, extra))
    return {"id": "results", "flag": "--result", "n": opps,
            "totals": tot, "rows": rows}


def study_positions_of(con, where, argv=None, columns=None):
    """`--by position` as pane rows. Clicking a row is `--pos`."""
    argv = situation_only(list(argv or []))
    cols = list(columns or columns_for(argv))
    got = report_of(con, where, "position", cols, argv)
    opps = sum(got["counts"].values()) if got["counts"] else 0
    rows = []
    for k in got["keys"]:
        if k is None:
            continue
        n = got["counts"].get(k, 0)
        if isinstance(n, tuple):
            n = n[0]
        if not n:
            continue
        cells = {}
        for c in cols:
            nn, kk = got["grid"][c].get(k, (0, 0))
            cells[c] = None if not nn else {
                "pct": 100 * kk / nn, "n": nn, "k": kk}
        rows.append(_row(str(k), str(k), n, opps, "--pos", str(k), argv,
                         {"cells": cells}))
    return {"id": "position", "flag": "--pos", "n": opps,
            "cols": cols, "rows": rows, "won_by": got.get("won_by") or {}}


def study_next_of(con, where, argv=None):
    """
    What they DID here, plus vs-OR rows when the filter is still broad.

    `--then` stays on the stats tab as Faced Next's sibling. The study
    pane's job is the H2N "Call vs OR" click, which is this action
    facing an open, not the next street.
    """
    argv = situation_only(list(argv or []))
    acts = actions_of(con, where)
    opps = acts["n"]
    rows = []
    for r in acts["mix"]:
        if not r["k"]:
            continue
        child = f"({where}) AND ({dict(ACTION_MIX)[r['key']]})"
        rows.append(_row(r["key"], r["label"], r["k"], opps,
                         "--action", r["key"], argv,
                         {"profit": action_profit_of(con, child),
                          "band": r["band"], "pct": r["pct"]}))
    streets = set(flag_values(argv, "--street"))
    facing = set(flag_values(argv, "--facing"))
    if not streets and not facing:
        for label, action in STUDY_VS_OR:
            step = {"argv": ["--street", "preflop", "--facing", "open",
                             "--action", action]}
            child_argv = drill_child(argv, step)
            child_where, _, _ = build(child_argv)
            n = con.execute(
                f"SELECT COUNT(*) FROM decisions WHERE {child_where}"
            ).fetchone()[0]
            if not n:
                continue
            p, lo, hi = wilson(n, opps) if opps else (0.0, 0.0, 0.0)
            rows.append({
                "key": label, "label": label, "n": n, "k": n,
                "hits": n, "opps": opps,
                "pct": 100 * p, "band": 100 * (hi - lo) / 2,
                "flag": None, "value": label,
                "argv": child_argv,
                "how": " ".join(step["argv"]),
                "profit": action_profit_of(con, child_where),
                "composed": True,
            })
    later = chain_report(con, where, argv, True)
    return {"id": "next", "flag": "--action", "n": opps, "rows": rows,
            "then": later}


def study_combos_of(con, where, argv=None):
    """Family rows (Axs, pairs, …) for the + Combos pane and the strip."""
    argv = situation_only(list(argv or []))
    opps = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where} "
        f"AND combo IS NOT NULL").fetchone()[0]
    rows = []
    for fam in COMBO_FAMILIES:
        combos = expand_combo(fam)
        if not combos:
            continue
        items = ", ".join(q(c) for c in combos)
        child = f"({where}) AND combo IN ({items})"
        n = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {child}").fetchone()[0]
        if not n:
            continue
        rows.append(_row(fam, fam, n, opps or n, "--combo", fam, argv))
    return {"id": "combo", "flag": "--combo", "n": opps, "rows": rows}


def study_made_of(con, where, argv=None):
    """`--by hand` (what the flop became). Clicking is `--made`."""
    argv = situation_only(list(argv or []))
    got = report_of(con, where, "hand", [], argv)
    # report_of with no columns still has counts/keys.
    opps = sum(got["counts"].values()) if got["counts"] else 0
    rows = []
    for k in got["keys"]:
        if not k:
            continue
        n = got["counts"].get(k, 0)
        if isinstance(n, tuple):
            n = n[0]
        if not n:
            continue
        rows.append(_row(str(k), str(k), n, opps, "--made", str(k), argv))
    return {"id": "made", "flag": "--made", "n": opps, "rows": rows}


def study_board_of(con, where, argv=None):
    """Flop texture rows. Clicking is `--board`."""
    argv = situation_only(list(argv or []))
    got = report_of(con, where, "texture", [], argv)
    opps = sum(got["counts"].values()) if got["counts"] else 0
    # `--board` wants the named texture keys (mono, paired, …), not
    # the CASE labels (monotone, paired). Map the display back.
    named = {
        "monotone": "mono", "two-tone": "twotone", "paired": "paired",
        "connected": "connected", "dry": "dry",
    }
    rows = []
    for k in got["keys"]:
        if not k:
            continue
        n = got["counts"].get(k, 0)
        if isinstance(n, tuple):
            n = n[0]
        if not n:
            continue
        flag_val = named.get(str(k), str(k))
        rows.append(_row(str(k), str(k), n, opps, "--board", flag_val, argv))
    return {"id": "board", "flag": "--board", "n": opps, "rows": rows}


def study_of(con, where, argv, panes=None, pin=""):
    """
    One payload for the Reports study cockpit.

    Summary + default panes + compact hands. Extra panes are cheap and
    only built when asked. Pin is the same `compare_of` the other
    views already use -- a named report or a JSON argv list, so
    pinning another stack is `["--stack","120-200"]`, not a second
    compare language.
    """
    argv = situation_only(list(argv or []))
    panes = list(panes or DEFAULT_STUDY_PANES)
    cols = columns_for(argv)
    out = {
        "view": "study",
        "columns": cols,
        "summary": spot_summary(con, where, argv),
        "actions": actions_of(con, where),
        "profit": action_profit_of(con, where),
        "call_profit": call_profit_of(con, where),
        "related": related_spots(argv),
        "panes": {},
        "plus": list(PLUS_STUDY_PANES),
        "defaults": list(DEFAULT_STUDY_PANES),
    }
    builders = {
        "results": lambda: results_breakdown(con, where, argv),
        "stack": lambda: study_stacks_of(con, where, argv),
        "position": lambda: study_positions_of(con, where, argv, cols),
        "next": lambda: study_next_of(con, where, argv),
        "size": lambda: bet_sizes_of(con, where, argv),
        "made": lambda: study_made_of(con, where, argv),
        "board": lambda: study_board_of(con, where, argv),
        "combo": lambda: study_combos_of(con, where, argv),
    }
    for name in panes:
        if name in builders:
            blob = builders[name]()
            blob.setdefault("id", name)
            blob.setdefault("title", STUDY_PANE_LABELS.get(name, name))
            out["panes"][name] = blob
    # Combo-family chips on the Smart strip, even when the Combos pane
    # is off -- Axs has to be one click from unfiltered.
    out["families"] = study_combos_of(con, where, argv)
    hands = matching_hands(con, where, limit=400)
    notes.decorate(con, hands)
    compact.attach(con, hands, fmt="text")
    out["hands"] = hands
    if pin:
        try:
            argv_a, argv_b = pin_sides(argv, pin)
            out["compare"] = compare_of(
                con, argv_a, argv_b, None, pin, packs=True)
        except SystemExit as e:
            out["pinned"] = {"error": str(e), "name": pin}
    return out


def _replace_flag(argv, flag, value=None):
    """argv with this flag removed, or replaced by a new value."""
    out = []
    i = 0
    argv = situation_only(list(argv))
    while i < len(argv):
        a = argv[i]
        if a == flag:
            i += 2 if (a in VALUE_FLAGS or a in OPTIONS) else 1
            continue
        if a in VALUE_FLAGS or a in OPTIONS:
            out += argv[i:i + 2]
            i += 2
        else:
            out.append(a)
            i += 1
    if value is None:
        if flag in SWITCHES:
            out.append(flag)
    else:
        out += [flag, value]
    return out


def related_spots(argv, path=None, limit=8):
    """
    Spots a Hand2Note user would click next from this filter.

    Two sources, on purpose the same two H2N has: the named reports that
    sit next to this one in the tree, and a handful of generated variants
    (the other seat, in or out of position, the next street). Each item
    is a filter that `build` already understands, so clicking one is
    opening a report rather than inventing a second vocabulary.

    `why` is the reason it was offered -- "the other seat", "the next
    street" -- because a list of report names with no relation to the
    page you are on is just a shorter report box.
    """
    argv = without_who(argv)
    here = matching_report(argv, path)
    seen = {_canonical(argv)}
    out = []

    def add(name, flags, why):
        key = _canonical(flags)
        if not key or key in seen:
            return
        try:
            build(list(flags))
        except SystemExit:
            return
        seen.add(key)
        out.append({"name": name, "argv": list(flags), "why": why,
                    "preset": name if name in reports(path) else ""})

    if here:
        for name in REPORT_RELATED.get(here, ()):
            if name in SMART_REPORTS:
                add(name, SMART_REPORTS[name], "related report")
        family = REPORT_FAMILY.get(here)
        if family:
            for name, flags in SMART_REPORTS.items():
                if name != here and REPORT_FAMILY.get(name) == family:
                    add(name, flags, f"same street" if family in STREETS
                        else "same family")

    streets = flag_values(argv, "--street")
    street = streets[0] if len(streets) == 1 else None
    facing = set(flag_values(argv, "--facing"))
    # 'check' / 'bet' / 'raise' are postflop words; 'open' / '3bet' are
    # preflop ones. Offering the neighbouring street without dropping a
    # facing that cannot occur there is how "related" used to open an
    # empty page -- the thing `why_empty` already has a sentence for.
    pre_face = bool(facing & set(PREFLOP_FACING))
    post_face = bool(facing & set(POSTFLOP_FACING))
    if street in STREETS:
        i = STREETS.index(street)
        if i + 1 < len(STREETS) and not pre_face:
            nxt = STREETS[i + 1]
            flags = _replace_flag(argv, "--street", nxt)
            name = matching_report(flags, path) or f"this spot, on the {nxt}"
            add(name, flags, "the next street")
        if i > 0:
            prev = STREETS[i - 1]
            if not (prev == "preflop" and post_face):
                flags = _replace_flag(argv, "--street", prev)
                name = matching_report(flags, path) or f"this spot, on the {prev}"
                add(name, flags, "the previous street")

    # In and out of position only exist after the flop. Offering them on
    # a preflop filter produces the empty page `why_empty` already has a
    # sentence for, which is the opposite of navigation.
    postflop = (street in ("flop", "turn", "river")
                or "--ip" in argv or "--oop" in argv)
    if postflop:
        if "--ip" in argv:
            flags = _replace_flag([a for a in argv if a != "--ip"], "--oop")
            name = matching_report(flags, path) or "same spot, out of position"
            add(name, flags, "the other seat")
        elif "--oop" in argv:
            flags = _replace_flag([a for a in argv if a != "--oop"], "--ip")
            name = matching_report(flags, path) or "same spot, in position"
            add(name, flags, "the other seat")
        else:
            for flag, label in (("--ip", "in position"),
                                ("--oop", "out of position")):
                flags = _replace_flag(argv, flag)
                name = matching_report(flags, path) or f"same spot, {label}"
                add(name, flags, label)

    pots = flag_values(argv, "--pot")
    pot = pots[0] if len(pots) == 1 else None
    if pot == "raised":
        flags = _replace_flag(argv, "--pot", "3bet")
        name = matching_report(flags, path) or "this spot, in a 3-bet pot"
        add(name, flags, "a fatter pot")
    elif pot == "3bet":
        flags = _replace_flag(argv, "--pot", "raised")
        name = matching_report(flags, path) or "this spot, in a single-raised pot"
        add(name, flags, "a thinner pot")
        flags = _replace_flag(argv, "--pot", "4bet")
        name = matching_report(flags, path) or "this spot, in a 4-bet pot"
        add(name, flags, "a fatter pot")

    return out[:limit]

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


# The next action after this one, by the other seat (`--after`) or by
# the same player (`--then`). Names rather than letters so the window
# and the command line stay on the same words the action mix already
# uses. `raise` includes the all-in that is a raise; `call` includes
# the all-in that is a call -- `lines.letter` again, from the other
# direction.
AFTER = {
    "fold": "action = 'F'",
    "check": "action = 'X'",
    "call": "action IN ('C','A') AND agg = 0",
    "bet": "agg = 1 AND to_call = 0",
    "raise": "agg = 1 AND to_call > 0",
    "continue": "action <> 'F'",
    # Squeeze is not the first later action. After an open the first
    # later action is usually the call; the squeeze is the raise that
    # comes after that call. The predicate is the squeeze stat's
    # chance-and-action on a LATER row -- n_live>=4 and pot_bb>4 is
    # the caller-already-in proxy that stat already uses -- so
    # `--after squeeze` and `--quick squeeze` agree on what a squeeze
    # is. `--live` plus `--after raise` still works; this is that,
    # named.
    "squeeze": ("agg = 1 AND street = 'preflop' AND facing = 'open' "
                "AND n_live >= 4 AND pot_bb > 4"),
}
# Names a person types after an open. They are the same first-later
# action -- a 3-bet is a raise -- so they share SQL and stay one
# filter language. Squeeze is its own verb: any later squeeze, not
# the first later action, because that is where the data supports it.
AFTER_ALIAS = {
    "fold-out": "fold", "fold_out": "fold", "foldout": "fold",
    "3bet": "raise", "3-bet": "raise",
    "none": "none", "end": "none",
    "squeeze": "squeeze", "sqz": "squeeze", "squeezed": "squeeze",
}


def _later_other(pred):
    """Somebody else acted after this row, matching pred."""
    return (
        "EXISTS (SELECT 1 FROM decisions x "
        "WHERE x.hand_id = decisions.hand_id AND x.n > decisions.n "
        f"AND x.seat <> decisions.seat AND ({pred}))"
    )


def _later_any_sql(same_seat, pred, alias="decisions"):
    """A later action exists matching pred -- not necessarily the first."""
    cmp = "=" if same_seat else "<>"
    return (
        "EXISTS (SELECT 1 FROM decisions x "
        f"WHERE x.hand_id = {alias}.hand_id AND x.n > {alias}.n "
        f"AND x.seat {cmp} {alias}.seat AND ({pred}))"
    )


# What the pot did with this bet, not the first later action. Fold-out
# is every remaining player folding (or nobody acting). Call is a
# passive continue and no raise. Raise-back is any later aggression.
# The three partition aggressive rows: a leftover verb would shrink
# every percentage the way a missing action-mix verb does.
OUTCOMES = {
    "fold-out": (
        "agg = 1 AND NOT " + _later_other("x.action <> 'F'"),
        "All Villains Fold"),
    "call": (
        "agg = 1 AND " + _later_other("x.action <> 'F' AND x.agg = 0")
        + " AND NOT " + _later_other("x.agg = 1"),
        "One Villain Call"),
    "raise-back": (
        "agg = 1 AND " + _later_other("x.agg = 1"),
        "Villain Raise"),
}
OUTCOME_ALIAS = {
    "fold-out": "fold-out", "fold_out": "fold-out", "foldout": "fold-out",
    "fold": "fold-out",
    "call": "call", "called": "call",
    "raise-back": "raise-back", "raise_back": "raise-back",
    "raise": "raise-back", "reraise": "raise-back", "3bet": "raise-back",
}
OUTCOME_MIX = tuple(
    (key, OUTCOMES[key][1], OUTCOMES[key][0])
    for key in ("fold-out", "call", "raise-back"))


def size_sql(letter):
    """pot_frac bounds for one of lines.BUCKETS, matching lines.bucket."""
    prev = None
    for edge, name in lines.SIZES:
        if name == letter:
            if prev is None:
                return f"pot_frac IS NOT NULL AND pot_frac <= {edge}"
            return f"pot_frac > {prev} AND pot_frac <= {edge}"
        prev = edge
    if letter == lines.OVERBET:
        return f"pot_frac > {lines.SIZES[-1][0]}"
    raise ValueError(letter)


SIZE_ALIAS = {
    "s": "s", "small": "s",
    "m": "m", "medium": "m", "half": "m",
    "l": "l", "large": "l",
    "p": "p", "pot": "p", "pot+": "p",
    "o": "o", "overbet": "o", "over": "o",
}


def parse_size(token):
    """
    A size letter, or a pot-frac range the letters cannot say.

    `m` is still half-pot. `0.4-0.75` is the custom-builder slider.
    A trailing % is percent of pot; a number bigger than 3 is too
    (nobody means a 40-pot bet when they type 40-75).
    """
    raw = (token or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("empty size")
    if raw in SIZE_ALIAS:
        return ("letter", SIZE_ALIAS[raw])
    force_pct = "%" in raw
    body = raw.replace("%", "")

    def frac(s):
        x = float(s)
        if force_pct or x > 3:
            return x / 100.0
        return x

    try:
        if body.endswith("+") or body.startswith(">="):
            return ("range", frac(body.replace(">=", "").rstrip("+")), None)
        if body.startswith(">"):
            return ("range", frac(body[1:]), None)
        if body.startswith("<="):
            return ("range", None, frac(body[2:]))
        if body.startswith("<"):
            return ("range", None, frac(body[1:]))
        m = re.match(r"^([0-9]*\.?[0-9]+)-([0-9]*\.?[0-9]+)$", body)
        if m:
            return ("range", frac(m.group(1)), frac(m.group(2)))
        return ("range", frac(body), frac(body))
    except ValueError:
        raise ValueError(raw)


def size_range_sql(lo, hi):
    parts = ["pot_frac IS NOT NULL"]
    if lo is not None:
        parts.append(f"pot_frac >= {lo}")
    if hi is not None:
        parts.append(f"pot_frac <= {hi}")
    return " AND ".join(parts)


def parse_stack(token):
    """Stack in bb: `100+`, `<40`, `80-200`."""
    raw = (token or "").strip().lower().replace(" ", "").replace("bb", "")
    if not raw:
        raise ValueError("empty stack")

    def num(s):
        return float(s)

    try:
        if raw.endswith("+") or raw.startswith(">="):
            return (num(raw.replace(">=", "").rstrip("+")), None)
        if raw.startswith(">"):
            return (num(raw[1:]), None)
        if raw.startswith("<="):
            return (None, num(raw[2:]))
        if raw.startswith("<"):
            return (None, num(raw[1:]))
        m = re.match(r"^([0-9]*\.?[0-9]+)-([0-9]*\.?[0-9]+)$", raw)
        if m:
            return (num(m.group(1)), num(m.group(2)))
        return (num(raw), None)
    except ValueError:
        raise ValueError(raw)


def stack_sql(lo, hi):
    parts = ["eff_bb IS NOT NULL"]
    if lo is not None:
        parts.append(f"eff_bb >= {lo}")
    if hi is not None:
        parts.append(f"eff_bb < {hi}")
    return " AND ".join(parts)


def _next_sql(same_seat, pred):
    """The first later decision on this hand, by this seat or another."""
    cmp = "=" if same_seat else "<>"
    return (
        "EXISTS (SELECT 1 FROM decisions x "
        "WHERE x.hand_id = decisions.hand_id AND x.n > decisions.n "
        f"AND x.seat {cmp} decisions.seat AND ({pred}) "
        "AND x.n = (SELECT MIN(y.n) FROM decisions y "
        "WHERE y.hand_id = decisions.hand_id AND y.n > decisions.n "
        f"AND y.seat {cmp} decisions.seat))"
    )


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
    "cbet_river": "Missed 3rd Barrel River",
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
ACTION_FLAGS = ("--aggressive", "--allin", "--quick",
                "--size", "--outcome")


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


def clock_from_argv(argv):
    """`--start-of-day` / `--tz` on this command, else `sessions.json`."""
    start = tz = None
    i = 0
    argv = list(argv or [])
    while i < len(argv):
        if argv[i] == "--start-of-day" and i + 1 < len(argv):
            start = argv[i + 1]
            i += 2
        elif argv[i] == "--tz" and i + 1 < len(argv):
            tz = argv[i + 1]
            i += 2
        else:
            i += 1
    return sessions.load_clock(start_of_day=start, tz=tz)


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
        if a == "--today":
            # Not in SWITCHES: the SQL is the clock, and a machine
            # whose corpus is last month would fail "selects nothing"
            # for a filter that is working.
            clock = clock_from_argv(argv)
            parts.append("(" + clock.today_sql() + ")")
            described.append("today")
            i += 1
            continue
        if a in SWITCHES:
            parts.append(SWITCHES[a])
            described.append(a.lstrip("-"))
            i += 1
            continue
        if a in STUDY_SWITCHES:
            sql, word = STUDY_SWITCHES[a]
            parts.append(sql)
            described.append(word)
            i += 1
            continue
        if a in SKIP:
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
            if a == "--tag":
                names = [x.strip() for x in str(v).split(",") if x.strip()]
                if not names:
                    raise SystemExit("--tag needs a name")
                for name in names:
                    if not notes.valid_tag(name):
                        raise SystemExit(
                            f"invalid tag {name!r} -- letters, digits, "
                            "and _./+-")
                items = ", ".join(q(n) for n in names)
                parts.append(
                    "hand_id IN (SELECT hand_id FROM study.hand_tags "
                    f"WHERE tag IN ({items}))")
                described.append("tag " + ",".join(names))
                continue
            if a in ("--alias", "--vs-alias"):
                who = "player" if a == "--alias" else "vs_player"
                try:
                    parts.append(aliases.where_sql(v, who))
                except ValueError as e:
                    raise SystemExit(str(e))
                _members, single = aliases.members_of(v)
                kind = "alias" if single else "alias-group"
                described.append(f"{kind} {v}" if a == "--alias"
                                 else f"vs {kind} {v}")
                continue
            if a in ("--villain-type", "--vs-class"):
                kinds = [x.strip().lower() for x in str(v).split(",")
                         if x.strip()]
                allowed = ("reg", "fish", "unknown")
                if not kinds or any(k not in allowed for k in kinds):
                    raise SystemExit(
                        f"unknown villain type {v!r} -- reg, fish, unknown")
                items = ", ".join(q(k) for k in kinds)
                parts.append(f"vs_class IN ({items})")
                described.append("vs-class " + ",".join(kinds))
                continue
            if a == "--quick":
                known = quick_by_key()
                for name in v.split(","):
                    if name not in known:
                        raise SystemExit(f"unknown quick filter {name!r}")
                    parts.append("(" + known[name]["sql"] + ")")
                    described.append(known[name]["label"])
                continue
            if a in ("--after", "--then"):
                same = a == "--then"
                words = []
                for name in v.split(","):
                    key = AFTER_ALIAS.get(name, name)
                    if key == "none":
                        parts.append(_ended_sql(same))
                        words.append("none")
                        continue
                    if key not in AFTER:
                        raise SystemExit(
                            f"unknown next action {name!r} -- one of: "
                            f"{', '.join(list(AFTER) + ['none'])}")
                    if key == "squeeze":
                        # Any later squeeze, not the first later action.
                        # After an open the first later action is the
                        # call; `--after raise` would miss the squeeze.
                        parts.append(
                            _later_any_sql(same, AFTER["squeeze"]))
                    else:
                        parts.append(_next_sql(same, AFTER[key]))
                    words.append(key)
                described.append(
                    ("then " if same else "after ") + ", ".join(words))
                continue
            if a == "--size":
                labels = []
                for name in v.split(","):
                    try:
                        kind, *rest = parse_size(name)
                    except ValueError:
                        raise SystemExit(
                            f"unknown size {name!r} -- a letter "
                            f"({', '.join(lines.BUCKETS)}) or a pot-frac "
                            f"range (0.4-0.75, 50%+, <=0.33)")
                    if kind == "letter":
                        parts.append("(" + size_sql(rest[0]) + ")")
                        labels.append(rest[0])
                    else:
                        lo, hi = rest
                        parts.append("(" + size_range_sql(lo, hi) + ")")
                        labels.append(name.strip())
                described.append("size " + ",".join(labels))
                continue
            if a == "--stack":
                try:
                    lo, hi = parse_stack(v)
                except ValueError:
                    raise SystemExit(
                        f"unknown stack {v!r} -- 100+, <40, or 80-200")
                parts.append("(" + stack_sql(lo, hi) + ")")
                described.append("stack " + v)
                continue
            if a == "--outcome":
                keys = []
                for name in v.split(","):
                    key = OUTCOME_ALIAS.get(name.strip().lower())
                    if key is None:
                        raise SystemExit(
                            f"unknown outcome {name!r} -- one of: "
                            f"fold-out, call, raise-back")
                    parts.append("(" + OUTCOMES[key][0] + ")")
                    keys.append(key)
                described.append("outcome " + ", ".join(keys))
                continue
            if a == "--action":
                words = []
                for name in v.split(","):
                    key = ACTION_ALIAS.get(name.strip().lower())
                    if key is None:
                        raise SystemExit(
                            f"unknown action {name!r} -- one of: "
                            f"{', '.join(k for k, _ in ACTION_MIX)}")
                    parts.append("(" + dict(ACTION_MIX)[key] + ")")
                    words.append(key)
                described.append("action " + ", ".join(words))
                continue
            if a == "--result":
                words = []
                for name in v.split(","):
                    key = RESULT_ALIAS.get(name.strip().lower())
                    if key is None:
                        raise SystemExit(
                            f"unknown result {name!r} -- one of: "
                            f"{', '.join(RESULTS)}")
                    parts.append("(" + result_sql(key) + ")")
                    words.append(key)
                described.append("result " + ", ".join(words))
                continue
            if a == "--session":
                sid = str(v).strip()
                if not sid:
                    raise SystemExit("--session needs an id "
                                     "(the first hand of the sit-down)")
                parts.append("(" + sessions.session_sql(sid) + ")")
                described.append("session " + sid)
                continue
            if a == "--hours":
                clock = clock_from_argv(argv)
                try:
                    parts.append("(" + clock.hours_sql(v) + ")")
                except ValueError as e:
                    raise SystemExit(str(e))
                described.append(f"last {v} hours")
                continue
            if a == "--fmt":
                try:
                    sql, word = fmt_sql(v)
                except ValueError as e:
                    raise SystemExit(str(e))
                parts.append("(" + sql + ")")
                described.append(word)
                continue
            if a == "--last-sessions":
                try:
                    parts.append("(" + sessions.last_n_sql(v) + ")")
                except ValueError as e:
                    raise SystemExit(str(e))
                described.append(f"last {int(v)} sessions")
                continue
            if a == "--call-range":
                key = str(v).strip()
                if key not in BY_KEY:
                    raise SystemExit(f"unknown call-range stat {key!r}")
                parts.append("(" + call_range_sql(key) + ")")
                described.append("call range of " + BY_KEY[key].label)
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
            if "{list}" in tpl:
                raw = [x.strip() for x in v.split(",") if x.strip()]
                if a == "--combo":
                    # Axs / Kxo / 22+ are families, not combo names.
                    # Expanding here keeps `--combo Axs` the same
                    # filter the study pane click produces.
                    items = []
                    for token in raw:
                        items.extend(expand_combo(token))
                    raw = items
                items = ", ".join(q(x) for x in raw)
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
    joined = " ".join(sql for _l, sql in parts)
    if "session_hands" in joined:
        # A stale id, or ensure() never ran, looks like an empty
        # report rather than a sit-down that is not in this database.
        try:
            n_sess = con.execute(
                "SELECT COUNT(*) FROM session_hands").fetchone()[0]
        except sqlite3.Error:
            n_sess = 0
        if not n_sess:
            return ("no session cache -- sessions.ensure has not run, "
                    "or there are no hero cash hands")
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


# The H2N Statistics grid. A curated subset of the registry, not every
# sizing leftover -- the dump of all matching stats is still the `stats`
# tab. Showdown stats are spots-sourced and counted over the same
# (hand, seat) sample as the decision rows.
CURATED = (
    "vpip", "pfr", "rfi", "limp", "iso", "threebet", "coldcall", "squeeze",
    "fold_to_3bet", "fourbet", "fold_to_4bet", "steal", "fold_to_steal",
    "bb_defend",
    "cbet_flop", "fold_to_cbet", "raise_cbet", "donk_flop", "checkraise_flop",
    "cbet_turn", "delayed_cbet", "probe_turn", "fold_to_turn_bet",
    "cbet_river", "fold_to_river_bet",
    "wtsd", "wsd", "wwsf",
)

# H2N: a regular's decision with a fish still in is "played against a
# fish" and drops out of the Statistics sample. Fish-vs-reg stays --
# that is how a fish plays against regs. Unknown stays. Reports never
# see this predicate; it is AND-ed on only in `statistics_of`.
REG_VS_FISH = "(player_class = 'reg' AND n_fish > 0)"


def fmt_sql(mode):
    """Cash (not MTT) or a tournament, or a literal `fmt` value."""
    raw = str(mode or "").strip()
    if not raw:
        raise ValueError("--fmt needs cash, mtt, or a stored format")
    key = raw.lower()
    if key in ("cash", "ring"):
        return "fmt IS NOT NULL AND fmt <> 'MTT'", "cash"
    if key == "mtt":
        return "fmt = 'MTT'", "mtt"
    if raw in ("RING", "ZONE", "BLITZ", "MTT") or key in (
            "ring", "zone", "blitz", "mtt"):
        token = raw.upper() if raw.isalpha() else raw
        return f"fmt = {q(token)}", f"fmt {token}"
    raise ValueError(
        f"unknown fmt {mode!r} -- cash, mtt, RING, ZONE, BLITZ, MTT")


def with_exclude(where, on):
    """AND the Statistics-only reg-vs-fish drop onto a decisions WHERE."""
    if not on:
        return where
    return f"({where}) AND NOT ({REG_VS_FISH})"


def call_range_sql(stat):
    """
    Same chance as the stat, a call instead of the stat's action.

    3bet's chance is facing an open; Call Range is Call Open Raise.
    An all-in call is `action='A'` and `agg=0`; a raise is `agg=1`.
    """
    st = BY_KEY[stat] if isinstance(stat, str) else stat
    return f"({st.chance}) AND action IN ('C','A') AND agg = 0"


def action_range_sql(stat):
    st = BY_KEY[stat] if isinstance(stat, str) else stat
    return f"({st.chance}) AND ({st.action})"


def reports_argv(subject_argv, key, kind="action", combo=None):
    """
    Open-in-Reports flags for a Statistics click.

    Who, date, cash/MTT, last-N stay. The exclude flag does not --
    Reports ignore it, matching H2N. `--villain-type` is already a
    Reports filter and is not invented here.
    """
    out = list(who_only(subject_argv or []))
    if kind == "call":
        out += ["--call-range", key]
    else:
        out += ["--quick", key]
    if combo:
        out += ["--combo", combo]
    return out


def take_stat_drill(argv):
    """
    Pull click-stat flags off a Statistics command so they do not
    become the sample.

    `--hit` / `--quick` / `--call-range` / a cell `--combo` describe
    the drill, not the subject. Leaving them in made the grid the
    3bet hands and every rate read 100% or empty.
    """
    hit = kind = combo = None
    out = []
    i = 0
    argv = list(argv or [])
    while i < len(argv):
        a = argv[i]
        if a in ("--call-range", "--quick", "--hit", "--combo") and \
                i + 1 < len(argv):
            val = argv[i + 1]
            if a == "--call-range":
                hit, kind = val, "call"
            elif a in ("--quick", "--hit") and hit is None:
                hit, kind = val, "action"
            elif a == "--combo":
                combo = val
            i += 2
            continue
        out.append(a)
        i += 1
    return out, hit, kind or "action", combo


def spots_sample_sql(dec_where):
    """Spots rows whose (hand, seat) appear in the Statistics sample."""
    return (
        "(hand_id, seat) IN (SELECT DISTINCT hand_id, seat "
        f"FROM decisions WHERE {dec_where})"
    )


def class_counts(con, where):
    """How many identities of each class sit in this sample."""
    rows = con.execute(
        f"SELECT COALESCE(player_class, 'unknown'), "
        f"COUNT(DISTINCT site || char(0) || player), "
        f"COUNT(DISTINCT hand_id || char(0) || seat) "
        f"FROM decisions WHERE {where} GROUP BY 1"
    ).fetchall()
    out = {"reg": 0, "fish": 0, "unknown": 0,
           "reg_hands": 0, "fish_hands": 0, "unknown_hands": 0}
    for klass, n_pl, n_h in rows:
        key = klass if klass in ("reg", "fish") else "unknown"
        out[key] += n_pl
        out[key + "_hands"] += n_h
    out["players"] = out["reg"] + out["fish"] + out["unknown"]
    out["seats"] = out["reg_hands"] + out["fish_hands"] + out["unknown_hands"]
    return out


def statistics_of(con, where, argv=None, exclude=False):
    """
    The Statistics grid: curated rates for this subject, one sample.

    `exclude` is the H2N rebuild flag. It is not a `build()` switch.
    Passing `--exclude-reg-vs-fish` on a Reports command is a no-op
    because `build` skips it; this function is the only place that
    AND-s the predicate.
    """
    argv = list(argv or [])
    sample = with_exclude(where, exclude)
    n_dec = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {sample}").fetchone()[0]
    n_all = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    try:
        counted = stat_rates(con, sample)
    except sqlite3.OperationalError:
        # A fixture (and a half-built DB) may lack a column a leftover
        # stat names. One missing column must not blank the whole grid
        # -- that is how a 3bet rate vanished while VPIP still worked.
        counted = {}
        for key in CURATED:
            st = BY_KEY.get(key)
            if st is None or st.source != "d":
                continue
            try:
                n, k, _p, _lo, _hi = rate(con, st, sample)
                counted[key] = (n, k)
            except sqlite3.OperationalError:
                counted[key] = (0, 0)
    spots_where = spots_sample_sql(sample)
    rows = []
    for key in CURATED:
        st = BY_KEY.get(key)
        if st is None:
            continue
        if st.source == "s":
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "spots" not in tables:
                n = k = 0
                p = lo = hi = 0.0
            else:
                n, k, p, lo, hi = rate(con, st, spots_where)
        else:
            n, k = counted.get(key, (0, 0))
            if not n:
                p = lo = hi = 0.0
            else:
                p, lo, hi = wilson(k, n)
        rows.append({
            "key": st.key, "label": st.label, "group": st.group,
            "note": st.note, "source": st.source,
            "n": n, "k": k, "pct": 100 * p,
            "band": 100 * (hi - lo) / 2,
            "has_call": st.source == "d",
        })
    counts = class_counts(con, sample)
    return {
        "n": n_dec, "n_all": n_all, "excluded": max(0, n_all - n_dec),
        "exclude": bool(exclude),
        "rows": rows, "counts": counts,
        "label": None,
        "note": (
            "exclude_reg_vs_fish drops a regular's decision when a fish "
            "is still in (n_fish > 0). Fish-vs-reg and unknown stay. "
            "Reports and Sessions do not use this flag."
        ),
    }


def stat_range_of(con, where, key, kind="action", exclude=False,
                  combo=None, hand_limit=200):
    """
    Click-stat payload: the 13x13 of the hits (or the Call Range),
    plus compact hands. `kind` is `action` or `call`.
    """
    st = BY_KEY.get(key)
    if st is None:
        raise SystemExit(f"unknown stat {key!r}")
    sample = with_exclude(where, exclude)
    if kind == "call":
        pred = call_range_sql(st)
        title = "Call Range of " + st.label
    else:
        pred = action_range_sql(st)
        title = st.label
    drill = f"({sample}) AND ({pred})"
    if combo:
        items = ", ".join(q(c) for c in expand_combo(combo))
        drill = f"({drill}) AND combo IN ({items})"
        title = f"{title} · {combo}"
    chart = chart_of(con, drill)
    rng = range_of(con, drill)
    hands = matching_hands(con, drill, limit=hand_limit)
    notes.decorate(con, hands)
    return {
        "key": key, "kind": kind, "title": title,
        "combo": combo, "where": drill,
        "chart": chart, "range": rng, "hands": hands,
        "reports_argv": reports_argv([], key, kind, combo),
    }


def show_statistics(con, where, label, argv=None, exclude=False,
                    hit=None, kind="action", parts=(), combo=None):
    """The Statistics grid, printed. `--hit` drills one stat."""
    got = statistics_of(con, where, argv, exclude=exclude)
    print(f"\nStatistics  {label}")
    print("=" * (len(label) + 13))
    c = got["counts"]
    print(f"{got['n']:,} decisions"
          + (f"  (excluded {got['excluded']:,} reg-vs-fish of "
             f"{got['n_all']:,})" if exclude else "")
          + f"  ·  {c['players']:,} identities  "
          f"{c['reg']} regs / {c['fish']} fish / {c['unknown']} unknown")
    print()
    group = None
    for r in got["rows"]:
        if r["group"] != group:
            group = r["group"]
            print(f"  [{group}]")
        if not r["n"]:
            print(f"    {r['label']:22}    –")
            continue
        print(f"    {r['label']:22} {r['pct']:5.1f}%  n={r['n']:,}  "
              f"±{r['band']:.1f}")
    if exclude:
        print()
        print("  " + got["note"])
    if not hit:
        return got
    drill = stat_range_of(con, where, hit, kind=kind, exclude=exclude,
                          combo=combo)
    print(f"\n  {drill['title']}")
    show_chart(con, drill["where"], drill["title"])
    compact.attach(con, drill["hands"], fmt="text")
    print(f"  {len(drill['hands']):,} hands  (Open in Reports: "
          f"{' '.join(reports_argv(argv or [], hit, kind, combo))})")
    return got


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
    where_a, where_b = with_cohort(con, where_a), with_cohort(con, where_b)
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
            "strong": 100.0 * (seen - weak) / seen if seen else 0.0,
            "coverage": coverage_of(con, where)}


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
          f"({100 * out['n'] / out['total']:.0f}%)")
    _print_coverage(out.get("coverage") or coverage_of(con, where))
    print()
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
            "peak": max((n for n, _k in cells.values()), default=0),
            "coverage": coverage_of(con, where)}


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
    _print_coverage(g.get("coverage") or coverage_of(con, where))
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


def _print_related(related):
    """The spots you would open next, as flags you can paste."""
    if not related:
        return
    print("related spots:")
    for r in related:
        how = f"--preset {r['name']!r}" if r.get("preset") else " ".join(r["argv"])
        print(f"  {r['name']:28} {r['why']}")
        print(f"    {how}")
    print()


def _print_bet_sizes(blob):
    if not blob or not blob.get("rows"):
        return
    print("  [bet sizes]")
    print(f"  {'':22} {'freq':>7} {'hits/opps':>14} {'act bb':>9}  apply")
    for r in blob["rows"]:
        thin = " ?" if r["n"] < 30 else "  "
        prof = r.get("profit") or {}
        if prof.get("bb_per_hand") is not None:
            act = f"{prof['bb_per_hand']:+.2f}"
        else:
            act = "   –"
        print(f"  {r['label']:22} {r['pct']:6.1f}% "
              f"{'+/-%.0f' % r['band']:>6}{thin}"
              f"{r['hits']:>5}/{r['opps']:<6} {act:>8}  {r['how']}")
    print()


def _print_chain(title, chain):
    if not chain["rows"]:
        return
    print(f"  [{title}]")
    print(f"  {'':22} {'freq':>7} {'hits/opps':>14} {'act bb':>9}  apply")
    for r in chain["rows"]:
        thin = " ?" if r["n"] < 30 else "  "
        how = r.get("how") or (
            f"{chain['flag']} {r['key']}" if r.get("key")
            else "  (hand ended)")
        hits = r.get("hits", r["k"])
        opps = r.get("opps", r["n"])
        prof = r.get("profit") or {}
        if prof.get("bb_per_hand") is not None:
            act = f"{prof['bb_per_hand']:+.2f}"
        elif prof.get("n"):
            act = "   –"
        else:
            act = "   –"
        print(f"  {r['label']:22} {r['pct']:6.1f}% "
              f"{'+/-%.0f' % r['band']:>6}{thin}"
              f"{hits:>5}/{opps:<6} {act:>8}  {how}")
    print()


def show_chain_report(con, where, label, argv, same_seat=False):
    """The Faced Next or Next Actions table as its own report."""
    title = ("next actions  (this player)" if same_seat
             else "faced next  (the other seat)")
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    print(f"{n} decisions match")
    if not n:
        print("  " + why_empty(con, []))
        return
    blob = chain_report(con, where, argv, same_seat)
    _print_chain(title, blob)
    print("  apply a row with --after / --then / --branch, or click it.")
    print("  act bb is Action Profit v1 on the parent action given this")
    print("  continuation -- not Won$, not EV. A dash is unpriced.")
    print("  squeeze is any later squeeze (callers already in), not the")
    print("  first later action. After an open that is usually the call.")


def show_stats(con, where, label, parts=(), related=None, argv=None):
    """Every stat that has anything to say under this filter."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    n_dec, rows = stats_of(con, where)
    print(f"{n_dec} decisions match")
    if not n_dec:
        print("  " + why_empty(con, parts))
        if related:
            print()
            _print_related(related)
        return
    if argv is not None:
        summ = spot_summary(con, where, argv)
        thin = " ?" if summ["opps"] < 30 else "  "
        print(f"  hits {summ['hits']:,}  of  {summ['opps']:,} opportunities"
              f"  ({summ['label']} {summ['pct']:.1f}%"
              f" +/-{summ['band']:.0f}{thin})")
        print(f"  {summ['per_1k']:.1f} hits / 1000 hands"
              f"  (of {summ['hands']:,} player-hands)")
        prof = action_profit_of(con, where)
        _print_mean_profit("action profit", prof, "hits")
        callp = call_profit_of(con, where)
        _print_mean_profit("call profit  ", callp, "calls")
        if pack_wants_won(argv):
            won = amount_won_of(con, where)
            _print_amount_won(won)
        if cohort_loaded(con):
            _print_cohort_range(con, where)
        print()
    # What they did HERE, before the named stats. A cbet frequency is "of
    # the times they could"; this is "of the decisions you already asked
    # about", which is the number a popup report leads with.
    acts = actions_of(con, where)
    if acts["mix"]:
        print("\n  [this spot]")
        for r in acts["mix"]:
            thin = " ?" if r["n"] < 30 else "  "
            print(f"  {r['label']:22} {r['pct']:6.1f}% "
                  f"{'+/-%.0f' % r['band']:>7}{thin} n={r['n']:<6d}")
        for r in acts["extra"]:
            thin = " ?" if r["n"] < 30 else "  "
            print(f"  {r['label']:22} {r['pct']:6.1f}% "
                  f"{'+/-%.0f' % r['band']:>7}{thin} n={r['n']:<6d}")
        print()
    outs = outcomes_of(con, where)
    if outs["rows"]:
        print("  [outcome]")
        for r in outs["rows"]:
            thin = " ?" if r["n"] < 30 else "  "
            print(f"  {r['label']:22} {r['pct']:6.1f}% "
                  f"{'+/-%.0f' % r['band']:>7}{thin} n={r['k']:<6d}  "
                  f"--outcome {r['key']}")
        print()
    _print_chain("faced next  (the other seat)",
                 chain_report(con, where, argv, False))
    _print_chain("next actions  (this player)",
                 chain_report(con, where, argv, True))
    _print_bet_sizes(bet_sizes_of(con, where, argv))
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
    if related:
        print()
        _print_related(related)


def show_related(related, label):
    """Just the neighbouring spots -- the other half of opening a report."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    if not related:
        print("  no neighbouring spot from here -- open a report from "
              "--presets, or add a street / pot / position to this filter.")
        return
    _print_related(related)


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
    pots = {r["n"]: (r["pot_before"], r["to_call"], r["pot_bb"],
                    r["agg"], r["allin"])
            for r in con.execute(
                "SELECT n, pot_before, to_call, pot_bb, agg, allin "
                "FROM decisions WHERE hand_id=?", (hand_id,))}
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
            pot, to_call, pot_bb, agg, dec_allin = pots.get(
                a["n"], (None, None, None, None, None))
            who = by_seat.get(a["seat"], {})
            size = a["total"] if a["action"] in ("R", "A") and a["total"] \
                else a["amount"]
            lines.append({
                "seat": a["seat"], "position": a["position"],
                "name": who.get("label"), "is_hero": who.get("is_hero"),
                "verb": VERBS.get(a["action"], a["action"]),
                "action": a["action"], "amount": size,
                "allin": bool(a.get("allin") or dec_allin),
                "agg": agg,
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
    line = compact.CompactHandRenderer(
        d, fmt="ansi" if sys.stdout.isatty() else "text")
    if line:
        print(line)
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


def show_report(con, where, label, dim, columns, min_n=30, argv=None):
    """
    One row per value of the dimension, one column per stat.

    Cells below `min_n` chances are printed but marked, because dropping
    them would hide that the split ran out of data and leaving them unmarked
    would let a 100% on four hands be read as a tendency.

    `--by size` is the Bet Sizes pane, not VPIP-by-pot-frac: hits/opps,
    freq and Action Profit per letter. `--show` still adds stat columns
    after that table when somebody named them.
    """
    if dim == "size":
        show_bet_sizes(con, where, label, argv)
        if not (argv and "--show" in argv):
            return
    print(f"\nfilter: {label}")
    print(f"by {dim}")
    print("=" * (len(label) + 8))

    got = report_of(con, where, dim, columns, argv)
    grid, counts, keys, won_by = (
        got["grid"], got["counts"], got["keys"], got["won_by"])
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
    print("\n  decisions in this filter, per row:")
    for k in keys:
        n = counts.get(k, 0)
        print(f"    {str(k)[:width - 1]:<{width}} n={n}")
    print("\n  '?' marks a cell measured on fewer than "
          f"{min_n} chances -- ignore it.")
    if won_by:
        print("\n  Won$ / Won hand% of the hands in each row "
              "(spots-sourced via the filtered seats; MTT out; "
              "whole-hand, not this street):")
        for k in keys:
            w = won_by.get(k)
            if not w:
                print(f"    {str(k)[:width - 1]:<{width}} --")
                continue
            band = ""
            if w.get("lo") is not None:
                band = f"  [{w['lo']:+.1f}, {w['hi']:+.1f}]"
            wband = ""
            if w.get("won_lo") is not None:
                wband = f" [{w['won_lo']:.0f}, {w['won_hi']:.0f}]"
            print(f"    {str(k)[:width - 1]:<{width}}"
                  f"{w['bb100']:+8.1f} bb/100 ±{w['error']:.0f}{band}"
                  f"  won {w['won_pct']:.0f}%{wband}  n={w['hands']}")


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
    expr_text = None
    rest = []
    for item in conditions:
        if item[0] == "_expr":
            expr_text = item[1]
        else:
            rest.append(item)
    rows = players.cohort(con, rest, site, klass, durable)
    if expr_text:
        rows = expr.filter_players(con, rows, expr_text)
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _cohort "
                "(site TEXT, player TEXT)")
    con.execute("DELETE FROM _cohort")
    con.executemany("INSERT INTO _cohort VALUES (?, ?)",
                    [(row[0], row[1]) for row in rows])
    con.execute("CREATE INDEX IF NOT EXISTS _cohort_ix "
                "ON _cohort(site, player)")
    return players.cohort_summary(rows)


# The join every report uses once a cohort is parked. Pin, compare,
# Hits/Opps and versus all call `build` again, which cannot see the
# temp table, so they AND this in through `with_cohort` rather than
# smuggling a `--where` that `who_only` would drop.
COHORT_PRED = (
    "EXISTS (SELECT 1 FROM _cohort c "
    "WHERE c.site = decisions.site AND c.player = decisions.player)"
)


def cohort_loaded(con):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_temp_master "
        "WHERE type='table' AND name='_cohort'").fetchone())


def with_cohort(con, where):
    """AND the parked cohort into a WHERE rebuilt from argv."""
    if con is None or not cohort_loaded(con) or COHORT_PRED in where:
        return where
    return f"({where}) AND {COHORT_PRED}"


def apply_cohort(con, spec, where, label=None):
    """
    Park the players, narrow `where`, and return the header a report prints.

    One function so the window, the page and the command line cannot
    drift on what "this cohort" means -- the failure mode last time was
    pin/compare returning before the EXISTS was applied, so a pooled
    report of fish was the whole database wearing that heading.
    """
    if spec is None:
        return where, None, label
    summary = select_cohort(con, spec)
    header = {
        "describe": players.describe_cohort(spec),
        "players": summary["players"],
        "hands": summary["hands"],
        "vpip": summary.get("vpip"),
        "pfr": summary.get("pfr"),
        "bb100": summary.get("bb100"),
    }
    where = with_cohort(con, where)
    if label is not None:
        n_pl = header["players"]
        who = "player" if n_pl == 1 else "players"
        label = (f"{label}, cohort: {header['describe']} "
                 f"({n_pl} {who}, {header['hands']:,} hands)")
    return where, header, label


def show_cohort_banner(header):
    """#players and #hands above the report numbers, not only in the label."""
    if not header:
        return
    print(f"COHORT  {header['describe']}")
    print(f"  {header['players']:,} players · {header['hands']:,} hands")


def results_of(con, pairs):
    """The money over a set of (hand, seat) pairs, tournaments excluded."""
    select_into(con, pairs)
    n, net_bb, money, saw, wtsd, wwsf = con.execute(
        "SELECT COUNT(*), SUM(s.net_bb), SUM(s.won - s.put_in), "
        "       SUM(s.saw_flop), SUM(s.wtsd), SUM(s.wwsf) "
        "FROM spots s JOIN _sel ON _sel.hand_id = s.hand_id "
        "AND _sel.seat = s.seat WHERE s.fmt <> 'MTT'").fetchone()
    if not n:
        return None
    return {"hands": n, "net_bb": net_bb or 0.0, "money": money or 0.0,
            "saw_flop": saw or 0, "wtsd": wtsd or 0, "wwsf": wwsf or 0,
            "bb100": 100 * (net_bb or 0.0) / n,
            "error": 1170 / n ** 0.5}


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
    # clothing: one hand's result has a standard deviation around 11.7bb, so
    # the error on bb/100 is 1170/sqrt(n) and it is usually larger than
    # anything being compared.
    print(f"  error on that    {1170 / max(1, n) ** 0.5:8.0f} bb/100"
          f"   <- and this is why")
    if saw:
        print(f"  saw a flop       {saw:8d}   ({100 * saw / n:.1f}%)")
        print(f"  won at showdown  {wtsd or 0:8d}")
        print(f"  won after flop   {100 * (wwsf or 0) / saw:8.1f}%")


def amount_won_of(con, where):
    """
    Won$ and Won hand% of the hands this decision filter selected.

    Money is a property of a hand. The filter picks decisions; this
    collapses to (hand, seat) and reads `spots.net_bb`. That is why a
    street filter does not blank -- we never ask a spots-sourced Stat
    whose chance cannot see `street=`. MTT is out (chips are not
    dollars). The whole pot is credited, not the street.

    Won hand% is a rate, so it gets a Wilson interval. Won$ is a mean
    of net_bb: t interval when n>=2, plus the same 1170/sqrt(n) error
    `--results` prints on bb/100, so the two views cannot disagree on
    what "noise" looks like.
    """
    empty = {
        "hands": 0, "net_bb": 0.0, "bb_per_hand": None, "bb100": None,
        "error": None, "lo": None, "hi": None,
        "won_hands": 0, "won_pct": None, "won_lo": None, "won_hi": None,
        "source": "spots, via the filtered (hand, seat) pairs",
        "note": ("whole-hand net_bb, not this street. MTT excluded. "
                 "A spots-sourced Stat would blank here; this join does not."),
    }
    pairs = matching_seats(con, where)
    if not pairs:
        return empty
    select_into(con, pairs)
    row = con.execute(
        "SELECT COUNT(*), SUM(s.net_bb), SUM(s.net_bb * s.net_bb), "
        "       SUM(CASE WHEN s.net_bb > 0 THEN 1 ELSE 0 END) "
        "FROM spots s JOIN _sel ON _sel.hand_id = s.hand_id "
        "AND _sel.seat = s.seat "
        "WHERE s.fmt <> 'MTT' AND s.net_bb IS NOT NULL"
    ).fetchone()
    n, total, sumsq, won = row[0] or 0, row[1], row[2], row[3] or 0
    if not n:
        return empty
    _mean, lo, hi = mean_interval(total, sumsq, n)
    p, wlo, whi = wilson(won, n)
    return {
        "hands": n,
        "net_bb": total or 0.0,
        "bb_per_hand": (total or 0.0) / n,
        "bb100": 100 * (total or 0.0) / n,
        "error": 1170 / n ** 0.5,
        "lo": lo, "hi": hi,
        "won_hands": won,
        "won_pct": 100 * p,
        "won_lo": None if wlo is None else 100 * wlo,
        "won_hi": None if whi is None else 100 * whi,
        "source": "spots, via the filtered (hand, seat) pairs",
        "note": ("whole-hand net_bb, not this street. MTT excluded. "
                 "A spots-sourced Stat would blank here; this join does not."),
    }


def amount_won_by(con, where, expr):
    """Won$ / Won hand% per value of a `--by` expression."""
    rows = con.execute(
        f"""
        SELECT g, COUNT(*), SUM(net_bb), SUM(net_bb * net_bb),
               SUM(won_hand)
        FROM (
          SELECT ({expr}) g, d.hand_id, d.seat,
                 MAX(s.net_bb) AS net_bb,
                 MAX(CASE WHEN s.net_bb > 0 THEN 1 ELSE 0 END) AS won_hand
          FROM decisions d
          JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat
          WHERE ({where}) AND ({expr}) IS NOT NULL
            AND s.fmt <> 'MTT' AND s.net_bb IS NOT NULL
          GROUP BY d.hand_id, d.seat
        )
        GROUP BY g
        """
    )
    out = {}
    for g, n, total, sumsq, won in rows:
        n, won = n or 0, won or 0
        if not n:
            continue
        _mean, lo, hi = mean_interval(total, sumsq, n)
        p, wlo, whi = wilson(won, n)
        out[g] = {
            "hands": n,
            "net_bb": total or 0.0,
            "bb_per_hand": (total or 0.0) / n,
            "bb100": 100 * (total or 0.0) / n,
            "error": 1170 / n ** 0.5,
            "lo": lo, "hi": hi,
            "won_hands": won,
            "won_pct": 100 * p,
            "won_lo": None if wlo is None else 100 * wlo,
            "won_hi": None if whi is None else 100 * whi,
        }
    return out


def coverage_of(con, where):
    """
    Hole-card coverage of the player-hands this filter selected, by site.

    Ignition writes every hand including folds; ACR writes the ones that
    reached showdown. A cohort mixed across both is a mixture of a full
    range and a showdown-selected one, and presenting that mixture as
    "the range" is the H2N showdown-bias failure with a new heading.
    """
    rows = list(con.execute(
        f"""
        SELECT site, COUNT(*), SUM(combo IS NOT NULL)
        FROM (SELECT DISTINCT hand_id, seat, site, combo
              FROM decisions WHERE {where})
        GROUP BY site
        """
    ))
    sites_out = []
    seen = total = 0
    revealing = set(sites.revealing())
    for site, tot, got in rows:
        tot, got = tot or 0, got or 0
        seen += got
        total += tot
        reveals = bool(site and site in revealing)
        sites_out.append({
            "site": site, "total": tot, "seen": got,
            "pct": 100.0 * got / tot if tot else 0.0,
            "reveals": reveals,
            "note": ("every hand, folds included" if reveals else
                     "shown hands only -- a stronger slice than the "
                     "range that arrived"),
        })
    return {
        "seen": seen, "total": total,
        "pct": 100.0 * seen / total if total else 0.0,
        "sites": sites_out,
        "note": ("This is the range that was SEEN. Sites that reveal "
                 "(Ignition) show every hand including folds; the "
                 "others show a showdown-selected slice. A mixed "
                 "cohort is a mixture of the two."),
    }


def _print_coverage(cov):
    """The seen-fraction, by site, so a mixed cohort cannot hide ACR bias."""
    if not cov["total"]:
        return
    print(f"  hole cards shown  {cov['seen']:,} of {cov['total']:,} "
          f"player-hands ({cov['pct']:.0f}%)")
    for s in cov["sites"]:
        name = s["site"] or "?"
        print(f"    {name:12} {s['seen']:,}/{s['total']:,} "
              f"({s['pct']:.0f}%)  -- {s['note']}")
    print(f"  {cov['note']}")


def _print_mean_profit(name, prof, kind):
    """Action / Call Profit with n and a t interval, or n and why not."""
    if not prof or not prof["n"]:
        return
    if prof["bb_per_hand"] is not None:
        band = ""
        if prof.get("lo") is not None:
            band = f"  [{prof['lo']:+.1f}, {prof['hi']:+.1f}]"
        print(f"  {name}  {prof['bb_per_hand']:+.2f} bb/hand{band}"
              f"  n={prof['priced']:,} priced of {prof['n']:,} hits")
        print(f"  {prof['note']}")
        print(f"  {prof['interval_note']}")
    else:
        print(f"  {name}  unpriced on {prof['n']:,} hits"
              f"  ({prof['note']})")
    for edge in prof.get("edges") or []:
        print(f"    unpriced: {edge}")


def _print_amount_won(won):
    """Won$ / Won hand% on a c-bet pack -- spots-sourced, not blank."""
    if not won or not won.get("hands"):
        return
    band = ""
    if won.get("lo") is not None:
        band = f"  [{won['lo']:+.1f}, {won['hi']:+.1f}] bb/hand"
    print(f"  Won$           {won['bb_per_hand']:+.2f} bb/hand{band}"
          f"  n={won['hands']:,} cash hands")
    print(f"                 {won['bb100']:+.1f} bb/100  "
          f"±{won['error']:.0f}  (1170/√n, same as --results)")
    wband = ""
    if won.get("won_lo") is not None:
        wband = f"  [{won['won_lo']:.0f}, {won['won_hi']:.0f}]"
    print(f"  Won hand%      {won['won_pct']:.1f}%{wband}"
          f"  {won['won_hands']:,} of {won['hands']:,}")
    print(f"  {won['note']}")


def _print_cohort_range(con, where):
    """Preflop range of the parked Multi-Player cohort, with coverage."""
    print("\n  [preflop range -- this cohort]")
    cov = coverage_of(con, where)
    _print_coverage(cov)
    g = chart_of(con, where)
    if not g["seen"]:
        print("  no hole cards in this cohort to draw a range from")
        return
    top = sorted(g["cells"].items(), key=lambda kv: -kv[1][0])[:8]
    print("  most of it: " + ", ".join(
        f"{c} {100.0 * n / g['seen']:.1f}%" for c, (n, _k) in top))
    print("  --chart for the 13x13; --range for what the hands became")


def show_hands(con, where, label, limit=40, parts=()):
    """The hands themselves, most recent first."""
    print(f"\nfilter: {label}")
    print("=" * (len(label) + 8))
    # The filter names bare columns, and `spots` shares several of them
    # with `decisions` -- is_hero, position, combo -- so it is applied inside
    # a subquery where there is only one table for a name to mean.
    rows = matching_hands(con, where)
    print(f"{len(rows)} hands match; showing up to {limit}\n")
    if not rows:
        print("  " + why_empty(con, parts))
        return
    print(f"    {'when':17} {'site':10} {'bb':>5} {'pos':4} {'hand':5} "
          f"{'net bb':>7} {'act bb':>7} {'call bb':>8}  board")
    print("    " + "-" * 92)
    shown = rows[:limit]
    notes.attach(con)
    notes.decorate(con, shown)
    compact.attach(con, shown,
                   fmt="ansi" if sys.stdout.isatty() else "text")
    for r in shown:
        when = (r["when"] or "")[:16]
        act = (f"{r['act']:+.1f}" if r["act"] is not None else "   –")
        call = (f"{r['call']:+.1f}" if r.get("call") is not None else "    –")
        net = r["net"] if r["net"] is not None else 0
        star = "*" if r.get("marked") else " "
        extra = ",".join(r.get("tags") or [])
        if r.get("n_notes"):
            extra = (extra + " " if extra else "") + f"n{r['n_notes']}"
        print(f"  {star} {when:17} {r['site'] or '':10} {r['bb'] or 0:5.2f} "
              f"{r['pos'] or '?':4} {r['combo'] or '--':5} "
              f"{net:7.1f} {act:>7} {call:>8}  {r['board'] or ''}"
              + (f"  {extra}" if extra else ""))
        if r.get("compact"):
            print(f"    {r['compact']}")
    print()
    print("  act bb is Action Profit v1; call bb is Call Profit Rate "
          "(actual calls, raise also legal); net bb is the whole hand. "
          "A dash is unpriced -- see --stats.")
    print("  The second line is the compact hand: X check, B/C/R + bb, "
          "' all-in. Marked actions are this row's seat.")


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
    print("    --cohort [EXPR]  select players before filtering situations")
    print("                     compact: vpip>=40,pfr<=10,hands>=100")
    print("                     expression: Value(3Bet)<2 and Opps(3Bet)>100")
    print("                     --chart / --range: the pooled preflop range,")
    print("                     with hole-card coverage by site")
    print("    --alias NAME      merge a single-person alias as the player")
    print("    --vs-alias NAME   the other seat is in that alias (group or one)")
    print("    --villain-type T  vs_class IN (reg|fish|unknown); also --vs-class")
    print("    --cohort-hands N   players with at least N hands (bare N is >=)")
    print("    --cohort-vpip  V   player VPIP (40+ means >=40)")
    print("    --cohort-pfr   V   player PFR (<=10, 18, 10+)")
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
          f"(default: the ones this situation is about, else "
          f"{','.join(DEFAULT_COLUMNS)})")
    print(f"    {'--min':14} mark cells below this many chances (default 30)")
    print("\n  study (notes.db -- survives a rebuild of hands.db):")
    print("    --marked       only starred hands")
    print("    --noted        only hands bound to a note")
    print("    --tag NAME     hands with that tag (comma-separate)")
    print("    --mark         star --hand ID  (optional --tag)")
    print("    --unmark       drop the star, or --tag off that hand")
    print("    --note TEXT    add a note on --hand ID")
    print("    notes.py is the list / template / catalog CLI")
    print(f"    {'--hand':14} replay one hand by id, ignoring every filter")
    print(f"    {'--chart':14} the 13x13 chart: what the range holds, or "
          f"one stat per combo with --show")
    print(f"    {'--related':14} neighbouring spots from this filter "
          f"(also printed under --stats)")
    print(f"    {'--after':14} first later action by another seat: "
          f"{', '.join(list(AFTER) + ['none'])} "
          f"(fold-out/3bet alias fold/raise; squeeze is any later "
          f"squeeze, not the first later action)")
    print(f"    {'--then':14} first later action by this player: "
          f"{', '.join(list(AFTER) + ['none'])} "
          f"(squeeze is any later squeeze by this seat)")
    print(f"    {'--faced-next':14} Faced Next report (freq, hits/opps, "
          f"action profit) from this filter")
    print(f"    {'--next-actions':14} Next Actions report, same columns")
    print(f"    {'--bet-sizes':14} Bet Sizes pane: hits/opps, freq, "
          f"Action Profit by pot-frac bucket (same as --by size)")
    print(f"    {'--from':14} alias for --filter, for --faced-next / "
          f"--next-actions")
    print(f"    {'--branch':14} apply a Faced Next / Next Actions row "
          f"(--after or --then)")
    print(f"    {'--hit':14} alias for --quick: narrow to hands that "
          f"hit that stat")
    print(f"    {'--size':14} pot fraction: "
          f"{', '.join(lines.BUCKETS)} or a range (0.4-0.75, 50%+)")
    print(f"    {'--stack':14} effective stack in bb: 100+, <40, 80-200")
    print(f"    {'--action':14} this decision: fold, check, call, bet, raise")
    print(f"    {'--result':14} whole-hand: won, lost, even, showdown, "
          f"no-showdown")
    print(f"    {'--fmt':14} cash (not MTT) or mtt -- Statistics mode")
    print(f"    {'--last-sessions':14} last N hero cash sit-downs")
    print(f"    {'--call-range':14} same chance as a stat, a call "
          f"(3bet → Call Open Raise)")
    print(f"    {'--statistics':14} curated Statistics grid (H2N tab)")
    print(f"    {'--exclude-reg-vs-fish':14} Statistics sample only -- "
          f"Reports / Sessions ignore it")
    print(f"    {'--session':14} one sit-down (id = first hand); "
          f"see sessions.py")
    print(f"    {'--today':14} local Today via start-of-day hour "
          f"and per-room HH timezone offset")
    print(f"    {'--hours':14} last N hours, same clock")
    print(f"    {'--start-of-day':14} hour the day begins (0-23), "
          f"for --today / --hours")
    print(f"    {'--tz':14} site=hours offset "
          f"(acr=-5,ignition=0)")
    print(f"    {'--combo':14} AKs, or a family: Axs, Kxo, 22+, pairs, "
          f"broadways")
    print(f"    {'--outcome':14} what the pot did with this bet: "
          f"fold-out, call, raise-back")
    print(f"    {'--first-in':14} first to put chips in on this street")
    print(f"    {'--first-raise':14} first raise on this street "
          f"(an open, or the first raise of a bet)")
    print(f"    {'--last-raise':14} this player already raised on the street")
    print(f"    {'--last-action':14} this decision ended the street")
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
    print(f"    {'--filter':14} the same, a --quick key, JSON argv, "
          f"or a FilterDef object")
    print(f"    {'--pin':14} freeze a named report, same person: "
          f"compact Hits/Opps / freq / Action Profit, plus two "
          f"--by grids, two Bet Sizes tables, or two stat packs")
    print(f"    {'--compare':14} two named reports, same columns "
          f"(--compare \"Flop c-bets\" \"Flop vs c-bet\")")
    print(f"    {'--versus':14} Holm table of every stat between "
          f"two populations (raw flags)")
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
    "--after fold":
        "Faced Next is an EXISTS over the next row of the same hand. "
        "The seek is on (hand_id, n), which is the primary key, but the "
        "outer query has no prefix of its own -- it is 'any decision "
        "whose next opponent folded', and that is most of the table",
    "--then bet":
        "Next Actions is the same shape for the same seat: a correlated "
        "look at n+1, not a column we can index without writing a second "
        "copy of every decision",
    "--after none":
        "the hand ended after this decision: NOT EXISTS on n+1, same "
        "shape as --after fold",
    "--after squeeze":
        "a later squeeze is EXISTS over a later row of the same hand, "
        "the same shape as --after fold, and not the first later action",
    "--outcome fold-out":
        "the pot's answer after this bet is an EXISTS over later rows "
        "of the same hand, the same shape as --after and for the same "
        "reason: there is no column for 'everybody folded after'",
    "--first-in":
        "first-to-act on a street is a large slice of the table, and "
        "first_in is not in dec_flags -- adding it was not measured",
    "--last-raise":
        "was_agg is the same shape as first_in: half the interesting "
        "rows, and not a prefix of an index we have",
    "--first-raise":
        "street_agg plus to_call is not a prefix of an index we have, "
        "and the first raise on a street is still a large slice",
    "--last-action":
        "last on the street is NOT EXISTS on a later n of the same "
        "hand -- there is no column for it, and adding one was not "
        "measured",
    "--result won":
        "a whole-hand result is EXISTS onto spots.net_bb / wtsd. "
        "Money is a property of a hand, so there is no prefix on "
        "decisions that can seek it",
    "--session":
        "a session is an IN-list of hand ids from session_hands, "
        "not a prefix of decisions.played_at -- the sit-down is "
        "clustered, not a date range",
}


def check_shape():
    """
    Filters that do not need a corpus: they build, they name, they pack.

    The live check below needs `hands.db`. This one does not, and is
    what a machine without a database can still fail -- a typo in
    `--outcome` or a StatPack that leads with VPIP.
    """
    fails = []
    for argv, needle in (
            (["--first-in"], "first_in = 1"),
            (["--last-raise"], "was_agg = 1"),
            (["--first-raise"], "street_agg"),
            (["--last-action"], "NOT EXISTS"),
            (["--size", "m"], "pot_frac > 0.4"),
            (["--size", "0.4-0.75"], "pot_frac >= 0.4"),
            (["--stack", "100+"], "eff_bb >= 100"),
            (["--outcome", "fold-out"], "agg = 1"),
            (["--after", "none"], "NOT EXISTS"),
            (["--after", "fold-out"], "action = 'F'"),
            (["--after", "3bet"], "agg = 1"),
            (["--after", "squeeze"], "n_live >= 4"),
            (["--after", "raise"], "MIN(y.n)"),
            (["--players", "6"], "n_players = 6"),
            (["--live", "2"], "n_live = 2"),
            (["--marked"], "study.hand_marks"),
            (["--noted"], "study.note_hands"),
            (["--tag", "leak"], "study.hand_tags"),
            (["--villain-type", "fish"], "vs_class IN ('fish')"),
            (["--vs-class", "reg,unknown"],
             "vs_class IN ('reg', 'unknown')"),
            (["--action", "call"], "action IN ('C','A') AND agg = 0"),
            (["--result", "won"], "s.net_bb > 0"),
            (["--combo", "Axs"], "A2s"),
            (["--session", "h1"], "session_hands"),
            (["--hours", "4"], "played_at"),
            (["--today"], "played_at")):
        where, _label, _p = build(argv)
        if needle not in where.replace("0.40", "0.4"):
            fails.append(f"{argv} built {where!r}, expected {needle!r}")
    sq_where, _, _ = build(["--after", "squeeze"])
    if "MIN(y.n)" in sq_where:
        fails.append("--after squeeze used first-later MIN -- it is any later")
    if resolve_filter("Flop c-bets") != list(SMART_REPORTS["Flop c-bets"]):
        fails.append("--filter did not open a Smart Report")
    if resolve_filter("cbet_flop") != ["--quick", "cbet_flop"]:
        fails.append("--filter did not open a quick key")
    if resolve_filter('["--ip","--pot","3bet"]') != ["--ip", "--pot", "3bet"]:
        fails.append("--filter JSON argv did not expand")
    if columns_for(["--quick", "raise_cbet"])[0] != "raise_cbet":
        fails.append("raise-cbet pack does not lead with raise_cbet")
    raise_pack = columns_for(["--quick", "raise_cbet"])
    if "cbet_turn" not in raise_pack:
        fails.append("raise-cbet pack dropped 2nd barrel")
    if "cbet_river" not in raise_pack:
        fails.append("raise-cbet pack dropped 3rd barrel")
    if "cbet_river" not in columns_for(["--quick", "cbet_flop"]):
        fails.append("cbet pack dropped 3rd barrel")
    if "cbet_river_not" not in quick_by_key():
        fails.append("Missed 3rd Barrel is not a quick filter")
    if resolve_filter("Missed 2nd Barrel") != ["--quick", "cbet_turn_not"]:
        fails.append("--filter did not open Missed 2nd Barrel")
    if resolve_filter("3rd Barrel") != ["--quick", "cbet_river"]:
        fails.append("--filter did not open 3rd Barrel")
    if "vpip" in columns_for(["--quick", "cbet_flop"]):
        fails.append("cbet pack still leads with VPIP")
    axs = expand_combo("Axs")
    if "AKs" not in axs or "A2s" not in axs or "AKo" in axs:
        fails.append(f"Axs expanded to {axs}")
    if expand_combo("AKs") != ["AKs"]:
        fails.append("AKs was treated as a family")
    if "22" not in expand_combo("22+") or "AA" not in expand_combo("22+"):
        fails.append("22+ did not cover the pairs")
    if "AKs" not in expand_combo("broadways"):
        fails.append("broadways dropped AKs")
    child = drill_child([], {"flag": "--stack", "value": "80-120"})
    if child != ["--stack", "80-120"]:
        fails.append(f"drill onto empty was {child}")
    nested = drill_child(child, {"argv": ["--street", "preflop",
                                          "--facing", "open",
                                          "--action", "call"]})
    if "--stack" not in nested or nested[nested.index("--action") + 1] != "call":
        fails.append(f"Call vs OR did not AND onto stack: {nested}")
    replaced = drill_child(["--stack", "80-120"],
                           {"flag": "--stack", "value": "200+"})
    if replaced != ["--stack", "200+"]:
        fails.append(f"second stack AND-ed rather than replaced: {replaced}")
    deep = drill_stack([], [{"flag": "--stack", "value": "80-120"},
                            {"flag": "--action", "value": "call"},
                            {"flag": "--combo", "value": "Axs"},
                            {"flag": "--pos", "value": "BTN"}])
    if "--pos" in deep:
        fails.append("drill_stack did not cap at 3")
    if "vpip" in columns_for(["--street", "flop"]) or \
            "pfr" in columns_for(["--street", "flop"]):
        fails.append("postflop study pack still shows VPIP/PFR")
    axs_where, axs_label, _ = build(["--combo", "Axs"])
    if "Axs" in axs_where:
        fails.append("--combo Axs was stored as a name, not expanded")
    if "combo Axs" not in axs_label:
        fails.append(f"--combo Axs labelled {axs_label!r}")
    if not pack_wants_won(["--quick", "raise_cbet"]):
        fails.append("raise-cbet pack should carry Won$ as a join extra")
    if not pack_wants_won(["--quick", "cbet_flop"]):
        fails.append("cbet pack should carry Won$ as a join extra")
    if pack_wants_won(["--quick", "threebet"]):
        fails.append("3-bet pack is not a Won$ street pack")
    if pack_wants_won(["--street", "flop"]):
        fails.append("a bare street filter is not a Won$ pack")
    if "wwsf" in columns_for(["--quick", "raise_cbet"]) or \
            "wsd" in columns_for(["--quick", "raise_cbet"]):
        fails.append("Won$ leaked into the Stat columns -- that blanks")
    m, lo, hi = mean_interval(5.0, 125.0, 2)
    if m is None or abs(m - 2.5) > 1e-9:
        fails.append(f"mean_interval(10, -5) was {m}, not 2.5")
    if lo is None or hi is None or hi - lo < 50:
        fails.append("n=2 must produce a wide t interval, not fake precision")
    if mean_interval(10.0, 100.0, 1)[1] is not None:
        fails.append("n=1 invented an interval -- one row has no spread")
    if mean_interval(0, 0, 0)[0] is not None:
        fails.append("empty mean_interval was not empty")
    if size_sql("s") != "pot_frac IS NOT NULL AND pot_frac <= 0.4" and \
            "0.40" not in size_sql("s"):
        fails.append(f"size s is {size_sql('s')!r}")
    if lines.bucket(0.55) != "m":
        fails.append("size m does not match lines.bucket")
    if "size" not in DIMENSIONS:
        fails.append("--by size is not a dimension")
    if DIMENSIONS["size"][0] != size_expr():
        fails.append("--by size CASE drifted from size_expr")
    for letter in lines.BUCKETS:
        if letter not in SIZE_NAMES:
            fails.append(f"SIZE_NAMES dropped {letter}")
        if letter not in size_expr():
            fails.append(f"size_expr dropped letter {letter}")
    # The CASE and lines.bucket must agree on every edge, including
    # the ones that sit exactly on a boundary -- a half-pot bet is
    # medium, and a 40% bet is small, because bucket is `<= edge`.
    size_con = sqlite3.connect(":memory:")
    size_con.execute("CREATE TABLE decisions (pot_frac REAL)")
    probes = (0.0, 0.4, 0.4000001, 0.55, 0.6, 0.6000001, 0.9, 1.0,
              1.2, 1.2000001, 2.0)
    size_con.executemany("INSERT INTO decisions VALUES (?)",
                         [(x,) for x in probes] + [(None,)])
    for frac, letter in size_con.execute(
            f"SELECT pot_frac, {size_expr()} FROM decisions"):
        want = lines.bucket(frac) or None
        # lines.bucket(None) is "" ; the CASE is NULL. Same fact.
        got = letter or None
        want = want or None
        if got != want:
            fails.append(f"size_expr({frac}) is {got!r}, "
                         f"lines.bucket is {want!r}")
        if frac is not None and want:
            n = size_con.execute(
                f"SELECT COUNT(*) FROM decisions WHERE pot_frac = ? "
                f"AND ({size_sql(want)})", (frac,)).fetchone()[0]
            if n != 1:
                fails.append(f"size_sql({want!r}) missed pot_frac={frac}")
    size_con.close()
    # The three outcomes partition an aggressive row: a bet that is
    # folded to, called, or raised. Building each must stay exclusive
    # enough that AND-ing two of them is empty SQL, not a crash.
    for a, b in (("fold-out", "call"), ("fold-out", "raise-back"),
                 ("call", "raise-back")):
        where, _, _ = build(["--outcome", a, "--outcome", b])
        if "agg = 1" not in where:
            fails.append(f"--outcome {a}+{b} dropped agg")
    if resolve_filter('{"street":"flop","first_raise":true,"size":"m"}') \
            != ["--street", "flop", "--size", "m", "--first-raise"]:
        fails.append("FilterDef JSON did not become argv")
    print(f"filter shape (no database)    "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_filterdef():
    """
    The custom-builder AST round-trips, and the new modifiers select
    the rows they name. No corpus -- a four-row table is enough to
    tell a first raise from a cbet and a last action from the one
    before it.
    """
    fails = []
    argv = ["--street", "flop", "--flop", "XBmC", "--first-raise",
            "--size", "0.4-0.75", "--players", "6", "--stack", "100+",
            "--ip"]
    d = FilterDef.from_argv(argv)
    back = FilterDef.from_argv(d.to_argv())
    if d.to_dict() != back.to_dict():
        fails.append(f"FilterDef round-trip drifted: {d.to_dict()} vs "
                     f"{back.to_dict()}")
    where, _, _ = build(d.to_argv())
    for needle in ("street_agg", "pot_frac >= 0.4", "n_players = 6",
                   "eff_bb >= 100", "GLOB", "is_ip = 1"):
        if needle not in where:
            fails.append(f"compiled filter missed {needle!r}: {where}")

    as_json = json.dumps(d.to_dict())
    from_json = FilterDef.from_dict(json.loads(as_json))
    if _canonical(from_json.to_argv()) != _canonical(d.to_argv()):
        fails.append("FilterDef JSON dict did not rebuild the argv")

    try:
        parse_size("nope")
        fails.append("parse_size accepted 'nope'")
    except ValueError:
        pass
    if parse_size("m") != ("letter", "m"):
        fails.append(f"size m was {parse_size('m')}")
    if parse_size("0.4-0.75") != ("range", 0.4, 0.75):
        fails.append(f"size 0.4-0.75 was {parse_size('0.4-0.75')}")
    if parse_size("50%+")[0] != "range" or abs(parse_size("50%+")[1] - 0.5) > 1e-9:
        fails.append(f"size 50%+ was {parse_size('50%+')}")
    if parse_size("40-75") != ("range", 0.4, 0.75):
        fails.append(f"size 40-75 (percent by magnitude) was {parse_size('40-75')}")
    if parse_stack("100+") != (100.0, None):
        fails.append(f"stack 100+ was {parse_stack('100+')}")
    if parse_stack("<40") != (None, 40.0):
        fails.append(f"stack <40 was {parse_stack('<40')}")
    if parse_stack("80-200") != (80.0, 200.0):
        fails.append(f"stack 80-200 was {parse_stack('80-200')}")

    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, street TEXT, seat INT, action TEXT, "
        "agg INT, to_call REAL, street_agg INT, first_in INT, "
        "was_agg INT, pot_frac REAL, n_players INT, eff_bb REAL)")
    # Flop: cbet (first-in, not a raise), raise of the cbet (first raise),
    # call (last action). Preflop open is a first raise with street_agg 0.
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h1", 1, "flop", 1, "B", 1, 0, 0, 1, 0, 0.50, 6, 120),
         ("h1", 2, "flop", 2, "R", 1, 5, 1, 0, 0, 1.10, 6, 120),
         ("h1", 3, "flop", 1, "C", 0, 10, 2, 0, 1, None, 6, 120),
         ("h2", 1, "preflop", 1, "R", 1, 1, 0, 1, 0, 2.00, 6, 30)])
    first_w, _, _ = build(["--first-raise"])
    rows = [r[0] for r in con.execute(
        f"SELECT n || street FROM decisions WHERE {first_w}")]
    if sorted(rows) != ["1preflop", "2flop"]:
        fails.append(f"--first-raise selected {rows}, not the open and the "
                     f"flop raise")
    last_w, _, _ = build(["--last-action"])
    last = [r[0] for r in con.execute(
        f"SELECT hand_id || n FROM decisions WHERE {last_w}")]
    if sorted(last) != ["h13", "h21"]:
        fails.append(f"--last-action selected {last}, not the last n "
                     f"on each street")
    both, _, _ = build(["--street", "flop", "--first-raise", "--size",
                        "0.9-1.2"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {both}").fetchone()[0]
    if n != 1:
        fails.append(f"flop first-raise of 0.9-1.2 pot selected {n}, "
                     f"not the one raise")
    stack_w, _, _ = build(["--stack", "100+"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {stack_w}").fetchone()[0]
    if n != 3:
        fails.append(f"--stack 100+ selected {n}, expected the three "
                     f"deep flop rows")
    con.close()
    print(f"custom filter AST/modifiers   "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_compare():
    """
    Pin keeps the person; compare holds two spots; the two-column
    numbers come from the same functions the report already prints.

    No corpus. A pin that dropped `--hero` would compare you to the
    pool and look like a finding.
    """
    fails = []
    this, pinned = pin_sides(["--hero", "--street", "flop"], "Flop vs c-bet")
    if "--hero" not in pinned:
        fails.append("pin dropped --hero on the other side")
    if "--not-pfa" not in pinned or "--pfa" in pinned:
        fails.append(f"pin did not open Flop vs c-bet: {pinned}")
    if "--hero" not in this:
        fails.append("pin dropped --hero on this side")
    a, b = compare_sides(["--hero"], "Flop c-bets", "Flop vs c-bet")
    if "--hero" not in a or "--hero" not in b:
        fails.append("compare dropped --hero on a side")
    if _canonical(a) == _canonical(b):
        fails.append("compare of two reports produced the same filter")
    if "--facing" not in a or "check" not in a:
        fails.append(f"compare A was not Flop c-bets: {a}")
    if "--facing" not in b or "bet" not in b:
        fails.append(f"compare B was not Flop vs c-bet: {b}")

    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, seat INT, action TEXT, agg INT, "
        "to_call REAL, amount REAL, pot_before REAL, bb REAL, "
        "first_in INT, was_agg INT, pot_frac REAL, street TEXT, "
        "allin INT)")
    con.execute(
        "CREATE TABLE spots ("
        "hand_id TEXT, seat INT, fmt TEXT, wtsd INT, won REAL, net_bb REAL)")
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h1", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h1", 2, 2, "F", 0, 5, 0, 15, 1, 0, 0, None, "flop", 0),
         ("h5", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h5", 2, 2, "R", 1, 5, 15, 15, 1, 0, 1, 1.00, "flop", 0),
         ("h5", 3, 1, "F", 0, 10, 0, 30, 1, 0, 0, None, "flop", 0)])
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?)",
        [("h1", 1, "RING", 0, 15, 10),
         ("h5", 1, "RING", 0, 0, -5)])
    got = compare_of(con, ["--first-in"], ["--last-raise"],
                     "first in", "last raise")
    if got["a"]["summary"]["opps"] == got["b"]["summary"]["opps"]:
        fails.append("compare_of gave both sides the same opportunities")
    # Two first-in bets: uncontested +10, bet-fold -5. Mean +2.5.
    ap = (got["a"]["profit"] or {}).get("bb_per_hand")
    if ap is None or abs(ap - 2.5) > 1e-9:
        fails.append(f"pinned first-in action profit was {ap}, not +2.5")
    if not got.get("freq_diff"):
        fails.append("compare_of dropped the frequency difference")
    if got["a"]["name"] != "first in" or got["b"]["name"] != "last raise":
        fails.append("compare_of dropped a side's name")

    sizes = bet_sizes_of(con, "street = 'flop'")
    letters = [r["key"] for r in sizes["rows"]]
    if letters != ["m", "p"]:
        fails.append(f"flop bet sizes were {letters}, not ['m', 'p']")
    by_m = {r["key"]: r for r in sizes["rows"]}
    if (by_m.get("m") or {}).get("hits") != 2 \
            or (by_m.get("p") or {}).get("hits") != 1:
        fails.append(f"bet size hits were {by_m}, not m=2 p=1")
    if (by_m.get("m") or {}).get("opps") != 5:
        fails.append(f"bet size opps were {(by_m.get('m') or {}).get('opps')}, "
                     f"not 5 (checks/folds have no size and stay in the parent)")
    if (by_m.get("m") or {}).get("how") != "--size m":
        fails.append("bet size row is not a --size click")

    # `--last-raise` is was_agg, and this fixture sets that on the
    # first-in bets too. The raise row is the one with a pot-sized
    # pot_frac; comparing first-in to that is two different size
    # letters, which is the richer pin.
    rich = compare_of(con, ["--first-in"], ["--where", "action = 'R'"],
                      "first in", "the raise", dim="size")
    sa = {r["key"]: r for r in (rich.get("sizes") or {}).get("a", {}).get("rows") or []}
    sb = {r["key"]: r for r in (rich.get("sizes") or {}).get("b", {}).get("rows") or []}
    if set(sa) != {"m"}:
        fails.append(f"pinned first-in sizes were {set(sa)}, not {{m}}")
    if set(sb) != {"p"}:
        fails.append(f"pinned raise sizes were {set(sb)}, not {{p}}")
    if rich.get("by"):
        fails.append("--by size grew a stat grid -- it is the Bet Sizes pane")
    if (sa.get("m") or {}).get("hits") != 2 \
            or (sb.get("p") or {}).get("hits") != 1:
        fails.append("richer pin size hits drifted from bet_sizes_of")

    by_grid = compare_of(con, ["--first-in"], ["--where", "action = 'R'"],
                         "first in", "the raise",
                         dim="street", columns=["overbet"])
    if not by_grid.get("by"):
        fails.append("pin --by street dropped the two grids")
    elif by_grid["by"]["a"]["counts"].get("flop") != 2:
        fails.append(
            f"pin --by street THIS n={by_grid['by']['a']['counts']}, "
            f"not 2 first-in flop rows")
    elif by_grid["by"]["b"]["counts"].get("flop") != 1:
        fails.append(
            f"pin --by street PINNED n={by_grid['by']['b']['counts']}, "
            f"not 1 raise flop row")
    if by_grid.get("sizes"):
        fails.append("--by street also drew Bet Sizes -- that is --by size")
    con.close()
    print(f"pin / side-by-side compare    "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_cohort():
    """
    Compact parse, aliases, the pooled header, and pin/compare staying
    on the parked players. No corpus -- three rows in `players` and a
    handful of decisions are enough to tell a cohort from everybody.
    """
    fails = []
    fails.extend(players.check_parse())

    spec, rest = players.parse_cohort(
        ["--cohort", "vpip>=40,pfr<=10,hands>=100", "--filter", "3bet"])
    if spec is None or spec[0] != [("vpip", ">=40"), ("pfr", "<=10"),
                                   ("hands", ">=100")]:
        fails.append(f"compact --cohort parsed {spec}")
    if rest != ["--filter", "3bet"]:
        fails.append(f"compact --cohort leftover {rest}")

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE players ("
        "site TEXT, player TEXT, durable INT, hands INT, "
        "vpip REAL, pfr REAL, threebet REAL, fold_to_threebet REAL, "
        "wwsf REAL, wtsd REAL, wsd REAL, bb100 REAL, class TEXT)")
    con.executemany(
        "INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("acr", "loose", 1, 200, 45, 8, None, None, None, None, None, 0, "fish"),
         ("acr", "tight", 1, 500, 22, 18, None, None, None, None, None, 5, "reg"),
         ("acr", "short", 1, 20, 50, 5, None, None, None, None, None, 0, "unknown")])
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, seat INT, action TEXT, agg INT, "
        "to_call REAL, amount REAL, pot_before REAL, bb REAL, "
        "first_in INT, was_agg INT, pot_frac REAL, street TEXT, "
        "site TEXT, player TEXT, allin INT)")
    con.execute(
        "CREATE TABLE spots ("
        "hand_id TEXT, seat INT, fmt TEXT, wtsd INT, won REAL, net_bb REAL)")
    # loose: two first-in bets (the compare fixture). tight/short: one each,
    # so a cohort that forgot to join would still have "some" hits.
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h1", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", "acr", "loose", 0),
         ("h1", 2, 2, "F", 0, 5, 0, 15, 1, 0, 0, None, "flop", "acr", "tight", 0),
         ("h5", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", "acr", "loose", 0),
         ("h5", 2, 2, "R", 1, 5, 15, 15, 1, 0, 1, 1.00, "flop", "acr", "tight", 0),
         ("h5", 3, 1, "F", 0, 10, 0, 30, 1, 0, 0, None, "flop", "acr", "loose", 0),
         ("h9", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", "acr", "tight", 0),
         ("h8", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", "acr", "short", 0)])
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?)",
        [("h1", 1, "RING", 0, 15, 10),
         ("h5", 1, "RING", 0, 0, -5),
         ("h9", 1, "RING", 0, 15, 10),
         ("h8", 1, "RING", 0, 15, 10)])

    spec, _argv = players.parse_cohort(
        ["--cohort", "vpip>=40,pfr<=10,hands>=100"])
    where, header, label = apply_cohort(con, spec, "1=1", "everything")
    if header is None:
        fails.append("apply_cohort returned no header")
    else:
        if header["players"] != 1 or header["hands"] != 200:
            fails.append(
                f"header was {header['players']} players / "
                f"{header['hands']} hands, not 1 / 200")
        if "vpip" not in header["describe"] or "hands" not in header["describe"]:
            fails.append(f"header describe dropped the filter: {header['describe']}")
        if label is None or "1 player" not in label or "200" not in label:
            fails.append(f"report label missing counts: {label!r}")
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    if n != 3:
        fails.append(f"pooled cohort kept {n} decisions, not the loose player's 3")

    # Pin/compare rebuild WHERE from argv. Without with_cohort they
    # would count tight's first-in too and the heading would still
    # say "fish with 100+ hands".
    got = compare_of(con, ["--first-in"], ["--last-raise"],
                     "first in", "last raise")
    if got["a"]["summary"]["opps"] != 2:
        fails.append(
            f"compare under cohort saw {got['a']['summary']['opps']} "
            f"first-in opps, not the loose player's 2")
    if not cohort_loaded(con):
        fails.append("apply_cohort did not park _cohort")

    spec, _ = players.parse_cohort(["--cohort", "Hands() >= 100"])
    where, header, _label = apply_cohort(con, spec, "1=1", "everything")
    if header is None or header["players"] != 2 or header["hands"] != 700:
        fails.append(
            f"Hands() >= 100 header was {header}, not 2 players / 700 hands")
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
    if n != 6:
        fails.append(f"Hands() cohort kept {n} decisions, not 6")

    spec_fish, _ = players.parse_cohort(
        ["--cohort-hands", "100", "--cohort-vpip", "40+", "--class", "fish"])
    rows = players.cohort(con, *spec_fish)
    if [r["player"] for r in rows] != ["loose"]:
        fails.append(f"--cohort-hands/--class fish selected "
                     f"{[r['player'] for r in rows]}")

    # Preflop range on the parked cohort: only loose's hole cards.
    # tight has AA shown; if the chart forgot the join it would draw AA
    # under a "fish with 100+ hands" heading.
    con.execute("ALTER TABLE decisions ADD COLUMN combo TEXT")
    con.execute("ALTER TABLE decisions ADD COLUMN made TEXT")
    con.execute("ALTER TABLE decisions ADD COLUMN fd TEXT")
    con.execute("ALTER TABLE decisions ADD COLUMN sd TEXT")
    con.execute("UPDATE decisions SET combo = 'AKs', made = 'top pair' "
                "WHERE hand_id = 'h1' AND seat = 1")
    con.execute("UPDATE decisions SET combo = 'QJs', made = 'middle pair' "
                "WHERE hand_id = 'h5' AND seat = 1")
    con.execute("UPDATE decisions SET combo = 'AA', made = 'overpair' "
                "WHERE player = 'tight'")
    spec, _argv = players.parse_cohort(
        ["--cohort", "vpip>=40,pfr<=10,hands>=100"])
    where, _header, _label = apply_cohort(con, spec, "1=1", "everything")
    g = chart_of(con, where)
    if g["seen"] != 2:
        fails.append(f"cohort chart saw {g['seen']} player-hands, not loose's 2")
    if "AA" in g["cells"]:
        fails.append("cohort chart included tight's AA -- the join dropped")
    if set(g["cells"]) != {"AKs", "QJs"}:
        fails.append(f"cohort chart cells were {sorted(g['cells'])}, not AKs/QJs")
    cov = g.get("coverage") or coverage_of(con, where)
    if cov["seen"] != 2 or cov["total"] != 2:
        fails.append(f"cohort coverage was {cov['seen']}/{cov['total']}, not 2/2")
    rng = range_of(con, where)
    made = {r["made"] for r in rng["rows"]}
    if "overpair" in made:
        fails.append("cohort range included tight's overpair")
    if made != {"top pair", "middle pair"}:
        fails.append(f"cohort range was {made}, not loose's two hands")
    if not rng.get("coverage") or rng["coverage"]["seen"] != 2:
        fails.append("cohort range dropped the hole-card coverage caveat")
    con.close()
    print(f"multi-player cohort           "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_aliases_filter():
    """
    `--alias` / `--vs-alias` become an OR of quoted (site, name) pairs.
    No corpus -- a temp aliases.json is enough to tell a merge from
    `--hero`, which is a different door (`is_hero = 1`).
    """
    fails = []
    was = aliases.PATH
    folder = Path(tempfile.mkdtemp())
    aliases.PATH = folder / "aliases.json"
    try:
        aliases.create("me", "HeroA", "acr", single=True)
        aliases.add("me", "HeroB", "pokerstars")
        aliases.create("nits", "TightGuy", "acr", single=False)
        where, label, _ = build(["--alias", "me"])
        if "HeroA" not in where or "pokerstars" not in where:
            fails.append(f"--alias SQL dropped a member: {where}")
        if "is_hero" in where:
            fails.append("--alias used is_hero instead of the named merge")
        if "alias" not in label:
            fails.append(f"--alias label hid the name: {label}")
        where, label, _ = build(["--vs-alias", "nits"])
        if "vs_player" not in where or "TightGuy" not in where:
            fails.append(f"--vs-alias SQL was {where}")
        if "group" not in label:
            fails.append(f"group alias label was {label}")
        try:
            build(["--alias", "missing"])
            fails.append("missing alias was accepted")
        except SystemExit:
            pass
        who = who_only(["--alias", "me", "--street", "flop"])
        if "--alias" not in who or "--street" in who:
            fails.append(f"who_only dropped --alias: {who}")
    finally:
        aliases.PATH = was
    print(f"alias / vs-alias filters      "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_study():
    """
    --marked / --tag / --noted select the starred hands, and only those.

    No corpus. Two decisions and a notes.db in a temp dir are enough
    to tell a mark from everybody, which is the failure this filter
    has if ATTACH is forgotten.
    """
    fails = []
    import tempfile
    store = Path(tempfile.mkdtemp()) / "notes.db"
    notes.mark("h1", tags=["leak"], path=store)
    notes.add("too wide", player="Alice", hand_id="h1", path=store)
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE decisions (hand_id TEXT, seat INT, action TEXT)")
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?)",
        [("h1", 1, "B"), ("h2", 1, "F")])
    notes.attach(con, path=store)
    marked_w, _, _ = build(["--marked"])
    tag_w, _, _ = build(["--tag", "leak"])
    noted_w, _, _ = build(["--noted"])
    n_mark = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {marked_w}").fetchone()[0]
    n_tag = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {tag_w}").fetchone()[0]
    n_note = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {noted_w}").fetchone()[0]
    if n_mark != 1 or n_tag != 1 or n_note != 1:
        fails.append(
            f"--marked/--tag/--noted kept {n_mark}/{n_tag}/{n_note}, "
            "not 1/1/1")
    both = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {marked_w} "
        f"AND {tag_w}").fetchone()[0]
    if both != 1:
        fails.append("marked AND tag did not stay on the one hand")
    con.close()
    print(f"notes / marked-hand filters   "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_sessions():
    """
    `--session` / `--today` / `--hours` compile, and stay on the person.

    No corpus. The cluster itself is `sessions.py --check`; this is
    the filter half -- a sit-down that compiled to a date range, or
    that a Smart Report dropped, would empty Reports and look like
    the session had no hands.
    """
    fails = []
    where, label, _p = build(["--session", "h1"])
    if "session_hands" not in where or "h1" not in where:
        fails.append(f"--session compiled to {where!r}")
    if "session h1" not in label:
        fails.append(f"--session labelled {label!r}")
    kept = who_only(["--hero", "--session", "h1", "--street", "flop"])
    if "--session" not in kept or "h1" not in kept:
        fails.append("who_only dropped --session")
    if "--street" in kept:
        fails.append("who_only kept a situation flag")
    gone = without_who(["--session", "h1", "--pot", "3bet"])
    if "--session" in gone:
        fails.append("without_who kept --session")
    fmt_w, fmt_l, _ = build(["--fmt", "cash"])
    if "fmt <> 'MTT'" not in fmt_w or fmt_l != "cash":
        fails.append(f"--fmt cash compiled to {fmt_w!r} / {fmt_l!r}")
    mtt_w, mtt_l, _ = build(["--fmt", "mtt"])
    if "fmt = 'MTT'" not in mtt_w or mtt_l != "mtt":
        fails.append(f"--fmt mtt compiled to {mtt_w!r} / {mtt_l!r}")
    last_w, last_l, _ = build(["--last-sessions", "10"])
    if "LIMIT 10" not in last_w or "last 10 sessions" not in last_l:
        fails.append(f"--last-sessions compiled to {last_w!r} / {last_l!r}")
    who = who_only(["--fmt", "cash", "--last-sessions", "5",
                    "--street", "flop"])
    if "--fmt" not in who or "--last-sessions" not in who:
        fails.append("who_only dropped --fmt / --last-sessions")
    if "--street" in who:
        fails.append("who_only kept --street with fmt")
    today, tlabel, _ = build(["--today", "--start-of-day", "6"])
    if "played_at" not in today or "site = 'acr'" not in today:
        fails.append(f"--today was not per-site: {today!r}")
    if tlabel != "today":
        fails.append(f"--today labelled {tlabel!r}")
    hours, hlabel, _ = build(["--hours", "4", "--tz", "acr=-3"])
    if "played_at" not in hours or "last 4 hours" not in hlabel:
        fails.append(f"--hours compiled to {hours!r} / {hlabel!r}")
    try:
        build(["--hours", "0"])
        fails.append("--hours 0 was accepted")
    except SystemExit:
        pass
    print(f"session / today / hours flags  "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_statistics():
    """
    Statistics grid, Call Range, and exclude_reg_vs_fish.

    No corpus. The failure this catches is the H2N one: a rebuild flag
    that leaked into Reports, or a Call Range that was the raise again.
    """
    fails = []
    if "--exclude-reg-vs-fish" not in SKIP:
        fails.append("exclude flag is not in SKIP -- build() would "
                     "raise or apply it to Reports")
    if "--exclude-reg-vs-fish" in OPTIONS:
        fails.append("exclude flag is in OPTIONS -- build() skips two "
                     "tokens and would drop the next flag")
    try:
        build(["--exclude-reg-vs-fish"])
    except SystemExit:
        fails.append("build() rejected --exclude-reg-vs-fish instead of "
                     "skipping it (Reports must ignore it)")
    excl_w, excl_l, _ = build(["--exclude-reg-vs-fish", "--hero"])
    if "n_fish" in excl_w or "player_class = 'reg'" in excl_w:
        fails.append("build() applied exclude_reg_vs_fish -- Reports "
                     "would change with the Statistics toggle")
    if "is_hero = 1" not in excl_w:
        fails.append("build(--exclude-reg-vs-fish --hero) dropped --hero")
    kept_hero = who_only(["--exclude-reg-vs-fish", "--hero"])
    if "--hero" not in kept_hero:
        fails.append("who_only ate --hero after the exclude flag")

    cr_w, cr_l, _ = build(["--call-range", "threebet"])
    if "facing='open'" not in cr_w.replace(" ", "") and \
            "facing = 'open'" not in cr_w:
        # chance is street='preflop' AND facing='open'
        if "facing='open'" not in cr_w:
            fails.append(f"--call-range threebet lost the chance: {cr_w!r}")
    if "action IN ('C','A')" not in cr_w:
        fails.append(f"--call-range threebet was not a call: {cr_w!r}")
    if "agg = 0" not in cr_w:
        fails.append("--call-range threebet counted raises as calls")
    if "agg=1" in cr_w.replace(" ", ""):
        fails.append("--call-range threebet still used the 3bet action")

    opened = reports_argv(["--hero", "--fmt", "cash", "--last-sessions", "3",
                           "--exclude-reg-vs-fish"],
                          "threebet", "action")
    if "--quick" not in opened or "threebet" not in opened:
        fails.append(f"Open in Reports missing --quick threebet: {opened}")
    if "--exclude-reg-vs-fish" in opened:
        fails.append("Open in Reports carried the exclude flag")
    if "--hero" not in opened or "--fmt" not in opened:
        fails.append("Open in Reports dropped the subject / cash mode")
    called = reports_argv(["--hero"], "threebet", "call", "AKs")
    if called != ["--hero", "--call-range", "threebet", "--combo", "AKs"]:
        fails.append(f"Call Range reports_argv drifted: {called}")

    cleaned, hit, kind, combo = take_stat_drill(
        ["--hero", "--quick", "threebet", "--combo", "AKs", "--fmt", "cash"])
    if hit != "threebet" or kind != "action" or combo != "AKs":
        fails.append(f"take_stat_drill lost the click: {hit} {kind} {combo}")
    if "--quick" in cleaned or "--combo" in cleaned:
        fails.append("take_stat_drill left the drill on the sample argv")
    if "--hero" not in cleaned or "--fmt" not in cleaned:
        fails.append("take_stat_drill dropped the subject")
    sample, _, _ = build(cleaned)
    if "facing='open'" in sample.replace(" ", "") or \
            "facing = 'open'" in sample:
        fails.append("Statistics --hit leaked into the grid sample")
    cleaned_c, hit_c, kind_c, _ = take_stat_drill(
        ["--call-range", "threebet", "--hero"])
    if hit_c != "threebet" or kind_c != "call" or "--call-range" in cleaned_c:
        fails.append("take_stat_drill did not lift Call Range off the sample")

    # Four 3bets: reg-vs-fish, reg-vs-reg, fish-vs-reg, unknown-vs-fish.
    # Exclude must drop only the first. `--quick threebet` (Reports)
    # must still see all four.
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, seat INT, player TEXT, site TEXT, "
        "player_class TEXT, n_fish INT, n_reg INT, vs_class TEXT, "
        "street TEXT, facing TEXT, action TEXT, agg INT, combo TEXT, "
        "fmt TEXT, is_hero INT, played_at TEXT)")
    rows = [
        ("h1", 1, 1, "RegA", "acr", "reg", 1, 0, "fish",
         "preflop", "open", "R", 1, "AKs", "RING", 1, "2026-09-01"),
        ("h2", 1, 1, "RegA", "acr", "reg", 0, 1, "reg",
         "preflop", "open", "R", 1, "AQs", "RING", 1, "2026-09-01"),
        ("h3", 1, 1, "FishB", "acr", "fish", 0, 1, "reg",
         "preflop", "open", "R", 1, "KQs", "RING", 1, "2026-09-01"),
        ("h4", 1, 1, "UnkC", "acr", "unknown", 1, 0, "fish",
         "preflop", "open", "R", 1, "AJo", "RING", 1, "2026-09-01"),
        ("h5", 1, 1, "RegA", "acr", "reg", 1, 0, "fish",
         "preflop", "open", "C", 0, "T9s", "RING", 1, "2026-09-01"),
    ]
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    where, _, _ = build([])
    all_n = con.execute(f"SELECT COUNT(*) FROM decisions WHERE {where}"
                        ).fetchone()[0]
    got = statistics_of(con, where, [], exclude=False)
    dropped = statistics_of(con, where, [], exclude=True)
    # five decisions; exclude drops h1 and h5 (reg + n_fish>0)
    if got["n"] != 5:
        fails.append(f"statistics_of without exclude saw {got['n']}, not 5")
    if dropped["n"] != 3:
        fails.append(f"exclude_reg_vs_fish left {dropped['n']}, not 3 "
                     "(should drop the two reg-vs-fish rows)")
    if dropped["excluded"] != 2:
        fails.append(f"excluded count was {dropped['excluded']}, not 2")
    # Reports path: --quick threebet still sees every 3bet including
    # the reg-vs-fish raise. h5 is a call, not a 3bet.
    q_w, _, _ = build(["--quick", "threebet"])
    q_n = con.execute(f"SELECT COUNT(*) FROM decisions WHERE {q_w}"
                      ).fetchone()[0]
    if q_n != 4:
        fails.append(f"--quick threebet (Reports) saw {q_n}, not 4 -- "
                     "exclude leaked into build()")
    tb = next((r for r in dropped["rows"] if r["key"] == "threebet"), None)
    if tb is None or tb["k"] != 3:
        fails.append(f"excluded 3bet hits were {tb}, not 3 "
                     "(reg-vs-reg + fish-vs-reg + unknown)")
    cr = call_range_sql("threebet")
    cr_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {cr}").fetchone()[0]
    if cr_n != 1:
        fails.append(f"Call Range of 3bet saw {cr_n}, not the one call")
    # The one call is h5, a reg-vs-fish limp-call. Exclude drops it
    # from the Statistics Call Range sample.
    cr_ex = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {with_exclude(cr, True)}"
    ).fetchone()[0]
    if cr_ex != 0:
        fails.append("excluded Call Range still kept the reg-vs-fish call")
    con.close()
    if all_n != 5:
        fails.append("fixture WHERE 1=1 was not 5 rows")

    mem = sqlite3.connect(":memory:")
    mem.execute(
        "CREATE TABLE decisions (hand_id TEXT, seat INT, player TEXT, "
        "site TEXT, player_class TEXT, n_fish INT, street TEXT, "
        "facing TEXT, action TEXT, agg INT, combo TEXT)")
    empty = statistics_of(mem, "1=1")
    mem.close()
    if [r["key"] for r in empty["rows"]] != list(CURATED):
        fails.append("statistics_of did not return the curated grid")
    if "threebet" not in CURATED:
        fails.append("3bet is not on the Statistics grid")

    print(f"Statistics / Call Range / exclude  "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def check_fixture():
    """
    Outcome / size / first-in against a hand-built table.

    The live corpus is gitignored. These three hands are enough to
    prove the partition and the size letter, and they do not need
    anyone's database.
    """
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, seat INT, action TEXT, agg INT, "
        "to_call REAL, amount REAL, pot_before REAL, bb REAL, "
        "first_in INT, was_agg INT, pot_frac REAL, street TEXT)")
    # Three bets by seat 1, then what seat 2 did: fold, call, raise.
    rows = [
        ("h1", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop"),
        ("h1", 2, 2, "F", 0, 5, 0, 15, 1, 0, 0, None, "flop"),
        ("h2", 1, 1, "B", 1, 0, 6, 10, 1, 1, 1, 0.60, "flop"),
        ("h2", 2, 2, "C", 0, 6, 6, 16, 1, 0, 0, 0.60, "flop"),
        ("h3", 1, 1, "B", 1, 0, 8, 10, 1, 1, 1, 0.80, "flop"),
        ("h3", 2, 2, "R", 1, 8, 20, 18, 1, 0, 1, 1.11, "flop"),
        ("h4", 1, 1, "F", 0, 5, 0, 10, 1, 0, 0, None, "flop"),
    ]
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # Call Profit Rate reads allin (an all-in call could not raise).
    # Faced Next squeeze reads facing / n_live / pot_bb on a later
    # row. Added here so matching_hands and chain_report, which now
    # always touch those, cannot fail on the original three-hand table.
    con.execute("ALTER TABLE decisions ADD COLUMN allin INT")
    con.execute("ALTER TABLE decisions ADD COLUMN facing TEXT")
    con.execute("ALTER TABLE decisions ADD COLUMN n_live INT")
    con.execute("ALTER TABLE decisions ADD COLUMN pot_bb REAL")
    con.execute("UPDATE decisions SET allin = 0")
    fails = []
    bets = "seat = 1 AND agg = 1"
    outs = outcomes_of(con, bets)
    by = {r["key"]: r["k"] for r in outs["rows"]}
    if by.get("fold-out") != 1 or by.get("call") != 1 or by.get("raise-back") != 1:
        fails.append(f"outcome partition was {by}, not one of each")
    if outs["bets"] != 3 or sum(by.values()) != 3:
        fails.append(f"outcomes covered {sum(by.values())} of {outs['bets']}")
    fold_w, _, _ = build(["--outcome", "fold-out"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {fold_w}").fetchone()[0]
    # The h1 bet (villain folded) and the h3 raise (nobody acted after).
    if n != 2:
        fails.append(f"--outcome fold-out selected {n}, not both uncontested")
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({fold_w}) AND seat = 1"
    ).fetchone()[0]
    if n != 1:
        fails.append(f"seat-1 fold-out selected {n}, not the one bet")
    size_w, _, _ = build(["--size", "m"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {size_w}").fetchone()[0]
    # h1 bet 0.50 and h2 bet 0.60 are medium (0.40 < x <= 0.60);
    # the call on h2 is also 0.60. Three medium rows.
    if n != 3:
        fails.append(f"--size m selected {n}, expected 3")
    first_w, _, _ = build(["--first-in"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {first_w}").fetchone()[0]
    if n != 3:
        fails.append(f"--first-in selected {n}, expected 3")
    last_w, _, _ = build(["--last-raise"])
    n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {last_w}").fetchone()[0]
    if n != 4:
        fails.append(f"--last-raise selected {n}, expected 4")

    # Action profit v1: the three examples, and a called pot left unpriced.
    # spots is required for the cash-game / won / wtsd half; a fold with
    # no spots row must still be 0 (LEFT JOIN), or a missing derivation
    # would drop every fold from the mean.
    con.execute(
        "CREATE TABLE spots ("
        "hand_id TEXT, seat INT, fmt TEXT, wtsd INT, won REAL, net_bb REAL)")
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?)",
        [("h1", 1, "RING", 0, 15, 10),
         ("h2", 1, "RING", 1, 0, -6),
         ("h3", 1, "RING", 1, 20, 5),
         ("h5", 1, "RING", 0, 0, -5)])
    con.executemany(
        "INSERT INTO decisions "
        "(hand_id,n,seat,action,agg,to_call,amount,pot_before,bb,"
        "first_in,was_agg,pot_frac,street,allin) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h5", 1, 1, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h5", 2, 2, "R", 1, 5, 15, 15, 1, 0, 1, 1.00, "flop", 0),
         ("h5", 3, 1, "F", 0, 10, 0, 30, 1, 0, 0, None, "flop", 0)])
    for where, want, priced in (
            ("hand_id='h1' AND seat=1 AND agg=1", 10.0, 1),
            ("hand_id='h5' AND seat=1 AND agg=1", -5.0, 1),
            ("hand_id='h4' AND seat=1", 0.0, 1)):
        got = action_profit_of(con, where)
        if got["priced"] != priced or got["bb_per_hand"] != want:
            fails.append(
                f"action profit {where} was {got['bb_per_hand']} "
                f"on {got['priced']} priced, expected {want} on {priced}")
    called = action_profit_of(con, "hand_id='h2' AND seat=1 AND agg=1")
    if called["priced"] or called["bb_per_hand"] is not None:
        fails.append("a called pot was priced -- that is Call Profit Rate")
    three = action_profit_of(
        con,
        "seat=1 AND ((hand_id='h1' AND agg=1) OR "
        "(hand_id='h5' AND agg=1) OR hand_id='h4')")
    if three["priced"] != 3 or abs((three["bb_per_hand"] or 0) - (5 / 3)) > 1e-9:
        fails.append(
            f"the three examples averaged {three['bb_per_hand']}, "
            f"not (10-5+0)/3")
    for col in ("played_at", "site", "position", "combo", "board"):
        con.execute(f"ALTER TABLE decisions ADD COLUMN {col} TEXT")
    per = matching_hands(con, "hand_id='h1' AND seat=1 AND agg=1")
    if len(per) != 1 or per[0]["act"] != 10:
        fails.append(
            f"per-hand action profit was {per}, not +10 on the uncontested bet")
    per = matching_hands(con, "hand_id='h5' AND seat=1 AND agg=1")
    if len(per) != 1 or per[0]["act"] != -5:
        fails.append(
            f"per-hand action profit was {per}, not -5 on the bet-fold")
    faced = chain_report(con, "seat=1 AND agg=1", None, False)
    by = {r["key"]: r for r in faced["rows"]}
    if "fold" not in by or "call" not in by or "raise" not in by:
        fails.append(f"faced next missed a verb: {sorted(by)}")
    fold_p = (by.get("fold") or {}).get("profit") or {}
    if fold_p.get("bb_per_hand") != 10:
        fails.append(
            f"faced-next fold action profit was {fold_p.get('bb_per_hand')}, "
            f"not +10")
    raise_p = (by.get("raise") or {}).get("profit") or {}
    if raise_p.get("bb_per_hand") != -5:
        fails.append(
            f"faced-next raise action profit was {raise_p.get('bb_per_hand')}, "
            f"not -5 (the bet that was raised and folded)")
    none_w, _, _ = build(["--after", "none"])
    none_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {none_w}").fetchone()[0]
    # h3 seat 2 raise and h4 fold have no later other-seat action? 
    # h4 is a fold by seat 1, no later other. h3 raise by seat 2, no later other.
    # Also h5 fold by seat 1. Several rows. Just assert it narrows.
    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    if none_n == 0 or none_n == total:
        fails.append(f"--after none selected {none_n} of {total}")

    # Call Profit Rate: actual calls when raise was also legal.
    # allin is already on the table (see above).
    # h6: call 5 into pot 15, no more chips, win 20 → +15 (= pot_before)
    # h7: call 5, lose → −5
    # h8: all-in call -- raise was not an option, unpriced
    # h9: MTT call -- chips are not dollars, unpriced
    # h10: call 5 then bet 10, win 40 → (40-15)/1 = +25 (later assigned back)
    con.executemany(
        "INSERT INTO decisions "
        "(hand_id,n,seat,action,agg,to_call,amount,pot_before,bb,"
        "first_in,was_agg,pot_frac,street,allin) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h6", 1, 2, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h6", 2, 1, "C", 0, 5, 5, 15, 1, 0, 0, 0.33, "flop", 0),
         ("h7", 1, 2, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h7", 2, 1, "C", 0, 5, 5, 15, 1, 0, 0, 0.33, "flop", 0),
         ("h8", 1, 2, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h8", 2, 1, "C", 0, 5, 5, 15, 1, 0, 0, 0.33, "flop", 1),
         ("h9", 1, 2, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h9", 2, 1, "C", 0, 5, 5, 15, 1, 0, 0, 0.33, "flop", 0),
         ("h10", 1, 2, "B", 1, 0, 5, 10, 1, 1, 1, 0.50, "flop", 0),
         ("h10", 2, 1, "C", 0, 5, 5, 15, 1, 0, 0, 0.33, "flop", 0),
         ("h10", 3, 1, "B", 1, 0, 10, 20, 1, 1, 1, 0.50, "turn", 0)])
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?)",
        [("h6", 1, "RING", 1, 20, 10),
         ("h7", 1, "RING", 1, 0, -5),
         ("h8", 1, "RING", 1, 20, 10),
         ("h9", 1, "MTT", 1, 20, 10),
         ("h10", 1, "RING", 1, 40, 20)])
    for where, want, priced in (
            ("hand_id='h6' AND seat=1 AND action='C'", 15.0, 1),
            ("hand_id='h7' AND seat=1 AND action='C'", -5.0, 1),
            ("hand_id='h10' AND seat=1 AND action='C'", 25.0, 1)):
        got = call_profit_of(con, where)
        if got["priced"] != priced or got["bb_per_hand"] != want:
            fails.append(
                f"call profit {where} was {got['bb_per_hand']} "
                f"on {got['priced']} priced, expected {want} on {priced}")
    shove = call_profit_of(con, "hand_id='h8' AND seat=1 AND action='C'")
    if shove["priced"] or shove["bb_per_hand"] is not None:
        fails.append("an all-in call was priced -- raise was not an option")
    mtt = call_profit_of(con, "hand_id='h9' AND seat=1 AND action='C'")
    if mtt["priced"] or mtt["bb_per_hand"] is not None:
        fails.append("an MTT call was priced -- chips are not dollars")
    bettor = call_profit_of(con, "hand_id='h1' AND seat=1 AND agg=1")
    if bettor["priced"] or bettor["bb_per_hand"] is not None:
        fails.append("a bet was priced as Call Profit -- only actual calls")
    two = call_profit_of(
        con, "seat=1 AND action='C' AND hand_id IN ('h6','h7')")
    if two["priced"] != 2 or abs((two["bb_per_hand"] or 0) - 5.0) > 1e-9:
        fails.append(
            f"the two call examples averaged {two['bb_per_hand']}, "
            f"not (15-5)/2")
    per = matching_hands(con, "hand_id='h6' AND seat=1 AND action='C'")
    if len(per) != 1 or per[0]["call"] != 15:
        fails.append(
            f"per-hand call profit was {per}, not +15 on the winning call")

    # Squeeze: any later squeeze, not the first later action.
    # h11: open, call, squeeze -- the open's first later action is the
    # call; --after squeeze must still select the open.
    # h12: open, 3-bet, no caller -- first later is raise, not a squeeze.
    con.executemany(
        "INSERT INTO decisions "
        "(hand_id,n,seat,action,agg,to_call,amount,pot_before,bb,"
        "first_in,was_agg,pot_frac,street,allin,facing,n_live,pot_bb) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("h11", 1, 1, "R", 1, 0, 2.5, 1.5, 1, 1, 1, None, "preflop",
          0, "unopened", 6, 1.5),
         ("h11", 2, 2, "C", 0, 2.5, 2.5, 4.0, 1, 0, 0, None, "preflop",
          0, "open", 5, 4.0),
         ("h11", 3, 3, "R", 1, 2.5, 10, 6.5, 1, 0, 1, None, "preflop",
          0, "open", 4, 6.5),
         ("h12", 1, 1, "R", 1, 0, 2.5, 1.5, 1, 1, 1, None, "preflop",
          0, "unopened", 6, 1.5),
         ("h12", 2, 2, "R", 1, 2.5, 8, 4.0, 1, 0, 1, None, "preflop",
          0, "open", 5, 4.0)])
    sq_w, _, _ = build(["--after", "squeeze"])
    sq_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({sq_w}) AND hand_id='h11'"
    ).fetchone()[0]
    # Open and the call both have a later squeeze; the squeeze itself
    # does not.
    if sq_n != 2:
        fails.append(
            f"--after squeeze on the squeezed hand selected {sq_n}, "
            f"not the open and the call")
    sq_open = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({sq_w}) "
        f"AND hand_id='h11' AND n=1"
    ).fetchone()[0]
    if sq_open != 1:
        fails.append("--after squeeze missed the open (first later is the call)")
    sq_3bet = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({sq_w}) AND hand_id='h12'"
    ).fetchone()[0]
    if sq_3bet:
        fails.append("--after squeeze selected the no-caller 3-bet hand")
    raise_w, _, _ = build(["--after", "raise"])
    raise_open = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE ({raise_w}) "
        f"AND hand_id='h11' AND n=1"
    ).fetchone()[0]
    if raise_open:
        fails.append("--after raise selected the open that was called, "
                     "not 3-bet -- that is first-later, and squeeze is not")
    faced = chain_of(con, "hand_id='h11' AND n=1", False)
    by = {r["key"]: r for r in faced["rows"]}
    if "squeeze" not in by:
        fails.append(f"faced next on the open missed squeeze: {sorted(by)}")
    if (by.get("squeeze") or {}).get("k") != 1:
        fails.append("faced next squeeze on the open was not 1")
    if "call" not in by:
        fails.append("faced next on the open dropped the first-later call")

    # Intervals: the three Action Profit examples are n=3, so a t band
    # must exist; a single priced row must not invent one.
    three = action_profit_of(
        con,
        "seat=1 AND ((hand_id='h1' AND agg=1) OR "
        "(hand_id='h5' AND agg=1) OR hand_id='h4')")
    if three["lo"] is None or three["hi"] is None:
        fails.append("n=3 action profit dropped its sampling interval")
    if three["lo"] >= three["bb_per_hand"] or three["hi"] <= three["bb_per_hand"]:
        fails.append("action profit interval does not contain the mean")
    one = action_profit_of(con, "hand_id='h1' AND seat=1 AND agg=1")
    if one["lo"] is not None or one.get("interval_note", "").find("no interval") < 0:
        fails.append("n=1 action profit invented an interval")
    two = call_profit_of(
        con, "seat=1 AND action='C' AND hand_id IN ('h6','h7')")
    if two["lo"] is None or two["hi"] is None:
        fails.append("n=2 call profit dropped its sampling interval")

    # Won$ of the two priced Action Profit hands: +10 and −5. MTT h9 stays out.
    won = amount_won_of(con, "hand_id IN ('h1','h5') AND seat=1 AND agg=1")
    if won["hands"] != 2:
        fails.append(f"Won$ counted {won['hands']} hands, not h1 and h5")
    if abs((won["bb_per_hand"] or 0) - 2.5) > 1e-9:
        fails.append(f"Won$ mean was {won['bb_per_hand']}, not (10-5)/2")
    if won["won_hands"] != 1 or abs((won["won_pct"] or 0) - 50) > 1e-9:
        fails.append(f"Won hand% was {won['won_pct']} on {won['won_hands']}, not 50")
    if won["lo"] is None:
        fails.append("Won$ n=2 dropped its sampling interval")
    mtt_won = amount_won_of(con, "hand_id='h9'")
    if mtt_won["hands"]:
        fails.append("Won$ priced an MTT hand -- chips are not dollars")
    # A street-shaped filter still has Won$ -- that is the blanking we refuse.
    flop_won = amount_won_of(con, "street='flop' AND seat=1 AND agg=1")
    if not flop_won["hands"]:
        fails.append("Won$ blanked under a street filter")
    con.close()
    print(f"outcome/size/action-profit fixture  "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


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
    fails = []
    fails.extend(check_shape())
    fails.extend(check_fixture())
    fails.extend(check_filterdef())
    fails.extend(check_compare())
    fails.extend(check_cohort())
    fails.extend(check_aliases_filter())
    fails.extend(check_study())
    fails.extend(check_sessions())
    fails.extend(check_statistics())
    fails.extend(compact.check())
    db = Path(db_path)
    if not db.exists() or db.stat().st_size == 0:
        print()
        print("FAIL: " + "; ".join(fails) if fails else
              "PASS (no hands.db -- shape and fixture only)")
        return not fails
    con = sqlite3.connect(db)
    sessions.ensure(con)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "decisions" not in tables:
        con.close()
        print()
        print("FAIL: " + "; ".join(fails) if fails else
              "PASS (no hands.db -- shape and fixture only)")
        return not fails
    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    checked = 0

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
    cases += [("--after fold", ["--after", "fold"]),
              ("--then bet", ["--then", "bet"]),
              ("--after none", ["--after", "none"]),
              ("--size m", ["--size", "m"]),
              ("--size 0.4-0.75", ["--size", "0.4-0.75"]),
              ("--stack 100+", ["--stack", "100+"]),
              ("--outcome fold-out", ["--outcome", "fold-out"]),
              ("--action call", ["--action", "call"]),
              ("--result won", ["--result", "won"]),
              ("--combo Axs", ["--combo", "Axs"])]
    cases += [(f"--preset {name}", list(flags))
              for name, flags in SMART_REPORTS.items()]
    cases.append(("--player", ["--player", con.execute(
        "SELECT player FROM decisions WHERE player IS NOT NULL LIMIT 1"
    ).fetchone()[0]]))
    try:
        sid = con.execute("SELECT id FROM sessions LIMIT 1").fetchone()
    except sqlite3.Error:
        sid = None
    if sid:
        cases.append(("--session", ["--session", sid[0]]))

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

    # Smart Reports are situations, and every one of them has to stay a
    # situation this engine can open. A related name that is not a report
    # is a dead click in the window; a column that is not a stat is a
    # blank report tab that looks like "nothing happened on the flop".
    report_fails = []
    for name, flags in SMART_REPORTS.items():
        try:
            build(list(flags))
        except SystemExit as e:
            report_fails.append(f"{name}: {e}")
        for other in REPORT_RELATED.get(name, ()):
            if other not in SMART_REPORTS:
                report_fails.append(f"{name} related {other!r} is not a report")
        if name not in REPORT_FAMILY:
            report_fails.append(f"{name} has no family")
    if len(SMART_REPORTS) < 15:
        report_fails.append(f"only {len(SMART_REPORTS)} built-in reports -- "
                            f"the tree is the product")
    print(f"built-in reports still build  "
          f"{len(SMART_REPORTS) - len(report_fails)}/{len(SMART_REPORTS)}")
    fails.extend(report_fails)

    col_fails = []
    if columns_for([]) != list(DEFAULT_COLUMNS):
        col_fails.append("an empty filter must keep the default columns")
    river_cols = columns_for(["--street", "river"])
    if "vpip" in river_cols or "cbet_flop" in river_cols:
        col_fails.append("a river filter still leads with a preflop/flop stat")
    if "fold_to_river_bet" not in river_cols:
        col_fails.append("a river filter dropped fold_to_river_bet")
    flop_cols = columns_for(["--street", "flop", "--pfa", "--facing", "check"])
    if "cbet_flop" not in flop_cols:
        col_fails.append("a flop c-bet filter dropped cbet_flop")
    if "vpip" in flop_cols:
        col_fails.append("a flop filter still leads with VPIP")
    raise_cols = columns_for(["--quick", "raise_cbet"])
    if "raise_cbet" not in raise_cols:
        col_fails.append("raise-cbet pack dropped raise_cbet")
    if "vpip" in raise_cols:
        col_fails.append("raise-cbet pack still leads with VPIP")
    for cols in (river_cols, flop_cols, columns_for(["--pot", "3bet"]),
                 raise_cols):
        for c in cols:
            if c not in BY_KEY:
                col_fails.append(f"columns_for named unknown stat {c}")
    print(f"report columns follow the spot  "
          f"{'yes' if not col_fails else 'NO'}")
    fails.extend(col_fails)

    # The action mix is a partition of the filtered rows. If a verb is
    # missing, every percentage shrinks and the spot looks tighter than
    # it is -- the same class of silent error a dropped parser line is.
    mix = actions_of(con, "1=1")
    mix_k = sum(r["k"] for r in mix["mix"])
    print(f"action mix covers every decision  {mix_k}/{mix['n']}")
    if mix["n"] and mix_k != mix["n"]:
        fails.append(f"action mix counted {mix_k} of {mix['n']} decisions")
    empty_mix = actions_of(con, "1=0")
    if empty_mix["mix"] or empty_mix["n"]:
        fails.append("action mix on an empty filter was not empty")

    # Hits / opportunities must be a rate: hits <= opps, and Hits/1000
    # uses player-hands not opportunities. Action profit on an empty
    # filter is empty; on everything, priced + unpriced = n, and a fold
    # is 0 so the priced count is at least the number of folds.
    summ = spot_summary(con, "1=1", [])
    if summ["hits"] > summ["opps"]:
        fails.append(f"hits {summ['hits']} exceed opportunities {summ['opps']}")
    if summ["hands"] and summ["per_1k"] > 1000:
        fails.append("hits/1000 is over 1000 -- the denominator is not hands")
    q_where, _, _ = build(["--quick", "cbet_flop"])
    qsum = spot_summary(con, q_where, ["--quick", "cbet_flop"])
    if qsum["label"] != "cbet flop":
        fails.append(f"--quick cbet_flop labelled {qsum['label']!r}")
    if qsum["hits"] > qsum["opps"]:
        fails.append("quick-filter hits exceed its chance")
    prof = action_profit_of(con, "1=0")
    if prof["priced"] or prof["n"]:
        fails.append("action profit on an empty filter was not empty")
    prof = action_profit_of(con, "1=1")
    if prof["priced"] + prof["unpriced"] != prof["n"]:
        fails.append("action profit priced+unpriced != n")
    folds = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE action='F'").fetchone()[0]
    if prof["n"] and prof["priced"] < folds:
        fails.append("action profit did not price every fold as 0")
    cp = call_profit_of(con, "1=0")
    if cp["priced"] or cp["n"]:
        fails.append("call profit on an empty filter was not empty")
    cp = call_profit_of(con, "1=1")
    if cp["priced"] + cp["unpriced"] != cp["n"]:
        fails.append("call profit priced+unpriced != n")
    # A raise is never a priced call -- that would be inventing EV.
    agg_w, _, _ = build(["--aggressive"])
    agg_cp = call_profit_of(con, agg_w)
    if agg_cp["priced"]:
        fails.append("call profit priced an aggressive action -- only "
                     "actual calls")
    calls = con.execute(
        "SELECT COUNT(*) FROM decisions d "
        "LEFT JOIN spots s ON s.hand_id = d.hand_id AND s.seat = d.seat "
        "WHERE d.action = 'C' AND d.to_call > 0 "
        "AND IFNULL(d.allin, 0) = 0 AND IFNULL(d.amount, 0) > 0 "
        "AND s.fmt IS NOT NULL AND s.fmt <> 'MTT' AND d.bb "
        "AND s.won IS NOT NULL"
    ).fetchone()[0]
    if cp["n"] and cp["priced"] != calls:
        fails.append(
            f"call profit priced {cp['priced']} of {calls} legal calls")
    sq_w, _, _ = build(["--after", "squeeze"])
    sq_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {sq_w}").fetchone()[0]
    # squeeze may be empty on a tiny corpus; if it has rows it must
    # narrow, and it must not be the same filter as --after raise.
    raise_after, _, _ = build(["--after", "raise"])
    raise_after_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {raise_after}"
    ).fetchone()[0]
    if sq_n == total:
        fails.append("--after squeeze selected every decision")
    if sq_n and sq_n == raise_after_n:
        fails.append("--after squeeze and --after raise selected the "
                     "same rows -- squeeze is any later, raise is first")
    faced = chain_of(con, "1=1", False)
    nxt = chain_of(con, "1=1", True)
    if faced["n"] and not faced["rows"]:
        fails.append("faced next on the whole table was empty")
    # Opening a Faced Next row must be a filter that builds and narrows.
    after_w, _, _ = build(["--after", "fold"])
    after_n = con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {after_w}").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    if after_n == 0 or after_n == total:
        fails.append("--after fold did not narrow")
    outs = outcomes_of(con, "1=1")
    if outs["bets"]:
        covered = sum(r["k"] for r in outs["rows"])
        if covered != outs["bets"]:
            fails.append(
                f"outcome block counted {covered} of {outs['bets']} bets")
        fold_w, _, _ = build(["--outcome", "fold-out"])
        fold_n = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {fold_w}").fetchone()[0]
        if fold_n == 0 or fold_n == total:
            fails.append("--outcome fold-out did not narrow")
    first_w, _, _ = build(["--first-in"])
    last_w, _, _ = build(["--last-raise"])
    size_w, _, _ = build(["--size", "m"])
    for name, w in (("--first-in", first_w), ("--last-raise", last_w),
                    ("--size m", size_w)):
        n = con.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {w}").fetchone()[0]
        if n == 0 or n == total:
            fails.append(f"{name} did not narrow")
    print(f"hits/opps and action profit     "
          f"{'yes' if not [f for f in fails if 'hits' in f or 'profit' in f or 'after fold' in f or 'faced next' in f] else 'NO'}")

    rel_fails = []
    for name, flags in SMART_REPORTS.items():
        spots = related_spots(list(flags))
        if not spots:
            rel_fails.append(f"{name}: no neighbouring spots")
            continue
        for r in spots:
            try:
                build(r["argv"])
            except SystemExit as e:
                rel_fails.append(f"{name} -> {r['name']}: {e}")
            if _canonical(r["argv"]) == _canonical(flags):
                rel_fails.append(f"{name} offered itself as related")
    flop_related = {r["name"] for r in related_spots(list(SMART_REPORTS["Flop c-bets"]))}
    if "Flop vs c-bet" not in flop_related:
        rel_fails.append("Flop c-bets does not offer Flop vs c-bet")
    if matching_report(["--hero"] + list(SMART_REPORTS["Flop c-bets"])) != "Flop c-bets":
        rel_fails.append("a report opened on hero is not recognised as itself")
    print(f"related spots build and differ  "
          f"{len(SMART_REPORTS) - len(rel_fails)}/{len(SMART_REPORTS)}")
    fails.extend(rel_fails)

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
    if "--presets" in argv or "--filters" in argv:
        for family, names in reports_by_family():
            print(f"\n[{family}]")
            known = reports()
            for name in names:
                flags = known[name]
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
    # `--hit` is click-stat on the command line: the same flag as
    # `--quick`, named the way the research brief names the click.
    argv = ["--quick" if a == "--hit" else a for a in argv]
    if "--from" in argv and "--filter" not in argv:
        argv[argv.index("--from")] = "--filter"
    compare_names = None
    if "--compare" in argv:
        i = argv.index("--compare")
        rest = argv[i + 1:]
        if len(rest) < 2:
            raise SystemExit(
                "--compare needs two report names -- "
                '--compare "Flop c-bets" "Flop vs c-bet"')
        compare_names = (rest[0], rest[1])
        argv = argv[:i] + rest[2:]
    mode = "--stats"
    for m in ("--stats", "--hands", "--results", "--graph", "--range",
              "--chart", "--related", "--faced-next", "--next-actions",
              "--bet-sizes", "--statistics"):
        if m in argv:
            mode = m
            argv = [a for a in argv if a != m]
    exclude_reg_vs_fish = "--exclude-reg-vs-fish" in argv
    argv = [a for a in argv if a != "--exclude-reg-vs-fish"]

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
    named = opt("--filter") if "--filter" in argv else None
    if named:
        argv = resolve_filter(named) + argv
        if preset is None:
            preset = named
    # A Faced Next / Next Actions row, applied. `--branch fold` is
    # `--after fold` unless this report is Next Actions.
    if "--branch" in argv:
        verb = opt("--branch")
        flag = "--then" if mode == "--next-actions" else "--after"
        argv += [flag, verb]

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
    columns = (opt("--show") or ",".join(columns_for(argv))).split(",")
    for c in columns:
        if c not in BY_KEY:
            raise SystemExit(f"unknown stat {c!r} -- see `stats.py --list`")
    min_n = int(opt("--min", "30"))

    if "--mark" in argv or "--unmark" in argv or "--note" in argv:
        hid = opt("--hand")
        if not hid:
            raise SystemExit("--mark / --unmark / --note need --hand ID")
        tag_raw = opt("--tag") if "--tag" in argv else None
        tags = [x.strip() for x in (tag_raw or "").split(",") if x.strip()]
        if "--mark" in argv:
            notes.mark(hid, tags)
            print(f"marked {hid}" + (f"  {', '.join(tags)}" if tags else ""))
        elif "--unmark" in argv:
            notes.unmark(hid, tags or None)
            print(f"unmarked {hid}" + (f"  {', '.join(tags)}" if tags else ""))
        if "--note" in argv:
            hands_con = None
            if Path(DB).exists() and Path(DB).stat().st_size > 0:
                hands_con = sqlite3.connect(DB)
                hands_con.row_factory = sqlite3.Row
            try:
                nid = notes.add(
                    opt("--note"),
                    player=opt("--player"),
                    site=opt("--site"),
                    hand_id=hid,
                    spot=opt("--spot"),
                    template=opt("--from-template"),
                    hands_con=hands_con)
            finally:
                if hands_con is not None:
                    hands_con.close()
            print(f"note #{nid}")
        if Path(DB).exists() and Path(DB).stat().st_size > 0:
            con = sqlite3.connect(DB)
            show_hand(con, hid)
            con.close()
        return 0

    if opt("--hand"):
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        show_hand(con, opt("--hand"))
        con.close()
        return 0

    other = opt("--versus")
    pinned = opt("--pin")
    if sum(x is not None for x in (pinned, other, compare_names)) > 1:
        raise SystemExit("--pin, --compare and --versus are three verbs -- "
                         "use one. --pin / --compare are the two-column "
                         "spot summary; --versus is the Holm table.")
    if compare_names or pinned or other is not None:
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        notes.attach(con)
        sessions.ensure(con)
        if cohort_spec is not None:
            _, header, _ = apply_cohort(con, cohort_spec, "1=1")
            show_cohort_banner(header)
        if compare_names or pinned:
            if compare_names:
                argv_a, argv_b = compare_sides(argv, compare_names[0],
                                               compare_names[1])
                name_a, name_b = compare_names
            else:
                argv_a, argv_b = pin_sides(argv, pinned)
                name_a, name_b = None, pinned
            want_sizes = mode == "--bet-sizes" or dim == "size"
            show_compare(compare_of(
                con, argv_a, argv_b, name_a, name_b,
                dim=dim, columns=columns, bet_sizes=want_sizes,
                packs=not dim and not want_sizes))
        else:
            try:
                other_argv, named_spot = resolve_filter(other), True
            except SystemExit:
                other_argv, named_spot = shlex.split(other), False
            if named_spot:
                other_argv = who_only(argv) + without_who(other_argv)
            show_versus(con, argv, other_argv, min_n,
                         only=set(columns) if opt("--show") else None)
        con.close()
        return 0

    stat_hit = stat_kind = stat_combo = None
    if mode == "--statistics":
        # Click-stat flags are the drill. `build` must not see them
        # or the grid becomes the 3bet hands and every rate is a lie.
        argv, stat_hit, stat_kind, stat_combo = take_stat_drill(argv)
    where, label, _parts = build(argv)
    if preset:
        label = f"{preset}: {label}"
    neighbours = related_spots(argv)
    if mode == "--related":
        # No database: this is a walk of the report tree, and connecting
        # just to print flags is how an empty `hands.db` gets created on
        # a machine that has not imported yet.
        show_related(neighbours, label)
        return 0
    if mode in ("--faced-next", "--next-actions", "--bet-sizes") \
            and not Path(DB).exists():
        raise SystemExit(f"no database at {DB} -- load some hands first")
    con = sqlite3.connect(DB)
    notes.attach(con)
    sessions.ensure(con)
    if cohort_spec is not None:
        where, header, label = apply_cohort(con, cohort_spec, where, label)
        show_cohort_banner(header)
    if mode == "--graph":
        show_graph(con, where, label, opt("--out", "graph.html"))
    elif mode == "--hands":
        show_hands(con, where, label, parts=_parts)
    elif mode == "--range":
        show_range(con, where, label, _parts)
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
    elif mode == "--faced-next":
        show_chain_report(con, where, label, argv, False)
    elif mode == "--next-actions":
        show_chain_report(con, where, label, argv, True)
    elif mode == "--bet-sizes":
        show_bet_sizes(con, where, label, argv, _parts)
    elif mode == "--statistics":
        show_statistics(con, where, label, argv, exclude=exclude_reg_vs_fish,
                        hit=stat_hit, kind=stat_kind or "action",
                        parts=_parts, combo=stat_combo)
    elif dim:
        show_report(con, where, label, dim, columns, min_n, argv=argv)
    else:
        show_stats(con, where, label, _parts, related=neighbours, argv=argv)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
