"""A scripted stand-in for a worker CLI, used by tests/test_teamwork_e2e.py.

    scripted_worker.py --model <model> <launch prompt>

It reads the Fleet prompt file named in the launch prompt and follows the steps $FAKE_PLAN (JSON) gives for its
ticket: `edit` writes files (repo-relative path -> content) and `report` writes its report with `status` (default
needs-verification). The first step runs at once; each lead message (`Lead message N: ...`) runs the next one from
`on_message`. Every line typed into its terminal is logged, verbatim, to $FAKE_LOG/received-<ticket>.log, and the
prompt it was given is copied to $FAKE_LOG/prompt-<ticket>.md. It exits on /quit.
"""
import json
import os
import re
import sys
from pathlib import Path

if sys.argv[1:2] != ["--model"] or len(sys.argv) != 4:
    print(f"scripted worker: expected --model <model> <prompt>, got {sys.argv[1:]!r}", flush=True)
    sys.exit(2)
prompt = Path(re.search(r"Read (\S+) and follow it exactly\.", sys.argv[3]).group(1))
ticket = prompt.stem
text = prompt.read_text()
found = re.search(r"`([^`\s]+/reports/" + re.escape(ticket) + r"\.md)`", text)
report = Path(found.group(1)) if found else prompt.parent.parent / "reports" / f"{ticket}.md"
repo = Path.cwd()
log = Path(os.environ["FAKE_LOG"])
plan = json.loads(Path(os.environ["FAKE_PLAN"]).read_text()).get(ticket, {})
(log / f"prompt-{ticket}.md").write_text(text)


def act(step: dict) -> None:
    for path, content in step.get("edit", {}).items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    if "report" in step:
        report.write_text(f"# Report {ticket}\n\nStatus: {step.get('status', 'needs-verification')}\n\n{step['report']}\n")


act(plan)
print(f"scripted worker {ticket} ready", flush=True)
rounds = iter(plan.get("on_message", []))
for got in sys.stdin:
    with (log / f"received-{ticket}.log").open("a") as fh:
        fh.write(got)
    if got.strip() == "/quit":
        break
    if got.startswith("Lead message "):
        step = next(rounds, None)
        if step:
            act(step)
