# Changelog

What changed, and why. Bugs get their own entries when the bug is worth
remembering — most of the ones here produced no error and no crash, only
wrong numbers, which is the failure mode this project is built against.

Newest first.

---

## PLO hand-strength: Omaha groups, not Hold'em bars

The histogram on `--game plo` was honest labels on Hold'em bars —
overpair sat on Overpair, ace-high wrap+FD sat on Draws, and Weak %
read `strength.WEAK` (under middle pair). That is a Hold'em diagram
with a new heading.

- **`best_omaha_hand`** — the public 2+3 walk. PLO5 still uses
  exactly two of five. One hole heart on a three-heart flop is
  ace-high, not a flush and not an FD.
- **`OMAHA_HIST_SPEC` / `OMAHA_WEAK`** — Combo, Wrap, FD, Air,
  Weak made, Medium, Strong, Nuts+. One pair is Weak made. Made
  hands stay on a made bar even with a draw, same rule as Hold'em.
- **Hist / Flop Hand / range Weak %** switch by variant.
  `--made "top pair"` is refused on a PLO filter;
  `--hist-group weak_made` is the click.
- **verify-parity J** golden trap + combo fixtures in
  `fixtures/plo_parity/`. NLHE A–I and the default Hold'em
  histogram stay on Hold'em groups.

Deferred: PLO combinatoric ranges, PLO6/8, hi/lo, Magnum AA classes.

---

## Multi-game v1: Game Type, Bodog/Bovada, 4/5-card compact, 13×13 gate

The PLO study track widened past strength. Same branch, same freeze
base, PR #7 import still reconciled in.

- **Bodog / Bovada HEADER** — the network writes three brand names.
  Downloads-style PLO files were skipped because only `Ignition Hand #`
  was accepted. `variant` and `hole_card_count` are written on `hands`
  from the registry, not guessed in a parser.
- **Game Type** — `{variant, cash|mtt}` → `nlhe-cash` / `plo4-cash` /
  `plo5-cash`. `--game-type` / `--type` / `--variant` are who-flags.
  Default `--game` is still Hold'em (all formats) so NLHE reports do
  not suddenly drop MTT. Stats under a type filter are that type only.
- **Compact / replayer** — four and five hole cards sit on the line
  (`[As Ad Kh 7d]`). Hold'em still omits them; the combo column has
  AhKd.
- **13×13 gated on PLO** — the chart says why, and Hands still lists
  the shown cards. An empty 169-square is not a range.

`verify-parity` A–I stay on the NLHE corpus. J is the PLO sibling
(`fixtures/plo_parity/`). Rebuild derived tables so `game_type`
lands on `spots` and `decisions`.

### Not this, on purpose

PLO combinatoric heat map, PLO6 / Omaha8, full Omaha taxonomy
polish, HUD, PokerStars Omaha, player class measured on PLO.

---

## PLO study: Omaha strength, `--game plo` histograms

A new track, stacked on the NLHE H2N freeze
(`cursor/h2n-dock-back-freeze-7372`) with Omaha import re-added from
PR #7 (`cursor/omaha-plo-import-ec94`). Master did not have PR #7
merged; the freeze tip had the study chrome and no `games.py`.

- **`games.py`** — HOLDEM / OMAHA / OMAHA5, `--game` aliases, default
  pool Hold'em so a PLO VPIP cannot rewrite an NLHE one.
- **ACR / Ignition import** — PLO4 and PLO5 stored with the right
  hole count. PokerStars Omaha is the documented skip.
- **`strength.classify`** — PLO4 and PLO5 use two hole cards and
  three board cards. The Hold'em reading of all four (royal on JT,
  flush with one hole heart, two pair from unused kickers) is
  refused. `wrap` is the straight-draw word Hold'em does not have.
- **Reports / Statistics / Sessions** — `--game plo` is a who-flag.
  Default is still HOLDEM. Postflop histogram / Weak % / range
  breakdowns read Omaha `made` labels. The 13×13 and all-in equity
  stay two-card.

`--check` fixtures are synthetic PLO hands in `acr.py`, `ignition.py`
and `strength.KNOWN_OMAHA`. Hold'em `verify-parity` is unchanged.
No HUD. No solver. No invented equity.

NLHE tracking-half chrome is still the freeze (~97%). PLO study
readiness is a separate score.

### Not this, on purpose

Omaha equity, a four-card 13×13, PokerStars Omaha import, player
class measured on PLO. See `GAPS.md`.

---

## Dock-back + tracking-half freeze

Detached window → **Dock back** → the main Reports tab, same
context and breadcrumb. No HUD. No solver. The remaining
tracking-half gaps are frozen on purpose; they are listed in
`GAPS.md`.

The report id is the same argv Detach and Pin already share --
who + situation + crumbs -- so a docked nest cannot silently
become a different filter. If the study tab already shows that
id, Dock-back focuses it rather than opening a duplicate.
Pin + Detach + Dock coexist: pin stays on the main bar.

Gear on a detached pane (and **Dock back** on the window) hands
`dock_payload` to `App.dock_report` / the page's `dockReport`.
Close without docking still does not touch the in-main pane.

`--check` covers the payload, hydrate = parent + crumbs, and
Detach → Dock → same breadcrumb and hands. No invented EV.

### Not this, on purpose

See `GAPS.md`. Rakeback overlay, WYSIWYG profile editor, full
256 board/bet editors, Dispersion/EV diff, more Expression
atoms, HUD/solver, view-as-opponent, anon IDs/import skip
flags. PLO / multi-game is a separate track.

---

## Statistics Profile Menu + Save/Open Report

The tracking-half Profile Menu and the costly-context Save/Open.
No HUD. No solver. No WYSIWYG profile editor. Dock-back is still
deferred.

`default` is the curated grid, locked. Preflop / Postflop /
Showdown are shipped subsets so the menu has something to switch
to without an editor. Extra profiles live in `profiles.json`
(gitignored). A missing stat id is skipped with a warning -- a
deleted custom stat must not blank the grid.

Save writes version, subject, cohort_predicate, fmt (cash|mtt),
date_range, last_n_sessions, exclude_reg_vs_fish, profile_id.
Open hydrates the Statistics bar and recomputes. Recent names
sit on the Report menu. This is not `filters.json`: a Smart
Report is a situation; this is who / when / which columns.

The Profile Menu changes columns only. Subject, cohort, dates,
and the exclude flag stay where they were.

```bash
python query.py stats save "regs last 10" --hero --fmt cash --last-sessions 10
python query.py stats open "regs last 10"
python stats.py save NAME --hero --profile preflop
```

`--check` covers the payload fields, missing-stat skip, and a
golden save→open identity on `fixtures/parity/` (parity I).
No invented EV.

### Not this, on purpose

No HUD. No solver. No WYSIWYG profile editor. No rakeback.
Dock-back is deferred.

---

## Detach report panes

H2N-style Detach: a study pane opens in its own window.
No HUD. No solver. Pin stays in-main. Dock-back is deferred.

The report identity is the same argv Pin already uses -- who +
situation + crumbs -- so a detached Stack pane and a pin of that
nest cannot drift. The window is live: a further drill in main
(or a row click in the copy) reshapes it. Freeze-on-detach is
the other legal reading; live is the cheaper one because the
payload is already built.

Gear → **Detach** on every study pane, including Win Graph,
Hand Values, and Hands. Cap is four; closing one frees the
slot. The title is `subject · pane · short filters`. Close
does not touch the in-main pane. Row-drill and compact hands
work inside a detached breakdown. Pin + nest still work on
the main bar.

`--check` covers the context id, the title, the cap, and
Pin coexistence. No invented EV.

### Not this, on purpose

No HUD. No solver. No detach of the whole shell. No
multi-profile Save/Open. No rakeback. Dock-back is deferred.

---

## Live-corpus correctness (verify-parity)

Lock the already-shipped H2N tracking-half semantics. No HUD, no
solver, no new UI.

A committed synthetic corpus in `fixtures/parity/` (9 ACR, 3
Ignition) loads through `importer.load` and `CHAIN`. `parity.py`
is the CLI (`python parity.py`, `python query.py --verify-parity`);
`check.py` runs it. CI runs the no-corpus `--check` suite plus
this. The live `hands.db` is still the user's and is still
gitignored.

### Invariants

A. Hits/Opps/freq is 0 on an empty sample, never a crash.
   `statistics_of` used to multiply Wilson's None by 100 when a
   spots-sourced stat had no opportunities -- a sample with no
   flop blanked the Statistics grid.
B. Fold Action Profit is 0, and it is not Won$ of the hand.
   An uncontested open is +pot_before (AP) and a different
   whole-hand net (Won$).
C. `--quick` is chance ∧ action. A nest is parent ∧ row.
   Smart Reports stay the loose spot (documented).
D. `exclude_reg_vs_fish` shrinks Statistics only. Reports
   `--quick` and Sessions sit-downs do not move. The test fails
   if they do.
E. Today uses start-of-day and the room timezone on imported
   stamps. 02:00 room / ACR −3h is in; 10:00 is out.
F. Win-graph WOS + WSD = Amount Won. No all-in, so yellow
   equals green -- EV is not invented.
G. Heatmap coverage names the showdown bias (Ignition 100%,
   ACR folds hide cards). Histogram Other is last; Weak % is
   of the bar counts.
H. Session export writes that sit-down, not the rest of the
   corpus. `matching_hands` of a filter is that filter.
   `importer.load` now stores the resolved path, not the
   basename -- export recovered nothing when cwd was not the
   folder the file was loaded from.

`build.py` now lists the H2N-era modules (sessions, notes,
aliases, compact, expr) plus `parity` as runtime / hidden
imports, so the packaged app and `build.py --check` agree.

### Not this, on purpose

No HUD. No solver. No rakeback, multi-profile, PLO, or
new Expression atoms. Detach shipped next.

---

## Weak-hand % on postflop histograms

H2N’s “how often they bluff” readout on a betting range. Tracking
half only: no HUD, no solver, no group editor, no board slices,
no Detach.

One aggregator, `query.hist_postflop_of`. Groups live in
`strength.HIST_SPEC` next to `WEAK`: ordered bars, first match
wins, Other implicit for leftovers. Air / Draws / Weak pair
default to `is_weak`. A made pair keeps its pair bar even when
it also has a draw — otherwise “how do they play a gutshot”
includes the top pairs that happen to have one.

Weak % is of the hands that were **seen**. Ignition shows every
hand including folds; ACR shows 23%. Coverage rides with the
payload for the same reason `range_of` prints it.

`--hist-group` is the bar click, the same flag a pane row already
knew how to AND. Right-click flips `is_weak` on that bar and
recomputes the percentage from the counts already on screen.

### Added — shared widget

`HistWidget` on Statistics (under the 13×13) and the Reports
study strip. The page draws the same payload. Hand Values is
also a plus pane.

### Added — CLI

```bash
python query.py --street flop --action bet --hist-postflop
python query.py hist-postflop --street flop --facing bet
```

`weak_pct` is in the payload and printed. `--check` uses eleven
shown hands and does not invent EV.

### Not this, on purpose

No HUD. No solver. No full group editor. No board slices.
No Detach. No rakeback. No multi-profile.

---

## Win Graph — official H2N four-line chart

Reports + Sessions, the tracking-half win chart. No HUD. No
solver. No rakeback overlay. No Chip EV. No Detach.

One aggregator, `query.graph_of`, so the Graph tab, the study
strip, Sessions detail, the page, and the CSV cannot drift.
Per-hand extractors are `won`, `all_in_ev`, `won_wos`,
`won_wsd`; the chart is their running totals in play order.
bb | $ is a unit switch on the same hands, not a second query.

Amount Won is green, All-in EV yellow, Won without Showdown the
red line, Won at Showdown gray (it was blue, which is not H2N).
Red + gray = green. Rising red ≈ bluffy; falling ≈ passive.
All-in EV is the existing `equity` price for the clean two-way
case with cards to come; everything else stays at the actual
result. `--check` does not invent an EV -- on a fixture with no
all-in the yellow line equals green, and that is asserted.

### Added — shared widget

`WinGraph` in the window (legend toggles, hover, bb/$). The
page and `query.py --graph` write the same HTML widget.
Reports study and Sessions detail reuse it; a 3-bet drill
reshapes the four lines.

### Added — CLI CSV

`python query.py --graph --csv` / `report graph`, and
`python sessions.py graph ID --csv`. Last `cum_won` is the
Won summary `--results` prints.

### Not this, on purpose

No HUD. No solver. No rakeback overlay. No Chip EV. No Detach.
Window `--check` still needs Tk.

---

## Statistics — curated grid, Call Range, exclude_reg_vs_fish

The tracking-half Statistics tab. No HUD. No solver.

A curated subset of the registry (VPIP through WTSD/WWSF), not
the dump of every matching stat -- that is still the `stats` tab.
Who is the player / cohort / alias already on the bar. Cash | MTT,
dates, and last N cash sit-downs sit on this tab. Click a stat
for its 13×13; Call Range is the same chance with a call
(3bet → Call Open Raise); a cell is that combo; compact hands;
Open in Reports writes `--quick` / `--call-range` onto study and
leaves the exclude flag behind.

`exclude_reg_vs_fish` is a Statistics compute flag, not a
`build()` switch. A regular's decision with a fish still in
drops out of the Statistics sample; fish-vs-reg and unknown stay.
Reports and Sessions ignore it, matching H2N. `--villain-type`
stays the Reports filter it already was.

Who-is-Reg is still `players.classify` (intervals; unknown is an
answer). A pin in `player_types.json` wins and restamps
`decisions`, so Statistics exclude and Reports `--class` see the
same person.

`--fmt cash` is `fmt <> 'MTT'`. Last-N is the sessions table
(cash sit-downs); MTT last-N is ignored.

`--exclude-reg-vs-fish` is a skip-one-token flag, not an
`OPTIONS` entry. OPTIONS skip two tokens (flag + argument);
putting a switch there ate `--hero` and Reports opened without
the person. The check is `build([exclude, --hero])` still
selects hero.

### Added -- Statistics path

`query.statistics_of` / `stat_range_of` / `take_stat_drill` /
`reports_argv`. Window tab, page tab, `--statistics` /
`--exclude-reg-vs-fish` / `--fmt` / `--last-sessions` /
`--call-range`. `players.set_type` and the Who is Reg dialog.

---

## Sessions — sit-downs, not spots

The other primary H2N study surface after the Reports cockpit.
No HUD. No solver. No Chip EV. No rakeback overlay.

### Added — session entity

A session is consecutive hero cash hands at one site, cut when the
gap exceeds 60 minutes or the room / named hero changes. `id` is
the first hand. Won is profit dollars (`spots.net`); **Won bb** is
`net_bb`. MTT is out. Opponent class is ignored -- a future
Reg-vs-Fish stats-rebuild exclusion does not touch sessions, and
they are not in `importer.CHAIN`.

Ignition is one hero stream per site. Following
`table:seat:segment` would start a new sit-down every time the seat
turned over.

### Added — clock (the date footguns)

**Today**, last N hours, and a date range go through
`sessions.Clock`: start-of-day hour (default midnight) and a
per-room HH timezone offset (hours added to `played_at` to reach
your clock). One `played_at >= X` across rooms is how Today goes
empty. Raw `--since` / `--until` stay literal so saved reports do
not move. An empty window names the hour and the offsets.

### Added — GUI, page, CLI

Sessions tab: list (Won bb column, column picker) → detail summary
+ the four-line graph + compact hands → Export session hands →
Open in Reports (`--session` chip). Mark / note on the compact
rows. `sessions.py list|show|export-hands|graph`, or the same
flags. `--today` / `--hours` / `--session` on `query.py`.

Export reads `hands.source` through that site's parser. A missing
file is counted, not invented.

`--facing` is a chip on both front ends, so opening a neighbouring
spot (Flop c-bets) writes the facing the command line already had.
The window used to drop it and rebuild a different filter.

### Not this, on purpose

No HUD. No solver. No live-table VPIP sort. No Chip EV graphs. No
rakeback overlay. No session merge/split. No session-level note
(hand marks/notes already exist). Window `--check` still needs Tk.
Live `check.py` on a real `hands.db` is still a remaining gap.

### What's next

Statistics grid. Detach. Session merge/split. Live `hands.db`.

---

## GUI Reports study-flow

The H2N Reports cockpit, without a second UI and without a solver.
CLI was ahead; the window and the page now run the same loop.

### Added — study tab as the Reports cockpit

Subject stays the Players / Filter / report / pin bar. Under it:
filter chips, a breadcrumb, a Smart strip (hits/opps, freq, Action
Profit v1), four default panes, and a compact hand list.

Default panes: **Results**, **Stack Sizes**, **Positions**, **Next
Action**. Hands sit under them. A row click is `parent ∧ row_key`
(`query.drill_child`), nested three deep, popped from the
breadcrumb. Detach is deferred.

Stack Sizes includes **80-120**. Next Action is this decision
(`--action`) plus composed **Call vs OR** / Fold vs OR / 3-bet vs
OR when the filter has not already named a street or a facing.
Combo-family chips on the Smart strip (Axs, Kxs, pairs, …) are
`--combo Axs`, expanded to the real combos -- `combo IN ('Axs')`
matches nothing.

`--action`, `--result`, and combo families (`Axs`, `Kxo`, `22+`,
`pairs`, `broadways`) are the same flags on the command line.

### Added — column gear, sort, hand context menu

Each pane has a column picker and a sortable heading. VPIP / PFR
stay off on postflop packs (they already left `columns_for`); the
gear can put them back. Double-click a compact row opens the
replayer; right-click is Mark / Unmark / Add to Note (`notes.py`).
Right-click a pane row can Pin that row (JSON argv, same person).
`+` pane adds Bet Sizes / Flop Hand / Flop Board / Combos.

### Not this, on purpose

No HUD. No solver. No Sessions view. No graphs inside the cockpit
(the graph tab is unchanged). No Statistics grid. No Detach. No
Reg-Fish stats exclusion toggle -- `--reg` / `--fish` / `--class`
are still the filter. No invented EV. Window `--check` needs Tk.

### What's next

Sessions. A Statistics grid. Detach. Live `check.py` on a real
`hands.db`. Window `--check` needs Tk.

---

## Bet Sizes pane and a richer pin

Two leftovers from the tracking-half arc. No HUD. No solver. No
Dispersion / EV diff.

### Added — Bet Sizes pane

Hits/opps, freq, and Action Profit v1 by pot-frac bucket (`s m l p
o`). The edges are `lines.bucket` / `--size m` as SQL (`size_expr`),
not a second set of cuts -- a report and a filter that named
different pots would look like a finding.

The current filter is the opportunities. Checks and folds have no
size and are not a row. `--bet-sizes` and `--by size` print the
pane; `--size LETTER` opens a row. The same block sits on `--stats`,
the window's stats and report tabs, and the page.

### Added — richer pin

`--pin` / `--compare` still print the compact THIS vs PINNED
summary. They now also draw the breakdown:

  * `--by position` (or any other split) -- two `--by` grids, keys
    aligned
  * `--by size` / `--bet-sizes` -- two Bet Sizes tables
  * no split -- two full stat packs under the compact rows

The window's pin box and the page's pin select do the same: the
report tab is two grids (or two size tables); the stats tab is the
compact summary plus two size tables and two packs.

`--check` holds the CASE against `lines.bucket` on every edge,
including the ones that sit exactly on a boundary, and a pin of
first-in vs last-raise whose size letters and `--by street` n
cannot be the same on both sides. No corpus.

### Not this, on purpose

No HUD. No solver. No Dispersion / EV diff. No assigning later pot
back onto a bet that was called -- Action Profit on a size row
stays unpriced there. Live `check.py` on a real `hands.db` is still
a remaining gap. Window `--check` needs Tk.

### What's next

Dispersion / EV diff. Action Profit later-pot-on-called-bet. Live
`check.py` on a real `hands.db`. Window `--check` needs Tk.

---

## Intervals on profit means, Won$ on c-bet packs, cohort preflop range

Three leftovers from the tracking-half arc. No HUD. No solver. No
invented EV.

### Added — n and a t interval on Action Profit / Call Profit Rate

A bare mean of priced rows was fake precision: +2.40 bb/hand on eight
hits reads as a finding. The mean still sits where it sat. Next to it:
how many rows were priced, and when two or more were, a 95% Student-t
interval on that observed mean (`stats.mean_interval`, from SUM and
SUM(x²)). n=1 prints the mean and says why there is no band -- one
observation has no estimated spread.

That interval is **sampling uncertainty, not EV**. Hands in a session
are not independent and the tails are heavy; the band is still better
than a point that looks exact. Wilson stays for rates. The same cells
are on `--stats`, `--pin` / `--compare`, the window, and the page.

### Added — Won$ / Won hand% on Raise C-bet / c-bet packs

They stay **out of `QUICK_PACKS`**. A spots-sourced Stat blanks under
a street filter, which is the empty-column failure those packs exist
to stop. The numbers come from a join onto the filtered `(hand, seat)`
pairs (`amount_won_of` / `amount_won_by`), so a flop filter still has
them.

Won$ is whole-hand `net_bb`, MTT excluded. Won hand% is the fraction
of those cash hands with `net_bb > 0`, with a Wilson interval. Won$
also carries the t interval on the mean and the same `1170/√n` error
`--results` prints on bb/100. The note says they are spots-sourced
and limited: the whole pot, not the street.

On `--by` they are extra columns after the eight decision stats, not
a ninth Stat that would be dropped by the cap.

### Added — Preflop Range on a Multi-Player cohort

`--cohort … --chart` / `--range` already applied the parked players.
What was missing was saying so, and the hole-card coverage caveat a
mixed Ignition/ACR cohort hides.

`--stats` under a parked cohort now prints a preflop-range block:
seen/total by site, the top combos, and `--chart` / `--range` for
the rest. `coverage_of` asks `sites.revealing()` which rooms show
every hand including folds; the others are the showdown-selected
slice. The window's range/chart tabs and the page's new range/chart
views print the same breakdown.

`--check` holds the interval fixtures (n=3 has a band, n=1 does not),
Won$ of two cash hands (MTT refused, street filter not blank), and a
cohort chart that must not include a player the join dropped.

### Not this, on purpose

No HUD. No solver. No Dispersion / EV diff. No richer pin (two full
`--by` grids). No Bet Sizes pane. No assigning later pot back onto a
bet that was called -- that stays unpriced on Action Profit.

### What's next

Dispersion / EV diff. Action Profit later-pot-on-called-bet. Live
`check.py` on a real `hands.db`. Window `--check` needs Tk.

---

## Call Profit Rate, squeeze as Faced Next, barrel packs

Three leftovers from the reports/filters arc, each where the data
already supported them. No HUD. No solver. No invented EV.

### Added — Call Profit Rate

Sibling to Action Profit in the same spot, for the comparison Smart
Reports actually make: when both call and raise were legal, what did
each alternative do?

This is **accounting of actual calls**, not the EV of calling when
they raised. A raise in the same filter stays unpriced so the mean
cannot be dragged by a counterfactual we do not have.

A priced call is `to_call > 0` (facing a bet or raise, including a
limp) and not all-in (a raise was still possible), cash game.
Profit is `(won − chips this seat put in from THIS action on) / bb`.
Later streets are assigned back on purpose -- that is why Action
Profit leaves the same pot unpriced. `won` already has rake out
where the site writes it. MTT stays unpriced.

  * call 5 into pot 15, win, no more chips in = +15 (`pot_before`)
  * call 5 and lose = −5
  * call 5, then bet 10, win 40 = +25
  * all-in call = unpriced
  * a bet = unpriced (only actual calls)

The mean is over priced calls, with its `n`, sitting next to Action
Profit on `--stats`, `--pin` / `--compare`, the window, and the
page. `--hands` prints `call bb` beside `act bb` and `net bb`.

`--check` holds the three examples, refuses an all-in call, an MTT
call, and a bet, and on a live corpus asserts priced + unpriced = n
and that no aggressive row is priced.

### Added — squeeze as a Faced Next verb

`--after squeeze` / `--then squeeze` (aliases `sqz`, `squeezed`).
A squeeze is the squeeze stat's chance on a **later** row: facing
an open, `n_live >= 4`, `pot_bb > 4`, aggressive. That is the
caller-already-in proxy the stat already uses, so the verb and
`--quick squeeze` agree on what a squeeze is.

It is **not** the first later action. After an open the first later
action is usually the call; `--after raise` would miss the squeeze.
The Faced Next table keeps the first-action partition (fold / call
/ raise / …) and adds squeeze as an overlay row when one happened.
`--live` plus `--after raise` still works.

`--check` holds an open-call-squeeze hand (the open is selected)
and a no-caller 3-bet (it is not).

### Added — 2nd / 3rd barrel in the c-bet packs

`QUICK_PACKS` for `cbet_flop` and `raise_cbet` now include
`cbet_turn` (2nd barrel) and `cbet_river` (3rd barrel). Missed 3rd
Barrel is the `cbet_river` negative, next to Missed 2nd Barrel.
Named reports: Raise C-bet, 2nd Barrel, Missed 2nd Barrel, 3rd
Barrel, Missed 3rd Barrel -- each a `--quick` key the engine
already had, with related spots from the c-bet tree.

Won$ / Won hand% stay out of the pack. They are spots-sourced and
blank under a street filter; putting them here would reprint the
empty-column failure the pack exists to stop.

### Not this, on purpose

No HUD. No solver. No interval on Action Profit or Call Profit
(v1 is a mean of priced rows). No Dispersion / EV diff. No richer
pin (two full `--by` grids). No assigning later pot back onto a
bet that was called -- that stays unpriced on Action Profit.

### What's next

Unchanged: richer pin, Dispersion / EV diff, Multi-Player compare
as its own view, Won$ in a street pack (needs a decision-sourced
column or it blanks), live `check.py` on a real `hands.db`.

---

## Expression syntax + aliases

Two v1 slices that close the planned H2N reports/filters arc.

**Expressions.** A plain stat is a frequency in a spot. An expression is
math over those frequencies -- no nesting, no second filter language.

    python query.py --cohort 'Value(3Bet) < 2 and Opps(3Bet) > 100' --filter 3bet
    python query.py --cohort 'Value(wtsd) > 30 and Opps(wtsd) > 50'

`Value(S)` / `Cases(S)` / `Opps(S)` bind S to `stats.BY_KEY` (`3Bet` is
`threebet`, `WentToSD` is `wtsd`). `Hands()` is the players-table count.
`and` / `or`, comparisons, `+ - * /`, and `if(cond, a, b)` are the rest.
`eval()` is not used. Rates are grouped by `site` and `player` together
-- grouping by name alone would merge two rooms that share a screen
name. The compact `vpip>=40,pfr<=10` string is unchanged; `and` / `Value(`
is what switches grammars. `--class` stays a flag beside the expression.

There is no `WentToSDCases` catalog entry. That is `Cases(wtsd)`, and
the denominator is `Opps(wtsd)` (saw a flop), not `Hands()`.

**Aliases.** A named group of `(username, room)` accounts in
`aliases.json` (gitignored). Single-person merges histories as one
player; a group is a villain pool.

    python aliases.py --create me --player NAME --site acr --single
    python aliases.py --import accounts.csv
    python query.py --alias me --filter "Flop c-bets"
    python query.py --hero --vs-alias nits --street flop
    python query.py --villain-type fish --reg --stats

CSV is `Alias, Username, Room, Is Single Person`. `--player me` does
not expand an alias. `--hero` still covers imported hero seats via
`is_hero`. `--villain-type` / `--vs-class` is the typed form of
`--vs-fish` over `players.py` classification.

`--check` on `expr.py`, `aliases.py`, and the query wiring uses
in-memory fixtures and a temp store -- no corpus, and nothing creates
an empty `hands.db`.

### Not this, on purpose

No nested expressions. No H2N full stat-name catalog or `[MP;IP]`
suffixes. No `VsHeroCases` / `AmountWon` / `ActionProfit` in
expressions. `--by player` still splits alias members. No
save-cohort-as-alias. No HUD. No solver.

---

## Notes + marked hands

Off-table study. Player notes, a star on a hand, and a tag catalog --
Hand2Note's note/mark UX without a HUD. The store is `notes.db`, not a
table inside `hands.db`, because a rebuild of the derived tables is
exactly the kind of thing that used to delete columns, and a note is
not a derived column.

    python notes.py --add "calls too wide" --player NAME --hand ID
    python notes.py --mark ID --tag leak
    python query.py --marked --hands
    python query.py --hand ID --mark --tag leak

`--hand` on a note picks the player from the hand when you do not
name one. Templates are a small CRUD list (seeded once). A note can
carry a spot/stat string (`--spot "Flop c-bets"`). The window and the
page mark from the hand list. Compact Hand View is unchanged.

`--check` holds the CRUD, the seat-to-player pick, and that
`--marked` / `--tag` / `--noted` select the starred hand on an
in-memory table -- no corpus.

### Not this, on purpose

No HUD overlay. No solver. No HH export of tagged hands. No paste-HH
into a stat note. No Compact Hand View rewrite.

---

## Multi-Player cohorts

Hand2Note's Range Research is one report over a set of people.
`--cohort` already selected players; it did not say how many hands
they covered, it did not accept the compact string the research
brief writes, and Pin / compare returned *before* the pool was
parked -- so a two-column view of "fish with 100+ hands" was the
whole database wearing that heading.

    python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --filter 3bet
    python query.py --cohort-hands 100 --cohort-vpip 40+ --cohort-pfr <=10

The header is now `#players` and `#hands`, then the ordinary report
numbers. Existing filters, Smart Reports and Pin run on the pooled
hands. `40+` is `>=40`. `--class fish` is the cheap reg/fish
checkbox; it was already a flag.

The window's Players dialog takes the compact string. The page grew
a Multiple Players fieldset. `--check` holds the parse, the header
shape, and that compare under a cohort sees only those players --
no `hands.db` required.

### Not this, on purpose

No HUD. No solver. No Expression Value/Opps strings
(`VPIP>30 AND HandsCount>1000`) -- that is the Expressions
milestone. No Preflop Range on the pool. No Bet Sizes view. No
second Multi-Player compare (two cohorts side by side); `--versus`
already compares two populations.

---

## Pin / side-by-side compare

Hand2Note's `+` adds a pane; Pin freezes one report while you change
the other. `--pin` used to be an alias for `--versus` -- the Holm
table of every stat -- which is a test, not a pane. It is now the
two-column summary those two spots actually need:

    hits / opps     THIS              PINNED
    freq            30.0%             40.0%
    hits / 1000     12.0              8.0
    action profit   +2.40 bb          −1.10 bb
    freq THIS − PINNED   −10.0 pts  [−18, −2]

`--compare "Flop c-bets" "Flop vs c-bet"` names both sides.
`--hero` stays on both; leaving it off B would compare you to the
pool and look like a finding. `--versus` is still the Holm table.

The window and the page draw the same two columns above the rest
of this report. Changing the filter leaves the pin where it is.

`--check` holds the person-keeping, the two reports staying
different, and a two-hand fixture whose first-in Action Profit is
(+10 − 5) / 2.

### Not this, on purpose

No HUD. No second `--by` grid (richer pin). No Multi-Player view.
No interval on Action Profit -- v1 is a mean of priced hits.

---

## Custom filter builder as a FilterDef, not a second language

Hand2Note's custom filter is a street-by-street action graph with
modifiers on the node. The graph here was already a line string
(`--flop XBmC`). The modifiers were a pile of flags, some missing.
`FilterDef` is that pair as a value: it parses argv, emits argv, and
a JSON object `--filter '{"street":"flop","first_raise":true}'` is
the same door as a typed command. Hits/Opps, stats, the hand list,
Faced Next and `--save` stay one pipeline.

New modifiers, over columns `decisions` already has:

- `--first-raise` -- the open, or the first raise of a bet. A flop
  cbet is `--first-in`, not this.
- `--last-action` -- this decision ended the street.
- `--size 0.4-0.75` / `50%+` / `40-75` -- a pot-frac range beside
  the `s m l p o` letters.
- `--stack 100+` / `<40` / `80-200` -- `eff_bb`. `--deep` / `--short`
  still work.

`--players`, `--live`, `--first-in`, `--last-raise` and the line
flags were already there. The window grew first-raise / last-action
chips, a size-range box and a stack box; the page grew the same plus
the action-line fields the window already had.

`--check` holds the AST round-trip, the JSON object, the range
parsers, and a four-row table that tells a first raise from a cbet
and a last action from the one before it.

### Not this, on purpose

No HUD. No second filter language (`filter list|save|load` is still
`--filters` / `--save` / `--filter`). A line token cannot name a
seat -- `--pos` / `--vs` name who this decision is. No graph widget.

---

## Compact Hand View in report hand lists

A list of timestamps is a list you have to open. Hand2Note's compact
hand view puts the betting on the row -- positions, sizes in bb, the
board and pot at each street -- so a scan of forty rows is a scan of
forty hands. `CompactHandRenderer(hand)` is that encoding, a pure
function over the dict `hand_detail` already returns.

    BTN R3  BB C2 | 7h Ks 8c (6)  BB X  BTN X | 8d (6)  BB B4.5  BTN C4.5 | Th (15)  BB B86'  BTN C86

`X` is a check. `B`/`C`/`R` carry a size in bb. A trailing `'` is
all-in. The focus seat -- the player the row is about -- is marked:
underline on the page and in a colour terminal, `_R3_` in the window
because Tk's Treeview cannot underline a substring. Position colours
are fixed (H2N cannot customize yet either). Double-click still opens
the full replay.

`--hands`, the window's hands tab, and the page's hands view all
render it. `--check` holds the H2N example, a fold/check line, an
all-in bet, an all-in call (`C10'`, not `R10'` -- 95 of 236 all-ins
here are calls), and the batch path the lists actually use.

### Not this, on purpose

No HUD. Blind posts, dead chips and a straddle are not tokens -- the
line starts at the first voluntary action. Hole cards stay in the
combo column. Early folds are kept. EP1–EP3 are not seats this
project has. A six-way flop is not truncated.

### What's next

Unchanged from the reports work: richer pin, Call Profit Rate /
Dispersion / EV diff, Multi-Player compare, squeeze as its own Faced
Next verb, Missed 2nd/3rd barrel and Won$ in the Raise C-bet pack.

---

## Smart Reports, related spots, and a report that matches its filter

The tracking half of Hand2Note is a tree of named situations, a popup
that says what happened *here*, and a click to the neighbouring spot.
This had the engine for all three and the surface of none of them:
five guessed-at presets, a stats dump of whatever still had `n > 0`,
and a report tab that led with VPIP under every filter.

### Added — a Smart Reports tree, not five guesses

`SMART_REPORTS` is the spots a tracker user actually clicks between:
steal and blind defense, facing a 3-bet / 4-bet, the pot types, flop
c-bet and the other seat, donk and check-raise, turn and river barrels,
the same ideas in a 3-bet pot. Family and related sit beside the argv
so `--presets`, the window's report box and the page's report menu
draw the same tree. Saved reports still land at the end, under *saved*.

Opening one **replaces the situation and keeps the person**. Leftover
river chips AND-ed onto "Flop c-bets" used to match nothing and look
like a broken report. The window, the page and `--preset` agree on
that split; a cohort is still not a report, for the reason it never
was.

### Added — what they did in this spot

`--stats` (and the stats tab, and the page) now leads with fold /
check / call / bet / raise of the decisions the filter already
selected, each with its `n` and a Wilson interval. A named stat asks
a different question -- "of the times a cbet was possible" -- and
stays the table underneath. The five verbs partition `decisions`;
`--check` asserts the counts sum to the row count, because a missing
verb would shrink every percentage and look like a tight pool. All-in
is counted beside them: 95 of 236 here are calls.

### Added — related spots

From a flop c-bet: the other seat, IP / OOP, the turn, the 3-bet-pot
version. CLI prints them under `--stats` as `--preset` lines you can
paste; `--related` prints only those. The window draws them as
clicks under the filter line; the page does the same. Generated
variants that are already a named report use that name, so the box
and the clicks stay one list.

`--hero` is not part of the situation. A report opened on your own
hands still recognises itself and still offers neighbours.

### Changed — report columns follow the spot

`columns_for` picks the stats the filter is about. A river filter
that still leads with VPIP is a report of a different street: VPIP's
chance is preflop, so every cell was empty and the `n` column --
which was VPIP's denominator -- printed as zero. That was the report
tab on "River bets". `--show` still wins; without it, flop spots
show flop stats. The row `n` is now how many decisions the filter
selected in that bucket, not VPIP's chance.

### Checked

`query.py --check` holds every built-in report to the same "builds
and narrows" test as a flag, asserts every related name is a report,
that `columns_for` drops VPIP on a river filter, that the action mix
covers every decision, and that a report opened on hero is still
that report. `app.py --check` asserts applying a spot rebuilds its
flags and that opening a report drops the previous street.
`gui.py --check` opens a preset the same way the command line does.

### Added — Hits / opportunities / Hits per 1000

Hand2Note prints these on every filtered report. If the filter already
names an action (`--quick cbet_flop`), hits are the matching rows and
opportunities are the chance with the action taken off. If it is only
a situation, opportunities are the matching rows and hits are the
aggressive ones among them. Hits/1000 is per thousand player-hands of
the person being measured -- mixing that with the frequency is how a
70% cbet on 12 flops looks like a leak you see every orbit.

### Added — Action profit (v1)

Profit attributed to the filtered action, not the hand, as bb/hand
over priced hits. One SQL CASE feeds the report mean and the act bb
column on --hands, so those two cannot drift.

  * fold = 0
  * bet 5 into pot 10, everyone folds = +10 (pot_before; the bet is
    returned, so it is not subtracted)
  * bet 5, face a raise, fold = -5 (amount on THIS action)

Distinct from Won$ of the filtered hands, all-in EV / EV diff, and
Call Profit Rate (deferred). The mean ignores unpriced hits rather
than scoring them as 0 -- a called pot stuffed with zero would look
like the action was break-even.

Unpriced, on purpose: called-and-played-on (later pot is not this
action), multiway unless everyone folds, later streets after a call,
rake not subtracted (and won=0 after rake is unpriced, not a guessed
loss), MTT, an uncalled overage coming back (v1 credits +pot_before
only). The note sits next to Hits/Opps.

### Added — Faced Next / Next Actions, and click-to-filter

`--after fold` is the first later action by another seat; `--then bet`
is the first later action by this player. Each row now carries
frequency, hits/opps (branch / parent), and Action Profit v1 on the
parent action given that continuation -- "I cbet and they folded" is
+pot, not Won$. `--faced-next` / `--next-actions` print that table
as its own report; `--from` is `--filter`; `--branch fold` applies
the row (`--after` or `--then`). `--after none` is nothing further;
`fold-out` / `3bet` alias fold / raise. Squeeze is `--live` plus
`--after raise`, not a third verb.

`--hit` is `--quick`: click-stat on the command line. The window
double-clicks a stat row into `--quick` and a Faced Next row into
`--after`; the page does the same with a click.

### Added — outcome block, StatPacks, custom builder, pin

A filter is the spot. Applying it now also:

- prints **All Villains Fold / One Villain Call / Villain Raise** of
  the aggressive rows (`--outcome fold-out|call|raise-back`). That is
  the pot's answer, not Faced Next's first later action. The three
  partition bets; `--check` asserts the counts sum.
- swaps `--by` columns to a **StatPack** when the filter is a top
  `--quick` key. Raise C-bet leads with raise_cbet, not VPIP. Showdown
  stats stay out of the pack -- they cannot see a street filter.
- accepts the custom builder flags the research brief named:
  `--first-in`, `--last-raise`, `--size s|m|l|p|o`, `--players`,
  `--live`. Size letters are `lines.bucket`, so `--flop XBmC` and
  `--size m` agree.
- opens a spot as `--filter <name|json>` (report, then quick key,
  then JSON argv) and compares two as `--pin "Flop vs c-bet"`.
  `--pin` keeps who is being measured; `--versus` still takes raw
  flags when the other side is a different population.

The window and the page grew the same controls: first-in / last-raise
chips, size and outcome picks, player-count boxes, a pin combobox
next to the report box. Clicking an outcome row is `--outcome`.

`query.py --check` runs the shape and a three-hand fixture without
`hands.db`, then the live corpus when it is there.

### Not this, on purpose

No HUD. No solver. No second filter language -- every new report is
argv `build` already understands. Custom stats and saved reports are
untouched.

### What's next

- **Richer pin.** The window shows the pinned spot's hits and action
  profit above this one. A true side-by-side report tab (two column
  packs, same `--by`) is still `--versus` on the command line.
- **Call Profit Rate, Dispersion, EV diff.** Action-profit v1 leaves
  called-and-played-on unpriced on purpose; those extras need a priced
  call or a solver.
- **Multi-Player compare** as its own view. `--versus` already compares
  two populations; a window for it is not this PR.
- **Squeeze as its own Faced Next verb.** A squeeze is a raise with
  callers already in; v1 keeps it as `--live` + `--after raise`.
- **Missed 2nd/3rd Barrel, Won$, Won hand%** in the Raise C-bet pack.
  The core subset (hits/opps/freq + outcomes + Action Profit + the
  related decision stats) is what ships; those extras are either
  already a `--quick` key or a spots-sourced stat that blanks under
  a street filter.

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
