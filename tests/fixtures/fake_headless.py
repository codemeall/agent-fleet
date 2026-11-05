"""A strict stand-in for a headless (one-shot, non-interactive) agent CLI, used by tests/test_headless.py.

    fake_headless.py <cli> <argv...>      <cli> is claude or codex

It accepts exactly the `headless` line skill/providers.toml ships for that CLI, its read-only flags included:
Claude's `-p --output-format json ... --permission-mode manual --permission-prompts none`, Codex's
`exec --json ... -s read-only -c approval_policy=never`. Anything else fails loudly: a `FAKE <cli>: ...` line on
stderr and exit status 2. The model and effort it expects, and what it does, come from the JSON file named by
$FAKE_HEADLESS_EXPECT; on a good line it records its argv in $FAKE_HEADLESS_LOG/<cli>.jsonl.

It finds the Fleet prompt file named in the prompt argument, refuses a prompt without the headless note, never
writes the report file itself, and prints its final message the way the real CLI does: one JSON result object for
Claude (`result`, `usage`, `permission_denials`), JSONL events for Codex (`item.completed` agent_message,
`turn.completed` usage). The "mode" field picks the run:

    report     a complete needs-verification report (the default)
    write      tries to write "write_path"; its read-only flags deny it, the denial is reported, the file is untouched
    no-status  a final message without a Status line
    no-usage   a complete report, with no usage reported
    fail       no final message: an error on stderr, exit 1
    sleep      waits for a signal, as a long review would (for fleet stop)
"""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
FLAGS = {  # name -> takes a value
    "claude": {"-p": False, "--output-format": True, "--model": True, "--effort": True, "--permission-mode": True,
               "--permission-prompts": True},
    "codex": {"--json": False, "-m": True, "-c": True, "-s": True},
}


class Refused(Exception):
    pass


def parse(cli: str, argv: list[str]) -> tuple[dict, list[str]]:
    spec, flags, positional, args = FLAGS[cli], {}, [], list(argv)
    while args:
        arg = args.pop(0)
        if arg.startswith("-") and len(arg) > 1:
            if arg not in spec:
                raise Refused(f"unknown flag {arg!r}")
            if spec[arg] and not args:
                raise Refused(f"{arg} needs a value")
            if arg in flags and arg != "-c":
                raise Refused(f"{arg} given twice")
            flags.setdefault(arg, []).append(args.pop(0) if spec[arg] else True)
        else:
            positional.append(arg)
    return flags, positional


def one(flags: dict, name: str, expected=None) -> str:
    if name not in flags:
        raise Refused(f"missing {name}")
    if expected is not None and flags[name][0] != expected:
        raise Refused(f"{name} {flags[name][0]!r}, expected {expected!r}")
    return flags[name][0]


def check(cli: str, argv: list[str], want: dict) -> str:
    """The prompt argument, once the line is exactly the shipped headless one with its read-only flags."""
    flags, positional = parse(cli, argv)
    if cli == "claude":
        one(flags, "-p")
        one(flags, "--output-format", "json")
        one(flags, "--model", want["model"])
        if one(flags, "--effort", want["effort"]) not in CLAUDE_EFFORTS:
            raise Refused("bad effort")
        one(flags, "--permission-mode", "manual")  # a non-accepting mode: edits and unapproved commands would ask
        one(flags, "--permission-prompts", "none")  # ... and nobody answers, so they are denied
    else:
        if not positional or positional[0] != "exec":
            raise Refused("expected the exec subcommand first")
        positional = positional[1:]
        one(flags, "--json")
        one(flags, "-m", want["model"])
        one(flags, "-s", "read-only")
        overrides = dict(item.partition("=")[::2] for item in flags.get("-c", []))
        expected = {"model_reasoning_effort": want["effort"], "approval_policy": "never"}
        if overrides != expected:
            raise Refused(f"-c overrides {overrides}, expected {expected}")
        if want["effort"] not in CODEX_EFFORTS:
            raise Refused("bad effort")
    if len(positional) != 1:
        raise Refused(f"expected exactly one prompt argument, got {positional!r}")
    return positional[0]


def prompt_file(prompt: str) -> tuple[str, Path]:
    m = re.fullmatch(r"Read (\S+) and follow it exactly\. You are fleet worker (\S+)\. .*", prompt, re.S)
    if not m:
        raise Refused(f"no Fleet prompt file in the prompt argument: {prompt!r}")
    path, ticket = Path(m.group(1)), m.group(2)
    if not path.is_file() or path.stem != ticket:
        raise Refused(f"prompt file {path} is missing or is not {ticket}'s")
    if "Fleet runs you headless" not in path.read_text():
        raise Refused("the prompt does not tell a headless run to reply with its report")
    return ticket, path


def report(ticket: str, finding: str = "") -> str:
    header = {"files_changed": [], "checks": [], "criteria": [{"id": 1, "met": True, "where": "the captured diff"}],
              "findings": [{"severity": "low", "file": "owned.txt", "confidence": "confirmed"}] if finding else [],
              "questions": []}
    return (f"# Report {ticket}\n\nStatus: needs-verification\n\n```fleet\n{json.dumps(header)}\n```\n\n"
            f"## Work or review performed\n- Read the prompt and the captured diff; changed no file.\n\n"
            f"## Findings\n- {finding or 'No actionable findings.'}\n")


def main() -> int:
    cli, argv = sys.argv[1], sys.argv[2:]
    want = json.loads(Path(os.environ["FAKE_HEADLESS_EXPECT"]).read_text())
    want = want.get("by_cli", {}).get(cli, want)
    log = Path(os.environ["FAKE_HEADLESS_LOG"])
    try:
        if cli not in FLAGS:
            raise Refused(f"no grammar for {cli}")
        ticket, path = prompt_file(check(cli, argv, want))
    except Refused as e:
        print(f"FAKE {cli}: {e}", file=sys.stderr, flush=True)
        with (log / f"{cli}.errors").open("a") as fh:
            fh.write(f"FAKE {cli}: {e}\n")
        return 2
    with (log / f"{cli}.jsonl").open("a") as fh:
        fh.write(json.dumps({"argv": argv, "ticket": ticket, "stdin_tty": sys.stdin.isatty()}) + "\n")
    mode = want.get("mode", "report")
    if cli == "codex":
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
        print(json.dumps({"type": "turn.started"}), flush=True)
    if mode == "sleep":
        while True:
            time.sleep(1)
    if mode == "fail":
        print("Error: not signed in", file=sys.stderr, flush=True)
        if cli == "codex":
            print(json.dumps({"type": "turn.failed", "error": {"message": "not signed in"}}), flush=True)
        return 1
    denials = []
    message = report(ticket)
    if mode == "write":
        target = want["write_path"]
        # Read-only by its flags (checked above), so the write is denied and nothing touches the file.
        denials = [{"tool_name": "Write", "tool_use_id": "toolu_1", "tool_input": {"file_path": target}}]
        message = report(ticket, f"Tried to write {target}; the read-only run denied it.")
    elif mode == "no-status":
        message = "I looked at the diff and it seems fine."
    usage = None if mode == "no-usage" else want.get("usage")
    if cli == "claude":
        result = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200, "num_turns": 3,
                  "result": message, "session_id": str(uuid.uuid4()), "total_cost_usd": 0.01,
                  "permission_denials": denials}
        if usage is not None:
            result["usage"] = usage
        print(json.dumps(result), flush=True)
    else:
        print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution",
                                                             "command": f"cat {path}", "aggregated_output": "",
                                                             "exit_code": 0, "status": "completed"}}), flush=True)
        for d in denials:
            print(json.dumps({"type": "item.completed", "item": {
                "id": "item_1", "type": "file_change", "status": "failed",
                "changes": [{"path": d["tool_input"]["file_path"], "kind": "update"}]}}), flush=True)
        print(json.dumps({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message",
                                                             "text": message}}), flush=True)
        print(json.dumps({"type": "turn.completed", "usage": usage or {}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
