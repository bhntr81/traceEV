"""
Hand histories from the rest of the world, held against our parsers.

A parser is proved by the money adding up on the files it was written
against, and that is the whole of what it is proved by. The files it was
written against are one week of one person's play, which is a thin idea of
what a site writes: PartyPoker's test corpus has a file named
`players.joining.leaving`, one named `silent.post.both`, one named
`extra.spaces`, each a real thing the site once did that broke a real
parser. Ours has never seen any of them.

FPDB -- the open-source tracker -- kept every such file for twenty-eight
sites as regression fixtures, and they are the nearest thing that exists to
a public test suite for hand-history parsing. They live outside this
repository, in `Desktop/hand_samples`, because they are the sites' output
contributed by FPDB's users under FPDB's licence and are not this
program's to redistribute; they are read from there and never copied in.

Two questions, both answered from the fixtures rather than from memory:

  * of the sites we parse, which fixture files parse completely, which
    partly, and which not at all -- loaded into a scratch database and
    held to the same money, positions and blinds proofs as real play;
  * of the sites we do not parse, which have the most fixtures to write a
    parser against -- the order the next parsers should be written in.

    python fixtures.py            what is there, by site, ours and not
    python fixtures.py --check    our parsers against every fixture they recognise
"""

import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

import importer
import sites

SAMPLES = Path.home() / "Desktop" / "hand_samples"
FPDB = SAMPLES / "fpdb-chaz" / "pyfpdb" / "regression-test-files"

# Site folders in FPDB's tree that will never need a parser here, and why.
# A room that is gone writes no new histories; a room that is not a room
# is a converter's output.
GONE = {
    "FTP": "Full Tilt closed in 2016",
    "Absolute": "Absolute Poker closed in 2011",
    "UltimateBet": "closed in 2011",
    "Everleaf": "network closed in 2012",
    "Boss": "Boss Media network closed",
    "Everest": "closed in 2020",
    "OnGame": "network closed in 2016",
    "PKR": "closed in 2016",
    "Entraction": "network closed in 2013",
    "Enet": "closed",
    "TheBigGame": "closed",
    "Cake": "became Winning (WPN); see acr",
    "Merge": "network closed in 2019",
    "PokerTracker": "a tracker's export, not a room",
    "Betfair": "one file, and the room moved to iPoker",
    "SealsWithClubs": "one file per format; SwC writes little",
    "Bovada": "the Ignition format; see ignition",
    "Winning": "the WPN format; see acr",
    "Stars": "see pokerstars",
}


# Fixtures a parser reads as well as the file allows, and why that is not
# all the way. Each is a fact about a file, not a gap in a parser, and is
# excluded from the proofs so that a real gap is never averaged away by it.
KNOWN = {
    "NLHE-USD-5-10-201511.concatenated.partial.txt":
        "two hands under one header, cut mid-summary and glued; refused",
}


def fixture_files():
    """Every text fixture under the cash tree, tagged with FPDB's site folder."""
    out = []
    if not FPDB.exists():
        return out
    for site_dir in sorted((FPDB / "cash").iterdir()):
        if not site_dir.is_dir():
            continue
        for f in sorted(site_dir.rglob("*.txt")):
            # Hold'em only; the derivations are hold'em-shaped throughout.
            # FPDB names the game in the filename, which is what makes the
            # corpus usable without opening every file.
            name = f.name.upper()
            if not (name.startswith("NLHE") or name.startswith("LHE")
                    or name.startswith("PLHE") or "HOLDEM" in name):
                continue
            out.append((site_dir.name, f))
    return out


def survey():
    """What is there, and whether we can read it, by FPDB's own site names."""
    files = fixture_files()
    by_site = {}
    for folder, f in files:
        row = by_site.setdefault(folder, {"files": 0, "ours": Counter()})
        row["files"] += 1
        key = importer.sniff(f)
        row["ours"][key or "-"] += 1
    return by_site


def show():
    if not FPDB.exists():
        print(f"no fixtures at {FPDB}")
        print("  git clone --depth 1 --filter=blob:none --sparse "
              "https://github.com/ChazDazzle/fpdb-chaz.git")
        print("  git -C fpdb-chaz sparse-checkout set pyfpdb/regression-test-files")
        return
    by_site = survey()
    print(f"hold'em fixtures under {FPDB}\n")
    print(f"{'FPDB folder':16} {'files':>5}  {'read by':18} note")
    todo = []
    for folder, row in sorted(by_site.items(), key=lambda kv: -kv[1]["files"]):
        read = ", ".join(f"{k}:{n}" for k, n in row["ours"].most_common()
                         if k != "-")
        unread = row["ours"].get("-", 0)
        note = GONE.get(folder, "")
        if not read and not note:
            note = "no parser yet"
            todo.append((row["files"], folder))
        print(f"{folder:16} {row['files']:5}  {read or '-':18} {note}"
              + (f"  ({unread} unread)" if read and unread else ""))
    todo.sort(reverse=True)
    print("\nnext parsers, by how much there is to write them against:")
    for n, folder in todo:
        print(f"  {folder:16} {n} files")


def check():
    """
    Our three parsers against every fixture they claim, in a scratch database.

    Claiming is `importer.sniff`, which reads the header -- so a fixture a
    parser recognises and then cannot read is exactly the case this exists
    to find. The proofs are `sites.check`'s own: money, positions, blinds.
    A file is "read" when every hand in it produced a row; "partly" when
    some did; a partial read is a silent drop of the kind this project is
    built against, and is a failure here whatever the money says.
    """
    fails = []
    if not FPDB.exists():
        print(f"no fixtures at {FPDB} -- see `python fixtures.py`")
        print("\nPASS (nothing to check)")
        return True

    claimed, known = {}, []
    for folder, f in fixture_files():
        key = importer.sniff(f)
        if key and f.name in KNOWN:
            known.append((key, f.name))
        elif key:
            claimed.setdefault(key, []).append((folder, f))
    print(f"fixtures our parsers claim   "
          + ", ".join(f"{k}:{len(v)}" for k, v in sorted(claimed.items())))
    for key, name in known:
        print(f"    known, not proved: {key:10} {name[:44]:46} {KNOWN[name]}")

    tmp = Path(tempfile.mkdtemp()) / "fixtures.db"
    partial = []
    for key, items in sorted(claimed.items()):
        site = sites.of(key)
        for folder, f in items:
            text = importer.read_text(f)
            blocks = list(site.module.split_hands(text))
            parsed = 0
            for block in blocks:
                try:
                    if site.module.parse_hand(block, source=f.name):
                        parsed += 1
                except Exception as e:           # a bug is a finding here
                    partial.append((key, f.name, len(blocks), parsed,
                                    f"{type(e).__name__}: {e}"))
                    break
            else:
                if parsed < len(blocks):
                    partial.append((key, f.name, len(blocks), parsed, "dropped"))
    print(f"files read completely        "
          f"{sum(len(v) for v in claimed.values()) - len(partial)}/"
          f"{sum(len(v) for v in claimed.values())}")
    for key, name, n, got, why in partial[:12]:
        print(f"    {key:10} {name[:52]:54} {got}/{n} hands  {why[:40]}")
    if partial:
        fails.append(f"{len(partial)} fixture files not fully read")

    # Then the money, on everything that did load. Loaded through the real
    # loader so the check is of what a user's import would produce.
    paths = [f for items in claimed.values() for _folder, f in items]
    importer.load(paths, db_path=tmp, progress=None)
    con = sqlite3.connect(tmp)
    n = con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
    con.close()
    print(f"hands loaded from fixtures   {n:,}")
    # And nothing a parser met that it did not understand. A verb it does
    # not know is a line of money it did not count, and it is dropped in
    # silence but for these tallies.
    unknown = {s.key: dict(s.module.UNKNOWN) for s in sites.SITES
               if getattr(s.module, "UNKNOWN", None)}
    print(f"verbs no parser knew         "
          f"{unknown if unknown else 'none'}")
    if unknown:
        fails.append("a parser dropped lines it did not understand")
    if n:
        print()
        ok = sites.check(db_path=tmp)
        if not ok:
            fails.append("the fixtures fail the site proofs")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    show()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
