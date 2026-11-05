"""Plan validation (#13): every ticket's checks hold a runnable command or a manual marker with its reason."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class PlanChecks(RuntimeCase):
    def plan_with(self, *tickets):
        """A plan file of tickets with these checks, each written as a local ticket file."""
        entries = []
        for n, checks in enumerate(tickets, 1):
            ticket = self.repo / f"{n:02}.md"
            ticket.write_text(f"# Ticket {n:02}\n")
            entries.append({"id": f"{n:02}", "ticket": str(ticket), "files": [f"f{n}.txt"], "provider": "writer",
                            "checks": checks})
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": entries}))
        return str(plan)

    def init(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"))

    # ---------- refuse ----------

    def test_plan_refuses_prose_only_checks_naming_the_ticket_and_both_forms(self):
        self.init()
        for checks in ("npm test", ["Read the diff", "npm run lint"]):
            with self.subTest(checks=checks):
                out = self.call("plan", "demo", "--file", self.plan_with([{"run": "true"}], checks), expected=1)
                self.assertIn("02: its checks are prose only, with no runnable command and no manual marker", out)
                self.assertIn('{"run": "<command>"', out)
                self.assertIn('{"manual": "<one-line reason no command can check it>"}', out)
                self.assertNotIn("warning", out)
                self.assertEqual(self.state()["tickets"], {})

    def test_plan_refuses_empty_checks_naming_the_ticket(self):
        self.init()
        for checks in ("", "  ", []):
            with self.subTest(checks=checks):
                out = self.call("plan", "demo", "--file", self.plan_with(checks), expected=1)
                self.assertIn("01: its checks are empty", out)
                self.assertIn('{"manual": ', out)

    def test_add_refuses_prose_only_checks_too(self):
        self.init()
        added = self.repo / "add.json"
        added.write_text(json.dumps({"tickets": [{"id": "later", "task": "Later.", "files": ["later.txt"],
                                                  "provider": "writer", "checks": "Read it."}]}))
        self.assertIn("later: its checks are prose only", self.call("add", "demo", "--file", str(added), expected=1))
        self.assertEqual(self.state()["tickets"], {})

    def test_an_investigate_ticket_follows_the_same_rule(self):
        self.init()
        plan = self.repo / "plan.json"
        why = {"id": "why", "kind": "investigate", "task": "Why?", "provider": "writer", "checks": "git log"}
        plan.write_text(json.dumps({"tickets": [why]}))
        self.assertIn("why: its checks are prose only", self.call("plan", "demo", "--file", str(plan), expected=1))
        why["checks"] = ["git log", {"manual": "read-only research; the lead reads the report"}]
        plan.write_text(json.dumps({"tickets": [why]}))
        self.call("plan", "demo", "--file", str(plan))
        self.assertEqual(self.state()["tickets"]["why"]["manual"], ["read-only research; the lead reads the report"])

    def test_a_manual_check_needs_a_nonempty_one_line_reason_and_nothing_else(self):
        self.init()
        for marker, message in (({"manual": ""}, "nonempty one-line reason"),
                                ({"manual": "  "}, "nonempty one-line reason"),
                                ({"manual": "line one\nline two"}, "nonempty one-line reason"),
                                ({"manual": 3}, "nonempty one-line reason"),
                                ({"manual": "x", "cwd": "."}, 'takes only "manual"')):
            with self.subTest(marker=marker):
                out = self.call("plan", "demo", "--file", self.plan_with(["Read it", marker]), expected=1)
                self.assertIn("01: ", out)
                self.assertIn(message, out)
                self.assertEqual(self.state()["tickets"], {})

    def test_omitted_checks_still_leave_the_prompt_to_the_lead(self):
        self.init()
        ticket = self.repo / "01.md"
        ticket.write_text("# Ticket 01\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(ticket), "files": ["owned.txt"],
                                                 "provider": "writer"}]}))
        self.call("plan", "demo", "--file", str(plan))
        self.assertIn("fill before launch: context, checks", self.call("prompt", "demo", "01"))

    # ---------- accept ----------

    def test_a_manual_marker_plans_shows_as_prose_and_is_kept_apart_from_commands(self):
        self.init()
        self.call("plan", "demo", "--file", self.plan_with(
            {"manual": "  Visual check of the banner on a phone  "},
            ["Read the copy", {"run": "true"}, {"manual": "Copy reads well"}]))
        tickets = self.state()["tickets"]
        self.assertEqual(tickets["01"]["manual"], ["Visual check of the banner on a phone"])
        self.assertNotIn("commands", tickets["01"])
        self.assertEqual(tickets["02"]["manual"], ["Copy reads well"])
        self.assertEqual(tickets["02"]["commands"], [{"run": "true", "cwd": ".", "timeout": 300}])
        self.call("prompt", "demo", "02")
        text = (self.repo / ".fleet/runs/demo/prompts/02.md").read_text()
        self.assertIn("**Checks to run:**\n\n- Read the copy\n"
                      "- `true` (from the repository root; Fleet runs it with fleet gate)\n"
                      "- Manual: Copy reads well\n", text)

    def test_gate_skips_manual_checks_and_says_so(self):
        self.init()
        self.call("plan", "demo", "--file", self.plan_with([{"run": "true"}, {"manual": "Look at the layout"}],
                                                           {"manual": "Read it"}))
        out = self.call("gate", "demo", "01")
        self.assertIn("GATE 01 1/1 passed, scope not checked (not launched yet), 1 manual check(s) skipped;", out)
        evidence = (self.repo / ".fleet/runs/demo/evidence/01.gate.md").read_text()
        self.assertIn("## Manual checks\n\nSkipped: no command checks these", evidence)
        self.assertIn("- Look at the layout\n", evidence)
        self.assertIn("02 has no runnable checks in the plan (its checks are manual)",
                      self.call("gate", "demo", "02", expected=1))

    # ---------- warn mode ----------

    def test_warn_policy_prints_the_same_message_and_saves(self):
        self.cfg["defaults"]["check_policy"] = "warn"
        self.init()
        out = self.call("plan", "demo", "--file", self.plan_with("Read it.", []))
        self.assertIn("warning: 01: its checks are prose only", out)
        self.assertIn("warning: 02: its checks are empty", out)
        self.assertIn('{"manual": "<one-line reason', out)
        self.assertIn('check_policy is "warn"', out)
        tickets = self.state()["tickets"]
        self.assertEqual(tickets["01"]["checks"], "Read it.")
        self.assertNotIn("checks", tickets["02"])  # empty is saved as omitted: the lead fills its prompt
        self.assertIn("fill before launch: context, checks", self.call("prompt", "demo", "02"))
        added = self.repo / "add.json"
        added.write_text(json.dumps({"tickets": [{"id": "later", "task": "Later.", "files": ["later.txt"],
                                                  "provider": "writer", "checks": "Read it."}]}))
        self.assertIn("warning: later: its checks are prose only", self.call("add", "demo", "--file", str(added)))
        self.assertIn("later", self.state()["tickets"])

    # ---------- the shipped example ----------

    def test_the_shipped_example_plan_plans(self):
        shutil.copytree(ROOT / "examples", self.repo / "examples")
        self.cfg["providers"]["codex"] = dict(self.cfg["providers"]["writer"])
        self.call("init", "demo", "--workspace", "workspace:1", "--routing", "single:codex", "--review", "off",
                  *self.off_reason("off"))
        self.assertIn("saved 3 tickets", self.call("plan", "demo", "--file", "examples/plan.json"))
        for t in self.state()["tickets"].values():
            self.assertEqual(len(t["commands"]), 1)
            self.assertEqual(len(t["manual"]), 1)

