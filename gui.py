"""
The tracker as a local page, for when a window is not what is wanted.

`app.py` is the desktop application and the one to reach for. This serves
the same tool over http instead, which is worth keeping for the one thing a
window cannot do: reach it from another machine on the same network, or from
a phone beside the table. It is not the default and nothing depends on it.

Everything this shows, `query.py` could already answer. What it could not do
is let you change one thing and look again, which is most of what using a
tracker actually is -- you do not know the question until you have seen the
answer to a nearby one.

It is a local page rather than a desktop window for two reasons, and neither
is fashion. The graph is already an SVG, so a browser draws it for free and
a widget toolkit would need it rewritten. And the standard library's only
GUI is Tk, which fights every attempt to make it dark and loses.

Nothing is installed and nothing leaves the machine: the server binds to
127.0.0.1, serves one page and a handful of JSON endpoints, and stops when
you close the terminal.

**The filter is built by `query.build`, from the same flags the command line
takes.** That is deliberate and it is the only thing here that really
matters: two front ends that each assemble their own WHERE clause will
disagree eventually, and the disagreement will be silent.

    python gui.py              open it in a browser
    python gui.py --port 9000  somewhere else
    python gui.py --check      the GUI and the CLI agree, PASS or FAIL
"""

import json
import shlex
import sqlite3
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import compact
import notes
import players
import query
import sessions
from stats import BY_KEY, STATS

DB = Path(__file__).parent / "hands.db"
PORT = 8765

# Form field -> the command-line flag it stands for. Written this way so the
# page cannot invent a filter the command line does not have, and so adding
# a flag to `query.py` is the only place a filter is ever defined.
SWITCH_FIELDS = {
    "hero": "--hero", "pool": "--pool", "ip": "--ip", "oop": "--oop",
    "pfa": "--pfa", "not_pfa": "--not-pfa",
    "multiway": "--multiway", "headsup": "--headsup",
    "vs_pfa": "--vs-pfa", "standard": "--standard", "allin": "--allin",
    "vs_hero": "--vs-hero", "vs_pool": "--vs-pool",
    "first_in": "--first-in", "last_raise": "--last-raise",
    "first_raise": "--first-raise", "last_action": "--last-action",
    "marked": "--marked", "noted": "--noted",
    "today": "--today",
}
VALUE_FIELDS = {
    "site": "--site", "game": "--game", "game_type": "--game-type",
    "variant": "--variant",
    "player": "--player", "pos": "--pos",
    "street": "--street", "pot": "--pot", "facing": "--facing",
    "vs": "--vs", "opener": "--opener",
    "combo": "--combo", "stake": "--stake", "deep": "--deep",
    "action": "--action", "result": "--result",
    "short": "--short", "board": "--board", "since": "--since",
    "until": "--until", "where": "--where",
    "after": "--after", "then": "--then",
    "quick": "--quick",
    "size": "--size", "outcome": "--outcome",
    "players": "--players", "live": "--live",
    "stack": "--stack",
    "tag": "--tag",
    "session": "--session",
    "hours": "--hours",
    "fmt": "--fmt",
    "last_sessions": "--last-sessions",
    "call_range": "--call-range",
    "hist_group": "--hist-group",
    "start_of_day": "--start-of-day",
    "tz": "--tz",
    "alias": "--alias",
    "vs_alias": "--vs-alias",
    "villain_type": "--villain-type",
    "pre": "--pre", "flop": "--flop", "turn": "--turn",
    "river": "--river", "line": "--line", "node": "--node",
}


def argv_from(params):
    """A form's fields as the argument list `query.build` already understands."""
    argv = []
    preset = (params.get("preset", [""])[0] or "").strip()
    if preset:
        argv += list(query.preset_argv(preset))
    spot = (params.get("spot", [""])[0] or "").strip()
    if spot:
        # A neighbouring spot that is not a named report -- same flags
        # the command line would take, written as one string so the page
        # does not have to grow a control for every flag a report uses
        # (`--facing` is also a chip group; this path is for
        # generated neighbours that are not a named report).
        argv += shlex.split(spot)
    for field, flag in SWITCH_FIELDS.items():
        if params.get(field, [""])[0] in ("1", "true", "on"):
            argv.append(flag)
    for field, flag in VALUE_FIELDS.items():
        v = (params.get(field, [""])[0] or "").strip()
        if v:
            argv += [flag, v]
    # A typed pot-frac range is `--size`, not a second flag -- the
    # letter select and this box are one modifier, and two sizes AND-ed
    # would match nothing and look like a broken builder.
    pot_frac = (params.get("pot_frac", [""])[0] or "").strip()
    if pot_frac:
        argv = [a for i, a in enumerate(argv)
                if a != "--size" and (i == 0 or argv[i - 1] != "--size")]
        argv += ["--size", pot_frac]
    # Cohort first so parse_cohort sees `--site` / `--class` after it
    # as the player filter, the same order the command line uses.
    cohort = (params.get("cohort", [""])[0] or "").strip()
    klass = (params.get("cohort_class", [""])[0] or "").strip()
    extra = []
    if cohort:
        extra += ["--cohort", cohort]
    elif klass:
        extra.append("--cohort")
    if klass and klass not in ("any",):
        extra += ["--class", klass]
    return extra + argv


def payload(con, params):
    """Whatever the page asked for, as plain data."""
    view = params.get("view", ["study"])[0]
    argv = argv_from(params)
    raw_crumbs = (params.get("crumbs", [""])[0] or "").strip()
    crumbs = []
    if raw_crumbs:
        try:
            crumbs = json.loads(raw_crumbs)
        except ValueError:
            crumbs = []
    if crumbs:
        argv = query.drill_stack(argv, crumbs)
    if view == "statistics":
        # Click-stat / Call Range / cell combo are the drill. Left
        # in, the grid would be the 3bet hands and every rate a lie.
        argv, _, _, _ = query.take_stat_drill(argv)
    spec, argv = players.parse_cohort(argv)
    if view == "profiles":
        return {"profiles": [
            {"id": p["id"], "name": p["name"]} for p in query.profiles()]}
    if view == "stat-reports":
        return {"reports": query.list_stat_reports(),
                "recent": query.recent_stat_reports()}
    if view == "save-stat-report":
        name = (params.get("name", [""])[0] or "").strip()
        exclude = params.get("exclude_reg_vs_fish", [""])[0] in (
            "1", "true", "on")
        profile_id = (params.get("profile", [""])[0] or "").strip() or None
        try:
            saved, n, described = query.save_stat_report(
                name, argv, cohort_spec=spec, exclude=exclude,
                profile_id=profile_id)
        except ValueError as e:
            return {"error": str(e)}
        return {"ok": True, "name": saved["name"], "n": n,
                "described": described, "payload": saved,
                "recent": query.recent_stat_reports()}
    if view == "open-stat-report":
        name = (params.get("name", [""])[0] or "").strip()
        try:
            saved = query.open_stat_report(name)
        except ValueError as e:
            return {"error": str(e)}
        ctx = query.hydrate_stat_report(saved)
        return {"ok": True, "payload": saved, "hydrate": {
            "argv": ctx["argv"],
            "subject_argv": ctx["subject_argv"],
            "cohort": players.describe_cohort(ctx["cohort_spec"])
            if ctx["cohort_spec"] else "",
            "cohort_predicate": ctx["cohort_predicate"],
            "fmt": ctx["fmt"],
            "date_range": ctx["date_range"],
            "last_n_sessions": ctx["last_n_sessions"],
            "exclude_reg_vs_fish": ctx["exclude_reg_vs_fish"],
            "profile_id": ctx["profile_id"],
            "warnings": ctx["warnings"],
        }, "recent": query.recent_stat_reports()}
    where, label, parts = query.build(argv)
    notes.attach(con)
    sessions.ensure(con)
    if spec is not None:
        where, _header, label = query.apply_cohort(con, spec, where, label)

    def nothing():
        """Why this filter is empty, so the page never just goes blank."""
        return query.why_empty(con, parts)
    if view == "sessions":
        clock = query.clock_from_argv(argv)
        kind = "today" if "--today" in argv else None
        hours = None
        if "--hours" in argv:
            i = argv.index("--hours")
            if i + 1 < len(argv):
                hours = argv[i + 1]
        since = until = None
        if "--since" in argv:
            i = argv.index("--since")
            if i + 1 < len(argv):
                since = argv[i + 1]
        if "--until" in argv:
            i = argv.index("--until")
            if i + 1 < len(argv):
                until = argv[i + 1]
        # The Sessions date bar uses the clock. Raw --since on this
        # view is a range through start-of-day, not the literal
        # played_at comparison Reports keeps.
        if since or until:
            kind = kind or "range"
        out = sessions.list_of(con, clock=clock, kind=kind,
                               since=since, until=until, hours=hours)
        sid = (params.get("session_id", [""])[0] or "").strip()
        if not sid:
            for i, a in enumerate(argv):
                if a == "--session" and i + 1 < len(argv):
                    sid = argv[i + 1]
                    break
        if sid:
            out["detail"] = sessions.detail_of(con, sid, clock=clock)
        out["label"] = label
        return out

    if view == "statistics":
        exclude = params.get("exclude_reg_vs_fish", [""])[0] in (
            "1", "true", "on")
        profile_id = (params.get("profile", [""])[0] or "").strip() or None
        out = query.statistics_of(con, where, argv, exclude=exclude,
                                  profile_id=profile_id)
        out["label"] = label
        out["why"] = None if out.get("n") else nothing()
        if spec is not None:
            try:
                old_rf = con.row_factory
                con.row_factory = sqlite3.Row
                try:
                    out["cohort"] = {
                        "describe": players.describe_cohort(spec),
                        **players.cohort_summary(players.cohort(con, *spec)),
                    }
                finally:
                    con.row_factory = old_rf
            except ValueError:
                out["cohort"] = {"describe": players.describe_cohort(spec)}
        key = (params.get("stat_key", [""])[0] or "").strip()
        kind = "action"
        call = (params.get("call_range", [""])[0] or "").strip()
        if call:
            key, kind = call, "call"
        combo = (params.get("combo", [""])[0] or "").strip() or None
        hist_group = (params.get("hist_group", [""])[0] or "").strip() or None
        if key:
            try:
                drill = query.stat_range_of(
                    con, where, key, kind=kind, exclude=exclude,
                    combo=combo, hist_group=hist_group)
                compact.attach(con, drill.get("hands") or [], fmt="html")
                notes.decorate(con, drill.get("hands") or [])
                out["drill"] = drill
                out["open_reports"] = query.reports_argv(
                    argv, key, kind, combo, hist_group)
            except SystemExit as e:
                out["drill_error"] = str(e)
        return out

    if view == "study":
        extra = [x.strip() for x in
                 (params.get("panes", [""])[0] or "").split(",") if x.strip()]
        panes = list(query.DEFAULT_STUDY_PANES) + [
            p for p in extra if p in query.PLUS_STUDY_PANES]
        out = query.study_of(con, where, argv, panes=panes, pin=(
            params.get("pin", [""])[0] or "").strip(), crumbs=crumbs)
        compact.attach(con, out.get("hands") or [], fmt="html")
        out["label"] = label
        out["why"] = None if (out.get("hands") or out.get("panes")) else nothing()
        out["crumbs"] = crumbs
        if out.get("graph") and out["graph"].get("n", 0) >= 2:
            out["graph_markup"] = query.graph_markup(
                out["graph"], label, dark=True)
        return out

    if view == "stats":
        n_dec, rows = query.stats_of(con, where)
        out = {"label": label, "decisions": n_dec, "rows": rows,
               "actions": query.actions_of(con, where),
               "summary": query.spot_summary(con, where, argv),
               "profit": query.action_profit_of(con, where),
               "call_profit": query.call_profit_of(con, where),
                "faced": query.chain_report(con, where, argv, False),
                "next": query.chain_report(con, where, argv, True),
               "outcomes": query.outcomes_of(con, where),
               "sizes": query.bet_sizes_of(con, where, argv),
               "related": query.related_spots(argv),
               "why": None if n_dec else nothing()}
        if query.pack_wants_won(argv):
            out["amount_won"] = query.amount_won_of(con, where)
        if spec is not None:
            g = query.chart_of(con, where)
            top = []
            if g["seen"]:
                ranked = sorted(g["cells"].items(),
                                key=lambda kv: -kv[1][0])[:8]
                top = [{"combo": c, "pct": 100.0 * n / g["seen"]}
                       for c, (n, _k) in ranked]
            out["cohort_range"] = {
                "coverage": g.get("coverage"), "seen": g["seen"],
                "total": g["total"], "top": top}
        pin = (params.get("pin", [""])[0] or "").strip()
        if pin:
            try:
                argv_a, argv_b = query.pin_sides(argv, pin)
                out["compare"] = query.compare_of(
                    con, argv_a, argv_b, None, pin, packs=True)
            except SystemExit as e:
                out["pinned"] = {"name": pin, "error": str(e)}
        return out

    if view == "report":
        dim = params.get("by", ["position"])[0]
        if dim not in query.DIMENSIONS:
            return {"error": f"unknown dimension {dim}"}
        cols = [c for c in (params.get("show", [""])[0] or "").split(",") if c]
        cols = [c for c in cols if c in BY_KEY] or query.columns_for(argv)
        pin = (params.get("pin", [""])[0] or "").strip()
        out = {"label": label, "dim": dim,
               "related": query.related_spots(argv)}
        if pin:
            try:
                argv_a, argv_b = query.pin_sides(argv, pin)
                out["compare"] = query.compare_of(
                    con, argv_a, argv_b, None, pin,
                    dim=dim, columns=cols, bet_sizes=(dim == "size"))
            except SystemExit as e:
                out["pinned"] = {"name": pin, "error": str(e)}
        if dim == "size":
            sizes = query.bet_sizes_of(con, where, argv)
            out["sizes"] = sizes
            out["columns"] = []
            out["won_by"] = {}
            out["why"] = None if sizes["rows"] or out.get("compare") else nothing()
            out["rows"] = [{"key": r["key"], "n": r["hits"],
                            "cells": []} for r in sizes["rows"]]
            return out
        got = query.report_of(con, where, dim, cols, argv)
        won_by = got["won_by"]
        out.update({
            "columns": [{"key": c, "label": BY_KEY[c].label} for c in cols],
            "won_by": {str(k): won_by[k] for k in won_by},
            "why": None if got["keys"] or out.get("compare") else nothing(),
            "rows": [{
                "key": str(k), "n": got["counts"].get(k, 0),
                "won": won_by.get(k),
                "cells": [
                    None if not got["grid"][c].get(k, (0, 0))[0] else {
                        "pct": 100 * got["grid"][c][k][1] / got["grid"][c][k][0],
                        "n": got["grid"][c][k][0]}
                    for c in cols]} for k in got["keys"]]})
        return out

    if view == "results":
        dim = params.get("by", [""])[0]
        if dim and dim in query.DIMENSIONS:
            expr, order = query.DIMENSIONS[dim]
            values = [r[0] for r in con.execute(
                f"SELECT DISTINCT {expr} FROM decisions WHERE ({where}) "
                f"AND ({expr}) IS NOT NULL")]
            out = []
            for v in sorted(values, key=order):
                lit = query.q(v) if isinstance(v, str) else str(v)
                got = query.results_of(con, query.matching_seats(
                    con, f"({where}) AND ({expr}) = {lit}"))
                if got:
                    out.append(dict(got, key=str(v)))
            return {"label": label, "dim": dim, "rows": out,
                    "why": None if out else nothing()}
        got = query.results_of(con, query.matching_seats(con, where))
        return {"label": label, "totals": got,
                "why": None if got else nothing()}

    if view == "hands":
        rows = query.matching_hands(con, where, limit=300)
        notes.decorate(con, rows)
        compact.attach(con, rows, fmt="html")
        return {"label": label, "rows": rows,
                "why": None if rows else nothing()}

    if view == "range":
        out = query.range_of(con, where)
        out["label"] = label
        out["why"] = None if out["n"] else nothing()
        return out

    if view == "chart":
        out = query.chart_of(con, where)
        out["label"] = label
        if out.get("gated"):
            out["why"] = out.get("reason")
        else:
            out["why"] = None if out["total"] else nothing()
        return out

    if view == "export-session":
        sid = (params.get("session_id", [""])[0]
               or params.get("id", [""])[0] or "").strip()
        dest = (params.get("out", [""])[0] or "").strip()
        if not sid or not dest:
            return {"error": "export needs a session id and a path"}
        session = sessions.of(con, sid)
        if session is None:
            return {"error": f"no session {sid!r}"}
        return sessions.export_hands(con, session, dest)

    if view == "note":
        hid = (params.get("id", [""])[0] or "").strip()
        text = (params.get("text", [""])[0] or "").strip()
        if not hid or not text:
            return {"error": "note needs a hand id and some text"}
        seat = params.get("seat", [""])[0]
        notes.add(text, hand_id=hid,
                  seat=int(seat) if seat.isdigit() else None,
                  hands_con=con)
        return {"ok": True, "id": hid}

    if view == "mark":
        hid = (params.get("id", [""])[0] or "").strip()
        if not hid:
            return {"error": "mark needs a hand id"}
        raw = (params.get("tag", [""])[0] or "").strip()
        tags = [x.strip() for x in raw.split(",") if x.strip()]
        if params.get("unmark", [""])[0] in ("1", "true", "on"):
            notes.unmark(hid, tags or None)
            return {"ok": True, "marked": False, "id": hid}
        notes.mark(hid, tags)
        return {"ok": True, "marked": True, "id": hid}

    if view == "hand":
        hid = params.get("id", [""])[0]
        seat = params.get("seat", [""])[0]
        d = query.hand_detail(
            con, hid, int(seat) if seat.isdigit() else None)
        if d:
            compact.decorate_detail(d, fmt="html")
        return {"hand": d}

    if view == "graph":
        got = query.graph_of(con, where)
        if got.get("why") or got.get("n", 0) < 2:
            return {"label": label, "svg": None, "graph": got,
                    "why": got.get("why") or nothing()}
        return {"label": label, "graph": got,
                "svg": query.graph_markup(got, label, dark=True)}

    return {"error": f"unknown view {view}"}


def options(con):
    """What this particular database actually contains, for the dropdowns."""
    one = lambda sql: [r[0] for r in con.execute(sql) if r[0] is not None]
    return {
        "sites": one("SELECT DISTINCT site FROM decisions ORDER BY 1"),
        "stakes": one("SELECT DISTINCT bb FROM decisions ORDER BY 1"),
        "players": one(
            "SELECT player FROM decisions WHERE player IS NOT NULL "
            "GROUP BY player HAVING COUNT(DISTINCT hand_id) >= 100 "
            "ORDER BY COUNT(DISTINCT hand_id) DESC LIMIT 200"),
        "boards": list(query.BOARDS),
        "dimensions": list(query.DIMENSIONS),
        "stats": [{"key": s.key, "label": s.label, "group": s.group,
                   "note": s.note} for s in STATS],
        "defaults": query.DEFAULT_COLUMNS,
        "reports": [{"name": n, "family": fam}
                    for fam, names in query.reports_by_family()
                    for n in names],
        "profiles": [{"id": p["id"], "name": p["name"]}
                     for p in query.profiles()],
        "stat_reports": query.list_stat_reports(),
    }


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>poker_analysis</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#14161a; --panel:#1b1e24; --edge:#2a2f38; --ink:#d8dbe0;
  --dim:#8b929c; --accent:#4c9aff; --good:#22a35a; --bad:#d1443c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.5 ui-sans-serif,system-ui,'Segoe UI',sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--edge);
  display:flex;align-items:baseline;gap:14px}
header h1{margin:0;font-size:14px;font-weight:600;letter-spacing:.02em}
header .sub{color:var(--dim);font-size:12px}
main{display:flex;align-items:flex-start;min-height:calc(100vh - 47px)}
aside{width:260px;flex:none;padding:14px;border-right:1px solid var(--edge);
  position:sticky;top:0;max-height:100vh;overflow:auto}
section{flex:1;padding:18px 22px;min-width:0;overflow-x:auto}
fieldset{border:none;padding:0;margin:0 0 14px}
legend{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:6px}
label{display:block;margin:0 0 6px}
select,input{width:100%;background:#0f1115;color:var(--ink);
  border:1px solid var(--edge);border-radius:5px;padding:5px 7px;font:inherit}
select[multiple]{height:88px}
input:focus,select:focus{outline:1px solid var(--accent);border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{border:1px solid var(--edge);border-radius:20px;padding:3px 10px;
  cursor:pointer;color:var(--dim);user-select:none;font-size:12px}
button.mark{background:transparent;border:0;color:var(--accent);cursor:pointer;
  font-size:16px;padding:0 4px;line-height:1}
.chip.on{background:var(--accent);border-color:var(--accent);color:#08111f;
  font-weight:600}
nav{display:flex;gap:4px;margin-bottom:14px;flex-wrap:wrap}
nav button{background:none;border:1px solid var(--edge);color:var(--dim);
  padding:5px 13px;border-radius:6px;cursor:pointer;font:inherit}
nav button.on{background:var(--panel);color:var(--ink);border-color:var(--accent)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:5px 9px;border-bottom:1px solid var(--edge);
  white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
  letter-spacing:.05em}
tbody tr:hover{background:var(--panel)}
tr.click{cursor:pointer}
.compact{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12px;letter-spacing:0;text-align:left;white-space:pre-wrap}
.compact u{text-underline-offset:2px}
td.compact{text-align:left;font-weight:500;white-space:pre-wrap;max-width:52em}
.compact .hole,.holes{letter-spacing:0}
.used{font-weight:700;text-decoration:underline;text-underline-offset:2px}
.muck{color:var(--dim)}
.link{color:var(--accent);cursor:pointer}
.thin{color:var(--dim)}
.thin::after{content:' ?';color:#b8892a}
.n{color:var(--dim);font-size:11px}
.drill{cursor:pointer}
.thin{color:#b8892a}
.pos{color:var(--good)} .neg{color:var(--bad)}
.filter{color:var(--dim);margin:0 0 14px;font-size:12px}
.group{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;padding-top:12px}
.empty{color:var(--dim);padding:26px 0}
.bar{height:5px;background:var(--accent);border-radius:3px;opacity:.5}
.hist{margin:8px 0 12px;padding:8px;border:1px solid var(--edge);border-radius:6px;
  background:var(--panel)}
.hist .weakpct{font-weight:600;margin:0 0 6px}
.histbars{display:flex;align-items:flex-end;gap:4px;height:110px}
.histbar{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;
  justify-content:flex-end;cursor:pointer;height:100%}
.histbar i{display:block;width:100%;border-radius:3px 3px 0 0}
.histbar.weak i{background:var(--bad)}
.histbar.strong i{background:var(--accent);opacity:.75}
.histbar.other i{background:var(--dim)}
.histbar span{font-size:10px;color:var(--dim);margin-top:4px;text-align:center;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.crumbs,.chips-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;
  margin:0 0 8px;font-size:12px}
.crumbs a,.chip-x{color:var(--accent);cursor:pointer;text-decoration:none}
.smart{color:var(--dim);margin:0 0 10px;font-size:12px}
.smart a{color:var(--accent);margin-left:8px;cursor:pointer}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 12px}
.pane{border:1px solid var(--edge);border-radius:6px;padding:8px 8px 4px;
  min-width:0;background:var(--panel)}
.pane h3{margin:0 0 6px;font-size:12px;font-weight:600;display:flex;
  justify-content:space-between;align-items:center}
.pane h3 button{background:none;border:1px solid var(--edge);color:var(--dim);
  border-radius:4px;cursor:pointer;font:inherit;padding:1px 6px}
.pane table{font-size:12px}
.ctx{position:fixed;background:var(--panel);border:1px solid var(--edge);
  border-radius:6px;padding:4px 0;z-index:20;min-width:140px}
.ctx button{display:block;width:100%;text-align:left;background:none;border:0;
  color:var(--ink);padding:5px 12px;cursor:pointer;font:inherit}
.ctx button:hover{background:var(--edge)}
.statgrid{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}
.statcol{min-width:140px}
.statcell{display:block;background:var(--panel);border:1px solid var(--edge);
  border-radius:6px;padding:6px 8px;margin:3px 0;cursor:pointer;text-align:left;
  color:var(--ink);font:inherit;width:100%}
.statcell.on{background:var(--accent);color:#14161a}
.statcell:hover{border-color:var(--accent)}
.chartcell{display:inline-block;width:36px;height:36px;margin:1px;
  font-size:9px;text-align:center;line-height:11px;vertical-align:top;
  cursor:pointer;border:0;color:var(--ink)}
body.detached aside, body.detached nav, body.detached header{display:none}
body.detached main{display:block;min-height:100vh}
body.detached section{padding:12px 14px}
body.detached .panes{grid-template-columns:1fr}
body.detached .filter, body.detached #related{display:none}
.wingraph{margin:0 0 12px}
.wg-bar{margin:0 0 6px}
.wg-unit{margin-right:6px;background:var(--panel);border:1px solid var(--edge);
  color:var(--dim);border-radius:4px;cursor:pointer;font:inherit;padding:2px 8px}
.wg-unit.on{color:var(--ink);border-color:var(--accent);font-weight:700}
.wg-tip{min-height:1.2em;margin:0 0 6px}
</style>
<header>
  <h1>poker_analysis</h1>
  <span class="sub" id="sub">loading…</span>
</header>
<main>
<aside>
  <fieldset><legend>smart reports</legend>
    <label><select id="preset"><option value="">no report</option></select></label>
    <label>pin / compare
      <select id="pin"><option value="">none</option></select></label>
  </fieldset>
  <fieldset><legend>who</legend>
    <div class="chips" id="who">
      <span class="chip" data-f="hero">hero</span>
      <span class="chip" data-f="pool">pool</span>
    </div>
  </fieldset>
  <fieldset><legend>study</legend>
    <div class="chips">
      <span class="chip" data-f="marked">marked</span>
      <span class="chip" data-f="noted">has a note</span>
    </div>
    <label>tag <input id="tag" placeholder="leak,bluff"></label>
  </fieldset>
  <fieldset><legend>multiple players</legend>
    <label>cohort
      <input id="cohort" placeholder="vpip>=40,pfr<=10,hands>=100 or Value(3Bet)<2 and Opps(3Bet)>100"></label>
    <label>class
      <select id="cohort_class">
        <option value="">any class</option>
        <option value="fish">fish</option>
        <option value="reg">reg</option>
        <option value="unknown">unknown</option>
      </select></label>
  </fieldset>
  <fieldset><legend>site &amp; stake</legend>
    <label><select id="site"><option value="">any site</option></select></label>
    <label><select id="game">
      <option value="">holdem (default)</option>
      <option value="plo">plo</option>
      <option value="plo5">plo5</option>
      <option value="all">all games</option>
    </select></label>
    <label><select id="game_type">
      <option value="">any type</option>
      <option value="nlhe-cash">NLHE Cash</option>
      <option value="plo4-cash">PLO4 Cash</option>
      <option value="plo5-cash">PLO5 Cash</option>
    </select></label>
    <label><select id="stake"><option value="">any stake</option></select></label>
    <label><select id="player"><option value="">any player</option></select></label>
    <label>alias
      <input id="alias" placeholder="me (single-person merge)"></label>
    <label>vs alias
      <input id="vs_alias" placeholder="nits (group as pool)"></label>
    <label>villain type
      <select id="villain_type">
        <option value="">any</option>
        <option value="fish">fish</option>
        <option value="reg">reg</option>
        <option value="unknown">unknown</option>
      </select></label>
  </fieldset>
  <fieldset><legend>my position</legend>
    <div class="chips" id="pos"></div>
  </fieldset>
  <fieldset><legend>against  (heads-up pots only)</legend>
    <div class="chips" id="vs"></div>
    <div class="chips" id="vsside">
      <span class="chip" data-f="vs_hero">vs me</span>
      <span class="chip" data-f="vs_pool">vs the pool</span>
    </div>
  </fieldset>
  <fieldset><legend>street</legend>
    <div class="chips" id="street"></div>
  </fieldset>
  <fieldset><legend>pot type</legend>
    <div class="chips" id="pot"></div>
  </fieldset>
  <fieldset><legend>facing</legend>
    <div class="chips" id="facing"></div>
  </fieldset>
  <fieldset><legend>situation</legend>
    <div class="chips" id="sit">
      <span class="chip" data-f="ip">in position</span>
      <span class="chip" data-f="oop">out of position</span>
      <span class="chip" data-f="pfa">was the raiser</span>
      <span class="chip" data-f="not_pfa">was not the raiser</span>
      <span class="chip" data-f="vs_pfa">facing the raiser</span>
      <span class="chip" data-f="first_in">first in</span>
      <span class="chip" data-f="first_raise">first raise</span>
      <span class="chip" data-f="last_raise">last raise</span>
      <span class="chip" data-f="last_action">last action</span>
      <span class="chip" data-f="multiway">multiway</span>
      <span class="chip" data-f="headsup">heads up</span>
    </div>
  </fieldset>
  <fieldset><legend>flop texture</legend>
    <div class="chips" id="board"></div>
  </fieldset>
  <fieldset><legend>stack depth (bb)</legend>
    <label>at least <input id="deep" type="number" min="0" step="10"></label>
    <label>less than <input id="short" type="number" min="0" step="10"></label>
  </fieldset>
  <fieldset><legend>dates</legend>
    <div class="chips">
      <span class="chip" data-f="today">Today</span>
    </div>
    <label>last N hours <input id="hours" placeholder="4"></label>
    <label>from <input id="since" type="date"></label>
    <label>to <input id="until" type="date"></label>
    <label>start-of-day hour <input id="start_of_day" placeholder="0"></label>
    <label>room tz <input id="tz" placeholder="acr=-5,ignition=0"></label>
    <label>session id <input id="session" placeholder="first hand id"></label>
    <p class="n">Today / hours use start-of-day and each room's HH
      timezone offset. An empty Today is usually the hour or the
      offset, not missing hands. Raw from/to stay played_at.</p>
  </fieldset>
  <fieldset><legend>statistics</legend>
    <label>Cash or MTT
      <select id="fmt"><option value="">any</option>
        <option value="cash">cash</option>
        <option value="mtt">mtt</option>
      </select></label>
    <label>last N sessions <input id="last_sessions" placeholder="10"></label>
    <label><input id="exclude_reg_vs_fish" type="checkbox">
      exclude reg-vs-fish (Statistics only)</label>
    <label>profile
      <select id="profile">
        <option value="default">Default</option>
        <option value="preflop">Preflop</option>
        <option value="postflop">Postflop</option>
        <option value="showdown">Showdown</option>
      </select></label>
    <label>save report as
      <input id="stat_report_name" placeholder="regs last 10"></label>
    <p class="n"><a class="link" id="save_stat_report">Save Report</a>
      · <a class="link" id="open_stat_report">Open Report</a>
      · recent <select id="recent_stat_reports"></select></p>
    <label>click-stat
      <input id="stat_key" placeholder="threebet"></label>
    <label>Call Range <input id="call_range" placeholder="threebet"></label>
    <input type="hidden" id="quick">
    <input type="hidden" id="hist_group">
    <p class="n">The exclude box is a Statistics compute flag.
      Reports and Sessions ignore it. villain_type stays a Reports
      filter. Last N is cash sit-downs.</p>
  </fieldset>
  <fieldset><legend>faced next / next actions</legend>
    <label>the other seat then
      <select id="after"><option value="">any</option></select></label>
    <label>this player then
      <select id="then"><option value="">any</option></select></label>
  </fieldset>
  <fieldset><legend>custom action</legend>
    <label>outcome of this bet
      <select id="outcome"><option value="">any</option>
        <option value="fold-out">all villains fold</option>
        <option value="call">one villain call</option>
        <option value="raise-back">villain raise</option>
      </select></label>
    <label>bet size
      <select id="size"><option value="">any</option>
        <option value="s">small</option>
        <option value="m">medium</option>
        <option value="l">large</option>
        <option value="p">pot+</option>
        <option value="o">overbet</option>
        <option value="0.4-0.75">0.4–0.75 pot</option>
        <option value="50%+">50%+</option>
      </select></label>
    <label>or pot-frac range <input id="pot_frac" placeholder="0.4-0.75"></label>
    <label>this action
      <select id="action"><option value="">any</option>
        <option value="fold">fold</option>
        <option value="check">check</option>
        <option value="call">call</option>
        <option value="bet">bet</option>
        <option value="raise">raise</option>
      </select></label>
    <label>whole-hand result
      <select id="result"><option value="">any</option>
        <option value="won">won</option>
        <option value="lost">lost</option>
        <option value="showdown">showdown</option>
        <option value="no-showdown">no showdown</option>
      </select></label>
    <label>combo / family
      <input id="combo" placeholder="AKs  or  Axs, 22+"></label>
    <label>stack bb <input id="stack" placeholder="100+  or  80-200"></label>
    <label>players at the table <input id="players" type="number" min="2" max="10"></label>
    <label>still in the pot <input id="live" type="number" min="2" max="10"></label>
  </fieldset>
  <fieldset><legend>action line</legend>
    <label>preflop <input id="pre" placeholder="*R*R*"></label>
    <label>flop <input id="flop" placeholder="XBmC"></label>
    <label>turn <input id="turn" placeholder="XX"></label>
    <label>river <input id="river" placeholder="*Bo*"></label>
    <label>line <input id="line" placeholder="*R*/XBC"></label>
    <label>node <input id="node" placeholder="*/XB"></label>
  </fieldset>
  <fieldset><legend>raw sql over decisions</legend>
    <label><input id="where" placeholder="eff_bb > 150 AND fl_paired=1"></label>
  </fieldset>
</aside>
<section>
  <nav id="tabs">
    <button data-v="sessions">sessions</button>
    <button data-v="study" class="on">study</button>
    <button data-v="statistics">statistics</button>
    <button data-v="stats">stats</button>
    <button data-v="range">range</button>
    <button data-v="chart">chart</button>
    <button data-v="report">report</button>
    <button data-v="results">results</button>
    <button data-v="graph">graph</button>
    <button data-v="hands">hands</button>
  </nav>
  <div id="byrow" style="display:none;margin-bottom:14px">
    <select id="by" style="width:auto;min-width:150px"></select>
  </div>
  <p class="filter" id="filter"></p>
  <p class="filter" id="related"></p>
  <div id="out"><p class="empty">…</p></div>
</section>
</main>
<script>
const $ = s => document.querySelector(s);
const state = {view:'study', by:'position', flags:{}, multi:{}, preset:'',
               spot:'', crumbs:[], extraPanes:[], paneHidden:{}, paneSort:{},
               pinAlias:{}, sessionId:'', detached:[]};
const DETACH_PANE = new URLSearchParams(location.search).get('detach') || '';
const DETACH_CH = (typeof BroadcastChannel !== 'undefined')
  ? new BroadcastChannel('h2n-study') : null;
let OPT = {};

const POSITIONS = ['UTG','HJ','CO','BTN','SB','BB'];
const STREETS   = ['preflop','flop','turn','river'];
const POTS      = ['unopened','limped','raised','3bet','4bet'];
const FACINGS   = ['unopened','open','3bet','4bet','5bet+','check','bet','raise'];

function chips(host, items, group){
  $('#'+host).innerHTML = items.map(v =>
    `<span class="chip" data-g="${group}" data-v="${v}">${v}</span>`).join('');
}
function paintChips(){
  document.querySelectorAll('.chip').forEach(c => {
    if (c.dataset.f) c.classList.toggle('on', !!state.flags[c.dataset.f]);
    else c.classList.toggle('on',
      (state.multi[c.dataset.g]||[]).includes(c.dataset.v));
  });
}
document.addEventListener('click', e => {
  const c = e.target.closest('.chip');
  if (!c) return;
  if (c.dataset.f){
    // hero and pool are opposites, as are in and out of position; turning
    // one on has to turn its twin off or the filter selects nothing.
    const twins = {hero:'pool', pool:'hero', ip:'oop', oop:'ip',
                   pfa:'not_pfa', not_pfa:'pfa',
                   multiway:'headsup', headsup:'multiway',
                   vs_hero:'vs_pool', vs_pool:'vs_hero'};
    state.flags[c.dataset.f] = !state.flags[c.dataset.f];
    if (state.flags[c.dataset.f] && twins[c.dataset.f])
      state.flags[twins[c.dataset.f]] = false;
  } else {
    const g = c.dataset.g, v = c.dataset.v;
    const cur = state.multi[g] || [];
    state.multi[g] = cur.includes(v) ? cur.filter(x=>x!==v) : cur.concat([v]);
  }
  paintChips(); load();
});
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  state.view = b.dataset.v;
  document.querySelectorAll('#tabs button').forEach(x =>
    x.classList.toggle('on', x === b));
  $('#byrow').style.display =
    (state.view === 'report' || state.view === 'results') ? 'block' : 'none';
  load();
});
['site','game','game_type','stake','player','deep','short','since','until','where','by','preset','pin','after','then','size','outcome','players','live','stack','pot_frac','pre','flop','turn','river','line','node','cohort','cohort_class','tag','alias','vs_alias','villain_type','combo','action','result','hours','start_of_day','tz','session','fmt','last_sessions','call_range','stat_key','profile']
  .forEach(id => $('#'+id).addEventListener('change', () => {
    if (id === 'by') state.by = $('#by').value;
    if (id === 'preset'){
      state.preset = $('#preset').value;
      state.spot = '';
      // Opening a report replaces leftover situation chips.
      ['ip','oop','pfa','not_pfa','vs_pfa','multiway','headsup','allin','first_in','last_raise','first_raise','last_action'].forEach(f => {
        state.flags[f] = false;
      });
      ['pos','vs','street','pot','facing','board'].forEach(g => { state.multi[g] = []; });
      ['after','then','size','outcome','players','live','stack','pot_frac','pre','flop','turn','river','line','node','combo','action','result'].forEach(fid => {
        const el = $('#'+fid); if (el) el.value = '';
      });
      paintChips();
      state.crumbs = [];
    }
    load();
  }));
['where','pot_frac','stack','pre','flop','turn','river','line','node','cohort','combo']
  .forEach(id => $('#'+id).addEventListener('keydown', e => {
    if (e.key === 'Enter') load();
  }));

function params(){
  const p = new URLSearchParams();
  p.set('view', state.view);
  if (state.view === 'report' || state.view === 'results') p.set('by', state.by);
  if (state.preset) p.set('preset', state.preset);
  if (state.spot) p.set('spot', state.spot);
  if (state.crumbs && state.crumbs.length)
    p.set('crumbs', JSON.stringify(state.crumbs));
  if (state.extraPanes && state.extraPanes.length)
    p.set('panes', state.extraPanes.join(','));
  for (const [k,v] of Object.entries(state.flags)) if (v) p.set(k,'1');
  for (const [g,vs] of Object.entries(state.multi))
    if (vs.length) p.set(g, vs.join(','));
  for (const id of ['site','game','game_type','stake','player','deep','short','since','until','where','after','then','size','outcome','players','live','stack','pot_frac','pre','flop','turn','river','line','node','pin','cohort','cohort_class','tag','alias','vs_alias','villain_type','combo','action','result','hours','start_of_day','tz','session','fmt','last_sessions','call_range','quick','hist_group']){
    const v = $('#'+id).value.trim();
    if (v) p.set(id, v);
  }
  if (state.sessionId) p.set('session_id', state.sessionId);
  const ex = $('#exclude_reg_vs_fish');
  if (ex && ex.checked) p.set('exclude_reg_vs_fish', '1');
  const sk = $('#stat_key');
  if (sk && sk.value.trim()) p.set('stat_key', sk.value.trim());
  const pr = $('#profile');
  if (pr && pr.value.trim()) p.set('profile', pr.value.trim());
  return p;
}
const money = v => `<span class="${v>=0?'pos':'neg'}">${v>=0?'+':''}${
  v.toLocaleString(undefined,{maximumFractionDigits:1})}</span>`;

function paintRelated(items){
  const host = $('#related');
  if (!items || !items.length){ host.innerHTML = ''; return; }
  host.innerHTML = 'related: ' + items.map((s,i) =>
    `<a href="#" data-i="${i}" style="color:var(--accent);margin-right:10px">${s.name}</a>`
  ).join('');
  host.querySelectorAll('a').forEach(a => {
    a.onclick = e => {
      e.preventDefault();
      const s = items[+a.dataset.i];
      openSpot(s);
    };
  });
}
function openSpot(s){
  // A report replaces the situation and keeps who, the same way the
  // window's report box does -- leftover street chips AND-ed onto a
  // flop report are how those reports used to open empty.
  const keep = {hero: state.flags.hero, pool: state.flags.pool,
                vs_hero: state.flags.vs_hero, vs_pool: state.flags.vs_pool};
  state.flags = keep;
  state.multi = {};
  state.spot = '';
  state.preset = s.preset || '';
  if (!s.preset && s.argv && s.argv.length)
    state.spot = s.argv.map(x => /\\s/.test(x) ? JSON.stringify(x) : x).join(' ');
  const box = $('#preset');
  if (box) box.value = state.preset;
  paintChips();
  load();
}

function htmlCompareSizes(cmp){
  const A = ((cmp.sizes||{}).a||{}).rows || [];
  const B = ((cmp.sizes||{}).b||{}).rows || [];
  const byA = Object.fromEntries(A.map(r => [r.key, r]));
  const byB = Object.fromEntries(B.map(r => [r.key, r]));
  const keys = ['s','m','l','p','o'].filter(k => byA[k] || byB[k]);
  if (!keys.length) return '';
  const cell = r => {
    if (!r || !r.opps) return '–';
    const ap = r.profit || {};
    const act = ap.bb_per_hand == null ? '–'
      : ((ap.bb_per_hand>=0?'+':'') + ap.bb_per_hand.toFixed(2));
    return `${r.hits.toLocaleString()}/${r.opps.toLocaleString()} ${r.pct.toFixed(1)}% ${act}`;
  };
  let h = `<tr><td colspan="4" class="group">bet sizes</td></tr>`;
  for (const k of keys){
    const ra = byA[k] || {}, name = ra.label || (byB[k]||{}).label || k;
    h += `<tr class="drill" data-flag="size" data-key="${k}">`
      + `<td>${name}</td><td>${cell(byA[k])}</td><td></td>`
      + `<td>${cell(byB[k])}</td></tr>`;
  }
  return h;
}
function htmlComparePacks(cmp){
  const A = (cmp.packs||{}).a || [], B = (cmp.packs||{}).b || [];
  const byA = Object.fromEntries(A.map(r => [r.key, r]));
  const byB = Object.fromEntries(B.map(r => [r.key, r]));
  const keys = [];
  const seen = {};
  for (const r of A.concat(B)){
    if (seen[r.key]) continue;
    seen[r.key] = 1;
    keys.push(r.key);
  }
  if (!keys.length) return '';
  let h = `<tr><td colspan="4" class="group">this vs pinned (every stat)</td></tr>`;
  let g = null;
  const cell = r => r && r.n ? `${r.pct.toFixed(1)}% n=${r.n.toLocaleString()}` : '–';
  for (const k of keys){
    const a = byA[k], b = byB[k], row = a || b;
    if (row.group !== g){
      g = row.group;
      h += `<tr><td colspan="4" class="group">${g}</td></tr>`;
    }
    h += `<tr class="drill" data-flag="quick" data-key="${k}">`
      + `<td>${row.label}</td><td>${cell(a)}</td><td></td>`
      + `<td>${cell(b)}</td></tr>`;
  }
  return h;
}
function htmlReportCompare(d){
  const cmp = d.compare;
  if (cmp.sizes){
    return '<table><tbody>'
      + `<tr><td class="group">this vs pinned</td>`
      + `<td class="group">${(cmp.a&&cmp.a.name)||'this'}</td><td></td>`
      + `<td class="group">${(cmp.b&&cmp.b.name)||'pinned'}</td></tr>`
      + htmlCompareSizes(cmp) + '</tbody></table>'
      + '<p class="n">freq is this size of the parent filter. act bb is Action Profit v1.</p>';
  }
  const blob = cmp.by || {};
  const ga = blob.a || {}, gb = blob.b || {};
  const cols = ga.cols || gb.cols || (d.columns||[]).map(c => c.key);
  const labels = {};
  (d.columns||[]).forEach(c => { labels[c.key] = c.label; });
  const keys = Array.from(new Set([...(ga.keys||[]), ...(gb.keys||[])]));
  let h = `<table><thead><tr><th>${blob.dim||d.dim||'by'}</th>`;
  for (const side of ['this','pin']){
    for (const c of cols) h += `<th>${side} ${labels[c]||c}</th>`;
    h += `<th>${side} n</th>`;
  }
  h += '</tr></thead><tbody>';
  const pct = (grid, c, k) => {
    const pair = (grid[c]||{})[k];
    if (!pair || !pair[0]) return '–';
    return (100*pair[1]/pair[0]).toFixed(1)+'%';
  };
  for (const k of keys){
    h += `<tr><td>${k}</td>`;
    for (const g of [ga, gb]){
      for (const c of cols) h += `<td>${pct(g.grid||{}, c, k)}</td>`;
      h += `<td class="n">${((g.counts||{})[k]||0).toLocaleString()}</td>`;
    }
    h += '</tr>';
  }
  return h + '</tbody></table>';
}

function stepFromRow(r){
  if (r.composed || (r.argv && !r.flag))
    return {label: r.label || r.key, argv: r.argv || [], how: r.how};
  if (!r.flag) return null;
  return {label: r.label || r.value || r.key, flag: r.flag,
          value: String(r.value || r.key), how: r.how};
}
function applyStep(step){
  if (!step) return;
  // A row click in a detached window is the same crumb the main
  // report would take -- live context, one report id. Pin stays
  // in-main; posting the step there is what keeps that true.
  if (DETACH_PANE && window.opener && !window.opener.closed){
    window.opener.postMessage({kind:'drill', step: step}, '*');
    return;
  }
  if (state.crumbs.length >= 3) state.crumbs = state.crumbs.slice(0,2);
  state.crumbs.push(step);
  load();
}
function popCrumb(i){
  state.crumbs = i < 0 ? [] : state.crumbs.slice(0, i+1);
  load();
}
function pinStep(step){
  if (DETACH_PANE && window.opener && !window.opener.closed){
    window.opener.postMessage({kind:'pin', step: step}, '*');
    return;
  }
  const label = 'pin ' + (step.label || step.value || 'row');
  const argv = step.argv || (step.flag ? [step.flag, step.value] : []);
  const box = $('#pin');
  let opt = [...box.options].find(o => o.textContent === label);
  if (!opt){
    opt = document.createElement('option');
    opt.textContent = label;
    box.appendChild(opt);
  }
  opt.value = JSON.stringify(argv);
  box.value = opt.value;
  load();
}
function renderStatistics(d){
  const c = d.counts || {};
  let h = `<p class="n">${(d.n||0).toLocaleString()} decisions`;
  if (d.exclude)
    h += ` · excluded ${(d.excluded||0).toLocaleString()} reg-vs-fish of ${(d.n_all||0).toLocaleString()}`;
  h += ` · ${c.players||0} identities · ${c.reg||0} regs / ${c.fish||0} fish / ${c.unknown||0} unknown`;
  if (d.cohort)
    h += ` · ${d.cohort.describe||'cohort'}`
      + (d.cohort.players
        ? ` (${d.cohort.regs||0} regs / ${d.cohort.fish||0} fish / ${d.cohort.unknown||0} unknown)`
        : '');
  if (d.profile) h += ` · profile ${d.profile.name||d.profile_id||''}`;
  for (const w of (d.warnings||[])) h += ` · warning: ${w}`;
  h += '</p>';
  h += `<p class="n">${d.note||''}</p>`;
  const by = {};
  for (const r of (d.rows||[]))
    (by[r.group||''] = by[r.group||''] || []).push(r);
  h += '<div class="statgrid">';
  for (const [g, items] of Object.entries(by)){
    h += `<div class="statcol"><p class="n">${g}</p>`;
    for (const r of items){
      const pct = r.n ? r.pct.toFixed(1)+'%' : '–';
      const on = ($('#stat_key') && $('#stat_key').value===r.key) ? ' on' : '';
      h += `<button type="button" class="statcell${on}" data-key="${r.key}">`
        + `<b>${r.label}</b><br>${pct}`
        + (r.n ? ` <span class="n">n=${r.n.toLocaleString()}</span>` : '')
        + `</button>`;
    }
    h += '</div>';
  }
  h += '</div>';
  const drill = d.drill;
  if (d.drill_error) h += `<p class="empty">${d.drill_error}</p>`;
  if (drill){
    h += `<p><b>${drill.title||''}</b> `
      + `<button type="button" id="stat_range">Range</button> `
      + `<button type="button" id="stat_call">Call Range</button> `
      + `<button type="button" id="stat_reports">Open in Reports</button></p>`;
    const cells = (drill.chart && drill.chart.cells) || {};
    const seen = (drill.chart && drill.chart.seen) || 0;
    h += '<div>';
    const ranks = 'AKQJT98765432';
    for (let i=0;i<13;i++){
      for (let j=0;j<13;j++){
        const combo = ranks[i]===ranks[j] ? ranks[i]+ranks[j]
          : (i<j ? ranks[i]+ranks[j]+'s' : ranks[j]+ranks[i]+'o');
        const n = (cells[combo]||[0])[0];
        const share = seen ? (100*n/seen) : 0;
        h += `<button type="button" class="chartcell" data-combo="${combo}" `
          + `style="background:rgba(76,154,255,${Math.min(1, share/8)})">`
          + `${combo}<br>${n?share.toFixed(1):''}</button>`;
      }
      h += '<br>';
    }
    h += '</div>';
    h += renderHist(drill.hist, {click: 'stat'});
    h += renderStudyHands(drill.hands||[]);
  }
  $('#out').innerHTML = h;
  $('#out').querySelectorAll('.statcell').forEach(b => {
    b.onclick = () => { $('#stat_key').value = b.dataset.key;
      const cr = $('#call_range'); if (cr) cr.value = '';
      const hg = $('#hist_group'); if (hg) hg.value = '';
      load(); };
  });
  const rangeBtn = $('#stat_range'), callBtn = $('#stat_call');
  if (rangeBtn) rangeBtn.onclick = () => {
    const cr = $('#call_range'); if (cr) cr.value = ''; load(); };
  if (callBtn) callBtn.onclick = () => {
    const cr = $('#call_range'); if (cr && $('#stat_key'))
      cr.value = $('#stat_key').value;
    load();
  };
  const open = $('#stat_reports');
  if (open) open.onclick = () => {
    state.view = 'study';
    document.querySelectorAll('#tabs button').forEach(x =>
      x.classList.toggle('on', x.dataset.v==='study'));
    const cr = $('#call_range');
    const sk = $('#stat_key');
    const q = $('#quick');
    // Action range is --quick KEY. Call Range is already --call-range.
    // The exclude box is not an argv flag and stays behind.
    if (q) q.value = (cr && cr.value) ? '' : ((sk && sk.value) || '');
    if (cr && cr.value) { /* --call-range already in params */ }
    else if (cr) cr.value = '';
    load();
  };
  $('#out').querySelectorAll('.chartcell').forEach(b => {
    b.onclick = () => { $('#combo').value = b.dataset.combo; load(); };
  });
  bindHist($('#out'), d, 'stat');
  bindHandRows(d.drill || {});
}
function renderSessions(d){
  const warn = d.warning || '';
  const rows = d.rows || [];
  let h = '<p class="n">'+(d.clock||'')+'</p>';
  if (warn) h += '<p class="empty">'+warn+'</p>';
  h += '<table><thead><tr><th>when</th><th>site</th><th>hero</th>'
    + '<th>hands</th><th>duration</th><th>Won</th><th>Won bb</th></tr></thead><tbody>';
  if (!rows.length)
    h += '<tr><td colspan="7" class="n">no hero cash sessions</td></tr>';
  for (const r of rows){
    const cls = r['Won bb']>0 ? 'pos' : (r['Won bb']<0 ? 'neg' : '');
    h += `<tr class="click ${cls}" data-sid="${r.id}">`
      + `<td>${(r.when||'').slice(0,16)}</td><td>${r.site}</td>`
      + `<td>${r.hero}</td><td>${r.hands}</td><td>${r.duration}</td>`
      + `<td>${money(r.Won)}</td><td>${money(r['Won bb'])}</td></tr>`;
  }
  h += '</tbody></table>';
  const det = d.detail || {};
  const s = det.session;
  if (s){
    h += `<p class="smart">${s.site} ${s.hero} · ${s.hands} hands · ${s.duration}`
      + ` · Won ${money(s.Won)} · Won bb ${money(s['Won bb'])}</p>`
      + `<p><a class="link" id="openrep">Open in Reports</a>`
      + ` · <a class="link" id="expsess">Export session hands</a></p>`;
    if (det.markup) h += det.markup;
    else if (det.svg) h += det.svg;
    else if (det.why_graph) h += `<p class="n">${det.why_graph}</p>`;
    h += renderStudyHands(det.hands || []);
  } else {
    h += '<p class="n">click a session for the graph, compact hands, export, '
      + 'and Open in Reports.</p>';
  }
  $('#out').innerHTML = h;
  if (window.bindWinGraph)
    $('#out').querySelectorAll('.wingraph').forEach(window.bindWinGraph);
  $('#out').querySelectorAll('tr[data-sid]').forEach(tr => {
    tr.onclick = () => { state.sessionId = tr.dataset.sid; load(); };
  });
  const open = $('#openrep');
  if (open) open.onclick = e => {
    e.preventDefault();
    $('#session').value = state.sessionId || (s && s.id) || '';
    state.view = 'study';
    document.querySelectorAll('#tabs button').forEach(x =>
      x.classList.toggle('on', x.dataset.v === 'study'));
    load();
  };
  const exp = $('#expsess');
  if (exp) exp.onclick = e => {
    e.preventDefault();
    const dest = prompt('export path', 'session-hands.txt');
    if (!dest) return;
    const p = new URLSearchParams({view:'export-session',
      session_id: state.sessionId || (s && s.id) || '', out: dest});
    fetch('/api?'+p).then(r => r.json()).then(got => {
      alert(got.error || ('wrote '+(got.wrote||0)+' hands'
        + (got.missing ? '\\n'+got.missing+' skipped (source gone)' : '')));
    });
  };
}
function renderStudy(d){
  const crumbs = ['<a data-i="-1">All</a>'].concat(
    (state.crumbs||[]).map((c,i) => ' › <a data-i="'+i+'">'+(c.label||c.value||'?')+'</a>')
  ).join('');
  const sessChip = ($('#session') && $('#session').value.trim())
    ? '<span class="chip on chip-x" data-sess="1">session '
      + $('#session').value.trim().slice(0,18)+' ×</span>' : '';
  const chips = sessChip + ((state.crumbs||[]).map((c,i) =>
    '<span class="chip on chip-x" data-i="'+i+'">'+(c.label||c.how||'?')+' ×</span>'
  ).join('') || (sessChip ? '' : '<span class="n">unfiltered</span>'));
  const summ = d.summary || {}, prof = d.profit || {};
  let smart = [];
  if (summ.opps) smart.push((summ.hits||0).toLocaleString()+'/'+summ.opps.toLocaleString()
    +'  '+(summ.pct||0).toFixed(1)+'%');
  if (prof.bb_per_hand != null) smart.push('AP '+(prof.bb_per_hand>=0?'+':'')+prof.bb_per_hand.toFixed(2)+' bb');
  const fams = ((d.families||{}).rows||[]).slice(0,8).map(r =>
    '<a data-fam="'+r.key+'">'+r.label+'</a>').join('');
  const plus = (d.plus||[]).filter(k => !(state.extraPanes||[]).includes(k))
    .map(k => '<option value="'+k+'">'+( {size:'Bet Sizes',made:'Flop Hand',
      board:'Flop Board',combo:'Combos',hist:'Hand Values'}[k]||k)+'</option>').join('');
  const names = ['results','stack','position','next'].concat(state.extraPanes||[]);
  let panes = '<div class="panes">';
  for (const name of names){
    const pane = (d.panes||{})[name] || (name==='combo' ? d.families : null);
    panes += renderPane(name, pane);
  }
  panes += '</div>';
  let h = '<div class="crumbs">path '+crumbs+'</div>'
    + '<div class="chips-row">filter '+chips+'</div>'
    + '<p class="smart">'+(smart.join('  ·  ')||'Smart')
    + (fams ? '  combos '+fams : '')+'</p>'
    + '<p class="n">+ pane <select id="plus">'+plus+'</select>'
    + '  click a row to drill · ⚙ Detach / Dock back · right-click a hand</p>'
    + panes
    + renderHist(d.hist, {click: 'study'})
    + '<p class="n"><button type="button" id="detach-graph">⚙ Detach graph</button></p>'
    + (d.graph && d.graph.n >= 2
        ? (d.graph_markup || '')
        : '')
    + renderStudyHands(d.hands||[]);
  if (DETACH_PANE){
    const titles = (d.detach && d.detach.titles) || {};
    document.title = titles[DETACH_PANE] || (DETACH_PANE + ' · detached');
    let only = '';
    if (DETACH_PANE === 'graph'){
      only = (d.graph_markup || '<p class="empty">not enough hands to draw a line</p>');
    } else if (DETACH_PANE === 'hands'){
      only = renderStudyHands(d.hands||[]);
    } else if (DETACH_PANE === 'hist'){
      only = renderHist(d.hist, {click: 'study'}) + renderStudyHands(d.hands||[]);
    } else {
      const pane = (d.panes||{})[DETACH_PANE] || (DETACH_PANE==='combo' ? d.families : null);
      only = '<div class="panes">'+renderPane(DETACH_PANE, pane)+'</div>'
        + renderStudyHands(d.hands||[]);
    }
    h = '<p class="n">live with the main filter · pin stays in-main'
      + ' · <button type="button" id="dock-back">Dock back</button></p>' + only;
  }
  $('#out').innerHTML = h;
  $('#out').querySelectorAll('.crumbs a').forEach(a => {
    a.onclick = e => { e.preventDefault(); popCrumb(+a.dataset.i); };
  });
  $('#out').querySelectorAll('.chip-x').forEach(a => {
    a.onclick = () => {
      if (a.dataset.sess){ $('#session').value = ''; state.sessionId = ''; load(); return; }
      state.crumbs = state.crumbs.filter((_,i)=>i!==+a.dataset.i); load();
    };
  });
  $('#out').querySelectorAll('[data-fam]').forEach(a => {
    a.onclick = e => { e.preventDefault();
      const row = ((d.families||{}).rows||[]).find(r => r.key===a.dataset.fam);
      if (row) applyStep(stepFromRow(row));
    };
  });
  const plusEl = $('#plus');
  if (plusEl) plusEl.onchange = () => {
    if (plusEl.value && !state.extraPanes.includes(plusEl.value))
      state.extraPanes.push(plusEl.value);
    load();
  };
  $('#out').querySelectorAll('.pane tbody tr[data-pane]').forEach(tr => {
    tr.onclick = () => {
      const pane = (d.panes||{})[tr.dataset.pane] || d.families;
      const row = (pane.rows||[])[+tr.dataset.i];
      if (row) applyStep(stepFromRow(row));
    };
    tr.oncontextmenu = e => {
      e.preventDefault();
      const pane = (d.panes||{})[tr.dataset.pane] || d.families;
      const row = (pane.rows||[])[+tr.dataset.i];
      if (row) showCtx(e, [
        ['Drill', () => applyStep(stepFromRow(row))],
        ['Pin this', () => pinStep(stepFromRow(row))],
      ]);
    };
  });
  $('#out').querySelectorAll('.pane h3 button').forEach(b => {
    b.onclick = () => gearPane(b.dataset.pane, d);
  });
  const dg = $('#detach-graph');
  if (dg) dg.onclick = () => detachPane('graph');
  const db = $('#dock-back');
  if (db) db.onclick = () => dockBack();
  $('#out').querySelectorAll('.pane th[data-sort]').forEach(th => {
    th.onclick = () => {
      const pane = th.closest('.pane');
      const name = (pane && pane.querySelector('h3 button')
                    && pane.querySelector('h3 button').dataset.pane);
      if (!name) return;
      const col = th.dataset.sort;
      const cur = state.paneSort[name];
      state.paneSort[name] = [col, !!(cur && cur[0]===col && !cur[1])];
      renderStudy(d);
    };
  });
  bindHandRows(d);
  bindHist($('#out'), d, 'study');
  if (window.bindWinGraph)
    $('#out').querySelectorAll('.wingraph').forEach(window.bindWinGraph);
  window.__lastStudy = d;
  if (!DETACH_PANE && DETACH_CH)
    DETACH_CH.postMessage({kind:'study', data: d});
}
function renderHist(got, how){
  how = how || {};
  if (!got || !got.n){
    return '<div class="hist"><p class="n">'
      + 'no shown hands — Weak % needs cards (Ignition shows folds; ACR 23%)</p></div>';
  }
  const rows = (got.rows||[]).filter(r => r.n || r.key==='other');
  const peak = Math.max(1, ...rows.map(r => r.pct||0));
  let h = '<div class="hist"><p class="weakpct" title="'
    + (got.note||'') + '">Weak ' + got.weak_pct.toFixed(1) + '%  ·  '
    + (got.weak_n||0).toLocaleString() + ' of ' + got.n.toLocaleString()
    + ' shown</p><p class="n">' + (got.weak_labels||[]).join(', ')
    + '  ·  click a bar to filter · click Weak on a bar to flip</p>';
  const cov = got.coverage||{};
  if ((cov.sites||[]).length){
    h += '<p class="n">' + cov.sites.map(s =>
      (s.site||'?')+': '+s.seen+'/'+s.total+' ('+s.pct.toFixed(0)+'%)'
    ).join(' · ') + '</p>';
  }
  h += '<div class="histbars">';
  for (const r of rows){
    const cls = r.is_weak ? 'weak' : (r.key==='other' ? 'other' : 'strong');
    const ht = Math.max(2, Math.round(100 * (r.pct||0) / peak));
    h += `<div class="histbar ${cls}" data-hist="${r.key}" data-weak="${r.is_weak?1:0}" `
      + `title="${r.label} ${r.pct.toFixed(1)}% n=${r.n} ${r.is_weak?'weak':'strong'}">`
      + `<i style="height:${ht}%"></i><span>${r.label}</span></div>`;
  }
  return h + '</div></div>';
}
function bindHist(root, d, kind){
  (root || document).querySelectorAll('.histbar').forEach(b => {
    b.onclick = (ev) => {
      if (ev.target.closest && ev.target.closest('.flipweak')) return;
      const key = b.dataset.hist;
      if (kind === 'stat'){
        const el = $('#hist_group');
        if (el) el.value = (el.value === key) ? '' : key;
        load();
        return;
      }
      const hist = d.hist || ((d.drill||{}).hist);
      const row = ((hist && hist.rows) || []).find(r => r.key === key);
      if (row) applyStep(stepFromRow(row));
    };
    b.oncontextmenu = (ev) => {
      ev.preventDefault();
      if (b.dataset.hist === 'other') return;
      const hist = (kind==='stat' ? ((d.drill||{}).hist) : d.hist);
      if (!hist || !hist.rows) return;
      const row = hist.rows.find(r => r.key === b.dataset.hist);
      if (!row) return;
      row.is_weak = !row.is_weak;
      let weak_n = 0;
      for (const r of hist.rows) if (r.is_weak) weak_n += r.n;
      hist.weak_n = weak_n;
      hist.weak_pct = hist.n ? 100.0 * weak_n / hist.n : 0;
      hist.weak_labels = hist.rows.filter(r => r.is_weak).map(r => r.label);
      if (kind === 'stat') renderStatistics(d);
      else renderStudy(d);
    };
  });
}
function renderPane(name, pane){
  const title = {results:'Results',stack:'Stack Sizes',position:'Positions',
    next:'Next Action',size:'Bet Sizes',made:'Flop Hand',board:'Flop Board',
    combo:'Combos',hist:'Hand Values'}[name] || name;
  const rows = (pane && pane.rows) || [];
  if (!rows.length)
    return '<div class="pane"><h3>'+title+'</h3><p class="n">nothing in this pane</p></div>';
  const hidden = new Set(state.paneHidden[name]||[]);
  if (name==='position' && !state.paneHidden[name]){
    hidden.add('vpip'); hidden.add('pfr');
  }
  let cols = ['label','n','freq'];
  if (name==='results') cols = ['label','hands','net','bb/100'];
  if (name==='stack' || name==='next' || name==='size')
    cols = ['label','n','freq','act bb'];
  if (name==='hist') cols = ['label','n','freq','weak'];
  cols = cols.filter(c => !hidden.has(c));
  const sort = state.paneSort[name];
  const copy = rows.slice();
  if (sort){
    copy.sort((a,b) => {
      const ka = paneSortKey(a, sort[0]), kb = paneSortKey(b, sort[0]);
      return sort[1] ? (ka<kb?1:-1) : (ka<kb?-1:1);
    });
  }
  const cell = (r,c) => {
    if (c==='label') return r.label||r.key||'';
    if (c==='n' || c==='hands') return (r.hands||r.n||0).toLocaleString();
    if (c==='freq') return (r.pct||0).toFixed(1)+'%';
    if (c==='act bb'){
      const ap = r.profit||{};
      return ap.bb_per_hand==null ? '–' : ((ap.bb_per_hand>=0?'+':'')+ap.bb_per_hand.toFixed(2));
    }
    if (c==='net') return (r.net_bb>=0?'+':'')+(r.net_bb||0).toFixed(1);
    if (c==='bb/100') return (r.bb100>=0?'+':'')+(r.bb100||0).toFixed(1);
    if (c==='weak') return r.is_weak ? 'weak' : '';
    return '';
  };
  let h = '<div class="pane"><h3>'+title+'<button type="button" data-pane="'+name+'">⚙</button></h3><table><thead><tr>';
  for (const c of cols) h += '<th data-sort="'+c+'">'+c+'</th>';
  h += '</tr></thead><tbody>';
  copy.forEach((r,i) => {
    const idx = rows.indexOf(r);
    h += '<tr class="click" data-pane="'+name+'" data-i="'+idx+'">';
    for (const c of cols) h += '<td>'+cell(r,c)+'</td>';
    h += '</tr>';
  });
  return h + '</tbody></table></div>';
}
function paneSortKey(r, col){
  if (col==='n'||col==='hands') return r.hands||r.n||0;
  if (col==='freq') return r.pct||0;
  if (col==='act bb') return (r.profit&&r.profit.bb_per_hand)||0;
  if (col==='net') return r.net_bb||0;
  if (col==='bb/100') return r.bb100||0;
  return String(r.label||r.key||'');
}
function gearPane(name, d){
  const hidden = new Set(state.paneHidden[name]||[]);
  const cols = name==='results'
    ? ['label','hands','net','bb/100']
    : (name==='graph' || name==='hands') ? []
    : ['label','n','freq','act bb'];
  const items = (DETACH_PANE
    ? [['Dock back', () => dockBack()]]
    : [['Detach', () => detachPane(name)]]).concat(
    cols.map(c => [(hidden.has(c)?'☐ ':'☑ ')+c, () => {
      if (hidden.has(c)) hidden.delete(c); else hidden.add(c);
      state.paneHidden[name] = [...hidden];
      renderStudy(d);
    }]));
  showCtx({clientX: window.event?window.event.clientX:80,
           clientY: window.event?window.event.clientY:80},
    items);
}
function detachPane(name){
  if (DETACH_PANE) return;
  const open = (state.detached||[]).filter(w => w && !w.closed);
  state.detached = open;
  const already = open.find(w => w.__pane === name);
  if (already){ try{ already.focus(); }catch(e){} return; }
  const cap = ((window.__lastStudy||{}).detach||{}).cap || 4;
  if (open.length >= cap){
    const el = $('#filter');
    if (el) el.textContent = 'detach cap 4 — close one';
    return;
  }
  const p = params();
  p.set('view', 'study');
  p.set('detach', name);
  const w = window.open('/?'+p.toString(), '_blank', 'width=720,height=520');
  if (w){
    w.__pane = name;
    state.detached.push(w);
  }
}
function dockBack(){
  if (!DETACH_PANE) return;
  const study = window.__lastStudy || {};
  const payload = study.dock || {
    context: (study.detach && study.detach.context) || [],
    crumbs: state.crumbs || []
  };
  if (window.opener && !window.opener.closed){
    window.opener.postMessage({kind:'dock', payload: payload}, '*');
    window.close();
  }
}
function dockReport(payload){
  // Same context id Detach already uses. If the study tab
  // already shows that nest, just focus it -- a second Reports
  // tab of the same crumbs is a duplicate.
  payload = payload || {};
  state.view = 'study';
  document.querySelectorAll('#tabs button').forEach(x =>
    x.classList.toggle('on', x.dataset.v === 'study'));
  $('#byrow').style.display = 'none';
  const incoming = payload.crumbs || [];
  const currentCtx = ((window.__lastStudy||{}).detach||{}).context || [];
  const incomingCtx = payload.context || [];
  const same = JSON.stringify(currentCtx) === JSON.stringify(incomingCtx);
  if (!same && incoming.length)
    state.crumbs = incoming.slice();
  try { window.focus(); } catch (e) {}
  load();
}
function renderStudyHands(rows){
  if (!rows.length) return '<p class="empty">no hands in this filter</p>';
  let h = '<table><thead><tr><th></th><th>when</th><th>site</th><th>pos</th>'
    + '<th>hand</th><th>net bb</th><th>act bb</th><th>compact</th></tr></thead><tbody>';
  for (const r of rows){
    const tags = (r.tags||[]).join(',');
    h += `<tr class="click handrow" data-id="${r.id}" data-seat="${r.seat}">`
      + `<td><button type="button" class="mark" data-id="${r.id}" data-unmark="${r.marked?1:0}">${r.marked?'★':'☆'}</button></td>`
      + `<td>${(r.when||'').slice(0,16)}</td><td>${r.site||''}</td>`
      + `<td>${r.pos||''}</td><td>${r.hand||r.combo||'–'}</td>`
      + `<td>${r.net==null?'':money(r.net)}</td>`
      + `<td>${r.act==null?'–':money(r.act)}</td>`
      + `<td class="compact">${tags?('['+tags+'] '):''}${r.compact||r.board||''}</td></tr>`;
  }
  return h + '</tbody></table>';
}
function bindHandRows(d){
  $('#out').querySelectorAll('tr.handrow, tr.click[data-id]').forEach(tr => {
    tr.ondblclick = () => openHand(tr.dataset.id, tr.dataset.seat);
    tr.oncontextmenu = e => {
      e.preventDefault();
      showCtx(e, [
        ['Replay', () => openHand(tr.dataset.id, tr.dataset.seat)],
        ['Mark', () => markHand(tr.dataset.id, false)],
        ['Unmark', () => markHand(tr.dataset.id, true)],
        ['Add to Note', () => noteHand(tr.dataset.id, tr.dataset.seat)],
      ]);
    };
  });
  $('#out').querySelectorAll('button.mark').forEach(b => {
    b.onclick = async e => {
      e.stopPropagation();
      await markHand(b.dataset.id, b.dataset.unmark==='1');
    };
  });
}
async function markHand(id, unmark){
  const p = new URLSearchParams({view:'mark', id});
  if (unmark) p.set('unmark','1');
  const tag = ($('#tag') && $('#tag').value.trim()) || '';
  if (tag) p.set('tag', tag);
  await fetch('/api?' + p);
  load();
}
async function noteHand(id, seat){
  const text = prompt('Note on '+id);
  if (!text || !text.trim()) return;
  const p = new URLSearchParams({view:'note', id, seat, text: text.trim()});
  await fetch('/api?' + p);
  load();
}
function showCtx(e, items){
  document.querySelectorAll('.ctx').forEach(n => n.remove());
  const m = document.createElement('div');
  m.className = 'ctx';
  m.style.left = (e.clientX||80)+'px';
  m.style.top = (e.clientY||80)+'px';
  items.forEach(([lab, fn]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = lab;
    b.onclick = () => { m.remove(); fn(); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', () => m.remove(), {once:true}), 0);
}
function openHand(id, seat){
  state.view = 'hand';
  fetch('/api?' + new URLSearchParams({view:'hand', id, seat}))
    .then(r => r.json()).then(d => renderHand(d.hand));
}

function render(d){
  const out = $('#out');
  $('#filter').textContent = 'filter: ' + (d.label || 'everything');
  paintRelated(d.related);
  if (state.view === 'sessions'){
    if (d.error){ out.innerHTML = `<p class="empty">${d.error}</p>`; return; }
    renderSessions(d); return;
  }
  if (state.view === 'statistics'){
    if (d.error){ out.innerHTML = `<p class="empty">${d.error}</p>`; return; }
    renderStatistics(d); return;
  }
  if (state.view === 'study'){
    if (d.error){ out.innerHTML = `<p class="empty">${d.error}</p>`; return; }
    renderStudy(d); return;
  }
  if (d.error){ out.innerHTML = `<p class="empty">${d.error}</p>`; return; }
  const nope = msg => `<p class="empty">nothing matches<br><span class="n">${
    d.why || msg || ''}</span></p>`;

  if (state.view === 'stats'){
    if (!d.rows.length && !(d.actions && d.actions.mix && d.actions.mix.length)){
      out.innerHTML = d.decisions
        ? `<p class="empty">no stat can occur inside this filter<br><span class="n">asking for a preflop stat inside street=flop does this</span></p>`
        : nope(); return; }
    let g = null, h = `<p class="n">${d.decisions.toLocaleString()} decisions match</p><table><tbody>`;
    if (d.compare && d.compare.a && d.compare.b){
      const A = d.compare.a, B = d.compare.b;
      const sa = A.summary || {}, sb = B.summary || {};
      const pa = A.profit || {}, pb = B.profit || {};
      const ap = p => p.bb_per_hand == null ? '–'
        : ((p.bb_per_hand>=0?'+':'') + p.bb_per_hand.toFixed(2) + ' bb'
           + (p.lo == null ? '' : ' ['+p.lo.toFixed(1)+', '+p.hi.toFixed(1)+']'));
      h += `<tr><td class="group">this vs pinned</td>`
        + `<td class="group">${A.name || 'this'}</td><td></td>`
        + `<td class="group">${B.name || 'pinned'}</td></tr>`
        + `<tr><td>hits / opps</td>`
        + `<td>${(sa.hits||0).toLocaleString()} / ${(sa.opps||0).toLocaleString()}</td><td></td>`
        + `<td>${(sb.hits||0).toLocaleString()} / ${(sb.opps||0).toLocaleString()}</td></tr>`
        + `<tr><td>freq</td>`
        + `<td>${sa.opps ? sa.pct.toFixed(1)+'%' : '–'}</td><td></td>`
        + `<td>${sb.opps ? sb.pct.toFixed(1)+'%' : '–'}</td></tr>`
        + `<tr><td>hits / 1000</td>`
        + `<td>${(sa.per_1k||0).toFixed(1)}</td><td></td>`
        + `<td>${(sb.per_1k||0).toFixed(1)}</td></tr>`
        + `<tr><td>action profit</td><td>${ap(pa)}</td><td></td><td>${ap(pb)}</td></tr>`
        + `<tr><td>call profit</td><td>${ap(A.call_profit||{})}</td><td></td>`
        + `<td>${ap(B.call_profit||{})}</td></tr>`;
      const fd = d.compare.freq_diff;
      if (fd && fd.d != null)
        h += `<tr><td>freq this − pin</td>`
          + `<td>${(100*fd.d).toFixed(1)} pts</td>`
          + `<td class="n" colspan="2">[${(100*fd.lo).toFixed(1)}, ${(100*fd.hi).toFixed(1)}]</td></tr>`;
    } else if (d.summary && d.summary.opps){
      h += `<tr><td colspan="4" class="group">hits / opportunities</td></tr>`
        + `<tr><td>hits (${d.summary.label})</td><td>${d.summary.hits.toLocaleString()}</td>`
        + `<td class="n"></td><td class="n">${d.summary.opps.toLocaleString()} opps</td></tr>`
        + `<tr><td>hits / 1000 hands</td><td>${d.summary.per_1k.toFixed(1)}</td>`
        + `<td class="n">±${d.summary.band.toFixed(0)}</td>`
        + `<td class="n">${d.summary.hands.toLocaleString()} hands</td></tr>`
        + `<tr><td>${d.summary.label}</td><td>${d.summary.pct.toFixed(1)}%</td>`
        + `<td class="n">±${d.summary.band.toFixed(0)}</td>`
        + `<td class="n">n=${d.summary.opps.toLocaleString()}</td></tr>`;
    }
    if (d.pinned && d.pinned.error){
      h += `<tr><td colspan="4" class="n">${d.pinned.error}</td></tr>`;
    }
    const meanBand = p => {
      if (p.bb_per_hand == null) return 'unpriced';
      const m = (p.bb_per_hand>=0?'+':'') + p.bb_per_hand.toFixed(2) + ' bb/hand';
      if (p.lo != null) return m + ' ['+p.lo.toFixed(1)+', '+p.hi.toFixed(1)+']';
      if (p.priced === 1) return m + ' (n=1, no interval)';
      return m;
    };
    if (d.profit && d.profit.n){
      h += `<tr><td>action profit</td><td>${meanBand(d.profit)}</td><td class="n">priced hits</td>`
        + `<td class="n">${d.profit.priced.toLocaleString()} of ${d.profit.n.toLocaleString()}</td></tr>`
        + `<tr><td colspan="4" class="n">${d.profit.note}</td></tr>`;
      if (d.profit.interval_note)
        h += `<tr><td colspan="4" class="n">${d.profit.interval_note}</td></tr>`;
      for (const edge of (d.profit.edges || []))
        h += `<tr><td colspan="4" class="n">unpriced: ${edge}</td></tr>`;
    }
    if (d.call_profit && d.call_profit.n){
      h += `<tr><td>call profit</td><td>${meanBand(d.call_profit)}</td><td class="n">priced calls</td>`
        + `<td class="n">${d.call_profit.priced.toLocaleString()} of ${d.call_profit.n.toLocaleString()}</td></tr>`
        + `<tr><td colspan="4" class="n">${d.call_profit.note}</td></tr>`;
      if (d.call_profit.interval_note)
        h += `<tr><td colspan="4" class="n">${d.call_profit.interval_note}</td></tr>`;
      for (const edge of (d.call_profit.edges || []))
        h += `<tr><td colspan="4" class="n">unpriced: ${edge}</td></tr>`;
    }
    if (d.amount_won && d.amount_won.hands){
      const w = d.amount_won;
      const band = w.lo == null ? '' : ' ['+w.lo.toFixed(1)+', '+w.hi.toFixed(1)+']';
      const wband = w.won_lo == null ? '' : ' ['+w.won_lo.toFixed(0)+', '+w.won_hi.toFixed(0)+']';
      h += `<tr><td>Won$</td><td>${w.bb_per_hand>=0?'+':''}${w.bb_per_hand.toFixed(2)} bb/hand${band}</td>`
        + `<td class="n">${w.bb100>=0?'+':''}${w.bb100.toFixed(1)} bb/100 ±${w.error.toFixed(0)}</td>`
        + `<td class="n">${w.hands.toLocaleString()} cash hands</td></tr>`
        + `<tr><td>Won hand%</td><td>${w.won_pct.toFixed(1)}%${wband}</td>`
        + `<td class="n"></td><td class="n">${w.won_hands.toLocaleString()} of ${w.hands.toLocaleString()}</td></tr>`
        + `<tr><td colspan="4" class="n">${w.note}</td></tr>`;
    }
    if (d.cohort_range){
      const cr = d.cohort_range, cov = cr.coverage || {};
      h += `<tr><td colspan="4" class="group">preflop range — this cohort</td></tr>`
        + `<tr><td>hole cards shown</td><td>${(cr.seen||0).toLocaleString()} of ${(cr.total||0).toLocaleString()}</td>`
        + `<td class="n">${(cov.pct||0).toFixed(0)}%</td><td></td></tr>`;
      for (const s of (cov.sites || []))
        h += `<tr><td>${s.site || '?'}</td><td>${s.seen.toLocaleString()}/${s.total.toLocaleString()}</td>`
          + `<td class="n">${s.pct.toFixed(0)}%</td><td class="n">${s.note||''}</td></tr>`;
      if (cov.note)
        h += `<tr><td colspan="4" class="n">${cov.note}</td></tr>`;
      if (cr.top && cr.top.length)
        h += `<tr><td>most of it</td><td colspan="3">${cr.top.map(t => t.combo+' '+t.pct.toFixed(1)+'%').join(', ')}</td></tr>`;
    }
    if (d.actions && d.actions.mix && d.actions.mix.length){
      h += `<tr><td colspan="4" class="group">this spot</td></tr>`;
      for (const r of d.actions.mix.concat(d.actions.extra || []))
        h += `<tr><td>${r.label}</td>`
          + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
          + `<td class="n">±${r.band.toFixed(0)}</td>`
          + `<td class="n">n=${r.n.toLocaleString()}</td></tr>`;
    }
    if (d.outcomes && d.outcomes.rows && d.outcomes.rows.length){
      h += `<tr><td colspan="4" class="group">outcome</td></tr>`;
      for (const r of d.outcomes.rows)
        h += `<tr class="drill" data-flag="outcome" data-key="${r.key}">`
          + `<td>${r.label}</td>`
          + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
          + `<td class="n">±${r.band.toFixed(0)}</td>`
          + `<td class="n">n=${r.k.toLocaleString()}</td></tr>`;
    }
    for (const [title, flag, blob] of [
        ['faced next (the other seat)', 'after', d.faced],
        ['next actions (this player)', 'then', d.next]]){
      if (!blob || !blob.rows || !blob.rows.length) continue;
      h += `<tr><td colspan="4" class="group">${title}</td></tr>`;
      for (const r of blob.rows){
        const ap = r.profit || {};
        const act = ap.bb_per_hand == null ? '–'
          : ((ap.bb_per_hand>=0?'+':'') + ap.bb_per_hand.toFixed(2));
        const hits = r.hits != null ? r.hits : r.k;
        const opps = r.opps != null ? r.opps : r.n;
        h += `<tr class="drill" data-flag="${flag}" data-key="${r.key}">`
          + `<td>${r.label}</td>`
          + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
          + `<td class="n">${hits.toLocaleString()} / ${opps.toLocaleString()}</td>`
          + `<td class="n">${act}</td></tr>`;
      }
    }
    if (d.compare && d.compare.sizes){
      h += htmlCompareSizes(d.compare);
    } else if (d.sizes && d.sizes.rows && d.sizes.rows.length){
      h += `<tr><td colspan="4" class="group">bet sizes</td></tr>`;
      for (const r of d.sizes.rows){
        const ap = r.profit || {};
        const act = ap.bb_per_hand == null ? '–'
          : ((ap.bb_per_hand>=0?'+':'') + ap.bb_per_hand.toFixed(2));
        h += `<tr class="drill" data-flag="size" data-key="${r.key}">`
          + `<td>${r.label}</td>`
          + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
          + `<td class="n">${r.hits.toLocaleString()} / ${r.opps.toLocaleString()}</td>`
          + `<td class="n">${act}</td></tr>`;
      }
    }
    if (d.compare && d.compare.packs && (d.compare.packs.a||[]).length + (d.compare.packs.b||[]).length){
      h += htmlComparePacks(d.compare);
    }
    for (const r of d.rows){
      if (r.group !== g){ g = r.group;
        h += `<tr><td colspan="4" class="group">${g}</td></tr>`; }
      h += `<tr class="drill" data-flag="quick" data-key="${r.key}"`
        + ` title="${r.note||''}"><td>${r.label}</td>`
        + `<td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
        + `<td class="n">±${r.band.toFixed(0)}</td>`
        + `<td class="n">n=${r.n.toLocaleString()}</td></tr>`;
    }
    out.innerHTML = h + '</tbody></table>';
    out.querySelectorAll('tr.drill').forEach(tr => {
      tr.onclick = () => {
        const flag = tr.dataset.flag, key = tr.dataset.key;
        if (!key) return;
        if (flag === 'quick'){
          state.multi.quick = [key];
        } else if (flag === 'size'){
          $('#size').value = key;
        } else {
          $('#'+flag).value = key;
        }
        load();
      };
    });

  } else if (state.view === 'range'){
    if (!d.n){ out.innerHTML = nope('no hand in this filter was ever shown'); return; }
    let h = `<p class="n">${d.n.toLocaleString()} of ${d.total.toLocaleString()} decisions had cards to read (${(100*d.n/d.total).toFixed(0)}%)</p><table><tbody>`;
    for (const r of (d.rows||[]))
      h += `<tr><td>${r.made}</td><td>${r.pct.toFixed(1)}%</td>`
        + `<td class="n">${r.n.toLocaleString()}</td>`
        + `<td class="n">${r.weak?'weak':''}</td></tr>`;
    h += `<tr><td>WEAK</td><td>${d.weak.toFixed(1)}%</td><td></td><td class="n">cannot call</td></tr>`
      + `<tr><td>STRONG</td><td>${d.strong.toFixed(1)}%</td><td></td><td></td></tr>`;
    for (const x of (d.draws||[]))
      h += `<tr><td>${x.label}</td><td>${x.pct.toFixed(1)}%</td><td class="n">${x.n.toLocaleString()}</td><td></td></tr>`;
    const cov = d.coverage || {};
    for (const s of (cov.sites||[]))
      h += `<tr><td>${s.site||'?'}</td><td>${s.seen.toLocaleString()}/${s.total.toLocaleString()}</td>`
        + `<td class="n">${s.pct.toFixed(0)}%</td><td class="n">${s.note||''}</td></tr>`;
    if (cov.note) h += `<tr><td colspan="4" class="n">${cov.note}</td></tr>`;
    out.innerHTML = h + '</tbody></table>';

  } else if (state.view === 'chart'){
    if (d.gated){
      out.innerHTML = `<p>${d.reason || 'The 13×13 is a Hold\\u2019em chart.'}</p>`
        + (d.total ? `<p class="n">${d.total.toLocaleString()} player-hands match. Open Hands for the shown cards.</p>` : '');
      return;
    }
    if (!d.total){ out.innerHTML = nope(); return; }
    const RANKS = 'AKQJT98765432';
    const comboAt = (i,j) => {
      const hi = RANKS[i], lo = RANKS[j];
      if (i===j) return hi+hi;
      return i<j ? hi+lo+'s' : lo+hi+'o';
    };
    let h = `<p class="n">${(d.seen||0).toLocaleString()} of ${d.total.toLocaleString()} player-hands showed cards (${d.total? (100*d.seen/d.total).toFixed(1):0}%)</p>`;
    const cov = d.coverage || {};
    for (const s of (cov.sites||[]))
      h += `<p class="n">${s.site||'?'}: ${s.seen.toLocaleString()}/${s.total.toLocaleString()} (${s.pct.toFixed(0)}%) — ${s.note||''}</p>`;
    if (cov.note) h += `<p class="n">${cov.note}</p>`;
    h += '<table><thead><tr><th></th>' + RANKS.split('').map(r=>`<th>${r}</th>`).join('') + '</tr></thead><tbody>';
    for (let i=0;i<13;i++){
      h += `<tr><th>${RANKS[i]}</th>`;
      for (let j=0;j<13;j++){
        const cell = (d.cells||{})[comboAt(i,j)];
        const n = cell ? cell[0] : 0;
        h += n ? `<td class="n">${(100*n/d.seen).toFixed(1)}</td>` : '<td class="n">.</td>';
      }
      h += '</tr>';
    }
    out.innerHTML = h + '</tbody></table>';

  } else if (state.view === 'report'){
    if (d.compare && (d.compare.sizes || d.compare.by)){
      out.innerHTML = htmlReportCompare(d); return;
    }
    if (d.dim === 'size' || d.sizes){
      const rows = (d.sizes && d.sizes.rows) || [];
      if (!rows.length){ out.innerHTML = nope(); return; }
      let h = '<table><thead><tr><th>size</th><th>freq</th><th>hits / opps</th><th>act bb</th></tr></thead><tbody>';
      for (const r of rows){
        const ap = r.profit || {};
        const act = ap.bb_per_hand == null ? '–'
          : ((ap.bb_per_hand>=0?'+':'') + ap.bb_per_hand.toFixed(2));
        h += `<tr class="drill" data-flag="size" data-key="${r.key}">`
          + `<td>${r.label}</td><td class="${r.n<30?'thin':''}">${r.pct.toFixed(1)}%</td>`
          + `<td class="n">${r.hits.toLocaleString()} / ${r.opps.toLocaleString()}</td>`
          + `<td class="n">${act}</td></tr>`;
      }
      h += '</tbody></table><p class="n">freq is this size of the parent filter. Checks and folds have no size. act bb is Action Profit v1; later pot on a called bet stays unpriced.</p>';
      out.innerHTML = h;
      out.querySelectorAll('tr.drill').forEach(tr => {
        tr.onclick = () => { $('#size').value = tr.dataset.key; load(); };
      });
      return;
    }
    if (!d.rows || !d.rows.length){ out.innerHTML = nope(); return; }
    const hasWon = !!(d.won_by && Object.keys(d.won_by).length);
    let h = '<table><thead><tr><th>'+d.dim+'</th>'
      + d.columns.map(c=>`<th>${c.label}</th>`).join('') + '<th>n</th>'
      + (hasWon ? '<th>Won$ bb/100</th><th>Won hand%</th>' : '')
      + '</tr></thead><tbody>';
    for (const r of d.rows){
      h += `<tr><td>${r.key}</td>` + r.cells.map(c => c === null
        ? '<td class="n">–</td>'
        : `<td class="${c.n<30?'thin':''}" title="n=${c.n}">${c.pct.toFixed(1)}%</td>`
      ).join('') + `<td class="n">${r.n.toLocaleString()}</td>`;
      if (hasWon){
        const w = r.won;
        h += w
          ? `<td class="n">${w.bb100>=0?'+':''}${w.bb100.toFixed(0)} ±${w.error.toFixed(0)}</td>`
            + `<td class="n">${w.won_pct.toFixed(0)}%</td>`
          : '<td class="n">–</td><td class="n">–</td>';
      }
      h += '</tr>';
    }
    out.innerHTML = h + '</tbody></table>'
      + (hasWon ? '<p class="n">Won$ is whole-hand net_bb of these seats, spots-sourced; MTT out. Not this street.</p>' : '');

  } else if (state.view === 'results'){
    if (d.rows){
      if (!d.rows.length){ out.innerHTML = nope(); return; }
      let h = `<table><thead><tr><th>${d.dim}</th><th>hands</th><th>net bb</th>`
        + `<th>bb/100</th><th>± error</th></tr></thead><tbody>`;
      for (const r of d.rows)
        h += `<tr><td>${r.key}</td><td class="n">${r.hands.toLocaleString()}</td>`
          + `<td>${money(r.net_bb)}</td><td>${money(r.bb100)}</td>`
          + `<td class="n">${r.error.toFixed(0)}</td></tr>`;
      out.innerHTML = h + '</tbody></table>';
    } else if (!d.totals){
      out.innerHTML = nope();
    } else {
      const t = d.totals;
      out.innerHTML = `<table><tbody>
        <tr><td>hands</td><td>${t.hands.toLocaleString()}</td></tr>
        <tr><td>net</td><td>${money(t.net_bb)} bb</td></tr>
        <tr><td>in money</td><td>${money(t.money)}</td></tr>
        <tr><td>per 100 hands</td><td>${money(t.bb100)} bb/100</td></tr>
        <tr><td>error on that</td><td class="n">±${t.error.toFixed(0)} bb/100</td></tr>
        <tr><td>saw a flop</td><td class="n">${t.saw_flop.toLocaleString()}</td></tr>
        <tr><td>won at showdown</td><td class="n">${t.wtsd.toLocaleString()}</td></tr>
        </tbody></table>
        <p class="n" style="margin-top:14px;max-width:56ch">One hand's result has a
        standard deviation around 11.7bb, so the error on a win rate is about
        1170/√n. Where it is wider than the rate, the rate is not telling you
        anything.</p>`;
    }

  } else if (state.view === 'graph'){
    out.innerHTML = d.svg || nope(d.why || 'not enough hands to draw a line');
    if (window.bindWinGraph)
      out.querySelectorAll('.wingraph').forEach(window.bindWinGraph);

  } else {
    if (!d.rows.length){ out.innerHTML = nope(); return; }
    let h = '<p class="n">act bb is Action Profit; call bb is Call Profit Rate (actual calls). net bb is the hand. A dash is unpriced. Underlined actions are this row\'s seat. Click a hand to replay it. The star marks it.</p>'
      + '<table><thead><tr><th></th><th>when</th><th>site</th><th>bb</th><th>pos</th>'
      + '<th>hand</th><th>net bb</th><th>act bb</th><th>call bb</th><th>compact</th></tr></thead><tbody>';
    for (const r of d.rows){
      const tags = (r.tags||[]).join(',');
      h += `<tr class="click" data-id="${r.id}" data-seat="${r.seat}">`
        + `<td><button type="button" class="mark" data-id="${r.id}" data-unmark="${r.marked?1:0}">${r.marked?'★':'☆'}</button></td>`
        + `<td>${(r.when||'').slice(0,16)}</td><td>${r.site}</td>`
        + `<td class="n">${r.bb??''}</td><td>${r.pos||''}</td>`
        + `<td>${r.hand||r.combo||'–'}</td><td>${r.net==null?'':money(r.net)}</td>`
        + `<td>${r.act==null?'–':money(r.act)}</td>`
        + `<td>${r.call==null?'–':money(r.call)}</td>`
        + `<td class="compact">${tags?('['+tags+'] '):''}${r.compact||r.board||''}</td></tr>`;
    }
    out.innerHTML = h + '</tbody></table>';
    out.querySelectorAll('button.mark').forEach(b => {
      b.onclick = async e => {
        e.stopPropagation();
        const p = new URLSearchParams({view:'mark', id:b.dataset.id});
        if (b.dataset.unmark === '1') p.set('unmark','1');
        const tag = ($('#tag') && $('#tag').value.trim()) || '';
        if (tag) p.set('tag', tag);
        await fetch('/api?' + p);
        load();
      };
    });
  }
}

const CARD = c => {
  // Suits get their colour back, because a flush is the thing you are
  // looking for and four letters in one grey is not how anyone reads a hand.
  const col = {s:'#d8dbe0', h:'#d1443c', d:'#4c9aff', c:'#22a35a'}[c[1]] || '';
  return `<span style="color:${col}">${c}</span>`;
};
const CARDS = t => (t||'').split(/\s+/).filter(Boolean).map(CARD).join(' ');

function renderHand(d){
  if (!d){ $('#out').innerHTML = '<p class="empty">hand not found</p>'; return; }
  const stake = d.bb ? `$${d.sb}/$${d.bb}` : '–';
  let h = `<p><span class="link" id="back">&larr; back to hands</span></p>`
    + `<p class="filter">${d.hand_id} · ${d.site} · ${d.fmt} · ${stake}`
    + ` · ${d.played_at} · ${d.table}</p>`
    + (d.compact ? `<p class="compact">${d.compact}</p>` : '')
    + '<table><thead><tr><th>seat</th><th>player</th><th>stack</th>'
    + '<th>cards</th><th>net</th></tr></thead><tbody>';
  for (const s of d.seats){
    const net = (s.won||0) - (s.put_in||0);
    const me = s.seat === d.focus ? ' style="background:#232833"' : '';
    h += `<tr${me}><td>${s.position||'?'}${s.is_hero?' <span class="n">(you)</span>':''}</td>`
      + `<td>${s.name||''}</td><td class="n">${(s.stack||0).toFixed(2)}</td>`
      + `<td>${s.hand||(s.cards?CARDS(s.cards):'<span class="n">not shown</span>')}</td>`
      + `<td>${money(net)}</td></tr>`;
  }
  h += '</tbody></table>';
  for (const st of d.streets){
    const pot = st.actions.length && st.actions[0].pot_before != null
      ? `  ·  pot ${st.actions[0].pot_before.toFixed(2)}` : '';
    h += `<p class="group">${st.street}${st.board?'  '+CARDS(st.board):''}${pot}</p>`
      + '<table><tbody>';
    for (const a of st.actions)
      h += `<tr><td style="width:80px">${a.position||'?'}</td>`
        + `<td>${a.name||''}</td><td>${a.verb}`
        + `${a.amount?' '+a.amount.toFixed(2):''}</td></tr>`;
    h += '</tbody></table>';
  }
  if (d.pot) h += `<p class="n">total pot ${d.pot.toFixed(2)}`
    + `${d.rake?' · rake '+d.rake.toFixed(2):''}</p>`;
  $('#out').innerHTML = h;
  $('#back').onclick = load;
}

document.addEventListener('click', async e => {
  const row = e.target.closest('tr.click');
  if (!row) return;
  const p = new URLSearchParams({view:'hand', id:row.dataset.id,
                                 seat:row.dataset.seat});
  const d = await (await fetch('/api?' + p)).json();
  renderHand(d.hand);
});

let seq = 0;
async function load(){
  const mine = ++seq;
  $('#out').innerHTML = '<div class="bar"></div>';
  const r = await fetch('/api?' + params().toString());
  const d = await r.json();
  if (mine === seq) render(d);
}
function hydrateFromURL(){
  const q = new URLSearchParams(location.search);
  if (!q.get('detach') && !q.get('view')) return;
  if (q.get('preset')){
    state.preset = q.get('preset');
    const box = $('#preset');
    if (box) box.value = state.preset;
  }
  if (q.get('crumbs')){
    try { state.crumbs = JSON.parse(q.get('crumbs')); } catch (e) { state.crumbs = []; }
  }
  if (q.get('panes'))
    state.extraPanes = q.get('panes').split(',').filter(Boolean);
  ['hero','pool','ip','oop','pfa','not_pfa','multiway','headsup',
   'vs_pfa','vs_hero','vs_pool','first_in','last_raise','first_raise',
   'last_action','marked','noted','today'].forEach(f => {
    if (q.get(f) === '1') state.flags[f] = true;
  });
  ['pos','vs','street','pot','facing','board'].forEach(g => {
    if (q.get(g)) state.multi[g] = q.get(g).split(',').filter(Boolean);
  });
  ['site','game','game_type','stake','player','stack','combo','action','result','pin',
   'session','fmt','last_sessions','alias','hist_group'].forEach(id => {
    const el = $('#'+id);
    if (el && q.get(id)) el.value = q.get(id);
  });
  state.view = 'study';
}
window.addEventListener('message', ev => {
  const msg = ev.data || {};
  if (msg.kind === 'drill') applyStep(msg.step);
  if (msg.kind === 'pin') pinStep(msg.step);
  if (msg.kind === 'dock') dockReport(msg.payload);
});
if (DETACH_CH){
  DETACH_CH.onmessage = ev => {
    const msg = ev.data || {};
    if (DETACH_PANE && msg.kind === 'study' && msg.data)
      renderStudy(msg.data);
  };
}

(async function start(){
  if (DETACH_PANE) document.body.classList.add('detached');
  OPT = await (await fetch('/api/options')).json();
  $('#site').innerHTML = '<option value="">any site</option>'
    + OPT.sites.map(s=>`<option>${s}</option>`).join('');
  $('#stake').innerHTML = '<option value="">any stake</option>'
    + OPT.stakes.map(s=>`<option value="${s}">$${s}</option>`).join('');
  $('#player').innerHTML = '<option value="">any player</option>'
    + OPT.players.map(s=>`<option>${s}</option>`).join('');
  $('#by').innerHTML = OPT.dimensions.map(d=>
    `<option${d==='position'?' selected':''}>${d}</option>`).join('');
  chips('pos', POSITIONS, 'pos');
  chips('vs', POSITIONS, 'vs');
  chips('street', STREETS, 'street');
  chips('pot', POTS, 'pot');
  chips('facing', FACINGS, 'facing');
  chips('board', OPT.boards, 'board');
  for (const id of ['after','then']){
    $('#'+id).innerHTML = '<option value="">any</option>'
      + ['fold','check','call','bet','raise','squeeze','continue','none','fold-out','3bet']
          .map(v=>`<option>${v}</option>`).join('');
  }
  if (OPT.reports){
    let fam = null, html = '<option value="">no report</option>';
    for (const r of OPT.reports){
      if (r.family !== fam){
        if (fam) html += '</optgroup>';
        fam = r.family;
        html += `<optgroup label="${fam}">`;
      }
      html += `<option>${r.name}</option>`;
    }
    if (fam) html += '</optgroup>';
    $('#preset').innerHTML = html;
    $('#pin').innerHTML = html.replace('>no report<', '>none<');
  }
  $('#sub').textContent = OPT.sites.join(' · ');
  fillProfiles(OPT.profiles || []);
  fillRecent(OPT.stat_reports || []);
  hydrateFromURL();
  paintChips();
  load();
})();

function fillProfiles(items){
  const el = $('#profile'); if (!el) return;
  const cur = el.value || 'default';
  el.innerHTML = (items||[]).map(p =>
    `<option value="${p.id}"${p.id===cur?' selected':''}>${p.name}</option>`
  ).join('');
}
function fillRecent(items){
  const el = $('#recent_stat_reports'); if (!el) return;
  el.innerHTML = '<option value="">recent…</option>'
    + (items||[]).map(r => `<option>${r.name}</option>`).join('');
}
function cohortCompact(pred){
  if (!pred) return '';
  const bits = [];
  for (const pair of (pred.conditions||[])){
    if (pair[0] === '_expr') bits.push(pair[1]);
    else bits.push(pair[0] + pair[1]);
  }
  if (pred.class) bits.push('class='+pred.class);
  if (pred.site) bits.push('site='+pred.site);
  return bits.join(',');
}
function applyHydrate(h){
  if (!h) return;
  ['hero','pool','reg','fish'].forEach(f => { state.flags[f] = false; });
  const argv = h.subject_argv || [];
  for (let i=0;i<argv.length;i++){
    const a = argv[i];
    if (a === '--hero') state.flags.hero = true;
    else if (a === '--pool') state.flags.pool = true;
    else if (a === '--reg') state.flags.reg = true;
    else if (a === '--fish') state.flags.fish = true;
    else if (a === '--player' && argv[i+1]){ $('#player').value = argv[++i]; }
    else if (a === '--site' && argv[i+1]){ $('#site').value = argv[++i]; }
    else if (a === '--game' && argv[i+1]){ $('#game').value = argv[++i]; }
    else if ((a === '--game-type' || a === '--type') && argv[i+1]){
      $('#game_type').value = argv[++i]; }
    else if (a === '--alias' && argv[i+1]){ $('#alias').value = argv[++i]; }
  }
  $('#cohort').value = cohortCompact(h.cohort_predicate);
  if (h.cohort_predicate && h.cohort_predicate.class)
    $('#cohort_class').value = h.cohort_predicate.class;
  $('#fmt').value = h.fmt || 'cash';
  const dr = h.date_range || {};
  $('#since').value = dr.since || '';
  $('#until').value = dr.until || '';
  $('#last_sessions').value = h.last_n_sessions || '';
  const ex = $('#exclude_reg_vs_fish');
  if (ex) ex.checked = !!h.exclude_reg_vs_fish;
  if ($('#profile')) $('#profile').value = h.profile_id || 'default';
  if (h.warnings && h.warnings.length) alert(h.warnings.join('\\n'));
  paintChips();
  state.view = 'statistics';
  document.querySelectorAll('#tabs button').forEach(x =>
    x.classList.toggle('on', x.dataset.v==='statistics'));
  load();
}
const saveBtn = $('#save_stat_report');
if (saveBtn) saveBtn.onclick = ev => {
  ev.preventDefault();
  const name = ($('#stat_report_name').value || '').trim()
    || prompt('Save Statistics report as');
  if (!name) return;
  const p = params();
  p.set('view', 'save-stat-report');
  p.set('name', name);
  fetch('/api?'+p).then(r => r.json()).then(d => {
    if (d.error){ alert(d.error); return; }
    fillRecent(d.recent || []);
    alert(d.n ? ('Saved. '+d.n+' decisions match it today.')
              : 'Saved -- NOTHING MATCHES. Open will recompute empty.');
  });
};
const openBtn = $('#open_stat_report');
if (openBtn) openBtn.onclick = ev => {
  ev.preventDefault();
  const name = ($('#recent_stat_reports').value || '').trim()
    || ($('#stat_report_name').value || '').trim()
    || prompt('Open Statistics report');
  if (!name) return;
  fetch('/api?'+new URLSearchParams({view:'open-stat-report', name}))
    .then(r => r.json()).then(d => {
      if (d.error){ alert(d.error); return; }
      fillRecent(d.recent || []);
      applyHydrate(d.hydrate);
    });
};
const recentSel = $('#recent_stat_reports');
if (recentSel) recentSel.onchange = () => {
  const name = recentSel.value.trim();
  if (!name) return;
  fetch('/api?'+new URLSearchParams({view:'open-stat-report', name}))
    .then(r => r.json()).then(d => {
      if (d.error){ alert(d.error); return; }
      applyHydrate(d.hydrate);
    });
};
""" + query.GRAPH_WIDGET_JS + """
</script>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(PAGE, "text/html; charset=utf-8")
        # One connection per request: sqlite objects belong to the thread
        # that made them, and this server answers on several.
        con = sqlite3.connect(DB)
        try:
            if url.path == "/api/options":
                return self._send(json.dumps(options(con)), "application/json")
            if url.path == "/api":
                try:
                    body = payload(con, parse_qs(url.query))
                except SystemExit as e:
                    body = {"error": str(e)}
                except sqlite3.Error as e:
                    # A raw SQL box invites bad SQL, and the honest answer is
                    # the database's own complaint rather than a blank page.
                    body = {"error": f"SQL: {e}"}
                return self._send(json.dumps(body, default=float),
                                  "application/json")
        finally:
            con.close()
        self.send_error(404)

    def log_message(self, *_a):
        pass          # a request log on every keystroke is not useful here


def check(db_path=DB):
    """
    The page and the command line must build the same filter.

    They are two front ends over one database, and the way that goes wrong is
    not a crash -- it is the page quietly meaning something slightly different
    by "3-bet pots in position" than the command line does, so two answers
    disagree and neither looks wrong. So the check is an equality: a form's
    fields, and the flags they stand for, must produce the same WHERE clause.
    """
    fails = []
    cases = [
        ({"hero": ["1"], "pot": ["3bet"], "street": ["flop"], "ip": ["1"]},
         ["--hero", "--ip", "--pot", "3bet", "--street", "flop"]),
        ({"pool": ["1"], "site": ["acr"], "pos": ["BTN,CO"]},
         ["--pool", "--site", "acr", "--pos", "BTN,CO"]),
        ({"board": ["mono,paired"], "deep": ["100"]},
         ["--deep", "100", "--board", "mono,paired"]),
        ({"pool": ["1"], "vs_pool": ["1"], "pos": ["BTN"], "vs": ["BB"],
          "pot": ["3bet"]},
         ["--pool", "--vs-pool", "--pos", "BTN", "--vs", "BB",
          "--pot", "3bet"]),
        ({"where": ["eff_bb > 150"]}, ["--where", "eff_bb > 150"]),
        ({}, []),
        ({"preset": ["3-bet pots"]}, list(query.SMART_REPORTS["3-bet pots"])),
        ({"first_in": ["1"], "size": ["m"], "outcome": ["fold-out"]},
         ["--first-in", "--size", "m", "--outcome", "fold-out"]),
        ({"first_raise": ["1"], "last_action": ["1"], "stack": ["100+"],
          "pot_frac": ["0.4-0.75"], "flop": ["XBmC"]},
         ["--first-raise", "--last-action", "--size", "0.4-0.75",
          "--stack", "100+", "--flop", "XBmC"]),
        ({"players": ["6"], "live": ["2"]},
         ["--players", "6", "--live", "2"]),
        ({"after": ["none"]}, ["--after", "none"]),
        ({"after": ["3bet"]}, ["--after", "3bet"]),
        ({"after": ["squeeze"]}, ["--after", "squeeze"]),
        ({"cohort": ["vpip>=40,pfr<=10,hands>=100"], "hero": ["1"]},
         ["--cohort", "vpip>=40,pfr<=10,hands>=100", "--hero"]),
        ({"cohort": ["hands>=100"], "cohort_class": ["fish"], "pos": ["BTN"]},
         ["--cohort", "hands>=100", "--class", "fish", "--pos", "BTN"]),
        ({"marked": ["1"], "tag": ["leak"]},
         ["--marked", "--tag", "leak"]),
        ({"cohort": ["Value(3Bet) < 2 and Opps(3Bet) > 100"],
          "pos": ["BTN"]},
         ["--cohort", "Value(3Bet) < 2 and Opps(3Bet) > 100",
          "--pos", "BTN"]),
        ({"villain_type": ["fish"]},
         ["--villain-type", "fish"]),
        ({"action": ["call"], "stack": ["80-120"], "combo": ["Axs"]},
         ["--action", "call", "--stack", "80-120", "--combo", "Axs"]),
        ({"result": ["won"]}, ["--result", "won"]),
        ({"today": ["1"]}, ["--today"]),
        ({"hours": ["4"], "start_of_day": ["6"]},
         ["--hours", "4", "--start-of-day", "6"]),
        ({"session": ["h1"]}, ["--session", "h1"]),
        ({"street": ["flop"], "pfa": ["1"], "facing": ["check"]},
         ["--street", "flop", "--pfa", "--facing", "check"]),
        ({"fmt": ["cash"], "last_sessions": ["10"]},
         ["--fmt", "cash", "--last-sessions", "10"]),
        ({"call_range": ["threebet"]},
         ["--call-range", "threebet"]),
        ({"hist_group": ["air"]},
         ["--hist-group", "air"]),
        ({"game": ["plo"]},
         ["--game", "plo"]),
        ({"game_type": ["plo4-cash"]},
         ["--game-type", "plo4-cash"]),
    ]
    for form, argv in cases:
        spec_a, rest_a = players.parse_cohort(argv_from(form))
        spec_b, rest_b = players.parse_cohort(list(argv))
        a, _label_a, _pa = query.build(rest_a)
        b, _label_b, _pb = query.build(rest_b)
        if spec_a != spec_b:
            fails.append(f"{form} cohort {spec_a!r} but CLI gives {spec_b!r}")
        if sorted(a.split(" AND ")) != sorted(b.split(" AND ")):
            fails.append(f"{form} -> {a!r} but CLI gives {b!r}")
    print(f"page and command line agree  {len(cases) - len(fails)}/{len(cases)}")
    for f in fails:
        print(f"    {f}")

    leaked = argv_from({"exclude_reg_vs_fish": ["1"], "hero": ["1"]})
    if "--exclude-reg-vs-fish" in leaked:
        fails.append("exclude_reg_vs_fish leaked into argv_from -- "
                     "Reports would change with the Statistics toggle")
    print(f"exclude stays off Reports     "
          f"{'yes' if '--exclude-reg-vs-fish' not in leaked else 'NO'}")
    leaked_p = argv_from({"profile": ["preflop"], "hero": ["1"]})
    if "--profile" in leaked_p:
        fails.append("profile leaked into argv_from -- "
                     "a Profile Menu click would become a filter")
    print(f"profile stays off Reports     "
          f"{'yes' if '--profile' not in leaked_p else 'NO'}")

    store = Path(tempfile.mkdtemp()) / "stat_reports.json"
    was = query.STAT_REPORTS_FILE
    query.STAT_REPORTS_FILE = store
    try:
        saved, _n, _d = query.save_stat_report(
            "page-roundtrip",
            argv_from({"hero": ["1"], "fmt": ["cash"],
                       "last_sessions": ["10"]}),
            cohort_spec=([("hands", ">=1")], None, "reg", None),
            exclude=True, profile_id="preflop", path=store,
            db=Path("no-such.db"))
        opened = query.open_stat_report("page-roundtrip", path=store)
        if query.report_identity(saved) != query.report_identity(opened):
            fails.append("page save→open identity drifted")
        ctx = query.hydrate_stat_report(opened)
        if ctx["profile_id"] != "preflop" or not ctx["exclude_reg_vs_fish"]:
            fails.append("page hydrate dropped profile / exclude")
    finally:
        query.STAT_REPORTS_FILE = was
    print(f"Save→Open identity            "
          f"{'yes' if not [f for f in fails if 'save→open' in f or 'hydrate dropped' in f] else 'NO'}")

    parent = argv_from({"stack": ["80-120"]})
    child = query.drill_child(parent, {"flag": "--action", "value": "call"})
    want = query.drill_child(["--stack", "80-120"],
                             {"flag": "--action", "value": "call"})
    if query._canonical(child) != query._canonical(want):
        fails.append("page drill_child drifted from the CLI")
    axs_a, _, _ = query.build(argv_from({"combo": ["Axs"]}))
    axs_b, _, _ = query.build(["--combo", "Axs"])
    if axs_a != axs_b or "A2s" not in axs_a:
        fails.append("page --combo Axs did not expand")
    print(f"study drill matches CLI      "
          f"{'yes' if query._canonical(child) == query._canonical(want) else 'NO'}")

    nest = argv_from({"hero": ["1"], "stack": ["80-120"]})
    ctx = query.report_context(nest)
    title = query.detach_title(ctx, "stack")
    if "hero" not in title or "80-120" not in title or "Stack" not in title:
        fails.append(f"page detach title drifted: {title!r}")
    if query.can_detach(4):
        fails.append("page detach cap drifted from 4")
    this, _other = query.pin_sides(ctx, '["--action","call"]')
    if query._canonical(this) != query._canonical(ctx):
        fails.append("page pin THIS side is not the detach context")
    crumbs = [{"flag": "--stack", "value": "80-120", "label": "80-120"}]
    dock = query.dock_payload(ctx, crumbs)
    if query._canonical(dock["context"]) != query._canonical(ctx):
        fails.append("page dock context drifted from detach")
    if not dock["crumbs"] or dock["crumbs"][0].get("value") != "80-120":
        fails.append("page dock payload dropped the breadcrumb")
    if not query.same_report(dock["context"], nest):
        fails.append("page dock same_report missed the nest")
    print(f"detach title / pin context   "
          f"{'yes' if 'hero' in title and '80-120' in title else 'NO'}")

    # Every view must answer, including on a filter that matches nothing --
    # which a user will type within a minute of being handed a form.
    # Connecting when `hands.db` is missing creates an empty file, and
    # every later `--cohort` / `--pin` then finds that file and fails
    # inside it instead of saying to load hands.
    db = Path(db_path)
    if not db.exists() or db.stat().st_size == 0:
        print()
        print("FAIL: " + "; ".join(fails) if fails else
              "PASS (no hands.db -- form/CLI only)")
        return not fails
    con = sqlite3.connect(db_path)
    views = ("sessions", "study", "statistics", "stats", "report",
             "results", "hands", "graph")
    broke = []
    for v in views:
        for form in ({"view": [v]},
                     {"view": [v], "pos": ["BTN"], "street": ["preflop"],
                      "facing": ["check"]}):
            try:
                got = payload(con, form)
                if "error" in got and form.get("pos"):
                    pass
            except Exception as e:
                broke.append(f"{v}: {type(e).__name__}: {e}")
    print(f"every view answers           {len(views) * 2 - len(broke)}"
          f"/{len(views) * 2}")
    for b in broke:
        print(f"    {b}")
    fails += broke

    try:
        got = payload(con, {"view": ["study"], "hero": ["1"],
                            "stack": ["80-120"]})
        meta = got.get("detach") or {}
        if meta.get("cap") != 4:
            fails.append("study payload dropped detach cap")
        stack_t = (meta.get("titles") or {}).get("stack") or ""
        if "80-120" not in stack_t:
            fails.append(f"study detach title missed the nest: {stack_t!r}")
        if query._canonical(meta.get("context") or []) != \
                query._canonical(["--hero", "--stack", "80-120"]):
            fails.append("study detach context drifted from the form")
        dock = got.get("dock") or {}
        if query._canonical(dock.get("context") or []) != \
                query._canonical(meta.get("context") or []):
            fails.append("study dock context drifted from detach")
        crumb = json.dumps([{"flag": "--stack", "value": "80-120",
                             "label": "80-120"}])
        nested = payload(con, {"view": ["study"], "hero": ["1"],
                               "crumbs": [crumb]})
        nest_dock = nested.get("dock") or {}
        if not (nest_dock.get("crumbs") or []):
            fails.append("study dock payload dropped the crumbs")
        if query._canonical(nest_dock.get("context") or []) != \
                query._canonical(["--hero", "--stack", "80-120"]):
            fails.append("study dock crumbs did not fold the nest")
    except Exception as e:
        fails.append(f"study detach payload: {type(e).__name__}: {e}")

    # The dropdowns must offer things this database actually has, or the
    # first click produces an empty page and looks broken.
    opt = options(con)
    for name in ("sites", "stakes", "players", "boards", "dimensions", "stats",
                 "reports"):
        if not opt[name]:
            fails.append(f"no {name} offered")
    print(f"dropdowns are populated      "
          f"{opt['sites']} · {len(opt['players'])} players · "
          f"{len(opt['stats'])} stats")
    con.close()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    if not DB.exists():
        print(f"no database at {DB} -- load some hands first")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"poker_analysis  ->  {url}")
    print("ctrl-c to stop")

    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
