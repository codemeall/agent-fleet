"""Executable checks in plans, fleet gate and repeatable verify evidence (#25)."""
import fcntl
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class Gate(RuntimeCase):
    def gate_run(self, checks, kind="implement"):
        files = [] if kind == "investigate" else ["owned.txt"]
        self.setup_run(tickets=[{"id": "01", "files": files, "kind": kind, "context": "Agreed scope.",
                                 "checks": checks}])

    def evidence_text(self, ticket="01"):
        return (self.repo / ".fleet/runs/demo/evidence" / f"{ticket}.gate.md").read_text()

    def plan_with(self, checks):
        ticket = self.repo / "01.md"
        ticket.write_text("# Ticket 01\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(ticket), "files": ["owned.txt"],
                                                 "provider": "writer", "checks": checks}]}))
        return plan

    # ---------- plan validation ----------

    def test_plan_saves_mixed_checks_and_the_prompt_shows_both(self):
        (self.repo / "web").mkdir()
        self.gate_run(["Read the prose.", {"run": "npm test", "cwd": "web"}, {"run": "true", "timeout": 5}])
        t = self.state()["tickets"]["01"]
        self.assertEqual(t["commands"], [{"run": "npm test", "cwd": "web", "timeout": 300},
                                         {"run": "true", "cwd": ".", "timeout": 5}])
        text = (self.repo / ".fleet/runs/demo/prompts/01.md").read_text()
        self.assertIn("**Checks to run:**\n\n- Read the prose.\n"
                      "- `npm test` (from web/; Fleet runs it with fleet gate)\n"
                      "- `true` (from the repository root; Fleet runs it with fleet gate)\n", text)

    def test_prose_checks_save_no_commands(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": ["python3 -m unittest", "read it"]},
                                {"id": "02", "files": ["next.txt"], "checks": "npm run lint"}])
        for t in self.state()["tickets"].values():
            self.assertNotIn("commands", t)
        self.assertEqual(self.state()["tickets"]["02"]["checks"], "npm run lint")

    def test_plan_refuses_bad_commands_naming_the_ticket_and_saves_nothing(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        (self.repo / "file.txt").write_text("not a directory\n")
        (self.repo / "sub").mkdir()
        (self.repo / "link").symlink_to(Path(self.temp.name).parent, target_is_directory=True)
        (self.repo / "inlink").symlink_to(self.repo / "sub", target_is_directory=True)
        cases = [({"cwd": "."}, "needs \"run\""), ({"run": "  "}, "needs \"run\""),
                 ({"run": "true", "cwd": "../"}, "without .."), ({"run": "true", "cwd": str(self.repo)}, "without .."),
                 ({"run": "true", "cwd": "link"}, "escapes"), ({"run": "true", "cwd": "inlink"}, "symlink"), ({"run": "true", "cwd": "missing"}, "not a directory"),
                 ({"run": "true", "cwd": "file.txt"}, "not a directory"),
                 ({"run": "true", "timeout": 0}, "timeout"), ({"run": "true", "timeout": 3601}, "timeout"),
                 ({"run": "true", "timeout": "30"}, "timeout"), ({"run": "true", "timeout": 1.5}, "timeout"),
                 ({"run": "true", "timeout": True}, "timeout"), ({"run": "true", "manual": "x"}, "unknown key manual")]
        for command, message in cases:
            with self.subTest(command=command):
                out = self.call("plan", "demo", "--file", str(self.plan_with(["prose", command])), expected=1)
                self.assertIn("01: ", out)
                self.assertIn(message, out)
                self.assertEqual(self.state()["tickets"], {})
        with self.subTest("commands given directly"):
            plan = self.plan_with("prose")
            data = json.loads(plan.read_text())
            data["tickets"][0]["commands"] = [{"run": "rm -rf /", "cwd": ".", "timeout": 1}]
            plan.write_text(json.dumps(data))
            self.assertIn("inside checks", self.call("plan", "demo", "--file", str(plan), expected=1))

    def test_add_takes_command_checks_too(self):
        self.setup_run()
        added = self.repo / "add.json"
        added.write_text(json.dumps({"tickets": [{"id": "later", "task": "Later.", "files": ["later.txt"],
                                                  "provider": "writer", "checks": [{"run": "true"}]}]}))
        self.call("add", "demo", "--file", str(added))
        self.assertEqual(self.state()["tickets"]["later"]["commands"], [{"run": "true", "cwd": ".", "timeout": 300}])
        added.write_text(json.dumps({"tickets": [{"id": "bad", "task": "Bad.", "files": ["bad.txt"],
                                                  "provider": "writer", "checks": [{"run": "true", "cwd": "/"}]}]}))
        self.assertIn("bad: check cwd", self.call("add", "demo", "--file", str(added), expected=1))
        self.assertNotIn("bad", self.state()["tickets"])

    # ---------- gate ----------

    def test_gate_passes_and_records_each_command(self):
        (self.repo / "sub").mkdir()
        self.gate_run([{"run": "true"}, {"run": "pwd", "cwd": "sub"}, "Read the diff."])
        self.launch()
        (self.repo / "owned.txt").write_text("in scope\n")
        before = (self.repo / ".fleet/runs/demo/run.json").read_text()
        out = self.call("gate", "demo", "01")
        path = self.repo / ".fleet/runs/demo/evidence/01.gate.md"
        self.assertIn(f"GATE 01 2/2 passed, scope ok; evidence {path}", out)
        self.assertNotIn("FAIL", out)
        text = self.evidence_text()
        self.assertIn("1. `true`: passed, exit 0, ", text)
        self.assertIn("2. `pwd`: passed, exit 0, ", text)
        self.assertIn("cwd `sub`", text)
        self.assertIn("None: no file outside every ticket's scope", text)
        self.assertIn("2/2 passed, scope ok", text)
        self.assertNotIn("### Output", text)                               # passing output is not kept
        self.assertEqual((self.repo / ".fleet/runs/demo/run.json").read_text(), before)  # no run state changes

    def test_gate_fails_with_the_failing_tail_in_the_evidence(self):
        noisy = "for i in $(seq 1 60); do echo line$i; done; exit 3"
        self.gate_run([{"run": "true"}, {"run": noisy}, {"run": "false"}])
        self.launch()
        out = self.call("gate", "demo", "01", expected=1)
        self.assertIn("GATE 01 1/3 passed, scope ok;", out)
        self.assertIn(f"FAIL {noisy}: exit 3", out)
        self.assertIn("FAIL false: exit 1", out)
        text = self.evidence_text()
        self.assertIn("FAILED, exit 3", text)
        self.assertIn("line60", text)
        self.assertIn("line21\n", text)                                    # the last 40 lines
        self.assertNotIn("line20\n", text)

    def test_gate_kills_a_command_that_times_out(self):
        self.gate_run([{"run": "sleep 5; echo late", "timeout": 1}, {"run": "true"}])
        out = self.call("gate", "demo", "01", expected=1)                  # gate needs no launched worker
        self.assertIn("GATE 01 1/2 passed, scope not checked (not launched yet);", out)
        self.assertIn("FAIL sleep 5; echo late: timed out after 1s", out)
        text = self.evidence_text()
        self.assertIn("FAILED, timed out after 1s, 1.", text)
        self.assertIn("### Output of 1: `sleep 5; echo late` (last 40 lines at most)\n\nNo output.", text)
        self.assertIn("Not launched yet", text)

    def test_gate_runs_without_the_leads_session_or_stdin(self):
        with patch.dict(os.environ, {"CLAUDECODE": "1", "TMUX": "/tmp/x", "FLEET_GATE_KEEP": "kept"}):
            self.gate_run([{"run": 'test -z "$CLAUDECODE$TMUX" && test "$FLEET_GATE_KEEP" = kept && ! read x'}])
            self.call("gate", "demo", "01")

    def test_gate_reports_unowned_changes_and_fails_on_them(self):
        self.commit_other()
        self.gate_run([{"run": "true"}])
        self.launch()
        (self.repo / "other.txt").write_text("stray edit\n")
        out = self.call("gate", "demo", "01", expected=1)
        self.assertIn("GATE 01 1/1 passed, 1 file(s) outside every ticket's scope;", out)
        self.assertIn("SCOPE other.txt (changed)", out)
        self.assertIn("- `other.txt` (changed)", self.evidence_text())

    def test_gate_reports_a_worker_without_a_snapshot(self):
        self.gate_run([{"run": "true"}])
        self.launch()
        (self.repo / ".fleet/runs/demo/snapshots/01.tree.json").unlink()   # launched by an earlier Fleet
        self.assertIn("scope not checked (no whole-tree snapshot)", self.call("gate", "demo", "01"))

    def test_gate_output_is_the_leads_file_and_verify_accepts_it(self):
        self.gate_run([{"run": "true"}])
        self.launch()
        self.hand_back()
        out = self.repo / "gate-01.md"
        self.call("gate", "demo", "01", "--output", str(out))
        self.call("gate", "demo", "01", "--output", str(out))              # a rerun never blames its own output
        self.call("verify", "demo", "01", "--evidence", str(out), "--evidence", str(self.evidence))
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_gate_works_for_an_investigate_ticket(self):
        self.gate_run([{"run": "true"}], kind="investigate")
        self.launch()
        self.assertIn("GATE 01 1/1 passed, scope ok;", self.call("gate", "demo", "01"))

    def test_gate_refuses_prose_checks_reviewers_and_unknown_ids(self):
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"], "checks": "Read it."}])
        self.assertIn("run its checks by hand", self.call("gate", "demo", "01", expected=1))
        self.launch()
        (self.repo / "owned.txt").write_text("in scope\n")
        self.hand_back()
        self.review_prompt()
        self.assertIn("review-01 is a reviewer; gate the ticket it reviews: fleet gate demo 01",
                      self.call("gate", "demo", "review-01", expected=1))
        self.assertIn("no ticket 'nope'", self.call("gate", "demo", "nope", expected=1))

    def test_gate_holds_no_run_lock(self):
        self.gate_run([{"run": "true"}])
        lock = self.repo / ".fleet/runs/.lock"
        with lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)               # another command is mid-change
            self.call("gate", "demo", "01")
            self.assertIn("another Fleet command", self.call("status", "demo", expected=1))
        with patch.object(fleet, "save_run", side_effect=AssertionError("gate saved run state")):
            self.call("gate", "demo", "01")

    # ---------- verify evidence ----------

    def test_verify_records_every_evidence_file_in_order(self):
        self.setup_run()
        self.launch()
        self.hand_back()
        first, empty = self.repo / "a.md", self.repo / ".fleet/runs/demo/evidence/empty.md"
        first.write_text("Gate: 2/2 passed.\n")
        empty.write_text("\n")
        self.assertIn(f"{empty}: evidence file must", self.call(
            "verify", "demo", "01", "--evidence", str(first), "--evidence", str(empty), expected=1))
        self.call("verify", "demo", "01", "--evidence", str(first), "--evidence", str(self.evidence))
        recorded = self.state()["workers"]["01"]["verification_evidence"]
        self.assertEqual(recorded, "Gate: 2/2 passed.\n\n" + self.evidence.read_text().strip())
        report = (self.repo / ".fleet/runs/demo/reports/01.md").read_text()
        self.assertIn("## Lead verification\nGate: 2/2 passed.\n\nInspected the owned diff", report)
        self.assertIn("a.md", self.state()["lead_files"])
