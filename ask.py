"""
Ask the database a question in English, and get the engine's answer.

    python ask.py "how often does the pool fold to a river bet in 3bet pots,
                   SB vs BTN, when the flop and turn were both bet?"

The assistant does not know any poker numbers. It knows the program's
vocabulary -- every flag `query.py` takes, generated from `query.py` itself
so it can never drift -- and it has one tool: run a query. It turns the
sentence into flags, the program runs them exactly as the command line
would, and the assistant reads the result back with the n and the interval
that came with it. A number it did not get from the tool is a number it
was told not to say, and the command it ran is printed under every answer
so the answer can be re-run by hand and checked.

That is what puts this past a tracker's own filter dialog: the question
above is nine flags in the right order, and the dialog has no box for
"and the flop and turn were both bet". The line filters do, and the
assistant knows them.

WHO ANSWERS. Whichever AI has a key: Gemini, Claude, ChatGPT or Grok,
each spoken to over plain HTTPS in its own dialect, all given the same
one tool. Gemini is the default because its API has a free tier and the
others' do not, so the panel works without paying anybody; when that
runs out, a key for any of the four goes in `ai.json` beside the program
(the panel's settings box writes it) or in the usual environment
variable. Standard library only, like the rest of this program: the
packaged window cannot carry a third-party SDK and `build.py --check`
would refuse one. With no key at all, the Claude Code command-line tool
is tried last, on a Claude subscription, if it is signed in.

WHAT IT MAY RUN. Anything that reads. The options that write --
`--export`, `--save`, `--define`, `--forget`, `--out` -- are refused by the
tool itself, not by the prompt, so a misunderstanding cannot write a file.

THE DESKTOP APP. The Claude Desktop app can use a program on the
person's own computer through the Model Context Protocol, and `--mcp`
serves exactly that: the same one tool, the same refusals, over stdin and
stdout. `--install-desktop` writes the line into the app's config that
tells it TraceEV is here. After that a person on a Claude subscription
opens the app and says "find every spot the pool overfolds", and the app
runs as many queries as that takes, on their machine, with no key at all.
That route has no call budget, which is the right thing for a question
that is really twenty questions.

    python ask.py "<question>"             one question, answer on the terminal
    python ask.py --with grok "<q>"        a particular AI: gemini, claude, openai, grok, claude-cli
    python ask.py --providers              who has a key right now
    python ask.py --vocabulary             what the assistant is told the program can do
    python ask.py --mcp                    serve the tool to the Claude Desktop app (it runs this)
    python ask.py --install-desktop        tell the Claude Desktop app where TraceEV is
    python ask.py --check                  the executor and the vocabulary, no network
    python ask.py --score [--with P] [--limit N]   the golden questions, marked against the answer
"""

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import query
import stats

# Options that write something. Refused in `run_query` itself, so that no
# reading of the question, however wrong, can produce a file.
WRITES = ("--export", "--save", "--define", "--forget", "--out")
# Queries per question over an API. "Where does the pool overfold" is a
# dozen queries, one per spot, and cutting it off at four gave a third of
# an answer that read like a whole one.
MAX_CALLS = 12


def vocabulary():
    """
    What the program can be asked, in the program's own words.

    Built from `query.py`'s tables at call time rather than written out
    here, so a flag added there is known here the same day. The examples
    are the ones a person actually types.
    """
    lines = ["# TraceEV query vocabulary", "",
             "Every question becomes `python query.py <flags> <mode>`. Flags "
             "combine with AND. Modes:", "",
             "  --stats     every stat under the filter, each with n and a 95% interval (default)",
             "  --results   the money in the hands the filter selects: bb/100 with its error",
             "  --hands     the hands themselves",
             "  --range     what hands the players held there (with the seen-fraction)",
             "  --chart     the 13x13 chart of that",
             "  --sessions  which sittings",
             "  --versus \"<flags>\"  A against B: both rates, the interval on the DIFFERENCE,",
             "              a p-value corrected for how many stats were compared, and -- when",
             "              nothing is real -- the smallest difference this much data could see.",
             "              Add --show KEY so ONE stat is tested and not thirty.",
             "  --by DIM    split any of the above into a table by one dimension:",
             "              " + ", ".join(query.DIMENSIONS),
             "  --show KEYS which stat columns to show (comma-separated keys from below)",
             "  --min N     hide rows with fewer than N chances", "",
             "## switches (no value)"]
    for k, v in query.SWITCHES.items():
        lines.append(f"  {k:16} {v}")
    lines += ["", "## flags taking a value (comma-separate lists)"]
    for k, v in query.VALUE_FLAGS.items():
        if v:
            lines.append(f"  {k:16} {v}")
    lines += [
        "",
        "  --board TEX      flop texture, one of: " + ", ".join(query.BOARDS),
        "  --turn-card X    what the turn did: " + ", ".join(query.RUNOUT),
        "  --river-card X   what the river did: the same words",
        "  --quick NAME     a named filter: " + ", ".join(sorted(query.quick_by_key())),
        "",
        "## the betting, as a string per street -- these are the ones a dialog cannot ask",
        "  --pre P  --flop P  --turn P  --river P   GLOB over the street's WHOLE action",
        "  string, letters in acting order: X check, B bet, C call, R raise, F fold.",
        "  A plain string is the entire street, so --flop B means 'bet, and everyone",
        "  folded'; a bet that was called is BC; checked to the bettor who bet and was",
        "  called is XBC. Use a wildcard to mean 'and then anything': --flop \"XB*\".",
        "  --line 'RC/XBC/*'  the whole hand, streets separated by /, GLOB.",
        "  --node 'RC/X*'     the tree node -- the betting so far when somebody had to act.",
        "  \"B-B-B\" / 'barrelled three streets' means bet and called on the flop and the",
        "  turn, then bet on the river: --flop BC --turn BC, and the river is the street",
        "  being asked about (--street river --facing bet for the player facing it).",
        "  Who is asked matters: 'the pool folds to a river bet' is the CALLER's decision,",
        "  so --pos is the caller's seat and --vs the bettor's.",
        "",
        "## positions, pots, facing",
        "  positions: " + ", ".join(query.POSITIONS),
        "  --pot: " + ", ".join(query.POT_TYPES) + "   (3bet = a 3bet pot)",
        "  --facing: " + ", ".join(query.FACINGS) + "   (what the player is looking at when they act)",
        "  --street: preflop, flop, turn, river   --pos: the player's seat   --vs: the opponent's seat (heads-up only)",
        "  --opener / --raiser: who opened / who was the last aggressor preflop",
        "",
        "## stats (keys for --show), each 'chance' -> 'action'",
    ]
    for st in stats.STATS:
        lines.append(f"  {st.key:18} {st.label}: {st.chance}  ->  {st.action}")
    lines += [
        "",
        "## sites: " + ", ".join(__import__("sites").KEYS),
        "  Always add --site when the question is about the pool: three sites are three",
        "  different games and a pool with no site averages them.",
        "  Which site: pokerstars for how often people DO things (the biggest sample).",
        "  But for what people HOLD -- --range, --chart, or any --made/--kicker/--fd/--sd",
        "  filter -- use ignition, the one site that writes every hand's cards including",
        "  the folds. PokerStars and ACR show only the hands that reached showdown, which",
        "  is the strong quarter of a range presented as the range.",
        "  'the pool' = --pool (everyone but me). 'me'/'I'/'hero' = --hero.",
        "",
        "## overfolds, and what was done in a spot",
        "  'where does the pool overfold' / 'fold too much' is ONE query, --overfolds:",
        "    --pool --site pokerstars --overfolds          (add --by position, --pot 3bet, etc.)",
        "  The tool sets the bar from the bet size -- a bet of B into P profits on its",
        "  own past a fold rate of B/(P+B) -- and says REAL only when the interval clears",
        "  it. Quote its verdict; never call a fold rate an overfold from the number.",
        "  'what happens when I bet here' / 'action profit' / 'what do they do next' is",
        "  --actions on the spot's filter; --by size splits it by bet size, --by hand by",
        "  what was held.",
        "",
        "## comparisons",
        "  Any question of the form 'is X different when Y', 'more than', 'compared to',",
        "  'better in A than B' is ONE query with --versus, never two queries eyeballed:",
        "    --pool --site pokerstars --pot 3bet --street turn --facing bet --show fold_to_turn_bet",
        "        --versus \"--pool --site pokerstars --pot raised --street turn --facing bet\"",
        "  The tool decides. Its last lines say either 'Real: ...' or 'Nothing survives',",
        "  and the second comes with the smallest difference the sample could have seen.",
        "",
        "## worked examples",
        "  how often does the pool fold to a river bet in 3bet pots, SB vs BTN, B-B-B (SB",
        "  3bet and barrelled, BTN called twice and now faces the river bet):",
        "    --pool --site pokerstars --pot 3bet --pos BTN --vs SB --flop BC --turn BC --street river --facing bet --show fold_to_river_bet",
        "  my cbet on monotone flops in single-raised pots, by position:",
        "    --hero --pot raised --street flop --board mono --show cbet_flop --by position",
        "  how do I do with a fish on my right:",
        "    --hero --fish-right --results",
        "  what does the pool 3bet with from the blinds (Ignition, since it shows every hand):",
        "    --pool --site ignition --street preflop --facing open --pos SB,BB --aggressive --range",
    ]
    return "\n".join(lines)


SYSTEM = """You are TraceEV's assistant. TraceEV is a poker tracker over the user's own hand histories (PokerStars, ACR, Ignition). You answer questions about what the data says by running queries with the run_query tool, and ONLY from what the tool returns.

Rules that matter:
- Every number you state must appear in a tool result. Never estimate, never recall poker theory as if it were this data. If the tool returned nothing, say so.
- Always report the n beside a rate, and the interval when the tool printed one. If n is small say the number is not worth much yet.
- Translate the question into the vocabulary below. Prefer the specific stat (--show KEY) over reading it off a table. For "how often does X do Y facing Z" use --street, --facing and the stat key. For money, use --results.
- Pool questions need --pool and a --site. If the user did not name a site, use pokerstars (the biggest sample) and SAY you did -- EXCEPT for anything about what hands people hold (--range, --chart, --made, --fd, --sd), which must use ignition, the only site that shows every hand's cards. Say that too.
- Use as many tool calls as the question needs. "Where does the pool overfold" is --overfolds (the tool's own bar, from the bet size); "what happens when I bet here" is --actions. If a query errors, read the message and fix the flags once.
- A comparison is ONE query with --versus and --show KEY. Report a difference as real ONLY if the tool's own verdict line says "Real:". If it says "Nothing survives", say there is no difference this data can see, and quote the "has to be about N points" line -- that is the finding. Never decide significance yourself from two rates, two intervals, or two separate queries; the tool corrects for how many questions were asked and you cannot.
- End with one line: `ran: python query.py <the flags you used>` so the user can repeat it.
- Be brief. A sentence or two and the numbers.

""" + "{vocabulary}"

TOOL = {
    "name": "run_query",
    "description": "Run TraceEV's query.py with these command-line flags and return its "
                   "output. Read-only. Use the vocabulary in the system prompt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"},
                     "description": "the flags, one list item per token, e.g. "
                                    "[\"--pool\", \"--site\", \"pokerstars\", \"--pot\", \"3bet\", \"--results\"]"},
        },
        "required": ["argv"],
        "additionalProperties": False,
    },
    "strict": True,
}


def run_query(argv):
    """
    query.py, exactly as the command line runs it, output captured.

    Nothing here reinterprets the flags: `query.main` is what the terminal
    runs, so an answer here is the answer there. Writing options are
    refused before anything runs.
    """
    argv = [str(a) for a in argv]
    bad = [a for a in argv if a in WRITES]
    if bad:
        return f"refused: {bad[0]} writes a file, and this tool only reads"
    if not argv:
        return "refused: no flags given"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            query.main(argv)
    except SystemExit as e:
        return f"query.py refused these flags: {e}\n(see the vocabulary; fix and try once more)"
    except Exception as e:                      # a bug, said rather than hidden
        return f"query.py failed: {type(e).__name__}: {e}"
    text = out.getvalue().strip()
    if len(text) > 12000:
        text = text[:12000] + "\n... (cut here; narrow the question or use --min)"
    return text or "(no output)"


# -------------------------------------------------------------- providers
#
# Four AIs, one tool. Each speaks its own dialect over HTTPS, and every
# one of them can call a function, which is all this needs: the assistant
# is handed `run_query`, calls it, reads the result, answers. The dialects
# are translated in the three small classes below and nowhere else, so
# the loop that asks is written once.
#
# Gemini is the default because its API has a free tier and the others'
# do not: a person can open the panel and ask without paying anybody. A
# key for any of the four goes in `ai.json` beside the program (theirs,
# gitignored; the panel's settings box writes it) or in the environment
# variable named here. The model is overridable in the same file, since
# these names will be superseded and nobody should have to wait for a
# release to use the new one.
PROVIDERS = {
    "gemini": {
        "label": "Gemini (Google) -- has a free tier",
        "env": "GEMINI_API_KEY", "model": "gemini-flash-latest",
        "keys_at": "https://aistudio.google.com/apikey",
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "env": "ANTHROPIC_API_KEY", "model": "claude-opus-5",
        "keys_at": "https://console.anthropic.com/settings/keys",
        # Where the key lived before there was a settings file.
        "file": Path.home() / "Desktop" / "claude apikey.txt",
    },
    "openai": {
        "label": "ChatGPT (OpenAI)",
        "env": "OPENAI_API_KEY", "model": "gpt-5-mini",
        "keys_at": "https://platform.openai.com/api-keys",
    },
    "grok": {
        "label": "Grok (xAI)",
        "env": "XAI_API_KEY", "model": "grok-4",
        "keys_at": "https://console.x.ai",
    },
    # Qwen speaks OpenAI's protocol at Alibaba's endpoint, and locally
    # through Ollama, which needs no key at all -- the one way to run the
    # assistant with nothing leaving the machine. The local entry's "key"
    # is a placeholder so the provider loop treats it as configured.
    "qwen": {
        "label": "Qwen (Alibaba Cloud)",
        "env": "DASHSCOPE_API_KEY", "model": "qwen-plus",
        "keys_at": "https://modelstudio.console.alibabacloud.com/?tab=model#/api-key",
        "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    },
    "ollama": {
        "label": "Ollama, local (Qwen or any model; no key)",
        "env": "OLLAMA_MODEL", "model": "qwen3",
        "keys_at": "https://ollama.com/download -- then: ollama pull qwen3",
        "url": "http://localhost:11434/v1/chat/completions",
        "keyless": True,
    },
}
# Not an API: the Claude Code command-line tool on a Claude subscription,
# which needs no key, only a sign-in. Tried last when nothing else can.
CLI = "claude-cli"
CLI_LABEL = "Claude Code (subscription sign-in, no key)"
SETTINGS = Path(__file__).parent / "ai.json"


def settings():
    """{"provider": ..., "keys": {name: key}, "models": {name: model}}."""
    try:
        got = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        got = {}
    got.setdefault("provider", "gemini")
    got.setdefault("keys", {})
    got.setdefault("models", {})
    return got


def save_settings(provider=None, key=None, model=None):
    """Write what was given; an empty key or model forgets the stored one."""
    got = settings()
    if provider:
        got["provider"] = provider
    if key is not None and provider in PROVIDERS:
        if key.strip():
            got["keys"][provider] = key.strip()
        else:
            got["keys"].pop(provider, None)
    if model is not None and provider in PROVIDERS:
        if model.strip():
            got["models"][provider] = model.strip()
        else:
            got["models"].pop(provider, None)
    SETTINGS.write_text(json.dumps(got, indent=1), encoding="utf-8")
    return got


def key_for(name):
    """A provider's key: the settings file, the environment, or its old file."""
    p = PROVIDERS[name]
    key = (settings()["keys"].get(name) or os.environ.get(p["env"], "")).strip()
    if not key and p.get("file") and p["file"].exists():
        # utf-8-sig: a key pasted into Notepad arrives with a byte-order
        # mark, and a BOM on the front of a key is an authentication error
        # that says nothing about a BOM.
        key = p["file"].read_text(encoding="utf-8-sig").strip()
    return key


def model_for(name):
    return settings()["models"].get(name) or PROVIDERS[name]["model"]


def available():
    """The providers with a key right now."""
    return [n for n in PROVIDERS if key_for(n)]


# Answers that mean "not right now" rather than "no": the service is busy
# or the key is rate-limited. Worth a short wait and one more try before
# it counts as a failure, since the free tiers say this often and mean it
# for seconds.
BUSY = (429, 503, 529)


def _post(url, body, headers):
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            try:
                j = json.loads(detail)
                if isinstance(j, list) and j:
                    j = j[0]
                detail = (j.get("error") or {}).get("message") or detail
            except (ValueError, AttributeError):
                pass
            if e.code in BUSY and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"{e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"could not reach the service: {e.reason}") from None


# The tool's parameters in the plain JSON-schema form the other three take;
# Claude's copy above carries `strict` and `additionalProperties` as well.
PARAMS = {"type": "object",
          "properties": {"argv": {"type": "array", "items": {"type": "string"},
                                  "description": TOOL["input_schema"]["properties"]["argv"]["description"]}},
          "required": ["argv"]}


class Claude:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, key, model):
        self.key, self.model = key, model

    def user(self, text):
        return {"role": "user", "content": text}

    def turn(self, system, messages):
        """One round: the model's reply is appended; returns (calls, text)."""
        body = {"model": self.model, "max_tokens": 4000,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "medium"},
                # The vocabulary is most of the request and the same every
                # time, so it is marked cacheable.
                "system": [{"type": "text", "text": system,
                            "cache_control": {"type": "ephemeral"}}],
                "tools": [TOOL], "messages": messages}
        resp = _post(self.URL, body, {"content-type": "application/json",
                                      "x-api-key": self.key,
                                      "anthropic-version": "2023-06-01"})
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        calls = [(b["id"], (b.get("input") or {}).get("argv") or [])
                 for b in content if b.get("type") == "tool_use"]
        text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        return calls, text.strip()

    def results(self, messages, results):
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": out}
            for cid, out in results]})


class Gemini:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

    def __init__(self, key, model):
        self.key, self.model = key, model

    def user(self, text):
        return {"role": "user", "parts": [{"text": text}]}

    def turn(self, system, messages):
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "tools": [{"functionDeclarations": [
                    {"name": TOOL["name"], "description": TOOL["description"],
                     "parameters": PARAMS}]}],
                "contents": messages}
        # The key goes in a header, never the URL: a URL is logged by
        # every proxy on the way, a header is not.
        resp = _post(self.URL.format(self.model), body,
                     {"content-type": "application/json", "x-goog-api-key": self.key})
        cands = resp.get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        messages.append({"role": "model", "parts": parts})
        calls = [(p["functionCall"].get("name", TOOL["name"]),
                  (p["functionCall"].get("args") or {}).get("argv") or [])
                 for p in parts if "functionCall" in p]
        text = "\n".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        return calls, text.strip()

    def results(self, messages, results):
        messages.append({"role": "user", "parts": [
            {"functionResponse": {"name": TOOL["name"], "response": {"output": out}}}
            for _cid, out in results]})


class OpenAIChat:
    """OpenAI's chat completions, which Grok speaks too."""
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, key, model, url=None):
        self.key, self.model, self.url = key, model, url or self.URL

    def user(self, text):
        return {"role": "user", "content": text}

    def turn(self, system, messages):
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": system})
        body = {"model": self.model, "messages": messages,
                "tools": [{"type": "function", "function": {
                    "name": TOOL["name"], "description": TOOL["description"],
                    "parameters": PARAMS}}]}
        resp = _post(self.url, body, {"content-type": "application/json",
                                      "authorization": "Bearer " + self.key})
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        messages.append(msg)
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append((tc["id"], args.get("argv") or []))
        return calls, (msg.get("content") or "").strip()

    def results(self, messages, results):
        for cid, out in results:
            messages.append({"role": "tool", "tool_call_id": cid, "content": out})


def client_for(name):
    key = key_for(name) or ("ollama" if PROVIDERS[name].get("keyless") else None)
    if not key:
        raise RuntimeError(f"no key for {PROVIDERS[name]['label']} -- paste one in the "
                           f"panel's settings; keys are at {PROVIDERS[name]['keys_at']}")
    model = model_for(name)
    if name == "claude":
        return Claude(key, model)
    if name == "gemini":
        return Gemini(key, model)
    if name == "openai":
        return OpenAIChat(key, model)
    if name == "grok":
        return OpenAIChat(key, model, "https://api.x.ai/v1/chat/completions")
    if PROVIDERS[name].get("url"):
        return OpenAIChat(key, model, PROVIDERS[name]["url"])
    raise RuntimeError(f"unknown provider {name!r}")


def ask_with(name, question, log=None, history=None):
    """
    One question through one provider: a tool loop, at most MAX_CALLS runs.

    The transcript comes back so a follow-up can continue it. It is in the
    provider's own dialect and is only good for that provider; `ask` keeps
    it from being handed to another.
    """
    client = client_for(name)
    system = SYSTEM.replace("{vocabulary}", vocabulary())
    messages = list(history or [])
    messages.append(client.user(question))
    ran = []
    for _ in range(MAX_CALLS + 1):
        calls, text = client.turn(system, messages)
        if not calls:
            return text or "(no answer)", ran, messages
        results = []
        for cid, argv in calls:
            argv = [str(a) for a in argv]
            if log:
                log("  running: python query.py " + " ".join(argv))
            ran.append(argv)
            results.append((cid, run_query(argv)))
        client.results(messages, results)
    return "(stopped after too many queries; ask something narrower)", ran, messages


def claude_cli():
    """The Claude Code command-line tool, if it is here."""
    found = shutil.which("claude")
    if found:
        return found
    base = Path.home() / "AppData" / "Roaming" / "Claude" / "claude-code"
    exes = list(base.glob("*/claude.exe")) if base.is_dir() else []
    if not exes:
        return None
    return str(max(exes, key=lambda p: [int(x) if x.isdigit() else x
                                        for x in p.parent.name.split(".")]))


def ask_cli(question, log=None):
    """
    The question through Claude Code on a Claude subscription: it may run
    `python query.py` itself, and nothing else, in this folder.
    """
    exe = claude_cli()
    if not exe:
        raise RuntimeError("the Claude Code command-line tool is not installed")
    prompt = (SYSTEM.replace("{vocabulary}", vocabulary())
              + "\n\nYou are running inside the TraceEV folder. To run a query, "
                "use the Bash tool with exactly `python query.py <flags>`; run "
                "nothing else. Never use " + ", ".join(WRITES) + ".\n\nQuestion: "
              + question)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json",
         "--allowedTools", "Bash(python query.py:*)"],
        input=prompt, capture_output=True, text=True, encoding="utf-8",
        cwd=str(Path(__file__).parent), env=env, timeout=600)
    try:
        env_ = json.loads((proc.stdout or "").strip())
    except ValueError:
        raise RuntimeError("the claude tool answered with something other than "
                           "JSON: " + (proc.stdout or proc.stderr or "")[:200])
    text = env_.get("result") or ""
    if env_.get("is_error"):
        if "login" in text.lower():
            raise RuntimeError("the claude command-line tool is not signed in -- "
                               "run `claude auth login` once")
        raise RuntimeError("the claude tool failed: " + text[:200])
    return text, ran_from_text(text), []


def ran_from_text(text):
    """
    The queries an answer says it ran, read back out of the prose.

    The command-line route hands back only the final answer, not the tool
    calls, so what it ran is known only because the prompt makes it print
    `ran: python query.py <flags>` at the end. Read from there. Anything
    after `python query.py` up to a backtick, a bracket or the end of the
    line is the command; the scorer needs it, and the panel's "repeat this"
    button does too.
    """
    import re
    out = []
    for m in re.finditer(r"python query\.py\s+([^`\n)]+)", text):
        try:
            argv = shlex.split(m.group(1).strip())
        except ValueError:
            continue
        if argv and argv not in out:
            out.append(argv)
    return out


# What a provider says when the problem is the account or the service and
# not the question: no key, no credit, no quota, wrong key, too busy. Any
# of these means try the next one; anything else is a bug and is raised.
TRY_NEXT = ("400", "401", "402", "403", "429", "503", "529", "credit", "quota",
            "billing", "demand", "overloaded", "no key", "not signed in",
            "not installed", "could not reach")


def who_label(name):
    """The provider's label for a person, whichever kind of provider it is."""
    return CLI_LABEL if name == CLI else PROVIDERS[name]["label"]


def ask(question, log=None, history=None, provider=None):
    """
    The answer, the queries it ran, the transcript, and who answered.

    The chosen provider first. If it has no key, or refuses for money or
    authentication, every other provider with a key is tried in turn, and
    the Claude subscription's command-line tool last; the panel tells the
    user who actually answered. A key with no credit on it is the usual
    way a provider fails, and that is not a reason to answer nothing.
    """
    chosen = provider or settings()["provider"]
    order = [chosen] + [n for n in PROVIDERS if n != chosen]
    if CLI not in order:
        order.append(CLI)
    reasons = []
    for name in order:
        try:
            if name == CLI:
                answer, ran, transcript = ask_cli(question, log)
            else:
                if not key_for(name) and name != chosen:
                    continue
                answer, ran, transcript = ask_with(
                    name, question, log, history if name == chosen else None)
            return answer, ran, transcript, name
        except RuntimeError as e:
            label = CLI_LABEL if name == CLI else PROVIDERS[name]["label"]
            reasons.append(f"{label}: {e}")
            if not any(w in str(e) for w in TRY_NEXT):
                raise
            if log:
                log(f"  ({label} -- {e})")
    raise RuntimeError("nobody could answer:\n  " + "\n  ".join(reasons)
                       + "\n  paste a key in the panel's settings -- Gemini's is free")


# ------------------------------------------------------------------- MCP
#
# JSON-RPC 2.0, one message per line, over stdin and stdout: that is the
# whole of the Model Context Protocol as a tool server needs it. Nothing
# but protocol may reach stdout -- `run_query` already captures what the
# query prints, and everything else here goes to stderr -- because one
# stray line of text on stdout is a parse error at the app's end that
# looks like the server hanging.

MCP_TOOLS = [
    {"name": TOOL["name"], "description": TOOL["description"], "inputSchema": PARAMS},
    {"name": "vocabulary",
     "description": "Every flag, stat, position and line filter TraceEV's query.py "
                    "takes, with worked examples. Read this before the first run_query.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def mcp_serve(inp=None, out=None):
    """Serve until stdin closes. `inp`/`out` are for the check."""
    inp = inp or sys.stdin
    out = out or sys.stdout
    for stream in (inp, out):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    def send(mid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": mid}
        msg["error" if error else "result"] = error or result
        out.write(json.dumps(msg) + "\n")
        out.flush()

    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            send(mid, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "traceev", "version": "1"},
                       "instructions": SYSTEM.replace("{vocabulary}", vocabulary())})
        elif method == "tools/list":
            send(mid, {"tools": MCP_TOOLS})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == TOOL["name"]:
                text = run_query(args.get("argv") or [])
            elif name == "vocabulary":
                text = vocabulary()
            else:
                send(mid, error={"code": -32602, "message": f"no tool {name!r}"})
                continue
            send(mid, {"content": [{"type": "text", "text": text}]})
        elif method == "ping":
            send(mid, {})
        elif mid is None:
            continue                     # a notification wants no reply
        else:
            send(mid, error={"code": -32601, "message": f"no method {method!r}"})


def mcp_command():
    """How the app should start the server: the packaged program or this file."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp"]
    return [sys.executable, str(Path(__file__).resolve()), "--mcp"]


DESKTOP_CONFIG = Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "claude_desktop_config.json"


def install_desktop(path=DESKTOP_CONFIG):
    """
    One entry under `mcpServers` in the Claude Desktop app's config, the
    rest of the file left exactly as it was. The app reads it when it
    starts, so it needs a restart after this.
    """
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except ValueError:
        raise RuntimeError(f"{path} is not JSON; fix or move it and try again")
    servers = cfg.setdefault("mcpServers", {})
    cmd = mcp_command()
    servers["traceev"] = {"command": cmd[0], "args": cmd[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


# ----------------------------------------------------------------- check

GOLDEN = Path(__file__).parent / "golden.json"

# The tokens that are a mode, not a filter. `query.build` ignores them,
# and the scorer has to know which one a query was in.
MODES = ("--stats", "--results", "--hands", "--range", "--chart", "--actions",
         "--overfolds",
         "--sessions")


def _parts(argv):
    """(mode, by, show, where_a, where_b) for one command line."""
    argv = [str(a) for a in argv]
    mode = next((m for m in MODES if m in argv), "--stats")
    rest = [a for a in argv if a not in MODES]

    def opt(name):
        if name not in rest:
            return None
        i = rest.index(name)
        return rest[i + 1] if i + 1 < len(rest) else None

    where_a = query.build(rest)[0]
    versus = opt("--versus")
    where_b = query.build(shlex.split(versus))[0] if versus else None
    show = tuple(sorted((opt("--show") or "").split(","))) if opt("--show") else ()
    return mode, opt("--by"), show, where_a, where_b


def same_answer(con, want, got):
    """
    Whether two command lines answer the same question.

    Not whether they are the same flags. "How often do I 3bet from the
    blinds" is `--hero --pos SB,BB --show threebet`, and it is also the same
    with `--street preflop --facing open` in front, because the stat's own
    chance already says that. Comparing flag lists would mark the second
    wrong. So the two are run against the database and it is the NUMBERS
    that must agree: the shown stat's chances and cases under each filter,
    both sides of a --versus, the grouping and the mode. That is the only
    equivalence that means anything, and it is also what the user sees.
    """
    try:
        a, b = _parts(want), _parts(got)
    except SystemExit:
        return False
    if a[0] != b[0] or (a[1] or None) != (b[1] or None):
        return False
    if (a[4] is None) != (b[4] is None):
        return False
    # `want` is the golden answer, `got` is what ran. A table that shows
    # every stat contains the one column that was wanted, and a table with
    # the wanted columns and one more contains them too; both are right
    # answers from the user's chair. The reverse -- a narrower table than
    # wanted -- is not.
    if a[2] and b[2] and not set(a[2]) <= set(b[2]):
        return False
    keys = a[2] or b[2] or tuple(st.key for st in stats.STATS
                                 if st.source != "s")
    for key in keys:
        st = stats.BY_KEY.get(key)
        if st is None or st.source == "s":
            continue
        for wa, wb in ((a[3], b[3]), (a[4], b[4])):
            if wa is None:
                continue
            na, ka = stats.rate(con, st, wa)[:2]
            nb, kb = stats.rate(con, st, wb)[:2]
            if (na, ka) != (nb, kb):
                return False
    return True


def score(provider=None, limit=None, log=print):
    """
    The golden questions through one provider, marked against the answer.

    This is the number the whole idea rests on. A translator that is right
    "most of the time" is worth exactly as much as knowing how often, and
    the demo's silent narrowing to one site is the kind of thing that only a
    scored set finds. Each question is asked cold -- no history -- and it
    counts as right if ANY query the model ran answers the question, since
    a model that breaks a question into a table and then a cell has still
    found the right cell.
    """
    import sqlite3
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    if limit:
        golden = golden[:limit]
    con = sqlite3.connect(query.DB)
    right, rows = 0, []
    start = time.time()
    for i, item in enumerate(golden, 1):
        try:
            _answer, ran, _t, who = ask(item["q"], log=None, provider=provider)
        except RuntimeError as e:
            ran, who = [], f"failed: {e}"
        wants = [item["flags"]] + item.get("alt", [])
        hit = next((r for r in ran for w in wants if same_answer(con, w, r)),
                   None)
        right += hit is not None
        rows.append((item["q"], hit is not None, ran, who))
        mark = "ok " if hit else "MISS"
        log(f"{i:3} {mark}  {item['q'][:60]}")
        if not hit:
            for r in ran[:3]:
                log("        ran: " + " ".join(r))
            if not ran:
                log(f"        ran nothing -- {who}")
            log("        wanted: " + " ".join(item["flags"]))
    took = time.time() - start
    log("")
    log(f"{right}/{len(golden)} right  ({100 * right / max(1, len(golden)):.0f}%)  "
        f"via {provider or settings()['provider']}  in {took / 60:.1f} min")
    return right, len(golden), rows


def check():
    fails = []

    # The golden set has to be a set of questions the program can answer:
    # every expected command line builds and runs. Scoring needs a
    # provider and the network, so it is `--score`, not here; but a golden
    # entry that does not run would mark every provider wrong on it, and
    # that is caught offline.
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    broken = []
    for item in golden:
        for flags in [item["flags"]] + item.get("alt", []):
            got = run_query(flags)
            if got.startswith(("refused", "query.py")):
                broken.append((item["q"], got[:80]))
    print(f"golden questions that run      {len(golden) - len(broken)}/{len(golden)}")
    for q, why in broken[:4]:
        print(f"    {q[:50]}: {why}")
    if broken:
        fails.append("golden questions that do not run")
    # And the scorer knows a right answer when it sees one, in both
    # directions: the same question phrased two ways agrees, and a
    # different question does not.
    import sqlite3
    con = sqlite3.connect(query.DB)
    agree = same_answer(con, ["--hero", "--pos", "SB,BB", "--show", "threebet"],
                        ["--hero", "--street", "preflop", "--facing", "open",
                         "--pos", "BB,SB", "--show", "threebet"])
    differ = same_answer(con, ["--hero", "--pos", "SB", "--show", "threebet"],
                         ["--hero", "--pos", "BB", "--show", "threebet"])
    print(f"the scorer tells same from different  "
          f"{'yes' if agree and not differ else 'NO'}")
    if not (agree and not differ):
        fails.append("same_answer is wrong about equivalence")
    con.close()

    v = vocabulary()
    # Every flag the program takes is in the vocabulary, and every stat.
    missing = [k for k in list(query.SWITCHES) + list(query.VALUE_FLAGS)
               if query.VALUE_FLAGS.get(k, True) is not None and k not in v]
    print(f"every flag in the vocabulary   {'yes' if not missing else 'NO: ' + str(missing[:4])}")
    if missing:
        fails.append("flags missing from the vocabulary")
    absent = [s.key for s in stats.STATS if s.key not in v]
    if absent:
        fails.append(f"stats missing from the vocabulary: {absent[:4]}")

    # The executor runs the real thing and refuses to write.
    got = run_query(["--hero", "--street", "river", "--facing", "bet",
                     "--show", "fold_to_river_bet"])
    ok = "fold to river bet" in got and "n=" in got
    print(f"a query runs and answers       {'yes' if ok else 'NO'}")
    if not ok:
        fails.append("run_query gave " + got[:120])
    for bad in (["--hero", "--export", "x.txt"], ["--hero", "--save", "x"]):
        if not run_query(bad).startswith("refused"):
            fails.append(f"{bad[1]} was not refused")
    print(f"writing options are refused    yes")
    wrong = run_query(["--nonsense"])
    print(f"a bad flag comes back as text  {'yes' if 'refused' in wrong else 'NO'}")
    if "refused" not in wrong:
        fails.append("a bad flag did not come back as a message")

    # The worked example in the vocabulary must itself run.
    ex = ["--pool", "--site", "pokerstars", "--pot", "3bet", "--pos", "BTN",
          "--vs", "SB", "--flop", "BC", "--turn", "BC", "--street", "river",
          "--facing", "bet", "--show", "fold_to_river_bet"]
    got = run_query(ex)
    print(f"the worked example runs        {'yes' if 'fold to river bet' in got else 'NO'}")
    if "fold to river bet" not in got:
        fails.append("the worked example does not run: " + got[:120])
    # Every provider has a label, a model, an environment variable and
    # somewhere to get a key; and the settings file round-trips, against a
    # scratch file so the user's real one is never touched.
    for name, p in PROVIDERS.items():
        if not all(p.get(k) for k in ("label", "model", "env", "keys_at")):
            fails.append(f"provider {name} is missing a field")
    global SETTINGS
    real, SETTINGS = SETTINGS, Path(tempfile.mkdtemp()) / "ai.json"
    try:
        save_settings("grok", "k-test", "grok-9")
        got = settings()
        kept = (got["provider"] == "grok" and got["keys"].get("grok") == "k-test"
                and model_for("grok") == "grok-9")
        save_settings("grok", "", "")
        gone = ("grok" not in settings()["keys"]
                and model_for("grok") == PROVIDERS["grok"]["model"])
    finally:
        SETTINGS = real
    print(f"settings round-trip            {'yes' if kept and gone else 'NO'}")

    # The MCP server, driven the way the desktop app drives it: initialize,
    # list, call, and a notification that must get no reply.
    talk = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "run_query",
                        "arguments": {"argv": ["--hero", "--show", "vpip"]}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "run_query",
                        "arguments": {"argv": ["--hero", "--export", "x"]}}}]
    out = io.StringIO()
    mcp_serve(io.StringIO("\n".join(json.dumps(m) for m in talk) + "\n"), out)
    replies = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    by_id = {r["id"]: r for r in replies}
    mcp_ok = (len(replies) == 4
              and "vocabulary" in by_id[1]["result"]["instructions"]
              and [t["name"] for t in by_id[2]["result"]["tools"]] == ["run_query", "vocabulary"]
              and "VPIP" in by_id[3]["result"]["content"][0]["text"]
              and by_id[4]["result"]["content"][0]["text"].startswith("refused"))
    print(f"the MCP server answers         {'yes' if mcp_ok else 'NO'}")
    if not mcp_ok:
        fails.append("the MCP server did not answer as the app expects")
    # And the install writes one entry and leaves the rest alone.
    cfg = Path(tempfile.mkdtemp()) / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"preferences": {"kept": True}}), encoding="utf-8")
    install_desktop(cfg)
    got = json.loads(cfg.read_text(encoding="utf-8"))
    inst_ok = got.get("preferences") == {"kept": True} and \
        got["mcpServers"]["traceev"]["args"][-1] == "--mcp"
    print(f"the desktop install is polite  {'yes' if inst_ok else 'NO'}")
    if not inst_ok:
        fails.append("install_desktop rewrote or missed the config")
    if not (kept and gone):
        fails.append("the settings file did not round-trip")
    print(f"providers with a key now       {', '.join(available()) or 'none'}")
    print()
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    return not fails


def main(argv):
    if "--check" in argv:
        return 0 if check() else 1
    if "--vocabulary" in argv:
        print(vocabulary())
        return 0
    if "--score" in argv:
        provider = limit = None
        if "--with" in argv:
            provider = argv[argv.index("--with") + 1]
        if "--limit" in argv:
            limit = int(argv[argv.index("--limit") + 1])
        right, total, _rows = score(provider, limit)
        return 0 if right == total else 1
    if "--mcp" in argv:
        mcp_serve()
        return 0
    if "--install-desktop" in argv:
        try:
            path = install_desktop()
        except RuntimeError as e:
            print(f"[-] {e}")
            return 1
        print(f"wrote the traceev server into {path}")
        print("restart the Claude Desktop app; TraceEV is then one of its tools")
        return 0
    if "--providers" in argv:
        have = available()
        for name, p in PROVIDERS.items():
            print(f"  {name:8} {'key' if name in have else '-  '}  {p['label']}  ({model_for(name)})")
        print(f"  {CLI:8} {'yes' if claude_cli() else '-  '}  {CLI_LABEL}")
        print(f"  default: {settings()['provider']}")
        return 0
    argv = list(argv)
    provider = None
    if "--with" in argv:
        i = argv.index("--with")
        provider = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
        if provider not in PROVIDERS and provider != CLI:
            print(f"[-] --with takes one of {', '.join(PROVIDERS)}, {CLI}")
            return 1
    question = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not question:
        print(__doc__)
        return 1
    try:
        answer, ran, _, who = ask(question, log=print, provider=provider)
    except RuntimeError as e:
        print(f"[-] {e}")
        return 1
    print()
    print(answer)
    print(f"\n(answered by {CLI_LABEL if who == CLI else PROVIDERS[who]['label']})")
    return 0


if __name__ == "__main__":
    # The answer is the model's prose, and a model writes "≥" and "±" as
    # readily as a person does. A Windows console is cp1252 unless told
    # otherwise, and the first answer this ever printed died on a "≥" at
    # character 940 -- after three providers had been tried and the fourth
    # had actually answered. UTF-8 is what the terminal can show; the
    # fallback is for the rare console that cannot, where a "?" in a
    # sentence loses nothing the command printed beneath it cannot recover.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
