# poker_analysis

A poker tracker built from scratch, over our own hand histories from
**ACR** and **Ignition**.

Import hands, build a database, and ask it anything about any player or the
pool, filtered any way you like. It never tells you what is *correct*. It
tells you what people **do**.

## Scope, decided rather than drifted into

**The tracking and filtering half of a Hand2Note, and nothing else.**

  * **No HUD.** No overlay, no popups, no reading the table while you play.
  * **No solver.** Hand2Note contains none, and neither does this. A solver
    answers "what is correct"; a tracker answers "what happened". Mixing the
    two is how a tracker turns into a different product.

The solver modules that used to sit in this folder — `walk.py`, `leaks.py`,
`poptree.py`, `bestresponse.py`, `postflop.py` and the `gtowizard` package —
were that different product, and they are gone as of 5 Sep 2026. They worked
and they were expensive to build, so they are not lost: `git show
4927a11:walk.py` still has any of them, and `git checkout 4927a11 -- walk.py
gtowizard/` brings the set back. What they were doing was making this repo
look like two programs sharing a folder.

## Why build one instead of buying one

Because of what these two sites happen to give away, and the fact that no
commercial tracker holds both at once.

|  | Ignition | ACR |
|---|---|---|
| hole cards | **every player's, folds included** | hero's, plus showdowns (23%) |
| identity | none — a seat, and only within a session | **real names, persisting for months** |
| what it can measure | the pool's actual range | one named person's game |

Ignition shows you the whole deal. Not the hands people were willing to show
at showdown — which are not a fair sample of the hands they held — but every
folded hand too. That means a population's range at a spot can be *counted*
rather than inferred. Almost no site allows this.

ACR gives what Ignition cannot: a name that is the same person next
week, at another table, at another stake. 51 opponents here have 150+ hands.

**Ignition measures the population. ACR measures the person.** The
thing worth building — and the reason this is not a worse copy of software
that already exists — is using the first as the prior for the second.

## What is in here

```
sites.py        what each site is, decided once      --check
acr.py, ignition.py, pokerstars.py   the parsers, one per format
importer.py     find, identify and load hand histories   --check
        v
spots.py        one row per player per hand          --check
decisions.py    one row per decision, 50 columns     --check
lines.py        how the betting went, as a string    --check
strength.py     what the hand is against the board    --check
update.py       fast-forward from github, safely      --check
players.py      who each player is: reg, fish, unknown  --check
        v
stats.py        35 stats declaratively, plus your own   --check
query.py        ask anything, filtered any way       --check
app.py          desktop study / Statistics / Sessions  --check
gui.py          the same three surfaces as a page    --check
expr.py         Value/Cases/Opps over plain stats    --check
aliases.py      named (username, room) groups        --check
notes.py        player notes, marks, tags            --check
sessions.py     sit-downs: Won / Won bb, Today clock --check
opponents.py      what one opponent does differently   --check
population.py   what the pool does, split-half validated  --check
check.py        run every check, in dependency order
```

`query.py` is the one you will use most:

```bash
python query.py --pool --pot 3bet --street flop --ip
python query.py --hero --board mono --results
python query.py --player dblj32 --pos BTN --hands
python query.py --pot 3bet --ip --street river --define river_barrel3 --do bet
python query.py --site ignition --quick threebet --chart
python query.py --statistics --fmt cash --exclude-reg-vs-fish
```

## The database, as it stands

| | |
|---|---|
| hands | 22,165 (May 2025 – Aug 2026) |
| decisions | 173,549 |
| PokerStars / ACR / Ignition | 9,961 / 8,284 / 4,010 |
| named opponents with 100+ hands | 189 |

## Requirements

Python 3.11 and **nothing else** — standard library only, and the database
is one SQLite file. `build.py --check` asserts it stays that way.

## Quick start

```bash
python importer.py "path/to/your/hand/histories"     # any site, any mix
python check.py
```

The importer identifies each file by its header, loads it with that site's
parser, and derives every table. Which sites exist is `sites.py`; adding
one is a parser module and a registry entry there.

Then ask it something:

```bash
python stats.py --pool
python opponents.py
python opponents.py SomeOpponent
```

`USAGE.md` covers every command and what its output means.
`CONTRIBUTING.md` is the rules for changing any of it — read it before you
touch a derivation. `ROADMAP.md` is the run log: what each session set out
to do, and whether it did it. `the-augster.xml` is the operating framework
this project is developed under, and `CLAUDE.md` is the short version.

## What this is honest about

The binding constraint is **hands, not code**. 51 profileable opponents is
enough to rank a pool and not enough to know a person; a flop stat on 700
hands still carries a ±15 point interval. Every claim here gets sharper with
volume and with nothing else, which is why loading another session is often
worth more than another feature.

Every rate this project prints carries its `n` and a Wilson interval, two
rates are compared by an interval on their difference rather than by
whether their own intervals overlap, and a family of comparisons is charged
for its size. "No difference" and "not enough hands to see one" are printed
as different findings, because the second is usually the true one.
