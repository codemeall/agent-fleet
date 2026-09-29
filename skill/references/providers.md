# Provider gotchas

Launch flags, quit keys, login checks and tier models live in `providers.toml` and `fleet providers`. This file holds what the config can't say.

## claude and claude-co

- `claude-co` is the owner's second subscription: the same binary with `CLAUDE_CONFIG_DIR=$HOME/.claude-co`. It has **no plugins**, so its prompt must spell out the whole workflow. The ticket template already does.
- Its login expires. The pane then shows a login prompt; only the owner can run `/login` there.
- Workers run `--permission-mode auto`. If the auto-mode classifier blocks something reasonable, answer in the pane or give the step to another worker.

## codex

- Workers run `-s workspace-write -a never`: they write inside the repo, have no network, and can't reach cmux. Tickets that need the network are routed elsewhere.
- Use the full model ids (`gpt-6-sol`, `gpt-6-luna`); the account rejects bare family names. Efforts: `low`, `medium`, `high`, `xhigh`.
- The owner can switch the model in the tab (`/model`). Afterwards, `fleet send` the worker a line saying its partial edits in the tree are its own.

## cursor (cursor-agent)

- Effort is part of the model id (`grok-4.7-high`, `kimi-k3-high`, `muse-spark-1.3-max`), and the ids drift between releases. Check with `cursor-agent --list-models` before pinning a new one.
- Muse Spark runs **through Cursor**; there is no separate Muse subscription.
- Workers run `--force --trust`: no approval prompts. The preamble is the only fence, so give Cursor tickets whose files are cleanly scoped.

## agy (Antigravity)

- The only route to Gemini (3.1 Pro, 3.8 Flash). It also serves Claude 4.6 and GPT-OSS without spending a Claude or OpenAI subscription. See `agy models`.
- Workers run `--mode accept-edits`, which accepts edits but asks before running commands. Expect approval prompts for test runs: peek at its tab more often than the others, or pin a stronger mode in config once you trust it.
- Quit is a double `ctrl+c`.

## Planned: grok (Grok Build) and muse (Muse Code)

These are off in `providers.toml`. To turn one on:
- check its interactive launch syntax, model flag, quit key and a no-model login check;
- fill in its adapter;
- set `enabled = true`;
- run `fleet doctor`.

The `grok` on PATH inside cmux is a cmux-bundled binary. Confirm it is Grok Build before relying on it.
