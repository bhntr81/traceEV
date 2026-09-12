"""
Named groups of (username, room) accounts.

An alias is not a player and not a class. It is a list of seats that
already exist, kept under a name you chose, in a JSON file beside the
database -- the same shape as `stats.json` and `filters.json`, and
gitignored for the same reason. Rebuilding `hands.db` used to delete
columns; an alias you typed is not a derived column.

    Is Single Person = true   merge the histories as one person
                              (hero on a second site, a second account)
    Is Single Person = false  a style / group, usable as a villain pool

`--hero` already merges every imported hero seat via `is_hero = 1`.
A single-person alias is the same idea for names that are not that
flag: `--alias me` is the OR of its (site, player) pairs. A group
alias is the same SQL pointed at the other seat: `--vs-alias nits`.

    python aliases.py --list
    python aliases.py --create me --player NAME --site acr --single
    python aliases.py --add me --player OTHER --site pokerstars
    python aliases.py --forget me
    python aliases.py --import accounts.csv
    python aliases.py --export accounts.csv
    python aliases.py --check

CSV columns, Hand2Note's order: Alias, Username, Room, Is Single Person.

`--player me` does not expand an alias -- a screen name and an alias
can share a word, and silently treating the name as the group would
drop the person. `--alias` / `--vs-alias` are the doors.

Not this, on purpose: rewriting `--by player` as the alias display
name; save-cohort-as-alias; HUD. `--hero` still covers "me on every
site" for seats the importer marked.
"""

import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import sites

PATH = Path(__file__).parent / "aliases.json"

# Letters first, so `check` (a SQL keyword) is a legal alias name
# and never an identifier -- the predicate always uses bound/quoted
# player and site values, never the alias name as SQL.
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _.-]{0,39}$")

ROOM = {
    "acr": "acr",
    "wpn": "acr",
    "winning": "acr",
    "americascardroom": "acr",
    "blackchip": "acr",
    "ignition": "ignition",
    "bovada": "ignition",
    "bodog": "ignition",
    "pokerstars": "pokerstars",
    "ps": "pokerstars",
    "stars": "pokerstars",
    "pokerstars.fr": "pokerstars",
    "pokerstars.eu": "pokerstars",
    "pokerstars.uk": "pokerstars",
}

CSV_FIELDS = ("Alias", "Username", "Room", "Is Single Person")


def valid_name(name):
    return bool(name) and NAME.fullmatch(str(name).strip())


def room_of(raw):
    """A typed room → a `sites.KEYS` value. Unknown is an error."""
    key = str(raw or "").strip().lower()
    if key in sites.KEYS:
        return key
    if key in ROOM:
        return ROOM[key]
    raise ValueError(
        f"unknown room {raw!r} -- one of: {', '.join(sites.KEYS)} "
        f"(or wpn, bovada, ps, stars)")


def _bool(raw):
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "y"):
        return True
    if v in ("0", "false", "no", "n"):
        return False
    raise ValueError(
        f"Is Single Person must be true or false, not {raw!r}")


def load(path=None):
    p = Path(path) if path is not None else PATH
    if not p.exists() or p.stat().st_size == 0:
        return {}
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("aliases.json must be an object of named aliases")
    return data


def save(store, path=None):
    p = Path(path) if path is not None else PATH
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(p)


def get(name, path=None):
    key = str(name).strip()
    store = load(path)
    if key not in store:
        raise ValueError(f"no alias {name!r} -- aliases.py --list")
    return store[key]


def members_of(name, path=None):
    alias = get(name, path)
    return list(alias.get("members") or []), bool(alias.get("single"))


def _q(value):
    return "'" + str(value).replace("'", "''") + "'"


def where_sql(name, who="player", path=None):
    """
    `(site = 'acr' AND player = 'X') OR ...` for `build` to AND in.

    `who` is `player` or `vs_player`. The alias name itself never
    reaches SQL -- a name like `check` is a catalog key, not a column.
    """
    members, _single = members_of(name, path)
    if not members:
        raise ValueError(f"alias {name!r} has no accounts")
    parts = []
    for m in members:
        parts.append(
            f"(site = {_q(m['site'])} AND {who} = {_q(m['player'])})")
    return "(" + " OR ".join(parts) + ")"


def create(name, player, site, single=False, path=None):
    if not valid_name(name):
        raise ValueError(
            f"invalid alias {name!r} -- start with a letter; "
            "letters, digits, space, _.-")
    player = str(player or "").strip()
    if not player:
        raise ValueError("an alias member needs a username")
    site = room_of(site)
    store = load(path)
    key = str(name).strip()
    if key in store:
        raise ValueError(f"alias {key!r} already exists -- use --add")
    store[key] = {
        "single": bool(single),
        "members": [{"player": player, "site": site}],
    }
    save(store, path)
    return store[key]


def add(name, player, site, path=None):
    player = str(player or "").strip()
    if not player:
        raise ValueError("an alias member needs a username")
    site = room_of(site)
    store = load(path)
    key = str(name).strip()
    if key not in store:
        raise ValueError(f"no alias {key!r} -- --create it first")
    members = store[key].setdefault("members", [])
    pair = {"player": player, "site": site}
    if pair not in members:
        members.append(pair)
    save(store, path)
    return store[key]


def forget(name, path=None):
    store = load(path)
    key = str(name).strip()
    if key not in store:
        raise ValueError(f"no alias {key!r}")
    del store[key]
    save(store, path)


def remove_member(name, player, site, path=None):
    store = load(path)
    key = str(name).strip()
    if key not in store:
        raise ValueError(f"no alias {key!r}")
    site = room_of(site)
    player = str(player).strip()
    before = store[key]["members"]
    store[key]["members"] = [
        m for m in before
        if not (m["player"] == player and m["site"] == site)]
    if not store[key]["members"]:
        del store[key]
    save(store, path)


def read_csv(text):
    """CSV text → {name: {single, members}}. Flag must agree per name."""
    sample = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        raise ValueError("CSV needs a header: Alias, Username, Room, "
                         "Is Single Person")
    fields = {f.strip(): f for f in reader.fieldnames}
    needed = {n.lower(): n for n in CSV_FIELDS}
    remap = {}
    for want, label in needed.items():
        for got, orig in fields.items():
            if got.lower() == want:
                remap[label] = orig
                break
        else:
            raise ValueError(
                f"CSV is missing {label!r} -- columns are "
                + ", ".join(CSV_FIELDS))
    out = {}
    for row in reader:
        name = (row.get(remap["Alias"]) or "").strip()
        player = (row.get(remap["Username"]) or "").strip()
        room = (row.get(remap["Room"]) or "").strip()
        flag = row.get(remap["Is Single Person"])
        if not name and not player:
            continue
        if not valid_name(name):
            raise ValueError(f"invalid alias {name!r} in CSV")
        if not player:
            raise ValueError(f"alias {name!r} is missing a username")
        site = room_of(room)
        single = _bool(flag)
        if name not in out:
            out[name] = {"single": single, "members": []}
        elif bool(out[name]["single"]) != single:
            raise ValueError(
                f"alias {name!r} has both single-person and group rows")
        pair = {"player": player, "site": site}
        if pair not in out[name]["members"]:
            out[name]["members"].append(pair)
    return out


def write_csv(store, dest):
    dest = Path(dest)
    with dest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDS))
        w.writeheader()
        for name in sorted(store):
            alias = store[name]
            flag = "true" if alias.get("single") else "false"
            for m in alias.get("members") or []:
                w.writerow({
                    "Alias": name,
                    "Username": m["player"],
                    "Room": m["site"],
                    "Is Single Person": flag,
                })


def import_csv(source, path=None):
    raw = Path(source).read_text(encoding="utf-8")
    incoming = read_csv(raw)
    store = load(path)
    for name, alias in incoming.items():
        if name in store and bool(store[name].get("single")) != bool(
                alias["single"]):
            raise ValueError(
                f"alias {name!r} is already "
                f"{'single-person' if store[name].get('single') else 'a group'}")
        if name not in store:
            store[name] = {"single": alias["single"], "members": []}
        have = store[name]["members"]
        for m in alias["members"]:
            if m not in have:
                have.append(m)
    save(store, path)
    return incoming


def show_list(path=None):
    store = load(path)
    if not store:
        print("no aliases")
        return
    for name in sorted(store):
        alias = store[name]
        kind = "single person" if alias.get("single") else "group"
        print(f"{name}  ({kind})")
        for m in alias.get("members") or []:
            print(f"  {m['site']:12} {m['player']}")


def check():
    """CRUD, CSV, and the SQL shape. Never writes the project's aliases.json."""
    fails = []
    fd, raw = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    path = Path(raw)
    try:
        path.write_text("{}", encoding="utf-8")
        create("me", "HeroACR", "acr", single=True, path=path)
        add("me", "HeroPS", "stars", path=path)
        members, single = members_of("me", path=path)
        if not single:
            fails.append("--single did not stick")
        if {(m["site"], m["player"]) for m in members} != {
                ("acr", "HeroACR"), ("pokerstars", "HeroPS")}:
            fails.append(f"members were {members}")
        sql = where_sql("me", "player", path=path)
        if "HeroACR" not in sql or "pokerstars" not in sql:
            fails.append(f"where_sql dropped a member: {sql}")
        if "me" in sql.replace("HeroACR", "").replace("HeroPS", ""):
            # The alias name must not be interpolated as SQL.
            if "alias" in sql.lower():
                fails.append(f"alias name leaked into SQL: {sql}")
        vs = where_sql("me", "vs_player", path=path)
        if "vs_player" not in vs or "player =" in vs.replace("vs_player", ""):
            fails.append(f"--vs-alias SQL was {vs}")

        create("nits", "TightGuy", "wpn", single=False, path=path)
        _m, group = members_of("nits", path=path)
        if group:
            fails.append("group alias was marked single")
        if "TightGuy" not in where_sql("nits", "vs_player", path=path):
            fails.append("group vs-alias dropped the member")

        csv_path = path.with_suffix(".csv")
        write_csv(load(path), csv_path)
        text = csv_path.read_text(encoding="utf-8")
        if "Alias" not in text or "HeroACR" not in text:
            fails.append(f"export missed a row: {text!r}")
        forget("me", path=path)
        import_csv(csv_path, path=path)
        members, single = members_of("me", path=path)
        if not single or len(members) != 2:
            fails.append(f"CSV round-trip lost me: {members}")

        mixed = (
            "Alias,Username,Room,Is Single Person\n"
            "crew,A,acr,true\n"
            "crew,B,acr,false\n")
        try:
            read_csv(mixed)
            fails.append("mixed single/group rows were accepted")
        except ValueError:
            pass
        try:
            create("bad name!", "x", "acr", path=path)
            fails.append("invalid alias name was accepted")
        except ValueError:
            pass
        try:
            create("x", "y", "not-a-room", path=path)
            fails.append("unknown room was accepted")
        except ValueError:
            pass
        try:
            get("missing", path=path)
            fails.append("missing alias did not raise")
        except ValueError:
            pass
        # A name that is a SQL keyword still produces quoted predicates.
        create("check", "O'Brien", "acr", single=True, path=path)
        sql = where_sql("check", "player", path=path)
        if "O''Brien" not in sql:
            fails.append(f"quote was not doubled: {sql}")
        if re.search(r"\bcheck\b", sql, re.I) and "O''Brien" in sql:
            # `check` the keyword must not appear as an identifier.
            if "check =" in sql.lower() or "alias check" in sql.lower():
                fails.append(f"SQL keyword alias leaked: {sql}")
    finally:
        for p in (path, path.with_suffix(".csv"),
                  path.with_suffix(path.suffix + ".tmp")):
            try:
                p.unlink()
            except OSError:
                pass

    print(f"aliases store and CSV         "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return not fails


def _opt(argv, name, default=None):
    if name not in argv:
        return default
    i = argv.index(name) + 1
    if i >= len(argv):
        raise SystemExit(f"{name} needs a value")
    return argv[i]


def main(argv):
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--check" in argv:
        return 0 if check() else 1
    path = _opt(argv, "--db") or None
    # `--path` is the JSON; `--db` would look like hands.db and this
    # file is not that.
    if "--path" in argv:
        path = _opt(argv, "--path")
    try:
        if "--list" in argv:
            show_list(path=path)
            return 0
        if "--create" in argv:
            create(
                _opt(argv, "--create"),
                _opt(argv, "--player"),
                _opt(argv, "--site") or _opt(argv, "--room"),
                single="--single" in argv,
                path=path)
            print("created", _opt(argv, "--create"))
            return 0
        if "--add" in argv:
            add(_opt(argv, "--add"),
                _opt(argv, "--player"),
                _opt(argv, "--site") or _opt(argv, "--room"),
                path=path)
            print("added to", _opt(argv, "--add"))
            return 0
        if "--forget" in argv:
            forget(_opt(argv, "--forget"), path=path)
            print("forgot", _opt(argv, "--forget"))
            return 0
        if "--remove" in argv:
            remove_member(
                _opt(argv, "--remove"),
                _opt(argv, "--player"),
                _opt(argv, "--site") or _opt(argv, "--room"),
                path=path)
            print("removed a member of", _opt(argv, "--remove"))
            return 0
        if "--import" in argv:
            incoming = import_csv(_opt(argv, "--import"), path=path)
            print(f"imported {len(incoming)} alias"
                  f"{'' if len(incoming) == 1 else 'es'}")
            return 0
        if "--export" in argv:
            dest = _opt(argv, "--export")
            write_csv(load(path), dest)
            print("wrote", dest)
            return 0
    except ValueError as e:
        raise SystemExit(str(e))
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
