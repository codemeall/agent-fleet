# Routing and review

For parameterized model IDs containing commas, select `single:<provider>` and set `model` in the JSON plan. The conversational `agents=` shorthand is comma-separated and cannot represent commas inside an ID. Quote the same full ID when passing `launch --model`.

The lead chooses assignments; Fleet enforces the saved plan. Persist the routing mode with `init --routing` and every ticket assignment with `plan --file`. Use the same provider, tier and explicit overrides at launch.

| Mode | Pool |
| --- | --- |
| `auto` | Enabled, authenticated adapters in `defaults.prefer` order |
| `single:<provider>` | Only the named adapter; tiers still select models |
| `agents=<provider>[:<model>],...` | Only listed adapters, with the listed model overriding tier models |

An unavailable explicit choice is a blocker to explain, not permission for a silent substitution. `prefer` is an editable ordering, not a claim about price, subscription quotas or model quality. All selected models must be checked against the account's availability.

## Ticket tiers

| Tier | Use |
| --- | --- |
| `heavy` | Cross-module work, authentication, money, data integrity, schemas, new shared abstractions or consequential judgment |
| `standard` | A feature slice with clear criteria and established patterns |
| `light` | Mechanical, tightly scoped changes such as documentation or isolated repairs |
| `review` | Dedicated read-only review of a writer's captured diff |

Take consequential work first, balance ready providers without exceeding global or provider caps, and queue remaining tickets. Never raise caps merely to avoid waiting. Waves require satisfied blockers and disjoint exact file scopes. A `needs-verification` report does not free a slot: the process must exit.

## Model family and review policy

`cross-heavy` (default) requires a different-family reviewer for each heavy implementation ticket. `cross-all` requires one for every ticket; `off` means lead verification alone. Record the chosen mode at run creation.

Family follows the resolved model, not the CLI. Use exact model-family mappings or matching tier metadata; a known single-family adapter can supply its family as a fallback. Mixed adapters require known model metadata. Use an explicit, accurate `family` in the plan or `--family` where necessary. Never invent a family solely to pass the gate.

If the allowed pool cannot provide a known different-family reviewer, tell the owner before implementation. Expand the pool under their routing instruction or obtain an explicit change to `review=off`; never silently downgrade the review policy. In particular, a second Claude account remains Anthropic, while two Cursor models may belong to different families.

Stop the writer before generating its scoped diff. Give the reviewer a separate ID, the original ticket and rules, that exact immutable diff, and only its report as a writable path. Review-only is a prompt contract in a shared checkout, not a security sandbox. Inspect for unauthorized changes. Stop and verify the reviewer before verifying the writer; dependents remain blocked throughout. If the writer's diff changes, redo review against the new diff.

## Failures and plan changes

Before any worker launches, record an unavailable adapter and any owner-authorized substitution in the plan's decisions and update assignments explicitly. After the first launch the plan is immutable: use a new follow-up run for routing or scope changes, stopping all overlapping workers first and carrying forward pending dependencies, partial edits and context. A same-assignment repair may be relaunched only after the old process has exited. A missing tab is insufficient proof. Use `resume` to reconcile receipts, normal `stop` where possible, and evidence-backed `recover` only after independently verifying the process is gone.

Never change providers to circumvent a permission denial. Work requiring installs, secrets, network access or infrastructure changes falls outside the default worker contract; isolate and ask the owner to handle or explicitly authorize it separately.
