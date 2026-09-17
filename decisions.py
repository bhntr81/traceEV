"""
One row per decision, carrying everything that was true when it was made.

`spots` answers questions somebody thought of in advance. It has a column
for fold-to-cbet because fold-to-cbet was on the list the day it was
written, and no column for a delayed turn probe because that was not. Every
new question means editing the derivation and rebuilding tens of thousands
of rows -- which is why seven analysis modules each hardcode their own SQL,
and why none of them can be asked something new.

A tracker works the other way round. It records the *situation* -- street,
pot type, who raised last, what it costs, who is in position, how many are
left, what the flop looks like -- and a statistic is then nothing but two
filters over those situations: the times the spot arose, and the times the
player did the thing. Fold-to-cbet and delayed-turn-probe become the same
kind of object, and a new stat is a line of definition instead of a rebuild.

This table is deliberately wider than any one question needs, because its
columns are the vocabulary every future question has to be phrased in.

    python decisions.py            build, or rebuild
    python decisions.py --index    just the indexes, on a table already built
    python decisions.py --check    cross-check against spots, PASS or FAIL
"""

import sqlite3
import sys
from pathlib import Path

import sites
from equity import completing
from spots import combo_of, is_aggressive, with_pot

DB = Path(__file__).parent / "hands.db"

RANKS = "23456789TJQKA"

SCHEMA = """
DROP TABLE IF EXISTS decisions;
CREATE TABLE decisions (
  hand_id TEXT, n INT, street TEXT,
  seat INT, player TEXT, is_hero INT, site TEXT,
  table_id TEXT, fmt TEXT, bb REAL, played_at TEXT, n_players INT,
  standard INT, position TEXT, cards TEXT, combo TEXT, board TEXT,

  -- the state in front of the player when it was their turn
  pot_before REAL, to_call REAL, pot_bb REAL, to_call_bb REAL,
  stack_before REAL, eff_stack REAL, eff_bb REAL, spr REAL,
  n_live INT, n_opp INT,

  -- who they are in this hand
  pot_type TEXT, is_pfa INT, prev_agg INT, is_ip INT,
  first_in INT, checked_to INT, was_agg INT, acted_before INT,
  vs_pfa INT, vs_pos TEXT, vs_hero INT, opener_pos TEXT, pfa_pos TEXT,

  -- what they are answering
  facing TEXT, street_agg INT,

  -- what they did
  action TEXT, agg INT, allin INT, amount REAL, total REAL,
  pot_frac REAL, size_bb REAL,

  -- the flop, described the way a filter wants to ask about it
  fl_paired INT, fl_mono INT, fl_twotone INT, fl_conn INT, fl_hi TEXT,

  -- and what each later card did to it. NULL until that card is out, for
  -- the same reason the flop's texture is NULL before the flop: a decision
  -- made on the flop was not made on a board the turn had paired.
  tn_over INT, tn_pair INT, tn_flush INT, tn_straight INT,
  rv_over INT, rv_pair INT, rv_flush INT, rv_straight INT,

  -- the pot's matchup, which is a fact about the hand and not about the
  -- moment: "BTN/BB" when the button opened, the big blind alone put
  -- money in behind it, and everybody else folded. NULL for a limped pot,
  -- a multiway one, or an open nobody answered. `mu_vs_hero` says whether
  -- the other seat of it is hero.
  matchup TEXT, mu_vs_hero INT,

  -- a raise as a multiple of the bet it raised: 3.0 for a 3x, the one
  -- sizing the others cannot express, since pot_frac is over the pot and
  -- size_bb is absolute. NULL for anything that was not a raise.
  raise_x REAL,

  -- whether hero is among the opponents still in the pot at this moment.
  -- `vs_hero` says it only when one opponent is left, so "the pool
  -- against the pool" read as "the pool's heads-up decisions against
  -- somebody else" and put UTG's VPIP at 68%: every open that was called
  -- was in, every open that was not was out. This is never NULL for a
  -- seat that has an opponent, and 0 on hero's own rows.
  hero_in INT,

  PRIMARY KEY (hand_id, n));
"""

# The indexes, kept out of the schema so that an existing database can be
# given them without being rebuilt -- two minutes of derivation to acquire
# a B-tree is a bad trade, and one nobody makes, so the indexes quietly
# never arrive.
#
# Which ones is a measured question, not a guessed one. Of twenty-two
# filters the window can actually produce, thirteen scanned the whole table
# before these; three do afterwards, and each of those three has a reason
# it cannot be helped. The five below were chosen by running that workload
# with each candidate present and absent:
#
#   dec_when    a date range is how the graph is drawn and how a session is
#               looked at, and it scanned            146ms -> 3.9ms
#   dec_hero    hero-or-pool is the most-used filter there is, and by
#               itself it scanned the whole table    507ms -> 3.1ms
#   dec_board   the texture flags are five columns nothing indexed
#                                                    175ms -> 0.4ms
#   dec_combo   169 values, so it is very selective  156ms -> 0.3ms
#   dec_allin   an all-in is 0.9% of decisions, which is what makes a
#               PARTIAL index right: it holds only the 833 rows that are
#               one, so it costs almost nothing     178ms -> 0.5ms
#   dec_depth   stack depth and how many are live are ranges, and a range
#               that selects most of the table cannot be seeked -- but
#               reading a two-column index end to end still beats reading
#               a fifty-column table end to end     256ms -> 3.9ms,
#               and --multiway 224ms -> 13ms
#
#   dec_flags   the nine yes/no switches. None of them narrows anything --
#               "in position" is half the table -- so none can be seeked,
#               and the gain is entirely that seven small columns are read
#               instead of fifty                    366ms -> 1.7ms
#   dec_game    stake and table size                223ms -> 6.4ms
#   dec_size    bet sizes as a fraction of the pot. `street` is in it for
#               one reason: without it the overbet filter fetched a row per
#               match and took 622ms, and with it the index answers the
#               whole question                      194ms -> 31ms
#   dec_runout  the eight flags for what the turn and river did, for the
#               same reason as dec_board: eight small columns beside fifty
#
# Three of these were measured against a database they had been created in
# by hand and never written down here, so a rebuild silently lost them and
# twenty-eight filters went back to reading every row. The plan check caught
# it on the next run, which is the whole reason it asserts plans rather than
# times -- at ninety thousand rows the difference is invisible.
#
# Two more were built, measured and thrown away. A wide covering index over
# the whole situation fixed no query these do not, and cost 7MB. An index
# shaped exactly for the stat predicates made every quick filter's COUNT
# five times faster and the stats table itself **15% slower**, reproducibly:
# when a filter selects most of the rows, seeking an index and fetching each
# row costs more than reading the table straight through. That table was
# made fast by scanning once instead of thirty times -- see `stats.rates`.
INDEXES = """
CREATE INDEX IF NOT EXISTS dec_player ON decisions(player, site);
CREATE INDEX IF NOT EXISTS dec_spot ON decisions(street, pot_type, facing);
CREATE INDEX IF NOT EXISTS dec_pos ON decisions(site, fmt, position, street);
CREATE INDEX IF NOT EXISTS dec_vs ON decisions(position, vs_pos, pot_type);
CREATE INDEX IF NOT EXISTS dec_hand ON decisions(hand_id);
CREATE INDEX IF NOT EXISTS dec_when ON decisions(played_at);
CREATE INDEX IF NOT EXISTS dec_hero ON decisions(is_hero, street, pot_type);
CREATE INDEX IF NOT EXISTS dec_board
    ON decisions(fl_mono, fl_paired, fl_twotone, fl_conn, fl_hi, street);
CREATE INDEX IF NOT EXISTS dec_combo ON decisions(combo);
CREATE INDEX IF NOT EXISTS dec_allin ON decisions(allin) WHERE allin = 1;
CREATE INDEX IF NOT EXISTS dec_depth ON decisions(eff_bb, n_live);
CREATE INDEX IF NOT EXISTS dec_flags
    ON decisions(is_ip, is_pfa, agg, vs_pfa, vs_hero, standard, street);
CREATE INDEX IF NOT EXISTS dec_game ON decisions(bb, n_players, fmt);
-- "vs hero" is the other seat at the moment, or failing that the other
-- seat of the pot's matchup; written as an OR of the two so that each
-- half seeks this index, where a COALESCE over them read the whole table.
CREATE INDEX IF NOT EXISTS dec_other ON decisions(hero_in, is_hero, street);
-- The sizing ranges each seek their own column: a range on the second
-- column of a compound index is a scan, so one index each.
CREATE INDEX IF NOT EXISTS dec_size_bb ON decisions(size_bb) WHERE size_bb IS NOT NULL;
CREATE INDEX IF NOT EXISTS dec_raise_x ON decisions(raise_x) WHERE raise_x IS NOT NULL;
CREATE INDEX IF NOT EXISTS dec_spr ON decisions(spr);
CREATE INDEX IF NOT EXISTS dec_size
    ON decisions(pot_frac, to_call, pot_before, street);
CREATE INDEX IF NOT EXISTS dec_runout
    ON decisions(tn_over, tn_pair, tn_flush, tn_straight,
                 rv_over, rv_pair, rv_flush, rv_straight, street);
"""


def index(db_path=DB, con=None):
    """
    Build the indexes, and tell SQLite how big each one is.

    ANALYZE is the second half and is not optional. Without it the planner
    guesses at how selective each index is and sometimes guesses badly: a
    3-bet pot filtered by hero and by an exact flop line picked the hero
    index over the far narrower line index, and took 61ms instead of 37ms.
    It reads the indexes once and writes a few kilobytes.
    """
    own = con is None
    con = con or sqlite3.connect(db_path)
    con.executescript(INDEXES)
    con.execute("ANALYZE")
    con.commit()
    if own:
        con.close()


def flop_texture(board):
    """The three flop cards as four flags and a high card, or nothing."""
    cards = (board or "").split()
    if len(cards) < 3:
        return (None,) * 5
    ranks = [c[0] for c in cards[:3]]
    suits = [c[1] for c in cards[:3]]
    if any(r not in RANKS for r in ranks):
        return (None,) * 5
    idx = sorted(RANKS.index(r) for r in ranks)
    paired = int(len(set(ranks)) < 3)
    # Connected in the sense that changes how a flop plays: three cards
    # inside a five-card window can make a straight with two more. A pair on
    # the board is not connectedness.
    return (paired, int(len(set(suits)) == 1), int(len(set(suits)) == 2),
            int(not paired and idx[2] - idx[0] <= 4), RANKS[idx[2]])


def one_card(before, card):
    """
    What a single new card did to the board in front of it.

    Four facts, and "brick" is the absence of all four rather than a fifth
    column, because a card that does nothing is defined by what it did not
    do and a column for it would have to be kept in step with the others.

    A flush card is one that takes some suit to three on the board -- three
    is where a flush becomes possible and where people start playing as
    though it has. Straight is asymmetric between the streets on purpose:
    on the turn it means the board is now one card away from a straight,
    and on the river it means that card came. Those are the meaningful
    events on their respective streets, and a single definition covering
    both would describe neither.
    """
    ranks = [RANKS.index(c[0]) for c in before]
    suits = [c[1] for c in before]
    r, suit = RANKS.index(card[0]), card[1]

    over = int(bool(ranks) and r > max(ranks))
    paired = int(r in ranks)
    flush = int(suits.count(suit) + 1 >= 3)
    if len(before) == 3:
        # The turn: did it bring the board to one card off a straight?
        straight = int(bool(completing(ranks + [r])) and not completing(ranks))
    else:
        # The river: did it bring the card the board was waiting for?
        straight = int(r in completing(ranks))
    return (over, paired, flush, straight)


def runout(board, street):
    """The turn's four facts then the river's, blank until each card is out."""
    cards = (board or "").split()
    blank = (None,) * 4
    turn = (one_card(cards[:3], cards[3])
            if street in ("turn", "river") and len(cards) >= 4 else blank)
    river = (one_card(cards[:4], cards[4])
             if street == "river" and len(cards) >= 5 else blank)
    return turn + river


def board_to(board, street):
    """The board as it stood on that street -- not the runout after it."""
    cards = (board or "").split()
    return " ".join(cards[:{"preflop": 0, "flop": 3, "turn": 4, "river": 5}[street]])


def build(db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    hands = con.execute(
        "SELECT * FROM hands WHERE game='HOLDEM' ORDER BY played_at, hand_id"
    ).fetchall()
    seats_by, acts_by = {}, {}
    for r in con.execute("SELECT * FROM seats ORDER BY hand_id, seat"):
        seats_by.setdefault(r["hand_id"], []).append(dict(r))
    for r in con.execute("SELECT * FROM actions ORDER BY hand_id, n"):
        acts_by.setdefault(r["hand_id"], []).append(dict(r))
    # Identity is settled once, in spots.identify, for both sites. Deriving
    # it a second time here would be a second answer to the same question,
    # and the two would drift apart the first time either changed.
    who = {(r[0], r[1]): r[2]
           for r in con.execute("SELECT hand_id, seat, player FROM spots")}

    rows = []
    for h in hands:
        hid, bb = h["hand_id"], h["bb"] or 0.0
        seats, acts = seats_by.get(hid, []), acts_by.get(hid, [])
        if not seats or not acts:
            continue
        site = h["site"]
        by_seat = {s["seat"]: s for s in seats}
        actions = with_pot(seats, acts)
        texture = flop_texture(h["board"])

        # Acting order after the flop. Taken from the flop itself when there
        # was one, because that is what happened rather than a rule about
        # what usually happens; rotated from the preflop order otherwise,
        # the blinds acting first once the cards are out.
        pf_order = list(dict.fromkeys(
            a["seat"] for a in actions if a["street"] == "preflop"))
        fl_order = list(dict.fromkeys(
            a["seat"] for a in actions if a["street"] == "flop"))
        order = fl_order or (pf_order[-2:] + pf_order[:-2])
        rank_of = {s: i for i, s in enumerate(order)}

        put_in = {s["seat"]: (s["posted"] or 0.0) for s in seats}
        # What each seat had put in when the street began, so that the
        # street's own contribution -- what a raise is measured against --
        # can be told from the hand's running total.
        at_start = {}
        live = {s["seat"] for s in seats}
        street, street_agg, last_agg = "preflop", 0, None
        # Who has already acted on this street, and who has already been
        # aggressive on it. Preflop there is no "preflop aggressor" yet --
        # that only exists once the street is over -- so the original raiser
        # facing a 3bet has to be identified as somebody who already raised.
        acted_this, agg_this = set(), set()
        pf_raises, pfa = 0, None
        opener = None       # seat of the first player to raise preflop
        prev_street_agg, checked_through, first_of_street = None, 0, True
        # The rows of this hand, held back until the preflop has played
        # out, because the matchup stamped on every one of them is known
        # only then. And who put money in by choice before the open (a
        # limp) and after it (a call or a raise): a matchup is exactly one
        # of the second and none of the first.
        hand_rows, limped, answered = [], set(), []
        r_x = {}                # raise_x by decision number, stamped at the end
        h_in = {}               # hero_in, likewise

        for a in actions:
            if a["street"] != street:
                if street == "preflop":
                    pfa = last_agg
                prev_street_agg = last_agg
                checked_through = int(street_agg == 0 and street != "preflop")
                street, street_agg, last_agg = a["street"], 0, None
                at_start = dict(put_in)
                acted_this, agg_this = set(), set()
                first_of_street = True

            seat = a["seat"]
            s = by_seat.get(seat, {})
            pot, call = a["pot_before"], a["to_call"]
            contributed = put_in.get(seat, 0.0)
            stack = (s.get("stack") or 0.0) - contributed
            opp = [x for x in live if x != seat]
            # The stack that matters is the one that can actually be lost,
            # so it is capped by the biggest opponent still in the hand.
            eff = min(stack, max(((by_seat[x].get("stack") or 0.0)
                                  - put_in.get(x, 0.0)) for x in opp)) \
                if opp else stack

            # What they are answering. Preflop a blind is not aggression, so
            # the ladder counts raises: unopened, an open, a 3bet, a 4bet.
            # After the flop the first bet IS aggression and the ladder
            # starts one rung lower.
            if street == "preflop":
                facing = ("unopened", "open", "3bet", "4bet")[street_agg] \
                    if street_agg < 4 else "5bet+"
            else:
                facing = ("check", "bet", "raise", "3bet")[min(street_agg, 3)]

            if street != "preflop" and pf_raises == 0:
                pot_type = "limped"
            else:
                pot_type = ("unopened", "raised", "3bet", "4bet")[pf_raises] \
                    if pf_raises < 4 else "5bet+"

            agg = int(is_aggressive(a))
            amount, total = a["amount"] or 0.0, a["total"]
            # Whether this action put the last chip in. ACR says so
            # outright; Ignition writes an all-in RAISE as an ordinary raise
            # and reserves "All-in" for its shoves, so on that site it has to
            # be worked out -- and it must be, because an all-in player takes
            # no further decisions and otherwise looks like one who declined
            # to act on every street that followed.
            is_raise = a["action"] in ("R", "A") and total is not None
            went_in = (total - contributed) if is_raise else amount
            said = a["allin"] if "allin" in a and a["allin"] is not None else 0
            allin = int(bool(said) or stack - went_in <= 0.005)
            pot_frac = raise_x = None
            if agg:
                if call > 0:
                    # A raise measured the way a solver states one: what goes
                    # in on top of the call, over the pot you would call into.
                    top = (total or (amount + call)) - call
                    pot_frac = top / (pot + call) if pot + call else None
                    # And as a multiple of the bet faced, which is how a
                    # raise is said at the table: the raise-to over what
                    # the raiser would have had to put in to call.
                    mine = contributed - at_start.get(seat, 0.0)
                    faced = mine + call
                    raise_to = total or (mine + amount)
                    raise_x = round(raise_to / faced, 3) if faced > 0 else None
                else:
                    pot_frac = amount / pot if pot else None

            hand_rows.append((
                hid, a["n"], street, seat, who.get((hid, seat)),
                s.get("is_hero"), site, h["table_id"], h["fmt"], h["bb"],
                h["played_at"], h["n_players"], h["standard"], a["position"],
                s.get("cards"), combo_of(s.get("cards"))[0],
                board_to(h["board"], street),

                pot, call,
                round(pot / bb, 3) if bb else None,
                round(call / bb, 3) if bb else None,
                round(stack, 4), round(eff, 4),
                round(eff / bb, 2) if bb else None,
                round(eff / pot, 3) if pot else None,
                len(live), len(opp),

                pot_type,
                None if street == "preflop" else int(
                    pfa is not None and seat == pfa),
                int(prev_street_agg is not None and seat == prev_street_agg),
                None if street == "preflop" else int(
                    rank_of.get(seat, -1) == max(
                        (rank_of.get(x, -1) for x in live), default=-1)),
                int(first_of_street), checked_through,
                int(seat in agg_this), int(seat in acted_this),
                # Whether the bet in front of them is the preflop raiser's.
                # It is the difference between folding to a continuation bet
                # and folding to a donk bet, and those are not one stat.
                int(last_agg is not None and pfa is not None
                    and last_agg == pfa),

                # Who this decision is AGAINST. A tracker question is almost
                # never "how does the big blind play" -- it is "how does the
                # big blind play against a button open, and is that button
                # me or one of them", and neither half of that can be asked
                # unless the other seat is named on the row.
                #
                # `vs_pos` is filled only when exactly one opponent is still
                # live, because with two of them "the opponent" is not a
                # thing that exists, and picking one would put a real name on
                # an invented matchup.
                (by_seat[opp[0]]["position"]
                 if len(opp) == 1 and opp[0] in by_seat else None),
                (int(bool(by_seat[opp[0]]["is_hero"]))
                 if len(opp) == 1 and opp[0] in by_seat else None),
                (by_seat[opener]["position"]
                 if opener is not None and opener in by_seat else None),
                # Preflop this is whoever raised most recently, which is the
                # player being answered. Afterwards it is the preflop
                # aggressor, which is who the street is played against.
                (by_seat[pfa if street != "preflop" else last_agg]["position"]
                 if (pfa if street != "preflop" else last_agg) in by_seat
                 else None),

                facing, street_agg,

                a["action"], agg, allin, amount, total,
                round(pot_frac, 4) if pot_frac is not None else None,
                round((total or amount) / bb, 3) if bb and (total or amount) else None,
                # The flop's texture belongs only to decisions made once the
                # flop was down. Stamped on preflop rows as well it reads as
                # "this preflop decision was made on a monotone flop", which
                # is information from the future -- and a filter for monotone
                # flops then selects preflop folds in hands that happened to
                # run out monotone. That returned 194 hands of which 69 had
                # seen a flop at all.
                *(texture if street != "preflop" else (None,) * 5),
                *runout(h["board"], street)))

            r_x[a["n"]] = raise_x
            h_in[a["n"]] = int(any(by_seat[x].get("is_hero") for x in opp
                                   if x in by_seat))
            first_of_street = False
            acted_this.add(seat)
            if agg:
                agg_this.add(seat)
            if a["action"] == "F":
                live.discard(seat)
            elif a["amount"]:
                # A raise names the street total; everything else names what
                # was added. Getting this backwards makes every later pot in
                # the hand the wrong size.
                if total is not None and a["action"] in ("R", "A"):
                    put_in[seat] = max(total, contributed + amount)
                else:
                    put_in[seat] = contributed + amount
            if street == "preflop" and a["action"] in ("C", "R", "A") and a["amount"]:
                if opener is None and not agg:
                    limped.add(seat)
                elif opener is not None and seat != opener and seat not in answered:
                    answered.append(seat)
            if agg:
                street_agg += 1
                last_agg = seat
                if street == "preflop":
                    pf_raises += 1
                    if opener is None:
                        opener = seat

        # "BTN vs BB" means the button raised and the big blind did not
        # fold -- the user's words, and the shape every tracker gives the
        # question. It is a property of the pot, so it goes on every row of
        # the hand; `vs_pos` beside it is the opponent at the moment of the
        # decision, which preflop an open does not have.
        matchup, pair = None, ()
        if opener is not None and not limped and len(answered) == 1:
            other = answered[0]
            if opener in by_seat and other in by_seat:
                matchup = f"{by_seat[opener]['position']}/{by_seat[other]['position']}"
                pair = (opener, other)
        for r in hand_rows:
            seat_of_row = r[3]                      # seat is the fourth column
            # For the two seats of the matchup: is the OTHER one hero. The
            # seats that folded around it get nothing, so that "vs hero"
            # never selects a fold that was made against nobody.
            other_is_hero = None
            if seat_of_row in pair:
                other_is_hero = int(any(by_seat[x].get("is_hero")
                                        for x in pair if x != seat_of_row))
            rows.append(r + (matchup, other_is_hero, r_x.get(r[1]),
                             h_in.get(r[1])))

    n = len(con.execute("SELECT * FROM decisions LIMIT 0").description)
    con.executemany(
        "INSERT INTO decisions VALUES ({})".format(",".join("?" * n)), rows)
    con.commit()
    index(con=con)
    con.close()
    return len(rows)


def check(db_path=DB):
    """
    Does the new table say the same things the old one says?

    Only the stats `spots` already knows can be checked this way, and that
    is the point -- the ones it does not know are exactly what this table is
    for, and they have nothing to be checked against. So the agreement on
    the overlap is the whole of the evidence, and it has to be exact rather
    than close.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fails = []

    n_dec = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    n_act = con.execute(
        "SELECT COUNT(*) FROM actions a JOIN hands h USING(hand_id) "
        "WHERE h.game='HOLDEM'").fetchone()[0]
    print(f"every action is a decision   {n_dec}/{n_act}"
          f"{'' if n_dec == n_act else '   <-- rows lost'}")
    if n_dec != n_act:
        fails.append("coverage")

    # A player VPIPs once per hand however many times they act, so the
    # comparison is per (hand, seat), not per row.
    # An all-in is voluntary money too. Ignition writes every all-in as one
    # verb whether it was a shove or a call for the last of a stack, so a
    # VPIP filter that only looks for "C" misses the calls.
    pairs = [
        ("VPIP", "SUM(vpip)", "street='preflop'", "agg=1 OR action IN ('C','A')"),
        ("PFR", "SUM(pfr)", "street='preflop'", "agg=1"),
    ]
    for name, spots_expr, where, act in pairs:
        a = con.execute(f"SELECT {spots_expr} FROM spots").fetchone()[0] or 0
        b = con.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat FROM decisions "
            f"WHERE {where} AND ({act}))").fetchone()[0]
        same = a == b
        print(f"{name:28} spots {a:>7}   decisions {b:>7}   "
              f"{'OK' if same else 'DISAGREE'}")
        if not same:
            fails.append(name)

    # Seeing a flop is not a decision -- a player already all in sees one
    # and has nothing to decide -- so the two tables are expected to differ
    # here, and every player in the gap has to be accounted for: either
    # all in before the flop, or dealt a flop nobody ever bet on because
    # everyone else open-folded before it was their turn. The second case
    # took a limped PokerStars pot to appear -- the small blind folded to
    # no bet and the big blind collected without a decision -- and the
    # check, written for a gap of all-ins alone, read it as a defect.
    # Cash only. The tournament hands include histories that begin part
    # way through a hand, so a player can have a turn decision and no
    # preflop one; that is a property of the export, not of this
    # derivation, and letting it sit in the check would mean the check
    # never goes green and therefore never gets read.
    saw = con.execute(
        f"SELECT SUM(saw_flop) FROM spots WHERE {sites.CASH}").fetchone()[0] or 0
    acted = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT hand_id, seat FROM decisions "
        f"WHERE street='flop' AND {sites.CASH})").fetchone()[0]
    silent = con.execute(
        f"SELECT s.hand_id, s.seat FROM spots s WHERE s.saw_flop=1 AND {sites.CASH} "
        "AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.hand_id=s.hand_id "
        "AND d.seat=s.seat AND d.street='flop')").fetchall()
    allin = sum(1 for hid, seat in silent if con.execute(
        "SELECT 1 FROM decisions WHERE hand_id=? AND seat=? AND street='preflop' "
        "AND allin=1", (hid, seat)).fetchone())
    unbet = sum(1 for hid, seat in silent if not con.execute(
        "SELECT 1 FROM decisions WHERE hand_id=? AND seat=? AND street='preflop' "
        "AND allin=1", (hid, seat)).fetchone() and not con.execute(
        "SELECT 1 FROM decisions WHERE hand_id=? AND street='flop' "
        "AND action != 'F'", (hid,)).fetchone())
    same = (saw - acted) == allin + unbet
    print(f"{'saw flop (cash)':28} spots {saw:>7}   decisions {acted:>7}   "
          f"{'OK' if same else 'DISAGREE'}  (gap {saw - acted}, "
          f"all-in preflop {allin}, flop open-folded to them {unbet})")
    if not same:
        fails.append("saw flop")

    # Columns whose contents are a known, closed vocabulary. A value from
    # outside it means the row tuple and the schema have drifted apart --
    # which is exactly what happened when four columns were added in the
    # middle of the schema and appended to the end of the tuple. Every
    # figure after the shift was read from the wrong column, and every check
    # here still passed, because none of them touched the columns that moved.
    VOCAB = {
        "street": {"preflop", "flop", "turn", "river"},
        "vs_pos": {"UTG", "HJ", "CO", "BTN", "SB", "BB", None},
        "opener_pos": {"UTG", "HJ", "CO", "BTN", "SB", "BB", None},
        "pfa_pos": {"UTG", "HJ", "CO", "BTN", "SB", "BB", None},
        "pot_type": {"unopened", "limped", "raised", "3bet", "4bet", "5bet+"},
        "facing": {"unopened", "open", "3bet", "4bet", "5bet+",
                   "check", "bet", "raise"},
        "action": {"F", "X", "C", "B", "R", "A"},
    }
    bad = []
    for col, allowed in VOCAB.items():
        seen = {r[0] for r in con.execute(f"SELECT DISTINCT {col} FROM decisions")}
        stray = {v for v in seen - allowed
                 if not (isinstance(v, str) and v.startswith("UTG+"))}
        if stray:
            bad.append(f"{col}: {sorted(str(x) for x in stray)[:4]}")
    print(f"columns hold only their own vocabulary  {len(VOCAB) - len(bad)}/{len(VOCAB)}")
    for b in bad:
        print(f"    {b}")
    if bad:
        fails.append("a column holds values from another column")

    print()
    print("what the new columns can express that spots cannot:")
    for label, where in [
            ("turn cbet chance", "street='turn' AND prev_agg=1 AND facing='check'"),
            ("delayed cbet chance",
             "street='turn' AND is_pfa=1 AND checked_to=1 AND facing='check'"),
            ("donk bet chance",
             "street='flop' AND first_in=1 AND is_pfa=0 AND is_ip=0"),
            ("float chance", "street='turn' AND prev_agg=0 AND is_ip=1"),
            ("check-raise chance", "street='flop' AND facing='bet' AND first_in=0"),
            ("3bet-pot flop, IP", "street='flop' AND pot_type='3bet' AND is_ip=1"),
            ("river, 100bb+ deep", "street='river' AND eff_bb>=100"),
            ("monotone flop", "street='flop' AND fl_mono=1"),
            ("facing an overbet",
             "to_call > pot_before - to_call AND street!='preflop'"),
            ("shortstacked open",
             "street='preflop' AND facing='unopened' AND eff_bb<40"),
    ]:
        n = con.execute(f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]
        print(f"  {label:24} {n:>7}")

    # The matchup is re-derived from the actions for every hand that has
    # one: an open with no limp before it, exactly one seat putting money
    # in behind it. A hand stamped with a matchup that the actions do not
    # support would put a real name on an invented pot.
    off = con.execute("""
        SELECT COUNT(*) FROM (
          SELECT d.hand_id, d.matchup, d.opener_pos,
                 (SELECT COUNT(DISTINCT seat) FROM decisions x
                   WHERE x.hand_id = d.hand_id AND x.street = 'preflop'
                     AND x.action IN ('C', 'R', 'A') AND x.amount > 0) vol
          FROM decisions d WHERE d.matchup IS NOT NULL
          GROUP BY d.hand_id)
        WHERE vol != 2 OR substr(matchup, 1, instr(matchup, '/') - 1) != opener_pos
        """).fetchone()[0]
    n_mu = con.execute("SELECT COUNT(DISTINCT hand_id) FROM decisions "
                       "WHERE matchup IS NOT NULL").fetchone()[0]
    print(f"\n  matchup pots           {n_mu:>7}   "
          f"({off} disagree with their actions)")
    if off:
        fails.append("matchup")
    # Hero is never their own opponent, and a pool row in a hand hero was
    # dealt into but had left is not "against hero" either: the count of
    # rows where hero_in disagrees with the seats must be zero.
    bad_in = con.execute("""
        SELECT COUNT(*) FROM decisions d
        WHERE d.is_hero = 1 AND d.hero_in = 1""").fetchone()[0]
    print(f"  hero as own opponent   {bad_in:>7}   rows")
    if bad_in:
        fails.append("hero_in")
    # A raise is more than the bet it raised, or it was not a raise; a
    # multiple under one is a bet size read wrong.
    tiny = con.execute("SELECT COUNT(*) FROM decisions WHERE raise_x IS NOT NULL "
                       "AND raise_x < 1.0").fetchone()[0]
    n_rx = con.execute("SELECT COUNT(*) FROM decisions WHERE raise_x IS NOT NULL").fetchone()[0]
    print(f"  raises with a multiple  {n_rx:>7}   ({tiny} under 1x)")
    if tiny:
        fails.append("raise_x")

    con.close()
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    if "--index" in sys.argv:
        index()
        print("indexes built")
        sys.exit(0)
    print(f"{build()} decisions written\n")
    check()
