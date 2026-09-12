"""Single-line hand encoding, after Hand2Note's compact hand view.

A report hand list that is only timestamps is a list you have to open.
H2N's compact line puts the betting on the row itself -- positions,
sizes in bb, the board and pot at each street -- so forty hands can
be scanned as forty hands. Their page is
https://hand2note.com/Help/Features/compact-hand-view. Fixed colors:
they cannot customize yet either, and neither do we.

    BTN R3  BB C2 | 7h Ks 8c (6)  BB X  BTN X | 8d (6)  BB B4.5  BTN C4.5 | Th (15)  BB B86'  BTN C86

X is a check. B/C/R carry a size in bb. A trailing ' is all-in.
The focus seat's action is marked (underline in ANSI/HTML, _R3_ in
plain text) so a pool list still shows whose row it is.

What this v1 does not encode, and why:

- Blind posts, dead chips, a straddle. The line starts at the first
  voluntary action; posting is not a decision.
- Hole cards, for Hold'em, on the compact line. The list already
  has a combo column; repeating AhKd here crowds the action. The
  card-row still knows N=2 (and `??` for a muck). Four- and
  five-card Omaha has no combo, so those cards are prepended
  `[As Ad Kh 7d]` -- otherwise the line is a board with nobody
  in it. A muck keeps the slots (`[?? ?? ?? ??]`) so a PLO list
  does not look like Hold'em. Overflow wraps to two lines
  rather than dropping the holes.
- Early folds. H2N sometimes drops them; we keep every voluntary
  action so a fold from UTG is still visible.
- EP1–EP3. This project's seats are UTG/HJ/CO/BTN/SB/BB. STR is
  coloured if it ever appears.
- Multiway crowding. A six-way flop is a long line; v1 does not
  truncate it, because cutting the wrong seat hides the spot.
- Tk Treeview cannot underline a substring, so the window marks the
  focus action as _R3_ instead of a true underline.

`CompactHandRenderer` is a pure function over a hand dict of the
shape `query.hand_detail` returns. `--check` proves the encodings
against fixtures and does not need `hands.db`.
"""

import html as html_lib
import re
import sqlite3
import sys
from pathlib import Path

import games
import lines
import strength
from equity import RANKS, SUITS

STREETS = ("preflop", "flop", "turn", "river")
VOLUNTARY = frozenset("FXCBRA")

# Hand2Note's position colours, fixed. They do not offer a picker yet
# and a second map here would be a second opinion about the same seats.
POSITION_COLOR = {
    "UTG": "#c9a227",
    "EP": "#c9a227",
    "EP1": "#c9a227",
    "EP2": "#c9a227",
    "EP3": "#c9a227",
    "HJ": "#6aa84f",
    "MP": "#6aa84f",
    "CO": "#3d85c6",
    "BTN": "#e69138",
    "SB": "#a64d79",
    "BB": "#cc4125",
    "STR": "#8e7cc3",
}

# Closest ANSI-256 to the same map, for a terminal list.
POSITION_ANSI = {
    "UTG": "\033[38;5;178m",
    "EP": "\033[38;5;178m",
    "EP1": "\033[38;5;178m",
    "EP2": "\033[38;5;178m",
    "EP3": "\033[38;5;178m",
    "HJ": "\033[38;5;71m",
    "MP": "\033[38;5;71m",
    "CO": "\033[38;5;68m",
    "BTN": "\033[38;5;208m",
    "SB": "\033[38;5;132m",
    "BB": "\033[38;5;167m",
    "STR": "\033[38;5;104m",
}

RESET = "\033[0m"
UNDER = "\033[4m"
UNDER_OFF = "\033[24m"
BOLD = "\033[1m"
BOLD_OFF = "\033[22m"

# A mucked seat still occupies its N slots. Dropping them on ACR
# (23% shown) made a PLO4 list look like Hold'em with an empty
# combo, which is how a four-card hand disappears from a scan.
MUCK = "??"

# Longer than this and the holes wrap onto their own line. Cutting
# the holes to keep one line is the failure the wrap exists to
# refuse -- a PLO list without the four cards is a board.
WRAP = 88


def CompactHandRenderer(hand, fmt="text"):
    """
    One hand as a scannable line, or two when the holes would be cut.

    `hand` is the dict `query.hand_detail` returns, or a slimmer fixture
    with `bb`, `board`, `focus` and `streets`. `fmt` is text, ansi or
    html. Text marks the focus seat as _R3_ because a Treeview cell
    cannot underline a substring. Omaha prepends N∈{4,5} hole cards
    (or `??` for a muck). Hold'em stays off the line -- the combo
    column already has AhKd.
    """
    if not hand:
        return ""
    bb = hand.get("bb") or 0
    focus = hand.get("focus")
    if focus is None:
        focus = hand.get("focus_seat")
    streets = hand.get("streets") or []
    parts = []
    for st in streets:
        name = st.get("street") or ""
        tokens = []
        for a in st.get("actions") or []:
            tok = _action_token(a, bb, focus, fmt)
            if tok:
                tokens.append(tok)
        if name == "preflop":
            if tokens:
                parts.append("  ".join(tokens))
            continue
        board = _street_cards(name, st.get("board") or hand.get("board") or "")
        pot = None
        acts = st.get("actions") or []
        if acts:
            pot = _bb_amount(_pot_chips(acts[0]), bb if _pot_needs_bb(acts[0]) else 1)
        head = board
        if pot:
            head = f"{board} ({pot})" if board else f"({pot})"
        chunk = "  ".join(([head] if head else []) + tokens)
        if chunk:
            parts.append(chunk)
    action = " | ".join(parts)
    shown = _hole_prefix(hand, fmt)
    if not shown:
        return action
    sep = "<br>" if fmt == "html" else "\n"
    glued = f"{shown} {action}" if action else shown
    # Measure the text form. HTML tags and ANSI would make a short
    # Hold'em line look like overflow and wrap a line that fits.
    if _visible_len(shown, fmt) + (1 if action else 0) + _visible_len(action, fmt) > WRAP and action:
        return f"{shown}{sep}{action}"
    return glued


def card_row(cards, n=None, used=None, fmt="text"):
    """
    N hole-card slots. Missing ones stay `??`, never disappear.

    N is 2, 4 or 5. A Hold'em muck is `?? ??`; a PLO4 muck is
    four of them. `used` is the exactly-two hole cards that made
    the Omaha hand -- the replayer highlights those, and only
    those. Scoring all four as Hold'em is the lie `best_omaha`
    exists to refuse.
    """
    n = _holes_n(n, cards)
    parts = [p for p in str(cards or "").split() if p]
    slots = []
    for i in range(n):
        c = parts[i] if i < len(parts) else MUCK
        slots.append(_paint_card(c, used, fmt))
    return " ".join(slots)


def hand_cell(row, fmt="text"):
    """
    What the list 'hand' column shows.

    Hold'em keeps the combo (AKs) -- repeating AhKd next to a
    combo column is how the compact line got crowded. Omaha has
    no combo, so the column is the N-card row, with `??` for a
    muck. One function, so Reports / Sessions / Marks / Notes
    cannot drift.
    """
    combo = row.get("combo")
    n = holes_of(row)
    if n <= 2 and combo:
        return combo
    if n >= 4:
        return card_row(row.get("cards"), n, fmt=fmt)
    cards = row.get("cards") or ""
    if len(str(cards).split()) >= 4:
        return cards
    return "--"


def holes_of(row):
    """N∈{2,4,5} from the registry, then the cards, then Hold'em."""
    n = row.get("hole_card_count") or row.get("holes")
    if n in (2, 4, 5):
        return int(n)
    game = row.get("game")
    if game:
        try:
            return games.holes(game)
        except KeyError:
            pass
    cards = row.get("cards") or ""
    got = len(str(cards).split()) if cards else 0
    if got in (2, 4, 5):
        return got
    return 2


def used_in_made(cards, board):
    """
    The two hole cards that made the Omaha hand, or ().

    `best_omaha` already walked C(N,2)×C(board,3). Asking it
    again here is the same answer; inventing a second walk is
    how a highlight would disagree with the histogram.
    """
    hole = strength.parse(cards)
    table = strength.parse(board)
    if len(hole) < 4 or len(table) < 3:
        return ()
    picked = strength.best_omaha(hole, table)
    if not picked:
        return ()
    _shape, h2, _b3 = picked
    return tuple(_unparse(c) for c in h2)


def decorate_detail(d, fmt="text"):
    """Stamp compact, card-row, and used-two onto a hand_detail dict."""
    if not d:
        return d
    n = holes_of(d)
    board = d.get("board") or ""
    d["hole_card_count"] = n
    d["compact"] = CompactHandRenderer(d, fmt=fmt)
    for s in d.get("seats") or []:
        used = used_in_made(s.get("cards"), board) if n >= 4 else ()
        s["used"] = list(used)
        s["hand"] = card_row(s.get("cards"), n, used=used, fmt=fmt)
    return d


def attach(con, rows, fmt="text"):
    """Write `compact` and the card-row onto matching-hand rows."""
    pairs = [(r.get("id"), r.get("seat")) for r in rows if r.get("id") is not None]
    got = lines_for(con, pairs, fmt=fmt)
    meta = _row_meta(con, pairs)
    for r in rows:
        key = (r.get("id"), r.get("seat"))
        r["compact"] = got.get(key, "")
        info = meta.get(key) or {}
        if info.get("game") and not r.get("game"):
            r["game"] = info["game"]
        if info.get("hole_card_count") and not r.get("hole_card_count"):
            r["hole_card_count"] = info["hole_card_count"]
        if info.get("cards") and not r.get("cards"):
            r["cards"] = info["cards"]
        r["hand"] = hand_cell(r, fmt=fmt)
    return rows


def lines_for(con, pairs, fmt="text"):
    """`{(hand_id, seat): line}` for the listed pairs, one query per chunk."""
    if not pairs:
        return {}
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "actions" not in tables or "hands" not in tables:
        return {}
    ids = []
    seen = set()
    for hid, _seat in pairs:
        if hid not in seen:
            seen.add(hid)
            ids.append(hid)
    acols = {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    dcols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")} \
        if "decisions" in tables else set()
    by_hand = {}
    for chunk in _chunks(ids, 200):
        by_hand.update(_load_hands(con, chunk, acols, dcols))
    out = {}
    for hid, seat in pairs:
        raw = by_hand.get(hid)
        if raw is None:
            out[(hid, seat)] = ""
            continue
        cards = (raw.get("seat_cards") or {}).get(seat)
        out[(hid, seat)] = CompactHandRenderer(
            dict(raw, focus=seat, cards=cards,
                 game=raw.get("game"),
                 hole_card_count=raw.get("hole_card_count")), fmt=fmt)
    return out


def check():
    """Encodings against fixtures. No corpus."""
    fails = []
    hand = _h2n_example()
    got = CompactHandRenderer(hand)
    want = ("BTN R3  BB _C2_ | 7h Ks 8c (6)  BB _X_  BTN X | "
            "8d (6)  BB _B4.5_  BTN C4.5 | Th (15)  BB _B86'_  BTN C86")
    if got != want:
        fails.append(f"H2N example was {got!r}, not {want!r}")
    if got.startswith("["):
        fails.append("Hold'em compact repeated the hole cards")

    plo4 = CompactHandRenderer({
        "bb": 0.10, "focus": 2, "cards": "As Ad Kh 7d",
        "seats": [{"seat": 2, "cards": "As Ad Kh 7d"}],
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "SB", "C", 0.05),
                _act(2, "BB", "X", 0),
            ]},
            {"street": "flop", "board": "Kc 2h 3s", "actions": [
                _act(1, "SB", "X", 0, pot_bb=2),
                _act(2, "BB", "B", 0.20, pot_bb=2),
            ]},
        ]})
    if not plo4.startswith("[As Ad Kh 7d]"):
        fails.append(f"PLO4 compact hid the four cards: {plo4!r}")
    plo5 = CompactHandRenderer({
        "bb": 0.10, "focus": 1, "cards": "Qh Jh Qd Kc 2c",
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "BB", "X", 0),
            ]},
        ]})
    if not plo5.startswith("[Qh Jh Qd Kc 2c]"):
        fails.append(f"PLO5 compact hid the five cards: {plo5!r}")

    # N∈{2,4,5}. Hold'em stays two slots; Omaha never collapses to a combo.
    if card_row("Ah Kd", 2) != "Ah Kd":
        fails.append(f"N=2 card-row was {card_row('Ah Kd', 2)!r}")
    if card_row("As Ad Kh 7d", 4) != "As Ad Kh 7d":
        fails.append(f"N=4 card-row was {card_row('As Ad Kh 7d', 4)!r}")
    if card_row("Qh Jh Qd Kc 2c", 5) != "Qh Jh Qd Kc 2c":
        fails.append(f"N=5 card-row was {card_row('Qh Jh Qd Kc 2c', 5)!r}")
    if card_row(None, 2) != "?? ??":
        fails.append(f"N=2 muck was {card_row(None, 2)!r}, not ?? ??")
    if card_row("", 4) != "?? ?? ?? ??":
        fails.append(f"N=4 muck was {card_row('', 4)!r}")
    if card_row(None, 5) != "?? ?? ?? ?? ??":
        fails.append(f"N=5 muck was {card_row(None, 5)!r}")

    muck4 = CompactHandRenderer({
        "bb": 0.10, "focus": 2, "game": "OMAHA", "hole_card_count": 4,
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(2, "BB", "X", 0),
            ]},
        ]})
    if not muck4.startswith("[?? ?? ?? ??]"):
        fails.append(f"PLO4 muck hid the four slots: {muck4!r}")
    hold_muck = CompactHandRenderer({
        "bb": 1, "focus": 1, "game": "HOLDEM", "hole_card_count": 2,
        "cards": "",
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "BB", "X", 0),
            ]},
        ]})
    if hold_muck.startswith("["):
        fails.append(f"Hold'em compact grew a hole prefix: {hold_muck!r}")

    # Overflow wraps. Cutting the holes to keep one line is the
    # failure a six-way PLO flop would have shipped.
    wide = CompactHandRenderer({
        "bb": 1, "focus": 2, "cards": "As Ad Kh 7d", "game": "OMAHA",
        "hole_card_count": 4,
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "UTG", "F", 0),
                _act(3, "HJ", "R", 3),
                _act(4, "CO", "C", 3),
                _act(5, "BTN", "C", 3),
                _act(6, "SB", "F", 0),
                _act(2, "BB", "C", 2),
            ]},
            {"street": "flop", "board": "7h Ks 8c", "actions": [
                _act(2, "BB", "X", 0, pot_bb=12),
                _act(3, "HJ", "B", 8, pot_bb=12),
                _act(4, "CO", "C", 8),
                _act(5, "BTN", "C", 8),
                _act(2, "BB", "F", 0),
            ]},
        ]})
    if "\n" not in wide:
        fails.append(f"overflow stayed one line: {wide!r}")
    first, rest = wide.split("\n", 1)
    if first != "[As Ad Kh 7d]":
        fails.append(f"overflow dropped the holes: {first!r}")
    if "7h Ks 8c" not in rest or "BB _X_" not in rest:
        fails.append(f"overflow dropped the action: {rest!r}")

    used = used_in_made("As Ad Kh 7d", "Kc 2h 3s")
    if set(used) != {"As", "Ad"}:
        fails.append(f"used-two on aces over K23 was {used}, not As Ad")
    painted = card_row("As Ad Kh 7d", 4, used=used)
    if "*As*" not in painted or "*Ad*" not in painted:
        fails.append(f"used-two was not marked: {painted!r}")
    if "*Kh*" in painted or "*7d*" in painted:
        fails.append(f"unused hole cards were marked: {painted!r}")

    if hand_cell({"combo": "AKs", "game": "HOLDEM"}) != "AKs":
        fails.append("Hold'em hand cell dropped the combo")
    if hand_cell({"cards": "As Ad Kh 7d", "game": "OMAHA"}) != "As Ad Kh 7d":
        fails.append("PLO4 hand cell hid the four cards")
    if hand_cell({"game": "OMAHA5"}) != "?? ?? ?? ?? ??":
        fails.append("PLO5 hand cell hid the muck slots")

    fold_check = CompactHandRenderer({
        "bb": 1, "focus": 2, "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "UTG", "F", 0),
                _act(2, "BTN", "R", 3),
                _act(3, "BB", "C", 2),
            ]},
            {"street": "flop", "board": "Ah Kd 2c", "actions": [
                _act(3, "BB", "X", 0, pot_bb=6),
                _act(2, "BTN", "B", 4, pot_bb=6),
                _act(3, "BB", "F", 0),
            ]},
        ]})
    want_fc = ("UTG F  BTN _R3_  BB C2 | Ah Kd 2c (6)  BB X  BTN _B4_  BB F")
    if fold_check != want_fc:
        fails.append(f"fold/check encoding was {fold_check!r}, not {want_fc!r}")

    shove = CompactHandRenderer({
        "bb": 0.5, "focus": 2, "streets": [
            {"street": "river", "board": "Th", "actions": [
                _act(2, "BB", "B", 43, allin=True, pot_bb=15),
                _act(1, "BTN", "C", 43),
            ]},
        ]})
    if shove != "Th (15)  BB _B86'_  BTN C86":
        fails.append(f"all-in bet was {shove!r}, not Th (15)  BB _B86'_  BTN C86")

    # An all-in is a call 95 times in 236 here. Counting it as a raise
    # would write R10' for the last of a stack and look like a shove.
    call_shove = CompactHandRenderer({
        "bb": 1, "focus": 1, "streets": [
            {"street": "flop", "board": "Qs Jh 2d", "actions": [
                _act(2, "BTN", "B", 6, pot_bb=10),
                _act(1, "BB", "A", 10, allin=True, agg=0, to_call=6),
            ]},
        ]})
    if "BB _C10'_" not in call_shove:
        fails.append(f"all-in call was {call_shove!r}, not C10'")

    raise_shove = CompactHandRenderer({
        "bb": 1, "focus": 1, "streets": [
            {"street": "flop", "board": "Qs Jh 2d", "actions": [
                _act(2, "BTN", "B", 6, pot_bb=10),
                _act(1, "BB", "A", 20, total=20, allin=True, agg=1, to_call=6),
            ]},
        ]})
    if "BB _R20'_" not in raise_shove:
        fails.append(f"all-in raise was {raise_shove!r}, not R20'")

    marked = CompactHandRenderer(_h2n_example(), fmt="text")
    if "_C2_" not in marked or "_B86'_" not in marked:
        fails.append(f"focus seat was not marked: {marked!r}")
    if "_R3_" in marked:
        fails.append("BTN open was marked; BB is the focus seat")

    h = CompactHandRenderer(_h2n_example(), fmt="html")
    if "<u>C2</u>" not in h or "<u>B86&#x27;</u>" not in h and "<u>B86'</u>" not in h:
        fails.append(f"html did not underline the focus actions: {h!r}")
    if POSITION_COLOR["BB"] not in h or POSITION_COLOR["BTN"] not in h:
        fails.append("html dropped a position colour")

    ansi = CompactHandRenderer(_h2n_example(), fmt="ansi")
    if UNDER not in ansi or "C2" not in ansi:
        fails.append(f"ansi did not underline: {ansi!r}")

    # Batch load, the path the hand lists actually use. Two seats of
    # one hand must produce two lines that differ only in who is marked.
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE hands (hand_id TEXT, bb REAL, board TEXT);"
        "CREATE TABLE actions ("
        "hand_id TEXT, street TEXT, n INT, position TEXT, seat INT, "
        "action TEXT, amount REAL, total REAL, allin INT);"
        "CREATE TABLE decisions ("
        "hand_id TEXT, n INT, pot_before REAL, pot_bb REAL, "
        "to_call REAL, agg INT);")
    con.execute("INSERT INTO hands VALUES ('h1', 1, '7h Ks 8c 8d Th')")
    con.executemany(
        "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
        [("h1", "preflop", 1, "BTN", 1, "R", 3, 3, 0),
         ("h1", "preflop", 2, "BB", 2, "C", 2, None, 0),
         ("h1", "flop", 3, "BB", 2, "X", 0, None, 0),
         ("h1", "flop", 4, "BTN", 1, "X", 0, None, 0),
         ("h1", "turn", 5, "BB", 2, "B", 4.5, None, 0),
         ("h1", "turn", 6, "BTN", 1, "C", 4.5, None, 0),
         ("h1", "river", 7, "BB", 2, "B", 86, None, 1),
         ("h1", "river", 8, "BTN", 1, "C", 86, None, 0)])
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
        [("h1", 3, 6, 6, 0, 0),
         ("h1", 5, 6, 6, 0, 1),
         ("h1", 7, 15, 15, 0, 1)])
    rows = [{"id": "h1", "seat": 2}, {"id": "h1", "seat": 1}]
    attach(con, rows, fmt="text")
    if "BB _C2_" not in (rows[0].get("compact") or ""):
        fails.append(f"batch BB focus was {rows[0].get('compact')!r}")
    if "BTN _R3_" not in (rows[1].get("compact") or ""):
        fails.append(f"batch BTN focus was {rows[1].get('compact')!r}")
    if "B86'" not in (rows[0].get("compact") or ""):
        fails.append(f"batch missed the all-in mark: {rows[0].get('compact')!r}")
    con.close()

    bare = sqlite3.connect(":memory:")
    orphan = [{"id": "x", "seat": 1}]
    attach(bare, orphan, fmt="text")
    if orphan[0].get("compact"):
        fails.append("attach without actions wrote a line")
    bare.close()

    fails.extend(_check_snapshots())
    fails.extend(_check_smoke())

    print(f"compact hand encodings        "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return fails


def _h2n_example():
    """BTN open 3, BB call 2; flop XX; turn B4.5 C4.5; river shove 86."""
    return {
        "bb": 1,
        "focus": 2,
        "board": "7h Ks 8c 8d Th",
        "streets": [
            {"street": "preflop", "board": "", "actions": [
                _act(1, "BTN", "R", 3, total=3),
                _act(2, "BB", "C", 2),
            ]},
            {"street": "flop", "board": "7h Ks 8c", "actions": [
                _act(2, "BB", "X", 0, pot_bb=6),
                _act(1, "BTN", "X", 0),
            ]},
            {"street": "turn", "board": "7h Ks 8c 8d", "actions": [
                _act(2, "BB", "B", 4.5, pot_bb=6),
                _act(1, "BTN", "C", 4.5),
            ]},
            {"street": "river", "board": "7h Ks 8c 8d Th", "actions": [
                _act(2, "BB", "B", 86, allin=True, pot_bb=15),
                _act(1, "BTN", "C", 86),
            ]},
        ],
    }


def _act(seat, position, action, amount, **extra):
    row = {"seat": seat, "position": position, "action": action,
           "amount": amount, "allin": False}
    row.update(extra)
    return row


def _action_token(a, bb, focus, fmt):
    letter, shove = _verb(a)
    if letter is None:
        return None
    size = ""
    if letter in "CBR":
        chips = _chips(a)
        size = _bb_amount(chips, bb) if chips else ""
    body = f"{letter}{size}{shove}"
    pos = (a.get("position") or "?").strip() or "?"
    hero = (a.get("seat") == focus) if focus is not None \
        else bool(a.get("is_hero"))
    return _paint(pos, body, hero, fmt)


def _verb(a):
    """Letter plus optional all-in tick. None means skip (a post, a sit-out)."""
    action = a.get("action") or ""
    if action not in VOLUNTARY:
        return None, ""
    allin = bool(a.get("allin") or action == "A")
    if action == "A":
        agg = a.get("agg")
        to_call = a.get("to_call")
        amount = a.get("amount")
        # lines.letter is the project's one answer to "was this all-in
        # a call": 95 of 236 are, and counting them as raises put 31
        # preflop decisions in the wrong pot type.
        if agg is not None:
            letter = lines.letter("A", int(bool(agg)))
        elif to_call and amount is not None and amount <= to_call + 1e-9:
            letter = "C"
        elif to_call:
            letter = "R"
        else:
            letter = "B"
        if letter == "A":
            letter = "R" if to_call else "B"
        return letter, "'"
    return action, ("'" if allin else "")


def _chips(a):
    action = a.get("action")
    # Raise-to is the figure H2N prints (R3, not the increment). Calls
    # and bets keep the amount that went in on this action -- BB C2 is
    # the two bb on top of the posted blind, not the three-bb total.
    if action in ("R", "A") and a.get("total"):
        return a["total"]
    return a.get("amount")


def _pot_chips(a):
    if a.get("pot_bb") is not None:
        return a["pot_bb"]
    return a.get("pot_before")


def _pot_needs_bb(a):
    return a.get("pot_bb") is None


def _bb_amount(chips, bb):
    if chips is None or not bb:
        return ""
    x = float(chips) / float(bb)
    rounded = round(x, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def _street_cards(street, board):
    """Flop is the three cards; turn and river are the card that arrived.

    A 'straight' on the turn is the board being a card off one, and on
    the river it is that card having come. The compact line follows the
    same cut: the new card, not the board as it looks afterwards.
    """
    cards = (board or "").split()
    if street == "flop":
        return " ".join(cards[:3])
    if street == "turn":
        if len(cards) >= 4:
            return cards[3]
        return cards[-1] if cards else ""
    if street == "river":
        if len(cards) >= 5:
            return cards[4]
        return cards[-1] if cards else ""
    return ""


def _paint(pos, body, hero, fmt):
    if fmt == "html":
        colour = POSITION_COLOR.get(pos, "#9aa0a6")
        pos_h = (f'<span style="color:{colour}">'
                 f"{html_lib.escape(pos)}</span>")
        body_h = html_lib.escape(body)
        if hero:
            body_h = f"<u>{body_h}</u>"
        return f"{pos_h} {body_h}"
    if fmt == "ansi":
        colour = POSITION_ANSI.get(pos, "")
        pos_a = f"{colour}{pos}{RESET}" if colour else pos
        body_a = f"{UNDER}{body}{UNDER_OFF}" if hero else body
        return f"{pos_a} {body_a}"
    if hero:
        return f"{pos} _{body}_"
    return f"{pos} {body}"


def _load_hands(con, ids, acols, dcols):
    qs = ",".join("?" * len(ids))
    allin_sql = "a.allin" if "allin" in acols else "0"
    hcols = {r[1] for r in con.execute("PRAGMA table_info(hands)")}
    game_sql = "h.game" if "game" in hcols else "NULL"
    holes_sql = "h.hole_card_count" if "hole_card_count" in hcols else "NULL"
    extra, join = "", ""
    if dcols:
        extra = (", d.pot_before, d.pot_bb, d.to_call, d.agg"
                 if {"pot_before", "pot_bb", "to_call", "agg"} <= dcols
                 else "")
        if extra:
            join = (" LEFT JOIN decisions d ON d.hand_id = a.hand_id "
                    "AND d.n = a.n")
    sql = (f"SELECT a.hand_id, a.street, a.n, a.position, a.seat, a.action, "
           f"a.amount, a.total, {allin_sql}, h.bb, h.board, "
           f"{game_sql}, {holes_sql}{extra} "
           f"FROM actions a JOIN hands h ON h.hand_id = a.hand_id{join} "
           f"WHERE a.hand_id IN ({qs}) ORDER BY a.hand_id, a.n")
    rows = con.execute(sql, ids).fetchall()
    grouped = {}
    has_dec = bool(extra)
    for r in rows:
        hid = r[0]
        if hid not in grouped:
            grouped[hid] = {"bb": r[9], "board": r[10], "game": r[11],
                            "hole_card_count": r[12], "actions": [],
                            "seat_cards": {}}
        action = {
            "street": r[1], "n": r[2], "position": r[3], "seat": r[4],
            "action": r[5], "amount": r[6], "total": r[7],
            "allin": bool(r[8]),
        }
        if has_dec:
            action["pot_before"] = r[13]
            action["pot_bb"] = r[14]
            action["to_call"] = r[15]
            action["agg"] = r[16]
        grouped[hid]["actions"].append(action)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "seats" in names and ids:
        for hid, seat, cards in con.execute(
                f"SELECT hand_id, seat, cards FROM seats "
                f"WHERE hand_id IN ({qs})", ids):
            if hid in grouped:
                grouped[hid].setdefault("seat_cards", {})[seat] = cards
    out = {}
    for hid, raw in grouped.items():
        out[hid] = _as_hand(raw)
    return out


def _as_hand(raw):
    board = (raw.get("board") or "").split()
    shown = {"preflop": "", "flop": " ".join(board[:3]),
             "turn": " ".join(board[:4]), "river": " ".join(board[:5])}
    streets = []
    for st in STREETS:
        acts = [a for a in raw["actions"] if a.get("street") == st]
        if not acts:
            continue
        streets.append({"street": st, "board": shown[st], "actions": acts})
    return {"bb": raw.get("bb"), "board": raw.get("board"),
            "game": raw.get("game"),
            "hole_card_count": raw.get("hole_card_count"),
            "streets": streets, "seat_cards": raw.get("seat_cards") or {}}


def _hole_prefix(hand, fmt):
    """
    `[As Ad Kh 7d]` / `[?? ?? ?? ??]` for Omaha, or None.

    Two-card Hold'em stays off the line -- the combo column already
    has AhKd. A muck still keeps four or five slots so a PLO list
    cannot be scanned as Hold'em with an empty combo.
    """
    n = holes_of(hand)
    if n < 4:
        cards = _focus_hole_text(hand)
        if cards and len(cards.split()) >= 4:
            n = len(cards.split())
        else:
            return None
    cards = _focus_hole_text(hand)
    body = card_row(cards, n, fmt=fmt)
    if fmt == "html":
        return f"<span class=\"hole\">[{body}]</span>"
    return f"[{body}]"


def _focus_hole_text(hand):
    cards = hand.get("cards") or ""
    if cards:
        return cards
    focus = hand.get("focus")
    if focus is None:
        focus = hand.get("focus_seat")
    for s in hand.get("seats") or []:
        if s.get("seat") == focus and s.get("cards"):
            return s["cards"]
    if focus is not None:
        return (hand.get("seat_cards") or {}).get(focus) or ""
    return ""


def _focus_cards(hand):
    """Four or five shown hole cards, or None. Kept for the old callers."""
    cards = _focus_hole_text(hand)
    n = len(str(cards).split()) if cards else 0
    return cards if n >= 4 else None


def _holes_n(n, cards):
    if n in (2, 4, 5):
        return int(n)
    got = len(str(cards or "").split()) if cards else 0
    if got in (2, 4, 5):
        return got
    return 2


def _paint_card(c, used, fmt):
    if c == MUCK:
        if fmt == "html":
            return f'<span class="muck">{MUCK}</span>'
        return MUCK
    mark = bool(used) and c in used
    if fmt == "html":
        body = html_lib.escape(c)
        cls = "used" if mark else ""
        return f'<span class="{cls}">{body}</span>' if cls else body
    if fmt == "ansi":
        return f"{BOLD}{c}{BOLD_OFF}" if mark else c
    if mark:
        return f"*{c}*"
    return c


def _unparse(c):
    return RANKS[c[0]] + SUITS[c[1]]


def _visible_len(s, fmt):
    if not s:
        return 0
    if fmt == "html":
        return len(re.sub(r"<[^>]+>", "", s))
    if fmt == "ansi":
        return len(re.sub(r"\033\[[0-9;]*m", "", s))
    return len(s)


def _strip_paint(s, fmt):
    if fmt == "html":
        return re.sub(r"<[^>]+>", "", s)
    if fmt == "ansi":
        return re.sub(r"\033\[[0-9;]*m", "", s)
    return s.replace("*", "")


def _row_meta(con, pairs):
    """game / hole_card_count / cards for the listed (hand, seat) pairs."""
    if not pairs:
        return {}
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "hands" not in tables:
        return {}
    hcols = {r[1] for r in con.execute("PRAGMA table_info(hands)")}
    ids = list({hid for hid, _s in pairs if hid is not None})
    if not ids:
        return {}
    qs = ",".join("?" * len(ids))
    game_sql = "game" if "game" in hcols else "NULL"
    holes_sql = "hole_card_count" if "hole_card_count" in hcols else "NULL"
    by_hand = {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT hand_id, {game_sql}, {holes_sql} FROM hands "
        f"WHERE hand_id IN ({qs})", ids)}
    cards = {}
    if "seats" in tables:
        for hid, seat, c in con.execute(
                f"SELECT hand_id, seat, cards FROM seats "
                f"WHERE hand_id IN ({qs})", ids):
            cards[(hid, seat)] = c
    out = {}
    for hid, seat in pairs:
        g, n = by_hand.get(hid, (None, None))
        out[(hid, seat)] = {"game": g, "hole_card_count": n,
                            "cards": cards.get((hid, seat))}
    return out


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def from_parsed(parsed, focus=None):
    """Parser dict → CompactHandRenderer input. Smoke, not a second pot."""
    h = parsed["hand"]
    game = h.get("game")
    try:
        n = games.holes(game) if game else None
    except KeyError:
        n = None
    focus = h.get("hero_seat") if focus is None else focus
    cards = None
    for s in parsed.get("seats") or []:
        if s.get("seat") == focus:
            cards = s.get("cards")
            break
    by = {}
    for a in parsed.get("actions") or []:
        by.setdefault(a.get("street") or "preflop", []).append(a)
    board = (h.get("board") or "").split()
    shown = {"preflop": "", "flop": " ".join(board[:3]),
             "turn": " ".join(board[:4]), "river": " ".join(board[:5])}
    streets = []
    for st in STREETS:
        acts = by.get(st) or []
        if not acts:
            continue
        streets.append({"street": st, "board": shown[st], "actions": acts})
    return {
        "bb": h.get("bb"), "board": h.get("board"), "game": game,
        "hole_card_count": n, "focus": focus, "cards": cards,
        "seats": parsed.get("seats") or [], "streets": streets,
    }


def _check_snapshots():
    """Golden encodings on disk. A silent rewrite is a different product."""
    fails = []
    here = Path(__file__).parent / "fixtures" / "compact"
    cases = {
        "nlhe_h2n.txt": CompactHandRenderer(_h2n_example()),
        "plo4.txt": CompactHandRenderer({
            "bb": 0.10, "focus": 2, "cards": "As Ad Kh 7d",
            "game": "OMAHA", "hole_card_count": 4,
            "streets": [
                {"street": "preflop", "board": "", "actions": [
                    _act(1, "SB", "C", 0.05),
                    _act(2, "BB", "X", 0),
                ]},
                {"street": "flop", "board": "Kc 2h 3s", "actions": [
                    _act(1, "SB", "X", 0, pot_bb=2),
                    _act(2, "BB", "B", 0.20, pot_bb=2),
                ]},
            ]}),
        "plo5.txt": CompactHandRenderer({
            "bb": 0.10, "focus": 1, "cards": "Qh Jh Qd Kc 2c",
            "game": "OMAHA5", "hole_card_count": 5,
            "streets": [
                {"street": "preflop", "board": "", "actions": [
                    _act(1, "BB", "X", 0),
                ]},
            ]}),
        "plo4_muck.txt": CompactHandRenderer({
            "bb": 0.10, "focus": 2, "game": "OMAHA", "hole_card_count": 4,
            "streets": [
                {"street": "preflop", "board": "", "actions": [
                    _act(2, "BB", "X", 0),
                ]},
            ]}),
        "card_row_n.txt": "\n".join((
            card_row("Ah Kd", 2),
            card_row("As Ad Kh 7d", 4),
            card_row("Qh Jh Qd Kc 2c", 5),
            card_row(None, 2),
            card_row("", 4),
            card_row(None, 5),
        )),
    }
    if not here.is_dir():
        here.mkdir(parents=True)
    for name, got in cases.items():
        path = here / name
        if not path.exists():
            path.write_text(got + "\n", encoding="utf-8")
            fails.append(f"snapshot {name} was missing; wrote it")
            continue
        want = path.read_text(encoding="utf-8").rstrip("\n")
        if got != want:
            fails.append(f"snapshot {name} was {got!r}, not {want!r}")
    return fails


def _check_smoke():
    """Ignition / ACR PLO histories through the renderer, no corpus."""
    fails = []
    import acr
    import ignition

    src = "HH - RING - $0.05-$0.10 - OMAHA.txt"
    p4 = ignition.parse_hand(ignition.PLO4_PLAYED, source=src)
    if p4 is None:
        fails.append("Ignition PLO4_PLAYED did not parse")
        return fails
    h4 = from_parsed(p4, focus=2)
    line4 = CompactHandRenderer(h4)
    if "[As Ad Kh 7d]" not in line4.split("\n")[0]:
        fails.append(f"Ignition PLO4 smoke hid the holes: {line4!r}")
    if "C0.5" not in line4 and "C.5" not in line4:
        fails.append(f"Ignition PLO4 smoke lost the limp: {line4!r}")
    seats4 = decorate_detail(dict(h4, board=h4.get("board") or "Kc 2h 3s"),
                             fmt="text")["seats"]
    hero4 = next(s for s in seats4 if s.get("seat") == 2)
    if "As" not in (hero4.get("hand") or ""):
        fails.append(f"Ignition PLO4 replayer hid hero: {hero4.get('hand')!r}")
    if set(hero4.get("used") or []) != {"As", "Ad"}:
        fails.append(f"Ignition PLO4 used-two was {hero4.get('used')}")

    p5 = ignition.parse_hand(ignition.PLO5, source=src.replace("OMAHA", "5CARD OMAHA"))
    if p5 is None:
        fails.append("Ignition PLO5 did not parse")
    else:
        h5 = from_parsed(p5, focus=2)
        line5 = CompactHandRenderer(h5)
        if "[Qh Jh Qd Kc 2c]" not in line5.split("\n")[0]:
            fails.append(f"Ignition PLO5 smoke hid the five: {line5!r}")
        if card_row(h5.get("cards"), 5).split() != [
                "Qh", "Jh", "Qd", "Kc", "2c"]:
            fails.append(f"Ignition PLO5 card-row was {h5.get('cards')!r}")

    show = acr.parse_hand(acr.PLO4_SHOW, source="acr")
    if show is None:
        fails.append("ACR PLO4_SHOW did not parse")
    else:
        hs = from_parsed(show, focus=2)
        line_s = CompactHandRenderer(hs)
        if "[As Ad Kh 7d]" not in line_s.split("\n")[0]:
            fails.append(f"ACR PLO4 showdown hid the holes: {line_s!r}")
        used = used_in_made("As Ad Kh 7d", show["hand"].get("board") or "")
        if set(used) != {"As", "Ad"}:
            fails.append(f"ACR showdown used-two was {used}")
        muck = card_row(None, holes_of(hs))
        if muck != "?? ?? ?? ??":
            fails.append(f"ACR muck row was {muck!r}")
    return fails


if __name__ == "__main__":
    fails = check() if "--check" in sys.argv[1:] else []
    raise SystemExit(1 if fails else 0)
