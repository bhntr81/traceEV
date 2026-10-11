"""
The Bankroll tab: the summary `bankroll.py` prints, drawn, and the ledger
beside it to add to and delete from.

Nothing here works anything out. The worker runs `bankroll.summary` on the
window's one query thread, as every tab's query runs, and this draws the
dict it returns and the lines `bankroll.report` makes of it -- so the tab
and `python bankroll.py` cannot disagree about a number. A row added or
deleted here goes through the same queue, as a write the worker runs between
queries, and the tab asks again when it is done; the interface thread never
touches the database, which the worker may be reading.

A milestone crossed while the window is open -- $300, $600, $1,200, $3,000
in the default plan -- says so once, in a box. Opening the window over a
bankroll already past one says nothing: that is not news.
"""

import queue
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import bankroll


class BankrollTab:
    def __init__(self, app, parent, look, db):
        self.app, self.look, self.db = app, look, db
        self.done = queue.Queue()
        # Writes the worker has finished, so a caller can wait for one.
        self.writes = 0
        self.last_bankroll = None
        self.shown = None

        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(8, 6))
        for text, command in (("Deposit…", lambda: self.money("deposit")),
                              ("Withdrawal…", lambda: self.money("withdrawal")),
                              ("Bonus / rakeback…", lambda: self.money("bonus")),
                              ("Transfer…", self.transfer),
                              ("Session…", self.session),
                              ("The site says…", self.balance),
                              ("Currency…", self.account)):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Delete the selected row",
                   command=self.delete).pack(side="left", padx=(12, 6))
        ttk.Button(bar, text="Import the old tracker…",
                   command=self.import_old).pack(side="right")

        # The plan's bar: the bankroll against the goal, with each milestone
        # marked where it falls.
        self.bar = tk.Canvas(parent, height=look["px"](34), bg=look["bg"],
                             highlightthickness=0)
        self.bar.pack(fill="x", pady=(0, 6))
        self.bar.bind("<Configure>", lambda e: self._draw_bar())

        split = ttk.Panedwindow(parent, orient="horizontal")
        split.pack(fill="both", expand=True)
        left, right = ttk.Frame(split), ttk.Frame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        self.text = tk.Text(left, bg=look["panel"], fg=look["ink"],
                            insertbackground=look["ink"], relief="flat",
                            font=(look["mono"], 10), wrap="none",
                            padx=look["px"](12), pady=look["px"](10))
        # Unwrapped, because the report is columns and a wrapped column is
        # not one; the long lines scroll sideways instead.
        ys = ttk.Scrollbar(left, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(left, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("warn", foreground=look["bad"])
        self.text.tag_configure("dim", foreground=look["dim"])
        self.text.configure(state="disabled")

        cols = ("id", "date", "site", "what", "amount", "note")
        self.tree = ttk.Treeview(right, columns=cols, show="headings",
                                 selectmode="browse")
        widths = {"id": 46, "date": 92, "site": 70, "what": 150, "amount": 84, "note": 220}
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=look["px"](widths[c]),
                             anchor="e" if c == "amount" else "w",
                             stretch=c == "note")
        vs = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("pos", foreground=look["good"])
        self.tree.tag_configure("neg", foreground=look["bad"])
        self.tree.tag_configure("dim", foreground=look["dim"])

    def compute(self):
        """
        What the worker runs for this tab: the summary and its lines.

        On the tab's own database path, which is the window's, so that the
        window's check can point the tab at a copy and add a row to that
        rather than to anybody's real ledger.
        """
        import query
        con = query.connect(self.db)
        try:
            s = bankroll.summary(con)
            return {"summary": s, "lines": bankroll.report(s)}
        finally:
            con.close()

    # ---- drawing --------------------------------------------------------
    def render(self, out):
        if out.get("error"):
            self._say([out["error"]])
            return
        s = out["summary"]
        self.shown = s
        self._say(out["lines"])
        self.tree.delete(*self.tree.get_children())
        for r in s["rows"]:
            tag = ("dim" if "replaced" in r["what"] or r["cents"] is None
                   else "pos" if (r["cents"] or 0) > 0 and "balance" not in r["what"]
                   else "neg" if (r["cents"] or 0) < 0 else "")
            self.tree.insert("", "end", iid=r["id"], tags=(tag,),
                             values=(r["id"], r["at"], r["site"], r["what"],
                                     bankroll.money(r["cents"], r["currency"]),
                                     r["note"]))
        self._draw_bar()
        self._milestone(s)

    def _say(self, lines):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        # The report's explanations are paragraphs of indented lines, and
        # dim to the end of the paragraph, not just its first line.
        dim = False
        for line in lines:
            if not line.startswith("  "):
                dim = False
            elif "A gap" in line or "Over a few" in line:
                dim = True
            tag = ("warn" if "STOP-LOSS" in line or line.startswith("  NOW") else
                   "dim" if dim else "")
            self.text.insert("end", line + "\n", tag)
        self.text.configure(state="disabled")

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        if not self.shown:
            return
        plan, p = self.shown["plan"], self.shown["progress"]
        w, h, pad = c.winfo_width(), c.winfo_height(), self.look["px"](8)
        if w < 40:
            return
        goal = plan["goal"]
        x = lambda v: pad + (w - 2 * pad) * max(0.0, min(1.0, v / goal))
        y0, y1 = h * 0.12, h * 0.48
        c.create_rectangle(pad, y0, w - pad, y1, fill=self.look["field"], width=0)
        c.create_rectangle(pad, y0, x(p["bankroll"]), y1, width=0,
                           fill=self.look["bad"] if p.get("stop_loss", {}).get("fires")
                           else self.look["accent"])
        for m, day in p["milestones"]:
            c.create_line(x(m), y0 - 3, x(m), y1 + 3,
                          fill=self.look["good"] if day else self.look["dim"])
            c.create_text(x(m), y1 + 3, anchor="ne" if m == goal else "n",
                          text=bankroll.money(m, plan["currency"]).split(".")[0],
                          fill=self.look["dim"], font=(self.look["mono"], 8))

    def _milestone(self, s):
        """A box when this window sees the bankroll cross one, and only then."""
        now = s["progress"]["bankroll"]
        before, self.last_bankroll = self.last_bankroll, now
        if before is None:
            return
        crossed = [m for m in s["plan"]["milestones"] if before < m <= now]
        if crossed:
            code = s["plan"]["currency"]
            messagebox.showinfo(
                "milestone",
                f"Your bankroll passed {bankroll.money(crossed[-1], code)}: "
                f"{bankroll.money(now, code)} on {', '.join(s['progress']['sites'])}.",
                parent=self.tree)

    # ---- writing --------------------------------------------------------
    def _write(self, action, *args, **kwargs):
        """
        Run a write on the worker and ask again when it is done.

        The worker takes callables between queries; the answer comes back
        through `done`, which this polls, because a write's error belongs in
        a box on this thread and not in the worker's log.
        """
        def run():
            con = sqlite3.connect(self.db)
            try:
                self.done.put((True, action(con, *args, **kwargs)))
            except (ValueError, OSError, sqlite3.Error) as e:
                self.done.put((False, str(e)))
            finally:
                con.close()
        self.app.requests.put(run)
        self.tree.after(80, self._written)

    def _written(self):
        try:
            ok, said = self.done.get_nowait()
        except queue.Empty:
            self.tree.after(80, self._written)
            return
        self.writes += 1
        if not ok:
            messagebox.showerror("not saved", said, parent=self.tree)
            return
        if isinstance(said, str) and said:
            messagebox.showinfo("done", said, parent=self.tree)
        for key in [k for k in self.app.cache if k[0] == "bankroll"]:
            del self.app.cache[key]
        self.app.refresh()

    def _form(self, title, fields, then):
        """A small box of labelled entries; `then` gets the values."""
        win = tk.Toplevel(self.tree)
        win.title(title)
        win.configure(bg=self.look["bg"])
        win.transient(self.tree.winfo_toplevel())
        boxes = {}
        sites = sorted(set(bankroll.site_key(s) for s in (self.shown or {}).get("accounts", {})))
        for i, (key, label, default) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w",
                                            padx=10, pady=4)
            if key in ("site", "to") and sites:
                box = ttk.Combobox(win, values=sites, width=24)
            else:
                box = ttk.Entry(win, width=26)
            box.insert(0, default)
            box.grid(row=i, column=1, padx=10, pady=4)
            boxes[key] = box

        def go(_e=None):
            values = {k: b.get().strip() for k, b in boxes.items()}
            win.destroy()
            then(values)
        ttk.Button(win, text="Save", command=go).grid(
            row=len(fields), column=1, sticky="e", padx=10, pady=8)
        win.bind("<Return>", go)
        win.bind("<Escape>", lambda e: win.destroy())
        next(iter(boxes.values())).focus_set()
        return win

    def money(self, kind):
        kinds = ("deposit", "withdrawal", "bonus", "rakeback")
        self._form(f"{kind}", [("site", "site", ""), ("kind", "kind (" + ", ".join(kinds) + ")", kind),
                               ("amount", "amount", ""), ("date", "date", bankroll.when("today")),
                               ("note", "note", "")],
                   lambda v: self._write(bankroll.add_entry, v["site"], v["kind"],
                                         v["amount"], v["date"], v["note"]))

    def transfer(self):
        self._form("transfer", [("site", "from", ""), ("to", "to", ""),
                                ("amount", "amount sent", ""),
                                ("arrived", "amount arrived (another currency only)", ""),
                                ("date", "date", bankroll.when("today")), ("note", "note", "")],
                   lambda v: self._write(bankroll.transfer, v["site"], v["to"], v["amount"],
                                         v["date"], v["arrived"] or None, v["note"]))

    def session(self):
        self._form("a session with no hand history",
                   [("site", "site", ""), ("result", "result (+ or -)", ""),
                    ("date", "date (and time, if you know it)", bankroll.when("today")),
                    ("hours", "hours", ""), ("hands", "hands", ""),
                    ("stake", "stake, e.g. 0.05/0.10 or 0.05/0.10/0.20", ""),
                    ("note", "note", "")],
                   lambda v: self._write(bankroll.add_session, v["site"], v["result"],
                                         v["date"], v["hours"] or None, v["hands"] or None,
                                         v["stake"], v["note"]))

    def balance(self):
        self._form("what the site says you have",
                   [("site", "site", ""), ("amount", "balance", ""),
                    ("date", "date", bankroll.when("today")), ("note", "note", "")],
                   lambda v: self._write(bankroll.add_balance, v["site"], v["amount"],
                                         v["date"], v["note"]))

    def account(self):
        self._form("which currency a site keeps",
                   [("site", "site", ""), ("currency", "currency", "USD")],
                   lambda v: self._write(lambda con, site, cur: "{} keeps {}".format(
                       *bankroll.set_account(con, site, cur)), v["site"], v["currency"]))

    def delete(self):
        chosen = self.tree.selection()
        if not chosen:
            messagebox.showinfo("delete", "Pick a row in the ledger first.", parent=self.tree)
            return
        ref = chosen[0]
        values = self.tree.item(ref, "values")
        if messagebox.askyesno("delete", f"Delete {ref}: {values[3]} {values[4]} "
                               f"on {values[1]}? Nothing else changes.", parent=self.tree):
            self._write(bankroll.delete, ref)

    def import_old(self):
        path = filedialog.askopenfilename(
            title="your old bankroll_tracker.db, or a Date,Bankroll CSV",
            filetypes=[("old tracker", "*.db *.sqlite *.sqlite3"), ("Date,Bankroll CSV", "*.csv"),
                       ("anything", "*.*")], parent=self.tree)
        if not path:
            return

        def then(v):
            def run(con):
                if path.lower().endswith(".csv"):
                    n, others = bankroll.import_csv(con, path, v["site"], v["currency"]), []
                else:
                    n, others = bankroll.import_old(con, path, v["site"], v["currency"])
                return (f"Imported {n} days, each a balance check and a typed session."
                        + "".join(f"\nNot a list of days, so not imported: {t} ({k} rows)"
                                  for t, k in others))
            self._write(run)
        self._form("which room were these days played on?",
                   [("site", "site", "clubwpt"), ("currency", "currency", "USD")], then)


def check_tab(app, db):
    """
    The tab draws what the command line prints, and a row added through it
    arrives through the worker and is drawn. Run by `app.py --check`.

    On a copy of the window's database: a check that added a deposit to the
    real ledger and took it out again would be one crash away from leaving
    it there.
    """
    import shutil
    import tempfile
    from pathlib import Path
    fails = []
    tab = app.bankroll
    real = tab.db
    tmp = tempfile.mkdtemp()
    copy = Path(tmp) / "hands.db"
    try:
        if Path(db).exists():
            src, dst = sqlite3.connect(db), sqlite3.connect(copy)
            src.backup(dst)
            src.close()
            dst.close()
        tab.db = copy
        for key in [k for k in app.cache if k[0] == "bankroll"]:
            del app.cache[key]
        tab.shown = None
        app.nb.select(app.tabs["bankroll"])
        app.refresh()
        _wait(app, lambda: tab.shown is not None)
        if tab.shown is None:
            return ["the bankroll tab drew nothing"]
        con = sqlite3.connect(copy)
        lines = bankroll.report(bankroll.summary(con))
        con.close()
        drawn = tab.text.get("1.0", "end").rstrip("\n").split("\n")
        if drawn != lines:
            fails.append("the bankroll tab says something the command line does not")
        before = len(tab.tree.get_children())
        done = tab.writes
        tab._write(bankroll.set_account, "checkroom", "USD")
        _wait(app, lambda: tab.writes > done)
        tab._write(bankroll.add_entry, "checkroom", "deposit", "1.23", "2026-10-11",
                   "app.py --check")
        _wait(app, lambda: len(tab.tree.get_children()) > before)
        rows = tab.tree.get_children()
        if len(rows) != before + 1 or "1.23" not in str(tab.tree.item(rows[0], "values")):
            fails.append("a deposit added through the tab did not arrive in its ledger")
        con = sqlite3.connect(real) if Path(real).exists() else None
        if con is not None:
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
            if "bankroll_entries" in have and con.execute(
                    "SELECT COUNT(*) FROM bankroll_entries WHERE note='app.py --check'"
            ).fetchone()[0]:
                fails.append("the check wrote into the real ledger")
            con.close()
    finally:
        tab.db = real
        for key in [k for k in app.cache if k[0] == "bankroll"]:
            del app.cache[key]
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def _wait(app, ready, tries=400):
    for _ in range(tries):
        app.update()
        if ready():
            return True
        app.after(20)
    return False
