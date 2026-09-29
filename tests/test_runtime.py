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
        if args[0] == "new-surface":
            self.surface += 1
            return "OK surface:" + str(self.surface)
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
        state = self.state()
        self.assertIn('"02"', output)
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
