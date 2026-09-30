"""Command-level regressions in disposable Git repositories; no live agents/cmux."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import re
import shlex
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
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
        self.panes = [{"ref": "pane:1", "surface_refs": ["surface:lead"], "selected_surface_ref": "surface:lead",
                       "pixel_frame": {"width": 1200, "height": 900}}]
        real_sh = fleet.sh

        def shell(args, **kwargs):
            if isinstance(args, list) and args and args[0] == "cmux":
                return self.fake_cmux(*args[1:])
            return real_sh(args, **kwargs)

        for mocker in (patch.object(fleet, "repo_root", return_value=self.repo),
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
        self.cmux_calls.append(args)
        if args[0] in ("new-surface", "new-split"):
            self.surface += 1
            return "OK surface:" + str(self.surface)
        if args[:2] == ("--json", "list-panes"):
            return json.dumps({"panes": self.panes})
        if args[:2] == ("--json", "identify"):
            return json.dumps({"caller": {"pane_ref": "pane:1"}})
        return "OK"

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
        self.assertEqual(self.scan(), [])                       # same screen does not wake again
        self.assertEqual(self.scan(stall=0), [])
        marker.unlink()
        self.report()                                           # report handed back: idle is expected
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.scan(), [])

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

    def fill_workspace(self, finished):
        self.panes += [{"ref": f"pane:{n}", "surface_refs": [f"surface:shell{n}"]} for n in range(2, 8)]
        self.panes.append({"ref": "pane:8", "surface_refs": [finished]})

    def test_at_eight_panes_launch_replaces_a_finished_workers_pane(self):
        self.setup_run()
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.fill_workspace("surface:1")
        self.cmux_calls.clear()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch("02")
        new = [c for c in self.cmux_calls if c[0] in ("new-surface", "new-split", "close-surface")]
        self.assertEqual([c[0] for c in new], ["new-surface", "close-surface"])
        self.assertEqual(new[0][3:5], ("--pane", "pane:8"))
        self.assertEqual(new[1][-1], "surface:1")
        workers = self.state()["workers"]
        self.assertEqual((workers["01"]["state"], workers["02"]["state"]), ("closed", "running"))

    def test_at_eight_panes_launch_adds_a_tab_to_an_idle_pane_when_none_can_be_replaced(self):
        self.setup_run()
        self.fill_workspace("surface:user")
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            self.launch()
        new = [c for c in self.cmux_calls if c[0] in ("new-surface", "new-split", "close-surface")]
        self.assertEqual([c[0] for c in new], ["new-surface"])
        self.assertEqual(new[0][3:5], ("--pane", "pane:2"))
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_at_eight_panes_launch_refuses_when_every_pane_has_a_live_worker(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.launch()
        self.panes += [{"ref": f"pane:{n}", "surface_refs": ["surface:1"]} for n in range(2, 9)]
        self.cmux_calls.clear()
        with patch.dict(fleet.os.environ, {"CMUX_SURFACE_ID": "lead"}):
            out = self.launch("02", expected=1)
        self.assertIn("every pane has a live worker", out)
        self.assertFalse([c for c in self.cmux_calls if c[0] in ("new-surface", "new-split")])
        self.assertNotIn("02", self.state()["workers"])

    def test_explicit_pane_is_split_rather_than_given_a_tab(self):
        self.setup_run()
        self.panes.append({"ref": "pane:9", "surface_refs": ["surface:9"], "pixel_frame": {"width": 400, "height": 900}})
        self.call("launch", "demo", "01", "writer", "--tier", "standard", "--pane", "pane:9")
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
