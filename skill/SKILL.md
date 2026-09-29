---
name: fleet
description: Lead a fleet of coding-agent CLIs (Claude Code, claude-co, Codex, Cursor, Antigravity) in visible cmux tabs that implement a batch of tickets in parallel in one checkout.
disable-model-invocation: true
---

# Fleet

You are the **lead**. Each worker is an interactive agent CLI in its own cmux tab, working one ticket in the shared checkout. You plan, route, launch, watch and verify. The owner commits; nobody in the fleet does.

`fleet` (on PATH) does the mechanics. Run `fleet <cmd> --help` for flags. Invocation:

```
/fleet [auto | single:<provider> | agents=<provider>[:<model>],...] [workspace=<ref>] [review=off|cross-heavy|cross-all] -- <tickets dir | task list>
```

Unset options come from `~/.config/agent-fleet/config.toml` (`fleet providers` prints the merged config). Routing rules: [references/routing.md](references/routing.md). Provider gotchas: [references/providers.md](references/providers.md).

## 1. Preflight

- Read [references/harnesses.md](references/harnesses.md) for the harness you are running in, and meet its lead requirements.
- `fleet doctor` → every provider you plan to route to reads `ready`. A `NOT LOGGED IN` provider leaves the pool until the owner logs it in; say so in the plan.
- `fleet init <run>` (run = feature slug) records the git baseline for the no-commit check.

Done when doctor is green for the routed providers and the run dir exists.

## 2. Plan waves

For every ticket: its blockers, the files it will touch (search the code, don't guess from the title), and its tier (heavy / standard / light, per the routing reference).

A **wave** is a set of tickets whose blockers are all resolved and whose file sets are pairwise disjoint. Tickets that share a hot file (a layout, a shared header, a schema) go in different waves, blockers-first.

Show the owner one table: wave · ticket · tier · provider:model · files. Then launch. Owner corrections override the plan.

Done when every ticket sits in exactly one wave with a provider, and no two tickets in a wave share a file.

## 3. Prompt and launch

- `fleet prompt <run> <id> --ticket <path>` scaffolds `prompts/<id>.md` from the preamble, the repo's `.fleet/rules.md` and the ticket template. Fill its three LEAD fields: files in scope, context the worker can't find by looking, and exact check commands.
- `fleet launch <run> <id> <provider> --tier <tier>` opens a background tab named `<id> · provider:model` and passes the worker one line: read the prompt file. Caps (`max_parallel`, per-provider `max`) are enforced.
- A few seconds later, `fleet peek <run> <id>` on each new tab shows the agent working, not stuck on a trust, login or model prompt.

Done when every worker in the wave is visibly working.

## 4. Watch

Loop `fleet wait <run>`. It blocks up to 9 minutes and exits `0` with `REPORT <id> [<status>]` lines when a report reaches `needs-verification` or `blocked`, or `2` on timeout. On each wake:

- `needs-verification` → step 5.
- `blocked` → answer the question with a decision (`fleet send <run> <id> <answer>`) and record the decision where the project keeps them.
- Timeout → `fleet status <run>`, then `fleet peek` any tab whose report is missing or stale. Answer approval or model prompts yourself. Only the owner can fix a login or permission prompt: `cmux notify --title fleet --body "<id> needs <what>"`.

Done when every ticket in the run is resolved or explicitly handed back to the owner.

## 5. Verify (lead only)

Per ticket: read the report and the diff of its files, then run the repo's gates from `.fleet/rules.md` yourself: type-check, tests, and a build from a snapshot copy when the repo says so. Add a visual check when the ticket touches UI.

Whenever you edit a report, rewrite its `Status:` line first. That line is what `fleet wait` wakes on.

- Gaps → set the report to `Status: changes-requested`, `fleet send` the fix list to the same worker, and tell it to set `needs-verification` again when done. Then back to step 4. Fix trivial things yourself.
- Pass → set the report to `Status: verified` and append `## Lead verification` (commands + results). Set the ticket's own `Status:` to resolved and check its acceptance boxes.
- `review=cross-*` → after your pass, launch a read-only reviewer from a different model family (`--tier review`) on the ticket's diff, and act on its findings.
- Then launch whatever the resolved ticket unblocked.

Done when every acceptance criterion has evidence and every gate is green on the combined tree.

## 6. Close

- `fleet check <run>` → `OK`. Anything else means someone committed or staged. Stop and tell the owner.
- `fleet stop <run> --all` (add `--close` only once the owner no longer needs the tabs).
- Report to the owner: each ticket with provider:model and result, gates run, decisions made, follow-ups, and the owner's next steps (review, commit, deploy order).

## Guardrails

- Workers get the preamble verbatim. Soften nothing; add repo rules through `.fleet/rules.md`.
- Drive cmux only through `fleet` or non-focusing verbs (`send`, `send-key`, `read-screen`, `new-surface --focus false`). The owner's focus belongs to the owner.
- Build output shared with a live dev server (for example `.next`) stays untouched: build from an rsync snapshot with symlinked `node_modules`.
- Commits, pushes, deploys, migrations, provisioning and key rotation belong to the owner.
