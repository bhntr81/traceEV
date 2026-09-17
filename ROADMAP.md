# poker_analysis — roadmap and run log

Three parts, in the order they happened. Part I is the run log of the
analysis work this repository grew out of: real measurements, three
post-mortems that cost a day each to learn, and standing rules on evidence
that still bind. Its long-term goal is not this project's any more, and the
solver modules it describes were removed on 5 Sep 2026. Part II is the
decision to build a tracker, and the plan. **Part III is the current
vision and the current state** -- start there.

---

# Part I — AI hand analysis, the run log

**Long-term goal.** A tool that reads the Ignition hand history database and
finds, with evidence: (1) exploits against the population pool, (2) seats
that bet like bots, (3) leaks in hero's own game.

**Status of the OCR screenshot reader.** It was fixed as the long-term goal
earlier today and explicitly excluded a general analysis tool. That has now
been replaced. The OCR work is not deleted, but nothing here advances it —
if it is still wanted, it is a second track and needs its own goals.

---

## What the data can and cannot support

This section exists so goals are set against reality rather than against
hope. It gets updated as the corpus grows.

| | |
|---|---|
| Hands loaded | 3,942 (20–26 Aug 2026) |
| Ring / Zone / MTT | 3,143 / 749 / 50 |
| Hole cards seen | 18,802 of 18,803 seats — **the whole deal, folds included** |
| Per-action timing | **Not recorded by Ignition.** Timing tells are impossible |
| Player names | **Not recorded.** Identity is `table:seat`, ring only |
| Longest seat profiles | 16 seats with 100+ hands, 53 with 50–99 |

Three consequences the PM should hold the programmer to:

1. **The pool work is well supported; the bot work is not, yet.** Seeing
   every folded hand means population ranges can be *measured*, not guessed
   — no other site allows this. But 100 hands on a seat cannot distinguish a
   bot from a tight player. Bot detection is corpus-limited, not code-limited.
2. **Hand volume is a first-class metric.** More sessions loaded moves the
   goal further than more code does. Tracked below alongside features.
3. **No timing analysis.** Any plan that leans on bet-timing is dead on
   arrival; sizing quantisation and frequency rigidity are what is left.

## Standing rules for any finding

- **No percentage ships without its `n`.** A rate on 12 samples is not a read.
- **Split-half or it did not happen.** A pattern found in the first half of
  the hands must survive the second half before it is called an exploit.
  3,942 hands will happily produce a dozen exciting fictions.
- **Goals are numbers, checked by a command.** "Better analysis" is not a
  goal. "`population.py` reports ≥5 spots that survive split-half" is.
- **The programmer may refuse a goal the data cannot support**, and say why.
  The PM then changes the goal, rather than the programmer padding the output.

---

## Milestones

- [x] **M1 — Derivation layer.** One row per player per hand with the pot
  rebuilt, chances separated from actions, sizing in pot-fractions.
  `spots.py` → `spots` (18,455 rows), `bets` (6,743 rows).
- [ ] **M2 — Equity engine.** A hand evaluator, so "what did they actually
  hold" becomes "what was it actually worth". Everything below needs it.
- [ ] **M3 — Population ranges.** For each spot, the literal set of combos
  the pool takes each action with, versus what is defensible. The centrepiece
  — this is the part only Ignition data can produce.
- [ ] **M4 — Hero leak finder.** True EV loss per decision, computed against
  villains' known cards rather than against results.
- [ ] **M5 — Seat profiling / bot signals.** Sizing entropy, frequency
  rigidity, session shape. Gated on corpus size.
- [ ] **M6 — The AI layer.** Claude reads the compact stat tables and writes
  the exploit and leak narrative. Reasoning over numbers, never over raw hands.

## Run log

Each entry: the goal set **before** the run, then whether it was met.

### Run 1 — derivation layer (M1)

**Goal set beforehand:** none. The two-role process was being defined while
this run was in flight, so there is no goal to check against. Recorded as
unscored rather than back-filled with a goal it obviously met.

**Result:** `spots.py` built and run. Sanity figures all land in micro-stakes
range — VPIP 29.7 / PFR 17.0, fold-to-steal 66.5%, cbet 52.2%, WTSD 28.7%,
open sizes clustering at 2.5bb and 3.0bb. Wrong-looking output would have
shown here; none did.

**Distance to long-term goal:** 1 of 6 milestones. The foundation the other
five stand on is in place, which is more than 1/6 of the work.

### Run 2 — population ranges and pool leak map (M2)

The equity engine was set as this run's goal and then overruled before it
started. It is infrastructure that produces no poker insight, and it does
not in fact block the range work — `spots` already carries every combo,
folded ones included. Equity is only needed to *judge* postflop play. The
larger untested risk was whether any finding at all survives validation at
this corpus size, so that was tested first instead.

**Goal set beforehand:** `population.py` produces a ranked pool-leak map and
revealed range grids, and **≥5 findings survive split-half** — n≥150 in each
half, frequencies agreeing within 8 points.

**Result: PASS. 22 of 22 findings survived, none failed.** Preflop
frequencies are stable at this corpus size; the largest half-to-half gap was
2.6 points (BTN 3-bet). The split was by date rather than at random, which is
the harder test — a random split shares tables between halves.

**What it cost to be honest.** The money column was noise and the first draft
would have shipped it as a discovery. One hand's result has a standard
deviation of 11.7bb, so bb/100 over 100 hands carries an error of ±117. Error
bars were added and the leak table now reports only lines clearing twice
their own error: **2 of 15 survive** (CO limp −365 ±155, BB cold-call −167
±78). Folds are excluded — "folding the small blind loses 50bb/100" is
arithmetic, not a leak, and it would have topped that table forever.

**The standing split is now measured, not guessed:** frequencies are
measurable at 3,942 hands; win rates are not. Both improve only with hands.

**Distance to long-term goal:** 2 of 6 milestones, and the first real
findings exist — the BB's fold-to-steal grid shows offsuit aces (A7o–A3o)
folded 57–75% of the time, and the pool's 3-bet range is value-heavy with
thin bluffing below AJ.

### Run 3 — GTO Wizard as a baseline oracle (M3)

Requested directly: give the tool the ability to look hands up in GTO Wizard,
so leaks are measured against a solver rather than against assumption. This
replaces the hardcoded reference range the leak finder would otherwise need,
and it supplies EV directly — which is also why the equity engine drops off
the plan for now rather than moving down it.

**Two problems with the existing code, found before setting the goal:**

1. It is pointed at **PLO**. `step2_build_url.py` builds
   `/solutions/plo/strategy` with gametype `PLO4Cash6mAntePLO100SimpleAI`.
   The hand database is NLHE. The NLHE gametype string and endpoint are
   unknown and have to be discovered from the live app.
2. It drives the **UI**, clicking betting-tree buttons over CDP and reading
   `data-tst` attributes. That is seconds per node. Scoring 3,942 hands that
   way is days of clicking, and it breaks whenever the front end changes.

**The architecture that makes it work: fetch once, score offline.** Preflop
at 6-max has only a few hundred distinct nodes per gametype and depth. Pull
each one once into a `solutions` table keyed by node, and scoring every hand
in the database becomes a local join that runs in a second. Postflop is
unbounded and stays on-demand, for flagged hands only.

**Goal set beforehand:** `gtow.py` fetches the NLHE 6-max preflop strategy
for a named node and caches it, verified by: (a) ≥20 distinct preflop nodes
cached; (b) each cached node's frequencies sum to 100% (±1) per combo across
its available actions; (c) a second run of the same nodes hits the cache and
makes zero network calls. Pass = all three. Fail = report which of the two
problems above blocked it, rather than half-fetching.

**Result: PASS on all three.** 250 nodes cached; 0 combos miss 100% (worst
miss 0.0001); a second run reported "0 new, 60 already had" without opening a
browser.

**What was found.** The endpoint is
`api.gtowizard.com/v4/solutions/spot-solution/`, taking `gametype`, `depth`
and a `-` separated `preflop_actions` sequence (`F-F`, `R2.5-F`). The
gametype for this pool is **`Cash6mGeneral_6mNL25R25`** — 6-max NL25 *with
rake*, which is the game actually being played, not an approximation of it.
Auth is a bearer token that lives in page memory rather than storage, so it
is taken from a request the app makes for itself and refreshed the same way
if it expires mid-walk.

The reply carries a 169-float `strategy` array **and an `evs` array** per
action. EV comes free, which is why the equity engine is now off the plan
entirely rather than merely deferred.

**The combo order is self-describing.** The payload names its own hand order
in `simple_hand_counters`, so nothing is hardcoded. Decoding the root node
this way reproduced UTG RFI at 17.49%, matching the payload's own
`total_frequency` of 0.1749 — an arithmetic check that the order is right,
not a visual one. The RFI ladder from cache is textbook: UTG 17.5, HJ 21.7,
CO 27.9, BTN 40.6, SB 34.4.

**Distance to long-term goal:** 3 of 6 milestones, and the baseline problem
is solved — leaks can now be measured against a solver instead of an
assumption.

### Run 4 — hero leak finder (M4)

Blind breadth-first fetching is stopped here: 323 nodes were still queued at
the cap, and fetching a tree nobody asks about is the wrong shape of work.
From now nodes are fetched **on demand, driven by hands that actually
occurred**, which caches exactly what gets used.

**The problem this run has to solve honestly.** Real players do not use
solver sizings. The pool opens 2.5bb *and* 3.0bb; the solver node offers
`R2.5` only. Limped pots have no node at all. So hero's action has to be
snapped to the nearest node the solver offers, and some hands will not map
at all. What fraction maps is a fact to be *measured and reported*, not
quietly hidden by scoring only the hands that happen to fit.

**Goal set beforehand:** `leaks.py` maps hero's preflop decisions onto cached
solver nodes and reports EV loss per decision, verified by: (a) the mapped
fraction of hero's preflop decisions is reported explicitly and is ≥60%;
(b) EV loss is never negative — hero cannot beat the solver at a solved
node, so a negative loss means the mapping is wrong, not that hero is
brilliant; (c) the biggest single leaks are ranked with n and bb lost.
Pass = all three.

**Result: PASS on all three.** Coverage 99.0% (3,144 of 3,175 hero preflop
decisions priced) against a goal of 60%. Zero decisions priced as a gain.
Leaks ranked with n and bb lost.

**Hero loses 1.3 bb/100 preflop**, 51.3bb over 3,144 decisions, with 92.5% of
decisions costing exactly nothing. Preflop is not where hero's money is going.

**What the fit filter caught.** The first ranking put "UTG shoves JJ/TT" at
the top. It was an artifact: a villain's min-3-bet had been snapped to the
solver's full 8bb 3-bet, so hero was priced in a spot they were never in.
The walk now carries `path_gap` -- how far the actions IN FRONT of a player
had to be bent -- and the rankings use only well-fitted paths (2,523 of
3,144), while the totals keep everything. The worst-fit decisions are exactly
the ones that float to the top of a leak table, so this is not a detail.

What survives is credible and small: flatting AA on the button instead of
4-betting (2.65bb), flatting Q9s and QTo where folding is better.

**A caveat that stands.** Everything is priced at the 100bb solution, and
hero's median effective stack is 114bb with 36% of hands between 125 and
250bb. Deep hands are priced slightly wrong. Short ones are not a problem --
only 1.4% are under 75bb.

### Run 5 — the population's own preflop tree (M3)

**Goal:** set by the user mid-run rather than in advance, so this is scored
on what is checkable rather than against a number fixed beforehand.

**Result.** `poptree.py` places all 14,124 preflop decisions in the solved
tree and puts the pool beside the solver at the same nodes with the same
hands. Two reporting faults were found and fixed before the numbers were
believed:

  * A cold seat facing a 3-bet is offered no flat by the solved tree, so
    reporting cold seats and the opener in one row drove "flat" to 0.0% and
    read as a fact about the pool. Now split, with the constraint stated.
  * Actions the tree cannot represent are counted per depth rather than
    dropped silently -- 722 at the unopened pot, which are limps.

**The finding, and it is one finding at three depths.** The pool opens at
almost exactly solver frequency (28.2% vs 28.4%; UTG 16.7 vs 16.2, BTN 40.8
vs 40.6). Their opening ranges are not a leak. What they cannot do is raise
in *response*:

| facing | pool raises | solver | pool flats | solver |
|---|---|---|---|---|
| an open | 7.4% | 9.9% | 23.2% | 9.7% |
| a 3-bet (as opener) | 11.0% | 20.0% | 35.2% | 15.3% |

Under-raising and over-flatting, at every depth and every position. Their
3-bet range is also missing its bluffs -- KQo 26% against the solver's 66%,
A7s 0% against 38%, A2s 17% against 54% -- so their 3-bets can be believed.

Sample sizes at the 4-bet depth are thin (383 decisions, 40-125 per seat).
The effect is large relative to that, but it is the row to re-read as the
database grows.

### Run 6 — per-opponent preflop profiles (M5)

The population aggregate is settled. The request now is per-opponent: how
much each seat 3-bets, what with, what bluffs they lack, how far from the
solver, by position. The obstacle is not code, it is that identity on
Ignition is `table:seat` and only 16 seats have 100+ hands.

**Goal set beforehand:** `opponents.py` reports per-seat preflop profiles
against the solver, verified by: (a) every reported deviation carries its own
error bar, and no seat is described as deviating unless the deviation clears
twice it; (b) the number of seats clearing that bar is reported explicitly,
including if the answer is zero; (c) split-half is not required here, but any
seat profiled on fewer than 30 decisions is excluded and counted. Pass = all
three, INCLUDING an honest report of zero if the corpus cannot support it.

---

## Post-mortem — three reporting failures, one cause

Called by the user after the SB 4-bet figure did not survive scrutiny.

**What happened, in order.**

1. *Run 2.* bb/100 figures ranked as findings when their error bars were
   larger than the effect. Caught in-run by the programmer.
2. *Run 5.* The "solver" column held the hand-matched frequency but was
   labelled as the spot frequency. Two quantities, one label, five points
   apart. Caught by the user.
3. *Run 5 again.* SB 4-bet reported as the headline deviation. The figure was
   arithmetically right (26.0% of SB's continuing range, verified against the
   payload's own combo counts) but it is the **maximum** of the distribution:
   across all 34 genuine opener-facing-a-3-bet nodes the solver's 4-bet runs
   13.6%–26.0%, median ~18.5%. Caught by the user.

**The single cause.** Point estimates were shipped without the context that
gives them meaning. The standing rule "no percentage without its n" catches
only one third of it. A frequency needs three things, not one:

  * **what it is a percentage OF** — 26% of SB's continuing range is 9.0% of
    all dealt hands, and the reader hears the second;
  * **its n** — the SB 4-bet row is 66 decisions;
  * **where it sits in the distribution** of comparable nodes — quoting the
    maximum as though it were typical is how a real number misleads.

**New standing rule.** Every solver or population figure ships with its
denominator named, its n, and its position among comparable nodes. A single
node may not be quoted as a headline without the spread it came from.

**A real bug found while checking, now fixed.** `facing` counted an all-in as
a raise, so nodes where a player faced a shove — where raising is impossible
and the solver's raise frequency is 0% by force — were being averaged into
the 4-bet comparison. It happens not to move these numbers, because this pool
rarely shoves preflop, but it would have.

**What this does NOT fix, and it gates the deep claims.** The pool's range at
a node is not the solver's range at that node — the pool's SB opens 38.6%
where the solver opens 34.4%, so it arrives wider and weaker, and 4-betting
less with a weaker range is partly correct rather than purely a leak. Every
deviation reported at the 3-bet and 4-bet depth is inflated by an unknown
amount by this. It cannot be filtered away; the comparison has to be
conditioned on the hand, which is what the hand-matched number was for.

**Standing until fixed:** the facing-an-open findings (n=4,499) hold. The
4-bet-depth findings (n=383, 40–125 per cell) are provisional and must not be
quoted as fact.

## Cache audit — prompted by the user doubting it

Both doubts were justified; one was wrong, one was right.

**Accuracy: verified.** Ten cached nodes re-fetched and diffed field by
field -- hero seat, combo order, action codes, and every strategy and EV
float. Maximum drift 0.0e+00: exact equality, not "close enough". The cache
is faithful to what the site returns.

**Completeness: it was not the whole tree, and still is not.** The cache was
297 nodes with 424 known children uncached -- 41% of even its own frontier.
It is now 1,499 nodes, and the frontier is 1,263, all at depth 8-9. The tree
is deeper than estimated and each level multiplies.

For reading real hands this no longer matters: uncached nodes now cost 3 of
3,208 hero decisions (was 29) and 18 of 14,822 pool decisions (was 554). For
a preflop solver it would matter, because a solver needs the whole tree, not
the shallow slice real play visits.

**Three bugs found by this audit, all real:**

1. `capture_auth` accepted `Bearer null` -- an eleven-character placeholder
   the app sends before it has authenticated. Runs printed "authorised" and
   then 401'd on every request. Now requires a real JWT.
2. `release_profile` killed Chromium and launched immediately, before the
   processes had died, and never removed the stale `SingletonLock` a killed
   browser leaves behind. That is why "profile already in use" kept coming
   back. Now waits for the processes to actually go and clears the locks.
3. `Browser.open()` waits for a login internally -- right for unattended
   runs, wrong for a script that wants to click the login button itself,
   which it blocked for seventeen minutes. Scripts that drive the login now
   launch the browser directly.

**Unexplained and still open:** 129 hero decisions are dropped as "tree
disagreed" -- the position of the player to act does not match the seat the
solver names. Most likely the 362 non-standard hands (missed blinds, dead
seats) that `spots.py` already flags, but it is not confirmed, and until it
is those 129 are an unknown rather than a known exclusion.

### Run 6 — the exploitative chart (best response to the measured pool)

The 129 "tree disagreed" cases were run down first, because the engine reads
the same walk: **all 129 are `standard=0` hands** -- the ones `spots.py`
already flags as having an untrustworthy label set. The position arrives as a
raw Ignition label (`UTG+1`, `UTG+2`) because `positions_for()` passes labels
through unmapped when the table shape does not parse. A known exclusion now,
not an unknown one.

**What was built.** `bestresponse.py`. No solving: the opponent's strategy is
fixed because it was counted, so the best response is one walk taking the
highest-EV action at each node. The honest part is the decomposition -- the
solver's EV for a raise mixes fold equity with the value of playing on, so
the play-on term is recovered from the solver's own numbers and recombined
with the POOL's measured fold frequency. Fold equity measured, continuation
borrowed, and the borrowing is printed in the output.

**A modelling error caught by its own output.** The first run said to 3-bet
32 offsuit, and rewrote 141 of 169 hands. Cause: fold equity was read from
the single next node. When the SB 3-bets a button open the BIG BLIND acts
first and the button after, so "they fold" is the whole remaining table
folding, not the next seat. `fold_through` now walks the all-fold branch to
its end and multiplies, carrying the thinnest sample along the chain. A
guard now also flags any spot where more than half the chart moves as broken
rather than as a discovery.

**The finding, and it points the opposite way to intuition.** In both spots
with enough data, fold equity is *lower* than the solver assumes -- BB vs a
BTN open 59% against 71%, BB vs an SB open 56% against 65%. This pool does
not fold to 3-bets enough. So the correct adjustment is to 3-bet **less**,
not more: AQo, AQs, KQs, TT, A5o, A7o and similar move from 3-betting to
flatting. It corroborates the aggregate, where the pool folds to 3-bets 53.8%
against the solver's 69.0% (n=383).

**Coverage is the limit, and it is severe.** 2 spots had enough data; **107
were skipped**. The thinnest link in the surviving chains is n=66. The
direction agrees across both spots and with the independent aggregate, which
is corroboration rather than proof. This is corpus-limited, exactly as the
constraints section said bot detection was.

**Distance to long-term goal:** 4 of 6 milestones, and the centrepiece
exists: a chart that says what to do against these opponents rather than
against imaginary ones.

## Correction — Run 6's direction was wrong, and why

The user challenged the claim that this pool under-folds to 3-bets, on the
grounds that people over-fold in certain spots, BTN facing a BB 3-bet
especially. Checking it overturned the finding.

**Fold-to-3-bet is enormously size-elastic**, and pooling sizes destroyed the
measurement. BTN, having opened, facing a BB 3-bet:

| BB's actual 3-bet | n | BTN folds |
|---|---|---|
| =<7bb | 10 | 20.0% |
| 7-9bb | 18 | 22.2% |
| 9-11bb | 15 | 40.0% |
| **11-14bb** | **55** | **69.1%** |
| 14bb+ | 14 | 35.7% |

The solver node models a 13.5bb 3-bet. **At the matching size the pool folds
69.1% against the solver's 70.7%** -- they are not under-folding at all. The
reported 59%, and the "3-bet less" advice that followed, came from averaging
in small 3-bets that the tree cannot represent.

`bestresponse.py` now discards any observation whose path was bent more than
15% to fit the node. With that filter **no spot has enough data** -- 0 of
109, even at a lowered threshold of 25. That is the honest state of the
exploit chart: the corpus cannot support it yet at matched sizes.

**The deeper limitation, which was there from Run 3 and went unnoticed.**
`Cash6mGeneral_6mNL25R25` is a simplified tree offering the BB exactly ONE
3-bet size, 13.5bb -- 5.4x a 2.5bb open. The commonly quoted "BTN folds
54-55% to a BB 3-bet" is for a standard ~4x, a node this tree does not have.
Both figures are right for their own sizing; the baseline was never as
general as it was being treated.

The captured traffic names richer trees -- `Cash6m50zGeneral3betV2`,
`Cash6m50zGeneral25Open3betV2` -- and an endpoint that lists them,
`/v4/game-modes/?variant_in=NLHOLDEM&format_in=Cash`. None of the richer
sizings visible so far is an NL25-with-rake tree, so switching trades sizing
fidelity against matching the actual stake and rake. That is a judgement for
the user, not a default.

**Standing rule added:** a solver baseline must have its betting tree
described alongside its numbers -- how many sizings, and which. A single-size
tree quoted as "the solver" is the same error as quoting one node as a
distribution.

---

# CEO review

## Is it producing results?

The ledger, counted rather than felt:

**Findings that have survived every challenge (3):**
- The pool over-flats opens by +13 points, every seat, n=4,499, split-half stable.
- CO limp -365 +/-155 bb/100; BB cold-call -167 +/-78. Both clear twice their error.
- Hero's preflop play is near-clean: -1.3 bb/100 against the solver, 92.5% of
  decisions costing exactly nothing.

**Findings retracted after being reported as fact (3):**
- "The pool under-folds to 3-bets, so 3-bet less."
- "The pool over-folds by +14.5 points."
- "SB 4-bets 26% where the solver wants 31.4%."

**The uncomfortable part.** The over-flatting finding existed at Run 2, from
`population.py`, before GTO Wizard was touched. Runs 3 to 6 -- the majority of
the effort, the library, the 1,499-node cache, the walk, the best-response
engine -- have produced **no surviving exploit finding**. They produced good
infrastructure, one solid negative result (no fold-equity edge at hero's
sizings), and three retractions.

## Is the logic faulty?

The statistical apparatus is sound: split-half, error bars, sample gates,
soundness checks. It has never once let noise through.

**But every modelling error got past it.** Six analytical errors this
session; three were caught only by the user's poker knowledge, not by any
check in the code. Split-half cannot detect that two different quantities
share a label, that a baseline is for a different bet size, or that the
maximum of a distribution is being quoted as typical. The validation catches
noise and is blind to category errors.

That is the structural weakness, and it is not fixed by more statistics.

## The number that decides where to go

| | |
|---|---|
| Hero's actual result | **+1.0 bb/100 +/- 19** over 3,802 hands |
| Hero's preflop EV loss vs solver | **-1.3 bb/100, no variance** |

The result is statistically indistinguishable from zero and will stay that
way for tens of thousands of hands. The solver-EV figure is exact on the
first hand. **Measuring against a solver is roughly twenty times more
statistically powerful than measuring against results** -- and that is the
real product of Runs 3-6, even though it produced no exploit.

And it has been applied only to preflop, which is worth -1.3 bb/100. Only
20% of hands see a flop, but that is where the pots are large and where every
consequence of the pool's over-flatting is actually realised.

## Direction

**1. Pivot from exploitation to leak-finding.** Exploits need a corpus we do
not have -- 4 of 109 spots have data. Leaks need none: exact EV works on the
first hand. Exploitation comes back when the database is large; leak-finding
pays now.

**2. Go postflop, through the machinery already built.** The same endpoint
takes `board`, `flop_actions`, `turn_actions`, `river_actions`. Hero saw 770
flops; fetching those nodes on demand is thousands of requests, not millions,
and the cache and walk already handle it. This is the one move that uses
everything built and aims it where the money is.

**3. Add an adversarial check for modelling errors, not statistical ones.**
Every reported figure must carry: what it is a percentage OF, the baseline's
bet sizes, its position in the distribution, and what would falsify it. Three
of the six errors would have been caught.

**4. Stop adding analysis surface until 1-3 are done.** Seven modules exist.
The constraint is not coverage.

### Run 7 — postflop (M4 continued)

**Blocked, then unblocked.** Every request carrying a board returned
`PERMISSION_DENIED: SolutionsPermission`. Two probes established it was
entitlement rather than format -- the same request without a board returned
normally -- and the cause was a lapsed GTO Wizard subscription, since
resubscribed. Five minutes of probing rather than a postflop pipeline that
403s on every hand. It probably also explains the repeated session drops and
the `Bearer null` token.

**Format, learned in one pass.** Board accepted in any spelling (sorted,
dealt order, spaces, uppercase). `flop_actions` uses the same grammar as
preflop -- `X` / `F` / `C` / `R{size}`, dash separated, sizes in bb -- and
hero rotates correctly, the flop root being the out-of-position seat.

**The cache had to be rebuilt.** Its primary key was
(gametype, depth, preflop_actions), which would collide the moment two boards
shared a preflop line. SQLite cannot alter a primary key, so the table is
recreated and the 1,499 preflop rows carried across.

**A near miss worth recording.** Postflop, the strategy arrays are **1326**
long -- one entry per exact combination -- while `simple_hand_counters` still
lists only the **169** hand classes. Preflop both were 169, so indexing by
class worked; postflop it silently reads the wrong slots. It surfaced only
because a hand came back with 11.8bb of EV in a 5.5bb pot. Had that number
been merely wrong rather than impossible, Run 7 would have produced a
complete, plausible, entirely fictional postflop leak report.

**The ordering is proven, not guessed.** Nothing in the payload names it, so
candidates were tested against three constraints on two different boards:
summed range weight per class matching `total_combos`, unblocked combo counts
matching `total_combos_available`, and no combo containing a board card
carrying weight. Exactly one ordering satisfies all three -- cards
rank-descending with suits s h d c, combos every i<j pair reversed. Every
other suit permutation puts weight on a face-up card.

Poker logic confirms it independently: on the two-heart flop Jh3h2s, `Th9h`
prices at 2.69bb and `Ts9s` at 0.66bb. Same class, four times the value,
because one has the backdoor flush draw. A wrong ordering could produce
noise; it could not systematically favour the suit matching the board.

**Standing rule added:** every EV must be checked against the bounds of the
pot it came from. That single check would have caught this automatically, and
it costs nothing.

**Run 7, where it actually stands.** The walk WORKS. Run offline against the
eleven postflop spots already cached, it prices decisions and passes both
soundness checks -- no loss larger than its own pot, none below zero -- and
the street-transition code, the piece that had never executed, executes
correctly. Hero checked where checking was best and it cost 0.00.

Projected coverage from the same offline run over all 653 hands: 489 stop
only because their node is not cached yet, 163 are genuinely unmappable
(limped pots, which a solved 6-max tree does not contain), 1 ended. So about
**75% should price** once fetching completes.

**The only thing outstanding is a token.** `python gtow.py login`, once.
After that fetching is plain HTTP and the run is unattended.

**Two self-inflicted architecture errors cost most of this run.** Keeping a
browser alive to make HTTP requests, when the browser was only ever needed to
mint a token -- that produced a MemoryError, a broken headless login, a
renderer crash, and a run that spent fifteen minutes on a login page nobody
was watching. Then switching from the persistent profile to a `storage_state`
file that had never been written successfully, which discarded the working
session and made every launch afterwards start logged out. Both are fixed;
neither should have happened.


---

# Part II — building our own tracker

Hand2Note is a database, a stat engine and a way to look at both. Nothing in
it is beyond this project; what it has is coverage and a UI, and what we
have is data neither it nor anything else can get. So we build our own.

## How this part is run

Three roles, each answering to the one above, and the user above all three.

| role | decides | approved by |
|---|---|---|
| **CEO** | the vision, the odds of it working, long- and short-term goals for the PM | the user |
| **PM** | what the programmer builds this iteration, and the number that says it worked | the CEO |
| **Programmer** | how it is built, and what it refuses to build | the PM |

Five iterations, then a review in which the CEO scores the PM: how close the
goals are, whether the approach is the right one, and what to change. Then
the user is consulted.

## CEO — the vision, and the odds

**The vision.** One tracker over ACR and Ignition that answers any
question about either pool, or about one named player, without new code
being written for the question.

**Why this can work, and it is not the obvious reason.** The reason is not
that we can reimplement a HUD. It is that the two sites are complementary in
exactly the way that matters, and no commercial tracker joins them:

  * **Ignition shows every folded hand.** The pool's range at a spot can be
    *counted*. Not inferred from showdowns -- counted, all 18,802 of them.
  * **ACR names every player.** 777 opponents, 89 of them with 100+
    hands. Identity persists across months, which is what a profile needs.

Ignition measures the population. ACR measures the person. A tracker
holding both can price a named ACR opponent against a range that was
*observed* on Ignition rather than assumed -- the strongest form of the
prior-and-update every HUD gestures at and none of them can actually do.

**Odds.** High for the engine, and the risk is not technical. Seven analysis
modules already exist and each hardcodes its own SQL; the failure mode of
this project is an eighth. Success means the count of hardcoded modules goes
DOWN while the count of answerable questions goes up.

**Long-term goals, in order.**

1. Both sites in one schema, verified by checks that would catch a misread.
2. A decision layer: one row per decision, carrying the situation, so a stat
   is a filter rather than a column.
3. A stat engine: any stat, any filter, with n and an error bar, by name.
4. Player and pool reports built on that engine, not on new SQL.
5. The join that only we can make -- Ignition range as prior, ACR
   player as evidence.

**Short-term goal for the PM:** iterations 1-5 deliver 1-4, each with a
number checked by a command. Iteration 5 proves 5 is reachable or reports
that it is not.

## Run 8 / iteration 1 — ACR into the same schema

**PM's goal, set beforehand:** `acr.py` loads the ACR histories
into the existing hands / seats / actions tables, verified by (a) money adds
up in ≥99% of hands -- everything in comes back out minus the house's cut;
(b) positions are balanced across the six seats to within 2%; (c) blinds are
posted by the seats the button says are the blinds; (d) the named-opponent
count is reported, and ≥20 have 100+ hands, or the identity premise of the
whole plan is wrong. Pass = all four.

**Result: PASS on all four.** 8,019 hands added from 146 files.

```
money adds up           99.91%  (8012/8019 hands within a cent)
positions balanced     100.00%  (6 positions, 4242-4242 each)
blinds posted by blinds 99.09%
named opponents           777  (306 with 30+ hands, 89 with 100+, 7 with 500+)
```

**What the money check caught, twice.** It failed first at 79.4%, then at
98.4%, and both causes were real:

1. ACR takes a **jackpot fee** as well as rake -- `Total pot $1.52 |
   Rake $0.05 | JP Fee $0.02`. Counting only the rake leaves a hole in one
   hand in five.
2. A returning player posts with a bare `posts $0.05`, naming no blind. The
   regex wanted a blind named, so that money vanished from the pot.

Neither would have crashed anything. Both would have quietly biased every
win-rate and every pot-size-relative bet in the ACR half of the
database. This is the argument for the check existing at all.

**What the corpus now is.**

| | Ignition | ACR |
|---|---|---|
| hands | 3,942 | 8,019 |
| seats | 18,803 | 45,873 |
| with hole cards | 100.0% | 21.7% |
| distinct names | 9 (labels) | 777 |

**One caveat recorded now rather than discovered later.** ACR's Blitz
tables (795 hands) move the player every hand like Ignition's Zone -- but
unlike Zone, the names persist, so Blitz hands still count toward a profile.
The `fmt` column separates them; the `site` column says which rules apply.

## CEO — the plan, with estimates

An **iteration** is one focused working session ending in a check that
passes or fails. Iterations 1-3 took roughly one session each, so the
estimates below are calibrated against real ones rather than guessed.

Two numbers per step: iterations, and what could make it take longer.

### Phase 1 — foundation (3 iterations) — DONE

| # | step | est | actual | risk |
|---|---|---|---|---|
| 1 | ACR histories into the same schema | 1 | 1 | none left |
| 2 | Identity and derivation across both sites | 1 | 1 | none left |
| 3 | Decision layer -- the stat vocabulary | 1 | 1 | none left |

### Phase 2 — the engine (3 iterations)

| # | step | est | risk |
|---|---|---|---|
| 4 | Stat engine: named stats, filters, n and error bars | 1 | low. The hard thinking is done; this is a registry over `decisions` |
| 5 | Player report -- the numbers a HUD would show, per opponent | 1 | low, but **sample size binds**: 84 ACR opponents have 100+ hands, and 100 hands is a VPIP, not a read |
| 6 | Pool report rebuilt on the engine, hardcoded SQL retired | 1 | medium. Seven modules exist; the goal is that the count goes DOWN |

**End of phase 2 is the first genuinely usable tool** -- any stat, any
filter, either site, per player or per pool. Roughly a Hand2Note without
the overlay.

### Phase 3 — the part nobody else can build (4-5 iterations)

| # | step | est | risk |
|---|---|---|---|
| 7 | Ignition-measured range as the prior for a named ACR opponent | 2 | **high, and it is a modelling risk, not a coding one.** Two pools at two stakes are not the same population; the join has to be justified, not assumed |
| 8 | Postflop scoring through the GTO Wizard cache already built | 2-3 | medium. The machinery exists and is proven; the cost is fetch volume and the 1326-combo ordering already solved |

### Phase 4 — surface (5-8 iterations, and the only genuinely uncertain part)

| # | step | est | risk |
|---|---|---|---|
| 9 | Live HUD overlay at the table | 3-5 | **highest in the project.** Needs either the OCR track revived or the client's own data read. This is the one place we are not better placed than Hand2Note |
| 10 | Replayer, notes, filter UI | 2-3 | low, but it is a lot of surface for a single user |

### What the CEO actually recommends

**Stop after phase 3 and reassess.** Steps 1-8 are about eleven iterations
and deliver everything that made this worth doing. Step 9 is where a
tracker becomes a product, and it is also where three to five iterations
buy the least: a HUD shows numbers at the table that a report already shows
away from it, and the analysis is where the money is.

**The binding constraint is not code, it is hands.** 84 named opponents with
100+ hands is enough to rank a pool and not enough to profile a person.
Every phase-3 claim gets sharper with volume and with nothing else. Loading
more ACR sessions is worth more than any single iteration above.


---

# Part III — every site, one tracker

## The vision, restated on 10 Sep 2026

The user's decision, in two sentences: **ACR stays.** And the tracker
should cover **as many sites as Hand2Note does.**

That changes what the next step is. It is not a third parser. The codebase
knew about exactly two sites by name, in thirty-eight places across
fourteen modules, and the third site would have been the moment that
stopped working -- not loudly, but the way `fmt='RING'` stopped naming one
pool when ACR arrived and four modules kept averaging two pools into a
number that described neither.

## Where Part II's plan ended up

| goal / step | state |
|---|---|
| 1. Both sites in one schema, checked | done |
| 2. Decision layer | done -- 96,377 decisions |
| 3. Stat engine, n and error bars | done -- 36 built-in stats, Wilson, Newcombe, Holm, `detectable` |
| 4. Player and pool reports on the engine | mostly; `opponents.py` and `population.py` still carry their own SQL |
| 5. Ignition range as prior for a named ACR player | **not started**, and blocked by data: one week of ACR from May 2025, one week of Ignition from Aug 2026 |
| step 8 -- postflop scoring against a solver | **cut**, 5 Sep 2026: a tracker says what people do, not what is correct |
| step 9 -- HUD overlay | **cut**, same day, same reason |
| step 10 -- replayer, notes, filter UI | arrived unplanned: `app.py` / `gui.py` |

Phases 1-2 are finished. Phase 4's surface arrived early. The thing
Part II called "the reason this is not a worse copy of software that
already exists" -- goal 5 -- needs a named opponent with hundreds of hands
and a pool at a comparable stake, and every week of play sharpens it more
than any feature would. Nothing in code unblocks it.

## Long-term goals, in order

1. **A site is a fact in one place.** `sites.py` says what each site is;
   nothing else spells a site's name. A new site is one parser module and
   one registry entry, and passes the same three checks the first two do.
2. **One site per iteration, as the hand histories arrive.** A parser
   without sample files is the silent failure this project is built
   against, so no site is written from memory of its format. The order is
   whichever sites the user plays and has files for.
3. **Goal 4 closed out**: `opponents.py` and `population.py` onto
   `stats.rates_by`, their SQL deleted, the count of hardcoded modules
   down as the CEO's own metric demanded.
4. **The rest of the manual's list from Run 11, in its order**: hand
   marking and tags, player notes, aliases, table composition, export
   with a filter. Each is something used every session; each is a day or
   less.
5. **Goal 5, tested where it can be tested first**: Ignition alone has
   both halves -- the counted pool range as prior, a `table:seat:segment`
   identity as evidence -- at one stake in one pool, which removes the
   modelling risk step 7 was scored "high" for. Moved below the manual's
   list on 14 Sep 2026: it is in scope -- it answers "what does this
   player do" with fewer hands than a raw rate needs, which is the
   tracker's question and the answer to every popup's "sample too small"
   -- but it is a better number in a column, and the items above it are
   features. If prior-and-update cannot beat the pool rate at predicting
   a seat's next decision there, it will not across sites either.

## What a site is

Two facts decide how a site's hands are handled, and they are per-site,
not per-hand:

  * **whether a label is a person** -- ACR writes the screen name and it
    is the same player next week; an Ignition seat is whoever is sat in
    it, and Zone is nobody. `spots.identify` still decides the name; it
    asks the registry which way to decide it.
  * **whether folded hands are shown** -- Ignition writes every seat's
    cards; ACR writes 23%. A pool is the sites that reveal, and
    `population.py` pins itself to them by asking rather than by spelling
    a name.

Plus the mechanics: the header a hand begins with, where the client keeps
its files, and how money is proved -- by `in - house = won` where the
history states the rake, by `in = stated pot` where it does not.

**The parser contract.** A parser module provides `HEADER(line)`,
`split_hands(text)` and `parse_hand(block, source)` returning the shared
dict shape, and nothing else. Loading, the schema and the checks are
`importer.py` and `sites.py`. Two parsers each carried a private copy of
the loader until this run, one writing fewer columns than the other.

**Networks, not sites.** The ACR parser reads the Winning Poker Network
format, which Black Chip, YaPoker and True Poker share; the Ignition
parser reads Bodog's, which Bovada shares. Whether a second skin of a
network is a second `site` value or the same one is decided when the
files arrive, not before -- it depends on whether the pools are the same
pool, which is a measured question.

Some of Hand2Note's list -- the app rooms, PPPoker and PokerBros and the
like -- write no hand history at all; H2N reads them from the screen.
That is the `gto_pipeline` project's problem, not this one's.

## Run 9 — `sites.py`, and the parser contract

**Goal, set beforehand:** every hardcoded site name outside the parsers
moved onto a registry, the two parsers reduced to the contract, one loader
-- with ACR and Ignition behaving exactly as before, proved by the raw
tables loaded from disk being byte-identical to the old loader's, and the
derivation chain reproducing the live database exactly. And the import
check generalised so that Ignition has one, which it never did.

**Result: PASS.** 38 site-name hardcodings across 14 modules are now 3
test fixtures and a migration default. Raw tables through the new loader:
identical. Every derived table after a full chain rebuild: identical.
`check.py`: 16 of 16.

**What the generalised check found.** Applying ACR's money identity to
Ignition -- `in = stated pot`, since Ignition never writes its rake --
showed four cash hands with money coming out that never went in. All four
carried `Posts dead chip`, a returning player's dead post, which was not
in `ignition.POSTS` and fell through the parser without a sound. It is the
Ignition twin of ACR's bare `posts $0.05` bug, uncaught for exactly as long
as the check did not exist. Four hands does not move a report. The next
one will not necessarily be four.

```
ignition   money adds up  100.00%  (3892/3892, in = stated pot, won <= pot)
           positions      100.00%  blinds by blinds  98.47%
acr        money adds up   99.92%  (8277/8284, in - house = won)
           positions      100.00%  blinds by blinds  99.02%  786 named, 85 with 100+
```

**Also found, not fixed here:** `opponents.py` still decides a deviation
by whether two intervals overlap, the test CLAUDE.md retired on 5 Sep in
favour of `stats.difference`. It is goal 3 above.

## Run 10 — PokerStars, the registry's first test

**Goal:** the third site for one parser module and one registry entry,
proved by the same three checks.

**Result: PASS.** `pokerstars.py` and one `Site(...)`; 9,961 NL100 6-max
hands; money 9,961/9,961, positions 5,648 each, blinds by blinds 99.7%;
431 named opponents, 104 with 100+. Two things the money check caught
before the load: PokerStars' "raises $1 to $2" names the increment, not
the amount added; and an All-in Cash Out leaves no "collected" line. The
database is 22,165 hands and 173,549 decisions. Goal 1 of Part III is
demonstrated, not just claimed.

## Run 11 — the manual, audited; sessions; goal 3

**Goal, set beforehand:** every feature in Hand2Note's user manual held
against this program, and the largest gap that fits the scope built.

**The audit.** Of the manual's twenty-odd tracking features, twelve were
already here (several better -- no H2N view carries an interval, a
difference test or a family correction). Missing, in order of worth:
sessions and the time filters (three of the manual's nine use cases), hand
marking and tags, notes, aliases, table composition (fish at the table and
the seats between you and them), export with a filter, configurable
categories, expression stats, bet size relative to the previous bet. The
HUD, its popups, auto-import and the emulator are out of scope. The full
table is on the plan page.

**Built: `sessions.py`.** A sitting is a run of your own hands on one site
with no gap longer than ten minutes. 83 sittings over 22,150 hands, 65 of
fifty hands or more, one to nine tables at a time. Per site because the
clock is the site's; the hour is the site's hour and every view says so.
Filters, dimensions, a view, a tab; the check is the arithmetic and it is
exact.

**Goal 3, closed:** `opponents.py` onto `rates_by_player`, `difference`
and `holm`, baseline without the player. Its check had reached 275 seconds
of CPU at three sites and was the reason the suite had become a thing
nobody ran; 2m13s now, 214 reads, 40 of 40 re-derived. `population.py`
still carries its own SQL and is the last of the six.

**Result: PASS**, 17 of 17.

## Run 12 — goal 3, finished

**Goal:** `population.py` onto the engine, its SQL deleted, its findings
unchanged.

**Result: PASS.** No query left in the module; the same 22 split-half
findings hold, rates within a point of before except cbet, which moves
from a spots column the engine's check had already recorded as wrong to
the engine's figure. `call_open` added to the registry for the report's
"every seat that called" meaning; `query.results_of` measures its error
bar from the hands instead of assuming 11.7bb per hand; the pool check's
verdict is its exit code, which it had never been. Six modules once
carried their own SQL; the count is zero.

## Run 13 — marked hands and player notes

**Goal:** the next two items on the manual's list: tag a hand and filter
by the tag; write a note on a player and see it wherever they appear.

**Result: PASS**, 18 of 18. `notes.py`, two tables the chain never
touches, `--tag` in the filter vocabulary and the window's dialog, hand
ids in every listing, the replayer window as the place a hand is marked
and a player annotated.

## Run 14 — aliases and export

**Goal:** the next two: one person under two names, and the hands a
filter selects out as text.

**Result: PASS**, 18 of 18. Aliases within a site, applied in
`spots.identify` and needing a rebuild, which `importer.py --rebuild`
now does; `--export` reads each hand back out of its file and the
importer's check proves the round trip. What remains of the manual's
list: table composition, configurable hand categories, expression
stats, bet size relative to the previous bet. Then goal 5.

## Run 25 — the expression language

**Goal:** "get the manual and study the H2N expression language and let's
use it since it's public."

**Result: PASS**, 20 of 20. The ten functions, the operators and `if`,
per-player-hand counting, a sandboxed parser; six built-ins from the
manual's examples; `--define-expr`; `--show` and `--by` columns; the
assistant knows them. The last of the six gaps from the manual reading.
Not in the window's SAVE AS STAT dialog yet -- a formula box there is
the next small thing.

## Run 24 — the filter list, and what the window was showing

**Goal:** the user's Hand2Note 4 filter list checked item by item; and
"UTG has 67% VPIP, BTN 11% PFR, stats seem off".

**Result: PASS**, 20 of 20. Six sizing, depth and game filters added;
`raise_x` and `hero_in` on `decisions`. The stats were true and the
filter was wrong: "vs the pool" was heads-up decisions, and the user was
running a packaged build from 15 Sep with none of the week's work in it.
The chips are renamed, the note is printed, the update message says what
to do, the workflow builds the right program and publishes a release.
Expression stats next, in Hand2Note's own language.

## Run 23 — the assistant as the front door

**Goal:** the user's "let's start working on the AI attachment": a chat
that opens and closes, that controls the program and looks things up;
and "where is the pool overfolding" -- which needs a bar Hand2Note does
not supply.

**Result: PASS**, 20 of 20. The panel drives the window; Qwen and a
local Ollama model beside the four providers; `--overfolds` with the
bet-size bar and Holm across its rows, as a tab and as the assistant's
answer; the range view's kicker, ace-high and pair-plus-draw groups (gap
five of six). Left: expression stats.

## Run 22 — Hand2Note's gaps, one to four

**Goal:** the order written below: the bet-sizing split with reactions,
action profit by hand strength and the won/showdown columns, rake paid
and hands per hour and the biggest pots, the hole-card grid.

**Result: PASS**, 20 of 20. All four, and a hand that was listed once per
street is listed once. Left in the order: strength groups (top pair by
kicker, A-high, pair + draw), then expression stats.

## Hand2Note, page by page — what it has that this does not (16 Sep 2026)

Read from the manual's Features and Custom Stats pages and their
screenshots, saved under `Desktop/h2n_manual`. HUD items are out of scope
by decision and are listed only so the list is complete. "Have" means
the same question can be asked here, not that the screen looks alike.

### Reports and Sessions

| Hand2Note | here |
|---|---|
| Results by game type (stake, table size, fast-fold), with Net Won, EV, bb/100, EV bb/100, **rake paid** | have the split (`--by stake`, `--by site`); **rake paid is not a column anywhere** — a report of what the house took, by stake and month, is a query away |
| Report list: Positions, Positions Full Ring, Rooms, Number of Tables, Number of Fishes, Stack sizes | have position, site, tables, `n_fish`, `--by stack` |
| Date range picker; Hero picker with aliases | have `--since/--until`, aliases |
| Filter dialog, General tab: room, blind size, players on hand start, table size, initial stack, session length, time of day, fish count, max fish VPIP, **distance to fish**, straddle, game type | have all but **distance to fish** as a number (we have left/right, not how many seats) and straddle |
| Filter dialog, Action tab: "preflop action facing" (unopened, 1 limp, 2+ limps, 1 raise, 2 raises, raise+call, raise+2 calls, blind posters, all folded to BB) and per-street **click buttons** Raise / Raise-Fold / Raise-Call / Call / Call-Fold / Call-Call / Call-Raise, with "selected action is the last action on street" | have the facing ladder and, since today, the click builder; the two-action combos (Raise then Fold on the same street) are one press each here; **"all folded to BB" and "limpers count" are not facings** |
| Filter dialog, Hole Cards tab: a 13x13 grid to pick hands | have `--combo` typed; **no grid to click** |
| Positions report shows Net Won, EV, bb/100, EV bb/100, Rake per position | have net and EV per position; rake missing |
| "Show hands" from any result line, opening the compact hand list | have the hands tab for any filter |
| Sessions: start, length, hands, net, EV net, hands/hour, tables; graph of the session with four lines | have sessions, graph (four lines); **hands per hour is not printed** |
| Hero's stats "in the opponent's eyes" (vs-hero values) | have `--vs-hero` |

### Quick Hand View

| Hand2Note | here |
|---|---|
| The whole hand on one line: hole cards, per-street actions as coloured letters with sizes in bb, board cards, pot size, underlined hero actions; replay button | the hands tab shows cards, line, board, result; **not the one-line coloured format with sizes and pot**; replayer exists |
| Sort by biggest wins / losses | **missing** — one `ORDER BY` |

### Extended popup on a stat (the big one)

| Hand2Note | here |
|---|---|
| Next actions F/C/R and B/X after the stat's action | have, since today (`--actions`, next player's action) |
| Stat value, vs-Hero value, Won Hand %, Went to SD %, Won at SD % after the action | have wtsd/wsd as stats; **"won hand after this action" and "went to showdown after this action" as columns of the actions view are missing** — two joins |
| **Bet-sizing distribution** of the action (overbet, 0.8–1 pot, 0.6–0.8, 0.4–0.6) each with its own next-actions | have size buckets on lines (`--flop XBm`) and `--quick overbet/small_bet`; **a by-size split of one action with the reactions to each size is missing** |
| Action Profit total and for weak hands, ± dispersion; Spot Frequency (cases and opportunities per 1000 hands) | have profit ± SE and, for hero, per 1000; **profit split by hand strength is missing** |
| **Action Profit Details**: profit per hand-strength group (pair+draw, LP, MP, TPWK, TPGK, overpair, set, 2pair+, KQ/KJ/QJ, trash, AK/AQ/AJ, A-high, gutshot, draw, low PP) and per bet size | **missing** — `--by hand` exists for rates; the actions view needs a `--by made` |
| Postflop diagram: the showdown range after the action, in those groups, with counts | have `--range` (our groups: set, trips, two pair, overpair, top pair, middle pair, weak pair, under pair, board pair, high card + draws); **no top-pair kicker split (TPGK/TPWK), no A-high/K-high split, no "pair + draw" group** |
| Preflop range map with two colours (base action and alternative, e.g. raise vs call) and Raise Total / Call Total | have `--chart` for one stat; **the two-action overlay is missing** |
| List of showdown hands under the diagram, sorted by strength | have hands tab; sort by strength missing |
| Customisable groups (hand strength), boards, bet sizes — with "Is weak" per group | have `strength.WEAK/STRONG` lists and `BOARDS`; not user-editable in the window |

### Range Research (pool by stat filter)

| Hand2Note | here |
|---|---|
| Select a group of players by stat conditions (VPIP ≤ 17, PFR ≤ 14, hands ≥ 1000, reg/fish, colour label, expression string) and see the merged popup | have `players.parse_cohort` / the Players dialog (class, stat intervals) and `--reg/--fish` — the same idea, smaller vocabulary; **no expression string** |
| Hero's hands excluded automatically | have `--pool` |

### Decision Analysis

| Hand2Note | here |
|---|---|
| Action Profit (stack at end − stack before action; fold = 0) | have, since today, by that definition |
| Spot Frequency per 1000 hands (cases and opportunities) | have cases for hero; **opportunities (the chance count) not printed** |
| Next Villain's Actions | have |

### Notes, marks, sharing

| Hand2Note | here |
|---|---|
| Notes per player, **note templates**, inserting a hand into a note, **notes on a stat**, badges (rule-based icons) | have player notes and hand marks; templates, hand-in-note, stat notes, badges missing (badges are HUD) |
| Marked hands with **labels** (named, coloured) and per-hand notes | have marks (`--tag`), which are labels; per-hand notes exist in `notes.py` |
| Share a hand as a link or image, hiding names and showdown | **missing** — `--export` writes text; an anonymised one-hand export is close |
| Showdowns indicator (count of new showdowns per opponent since sitting down) | HUD; out of scope |

### Statistics engine

| Hand2Note | here |
|---|---|
| Plain stat editor: simulate a sequence of actions (Player raises, Villain calls…), with per-action parameters: first/last action on street, first non-fold, stack size, eff stack, eff stack / pot, bet size, bet size / pot, bet size / previous bet, all-in, position postflop, reg or fish, is hero, hero still in hand, same-player id, stat intervals of the actor, initial pot, players on street | have most as filters (`--facing`, `--pfa`, `--ip`, `--deep`, `--node`, sizes in lines, `--reg`, `--vs-hero`); **missing: bet size relative to the previous bet; initial pot on street; "hero still in hand"; stat intervals of the actor as a filter (needs the cohort as a row property)** |
| Preflop tab: range, BB size, ante, players dealt, hero on blinds, fish on blinds, zoom, out-of-queue posts, position | have most; **fish on blinds, ante, out-of-queue posts missing** |
| Expression stats: Value, Cases, Opps, VsHeroCases, WonHandCases, WentToSDCases, WonHandAtSDCases, AmountWon, ActionProfit over any plain stat, with arithmetic | **missing as a language**; `--define` makes plain stats only. AF, WWSF, WTSD-after-cbet exist as built-ins |
| Reg-vs-fish exclusion when computing a reg's stats | have `--regs-only`, `--vs-reg`; not a global switch |
| Game types (NL cash 3–10max etc.) as named configurations | have `fmt` + `--site` + stake |
| Rebuild stats after editing | have `importer --rebuild` |

### The order to do them, by value for the money

1. **Bet-sizing distribution of an action with the reactions to each size**, in the actions view — the single most-used thing in H2N's popup and one GROUP BY on `size_bb`/`pot_frac` buckets.
2. **Action profit by hand strength** (`--actions --by made`) and the two extra columns (won hand %, went to showdown % after the action).
3. **Rake paid** as a column and a dimension; **hands per hour** on sessions; **sort hands by result**.
4. The **hole-cards grid** in the filter dialog, and the **quick one-line hand view** with sizes and pot.
5. Strength groups: **top pair by kicker**, **A-high / K-high**, **pair + draw**.
6. **Expression stats** — a small formula language over stat cases/opportunities. Big; last.

## Run 21 — what the user saw, second pass; PartyPoker

**Goal:** the next things said at the window -- action profit and next
action; a line picker instead of letters; "strong" should be top pair
or better; quick filters for the barrels; the window freezing between
tabs; the graph's default -- and PartyPoker, which the user put back on
the list.

**Result: PASS**, 20 of 20. The actions view, the line builder, three
strength tiers, five barrel stats, one worker thread with a cache, hero
by default on results and the graph. `partypoker.py` against 31
fixtures: 17 hands prove, 9 refused with reasons. The Hand2Note manual's
Features and Custom Stats pages, 27 pages and 120 images, saved under
`Desktop/h2n_manual` for the comparison the user asked for.

## Run 20 — what the user saw

**Goal:** three things said while looking at the window: "PFR vs BB says
9%, where does that come from"; "BTN vs BB should mean the button raised
and the big blind did not fold"; "we should use H2N notation for lines,
and have a large list of common filters -- triple barrel, double barrel".

**Result: PASS**, 20 of 20. The number was right and the filter was the
wrong question: `--vs` is the seat at the moment, and an open has none.
`matchup` is the pot, stamped on every row and checked against the
actions; `own`/`own_node` are a player's own line in the notation lines
are said in; thirty-five reports by name. The graph's idle text no longer
reads as a verdict.

## Run 19 — the app rooms

**Goal:** the cheapest sites first. Thirteen of FPDB's "Stars" fixtures
are other rooms writing PokerStars' format under their own name.

**Result: PASS.** `Site.brand`, `HEADER(line, brand)`, two registry
lines: PokerBros and PokerMaster, the rooms Hand2Note sells its app
support on. Their fixtures found a real bug on the Stars parser -- an
ante seeded the street's tally and every raise from an anted seat came
out short -- which 9,961 Stars cash hands could never have shown. Run
It Once and MPLPoker are the same format and are not added: the first
closed in 2022 and the second's liveness is not known. Next: PartyPoker,
31 fixtures, the first parser from scratch since Stars.

## Run 18 — the parsers against the world's files

**Goal:** the user's "we can get just as many sites as H2N; it is a
matter of finding hand histories online." First, the three parsers we
have against a corpus we did not write.

**Result: PASS**, 20 of 20. FPDB's regression corpus fetched (AGPL; read,
never copied in); `fixtures.py --check` over fifty-one claimed files and 971
hands, every hand read, no verb dropped, proofs held. Then the corrected
parsers over the user's own files against the database, seat by seat:
seven Ignition side pots recorded as the main pot alone, seven ACR
run-twice hands recorded as one board, and a tournament folder with 895
lines dropped as unknown verbs. `--reread` for exactly that; 174,298
decisions from 173,549. The Ignition money identity gained the floor
that would have caught the first of these a month ago.

The survey is the plan for goal 5's sites: PartyPoker 31 fixtures, iPoker
12, Winamax 11, BetOnline 11, Microgaming 5, KingsClub 4, 888 3, Unibet 1,
plus the Winning client's 2015–2017 format, eight files in UTF-16 under
"Game started at:". Each is a parser module and a registry line. Cheaper than any of them: Run It Once, PokerBros, PokerMaster and
MPLPoker write the PokerStars format under their own name -- thirteen
Stars-folder fixtures are these -- so a brand list on the one parser is
four rooms, and it is how a tracker "supports" the app rooms. GGPoker
writes no usable text and is not on the list.

## Run 17 — the assistant scored

**Goal:** the user's "can we implement Gemini's text-to-SQL idea, will it
sell" -- with the improvements: filters not SQL, the tool judges
significance, translation accuracy measured.

**Result: PASS.** `golden.json`, `ask.py --score`, answer-equivalence
scoring; 32 of 41 on the first run, the misses traced to one rule (which
site answers hold-questions) and written into the vocabulary. Re-score
when a provider's limit resets. The honest market answer, given at the
time: a hundred people at twenty dollars is the ceiling and not the
likely case; the assistant is a feature, not the product.

## Run 16 — ask in English

**Goal:** carried over from a parallel session and consolidated: the
database asked in English, with the model confined to the program's own
vocabulary and one read-only tool.

**Result: PASS.** `ask.py`, the chat panel, five providers, an MCP server
for the desktop app.

## Run 15 — table composition

**Goal:** where the fish sits, not just whether one is in the pot.

**Result: PASS**, 18 of 18. Four columns from the liveness walk already
in `players.py`, four switches, two exact identities checked on every
row. Hero with a fish in the pot: +15 (right) and +19 (left) bb/100,
each ±22; regs only, +0.5 ±10. What remains of the manual's list:
configurable hand categories, expression stats, bet size relative to
the previous bet. Then goal 5.
