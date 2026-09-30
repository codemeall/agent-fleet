---
name: fleet
description: Plan, launch, resume and verify a local fleet of coding-agent CLIs in visible cmux tabs for approved tickets, with saved dependencies and explicit file ownership.
disable-model-invocation: true
---

# Fleet

You are the lead. Workers are local interactive CLIs in cmux tabs sharing one checkout. You own planning, routing, decisions, review and final verification. Workers implement one ticket at a time. The owner controls commits and publishing.

Use `fleet` on PATH; if unavailable, resolve this installed skill's absolute path and invoke `<absolute-skill-path>/bin/fleet`. Never guess a checkout path. Read `fleet <command> --help` when a flag is unclear.

```text
/fleet [auto | single:<provider> | agents=<provider>[:<model>],...]
       [workspace=<ref>] [review=off|cross-heavy|cross-all]
       -- <local tickets directory or task list>
```

These are conversational options for you to translate into CLI flags, not an autonomous scheduler. To add another subscription to the fleet (for example a second Claude or Codex account), run `fleet account add <name> --from <adapter> --dir <path>` without tickets; see [provider notes](references/providers.md#extra-subscriptions). Defaults come from `fleet providers`. Read [harness requirements](references/harnesses.md), [routing](references/routing.md), and [provider notes](references/providers.md) as needed.

## 1. Establish or resume the run

- Read repository instructions, `.fleet/rules.md`, the agreed spec and the full tickets. Export remote issue content to local files first; a URL is not a `--ticket` input. Preserve original IDs and source links, using safe slug IDs for Fleet (`settings-copy`, not a path or URL).
- Verify the active machine's absolute repository path. Run `fleet doctor` from it. If the owner wants another subscription in the fleet, use `fleet account add <name> --from <adapter> --dir <path>` and let the owner sign it in; never sign in for them. Route only to adapters whose executable and authentication checks pass, and verify the selected model/effort is available. Doctor does not prove launch readiness.
- If the named run exists, use `fleet resume <run>`, then inspect its complete saved plan, reports, decisions, any `notes.md` and reconciled process states. Do not initialize a replacement or relaunch a worker whose exit is unconfirmed.
- Otherwise use `fleet init <run> [--workspace <ref>] [--routing <mode>] [--review <mode>]`. Map conversational `workspace=`, routing and `review=` directly to these flags. Init validates the selected cmux workspace; never fall back to an unrelated workspace.

## 2. Save a complete plan

Discover the exact repository-relative files each ticket needs. Preserve blockers, acceptance criteria, spec decisions and testing requirements. A wave contains tickets with satisfied blockers and disjoint file scopes. Existing owner edits require careful review and must be preserved.

Show a compact table: wave, ticket, tier, provider/model/effort, exact files. Persist the complete graph with `fleet plan <run> --file <plan.json>` before launch:

```json
{
  "tickets": [
    {"id":"copy","ticket":".scratch/feature/issues/copy.md","files":["docs/copy.md"],"blockers":[],"provider":"codex","tier":"light"},
    {"id":"guide","ticket":".scratch/feature/issues/guide.md","files":["docs/guide.md"],"blockers":["copy"],"provider":"claude","tier":"standard"}
  ],
  "decisions": ["Checks and context are specified in each generated prompt."]
}
```

Optional per-ticket `model`, `effort` and `family` pin routing. The CLI validates dependencies, cycles, scope and assignments. Use exact files, not directories or globs. Record decisions and agreed routing before the first launch; the plan is immutable once workers have launched. Use one active run per checkout. Serialize CLI mutations through this lead; concurrent leads must not edit the same run.

## 3. Generate prompts and launch ready tickets

- `fleet prompt <run> <id>` uses the planned local ticket (`--ticket <local-path>` may supply it explicitly). Inspect the generated prompt, preserve the worker rules, and fill its context and exact checks, then remove the LEAD instruction comment before launch. Its file list must match the saved plan.
- Resolve the actual glossary/ADR paths from project configuration. Prefer `GLOSSARY.md` and optional `GLOSSARY-MAP.md`, with `CONTEXT.md` for older repositories. Carry upstream `to-spec` testing and design decisions forward. Do not blindly invoke upstream `/implement`: it includes commits that Fleet forbids.
- `fleet launch <run> <id> <provider> --tier <tier> [--model <model>] [--effort <effort>] [--family <family>]` must match the saved assignment. The CLI enforces blockers, active file ownership and capacity.
- `fleet peek <run> <id>` after launch confirms progress or reveals a login, trust, model or permission prompt. Never assume creating a tab means work started.

## 4. Watch and resolve blockers

Use `fleet wait <run>` (45-second default; `--timeout` is bounded at 60 seconds) and report meaningful progress within your host's time limits. On `needs-verification`, inspect the report and go to verification. On `blocked`, clarify an authorized task decision with `fleet send <run> <id> <answer>` and save the decision in `.fleet/runs/<run>/notes.md` (the saved plan is frozen after launch). On timeout, inspect `fleet status` and `fleet peek` for stale or missing reports.

Respect host approval boundaries. Task clarification does not authorize accepting a permission request, logging into an account, disabling a sandbox or rerouting to evade a denial. Stop and ask the owner when their action is required. A report never proves that the worker process exited.

## 5. Stop, review and verify

Read the implementation report and scoped changes against all criteria. Run the exact agreed checks yourself, plus appropriate combined-tree checks and visual inspection for UI work. Do not add low-value tests for prose or mechanical changes merely to satisfy a template.

- For gaps while the worker is active: set the report to `Status: changes-requested`, send a bounded fix list, and wait for a new `needs-verification` report. Never mark a ticket verified by editing its report alone.
- When ready for stable review, `fleet stop <run> <id>`. It waits for confirmed exit. A failed stop retains capacity: inspect the process before proceeding. This releases the writer slot before a reviewer is launched, including at `max_parallel=1`.
- If required by the saved review mode, run `fleet diff <run> <id> --output <diff-path>` to capture changes against the launch baseline, including new files. Then `fleet prompt <run> <review-id> --review-of <id> --diff <diff-path>`. Inspect the dedicated reviewer prompt, which permits writing only its report; this is an instruction boundary, not OS isolation.
- Launch that reviewer with `--tier review` on a permitted provider/model whose known family differs from the writer. Read its findings and run any necessary checks. Resolve findings before acceptance. Any changes after the reviewed diff invalidate review; stop writers, regenerate the diff and obtain a fresh review.
- Stop the reviewer, write concrete verification evidence to a local file, then `fleet verify <run> <review-id> --evidence <evidence-file>`. Verify the writer separately with its own evidence file using the same command. The writer's process must have exited, its report must need verification, required review must be verified, and its current diff must match the reviewed content.
- Only after successful writer verification update the source ticket's resolved status/acceptance boxes and launch newly unblocked tickets. Reviewers never resolve tickets themselves.

Evidence records commands, outcomes, acceptance criteria and review disposition. Preserve failures and limits honestly. If a stopped worker needs repair within its existing assignment, relaunch only after its prior exit is confirmed; give it the existing edits and remaining scope explicitly. Scope or routing changes after the first launch need a new follow-up run: first stop every worker that could overlap, then carry forward unresolved tickets, partial edits and decisions. Do not edit run.json to bypass immutable planning.

## 6. Recovery and completion

Use `fleet resume <run>` after interruption. Exit receipts reconcile process state; reports and missing tabs are not exit evidence. If normal stop cannot establish exit, independently verify that the recorded worker process is truly gone, write that evidence to a file, then use `fleet recover <run> <id> --evidence <file>`. Recovery releases process state only; it does not accept implementation. Never use it just because a tab disappeared.

Before finishing, run combined-tree gates and `fleet check <run>`. Check compares captured HEAD and full index entries (including already-staged content); investigate differences with the owner. It cannot prove no intervening Git action occurred. Stop remaining workers; use `--close` only when exit is confirmed and tabs are no longer needed. Report ticket outcomes, resolved model/effort, evidence, decisions, remaining limits and the owner's review/commit steps.

## Shared-checkout rules

- Preserve the worker preamble and exact scope. Scope additions need a conflict check and, after launch, a follow-up run before editing.
- Use Fleet or non-focusing cmux actions. Do not take the owner's focus.
- Run builds that would disturb a live dev server in an appropriate isolated copy; never run them over shared build output without checking repository instructions.
- No worker commits, stages, pushes, deploys, installs packages, migrates data or accesses secrets. Network-dependent work must be handled separately under explicit owner authorization; changing providers does not remove these rules.
