"""
The numbers at the table: a box beside each opponent while you play.

Everything here is a view over what the rest of the program already knows.
The stats are `stats.py`'s, the identities are `spots.identify`'s, the
classes are `players.classify`'s, and a hand reaches the database through
`importer.update`, the same road an import takes. A HUD that computed its
own VPIP would be a second answer to a question this project answers once,
and the two would drift apart the first time either changed.

What is new is only the part that has to be: finding the poker client's
windows, deciding which seat of a table is where on the screen, and
drawing a small window over each one that moves when the table does.

**Only on sites where a HUD is allowed and a label is a person** --
`sites.with_hud()`, which is ACR and PokerStars. PartyPoker has banned
HUDs since 2019, Ignition's seats change hands without the name changing,
and the app rooms arrive after the session.

**The numbers are shrunk towards the pool by default**, the hand count
beside them, because a rate on three chances printed bare is believed: the
profile view printed "fold to 3-bet 0.0%" off one observation for months.
The pool is the table's own game -- its site and stake -- with the player
taken out. Hand2Note prints the raw rate; `hud.json` with
`{"rates": "raw"}` does the same here. That is one of the five choices in
the HUD design left open for the user, and each of them is a setting in
`DEFAULTS` rather than a decision buried in the drawing code.

    python hud.py              run it: watch the hand histories, draw the boxes
    python hud.py --demo       a pretend table from your database, with the HUD on it
    python hud.py --print      the numbers for the tables you played last, as text
    python hud.py --windows    every window title, and which table each one matched
    python hud.py --check      the numbers against the stat engine, the layout,
                               the title matching and the watcher
"""

import json
import math
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

import importer
import sites
import stats

DB = Path(__file__).parent / "hands.db"
SETTINGS = Path(__file__).parent / "hud.json"

# The five choices the design left open, as settings. `hud.json` beside the
# program overrides any of them; it is the user's and is not in the
# repository, for the same reason `stats.json` is not.
DEFAULTS = {
    # What a box shows, line by line: registry keys, so a stat saved in
    # `stats.json` can be put on the HUD by naming it.
    "lines": [["vpip", "pfr", "threebet"],
              ["fold_to_3bet", "cbet_flop", "fold_to_cbet"]],
    # "pooled": shrunk towards the table's game, the player taken out.
    # "raw": the player's own rate, as Hand2Note prints it.
    "rates": "pooled",
    # Below this many chances a number is drawn faint. Ten is where
    # `stats.SHRINK` says a player's own figure starts to outweigh the
    # pool's, so it is the point at which the number becomes theirs.
    "faint_below": stats.SHRINK,
    # How often the hand history folders are looked at, in seconds.
    "poll": 1.0,
    # A table counts as one being played if its file changed this recently,
    # in minutes. The file's clock is this machine's, which is the point:
    # `played_at` is the site's clock and nothing says it agrees.
    "active": 30,
    # Where the seats lie on a table window, as an ellipse in fractions of
    # its width and height: centre x, centre y, half-width, half-height.
    # A first guess that has not been measured against either client; the
    # right numbers are whatever puts the boxes beside the names.
    "ellipse": [0.5, 0.47, 0.40, 0.36],
}


def settings():
    """`DEFAULTS` with whatever `hud.json` overrides."""
    got = dict(DEFAULTS)
    try:
        got.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return got


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

def table_now(con, site, table_id):
    """
    Who was in the last hand dealt at this table, and the game it was.

    The last hand and not the last few: a seat that stood up two hands ago
    is somebody else's now, and the HUD would otherwise draw the departed.
    Seats come from `spots`, which already leaves out a player who was
    listed at the table and sitting out.
    """
    h = con.execute(
        "SELECT hand_id, fmt, bb, max_seats, n_players FROM hands "
        "WHERE site=? AND table_id=? ORDER BY played_at DESC, hand_id DESC "
        "LIMIT 1", (site, table_id)).fetchone()
    if h is None:
        return None
    seats = [{"seat": r[0], "player": r[1], "is_hero": bool(r[2])}
             for r in con.execute(
                 "SELECT seat, player, is_hero FROM spots WHERE hand_id=? "
                 "ORDER BY seat", (h[0],))]
    return {"site": site, "table_id": table_id, "hand_id": h[0],
            "fmt": h[1], "bb": h[2],
            # A history that does not say how many seats the table has is
            # sized by the highest seat number anybody sat in.
            "max_seats": h[3] or max([s["seat"] for s in seats] + [h[4] or 6]),
            "seats": seats}


def game_where(site, fmt, bb):
    """The pool a player at this table is read against, with them still in."""
    return "site=? AND fmt=? AND bb=? AND is_hero=0", (site, fmt, bb)


def numbers(con, site, fmt, bb, player, keys, mode="pooled", pool=None):
    """
    One player's figures for these stats: {key: (n, k, raw, prior, shown)}.

    `n` and `k` are over every hand of theirs on the site, which is what the
    profile view counts. `prior` is the table's game with this player taken
    out -- `pool`, the game's totals, is passed in so that a table of six
    players costs one pass over the game and not six. `shown` is what goes
    in the box: the shrunk rate, or the raw one if `mode` is "raw".
    """
    want = [stats.BY_KEY[k] for k in keys if k in stats.BY_KEY]
    mine = stats.rates(con, "player=? AND site=?", (player, site), want)
    gw, gp = game_where(site, fmt, bb)
    if pool is None:
        pool = stats.rates(con, gw, gp, want)
    at_game = stats.rates(con, gw + " AND player=?", gp + (player,), want)

    out = {}
    for s in want:
        if s.key in mine:
            n, k = mine[s.key]
            pn = pool.get(s.key, (0, 0))[0] - at_game.get(s.key, (0, 0))[0]
            pk = pool.get(s.key, (0, 0))[1] - at_game.get(s.key, (0, 0))[1]
        else:
            # Spots-sourced stats cannot join the one-pass count; asked
            # singly, exactly as the profile view asks them.
            n, k = stats.rate(con, s, "player=? AND site=?", (player, site))[:2]
            pn, pk = stats.rate(con, s, gw + " AND player<>?", gp + (player,))[:2]
        raw = k / n if n else None
        prior = pk / pn if pn else None
        shown = raw if mode == "raw" else stats.shrunk(k, n, prior)
        out[s.key] = (n, k, raw, prior, shown if shown is not None else raw)
    return out


def view(con, site, table_id, conf=None):
    """
    Everything one table's boxes need, worked out away from the window.

    Returns the table and, per opponent seat, the lines of the box: each a
    list of (text, faint). Hero gets no box.
    """
    conf = conf or settings()
    t = table_now(con, site, table_id)
    if t is None:
        return None
    keys = [k for line in conf["lines"] for k in line]
    want = [stats.BY_KEY[k] for k in keys if k in stats.BY_KEY]
    gw, gp = game_where(site, t["fmt"], t["bb"])
    pool = stats.rates(con, gw, gp, want)
    klass = {r[0]: r[1] for r in con.execute(
        "SELECT player, class FROM players WHERE site=? AND player IN "
        f"({','.join('?' * len(t['seats']))})",
        (site,) + tuple(s["player"] for s in t["seats"]))}
    hands = {r[0]: r[1] for r in con.execute(
        "SELECT player, COUNT(*) FROM spots WHERE site=? AND player IN "
        f"({','.join('?' * len(t['seats']))}) GROUP BY player",
        (site,) + tuple(s["player"] for s in t["seats"]))}

    boxes = {}
    for s in t["seats"]:
        if s["is_hero"] or not s["player"]:
            continue
        got = numbers(con, site, t["fmt"], t["bb"], s["player"], keys,
                      conf["rates"], pool)
        head = f"{s['player'][:12]}  {hands.get(s['player'], 0)}h"
        # The class only when there is one. "unknown" is the usual answer
        # and printing it on every box would teach the eye to skip the
        # place where a real one appears.
        if klass.get(s["player"]) in ("reg", "fish"):
            head += "  " + klass[s["player"]].upper()
        lines = [[(head, False)]]
        for line in conf["lines"]:
            cells = []
            for key in line:
                n, _k, _raw, _prior, shown = got.get(key, (0, 0, None, None, None))
                # A dash, never a zero, when there has been no chance: "0"
                # says they never do it, and they have never been asked.
                text = "-" if not n or shown is None else f"{round(100 * shown)}"
                cells.append((text, n < conf["faint_below"]))
            lines.append(cells)
        boxes[s["seat"]] = {"player": s["player"], "lines": lines,
                            "numbers": got}
    return {"table": t, "boxes": boxes}


def as_text(v):
    """A table's boxes as text, for `--print` and for reading a check."""
    t = v["table"]
    out = [f"{t['site']}  {t['table_id']}  {t['fmt']} bb={t['bb']}  "
           f"{t['max_seats']}-max  after hand {t['hand_id']}"]
    for seat, box in sorted(v["boxes"].items()):
        rows = []
        for line in box["lines"]:
            rows.append(" / ".join(f"({text})" if faint else text
                                   for text, faint in line))
        out.append(f"  seat {seat:<2} " + "\n           ".join(rows))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Where each seat is on the screen
# ---------------------------------------------------------------------------

def slot(seat, hero_seat, max_seats):
    """
    Where a seat sits counting clockwise from the bottom of the table, 0 up.

    Both clients put hero at the bottom when seat preference is on, which
    is how almost everybody plays, so the table is rotated to match: hero
    is slot 0 and the others follow clockwise in seat order. Without a
    hero in the hand, seat 1 takes the bottom.
    """
    anchor = hero_seat if hero_seat else 1
    return (seat - anchor) % max_seats


def place(index, max_seats, ellipse=None):
    """
    The point on the table window, as fractions of its width and height,
    where the box for this slot goes.

    Seats lie on an ellipse inside the felt, clockwise from the bottom
    centre, on the ellipse the settings give.
    """
    cx, cy, rx, ry = ellipse or DEFAULTS["ellipse"]
    angle = math.pi / 2 + 2 * math.pi * index / max_seats
    return cx + rx * math.cos(angle), cy + ry * math.sin(angle)


# ---------------------------------------------------------------------------
# Finding the tables
# ---------------------------------------------------------------------------

def windows():
    """
    Every visible top-level window on this machine: (title, x, y, w, h).

    Windows only, through the window list Windows keeps itself. Minimised
    windows are left out, and so the boxes over a table go when it does.
    Elsewhere there are no poker clients to find, and the demo supplies
    its own windows instead.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    def one(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((buf.value, r.left, r.top, r.right - r.left,
                      r.bottom - r.top))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proc(one), 0)
    return found


def match(title, tables):
    """
    The table a window title belongs to, or None.

    Both clients put the table's name in the title, and both write the
    same name into the history -- "Halley II", "Miramar (Cap)" -- so the
    title is matched against the tables being played rather than parsed
    by a pattern per client that the next client update would break. The
    name has to start the title, or follow "Table '", and be followed by
    something that is not more name: "Halley" must not claim the window
    of "Halley II". The longest name that fits wins.
    """
    best = None
    for site, table_id in tables:
        for start in (title, title[len("Table '"):] if title.startswith("Table '") else None):
            if start is None or not start.startswith(table_id):
                continue
            rest = start[len(table_id):]
            if rest and rest[0] not in " -',(|":
                continue
            if best is None or len(table_id) > len(best[1]):
                best = (site, table_id)
    return best


# ---------------------------------------------------------------------------
# Watching the hand history folders
# ---------------------------------------------------------------------------

class Watcher:
    """
    New hands in, tables' boxes worked out again, away from the window.

    Each step looks at every history file in the HUD sites' folders and
    reads the ones that changed size or time since the last look -- the
    whole file, because `importer.load` skips the hands it already has and
    a history file is a few hundred hands at most. The hands that were new
    go through `importer.update`, and every table whose file moved is
    worked out again and handed to the window through `out`.
    """

    def __init__(self, db_path=DB, folders=None, out=None, conf=None):
        self.db_path = db_path
        self.conf = conf or settings()
        self.folders = folders
        self.out = out if out is not None else queue.Queue()
        self.seen = {}
        self.active = {}            # (site, table_id) -> when its file last moved
        self.first = True

    def files(self):
        if self.folders is not None:
            roots = [Path(f) for f in self.folders]
        else:
            import os
            roots = [Path(os.path.expandvars(p)) for key in sites.with_hud()
                     for p in sites.of(key).places]
        for root in roots:
            if root.is_dir():
                yield from root.rglob("*.txt")
            elif root.is_file():
                yield root

    def step(self):
        """One look at the folders. Returns the tables whose boxes changed."""
        now = time.time()
        changed = []
        for f in self.files():
            try:
                st = f.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            if self.seen.get(f) == sig:
                continue
            self.seen[f] = sig
            # On the first look only the files of the last half hour count:
            # a folder holds years of history and none of it is a table
            # being played now.
            if self.first and now - st.st_mtime > 60 * self.conf["active"]:
                continue
            changed.append(f)
        self.first = False
        if not changed:
            return []

        got = importer.load(changed, self.db_path)
        if got["ids"]:
            importer.update(got["ids"], self.db_path)
        con = sqlite3.connect(self.db_path)
        hud_sites = sites.with_hud()
        for f in changed:
            for site, table_id in con.execute(
                    "SELECT DISTINCT site, table_id FROM hands WHERE source=?",
                    (f.name,)):
                if site in hud_sites:
                    self.active[(site, table_id)] = now
        stale = [t for t, when in self.active.items()
                 if now - when > 60 * self.conf["active"]]
        for t in stale:
            del self.active[t]
        fresh = {}
        for site, table_id in self.active:
            v = view(con, site, table_id, self.conf)
            if v is not None:
                fresh[(site, table_id)] = v
        con.close()
        self.out.put(fresh)
        return list(fresh)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.step()
            except Exception as e:          # the window must outlive a bad file
                import diag
                diag.event("hud watcher failed", error=repr(e))
            stop.wait(self.conf["poll"])


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

BG, INK, FAINT = "#14161a", "#e8e8e8", "#7b8088"


class Overlay:
    """
    A small always-on-top window per opponent, kept beside their seat.

    Ten times a second it reads where every table window is and moves the
    boxes there, so a table dragged across the screen takes its numbers
    with it. The numbers themselves arrive from the watcher's thread
    through a queue; Tk is only ever touched from its own thread.
    """

    def __init__(self, root, source, conf=None):
        self.root = root
        self.source = source            # () -> [(title, x, y, w, h)]
        self.conf = conf or settings()
        self.views = {}
        self.boxes = {}                 # (site, table_id, seat) -> (Toplevel, Label)
        self.inbox = queue.Queue()

    def tick(self):
        try:
            while True:
                self.views = self.inbox.get_nowait()
        except queue.Empty:
            pass
        placed = set()
        for title, x, y, w, h in self.source():
            hit = match(title, self.views)
            if not hit:
                continue
            v = self.views[hit]
            t = v["table"]
            hero = next((s["seat"] for s in t["seats"] if s["is_hero"]), None)
            for seat, box in v["boxes"].items():
                fx, fy = place(slot(seat, hero, t["max_seats"]), t["max_seats"],
                               self.conf["ellipse"])
                key = hit + (seat,)
                self.draw(key, box, int(x + fx * w), int(y + fy * h))
                placed.add(key)
        for key in [k for k in self.boxes if k not in placed]:
            self.boxes.pop(key)[0].destroy()
        self.root.after(100, self.tick)

    def draw(self, key, box, x, y):
        import tkinter as tk
        if key not in self.boxes:
            top = tk.Toplevel(self.root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            try:
                top.attributes("-alpha", 0.88)
            except tk.TclError:
                pass
            frame = tk.Frame(top, bg=BG, padx=4, pady=2)
            frame.pack()
            self.boxes[key] = (top, frame)
        top, frame = self.boxes[key]
        shape = [[(text, faint) for text, faint in line] for line in box["lines"]]
        if getattr(frame, "shape", None) != shape:
            for child in frame.winfo_children():
                child.destroy()
            for line in shape:
                row = tk.Frame(frame, bg=BG)
                row.pack(anchor="w")
                for i, (text, faint) in enumerate(line):
                    if i:
                        tk.Label(row, text="/", bg=BG, fg=FAINT,
                                 font=("Consolas", 9)).pack(side="left")
                    tk.Label(row, text=text, bg=BG, fg=FAINT if faint else INK,
                             font=("Consolas", 9)).pack(side="left")
            frame.shape = shape
        top.update_idletasks()
        top.geometry(f"+{x - top.winfo_width() // 2}+{y - top.winfo_height() // 2}")


def dpi_aware():
    """
    Make Windows report real pixels to this process.

    At 125% or 150% scaling Windows tells a program that has not declared
    itself aware a scaled-down picture of the screen, and Tk and the window
    list then disagree about where everything is: the boxes land a fifth of
    the way off. Declared before Tk starts, both see the same pixels.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Clients that look for HUDs on the machine and restrict the account that
# runs one, matched in lower case against window titles and process names.
# ClubWPT Gold says so in its own help pages, and restricts coins and
# redemptions with the account, so the HUD will not start beside it.
HOSTILE = ("clubwpt",)


def processes():
    """Names of the running programs, on Windows; nothing elsewhere."""
    if sys.platform != "win32":
        return []
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split('","')[0].strip('"') for line in out.splitlines() if line]


def hostile(titles, names):
    """The first window title or process name of a client that bans HUDs."""
    for text in list(titles) + list(names):
        if any(h in text.lower().replace(" ", "") for h in HOSTILE):
            return text
    return None


def run(source=None, db_path=DB, folders=None, extra=None):
    """The HUD: the watcher on its thread, the boxes on Tk's."""
    found = hostile([w[0] for w in windows()], processes())
    if found:
        print(f"not starting: {found!r} is open, and that client restricts "
              "accounts that run a HUD. Close it completely, then start the "
              "HUD again.")
        return 1
    dpi_aware()
    import tkinter as tk
    root = tk.Tk()
    root.title("TraceEV HUD")
    root.geometry("260x60")
    tk.Label(root, text="TraceEV HUD is running.\nClose this window to stop it.",
             justify="left").pack(padx=10, pady=8)
    conf = settings()
    overlay = Overlay(root, source or windows, conf)
    watcher = Watcher(db_path, folders, overlay.inbox, conf)
    stop = threading.Event()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    if extra:
        extra(root, watcher, overlay)
    root.after(100, overlay.tick)
    root.protocol("WM_DELETE_WINDOW", lambda: (stop.set(), root.destroy()))
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# The demo, for seeing it without a poker client open
# ---------------------------------------------------------------------------

def last_tables(con, n=1):
    """The HUD-site tables whose latest hands are newest, with a hero seated."""
    hud_sites = sites.with_hud()
    return [tuple(r) for r in con.execute(
        "SELECT site, table_id FROM hands WHERE hero_seat IS NOT NULL AND "
        f"game='HOLDEM' AND site IN ({','.join('?' * len(hud_sites))}) "
        "GROUP BY site, table_id ORDER BY MAX(played_at) DESC LIMIT ?",
        hud_sites + (n,))]


def demo(db_path=DB):
    """
    A pretend table window named after the last table you played, with the
    HUD drawn over it from your own database.

    Everything but the poker client is real: the boxes come from the same
    numbers, the same title matching and the same window code. Drag the
    green window around and the boxes follow; resize it and they spread.
    """
    con = sqlite3.connect(db_path)
    tables = last_tables(con, 2)
    con.close()
    if not tables:
        print("no ACR or PokerStars hand with you seated in the database")
        return 1
    tables_open = []

    def open_tables(root, watcher, overlay):
        import tkinter as tk
        con = sqlite3.connect(db_path)
        views = {}
        for i, (site, table_id) in enumerate(tables):
            v = view(con, site, table_id, overlay.conf)
            if v is None:
                continue
            views[(site, table_id)] = v
            win = tk.Toplevel(root)
            win.title(f"{table_id} - demo of the TraceEV HUD ({site})")
            win.geometry(f"800x560+{80 + 60 * i}+{80 + 60 * i}")
            canvas = tk.Canvas(win, bg="#0b3d1f", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda e, c=canvas: (
                c.delete("felt"),
                c.create_oval(e.width * 0.12, e.height * 0.16, e.width * 0.88,
                              e.height * 0.80, fill="#14703a", outline="#2b2b2b",
                              width=8, tags="felt")))
            tables_open.append(win)
            print(as_text(v))
        con.close()
        overlay.inbox.put(views)
        # The watcher would replace these with the tables it saw move, and
        # in a demo none move, so it is stopped from sending.
        watcher.out = queue.Queue()

    def source():
        out = []
        for win in tables_open:
            try:
                out.append((win.title(), win.winfo_rootx(), win.winfo_rooty(),
                            win.winfo_width(), win.winfo_height()))
            except Exception:
                pass
        return out

    return run(source=source, db_path=db_path, folders=[],
               extra=open_tables) or 0


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check(db_path=DB):
    """
    The HUD says what the engine says, and puts it in the right place.

    Its numbers are held to `stats.rate`, `stats.shrunk` and a pool
    written out longhand, for real players; the layout is held to having
    hero at the bottom and nobody sharing a place; the title matching to
    every table name in the database, including the pairs where one name
    begins another; and the watcher to a history file that grows.
    """
    fails = []
    con = sqlite3.connect(db_path)
    conf = dict(DEFAULTS)
    keys = [k for line in conf["lines"] for k in line]
    missing = [k for k in keys if k not in stats.BY_KEY]
    if missing:
        fails.append(f"the HUD names stats the registry does not have: {missing}")

    # The numbers, for the busiest few opponents at each HUD site's
    # busiest game.
    tested = 0
    for site in sites.with_hud():
        for player, fmt, bb in con.execute(
                "SELECT player, fmt, bb FROM spots WHERE site=? AND is_hero=0 "
                "AND player IS NOT NULL GROUP BY player ORDER BY COUNT(*) DESC "
                "LIMIT 4", (site,)).fetchall():
            got = numbers(con, site, fmt, bb, player, keys)
            raw = numbers(con, site, fmt, bb, player, keys, mode="raw")
            for key in keys:
                n, k, _p, _l, _h = stats.rate(con, key, "player=? AND site=?",
                                              (player, site))
                pn, pk, _pp, _pl, _ph = stats.rate(
                    con, key, "site=? AND fmt=? AND bb=? AND is_hero=0 "
                    "AND player<>?", (site, fmt, bb, player))
                prior = pk / pn if pn else None
                want = stats.shrunk(k, n, prior)
                want = want if want is not None else (k / n if n else None)
                gn, gk, _raw, gprior, shown = got[key]
                close = lambda a, b: (a is None and b is None) or (
                    a is not None and b is not None and abs(a - b) < 1e-12)
                if (gn, gk) != (n, k) or not close(gprior, prior) \
                        or not close(shown, want) \
                        or not close(raw[key][4], k / n if n else None):
                    fails.append(f"{site} {player} {key}: HUD ({gn}, {gk}, "
                                 f"{shown}) against the engine ({n}, {k}, {want})")
                tested += 1
    print(f"numbers agree with the engine  {'yes' if not fails else 'NO'}   "
          f"{tested} player-stats, pooled and raw")

    # A table's boxes: hero has none, a stat never asked is a dash, and a
    # number on few chances is faint.
    tables = last_tables(con, 5)
    boxes = 0
    for site, table_id in tables:
        v = view(con, site, table_id, conf)
        hero = {s["seat"] for s in v["table"]["seats"] if s["is_hero"]}
        if hero & set(v["boxes"]):
            fails.append(f"{table_id}: hero has a box")
        for seat, box in v["boxes"].items():
            boxes += 1
            for line_keys, cells in zip(conf["lines"], box["lines"][1:]):
                for key, (text, faint) in zip(line_keys, cells):
                    n = box["numbers"][key][0]
                    if (n == 0) != (text == "-"):
                        fails.append(f"{table_id} seat {seat} {key}: n={n} "
                                     f"drawn as {text!r}")
                    if faint != (n < conf["faint_below"]):
                        fails.append(f"{table_id} seat {seat} {key}: n={n} "
                                     f"faint={faint}")
    print(f"boxes drawn as specified       {'yes' if not fails else 'NO'}   "
          f"{boxes} boxes over {len(tables)} tables")
    if tables:
        print(as_text(view(con, *tables[0], conf)))

    # The layout.
    bad = 0
    for max_seats in (2, 3, 4, 5, 6, 8, 9, 10):
        for hero in range(1, max_seats + 1):
            spots_ = [place(slot(s, hero, max_seats), max_seats)
                      for s in range(1, max_seats + 1)]
            bottom = max(spots_, key=lambda p: p[1])
            if bottom != spots_[hero - 1]:
                bad += 1
            if len({(round(x, 3), round(y, 3)) for x, y in spots_}) != max_seats:
                bad += 1
            if not all(0 <= x <= 1 and 0 <= y <= 1 for x, y in spots_):
                bad += 1
    print(f"hero at the bottom, no overlap {'yes' if not bad else 'NO'}")
    if bad:
        fails.append(f"{bad} table layouts put hero elsewhere or two seats together")

    # Title matching, against every HUD-site table name there is.
    names = [tuple(r) for r in con.execute(
        "SELECT DISTINCT site, table_id FROM hands WHERE site IN "
        f"({','.join('?' * len(sites.with_hud()))})", sites.with_hud())]
    wrong = []
    for site, table_id in names:
        for title in (f"{table_id} - $0.01/$0.02 USD - No Limit Hold'em",
                      f"Table '{table_id}' 6-max - Logged In as hero",
                      f"{table_id} (#123456) No Limit Hold'em"):
            if match(title, names) != (site, table_id):
                wrong.append((title, match(title, names)))
    for title in ("Inbox - Outlook", "PokerStars Lobby", ""):
        if match(title, names) is not None:
            wrong.append((title, match(title, names)))
    # The case that has to be right: one name the start of another.
    pair = [("pokerstars", "Halley"), ("pokerstars", "Halley II")]
    if match("Halley II - $0.01/$0.02", pair) != pair[1] or \
            match("Halley - $0.01/$0.02", pair) != pair[0]:
        wrong.append(("Halley / Halley II", None))
    print(f"window titles find their table {'yes' if not wrong else 'NO'}   "
          f"{len(names)} table names")
    for title, got in wrong[:3]:
        print(f"    {title!r} -> {got}")
    if wrong:
        fails.append(f"{len(wrong)} window titles matched the wrong table")
    con.close()

    # The refusal to start beside a client that bans HUDs.
    guard = [hostile(["ClubWPT Gold"], []), hostile([], ["ClubWPTGold.exe"]),
             hostile(["Halley - $0.01/$0.02", "Inbox"], ["python.exe"])]
    ok = guard[0] and guard[1] and guard[2] is None
    print(f"refuses beside ClubWPT Gold   {'yes' if ok else 'NO'}")
    if not ok:
        fails.append("the HUD would start beside a client that bans it")

    fails += watcher_check()
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def watcher_check():
    """
    A history file that grows while the HUD watches reaches its boxes.

    Played out on a PokerStars file from FPDB's corpus: the first hands
    written, a look, the rest appended, another look. Without the corpus
    on this machine it says so and passes, as `fixtures.py` does.
    """
    import tempfile

    import fixtures
    import pokerstars
    sample = None
    for folder, f in fixtures.fixture_files():
        if importer.sniff(f) != "pokerstars":
            continue
        blocks = list(pokerstars.split_hands(importer.read_text(f)))
        parsed = [pokerstars.parse_hand(b, source=f.name) for b in blocks]
        if len(blocks) >= 4 and all(parsed) and any(
                p["hand"].get("hero_seat") for p in parsed):
            sample = blocks
            break
    if sample is None:
        print("the watcher sees a file grow   no PokerStars fixture on this machine")
        return []

    fails = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "histories"
        folder.mkdir()
        db = Path(tmp) / "hud.db"
        f = folder / "table.txt"
        f.write_text("\n\n".join(sample[:2]) + "\n\n", encoding="utf-8")
        w = Watcher(db, [folder], conf=dict(DEFAULTS))
        first = w.step()
        con = sqlite3.connect(db)
        before = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        con.close()
        with f.open("a", encoding="utf-8") as out:
            out.write("\n\n".join(sample[2:]) + "\n\n")
        # The same second, the same size twice, must not hide a change:
        # the signature is size and time together, and appending changes
        # the size.
        second = w.step()
        con = sqlite3.connect(db)
        after = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        hands = con.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
        con.close()
        quiet = w.step()
        views = []
        while not w.out.empty():
            views.append(w.out.get())
    ok = (first and second and not quiet and after > before
          and hands == len(sample) and views and views[-1])
    print(f"the watcher sees a file grow   {'yes' if ok else 'NO'}   "
          f"{hands} hands, decisions {before} then {after}")
    if not ok:
        fails.append("a history file that grew did not reach the HUD")
    return fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--demo" in argv:
        return demo()
    if "--windows" in argv:
        con = sqlite3.connect(DB)
        tables = [tuple(r) for r in con.execute(
            "SELECT DISTINCT site, table_id FROM hands WHERE site IN "
            f"({','.join('?' * len(sites.with_hud()))})", sites.with_hud())]
        con.close()
        found = windows()
        if not found:
            print("no windows to list -- this is Windows-only")
        for title, x, y, w, h in found:
            hit = match(title, tables)
            print(f"{'-> ' + hit[0] + ' ' + hit[1] if hit else '':40} "
                  f"{w}x{h}+{x}+{y}  {title}")
        return 0
    if "--print" in argv:
        con = sqlite3.connect(DB)
        for site, table_id in last_tables(con, 3):
            print(as_text(view(con, site, table_id)))
            print()
        con.close()
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
