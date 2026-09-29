# Provider adapters

`providers.toml` is the shipped adapter configuration; `fleet providers` prints it merged with personal overrides. Adapter command templates are trusted local executable configuration, not safe inputs from tickets. Model, effort and prompt substitutions are shell-quoted by the runtime. Keep account setup separate from model data.

All model IDs and efforts are examples tied to accounts and CLI versions. Before routing, check the local CLI's model list/help and authenticate through the owner's normal process. `doctor` checks binary presence and login signals, including the login command's exit status; it does not spend a model call or guarantee model access. A passing doctor is a preflight check, not an end-to-end compatibility claim.

## Claude Code

The default `claude` adapter uses the normal account and interactive permission controls. `opus` and `sonnet` are example aliases. Effort support depends on the installed CLI and model. Do not respond to a permission denial by switching account or bypassing the host's controls.

`claude-co` is a disabled example of a second account using `CLAUDE_CONFIG_DIR=$HOME/.claude-co`. Enable it only after explicitly configuring and authenticating that account. It must receive a self-contained prompt; do not assume the same plugins exist under both configurations. Both accounts use Anthropic-family models, so switching between them does not satisfy cross-family review.

## Codex

The example adapter uses `workspace-write` with `on-request` approval. Effective network and filesystem permissions still come from the host configuration; neither the adapter nor the preamble guarantees OS isolation. Fleet's worker contract prohibits network calls and package installation regardless of those capabilities. Request owner help for a necessary permission instead of weakening the sandbox.

Use model IDs and effort levels supported by the authenticated account. The shipped OpenAI IDs are examples. Model changes must be reflected in the plan and recorded worker metadata; do not silently switch a running worker's model in its UI and leave routing records stale.

## Cursor

The default launches `cursor-agent` with the chosen model and repository trust, retaining interactive permission handling; it does not use `--force`. A trusted workspace is not permission for an arbitrary command. Inspect trust or command prompts as part of launch verification.

Cursor can serve several families. The example tiers identify xAI (`grok-…`), Moonshot (`kimi-…`) and Meta (`muse-…`) separately. Use `cursor-agent --list-models` to confirm your account's exact IDs; parameterized IDs must remain a single argument. A model override needs accurate family metadata, especially for cross-family review. A different CLI name alone is not evidence of a different family.

## Optional and experimental adapters

Antigravity (`agy`) is disabled by default. Its example tiers describe Google models, but the adapter itself may serve multiple families. Validate its binary, authentication, model availability, command permissions and shutdown sequence before enabling it. No live compatibility claim is made.

The disabled `grok` and `muse` entries are incomplete placeholders, not supported providers. Verify that a binary with the expected name is actually the intended product; fill in a no-model authentication check, known family/tier metadata and tested launch/shutdown commands before use.

For every new adapter, test a harmless local ticket, multiple waves, a required review at capacity, process exit confirmation and recovery. Keep unavailable adapters out of auto routing. Login expiry or quota exhaustion can require owner intervention; they are not reasons to ignore the worker contract.
