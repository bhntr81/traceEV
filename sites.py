"""
What each site is, decided once.

Everything that differs between two sites' hand histories is a fact about
the site, and until this file existed each of those facts was written
where it was first needed: the header a file starts with in the importer,
whether a name is a person in `spots.identify`, whether folded hands are
shown in `population`, which sites have people in `opponents` and `stats`,
where the client keeps its files in the importer again. Thirty-eight places
across fourteen modules, each spelling "ignition" or "acr" by hand. A third
site meant finding all of them, and the one missed would not raise -- it
would average two pools into a number describing neither, which is what
happened when ACR arrived and `fmt='RING'` stopped naming one pool.

So a site is an entry here, and the rest of the program asks. A new site is
one parser module and one entry below, and the parser's whole contract is:

  HEADER(line)               true if this line begins one of its hands
  split_hands(text)          each hand in a file, as its own block
  parse_hand(block, source)  the shared dict shape -- {"hand", "seats",
                             "actions"} with every column the loader writes,
                             `won` being what came back from the pot and
                             never profit, positions named as the rest of
                             the program expects them

The loader, the schema and the checks are the importer's and this file's,
never the parser's. Two parsers each carried a private copy of the loader
until now, one of them writing fewer columns than the other.

    python sites.py            the registry, and what the database holds
    python sites.py --stats    each site's hands, coverage and results
    python sites.py --check    every site loaded proves its import
"""

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import acr
import ignition
import pokerstars

DB = Path(__file__).parent / "hands.db"


@dataclass(frozen=True)
class Site:
    # The value in the `site` column, and the word `--site` takes.
    key: str
    # The parser: HEADER, split_hands, parse_hand, nothing else.
    module: object
    # A label is a person. ACR writes the screen name and it is the same
    # player next week at another stake; an Ignition seat is whoever is sat
    # in it, so identity there is table:seat:segment and dies with the
    # session, and Zone is nobody at all. Anything that counts identities
    # as people asks this, and `spots.identify` decides the name from it.
    names: bool
    # Every seat's hole cards are written, folds included, so a range at a
    # spot can be counted rather than inferred from who chose to show.
    # `population` is built on this premise and pins itself to these sites:
    # a site showing 23% of hands presented as a range is worse than none.
    reveals: bool
    # The history states the house's cut. Where it does, the import is
    # proved by "everything in came back out minus the cut"; where it does
    # not, by "everything in equals the pot the site says there was", which
    # is what Ignition's gross `Total Pot(...)` allows.
    rake: bool
    # Where the client writes histories when nobody has moved them, with
    # environment variables unexpanded.
    places: tuple
    # One line for a person, including which other rooms share the format.
    about: str


SITES = (
    Site("ignition", ignition, names=False, reveals=True, rake=False,
         places=(r"%USERPROFILE%\Ignition Casino Poker\Hand History",
                 r"%USERPROFILE%\Bovada Poker\Hand History"),
         about="Ignition; Bovada and Bodog are the same network and format"),
    Site("acr", acr, names=True, reveals=False, rake=True,
         places=(r"%LOCALAPPDATA%\AmericasCardroom\handHistory",
                 r"%LOCALAPPDATA%\BlackChipPoker\handHistory",
                 r"%USERPROFILE%\Documents\AmericasCardroom"),
         about="ACR -- the Winning Poker Network, shared with Black Chip, "
               "YaPoker and True Poker"),
    Site("pokerstars", pokerstars, names=True, reveals=False, rake=True,
         places=(r"%LOCALAPPDATA%\PokerStars\HandHistory",
                 r"%LOCALAPPDATA%\PokerStars.EU\HandHistory",
                 r"%LOCALAPPDATA%\PokerStars.UK\HandHistory"),
         about="PokerStars -- the same text whichever licence the client is"),
)

KEYS = tuple(s.key for s in SITES)
BY_KEY = {s.key: s for s in SITES}

# A cash hand, whichever site and whichever of its formats. Every site names
# its fast-fold game differently -- Zone, Blitz, Zoom -- and two modules
# kept their own list of the names, so the third site's fast-fold hands
# were cash to one and invisible to the other. Tournament chips are not
# dollars, and that is the only line that matters here.
CASH = "fmt != 'MTT'"


def of(key):
    """The site behind a `site` value, or a message naming the real ones."""
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"no site {key!r}; the registry knows "
                       f"{', '.join(KEYS)}") from None


def named():
    """Sites where a label is a person -- the ones a player report is for."""
    return tuple(s.key for s in SITES if s.names)


def revealing():
    """Sites that show every hand, folds included -- the ones a pool is."""
    return tuple(s.key for s in SITES if s.reveals)


def sql_in(keys):
    """A `site IN (...)` clause, so callers never spell a key by hand."""
    return "site IN (" + ", ".join(f"'{k}'" for k in keys) + ")"


def stats(db_path=DB):
    """What the database holds, per site -- and what each site gives away."""
    if not Path(db_path).exists():
        print("no database yet -- `python importer.py <folder>` first")
        return
    con = sqlite3.connect(db_path)
    print("hands by site and format:")
    for site, fmt, bb, n in con.execute(
            "SELECT site, fmt, bb, COUNT(*) FROM hands "
            "GROUP BY 1, 2, 3 ORDER BY 1, COUNT(*) DESC"):
        print(f"  {site:10} {fmt:6} {('$%.2f' % bb) if bb else '-':>7}  {n:6d}")

    print("\ncoverage of what each site actually shows:")
    for key in KEYS:
        row = con.execute(
            "SELECT COUNT(*), SUM(s.cards IS NOT NULL), COUNT(DISTINCT s.label) "
            "FROM seats s JOIN hands h USING(hand_id) WHERE h.site=?",
            (key,)).fetchone()
        if not row[0]:
            continue
        print(f"  {key:10} {row[0]:7d} seats, {row[1] or 0:7d} with cards "
              f"({100 * (row[1] or 0) / row[0]:5.1f}%), "
              f"{row[2]:6d} distinct names")

    print("\nyour results, by site and stake:")
    for site, fmt, bb, n, profit in con.execute(
            "SELECT h.site, h.fmt, h.bb, COUNT(*), "
            "SUM(s.won - s.posted - s.invested) FROM seats s "
            "JOIN hands h USING(hand_id) WHERE s.is_hero=1 AND h.bb IS NOT NULL "
            "AND h.fmt != 'MTT' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"):
        profit = profit or 0.0
        print(f"  {site:10} {fmt:6} ${bb:.2f}  {n:6d} hands  ${profit:+9.2f}"
              f"  {100 * profit / bb / max(1, n):+8.1f} bb/100")
    con.close()


def check(db_path=DB):
    """
    Every site in the database proves its import, the same way.

    A hand history parser fails quietly. It does not crash on a line it
    misreads; it drops the line and every figure downstream comes out
    slightly wrong and entirely plausible. So an import is not believed
    because it ran -- it is believed because the money adds up, the button
    goes round, and the blinds are posted by the seats named as blinds.

    ACR had this check from the day it was loaded and it caught two real
    bugs before either reached a report. Ignition never had it, and the
    first time it ran against Ignition it found one: `Posts dead chip`, a
    returning player's dead post, was not in the parser's list of posting
    verbs, so the line fell through and four hands had money come out that
    never went in. Four hands is not a number that moves a report. It is
    the same bug as ACR's bare `posts $0.05`, on the other site, and the
    next one will not necessarily be four.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    fails = []

    # The registry and the database agree on what a site is, and every
    # parser offers what the loader will ask of it. A key in the database
    # that is not here would be a site nothing can describe.
    unknown = [r[0] for r in q("SELECT DISTINCT site FROM hands")
               if r[0] not in BY_KEY]
    print(f"sites in the database are registered   "
          f"{'yes' if not unknown else 'NO: ' + ', '.join(map(str, unknown))}")
    if unknown:
        fails.append(f"unregistered site values {unknown}")
    for s in SITES:
        lacking = [name for name in ("HEADER", "split_hands", "parse_hand")
                   if not callable(getattr(s.module, name, None))]
        if lacking:
            fails.append(f"{s.key}'s parser lacks {lacking}")
    print(f"every parser meets the contract        "
          f"{'yes' if not any('parser' in f for f in fails) else 'NO'}")

    for s in SITES:
        if not q("SELECT 1 FROM hands WHERE site=? LIMIT 1", s.key):
            print(f"\n{s.key}: no hands loaded")
            continue
        print(f"\n{s.key}")
        # Tournament chips are not dollars and a tournament history can
        # begin mid-hand, so MTT is out of anything involving money.
        cash = "h.site=? AND h.fmt != 'MTT'"

        # 1. Money. Every chip that went in came back to somebody, minus what
        #    the house took -- where the site says what it took. Where it
        #    does not, what went in must equal the pot the site declared,
        #    which is gross of rake on Ignition. Either identity catches a
        #    misread bet size, a missed call, a dropped post.
        rows = q(f"""SELECT h.hand_id, h.pot, SUM(s.won) w,
                       SUM(s.posted + s.invested) inp, h.rake,
                       COALESCE(h.rake,0) + COALESCE(h.jp_fee,0) house
                     FROM seats s JOIN hands h USING(hand_id)
                     WHERE {cash} GROUP BY h.hand_id""", s.key)
        # Which identity is the hand's to answer, not only the site's: the
        # site says what its client writes today, and a hand from an older
        # client may say more. Bovada wrote the rake in 2012 and does not
        # now, and one of FPDB's 2012 hands carries a summary from a
        # different hand entirely -- the stated pot is wrong and the money
        # is right, which only the stronger identity can tell.
        # Without the rake written, what came out can only be bounded: no
        # more than the pot, and no less than the pot minus any rake a
        # room takes. The lower bound was missing until 16 Sep 2026, and
        # seven hands in which hero's side pot was overwritten by the main
        # pot -- $44.80 recorded as $4.88 -- passed the check for a month.
        off = [r for r in rows
               if (abs((r["inp"] or 0) - r["house"] - (r["w"] or 0)) > 0.011
                   if r["rake"] is not None or s.rake else
                   r["pot"] is None or abs((r["inp"] or 0) - r["pot"]) > 0.011
                   or (r["w"] or 0) > r["pot"] + 0.011
                   or (r["w"] or 0) < 0.8 * r["pot"] - 0.011)]
        how = "in - house = won" if s.rake else "in = stated pot, 0.8 pot <= won <= pot"
        ok = 100 * (1 - len(off) / max(1, len(rows)))
        print(f"  money adds up           {ok:6.2f}%  "
              f"({len(rows) - len(off)}/{len(rows)} hands within a cent, "
              f"{how})")
        if ok < 99.0:
            fails.append(f"{s.key} money")
            for r in off[:5]:
                print(f"      {r['hand_id']}  in {r['inp'] or 0:.2f}  "
                      f"pot {r['pot'] or 0:.2f}  house {r['house']:.2f}  "
                      f"won {r['w'] or 0:.2f}")

        # 2. The button goes round. Over thousands of hands at a full table
        #    every seat is every position equally often. A skew means the
        #    rotation was read wrong, which would silently rewrite every
        #    positional stat.
        counts = q(f"""SELECT s.position, COUNT(*) n FROM seats s
                       JOIN hands h USING(hand_id)
                       WHERE {cash} AND h.n_players=6 AND h.standard=1
                       GROUP BY 1""", s.key)
        ns = [r["n"] for r in counts]
        spread = (max(ns) - min(ns)) / max(1, sum(ns) / len(ns)) if ns else 1
        print(f"  positions balanced      {100 * (1 - spread):6.2f}%  "
              f"(6 positions, {min(ns) if ns else 0}-{max(ns) if ns else 0} each)")
        if len(ns) != 6 or spread > 0.02:
            fails.append(f"{s.key} positions")

        # 3. The blinds. Whoever the button says is the small blind is
        #    whoever the history says posted it -- otherwise positions are
        #    one seat out. A returning player's dead post is a real post
        #    from a non-blind seat, so this is never exactly 100%: measured
        #    1.0% on ACR and 1.5% on Ignition, against the ~100% that
        #    positions read one seat out would show.
        bad = q(f"""SELECT COUNT(*) n FROM seats s JOIN hands h USING(hand_id)
                    WHERE {cash} AND h.standard=1 AND s.posted > 0
                      AND s.position NOT IN ('SB','BB')""", s.key)[0]["n"]
        total = q(f"""SELECT COUNT(*) n FROM seats s JOIN hands h USING(hand_id)
                      WHERE {cash} AND h.standard=1 AND s.posted > 0""",
                  s.key)[0]["n"]
        print(f"  blinds posted by blinds {100 * (1 - bad / max(1, total)):6.2f}%  "
              f"({bad} of {total} posts were dead posts from other seats)")
        # Judged by the interval, not the point: one dead post in nine is
        # 11% and says nothing, one in nine hundred says positions are
        # right. The fixture corpus is the small case and it is real.
        from stats import wilson          # stats imports this module
        _p, low, _high = wilson(bad, total) if total else (0, 0, 0)
        if low > 0.05:
            fails.append(f"{s.key} blinds")

        # 4. Identity, where the site has it. The point of a site with names
        #    is that names recur, so a profile is worth building; a site
        #    without them is not expected to, and is not asked.
        if s.names:
            seen = q("""SELECT s.label, COUNT(*) n FROM seats s
                        JOIN hands h USING(hand_id)
                        WHERE h.site=? AND s.is_hero=0 GROUP BY 1""", s.key)
            over = {k: sum(1 for r in seen if r["n"] >= k) for k in (30, 100, 500)}
            hands = q("SELECT COUNT(*) n FROM hands WHERE site=?", s.key)[0]["n"]
            print(f"  named opponents         {len(seen):6d}  "
                  f"({over[30]} with 30+ hands, {over[100]} with 100+, "
                  f"{over[500]} with 500+)")
            # Twenty regulars at a hundred hands is a claim about a
            # database of thousands; a few hundred fixture hands could not
            # hold them whatever the parser did, and are not asked.
            if over[100] < 20 and hands >= 2000:
                fails.append(f"{s.key} identity")
        else:
            print(f"  named opponents         none -- this site has no names")

    con.close()
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--stats" in argv:
        stats()
        return 0
    print(f"{'site':10} {'names':6} {'reveals':8} {'rake':5}  about")
    for s in SITES:
        print(f"{s.key:10} {'yes' if s.names else 'no':6} "
              f"{'yes' if s.reveals else 'no':8} "
              f"{'yes' if s.rake else 'no':5}  {s.about}")
    print()
    stats()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
