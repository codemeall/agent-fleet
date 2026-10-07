# Delegating mid-conversation

Use this when the owner hands you work in conversation rather than a ticket folder, or adds work while a run is live. Workers, gates and evidence are the same as for planned tickets (skill steps 3–5); only how the work enters the run differs.

## 1. Pick the run

Only one run per checkout may have live workers: `fleet init` and `fleet launch` refuse while another run has any.

- An **open** run (live workers, or tickets not yet verified): add to it, ticketed or not.
- More than one open run: ask the owner which; never start another.
- No open run: today's `desk-YYYYMMDD` if it exists and `fleet check desk-YYYYMMDD` passes; otherwise `fleet init desk-YYYYMMDD` (`-2`, `-3` … when that name exists, for example after the owner committed).

Find open runs without resuming each one: `grep -l '"status": "pending"' .fleet/runs/*/run.json` lists every run that still has an unverified ticket (a live worker's ticket is one of them).

## 2. Propose one line per task

Find the exact files, then choose kind, tier and provider as in skill step 2. Show one line per task and launch only after the owner confirms:

```text
fix-flaky → implement · codex · light · tests/test_x.py · checks: pytest -q tests/test_x.py
why-slow  → investigate · claude · standard · checks: pytest -q tests/test_perf.py --durations=5
```

- `implement` (the default) owns exact files, like any ticket.
- `investigate` owns no files: research, investigation, debugging. It writes only its report, runs only read-only commands and the checks you list, and never fixes what it finds. It is never cross-family reviewed; you verify its report.

## 3. Add and launch

```sh
fleet add desk-20261007 --file - <<'JSON'
{"tickets": [
  {"id": "fix-flaky", "task": "test_retry fails about 1 run in 5; make it deterministic.",
   "files": ["tests/test_x.py"], "provider": "codex", "tier": "light",
   "context": "Do not change src/.", "checks": "pytest -q tests/test_x.py five times; all pass"},
  {"id": "why-slow", "kind": "investigate", "task": "Why did test_perf get 3x slower this week?",
   "provider": "claude", "context": "Start at src/cache.py and git log -- src/cache.py.",
   "checks": "pytest -q tests/test_perf.py --durations=5"}
], "decisions": ["Owner: fix the flake without touching src/."]}
JSON
fleet launch desk-20261007 --ready
```

- `task` is inline text; Fleet writes it to `.fleet/runs/<run>/tasks/<id>.md`. Give `context` and `checks` so the prompt is complete and `--ready` can write it.
- `add` appends and never changes saved tickets. A new ticket touching files of an unverified ticket must list it in `blockers`.
- `--ready` prints `LAUNCHED` or `SKIPPED <id>: <reason>` per ticket and no worker screens; `fleet wait` reports a worker stuck at a prompt as `STALLED`.

Then run `fleet wait <run> --timeout 540` in the background and return to the owner's conversation.

## 4. When a worker wakes you

Handle the line as in skill steps 4–5, then tell the owner the outcome in one or two lines. Two differences keep small delegations cheap:

- Verifying an `investigate` ticket runs no combined-tree checks (suite, typecheck, build); run its listed `checks` only to confirm a finding you doubt.
- An inline task has no source ticket, so there are no acceptance boxes to update.

A finding that needs a fix becomes a proposed `implement` task (one line; the owner confirms) carrying the finding in its `context`, added with `fleet add` and launched with `--ready`.

## 5. Close a desk run

Close it when the owner asks, or before they commit (a commit moves HEAD, so `fleet check` would flag the run). Never close it after each task.

1. `fleet stop <run> --all` and confirm the exits.
2. If an `implement` ticket was verified since the last close, run the combined-tree checks once.
3. `fleet check <run>`.
4. Report in a few lines: tickets and outcomes, uncommitted files to review, the owner's commit step. No Next-step start prompt; that block is for ticketed runs (skill step 6).
