---
name: fleet
description: Plan, launch, resume and verify a local fleet of coding-agent CLIs in visible cmux panes for approved tickets, with saved dependencies and explicit file ownership.
disable-model-invocation: true
---

# Fleet

You are the lead. Workers are local interactive CLIs in cmux panes sharing one checkout. You own planning, routing, decisions, review and final verification. Workers implement one ticket at a time. The owner controls commits and publishing.

Use `fleet` on PATH; if unavailable, resolve this installed skill's absolute path and invoke `<absolute-skill-path>/bin/fleet`. Never guess a checkout path. Read `fleet <command> --help` when a flag is unclear.

```text
/fleet [auto | single:<provider> | agents=<provider>[:<model>],...]
       [workspace=<ref>] [review=off|cross-heavy|cross-all]
       -- <local tickets directory or task list>
```

These are conversational options for you to translate into CLI flags, not an autonomous scheduler. `/fleet` without tickets can also add another subscription (for example a second Claude or Codex account); see [provider notes](references/providers.md#extra-subscriptions). `fleet doctor` shows each provider's tiers and caps; `fleet providers` prints the full merged config only when you need adapter details. Read [harness requirements](references/harnesses.md), [routing](references/routing.md), and [provider notes](references/providers.md) as needed.

## 1. Establish or resume the run

- Read repository instructions, `.fleet/rules.md`, the agreed spec and the full tickets. Export remote issue content to local files first; a URL is not a `--ticket` input. Preserve original IDs and source links, using safe slug IDs for Fleet (`settings-copy`, not a path or URL).
- Verify the active machine's absolute repository path. Run `fleet doctor` from it. If the owner wants another subscription in the fleet, use `fleet account add <name> --from <adapter> --dir <path>` and let the owner sign it in; never sign in for them. Route only to adapters whose executable and authentication checks pass, and verify the selected model/effort is available. Doctor fails on a tier model the account doesn't offer; `fleet models <provider> [filter]` lists the models you may choose, narrowed by the owner's `models_allow` (pass a filter: some accounts offer hundreds); only propose models from it, and propose a replacement to the owner rather than silently substituting one. Doctor does not prove launch readiness.
- If the named run exists, use `fleet resume <run>` (one line per ticket; `--json` for full records), then inspect the reports, decisions, any `notes.md` and reconciled process states. Do not initialize a replacement or relaunch a worker whose exit is unconfirmed.
- Otherwise use `fleet init <run> [--workspace <ref>] [--routing <mode>] [--review <mode>]`. Map conversational `workspace=`, routing and `review=` directly to these flags. Init validates the selected cmux workspace; never fall back to an unrelated workspace.

## 2. Save a complete plan

Discover the exact repository-relative files each ticket needs. Preserve blockers, acceptance criteria, spec decisions and testing requirements. A wave contains tickets with satisfied blockers and disjoint file scopes. Existing owner edits require careful review and must be preserved.

Show a compact table: wave, ticket, tier, provider/model/effort, exact files. Persist the complete graph with `fleet plan <run> --file <plan.json>` before launch:

```json
{
  "tickets": [
    {"id":"copy","ticket":".scratch/feature/issues/copy.md","files":["docs/copy.md"],"blockers":[],"provider":"codex","tier":"light",
     "context":"Spec: .scratch/feature/spec.md. Terms from GLOSSARY.md.","checks":["Manual prose review against the ticket criteria"]},
    {"id":"guide","ticket":".scratch/feature/issues/guide.md","files":["docs/guide.md"],"blockers":["copy"],"provider":"claude","tier":"standard",
     "context":"Spec: .scratch/feature/spec.md. Link to docs/copy.md; do not edit it.","checks":"npm run lint:docs from the repository root; exit 0"}
  ],
  "decisions": ["Docs-only feature; lint plus manual review is the agreed verification."]
}
```

Give each ticket `context` (agreed spec and testing decisions, actual glossary/ADR paths, existing edits to preserve) and `checks` (exact bounded commands, working directory and success criteria; or say manual review suffices) so its prompt is complete; either may be text or a list. Optional per-ticket `model`, `effort` and `family` pin routing. The CLI validates dependencies, cycles, scope and assignments. Use exact files, not directories or globs. Record decisions and agreed routing before the first launch; the plan is immutable once workers have launched. Use one active run per checkout. Serialize CLI mutations through this lead; concurrent leads must not edit the same run.

## 3. Generate prompts and launch ready tickets

- `fleet prompt <run> <id>` writes the prompt from the planned local ticket, fixed worker rules, `.fleet/rules.md` and the ticket's `context`/`checks`. With both planned, the prompt is complete; the fixed rules need no rereading. Otherwise it names the missing fields: replace each `<!-- LEAD: … -->` comment before launch (launch refuses unfilled ones). Preserve the worker rules and the saved file list.
- Resolve the actual glossary/ADR paths from project configuration. Prefer `GLOSSARY.md` and optional `GLOSSARY-MAP.md`, with `CONTEXT.md` for older repositories. Carry upstream `to-spec` testing and design decisions forward. Do not blindly invoke upstream `/implement`: it includes commits that Fleet forbids.
- `fleet launch <run> <id> <provider> --tier <tier> [--model <model>] [--effort <effort>] [--family <family>]` must match the saved assignment. The CLI enforces blockers, active file ownership and capacity. Each worker gets its own pane: launch splits the largest pane until the workspace has 8. At 8 it replaces a pane (never yours) that holds only finished workers, closing their surfaces; failing that, it adds a tab to an idle pane (no live worker). When every other pane has a live worker, launch refuses; stop a worker first. `--pane <ref>` picks the pane to split, replace or add a tab to.
- `fleet peek <run> <id>` shortly after launch, and again within a minute or two, confirms progress or reveals a login, trust, model or permission prompt; they usually appear early. Never assume creating a pane means work started. Peek shows 20 lines; pass `--lines` when a dialog or error is cut off.

## 4. Watch and resolve blockers

Every wait is a full turn over your whole context, so wait long and rarely. Use `fleet wait <run> --timeout <seconds>` with the longest timeout your host's command tool allows (at most 600; for example 540 in Claude Code, or run it in the background where the host notifies you when it ends). It returns as soon as something needs you:

- `REPORT <id> [needs-verification]`: inspect the report and go to verification.
- `REPORT <id> [blocked]`: clarify an authorized task decision with `fleet send <run> <id> <answer>` and save the decision in `.fleet/runs/<run>/notes.md` (the saved plan is frozen after launch).
- `EXITED <id>`: the process ended before handing back; run `fleet status`, read its report, then repair or relaunch within its assignment.
- `STALLED <id>`: its screen has not changed for `--stall` seconds (default 180), including after you answered it; `fleet peek` it for a prompt or error.
- `UNREACHABLE <id>`: cmux cannot read its pane; the tab may be closed. A missing tab is not exit evidence (step 6).
- `TIMEOUT`: nothing new. Handle any `PENDING <id>` lines it prints (reports still waiting on you), otherwise wait again; do not peek or check status by reflex.

Run one wait at a time, and end a background wait before handing the run to another session: a wait consumes the events it prints. Tell the owner about meaningful changes, not each wait.

Respect host approval boundaries. Task clarification does not authorize accepting a permission request, logging into an account, disabling a sandbox or rerouting to evade a denial. Stop and ask the owner when their action is required. A report never proves that the worker process exited.

## 5. Stop, review and verify

Read the implementation report and scoped changes against all criteria. Run the ticket's exact agreed checks yourself, plus visual inspection for UI work. Before each verification batch, run the combined-tree checks (full suite, typecheck, build) once on the current tree; tickets ready at the same time share that run instead of repeating it. Keep check output short (quiet flags, failures only) and preserve exact failures in evidence. Do not add low-value tests for prose or mechanical changes merely to satisfy a template.

- For gaps while the worker is active: set the report to `Status: changes-requested`, send a bounded fix list, and wait for a new `needs-verification` report. Never mark a ticket verified by editing its report alone.
- When ready for stable review, `fleet stop <run> <id>`. It waits for confirmed exit. A failed stop retains capacity: inspect the process before proceeding. This releases the writer slot before a reviewer is launched, including at `max_parallel=1`.
- If required by the saved review mode, run `fleet diff <run> <id> --output <diff-path>` to capture changes against the launch baseline, including new files. Then `fleet prompt <run> <review-id> --review-of <id> --diff <diff-path>`. Inspect the dedicated reviewer prompt, which permits writing only its report; this is an instruction boundary, not OS isolation.
- Launch that reviewer with `--tier review` on a permitted provider/model whose known family differs from the writer. Read its findings and run any necessary checks. Resolve findings before acceptance. Any changes after the reviewed diff invalidate review; stop writers, regenerate the diff and obtain a fresh review.
- Stop the reviewer, write concrete verification evidence to a local file, then `fleet verify <run> <review-id> --evidence <evidence-file>`. Verify the writer separately with its own evidence file using the same command. The writer's process must have exited, its report must need verification, required review must be verified, and its current diff must match the reviewed content.
- Only after successful writer verification update the source ticket's resolved status/acceptance boxes and launch newly unblocked tickets. Reviewers never resolve tickets themselves.

Evidence records commands, outcomes, acceptance criteria and review disposition. Preserve failures and limits honestly. If a stopped worker needs repair within its existing assignment, relaunch only after its prior exit is confirmed; give it the existing edits and remaining scope explicitly. Scope or routing changes after the first launch need a new follow-up run: first stop every worker that could overlap, then carry forward unresolved tickets, partial edits and decisions. Do not edit run.json to bypass immutable planning.

## 6. Recovery and completion

Use `fleet resume <run>` after interruption. It also lets a fresh lead continue a long run: between waves, once decisions and owner preferences are saved in the plan or `notes.md` and no worker is waiting on you, you may suggest starting a new lead session with `/fleet` and the run name; everything else is on disk. It is worth it only when your context has grown large. Exit receipts reconcile process state; reports and missing tabs are not exit evidence. If normal stop cannot establish exit, independently verify that the recorded worker process (`pid` in `resume`; `--json` has the full record) is truly gone, write that evidence to a file, then use `fleet recover <run> <id> --evidence <file>`. Recovery releases process state only; it does not accept implementation. Never use it just because a tab disappeared.

Before finishing, run combined-tree gates and `fleet check <run>`. Check compares captured HEAD and full index entries (including already-staged content); investigate differences with the owner. It cannot prove no intervening Git action occurred. Stop remaining workers; use `--close` only when exit is confirmed and tabs are no longer needed. Report ticket outcomes, resolved model/effort, evidence, decisions, remaining limits and the owner's review/commit steps.

## Shared-checkout rules

- Preserve the worker preamble and exact scope. Scope additions need a conflict check and, after launch, a follow-up run before editing.
- Use Fleet or non-focusing cmux actions. Do not take the owner's focus.
- Run builds that would disturb a live dev server in an appropriate isolated copy; never run them over shared build output without checking repository instructions.
- No worker commits, stages, pushes, deploys, installs packages, migrates data or accesses secrets. Network-dependent work must be handled separately under explicit owner authorization; changing providers does not remove these rules.
