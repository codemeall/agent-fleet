# Agent Fleet

Run a fleet of coding-agent CLIs (Claude Code, Codex, Cursor) led by the agent you are already talking to: in visible [cmux](https://github.com/manaflow-ai/cmux) panes beside the lead, or headless in [tmux](https://github.com/tmux/tmux), where you browse every worker and open any one with `fleet agents`.

The lead turns approved tickets into a saved execution plan, picks a worker model and effort for each ticket, watches progress, and verifies every result before dependents start. Workers share one Git checkout with explicit file ownership. **You keep control of commits and publishing.**

> **Preview `0.1.0-preview.23`.** Bundled adapters exist for Claude Code, Codex and Cursor, plus Antigravity and OpenCode (disabled by default). Live compatibility depends on your installed CLI versions, account models and local permissions. MIT licensed.

- [Quick start](#quick-start)
- [Installation](#installation)
  - [Companion skills (optional)](#companion-skills-optional)
- [Usage](#usage)
- [Where workers run: cmux or headless](#where-workers-run-cmux-or-headless)
  - [Running workers in cmux](#running-workers-in-cmux)
  - [Running workers headless in tmux](#running-workers-headless-in-tmux)
  - [Watching workers with `fleet agents`](#watching-workers-with-fleet-agents)
- [Keeping the lead sharp](#keeping-the-lead-sharp)
- [State, continuation and safety](#state-continuation-and-safety)
- [Compatibility and adapters](#compatibility-and-adapters)
- [Development](#development)
  - [Terminal backends](#terminal-backends)

## Quick start

```sh
# 1. Install the skill globally with the skills CLI
npx skills add codemeall/agent-fleet -g -a claude-code -a codex -a cursor

# 2. Check the terminal backend (cmux or tmux), Python and worker CLIs
~/.claude/skills/fleet/bin/fleet doctor
```

Restart your agent host, open it inside a cmux workspace (or any terminal with tmux installed, for headless workers), and ask:

> Use `/fleet` with auto routing for the approved tickets in `.scratch/my-feature/issues`.

## Installation

### Requirements

- **Python 3.11+** and **Git**, plus a terminal backend for the workers:
  - **[cmux](https://github.com/manaflow-ai/cmux)** (macOS) running with a reachable socket, for workers in visible panes beside the lead, or
  - **[tmux](https://github.com/tmux/tmux)** 3.0+ (macOS or Linux, including SSH sessions and containers), for headless workers you open with `fleet agents`
- At least one installed, authenticated worker CLI: `claude`, `codex` or `cursor-agent` (`agy` and `opencode` adapters ship disabled; turn them on in your config)
- **Node.js 18+** for the installer (not needed at runtime)

The lead host must be allowed to use the local shell, the repository and cmux or tmux. Installing the skill does not grant those permissions; see [host setup](skill/references/harnesses.md).

### Pick an install method

| Method | Best for | Command |
| --- | --- | --- |
| **skills CLI** (recommended) | Claude Code, Codex, Cursor and other agents | `npx skills add codemeall/agent-fleet` |
| **Fleet installer (npm)** | Version-tracked installs, `fleet` on PATH | `npx @codemeall/agent-fleet@preview setup --harness all` |
| **Claude Code plugin** | Claude Code only, managed updates | `/plugin marketplace add codemeall/agent-fleet` |
| **Git clone** | Contributors, pinned checkouts | `./install.sh --harness all` |

Pick one method per host. Installing the same skill twice (for example, with the skills CLI and the Fleet installer) leaves duplicate copies; the Fleet installer refuses to overwrite a directory it didn't create.

#### Option 1: skills CLI (recommended)

The [skills CLI](https://github.com/vercel-labs/skills) installs the `fleet` skill, including its bundled `bin/fleet` runtime, into your agents' skill directories:

```sh
npx skills add codemeall/agent-fleet                                     # interactive: pick agents and scope
npx skills add codemeall/agent-fleet -g -a claude-code -a codex -a cursor # user-level, no prompts
npx skills add codemeall/agent-fleet -a claude-code -y                    # current project only
```

Without `-g`, the skill goes into the current project (`.agents/skills/fleet`, symlinked into `.claude/skills/fleet` for Claude Code). With `-g`, it goes into your user-level skill directories. Pass `-a` once per agent. Run `npx skills add codemeall/agent-fleet --list` to preview what will be installed.

To update, run `npx skills update fleet`. To remove, run `npx skills remove fleet`.

#### Option 2: Fleet installer from npm

The package's own installer copies the skill to each host and records the installed version, so later runs can upgrade it safely:

```sh
npx @codemeall/agent-fleet@preview setup --harness all   # one-off install
npm install -g @codemeall/agent-fleet@preview            # also adds `fleet` to PATH
fleet setup --harness all
```

The package is published under the `preview` tag during the preview period. To install straight from GitHub instead of npm, use `npx github:codemeall/agent-fleet setup --harness all`.

Use `--harness claude`, `codex`, `cursor` or `all`. The skill is copied to:

| Host | User install (default) | Project install (`--scope project`) |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/fleet` | `<repo>/.claude/skills/fleet` |
| Codex | `~/.agents/skills/fleet` | `<repo>/.agents/skills/fleet` |
| Cursor | `~/.cursor/skills/fleet` | `<repo>/.cursor/skills/fleet` |

For a single repository:

```sh
npx @codemeall/agent-fleet@preview setup --harness codex --scope project --root /absolute/path/to/repo
```

To update, run `setup` again with the newest version (for example, `npx @codemeall/agent-fleet@preview setup --harness all`, or `npm update -g @codemeall/agent-fleet` for a global install). Each installed copy records its version in `.fleet-install.json`. Setup replaces only copies it installed itself. If you edited an installed copy, or another skill already uses that directory, setup stops and reports it without overwriting anything.

#### Option 3: Claude Code plugin marketplace

This repository is also a Claude Code plugin marketplace. In Claude Code:

```text
/plugin marketplace add codemeall/agent-fleet
/plugin install agent-fleet@agent-fleet
```

Or from a terminal:

```sh
claude plugin marketplace add codemeall/agent-fleet
claude plugin install agent-fleet@agent-fleet
```

Plugin skills are namespaced, so you invoke this one as `/agent-fleet:fleet`. To update, run `claude plugin marketplace update agent-fleet`, then `claude plugin update agent-fleet@agent-fleet`. The plugin does not add `fleet` to your PATH. The skill finds its bundled runtime itself, and you can [add a PATH command](#put-fleet-on-your-path-optional) if you want one.

#### Option 4: Git clone

```sh
git clone https://github.com/codemeall/agent-fleet.git
cd agent-fleet
./install.sh --harness all          # same options as `setup`
./skill/bin/fleet doctor
```

Claude Code can also load the checkout directly as a plugin: `claude --plugin-dir /absolute/path/to/agent-fleet`.

To update, run `git pull`, then `./install.sh` again with the same options.

### Companion skills (optional)

Fleet runs without them, and workers never need them. They help the lead before and around a run:

| Skill | Source | What the lead uses it for |
| --- | --- | --- |
| `cmux` | [manaflow-ai/cmux](https://github.com/manaflow-ai/cmux) | Inspecting windows, workspaces and panes beyond what `fleet` prints, such as finding the right `workspace=` ref |
| `grill-with-docs` (or `grill-me`) | [mattpocock/skills](https://github.com/mattpocock/skills) | Stress-testing the plan before any tickets exist |
| `to-spec` | mattpocock/skills | Saving the agreed design as the spec workers get as context |
| `to-tickets` | mattpocock/skills | Writing the local Markdown tickets Fleet runs |
| `feature-docs` | [codemeall/feature-init](https://github.com/codemeall/feature-init) | Recording the built feature as tracked feature docs, since the spec and tickets in `.scratch/` stay local |

Install just these skills with the skills CLI. The cmux repository also ships skills for developing cmux itself, so name the one you want:

```sh
npx skills add manaflow-ai/cmux --skill cmux -g -a claude-code -a codex -a cursor
npx skills add mattpocock/skills --skill grill-with-docs --skill grill-me --skill to-spec --skill to-tickets -g -a claude-code -a codex -a cursor
npx skills add codemeall/feature-init --skill feature-docs -g -a claude-code -a codex -a cursor
```

Drop `-g` to install into the current project instead. `fleet doctor` lists where it finds each companion skill. See [Daily workflow](#daily-workflow) for how they fit together.

### Put `fleet` on your PATH (optional)

The skill never needs `fleet` on PATH; it calls its own copied runtime. For convenience in your shell:

```sh
mkdir -p ~/.local/bin
ln -s ~/.claude/skills/fleet/bin/fleet ~/.local/bin/fleet   # or ~/.agents/… / ~/.cursor/…
fleet doctor
```

### Verify

```sh
fleet version   # or fleet --version
fleet doctor
```

`version` prints the runtime's version and the skill directory it runs from. With several installs, it tells you which copy your shell found.

`doctor` checks the terminal backends (cmux connectivity, tmux availability; only your configured one has to pass), worker executables and login signals, and checks each tier model against the account's model list where the CLI offers one (Codex, Cursor, Antigravity, OpenCode). It cannot guarantee model access or a successful worker session. It also reports:

- the runtime's version, and each host's installed `fleet` skill with its version, flagging copies that differ from the runtime you ran. Copies installed before `fleet version` existed show `version unknown`.
- where each [companion skill](#companion-skills-optional) is installed: the user-level skill directories, plus `.claude/skills`, `.agents/skills` and `.cursor/skills` in the current directory. Missing companions get an install command and never make `doctor` fail. Skills installed through a plugin aren't detected.

When a newer model ships, you don't need a Fleet update. List what your account offers and point a tier at it in your config:

```sh
fleet models cursor grok        # IDs containing "grok"
```

```toml
[providers.cursor.tiers.heavy]
model = "grok-4.8-high"
family = "xai"
```

To limit which models the lead may choose, set `models_allow`. Plans outside it are refused; tier models always count as allowed, so the override above needs no list edit:

```toml
[providers.cursor.models_allow]     # Cursor ships none, so this limits it to these
"grok-4.7-high" = "xai"
"kimi-k3-high" = "moonshot"

[providers.claude]
models_allow = ["opus", "sonnet", "fable"]
```

Codex's list comes from its `codex debug models` catalog and includes each model's effort levels. Claude's `opus`, `sonnet` and `fable` aliases already follow the current model. Launch one small ticket first (the [documentation-only example](examples/README.md) is a safe choice).

### Configure

Personal overrides live in `~/.config/agent-fleet/config.toml`; start from [config.example.toml](config.example.toml). `backend` under `[defaults]` picks where workers run (see [below](#where-workers-run-cmux-or-headless)). No cmux workspace is pinned by default. Start the lead in the intended workspace, or pass `workspace=<ref>` explicitly.

### Updating

1. **Finish or stop active runs.** An update replaces the runtime that live workers report back to. `fleet hold` keeps new workers from starting while current ones finish; otherwise run `fleet stop <run> --all`. Confirm the exits first, and `fleet release` after updating.
2. **Read [CHANGELOG.md](CHANGELOG.md)** for changes that need action from you, such as a config field that now behaves differently.
3. **Update with the method you installed with:**

   | Method | Command |
   | --- | --- |
   | skills CLI | `npx skills update fleet` |
   | Fleet installer (npm) | `npx @codemeall/agent-fleet@preview setup --harness all` (same `--scope`/`--root` as before), or `npm update -g @codemeall/agent-fleet` then `fleet setup --harness all` |
   | Claude Code plugin | `claude plugin marketplace update agent-fleet`, then `claude plugin update agent-fleet@agent-fleet` |
   | Git clone | `git pull`, then `./install.sh` with the same options |

4. **Restart your agent host** so it loads the new skill, then run `fleet doctor`.

Your personal settings in `~/.config/agent-fleet/config.toml` live outside the skill, so updates keep them, including any extra accounts you added. Saved runs in `.fleet/runs/` stay too; use `fleet resume <run>` to continue one. Only Fleet-installer copies record their version (in `.fleet-install.json`); skills CLI and plugin copies carry no version marker.

### Uninstall

```sh
npx skills remove fleet                                         # if installed with the skills CLI (add -g for global)
npx @codemeall/agent-fleet@preview uninstall --harness all     # same --scope/--root as setup
claude plugin uninstall agent-fleet@agent-fleet              # if installed as a Claude plugin
```

Uninstall removes only the Fleet skill it installed. It keeps modified copies, and it never touches `.fleet/` run history in your repositories.

## Usage

| Host | Invoke |
| --- | --- |
| Claude Code | `/fleet …` (plugin install: `/agent-fleet:fleet …`) |
| Codex CLI / desktop | `$fleet …` or pick it in the skill menu |
| Cursor | `/fleet …` |

```text
/fleet [auto | single:<provider> | agents=<provider>[:<model>],...]
       [backend=cmux|tmux] [workspace=<ref>] [review=off|cross-heavy|cross-all]
       -- <local tickets directory, or tasks in plain words>
```

The lead interprets these options and saves them with CLI commands; there's no autonomous scheduler behind them. `single:codex` restricts the pool to one adapter. Required review still needs a *different* model family, so either set `review=off` explicitly or widen the pool. Fleet does not fetch issue URLs. Export tracker tickets to local Markdown first, keeping their IDs, blockers, acceptance criteria and source links.

Example request to the lead:

> Use Fleet with auto routing and cross-heavy review for the approved tickets in `.scratch/settings/issues`. Carry the agreed spec and testing decisions into each worker prompt. Resume the `settings` run if it already exists.

### Daily workflow

Fleet runs approved tickets, or tasks you confirm in one line. It works with plain Markdown tickets, with tasks described in conversation, or as the execution step after [mattpocock/skills](https://github.com/mattpocock/skills) (see [companion skills](#companion-skills-optional) to install them):

1. Brainstorm and discuss the feature.
2. Run upstream `grill-with-docs` (or `grill-me`), then `to-spec`, then `to-tickets`.
3. Ask Fleet to plan and implement the resulting local tickets.
4. The lead verifies each ticket, gets any configured cross-family review, and reports back for your review and commit.
5. Optionally, ask the lead to write the feature docs with `feature-docs` before you commit. The spec and tickets in `.scratch/` are untracked, so `docs/<feature>/feature-docs/` is what records the feature in Git: what was built, the decisions behind it and the tickets it was delivered in. After a later change to the spec, the tickets or the code they cite, ask the lead to sync them.

Without those skills, hand the lead work in plain words. It proposes one line per task, and after you confirm it adds them to the open run (or a `desk-YYYYMMDD` run), launches every ready one with `fleet launch --ready` and keeps talking with you while they run. Read-only `investigate` tasks cover research and debugging. See [delegation](skill/references/delegation.md).

Point the lead at the real spec, glossary (`GLOSSARY.md`, optionally `GLOSSARY-MAP.md`; older repos may use `CONTEXT.md`), ADRs and tickets. Upstream `/implement` includes a commit step, so Fleet workers follow Fleet's no-commit contract instead. Workers don't need the upstream skills installed. These conventions were checked on 2026-09-30, and upstream may change them.

### Commands

The lead drives Fleet through the `fleet` CLI; you rarely need to, but you can run any command yourself once [`fleet` is on your PATH](#put-fleet-on-your-path-optional). The [command reference](skill/references/commands.md) lists every command, what it does and when to use it. `hold` blocks new workers while running ones carry on; `stop` quits running workers. (`fleet wait`, which you'll see the lead run, is just the lead listening for worker events; it changes nothing.) The ones you're most likely to type or ask for:

| Command | What it does |
| --- | --- |
| `fleet hold [--reason <text>]` | Pause: no new workers start in this checkout; running ones carry on |
| `fleet release` | Lift the hold so launches continue |
| `fleet resume <run>` | Show a run's plan, workers and reports; a fresh lead starts here |
| `fleet status <run>` | Table of workers and their report status |
| `fleet stop <run> --all` | Quit every worker and confirm it exited; do this before shutting down |
| `fleet context` | How full the lead's context is |
| `fleet doctor` | Check the terminal backend, logins, models and installed skills |
| `fleet agents [<run>]` | Browse the workers, watch any one live, attach to it (yours; the lead never runs it) |
| `fleet attach <run> <id>` | Open one worker's terminal (headless runs) |
| `fleet provider enable\|disable <name>` | Turn a provider on or off in your config |

More: [safe first run](examples/README.md) · [agent workflow](skill/SKILL.md) · [commands](skill/references/commands.md) · [routing](skill/references/routing.md) · [host setup](skill/references/harnesses.md) · [providers](skill/references/providers.md) · [lead context](skill/references/lead-context.md) · [worked examples](skill/references/examples.md)

## Where workers run: cmux or headless

Every run picks a terminal backend at `fleet init` and keeps it. Both run the same interactive worker CLIs with the same plan, ownership, review and verification rules; only where the terminals live differs.

| | cmux | tmux (headless) |
| --- | --- | --- |
| Where workers appear | Panes beside the lead in your cmux workspace | Windows of a detached tmux session (one per run); nothing opens on screen |
| Platforms | macOS | macOS, Linux, SSH sessions, containers |
| How you look at a worker | It's on screen; `fleet agents` also works | `fleet agents`, `fleet attach`, or plain `tmux` |
| Limit | 8 panes per workspace, then finished panes are reused | None |

Choose with `backend=cmux|tmux` in your request to the lead, `fleet init <run> --backend …`, or `backend` under `[defaults]` in your config. The default, `auto`, uses cmux when the lead runs inside cmux (or you name a workspace) and headless tmux otherwise, so existing cmux setups behave as before.

| How you choose | Example | Applies to |
| --- | --- | --- |
| In your request to the lead | `/fleet backend=tmux -- .scratch/settings/issues` | That run |
| On the command line | `fleet init settings --backend tmux` | That run |
| In your config | `backend = "tmux"` under `[defaults]` | Every new run |
| Nothing | `auto`: cmux inside cmux or with a `workspace=`, otherwise tmux | Every new run |

A run never changes backend after `init`. To move work to the other backend, finish or stop the run and start a new one. `fleet resume <run>` and `fleet status <run>` show which one a run uses (`workspace …` for cmux, `tmux session …` for tmux).

### Running workers in cmux

Use cmux when you're on macOS and want to see every worker on screen beside the lead.

1. Install and start [cmux](https://github.com/manaflow-ai/cmux), and open a workspace for the repository.
2. Start your agent host (Claude Code, Codex, Cursor) **inside that workspace**, in the repository.
3. Run `fleet doctor`; the `cmux` line must say `ready`.
4. Ask the lead to run your tickets, for example:
   > Use `/fleet` with auto routing for the approved tickets in `.scratch/settings/issues`.

   To be explicit, add `backend=cmux`. To target a workspace other than the current one, add `workspace=workspace:3` (the `cmux` companion skill helps find the ref).
5. Each worker opens in its own pane, labelled `<ticket> · <provider>:<model>@<effort>`. Up to 8 panes, launch splits the largest one; past that it reuses panes of verified workers or adds tabs. Click a pane to watch it, or run `fleet agents` in another pane for the list view.

Don't type into a worker's pane while the lead is driving it; ask the lead to `send` a message instead.

### Running workers headless in tmux

Use tmux when you're not on macOS, work over SSH or in a container, don't want panes opening around the lead, or run more than 8 workers.

1. Install tmux 3.0 or newer:
   ```sh
   brew install tmux          # macOS
   sudo apt install tmux      # Debian/Ubuntu
   sudo dnf install tmux      # Fedora
   ```
2. Start your agent host in the repository, in any terminal. You don't need to be inside tmux.
3. Run `fleet doctor`; the `tmux` line must show a version. A failing `cmux` line is fine for tmux runs.
4. Ask the lead, adding `backend=tmux` (or rely on `auto` when you're outside cmux):
   > Use `/fleet` with auto routing and `backend=tmux` for the approved tickets in `.scratch/settings/issues`.

   `fleet init` prints a line like `backend: tmux (headless): workers start in session fleet-app-settings-3fa2; the owner watches them with fleet agents settings`, which the lead passes on to you.
5. Nothing opens on screen. Each worker gets its own window in that session, named after its ticket and model. Open a second terminal in the repository and run `fleet agents` to watch them (see [below](#watching-workers-with-fleet-agents)).
6. When the run closes, the lead stops the workers. A finished worker's window stays (with its last screen) until `fleet stop <run> <id> --close`, or until you end the session yourself with `tmux -L fleet kill-session -t <session>`.

To make headless the default everywhere, put this in `~/.config/agent-fleet/config.toml`:

```toml
[defaults]
backend = "tmux"
# tmux_socket = ""   # optional: use your normal tmux server instead of Fleet's own
```

**On a remote machine.** SSH in, start the lead there (for example `claude` in the repository) and ask for `backend=tmux`. Workers keep running if your SSH connection drops, because tmux owns them. The lead doesn't, so run the lead itself inside tmux or `screen` too if it must survive a disconnect. Reconnect and run `fleet agents` to look in.

**Plain tmux works too.** Fleet keeps its sessions on a separate tmux server named `fleet`, so they never mix with your own sessions:

| To | Run |
| --- | --- |
| List Fleet's sessions | `tmux -L fleet ls` |
| Open a run's session | `tmux -L fleet attach -t <session>` |
| Jump between workers inside it | your prefix (`Ctrl-b` by default), then `w` |
| Leave without stopping anything | your prefix, then `d` |
| Watch without being able to type | `fleet attach <run> <id> --read-only` |

With `tmux_socket = ""`, drop the `-L fleet`: workers then appear in your normal `tmux ls`, and `fleet attach` from inside tmux switches straight to the worker instead of opening tmux inside tmux.

**If something looks wrong:**

| Symptom | Cause and fix |
| --- | --- |
| `init` says `no terminal backend` | Neither cmux nor tmux is available. Install tmux, or start the lead inside cmux. |
| `init` says `no cmux workspace` | The run is set to cmux but the lead isn't inside a cmux workspace. Pass `backend=tmux`, or start the lead inside cmux. |
| `--pane applies to cmux runs` | `--pane` places cmux panes; tmux workers always get their own window. Drop it. |
| `UNREACHABLE <id>` or `tmux window @3 is gone` | The window was closed or the tmux server stopped (reboot, `kill-server`). The worker is gone without an exit receipt: confirm it, then `fleet recover`, as for a lost cmux pane. Fleet never types into a window that a restarted server reused. |
| A Claude Code worker quits at once under a Claude Code lead | Shouldn't happen: Fleet removes the lead's `CLAUDECODE` and session variables from tmux workers. Check `fleet peek` and report it. |
| Detach keys don't work inside `fleet agents` | You're already inside tmux, so press your prefix twice, then `d`. |

### Watching workers with `fleet agents`

Run `fleet agents` in any terminal in the repository (it opens the run changed most recently; name one to pick another). It works like Claude Code's agent list:

```text
 fleet checkout  ·  tmux session fleet-app-checkout-3fa2  ·  2 running, 3 workers

 ● api-client   codex:gpt-6-sol@high         running     in-progress             12m  Typed API client
 ○ settings-ui  claude:sonnet@medium         running     needs-verification       9m  Settings page
 ○ copy         cursor:auto                  closed      verified                31m  Copy updates
── api-client ─────────────────────────────────────────────────────────────────────
 …the selected worker's live screen…
 ↑/↓ select · enter open · a attach · q quit
```

- **↑/↓** picks a worker and shows its screen live underneath, whichever provider it runs.
- **enter** shows that screen full size; **esc** goes back.
- **a** attaches to the worker's own terminal so you can type to it, as if you had started it yourself. Detach with your tmux prefix and `d` (prefix twice if you're already inside tmux) to return to the list.

`fleet agents --list` prints the same rows once, and `fleet attach <run> <id> [--read-only]` opens one worker directly. Headless workers live on Fleet's own tmux server, so you can also use tmux itself: `tmux -L fleet attach`, then your prefix and `w` to jump between every run and worker. Set `tmux_socket = ""` to put them on your usual tmux server instead.

Typing into a worker bypasses the lead. Tell the lead what you said, or ask it to `send` the message for you. The view only reads run state, so it's safe to keep open while the lead works.

A headless worker's window stays open after it exits, so the lead (and you) can still read its last screen; `fleet stop <run> <id> --close` removes it. If the tmux server stops (a reboot, `tmux kill-server`), its workers die without an exit receipt and show as `UNREACHABLE`: confirm they're gone and record it with `fleet recover`, exactly as for a closed cmux pane.

## Keeping the lead sharp

The lead's context grows with every wave: reports, diffs, check output. Answer quality degrades gradually as context grows, well before the window is full, and no vendor publishes a cutoff. So you decide when the lead needs a reset. Fleet shows you how full the lead is and makes the reset safe: `fleet handoff` brings the lead to a safe point, saves what it knows to disk and gives you the exact commands to compact it or replace it with a fresh lead.

**In short:** between waves, tell the lead *"Prepare a handoff"* or *"Prepare to be compacted"*, then type the commands it relays. Fleet never resets the lead on its own.

**When a run closes**, no handoff is needed. The lead's closing report ends with a **Next step** block: whether it suggests a fresh lead (the default) or compaction, and a start prompt for the next run with placeholders for the decisions you still owe. Fill them in, type `/clear`, then paste the prompt.

### How it works

- **Measurement.** Every run command (`wait`, `status`, `resume`, `launch`, …) reads the lead's own session log: the latest model call's usage in Claude Code, `last_token_usage` in Codex. It finds the log through the session ID the host exports to its shell (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`). No hooks or plugins are needed. Cursor and other hosts are not measured.
- **Your view.** The lead includes its context size when it reports a wave. `fleet context` prints it on demand; ask the lead, or, with [`fleet` on your PATH](#put-fleet-on-your-path-optional), run `! fleet context` yourself in Claude Code:
  ```text
  lead context: 312K tokens (no zones set; the owner decides when to hand off or compact)
  session: claude f5836835  ~/.claude/projects/-Users-me-app/f5836835-….jsonl
  ```
- **Safe point.** `fleet handoff` refuses while a background `fleet wait` is running (it would consume events the next lead needs) or before the plan is saved. Reports still waiting on the lead, and workers that exited without handing back, don't block it; they are listed for the next lead. Handoff never stops, messages or closes a worker: it only reads run state and appends to `notes.md`, so workers keep running and reporting while you reset the lead.
- **After a compaction.** Whether you compact or the host auto-compacts, the next run command spots the drop and tells the lead to resume from disk:
  ```text
  LEAD compacted 230K -> 35K: run fleet resume checkout-redesign and read its notes.md before acting; trust the files over your summary
  ```

### Hand off or compact, between waves

1. Tell the lead what you want. It finishes the current step and does not start the next wave.
   - *"Prepare a handoff."* (fresh lead)
   - *"Prepare to be compacted."*
2. The lead ends any background wait and runs `fleet handoff`, passing every decision or preference that is not yet in the saved plan:
   ```text
   fleet handoff checkout-redesign --note "Owner prefers Codex for API tickets." --note "price-format waits on the owner's copy decision."
   ```
   `--compact` prepares for compaction instead; `--no-notes` states there is nothing to add.
3. Fleet appends an entry to `.fleet/runs/<run>/notes.md` and prints your steps for the lead's host:
   ```text
   saved to /Users/me/app/.fleet/runs/checkout-redesign/notes.md:
   ## Handoff 2026-10-06 14:20 (fresh lead, lead at 312K)
   - Running: api-client (codex). Awaiting the next lead: settings-ui [needs-verification].
   - Owner prefers Codex for API tickets.
   - price-format waits on the owner's copy decision.

   Owner, in the lead's session:
     /clear
     /fleet resume checkout-redesign   (plugin install: /agent-fleet:fleet resume checkout-redesign)

   Lead: end your turn here. Relay these steps; do not launch or wait until the owner has acted. Workers keep running.
   ```
4. You run the printed steps. The new or compacted lead runs `fleet resume`, reads `notes.md`, handles the reports listed as awaiting it, then carries on.

| Host | Fresh lead | Compaction |
| --- | --- | --- |
| Claude Code | `/clear`, then `/fleet resume <run>` | `/compact Fleet lead for run <run>. Keep the run name, … read notes.md before acting.` (printed in full) |
| Codex | quit and relaunch `codex` (or `/new`), then `$fleet resume <run>` | `/compact`, then send *"Run fleet resume <run> and read notes.md before doing anything else."* |
| Cursor, others | new session, then `/fleet resume <run>` or send the resume message | compact, then send the resume message |

A fresh lead or a compacted one? A fresh lead is the cleaner reset: predictable size, and nothing but what's on disk. It starts at its host's baseline (system prompt, tools, skills), often 40–60K in Claude Code with many skills or MCP servers and 20–25K in Codex. Compaction keeps the same session and some unsaved nuance, but how well the summary holds up varies. Both are safe once `fleet handoff` has run. Neither host lets the lead compact itself.

If the host auto-compacts and the lead acts without resuming, send: *"Run fleet resume <run> and read notes.md before doing anything else."* Cursor is not measured, so this is always your step there.

### Optional: a size line

To have every run command show the lead's size once it passes a mark, set zones for its host. The line only informs; the lead tells you once and keeps working until you ask for a handoff.

```toml
[lead_context.claude]
warn_at = 500000
dumb_at = 600000
```

```text
LEAD 520K nearing the dumb zone (600K): keep reads lean; the owner decides whether to hand off or compact
LEAD 610K dumb zone (from 600K): tell the owner once and keep working; prepare a handoff or compaction only when they ask (references/lead-context.md)
```

Window sizes differ by host and setting: Claude Code runs Opus 5.5 with a 1M window and auto-compacts near 967K. Codex runs gpt-6.1-sol and the gpt-6 models at 272K by default (258K usable) and can be raised to 872K in Codex's config, although the API model offers 1.05M. A status line in Claude Code works alongside Fleet; set it to the same marks so its colours match.

### Limits

- Only Claude Code and Codex leads are measured. Their session logs are not a public format, so a host update can break the reading. When it does, Fleet prints nothing and keeps working.
- A log not written in the last 15 minutes is treated as an earlier session and ignored.
- The reading is the latest model call's context, so it can trail the live context by one step.
- A drop of more than 40% from at least 100K counts as a compaction. A Claude Code `/rewind` or cleared tool results can also cause one; resuming from disk is harmless then.

## State, continuation and safety

- **Saved plan.** `fleet plan` saves every ticket, dependency, exact file scope, model assignment and decision. Run records, prompts, reports and exit receipts live in `.fleet/runs/<run>/`, which Git ignores. Repository-specific checks belong in `.fleet/rules.md`.
- **Resume.** A fresh lead runs `fleet resume <run>` and reconciles reports and processes before continuing. The plan is frozen after the first launch. Record later decisions in run notes, and use a follow-up run for changed scope.
- **Capacity.** A report does not free a slot; the worker must actually exit, and a failed stop keeps its slot. Stop an implementation worker before capturing its review diff, then stop and verify the reviewer before accepting the work. This works even with a single slot. Dependents stay blocked until `fleet verify` accepts the ticket.
- **Steering.** If a provider is failing or rate-limited mid-run, `fleet steer provider <run> <name>` (then optionally `fleet steer model <run> <name>`) moves future implementation launches there without editing the frozen plan; `fleet steer reset <run>` goes back to the plan. It stays inside the run's routing and `models_allow`. Open panes and review launches are never steered, so cross-family review still holds.
- **Usage limits.** A worker that hits its limit shows as stalled. Claude workers can be relaunched with `fleet launch … --resume` after the reset: Fleet records each one's session ID, so the worker continues its own conversation instead of starting over. Otherwise the lead steers to another provider in the run's routing and relaunches fresh.
- **Taking a break.** `fleet hold [--reason <text>]` blocks new workers from starting in this checkout, for every run. Running workers carry on and can still be verified, and once none is running the lead ends its turn instead of polling; it does not `fleet stop` anything. Only `fleet release` lifts the hold; there is no timer. Ask the lead to hold or release, or run the commands yourself. The lead is told to release only when you ask, but it can technically run the command. A hold does not make a shutdown safe: sleep is fine, but when the machine, cmux or the tmux server shuts down, running workers die without an exit receipt, so after `fleet release` Fleet still lists them as running and refuses to relaunch them. Confirm each one is gone, record it with `fleet recover`, then relaunch, with `--resume` where the adapter supports it (see [example 7](skill/references/examples.md#7-recover-a-worker-whose-exit-cannot-be-confirmed)). To avoid that, run `fleet stop <run> --all` before you shut down.
- **Isolation is by instruction, not by the OS.** All workers share one checkout. Exact file scope and read-only review are prompt rules plus lead checks. Use one active run per checkout, keep sensitive or high-conflict work out of concurrent waves, inspect scoped diffs, and run checks on the combined tree. The Git check compares the captured HEAD and index; it can't prove that no Git operation happened in between.

## Compatibility and adapters

| Lead or worker | Preview scope |
| --- | --- |
| Local Claude Code, Codex CLI, Cursor CLI | Bundled worker adapters; the lead needs shell, repository and cmux or tmux access |
| Antigravity (`agy`), OpenCode | Bundled worker adapters, disabled by default; `fleet provider enable agy`, then `fleet doctor agy` |
| Grok, Muse | Not supported as their own CLIs (disabled placeholders). Their models work through Cursor: `grok-4.7-high`, `muse-spark-1.3-max` |
| Codex desktop / ChatGPT with local execution | Usable when the same capabilities and permissions are available |
| Remote machine over SSH, or a container | Use the headless tmux backend on that machine; `fleet agents` there shows the workers |
| Cloud-only session | Needs an explicit connection to a machine that can run the workers |
| Other providers or accounts | Configure and test an adapter yourself |

The lead host, worker CLI, provider account and model family are independent choices. For example, a Claude lead can run Codex workers, and Cursor can serve several model families. The bundled model IDs are examples that depend on your account, so check which IDs and effort levels you can actually use before assigning work.

[providers.toml](skill/providers.toml) holds launch templates, login checks, shutdown keys and tiers. Claude Code, Codex and Cursor ship on. The `claude-co` example, Antigravity and OpenCode ship off. The `grok` and `muse` placeholders ship off and can't be turned on until someone fills in and tests their adapter.

Turn a provider on or off with one command. It edits `~/.config/agent-fleet/config.toml` for you:

```bash
fleet provider enable agy        # Antigravity: install and sign in to agy first
fleet provider enable opencode   # its shipped tiers assume OpenRouter; see the provider notes
fleet provider disable cursor    # shipped providers can be turned off too
fleet doctor                     # shows each provider as off, signed in, or failing
```

Or set it by hand in that file:

```toml
[providers.agy]
enabled = true
```

Add a provider's name to `defaults.prefer` to include it in auto routing. Turning one off stops new launches to it; running workers finish. If you use Fleet through an AI agent, it turns providers on or off only when you ask, and never signs a CLI in for you. See [turning providers on and off](skill/references/providers.md#turning-providers-on-and-off) for the full rules, and the [provider notes](skill/references/providers.md#antigravity) for what each CLI asks before it acts.

### More than one subscription

If you pay for a second Claude Code or Codex account, add it as its own provider so Fleet can spread workers across both quotas. Each account needs its own directory:

```sh
CLAUDE_CONFIG_DIR=~/.claude-co claude          # sign in to the second account once (/login)
fleet account add claude-co --from claude --dir ~/.claude-co --max 2
fleet doctor claude claude-co
```

For Codex, use `--from codex --dir ~/.codex-2` and sign in with `CODEX_HOME=~/.codex-2 codex login`. The bundled `claude-co` example uses `~/.claude-co` and only needs turning on: `fleet provider enable claude-co`. Put the new name in `defaults.prefer` to use it in auto routing. `doctor` fails if two Claude providers turn out to be signed in to the same account; Codex doesn't report an identity, so its accounts are told apart only by directory. A second Claude account is still Anthropic, so it can't be the cross-family reviewer. Before relying on a new adapter, test authentication, model selection, the full launch → report → confirmed exit → verify loop, crash recovery, and a full-capacity review cycle. Passing `doctor` alone doesn't certify an adapter. See [provider notes](skill/references/providers.md).

## Development

```sh
python3 -m unittest discover tests -v
npm run check
npm pack --dry-run
claude plugin validate .claude-plugin/marketplace.json
```

`skill/` is the single source of truth: agent instructions, runtime, templates and adapter defaults. Everything else is packaging around it.

The lead carries `skill/SKILL.md` on every turn of a run, so a test holds it to 19,000 bytes. Put new guidance in `skill/references/` (read when needed) or in the CLI's output (seen when it applies); to add to `SKILL.md`, move something out.

| Artifact | Source |
| --- | --- |
| Portable skill (`setup`, `install.sh`) | `skill/` copied to each host's skills directory |
| Claude plugin + marketplace | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (repo root) |
| Codex plugin | `npm run build` → `dist/codex/agent-fleet/` from `skill/` + `plugin-manifests/codex.json` |
| npm package | `npm pack` (runs the build and includes the Codex bundle) |

The Codex bundle expresses explicit invocation through `agents/openai.yaml`. Add it through a configured Codex plugin marketplace, because `codex plugin add` takes a marketplace selector rather than a local path. Codex marketplace discovery still needs live validation, so the portable skill install is the common path for all three hosts.

**Releasing:** bump the version in `package.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and `plugin-manifests/codex.json`. Run the checks above and `npm pack`, then inspect the tarball. Once npm publishing is set up, publish with `npm publish --access public --tag preview`. Setup, build and tests never publish anything.

### Terminal backends

Everything that touches a worker's terminal goes through one small layer in `skill/bin/fleet` (the `terminal backends` section). Command code never calls cmux or tmux directly: it calls `terminal(state)`, which returns the backend the run was created with.

```text
fleet launch / peek / send / wait / stop / agents / attach
                    │
             terminal(state)      state["backend"]: "cmux" | "tmux" (missing means cmux, for older runs)
              ┌─────┴──────┐
            Cmux          Tmux
      cmux CLI socket   tmux -L <tmux_socket>, one detached session per run
```

**What a run records.** `fleet init` calls `pick_backend()` and saves `backend` in `run.json`, plus `workspace` (the cmux workspace UUID, or the tmux session name from `tmux_session_name()`) and, for tmux, `tmux_socket`. Each worker records `surface`, what people see (`surface:4`, or a window id like `@3`), and `surface_id`, what Fleet addresses (the cmux surface UUID, or `<server pid>/<window id>` for tmux). Both are stable on purpose: cmux refs and tmux window ids are reused after a restart.

**The interface.** A backend is a class built from the run state, with these methods:

| Method | Called by | Must |
| --- | --- | --- |
| `check(cfg)` (static) | `doctor`, `init` | Return a one-line status, or raise `FleetError` when unusable |
| `where()` | `status`, `resume`, `agents` | Say where the run's workers live |
| `place(state, want)` | `launch`, before any side effect | Decide where the worker goes, or refuse (raise) without changing anything. `want` is `--pane` |
| `open(state, place, repo, wrapped, label)` | `launch` | Start the shell command `wrapped` in `repo`; return `(surface, surface_id, why_no_id)`. Raise `FleetError("uncertain launch …")` when it can't tell whether the terminal exists |
| `retire(state, place)` | `launch`, after `open` | Close terminals the placement replaced; return whether state changed |
| `read(w, lines)` | `peek`, `wait`, `launch --settle`, `agents` | Return the screen plus about `lines` lines of scrollback |
| `send(w, items)` | `send`, `stop` | Type each item: `enter`, `ctrl+c`, `escape` and `tab` are keys, anything else is literal text |
| `close(w)` | `stop --close` | Close the terminal; one that's already closed counts as success |
| `attach_argv(w, read_only)` | `attach`, `agents` | The command that opens the worker for the owner, or `None` when there's nothing to attach to |
| `locate(w)` | `attach`, `agents --list` | A hint telling a person where to find the worker |

Every method that addresses a worker must raise rather than reach a terminal that might belong to someone else. `wait` turns a failed `read` into `UNREACHABLE`; `stop` turns a failed `send` into `stop-failed`, which keeps the worker's slot.

**What the layer doesn't own.** Exit tracking doesn't depend on the backend: `wrapped` is `fleet _run <receipt> <token> <command>`, and that wrapper writes the started and exit receipts the lead trusts. A backend only has to run the command in `repo` and keep its screen readable. Folder trust, prompts, file ownership, capacity and verification never touch it.

**Adding a backend** (for example zellij or WezTerm):

1. Write a class with the methods above, next to `Cmux` and `Tmux`.
2. Add its name to `BACKENDS`, return it from `terminal()`, and from `pick_backend()` if `auto` should ever choose it.
3. Give it a branch in `cmd_init` for what it must store (a session or workspace name, a socket), and add it to the loop in `cmd_doctor`.
4. If the multiplexer passes the caller's environment to new terminals, strip the lead's variables (`LEAD_ENV`) from workers, as `Tmux.open` does.
5. Add unit tests against a fake CLI, and a live test class that skips when the binary is missing (see below).
6. Document it here, in `config.example.toml`, `skill/references/commands.md` (`init`, `launch`, `agents`) and `CHANGELOG.md`. Touch `skill/SKILL.md` only if the lead must act differently, and stay within its byte budget.

**Tests.**

- `tests/test_runtime.py` runs the commands against a fake `cmux` (`fake_cmux`), so cmux behaviour is tested without cmux installed.
- `tests/test_backends.py` covers backend choice, config validation, `attach` and `doctor`, plus live tmux runs: it starts a real tmux server on a private socket and drives a stand-in agent (`FAKE_AGENT`: a few lines of Python that echo input, write the report on `report` and exit on `/quit`) through launch, peek, send, wait, stop and close, and through a server crash. The live class skips when tmux isn't installed; CI installs tmux, so it always runs there.

Run only the backend tests with `python3 -m unittest tests.test_backends -v`.

**Trying it by hand.** The same stand-in agent works for manual runs. Save `FAKE_AGENT` from `tests/test_backends.py` as an executable script (with a `#!/usr/bin/env python3` line), point a scratch config at it, and drive a throwaway Git repository with a one-ticket `plan.json` in the [plan format](skill/SKILL.md#2-save-a-complete-plan) whose ticket uses `"provider": "fake"`:

```toml
# /tmp/fleet-dev.toml
[defaults]
backend = "tmux"
tmux_socket = "fleetdev"      # keeps experiments off Fleet's real tmux server
review = "off"

[providers.fake]
enabled = true
bin = "/path/to/fake-agent"
launch = "{bin} --model {model} {prompt}"
quit = ["/quit", "enter"]
family = "test"

[providers.fake.tiers.standard]
model = "fake-1"
family = "test"
```

```sh
export FLEET_CONFIG=/tmp/fleet-dev.toml
fleet init dev && fleet plan dev --file plan.json && fleet launch dev --ready
fleet agents dev                          # the browser: a attaches, q quits
fleet send dev 01 report && fleet wait dev --timeout 10
fleet stop dev --all --close
tmux -L fleetdev kill-server              # clean up, if closing the last window has not ended it already
```

To see what the browser draws without a second terminal, run it in a detached tmux pane and capture it: `tmux -L view new-session -d -x 130 -y 30 'fleet agents dev'`, then `tmux -L view capture-pane -p`.

See [CHANGELOG.md](CHANGELOG.md) for changes. The review that motivated this preview is kept in [REVIEW.md](https://github.com/codemeall/agent-fleet/blob/main/REVIEW.md) as a historical assessment.
