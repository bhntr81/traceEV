"""
The tracker as a desktop window: no browser, no server, no terminal.

`gui.py` served the same thing as a local page, and it worked, but a page is
not an application -- it has no window of its own, it needs a terminal left
running behind it, and it arrives in a tab beside everything else. This is
the same tool as a program you open.

Tk rather than anything better looking, because the alternative is a
dependency and this project has none. Tk fights being made dark: its default
Windows themes hand their drawing to native controls that ignore colour
settings, so the whole interface is built on `clam`, the one bundled theme
that draws its own widgets and therefore does as it is told.

**The filter is built by `query.build`, from the flags the command line
takes.** Three front ends now share one definition of what "3-bet pots in
position" means. They would not stay agreeing if any of them assembled its
own WHERE clause, and the disagreement would be silent -- so `--check`
asserts the equality rather than trusting it.

    python app.py             open it
    python app.py --no-update start without looking at github or the disk
    python app.py --debug     also print the log to the terminal
    python app.py --check   the window and the command line agree

Anything that goes wrong is written to `poker_analysis.log` beside the
program, including the failures Tk would otherwise swallow. `python diag.py`
prints the end of it.

Hands come in through the Import menu -- a folder, a file, another database,
or whatever it can find on this computer. Nothing there asks which site the
hands are from; every file is identified by reading it.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

import sqlite3

import compact
import diag
import importer
import notes
import players
import sessions
import sites
import query
import stats
import update
from stats import BY_KEY, STATS

# Packaged as a single executable, PyInstaller unpacks the code into a
# temporary directory and deletes it afterwards, so `__file__` points
# somewhere that will not exist tomorrow. The database is not part of the
# program -- it is the user's data -- and lives beside the executable.
HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)
DB = HERE / "hands.db"


def connect_db(path=None, **kw):
    """The corpus, or memory when there is none.

    sqlite3.connect(path) creates an empty file. `--check` used to
    leave that behind, and every later module then failed inside it
    instead of skipping a missing corpus.
    """
    path = Path(path or DB)
    if path.exists() and path.stat().st_size:
        return sqlite3.connect(str(path), **kw)
    return sqlite3.connect(":memory:", **kw)


query.DB = DB
# The saved stats are the user's as much as the database is, so they sit
# beside it rather than beside the code -- which, frozen, is a directory
# PyInstaller deletes on the way out. Loading them again here is what puts
# them into the registry every view already reads: the copy loaded when
# `stats` was imported looked next to the code and found nothing there.
stats.DB = DB
stats.load_custom(HERE / "stats.json")
query.SAVED = HERE / "filters.json"

# One palette, so a colour is changed in one place. The four win-graph
# colours live on `query.LINES` -- the official H2N chart -- and both
# surfaces read them from there.
BG, PANEL, EDGE = "#14161a", "#1b1e24", "#2a2f38"
INK, DIM, ACCENT = "#d8dbe0", "#8b929c", "#4c9aff"
GOOD, BAD, WARN = "#22a35a", "#d1443c", "#b8892a"
LINE = dict(query.LINE_COLOUR)
DRAW_ORDER = query.DRAW_ORDER

def blend(a, b, t):
    """
    A colour t of the way from a to b, both written "#rrggbb".

    The range chart needs a hundred and sixty-nine shades of one colour and
    a palette cannot hold them, so they are mixed. Clamped, because a weight
    computed from a ratio arrives slightly over 1.0 often enough to matter
    and an out-of-range colour is a TclError rather than a wrong shade.
    """
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}"
                         for x, y in zip(pa, pb))


# The fonts a machine actually has. "Segoe UI" and "Consolas" ship with
# Windows and with nothing else; asking Tk for a font that is not installed
# does not fail, it silently substitutes, and what it substitutes on Linux
# is a bitmap face from the eighties. Chosen once, from what the system
# reports, so the same window is legible on all three.
UI, MONO = "Segoe UI", "Consolas"

# The first entry in the player list. Hero has a screen name on ACR and a
# different session-scoped one on Ignition, so picking a name picks one
# site's worth of your own hands and silently drops the rest -- 7,715 of
# about 11,000. This selects all of them, on every site.
HERO_CHOICE = "me — every site"

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
STREETS = ("preflop", "flop", "turn", "river")
POT_TYPES = ("unopened", "limped", "raised", "3bet", "4bet")
# What the hand became. Ordered as they beat each other, because a list of
# them sorted alphabetically is a list nobody can read down.
MADE = ("high card", "board pair", "weak pair", "under pair", "middle pair",
        "top pair", "overpair", "two pair", "trips", "set", "straight",
        "flush", "boat", "quads", "straight flush")
KICKERS = ("top", "good", "weak")
FLUSH_DRAWS = ("nut", "second", "weak", "backdoor")
STRAIGHT_DRAWS = ("oesd", "double gutshot", "gutshot")
# Who the other seat is. "The big blind against a button open" is the shape
# most real questions have, and it needs both halves of the matchup named.
VS_SIDE = [("--vs-hero", "vs me"), ("--vs-pool", "vs the pool")]
# What kind of player, on each side of the matchup. A class is only given to
# somebody there is enough evidence about; everybody else is "unknown" and
# is selected by neither of these, which is the point of them.
WHO = [("--reg", "the player is a reg"), ("--fish", "the player is a fish"),
       ("--vs-reg", "against a reg"), ("--vs-fish", "against a fish"),
       ("--regs-only", "everyone left is a reg"),
       ("--with-fish", "a fish is in the pot")]
SITUATIONS = [("--ip", "in position"), ("--oop", "out of position"),
              ("--pfa", "was the raiser"), ("--not-pfa", "was not the raiser"),
              ("--vs-pfa", "facing the raiser"),
              ("--first-in", "first in on this street"),
              ("--first-raise", "first raise on this street"),
              ("--last-raise", "already raised on the street"),
              ("--last-action", "last action on this street"),
              ("--multiway", "multiway"), ("--headsup", "heads up"),
              ("--allin", "all-in")]
# Turning one of these on turns its opposite off, or the filter selects
# nothing and looks broken rather than contradictory.
OPPOSITES = {"--hero": "--pool", "--pool": "--hero", "--ip": "--oop",
             "--oop": "--ip", "--multiway": "--headsup",
             "--headsup": "--multiway",
             "--pfa": "--not-pfa", "--not-pfa": "--pfa",
             "--reg": "--fish", "--fish": "--reg",
             "--vs-reg": "--vs-fish", "--vs-fish": "--vs-reg",
             "--vs-hero": "--vs-pool", "--vs-pool": "--vs-hero"}


def pick_fonts():
    """
    The best font on this machine, from the ones it says it has.

    Needs a root window: Tk cannot be asked what fonts exist before it has
    one. `TkDefaultFont` and `TkFixedFont` are the last resort and are the
    only two names guaranteed to resolve to something on every platform.
    """
    global UI, MONO
    have = set(tkfont.families())
    UI = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue",
                           "Ubuntu", "Cantarell", "DejaVu Sans", "Arial")
               if f in have), "TkDefaultFont")
    MONO = next((f for f in ("Consolas", "SF Mono", "Menlo",
                             "DejaVu Sans Mono", "Ubuntu Mono", "Courier New")
                 if f in have), "TkFixedFont")
    return UI, MONO


def open_folder(path):
    """
    Show a folder in whatever file browser this machine has.

    `os.startfile` exists only on Windows -- on a Mac or a Linux box the Help
    menu would raise AttributeError, and in a windowed build that failure is
    invisible, which is precisely the class of bug `diag` was written for.
    """
    if sys.platform == "win32":
        os.startfile(path)                                  # noqa: S606
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(path)], check=False)


def dark_titlebar(window):
    """
    The frame around the window, dark as well.

    Windows draws the title bar itself and ignores everything Tk says about
    colour, so a carefully dark window arrives wearing a white hat. One DWM
    attribute fixes it. The call does nothing on other platforms and nothing
    on Windows builds too old to know the attribute, which is why it is not
    guarded by a version test -- and it is wrapped, because a light title
    bar is not worth a crash.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        on = ctypes.c_int(1)
        for attribute in (20, 19):      # 20 since Windows 10 2004, 19 before
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on))
        # Windows paints the frame once and does not repaint it because an
        # attribute changed, so the bar stays white until something makes it
        # redraw. Hiding and showing the window is what does that.
        window.withdraw()
        window.deiconify()
    except Exception:
        pass


def dark(root):
    """Make Tk dark, which it does not want to be."""
    pick_fonts()
    style = ttk.Style(root)
    # `clam` draws its own widgets. `vista` and `winnative` delegate to the
    # operating system, which draws them light whatever it is told.
    style.theme_use("clam")
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=INK, fieldbackground=PANEL,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                    troughcolor=BG, focuscolor=ACCENT, insertcolor=INK)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("Dim.TLabel", background=BG, foreground=DIM)
    style.configure("Head.TLabel", background=BG, foreground=DIM,
                    font=(UI, 8, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=INK,
                    font=(UI, 11, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=DIM)
    style.map("TCheckbutton",
              foreground=[("selected", ACCENT), ("active", INK)],
              background=[("active", BG)])
    style.configure("TButton", background=PANEL, foreground=INK,
                    bordercolor=EDGE, focusthickness=0, padding=(10, 4))
    style.map("TButton", background=[("active", EDGE)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#08111f",
                    bordercolor=ACCENT, padding=(14, 5))
    style.map("Accent.TButton", background=[("active", "#5ea6ff")])
    style.configure("Big.TNotebook.Tab", padding=(20, 9),
                    font=(UI, 10))
    style.configure("TEntry", fieldbackground=PANEL, foreground=INK,
                    bordercolor=EDGE, insertcolor=INK)
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    foreground=INK, arrowcolor=DIM, bordercolor=EDGE)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", INK)])
    # The dropdown LIST inside a combobox is a Tk listbox, not a ttk widget,
    # so ttk styling never reaches it and it has to be coloured by option.
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    style.configure("TNotebook", background=BG, bordercolor=EDGE)
    style.configure("TNotebook.Tab", background=BG, foreground=DIM,
                    padding=(14, 6), bordercolor=EDGE)
    style.map("TNotebook.Tab", background=[("selected", PANEL)],
              foreground=[("selected", INK)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, bordercolor=EDGE, rowheight=22)
    style.configure("Treeview.Heading", background=BG, foreground=DIM,
                    relief="flat", font=(UI, 8, "bold"))
    style.map("Treeview.Heading", background=[("active", EDGE)])
    style.map("Treeview", background=[("selected", "#233047")],
              foreground=[("selected", INK)])
    style.configure("TSeparator", background=EDGE)
    style.configure("Vertical.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    style.configure("Horizontal.TScrollbar", background=PANEL,
                    troughcolor=BG, bordercolor=BG, arrowcolor=DIM)
    return style


def _coincident(series, slack=0.5):
    """Which lines sit on top of which. The rule lives on query.coincident."""
    return query.coincident(series, slack=slack)


class WinGraph:
    """
    The official H2N four-line win chart.

    One widget, used by the Graph tab, the Reports study strip, and
    Sessions detail. Legend clicks hide a line; bb / $ switch units
    without asking the database again; a hover names the hand under
    the pointer. The series comes from `query.graph_of`.
    """

    def __init__(self, parent, unit_var=None, height=None, compact=False,
                 on_gear=None):
        self.unit = unit_var or tk.StringVar(value="bb")
        self.hidden = set()
        self.got = None
        self.message = None
        self.compact = compact
        self.frame = ttk.Frame(parent)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="win graph", style="Dim.TLabel").pack(side="left")
        ttk.Radiobutton(bar, text="bb", value="bb", variable=self.unit,
                        command=self.draw).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(bar, text="$", value="currency", variable=self.unit,
                        command=self.draw).pack(side="left")
        ttk.Label(bar, text="rising red ≈ bluffy · falling ≈ passive",
                  style="Dim.TLabel").pack(side="left", padx=12)
        self.leg_vars = {}
        for key, _c, why in query.LINES:
            var = tk.BooleanVar(value=True)
            self.leg_vars[key] = var
            ttk.Checkbutton(bar, text=why, variable=var,
                            command=self._toggle).pack(side="left", padx=(8, 0))
        if on_gear:
            ttk.Button(bar, text="⚙", width=3, command=on_gear).pack(
                side="right")
        self.tip = ttk.Label(self.frame, text="", style="Dim.TLabel")
        self.tip.pack(anchor="w", padx=8)
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0,
                                height=height or (180 if compact else 1))
        self.canvas.pack(fill="both", expand=not compact)
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _e: self.tip.configure(text=""))

    def pack(self, **kw):
        self.frame.pack(**kw)

    def _toggle(self):
        self.hidden = {k for k, v in self.leg_vars.items() if not v.get()}
        self.draw()

    def set(self, got, message=None):
        self.got = got if got and not got.get("why") and got.get("n", 0) >= 2 else None
        self.message = message or (got or {}).get("why")
        self.draw()

    def _series(self):
        if not self.got:
            return {}
        unit = query.graph_unit(self.unit.get())
        return (self.got.get("units") or {}).get(unit) or self.got.get("series") or {}

    def draw(self, message=None):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s = self._series()
        keys = [k for k in query.GRAPH_KEYS if k in s and k not in self.hidden]
        n = max((len(s[k]) for k in keys), default=0)
        if not s or n < 2:
            c.create_text(w / 2, h / 2, fill=DIM, font=(UI, 10),
                          text=message or self.message
                          or "not enough hands to draw a line")
            return
        L, R, T, B = 56, 16, 16, 28
        vals = [v for k in keys for v in s[k]]
        lo, hi = min(vals + [0.0]), max(vals + [0.0])
        span = (hi - lo) or 1.0
        x = lambda i: L + (w - L - R) * i / max(1, n - 1)
        y = lambda v: T + (h - T - B) * (1 - (v - lo) / span)
        for f in range(5):
            v = lo + span * f / 4
            c.create_line(L, y(v), w - R, y(v), fill=EDGE)
            c.create_text(L - 8, y(v), text=f"{v:,.0f}", fill=DIM,
                          anchor="e", font=(UI, 8))
        c.create_line(L, y(0), w - R, y(0), fill=DIM, dash=(3, 3))
        for key in DRAW_ORDER:
            if key not in keys:
                continue
            pts = []
            for j, v in enumerate(s[key]):
                pts += [x(j), y(v)]
            if len(pts) >= 4:
                c.create_line(*pts, fill=LINE[key], width=2, smooth=False,
                              tags=("line", key))
        covered = _coincident({k: s[k] for k in keys})
        unit = "bb" if query.graph_unit(self.unit.get()) == "bb" else "$"
        bits = []
        for key, _c, why in query.LINES:
            if key in self.hidden or key not in s:
                continue
            end = s[key][-1]
            note = covered.get(key)
            bits.append(f"{why} {end:+,.0f}{unit}"
                        + (f" (under {query.LINE_LABEL[note]})" if note else ""))
        note = (self.got or {}).get("note") or ""
        c.create_text((L + w - R) / 2, h - 10, fill=DIM, font=(UI, 8),
                      text=note + (("  ·  " + "  ·  ".join(bits)) if bits else ""))
        self._xy = (x, n, L, w - R)

    def _hover(self, event):
        if not self.got or not getattr(self, "_xy", None):
            return
        x, n, left, right = self._xy
        if n < 2 or right <= left:
            return
        t = (event.x - left) / (right - left)
        i = max(0, min(n - 1, int(round(t * (n - 1)))))
        unit = query.graph_unit(self.unit.get())
        pts = (self.got.get("points_by_unit") or {}).get(unit) or []
        s = self._series()
        p = pts[i] if i < len(pts) else {}
        bits = []
        for key, _c, why in query.LINES:
            if key in self.hidden:
                continue
            vals = s.get(key) or []
            if i < len(vals):
                bits.append(f"{why} {vals[i]:+,.1f}")
        hid = p.get("hand_id") or ""
        when = (p.get("when") or "")[:16]
        self.tip.configure(
            text=f"#{i + 1}  {hid}  {when}   " + "   ".join(bits))


class HistWidget:
    """
    Postflop hand-value histogram + Weak %.

    One widget, used by Statistics and the Reports study strip.
    Left-click a bar ANDs `--hist-group` onto the filter the same
    way a pane row does. Right-click flips that bar's is_weak and
    recomputes Weak % from the counts already on screen -- a group
    editor is deferred, but the percentage has to move.
    """

    def __init__(self, parent, on_click=None, height=150, on_gear=None):
        self.on_click = on_click
        self.got = None
        self.message = None
        self.bars = []
        self.frame = ttk.Frame(parent)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="hand values", style="Dim.TLabel").pack(
            side="left")
        self.weak_lab = ttk.Label(bar, text="Weak  –", style="Dim.TLabel")
        self.weak_lab.pack(side="left", padx=(12, 0))
        ttk.Label(bar, text="click a bar to filter · right-click flips weak",
                  style="Dim.TLabel").pack(side="left", padx=12)
        if on_gear:
            ttk.Button(bar, text="⚙", width=3, command=on_gear).pack(
                side="right")
        self.tip = ttk.Label(self.frame, text="", style="Dim.TLabel")
        self.tip.pack(anchor="w", padx=8)
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0,
                                height=height)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _e: self.tip.configure(text=""))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", self._flip)

    def pack(self, **kw):
        self.frame.pack(**kw)

    def set(self, got, message=None):
        self.got = got if got and got.get("n") else None
        self.message = message or (got or {}).get("why") or (
            "no hand under this filter was ever shown, so there is "
            "no histogram to draw")
        if self.got:
            # A local copy so flipping is_weak does not mutate the
            # payload the next refresh would redraw from.
            self.got = dict(self.got)
            self.got["rows"] = [dict(r) for r in self.got.get("rows") or []]
        self.draw()

    def draw(self, _message=None):
        c = self.canvas
        c.delete("all")
        self.bars = []
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 40:
            return
        if not self.got:
            c.create_text(w / 2, h / 2, fill=DIM, font=(UI, 10), width=w - 40,
                          justify="center", text=self.message)
            self.weak_lab.configure(text="Weak  –")
            return
        rows = [r for r in self.got["rows"]
                if r.get("n") or r.get("key") == "other"]
        seen = self.got.get("n") or 0
        weak = query.weak_pct_of(self.got["rows"], seen)
        weak_n = sum(r["n"] for r in self.got["rows"] if r.get("is_weak"))
        labels = [r["label"] for r in self.got["rows"] if r.get("is_weak")]
        self.weak_lab.configure(
            text=f"Weak  {weak:.1f}%  ({weak_n:,} of {seen:,})"
            if seen else "Weak  –")
        tagged = ", ".join(labels) or "none"
        cov = self.got.get("coverage") or {}
        site_bits = []
        for s in cov.get("sites") or []:
            site_bits.append(
                f"{s.get('site') or '?'}: {s['seen']:,}/{s['total']:,} "
                f"({s['pct']:.0f}%)")
        self.tip.configure(
            text=f"tagged weak: {tagged}"
                 + (("  ·  " + "  ·  ".join(site_bits)) if site_bits else ""))
        L, R, T, B = 8, 8, 8, 28
        n = max(1, len(rows))
        gap = 4
        bw = max(8, (w - L - R - gap * (n - 1)) / n)
        peak = max((r.get("pct") or 0) for r in rows) or 1.0
        inner = h - T - B
        for i, r in enumerate(rows):
            x0 = L + i * (bw + gap)
            x1 = x0 + bw
            pct = r.get("pct") or 0.0
            bh = inner * (pct / peak)
            y1 = h - B
            y0 = y1 - bh
            fill = BAD if r.get("is_weak") else (
                DIM if r.get("key") == "other" else ACCENT)
            c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=BG,
                               tags=("bar", r["key"]))
            c.create_text((x0 + x1) / 2, h - 12, text=r.get("label") or "",
                          fill=DIM, font=(UI, 7), angle=0,
                          width=max(10, bw))
            self.bars.append((x0, y0, x1, y1, r))

    def _at(self, event):
        for x0, y0, x1, y1, r in self.bars:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                return r
        return None

    def _hover(self, event):
        r = self._at(event)
        if not r:
            return
        kind = "weak" if r.get("is_weak") else "strong"
        self.tip.configure(
            text=f"{r['label']}  {r['pct']:.1f}%  n={r['n']:,}  {kind}  "
                 f"— click filters · right-click flips weak")

    def _click(self, event):
        r = self._at(event)
        if r and self.on_click:
            self.on_click(r)

    def _flip(self, event):
        r = self._at(event)
        if not r or r.get("key") == "other":
            return
        r["is_weak"] = not r.get("is_weak")
        self.draw()


class Progress(tk.Toplevel):
    """
    A window that says what the import is doing while it does it.

    Loading a season of hand histories takes minutes and rebuilding the
    derived tables takes minutes more. Without this the application simply
    stops responding, and the only available conclusion is that it has
    crashed.
    """

    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.configure(background=BG)
        self.geometry("560x300")
        self.transient(master)
        self.text = tk.Text(self, background=BG, foreground=INK, borderwidth=0,
                            font=(MONO, 10), padx=14, pady=12,
                            insertbackground=BG)
        self.text.pack(fill="both", expand=True)
        self.close = ttk.Button(self, text="close", command=self.destroy,
                                state="disabled")
        self.close.pack(pady=(0, 10))
        self.protocol("WM_DELETE_WINDOW", lambda: None)

    def say(self, line):
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.update_idletasks()

    def done(self):
        self.close.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)


class ImportMixin:
    """Everything the Import menu does. Kept apart because none of it is UI."""

    def _menu(self, root):
        bar = tk.Menu(root, background=PANEL, foreground=INK,
                      activebackground=ACCENT, activeforeground=BG,
                      borderwidth=0)
        m = tk.Menu(bar, tearoff=0, background=PANEL, foreground=INK,
                    activebackground=ACCENT, activeforeground=BG)
        m.add_command(label="Import new hands", command=self.import_new)
        m.add_command(label="Find hands on this computer…",
                      command=self.import_autodetect)
        m.add_separator()
        m.add_command(label="Import a folder…", command=self.import_folder)
        m.add_command(label="Import a file…", command=self.import_file)
        m.add_command(label="Merge another database…", command=self.import_db)
        bar.add_cascade(label="Import", menu=m)

        u = tk.Menu(bar, tearoff=0, background=PANEL, foreground=INK,
                    activebackground=ACCENT, activeforeground=BG)
        u.add_command(label="Update from GitHub now", command=self.update_now)
        u.add_command(label="What version is this?", command=self.show_version)
        bar.add_cascade(label="Update", menu=u)

        h = tk.Menu(bar, tearoff=0, background=PANEL, foreground=INK,
                    activebackground=ACCENT, activeforeground=BG)
        h.add_command(label="Show the log…", command=self.show_log)
        h.add_command(label="Open the log folder",
                      command=lambda: open_folder(diag.LOG.parent))
        bar.add_cascade(label="Help", menu=h)
        root.configure(menu=bar)

    def update_now(self):
        """
        Pull, and say plainly what happened.

        On a worker, because git talks to the network and a menu command
        that freezes the window for twenty seconds looks like a crash. The
        answer arrives in a box rather than the banner, because this one was
        asked for and an answer that has to be hunted for is not an answer.
        """
        def work():
            try:
                state, message = update.update()
            except Exception:
                diag._report("update from the menu", *sys.exc_info()[1:])
                state, message = "skipped", "the update failed -- see the log"
            diag.event("update requested", state=state, detail=message)
            self.after(0, lambda: messagebox.showinfo(
                {"updated": "Updated", "current": "Already up to date",
                 "blocked": "Not updated", "available": "A newer version exists",
                 "skipped": "Could not check"}[state], message))
        threading.Thread(target=work, daemon=True).start()

    def show_version(self):
        where = "a packaged build" if getattr(sys, "frozen", False) else "source"
        messagebox.showinfo(
            "poker_analysis",
            f"Running from {where}.\n\n"
            f"Commit: {update.head() or 'unknown'}\n"
            f"Repository: {update.remote_repo()}\n\n"
            f"The database is at:\n{DB}")

    def show_log(self):
        win = tk.Toplevel(self.master)
        win.title("poker_analysis.log")
        win.configure(background=BG)
        win.geometry("900x560")
        text = tk.Text(win, background=BG, foreground=INK, borderwidth=0,
                       font=(MONO, 9), padx=12, pady=10, wrap="none")
        bar = ttk.Scrollbar(win, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        text.insert("end", f"{diag.LOG}\n\n{diag.tail(400)}")
        text.see("end")

    def _run_import(self, title, work):
        """
        Every import runs on a thread, reporting into a window.

        The rebuild afterwards is not optional and is why it is here rather
        than left to the caller: loading writes to `hands`, `seats` and
        `actions`, and every question this program answers is asked of the
        tables derived from them. An import without the rebuild looks like an
        import that did nothing.
        """
        win = Progress(self.master, title)
        lines = queue.Queue()

        def go():
            try:
                got = work(lines.put)
                lines.put(("done", got))
            except Exception as e:
                lines.put(("failed", f"{type(e).__name__}: {e}"))

        threading.Thread(target=go, daemon=True).start()

        def pump():
            try:
                while True:
                    item = lines.get_nowait()
                    if isinstance(item, tuple):
                        kind, payload = item
                        if kind == "failed":
                            win.say("")
                            win.say(payload)
                        else:
                            win.say("")
                            win.say(str(payload))
                            self.con = sqlite3.connect(DB, check_same_thread=False)
                            self.load_options()
                            self.refresh()
                        win.done()
                        return
                    win.say(item)
            except queue.Empty:
                pass
            win.after(120, pump)
        win.after(120, pump)

    def import_autodetect(self):
        found = importer.scan()
        if not found:
            messagebox.showinfo(
                "nothing found",
                "No hand histories in the usual places.\n\n"
                "Use Import a folder… and point it at wherever your site "
                "writes them.")
            return
        summary = "\n".join(importer.describe(p) for p in found)
        if not messagebox.askyesno(
                "found these", summary + "\n\nImport all of them?"):
            return
        paths = [p["path"] for p in found]
        self._run_import("importing", lambda say: self._do_load(paths, say))

    def import_new(self):
        """
        Load whatever has appeared in the usual places since last time.

        No dialog and no folder to choose, because the answer to both is
        always the same: this is the thing you want after a session, and
        being asked to confirm the same three folders every time is how a
        one-click action becomes a three-click one. `importer.refresh`
        already skips a hand that is in the database and already leaves the
        derived tables alone when nothing was added.
        """
        self._run_import("importing new hands", self._do_refresh)

    @staticmethod
    def _do_refresh(say):
        say("looking for new hands…")
        got = importer.refresh(DB, progress=say)
        if not got["places"]:
            return ("no hand histories in the usual places -- try "
                    "Import a folder…")
        if not got["added"]:
            return (f"nothing new in {got['files']} files "
                    f"({got['known']} hands already known)")
        return f"{got['added']} hands added, and the tables rebuilt"

    def import_folder(self):
        folder = filedialog.askdirectory(title="folder of hand histories")
        if folder:
            self._run_import("importing",
                             lambda say: self._do_load([folder], say))

    def import_file(self):
        files = filedialog.askopenfilenames(
            title="hand history files",
            filetypes=[("hand histories", "*.txt"), ("all files", "*.*")])
        if files:
            self._run_import("importing",
                             lambda say: self._do_load(list(files), say))

    def import_db(self):
        other = filedialog.askopenfilename(
            title="another hands.db", filetypes=[("database", "*.db")])
        if not other:
            return

        def work(say):
            say(f"merging {other}")
            got = importer.merge(other, DB, progress=say)
            importer.rebuild(progress=say)
            return f"{got['added']} hands merged, {got['known']} already known"
        self._run_import("merging", work)

    @staticmethod
    def _do_load(paths, say):
        say("looking at the files…")
        survey = importer.survey(paths)
        say("  " + ", ".join(f"{len(survey[key])} {key}" for key in sites.KEYS)
            + f", {len(survey['unknown'])} not recognised")
        if not importer.recognised(survey):
            return "nothing to import"
        got = importer.load(paths, DB, progress=say)
        say(f"{got['added']} hands added, {got['known']} already known")
        if got["added"]:
            importer.rebuild(progress=say)
        return (f"{got['added']} hands added"
                + (f", {got['unknown']} files unrecognised"
                   if got["unknown"] else ""))


class App(ImportMixin, ttk.Frame):
    def __init__(self, master, check_updates=False):
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.con = connect_db(check_same_thread=False)
        # Every switch the command line has, whether or not a control for
        # it has been built yet. These used to appear as a side effect of
        # drawing the rail, so deleting the rail silently emptied the filter.
        self.flags = {f: tk.BooleanVar() for f in query.SWITCHES}
        self.flags["--marked"] = tk.BooleanVar()
        self.flags["--noted"] = tk.BooleanVar()
        # Not in SWITCHES: the SQL is the clock. Same reason --marked
        # lives here -- a machine with no hands today would fail the
        # live "selects nothing" loop for a filter that is working.
        self.flags["--today"] = tk.BooleanVar()
        self.multi = {"pos": set(), "vs": set(), "street": set(),
                      "pot": set(), "facing": set(), "board": set(),
                      "quick": set(),
                      "made": set(), "kicker": set(), "fd": set(),
                      "sd": set(), "turn_card": set(), "river_card": set()}
        # The filter's values live here rather than on the widgets, because
        # the widgets belong to a dialog that is destroyed every time it is
        # closed and the filter is not.
        self.vals = {n: tk.StringVar() for n in
                     ("site", "stake", "player", "deep", "short",
                      "since", "until", "where",
                      "line", "node", "pre", "flop", "turn", "river",
                      "after", "then", "size", "outcome",
                      "players", "live", "stack", "tag",
                      "alias", "vs_alias", "villain_type",
                      "combo", "action", "result", "hist_group",
                      "session", "hours", "start_of_day", "tz",
                      "fmt", "last_sessions", "call_range")}
        self.options = {"sites": [], "stakes": [], "players": []}
        self.cohort_spec = None
        # Study cockpit: each crumb is a row click (parent ∧ row).
        # Kept off the widgets so popping the breadcrumb does not have
        # to reverse-engineer which box a composed row wrote into.
        self.crumbs = []
        self.extra_panes = []
        self.pane_hidden = {}
        self.pane_sort = {}
        self._pin_alias = {}
        self._detached = []
        self._study_out = None
        self._session_id = None
        self._sessions_out = None
        self._statistics_out = None
        self.stat_fmt = tk.StringVar(value="cash")
        self.stat_since = tk.StringVar()
        self.stat_until = tk.StringVar()
        self.stat_last_n = tk.StringVar()
        self.stat_exclude = tk.BooleanVar(value=False)
        self.stat_key = None
        self.stat_kind = "action"
        self.stat_combo = None
        self.stat_hist_group = None
        self.stat_cells = {}
        self.session_hidden = set()
        self.session_series = None
        self.graph_unit = tk.StringVar(value="bb")
        clock = sessions.load_clock()
        self.sess_today = tk.BooleanVar()
        self.sess_hours = tk.StringVar()
        self.sess_since = tk.StringVar()
        self.sess_until = tk.StringVar()
        self.sess_sod = tk.StringVar(value=str(clock.start_of_day))
        self.sess_tz = {k: tk.StringVar(value=f"{clock.offset(k):g}")
                        for k in sites.KEYS}
        # Shared by the hands tab, the study list, and the session list.
        # Created here so `_build_sessions` can bind it before the
        # hands tab exists.
        self.hand_tag = tk.StringVar()

        self.results = queue.Queue()
        self.pending = 0
        self.news = None
        self._build()
        self.after(80, self._drain)
        self.refresh()
        # On a worker, because it talks to git and to the network and the
        # window must open at the same speed whether either answers. Every
        # way it can fail comes back as a sentence rather than an exception.
        if check_updates:
            threading.Thread(target=self._look_for_update,
                             daemon=True).start()
            self.after(400, self._say_update)
        # Whether there is anything to import, asked on a worker for the
        # same reason: it walks three folders, and the window has to open at
        # the same speed whether or not a disk is slow today. It only
        # notices -- importing is a minute of derivation and is nobody's
        # idea of a thing that should happen while they are looking for a
        # number.
        self.fresh = None
        if check_updates:
            threading.Thread(target=self._look_for_hands, daemon=True).start()
            self.after(600, self._say_hands)

    def _look_for_hands(self):
        try:
            self.fresh = importer.anything_new(DB)
        except Exception:
            diag._report("new-hand check", *sys.exc_info()[1:])
            self.fresh = 0

    def _say_hands(self):
        """
        Say it once, and only when it is true.

        The banner is shared with the updater, which has first claim on it:
        an update is about the program and this is about the data, and two
        messages in one label is one message nobody reads. So this waits for
        the update check to have had its say and then takes the space if it
        is still empty.
        """
        if self.fresh is None:
            self.after(400, self._say_hands)
            return
        diag.event("new hands", files=self.fresh)
        if not self.fresh or self.banner.winfo_ismapped():
            return
        self.banner.configure(
            text=f"{self.fresh} hand history files written since your last "
                 f"import — Import ▸ Import new hands",
            foreground=ACCENT)
        self.banner.pack(side="left", padx=12)

    def _look_for_update(self):
        try:
            self.news = update.update()
        except Exception:
            diag._report("update check", *sys.exc_info()[1:])
            self.news = ("skipped", "the update check itself failed -- "
                                    "see the log")

    def _say_update(self):
        """
        Say something only when there is something to say.

        "Up to date" is not news and a line that says it every launch is a
        line people stop reading, which is how the one launch that says
        something else goes unnoticed.
        """
        if self.news is None:
            self.after(400, self._say_update)
            return
        state, message = self.news
        if state in ("current", "skipped"):
            diag.event("update", state=state, detail=message)
            return
        colour = {"updated": GOOD, "blocked": WARN, "available": ACCENT}[state]
        self.banner.configure(text=message, foreground=colour)
        self.banner.pack(side="left", padx=12)
        diag.event("update", state=state, detail=message)

    # ---- layout -------------------------------------------------------
    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(12, 8))
        ttk.Label(head, text="poker_analysis",
                  style="Title.TLabel").pack(side="left")
        self.sub = ttk.Label(head, text="", style="Dim.TLabel")
        self.sub.pack(side="left", padx=12)
        # Packed only when it has something to report -- see `_say_update`.
        self.banner = ttk.Label(head, text="", style="Dim.TLabel")
        self.status = ttk.Label(head, text="", style="Dim.TLabel")
        self.status.pack(side="right")

        # The filter lives behind a button rather than down the side. A rail
        # wide enough for every filter this database supports is a rail that
        # leaves no room for the answer, and the filter is looked at far less
        # often than the thing it produces.
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Button(bar, text="＋  Filter", style="Accent.TButton",
                   command=self.open_filters).pack(side="left")
        self.cohort_btn = ttk.Button(bar, text="Players",
                         command=self.open_cohort)
        self.cohort_btn.pack(side="left", padx=(6, 0))
        ttk.Label(bar, text="report", style="Dim.TLabel").pack(
            side="left", padx=(18, 6))
        self.preset = tk.StringVar(value="")
        self.preset_box = ttk.Combobox(
            bar, textvariable=self.preset,
            values=["" ] + list(query.reports()), state="readonly",
            width=28)
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>",
                             lambda _e: self._on_preset())
        ttk.Label(bar, text="pin", style="Dim.TLabel").pack(
            side="left", padx=(14, 6))
        self.pin = tk.StringVar(value="")
        self.pin_box = ttk.Combobox(
            bar, textvariable=self.pin,
            values=[""] + list(query.reports()), state="readonly",
            width=22)
        self.pin_box.pack(side="left")
        self.pin_box.bind("<<ComboboxSelected>>",
                          lambda _e: self.refresh())
        self.clear_btn = ttk.Button(bar, text="clear", command=self.clear_filters)
        self.summary = ttk.Label(bar, text="all hands", style="Dim.TLabel")
        self.summary.pack(side="left", padx=12)
        ttk.Separator(self).pack(fill="x")

        right = ttk.Frame(self)
        right.pack(fill="both", expand=True)
        self._views(right)

    def open_filters(self):
        FilterDialog(self)

    def clear_filters(self):
        for v in self.flags.values():
            v.set(False)
        for g in self.multi:
            self.multi[g] = set()
        for v in self.vals.values():
            v.set("")
        self.cohort_spec = None
        self.cohort_btn.configure(text="Players")
        self.preset.set("")
        self.pin.set("")
        self.crumbs = []
        self.refresh()

    # Who is being measured, as opposed to the situation they are in. A
    # Smart Report replaces the situation and keeps the person -- opening
    # "Flop c-bets" while hero is selected would otherwise AND the leftover
    # street/pot clicks onto the report and often match nothing.
    WHO_SWITCHES = ("--hero", "--pool", "--vs-hero", "--vs-pool",
                    "--reg", "--fish", "--vs-reg", "--vs-fish",
                    "--with-fish", "--regs-only",
                    "--today")
    WHO_VALS = ("site", "stake", "player", "since", "until",
                "alias", "vs_alias", "villain_type",
                "session", "hours", "start_of_day", "tz",
                "fmt", "last_sessions")

    def clear_situation(self, keep_preset=False):
        """Drop the situation filters; keep the player, the site, the cohort."""
        for flag, var in self.flags.items():
            if flag not in self.WHO_SWITCHES:
                var.set(False)
        for group in self.multi:
            self.multi[group] = set()
        for name, var in self.vals.items():
            if name not in self.WHO_VALS:
                var.set("")
        if not keep_preset:
            self.preset.set("")
        self.crumbs = []

    def _on_preset(self):
        """Opening a named report replaces the situation, the way H2N does."""
        if self.preset.get():
            self.clear_situation(keep_preset=True)
        self.refresh()

    def apply_report(self, name):
        """Open a named Smart Report and keep who is being measured."""
        self.clear_situation()
        self.preset.set(name)
        self.refresh()

    def apply_spot(self, name, argv):
        """
        Open a neighbouring spot. A named report goes through the box;
        a generated variant (same filter, in position) is written onto
        the widgets so it is the filter you would have built by hand.
        """
        self.clear_situation()
        if name in query.reports():
            self.preset.set(name)
        else:
            self._apply_argv(argv)
        self.refresh()

    def _apply_argv(self, argv, who=False):
        """Set the widgets from a flag list. The inverse of `argv`."""
        i = 0
        argv = list(argv) if who else query.situation_only(list(argv))
        while i < len(argv):
            a = argv[i]
            if a in self.flags:
                self.flags[a].set(True)
                twin = OPPOSITES.get(a)
                if twin:
                    self.flags[twin].set(False)
                i += 1
                continue
            if a in query.VALUE_FLAGS and i + 1 < len(argv):
                v = argv[i + 1]
                group = {"--pos": "pos", "--vs": "vs", "--street": "street",
                         "--pot": "pot", "--facing": "facing",
                         "--board": "board", "--quick": "quick",
                         "--made": "made", "--kicker": "kicker",
                         "--fd": "fd", "--sd": "sd",
                         "--turn-card": "turn_card",
                         "--river-card": "river_card"}.get(a)
                if group:
                    self.multi[group] = set(x.strip() for x in v.split(",")
                                            if x.strip())
                else:
                    name = {"--site": "site", "--stake": "stake",
                            "--player": "player", "--deep": "deep",
                            "--short": "short", "--since": "since",
                            "--until": "until", "--where": "where",
                            "--line": "line", "--node": "node",
                            "--pre": "pre", "--flop": "flop",
                            "--turn": "turn", "--river": "river",
                            "--after": "after", "--then": "then",
                            "--size": "size", "--outcome": "outcome",
                            "--players": "players", "--live": "live",
                            "--stack": "stack", "--tag": "tag",
                            "--alias": "alias", "--vs-alias": "vs_alias",
                            "--villain-type": "villain_type",
                            "--vs-class": "villain_type",
                            "--combo": "combo", "--action": "action",
                            "--result": "result",
                            "--hist-group": "hist_group",
                            "--session": "session", "--hours": "hours",
                            "--start-of-day": "start_of_day",
                            "--tz": "tz",
                            "--fmt": "fmt",
                            "--last-sessions": "last_sessions",
                            "--call-range": "call_range"}.get(a)
                    if name:
                        self.vals[name].set(v)
                i += 2
                continue
            i += 1

    def open_cohort(self):
        CohortDialog(self)

    def reload_reports(self):
        """
        Put a newly saved report into the box without restarting.

        The values of a Combobox are a snapshot taken when it was made. A
        report saved from the filter dialog would otherwise be in the file,
        in `--preset` and on the command line, and missing from the one
        place it was saved from -- which reads as the save having silently
        failed.
        """
        keep = self.preset.get()
        known = list(query.reports())
        self.preset_box.configure(values=[""] + known)
        self.preset.set(keep if keep in known else "")
        pinned = self.pin.get()
        extras = [k for k in self._pin_alias if k not in known]
        self.pin_box.configure(values=[""] + known + extras)
        self.pin.set(pinned if pinned in known or pinned in self._pin_alias
                     else "")


    def _views(self, right):
        bar = ttk.Frame(right)
        bar.pack(fill="x", padx=12, pady=(10, 4))
        self.by = ttk.Combobox(bar, values=list(query.DIMENSIONS),
                               state="readonly", width=12)
        self.by.current(0)
        self.by.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.by_label = ttk.Label(bar, text="split by", style="Dim.TLabel")
        # What the chart is of. Empty means the range itself -- which combos
        # got here -- and a stat means that stat per combo. They answer
        # different questions and both are wanted, so it is a choice and not
        # a second tab.
        self.of = ttk.Combobox(bar, state="readonly", width=22,
                               values=["the range itself"] +
                               [st.label for st in STATS if st.source == "d"])
        self.of.current(0)
        self.of.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.of_label = ttk.Label(bar, text="chart of", style="Dim.TLabel")

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())
        self.bar = bar

        self.filter_line = ttk.Label(right, text="", style="Dim.TLabel")
        self.filter_line.pack(anchor="w", padx=14, pady=(0, 4))
        # Neighbouring spots, the way Hand2Note's report tree lets you
        # walk from a flop c-bet to the other seat and the next street
        # without rebuilding the filter. Built empty; `refresh` fills it.
        self.related_bar = ttk.Frame(right)
        self.related_bar.pack(fill="x", padx=14, pady=(0, 8))

        self.tabs = {}
        for name in ("study", "statistics", "sessions", "stats", "range",
                     "chart", "report", "results", "graph", "hands"):
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=name)
            self.tabs[name] = frame
        self._build_sessions(self.tabs["sessions"])
        self._build_statistics(self.tabs["statistics"])
        self._build_study(self.tabs["study"])
        self.tree = {}
        for name in ("stats", "range", "report", "results", "hands"):
            self.tree[name] = self._table(self.tabs[name])
        self.win_graph = WinGraph(self.tabs["graph"], unit_var=self.graph_unit)
        self.win_graph.pack(fill="both", expand=True)
        self.canvas = self.win_graph.canvas
        self.series = None
        # The chart is drawn and not tabulated, so like the graph it gets a
        # canvas rather than a Treeview. A range is a shape; 169 numbers in
        # rows is the same information in the one form nobody can read it in.
        self.chart_canvas = tk.Canvas(self.tabs["chart"], bg=BG,
                                      highlightthickness=0)
        self.chart_canvas.pack(fill="both", expand=True)
        self.chart_canvas.bind("<Configure>", lambda e: self._draw_chart())
        self.chart = None
        self.tree["hands"].bind("<Double-1>", self._open_hand)
        self.tree["hands"].bind("<Return>", self._open_hand)
        self.tree["hands"].bind("<Button-3>", self._hand_menu)
        wrap = self.tree["hands"].master
        study = ttk.Frame(self.tabs["hands"])
        study.pack(fill="x", padx=8, pady=(6, 0), before=wrap)
        ttk.Button(study, text="Mark",
                   command=lambda: self._mark_selected(True)).pack(
                       side="left")
        ttk.Button(study, text="Unmark",
                   command=lambda: self._mark_selected(False)).pack(
                       side="left", padx=(6, 0))
        ttk.Label(study, text="tag", style="Dim.TLabel").pack(
            side="left", padx=(12, 4))
        ttk.Entry(study, textvariable=self.hand_tag, width=12).pack(
            side="left")
        ttk.Button(study, text="Note",
                   command=self._note_selected).pack(
                       side="left", padx=(12, 0))
        # A stat or a Faced Next row is a filter. Double-clicking it is
        # how Hand2Note walks from a frequency to the hands that made it,
        # without going back through the dialog.
        self.tree["stats"].bind("<Double-1>", self._drill_stat)
        self.tree["report"].bind("<Double-1>", self._drill_stat)

    def _pin_name(self):
        """The pin box value, or the JSON argv a pane row stored under it."""
        raw = self.pin.get().strip()
        return self._pin_alias.get(raw, raw)

    def _build_sessions(self, parent):
        """
        Hand2Note's other primary study surface: sit-downs, not spots.

        List (Won bb on the row) → detail + graph + compact hands →
        export → Open in Reports. The date bar is the clock in
        `sessions.py`, not raw `--since`.
        """
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Checkbutton(bar, text="Today", variable=self.sess_today,
                        command=self._sess_dates_changed).pack(side="left")
        ttk.Label(bar, text="last", style="Dim.TLabel").pack(
            side="left", padx=(12, 4))
        ttk.Entry(bar, textvariable=self.sess_hours, width=5).pack(side="left")
        ttk.Label(bar, text="hours", style="Dim.TLabel").pack(
            side="left", padx=(4, 12))
        ttk.Label(bar, text="from", style="Dim.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.sess_since, width=12).pack(
            side="left", padx=(4, 8))
        ttk.Label(bar, text="to", style="Dim.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.sess_until, width=12).pack(
            side="left", padx=(4, 12))
        ttk.Label(bar, text="start-of-day", style="Dim.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.sess_sod, width=4).pack(
            side="left", padx=(4, 12))
        ttk.Label(bar, text="room tz", style="Dim.TLabel").pack(side="left")
        for key in sites.KEYS:
            ttk.Label(bar, text=key, style="Dim.TLabel").pack(
                side="left", padx=(8, 2))
            ttk.Entry(bar, textvariable=self.sess_tz[key], width=5).pack(
                side="left")
        ttk.Button(bar, text="Apply", command=self._sess_dates_changed).pack(
            side="left", padx=(12, 0))
        self.sess_warn = ttk.Label(parent, text="", style="Dim.TLabel")
        self.sess_warn.pack(fill="x", padx=8, pady=(0, 4))

        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.LabelFrame(body, text="Sessions")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        gear = ttk.Frame(left)
        gear.pack(fill="x")
        ttk.Button(gear, text="⚙", width=3,
                   command=self._session_gear).pack(side="right")
        ttk.Label(gear, text="Won bb is profit in big blinds, not collected",
                  style="Dim.TLabel").pack(side="left", padx=6)
        self.session_list = self._table(left)
        self.session_list.bind("<<TreeviewSelect>>", self._pick_session)
        self.session_list.bind("<Double-1>", self._pick_session)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        tools = ttk.Frame(right)
        tools.pack(fill="x", pady=(0, 4))
        ttk.Button(tools, text="Open in Reports",
                   command=self._session_to_reports).pack(side="left")
        ttk.Button(tools, text="Export session hands",
                   command=self._export_session).pack(side="left", padx=(8, 0))
        self.sess_sum = ttk.Label(tools, text="", style="Dim.TLabel")
        self.sess_sum.pack(side="left", padx=12)
        self.session_graph = WinGraph(right, unit_var=self.graph_unit,
                                      height=200, compact=True)
        self.session_graph.pack(fill="x")
        self.session_canvas = self.session_graph.canvas
        hands = ttk.LabelFrame(right, text="Hands")
        hands.pack(fill="both", expand=True, pady=(6, 0))
        mark = ttk.Frame(hands)
        mark.pack(fill="x")
        ttk.Button(mark, text="Mark",
                   command=lambda: self._mark_selected(True)).pack(side="left")
        ttk.Button(mark, text="Unmark",
                   command=lambda: self._mark_selected(False)).pack(
                       side="left", padx=(6, 0))
        ttk.Label(mark, text="tag", style="Dim.TLabel").pack(
            side="left", padx=(12, 4))
        ttk.Entry(mark, textvariable=self.hand_tag, width=12).pack(side="left")
        ttk.Button(mark, text="Note", command=self._note_selected).pack(
            side="left", padx=(12, 0))
        self.session_hands = self._table(hands)
        self.session_hands.bind("<Double-1>", self._open_hand)
        self.session_hands.bind("<Return>", self._open_hand)
        self.session_hands.bind("<Button-3>", self._hand_menu)

    def _build_statistics(self, parent):
        """
        Hand2Note's Statistics tab: curated rates for a subject,
        then click-stat → range → Call Range → hands → Reports.

        The exclude checkbox is a compute flag on this sample only.
        It is not written into argv(), so Open in Reports and the
        Sessions tab cannot inherit it.
        """
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(bar, text="mode", style="Dim.TLabel").pack(side="left")
        for lab, val in (("Cash", "cash"), ("MTT", "mtt")):
            ttk.Radiobutton(bar, text=lab, value=val,
                            variable=self.stat_fmt,
                            command=self._stat_context_changed).pack(
                                side="left", padx=(6, 0))
        ttk.Label(bar, text="from", style="Dim.TLabel").pack(
            side="left", padx=(14, 4))
        ttk.Entry(bar, textvariable=self.stat_since, width=12).pack(
            side="left")
        ttk.Label(bar, text="to", style="Dim.TLabel").pack(
            side="left", padx=(8, 4))
        ttk.Entry(bar, textvariable=self.stat_until, width=12).pack(
            side="left")
        ttk.Label(bar, text="last", style="Dim.TLabel").pack(
            side="left", padx=(14, 4))
        ttk.Entry(bar, textvariable=self.stat_last_n, width=4).pack(
            side="left")
        ttk.Label(bar, text="sessions", style="Dim.TLabel").pack(
            side="left", padx=(4, 12))
        ttk.Checkbutton(bar, text="exclude reg-vs-fish",
                        variable=self.stat_exclude,
                        command=self._stat_context_changed).pack(
                            side="left")
        ttk.Button(bar, text="Who is Reg",
                   command=self._who_is_reg).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Apply",
                   command=self._stat_context_changed).pack(
                       side="left", padx=(8, 0))
        self.stat_counts = ttk.Label(parent, text="", style="Dim.TLabel")
        self.stat_counts.pack(fill="x", padx=8, pady=(0, 4))

        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.LabelFrame(body, text="Statistics")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.stat_grid = ttk.Frame(left)
        self.stat_grid.pack(fill="both", expand=True, padx=4, pady=4)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        tools = ttk.Frame(right)
        tools.pack(fill="x", pady=(0, 4))
        ttk.Button(tools, text="Range",
                   command=lambda: self._stat_kind("action")).pack(
                       side="left")
        ttk.Button(tools, text="Call Range",
                   command=lambda: self._stat_kind("call")).pack(
                       side="left", padx=(6, 0))
        ttk.Button(tools, text="Open in Reports",
                   command=self._stat_to_reports).pack(
                       side="left", padx=(12, 0))
        self.stat_title = ttk.Label(tools, text="", style="Dim.TLabel")
        self.stat_title.pack(side="left", padx=12)
        self.stat_canvas = tk.Canvas(right, bg=BG, highlightthickness=0,
                                     height=280)
        self.stat_canvas.pack(fill="x")
        self.stat_canvas.bind("<Configure>",
                              lambda _e: self._draw_stat_chart())
        self.stat_canvas.bind("<Button-1>", self._click_stat_cell)
        self.stat_hist = HistWidget(right, on_click=self._click_stat_hist,
                                    height=150)
        self.stat_hist.pack(fill="x", pady=(4, 0))
        hands = ttk.LabelFrame(right, text="Hands")
        hands.pack(fill="both", expand=True, pady=(6, 0))
        mark = ttk.Frame(hands)
        mark.pack(fill="x")
        ttk.Button(mark, text="Mark",
                   command=lambda: self._mark_selected(True)).pack(
                       side="left")
        ttk.Button(mark, text="Unmark",
                   command=lambda: self._mark_selected(False)).pack(
                       side="left", padx=(6, 0))
        ttk.Label(mark, text="tag", style="Dim.TLabel").pack(
            side="left", padx=(12, 4))
        ttk.Entry(mark, textvariable=self.hand_tag, width=12).pack(
            side="left")
        ttk.Button(mark, text="Note", command=self._note_selected).pack(
            side="left", padx=(12, 0))
        self.stat_hands = self._table(hands)
        self.stat_hands.bind("<Double-1>", self._open_hand)
        self.stat_hands.bind("<Return>", self._open_hand)
        self.stat_hands.bind("<Button-3>", self._hand_menu)

    def _stat_context_changed(self):
        self.refresh()

    def statistics_argv(self):
        """
        Who + cash/MTT + date + last-N. Not the study crumbs, and
        not the exclude flag -- that is a compute option, not a filter.
        """
        argv = query.who_only(self.argv())
        argv = [a for i, a in enumerate(argv)
                if a not in ("--fmt", "--last-sessions", "--since", "--until",
                             "--call-range")
                and (i == 0 or argv[i - 1] not in (
                    "--fmt", "--last-sessions", "--since", "--until",
                    "--call-range"))]
        fmt = (self.stat_fmt.get() or "cash").strip()
        argv += ["--fmt", fmt]
        since = (self.stat_since.get() or "").strip()
        until = (self.stat_until.get() or "").strip()
        if since:
            argv += ["--since", since]
        if until:
            argv += ["--until", until]
        raw_n = (self.stat_last_n.get() or "").strip()
        if raw_n and fmt != "mtt":
            argv += ["--last-sessions", raw_n]
        return argv

    def _stat_kind(self, kind):
        if not self.stat_key:
            return
        self.stat_kind = kind
        self.stat_combo = None
        self.stat_hist_group = None
        self.refresh()

    def _pick_stat(self, key):
        self.stat_key = key
        self.stat_kind = "action"
        self.stat_combo = None
        self.stat_hist_group = None
        self.refresh()

    def _stat_to_reports(self):
        """The clicked stat as a Reports filter. Exclude stays behind."""
        if not self.stat_key:
            return
        argv = query.reports_argv(
            self.statistics_argv(), self.stat_key, self.stat_kind,
            self.stat_combo, self.stat_hist_group)
        self.clear_situation()
        # Who/when stay: cash/MTT, dates, last-N. situation_only
        # would drop them and Reports would open a different subject.
        self._apply_argv(argv, who=True)
        self.nb.select(self.tabs["study"])
        self.refresh()

    def _who_is_reg(self):
        WhoIsRegDialog(self)

    def _sess_clock(self):
        tz = {}
        for key, var in self.sess_tz.items():
            raw = (var.get() or "0").strip()
            try:
                tz[key] = float(raw)
            except ValueError:
                tz[key] = 0.0
        try:
            hour = int((self.sess_sod.get() or "0").strip() or 0)
        except ValueError:
            hour = 0
        clock = sessions.Clock(start_of_day=hour % 24, offsets=tz)
        sessions.save_clock(clock)
        # Clock prefs stay on the date bar. Writing them into study
        # vals here made argv() grow `--start-of-day 0 --tz …` on
        # every apply_argv check and broke the study-flag round-trip.
        return clock

    def _sess_dates_changed(self):
        self._session_id = None
        self.refresh()

    def _session_window(self):
        clock = self._sess_clock()
        kind = hours = since = until = None
        if self.sess_today.get():
            kind = "today"
        raw_h = (self.sess_hours.get() or "").strip()
        if raw_h:
            hours = raw_h
        since = (self.sess_since.get() or "").strip() or None
        until = (self.sess_until.get() or "").strip() or None
        return clock, kind, hours, since, until

    def _pick_session(self, _event=None):
        tv = self.session_list
        sel = tv.selection()
        ids = getattr(tv, "_session_ids", {})
        if not sel or sel[0] not in ids:
            return
        self._session_id = ids[sel[0]]
        self.refresh()

    def _session_to_reports(self):
        """`--session ID` as the Reports chip. The sit-down, not the date."""
        if not self._session_id:
            return
        self.vals["session"].set(self._session_id)
        self.nb.select(self.tabs["study"])
        self.refresh()

    def _export_session(self):
        if not self._session_id:
            return
        path = filedialog.asksaveasfilename(
            title="export session hands",
            defaultextension=".txt",
            filetypes=[("hand histories", "*.txt"), ("all files", "*.*")])
        if not path:
            return
        con = sqlite3.connect(DB)
        try:
            sessions.ensure(con)
            session = sessions.of(con, self._session_id)
            if session is None:
                messagebox.showinfo("export", "that session is gone")
                return
            got = sessions.export_hands(con, session, path)
        finally:
            con.close()
        msg = f"{got['wrote']} hands written to {got['path']}"
        if got["missing"]:
            msg += (f"\n{got['missing']} skipped -- the original HH file "
                    "is gone. Export cannot invent the text.")
        messagebox.showinfo("export", msg)

    def _session_gear(self):
        hidden = set(self.session_hidden)
        win = tk.Toplevel(self)
        win.title("session columns")
        win.configure(bg=BG)
        vars = {}
        for col in sessions.LIST_COLUMNS:
            v = tk.BooleanVar(value=col not in hidden)
            vars[col] = v
            ttk.Checkbutton(win, text=col, variable=v).pack(
                anchor="w", padx=12, pady=2)

        def apply():
            self.session_hidden = {c for c, v in vars.items() if not v.get()}
            win.destroy()
            if self._sessions_out:
                self._render_sessions(self._sessions_out)

        ttk.Button(win, text="Apply", command=apply).pack(pady=8)

    def _draw_session_graph(self, message=None):
        self.session_graph.set(self.session_series, message)

    def _build_study(self, parent):
        """
        Hand2Note's Reports cockpit: subject is the bar above; this is
        chips, breadcrumb, Smart strip, clickable panes, compact hands.
        """
        self.crumb_bar = ttk.Frame(parent)
        self.crumb_bar.pack(fill="x", padx=8, pady=(8, 2))
        self.chip_bar = ttk.Frame(parent)
        self.chip_bar.pack(fill="x", padx=8, pady=(0, 2))
        self.smart = ttk.Frame(parent)
        self.smart.pack(fill="x", padx=8, pady=(4, 4))
        tools = ttk.Frame(parent)
        tools.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(tools, text="+ pane", style="Dim.TLabel").pack(side="left")
        self.plus_box = ttk.Combobox(
            tools, values=[query.STUDY_PANE_LABELS[k]
                           for k in query.PLUS_STUDY_PANES],
            state="readonly", width=14)
        self.plus_box.pack(side="left", padx=(6, 0))
        self.plus_box.bind("<<ComboboxSelected>>", lambda _e: self._add_pane())
        ttk.Label(tools, text="click a row to drill  ·  ⚙ Detach  ·  right-click a hand",
                  style="Dim.TLabel").pack(side="left", padx=12)

        grid = ttk.Frame(parent)
        grid.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        for i in (0, 1):
            grid.columnconfigure(i, weight=1)
            grid.rowconfigure(i, weight=1)
        self.study_grid = grid
        self.study_trees = {}
        self.study_meta = {}
        self.study_heads = {}
        for i, name in enumerate(query.DEFAULT_STUDY_PANES):
            r, c = divmod(i, 2)
            self._make_pane(grid, name, r, c)
        self.plus_frames = {}

        self.study_hist = HistWidget(parent, on_click=self._click_study_hist,
                                     height=140,
                                     on_gear=lambda: self._pane_gear("hist"))
        self.study_hist.pack(fill="x", padx=8, pady=(0, 4))
        self.study_graph = WinGraph(parent, unit_var=self.graph_unit,
                                    height=170, compact=True,
                                    on_gear=lambda: self._pane_gear("graph"))
        self.study_graph.pack(fill="x", padx=8, pady=(0, 4))
        hands = ttk.LabelFrame(parent, text="Hands")
        hands.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        hbar = ttk.Frame(hands)
        hbar.pack(fill="x")
        ttk.Button(hbar, text="⚙", width=3,
                   command=lambda: self._pane_gear("hands")).pack(side="right")
        self.study_hands = self._table(hands)
        self.study_hands.bind("<Double-1>", self._open_hand)
        self.study_hands.bind("<Return>", self._open_hand)
        self.study_hands.bind("<Button-3>", self._hand_menu)

    def _make_pane(self, grid, name, row, col):
        box = ttk.LabelFrame(grid, text=query.STUDY_PANE_LABELS.get(name, name))
        box.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Button(bar, text="⚙", width=3,
                   command=lambda n=name: self._pane_gear(n)).pack(
                       side="right")
        tv = self._table(box)
        tv.bind("<Button-1>", lambda e, n=name: self._drill_pane(n, e))
        tv.bind("<Button-3>", lambda e, n=name: self._pane_pin_menu(n, e))
        self.study_trees[name] = tv
        self.study_meta[name] = box
        return box

    def _add_pane(self):
        label = self.plus_box.get()
        key = None
        for k, lab in query.STUDY_PANE_LABELS.items():
            if lab == label:
                key = k
                break
        if not key or key in self.study_trees:
            return
        self.extra_panes.append(key)
        n = len(self.study_trees)
        # Default four occupy 2x2; extras start a new row under that.
        r, c = divmod(n, 2)
        self.study_grid.rowconfigure(r, weight=1)
        self._make_pane(self.study_grid, key, r, c)
        self.plus_box.set("")
        self.refresh()

    def _table(self, parent):
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        tv = ttk.Treeview(wrap, show="headings", selectmode="browse")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        tv.tag_configure("group", foreground=DIM)
        tv.tag_configure("thin", foreground=WARN)
        tv.tag_configure("pos", foreground=GOOD)
        tv.tag_configure("neg", foreground=BAD)
        tv.tag_configure("note", foreground=DIM)
        return tv

    # ---- filter -------------------------------------------------------
    def _toggled(self, flag):
        other = OPPOSITES.get(flag)
        if other and self.flags[flag].get():
            self.flags[other].set(False)
        self.refresh()

    def _chip(self, group, value, var):
        (self.multi[group].add if var.get() else self.multi[group].discard)(value)
        self.refresh()

    def argv(self):
        """The window's state as the argument list `query.build` understands."""
        argv = query.preset_argv(self.preset.get()) if self.preset.get() else []
        argv += [fl for fl, v in self.flags.items() if v.get()]
        if self.cohort_spec is not None:
            conditions, site, klass, durable = self.cohort_spec
            argv.append("--cohort")
            flags = {"fold_to_threebet": "--fold-to-threebet"}
            for field, value in conditions:
                if field == "_expr":
                    argv.append(value)
                    continue
                argv += [flags.get(field, "--" + field), value]
            if site:
                argv += ["--site", site]
            if klass:
                argv += ["--class", klass]
            if durable is not None:
                argv += ["--durable", str(durable)]
        for group, flag in (("pos", "--pos"), ("vs", "--vs"),
                            ("street", "--street"), ("pot", "--pot"),
                            ("facing", "--facing"),
                            ("board", "--board"), ("quick", "--quick"),
                            ("made", "--made"), ("kicker", "--kicker"),
                            ("fd", "--fd"), ("sd", "--sd"),
                            ("turn_card", "--turn-card"),
                            ("river_card", "--river-card")):
            if self.multi.get(group):
                argv += [flag, ",".join(sorted(self.multi[group]))]
        for name, flag in (("site", "--site"), ("stake", "--stake"),
                           ("player", "--player"), ("deep", "--deep"),
                           ("short", "--short"), ("since", "--since"),
                           ("until", "--until"), ("where", "--where"),
                           ("line", "--line"), ("node", "--node"),
                           ("pre", "--pre"), ("flop", "--flop"),
                           ("turn", "--turn"), ("river", "--river"),
                           ("after", "--after"), ("then", "--then"),
                           ("size", "--size"), ("outcome", "--outcome"),
                           ("players", "--players"), ("live", "--live"),
                           ("stack", "--stack"), ("tag", "--tag"),
                           ("alias", "--alias"),
                           ("vs_alias", "--vs-alias"),
                           ("villain_type", "--villain-type"),
                           ("combo", "--combo"),
                           ("action", "--action"),
                           ("result", "--result"),
                           ("hist_group", "--hist-group"),
                           ("session", "--session"),
                           ("hours", "--hours"),
                           ("start_of_day", "--start-of-day"),
                           ("tz", "--tz"),
                           ("fmt", "--fmt"),
                           ("last_sessions", "--last-sessions"),
                           ("call_range", "--call-range")):
            v = self.vals[name].get().strip()
            if not v or v.startswith("any "):
                continue
            if name in ("start_of_day", "tz"):
                # Prefs for `--today` / `--hours`. Emitting them on
                # every study filter made two writings of the same
                # situation compare unequal.
                clocked = (self.flags["--today"].get()
                           or self.vals["hours"].get().strip())
                if not clocked:
                    continue
            if name == "player" and v == HERO_CHOICE:
                # Not a name, so not `--player`: hero is a different name on
                # each site and none of them covers the others.
                if "--hero" not in argv:
                    argv.append("--hero")
                continue
            argv += [flag, v]
        if self.crumbs:
            argv = query.drill_stack(argv, self.crumbs)
        return argv


    def refresh(self):
        view = self.nb.tab(self.nb.select(), "text") if self.tabs else "stats"
        if view in ("report", "results"):
            self.by_label.pack(side="left", padx=(0, 6))
            self.by.pack(side="left")
        else:
            self.by_label.pack_forget()
            self.by.pack_forget()
        if view == "chart":
            self.of_label.pack(side="left", padx=(0, 6))
            self.of.pack(side="left")
        else:
            self.of_label.pack_forget()
            self.of.pack_forget()
        try:
            argv = self.statistics_argv() if view == "statistics" else self.argv()
            cohort_spec, query_argv = players.parse_cohort(argv)
            where, label, parts = query.build(query_argv)
        except SystemExit as e:
            self.filter_line.configure(text=str(e))
            return
        diag.event("refresh", view=view, filter=label)
        self.filter_line.configure(text="filter: " + label)
        self.summary.configure(text=self.describe_filter())
        self._paint_related(query.related_spots(query_argv))
        if self.argv():
            self.clear_btn.pack(side="left", padx=(6, 0))
        else:
            self.clear_btn.pack_forget()
        self.pending += 1
        token = self.pending
        self.status.configure(text="working…")
        sess = None
        if view == "sessions":
            try:
                clock, kind, hours, since, until = self._session_window()
            except ValueError as e:
                self.filter_line.configure(text=str(e))
                self.sess_warn.configure(text=str(e))
                return
            sess = (clock, kind, hours, since, until, self._session_id)
        stat_ctx = None
        if view == "statistics":
            stat_ctx = (bool(self.stat_exclude.get()), self.stat_key,
                        self.stat_kind, self.stat_combo,
                        self.stat_hist_group)
        threading.Thread(target=self._work, daemon=True,
                         args=(token, view, where, label, parts,
                               self.by.get(), cohort_spec, self.chart_stat(),
                               query_argv, self._pin_name(), sess, stat_ctx)
                         ).start()

    def _paint_related(self, related):
        """Clickable neighbours under the filter line."""
        for kid in self.related_bar.winfo_children():
            kid.destroy()
        if not related:
            return
        ttk.Label(self.related_bar, text="related",
                  style="Dim.TLabel").pack(side="left", padx=(0, 8))
        for spot in related:
            lab = tk.Label(self.related_bar, text=spot["name"],
                           bg=BG, fg=ACCENT, cursor="hand2",
                           font=(UI, 9), padx=6)
            lab.pack(side="left")
            if spot.get("why"):
                lab.configure(text=spot["name"])
            lab.bind("<Button-1>",
                     lambda _e, s=spot: self.apply_spot(s["name"], s["argv"]))
            lab.bind("<Enter>", lambda _e, w=lab: w.configure(fg=INK))
            lab.bind("<Leave>", lambda _e, w=lab: w.configure(fg=ACCENT))

    def chart_stat(self):
        """Which stat the chart is of, or None for the range itself."""
        chosen = self.of.get()
        for st in STATS:
            if st.label == chosen and st.source == "d":
                return st.key
        return None

    def _work(self, token, view, where, label, parts, dim, cohort_spec,
              stat=None, argv=None, pin="", sess=None, stat_ctx=None):
        """
        Every query runs here, never on the interface thread.

        A window that stops repainting while it thinks looks broken, and some
        of these take seconds: the graph prices all-ins the first time it sees
        them. So the work happens on a thread and the answer is posted back
        through a queue, with a token so that a slow answer to a filter the
        user has already changed is discarded rather than drawn.
        """
        argv = list(argv or [])
        con = connect_db()
        try:
            notes.attach(con)
            sessions.ensure(con)
            if cohort_spec is not None:
                where, _header, label = query.apply_cohort(
                    con, cohort_spec, where, label)
            out = {"view": view, "label": label}
            if view == "stats":
                out["n"], out["rows"] = query.stats_of(con, where)
                out["actions"] = query.actions_of(con, where)
                out["summary"] = query.spot_summary(con, where, argv)
                out["profit"] = query.action_profit_of(con, where)
                out["call_profit"] = query.call_profit_of(con, where)
                if query.pack_wants_won(argv):
                    out["amount_won"] = query.amount_won_of(con, where)
                if cohort_spec is not None:
                    out["cohort_range"] = _cohort_range_blob(con, where)
                out["faced"] = query.chain_report(con, where, argv, False)
                out["next"] = query.chain_report(con, where, argv, True)
                out["outcomes"] = query.outcomes_of(con, where)
                out["sizes"] = query.bet_sizes_of(con, where, argv)
                if pin:
                    try:
                        argv_a, argv_b = query.pin_sides(argv, pin)
                        out["compare"] = query.compare_of(
                            con, argv_a, argv_b, None, pin, packs=True)
                    except SystemExit as e:
                        out["pinned"] = {"error": str(e), "name": pin}
            elif view == "range":
                out.update(query.range_of(con, where))
            elif view == "chart":
                out.update(query.chart_of(con, where, stat))
            elif view == "report":
                dim = dim if dim in query.DIMENSIONS else "position"
                cols = query.columns_for(argv)
                if pin:
                    try:
                        argv_a, argv_b = query.pin_sides(argv, pin)
                        out["compare"] = query.compare_of(
                            con, argv_a, argv_b, None, pin,
                            dim=dim, columns=cols,
                            bet_sizes=(dim == "size"))
                    except SystemExit as e:
                        out["pinned"] = {"error": str(e), "name": pin}
                if dim == "size":
                    out["sizes"] = query.bet_sizes_of(con, where, argv)
                    out.update(dim=dim, cols=[], grid={}, counts={},
                               keys=[r["key"] for r in out["sizes"]["rows"]],
                               won_by={})
                else:
                    got = query.report_of(con, where, dim, cols, argv)
                    out.update(got)
            elif view == "sessions":
                clock, kind, hours, since, until, sid = sess or (
                    None, None, None, None, None, None)
                out.update(sessions.list_of(
                    con, clock=clock, kind=kind, since=since,
                    until=until, hours=hours))
                if sid:
                    detail = sessions.detail_of(con, sid, clock=clock)
                    out["detail"] = detail
            elif view == "statistics":
                exclude, key, kind, combo, hist_group = (
                    False, None, "action", None, None)
                if stat_ctx:
                    exclude, key, kind, combo, hist_group = (
                        list(stat_ctx) + [None] * 5)[:5]
                out.update(query.statistics_of(
                    con, where, argv, exclude=exclude))
                if cohort_spec is not None:
                    header = {
                        "describe": players.describe_cohort(cohort_spec),
                    }
                    try:
                        old_rf = con.row_factory
                        con.row_factory = sqlite3.Row
                        try:
                            header.update(players.cohort_summary(
                                players.cohort(con, *cohort_spec)))
                        finally:
                            con.row_factory = old_rf
                    except ValueError:
                        pass
                    out["cohort"] = header
                if key:
                    try:
                        drill = query.stat_range_of(
                            con, where, key, kind=kind, exclude=exclude,
                            combo=combo, hist_group=hist_group)
                        compact.attach(con, drill.get("hands") or [],
                                       fmt="text")
                        notes.decorate(con, drill.get("hands") or [])
                        out["drill"] = drill
                    except SystemExit as e:
                        out["drill_error"] = str(e)
            elif view == "study":
                panes = list(query.DEFAULT_STUDY_PANES) + list(
                    getattr(self, "extra_panes", []) or [])
                out.update(query.study_of(con, where, argv, panes=panes,
                                          pin=pin))
            elif view == "results":
                pairs = query.matching_seats(con, where)
                out["totals"] = query.results_of(con, pairs) if pairs else None
            elif view == "hands":
                rows = query.matching_hands(con, where, limit=500)
                notes.decorate(con, rows)
                compact.attach(con, rows, fmt="text")
                out["rows"] = rows
            elif view == "graph":
                out["graph"] = query.graph_of(con, where)
                out["series"] = out["graph"].get("series")
            if not self._any(out):
                out["why"] = query.why_empty(con, parts)
        except sqlite3.Error as e:
            diag.event("query failed", view=view, where=where, error=str(e))
            out = {"view": view, "error": f"SQL: {e}"}
        except Exception:
            # A worker dying quietly leaves the window up and unresponsive,
            # which is the hardest kind of failure to report from the outside.
            diag._report(f"worker ({view})", *sys.exc_info()[1:])
            out = {"view": view, "error": "something went wrong -- see the log"}
        finally:
            con.close()
        self.results.put((token, out))

    @staticmethod
    def _any(out):
        return bool(out.get("n") or out.get("rows") or out.get("totals")
                    or out.get("keys") or out.get("series")
                    or out.get("cells")
                    or out.get("compare")
                    or out.get("hands") or out.get("panes")
                    or (out.get("graph") or {}).get("n")
                    or (out.get("hist") or {}).get("n")
                    or (out.get("sizes") or {}).get("rows")
                    or "clock" in out
                    or out.get("rows") is not None
                    or "counts" in out)

    def _series(self, con, where):
        got = query.graph_of(con, where)
        return got if got.get("n", 0) >= 2 else None

    def _drain(self):
        try:
            while True:
                token, out = self.results.get_nowait()
                if token == self.pending:
                    self.status.configure(text="")
                    self._render(out)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    # ---- drawing ------------------------------------------------------
    def _render(self, out):
        view = out["view"]
        if out.get("label"):
            self.filter_line.configure(text="filter: " + out["label"])
        if view == "graph":
            self.series = out.get("graph") or out.get("series")
            self.win_graph.set(out.get("graph") or out.get("series"),
                               out.get("why") or out.get("error"))
            return
        if view == "chart":
            self.chart = out if out.get("cells") else None
            self._draw_chart(out.get("why") or out.get("error"))
            return
        if view == "sessions":
            self._render_sessions(out)
            return
        if view == "statistics":
            self._render_statistics(out)
            return
        if view == "study":
            self._render_study(out)
            return
        tv = self.tree[view]
        tv.delete(*tv.get_children())
        if out.get("error") or (out.get("why") and view != "report"):
            tv.configure(columns=("msg",))
            tv.heading("msg", text="")
            tv.column("msg", width=900, anchor="w")
            msg = out.get("error") or "nothing matches"
            tv.insert("", "end", values=(msg,), tags=("neg",))
            if out.get("why"):
                tv.insert("", "end", values=(out["why"],), tags=("note",))
            return
        getattr(self, "_render_" + view)(tv, out)

    def _cols(self, tv, cols, widths, anchors=None):
        """
        Fixed columns, and a spacer that takes whatever is left over.

        The first column used to absorb every spare pixel. On a wide window
        that put a stat's name against the left edge and its number against
        the right, with a foot of empty table between them and the heading
        floating in the middle of the gap -- so reading a row meant tracking
        across nothing. The numbers now sit where the names end, and the
        window gets wider by growing the empty part on the right, which is
        the part nobody needs to read.
        """
        anchors = anchors or {}
        tv.configure(columns=tuple(cols) + ("_pad",))
        for i, c in enumerate(cols):
            side = anchors.get(c, "e")
            tv.heading(c, text=c, anchor=side)
            tv.column(c, width=widths[i], minwidth=widths[i],
                      anchor=side, stretch=False)
        tv.heading("_pad", text="")
        tv.column("_pad", width=1, minwidth=1, anchor="w", stretch=True)

    def _render_sessions(self, out):
        """List + optional detail. Won bb stays on the row."""
        self._sessions_out = out
        warn = out.get("warning") or out.get("error") or ""
        self.sess_warn.configure(text=warn or (out.get("clock") or ""))
        tv = self.session_list
        tv.delete(*tv.get_children())
        hidden = set(self.session_hidden)
        cols = [c for c in sessions.LIST_COLUMNS if c not in hidden]
        if not cols:
            cols = ["Won bb"]
        widths = {"when": 140, "site": 80, "hero": 110, "hands": 60,
                  "duration": 70, "Won": 80, "Won bb": 80}
        anchors = {"when": "w", "site": "w", "hero": "w"}
        self._cols(tv, cols, [widths.get(c, 80) for c in cols], anchors)
        tv._session_ids = {}
        for r in out.get("rows") or []:
            vals = []
            for c in cols:
                if c == "Won":
                    vals.append(f"{r['Won']:+.2f}")
                elif c == "Won bb":
                    vals.append(f"{r['Won bb']:+.1f}")
                else:
                    vals.append(r.get(c) if c != "when" else (r.get("when") or "")[:16])
            tag = ("pos",) if r.get("Won bb", 0) > 0 else (
                ("neg",) if r.get("Won bb", 0) < 0 else ())
            iid = tv.insert("", "end", values=vals, tags=tag)
            tv._session_ids[iid] = r["id"]
            if r["id"] == self._session_id:
                tv.selection_set(iid)
        if not (out.get("rows") or []):
            tv.insert("", "end", tags=("note",),
                      values=[warn or "no hero cash sessions"]
                      + [""] * (len(cols) - 1))
        detail = out.get("detail") or {}
        session = detail.get("session")
        if session:
            self.sess_sum.configure(
                text=(f"{session['site']} {session['hero']}  "
                      f"{session['hands']} hands  {session['duration']}  "
                      f"Won {session['Won']:+.2f}  "
                      f"Won bb {session['Won bb']:+.1f}"))
            self.session_series = detail.get("graph") or detail.get("series")
            self._draw_session_graph(detail.get("why_graph")
                                     or detail.get("error"))
            self._render_hands(self.session_hands,
                               {"rows": detail.get("hands") or []})
        else:
            self.sess_sum.configure(text="pick a session")
            self.session_series = None
            self._draw_session_graph("pick a session")
            self._render_hands(self.session_hands, {"rows": []})

    def _render_statistics(self, out):
        """Curated grid, then the selected stat's range and hands."""
        self._statistics_out = out
        if out.get("error"):
            self.stat_counts.configure(text=out["error"])
            return
        c = out.get("counts") or {}
        bits = [f"{out.get('n', 0):,} decisions"]
        if out.get("exclude"):
            bits.append(f"excluded {out.get('excluded', 0):,} reg-vs-fish "
                        f"(of {out.get('n_all', 0):,})")
        bits.append(f"{c.get('players', 0)} identities")
        bits.append(f"{c.get('reg', 0)} regs / {c.get('fish', 0)} fish / "
                    f"{c.get('unknown', 0)} unknown")
        if out.get("cohort"):
            coh = out["cohort"]
            bits.append(coh.get("describe") or "cohort")
            if coh.get("players"):
                bits.append(
                    f"cohort {coh['players']} "
                    f"({coh.get('regs', 0)} regs / "
                    f"{coh.get('fish', 0)} fish / "
                    f"{coh.get('unknown', 0)} unknown)")
        if (self.stat_fmt.get() or "") == "mtt" and (
                self.stat_last_n.get() or "").strip():
            bits.append("last-N is cash sit-downs -- ignored in MTT")
        self.stat_counts.configure(text="  ·  ".join(bits))
        self._fill_stat_grid(out.get("rows") or [])
        drill = out.get("drill")
        if out.get("drill_error"):
            self.stat_title.configure(text=out["drill_error"])
            self.stat_chart = None
            self._draw_stat_chart(out["drill_error"])
            self.stat_hist.set(None, out["drill_error"])
            self._render_hands(self.stat_hands, {"rows": []})
            return
        if drill:
            self.stat_title.configure(text=drill.get("title") or "")
            self.stat_chart = drill.get("chart")
            self._draw_stat_chart()
            self.stat_hist.set(drill.get("hist"))
            self._render_hands(self.stat_hands,
                               {"rows": drill.get("hands") or []})
        else:
            self.stat_title.configure(
                text="click a stat · Call Range compares the call in "
                     "the same spot · a cell opens those hands")
            self.stat_chart = None
            self._draw_stat_chart()
            self.stat_hist.set(None)
            self._render_hands(self.stat_hands, {"rows": []})

    def _fill_stat_grid(self, rows):
        for kid in self.stat_grid.winfo_children():
            kid.destroy()
        by = {}
        for r in rows:
            by.setdefault(r.get("group") or "", []).append(r)
        col = 0
        for group, items in by.items():
            box = ttk.LabelFrame(self.stat_grid, text=group or "stats")
            box.grid(row=0, column=col, sticky="nsew", padx=3, pady=3)
            self.stat_grid.columnconfigure(col, weight=1)
            for i, r in enumerate(items):
                pct = "–" if not r.get("n") else f"{r['pct']:.1f}%"
                n = "" if not r.get("n") else f"n={r['n']:,}"
                on = r["key"] == self.stat_key
                lab = tk.Label(
                    box,
                    text=f"{r['label']}\n{pct}  {n}",
                    bg=ACCENT if on else PANEL, fg=BG if on else INK,
                    cursor="hand2", font=(UI, 9), padx=8, pady=6,
                    justify="left", anchor="w")
                lab.pack(fill="x", pady=1)
                lab.bind("<Button-1>",
                         lambda _e, k=r["key"]: self._pick_stat(k))
            col += 1

    def _draw_stat_chart(self, message=None):
        """The 13x13 for the selected stat, clickable by combo."""
        c = getattr(self, "stat_canvas", None)
        if c is None:
            return
        old, old_g = self.chart_canvas, self.chart
        self.chart_canvas = c
        self.chart = getattr(self, "stat_chart", None)
        try:
            self._draw_chart(message)
        finally:
            self.chart_canvas = old
            self.chart = old_g
        self._stat_cell_geom(c)

    def _stat_cell_geom(self, canvas):
        """Remember cell bounds so a click can name a combo."""
        self.stat_cells = {}
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 80 or h < 80:
            return
        top, foot = 16, 52
        size = min((w - 28) / 13.0, (h - top - foot) / 13.0)
        left = (w - size * 13) / 2.0
        for i in range(13):
            for j in range(13):
                combo = query.combo_at(i, j)
                x, y = left + j * size, top + i * size
                self.stat_cells[combo] = (x, y, x + size, y + size)

    def _click_stat_cell(self, event):
        if not self.stat_key:
            return
        for combo, (x0, y0, x1, y1) in self.stat_cells.items():
            if x0 <= event.x < x1 and y0 <= event.y < y1:
                self.stat_combo = None if self.stat_combo == combo else combo
                self.stat_hist_group = None
                self.refresh()
                return

    def _click_stat_hist(self, row):
        if not self.stat_key or not row:
            return
        key = row.get("key")
        self.stat_hist_group = None if self.stat_hist_group == key else key
        self.refresh()

    def _click_study_hist(self, row):
        if not row:
            return
        self._apply_step(_step_from_row(row))

    def _render_study(self, out):
        """Chips, breadcrumb, Smart strip, panes, compact hands."""
        self._study_out = out
        if out.get("error"):
            self._paint_crumbs()
            self._paint_chips()
            self._paint_smart({"error": out["error"]})
            return
        self._paint_crumbs()
        self._paint_chips()
        self._paint_smart(out)
        for name, tv in self.study_trees.items():
            pane = (out.get("panes") or {}).get(name)
            if name == "combo" and not pane:
                pane = out.get("families")
            self._fill_pane(name, tv, pane, out)
        self._render_hands(self.study_hands, {"rows": out.get("hands") or []})
        self.study_graph.set(out.get("graph"),
                             out.get("why") or out.get("error"))
        self.study_hist.set(out.get("hist"),
                            out.get("why") or out.get("error"))
        self._paint_related(out.get("related") or [])
        self._sync_detached()

    def _paint_crumbs(self):
        for kid in self.crumb_bar.winfo_children():
            kid.destroy()
        ttk.Label(self.crumb_bar, text="path",
                  style="Dim.TLabel").pack(side="left", padx=(0, 6))
        self._crumb_link("All", -1)
        for i, step in enumerate(self.crumbs):
            ttk.Label(self.crumb_bar, text="›",
                      style="Dim.TLabel").pack(side="left", padx=4)
            self._crumb_link(step.get("label") or step.get("value") or "?", i)

    def _crumb_link(self, text, index):
        lab = tk.Label(self.crumb_bar, text=text, bg=BG, fg=ACCENT,
                       cursor="hand2", font=(UI, 9))
        lab.pack(side="left")
        lab.bind("<Button-1>", lambda _e, i=index: self._pop_crumb(i))
        lab.bind("<Enter>", lambda _e, w=lab: w.configure(fg=INK))
        lab.bind("<Leave>", lambda _e, w=lab: w.configure(fg=ACCENT))

    def _pop_crumb(self, index):
        # -1 is All: drop the drill path, keep who and the dialog.
        self.crumbs = [] if index < 0 else self.crumbs[:index + 1]
        self.refresh()

    def _paint_chips(self):
        for kid in self.chip_bar.winfo_children():
            kid.destroy()
        ttk.Label(self.chip_bar, text="filter",
                  style="Dim.TLabel").pack(side="left", padx=(0, 6))
        if not self.crumbs and not self.preset.get():
            ttk.Label(self.chip_bar, text="unfiltered",
                      style="Dim.TLabel").pack(side="left")
        if self.preset.get():
            self._chip_label("report " + self.preset.get(), None)
        sid = self.vals["session"].get().strip()
        if sid:
            self._chip_label("session " + sid[:18], "session")
        if self.flags["--today"].get():
            self._chip_label("today", "today")
        for i, step in enumerate(self.crumbs):
            self._chip_label(step.get("label") or step.get("how") or "?", i)

    def _chip_label(self, text, index):
        lab = tk.Label(self.chip_bar, text=text + " ×", bg=PANEL, fg=INK,
                       cursor="hand2", font=(UI, 9), padx=7, pady=1)
        lab.pack(side="left", padx=3)
        lab.bind("<Button-1>", lambda _e, i=index: self._dismiss_chip(i))

    def _dismiss_chip(self, index):
        if index is None:
            self.preset.set("")
            self.crumbs = []
        elif index == "session":
            self.vals["session"].set("")
        elif index == "today":
            self.flags["--today"].set(False)
        else:
            self.crumbs = [c for i, c in enumerate(self.crumbs) if i != index]
        self.refresh()

    def _paint_smart(self, out):
        for kid in self.smart.winfo_children():
            kid.destroy()
        if out.get("error"):
            ttk.Label(self.smart, text=out["error"],
                      style="Dim.TLabel").pack(side="left")
            return
        summ = out.get("summary") or {}
        prof = out.get("profit") or {}
        bits = []
        if summ.get("opps"):
            bits.append(f"{summ['hits']:,}/{summ['opps']:,}  {summ['pct']:.1f}%")
            bits.append(f"{summ['per_1k']:.1f}/1k")
        if prof.get("bb_per_hand") is not None:
            bits.append(f"AP {prof['bb_per_hand']:+.2f} bb")
        ttk.Label(self.smart, text="  ·  ".join(bits) or "Smart",
                  style="Dim.TLabel").pack(side="left")
        cmp = out.get("compare")
        if cmp:
            a, b = cmp.get("a") or {}, cmp.get("b") or {}
            ttk.Label(self.smart,
                      text=f"   pin {(b.get('name') or 'pinned')[:22]}",
                      style="Dim.TLabel").pack(side="left", padx=(10, 0))
        fams = (out.get("families") or {}).get("rows") or []
        if fams:
            ttk.Label(self.smart, text="  combos",
                      style="Dim.TLabel").pack(side="left", padx=(14, 4))
        for r in fams[:8]:
            lab = tk.Label(self.smart, text=r["label"], bg=BG, fg=ACCENT,
                           cursor="hand2", font=(UI, 9), padx=5)
            lab.pack(side="left")
            lab.bind("<Button-1>",
                     lambda _e, row=r: self._apply_step(_step_from_row(row)))
            lab.bind("<Enter>", lambda _e, w=lab: w.configure(fg=INK))
            lab.bind("<Leave>", lambda _e, w=lab: w.configure(fg=ACCENT))

    def _fill_pane(self, name, tv, pane, out):
        tv.delete(*tv.get_children())
        if not pane or not pane.get("rows"):
            self._cols(tv, ("msg",), (360,), {"msg": "w"})
            tv.insert("", "end", values=("nothing in this pane",),
                      tags=("note",))
            return
        hidden = set(self.pane_hidden.get(name, set()))
        if name == "position" and name not in self.pane_hidden:
            pack = set(pane.get("cols") or [])
            hidden |= {c for c in ("vpip", "pfr") if c not in pack}
        cols, widths, anchors, render = _pane_columns(name, pane, hidden)
        self._cols(tv, cols, widths, anchors)
        for c in cols:
            tv.heading(c, text=c, anchor=anchors.get(c, "e"),
                       command=lambda col=c, n=name: self._sort_pane(n, col))
        rows = list(pane["rows"])
        sort = self.pane_sort.get(name)
        if sort:
            col, rev = sort
            rows.sort(key=lambda r: _pane_sort_key(r, col), reverse=rev)
        tv._study_rows = {}
        for r in rows:
            vals = [render[c](r) for c in cols]
            iid = tv.insert("", "end",
                            tags=("thin",) if r.get("n", 0) < 30 else (),
                            values=vals)
            tv._study_rows[iid] = r
        if name == "position" and hidden & {"vpip", "pfr"}:
            tv.insert("", "end",
                      values=["VPIP/PFR off on postflop packs — ⚙ to show"]
                      + [""] * (len(cols) - 1),
                      tags=("note",))

    def _sort_pane(self, name, col):
        cur = self.pane_sort.get(name)
        rev = bool(cur and cur[0] == col and not cur[1])
        self.pane_sort[name] = (col, rev)
        if self._study_out:
            self._render_study(self._study_out)

    def _pane_gear(self, name):
        pane = ((self._study_out or {}).get("panes") or {}).get(name) or {}
        available = _pane_available(name, pane) if name not in (
            "graph", "hands") else []
        # Detach is always on the gear, even when a pane has no
        # columns to hide -- Graph and Hands have no column list
        # and still have to open a window.
        menu = tk.Menu(self, tearoff=0, background=PANEL, foreground=INK,
                       activebackground=EDGE)
        menu.add_command(label="Detach",
                         command=lambda n=name: self._detach_pane(n))
        hidden = set(self.pane_hidden.get(name, set()))
        for col in available:
            on = col not in hidden

            def toggle(c=col, n=name, currently=on):
                gone = set(self.pane_hidden.get(n, set()))
                if currently:
                    gone.add(c)
                else:
                    gone.discard(c)
                self.pane_hidden[n] = gone
                if self._study_out:
                    self._render_study(self._study_out)

            menu.add_checkbutton(label=col, command=toggle)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _alive_detached(self):
        alive = []
        for w in self._detached:
            try:
                if w.winfo_exists():
                    alive.append(w)
            except tk.TclError:
                pass
        self._detached = alive
        return alive

    def _detach_pane(self, name):
        """
        Open this pane in its own window.

        Live: the window redraws from the latest study payload, so
        a drill in main (or in the copy) reshapes it. Pin stays on
        the main bar -- a detached pane is a viewport, not a second
        report. Dock-back is deferred.
        """
        if name not in query.DETACHABLE:
            return None
        alive = self._alive_detached()
        for w in alive:
            if getattr(w, "pane", None) == name:
                try:
                    w.lift()
                    w.focus_force()
                except tk.TclError:
                    pass
                return w
        if not query.can_detach(len(alive)):
            self.status.configure(
                text=f"detach cap {query.DETACH_CAP} — close one")
            return None
        win = DetachedPane(self, name)
        self._detached.append(win)
        return win

    def _sync_detached(self):
        out = self._study_out
        title_argv = self.argv()
        for w in self._alive_detached():
            try:
                w.sync(out, title_argv)
            except tk.TclError:
                pass

    def _forget_detached(self, win):
        self._detached = [w for w in self._detached if w is not win]

    def _drill_pane(self, name, event):
        tv = event.widget if event is not None else self.study_trees.get(name)
        if not tv:
            return
        iid = tv.identify_row(event.y)
        if not iid:
            return
        row = getattr(tv, "_study_rows", {}).get(iid)
        if not row:
            return
        self._apply_step(_step_from_row(row))

    def _apply_step(self, step):
        if not step:
            return
        if len(self.crumbs) >= query.DRILL_MAX:
            # Replace the last rather than grow past three -- a fourth
            # AND is almost always empty and looks like a broken pane.
            self.crumbs = self.crumbs[:-1] + [step]
        else:
            self.crumbs.append(step)
        self.refresh()

    def _pane_pin_menu(self, name, event):
        tv = event.widget if event is not None else self.study_trees.get(name)
        if not tv:
            return
        iid = tv.identify_row(event.y)
        row = getattr(tv, "_study_rows", {}).get(iid) if iid else None
        if not row:
            return
        step = _step_from_row(row)
        menu = tk.Menu(self, tearoff=0, background=PANEL, foreground=INK,
                       activebackground=EDGE)
        menu.add_command(label="Drill", command=lambda: self._apply_step(step))
        menu.add_command(label="Pin this", command=lambda: self._pin_step(step))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _pin_step(self, step):
        """Pin the other row of this pane, same parent filter."""
        b = query.without_who(query.drill_child(self.argv(), step))
        label = step.get("label") or step.get("value") or "pinned"
        key = f"pin {label}"
        self._pin_alias[key] = json.dumps(b)
        known = list(query.reports())
        extras = [k for k in self._pin_alias if k not in known]
        self.pin_box.configure(values=[""] + known + extras)
        self.pin.set(key)
        self.refresh()

    def _hand_menu(self, event):
        tv = event.widget
        iid = tv.identify_row(event.y)
        if iid:
            tv.selection_set(iid)
            tv.focus(iid)
        menu = tk.Menu(self, tearoff=0, background=PANEL, foreground=INK,
                       activebackground=EDGE)
        menu.add_command(label="Replay", command=lambda: self._open_hand(event))
        menu.add_command(label="Mark", command=lambda: self._mark_selected(True))
        menu.add_command(label="Unmark",
                         command=lambda: self._mark_selected(False))
        menu.add_command(label="Add to Note", command=self._note_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _drill_stat(self, _event):
        """Open the clicked stat or next-action as a filter on this spot."""
        tv = _event.widget
        iid = tv.focus()
        if not iid or ":" not in iid:
            return
        kind, key = iid.split(":", 1)
        if not key:
            return
        if kind == "stat":
            self.multi["quick"] = {key}
            self.refresh()
        elif kind == "after":
            self.vals["after"].set(key)
            self.refresh()
        elif kind == "then":
            self.vals["then"].set(key)
            self.refresh()
        elif kind == "outcome":
            self.vals["outcome"].set(key)
            self.refresh()
        elif kind == "size":
            self.vals["size"].set(key)
            self.refresh()

    def _render_stats(self, tv, out):
        self._cols(tv, ("stat", "value", "±", "n", "act bb"),
                   (220, 80, 60, 100, 80),
                   {"stat": "w"})
        self._stat_iids = {}
        cmp = out.get("compare")
        if cmp:
            a, b = cmp["a"], cmp["b"]
            sa, sb = a.get("summary") or {}, b.get("summary") or {}
            pa, pb = a.get("profit") or {}, b.get("profit") or {}
            tv.insert("", "end", values=("THIS vs PINNED", "", "", "", ""),
                      tags=("group",))
            tv.insert("", "end", values=(
                "", (a.get("name") or "this")[:22],
                "", (b.get("name") or "pinned")[:22], ""))
            tv.insert("", "end", values=(
                "hits / opps",
                f"{sa.get('hits', 0):,} / {sa.get('opps', 0):,}",
                "",
                f"{sb.get('hits', 0):,} / {sb.get('opps', 0):,}", ""))
            tv.insert("", "end", values=(
                "freq",
                f"{sa.get('pct', 0):.1f}%" if sa.get("opps") else "–",
                "",
                f"{sb.get('pct', 0):.1f}%" if sb.get("opps") else "–", ""))
            tv.insert("", "end", values=(
                "hits / 1000",
                f"{sa.get('per_1k', 0):.1f}", "",
                f"{sb.get('per_1k', 0):.1f}", ""))
            this_ap = (f"{pa['bb_per_hand']:+.2f} bb"
                       if pa.get("bb_per_hand") is not None else "–")
            pin_ap = (f"{pb['bb_per_hand']:+.2f} bb"
                      if pb.get("bb_per_hand") is not None else "–")
            tv.insert("", "end", values=(
                "action profit", this_ap, _profit_band(pa), pin_ap, ""))
            ca, cb = a.get("call_profit") or {}, b.get("call_profit") or {}
            this_cp = (f"{ca['bb_per_hand']:+.2f} bb"
                       if ca.get("bb_per_hand") is not None else "–")
            pin_cp = (f"{cb['bb_per_hand']:+.2f} bb"
                      if cb.get("bb_per_hand") is not None else "–")
            tv.insert("", "end", values=(
                "call profit", this_cp, _profit_band(ca), pin_cp, ""))
            wa, wb = a.get("amount_won") or {}, b.get("amount_won") or {}
            if wa.get("hands") or wb.get("hands"):
                this_w = (f"{wa['bb_per_hand']:+.2f} bb"
                          if wa.get("bb_per_hand") is not None else "–")
                pin_w = (f"{wb['bb_per_hand']:+.2f} bb"
                         if wb.get("bb_per_hand") is not None else "–")
                tv.insert("", "end", values=(
                    "Won$", this_w, f"n={wa.get('hands') or 0:,}",
                    pin_w, ""))
            diff = cmp.get("freq_diff") or {}
            if diff.get("d") is not None:
                tv.insert("", "end", values=(
                    "freq this − pin",
                    f"{100 * diff['d']:+.1f} pts",
                    f"[{100 * diff['lo']:+.1f}, {100 * diff['hi']:+.1f}]",
                    "", ""), tags=("note",))
            _render_compare_sizes(tv, cmp)
            _render_compare_packs(tv, cmp)
        else:
            summ = out.get("summary")
            if summ and summ["opps"]:
                tv.insert("", "end", values=("HITS / OPPORTUNITIES", "", "", "", ""),
                          tags=("group",))
                tv.insert("", "end", values=(
                    f"hits  ({summ['label']})", f"{summ['hits']:,}", "",
                    f"{summ['opps']:,} opps", ""))
                tv.insert("", "end", values=(
                    "hits / 1000 hands", f"{summ['per_1k']:.1f}",
                    f"±{summ['band']:.1f}" if summ["band"] < 1
                    else f"±{summ['band']:.0f}",
                    f"{summ['hands']:,} hands", ""))
                tv.insert("", "end", values=(
                    f"{summ['label']}", f"{summ['pct']:.1f}%",
                    f"±{summ['band']:.1f}" if summ["band"] < 1
                    else f"±{summ['band']:.0f}",
                    f"{summ['opps']:,}", ""))
        pinned = out.get("pinned")
        if pinned and pinned.get("error"):
            tv.insert("", "end", values=(pinned["error"], "", "", "", ""),
                      tags=("note",))
        prof = out.get("profit")
        if prof and prof["n"]:
            if prof["bb_per_hand"] is not None:
                tv.insert("", "end", values=(
                    "action profit", f"{prof['bb_per_hand']:+.2f} bb",
                    _profit_band(prof),
                    f"{prof['priced']:,} of {prof['n']:,}", ""))
            else:
                tv.insert("", "end", values=(
                    "action profit", "unpriced", "", f"{prof['n']:,}", ""),
                    tags=("note",))
            tv.insert("", "end", values=(prof["note"], "", "", "", ""),
                      tags=("note",))
            if prof.get("interval_note"):
                tv.insert("", "end", values=(prof["interval_note"], "", "", "", ""),
                          tags=("note",))
            for edge in prof.get("edges") or []:
                tv.insert("", "end", values=(edge, "", "", "", ""),
                          tags=("note",))
        callp = out.get("call_profit")
        if callp and callp["n"]:
            if callp["bb_per_hand"] is not None:
                tv.insert("", "end", values=(
                    "call profit", f"{callp['bb_per_hand']:+.2f} bb",
                    _profit_band(callp),
                    f"{callp['priced']:,} of {callp['n']:,}",
                    ""))
            else:
                tv.insert("", "end", values=(
                    "call profit", "unpriced", "", f"{callp['n']:,}", ""),
                    tags=("note",))
            tv.insert("", "end", values=(callp["note"], "", "", "", ""),
                      tags=("note",))
            if callp.get("interval_note"):
                tv.insert("", "end", values=(
                    callp["interval_note"], "", "", "", ""), tags=("note",))
            for edge in callp.get("edges") or []:
                tv.insert("", "end", values=(edge, "", "", "", ""),
                          tags=("note",))
        won = out.get("amount_won")
        if won and won.get("hands"):
            band = (_profit_band(won) if won.get("lo") is not None
                    else f"±{won['error']:.0f} bb/100")
            tv.insert("", "end", values=(
                "Won$", f"{won['bb_per_hand']:+.2f} bb",
                band, f"{won['hands']:,} hands", ""))
            wband = (f"[{won['won_lo']:.0f}, {won['won_hi']:.0f}]"
                     if won.get("won_lo") is not None else "")
            tv.insert("", "end", values=(
                "Won hand%", f"{won['won_pct']:.1f}%", wband,
                f"{won['won_hands']:,} of {won['hands']:,}", ""))
            tv.insert("", "end", values=(won["note"], "", "", "", ""),
                      tags=("note",))
        cr = out.get("cohort_range")
        if cr:
            tv.insert("", "end", values=("PREFLOP RANGE -- THIS COHORT",
                                        "", "", "", ""), tags=("group",))
            cov = cr.get("coverage") or {}
            tv.insert("", "end", values=(
                "hole cards shown",
                f"{cr.get('seen', 0):,} of {cr.get('total', 0):,}",
                f"{cov.get('pct', 0):.0f}%", "", ""), tags=("note",))
            for s in cov.get("sites") or []:
                tv.insert("", "end", values=(
                    s.get("site") or "?",
                    f"{s['seen']:,}/{s['total']:,}",
                    f"{s['pct']:.0f}%", s.get("note") or "", ""),
                    tags=("note",))
            if cov.get("note"):
                tv.insert("", "end", values=(cov["note"], "", "", "", ""),
                          tags=("note",))
            if cr.get("top"):
                tv.insert("", "end", values=(
                    "most of it",
                    ", ".join(f"{t['combo']} {t['pct']:.1f}%"
                              for t in cr["top"]),
                    "", "", ""), tags=("note",))
        acts = out.get("actions") or {}
        if acts.get("mix"):
            tv.insert("", "end", values=("THIS SPOT", "", "", "", ""),
                      tags=("group",))
            for r in acts["mix"]:
                tv.insert("", "end", tags=("thin",) if r["n"] < 30 else (),
                          values=(r["label"], f"{r['pct']:.1f}%",
                                  f"±{r['band']:.1f}" if r["band"] < 1
                                  else f"±{r['band']:.0f}",
                                  f"{r['n']:,}", ""))
            for r in acts.get("extra") or []:
                tv.insert("", "end", tags=("thin",) if r["n"] < 30 else (),
                          values=(r["label"], f"{r['pct']:.1f}%",
                                  f"±{r['band']:.1f}" if r["band"] < 1
                                  else f"±{r['band']:.0f}",
                                  f"{r['n']:,}", ""))
        outs = out.get("outcomes") or {}
        if outs.get("rows"):
            tv.insert("", "end", values=("OUTCOME", "", "", "", ""),
                      tags=("group",))
            for r in outs["rows"]:
                tv.insert("", "end", iid=f"outcome:{r['key']}",
                          tags=("thin",) if r["n"] < 30 else (),
                          values=(r["label"], f"{r['pct']:.1f}%",
                                  f"±{r['band']:.1f}" if r["band"] < 1
                                  else f"±{r['band']:.0f}",
                                  f"{r['k']:,}", ""))
        for title, key, blob in (
                ("FACED NEXT  (the other seat)", "after", out.get("faced")),
                ("NEXT ACTIONS  (this player)", "then", out.get("next"))):
            if not (blob and blob.get("rows")):
                continue
            tv.insert("", "end", values=(title, "", "", "", ""), tags=("group",))
            for r in blob["rows"]:
                iid = f"{key}:{r['key']}" if r.get("key") else ""
                ap = (r.get("profit") or {})
                act = (f"{ap['bb_per_hand']:+.2f}" if ap.get("bb_per_hand")
                       is not None else "–")
                tv.insert("", "end", iid=iid or None,
                          tags=("thin",) if r["n"] < 30 else (),
                          values=(r["label"], f"{r['pct']:.1f}%",
                                  f"±{r['band']:.1f}" if r["band"] < 1
                                  else f"±{r['band']:.0f}",
                                  f"{r.get('hits', r['k']):,} / {r.get('opps', r['n']):,}",
                                  act))
        if not (out.get("compare") or {}).get("sizes"):
            sizes = out.get("sizes") or {}
            if sizes.get("rows"):
                tv.insert("", "end", values=("BET SIZES", "", "", "", ""),
                          tags=("group",))
                for r in sizes["rows"]:
                    ap = r.get("profit") or {}
                    act = (f"{ap['bb_per_hand']:+.2f}"
                           if ap.get("bb_per_hand") is not None else "–")
                    tv.insert("", "end", iid=f"size:{r['key']}",
                              tags=("thin",) if r["n"] < 30 else (),
                              values=(r["label"], f"{r['pct']:.1f}%",
                                      f"±{r['band']:.1f}" if r["band"] < 1
                                      else f"±{r['band']:.0f}",
                                      f"{r['hits']:,} / {r['opps']:,}",
                                      act))
        group = None
        for r in out["rows"]:
            if r["group"] != group:
                group = r["group"]
                tv.insert("", "end", values=(group.upper(), "", "", "", ""),
                          tags=("group",))
            tv.insert("", "end", iid=f"stat:{r['key']}",
                      tags=("thin",) if r["n"] < 30 else (),
                      values=(r["label"], f"{r['pct']:.1f}%",
                              # A band under a point still has a size, and
                              # "±0" reads as a number that failed to print.
                              f"±{r['band']:.1f}" if r["band"] < 1
                              else f"±{r['band']:.0f}",
                              f"{r['n']:,}", ""))

    def _render_range(self, tv, out):
        """
        What the hands that got here actually were, and how much of it is air.

        The bar is drawn out of block characters rather than as a canvas.
        This is a table, the numbers beside it are the answer, and a bar is
        there to be glanced at down the column -- a drawing would need its
        own widget, its own resize handling and its own theme, to say the
        same thing slightly better.
        """
        self._cols(tv, ("hand", "%", "n", "", "share"),
                   (150, 70, 80, 60, 260),
                   {"hand": "w", "": "w", "share": "w"})
        if not out["n"]:
            tv.insert("", "end", tags=("note",), values=(
                "no hand under this filter was ever shown", "", "", "", ""))
            return
        for r in out["rows"]:
            tv.insert("", "end", tags=("neg",) if r["weak"] else (),
                      values=(r["made"], f"{r['pct']:.1f}%", f"{r['n']:,}",
                              "weak" if r["weak"] else "",
                              "█" * int(round(r["pct"] / 2))))
        tv.insert("", "end", values=("", "", "", "", ""))
        tv.insert("", "end", tags=("neg",), values=(
            "WEAK", f"{out['weak']:.1f}%", "", "cannot call", ""))
        tv.insert("", "end", tags=("pos",), values=(
            "STRONG", f"{out['strong']:.1f}%", "", "", ""))
        if out["draws"]:
            tv.insert("", "end", values=("", "", "", "", ""))
            tv.insert("", "end", tags=("group",),
                      values=("OVERLAPPING THE ABOVE", "", "", "", ""))
            for d in out["draws"]:
                tv.insert("", "end", values=(
                    d["label"], f"{d['pct']:.1f}%", f"{d['n']:,}", "", ""))
        tv.insert("", "end", values=("", "", "", "", ""))
        tv.insert("", "end", tags=("note",), values=(
            f"{out['n']:,} of {out['total']:,} decisions had cards to read "
            f"({100 * out['n'] / out['total']:.0f}%) — this is the range that "
            f"was SEEN, and on ACR that is the showdown half",
            "", "", "", ""))
        cov = out.get("coverage") or {}
        for s in cov.get("sites") or []:
            tv.insert("", "end", tags=("note",), values=(
                f"{s.get('site') or '?'}: {s['seen']:,}/{s['total']:,} "
                f"({s['pct']:.0f}%) — {s.get('note') or ''}",
                "", "", "", ""))
        if cov.get("note"):
            tv.insert("", "end", tags=("note",), values=(
                cov["note"], "", "", "", ""))

    def _render_report(self, tv, out):
        cmp = out.get("compare")
        if cmp and (cmp.get("sizes") or cmp.get("by")):
            self._render_report_compare(tv, out, cmp)
            return
        if out.get("dim") == "size" or out.get("sizes"):
            self._render_report_sizes(tv, out.get("sizes") or {})
            return
        cols = ["by"] + [BY_KEY[c].label for c in out["cols"]] + ["n"]
        widths = [130] + [95] * len(out["cols"]) + [80]
        if out.get("won_by"):
            cols += ["Won$ bb/100", "Won hand%"]
            widths += [110, 90]
        self._cols(tv, tuple(cols), widths, {"by": "w"})
        if not out["keys"]:
            tv.insert("", "end", values=["nothing matches"] + [""] * len(cols[1:]),
                      tags=("neg",))
            if out.get("why"):
                tv.insert("", "end",
                          values=[out["why"]] + [""] * len(cols[1:]),
                          tags=("note",))
            return
        for k in out["keys"]:
            row = [str(k)]
            thin = False
            for c in out["cols"]:
                n, kk = out["grid"][c].get(k, (0, 0))
                row.append("–" if not n else f"{100 * kk / n:.1f}%")
                thin = thin or (0 < n < 30)
            n_here = out["counts"].get(k, 0)
            if isinstance(n_here, tuple):
                n_here = n_here[0]
            row.append(f"{n_here:,}")
            w = (out.get("won_by") or {}).get(k)
            if out.get("won_by"):
                if not w:
                    row.append("–")
                    row.append("–")
                else:
                    row.append(f"{w['bb100']:+.0f} ±{w['error']:.0f}")
                    row.append(f"{w['won_pct']:.0f}%")
            tv.insert("", "end", values=row, tags=("thin",) if thin else ())
        if out.get("won_by"):
            tv.insert("", "end",
                      values=["Won$ is whole-hand net_bb of these seats, "
                              "spots-sourced; MTT out. Not this street."]
                      + [""] * (len(cols) - 1),
                      tags=("note",))

    def _render_report_sizes(self, tv, sizes):
        """The Bet Sizes pane in the report tab."""
        self._cols(tv, ("size", "freq", "hits / opps", "act bb"),
                   (160, 80, 110, 80), {"size": "w"})
        rows = sizes.get("rows") or []
        if not rows:
            tv.insert("", "end", values=("no sized action in this filter",
                                         "", "", ""), tags=("note",))
            return
        for r in rows:
            ap = r.get("profit") or {}
            act = (f"{ap['bb_per_hand']:+.2f}"
                   if ap.get("bb_per_hand") is not None else "–")
            tv.insert("", "end", iid=f"size:{r['key']}",
                      tags=("thin",) if r["n"] < 30 else (),
                      values=(r["label"], f"{r['pct']:.1f}%",
                              f"{r['hits']:,} / {r['opps']:,}", act))
        tv.insert("", "end", values=(
            "freq is this size of the parent filter. Checks and folds "
            "have no size. act bb is Action Profit v1; later pot on a "
            "called bet stays unpriced.", "", "", ""), tags=("note",))

    def _render_report_compare(self, tv, out, cmp):
        """Two `--by` grids or two Bet Sizes tables, THIS vs PINNED."""
        if cmp.get("sizes"):
            self._cols(tv, ("size", "this hits/freq", "this AP",
                            "pin hits/freq", "pin AP"),
                       (160, 140, 80, 140, 80), {"size": "w"})
            tv.insert("", "end", values=(
                "THIS vs PINNED",
                (cmp["a"].get("name") or "this")[:22], "",
                (cmp["b"].get("name") or "pinned")[:22], ""),
                      tags=("group",))
            _render_compare_sizes(tv, cmp)
            return
        blob = cmp.get("by") or {}
        ga, gb = blob.get("a") or {}, blob.get("b") or {}
        dim = blob.get("dim") or out.get("dim") or "by"
        cols = ga.get("cols") or gb.get("cols") or out.get("cols") or []
        order = query.DIMENSIONS[dim][1] if dim in query.DIMENSIONS else str
        keys = sorted(set(ga.get("keys") or []) | set(gb.get("keys") or []),
                      key=lambda k: order(k) if k is not None else "")
        headers = ["by"]
        widths = [120]
        for side in ("this", "pin"):
            for c in cols:
                headers.append(f"{side} {BY_KEY[c].label[:8]}")
                widths.append(90)
            headers.append(f"{side} n")
            widths.append(70)
        self._cols(tv, tuple(headers), widths, {"by": "w"})
        tv.insert("", "end",
                  values=[f"THIS vs PINNED  by {dim}"] + [""] * (len(headers) - 1),
                  tags=("group",))
        if not keys:
            tv.insert("", "end",
                      values=["nothing matches"] + [""] * (len(headers) - 1),
                      tags=("neg",))
            return
        for k in keys:
            row = [str(k)]
            thin = False
            for grid, counts in ((ga.get("grid") or {}, ga.get("counts") or {}),
                                 (gb.get("grid") or {}, gb.get("counts") or {})):
                for c in cols:
                    n, kk = grid.get(c, {}).get(k, (0, 0))
                    row.append("–" if not n else f"{100 * kk / n:.1f}%")
                    thin = thin or (0 < n < 30)
                row.append(f"{counts.get(k, 0):,}")
            tv.insert("", "end", values=row, tags=("thin",) if thin else ())

    def _render_results(self, tv, out):
        self._cols(tv, ("figure", "value"), (320, 220), {"figure": "w"})
        t = out["totals"]
        rows = [("hands", f"{t['hands']:,}"),
                ("net", f"{t['net_bb']:+,.1f} bb"),
                ("in money", f"{t['money']:+,.2f}"),
                ("per 100 hands", f"{t['bb100']:+.1f} bb/100"),
                ("error on that", f"±{t['error']:.0f} bb/100"),
                ("saw a flop", f"{t['saw_flop']:,}"),
                ("won at showdown", f"{t['wtsd']:,}")]
        for name, val in rows:
            tag = ()
            if name in ("net", "per 100 hands"):
                tag = ("pos",) if val.startswith("+") else ("neg",)
            tv.insert("", "end", values=(name, val), tags=tag)
        tv.insert("", "end", values=("", ""))
        tv.insert("", "end", tags=("note",), values=(
            "one hand's result has a standard deviation near 11.7bb, so the "
            "error on a win rate is about 1170/√n", ""))

    def _render_hands(self, tv, out):
        # Compact replaces the board column: the board is already in
        # the line, and a Treeview cannot underline a substring so the
        # focus seat's action is marked _R3_. Double-click still opens
        # the full replay -- that path is unchanged.
        self._cols(tv, ("*", "when", "site", "bb", "pos", "hand", "net bb",
                        "act bb", "call bb", "compact"),
                   (36, 140, 90, 60, 60, 70, 80, 80, 80, 440),
                   {"*": "w", "when": "w", "site": "w", "pos": "w", "hand": "w",
                    "compact": "w"})
        self._hand_ids = {}
        tv._hand_ids = {}
        for r in out["rows"]:
            net, act, call = r.get("net"), r.get("act"), r.get("call")
            star = "*" if r.get("marked") else ""
            extra = ",".join(r.get("tags") or [])
            compact_line = r.get("compact") or r.get("board") or ""
            if extra:
                compact_line = f"[{extra}]  {compact_line}"
            iid = tv.insert("", "end", values=(
                star,
                (r.get("when") or "")[:16], r.get("site") or "",
                f"{r['bb']:g}" if r.get("bb") else "",
                r.get("pos") or "", r.get("combo") or "–",
                f"{net:+.1f}" if net is not None else "",
                f"{act:+.1f}" if act is not None else "–",
                f"{call:+.1f}" if call is not None else "–",
                compact_line),
                tags=("pos",) if (act or 0) > 0 else
                     ("neg",) if (act or 0) < 0 else ())
            self._hand_ids[iid] = (r["id"], r["seat"])
            tv._hand_ids[iid] = (r["id"], r["seat"])
        if out["rows"]:
            tv.insert("", "end", values=(
                "", "", "", "", "", "", "", "", "", ""))
            tv.insert("", "end", tags=("note",),
                      values=("", "act bb is Action Profit; call bb is "
                              "Call Profit Rate (actual calls). "
                              "net bb is the hand. – is unpriced. "
                              "_marked_ actions are this row's seat. "
                              "Mark / Unmark / Note on the row. "
                              "Double-click to replay.",
                              "", "", "", "", "", "", "", ""))

    def _selected_hand(self, tv=None):
        extra = []
        for w in getattr(self, "_detached", []) or []:
            extra.append(getattr(w, "hands", None))
        trees = [tv, getattr(self, "study_hands", None),
                 getattr(self, "session_hands", None),
                 getattr(self, "stat_hands", None),
                 (self.tree or {}).get("hands")] + extra
        for t in trees:
            if t is None:
                continue
            ids = getattr(t, "_hand_ids", None) or getattr(self, "_hand_ids", {})
            sel = t.selection()
            if sel and sel[0] in ids:
                return ids[sel[0]]
        return None

    def _open_hand(self, event=None):
        tv = event.widget if event is not None else None
        got = self._selected_hand(tv)
        if not got:
            return
        hid, seat = got
        HandWindow(self, self.con, hid, seat)

    def _mark_selected(self, star):
        got = self._selected_hand()
        if not got:
            return
        hid, _seat = got
        raw = self.hand_tag.get().strip()
        tags = [x.strip() for x in raw.split(",") if x.strip()]
        if star:
            notes.mark(hid, tags)
        else:
            notes.unmark(hid, tags or None)
        self.refresh()

    def _note_selected(self):
        got = self._selected_hand()
        if not got:
            return
        hid, seat = got
        text = simpledialog.askstring(
            "Note", f"Note on {hid}", parent=self.master)
        if not text or not text.strip():
            return
        hands_con = sqlite3.connect(DB)
        hands_con.row_factory = sqlite3.Row
        try:
            notes.add(text.strip(), hand_id=hid, seat=seat,
                      hands_con=hands_con)
        finally:
            hands_con.close()
        self.refresh()

    def _draw_chart(self, message=None):
        """
        The 13x13 chart, in the shape every range chart is drawn in.

        Shaded against the biggest cell rather than against 100%, and the
        caption says so. A range's combos are each under three percent of
        it, so shading them on an absolute scale produces a chart that is
        uniformly almost black -- technically honest and completely
        unreadable, which is a worse kind of dishonest.

        A rate is shaded absolutely, because there 100% means something.
        """
        c = self.chart_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 80:
            return
        g = self.chart
        if not g:
            c.create_text(w / 2, h / 2, fill=DIM, font=(UI, 10), width=w - 60,
                          justify="center",
                          text=message or "no hand in this filter showed its "
                                          "cards, so there is no range to draw")
            return

        rate = g["mode"] == "rate"
        top, foot = 16, 52
        size = min((w - 28) / 13.0, (h - top - foot) / 13.0)
        left = (w - size * 13) / 2.0
        peak = max(1e-9, g["peak"] / max(1, g["seen"]))
        # Below this the two lines of text collide, so the value is
        # dropped and the label kept. It was set by measuring the window:
        # at 1360x880 the chart gets about 530 pixels of height and so 40 to
        # a cell, and a threshold above that hid the numbers on every chart
        # anybody would actually see.
        small = size < 32

        for i in range(13):
            for j in range(13):
                combo = query.combo_at(i, j)
                n, k = g["cells"].get(combo, (0, None))
                if rate:
                    value = None if n < g["min_n"] else k / n
                    weight = value or 0.0
                else:
                    value = (n / g["seen"]) if n and g["seen"] else None
                    weight = (value or 0.0) / peak
                x, y = left + j * size, top + i * size
                c.create_rectangle(
                    x, y, x + size, y + size, width=1, outline=BG,
                    fill=PANEL if value is None else blend(PANEL, ACCENT,
                                                           weight))
                # Ink that stays legible over both ends of the shading. Dark
                # text on the deepest cells and light on the rest; one
                # colour for all of them made either the strongest squares
                # or the empty ones unreadable, and the strongest squares
                # are the ones being looked at.
                ink = DIM if value is None else (BG if weight > 0.6 else INK)
                c.create_text(x + size / 2, y + size / 2 - (0 if small else 7),
                              text=combo, fill=ink,
                              font=(UI, 7 if small else 9))
                if not small and value is not None:
                    c.create_text(x + size / 2, y + size / 2 + 8, fill=ink,
                                  font=(UI, 8),
                                  text=(f"{100 * value:.0f}%" if rate
                                        else f"{100 * value:.1f}"))

        seen, total = g["seen"], g["total"]
        share = 100.0 * seen / total if total else 0.0
        c.create_text(left, top + size * 13 + 12, anchor="nw", fill=DIM,
                      font=(UI, 9),
                      text=(f"{g['stat']} per combo, shaded 0-100%."
                            f"  Blank: dealt fewer than {g['min_n']} times."
                            if rate else
                            f"Each combo's share of the range, shaded "
                            f"against the biggest cell ({100 * peak:.1f}%)."))
        c.create_text(left, top + size * 13 + 30, anchor="nw", fill=DIM,
                      font=(UI, 9),
                      text=f"{seen:,} of {total:,} player-hands showed cards "
                           f"({share:.0f}%)"
                           + ("" if share > 90 else
                              " -- this is the range that was SEEN, which on "
                              "ACR is the hands that got to showdown"))
        cov = g.get("coverage") or {}
        extra = []
        for s in cov.get("sites") or []:
            extra.append(f"{s.get('site') or '?'}: {s['seen']:,}/{s['total']:,} "
                         f"({s['pct']:.0f}%) {s.get('note') or ''}")
        if extra:
            c.create_text(left, top + size * 13 + 48, anchor="nw", fill=DIM,
                          font=(UI, 8),
                          text="  ·  ".join(extra))
        if cov.get("note"):
            c.create_text(left, top + size * 13 + 64, anchor="nw", fill=DIM,
                          font=(UI, 8),
                          text=cov["note"])

    # ---- the graph, drawn rather than served ---------------------------
    def _draw_graph(self, message=None):
        self.win_graph.set(self.series, message)

    # ---- what this database holds -------------------------------------
    def load_options(self):
        """What this database holds, for the dialog to offer."""
        one = lambda sql: [r[0] for r in self.con.execute(sql)
                           if r[0] is not None]
        self.options["sites"] = one(
            "SELECT DISTINCT site FROM decisions ORDER BY 1")
        self.options["stakes"] = [f"{v:g}" for v in one(
            "SELECT DISTINCT bb FROM decisions ORDER BY 1")]
        self.options["players"] = [HERO_CHOICE] + one(
            "SELECT player FROM decisions WHERE player IS NOT NULL "
            "GROUP BY player HAVING COUNT(DISTINCT hand_id) >= 100 "
            "ORDER BY COUNT(DISTINCT hand_id) DESC LIMIT 300")
        hands = self.con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
        self.sub.configure(
            text=f"{' · '.join(self.options['sites'])}   {hands:,} hands")

    def describe_filter(self):
        """The active filter as a sentence, for the bar above the answer."""
        try:
            cohort_spec, query_argv = players.parse_cohort(self.argv())
            _w, label, _p = query.build(query_argv)
        except SystemExit:
            return "…"
        if cohort_spec is not None:
            label += ", cohort: " + players.describe_cohort(cohort_spec)
        return "all hands" if label == "everything" else label


def _step_from_row(row):
    """A pane row as the crumb `drill_child` understands."""
    if row.get("composed") or (row.get("argv") and not row.get("flag")):
        return {"label": row.get("label") or row.get("key"),
                "argv": list(row.get("argv") or []),
                "how": row.get("how")}
    if not row.get("flag"):
        return None
    return {"label": row.get("label") or str(row.get("value") or row.get("key")),
            "flag": row["flag"], "value": str(row.get("value") or row["key"]),
            "how": row.get("how")}


def _fmt_ap(row):
    ap = row.get("profit") or {}
    if ap.get("bb_per_hand") is None:
        return "–"
    return f"{ap['bb_per_hand']:+.2f}"


def _pane_available(name, pane):
    base = {
        "results": ["label", "hands", "net", "bb/100"],
        "stack": ["label", "n", "freq", "act bb"],
        "position": ["label", "n", "freq"] + list(pane.get("cols") or []),
        "next": ["label", "freq", "hits/opps", "act bb"],
        "size": ["label", "freq", "hits/opps", "act bb"],
        "made": ["label", "n", "freq"],
        "board": ["label", "n", "freq"],
        "combo": ["label", "n", "freq"],
        "hist": ["label", "n", "freq", "weak"],
    }.get(name, ["label", "n", "freq"])
    # Gear can put VPIP/PFR back on a postflop pack; they stay off
    # until asked because columns_for already dropped them.
    if name == "position":
        for extra in ("vpip", "pfr"):
            if extra not in base:
                base.append(extra)
    return base


def _pane_columns(name, pane, hidden):
    """Visible columns, widths, anchors, and a renderer per column."""
    hidden = set(hidden or [])
    available = _pane_available(name, pane)
    cols = [c for c in available if c not in hidden]
    if "label" not in cols:
        cols = ["label"] + cols
    widths = {"label": 140, "n": 70, "freq": 70, "act bb": 70,
              "hits/opps": 110, "hands": 70, "net": 80, "bb/100": 80,
              "weak": 60}
    anchors = {"label": "w"}
    def n_of(r):
        return r.get("hands") if name == "results" else r.get("n") or r.get("k") or 0
    render = {
        "label": lambda r: r.get("label") or r.get("key") or "",
        "n": lambda r: f"{n_of(r):,}",
        "freq": lambda r: f"{r.get('pct', 0):.1f}%",
        "act bb": _fmt_ap,
        "hits/opps": lambda r: f"{r.get('hits', r.get('k', 0)):,} / {r.get('opps', r.get('n', 0)):,}",
        "hands": lambda r: f"{r.get('hands', r.get('n', 0)):,}",
        "net": lambda r: f"{r.get('net_bb', 0):+.1f}",
        "bb/100": lambda r: f"{r.get('bb100', 0):+.1f}",
        "weak": lambda r: "weak" if r.get("is_weak") else "",
    }
    for c in pane.get("cols") or []:
        if c not in render:
            render[c] = (lambda key: lambda r: _cell_pct(r, key))(c)
            widths.setdefault(c, 80)
    w = [widths.get(c, 80) for c in cols]
    return cols, w, anchors, render


def _cell_pct(row, key):
    cell = (row.get("cells") or {}).get(key)
    if not cell:
        return "–"
    return f"{cell['pct']:.1f}%"


def _pane_sort_key(row, col):
    if col in ("n", "hands"):
        return row.get("hands") or row.get("n") or 0
    if col == "freq":
        return row.get("pct") or 0
    if col == "act bb":
        return ((row.get("profit") or {}).get("bb_per_hand")) or 0
    if col == "net":
        return row.get("net_bb") or 0
    if col == "bb/100":
        return row.get("bb100") or 0
    if col == "hits/opps":
        return row.get("hits") or row.get("k") or 0
    return str(row.get("label") or row.get("key") or "")


def _cohort_range_blob(con, where):
    """Preflop range summary for a parked Multi-Player cohort."""
    g = query.chart_of(con, where)
    top = []
    if g["seen"]:
        ranked = sorted(g["cells"].items(), key=lambda kv: -kv[1][0])[:8]
        top = [{"combo": c, "pct": 100.0 * n / g["seen"], "n": n}
               for c, (n, _k) in ranked]
    return {"coverage": g.get("coverage"), "seen": g["seen"],
            "total": g["total"], "top": top}


def _render_compare_sizes(tv, cmp):
    """Two Bet Sizes tables on a pin, aligned by letter."""
    sizes = cmp.get("sizes") or {}
    by_a = {r["key"]: r for r in (sizes.get("a") or {}).get("rows") or []}
    by_b = {r["key"]: r for r in (sizes.get("b") or {}).get("rows") or []}
    keys = [k for k in query.lines.BUCKETS if k in by_a or k in by_b]
    if not keys:
        return
    tv.insert("", "end", values=("BET SIZES", "", "", "", ""),
              tags=("group",))
    for k in keys:
        ra, rb = by_a.get(k) or {}, by_b.get(k) or {}
        pa, pb = ra.get("profit") or {}, rb.get("profit") or {}
        this = (f"{ra.get('hits', 0):,}/{ra.get('opps', 0):,}"
                if ra.get("opps") else "–")
        pin = (f"{rb.get('hits', 0):,}/{rb.get('opps', 0):,}"
               if rb.get("opps") else "–")
        this_ap = (f"{pa['bb_per_hand']:+.2f}"
                   if pa.get("bb_per_hand") is not None else "–")
        pin_ap = (f"{pb['bb_per_hand']:+.2f}"
                  if pb.get("bb_per_hand") is not None else "–")
        tv.insert("", "end", iid=f"size:{k}",
                  values=(query.SIZE_NAMES[k],
                          f"{this}  {ra.get('pct', 0):.1f}%"
                          if ra.get("opps") else "–",
                          this_ap,
                          f"{pin}  {rb.get('pct', 0):.1f}%"
                          if rb.get("opps") else "–",
                          pin_ap))


def _render_compare_packs(tv, cmp):
    """Two stat packs on a pin, one row per stat that either side has."""
    packs = cmp.get("packs") or {}
    pa = {r["key"]: r for r in packs.get("a") or []}
    pb = {r["key"]: r for r in packs.get("b") or []}
    keys, seen = [], set()
    for r in (packs.get("a") or []) + (packs.get("b") or []):
        if r["key"] not in seen:
            seen.add(r["key"])
            keys.append(r["key"])
    if not keys:
        return
    tv.insert("", "end", values=("THIS vs PINNED  (every stat)", "", "", "", ""),
              tags=("group",))
    last = None
    for key in keys:
        a, b = pa.get(key), pb.get(key)
        group = (a or b or {}).get("group")
        if group != last:
            tv.insert("", "end", values=(group.upper(), "", "", "", ""),
                      tags=("group",))
            last = group
        tv.insert("", "end", iid=f"stat:{key}",
                  values=((a or b)["label"],
                          f"{a['pct']:.1f}%" if a else "–",
                          f"n={a['n']:,}" if a else "",
                          f"{b['pct']:.1f}%" if b else "–",
                          f"n={b['n']:,}" if b else ""))


def _profit_band(p):
    """Interval cell for a mean, or the reason there is none."""
    if p.get("lo") is not None:
        return f"[{p['lo']:+.1f}, {p['hi']:+.1f}]"
    if p.get("priced") == 1:
        return "n=1, no interval"
    return p.get("interval_note") or ""


def _cohort_expr(conditions):
    """The compact string the dialog started from, so it can be edited back."""
    parts = []
    for field, value in conditions:
        if field == "_expr":
            return value
        if str(value)[:1] in "<>=":
            parts.append(f"{field}{value}")
        else:
            parts.append(f"{field}={value}")
    return ",".join(parts)


class DetachedPane(tk.Toplevel):
    """
    One study pane in its own window.

    Live: `sync` redraws from the latest study payload, so a
    drill in the main window (or a row click here) reshapes
    this copy. The report identity is `App.argv` -- the same
    list Pin uses. Pin itself stays on the main bar. Closing
    does not touch the in-main pane. Dock-back is deferred.
    """

    def __init__(self, app, name):
        super().__init__(app)
        self.app = app
        self.pane = name
        self.tree = None
        self.hands = None
        self.graph = None
        self.hist = None
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.configure(bg=BG)
        self.minsize(420, 280)
        self.geometry("720x520")
        dark_titlebar(self)
        why = (f"live with the main filter  ·  pin stays in-main  ·  "
               f"close does not dock")
        ttk.Label(self, text=why, style="Dim.TLabel").pack(
            anchor="w", padx=8, pady=(6, 0))
        if name == "graph":
            self.graph = WinGraph(self, unit_var=app.graph_unit)
            self.graph.pack(fill="both", expand=True, padx=8, pady=8)
        elif name == "hist":
            self.hist = HistWidget(self, on_click=app._click_study_hist,
                                   height=260)
            self.hist.pack(fill="both", expand=True, padx=8, pady=8)
            self.hist.canvas.pack_configure(fill="both", expand=True)
        elif name == "hands":
            box = ttk.LabelFrame(self, text="Hands")
            box.pack(fill="both", expand=True, padx=8, pady=8)
            self.hands = app._table(box)
            self._bind_hands(self.hands)
        else:
            box = ttk.LabelFrame(
                self, text=query.STUDY_PANE_LABELS.get(name, name))
            box.pack(fill="both", expand=True, padx=8, pady=(4, 2))
            self.tree = app._table(box)
            self.tree.bind("<Button-1>",
                           lambda e, n=name: app._drill_pane(n, e))
            self.tree.bind("<Button-3>",
                           lambda e, n=name: app._pane_pin_menu(n, e))
            hands = ttk.LabelFrame(self, text="Hands")
            hands.pack(fill="both", expand=True, padx=8, pady=(2, 8))
            self.hands = app._table(hands)
            self._bind_hands(self.hands)
        self.sync(app._study_out, app.argv())

    def _bind_hands(self, tv):
        tv.bind("<Double-1>", self.app._open_hand)
        tv.bind("<Return>", self.app._open_hand)
        tv.bind("<Button-3>", self.app._hand_menu)

    def sync(self, out, argv=None):
        argv = list(argv if argv is not None else self.app.argv())
        try:
            self.title(query.detach_title(argv, self.pane))
        except tk.TclError:
            return
        out = out or {}
        why = out.get("why") or out.get("error")
        if self.graph is not None:
            self.graph.set(out.get("graph"), why)
            return
        if self.hist is not None:
            self.hist.set(out.get("hist"), why)
            return
        if self.tree is not None:
            pane = (out.get("panes") or {}).get(self.pane)
            if self.pane == "combo" and not pane:
                pane = out.get("families")
            if self.pane == "hist" and not pane:
                pane = out.get("hist")
            self.app._fill_pane(self.pane, self.tree, pane, out)
        if self.hands is not None:
            self.app._render_hands(self.hands, {"rows": out.get("hands") or []})

    def _close(self):
        self.app._forget_detached(self)
        self.destroy()


class WhoIsRegDialog(tk.Toplevel):
    """
    Auto Who-is-Reg plus a manual pin.

    The interval rule is still the default. A pin writes
    `player_types.json` and restamps `decisions.class`, so Statistics
    exclude and Reports `--class` see the same person.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Who is Reg")
        self.configure(bg=BG)
        self.geometry("640x420")
        ttk.Label(self, text="WHO IS REG", style="Title.TLabel").pack(
            anchor="w", padx=18, pady=(16, 4))
        ttk.Label(self, text="Auto is the interval rule in players.py. "
                  "A pin wins, the way H2N colour markers do. "
                  "Clearing a pin returns them to auto.",
                  style="Dim.TLabel").pack(anchor="w", padx=18, pady=(0, 8))
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        self.tree = ttk.Treeview(wrap, show="headings", selectmode="browse")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree["columns"] = ("player", "site", "hands", "auto",
                                "effective", "pin")
        for col, w in (("player", 180), ("site", 80), ("hands", 70),
                       ("auto", 70), ("effective", 80), ("pin", 70)):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="w")
        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=18, pady=(0, 16))
        ttk.Label(foot, text="pin as", style="Dim.TLabel").pack(side="left")
        self.pin = ttk.Combobox(foot, values=("auto", "reg", "fish", "unknown"),
                                state="readonly", width=10)
        self.pin.set("auto")
        self.pin.pack(side="left", padx=8)
        ttk.Button(foot, text="Apply pin",
                   command=self.apply).pack(side="left")
        ttk.Button(foot, text="Close", command=self.destroy).pack(
            side="right")
        self._rows = {}
        self.reload()

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        overrides = players.load_types()
        con = connect_db()
        con.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "players" not in tables:
                return
            rows = con.execute(
                "SELECT site, player, hands, vpip, pfr, class, durable "
                "FROM players WHERE durable = 1 "
                "ORDER BY hands DESC, player LIMIT 200").fetchall()
        except sqlite3.Error:
            con.close()
            return
        con.close()
        for r in rows:
            auto = players.auto_class_of(r["hands"], r["vpip"], r["pfr"])
            pin = overrides.get((r["site"], r["player"])) or ""
            iid = self.tree.insert("", "end", values=(
                r["player"], r["site"], f"{r['hands']:,}", auto,
                r["class"], pin or "—"))
            self._rows[iid] = (r["site"], r["player"])

    def apply(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._rows:
            return
        site, player = self._rows[sel[0]]
        try:
            players.set_type(site, player, self.pin.get())
        except ValueError as e:
            messagebox.showinfo("Who is Reg", str(e))
            return
        self.reload()
        self.app.refresh()


class CohortDialog(tk.Toplevel):
    """Choose a player cohort before applying the ordinary situation filters."""

    FIELDS = (("hands", "Hands", ""),
              ("vpip", "VPIP", ""),
              ("pfr", "PFR", ""),
              ("gap", "VPIP-PFR", ""),
              ("threebet", "3-bet", ""),
              ("fold_to_threebet", "Fold to 3-bet", ""),
              ("wwsf", "WWSF", ""),
              ("wtsd", "WTSD", ""),
              ("wsd", "W$SD", ""),
              ("bb100", "bb/100", ""))

    def __init__(self, app):
        super().__init__(app.master)
        self.app = app
        self.title("Players")
        self.configure(background=BG)
        self.geometry("520x500")
        self.transient(app.master)
        self.grab_set()

        current = {field: value for field, value in
                   (app.cohort_spec[0] if app.cohort_spec else [])}
        self.values = {field: tk.StringVar(value=current.get(field, default))
                       for field, _label, default in self.FIELDS}
        current_spec = app.cohort_spec or ([], None, None, None)
        conditions, site, klass, durable = current_spec
        self.expr = tk.StringVar(value=_cohort_expr(conditions))
        self.site = tk.StringVar(value=site or "")
        self.klass = tk.StringVar(value=klass or "")
        self.durable = tk.StringVar(
            value="" if durable is None else str(durable))

        ttk.Label(self, text="PLAYER COHORT", style="Title.TLabel").pack(
            anchor="w", padx=24, pady=(22, 4))
        ttk.Label(self, text="Filter players first; the selected cohort is "
                  "then used by every report tab. 40+ means >=40.",
                  style="Dim.TLabel").pack(anchor="w", padx=24, pady=(0, 12))
        compact = ttk.Frame(self)
        compact.pack(fill="x", padx=24, pady=(0, 10))
        ttk.Label(compact, text="or type", width=14).pack(side="left")
        ttk.Entry(compact, textvariable=self.expr, width=36).pack(side="left")
        ttk.Label(self, text="vpip>=40,pfr<=10,hands>=100  or  "
                  "Value(3Bet)<2 and Opps(3Bet)>100",
                  style="Dim.TLabel").pack(anchor="w", padx=24, pady=(0, 12))
        body = ttk.Frame(self)
        body.pack(fill="x", padx=24)
        for field, label, _default in self.FIELDS:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=14).pack(side="left")
            ttk.Entry(row, textvariable=self.values[field], width=18).pack(
                side="left")
            ttk.Label(row, text="blank or comparator value, e.g. >=500",
                      style="Dim.TLabel").pack(side="left", padx=10)
        for label, variable, values in (
                ("Site", self.site, ("",) + sites.KEYS),
                ("Class", self.klass, ("", "reg", "fish", "unknown")),
                ("Durable", self.durable, ("", "1", "0"))):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=14).pack(side="left")
            ttk.Combobox(row, textvariable=variable, values=values,
                         state="readonly", width=16).pack(side="left")

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=22)
        ttk.Button(foot, text="CLEAR", command=self.clear).pack(side="left")
        ttk.Button(foot, text="CANCEL", command=self.destroy).pack(
            side="right")
        ttk.Button(foot, text="APPLY", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self.apply())

    def clear(self):
        self.expr.set("")
        for value in self.values.values():
            value.set("")
        self.site.set("")
        self.klass.set("")
        self.durable.set("")

    def apply(self):
        argv = ["--cohort"]
        compact = self.expr.get().strip()
        if compact:
            argv.append(compact)
        flags = {"fold_to_threebet": "--fold-to-threebet"}
        for field, _label, _default in self.FIELDS:
            value = self.values[field].get().strip()
            if value:
                argv += [flags.get(field, "--" + field), value]
        for flag, value in (("--site", self.site.get()),
                            ("--class", self.klass.get()),
                            ("--durable", self.durable.get())):
            if value:
                argv += [flag, value]
        try:
            spec, _remaining = players.parse_cohort(argv)
            # Validate the conditions before changing the live application.
            con = sqlite3.connect(DB)
            players.cohort(con, *spec)
            con.close()
        except (SystemExit, ValueError) as error:
            messagebox.showerror("Invalid player filter", str(error),
                                 parent=self)
            return
        self.app.cohort_spec = spec
        self.app.cohort_btn.configure(text="Players: active")
        self.destroy()
        self.app.refresh()


class ReportDialog(tk.Toplevel):
    """
    The filter on the screen, kept under a name in the report box.

    The sibling of `StatDialog`, and the two are deliberately separate
    windows rather than one with two buttons: saving a stat and saving a
    report are different verbs on the same filter, and a window offering
    both would have to explain which SAVE was which.

    A report is only the situation. The reporting options -- which columns,
    what to split by -- are stripped by `query.situation_only` before
    anything is written, because a report that remembered `--show vpip,pfr`
    would rewrite the columns of every view it was opened in, and that reads
    as the window forgetting what you asked it rather than as the report
    having an opinion.
    """

    def __init__(self, app):
        super().__init__(app.master)
        self.app = app
        self.title("Save as report")
        self.configure(background=BG)
        self.geometry("620x420")
        self.transient(app.master)
        self.grab_set()

        self.argv = players.parse_cohort(app.argv())[1]
        try:
            _where, described, _parts = query.build(self.argv)
        except SystemExit as error:
            described = str(error)
        self.name = tk.StringVar()

        ttk.Label(self, text="SAVE AS REPORT", style="Title.TLabel").pack(
            anchor="w", padx=24, pady=(22, 4))
        ttk.Label(self, text="It joins the report box at the top of the "
                             "window, beside the ones built in.",
                  style="Dim.TLabel").pack(anchor="w", padx=24, pady=(0, 12))
        ttk.Label(self, text="filter: " + described, style="Dim.TLabel",
                  wraplength=560, justify="left").pack(
                      anchor="w", padx=24, pady=(0, 16))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=24, pady=4)
        ttk.Label(row, text="Name", width=11).pack(side="left")
        ttk.Entry(row, textvariable=self.name, width=30).pack(side="left")

        self.result = ttk.Label(self, text="", style="Dim.TLabel",
                                wraplength=560, justify="left")
        self.result.pack(anchor="w", padx=24, pady=(18, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=24, pady=(18, 0))
        ttk.Label(row, text="Saved", width=11).pack(side="left")
        self.saved = ttk.Combobox(row, values=self._saved_names(),
                                  state="readonly", width=28)
        self.saved.pack(side="left")
        ttk.Button(row, text="FORGET", command=self.forget).pack(
            side="left", padx=8)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=20, side="bottom")
        ttk.Button(foot, text="CLOSE", command=self.close).pack(side="right")
        ttk.Button(foot, text="SAVE", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self.save())

    def _saved_names(self):
        return list(query.saved_filters())

    def save(self):
        try:
            n, _described = query.save_filter(self.name.get(), self.argv)
        except (ValueError, SystemExit) as error:
            messagebox.showerror("Not a report yet", str(error), parent=self)
            return
        self.result.configure(
            text=(f"Saved. {n:,} decisions match it today. It is in the "
                  f"report box now." if n else
                  "Saved -- but NOTHING MATCHES. Every view opened under it "
                  "will be empty."))
        self.saved.configure(values=self._saved_names())
        self.app.reload_reports()

    def forget(self):
        name = self.saved.get()
        if not name:
            return
        try:
            query.forget_filter(name)
        except ValueError as error:
            messagebox.showerror("Cannot forget that", str(error), parent=self)
            return
        self.saved.set("")
        self.saved.configure(values=self._saved_names())
        self.result.configure(text=f"Forgot {name}.")
        self.app.reload_reports()
        self.app.refresh()

    def close(self):
        self.grab_release()
        self.destroy()


class StatDialog(tk.Toplevel):
    """
    The filter on the screen, saved as a stat with a name of its own.

    Hand2Note builds a custom stat by clicking the actions street by street
    and then naming what comes out. This window is the naming and none of
    the clicking, because the filter dialog has already said what the
    situation is -- position, pot type, the betting written out on its Lines
    tab. All that is left is what to count inside it and what to call the
    answer.

    Which is exactly why there is no builder here. A builder would be a
    second way to describe a situation, and two ways to say "3-bet pot in
    position" start meaning different things the first time either changes.
    """

    def __init__(self, app):
        super().__init__(app.master)
        self.app = app
        self.title("Save as stat")
        self.configure(background=BG)
        self.geometry("640x520")
        self.transient(app.master)
        self.grab_set()

        # The player cohort is deliberately left out. A cohort chooses
        # PEOPLE and a stat describes a SITUATION, so a stat that quietly
        # carried "regs with over 500 hands" inside it would report a
        # different population from the one its own column heading claims,
        # in every report anybody ever put it in.
        self.argv = players.parse_cohort(app.argv())[1]
        try:
            self.chance, described, _parts = query.build(self.argv)
        except SystemExit as error:
            self.chance, described = "1=1", str(error)

        self.key = tk.StringVar()
        self.label = tk.StringVar()
        self.do = tk.StringVar(value="aggressive")
        self.per = tk.StringVar(value="decision")

        ttk.Label(self, text="SAVE AS STAT", style="Title.TLabel").pack(
            anchor="w", padx=24, pady=(22, 4))
        ttk.Label(self, text="The filter becomes the chance to do something. "
                             "What you pick below is the doing of it.",
                  style="Dim.TLabel").pack(anchor="w", padx=24, pady=(0, 12))
        ttk.Label(self, text="filter: " + described, style="Dim.TLabel",
                  wraplength=580, justify="left").pack(
                      anchor="w", padx=24, pady=(0, 16))

        body = ttk.Frame(self)
        body.pack(fill="x", padx=24)
        for text, variable, note in (
                ("Name", self.key,
                 "lowercase, no spaces -- reports pick columns by it"),
                ("Shown as", self.label, "blank uses the name")):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=text, width=11).pack(side="left")
            ttk.Entry(row, textvariable=variable, width=22).pack(side="left")
            ttk.Label(row, text=note, style="Dim.TLabel").pack(
                side="left", padx=10)

        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Counts", width=11).pack(side="left")
        self.do_box = ttk.Combobox(row, textvariable=self.do,
                                   values=list(stats.ACTIONS),
                                   state="readonly", width=20)
        self.do_box.pack(side="left")
        self.do_note = ttk.Label(row, text="", style="Dim.TLabel")
        self.do_note.pack(side="left", padx=10)
        self.do_box.bind("<<ComboboxSelected>>", lambda _e: self._explain())
        self._explain()

        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Once per", width=11).pack(side="left")
        ttk.Combobox(row, textvariable=self.per, values=("decision", "hand"),
                     state="readonly", width=20).pack(side="left")
        ttk.Label(row, text="a player VPIPs once however often they act",
                  style="Dim.TLabel").pack(side="left", padx=10)

        self.result = ttk.Label(self, text="", style="Dim.TLabel",
                                wraplength=580, justify="left")
        self.result.pack(anchor="w", padx=24, pady=(18, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=24, pady=(18, 0))
        ttk.Label(row, text="Saved", width=11).pack(side="left")
        self.saved = ttk.Combobox(row, values=self._saved_keys(),
                                  state="readonly", width=20)
        self.saved.pack(side="left")
        ttk.Button(row, text="FORGET", command=self.forget).pack(
            side="left", padx=8)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=20, side="bottom")
        ttk.Button(foot, text="CLOSE", command=self.close).pack(side="right")
        ttk.Button(foot, text="SAVE", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self.save())

    def _explain(self):
        self.do_note.configure(text=stats.ACTIONS[self.do.get()][1])

    def _saved_keys(self):
        return [st.key for st in STATS if st.custom]

    def save(self):
        try:
            n, k = query.define_stat(self.argv, self.key.get().strip(),
                                     self.label.get().strip() or None,
                                     self.do.get(), self.per.get())
        except SystemExit as error:
            messagebox.showerror("Not a stat yet", str(error), parent=self)
            return
        # The count is the whole point of showing anything back. A stat that
        # nothing in the database has ever had the chance to do saves
        # perfectly happily and then reads as an empty column for ever,
        # which looks like a broken report rather than like a spot nobody
        # has played.
        self.result.configure(
            text=(f"Saved. {k} of {n} ({100 * k / n:.1f}%). It is a column "
                  f"in every report now, and a one-click filter on the Quick "
                  f"tab." if n else
                  "Saved -- but NOTHING MATCHES. Nobody in this database has "
                  "ever been in that spot, so the stat will be blank "
                  "wherever it is shown."))
        self.saved.configure(values=self._saved_keys())
        self.app.refresh()

    def forget(self):
        key = self.saved.get()
        if not key:
            return
        try:
            stats.forget(key)
        except ValueError as error:
            messagebox.showerror("Cannot forget that", str(error), parent=self)
            return
        self.saved.set("")
        self.saved.configure(values=self._saved_keys())
        self.result.configure(text=f"Forgot {key}.")
        self.app.refresh()

    def close(self):
        self.grab_release()
        self.destroy()


class FilterDialog(tk.Toplevel):
    """
    Every filter, laid out to be read rather than squeezed down one side.

    The rail this replaces could hold about a third of what the database can
    be asked, and it did it in a column narrow enough that each control was a
    guess at its own label. A filter is consulted far less often than the
    answer it produces, so it belongs behind a button and is worth giving the
    whole window when it is open.

    Nothing here defines a filter. Every control sets one of the variables
    the application already holds, and `query.build` turns those into a WHERE
    clause exactly as it does for the command line -- which is what stops the
    window and the terminal from drifting into meaning different things.
    """

    COLUMNS = 3

    def __init__(self, app):
        super().__init__(app.master)
        self.app = app
        self.title("Filter")
        self.configure(background=BG)
        self.geometry("1080x720")
        self.transient(app.master)
        self.grab_set()

        # Edit a copy. Cancel then means what it says, rather than leaving
        # behind whatever was clicked before somebody changed their mind.
        self.was = ({f: v.get() for f, v in app.flags.items()},
                    {g: set(v) for g, v in app.multi.items()},
                    {n: v.get() for n, v in app.vals.items()})

        nb = ttk.Notebook(self, style="Big.TNotebook")
        nb.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        self._reports_tab(nb)
        self._quick_tab(nb)
        self._positions_tab(nb)
        self._actions_tab(nb)
        self._cards_tab(nb)
        self._lines_tab(nb)
        self._general_tab(nb)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=20, pady=14)
        ttk.Label(foot, style="Dim.TLabel",
                  text="closing this window keeps your choices — "
                       "CANCEL throws them away").pack(side="left")
        ttk.Button(foot, text="APPLY", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=(8, 0))
        ttk.Button(foot, text="RESET", command=self.reset).pack(side="right",
                                                                padx=8)
        ttk.Button(foot, text="CANCEL", command=self.cancel).pack(side="right")
        ttk.Button(foot, text="SAVE AS STAT",
                   command=self.save_as_stat).pack(side="right", padx=8)
        ttk.Button(foot, text="SAVE AS REPORT",
                   command=self.save_as_report).pack(side="right")
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.apply())
        # Closing the window keeps what was clicked. The dialog edits the
        # filter in place, so shutting it with the title bar used to leave
        # every choice set and the view never redrawn -- which looks exactly
        # like a filter that does nothing, and was reported as one. CANCEL
        # is the way to throw the choices away, and it is a button.
        self.protocol("WM_DELETE_WINDOW", self.apply)

    # ---- the pieces a tab is made of ----------------------------------
    def _page(self, nb, title):
        outer = ttk.Frame(nb)
        nb.add(outer, text=title)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        # `bind_all` puts the binding on the whole application, and this runs
        # once per tab -- so every tab overwrote the last, only one of them
        # scrolled, and the binding outlived the dialog. Closing the filter
        # and then touching the wheel raised
        #     TclError: invalid command name ".!filterdialog...!canvas"
        # against a canvas that no longer existed. It is still `bind_all`,
        # because on Windows the wheel goes to the focused widget rather than
        # the one under the pointer, but it is now put on while the pointer
        # is over this canvas and taken off again when it leaves or the tab
        # is destroyed.
        def wheel(event):
            canvas.yview_scroll(-event.delta // 120, "units")

        def grab(_e):
            canvas.bind_all("<MouseWheel>", wheel)

        def release(_e):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", grab)
        canvas.bind("<Leave>", release)
        outer.bind("<Destroy>", release)
        return inner

    def _heading(self, parent, text):
        ttk.Label(parent, text=text.upper(), style="Head.TLabel").pack(
            anchor="w", padx=18, pady=(18, 6))

    def _pick(self, parent, label, on, off, is_on, note=""):
        """
        One clickable choice, drawn as text rather than as a checkbox.

        Tk's checkbutton indicator cannot be made to look like anything but a
        Tk checkbutton, and a page of them reads as a form. What is wanted
        here is a list of things you can pick, so the label itself is the
        control and being chosen is shown by its colour.
        """
        lab = tk.Label(parent, text=label, bg=BG, anchor="w", padx=10, pady=5,
                       font=(UI, 10), cursor="hand2")
        if note:
            self._tip(lab, note)

        def paint():
            lab.configure(fg=WARN if is_on() else INK,
                          font=(UI, 10, "bold" if is_on() else "normal"))

        def click(_e):
            (off if is_on() else on)()
            paint()
        lab.bind("<Button-1>", click)
        lab.bind("<Enter>", lambda e: lab.configure(bg=PANEL))
        lab.bind("<Leave>", lambda e: lab.configure(bg=BG))
        paint()
        lab.pack(fill="x", anchor="w")
        return lab

    def _tip(self, widget, text):
        """A note on hover, since a filter's name rarely says its definition."""
        tip = {"win": None}

        def show(_e):
            if tip["win"]:
                return
            w = tk.Toplevel(widget)
            w.wm_overrideredirect(True)
            w.configure(background=EDGE)
            tk.Label(w, text=text, bg=EDGE, fg=INK, font=(UI, 9),
                     padx=8, pady=4, wraplength=380, justify="left").pack()
            w.wm_geometry(f"+{widget.winfo_rootx() + 20}"
                          f"+{widget.winfo_rooty() + 26}")
            tip["win"] = w

        def hide(_e):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _grid(self, parent, items):
        """Items in columns, filled down then across, as the screenshot does."""
        holder = ttk.Frame(parent)
        holder.pack(fill="x", padx=8)
        cols = [ttk.Frame(holder) for _ in range(self.COLUMNS)]
        for c in cols:
            c.pack(side="left", fill="both", expand=True, anchor="n")
        per = (len(items) + self.COLUMNS - 1) // self.COLUMNS or 1
        for i, make in enumerate(items):
            make(cols[min(i // per, self.COLUMNS - 1)])

    def _set_item(self, group, value):
        m = self.app.multi[group]
        return (lambda: m.add(value), lambda: m.discard(value),
                lambda: value in m)

    def _val_item(self, name, value):
        """A one-of pick written onto a string var, used by Faced Next."""
        var = self.app.vals[name]

        def on():
            var.set(value)

        def off():
            if var.get() == value:
                var.set("")
        return on, off, (lambda: var.get() == value)

    def _flag_item(self, flag):
        v = self.app.flags[flag]

        def on():
            v.set(True)
            twin = OPPOSITES.get(flag)
            if twin:
                self.app.flags[twin].set(False)
        return on, (lambda: v.set(False)), (lambda: bool(v.get()))

    # ---- the tabs ------------------------------------------------------
    def _reports_tab(self, nb):
        """Hand2Note's Smart Reports tree: named spots, grouped by street."""
        page = self._page(nb, "Reports")
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A report is a situation, not a stat. Opening one "
                       "replaces the street / pot / facing you have clicked "
                       "and keeps who you are measuring -- the same split "
                       "the command line already makes between --preset and "
                       "--hero."
                  ).pack(anchor="w", padx=18, pady=(12, 0))
        for family, names in query.reports_by_family():
            self._heading(page, family)
            self._grid(page, [
                (lambda parent, n=n: self._pick(
                    parent, n, *self._report_item(n),
                    note=" ".join(query.reports()[n])))
                for n in names])

    def _report_item(self, name):
        def on():
            self.app.clear_situation()
            self.app.preset.set(name)
        def off():
            if self.app.preset.get() == name:
                self.app.preset.set("")
        return on, off, (lambda n=name: self.app.preset.get() == n)

    def _quick_tab(self, nb):
        page = self._page(nb, "Quick Filters")
        by_group = {}
        for f in query.quick_filters():
            by_group.setdefault(f["group"], []).append(f)
        for group, items in by_group.items():
            self._heading(page, group)
            self._grid(page, [
                (lambda parent, f=f: self._pick(
                    parent, f["label"], *self._set_item("quick", f["key"]),
                    note=f.get("note") or ""))
                for f in items])

    def _positions_tab(self, nb):
        page = self._page(nb, "Positions")
        self._heading(page, "my position")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("pos", v))) for v in POSITIONS])
        self._heading(page, "against  (heads-up pots only)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("vs", v))) for v in POSITIONS])
        self._heading(page, "and that opponent is")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in VS_SIDE])
        self._heading(page, "who is being measured")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f)))
            for f, t in (("--hero", "me"), ("--pool", "the pool"))])

        self._heading(page, "what kind of player")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in WHO])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A reg plays a third of hands or fewer and raises at "
                       "least one in ten. A fish is loose or passive: over a "
                       "third of hands, or almost never raising while still "
                       "coming in often. Everybody there is not enough "
                       "evidence about is unknown and neither of these "
                       "selects them — which is why the two do not add up "
                       "to the whole pool.\n\nOnly ACR names people. An "
                       "Ignition ring seat is one player for as long as they "
                       "stay sat there and a different one after; Zone names "
                       "nobody at all."
                  ).pack(anchor="w", padx=18, pady=(4, 0))

    def _actions_tab(self, nb):
        page = self._page(nb, "Actions")
        self._heading(page, "street")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("street", v))) for v in STREETS])
        self._heading(page, "pot type")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("pot", v))) for v in POT_TYPES])
        self._heading(page, "facing")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("facing", v)))
            for v in query.FACINGS])
        self._heading(page, "situation")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in SITUATIONS])
        self._heading(page, "this action")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._val_item("action", v)))
            for v in ("fold", "check", "call", "bet", "raise")])
        self._heading(page, "whole-hand result")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._val_item("result", v)))
            for v in ("won", "lost", "showdown", "no-showdown")])
        self._heading(page, "faced next  (the other seat then)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._val_item("after", v)))
            for v in list(query.AFTER) + ["none"]])
        self._heading(page, "next actions  (this player then)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._val_item("then", v)))
            for v in list(query.AFTER) + ["none"]])
        self._heading(page, "outcome of this bet")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, query.OUTCOMES[v][1], *self._val_item("outcome", v)))
            for v in ("fold-out", "call", "raise-back")])
        self._heading(page, "bet size  (fraction of the pot)")
        self._grid(page, [(lambda parent, v=v, t=t: self._pick(
            parent, t, *self._val_item("size", v)))
            for v, t in (("s", "small"), ("m", "medium"), ("l", "large"),
                         ("p", "pot+"), ("o", "overbet"))])
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(2, 0))
        ttk.Label(row, text="or a range", style="Dim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.app.vals["size"], width=14).pack(
            side="left", padx=(6, 18))
        ttk.Label(row, text="0.4-0.75   50%+   <=0.33",
                  style="Dim.TLabel").pack(side="left")
        self._heading(page, "stack  (effective bb)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        ttk.Entry(row, textvariable=self.app.vals["stack"], width=14).pack(
            side="left")
        ttk.Label(row, text="100+   <40   80-200   — same column as at least / less than",
                  style="Dim.TLabel").pack(side="left", padx=14)
        self._heading(page, "flop texture")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("board", v)))
            for v in query.BOARDS])
        self._heading(page, "what the turn card did")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("turn_card", v)))
            for v in query.RUNOUT])
        self._heading(page, "what the river card did")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("river_card", v)))
            for v in query.RUNOUT])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A flush card is one that takes a suit to three on "
                       "the board. On the turn, straight means the board is "
                       "now one card off one; on the river it means that "
                       "card came. A brick did none of the four."
                  ).pack(anchor="w", padx=18, pady=(6, 0))

    def _cards_tab(self, nb):
        """
        What the hand is, which only exists where the cards were shown.

        Three quarters of decisions have no cards to name a hand from -- all
        of Ignition's are known and 23% of ACR's -- so everything on this tab
        narrows hard, and the note says so before an empty table does.
        """
        page = self._page(nb, "Cards")
        self._heading(page, "preflop combo  (AKs, or a family: Axs, 22+)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(0, 6))
        ttk.Entry(row, textvariable=self.app.vals["combo"], width=26).pack(
            side="left")
        ttk.Label(row, text="Axs  Kxo  22+  pairs  broadways",
                  style="Dim.TLabel").pack(side="left", padx=14)
        self._heading(page, "what the hand became")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("made", v))) for v in MADE])
        self._heading(page, "kicker, where a pair uses one")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("kicker", v))) for v in KICKERS])
        self._heading(page, "flush draw")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("fd", v))) for v in FLUSH_DRAWS])
        self._heading(page, "straight draw")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("sd", v))) for v in STRAIGHT_DRAWS])
        self._heading(page, "either, or both at once")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f)))
            for f, t in (("--drawing", "has a draw"),
                         ("--combo-draw", "a flush draw and a straight draw"),
                         ("--shown", "the cards are known"))])
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="Every filter here needs the cards, and the cards are "
                       "known for 20,465 postflop decisions out of 94,017 — "
                       "all of Ignition's hands, including the ones that "
                       "folded, and 23% of ACR's. So these narrow hard, and "
                       "a small n here is the data and not the filter.\n\n"
                       "A draw has to be the player's own: four hearts on "
                       "the board is not a flush draw, it is a board "
                       "everybody shares."
                  ).pack(anchor="w", padx=18, pady=(8, 0))

    def _lines_tab(self, nb):
        """
        The betting written out, which is the one thing checkboxes cannot say.

        Everything on the other tabs describes a single decision. This
        describes the shape of the hand -- "the flop went check, bet, call"
        -- and no list of named filters covers it, because the number of
        shapes a hand can have is the number of strings these letters spell.
        """
        page = self._page(nb, "Lines")
        self._heading(page, "how the betting went")
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="F fold   X check   C call   B bet   R raise   "
                       "A all-in        *  anything at all   ?  any one "
                       "action\n\nAdd a size letter after a bet to say how "
                       "big it was:   s  a third or less   m  half   "
                       "l  two-thirds to three-quarters   p  pot   "
                       "o  an overbet.\nSo XBC is a flop that went check, "
                       "bet, call at any size, and XBmC is the same flop "
                       "with a half-pot bet."
                  ).pack(anchor="w", padx=18, pady=(0, 4))

        for name, label, example in (
                ("pre", "preflop", "*R*R*   somebody 3-bet"),
                ("flop", "flop", "XBC   checked, bet, called"),
                ("turn", "turn", "XX   checked through"),
                ("river", "river", "*Bo*   somebody overbet")):
            row = ttk.Frame(page)
            row.pack(fill="x", padx=18, pady=3)
            ttk.Label(row, text=label, style="Dim.TLabel", width=9).pack(
                side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=26).pack(
                side="left")
            ttk.Label(row, text=example, style="Dim.TLabel").pack(
                side="left", padx=14)

        self._heading(page, "the whole hand, streets separated by /")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=3)
        ttk.Label(row, text="line", style="Dim.TLabel", width=9).pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["line"], width=40).pack(
            side="left")
        ttk.Label(row, text="*R*R*/XBC/XX/*   3-bet pot, flop check-bet-call, "
                            "turn through", style="Dim.TLabel").pack(
            side="left", padx=14)

        self._heading(page, "where the player was standing when they acted")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=3)
        ttk.Label(row, text="node", style="Dim.TLabel", width=9).pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["node"], width=40).pack(
            side="left")
        ttk.Label(row, text="*/XB   it was checked to somebody, who bet, and "
                            "now it is this player's turn",
                  style="Dim.TLabel").pack(side="left", padx=14)
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="A node is the hand cut short at the moment this "
                       "player had to act, so it never contains what they "
                       "did next -- which is what makes it the right thing "
                       "to measure a decision against."
                  ).pack(anchor="w", padx=18, pady=(8, 0))

    def _general_tab(self, nb):
        page = self._page(nb, "General")
        self._heading(page, "site, stake, player")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, blank, values in (
                ("site", "any site", self.app.options["sites"]),
                ("stake", "any stake", self.app.options["stakes"]),
                ("player", "any player", self.app.options["players"])):
            box = ttk.Combobox(row, textvariable=self.app.vals[name],
                               values=[blank] + list(values), width=22)
            if not self.app.vals[name].get():
                self.app.vals[name].set(blank)
            box.pack(side="left", padx=(0, 14))

        self._heading(page, "how many sat, how many are left")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, text in (("players", "players at the table"),
                           ("live", "still in the pot")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=6).pack(
                side="left", padx=(6, 18))

        self._heading(page, "stack depth, in big blinds")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, text in (("deep", "at least"), ("short", "less than")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=8).pack(
                side="left", padx=(6, 18))

        self._heading(page, "dates   (yyyy-mm-dd)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        ttk.Checkbutton(row, text="Today",
                        variable=self.app.flags["--today"]).pack(side="left")
        ttk.Label(row, text="last N hours", style="Dim.TLabel").pack(
            side="left", padx=(14, 4))
        ttk.Entry(row, textvariable=self.app.vals["hours"], width=5).pack(
            side="left", padx=(0, 18))
        for name, text in (("since", "from"), ("until", "to")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=14).pack(
                side="left", padx=(6, 18))
        ttk.Label(row, text="start-of-day", style="Dim.TLabel").pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["start_of_day"],
                  width=4).pack(side="left", padx=(4, 8))
        ttk.Label(row, text="tz site=hours", style="Dim.TLabel").pack(
            side="left")
        ttk.Entry(row, textvariable=self.app.vals["tz"], width=22).pack(
            side="left", padx=(4, 0))
        ttk.Label(page,
                  text="Today / hours use start-of-day and each room's "
                       "HH timezone offset. Raw from/to are played_at "
                       "as written. An empty Today is usually the hour "
                       "or the offset, not missing hands.",
                  style="Dim.TLabel").pack(anchor="w", padx=18, pady=(4, 0))
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(8, 0))
        ttk.Label(row, text="session id", style="Dim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.app.vals["session"], width=22).pack(
            side="left", padx=(6, 0))

        self._heading(page, "study  (marked hands and tags)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Checkbutton(row, text="marked only",
                        variable=self.app.flags["--marked"]).pack(side="left")
        ttk.Checkbutton(row, text="has a note",
                        variable=self.app.flags["--noted"]).pack(
                            side="left", padx=(14, 0))
        ttk.Label(row, text="tag", style="Dim.TLabel").pack(
            side="left", padx=(18, 6))
        ttk.Entry(row, textvariable=self.app.vals["tag"], width=16).pack(
            side="left")

        self._heading(page, "aliases  (username + room groups)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Label(row, text="alias", style="Dim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.app.vals["alias"], width=14).pack(
            side="left", padx=(6, 18))
        ttk.Label(row, text="vs alias", style="Dim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.app.vals["vs_alias"], width=14).pack(
            side="left", padx=(6, 18))
        ttk.Label(row, text="villain type", style="Dim.TLabel").pack(
            side="left")
        ttk.Combobox(row, textvariable=self.app.vals["villain_type"],
                     values=("", "fish", "reg", "unknown"), width=10,
                     state="readonly").pack(side="left", padx=(6, 0))
        ttk.Label(page, style="Dim.TLabel", wraplength=900, justify="left",
                  text="A single-person alias merges those accounts as "
                       "the player. A group alias is a villain pool "
                       "(--vs-alias). --player does not expand a name "
                       "into an alias."
                  ).pack(anchor="w", padx=18, pady=(0, 8))

        self._heading(page, "anything else, as SQL over `decisions`")
        ttk.Entry(page, textvariable=self.app.vals["where"]).pack(
            fill="x", padx=18, pady=(0, 6))
        ttk.Label(page, style="Dim.TLabel", wraplength=900, justify="left",
                  text="For what the named filters cannot say. The columns "
                       "are the ones `decisions` has: pot_frac, eff_bb, spr, "
                       "n_live, fl_hi, size_bb, to_call_bb and the rest."
                  ).pack(anchor="w", padx=18)

    # ---- the three buttons ---------------------------------------------
    def apply(self):
        diag.event("filter applied", argv=self.app.argv())
        self.grab_release()
        self.destroy()
        self.app.refresh()

    def save_as_stat(self):
        """
        Keep the filter, then name it.

        The naming window is opened from the main window and not from this
        one, which holds a grab: a modal dialog on top of a modal dialog is
        how a window ends up unreachable behind the thing that will not let
        go of the mouse.
        """
        self.apply()
        StatDialog(self.app)

    def save_as_report(self):
        """Keep the filter, then name it -- the same two steps, one verb over."""
        self.apply()
        ReportDialog(self.app)

    def cancel(self):
        flags, multi, vals = self.was
        for f, v in flags.items():
            self.app.flags[f].set(v)
        for g, v in multi.items():
            self.app.multi[g] = set(v)
        for n, v in vals.items():
            self.app.vals[n].set(v)
        self.grab_release()
        self.destroy()

    def reset(self):
        self.app.clear_filters()
        self.grab_release()
        self.destroy()
        FilterDialog(self.app)


class HandWindow(tk.Toplevel):
    """One hand, replayed in a window of its own."""

    def __init__(self, master, con, hand_id, seat):
        super().__init__(master)
        self.configure(background=BG)
        self.title(f"hand {hand_id}")
        self.geometry("760x680")
        self.hand_id = hand_id
        self.seat = seat
        study = ttk.Frame(self)
        study.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Button(study, text="Mark",
                   command=lambda: notes.mark(hand_id)).pack(side="left")
        ttk.Button(study, text="Unmark",
                   command=lambda: notes.unmark(hand_id)).pack(
                       side="left", padx=(6, 0))
        self._tag = tk.StringVar()
        ttk.Entry(study, textvariable=self._tag, width=12).pack(
            side="left", padx=(12, 4))
        ttk.Button(study, text="Tag", command=self._tag_hand).pack(side="left")
        self._note = tk.StringVar()
        ttk.Entry(study, textvariable=self._note, width=28).pack(
            side="left", padx=(12, 4))
        ttk.Button(study, text="Note", command=self._add_note).pack(side="left")
        d = query.hand_detail(con, hand_id, seat)
        mono = tkfont.Font(family=MONO, size=10)
        text = tk.Text(self, background=BG, foreground=INK, borderwidth=0,
                       font=mono, padx=16, pady=12, wrap="none",
                       insertbackground=BG)
        bar = ttk.Scrollbar(self, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for name, colour in (("dim", DIM), ("hi", ACCENT), ("good", GOOD),
                             ("bad", BAD), ("head", INK)):
            text.tag_configure(name, foreground=colour)
        text.tag_configure("head", font=(mono.cget("family"), 10, "bold"))

        if d is None:
            text.insert("end", "hand not found")
            text.configure(state="disabled")
            return
        stake = f"${d['sb']}/${d['bb']}" if d["bb"] else "-"
        text.insert("end", f"{d['hand_id']}\n", "head")
        text.insert("end", f"{d['site']}  {d['fmt']}  {stake}  "
                           f"{d['played_at']}  {d['table']}\n", "dim")
        line = compact.CompactHandRenderer(d, fmt="text")
        if line:
            text.insert("end", f"{line}\n", "hi")
        text.insert("end", "\n")
        for s in d["seats"]:
            net = (s["won"] or 0) - (s["put_in"] or 0)
            mark = "*" if s["seat"] == seat else (">" if s["is_hero"] else " ")
            text.insert("end", f" {mark} {s['position'] or '?':4} "
                               f"{(s['name'] or '')[:18]:18} "
                               f"{s['stack'] or 0:9.2f}  "
                               f"{s['cards'] or 'not shown':>10}  ")
            text.insert("end", f"{net:+9.2f}\n", "good" if net > 0 else "bad")
        for st in d["streets"]:
            head = st["street"].upper()
            if st["board"]:
                head += f"  [{st['board']}]"
            first = st["actions"][0] if st["actions"] else None
            if first and first["pot_before"] is not None:
                head += f"   pot {first['pot_before']:.2f}"
            text.insert("end", f"\n{head}\n", "head")
            for a in st["actions"]:
                amt = f" {a['amount']:.2f}" if a["amount"] else ""
                text.insert("end", f"    {a['position'] or '?':4} "
                                   f"{(a['name'] or '')[:16]:16} "
                                   f"{a['verb']}{amt}\n")
        if d["pot"]:
            rake = f"   rake {d['rake']:.2f}" if d["rake"] else ""
            text.insert("end", f"\nTOTAL POT {d['pot']:.2f}{rake}\n", "dim")
        text.configure(state="disabled")

    def _tag_hand(self):
        raw = self._tag.get().strip()
        tags = [x.strip() for x in raw.split(",") if x.strip()]
        notes.mark(self.hand_id, tags)

    def _add_note(self):
        text = self._note.get().strip()
        if not text:
            return
        hands_con = sqlite3.connect(DB)
        hands_con.row_factory = sqlite3.Row
        try:
            notes.add(text, hand_id=self.hand_id, seat=self.seat,
                      hands_con=hands_con)
        finally:
            hands_con.close()
        self._note.set("")


def check(db_path=DB):
    """
    The window and the command line must build the same filter.

    Same argument as `gui.py`'s check and for the same reason: three front
    ends over one database stay agreeing only if they share one definition of
    what a filter means. Here the window's own widgets are driven, so what is
    tested is the thing the user actually manipulates rather than a
    reimplementation of it.
    """
    fails = []
    root = tk.Tk()
    root.withdraw()
    dark(root)
    app = App(root)
    # An empty or missing corpus used to raise here and leave the
    # Tk root alive, so `--check` hung on exit instead of reporting
    # the widget/CLI equality that does not need any hands.
    try:
        app.load_options()
    except sqlite3.Error:
        pass

    cases = [
        ({"flags": ["--hero", "--ip"], "pos": [], "vs": [],
          "street": ["flop"], "pot": ["3bet"], "board": []},
         ["--hero", "--ip", "--street", "flop", "--pot", "3bet"]),
        ({"flags": ["--pool"], "pos": ["BTN", "CO"], "vs": [], "street": [],
          "pot": [], "board": []},
         ["--pool", "--pos", "BTN,CO"]),
        # The matchup, which is the reason these columns exist.
        ({"flags": ["--pool", "--vs-pool"], "pos": ["BTN"], "vs": ["BB"],
          "street": [], "pot": ["3bet"], "board": []},
         ["--pool", "--vs-pool", "--pos", "BTN", "--vs", "BB",
          "--pot", "3bet"]),
        ({"flags": [], "pos": [], "vs": [], "street": [], "pot": [],
          "board": ["mono", "paired"]},
         ["--board", "mono,paired"]),
        ({"flags": [], "pos": [], "vs": [], "street": [], "pot": [],
          "board": []}, []),
        # A quick filter is a named stat used as a filter, and it has to
        # reach uild by the same road every other control does.
        ({"flags": ["--hero"], "quick": ["cbet_flop"], "pot": ["raised"]},
         ["--hero", "--quick", "cbet_flop", "--pot", "raised"]),
        # A typed line, which reaches the filter by a different road from
        # every clickable one: it is a text box rather than a state a click
        # sets, and a box that is read into the wrong flag looks like a box
        # that does nothing.
        ({"flags": ["--hero"], "vals": {"flop": "XBC", "turn": "XX"}},
         ["--hero", "--flop", "XBC", "--turn", "XX"]),
        ({"flags": [], "vals": {"node": "*/xbm"}}, ["--node", "*/xbm"]),
        # "me" in the player list is the one entry there that is not a name.
        # Picking a screen name would select one site's worth of hero's
        # hands and quietly drop the rest.
        ({"flags": [], "vals": {"player": HERO_CHOICE}}, ["--hero"]),
    ]
    for state, argv in cases:
        for f, var in app.flags.items():
            var.set(f in state["flags"])
        for g in app.multi:
            app.multi[g] = set(state.get(g, []))
        for n, var in app.vals.items():
            var.set(state.get("vals", {}).get(n, ""))
        a, _la, _pa = query.build(app.argv())
        b, _lb, _pb = query.build(argv)
        if sorted(a.split(" AND ")) != sorted(b.split(" AND ")):
            fails.append(f"{state} -> {a!r} but CLI gives {b!r}")
    print(f"window and command line agree  {len(cases) - len(fails)}/{len(cases)}")
    for f in fails:
        print(f"    {f}")

    # Every view must build its rows without raising, including on a filter
    # that matches nothing -- which is one click away at all times.
    con = connect_db(db_path)
    broke = []
    views = ("sessions", "study", "statistics", "stats", "range", "chart",
             "report", "results", "hands", "graph")
    filters = ([], ["--ip", "--street", "preflop"])
    for view in views:
        for argv in filters:
            where, _l, parts = query.build(argv)
            try:
                app._work(app.pending, view, where, _l, parts, "position",
                          None)
                app.results.get_nowait()
            except Exception as e:
                broke.append(f"{view}: {type(e).__name__}: {e}")
    tried = len(views) * len(filters)
    print(f"every view answers             {tried - len(broke)}/{tried}")
    for b in broke:
        print(f"    {b}")
    fails += broke

    # The dark theme is only dark if `clam` is the theme in use; the others
    # hand their drawing to Windows and ignore every colour set here.
    # The dialog must offer what the command line can express. A filter that
    # exists only as a flag is a filter nobody will find, and the window
    # quietly falling behind the engine is how that comes about.
    missing = [f for f in query.SWITCHES if f not in app.flags]
    dialog = FilterDialog(app)
    dialog.withdraw()
    dialog.update_idletasks()

    def clickable(w, out):
        for kid in w.winfo_children():
            if isinstance(kid, tk.Label) and kid.cget("cursor") == "hand2":
                out.append(kid.cget("text"))
            clickable(kid, out)
        return out

    # Closing the dialog must do something. It used to do nothing at all --
    # the choices stayed set and the view was never redrawn -- which is
    # indistinguishable from a filter that has no effect, and was reported
    # as exactly that.
    closer = dialog.protocol("WM_DELETE_WINDOW")
    print(f"closing the dialog          "
          f"{'applies the filter' if closer else 'DOES NOTHING'}")
    if not closer:
        fails.append("closing the filter dialog silently discards the redraw")

    offered = clickable(dialog, [])
    print(f"switches the window can set   "
          f"{len(query.SWITCHES) - len(missing)}/{len(query.SWITCHES)}")
    print(f"filters offered in the dialog {len(offered)}")
    if missing:
        fails.append(f"the window cannot set {missing}")
    if len(offered) < len(query.quick_filters()):
        fails.append("the dialog offers fewer filters than are defined")
    dialog.destroy()

    # The wheel must not outlive the window it scrolls. Each tab of the
    # dialog bound <MouseWheel> for the whole application, so every tab
    # overwrote the last and the binding survived the dialog being closed --
    # after which one turn of the wheel raised
    #     TclError: invalid command name ".!filterdialog...!canvas"
    # against a canvas that no longer existed. Reported from the log, which
    # is what the log is for. Tested by opening the dialog, shutting it, and
    # turning the wheel.
    gone = FilterDialog(app)
    gone.update_idletasks()
    gone.cancel()
    root.update()
    stale = None
    try:
        root.event_generate("<MouseWheel>", delta=-120)
        root.update()
    except tk.TclError as e:
        stale = str(e)
    print(f"the wheel outlives no window  "
          f"{'yes' if not stale else 'NO -- ' + stale[:60]}")
    if stale:
        fails.append("a mousewheel binding survived the dialog that made it")

    # A stat saved from the window and one saved from the command line have
    # to be the same stat. Both reach `query.define_stat` from the same argv,
    # and the way that stops being true is a window that assembles its own
    # filter -- which is the failure every other check in here exists to
    # prevent, arriving by a new road.
    for f, var in app.flags.items():
        var.set(f == "--ip")
    for g in app.multi:
        app.multi[g] = {"3bet"} if g == "pot" else set()
    for _n, var in app.vals.items():
        var.set("")
    naming = StatDialog(app)
    naming.withdraw()
    same = naming.chance == query.build(["--ip", "--pot", "3bet"])[0]
    print(f"the stat window names what it shows  "
          f"{'yes' if same else 'NO -- ' + naming.chance}")
    if not same:
        fails.append("the stat window would save a filter other than the one "
                     "on the screen")
    offered = set(naming.do_box.cget("values"))
    print(f"actions a saved stat can count "
          f"{len(offered & set(stats.ACTIONS))}/{len(stats.ACTIONS)}")
    if not set(stats.ACTIONS) <= offered:
        fails.append("the stat window cannot count "
                     f"{sorted(set(stats.ACTIONS) - offered)}")
    naming.close()

    # Same argument, one verb over: the report window must save the filter
    # that is on the screen, and the report box must offer every report that
    # exists. A report saved into the file and missing from the box is
    # indistinguishable from a save that failed, and that is the failure
    # this pair of assertions is for.
    keeping = ReportDialog(app)
    keeping.withdraw()
    same = keeping.argv == ["--ip", "--pot", "3bet"]
    print(f"the report window names what it shows  "
          f"{'yes' if same else 'NO -- ' + repr(keeping.argv)}")
    if not same:
        fails.append("the report window would save a filter other than the "
                     "one on the screen")
    keeping.close()

    app.reload_reports()
    offered_reports = set(app.preset_box.cget("values")) - {""}
    known_reports = set(query.reports())
    print(f"reports the box offers        "
          f"{len(offered_reports & known_reports)}/{len(known_reports)}")
    if not known_reports <= offered_reports:
        fails.append("the report box is missing "
                     f"{sorted(known_reports - offered_reports)}")

    # Applying a neighbouring spot must produce the same filter the
    # command line would. The leftover street/pot clicks used to stay
    # set, so opening "Flop c-bets" while river was still clicked
    # matched nothing and looked like a broken report.
    for f, var in app.flags.items():
        var.set(False)
    for g in app.multi:
        app.multi[g] = set()
    for _n, var in app.vals.items():
        var.set("")
    app.preset.set("")
    app._apply_argv(["--street", "flop", "--pfa", "--facing", "check"])
    a, _la, _ = query.build(app.argv())
    b, _lb, _ = query.build(list(query.SMART_REPORTS["Flop c-bets"]))
    same_spot = sorted(a.split(" AND ")) == sorted(b.split(" AND "))
    print(f"applying a spot matches its flags  "
          f"{'yes' if same_spot else 'NO -- ' + a}")
    if not same_spot:
        fails.append("apply_argv does not rebuild the filter it was given")
    app.multi["street"] = {"river"}
    app.apply_report("Flop vs c-bet")
    leftover = bool(app.multi["street"])
    same = query._canonical(app.argv()) == query._canonical(
        query.SMART_REPORTS["Flop vs c-bet"])
    print(f"opening a report drops the old street  "
          f"{'yes' if same and not leftover else 'NO'}")
    if leftover or not same:
        fails.append("opening a Smart Report left the previous situation on")

    app.clear_situation()
    app.crumbs = []
    app._apply_argv(["--stack", "80-120", "--action", "call",
                     "--combo", "Axs", "--result", "won",
                     "--hist-group", "air"])
    a, _, _ = query.build(app.argv())
    b, _, _ = query.build(["--stack", "80-120", "--action", "call",
                           "--combo", "Axs", "--result", "won",
                           "--hist-group", "air"])
    same_study = sorted(a.split(" AND ")) == sorted(b.split(" AND "))
    print(f"study flags round-trip        "
          f"{'yes' if same_study else 'NO -- ' + a}")
    if not same_study:
        fails.append("study flags did not survive apply_argv")
    app.clear_situation()
    app.crumbs = [{"flag": "--stack", "value": "80-120", "label": "80-120"}]
    child = query.drill_child([], {"flag": "--stack", "value": "80-120"})
    same = query._canonical(app.argv()) == query._canonical(child)
    print(f"a crumb is parent ∧ row       "
          f"{'yes' if same else 'NO -- ' + repr(app.argv())}")
    if not same:
        fails.append("study crumb did not AND onto the parent filter")
    if "study" not in app.tabs:
        fails.append("the window has no study cockpit tab")
    print(f"study cockpit tab             "
          f"{'yes' if 'study' in app.tabs else 'NO'}")
    if "sessions" not in app.tabs:
        fails.append("the window has no Sessions tab")
    print(f"sessions tab                  "
          f"{'yes' if 'sessions' in app.tabs else 'NO'}")
    if "statistics" not in app.tabs:
        fails.append("the window has no Statistics tab")
    print(f"statistics tab                "
          f"{'yes' if 'statistics' in app.tabs else 'NO'}")
    if not hasattr(app, "stat_hist") or not hasattr(app, "study_hist"):
        fails.append("histogram widget missing on Statistics or Reports")
    else:
        blob = {"n": 10, "weak_pct": 50.0, "weak_n": 5, "rows": [
            {"key": "air", "label": "Air", "n": 5, "pct": 50.0,
             "is_weak": True, "flag": "--hist-group", "value": "air"},
            {"key": "other", "label": "Other", "n": 5, "pct": 50.0,
             "is_weak": False, "flag": "--hist-group", "value": "other"},
        ], "coverage": {"sites": []}}
        app.stat_hist.set(blob)
        air = next(r for r in app.stat_hist.got["rows"] if r["key"] == "air")
        air["is_weak"] = False
        moved = query.weak_pct_of(app.stat_hist.got["rows"], 10)
        if abs(moved - 0.0) > 1e-9:
            fails.append(f"flipping is_weak left Weak % {moved}, not 0")
        print(f"histogram Weak % flips        "
              f"{'yes' if abs(moved - 0.0) <= 1e-9 else 'NO'}")
    app.stat_fmt.set("cash")
    app.stat_last_n.set("10")
    app.stat_exclude.set(True)
    sargv = app.statistics_argv()
    if "--fmt" not in sargv or "cash" not in sargv:
        fails.append("Statistics argv dropped cash mode")
    if "--last-sessions" not in sargv or "10" not in sargv:
        fails.append("Statistics argv dropped last-N sessions")
    if "--exclude-reg-vs-fish" in sargv or "--exclude-reg-vs-fish" in app.argv():
        fails.append("exclude_reg_vs_fish leaked into argv -- Reports "
                     "would change with the Statistics toggle")
    print(f"exclude stays off Reports     "
          f"{'yes' if '--exclude-reg-vs-fish' not in app.argv() else 'NO'}")
    app.stat_key = "threebet"
    app.stat_kind = "action"
    app.stat_combo = None
    opened = query.reports_argv(app.statistics_argv(), "threebet", "action")
    app.clear_situation()
    app._apply_argv(opened, who=True)
    if "--quick" not in app.argv() or "threebet" not in app.argv():
        fails.append("Open in Reports did not apply --quick threebet")
    if "--exclude-reg-vs-fish" in app.argv():
        fails.append("Open in Reports applied the exclude flag")
    if "--fmt" not in app.argv() or "cash" not in app.argv():
        fails.append("Open in Reports dropped cash mode")
    if "--last-sessions" not in app.argv() or "10" not in app.argv():
        fails.append("Open in Reports dropped last-N sessions")
    print(f"Open in Reports is --quick     "
          f"{'yes' if '--quick' in app.argv() else 'NO'}")
    # Cash / last-N are who-context and survive clear_situation.
    # Leave them on the widgets and the custom-builder check
    # below compares unequal for a reason that is not its own.
    app.vals["fmt"].set("")
    app.vals["last_sessions"].set("")
    app.vals["session"].set("h1")
    if "--session" not in app.argv() or "h1" not in app.argv():
        fails.append("Open-in-Reports session chip did not reach argv")
    print(f"session chip reaches argv     "
          f"{'yes' if '--session' in app.argv() else 'NO'}")
    app.vals["session"].set("")
    app.flags["--today"].set(True)
    app.vals["hours"].set("")
    if "--today" not in app.argv():
        fails.append("Today on the dialog did not reach argv")
    print(f"today flag reaches argv       "
          f"{'yes' if '--today' in app.argv() else 'NO'}")
    app.flags["--today"].set(False)
    if not getattr(app, "study_trees", None) or \
            set(query.DEFAULT_STUDY_PANES) - set(app.study_trees):
        fails.append("study default panes are missing")
    print(f"default study panes           "
          f"{len(getattr(app, 'study_trees', {}))}/"
          f"{len(query.DEFAULT_STUDY_PANES)}")

    # Detach: nest Stack 80-120, open a window, keep pin in-main,
    # close without breaking the cockpit, cap at four.
    app.clear_situation()
    app.pin.set("")
    app._pin_alias.clear()
    app.crumbs = [{"flag": "--stack", "value": "80-120", "label": "80-120"}]
    app._study_out = {
        "panes": {"stack": {"id": "stack", "rows": [
            {"key": "80-120", "label": "80-120", "n": 10, "pct": 20.0,
             "flag": "--stack", "value": "80-120"}]},
                  "next": {"id": "next", "rows": [
            {"key": "call", "label": "Call", "n": 4, "pct": 40.0,
             "flag": "--action", "value": "call"}]}},
        "hands": [{"id": "h1", "seat": 1, "when": "2026-09-01",
                   "site": "acr", "pos": "BTN", "combo": "AKs",
                   "net": 2.0, "act": 1.0, "compact": "BTN _R3_"}],
        "graph": None, "hist": None,
    }
    nest_argv = app.argv()
    if query._canonical(query.report_context(nest_argv)) != \
            query._canonical(nest_argv):
        fails.append("window argv is not the pin report context")
    win = app._detach_pane("stack")
    if win is None:
        fails.append("Detach did not open Stack Sizes")
    else:
        win.withdraw()
        if "80-120" not in win.title() or "Stack" not in win.title():
            fails.append(f"detached title drifted: {win.title()!r}")
        if "hero" not in win.title() and "all" not in win.title():
            fails.append(f"detached title dropped the subject: {win.title()!r}")
        if win.tree is None or win.hands is None:
            fails.append("detached stack window has no table or hands")
        if app.study_trees.get("stack") is None:
            fails.append("Detach removed the in-main Stack pane")
        # Pin stays in-main and still writes the same context id.
        app._pin_step({"flag": "--action", "value": "call", "label": "Call"})
        if not app.pin.get():
            fails.append("Pin did not stay in-main after Detach")
        pinned = app._pin_name()
        this, other = query.pin_sides(app.argv(), pinned)
        if query._canonical(this) != query._canonical(app.argv()):
            fails.append("pin THIS side drifted from the detached nest")
        if "--action" not in other or "call" not in other:
            fails.append("pin of a row after Detach lost the other side")
        # Live: a further drill updates the detached title.
        app.crumbs.append({"flag": "--action", "value": "call",
                           "label": "Call"})
        app._sync_detached()
        if "call" not in win.title().lower() and "Call" not in win.title():
            fails.append(f"live title did not follow the next drill: "
                         f"{win.title()!r}")
        win._close()
        if app.study_trees.get("stack") is None:
            fails.append("closing a detached pane broke the main Stack")
        if app._alive_detached():
            fails.append("closed detach stayed in the host list")
        # Pin still works after the window is gone.
        if not app.pin.get():
            fails.append("closing Detach cleared the in-main pin")
    gwin = app._detach_pane("graph")
    if gwin is None or gwin.graph is None:
        fails.append("Detach did not open Win Graph")
    else:
        gwin.withdraw()
        if "Win Graph" not in gwin.title():
            fails.append(f"graph title drifted: {gwin.title()!r}")
        gwin._close()
    # Cap: four open, a fifth is refused, closing frees a slot.
    opened = []
    for name in ("results", "stack", "position", "next"):
        w = app._detach_pane(name)
        if w is None:
            fails.append(f"Detach refused {name} under the cap")
        else:
            w.withdraw()
            opened.append(w)
    fifth = app._detach_pane("graph")
    if fifth is not None:
        fails.append("detach cap did not hold at 4")
        try:
            fifth._close()
        except tk.TclError:
            pass
    if opened:
        opened[0]._close()
        extra = app._detach_pane("graph")
        if extra is None:
            fails.append("closing a detached pane did not free the cap")
        else:
            extra.withdraw()
            extra._close()
    for w in opened[1:]:
        try:
            w._close()
        except tk.TclError:
            pass
    print(f"detach + pin coexist          "
          f"{'yes' if not [f for f in fails if 'detach' in f.lower() or 'Detach' in f or 'pin THIS' in f or 'Pin did' in f] else 'NO'}")

    app.clear_situation()
    app._apply_argv(["--first-in", "--first-raise", "--last-action",
                     "--size", "0.4-0.75", "--stack", "100+",
                     "--outcome", "fold-out"])
    built, _, _ = query.build(app.argv())
    want, _, _ = query.build(["--first-in", "--first-raise",
                              "--last-action", "--size", "0.4-0.75",
                              "--stack", "100+", "--outcome", "fold-out"])
    same_custom = (sorted(built.split(" AND "))
                   == sorted(want.split(" AND ")))
    print(f"custom builder flags round-trip  "
          f"{'yes' if same_custom else 'NO -- ' + built}")
    if not same_custom:
        fails.append("custom builder flags did not survive apply_argv")

    theme = ttk.Style(root).theme_use()
    print(f"theme in use                   {theme}")
    if theme != "clam":
        fails.append(f"theme is {theme}, which will not honour dark colours")

    con.close()
    root.destroy()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    diag.setup(verbose="--debug" in argv)
    if "--check" in argv:
        return 0 if check() else 1
    if not DB.exists():
        print(f"no database at {DB} -- load some hands first")
        return 1
    root = tk.Tk()
    root.title("poker_analysis")
    root.geometry("1360x880")
    root.minsize(1050, 640)
    dark(root)
    dark_titlebar(root)
    diag.watch_tk(root)
    # Checked on every launch, on a worker, and never applied to a running
    # program: see `update.py`. `--no-update` turns it off entirely.
    app = App(root, check_updates="--no-update" not in argv)
    app._menu(root)
    app.load_options()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
