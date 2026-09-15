"""
Somewhere for a failure to go, because in a packaged window there is nowhere.

Run from a terminal, a Python program that breaks says so. Packaged with
`--windowed` it has no console at all: `sys.stderr` is None, and Tk's own
handler for an exception raised inside a callback writes the traceback
there. So a click that raises does nothing, silently, and looks exactly like
a click that was ignored -- which is how a filter dialog spent an afternoon
appearing to have no effect.

Three places a failure can escape, and all three are caught here:

  the main thread          `sys.excepthook`
  a worker thread          `threading.excepthook`
  inside a Tk callback     `Tk.report_callback_exception`

Everything lands in one log beside the program, with the breadcrumbs that
led to it. The log is the point: a crash report nobody can find is not a
crash report.

    python diag.py            where the log is, and what is in it
    python diag.py --check    a raised error really is caught and written
"""

import datetime
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path

# Beside the executable when frozen, beside the source otherwise -- the same
# rule the database follows, so the log sits where somebody would look.
HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)
LOG = HERE / "TraceEV.log"

_log = logging.getLogger("TraceEV")
_installed = False


def setup(verbose=False):
    """Start logging, and make sure nothing can fail without saying so."""
    global _installed
    if _installed:
        return _log
    _log.setLevel(logging.DEBUG)
    # Rotating, because a log that grows without limit is one that gets
    # deleted rather than read.
    handler = logging.handlers.RotatingFileHandler(
        LOG, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _log.addHandler(handler)
    if verbose and sys.stderr is not None:
        echo = logging.StreamHandler(sys.stderr)
        echo.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        _log.addHandler(echo)

    sys.excepthook = _on_uncaught
    threading.excepthook = _on_thread_uncaught
    _installed = True
    _log.info("--- started  pid=%s  frozen=%s  python=%s",
              os.getpid(), getattr(sys, "frozen", False),
              sys.version.split()[0])
    return _log


def watch_tk(root):
    """
    Catch what Tk would otherwise swallow.

    Tk calls this for any exception raised inside a callback -- a button
    press, a menu command, a scheduled `after`. Its default implementation
    prints to stderr and carries on, which in a windowed build means the
    error is destroyed rather than reported.
    """
    def handler(_exc, value, tb, _root=root):
        _report("tk callback", value, tb)
        _show(root, value)
    root.report_callback_exception = handler
    return root


def event(what, **detail):
    """A breadcrumb. These are what make a traceback mean something."""
    if detail:
        _log.debug("%s  %s", what,
                   "  ".join(f"{k}={v!r}" for k, v in detail.items()))
    else:
        _log.debug(what)


def _report(where, value, tb):
    text = "".join(traceback.format_exception(type(value), value, tb))
    _log.error("unhandled in %s\n%s", where, text.rstrip())
    return text


def _on_uncaught(kind, value, tb):
    _report("main thread", value, tb)
    _show(None, value)


def _on_thread_uncaught(args):
    # A thread dying quietly is worse than the main one dying loudly: the
    # window stays up and simply stops answering.
    _report(f"thread {args.thread.name if args.thread else '?'}",
            args.exc_value, args.exc_traceback)


def _show(root, value):
    """Tell the user, once, and say where the detail is."""
    try:
        from tkinter import messagebox
        messagebox.showerror(
            "TraceEV hit a problem",
            f"{type(value).__name__}: {value}\n\n"
            f"The details are in:\n{LOG}\n\n"
            "The program is still running; what you just did did not finish.")
    except Exception:
        pass          # no display, or Tk already gone -- the log still has it


def tail(n=40):
    if not LOG.exists():
        return "(no log yet)"
    return "\n".join(LOG.read_text(encoding="utf-8",
                                   errors="replace").splitlines()[-n:])


def check():
    """
    An error raised in each of the three places must reach the log.

    Tested by actually raising them, because the whole value of this module
    is in the paths that only run when something has already gone wrong --
    which is exactly the code least likely to have been exercised.
    """
    import tkinter as tk
    fails = []
    setup()
    marker = f"check-{datetime.datetime.now():%H%M%S%f}"
    before = LOG.stat().st_size if LOG.exists() else 0

    # 1. a worker thread
    def boom():
        raise ValueError(f"{marker}-thread")
    t = threading.Thread(target=boom, name="check-thread")
    t.start()
    t.join()

    # 2. a Tk callback
    root = tk.Tk()
    root.withdraw()
    watch_tk(root)
    shown = {"n": 0}
    import tkinter.messagebox as mb
    real = mb.showerror
    mb.showerror = lambda *a, **k: shown.__setitem__("n", shown["n"] + 1)
    root.after(1, lambda: (_ for _ in ()).throw(ValueError(f"{marker}-tk")))
    root.update()
    root.after(20, root.quit)
    root.mainloop()
    mb.showerror = real
    root.destroy()

    # Sliced as BYTES and decoded afterwards, because `before` came from
    # `st_size` and that is bytes. Slicing the decoded text by a byte offset
    # works only while the log is pure ASCII, and it stopped being so the
    # moment a filter label with an em-dash in it reached a breadcrumb: the
    # slice then started past the beginning of the new lines and this check
    # reported a worker thread's error as lost when it had been written
    # correctly.
    text = LOG.read_bytes()[before:].decode("utf-8", errors="replace")
    for where, needle in (("worker thread", f"{marker}-thread"),
                          ("tk callback", f"{marker}-tk")):
        ok = needle in text
        print(f"error in a {where:14} {'logged' if ok else 'LOST'}")
        if not ok:
            fails.append(where)
    print(f"the user was told           "
          f"{'yes' if shown['n'] else 'NO -- it failed silently'}")
    if not shown["n"]:
        fails.append("nothing was shown to the user")

    print(f"log file                    {LOG}")
    print(f"log size                    {LOG.stat().st_size:,} bytes")
    print()
    print("FAIL: " + ", ".join(fails) if fails else "PASS")
    return not fails


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    setup()
    print(f"log: {LOG}\n")
    print(tail(40))
