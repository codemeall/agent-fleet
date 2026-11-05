"""A strict stand-in for a real agent CLI, used by tests/test_adapters_smoke.py.

    fake_cli.py <cli> <argv...>      <cli> is claude, codex, cursor-agent, agy or opencode

It accepts exactly the command line the shipped adapter in skill/providers.toml builds for that CLI: every flag
the real CLI is launched with, nothing else, each flag at most once (Codex's `-c` excepted), and one prompt in the
place the adapter puts it. Anything else fails loudly: a `FAKE <cli>: ...` line on screen and exit status 2.
The values the test expects (model, effort, session mode, trust override, account directory) come from the JSON
file named by $FAKE_CLI_EXPECT. On a good command line it records its argv in $FAKE_CLI_LOG/<cli>.jsonl, finds the
Fleet prompt file named in the prompt argument, writes a needs-verification report, and then reads its terminal
until it gets its own quit command (/exit or /quit). The other CLI's quit command is refused, not obeyed.

Claude's `--settings <file>` must name a file that parses to a settings object; the fake then runs its hooks as
Claude Code would: PreToolUse (Write) before each file it writes, refusing to write when a hook exits 2, and Stop
after its report. An expectation with "settings": true also requires the flag.

Codex's `-c notify=[...]` must parse as TOML to Fleet's exact program (`<python> <fleet> _notify <this ticket's events
file> <token>`); the fake runs it after its turn as Codex does, with the agent-turn-complete JSON as the last
argument. "notify": true requires it, false refuses it. "turn_message" makes the turn end with that message and no
report, as a worker that asks a question in its pane instead of handing back.

A warm worker (`fleet launch --after`) gets Fleet's continuation line instead of the worker line; the fake accepts it
only when the expectation's "warm_after" names that earlier ticket, the line names this ticket's own prompt, inbox
and report, and the session is resumed (--resume), and it refuses a warm line nobody expected. A --resume of a
session listed in "unknown_sessions" prints Claude's "No conversation found" line and exits 1, as a lost session does.

For tests/test_smoke.py: $FAKE_CLI_EXPECT may hold one expectation per CLI under "by_cli"; a writer whose prompt
carries a `fleet smoke` check (`grep -qx '<line>' smoke/<name>.txt`) writes that file first; and a CLI named in
$FAKE_CLI_STALL (comma-separated) never writes its report, like a CLI stuck at a login or trust prompt.
"""
import json
import os
import re
import subprocess
import sys
import tomllib
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
                         "--permission-mode": True, "--settings": True},
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


SETTINGS = {}  # claude's --settings file, once it parsed


def claude_settings(value: str) -> None:
    """--settings takes a file or a JSON string (`claude --help`); Fleet passes a file, which must exist and parse to
    an object whose hooks have the documented shape: event -> [{matcher?, hooks: [{type: command, command}]}]."""
    path = Path(value)
    if not path.is_file():
        raise Refused(f"--settings {value!r} is not a file")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise Refused(f"--settings {value!r} is not JSON: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise Refused(f"--settings {value!r} is not a settings object")
    for event, groups in data.get("hooks", {}).items():
        if not isinstance(groups, list) or not all(
                isinstance(g, dict) and all(h.get("type") == "command" and isinstance(h.get("command"), str)
                                            for h in g.get("hooks", [])) for g in groups):
            raise Refused(f"--settings {value!r}: bad {event} hooks")
    SETTINGS.update(data)


def run_hooks(event: str, payload: dict, target: str = "") -> list[int]:
    """Run the settings' command hooks for an event the way Claude Code does: `sh -c` with the JSON on stdin, for
    each group whose matcher (an exact name list here) names `target`. Returns their exit codes."""
    codes = []
    for group in SETTINGS.get("hooks", {}).get(event, []):
        matcher = group.get("matcher", "")
        if matcher and target not in re.split(r"[|,]\s*", matcher):
            continue
        for hook in group["hooks"]:
            res = subprocess.run(["/bin/sh", "-c", hook["command"]], input=json.dumps(payload), text=True,
                                 capture_output=True, timeout=hook.get("timeout", 600))
            codes.append(res.returncode)
            if res.returncode == 2:
                print(f"{event}:{target} hook error: {res.stderr.strip()}", flush=True)
    return codes


def fake_write(path: Path, text: str) -> bool:
    """Write a file as Claude's Write tool would, after the PreToolUse hooks; False when one blocks it."""
    payload = {"session_id": "fake", "transcript_path": "/dev/null", "cwd": os.getcwd(), "permission_mode": "default",
               "hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": str(path.resolve()), "content": text}, "tool_use_id": "toolu_fake"}
    if 2 in run_hooks("PreToolUse", payload, "Write"):
        return False
    path.write_text(text)
    return True


NOTIFY = []  # codex's notify program, once its -c override parsed


def codex_notify(value: str) -> list[str]:
    """Codex parses a -c value as TOML (codex-rs/utils/cli/src/config_override.rs) and `notify` must be an array of
    strings (config_toml.rs). Fleet's is exactly <python> <fleet> _notify <events file> <token>, with absolute paths
    to files that exist (the events file's folder) and an events file named <ticket>.jsonl under events/."""
    try:
        argv = tomllib.loads("notify = " + value)["notify"]
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"-c notify value {value!r} is not TOML: {e}") from None
    if not (isinstance(argv, list) and len(argv) == 5 and all(isinstance(a, str) for a in argv)):
        raise Refused(f"-c notify {argv!r} is not [python, fleet, _notify, events file, token]")
    python, fleet, sub, events, token = argv
    if sub != "_notify" or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise Refused(f"-c notify {argv!r} does not run `fleet _notify <events file> <token>`")
    for path in (python, fleet):
        if not (os.path.isabs(path) and os.path.isfile(path)):
            raise Refused(f"-c notify program {path!r} is not an absolute path to a file")
    if not (os.path.isabs(events) and Path(events).parent.name == "events" and Path(events).parent.is_dir()
            and events.endswith(".jsonl")):
        raise Refused(f"-c notify events file {events!r} is not under an events/ folder")
    return argv


def codex_turn_complete(message: str, ticket: str) -> None:
    """Run the notify program as Codex does after a turn (legacy_notify.rs): its argv plus the agent-turn-complete
    JSON as one last argument, stdin, stdout and stderr null, and an environment Codex rebuilt (here a bare PATH)."""
    if not NOTIFY:
        return
    payload = {"type": "agent-turn-complete", "thread-id": "b5f6c1c2-1111-2222-3333-444455556666",
               "turn-id": "1", "cwd": os.getcwd(), "client": "codex-tui",
               "input-messages": [f"Read the prompt for {ticket}."], "last-assistant-message": message}
    subprocess.run([*NOTIFY, json.dumps(payload)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, timeout=30)


def check(cli: str, flags: dict, positional: list[str], want: dict) -> str:
    """The prompt argument, once every flag matches what the adapter should have built."""
    model, effort = want["model"], want.get("effort")
    if cli == "claude":
        one(flags, "--model", model)
        one(flags, "--effort", effort, CLAUDE_EFFORTS)
        mode = want.get("permission_mode")  # the review profile pins one; a worker gets none
        roles = want.get("permission_modes")  # one fake shared by a writer and a reviewer: either role's mode
        if roles is not None:
            given = flags.get("--permission-mode", [None])[0]
            if given not in roles:
                raise Refused(f"--permission-mode {given!r}, expected one of {roles!r}")
        elif mode is None and "--permission-mode" in flags:
            raise Refused("unexpected --permission-mode")
        elif mode is not None:
            one(flags, "--permission-mode", mode, CLAUDE_MODES)
        if "--settings" in flags:
            claude_settings(flags["--settings"][0])
        elif want.get("settings"):
            raise Refused("missing --settings")
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
        if "notify" in overrides:
            if want.get("notify") is False:
                raise Refused("unexpected -c notify")
            NOTIFY[:] = codex_notify(overrides.pop("notify"))
        elif want.get("notify"):
            raise Refused("missing -c notify=[...]")
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


def prompt_file(prompt: str) -> tuple[str, Path, bool, str | None]:
    """The ticket ID, its Fleet prompt file, whether this is a resume, and the earlier ticket a warm worker
    continues after (launch --after), from the launch prompt argument."""
    # The prompt file's path may hold spaces (tests/test_signals.py runs in such a repo).
    m = re.fullmatch(r"Read (\S.*?) and follow it exactly\. You are fleet worker (\S+)\. .*", prompt, re.S)
    resumed = re.fullmatch(r"Continue as fleet worker (\S+) from where you stopped;.* Check the end of (\S+) for .*",
                           prompt, re.S)
    warm = re.fullmatch(r"Fleet ticket (\S+) is closed: it was verified, so leave its work as it is\. You are now "
                        r"fleet worker (\S+)\. Read (\S+) and follow it exactly: its rules and files replace \1's, and "
                        r"you may edit only the files it names\. Read (\S+) if it exists: it holds the lead's "
                        r"messages\. Write your report to (\S+)\.", prompt, re.S)
    after = None
    if m:
        path, ticket, resume = Path(m.group(1)), m.group(2), False
    elif resumed:
        path, ticket, resume = Path(resumed.group(2)), resumed.group(1), True
    elif warm:
        after, ticket, path, resume = warm.group(1), warm.group(2), Path(warm.group(3)), False
        inbox, report = Path(warm.group(4)), Path(warm.group(5))
        if (inbox.parent.name, inbox.name) != ("inbox", f"{ticket}.md") or \
                (report.parent.name, report.name) != ("reports", f"{ticket}.md"):
            raise Refused(f"warm line names another ticket's inbox or report: {prompt!r}")
    else:
        raise Refused(f"no Fleet prompt file in the prompt argument: {prompt!r}")
    if not path.is_file() or path.parent.name != "prompts" or path.stem != ticket:
        raise Refused(f"prompt file {path} is missing or is not worker {ticket}'s")
    return ticket, path, resume, after


def smoke_edit(ticket: str, text: str) -> None:
    """A writer's `fleet smoke` ticket: write the line its grep check looks for. Reviewers edit nothing."""
    if ticket.startswith("review-"):
        return
    for line, path in re.findall(r"grep -qx '([^']*)' (smoke/[\w.-]+\.txt)", text):
        Path(path).parent.mkdir(exist_ok=True)
        fake_write(Path(path), line + "\n")


def main() -> int:
    cli, argv = sys.argv[1], sys.argv[2:]
    want = json.loads(Path(os.environ["FAKE_CLI_EXPECT"]).read_text())
    want = want.get("by_cli", {}).get(cli, want)
    log = Path(os.environ["FAKE_CLI_LOG"])
    try:
        if cli not in GRAMMAR:
            raise Refused(f"no grammar for {cli}")
        flags, positional = parse(cli, argv)
        prompt = check(cli, flags, positional, want)
        ticket, path, resume, after = prompt_file(prompt)
        if after != want.get("warm_after"):  # a warm line only where the test expects one, after the right ticket
            raise Refused(f"warm line after {after!r}, expected {want.get('warm_after')!r}")
        if after and "--resume" not in flags:
            raise Refused("a warm line without --resume would start a new session")
        if NOTIFY and Path(NOTIFY[3]).name != f"{ticket}.jsonl":
            raise Refused(f"-c notify events file {NOTIFY[3]!r} is not ticket {ticket}'s")
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
        fh.write(json.dumps({"argv": argv, "ticket": ticket, "resume": resume, "warm_after": after,
                             "id": uuid.uuid4().hex}) + "\n")
    lost = flags.get("--resume", [None])[0]
    if lost and lost in want.get("unknown_sessions", []):  # a session the CLI no longer has: it exits at once
        print(f"No conversation found with session ID: {lost}", flush=True)
        return 1
    if cli in os.environ.get("FAKE_CLI_STALL", "").split(","):
        print(f"fake {cli} is waiting at a sign-in prompt", flush=True)
    elif want.get("turn_message"):  # a turn that ends with a question in the pane instead of a hand-back
        print(f"fake {cli}: {want['turn_message']}", flush=True)
        codex_turn_complete(want["turn_message"], ticket)
    else:
        smoke_edit(ticket, text)
        how = " (resumed)" if resume else f" (warm, after {after})" if after else ""
        if not fake_write(report, f"# Report {ticket}\n\nStatus: needs-verification\n\n## Work or review performed\n"
                                  f"- Smoke run of the {cli} launch line{how}; no files changed.\n"):
            print(f"FAKE {cli}: a PreToolUse hook refused my own report", flush=True)
        run_hooks("Stop", {"session_id": "fake", "hook_event_name": "Stop", "stop_hook_active": False,
                           "last_assistant_message": f"Report written for {ticket}.", "background_tasks": [],
                           "session_crons": []})
        codex_turn_complete(f"Report written for {ticket}.", ticket)
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
