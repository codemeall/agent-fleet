```markdown
# Report {{id}}

Status: needs-verification

## Work or review performed
- Implementation: each file changed and why, one line per file; review: captured diff inspected.

## Acceptance criteria
- [x] criterion: the test, command or location that shows it, in one line
- [ ] criterion: remaining gap or why it could not be checked

## Checks and evidence
- `command`: actual result in one line (exit code, counts); state when a check was not run

## Findings
- Severity, file/location, failure scenario and suggested correction; or no actionable findings with review limits.

## Decisions and blockers
- Decisions made, questions for the lead and any permission or scope blocker.

## Out-of-scope observations
- Relevant issues left untouched.

## Appendix
- Supporting detail only: longer reasoning, output excerpts, per-criterion notes.
```

Right after the `Status:` line, add one `fleet` block: a single JSON object Fleet checks against the run, so the lead opens your prose only when something is off. For example:

```fleet
{"files_changed": ["src/api.py"],
 "checks": [{"run": "npm test", "exit": 0}],
 "criteria": [{"id": 1, "met": true, "where": "tests/api.test.js limit case"}],
 "findings": [{"severity": "low", "file": "src/api.py:40", "confidence": "suspected"}],
 "questions": []}
```

List every file you changed (none for a review or an investigation), every check with its exit code (run each planned command: one reported with `null` refuses acceptance; give each Fleet-run check by its exact command), one criterion per acceptance criterion, severity `high`, `medium` or `low`, confidence `confirmed` or `suspected`, and open questions for the lead. The block only repeats facts: the sections below still carry everything a verifier needs. The `Status:` line stays the only status.

Use one status only: `needs-verification` or `blocked`. The lead may request changes, but only `fleet verify` records acceptance. Do not copy the illustrative checkboxes as evidence.

The lead reads everything above `## Appendix` in every report and opens the appendix only to check a line it doubts. Above it, write what a verifier needs and nothing else: one line per item, no pasted command output, no diff excerpts (the lead has the diff) and no account of how you got there. Every finding, failed or unrun check, decision and blocker goes above it in full; never shorten one to save space. Supporting detail goes in the appendix; leave the section out when there is none.
