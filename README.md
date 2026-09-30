# Agent Fleet

Run a fleet of coding-agent CLIs (Claude Code, Codex, Cursor) in visible [cmux](https://github.com/manaflow-ai/cmux) tabs, led by the agent you are already talking to.

The lead turns approved tickets into a saved execution plan, picks a worker model and effort for each ticket, watches progress, and verifies every result before dependents start. Workers share one Git checkout with explicit file ownership. **You keep control of commits and publishing.**

> **Preview `0.1.0-preview.2`.** Bundled adapters exist for Claude Code, Codex and Cursor. Live compatibility depends on your installed CLI versions, account models and local permissions. MIT licensed.

- [Quick start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
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

### Put `fleet` on your PATH (optional)

The skill never needs `fleet` on PATH; it calls its own copied runtime. For convenience in your shell:

```sh
mkdir -p ~/.local/bin
ln -s ~/.claude/skills/fleet/bin/fleet ~/.local/bin/fleet   # or ~/.agents/… / ~/.cursor/…
fleet doctor
```

### Verify

```sh
fleet doctor
```

`doctor` checks cmux connectivity, worker executables and login signals, and checks each tier model against the account's model list where the CLI offers one (Codex, Cursor, Antigravity). It cannot guarantee model access or a successful worker session.

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

1. **Finish or stop active runs.** An update replaces the runtime that live workers report back to. Run `fleet stop <run> --all` and confirm the exits first.
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

Fleet starts from approved tickets. It works with plain Markdown tickets, or as the execution step after [mattpocock/skills](https://github.com/mattpocock/skills):

1. Brainstorm and discuss the feature.
2. Run upstream `grill-with-docs` (or `grill-me`), then `to-spec`, then `to-tickets`.
3. Ask Fleet to plan and implement the resulting local tickets.
4. The lead verifies each ticket, gets any configured cross-family review, and reports back for your review and commit.

Point the lead at the real spec, glossary (`GLOSSARY.md`, optionally `GLOSSARY-MAP.md`; older repos may use `CONTEXT.md`), ADRs and tickets. Upstream `/implement` includes a commit step, so Fleet workers follow Fleet's no-commit contract instead. Workers don't need the upstream skills installed. These conventions were checked on 2026-09-30, and upstream may change them.

More: [safe first run](examples/README.md) · [agent workflow](skill/SKILL.md) · [routing](skill/references/routing.md) · [host setup](skill/references/harnesses.md) · [providers](skill/references/providers.md)

## State, continuation and safety

- **Saved plan.** `fleet plan` saves every ticket, dependency, exact file scope, model assignment and decision. Run records, prompts, reports and exit receipts live in `.fleet/runs/<run>/`, which Git ignores. Repository-specific checks belong in `.fleet/rules.md`.
- **Resume.** A fresh lead runs `fleet resume <run>` and reconciles reports and processes before continuing. The plan is frozen after the first launch. Record later decisions in run notes, and use a follow-up run for changed scope.
- **Capacity.** A report does not free a slot; the worker must actually exit, and a failed stop keeps its slot. Stop an implementation worker before capturing its review diff, then stop and verify the reviewer before accepting the work. This works even with a single slot. Dependents stay blocked until `fleet verify` accepts the ticket.
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
