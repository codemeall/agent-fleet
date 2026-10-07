"""Command-level regressions in disposable Git repositories; no live agents/cmux."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import tempfile
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
# The suite may run inside a Claude Code or Codex session; its own context must not leak into Fleet output.
for _var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
    os.environ.pop(_var, None)
loader = importlib.machinery.SourceFileLoader("fleet_runtime_tests", str(ROOT / "skill/bin/fleet"))
spec = importlib.util.spec_from_loader(loader.name, loader)
fleet = importlib.util.module_from_spec(spec)
loader.exec_module(fleet)


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Fleet tests")
        (self.repo / "owned.txt").write_text("owner baseline\n")
        self.git("add", "owned.txt")
        self.git("commit", "-qm", "baseline")
        self.cfg = {"defaults": dict(fleet.DEFAULTS, max_parallel=1), "providers": {}}
        for name, family in (("writer", "openai"), ("reviewer", "anthropic")):
            self.cfg["providers"][name] = {
                "bin": "unused-test-agent", "launch": "{bin} --model {model} --effort {effort} {prompt}",
                "enabled": True, "family": family, "max": 1, "quit": ["/quit", "enter"],
                "tiers": {tier: {"model": name + "-model", "effort": "high", "family": family}
                          for tier in ("standard", "heavy", "light", "review")},
            }
        self.cmux_calls = []
        self.surface = 0
        self.inline_ids = True    # a current cmux prints the new surface's UUID when asked with --id-format both
        self.workspace_id = None  # set to have list-panes name the workspace's UUID, as a current cmux does
        self.panes = [{"ref": "pane:1", "surface_refs": ["surface:lead"], "selected_surface_ref": "surface:lead",
                       "pixel_frame": {"width": 1200, "height": 900}}]
        real_sh = fleet.sh

        def shell(args, **kwargs):
            if isinstance(args, list) and args and args[0] == "cmux":
                return self.fake_cmux(*args[1:])
            return real_sh(args, **kwargs)

        for mocker in (patch.object(fleet, "repo_root", return_value=self.repo),
                       patch.object(fleet, "LAUNCH_SETTLE_S", 0),  # no start-screen pause unless a test asks
                       patch.object(fleet, "load_config", side_effect=lambda: copy.deepcopy(self.cfg)),
                       patch.object(fleet, "cmux", side_effect=self.fake_cmux),
                       patch.object(fleet, "sh", side_effect=shell)):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.evidence = self.repo / "lead-evidence.txt"
        self.evidence.write_text("Inspected the owned diff and confirmed the ticket acceptance criteria and checks.\n")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def fake_cmux(self, *args, **kwargs):
        with_ids = args[:2] == ("--id-format", "both")
        args = args[2:] if with_ids else args
        self.cmux_calls.append(args)
        if args[0] in ("new-surface", "new-split"):
            self.surface += 1
            ref = "surface:" + str(self.surface)
            if args[0] == "new-split":
                self.panes.append({"ref": f"pane:s{self.surface}", "surface_refs": [ref],
                                   "pixel_frame": {"width": 100, "height": 100}})
            for p in self.panes:
                if args[0] == "new-surface" and p["ref"] == args[args.index("--pane") + 1]:
                    p["surface_refs"].append(ref)
            return f"OK {ref} ({self.uuid(ref)}) workspace:1" if with_ids and self.inline_ids else "OK " + ref
        if args[0] == "close-surface":
            for p in self.panes:
                p["surface_refs"] = [r for r in p["surface_refs"] if self.uuid(r) != args[-1]]
        if "list-panes" in args:
            panes = copy.deepcopy(self.panes)
            for p in panes:  # like --id-format both; a pane may pin its own UUIDs
                p.setdefault("id", "uuid-" + p["ref"])
                p.setdefault("surface_ids", [self.uuid(r) for r in p["surface_refs"]])
            return json.dumps({"panes": panes, **({"workspace_id": self.workspace_id} if self.workspace_id else {})})
        if args[:2] == ("--json", "identify"):
            return json.dumps({"caller": {"pane_ref": "pane:1"}})
        return "OK"

    @staticmethod
    def uuid(ref):
        return "uuid-" + ref

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = fleet.main(list(args))
        self.assertEqual(result, expected, f"fleet {' '.join(args)}\n{out.getvalue()}\n{err.getvalue()}")
        return out.getvalue() + err.getvalue()

    def state(self):
        return json.loads((self.repo / ".fleet/runs/demo/run.json").read_text())

    def setup_run(self, review="off", tickets=None):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", review)
        if tickets is None:
            tickets = [{"id": "01", "files": ["owned.txt"]},
                       {"id": "02", "files": ["next.txt"], "blockers": ["01"]}]
        for ticket in tickets:
            path = self.repo / (ticket["id"] + ".md")
            path.write_text("# Ticket " + ticket["id"] + "\nImplement owned change and verify its behavior.\n")
            ticket.update(ticket=str(path), provider="writer", tier="standard")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": tickets, "decisions": ["Use bounded local checks."]}))
        self.call("plan", "demo", "--file", str(plan))
        for ticket in tickets:
            self.prompt(ticket["id"])

    def prompt(self, ticket, *args):
        self.call("prompt", "demo", ticket, *args)
        path = self.repo / ".fleet/runs/demo/prompts" / (ticket + ".md")
        # Fill the same template fields that a lead must complete before launch.
        path.write_text(re.sub(r"<!--.*?-->", "Lead: inspect the diff and execute the agreed local checks.",
                               path.read_text(), flags=re.S))

    def launch(self, ticket="01", provider="writer", tier="standard", expected=0):
        return self.call("launch", "demo", ticket, provider, "--tier", tier, expected=expected)

    def report(self, ticket="01"):
        path = self.repo / ".fleet/runs/demo/reports" / (ticket + ".md")
        path.write_text("# Worker report\nStatus: needs-verification\n\nChecks passed; lead must inspect.\n")

    def receipt(self, ticket="01", returncode=0, token=None):
        worker = self.state()["workers"][ticket]
        Path(worker["exit_file"]).write_text(json.dumps({"token": token or worker["token"], "returncode": returncode}))

    def verify(self, ticket="01", expected=0):
        return self.call("verify", "demo", ticket, "--evidence", str(self.evidence), expected=expected)

    def stop_with_receipt(self, ticket="01"):
        with patch.object(fleet, "send_keys", side_effect=lambda *_: self.receipt(ticket)):
            self.call("stop", "demo", ticket, "--timeout", "1")

    def review_prompt(self):
        diff = self.repo / "review.diff"
        self.call("diff", "demo", "01", "--output", str(diff))
        self.prompt("review-01", "--review-of", "01", "--diff", str(diff))
        return diff

    def test_run_commands_tell_the_lead_its_zone_and_spot_a_compaction(self):
        self.setup_run()
        readings = iter([{"session": "lead-1", "tokens": 131_000, "zone": "dumb", "warn_at": 100_000, "dumb_at": 125_000},
                         {"session": "lead-1", "tokens": 30_000, "zone": "smart", "warn_at": 100_000, "dumb_at": 125_000},
                         {"session": "lead-1", "tokens": 31_000, "zone": "smart", "warn_at": 100_000, "dumb_at": 125_000}])
        with patch.object(fleet, "lead_reading", side_effect=lambda cfg: next(readings)):
            dumb = self.call("status", "demo")
            self.assertIn("LEAD 131K dumb zone", dumb)
            self.assertIn("only when they ask", dumb)
            self.assertIn("LEAD compacted 131K -> 30K: run fleet resume demo", self.call("status", "demo"))
            self.assertNotIn("LEAD", self.call("status", "demo"))
        with patch.object(fleet, "lead_reading", side_effect=RuntimeError("unreadable log")):
            self.assertNotIn("LEAD", self.call("status", "demo"))  # measurement failures never alter a command

    def test_handoff_refuses_until_the_lead_is_at_a_safe_point(self):
        self.setup_run()
        notes = self.repo / ".fleet/runs/demo/notes.md"
        self.assertIn("--no-notes", self.call("handoff", "demo", expected=1))
        pid_file = self.repo / ".fleet/runs/demo/.seen/wait.pid"
        pid_file.write_text(str(os.getpid()))  # a live background wait
        self.assertIn("still running", self.call("handoff", "demo", "--no-notes", expected=1))
        self.assertFalse(notes.exists())
        pid_file.write_text("99999999")  # left behind by a killed wait
        self.call("handoff", "demo", "--no-notes")
        self.assertIn("## Handoff", notes.read_text())

    def test_wait_releases_its_pid_marker(self):
        self.setup_run()
        self.assertIn("TIMEOUT", self.call("wait", "demo", "--timeout", "1", "--interval", "1", expected=2))
        self.assertFalse((self.repo / ".fleet/runs/demo/.seen/wait.pid").exists())

    def test_handoff_saves_notes_and_prints_the_owners_steps_for_the_host(self):
        self.setup_run()
        self.launch()
        self.report()
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "11111111-2222-3333-4444-555555555555"}):
            out = self.call("handoff", "demo", "--note", "Owner prefers Codex for API tickets.")
        notes = (self.repo / ".fleet/runs/demo/notes.md").read_text()
        self.assertIn("(fresh lead)", notes)
        self.assertIn("- Running: 01 (writer). Awaiting the next lead: 01 [needs-verification].", notes)
        self.assertIn("- Owner prefers Codex for API tickets.", notes)
        self.assertIn("/clear", out)
        self.assertIn("/fleet resume demo", out)
        self.assertIn("Lead: end your turn here", out)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "01a10dd0-cc90-7880-884b-11a5b4ba24dc"}):
            out = self.call("handoff", "demo", "--compact", "--no-notes")
        self.assertIn("(compaction)", (self.repo / ".fleet/runs/demo/notes.md").read_text())
        self.assertIn("  /compact\n  then send: Run fleet resume demo", out)

    def test_handoff_counts_an_answered_blocked_report_as_handled(self):
        self.setup_run()
        self.launch()
        path = self.repo / ".fleet/runs/demo/reports/01.md"
        path.write_text("# Worker report\nStatus: blocked\n\nWhich copy should the banner use?\n")
        old = path.stat().st_mtime - 60
        os.utime(path, (old, old))
        self.call("send", "demo", "01", "Use the short copy.")
        out = self.call("handoff", "demo", "--no-notes")
        self.assertIn("Awaiting the next lead: nothing.", out)

    def test_handoff_leaves_running_workers_and_their_events_alone(self):
        self.cfg["defaults"]["max_parallel"] = self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.launch()
        self.launch("02")
        calls, before = len(self.cmux_calls), self.state()["workers"]
        self.call("handoff", "demo", "--no-notes")
        self.assertEqual(len(self.cmux_calls), calls)            # no keys sent, no pane read, closed or replaced
        self.assertEqual(self.state()["workers"], before)
        self.report("02")                                          # a worker hands back while the lead resets
        self.assertIn("REPORT 02 [needs-verification]", self.call("wait", "demo", "--timeout", "0"))

    def test_handoff_lists_a_worker_that_exited_without_handing_back(self):
        self.setup_run()
        self.launch()
        self.receipt(returncode=1)
        out = self.call("handoff", "demo", "--no-notes")
        self.assertIn("Running: none. Awaiting the next lead: 01 [exited, returncode=1, report: no report].", out)

    def test_two_waves_release_capacity_only_after_process_exit_and_acceptance(self):
        self.setup_run()
        self.launch()
        self.report()
        self.assertIn("stop", self.verify(expected=1))
        self.launch("02", expected=1)
        self.stop_with_receipt()
        self.assertFalse(fleet.active(self.state()))
        self.assertIn("blocker", self.launch("02", expected=1))
        self.verify()
        self.launch("02")
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")

    def test_verify_records_when_the_lead_accepted_the_result(self):
        self.setup_run()
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.assertNotIn("verified_at", self.state()["workers"]["01"])
        self.verify()
        worker = self.state()["workers"]["01"]
        self.assertTrue(worker["verified"])
        self.assertRegex(worker["verified_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_cross_family_review_at_capacity_one_unblocks_dependents_after_acceptance(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        self.stop_with_receipt()
        self.assertIn("review", self.verify(expected=1))
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        self.report("review-01")
        self.stop_with_receipt("review-01")
        self.verify("review-01")
        self.assertIn("blocker", self.launch("02", expected=1))
        self.verify()
        self.launch("02")

    def test_failed_quit_keeps_slot_and_prevents_relaunch(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "send_keys", side_effect=fleet.FleetError("socket unavailable")):
            self.assertIn("socket unavailable", self.call("stop", "demo", "01", expected=1))
        self.assertEqual(self.state()["workers"]["01"]["state"], "stop-failed")
        self.assertEqual(len(fleet.active(self.state())), 1)
        self.launch(expected=1)
        self.launch("02", expected=1)

    def test_quit_without_exit_receipt_keeps_slot(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "send_keys"):
            self.call("stop", "demo", "01", "--timeout", "0", expected=1)
        self.assertEqual(len(fleet.active(self.state())), 1)

    def test_recovery_allows_relaunch_and_ignores_an_old_exit_receipt(self):
        self.setup_run()
        self.launch()
        old = self.state()["workers"]["01"]
        self.call("recover", "demo", "01", "--evidence", str(self.evidence))
        self.launch()
        current = self.state()["workers"]["01"]
        self.assertNotEqual(current["token"], old["token"])
        self.receipt(token=old["token"])
        self.call("resume", "demo")
        self.assertEqual(len(fleet.active(self.state())), 1)
        self.receipt(returncode=1)
        self.call("resume", "demo")
        self.assertFalse(fleet.active(self.state()))

    def test_resume_persists_pending_plan_routing_ownership_and_decisions(self):
        self.setup_run()
        self.launch()
        self.receipt(returncode=1)
        output = self.call("resume", "demo")
        self.assertIn("  02  pending  standard  writer:writer-model@high", output)
        self.assertIn("blockers=[01]  ticket=02.md  files=next.txt", output)
        self.assertIn("  - Use bounded local checks.", output)
        full = self.call("resume", "demo", "--json")
        state = self.state()
        self.assertIn('"02"', full)
        self.assertEqual(state["tickets"]["02"]["status"], "pending")
        self.assertEqual(state["tickets"]["02"]["blockers"], ["01"])
        self.assertEqual(state["tickets"]["02"]["files"], ["next.txt"])
        self.assertEqual(state["tickets"]["02"]["provider"], "writer")
        self.assertEqual(state["decisions"], ["Use bounded local checks."])
        self.assertFalse(fleet.active(state))

    def test_check_detects_restaging_an_already_staged_file(self):
        (self.repo / "owned.txt").write_text("owner staged content\n")
        self.git("add", "owned.txt")
        self.setup_run()
        self.call("check", "demo")
        (self.repo / "owned.txt").write_text("worker overwrote and staged content\n")
        self.git("add", "owned.txt")
        self.assertIn("index changed", self.call("check", "demo", expected=1))

    def test_invalid_run_and_worker_ids_do_not_escape(self):
        for value in ("../outside", "/absolute", "foo/bar", ".."):
            self.call("init", value, "--workspace", "workspace:1", expected=1)
        self.assertFalse((self.repo / ".fleet").exists())
        self.setup_run()
        self.call("prompt", "demo", "../escape", expected=1)
        self.assertFalse((self.repo / ".fleet/runs/demo/escape.md").exists())

    def test_ownership_rejects_parent_paths_and_symlinks_outside_repo(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        (self.repo / "ticket.md").write_text("# ticket\n")
        (self.repo / "external").symlink_to(self.repo.parent, target_is_directory=True)
        for value in ("../escape.txt", "/tmp/escape.txt", "external/escape.txt"):
            plan = self.repo / "invalid-plan.json"
            plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": "ticket.md", "provider": "writer", "files": [value]}]}))
            self.call("plan", "demo", "--file", str(plan), expected=1)
        self.assertEqual(self.state()["tickets"], {})

    def test_scoped_diff_preserves_preexisting_edits_and_captures_new_files(self):
        (self.repo / "owned.txt").write_text("owner uncommitted baseline\n")
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt", "new.txt"]}])
        self.launch()
        (self.repo / "owned.txt").write_text("owner uncommitted baseline\nworker addition\n")
        (self.repo / "new.txt").write_text("new implementation\n")
        self.receipt()
        diff = self.call("diff", "demo", "01")
        self.assertIn("+worker addition", diff)
        self.assertIn("+new implementation", diff)
        self.assertNotIn("-owner baseline", diff)

    def test_verified_review_is_invalidated_when_owned_diff_changes(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("first implementation\n")
        self.report()
        self.receipt()
        self.call("resume", "demo")
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        self.report("review-01")
        self.receipt("review-01")
        self.verify("review-01")
        (self.repo / "owned.txt").write_text("changed after review\n")
        self.assertIn("stale", self.verify(expected=1))
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")

    def test_same_family_reviewer_is_rejected_even_with_different_provider(self):
        self.cfg["providers"]["reviewer"]["family"] = "openai"
        self.cfg["providers"]["reviewer"]["tiers"]["review"]["family"] = "openai"
        self.setup_run(review="cross-all")
        self.launch()
        self.receipt()
        self.call("resume", "demo")
        self.review_prompt()
        self.assertIn("famil", self.launch("review-01", "reviewer", "review", expected=1))


    def test_second_run_cannot_overlap_a_live_checkout(self):
        self.setup_run()
        self.launch()
        output = self.call("init", "other", "--workspace", "workspace:1", expected=1)
        self.assertIn("one active run", output)
        self.receipt()
        self.call("init", "other", "--workspace", "workspace:1")

    def test_bracketed_route_filenames_are_literal_scope(self):
        self.setup_run(tickets=[{"id": "01", "files": ["app/[id]/page.tsx"]}])
        self.launch()
        self.assertEqual(self.state()["tickets"]["01"]["files"], ["app/[id]/page.tsx"])

    def test_dependency_cycle_is_rejected_without_saving_plan(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        ticket = self.repo / "ticket.md"
        ticket.write_text("Work")
        plan = self.repo / "invalid-plan.json"
        plan.write_text(json.dumps({"tickets": [
            {"id": "a", "ticket": str(ticket), "provider": "writer", "files": ["a.txt"], "blockers": ["b"]},
            {"id": "b", "ticket": str(ticket), "provider": "writer", "files": ["b.txt"], "blockers": ["a"]}]}))
        self.assertIn("cycle", self.call("plan", "demo", "--file", str(plan), expected=1))
        self.assertEqual(self.state()["tickets"], {})


    def test_plan_context_and_checks_complete_the_prompt(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off")
        ticket = self.repo / "01.md"
        ticket.write_text("# Ticket 01\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": str(ticket), "files": ["owned.txt"], "provider": "writer", "tier": "standard",
             "context": "Spec in docs/spec.md; keep the owner's edit.", "checks": ["python3 -m unittest", "read the prose"]},
            {"id": "02", "ticket": str(ticket), "files": ["next.txt"], "provider": "writer", "tier": "standard"}]}))
        self.call("plan", "demo", "--file", str(plan))
        out = self.call("prompt", "demo", "01")
        self.assertNotIn("fill before launch", out)
        text = (self.repo / ".fleet/runs/demo/prompts/01.md").read_text()
        self.assertIn("**Context and decisions:** Spec in docs/spec.md; keep the owner's edit.", text)
        self.assertIn("**Checks to run:**\n\n- python3 -m unittest\n- read the prose\n", text)
        self.assertNotIn("<!--", text)
        self.launch("01")
        # Without plan fields the prompt keeps LEAD comments, and launch refuses it until they are filled.
        self.assertIn("fill before launch: context, checks", self.call("prompt", "demo", "02"))
        self.stop_with_receipt("01")
        self.assertIn("fill LEAD comments", self.launch("02", expected=1))

    def test_plan_rejects_empty_checks(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        ticket = self.repo / "01.md"
        ticket.write_text("# Ticket 01\n")
        plan = self.repo / "plan.json"
        for checks in ("  ", [], [""], 3):
            plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(ticket), "files": ["owned.txt"],
                                                     "provider": "writer", "checks": checks}]}))
            with self.subTest(checks=checks):
                self.assertIn("checks must be nonempty", self.call("plan", "demo", "--file", str(plan), expected=1))

    def plan_file(self, tickets, decisions=()):
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": tickets, "decisions": list(decisions)}))
        return str(plan)

    def test_plan_writes_an_inline_task_into_the_run(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off")
        self.call("plan", "demo", "--file", self.plan_file([
            {"id": "fix", "task": "  Make the banner copy shorter.\n", "files": ["owned.txt"], "provider": "writer"}]))
        t = self.state()["tickets"]["fix"]
        path = self.repo / ".fleet/runs/demo/tasks/fix.md"
        self.assertEqual(t["ticket"], str(path.resolve()))
        self.assertEqual(path.read_text(), "# Task fix\n\nMake the banner copy shorter.\n")
        self.assertEqual(t["kind"], "implement")
        self.assertNotIn("task", t)
        self.prompt("fix")
        self.launch("fix")

    def test_plan_needs_exactly_one_of_ticket_or_task(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        ticket = self.repo / "01.md"
        ticket.write_text("# 01\n")
        for entry, message in (({"ticket": str(ticket), "task": "Also inline."}, "exactly one of ticket"),
                               ({}, "exactly one of ticket"),
                               ({"task": "  "}, "task must be nonempty")):
            with self.subTest(entry=entry):
                plan = self.plan_file([dict(entry, id="01", files=["owned.txt"], provider="writer")])
                self.assertIn(message, self.call("plan", "demo", "--file", plan, expected=1))
        self.assertEqual(self.state()["tickets"], {})
        self.assertFalse((self.repo / ".fleet/runs/demo/tasks").exists())

    def test_plan_applies_each_kinds_file_and_review_rules(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "cross-all")
        for entry, message in (({"kind": "investigate", "files": ["owned.txt"]}, "owns no files"),
                               ({"kind": "implement"}, "files must be a nonempty list"),
                               ({"kind": "debug", "files": ["owned.txt"]}, "kind must be implement or investigate")):
            with self.subTest(entry=entry):
                plan = self.plan_file([dict(entry, id="01", task="Look.", provider="writer")])
                self.assertIn(message, self.call("plan", "demo", "--file", plan, expected=1))
        self.call("plan", "demo", "--file", self.plan_file([
            {"id": "why", "kind": "investigate", "task": "Why?", "provider": "writer"},
            {"id": "fix", "task": "Fix.", "files": ["owned.txt"], "provider": "writer"}]))
        tickets = self.state()["tickets"]
        self.assertEqual((tickets["why"]["files"], tickets["why"]["review_required"]), ([], False))
        self.assertTrue(tickets["fix"]["review_required"])

    def test_tickets_saved_before_kind_existed_still_launch_verify_and_resume(self):
        self.setup_run()
        state = self.state()
        for t in state["tickets"].values():
            t.pop("kind")
        (self.repo / ".fleet/runs/demo/run.json").write_text(json.dumps(state))
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.assertIn("  01  verified  standard  writer:", self.call("resume", "demo"))

    def add(self, tickets, decisions=(), expected=0):
        for t in tickets:
            t.setdefault("provider", "writer")
        path = self.repo / "add.json"
        path.write_text(json.dumps({"tickets": tickets, "decisions": list(decisions)}))
        return self.call("add", "demo", "--file", str(path), expected=expected)

    def test_add_appends_to_a_run_after_its_first_launch(self):
        self.setup_run()
        self.launch()
        self.assertIn("frozen", self.call("plan", "demo", "--file", str(self.repo / "plan.json"), expected=1))
        out = self.add([{"id": "03", "task": "Tidy the readme.", "files": ["readme.txt"]}], ["Owner wants it short."])
        self.assertIn("added 1 tickets: 03", out)
        state = self.state()
        self.assertEqual(list(state["tickets"]), ["01", "02", "03"])
        self.assertRegex(state["tickets"]["03"]["added_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertNotIn("added_at", state["tickets"]["01"])
        self.assertRegex(state["decisions"][-1], r"^Owner wants it short\. \(added \d{4}-\d{2}-\d{2}T")
        resume = self.call("resume", "demo")
        self.assertIn(" +03  pending", resume)
        self.assertIn("  01  pending", resume)

    def test_add_on_a_fresh_run_needs_no_plan(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off")
        self.add([{"id": "solo", "task": "Fix the typo.", "files": ["owned.txt"],
                   "context": "Typo on line 1.", "checks": "Read the file."}])
        self.assertNotIn("fill before launch", self.call("prompt", "demo", "solo"))
        self.launch("solo")

    def test_add_refuses_overlap_with_unverified_work_unless_it_waits_for_it(self):
        self.setup_run()
        self.launch()
        out = self.add([{"id": "03", "task": "Touch owned too.", "files": ["owned.txt"]}], expected=1)
        self.assertIn("03: files overlap unverified ticket 01; add 01 to its blockers", out)
        self.assertFalse((self.repo / ".fleet/runs/demo/tasks/03.md").exists())
        # 02 waits on 01, so waiting on 02 waits on 01 too.
        self.add([{"id": "03", "task": "Touch owned too.", "files": ["owned.txt"], "blockers": ["02"]}])

    def test_add_lets_new_work_touch_files_of_verified_tickets(self):
        self.setup_run()
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.add([{"id": "03", "task": "Another pass.", "files": ["owned.txt"]}])

    def test_add_refuses_bad_entries_without_saving_any(self):
        self.setup_run()
        before = self.state()["tickets"]
        for tickets, message in (
                ([{"id": "01", "task": "Dup.", "files": ["x.txt"]}], "ticket id 01 is already used"),
                ([{"id": "03", "task": "A.", "files": ["x.txt"]}, {"id": "03", "task": "B.", "files": ["y.txt"]}],
                 "duplicate ticket 03"),
                ([{"id": "03", "task": "Loop.", "files": ["x.txt"], "blockers": ["04"]},
                  {"id": "04", "task": "Loop.", "files": ["y.txt"], "blockers": ["03"]}], "cycle"),
                ([{"id": "03", "task": "Ok.", "files": ["x.txt"]},
                  {"id": "04", "task": "Ghost.", "files": ["y.txt"], "blockers": ["nope"]}], "unknown blocker nope"),
                ([], "at least one ticket")):
            with self.subTest(message=message):
                self.assertIn(message, self.add(tickets, expected=1))
                self.assertEqual(self.state()["tickets"], before)
        self.assertFalse((self.repo / ".fleet/runs/demo/tasks").exists())

    def test_add_refuses_an_id_a_reviewer_already_uses(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        self.stop_with_receipt()
        self.review_prompt()
        out = self.add([{"id": "review-01", "task": "Clash.", "files": ["x.txt"]}], expected=1)
        self.assertIn("ticket id review-01 is already used", out)

    def test_add_reads_tickets_from_stdin(self):
        self.setup_run()
        data = json.dumps({"tickets": [{"id": "03", "task": "From stdin.", "files": ["x.txt"], "provider": "writer"}]})
        with patch.object(sys, "stdin", io.StringIO(data)):
            self.call("add", "demo", "--file", "-")
        self.assertEqual(self.state()["tickets"]["03"]["files"], ["x.txt"])

    def scan(self, stall=60):
        seen = self.repo / ".fleet/runs/demo/.seen"
        return fleet.scan_workers(self.repo, self.state(), seen, stall)

    def test_wait_wakes_once_when_a_worker_exits_without_reporting(self):
        self.setup_run()
        self.launch()
        self.assertEqual(self.scan(), [])
        self.receipt(returncode=3)
        self.assertIn("returncode=3", self.call("wait", "demo", "--timeout", "0"))
        self.assertIn("TIMEOUT", self.call("wait", "demo", "--timeout", "0", expected=2))

    def test_wait_wakes_once_on_an_unchanged_screen_but_not_while_waiting_on_the_lead(self):
        self.setup_run()
        self.launch()
        marker = self.repo / ".fleet/runs/demo/.seen/01.screen"
        self.assertEqual(self.scan(), [])                       # first sight starts the clock
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        events = self.scan()
        self.assertEqual(len(events), 1)
        self.assertIn("STALLED 01 screen unchanged for 6", events[0])
        self.assertIn("usage limit", events[0])
        self.assertEqual(self.scan(), [])                       # same screen does not wake again
        self.assertEqual(self.scan(stall=0), [])
        marker.unlink()
        self.report()                                           # report handed back: idle is expected
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.scan(), [])

    def test_a_new_workers_unchanged_screen_wakes_sooner_than_the_stall(self):
        self.setup_run()
        self.launch()
        marker = self.repo / ".fleet/runs/demo/.seen/01.screen"

        def idle_for(seconds):
            marker.unlink(missing_ok=True)
            self.assertEqual(self.scan(stall=180), [])
            key, since, flagged = marker.read_text().split("|")
            marker.write_text(f"{key}|{float(since) - seconds}|{flagged}")
            return self.scan(stall=180)

        self.assertEqual(idle_for(fleet.LAUNCH_STALL_S - 5), [])
        self.assertIn("STALLED 01", idle_for(fleet.LAUNCH_STALL_S + 1)[0])  # a prompt at start-up, not a long task
        self.assertEqual(idle_for(fleet.LAUNCH_STALL_S + 1) and self.scan(stall=0), [])  # --stall 0 still turns it off
        state = self.state()
        state["workers"]["01"]["launched_at"] -= fleet.LAUNCH_WATCH_S + 1
        fleet.save_run(self.repo, "demo", state)
        self.assertEqual(idle_for(fleet.LAUNCH_STALL_S + 1), [])           # past its first minutes: the full stall
        self.assertIn("STALLED 01", idle_for(181)[0])
        del state["workers"]["01"]["launched_at"]                          # a worker from an older runtime
        fleet.save_run(self.repo, "demo", state)
        self.assertEqual(idle_for(fleet.LAUNCH_STALL_S + 1), [])

    def test_launch_shows_the_new_workers_screen_after_the_lock(self):
        self.setup_run()
        real, held = self.fake_cmux, []

        def cmux(*args, **kwargs):
            if args[0] == "read-screen":
                lock = (self.repo / ".fleet/runs/.lock").open("a")
                try:
                    fleet.fcntl.flock(lock, fleet.fcntl.LOCK_EX | fleet.fcntl.LOCK_NB)
                except BlockingIOError:
                    held.append(True)
                lock.close()
                return "banner\n" * 20 + "Do you trust the files in this folder?   \n\n\n\n  1. Yes, proceed\n" + "\n" * 30
            return real(*args, **kwargs)

        with patch.object(fleet, "cmux", side_effect=cmux), patch.object(fleet.time, "sleep") as sleep:
            out = self.call("launch", "demo", "01", "writer", "--tier", "standard", "--settle", "3")
        self.assertIn("screen after 3s:\n", out)
        self.assertTrue(out.endswith("  Do you trust the files in this folder?\n\n    1. Yes, proceed\n"), out)
        self.assertEqual(out.count("banner"), fleet.LAUNCH_SCREEN_LINES - 3)
        self.assertIn(3.0, [c.args[0] for c in sleep.call_args_list])
        self.assertEqual(held, [])                                         # the pause does not block other commands
        self.call("stop", "demo", "01", "--timeout", "0", expected=1)
        self.receipt()
        self.call("status", "demo")

        def unreadable(*args, **kwargs):
            if args[0] == "read-screen":
                raise fleet.FleetError("surface not found")
            return real(*args, **kwargs)

        with patch.object(fleet, "cmux", side_effect=unreadable), patch.object(fleet.time, "sleep"):
            out = self.call("launch", "demo", "01", "writer", "--tier", "standard")   # LAUNCH_SETTLE_S is 0 here
            self.assertNotIn("screen after", out)
            self.call("stop", "demo", "01", "--timeout", "0", expected=1)
            self.receipt()
            self.call("status", "demo")
            out = self.call("launch", "demo", "01", "writer", "--tier", "standard", "--settle", "1")
        self.assertIn("screen after 1s: unreadable (surface not found); fleet peek it", out)  # the launch stands
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.assertIn("settle must be between", self.call("launch", "demo", "02", "writer", "--settle", "31", expected=1))

    def test_wait_names_the_lines_of_a_summary_first_report(self):
        self.setup_run()
        self.launch()
        report = self.repo / ".fleet/runs/demo/reports/01.md"
        report.write_text("# Report 01\n\nStatus: needs-verification\n\n## Findings\n- none\n\n## Appendix\nlong\noutput\n")
        out = self.call("wait", "demo", "--timeout", "0")
        self.assertIn(f"REPORT 01 [needs-verification] {report}  (read lines 1-7 of 10; appendix below)", out)
        self.report()                                                       # no appendix: the whole report is the summary
        self.assertTrue(self.call("wait", "demo", "--timeout", "0").startswith(f"REPORT 01 [needs-verification] {report}\n"))

    def test_launch_splits_a_new_pane_instead_of_adding_a_tab(self):
        self.setup_run()
        self.panes.append({"ref": "pane:2", "surface_refs": ["surface:shell"], "pixel_frame": {"width": 600, "height": 900}})
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch()
        self.assertFalse([c for c in self.cmux_calls if c[0] == "new-surface"])
        split = [c for c in self.cmux_calls if c[0] == "new-split"][-1]
        self.assertEqual(split[:6], ("new-split", "right", "--workspace", "workspace:1", "--surface", "surface:lead"))
        command = split[split.index("--command") + 1]
        self.assertTrue(command.startswith("cd " + shlex.quote(str(self.repo)) + " && exec "), command)
        self.assertEqual(self.state()["workers"]["01"]["surface"], "surface:1")

    def fill_workspace(self):
        self.panes += [{"ref": f"pane:{n}", "surface_refs": [f"surface:shell{n}"]} for n in range(2, 10 - len(self.panes))]
        self.assertEqual(len(self.panes), fleet.MAX_PANES)

    def pane_of(self, surface):
        return next(p["ref"] for p in self.panes if surface in p["surface_refs"])

    def test_at_eight_panes_launch_replaces_a_verified_workers_pane(self):
        self.setup_run()
        self.launch()
        self.assertEqual(self.state()["workers"]["01"]["surface_id"], "uuid-surface:1")
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.fill_workspace()
        finished = self.pane_of("surface:1")
        self.cmux_calls.clear()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch("02")
        new = [c for c in self.cmux_calls if c[0] in ("new-surface", "new-split", "close-surface")]
        self.assertEqual([c[0] for c in new], ["new-surface", "close-surface"])
        self.assertEqual(new[0][3:5], ("--pane", finished))
        self.assertEqual(new[1][-1], "uuid-surface:1")  # closed by UUID, never by a reusable ref
        workers = self.state()["workers"]
        self.assertEqual((workers["01"]["state"], workers["02"]["state"]), ("closed", "running"))

    def test_at_eight_panes_launch_keeps_an_unverified_workers_pane_and_adds_a_tab(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.launch()
        self.report()
        self.stop_with_receipt()  # exited, not yet verified: the lead may still need its scrollback
        self.fill_workspace()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch("02")
        self.assertFalse([c for c in self.cmux_calls if c[0] == "close-surface"])
        self.assertEqual(self.state()["workers"]["01"]["state"], "exited")
        self.assertEqual(self.pane_of("surface:2"), self.pane_of("surface:1"))  # a tab beside it

    def test_at_eight_panes_launch_never_closes_a_reused_surface_ref(self):
        self.setup_run()
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.fill_workspace()
        # cmux restarted: surface:1 now names someone else's terminal, with a different UUID.
        next(p for p in self.panes if "surface:1" in p["surface_refs"])["surface_ids"] = ["uuid-new-terminal"]
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch("02")
        self.assertFalse([c for c in self.cmux_calls if c[0] == "close-surface"])
        self.assertEqual(self.state()["workers"]["01"]["state"], "exited")

    def test_at_eight_panes_launch_adds_a_tab_to_an_idle_pane_when_none_can_be_replaced(self):
        self.setup_run()
        self.fill_workspace()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch()
        new = [c for c in self.cmux_calls if c[0] in ("new-surface", "new-split", "close-surface")]
        self.assertEqual([c[0] for c in new], ["new-surface"])
        self.assertEqual(new[0][3:5], ("--pane", "pane:2"))
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_at_eight_panes_launch_refuses_before_any_side_effect(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.launch()
        self.panes += [{"ref": f"pane:{n}", "surface_refs": ["surface:1"]} for n in range(2, 8)]
        report = self.repo / ".fleet/runs/demo/reports/02.md"
        report.write_text("# Worker report\nStatus: blocked\n")
        self.cmux_calls.clear()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            out = self.launch("02", expected=1)
        self.assertIn("every pane has a live worker", out)
        self.assertFalse([c for c in self.cmux_calls if c[0] in ("new-surface", "new-split")])
        self.assertNotIn("02", self.state()["workers"])
        # A refused launch must not freeze a baseline (it would absorb the owner's later edits) or move the report.
        self.assertFalse((self.repo / ".fleet/runs/demo/snapshots/02.json").exists())
        self.assertTrue(report.exists())

    def test_explicit_pane_is_split_rather_than_given_a_tab(self):
        self.setup_run()
        self.panes.append({"ref": "pane:9", "surface_refs": ["surface:9"], "pixel_frame": {"width": 400, "height": 900}})
        self.call("launch", "demo", "01", "writer", "--tier", "standard", "--pane", "uuid-pane:9")  # ref, UUID or index
        self.assertFalse([c for c in self.cmux_calls if c[0] == "new-surface"])
        self.assertEqual([c[:6] for c in self.cmux_calls if c[0] == "new-split"],
                         [("new-split", "down", "--workspace", "workspace:1", "--surface", "surface:9")])

    def test_wait_reports_a_pane_it_cannot_read(self):
        self.setup_run()
        self.launch()
        real = self.fake_cmux
        def gone(*args, **kwargs):
            if args[0] == "read-screen":
                raise fleet.FleetError("surface not found")
            return real(*args, **kwargs)
        with patch.object(fleet, "cmux", side_effect=gone):
            self.assertIn("UNREACHABLE 01", self.scan()[0])
            self.assertEqual(self.scan(), [])

    def test_only_wait_accepts_long_timeouts(self):
        self.setup_run()
        self.launch()
        self.receipt()
        self.assertIn("EXITED 01", self.call("wait", "demo", "--timeout", "600"))
        self.assertIn("between 0 and 600", self.call("wait", "demo", "--timeout", "601", expected=1))
        self.assertIn("between 0 and 60 ", self.call("stop", "demo", "01", "--timeout", "61", expected=1))

    def test_braces_in_context_checks_and_rules_are_content(self):
        (self.repo / ".fleet").mkdir()
        (self.repo / ".fleet/rules.md").write_text("CI reads ${{ secrets.TOKEN }}; never print it.\n")
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off")
        ticket = self.repo / "01.md"
        ticket.write_text("# Ticket 01\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(ticket), "files": ["owned.txt"],
            "provider": "writer", "context": "Keep the Handlebars `{{title}}` helper and {{checks}} text.",
            "checks": "npm test"}]}))
        self.call("plan", "demo", "--file", str(plan))
        self.call("prompt", "demo", "01")
        text = (self.repo / ".fleet/runs/demo/prompts/01.md").read_text()
        self.assertIn("Keep the Handlebars `{{title}}` helper and {{checks}} text.", text)
        self.assertIn("${{ secrets.TOKEN }}", text)
        self.assertIn("**Checks to run:** npm test", text)
        # Known placeholders left in content still block launch, and are named.
        self.assertIn("{{checks}}", self.launch(expected=1))
        path = self.repo / ".fleet/runs/demo/prompts/01.md"
        path.write_text(text.replace("{{checks}}", "checks"))
        self.launch()

    def test_exit_after_a_handed_back_report_is_the_leads_stop_not_an_event(self):
        self.setup_run()
        self.launch()
        self.report()
        self.receipt()
        self.assertEqual(self.scan(), [])

    def test_answering_a_blocked_worker_re_enables_stall_detection(self):
        self.setup_run()
        self.launch()
        report = self.repo / ".fleet/runs/demo/reports/01.md"
        report.write_text("Status: blocked\n\nWhich copy?\n")
        marker = self.repo / ".fleet/runs/demo/.seen/01.screen"
        self.assertEqual(self.scan(), [])
        self.assertFalse(marker.exists())                        # waiting on the lead: not watched
        self.call("send", "demo", "01", "Use the short copy.")
        self.assertEqual(self.scan(), [])                        # answered: the clock starts
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        self.assertIn("STALLED 01", self.scan()[0])

    def test_failed_stop_is_not_reported_as_a_stall(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "send_keys"):
            self.call("stop", "demo", "01", "--timeout", "0", expected=1)
        self.assertEqual(self.state()["workers"]["01"]["state"], "stop-failed")
        self.assertEqual(self.scan(), [])

    def test_send_peek_wait_and_stop_address_a_worker_by_uuid_not_its_reusable_ref(self):
        self.setup_run()
        self.launch()

        def targets():
            self.cmux_calls.clear()
            with patch.object(fleet.time, "sleep"):
                self.call("send", "demo", "01", "hello")
                self.call("peek", "demo", "01")
                self.scan()
                self.call("stop", "demo", "01", "--timeout", "0", expected=1)
            return {c[c.index("--surface") + 1] for c in self.cmux_calls if "--surface" in c}

        # After a cmux restart surface:1 can name someone else's terminal; its UUID cannot.
        self.assertEqual(targets(), {"uuid-surface:1"})
        state = self.state()
        del state["workers"]["01"]["surface_id"]  # launch could not record it: the ref is all there is
        state["workers"]["01"]["state"] = "running"
        fleet.save_run(self.repo, "demo", state)
        self.assertEqual(targets(), {"surface:1"})

    def test_launch_records_the_uuid_cmux_prints_before_the_surface_is_listed(self):
        self.setup_run()
        with patch.object(fleet, "surface_uuid", return_value=None):  # cmux lists a new surface a moment later
            self.launch()
        self.assertEqual(self.state()["workers"]["01"]["surface_id"], "uuid-surface:1")

    def test_launch_waits_for_the_listing_when_cmux_prints_no_uuid(self):
        self.setup_run()
        self.inline_ids = False  # an older cmux
        with patch.object(fleet.time, "sleep"), patch.object(fleet, "surface_uuid", side_effect=[None, "uuid-late"]):
            self.launch()
        self.assertEqual(self.state()["workers"]["01"]["surface_id"], "uuid-late")
        self.receipt()
        with patch.object(fleet.time, "sleep"), patch.object(fleet, "surface_uuid", return_value=None):
            out = self.launch()
        self.assertIn("addressed by its ref", out)
        self.assertIsNone(self.state()["workers"]["01"]["surface_id"])

    def test_init_stores_the_workspace_uuid_because_refs_are_renumbered(self):
        self.workspace_id = "WS-UUID"
        self.assertIn("workspace: WS-UUID (workspace:1)", self.call("init", "demo", "--workspace", "workspace:1"))
        self.assertEqual(self.state()["workspace"], "WS-UUID")
        self.assertNotIn("cmux ref", self.call("resume", "demo"))

    def test_resume_warns_when_an_older_run_stored_a_workspace_ref(self):
        self.setup_run()
        self.assertIn("workspace:1 is a cmux ref", self.call("resume", "demo"))

    def test_stop_close_accepts_a_surface_that_closed_itself_when_the_worker_exited(self):
        self.setup_run()
        self.launch()
        real = self.fake_cmux

        def refused(*args, **kwargs):
            if args[0] == "close-surface":
                raise fleet.FleetError("Surface not found")
            return real(*args, **kwargs)

        with patch.object(fleet, "cmux", side_effect=refused), patch.object(fleet.time, "sleep"), \
                patch.object(fleet, "send_keys", side_effect=lambda *_: self.receipt()):
            self.assertIn("Surface not found", self.call("stop", "demo", "01", "--close", "--timeout", "1", expected=1))
            self.assertEqual(self.state()["workers"]["01"]["state"], "exited")  # still listed: the failure stands
            self.panes = [p for p in self.panes if "surface:1" not in p["surface_refs"]]
            self.call("stop", "demo", "01", "--close")
        self.assertEqual(self.state()["workers"]["01"]["state"], "closed")

    def test_each_unreachable_episode_wakes_once(self):
        self.setup_run()
        self.launch()
        real = self.fake_cmux
        def gone(*args, **kwargs):
            if args[0] == "read-screen":
                raise fleet.FleetError("timed out")
            return real(*args, **kwargs)
        with patch.object(fleet, "cmux", side_effect=gone):
            self.assertIn("UNREACHABLE 01", self.scan()[0])
        self.assertEqual(self.scan(), [])                        # readable again
        with patch.object(fleet, "cmux", side_effect=gone):
            self.assertIn("UNREACHABLE 01", self.scan()[0])      # the real close still wakes

    def test_relaunch_with_a_new_token_resets_exit_and_screen_markers(self):
        self.setup_run()
        self.launch()
        self.receipt(returncode=1)
        self.assertIn("EXITED 01", self.scan()[0])
        self.call("status", "demo")                             # reconcile the exit
        self.launch()
        self.assertEqual(self.scan(), [])
        self.receipt(returncode=2)
        self.assertIn("returncode=2", self.scan()[0])

    def resumable_writer(self):
        self.cfg["providers"]["writer"].update(launch="{bin} {session} --model {model} --effort {effort} {prompt}",
                                               session="--session-id {id}", resume="--resume {id}")

    def worker_argv(self, ticket="01"):
        return shlex.split(self.state()["workers"][ticket]["command"])

    def test_resume_continues_the_exited_workers_own_session(self):
        self.resumable_writer()
        self.setup_run()
        self.launch()
        first = self.state()["workers"]["01"]
        sid = str(uuid.UUID(first["session"]))                  # a real, dashed UUID
        argv = self.worker_argv()
        self.assertEqual(argv[argv.index("--session-id") + 1], sid)
        self.receipt(returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "01", "writer", "--resume")
        w = self.state()["workers"]["01"]
        self.assertEqual(w["session"], sid)
        self.assertNotEqual(w["token"], first["token"])
        argv = self.worker_argv()
        self.assertNotIn("--session-id", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], sid)
        self.assertIn(str(self.repo / ".fleet/runs/demo/reports/01.md"), argv[-1])  # the old report was archived
        self.receipt(returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "01", "writer", "--resume")     # a second limit resumes the same session
        self.assertEqual(self.state()["workers"]["01"]["session"], sid)

    def test_resume_needs_a_saved_session(self):
        self.setup_run()                                        # this writer cannot name a session
        self.launch()
        self.assertNotIn("session", self.state()["workers"]["01"])
        self.receipt()
        self.call("status", "demo")
        self.assertIn("no saved session", self.call("launch", "demo", "01", "writer", "--resume", expected=1))
        self.assertIn("no saved session", self.call("launch", "demo", "02", "writer", "--resume", expected=1))

    def test_resume_stays_on_the_sessions_account(self):
        self.resumable_writer()
        self.setup_run()
        self.launch()
        self.receipt()
        self.call("status", "demo")
        self.call("steer", "provider", "demo", "reviewer")
        out = self.call("launch", "demo", "01", "writer", "--resume", expected=1)
        self.assertIn("belongs to writer", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "exited")  # refused before any side effect
        self.call("steer", "reset", "demo")
        del self.cfg["providers"]["writer"]["resume"]           # the adapter lost resume since launch
        self.assertIn("cannot resume", self.call("launch", "demo", "01", "writer", "--resume", expected=1))

    def test_timeout_lists_reports_another_wait_already_consumed(self):
        self.setup_run()
        self.launch()
        self.report()
        self.assertIn("REPORT 01 [needs-verification]", self.call("wait", "demo", "--timeout", "0"))
        out = self.call("wait", "demo", "--timeout", "0", expected=2)
        self.assertIn("TIMEOUT", out)
        self.assertIn("PENDING 01 [needs-verification]", out)

    def test_wait_validates_stall_and_compact_resume_handles_an_empty_run(self):
        self.call("init", "demo", "--workspace", "workspace:1")
        self.assertIn("stall must be", self.call("wait", "demo", "--stall", "-1", expected=1))
        out = self.call("resume", "demo")
        self.assertIn("decisions:\n  - (none)", out)
        self.assertIn("workers:\n  (none)", out)

    def test_compact_resume_shows_pid_and_returncode_for_recovery(self):
        self.setup_run()
        self.launch()
        worker = self.state()["workers"]["01"]
        Path(worker["exit_file"]).with_suffix(".started.json").write_text(
            json.dumps({"token": worker["token"], "wrapper_pid": 10, "child_pid": 11}))
        self.receipt(returncode=4)
        self.assertIn("pid=11  returncode=4", self.call("resume", "demo"))

    def test_peek_keeps_the_last_lines_after_dropping_padding(self):
        self.setup_run()
        self.launch()
        screen = "\n".join(f"line {n}" for n in range(30)) + "\n" * 40
        with patch.object(fleet, "cmux", return_value=screen) as read:
            out = self.call("peek", "demo", "01", "--lines", "5")
        self.assertEqual(out, "line 25\nline 26\nline 27\nline 28\nline 29\n")
        self.assertIn("60", read.call_args.args)

    def test_peek_drops_blank_padding(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "cmux", return_value="\n\nAllow edit?   \n\n\n\n  1. Yes  \n\n\n"):
            self.assertEqual(self.call("peek", "demo", "01"), "Allow edit?\n\n  1. Yes\n")


    def test_steer_moves_implementation_launches_until_reset(self):
        self.setup_run()
        self.call("steer", "provider", "demo", "reviewer")
        self.assertIn("steered (standard): writer:writer-model -> reviewer:reviewer-model", self.launch())
        w = self.state()["workers"]["01"]
        self.assertEqual((w["provider"], w["model"], w["family"], w["steered"]),
                         ("reviewer", "reviewer-model", "anthropic", True))
        self.assertIn("steer: reviewer:<tier model>", self.call("resume", "demo"))
        self.assertEqual(json.loads(self.call("resume", "demo", "--json").split("Unconfirmed")[0])["steer"],
                         {"provider": "reviewer"})
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.call("steer", "reset", "demo")
        self.assertNotIn("steer:", self.call("resume", "demo"))
        self.launch("02")
        w = self.state()["workers"]["02"]
        self.assertEqual((w["provider"], w["steered"]), ("writer", False))

    def test_steer_skips_reviews_and_review_checks_the_steered_writers_family(self):
        self.setup_run(review="cross-all")
        self.call("steer", "provider", "demo", "reviewer")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        self.stop_with_receipt()
        self.review_prompt()
        # The plan's writer family is openai, but the steered writer ran on anthropic.
        self.assertIn("famil", self.launch("review-01", "reviewer", "review", expected=1))
        self.launch("review-01", "writer", "review")
        self.assertEqual(self.state()["workers"]["review-01"]["provider"], "writer")

    def test_steered_model_takes_its_own_family_and_needs_one_for_review(self):
        self.setup_run(review="cross-all")
        # A mixed-family account only knows the families its tiers and model_families name.
        self.cfg["providers"]["writer"].update(family="mixed", model_families={"borrowed-model": "anthropic"})
        self.call("steer", "provider", "demo", "writer")
        self.call("steer", "model", "demo", "mystery-model")
        self.assertIn("no known family", self.call("launch", "demo", "01", "writer", "--tier", "standard",
                                                   "--family", "openai", expected=1))
        self.call("steer", "model", "demo", "borrowed-model")
        self.call("launch", "demo", "01", "writer", "--tier", "standard", "--family", "openai")
        w = self.state()["workers"]["01"]
        self.assertEqual((w["model"], w["family"]), ("borrowed-model", "anthropic"))

    def test_steer_onto_the_plans_route_is_not_reported_as_steered(self):
        self.setup_run()
        self.call("steer", "provider", "demo", "writer")
        self.call("steer", "model", "demo", "writer-model")
        self.assertNotIn("steered (", self.call("launch", "demo", "01", "writer", "--tier", "standard",
                                               "--family", "openai"))
        w = self.state()["workers"]["01"]
        self.assertEqual((w["model"], w["family"], w["steered"]), ("writer-model", "openai", False))

    def test_steer_to_a_provider_without_a_tier_model_names_steer_model(self):
        self.setup_run()
        del self.cfg["providers"]["reviewer"]["tiers"]["standard"]["model"]
        self.call("steer", "provider", "demo", "reviewer")
        self.assertIn("fleet steer model demo <name>", self.launch(expected=1))

    def test_steer_stays_inside_routing_and_allowed_models(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--routing", "single:writer")
        self.assertIn("routing", self.call("steer", "provider", "demo", "reviewer", expected=1))
        self.assertIn("unknown provider", self.call("steer", "provider", "demo", "nobody", expected=1))
        self.assertIn("provider first", self.call("steer", "model", "demo", "writer-model", expected=1))
        self.cfg["providers"]["writer"]["models_allow"] = ["writer-model"]
        self.call("steer", "provider", "demo", "writer")
        self.assertIn("models_allow", self.call("steer", "model", "demo", "other-model", expected=1))
        self.call("steer", "model", "demo", "writer-model")
        self.assertEqual(json.loads((self.repo / ".fleet/runs/demo/live.json").read_text()),
                         {"provider": "writer", "model": "writer-model"})

    def test_hold_blocks_new_launches_but_not_running_workers(self):
        self.setup_run()
        self.launch()
        self.call("hold", "--reason", "quota break")
        run_json = (self.repo / ".fleet/runs/demo/run.json").read_bytes()
        calls = len(self.cmux_calls)
        out = self.launch("02", expected=1)
        self.assertIn("on hold", out)
        self.assertIn("quota break", out)
        self.assertEqual((self.repo / ".fleet/runs/demo/run.json").read_bytes(), run_json)
        self.assertEqual(len(self.cmux_calls), calls)
        self.assertIn("hold: launches are on hold", self.call("status", "demo"))
        self.assertIn("hold: launches are on hold", self.call("resume", "demo"))
        self.assertEqual(json.loads(self.call("resume", "demo", "--json").split("Unconfirmed")[0])["hold"]["reason"],
                         "quota break")
        # The running worker still finishes and is verified as usual.
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.assertIn("released", self.call("release"))
        self.assertIn("no hold", self.call("release"))
        self.launch("02")

    def test_wait_on_a_held_run_stops_once_a_worker_exits_by_itself(self):
        self.setup_run()
        self.launch()
        self.call("hold")
        self.receipt()  # the worker exits without fleet stop, so run.json still says running
        self.assertIn("EXITED 01", self.call("wait", "demo", "--timeout", "1"))
        self.assertIn("HOLD", self.call("wait", "demo", "--timeout", "1", "--stall", "0"))

    def test_damaged_hold_file_still_blocks_and_release_removes_it(self):
        self.setup_run()
        (self.repo / ".fleet/runs/hold.json").write_text("{not json")
        self.assertIn("unreadable", self.launch(expected=1))
        self.assertIn("unreadable", self.call("resume", "demo"))
        self.assertIn("released", self.call("release"))
        self.launch()

    def test_wait_on_a_held_idle_run_returns_at_once(self):
        self.setup_run()
        self.launch()
        self.call("hold")
        self.assertNotIn("HOLD", self.call("wait", "demo", "--timeout", "1", "--interval", "1", expected=2))
        self.report()
        self.stop_with_receipt()
        self.assertIn("REPORT 01", self.call("wait", "demo", "--timeout", "1"))  # events still come first
        started = time.monotonic()
        out = self.call("wait", "demo", "--timeout", "60", "--stall", "0")
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("HOLD", out)
        self.assertIn("PENDING 01 [needs-verification]", out)

class ProcessRunner(unittest.TestCase):
    def test_exit_receipt_follows_real_child_exit_and_records_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "worker.json"
            output = root / "worker-output.txt"
            command = shlex.join([sys.executable, "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('finished'); sys.exit(7)", str(output)])
            result = subprocess.run([sys.executable, str(ROOT / "skill/bin/fleet"), "_run", str(receipt), "test-token", command],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(output.read_text(), "finished")
            self.assertEqual(json.loads(receipt.read_text()), {"token": "test-token", "returncode": 7})
            process = json.loads(receipt.with_suffix(".started.json").read_text())
            self.assertGreater(process["child_pid"], 0)
            self.assertEqual(process["token"], "test-token")


class Quoting(unittest.TestCase):
    def test_model_effort_and_prompt_remain_literal_shell_arguments(self):
        # Run through a shell to catch globbing, quotes and command substitution,
        # not merely a string assertion about the builder's implementation.
        for shell in ("/bin/sh", "/bin/zsh"):
            if not Path(shell).exists():
                continue
            with self.subTest(shell=shell):
                model = "vendor-model[context=1m,effort=high,fast=false] ' ; $(printf injected)"
                effort = "high ; $(printf injected)"
                prompt = "Read 'a b.md'; $(printf injected)"
                cfg = {"bin": "printf", "launch": "{bin} '%s\\n' {model} {effort} {prompt}"}
                command = fleet.build_launch(cfg, model, effort, prompt)
                result = subprocess.run([shell, "-c", command], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.splitlines(), [model, effort, prompt])
                self.assertEqual(shlex.split(command)[2:], [model, effort, prompt])


if __name__ == "__main__":
    unittest.main()
