"""
The numbers at the table: a box beside each opponent while you play.

Everything here is a view over what the rest of the program already knows.
The stats are `stats.py`'s, the identities are `spots.identify`'s, the
classes are `players.classify`'s, and a hand reaches the database through
`importer.update`, the same road an import takes. A HUD that computed its
own VPIP would be a second answer to a question this project answers once,
and the two would drift apart the first time either changed.

What is new is only the part that has to be: finding the poker client's
windows, deciding which seat of a table is where on the screen, and
drawing a small window over each one that moves when the table does.

**Only on sites where a HUD is allowed and a label is a person** --
`sites.with_hud()`, which is ACR and PokerStars. PartyPoker has banned
HUDs since 2019, Ignition's seats change hands without the name changing,
and the app rooms arrive after the session.

**The numbers are shrunk towards the pool by default**, the hand count
beside them, because a rate on three chances printed bare is believed: the
profile view printed "fold to 3-bet 0.0%" off one observation for months.
The pool is the table's own game -- its site and stake -- with the player
taken out. Hand2Note prints the raw rate; `hud.json` with
`{"rates": "raw"}` does the same here. That is one of the five choices in
the HUD design left open for the user, and each of them is a setting in
`DEFAULTS` rather than a decision buried in the drawing code.

    python hud.py              run it: watch the hand histories, draw the boxes
    python hud.py --demo       a pretend table from your database, with the HUD on it
    python hud.py --edit       what the boxes, popups, colours and badges show
    python hud.py --layout     drag each box onto its seat, at a real table
    python hud.py --layout --demo   the same, over the pretend table
    python hud.py --print      the numbers for the tables you played last, as text
    python hud.py --popup NAME [SITE]
                               what a click on that player's box opens, as text
    python hud.py --windows    every window title, and which table each one matched
    python hud.py --check      the numbers against the stat engine, the layout,
                               the title matching and the watcher
"""

import json
import math
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

import importer
import sites
import stats

# Beside the program, as `app.py` has it: frozen, `__file__` is a directory
# PyInstaller deletes on the way out, and the database and the settings are
# the user's, not the program's.
HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)
DB = HERE / "hands.db"
SETTINGS = HERE / "hud.json"

# The five choices the design left open, as settings. `hud.json` beside the
# program overrides any of them; it is the user's and is not in the
# repository, for the same reason `stats.json` is not.
DEFAULTS = {
    # What a box shows, line by line: registry keys, so a stat saved in
    # `stats.json` can be put on the HUD by naming it.
    "lines": [["vpip", "pfr", "threebet"],
              ["fold_to_3bet", "cbet_flop", "fold_to_cbet"]],
    # "pooled": shrunk towards the table's game, the player taken out.
    # "raw": the player's own rate, as Hand2Note prints it.
    "rates": "pooled",
    # Below this many chances a number is drawn faint. Ten is where
    # `stats.SHRINK` says a player's own figure starts to outweigh the
    # pool's, so it is the point at which the number becomes theirs.
    "faint_below": stats.SHRINK,
    # How often the hand history folders are looked at, in seconds.
    "poll": 1.0,
    # Folders to watch besides the ones `sites.py` knows a client saves to.
    # A client can be told to save anywhere, and a HUD that looked only in
    # the usual places would draw nothing at such a table and say nothing
    # about why.
    "folders": [],
    # A table counts as one being played if its file changed this recently,
    # in minutes. The file's clock is this machine's, which is the point:
    # `played_at` is the site's clock and nothing says it agrees.
    "active": 30,
    # Where the seats lie on a table window, as an ellipse in fractions of
    # its width and height: centre x, centre y, half-width, half-height.
    # A first guess that has not been measured against either client; the
    # right numbers are whatever puts the boxes beside the names. Either one
    # ellipse for every table, or {"6": [...], "9": [...], "default": [...]}
    # by table size.
    "ellipse": [0.5, 0.47, 0.40, 0.36],
    # Where each box goes, by table size, as fractions of the window, slot
    # by slot clockwise from hero at the bottom: {"9": [[x, y], ...]}. Set by
    # dragging the boxes in `--layout`; a table size not here uses the
    # ellipse. Measured on the user's own clients, which is the only way
    # these numbers can be right.
    "seats": {},
    # Colour ranges: a number below "below" or above "above" is drawn in
    # that colour, in percent. Opinion, like `strength.WEAK`, and kept in
    # one place so disagreeing is a number changed. Never on a faint
    # number: a colour says "this is a finding", and four chances are not.
    "colours": {
        "vpip": {"below": [15, "#7fb2ff"], "above": [40, "#ff9b73"]},
        "threebet": {"above": [12, "#ff9b73"]},
        "fold_to_cbet": {"above": [60, "#ffd36e"]},
    },
    # Badges after the name. "note" marks a player you have written a note
    # on; "overfold" a player `query.py --overfolds` finds a REAL overfold
    # for, at some street and bet size; each rule a stat whose 95% interval
    # lies wholly above or below a line, in percent -- an interval, as
    # `players.classify` uses, so a badge is refused far more often than
    # given, and a player seen fold twice is not "folds to everything".
    "badges": {
        "note": "*",
        "overfold": "OF",
        "rules": [
            {"badge": "LOOSE", "stat": "vpip", "above": 40},
            {"badge": "NIT", "stat": "vpip", "below": 14},
            {"badge": "3B+", "stat": "threebet", "above": 10},
        ],
    },
    # What a click on a box opens: sections of stats, street by street, in
    # the order a hand is played. Any registry key goes in any section.
    "popup": [
        ["Preflop", ["vpip", "pfr", "rfi", "limp", "threebet", "fold_to_3bet",
                     "fourbet", "steal", "fold_to_steal", "bb_defend"]],
        ["Flop", ["cbet_flop", "fold_to_cbet", "raise_cbet", "checkraise_flop",
                  "donk_flop", "flop_agg"]],
        ["Turn", ["double_barrel", "fold_to_double_barrel", "delayed_cbet",
                  "probe_turn", "fold_to_turn_bet"]],
        ["River", ["triple_barrel", "fold_to_triple_barrel",
                   "fold_to_river_bet", "river_agg"]],
        ["Showdown", ["wtsd", "wsd", "wwsf"]],
    ],
    # A line on the box for the seat each player will have in the NEXT hand:
    # the button moves one seat, so between hands every position is known,
    # and the stats that matter for it are shown at that position -- the
    # coming button's steal rate, the coming blinds' fold to a steal. This
    # is as far as a hand history can take "stats that change with the
    # hand": both clients write a hand only once it is over, and knowing
    # who bet in the hand being played would mean reading the live table.
    # Empty turns the line off.
    "next_hand": {"UTG": ["rfi"], "HJ": ["rfi"], "CO": ["rfi", "steal"],
                  "BTN": ["rfi", "steal"], "SB": ["steal", "fold_to_steal"],
                  "BB": ["fold_to_steal", "bb_defend"]},
    # The stats the popup also splits by position. Opening is the one a
    # position changes most: a 15% opener and a 45% opener are the same
    # player, under the gun and on the button.
    "by_position": ["rfi", "vpip"],
}


def settings(path=None):
    """`DEFAULTS` with whatever `hud.json` overrides."""
    got = json.loads(json.dumps(DEFAULTS))
    try:
        got.update(json.loads(Path(path or SETTINGS).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return got


def problems(conf):
    """
    What is wrong with a set of HUD settings, in words; empty when nothing is.

    `hud.json` is written by hand as well as by the editor, and a stat key
    misspelled there would otherwise be a column that is silently never
    drawn -- the failure this project is built against, one file over.
    """
    out = []

    def known(key, where):
        if key in stats.EXPR_BY_KEY:
            out.append(f"{where}: {key} is an expression stat, which the HUD "
                       "does not draw yet")
        elif key not in stats.BY_KEY:
            out.append(f"{where}: no stat called {key!r}")

    def colour(c, where):
        if not (isinstance(c, str) and len(c) == 7 and c[0] == "#"
                and all(ch in "0123456789abcdefABCDEF" for ch in c[1:])):
            out.append(f"{where}: {c!r} is not a colour like #ff9b73")

    def percent(x, where):
        if not isinstance(x, (int, float)) or not 0 <= x <= 100:
            out.append(f"{where}: {x!r} is not a percentage")

    if not conf.get("lines") or not all(conf["lines"]):
        out.append("lines: a box needs at least one line, and no line empty")
    for i, line in enumerate(conf.get("lines") or ()):
        for key in line:
            known(key, f"line {i + 1}")
    for title, keys in conf.get("popup") or ():
        for key in keys:
            known(key, f"popup {title}")
    for key in conf.get("by_position") or ():
        known(key, "by position")
    from query import POSITIONS
    for pos, keys in (conf.get("next_hand") or {}).items():
        if pos not in POSITIONS:
            out.append(f"next hand: {pos!r} is not a position "
                       f"({', '.join(POSITIONS)})")
        for key in keys:
            known(key, f"next hand {pos}")
    if conf.get("rates") not in ("pooled", "raw"):
        out.append(f"rates: {conf.get('rates')!r} is neither 'pooled' nor 'raw'")
    if not isinstance(conf.get("faint_below"), int) or conf["faint_below"] < 0:
        out.append(f"faint_below: {conf.get('faint_below')!r} is not a count")
    for key, rule in (conf.get("colours") or {}).items():
        known(key, "colours")
        for side, pair in rule.items():
            if side not in ("below", "above") or len(pair) != 2:
                out.append(f"colours {key}: {side!r} must be below or above, "
                           "with a percentage and a colour")
                continue
            percent(pair[0], f"colours {key} {side}")
            colour(pair[1], f"colours {key} {side}")
    badges = conf.get("badges") or {}
    for i, rule in enumerate(badges.get("rules") or ()):
        where = f"badge {rule.get('badge', i + 1)}"
        known(rule.get("stat"), where)
        sides = [k for k in ("above", "below") if k in rule]
        if len(sides) != 1:
            out.append(f"{where}: needs exactly one of above or below")
        for k in sides:
            percent(rule[k], where)
        if not rule.get("badge"):
            out.append(f"{where}: has no text to show")
    for size, spots in (conf.get("seats") or {}).items():
        if not str(size).isdigit() or len(spots) != int(size) or not all(
                len(p) == 2 and all(isinstance(v, (int, float)) and -0.5 <= v <= 1.5
                                    for v in p) for p in spots):
            out.append(f"seats {size}: needs one [x, y] per seat, as fractions "
                       "of the window")
    folders = conf.get("folders")
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        out.append("folders: needs a list of folder names")
    ell = conf.get("ellipse")
    for size, e in (ell.items() if isinstance(ell, dict) else [("", ell)]):
        if not (isinstance(e, (list, tuple)) and len(e) == 4):
            out.append(f"ellipse {size}".strip() + ": needs four numbers")
    return out


def save(conf, path=None):
    """Write the settings that differ from `DEFAULTS` to `hud.json`."""
    bad = problems(conf)
    if bad:
        raise ValueError("; ".join(bad))
    mine = {k: v for k, v in conf.items() if DEFAULTS.get(k) != v}
    Path(path or SETTINGS).write_text(json.dumps(mine, indent=2) + "\n",
                                      encoding="utf-8")
    return mine


def colour_of(conf, key, shown, n):
    """The colour a figure is drawn in under `conf["colours"]`, or None."""
    rule = (conf.get("colours") or {}).get(key)
    if not rule or shown is None or n < conf["faint_below"]:
        return None
    pct = 100 * shown
    if "below" in rule and pct < rule["below"][0]:
        return rule["below"][1]
    if "above" in rule and pct > rule["above"][0]:
        return rule["above"][1]
    return None


def badges_of(con, site, player, conf, got):
    """
    The badges a player earns, each with the sentence that earned it.

    `got` is `numbers` for at least the rule stats; a rule fires only when
    the 95% interval on the player's own count lies wholly past its line.
    The overfold badge is `query.overfolds_of` on the player's own
    decisions, and only its REAL rows -- Holm-corrected, thirty decisions
    or more -- because the design promised that badge only where the
    tool calls it, never from a rate read by eye.
    """
    import notes
    import query
    b = conf.get("badges") or {}
    out = []
    if b.get("note") and notes.note_of(con, site, player):
        out.append((b["note"], "you have a note on this player"))
    for rule in b.get("rules") or ():
        n, k = got.get(rule["stat"], (0, 0))[:2]
        # Under the faint line no badge, whatever the interval says: one
        # 3-bet in one chance has a Wilson floor of 21%, which clears a 10%
        # line, and a badge on one hand is the colour-on-a-faint-number
        # mistake again. The first version gave exactly that badge.
        if n < conf["faint_below"]:
            continue
        _p, lo, hi = stats.wilson(k, n)
        label = stats.BY_KEY[rule["stat"]].label
        if "above" in rule and 100 * lo > rule["above"]:
            out.append((rule["badge"], f"{label} surely above {rule['above']}%: "
                        f"{k}/{n}, 95% from {round(100 * lo)}%"))
        if "below" in rule and 100 * hi < rule["below"]:
            out.append((rule["badge"], f"{label} surely below {rule['below']}%: "
                        f"{k}/{n}, 95% up to {round(100 * hi)}%"))
    if b.get("overfold"):
        where = f"player = {query.q(player)} AND site = {query.q(site)}"
        real = [r for r in query.overfolds_of(con, where)
                if r["real"] and r["n"] >= 30]
        if real:
            r = real[0]
            out.append((b["overfold"], f"overfolds the {r['street']} to a "
                        f"{r['size']} bet: {r['fold']:.0f}% of {r['n']} against "
                        f"a bar of {r['bar']:.0f}%"))
    return out


def next_positions(con, hand_id):
    """
    The position each seat of this hand will have in the next one.

    The button moves one seat clockwise, and seat numbers rise clockwise in
    both clients, so each seat takes the position the seat before it had --
    the old small blind deals, the old button posts nothing and acts last
    but one. Rotating the labels rather than recomputing them keeps the
    tables where eight seats share six names exactly as the next history
    will write them. A player who sits down or stands up between hands
    moves the blinds in ways the rule cannot know; measured on the corpus,
    the rule is right on 177 of 178 consecutive hands with the same seats.
    """
    seats = dict(con.execute("SELECT seat, position FROM spots WHERE hand_id=?",
                             (hand_id,)).fetchall())
    order = sorted(seats)
    return {seat: seats[order[i - 1]] for i, seat in enumerate(order)}


def next_hand_cells(con, t, player, conf, pool_by_pos):
    """
    The next-hand line of one player's box: [(text, faint, colour)], led
    by the position, and the numbers behind it for the check.

    Each stat at that position only, shrunk towards the game's figure at
    that position with the player taken out -- as the popup's positions
    are, and for the same reason.
    """
    pos = t["next"].get(t["seat_of"][player])
    keys = [k for k in (conf.get("next_hand") or {}).get(pos, ())
            if k in stats.BY_KEY]
    if not keys:
        return None, {}
    gw, gp = game_where(t["site"], t["fmt"], t["bb"])
    cells, got = [(pos, False, BADGE)], {}
    for key in keys:
        s = stats.BY_KEY[key]
        n, k = stats.rates_by(con, s, "position", "player=? AND site=?",
                              (player, t["site"])).get(pos, (0, 0))
        at = stats.rates_by(con, s, "position", gw + " AND player=?",
                            gp + (player,)).get(pos, (0, 0))
        pn = pool_by_pos[key].get(pos, (0, 0))[0] - at[0]
        pk = pool_by_pos[key].get(pos, (0, 0))[1] - at[1]
        text, faint, _raw, _lo, _hi, shown = cell(
            n, k, pk / pn if pn else None, conf["rates"], conf["faint_below"])
        cells.append((text, faint, colour_of(conf, key, shown, n)))
        got[key] = (n, k, pk / pn if pn else None, shown)
    return cells, got


def rule_keys(conf):
    """The stats the badge rules read, so they are counted with the box's."""
    return [r["stat"] for r in (conf.get("badges") or {}).get("rules") or ()
            if r.get("stat") in stats.BY_KEY]


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

def table_now(con, site, table_id):
    """
    Who was in the last hand dealt at this table, and the game it was.

    The last hand and not the last few: a seat that stood up two hands ago
    is somebody else's now, and the HUD would otherwise draw the departed.
    Seats come from `spots`, which already leaves out a player who was
    listed at the table and sitting out.
    """
    h = con.execute(
        "SELECT hand_id, fmt, bb, max_seats, n_players FROM hands "
        "WHERE site=? AND table_id=? ORDER BY played_at DESC, hand_id DESC "
        "LIMIT 1", (site, table_id)).fetchone()
    if h is None:
        return None
    seats = [{"seat": r[0], "player": r[1], "is_hero": bool(r[2])}
             for r in con.execute(
                 "SELECT seat, player, is_hero FROM spots WHERE hand_id=? "
                 "ORDER BY seat", (h[0],))]
    return {"site": site, "table_id": table_id, "hand_id": h[0],
            "fmt": h[1], "bb": h[2],
            # A history that does not say how many seats the table has is
            # sized by the highest seat number anybody sat in.
            "max_seats": h[3] or max([s["seat"] for s in seats] + [h[4] or 6]),
            "seats": seats}


def game_where(site, fmt, bb):
    """The pool a player at this table is read against, with them still in."""
    return "site=? AND fmt=? AND bb=? AND is_hero=0", (site, fmt, bb)


def numbers(con, site, fmt, bb, player, keys, mode="pooled", pool=None):
    """
    One player's figures for these stats: {key: (n, k, raw, prior, shown)}.

    `n` and `k` are over every hand of theirs on the site, which is what the
    profile view counts. `prior` is the table's game with this player taken
    out -- `pool`, the game's totals, is passed in so that a table of six
    players costs one pass over the game and not six. `shown` is what goes
    in the box: the shrunk rate, or the raw one if `mode` is "raw".
    """
    want = [stats.BY_KEY[k] for k in keys if k in stats.BY_KEY]
    mine = stats.rates(con, "player=? AND site=?", (player, site), want)
    gw, gp = game_where(site, fmt, bb)
    if pool is None:
        pool = stats.rates(con, gw, gp, want)
    at_game = stats.rates(con, gw + " AND player=?", gp + (player,), want)

    out = {}
    for s in want:
        if s.key in mine:
            n, k = mine[s.key]
            pn = pool.get(s.key, (0, 0))[0] - at_game.get(s.key, (0, 0))[0]
            pk = pool.get(s.key, (0, 0))[1] - at_game.get(s.key, (0, 0))[1]
        else:
            # Spots-sourced stats cannot join the one-pass count; asked
            # singly, exactly as the profile view asks them.
            n, k = stats.rate(con, s, "player=? AND site=?", (player, site))[:2]
            pn, pk = stats.rate(con, s, gw + " AND player<>?", gp + (player,))[:2]
        raw = k / n if n else None
        prior = pk / pn if pn else None
        shown = raw if mode == "raw" else stats.shrunk(k, n, prior)
        out[s.key] = (n, k, raw, prior, shown if shown is not None else raw)
    return out


def view(con, site, table_id, conf=None):
    """
    Everything one table's boxes need, worked out away from the window.

    Returns the table and, per opponent seat, the lines of the box: each a
    list of (text, faint, colour), the first the name and the badges. Hero
    gets no box.
    """
    conf = conf or settings()
    t = table_now(con, site, table_id)
    if t is None:
        return None
    keys = list(dict.fromkeys([k for line in conf["lines"] for k in line]
                              + rule_keys(conf)))
    want = [stats.BY_KEY[k] for k in keys if k in stats.BY_KEY]
    gw, gp = game_where(site, t["fmt"], t["bb"])
    pool = stats.rates(con, gw, gp, want)
    klass = {r[0]: r[1] for r in con.execute(
        "SELECT player, class FROM players WHERE site=? AND player IN "
        f"({','.join('?' * len(t['seats']))})",
        (site,) + tuple(s["player"] for s in t["seats"]))}
    hands = {r[0]: r[1] for r in con.execute(
        "SELECT player, COUNT(*) FROM spots WHERE site=? AND player IN "
        f"({','.join('?' * len(t['seats']))}) GROUP BY player",
        (site,) + tuple(s["player"] for s in t["seats"]))}

    t["next"] = next_positions(con, t["hand_id"])
    t["seat_of"] = {s["player"]: s["seat"] for s in t["seats"]}
    nh_keys = {k for keys in (conf.get("next_hand") or {}).values()
               for k in keys if k in stats.BY_KEY}
    pool_by_pos = {k: stats.rates_by(con, stats.BY_KEY[k], "position", gw, gp)
                   for k in nh_keys}

    boxes = {}
    for s in t["seats"]:
        if s["is_hero"] or not s["player"]:
            continue
        got = numbers(con, site, t["fmt"], t["bb"], s["player"], keys,
                      conf["rates"], pool)
        head = f"{s['player'][:12]}  {hands.get(s['player'], 0)}h"
        # The class only when there is one. "unknown" is the usual answer
        # and printing it on every box would teach the eye to skip the
        # place where a real one appears.
        if klass.get(s["player"]) in ("reg", "fish"):
            head += "  " + klass[s["player"]].upper()
        badges = badges_of(con, site, s["player"], conf, got)
        lines = [[(head, False, None)]
                 + [(badge, False, BADGE) for badge, _why in badges]]
        for line in conf["lines"]:
            cells = []
            for key in line:
                n, _k, _raw, _prior, shown = got.get(key, (0, 0, None, None, None))
                # A dash, never a zero, when there has been no chance: "0"
                # says they never do it, and they have never been asked.
                text = "-" if not n or shown is None else f"{round(100 * shown)}"
                cells.append((text, n < conf["faint_below"],
                              colour_of(conf, key, shown, n)))
            lines.append(cells)
        nxt, nxt_got = next_hand_cells(con, t, s["player"], conf, pool_by_pos)
        if nxt:
            lines.append(nxt)
        boxes[s["seat"]] = {"player": s["player"], "lines": lines,
                            "numbers": got, "badges": badges,
                            "next": nxt_got}
    return {"table": t, "boxes": boxes}


def as_text(v):
    """A table's boxes as text, for `--print` and for reading a check."""
    t = v["table"]
    out = [f"{t['site']}  {t['table_id']}  {t['fmt']} bb={t['bb']}  "
           f"{t['max_seats']}-max  after hand {t['hand_id']}"]
    for seat, box in sorted(v["boxes"].items()):
        rows = []
        for i, line in enumerate(box["lines"]):
            # Coloured numbers are marked with a star; on the screen they
            # are the colour, and here there is none to show.
            rows.append((" " if i == 0 else " / ").join(
                f"({text})" if faint else
                text + ("*" if colour and text[:1].isdigit() else "")
                for text, faint, colour in line))
        out.append(f"  seat {seat:<2} " + "\n           ".join(rows))
    return "\n".join(out)


def cell(n, k, prior, mode, faint_below):
    """
    One figure as the HUD prints it: (text, faint, raw, lo, hi, shown).

    `shown` is the shrunk rate, or the raw one in raw mode; the interval is
    always the raw count's, because that is the count it belongs to. No
    chance at all is a dash, never a zero.
    """
    if not n:
        return "-", True, None, None, None, None
    raw = k / n
    _p, lo, hi = stats.wilson(k, n)
    shown = raw if mode == "raw" else stats.shrunk(k, n, prior)
    shown = raw if shown is None else shown
    return f"{round(100 * shown)}", n < faint_below, raw, lo, hi, shown


def popup_view(con, site, fmt, bb, player, conf=None):
    """
    Everything one player's popup holds, worked out away from the window.

    The sections of `conf["popup"]`, each row a stat with its count, its
    interval and the pool it is read against, then the stats of
    `conf["by_position"]` split by position, then the note the user wrote
    on this player, if any. The pool is the table's game with the player
    taken out, the same as the box beside the seat; a popup that read
    against a different pool would show the same stat as two numbers.
    """
    conf = conf or settings()
    mode, faint = conf["rates"], conf["faint_below"]
    sections = []
    for title, keys in conf["popup"]:
        keys = [k for k in keys if k in stats.BY_KEY]
        got = numbers(con, site, fmt, bb, player, keys, mode)
        rows = []
        for key in keys:
            n, k, _raw, prior, _shown = got[key]
            text, dim, raw, lo, hi, shown = cell(n, k, prior, mode, faint)
            rows.append({"key": key, "label": stats.BY_KEY[key].label,
                         "n": n, "k": k, "text": text, "faint": dim,
                         "colour": colour_of(conf, key, shown, n),
                         "raw": raw, "lo": lo, "hi": hi, "prior": prior,
                         "shown": shown})
        sections.append((title, rows))

    # By position. The pool is split the same way: a button open is read
    # against the pool's button opens, never its opens everywhere, which
    # would pull every late-position number down and every early one up.
    from query import POSITIONS
    gw, gp = game_where(site, fmt, bb)
    positions = []
    for key in conf["by_position"]:
        if key not in stats.BY_KEY:
            continue
        s = stats.BY_KEY[key]
        mine = stats.rates_by(con, s, "position", "player=? AND site=?",
                              (player, site))
        pool = stats.rates_by(con, s, "position", gw + " AND player<>?",
                              gp + (player,))
        cells = []
        for pos in POSITIONS:
            n, k = mine.get(pos, (0, 0))
            pn, pk = pool.get(pos, (0, 0))
            text, dim, *_rest = cell(n, k, pk / pn if pn else None, mode, faint)
            cells.append((pos, n, k, text, dim))
        positions.append((s.label, cells))

    import notes
    hands = con.execute("SELECT COUNT(*) FROM spots WHERE site=? AND player=?",
                        (site, player)).fetchone()[0]
    klass = con.execute("SELECT class FROM players WHERE site=? AND player=?",
                        (site, player)).fetchone()
    got = numbers(con, site, fmt, bb, player, rule_keys(conf), mode)
    return {"player": player, "site": site, "fmt": fmt, "bb": bb,
            "badges": badges_of(con, site, player, conf, got),
            "hands": hands, "class": klass[0] if klass else None,
            "note": notes.note_of(con, site, player),
            "sections": sections, "positions": positions, "mode": mode}


def popup_for(con, player, site=None):
    """
    A player's popup at the game they have played most, for `--popup`.

    A name is a person on one site only -- the corpus has a "Player4" on
    both -- so a site narrows it, and without one the busier is taken.
    """
    hud_sites = (site,) if site else sites.with_hud()
    row = con.execute(
        "SELECT site, fmt, bb FROM spots WHERE player=? AND site IN "
        f"({','.join('?' * len(hud_sites))}) GROUP BY site, fmt, bb "
        "ORDER BY COUNT(*) DESC LIMIT 1", (player,) + hud_sites).fetchone()
    return popup_view(con, *row, player) if row else None


def interval(lo, hi):
    return f"{round(100 * lo)}-{round(100 * hi)}" if lo is not None else ""


def popup_text(p):
    """A popup as text, for `--popup` and for reading a check."""
    out = [f"{p['player']}  {p['site']}  {p['hands']} hands"
           + (f"  {p['class'].upper()}" if p["class"] in ("reg", "fish") else ""),
           f"  rates {'shrunk towards' if p['mode'] != 'raw' else 'raw; pool is'}"
           f" the {p['fmt']} bb={p['bb']} game without them; "
           "(brackets) under ten chances"]
    if p["note"]:
        out.append(f"  note: {p['note']}")
    for badge, why in p["badges"]:
        out.append(f"  {badge}: {why}")
    for title, rows in p["sections"]:
        out.append(f"  {title}")
        for r in rows:
            text = f"({r['text']})" if r["faint"] else r["text"]
            pool = f"{round(100 * r['prior'])}" if r["prior"] is not None else "-"
            out.append(f"    {r['label']:24} {text:>5}  n={r['n']:<5} "
                       f"{interval(r['lo'], r['hi']):>7}  pool {pool}")
    for label, cells in p["positions"]:
        out.append(f"  {label} by position  "
                   + "  ".join(f"{pos} {f'({t})' if dim else t}"
                               for pos, _n, _k, t, dim in cells))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Where each seat is on the screen
# ---------------------------------------------------------------------------

def slot(seat, hero_seat, max_seats):
    """
    Where a seat sits counting clockwise from the bottom of the table, 0 up.

    Both clients put hero at the bottom when seat preference is on, which
    is how almost everybody plays, so the table is rotated to match: hero
    is slot 0 and the others follow clockwise in seat order. Without a
    hero in the hand, seat 1 takes the bottom.
    """
    anchor = hero_seat if hero_seat else 1
    return (seat - anchor) % max_seats


def place(index, max_seats, conf=None):
    """
    The point on the table window, as fractions of its width and height,
    where the box for this slot goes.

    Where the user has placed the boxes for this table size, there; else
    on the settings' ellipse for this size, clockwise from the bottom
    centre.
    """
    conf = conf or DEFAULTS
    placed = (conf.get("seats") or {}).get(str(max_seats))
    if placed and len(placed) == max_seats:
        return tuple(placed[index])
    ell = conf.get("ellipse") or DEFAULTS["ellipse"]
    if isinstance(ell, dict):
        ell = ell.get(str(max_seats)) or ell.get("default") or DEFAULTS["ellipse"]
    cx, cy, rx, ry = ell
    angle = math.pi / 2 + 2 * math.pi * index / max_seats
    return cx + rx * math.cos(angle), cy + ry * math.sin(angle)


# ---------------------------------------------------------------------------
# Finding the tables
# ---------------------------------------------------------------------------

def windows():
    """
    Every visible top-level window on this machine: (title, x, y, w, h).

    Windows only, through the window list Windows keeps itself. Minimised
    windows are left out, and so the boxes over a table go when it does.
    Elsewhere there are no poker clients to find, and the demo supplies
    its own windows instead.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    def one(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((buf.value, r.left, r.top, r.right - r.left,
                      r.bottom - r.top))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proc(one), 0)
    return found


def match(title, tables):
    """
    The table a window title belongs to, or None.

    Both clients put the table's name in the title, and both write the
    same name into the history -- "Halley II", "Miramar (Cap)" -- so the
    title is matched against the tables being played rather than parsed
    by a pattern per client that the next client update would break. The
    name has to start the title, or follow "Table '", and be followed by
    something that is not more name: "Halley" must not claim the window
    of "Halley II". The longest name that fits wins.
    """
    best = None
    for site, table_id in tables:
        for start in (title, title[len("Table '"):] if title.startswith("Table '") else None):
            if start is None or not start.startswith(table_id):
                continue
            rest = start[len(table_id):]
            if rest and rest[0] not in " -',(|":
                continue
            if best is None or len(table_id) > len(best[1]):
                best = (site, table_id)
    return best


# ---------------------------------------------------------------------------
# Watching the hand history folders
# ---------------------------------------------------------------------------

class Watcher:
    """
    New hands in, tables' boxes worked out again, away from the window.

    Each step looks at every history file in the HUD sites' folders and
    reads the ones that changed size or time since the last look -- the
    whole file, because `importer.load` skips the hands it already has and
    a history file is a few hundred hands at most. The hands that were new
    go through `importer.update`, and every table whose file moved is
    worked out again and handed to the window through `out`.
    """

    def __init__(self, db_path=DB, folders=None, out=None, conf=None):
        self.db_path = db_path
        self.conf = conf or settings()
        self.folders = folders
        self.out = out if out is not None else queue.Queue()
        self.seen = {}
        self.active = {}            # (site, table_id) -> when its file last moved
        self.first = True
        # Set when the settings change: every active table is worked out
        # again on the next look, whether or not a file moved.
        self.redraw = False

    def roots(self):
        """Every folder this watcher looks in, whether or not it exists."""
        if self.folders is not None:
            return [Path(f) for f in self.folders]
        import os
        return ([Path(os.path.expandvars(p)) for key in sites.with_hud()
                 for p in sites.of(key).places]
                + [Path(f) for f in self.conf.get("folders") or ()])

    def files(self):
        for root in self.roots():
            if root.is_dir():
                yield from root.rglob("*.txt")
            elif root.is_file():
                yield root

    def step(self):
        """One look at the folders. Returns the tables whose boxes changed."""
        now = time.time()
        changed = []
        for f in self.files():
            try:
                st = f.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            if self.seen.get(f) == sig:
                continue
            self.seen[f] = sig
            # On the first look only the files of the last half hour count:
            # a folder holds years of history and none of it is a table
            # being played now.
            if self.first and now - st.st_mtime > 60 * self.conf["active"]:
                continue
            changed.append(f)
        self.first = False
        if not changed and not self.redraw:
            return []
        self.redraw = False

        if changed:
            got = importer.load(changed, self.db_path)
            if got["ids"]:
                importer.update(got["ids"], self.db_path)
        con = sqlite3.connect(self.db_path)
        hud_sites = sites.with_hud()
        for f in changed:
            for site, table_id in con.execute(
                    "SELECT DISTINCT site, table_id FROM hands WHERE source=?",
                    (f.name,)):
                if site in hud_sites:
                    self.active[(site, table_id)] = now
        stale = [t for t, when in self.active.items()
                 if now - when > 60 * self.conf["active"]]
        for t in stale:
            del self.active[t]
        fresh = {}
        for site, table_id in self.active:
            v = view(con, site, table_id, self.conf)
            if v is not None:
                fresh[(site, table_id)] = v
        con.close()
        self.out.put(fresh)
        return list(fresh)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.step()
            except Exception as e:          # the window must outlive a bad file
                import diag
                diag.event("hud watcher failed", error=repr(e))
            stop.wait(self.conf["poll"])


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

BG, INK, FAINT = "#14161a", "#e8e8e8", "#7b8088"
BADGE = "#c9a2ff"


class Overlay:
    """
    A small always-on-top window per opponent, kept beside their seat.

    Ten times a second it reads where every table window is and moves the
    boxes there, so a table dragged across the screen takes its numbers
    with it. The numbers themselves arrive from the watcher's thread
    through a queue; Tk is only ever touched from its own thread.
    """

    def __init__(self, root, source, conf=None, db_path=DB, layout=False,
                 settings_path=None):
        self.root = root
        self.source = source            # () -> [(title, x, y, w, h)]
        self.conf = conf or settings()
        self.db_path = db_path
        # In layout mode a box is dragged rather than clicked, and where it
        # is dropped is saved for its table size; see `--layout`.
        self.layout = layout
        self.settings_path = settings_path
        self.dragging = None            # (key, dx, dy) of the box held
        self.where = {}                 # key -> (window x, y, w, h, slot, max_seats)
        self.views = {}
        self.boxes = {}                 # (site, table_id, seat) -> (Toplevel, Frame)
        self.who = {}                   # the same key -> (site, fmt, bb, player)
        self.inbox = queue.Queue()
        # A popup's numbers are a few passes over the player's game, too slow
        # to ask on Tk's thread without the boxes stopping while they come.
        # Asked on a thread of their own, with their own connection, and
        # drawn when they arrive -- unless the click has been taken back.
        self.asks = queue.Queue()
        self.answers = queue.Queue()
        self.popup = None               # (key, Toplevel) of the one open
        threading.Thread(target=self.answer, daemon=True).start()

    def answer(self):
        con = None
        while True:
            key, who = self.asks.get()
            try:
                con = con or sqlite3.connect(self.db_path)
                self.answers.put((key, popup_view(con, *who, conf=self.conf)))
            except Exception as e:      # a popup that fails must not stop the HUD
                import diag
                diag.event("hud popup failed", error=repr(e))

    def toggle(self, key):
        """A click on a box opens its popup; a second click, or another box's, closes it."""
        was = self.popup[0] if self.popup else None
        self.close()
        if key != was and key in self.who:
            self.popup = (key, None)
            self.asks.put((key, self.who[key]))

    def close(self, _event=None):
        if self.popup and self.popup[1] is not None:
            self.popup[1].destroy()
        self.popup = None

    def press(self, key, event):
        if not self.layout:
            return self.toggle(key)
        top = self.boxes[key][0]
        self.dragging = (key, event.x_root - top.winfo_rootx(),
                         event.y_root - top.winfo_rooty())

    def motion(self, key, event):
        if self.dragging and self.dragging[0] == key:
            _k, dx, dy = self.dragging
            self.boxes[key][0].geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def release(self, key, _event=None):
        """
        A box dropped in layout mode: its place, for every table this size.

        Stored as the centre of the box in fractions of the table window, by
        slot -- counted from hero -- so it holds when the window is resized
        and whichever seat hero is given. The other slots of that size are
        written where they are now drawn, so the first drag turns the
        ellipse into a layout that can then be corrected one box at a time.
        """
        if not (self.dragging and self.dragging[0] == key):
            return
        self.dragging = None
        top = self.boxes[key][0]
        x, y, w, h, index, size = self.where[key]
        cx = top.winfo_rootx() + top.winfo_width() / 2
        cy = top.winfo_rooty() + top.winfo_height() / 2
        spots = [list(place(i, size, self.conf)) for i in range(size)]
        spots[index] = [round((cx - x) / w, 4), round((cy - y) / h, 4)]
        self.conf.setdefault("seats", {})[str(size)] = spots
        save(self.conf, self.settings_path)

    def tick(self):
        try:
            while True:
                self.views = self.inbox.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                key, p = self.answers.get_nowait()
                if self.popup == (key, None) and key in self.boxes:
                    self.popup = (key, self.show_popup(key, p))
        except queue.Empty:
            pass
        placed = set()
        for title, x, y, w, h in self.source():
            hit = match(title, self.views)
            if not hit:
                continue
            v = self.views[hit]
            t = v["table"]
            hero = next((s["seat"] for s in t["seats"] if s["is_hero"]), None)
            for seat, box in v["boxes"].items():
                fx, fy = place(slot(seat, hero, t["max_seats"]), t["max_seats"],
                               self.conf)
                key = hit + (seat,)
                self.who[key] = (t["site"], t["fmt"], t["bb"], box["player"])
                self.where[key] = (x, y, w, h, slot(seat, hero, t["max_seats"]),
                                   t["max_seats"])
                self.draw(key, box, int(x + fx * w), int(y + fy * h))
                placed.add(key)
        for key in [k for k in self.boxes if k not in placed]:
            self.boxes.pop(key)[0].destroy()
            if self.popup and self.popup[0] == key:
                self.close()
        self.root.after(100, self.tick)

    def draw(self, key, box, x, y):
        import tkinter as tk
        if key not in self.boxes:
            top = tk.Toplevel(self.root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            try:
                top.attributes("-alpha", 0.88)
            except tk.TclError:
                pass
            frame = tk.Frame(top, bg=BG, padx=4, pady=2,
                             cursor="fleur" if self.layout else "hand2")
            frame.pack()
            self.boxes[key] = (top, frame)
        top, frame = self.boxes[key]
        shape = [list(line) for line in box["lines"]]
        if getattr(frame, "shape", None) != shape:
            for child in frame.winfo_children():
                child.destroy()
            for n, line in enumerate(shape):
                row = tk.Frame(frame, bg=BG)
                row.pack(anchor="w")
                for i, (text, faint, colour) in enumerate(line):
                    # The name and its badges are spaced; numbers are
                    # slashed, as every HUD writes them.
                    # ...except after the next hand's position, which
                    # leads its line as a name leads the first.
                    if i and n and not (i == 1 and line[0][2] == BADGE):
                        tk.Label(row, text="/", bg=BG, fg=FAINT,
                                 font=("Consolas", 9)).pack(side="left")
                    tk.Label(row, text=text, bg=BG,
                             fg=FAINT if faint else colour or INK,
                             font=("Consolas", 9, "bold") if colour else ("Consolas", 9)
                             ).pack(side="left", padx=(4, 0) if i and (
                                 not n or (i == 1 and line[0][2] == BADGE)) else 0)
            # Every label takes the click too, or only the box's thin border
            # would open it.
            for w in [frame] + frame.winfo_children() + [
                    label for row in frame.winfo_children()
                    for label in row.winfo_children()]:
                w.bind("<ButtonPress-1>", lambda e, k=key: self.press(k, e))
                w.bind("<B1-Motion>", lambda e, k=key: self.motion(k, e))
                w.bind("<ButtonRelease-1>", lambda e, k=key: self.release(k, e))
            frame.shape = shape
        if self.dragging and self.dragging[0] == key:
            return
        top.update_idletasks()
        top.geometry(f"+{x - top.winfo_width() // 2}+{y - top.winfo_height() // 2}")

    def show_popup(self, key, p):
        """The popup beside its box: a table per street, then by position."""
        import tkinter as tk
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=BG, padx=8, pady=6, cursor="hand2")
        frame.pack()
        font, bold = ("Consolas", 9), ("Consolas", 9, "bold")

        def line(r, *cells):
            for c, (text, fg, f) in enumerate(cells):
                tk.Label(frame, text=text, bg=BG, fg=fg, font=f,
                         anchor="e" if c else "w").grid(row=r, column=c,
                                                        sticky="e" if c else "w",
                                                        padx=(0, 8))
        r = 0
        head = f"{p['player']}   {p['hands']} hands"
        if p["class"] in ("reg", "fish"):
            head += "   " + p["class"].upper()
        line(r, (head, INK, bold)); r += 1
        line(r, ("raw rates" if p["mode"] == "raw" else
                 "shrunk towards the pool", FAINT, font),
             ("%", FAINT, font), ("n", FAINT, font), ("95%", FAINT, font),
             ("pool", FAINT, font)); r += 1
        if p["note"]:
            line(r, ("note: " + p["note"][:60], INK, font)); r += 1
        for badge, why in p["badges"]:
            tk.Label(frame, text=f"{badge}  {why}", bg=BG, fg=BADGE, font=font,
                     anchor="w").grid(row=r, column=0, columnspan=5, sticky="w")
            r += 1
        for title, rows in p["sections"]:
            line(r, (title, INK, bold)); r += 1
            for row in rows:
                fg = FAINT if row["faint"] else INK
                pool = f"{round(100 * row['prior'])}" if row["prior"] is not None else "-"
                line(r, ("  " + row["label"], fg, font),
                     (row["text"], row["colour"] or fg, bold),
                     (str(row["n"]), FAINT, font),
                     (interval(row["lo"], row["hi"]), FAINT, font),
                     (pool, FAINT, font)); r += 1
        for label, cells in p["positions"]:
            text = "  ".join(f"{pos} {t}" for pos, _n, _k, t, _dim in cells)
            line(r, (label + " by position", INK, bold)); r += 1
            tk.Label(frame, text="  " + text, bg=BG, fg=INK, font=font).grid(
                row=r, column=0, columnspan=5, sticky="w"); r += 1
        for w in [frame] + frame.winfo_children():
            w.bind("<Button-1>", self.close)
        top.bind("<Escape>", self.close)

        # Beside the box, on whichever side of it the screen has room.
        box = self.boxes[key][0]
        top.update_idletasks()
        x = box.winfo_rootx() + box.winfo_width() + 6
        if x + top.winfo_width() > top.winfo_screenwidth():
            x = box.winfo_rootx() - top.winfo_width() - 6
        y = min(box.winfo_rooty(),
                top.winfo_screenheight() - top.winfo_height() - 40)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        return top


def dpi_aware():
    """
    Make Windows report real pixels to this process.

    At 125% or 150% scaling Windows tells a program that has not declared
    itself aware a scaled-down picture of the screen, and Tk and the window
    list then disagree about where everything is: the boxes land a fifth of
    the way off. Declared before Tk starts, both see the same pixels.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Clients that look for HUDs on the machine and restrict the account that
# runs one, matched in lower case against window titles and process names.
# ClubWPT Gold says so in its own help pages, and restricts coins and
# redemptions with the account, so the HUD will not start beside it.
HOSTILE = ("clubwpt",)


def processes():
    """Names of the running programs, on Windows; nothing elsewhere."""
    if sys.platform != "win32":
        return []
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split('","')[0].strip('"') for line in out.splitlines() if line]


def hostile(titles, names):
    """The first window title or process name of a client that bans HUDs."""
    for text in list(titles) + list(names):
        if any(h in text.lower().replace(" ", "") for h in HOSTILE):
            return text
    return None


def tell(text):
    """
    Say why the HUD is not starting, where the person will see it.

    Started from the TraceEV window there is no console: a windowed build
    has no `sys.stdout` at all, and a refusal printed there is a HUD that
    silently never appears -- beside ClubWPT Gold, exactly the moment the
    reason matters most. So with no console the reason is a message box.
    """
    if sys.stdout is not None and not getattr(sys, "frozen", False):
        print(text)
        return
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showwarning("TraceEV HUD", text, parent=root)
    root.destroy()


def run(source=None, db_path=DB, folders=None, extra=None, layout=False):
    """The HUD: the watcher on its thread, the boxes on Tk's."""
    found = hostile([w[0] for w in windows()], processes())
    if found:
        tell(f"Not starting: {found!r} is open, and that client restricts "
             "accounts that run a HUD. Close it completely, then start the "
             "HUD again.")
        return 1
    conf = settings()
    bad = problems(conf)
    if bad:
        tell(f"Not starting: {SETTINGS} has problems --\n"
             + "\n".join(f"  {p}" for p in bad))
        return 1
    dpi_aware()
    import tkinter as tk
    root = tk.Tk()
    root.title("TraceEV HUD")
    tk.Label(root, justify="left", text=(
        "TraceEV HUD, layout mode: drag each box onto its seat.\n"
        "Where you drop it is saved for every table that size."
        if layout else
        "TraceEV HUD is running.\nClick a box for the player in full.\n"
        "Close this window to stop it.")).pack(padx=10, pady=(8, 4))
    overlay = Overlay(root, source or windows, conf, db_path, layout=layout)
    watcher = Watcher(db_path, folders, overlay.inbox, conf)

    # Which folders are being watched, said, because a client saving its
    # hands somewhere else is a HUD that draws nothing -- and from the
    # outside that looks the same as a HUD that is broken.
    where = tk.Label(root, justify="left", anchor="w")
    where.pack(padx=10, pady=(0, 4), fill="x")

    def show_where():
        there = [str(r) for r in watcher.roots() if r.exists()]
        where.config(text=(
            "Watching for hands in:\n  " + "\n  ".join(there) if there else
            "No ACR or PokerStars hand history folder found.\n"
            "Use Add a folder... to show the HUD where your client saves them."))
    show_where()

    def add_folder():
        from tkinter import filedialog
        chosen = filedialog.askdirectory(
            parent=root, title="Folder your poker client saves hands in")
        if not chosen or chosen in conf["folders"]:
            return
        conf["folders"] = conf["folders"] + [chosen]
        save(conf)
        show_where()

    def edited(new):
        # The same dicts the overlay and the watcher hold, changed in place,
        # and every table worked out again under them.
        conf.clear()
        conf.update(new)
        watcher.redraw = True
    buttons = tk.Frame(root)
    buttons.pack(padx=10, pady=(0, 8), anchor="w")
    tk.Button(buttons, text="Edit the HUD...",
              command=lambda: Editor(root, conf, on_save=edited)).pack(side="left")
    if folders is None:
        tk.Button(buttons, text="Add a folder...",
                  command=add_folder).pack(side="left", padx=(6, 0))
    stop = threading.Event()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    if extra:
        extra(root, watcher, overlay)
    root.after(100, overlay.tick)
    root.protocol("WM_DELETE_WINDOW", lambda: (stop.set(), root.destroy()))
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# The editor
# ---------------------------------------------------------------------------

def form_of(conf):
    """
    The settings as the editor's text fields: one string per field.

    The fields are plain text, a stat key per word, because the registry
    has forty stats and a user's own besides, and a word typed is quicker
    than a list scrolled -- and the list of every key is beside them.
    """
    colours = []
    for key, rule in (conf.get("colours") or {}).items():
        colours.append(" ".join([key] + [f"{side} {pct:g} {c}" for side, (pct, c)
                                         in rule.items()]))
    badges = conf.get("badges") or {}
    return {
        "rates": conf["rates"],
        "faint_below": str(conf["faint_below"]),
        "lines": "\n".join(" ".join(line) for line in conf["lines"]),
        "popup": "\n".join(f"{title}: {' '.join(keys)}"
                           for title, keys in conf["popup"]),
        "by_position": " ".join(conf["by_position"]),
        "next_hand": "\n".join(f"{pos}: {' '.join(keys)}" for pos, keys
                               in (conf.get("next_hand") or {}).items()),
        "colours": "\n".join(colours),
        "note": badges.get("note") or "",
        "overfold": badges.get("overfold") or "",
        "rules": "\n".join(
            f"{r['badge']} {r['stat']} {'above' if 'above' in r else 'below'} "
            f"{r.get('above', r.get('below')):g}" for r in badges.get("rules") or ()),
    }


def conf_of(form, base):
    """
    The editor's text fields back into settings, on top of `base`.

    Anything that will not parse is left in a shape `problems` names in
    words rather than raised: the editor shows the list and saves nothing.
    """
    def number(word):
        try:
            return float(word) if "." in word else int(word)
        except ValueError:
            return word

    conf = json.loads(json.dumps(base))
    conf["rates"] = form["rates"].strip()
    conf["faint_below"] = number(form["faint_below"].strip())
    conf["lines"] = [l.split() for l in form["lines"].splitlines() if l.strip()]
    popup = []
    for l in form["popup"].splitlines():
        if l.strip():
            title, _colon, keys = l.partition(":")
            popup.append([title.strip(), keys.split()])
    conf["popup"] = popup
    conf["by_position"] = form["by_position"].split()
    nxt = {}
    for l in form.get("next_hand", "").splitlines():
        if l.strip():
            pos, _colon, keys = l.partition(":")
            nxt[pos.strip()] = keys.split()
    conf["next_hand"] = nxt
    colours = {}
    for l in form["colours"].splitlines():
        words = l.split()
        if not words:
            continue
        rule = colours.setdefault(words[0], {})
        rest = words[1:]
        while rest:
            side, pair, rest = rest[0], rest[1:3], rest[3:]
            rule[side] = [number(pair[0]) if pair else None,
                          pair[1] if len(pair) > 1 else None]
    conf["colours"] = colours
    rules = []
    for l in form["rules"].splitlines():
        words = l.split()
        if not words:
            continue
        rule = {"badge": words[0], "stat": words[1] if len(words) > 1 else None}
        if len(words) > 3:
            rule[words[2]] = number(words[3])
        rules.append(rule)
    conf["badges"] = {"note": form["note"].strip(),
                      "overfold": form["overfold"].strip(), "rules": rules}
    return conf


class Editor:
    """
    The HUD's settings in a window: what a box shows, what a popup shows,
    the colours and the badges. Saved to `hud.json`, refused with the
    reasons when anything does not parse, and applied to the running HUD
    without restarting it.
    """

    HELP = {
        "lines": "One line of the box per line, stat keys separated by spaces.",
        "popup": "One section per line:  Title: key key key",
        "by_position": "Stats the popup also splits by position.",
        "next_hand": "The box's last line, for the seat each player has in the "
                     "next hand:  BTN: rfi steal",
        "colours": "One stat per line:  vpip below 15 #7fb2ff above 40 #ff9b73",
        "rules": "One badge per line, shown only when the 95% interval is past "
                 "the line:  LOOSE vpip above 40",
    }

    def __init__(self, root, conf, path=None, on_save=None):
        import tkinter as tk
        self.conf, self.path, self.on_save = conf, path, on_save
        self.top = top = tk.Toplevel(root)
        top.title("Edit the TraceEV HUD")
        form = form_of(conf)
        self.fields = {}
        left = tk.Frame(top, padx=10, pady=8)
        left.pack(side="left", fill="both", expand=True)

        row = tk.Frame(left)
        row.pack(fill="x", pady=(0, 6))
        self.rates = tk.StringVar(value=form["rates"])
        tk.Label(row, text="Numbers").pack(side="left")
        for value, text in (("pooled", "pulled towards the pool"),
                            ("raw", "raw, as Hand2Note")):
            tk.Radiobutton(row, text=text, value=value,
                           variable=self.rates).pack(side="left")
        tk.Label(row, text="   faint under").pack(side="left")
        self.faint = tk.Entry(row, width=4)
        self.faint.insert(0, form["faint_below"])
        self.faint.pack(side="left")
        tk.Label(row, text="chances").pack(side="left")

        for name, label, height in (("lines", "The box", 3),
                                    ("popup", "The popup", 6),
                                    ("by_position", "By position", 1),
                                    ("next_hand", "Next hand", 6),
                                    ("colours", "Colours", 4),
                                    ("rules", "Badges", 4)):
            tk.Label(left, text=label, font=("TkDefaultFont", 9, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(left, text=self.HELP[name], anchor="w", fg="#666").pack(fill="x")
            text = tk.Text(left, height=height, width=72, wrap="none",
                           undo=True, font=("Consolas", 9))
            text.insert("1.0", form[name])
            text.pack(fill="x", pady=(0, 6))
            self.fields[name] = text
        row = tk.Frame(left)
        row.pack(fill="x")
        self.marks = {}
        for name, label in (("note", "note mark"), ("overfold", "overfold badge")):
            tk.Label(row, text=label).pack(side="left")
            e = tk.Entry(row, width=6)
            e.insert(0, form[name])
            e.pack(side="left", padx=(2, 12))
            self.marks[name] = e
        sizes = sorted((conf.get("seats") or {}), key=int)
        self.layouts = tk.Label(
            left, anchor="w", fg="#666",
            text=("Seat layouts you placed: " + ", ".join(f"{s}-max" for s in sizes)
                  if sizes else "No seat layouts placed yet; the ellipse is used.")
            + "  Place them with  python hud.py --layout")
        self.layouts.pack(fill="x", pady=(6, 0))
        if sizes:
            tk.Button(left, text="Forget the placed layouts",
                      command=self.forget).pack(anchor="w")

        self.errors = tk.Label(left, fg="#b00020", justify="left", anchor="w",
                               wraplength=520)
        self.errors.pack(fill="x", pady=6)
        buttons = tk.Frame(left)
        buttons.pack(fill="x")
        tk.Button(buttons, text="Save", command=self.save, width=10).pack(side="right")
        tk.Button(buttons, text="Defaults", command=self.defaults).pack(side="left")

        # Every stat there is, saved ones included; a double-click puts its
        # key where the cursor is.
        right = tk.Frame(top, padx=6, pady=8)
        right.pack(side="right", fill="y")
        tk.Label(right, text="Stats (double-click to insert)").pack(anchor="w")
        box = tk.Listbox(right, width=46, height=34, font=("Consolas", 9))
        for s in stats.STATS:
            box.insert("end", f"{s.key:24} {s.label}" + ("  (yours)" if s.custom else ""))
        box.pack(fill="y", expand=True)
        self.focus = self.fields["lines"]
        for text in self.fields.values():
            text.bind("<FocusIn>", lambda e: setattr(self, "focus", e.widget))
        box.bind("<Double-Button-1>", lambda _e: self.insert(box))

    def insert(self, box):
        pick = box.curselection()
        if pick:
            self.focus.insert("insert", " " + box.get(pick[0]).split()[0])

    def form(self):
        got = {name: text.get("1.0", "end") for name, text in self.fields.items()}
        got.update({name: e.get() for name, e in self.marks.items()})
        got["rates"] = self.rates.get()
        got["faint_below"] = self.faint.get()
        return got

    def save(self):
        new = conf_of(self.form(), self.conf)
        bad = problems(new)
        if bad:
            self.errors.config(text="Not saved:\n" + "\n".join(bad[:8]))
            return None
        save(new, self.path)
        self.errors.config(text="")
        if self.on_save:
            self.on_save(new)
        self.top.destroy()
        return new

    def defaults(self):
        form = form_of(DEFAULTS)
        for name, text in self.fields.items():
            text.delete("1.0", "end")
            text.insert("1.0", form[name])
        for name, e in self.marks.items():
            e.delete(0, "end")
            e.insert(0, form[name])
        self.rates.set(form["rates"])
        self.faint.delete(0, "end")
        self.faint.insert(0, form["faint_below"])

    def forget(self):
        self.conf["seats"] = {}
        self.layouts.config(text="Placed layouts will be forgotten on Save.")


def edit():
    """`--edit`: the editor on its own, without the HUD running."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    ed = Editor(root, settings())
    ed.top.protocol("WM_DELETE_WINDOW", root.destroy)
    ed.top.bind("<Destroy>", lambda e: root.destroy() if e.widget is ed.top else None)
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# The demo, for seeing it without a poker client open
# ---------------------------------------------------------------------------

def last_tables(con, n=1):
    """The HUD-site tables whose latest hands are newest, with a hero seated."""
    hud_sites = sites.with_hud()
    return [tuple(r) for r in con.execute(
        "SELECT site, table_id FROM hands WHERE hero_seat IS NOT NULL AND "
        f"game='HOLDEM' AND site IN ({','.join('?' * len(hud_sites))}) "
        "GROUP BY site, table_id ORDER BY MAX(played_at) DESC LIMIT ?",
        hud_sites + (n,))]


def demo(db_path=DB, layout=False):
    """
    A pretend table window named after the last table you played, with the
    HUD drawn over it from your own database.

    Everything but the poker client is real: the boxes come from the same
    numbers, the same title matching and the same window code. Drag the
    green window around and the boxes follow; resize it and they spread.
    """
    con = sqlite3.connect(db_path)
    tables = last_tables(con, 2)
    con.close()
    if not tables:
        tell("No ACR or PokerStars hand with you seated in the database yet. "
             "Import some hands, then try again.")
        return 1
    tables_open = []

    def open_tables(root, watcher, overlay):
        import tkinter as tk
        con = sqlite3.connect(db_path)
        views = {}
        for i, (site, table_id) in enumerate(tables):
            v = view(con, site, table_id, overlay.conf)
            if v is None:
                continue
            views[(site, table_id)] = v
            win = tk.Toplevel(root)
            win.title(f"{table_id} - demo of the TraceEV HUD ({site})")
            # Side by side, so that neither table's boxes sit on the other's.
            win.geometry(f"640x460+{40 + 680 * i}+80")
            canvas = tk.Canvas(win, bg="#0b3d1f", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda e, c=canvas: (
                c.delete("felt"),
                c.create_oval(e.width * 0.12, e.height * 0.16, e.width * 0.88,
                              e.height * 0.80, fill="#14703a", outline="#2b2b2b",
                              width=8, tags="felt")))
            tables_open.append(win)
            print(as_text(v))
        con.close()
        overlay.inbox.put(views)
        # The watcher keeps these tables for ever, as if their files never
        # stopped moving, so that an edit to the settings redraws them.
        for t in views:
            watcher.active[t] = float("inf")

    def source():
        out = []
        for win in tables_open:
            try:
                out.append((win.title(), win.winfo_rootx(), win.winfo_rooty(),
                            win.winfo_width(), win.winfo_height()))
            except Exception:
                pass
        return out

    return run(source=source, db_path=db_path, folders=[],
               extra=open_tables, layout=layout) or 0


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check(db_path=DB):
    """
    The HUD says what the engine says, and puts it in the right place.

    Its numbers are held to `stats.rate`, `stats.shrunk` and a pool
    written out longhand, for real players; the layout is held to having
    hero at the bottom and nobody sharing a place; the title matching to
    every table name in the database, including the pairs where one name
    begins another; and the watcher to a history file that grows.
    """
    fails = []
    con = sqlite3.connect(db_path)
    conf = dict(DEFAULTS)
    keys = [k for line in conf["lines"] for k in line]
    missing = [k for k in keys if k not in stats.BY_KEY]
    if missing:
        fails.append(f"the HUD names stats the registry does not have: {missing}")

    # The numbers, for the busiest few opponents at each HUD site's
    # busiest game.
    tested = 0
    for site in sites.with_hud():
        for player, fmt, bb in con.execute(
                "SELECT player, fmt, bb FROM spots WHERE site=? AND is_hero=0 "
                "AND player IS NOT NULL GROUP BY player ORDER BY COUNT(*) DESC "
                "LIMIT 4", (site,)).fetchall():
            got = numbers(con, site, fmt, bb, player, keys)
            raw = numbers(con, site, fmt, bb, player, keys, mode="raw")
            for key in keys:
                n, k, _p, _l, _h = stats.rate(con, key, "player=? AND site=?",
                                              (player, site))
                pn, pk, _pp, _pl, _ph = stats.rate(
                    con, key, "site=? AND fmt=? AND bb=? AND is_hero=0 "
                    "AND player<>?", (site, fmt, bb, player))
                prior = pk / pn if pn else None
                want = stats.shrunk(k, n, prior)
                want = want if want is not None else (k / n if n else None)
                gn, gk, _raw, gprior, shown = got[key]
                close = lambda a, b: (a is None and b is None) or (
                    a is not None and b is not None and abs(a - b) < 1e-12)
                if (gn, gk) != (n, k) or not close(gprior, prior) \
                        or not close(shown, want) \
                        or not close(raw[key][4], k / n if n else None):
                    fails.append(f"{site} {player} {key}: HUD ({gn}, {gk}, "
                                 f"{shown}) against the engine ({n}, {k}, {want})")
                tested += 1
    print(f"numbers agree with the engine  {'yes' if not fails else 'NO'}   "
          f"{tested} player-stats, pooled and raw")

    # A table's boxes: hero has none, a stat never asked is a dash, and a
    # number on few chances is faint.
    tables = last_tables(con, 5)
    boxes = 0
    for site, table_id in tables:
        v = view(con, site, table_id, conf)
        hero = {s["seat"] for s in v["table"]["seats"] if s["is_hero"]}
        if hero & set(v["boxes"]):
            fails.append(f"{table_id}: hero has a box")
        for seat, box in v["boxes"].items():
            boxes += 1
            for line_keys, cells in zip(conf["lines"], box["lines"][1:]):
                for key, (text, faint, colour) in zip(line_keys, cells):
                    n, _k, _raw, _prior, shown = box["numbers"][key]
                    if (n == 0) != (text == "-"):
                        fails.append(f"{table_id} seat {seat} {key}: n={n} "
                                     f"drawn as {text!r}")
                    if faint != (n < conf["faint_below"]):
                        fails.append(f"{table_id} seat {seat} {key}: n={n} "
                                     f"faint={faint}")
                    # The colour, written out from the rule by hand: the
                    # rule's side, and never on a faint number.
                    rule = conf["colours"].get(key, {})
                    want = None
                    if n >= conf["faint_below"] and shown is not None:
                        if "below" in rule and 100 * shown < rule["below"][0]:
                            want = rule["below"][1]
                        elif "above" in rule and 100 * shown > rule["above"][0]:
                            want = rule["above"][1]
                    if colour != want:
                        fails.append(f"{table_id} seat {seat} {key}: "
                                     f"{100 * (shown or 0):.0f}% on n={n} "
                                     f"coloured {colour}, the rule says {want}")
    print(f"boxes drawn as specified       {'yes' if not fails else 'NO'}   "
          f"{boxes} boxes over {len(tables)} tables")
    if tables:
        print(as_text(view(con, *tables[0], conf)))

    # The popup: every row the engine's count, interval and pool, the
    # positions each the engine's count at that position, shrunk towards
    # the pool's figure at that same position.
    from query import POSITIONS
    unknown = [k for _t, keys in conf["popup"] for k in keys if k not in stats.BY_KEY]
    unknown += [k for k in conf["by_position"] if k not in stats.BY_KEY]
    if unknown:
        fails.append(f"the popup names stats the registry does not have: {unknown}")
    rows = cells = 0
    before = len(fails)
    for site in sites.with_hud():
        for (player,) in con.execute(
                "SELECT player FROM spots WHERE site=? AND is_hero=0 AND player "
                "IS NOT NULL GROUP BY player ORDER BY COUNT(*) DESC LIMIT 2",
                (site,)).fetchall():
            p = popup_for(con, player, site)
            gw = "site=? AND fmt=? AND bb=? AND is_hero=0 AND player<>?"
            gp = (p["site"], p["fmt"], p["bb"], player)
            for _title, section in p["sections"]:
                for r in section:
                    rows += 1
                    n, k, _p, lo, hi = stats.rate(con, r["key"],
                                                  "player=? AND site=?",
                                                  (player, site))
                    pn, pk = stats.rate(con, r["key"], gw, gp)[:2]
                    if (r["n"], r["k"]) != (n, k) or \
                            (n and (abs(r["lo"] - lo) > 1e-12
                                    or abs(r["hi"] - hi) > 1e-12)) or \
                            (r["prior"] is None) != (pn == 0) or \
                            (pn and abs(r["prior"] - pk / pn) > 1e-12) or \
                            (n == 0) != (r["text"] == "-") or \
                            (n and r["faint"] != (n < conf["faint_below"])):
                        fails.append(f"popup {player} {r['key']}: {r}")
            for label, row in p["positions"]:
                key = next(k for k in conf["by_position"]
                           if stats.BY_KEY[k].label == label)
                for pos, n_, k_, text, _dim in row:
                    cells += 1
                    n, k = stats.rate(con, key, "player=? AND site=? AND position=?",
                                      (player, site, pos))[:2]
                    pn, pk = stats.rate(con, key, gw + " AND position=?",
                                        gp + (pos,))[:2]
                    want = stats.shrunk(k, n, pk / pn if pn else None)
                    want = "-" if not n else f"{round(100 * (want if want is not None else k / n))}"
                    if (n_, k_) != (n, k) or text != want:
                        fails.append(f"popup {player} {key} at {pos}: "
                                     f"({n_}, {k_}, {text}) against ({n}, {k}, {want})")
    print(f"popups agree with the engine   {'yes' if len(fails) == before else 'NO'}   "
          f"{rows} rows, {cells} position cells")

    # The layout.
    bad = 0
    for max_seats in (2, 3, 4, 5, 6, 8, 9, 10):
        for hero in range(1, max_seats + 1):
            spots_ = [place(slot(s, hero, max_seats), max_seats, conf)
                      for s in range(1, max_seats + 1)]
            bottom = max(spots_, key=lambda p: p[1])
            if bottom != spots_[hero - 1]:
                bad += 1
            if len({(round(x, 3), round(y, 3)) for x, y in spots_}) != max_seats:
                bad += 1
            if not all(0 <= x <= 1 and 0 <= y <= 1 for x, y in spots_):
                bad += 1
    print(f"hero at the bottom, no overlap {'yes' if not bad else 'NO'}")
    if bad:
        fails.append(f"{bad} table layouts put hero elsewhere or two seats together")

    # Title matching, against every HUD-site table name there is.
    names = [tuple(r) for r in con.execute(
        "SELECT DISTINCT site, table_id FROM hands WHERE site IN "
        f"({','.join('?' * len(sites.with_hud()))})", sites.with_hud())]
    wrong = []
    for site, table_id in names:
        for title in (f"{table_id} - $0.01/$0.02 USD - No Limit Hold'em",
                      f"Table '{table_id}' 6-max - Logged In as hero",
                      f"{table_id} (#123456) No Limit Hold'em"):
            if match(title, names) != (site, table_id):
                wrong.append((title, match(title, names)))
    for title in ("Inbox - Outlook", "PokerStars Lobby", ""):
        if match(title, names) is not None:
            wrong.append((title, match(title, names)))
    # The case that has to be right: one name the start of another.
    pair = [("pokerstars", "Halley"), ("pokerstars", "Halley II")]
    if match("Halley II - $0.01/$0.02", pair) != pair[1] or \
            match("Halley - $0.01/$0.02", pair) != pair[0]:
        wrong.append(("Halley / Halley II", None))
    print(f"window titles find their table {'yes' if not wrong else 'NO'}   "
          f"{len(names)} table names")
    for title, got in wrong[:3]:
        print(f"    {title!r} -> {got}")
    if wrong:
        fails.append(f"{len(wrong)} window titles matched the wrong table")
    fails += badge_check(con)
    fails += next_hand_check(con)
    con.close()

    # The refusal to start beside a client that bans HUDs.
    guard = [hostile(["ClubWPT Gold"], []), hostile([], ["ClubWPTGold.exe"]),
             hostile(["Halley - $0.01/$0.02", "Inbox"], ["python.exe"])]
    ok = guard[0] and guard[1] and guard[2] is None
    print(f"refuses beside ClubWPT Gold   {'yes' if ok else 'NO'}")
    if not ok:
        fails.append("the HUD would start beside a client that bans it")

    fails += settings_check()
    fails += watcher_check()
    fails += window_check(db_path)
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def settings_check():
    """
    The settings the editor writes are the settings it read, and the ones
    that are wrong are refused with a reason rather than drawn as nothing.
    """
    import tempfile
    fails = []
    if problems(DEFAULTS):
        fails.append(f"the default settings have problems: {problems(DEFAULTS)}")
    if conf_of(form_of(DEFAULTS), DEFAULTS) != DEFAULTS:
        fails.append("the editor's fields do not give back the settings they show")
    # One wrong thing per case, each of which would otherwise be drawn as
    # nothing at all, or as the wrong thing.
    form = form_of(DEFAULTS)
    cases = {
        "a misspelled stat on the box": dict(form, lines="vpip pfr threbet"),
        "a misspelled stat in the popup": dict(form, popup="Flop: cbet_flp"),
        "a colour that is not one": dict(form, colours="vpip above 40 orange"),
        "a colour with no percentage": dict(form, colours="vpip above #ff9b73"),
        "a badge with no side": dict(form, rules="LOOSE vpip 40"),
        "a badge on no stat": dict(form, rules="LOOSE"),
        "rates neither pooled nor raw": dict(form, rates="shrunk"),
        "a faint threshold that is not a count": dict(form, faint_below="ten"),
        "an empty box": dict(form, lines=""),
    }
    missed = [name for name, f in cases.items()
              if not problems(conf_of(f, DEFAULTS))]
    if missed:
        fails.append(f"settings accepted that should be refused: {missed}")
    # A layout saved for one table size is used for that size and no other,
    # and comes back from the file it was saved to.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hud.json"
        conf = json.loads(json.dumps(DEFAULTS))
        conf["seats"] = {"6": [[0.5, 0.9], [0.1, 0.7], [0.1, 0.2],
                               [0.5, 0.05], [0.9, 0.2], [0.9, 0.7]]}
        save(conf, path)
        back = settings(path)
        if back != conf:
            fails.append("settings saved and read back are not the same")
        if place(3, 6, back) != (0.5, 0.05) or place(3, 9, back) != place(3, 9):
            fails.append("a placed layout is not used for its size alone")
        mine = json.loads(path.read_text(encoding="utf-8"))
        if set(mine) != {"seats"}:
            fails.append(f"hud.json holds more than what was changed: {sorted(mine)}")
        try:
            save(dict(conf, lines=[["nonsense"]]), path)
            fails.append("bad settings were saved")
        except ValueError:
            pass
        # A folder added from the HUD window is watched, alongside the
        # usual places rather than instead of them, and only when the
        # watcher was not given folders of its own.
        if not problems(dict(conf, folders="C:/hands")):
            fails.append("a folder that is not in a list was accepted")
        extra = Path(tmp) / "elsewhere"
        roots = Watcher(conf=dict(conf, folders=[str(extra)])).roots()
        usual = Watcher(conf=conf).roots()
        if extra not in roots or not set(usual) <= set(roots):
            fails.append("an added folder is not watched beside the usual ones")
        if extra in Watcher(folders=[], conf=dict(conf, folders=[str(extra)])).roots():
            fails.append("a watcher given its folders also watches the settings'")
    print(f"the editor's settings hold     {'yes' if not fails else 'NO'}   "
          f"{len(cases)} wrong settings refused")
    return fails


def badge_check(con):
    """
    A badge appears exactly when its rule says, written out longhand.

    The rule badges against `stats.wilson` on the engine's own count; the
    overfold badge against `query.overfolds_of` asked by hand; the note
    mark against a note written into a copy of the database.
    """
    import query
    fails = []
    conf = json.loads(json.dumps(DEFAULTS))
    # Lines low enough that the corpus's small samples clear some of them,
    # so the test is of badges given as well as refused.
    conf["badges"]["rules"] += [{"badge": "VP>5", "stat": "vpip", "above": 5},
                                {"badge": "WT<60", "stat": "wtsd", "below": 60}]
    given = asked = 0
    for site in sites.with_hud():
        for (player,) in con.execute(
                "SELECT player FROM spots WHERE site=? AND is_hero=0 AND player "
                "IS NOT NULL GROUP BY player ORDER BY COUNT(*) DESC LIMIT 6",
                (site,)).fetchall():
            got = numbers(con, site, "RING", 0, player, rule_keys(conf))
            have = {b for b, _why in badges_of(con, site, player, conf, got)}
            for rule in conf["badges"]["rules"]:
                asked += 1
                n, k = stats.rate(con, rule["stat"], "player=? AND site=?",
                                  (player, site))[:2]
                _p, lo, hi = stats.wilson(k, n) if n else (0, 0, 1)
                want = n >= conf["faint_below"] and (
                    100 * lo > rule["above"] if "above" in rule
                                  else 100 * hi < rule["below"])
                if want != (rule["badge"] in have):
                    fails.append(f"{player} {rule['badge']}: {k}/{n}, "
                                 f"interval {lo:.2f}-{hi:.2f}, badge {not want}")
                given += want
            real = any(r["real"] and r["n"] >= 30 for r in query.overfolds_of(
                con, f"player = {query.q(player)} AND site = {query.q(site)}"))
            if real != (conf["badges"]["overfold"] in have):
                fails.append(f"{player}: overfold badge disagrees with --overfolds")
            if conf["badges"]["note"] in have:
                fails.append(f"{player}: a note mark with no note written")
    # The note mark, on a copy so that the user's notes are never touched.
    import tempfile
    import notes
    with tempfile.TemporaryDirectory() as tmp:
        copy = sqlite3.connect(Path(tmp) / "copy.db")
        con.backup(copy)
        site, player = next(iter(con.execute(
            "SELECT site, player FROM spots WHERE is_hero=0 AND player IS NOT "
            "NULL AND site IN ('acr', 'pokerstars') LIMIT 1")))
        notes.note(copy, site, player, "")
        before = badges_of(copy, site, player, conf, {})
        notes.note(copy, site, player, "limps everything")
        after = badges_of(copy, site, player, conf, {})
        copy.close()
        if any(b == conf["badges"]["note"] for b, _w in before) or \
                not any(b == conf["badges"]["note"] for b, _w in after):
            fails.append("the note mark does not follow the note")
    print(f"badges given as the rules say  {'yes' if not fails else 'NO'}   "
          f"{given} of {asked} rule badges given")
    return fails


def next_hand_check(con):
    """
    The next hand's positions are the ones the next history writes, and the
    line's numbers are the engine's at that position, written out by hand.
    """
    import query
    fails = []
    hands = con.execute(
        "SELECT site, table_id, hand_id FROM hands WHERE game='HOLDEM' AND site IN "
        f"({','.join('?' * len(sites.with_hud()))}) "
        "ORDER BY site, table_id, played_at, hand_id", sites.with_hud()).fetchall()
    right = same = 0
    for a, b in zip(hands, hands[1:]):
        if a[:2] != b[:2]:
            continue
        now = dict(con.execute("SELECT seat, position FROM spots WHERE hand_id=?",
                               (b[2],)).fetchall())
        guess = next_positions(con, a[2])
        if set(guess) != set(now):
            continue                # somebody sat down or stood up
        same += 1
        right += guess == now
    # The corpus is files of hands, not whole sessions, so a pair of
    # "consecutive" hands can have hands missing between them; the rule is
    # held to nearly all, not all.
    if same and right < 0.95 * same:
        fails.append(f"next-hand positions right on only {right} of {same} hands")

    conf = json.loads(json.dumps(DEFAULTS))
    cells = 0
    for site, table_id in last_tables(con, 5):
        v = view(con, site, table_id, conf)
        t = v["table"]
        for seat, box in v["boxes"].items():
            pos = t["next"][seat]
            want_keys = conf["next_hand"].get(pos, [])
            if list(box["next"]) != want_keys:
                fails.append(f"{table_id} seat {seat}: next-hand stats "
                             f"{list(box['next'])}, not {want_keys} for {pos}")
                continue
            if want_keys and box["lines"][-1][0][0] != pos:
                fails.append(f"{table_id} seat {seat}: the line is not led by {pos}")
            for key in want_keys:
                cells += 1
                player = box["player"]
                n, k = stats.rate(con, key, "player=? AND site=? AND position=?",
                                  (player, site, pos))[:2]
                pn, pk = stats.rate(
                    con, key, "site=? AND fmt=? AND bb=? AND is_hero=0 AND "
                    "player<>? AND position=?",
                    (site, t["fmt"], t["bb"], player, pos))[:2]
                prior = pk / pn if pn else None
                gn, gk, gprior, _shown = box["next"][key]
                if (gn, gk) != (n, k) or (prior is None) != (gprior is None) or \
                        (prior is not None and abs(prior - gprior) > 1e-12):
                    fails.append(f"{table_id} {player} {key} at {pos}: "
                                 f"({gn}, {gk}, {gprior}) against ({n}, {k}, {prior})")
    print(f"next hand's seats and numbers  {'yes' if not fails else 'NO'}   "
          f"positions right on {right} of {same} hands, {cells} numbers")
    return fails


def watcher_check():
    """
    A history file that grows while the HUD watches reaches its boxes.

    Played out on a PokerStars file from FPDB's corpus: the first hands
    written, a look, the rest appended, another look. Without the corpus
    on this machine it says so and passes, as `fixtures.py` does.
    """
    import tempfile

    import fixtures
    import pokerstars
    sample = None
    for folder, f in fixtures.fixture_files():
        if importer.sniff(f) != "pokerstars":
            continue
        blocks = list(pokerstars.split_hands(importer.read_text(f)))
        parsed = [pokerstars.parse_hand(b, source=f.name) for b in blocks]
        if len(blocks) >= 4 and all(parsed) and any(
                p["hand"].get("hero_seat") for p in parsed):
            sample = blocks
            break
    if sample is None:
        print("the watcher sees a file grow   no PokerStars fixture on this machine")
        return []

    fails = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "histories"
        folder.mkdir()
        db = Path(tmp) / "hud.db"
        f = folder / "table.txt"
        f.write_text("\n\n".join(sample[:2]) + "\n\n", encoding="utf-8")
        w = Watcher(db, [folder], conf=dict(DEFAULTS))
        first = w.step()
        con = sqlite3.connect(db)
        before = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        con.close()
        with f.open("a", encoding="utf-8") as out:
            out.write("\n\n".join(sample[2:]) + "\n\n")
        # The same second, the same size twice, must not hide a change:
        # the signature is size and time together, and appending changes
        # the size.
        second = w.step()
        con = sqlite3.connect(db)
        after = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        hands = con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
        con.close()
        quiet = w.step()
        views = []
        while not w.out.empty():
            views.append(w.out.get())
    ok = (first and second and not quiet and after > before
          and hands == len(sample) and views and views[-1])
    print(f"the watcher sees a file grow   {'yes' if ok else 'NO'}   "
          f"{hands} hands, decisions {before} then {after}")
    if not ok:
        fails.append("a history file that grew did not reach the HUD")
    return fails


def window_check(db_path=DB):
    """
    The boxes are drawn, follow their table, and open and close a popup.

    The real window code, over a pretend table window that this function
    moves: Tk, the overlay, the popup's own thread. It needs Tk and a
    display, which CI has under a virtual one; without them it says so
    and passes, as the four window modules of `check.py` cannot.
    """
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print(f"the window draws and pops up  not run here ({type(e).__name__})")
        return []
    root.withdraw()
    con = sqlite3.connect(db_path)
    tables = last_tables(con, 1)
    views = {t: view(con, *t, DEFAULTS) for t in tables}
    con.close()
    if not tables:
        root.destroy()
        print("the window draws and pops up  no table to draw")
        return []
    table = tables[0]
    where = [200, 150]
    overlay = Overlay(root, lambda: [(f"{table[1]} - check", where[0], where[1],
                                      800, 560)], dict(DEFAULTS), db_path)
    overlay.inbox.put(views)
    root.after = lambda *_a: None           # ticked by hand below

    def settle(seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.02)

    fails = []
    overlay.tick()
    settle()
    want = len(views[table]["boxes"])
    if len(overlay.boxes) != want:
        fails.append(f"{len(overlay.boxes)} boxes drawn for {want} opponents")
    first = sorted(overlay.boxes)[0] if overlay.boxes else None
    moved = False
    if first:
        before = overlay.boxes[first][0].winfo_rootx()
        where[0] += 120
        overlay.tick()
        settle()
        moved = overlay.boxes[first][0].winfo_rootx() - before == 120
        if not moved:
            fails.append("a box did not follow its table")
        # A click on a number inside the box, as a person would click.
        label = overlay.boxes[first][1].winfo_children()[1].winfo_children()[0]
        label.event_generate("<Button-1>")
        end = time.time() + 20
        while time.time() < end and not (overlay.popup and overlay.popup[1]):
            overlay.tick()
            settle(0.1)
        opened = bool(overlay.popup and overlay.popup[1])
        label.event_generate("<Button-1>")
        overlay.tick()
        settle(0.1)
        if not opened:
            fails.append("a click on a box opened no popup")
        elif overlay.popup is not None:
            fails.append("a second click left the popup open")
    drawn = len(overlay.boxes)
    for top, _frame in overlay.boxes.values():
        top.destroy()

    # Layout mode: a box dragged 50 right and 30 down is saved there, for
    # its slot and table size, and drawn there on the next tick.
    import tempfile
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "hud.json"
    layout = Overlay(root, overlay.source, json.loads(json.dumps(DEFAULTS)),
                     db_path, layout=True, settings_path=path)
    layout.inbox.put(views)
    layout.tick()
    settle()
    if layout.boxes:
        key = sorted(layout.boxes)[0]
        top, frame = layout.boxes[key]
        x0, y0 = top.winfo_rootx(), top.winfo_rooty()
        frame.event_generate("<ButtonPress-1>", x=5, y=5, rootx=x0 + 5, rooty=y0 + 5)
        frame.event_generate("<B1-Motion>", x=55, y=35, rootx=x0 + 55, rooty=y0 + 35)
        settle(0.1)
        frame.event_generate("<ButtonRelease-1>", x=55, y=35,
                             rootx=x0 + 55, rooty=y0 + 35)
        layout.tick()
        settle()
        saved = settings(path).get("seats", {})
        size = layout.where[key][5]
        dx, dy = top.winfo_rootx() - x0, top.winfo_rooty() - y0
        if str(size) not in saved:
            fails.append("a box dragged in layout mode saved no layout")
        elif abs(dx - 50) > 2 or abs(dy - 30) > 2:
            fails.append(f"a dragged box was drawn {dx},{dy} away, not 50,30")
    for top, _frame in layout.boxes.values():
        top.destroy()

    # The editor: a change saved is written and handed to the running HUD;
    # a wrong one is refused, with its reason on the screen, and not written.
    seen = []
    ed = Editor(root, json.loads(json.dumps(DEFAULTS)), path=path,
                on_save=seen.append)
    ed.fields["lines"].delete("1.0", "end")
    ed.fields["lines"].insert("1.0", "vpip pfr nonsense")
    ed.save()
    refused = bool(ed.errors.cget("text")) and not seen
    ed.fields["lines"].delete("1.0", "end")
    ed.fields["lines"].insert("1.0", "vpip pfr\nwtsd")
    ed.save()
    if not refused:
        fails.append("the editor saved a stat that does not exist")
    if not seen or settings(path)["lines"] != [["vpip", "pfr"], ["wtsd"]]:
        fails.append("the editor's save did not reach the file and the HUD")
    tmp.cleanup()
    root.destroy()
    print(f"the window draws and pops up  {'yes' if not fails else 'NO'}   "
          f"{drawn} boxes; dragged, edited")
    return fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--edit" in argv:
        return edit()
    if "--layout" in argv:
        # With a real client open, the real tables; with none, the demo's.
        return demo(layout=True) if "--demo" in argv else run(layout=True)
    if "--demo" in argv:
        return demo()
    if "--windows" in argv:
        con = sqlite3.connect(DB)
        tables = [tuple(r) for r in con.execute(
            "SELECT DISTINCT site, table_id FROM hands WHERE site IN "
            f"({','.join('?' * len(sites.with_hud()))})", sites.with_hud())]
        con.close()
        found = windows()
        if not found:
            print("no windows to list -- this is Windows-only")
        for title, x, y, w, h in found:
            hit = match(title, tables)
            print(f"{'-> ' + hit[0] + ' ' + hit[1] if hit else '':40} "
                  f"{w}x{h}+{x}+{y}  {title}")
        return 0
    if "--popup" in argv:
        at = argv.index("--popup") + 1
        if at >= len(argv):
            print("--popup needs a player's name")
            return 1
        con = sqlite3.connect(DB)
        p = popup_for(con, argv[at],
                      argv[at + 1] if at + 1 < len(argv) else None)
        con.close()
        if p is None:
            print(f"no hands of {argv[at]!r} on ACR or PokerStars")
            return 1
        print(popup_text(p))
        return 0
    if "--print" in argv:
        con = sqlite3.connect(DB)
        for site, table_id in last_tables(con, 3):
            print(as_text(view(con, site, table_id)))
            print()
        con.close()
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
