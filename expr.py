"""
Expressions over plain stats. Cohort first; no nested expressions.

A plain stat is a frequency in a spot -- `stats.BY_KEY`, the same
registry `--show` and `--quick` already use. An expression is arithmetic
and logic over those frequencies, not a second filter language and not
a graph. `Value(3Bet) < 2 and Opps(3Bet) > 100` is "this person's 3-bet
is under two percent, and they have been in that spot more than a
hundred times". That is a person filter, which is why the first caller
is `--cohort`.

    Value(S)   100 * hits / opps  (0 when they never faced the chance)
    Cases(S)   hits
    Opps(S)    opportunities
    Hands()    the players-table hand count (HandsCount is the same word)

S is a plain-stat name, resolved into `stats.BY_KEY`. `3Bet` is
`threebet`, `cbet` is `cbet_flop`, `WentToSD` is `wtsd`. There is no
`WentToSDCases` as its own catalog entry -- that is `Cases(wtsd)`, and
the denominator is `Opps(wtsd)` (hands that saw a flop), not `Hands()`.

Not this, on purpose: nested `Value(Value(...))`; H2N's full stat-name
catalog and positional `[MP;IP]` suffixes; `VsHeroCases`, `AmountWon`,
`ActionProfit` as expression atoms (those are report columns, and
per-player they are not cheap). `eval()` is not used -- the grammar is
a recursive descent over an allowlist.

    python expr.py --check
"""

import re
import sqlite3
import sys

import stats

# Group key that keeps site with the name. `rates_by_player` drops the
# site, and two rooms can share a screen name -- the failure `fmt='RING'`
# already taught, one column over.
SEP = "\x1f"

# Casual / H2N-ish spellings that are not the registry key even after
# hyphens and underscores are stripped. Everything else is a compact
# form of a `stats.BY_KEY` entry (`cbetflop` → `cbet_flop`).
NAME_ALIASES = {
    "3bet": "threebet",
    "cbet": "cbet_flop",
    "donk": "donk_flop",
    "wenttosd": "wtsd",
    "wenttoshowdown": "wtsd",
    "wtsdcases": "wtsd",
    "wenttosdcases": "wtsd",
}

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_?\-]*|[0-9]+[A-Za-z_][A-Za-z0-9_?\-]*")
_FUNCS = {"value", "cases", "opps", "hands", "handscount", "if"}
_CMP = {"<", "<=", ">", ">=", "=", "==", "!=", "<>"}


def is_expression(text):
    """True when the string is math/logic, not the compact field list."""
    t = str(text).lower()
    if re.search(r"\b(value|cases|opps|hands|handscount)\s*\(", t):
        return True
    if re.search(r"\bif\s*\(", t):
        return True
    if re.search(r"\b(and|or)\b", t):
        return True
    return False


def resolve_stat(name):
    """A typed name → the Stat in the registry. Unknown is an error."""
    raw = str(name).strip()
    if not raw or not re.fullmatch(r"[A-Za-z0-9_?\-]{1,40}", raw):
        raise ValueError(
            f"invalid stat name {name!r} -- letters, digits, _ and -")
    compact = raw.lower().replace("-", "").replace("_", "").replace(" ", "")
    if compact in ("hands", "handscount"):
        raise ValueError(
            "Hands() is a function, not a stat -- write Hands() or "
            "HandsCount(), not Value(Hands)")
    key = NAME_ALIASES.get(compact)
    if key is None:
        by_compact = {s.key.replace("_", ""): s.key for s in stats.STATS}
        key = by_compact.get(compact)
    if key is None:
        lowered = raw.lower().replace("-", "_")
        if lowered in stats.BY_KEY:
            key = lowered
    if key is None or key not in stats.BY_KEY:
        raise ValueError(
            f"unknown stat {name!r} -- a key from stats.BY_KEY "
            f"(vpip, pfr, threebet, wtsd, cbet_flop, ...) or a short "
            f"alias (3Bet, cbet, WentToSD). Not an H2N catalog name "
            f"and not a positional [MP;IP] suffix")
    return stats.BY_KEY[key]


def _lex(text):
    s = str(text)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if s.startswith("<=", i) or s.startswith(">=", i) \
                or s.startswith("!=", i) or s.startswith("==", i) \
                or s.startswith("<>", i):
            yield s[i:i + 2]
            i += 2
            continue
        if c in "<>()+-*/,=":
            yield c
            i += 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j < n and s[j] == "." and j + 1 < n and s[j + 1].isdigit():
                j += 1
                while j < n and s[j].isdigit():
                    j += 1
            # `3Bet` is a name, `3` and `3.5` are numbers. Looking only
            # at the leading digits would make Value(3Bet) a syntax error
            # and look like the parser does not know the example.
            if j < n and (s[j].isalpha() or s[j] in "_?-"):
                while j < n and (s[j].isalnum() or s[j] in "_?-"):
                    j += 1
                yield s[i:j]
            else:
                yield float(s[i:j])
            i = j
            continue
        m = _IDENT.match(s, i)
        if m:
            yield m.group(0)
            i = m.end()
            continue
        raise ValueError(f"unexpected {c!r} in expression")
    yield None


class _Parser:
    def __init__(self, text):
        self.tokens = list(_lex(text))
        self.i = 0

    def peek(self):
        return self.tokens[self.i]

    def take(self, wanted=None):
        tok = self.tokens[self.i]
        if wanted is not None and tok != wanted:
            raise ValueError(f"expected {wanted!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self):
        tree = self.parse_or()
        if self.peek() is not None:
            raise ValueError(f"unexpected {self.peek()!r} after expression")
        return tree

    def parse_or(self):
        left = self.parse_and()
        while _word(self.peek()) == "or":
            self.take()
            left = ("or", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while _word(self.peek()) == "and":
            self.take()
            left = ("and", left, self.parse_cmp())
        return left

    def parse_cmp(self):
        left = self.parse_add()
        tok = self.peek()
        if tok in _CMP:
            self.take()
            return ("cmp", tok, left, self.parse_add())
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.peek() in ("+", "-"):
            op = self.take()
            left = ("add", op, left, self.parse_mul())
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            left = ("mul", op, left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.peek() == "-":
            self.take()
            return ("neg", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        if isinstance(tok, float):
            self.take()
            return ("num", tok)
        if tok == "(":
            self.take()
            tree = self.parse_or()
            self.take(")")
            return tree
        if isinstance(tok, str) and _IDENT.fullmatch(tok):
            name = self.take()
            if self.peek() == "(":
                return self.parse_call(name)
            # A bare name in `vpip>=40 and pfr<=10` is Value(name),
            # so the compact idea still works once `and` is in the
            # string. A name that is not a stat fails here, not later.
            return ("value", resolve_stat(name).key)
        raise ValueError(f"expected a number, a name, or '(', got {tok!r}")

    def parse_call(self, name):
        kind = name.lower()
        if kind not in _FUNCS:
            raise ValueError(
                f"unknown function {name!r} -- Value, Cases, Opps, "
                f"Hands / HandsCount, or if")
        self.take("(")
        if kind in ("hands", "handscount"):
            if self.peek() != ")":
                raise ValueError("Hands() takes no argument")
            self.take(")")
            return ("hands",)
        if kind == "if":
            cond = self.parse_or()
            self.take(",")
            a = self.parse_or()
            self.take(",")
            b = self.parse_or()
            self.take(")")
            return ("if", cond, a, b)
        # Value/Cases/Opps take a stat name, never an expression --
        # that is the "no nesting" rule, and Value(Value(vpip)) has
        # to fail here rather than silently become Value(vpip).
        arg = self.peek()
        if not isinstance(arg, str) or arg in _CMP or arg in (
                "+", "-", "*", "/", ",", ")", "(") or isinstance(arg, float):
            raise ValueError(
                f"{name} takes a stat name, not an expression")
        if arg.lower() in _FUNCS and self.tokens[self.i + 1] == "(":
            raise ValueError(
                f"{name} takes a stat name, not an expression -- "
                "expressions do not nest")
        self.take()
        self.take(")")
        key = resolve_stat(arg).key
        return (kind, key)


def _word(tok):
    return tok.lower() if isinstance(tok, str) else tok


def parse(text):
    """Source text → tree. Raises ValueError on a bad expression."""
    if not str(text).strip():
        raise ValueError("empty expression")
    return _Parser(text).parse()


def collect_stats(tree, out=None):
    """The plain-stat keys the tree will ask `rates_by` for."""
    out = set() if out is None else out
    if not isinstance(tree, tuple) or not tree:
        return out
    head = tree[0]
    if head in ("value", "cases", "opps"):
        out.add(tree[1])
    else:
        for child in tree[1:]:
            if isinstance(child, tuple):
                collect_stats(child, out)
    return out


def evaluate(tree, env):
    """tree + {stat: (n, k), 'hands': N} → a number. Comparisons are 0/1."""
    head = tree[0]
    if head == "num":
        return float(tree[1])
    if head == "hands":
        return float(env.get("hands") or 0)
    if head in ("value", "cases", "opps"):
        n, k = env.get(tree[1], (0, 0))
        n, k = n or 0, k or 0
        if head == "cases":
            return float(k)
        if head == "opps":
            return float(n)
        return (100.0 * k / n) if n else 0.0
    if head == "neg":
        return -evaluate(tree[1], env)
    if head == "add":
        a, b = evaluate(tree[2], env), evaluate(tree[3], env)
        return a + b if tree[1] == "+" else a - b
    if head == "mul":
        a, b = evaluate(tree[2], env), evaluate(tree[3], env)
        if tree[1] == "*":
            return a * b
        return (a / b) if b else 0.0
    if head == "cmp":
        a, b = evaluate(tree[2], env), evaluate(tree[3], env)
        op = tree[1]
        if op in ("=", "=="):
            ok = a == b
        elif op in ("!=", "<>"):
            ok = a != b
        elif op == "<":
            ok = a < b
        elif op == "<=":
            ok = a <= b
        elif op == ">":
            ok = a > b
        else:
            ok = a >= b
        return 1.0 if ok else 0.0
    if head == "and":
        return 1.0 if evaluate(tree[1], env) and evaluate(tree[2], env) else 0.0
    if head == "or":
        return 1.0 if evaluate(tree[1], env) or evaluate(tree[2], env) else 0.0
    if head == "if":
        return evaluate(tree[2], env) if evaluate(tree[1], env) \
            else evaluate(tree[3], env)
    raise ValueError(f"unknown node {head!r}")


def _row_bits(row):
    if isinstance(row, sqlite3.Row) or hasattr(row, "keys"):
        return row["site"], row["player"], row["hands"]
    return row[0], row[1], row[3]


def _rates_by_identity(con, key):
    raw = stats.rates_by(con, key, "site || char(31) || player")
    return raw


def filter_players(con, rows, text):
    """Keep the player rows for which the expression is true."""
    tree = parse(text) if isinstance(text, str) else text
    keys = collect_stats(tree)
    cache = {key: _rates_by_identity(con, key) for key in keys}
    out = []
    for row in rows:
        site, player, hands = _row_bits(row)
        ident = f"{site}{SEP}{player}"
        env = {"hands": hands or 0}
        for key in keys:
            env[key] = cache[key].get(ident, (0, 0))
        if evaluate(tree, env):
            out.append(row)
    return out


def check():
    """Parse, eval, and a memory filter. Never opens hands.db."""
    fails = []

    def tree_of(text):
        try:
            return parse(text)
        except ValueError as e:
            fails.append(f"parse {text!r}: {e}")
            return None

    t = tree_of("Value(3Bet) < 2 and Opps(3Bet) > 100")
    if t is not None:
        env = {"threebet": (200, 3), "hands": 400}
        if not evaluate(t, env):
            fails.append("Value(3Bet)<2 and Opps>100 was false for 1.5% / 200")
        env_hi = {"threebet": (200, 10), "hands": 400}
        if evaluate(t, env_hi):
            fails.append("Value(3Bet)<2 kept a 5% 3-bet")
        env_few = {"threebet": (50, 0), "hands": 80}
        if evaluate(t, env_few):
            fails.append("Opps(3Bet)>100 kept a 50-opp player")

    t = tree_of("Value(wtsd) > 30 and Opps(wtsd) > 50")
    if t is not None:
        if not evaluate(t, {"wtsd": (80, 32)}):
            fails.append("WTSD 40% on 80 flop-saws should pass")
        if evaluate(t, {"wtsd": (80, 16)}):
            fails.append("WTSD 20% should fail Value>30")

    t = tree_of("vpip>=40 and pfr<=10")
    if t is not None:
        if not evaluate(t, {"vpip": (100, 45), "pfr": (100, 8)}):
            fails.append("bare vpip>=40 and pfr<=10 should be Value()")
        if evaluate(t, {"vpip": (100, 22), "pfr": (100, 18)}):
            fails.append("tight regular passed a fish expression")

    t = tree_of("Hands() > 100")
    if t is not None and not evaluate(t, {"hands": 200}):
        fails.append("Hands() did not read the players-table count")
    t = tree_of("HandsCount() >= 100")
    if t is not None and not evaluate(t, {"hands": 100}):
        fails.append("HandsCount() is not an alias of Hands()")

    t = tree_of("if(Opps(vpip)>100, Value(vpip), 0)")
    if t is not None:
        if evaluate(t, {"vpip": (40, 20)}) != 0:
            fails.append("if() did not take the else when Opps is short")
        if abs(evaluate(t, {"vpip": (200, 80)}) - 40.0) > 1e-9:
            fails.append("if() did not return Value(vpip) when Opps is enough")

    t = tree_of("Cases(threebet) / Opps(threebet) * 100")
    if t is not None:
        got = evaluate(t, {"threebet": (50, 5)})
        if abs(got - 10.0) > 1e-9:
            fails.append(f"arithmetic Cases/Opps*100 was {got}, not 10")
    if abs(evaluate(parse("1 / 0"), {}) - 0.0) > 1e-9:
        fails.append("division by zero was not 0")
    if evaluate(parse("Value(vpip)"), {"vpip": (0, 0)}) != 0:
        fails.append("Value of a never-faced chance was not 0")

    refused = 0
    for bad in ("Value(Value(vpip))", "Value(vpip + 1)",
                "Nope(vpip)", "Value(notastat)",
                "Value(vpip'); DROP TABLE players;--)",
                "Hands(vpip)", ""):
        try:
            parse(bad)
        except ValueError:
            refused += 1
        else:
            fails.append(f"{bad!r} was accepted")
    if refused < 6:
        fails.append("an expression that should have been refused was not")

    # Identity grouping: two rooms, one screen name, different 3-bets.
    # Grouping by player alone would average them and keep the wrong one.
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE players ("
        "site TEXT, player TEXT, durable INT, hands INT, "
        "vpip REAL, pfr REAL, threebet REAL, fold_to_threebet REAL, "
        "wwsf REAL, wtsd REAL, wsd REAL, bb100 REAL, class TEXT)")
    con.executemany(
        "INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("acr", "same", 1, 300, 20, 18, None, None, None, None, None, 0, "reg"),
         ("pokerstars", "same", 1, 300, 20, 18, None, None, None, None, None, 0, "reg"),
         ("acr", "lag", 1, 200, 40, 30, None, None, None, None, None, 0, "unknown")])
    con.execute(
        "CREATE TABLE decisions ("
        "hand_id TEXT, seat INT, street TEXT, facing TEXT, agg INT, "
        "action TEXT, site TEXT, player TEXT)")
    con.execute(
        "CREATE TABLE spots ("
        "hand_id TEXT, seat INT, saw_flop INT, wtsd INT, "
        "site TEXT, player TEXT)")
    # acr/same: 1/120 3-bets (0.83%); stars/same: 20/120 (16.7%);
    # lag: 15/80 (18.75%). The expression keeps only acr/same.
    rows = []
    for i in range(120):
        rows.append((f"a{i}", 1, "preflop", "open", 1 if i == 0 else 0,
                     "R" if i == 0 else "F", "acr", "same"))
        rows.append((f"s{i}", 1, "preflop", "open", 1 if i < 20 else 0,
                     "R" if i < 20 else "F", "pokerstars", "same"))
    for i in range(80):
        rows.append((f"l{i}", 1, "preflop", "open", 1 if i < 15 else 0,
                     "R" if i < 15 else "F", "acr", "lag"))
    con.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", rows)
    people = con.execute("SELECT * FROM players").fetchall()
    kept = filter_players(
        con, people, "Value(3Bet) < 2 and Opps(3Bet) > 100")
    names = {(r["site"], r["player"]) for r in kept}
    if names != {("acr", "same")}:
        fails.append(
            f"filter_players kept {names}, not just acr/same "
            "(grouping by player without site would merge the two rooms)")

    # WTSD uses spots, and Opps is saw_flop, not Hands().
    con.executemany(
        "INSERT INTO spots VALUES (?,?,?,?,?,?)",
        [(f"w{i}", 1, 1, 1 if i < 40 else 0, "acr", "same")
         for i in range(80)]
        + [(f"x{i}", 1, 1, 1 if i < 10 else 0, "acr", "lag")
           for i in range(40)])
    kept = filter_players(
        con, people, "Value(wtsd) > 30 and Opps(wtsd) > 50")
    names = {(r["site"], r["player"]) for r in kept}
    if names != {("acr", "same")}:
        fails.append(f"WTSD expression kept {names}, not acr/same")

    con.close()
    print(f"expressions over plain stats  "
          f"{'yes' if not fails else 'NO'}")
    for f in fails:
        print(f"    {f}")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
