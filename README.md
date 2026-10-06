# Agent Fleet

Run a fleet of coding-agent CLIs (Claude Code, Codex, Cursor) in visible [cmux](https://github.com/manaflow-ai/cmux) panes, led by the agent you are already talking to.

The lead turns approved tickets into a saved execution plan, picks a worker model and effort for each ticket, watches progress, and verifies every result before dependents start. Workers share one Git checkout with explicit file ownership. **You keep control of commits and publishing.**

> **Preview `0.1.0-preview.13`.** Bundled adapters exist for Claude Code, Codex and Cursor. Live compatibility depends on your installed CLI versions, account models and local permissions. MIT licensed.

- [Quick start](#quick-start)
- [Installation](#installation)
  - [Companion skills (optional)](#companion-skills-optional)
- [Usage](#usage)
- [Keeping the lead sharp](#keeping-the-lead-sharp)
- [State, continuation and safety](#state-continuation-and-safety)
- [Compatibility and adapters](#compatibility-and-adapters)
- [Development](#development)

## Quick start

```sh
# 1. Install the skill globally with the skills CLI
npx skills add codemeall/agent-fleet -g -a claude-code -a codex -a cursor

# 2. Check cmux, Python and worker CLIs
~/.claude/skills/fleet/bin/fleet doctor
```

Restart your agent host, open it inside a cmux workspace, and ask:

> Use `/fleet` with auto routing for the approved tickets in `.scratch/my-feature/issues`.

## Installation

### Requirements

- macOS (cmux is a macOS terminal) with **Python 3.11+**, **Git** and **[cmux](https://github.com/manaflow-ai/cmux)** running with a reachable socket
- At least one installed, authenticated worker CLI: `claude`, `codex` or `cursor-agent`
- **Node.js 18+** for the installer (not needed at runtime)

The lead host must be allowed to use the local shell, the repository and cmux. Installing the skill does not grant those permissions; see [host setup](skill/references/harnesses.md).

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

`doctor` checks cmux connectivity, worker executables and login signals, and checks each tier model against the account's model list where the CLI offers one (Codex, Cursor, Antigravity). It cannot guarantee model access or a successful worker session. It also reports:

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
[providers.cursor.models_allow]     # replaces the shipped starter set
"grok-4.7-high" = "xai"
"kimi-k3-high" = "moonshot"

[providers.claude]
models_allow = ["opus", "sonnet", "fable"]
```

Codex's list comes from its `codex debug models` catalog and includes each model's effort levels. Claude's `opus` and `sonnet` aliases already follow the current model. Launch one small ticket first (the [documentation-only example](examples/README.md) is a safe choice).

### Configure

Personal overrides live in `~/.config/agent-fleet/config.toml`; start from [config.example.toml](config.example.toml). No cmux workspace is pinned by default. Start the lead in the intended workspace, or pass `workspace=<ref>` explicitly.

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
       [workspace=<ref>] [review=off|cross-heavy|cross-all]
       -- <local tickets directory or task list>
```

The lead interprets these options and saves them with CLI commands; there's no autonomous scheduler behind them. `single:codex` restricts the pool to one adapter. Required review still needs a *different* model family, so either set `review=off` explicitly or widen the pool. Fleet does not fetch issue URLs. Export tracker tickets to local Markdown first, keeping their IDs, blockers, acceptance criteria and source links.

Example request to the lead:

> Use Fleet with auto routing and cross-heavy review for the approved tickets in `.scratch/settings/issues`. Carry the agreed spec and testing decisions into each worker prompt. Resume the `settings` run if it already exists.

### Daily workflow

Fleet starts from approved tickets. It works with plain Markdown tickets, or as the execution step after [mattpocock/skills](https://github.com/mattpocock/skills) (see [companion skills](#companion-skills-optional) to install them):

1. Brainstorm and discuss the feature.
2. Run upstream `grill-with-docs` (or `grill-me`), then `to-spec`, then `to-tickets`.
3. Ask Fleet to plan and implement the resulting local tickets.
4. The lead verifies each ticket, gets any configured cross-family review, and reports back for your review and commit.
5. Optionally, ask the lead to write the feature docs with `feature-docs` before you commit. The spec and tickets in `.scratch/` are untracked, so `docs/<feature>/feature-docs/` is what records the feature in Git: what was built, the decisions behind it and the tickets it was delivered in. After a later change to the spec, the tickets or the code they cite, ask the lead to sync them.

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
| `fleet doctor` | Check cmux, logins, models and installed skills |

More: [safe first run](examples/README.md) · [agent workflow](skill/SKILL.md) · [commands](skill/references/commands.md) · [routing](skill/references/routing.md) · [host setup](skill/references/harnesses.md) · [providers](skill/references/providers.md) · [lead context](skill/references/lead-context.md) · [worked examples](skill/references/examples.md)

## Keeping the lead sharp

The lead's context grows with every wave: reports, diffs, check output. Answer quality degrades gradually as context grows, well before the window is full, and no vendor publishes a cutoff. So you decide when the lead needs a reset. Fleet shows you how full the lead is and makes the reset safe: `fleet handoff` brings the lead to a safe point, saves what it knows to disk and gives you the exact commands to compact it or replace it with a fresh lead.

**In short:** between waves, tell the lead *"Prepare a handoff"* or *"Prepare to be compacted"*, then type the commands it relays. Fleet never resets the lead on its own.

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
- **Taking a break.** `fleet hold [--reason <text>]` blocks new workers from starting in this checkout, for every run. Running workers carry on and can still be verified, and once none is running the lead ends its turn instead of polling; it does not `fleet stop` anything. Only `fleet release` lifts the hold; there is no timer. Ask the lead to hold or release, or run the commands yourself. The lead is told to release only when you ask, but it can technically run the command. A hold does not make a shutdown safe: sleep is fine, but when the machine or cmux shuts down, running workers die without an exit receipt, so after `fleet release` Fleet still lists them as running and refuses to relaunch them. Confirm each one is gone, record it with `fleet recover`, then relaunch, with `--resume` where the adapter supports it (see [example 7](skill/references/examples.md#7-recover-a-worker-whose-exit-cannot-be-confirmed)). To avoid that, run `fleet stop <run> --all` before you shut down.
- **Isolation is by instruction, not by the OS.** All workers share one checkout. Exact file scope and read-only review are prompt rules plus lead checks. Use one active run per checkout, keep sensitive or high-conflict work out of concurrent waves, inspect scoped diffs, and run checks on the combined tree. The Git check compares the captured HEAD and index; it can't prove that no Git operation happened in between.

## Compatibility and adapters

| Lead or worker | Preview scope |
| --- | --- |
| Local Claude Code, Codex CLI, Cursor CLI | Bundled worker adapters; the lead needs shell, repository and cmux access |
| Codex desktop / ChatGPT with local execution | Usable when the same capabilities and permissions are available |
| Cloud-only or remote session | Needs an explicit connection to the machine running cmux |
| Other providers or accounts | Configure and test an adapter yourself |

The lead host, worker CLI, provider account and model family are independent choices. For example, a Claude lead can run Codex workers, and Cursor can serve several model families. The bundled model IDs are examples that depend on your account, so check which IDs and effort levels you can actually use before assigning work.

[providers.toml](skill/providers.toml) holds launch templates, login checks, shutdown keys and tiers. Optional `claude-co`, Antigravity and placeholder adapters ship disabled.

### More than one subscription

If you pay for a second Claude Code or Codex account, add it as its own provider so Fleet can spread workers across both quotas. Each account needs its own directory:

```sh
CLAUDE_CONFIG_DIR=~/.claude-co claude          # sign in to the second account once (/login)
fleet account add claude-co --from claude --dir ~/.claude-co --max 2
fleet doctor claude claude-co
```

For Codex, use `--from codex --dir ~/.codex-2` and sign in with `CODEX_HOME=~/.codex-2 codex login`. The bundled `claude-co` example uses `~/.claude-co` and only needs enabling: add `[providers.claude-co]` with `enabled = true` to your config. Put the new name in `defaults.prefer` to use it in auto routing. `doctor` fails if two Claude providers turn out to be signed in to the same account; Codex doesn't report an identity, so its accounts are told apart only by directory. A second Claude account is still Anthropic, so it can't be the cross-family reviewer. Before relying on a new adapter, test authentication, model selection, the full launch → report → confirmed exit → verify loop, crash recovery, and a full-capacity review cycle. Passing `doctor` alone doesn't certify an adapter. See [provider notes](skill/references/providers.md).

## Development

```sh
python3 -m unittest discover tests -v
npm run check
npm pack --dry-run
claude plugin validate .claude-plugin/marketplace.json
```

`skill/` is the single source of truth: agent instructions, runtime, templates and adapter defaults. Everything else is packaging around it.

| Artifact | Source |
| --- | --- |
| Portable skill (`setup`, `install.sh`) | `skill/` copied to each host's skills directory |
| Claude plugin + marketplace | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (repo root) |
| Codex plugin | `npm run build` → `dist/codex/agent-fleet/` from `skill/` + `plugin-manifests/codex.json` |
| npm package | `npm pack` (runs the build and includes the Codex bundle) |

The Codex bundle expresses explicit invocation through `agents/openai.yaml`. Add it through a configured Codex plugin marketplace, because `codex plugin add` takes a marketplace selector rather than a local path. Codex marketplace discovery still needs live validation, so the portable skill install is the common path for all three hosts.

**Releasing:** bump the version in `package.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and `plugin-manifests/codex.json`. Run the checks above and `npm pack`, then inspect the tarball. Once npm publishing is set up, publish with `npm publish --access public --tag preview`. Setup, build and tests never publish anything.

See [CHANGELOG.md](CHANGELOG.md) for changes. The review that motivated this preview is kept in [REVIEW.md](https://github.com/codemeall/agent-fleet/blob/main/REVIEW.md) as a historical assessment.
