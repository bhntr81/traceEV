"""
Auto session entities: a sit-down, not a filter.

Hand2Note's other primary study surface after Reports. A session is
consecutive hero cash hands at one site, cut when the clock jumps or
the room changes. The window is duration, hand count, Won / Won bb,
the four-line graph, compact hands, mark/note, and export.

    python sessions.py                 the list (Won bb in the row)
    python sessions.py list
    python sessions.py --show ID       one session: summary, graph, hands
    python sessions.py --export-hands ID --out session.txt
    python sessions.py --graph ID
    python sessions.py --today
    python sessions.py --hours 4
    python sessions.py --since 2026-09-01 --until 2026-09-02
    python sessions.py --start-of-day 6 --tz acr=-5
    python sessions.py --check

Gap / site / hero, and they stay true:

- A new session starts when the gap between consecutive hero cash
  hands at the *same* site exceeds GAP_MINUTES (60). That is a
  sit-down, not a calendar day.
- Changing site always starts a new session, even one minute later.
  Two rooms are two clocks and two pools; averaging them into one
  sit-down describes neither.
- Changing hero identity always starts a new session. A named site
  (ACR, PokerStars) uses the screen name; a site without names
  (Ignition) is one hero stream per site. Using Ignition's
  `table:seat:segment` here would split a real sit-down every time
  the seat turned over.
- A table change does not. Multi-tabling is still one session.
- MTT is out. Tournament chips are not dollars, and Won / Won bb
  would lie.
- Opponent class is ignored. `--reg` / `--fish` and a future
  Reg-vs-Fish stats-rebuild exclusion do not touch sessions -- a
  sit-down is every hero cash hand in the window, including the
  ones against unknown seats. Sessions are not in `importer.CHAIN`.

Date footguns, which is why the clock lives here rather than in
`--since` as a raw string:

- **Today** is not the calendar date on the hand. It is the most
  recent start-of-day hour, in *your* clock.
- **Start-of-day hour** (default 0). A session that ends at 04:00
  belongs to yesterday if the day starts at 06:00, and to today if
  it starts at midnight. Getting this wrong empties the list.
- **Per-room HH timezone offset** (hours added to the history's
  `played_at` to reach your local clock). Rooms write their own
  local time and do not say which. An ACR hand stamped 02:00 ET is
  23:00 the previous evening in PT; without the offset, Today
  misses the night session or shows an empty filter.

`--since` / `--until` on `query.py` stay raw `played_at` comparisons
so existing reports do not move. `--today` / `--hours` / the
Sessions date bar go through this clock. An empty window prints
the start-of-day hour and the offsets rather than a blank list.

`--check` is fixtures in memory. It does not create `hands.db`.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import sites

DB = Path(__file__).parent / "hands.db"
PREFS = Path(__file__).parent / "sessions.json"

# A sit-down, not a coffee. Thirty minutes split a bathroom break on a
# slow table; two hours glued two evenings into one row. Sixty is the
# H2N-shaped default and the number `--check` pins.
GAP_MINUTES = 60

WHEN = "%Y-%m-%d %H:%M:%S"
DAY = "%Y-%m-%d"

# Sites without names: one hero stream. Spelling a site key here would
# be the `fmt='RING'` failure; the registry decides.
UNNAMED_HERO = "hero"


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

def parse_when(text):
    """`played_at` as a datetime, or None if the string is not one."""
    raw = (text or "").strip()
    if not raw:
        return None
    for fmt in (WHEN, DAY, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def fmt_when(dt):
    return dt.strftime(WHEN)


@dataclass
class Clock:
    """
    Today / last-N-hours / a date range, with the two footguns applied.

    `offsets` is hours to ADD to a room's `played_at` to reach local
    time. `start_of_day` is the local hour the day begins (0–23).
    `now` is injectable so `--check` does not depend on the wall clock.
    """
    start_of_day: int = 0
    offsets: dict = field(default_factory=dict)
    now: datetime = field(default_factory=datetime.now)

    def offset(self, site):
        try:
            return float(self.offsets.get(site, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def to_local(self, played_at, site):
        dt = parse_when(played_at)
        if dt is None:
            return None
        return dt + timedelta(hours=self.offset(site))

    def to_room(self, local_dt, site):
        return local_dt - timedelta(hours=self.offset(site))

    def today_local(self):
        """[start, end) of Today in local time."""
        now = self.now
        start = now.replace(hour=int(self.start_of_day) % 24,
                            minute=0, second=0, microsecond=0)
        if now < start:
            start -= timedelta(days=1)
        return start, start + timedelta(days=1)

    def hours_local(self, n):
        n = float(n)
        if n <= 0:
            raise ValueError("--hours needs a positive number")
        end = self.now
        return end - timedelta(hours=n), end

    def range_local(self, since, until):
        """
        A typed range in local time.

        A date with no clock is the start-of-day hour on that day;
        `until` as a date is the *next* start-of-day, so the named
        day is included. A value with a time is taken as written.
        """
        start = self._bound(since, end=False) if since else None
        end = self._bound(until, end=True) if until else None
        return start, end

    def _bound(self, text, end):
        raw = (text or "").strip()
        dt = parse_when(raw)
        if dt is None:
            raise ValueError(f"not a date: {text!r}")
        if len(raw) <= 10:
            dt = dt.replace(hour=int(self.start_of_day) % 24,
                            minute=0, second=0, microsecond=0)
            if end:
                dt += timedelta(days=1)
        return dt

    def window_sql(self, start_local, end_local, column="played_at"):
        """
        Per-site `played_at` bounds, because one range is wrong.

        Two rooms with different offsets cannot share a single
        `played_at >= X` -- that is the empty-filter footgun.
        """
        parts = []
        for key in sites.KEYS:
            lo = self.to_room(start_local, key) if start_local else None
            hi = self.to_room(end_local, key) if end_local else None
            bits = [f"site = '{key}'"]
            if lo is not None:
                bits.append(f"{column} >= '{fmt_when(lo)}'")
            if hi is not None:
                bits.append(f"{column} < '{fmt_when(hi)}'")
            parts.append("(" + " AND ".join(bits) + ")")
        return "(" + " OR ".join(parts) + ")" if parts else "1=1"

    def today_sql(self, column="played_at"):
        a, b = self.today_local()
        return self.window_sql(a, b, column)

    def hours_sql(self, n, column="played_at"):
        a, b = self.hours_local(n)
        return self.window_sql(a, b, column)

    def describe(self, start_local=None, end_local=None, kind=None):
        bits = []
        if kind == "today":
            a, b = self.today_local()
            bits.append(f"today {fmt_when(a)} .. {fmt_when(b)}")
        elif kind and kind.startswith("hours"):
            n = kind.split(":", 1)[-1]
            a, b = self.hours_local(n)
            bits.append(f"last {n} hours")
        elif start_local or end_local:
            bits.append(f"{fmt_when(start_local) if start_local else '…'} .. "
                        f"{fmt_when(end_local) if end_local else '…'}")
        bits.append(f"start-of-day {int(self.start_of_day) % 24:02d}:00")
        offs = ", ".join(f"{k}{self.offset(k):+g}h" for k in sites.KEYS)
        bits.append("room tz " + offs)
        return "; ".join(bits)

    def empty_warning(self, n, kind=None):
        """The sentence an empty Today/hours/range has to print."""
        if n:
            return None
        return (
            "nothing in this window. Today, last-N-hours and a date "
            "range use the start-of-day hour and each room's HH "
            "timezone offset -- a night session can land on yesterday, "
            "and a room that writes a different zone than you think in "
            "will empty the filter. "
            + self.describe(kind=kind)
        )


def load_clock(path=None, now=None, start_of_day=None, tz=None):
    """Prefs file, then CLI overrides. Missing file is defaults."""
    path = Path(path or PREFS)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            data = {}
    hour = data.get("start_of_day", 0)
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = 0
    raw = data.get("offsets") or {}
    offsets = {}
    for key in sites.KEYS:
        try:
            offsets[key] = float(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            offsets[key] = 0.0
    if start_of_day is not None:
        hour = int(start_of_day)
    if tz:
        offsets.update(parse_tz(tz))
    clock = Clock(start_of_day=hour % 24, offsets=offsets,
                  now=now or datetime.now())
    return clock


def save_clock(clock, path=None):
    path = Path(path or PREFS)
    path.write_text(json.dumps({
        "start_of_day": int(clock.start_of_day) % 24,
        "offsets": {k: clock.offset(k) for k in sites.KEYS},
    }, indent=2), encoding="utf-8")


def parse_tz(text):
    """`acr=-5,ignition=0` as a dict. Unknown sites are refused."""
    out = {}
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(
                f"timezone {part!r} -- site=hours (acr=-5)")
        key, raw = part.split("=", 1)
        key = key.strip()
        if key not in sites.KEYS:
            raise ValueError(
                f"no site {key!r}; the registry knows {', '.join(sites.KEYS)}")
        out[key] = float(raw)
    return out


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def hero_key(site, player):
    """
    Who this hero stream belongs to.

    Named sites: the screen name. Unnamed sites: one stream, not the
    seat identity `spots.identify` wrote -- that identity dies when
    the seat turns over, and a session that followed it would be a
    new row every time somebody sat down, which is not a sit-down.
    """
    try:
        named = sites.of(site).names
    except KeyError:
        named = False
    if named and player:
        return player
    return UNNAMED_HERO


@dataclass(frozen=True)
class Session:
    """One sit-down. `id` is the first hand -- unique, and rebuild-stable."""
    id: str
    hero: str
    site: str
    start: str
    end: str
    hand_ids: tuple
    won: float
    won_bb: float
    hands: int
    duration_min: float

    def argv(self):
        return ["--session", self.id]

    def duration_label(self):
        m = int(round(self.duration_min))
        if m < 60:
            return f"{m}m"
        return f"{m // 60}h {m % 60:02d}m"


def cluster(rows, gap_minutes=GAP_MINUTES):
    """
    Hero cash hands -> sessions.

    `rows` is an iterable of dicts (or mappings) with hand_id, site,
    player, played_at, net, net_bb, fmt. MTT is dropped here so a
    caller that forgot cannot put tournament chips in Won.
    """
    items = []
    for r in rows:
        if (r.get("fmt") or "") == "MTT":
            continue
        hid = r.get("hand_id")
        site = r.get("site") or ""
        when = r.get("played_at") or ""
        if not hid or not site or not when:
            continue
        items.append({
            "hand_id": hid,
            "site": site,
            "hero": hero_key(site, r.get("player")),
            "played_at": when,
            "net": float(r.get("net") or 0),
            "net_bb": float(r.get("net_bb") or 0),
        })
    items.sort(key=lambda x: (x["site"], x["hero"], x["played_at"],
                              x["hand_id"]))
    gap = timedelta(minutes=gap_minutes)
    out, cur = [], None

    def close():
        nonlocal cur
        if not cur:
            return
        start = parse_when(cur["start"])
        end = parse_when(cur["end"])
        mins = 0.0
        if start and end:
            mins = max(0.0, (end - start).total_seconds() / 60.0)
        out.append(Session(
            id=cur["ids"][0],
            hero=cur["hero"],
            site=cur["site"],
            start=cur["start"],
            end=cur["end"],
            hand_ids=tuple(cur["ids"]),
            won=cur["won"],
            won_bb=cur["won_bb"],
            hands=len(cur["ids"]),
            duration_min=mins,
        ))
        cur = None

    for row in items:
        dt = parse_when(row["played_at"])
        if cur is None:
            cur = _fresh(row)
            continue
        prev = parse_when(cur["end"])
        split = (row["site"] != cur["site"]
                 or row["hero"] != cur["hero"]
                 or (dt and prev and (dt - prev) > gap))
        if split:
            close()
            cur = _fresh(row)
            continue
        cur["ids"].append(row["hand_id"])
        cur["end"] = row["played_at"]
        cur["won"] += row["net"]
        cur["won_bb"] += row["net_bb"]
    close()
    return out


def _fresh(row):
    return {
        "hero": row["hero"], "site": row["site"],
        "start": row["played_at"], "end": row["played_at"],
        "ids": [row["hand_id"]],
        "won": row["net"], "won_bb": row["net_bb"],
    }


def from_spots(con, gap_minutes=GAP_MINUTES):
    """Cluster whatever `spots` currently holds. No class filter."""
    rows = con.execute(
        "SELECT hand_id, player, site, played_at, net, net_bb, fmt "
        "FROM spots WHERE is_hero = 1"
    ).fetchall()
    cols = ("hand_id", "player", "site", "played_at", "net", "net_bb", "fmt")
    return cluster([dict(zip(cols, r)) for r in rows], gap_minutes)


def stamp(con):
    """What `spots` looks like, so a rebuild is skipped when nothing moved."""
    n, latest = con.execute(
        "SELECT COUNT(*), MAX(played_at) FROM spots WHERE is_hero = 1 "
        "AND fmt <> 'MTT'"
    ).fetchone()
    return f"{n or 0}|{latest or ''}|{GAP_MINUTES}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  hero TEXT NOT NULL,
  site TEXT NOT NULL,
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  hands INT NOT NULL,
  duration_min REAL NOT NULL,
  won REAL NOT NULL,
  won_bb REAL NOT NULL);
CREATE TABLE IF NOT EXISTS session_hands (
  session_id TEXT NOT NULL,
  hand_id TEXT NOT NULL,
  PRIMARY KEY (session_id, hand_id));
"""


def ensure(con, gap_minutes=GAP_MINUTES):
    """
    Materialise the cluster so `--session ID` is a subquery.

    Not in `importer.CHAIN`. A stats rebuild that later grows a
    Reg-vs-Fish exclusion must not drop these rows -- sessions are
    every hero cash hand, and that is the point of keeping them out
    of the derivation list.
    """
    if "spots" not in {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        return []
    con.executescript(SCHEMA)
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    now = stamp(con)
    row = con.execute(
        "SELECT value FROM meta WHERE key='sessions_stamp'").fetchone()
    if row and row[0] == now:
        return load_cached(con)
    built = from_spots(con, gap_minutes)
    write_cached(con, built, now)
    return built


def load_cached(con):
    hands = {}
    for sid, hid in con.execute(
            "SELECT session_id, hand_id FROM session_hands"):
        hands.setdefault(sid, []).append(hid)
    out = []
    for r in con.execute(
            "SELECT id, hero, site, start, end, hands, duration_min, "
            "won, won_bb FROM sessions ORDER BY start, id"):
        out.append(Session(
            id=r[0], hero=r[1], site=r[2], start=r[3], end=r[4],
            hand_ids=tuple(hands.get(r[0], ())),
            hands=r[5], duration_min=r[6], won=r[7], won_bb=r[8],
        ))
    return out


def write_cached(con, sessions, now):
    con.execute("DELETE FROM session_hands")
    con.execute("DELETE FROM sessions")
    con.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
        [(s.id, s.hero, s.site, s.start, s.end, s.hands,
          s.duration_min, s.won, s.won_bb) for s in sessions])
    con.executemany(
        "INSERT INTO session_hands VALUES (?,?)",
        [(s.id, hid) for s in sessions for hid in s.hand_ids])
    con.execute(
        "INSERT OR REPLACE INTO meta VALUES ('sessions_stamp', ?)", (now,))
    con.commit()


def of(con, sid):
    """The session with this id, or None. Ensures the cache."""
    for s in ensure(con):
        if s.id == sid:
            return s
    return None


def session_sql(sid):
    """`--session ID` as a predicate over `decisions` / any hand_id column."""
    # The cache is a pair of tables in hands.db. build() has no
    # connection, so the SQL names the tables and the front end calls
    # ensure() before running it.
    lit = str(sid).replace("'", "''")
    return (f"hand_id IN (SELECT hand_id FROM session_hands "
            f"WHERE session_id = '{lit}')")


def in_window(session, clock, start_local, end_local):
    """True if the session *starts* inside the local window."""
    local = clock.to_local(session.start, session.site)
    if local is None:
        return True
    if start_local and local < start_local:
        return False
    if end_local and local >= end_local:
        return False
    return True


def listed(con, clock=None, kind=None, since=None, until=None, hours=None,
           gap_minutes=GAP_MINUTES):
    """Sessions in the date window, newest first."""
    clock = clock or load_clock()
    start = end = None
    if kind == "today":
        start, end = clock.today_local()
    elif hours is not None:
        start, end = clock.hours_local(hours)
        kind = f"hours:{hours}"
    elif since or until:
        start, end = clock.range_local(since, until)
    rows = ensure(con, gap_minutes)
    if start or end:
        rows = [s for s in rows if in_window(s, clock, start, end)]
    rows = sorted(rows, key=lambda s: (s.start, s.id), reverse=True)
    return rows, clock.empty_warning(len(rows), kind=kind), clock


LIST_COLUMNS = ("when", "site", "hero", "hands", "duration", "Won", "Won bb")


def as_row(session):
    return {
        "id": session.id,
        "when": session.start,
        "site": session.site,
        "hero": session.hero,
        "hands": session.hands,
        "duration": session.duration_label(),
        "duration_min": session.duration_min,
        "Won": session.won,
        "Won bb": session.won_bb,
        "start": session.start,
        "end": session.end,
        "argv": session.argv(),
    }


def series_of(con, session):
    """The four-line graph over this session's hero seats. Reuses query."""
    import query
    where = f"is_hero = 1 AND ({session_sql(session.id)})"
    pairs = query.matching_seats(con, where)
    if len(pairs) < 2:
        return None, "not enough hands to draw a line"
    query.select_into(con, pairs)
    hands, adj, skipped = query.adjusted(con, pairs)
    if len(hands) < 2:
        return None, "not enough hands to draw a line"
    series = {k: [] for k, _, _ in query.LINES}
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
    series["_note"] = (
        f"{len(hands):,} hands · {adj} all-in pots at equity"
        + (f" · {skipped} unadjusted" if skipped else ""))
    return series, None


def hands_of(con, session, limit=500):
    """Compact-ready rows for the session, newest first."""
    import compact
    import notes
    import query
    where = f"is_hero = 1 AND ({session_sql(session.id)})"
    rows = query.matching_hands(con, where, limit=limit)
    notes.attach(con)
    notes.decorate(con, rows)
    compact.attach(con, rows, fmt="text")
    return rows


def export_hands(con, session, dest):
    """
    The original hand-history text for this session, in play order.

    Recovered from `hands.source` through that site's `split_hands` /
    `parse_hand`, so the export is the file the room wrote and not a
    reconstruction. A hand whose source file is gone is counted and
    skipped; the return value says how many of each.
    """
    dest = Path(dest)
    written, missing = [], []
    by_id = {r[0]: r for r in con.execute(
        "SELECT hand_id, source, site FROM hands WHERE hand_id IN ({})"
        .format(",".join("?" * len(session.hand_ids))),
        session.hand_ids)}
    cache = {}
    for hid in session.hand_ids:
        row = by_id.get(hid)
        if not row:
            missing.append(hid)
            continue
        _hid, source, site_key = row
        if not source or not Path(source).exists():
            missing.append(hid)
            continue
        if source not in cache:
            cache[source] = _index_source(source, site_key)
        block = cache[source].get(hid)
        if not block:
            missing.append(hid)
            continue
        written.append(block.rstrip() + "\n\n")
    dest.write_text("".join(written), encoding="utf-8")
    return {"path": str(dest.resolve()), "wrote": len(written),
            "missing": len(missing), "missing_ids": missing}


def _index_source(path, site_key):
    """hand_id -> original block, via the site's parser. Never guess."""
    try:
        site = sites.of(site_key)
    except KeyError:
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for block in site.module.split_hands(text):
        parsed = site.module.parse_hand(block, path)
        if not parsed:
            continue
        hid = (parsed.get("hand") or {}).get("hand_id")
        if hid:
            out[hid] = block
    return out


def detail_of(con, sid, clock=None):
    """One payload the window and the page both draw."""
    clock = clock or load_clock()
    ensure(con)
    session = of(con, sid)
    if session is None:
        return {"error": f"no session {sid!r}"}
    series, why_graph = series_of(con, session)
    svg = None
    if series:
        import query
        svg = query.svg(series, _label(session), series.get("_note", ""),
                        dark=True)
    return {
        "session": as_row(session),
        "label": _label(session),
        "hands": hands_of(con, session),
        "series": series,
        "svg": svg,
        "why_graph": why_graph,
        "clock": clock.describe(),
    }


def list_of(con, clock=None, kind=None, since=None, until=None, hours=None):
    rows, warning, clock = listed(
        con, clock=clock, kind=kind, since=since, until=until, hours=hours)
    return {
        "rows": [as_row(s) for s in rows],
        "n": len(rows),
        "warning": warning,
        "clock": clock.describe(kind=kind),
        "columns": list(LIST_COLUMNS),
        "kind": kind,
    }


def _label(session):
    return (f"{session.site} {session.hero}  {session.start[:16]}  "
            f"{session.hands} hands  {session.won_bb:+.1f} bb")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _opt(argv, name, default=None):
    if name not in argv:
        return default
    i = argv.index(name) + 1
    if i >= len(argv) or str(argv[i]).startswith("--"):
        raise SystemExit(f"{name} needs a value")
    return argv[i]


def _clock_from(argv, now=None):
    return load_clock(now=now,
                      start_of_day=_opt(argv, "--start-of-day"),
                      tz=_opt(argv, "--tz"))


def show_list(con, argv):
    kind = None
    hours = None
    if "--today" in argv:
        kind = "today"
    if "--hours" in argv:
        hours = _opt(argv, "--hours")
    got = list_of(con, clock=_clock_from(argv), kind=kind,
                  since=_opt(argv, "--since"), until=_opt(argv, "--until"),
                  hours=hours)
    print(f"sessions  {got['n']}")
    print(got["clock"])
    print("=" * 72)
    print(f"{'when':<17} {'site':<10} {'hero':<14} {'hands':>6} "
          f"{'dur':>7} {'Won':>10} {'Won bb':>9}")
    for r in got["rows"]:
        print(f"{r['when'][:16]:<17} {r['site']:<10} {str(r['hero'])[:14]:<14} "
              f"{r['hands']:>6} {r['duration']:>7} "
              f"{r['Won']:>+10.2f} {r['Won bb']:>+9.1f}")
    if got["warning"]:
        print()
        print(got["warning"])
    if not got["rows"] and not got["warning"]:
        print("no hero cash sessions in this database")
    return got


def show_one(con, sid):
    ensure(con)
    session = of(con, sid)
    if session is None:
        print(f"no session {sid!r}")
        return 1
    print(_label(session))
    print("=" * len(_label(session)))
    print(f"  hero        {session.hero}")
    print(f"  site        {session.site}")
    print(f"  start       {session.start}")
    print(f"  end         {session.end}")
    print(f"  duration    {session.duration_label()}")
    print(f"  hands       {session.hands}")
    print(f"  Won         {session.won:+.2f}")
    print(f"  Won bb      {session.won_bb:+.1f}")
    print()
    print("  a session is hero cash hands at one site, split on a "
          f"{GAP_MINUTES}-minute gap or a site/hero change.")
    print("  MTT is out. Opponent class is ignored.")
    print()
    rows = hands_of(con, session, limit=80)
    for r in rows[:40]:
        line = r.get("compact") or r.get("combo") or ""
        print(f"  {(r.get('when') or '')[:16]}  "
              f"{r.get('combo') or '–':>4}  "
              f"{(r.get('net') or 0):>+6.1f}  {line}")
    if len(rows) > 40:
        print(f"  … {len(rows) - 40} more")
    return 0


def show_graph(con, sid, out_path="session-graph.html"):
    import query
    ensure(con)
    session = of(con, sid)
    if session is None:
        print(f"no session {sid!r}")
        return 1
    where = f"is_hero = 1 AND ({session_sql(session.id)})"
    query.show_graph(con, where, _label(session), out_path)
    return 0


def show_export(con, sid, dest):
    ensure(con)
    session = of(con, sid)
    if session is None:
        print(f"no session {sid!r}")
        return 1
    got = export_hands(con, session, dest)
    print(f"wrote {got['wrote']} hands to {got['path']}")
    if got["missing"]:
        print(f"  {got['missing']} skipped -- source file gone or unreadable")
        print("  export needs the original HH files in hands.source; "
              "a rebuilt database without them cannot reconstruct the text")
    return 0


def usage():
    print(__doc__)


# ---------------------------------------------------------------------------
# --check  (no corpus)
# ---------------------------------------------------------------------------

def _mem():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE spots ("
        "hand_id TEXT, player TEXT, site TEXT, played_at TEXT, "
        "net REAL, net_bb REAL, fmt TEXT, is_hero INT)")
    con.execute(
        "CREATE TABLE hands ("
        "hand_id TEXT PRIMARY KEY, source TEXT, site TEXT)")
    return con


def _hand(hid, site, when, net=1.0, net_bb=10.0, player="Alice",
          fmt="RING", is_hero=1):
    return (hid, player, site, when, net, net_bb, fmt, is_hero)


def check():
    """
    Gap, site, hero, clock, money, export, and the SQL `--session` emits.

    No `hands.db`. The failures these catch are silent ones: a site
    change glued into one session, Ignition split on seat identity,
    Today empty because start-of-day was ignored, Won summed as
    `won` instead of profit.
    """
    fails = []

    def trip(name, ok):
        if not ok:
            fails.append(name)
        return ok

    # Gap: 61 minutes splits; 59 does not.
    rows = [
        {"hand_id": "a1", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:00:00", "net": 1, "net_bb": 10,
         "fmt": "RING"},
        {"hand_id": "a2", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:59:00", "net": 2, "net_bb": 20,
         "fmt": "RING"},
        {"hand_id": "a3", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 14:01:00", "net": 3, "net_bb": 30,
         "fmt": "RING"},
    ]
    got = cluster(rows)
    trip("59 minutes stay one session",
         len(got) == 2 and got[0].hands == 2 and got[1].hands == 1)
    trip("id is the first hand", got[0].id == "a1" and got[1].id == "a3")
    trip("Won is profit dollars, not collected chips",
         abs(got[0].won - 3) < 1e-9)
    trip("Won bb is net_bb", abs(got[0].won_bb - 30) < 1e-9)
    trip("duration is last minus first",
         abs(got[0].duration_min - 59) < 1e-6)

    # Site boundary: one minute later on another room is a new session.
    rows = [
        {"hand_id": "a1", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:00:00", "net": 1, "net_bb": 10,
         "fmt": "RING"},
        {"hand_id": "i1", "site": "ignition", "player": "table:1:0",
         "played_at": "2026-09-01 12:01:00", "net": 1, "net_bb": 10,
         "fmt": "RING"},
    ]
    got = cluster(rows)
    trip("site change splits even at one minute",
         len(got) == 2 and {s.site for s in got} == {"acr", "ignition"})

    # Ignition seat identity must not split a sit-down.
    rows = [
        {"hand_id": "i1", "site": "ignition", "player": "t:1:0",
         "played_at": "2026-09-01 12:00:00", "net": 1, "net_bb": 5,
         "fmt": "RING"},
        {"hand_id": "i2", "site": "ignition", "player": "t:2:1",
         "played_at": "2026-09-01 12:10:00", "net": 1, "net_bb": 5,
         "fmt": "RING"},
    ]
    got = cluster(rows)
    trip("Ignition hero is one stream, not the seat",
         len(got) == 1 and got[0].hero == UNNAMED_HERO and got[0].hands == 2)

    # Named-site hero change splits.
    rows = [
        {"hand_id": "a1", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:00:00", "net": 1, "net_bb": 5,
         "fmt": "RING"},
        {"hand_id": "a2", "site": "acr", "player": "Bob",
         "played_at": "2026-09-01 12:01:00", "net": 1, "net_bb": 5,
         "fmt": "RING"},
    ]
    got = cluster(rows)
    trip("named hero change splits",
         len(got) == 2 and {s.hero for s in got} == {"Alice", "Bob"})

    # MTT out; a fish villain does not drop the hand (no class column).
    rows = [
        {"hand_id": "m1", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:00:00", "net": 50, "net_bb": 50,
         "fmt": "MTT"},
        {"hand_id": "c1", "site": "acr", "player": "Alice",
         "played_at": "2026-09-01 12:01:00", "net": 1, "net_bb": 10,
         "fmt": "RING"},
    ]
    got = cluster(rows)
    trip("MTT is not a session hand",
         len(got) == 1 and got[0].hands == 1 and got[0].id == "c1")

    # Clock: start-of-day and offset.
    now = datetime(2026, 9, 2, 3, 0, 0)
    clock = Clock(start_of_day=6, offsets={"acr": -3, "ignition": 0,
                                           "pokerstars": 0}, now=now)
    start, end = clock.today_local()
    trip("at 03:00 with start-of-day 6, Today began yesterday 06:00",
         start == datetime(2026, 9, 1, 6, 0, 0)
         and end == datetime(2026, 9, 2, 6, 0, 0))
    # Room writes 02:00; offset -3 means local is 23:00 the previous day.
    # Today local is [Sep 1 06:00, Sep 2 06:00). Local 23:00 Sep 1 is in.
    # Room time for the window on ACR: [Sep 1 09:00, Sep 2 09:00).
    sql = clock.today_sql()
    trip("Today SQL is per-site, not one played_at range",
         "site = 'acr'" in sql and "site = 'ignition'" in sql)
    # 02:00 room + (-3h) = 23:00 Sep 1 local, inside Today.
    trip("offset pulls a late-night HH stamp into Today",
         in_window(Session("x", "Alice", "acr", "2026-09-02 02:00:00",
                           "2026-09-02 02:00:00", ("x",), 0, 0, 1, 0),
                   clock, start, end))
    # Same stamp on ignition (offset 0) is 02:00 Sep 2 local -- before
    # 06:00, so it is still Today (Today started Sep 1 06:00).
    # A stamp of 07:00 Sep 2 local is after Today ends.
    trip("a stamp after today's end is out",
         not in_window(Session("y", "Alice", "ignition",
                               "2026-09-02 07:00:00", "2026-09-02 07:00:00",
                               ("y",), 0, 0, 1, 0),
                       clock, start, end))
    warn = clock.empty_warning(0, kind="today")
    trip("empty window names start-of-day and offsets",
         warn and "start-of-day 06:00" in warn and "acr-3h" in warn)

    hours_sql = Clock(now=now).hours_sql(4)
    trip("--hours is a per-site played_at window",
         "played_at >=" in hours_sql and "site = 'acr'" in hours_sql)

    # Date-only range uses start-of-day.
    a, b = clock.range_local("2026-09-01", "2026-09-01")
    trip("a date-only until includes that day up to the next start-of-day",
         a == datetime(2026, 9, 1, 6, 0, 0)
         and b == datetime(2026, 9, 2, 6, 0, 0))

    # Cache + --session SQL + export from a real source file.
    con = _mem()
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?,?,?)",
        [_hand("h1", "acr", "2026-09-01 12:00:00", 1, 10),
         _hand("h2", "acr", "2026-09-01 12:20:00", -0.5, -5),
         _hand("h3", "acr", "2026-09-01 15:00:00", 2, 20),
         _hand("m1", "acr", "2026-09-01 12:10:00", 9, 90, fmt="MTT"),
         _hand("p1", "acr", "2026-09-01 12:05:00", 0, 0, is_hero=0)])
    built = ensure(con)
    trip("ensure clusters hero cash only",
         len(built) == 2 and built[0].hands == 2 and built[1].hands == 1)
    trip("--session SQL names the cache table",
         "session_hands" in session_sql(built[0].id)
         and built[0].id in session_sql(built[0].id))
    n = con.execute(
        f"SELECT COUNT(*) FROM spots WHERE is_hero=1 AND "
        f"({session_sql(built[0].id)})").fetchone()[0]
    trip("--session selects that sit-down's hands", n == 2)
    # Second ensure is a no-op on the same stamp.
    again = ensure(con)
    trip("ensure is idempotent on an unchanged spots",
         [s.id for s in again] == [s.id for s in built])

    # Export: a tiny ACR-shaped block on disk, recovered by parse_hand.
    import tempfile
    import acr
    scratch = Path(tempfile.mkdtemp())
    # A block the ACR parser will accept is more than we need for the
    # index; if parse_hand returns None the export must count missing
    # rather than invent text. The fixture is a real header + seats so
    # a future parser tightening cannot silently pass.
    # ACR verbs have no colon after the name (`Alice posts`, not
    # `Alice:`). A colon-shaped fixture used to make parse_hand
    # return None, and then the export check invented a pass.
    block = (
        "Hand #999001 - Holdem (No Limit) - $0.05/$0.10 "
        "- 2026/09/01 12:00:00 UTC\n"
        "Check 6-max Seat #1 is the button\n"
        "Seat 1: Alice ($10.00)\n"
        "Seat 2: Bob ($10.00)\n"
        "Alice posts the small blind $0.05\n"
        "Bob posts the big blind $0.10\n"
        "*** HOLE CARDS ***\n"
        "Dealt to Alice [Ah Kh]\n"
        "Alice raises $0.20 to $0.30\n"
        "Bob folds\n"
        "Uncalled bet ($0.20) returned to Alice\n"
        "Alice collected $0.20 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot $0.20 | Rake $0.00 | JP Fee $0.00\n"
    )
    src = scratch / "hh.txt"
    src.write_text(block, encoding="utf-8")
    parsed = acr.parse_hand(block, str(src))
    trip("export fixture is a real ACR hand", parsed is not None)
    if parsed is not None:
        hid = parsed["hand"]["hand_id"]
        con.execute("INSERT INTO hands VALUES (?,?,?)",
                    (hid, str(src), "acr"))
        fake = Session(hid, "Alice", "acr", "2026-09-01 12:00:00",
                       "2026-09-01 12:00:00", (hid,), 0.2, 2, 1, 0)
        dest = scratch / "out.txt"
        got = export_hands(con, fake, dest)
        trip("export writes the original block",
             got["wrote"] == 1 and "Hand #999001" in dest.read_text())
    gone = Session("nope", "Alice", "acr", "2026-09-01 12:00:00",
                   "2026-09-01 12:00:00", ("nope",), 0, 0, 1, 0)
    miss = export_hands(con, gone, scratch / "empty.txt")
    trip("export counts a missing source instead of inventing one",
         miss["wrote"] == 0 and miss["missing"] == 1)

    # Prefs + tz parse.
    try:
        parse_tz("acr=-5,ignition=0")
        tz_ok = True
    except ValueError:
        tz_ok = False
    trip("tz string parses", tz_ok)
    try:
        parse_tz("notasite=1")
        trip("unknown site in --tz is refused", False)
    except ValueError:
        trip("unknown site in --tz is refused", True)
    pref = scratch / "sessions.json"
    save_clock(Clock(start_of_day=6, offsets={"acr": -5}), pref)
    loaded = load_clock(pref, now=now)
    trip("prefs remember start-of-day and offset",
         loaded.start_of_day == 6 and loaded.offset("acr") == -5)

    # argv on the entity is the Open-in-Reports chip.
    trip("session.argv is --session ID",
         built[0].argv() == ["--session", built[0].id])

    print(f"session cluster / clock / export  "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return 0
    if "--check" in argv or (argv and argv[0] == "check"):
        return 0 if check() else 1

    rest = list(argv)
    verb = rest[0] if rest and not rest[0].startswith("--") else None
    if verb:
        rest = rest[1:]

    def sid():
        got = _opt(rest, "--show") or _opt(rest, "--export-hands") \
            or _opt(rest, "--graph")
        if got:
            return got
        if rest and not rest[0].startswith("--"):
            return rest[0]
        raise SystemExit("needs a session id (the first hand of the sit-down)")

    if verb in (None, "list") or "--list" in rest or (
            verb is None and not any(a in rest for a in
                                     ("--show", "--export-hands", "--graph"))):
        if verb in ("show", "export-hands", "graph"):
            pass
        elif "--show" not in rest and "--export-hands" not in rest \
                and "--graph" not in rest and verb != "show":
            if not Path(DB).exists():
                raise SystemExit(f"no database at {DB} -- load some hands first")
            con = sqlite3.connect(DB)
            try:
                show_list(con, rest)
            finally:
                con.close()
            return 0

    if verb == "show" or "--show" in rest:
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        try:
            return show_one(con, sid())
        finally:
            con.close()
    if verb == "export-hands" or "--export-hands" in rest:
        dest = _opt(rest, "--out", "session-hands.txt")
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        try:
            return show_export(con, sid(), dest)
        finally:
            con.close()
    if verb == "graph" or "--graph" in rest:
        dest = _opt(rest, "--out", "session-graph.html")
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        try:
            return show_graph(con, sid(), dest)
        finally:
            con.close()
    if verb == "list" or "--list" in rest or verb is None:
        if not Path(DB).exists():
            raise SystemExit(f"no database at {DB} -- load some hands first")
        con = sqlite3.connect(DB)
        try:
            show_list(con, rest)
        finally:
            con.close()
        return 0
    raise SystemExit(f"unknown command {verb!r} -- list, show, "
                     "export-hands, graph")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
