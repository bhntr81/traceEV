"""
The HUD on the screen: a panel per seat, over each poker table's window.

`hud.py` decides what a panel says; this decides where it goes. Every half
second it lists the windows on the screen, keeps the ones whose title names
a table we hold hands for, and puts the latest hand's seats round each of
them -- moving with the window, gone when it closes or is minimised, and
redrawn when a new hand at that table arrives (Import -> Import new hands
while open is what brings it).

Finding the windows is the only part that depends on the platform, and it
sits behind `windows()`: the real one asks Windows through `ctypes`, which
is where every client the HUD serves runs; anything else can be handed in,
which is how it is tested here on Linux.
"""

import json
import math
import queue
import sqlite3
import sys
import threading
import tkinter as tk
from pathlib import Path

import hud
import sites

HERE = Path(__file__).parent

# Panel offsets the user dragged, per site, table size and seat, as
# fractions of the table window so they survive a resize. Hold'em Manager,
# PokerTracker and Hand2Note all keep the layout per site and table size,
# because seat 3 on a 6-max table and on a 9-max table are different places.
LAYOUT = HERE / "hud.json"

POLL = 500          # ms between looks at the windows


def windows_win32():
    """Every visible, not minimised top-level window: (handle, title, rect)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(each, 0)
    return found


def match(title, names):
    """
    The table a window title belongs to, or None.

    Clients put the table's name first in the title -- "Lampland - $0.25/$0.50
    USD - No Limit Hold'em" -- and the same name is in every hand played
    there. So a title belongs to a table when it starts with the name and
    the name ends there; the longest such name wins, because "May" must not
    take the window of "May IV".
    """
    best = None
    for name in names:
        if title.startswith(name) and (len(title) == len(name)
                                       or not title[len(name)].isalnum()):
            if best is None or len(name) > len(best):
                best = name
    return best


def ring(max_seats, seat):
    """
    Where a seat's panel goes by default, as fractions of the table window.

    Round an oval, seat 1 at the top left and clockwise. Every client seats
    people differently and some rotate the table to put you at the bottom,
    so this is a start and the panels are dragged from it -- which is the
    convention, and the reason the layout is saved.
    """
    angle = math.pi * (1.25 + 2 * (seat - 1) / max(max_seats, 2))
    return 0.5 + 0.42 * math.cos(angle), 0.48 + 0.38 * math.sin(angle)


def load_layout(path=LAYOUT):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_layout(layout, path=LAYOUT):
    try:
        path.write_text(json.dumps(layout, indent=1, sort_keys=True))
    except OSError:
        pass


class Panel(tk.Toplevel):
    """One seat's numbers: borderless, on top, dragged to where it belongs."""

    def __init__(self, overlay, key):
        super().__init__(overlay.root)
        self.overlay, self.key = overlay, key
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.85)
        except tk.TclError:
            pass                       # a display without compositing
        self.label = tk.Label(self, text="", justify="left", padx=4, pady=1,
                              background="#14161a", foreground="#e8e6e3",
                              font=("Consolas", 9))
        self.label.pack()
        self.label.bind("<ButtonPress-1>", self._grab)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", self._drop)
        self._from = None

    def show(self, lines, x, y):
        self.label.configure(text="\n".join(lines))
        self.geometry(f"+{int(x)}+{int(y)}")

    def _grab(self, e):
        self._from = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag(self, e):
        if self._from:
            self.geometry(f"+{e.x_root - self._from[0]}+{e.y_root - self._from[1]}")

    def _drop(self, _e):
        if self._from:
            self.overlay.moved(self.key, self.winfo_x(), self.winfo_y())
        self._from = None


class Overlay:
    """
    Every table's panels, kept in step with the windows and the database.

    Panels are computed on a worker with a connection of its own, because a
    table's panels are tens of milliseconds and a nine-handed table with a
    long history a quarter of a second -- enough to stutter the window's own
    drawing if it were done on the interface thread.
    """

    def __init__(self, root, db_path, windows=None, layout_path=LAYOUT):
        self.root, self.db_path = root, Path(db_path)
        self.windows = windows or windows_win32
        self.layout_path = layout_path
        self.layout = load_layout(layout_path)
        self.tables = {}        # (site, table) -> {"hand", "max", "rect", "panels", "lines"}
        self.asked = queue.Queue()
        self.answers = queue.Queue()
        self.running = True
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(0, self._tick)

    def stop(self):
        self.running = False
        self.asked.put(None)
        for t in self.tables.values():
            for p in t["panels"].values():
                p.destroy()
        self.tables.clear()

    def _names(self, con):
        named = [s.key for s in sites.SITES if s.names]
        return {t: s for s, t in con.execute(
            f"SELECT DISTINCT site, table_id FROM hands WHERE site IN "
            f"({', '.join('?' * len(named))}) AND fmt <> 'MTT'", named)}

    def _worker(self):
        con = sqlite3.connect(self.db_path)
        try:
            while True:
                job = self.asked.get()
                if job is None:
                    return
                site, table_id = job
                table = hud.seated(con, site, table_id)
                if table:
                    panels = {p["seat"]: hud.text(p)
                              for p in hud.panels(con, table)}
                    self.answers.put(((site, table_id), table, panels))
        finally:
            con.close()

    def _tick(self):
        if not self.running:
            return
        try:
            self._look()
            self._collect()
        finally:
            self.root.after(POLL, self._tick)

    def _look(self):
        con = sqlite3.connect(self.db_path)
        try:
            names = self._names(con)
            seen = set()
            for _handle, title, rect in self.windows():
                name = match(title, names)
                if name is None:
                    continue
                key = (names[name], name)
                seen.add(key)
                latest = con.execute(
                    "SELECT hand_id FROM hands WHERE site=? AND table_id=? "
                    "ORDER BY played_at DESC, hand_id DESC LIMIT 1", key
                ).fetchone()[0]
                t = self.tables.setdefault(
                    key, {"hand": None, "max": None, "rect": None,
                          "panels": {}, "lines": {}})
                if t["hand"] != latest:
                    t["hand"] = latest
                    self.asked.put(key)
                if t["rect"] != rect:
                    t["rect"] = rect
                    self._place(key)
        finally:
            con.close()
        for key in [k for k in self.tables if k not in seen]:
            for p in self.tables[key]["panels"].values():
                p.destroy()
            del self.tables[key]

    def _collect(self):
        while True:
            try:
                key, table, lines = self.answers.get_nowait()
            except queue.Empty:
                return
            t = self.tables.get(key)
            if t is None or table["hand_id"] != t["hand"]:
                continue        # the window closed, or a newer hand is on its way
            t["max"], t["lines"] = table["max_seats"], lines
            for seat in [s for s in t["panels"] if s not in lines]:
                t["panels"].pop(seat).destroy()
            for seat in lines:
                if seat not in t["panels"]:
                    t["panels"][seat] = Panel(self, key + (seat,))
            self._place(key)

    def _spot(self, key, seat):
        """A seat's panel position as fractions of its table window."""
        t = self.tables[key]
        saved = self.layout.get(f"{key[0]}|{t['max']}|{seat}")
        return tuple(saved) if saved else ring(t["max"], seat)

    def _place(self, key):
        t = self.tables[key]
        if not t["rect"] or not t["max"]:
            return
        left, top, right, bottom = t["rect"]
        for seat, panel in t["panels"].items():
            fx, fy = self._spot(key, seat)
            panel.show(t["lines"].get(seat, ["-"]),
                       left + fx * (right - left), top + fy * (bottom - top))

    def moved(self, panel_key, x, y):
        site, table_id, seat = panel_key
        t = self.tables.get((site, table_id))
        if not t or not t["rect"] or not t["max"]:
            return
        left, top, right, bottom = t["rect"]
        w, h = max(right - left, 1), max(bottom - top, 1)
        self.layout[f"{site}|{t['max']}|{seat}"] = [(x - left) / w, (y - top) / h]
        save_layout(self.layout, self.layout_path)
        # Every other table of that site and size takes the new spot too.
        for key, other in self.tables.items():
            if key[0] == site and other["max"] == t["max"]:
                self._place(key)


def main(argv):
    if sys.platform != "win32":
        print("the HUD draws over Windows poker clients; see hud.py for its "
              "numbers on any platform")
        return 1
    root = tk.Tk()
    root.withdraw()
    Overlay(root, Path(argv[0]) if argv else hud.DB)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
