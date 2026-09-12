"""
Work out what a hand history is before loading it, and then load it.

The site used to be decided by whoever ran the command -- `python acr.py`
meant these are ACR hands. That went wrong exactly once and expensively:
eight thousand hands were loaded, named, analysed and reported on as
CoinPoker, because a CoinPoker client happened to be installed on the
machine. The site was inferred from the computer instead of from the file,
and the file had said so in plain text from the first line.

So nothing here asks. Every file is sniffed -- each site's parser says
whether a line is one of its headers -- and a file that cannot be
identified is counted and skipped rather than guessed at. Which sites exist
is `sites.py`; this file loads whatever that registry knows, and a new site
needs nothing changed here.

    python importer.py --scan               where hand histories are
    python importer.py --refresh            load anything new from those places
    python importer.py <folder-or-file>...  load them, whatever site they are
    python importer.py --merge other.db     take the hands from another database
    python importer.py --check              PASS or FAIL
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import sites

DB = Path(__file__).parent / "hands.db"

# The three raw tables every site's parser fills, and every column any site
# has needed. One schema for all of them: it was Ignition's, with the
# columns ACR added by ALTER, and a parser that wrote its own INSERT stopped
# being able to insert anything the day the other site added a column.
SCHEMA = """
CREATE TABLE IF NOT EXISTS hands (
  hand_id TEXT PRIMARY KEY, played_at TEXT, table_id TEXT, game TEXT,
  fmt TEXT, sb REAL, bb REAL, n_players INT, board TEXT, pot REAL,
  hero_seat INT, standard INT, source TEXT, site TEXT, rake REAL,
  max_seats INT, jp_fee REAL);
CREATE TABLE IF NOT EXISTS seats (
  hand_id TEXT, seat INT, label TEXT, position TEXT, stack REAL,
  cards TEXT, is_hero INT, won REAL, posted REAL, invested REAL,
  PRIMARY KEY (hand_id, seat));
CREATE TABLE IF NOT EXISTS actions (
  hand_id TEXT, street TEXT, n INT, position TEXT, seat INT,
  action TEXT, amount REAL, total REAL, allin INT);
CREATE INDEX IF NOT EXISTS actions_hand ON actions(hand_id);
CREATE INDEX IF NOT EXISTS actions_spot ON actions(street, position, action);
CREATE INDEX IF NOT EXISTS hands_fmt ON hands(fmt, bb);
"""

HAND_COLUMNS = ("hand_id", "played_at", "table_id", "game", "fmt", "sb", "bb",
                "n_players", "board", "pot", "hero_seat", "standard", "source",
                "site", "rake", "max_seats", "jp_fee")


def migrate(con):
    """
    Bring a database written before a column existed up to the schema.

    `merge` takes hands from another database of the same shape, and that
    shape is whatever version of this program the other machine ran. Hand
    ids are never rewritten: Ignition's are bare, ACR's carry "cp-", and
    that is enough to keep the sites from colliding.
    """
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(hands)")}
    for col, kind in (("site", "TEXT"), ("rake", "REAL"),
                      ("max_seats", "INT"), ("jp_fee", "REAL")):
        if col not in cols:
            con.execute(f"ALTER TABLE hands ADD COLUMN {col} {kind}")
    if "site" not in cols:
        # Before a second site existed, every hand was Ignition's.
        con.execute("UPDATE hands SET site='ignition' WHERE site IS NULL")
    acols = {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    if "allin" not in acols:
        con.execute("ALTER TABLE actions ADD COLUMN allin INT")
    con.commit()


def sniff(path):
    """Which site wrote this file, by reading it rather than by asking."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            # The header is at the top, but an export can begin with a blank
            # line or a stray byte-order mark, so a few lines are allowed.
            for _ in range(6):
                line = fh.readline()
                if not line:
                    break
                line = line.lstrip("﻿").strip()
                if not line:
                    continue
                for site in sites.SITES:
                    if site.module.HEADER(line):
                        return site.key
                return None
    except OSError:
        return None
    return None


def files_under(paths):
    """Every .txt beneath the given files and folders, without duplicates."""
    seen, out = set(), []
    for p in paths:
        p = Path(os.path.expandvars(str(p)))
        found = sorted(p.rglob("*.txt")) if p.is_dir() else [p]
        for f in found:
            key = str(f).lower()
            if key not in seen and f.is_file():
                seen.add(key)
                out.append(f)
    return out


def survey(paths):
    """What is in these places, by site, without loading anything."""
    counts = {key: [] for key in sites.KEYS}
    counts["unknown"] = []
    for f in files_under(paths):
        counts[sniff(f) or "unknown"].append(f)
    return counts


def recognised(survey):
    """The files a survey found a parser for, every site together."""
    return [f for key in sites.KEYS for f in survey[key]]


def describe(place):
    """One line for a place `scan` found: counts per site, then the path."""
    counts = "   ".join(f"{place['sites'][key]:>5} {key}" for key in sites.KEYS)
    return f"{counts}   {place['path']}"


def scan():
    """The places on this machine that actually hold hand histories."""
    found = []
    for raw in places():
        place = Path(os.path.expandvars(raw))
        if not place.exists():
            continue
        got = survey([place])
        if recognised(got):
            found.append({"path": place,
                          "sites": {key: len(got[key]) for key in sites.KEYS},
                          "unknown": len(got["unknown"])})
    return found


# Where hand histories live when nobody has moved them: each site's own
# folders from the registry, then the places a person drops an export.
# Checked in order and reported with what is actually in them, because a
# folder that exists and holds nothing is not a place to import from.
DROPPED = (r"%USERPROFILE%\Downloads", r"%USERPROFILE%\Desktop")


def places():
    return tuple(p for s in sites.SITES for p in s.places) + DROPPED


# How far the loader has read, kept in the database beside the hands it
# came from. Its own table, created on demand, because it is importer's
# bookkeeping and not part of the schema the parsers share -- and because
# `decisions.build` drops its table and this must survive that.
META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"


def high_water(db_path=DB, seen=None):
    """
    The newest file modification time the loader has already read, or None.

    Passing `seen` records a new one, keeping whichever is later. This is
    the thing `anything_new` compares against, and it exists because the
    obvious comparison is wrong: the newest hand's `played_at` is when it
    was PLAYED, and the two hundred and sixty-five hands the broken loader
    had been unable to read were all older than hands already held. Against
    `played_at` those files stayed "new" after they had been loaded, so the
    window would have offered to import them on every launch for ever --
    and a notice that says the same thing every launch is one nobody reads.
    """
    con = sqlite3.connect(db_path)
    con.execute(META)
    row = con.execute("SELECT value FROM meta WHERE key='high_water'"
                      ).fetchone()
    was = float(row[0]) if row else None
    if seen is not None and (was is None or seen > was):
        con.execute("INSERT OR REPLACE INTO meta VALUES ('high_water', ?)",
                    (repr(float(seen)),))
        con.commit()
        was = float(seen)
    con.close()
    return was


def anything_new(db_path=DB):
    """
    How many hand-history files have been written since the loader last read.

    A heuristic, and cheaply so on purpose: knowing for certain means
    parsing all three hundred and seventy files on this machine, which is
    not something to do while a window is trying to open. A file touched
    since the last load MIGHT hold hands that are not in the database; one
    touched before it cannot. So this over-reports and never under-reports,
    which is the right way round for something whose only consequence is
    offering to import.

    Before anything has been loaded there is no mark to compare against, so
    every file counts -- which is correct, since none of them have been read.
    """
    mark = high_water(db_path)
    fresh = 0
    for place in scan():
        got = survey([place["path"]])
        for f in recognised(got):
            if mark is None or f.stat().st_mtime > mark:
                fresh += 1
    return fresh


def refresh(db_path=DB, progress=None):
    """
    Load whatever is new in the places this machine keeps hand histories.

    The whole of "auto-import", and it is short because both halves already
    existed: `scan` finds the folders and `load` already skips a hand that
    is in the database. So this is safe to run at any time and costs a pass
    over the files when there is nothing to do -- and, crucially, only
    rebuilds when a hand was actually added. The derivation is
    three quarters of a minute; running it to discover nothing changed is
    how a refresh button becomes one nobody presses.
    """
    found = scan()
    if not found:
        return {"added": 0, "known": 0, "files": 0, "unknown": 0,
                "by_site": {}, "places": 0}
    got = load([p["path"] for p in found], db_path, progress)
    got["places"] = len(found)
    if got["added"]:
        rebuild(db_path, progress)
    return got


def load(paths, db_path=DB, progress=None):
    """
    Load every hand history under these paths, each by its own site's parser.

    Files are grouped by site first so that a folder holding both -- which is
    what a Downloads folder is -- loads correctly rather than by whichever
    parser was asked for. Safe to re-run: hands are keyed by the site's own
    id, so re-exported files, or exports overlapping ones already loaded,
    contribute only what is new. That is the intended way to use it as you
    keep playing.

    This is the only place rows are written. Each parser used to carry its
    own copy of this loop with its own INSERT, and the two had drifted: one
    wrote fewer columns than the other, and both had the same bug in the
    same line. A parser returns the dict; the writing is here.
    """
    got = survey(paths)
    result = {"added": 0, "known": 0, "files": 0, "unknown": len(got["unknown"]),
              "by_site": {}}
    # Recorded here rather than in `refresh`, so that every road into the
    # loader marks how far it has read -- Import a folder and Find hands on
    # this computer read the same files and would otherwise leave the
    # window still offering to fetch them.
    read = recognised(got)
    if read:
        high_water(db_path, max(f.stat().st_mtime for f in read))

    con = sqlite3.connect(db_path)
    migrate(con)
    known = {r[0] for r in con.execute("SELECT hand_id FROM hands")}
    for site in sites.SITES:
        files = got[site.key]
        if not files:
            continue
        if progress:
            progress(f"{site.key}: {len(files)} files")
        added = skipped = n_files = 0
        for f in files:
            n_files += 1
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for block in site.module.split_hands(text):
                parsed = site.module.parse_hand(block, source=f.name)
                if not parsed:
                    continue
                h = dict(parsed["hand"], site=site.key)
                # parse_hand sees the basename -- Ignition reads stakes
                # from it. The row stores the path we loaded from, or
                # export can only find the file when cwd happens to be
                # that folder, and a session export writes nothing.
                h["source"] = str(f.resolve())
                if h["hand_id"] in known:
                    skipped += 1
                    continue
                known.add(h["hand_id"])
                con.execute(
                    f"INSERT INTO hands ({', '.join(HAND_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(HAND_COLUMNS))})",
                    [h.get(c) for c in HAND_COLUMNS])
                con.executemany(
                    "INSERT OR REPLACE INTO seats (hand_id, seat, label, "
                    "position, stack, cards, is_hero, won, posted, invested) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(h["hand_id"], st["seat"], st["label"], st["position"],
                      st["stack"], st["cards"], int(st["is_hero"]), st["won"],
                      st["posted"], st["invested"]) for st in parsed["seats"]])
                con.executemany(
                    "INSERT INTO actions (hand_id, street, n, position, seat, "
                    "action, amount, total, allin) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(h["hand_id"], a["street"], a["n"], a["position"],
                      a["seat"], a["action"], a["amount"], a["total"],
                      a.get("allin")) for a in parsed["actions"]])
                added += 1
            if n_files % 25 == 0:
                con.commit()
                print(f"  ...{n_files} files, {added} hands", flush=True)
        con.commit()
        result["by_site"][site.key] = added
        result["added"] += added
        result["known"] += skipped
        result["files"] += n_files
    con.close()
    return result


def merge(other, db_path=DB, progress=None):
    """
    Take the hands from another database of the same shape.

    Only hands this one has never seen are copied, keyed by the site's own
    hand id, so merging the same file twice adds nothing the second time.
    The derived tables are not copied -- they are rebuilt from the raw rows,
    because a `spots` row from another database was derived by whatever
    version of the derivation that machine was running.
    """
    other = Path(other)
    if not other.exists():
        raise SystemExit(f"no database at {other}")
    con = sqlite3.connect(db_path)
    migrate(con)
    con.execute("ATTACH DATABASE ? AS src", (str(other),))
    have = {r[0] for r in con.execute("SELECT hand_id FROM hands")}
    incoming = [r[0] for r in con.execute("SELECT hand_id FROM src.hands")]
    new = [h for h in incoming if h not in have]
    if progress:
        progress(f"{len(new)} new of {len(incoming)}")
    added = 0
    for i in range(0, len(new), 500):
        chunk = new[i:i + 500]
        marks = ",".join("?" * len(chunk))
        cols = [r[1] for r in con.execute("PRAGMA table_info(hands)")]
        src_cols = {r[1] for r in con.execute("PRAGMA table_info(src.hands)")}
        shared = [c for c in cols if c in src_cols]
        con.execute(
            f"INSERT INTO hands ({','.join(shared)}) "
            f"SELECT {','.join(shared)} FROM src.hands "
            f"WHERE hand_id IN ({marks})", chunk)
        for table in ("seats", "actions"):
            tcols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            tsrc = {r[1] for r in con.execute(f"PRAGMA table_info(src.{table})")}
            sh = [c for c in tcols if c in tsrc]
            con.execute(
                f"INSERT INTO {table} ({','.join(sh)}) "
                f"SELECT {','.join(sh)} FROM src.{table} "
                f"WHERE hand_id IN ({marks})", chunk)
        added += len(chunk)
    con.commit()
    con.execute("DETACH DATABASE src")
    con.close()
    return {"added": added, "known": len(incoming) - len(new)}


# The derived tables, in the order they depend on each other. A list rather
# than five calls in a row, because the failure it exists to prevent is a
# stage being left out of it -- and that failure had already happened.
#
# `decisions.build` begins by DROPPING the table, and `lines`, `strength`
# and `players` each ALTER their own columns onto it afterwards. So a
# rebuild that stops after `decisions` does not leave the later columns
# stale, it leaves them GONE. This function ran spots and decisions and
# nothing else, so importing hands through the window deleted twenty-two
# columns -- every line string, every named hand, every player class -- and
# every filter that read one raised "no such column" until somebody thought
# to run the other three modules by hand.
#
# The indexes go last and are part of the chain for the same reason. They
# are not in `decisions.SCHEMA`, so a rebuild drops them, and thirteen
# filters go back to reading all ninety thousand rows without anything
# saying so. Built after the rows are written rather than during: keeping
# six B-trees up to date while rewriting every row took `lines` from eight
# seconds to forty-two.
CHAIN = (
    ("spots", "one row per player per hand"),
    ("decisions", "one row per decision -- and this DROPS the table"),
    ("lines", "the betting written out, onto decisions"),
    ("strength", "what each hand is, onto decisions"),
    ("players", "who each player is, onto decisions"),
)


def rebuild(db_path=DB, progress=None):
    """
    Redo every derived table, in the order they depend on each other.

    This is what makes new hands visible: loading writes `hands`, `seats`
    and `actions`, and every question the program answers is asked of the
    tables derived from those. It takes about three quarters of a minute at
    twelve thousand hands, and all of it has to run -- see `CHAIN`.
    """
    import importlib

    import decisions
    for name, _what in CHAIN:
        if progress:
            progress(f"deriving {name}...")
        importlib.import_module(name).build(db_path)
    if progress:
        progress("indexing...")
    decisions.index(db_path)


def check(db_path=DB):
    """
    Sniffing is right on files whose site is already known.

    Every hand in the database came from a file, and the database records
    which site each came from -- so the detector can be held against the
    answer rather than against an opinion. A detector that is merely
    plausible is how eight thousand hands got loaded under the wrong name.
    """
    fails = []
    con = sqlite3.connect(db_path)
    known = con.execute(
        "SELECT source, site, COUNT(*) FROM hands WHERE source IS NOT NULL "
        "GROUP BY source, site").fetchall()
    con.close()

    # Find each recorded source file wherever it now lives, and re-sniff it.
    index = {}
    for place in (Path(os.path.expandvars(p)) for p in places()):
        if place.exists():
            for f in place.rglob("*.txt"):
                index.setdefault(f.name, f)

    tested = wrong = 0
    for source, site, _n in known:
        f = index.get(source) or index.get(Path(source).name)
        if f is None and source and Path(source).exists():
            f = Path(source)
        if f is None:
            continue
        tested += 1
        if sniff(f) != site:
            wrong += 1
            if wrong <= 3:
                print(f"    {source[:60]}: sniffed {sniff(f)}, recorded {site}")
    print(f"site detected correctly       {tested - wrong}/{tested} "
          f"files found on disk")
    if wrong:
        fails.append("the detector disagrees with the database")
    if tested == 0:
        print("    (no source files still on disk -- nothing to test against)")

    # A file of the wrong kind must be refused, not guessed at.
    with tempfile.TemporaryDirectory() as tmp:
        junk = Path(tmp) / "notahand.txt"
        junk.write_text("this is not a poker hand\nnor is this\n")
        got = sniff(junk)
        print(f"nonsense is refused           {'yes' if got is None else 'NO -> ' + got}")
        if got is not None:
            fails.append("a non-hand-history file was identified as a site")

    # Every module that writes columns onto `decisions` must be in CHAIN,
    # and after `decisions`, which drops the table. This is the assertion
    # that would have caught a rebuild running two stages out of five: it
    # does not ask whether the columns are in the database today, it asks
    # whether an import would put them back.
    import decisions
    import lines
    import players
    import strength
    order = [name for name, _what in CHAIN]
    con = sqlite3.connect(db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(decisions)")}
    con.close()
    for name, owned in (("lines", lines.LINE_COLUMNS),
                        ("strength", strength.COLUMNS),
                        ("players", players.NAMES)):
        placed = name in order and order.index(name) > order.index("decisions")
        print(f"{name + ' is in the chain':30} "
              f"{'yes' if placed else 'NO'}   {len(owned)} columns")
        if not placed:
            fails.append(f"{name} writes {len(owned)} columns onto decisions "
                         f"and the rebuild does not run it after decisions, "
                         f"so an import would delete them")
        missing = sorted(c for c in owned if c not in cols)
        if missing:
            fails.append(f"{name}'s columns are missing from decisions: "
                         f"{missing[:4]} -- run `python {name}.py`")
    # The reason the order matters, asserted rather than remembered.
    if "DROP TABLE IF EXISTS decisions" not in decisions.SCHEMA:
        fails.append("decisions.SCHEMA no longer drops the table, so the "
                     "reason CHAIN is ordered is out of date and should be "
                     "rewritten rather than left as folklore")

    # A file actually loads, through the path the window uses.
    #
    # Sniffing was checked and loading was not, and the difference cost
    # every import there was. `importer.load` hands each parser an explicit
    # list of files -- it has to, because one folder holds both sites -- and
    # in both parsers the counter beside that parameter was called `files`
    # too and was assigned first, so the list was overwritten with 0 before
    # the loop read it. `python importer.py <folder>` and the window's
    # Import menu both raised TypeError, and had for as long as the
    # two-site importer existed. 265 hands were sitting on this machine
    # unable to get in.
    #
    # So this loads a real file into a database of its own and counts what
    # arrived, which is the only version of this check that would have
    # noticed.
    for site in sites.KEYS:
        sample = next((f for place in scan()
                       for f in survey([place["path"]])[site]), None)
        if sample is None:
            print(f"{site} loads a real file          no {site} file on disk")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "one.db"
            got = load([sample], fresh)
            # Closed rather than left to the garbage collector: Windows will
            # not delete a file another handle is open on, so a leaked
            # connection here fails the temporary directory's cleanup and
            # not the check, which reads as a broken check.
            con = sqlite3.connect(fresh)
            held = con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
            con.close()
            mark = high_water(fresh)
        ok = got["files"] == 1 and got["added"] == held > 0
        print(f"{site + ' loads a real file':30} "
              f"{'yes' if ok else 'NO'}   {got['added']} hands "
              f"from {sample.name[:28]}")
        if not ok:
            fails.append(f"loading one {site} file through importer.load "
                         f"added {got['added']} hands and left {held} in "
                         f"the database")
        # And it has to remember having read it. Without the mark the
        # window offers to import the same files on every launch for ever,
        # which is a notice nobody reads by the third time.
        if abs((mark or 0) - sample.stat().st_mtime) > 1:
            fails.append(f"loading a {site} file left the high-water mark "
                         f"at {mark}, so it would be offered again")

    places_found = scan()
    print(f"places holding hands          {len(places_found)}")
    for p in places_found:
        print("    " + describe(p))

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--scan" in argv:
        found = scan()
        if not found:
            print("no hand histories found in the usual places")
            return 0
        for p in found:
            print(describe(p))
        return 0
    if "--refresh" in argv:
        got = refresh(progress=print)
        if not got["places"]:
            print("no hand histories found in the usual places")
            return 0
        print(f"\n{got['files']} files in {got['places']} places, "
              f"{got['added']} hands added, {got['known']} already known")
        if not got["added"]:
            print("nothing new -- the derived tables were left alone")
        return 0
    if "--merge" in argv:
        got = merge(argv[argv.index("--merge") + 1], progress=print)
        print(f"{got['added']} hands merged, {got['known']} already known")
        rebuild(progress=print)
        return 0
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 1
    got = load(paths, progress=print)
    print(f"\n{got['files']} files, {got['added']} hands added, "
          f"{got['known']} already known, {got['unknown']} unrecognised")
    for site, n in got["by_site"].items():
        print(f"  {site:10} {n}")
    if got["added"]:
        rebuild(progress=print)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
