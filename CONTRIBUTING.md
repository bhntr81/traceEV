# Contributing

Written for whoever works on this next, which is usually Claude and
occasionally a human. It is the rules, and most of them were learned by
getting something wrong.

---

## The one rule everything else serves

**A hand history parser fails quietly.** It does not crash on a line it
misreads. It drops the line, and every figure downstream comes out slightly
wrong and entirely plausible. Nobody notices, because there is nothing to
notice — the report still prints, the percentages still look like
percentages, and the conclusion is confident and false.

So nothing here is believed because it ran. It is believed because a number
that would move if it were broken did not move.

Five real defects were caught this way in a single session, none of which
would have raised an error:

| what was wrong | how it was caught |
|---|---|
| ACR takes a **jackpot fee** as well as rake | money stopped adding up in 1 hand in 5 |
| a returning player posts with a bare `posts $0.05`, naming no blind | money still short in 1 hand in 60 |
| seats listed at the table but **not dealt in** ("waits for big blind") | pool showdown rate 20 points too high |
| Ignition writes an all-in **raise** as an ordinary raise | players who could not act looked like players who declined to |
| "fold to cbet" was really fold to *anyone's* bet | engine disagreed with the old derivation by 27 rows |

Every one of those would have produced a complete, plausible, wrong report.

---

## Before you change anything

```bash
cp hands.db hands.db.bak
python check.py
```

Know it was green before you started. `hands.db` is gitignored and is the
only copy.

## After you change anything

```bash
python check.py
python parity.py            # A–H on the committed fixture corpus
```

**A change to a derivation is not done until its check passes.**
In-memory tables in `query.py --check` prove the functions; they
do not prove the import. `parity.py` loads `fixtures/parity/`
through `importer.load` and `CHAIN` and is the gate that a
derivation which only looks right against a typed table fails.
CI runs both. The live `hands.db` is still the user's.

If your change makes a check fail, one of two things is true and you have to say
which:

1. The change is wrong. Fix it.
2. The check's expectation is wrong — usually because you have *corrected*
   something the old code got wrong. Then update the check to record the
   difference **and the reason for it**, so it stays a decision somebody
   made rather than a thing everybody stopped looking at.

`stats.py --check` has a column for exactly this. Three of its rows are
marked `KNOWN` with a sentence saying why the engine deliberately does not
reproduce what `spots` says.

---

## Build order

The tables are derived from each other. Rebuilding one without the ones
below it leaves a database that is internally inconsistent in a way no
single check would catch.

```
importer.py                     raw hands, by each site's parser (sites.py)
        v
spots.py                        one row per player per hand
        v
decisions.py                    one row per decision
        v
stats.py / opponents.py           read only, never rebuild
```

---

## Where new work goes

**Adding a statistic** — add a `Stat(...)` to the registry in `stats.py`.
That is the whole change.

```python
Stat("probe_turn", "probe turn",
     "street='turn' AND is_pfa=0 AND checked_to=1 AND first_in=1 "
     "AND facing='check'", "agg=1", group="turn",
     note="betting a turn the preflop raiser gave up on"),
```

**Do not write a new module and do not write SQL anywhere else.** Six
modules already hardcode their own queries; that is the mistake this design
exists to stop, and the measure of success is that their number goes *down*
while the number of answerable questions goes up.

If a stat cannot be expressed as two filters over `decisions`, the missing
thing is a **column on `decisions`**, not a script. Add it there, rebuild,
and the stat becomes a definition like all the others. That is how `was_agg`
and `vs_pfa` came to exist.

**Adding a site** — a parser module and a registry entry, and nothing
else. The module provides `HEADER(line)`, `split_hands(text)` and
`parse_hand(block, source)` returning the dict shape `acr.py` and
`ignition.py` return: every column of `hands`, the seats with `won` being
what came back from the pot and never profit, the actions with positions
named as the rest of the program expects. The entry in `sites.SITES` says
whether a label is a person, whether folded hands are shown, whether the
history states the rake, and where the client keeps its files. The loader,
the schema and the checks are `importer.py` and `sites.py`; a parser never
writes a row. Then `python sites.py --check` -- the new site must pass the
same money, positions and blinds tests the first two do, and the money
test has caught a real bug in each of them. Nothing outside the parser and
the registry should need to know the site exists; if something does, that
is the bug to fix, not a place to add the name.

**A game is the same idea one noun over.** `games.py` names HOLDEM /
OMAHA / OMAHA5, hole counts, `--game` words, and the three Game
Types (`nlhe-cash`, `plo4-cash`, `plo5-cash`). Parsers ask
`games.of` on the header; pool queries default to Hold'em.
`--game-type` is the variant plus cash. Scoring four hole cards as
Hold'em is a plausible lie -- strength uses two of them plus three
board cards, equity refuses the rest, and the 13×13 is gated.

Never write a parser from memory of a format. Without sample files there
is nothing to run the checks against, and a parser that has not been
checked is one that is dropping lines quietly.

---

## Rules for any number that gets reported

These are not style preferences. Each one is here because a figure was
reported that should not have been.

- **No percentage without its `n`.** A rate on 12 chances is not a read.
- **Name what it is a percentage OF.** "26% of SB's continuing range" is 9%
  of hands dealt, and the reader hears the second one.
- **Say where it sits among comparable spots.** Quoting the maximum of a
  distribution as though it were typical is how a true number misleads.
- **Split-half or it did not happen**, for any population finding. Measure
  on the first half of the sessions and the second half separately, and
  report only what agrees. Thousands of hands will happily produce a dozen
  exciting fictions.
- **Two rates are compared by an interval on their difference, never by
  whether their own intervals overlap.** `stats.difference` gives the
  Newcombe interval and a p-value; `stats.holm` charges a family of
  comparisons for its size; `stats.detectable` says what could have been
  seen when nothing was. The overlap test was the rule until 5 Sep 2026
  and it hid a real difference in this database.
- **Check every EV against the bounds of the pot it came from.** An
  impossible number is the only kind that gets caught for free — this rule
  exists because a hand once priced at 11.8bb of EV in a 5.5bb pot, which
  was the only reason a wrong combo ordering was ever found.
- **Refuse a goal the data cannot support**, and say why, rather than
  padding the output. 51 profileable opponents cannot tell a bot from a
  tight player, and no amount of code changes that.

## Intervals, specifically

Use `stats.wilson`, never `p ± z·sqrt(p(1-p)/n)`. At the sample sizes that
decide things here the textbook interval fails in the way that matters most:
a player who folded 3 of 3 gets an interval of zero width, and one who folded
0 of 8 gets a lower bound below zero.

---

## House style

The comments are worth more than the code and are held to a higher standard.

- Explain **why**, not what. Say what would go wrong without the line, and
  name the real failure that motivated it where there was one.
- Prose, in full sentences. Not bullet lists, not restatements of the code.
- Module docstrings open with what the module is for and what would
  otherwise go wrong — the argument for its existence, not a summary.
- No comment on a self-evident line.
- Match the surrounding code. It is consistent, and it is the spec.

A comment that says *"the seat list is the room; the hand is whoever put
money in or acted"* is worth ten that say *"filter the seats"*.

---

## How the project is run

**`the-augster.xml` is the operating framework.** Adopted wholesale on
2 Sep 2026, replacing the earlier three-role CEO/PM/programmer structure and
its approval chain, which conflicted with the `Autonomy` maxim.

Every mission runs the same four stages: distil a `Mission`, decompose it
into a `Workload`, research away every assumption, harden it into a
`Trajectory`, critique that adversarially until nothing is left to find,
execute it, then audit against a checklist built from the plan itself.
Output goes in the numbered sections `## 1. Mission` through
`## 10. Summary`. `CLAUDE.md` has the tool mapping and the two places the
framework has to be adapted to this harness.

Several of its maxims are the rules this repo already learned the hard way,
and they are the ones that bind hardest here:

- **`EmpiricalRigor`** — no assumptions during planning, implementation or
  verification. Resolve them with a tool or ask. This is the whole argument
  for the checks: a parser that fails silently cannot be caught by reading
  the code, only by a number that would move.
- **`Perceptivity`** — know what a change does to its callers. Violated on
  2 Sep 2026: adding ACR changed what `fmt='RING'` selects, and four
  modules filtering on it were left alone.
- **`AppropriateComplexity`** — minimum necessary complexity, over- and
  under-engineering equally wrong.
- **`PurityAndCleanliness`** — remove what you replace, in the same change.
  Six modules still hardcode their own SQL; retiring them is the measure.
- **`Consistency`** — the surrounding code is the spec.

`ROADMAP.md` is the run log. Every run records the goal **set beforehand**
and whether it was met — including the ones that failed, and what the
failure cost. Back-filling a goal that was obviously met is not allowed and
has been marked "unscored" instead.
