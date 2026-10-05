"""
Marked hands, player notes, and aliases -- what a tracker remembers for you.

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

An alias says two names on one site are one person -- a name change, a
second account -- and from then on every hand under the alias counts
under the player. It is applied where identity is decided and nowhere
else: `spots.identify` asks for the site's aliases and maps the label,
so `player` on every derived row is already the merged name and no view
has to know. That is also why an alias needs a rebuild to take effect
(`python importer.py --rebuild`), and the command says so.

Within a site only. The same name on two sites may well be one person,
but every pool baseline, note and report is per site, and merging across
them would put ACR hands into a PokerStars profile measured against the
PokerStars pool.

    python notes.py --tag <hand id> <tag> [<tag> ...]     mark a hand
    python notes.py --untag <hand id> <tag>              unmark it
    python notes.py --tags                               every tag, with its count
    python notes.py --note <site> <player> "<text>"      write a note (empty text removes)
    python notes.py --notes [<player>]                   read them
    python notes.py --alias <site> <alias> <player>      the alias is the player
    python notes.py --unalias <site> <alias>
    python notes.py --aliases                            every alias
    python notes.py --template <name> "<text>"           a sentence to reuse; {vpip} fills in
    python notes.py --templates                          every template
    python notes.py --use-template <site> <player> <name>  add one to a player's note
    python notes.py --note-hand <site> <player> <hand id> ["<words>"]  a hand beside them
    python notes.py --stat-note <site> <player> <stat> "<text>"        a note on one stat
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
CREATE TABLE IF NOT EXISTS aliases (
  site TEXT, alias TEXT, player TEXT, made TEXT, PRIMARY KEY (site, alias));
CREATE TABLE IF NOT EXISTS note_templates (
  name TEXT PRIMARY KEY, text TEXT, made TEXT);
CREATE TABLE IF NOT EXISTS stat_notes (
  site TEXT, player TEXT, stat TEXT, note TEXT, updated TEXT,
  PRIMARY KEY (site, player, stat));
CREATE TABLE IF NOT EXISTS note_hands (
  site TEXT, player TEXT, hand_id TEXT, comment TEXT, made TEXT,
  PRIMARY KEY (site, player, hand_id));
"""


def ensure(con):
    """The user's tables, if the database does not have them yet."""
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


def alias(con, site, alias_name, player):
    """
    The alias is the player, from the next rebuild on.

    Chains are resolved when written rather than when read: aliasing B to
    A and then C to B records C as A, so `identify` maps in one step and
    an alias can never point at a name that is itself an alias. Aliasing a
    name to itself, or making a loop, is refused.
    """
    ensure(con)
    alias_name, player = alias_name.strip(), player.strip()
    if not alias_name or not player:
        raise ValueError("an alias and a player are both needed")
    player = alias_map(con, site).get(player, player)
    if alias_name == player:
        raise ValueError(f"{alias_name!r} is already {player!r}")
    # Anyone who was aliased TO the new alias follows it to the player.
    con.execute("UPDATE aliases SET player=? WHERE site=? AND player=?",
                (player, site, alias_name))
    con.execute("INSERT OR REPLACE INTO aliases VALUES (?, ?, ?, ?)",
                (site, alias_name, player, time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()


def unalias(con, site, alias_name):
    ensure(con)
    n = con.execute("DELETE FROM aliases WHERE site=? AND alias=?",
                    (site, alias_name.strip())).rowcount
    con.commit()
    return n


def alias_map(con, site):
    """{alias: player} for one site -- what `spots.identify` applies."""
    ensure(con)
    return dict(con.execute("SELECT alias, player FROM aliases WHERE site=?",
                            (site,)).fetchall())


def all_aliases(con):
    ensure(con)
    return con.execute(
        "SELECT site, alias, player FROM aliases ORDER BY site, player, alias"
    ).fetchall()


# ---- Hand2Note's three kinds of note beyond the plain one --------------
#
# A template is a sentence written once and dropped into any player's note:
# "3-bets light from the button", or with the numbers in it, "{threebet}"
# becoming "3bet 14% (n=58)". The numbers are filled in at the moment the
# template is used and written into the note as text, dated, because a
# note is what was true when it was written -- a note that silently
# recomputed would say "fold to 3-bet 70%" today about a read made on 9%.
# Every number carries its n, by the project's first reporting rule; a
# template naming a stat that does not exist is refused when it is saved,
# not discovered as a literal "{fold_to_3bat}" in a note at the table.
#
# A hand in a note is a hand id beside the player, with a few words, so
# "the river overbluff" can be opened again from the player rather than
# remembered. It must be a hand the player was dealt into: a note pointing
# at a hand they were not in is a typo that reads as evidence.
#
# A note on a stat is the read beside the number it is about -- "folds to
# 3-bets only when out of position" -- keyed by site, player and stat, and
# shown on that stat's row wherever that player's rates are.

PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


def _stat_keys():
    import stats
    stats.load_custom()
    return stats.BY_KEY


def template(con, name, text):
    """Save a template; an empty text removes it. Unknown stats are refused."""
    ensure(con)
    name, text = (name or "").strip(), (text or "").strip()
    if not name:
        raise ValueError("a template needs a name")
    if not text:
        con.execute("DELETE FROM note_templates WHERE name=?", (name,))
        con.commit()
        return
    known = _stat_keys()
    unknown = [k for k in PLACEHOLDER.findall(text) if k not in known]
    if unknown:
        raise ValueError(f"no stat called {', '.join(unknown)}; a placeholder "
                         f"is a stat key, as `query.py --show` takes")
    con.execute("INSERT OR REPLACE INTO note_templates VALUES (?, ?, ?)",
                (name, text, time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()


def templates(con):
    ensure(con)
    return con.execute(
        "SELECT name, text FROM note_templates ORDER BY name").fetchall()


def fill(con, text, site, player):
    """
    A template's placeholders as this player's rates, each with its n.

    The rate is the raw one over everything the player did on the site --
    the note is about them, not about a filter -- and a stat they never
    had the chance to take reads "no chances" rather than 0%, which would
    be a read nobody made.
    """
    import stats
    known = _stat_keys()

    def one(m):
        s = known.get(m.group(1))
        if s is None:
            return m.group(0)
        n, k, p, _lo, _hi = stats.rate(con, s, "player=? AND site=?",
                                       (player, site))
        if not n:
            return f"{s.label} no chances"
        return f"{s.label} {100 * p:.0f}% (n={n})"
    return PLACEHOLDER.sub(one, text)


def filled(con, name, site, player):
    """A template as the line it puts in this player's note, dated if it has numbers."""
    ensure(con)
    row = con.execute("SELECT text FROM note_templates WHERE name=?",
                      (name,)).fetchone()
    if row is None:
        raise ValueError(f"no template called {name!r}")
    line = fill(con, row[0], site, player)
    if PLACEHOLDER.search(row[0]):
        line += f" ({time.strftime('%Y-%m-%d')})"
    return line


def use_template(con, site, player, name):
    """Add a template, filled for this player, to the end of their note."""
    line = filled(con, name, site, player)
    was = note_of(con, site, player)
    note(con, site, player, (was + "\n" if was else "") + line)
    return note_of(con, site, player)


def note_hand(con, site, player, hand_id, comment=""):
    """Put a hand beside a player; the player has to have been dealt in."""
    ensure(con)
    dealt = con.execute("SELECT 1 FROM spots WHERE hand_id=? AND site=? "
                        "AND player=?", (hand_id, site, player)).fetchone()
    if not dealt:
        raise ValueError(f"{player} on {site} was not dealt into {hand_id}")
    con.execute("INSERT OR REPLACE INTO note_hands VALUES (?, ?, ?, ?, ?)",
                (site, player, hand_id, (comment or "").strip(),
                 time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()


def unnote_hand(con, site, player, hand_id):
    ensure(con)
    n = con.execute("DELETE FROM note_hands WHERE site=? AND player=? "
                    "AND hand_id=?", (site, player, hand_id)).rowcount
    con.commit()
    return n


def hands_noted(con, site, player):
    """[(hand_id, comment)] beside a player, newest first."""
    ensure(con)
    return con.execute("SELECT hand_id, comment FROM note_hands WHERE site=? "
                       "AND player=? ORDER BY made DESC, hand_id",
                       (site, player)).fetchall()


def stat_note(con, site, player, stat, text):
    """Write a note on one of a player's stats; empty text removes it."""
    ensure(con)
    if stat not in _stat_keys():
        raise ValueError(f"no stat called {stat!r}")
    text = (text or "").strip()
    if not text:
        con.execute("DELETE FROM stat_notes WHERE site=? AND player=? AND stat=?",
                    (site, player, stat))
    else:
        con.execute("INSERT OR REPLACE INTO stat_notes VALUES (?, ?, ?, ?, ?)",
                    (site, player, stat, text, time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()


def stat_notes_of(con, site, player):
    """{stat key: note} for one player."""
    ensure(con)
    return dict(con.execute("SELECT stat, note FROM stat_notes WHERE site=? "
                            "AND player=?", (site, player)).fetchall())


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

        # Aliases: chains flatten, loops are refused, and identify applies
        # the map -- on the site it was written for and no other.
        alias(con, "acr", "old_name", "the_reg")
        alias(con, "acr", "older_name", "old_name")
        amap = alias_map(con, "acr")
        print(f"alias map                    {amap}")
        if amap != {"old_name": "the_reg", "older_name": "the_reg"}:
            fails.append(f"alias chain did not flatten: {amap}")
        try:
            alias(con, "acr", "the_reg", "old_name")
            fails.append("a loop was accepted")
        except ValueError:
            pass
        import spots
        hands = [{"hand_id": "h1", "site": "acr", "fmt": "RING", "table_id": "t"},
                 {"hand_id": "h2", "site": "pokerstars", "fmt": "RING", "table_id": "t"}]
        seats = {"h1": [{"seat": 1, "label": "older_name"}, {"seat": 2, "label": "x"}],
                 "h2": [{"seat": 1, "label": "older_name"}]}
        who = spots.identify(hands, seats, {"acr": amap})
        print(f"identify applies it          {who}")
        if who.get(("h1", 1)) != "the_reg" or who.get(("h2", 1)) != "older_name":
            fails.append(f"identify gave {who}")
        if unalias(con, "acr", "older_name") != 1 or "older_name" in alias_map(con, "acr"):
            fails.append("unalias did not remove the alias")

        # Templates: a misspelt stat is refused at the door, and a used one
        # lands on the end of the note rather than over it.
        try:
            template(con, "bad", "folds {fold_to_3bat}")
            fails.append("a template naming no stat was saved")
        except ValueError:
            pass
        template(con, "light", "3-bets light from the button")
        note(con, "acr", "dblj32", "calls down")
        got = use_template(con, "acr", "dblj32", "light")
        print(f"template appended            {got!r}")
        if got != "calls down\n3-bets light from the button":
            fails.append(f"use_template left the note as {got!r}")

        # A hand beside a player: only one they were dealt into.
        con.execute("CREATE TABLE spots (hand_id TEXT, seat INT, player TEXT, "
                    "site TEXT)")
        con.execute("INSERT INTO spots VALUES ('h9', 3, 'dblj32', 'acr')")
        try:
            note_hand(con, "acr", "dblj32", "h8", "never there")
            fails.append("a hand the player was not in was put in their note")
        except ValueError:
            pass
        note_hand(con, "acr", "dblj32", "h9", " river overbluff ")
        got = hands_noted(con, "acr", "dblj32")
        print(f"hand in a note               {got}")
        if got != [("h9", "river overbluff")]:
            fails.append(f"hands_noted gave {got}")
        if unnote_hand(con, "acr", "dblj32", "h9") != 1 or hands_noted(con, "acr", "dblj32"):
            fails.append("unnote_hand did not take the hand out")

        # A note on a stat: on that stat and that player only.
        stat_note(con, "acr", "dblj32", "fold_to_3bet", "only folds OOP")
        try:
            stat_note(con, "acr", "dblj32", "fold_to_3bat", "x")
            fails.append("a note on a stat that does not exist was kept")
        except ValueError:
            pass
        got = stat_notes_of(con, "acr", "dblj32")
        print(f"note on a stat               {got}")
        if got != {"fold_to_3bet": "only folds OOP"} or stat_notes_of(con, "pokerstars", "dblj32"):
            fails.append(f"stat notes came back as {got}")
        stat_note(con, "acr", "dblj32", "fold_to_3bet", "")
        if stat_notes_of(con, "acr", "dblj32"):
            fails.append("an empty stat note did not remove it")
        con.close()

    # Filled from the real engine where there is a database: every number
    # with its n, and a stat never had the chance at said so, not 0%.
    if Path(db_path).exists():
        real = sqlite3.connect(db_path)
        who = real.execute("SELECT site, player FROM decisions WHERE is_hero=0 "
                           "AND player IS NOT NULL GROUP BY 1, 2 "
                           "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        if who:
            got = fill(real, "{vpip}, {threebet}", *who)
            print(f"a template filled            {who[1]}: {got}")
            if not re.fullmatch(r"VPIP \d+% \(n=\d+\), \S+ (\d+% \(n=\d+\)|no chances)", got):
                fails.append(f"a filled template read {got!r}")
        real.close()

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
    if "--alias" in argv:
        i = argv.index("--alias")
        if len(argv) < i + 4:
            raise SystemExit("--alias <site> <alias> <player>")
        site, alias_name, player = argv[i + 1], argv[i + 2], argv[i + 3]
        if site not in sites.named():
            raise SystemExit(f"{site!r} has no names to alias; one of "
                             f"{', '.join(sites.named())}")
        try:
            alias(con, site, alias_name, player)
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"{site}: {alias_name} is {alias_map(con, site)[alias_name]}")
        print("takes effect on the next rebuild:  python importer.py --rebuild")
        return 0
    if "--unalias" in argv:
        i = argv.index("--unalias")
        if len(argv) < i + 3:
            raise SystemExit("--unalias <site> <alias>")
        n = unalias(con, argv[i + 1], argv[i + 2])
        print(f"{n} alias removed; takes effect on the next rebuild:  "
              "python importer.py --rebuild")
        return 0
    if "--aliases" in argv:
        rows = all_aliases(con)
        if not rows:
            print("no aliases yet -- `python notes.py --alias <site> <alias> <player>`")
        for site, a, p in rows:
            print(f"  {site:10} {a:22} is {p}")
        return 0
    if "--template" in argv:
        i = argv.index("--template")
        if len(argv) < i + 2:
            raise SystemExit('--template <name> "<text with {stat} in it>"   (empty text removes)')
        try:
            template(con, argv[i + 1], " ".join(argv[i + 2:]))
        except ValueError as e:
            raise SystemExit(str(e))
        return 0
    if "--templates" in argv:
        rows = templates(con)
        if not rows:
            print('no templates yet -- `python notes.py --template light "3-bets light, {threebet}"`')
        for name, text in rows:
            print(f"  {name:16} {text}")
        return 0
    if "--use-template" in argv:
        i = argv.index("--use-template")
        if len(argv) < i + 4:
            raise SystemExit("--use-template <site> <player> <template>")
        try:
            print(use_template(con, argv[i + 1], argv[i + 2], argv[i + 3]))
        except ValueError as e:
            raise SystemExit(str(e))
        return 0
    if "--note-hand" in argv:
        i = argv.index("--note-hand")
        if len(argv) < i + 4:
            raise SystemExit('--note-hand <site> <player> <hand id> ["<words>"]')
        try:
            note_hand(con, argv[i + 1], argv[i + 2], argv[i + 3],
                      " ".join(argv[i + 4:]))
        except ValueError as e:
            raise SystemExit(str(e))
        return 0
    if "--stat-note" in argv:
        i = argv.index("--stat-note")
        if len(argv) < i + 4:
            raise SystemExit('--stat-note <site> <player> <stat> "<text>"   (empty text removes)')
        try:
            stat_note(con, argv[i + 1], argv[i + 2], argv[i + 3],
                      " ".join(argv[i + 4:]))
        except ValueError as e:
            raise SystemExit(str(e))
        return 0
    if "--notes" in argv:
        i = argv.index("--notes")
        who = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        rows = all_notes(con, who)
        if not rows:
            print("no notes yet -- `python notes.py --note <site> <player> \"text\"`")
        for site, player, text, updated in rows:
            print(f"  {site:10} {player:22} {text}   ({updated[:10]})")
            for stat, said in sorted(stat_notes_of(con, site, player).items()):
                print(f"  {'':10} {'':22} on {stat}: {said}")
            for hid, words in hands_noted(con, site, player):
                print(f"  {'':10} {'':22} hand {hid}  {words}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
