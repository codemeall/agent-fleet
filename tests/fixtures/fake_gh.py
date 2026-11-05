"""A stand-in for GitHub CLI, used by tests/test_import.py. Tests put a `gh` wrapper that runs it first on PATH.

    fake_gh.py <argv...>

It records every call's argv as one JSON line in $FAKE_GH_LOG, before anything else, so a test can check that
each call is a read. It answers only the reads `fleet import` makes, from the JSON file named by $FAKE_GH_DATA:

    {"signed_in": true, "default_repo": "owner/repo",
     "issues": [{"repo": "owner/repo", "number": 7, "title": ..., "body": ..., "labels": [{"name": ...}],
                 "assignees": [{"login": ...}], "milestone": {"title": ...} | null, "state": "OPEN",
                 "updatedAt": ...}]}

  gh issue view <N> --repo <owner/repo> --json <fields>
  gh issue list [--repo <owner/repo>] [--state open|closed|all] [--limit N] [--label L]... [--milestone M] --json <fields>

Signed out ("signed_in": false), it fails as gh does: the `gh auth login` hint on stderr and exit status 4.
Anything else fails loudly with `FAKE gh: ...` and exit status 2.
"""
import json
import os
import sys
from pathlib import Path


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def options(args: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    takes = {"--repo", "-R", "--json", "--state", "-s", "--limit", "-L", "--label", "-l", "--milestone", "-m"}
    positional, opts = [], {}
    it = iter(args)
    for arg in it:
        if arg in takes:
            value = next(it, None)
            if value is None:
                fail(f"FAKE gh: {arg} needs a value")
            opts.setdefault(arg.lstrip("-"), []).append(value)
        elif arg.startswith("-"):
            fail(f"FAKE gh: unknown flag {arg}")
        else:
            positional.append(arg)
    return positional, opts


def shown(issue: dict, fields: str) -> dict:
    full = {k: v for k, v in issue.items() if k != "repo"}
    full["url"] = f"https://github.com/{issue['repo']}/issues/{issue['number']}"
    out = {}
    for field in fields.split(","):
        if field not in full and field not in ("milestone", "body", "labels", "assignees"):
            fail(f"FAKE gh: field {field} not in the test data")
        out[field] = full.get(field, {"labels": [], "assignees": []}.get(field))
    return out


def main(argv: list[str]) -> None:
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\n")
    data = json.loads(Path(os.environ["FAKE_GH_DATA"]).read_text())
    if argv[:2] not in (["issue", "view"], ["issue", "list"]):
        fail(f"FAKE gh: unsupported command {' '.join(argv[:2])}")
    if not data.get("signed_in", True):
        fail("To get started with GitHub CLI, please run:  gh auth login\n"
             "Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.", 4)
    positional, opts = options(argv[2:])
    if "json" not in opts:
        fail("FAKE gh: fleet import always asks for --json")
    fields = opts["json"][-1]
    repo = (opts.get("repo") or opts.get("R") or [data.get("default_repo")])[-1]
    issues = [i for i in data["issues"] if i["repo"] == repo]
    if argv[1] == "view":
        if len(positional) != 1:
            fail("FAKE gh: issue view takes one number")
        match = [i for i in issues if str(i["number"]) == positional[0]]
        if not match:
            fail(f"GraphQL: Could not resolve to an issue or pull request with the number of {positional[0]}.", 1)
        print(json.dumps(shown(match[0], fields)))
        return
    if positional:
        fail("FAKE gh: issue list takes no arguments")
    state = (opts.get("state") or opts.get("s") or ["open"])[-1]
    labels = opts.get("label", []) + opts.get("l", [])
    milestone = (opts.get("milestone") or opts.get("m") or [None])[-1]
    limit = int((opts.get("limit") or opts.get("L") or ["30"])[-1])
    found = [i for i in issues
             if (state == "all" or i.get("state", "OPEN").lower() == state)
             and all(label in [x["name"] for x in i.get("labels", [])] for label in labels)
             and (milestone is None or (i.get("milestone") or {}).get("title") == milestone)]
    print(json.dumps([shown(i, fields) for i in found[:limit]]))


if __name__ == "__main__":
    main(sys.argv[1:])
