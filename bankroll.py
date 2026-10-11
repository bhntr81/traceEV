"""
The money half of the tracker: what you have, where it came from, and the
plan you are measuring it against.

A tracker says what happened at the tables; a bankroll is that plus the
money that never touched one -- deposits, withdrawals, bonuses, rakeback,
transfers between sites. Until 11 Oct 2026 it lived in a separate script
with its own database, which recorded one balance a day and called the
difference "profit". That counted every deposit as winnings, measured a
back-dated day against the newest balance, and left every later profit wrong
when a day was deleted. So this is a LEDGER, not a column of snapshots:

    bankroll = deposits - withdrawals + bonuses + rakeback
               +/- transfers between sites
               + results

and RESULTS come from hands wherever this program has them: the sittings
`sessions.py` cuts, each with its money -- `spots.net`, `won - posted -
invested`, never `won` -- summed by the same derivation that sums its big
blinds. Tournaments stay out, as they do everywhere money is counted.

A room that writes no hand history -- ClubWPT Gold until
bhntr81/clubwpt-ocr can export, an app room without a converter -- gets
MANUAL sessions: a date, hours, hands, stake and a result you type. They
are marked as typed wherever they are counted, so a figure says how much of
it is your word rather than the hands', and a manual session is REPLACED,
not added to, once that site's own hands cover the same time: any sitting
of the site that overlaps it supersedes it.

A balance you type ("the site says $X") is a RECONCILIATION POINT. It is
compared with what the ledger says you had at that moment, and the gap is
shown -- a deposit not entered, a result not entered, a fee -- and it never
overwrites anything. Results are results; a balance is a check on them.

Per site, and per currency as each site states it. A hand history's amounts
carry no currency the parsers keep, so each site's currency is stated once
(`--account SITE CUR`), and two currencies are never added together: a
site whose currency nobody has stated is shown on its own and summed into
nothing.

The challenge plan is opinion, so it lives in one place: `DEFAULT_PLAN`
below, from the plan this replaced, and yours in `bankroll.json` beside
`stats.json` (gitignored), which replaces it whole. Moving up and the
stop-loss are measured in buy-ins of the stake you are actually PLAYING --
the last sitting's -- never of the stake your bankroll would allow: the old
script took the tier from the balance, so its stop-loss could only fire in
the first tier, and at 10NL with $250 it measured from 2NL's entry. The
stop-loss is five buy-ins under that tier's entry, and is not in effect at
all until the bankroll is under thirty buy-ins of the stake: the user's
rule of 11 Oct 2026, which leaves a bankroll that is short of the entry but
still deep in buy-ins alone.

The ledger is your data, not a derivation. It lives in `hands.db` beside the
hands, as notes and tags do, in tables nothing in `importer.CHAIN` touches;
a rebuild leaves it as it was, every schema change here adds and never
drops, and the database is copied to `hands.db.bak-*` before these tables
are first made.

    python bankroll.py                         the bankroll, per currency
    python bankroll.py --account SITE CUR      say which currency a site keeps
    python bankroll.py --deposit SITE AMOUNT [--date D] [--note TEXT]
    python bankroll.py --withdraw SITE AMOUNT [...]
    python bankroll.py --bonus SITE AMOUNT [...]
    python bankroll.py --rakeback SITE AMOUNT [...]
    python bankroll.py --transfer FROM TO AMOUNT [--to-amount AMOUNT] [...]
    python bankroll.py --balance SITE AMOUNT [--date D]      the site says $X
    python bankroll.py --session SITE RESULT --date D [--hours H]
                       [--hands N] [--stake 0.05/0.10[/0.20]] [--note TEXT]
    python bankroll.py --entries               every row, with the id to delete it by
    python bankroll.py --delete ID             one row (e12, b3, s7)
    python bankroll.py --plan                  the plan in effect
    python bankroll.py --import-old FILE --site SITE [--currency CUR]
    python bankroll.py --import-csv FILE --site SITE [--currency CUR]
    python bankroll.py --check
"""

import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "hands.db"
# Beside `stats.json`, and gitignored with it: your plan, not the project's.
PLAN_FILE = HERE / "bankroll.json"

# CREATE ... IF NOT EXISTS and nothing else, ever: these tables hold what you
# typed and nothing can derive it again. A column added later is an ALTER
# TABLE ADD COLUMN in `ensure`; the old script's schema change dropped its
# whole table, and `check` reads every module for a DROP of one of these.
# Money is in whole cents, so a ledger of a thousand rows adds up to the
# cent rather than to a float's idea of it.
SCHEMA = """
CREATE TABLE IF NOT EXISTS bankroll_accounts (
  site TEXT PRIMARY KEY, currency TEXT NOT NULL, made TEXT);
CREATE TABLE IF NOT EXISTS bankroll_entries (
  id INTEGER PRIMARY KEY, site TEXT NOT NULL, at TEXT NOT NULL,
  kind TEXT NOT NULL, cents INTEGER NOT NULL, currency TEXT NOT NULL,
  transfer INTEGER, note TEXT, source TEXT, made TEXT);
CREATE TABLE IF NOT EXISTS bankroll_balances (
  id INTEGER PRIMARY KEY, site TEXT NOT NULL, at TEXT NOT NULL,
  cents INTEGER NOT NULL, currency TEXT NOT NULL, note TEXT, source TEXT,
  made TEXT);
CREATE TABLE IF NOT EXISTS bankroll_sessions (
  id INTEGER PRIMARY KEY, site TEXT NOT NULL, started TEXT NOT NULL,
  minutes REAL, hands INTEGER, bb REAL, straddle INTEGER, cents INTEGER,
  currency TEXT NOT NULL, note TEXT, source TEXT, made TEXT);
CREATE TABLE IF NOT EXISTS bankroll_imports (
  digest TEXT PRIMARY KEY, path TEXT, rows INTEGER, made TEXT);
"""
TABLES = ("bankroll_accounts", "bankroll_entries", "bankroll_balances",
          "bankroll_sessions", "bankroll_imports")

# What each kind of money does to the bankroll. You type a positive amount;
# the sign is the kind's, so a withdrawal cannot be entered as a deposit of
# minus fifty and read back as one.
KINDS = {"deposit": 1, "withdrawal": -1, "bonus": 1, "rakeback": 1,
         "transfer_in": 1, "transfer_out": -1}

# A manual session's source when it is a balance difference from the old
# tracker: the one place a result here IS a change in balance, and it says
# so wherever it is counted, because it may hide a deposit made that day.
INFERRED = "old tracker: the change in balance"

# The plan you were following, as it was written down, with two of its
# mistakes undone: the start is the first tier's entry ($164, where the old
# title said $40), and every stage has its own name. A stake is a big blind
# AND whether the table is straddled, because two tiers are both 10NL; where
# only one tier has that big blind, `straddle` is None and either matches --
# the plan does not say whether 20NL ("effectively 40NL") meant a straddle
# or a deep stack, and a guess here would be a stop-loss that never fires.
# A buy-in is the plan's, in dollars: 200 big blinds at every tier.
DEFAULT_PLAN = {
    "name": "the 50NL challenge",
    "currency": "USD",
    # Empty: every site whose account is in the plan's currency.
    "sites": [],
    # The stop-loss: this many buy-ins under the entry of the tier played,
    # and in effect only while the bankroll is also under the second number
    # of that stake's buy-ins. At 2NL Deep the second decides -- $144 is
    # five under the $164 entry but still 36 buy-ins of $4 -- and at every
    # other tier the first does.
    "stop_loss_buyins": 5,
    "stop_loss_in_effect_under": 30,
    "after": "50NL",
    "tiers": [
        {"name": "2NL Deep", "bb": "0.02", "straddle": None,
         "buyin": "4", "entry": "164", "target": "300"},
        {"name": "10NL Deep", "bb": "0.10", "straddle": False,
         "buyin": "20", "entry": "300", "target": "600"},
        {"name": "10NL Straddle", "bb": "0.10", "straddle": True,
         "buyin": "20", "entry": "600", "target": "1200"},
        {"name": "20NL (effectively 40NL)", "bb": "0.20", "straddle": None,
         "buyin": "40", "entry": "1200", "target": "3000"},
    ],
}

# A stake the plan does not name is measured in buy-ins of 100 big blinds,
# the ordinary table maximum, so a downswing at 25NL on Ignition still has a
# size in buy-ins rather than only in dollars.
STANDARD_BUYIN_BB = 100

# Under this many hands a win rate's error is not measured but assumed, and
# the rate is said to be an anecdote. The overfolds view keeps rows under 30
# off its table for the same reason.
MIN_RATE_HANDS = 30


# ---- reading what is typed -------------------------------------------

def cents(text, signed=False):
    """
    An amount as whole cents, exactly, or a ValueError saying why not.

    "$1,234.50" and "1234.5" are the same; "12.345" is refused rather than
    rounded, because a third decimal is a typing mistake more often than a
    sub-cent balance, and the ledger cannot tell which.
    """
    raw = str(text).strip().replace(",", "").replace("$", "").replace("€", "") \
        .replace("£", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{text!r} is not an amount") from None
    if not value.is_finite():
        raise ValueError(f"{text!r} is not an amount")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError(f"{text!r} has more than two decimals")
    if value < 0 and not signed:
        raise ValueError(f"{text!r}: type the amount without a sign; "
                         "the kind of entry says which way it goes")
    return int(value * 100)


def money(c, currency=""):
    """Cents as a string with its currency, sign first: -$12.50, +€3.00."""
    if c is None:
        return "?"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    sign = "-" if c < 0 else ""
    whole = f"{abs(c) / 100:,.2f}"
    return f"{sign}{sym}{whole}" + ("" if sym or not currency else f" {currency}")


def tenths(num, den, sign=False):
    """
    num/den to one decimal, cut toward zero rather than rounded.

    For counts of buy-ins beside a line that fires at a count: $119.99 at
    2NL is 29.9975 buy-ins, and rounded it read "30.0" next to a stop-loss
    that had just fired for being under 30. Whole cents in, so it is exact.
    """
    q = abs(num) * 10 // den
    neg = num < 0 and q
    return (("-" if neg else "+" if sign else "") + f"{q // 10}.{q % 10}")


def when(text):
    """
    A date, or a date and time, as typed, checked and written one way.

    A day without a time is a day, not midnight: a balance typed for
    "2026-10-01" is what you had at the END of that day, and `order_key`
    sorts it after every sitting that day.
    """
    text = (text or "").strip()
    if text.lower() in ("", "today"):
        return date.today().isoformat()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%d" if fmt == "%Y-%m-%d" else "%Y-%m-%d %H:%M:%S")
    raise ValueError(f"{text!r} is not a date: write 2026-10-11, "
                     "or 2026-10-11 21:30")


def order_key(at):
    """Sorts a day-only time after every timed thing that day."""
    return at + " 99" if len(at) == 10 else at


def day_of(at):
    return at[:10]


def site_key(text):
    """A site as the `site` column spells it, or a name for a room with none."""
    key = (text or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,39}", key):
        raise ValueError(f"{text!r} is not a site name: letters and digits, "
                         "e.g. acr, ignition, clubwpt")
    return key


def stake(text):
    """
    (big blind, straddled) from what a person types for a stake.

    "0.05/0.10" is unstraddled and "0.05/0.10/0.20" straddled -- the third
    number is the straddle, as ClubWPT's title bar writes it. "10NL" says
    nothing about a straddle and "10NL straddle" says there was one; "$0.10"
    is the big blind alone. None for either part that is not said, and an
    empty stake is (None, None).
    """
    text = (text or "").strip().lower().replace("$", "")
    if not text:
        return None, None
    straddled = True if "straddle" in text else None
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if "/" in text and len(nums) >= 2:
        return float(Decimal(nums[1])), len(nums) >= 3 or straddled is True
    m = re.search(r"(\d+(?:\.\d+)?)\s*nl", text)
    if m:
        return float(Decimal(m.group(1)) / 100), straddled
    if len(nums) == 1:
        return float(Decimal(nums[0])), straddled
    raise ValueError(f"{text!r} is not a stake: write 0.05/0.10, "
                     "0.05/0.10/0.20 for a straddle, or 10NL")


def stake_label(bb, straddled):
    if bb is None:
        return "mixed stakes"
    nl = Decimal(str(bb)) * 100
    name = f"{nl.normalize():f}NL"
    return name + (" straddle" if straddled else "")


# ---- the tables --------------------------------------------------------

def ensure(con):
    """
    The ledger's tables, made the first time and never remade.

    The database is copied before they are first made, because this is a
    schema change to a file that holds your hands, and CLAUDE.md's rule is a
    copy first. Every later call finds them and does nothing.
    """
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if set(TABLES) <= have:
        return
    path = next((r[2] for r in con.execute("PRAGMA database_list")
                 if r[1] == "main"), "")
    if path and have and Path(path).exists():
        backup = Path(path).with_name(
            Path(path).name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        target = sqlite3.connect(backup)
        con.backup(target)
        target.close()
    con.executescript(SCHEMA)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def currency_of(con, site):
    ensure(con)
    row = con.execute("SELECT currency FROM bankroll_accounts WHERE site=?",
                      (site,)).fetchone()
    return row[0] if row else None


def set_account(con, site, currency):
    """
    Say which currency a site keeps your money in.

    Once rows exist in one currency the account cannot be moved to another:
    the rows were typed in the first, and relabelling them would turn
    dollars into euros without changing a digit.
    """
    ensure(con)
    site = site_key(site)
    currency = (currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", currency):
        raise ValueError(f"{currency!r} is not a currency code: USD, EUR, GBP...")
    old = currency_of(con, site)
    if old and old != currency:
        used = sum(con.execute(f"SELECT COUNT(*) FROM {t} WHERE site=?", (site,)).fetchone()[0]
                   for t in ("bankroll_entries", "bankroll_balances", "bankroll_sessions"))
        if used:
            raise ValueError(f"{site} already has {used} rows in {old}; "
                             "a site keeps one currency")
    con.execute("INSERT OR REPLACE INTO bankroll_accounts VALUES (?, ?, ?)",
                (site, currency, _now()))
    con.commit()
    return site, currency


def _account(con, site, currency=None):
    """The site's currency, stating it now if `currency` is given."""
    site = site_key(site)
    have = currency_of(con, site)
    if currency:
        currency = currency.strip().upper()
        if have and have != currency:
            raise ValueError(f"{site} keeps {have}, not {currency}")
        if not have:
            set_account(con, site, currency)
        return site, currency
    if not have:
        raise ValueError(f"which currency does {site} keep? say it once: "
                         f"bankroll.py --account {site} USD")
    return site, have


def add_entry(con, site, kind, amount, at=None, note="", source="typed",
              currency=None, commit=True):
    """A deposit, withdrawal, bonus or rakeback. Returns its id."""
    ensure(con)
    if kind not in KINDS or kind.startswith("transfer"):
        raise ValueError(f"{kind!r}: an entry is a deposit, withdrawal, bonus "
                         "or rakeback; a transfer has its own command")
    site, cur = _account(con, site, currency)
    c = cents(amount) * KINDS[kind]
    rid = con.execute(
        "INSERT INTO bankroll_entries (site, at, kind, cents, currency, note, "
        "source, made) VALUES (?,?,?,?,?,?,?,?)",
        (site, when(at), kind, c, cur, note or "", source, _now())).lastrowid
    if commit:
        con.commit()
    return rid


def transfer(con, from_site, to_site, amount, at=None, to_amount=None, note=""):
    """
    Money moved between two sites: one row out, one row in, one event.

    Between two currencies the amount that arrived is not the amount that
    left, and the rate is the cashier's, so `to_amount` is required then
    rather than worked out.
    """
    ensure(con)
    src, src_cur = _account(con, from_site)
    dst, dst_cur = _account(con, to_site)
    if src == dst:
        raise ValueError("a transfer goes between two sites")
    out = cents(amount)
    if src_cur != dst_cur and to_amount is None:
        raise ValueError(f"{src} keeps {src_cur} and {dst} keeps {dst_cur}: "
                         "say what arrived with --to-amount")
    arrived = cents(to_amount) if to_amount is not None else out
    at = when(at)
    first = con.execute(
        "INSERT INTO bankroll_entries (site, at, kind, cents, currency, note, "
        "source, made) VALUES (?,?,?,?,?,?,?,?)",
        (src, at, "transfer_out", -out, src_cur, note or f"to {dst}", "typed",
         _now())).lastrowid
    con.execute("UPDATE bankroll_entries SET transfer=? WHERE id=?", (first, first))
    con.execute(
        "INSERT INTO bankroll_entries (site, at, kind, cents, currency, transfer, "
        "note, source, made) VALUES (?,?,?,?,?,?,?,?,?)",
        (dst, at, "transfer_in", arrived, dst_cur, first, note or f"from {src}",
         "typed", _now()))
    con.commit()
    return first


def add_balance(con, site, amount, at=None, note="", source="typed",
                currency=None, commit=True):
    """What the site says you have. Compared, never applied. Returns its id."""
    ensure(con)
    site, cur = _account(con, site, currency)
    rid = con.execute(
        "INSERT INTO bankroll_balances (site, at, cents, currency, note, source, "
        "made) VALUES (?,?,?,?,?,?,?)",
        (site, when(at), cents(amount), cur, note or "", source, _now())).lastrowid
    if commit:
        con.commit()
    return rid


def add_session(con, site, result, at=None, hours=None, hands=None,
                stake_text="", note="", source="typed", currency=None,
                commit=True):
    """
    A session you played where the hands are not here, typed.

    Several on one day are several sessions: the old script kept one row per
    date, so a second session that day replaced the first.
    """
    ensure(con)
    site, cur = _account(con, site, currency)
    bb, straddled = stake(stake_text)
    minutes = None
    if hours not in (None, ""):
        minutes = float(Decimal(str(hours)) * 60)
        if minutes < 0:
            raise ValueError("hours cannot be negative")
    n = None
    if hands not in (None, ""):
        n = int(hands)
        if n < 0:
            raise ValueError("hands cannot be negative")
    c = None if result is None else cents(result, signed=True)
    rid = con.execute(
        "INSERT INTO bankroll_sessions (site, started, minutes, hands, bb, "
        "straddle, cents, currency, note, source, made) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (site, when(at), minutes, n, bb,
         None if straddled is None else int(straddled), c, cur, note or "",
         source, _now())).lastrowid
    if commit:
        con.commit()
    return rid


def delete(con, ref):
    """
    One row, by the id `--entries` shows: e12, b3, s7.

    Nothing else moves. Every row holds its own amount, so deleting a day
    cannot change the result of the day after it -- the old script derived
    each day's profit from the balance before it, and deleting one left
    every later profit wrong. A transfer is one event in two rows, and goes
    as one.
    """
    ensure(con)
    m = re.fullmatch(r"([ebs])(\d+)", (ref or "").strip().lower())
    if not m:
        raise ValueError(f"{ref!r}: an id is e12 (money in or out), "
                         "b3 (a balance) or s7 (a session)")
    table = {"e": "bankroll_entries", "b": "bankroll_balances",
             "s": "bankroll_sessions"}[m.group(1)]
    rid = int(m.group(2))
    if table == "bankroll_entries":
        row = con.execute("SELECT transfer FROM bankroll_entries WHERE id=?",
                          (rid,)).fetchone()
        if row and row[0]:
            gone = con.execute("DELETE FROM bankroll_entries WHERE transfer=?",
                               (row[0],)).rowcount
            con.commit()
            return gone
    gone = con.execute(f"DELETE FROM {table} WHERE id=?", (rid,)).rowcount
    con.commit()
    if not gone:
        raise ValueError(f"no row {ref}")
    return gone


# ---- the plan ------------------------------------------------------------

def load_plan(path=None):
    """
    Your plan from `bankroll.json` if there is one, else the default, checked.

    The file replaces the default whole rather than merging into it: a plan
    is one opinion, and half of yours on top of half of the default is
    neither.
    """
    path = Path(path) if path else PLAN_FILE
    plan = DEFAULT_PLAN
    origin = "the default plan, in bankroll.py"
    if path.exists():
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{path}: {e}") from None
        origin = str(path)
    return check_plan(plan), origin


def check_plan(plan):
    """
    The plan as numbers, or a ValueError naming what is wrong with it.

    Two stages of the old plan were both "Phase 4", and its title named a
    start of $40 against a first entry of $164: so names are unique, the
    start is the first tier's entry and nothing else, and every tier begins
    where the one before it ends.
    """
    try:
        tiers = [{"name": str(t["name"]), "bb": float(Decimal(str(t["bb"]))),
                  "straddle": t.get("straddle"),
                  "buyin": cents(t["buyin"]), "entry": cents(t["entry"]),
                  "target": cents(t["target"])} for t in plan["tiers"]]
        out = {"name": str(plan.get("name") or "the plan"),
               "currency": str(plan.get("currency", "USD")).upper(),
               "sites": [site_key(s) for s in plan.get("sites") or []],
               "stop_loss_buyins": float(plan.get("stop_loss_buyins", 5)),
               "stop_loss_in_effect_under": float(
                   plan.get("stop_loss_in_effect_under", 30)),
               "after": str(plan.get("after", "")), "tiers": tiers}
    except (KeyError, TypeError, ValueError, InvalidOperation) as e:
        raise ValueError(f"the plan does not read: {e}") from None
    if not tiers:
        raise ValueError("the plan has no tiers")
    names = [t["name"] for t in tiers]
    if len(set(names)) != len(names):
        raise ValueError("two tiers of the plan have the same name")
    for t in tiers:
        if t["straddle"] not in (None, True, False):
            raise ValueError(f"{t['name']}: straddle is true, false or null")
        if t["buyin"] <= 0 or t["entry"] <= 0 or t["target"] <= t["entry"]:
            raise ValueError(f"{t['name']}: a buy-in and an entry above "
                             "nothing, and a target above the entry")
    for a, b in zip(tiers, tiers[1:]):
        if b["entry"] != a["target"]:
            raise ValueError(f"{b['name']} begins at {money(b['entry'])} but "
                             f"{a['name']} ends at {money(a['target'])}")
    if out["stop_loss_buyins"] <= 0:
        raise ValueError("stop_loss_buyins must be above zero")
    if out["stop_loss_in_effect_under"] <= 0:
        raise ValueError("stop_loss_in_effect_under must be above zero")
    out["start"] = tiers[0]["entry"]
    out["goal"] = tiers[-1]["target"]
    out["milestones"] = [t["target"] for t in tiers]
    return out


def tier_for(plan, bb, straddled):
    """
    The tier a stake belongs to, or None -- with the reason when it is None.

    By the stake PLAYED. Where two tiers share a big blind the straddle
    decides, and a stake whose straddle is not known cannot be placed.
    """
    if bb is None:
        return None, "the last session mixed stakes"
    same = [t for t in plan["tiers"] if abs(t["bb"] - bb) < 1e-9]
    if not same:
        return None, f"{stake_label(bb, straddled)} is not a stake in the plan"
    fits = [t for t in same if t["straddle"] is None or straddled is None
            or t["straddle"] == bool(straddled)]
    if len(fits) == 1:
        return fits[0], ""
    if straddled is None:
        return None, (f"{stake_label(bb, None)} is two tiers of the plan and "
                      "the last session does not say whether it was straddled")
    return None, f"{stake_label(bb, straddled)} is not a stake in the plan"


def buyin_of(plan, bb, straddled):
    """A buy-in of this stake, in cents: the plan's, or 100 big blinds."""
    tier, _why = tier_for(plan, bb, straddled)
    if tier:
        return tier["buyin"]
    if bb is None:
        return None
    return int(round(bb * STANDARD_BUYIN_BB * 100))


# ---- what happened -------------------------------------------------------

def sittings(con):
    """
    Your sittings from hands, with their money and stake, oldest first.

    Only sittings with cash hands in them: a sitting of tournament hands has
    no money here, as tournaments have none anywhere. None when the sittings
    were cut before they carried their money -- a database not rebuilt
    since -- and the caller says so rather than counting nothing.
    """
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "sessions" not in have:
        return []
    import sessions
    cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    if not set(sessions.MONEY_COLUMNS) <= cols:
        return None
    return [dict(zip(("id", "site", "started", "ended", "minutes", "hands",
                      "net", "net_bb", "bb", "straddle"), r))
            for r in con.execute(
                "SELECT session_id, site, started, ended, minutes, hands, net, "
                "net_bb, bb, straddle FROM sessions WHERE net IS NOT NULL "
                "ORDER BY started")]


def _span(row):
    """A manual session's start and end, for overlap with a sitting."""
    start = row["started"]
    if len(start) == 10:
        return start + " 00:00:00", start + " 23:59:59"
    end = start
    if row["minutes"]:
        t = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        end = datetime.fromtimestamp(t.timestamp() + 60 * row["minutes"]) \
            .strftime("%Y-%m-%d %H:%M:%S")
    return start, end


def manual_sessions(con, sits):
    """
    The typed sessions, each marked with the sittings that replace it.

    Replaced when a sitting of the same site overlaps it on the site's
    clock: that site's hands have arrived for that time, and counting both
    would count the session twice.
    """
    by_site = {}
    for s in sits or []:
        by_site.setdefault(s["site"], []).append(s)
    out = []
    for r in con.execute(
            "SELECT id, site, started, minutes, hands, bb, straddle, cents, "
            "currency, note, source FROM bankroll_sessions ORDER BY started, id"):
        row = dict(zip(("id", "site", "started", "minutes", "hands", "bb",
                        "straddle", "cents", "currency", "note", "source"), r))
        start, end = _span(row)
        row["replaced_by"] = [s["id"] for s in by_site.get(row["site"], [])
                              if s["started"] <= end and s["ended"] >= start]
        out.append(row)
    return out


def results(con, plan):
    """
    Every result in time order: sittings from hands, typed sessions not
    replaced. Each with its money in cents, its currency (None if the site's
    is not stated), its stake and its size in buy-ins of that stake.
    """
    sits = sittings(con)
    accounts = dict(con.execute("SELECT site, currency FROM bankroll_accounts"))
    out = []
    for s in sits or []:
        bi = buyin_of(plan, s["bb"], s["straddle"])
        c = int(round(s["net"] * 100))
        out.append({"key": s["ended"], "day": day_of(s["started"]), "site": s["site"],
                    "cents": c, "currency": accounts.get(s["site"]),
                    "minutes": s["minutes"], "hands": s["hands"],
                    "bb": s["bb"], "straddle": s["straddle"],
                    "buyins": c / bi if bi else None, "typed": False,
                    "inferred": False, "ref": f"sitting {s['id']}"})
    for m in manual_sessions(con, sits):
        if m["replaced_by"] or m["cents"] is None:
            continue
        bi = buyin_of(plan, m["bb"], m["straddle"])
        out.append({"key": order_key(_span(m)[1] if len(m["started"]) > 10
                                     else m["started"]),
                    "day": day_of(m["started"]), "site": m["site"],
                    "cents": m["cents"], "currency": m["currency"],
                    "minutes": m["minutes"], "hands": m["hands"],
                    "bb": m["bb"], "straddle": m["straddle"],
                    "buyins": m["cents"] / bi if bi else None, "typed": True,
                    "inferred": m["source"] == INFERRED, "ref": f"s{m['id']}"})
    out.sort(key=lambda r: r["key"])
    return out, sits


def flows(con):
    return [dict(zip(("id", "site", "at", "kind", "cents", "currency", "transfer",
                      "note", "source"), r)) for r in con.execute(
        "SELECT id, site, at, kind, cents, currency, transfer, note, source "
        "FROM bankroll_entries ORDER BY at, id")]


def balances(con):
    return [dict(zip(("id", "site", "at", "cents", "currency", "note", "source"), r))
            for r in con.execute(
                "SELECT id, site, at, cents, currency, note, source "
                "FROM bankroll_balances ORDER BY at, id")]


def swings(points):
    """
    Every downswing in a cumulative series, and the one still going.

    A downswing runs from a peak to the first time the series is back at
    it; its depth is the peak less the lowest point between. `points` are
    (day, cumulative value) in order; the series starts at nothing on the
    first point's day.
    """
    if not points:
        return [], None
    done, peak, low = [], (0.0, points[0][0]), None
    for day, v in points:
        if v >= peak[0]:
            if low is not None:
                done.append(_swing(peak, low, day))
            peak, low = (v, day), None
        elif low is None or v < low[0]:
            low = (v, day)
    current = _swing(peak, low, None) if low is not None else None
    if current:
        # Where it stands, which is not the trough once it has come part
        # of the way back.
        current["now"] = points[-1][1]
    return done, current


def _swing(peak, low, recovered):
    days = None
    if recovered:
        days = (date.fromisoformat(recovered) - date.fromisoformat(peak[1])).days
    return {"peak": peak[0], "start": peak[1], "trough": low[0],
            "trough_day": low[1], "drop": peak[0] - low[0],
            "recovered": recovered, "days": days}


def win_rates(con, sits):
    """
    bb/100 per site and stake, from the hands, with n and its measured error.

    `query.results_of` over your hands at that stake: the same numbers the
    results view prints, and per stake only -- a rate in big blinds is a
    rate at one stake, and the old script's dollars per 100 hands added
    2NL to 10NL. Your hands are found once, by the filter `--hero` builds,
    and split by the stake each was dealt at; `results_of` then prices each
    stake's own pairs, which is a join over that stake's hands and nothing
    more. A filter per stake was a search of every decision per stake, 29 of
    them on the corpus the checks run on.
    """
    import query
    if not sits:
        return []
    pairs = query.matching_seats(con, query.build(["--hero"])[0])
    dealt = {h: (site, bb, int(st) if st is not None else None)
             for h, site, bb, st in con.execute(
                 "SELECT hand_id, site, bb, straddle > 0 FROM hands "
                 "WHERE hero_seat IS NOT NULL")}
    groups = {}
    for pair in pairs:
        key = dealt.get(pair[0])
        if key and key[1]:
            groups.setdefault(key, []).append(pair)
    out = []
    for (site, bb, straddled), mine in sorted(groups.items(),
                                              key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        got = query.results_of(con, mine)
        if got:
            out.append({"site": site, "stake": stake_label(bb, straddled),
                        "bb": bb, "straddle": straddled,
                        "hands": got["hands"], "bb100": got["bb100"],
                        "error": got["error"], "typed": False})
    return out


def typed_rates(manual):
    """bb/100 of typed sessions that say their hands and stake. No error."""
    acc = {}
    for m in manual:
        if m["replaced_by"] or m["cents"] is None or not m["hands"] or m["bb"] is None:
            continue
        k = (m["site"], m["bb"], m["straddle"])
        hands, c = acc.get(k, (0, 0))
        acc[k] = (hands + m["hands"], c + m["cents"])
    return [{"site": site, "stake": stake_label(bb, st), "hands": n,
             "bb100": 100 * (c / 100 / bb) / n, "error": None, "typed": True}
            for (site, bb, st), (n, c) in sorted(acc.items(),
                                                key=lambda kv: (kv[0][0], kv[0][1]))]


def summary(con, plan=None):
    """
    Everything the bankroll view shows, worked out once, as data.

    The command line prints it with `report` and the window draws the same
    dict, so the two cannot disagree about a number.
    """
    ensure(con)
    if plan is None:
        plan, origin = load_plan()
    else:
        origin = "given"
    res, sits = results(con, plan)
    money_in = flows(con)
    points = balances(con)
    manual = manual_sessions(con, sits)
    accounts = dict(con.execute("SELECT site, currency FROM bankroll_accounts"))

    # Per currency, per site: nothing is ever added across currencies, and a
    # site whose currency is unstated is in no currency at all.
    cur = {}
    unstated = {}
    for r in res:
        if r["currency"] is None:
            u = unstated.setdefault(r["site"], {"site": r["site"], "cents": 0, "n": 0})
            u["cents"] += r["cents"]
            u["n"] += 1
            continue
        c = cur.setdefault(r["currency"], {"sites": {}, "minutes": 0.0})
        s = c["sites"].setdefault(r["site"], _blank_site())
        s["results"] += r["cents"]
        s["typed" if r["typed"] else "from_hands"] += r["cents"]
        if r["inferred"]:
            s["inferred"] += r["cents"]
        s["sessions"] += 1
        s["minutes"] += r["minutes"] or 0.0
        s["hands"] += r["hands"] or 0
    for f in money_in:
        c = cur.setdefault(f["currency"], {"sites": {}, "minutes": 0.0})
        s = c["sites"].setdefault(f["site"], _blank_site())
        s["flows"] += f["cents"]
        s[f["kind"]] = s.get(f["kind"], 0) + f["cents"]
    for code, c in cur.items():
        for site, s in c["sites"].items():
            s["bankroll"] = s["flows"] + s["results"]
        c["bankroll"] = sum(s["bankroll"] for s in c["sites"].values())
        c["results"] = sum(s["results"] for s in c["sites"].values())
        c["typed"] = sum(s["typed"] for s in c["sites"].values())
        c["inferred"] = sum(s["inferred"] for s in c["sites"].values())
        c["minutes"] = sum(s["minutes"] for s in c["sites"].values())
        c["hands"] = sum(s["hands"] for s in c["sites"].values())

    # Each balance you typed against what the ledger says you had then.
    recon = []
    for b in points:
        k = order_key(b["at"])
        computed = sum(f["cents"] for f in money_in
                       if f["site"] == b["site"] and order_key(f["at"]) <= k) \
            + sum(r["cents"] for r in res
                  if r["site"] == b["site"] and r["key"] <= k)
        recon.append({**b, "computed": computed, "gap": b["cents"] - computed})

    return {"plan": plan, "plan_origin": origin, "currencies": cur,
            "unstated": sorted(unstated.values(), key=lambda u: u["site"]),
            "accounts": accounts, "reconciliation": recon,
            "progress": progress(plan, res, money_in, accounts),
            "rates": win_rates(con, sits) + typed_rates(manual),
            "sittings_have_money": sits is not None,
            "replaced": [m for m in manual if m["replaced_by"]],
            "no_result": [m for m in manual if m["cents"] is None and not m["replaced_by"]],
            "rows": ledger_rows(money_in, points, manual)}


def _blank_site():
    return {"results": 0, "from_hands": 0, "typed": 0, "inferred": 0,
            "flows": 0, "sessions": 0, "minutes": 0.0, "hands": 0}


def progress(plan, res, money_in, accounts):
    """
    The plan measured: bankroll, the tier you are playing, the stop-loss,
    the next tier, the milestones reached, and the downswings.

    Over the plan's sites in the plan's currency, and nothing else.
    """
    code = plan["currency"]
    chosen = set(plan["sites"]) or {s for s, c in accounts.items() if c == code}
    wrong = sorted(s for s in plan["sites"] if accounts.get(s) not in (None, code))
    mine = [r for r in res if r["site"] in chosen and r["currency"] == code]
    flows_in = [f for f in money_in if f["site"] in chosen and f["currency"] == code]
    out = {"currency": code, "sites": sorted(chosen), "wrong_currency": wrong}

    # The bankroll over time, every flow and result in order, for the
    # milestones: the first day it stood at or above each.
    events = sorted([(order_key(f["at"]), day_of(f["at"]), f["cents"]) for f in flows_in]
                    + [(r["key"], r["day"], r["cents"]) for r in mine])
    total, reached = 0, {}
    for _k, day, c in events:
        total += c
        for m in plan["milestones"]:
            if total >= m and m not in reached:
                reached[m] = day
    out["bankroll"] = total
    out["milestones"] = [(m, reached.get(m)) for m in plan["milestones"]]

    # The latest session on any of the plan's sites, each by its own clock.
    # Nothing says two sites' clocks agree, so two sittings on two sites in
    # one evening can come out in the wrong order; the line names the site,
    # so that when they do it shows.
    last = next((r for r in reversed(mine) if r["bb"] is not None or r["typed"]), None)
    if last is None:
        out["playing"] = None
        out["why"] = "no session at any stake yet"
    else:
        tier, why = tier_for(plan, last["bb"], last["straddle"])
        out["playing"] = {"stake": stake_label(last["bb"], last["straddle"]),
                          "day": last["day"], "site": last["site"],
                          "tier": tier, "why": why}
        if tier:
            buyin = tier["buyin"]
            floor = tier["entry"] - plan["stop_loss_buyins"] * buyin
            in_effect = plan["stop_loss_in_effect_under"] * buyin
            out["stop_loss"] = {
                "buyin": buyin, "entry": tier["entry"],
                "floor": floor, "in_effect_under": in_effect,
                "in_effect": total < in_effect,
                "fires": total <= floor and total < in_effect,
                "buyins": total / buyin,
                "buyins_from_entry": (total - tier["entry"]) / buyin}
            later = plan["tiers"][plan["tiers"].index(tier) + 1:]
            if later:
                nxt = later[0]
                out["next"] = {"tier": nxt, "needed": nxt["entry"] - total,
                               "buyins": (nxt["entry"] - total) / tier["buyin"]}
    # What the bankroll alone would allow, said as that and never used for
    # the stop-loss: this is the old script's tier, kept only to compare.
    allowed = [t for t in plan["tiers"] if total >= t["entry"]]
    out["bankroll_allows"] = allowed[-1] if allowed else None

    # Downswings: results only -- a withdrawal is not a downswing -- in
    # dollars and in buy-ins of each result's own stake, so a run at 10NL
    # and one at 2NL are the same size when they cost the same buy-ins.
    dollars, buyins, unknown = [], [], 0
    run_c = run_b = 0.0
    for r in mine:
        run_c += r["cents"]
        dollars.append((r["day"], run_c))
        if r["buyins"] is None:
            unknown += 1
        else:
            run_b += r["buyins"]
            buyins.append((r["day"], run_b))
    out["downswings"] = {"dollars": swings(dollars), "buyins": swings(buyins),
                         "unsized": unknown}
    return out


def ledger_rows(money_in, points, manual):
    """Every row you made, newest first, with the id it is deleted by."""
    rows = []
    for f in money_in:
        rows.append({"id": f"e{f['id']}", "at": f["at"], "site": f["site"],
                     "what": f["kind"].replace("_", " "), "cents": f["cents"],
                     "currency": f["currency"], "note": f["note"],
                     "source": f["source"]})
    for b in points:
        rows.append({"id": f"b{b['id']}", "at": b["at"], "site": b["site"],
                     "what": "balance check", "cents": b["cents"],
                     "currency": b["currency"], "note": b["note"],
                     "source": b["source"]})
    for m in manual:
        what = "session (typed)"
        if m["replaced_by"]:
            what = f"session, replaced by {len(m['replaced_by'])} sitting(s)"
        elif m["source"] == INFERRED:
            what = "session (balance change)"
        detail = []
        if m["minutes"]:
            detail.append(f"{m['minutes'] / 60:g}h")
        if m["hands"]:
            detail.append(f"{m['hands']} hands")
        if m["bb"] is not None:
            detail.append(stake_label(m["bb"], m["straddle"]))
        rows.append({"id": f"s{m['id']}", "at": m["started"], "site": m["site"],
                     "what": what, "cents": m["cents"], "currency": m["currency"],
                     "note": "; ".join(filter(None, [" ".join(detail), m["note"]])),
                     "source": m["source"]})
    rows.sort(key=lambda r: (order_key(r["at"]), r["id"]), reverse=True)
    return rows


# ---- saying it -------------------------------------------------------------

def report(s):
    """The summary as lines of text: the command line's and the window's."""
    out = []
    say = out.append
    plan = s["plan"]
    if not s["sittings_have_money"]:
        say("Your sittings were cut before they carried their money; results")
        say("from hands are left out until `python sessions.py` (or an import)")
        say("cuts them again.")
        say("")
    if not s["currencies"] and not s["unstated"]:
        say("Nothing yet. Say which currency a site keeps, then add a deposit,")
        say("a balance or a session: bankroll.py --account clubwpt USD")
        return out
    for code, c in sorted(s["currencies"].items()):
        say(f"{code}: bankroll {money(c['bankroll'], code)}")
        for site, x in sorted(c["sites"].items()):
            parts = [f"results {money(x['results'], code)}"]
            if x["typed"]:
                parts.append(f"{money(x['typed'], code)} of it typed")
            if x["inferred"]:
                parts.append(f"{money(x['inferred'], code)} of that a balance "
                             "change from the old tracker")
            moved = {k: x[k] for k in KINDS if x.get(k)}
            for k, v in moved.items():
                parts.append(f"{k.replace('_', ' ')} {money(v, code)}")
            say(f"  {site:12} {money(x['bankroll'], code):>12}   " + ", ".join(parts))
        hours = c["minutes"] / 60
        if hours:
            say(f"  {hours:,.1f} hours, {money(int(c['results'] / hours), code)}/hour"
                f" over {c['hands']:,} hands")
        say("")
    for u in s["unstated"]:
        say(f"{u['site']}: results {u['cents'] / 100:+,.2f} over {u['n']} sittings, in a "
            f"currency nobody has stated -- added to nothing until: "
            f"bankroll.py --account {u['site']} USD")
    if s["unstated"]:
        say("")

    gaps = [r for r in s["reconciliation"] if r["gap"]]
    if s["reconciliation"]:
        say("Balance checks (what the site said, against the ledger):")
        for r in s["reconciliation"][-8:]:
            say(f"  {r['at']:16} {r['site']:10} said {money(r['cents'], r['currency']):>11}"
                f"  ledger {money(r['computed'], r['currency']):>11}"
                + (f"  gap {money(r['gap'], r['currency'])}" if r["gap"] else "  agrees"))
        if gaps:
            say("  A gap is money the ledger has no record of: a deposit, a")
            say("  withdrawal, a bonus or a session not entered. It is shown, never")
            say("  applied.")
        say("")

    p = s["progress"]
    code = plan["currency"]
    say(f"{plan['name']}: {money(plan['start'], code)} to {money(plan['goal'], code)}"
        + (f", then {plan['after']}" if plan["after"] else "")
        + f"   ({s['plan_origin']})")
    if p["wrong_currency"]:
        say(f"  not counted, they keep another currency: {', '.join(p['wrong_currency'])}")
    say(f"  bankroll {money(p['bankroll'], code)} on {', '.join(p['sites']) or 'no site'}"
        f" -- {100 * p['bankroll'] / plan['goal']:.0f}% of the goal")
    playing = p.get("playing")
    if playing and playing["tier"]:
        t = playing["tier"]
        sl = p["stop_loss"]
        say(f"  playing {playing['stake']} (last session {playing['day']} on {playing['site']}):"
            f" {t['name']},"
            f" entry {money(t['entry'], code)}, buy-in {money(t['buyin'], code)}")
        say(f"  {tenths(p['bankroll'], sl['buyin'])} buy-ins of {t['name']}, "
            f"{tenths(p['bankroll'] - sl['entry'], sl['buyin'], sign=True)} from the entry")
        under, gate = plan["stop_loss_buyins"], plan["stop_loss_in_effect_under"]
        if sl["floor"] < sl["in_effect_under"]:
            rule = (f"stop-loss at {money(sl['floor'], code)}: {under:g} buy-ins under "
                    f"the entry, in effect under {gate:g} buy-ins "
                    f"({money(sl['in_effect_under'], code)})")
        else:
            # The entry's rule would fire first, while the bankroll is still
            # deep in buy-ins, and is not in effect there.
            rule = (f"stop-loss under {money(sl['in_effect_under'], code)}: "
                    f"{under:g} under the entry is {money(sl['floor'], code)}, "
                    f"but it is not in effect until under {gate:g} buy-ins")
        say("  " + rule + ("   <-- STOP-LOSS: move down" if sl["fires"] else ""))
        if "next" in p:
            n = p["next"]
            if n["needed"] <= 0:
                say(f"  {n['tier']['name']} is open: its entry is {money(n['tier']['entry'], code)}")
            else:
                say(f"  needed for {n['tier']['name']}: {money(n['needed'], code)}"
                    f" ({n['buyins']:.1f} buy-ins of {t['name']})")
    elif playing:
        say(f"  playing {playing['stake']}: {playing['why']}, so no stop-loss is measured")
    else:
        say(f"  {p['why']}")
    allows = p["bankroll_allows"]
    if allows and (not playing or playing.get("tier") != allows):
        say(f"  (the bankroll alone would allow {allows['name']}; the stop-loss goes by")
        say(f"  the stake played, not by this)")
    for m, day in p["milestones"]:
        say(f"  {money(m, code):>10}  " + (f"reached {day}" if day else "not yet"))
    say("")

    say("Win rates, per stake (bb/100 with its error, over n hands):")
    for r in s["rates"]:
        say(f"  {r['site']:12} {r['stake']:16} {r['bb100']:+8.1f} bb/100"
            + ("" if r["typed"] else f" +/- {r['error']:6.1f}")
            + f"   n={r['hands']:,}   {rate_verdict(r)}")
    if not s["rates"]:
        say("  none yet")
    say("  Over a few hundred hands a win rate is mostly noise: the error is")
    say("  measured from the hands, and a rate inside twice it says nothing yet.")
    say("")

    ds = p["downswings"]
    for unit, (done, current) in (("buy-ins", ds["buyins"]), ("dollars", ds["dollars"])):
        fmt = (lambda v: f"{v:.1f} BI") if unit == "buy-ins" else (lambda v: money(int(v), code))
        say(f"Downswings in {unit} (results only, {code}):")
        deepest = sorted(done, key=lambda d: -d["drop"])
        for d in deepest[:5]:
            say(f"  {d['start']} peak {fmt(d['peak'])}, trough {fmt(d['trough'])} on "
                f"{d['trough_day']}, down {fmt(d['drop'])}, back {d['recovered']}"
                f" after {d['days']} days")
        if len(deepest) > 5:
            say(f"  and {len(deepest) - 5} shallower ones")
        if current:
            say(f"  NOW: {fmt(current['peak'] - current['now'])} under the peak of "
                f"{fmt(current['peak'])} on {current['start']}; at worst "
                f"{fmt(current['drop'])} under, at {fmt(current['trough'])} on "
                f"{current['trough_day']}")
        if not done and not current:
            say("  none")
    if ds["unsized"]:
        say(f"  {ds['unsized']} results have no stake, so no size in buy-ins")
    if s["replaced"]:
        say("")
        say(f"{len(s['replaced'])} typed sessions are replaced by the hands that arrived "
            "for them and are not counted.")
    if s["no_result"]:
        say(f"{len(s['no_result'])} typed sessions have no result (the first day of an "
            "imported tracker has nothing before it to measure against).")
    return out



def rate_verdict(r):
    """
    What a win rate can be taken to say, in words, beside the number.

    Under 30 hands the error itself is not measured -- one hand has no
    spread, and `results_of` falls back to 1170/sqrt(n) -- so the rate is an
    anecdote whatever the arithmetic says; it printed "-11,322 +/- 1,170 over
    1 hand" as though that cleared its error. A typed session has no hands
    to measure an error from at all.
    """
    if r["typed"]:
        return "typed: no error, the hands are not here"
    if r["hands"] < MIN_RATE_HANDS:
        return f"under {MIN_RATE_HANDS} hands: an anecdote, not a rate"
    if abs(r["bb100"]) <= 2 * r["error"]:
        return "inside twice its error: says nothing yet"
    return "clear of twice its error"


# ---- the old tracker --------------------------------------------------------

# What the old tracker's columns might be called. One row per day with its
# end bankroll, hours, hands, stake and notes is what it kept; the names are
# looked for rather than assumed, and an import that cannot place a column
# stops and lists what it found.
ALIASES = {
    "date": ("date", "day", "session_date", "dt"),
    "bankroll": ("bankroll", "balance", "end_bankroll", "ending_bankroll",
                 "end_balance", "br", "end"),
    "hours": ("hours", "hrs", "duration", "time_played"),
    "hands": ("hands", "hand_count", "num_hands", "hands_played"),
    "stake": ("stake", "stakes", "level", "limit", "game"),
    "notes": ("notes", "note", "comment", "comments"),
}
OLD_DATES = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y",
             "%m/%d/%y", "%d.%m.%Y")


def _place(columns):
    """{field: column} for a table's columns, or None if date and bankroll are not both there."""
    lower = {c.lower(): c for c in columns}
    found = {}
    for field, names in ALIASES.items():
        hit = next((lower[n] for n in names if n in lower), None)
        if hit:
            found[field] = hit
    return found if {"date", "bankroll"} <= set(found) else None


def _old_date(text):
    text = str(text).strip()
    for fmt in OLD_DATES:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"{text!r} is not a date this importer knows")


def import_rows(con, rows, site, source, digest, path, currency=None):
    """
    Old rows, each a balance check and a typed session, in one transaction.

    The session's result is the change from the previous day's balance,
    because that is all the old tracker knew -- and it may hide a deposit
    made that day, which is why every one is marked as a balance change
    wherever it is counted. The first day has no day before it, so its
    session has no result. Nothing is dropped: a row this cannot read stops
    the import and is named.
    """
    ensure(con)
    if con.execute("SELECT 1 FROM bankroll_imports WHERE digest=?", (digest,)).fetchone():
        raise ValueError(f"{path} has been imported already")
    site, cur = _account(con, site, currency)
    bad, clean = [], []
    for i, r in enumerate(rows, 1):
        try:
            day = _old_date(r["date"])
            bal = cents(str(r["bankroll"]).replace("$", ""), signed=True)
        except (KeyError, ValueError) as e:
            bad.append(f"row {i}: {e}")
            continue
        clean.append((day, i, bal, r))
    if bad:
        raise ValueError("these rows do not read, and nothing was imported:\n  "
                         + "\n  ".join(bad))
    clean.sort(key=lambda x: (x[0], x[1]))
    prev = None
    try:
        for day, i, bal, r in clean:
            note = str(r.get("notes") or "").strip()
            stake_text = str(r.get("stake") or "").strip()
            try:
                stake(stake_text)
            except ValueError:
                note = "; ".join(filter(None, [f"stake {stake_text!r}", note]))
                stake_text = ""
            add_balance(con, site, bal / 100, day, note=f"{source} row {i}",
                        source=source, commit=False)
            add_session(con, site, None if prev is None else (bal - prev) / 100,
                        day, hours=r.get("hours") or None,
                        hands=r.get("hands") or None, stake_text=stake_text,
                        note=note, source=INFERRED, commit=False)
            prev = bal
        con.execute("INSERT INTO bankroll_imports VALUES (?,?,?,?)",
                    (digest, str(path), len(clean), _now()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(clean)


def import_old(con, path, site, currency=None):
    """Your old bankroll_tracker.db: every day's row, and a list of what else was in it."""
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    old = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in old.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        placed, others = [], []
        for t in tables:
            cols = [r[1] for r in old.execute(f'PRAGMA table_info("{t}")')]
            n = old.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            where = _place(cols)
            (placed if where else others).append((t, cols, n, where))
        if len(placed) != 1:
            listing = "; ".join(f"{t} ({n} rows: {', '.join(c)})"
                                for t, c, n, _w in placed + others)
            raise ValueError("cannot tell which table holds the days -- "
                             f"{'none' if not placed else 'several'} have a date and a "
                             f"bankroll column. The file holds: {listing}")
        t, cols, n, where = placed[0]
        rows = []
        for raw in old.execute(f'SELECT * FROM "{t}" ORDER BY rowid'):
            rec = dict(zip(cols, raw))
            rows.append({field: rec[col] for field, col in where.items()})
    finally:
        old.close()
    got = import_rows(con, rows, site, f"{path.name}:{t}", digest, path, currency)
    return got, [(o[0], o[2]) for o in others if o[2]]


def import_csv(con, path, site, currency=None):
    """
    A Date,Bankroll CSV -- with Hours, Hands, Stake and Notes if it has them.

    Named for what it reads. The old script's "Import CSV (H2N/DriveHUD)"
    read this layout of its own and nothing either program writes.
    """
    path = Path(path)
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    where = _place(reader.fieldnames or [])
    if not where:
        raise ValueError(f"{path} needs a Date and a Bankroll column; it has "
                         f"{', '.join(reader.fieldnames or []) or 'none'}")
    rows = [{field: rec.get(col) for field, col in where.items()} for rec in reader]
    return import_rows(con, rows, site, path.name,
                       hashlib.sha256(data).hexdigest(), path, currency)


# ---- the command line --------------------------------------------------------

def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise ValueError(f"{name} needs a value")
        return argv[i + 1]
    return default


def main(argv, db_path=DB):
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    if "--plan" in argv:
        # Before the connection: reading the plan is no reason to make the
        # ledger's tables, and making them copies the whole of hands.db.
        plan, origin = load_plan()
        print(f"{plan['name']} -- {origin}")
        for t in plan["tiers"]:
            st = {None: "either", True: "straddled", False: "no straddle"}[t["straddle"]]
            cur = plan["currency"]
            print(f"  {t['name']:26} bb {t['bb']:<5} {st:12} buy-in {money(t['buyin'], cur)}"
                  f"  entry {money(t['entry'], cur)}  target {money(t['target'], cur)}")
        print(f"  stop-loss {plan['stop_loss_buyins']:g} buy-ins under the entry "
              "of the tier you are playing, in effect only under "
              f"{plan['stop_loss_in_effect_under']:g} buy-ins of its stake")
        return 0
    con = sqlite3.connect(db_path)
    try:
        ensure(con)
        date_ = _opt(argv, "--date")
        note = _opt(argv, "--note", "")
        if "--account" in argv:
            i = argv.index("--account")
            site, code = set_account(con, argv[i + 1], argv[i + 2])
            print(f"{site} keeps {code}")
        elif any(a in argv for a in ("--deposit", "--withdraw", "--bonus", "--rakeback")):
            flag = next(a for a in ("--deposit", "--withdraw", "--bonus", "--rakeback")
                        if a in argv)
            kind = {"--deposit": "deposit", "--withdraw": "withdrawal",
                    "--bonus": "bonus", "--rakeback": "rakeback"}[flag]
            i = argv.index(flag)
            rid = add_entry(con, argv[i + 1], kind, argv[i + 2], date_, note,
                            currency=_opt(argv, "--currency"))
            print(f"e{rid}: {kind} recorded")
        elif "--transfer" in argv:
            i = argv.index("--transfer")
            rid = transfer(con, argv[i + 1], argv[i + 2], argv[i + 3], date_,
                           _opt(argv, "--to-amount"), note)
            print(f"e{rid}: transfer recorded")
        elif "--balance" in argv:
            i = argv.index("--balance")
            rid = add_balance(con, argv[i + 1], argv[i + 2], date_, note,
                              currency=_opt(argv, "--currency"))
            print(f"b{rid}: balance recorded; it is compared with the ledger, not applied")
        elif "--session" in argv:
            i = argv.index("--session")
            if date_ is None:
                raise ValueError("a session needs --date")
            rid = add_session(con, argv[i + 1], argv[i + 2], date_,
                              _opt(argv, "--hours"), _opt(argv, "--hands"),
                              _opt(argv, "--stake", ""), note,
                              currency=_opt(argv, "--currency"))
            print(f"s{rid}: typed session recorded")
        elif "--delete" in argv:
            gone = delete(con, _opt(argv, "--delete"))
            print(f"deleted {gone} row{'s' if gone != 1 else ''}")
        elif "--entries" in argv:
            s = summary(con)
            for r in s["rows"]:
                print(f"{r['id']:>6} {r['at']:19} {r['site']:10} {r['what']:28} "
                      f"{money(r['cents'], r['currency']):>12}  {r['note']}")
        elif "--import-old" in argv or "--import-csv" in argv:
            flag = "--import-old" if "--import-old" in argv else "--import-csv"
            site = _opt(argv, "--site")
            if not site:
                raise ValueError(f"{flag} needs --site: which room were these days played on?")
            if flag == "--import-old":
                n, others = import_old(con, _opt(argv, flag), site, _opt(argv, "--currency"))
            else:
                n, others = import_csv(con, _opt(argv, flag), site, _opt(argv, "--currency")), []
            print(f"imported {n} days: each a balance check and a typed session")
            for t, rows in others:
                print(f"  not a list of days, so not imported: {t} ({rows} rows)")
        else:
            print("\n".join(report(summary(con))))
        return 0
    except (ValueError, IndexError) as e:
        print(f"bankroll: {e}" if str(e) else "bankroll: missing an argument -- see --help",
              file=sys.stderr)
        return 1
    finally:
        con.close()


# ---- the check ------------------------------------------------------------

def check(db_path=DB):
    """
    The ledger's arithmetic, its reconciliation, the stop-loss at a tier
    boundary, downswings on a series worked out by hand, the importer on a
    made-up old file, and a rebuild that leaves the ledger alone.

    Every part runs on a scratch copy; the rebuild runs on a copy of this
    machine's database when there is one -- the real sittings, with their
    money held to the hands' -- and on an empty one made for it when not.
    """
    import shutil
    import tempfile
    fails = []

    def ok(cond, what):
        print(f"  {'ok ' if cond else 'BAD'}  {what}")
        if not cond:
            fails.append(what)

    plan = check_plan(DEFAULT_PLAN)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        print("the ledger")
        con = sqlite3.connect(tmp / "ledger.db")
        ensure(con)
        set_account(con, "clubwpt", "USD")
        set_account(con, "acr", "USD")
        set_account(con, "stars", "EUR")
        add_entry(con, "clubwpt", "deposit", "100", "2026-10-01")
        add_entry(con, "clubwpt", "withdrawal", "30", "2026-10-03")
        add_entry(con, "clubwpt", "bonus", "5", "2026-10-03")
        add_entry(con, "clubwpt", "rakeback", "2.50", "2026-10-04")
        s1 = add_session(con, "clubwpt", "20", "2026-10-02", hours=2, hands=150,
                         stake_text="0.05/0.10")
        add_session(con, "clubwpt", "-7.25", "2026-10-02", hours=1, hands=60,
                    stake_text="0.05/0.10")
        transfer(con, "clubwpt", "acr", "10", "2026-10-04")
        add_entry(con, "stars", "deposit", "50", "2026-10-01")
        s = summary(con, plan)
        usd, eur = s["currencies"]["USD"], s["currencies"]["EUR"]
        ok(usd["sites"]["clubwpt"]["bankroll"] == 10000 - 3000 + 500 + 250 + 2000 - 725 - 1000,
           "clubwpt: 100 in, 30 out, 5 bonus, 2.50 rakeback, +20 and -7.25, 10 sent = $80.25")
        ok(usd["sites"]["acr"]["bankroll"] == 1000, "acr: the $10 that arrived")
        ok(usd["bankroll"] == 9025 and eur["bankroll"] == 5000,
           "USD $90.25 and EUR €50.00, never added together")
        ok(usd["results"] == 1275,
           "results are the sessions only ($12.75): deposits and bonuses are not winnings")
        ok(sum(1 for r in s["rows"] if r["what"].startswith("session")) == 2,
           "two sessions on one day are two sessions")
        try:
            add_entry(con, "clubwpt", "deposit", "-5")
            ok(False, "a negative deposit is refused")
        except ValueError:
            ok(True, "a negative deposit is refused")
        try:
            transfer(con, "clubwpt", "stars", "10")
            ok(False, "a transfer between currencies needs what arrived")
        except ValueError:
            ok(True, "a transfer between currencies needs what arrived")

        print("reconciliation")
        add_balance(con, "clubwpt", "95", "2026-10-02")
        add_balance(con, "clubwpt", "82", "2026-10-04")
        s = summary(con, plan)
        gaps = {r["at"]: r for r in s["reconciliation"]}
        ok(gaps["2026-10-02"]["computed"] == 10000 + 2000 - 725
           and gaps["2026-10-02"]["gap"] == 9500 - 11275,
           "a balance is measured against the ledger on its own day: $95 vs $112.75")
        ok(gaps["2026-10-04"]["gap"] == 8200 - 8025,
           "and the later one against its day: a gap of $1.75")
        ok(s["currencies"]["USD"]["sites"]["clubwpt"]["bankroll"] == 8025,
           "a balance changes nothing: the bankroll is still $80.25")
        add_entry(con, "clubwpt", "deposit", "40", "2026-09-30")
        s = summary(con, plan)
        gaps = {r["at"]: r for r in s["reconciliation"]}
        ok(gaps["2026-10-02"]["computed"] == 15275,
           "a back-dated deposit is counted where it belongs, before the 2nd")
        delete(con, f"s{s1}")
        s = summary(con, plan)
        left = [r for r in s["rows"] if r["what"].startswith("session")]
        ok(len(left) == 1 and left[0]["cents"] == -725,
           "deleting a session leaves the other one's result as it was")
        con.close()

        print("the stop-loss at a tier boundary: 5 buy-ins under the entry, "
              "in effect under 30")
        # Bankrolls in cents, at the edges of both numbers. 2NL Deep is the
        # tier where thirty buy-ins decides: $144 is five under its entry
        # but 36 buy-ins of $4, so it is not in effect until under $120.
        for bankroll, stake_text, expect_tier, fires in (
                (25000, "0.05/0.10", "10NL Deep", False),
                (20100, "0.05/0.10", "10NL Deep", False),
                (20000, "0.05/0.10", "10NL Deep", True),
                (19900, "0.05/0.10", "10NL Deep", True),
                (50000, "0.05/0.10/0.20", "10NL Straddle", True),
                (52000, "0.05/0.10/0.20", "10NL Straddle", False),
                (14400, "0.01/0.02", "2NL Deep", False),
                (12000, "0.01/0.02", "2NL Deep", False),
                (11999, "0.01/0.02", "2NL Deep", True),
                (100000, "0.10/0.20", "20NL (effectively 40NL)", True),
                (100001, "0.10/0.20", "20NL (effectively 40NL)", False)):
            con = sqlite3.connect(":memory:")
            ensure(con)
            set_account(con, "clubwpt", "USD")
            add_entry(con, "clubwpt", "deposit", "164", "2026-09-01")
            add_session(con, "clubwpt", f"{(bankroll - 16400) / 100:.2f}", "2026-09-02",
                        stake_text=stake_text)
            p = summary(con, plan)["progress"]
            t = p["playing"]["tier"]
            ok(t and t["name"] == expect_tier and p["stop_loss"]["fires"] == fires,
               f"{money(bankroll, 'USD')} at {stake_text}: {expect_tier}, "
               f"{tenths(bankroll, p['stop_loss']['buyin'])} buy-ins, "
               f"{tenths(bankroll - p['stop_loss']['entry'], p['stop_loss']['buyin'], True)}"
               " from the entry, "
               f"stop-loss {'fires' if fires else 'quiet'}")
            if bankroll == 19900:
                old = p["bankroll_allows"]
                ok(old["name"] == "2NL Deep" and 19900 > old["entry"] - 5 * old["buyin"],
                   "the old rule -- the tier from the bankroll -- read 2NL here and "
                   "stayed silent")
            con.close()
        con = sqlite3.connect(":memory:")
        ensure(con)
        set_account(con, "clubwpt", "USD")
        add_entry(con, "clubwpt", "deposit", "300", "2026-09-01")
        add_session(con, "clubwpt", "10", "2026-09-02", stake_text="10NL")
        p = summary(con, plan)["progress"]
        ok(p["playing"]["tier"] is None and "two tiers" in p["playing"]["why"],
           "10NL with the straddle not said is two tiers, and no stop-loss is guessed")
        con.close()

        print("downswings on a series worked out by hand")
        series = [("2026-01-01", 10), ("2026-01-02", -10), ("2026-01-03", -15),
                  ("2026-01-05", 15), ("2026-01-06", 9), ("2026-01-07", 11)]
        done, current = swings(series)
        ok(len(done) == 1 and done[0]["peak"] == 10 and done[0]["trough"] == -15
           and done[0]["drop"] == 25 and done[0]["recovered"] == "2026-01-05"
           and done[0]["days"] == 4,
           "peak 10 on the 1st, trough -15 on the 3rd, back on the 5th: 25 down, 4 days")
        ok(current and current["peak"] == 15 and current["trough"] == 9
           and current["drop"] == 6 and current["recovered"] is None
           and current["now"] == 11,
           "and one still going: 6 down from 15 at worst, 4 down now")
        done, current = swings([("2026-01-01", -3), ("2026-01-02", 1)])
        ok(len(done) == 1 and done[0]["drop"] == 3 and current is None,
           "a downswing from the very first session counts from nothing")
        con = sqlite3.connect(":memory:")
        ensure(con)
        set_account(con, "clubwpt", "USD")
        add_entry(con, "clubwpt", "deposit", "300", "2026-09-01")
        add_session(con, "clubwpt", "40", "2026-09-02", stake_text="0.05/0.10")
        add_session(con, "clubwpt", "-8", "2026-09-03", stake_text="0.01/0.02")
        add_session(con, "clubwpt", "-60", "2026-09-04", stake_text="0.05/0.10")
        add_entry(con, "clubwpt", "withdrawal", "200", "2026-09-05")
        p = summary(con, plan)["progress"]
        (_, cur_d), (_, cur_b) = p["downswings"]["dollars"], p["downswings"]["buyins"]
        ok(cur_d["drop"] == 6800 and abs(cur_b["drop"] - (2 + 3)) < 1e-9,
           "$68 down is 5 buy-ins: 2 at 2NL ($4 each) and 3 at 10NL ($20); "
           "the $200 withdrawal is no part of it")
        con.close()

        print("the plan")
        bad = json.loads(json.dumps(DEFAULT_PLAN))
        bad["tiers"][3]["name"] = bad["tiers"][2]["name"]
        try:
            check_plan(bad)
            ok(False, "two stages with one name are refused")
        except ValueError:
            ok(True, "two stages with one name are refused")
        gap = json.loads(json.dumps(DEFAULT_PLAN))
        gap["tiers"][1]["entry"] = "350"
        try:
            check_plan(gap)
            ok(False, "a tier that does not begin where the last one ends is refused")
        except ValueError:
            ok(True, "a tier that does not begin where the last one ends is refused")
        mine = tmp / "bankroll.json"
        own = json.loads(json.dumps(DEFAULT_PLAN))
        own["name"] = "mine"
        own["stop_loss_buyins"] = 3
        del own["stop_loss_in_effect_under"]
        mine.write_text(json.dumps(own), encoding="utf-8")
        got, origin = load_plan(mine)
        ok(got["name"] == "mine" and got["stop_loss_buyins"] == 3 and origin == str(mine)
           and got["stop_loss_in_effect_under"] == 30,
           "a bankroll.json replaces the default, and one that does not say when "
           "the stop-loss comes into effect gets thirty buy-ins")
        never = json.loads(json.dumps(DEFAULT_PLAN))
        never["stop_loss_in_effect_under"] = 0
        try:
            check_plan(never)
            ok(False, "a stop-loss never in effect is refused")
        except ValueError:
            ok(True, "a stop-loss never in effect is refused")
        ok(plan["start"] == 16400 and plan["goal"] == 300000
           and plan["milestones"] == [30000, 60000, 120000, 300000],
           "the default runs $164 to $3,000, with milestones at 300, 600, 1,200, 3,000")

        print("the old tracker, on a made-up file")
        old = tmp / "bankroll_tracker.db"
        oc = sqlite3.connect(old)
        oc.execute("CREATE TABLE daily (id INTEGER PRIMARY KEY, date TEXT, "
                   "end_bankroll REAL, hours REAL, hands INT, stake TEXT, notes TEXT)")
        oc.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        oc.execute("INSERT INTO settings VALUES ('title', '$40 to $3000')")
        oc.executemany("INSERT INTO daily (date, end_bankroll, hours, hands, stake, notes) "
                       "VALUES (?,?,?,?,?,?)",
                       [("2026-09-03", 180.5, 2, 300, "2NL", ""),
                        ("2026-09-01", 164, 1.5, 200, "2NL Deep", "start"),
                        ("2026-09-02", 171.25, 3, 450, "2NL", "good day"),
                        ("2026-09-05", 310, 2, 120, "0.05/0.10", "moved up"),
                        ("2026-09-04", 150, 1, 90, "who knows", "")])
        oc.commit()
        oc.close()
        con = sqlite3.connect(tmp / "import.db")
        n, others = import_old(con, old, "clubwpt", "USD")
        s = summary(con, plan)
        sess = sorted((r for r in s["rows"] if r["what"].startswith("session")),
                      key=lambda r: r["at"])
        ok(n == 5 and len(s["reconciliation"]) == 5 and len(sess) == 5,
           "five days: five balance checks and five sessions, none dropped")
        ok([r["cents"] for r in sess] == [None, 725, 925, -3050, 16000],
           "results are the change from the previous DAY, in date order, not file order")
        ok(all(r["gap"] == 16400 for r in s["reconciliation"]),
           "the gap is the $164 the ledger has no deposit for, the same every day")
        ok(any("who knows" in r["note"] for r in sess),
           "a stake it cannot read is kept in the note")
        ok(others == [("settings", 1)], "the other table is named, not silently ignored")
        try:
            import_old(con, old, "clubwpt", "USD")
            ok(False, "the same file twice is refused")
        except ValueError:
            ok(True, "the same file twice is refused")
        con.close()
        csvf = tmp / "days.csv"
        csvf.write_text("Date,Bankroll,Hours\n2026-09-01,164,1\n2026-09-02,170,2\n",
                        encoding="utf-8")
        con = sqlite3.connect(tmp / "csv.db")
        ok(import_csv(con, csvf, "clubwpt", "USD") == 2, "a Date,Bankroll CSV reads")
        con.close()

        print("nothing rebuilds the ledger away")
        work = tmp / "hands.db"
        real = Path(db_path).exists()
        if real:
            src = sqlite3.connect(db_path)
            dst = sqlite3.connect(work)
            src.backup(dst)
            src.close()
            dst.close()
        else:
            import importer
            importer.create(work)
        # The copy first, on a database of its own, because this machine's
        # may have its ledger already -- and then, rightly, nothing is made
        # and nothing copied. Asked of this machine's, it failed on every
        # database that had ever opened the Bankroll tab.
        fresh = tmp / "fresh.db"
        con = sqlite3.connect(fresh)
        con.execute("CREATE TABLE hands (hand_id TEXT)")
        con.commit()
        ensure(con)
        again = len(list(tmp.glob("fresh.db.bak-*")))
        ensure(con)
        con.close()
        ok(again == 1 and len(list(tmp.glob("fresh.db.bak-*"))) == 1,
           "the database was copied before the ledger's tables were made, and only then")
        con = sqlite3.connect(work)
        ensure(con)
        # And the copy's ledger emptied -- the copy's, never this machine's --
        # so that what follows counts the rows it adds and not yours.
        for t in TABLES:
            con.execute(f"DELETE FROM {t}")
        con.commit()
        sits = sittings(con) or []
        for site in {s["site"] for s in sits}:
            set_account(con, site, "USD")
        add_entry(con, "clubwpt", "deposit", "164", "2026-09-01", currency="USD")
        add_balance(con, "clubwpt", "170", "2026-09-02")
        before = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in TABLES}
        if sits:
            first = sits[0]
            add_session(con, first["site"], "12", first["started"][:10],
                        note="typed before the hands arrived")
            add_session(con, first["site"], "5", "1999-01-01")
        con.close()
        import importer
        importer.rebuild(work)
        con = sqlite3.connect(work)
        after = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in TABLES}
        same = all(after[t][:len(before[t])] == before[t] for t in TABLES)
        ok(same, "importer.rebuild leaves every ledger row as it was")
        ok(not any(name in dict(importer.CHAIN) for name in ("bankroll", "bankroll_view")),
           "the ledger is no stage of importer.CHAIN")
        drops = [p.name for p in HERE.glob("*.py")
                 if re.search(r"DROP\s+TABLE[^;\n]*bankroll_", p.read_text(encoding="utf-8"))
                 and p.name != "bankroll.py"]
        ok(not drops and "DROP" not in SCHEMA.upper(),
           "no module's schema drops a ledger table")
        ensure(con)
        ok(con.execute("SELECT COUNT(*) FROM bankroll_entries").fetchone()[0] >= 1,
           "making the tables again keeps what is in them")
        if sits:
            s = summary(con, plan)
            replaced = s["replaced"]
            ok(len(replaced) == 1 and replaced[0]["started"] == sits[0]["started"][:10],
               f"a typed session on a day {first['site']}'s hands cover is replaced, "
               "not doubled; one on a day they do not is counted")
            import query
            by_site = {}
            for x in sittings(con):
                by_site[x["site"]] = by_site.get(x["site"], 0) + int(round(x["net"] * 100))
            agree = True
            for site, total in sorted(by_site.items()):
                where = query.build(["--hero", "--site", site])[0]
                got = query.results_of(con, query.matching_seats(con, where))
                diff = abs(total - int(round(got["money"] * 100)))
                agree &= diff <= len(sits)
            whose = "this machine's" if real else "an empty"
            ok(agree, f"on {whose} database, each site's sittings add up to the "
                      f"results view's money ({len(sits)} sittings)")
            usd = s["currencies"].get("USD", {})
            ok(usd and usd["sites"][first["site"]]["typed"] == 500,
               "only the typed session the hands do not cover is counted")
            rates = [r for r in s["rates"] if not r["typed"]]
            biggest = max(rates, key=lambda r: r["hands"])
            argv = ["--hero", "--site", biggest["site"], "--stake", repr(biggest["bb"]),
                    "--straddle" if biggest["straddle"] else "--no-straddle"]
            alone = query.results_of(con, query.matching_seats(con, query.build(argv)[0]))
            ok(rates and all(r["hands"] > 0 and r["error"] is not None for r in rates)
               and alone["hands"] == biggest["hands"]
               and abs(alone["bb100"] - biggest["bb100"]) < 1e-6
               and abs(alone["error"] - biggest["error"]) < 1e-6,
               f"{len(rates)} win rates, one per site and stake, each with n and its error, "
               f"the largest ({biggest['site']} {biggest['stake']}, n={biggest['hands']}) "
               "the results view's to the digit")
            print("\n  ".join([""] + report(s)[:12]))

            # A database older than `hands.straddle` has it NULL on every
            # hand imported before it, until a reread. Such a sitting's
            # straddle is not known, and the derivation must say so rather
            # than stop: it once raised on exactly these databases, which
            # the corpus, built fresh, never is.
            known = sum(1 for x in sittings(con) if x["straddle"] is not None)
            con.execute("UPDATE hands SET straddle = NULL WHERE hand_id IN "
                        "(SELECT hand_id FROM hands WHERE hero_seat IS NOT NULL "
                        "AND fmt <> 'MTT' AND game = 'HOLDEM' LIMIT 3)")
            con.commit()
            con.close()
            import sessions
            try:
                sessions.build(work)
                con = sqlite3.connect(work)
                still = sum(1 for x in sittings(con) if x["straddle"] is not None)
                summary(con, plan)
                ok(still < known, f"hands from before the straddle column leave "
                                  f"{known - still} sittings' straddle not known, "
                                  "and nothing stops")
            except Exception as e:
                con = sqlite3.connect(work)
                ok(False, f"hands from before the straddle column stop the "
                          f"sittings: {type(e).__name__}: {e}")
        con.close()

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
