## Your ticket

Read the local ticket: `{{ticket}}`.

**Files in scope:** {{files}}

**Context and decisions:**{{context}}

**Checks to run:**{{checks}}

## How to work

1. Read the ticket, project instructions, scoped code and the specified context. Resolve glossary paths from project configuration: current upstream conventions use `GLOSSARY.md` and optional `GLOSSARY-MAP.md`; older projects may use `CONTEXT.md`. Read relevant ADRs if they exist.
2. Follow the agreed testing approach. For behavior changes, test meaningful acceptance criteria through public behavior where practical; do not invent redundant tests for prose or mechanical edits. Record visual/manual criteria honestly.
3. Match existing naming and patterns. Keep changes inside scope and preserve pre-existing owner or other-worker edits.
4. Run the exact permitted checks. Report failures and distinguish a demonstrated pre-existing failure from an assumption. Ask the lead about checks requiring forbidden operations.
5. Review your scoped diff against every criterion and rule. Write your report, then stop editing. Fleet owns implementation under a no-commit contract; do not invoke upstream `/implement` unchanged.
