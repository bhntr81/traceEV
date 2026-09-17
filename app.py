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

Anything that goes wrong is written to `TraceEV.log` beside the
program, including the failures Tk would otherwise swallow. `python diag.py`
prints the end of it.

Hands come in through the Import menu -- a folder, a file, another database,
or whatever it can find on this computer. Nothing there asks which site the
hands are from; every file is identified by reading it.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import sqlite3

import ask
import diag
import importer
import players
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
query.DB = DB
# The saved stats are the user's as much as the database is, so they sit
# beside it rather than beside the code -- which, frozen, is a directory
# PyInstaller deletes on the way out. Loading them again here is what puts
# them into the registry every view already reads: the copy loaded when
# `stats` was imported looked next to the code and found nothing there.
stats.DB = DB
stats.load_custom(HERE / "stats.json")
query.SAVED = HERE / "filters.json"

# One palette, so a colour is changed in one place. The line colours are the
# same four the graph has always used.
BG, PANEL, EDGE = "#14161a", "#1b1e24", "#2a2f38"
INK, DIM, ACCENT = "#d8dbe0", "#8b929c", "#4c9aff"
GOOD, BAD, WARN = "#22a35a", "#d1443c", "#b8892a"
LINE = {"total": "#22a35a", "showdown": "#2f7fd6",
        "nonshowdown": "#d1443c", "allin_ev": "#e0b020"}
# Painted in this order, so the headline is the one on top. Drawing them in
# the order above put the all-in EV line over the total, and where the two
# agree -- which they do exactly when nothing could be adjusted -- the green
# line was invisible and looked missing. It was underneath.
DRAW_ORDER = ("allin_ev", "nonshowdown", "showdown", "total")

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
       ("--with-fish", "a fish is in the pot"),
       ("--fish-left", "a fish on my left (acts after me)"),
       ("--fish-right", "a fish on my right (acts before me)"),
       ("--reg-left", "a reg on my left"),
       ("--reg-right", "a reg on my right")]
SITUATIONS = [("--ip", "in position"), ("--oop", "out of position"),
              ("--pfa", "was the raiser"), ("--vs-pfa", "facing the raiser"),
              ("--multiway", "multiway"), ("--headsup", "heads up"),
              ("--allin", "all-in")]
# Turning one of these on turns its opposite off, or the filter selects
# nothing and looks broken rather than contradictory.
OPPOSITES = {"--hero": "--pool", "--pool": "--hero", "--ip": "--oop",
             "--oop": "--ip", "--multiway": "--headsup",
             "--headsup": "--multiway",
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
    style.configure("Tag.TButton", padding=(4, 0))
    style.configure("Head.TLabel", background=BG, foreground=DIM,
                    font=(UI, 8, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=INK,
                    font=(UI, 11, "bold"))
    # The subject line -- WHO the window is about -- is the largest text
    # on it, because it was the hardest thing to find.
    style.configure("Subject.TLabel", background=BG, foreground=INK,
                    font=(UI, 15, "bold"))
    style.configure("Warn.TLabel", background=BG, foreground=WARN,
                    font=(UI, 10, "bold"))
    style.configure("On.TButton", background=ACCENT, foreground="#08111f",
                    font=(UI, 11, "bold"), padding=(14, 6))
    style.map("On.TButton", background=[("active", "#5ea6ff")])
    style.configure("Off.TButton", background=PANEL, foreground=DIM,
                    font=(UI, 11), padding=(14, 6))
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
    """
    Which lines are sitting exactly on top of which, and under what.

    Drawn last wins, so a line that agrees with one painted after it is
    invisible. Reported rather than nudged apart: two results that are equal
    are equal, and moving one to prove it exists would be a lie drawn to
    look like data.
    """
    out = {}
    for i, key in enumerate(DRAW_ORDER):
        for later in DRAW_ORDER[i + 1:]:
            a, b = series.get(key), series.get(later)
            if a and b and len(a) == len(b) and all(
                    abs(x - y) <= slack for x, y in zip(a, b)):
                out[key] = later
                break
    return out


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
            "TraceEV",
            f"Running from {where}.\n\n"
            f"Commit: {update.head() or 'unknown'}\n"
            f"Repository: {update.remote_repo()}\n\n"
            f"The database is at:\n{DB}")

    def show_log(self):
        win = tk.Toplevel(self.master)
        win.title("TraceEV.log")
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
                            self.cache.clear()
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


# The interpreter hands the GIL to another thread every 5ms by default,
# which is fine for two threads sharing work and not fine for one thread
# drawing a window while another prices all-in pots in pure Python: the
# window got its turn too rarely to keep up with the mouse and Windows
# called it "not responding". A shorter turn costs the query a little and
# keeps the window drawing.
sys.setswitchinterval(0.001)


class App(ImportMixin, ttk.Frame):
    def __init__(self, master, check_updates=False):
        super().__init__(master)
        self.pack(fill="both", expand=True)
        self.con = sqlite3.connect(DB, check_same_thread=False)
        # Every switch the command line has, whether or not a control for
        # it has been built yet. These used to appear as a side effect of
        # drawing the rail, so deleting the rail silently emptied the filter.
        self.flags = {f: tk.BooleanVar() for f in query.SWITCHES}
        self.multi = {"pos": set(), "vs": set(), "street": set(),
                      "pot": set(), "board": set(), "quick": set(),
                      "made": set(), "kicker": set(), "fd": set(),
                      "sd": set(), "turn_card": set(), "river_card": set(),
                      "facing": set()}
        # The filter's values live here rather than on the widgets, because
        # the widgets belong to a dialog that is destroyed every time it is
        # closed and the filter is not.
        self.vals = {n: tk.StringVar() for n in
                     ("site", "stake", "player", "deep", "short",
                      "since", "until", "where",
                      "line", "node", "my_line", "my_node",
                      "pre", "flop", "turn", "river",
                      "hour", "weekday", "session_len", "session_min",
                      "tables", "tag")}
        self.options = {"sites": [], "stakes": [], "players": []}
        self.cohort_spec = None

        self.results = queue.Queue()
        # Requests go through one queue to one worker, and a request that
        # is still waiting when the next arrives is never run. Each tab
        # click used to start its own thread, and five quick clicks were
        # five heavy queries fighting one interpreter -- the window went
        # "not responding" while they took turns at the GIL. Answers to a
        # view already computed for this filter come from the cache and
        # never leave the interface thread at all.
        self.requests = queue.Queue()
        self.cache = {}
        threading.Thread(target=self._worker, daemon=True).start()
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
        ttk.Label(head, text="TraceEV",
                  style="Title.TLabel").pack(side="left")
        self.sub = ttk.Label(head, text="", style="Dim.TLabel")
        self.sub.pack(side="left", padx=12)
        # Packed only when it has something to report -- see `_say_update`.
        self.banner = ttk.Label(head, text="", style="Dim.TLabel")
        self.status = ttk.Label(head, text="", style="Dim.TLabel")
        self.status.pack(side="right")

        # WHO the window is about, before anything else. This was the last
        # heading on the last page of the filter dialog, and nothing on the
        # window said which was chosen -- the first thing a person needs to
        # know, found by reading a dim line under a button. Now it is two
        # buttons and a headline: ME, THE POOL, and the site, because a pool
        # with no site is three games averaged into one number.
        who = ttk.Frame(self)
        who.pack(fill="x", padx=18, pady=(0, 6))
        ttk.Label(who, text="looking at", style="Dim.TLabel").pack(
            side="left", padx=(0, 10))
        self.me_btn = ttk.Button(who, text="ME", command=lambda: self.set_who("--hero"))
        self.me_btn.pack(side="left")
        self.pool_btn = ttk.Button(who, text="THE POOL",
                                   command=lambda: self.set_who("--pool"))
        self.pool_btn.pack(side="left", padx=(4, 0))
        ttk.Label(who, text="on", style="Dim.TLabel").pack(side="left", padx=(16, 6))
        self.site_box = ttk.Combobox(who, textvariable=self.vals["site"],
                                     values=["any site"], state="readonly",
                                     width=14)
        self.site_box.pack(side="left")
        self.site_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.subject = ttk.Label(who, text="", style="Subject.TLabel")
        self.subject.pack(side="left", padx=(24, 0))
        self.subject_note = ttk.Label(who, text="", style="Warn.TLabel")
        self.subject_note.pack(side="left", padx=(12, 0))

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
            width=22)
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>",
                             lambda _e: self.refresh())
        self.clear_btn = ttk.Button(bar, text="clear", command=self.clear_filters)
        self.summary = ttk.Label(bar, text="all hands", style="Dim.TLabel")
        self.summary.pack(side="left", padx=12)
        ttk.Button(bar, text="Ask  ▸", command=self.toggle_ask).pack(side="right")
        ttk.Separator(self).pack(fill="x")

        # The answer on the left, the assistant on the right when it is
        # open. A panel rather than a window, because the point is to ask
        # about what is on screen and read the answer beside it.
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        self._views(right)
        self.ask_panel = AskPanel(body, self)

    def open_filters(self):
        FilterDialog(self)

    def toggle_ask(self):
        self.ask_panel.toggle()

    def set_who(self, flag):
        """
        ME or THE POOL, from the buttons. Pressing the lit one turns both
        off, which is "everyone" -- me and the pool together -- and the
        headline says so, since that is rarely what anybody means.
        """
        twin = OPPOSITES[flag]
        if self.flags[flag].get():
            self.flags[flag].set(False)
        else:
            self.flags[flag].set(True)
            self.flags[twin].set(False)
        self.refresh()

    def paint_subject(self):
        """The headline, and which button is lit, from the filter's state."""
        me, pool = self.flags["--hero"].get(), self.flags["--pool"].get()
        self.me_btn.configure(style="On.TButton" if me else "Off.TButton")
        self.pool_btn.configure(style="On.TButton" if pool else "Off.TButton")
        site = self.vals["site"].get().strip()
        if not site or site.startswith("any "):
            site = ""
        subject = "ME" if me else "THE POOL" if pool else "EVERYONE"
        self.subject.configure(text=subject + (f"  ·  {site}" if site else
                                               "  ·  all sites"))
        note = ""
        if pool and not site:
            note = "pick a site -- three sites are three different games"
        elif not me and not pool:
            note = "me and the pool together; pick one"
        self.subject_note.configure(text=note)

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
        self.refresh()

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
        self.preset_box.configure(values=["" ] + known)
        self.preset.set(keep if keep in known else "")


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
        self.filter_line.pack(anchor="w", padx=14, pady=(0, 8))

        self.tabs = {}
        for name in ("stats", "actions", "range", "chart", "report", "results",
                     "graph", "hands", "sessions"):
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=name)
            self.tabs[name] = frame
        self.tree = {}
        for name in ("stats", "actions", "range", "report", "results", "hands",
                     "sessions"):
            self.tree[name] = self._table(self.tabs[name])
        self.canvas = tk.Canvas(self.tabs["graph"], bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_graph())
        self.series = None
        # Whether a graph query has answered yet. Before it has, the canvas
        # is empty for no reason the filter can be blamed for, and it used
        # to say "not enough hands" -- which was read as a verdict.
        self.graph_ran = False
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
                argv += [flags.get(field, "--" + field), value]
            if site:
                argv += ["--site", site]
            if klass:
                argv += ["--class", klass]
            if durable is not None:
                argv += ["--durable", str(durable)]
        # "Against" is the pot's matchup, in the user's words: BTN vs BB is
        # any time the button raised and the big blind did not fold. With
        # no "my position" chosen it is every seat's pots against those.
        if self.multi.get("vs"):
            mine = sorted(self.multi.get("pos") or set(POSITIONS))
            pairs = [f"{a},{b}" for a in mine
                     for b in sorted(self.multi["vs"]) if a != b]
            if pairs:
                argv += ["--matchup", ";".join(pairs)]
        for group, flag in (("pos", "--pos"),
                            ("street", "--street"), ("pot", "--pot"),
                            ("board", "--board"), ("quick", "--quick"),
                            ("made", "--made"), ("kicker", "--kicker"),
                            ("fd", "--fd"), ("sd", "--sd"),
                            ("turn_card", "--turn-card"),
                            ("river_card", "--river-card"),
                            ("facing", "--facing")):
            if self.multi.get(group):
                argv += [flag, ",".join(sorted(self.multi[group]))]
        for name, flag in (("site", "--site"), ("stake", "--stake"),
                           ("player", "--player"), ("deep", "--deep"),
                           ("short", "--short"), ("since", "--since"),
                           ("until", "--until"), ("where", "--where"),
                           ("line", "--line"), ("node", "--node"),
                           ("my_line", "--my-line"), ("my_node", "--my-node"),
                           ("pre", "--pre"), ("flop", "--flop"),
                           ("turn", "--turn"), ("river", "--river"),
                           ("hour", "--hour"), ("weekday", "--weekday"),
                           ("session_len", "--session-len"),
                           ("session_min", "--session-min"),
                           ("tables", "--tables"), ("tag", "--tag")):
            v = self.vals[name].get().strip()
            if not v or v.startswith("any "):
                continue
            if name == "player" and v == HERO_CHOICE:
                # Not a name, so not `--player`: hero is a different name on
                # each site and none of them covers the others.
                if "--hero" not in argv:
                    argv.append("--hero")
                continue
            argv += [flag, v]
        return argv


    # Which box each value flag fills, the inverse of the table in `argv`.
    MULTI_OF = {"--pos": "pos", "--vs": "vs", "--street": "street",
                "--pot": "pot", "--board": "board", "--quick": "quick",
                "--made": "made", "--kicker": "kicker", "--fd": "fd",
                "--sd": "sd", "--turn-card": "turn_card",
                "--river-card": "river_card", "--facing": "facing"}
    VAL_OF = {"--site": "site", "--stake": "stake", "--player": "player",
              "--deep": "deep", "--short": "short", "--since": "since",
              "--until": "until", "--where": "where", "--line": "line",
              "--node": "node", "--my-line": "my_line", "--my-node": "my_node",
              "--pre": "pre", "--flop": "flop",
              "--turn": "turn", "--river": "river", "--hour": "hour",
              "--weekday": "weekday", "--session-len": "session_len",
              "--session-min": "session_min", "--tables": "tables",
              "--tag": "tag"}
    TAB_OF = {"--stats": "stats", "--results": "results", "--hands": "hands",
              "--range": "range", "--chart": "chart", "--sessions": "sessions",
              "--actions": "actions",
              "--graph": "graph"}

    def apply_argv(self, argv):
        """
        A command line, into the window's own boxes -- the inverse of `argv`.

        This is what makes the assistant's answer a starting point rather
        than a dead end: the flags it ran become the window's filter, the
        tab it implied is selected, and every view shows the same hands
        the answer was about. Flags the window has no box for (`--show`,
        `--min`, a cohort) are dropped, and the summary line beneath the
        bar shows what was kept.
        """
        self.clear_filters()
        tab, argv = "stats", list(argv)
        i = 0
        while i < len(argv):
            a = argv[i]
            if a in self.TAB_OF:
                tab = self.TAB_OF[a]
                i += 1
            elif a in self.flags:
                self.flags[a].set(True)
                twin = OPPOSITES.get(a)
                if twin:
                    self.flags[twin].set(False)
                i += 1
            elif a == "--matchup" and i + 1 < len(argv):
                # Pairs back into the two boxes: the positions named on
                # the left go to "my position" unless one is already
                # chosen, the ones on the right to "against".
                pairs = [p.replace("/", ",").split(",") for p in argv[i + 1].split(";")]
                self.multi["vs"] = {b.strip().upper() for _a, b in pairs if b.strip()}
                if not self.multi.get("pos"):
                    self.multi["pos"] = {a.strip().upper() for a, _b in pairs if a.strip()}
                i += 2
            elif a in self.MULTI_OF and i + 1 < len(argv):
                self.multi[self.MULTI_OF[a]] = set(argv[i + 1].split(","))
                i += 2
            elif a in self.VAL_OF and i + 1 < len(argv):
                self.vals[self.VAL_OF[a]].set(argv[i + 1])
                i += 2
            elif a == "--by" and i + 1 < len(argv):
                if argv[i + 1] in query.DIMENSIONS:
                    self.by.set(argv[i + 1])
                    if tab == "stats":
                        tab = "report"
                i += 2
            elif a in query.OPTIONS and i + 1 < len(argv):
                i += 2                    # --show, --min: no box for these
            else:
                i += 1
        for name, frame in self.tabs.items():
            if name == tab:
                self.nb.select(frame)
        self.refresh()

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
            argv = self.argv()
            cohort_spec, query_argv = players.parse_cohort(argv)
            # Results and the graph are somebody's money. With nobody
            # chosen they summed every seat at every table -- 112,936
            # "hands" and the rake's worth of loss -- which is a number
            # about nothing. They are yours until the pool is pressed.
            if view in ("results", "graph") and not any(
                    a in query_argv for a in ("--hero", "--pool", "--player",
                                              "--vs-player", "--reg", "--fish")):
                query_argv = ["--hero"] + query_argv
            where, label, parts = query.build(query_argv)
        except SystemExit as e:
            self.filter_line.configure(text=str(e))
            return
        diag.event("refresh", view=view, filter=label)
        self.filter_line.configure(text="filter: " + label)
        self.paint_subject()
        self.summary.configure(text=self.describe_filter())
        if self.argv():
            self.clear_btn.pack(side="left", padx=(6, 0))
        else:
            self.clear_btn.pack_forget()
        self.pending += 1
        token = self.pending
        key = (view, where, self.by.get(), self.chart_stat(), repr(cohort_spec))
        if key in self.cache:
            self.status.configure(text="")
            self._render(self.cache[key])
            return
        self.status.configure(text="working…")
        self.requests.put((token, key, view, where, label, parts,
                           self.by.get(), cohort_spec, self.chart_stat()))

    def _worker(self):
        """The one thread every query runs on; see `_work`."""
        while True:
            item = self.requests.get()
            # Anything queued behind it supersedes it: only the newest
            # request is worth the seconds it may take.
            while True:
                try:
                    item = self.requests.get_nowait()
                except queue.Empty:
                    break
            token, key, *args = item
            if token != self.pending:
                continue
            out = self._work(token, *args)
            if out is not None and not out.get("error"):
                self.cache[key] = out
                if len(self.cache) > 64:
                    self.cache.pop(next(iter(self.cache)))

    def chart_stat(self):
        """Which stat the chart is of, or None for the range itself."""
        chosen = self.of.get()
        for st in STATS:
            if st.label == chosen and st.source == "d":
                return st.key
        return None

    def _work(self, token, view, where, label, parts, dim, cohort_spec,
              stat=None):
        """
        Every query runs here, never on the interface thread.

        A window that stops repainting while it thinks looks broken, and some
        of these take seconds: the graph prices all-ins the first time it sees
        them. So the work happens on a thread and the answer is posted back
        through a queue, with a token so that a slow answer to a filter the
        user has already changed is discarded rather than drawn.
        """
        con = sqlite3.connect(DB)
        try:
            if cohort_spec is not None:
                count = query.select_cohort(con, cohort_spec)
                label += (f", cohort: "
                          f"{players.describe_cohort(cohort_spec)} "
                          f"({count} players)")
                where = (f"({where}) AND EXISTS (SELECT 1 FROM _cohort c "
                         "WHERE c.site = decisions.site AND "
                         "c.player = decisions.player)")
            out = {"view": view}
            if view == "stats":
                out["n"], out["rows"] = query.stats_of(con, where)
            elif view == "range":
                out.update(query.range_of(con, where))
            elif view == "sessions":
                out["rows"] = query.sessions_of(con, where)
            elif view == "actions":
                out["rows"] = query.actions_of(con, where)
            elif view == "chart":
                out.update(query.chart_of(con, where, stat))
            elif view == "report":
                expr, order = query.DIMENSIONS[dim]
                cols = query.DEFAULT_COLUMNS
                grid = {c: query.rates_by(con, BY_KEY[c], expr, where)
                        for c in cols}
                counts = query.rates_by(con, BY_KEY["vpip"], expr, where)
                keys = sorted({k for g in grid.values() for k in g},
                              key=lambda k: order(k) if k is not None else "")
                out.update(dim=dim, cols=cols, grid=grid, counts=counts,
                           keys=keys)
            elif view == "results":
                pairs = query.matching_seats(con, where)
                out["totals"] = query.results_of(con, pairs) if pairs else None
            elif view == "hands":
                out["rows"] = query.hands_of(con, where, limit=500)
            elif view == "graph":
                out["series"] = self._series(con, where)
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
        return out

    @staticmethod
    def _any(out):
        return bool(out.get("n") or out.get("rows") or out.get("totals")
                    or out.get("keys") or out.get("series")
                    or out.get("cells"))

    def _series(self, con, where):
        pairs = query.matching_seats(con, where)
        if len(pairs) < 2:
            return None
        query.select_into(con, pairs)
        hands, adj, skipped = query.adjusted(con, pairs)
        if len(hands) < 2:
            return None
        s = {k: [] for k in LINE}
        total = sd = nsd = ev = 0.0
        for _when, net, was_sd, ev_net in hands:
            total += net or 0.0
            ev += ev_net or 0.0
            if was_sd:
                sd += net or 0.0
            else:
                nsd += net or 0.0
            s["total"].append(total)
            s["showdown"].append(sd)
            s["nonshowdown"].append(nsd)
            s["allin_ev"].append(ev)
        s["_note"] = (f"{len(hands):,} hands · {adj} all-in pots at equity"
                      + (f" · {skipped} unadjusted" if skipped else ""))
        return s

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
        if view == "graph":
            self.series = out.get("series")
            self.graph_ran = True
            self._draw_graph(out.get("why") or out.get("error"))
            return
        if view == "chart":
            self.chart = out if out.get("cells") else None
            self._draw_chart(out.get("why") or out.get("error"))
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

    def _render_stats(self, tv, out):
        self._cols(tv, ("stat", "value", "±", "n"), (230, 90, 70, 100),
                   {"stat": "w"})
        group = None
        for r in out["rows"]:
            if r["group"] != group:
                group = r["group"]
                tv.insert("", "end", values=(group.upper(), "", "", ""),
                          tags=("group",))
            tv.insert("", "end", tags=("thin",) if r["n"] < 30 else (),
                      values=(r["label"], f"{r['pct']:.1f}%",
                              # A band under a point still has a size, and
                              # "±0" reads as a number that failed to print.
                              f"±{r['band']:.1f}" if r["band"] < 1
                              else f"±{r['band']:.0f}",
                              f"{r['n']:,}"))

    def _render_actions(self, tv, out):
        """
        A row per action taken in the spot: how often, what it made, and
        what the next player did -- Hand2Note's action report.
        """
        self._cols(tv, ("action", "n", "freq", "bb/hand", "±", "then fold",
                        "check", "call", "bet", "raise", "street over"),
                   (90, 70, 70, 90, 60, 90, 70, 70, 70, 70, 90),
                   {"action": "w"})
        for r in out.get("rows") or []:
            nx = r["next"]
            m = max(1, sum(nx.values()))
            pct = lambda k: f"{100 * nx[k] / m:.0f}%"
            tv.insert("", "end",
                      tags=("pos",) if r["bb"] > 0 else ("neg",) if r["bb"] < 0 else (),
                      values=(r["action"], f"{r['n']:,}", f"{r['freq']:.1f}%",
                              f"{r['bb']:+.2f}", f"{r['se']:.2f}",
                              pct("fold"), pct("check"), pct("call"),
                              pct("bet"), pct("raise"), pct("street over")))
        tv.insert("", "end", values=("", "", "", "", "", "", "", "", "", "", ""))
        tv.insert("", "end", tags=("note",), values=(
            "bb/hand is the whole hand's result, averaged over the hands the "
            "action was taken in, with its standard error; 'then' is the next "
            "player's action on the same street", "", "", "", "", "", "", "",
            "", "", ""))

    def _render_sessions(self, tv, out):
        """
        The sittings the filter's hands belong to, newest first.

        `hit` is how many of the sitting's hands the filter selected, beside
        the sitting's whole result -- so a losing night and a filter that
        happens to land on one are told apart at a glance. The clock is the
        site's, which the last row says, because an "evening" that is
        somebody else's evening is the kind of thing that reads as a finding.
        """
        self._cols(tv, ("started", "site", "mins", "hands", "hit", "tables",
                        "net bb", "ev bb", "bb/100"),
                   (150, 90, 60, 70, 60, 60, 90, 90, 80),
                   {"started": "w", "site": "w"})
        rows = out.get("rows") or []
        if not rows:
            tv.insert("", "end", tags=("note",), values=(
                "no session holds a hand this filter selects",
                "", "", "", "", "", "", "", ""))
            return
        for r in rows:
            tag = ("pos",) if r["net_bb"] > 0 else ("neg",) if r["net_bb"] < 0 else ()
            tv.insert("", "end", tags=tag, values=(
                r["started"][:16], r["site"], f"{r['minutes']:.0f}",
                f"{r['hands']:,}", f"{r['matched']:,}", r["tables"],
                f"{r['net_bb']:+.1f}", f"{r['ev_bb']:+.1f}",
                "" if r["bb100"] is None else f"{r['bb100']:+.1f}"))
        n = sum(r["hands"] for r in rows)
        net = sum(r["net_bb"] for r in rows)
        tv.insert("", "end", values=("", "", "", "", "", "", "", "", ""))
        tv.insert("", "end", tags=("group",), values=(
            f"{len(rows)} SESSIONS", "", "", f"{n:,}", "", "",
            f"{net:+.1f}", "", ""))
        tv.insert("", "end", tags=("note",), values=(
            "the clock is the site's, not yours", "", "", "", "", "", "", "", ""))

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
            tv.insert("", "end", tags=("neg",) if r["weak"] else
                      ("pos",) if r["tier"] == "strong" else (),
                      values=(r["made"], f"{r['pct']:.1f}%", f"{r['n']:,}",
                              r["tier"] if r["tier"] != "medium" else "",
                              "█" * int(round(r["pct"] / 2))))
        tv.insert("", "end", values=("", "", "", "", ""))
        tv.insert("", "end", tags=("pos",), values=(
            "STRONG", f"{out['strong']:.1f}%", "", "top pair or better", ""))
        tv.insert("", "end", values=(
            "MEDIUM", f"{out['medium']:.1f}%", "", "middle pair", ""))
        tv.insert("", "end", tags=("neg",), values=(
            "WEAK", f"{out['weak']:.1f}%", "", "cannot call", ""))
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

    def _render_report(self, tv, out):
        cols = ["by"] + [BY_KEY[c].label for c in out["cols"]] + ["n"]
        self._cols(tv, tuple(cols), [130] + [95] * len(out["cols"]) + [80],
                   {"by": "w"})
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
            row.append(f"{out['counts'].get(k, (0, 0))[0]:,}")
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
        self._cols(tv, ("when", "site", "bb", "pos", "hand", "net bb", "board",
                        "tags"),
                   (140, 90, 60, 60, 70, 90, 170, 160),
                   {"when": "w", "site": "w", "pos": "w", "hand": "w",
                    "board": "w", "tags": "w"})
        self._hand_ids = {}
        for hid, seat, when, site, bb, pos, combo, board, net, marks in out["rows"]:
            iid = tv.insert("", "end", values=(
                (when or "")[:16], site, f"{bb:g}" if bb else "",
                pos or "", combo or "–",
                f"{net:+.1f}" if net is not None else "",
                board or "", ", ".join(marks)),
                tags=("pos",) if (net or 0) > 0 else
                     ("neg",) if (net or 0) < 0 else ())
            self._hand_ids[iid] = (hid, seat)
        if out["rows"]:
            tv.insert("", "end", values=("", "", "", "", "", "", "", ""))
            tv.insert("", "end", tags=("note",),
                      values=("double-click a hand to replay it, and tag it "
                              "there", "", "", "", "", "", "", ""))

    def _open_hand(self, _event):
        tv = self.tree["hands"]
        sel = tv.selection()
        if not sel or sel[0] not in getattr(self, "_hand_ids", {}):
            return
        hid, seat = self._hand_ids[sel[0]]
        HandWindow(self, self.con, hid, seat)

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

    # ---- the graph, drawn rather than served ---------------------------
    def _draw_graph(self, message=None):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s = self.series
        if not s:
            idle = "the graph is drawn once the filter has run"
            c.create_text(w / 2, h / 2, fill=DIM, font=(UI, 10),
                          text=message or (
                              "fewer than two of your hands match -- a line "
                              "needs two points" if self.graph_ran else idle))
            return
        L, R, T, B = 70, 210, 30, 40
        n = len(s["total"])
        lo = min(min(v) for k, v in s.items() if k in LINE)
        hi = max(max(v) for k, v in s.items() if k in LINE)
        lo, hi = min(lo, 0.0), max(hi, 0.0)
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
            pts = []
            for j, v in enumerate(s[key]):
                pts += [x(j), y(v)]
            if len(pts) >= 4:
                c.create_line(*pts, fill=LINE[key], width=2, smooth=False)

        # The legend keeps its own order, and says when a line cannot be
        # seen because another is sitting exactly on it. A line that is
        # missing and a line that is hidden look identical on the canvas and
        # are completely different facts.
        covered = _coincident(s)
        for i, (key, colour) in enumerate(LINE.items()):
            ly = T + 16 + i * 30
            c.create_line(w - R + 8, ly, w - R + 30, ly, fill=colour, width=3)
            note = covered.get(key)
            c.create_text(w - R + 38, ly, anchor="w", fill=INK,
                          font=(UI, 9),
                          text=f"{key.replace('_', ' ')}  {s[key][-1]:+,.0f}")
            if note:
                c.create_text(w - R + 38, ly + 12, anchor="w", fill=DIM,
                              font=(UI, 8),
                              text=f"under {note.replace('_', ' ')}")
        c.create_text((L + w - R) / 2, h - 14, fill=DIM,
                      font=(UI, 8), text=s.get("_note", ""))

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
        self.site_box.configure(values=["any site"] + self.options["sites"])
        if not self.vals["site"].get():
            self.vals["site"].set("any site")

    def describe_filter(self):
        """The active filter as a sentence, for the bar above the answer."""
        try:
            cohort_spec, query_argv = players.parse_cohort(self.argv())
            _w, label, _p = query.build(query_argv)
        except SystemExit:
            return "…"
        if cohort_spec is not None:
            label += ", player cohort"
        return "all hands" if label == "everything" else label


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
        self.geometry("520x430")
        self.transient(app.master)
        self.grab_set()

        current = {field: value for field, value in
                   (app.cohort_spec[0] if app.cohort_spec else [])}
        self.values = {field: tk.StringVar(value=current.get(field, default))
                       for field, _label, default in self.FIELDS}
        current_spec = app.cohort_spec or ([], None, None, None)
        _conditions, site, klass, durable = current_spec
        self.site = tk.StringVar(value=site or "")
        self.klass = tk.StringVar(value=klass or "")
        self.durable = tk.StringVar(
            value="" if durable is None else str(durable))

        ttk.Label(self, text="PLAYER COHORT", style="Title.TLabel").pack(
            anchor="w", padx=24, pady=(22, 4))
        ttk.Label(self, text="Filter players first; the selected cohort is "
                  "then used by every report tab.",
                  style="Dim.TLabel").pack(anchor="w", padx=24, pady=(0, 18))
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
        for value in self.values.values():
            value.set("")
        self.site.set("")
        self.klass.set("")
        self.durable.set("")

    def apply(self):
        argv = ["--cohort"]
        for field, _label, _default in self.FIELDS:
            value = self.values[field].get().strip()
            if value:
                argv += ["--" + field, value]
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

    def _flag_item(self, flag):
        v = self.app.flags[flag]

        def on():
            v.set(True)
            twin = OPPOSITES.get(flag)
            if twin:
                self.app.flags[twin].set(False)
        return on, (lambda: v.set(False)), (lambda: bool(v.get()))

    # ---- the tabs ------------------------------------------------------
    def _quick_tab(self, nb):
        page = self._page(nb, "Quick Filters")
        self._heading(page, "who is being measured")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f)))
            for f, t in (("--hero", "me"), ("--pool", "the pool"))])
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
        self._heading(page, "against  (they opened or answered, and nobody else stayed)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("vs", v))) for v in POSITIONS])
        self._heading(page, "and that opponent is")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in VS_SIDE])
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
        self._heading(page, "situation")
        self._grid(page, [(lambda parent, f=f, t=t: self._pick(
            parent, t, *self._flag_item(f))) for f, t in SITUATIONS])
        # What the player is looking at when they act. "Facing a bet" is
        # the shape of most river questions and the window could not ask
        # it: the flag existed on the command line and had no box here, so
        # the assistant's answers lost it on the way into the window.
        self._heading(page, "facing  (what is in front of the player when they act)")
        self._grid(page, [(lambda parent, v=v: self._pick(
            parent, v, *self._set_item("facing", v))) for v in query.FACINGS])
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

        # Built by clicking, the way a solver's line picker works: a row of
        # buttons per street, each press writing one action onto that
        # street's pattern. The letters were the only way in and were "hard
        # to learn", which is the user's phrase and a fair one; they are
        # still there, underneath, for anybody who wants to type them.
        self._heading(page, "my line, one street at a time -- click what you did")
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="Each press adds an action to that street of YOUR "
                       "line. A street left empty means anything; \"any\" "
                       "is a street you do not care about. Bet flop, bet "
                       "turn, bet river is a triple barrel; check then call "
                       "on the flop is a check-call."
                  ).pack(anchor="w", padx=18, pady=(0, 4))
        self._own_vars = {}
        for st, label in (("pre", "preflop"), ("flop", "flop"),
                          ("turn", "turn"), ("river", "river")):
            row = ttk.Frame(page)
            row.pack(fill="x", padx=18, pady=2)
            ttk.Label(row, text=label, style="Dim.TLabel", width=9).pack(side="left")
            var = tk.StringVar()
            self._own_vars[st] = var
            for text, letter in (("fold", "F"), ("check", "X"), ("call", "C"),
                                 ("bet", "B"), ("raise", "R"), ("any", "*")):
                ttk.Button(row, text=text, width=7,
                           command=lambda v=var, l=letter: self._own_press(v, l)
                           ).pack(side="left", padx=2)
            ttk.Button(row, text="⌫", width=3,
                       command=lambda v=var: self._own_press(v, None)
                       ).pack(side="left", padx=(8, 2))
            ttk.Label(row, textvariable=var, width=10).pack(side="left", padx=10)
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18, pady=(2, 8))
        ttk.Label(row, text="reads as", style="Dim.TLabel", width=9).pack(side="left")
        ttk.Label(row, textvariable=self.app.vals["my_line"]).pack(side="left")
        self._own_sync()

        self._heading(page, "how the betting went, everybody's actions in order")
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

        self._heading(page, "this player's own line, the way it is said")
        for name, label, example in (
                ("my_line", "my line", "*/XC/XC/XF   check-call, check-call, "
                                       "check-fold  (dashes work too)"),
                ("my_node", "my node", "*/B/B/   bet the flop and the turn, "
                                       "now on the river")):
            row = ttk.Frame(page)
            row.pack(fill="x", padx=18, pady=3)
            ttk.Label(row, text=label, style="Dim.TLabel", width=9).pack(
                side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=40).pack(
                side="left")
            ttk.Label(row, text=example, style="Dim.TLabel").pack(
                side="left", padx=14)
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="Streets from preflop, separated by / or -, and * "
                       "for anything: B/B/B after a * is a triple barrel, "
                       "XR on the flop is a check-raise. Only this "
                       "player's actions, so the other seats' bets and "
                       "calls are not in it."
                  ).pack(anchor="w", padx=18, pady=(8, 0))

    def _own_press(self, var, letter):
        """One press on the line builder: add an action, or take one off."""
        cur = var.get()
        var.set(cur[:-1] if letter is None else cur + letter)
        self._own_sync()

    def _own_sync(self):
        """
        The four street boxes, as the one pattern `--my-line` takes.

        Streets are joined with "/", an empty street is "*" (anything), and
        trailing empty streets are dropped so that "bet the flop" does not
        demand that a turn was dealt.
        """
        segs = [self._own_vars[s].get() or "*" for s in ("pre", "flop", "turn", "river")]
        while segs and segs[-1] == "*":
            segs.pop()
        # The trailing "/" is "and then whatever, or nothing": bet the flop
        # is "*/B/", which matches a flop bet that took the pot as well as
        # one that was called and played on.
        self.app.vals["my_line"].set("/".join(segs) + "/" if any(
            self._own_vars[s].get() for s in self._own_vars) else "")

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
        for name, text in (("since", "from"), ("until", "to")):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=14).pack(
                side="left", padx=(6, 18))

        self._heading(page, "when you were playing   (ranges are a-b)")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        for name, text, width in (("hour", "hour of day", 8),
                                  ("weekday", "weekday", 14),
                                  ("session_len", "session length, min", 9),
                                  ("session_min", "minutes into it", 9),
                                  ("tables", "tables open", 6)):
            ttk.Label(row, text=text, style="Dim.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=self.app.vals[name], width=width).pack(
                side="left", padx=(6, 16))
        ttk.Label(page, style="Dim.TLabel", wraplength=980, justify="left",
                  text="18-23 is the evening, mon,tue,wed the weekdays, "
                       "120-300 the sessions of two to five hours, 0-60 the "
                       "first hour of any session, 1-2 one or two tables. "
                       "The hour is the site's clock, not yours."
                  ).pack(anchor="w", padx=18, pady=(2, 0))

        # Hands you marked. A hand is tagged in its replayer window; here
        # the tag is a filter, so "the ones I marked review, on the button"
        # is two clicks and a word.
        self._heading(page, "hands you marked")
        row = ttk.Frame(page)
        row.pack(fill="x", padx=18)
        ttk.Label(row, text="tag", style="Dim.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.app.vals["tag"], width=18).pack(
            side="left", padx=(6, 16))
        ttk.Label(row, style="Dim.TLabel",
                  text="one or more, comma-separated; tag a hand from its "
                       "replayer window").pack(side="left")

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


class AskPanel(ttk.Frame):
    """
    A chat with the database, docked on the right.

    Every answer is the engine's: the assistant turns the sentence into
    `query.py` flags and runs them, and what comes back carries the n and
    the interval exactly as the stats tab would show it. The command it
    ran is printed under the answer, and RUN IT puts those flags into the
    window -- so the answer is not the end of the question, it is a filter
    you can keep working with.

    The call runs on a thread, since it takes a while and the window must
    not freeze; the reply is handed back through `after`, because Tk is
    single-threaded and a widget touched from another thread is a crash
    that looks like a click doing nothing.
    """

    def __init__(self, master, app):
        super().__init__(master, width=420)
        self.app = app
        self.history = []
        self.last_ran = []
        self.shown = False

        head = ttk.Frame(self)
        head.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(head, text="ask the database", style="Title.TLabel").pack(side="left")
        ttk.Button(head, text="×", width=3, command=self.toggle).pack(side="right")
        self.log = tk.Text(self, background=PANEL, foreground=INK, borderwidth=0,
                           font=(UI, 10), wrap="word", padx=10, pady=8,
                           insertbackground=INK, state="disabled")
        self.log.pack(fill="both", expand=True, padx=10)
        for name, colour in (("you", ACCENT), ("dim", DIM), ("bad", BAD)):
            self.log.tag_configure(name, foreground=colour)
        self.log.tag_configure("mono", font=(MONO, 9))
        self.run_btn = ttk.Button(self, text="run it in the window",
                                  command=self.run_last)
        self.entry = tk.Text(self, height=3, background=BG, foreground=INK,
                             insertbackground=INK, font=(UI, 10), wrap="word",
                             borderwidth=1)
        self.entry.pack(fill="x", padx=10, pady=(6, 4))
        self.entry.bind("<Return>", self._enter)
        self.entry.bind("<Shift-Return>", lambda e: None)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(row, text="ask", style="Accent.TButton",
                   command=self.send).pack(side="left")
        self.busy = ttk.Label(row, text="", style="Dim.TLabel")
        self.busy.pack(side="left", padx=10)
        self._say("Ask in English: \"how often does the pool fold to a river "
                  "bet in 3bet pots, SB vs BTN, B-B-B?\" -- every number in "
                  "the answer comes from a query the program ran, shown "
                  "underneath.", "dim")

    def toggle(self):
        if self.shown:
            self.pack_forget()
        else:
            self.pack(side="right", fill="y")
            self.pack_propagate(False)
            self.entry.focus_set()
        self.shown = not self.shown

    def _enter(self, _event):
        self.send()
        return "break"

    def _say(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n\n", tag or ())
        self.log.configure(state="disabled")
        self.log.see("end")

    def send(self):
        question = self.entry.get("1.0", "end").strip()
        if not question:
            return
        self.entry.delete("1.0", "end")
        self._say(question, "you")
        self.busy.configure(text="asking…")
        self.run_btn.pack_forget()
        history = list(self.history)

        def work():
            try:
                answer, ran, transcript, who = ask.ask(question, history=history)
                self.after(0, lambda: self._answered(answer, ran, transcript, who))
            except Exception as e:
                self.after(0, lambda: self._failed(str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _answered(self, answer, ran, transcript, who):
        # Who answered is shown because it is not always who was asked:
        # `ask.ask` falls through to the next provider with a key when the
        # chosen one is out of credit or overloaded, and an answer from
        # Claude Code labelled as Gemini's would be a mystery the next time
        # Gemini was asked directly.
        self.busy.configure(text="")
        self._say(answer)
        self._say(f"(answered by {ask.who_label(who)})", "mono")
        if ran:
            self._say("\n".join("ran: python query.py " + " ".join(a) for a in ran),
                      "mono")
            self.last_ran = ran[-1]
            self.run_btn.pack(fill="x", padx=10, pady=(0, 4))
        self.history = transcript

    def _failed(self, message):
        self.busy.configure(text="")
        self._say(message, "bad")

    def run_last(self):
        """The last query's flags, into the window's own filter."""
        if not self.last_ran:
            return
        self.app.apply_argv(self.last_ran)


class HandWindow(tk.Toplevel):
    """
    One hand, replayed in a window of its own -- and where it gets marked.

    The tag bar and the note box are here rather than on the hands tab
    because this is where a hand is being LOOKED at: "review this" is a
    thought you have with the action in front of you, and the note about
    the player is about what they just did. Both write straight to the
    database; nothing has to be saved.
    """

    def __init__(self, master, con, hand_id, seat):
        super().__init__(master)
        self.configure(background=BG)
        self.title(f"hand {hand_id}")
        self.geometry("760x680")
        self.con, self.hand_id = con, hand_id
        d = query.hand_detail(con, hand_id, seat)
        mono = tkfont.Font(family=MONO, size=10)

        # Tags along the top: the ones it has, clickable to remove, and a
        # box to add one. Enter adds; the list redraws from the database
        # so what is shown is always what is stored.
        import notes
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=12, pady=(10, 4))
        ttk.Label(top, text="tags", style="Dim.TLabel").pack(side="left")
        self.tag_row = ttk.Frame(top)
        self.tag_row.pack(side="left", padx=8)
        self.tag_entry = ttk.Entry(top, width=16)
        self.tag_entry.pack(side="left")
        self.tag_entry.bind("<Return>", lambda e: self._add_tag())
        ttk.Button(top, text="tag", command=self._add_tag).pack(side="left", padx=4)
        self._draw_tags()

        # The note on the seat this hand was opened for, when that seat is
        # a person -- `sites.named()` decides, and an Ignition seat, which
        # is nobody after the session, gets no box rather than a note that
        # would silently attach to the next stranger in the chair.
        focus = next((s for s in (d["seats"] if d else [])
                      if s["seat"] == seat), None)
        self.note_for = None
        if d and focus and focus.get("player") and d["site"] in sites.named() \
                and not focus.get("is_hero"):
            self.note_for = (d["site"], focus["player"])
            row = ttk.Frame(self)
            row.pack(side="top", fill="x", padx=12, pady=(0, 6))
            ttk.Label(row, text=f"note on {focus['player'][:24]}",
                      style="Dim.TLabel").pack(side="left")
            self.note_box = tk.Text(row, height=2, width=60, background=BG,
                                    foreground=INK, insertbackground=INK,
                                    font=(UI, 10), borderwidth=1, wrap="word")
            self.note_box.pack(side="left", padx=8, fill="x", expand=True)
            self.note_box.insert("1.0", focus.get("note") or "")
            ttk.Button(row, text="save", command=self._save_note).pack(side="left")

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
                           f"{d['played_at']}  {d['table']}\n\n", "dim")
        for s in d["seats"]:
            net = (s["won"] or 0) - (s["put_in"] or 0)
            mark = "*" if s["seat"] == seat else (">" if s["is_hero"] else " ")
            text.insert("end", f" {mark} {s['position'] or '?':4} "
                               f"{(s['name'] or '')[:18]:18} "
                               f"{s['stack'] or 0:9.2f}  "
                               f"{s['cards'] or 'not shown':>10}  ")
            text.insert("end", f"{net:+9.2f}", "good" if net > 0 else "bad")
            # Every seat's note beside it, so the table reads the way a
            # HUD popup would: who is who, at a glance.
            if s.get("note"):
                text.insert("end", f"   {s['note'][:50]}", "hi")
            text.insert("end", "\n")
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

    def _draw_tags(self):
        import notes
        for w in self.tag_row.winfo_children():
            w.destroy()
        for t in notes.tags_of(self.con, self.hand_id):
            b = ttk.Button(self.tag_row, text=f"{t} ×", style="Tag.TButton",
                           command=lambda t=t: self._drop_tag(t))
            b.pack(side="left", padx=2)

    def _add_tag(self):
        import notes
        notes.tag(self.con, self.hand_id, self.tag_entry.get())
        self.tag_entry.delete(0, "end")
        self._draw_tags()

    def _drop_tag(self, t):
        import notes
        notes.untag(self.con, self.hand_id, t)
        self._draw_tags()

    def _save_note(self):
        import notes
        if self.note_for:
            notes.note(self.con, *self.note_for,
                       self.note_box.get("1.0", "end"))


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
    app.load_options()

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
         ["--pool", "--vs-pool", "--matchup", "BTN,BB", "--pos", "BTN",
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
        # When. A range typed into a box has to reach the same predicate
        # the command line builds from it, or "evening" means two things.
        ({"flags": ["--hero"], "vals": {"hour": "18-23", "session_len": "120-300"}},
         ["--hero", "--hour", "18-23", "--session-len", "120-300"]),
        # A marked hand, by the word it was marked with.
        ({"flags": ["--hero"], "vals": {"tag": "review,cooler"}},
         ["--hero", "--tag", "review,cooler"]),
        # What the player is facing, which the window could not ask until
        # the assistant's answers lost it on the way in.
        ({"flags": ["--pool"], "street": ["river"], "facing": ["bet"]},
         ["--pool", "--street", "river", "--facing", "bet"]),
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

    # And back the other way: a command line the assistant ran, put into
    # the window, must come out as the same filter -- that is what "run it
    # in the window" promises. Reporting options have no box and are
    # dropped; everything that selects hands must survive.
    round_trips = [
        ["--pool", "--site", "pokerstars", "--pot", "3bet",
         "--matchup", "BTN,SB", "--pos", "BTN", "--flop", "BC", "--turn", "BC",
         "--street", "river", "--facing", "bet", "--show", "fold_to_river_bet"],
        ["--hero", "--fish-right", "--results"],
        ["--hero", "--pot", "raised", "--street", "flop", "--board", "mono",
         "--by", "position"],
    ]
    lost = []
    for argv in round_trips:
        app.apply_argv(argv)
        a, _la, _pa = query.build(app.argv())
        keep = [x for x in argv if x not in ("--results", "--stats", "--hands")]
        b, _lb, _pb = query.build(keep)
        if sorted(a.split(" AND ")) != sorted(b.split(" AND ")):
            lost.append(f"{' '.join(argv)} -> {a!r}, wanted {b!r}")
    print(f"a command line survives the window  {len(round_trips) - len(lost)}/{len(round_trips)}")
    for l in lost:
        fails.append(l)
        print(f"    {l}")

    # Every view must build its rows without raising, including on a filter
    # that matches nothing -- which is one click away at all times.
    con = sqlite3.connect(db_path)
    broke = []
    views = ("stats", "actions", "range", "chart", "report", "results",
             "hands", "graph", "sessions")
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
    root.title("TraceEV")
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
