"""
Package the tracker as one program, on whichever of the three this is.

The program is standard library only, so nothing here is needed to *run*
it -- PyInstaller is a build-time tool and never a dependency of the thing
it builds. `python app.py` works with none of this installed, on any machine
with Python, which is the portable form and the one that needs no build at
all.

**PyInstaller cannot cross-compile.** A Windows .exe can only be produced on
Windows, a Mac .app only on a Mac, a Linux binary only on Linux -- it works
by copying *this* machine's Python runtime into the output. So this script
builds for the machine it is run on and says so, and `.github/workflows`
runs it on all three so that a tag produces all three downloads without
anybody owning all three computers.

**The database is not bundled.** It is the user's data, it is eighty-odd
megabytes, and it changes every time hands are imported; freezing a copy
inside the program would ship a snapshot that silently goes stale. The
program looks for `hands.db` beside itself instead, which is also what makes
the pair movable: copy the program and the database to another machine and
it runs with no Python installed.

    python build.py            build for this machine
    python build.py --check    is the toolchain here, and did it work
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import sites

# The parser modules, from the registry rather than a second list here --
# a parser added there and forgotten here is a window that fails on the
# first import.
PARSERS = [s.module.__name__ for s in sites.SITES]

HERE = Path(__file__).parent
DIST = HERE / "dist"

# What the output is called on each platform, and what a user double-clicks.
# On a Mac `--windowed` produces a .app bundle *as well as* the bare binary,
# and the bundle is the thing that opens without a terminal, so it is the
# one named here.
TARGETS = {
    "Windows": ("poker_analysis.exe", "poker_analysis.exe"),
    "Darwin": ("poker_analysis.app", "poker_analysis"),
    "Linux": ("poker_analysis", "poker_analysis"),
}

# Every module the app reaches at runtime. PyInstaller finds imports by
# reading the code and it reads these correctly -- they are listed anyway
# because a missed one produces a program that starts and then fails on the
# first click, which is a much worse way to find out.
MODULES = ["query", "stats", "equity", "decisions", "spots", "lines",
           "strength", "players", "importer", "sites", "diag",
           "update", "sessions", "notes", "aliases", "compact",
           "expr", "parity"] + PARSERS


def target():
    """What this machine builds, and what to run to test it."""
    name, runnable = TARGETS.get(platform.system(), TARGETS["Linux"])
    return DIST / name, DIST / runnable


def build():
    if shutil.which("pyinstaller") is None and not _module("PyInstaller"):
        print("PyInstaller is not installed. It is only needed to build:\n"
              "    python -m pip install pyinstaller")
        return False
    out, _run = target()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        # No console window behind the app. A GUI that drags a black
        # terminal around with it is the thing this was meant to stop.
        "--windowed",
        "--name", "poker_analysis",
        "--distpath", str(DIST),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--noconfirm",
    ]
    for m in MODULES:
        cmd += ["--hidden-import", m]
    cmd.append(str(HERE / "app.py"))
    print(f"building for {platform.system()} {platform.machine()}")
    proc = subprocess.run(cmd, cwd=str(HERE))
    if proc.returncode:
        print("build failed")
        return False
    size = _size(out)
    print(f"\nbuilt {out}  ({size / 1e6:.1f} MB)" if size else
          f"\nbuild reported success but {out} is not there")
    print("put hands.db next to it and open it.")
    return bool(size)


def _size(path):
    if path.is_dir():           # a .app is a folder
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return path.stat().st_size if path.exists() else 0


def _module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def check():
    """
    The toolchain is here, and anything already built actually starts.

    Starting is the whole of what is checked, and it is worth checking: a
    packaged GUI that fails on launch does so with no console and no message
    -- the window simply never appears -- so the one thing worth proving
    automatically is that the program runs its own `--check` and exits zero.
    """
    fails = []
    print(f"building for              {platform.system()} "
          f"{platform.machine()}, python {platform.python_version()}")
    have = _module("PyInstaller")
    print(f"pyinstaller available     {'yes' if have else 'no (build-time only)'}")

    out, run = target()
    if _size(out):
        proc = subprocess.run([str(run), "--check"], capture_output=True,
                              text=True, timeout=600, cwd=str(HERE))
        ok = proc.returncode == 0 and "PASS" in (proc.stdout or "")
        print(f"built program runs its check  {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append("the built program does not pass its own check")
            print((proc.stdout or proc.stderr or "")[-400:])
        near = DIST / "hands.db"
        print(f"database beside it        "
              f"{'yes' if near.exists() else 'no -- copy hands.db into dist/'}")
    else:
        print(f"built program             not built yet (python build.py)")

    # Nothing outside the standard library may be imported at runtime, which
    # is what makes the source itself portable and the build optional. A
    # dependency that crept in would still run here and fail on a machine
    # that has not got it.
    missing = _third_party()
    print(f"runtime imports            "
          f"{'standard library only' if not missing else 'NOT PORTABLE: ' + ', '.join(missing)}")
    if missing:
        fails.append("a runtime module imports something outside the stdlib")

    # And nothing may sit in the folder that nothing runs. 2,733 lines of a
    # different program lived here for weeks with a note in the README
    # explaining why they were not deleted, which is a problem being
    # described rather than a decision being made. This is the check that
    # would have said so on the first day.
    orphans = _unreachable()
    print(f"every module is reachable  "
          f"{'yes' if not orphans else 'NO: ' + ', '.join(orphans)}")
    if orphans:
        fails.append("modules nothing imports or runs: " + ", ".join(orphans))

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


# The modules the program actually runs. This script is not among them --
# PyInstaller is a build tool and never a dependency of what it builds.
RUNTIME = ("app", "query", "stats", "spots", "decisions", "lines", "strength",
           "players", "equity", "importer", "sites", "diag",
           "update", "opponents", "gui",
           "sessions", "notes", "aliases", "compact", "expr",
           "parity") + tuple(PARSERS)


def _third_party():
    """Anything imported at runtime that will not be there on a fresh box."""
    import ast
    import sys as _sys
    stdlib = set(_sys.stdlib_module_names)
    ours = set(RUNTIME) | {"population", "check", "build"}
    bad = set()
    for name in RUNTIME:
        path = HERE / f"{name}.py"
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [(node.module or "").split(".")[0]]
            else:
                continue
            bad |= {m for m in mods if m and m not in stdlib and m not in ours}
    return sorted(bad)


def _unreachable():
    """
    Modules nothing reaches from the three things anybody runs.

    The roots are the program, the check suite and this script. `check.py`
    names its modules as strings rather than importing them, so those are
    read out of it -- a module that has a check is a module with a reason to
    exist even if nothing imports it.
    """
    import ast
    mods = {p.stem: p for p in HERE.glob("*.py")}
    named = set()
    text = (HERE / "check.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith('("') and '",' in line:
            named.add(line.strip().split('"')[1])

    def imports(path):
        out = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                out |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
        return out

    seen, queue = set(), ["app", "check", "build"] + sorted(named)
    while queue:
        m = queue.pop()
        if m in seen or m not in mods:
            continue
        seen.add(m)
        queue += [x for x in imports(mods[m]) if x in mods]
    return sorted(set(mods) - seen)


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    sys.exit(0 if build() else 1)
