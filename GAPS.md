# Tracking-half gaps, frozen on purpose

The NLHE study chrome that is this project's tracking half is
**frozen**. Dock-back closed the last pane-lifecycle gap that
was still in the H2N Reports cockpit. What remains below is
not unfinished work on this track. It is out.

A new track starts when John asks for one. Until then, do not
grow more Reports / Statistics / Sessions chrome, and do not
quietly fill any of these in because they sit next to something
that just shipped.

**No HUD. No solver.** Those were deleted on 5 Sep 2026
(`4927a11`). They are a different product. If a solver is
wanted it is a separate repository, not a folder that grows
back.

**PLO study is a new track**, stacked on the NLHE freeze. Import
(including Bodog/Bovada), `--game` / `--game-type`, Omaha 2-of-4
strength, 4/5-card compact, and the 13×13 gate shipped. What stays
out of this track is still out: no HUD, no solver, no invented
equity, no combinatoric Omaha heat map, no PokerStars Omaha import.

`--check` still does not invent EV. All-in EV on the win graph
is only the priced two-way case with cards to come.

---

| out | why it stays out |
|---|---|
| **Rakeback overlay** | A skin on the win graph, not a filter. The graph already has four lines; a fifth that is a deal with the room is a different question. |
| **WYSIWYG profile editor** | The Profile Menu switches a curated id list. Editing that list in-place is a second product. Extra profiles live in `profiles.json`. |
| **Full 256 board / bet editors** | `--board` chips and `--size` buckets are the cuts people click. A 13×13 of every texture × every size is a solver surface wearing a tracker label. |
| **Dispersion / EV diff** | Skipped on the Bet Sizes run and still skipped. It needs a priced all-in on every line, which this database does not have. Inventing it is how a graph lies. |
| **More Expression atoms** | `Value` / `Cases` / `Opps` / `Hands` are the cohort grammar. `VsHeroCases`, `AmountWon`, `ActionProfit`, nested `Value(Value(…))`, and H2N's `[MP;IP]` suffixes stay report columns or stay out. |
| **HUD / solver** | Out of scope. A tracker says what people do. |
| **View-as-opponent** | The report is the seat the filter named. Flipping the seat in-place is a second writing of every pane, and the two writings will drift. `--vs-hero` / `--vs-player` are the filter; they are not a costume. |
| **Anon IDs / import skip flags** | The importer sniffs a file and skips what it cannot identify. It does not invent an anonymous identity and it does not take a skip list. Ignition already has no names; ACR already has them. A flag that drops hands at import is how a pool silently shrinks. |
| **Omaha equity / combinatoric heat map** | Four hole cards have no two-card combo and no Hold'em all-in EV. `--game plo --chart` is gated, not drawn empty. A real PLO matrix is a different shape and is deferred. Inventing 169 squares, or scoring 2+3 as seven-card Hold'em, is a lie. |
| **PokerStars Omaha** | HEADER accepts the file; `parse_hand` still returns None. Flip that once a Stars Omaha history has sat next to the money check. |
| **Player class on PLO** | `players.classify` is a Hold'em measurement (the `games.HOLD` default on `totals`). A `--game plo --cohort fish` report uses people typed on NLHE. |

Chip EV, session merge/split, a live-table VPIP sort, and a
board-slice group editor sit in the same bucket: named in
earlier runs, not opened.

The documents that used to say "Dock-back is deferred" are
wrong as of this freeze. Dock-back shipped. These did not, and
will not, on this track.
