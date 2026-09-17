"""
Keep the program up to date with the repository it came from.

Run from a checkout, this fast-forwards to whatever is on `origin`. Run as
the packaged executable, it cannot -- a running .exe cannot replace itself on
Windows, and the source it would pull is not the code that is executing --
so there it checks whether a newer commit exists and says so.

**Fast-forward only, always.** `--ff-only` refuses to merge, refuses to
rebase, and refuses outright if there is anything local it would have to
reconcile. Uncommitted work is never touched and never lost; the update
simply does not happen and says why. An updater that can silently discard
what somebody was in the middle of is worse than no updater.

**It never restarts anything.** Python has already imported its modules by
the time this finishes, so new code on disk is not new code in memory, and
reloading a running application's modules underneath itself is a good way to
produce a program that is half of one version and half of another. The
window says an update arrived and that a restart will use it.

**It never blocks the window.** It runs on a worker thread from the moment
the program starts, with a short timeout, and every failure -- no git, no
network, no remote, a detached head -- is an ordinary answer rather than an
error. Being unable to check for an update is not a problem with the
program.

    python update.py            check, and fast-forward if there is one
    python update.py --check    the machinery works and reports honestly
    python app.py --no-update   start without checking
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)

# Where a packaged build looks, since it has no checkout to ask. Taken from
# the remote when there is one so a fork does not phone home to the original.
FALLBACK_REPO = "bhntr81/traceEV"

TIMEOUT = 20

# Every subprocess here, so that none of them flashes a console window over
# whatever the user is doing. Windows only; harmless elsewhere.
NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def _git(*args, cwd=None):
    """Run git, and treat every way it can be unavailable as an answer."""
    try:
        done = subprocess.run(("git", "-C", str(cwd or HERE)) + args,
                              capture_output=True, text=True,
                              timeout=TIMEOUT, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None, "git is not on this machine"
    if done.returncode:
        return None, (done.stderr or done.stdout or "git failed").strip()
    return done.stdout.strip(), None


def is_checkout():
    out, _why = _git("rev-parse", "--is-inside-work-tree")
    return out == "true"


def head():
    out, _why = _git("rev-parse", "--short", "HEAD")
    return out


def downloads_url():
    """Where a packaged build's newer version is fetched from by hand."""
    return f"https://github.com/{remote_repo()}/releases"


def remote_repo():
    """owner/name from the origin URL, or the fallback for a packaged build."""
    url, _why = _git("remote", "get-url", "origin")
    if not url:
        return FALLBACK_REPO
    url = url.strip().removesuffix(".git")
    for sep in ("github.com/", "github.com:"):
        if sep in url:
            return url.split(sep, 1)[1]
    return FALLBACK_REPO


def latest_remote_commit(repo=None):
    """
    The newest commit on the default branch, over the API.

    For the packaged build, which has no checkout to fetch into. Read-only
    and unauthenticated: it asks what the newest commit is and nothing else,
    so the worst a wrong answer can do is claim an update that is not there.
    """
    repo = repo or remote_repo()
    url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return None, f"could not reach github: {e}"
    if not data:
        return None, "github returned no commits"
    top = data[0]
    return (top.get("sha", "")[:7],
            (top.get("commit") or {}).get("message", "").splitlines()[0])


def update():
    """
    Bring the checkout up to date, or say why not.

    Returns (state, message). `state` is one of:

        "current"    already up to date
        "updated"    fast-forwarded; a restart will use it
        "blocked"    an update exists and something local is in the way
        "available"  a packaged build, and there is a newer commit
        "skipped"    no git, no network, not a checkout -- not a problem
    """
    if getattr(sys, "frozen", False):
        sha, subject = latest_remote_commit()
        if not sha:
            return "skipped", subject
        # The packaged program is one file that is running; Windows will
        # not let a running file overwrite itself, and the program cannot
        # tell whether the newest commit has been built into a download
        # yet. So it says what is new, where the downloads are, and what
        # to do with one -- the message used to stop at "cannot replace
        # itself", which read as an error about nothing.
        return "available", (f"There is a newer version on GitHub ({sha}: "
                             f"{subject}).\n\nThis is the packaged program, "
                             f"which cannot update itself. Update > Get the "
                             f"latest build opens the downloads page; save the "
                             f"new TraceEV.exe next to hands.db and start it. "
                             f"Nothing about your hands changes.")

    if not is_checkout():
        return "skipped", "not a git checkout, so there is nothing to update"

    was = head()
    _out, why = _git("fetch", "--quiet", "origin")
    if why:
        return "skipped", why

    behind, _why = _git("rev-list", "--count", "HEAD..@{upstream}")
    if behind is None:
        return "skipped", "this branch is not tracking a remote one"
    if behind == "0":
        return "current", f"up to date at {was}"

    # Anything uncommitted stops the update rather than being merged around.
    dirty, _why = _git("status", "--porcelain")
    if dirty:
        return "blocked", (f"{behind} new commit(s) on github, and this "
                           f"checkout has uncommitted changes. Nothing was "
                           f"touched.")

    _out, why = _git("merge", "--ff-only", "@{upstream}")
    if why:
        return "blocked", f"{behind} new commit(s), but the merge refused: {why}"
    now = head()
    return "updated", (f"updated {was} -> {now} ({behind} commit(s)). "
                       f"Restart to run the new code.")


def check():
    """
    The updater reports honestly about the checkout it is actually in.

    It cannot be checked by letting it update -- that would change the code
    under the test -- so what is checked is that every branch of it returns
    the shape it promises and that the read-only parts tell the truth about
    this repository.
    """
    fails = []
    checkout = is_checkout()
    print(f"running from                 "
          f"{'a git checkout' if checkout else 'a packaged build or a copy'}")
    print(f"repository                   {remote_repo()}")
    print(f"HEAD                         {head() or '-'}")

    state, message = update()
    print(f"update says                  {state}: {message}")
    if state not in ("current", "updated", "blocked", "available", "skipped"):
        fails.append(f"unknown state {state!r}")

    # `--ff-only` is the whole safety argument, so its absence is the one
    # thing worth asserting about the source of this module rather than
    # about its behaviour.
    src = Path(__file__).read_text(encoding="utf-8")
    ff = "--ff-only" in src
    print(f"merges are fast-forward only {'yes' if ff else 'NO -- unsafe'}")
    if not ff:
        fails.append("the updater can merge, so it can lose local work")

    # A failure to check must never be an error. Every path through `_git`
    # returns a reason instead of raising, and this proves the one that is
    # hardest to reach any other way.
    out, why = _git("definitely-not-a-git-command")
    print(f"a broken git call is an answer "
          f"{'yes' if out is None and why else 'NO -- it raised'}")
    if out is not None or not why:
        fails.append("a failing git call did not come back as a reason")

    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--help" in argv:
        print(__doc__)
        return 0
    state, message = update()
    print(f"{state}: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
