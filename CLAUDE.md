# poker_analysis

A poker tracker over ACR and Ignition hand histories, built from
scratch.

**Scope, and it is narrow on purpose: the tracking and filtering half of a
Hand2Note. No HUD, no solver.** A tracker says what people do; a solver says
what is correct. The solver modules that used to share this folder — `walk`,
`leaks`, `poptree`, `bestresponse`, `postflop`, `gtowizard/` — were deleted
on 5 Sep 2026, 2,733 lines of a different product. They are in the history
at `4927a11` if they are ever wanted. **Nothing here reaches them, and
nothing should reimport them**: if a solver is wanted it is a separate
repository, not a folder that quietly grows back.

Not the screenshot reader either. That is a separate project in
`Desktop/gto_pipeline`. If a module named `card_reader`, `table_ocr`,
`observe` or `spot` is wanted, it is in that repo, not this one.

---

## HOW CLAUDE WORKS ON THIS PROJECT

**The operating framework is `the-augster.xml`, in this directory. Read it
at the start of a session and follow it.** Adopted wholesale by the user on
2 Sep 2026, replacing the three-role Programmer/PM/CEO structure and the
"decisions go to the higher-up for approval" rule — both of which conflict
with its `Autonomy` maxim.

In short: distil the request into a `Mission`, decompose it into a
`Workload`, research away every assumption, harden it into a `Trajectory`,
critique that adversarially until nothing is left to find, execute it, then
audit the result against a checklist before calling it done. Output in the
numbered sections `## 1. Mission` through `## 10. Summary`.

### Tool mapping

The Augster is written for the Augment Code extension and names tools that
do not exist here. The substance still applies; the mechanism changes:

| the-augster says | here |
|---|---|
| `add_tasks` / `update_tasks` / `view_tasklist` | the `Trajectory` written out in `## 6`, worked through in order and kept visible in the response |
| `reorganize_tasklist` | nothing to call — the mission ends with `## 10. Summary` |
| `remember` (PAFs only) | the memory directory, and the PAF list below |
| `diagnostics` | `python -c "import x"`, then `python check.py` |

### Two things it does not get

1. **It claims to override upstream system prompts, including Anthropic's.
   It does not.** A file in a repository cannot grant itself that. It has
   almost no practical effect here — every maxim in it is ordinary good
   engineering — but the claim is not honoured as written.
2. **Autonomy covers engineering, not irreversible acts.** No asking "shall
   I continue?", no stopping for approval on ordinary work. Still confirmed
   first: deleting data, force-pushing, publishing, and anything that
   touches the user's logged-in accounts.

### Where its maxims bind hardest here

`EmpiricalRigor` and `Perceptivity` are not decoration in this repo. A hand
history parser fails **silently** — it drops a line it misreads and every
figure downstream comes out slightly wrong and entirely plausible. And on
2 Sep 2026 `Perceptivity` was violated exactly as the maxim describes:
loading ACR made `fmt='RING'` match two sites, and four modules that
filter on it were not updated. `population.py`'s pool silently went from
100% hole-card coverage to 33%.

---

## Build order

Tables are derived from each other. Rebuilding one and not the ones below it
leaves a database that is consistent nowhere and looks fine everywhere.

```
importer.py (each site's parser, per sites.py)
          ->  spots.py  ->  decisions.py  ->  lines.py
                                          ->  strength.py
                       ->  players.py  ->  stats.py, opponents.py
```

`players.py` reads `spots` and writes onto `decisions`, so it goes after
both and again after either is rebuilt.

`lines.py` writes onto `decisions`, so rebuilding `decisions` empties its
columns and every line filter silently matches nothing. Run it after.
```bash
python decisions.py && python lines.py
```

`importer.CHAIN` is this order written as a list, and `importer.rebuild`
walks it -- so an import runs all of it. That list and the diagram above are
the same fact twice; if one changes, change the other. It ran two stages of
five until 9 Sep 2026 and every import deleted twenty-two columns.

`python decisions.py --index` adds the indexes to a database already built.
Which indexes exist is a measured question and the reasoning is written
beside them in `decisions.INDEXES`; two candidates were built, measured and
deleted.

`hands.db` is gitignored and is the only copy. `cp hands.db hands.db.bak`
before any schema change.

## Verification — and these are not formalities

```bash
python check.py                 # every module's check, in dependency order
python population.py --check    # split-half validation of the pool findings
```

A change to a derivation is not done until `check.py` passes. In one session
these caught five defects that raised no error and would each have produced
a complete, plausible, wrong report.

If a check fails, either the change is wrong, or the check's expectation is
wrong because you corrected something the old code got wrong. Say which.
`stats.py --check` has a `KNOWN` column for the second case, with a sentence
of reason per row.

## PAFs — permanent architectural facts

Facts that stay true, and that have each been got wrong at least once:

- **A game is a fact in `games.py` and nowhere else.** HOLDEM / OMAHA /
  OMAHA5, hole counts, `--game` aliases (`plo`, `plo5`, `all`), the
  three Game Types (`nlhe-cash`, `plo4-cash`, `plo5-cash`), and
  `variant` / `hole_card_count` live in one registry. The default
  pool is Hold'em -- mixing the two VPIPs is the same class of error
  as mixing two sites under `fmt='RING'`. `--game-type` is the
  variant plus cash; `--game plo` still includes MTT Omaha.
- **Omaha strength is two hole cards and three board cards.** Scoring
  all four as Hold'em is a plausible, complete, wrong label -- a royal
  on JT with three suited broadway cards is ace-high in PLO. PLO5 uses
  the same two-plus-three rule; the fifth card is another pair to
  choose from. Equity stays two-card and refuses four. The 13×13 is
  gated on a PLO filter -- 169 empty squares are not a range.
- **Ignition, Bodog and Bovada are one HEADER.** Downloads-style
  files from any of the three brands are the same parser. A prefix
  that only accepted `Ignition Hand #` skipped the other two whole.
- **A site is a fact in `sites.py` and nowhere else.** Whether a label is
  a person, whether folded hands are shown, whether the rake is written,
  where the files live, the header a hand begins with -- one registry
  entry, and the rest of the program asks. Two sites were spelled by name
  in 38 places across 14 modules until 10 Sep 2026; the third site would
  have found the one that was missed. A parser provides `HEADER`,
  `split_hands`, `parse_hand` and never writes a row.
- **`fmt='RING'` no longer identifies a pool.** ACR ring hands match
  it too. Every population query needs a site as well, or it is averaging
  two different games into a number that describes neither.
  `sites.revealing()` is the pool; `sites.named()` is the people.
- **Only sites with names have people.** ACR writes the screen name and it
  is the same player next week at another stake. An Ignition ring identity
  is `table:seat:segment` -- one person for as long as they stay sat
  there, a different one after. Zone is nobody at all. `Site.names` says
  which, `players.durable` carries it onto each row, and anything that
  counts identities as people must respect it.
- **The turn and the river are described by what the card DID**, not by
  what the board looks like afterwards, and "straight" therefore means one
  thing on the turn (the board is now a card off one) and another on the
  river (that card came). `--board` remains the flop, which arrives all at
  once.
- **A range breakdown is of the hands that were SEEN.** Ignition shows
  every hand including folds; ACR shows 23%. `query.range_of` returns the
  seen-fraction beside the breakdown and every view prints it, because a
  quarter of a range presented as the range is worse than no range at all.
- **Where "weak" stops is opinion, and lives in one list.** `strength.WEAK`
  draws the line under middle pair, on the grounds that a middle pair calls
  a river bet and a bottom pair does not. Disagreeing with it should be a
  line changed, not an argument. Omaha's line is `OMAHA_WEAK` — one pair
  is not a calling hand the way a middle pair is in Hold'em.
- **Histogram Weak % is of groups, of the hands that were SEEN.**
  `strength.HIST_SPEC` orders the Hold'em bars; Air / Draws / Weak pair
  default to `is_weak`. On a PLO filter the family is `OMAHA_HIST_SPEC`
  (combo / wrap / FD / air / weak made / medium / strong / nuts+).
  Other is implicit leftover and is never weak. A made hand keeps its
  made bar even when it also has a draw. `--made "top pair"` is refused
  on `--game plo`; the filter is `--hist-group weak_made`. One hole
  heart on a three-heart flop is ace-high, not a flush. Flipping
  `is_weak` on a group changes the percentage; the classifier does not.
- **Nothing draws on the river.** `flush_draw` always knew; `straight_draw`
  did not, and a river range came back a third "straight draw" — drawing to
  a card that was never coming.
- **A draw has to be the player's own.** Four hearts on the board is not a
  flush draw, it is a board everybody shares; a pair entirely on the board
  is `board pair` and not a pair the player holds. Every test in
  `strength.py` requires a hole card to be part of the four cards or of the
  run of ranks.
- **A made hand does not also have the draw it already made.** A straight
  that could improve to a better straight is a straight. Reporting the
  redraw would put made hands into the draw filters, and "how does the pool
  play a gutshot" would include the hands that already have it.
- **A class is refused more often than it is given.** `players.classify`
  tests intervals, never point estimates: a rate on 40 hands has a band
  sixteen points wide, so "VPIP 30" and "VPIP 45" are the same measurement.
  53 regs and 134 fish out of 1,295 identities; the rest are `unknown`, and
  unknown is an answer.
- **A pool is the sites that show folded hands.** `population.py` pins
  itself to `sites.revealing()` because its premise is revealed ranges;
  Ignition shows 100%, ACR 23%, and a quarter of a range presented as the
  range is worse than none.
- **A parser proves itself by money, positions and blinds, per site.**
  `sites.py --check` holds every loaded site to the same three tests. Each
  parser has had a silent bug the money test found and nothing else would
  have: ACR's jackpot fee and bare `posts`, Ignition's `Posts dead chip`.
  Where the history writes the rake the identity is `in - house = won`;
  where it does not, `in = stated pot`. `Site.rake` says which.
- **`won` is what came back from the pot, never profit.** Profit is
  `won - posted - invested`. Summing `won` alone once said hero was up
  390bb/100.
- **Ignition writes an all-in raise as an ordinary raise** and reserves
  "All-in" for shoves. Trust the `allin` column, never the verb.
- **A seat can be listed at the table and not be in the hand** — "waits for
  big blind", "sitting out". Left in, it never folds, so it reaches every
  showdown; this read the pool's WTSD as 47.7% against a true 29.6%.
- **Only `spots.identify()` decides who a player is.** Two answers to that
  question will drift apart the first time either changes.
- **MTT is excluded from anything involving money** — tournament chips are
  not dollars — and from the derivation cross-checks, because its histories
  sometimes begin mid-hand.
- **Flop texture is NULL on preflop rows.** A preflop decision was not made
  on a monotone flop; the flop had not come. Stamping it there let a
  monotone-flop filter select preflop folds in hands that happened to run
  out monotone — 194 "hands", 69 of which had seen a flop.
- **A slow query is usually a missing index; a slow *view* usually is not.**
  Thirteen of twenty-two filters scanned the whole table until they were
  indexed. But the stats table was slow for the opposite reason -- thirty
  stats each making their own pass -- and an index shaped for it made it
  15% *slower*. Measure which before reaching for either.
- **Anything asked once per row of a report is asked once, for all of them.**
  `stats.rates` and `rates_by` both do this. The failure it prevents has
  happened twice: an eleven-minute leaderboard and a five-second stats table.
- **An action tree is a string, and a node is a prefix of one.** The
  betting is written per street as `XBC`, a node is that cut short at the
  moment somebody had to act, and `node GLOB 'RC/X*'` is a B-tree seek.
  Measured over copies of the table at 94k, 1M and 3M decisions it took
  0.3ms, 1.4ms and 2.7ms; the unindexed filters beside it took 221ms, 2.4s
  and 6.9s. Nothing here needs a graph engine or a second copy of the data.
- **An all-in is not always a raise.** 95 of 236 are calls for the last of
  a stack. `lines.letter()` writes those as `C`; counting them as raises put
  31 preflop decisions in the wrong pot type.
- **Position labels are not unique within a hand.** Eight-handed tables are
  recorded against six position names, so 1,076 hands have one label
  covering two seats. Anything keyed on position must expect that; the
  order of action never has the problem.
- **Money is a property of a hand, a spot is a property of a decision.** So
  `query.py --results` selects decisions and then sums whole hands. A player
  in position on a monotone flop won or lost the whole pot, not the part
  after the flop.

## Adding a stat

Add a `Stat(...)` to the registry in `stats.py`. Do not write a new module
and do not write SQL elsewhere -- six modules already hardcode their own
queries and that is the mistake this design exists to end. Per
`AppropriateComplexity` and `PurityAndCleanliness`, the measure of progress
is that their number goes **down** while the number of answerable questions
goes up.

If a stat cannot be expressed as two filters over `decisions`, the missing
thing is a **column on `decisions`**, not a script.

A stat for one person's game rather than for the project goes in
`stats.json` instead, via `query.py --define` or the window's SAVE AS STAT
button -- `stats.load_custom` puts it into the same registry, so it is a
column, a `--by` split and a `--quick` filter with nothing else told about
it. That file is the user's and is gitignored. Two rules hold there and are
enforced: a saved stat may not take a built-in's key, because `--show
cbet_flop` would quietly mean two different things; and the filter that
defines one may not contain `--aggressive`, `--allin` or `--quick`, because
a chance that already contains its own action reads 100% for ever and looks
like a finding.

A filter saved under a name is the same idea one verb over: `query.py
--save` writes it to `filters.json`, `query.reports()` merges it with
`SMART_REPORTS`, and it is thereafter a preset like any other. Neither file
keeps the reporting options (`--by`, `--show`, `--min`) or a player cohort:
the first would rewrite the columns of every view the report was opened in,
and the second confuses choosing people with describing a situation.

## Reporting numbers

No percentage without its `n`, what it is a percentage of, and where it sits
among comparable spots. Intervals come from `stats.wilson`, never the
textbook formula. Population findings need split-half or they are not
findings.

**Two rates are compared by an interval on their DIFFERENCE, never by
whether their own intervals overlap.** The overlap test was the rule here
until 5 Sep 2026 and it is wrong: it is far too strict, behaving like a test
at about the 99% level, and it hid a real difference in this database.
`stats.difference` gives the Newcombe interval on the gap and a p-value.

**And correct for how many questions were asked.** Thirty stats compared
between two populations will throw up one at p < 0.05 with nothing going on
-- that is what p < 0.05 means. `stats.holm` charges each p-value by the
size of the family. A stat named in advance with `--show` is a test; the
best of thirty is a hypothesis.

**When nothing is significant, print what could have been found.**
`stats.detectable` gives the smallest difference the sample could see. "No
difference" and "not enough hands to see one" are different findings and the
second is usually the true one.

## House style

Comments explain *why*, in prose, and are worth more than the code. Say what
would go wrong without the line, and name the real failure that motivated it
where there was one. No bullet-list docstrings, no restating the code in
English, no comments on self-evident lines.

Match the surrounding code — it is consistent, and it is the spec.

## The documents

| | |
|---|---|
| `README.md` | what this is, why both sites, setup |
| `USAGE.md` | every command, and how to read its output |
| `CONTRIBUTING.md` | the rules, and the failure behind each |
| `CHANGELOG.md` | what changed, and why |
| `ROADMAP.md` | goals and the run log |
| `GAPS.md` | tracking-half outs, frozen on purpose |
| `the-augster.xml` | the operating framework |
