"""
Turn raw hands into one row per player per hand, with the pot reconstructed.

The hands table says what happened. It does not say what it meant: that a
raise was a 3-bet, that a bet was two-thirds of the pot, that a player had
the chance to steal and passed it up. Every question worth asking about a
population -- how wide does the BB defend, what sizes does this seat use,
where is hero's money going -- needs those derived facts, and needs them the
same way each time, so they are worked out once here and written down.

Two tables come out of it:

  spots   one row per (hand, seat): the hand they held, the chances they had
          preflop and postflop, what they did with each, and the money
  bets    one row per aggressive action, with the pot as it stood before the
          action and what it cost to call -- the sizing fingerprint

Sizing is the part that cannot be read off the raw tables at all. Ignition
writes the chips a player added, never the pot, so the pot is replayed
action by action: blinds in, then each street's contributions, banked when
the street ends. That replay is also what gives "what did it cost to call",
and therefore what tells a raise from a call when a player is all in.

    python spots.py            build, or rebuild, both tables
    python spots.py --check    sanity figures, to see the derivation is sane
"""

import sqlite3
import sys
from pathlib import Path

import games
import sites

DB = Path(__file__).parent / "hands.db"

RANKS = "23456789TJQKA"

# A seat number only follows a person for as long as they stay sat down. If
# a seat goes quiet for this many hands at a table, whoever is there
# afterwards is treated as somebody new. Too small and a player who sits out
# a few hands is split into strangers; too large and two people are merged
# into one profile, which is the worse mistake when the point is to say
# "this seat plays like a bot".
SEAT_GAP = 12

STEAL_SEATS = ("CO", "BTN", "SB")

SCHEMA = """
DROP TABLE IF EXISTS spots;
DROP TABLE IF EXISTS bets;
CREATE TABLE spots (
  hand_id TEXT, seat INT, player TEXT, table_id TEXT, fmt TEXT, game TEXT,
  game_type TEXT, bb REAL,
  played_at TEXT, n_players INT, position TEXT, is_hero INT,
  cards TEXT, combo TEXT, suited INT, pair INT, hi TEXT, lo TEXT,

  pf_seq TEXT, vpip INT, pfr INT,
  rfi_chance INT, rfi INT, limped INT,
  faced_raise INT, threebet_chance INT, threebet INT, cold_call INT,
  faced_threebet INT, fold_to_threebet INT, fourbet INT,
  steal_chance INT, stole INT, faced_steal INT, fold_to_steal INT,
  open_size_bb REAL,

  folded_on TEXT, saw_flop INT, pfa INT,
  cbet_chance INT, cbet INT, faced_cbet INT, fold_to_cbet INT,
  raised_cbet INT, wtsd INT, wsd INT, wwsf INT,
  n_bet INT, n_raise INT, n_call INT, n_check INT,

  won REAL, put_in REAL, net REAL, net_bb REAL, site TEXT,
  standard INT,
  PRIMARY KEY (hand_id, seat));

CREATE TABLE bets (
  hand_id TEXT, n INT, seat INT, player TEXT, position TEXT, street TEXT,
  action TEXT, amount REAL, total REAL, pot_before REAL, to_call REAL,
  pot_frac REAL, size_bb REAL, bb REAL, is_hero INT, n_live INT, site TEXT,
  PRIMARY KEY (hand_id, n));

CREATE INDEX spots_player ON spots(player);
CREATE INDEX spots_site ON spots(site, fmt);
CREATE INDEX spots_pos ON spots(fmt, position);
CREATE INDEX spots_combo ON spots(combo);
CREATE INDEX bets_player ON bets(player);
CREATE INDEX bets_street ON bets(street, action);
"""


def identify(hands, seats_by):
    """
    Who a seat belongs to, decided once for every site.

    Sites answer this in two completely different ways, and the registry
    says which way each one takes -- the difference is the whole reason for
    having more than one.

      A site with names (ACR) writes the screen name. It is the same person
                 next week, at another table, at another stake -- including
                 on Blitz, where the table changes every hand but the name
                 does not.
      A site without (Ignition) writes nobody. A seat number persists only
                 while somebody stays sat in it, so identity is table + seat
                 + how many times that seat has turned over, and it dies
                 with the session. That only means anything at a ring
                 table: Zone moves you every hand, so a Zone seat is a
                 stranger and is left unnamed rather than given a name that
                 would silently merge hundreds of different people.
    """
    out = {}
    last_seen, segment = {}, {}
    for i, h in enumerate(hands):
        named = sites.of(h["site"]).names
        for s in seats_by.get(h["hand_id"], []):
            if named:
                out[(h["hand_id"], s["seat"])] = s["label"]
                continue
            if h["fmt"] != "RING":
                continue
            key = (h["table_id"], s["seat"])
            if key not in segment:
                segment[key] = 0
            elif i - last_seen[key] > SEAT_GAP:
                segment[key] += 1
            last_seen[key] = i
            out[(h["hand_id"], s["seat"])] = "{}:{}:{}".format(
                h["table_id"], s["seat"], segment[key])
    return out


def combo_of(cards):
    """'6d 5s' -> ('65o', suited, pair, hi, lo). Nothing but two cards reads."""
    if not cards:
        return None, 0, 0, None, None
    parts = cards.split()
    if len(parts) != 2:
        return None, 0, 0, None, None          # Omaha, or a misread
    (r1, s1), (r2, s2) = parts[0], parts[1]
    if r1 not in RANKS or r2 not in RANKS:
        return None, 0, 0, None, None
    hi, lo = (r1, r2) if RANKS.index(r1) >= RANKS.index(r2) else (r2, r1)
    if r1 == r2:
        return hi + lo, 0, 1, hi, lo
    suited = int(s1 == s2)
    return hi + lo + ("s" if suited else "o"), suited, 0, hi, lo


def with_pot(seats, actions):
    """
    The same actions, each carrying the pot before it and what it cost to call.

    Chips a player has put in on the current street are owed back to them in
    the sense that they do not have to call their own money, so the cost of
    calling is the largest contribution on the street minus your own. When a
    street ends those contributions are banked and everybody starts level.

    Preflop the blinds count as contributions rather than as dead money --
    the big blind has already paid its call. A tournament ante is not a
    contribution, but Ignition lumps antes and blinds into one "posted"
    figure and the cash games that make up nearly all of this have no antes,
    so the rare ante is counted here as though it were a blind.
    """
    street_put = {s["seat"]: (s["posted"] or 0.0) for s in seats}
    banked = 0.0
    street = "preflop"
    live = {s["seat"] for s in seats}
    out = []

    for a in actions:
        if a["street"] != street:
            banked += sum(street_put.values())
            street = a["street"]
            street_put = {s["seat"]: 0.0 for s in seats}

        mine = street_put.get(a["seat"], 0.0)
        a = dict(a)
        a["pot_before"] = round(banked + sum(street_put.values()), 4)
        a["to_call"] = round(max(max(street_put.values()) - mine, 0.0), 4)
        a["n_live"] = len(live)
        out.append(a)

        if a["action"] == "F":
            live.discard(a["seat"])
        elif a["amount"]:
            if a["total"] is not None and a["action"] in ("R", "A"):
                street_put[a["seat"]] = a["total"]
            else:
                street_put[a["seat"]] = mine + a["amount"]
    return out


def is_aggressive(a):
    """
    Whether an action put in more than it cost to call.

    Ignition writes every all-in as "All-in", whether it was a shove, a
    raise or a call for the last of a stack, and only marks "(raise)"
    sometimes. Comparing what went in against what was owed settles it
    without trusting the wording.
    """
    if a["action"] in ("B", "R"):
        return True
    if a["action"] == "A":
        return (a["amount"] or 0.0) > a["to_call"] + 1e-9
    return False


def preflop_story(seats, actions):
    """
    What chance each seat had preflop, and what they did with it.

    Everything here is defined by what had already happened when the player
    had to act, which is why it is a walk and not a set of counts. A raise
    is only a 3-bet if exactly one raise came before it; a call is only a
    limp if nothing has been raised; a seat only had the chance to steal if
    everybody before it folded.
    """
    out = {s["seat"]: {
        "pf_seq": [], "vpip": 0, "pfr": 0, "rfi_chance": 0, "rfi": 0,
        "limped": 0, "faced_raise": 0, "threebet_chance": 0, "threebet": 0,
        "cold_call": 0, "faced_threebet": 0, "fold_to_threebet": 0,
        "fourbet": 0, "steal_chance": 0, "stole": 0, "faced_steal": 0,
        "fold_to_steal": 0, "open_size_bb": None,
    } for s in seats}

    by_seat = {s["seat"]: s for s in seats}
    n_raises = 0
    entered = 0                 # players who have put money in by choice
    opener = pfa = None

    for a in actions:
        if a["street"] != "preflop":
            break
        me = out[a["seat"]]
        pos, act = a["position"], a["action"]
        me["pf_seq"].append(act)

        # The chance, judged on the table as it stood before the action.
        unopened = n_raises == 0 and entered == 0
        if unopened:
            me["rfi_chance"] = 1
            # Folded round to a late seat. The cutoff counts as a steal spot
            # for the same reason the button does: it is the same decision
            # with one more seat still to get through.
            if pos in STEAL_SEATS:
                me["steal_chance"] = 1
        elif n_raises == 1:
            me["faced_raise"] = 1
            if a["seat"] != opener:
                me["threebet_chance"] = 1
            if opener is not None and by_seat[opener]["position"] in STEAL_SEATS \
                    and pos in ("SB", "BB") and entered <= 1:
                me["faced_steal"] = 1
        elif n_raises >= 2:
            me["faced_raise"] = 1
            if a["seat"] == opener:
                me["faced_threebet"] = 1

        # What they did with it.
        if is_aggressive(a):
            me["pfr"] = me["vpip"] = 1
            if n_raises == 0:
                if unopened:
                    me["rfi"] = 1
                    me["stole"] = me["steal_chance"]
                me["open_size_bb"] = a["total"] or a["amount"]
                opener = a["seat"]
            elif n_raises == 1:
                me["threebet"] = 1
            elif me["faced_threebet"]:
                me["fourbet"] = 1
            n_raises += 1
            entered += 1
            pfa = a["seat"]
        elif act in ("C", "A"):
            me["vpip"] = 1
            if n_raises == 0:
                me["limped"] = 1
            elif me["threebet_chance"]:
                me["cold_call"] = 1
            entered += 1
        elif act == "F":
            me["fold_to_threebet"] = me["faced_threebet"]
            me["fold_to_steal"] = me["faced_steal"]

    return out, pfa


def postflop_story(actions, pfa):
    """
    Who folded when, and who was on which side of the continuation bet.

    A bet is only a continuation bet if the preflop raiser is the one who
    made it and nothing had been bet on the flop before it; everyone still
    in when that lands is facing it, whatever they do next.
    """
    folded_on, counts = {}, {}
    first_bettor, cbet_made = None, False
    faced, folded, raised = set(), set(), set()

    for a in actions:
        c = counts.setdefault(a["seat"], {"B": 0, "R": 0, "C": 0, "X": 0})
        if a["action"] in c:
            c[a["action"]] += 1
        elif a["action"] == "A":
            c["B" if is_aggressive(a) else "C"] += 1
        if a["action"] == "F" and a["seat"] not in folded_on:
            folded_on[a["seat"]] = a["street"]

        if a["street"] != "flop":
            continue
        if first_bettor is None:
            if is_aggressive(a) and a["to_call"] == 0:
                first_bettor = a["seat"]
                cbet_made = a["seat"] == pfa
        elif cbet_made and a["seat"] != first_bettor and a["to_call"] > 0:
            faced.add(a["seat"])
            if a["action"] == "F":
                folded.add(a["seat"])
            elif is_aggressive(a):
                raised.add(a["seat"])

    return folded_on, counts, cbet_made, faced, folded, raised


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    # Every variant. Hold'em was the filter so Omaha never reached a
    # combo or a strength label; those still refuse four cards, and the
    # pool filters pin themselves to `games.HOLD` so a PLO VPIP cannot
    # silently rewrite a Hold'em one.
    hands = con.execute(
        "SELECT * FROM hands ORDER BY played_at, hand_id"
    ).fetchall()
    seats_by, acts_by = {}, {}
    for r in con.execute("SELECT * FROM seats ORDER BY hand_id, seat"):
        seats_by.setdefault(r["hand_id"], []).append(dict(r))
    for r in con.execute("SELECT * FROM actions ORDER BY hand_id, n"):
        acts_by.setdefault(r["hand_id"], []).append(dict(r))

    seat_ids = identify(hands, seats_by)

    spot_rows, bet_rows = [], []
    for h in hands:
        hid, bb, site = h["hand_id"], h["bb"], h["site"]
        acts = acts_by.get(hid, [])
        # A seat can be at the table without being in the hand -- sitting
        # out, waiting for the big blind, disconnected. It never acts and it
        # never folds, so left in it reaches every showdown it is dealt
        # into, and the pool's showdown rate rises for no reason. The seat
        # list is the room; the hand is whoever put money in or acted.
        acted = {a["seat"] for a in acts}
        seats = [s for s in seats_by.get(hid, [])
                 if s["seat"] in acted or s["posted"] or s["is_hero"]]
        if len(seats) < 2:
            continue
        actions = with_pot(seats, acts)

        pf, pfa = preflop_story(seats, actions)
        folded_on, counts, cbet_made, faced_cbet, folded_cbet, raised_cbet = \
            postflop_story(actions, pfa)

        # A player who posted a blind and then never acted did not fold and
        # did not play on -- the history simply stops mentioning them, which
        # a tournament disconnect does. Left as live they see every flop and
        # reach every showdown, so they are treated as out of the hand from
        # the point they stopped making decisions.
        for gone in {x["seat"] for x in seats} - {a["seat"] for a in actions}:
            folded_on[gone] = "preflop"

        # Whether a flop was dealt at all decides which of these mean
        # anything: with everybody folding preflop there is no continuation
        # bet to have passed up.
        flop_dealt = any(a["street"] == "flop" for a in actions)
        non_folders = [s["seat"] for s in seats if s["seat"] not in folded_on]
        showdown = flop_dealt and len(non_folders) >= 2

        for s in seats:
            p = pf[s["seat"]]
            combo, suited, pair, hi, lo = combo_of(s["cards"])
            saw_flop = int(flop_dealt and folded_on.get(s["seat"]) != "preflop")
            is_pfa = int(s["seat"] == pfa)
            c = counts.get(s["seat"], {"B": 0, "R": 0, "C": 0, "X": 0})
            put_in = (s["posted"] or 0.0) + (s["invested"] or 0.0)
            net = round((s["won"] or 0.0) - put_in, 4)
            wtsd = int(showdown and s["seat"] in non_folders)
            spot_rows.append((
                hid, s["seat"], seat_ids.get((hid, s["seat"])), h["table_id"],
                h["fmt"], h["game"], games.type_id(h["game"], h["fmt"]),
                bb, h["played_at"], h["n_players"], s["position"],
                s["is_hero"], s["cards"], combo, suited, pair, hi, lo,

                "".join(p["pf_seq"]), p["vpip"], p["pfr"],
                p["rfi_chance"], p["rfi"], p["limped"],
                p["faced_raise"], p["threebet_chance"], p["threebet"],
                p["cold_call"], p["faced_threebet"], p["fold_to_threebet"],
                p["fourbet"], p["steal_chance"], p["stole"], p["faced_steal"],
                p["fold_to_steal"],
                round(p["open_size_bb"] / bb, 3) if p["open_size_bb"] and bb else None,

                folded_on.get(s["seat"]), saw_flop, is_pfa,
                int(is_pfa and saw_flop), int(is_pfa and cbet_made),
                int(s["seat"] in faced_cbet), int(s["seat"] in folded_cbet),
                int(s["seat"] in raised_cbet),
                wtsd, int(wtsd and (s["won"] or 0) > 0),
                int(saw_flop and (s["won"] or 0) > 0),
                c["B"], c["R"], c["C"], c["X"],

                s["won"], round(put_in, 4), net,
                round(net / bb, 3) if bb else None, site, h["standard"]))

        heroes = {s["seat"] for s in seats if s["is_hero"]}
        for a in actions:
            if not is_aggressive(a):
                continue
            pot_before, to_call = a["pot_before"], a["to_call"]
            amount, total = a["amount"] or 0.0, a["total"]
            if to_call > 0:
                # A raise, measured the way a solver states one: what you put
                # in on top of the call, over the pot you would call into.
                top = (total or (amount + to_call)) - to_call
                pot_frac = top / (pot_before + to_call) if pot_before + to_call else None
            else:
                pot_frac = amount / pot_before if pot_before else None
            bet_rows.append((
                hid, a["n"], a["seat"], seat_ids.get((hid, a["seat"])),
                a["position"], a["street"], a["action"], amount, total,
                pot_before, to_call,
                round(pot_frac, 4) if pot_frac is not None else None,
                round((total or amount) / bb, 3) if bb else None,
                bb, int(a["seat"] in heroes), a["n_live"], site))

    # Placeholders counted off the table itself. Writing the number by hand
    # is how a column added to the schema turns into an insert that fails
    # only once the whole build has run.
    def insert(table, rows):
        n = len(con.execute("SELECT * FROM {} LIMIT 0".format(table)).description)
        con.executemany("INSERT INTO {} VALUES ({})".format(
            table, ",".join("?" * n)), rows)

    insert("spots", spot_rows)
    insert("bets", bet_rows)
    con.commit()
    con.close()
    return len(spot_rows), len(bet_rows)


def check(db_path=DB):
    """Figures with a known shape, so a broken derivation shows up as nonsense."""
    con = sqlite3.connect(db_path)
    one = lambda s, *a: con.execute(s, a).fetchone()

    print("spots {}   bets {}\n".format(
        *one("SELECT (SELECT COUNT(*) FROM spots), (SELECT COUNT(*) FROM bets)")))

    print("population, 5-6 handed ring -- these should look like micro stakes:")
    for label, num, den in (
            ("VPIP", "vpip", "1"), ("PFR", "pfr", "1"),
            ("RFI", "rfi", "rfi_chance"), ("3bet", "threebet", "threebet_chance"),
            ("fold to 3bet", "fold_to_threebet", "faced_threebet"),
            ("fold to steal", "fold_to_steal", "faced_steal"),
            ("cbet flop", "cbet", "cbet_chance"),
            ("fold to cbet", "fold_to_cbet", "faced_cbet"),
            ("WTSD", "wtsd", "saw_flop"), ("W$SD", "wsd", "wtsd")):
        # Per site. Averaging two pools gives a figure that describes
        # neither, and the whole point of these lines is that a broken
        # derivation shows up as a number with the wrong shape.
        cells = []
        for site in sites.KEYS:
            got, tot = one("SELECT SUM({}), SUM({}) FROM spots WHERE "
                           "fmt='RING' AND n_players>=5 AND {} AND site='{}'".format(
                               num, den, games.HOLD, site))
            got, tot = got or 0, tot or 0
            cells.append("{:5.1f}% n={:<6d}".format(
                100 * got / tot if tot else 0, tot))
        print("  {:14} {}".format(label, "   ".join(cells)))

    for site in sites.KEYS:
        print("\nopen sizes actually used, {} ring:".format(site))
        for size, cnt in con.execute(
                "SELECT ROUND(open_size_bb,1), COUNT(*) FROM spots WHERE rfi=1 "
                "AND fmt='RING' AND game='HOLDEM' AND site=? GROUP BY 1 "
                "ORDER BY 2 DESC LIMIT 8", (site,)):
            print("  {}bb  {}".format(size, cnt))

    print("\nflop bet sizes as a fraction of pot:")
    for lo, hi in ((0, .3), (.3, .45), (.45, .6), (.6, .8), (.8, 1.1), (1.1, 99)):
        print("  {:4.2f}-{:4.2f}  {}".format(lo, hi, one(
            "SELECT COUNT(*) FROM bets WHERE street='flop' AND to_call=0 "
            "AND pot_frac>=? AND pot_frac<?", lo, hi)[0]))

    for site in sites.KEYS:
        print("\nhero, by position, {} ring (bb/100):".format(site))
        for pos, n, bb100 in con.execute(
                "SELECT position, COUNT(*), 100.0*SUM(net_bb)/COUNT(*) FROM spots "
                "WHERE is_hero=1 AND fmt='RING' AND game='HOLDEM' AND site=? "
                "GROUP BY position ORDER BY 3", (site,)):
            print("  {:4} {:5d}  {:+8.1f}".format(pos, n, bb100))

    print("\nseat identities that lasted long enough to profile:")
    for band, players in con.execute(
            "SELECT CASE WHEN c>=200 THEN '200+' WHEN c>=100 THEN '100-199' "
            "WHEN c>=50 THEN '50-99' WHEN c>=25 THEN '25-49' ELSE 'under 25' END, "
            "COUNT(*) FROM (SELECT player, COUNT(*) c FROM spots "
            "WHERE player IS NOT NULL AND is_hero=0 GROUP BY player) "
            "GROUP BY 1 ORDER BY 1 DESC"):
        print("  {:9} {}".format(band, players))
    con.close()


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        n, b = build()
        print("{} spots, {} bets\n".format(n, b))
        check()
