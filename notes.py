"""
Marked hands and player notes -- the two things a tracker remembers for you.

A tag is a word on a hand: "review", "bluff-catch", "cooler". Mark a hand
at the table or in the replayer, and `--tag review` is then a filter like
any other, so "the hands I marked to look at again, on the button, in 3bet
pots" is one command. A note is text on a player, shown wherever that
player is looked at.

Both live in `hands.db`, in tables of their own, because they are ABOUT
hands and players and a separate file would drift from the database the
first time hands were re-imported. But they are the user's, not the
derivation's: `decisions.build` drops and remakes its table, `spots.build`
its own, and neither touches these. They are created on demand, exactly as
`meta` is, so that nothing which rebuilds a derived table can take them.

A note is keyed by site and player, because a screen name on PokerStars is
not the same person as that screen name on ACR, and an Ignition identity is
a seat for a session and nobody after it -- `sites.named()` says where a
note is worth writing at all, and the window only offers it there.

    python notes.py --tag <hand id> <tag> [<tag> ...]     mark a hand
    python notes.py --untag <hand id> <tag>              unmark it
    python notes.py --tags                               every tag, with its count
    python notes.py --note <site> <player> "<text>"      write a note (empty text removes)
    python notes.py --notes [<player>]                   read them
    python notes.py --check
"""

import re
import sqlite3
import sys
import time
from pathlib import Path

import sites

DB = Path(__file__).parent / "hands.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
  hand_id TEXT, tag TEXT, made TEXT, PRIMARY KEY (hand_id, tag));
CREATE TABLE IF NOT EXISTS notes (
  site TEXT, player TEXT, note TEXT, updated TEXT, PRIMARY KEY (site, player));
"""


def ensure(con):
    """The two tables, if the database does not have them yet."""
    con.executescript(SCHEMA)


def clean(tag):
    """
    One word, lower case, no commas.

    A tag is typed by hand and read back by a filter that takes a
    comma-separated list, so a comma inside one would split it in two, and
    "Review" and "review" being different tags is the kind of thing nobody
    notices until half the marked hands are missing from the report.
    """
    tag = re.sub(r"[\s,]+", "-", (tag or "").strip().lower()).strip("-")
    return tag


def tag(con, hand_id, *tags):
    """Mark a hand. Marking it again with the same tag changes nothing."""
    ensure(con)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    for t in tags:
        t = clean(t)
        if not t:
            continue
        cur = con.execute("INSERT OR IGNORE INTO tags VALUES (?, ?, ?)",
                          (hand_id, t, now))
        added += cur.rowcount
    con.commit()
    return added


def untag(con, hand_id, *tags):
    ensure(con)
    gone = 0
    for t in tags:
        gone += con.execute("DELETE FROM tags WHERE hand_id=? AND tag=?",
                            (hand_id, clean(t))).rowcount
    con.commit()
    return gone


def tags_of(con, hand_id):
    ensure(con)
    return [r[0] for r in con.execute(
        "SELECT tag FROM tags WHERE hand_id=? ORDER BY tag", (hand_id,))]


def tags_for(con, hand_ids):
    """{hand_id: [tags]} for many hands at once, for a listing."""
    ensure(con)
    out = {}
    ids = list(hand_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for hid, t in con.execute(
                f"SELECT hand_id, tag FROM tags WHERE hand_id IN ({marks}) "
                "ORDER BY tag", chunk):
            out.setdefault(hid, []).append(t)
    return out


def all_tags(con):
    """Every tag and how many hands carry it, most used first."""
    ensure(con)
    return con.execute(
        "SELECT tag, COUNT(*) FROM tags GROUP BY tag ORDER BY 2 DESC, 1"
    ).fetchall()


def note(con, site, player, text):
    """Write a player's note; empty text removes it."""
    ensure(con)
    text = (text or "").strip()
    if not text:
        con.execute("DELETE FROM notes WHERE site=? AND player=?", (site, player))
    else:
        con.execute("INSERT OR REPLACE INTO notes VALUES (?, ?, ?, ?)",
                    (site, player, text, time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()


def note_of(con, site, player):
    ensure(con)
    row = con.execute("SELECT note FROM notes WHERE site=? AND player=?",
                      (site, player)).fetchone()
    return row[0] if row else ""


def all_notes(con, player=None):
    ensure(con)
    if player:
        return con.execute(
            "SELECT site, player, note, updated FROM notes WHERE player=? "
            "ORDER BY site", (player,)).fetchall()
    return con.execute(
        "SELECT site, player, note, updated FROM notes ORDER BY site, player"
    ).fetchall()


def check(db_path=DB):
    """
    Round trips, on a database of its own, and the rules that make a tag
    findable again.
    """
    import tempfile
    fails = []
    with tempfile.TemporaryDirectory() as tmp:
        con = sqlite3.connect(Path(tmp) / "t.db")
        n = tag(con, "h1", "Review", "bluff catch", "review,again")
        got = tags_of(con, "h1")
        print(f"tags cleaned and kept        {got}")
        if got != ["bluff-catch", "review", "review-again"] or n != 3:
            fails.append(f"tagging h1 gave {got} ({n} added)")
        if tag(con, "h1", "review") != 0:
            fails.append("tagging the same tag twice counted it twice")
        if untag(con, "h1", "REVIEW") != 1 or "review" in tags_of(con, "h1"):
            fails.append("untag did not remove the tag it was given")
        tag(con, "h2", "review")
        counted = dict(all_tags(con))
        print(f"tag counts                   {counted}")
        if counted.get("review") != 1 or counted.get("bluff-catch") != 1:
            fails.append(f"all_tags counted {counted}")
        listed = tags_for(con, ["h1", "h2", "h3"])
        if listed != {"h1": ["bluff-catch", "review-again"], "h2": ["review"]}:
            fails.append(f"tags_for gave {listed}")

        note(con, "acr", "dblj32", "  calls down light, never folds top pair ")
        got = note_of(con, "acr", "dblj32")
        print(f"note round trip              {got!r}")
        if got != "calls down light, never folds top pair":
            fails.append(f"note came back as {got!r}")
        note(con, "acr", "dblj32", "3-bets tight")
        if note_of(con, "acr", "dblj32") != "3-bets tight":
            fails.append("a second note did not replace the first")
        if note_of(con, "pokerstars", "dblj32"):
            fails.append("a note on one site leaked to the same name elsewhere")
        note(con, "acr", "dblj32", "")
        if note_of(con, "acr", "dblj32"):
            fails.append("an empty note did not remove the note")
        con.close()

    # The derivation must never take these tables with it. `decisions.build`
    # drops its own table and that is the whole hazard, so the assertion is
    # that the drop names only that table.
    import decisions
    drops = [l for l in decisions.SCHEMA.splitlines() if "DROP" in l.upper()]
    print(f"decisions drops only itself  {drops}")
    if any("tags" in d or "notes" in d for d in drops) or \
            any("DROP" in l.upper() and "decisions" not in l for l in drops):
        fails.append("a DROP in decisions.SCHEMA could take the user's tables")
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    con = sqlite3.connect(DB)
    if "--tag" in argv:
        i = argv.index("--tag")
        if len(argv) < i + 3:
            raise SystemExit("--tag <hand id> <tag> [<tag> ...]")
        hid, tags = argv[i + 1], argv[i + 2:]
        if not con.execute("SELECT 1 FROM hands WHERE hand_id=?", (hid,)).fetchone():
            raise SystemExit(f"no hand {hid!r} in the database")
        n = tag(con, hid, *tags)
        print(f"{hid}: {', '.join(tags_of(con, hid))}   ({n} added)")
        return 0
    if "--untag" in argv:
        i = argv.index("--untag")
        if len(argv) < i + 3:
            raise SystemExit("--untag <hand id> <tag>")
        hid = argv[i + 1]
        n = untag(con, hid, *argv[i + 2:])
        print(f"{hid}: {', '.join(tags_of(con, hid)) or '(no tags)'}   ({n} removed)")
        return 0
    if "--tags" in argv:
        rows = all_tags(con)
        if not rows:
            print("no hands marked yet -- `python notes.py --tag <hand id> <tag>`")
        for t, n in rows:
            print(f"  {t:24} {n:6d} hands")
        return 0
    if "--note" in argv:
        i = argv.index("--note")
        if len(argv) < i + 4:
            raise SystemExit('--note <site> <player> "<text>"   (empty text removes)')
        site, player, text = argv[i + 1], argv[i + 2], " ".join(argv[i + 3:])
        if site not in sites.KEYS:
            raise SystemExit(f"no site {site!r}; one of {', '.join(sites.KEYS)}")
        note(con, site, player, text)
        print(f"{site} {player}: {note_of(con, site, player) or '(note removed)'}")
        return 0
    if "--notes" in argv:
        i = argv.index("--notes")
        who = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        rows = all_notes(con, who)
        if not rows:
            print("no notes yet -- `python notes.py --note <site> <player> \"text\"`")
        for site, player, text, updated in rows:
            print(f"  {site:10} {player:22} {text}   ({updated[:10]})")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
