# Command reference

Every `fleet` command: what it does and when to use it. Commands that take `<run>` act on `.fleet/runs/<run>/` in the current checkout; run them from the repository root. `fleet <command> --help` prints the exact flags. For full sequences, see the [worked examples](examples.md).

The lead runs these commands. The owner may run any of them too, most often `hold`, `release`, `context` and `stop`, once `fleet` is on their PATH.

## At a glance

| Command | What it does | Use it when |
| --- | --- | --- |
| [`version`](#version) | Prints the runtime version and location | Checking which install is running |
| [`doctor`](#doctor) | Checks cmux, provider logins, models and installed skills | Before a run, or after changing config or accounts |
| [`providers`](#providers) | Prints the merged provider config | You need an adapter's launch, quit or tier details |
| [`models`](#models) | Lists the models a provider's account offers | Choosing or replacing a model |
| [`account add`](#account-add) | Adds a second subscription of an existing provider | The owner has another Claude or Codex account |
| [`init`](#init) | Starts a run and captures the Git baseline | Starting a new run |
| [`plan`](#plan) | Saves the ticket graph and routing | After the owner approves the plan, before any launch |
| [`prompt`](#prompt) | Writes a worker or reviewer prompt | Before each launch |
| [`launch`](#launch) | Starts a worker in a cmux pane | A ticket is ready and its prompt is written |
| [`peek`](#peek) | Shows the bottom of a worker's screen | Right after launch, or after `STALLED` |
| [`send`](#send) | Types a message into a worker's pane | Answering a blocked worker or requesting changes |
| [`wait`](#wait) | Blocks until something needs the lead | Between actions, while workers run |
| [`status`](#status) | Lists workers and report statuses | You need a snapshot after `EXITED` or a recovery |
| [`stop`](#stop) | Quits workers and confirms they exited | Before review or verification, or before shutdown |
| [`diff`](#diff) | Captures a ticket's changes since launch | Before a cross-family review |
| [`verify`](#verify) | Accepts a ticket with evidence | After checks and any required review pass |
| [`recover`](#recover) | Records that a worker's process is gone | `stop` could not confirm the exit and you confirmed it yourself |
| [`check`](#check) | Compares HEAD and the index with the baseline | Before finishing a run |
| [`resume`](#resume) | Reloads a run and reconciles process state | Picking up an existing run, after a compaction or a fresh start |
| [`handoff`](#handoff) | Saves notes and prints the owner's reset steps | The owner asks for a handoff or compaction |
| [`context`](#context) | Shows the lead's context size | Reporting a wave's outcome |
| [`steer`](#steer) | Moves future launches to another provider or model | A provider is failing or rate-limited mid-run |
| [`hold`](#hold) | Blocks new launches in this checkout | The owner wants a break |
| [`release`](#release) | Lifts a hold | The owner is ready to continue |

**Similar names:** `hold` and `release` pause and continue *launches*. `resume` reloads a *run* into the lead's memory. `launch --resume` continues a stopped *worker's* own CLI session. There is no `pause` command: `hold` is the pause.

## Setup and accounts

### version

```text
fleet version
```

Prints the version and the runtime's path. It works even when the config is broken. `fleet --version` prints the version only.

### doctor

```text
fleet doctor [<provider> ...]
```

Checks cmux, each provider's executable and login, each tier's model and effort, and which companion skills are installed. Name providers to check only those. It fails when a tier's model is one the account doesn't offer, or when two Claude providers are signed in to the same account. Passing `doctor` doesn't prove a launch will work: `peek` the first worker.

**Use:** at the start of every run, and after editing config or adding an account.

### providers

```text
fleet providers
```

Prints the merged provider config: bundled `providers.toml` plus the owner's config.

**Use:** only when you need adapter details such as launch templates, quit keys or tier defaults. `doctor` already shows tiers and caps.

### models

```text
fleet models <provider> [<filter>] [--all]
```

Lists the model IDs the provider's account offers, narrowed by the owner's `models_allow`. `<filter>` keeps IDs that contain the text; some accounts offer hundreds. `--all` ignores `models_allow`.

**Use:** before choosing or replacing a model. Propose only models from this list, and ask the owner before substituting one.

### account add

```text
fleet account add <name> --from <adapter> --dir <path> [--max N] [--disabled]
```

Adds a provider that reuses an adapter (`claude`, `codex`) with its own account directory. `--max` sets parallel workers (1–6, default 1). `--disabled` adds it turned off.

**Use:** when the owner has a second subscription. The owner signs in to it; the lead never does. See [extra subscriptions](providers.md#extra-subscriptions).

## Planning

### init

```text
fleet init <run> [--workspace <ref>] [--routing <mode>] [--review off|cross-heavy|cross-all]
```

Creates `.fleet/runs/<run>/`, adds it to `.fleet/.gitignore`, and records the HEAD and index baseline. It validates the cmux workspace (`--workspace`, the config default, or the current one). It refuses if the run already exists, or if another run in this checkout still has live workers.

**Use:** once per run. Map the owner's `workspace=`, routing (`auto`, `single:<provider>`, `agents=…`) and `review=` options to these flags. For an existing run, use `resume` instead.

### plan

```text
fleet plan <run> --file <plan.json>
```

Saves every ticket, blocker, exact file list, tier, provider and decision, and validates dependencies, cycles, scopes and assignments. The plan is frozen once a worker launches. The plan format is in [SKILL.md step 2](../SKILL.md#2-save-a-complete-plan).

**Use:** after the owner approves the plan, before the first launch. Record later decisions in `notes.md`; changed scope needs a follow-up run ([example 8](examples.md#8-change-scope-after-launch-a-follow-up-run)).

## Launching and talking to workers

### prompt

```text
fleet prompt <run> <id> [--force]
fleet prompt <run> <review-id> --review-of <id> --diff <diff-file> [--force]
```

Writes `.fleet/runs/<run>/prompts/<id>.md` from the saved ticket, the worker rules, `.fleet/rules.md`, and the ticket's `context` and `checks`. If either is missing from the plan, the prompt keeps a `<!-- LEAD: … -->` comment for you to fill; `launch` refuses until you do. With `--review-of`, it writes a reviewer prompt instead; the writer must be stopped and the diff must match its current changes. `--force` overwrites an existing prompt, but never a live worker's.

**Use:** before each launch, and before each review.

### launch

```text
fleet launch <run> <id> <provider> --tier heavy|standard|light|review
             [--model <m>] [--effort <e>] [--family <f>] [--pane <ref|uuid|index>] [--resume]
```

Starts the worker in its own cmux pane with the prompt. The provider, tier and pins must match the saved plan; a steer (see `steer`) may change the provider and model, and launch prints `steered (<tier>): …`. Launch enforces blockers, file ownership, capacity and any hold. Up to 8 panes it splits the largest one; at 8 it reuses a pane holding only verified, exited workers, or adds a tab to an idle pane, and refuses when every other pane has a live worker.

- `--pane` picks the pane to split, replace or add a tab to.
- `--family` names the model family when an account-specific model isn't recognized.
- `--resume` continues the stopped worker's own session (same provider and account, adapters with `resume` such as Claude), for example after a usage limit resets.

**Use:** for each ready ticket, and with `--tier review` for each reviewer. Then `peek`.

### peek

```text
fleet peek <run> <id> [--lines N]
```

Prints the last 20 lines of the worker's screen, or `N`.

**Use:** shortly after launch and again within a minute or two, to catch login, trust, model or permission prompts. Also after `STALLED`. Raise `--lines` when a dialog is cut off. Don't peek by reflex.

### send

```text
fleet send <run> <id> <text ...>
```

Types the text into the worker's pane and presses Enter. `wait` then expects a response, so a silent worker shows as `STALLED`.

**Use:** answering a `blocked` report, or sending a bounded fix list after setting the report to `Status: changes-requested`. Save decisions to `notes.md`. Never use it to accept a permission or login prompt for the owner.

## Watching

### wait

```text
fleet wait <run> --timeout <seconds> [--stall <seconds>] [--interval <seconds>] [--any-change]
```

Blocks until something needs the lead, then prints it and exits 0. On timeout it prints `TIMEOUT` and exits 2.

| Line | Meaning | Next |
| --- | --- | --- |
| `REPORT <id> [needs-verification]` | The worker handed back | Review and verify |
| `REPORT <id> [blocked]` | The worker needs a decision | `send` the answer |
| `EXITED <id>` | The process ended before handing back | `status`, read the report, repair or relaunch |
| `STALLED <id>` | The screen hasn't changed for `--stall` seconds (default 180; 0 turns it off) | `peek` |
| `UNREACHABLE <id>` | cmux can't read the pane | Investigate; a missing pane isn't proof of exit |
| `HOLD` | Launches are held and no worker is running | Handle `PENDING` lines, tell the owner, stop |
| `TIMEOUT` | Nothing new | Handle `PENDING` lines, otherwise wait again |
| `PENDING <id> [status]` | A report is still waiting on the lead | Handle it |

`--timeout` is at most 600; use the longest your host allows (540 in Claude Code), or run it in the background. `--interval` (default 10) sets how often it polls. `--any-change` wakes on any report change.

**Use:** whenever workers are running and you have nothing else to do. Run one wait at a time: a wait consumes the events it prints. End a background wait before `handoff`.

### status

```text
fleet status <run> [--json]
```

Reconciles exit receipts, then lists each worker's provider and model, pane, state and report status.

**Use:** after `EXITED`, after a recovery, or when you need a current table. `wait` covers routine watching.

## Review and acceptance

### stop

```text
fleet stop <run> <id ...> | --all [--close] [--timeout <seconds>]
```

Sends each worker its quit keys and waits up to `--timeout` seconds (default 10, at most 60) for a confirmed exit. A failed stop marks the worker `stop-failed` and keeps its slot. `--close` closes the tab, only after a confirmed exit.

**Use:** before capturing a review diff, before verifying, after a reviewer finishes, and with `--all` before the machine or cmux shuts down.

### diff

```text
fleet diff <run> <id> [--output <file>]
```

Prints or saves the ticket's changes since launch, including new files. The writer must be stopped and no live worker may own its files.

**Use:** to capture the diff for `prompt --review-of`.

### verify

```text
fleet verify <run> <id> --evidence <file>
```

Accepts a ticket or a review. The worker must have exited, its report must say `Status: needs-verification`, and for a writer, any required review must be verified and match the current diff. It sets the report to `verified`, appends the evidence, and unblocks dependents.

**Use:** after running the ticket's checks yourself. Verify the reviewer first, then the writer, each with its own evidence file. Only then update the source ticket.

### recover

```text
fleet recover <run> <id> --evidence <file>
```

Records that a worker's process is gone when `stop` couldn't confirm it, for example after a crash or a shutdown. It frees the slot. It doesn't accept the work.

**Use:** only after you have checked the `pid` that `resume` shows yourself and written that check to the evidence file ([example 7](examples.md#7-recover-a-worker-whose-exit-cannot-be-confirmed)). Never just because a tab disappeared.

### check

```text
fleet check <run>
```

Compares the current HEAD and index with the baseline from `init`. Exits 1 and lists changes if someone committed or staged during the run. It can't prove no Git action happened in between.

**Use:** before finishing a run. Investigate any difference with the owner.

## Continuity

### resume

```text
fleet resume <run> [--json]
```

Reconciles exit receipts and prints the run's options, any active steer or hold, one line per ticket, the decisions, each worker's state, report and `pid`, and the path to `notes.md`. `--json` prints the full records.

**Use:** when the named run already exists, after a compaction (`LEAD compacted …`), and when starting as a fresh lead. Then read `notes.md` and handle what the latest `## Handoff` entry lists as awaiting you. Never `init` a replacement run.

### handoff

```text
fleet handoff <run> [--compact] (--note <text> ... | --no-notes)
```

Appends a `## Handoff` entry to `notes.md` listing running workers, reports awaiting the next lead, and each `--note`. Then it prints the owner's steps to reset the lead. `--compact` prepares for compaction instead of a fresh lead. It refuses before a plan is saved or while a `wait` is running. It never stops or messages workers.

**Use:** only when the owner asks, at a safe point between steps. Relay the printed steps and stop. See [lead context](lead-context.md).

### context

```text
fleet context [--json]
```

Prints the lead's context size, measured from its Claude Code or Codex session log, and its zone when the owner has set `[lead_context]` marks. Other hosts print "unknown".

**Use:** when reporting a wave's outcome to the owner.

## Steering and pausing

### steer

```text
fleet steer provider <run> <name>
fleet steer model <run> <name>
fleet steer reset <run>
```

Changes the provider, then optionally the model, for future implementation launches without editing the frozen plan. `provider` must be inside the run's routing and keeps the tier models. `model` needs a steered provider and must pass `models_allow`; it replaces every tier's model, light tickets included. `reset` returns to the plan's routing. Open panes and review launches are never steered.

**Use:** when a provider is failing, rate-limited or a poor fit. Prefer steering the provider alone. Keep passing the plan's provider and tier to `launch`.

### hold

```text
fleet hold [--reason <text>]
```

Blocks new launches in this checkout, for every run. Running workers continue and can still be verified. `wait` prints `HOLD` once none is running. There is no timer.

**Use:** only when the owner asks, for example for a break. When `launch` refuses because of a hold, don't retry: finish verifying running workers, tell the owner and stop. A hold doesn't make a shutdown safe; run `stop --all` first.

### release

```text
fleet release
```

Removes the hold so launches can continue.

**Use:** only when the owner asks.
