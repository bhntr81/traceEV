"""
Run every module's check, in the order the tables depend on each other.

Each module already knows how to prove itself. What was missing was one
command that asks all of them at once, which matters because the failure
this catches is not "a module broke" -- it is "a module was rebuilt and the
ones downstream of it were not". A `spots` rebuilt after a schema change
with `decisions` left alone is a database where every table is individually
fine and the set of them is wrong.

    python check.py            run everything, exit non-zero if anything fails
    python check.py --quiet    just the verdicts

Not every module is here. The solver modules are out of the project's scope
and have no checks; that they sat broken for a whole run cycle without
anybody noticing is the argument for a module being in this list.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# In dependency order, so the first failure is the earliest cause rather
# than the loudest symptom.
CHECKS = [
    ("sites", "each site's import: money, positions, blinds, and names where it has them"),
    ("spots", "the per-hand derivation: figures with a known shape"),
    ("decisions", "the per-decision derivation, against spots exactly"),
    ("lines", "the betting written out, against the columns it must match"),
    ("strength", "what the hand is, against hands worked out by hand"),
    ("players", "who each player is, and whether the classes survive a split"),
    ("sessions", "every hand you played is in exactly one sitting"),
    ("stats", "the stat engine, against spots where they overlap"),
    ("opponents", "opponent profiles: deviations really clear their intervals"),
    ("importer", "the site detector, against hands already loaded"),
    ("equity", "the hand evaluator, against published equities"),
    ("query", "the filter surface: every filter runs, and every one narrows"),
    ("diag", "a failure in any of the three places reaches the log"),
    ("update", "the updater reports honestly and can only fast-forward"),
    ("app", "the window, its filter dialog, and the dark theme"),
    ("gui", "the page and the command line build the same filter"),
    ("population", "pool findings survive being split in half"),
    ("notes", "marked hands and player notes: round trips, and nothing rebuilds them away"),
    ("ask", "the assistant's vocabulary covers every flag, and its tool only reads"),
]


def run(module, quiet):
    proc = subprocess.run(
        [sys.executable, str(HERE / f"{module}.py"), "--check"],
        capture_output=True, text=True, cwd=str(HERE))
    out = (proc.stdout or "") + (proc.stderr or "")
    if not quiet:
        print(out.rstrip())
    return ("PASS" if proc.returncode == 0 else "FAIL"), out


def main(argv):
    quiet = "--quiet" in argv
    results = []
    for module, what in CHECKS:
        if not quiet:
            print(f"\n{'=' * 70}\n{module}.py -- {what}\n{'=' * 70}")
        verdict, _ = run(module, quiet)
        results.append((module, verdict))

    print(f"\n{'=' * 70}")
    for module, verdict in results:
        print(f"  {verdict:5} {module}")
    bad = [m for m, v in results if v == "FAIL"]
    print()
    if bad:
        print(f"FAIL: {', '.join(bad)}")
        print("Fix the earliest one first -- the later ones read its tables.")
        return 1
    print(f"PASS: all {len(results)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
