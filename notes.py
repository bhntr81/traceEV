"""
Off-table study: player notes, hand marks, and tags.

A report is what the pool did. This is what *you* thought about it --
a sentence on a player, a star on a hand, a tag you can filter back
to. Hand2Note keeps those on the HUD and in the hand list; we have no
HUD, so the store is a small SQLite file beside the database and the
verbs live on the command line.

It is `notes.db`, not a table inside `hands.db`. Rebuilding the
derived tables used to be the kind of thing that quietly deleted
twenty-two columns; a note you wrote last Tuesday is not a derived
column and must not go with them. `--check` uses a memory connection
and never creates `hands.db`.

    python notes.py --add "calls too wide" --player NAME [--hand ID]
    python notes.py --list [--player NAME] [--hand ID]
    python notes.py --bind HAND --note-id N
    python notes.py --mark HAND [--tag leak]
    python notes.py --unmark HAND [--tag leak]
    python notes.py --marked [--tag leak]
    python notes.py --templates
    python notes.py --template review [--text "Come back to this."]
    python notes.py --check

`query.py --marked` / `--tag leak` / `--noted` are the same store
used as a filter. `query.py --hand ID --mark` is the hand-list path.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "hands.db"
NOTES = Path(__file__).parent / "notes.db"

# Seeded once, when the table is empty. Deleting a template stays
# deleted; putting them back on every open would fight the CRUD.
DEFAULT_TEMPLATES = (
    ("review", "Come back to this."),
    ("leak", "Leaking here."),
    ("thin value", "Thin value -- check the range."),
    ("bluff", "Bluff -- did it have a story?"),
    ("sizing", "Sizing looks off."),
)
DEFAULT_TAGS = ("review", "leak", "bluff", "value", "sizing")

# Letters and a few separators. A tag is a catalog key and a filter
# value; it reaches SQL as a bound parameter, but the same string is
# also printed and saved, so the day someone types `leak' OR 1=1` the
# refusal has to happen here rather than looking like a tag named that.
import re
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./+-]{0,39}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS {p}notes (
  id INTEGER PRIMARY KEY,
  player TEXT,
  site TEXT,
  text TEXT NOT NULL,
  spot TEXT,
  template TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS {p}note_hands (
  note_id INTEGER NOT NULL,
  hand_id TEXT NOT NULL,
  PRIMARY KEY (note_id, hand_id));
CREATE TABLE IF NOT EXISTS {p}templates (
  name TEXT PRIMARY KEY,
  text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS {p}tags (
  name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS {p}hand_tags (
  hand_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (hand_id, tag));
CREATE TABLE IF NOT EXISTS {p}hand_marks (
  hand_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS notes_player ON notes(player, site);
CREATE INDEX IF NOT EXISTS note_hands_hand ON note_hands(hand_id);
CREATE INDEX IF NOT EXISTS hand_tags_tag ON hand_tags(tag);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def valid_tag(name):
    return bool(name) and TAG.fullmatch(str(name).strip())


def _prefix(schema):
    return f"{schema}." if schema else ""


def ensure(con, schema=None):
    """Create the study tables. Never DROP -- this is the user's writing."""
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.format(p=_prefix(schema)))
    # Index names cannot be schema-qualified the way tables can
    # (`study.notes_player` is a syntax error). The file already has
    # them; an attached memory schema does not need them for `--check`.
    if not schema:
        con.executescript(INDEXES)
    p = _prefix(schema)
    n = con.execute(f"SELECT COUNT(*) FROM {p}templates").fetchone()[0]
    if n == 0:
        con.executemany(
            f"INSERT INTO {p}templates(name, text) VALUES (?, ?)",
            DEFAULT_TEMPLATES)
    n = con.execute(f"SELECT COUNT(*) FROM {p}tags").fetchone()[0]
    if n == 0:
        con.executemany(
            f"INSERT INTO {p}tags(name) VALUES (?)",
            [(t,) for t in DEFAULT_TAGS])
    con.commit()


def connect(path=None):
    """Open the study file, creating it. Never opens `hands.db`."""
    path = Path(path or NOTES)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    ensure(con)
    return con


def attach(con, path=None):
    """
    Hang the study store off a hands connection as `study`.

    Reports filter with `hand_id IN (SELECT ... FROM study.hand_marks)`.
    If `notes.db` is missing the attach is an empty memory schema so
    that SQL still runs and matches nothing -- and so a `--marked`
    report on a machine that has not marked yet does not create
    `hands.db` while failing inside it.
    """
    dbs = [r[1] for r in con.execute("PRAGMA database_list")]
    if "study" in dbs:
        return con
    path = Path(path or NOTES)
    if path.exists() and path.stat().st_size > 0:
        con.execute("ATTACH DATABASE ? AS study", (str(path.resolve()),))
        ensure(con, schema="study")
    else:
        con.execute("ATTACH DATABASE ':memory:' AS study")
        ensure(con, schema="study")
    return con


def _study(con):
    """`study.` when attached, else the connection is the study file."""
    dbs = [r[1] for r in con.execute("PRAGMA database_list")]
    return "study." if "study" in dbs else ""


def player_from_hand(hands_con, hand_id, seat=None):
    """
    The player a note on this hand is about.

    Hero first -- a note written from a replay of your own hand is
    almost always about you -- then the seat the report row named,
    then any named seat. `spots.identify` already decided the name;
    this only reads it. Without `hands.db` the caller passes `--player`.
    """
    if hands_con is None:
        return None, None

    def one(sql, params):
        row = hands_con.execute(sql, params).fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    if seat is not None:
        site, player = one(
            "SELECT site, player FROM decisions "
            "WHERE hand_id = ? AND seat = ? AND player IS NOT NULL LIMIT 1",
            (hand_id, seat))
        if player:
            return site, player
    site, player = one(
        "SELECT site, player FROM decisions "
        "WHERE hand_id = ? AND is_hero = 1 AND player IS NOT NULL LIMIT 1",
        (hand_id,))
    if player:
        return site, player
    return one(
        "SELECT site, player FROM decisions "
        "WHERE hand_id = ? AND player IS NOT NULL LIMIT 1",
        (hand_id,))


def add(text, player=None, site=None, hand_id=None, seat=None,
        spot=None, template=None, path=None, hands_con=None):
    """Write a note. Bind a hand when given; pick the player from it if not set."""
    text = (text or "").strip()
    if template:
        rows = templates(path=path)
        known = {r["name"]: r["text"] for r in rows}
        if template not in known:
            raise ValueError(f"no template {template!r} -- see --templates")
        if not text or text == template:
            text = known[template]
    if not text:
        raise ValueError("a note needs some text")
    if hand_id and not player and hands_con is not None:
        site, player = player_from_hand(hands_con, hand_id, seat)
    con = connect(path)
    cur = con.execute(
        "INSERT INTO notes(player, site, text, spot, template, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (player, site, text, spot, template, now()))
    note_id = cur.lastrowid
    if hand_id:
        con.execute(
            "INSERT OR IGNORE INTO note_hands(note_id, hand_id) VALUES (?, ?)",
            (note_id, hand_id))
    con.commit()
    con.close()
    return note_id


def bind(note_id, hand_id, path=None):
    con = connect(path)
    exists = con.execute(
        "SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not exists:
        con.close()
        raise ValueError(f"no note {note_id}")
    con.execute(
        "INSERT OR IGNORE INTO note_hands(note_id, hand_id) VALUES (?, ?)",
        (note_id, hand_id))
    con.commit()
    con.close()


def list_notes(player=None, hand_id=None, spot=None, path=None):
    con = connect(path)
    where, params = ["1=1"], []
    if player:
        where.append("player = ?")
        params.append(player)
    if spot:
        where.append("spot = ?")
        params.append(spot)
    if hand_id:
        where.append(
            "id IN (SELECT note_id FROM note_hands WHERE hand_id = ?)")
        params.append(hand_id)
    rows = con.execute(
        "SELECT id, player, site, text, spot, template, created_at "
        "FROM notes WHERE " + " AND ".join(where) +
        " ORDER BY created_at DESC, id DESC", params).fetchall()
    out = []
    for row in rows:
        hands = [r[0] for r in con.execute(
            "SELECT hand_id FROM note_hands WHERE note_id = ? "
            "ORDER BY hand_id", (row["id"],))]
        out.append({**dict(row), "hands": hands})
    con.close()
    return out


def templates(path=None):
    con = connect(path)
    rows = [dict(r) for r in con.execute(
        "SELECT name, text FROM templates ORDER BY name")]
    con.close()
    return rows


def save_template(name, text, path=None):
    name = (name or "").strip()
    text = (text or "").strip()
    if not name or not text:
        raise ValueError("a template needs a name and some text")
    con = connect(path)
    con.execute(
        "INSERT INTO templates(name, text) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET text = excluded.text",
        (name, text))
    con.commit()
    con.close()


def forget_template(name, path=None):
    con = connect(path)
    cur = con.execute("DELETE FROM templates WHERE name = ?", (name,))
    con.commit()
    n = cur.rowcount
    con.close()
    if not n:
        raise ValueError(f"no template {name!r}")


def add_tag(name, path=None):
    name = (name or "").strip()
    if not valid_tag(name):
        raise ValueError(
            f"invalid tag {name!r} -- letters, digits, and _./+-")
    con = connect(path)
    con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
    con.commit()
    con.close()
    return name


def forget_tag(name, path=None):
    con = connect(path)
    con.execute("DELETE FROM hand_tags WHERE tag = ?", (name,))
    cur = con.execute("DELETE FROM tags WHERE name = ?", (name,))
    con.commit()
    n = cur.rowcount
    con.close()
    if not n:
        raise ValueError(f"no tag {name!r}")


def list_tags(path=None):
    con = connect(path)
    rows = [r[0] for r in con.execute(
        "SELECT name FROM tags ORDER BY name")]
    con.close()
    return rows


def mark(hand_id, tags=(), path=None):
    """Star a hand, and attach any tags. A tag not in the catalog is added."""
    if not hand_id:
        raise ValueError("mark needs a hand id")
    names = []
    for raw in tags:
        name = str(raw).strip()
        if not valid_tag(name):
            raise ValueError(f"invalid tag {name!r}")
        names.append(name)
    con = connect(path)
    con.execute(
        "INSERT OR IGNORE INTO hand_marks(hand_id, created_at) VALUES (?, ?)",
        (hand_id, now()))
    for name in names:
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        con.execute(
            "INSERT OR IGNORE INTO hand_tags(hand_id, tag) VALUES (?, ?)",
            (hand_id, name))
    con.commit()
    con.close()


def unmark(hand_id, tags=None, path=None):
    """
    Drop the star, or only the named tags.

    `--unmark HAND` is the star. `--unmark HAND --tag leak` takes the
    tag off and leaves the star, because a hand can stay marked after
    you decide it was not a leak.
    """
    if not hand_id:
        raise ValueError("unmark needs a hand id")
    con = connect(path)
    if tags:
        for name in tags:
            con.execute(
                "DELETE FROM hand_tags WHERE hand_id = ? AND tag = ?",
                (hand_id, str(name).strip()))
    else:
        con.execute("DELETE FROM hand_marks WHERE hand_id = ?", (hand_id,))
    con.commit()
    con.close()


def marked(tag=None, path=None):
    con = connect(path)
    if tag:
        rows = con.execute(
            "SELECT m.hand_id, m.created_at FROM hand_marks m "
            "JOIN hand_tags t ON t.hand_id = m.hand_id "
            "WHERE t.tag = ? ORDER BY m.created_at DESC",
            (tag,)).fetchall()
    else:
        rows = con.execute(
            "SELECT hand_id, created_at FROM hand_marks "
            "ORDER BY created_at DESC").fetchall()
    out = []
    for row in rows:
        tags = [r[0] for r in con.execute(
            "SELECT tag FROM hand_tags WHERE hand_id = ? ORDER BY tag",
            (row["hand_id"],))]
        out.append({"hand_id": row["hand_id"],
                    "created_at": row["created_at"], "tags": tags})
    con.close()
    return out


def decorate(con, rows):
    """Stamp marked / tags / note-count onto matching_hands rows."""
    attach(con)
    p = _study(con)
    ids = [r.get("id") or r.get("hand_id") for r in rows]
    if not ids:
        return rows
    qmarks = ",".join("?" * len(ids))
    starred = {r[0] for r in con.execute(
        f"SELECT hand_id FROM {p}hand_marks WHERE hand_id IN ({qmarks})",
        ids)}
    tags = {}
    for hid, tag in con.execute(
            f"SELECT hand_id, tag FROM {p}hand_tags "
            f"WHERE hand_id IN ({qmarks})", ids):
        tags.setdefault(hid, []).append(tag)
    notes_n = dict(con.execute(
        f"SELECT hand_id, COUNT(*) FROM {p}note_hands "
        f"WHERE hand_id IN ({qmarks}) GROUP BY hand_id", ids))
    for row in rows:
        hid = row.get("id") or row.get("hand_id")
        row["marked"] = hid in starred
        row["tags"] = tags.get(hid, [])
        row["n_notes"] = notes_n.get(hid, 0)
    return rows


def check(path=None):
    """CRUD, player-from-hand, and the filter SQL, without `hands.db`."""
    fails = []
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ensure(con)
    tmp = Path(path) if path else None
    if tmp is None:
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "notes.db"

    nid = add("calls too wide", player="Alice", site="acr",
              spot="Flop c-bets", path=tmp)
    bind(nid, "h1", path=tmp)
    got = list_notes(player="Alice", path=tmp)
    if len(got) != 1 or got[0]["text"] != "calls too wide":
        fails.append(f"add/list lost the note: {got}")
    if got and got[0]["hands"] != ["h1"]:
        fails.append(f"bind did not attach h1: {got}")
    if got and got[0]["spot"] != "Flop c-bets":
        fails.append(f"spot key dropped: {got}")

    add("leak", player="Alice", template="leak", path=tmp)
    leaks = [n for n in list_notes(player="Alice", path=tmp)
             if n["template"] == "leak"]
    if not leaks or leaks[0]["text"] != "Leaking here.":
        fails.append(f"template did not expand: {leaks}")

    save_template("custom", "Look at sizing.", path=tmp)
    names = [t["name"] for t in templates(path=tmp)]
    if "custom" not in names:
        fails.append("save_template did not keep the name")
    forget_template("custom", path=tmp)
    if "custom" in [t["name"] for t in templates(path=tmp)]:
        fails.append("forget_template left the name")

    mark("h1", tags=["leak", "bluff"], path=tmp)
    mark("h2", path=tmp)
    stars = marked(path=tmp)
    if {r["hand_id"] for r in stars} != {"h1", "h2"}:
        fails.append(f"marked listed {stars}")
    tagged = marked(tag="leak", path=tmp)
    if [r["hand_id"] for r in tagged] != ["h1"]:
        fails.append(f"--marked --tag leak listed {tagged}")
    unmark("h1", tags=["bluff"], path=tmp)
    left = marked(path=tmp)
    h1 = next(r for r in left if r["hand_id"] == "h1")
    if "bluff" in h1["tags"] or "leak" not in h1["tags"]:
        fails.append(f"unmark --tag dropped the wrong thing: {h1}")
    unmark("h2", path=tmp)
    if any(r["hand_id"] == "h2" for r in marked(path=tmp)):
        fails.append("unmark left the star")

    refused = 0
    for bad in ("", "leak' OR 1=1", ";DROP", "a" * 80):
        try:
            mark("h9", tags=[bad], path=tmp)
        except ValueError:
            refused += 1
        else:
            fails.append(f"tag {bad!r} was accepted")
    if refused < 4:
        fails.append("a tag that is not a tag was accepted")

    hands = sqlite3.connect(":memory:")
    hands.row_factory = sqlite3.Row
    hands.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, seat INT, site TEXT, player TEXT, is_hero INT)")
    hands.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?)",
        [("h3", 1, "acr", "Hero", 1),
         ("h3", 2, "acr", "Villain", 0)])
    site, player = player_from_hand(hands, "h3")
    if player != "Hero" or site != "acr":
        fails.append(f"player_from_hand picked {site!r} {player!r}, not hero")
    site, player = player_from_hand(hands, "h3", seat=2)
    if player != "Villain":
        fails.append(f"player_from_hand ignored seat: {player!r}")
    add("about villain", hand_id="h3", seat=2, path=tmp, hands_con=hands)
    about = list_notes(hand_id="h3", path=tmp)
    if not any(n["player"] == "Villain" for n in about):
        fails.append(f"add --hand did not pick the seat's player: {about}")

    # A report connection sees the store through ATTACH, which is how
    # `--marked` is going to find anything.
    report = sqlite3.connect(":memory:")
    report.execute("CREATE TABLE decisions (hand_id TEXT)")
    report.executemany("INSERT INTO decisions VALUES (?)",
                       [("h1",), ("h2",), ("hx",)])
    attach(report, path=tmp)
    n = report.execute(
        "SELECT COUNT(*) FROM decisions WHERE hand_id IN "
        "(SELECT hand_id FROM study.hand_marks)").fetchone()[0]
    if n != 1:
        fails.append(f"attached --marked saw {n} hands, not the one star")
    rows = decorate(report, [{"id": "h1"}, {"id": "hx"}])
    if not rows[0]["marked"] or rows[1]["marked"]:
        fails.append(f"decorate marked flags were {rows}")
    if "leak" not in rows[0]["tags"]:
        fails.append(f"decorate dropped tags: {rows[0]}")
    report.close()
    hands.close()
    con.close()

    print(f"notes / marks / tags          "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return not fails


def _opt(argv, name, default=None):
    if name not in argv:
        return default
    i = argv.index(name) + 1
    if i >= len(argv):
        raise SystemExit(f"{name} needs a value")
    return argv[i]


def show_list(rows):
    if not rows:
        print("no notes")
        return
    for row in rows:
        who = row["player"] or "(no player)"
        site = f"  {row['site']}" if row["site"] else ""
        spot = f"  [{row['spot']}]" if row["spot"] else ""
        hands = f"  hands {', '.join(row['hands'])}" if row["hands"] else ""
        print(f"#{row['id']}  {who}{site}{spot}")
        print(f"    {row['text']}")
        print(f"    {row['created_at']}{hands}")


def show_marked(rows):
    if not rows:
        print("no marked hands")
        return
    print(f"{len(rows)} marked")
    for row in rows:
        tags = ",".join(row["tags"]) if row["tags"] else "-"
        print(f"  {row['hand_id']:24}  {row['created_at']}  {tags}")


def main(argv):
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    path = _opt(argv, "--db") or None
    if "--templates" in argv:
        for row in templates(path=path):
            print(f"  {row['name']:16}  {row['text']}")
        return 0
    if "--template" in argv:
        name = _opt(argv, "--template")
        text = _opt(argv, "--text")
        if text:
            save_template(name, text, path=path)
            print(f"saved template {name!r}")
        else:
            known = {r["name"]: r["text"] for r in templates(path=path)}
            if name not in known:
                raise SystemExit(f"no template {name!r}")
            print(known[name])
        return 0
    if "--forget-template" in argv:
        forget_template(_opt(argv, "--forget-template"), path=path)
        print("forgot the template")
        return 0
    if "--tags" in argv:
        for name in list_tags(path=path):
            print(f"  {name}")
        return 0
    if "--tag-add" in argv:
        print("tag", add_tag(_opt(argv, "--tag-add"), path=path))
        return 0
    if "--forget-tag" in argv:
        forget_tag(_opt(argv, "--forget-tag"), path=path)
        print("forgot the tag")
        return 0
    if "--marked" in argv:
        show_marked(marked(tag=_opt(argv, "--tag"), path=path))
        return 0
    if "--mark" in argv:
        hid = _opt(argv, "--mark")
        raw = _opt(argv, "--tag")
        tags = [x.strip() for x in (raw or "").split(",") if x.strip()]
        mark(hid, tags, path=path)
        print(f"marked {hid}" + (f"  {', '.join(tags)}" if tags else ""))
        return 0
    if "--unmark" in argv:
        hid = _opt(argv, "--unmark")
        raw = _opt(argv, "--tag")
        tags = [x.strip() for x in (raw or "").split(",") if x.strip()]
        unmark(hid, tags or None, path=path)
        print(f"unmarked {hid}" + (f"  {', '.join(tags)}" if tags else ""))
        return 0
    if "--bind" in argv:
        hid = _opt(argv, "--bind")
        nid = _opt(argv, "--note-id")
        if nid is None:
            raise SystemExit("--bind needs --note-id")
        bind(int(nid), hid, path=path)
        print(f"bound note #{nid} to {hid}")
        return 0
    if "--add" in argv:
        text = _opt(argv, "--add")
        hid = _opt(argv, "--hand")
        hands_con = None
        if hid and DB.exists() and DB.stat().st_size > 0:
            hands_con = sqlite3.connect(str(DB))
            hands_con.row_factory = sqlite3.Row
        try:
            nid = add(
                text,
                player=_opt(argv, "--player"),
                site=_opt(argv, "--site"),
                hand_id=hid,
                seat=int(_opt(argv, "--seat")) if _opt(argv, "--seat") else None,
                spot=_opt(argv, "--spot"),
                template=_opt(argv, "--from-template"),
                path=path,
                hands_con=hands_con)
        finally:
            if hands_con is not None:
                hands_con.close()
        print(f"note #{nid}")
        return 0
    if "--list" in argv:
        show_list(list_notes(
            player=_opt(argv, "--player"),
            hand_id=_opt(argv, "--hand"),
            spot=_opt(argv, "--spot"),
            path=path))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
