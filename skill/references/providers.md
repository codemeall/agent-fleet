# Provider adapters

`providers.toml` is the shipped adapter configuration; `fleet providers` prints it merged with personal overrides. Adapter command templates are trusted local executable configuration, not safe inputs from tickets. Model, effort and prompt substitutions are shell-quoted by the runtime. Keep account setup separate from model data.

All model IDs and efforts are examples tied to accounts and CLI versions. Before routing, authenticate through the owner's normal process. `doctor` checks binary presence and login signals, including the login command's exit status, and checks every tier model against the account's list where the CLI can list models (`models` field: Cursor, Antigravity, OpenCode, and Codex through `codex debug models`, which also lists each model's efforts). For Codex doctor also shows the configured default from `config.toml`; Claude's `opus`/`sonnet` aliases follow the current model. Doctor spends no model call and does not guarantee model access. A passing doctor is a preflight check, not an end-to-end compatibility claim.

## Turning providers on and off

Which providers are on is the owner's decision. `fleet doctor` shows each one as `off`, as a sign-in result, or as `PLACEHOLDER`.

| Shipped | Providers |
| --- | --- |
| On | `claude`, `codex`, `cursor` |
| Off, supported | `agy` (Antigravity), `opencode`, `claude-co` (a second Claude account) |
| Off, placeholder | `grok`, `muse` ([not supported](#placeholder-adapters)) |

```bash
fleet provider enable agy       # turn a provider on
fleet doctor agy                # then check its binary, sign-in and tier models
fleet provider disable cursor   # turn one off, a shipped one included
```

`fleet provider enable|disable <name>` sets `enabled` under `[providers.<name>]` in `~/.config/agent-fleet/config.toml` (or `$FLEET_CONFIG`), creating the file or the section if needed. Every other line stays as it was, including comments (a comment on the `enabled` line is kept as written) and line endings; a symlinked config is written through to its target with its permissions. It does nothing when the provider is already in that state. If it cannot make the edit safely, for example when the config sets the provider through a dotted key or an inline table, it changes nothing and says so; the owner then edits that line by hand. A non-boolean value such as `enabled = "yes"` makes every Fleet command fail until the owner fixes it by hand. Editing the file by hand does the same thing as the command:

```toml
[providers.agy]
enabled = true

[providers.cursor]
enabled = false
```

- **Auto routing** uses enabled, authenticated providers in `defaults.prefer` order. Turning a provider on does not add it there; add its name to `prefer` under `[defaults]` yourself. `single:<name>` and `agents=` routing can name it without that; `fleet init` refuses such routing when a named provider is off or a placeholder.
- **Turning one off** stops new plans, launches, steers and review launches that name it. Workers already running on it keep running until they finish or you stop them. To move a live run off a failing provider, steer it ([skill](../SKILL.md#steering-routing)) rather than turning the provider off mid-run. A run steered to a provider that is then turned off cannot launch until `fleet steer reset <run>` or a steer elsewhere.
- **Sign-in** is separate. Enabling a provider never signs it in: the owner signs the CLI in, then runs `fleet doctor <name>`.
- **Placeholders** cannot be turned on until they are filled in ([below](#placeholder-adapters)).

### Agent rules

A Fleet lead, or any other agent working in the session, follows these rules:

- Turn a provider on or off only when the owner asks for that provider in the session ("turn on Antigravity", "stop using Cursor"). A routing request such as `single:agy` that names a provider that is off is not that request: tell the owner it is off and ask.
- Use `fleet provider enable|disable <name>`, never a hand edit of the owner's config, then run `fleet doctor <name>` and report the result. If the command refuses, show the owner its message; don't edit the file. Adding to `defaults.prefer` is also the owner's call: tell them the line, don't write it.
- Never turn a provider on to get around a failure, a usage limit, a permission denial or a login prompt, and never turn one off to route a run somewhere else. Steer within the run's routing, or ask the owner.
- Never sign a CLI in, answer its login or trust prompt, or set `placeholder = false`.

## Folder trust

Claude Code, Codex and Antigravity ask once per folder whether to trust it. With `auto_trust = true` under `[defaults]` in the owner's `~/.config/agent-fleet/config.toml` (off by default), launch marks the repo trusted first, the way each adapter's `trust` field says:

| `trust` | CLI | What launch does |
|---|---|---|
| `claude-config` | Claude Code and its extra accounts | sets the project's `hasTrustDialogAccepted` in that account's `.claude.json` (`CLAUDE_CONFIG_DIR`, else `~`) |
| `codex-flag` | Codex | passes `-c projects={"<repo>"={trust_level="trusted"}}`: trust for that session only, nothing saved |
| `agy-settings` | Antigravity | adds the repo to `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json` |

Cursor always launches with `--trust`; OpenCode asks nothing. Launch never creates a CLI's file: a CLI that has not run yet, or a file it cannot read or write, gets one stderr line and the worker shows its trust screen as before. `auto_trust` is the owner's decision. The lead never turns it on, and never answers a trust, login or permission prompt with `fleet send`.

## Keeping models current

Tier models are pinned on purpose: plans record the exact model and cross-family review needs a known family. When a provider ships new models, `fleet models <provider> [filter]` lists what the account offers, then override the tier in the user config (with `family` for a mixed adapter) and run `fleet doctor`. No Fleet release is needed. Shipped defaults are refreshed in releases and noted in the changelog.

## Allowed models

`models_allow` is the owner's list of models the lead may choose, either a list (`["opus", "sonnet"]`) or a table of model ID = family. With a live list (Codex, Cursor, Antigravity, OpenCode) it narrows that list; without one (Claude) it is the list. Tier models always count as allowed, so overriding a tier needs no list edit; the list governs per-ticket pins and review models. `fleet plan` and review launches refuse models outside it, and its families feed cross-family review. Doctor fails when a tier model is outside it and warns when an allowed model is no longer offered. A user's `models_allow` replaces the shipped one; an adapter that `extends` another inherits it. `fleet models <provider> --all` shows the account's full list.

| Provider | Shipped `models_allow` |
| --- | --- |
| Claude | `opus`, `sonnet` (the CLI cannot list models; add `fable` or full IDs such as `claude-opus-5-5` if your plan offers them) |
| Cursor, OpenCode | A starter set with families; the account offers far more |
| Codex, Antigravity | None: the live list is used as is |

`model_families` (model ID = family) names the family of models in a mixed adapter without narrowing what the lead may choose. Antigravity ships one for every model it lists, so its Claude and GPT-OSS models count as Anthropic and OpenAI in cross-family review.

`efforts` lists the effort levels a CLI accepts when it cannot report them per model. Claude ships `low, medium, high, xhigh, max` from `claude --help`; that is CLI-wide, so whether a given model accepts a level is only proven at launch. Tier efforts, plans and review launches outside the list are refused. Antigravity ships `low, medium, high, max` from `agy --help`. Codex reports efforts per model in its catalog; Cursor encodes effort in the model ID; OpenCode's interactive TUI has no effort flag, so its tiers carry none.

Codex's list comes from `codex debug models`, a debug command that refreshes its catalog over the network and may change between Codex versions. If it fails, doctor reports "model list failed" without failing. When the catalog marks a tier model for retirement, doctor warns (`RETIRING: gpt-5.5 retires 2026-10-14, switch to gpt-5.6-sol`) without failing, and `fleet models codex` shows the date and replacement.

## Claude Code

The default `claude` adapter uses the normal account and interactive permission controls. `opus` and `sonnet` are example aliases. Effort support depends on the installed CLI and model. Do not respond to a permission denial by switching account or bypassing the host's controls.

`claude-co` is a disabled example of a second account using `CLAUDE_CONFIG_DIR=~/.claude-co`. Turn it on with `fleet provider enable claude-co` only after the owner has signed that account in. It must receive a self-contained prompt; do not assume the same plugins exist under both configurations. Both accounts use Anthropic-family models, so switching between them does not satisfy cross-family review.

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

A Codex worker that needs an approval waits in its pane until the owner answers, because the lead never accepts a prompt for them. Codex 0.159+ can route those prompts to its own reviewer instead: `approvals_reviewer = "auto_review"` (`--approve-for-me` on the command line). It reviews sandbox escalations against the policy and does not widen what the sandbox allows. It is the owner's choice, made once in the user config rather than per run, and it is not a way past a decision the owner owes:

```toml
[providers.codex]
launch = "{bin} -m {model} -c model_reasoning_effort={effort} -c check_for_update_on_startup=false -c approvals_reviewer=auto_review -s workspace-write -a on-request {prompt}"
```

This is approval routing inside Codex. It is unrelated to Fleet's cross-family review of a ticket's diff, which `review=` controls.

Use model IDs and effort levels supported by the authenticated account. The shipped OpenAI IDs are examples. Model changes must be reflected in the plan and recorded worker metadata; do not silently switch a running worker's model in its UI and leave routing records stale.

## Cursor

The default launches `cursor-agent` with the chosen model and repository trust, retaining interactive permission handling; it does not use `--force`. `--trust` is in the shipped launch line, so Cursor is trusted with or without `auto_trust`. A trusted workspace is not permission for an arbitrary command. Inspect trust or command prompts as part of launch verification.

Cursor can serve several families. The example tiers identify xAI (`grok-…`), Moonshot (`kimi-…`) and Meta (`muse-…`) separately. Use `cursor-agent --list-models` to confirm your account's exact IDs; parameterized IDs must remain a single argument. A model override needs accurate family metadata, especially for cross-family review. A different CLI name alone is not evidence of a different family.

## Antigravity

`agy` ships disabled. To use it, install and sign in to `agy`, run `fleet provider enable agy`, then `fleet doctor agy`. It was checked live with agy 1.3.0: launch with an interactive first prompt (`-i`), follow-up input, `/exit` back to the shell, `doctor` and `fleet models agy`. `/exit` is the verified quit sequence.

- It runs in `accept-edits` mode: file edits are approved automatically, shell commands still ask. A worker's checks (even `git status`) wait at a prompt until someone answers it in the pane, so watch `fleet peek` after launch.
- On first use in a folder it asks whether to trust it. With `auto_trust` on, launch adds the repo to its trusted workspaces first ([Folder trust](#folder-trust)); otherwise the owner answers, once per checkout.
- The model ID names a thinking level (`gemini-3.8-flash-medium`) and `--effort` sets reasoning effort; tiers set both.
- Its account also offers Claude and GPT-OSS models. `model_families` labels every listed model (Gemini as Google), so an Antigravity Claude model cannot review a Claude writer. A model agy adds later needs an entry there, or `--family`, before it can take part in cross-family review.
- It cannot name a session at launch (only resume one with `--conversation`), so `fleet launch --resume` does not apply.

## OpenCode

`opencode` ships disabled; turn it on with `fleet provider enable opencode`, then run `fleet doctor opencode`. It was checked live with opencode 1.18.34: launch through the TUI's `--prompt` (submitted at once, the session stays interactive), follow-up input, `/exit` back to the shell, `doctor`, `fleet models opencode`, and a Fleet launch that ended in an in-scope change and a complete report.

- Model IDs are `provider/model`. The shipped tiers and login check assume OpenRouter (`opencode auth login`). `opencode models` lists only providers with credentials, so `login = "opencode models openrouter"` with `login_ok = "openrouter/"` is a real sign-in check; point both at the provider you use.
- It serves many families, so the adapter is `mixed`: every tier and `models_allow` entry carries its family. The shipped tiers use Zhipu, Alibaba and DeepSeek models, which gives cross-family review options outside Anthropic, OpenAI and Google.
- Effort: the TUI takes no `--variant` (only `opencode run` does), so tiers carry no effort.
- Permissions: OpenCode's default build agent allows every tool without asking, except paths outside the workspace and repeated identical calls (`opencode debug agent build` shows the resolved rules). Fleet's worker contract is the only limit unless your `opencode.json` sets `permission`; tighten it there, not in the adapter.
- Its sessions get IDs OpenCode picks, so `fleet launch --resume` does not apply.

## Placeholder adapters

`grok` and `muse` are placeholders, not supported providers. No Grok or Muse CLI has been checked, so their binary name (`grok`, `muse`), launch flags and `/quit` are guesses, and they have no sign-in check, model list or tiers. They ship with `placeholder = true`:

- `fleet doctor` reports an enabled placeholder as `PLACEHOLDER` and fails.
- `fleet provider enable`, `fleet plan`, `fleet launch` and `fleet steer provider` refuse it.

**Grok and Muse models work today through Cursor.** The `cursor` adapter, on by default, offers `grok-4.7-high`, `grok-4.7-high-fast` and `muse-spark-1.3-max`. Its heavy and light tiers use Grok, and its review tier uses Muse. `fleet models cursor grok` and `fleet models cursor muse` list what your account offers. Use `agents=cursor:muse-spark-1.3-max` to pin one.

To turn a placeholder into a working adapter, the owner fills it in under `[providers.grok]` (or `muse`) in the user config:

1. Confirm that the installed binary is the intended product, and set `bin`.
2. Set `launch` from the CLI's help: `{bin}`, `{model}`, `{effort}` if it has an effort flag, and `{prompt}` as an interactive first prompt that leaves the session open.
3. Set `quit` to the key sequence that returns to the shell, checked in a real pane.
4. Set `login` and `login_ok` to a command that spends no model call and prints a known string only when signed in.
5. Set `models` if the CLI can list models; otherwise set `models_allow`.
6. Add `tiers.heavy`, `tiers.standard`, `tiers.light` and `tiers.review` with models the account offers, plus `efforts` if the CLI takes one and `family` (on each tier for a mixed adapter).
7. Set `placeholder = false`, then run `fleet provider enable <name>` and `fleet doctor <name>`.

Then test it as below before routing real work to it.

For every new adapter, test a harmless local ticket, multiple waves, a required review at capacity, process exit confirmation and recovery. Keep unavailable adapters out of auto routing. Login expiry needs the owner. On quota exhaustion the lead may resume after the reset or steer within the run's routing (skill step 1); otherwise it needs the owner. Neither is a reason to ignore the worker contract.
