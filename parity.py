"""
Live-corpus lock on the H2N tracking-half invariants.

The in-memory fixtures in `query.py --check` prove the functions
against a hand-built table. This module proves the same facts after
a real import: the committed HH in `fixtures/parity/` go through
`importer.load` and `CHAIN`, then A–H run on the derived tables. A
derivation that silently drops a line still looks fine in a table
somebody typed; it does not survive here.

    python parity.py              verify-parity, PASS or FAIL
    python parity.py --check      the same
    python query.py --verify-parity

No HUD. No solver. No invented EV. The corpus is synthetic and
tiny on purpose -- enough hands to make each invariant move, not
enough to look like a live database.
"""

import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import importer
import players
import query
import sessions
import strength
from stats import BY_KEY

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures" / "parity"

# Alice / Bob / Carol are pinned after load. The auto-classifier
# refuses on a handful of hands (unknown is an answer); the
# exclude invariant needs a known reg and a known fish, which is
# what the Who-is-Reg pin is for. The file is a temp path so this
# never writes the user's player_types.json.
PINS = (("acr", "Alice", "reg"),
        ("acr", "Bob", "fish"),
        ("acr", "Carol", "reg"))

# Clock the Today invariant is written against. start-of-day 6 and
# ACR −3h: a 02:00 room stamp is 23:00 local yesterday and is in;
# a 10:00 room stamp is 07:00 local and is out.
TODAY_NOW = datetime(2026, 9, 2, 3, 0, 0)
TODAY_CLOCK = sessions.Clock(
    start_of_day=6,
    offsets={"acr": -3, "ignition": 0, "pokerstars": 0},
    now=TODAY_NOW,
)


def load_corpus():
    """
    Import the committed HH through the same path the window uses.

    A temp database: `--check` must not create or touch `hands.db`.
    Types are pinned on a temp file for the same reason.
    """
    if not FIXTURES.is_dir():
        raise SystemExit(f"no fixture corpus at {FIXTURES}")
    scratch = Path(tempfile.mkdtemp(prefix="parity-"))
    db = scratch / "hands.db"
    types = scratch / "player_types.json"
    got = importer.load([FIXTURES], db)
    if not got["added"]:
        raise SystemExit(
            f"fixture corpus loaded 0 hands from {FIXTURES} "
            f"(files={got['files']}, unknown={got['unknown']})")
    importer.rebuild(db)
    for site, player, klass in PINS:
        players.set_type(site, player, klass, path=types, db_path=db)
    return db, types, got


def _count(con, where):
    return con.execute(
        f"SELECT COUNT(*) FROM decisions WHERE {where}").fetchone()[0]


def _hands(con, where):
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT hand_id FROM decisions WHERE {where} "
        f"ORDER BY 1")]


def check_A(con):
    """Hits/Opps/freq: empty is 0, never a crash."""
    fails = []
    empty = query.spot_summary(con, "1=0", [])
    if empty["hits"] or empty["opps"] or empty["pct"] or empty["per_1k"]:
        fails.append(
            f"A: empty spot_summary was {empty}, not zeros")
    if empty["pct"] is None:
        fails.append("A: empty freq was None -- that used to crash * 100")
    grid = query.statistics_of(con, "1=0")
    if grid["n"] != 0:
        fails.append(f"A: empty statistics_of n={grid['n']}, not 0")
    if any(r["pct"] for r in grid["rows"]):
        fails.append("A: empty statistics_of invented a non-zero rate")
    # A real filter with no hits still has a freq of 0, not a crash.
    miss, _, _ = query.build(["--street", "river", "--made", "quads"])
    miss_s = query.spot_summary(con, miss, ["--street", "river"])
    if miss_s["pct"] is None:
        fails.append("A: zero-hit freq was None")
    return fails


def check_B(con):
    """Action Profit: fold=0, and it is not Won$ of the hand."""
    fails = []
    fold = query.action_profit_of(
        con, "hand_id='cp-900001' AND action='F'")
    if fold["bb_per_hand"] != 0 or fold["priced"] != 1:
        fails.append(
            f"B: fold AP was {fold['bb_per_hand']} on {fold['priced']} "
            "priced, not 0 on 1")
    fold_won = query.amount_won_of(
        con, "hand_id='cp-900001' AND action='F'")
    if fold_won.get("bb_per_hand") in (None, 0, fold["bb_per_hand"]):
        fails.append(
            f"B: fold Won$ was {fold_won.get('bb_per_hand')}, which "
            "must differ from AP 0 (the blinds they lost)")
    # Uncontested open: AP is +pot_before/bb (+1.5); Won$ includes
    # the blinds they posted and is +1.0. Same hand, two numbers.
    raise_ap = query.action_profit_of(
        con, "hand_id='cp-900002' AND seat=1 AND agg=1")
    raise_won = query.amount_won_of(
        con, "hand_id='cp-900002' AND seat=1 AND agg=1")
    if raise_ap.get("bb_per_hand") is None:
        fails.append("B: uncontested open was unpriced")
    elif abs(raise_ap["bb_per_hand"] - 1.5) > 1e-9:
        fails.append(
            f"B: uncontested AP was {raise_ap['bb_per_hand']}, not +1.5")
    if raise_won.get("bb_per_hand") is None:
        fails.append("B: uncontested Won$ was empty")
    elif abs((raise_won["bb_per_hand"] or 0) - 1.0) > 1e-9:
        fails.append(
            f"B: uncontested Won$ was {raise_won['bb_per_hand']}, not +1.0")
    if raise_ap.get("bb_per_hand") == raise_won.get("bb_per_hand"):
        fails.append("B: AP equalled Won$ on the uncontested open")
    return fails


def check_C(con):
    """Quick/Custom golden, and a nest is parent ∧ row."""
    fails = []
    for key in ("cbet_flop", "threebet"):
        st = BY_KEY[key]
        qw, _, _ = query.build(["--quick", key])
        if st.chance not in qw or st.action not in qw:
            fails.append(
                f"C: --quick {key} compiled to {qw!r}, missing "
                f"{st.chance!r} ∧ {st.action!r}")
        n_q = _count(con, qw)
        n_and = _count(con, f"({st.chance}) AND ({st.action})")
        if n_q != n_and:
            fails.append(
                f"C: --quick {key} selected {n_q}, chance∧action {n_and}")
        if n_q == 0:
            fails.append(
                f"C: --quick {key} matched nothing in the corpus")

    # Smart Reports stay loose: Flop c-bets is the chance (PFA, flop,
    # facing check), not the hits. Quick cbet_flop is chance ∧ action.
    # Counts can agree on a tiny corpus where every chance was taken;
    # the SQL is the divergence, and it is the one that must not move.
    smart, _, _ = query.build(list(query.SMART_REPORTS["Flop c-bets"]))
    quick, _, _ = query.build(["--quick", "cbet_flop"])
    if "agg = 1" in smart.replace("agg=1", "agg = 1"):
        fails.append(
            "C: Smart Flop c-bets required agg=1 -- that is the "
            "Quick filter, not the loose report")
    if "agg = 1" not in quick.replace("agg=1", "agg = 1") and \
            "agg=1" not in quick.replace(" ", ""):
        fails.append(
            "C: Quick cbet_flop dropped the action -- that is the "
            "loose Smart report")

    parent = ["--street", "flop", "--hero"]
    child = query.drill_child(parent, {"flag": "--action", "value": "bet"})
    pw, _, _ = query.build(parent)
    cw, _, _ = query.build(child)
    pred = dict(query.ACTION_MIX)["bet"]
    n_child = _count(con, cw)
    n_and = _count(con, f"({pw}) AND ({pred})")
    if n_child != n_and:
        fails.append(
            f"C: nest --action bet was {n_child}, parent∧row {n_and}")
    if n_child == 0:
        fails.append("C: flop hero bet nest matched nothing")

    d = query.FilterDef.from_argv(["--street", "flop", "--first-in"])
    if query._canonical(d.to_argv()) != query._canonical(
            ["--street", "flop", "--first-in"]):
        fails.append(f"C: FilterDef round-trip drifted: {d.to_argv()}")
    fw, _, _ = query.build(d.to_argv())
    n_fd = _count(con, fw)
    n_first = _count(con, f"({pw}) AND (first_in = 1)")
    if n_fd != n_first:
        fails.append(
            f"C: custom flop first-in was {n_fd}, parent∧first_in {n_first}")
    return fails


def check_D(con):
    """
    exclude_reg_vs_fish changes Statistics only.

    If this starts moving Reports or Sessions, the test must fail --
    that is the H2N boundary, and it is how a rebuild flag leaked
    into every view the last time it was put in OPTIONS.
    """
    fails = []
    alice, _, _ = query.build(["--player", "Alice", "--site", "acr"])
    off = query.statistics_of(con, alice, [], exclude=False)
    on = query.statistics_of(con, alice, [], exclude=True)
    if on["n"] >= off["n"]:
        fails.append(
            f"D: exclude did not shrink Statistics ({off['n']} → {on['n']})")
    if on["excluded"] < 1:
        fails.append("D: exclude reported 0 dropped rows")
    tb_off = next(r for r in off["rows"] if r["key"] == "threebet")
    tb_on = next(r for r in on["rows"] if r["key"] == "threebet")
    if tb_off["k"] != 2:
        fails.append(f"D: Alice 3bet hits were {tb_off['k']}, not 2")
    if tb_on["k"] != 1:
        fails.append(
            f"D: excluded 3bet hits were {tb_on['k']}, not 1 "
            "(reg-vs-fish 900005 must drop; vs-reg 900006 stays)")

    # Reports: --quick threebet still sees both, including the
    # reg-vs-fish raise. A leak into build() would drop one.
    reports, _, _ = query.build(
        ["--quick", "threebet", "--player", "Alice"])
    if _count(con, reports) != 2:
        fails.append(
            f"D: Reports --quick threebet saw {_count(con, reports)}, "
            "not 2 -- exclude leaked into build()")
    leaked, _, _ = query.build(
        ["--exclude-reg-vs-fish", "--quick", "threebet",
         "--player", "Alice"])
    if "n_fish" in leaked or "player_class = 'reg'" in leaked:
        fails.append("D: build() applied exclude_reg_vs_fish to Reports")
    if _count(con, leaked) != 2:
        fails.append(
            f"D: Reports with the exclude flag saw {_count(con, leaked)}, "
            "not 2 -- the flag must be a no-op on Reports")

    # Sessions ignore opponent class. The sit-down still has every
    # Alice cash hand, including the one Statistics dropped.
    built = sessions.ensure(con)
    acr = [s for s in built if s.site == "acr" and s.hero == "Alice"]
    n_session_hands = sum(s.hands for s in acr)
    n_alice = con.execute(
        "SELECT COUNT(*) FROM spots WHERE is_hero=1 AND site='acr' "
        "AND player='Alice' AND fmt <> 'MTT'").fetchone()[0]
    if n_session_hands != n_alice:
        fails.append(
            f"D: Sessions held {n_session_hands} Alice hands, spots "
            f"has {n_alice} -- exclude must not touch sit-downs")
    return fails


def check_E(con):
    """Today × start-of-day + room tz, against imported stamps."""
    fails = []
    sql = TODAY_CLOCK.today_sql()
    if "site = 'acr'" not in sql:
        fails.append(f"E: Today SQL was not per-site: {sql!r}")
    ids = [r[0] for r in con.execute(
        f"SELECT DISTINCT hand_id FROM spots WHERE is_hero=1 "
        f"AND site='acr' AND ({sql}) ORDER BY played_at")]
    if "cp-900007" not in ids:
        fails.append(
            "E: 02:00 room stamp (local 23:00 yesterday) missed Today")
    if "cp-900008" in ids:
        fails.append(
            "E: 10:00 room stamp (local 07:00) stayed in Today")
    if "cp-900001" not in ids:
        fails.append("E: midday Sep 1 hand missed Today")
    warn = TODAY_CLOCK.empty_warning(0, kind="today")
    if not warn or "start-of-day 06:00" not in warn or "acr-3h" not in warn:
        fails.append(f"E: empty warning lost the clock: {warn!r}")
    return fails


def check_F(con):
    """Win graph: WOS / WSD partition, red + gray = green."""
    fails = []
    where, _, _ = query.build(["--hero", "--site", "acr"])
    got = query.graph_of(con, where, unit="bb")
    if got["n"] < 2:
        fails.append(f"F: graph drew {got['n']} hands, need two")
        return fails
    wos = got["series"]["won_wos"]
    wsd = got["series"]["won_wsd"]
    won = got["series"]["won"]
    if not any(abs(v) > 1e-9 for v in wos):
        fails.append("F: WOS line was flat -- the fold-out hands vanished")
    if not any(abs(v) > 1e-9 for v in wsd):
        fails.append("F: WSD line was flat -- the showdown hand vanished")
    if not all(abs(won[i] - wos[i] - wsd[i]) < 1e-9
               for i in range(got["n"])):
        fails.append("F: red + gray != green at some hand")
    # No all-in in this corpus: yellow equals green. Inventing EV
    # here would be a lie.
    if got["series"]["all_in_ev"] != got["series"]["won"]:
        fails.append("F: All-in EV drifted from Amount Won with no all-in")
    return fails


def check_G(con):
    """Heatmap showdown-bias coverage, histogram Other + Weak %."""
    fails = []
    cov = query.coverage_of(con, "1=1")
    by = {s["site"]: s for s in cov.get("sites") or []}
    if "ignition" not in by or "acr" not in by:
        fails.append(f"G: coverage missed a site: {list(by)}")
    else:
        if abs(by["ignition"]["pct"] - 100) > 1e-6:
            fails.append(
                f"G: Ignition coverage was {by['ignition']['pct']}, "
                "not 100% (every seat was dealt cards)")
        if by["acr"]["pct"] >= 90:
            fails.append(
                f"G: ACR coverage was {by['acr']['pct']}% -- folds hid "
                "cards and this is the showdown-bias failure")
        if by["ignition"].get("reveals") is not True:
            fails.append("G: Ignition was not marked revealing")
        if by["acr"].get("reveals") is not False:
            fails.append("G: ACR was marked revealing -- it hides folds")
    chart = query.chart_of(con, "1=1")
    if not (chart.get("coverage") or {}).get("sites"):
        fails.append("G: heatmap dropped coverage -- the SD-bias hides")

    flop, _, _ = query.build(["--street", "flop"])
    hist = query.hist_postflop_of(con, flop)
    if not hist["rows"] or hist["rows"][-1]["key"] != strength.HIST_OTHER:
        fails.append("G: Other was not the last histogram bar")
    if sum(r["n"] for r in hist["rows"]) != hist["n"]:
        fails.append("G: histogram bars do not sum to the seen count")
    if hist["n"] == 0:
        fails.append("G: flop histogram saw no shown hands")
    weak_n = sum(r["n"] for r in hist["rows"] if r.get("is_weak"))
    want = 100.0 * weak_n / hist["n"] if hist["n"] else 0.0
    if abs((hist.get("weak_pct") or 0) - want) > 1e-9:
        fails.append(
            f"G: Weak % was {hist.get('weak_pct')}, not {want} "
            "from the bar counts")
    return fails


def check_H(con):
    """Export writes the filtered set, not the whole corpus."""
    fails = []
    built = sessions.ensure(con)
    acr = [s for s in built if s.site == "acr" and s.hero == "Alice"]
    if len(acr) < 2:
        fails.append(f"H: expected 2+ Alice sit-downs, got {len(acr)}")
        return fails
    first = min(acr, key=lambda s: s.start)
    dest = Path(tempfile.mkdtemp()) / "session.txt"
    got = sessions.export_hands(con, first, dest)
    if got["wrote"] != first.hands:
        fails.append(
            f"H: export wrote {got['wrote']}, session has {first.hands}")
    text = dest.read_text(encoding="utf-8")
    for hid in first.hand_ids:
        raw = hid[3:] if hid.startswith("cp-") else hid
        if f"Hand #{raw}" not in text:
            fails.append(f"H: export missing {hid}")
    others = [hid for s in acr if s.id != first.id for hid in s.hand_ids]
    for hid in others:
        raw = hid[3:] if hid.startswith("cp-") else hid
        if f"Hand #{raw}" in text:
            fails.append(
                f"H: export of {first.id} included {hid} from another "
                "sit-down -- that is the whole corpus, not the filter")
            break

    # Reports-side: matching_hands of a filter is that filter, not
    # every hero hand.
    flop, _, _ = query.build(["--hero", "--site", "acr", "--street", "flop"])
    rows = query.matching_hands(con, flop)
    ids = {r["id"] for r in rows}
    want = set(_hands(con, flop))
    if ids != want:
        fails.append(
            f"H: matching_hands flop was {sorted(ids)}, filter {sorted(want)}")
    all_hero = set(_hands(con, "is_hero = 1 AND site = 'acr'"))
    if ids == all_hero:
        fails.append("H: flop matching_hands was every hero hand")
    return fails


def check():
    """
    A–H against the committed corpus. Returns True on a clean pass.

    In-memory stop-ships (empty freq, fold AP, Reports ignore exclude)
    already live in `query.py --check`. This is the live import of the
    same facts. Both have to stay green.
    """
    fails = []
    db, _types, got = load_corpus()
    print(f"corpus  {got['added']} hands  "
          f"({', '.join(f'{k} {v}' for k, v in got['by_site'].items())})")
    con = sqlite3.connect(db)
    sessions.ensure(con)
    for name, fn in (
            ("A Hits/Opps/freq", check_A),
            ("B Action Profit", check_B),
            ("C Quick/Custom nest", check_C),
            ("D exclude_reg_vs_fish", check_D),
            ("E Today / start-of-day", check_E),
            ("F Graph WOS/WSD", check_F),
            ("G Heatmap / Weak %", check_G),
            ("H Export = filtered", check_H)):
        bad = fn(con)
        print(f"{name:28}  {'yes' if not bad else 'NO'}")
        for line in bad:
            print(f"    {line}")
        fails.extend(bad)
    con.close()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return 0 if check() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
