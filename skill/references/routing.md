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

The skill's step 2 says when to use `heavy`, `standard` and `light`. `review` is for a dedicated read-only review of a writer's captured diff.

Take consequential work first, balance ready providers without exceeding global or provider caps, and queue remaining tickets. Never raise caps merely to avoid waiting. Waves require satisfied blockers and disjoint exact file scopes. A `needs-verification` report does not free a slot: the process must exit.

## Warm workers

A warm worker takes a follow-up ticket in the session that did an earlier one, so it does not reread the same repo instructions, spec and code. Plan it with `"after": "<id>"`, or launch with `--after <verified-id>` ([warm workers](commands.md#warm-workers)).

Keep a worker warm when:

- the follow-up is `light` or `standard`, and works in the same files or area as the earlier ticket;
- both tickets run on the same provider, model and effort, on an adapter that can resume (Claude);
- the run is shared, not a worktree run.

Start fresh instead for heavy work, for a different area, and when launch warns that the earlier session is past `warm_context_tokens` (150000 by default): context only grows, and answers degrade well before the window is full.

`warm_max` (default 4) caps the tickets one session takes, the first included. Claude Code's docs suggest 5–6 tasks per teammate; the cap stays below that because each ticket also brings its prompt, checks and review fixes. Past the cap, start the next ticket fresh; it can start a new chain.

A warm ticket still waits for the earlier one to be verified, and its reviewer is a separate session, as for any ticket. If its resume fails, relaunch it fresh without `--after`.

## Model family and review policy

`cross-heavy` (default) requires a different-family reviewer for each heavy implementation ticket. `cross-all` requires one for every ticket; `off` means lead verification alone. Record the chosen mode at run creation.

Family follows the resolved model, not the CLI. Use exact model-family mappings, matching tier metadata or the adapter's vendor prefixes (`family_prefixes`); a known single-family adapter can supply its family as a fallback. Mixed adapters require known model metadata. Use an explicit, accurate `family` in the plan or `--family` where necessary. Never invent a family solely to pass the gate.

If the allowed pool cannot provide a known different-family reviewer, tell the owner before implementation. Fleet checks this before any worker launches: `init` refuses a `cross-all` run whose routing pool could review no writer (and warns under `cross-heavy`), and `plan` and `add` refuse each ticket whose required review no provider in the pool could give, using the same test as `fleet review` without capacity. The error names the writer's family, the routing and each provider's reason. The two ways out are the owner's: enable another provider or widen the routing (a new run with `--routing`), or explicitly choose `fleet init <run> --review off --reason "<owner's words>"`. Init refuses `--review off` without `--reason` while the config asks for review, and records the reason in the run's decisions; never silently downgrade the review policy. `fleet review` still checks again when the reviewer launches. In particular, a second Claude account remains Anthropic, while two Cursor models may belong to different families.

The review sequence is in the skill's step 5. Review-only is a prompt contract in a shared checkout, not a security sandbox: inspect for unauthorized changes. Dependents remain blocked until the writer is verified.

## Steering

When a provider is failing, rate-limited or a poor fit mid-run, steer future implementation launches instead of rewriting the plan:

- `fleet steer provider <run> <name>` moves future implementation launches to that provider, at the tier's model and effort (or the model pinned by `agents=`). It must be inside the run's routing.
- `fleet steer model <run> <name>` then picks that provider's model; it must pass `models_allow`. It replaces every tier's model, light tickets included, so prefer steering the provider alone.
- `fleet steer reset <run>` returns to the plan's routing.

Keep passing the plan's provider and tier to `fleet launch`; it prints `steered (<tier>): …` and records what the worker actually ran. Open panes and reviews are never steered, so cross-family review checks the writer's actual family. `fleet resume` shows an active steer.

A worker on a usage limit usually shows as `STALLED`; `fleet peek` confirms it. Stop it, then either relaunch after the reset with `--resume`, which continues the worker's own session (same provider, adapters with `resume` such as Claude), or steer within the run's routing and relaunch fresh with a Repair note (examples 6). A usage limit is not a permission denial: steering inside the run's routing needs no further approval. When neither is possible, tell the owner.

## Failures and plan changes

Before any worker launches, record an unavailable adapter and any owner-authorized substitution in the plan's decisions and update assignments explicitly. After the first launch, follow the skill's rules for follow-up runs, repairs and recovery (steps 5 and 6).

Never change providers to circumvent a permission denial. Work requiring installs, secrets, network access or infrastructure changes falls outside the default worker contract; isolate and ask the owner to handle or explicitly authorize it separately.
