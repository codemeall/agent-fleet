# Routing

## Modes

| Mode | Set by | Pool |
|---|---|---|
| `auto` (default) | nothing, or `auto` | every provider `fleet doctor` reports `ready`, ordered by `defaults.prefer` |
| `single:<provider>` | `single:claude-co` | that provider only; tiers still choose the model |
| pinned | `agents=claude-co:opus,codex,cursor:grok-4.7-high` | exactly those; a listed model overrides the tier model for every ticket that worker takes |

The owner's explicit choice always wins over this reference. A pinned provider that is not ready is a blocker to report, never a silent swap.

## Tiers

Rate each ticket by what a wrong answer costs, not by its length.

| Tier | Ticket shape |
|---|---|
| **heavy** | crosses modules or layers, touches auth, money, data integrity or a schema, owns a new abstraction others build on, or carries unresolved judgment calls |
| **standard** | one feature slice with clear acceptance criteria inside known patterns |
| **light** | mechanical: copy, config, a rename, test repair, a small isolated fix |
| **review** | a read-only cross-family review of another worker's diff |

## Auto assignment

Per wave, in wave order:

1. Take heavy tickets first, then standard, then light.
2. Give each ticket the first provider in `prefer` that is ready and under its `max`, skipping one that already holds a heavy ticket in this wave while another provider with a heavy tier is free. **Spread** heavy work; stack light work.
3. Model and effort come from that provider's tier (`fleet providers`).
4. If the wave has more tickets than capacity, the rest wait for the next free slot. Queueing beats raising caps.

`prefer` encodes quota strategy. The default spends the second Claude subscription first, then Codex and Cursor, keeping the owner's primary Claude and Antigravity as overflow.

## Review

- `cross-heavy` (default): every heavy ticket gets a read-only reviewer from a different `family` than its writer after the lead's own verification.
- `cross-all`: every ticket.
- `off`: the lead's verification only.

The reviewer prompt says: read-only, review the diff of the named files against the ticket's acceptance criteria and the repo rules, and report findings ranked by severity in its report file.

## Fallback

A provider that fails mid-run (login expired, quota, crash): record it, move its unstarted tickets to the next provider in `prefer` with the same tier, and name the substitution in the final report. A ticket already half-done stays with its tab. Relaunch that tab after the owner fixes the login, telling the worker that the partial edits in the tree are its own.
