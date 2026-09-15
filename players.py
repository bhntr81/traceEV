"""
Who each player is, so that "how regulars play" is a filter and not a guess.

Every stat in this tracker averages two different populations together.
People play completely differently against a recreational player than
against each other -- they isolate wider, they value-bet thinner, they bluff
less -- so a pool number that mixes the two describes neither. Separating
them is the single largest thing Hand2Note does that this could not, and it
needs one thing this database has never had: a row per player rather than a
row per hand.

Everything here is a `GROUP BY` over `spots`, which already carries VPIP,
PFR, 3-bet, fold-to-3-bet, WWSF, WTSD and money as per-hand flags. Nothing
new is measured; what is new is that it is measured *per person*.

**Only ACR has people.** ACR writes the screen name, and it is the same
player next week at another table and another stake. Ignition writes nobody,
so `spots.identify` gives a ring seat the name `table:seat:segment`, which
is one person for exactly as long as they stay sat there and is a different
person after they leave. Those rows are kept and marked `durable = 0`,
because a read that is true for a session is still a read -- but they must
never be counted as people, and a report that does not say which kind it has
is a report about a pool of ghosts.

The classification refuses more often than it decides. A rate on 40 hands
has an interval about sixteen points wide, so "VPIP 30" and "VPIP 45" are
the same measurement, and a rule that reads the point estimate would sort
half the pool at random. Every clause below is a test on an interval, and
anything that does not clear one is `unknown` -- which is an answer, and the
honest one. Checked by splitting each player's hands in two: of 85 ACR
players with 100 hands or more, **not one** is a reg on one half and a fish
on the other.

    python players.py            build the table
    python players.py --check    the classes survive being split in half
    python players.py NAME       one player in full
"""

import sqlite3
import sys
import re
from pathlib import Path

import sites
from stats import wilson

DB = Path(__file__).parent / "hands.db"

# Where a class begins, and what each number means in plain terms. They are
# thresholds on the *interval*, never on the estimate, so a player is only
# called loose when they cannot plausibly be tight.
LOOSE = 0.34         # plays more than a third of hands: too many to be solid
TIGHT = 0.33         # and the same line from the other side
RAISES = 0.10        # raises at least one hand in ten before the flop
PASSIVE = 0.10       # or, below this, essentially never raises
ENTERS = 0.20        # ...while still entering a fifth of pots, which is what
                     # makes it passive rather than merely tight

SCHEMA = """
DROP TABLE IF EXISTS players;
CREATE TABLE players (
  site TEXT, player TEXT, durable INT, hands INT,
  vpip REAL, pfr REAL, threebet REAL, fold_to_threebet REAL,
  wwsf REAL, wtsd REAL, wsd REAL,
  bb100 REAL, class TEXT,
  PRIMARY KEY (site, player));
CREATE INDEX players_class ON players(class, hands);
"""

# Added to `decisions` rather than kept beside it, because every filter in
# the program is a predicate over that one table and a join would be a
# second way of asking.
#
# `n_reg` and `n_fish` count who else is still in the pot, and they exist
# because `vs_class` only means anything heads up. That left 83% of the
# database outside every matchup question: a three-way pot has no "the other
# player", so it had no answer at all. Counting the company instead makes
# "a fish is in this pot" and "everybody left is a reg" askable multiway,
# and multiplies the sample the reg-versus-reg comparison has to work with
# -- which is the binding constraint on every finding here.
#
# `vs_seat` is there and `vs_player` is not enough on its own: on Ignition
# Zone nobody has a name, so a NULL `vs_player` means either "nobody is left
# to face" or "the man opposite is a stranger", and those are different
# facts. The seat is always known when there is one opponent, which is what
# makes it the thing to check the walk against.
#
# `fish_left` and the three beside it are the same company split by where
# it sits. A fish on your left acts after you postflop and has position on
# you for the whole hand; one on your right does not, and the difference
# is most of what "table selection" means. Left is everybody still in who
# acts after this player in the postflop order -- the seats clockwise from
# the button -- and right is everybody who acts before. Every fish in the
# pot is on one side or the other, so `fish_left + fish_right = n_fish`
# on every row, which is what the check holds them to.
COLUMNS = (("player_class", "TEXT"), ("vs_player", "TEXT"),
            ("vs_class", "TEXT"), ("vs_seat", "INT"),
            ("n_reg", "INT"), ("n_fish", "INT"),
            ("fish_left", "INT"), ("fish_right", "INT"),
            ("reg_left", "INT"), ("reg_right", "INT"))
NAMES = tuple(c for c, _t in COLUMNS)

COHORT_FIELDS = {
    "hands": "hands",
    "vpip": "vpip",
    "pfr": "pfr",
    "gap": "vpip - pfr",
    "threebet": "threebet",
    "fold_to_threebet": "fold_to_threebet",
    "wwsf": "wwsf",
    "wtsd": "wtsd",
    "wsd": "wsd",
    "bb100": "bb100",
}
CONDITION = re.compile(r"^\s*(>=|<=|=|>|<)?\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$")


def cohort(con, conditions=(), site=None, klass=None, durable=None):
    """Return players matching safe, player-level Multiple Players filters."""
    where, params = ["1=1"], []
    for field, value in conditions:
        column = COHORT_FIELDS.get(field)
        match = CONDITION.fullmatch(value)
        if column is None or match is None:
            raise ValueError(f"invalid cohort condition: {field} {value}")
        operator = match.group(1) or "="
        where.append(f"{column} {operator} ?")
        params.append(float(match.group(2)))
    if site:
        where.append("site = ?")
        params.append(site)
    if klass:
        if klass not in ("reg", "fish", "unknown"):
            raise ValueError(f"invalid player class: {klass}")
        where.append("class = ?")
        params.append(klass)
    if durable is not None:
        where.append("durable = ?")
        params.append(int(durable))
    # The columns are listed rather than starred because `select_cohort`
    # reads the first two by position. Under `SELECT *` that is a promise
    # the `players` schema is making without knowing it: a column inserted
    # ahead of `site` would silently fill the cohort with hand counts and
    # match nothing, and nothing would raise. Named here, a schema change
    # breaks loudly instead.
    return con.execute(
        "SELECT site, player, durable, hands, vpip, pfr, threebet, "
        "fold_to_threebet, wwsf, wtsd, wsd, bb100, class FROM players "
        "WHERE " + " AND ".join(where) +
        " ORDER BY hands DESC, site, player", params).fetchall()


def cohort_summary(rows):
    """Combine player rates by their underlying opportunity counts."""
    hands = sum(row["hands"] for row in rows)

    def weighted(name):
        known = [row for row in rows if row[name] is not None]
        denominator = sum(row["hands"] for row in known)
        return (sum(row["hands"] * row[name] for row in known) / denominator
                if denominator else None)

    vpip = weighted("vpip")
    pfr = weighted("pfr")
    return {
        "players": len(rows),
        "hands": hands,
        "vpip": vpip,
        "pfr": pfr,
        "gap": vpip - pfr if vpip is not None and pfr is not None else None,
        "bb100": weighted("bb100"),
    }


def show_cohort(conditions=(), site=None, klass=None, durable=None,
                db_path=DB):
    """List a Multiple Players cohort and its pooled base statistics."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = cohort(con, conditions, site, klass, durable)
    summary = cohort_summary(rows)
    print(f"{summary['players']:,} players - {summary['hands']:,} hands")
    print("player                  site       hands    VPIP    PFR     gap  class")
    for row in rows:
        print(f"{row['player'][:22]:22} {row['site']:9} {row['hands']:7,} "
              f"{row['vpip']:6.1f} {row['pfr']:6.1f} "
              f"{row['vpip'] - row['pfr']:6.1f}  {row['class']}")
    print("\ncombined cohort")
    for label, key, suffix in (("VPIP", "vpip", "%"),
                               ("PFR", "pfr", "%"),
                               ("VPIP-PFR", "gap", "%"),
                               ("bb/100", "bb100", "")):
        value = summary[key]
        print(f"  {label:10} {'-' if value is None else f'{value:.1f}{suffix}'}")
    con.close()
    return rows, summary


# The flags that describe a player, and what each one is called in the
# `players` table.
#
# Two of these words already mean something else. `--hands` is a player's
# hand count here and the name of a VIEW in `query.py`; `--site` picks the
# pool a player belongs to here and filters situations there. Both
# collisions are reachable, which is why the parser below walks the list
# instead of searching it.
COHORT_FLAGS = {
    "--hands": "hands", "--vpip": "vpip", "--pfr": "pfr", "--gap": "gap",
    "--threebet": "threebet", "--fold-to-threebet": "fold_to_threebet",
    "--wwsf": "wwsf", "--wtsd": "wtsd", "--wsd": "wsd", "--bb100": "bb100",
}
COHORT_OPTIONS = ("--site", "--class", "--durable")


def parse_cohort(argv):
    """
    Split the Multiple Players options off a command line, positionally.

    One token at a time, because the two vocabularies share words. The old
    parser searched instead: it collected the TOKENS it had used into a set
    and then dropped every argv entry equal to one of them, which is
    filtering a positional list by value. `--cohort --hands 6 --players 6`
    deleted both sixes and left `--players` with nothing after it, and a
    second `--hands` meaning "show me the hands" was deleted along with the
    first, so the hands view could not be asked for at all under a cohort.
    Neither failure had anything to do with what was typed.

    Each of these flags is therefore read exactly once, the first time it
    appears, and every later copy is passed through untouched. That is the
    only reading under which `--cohort --hands ">=500" --hands` can mean
    what it plainly means: players with 500 hands, shown as a list of hands.
    """
    if "--cohort" not in argv:
        return None, list(argv)
    conditions, remaining, seen = [], [], set()
    site = klass = durable = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--cohort":
            i += 1
            continue
        if (a in COHORT_FLAGS or a in COHORT_OPTIONS) and a not in seen:
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value")
            value = argv[i + 1]
            seen.add(a)
            if a in COHORT_FLAGS:
                conditions.append((COHORT_FLAGS[a], value))
            elif a == "--site":
                site = value
            elif a == "--class":
                klass = value
            else:
                if value not in ("0", "1"):
                    raise SystemExit("--durable must be 0 or 1")
                durable = int(value)
            i += 2
            continue
        remaining.append(a)
        i += 1
    return (conditions, site, klass, durable), remaining


def describe_cohort(spec):
    """
    The cohort in words, for the heading over the report it narrowed.

    Without this the heading read "cohort (8 players)" over a report that
    had also been cut to one site and to players with five hundred hands,
    and a report whose heading does not say what was filtered is one that
    will eventually be read as though it covered everything. The size alone
    does not say it: eight players is the same eight whatever picked them.
    """
    conditions, site, klass, durable = spec
    said = [f"{field} {value}" for field, value in conditions]
    if site:
        said.append(f"site {site}")
    if klass:
        said.append(klass + "s")
    if durable is not None:
        said.append("named players" if durable else "session-only seats")
    return ", ".join(said) if said else "every player"


def classify(hands, vpip, pfr):
    """
    reg, fish, or unknown -- and unknown is the usual answer.

    Two ways to be a fish and they are different faults. Loose: plays more
    than a third of the hands dealt, which no winning strategy does. Passive:
    comes in often and almost never raises, which is the recreational tell
    that survives at every stake. A reg has to fail both tests with room to
    spare, so the rule cannot promote somebody merely by not having watched
    them long enough.
    """
    if not hands:
        return "unknown"
    _p, v_lo, v_hi = wilson(vpip, hands)
    _q, p_lo, p_hi = wilson(pfr, hands)
    if v_lo >= LOOSE:
        return "fish"
    if p_hi <= PASSIVE and vpip / hands >= ENTERS:
        return "fish"
    if v_hi <= TIGHT and p_lo >= RAISES:
        return "reg"
    return "unknown"


def migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    for name, kind in COLUMNS:
        if name not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {name} {kind}")
    con.commit()


def totals(con, where="1=1", params=()):
    """One row per (site, player), straight out of `spots`."""
    return con.execute(f"""
        SELECT site, player, COUNT(*) hands,
               SUM(vpip) vpip, SUM(pfr) pfr,
               SUM(threebet) tb, SUM(threebet_chance) tb_n,
               SUM(fold_to_threebet) f3, SUM(faced_threebet) f3_n,
               SUM(wwsf) wwsf, SUM(wtsd) wtsd, SUM(saw_flop) flops,
               SUM(wsd) wsd,
               SUM(CASE WHEN fmt <> 'MTT' THEN net_bb ELSE 0 END) net,
               SUM(fmt <> 'MTT') money_hands
        FROM spots
        WHERE player IS NOT NULL AND ({where})
        GROUP BY site, player""", params).fetchall()


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    migrate(con)

    rows = []
    for r in totals(con):
        n = r["hands"]
        pct = lambda k, d: (100.0 * k / d) if d else None
        rows.append((
            r["site"], r["player"],
            # A site with names names a person. An Ignition ring seat names
            # a chair, and the person in it changes without the name doing
            # so. The registry says which a site is.
            int(sites.of(r["site"]).names), n,
            pct(r["vpip"], n), pct(r["pfr"], n),
            pct(r["tb"], r["tb_n"]), pct(r["f3"], r["f3_n"]),
            pct(r["wwsf"], r["flops"]), pct(r["wtsd"], r["flops"]),
            pct(r["wsd"], r["wtsd"]),
            (100.0 * r["net"] / r["money_hands"]) if r["money_hands"] else None,
            classify(n, r["vpip"], r["pfr"])))
    con.executemany(
        "INSERT INTO players VALUES (" + ",".join("?" * 13) + ")", rows)

    stamp(con)
    con.commit()
    named = sum(1 for r in rows if r[2])
    print(f"{len(rows):,} identities ({named:,} of them people), "
          f"{sum(1 for r in rows if r[12] == 'reg'):,} regs, "
          f"{sum(1 for r in rows if r[12] == 'fish'):,} fish")
    con.close()
    return len(rows)


def stamp(con):
    """
    Put the class of the player acting, and of the one they face, on every
    decision.

    The opponent only exists while one is left: in a three-way pot there is
    no "the other player", so `vs_player` is NULL there rather than being
    filled with whichever seat happened to be first. Liveness is worked out
    by walking each hand and dropping a seat when it folds -- an all-in
    player never folds and so stays live, which is right, and is why this
    cannot be read off the action verbs alone.

    Who is in the hand comes from `spots`, one row per player per hand, and
    not from who is seen to act. Taking it from the actions loses the player
    who never gets a turn: heads up, when the small blind folds immediately,
    the big blind wins without acting and appears nowhere in `decisions` --
    3,848 decisions had no opponent recorded for exactly that reason.
    `spots` also already excludes the seats that are at the table but not in
    the hand, which is a guard this would otherwise have to repeat.
    """
    klass = {(r[0], r[1]): r[2]
             for r in con.execute("SELECT site, player, class FROM players")}

    dealt, who_is, button = {}, {}, {}
    for r in con.execute("SELECT hand_id, seat, player, position FROM spots"):
        dealt.setdefault(r["hand_id"], set()).add(r["seat"])
        who_is[(r["hand_id"], r["seat"])] = r["player"]
        # The postflop order runs clockwise from the button. Heads up the
        # small blind IS the button and acts last, and there is no BTN
        # label to find, so the small blind stands in for it.
        if r["position"] == "BTN" or (r["position"] == "SB"
                                      and r["hand_id"] not in button):
            button[r["hand_id"]] = r["seat"]

    by_hand = {}
    for r in con.execute("SELECT hand_id, n, seat, player, site, action "
                         "FROM decisions ORDER BY hand_id, n"):
        by_hand.setdefault(r["hand_id"], []).append(r)

    out = []
    for hid, acts in by_hand.items():
        live = dealt.get(hid) or {a["seat"] for a in acts}
        btn = button.get(hid, max(live))
        # A seat's place in the postflop order: 0 for the first to act after
        # the button, counting clockwise, so "after me" is a bigger number.
        top = max(live) + 1
        order = {seat: (seat - btn - 1) % top for seat in live}
        folded = set()
        for a in acts:
            here = live - folded
            other = None
            if len(here) == 2:
                other = next(iter(here - {a["seat"]}), None)
            who = who_is.get((hid, other)) if other is not None else None
            # Everybody else still in, whether there is one of them or four,
            # and which side of this player each one sits.
            mine = order.get(a["seat"], -1)
            company = [(klass.get((a["site"], who_is.get((hid, seat)))),
                        order.get(seat, -1) > mine)
                       for seat in here if seat != a["seat"]]
            out.append((
                klass.get((a["site"], a["player"])),
                who,
                klass.get((a["site"], who)) if who else None,
                other,
                sum(c == "reg" for c, _left in company),
                sum(c == "fish" for c, _left in company),
                sum(c == "fish" and left for c, left in company),
                sum(c == "fish" and not left for c, left in company),
                sum(c == "reg" and left for c, left in company),
                sum(c == "reg" and not left for c, left in company),
                a["hand_id"], a["n"]))
            if a["action"] == "F":
                folded.add(a["seat"])

    con.execute("CREATE TEMP TABLE stamped ("
                + ", ".join(f"{c} {t}" for c, t in COLUMNS)
                + ", hand_id TEXT, n INT)")
    con.executemany("INSERT INTO stamped VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out)
    con.execute("CREATE INDEX temp.stamped_key ON stamped(hand_id, n)")
    con.execute(
        "UPDATE decisions SET "
        + ", ".join(f"{c} = stamped.{c}" for c in NAMES)
        + " FROM stamped WHERE decisions.hand_id = stamped.hand_id "
          "AND decisions.n = stamped.n")
    con.execute("CREATE INDEX IF NOT EXISTS dec_class "
                "ON decisions(player_class, vs_class, n_fish, n_reg, street)")
    con.execute("CREATE INDEX IF NOT EXISTS dec_vsplayer "
                "ON decisions(vs_player)")
    # Who else is in the pot, on its own. It is in dec_class too, but third
    # in that key, and a third column cannot be seeked without the first
    # two -- so "a fish is in this pot" read every row until this existed.
    con.execute("CREATE INDEX IF NOT EXISTS dec_company "
                "ON decisions(n_fish, n_reg, street)")
    # Which side. Two small indexes rather than one wide one, because the
    # questions are "a fish on my left" and "a fish on my right" and
    # almost never both at once.
    con.execute("CREATE INDEX IF NOT EXISTS dec_left "
                "ON decisions(fish_left, reg_left, street)")
    con.execute("CREATE INDEX IF NOT EXISTS dec_right "
                "ON decisions(fish_right, reg_right, street)")
    con.execute("ANALYZE")


def show(name, db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM players WHERE player = ?", (name,)).fetchall()
    if not rows:
        print(f"nobody called {name!r}. Try: python players.py --list")
        return
    for r in rows:
        note = "" if r["durable"] else ("   (a seat, not a person -- this "
                                        "identity dies with the session)")
        print(f"\n{r['player']}   {r['site']}   {r['hands']:,} hands   "
              f"{r['class'].upper()}{note}")
        for label, key, unit in (
                ("VPIP", "vpip", "%"), ("PFR", "pfr", "%"),
                ("3-bet", "threebet", "%"), ("fold to 3-bet", "fold_to_threebet", "%"),
                ("won when saw flop", "wwsf", "%"), ("went to showdown", "wtsd", "%"),
                ("won at showdown", "wsd", "%"), ("bb/100", "bb100", "")):
            v = r[key]
            print(f"  {label:20} {'-' if v is None else f'{v:6.1f}{unit}'}")


def leaderboard(db_path=DB, klass=None, min_hands=100):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    where = "hands >= ?" + (" AND class = ?" if klass else "")
    params = (min_hands,) + ((klass,) if klass else ())
    rows = con.execute(f"SELECT * FROM players WHERE {where} "
                       f"ORDER BY hands DESC LIMIT 40", params).fetchall()
    print(f"{'player':22} {'site':9} {'hands':>7} {'VPIP':>6} {'PFR':>6} "
          f"{'WWSF':>6} {'WTSD':>6}  class")
    for r in rows:
        f = lambda v: "     -" if v is None else f"{v:6.1f}"
        print(f"{r['player'][:21]:22} {r['site']:9} {r['hands']:7,} "
              f"{f(r['vpip'])} {f(r['pfr'])} {f(r['wwsf'])} {f(r['wtsd'])}  "
              f"{r['class']}")


def check(db_path=DB):
    """
    The classes must survive the hands being split in half.

    A rule that sorts players by a number always produces classes; the
    question is whether they describe the player or the sample. So each
    player's hands are split arbitrarily in two and classified twice. One
    half saying `unknown` is not a failure -- half the hands is half the
    evidence, and refusing is what the rule is supposed to do there. A reg on
    one half and a fish on the other is a failure, and it is the only kind
    that would not show up as anything else.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fails = []

    n = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    people = con.execute("SELECT COUNT(*) FROM players WHERE durable=1").fetchone()[0]
    print(f"identities                   {n:,}  ({people:,} of them people, "
          f"{n - people:,} session-only seats)")
    if not n:
        print("\nFAIL: no players -- run `python players.py`")
        return False

    for klass in ("reg", "fish", "unknown"):
        c = con.execute("SELECT COUNT(*) FROM players WHERE class=?",
                        (klass,)).fetchone()[0]
        big = con.execute("SELECT COUNT(*) FROM players WHERE class=? "
                          "AND hands>=100", (klass,)).fetchone()[0]
        print(f"  {klass:8} {c:6,}   {big:4} of them with 100+ hands")

    # The split. Parity of the last character of the hand id is arbitrary
    # with respect to how anybody plays, which is the point.
    halves = []
    for odd in (0, 1):
        halves.append({
            (r["site"], r["player"]): (r["hands"], r["vpip"], r["pfr"])
            for r in con.execute(
                "SELECT site, player, COUNT(*) hands, SUM(vpip) vpip, "
                "SUM(pfr) pfr FROM spots WHERE player IS NOT NULL "
                "AND CAST(SUBSTR(hand_id, -1) AS INT) % 2 = ? "
                "GROUP BY site, player", (odd,))})
    whole = {(r["site"], r["player"]): r["hands"] for r in
             con.execute("SELECT site, player, hands FROM players")}

    same = soft = flipped = 0
    for key, hands in whole.items():
        if hands < 100 or key not in halves[0] or key not in halves[1]:
            continue
        a, b = (classify(*halves[i][key]) for i in (0, 1))
        if a == b:
            same += 1
        elif "unknown" in (a, b):
            soft += 1
        else:
            flipped += 1
            if flipped <= 5:
                print(f"    {key[1]}: {a} on one half, {b} on the other")
    total = same + soft + flipped
    print(f"split-half, 100+ hands       {same}/{total} identical, "
          f"{soft} refused on one half, {flipped} contradicted")
    if flipped:
        fails.append(f"{flipped} players change class between halves")

    # What the classification is actually worth: how much of the action it
    # can speak about. A rule that is right about nobody is not useful.
    told = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE player_class IN ('reg','fish')"
    ).fetchone()[0]
    tot = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    vs = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE vs_class IN ('reg','fish')"
    ).fetchone()[0]
    print(f"decisions by a known class   {told:,}/{tot:,}  ({100*told/tot:.1f}%)")
    print(f"decisions against one        {vs:,}/{tot:,}  ({100*vs/tot:.1f}%)")
    if not vs:
        fails.append("no decision has a classified opponent -- vs_class is empty")

    # The liveness walk here and `n_live` in decisions.py are two separate
    # derivations of the same fact and have to agree about when exactly one
    # opponent is left. Checked against `vs_seat` and not `vs_player`: on
    # Zone nobody has a name, so a missing name would look like a missing
    # opponent and hide a real disagreement behind 2,189 false ones.
    #
    # Hands where the two modules' ghost-seat guards disagree are counted
    # separately rather than swept in. A seat can be at the table and not in
    # the hand -- "sitting out", "waits for big blind" -- and `spots` drops
    # those while `decisions` counts them, so on those hands the two are
    # answering slightly different questions. One hand does this: a 3-handed
    # Zone hand whose whole history is the button folding, which cannot be
    # a three-player hand however it is counted.
    ghost = set(r[0] for r in con.execute(
        "SELECT s.hand_id FROM (SELECT hand_id, COUNT(*) n FROM seats "
        "GROUP BY hand_id) s JOIN (SELECT hand_id, COUNT(*) n FROM spots "
        "GROUP BY hand_id) p USING(hand_id) WHERE s.n <> p.n"))
    rows = con.execute(
        "SELECT hand_id, COUNT(*) n FROM decisions "
        "WHERE (vs_seat IS NOT NULL) <> (n_live = 2) GROUP BY hand_id"
    ).fetchall()
    bad = sum(r["n"] for r in rows if r["hand_id"] not in ghost)
    excused = sum(r["n"] for r in rows if r["hand_id"] in ghost)
    note = "" if not excused else (
        f"   ({excused} on hands where the two ghost-seat guards differ)")
    print(f"one opponent, agreed with n_live  "
          f"{tot - bad - excused:,}/{tot:,}{note}"
          f"{'' if not bad else '   <-- DISAGREE'}")
    if bad:
        fails.append(f"{bad} decisions where the liveness walk and n_live "
                     f"disagree for no reason")

    # Every fish in the pot sits on one side of the player or the other,
    # and so does every reg; a row where the sides do not add up to the
    # company is a row where the order was read wrong.
    sides = con.execute(
        "SELECT COUNT(*), SUM(fish_left + fish_right <> n_fish "
        "OR reg_left + reg_right <> n_reg) FROM decisions "
        "WHERE n_fish IS NOT NULL").fetchone()
    print(f"left + right = company            "
          f"{sides[0] - (sides[1] or 0):,}/{sides[0]:,}"
          f"{'' if not sides[1] else '   <-- DISAGREE'}")
    if sides[1]:
        fails.append(f"{sides[1]} decisions where the fish and regs on each "
                     f"side do not add up to the company")
    # And the sides mean what they say: the button, with everybody still
    # in, has nobody on its left, since it acts last.
    wrong_side = con.execute(
        "SELECT COUNT(*) FROM decisions WHERE position='BTN' AND n_live=n_players "
        "AND street<>'preflop' AND (fish_left > 0 OR reg_left > 0)").fetchone()[0]
    print(f"nobody acts after the button      "
          f"{'yes' if not wrong_side else f'NO, {wrong_side} rows'}")
    if wrong_side:
        fails.append(f"{wrong_side} button decisions with somebody on the left")

    # ---- the cohort, which nothing used to check ----------------------
    #
    # A cohort narrows every report in the program and had no test of any
    # kind. What it gets wrong is not arithmetic, it is argument splitting:
    # the vocabulary here shares two words with `query.py`, and a parser
    # that mishandles them removes somebody else's filter without saying so.
    # Both cases below were real and both were reproduced before they were
    # fixed.
    splits = [
        # A value the cohort used, appearing again as another flag's value.
        # The old parser dropped every token equal to one it had consumed,
        # so this deleted both sixes and left `--players` dangling.
        (["--cohort", "--hands", "6", "--players", "6"],
         [("hands", "6")], ["--players", "6"]),
        # `--hands` twice: the player's hand count, then the hands VIEW.
        # The old parser deleted both and the view could not be reached.
        (["--cohort", "--hands", ">=500", "--hands"],
         [("hands", ">=500")], ["--hands"]),
        # Everything that is not the cohort's survives in order.
        (["--cohort", "--vpip", ">=28", "--pos", "BTN", "--street", "flop"],
         [("vpip", ">=28")], ["--pos", "BTN", "--street", "flop"]),
        (["--pos", "BTN"], None, ["--pos", "BTN"]),
    ]
    for argv, want_conditions, want_rest in splits:
        spec, rest = parse_cohort(list(argv))
        got = None if spec is None else spec[0]
        if got != want_conditions or rest != want_rest:
            fails.append(f"parse_cohort{argv} -> {got}, {rest}")
    print(f"the cohort takes only its own flags  "
          f"{len(splits) - len([f for f in fails if 'parse_cohort' in f])}"
          f"/{len(splits)}")

    # A condition is a comparator and a number and nothing else. It reaches
    # SQL as a column name and an operator chosen from a fixed list, with
    # the number bound -- but the column and the operator are interpolated,
    # so the day that stops being true is the day this matters.
    refused = 0
    for field, value in (("hands", "; DROP TABLE players"),
                         ("hands", "500 OR 1=1"),
                         ("nonsense", ">=500")):
        try:
            cohort(con, [(field, value)])
        except ValueError:
            refused += 1
    print(f"conditions that are not conditions   {refused}/3 refused")
    if refused < 3:
        fails.append("a cohort condition that is not a comparator and a "
                     "number was accepted")

    # And it has to actually narrow. A cohort that quietly matches everybody
    # is the failure mode of every filter in this project: it returns a
    # number, and the number describes a population nobody asked for.
    everyone = len(cohort(con))
    heavy = len(cohort(con, [("hands", ">=500")]))
    named = len(cohort(con, durable=1))
    print(f"cohorts narrow                       "
          f"{heavy} with 500+ hands and {named} named, of {everyone}")
    if not everyone or heavy >= everyone or named >= everyone:
        fails.append("a cohort selects everybody, so it is not filtering")

    # The heading has to say which players, not just how many. Eight players
    # is the same eight whatever picked them.
    said = describe_cohort(([("hands", ">=500")], "acr", None, None))
    print(f"the cohort says what it is           {said!r}")
    if "hands" not in said or "acr" not in said:
        fails.append(f"describe_cohort left the filter out of {said!r}")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--cohort" in argv:
        spec, _remaining = parse_cohort(argv)
        show_cohort(*spec)
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    if "--list" in argv:
        i = argv.index("--list")
        leaderboard(klass=argv[i + 1] if i + 1 < len(argv) else None)
        return 0
    if argv and not argv[0].startswith("-"):
        show(argv[0])
        return 0
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
