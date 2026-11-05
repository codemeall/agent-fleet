"""A worker's lifecycle and run state: what plan and add save, what launch refuses and leaves behind when it fails,
what send, status and the recovery advice say, and what the checkout's lock and hold leave in Git's view."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class Lifecycle(RuntimeCase):
    def setUp(self):
        super().setUp()
        # These tests cover launch and state mechanics with header-less reports; tests/test_quality_bar.py covers
        # "strict".
        self.cfg["defaults"]["acceptance"] = "warn"
        self.typed = []  # every item list typed into a pane; quitting a worker writes its exit receipt

        def keys(workspace, surface, items):
            self.typed.append(items)
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]) and items == ["/quit", "enter"]:
                    self.receipt(i)
                    return

        typer = patch.object(fleet, "send_keys", side_effect=keys)
        typer.start()
        self.addCleanup(typer.stop)

    # ---------- helpers ----------

    def run_file(self, *parts):
        return self.repo.joinpath(".fleet/runs/demo", *parts)

    def init(self, review="off"):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", review, *self.off_reason(review))

    def plan(self, tickets, decisions=(), expected=0):
        for t in tickets:
            t.setdefault("provider", "writer")
            for key in [k for k, v in t.items() if v is None]:  # None leaves the field out of the plan
                del t[key]
        path = self.repo / "plan.json"
        path.write_text(json.dumps({"tickets": tickets, "decisions": decisions}))
        return self.call("plan", "demo", "--file", str(path), expected=expected)

    def add(self, tickets, decisions=(), expected=0):
        for t in tickets:
            t.setdefault("provider", "writer")
        path = self.repo / "add.json"
        path.write_text(json.dumps({"tickets": tickets, "decisions": decisions}))
        return self.call("add", "demo", "--file", str(path), expected=expected)

    def edit_state(self, change):
        """Change run.json by hand, as a run saved by an earlier Fleet left it."""
        state = self.state()
        change(state)
        self.run_file("run.json").write_text(json.dumps(state))

    def no_effort_provider(self):
        """An adapter whose launch line needs an effort its tiers never give: build_launch refuses it, after launch
        has taken its snapshots and set the old report aside."""
        writer = self.cfg["providers"]["writer"]
        self.cfg["providers"]["bare"] = dict(writer, tiers={tier: {"model": "bare-model", "family": "openai"}
                                                            for tier in writer["tiers"]})

    # ---------- 1. launch holds a launched, unverified ticket's files ----------

    def test_launch_refuses_a_ticket_sharing_the_files_of_a_launched_unverified_one(self):
        self.setup_run(tickets=[{"id": "a", "files": ["owned.txt"]}, {"id": "b", "files": ["b.txt"]}])
        # Planned before plan sequenced such tickets: b shares a's file, and neither waits on the other.
        self.edit_state(lambda s: s["tickets"]["b"].update(files=["owned.txt"]))
        self.prompt("b", "--force")
        self.launch("a")
        (self.repo / "owned.txt").write_text("a's change\n")
        self.hand_back("a")
        out = self.launch("b", expected=1)
        self.assertIn("files overlap a, which has launched and is not verified, so its diff still awaits review; "
                      "verify a, or drop it (fleet drop demo a --reason …), first", out)
        self.assertNotIn("b", self.state()["workers"])
        self.assertFalse(self.run_file("snapshots", "b.json").exists())
        self.verify("a")
        self.launch("b")  # a's files are free once it is verified

    def test_plan_sequences_tickets_that_share_files_as_add_does(self):
        self.init()
        out = self.plan([{"id": "a", "task": "A.", "files": ["owned.txt"]},
                         {"id": "b", "task": "B.", "files": ["owned.txt", "b.txt"]}], expected=1)
        self.assertIn("b: files overlap unverified ticket a; add a to its blockers", out)
        self.assertEqual(self.state()["tickets"], {})
        self.assertFalse(self.run_file("tasks").exists())
        self.plan([{"id": "a", "task": "A.", "files": ["owned.txt"], "blockers": ["b"]},
                   {"id": "b", "task": "B.", "files": ["owned.txt", "b.txt"]}])  # sequenced either way round
        self.assertEqual(list(self.state()["tickets"]), ["a", "b"])

    # ---------- 2. a failed launch leaves nothing behind ----------

    def test_a_refused_relaunch_keeps_the_report_it_would_have_set_aside(self):
        self.no_effort_provider()
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.hand_back()
        self.call("steer", "provider", "demo", "bare")
        self.assertIn("this provider's launch needs an effort", self.launch(expected=1))
        self.assertEqual(fleet.parse_status(self.run_file("reports", "01.md").read_text()), "needs-verification")
        self.assertEqual(list(self.run_file("reports").glob("*.previous")), [])
        self.assertIn("needs-verification", self.call("status", "demo"))
        self.call("steer", "reset", "demo")
        self.verify()

    def test_a_refused_first_launch_takes_no_baseline_the_next_launch_would_reuse(self):
        self.no_effort_provider()
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.call("steer", "provider", "demo", "bare")
        self.launch(expected=1)
        self.assertFalse(self.run_file("snapshots", "01.json").exists())
        self.assertFalse(self.run_file("snapshots", "01.tree.json").exists())
        self.call("steer", "reset", "demo")
        (self.repo / "owned.txt").write_text("owner's edit after the refusal\n")
        self.launch()
        (self.repo / "owned.txt").write_text("owner's edit after the refusal\nworker's line\n")
        self.hand_back()
        diff = self.call("diff", "demo", "01")
        self.assertIn("+worker's line", diff)
        self.assertNotIn("owner baseline", diff)  # the owner's edit is the baseline, not the worker's work

    # ---------- 3. a re-plan never launches an old plan's prompt ----------

    def test_replanning_removes_the_prompts_written_for_the_earlier_plan(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        out = self.plan([{"id": "01", "task": "Change c instead.", "files": ["c.txt"]}])
        self.assertIn("removed the prompts written for the earlier plan (01, 02); write them again with fleet prompt "
                      "demo <id>", out)
        self.assertEqual(self.state()["prompts"], {})
        self.assertEqual(list(self.run_file("prompts").iterdir()), [])
        self.assertIn("write the prompt first with fleet prompt", self.launch("01", expected=1))
        out = self.launch("02", expected=1)  # a ticket the re-plan removed: a refusal, never a bare KeyError
        self.assertIn("write the prompt first with fleet prompt", out)
        self.prompt("01")
        self.assertIn("c.txt", self.run_file("prompts", "01.md").read_text())
        self.launch("01")

    def test_a_refused_replan_keeps_the_prompts(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.plan([{"id": "01", "task": "Loop.", "files": ["c.txt"], "blockers": ["01"]}], expected=1)
        self.assertIn("01", self.state()["prompts"])
        self.launch()

    def test_replanning_a_fan_out_after_its_prompt_is_not_refused(self):
        self.init()
        fan = {"id": "q", "kind": "investigate", "task": "Why does it fail?", "fanout": ["writer", "reviewer"],
               "context": "Read the logs.", "checks": [{"manual": "read-only research"}]}
        path = self.repo / "plan.json"
        path.write_text(json.dumps({"tickets": [fan]}))
        self.call("plan", "demo", "--file", str(path))
        self.call("prompt", "demo", "q--writer")
        self.call("plan", "demo", "--file", str(path))  # the same plan again: its investigator IDs are still free
        self.assertEqual(self.state()["prompts"], {})

    def test_launch_refuses_a_prompt_written_for_other_files(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.edit_state(lambda s: s["tickets"]["01"].update(files=["c.txt"]))  # as an earlier Fleet's re-plan left it
        out = self.launch(expected=1)
        self.assertIn("01's prompt was written for owned.txt, but 01 now owns c.txt; rewrite it with fleet prompt "
                      "demo 01 --force", out)
        self.assertNotIn("01", self.state()["workers"])
        self.prompt("01", "--force")
        self.launch()

    def test_a_grant_after_the_prompt_still_launches_as_grant_says(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.assertIn("regenerate it with fleet prompt demo 01 --force before its next launch",
                      self.call("grant", "demo", "01", "extra.txt", "--reason", "the helper lives there"))
        self.launch()
        self.assertIn("extra.txt", json.loads(self.run_file("snapshots", "01.json").read_text()))

    def test_launch_names_a_prompt_whose_ticket_is_gone(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.edit_state(lambda s: s["tickets"].pop("02"))  # its prompt outlived it, as an earlier Fleet's re-plan left
        out = self.launch("02", expected=1)
        self.assertIn("02 is not a ticket of this run's plan, so 02's prompt was written for an earlier one; "
                      "fleet resume demo lists the tickets", out)

    # ---------- 4. plan and add save only plan fields ----------

    def test_plan_and_add_drop_the_fields_fleet_keeps_itself(self):
        self.init()
        copied = {"id": "01", "task": "Fix it.", "files": ["owned.txt"], "status": "verified", "review_rounds": 2,
                  "launched_at": 1.0, "closed_at": 2.0, "plan": {"status": "approved"}, "added_at": "2026-01-01",
                  "grants": [{"files": ["x.txt"], "reason": "old"}], "review_required": True, "merged": {}}
        out = self.plan([copied])
        self.assertIn("note: 01: ignored added_at, closed_at, grants, launched_at, merged, plan, review_required, "
                      "review_rounds, status, which no plan sets", out)
        t = self.state()["tickets"]["01"]
        for key in ("review_rounds", "launched_at", "closed_at", "plan", "added_at", "grants", "merged"):
            self.assertNotIn(key, t)
        self.assertEqual((t["status"], t["review_required"]), ("pending", False))
        self.add([dict(copied, id="02", files=["two.txt"])])
        t = self.state()["tickets"]["02"]
        for key in ("review_rounds", "launched_at", "closed_at", "plan", "grants", "merged"):
            self.assertNotIn(key, t)
        self.assertNotEqual(t["added_at"], "2026-01-01")

    # ---------- 5. an uncertain launch's way out ----------

    def reviewable(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()

    def test_an_uncertain_reviewer_launch_says_it_may_be_running(self):
        self.reviewable()
        with patch.object(fleet.Cmux, "open", side_effect=fleet.FleetError("uncertain launch; inspect cmux")):
            out = self.call("review", "demo", "01", "--settle", "0", expected=1)
        self.assertNotIn("no reviewer is running", out)
        self.assertIn("01 is stopped. review-01 may be running: its launch is saved as launching on unknown. Inspect "
                      "the terminal: a running reviewer hands back as usual; otherwise confirm its exit with fleet "
                      "stop demo review-01, or record that it never started with fleet recover demo review-01 "
                      "--evidence <file>, and then run fleet review demo 01 again", out)
        self.assertEqual(self.state()["workers"]["review-01"]["state"], "launching")

    def test_a_reviewer_launch_refused_before_its_record_still_says_none_runs(self):
        self.reviewable()
        with patch.object(fleet.Cmux, "place", side_effect=fleet.FleetError("no pane to split")):
            out = self.call("review", "demo", "01", "--settle", "0", expected=1)
        self.assertIn("01 is stopped and no reviewer is running; resolve that, then run fleet review demo 01 again",
                      out)

    def test_an_uncertain_writer_relaunch_after_findings_says_it_may_be_running(self):
        self.reviewable()
        self.call("review", "demo", "01", "--settle", "0")
        finding = {"severity": "high", "file": "owned.txt:1", "confidence": "confirmed"}
        header = {"files_changed": [], "checks": [], "criteria": [], "findings": [finding], "questions": []}
        self.run_file("reports", "review-01.md").write_text(
            "# Review\nStatus: needs-verification\n\n```fleet\n" + json.dumps(header) + "\n```\n\nOne finding.\n")
        with patch.object(fleet.Cmux, "open", side_effect=fleet.FleetError("uncertain launch; inspect cmux")):
            out = self.call("send", "demo", "01", "--findings", expected=1)
        self.assertNotIn("did not relaunch", out)
        self.assertIn("01 may be running: its launch is saved as launching on unknown", out)
        self.assertIn("then run fleet send demo 01 --findings again (it saves nothing twice)", out)

    def test_an_uncertain_plan_reviewer_launch_says_it_may_be_running(self):
        self.cfg["defaults"]["max_parallel"] = 2  # the writer keeps running while its plan is reviewed
        self.init()
        self.plan([{"id": "01", "task": "Plan, then change it.", "files": ["owned.txt"], "plan_first": True,
                    "context": "Agreed scope.", "checks": [{"run": "true"}]}])
        self.call("launch", "demo", "--ready")
        self.run_file("reports", "01.md").write_text("# Report 01\n\nStatus: plan-ready\n\n## Plan\n- Edit it.\n")
        with patch.object(fleet.Cmux, "open", side_effect=fleet.FleetError("uncertain launch; inspect cmux")):
            out = self.call("approve", "demo", "01", "--review", expected=1)
        self.assertNotIn("No plan reviewer of 01 is running", out)
        self.assertIn("01's plan still waits. plan-review-01 may be running: its launch is saved as launching", out)
        self.assertIn("and then run fleet approve demo 01 --review again", out)

    def test_an_uncertain_judge_launch_says_it_may_be_running(self):
        self.cfg["defaults"]["max_parallel"] = 3
        self.cfg["providers"]["third"] = dict(self.cfg["providers"]["reviewer"], family="google", tiers={
            tier: {"model": "third-model", "effort": "high", "family": "google"}
            for tier in ("standard", "heavy", "light", "review")})
        self.init()
        self.plan([{"id": "q", "kind": "investigate", "task": "Why is it slow?", "fanout": ["writer", "reviewer"],
                    "context": "Start at owned.txt.", "checks": [{"manual": "read-only research"}],
                    "provider": None}])
        self.call("launch", "demo", "--ready")
        for wid in ("q--writer", "q--reviewer"):
            self.run_file("reports", f"{wid}.md").write_text(f"# Report {wid}\nStatus: needs-verification\n\nFound.\n")
        with patch.object(fleet.Cmux, "open", side_effect=fleet.FleetError("uncertain launch; inspect cmux")):
            out = self.call("judge", "demo", "q", "--settle", "0", expected=1)
        self.assertNotIn("no judge is running", out)
        self.assertIn("q's investigators are stopped. judge-q may be running: its launch is saved as launching", out)
        self.assertIn("and then run fleet judge demo q again", out)

    def test_a_review_prompt_may_not_take_another_tickets_reviewer_id(self):
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"]},
                                                    {"id": "02", "files": ["next.txt"]}])
        for i, f in (("01", "owned.txt"), ("02", "next.txt")):
            self.launch(i)
            (self.repo / f).write_text(f"{i}'s change\n")
            self.hand_back(i)
            self.call("diff", "demo", i, "--output", str(self.repo / f"{i}.diff"))
        self.call("prompt", "demo", "rev", "--review-of", "01", "--diff", str(self.repo / "01.diff"))
        out = self.call("prompt", "demo", "rev", "--review-of", "02", "--diff", str(self.repo / "02.diff"), "--force",
                        expected=1)
        self.assertIn("rev already reviews 01; give this reviewer an ID no other worker uses", out)
        self.assertEqual(self.state()["prompts"]["rev"]["review_of"], "01")
        self.call("prompt", "demo", "rev", "--review-of", "01", "--diff", str(self.repo / "01.diff"), "--force")

    # ---------- 6. send reads exit receipts first ----------

    def test_send_to_a_worker_that_exited_on_its_own_saves_the_message_for_its_next_launch(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.launch()
        self.report()
        self.receipt()  # it exited by itself; no command has read the receipt yet
        self.typed.clear()
        out = self.call("send", "demo", "01", "--changes", "Shorten", "the", "copy.")
        self.assertIn("no worker is running, so it reads it when launched", out)
        self.assertEqual(self.typed, [])
        w = self.state()["workers"]["01"]
        self.assertEqual(w["state"], "exited")
        self.assertNotIn("last_send", w)
        self.assertEqual(fleet.parse_status(self.run_file("reports", "01.md").read_text()), "changes-requested")

    # ---------- 7. status counts against the run's own limit ----------

    def test_status_shows_the_runs_max_parallel(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.cfg["defaults"]["max_parallel"] = 4  # the config changed after init; the run keeps 1
        self.launch()
        self.assertIn("running 1/1", self.call("status", "demo"))

    # ---------- 8. decisions are a list of text ----------

    def test_plan_and_add_refuse_decisions_that_are_not_a_list_of_text(self):
        self.init()
        for decisions in ("Keep the old API.", ["Fine.", 3]):
            with self.subTest(decisions=decisions):
                out = self.plan([{"id": "01", "task": "Fix.", "files": ["owned.txt"]}], decisions, expected=1)
                self.assertIn('decisions must be a list of text, one entry per decision, such as ["Owner: keep the '
                              'old API"]; nothing was saved', out)
                self.assertEqual(self.state()["tickets"], {})
        self.plan([{"id": "01", "task": "Fix.", "files": ["owned.txt"]}], ["Keep the old API."])
        before = self.state()["decisions"]
        out = self.add([{"id": "02", "task": "More.", "files": ["two.txt"]}], "Owner wants it short.", expected=1)
        self.assertIn("decisions must be a list of text", out)
        self.assertEqual(self.state()["decisions"], before)
        self.assertNotIn("02", self.state()["tickets"])

    # ---------- 9. the lock and a hold stay out of Git ----------

    def fleet_status(self):
        return self.git("status", "--porcelain", "--untracked-files=all", "--", ".fleet")

    def test_a_hold_before_any_run_is_gitignored(self):
        self.call("hold", "--reason", "owner is reviewing")
        self.assertTrue((self.repo / ".fleet/runs/hold.json").exists())
        self.assertEqual(self.fleet_status(), "?? .fleet/.gitignore\n")
        self.call("release")

    def test_the_lock_of_a_command_before_init_is_gitignored(self):
        self.assertIn("no run 'demo'", self.call("plan", "demo", "--file", "plan.json", expected=1))
        self.assertTrue((self.repo / ".fleet/runs/.lock").exists())
        self.assertEqual(self.fleet_status(), "?? .fleet/.gitignore\n")
        (self.repo / ".fleet/.gitignore").write_text("notes/")  # the owner's own rule is kept
        self.call("hold")
        self.assertEqual((self.repo / ".fleet/.gitignore").read_text(), "notes/\nruns/\n")
        self.init()
        self.assertEqual((self.repo / ".fleet/.gitignore").read_text(), "notes/\nruns/\n")


if __name__ == "__main__":
    import unittest
    unittest.main()
