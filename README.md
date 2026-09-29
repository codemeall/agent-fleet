# agent-fleet

A private skill for running a **fleet** of coding-agent CLIs in visible cmux tabs. The agent you are talking to becomes the **lead**:
- it plans waves of tickets;
- it routes each ticket to Claude Code, a second Claude subscription, Codex, Cursor or Antigravity;
- it launches and watches the workers, verifies every ticket itself, and commits nothing.

Use it for multi-ticket work, where each worker takes one ticket and all of them share one checkout. For quick delegation inside a single session, the public `orchestrate-agents` skill (`~/Documents/orchestrator-ai`) is still the tool.

## Install

```sh
./install.sh      # links skill/ into ~/.claude, ~/.claude-co, ~/.codex and ~/.cursor skill dirs, puts `fleet` on PATH, seeds the config
fleet doctor      # cmux reachable, each provider logged in, skill installed per harness
```

Personal settings: `~/.config/agent-fleet/config.toml` (see `config.example.toml`).
Per-repo worker rules: `<repo>/.fleet/rules.md`. Run state lives in `<repo>/.fleet/runs/`, which is gitignored.

## Use

| Lead harness | Command |
|---|---|
| Claude Code (either subscription) | `/fleet -- .scratch/<feature>/issues` |
| Codex CLI | `codex -c sandbox_workspace_write.network_access=true`, then `$fleet -- …` |
| Cursor | `/fleet -- …` |

Routing: `auto` (default), `single:claude-co`, or `agents=codex:gpt-6-sol,cursor:grok-4.7-high`. See `skill/references/routing.md`.

## Layout

```
skill/SKILL.md              lead workflow: preflight → waves → launch → watch → verify → close
skill/providers.toml        adapters: launch flags, quit keys, login checks, tier models
skill/bin/fleet             mechanics CLI (Python 3.11+ standard library only)
skill/references/           routing, provider gotchas, per-harness lead setup
skill/templates/            worker preamble, ticket prompt, report format
tests/                      python3 -m unittest discover tests
```

## Adding a provider (Grok Build, Muse Code)

Fill in its entry in `skill/providers.toml` and set `enabled = true`. The entry needs:
- the interactive launch line;
- the quit keys;
- a no-model login check;
- its tiers.

Then run `fleet doctor` and a smoke launch.
