# Provider adapters

`providers.toml` is the shipped adapter configuration; `fleet providers` prints it merged with personal overrides. Adapter command templates are trusted local executable configuration, not safe inputs from tickets. Model, effort and prompt substitutions are shell-quoted by the runtime. Keep account setup separate from model data.

All model IDs and efforts are examples tied to accounts and CLI versions. Before routing, authenticate through the owner's normal process. `doctor` checks binary presence and login signals, including the login command's exit status, and checks every tier model against the account's list where the CLI can list models (`models` field: Cursor, Antigravity, and Codex through `codex debug models`, which also lists each model's efforts). For Codex doctor also shows the configured default from `config.toml`; Claude's `opus`/`sonnet` aliases follow the current model. Doctor spends no model call and does not guarantee model access. A passing doctor is a preflight check, not an end-to-end compatibility claim.

## Keeping models current

Tier models are pinned on purpose: plans record the exact model and cross-family review needs a known family. When a provider ships new models, `fleet models <provider> [filter]` lists what the account offers, then override the tier in the user config (with `family` for a mixed adapter) and run `fleet doctor`. No Fleet release is needed. Shipped defaults are refreshed in releases and noted in the changelog.

## Allowed models

`models_allow` is the owner's list of models the lead may choose, either a list (`["opus", "sonnet"]`) or a table of model ID = family. With a live list (Codex, Cursor, Antigravity) it narrows that list; without one (Claude) it is the list. Tier models always count as allowed, so overriding a tier needs no list edit; the list governs per-ticket pins and review models. `fleet plan` and review launches refuse models outside it, and its families feed cross-family review. Doctor fails when a tier model is outside it and warns when an allowed model is no longer offered. A user's `models_allow` replaces the shipped one; an adapter that `extends` another inherits it. `fleet models <provider> --all` shows the account's full list.

| Provider | Shipped `models_allow` |
| --- | --- |
| Claude | `opus`, `sonnet` (the CLI cannot list models; add `fable` or full IDs such as `claude-opus-5-5` if your plan offers them) |
| Cursor | A starter set with families; the account offers far more |
| Codex, Antigravity | None: the live list is used as is |

`efforts` lists the effort levels a CLI accepts when it cannot report them per model. Claude ships `low, medium, high, xhigh, max` from `claude --help`; that is CLI-wide, so whether a given model accepts a level is only proven at launch. Tier efforts, plans and review launches outside the list are refused. Codex reports efforts per model in its catalog; Cursor encodes effort in the model ID.

Codex's list comes from `codex debug models`, a debug command that refreshes its catalog over the network and may change between Codex versions. If it fails, doctor reports "model list failed" without failing. When the catalog marks a tier model for retirement, doctor warns (`RETIRING: gpt-5.5 retires 2026-10-14, switch to gpt-5.6-sol`) without failing, and `fleet models codex` shows the date and replacement.

## Claude Code

The default `claude` adapter uses the normal account and interactive permission controls. `opus` and `sonnet` are example aliases. Effort support depends on the installed CLI and model. Do not respond to a permission denial by switching account or bypassing the host's controls.

`claude-co` is a disabled example of a second account using `CLAUDE_CONFIG_DIR=~/.claude-co`. Enable it only after the owner has signed that account in. It must receive a self-contained prompt; do not assume the same plugins exist under both configurations. Both accounts use Anthropic-family models, so switching between them does not satisfy cross-family review.

Claude workers start with `--session-id` set to a UUID Fleet records, so `fleet launch --resume` can continue the same conversation with `--resume` after a usage limit resets. A session stays with the account that ran it. Other adapters can opt in through the `session` and `resume` fields in `providers.toml`.

## Extra subscriptions

An adapter's `account_env` names the CLI's account-directory variable (`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for Codex). Fleet clears that variable for the launch and the login check unless the adapter's own `env` sets it, so no adapter silently uses the account the lead runs under. An extra account is an adapter that `extends` another and sets `env`:

```toml
[providers.claude-co]
extends = "claude"          # copies every field except `enabled`
enabled = true
env = { CLAUDE_CONFIG_DIR = "~/.claude-co" }
plugins = false
max = 1
```

`fleet account add <name> --from <adapter> --dir <path> [--max N] [--disabled]` appends such a block to the user config without touching existing lines. It never signs in: the owner signs the account in, then runs `fleet doctor <name>`. Doctor fails when two providers report the same signed-in email (Claude Code prints one; Codex does not, so Codex accounts are distinguished only by directory). Cursor has no `account_env`, so a second Cursor account is not supported. If your primary Claude account uses a custom `CLAUDE_CONFIG_DIR`, set it in `[providers.claude] env` because Fleet no longer inherits it.

## Codex

The example adapter uses `workspace-write` with `on-request` approval. Effective network and filesystem permissions still come from the host configuration; neither the adapter nor the preamble guarantees OS isolation. Fleet's worker contract prohibits network calls and package installation regardless of those capabilities. Request owner help for a necessary permission instead of weakening the sandbox.

Use model IDs and effort levels supported by the authenticated account. The shipped OpenAI IDs are examples. Model changes must be reflected in the plan and recorded worker metadata; do not silently switch a running worker's model in its UI and leave routing records stale.

## Cursor

The default launches `cursor-agent` with the chosen model and repository trust, retaining interactive permission handling; it does not use `--force`. A trusted workspace is not permission for an arbitrary command. Inspect trust or command prompts as part of launch verification.

Cursor can serve several families. The example tiers identify xAI (`grok-…`), Moonshot (`kimi-…`) and Meta (`muse-…`) separately. Use `cursor-agent --list-models` to confirm your account's exact IDs; parameterized IDs must remain a single argument. A model override needs accurate family metadata, especially for cross-family review. A different CLI name alone is not evidence of a different family.

## Optional and experimental adapters

Antigravity (`agy`) is disabled by default. Its example tiers describe Google models, but the adapter itself may serve multiple families. Validate its binary, authentication, model availability, command permissions and shutdown sequence before enabling it. No live compatibility claim is made.

The disabled `grok` and `muse` entries are incomplete placeholders, not supported providers. Verify that a binary with the expected name is actually the intended product; fill in a no-model authentication check, known family/tier metadata and tested launch/shutdown commands before use.

For every new adapter, test a harmless local ticket, multiple waves, a required review at capacity, process exit confirmation and recovery. Keep unavailable adapters out of auto routing. Login expiry needs the owner. On quota exhaustion the lead may resume after the reset or steer within the run's routing (skill step 1); otherwise it needs the owner. Neither is a reason to ignore the worker contract.
