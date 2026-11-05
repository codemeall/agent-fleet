"""A fake agent CLI for `benchmarks/run.py --fake`. It proves the kit's plumbing, not any agent's quality.

    agent.py <cli> <argv...>      <cli> is claude or codex; run.py puts shims by those names first on PATH

$FAKE_BENCH_PLAN names a JSON file run.py writes from the case's "fake" block: "edits" (repo-relative path ->
content), and optionally "usage" (the single agent's), "lead_usage", "worker_usage" and "review_findings". It
also holds the case's model, effort and checks. Three roles, told apart by the command line:

- A headless run (`claude -p ...` or `codex exec ...`) must be exactly the kit's line for that CLI, with its
  permission flags; anything else fails loudly with exit 2. A single agent applies the edits. A lead (its prompt
  says "Deliver it with Fleet") drives the Fleet runtime its prompt names through one ticket the way a lead
  would: init, plan, launch --ready, wait --gate, review, accept --gate, stop, cost. Both print their final
  message the way the real CLI does: one JSON result for Claude, JSONL events for Codex, with usage.
- Anything else is a worker that Fleet launched in a tmux window. It does not check its launch line (that is
  tests/test_adapters_smoke.py's job): it finds its Fleet prompt file, a writer applies the edits and runs the
  case's checks, a reviewer reports the plan's findings, it writes its report with its `fleet` header, and it
  waits for /exit or /quit. A Claude writer also writes a session log under $HOME/.claude, so `fleet cost` reads
  its tokens as for a real Claude worker.
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PLAN = json.loads(Path(os.environ["FAKE_BENCH_PLAN"]).read_text())
LEAD_MARK = "Deliver it with Fleet"
RUN = "bench"


class Refused(Exception):
    pass


def flags_of(argv: list[str], takes_value: dict) -> tuple[dict, list[str]]:
    flags, positional, args = {}, [], list(argv)
    while args:
        arg = args.pop(0)
        if arg.startswith("-") and len(arg) > 1:
            if arg not in takes_value:
                raise Refused(f"unknown flag {arg!r}")
            if takes_value[arg] and not args:
                raise Refused(f"{arg} needs a value")
            if arg in flags and arg != "-c":
                raise Refused(f"{arg} given twice")
            flags.setdefault(arg, []).append(args.pop(0) if takes_value[arg] else True)
        else:
            positional.append(arg)
    return flags, positional


def want(flags: dict, name: str, value=None) -> str:
    if name not in flags:
        raise Refused(f"missing {name}")
    if value is not None and flags[name][0] != value:
        raise Refused(f"{name} {flags[name][0]!r}, expected {value!r}")
    return flags[name][0]


def headless_prompt(cli: str, argv: list[str]) -> str:
    """The prompt, once the line is exactly benchmarks/run.py's headless line for this CLI."""
    if cli == "claude":
        flags, positional = flags_of(argv, {"-p": False, "--output-format": True, "--allowedTools": True,
                                            "--model": True, "--effort": True, "--permission-mode": True,
                                            "--permission-prompts": True})
        want(flags, "-p")
        want(flags, "--output-format", "json")
        want(flags, "--model", PLAN["model"])
        want(flags, "--effort", PLAN["effort"])
        want(flags, "--permission-mode", "acceptEdits")  # edits in the working directory, nothing outside it
        want(flags, "--permission-prompts", "none")  # nobody answers: anything else that would prompt is denied
        rules = want(flags, "--allowedTools")
        if not rules.strip():
            raise Refused("--allowedTools names no rule")
    else:
        flags, positional = flags_of(argv, {"--json": False, "-m": True, "-c": True, "-s": True})
        if positional[:1] != ["exec"]:
            raise Refused("expected the exec subcommand first")
        positional = positional[1:]
        want(flags, "--json")
        want(flags, "-m", PLAN["model"])
        want(flags, "-s", "workspace-write")  # writes in the working directory (and temp), no network
        overrides = dict(x.partition("=")[::2] for x in flags.get("-c", []))
        expected = {"model_reasoning_effort": PLAN["effort"], "approval_policy": "never"}
        if overrides != expected:
            raise Refused(f"-c overrides {overrides}, expected {expected}")
    if len(positional) != 1:
        raise Refused(f"expected exactly one prompt argument, got {len(positional)}")
    return positional[0]


def apply_edits() -> list[str]:
    for path, content in PLAN.get("edits", {}).items():
        p = Path.cwd() / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return list(PLAN.get("edits", {}))


def final_message(cli: str, message: str, usage: dict | None) -> None:
    if cli == "claude":
        result = {"type": "result", "subtype": "success", "is_error": False, "result": message,
                  "session_id": str(uuid.uuid4()), "permission_denials": []}
        if usage is not None:
            result["usage"] = usage
        print(json.dumps(result), flush=True)
    else:
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
        print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                                             "text": message}}), flush=True)
        print(json.dumps({"type": "turn.completed", "usage": usage or {}}), flush=True)


# ---------- the lead ----------

class Lead:
    def __init__(self, prompt: str):
        m = re.search(r"The Fleet runtime is `([^`]+)`", prompt)
        if not m:
            raise Refused("the lead prompt names no Fleet runtime")
        self.fleet = m.group(1)
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp"))
        self.log: list[str] = []

    def run(self, *args: str, timeout: float = 120) -> str:
        res = subprocess.run([sys.executable, self.fleet, *args], capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
        self.log.append(f"fleet {' '.join(args)} -> {res.returncode}")
        if res.returncode not in (0, 2):
            raise Refused(f"fleet {' '.join(args)} exited {res.returncode}: {(res.stdout + res.stderr)[-800:]}")
        return res.stdout + res.stderr

    def await_report(self, wid: str, seconds: float = 120) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            out = self.run("wait", RUN, "--gate", "--timeout", "5", "--interval", "1", "--settle", "0", "--stall", "0",
                           timeout=60)
            if re.search(rf"^REPORT {re.escape(wid)} ", out, re.M):
                return
            if re.search(rf"^(EXITED|GATE-FAILED|UNREACHABLE) {re.escape(wid)}\b", out, re.M):
                raise Refused(f"{wid} ended without a report: {out[-500:]}")
        raise Refused(f"no report from {wid} in {seconds}s")

    def deliver(self, ticket: str) -> str:
        self.run("init", RUN, "--backend", "tmux", "--review", "cross-heavy", "--routing", "agents=claude,codex")
        plan = {"tickets": [{"id": "t1", "provider": "claude", "tier": "heavy", "files": list(PLAN.get("edits", {})),
                             "task": ticket, "context": "A benchmark ticket, delivered by the fake lead.",
                             "checks": [{"run": c["run"], "timeout": c.get("timeout", 300)}
                                        for c in PLAN["checks"]]}]}
        (self.tmp / "plan.json").write_text(json.dumps(plan, indent=2))
        self.run("plan", RUN, "--file", str(self.tmp / "plan.json"))
        self.run("launch", RUN, "--ready")
        self.await_report("t1")
        self.run("review", RUN, "t1", "--settle", "0")
        self.await_report("review-t1")
        (self.tmp / "evidence.md").write_text("The fake lead accepted t1 after the gate ran its checks and the "
                                              "review handed back.\n")
        self.run("accept", RUN, "t1", "--gate", "--evidence", str(self.tmp / "evidence.md"))
        self.run("stop", RUN, "--all", "--timeout", "10")
        self.run("cost", RUN, "--json")
        return "Delivered t1 with Fleet.\n" + "\n".join(self.log)


# ---------- a worker in a tmux window ----------

def worker(cli: str, argv: list[str]) -> int:
    prompt = next((x for x in argv if x.startswith("Read ") and "You are fleet worker" in x), None)
    m = prompt and re.match(r"Read (\S+) and follow it exactly\. You are fleet worker (\S+)\.", prompt)
    if not m:
        print(f"FAKE {cli}: no Fleet prompt in {argv!r}", flush=True)
        return 2
    path, wid = Path(m.group(1)), m.group(2)
    text = path.read_text()
    found = re.search(r"`([^`\s]+/reports/" + re.escape(wid) + r"\.md)`", text)
    report = Path(found.group(1)) if found else path.parent.parent / "reports" / f"{wid}.md"
    review = wid.startswith("review-")
    findings = PLAN.get("review_findings", []) if review else []
    files = [] if review else apply_edits()
    # A writer runs the case's checks, as its prompt asks, and reports each with its real exit code.
    checks = [] if review else [{"run": c["run"], "exit": subprocess.run(c["run"], shell=True, capture_output=True,
                                                                         timeout=c.get("timeout", 300)).returncode}
                                for c in PLAN["checks"]]
    header = {"files_changed": files, "checks": checks, "criteria": [], "questions": [],
              "findings": [{"severity": f.get("severity", "low"), "file": f.get("file", ""),
                            "confidence": f.get("confidence", "suspected")} for f in findings]}
    body = "\n".join(f"- {f.get('text', 'a finding')}" for f in findings) or "- No actionable findings."
    report.write_text(f"# Report {wid}\n\nStatus: needs-verification\n\n```fleet\n{json.dumps(header)}\n```\n\n"
                      f"## Work or review performed\n- Fake {cli} {'reviewed the diff' if review else 'applied the case edits'}.\n\n"
                      f"## Findings\n{body}\n")
    if cli == "claude" and "--session-id" in argv and not review:
        session = argv[argv.index("--session-id") + 1]
        log = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects" / "fake" / f"{session}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        usage = PLAN.get("worker_usage", {"input_tokens": 1000, "output_tokens": 200})
        log.write_text(json.dumps({"type": "assistant", "timestamp": stamp,
                                   "message": {"id": "msg_fake", "usage": usage}}) + "\n")
    print(f"fake {cli} {wid} handed back", flush=True)
    for line in sys.stdin:
        if line.strip() in ("/exit", "/quit"):
            return 0
    return 0


def main() -> int:
    cli, argv = sys.argv[1], sys.argv[2:]
    headless = (cli == "claude" and "-p" in argv) or (cli == "codex" and argv[:1] == ["exec"])
    if not headless:
        return worker(cli, argv)
    try:
        prompt = headless_prompt(cli, argv)
        if os.environ.get("FAKE_BENCH_STALL"):
            print("fake agent: stalled", file=sys.stderr, flush=True)
            while True:
                time.sleep(1)
        if LEAD_MARK in prompt:
            ticket = prompt.split("The ticket:\n\n", 1)[-1].split("\n\nThe result is judged", 1)[0]
            message = Lead(prompt).deliver(ticket)
            usage = PLAN.get("lead_usage")
        else:
            message = "Applied the case edits: " + ", ".join(apply_edits())
            usage = PLAN.get("usage")
    except Refused as e:
        print(f"FAKE {cli}: {e}", file=sys.stderr, flush=True)
        return 2
    final_message(cli, message, usage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
