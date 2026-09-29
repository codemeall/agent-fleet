# Agent Fleet

A local orchestration skill for running coding-agent CLIs in visible cmux tabs. Your current agent is the lead: it turns approved tickets into a saved execution plan, chooses worker models and effort, watches progress, and verifies the result. Workers share one Git checkout with explicit file ownership. The owner retains control of commits and publishing.

**Preview: `0.1.0-preview.1`.** Claude Code, Codex and Cursor have bundled CLI adapters; live compatibility depends on installed CLI versions, account models and local permissions. Automated tests do not establish a successful live run on every provider. Additional adapters are experimental. Released under the [MIT license](LICENSE).

## Requirements and installation

Use a local machine with Python 3.11+, Git, cmux with a reachable socket, and at least one installed, authenticated worker CLI (`claude`, `codex` or `cursor-agent`). Source setup needs Node.js 18+; npm distribution also needs npm. The lead must have permission to use the local shell, repository and cmux; installing a skill does not grant that permission.

From a GitHub checkout:

```sh
./install.sh --harness all
# The installer copies the skill. Use its runtime directly if fleet is not on PATH:
./skill/bin/fleet doctor
```

The planned npm release can be installed **after it is published**:

```sh
npm install -g @codemeall/agent-fleet@0.1.0-preview.1
fleet setup --harness all --scope user
fleet doctor
```

npm installs the executable; `setup` registers a durable copy of the skill with the selected harness. It creates missing skill directories and reports conflicts rather than replacing unrelated skills. Choose `claude`, `codex`, `cursor` or `all`. User installs target `~/.claude/skills/fleet`, `~/.agents/skills/fleet` and `~/.cursor/skills/fleet`; project installs use the corresponding directories under the repository:

```sh
fleet setup --harness codex --scope project --root /absolute/path/to/repository
fleet uninstall --harness codex --scope project --root /absolute/path/to/repository
```

`--root` is an absolute base directory for the chosen scope. For user scope, it substitutes for the home directory. Uninstall removes the installed Fleet skill, not repository run history or the npm package. Remove the executable separately with `npm uninstall -g @codemeall/agent-fleet` when applicable.

Personal overrides belong in `~/.config/agent-fleet/config.toml`; see [config.example.toml](config.example.toml). No workspace is pinned by default. Start the lead in the intended cmux workspace or supply a verified workspace reference explicitly. `fleet doctor` checks connectivity, executable presence and login signals; it cannot guarantee model access or a successful worker session.

## Daily workflow

Fleet starts at approved tickets. It works with ordinary Markdown tickets or as an execution companion to [mattpocock/skills](https://github.com/mattpocock/skills):

1. Brainstorm and discuss the feature.
2. Use upstream `grill-with-docs` or `grill-me`, then `to-spec`, then `to-tickets`, using the invocation syntax supported by your harness.
3. Ask Fleet to plan and implement the resulting local tickets.
4. The lead verifies every ticket, obtains any configured cross-family review, and reports the combined result for your review and commit.

Read the upstream project's configuration to locate its spec, glossary, ADRs and issue tracker. Current upstream conventions use `GLOSSARY.md`, with optional `GLOSSARY-MAP.md`; older repositories may use `CONTEXT.md`. Supply actual paths. Upstream `/implement` includes a commit step, so Fleet workers follow Fleet's no-commit implementation contract instead of invoking it unchanged. Workers do not need the full upstream skill collection installed. These integration conventions were checked on 2026-09-30; upstream may evolve.

For example, tell the lead:

> Use Fleet with auto routing and cross-heavy review for the approved tickets in `.scratch/settings/issues`. Carry the agreed spec and testing decisions into each worker prompt. Resume the `settings` run if it already exists.

Standalone skill invocation is `/fleet` in Claude Code or Cursor, and `$fleet` or a skill selection in Codex. Plugin installations may namespace the invocation (for example `/agent-fleet:fleet` in Claude). Discover the command in your host rather than assuming a tool named `Skill`. Installing from npm is not, by itself, a host plugin installation.

The conversational options are interpreted by the lead, which persists them through CLI commands:

```text
/fleet [auto | single:<provider> | agents=<provider>[:<model>],...]
       [workspace=<ref>] [review=off|cross-heavy|cross-all]
       -- <local tickets directory or task list>
```

`single:codex` restricts the pool to that adapter. Required review still needs a different known model family; use `review=off` explicitly or expand the pool if it cannot supply one. Fleet does not fetch issue URLs: export tracker tickets to local Markdown first and preserve their IDs, blockers, criteria and source links.

See the complete [safe documentation-only example](examples/README.md), the [agent workflow](skill/SKILL.md), [routing](skill/references/routing.md), and [host setup](skill/references/harnesses.md).

## State, continuation and safety

The lead saves every ticket, dependency, exact file scope, model assignment and decision with `fleet plan`. Run records, prompts, reports and process exit receipts live under `.fleet/runs/<run>/`; run data is ignored by Git. Repository-specific checks and context belong in `.fleet/rules.md`. A fresh lead uses `fleet resume <run>` and reconciles reports and processes before continuing. Plans are frozen after the first launch; preserve later decisions in run notes and use a follow-up run for changed assignments or scope after stopping overlapping workers.

Reports do not release capacity. A worker must exit before its slot is free; failed stops retain capacity. Stop an implementation worker before creating its immutable review diff, then stop and verify the reviewer before accepting the implementation. This works even with one available slot. Dependents stay blocked until `fleet verify` accepts the ticket.

Exact file scope and read-only review are instructions plus lead checks, **not operating-system isolation**. All workers share a checkout. Use one active run per checkout and serialize Fleet CLI mutations through one lead, keep sensitive/high-conflict work out of concurrent waves, inspect scoped diffs, and run checks on the combined tree. The Git check compares captured HEAD and index states; it cannot prove no intermediate Git operation occurred.

## Compatibility and extending adapters

| Lead or worker | Preview scope |
| --- | --- |
| Local Claude Code, Codex CLI, Cursor CLI | Bundled worker adapters; local leads need shell, repository and cmux access |
| Codex desktop / ChatGPT with local execution | Usable when those same capabilities and permission controls are available |
| Cloud-only or remote session | Requires an explicit connection to the machine running cmux |
| Other providers or accounts | Configure and test an adapter; not universal provider support |

A lead host, worker CLI, provider account and model family are separate choices. A Claude lead may run a Codex worker; Cursor can serve several model families. Bundled model IDs are account-dependent examples. Verify supported IDs and effort settings locally before assigning work.

[providers.toml](skill/providers.toml) contains launch templates, login checks, shutdown keys and tiers. Optional `claude-co`, Antigravity and placeholder adapters are disabled. To add an adapter, configure and test authentication, model selection, launch → report → confirmed exit → verification, crash recovery and a full-capacity review cycle. `doctor` alone is not an adapter certification. See [provider notes](skill/references/providers.md).

## Development and distribution

```sh
python3 -m unittest discover tests -v
npm pack --dry-run
```

Keep `skill/` canonical: it contains the agent instructions, runtime, templates and adapter defaults. GitHub provides source and examples; npm distributes the runtime and those assets. Claude and Codex plugin manifests are included. Claude loads the source root (`claude --plugin-dir /absolute/path/to/agent-fleet`). For Codex, `npm run build` creates a separate plugin root at `dist/codex/agent-fleet/` from the canonical skill and `plugin-manifests/codex.json`. The Codex bundle expresses explicit invocation through `agents/openai.yaml` and omits the Claude-specific frontmatter flag. `npm pack` builds and includes that bundle automatically. Add the generated Codex bundle through a configured plugin marketplace using your host's supported workflow; `codex plugin add` takes a marketplace selector, not an arbitrary local path. Confirm Fleet appears in the host before use. Cursor uses explicit `fleet setup --harness cursor` in this preview. Marketplace discovery and host plugin loading still need live validation; the portable skill install is the common path across all three hosts.

The review that motivated this preview is preserved in [REVIEW.md](https://github.com/codemeall/agent-fleet/blob/main/REVIEW.md) as a historical assessment. Release validation should include installation from the actual npm tarball and live smoke runs on the versions you intend to support; no publication occurs merely by running setup or tests.

See [CHANGELOG.md](CHANGELOG.md) for fixes and validation. To prepare a release, run the checks above and `npm pack`, inspect the resulting tarball, then publish the reviewed version with `npm publish --access public --tag preview` using an authorized npm account. The package is not published by setup, build or tests.
