# Usage

Every command, what it does, and how to read what comes back.

---

## Loading hands

One loader for every site. Each file is identified by the header its hands
begin with -- never by which folder it was in or which client is installed
-- and goes to that site's parser; a file no parser recognises is counted
and skipped. Safe to re-run: hands are keyed by the site's own hand id, so
a folder that overlaps one already loaded contributes only what is new.
That is the intended way to use it as you keep playing.

```bash
python importer.py "C:/path/to/any/HH"      # load, whatever sites are in it
python importer.py --scan                   # where hand histories are on this machine
python importer.py --refresh                # load what is new from those places

python sites.py                             # the sites this program knows
python sites.py --stats                     # what is in the database, per site
python sites.py --check                     # prove every site's import
```

Folders are walked recursively for `*.txt`. Omaha files are skipped. ACR
hand ids are prefixed `cp-` so the sites can never collide.

`acr.py` and `ignition.py` are parsers and nothing else; neither is run
directly.

### Reading `sites.py --stats`

```
hands by site and format:
  acr  RING     $0.10    3697
  acr  BLITZ    $0.25     795
  ignition   ZONE     $0.25     749

coverage of what each site actually shows:
  ignition     18803 seats,   18802 with cards (100.0%),      9 distinct names
  acr    43666 seats,   10132 with cards ( 23.2%),    777 distinct names
```

`fmt` is `RING`, `BLITZ` (ACR's fast-fold), `ZONE` (Ignition's) or
`MTT`. The coverage block is the point of having both sites: Ignition's
100% is every folded hand; ACR's 777 names are identity.

### Reading `sites.py --check`

```
ignition
  money adds up           100.00%  (3892/3892 hands within a cent, in = stated pot, won <= pot)
  positions balanced      100.00%  (6 positions, 1512-1512 each)
  blinds posted by blinds  98.47%  (110 of 7190 posts were dead posts from other seats)
  named opponents         none -- this site has no names

acr
  money adds up            99.92%  (8277/8284 hands within a cent, in - house = won)
  positions balanced      100.00%  (6 positions, 3508-3508 each)
  blinds posted by blinds  99.02%  (160 of 16370 posts were dead posts from other seats)
  named opponents            786  (298 with 30+ hands, 85 with 100+, 7 with 500+)
```

Every site loaded is held to the same three tests, because a parser fails
silently -- it drops the line it misreads and every number downstream comes
out plausible. Money: everything that went in came back out minus the
house's cut, where the site writes its cut; where it does not (Ignition),
everything that went in equals the pot the site declared. Positions: over
thousands of full-table hands every seat is every position equally often.
Blinds: the seat the button says is the small blind is the one that posted
it. A site with names is also asked whether names recur, since that is
what a site with names is for. MTT is excluded from money -- chips are not
dollars, and a tournament history can begin mid-hand.

Your own results are shown as `won - posted - invested`, which is profit.
Beware any tracker — including an earlier version of this one — that reports
the pot you collected as your winnings; a player who builds a big pot and
wins it can still have lost money on the hand.

---

## Building the derived tables

Order matters. Each reads the one above it.

```bash
python spots.py            # one row per player per hand   (~1 min)
python spots.py --check

python decisions.py        # one row per decision          (~2 min)
python decisions.py --check

python lines.py            # how the betting went          (~15 s)
python lines.py --check

python strength.py         # what each hand is              (~20 s)
python strength.py --check

python players.py          # who each player is             (~15 s)
python players.py --check

python decisions.py --index   # just the indexes, without rebuilding
```

`lines` writes onto `decisions`, so it goes last and it goes again every
time `decisions` is rebuilt. Skip it and the line filters match nothing,
without complaining.

`spots` holds what is true of a whole hand: the cards, the money, whether
they saw a flop, whether they reached showdown. `decisions` holds what was
true at each moment somebody had to act — 50 columns of situation: the pot,
what it cost to call, who raised last, whether they are in position, how
deep, what the flop looks like.

The split is not arbitrary. Seeing a flop is a property of a hand — a player
all-in before the flop sees it and never acts again — so it cannot live in a
table of decisions.

---

## Asking questions

### Filtering by how the betting went

The betting on each street is written down as a short string, and you filter
it with a pattern:

```bash
python query.py --flop XBC              # checked, bet, called
python query.py --flop "XB*"            # checked to somebody who bet
python query.py --turn XX               # checked through
python query.py --pre "*R*R*"           # somebody 3-bet
python query.py --line "*R*R*/XBC/XX/*" # all of it at once
python query.py --node "*/XB"           # it is your turn, facing a bet
```

| | |
|---|---|
| `F` fold | `X` check |
| `C` call | `B` bet |
| `R` raise | `A` all-in |
| `*` anything | `?` any one action |

A `/` separates streets. Add a size letter after a bet to say how big:
`s` a third or less, `m` half, `l` two-thirds to three-quarters, `p` pot,
`o` an overbet. So `--flop XBmC` is a flop that went check, half-pot bet,
call. Sizes are buckets rather than percentages because a filter written
against an exact percentage matches almost nothing.

`--node` is the one to reach for when measuring a decision: it is the hand
cut short at the moment the player had to act, so it never contains what
they did next. `--line` is the whole hand and includes it.

`python lines.py --common flop` lists the lines that actually occur.

---

### The 13x13 chart

`--range` says what the hands *became* — top pair, a flush draw. `--chart`
says what they *were*:

```bash
python query.py --site ignition --quick threebet --chart
python query.py --site ignition --pos BTN --chart --show rfi
```

Without `--show` each cell is that combo's share of the range that reached
the spot — H2N's range diagram. With `--show` each cell is that stat for
that combo: `--show rfi` on the button is an opening range, read off the
hands themselves rather than assumed.

Aces top left, suited above the diagonal, offsuit below. In the window it is
the **chart** tab, with a *chart of* box beside it to switch between the two.

```
      A    K    Q    J    T    9    8 ...
  A  3.9  3.3  2.0  1.2  1.9  1.1  1.4
  K  5.6  3.6  2.2  1.7  0.8  0.6  0.5
  Q  5.1  2.6  2.8  1.4  0.3  0.5  0.5
```

Two things to know before trusting one:

- **It is the range that was SEEN.** Ignition shows every hand at showdown
  including the folds; ACR shows 23%. The fraction is printed above every
  chart, and on ACR a chart is of the hands that got to showdown, which is a
  stronger set than the hands that reached the spot. `--site ignition` is
  the filter that fixes it.
- **Composition counts each player-hand once, not each decision.** A hand
  that reached the river has four rows in `decisions` and one that folded
  preflop has one, so counting rows would draw a range visibly stronger than
  the one that actually arrived — in a picture nobody would think to doubt.
  `query.py --check` asserts the cells sum to the hands seen.

A rate cell is left blank below three occurrences: one hand dealt twice is
not a frequency. A composition cell is not, because a combo dealt twice
really is 0.1% of the range.

---

### Reports study-flow (the window and the page)

`python app.py` opens on the **study** tab. That is Hand2Note's
Reports cockpit: who you are measuring, the filter as chips, a
Smart strip, four panes, and a compact hand list. The command line
is not required.

Default panes: Results, Stack Sizes, Positions, Next Action. Click
a row to AND it onto the filter (`parent ∧ row`). The breadcrumb
is the path; three deep, then the last crumb is replaced. Detach
is not built.

The demo that has to work without typing flags:

Unfiltered → Stack **80-120** → Next Action **Call vs OR** → **Axs**
→ compact list → double-click a row to replay → right-click another
stack row and **Pin this**.

`--action` is this decision (fold / check / call / bet / raise).
`--then` is still the next street. Call vs OR is `--street preflop
--facing open --action call`, offered as a composed row when the
filter has not already named a street. `--combo Axs` expands to
every suited ace; `22+`, `pairs`, `broadways` are the same idea.
`--result won` is the whole-hand result via `spots` (cash only).

The page (`python gui.py`) has the same study view as its default
tab. Pin, Mark, and Add to Note hit the same backends.

The study strip now carries the postflop **hand-value histogram**
and its Weak % (Air / Draws / Weak pair by default). Click a bar
to AND `--hist-group` onto the filter; right-click flips whether
that group counts as weak. Other is always last, for leftovers.

Not in this cockpit: Detach, multi-profile Save/Open, a board
editor. Sessions is the neighbouring tab. Statistics is
the one after study.

### Statistics

Hand2Note's other primary study surface: a curated grid of rates
for one subject, then click a stat to see the range that produced
it. No HUD. No solver.

```bash
python query.py --statistics --fmt cash --last-sessions 10
python query.py --statistics --exclude-reg-vs-fish --hit threebet
python query.py --call-range threebet --hero --hands
python players.py --set NAME --as fish --site acr
python players.py --types
```

The window's **statistics** tab is the same loop. Who is the
player / cohort / alias already on the filter bar. Cash | MTT,
a date range, and last N sessions sit on this tab (last-N is
cash sit-downs; MTT has none and the box is ignored). Cohort
counts -- how many regs / fish / unknown -- sit above the grid.

Click **3bet** → the 13×13 of those raises. A postflop stat
(CBet Flop, …) also draws the **hand-value histogram** and Weak %
— how much of the shown range sits on bars tagged weak. Click a
bar to keep only that group; right-click flips `is_weak` and the
percentage moves. **Call Range** is the sibling: same chance, a
call instead (Call Open Raise). A cell narrows to that combo.
Compact hands under the chart; double-click replays. **Open in
Reports** writes `--quick threebet` (or `--call-range threebet`)
onto the study tab and keeps who / cash / dates / last-N, and a
clicked histogram bar as `--hist-group`. It does not take the
exclude flag with it.

**exclude reg-vs-fish** is a Statistics compute flag, not a
filter. H2N's rebuild toggle: a regular's decision with a fish
still in drops out of the Statistics sample. Fish-vs-reg stays --
that is how a fish plays against regs. Unknown stays. Reports and
Sessions never see it; `query.build` skips the flag so a Reports
command that happens to carry it is a no-op. `--villain-type`
stays a Reports filter and is not this toggle.

Player type is auto Who-is-Reg (`players.classify`: intervals,
unknown is an answer) plus a manual pin in `player_types.json`
(gitignored, the same shape as `aliases.json`). A pin wins. The
**Who is Reg** dialog writes the file and restamps `decisions`,
so Statistics exclude and Reports `--class` / `--reg` see the
same person.

`--fmt cash` is everything that is not a tournament (`fmt <>
'MTT'`). `fmt='RING'` is not a pool; ACR ring is in cash too.

Not this: multi-profile Save/Open, a board editor, Detach.

### Sessions

A session is a sit-down: consecutive hero cash hands at one site,
split when the gap exceeds 60 minutes or the room / named hero
changes. It is not a calendar day and not a Reports filter.

```bash
python sessions.py                         # the list (Won bb on the row)
python sessions.py list --today
python sessions.py --hours 4
python sessions.py --since 2026-09-01 --until 2026-09-02
python sessions.py --start-of-day 6 --tz acr=-5
python sessions.py show FIRST_HAND_ID      # same as --show: summary + hands
python sessions.py --show FIRST_HAND_ID
python sessions.py graph FIRST_HAND_ID
python sessions.py graph FIRST_HAND_ID --csv --out session-graph.csv
python sessions.py export-hands FIRST_HAND_ID --out session.txt
python query.py --session FIRST_HAND_ID --stats
```

The window's **sessions** tab is the same loop: date bar → list
(Won / Won bb) → click a row for the official four-line win
graph (Amount Won, All-in EV, Won without Showdown, Won at
Showdown) and compact hands → **Export session hands** →
**Open in Reports** (a `--session` chip on the study tab).
bb / $ on the graph, click a name to hide a line. Mark /
Unmark / Note on a compact row, the same store as the hands tab.

**Today is a footgun.** It is the most recent start-of-day hour in
*your* clock, not the date stamped on the hand. Rooms write their
own local time and do not say which. Set the start-of-day hour
(6 if you play until 4am) and each room's HH timezone offset
(hours added to `played_at` to reach you). An empty Today names
those two numbers rather than looking like missing hands.

Raw `--since` / `--until` on a report stay literal `played_at`
comparisons, so a saved filter does not move when the clock prefs
do.

MTT is out (chips are not dollars). Opponent class is ignored --
sessions are not affected by `--reg` / `--fish` or by
`exclude_reg_vs_fish` on the Statistics tab. Ignition is one hero
stream per site; the seat identity would split a real sit-down.
Export needs the original HH files still at `hands.source`.

Not this: Chip EV, rakeback overlay, live-table VPIP sort,
merge/split.

### Smart Reports, and walking to a neighbouring spot

The report box is a Hand2Note-style tree of **named situations** -- steal
attempts, flop c-bets, facing a 3-bet, river in a 3-bet pot -- not a list
of five guesses. Opening one replaces the street / pot / facing you had
clicked and keeps who you are measuring, so "Flop c-bets" as hero is that
spot on your own hands, not that spot AND leftover river chips.

```bash
python query.py --preset "Flop c-bets"
python query.py --preset "Flop c-bets" --hero --by position
python query.py --presets                 # the tree, then your saved ones
python query.py --preset "Flop c-bets" --related
```

`--stats` (the default) now leads with the popup report Hand2Note opens
on a filter:

- **Hits / opportunities / hits per 1000 hands.** Hits and opportunities
  are the two halves of a stat (`--quick cbet_flop` is hits; the chance
  without the action is opportunities). Hits/1000 is per thousand
  player-hands of the person being measured, not per thousand
  opportunities -- a 70% cbet on 12 flops is still a rare event.
- **Action profit** in bb/hand, attributed to *this* action, not the
  hand. The mean is over **priced hits** -- the matching actions v1
  can score -- sitting next to Hits/Opps. It is not opportunities
  (times they could have acted and did not), not Won$ of those hands,
  not all-in EV, and not Call Profit Rate.

  - fold → always 0
  - bet 5 into pot 10, everyone folds → **+10** (they take the pot;
    their bet comes back, so it is not subtracted)
  - bet 5, face a raise, fold → **−5** (the chips they put in on
    THIS action)

  `--hands` prints `act bb` and `call bb` beside `net bb` so the
  three cannot be mixed up. A dash is unpriced. Open cases for
  Action Profit, left unpriced on purpose:

  - a call that is played on (later pot is not assigned back --
    that number, when they called, is Call Profit Rate)
  - multiway unless every other seat folds
  - later streets after a call
  - rake: not subtracted from +pot; if rake ate the pot (`won=0`)
    the line is unpriced
  - MTT (chips are not dollars)
  - uncalled extra chips come back; v1 credits `+pot_before` only
- **Call profit rate** in bb/hand, the sibling used to compare
  alternatives when both call and raise were legal. It is
  **accounting of actual calls**, not the EV of calling when they
  raised. A raise in the same filter stays unpriced so the mean
  cannot be dragged by a counterfactual we do not have.

  A priced call is `to_call > 0` (facing a bet or raise, including
  a limp) and not all-in (so a raise was still possible), cash
  game. Profit is `(won − chips this seat put in from THIS action
  on) / bb`. Later streets are assigned back on purpose -- that is
  the point of the number. `won` already has rake out where the
  site writes it.

  - call 5 into pot 15, win, no more chips in → **+15** (`pot_before`)
  - call 5 and lose → **−5**
  - call 5, then bet 10, win 40 → **+25** (later streets assigned back)
  - all-in call → unpriced (no raise was available)
  - MTT → unpriced

  The mean is over **priced calls**, with its `n`. On "Flop vs
  c-bet", Action Profit is the priced raises (and folds as 0);
  Call Profit Rate is the priced calls. Sitting next to each
  other is the comparison. Neither is EV.

  Both means print **n priced of n hits**. When two or more rows
  were priced, a 95% t interval sits on the observed mean --
  sampling uncertainty, not EV. n=1 prints the mean and says why
  there is no band. Wilson stays for rates.
- **This spot** -- fold / check / call / bet / raise of the filtered
  decisions, each with its `n` and a Wilson interval.
- **Faced next** / **next actions** -- a report, not just a mix. Each
  row is what the other seat did next, or what this player did next,
  with frequency, hits/opps (branch / parent), and Action Profit v1
  on the parent action given that continuation. `--after fold` and
  `--then bet` open a row; `--after none` is "nothing further".
  `--after fold-out` and `--after 3bet` are aliases for fold and
  raise (an open's 3-bet is a raise). **`--after squeeze`** is its
  own verb: any later squeeze (the squeeze stat's chance -- facing
  an open, `n_live >= 4`, `pot_bb > 4`), not the first later action.
  After an open the first later action is usually the call; the
  squeeze is the raise that comes after that call. `--live` plus
  `--after raise` still works; this is that, named.

```bash
python query.py --from "Flop c-bets" --faced-next
python query.py --from "Flop c-bets" --next-actions
python query.py --from "Flop c-bets" --branch fold
python query.py --from "Flop c-bets" --after fold --stats
python query.py --from "Steal attempts" --after squeeze --stats
python query.py --hit cbet_flop --stats
```

  `--from` is `--filter`. `--branch fold` applies `--after fold`
  (or `--then` under `--next-actions`). `--hit` is `--quick`: click-
  stat on the command line. In the window, double-click a row; on
  the page, click it.
- **Outcome** -- what the pot did with this bet, which is a different
  question from Faced Next. Faced Next is the first later action;
  outcome is fold-out / call / raise-back over everybody still in.
  `--outcome fold-out` opens that row; a click does the same.
- **Bet sizes** -- Action Profit, hits/opps and freq by pot-frac
  bucket (`s m l p o`, the same edges as `--size m` and
  `lines.bucket`). The current filter is the opportunities. Checks
  and folds have no size and are not a row, so the freqs will not
  sum to 100% on a situation that includes them. `--bet-sizes` and
  `--by size` print the pane; `--size m` opens a row. Action Profit
  is still v1 -- later pot on a called bet stays unpriced.

A Quick Filter also **swaps the report columns** to the pack for that
spot. `--quick raise_cbet` leads with raise c-bet, not VPIP, and the
pack includes 2nd barrel (`cbet_turn`) and 3rd barrel (`cbet_river`).
The flop c-bet pack does the same. Missed 2nd / 3rd Barrel are
`--quick` negatives and named reports:

```bash
python query.py --filter "Raise C-bet" --by position
python query.py --filter "2nd Barrel" --stats
python query.py --filter "Missed 3rd Barrel" --hero
```

Won$ / Won hand% sit on the Raise C-bet / flop-c-bet / barrel packs
as a **join extra**, not as a Stat column. A spots-sourced Stat
blanks under a street filter; this join does not. They are whole-hand
`net_bb` of the filtered seats, MTT excluded, and the note says so.
On `--by` they are extra columns after the decision stats.

```bash
python query.py --hero --filter "Flop c-bets"
python query.py --hero --filter raise_cbet --by position
python query.py --filter "Flop c-bets" --pin "Flop vs c-bet"
python query.py --hero --compare "Flop c-bets" "Flop vs c-bet"
python query.py --hero --filter "Flop c-bets" --pin "Flop vs c-bet" --by position
python query.py --hero --street flop --bet-sizes
python query.py --hero --street flop --by size
python query.py --presets                 # also --filters
```

`--filter` is `--preset` with a wider lock: a Smart Report, a `--quick`
key, or a JSON argv list.

**`--pin`** freezes another named report, same person, and prints a
two-column summary: hits/opps, the primary frequency, hits per 1k,
Action Profit v1 when priced, and Call Profit Rate when a call with
raise available is in the filter. The frequency gap is an interval
on the difference. Changing the filter leaves the pin where it is --
that is the point. In the window and on the page the pin box does
the same thing above the rest of this report.

With `--by position` (or any other split) the pin also draws **two
`--by` grids**, keys aligned. `--by size` / `--bet-sizes` draws two
Bet Sizes tables. Without a split it draws two full stat packs
under the compact summary. That is the richer pin -- two filters'
breakdowns, not only THIS vs PINNED as six rows.

**`--compare A B`** is the same table with both sides named. Who-flags
(`--hero`) apply to both.

**`--versus`** is the other verb: every stat, Holm-corrected, for when
the question is "is this gap real?" not "what did each spot do?"

No Multi-Player pane. No Dispersion / EV diff.

The custom action builder is flags, not a second language.
`FilterDef` is those flags as a value -- line patterns plus the
modifiers -- so a saved JSON object and a typed command compile to
the same argv `build` already runs.

```bash
python query.py --street flop --first-in --size m --outcome fold-out
python query.py --players 6 --live 2 --last-raise --aggressive
python query.py --street flop --first-raise --last-action --size 0.4-0.75
python query.py --flop XBmC --stack 100+ --stats
python query.py --filter '{"street":"flop","first_raise":true,"size":"m"}'
python query.py --save "flop first-raise half-pot"
python query.py --filters                  # also --presets
```

Modifiers:

| flag | means |
|---|---|
| `--first-in` | first to put chips in on this street |
| `--first-raise` | first raise on this street (an open, or the first raise of a bet -- a cbet is not) |
| `--last-raise` | this player already raised on the street (`was_agg`) |
| `--last-action` | this decision ended the street |
| `--players` / `--live` | who sat / who is still in |
| `--size m` | pot-frac bucket (`s m l p o`, same as `lines.bucket`) |
| `--size 0.4-0.75` | a numeric pot-frac range (`50%+`, `<=0.33`, `40-75`) |
| `--stack 100+` | effective stack in bb (`<40`, `80-200`). `--deep` / `--short` still work |
| `--flop XBmC` | the street's action line, already the graph |

`--save` / `--filter` / `--filters` / `--forget` are list, load and
delete. There is no `filter list\|save\|load` subcommand -- those
would be a second door on the same file.

v1 does not let a line token name a seat (BTN bets). The line is the
sequence; `--pos` / `--vs` name who this decision is. No graph widget.

Then the named stats that can still occur inside the filter. Asking for
a preflop stat inside `--street flop` gives nothing, which is correct
rather than a bug.

Under that sit **related spots**: the other seat, the next street, the
same idea in a fatter pot. Each line is a `--preset` you can paste, or a
row you can click in the window and on the page. `--related` prints only
those, when you want to walk the tree without another stats dump.

The report tab / `--by` **changes its columns to match the situation**.
A river filter used to lead with VPIP, whose chance is preflop, so every
cell came back empty and the `n` column printed as zero. `--show` still
wins when you name the columns; without it, flop spots show flop stats
and river spots show river stats.

### Saving the filter as a report

The reports in the box are the built-in situations above. The next one
is whatever you were looking at last Tuesday:

```bash
python query.py --pot 3bet --ip --street river --save "my river spot"
python query.py --preset "my river spot" --by position
python query.py --presets                 # every report, built in and saved
python query.py --forget "my river spot"
```

A saved report is a preset, in the same list as the built-in ones, so it
turns up in `--preset`, in `--presets` and in the window's report box
without anything being told about it. In the window it is the **SAVE AS
REPORT** button at the foot of the filter dialog.

`--forget` takes either a saved stat or a saved report; it refuses if you
somehow have both under one name rather than guessing which you meant.

Two things are deliberately not saved with a report:

- **The reporting options.** `--by`, `--show` and `--min` say how to draw an
  answer, not which rows it is about. A report that remembered `--show
  vpip,pfr` would rewrite the columns of every view it was opened in.
- **The player cohort.** A cohort chooses people and a report describes a
  situation. Save the situation and pick the players beside it.

Reports live in `filters.json` beside the database, gitignored, next to
`stats.json`. Both are yours rather than the project's.

---

### Making a stat of your own

The built-in stats are the ones everybody wants. The one *you* want this
week is probably not among them, because the number of spots a hand can be
in is the number of strings those letters spell.

So any filter can be saved as a stat. The filter becomes the chance to do
something, and `--do` says what the doing is:

```bash
python query.py --pot 3bet --ip --headsup --street river --node "*/XBC/XBC/X" \
    --define river_barrel3_ip --label "river 3rd barrel, 3bet IP" --do bet
```

That is Hand2Note's "continued bet on the river in a 3-bet pot in position"
written in one line: a 3-bet pot, heads up, our player in position, both
earlier streets checked to them and bet, and now the river checked to them
again.

It prints the definition and tests it against the database on the spot,
which is what H2N's manual means by pressing *Test Stat* before using one:

```
saved 'river_barrel3_ip' -- river 3rd barrel, 3bet IP
  from:   pot 3bet, ip, headsup, street river, node */XBC/XBC/X, bet, with nothing to call
  chance: pot_type IN ('3bet') AND is_ip = 1 AND n_live = 2 AND street IN ('river') AND node GLOB '*/XBC/XBC/X'
  action: agg=1 AND to_call=0
    52.6%   +/-20 ? n=19
```

From then on it is a stat like any other — a column, a split, a filter:

```bash
python query.py --show river_barrel3_ip --by position
python query.py --quick river_barrel3_ip --hands
python stats.py --custom              # the ones you have saved
python query.py --forget river_barrel3_ip
```

`--do` is one of `bet`, `raise`, `aggressive`, `call`, `check`, `fold`,
`continue`, `allin`. `--per hand` counts a player once however often they
acted, the way VPIP does; the default counts every decision.

In the window it is the **SAVE AS STAT** button at the foot of the filter
dialog, and the same window forgets them again.

Two things it will not let you do, both because the result would look like a
stat rather than like a mistake:

- **`--aggressive`, `--allin` and `--quick` cannot be in the filter.** They
  say what the player *did*, and a stat whose chance already contains its
  own action reads 100% for ever.
- **A saved stat cannot take a built-in's name**, or `--show cbet_flop`
  would quietly start meaning something else.

The definitions live in `stats.json` beside the database. That file is
yours, not the project's: it is gitignored, and survives a `git pull`.

One limitation worth knowing. The action strings record the order of play
and not *who* played it. Heads up that is unambiguous — with `--headsup
--ip`, a flop of `XBC` can only be villain checks, you bet, villain calls.
Multiway it is not, and there a `--node` pattern means "the betting went
like this" rather than "this player did this". Positions are deliberately
kept out of the strings, because eight-handed tables are recorded against
six position names and 1,076 hands have one label covering two seats.

---

### Comparing two populations

```bash
python query.py --reg --regs-only --versus "--reg --with-fish"
python query.py --reg --regs-only --versus "--reg --with-fish" --show fold_to_river_bet
```

Prints both rates, the **difference**, and an interval on that difference —
which is not the same question as whether the two rates' own intervals
overlap, and is far less strict. It also corrects the p-values for how many
stats were compared at once, because thirty comparisons will produce one at
p < 0.05 with nothing going on.

**Name the stat with `--show` and it is a test. Compare all thirty and the
best one is a hypothesis.** The correction charges by the size of the
family, so one named question is worth much more than the pick of thirty.

Where nothing survives, the footer says how big a difference the sample
*could* have found — "no difference" and "not enough hands to see one" are
different findings.

---

### Filtering by what the turn or river did

```bash
python query.py --turn-card flush --street turn      # the turn brought a flush card
python query.py --river-card pair --pot 3bet         # the river paired the board
python query.py --turn-card brick --board dry        # a dry flop and a turn that changed nothing
```

`over` · `pair` · `flush` · `straight` · `brick`

A **flush card** is one that takes some suit to three on the board. On the
turn, **straight** means the board has come to one card off a straight; on
the river it means that card arrived. Those are the events that matter on
each street, and one definition covering both would describe neither. A
**brick** did none of the four.

`--board` still describes the flop, which arrives all at once. These
describe a single card landing on a board that was already there.

---

### What the range actually held

```bash
python query.py --pool --street river --facing bet --range
python query.py --pool --street flop --facing bet --range
```

A frequency is a number about somebody; a range is a number about what to
do. "He bets the river 40%" tells you nothing until you know how much of
that 40% is a hand that cannot call.

```
  two pair        30.9%     264        ###############
  middle pair     16.9%     144        ########
  board pair      12.4%     106  weak  ######
  high card       10.4%      89  weak  #####

  WEAK            25.4%   -- hands that cannot call
  STRONG          74.6%
```

The line between weak and strong is drawn under **middle pair**, and it is
one list in `strength.WEAK` precisely so that disagreeing with it is a line
changed rather than an argument.

The **histogram** is the same question grouped into bars, with Weak %
on top — H2N’s “how often they bluff” readout on a betting range:

```bash
python query.py --street flop --action bet --hist-postflop
python query.py hist-postflop --street flop --facing bet
python query.py --hist-group air --street flop --stats
```

Air / Draws / Weak pair default to weak. Other is always last and
catches leftovers (an unexpected `made`). A top pair with a flush
draw stays on Top pair. Right-clicking a bar (or passing a flipped
`is_weak` into `hist_postflop_of`) changes Weak %; the classifier
does not.

**It is the range that was *seen*.** Ignition shows every hand at showdown
including the folds; ACR shows 23%. So on an ACR-heavy filter this describes
the hands that reached showdown, which is the stronger half of what was
really there. The header says what fraction of the selection had cards to
read, every time.

---

### Filtering by what the hand is

```bash
python query.py --made "top pair" --kicker weak     # top pair, bad kicker
python query.py --made set,trips --street river     # sets and trips
python query.py --fd nut --sd gutshot               # the nut flush draw with a gutshot
python query.py --combo-draw --street flop          # both draws at once
python query.py --drawing --pot 3bet                # any draw in a 3-bet pot
python strength.py --common                         # what the pool turns up with
```

| | |
|---|---|
| `--made` | high card, board pair, weak pair, under pair, middle pair, top pair, overpair, two pair, trips, set, straight, flush, boat, quads, straight flush |
| `--kicker` | top, good, weak — only where a pair uses a hole card |
| `--fd` | nut, second, weak, backdoor |
| `--sd` | oesd, double gutshot, gutshot |

Four columns rather than one, so a combo draw is a flush draw and a straight
draw *at once* instead of a fourteenth category, and "pair plus a flush
draw" is `--made "top pair" --fd nut`.

**These need the cards**, and the cards are known for 20,465 postflop
decisions out of 94,017 — every Ignition hand including the ones that
folded, and 23% of ACR's. So they narrow hard, and a small `n` here is the
data rather than the filter. A `board pair` is a pair on the board that the
player does not hold; four hearts on the board is not a flush draw.

---

### Filtering by which players, not which spots

Every filter above narrows *situations*. This one narrows *people* first, and
then the situation filters apply inside what is left:

```bash
python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --filter 3bet
python query.py --cohort 'Value(3Bet) < 2 and Opps(3Bet) > 100' --filter 3bet
python query.py --cohort 'Value(wtsd) > 30 and Opps(wtsd) > 50' --pos BTN
python query.py --cohort-hands 100 --cohort-vpip 40+ --cohort-pfr <=10 --pos BTN
python query.py --cohort --hands ">=500" --site acr
python query.py --cohort --vpip ">=35" --pfr "<12" --pos BTN --street flop
python query.py --cohort --class fish --hands ">=100" --pot 3bet --by position
```

`--cohort` selects people first; every situation filter, Smart Report, Pin
and Faced Next then runs on the **pooled** hands of those players. One
aggregated report, not a row per name.

Three writings of the same idea:

- **`--cohort 'vpip>=40,pfr<=10,hands>=100'`** — compact string. `40+` is
  `>=40`. `class=fish` / `site=acr` / `durable=1` are allowed in the
  string.
- **`--cohort 'Value(3Bet) < 2 and Opps(3Bet) > 100'`** — expression over
  plain stats. `and` / `or` / `Value(` switch out of the compact list.
  Do not mix `class=fish` into that string; use `--class` beside it.
- **`--cohort-hands 100 --cohort-vpip 40+ --cohort-pfr <=10`** — aliases
  that do not collide with `--hands` the view. A bare number on
  `--cohort-hands` means at least that many.
- **`--cohort --hands ">=500" --vpip ">=28"`** — the original flags. The
  first `--hands` after `--cohort` is the player's hand count; a second
  `--hands` is the view. If the compact string or `--cohort-hands` already
  set the sample, `--hands` is only the view.

`--class` (reg / fish / unknown) and `--durable` still work. In the window
it is the **Players** button (a compact box plus the same fields). On the
page it is the **multiple players** fieldset.

The heading says which players **and** how many hands they cover:

```
COHORT  vpip >=40, pfr <=10, hands >=100
  12 players · 4,810 hands

filter: 3-bet pots, cohort: vpip >=40, pfr <=10, hands >=100 (12 players, 4,810 hands)
```

`--site` picks the pool a player belongs to. The cohort is joined on site
as well as name, so the situations are narrowed to that site too.

**A cohort is not a saved report and cannot be part of one.** A cohort
chooses people and a report describes a situation; save the situation and
pick the players beside it.

A parked cohort also prints its **preflop range**: hole-card coverage
by site (Ignition shows every hand including folds; ACR is
showdown-biased), the top combos, and `--chart` / `--range` for the
13x13 and the postflop breakdown. A mixed-site cohort is a mixture of
a full range and a showdown-selected one -- the coverage line is
there so that cannot be read as "the range".

```bash
python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --chart
python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --range
python query.py --cohort 'vpip>=40,pfr<=10,hands>=100' --filter 3bet --stats
```

Not this, on purpose: nested `Value(Value(...))`; H2N's full stat-name
catalog and positional `[MP;IP]` suffixes; `VsHeroCases` / `AmountWon` /
`ActionProfit` as expression atoms; Bet Sizes as a *cohort* view (the
pane is on the current filter, not a second Multi-Player compare); two
cohorts side by side (that is `--versus` of two populations, already
shipped).

Bear the sample size in mind before reading anything into a small cohort. 85
ACR players have 100+ hands and eight have 500+, so `--hands ">=500"` is
eight people, and eight people are not a pool.

---

### Filtering by what kind of player

```bash
python query.py --reg --vs-reg          # how regulars play each other
python query.py --reg --vs-fish         # and how they play a recreational
python query.py --villain-type fish --reg --stats
python query.py --vs-player NAME        # against one named opponent
python players.py --list reg            # who the regs are
python players.py NAME                  # one player in full
```

`--villain-type` / `--vs-class` is the typed form of `--vs-fish` /
`--vs-reg`: `vs_class IN ('fish')`, or a comma list (`reg,unknown`).
The classification is still `players.py` -- reg, fish, or unknown.

A **reg** plays a third of hands or fewer and raises at least one in ten. A
**fish** is loose or passive — over a third of hands, or almost never
raising while still coming in often. Everybody there is not enough evidence
about is **unknown**, and neither filter selects them, so the two do not add
up to the pool.

`--vs-reg` and `--vs-fish` only mean anything while one opponent is left:
in a three-way pot there is no "the other player". They select heads-up
decisions by construction, which is 17% of the database.

`--regs-only` and `--with-fish` ask the same thing of a pot of any size —
everybody still in is a reg, or at least one of them is a fish — and between
them cover 63%. Use these unless the matchup really has to be heads up.

Only ACR names people. An Ignition ring identity lasts as long as somebody
stays in the seat, and Zone names nobody.

---

### `query.py` — the one you will use most

Every stat has always taken a filter; nothing could supply one. This can.

```bash
python query.py --help                                  every filter there is
python query.py --pool --pot 3bet --street flop --ip    what the pool does there
python query.py --hero --board mono --results           what it made you
python query.py --player dblj32 --pos BTN --hands       which hands they were
python query.py --where "eff_bb > 150 AND fl_paired=1" --stats
```

Filters combine freely, and there are three things to ask for.

**`--stats`** (the default) leads with what they did in the spot the filter
already named -- fold / check / call / bet / raise, each with its `n` --
then every named stat that *can* occur inside it, and silently drops the
ones that cannot. Asking for a preflop stat inside `--street flop` gives
nothing, which is correct rather than a bug.

```
filter: pool, site acr, pot 3bet, street flop, ip
524 decisions match

  [this spot]
  fold                     28.1%    +/-4    n=524
  check                    31.0%    +/-4    n=524
  bet                      22.4%    +/-4    n=524
  [flop]
  cbet flop                70.7%    +/-7   n=184
  fold to cbet             42.6%    +/-7   n=190
  [sizing]
  bets a third or less     50.6%    +/-7   n=170

related spots:
  Flop in 3-bet pots           related report
    --preset 'Flop in 3-bet pots'
  same spot, out of position   the other seat
    --oop --pot 3bet --street flop
```

**`--results`** is the money, and it works differently on purpose. Money is
a property of a whole **hand**; "in position on a monotone flop" is a
property of a **decision**. So the filter selects decisions, and the money
is then summed over the hands those decisions occurred in — because a player
in position on a monotone flop won or lost the whole pot, not the part of it
after the flop.

```
filter: hero, site acr, board mono
  hands                  69
  net                +307.4 bb   ($+47.34)
  per 100 hands      +445.6 bb/100
  error on that         141 bb/100   <- and this is why
```

That last line is not decoration. One hand's result has a standard deviation
around 11.7bb, so the error on a win rate is roughly `1170/sqrt(n)`. At 69
hands it is ±141bb/100, which is wider than the +445 it is qualifying.
**Win rates need thousands of hands. Frequencies need hundreds.** Filter
hard and you will be looking at frequencies, which is the right thing to
look at anyway.

**`--graph`** is the official Hand2Note four-line win chart over the
hands the filter selected, in play order. Amount Won (green),
All-in EV (yellow), Won without Showdown / the red line, Won at
Showdown (gray). Red + gray = green at every hand. A rising red
line is taking pots without a showdown; a falling one is letting
them go. `--unit currency` is dollars; the default is bb. `--csv`
(or `--out file.csv`) is the per-hand extractors and their
running totals -- the last `cum_won` is the Won summary
`--results` prints.

```bash
python query.py --hero --graph
python query.py --hero --pot 3bet --graph --csv --out win.csv
python query.py report graph --hero --unit currency --out win.csv
python sessions.py graph FIRST_HAND --csv
```

The same widget sits on the window's **graph** tab, on the
Reports study strip (it reshapes when you drill), and on a
session's detail. Click a line name to hide it; hover names the
hand. All-in EV is only priced for the clean two-way case with
cards to come -- nothing is invented. MTT is out.

Not this: rakeback overlay, Chip EV, Detach.

**`--hands`** lists the hands themselves, newest first, with the board and
what each one made. Under each row is a **compact hand** -- the same
single-line encoding Hand2Note puts in a report list: position, then
`X` / `B` / `C` / `R` and a size in bb, with `'` for all-in. Street
cuts show the flop (three cards) or the card that arrived, and the pot
in bb. The marked action (`_R3_` on the command line and in the
window; underlined on the page) is the seat that row is about, not
always the session hero. Double-click a row (or click it on the page)
to open the full replay; that path is unchanged.

```
  2024-03-01 12:04  acr         0.50  BTN  AhKd    +12.4    +3.0  7h Ks 8c 8d Th
    BTN _R3_  BB C2 | 7h Ks 8c (6)  BB X  BTN X | 8d (6)  BB B4.5  BTN C4.5 | Th (15)  BB B86'  BTN C86
```

v1 does not write blind posts, hole cards (the combo column has those),
or EP1–EP3 labels this project does not use. Early folds stay in the
line. A six-way flop is not truncated.

On the command line you open one hand by id:

```bash
python query.py --hand 5331315698
```

That replays it — who sat where, what they held, and the action street by
street with the pot before each decision. On Ignition every player's cards
are there including the folded ones, because the site shows them. On ACR a
seat reads `--` when the hand was never shown, which is different from
having been dealt nothing.

---

### Notes, marks, and tags

Off-table study. A report is what the pool did; a note is what you thought
about it. The store is `notes.db` beside the database -- gitignored, and
not rebuilt with `hands.db`, so a derivation rebuild does not delete a
sentence you wrote last Tuesday.

```bash
python notes.py --add "calls too wide" --player NAME
python notes.py --add "too wide here" --hand cp-123 --spot "Flop c-bets"
python notes.py --list --player NAME
python notes.py --bind cp-123 --note-id 4
python notes.py --mark cp-123 --tag leak
python notes.py --marked --tag leak
python notes.py --templates
python notes.py --template review --text "Come back to this."
```

`--hand` without `--player` picks the player from the hand: the report
row's seat if you passed `--seat`, otherwise hero, otherwise any named
seat.

The same store as a filter:

```bash
python query.py --marked --hands
python query.py --tag leak --hero --stats
python query.py --noted --filter 3bet
python query.py --hand cp-123 --mark --tag leak
python query.py --hand cp-123 --note "calls too wide"
```

The hand list prints a `*` on starred rows and the tags after the board.
In the window: Filter → General → marked / tag, and Mark / Unmark / Note
on the hands tab (and on the replay). On the page: the study chips and
the star on each row.

Not this, on purpose: live HUD "Add Note"; exporting tagged hands as a
history file; pasting a raw HH into a stat note. Compact Hand View is
already the line on the row -- this does not redo it.

### Expressions over plain stats

A plain stat is a frequency in a spot (`stats.BY_KEY`). An expression is
math over those frequencies, used first as a Multi-Player cohort:

```bash
python query.py --cohort 'Value(3Bet) < 2 and Opps(3Bet) > 100' --filter 3bet
python query.py --cohort 'Value(wtsd) > 30 and Opps(wtsd) > 50'
python query.py --cohort 'vpip>=40 and pfr<=10 and Hands()>=100'
```

| atom | meaning |
|---|---|
| `Value(S)` | `100 * hits / opps` (0 if they never faced the chance) |
| `Cases(S)` | hits |
| `Opps(S)` | opportunities |
| `Hands()` / `HandsCount()` | the `players` table hand count |

`S` is a registry key or a short alias: `3Bet` → `threebet`, `cbet` →
`cbet_flop`, `WentToSD` → `wtsd`. There is no `WentToSDCases` name --
that is `Cases(wtsd)`, and the denominator is `Opps(wtsd)` (hands that
saw a flop), not `Hands()`. `and` / `or`, comparisons, `+ - * /`, and
`if(cond, a, b)` are the rest. Expressions do not nest:
`Value(Value(vpip))` is refused.

A comma compact string without `and`/`or`/`Value(` is still the v1
field list. `--class fish` stays a flag beside the expression.

Not this, on purpose: the full Hand2Note stat-name catalog; positional
`[MP;IP]` suffixes; `VsHeroCases`, `AmountWon`, `ActionProfit` as
expression atoms (those stay report columns).

### Aliases

A named group of `(username, room)` accounts, stored in `aliases.json`
(gitignored, like `stats.json`). Not a table inside `hands.db`.

```bash
python aliases.py --create me --player HeroACR --site acr --single
python aliases.py --add me --player HeroPS --site pokerstars
python aliases.py --list
python aliases.py --import accounts.csv
python aliases.py --export accounts.csv

python query.py --alias me --filter "Flop c-bets"
python query.py --hero --vs-alias nits --street flop
```

CSV columns, Hand2Note's order: `Alias, Username, Room, Is Single Person`.
`Room` accepts the registry keys and the usual nicknames (`wpn`, `ps`,
`bovada`). **Is Single Person = true** merges those histories as one
player (hero on a second site). **false** is a style / group, usable as
`--vs-alias` -- the other seat is in that set.

`--hero` already merges every imported hero seat via `is_hero = 1`.
`--alias` is the same idea for names that are not that flag.
`--player me` does **not** expand an alias -- a screen name and an
alias can share a word.

Reports: pick a person, a single-person alias, or a group alias as a
pool (`--vs-alias`). `--by player` still splits the members; the alias
is a filter, not a rewritten identity.

Not this, on purpose: save-cohort-as-alias; HUD; treating `--player`
as an alias lookup.

### When nothing matches

Some perfectly reasonable filters cannot match anything, and rather than a
blank page you get the reason:

```
$ python query.py --ip --street preflop
0 decisions match
  'ip' and 'street preflop' never occur together -- who acts last is only
  settled once the flop is out
```

The ones worth knowing, because they will all be clicked eventually:

| filter | why it is empty |
|---|---|
| `--ip` / `--oop` with `--street preflop` | who acts last is only settled once the flop is out |
| `--pfa` with `--street preflop` | there is no preflop aggressor until preflop is over |
| `--board ...` with `--street preflop` | the flop had not come |
| `--facing bet/raise/check` with `--street preflop` | those are postflop words; preflop uses `open`, `3bet`, `4bet` |
| `--facing open/3bet/4bet` with a postflop street | and the reverse |
| `--pot limped` with `--street preflop` | a pot is not limped until preflop is over; during it, it is `unopened` |

None of these is a bug. They are the filter asking for something that could
not have happened, and the point of the message is that you can tell the
difference without having to guess.

Filters worth knowing: `--hero`/`--pool`, `--site`, `--player`, `--pos`,
`--street`, `--pot`, `--facing`, `--ip`/`--oop`, `--deep N`/`--short N`,
`--board mono,paired,connected,...`, `--combo`, `--multiway`/`--headsup`,
`--since`/`--until`, and `--where` for raw SQL over `decisions` when the
named flags run out. `--help` prints the full list with the SQL each becomes.

### `stats.py` — the stat engine

```bash
python stats.py --list              # every stat and its definition
python stats.py --custom            # just the ones you saved yourself
python stats.py --pool              # both pools, side by side
python stats.py --player NAME       # one opponent, every stat
python stats.py --check             # engine vs. the old derivation
```

`--list` prints each stat as the two filters that define it:

```
threebet           3bet   [preflop, per decision, decisions]
  chance: street='preflop' AND facing='open'
  action: agg=1
```

That is the whole design — a stat is a pair of filters over a situation, so
adding one is adding a definition, not writing a module.

Output looks like:

```
  [preflop]
  VPIP                     22.7%    +/-3   n=759
  RFI                      26.2%    +/-4   n=404
  3bet                     13.7%    +/-4   n=285
  fold to steal            77.4%    +/-7   n=146
  [flop]
  cbet flop                37.5%   +/-14   n=40
  fold to cbet             33.3%   +/-17 ? n=27
```

Read it like this:

- **`+/-N`** is half the width of the 95% Wilson interval, in percentage
  points. `13.7% +/-4` means the true figure is very likely between 10 and
  18. It is not decoration — it is the difference between a read and a guess.
- **`?`** marks fewer than 30 chances. Ignore the number.
- **`n=`** is what the percentage is a percentage *of*. `fold to steal
  n=146` means 146 chances to fold to a steal, not 146 hands.

The pattern above is typical and worth internalising: **preflop stats are
usable at a few hundred hands, postflop stats are not.** A player with 780
hands gives you a solid 3-bet number and a meaningless cbet number.

### `opponents.py` — what one opponent does differently

```bash
python opponents.py                   # everyone worth a plan, ranked
python opponents.py NAME              # one opponent in full
python opponents.py --check
```

A page of somebody's stats is not a read — most of the numbers on it are
what the whole pool does. So this prints only the stats where the player's
95% interval and the pool's do not overlap, biggest gap first, with what to
do about it:

```
  stat                     them     pool   read
  ------------------------------------------------------------------
  fold to steal           77.4%    69.5% ^  folds blinds to steals -- open every button
                           n=146
  limp                     0.0%     5.4% v  never limps
                           n=404
```

`^` is above the pool, `v` is below. The baseline is the pool **on their own
site**, since comparing a ACR player against a mixed baseline would
make every one of them look tight.

A player with nothing listed is not a failure of the tool. It means that on
this many hands they are indistinguishable from the pool, which is itself
worth knowing before you invent a read.

The leaderboard ranks by how many stats clear the bar, on the reasoning that
someone unusual in six ways is both more exploitable and more reliably
measured than someone unusual in one.

### `population.py`

Written before the stat engine and still hardcoding its own SQL. It does one
thing the engine cannot: split-half validation of a finding about the pool.

```bash
python population.py            # the pool's leak map and revealed ranges
python population.py --check    # split-half validation, PASS or FAIL
```

The solver modules that used to be documented here — `poptree.py`,
`leaks.py`, `bestresponse.py`, `walk.py`, `postflop.py` and `gtowizard/` —
were removed on 5 Sep 2026. They priced play against cached GTO Wizard
solutions, which is answering "what is correct" and is a different program.
`git checkout 4927a11 -- walk.py gtowizard/` brings any of them back.

---

## Loading hands after a session

```bash
python importer.py --refresh
```

Looks in the places this machine keeps hand histories, loads anything that
is not already in the database, and rebuilds the derived tables **only if
something was added**. In the window it is **Import → Import new hands**,
and the window says on launch when there is something to fetch:

```
30 hand history files written since your last import — Import ▸ Import new hands
```

That notice is a heuristic and deliberately a cheap one — a file touched
since the loader last read *might* hold new hands, one touched before it
cannot. It over-reports rather than parsing every file on the machine while
the window is trying to open, and it goes quiet once you import: the loader
records how far it has read, in a `meta` table beside the hands.

It compares against that mark and not against the newest hand's `played_at`,
which was the first attempt and was wrong. The 265 hands recovered above
were all *older* than hands already in the database, so against `played_at`
those files stayed "new" after being loaded and the window would have
offered them on every launch for ever.

**The rebuild is the slow half and all of it has to run.** Loading writes
`hands`, `seats` and `actions`; everything you can ask is derived from
those, in this order:

```
spots → decisions → lines → strength → players → indexes
```

About three quarters of a minute at twelve thousand hands. `importer.CHAIN`
is that list, and it is a list rather than five calls in a row because the
failure it exists to prevent is a stage being missed out of it: `decisions`
**drops its table**, and `lines`, `strength` and `players` each add their
columns back afterwards. A rebuild that stops early does not leave those
columns stale, it leaves them gone.

---

## Keeping it up to date

The window checks GitHub on every launch, on a worker thread, and says
something only when there is something to say. **Update → Update from
GitHub now** does it on demand; **Update → What version is this?** says
which commit is running and where the database is.

```bash
python update.py            check, and fast-forward if there is one
python app.py --no-update   start without checking
```

Three rules it does not bend:

* **Fast-forward only.** It refuses to merge, refuses to rebase, and refuses
  outright if anything local would have to be reconciled. Uncommitted work
  is never touched — the update simply does not happen, and says why.
* **It never restarts anything.** Python has already imported its modules by
  the time the pull finishes, so new code on disk is not new code in memory.
  The window says an update arrived; a restart uses it.
* **It never blocks.** No git, no network, no remote, a detached head — each
  is an ordinary answer rather than an error. Being unable to check is not a
  problem with the program.

A packaged .exe cannot replace itself while running, so there it tells you a
newer commit exists rather than pretending to update.

---

## Verifying everything

```bash
python check.py                 # every check, in dependency order
python check.py --quiet         # just the verdicts
python parity.py                # A–H on the committed fixture corpus
python query.py --verify-parity # the same
```

`parity.py` loads `fixtures/parity/` through `importer.load` and
`CHAIN`, then locks the H2N tracking-half invariants: Hits/Opps/freq
never divide by zero; fold Action Profit is 0 and is not Won$; a
Quick filter is chance ∧ action and a nest is parent ∧ row;
`exclude_reg_vs_fish` moves Statistics only; Today uses start-of-day
and the room timezone; the win-graph red and gray lines add to green;
heatmap coverage names the showdown bias; export writes the filtered
set. CI runs this. The corpus is synthetic and tiny -- enough for
each invariant to move, not a live database.

### Quick Filters are strict; Smart Reports are loose

A `--quick` key is a Stat: `(chance) AND (action)`. `--quick cbet_flop`
is the bets. A Smart Report of the same name is the *spot*: Flop
c-bets is `--street flop --pfa --facing check`, the chances, and the
columns then say how often the bet was taken. Tightening the Smart
report onto the Quick filter would make every frequency on that
report 100% and look like a finding. Custom FilterDef modifiers
(`--first-in`, `--size`, `--stack`) AND onto the parent the same way
a pane click does (`parent ∧ row`). This is intentional, not a
missing feature.

```
  PASS  acr
  PASS  spots
  PASS  decisions
  PASS  stats
  PASS  profile

PASS: all 5 checks
```

Run this after any change to a loader or a derivation, and after loading new
hands. The failure it exists to catch is not "a module broke" — it is "a
module was rebuilt and the ones below it were not", which leaves a database
where every table is individually fine and the set of them is wrong.

If something fails, **fix the earliest one first**; the later ones read its
tables and will fail as a consequence.

---

## Backing up

`hands.db` is gitignored and is the only copy of the parsed corpus.

```bash
cp hands.db hands.db.bak
```

Do this before any schema change. Re-parsing from the original histories is
possible but slow, and only works if you still have them.
