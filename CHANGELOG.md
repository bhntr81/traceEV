# Changelog

What changed, and why. Bugs get their own entries when the bug is worth
remembering — most of the ones here produced no error and no crash, only
wrong numbers, which is the failure mode this project is built against.

Newest first.

---

## The parsers against twenty years of files

### Fixed -- money that was wrong in the database, and passed the check

Three real bugs, found by running the parsers over FPDB's regression
corpus and then over your own files with the corrected code, comparing
every seat's money with what the database held:

- **Ignition, a hand with a side pot.** "Hand result-Side pot $44.80" and
  "Hand result $4.88" to the same seat; the second *set* `won` instead of
  adding. Seven hands in the database had hero winning the main pot alone.
  `won +=` now, and the same fix in `acr.py`, where a hand run twice has a
  summary per board and a seat that won both was recorded as winning the
  second.
- **Ignition tournaments.** The tournament client writes "Call 20" for
  the ring client's "Calls $0.25", "Fold(Blind Disconnected)" for a
  disconnected player's forced fold, and "Hand Result" with a capital.
  All three were verbs the parser did not know and dropped: 391 calls, 367
  folds, 137 results in one folder. A fold not recorded is a seat that
  never folds. 749 decisions came back on the re-read.
- **Ignition, an ante for the last of a stack.** "All-in 20" before the
  hole cards is the ante, not a shove; it was an action. Now a post, like
  anything before the deal.

### Why the check let them through

The Ignition money identity was `in = stated pot, won <= pot`, because the
history does not write the rake. A seat recorded as winning a tenth of the
pot satisfies it. The identity now has a floor: `0.8 pot <= won <= pot`,
which those seven hands fail and every real hand passes. And where a hand
*does* write the rake -- the 2012 Bovada client did -- the stronger
identity `in - house = won` is used for that hand whatever the site's rule
is, because one FPDB fixture carries a summary from a different hand: the
stated pot is wrong and the money is right, and only the stronger identity
can tell.

### Added -- `fixtures.py`, `importer.py --reread`, and the tallies

`fixtures.py --check` runs the three parsers over every FPDB fixture they
claim -- fifty-one files, 971 hands, 2005 to 2023 -- and requires every hand
read, no verb dropped, and the site proofs to hold. Each parser now keeps
`UNKNOWN`, a tally of the verbs it met and did not know, and the check
reads it; a parser that drops a line silently is the failure this project
is built against and this is the first time it has been audible.

`importer.py --reread <paths>` drops the hands the files hold and loads
them again, for exactly this case: a refresh skips known hands and would
have left the seven wrong for ever.

### Fixed -- formats the parsers did not know

From the fixtures: PokerStars "Game #" (before 2011), "Home Game Hand #",
euro and pound tables, play money, tables with no "-max", the archive
export that indents every line under a rule of asterisks, and the 2005
client's habit of handing an uncalled bet back with no line for it (only
then, only when one seat collected). Bovada and Bodog headers on the
Ignition parser; the 2012 client's "Ante/Small Blind", "Big blind/Bring
in", one-figure raises that name the street total, boards dealt as "Card
dealt to table" with no street markers, and a written rake. Format and
stakes from the hand when the filename has lost them. ACR "caps $0.55" on
a Cap table. A hand run twice, on both sites: first board recorded, the
second's markers skipped. UTF-16 files read. A block with a seat number
listed twice -- two hands glued under one header -- refused rather than
read as one.

`sites.py --check` judges its blinds test by the Wilson interval rather
than the point -- one dead post in nine says nothing -- and asks the
identity test only of a site with two thousand hands, so the proofs mean
the same on a fixture corpus as on a database.

---

## The assistant is scored, and comparisons go through the tool

### Added -- `golden.json`, `ask.py --score`

Fifty-one questions with the command that answers each. `--score` asks
every one cold and marks it right if any query the model ran gives the
same answer -- the same chances and cases for the stat asked, by
`ask.same_answer` -- not the same flags. A wider table than wanted is
right; a narrower one is not; an entry may list alternatives. The first
run scored 32 of 41: five misses were hold-questions sent to PokerStars,
which shows a quarter of its hands, and the vocabulary now says which
site answers which kind of question.

### Changed -- the prompt

A comparison is one query with `--versus` and `--show`, and the model may
call a difference real only when the tool's verdict line does. The first
answer the assistant ever gave compared two rates by whether their
intervals overlapped, which is the test this project retired on 5 Sep.

---

## Ask the database in English

### Added -- `ask.py`, and a chat panel in the window

The model is given the program's vocabulary, generated from `query.py`,
and one read-only tool. Every number in an answer came from the tool and
the command is printed under it. Providers: Gemini, Claude, OpenAI, Grok,
and the Claude Code command line; `--mcp` serves the same tool to the
Claude Desktop app. Keys in `ai.json`, which is gitignored. Carried over
from a parallel session and consolidated here.

---

## The program is TraceEV

Everything that showed a name -- the window's title and heading, the
crash log, the packaged build, the page `gui.py` serves, the two front
documents -- says TraceEV now, which has been the repository's name on
GitHub since it was made. The folder on the desktop is still
`poker_analysis`; renaming it is a thing to do with every session closed,
and the name inside it does not depend on it.

---

## Table composition: which side of you the fish sits

### Added -- `fish_left`, `fish_right`, `reg_left`, `reg_right`

Four columns on `decisions`, stamped by `players.py` beside `n_fish` and
`n_reg` from the same liveness walk: the company still in the pot, split
by whether each seat acts after this player in the postflop order (left,
with position on them) or before (right). Four switches -- `--fish-left`,
`--fish-right`, `--reg-left`, `--reg-right` -- on the command line and
in the window's dialog. "How do I do with a fish on my right" is the
question table selection is supposed to answer, and it is now one flag.

The order is the seats clockwise from the button, read from `spots`;
heads up the small blind is the button and stands in for it. Two
identities are checked on every row: the sides add up to the company,
and the button with everybody still in has nobody on its left.

On this database hero runs +15 bb/100 with a fish on the right and +19
with one on the left, each ±22, against +0.5 ±10 with only regs left --
a gap that is probably real and not yet seen, which the numbers say
rather than the prose.

---

## Aliases, and export

### Added -- aliases

`python notes.py --alias pokerstars old_name the_reg`: two names on one
site are one person. Applied in `spots.identify` and nowhere else, so
after a rebuild every derived row under the alias carries the player's
name and no view has to know an alias exists -- which keeps "only
`spots.identify()` decides who a player is" true. Chains flatten when
written; loops are refused; `importer.py --rebuild` is the new command
that makes it take effect. Within a site only, because a profile is
measured against its site's pool.

### Added -- `--export`

`python query.py <filter> --export file.txt` writes the hands a filter
selects as hand histories, as the sites wrote them, read back out of the
files they came from. A hand whose file has moved is counted and named
rather than left out. `importer.py --check` now exports ten hands and
loads them into a fresh database, and they must all come back.

---

## Marked hands and player notes

The two things a tracker remembers for you, and the next two on the
manual's list after sessions. `notes.py`: a tag is a word on a hand, a
note is text on a player.

### Added -- `--tag`, and the hand id in every listing

`python notes.py --tag <hand id> review` marks a hand; `--tag review` is
then a filter over `decisions` like any other, so "the hands I marked, on
the button, in 3bet pots" is one command, and the window's filter dialog
has a box for it. The hand listings print the hand id now -- the window's
had no id at all, so a hand seen there could not be marked from the
command line -- and any tags beside it. The terminal and the window share
one query for the listing (`query.hands_of`); each had its own copy.

### Added -- the replayer marks and annotates

The hand window has a tag bar along the top and, when the seat it was
opened for is a person, a note box that writes straight to the database.
Every seat's note is shown beside it in the replayer and
`opponents.py NAME` prints it above the numbers. A note is keyed by site
and player, because a name on PokerStars is not that name on ACR, and an
Ignition seat gets no box: it is nobody after the session, and a note on
it would attach to the next stranger in the chair.

### Where they live

In `hands.db`, in tables of their own, created on demand the way `meta`
is. They are about hands and players, so a file beside the database would
drift from it at the first re-import; and they are the user's, so the
derivation never touches them -- `notes --check` asserts that the one
DROP in the chain names only `decisions`. A backup of the database is a
backup of these.

### Also

`sessions.py` had never been added to the packager's module lists.
`importer.rebuild` imports it dynamically, which PyInstaller cannot see,
so a packaged window would have failed at "deriving sessions" on its
first import. Listed now, with `notes`.

---

## The pool report onto the engine, and the last hardcoded module is gone

`population.py` was the sixth and last module with its own SQL. Every
number in it now comes from the engine: a spot is a registry key, a rate
is `stats.rate`, the money is `query.results_of` over `matching_seats`,
the chart is `query.chart_of`, and the split-half cut is
`stats.split_point`. The module contains no query.

### What it cost to have been separate

Its cbet rate came from `spots.cbet_chance`, a column that gives the
raiser a cbet chance when they had been bet into. `stats.py --check` had
that on record as a KNOWN discrepancy -- 60.6% on the engine against
56.0% from the column -- and the pool report went on printing the
column's figure, because nothing connected the two. On the engine it
reads 59.6% for the same pool. Everything else agreed within a point,
and the split-half check finds the same 22 findings holding either way.

### Added — `call_open`, and a measured error bar

The report's "cold call" was every seat that called an open, blinds
included; the engine's `coldcall` is the standard one -- first action,
not from a blind -- and reads 16.7% where the report read 24.9%. The
report's meaning is now a stat of its own, `call_open`, so it is a
column, a split and a filter like any other.

`query.results_of` now returns the error on bb/100 measured from the
hands it summed, in place of the constant 1170/sqrt(n) that assumed every
line's hands have an 11.7bb standard deviation. The leak map sorts lines
by whether their loss clears twice their own error, and a line that is
always a fold has almost none while a line that is always a shove has
three times that; the constant made both wrong in opposite directions.
`query.py --results` prints the measured figure too.

### Fixed — the pool check could not fail

`population.py --check` printed FAIL and exited 0, so `check.py` would
have stayed green through a pool that fell apart. The verdict is the
exit code now.

---

## Sessions, and the opponent report onto the engine at last

### Added — `sessions.py`: when you sat down, when you got up

Three of the nine worked examples in Hand2Note's own manual are session
questions — morning or evening, whether the fourth hour is worth playing,
whether six tables is too many — and none of them could be asked here,
because nothing knew where one sitting ended and the next began.

A session is a run of your own hands on one site with no gap longer than
ten minutes (Hand2Note's rule and default). On this data the threshold is
almost irrelevant: 21,171 of the gaps between consecutive hands are under
two minutes and 53 are over an hour. **83 sessions over 22,150 hands, 65 of
them fifty hands or more, spread over one to nine tables at a time.**

Four columns on `decisions` — the sitting, minutes into it, its length, and
how many tables were dealing you hands at that moment — and five filters,
five `--by` dimensions, a `--sessions` view and a tab.

```
python query.py --hero --results --by hour
python query.py --hero --hour 18-23 --session-len 120-300 --stats
```

Two rules, both stated on every view that touches them. **Sessions are per
site**: `played_at` is whatever clock the site wrote, and nothing says two
sites agree, so sittings are never spliced across sites by timestamp. And
**the hour is the site's hour**, not yours.

The check is the arithmetic: hands in sessions equal your hands
(22,150/22,150), money in sessions equals your money to the cent, no
sitting holds a gap longer than the rule (longest 9.3 min), no two sittings
on a site overlap.

### Fixed — `opponents.py` took five minutes and used the wrong test

Its check re-derived every reported deviation with two full-table queries
each. At one site that was tolerable; at three sites and 1,217 opponents it
was **275 seconds of CPU and still running**, which made the whole suite a
thing nobody runs. And it still decided a deviation by whether two intervals
overlap — the test CLAUDE.md retired on 5 September because it behaves like
a test at the 99% level.

Both fixed together, which is goal 3 of the roadmap. One pass per stat over
every player (`rates_by_player`, which it already used for the leaderboard
and not for the check), the interval on the *difference*, and Holm per
player because thirty-six stats are asked about each. The baseline is now
the pool **without the player in it** — a regular with two thousand hands
is enough of the pool that leaving them in shrinks every difference they
have. The check re-derives a sample of forty reads independently rather
than all 214.

**2m13s, 214 reads across three sites, 40/40 re-derived.**

`show()` also still told hero to run `leaks.py` and `poptree.py` against
the solver. Those were deleted nine days ago.

### Also

* The Filter dialog's General tab has a "when you were playing" row.
* `importer.CHAIN` and the check that enforces it know about `sessions`,
  so an import rebuilds them and a rebuild that skipped them would fail.

---

## PokerStars, the third site -- one parser and one registry entry

9,961 hands of NL100 6-max from `Downloads
l100stars.txt`, loaded the
day after the registry was built, and the claim held: `pokerstars.py`
providing `HEADER`, `split_hands` and `parse_hand`, one `Site(...)` in
`sites.py`, and nothing else in the program touched. The database is
22,165 hands and 173,549 decisions; PokerStars brings 431 named
opponents, 104 of them with 100+ hands and 17 with 500+, which is more
people to profile than ACR had.

The money check earned its keep twice before a single hand was loaded:

  * **"raises $1 to $2" is not "added $1".** On PokerStars the first
    figure is how much the bet went UP by; a player with nothing in yet
    put in the whole $2. WPN writes the added amount first and `acr.py`
    takes it as it comes; the same reading here left every open a big
    blind short and the identity at 0% on the first hand it saw. The
    parser keeps a per-street ledger and a raise adds its total less what
    the seat already had in.
  * **All-in Cash Out.** A player who takes it is paid by the house, the
    "collected" line never appears, and the summary says the pot was "not
    awarded". Eight hands. The pot still went where the cards said and
    the summary names the amount, so it is read from there when the body
    gave nobody anything.

Two checks failed on the three-site database, and both were the checks'
expectations rather than the derivation:

  * `decisions --check` expected every player who saw a flop without a
    flop decision to have been all in preflop. A limped PokerStars pot
    produced the other way: the small blind open-folded on the flop and
    the big blind collected without ever having to act. The check now
    accounts for both. It also kept its own list of fast-fold format
    names -- `fmt IN ('RING','BLITZ','ZONE')` -- as did `stats.POOL`,
    which is a site fact spelled by hand; both now use `sites.CASH`,
    which is "not a tournament" and nothing else.
  * `opponents --check` re-derived every deviation against the pool of
    all named sites together, while the report measures a player against
    their own site's pool. The same thing while ACR was the only site
    with names; 29 of PokerStars' 430 deviations "wrong" once there were
    two. The check uses the report's baseline now.

Zoom hands are `fmt='ZOOM'`; a name persists across them, so a Zoom seat
is still a person. The file was a single export, so the client's folders
under `%LOCALAPPDATA%\PokerStars*\HandHistory` are registered on trust
and will be confirmed the first time the client writes to one.

---

## A site is a fact in one place, and the third site costs a parser

The decision that prompted this: ACR stays, and the tracker should cover as
many sites as Hand2Note does. The codebase knew about exactly two sites by
name -- "ignition" or "acr" spelled out in 38 places across 14 modules --
and the third site would have been the moment that stopped working. Not
loudly: the way `fmt='RING'` stopped naming one pool when ACR arrived and
four modules kept averaging two pools into a number describing neither.

### Added — `sites.py`

One entry per site: the parser module, whether a label is a person,
whether folded hands are shown, whether the history states the rake, where
the client keeps its files, and a line for humans. Everything that used to
spell a site's name now asks: `spots.identify` asks `Site.names` to decide
who a seat is, `players.durable` carries the same answer onto each row,
`population.POOL` is `sites.revealing()`, `opponents` and `stats` run per
`sites.named()` and per `sites.KEYS`, the window's Site menu and the
import summaries read the registry, and `build.py` derives the parser
module list from it rather than keeping a second one.

What still spells a site by name: three test fixtures that feed a literal
`--site acr` to the filter parser, and the migration default for a
database written before the column existed. Data, not facts.

### Changed — a parser is a parser, and the loader is one loader

Each parser carried a private copy of the loading loop with its own
`INSERT`, and the two had drifted: Ignition's wrote fewer columns, Ignition
read `fmt`, `sb` and `bb` from the filename inside `build` while ACR read
them from the text inside `parse_hand`, and both had the same `files`
counter bug on the same line. `importer.load` is now the only place a row
is written; the schema and `migrate` moved there with it. A parser
provides `HEADER(line)`, `split_hands(text)` and `parse_hand(block,
source)` and nothing else -- `acr.py` and `ignition.py` lost `build`,
`stats`, `check`, `migrate`, `SCHEMA` and their command lines.
`python importer.py <folder>` is the one command; `python sites.py
--stats` is what `acr.py --stats` was.

Proved two ways, on purpose separately. Every file on disk loaded through
the new loader gave `hands` / `seats` / `actions` byte-identical to the
old loaders' (12,294 hands). Then the whole derivation chain rebuilt on a
copy of the live database gave `spots`, `bets`, `decisions` and `players`
identical to the live ones. So the refactor changed nothing -- which is the
only claim a refactor gets to make, and the one that has to be shown
rather than said.

### Added — every site proves its import the same way

`sites.py --check` replaces `acr.py --check` and generalises it: money,
positions and blinds for every site loaded, names recurring for the sites
that have names. ACR had these tests from the day it was loaded and they
caught the jackpot fee and the bare `posts`. Ignition never had them.

The money identity differs by site and the registry says which. ACR
writes its rake, so `in - house = won` (8,277 of 8,284). Ignition never
does, but its `Total Pot(...)` is gross, so `in = stated pot` and
`won <= pot`. MTT is out of the money test: chips are not dollars, and a
tournament history can begin mid-hand. The blinds threshold is 5% rather
than 2% because a returning player's dead post is a real post from a
non-blind seat -- measured 1.0% on ACR and 1.5% on Ignition -- and the
failure the test exists for, positions read one seat out, shows as ~100%.

### Fixed — Ignition dropped the dead post

The first time the money test ran against Ignition it found the thing it
exists to find. `UTG+1 : Posts dead chip $0.15` -- a returning player's
dead post -- was not in `ignition.POSTS`, so the line fell through the
verb parser and was dropped without a sound, and four cash hands had money
come out of the pot that never went in. It is ACR's bare-`posts` bug on
the other site, uncaught for exactly as long as there was no check. Four
hands does not move a report; that is luck, and the next one need not be.
`ignition.py` knows the verb, the four `seats.posted` values are
corrected, and Ignition's money is 3,892 of 3,892.

### Documents

`ROADMAP.md` opened by saying it was the archived log of a different
project -- the header from the folder it was copied out of -- and carried
the CEO plan twice. Fixed, and Part III added: the multi-site vision,
where Part II's plan actually ended up (steps 8 and 9 cut on 5 Sep, step
10 arrived unplanned), the site contract, and this run. `README.md` still
advertised 1,510 cached solver nodes and a Playwright requirement, and
described the interval-overlap rule retired on 5 Sep; `CONTRIBUTING.md`
had the same rule and the old "write a loader" advice for a new site;
`CLAUDE.md` had a PAF about solver nodes that no longer exist. All brought
up to date, and the registry is now a PAF.

### Not done

`opponents.py` still decides a deviation by whether two intervals overlap,
which `stats.difference` replaced everywhere else on 5 Sep. `USAGE.md`
describes it accurately as it stands. It is the next thing.

---

## Importing hands, which had been broken in three places at once

The next thing on the list was auto-import. Before writing any of it the
existing path was run, and it did not work -- in three separate ways, none
of which any check would have caught, and all of which had been true for as
long as the two-site importer had existed. 265 hands were sitting on this
machine unable to get in, and the database's newest hand was twelve days old.

### Fixed — every import raised, because a counter and a parameter shared a name

`importer.load` hands each parser an explicit list of files. It has to: one
folder holds both sites, and each file has to go to the parser that wrote
it. Both parsers took that list as `files=` and then began

```python
added = skipped = files = 0
```

which overwrote the list with 0 before the loop could read it -- and 0 is
not None, so the loop tried to iterate the number zero. `python importer.py
<folder>` and the window's whole Import menu raised `TypeError: 'int' object
is not iterable`. Only calling a parser directly on a folder worked, which
is the one path a person is least likely to use.

The counter is `n_files` now. The comment above it carried a stray form feed
where the "f" of "files" should have been, which is the fingerprint of
whatever edit did this.

### Fixed — the rebuild ran two stages out of five

`importer.rebuild` derived `spots` and `decisions` and stopped. But
`decisions.build` begins by **dropping the table**, and `lines`, `strength`
and `players` each ALTER their columns back onto it afterwards. So an import
did not leave those columns stale -- it deleted twenty-two of them. Measured
on a copy of the live database: `node`, `line`, every per-street string,
`made`, `kicker`, `fd`, `sd`, `player_class`, `vs_class`, `n_reg`, `n_fish`,
`vs_player`, `vs_seat`, all gone, and every filter that reads one raising
"no such column" until somebody thought to run three more modules by hand.

The indexes went the same way. They are not in `decisions.SCHEMA`, so the
rebuild dropped them and thirteen filters went back to reading all ninety
thousand rows.

`importer.CHAIN` is now the build order as a list -- spots, decisions,
lines, strength, players, then the indexes -- and `rebuild` walks it. A list
because the failure is a stage being left out of one.

### Fixed — ACR could not create a database

`acr.build` called `migrate`, which only ALTERs. Against a database that did
not have the tables yet it asked SQLite to add a column to `hands` and got
"no such table". Ignition's loader has always run the schema first; both
write the same tables, so both must be able to make them. An ACR-first
import into a fresh database -- which is what this machine would be, holding
three hundred ACR files and one Ignition folder -- could not work.

Found by the new check, not by reading.

### Added — `--refresh`, and the window noticing on its own

```bash
python importer.py --refresh
```

Scans the usual places, loads what is new, and rebuilds **only if a hand was
actually added**: the derivation is three quarters of a minute, and running
it to discover nothing changed is how a refresh button becomes one nobody
presses. In the window it is **Import → Import new hands**, with no dialog,
because the answer to "which folder" is always the same one.

The window also says on launch when there is something to fetch. That check
is a heuristic on file modification times -- a file touched after the newest
hand in the database might hold new hands, one touched before it cannot --
because the honest version means parsing three hundred and seventy files
while the window is trying to open. It over-reports and never under-reports,
which is the right way round for something whose only consequence is an
offer.

It only notices. Importing stays a thing you ask for: a minute of derivation
starting by itself while somebody is reading a number is not a feature.

### Fixed — the notice would have nagged for ever

The first version of that heuristic compared file times against the newest
hand's `played_at`, and running it on the real database showed why that is
wrong: all 265 recovered hands were **older** than hands already held --
they were backfill the broken loader had never been able to read, not
recent play. So `played_at` did not move, those thirty files stayed "newer"
than it after being loaded, and the window would have offered to import
them on every launch for ever. A notice that says the same thing every
launch is one nobody reads, which is the argument the update banner already
makes about itself.

The loader now records how far it has read -- the newest modification time
among the files it has been through -- in a `meta` table of its own,
created on demand so that `decisions.build` dropping its table cannot take
it. Recorded in `load` rather than in `refresh`, so that Import a folder and
Find hands on this computer mark it too; they read the same files, and
would otherwise leave the window still offering them. Checked: loading a
file leaves the mark on that file's time.

### Not done — incremental derivation, and the measurement that says why

The plan had been to derive only the new hands. The full chain was timed
first: spots 7.6s, decisions 17.8s, lines 8.0s, strength 5.7s, players 7.1s
-- about 46 seconds at 12,000 hands, and the whole chain reproduces the live
database exactly, which is now checked. Incremental derivation is
worth building when that number hurts. It does not yet, and four of those
five stages are per-hand while `players` is inherently global -- a player's
class changes for every one of their old rows when new hands arrive -- so
the incremental version would be four fast paths and one full pass anyway.

### Checked

`importer.py --check` tested that the site detector was right and never
tested that anything loaded, which is the whole difference. It now loads a
real file of each site into a database of its own and counts what arrived --
the check that finds all three bugs above. Beside it: every module that
writes columns onto `decisions` is in `CHAIN` and after `decisions`, its
columns are present, and `decisions.SCHEMA` still drops the table, so the
reason the order matters is asserted rather than remembered.

---

## Multiple Players, and two ways its parser removed somebody else's filter

### Added — a cohort: filtering by which players, before which spots

Every filter in this program narrowed situations. This one narrows people:
`--cohort --hands ">=500" --site acr` selects the players first, materialises
them into a temp table, and every view then joins against it. In the window
it is the **Players** button. Hand2Note calls the same thing Multiple
Players, and it is the difference between "how does the pool play this" and
"how do the eight people I keep sitting with play this".

The feature had been written and left uncommitted, undocumented and — this
being the part that mattered — **unchecked**. No `check()` in any of the
four modules that could have tested it mentioned it. `check.py` passed
sixteen out of sixteen around it.

### Fixed — the parser filtered a positional list by value

`parse_cohort` collected the tokens it had consumed into a **set of
strings** and then dropped every argv entry equal to one of them. That is
not argument parsing, and it removed things nobody had asked it to remove:

```bash
python query.py --cohort --hands 6 --players 6      # --players needs a value
```

Both sixes matched a consumed token, both were deleted, and `--players` was
left dangling. The same mechanism silently strips any legitimate filter
whose value happens to collide with a cohort value -- and a filter that
quietly disappears is the failure this project is built against, not the one
that raises.

It also made the hands view unreachable under a cohort. `--hands` is a
player's hand count to the cohort and the name of a view to `query.py`, so
`--cohort --hands ">=500" --hands` deleted **both** copies and the view
could never be asked for.

The parser now walks the list one token at a time and reads each of its
flags exactly once, the first time it appears. Every later copy passes
through untouched, which is the only reading under which that command means
what it plainly means.

### Fixed — the heading did not say which players

`--cohort --hands ">=500" --site acr --pos BTN` printed

```
filter: pos BTN, cohort (8 players)
```

over a report that had also been cut to one site and to players with five
hundred hands. The numbers were right -- the cohort join carries the site --
but eight players is the same eight whatever picked them, and a report whose
heading does not say what was filtered is one that will eventually be read
as though it covered everything. `describe_cohort` now names the filter and
both front ends print it.

### Fixed — two traps rather than bugs

`cohort()` selected `SELECT *` and `select_cohort` read the first two
columns by position, which made the `players` schema a promise it did not
know it was making: a column inserted ahead of `site` would have filled the
cohort with hand counts, matched nothing, and raised nothing. The columns
are named in the query now, so a schema change breaks loudly.

`select_cohort` also created its temp table without `IF NOT EXISTS`, so a
second call on one connection raised. Nothing does that today because every
view opens its own connection -- which is what makes it a trap: the first
caller to reuse one would meet it and would have no reason to look here.

### Checked

`players.py --check` now covers the cohort, and covers the argument
splitting first because that is where the silence was. Both failures above
are in it as cases, with the command that produced them. Beside them: a
condition that is not a comparator and a number is refused (three ways,
including the two that look like SQL), a cohort actually narrows rather than
quietly selecting everybody, and the heading names the filter and not just
its size.

---

## The chart, and a report that remembers what you asked

### Added — the 13x13 chart, over the whole filter vocabulary

`range_of` already answered the postflop half of "what does he have" -- top
pair, a flush draw, how much of it cannot call. The preflop half is a shape
and not a list, and there was no way to draw it. `--chart` draws it.

Two charts out of one function. With no stat it is the range's
**composition**: each combo's share of the hands that reached the spot,
which is the diagram H2N draws. With `--show` it is that stat per combo --
`--pos BTN --chart --show rfi` is a button opening range read off the hands
themselves rather than assumed. In the window it is a tab with a *chart of*
box, drawn on a canvas rather than tabulated, because a range is a shape and
169 numbers in rows is the same information in the one form nobody can read.

The Ignition pool's 3-bet range comes out as AKo 5.6%, AQo 5.1%, JJ 4.5%,
AA 3.9% -- which is what a 3-bet range looks like, and is the first thing
this program has drawn that can be checked against knowing how poker works.

**Composition counts each player-hand once and not each decision.** This is
the whole correctness of the function and the one way it could have been
wrong without looking wrong: a hand that reached the river has four rows in
`decisions` and one that folded preflop has one, so counting rows would draw
the chart from a population weighted towards the hands that went furthest --
a range visibly stronger than the one that actually arrived, in a picture
nobody would think to doubt. `query.py --check` asserts the cells sum to the
number of player-hands seen, over three filters including one that spans
streets.

The seen-fraction is printed above every chart, terminal and window. On ACR
a chart is of the hands that got to showdown, and 169 confident squares
drawn from a quarter of a range is the most convincing wrong picture this
program can produce.

`population.py` has drawn the same shape for months from its own hardcoded
SQL over `spots`, pinned to the Ignition pool. This one is over `decisions`
and takes every filter, so that copy is the next one to retire -- the
measure of progress being that the number of modules writing their own SQL
goes down.

### Added — a filter, saved as a report

`SMART_REPORTS` was five reports this project guessed at. A saved filter
goes into the same namespace, so it is a preset: `--preset`, `--presets` and
the window's report box all pick it up without being told, exactly as a
saved stat becomes a column and a `--quick` filter. `--save` names one,
`--forget` takes either a saved stat or a saved report, and refuses rather
than guessing if a name is somehow both.

Two things a report does not keep, and both would be quietly wrong:

**The reporting options.** `--by`, `--show` and `--min` say how to draw an
answer, not which rows it is about. A report that remembered `--show
vpip,pfr` would rewrite the columns of every view it was opened in, which
reads as the window forgetting what you asked rather than as the report
having an opinion. `situation_only` strips them before anything is written.

**The cohort.** A cohort chooses PEOPLE and a report describes a SITUATION
-- the same line already drawn for saved stats. It is also the wrong shape:
presets are expanded after the cohort flags have been taken off the command
line, so a saved one would arrive too late to be read at all.

Saved reports are proved to still build every time they are loaded, not
trusted. A flag renamed in `query.py` would otherwise turn every report that
used it into a stack trace over somebody's window; the ones that no longer
build are named in `UNREADABLE`, printed by `--presets`, and the rest still
work.

### Changed — one word for one thing

The window's tab, the flag and the function had drifted into calling the
same 13x13 picture a chart and a grid. Renamed to `chart` throughout, which
is the whole of the reason three front ends over one database stay agreeing.

### Checked

`query.py --check` gained the composition assertion above, that the chart
has 169 squares and that every combo the database contains has one of them,
and the saved-report round trip -- saved, read back identical, options
stripped, a built-in name refused, forgotten -- against a file of its own.

`app.py --check` draws every view including the chart, asserts the report
window would save the filter on the screen, and asserts the report box
offers every report that exists. A report saved to the file and missing from
the box is indistinguishable from a save that failed.

---

## A stat you can define without being able to edit the code

### Added — any filter, saved as a stat of its own

Hand2Note's manual has a page on building "continued bet on the river in a
3-bet pot in position": click the actions street by street, name the result,
press *Test Stat*, and it becomes a column. Nothing here could do that. The
registry in `stats.py` is a list in a Python file, so a stat this project
had not thought of was a stat you could not have without editing the
project — and the thirty-five in there will never be the right
thirty-five, because the number of spots a hand can be in is the number of
strings the action letters spell.

The question was already answerable. `--node "*/XBC/XBC/X" --pot 3bet --ip
--headsup --street river` selects exactly those decisions and always did.
What was missing was the *name*, and the name is not decoration: `--show`,
`--by position` and the opponent leaderboard all pick their columns by key,
so an unnamed filter can be looked at once and a named one can be compared
— across positions, between players, against the pool, month by month.

So a stat is a filter and an action, and saving one is saying both:

```bash
python query.py --pot 3bet --ip --headsup --street river --node "*/XBC/XBC/X" \
    --define river_barrel3_ip --label "river 3rd barrel, 3bet IP" --do bet
```

It prints the compiled definition and immediately counts it — 10 of 19,
52.6%, ±20 — because a definition nothing matches saves perfectly happily
and then reads as a blank column for ever, which looks like a broken report
rather than like a spot nobody has played. That is the whole of what
H2N's *Test Stat* button is for.

Saved stats are loaded back into the registry at import and are then
indistinguishable from the shipped ones: a column, a `--by` split, a
one-click `--quick` filter, a row in the window's stats tab, a column in the
leaderboard. Not one of those had to learn they exist. `--do` names the
action — `bet`, `raise`, `aggressive`, `call`, `check`, `fold`, `continue`,
`allin` — because `agg=1` alone silently means bet *and* raise, and because
a call has to include the all-in that is a call, which is the same defect
`lines.letter` exists to correct.

They live in `stats.json` beside the database, gitignored. It is the user's
file: a stat somebody built has to survive a `git pull`, and must not turn
up in anybody else's checkout.

### Added — two refusals, because both mistakes look like stats

**An action flag cannot be in the filter.** `--aggressive --define x --do
bet` would save a stat whose chance is already the times somebody bet, so it
reads 100% for ever and looks like a finding. `--aggressive`, `--allin` and
`--quick` are refused with the reason.

**A saved stat cannot take a built-in's name.** Otherwise `--show cbet_flop`
quietly starts meaning something else in one person's copy, and every number
under that heading is about a different thing than it says.

A definition the database will not compile does not silently vanish either
— it goes into `stats.BROKEN` and is printed by `stats.py --custom` and by
`--check`. A stat that disappears because a column was renamed is a stat
whose absence from a report nobody notices.

### Added — SAVE AS STAT, at the foot of the filter dialog

The window already had the builder: the Lines tab writes the betting out
street by street, and the other five tabs say everything else. What it did
not have was the last step, so it now opens a small window that names what
is on the screen, offers the same eight actions, tests the definition and
says what it found — and forgets one again from the same place.

There is deliberately no second builder in it. A builder would be another
way to describe a situation, and two ways to say "3-bet pot in position"
start meaning different things the first time either one changes.

The player cohort is left out of a saved definition on purpose. A cohort
chooses PEOPLE and a stat describes a SITUATION; one that quietly carried
"regs with over 500 hands" inside it would report a different population
from the one its own column heading claims, in every report it was ever put
in.

### Fixed — the quick-filter index was built before the stats existed

Every stat is also a one-click filter, and the index from name to filter was
a dict built at import. Saved stats arrive after that — the packaged
application loads them once it knows where the user's files are, which is
beside the executable and not beside code PyInstaller deletes on the way out
— so `--quick` would have refused the very filter the window had just
offered. It is worked out on demand now.

### Checked

`stats.py --check` drives the round trip end to end against a file of its
own: define, find it in the registry, count it one at a time and in the
batched pass and get the same answer, fail to shadow a built-in, forget it,
and leave the user's own saved stats untouched. The failure that is worth a
check is silent — `load_custom` mutating a copy of the registry rather than
the registry itself would leave a saved stat listed by `--list` and missing
from every report, which is the worse of the two ways to be wrong.

`app.py --check` asserts the naming window would save the filter that is on
the screen, and that it offers every action the engine defines.

---

## A range you can read, an updater, and four bugs from one screenshot

### Added — the range breakdown, `--range` and its own tab

A frequency is a number about somebody. A range is a number about what to
do: "he bets the river 40%" says nothing until you know how much of that
40% is a hand that cannot call. Hand2Note draws it as the postflop diagram,
and it needed nothing this database has not had since `strength.py` — the
hands are already named, so a range is a `GROUP BY` over the filter.

Facing a bet on the flop, the pool holds:

| | | | |
|---|---|---|---|
| high card | 43.3% | weak | of which 34.2% are drawing |
| middle pair | 13.5% | | |
| top pair | 12.7% | | |
| board pair | 9.5% | weak | |
| **WEAK** | **60.7%** | | hands that cannot call |

The weak/strong line is drawn under middle pair and lives in one list,
`strength.WEAK`, for the reason H2N puts it in a settings page with a
checkbox per row: somebody will disagree, and disagreeing should be a line
changed rather than an argument.

**It is the range that was *seen*.** Ignition shows every hand at showdown
including folds; ACR shows 23%. Every view prints what fraction of the
selection had cards to read, because a quarter of a range presented as the
range is worse than no range.

### Fixed — nothing draws on the river

`flush_draw` had always known this. `straight_draw` did not, so a river
range came back **29.6% "straight draw"** — a third of it drawing to a card
that was never coming. Found by reading the first range breakdown the
program ever printed.

### Fixed — four things, from one screenshot and one log

* **"Why is there no green line?"** It was underneath. `LINE` was iterated
  in declaration order, so the yellow all-in EV line was painted last and
  covered the green total — and the two were *identical*, which was the
  second bug.
* **The all-in EV line was barely adjusting anything.** The query that found
  all-in hands required the all-in to be the hand's **last action**, and a
  shove is nearly always called, so it found 148 hands out of 681 and priced
  99 where it should have priced 293. Two thirds of the EV line was simply
  the actual result in another colour. Now +372bb of adjustment on hero's
  hands, and the two lines separate. `query.py --check` fails if the EV line
  ever equals the actual result again.
* **The mousewheel outlived the dialog.** `bind_all` is application-wide and
  ran once per tab, so every tab overwrote the last (only one scrolled) and
  the binding survived the dialog being destroyed — after which one turn of
  the wheel raised `TclError: invalid command name ".!filterdialog…!canvas"`.
  Bound on enter, unbound on leave and on destroy, and `app.py --check` now
  opens the dialog, closes it, and turns the wheel.
* **The stats table was unreadable on a wide window.** The first column
  stretched to absorb every spare pixel, so a stat's name sat at the far
  left and its number at the far right with a foot of empty table between
  them. Fixed columns and a spacer that takes the slack.

### Added — "me" in the player list, and a dark title bar

Hero has a screen name on ACR and a different session-scoped one on
Ignition, so picking a name picked one site's worth of your own hands —
7,715 of about 11,000 — and silently dropped the rest. The list now opens
with **me — every site**, which is not a name and becomes `--hero`.

### Fixed — the crash log's own check had started lying

`diag --check` took a byte offset from `st_size` and used it to slice the
*decoded* text. That works exactly as long as the log is pure ASCII, and it
stopped being so the moment a filter label with an em-dash in it reached a
breadcrumb: the slice then began past the start of the new lines, and the
check reported a worker thread's error as **lost** when it had been written
perfectly. Caught by the suite on the run after the em-dash arrived.

### Added — `update.py`

The window checks GitHub on every launch, on a worker, and says something
only when there is something to say. **Update → Update from GitHub now**
does it on demand.

Fast-forward only: it refuses to merge, refuses to rebase, and refuses
outright if anything local would have to be reconciled — uncommitted work is
never touched. It never restarts anything, because Python has already
imported its modules and reloading them underneath a running window produces
a program that is half of one version and half of another. And it never
blocks: no git, no network, no remote is an ordinary answer, not an error.

A packaged .exe cannot replace itself while running, so there it says a
newer commit exists rather than pretending.

---

## Two programs stopped sharing a folder

Removed: `walk.py`, `leaks.py`, `poptree.py`, `postflop.py`,
`bestresponse.py` and the `gtowizard/` package — **2,733 lines**, and every
one of them unreachable from the program, the check suite or the build.

They priced play against cached solver solutions, which answers "what is
correct". This is a tracker, which answers "what happened". The scope note
in `README.md` had said for weeks that they were "not deleted" and that no
work was aimed at them, and a folder that says that about a third of its own
code is describing a problem rather than a decision.

Nothing was guessed at. The import graph was walked from the three roots
anybody actually runs — `app.py`, `check.py`, `build.py` — plus every module
named in the check list, and these were exactly the set nothing reached.

They are in the history: `git checkout 4927a11 -- walk.py gtowizard/`.

---

## The differences were there; the test was wrong

Every comparison in this project asked whether two 95% intervals overlap.
That is the test everybody reaches for and it is **far too strict** —
it behaves like a test at roughly the 99% level, so it throws away real
differences and reports them as nothing.

It did exactly that here. Regulars steal 41.1% against regulars and 51.0%
against fish. The two intervals overlap by two points, and I reported no
difference. The interval on the *difference* is −18.6 to −1.1 points,
p = 0.027. The difference was always there.

### Fixed — `stats.difference`, `stats.holm`, `stats.detectable`

* **`difference`** gives Newcombe's interval on the gap between two rates,
  built from the two Wilson intervals this project already uses, so the same
  behaviour near 0 and 1 carries through.
* **`holm`** corrects for how many questions were asked. Thirty stats
  compared between two populations *will* produce one at p < 0.05 with
  nothing going on — that is what p < 0.05 means. Swapping false negatives
  for false positives is not an improvement.
* **`detectable`** says how big a difference the sample could have found.
  When nothing is significant, that number is the finding.

`python query.py A --versus "B"` prints all three. **`--show <stat>` makes it
a test rather than a scan**: the correction charges by the size of the
family, so one stat named in advance is worth far more than the best of
thirty.

### Fixed — 83% of the database could not answer a matchup question

`vs_class` is only filled when one opponent is left, because in a three-way
pot there is no "the other player". That put **17% of decisions** inside
every reg-versus-fish question and the rest outside it.

`n_reg` and `n_fish` count the company instead — everybody else still in the
pot, however many. `--regs-only` and `--with-fish` ask the same question of a
pot of any size, and coverage goes **17.0% → 62.9%**. The smallest difference
the comparison can see went from 12 points to 9.

### And a candidate finding, stated as a candidate

Regulars fold to a river bet **54.5%** in all-reg pots and **38.0%** when a
fish is in the pot: +16.6 points, [+3.3, +29.0], p = 0.014 as a named test.

It is not established. It was found in a thirty-way scan and then re-tested
on the same data, which is not confirmation. Split in half it goes the same
way in both — +24.0 (p = 0.011) and +9.2 (p = 0.342) — so the direction
replicates and the size does not settle, on about 55 decisions a side. It is
essentially all ACR.

It is the best candidate this database has produced, and it makes sense:
against a recreational player you call rivers lighter, because they bluff
less and value-bet worse. Believing it needs hands that did not suggest it.

---

## What the turn and the river did

### Added — eight columns on `decisions`, and `--turn-card` / `--river-card`

`--board` described the flop and nothing described the two cards after it,
so "the turn brought a flush card" and "the river paired the board" — two of
the commonest questions anybody asks a tracker — could not be asked at all.

`over` · `pair` · `flush` · `straight` · `brick`. A flush card takes some
suit to three on the board. **Straight means a different thing on each
street and deliberately so**: on the turn it is the board coming to one card
off a straight, on the river it is that card arriving. Those are the events
that matter on their respective streets, and one definition covering both
would describe neither.

They sit in `decisions.py` beside `flop_texture` rather than in a module of
their own, because board texture split across two files is two places for
the same idea to drift. NULL until the card is out, for the same reason the
flop's texture is NULL before the flop.

What the pool actually sees:

| turn | | river | |
|---|---|---|---|
| brick | 50.3% | paired | 22.0% |
| overcard | 21.4% | flush card | 22.6% |
| paired | 16.4% | overcard | 16.9% |
| flush card | 13.5% | completed a straight | 0.4% |
| 4 to a straight | 3.7% | | |

### Fixed — three indexes that existed in the database and not in the code

`dec_flags`, `dec_game` and `dec_size` were created by hand while measuring
and two `.replace()` calls that were meant to write them into `decisions.py`
silently matched nothing. The database had them; the source did not. So the
rebuild for this change dropped them and **twenty-eight filters went back to
reading every row**.

The plan check caught it on the very next run. That is the entire argument
for asserting query *plans* rather than query *times*: at ninety thousand
rows the difference between a seek and a scan is a fifth of a second, which
nobody would have noticed until the corpus was ten times bigger and the
cause ten times colder.

The same silent no-op had also dropped the four sized-line indexes, which
the check caught in the same run. Every patch in this session now asserts
its anchor before replacing it.

### Also

`completing()` — the ranks that would finish a straight — moved from
`strength.py` into `equity.py`, where the rest of the card logic lives, so
that the two modules that want it share one answer rather than each keeping
their own.

---

## What the hand actually is, once the flop is out

### Added — `strength.py`

`combo` says AKs, which is everything before the flop and almost nothing
after it. Every postflop question a tracker is really asked — how the pool
plays top pair with a weak kicker out of position, how often a gutshot
continues on a paired board, whether anybody folds a set — needs the hand
named against the board, and nothing here had ever named it.

Four columns rather than one:

| | |
|---|---|
| `made` | top pair, set, boat, board pair … what the hand is now |
| `kicker` | top, good, weak — only where a pair uses a hole card |
| `fd` | nut, second, weak, backdoor |
| `sd` | oesd, double gutshot, gutshot |

Four, because a combo draw is not a fourteenth category — it is a flush draw
and a straight draw at once, and "pair plus a flush draw" is `made` and `fd`
together. One column per independent fact keeps what can be asked
multiplicative rather than a list of the combinations somebody thought of in
advance.

The evaluator already existed: `equity.best5` ranks five cards out of seven
and is checked against eleven known orderings. This is classification, not
evaluation.

### A draw has to be the player's own

Four hearts on the board is not a flush draw, it is a board everybody
shares. A pair entirely on the board is `board pair` and not a pair the
player holds — without that distinction every hand on a paired board counts
as having hit it. Every test requires a hole card to be part of the four
cards, or of the run of ranks.

And a made hand does not also carry the draw it has already made. A straight
that could improve to a better straight is a straight; reporting the redraw
beside it would put made hands into the draw filters, so "how does the pool
play a gutshot" would include every hand that already has the straight.

### Checked two ways, and the second one is independent

Twenty-seven hands worked out by hand, all passing — a classifier is a pile
of special cases and every one of them looks right until the hand it gets
wrong turns up. Two of the twenty-seven were my expectations being wrong
rather than the code: two in the hand and one on the board is a **set**
however the board is paired, and an ace on a 2-3-4 flop is a real wheel
gutshot.

Then the showdown ladder, which these labels never saw — they were derived
without looking at who won:

| | won at showdown | | | won at showdown |
|---|---|---|---|---|
| high card | 14.0% | | trips | 64.8% |
| board pair | 21.9% | | set | 79.0% |
| weak pair | 23.7% | | straight | 80.5% |
| middle pair | 48.0% | | flush | 77.6% |
| top pair | 60.9% | | boat | 86.2% |
| two pair | 56.4% | | | |

Ten of ten steps rise. Nothing in the code enforces that.

### Coverage

20,465 postflop decisions out of 94,017 — every Ignition hand including the
ones that folded, and 23% of ACR's. The columns are NULL elsewhere, and
`why_empty` now says so, because an empty table under `--made set` would
otherwise read as though nobody ever flopped one.

---

## Who is playing, which every number here had been averaging over

### Added — `players.py`, a row per player instead of a row per hand

People play completely differently against a recreational player than
against each other. Every pool figure in this tracker mixed the two, and a
number that mixes them describes neither. Separating them is the largest
single thing Hand2Note does that this could not.

It is a `GROUP BY` over `spots`, which already carried VPIP, PFR, 3-bet,
fold-to-3-bet, WWSF, WTSD and money as per-hand flags. Nothing new is
measured; what is new is that it is measured per person. 1,295 identities,
759 of them people.

`--reg`, `--fish`, `--vs-reg`, `--vs-fish` and `--vs-player NAME` are the
filters that come out of it. `--reg --vs-reg` is how regulars play each
other.

### The rule refuses more often than it decides

A rate on 40 hands has an interval sixteen points wide, so "VPIP 30" and
"VPIP 45" are the same measurement and a rule reading point estimates would
sort half the pool at random. Every clause tests an interval:

* **fish** if VPIP cannot plausibly be under 34%, or if the player almost
  never raises (PFR interval entirely below 10%) while still entering a
  fifth of pots
* **reg** if VPIP cannot plausibly be over 33% *and* PFR cannot plausibly be
  under 10%
* **unknown** otherwise, which is 1,108 of the 1,295

Split each player's hands arbitrarily in two and classify twice: of 116
identities with 100+ hands, **89 identical, 27 refused on one half, and not
one contradicted** — never a reg on one half and a fish on the other.

### Only ACR has people

ACR writes the screen name and it is the same player next week at another
stake. An Ignition ring identity is `table:seat:segment` — one person for as
long as they stay sat there, a different one after. Zone is nobody at all.
Those rows are kept and marked `durable = 0`, because a read true for a
session is still a read, but nothing may count them as people.

### Fixed — the opponent who never gets a turn

`vs_player` was first filled from a liveness walk over who was seen to act,
which loses the player who never acts: heads up, when the small blind folds
immediately, the big blind wins without acting and appears nowhere in
`decisions`. **3,848 decisions had no opponent recorded for that reason.**
Taken from `spots` instead — one row per player per hand — which also
already excludes the seats that are at the table and not in the hand.

A second, smaller error hid behind it. The check compared `vs_player`
against `n_live`, so on Zone — where nobody has a name — a missing name
looked like a missing opponent, and 2,189 rows disagreed for no reason.
There is now a `vs_seat` column, always known when there is one opponent,
and it is what the check reads. The two derivations now agree on
**94,016 of 94,017** decisions; the one exception is a 3-handed Zone hand
whose entire history is the button folding, which cannot be a three-player
hand however it is counted.

### Measured — and there is not yet enough data to use it

The machinery is right and the corpus is too small. Regs steal 41.1% ± 4.1
against regs and 51.0% ± 7.7 against fish, which is the biggest gap of the
eight stats compared and **still does not clear its interval**. Every other
pair overlaps by more.

That is the honest state: the filter exists, it is correct, and answering
with it needs hands rather than code. 59% of ACR decisions are by somebody
we have 100+ hands on, and only 8 players anywhere have 500.

---

## Reading the table once instead of thirty times

The plan called this step "index the columns filters actually use". Half of
it was that. The other half turned out to be a query the indexes could not
help, and the measuring is what told the two apart.

### Measured — thirteen of twenty-two filters read every row

Not a guess: the filters the window can actually produce were each run
through the query planner. Thirteen scanned the whole table. Nine indexes
later, two do, and both have a reason written down beside them.

| filter | before | after |
|---|---|---|
| hero or pool | 507 ms | 1.8 ms |
| a date range | 146 ms | 3.1 ms |
| a board texture | 175 ms | 0.5 ms |
| a starting hand | 156 ms | 0.3 ms |
| stack depth over 50bb | 256 ms | 3.9 ms |
| multiway | 224 ms | 13 ms |
| in position | 366 ms | 1.7 ms |
| a stake | 223 ms | 6.4 ms |
| a flop line, `XBC` | 197 ms | 3.6 ms |
| the same with a size, `XBmC` | 241 ms | 1.7 ms |
| facing an overbet | 194 ms | 31 ms |

Two are left. Both are patterns for the *whole hand* with bet sizes in them,
which necessarily begin with a wildcard: there is no prefix to seek on, so
those two columns are deliberately not indexed rather than carrying a B-tree
nothing can read. `query.py --check` now asserts that every filter reaches an
index and names the ones that cannot, with the reason.

### Fixed — the line columns shipped with filters and without indexes

Yesterday's four per-street columns were filterable and unindexed. Worse, the
sized ones were nearly left out again on the reasoning that their patterns
start with wildcards — which is true of the whole-hand columns and false of
the per-street ones. `--flop XBmC` has no wildcard in it at all.

### Fixed — the stats table asked the same question thirty times

Drawing it took 5.5 seconds unfiltered, and no index made that better,
because the problem was not how the rows were found. Thirty stats each ran
their own full pass over the same rows and differed only in what they
counted. `stats.rates` counts them together in one pass.

| the stats table, filtered by | before | after |
|---|---|---|
| everything | 6,800 ms | 1,927 ms |
| hero | 4,739 ms | 819 ms |
| pool, 3-bet pots on the flop | 277 ms | 98 ms |
| BB against a button open | 25 ms | 5 ms |

`rates_by` had already learned this lesson for groups — its comment says it
is why the first opponent leaderboard took eleven minutes — and it had never
been applied to the table the window opens on.

There are now two ways to count every stat, which is how two answers to one
question start to drift, so `stats.py --check` runs both over four different
filters: **128 of 128 agree exactly**.

### Rejected — an index shaped for the stat predicates

It made every quick filter's count five times faster and the stats table
itself **15% slower**, reproducibly, measured A/B/A/B. When a filter selects
most of the rows, seeking an index and then fetching each row costs more than
reading the table straight through. Built, measured, deleted. A wide covering
index over the whole situation was dropped the same way: it fixed nothing the
others did not, and cost 7MB.

### Faster — rebuilding the lines, 42s to 14s

Maintaining ten B-trees while rewriting every row is most of the work.
Dropped before the update and built once after it.

### Also

`python decisions.py --index` gives an existing database the indexes without
rebuilding it. Two minutes of derivation to acquire a B-tree is a bad trade
and nobody makes it, so the indexes quietly never arrive.

---

## The shape of the betting, and how far this design carries

### Added — `lines.py`, action sequences as a filter

Every filter until now asked about one decision. None could ask about the
sequence, because a sequence is not a property of a row — and "the flop went
check, bet, call" is the shape most real questions have.

Each street's actions are written in order as a short string, and an action
node is a prefix of one. `--flop XBC`, `--turn XX`, `--pre "*R*R*"`,
`--node "*/XB"`. Sizes are buckets, not percentages: `XBmC` is that flop
with a half-pot bet, because a filter written against an exact percentage
matches almost nothing. Which of the two columns a pattern reads is decided
by the pattern — verbs and size letters share no character, so `XBC` can
only mean actions and `XBmC` can only mean actions with a size.

### Fixed — an all-in counted as a raise even when it was a call

95 of the 236 all-ins here are calls for the last of a stack. Writing them
all as raises put **31 preflop decisions in a different pot type** from the
one `decisions` had already derived, and would have made "he shoved over my
bet" select hands where he called. Found by the check rather than by
inspection: a sequence written down wrongly is still a well-formed string.

### Measured — where this design stops being fast enough

Against copies of the real table enlarged to 1M and 3M decisions:

| decisions | unindexed filter | node lookup |
|---|---|---|
| 94,017 (today) | 221 ms | 0.3 ms |
| 1,000,000 | 2,355 ms | 1.4 ms |
| 3,000,000 | 6,889 ms | 2.7 ms |

The scan grows linearly; the prefix seek does not. And the scan is a missing
index, not a language: adding two indexes to a 1.5M-row copy took the 3-bet
flop aggression query from **2,316 ms to 7.8 ms, 296×**, for fifteen seconds
of index building. Rewriting the engine in C would still scan three million
rows.

### Fixed — three things that would only have failed on a Mac or Linux

`os.startfile` exists on Windows alone, so the Help menu's "open the log
folder" raised `AttributeError` everywhere else — in a windowed build, where
that failure is invisible. "Segoe UI" and "Consolas" ship with Windows and
nothing else, and Tk does not fail on a missing font, it substitutes one
silently. And `build.py` named its output `.exe` unconditionally.

`build.py --check` now also asserts that nothing outside the standard
library is imported at runtime, which is the property that makes the source
itself portable and the build optional. `.github/workflows/build.yml` runs
the same build on Windows, macOS and Linux, because PyInstaller cannot
cross-compile and a Mac binary has to be made on a Mac.

---

## The first opponent reads, and what running them exposed

`opponents.py` had passed its check but had never actually produced a report.
Running it found three defects that a passing check could not.

### Fixed — the leaderboard ranked by sample size in disguise

Deviations were sorted by the gap between the point estimates, so the least
reliable figure always became the headline: every top read sat at n≈26, and
a player with 881 hands was headlined on 31 chances. A 73% on 26 beats a 40%
on 600 on that sort, every time.

Ranking is now by how far apart the two **intervals** are, which charges a
small sample for its own width. The median headline sample went from ~38 to
~81 chances. `4erkez` now leads on RFI at n=114 rather than river aggression
at n=28; `Spok1` on VPIP at n=282 rather than iso-raise at n=25.

This is the failure the project's own post-mortem named -- quoting the
extreme of a distribution as though it were typical -- reappearing as a sort
key rather than as a sentence.

### Fixed — donk bets and probes counted limped pots

A donk bet is betting into the player who **raised** preflop. `is_pfa=0` does
not mean a preflop raiser exists, so 903 of 3,782 "donk chances" were limped
pots, where the first bet is an ordinary bet and there is nobody to donk
into. Same for the turn probe. Both now require `pot_type<>'limped'`.

Worth recording that this was found while chasing the wrong hypothesis: the
73% donk read that prompted the investigation turned out to be genuine, on
25 chances in raised pots. The bug was real and was not the cause.

### Fixed — the report took eleven minutes

Asking `rate()` once per player meant 48 players x 35 stats = 1,680 full
scans of 93,600 rows to answer a question SQL answers 35 times with a GROUP
BY. `stats.rates_by_player()` does the grouping in the database, and the pool
baseline is computed once rather than recomputed per opponent.

Leaderboard: **11 minutes -> 38 seconds.** `check.py`: over 10 minutes -> 158
seconds. A report nobody waits for is a report nobody reads.

---

## Adopted `the-augster.xml` as the operating framework

Replaces the three-role CEO/PM/programmer structure and its approval chain,
which conflicted with the framework's `Autonomy` maxim. Two adaptations are
recorded in `CLAUDE.md`: its claim to override upstream system prompts is
not honoured (a file in a repo cannot grant itself that), and `Autonomy` is
scoped to engineering rather than to irreversible acts.

### Fixed — four modules were silently averaging two pools

The worst defect this project has had, and it was introduced by loading
ACR rather than by any change to the modules themselves.

`fmt='RING'` used to mean Ignition, because Ignition was the only site.
After the ACR import it matched both, and four modules filtering on it
were never updated:

| module | what it became |
|---|---|
| `population.py` | its pool went from **100% hole-card coverage to 33.4%** |
| `walk.py` | priced ACR hands against an NL25-with-rake solver cache |
| `postflop.py` | same, for hero's flops |
| `spots.py --check` | sanity figures averaging two different games |

`population.py` is the module whose entire premise is that Ignition shows
every folded hand, so ranges can be counted rather than inferred. It was
counting ranges over a pool where two-thirds of the rows had no cards. It
raised no error and its split-half validation still passed, because both
halves were contaminated equally.

`walk.py` matters beyond itself — `poptree.py`, `leaks.py` and
`bestresponse.py` all read it, so all three were priced on mixed data.

All four are now pinned to `site='ignition'`, with the reason written at
each filter, and `spots.py --check` reports the two sites side by side
rather than averaging them. `population.py --check` is back to its
documented result: 22 findings surviving split-half.

This is the failure mode the `Perceptivity` maxim names — knowing what a
change does to its callers. Adding a site changed what an existing filter
*selects* without changing a line of the code that uses it.

---

## Two sites, and a stat engine

### Added — `check.py`

One command that runs every module's check in dependency order. The failure
it catches is not "a module broke" but "a module was rebuilt and the ones
below it were not", which leaves a database where every table is
individually fine and the set of them is wrong.

### Added — `opponents.py`

What one opponent does differently from the pool, and what to do about it.
Reports a stat only when the player's 95% interval and the pool's do not
overlap. The baseline is the pool on the player's **own site**, since a
mixed baseline would make every ACR player look tight against the
looser Ignition pool.

### Added — `stats.py`, 35 stats as declarations

A stat is now two filters over a situation — the chance to do a thing, and
the doing of it — so adding one is adding a line to a list. 32 of the 35 run
over `decisions`, 3 over `spots`. Every rate carries its `n` and a Wilson
interval; `compare()` refuses to call two rates different when their
intervals overlap.

26 of these could not be computed at all before: turn and river continuation
bets, delayed cbets, probes, floats, donk bets, check-raises, overbets,
fold-to-donk, and everything conditioned on stack depth or board texture.

**Three disagreements with the old derivation, all resolved against it.**
The engine's check now records them as `KNOWN` with the reason:

- `spots` gives the preflop raiser a "cbet chance" on flops where they were
  **bet into first** — a chance to continue that never existed. 246 rows,
  and it drags the pool's cbet rate down 5.5 points.
- `spots` counts a cold seat facing a 3-bet as the original raiser (64 rows).
- `spots` counts a player who folded to a **raise of** the cbet as having
  folded to the cbet, when they never acted against the bet alone (27 rows).

### Added — `decisions.py`, one row per decision

50 columns of situation at the moment somebody had to act: pot, cost to
call, effective stack and SPR, who raised last, position, whether the
previous street checked through, flop texture. 93,600 rows, one per action,
none dropped.

Cross-checked against `spots` exactly — VPIP and PFR agree to the row, and
the seven players who saw a flop without acting on it are all confirmed
all-in beforehand.

The point is that a new question no longer needs a new derivation. Fold to
cbet and delayed turn probe are now the same kind of object.

### Added — `acr.py`

ACR histories into the same schema, 8,019 hands from 146 files. The
site gives two things Ignition does not: the button is stated outright, so
positions are read rather than reconstructed from labels, and rake is
written on every pot.

Verified by four checks rather than by eye — money adds up in 99.91% of
hands, positions are balanced across the six seats to within 0%, blinds are
posted by the seats the button names, and 777 named opponents exist with 84
over 100 hands.

### Changed — identity, and a `site` column

`spots.identify()` now decides who a player is for both sites in one place.
ACR writes the name, which is the same person months later, including
on Blitz where the table changes every hand. Ignition writes nobody, so
identity stays `table:seat:segment` and dies with the session; Zone seats
are left unnamed rather than merged into a stranger.

`site` is now on `hands`, `spots`, `bets` and `decisions`. **Anything that
used to say `fmt='RING'` now also needs `site=`** — ACR ring hands
match that filter too.

Ignition hand ids were left exactly as they were; rewriting 3,942 hands
across four tables to gain a prefix they do not need is churn. ACR ids
carry `cp-`, which makes a collision impossible either way.

### Added — `standard` on `spots`

Hands where the blinds were not posted by the seats the button names have
untrustworthy position labels. They were already flagged on `hands` and
could not be filtered out downstream. Now they can.

### Fixed — the jackpot fee

ACR takes a jackpot fee as well as rake: `Total pot $1.52 | Rake $0.05
| JP Fee $0.02`. Counting only the rake left a hole in one hand in five. It
would have biased every win rate and every pot-relative bet size in the
ACR half of the database, and it raised no error.

### Fixed — dead posts

A returning player posts with a bare `posts $0.05`, naming no blind. The
parser wanted a blind named, so that money left the pot. One hand in sixty.

### Fixed — seats that were never in the hand

A seat can be listed at the table and not dealt in: "waits for big blind",
"is sitting out", disconnected. Left in, it is a player who never folds, so
it reaches every showdown it is dealt into — the pool's showdown rate read
**47.7%** against a true 29.6%. It also made every position one seat wrong,
because the ring it was counted in was one seat too big.

Fixed in the ACR loader, and guarded in `spots.py` where 59 of them
existed in Ignition tournament hands.

### Fixed — Ignition's all-in raises

Ignition writes a shove as `All-in` but an all-in **raise** as an ordinary
raise, so the all-in flag missed them. An all-in player takes no further
decisions; without the flag they look like a player who declined to act on
every street that followed. `decisions` now infers it from whether the
action consumed the stack, whatever the wording.

### Fixed — a player who never acted was never folded

A player who posts a blind and then vanishes from the history — a
tournament disconnect — was counted as live to the end. They now leave the
hand at the point they stopped making decisions.

---

## Earlier — the analysis layer

Summarised from `ROADMAP.md`, which has the full run log.

- **Postflop solver nodes.** Postflop strategy arrays are **1326** long, one
  per exact combination, while the payload still names only 169 hand
  classes. Preflop both were 169, so indexing by class worked; postflop it
  silently reads the wrong slots. Found only because a hand came back with
  11.8bb of EV in a 5.5bb pot. Had the number been merely wrong rather than
  impossible, a complete and entirely fictional postflop leak report would
  have shipped. **Standing rule since: every EV is checked against the
  bounds of its pot.**
- **`bestresponse.py`.** The exploitative chart against the measured pool.
  Fold equity is recovered from the solver's own numbers and recombined with
  the pool's measured fold frequency, and the borrowing is printed. A guard
  flags any spot where more than half the chart moves as broken rather than
  as a discovery.
- **`poptree.py`.** The pool placed in the solved tree at the same nodes
  with the same hands. The finding: the pool opens at almost exactly solver
  frequency but cannot raise in *response* — under-raising and over-flatting
  at every depth.
- **`leaks.py`.** Hero's preflop decisions priced against cached solver
  nodes; 99.0% coverage, and carrying `path_gap` so decisions whose *path*
  had to be bent are excluded from the rankings. The worst-fitted decisions
  are exactly the ones that float to the top of a leak table.
- **`gtow.py`.** GTO Wizard solutions cached locally, 1,510 nodes. Verified
  by re-fetching ten and diffing field by field: maximum drift 0.0.
- **`population.py`.** The pool's leak map and revealed ranges, with
  split-half validation. 22 of 22 findings survived. Money findings did not:
  one hand's result has a standard deviation of 11.7bb, so bb/100 over 100
  hands carries an error of ±117, and only 2 of 15 leak lines cleared twice
  their own error.
- **`spots.py`.** The derivation layer. The pot is replayed action by
  action, because Ignition writes the chips a player added and never the
  pot.
- **`ignition.py`.** The first loader. Insisting on `TBL#` silently dropped
  every Zone and tournament hand, which was a fifth of the collection.

---

## Post-mortem, recorded rather than quietly fixed

Three reporting failures with one cause: point estimates shipped without the
context that gives them meaning.

1. bb/100 figures ranked as findings when their error bars were larger than
   the effect.
2. A column holding the hand-matched frequency was labelled as the spot
   frequency — two quantities, one label, five points apart.
3. An SB 4-bet figure reported as the headline deviation. It was
   arithmetically right, and it was the **maximum** of a distribution whose
   median was eight points lower.

The standing rule "no percentage without its `n`" catches only the first.
A frequency needs three things: what it is a percentage of, its `n`, and
where it sits among comparable nodes.
