"""A strict stand-in for a real agent CLI, used by tests/test_adapters_smoke.py.

    fake_cli.py <cli> <argv...>      <cli> is claude, codex, cursor-agent, agy or opencode

It accepts exactly the command line the shipped adapter in skill/providers.toml builds for that CLI: every flag
the real CLI is launched with, nothing else, each flag at most once (Codex's `-c` excepted), and one prompt in the
place the adapter puts it. Anything else fails loudly: a `FAKE <cli>: ...` line on screen and exit status 2.
The values the test expects (model, effort, session mode, trust override, account directory) come from the JSON
file named by $FAKE_CLI_EXPECT. On a good command line it records its argv in $FAKE_CLI_LOG/<cli>.jsonl, finds the
Fleet prompt file named in the prompt argument, writes a needs-verification report, and then reads its terminal
until it gets its own quit command (/exit or /quit). The other CLI's quit command is refused, not obeyed.
"""
import json
import os
import re
import sys
import uuid
from pathlib import Path

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `claude --help`
CLAUDE_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")  # `claude --help` 2.1.318
AGY_EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `agy --help`
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

# Per CLI: the flags it is launched with (name -> takes a value), where the prompt goes, the quit command and
# the account-directory variable its adapter clears or sets.
GRAMMAR = {
    "claude": {"flags": {"--session-id": True, "--resume": True, "--model": True, "--effort": True,
                         "--permission-mode": True},
               "prompt": "positional", "quit": "/exit", "account": "CLAUDE_CONFIG_DIR"},
    "codex": {"flags": {"-m": True, "-c": True, "-s": True, "-a": True},
              "prompt": "positional", "quit": "/quit", "account": "CODEX_HOME"},
    "cursor-agent": {"flags": {"--model": True, "--trust": False}, "prompt": "positional", "quit": "/quit"},
    "agy": {"flags": {"--model": True, "--effort": True, "--mode": True, "-i": True}, "prompt": "-i", "quit": "/exit"},
    "opencode": {"flags": {"--model": True, "--prompt": True}, "prompt": "--prompt", "quit": "/exit"},
}
QUITS = {"/exit", "/quit"}


class Refused(Exception):
    pass


def parse(cli: str, argv: list[str]) -> tuple[dict[str, list], list[str]]:
    """Flags (each a list of values; True for a bare flag) and positional arguments, refusing unknown flags."""
    spec = GRAMMAR[cli]["flags"]
    flags, positional, args = {}, [], list(argv)
    while args:
        arg = args.pop(0)
        if arg.startswith("-") and len(arg) > 1:
            name, eq, inline = arg.partition("=")
            if name not in spec:
                raise Refused(f"unknown flag {arg!r}")
            if spec[name]:
                if eq:
                    raise Refused(f"{name} takes its value as the next argument, not {arg!r}")
                if not args:
                    raise Refused(f"{name} needs a value")
                value = args.pop(0)
            elif eq:
                raise Refused(f"{name} takes no value: {arg!r}")
            else:
                value = True
            if name in flags and name != "-c":
                raise Refused(f"{name} given twice")
            flags.setdefault(name, []).append(value)
        else:
            positional.append(arg)
    return flags, positional


def one(flags: dict, name: str, expected=None, choices=None):
    if name not in flags:
        raise Refused(f"missing {name}")
    value = flags[name][0]
    if expected is not None and value != expected:
        raise Refused(f"{name} {value!r}, expected {expected!r}")
    if choices is not None and value not in choices:
        raise Refused(f"{name} {value!r} is not one of {', '.join(choices)}")
    return value


def check(cli: str, flags: dict, positional: list[str], want: dict) -> str:
    """The prompt argument, once every flag matches what the adapter should have built."""
    model, effort = want["model"], want.get("effort")
    if cli == "claude":
        one(flags, "--model", model)
        one(flags, "--effort", effort, CLAUDE_EFFORTS)
        mode = want.get("permission_mode")  # the review profile pins one; a worker gets none
        if mode is None and "--permission-mode" in flags:
            raise Refused("unexpected --permission-mode")
        if mode is not None:
            one(flags, "--permission-mode", mode, CLAUDE_MODES)
        session = want.get("session")  # "new", "resume" or None (no session fragment at all)
        given = [f for f in ("--session-id", "--resume") if f in flags]
        if session is None and given:
            raise Refused(f"unexpected {given[0]}")
        if session == "new" and given != ["--session-id"]:
            raise Refused("expected --session-id <uuid> and no --resume")
        if session == "resume" and given != ["--resume"]:
            raise Refused("expected --resume <uuid> and no --session-id")
        for f in given:
            if not UUID_RE.match(flags[f][0]):
                raise Refused(f"{f} {flags[f][0]!r} is not a UUID")
            if want.get("session_id") and flags[f][0] != want["session_id"]:
                raise Refused(f"{f} {flags[f][0]!r}, expected {want['session_id']!r}")
    elif cli == "codex":
        one(flags, "-m", model)
        one(flags, "-s", "workspace-write")
        one(flags, "-a", "on-request")
        overrides = {}
        for item in flags.get("-c", []):
            key, eq, value = item.partition("=")
            if not eq or key in overrides:
                raise Refused(f"bad or repeated -c override {item!r}")
            overrides[key] = value
        expected = {"model_reasoning_effort": effort, "check_for_update_on_startup": "false"}
        if want.get("trust_repo"):
            expected["projects"] = "{" + json.dumps(want["trust_repo"]) + '={trust_level="trusted"}}'
        if overrides != expected:
            raise Refused(f"-c overrides {overrides}, expected {expected}")
        if effort not in CODEX_EFFORTS:
            raise Refused(f"effort {effort!r} is not one of {', '.join(CODEX_EFFORTS)}")
    elif cli == "cursor-agent":
        one(flags, "--model", model)
        one(flags, "--trust")
    elif cli == "agy":
        one(flags, "--model", model)
        one(flags, "--effort", effort, AGY_EFFORTS)
        one(flags, "--mode", "accept-edits")
    elif cli == "opencode":
        one(flags, "--model", model)
    place = GRAMMAR[cli]["prompt"]
    if place == "positional":
        if len(positional) != 1:
            raise Refused(f"expected exactly one prompt argument, got {len(positional)}: {positional!r}")
        return positional[0]
    if positional:
        raise Refused(f"unexpected argument {positional[0]!r} (the prompt goes in {place})")
    return one(flags, place)


def prompt_file(prompt: str) -> tuple[str, Path, bool]:
    """The ticket ID, its Fleet prompt file and whether this is a resume, from the launch prompt argument."""
    m = re.fullmatch(r"Read (\S+) and follow it exactly\. You are fleet worker (\S+)\. .*", prompt, re.S)
    resumed = re.fullmatch(r"Continue as fleet worker (\S+) from where you stopped;.* Check the end of (\S+) for .*",
                           prompt, re.S)
    if m:
        path, ticket, resume = Path(m.group(1)), m.group(2), False
    elif resumed:
        path, ticket, resume = Path(resumed.group(2)), resumed.group(1), True
    else:
        raise Refused(f"no Fleet prompt file in the prompt argument: {prompt!r}")
    if not path.is_file() or path.parent.name != "prompts" or path.stem != ticket:
        raise Refused(f"prompt file {path} is missing or is not worker {ticket}'s")
    return ticket, path, resume


def main() -> int:
    cli, argv = sys.argv[1], sys.argv[2:]
    want = json.loads(Path(os.environ["FAKE_CLI_EXPECT"]).read_text())
    log = Path(os.environ["FAKE_CLI_LOG"])
    try:
        if cli not in GRAMMAR:
            raise Refused(f"no grammar for {cli}")
        flags, positional = parse(cli, argv)
        prompt = check(cli, flags, positional, want)
        ticket, path, resume = prompt_file(prompt)
        account = GRAMMAR[cli].get("account")
        if account and os.environ.get(account) != want.get("account_dir"):
            raise Refused(f"{account}={os.environ.get(account)!r}, expected {want.get('account_dir')!r}")
    except Refused as e:
        message = f"FAKE {cli}: {e}"
        print(message, flush=True)
        with (log / f"{cli}.errors").open("a") as fh:
            fh.write(message + "\n")
        return 2
    text = path.read_text()
    found = re.search(r"`([^`\s]+/reports/" + re.escape(ticket) + r"\.md)`", text)
    report = Path(found.group(1)) if found else path.parent.parent / "reports" / f"{ticket}.md"
    with (log / f"{cli}.jsonl").open("a") as fh:
        fh.write(json.dumps({"argv": argv, "ticket": ticket, "resume": resume, "id": uuid.uuid4().hex}) + "\n")
    report.write_text(f"# Report {ticket}\n\nStatus: needs-verification\n\n## Work or review performed\n"
                      f"- Smoke run of the {cli} launch line{' (resumed)' if resume else ''}; no files changed.\n")
    print(f"fake {cli} working on {ticket}", flush=True)
    quit_word = GRAMMAR[cli]["quit"]
    for line in sys.stdin:
        line = line.strip()
        if line == quit_word:
            print(f"fake {cli} quits", flush=True)
            return 0
        if line in QUITS:
            print(f"FAKE {cli}: unknown command {line}", flush=True)  # the wrong CLI's quit: keep running
        else:
            print(f"got {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
