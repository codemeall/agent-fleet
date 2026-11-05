# Benchmarks

Does Fleet beat one agent on the same ticket? This kit is how the owner finds out ([#16](https://github.com/codemeall/agent-fleet/issues/16)). It runs one ticket twice from the same commit: once with Fleet, and once with one agent of the same model. Then it runs the same checks on both results and writes a table.

There are no results yet, so Fleet makes no claim. When there are, they are in [results/](results/README.md), losses included.

The kit is a repository tool. It is not part of the npm package.

- [Method](#method)
- [Fairness rules](#fairness-rules)
- [Picking the three tickets for #16](#picking-the-three-tickets-for-16)
- [Approval and budget](#approval-and-budget)
- [Writing a case](#writing-a-case)
- [Running it](#running-it)
- [What is recorded](#what-is-recorded)
- [Command lines checked](#command-lines-checked)

## Method

1. **A case** names a repository and commit, the ticket text, its acceptance criteria, the check commands that judge the result, a size class and the model. See [writing a case](#writing-a-case).
2. **Each arm gets a fresh clone.** The runner clones the repository into a new temporary directory, checks out the commit and removes the `origin` remote, so nothing the agent runs can push. It never writes the owner's checkout, Fleet config or tmux server.
3. **The single arm** runs one headless agent (`claude -p` or `codex exec`) in its clone with the ticket text. It may edit files and run the case's test commands there, and nothing asks a person.
4. **The fleet arm** installs the Fleet skill into its clone (`node bin/fleet.js setup --scope project`), then runs a headless lead of the same model, told to deliver the ticket with Fleet. Workers use the owner's configured providers on a private tmux server, through a throwaway `FLEET_CONFIG`: the owner's merged config with `backend = "tmux"`, its own `tmux_socket`, and the budget as `max_workers` and `max_wall_minutes`.
5. **After each arm** the runner runs the case's checks in that clone and records wall time, tokens, review findings, checks passed and criteria passed. The owner fills in manual fixes, and marks criteria that have no check.
6. **The table** in `results/README.md` is rebuilt from every result file. For each metric it says whether Fleet won, lost or tied. A metric that either side doesn't know is not compared.

## Fairness rules

- **Same commit.** Both arms clone the same commit. The result file records the commit each arm actually checked out, and the runner warns if they differ.
- **Same top model.** Both the single agent and the Fleet lead run the case's model and effort. Fleet's workers use the owner's tiers. Before a run, set the heavy tier of the case's provider to the same model (the plan prints each provider's heavy tier and warns when none matches). Cheaper tiers for standard and light tickets are part of what Fleet is, so they stay.
- **Same ticket text.** Both prompts carry the ticket text unchanged. The fleet prompt adds only how to use Fleet; the single prompt adds only the matching plain instructions (work in the tree, don't commit, nobody can answer).
- **Same checks.** Both prompts list the check commands, and the runner runs the same commands on both clones afterwards. The agents' own claims count for nothing.
- **Same budget.** `--budget-minutes` is each arm's wall clock. For Fleet it is also `max_wall_minutes`, the workers' summed time.
- **Nobody helps.** Neither arm gets answers or a person at the keyboard. Anything a person fixes afterwards is recorded under manual fixes.
- **Losses are published.** Every run that finishes goes in the table, including the ones Fleet loses.

## Picking the three tickets for #16

Pick three real, closed tickets from the owner's own repositories, one of each size class:

| Size class | What it looks like |
| --- | --- |
| `small` | One or two files, a change you could review in one sitting. Fleet is expected to lose on cost here; that's the point of measuring it. |
| `medium-multi-file` | Several files that split into tickets with disjoint files, so workers can run side by side. |
| `hidden-dependency` | A change whose obvious first step breaks something elsewhere, such as a shared helper, a schema or a config read in two places. |

For each one:

- Use the commit just before the ticket was done, and write the ticket text as it was given, without hints from the eventual fix.
- Write checks that pass on the real fix and fail on the base commit. Run them on both before you trust them.
- Write criteria from the ticket's acceptance criteria. Link each one to a check where you can; leave the rest for the owner to mark.
- Keep the repository free of secrets and of anything that needs the network to build or test.

## Approval and budget

Nothing paid runs without `--yes-paid`. Before any paid run:

1. The owner approves the three tickets, with their checks and criteria.
2. The owner sets a budget per run: `--budget-minutes` and `--max-workers`. The runner stops an arm's agent at its budget. Fleet refuses new workers past its caps.
3. Run without `--yes-paid` first. It prints the plan and the exact commands for both arms, and runs nothing.
4. Only then run it again with `--yes-paid`.

Costs come only from a price file the owner supplies with `--prices`. None is built in, because prices change. Write down where each price came from and when:

```toml
checked = "2026-11-05, from the vendor's pricing page"

[models."opus"]   # the model name as the case and fleet cost record it
input = <USD per million input tokens>
output = <USD per million output tokens>
cache_read = <USD per million cache-read tokens>
cache_write = <USD per million cache-write tokens>
```

The placeholders are not valid TOML on purpose, so a copy that isn't filled in fails instead of pricing everything at zero.

A launch with unknown tokens, or a model or token kind without a price, is listed as not priced. The cost then shows as "at least" the priced part, or as unknown, never as zero.

## Writing a case

`benchmarks/cases/<name>/case.json`:

```json
{
  "size": "small",
  "repo": "https://github.com/owner/repo.git",
  "commit": "<full SHA>",
  "model": {"cli": "claude", "model": "opus", "effort": "high"},
  "ticket": "The ticket text, as given.",
  "criteria": [
    {"id": "works", "text": "What must be true", "checks": ["unit"]},
    {"id": "docs", "text": "A criterion with no check, for the owner to mark"}
  ],
  "checks": [{"id": "unit", "run": "python3 -m unittest tests.test_thing", "timeout": 300}],
  "allow": ["Bash(npm test *)"]
}
```

- `repo` is a git URL or a path (relative to the case directory). A local path is cloned with `--no-hardlinks`, so the clone shares no files with it.
- `model.cli` is `claude` or `codex`.
- `allow` adds Claude permission rules for test commands the agent may want beyond the checks themselves.
- `fake` is only for `--fake`: the files the fake agents write, and the usage and review findings they report.

[`cases/example`](cases/example/case.json) shows the format on this repository. It is a demonstration and the case `--fake` runs, not one of the three tickets.

## Running it

```sh
python3 benchmarks/run.py example                       # the plan and exact commands; runs nothing
python3 benchmarks/run.py <case> --arm both --yes-paid --budget-minutes 45 --max-workers 4 --prices ~/prices.toml
python3 benchmarks/run.py <case> --fake                 # both arms against fake CLIs; nothing paid
python3 benchmarks/run.py --table                       # rebuild results/README.md after editing results
```

| Option | What it does |
| --- | --- |
| `--arm fleet\|single\|both` | Which arms to run (default both). Arms run one after the other. A later run of the other arm on the same day adds to the same result file. |
| `--yes-paid` | Run the real CLIs. Without it, the plan only. |
| `--fake` | Run against the fake CLIs in `fake/`. Results go to a new temporary directory unless `--results` names one. |
| `--budget-minutes N` | Each arm's wall clock, and Fleet's `max_wall_minutes` (default 60). |
| `--max-workers N` | Fleet's `max_workers` (default 6). |
| `--prices file` | The owner's price file. Without it, cost is unknown. |
| `--keep` | Keep each arm's temporary directory, clone included, and print its path. |
| `--results dir` | Where result files and the table go (default `benchmarks/results/`). |
| `--table` | Only rebuild the table. |

A paid run uses the owner's real `HOME` and accounts, as `fleet smoke` does, so each CLI keeps its usual session logs there.

Temporary directories are removed on success, on failure and on Ctrl-C, unless `--keep`. The runner stops the agent's whole process group, every Fleet worker (`fleet stop --all`) and the private tmux server. After Ctrl-C it exits 130 and writes no result.

**`--fake`** puts shims named `claude` and `codex` first on `PATH`, with a throwaway `HOME`, and uses the shipped providers rather than the owner's. The fake single agent checks its command line and writes the case's `fake.edits`. The fake lead runs one ticket through `init`, `plan`, `launch --ready`, `wait --gate`, `review`, `accept --gate`, `stop` and `cost`. Its workers are fake TUIs in the private tmux server. This proves the plumbing, not the quality of any agent. `tests/test_benchmarks.py` runs it.

## What is recorded

`results/<case>-<date>.json` (`-fake` is added for fake runs), one entry per arm:

| Field | What it holds |
| --- | --- |
| `commit`, `command` | The commit checked out and the exact command line. |
| `exit`, `timed_out`, `wall_s` | The agent's exit, whether the budget stopped it, and its wall time. For Fleet, `worker_wall_s` is the workers' summed time. |
| `tokens` | `agent`: the single agent's or the lead's usage from its CLI's JSON. `workers`: summed from `fleet cost --json`, with `workers_unknown` of `workers_count` unknown. Unknown is `null`, never 0. |
| `cost` | The estimate from the price file, whether it is complete, and what could not be priced. |
| `checks` | Each check's pass or fail, exit and last lines. |
| `criteria` | Passed when all its checks pass. `null` with `"by": "owner"` when it has no check: set `passed` to true or false. |
| `review_findings` | Findings in the Fleet reviewers' report headers. The single arm has no review. |
| `manual_fixes` | `null` until the owner fills it in: a count, or a list of what they fixed. |
| `diffstat`, `untracked`, `notes` | What changed, and anything the runner noticed. |

Edit `manual_fixes` and owner-marked criteria in the JSON, then run `--table`.

## Command lines checked

Checked on 2026-11-05. Nothing paid was run: the lines' shape is proven by the fakes, not by a real run.

| Arm | Command line | Permissions |
| --- | --- | --- |
| single, Claude | `claude -p --output-format json --allowedTools <rules> --model M --effort E --permission-mode acceptEdits --permission-prompts none <prompt>` | `acceptEdits` approves edits and `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp`, `sed` inside the working directory (the clone) only. Other commands need an allow rule: the runner adds `Bash(<check>)` and `Bash(<check> *)` for each check (one rule per part of a piped or chained check, since Claude checks each part), plus the case's `allow`. With `--permission-prompts none`, anything else is denied and Claude is told not to retry. |
| single, Codex | `codex exec --json -m M -c model_reasoning_effort=E -s workspace-write -c approval_policy=never <prompt>` | The `workspace-write` sandbox lets commands write the working directory (the clone), plus `/tmp` and `$TMPDIR` (the runner points `TMPDIR` at the arm's own directory), with no network. With approvals never asked, nothing escalates out of it. |
| fleet lead | The same line for its CLI. Claude also gets `Bash(<skill>/bin/fleet *)` and `Bash(python3 <skill>/bin/fleet *)`, by absolute and relative path. | As above. The runner starts the private tmux server itself before the lead runs, so worker windows are children of that server, outside any sandbox the lead's commands run in. |

Sources:

- **Claude:** `claude --help` on 2.1.318, in this session: `-p`, `--output-format` (`json`), `--allowedTools` ("Comma or space-separated list of tool names to allow (e.g. "Bash(git *) Edit")"), `--model`, `--effort`, `--permission-mode` (choices `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`) and `--permission-prompts` (`none`: "anything that would prompt is denied automatically; the permission mode still decides everything else").
- **Claude docs, read 2026-11-05:** [headless](https://code.claude.com/docs/en/headless) (`--allowedTools` uses permission rule syntax, comma-separated in one argument; in `acceptEdits`, shell commands other than the read-only set need an allow rule; `--permission-prompts none` needs 2.1.259 or later; the `json` result carries `usage` and `total_cost_usd`) and [permission modes](https://code.claude.com/docs/en/permission-modes) (`acceptEdits` auto-approval "applies only to paths inside your working directory or `additionalDirectories`", and symlinks that resolve outside it still prompt).
- **Codex:** no Codex CLI here, and developers.openai.com could not be reached from this session, so no `codex exec --help` was run. Read instead in the Codex source on GitHub (the `main` branch as served on 2026-11-05): `codex-rs/exec/src/cli.rs` (`codex exec [OPTIONS] [PROMPT]`, `--json` prints JSONL events), `codex-rs/utils/cli/src/shared_options.rs` (`-s, --sandbox`), `codex-rs/utils/cli/src/sandbox_mode_cli_arg.rs` (`read-only`, `workspace-write`, `danger-full-access`) and `codex-rs/protocol/src/protocol.rs` (`WorkspaceWrite`: the working directory is writable, `TMPDIR` and `/tmp` too unless excluded, and `network_access` is false by default). The `approval_policy=never` override and the JSON events are the ones the headless reviewer line uses ([headless lines checked](../skill/references/providers.md#headless-lines-checked)).

**Not verified, to check on the first paid run:**

- That `claude -p` takes the prompt as the last argument after `--allowedTools` and the other flags. `--allowedTools` takes a list, and the next flag ends it, so the prompt goes last after `--permission-prompts none`.
- That a sandboxed Codex lead can reach the private tmux server's socket and run `fleet`, which writes only under the clone's `.fleet/` and `$TMPDIR`. If the sandbox blocks the socket, the fleet arm fails at its first launch; the result's `agent.errors` and `stderr_tail` show it.
- That a Claude lead in `acceptEdits` gets through a whole run with only those allow rules. Commands it runs outside them and outside Claude's read-only set are denied, not asked; the denials are listed in the result's `agent.denials`.
